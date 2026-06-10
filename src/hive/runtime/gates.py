"""Interactive-gate primitives — structural detection + keystroke planning.

The three deep, PTY-free modules behind Ticket 003's interactive-gate bridge:

- ``Gate``            — the value object: which kind of gate + its payload.
- ``GateDetector``    — pure: parsed transcript entries -> ``Gate | None``.
- ``KeystrokePlanner``— pure: ``(gate, decision)`` -> the keys to inject.

Detection is transcript-only (ADR 0001 — the transcript is the source of
truth, never the screen). A gate is an interactive pause the Harness makes
mid-Turn; on the PTY it freezes the subprocess on a TUI menu, so no assistant
entry is ever written and the Turn would otherwise hang to the 180s timeout.

Issue #22 shipped the **plan** gate; issue #23 added the **ask**
(``AskUserQuestion``) gate. The permission-prompt gate extends ``GateDetector``
/ ``KeystrokePlanner`` in a later slice behind the same signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

GateKind = Literal["plan", "ask", "permission"]


def resolved_tool_use_ids(entries: list[dict]) -> set[str]:
    """All ``tool_use_id``s that have a matching ``tool_result``.

    The tool_use/tool_result pairing shared by gate detection and the
    transcript reader's pending-tool accept-guard (ADR 0010): a ``tool_use``
    block whose ``id`` is absent from this set is still in flight.
    """
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
        matching ``tool_result``. Ask gate = an ``AskUserQuestion`` ``tool_use``
        block with no matching ``tool_result``; its option list comes from the
        block's ``input``. The bare strings ``"ExitPlanMode"`` /
        ``"AskUserQuestion"`` inside a ``deferred_tools_delta`` are ignored —
        only the structured ``tool_use`` block counts (research.md
        false-positive note).
        """
        for entry in entries:
            attachment = entry.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "plan_mode":
                return Gate(kind="plan", payload={"plan": attachment.get("plan", "")})

        resolved_ids = resolved_tool_use_ids(entries)
        for tool_use in self._tool_use_blocks(entries):
            if tool_use.get("id") in resolved_ids:
                continue
            name = tool_use.get("name")
            if name == "ExitPlanMode":
                return Gate(kind="plan", payload=self._plan_payload(tool_use))
            if name == "AskUserQuestion":
                return Gate(kind="ask", payload=self._ask_payload(tool_use))

        return None

    @staticmethod
    def _plan_payload(tool_use: dict) -> dict[str, Any]:
        plan = ""
        tool_input = tool_use.get("input")
        if isinstance(tool_input, dict):
            plan = tool_input.get("plan", "")
        return {"plan": plan}

    @staticmethod
    def _ask_payload(tool_use: dict) -> dict[str, Any]:
        """Read the question + ordered option labels from the tool_use input.

        Claude Code's ``AskUserQuestion`` input is
        ``{"questions": [{"question": ..., "options": [{"label": ...}, ...]}]}``.
        We surface the first question and its option labels in menu order, so
        the chosen option's index is known without ever reading the screen.
        """
        question = ""
        options: list[str] = []
        tool_input = tool_use.get("input")
        if isinstance(tool_input, dict):
            questions = tool_input.get("questions")
            if isinstance(questions, list) and questions:
                first = questions[0]
                if isinstance(first, dict):
                    question = first.get("question", "")
                    raw_options = first.get("options")
                    if isinstance(raw_options, list):
                        for option in raw_options:
                            if isinstance(option, dict):
                                options.append(option.get("label", ""))
        return {"question": question, "options": options}

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

    def ask_keys(self, gate: Gate, option_index: int) -> list[str]:
        """Keys for the ``AskUserQuestion`` gate: select option ``option_index``.

        The menu lists the options in the same order as ``gate.payload["options"]``
        with the first row highlighted. Selecting option N is ``Down × N`` to
        move the cursor onto that row, then Enter — the cortexOS ``selectOption``
        pattern. Index 0 needs no navigation.
        """
        options = gate.payload.get("options") or []
        if option_index < 0 or option_index >= len(options):
            raise ValueError(
                f"option index {option_index} out of range for {len(options)} option(s)"
            )
        return [_ARROW_DOWN] * option_index + [_ENTER]
