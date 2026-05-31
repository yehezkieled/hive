"""Interactive-gate primitives — structural detection + keystroke planning.

The three deep, PTY-free modules behind Ticket 003's interactive-gate bridge:

- ``Gate``            — the value object: which kind of gate + its payload.
- ``GateDetector``    — pure: parsed transcript entries -> ``Gate | None``.
- ``KeystrokePlanner``— pure: ``(gate, decision)`` -> the keys to inject.

Detection is transcript-only (ADR 0001 — the transcript is the source of
truth, never the screen). A gate is an interactive pause the Harness makes
mid-Turn; on the PTY it freezes the subprocess on a TUI menu, so no assistant
entry is ever written and the Turn would otherwise hang to the 180s timeout.

This slice (issue #22) ships the **plan** gate only. ``AskUserQuestion`` and
permission prompts extend ``GateDetector`` / ``KeystrokePlanner`` in later
slices behind the same signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

GateKind = Literal["plan", "ask", "permission"]


@dataclass(frozen=True)
class Gate:
    """An unanswered interactive gate detected in the transcript.

    ``kind`` selects the gate type; ``payload`` carries everything the rest
    of the bridge needs (the plan text to surface, the option list for an
    ``ask`` gate, the requested tool for a permission prompt) — all read from
    the transcript, never the screen.
    """

    kind: GateKind
    payload: dict[str, Any]


class GateDetector:
    """Decides whether a Turn is parked on an unanswered gate (transcript-only).

    Pure over a list of parsed ``.jsonl`` entries: no PTY, no screen, no I/O.
    The ``.jsonl`` schema may drift, but ``entries -> Gate | None`` does not.
    """

    def detect(self, entries: list[dict]) -> Gate | None:
        """Return the unanswered gate in ``entries``, or ``None``.

        Plan gate = an ``attachment.type == "plan_mode"`` anywhere, OR an
        ``ExitPlanMode`` ``tool_use`` block whose ``tool_use_id`` has no
        matching ``tool_result``. The bare string ``"ExitPlanMode"`` inside a
        ``deferred_tools_delta`` is ignored — only the structured ``tool_use``
        block counts (research.md false-positive note).
        """
        for entry in entries:
            attachment = entry.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "plan_mode":
                return Gate(kind="plan", payload={"plan": attachment.get("plan", "")})

        resolved_ids = self._resolved_tool_use_ids(entries)
        for tool_use in self._tool_use_blocks(entries):
            if tool_use.get("name") != "ExitPlanMode":
                continue
            if tool_use.get("id") in resolved_ids:
                continue
            plan = ""
            tool_input = tool_use.get("input")
            if isinstance(tool_input, dict):
                plan = tool_input.get("plan", "")
            return Gate(kind="plan", payload={"plan": plan})

        return None

    @staticmethod
    def _tool_use_blocks(entries: list[dict]) -> list[dict]:
        """Every structured ``tool_use`` block across all assistant entries."""
        blocks: list[dict] = []
        for entry in entries:
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    blocks.append(block)
        return blocks

    @staticmethod
    def _resolved_tool_use_ids(entries: list[dict]) -> set[str]:
        """All ``tool_use_id``s that have a matching ``tool_result``."""
        resolved: set[str] = set()
        for entry in entries:
            content = (entry.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id is not None:
                        resolved.add(tool_use_id)
        return resolved


# TUI control sequences. Carriage return submits the selected menu row; the
# ANSI "cursor down" escape moves the selection one row down.
_ENTER = "\r"
_ARROW_DOWN = "\x1b[B"

# Plan-mode menu row order (verified layout, design.md open choice #2):
#   0 yes, auto-accept edits   1 yes, manual edits   2 no, keep planning
# Approve lands on the default (row 0); deny lands on the reject row (row 2).
_PLAN_REJECT_ROW_INDEX = 2


class KeystrokePlanner:
    """Translates a resolved decision into the keys for that gate's TUI menu.

    Pure: ``(gate, decision) -> list[str]``. This is the *only* module coupled
    to the Harness's on-screen menu layout (ADR 0001 flags it as the known
    TUI sensitivity). When a menu's row order changes, only this changes.
    """

    def plan_keys(self, gate: Gate, decision: str) -> list[str]:
        """Keys for the plan gate. ``decision`` is ``"approve"`` or ``"deny"``.

        Approve presses Enter on the default row. Deny arrows down to the
        reject row first, then Enter.
        """
        if decision == "approve":
            return [_ENTER]
        if decision == "deny":
            return [_ARROW_DOWN] * _PLAN_REJECT_ROW_INDEX + [_ENTER]
        raise ValueError(f"Unknown plan decision {decision!r}; expected 'approve' or 'deny'.")
