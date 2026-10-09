"""Firstmate's merge posture and worker settings, read-only and never copied.

Hive carries no merge rules and no worker defaults of its own. Everything here reads the
first mate's files under ``HIVE_GATEWAY_FM_HOME`` on each call (they are tiny) and reports
what they say, or why they cannot be read. Nothing is cached, written or guessed:

- ``data/projects.md``: per-project delivery mode and ``+yolo`` (format owned by
  firstmate's ``bin/fm-project-mode.sh``).
- ``config/crew-dispatch.json``, ``config/crew-harness``, ``config/claude-permission-mode``:
  the worker profiles (schema owned by firstmate's ``docs/configuration.md``).

Where firstmate falls back to a default for a missing or unknown value, this module does
not: a missing, unparsable or ambiguous source is an ``error`` and every gate built on it
(``merge_decision``, ``worker_gate``) answers "ask the captain".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("no-mistakes", "no-mistakes-prod-only", "direct-PR", "local-only")
PERMISSION_MODES = ("bypass", "auto")
_LINE = re.compile(r"^- (?P<name>\S+)(?: \[(?P<flags>[^\]]*)\])?(?: - .*)?$")


@dataclass(frozen=True)
class Posture:
    mode: str
    yolo: bool


@dataclass(frozen=True)
class Projects:
    postures: dict[str, Posture] = field(default_factory=dict)
    # Names listed more than once or with an unreadable annotation: ambiguous, never merged.
    ambiguous: dict[str, str] = field(default_factory=dict)
    error: str = ""  # the whole registry is unusable


@dataclass(frozen=True)
class Profile:
    harness: str
    model: str = ""
    effort: str = ""


@dataclass(frozen=True)
class Rule:
    when: str
    profiles: tuple[Profile, ...]


@dataclass(frozen=True)
class Dispatch:
    rules: tuple[Rule, ...] = ()
    default: tuple[Profile, ...] = ()
    error: str = ""
    absent: bool = False  # no dispatch file: firstmate then uses crew-harness alone


@dataclass(frozen=True)
class Value:
    """One small config file: its text, or why it is unavailable."""

    text: str = ""
    error: str = ""
    absent: bool = False  # the file simply does not exist


@dataclass(frozen=True)
class FmConfig:
    projects: Projects
    dispatch: Dispatch
    harness: Value
    permission: Value  # the validated token, "bypass" when the file is absent

    @property
    def workers_ready(self) -> str:
        """Empty when workers can be created from this config, else the reason they cannot."""
        return worker_gate(self)


def _read(path: Path) -> tuple[str, str, bool]:
    """(text, error, absent). Never raises."""
    try:
        if not path.is_file():
            return "", f"{path.name} not found", True
        return path.read_text(encoding="utf-8"), "", False
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"{path.name} unreadable ({type(exc).__name__})", False


def read_projects(fm_home: Path) -> Projects:
    text, err, _ = _read(fm_home / "data" / "projects.md")
    if err:
        return Projects(error=err)
    postures: dict[str, Posture] = {}
    ambiguous: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.startswith("- "):
            continue  # headings, blank lines and prose are not registry rows
        m = _LINE.match(raw.rstrip())
        if m is None:
            word = raw[2:].split(None, 1)[0] if raw[2:].strip() else "?"
            ambiguous[word] = "row not understood"
            continue
        name = m["name"]
        if name.startswith("["):  # an annotation where the name belongs
            ambiguous[name] = "row has no project name"
            continue
        tokens = (m["flags"] or "").split()
        yolo = "+yolo" in tokens
        modes = [t for t in tokens if t != "+yolo"]
        if name in postures or name in ambiguous:
            ambiguous[name] = "listed more than once"
            postures.pop(name, None)
        elif len(modes) > 1 or (modes and modes[0] not in MODES):
            ambiguous[name] = f"unknown mode {' '.join(modes)!r}"
        else:
            postures[name] = Posture(modes[0] if modes else "no-mistakes", yolo)
    if not postures and not ambiguous:
        return Projects(error="projects.md lists no projects")
    return Projects(postures, ambiguous)


def _profile(obj: object, where: str) -> Profile:
    if not isinstance(obj, dict) or not isinstance(obj.get("harness"), str) or not obj["harness"]:
        raise ValueError(f"{where}: a profile needs a harness")
    out = {}
    for key in ("model", "effort"):
        value = obj.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{where}: {key} must be text")
        out[key] = value
    return Profile(obj["harness"], out["model"], out["effort"])


def _profiles(obj: object, where: str) -> tuple[Profile, ...]:
    items = obj if isinstance(obj, list) else [obj]
    if not items:
        raise ValueError(f"{where}: empty profile list")
    return tuple(_profile(o, where) for o in items)


def read_dispatch(fm_home: Path) -> Dispatch:
    text, err, absent = _read(fm_home / "config" / "crew-dispatch.json")
    if err:
        return Dispatch(error=err, absent=absent)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        raw_rules = data.get("rules", [])
        if not isinstance(raw_rules, list):
            raise ValueError("rules must be a list")
        rules = []
        for i, r in enumerate(raw_rules, 1):
            if not isinstance(r, dict) or not isinstance(r.get("when"), str) or "use" not in r:
                raise ValueError(f"rule {i}: needs when and use")
            rules.append(Rule(r["when"], _profiles(r["use"], f"rule {i}")))
        default = _profiles(data["default"], "default") if "default" in data else ()
    except (ValueError, RecursionError) as exc:  # json.JSONDecodeError is a ValueError
        return Dispatch(error=f"crew-dispatch.json unparsable ({exc})")
    if not rules and not default:
        return Dispatch(error="crew-dispatch.json has no rules and no default")
    return Dispatch(tuple(rules), default)


def read_harness(fm_home: Path) -> Value:
    text, err, absent = _read(fm_home / "config" / "crew-harness")
    if err:
        return Value(error=err, absent=absent)
    word = text.strip()
    if not word or len(word.split()) != 1:
        return Value(error="crew-harness must hold one adapter name")
    return Value(word)


def read_permission(fm_home: Path) -> Value:
    """Absent means firstmate's own default (``bypass``); any other bad value is an error."""
    text, err, absent = _read(fm_home / "config" / "claude-permission-mode")
    if absent:
        return Value("bypass", absent=True)
    if err:
        return Value(error=err)
    word = text.strip()
    if word not in PERMISSION_MODES:
        return Value(error=f"claude-permission-mode must be one of {', '.join(PERMISSION_MODES)}")
    return Value(word)


def load(fm_home: Path) -> FmConfig:
    return FmConfig(
        read_projects(fm_home),
        read_dispatch(fm_home),
        read_harness(fm_home),
        read_permission(fm_home),
    )


# ---- gates ------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeDecision:
    """``self_merge`` is True only when firstmate's own posture says it may merge this on
    its own. Anything else is the captain's call, with ``reason`` saying why."""

    self_merge: bool
    reason: str

    @property
    def ask_captain(self) -> bool:
        return not self.self_merge


def merge_decision(
    projects: Projects,
    project: str,
    *,
    checks_red: bool | None,
    destructive: bool | None,
    security_sensitive: bool | None,
) -> MergeDecision:
    """May this merge happen without the captain's word? Fail-safe: only an explicit
    ``+yolo`` on a readable, unambiguous row, with every risk flag known to be False.
    A flag of ``None`` means "not known", which is treated as true."""
    if projects.error:
        return MergeDecision(False, f"merge posture unknown: {projects.error}")
    if project in projects.ambiguous:
        return MergeDecision(False, f"merge posture ambiguous: {projects.ambiguous[project]}")
    posture = projects.postures.get(project)
    if posture is None:
        return MergeDecision(False, "merge posture unknown: project not in projects.md")
    for flag, why in (
        (checks_red, "checks are red or not known green"),
        (destructive, "destructive or not known safe"),
        (security_sensitive, "security-sensitive or not known safe"),
    ):
        if flag is not False:
            return MergeDecision(False, f"captain approval required: {why}")
    if not posture.yolo:
        return MergeDecision(False, f"{posture.mode} without yolo: captain approval required")
    return MergeDecision(True, f"{posture.mode} +yolo: firstmate may merge green, in-scope work")


def posture_label(projects: Projects, project: str) -> str:
    """One short word group for a note or a tag: ``no-mistakes +yolo`` or ``unknown (why)``."""
    d = merge_decision(
        projects, project, checks_red=None, destructive=None, security_sensitive=None
    )
    if project in projects.ambiguous or projects.error or project not in projects.postures:
        return "unknown (" + d.reason.split(": ", 1)[-1] + ")"
    p = projects.postures[project]
    return f"{p.mode}{' +yolo' if p.yolo else ''}"


def worker_gate(cfg: FmConfig) -> str:
    """Empty when workers may be created, else why not. Mirrors firstmate's own rule: a
    dispatch file, when present, must parse; with none, ``crew-harness`` must name the
    adapter. The permission token must be valid either way."""
    if cfg.permission.error:
        return cfg.permission.error
    if not cfg.dispatch.error:
        return ""
    if cfg.dispatch.absent:
        return "" if not cfg.harness.error else f"{cfg.dispatch.error}; {cfg.harness.error}"
    return cfg.dispatch.error
