"""Pure view model: fleet snapshot dict -> Home and Project desk data.

No logic about holds, merges or crews lives here. It groups what the snapshot already
decided (``hold_bucket``, ``captain_actionable``, ``open_decisions``, the captain
contribution list) by project, and reads every field defensively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

NO_PROJECT = "General"
STATE_ORDER = ("in_flight", "queued", "done")


@dataclass
class NeedsYou:
    project: str
    kind: str  # decision | merge | hold
    ref: str
    text: str
    url: str | None = None
    title: str = ""  # the work item's human title, when the snapshot knows it
    gated: bool = False  # a hold on a work item: answering releases it instead of closing
    owner: str = ""  # the home that owns the work item; empty when the snapshot omits it


@dataclass
class Row:
    id: str
    title: str
    state: str
    blocked_by: list[str]
    hold: str | None
    pr_url: str | None
    owner: str = ""  # the home that owns the ticket; empty when the snapshot omits it


@dataclass
class Crew:
    id: str
    kind: str
    harness: str
    state: str
    detail: str
    title: str = ""


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
    more: dict[str, int] = field(default_factory=dict)  # second-mate tickets the roll-up cut off


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
    task_title: dict[str, str] = {}

    def add_record(rec: dict) -> None:
        tid = _s(rec.get("id"))
        if tid in task_project:  # one owning home per ticket; the first read wins
            return
        name = _project_name(rec.get("repo"))
        owner = _s(rec.get("owner"))
        task_project[tid] = name
        task_title[tid] = _s(rec.get("title"))
        hold = _s(rec.get("hold_reason")) or None
        project(name).rows.append(
            Row(
                id=tid,
                title=_s(rec.get("title")),
                state=_s(rec.get("state"), "queued"),
                blocked_by=[
                    b for b in _list(rec.get("unresolved_blocker_ids")) if isinstance(b, str)
                ],
                hold=hold,
                pr_url=_s(rec.get("pr_url")) or None,
                owner=owner,
            )
        )
        if rec.get("captain_actionable") is True:
            project(name).needs_you.append(
                NeedsYou(
                    name,
                    "hold",
                    tid,
                    hold or _s(rec.get("title")),
                    title=_s(rec.get("title")),
                    gated=_s(rec.get("kind")) != "captain",
                    owner=owner,
                )
            )

    for rec in _list(_list_of(data, "backlog", "records")):
        if isinstance(rec, dict):
            add_record(rec)

    # Second mates own tickets too (ADR 0030): read their roll-up as the same rows.
    more: dict[str, int] = {}
    for mate in _list(_list_of(data, "secondmate_current", "records")):
        if not isinstance(mate, dict):
            continue
        owner = _s(mate.get("id"))
        seen: set[str] = set()
        for rec in _list(mate.get("queued")):
            if isinstance(rec, dict):
                seen.add(_s(rec.get("id")))
                add_record({"owner": owner, **rec})
        for child in _list(mate.get("active_children")):
            if isinstance(child, dict):
                seen.add(_s(child.get("id")))
                add_record(
                    {
                        "owner": owner,
                        "id": child.get("id"),
                        "title": child.get("name"),
                        "repo": child.get("repo"),
                        "state": "in_flight",
                    }
                )
        for dec in _list(mate.get("decisions_open")):  # a hold the bounded queued list cut off
            if not isinstance(dec, dict) or dec.get("verb") != "captain-hold":
                continue
            seen.add(_s(dec.get("id")))
            add_record(
                {
                    "owner": owner,
                    "id": dec.get("id"),
                    "title": dec.get("summary"),
                    "hold_reason": dec.get("reason"),
                    "kind": "captain",
                    "captain_actionable": True,
                }
            )
        counts = mate.get("counts") if isinstance(mate.get("counts"), dict) else {}
        owned = sum(
            n for n in (counts.get("queued"), counts.get("active_children")) if isinstance(n, int)
        )
        if owned > len(seen):
            more[owner] = owned - len(seen)

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
                task_title.get(tid, ""),
            )
        )
        hints = task.get("hints") if isinstance(task.get("hints"), dict) else {}
        for dec in _list(hints.get("open_decisions")):
            if isinstance(dec, dict):
                project(name).needs_you.append(
                    NeedsYou(
                        name,
                        "decision",
                        f"{tid}/{_s(dec.get('key'))}",
                        _s(dec.get("summary")),
                        title=task_title.get(tid, ""),
                    )
                )

    for item in _list(_list_of(data, "contributions", "captain")):
        if not isinstance(item, dict):
            continue
        tid = _s(item.get("task"))
        name = task_project.get(tid, NO_PROJECT)
        project(name).needs_you.append(
            NeedsYou(
                name,
                "merge",
                tid,
                _s(item.get("reason")),
                _s(item.get("url")) or None,
                task_title.get(tid, ""),
            )
        )

    for proj in projects.values():
        proj.rows.sort(key=lambda r: STATE_ORDER.index(r.state) if r.state in STATE_ORDER else 99)
    needs = [n for p in projects.values() for n in p.needs_you]
    ordered = dict(
        sorted(projects.items(), key=lambda kv: (-len(kv[1].needs_you), kv[0] == NO_PROJECT, kv[0]))
    )
    generated = data.get("generated") if isinstance(data.get("generated"), str) else None
    return Desk(generated, ordered, needs, more)


def _list_of(data: dict, section: str, key: str) -> object:
    sect = data.get(section)
    return sect.get(key) if isinstance(sect, dict) else None
