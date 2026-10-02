"""Write actions: each one is a fixed firstmate script called with an argument list.

The gateway holds no logic about holds, merges or crews. It validates ids against the
live snapshot, builds the text to record, and hands it to the script that owns the
mechanics (``fm-captain-hold.sh``, ``fm-inbox.sh``, ``fm-control.sh``). It never edits a
backlog file and never calls a shell. Message shapes for the first mate are documented in
``docs/gateway-requests.md``; keep that file in step with the builders here.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from hive.gateway.settings import GatewaySettings

audit_log = logging.getLogger("hive.gateway.audit")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUEST_ID_RE = re.compile(r"^web-[0-9a-f]{16}$")
MAX_ANSWER_BYTES = 6000  # the script caps a decision at 8192; leave room for provenance
MAX_NOTE_CHARS = 4000
SCRIPT_TIMEOUT_S = 30.0
STEP_UP_TTL_S = 180
TICKET_FIELDS = ("title", "body", "priority")
CONTROL_VERBS = ("interrupt", "relaunch")


class ActionError(Exception):
    """A refused or failed action; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Outcome:
    action: str
    subject: str
    summary: str  # sanitised script output, safe to show


# ---- CSRF and step-up tokens ------------------------------------------------------


class Tokens:
    """Stateless HMAC tokens keyed by a per-process secret.

    ``csrf`` goes in every form. ``step_up`` proves the confirm page was shown for exactly
    this action and subject within the last few minutes.
    """

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)

    def _mac(self, *parts: str) -> str:
        msg = "\x1f".join(parts).encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def csrf(self) -> str:
        return self._mac("csrf")

    def check_csrf(self, token: str) -> bool:
        return hmac.compare_digest(token, self.csrf())

    def step_up(self, action: str, subject: str, now: float | None = None) -> str:
        exp = str(int((now if now is not None else time.time()) + STEP_UP_TTL_S))
        return f"{exp}.{self._mac('step', action, subject, exp)}"

    def check_step_up(
        self, token: str, action: str, subject: str, now: float | None = None
    ) -> bool:
        exp, _, mac = token.partition(".")
        if not exp.isdigit() or int(exp) < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(mac, self._mac("step", action, subject, exp))


def new_request_id() -> str:
    return f"web-{secrets.token_hex(8)}"


class RunOnce:
    """In-memory request ids seen within the step-up lifetime.

    A repeat POST with the same id awaits the first run and gets its result instead of
    running the script again. A failed run is forgotten so a corrected retry can run.
    Nothing is persisted; expired ids are pruned.
    """

    def __init__(self, ttl_s: float = STEP_UP_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._runs: dict[str, tuple[float, asyncio.Future[Outcome]]] = {}

    async def run(self, request_id: str, start: Callable[[], Awaitable[Outcome]]) -> Outcome:
        now = time.time()
        self._runs = {k: v for k, v in self._runs.items() if v[0] > now}
        if request_id not in self._runs:
            self._runs[request_id] = (now + self._ttl_s, asyncio.ensure_future(start()))
        run = self._runs[request_id][1]
        try:
            return await asyncio.shield(run)
        except ActionError:
            if self._runs.get(request_id, (0.0, None))[1] is run:
                del self._runs[request_id]
            raise


# ---- running scripts --------------------------------------------------------------


def _clean(text: str, limit: int = 600) -> str:
    """One bounded printable line block for showing script output."""
    text = "".join(c if c.isprintable() or c in "\n\t" else " " for c in text).strip()
    return text[:limit]


async def run_script(
    settings: GatewaySettings,
    script: str,
    *args: str,
    stdin: str | None = None,
    merge_stderr: bool = True,
) -> tuple[int, str]:
    """Run ``<fm_home>/bin/<script>`` with an argument list. Returns (exit code, output)."""
    path = settings.fm_home / "bin" / script
    if not path.is_file():
        raise ActionError(f"firstmate script {script} not found", 502)
    env = {**os.environ, "FM_HOME": str(settings.fm_home)}
    try:
        proc = await asyncio.create_subprocess_exec(
            str(path),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.DEVNULL,
            env=env,
            cwd=str(settings.fm_home),
        )
    except OSError as exc:
        raise ActionError(f"could not run {script}", 502) from exc
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), SCRIPT_TIMEOUT_S
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ActionError(f"{script} timed out", 504) from exc
    return proc.returncode or 0, out.decode("utf-8", "replace")


def audit(action: str, subject: str, outcome: str, **extra: object) -> None:
    """One line per action. Never logs free text, only its length."""
    parts = [f"action={action}", f"subject={subject}", f"outcome={outcome}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    audit_log.info("gateway-audit %s", " ".join(parts))


# ---- validation -------------------------------------------------------------------


def check_id(value: str, what: str) -> str:
    if not ID_RE.fullmatch(value):
        raise ActionError(f"invalid {what}")
    return value


def check_text(value: str, what: str, limit: int = MAX_NOTE_CHARS) -> str:
    value = value.replace("\r\n", "\n").strip()
    if not value:
        raise ActionError(f"{what} is empty")
    if len(value) > limit:
        raise ActionError(f"{what} is too long (max {limit} characters)")
    return value


def check_line(value: str, what: str, limit: int = 200) -> str:
    value = check_text(value, what, limit)
    if "\n" in value:
        raise ActionError(f"{what} must be a single line")
    return value


# ---- message shapes (see docs/gateway-requests.md) ---------------------------------


def ticket_request_body(
    action: str, ticket: str, project: str, field: str, text: str, owner: str
) -> str:
    return (
        "HIVE-WEB TICKET REQUEST v1\n"
        f"action: {action}\n"
        f"ticket: {ticket}\n"
        f"project: {project}\n"
        f"field: {field}\n"
        f"from: hive web ({owner})\n"
        "---\n"
        f"{text}\n"
    )


def merge_word_body(task: str, pr_url: str, owner: str) -> str:
    return (
        "HIVE-WEB MERGE WORD v1\n"
        f"task: {task}\n"
        f"pr: {pr_url or '(none)'}\n"
        f"from: hive web ({owner})\n"
        "---\n"
        "The owner gives the merge word for this PR. Record it and merge; "
        "the website never merges.\n"
    )


def decision_note_body(task: str, key: str, text: str, owner: str) -> str:
    return (
        "HIVE-WEB DECISION ANSWER v1\n"
        f"task: {task}\n"
        f"decision: {key}\n"
        f"from: hive web ({owner})\n"
        "---\n"
        f"{text}\n"
    )


def provenance_footer(owner: str) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return f"\n\n[answered from the Hive website by {owner} at {stamp}]"


# ---- actions ----------------------------------------------------------------------


def _first_line(output: str) -> str:
    for line in output.splitlines():
        if line.strip():
            return _clean(line, 300)
    return "ok"


async def send_note(
    settings: GatewaySettings, body: str, request_id: str, action: str, subject: str
) -> Outcome:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ActionError("invalid request id")
    code, out = await run_script(
        settings, "fm-inbox.sh", "note", "--request-id", request_id, "--json", "-", stdin=body
    )
    # Exit 3 means saved but not announced; the note is durable, so it is not a failure.
    if code not in (0, 3):
        audit(action, subject, "failed", exit=code)
        raise ActionError(f"fm-inbox note failed: {_first_line(out)}", 502)
    try:
        info = json.loads(out)
    except ValueError:
        info = {}
    outcome = info.get("outcome") if isinstance(info, dict) else None
    audit(action, subject, outcome or "sent", exit=code, request_id=request_id, chars=len(body))
    if outcome == "replay":
        summary = "Already sent (this request was recorded before)."
    elif code == 3:
        summary = "Saved; the first mate has not been woken yet and will see it on its next pass."
    else:
        summary = "Sent to the first mate."
    return Outcome(action, subject, summary)


async def answer_hold(
    settings: GatewaySettings, task: str, text: str, release: bool, owner: str
) -> Outcome:
    check_id(task, "task id")
    text = check_text(text, "answer", MAX_ANSWER_BYTES)
    body = text + provenance_footer(owner)
    if len(body.encode()) > MAX_ANSWER_BYTES + 400:
        raise ActionError("answer is too long")
    fd, name = tempfile.mkstemp(prefix="hive-gw-answer-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        args = ["answer", task, "--decision-file", name]
        if release:
            args.append("--release")
        code, out = await run_script(settings, "fm-captain-hold.sh", *args)
    finally:
        Path(name).unlink(missing_ok=True)
    subject = f"{task}{' release' if release else ''}"
    if code != 0:
        audit("answer", subject, "failed", exit=code, chars=len(text))
        raise ActionError(f"fm-captain-hold refused: {_first_line(out)}", 409)
    audit("answer", subject, "recorded", exit=code, chars=len(text))
    return Outcome("answer", subject, _clean(out) or "Recorded.")


async def control(settings: GatewaySettings, task: str, verb: str, note: str | None) -> Outcome:
    check_id(task, "task id")
    if verb not in CONTROL_VERBS:
        raise ActionError("unsupported control verb")
    args = [task, verb]
    if verb == "relaunch":
        args += ["--note", check_text(note or "", "relaunch note", 1000)]
    code, out = await run_script(settings, "fm-control.sh", *args)
    subject = f"{task} {verb}"
    if code != 0:
        audit("control", subject, "failed", exit=code)
        raise ActionError(f"fm-control refused: {_first_line(out)}", 409)
    audit("control", subject, "done", exit=code)
    return Outcome("control", subject, _clean(out) or "Done.")


async def read_json_script(settings: GatewaySettings, script: str, *args: str) -> dict | None:
    """Best-effort read of a JSON-emitting script; None when unavailable."""
    try:
        code, out = await run_script(settings, script, *args, merge_stderr=False)
    except ActionError:
        return None
    if code != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
