"""Codex headless adapter using the installed, subscription-backed CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import hive.config as config
from hive.runtime.adapter_config import AdapterConfig, build_system_prompts
from hive.runtime.base import Runtime
from hive.runtime.harness import HarnessError, HarnessErrorKind, HarnessStatus, RunMode
from hive.runtime.headless import (
    classify_failure_text,
    parse_jsonl,
    run_process,
    tail,
    unless_work_done,
)

HARNESS = "codex"
_WORK_ITEMS = frozenset({"command_execution", "file_change", "mcp_tool_call", "collab_tool_call"})
_API_BILLING_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")


def _subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _API_BILLING_ENV:
        env.pop(key, None)
    return env


def probe_codex() -> HarnessStatus:
    """Check the local Codex login without sending a model request."""
    binary = config.CODEX_BINARY
    if shutil.which(binary) is None:
        return HarnessStatus(HARNESS, False, None, f"{binary} not found")
    try:
        proc = subprocess.run(
            [binary, "login", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_subscription_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return HarnessStatus(HARNESS, True, None, f"`codex login status` failed: {exc}")
    detail = tail(proc.stderr or proc.stdout, 120)
    if "using api key" in detail.lower():
        return HarnessStatus(HARNESS, True, False, "API-key login is not a subscription login")
    return HarnessStatus(HARNESS, True, proc.returncode == 0, detail)


class CodexAdapter(Runtime):
    """One ``codex exec --json`` process per turn; resume by thread id."""

    def __init__(
        self, cfg: AdapterConfig, cwd: Path | None = None, resume_session_id: str | None = None
    ) -> None:
        self._config = cfg
        self._cwd = cwd
        self._session_id = resume_session_id
        self._usage_totals = dict(cfg.codex_usage)
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def is_alive(self) -> bool:
        return self._started

    def adopt_session(self, session_id: str | None) -> None:
        if session_id:
            self._session_id = session_id

    def _argv(self) -> list[str]:
        argv = [config.CODEX_BINARY, "exec"]
        if self._session_id:
            argv.extend(["resume", self._session_id])
        argv.extend(["--json", "--model", config.codex_model_for(self._config.role)])
        argv.extend(
            [
                "-c",
                f"model_reasoning_effort={json.dumps(config.codex_effort_for(self._config.role))}",
            ]
        )
        from hive.models.entity import DANGEROUS_MODES

        if self._config.permission_mode in DANGEROUS_MODES:
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            argv.extend(["-c", 'sandbox_mode="workspace-write"'])
        if not self._session_id:
            argv.append("--skip-git-repo-check")
        argv.append("-")
        return argv

    async def send_turn(self, prompt: str) -> tuple[str, dict]:
        blocks = build_system_prompts(self._config)
        instructions = "\n\n".join(blocks)
        turn_prompt = f"{instructions}\n\n{prompt}" if instructions else prompt
        result = await self._run(turn_prompt)
        if result is None:
            # An Entity may carry a session from Claude or Pi. Codex explicitly
            # rejected that id before a turn began; start a fresh Codex thread.
            self._session_id = None
            result = await self._run(turn_prompt)
        assert result is not None
        return result

    async def _run(self, prompt: str) -> tuple[str, dict] | None:
        proc = await run_process(
            self._argv(),
            harness=HARNESS,
            stdin_text=prompt,
            cwd=self._cwd,
            env=_subscription_env(),
            timeout=config.HEADLESS_TIMEOUT_S,
        )
        events = parse_jsonl(proc.stdout)
        terminal = next(
            (e for e in reversed(events) if e.get("type") in {"turn.failed", "turn.completed"}),
            None,
        )
        completed = terminal if terminal and terminal.get("type") == "turn.completed" else None
        if proc.returncode or completed is None:
            error = (terminal or {}).get("error") or {}
            detail = str(error.get("message") or "") if isinstance(error, dict) else str(error)
            if not detail:
                detail = next(
                    (str(e.get("message") or "") for e in reversed(events) if e.get("type") == "error"),
                    "",
                )
            if not detail:
                detail = tail(proc.stderr) or f"exit {proc.returncode}, no completed turn"
            if self._session_id and _missing_session(detail) and not _did_work(events):
                return None
            kind = classify_failure_text(detail)
            if kind is HarnessErrorKind.OTHER and re.search(
                r"model[^\n]*(?:not (?:available|supported|found)|does not exist|unsupported)|"
                r"(?:unsupported|unavailable) model", detail, re.I
            ):
                kind = HarnessErrorKind.UNAVAILABLE
            kind = unless_work_done(kind, _did_work(events))
            raise HarnessError(kind, HARNESS, RunMode.HEADLESS, tail(detail))

        thread = next((e for e in events if e.get("type") == "thread.started"), {})
        session_id = thread.get("thread_id") or self._session_id
        self._session_id = session_id
        messages = [
            item.get("text", "")
            for e in events
            if e.get("type") == "item.completed"
            if isinstance(item := e.get("item"), dict) and item.get("type") == "agent_message"
        ]
        raw = completed.get("usage") or {}
        totals = {
            key: int(raw.get(key) or 0)
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens")
        }
        previous = self._usage_totals if self._usage_totals.get("session_id") == session_id else {}
        delta = {key: max(0, value - int(previous.get(key) or 0)) for key, value in totals.items()}
        self._usage_totals = {"session_id": session_id, **totals}
        usage = {
            "input_tokens": max(0, delta["input_tokens"] - delta["cached_input_tokens"]),
            "output_tokens": delta["output_tokens"],
            "cache_read_input_tokens": delta["cached_input_tokens"],
            "cache_creation_input_tokens": delta["cache_write_input_tokens"],
            "codex_usage": dict(self._usage_totals),
            "session_id": session_id,
            "model": config.codex_model_for(self._config.role),
            "cost_usd": None,
        }
        return "\n".join(messages), usage


def _missing_session(detail: str) -> bool:
    lower = detail.lower()
    return ("session" in lower or "conversation" in lower or "thread" in lower) and (
        "not found" in lower or "does not exist" in lower
    )


def _did_work(events: list[dict]) -> bool:
    return any(
        isinstance(e.get("item"), dict) and e["item"].get("type") in _WORK_ITEMS
        for e in events
        if e.get("type") in {"item.started", "item.completed"}
    )
