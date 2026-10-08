"""Structure a decision / Needs-you text for the desk card: one question, a short option list
with the recommended one marked, and the full text kept behind a "More" control.

Parsing is defensive and never drops text: ``more`` always carries the original wording when
the card shows anything less than all of it. Text that does not follow ``question / options
A, B, C / recommended`` falls back to its first sentence as the question, the rest as More.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARK = re.compile(r"(?:(?<=\s)|^)\(?([A-Z])[).:]\s+(?=\S)")
_REC = re.compile(
    r"\brecommend(?:ed|s|ation)?\b[\s:=-]*(?:option\s+|choice\s+)?\(?([A-Z])\b(?![a-z])", re.I
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")
_LEAD_IN = re.compile(r"[\s:;,-]*\b(?:options?|choices?)\b[\s:;,-]*$", re.I)
_LABEL_WORDS = 8
_QUESTION_MAX = 220


@dataclass(frozen=True)
class Option:
    letter: str
    label: str
    recommended: bool = False


@dataclass(frozen=True)
class Card:
    question: str
    options: list[Option] = field(default_factory=list)
    more: str = ""  # the full original text; empty when the card already shows all of it


def _first_sentence(text: str) -> tuple[str, str]:
    m = _SENTENCE_END.search(text)
    if m:
        head, rest = text[: m.start()], text[m.end() :]
    else:
        head, rest = text, ""
    if len(head) > _QUESTION_MAX:
        cut = head.rfind(" ", 0, _QUESTION_MAX)
        head, rest = head[: cut if cut > 0 else _QUESTION_MAX].rstrip(), text[len(head[:cut]) :]
        head += "…"
    return head, rest.strip()


def _label(raw: str) -> str:
    raw = _REC.split(raw)[0]
    raw = re.split(r"(?<=[.!?;])\s|\s[—–-]\s|\s*\(", raw.strip(), maxsplit=1)[0]
    raw = raw.strip(" \t;,.:—–-")
    raw = re.sub(r"\s+(?:or|and)$", "", raw, flags=re.I)
    words = raw.split()
    if len(words) > _LABEL_WORDS:
        return " ".join(words[:_LABEL_WORDS]) + "…"
    return raw


def _options(text: str) -> tuple[int, list[tuple[str, str]]] | None:
    """(offset of the first option marker, [(letter, raw text)]) for a consecutive A, B, …
    run of at least two markers; ``None`` when the text has no such list."""
    marks = list(_MARK.finditer(text))
    for i, first in enumerate(marks):
        if first[1] != "A":
            continue
        run = [first]
        for m in marks[i + 1 :]:
            if m[1] == chr(ord(run[-1][1]) + 1):
                run.append(m)
        if len(run) < 2:
            continue
        out = []
        for j, m in enumerate(run):
            end = run[j + 1].start() if j + 1 < len(run) else len(text)
            out.append((m[1], text[m.end() : end]))
        return first.start(), out
    return None


def parse_card(text: str) -> Card:
    text = " ".join((text or "").split())
    if not text:
        return Card("")
    found = _options(text)
    if found is not None:
        start, raw = found
        rec_m = _REC.search(text[start:])
        rec = rec_m[1].upper() if rec_m else None
        question = _LEAD_IN.sub("", text[:start]).strip()
        if not question:
            question = "Pick an option"
        opts = [Option(letter, _label(body), letter == rec) for letter, body in raw]
        if all(o.label for o in opts):
            q, _ = _first_sentence(question) if len(question) > _QUESTION_MAX else (question, "")
            return Card(q, opts, text)
    question, rest = _first_sentence(text)
    return Card(question, [], text if rest or question != text else "")
