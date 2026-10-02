"""Pure view model: fleet snapshot dict -> Home and Project desk data.

No logic about holds, merges or crews lives here. It groups what the snapshot already
decided (``hold_bucket``, ``captain_actionable``, ``open_decisions``, the captain
contribution list) by project, and reads every field defensively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

NO_PROJECT = "(no project)"
STATE_ORDER = ("in_flight", "queued", "done")


@dataclass
class NeedsYou:
    project: str
    kind: str  # decision | merge | hold
    ref: str
    text: str
    url: str | None = None
    gated: bool = False  # a hold on a work item: answering releases it instead of closing


@dataclass
class Row:
    id: str
    title: str
    state: str
    blocked_by: list[str]
    hold: str | None
    pr_url: str | None


@dataclass
class Crew:
    id: str
    kind: str
    harness: str
    state: str
    detail: str


@dataclass
class Project:
    name: str
    needs_you: list[NeedsYou] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    crews: list[Crew] = field(default_factory=list)

    def count(self, state: str) -> int:
        return sum(1 for r in self.rows if r.state == state)


@dataclass
class Desk:
    generated: str | None
    projects: dict[str, Project]
    needs_you: list[NeedsYou]


def _s(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _project_name(value: object) -> str:
    name = PurePath(_s(value)).name if value else ""
    return name or NO_PROJECT


def build_desk(data: dict) -> Desk:
    projects: dict[str, Project] = {}

    def project(name: str) -> Project:
        return projects.setdefault(name, Project(name))

    task_project: dict[str, str] = {}
    for rec in _list(_list_of(data, "backlog", "records")):
        if not isinstance(rec, dict):
            continue
        name = _project_name(rec.get("repo"))
        task_project[_s(rec.get("id"))] = name
        hold = _s(rec.get("hold_reason")) or None
        project(name).rows.append(
            Row(
                id=_s(rec.get("id")),
                title=_s(rec.get("title")),
                state=_s(rec.get("state"), "queued"),
                blocked_by=[
                    b for b in _list(rec.get("unresolved_blocker_ids")) if isinstance(b, str)
                ],
                hold=hold,
                pr_url=_s(rec.get("pr_url")) or None,
            )
        )
        if rec.get("captain_actionable") is True:
            project(name).needs_you.append(
                NeedsYou(
                    name,
                    "hold",
                    _s(rec.get("id")),
                    hold or _s(rec.get("title")),
                    gated=_s(rec.get("kind")) != "captain",
                )
            )

    for task in _list(data.get("tasks")):
        if not isinstance(task, dict):
            continue
        tid = _s(task.get("id"))
        name = (
            _project_name(task.get("project"))
            if task.get("project")
            else task_project.get(tid, NO_PROJECT)
        )
        cur = task.get("current_state") if isinstance(task.get("current_state"), dict) else {}
        project(name).crews.append(
            Crew(
                tid,
                _s(task.get("kind")),
                _s(task.get("harness")),
                _s(cur.get("state")),
                _s(cur.get("detail")),
            )
        )
        hints = task.get("hints") if isinstance(task.get("hints"), dict) else {}
        for dec in _list(hints.get("open_decisions")):
            if isinstance(dec, dict):
                project(name).needs_you.append(
                    NeedsYou(
                        name, "decision", f"{tid}/{_s(dec.get('key'))}", _s(dec.get("summary"))
                    )
                )

    for item in _list(_list_of(data, "contributions", "captain")):
        if not isinstance(item, dict):
            continue
        tid = _s(item.get("task"))
        name = task_project.get(tid, NO_PROJECT)
        project(name).needs_you.append(
            NeedsYou(name, "merge", tid, _s(item.get("reason")), _s(item.get("url")) or None)
        )

    for proj in projects.values():
        proj.rows.sort(key=lambda r: STATE_ORDER.index(r.state) if r.state in STATE_ORDER else 99)
    needs = [n for p in projects.values() for n in p.needs_you]
    ordered = dict(
        sorted(projects.items(), key=lambda kv: (-len(kv[1].needs_you), kv[0] == NO_PROJECT, kv[0]))
    )
    generated = data.get("generated") if isinstance(data.get("generated"), str) else None
    return Desk(generated, ordered, needs)


def _list_of(data: dict, section: str, key: str) -> object:
    sect = data.get(section)
    return sect.get(key) if isinstance(sect, dict) else None
