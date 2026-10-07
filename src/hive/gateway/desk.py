"""Pure view model: fleet snapshot dict -> Home and Project desk data.

No logic about holds, merges or crews lives here. It groups what the snapshot already
decided (``hold_bucket``, ``captain_actionable``, ``open_decisions``, the captain
contribution list, each second mate's ``secondmate_current`` roll-up) by project, and
reads every field defensively. ``glance`` derives a project card's state from those
groups, so a card is blocked exactly when the project has a needs-you item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

NO_PROJECT = "General"
STATE_ORDER = ("in_flight", "queued", "done")
# A mate's captain hold, echoed onto the primary's status channel as a needs-decision line.
CAPTAIN_HOLD_KEY = re.compile(r"captain-hold-(.+)-\d+")
RUNNING = "working"  # the crew state that counts as a running loop
# The needs-you lane sorts by kind in a fixed order, so each kind keeps its place (T001).
_KIND_ORDER = {"decision": 0, "hold": 1, "merge": 2}


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
    body: str = ""  # the ticket's notes, when the snapshot carries them (first mate's only)


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
    mate: bool = False  # a second mate owns this project; otherwise the first mate answers

    def count(self, state: str) -> int:
        return sum(1 for r in self.rows if r.state == state)

    @property
    def running(self) -> list[Crew]:
        """Working loops; a second mate's own session supervises, so it is not one."""
        return [c for c in self.crews if c.state == RUNNING and c.kind != "secondmate"]


@dataclass(frozen=True)
class Glance:
    """What a project card shows: loop status, what it is doing now, and progress."""

    status: str  # blocked (a needs-you item exists) | running | idle
    lead: str  # the bold opening of the activity line; may be empty
    now: str
    done: int
    total: int


_NEED_WORD = {"hold": "question", "decision": "decision", "merge": "merge approval"}


def glance(p: Project) -> Glance:
    done, total = p.count("done"), len(p.rows)
    if p.needs_you:
        if len(p.needs_you) == 1:
            n = p.needs_you[0]
            what = f"{_NEED_WORD.get(n.kind, n.kind)}: {n.title or n.text or n.ref}"
        else:
            what = f"{len(p.needs_you)} items"
        return Glance("blocked", "Waiting on you", f"{what} (open the project)", done, total)
    running = p.running
    if running:
        first = running[0]
        now = first.title or first.id
        if len(running) > 1:
            now += f" · {len(running)} crews in flight"
        return Glance("running", "Working", now, done, total)
    queued = p.count("queued")
    parts = ["Idle", f"{queued} queued" if queued else "nothing queued"]
    parked = [c for c in p.crews if c.kind != "secondmate"]
    if parked:
        parts.append(f"{len(parked)} crew{'s' if len(parked) != 1 else ''} parked")
    return Glance("idle", "", " · ".join(parts), done, total)


@dataclass
class BacklogGroup:
    """One project's slice of the "Needs you" lane: what is waiting on the owner (a captain
    hold) and what is queued behind it. In-flight chatter and worker questions are not here."""

    project: str
    mate: bool
    waiting: list[NeedsYou]
    queued: list[Row]

    @property
    def count(self) -> int:
        return len(self.waiting) + len(self.queued)


@dataclass
class Desk:
    generated: str | None
    projects: dict[str, Project]
    needs_you: list[NeedsYou]
    more: dict[str, int] = field(default_factory=dict)  # second-mate tickets the roll-up cut off
    backlog: list[BacklogGroup] = field(default_factory=list)

    @property
    def backlog_count(self) -> int:
        return sum(g.count for g in self.backlog)

    @property
    def loops_running(self) -> int:
        return sum(len(p.running) for p in self.projects.values())

    def default_focus(self) -> str | None:
        """The sole real project, selected on load; None means the first mate, unfocused."""
        real = [name for name in self.projects if name != NO_PROJECT]
        return real[0] if len(real) == 1 else None


def _s(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _body(rec: dict) -> str:
    """A record's notes: the body lines, else the excerpt."""
    lines = [x for x in _list(rec.get("body_lines")) if isinstance(x, str)]
    return "\n".join(lines) if lines else _s(rec.get("body_excerpt"))


def _project_name(value: object) -> str:
    name = PurePath(_s(value)).name if value else ""
    return name or NO_PROJECT


def build_desk(data: dict) -> Desk:
    projects: dict[str, Project] = {}

    def project(name: str) -> Project:
        return projects.setdefault(name, Project(name))

    for name in _list(data.get("registered_projects")):  # an idle project still has a card
        if isinstance(name, str) and name:
            project(name)

    task_project: dict[str, str] = {}
    task_title: dict[str, str] = {}
    hold_ids: set[str] = set()  # ids already on the desk as a hold card

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
                body=_body(rec),
            )
        )
        if rec.get("captain_actionable") is True:
            hold_ids.add(tid)
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

    # A second mate's session is a task naming the projects it owns; a mate record without
    # a repo (a hold the bounded queued list cut off) belongs to that mate's project.
    mate_home: dict[str, str] = {}
    for task in _list(data.get("tasks")):
        if isinstance(task, dict):
            owned = [p for p in _list(task.get("secondmate_projects")) if isinstance(p, str)]
            if owned:
                mate_home[_s(task.get("id"))] = owned[0]

    # Second mates own tickets too (ADR 0030): read their roll-up as the same rows.
    more: dict[str, int] = {}
    for mate in _list(_list_of(data, "secondmate_current", "records")):
        if not isinstance(mate, dict):
            continue
        owner = _s(mate.get("id"))
        home_repo = mate_home.get(owner)

        def add_mate_record(rec: dict, owner: str = owner, repo: str | None = home_repo) -> None:
            add_record({"owner": owner, **rec, "repo": rec.get("repo") or repo})

        queued_seen: set[str] = set()
        active_seen: set[str] = set()
        for rec in _list(mate.get("queued")):
            if isinstance(rec, dict):
                queued_seen.add(_s(rec.get("id")))
                add_mate_record(rec)
        for child in _list(mate.get("active_children")):
            if isinstance(child, dict):
                active_seen.add(_s(child.get("id")))
                add_mate_record(
                    {
                        "id": child.get("id"),
                        "title": child.get("name"),
                        "repo": child.get("repo"),
                        "state": "in_flight",
                    }
                )
        for held in _list(mate.get("holds")):  # an in-flight ticket with a parked worker
            if not isinstance(held, dict) or held.get("source") != "child-state":
                continue
            queued_seen.add(_s(held.get("id")))
            add_mate_record(
                {
                    "id": held.get("id"),
                    "title": held.get("title"),
                    "hold_reason": held.get("reason"),
                    "state": "in_flight",
                }
            )
        for dec in _list(mate.get("decisions_open")):  # a hold the bounded queued list cut off
            if not isinstance(dec, dict) or dec.get("verb") != "captain-hold":
                continue
            queued_seen.add(_s(dec.get("id")))
            add_mate_record(
                {
                    "id": dec.get("id"),
                    "title": dec.get("summary"),
                    "hold_reason": dec.get("reason"),
                    "kind": "captain",
                    "captain_actionable": True,
                }
            )
        # In-flight tickets in other states (idle, done awaiting landing) have no surface in
        # the mate's summary, and the holds list's uncapped count overlaps the queued one, so
        # the remainder counts only the exact queued and working totals: it may undercount
        # parked workers but never shows a ticket that does not exist.
        counts = mate.get("counts") if isinstance(mate.get("counts"), dict) else {}
        hidden = sum(
            max(0, n - len(shown))
            for n, shown in (
                (counts.get("queued"), queued_seen),
                (counts.get("active_children"), active_seen),
            )
            if isinstance(n, int)
        )
        if hidden:
            more[owner] = hidden

    mate_projects: set[str] = set()
    for task in _list(data.get("tasks")):
        if not isinstance(task, dict):
            continue
        tid = _s(task.get("id"))
        owned = [
            _project_name(p) for p in _list(task.get("secondmate_projects")) if isinstance(p, str)
        ]
        mate_projects.update(owned)
        if owned:  # a second mate's own session runs from its home; show it on its project
            name = owned[0]
        elif task.get("project"):
            name = _project_name(task.get("project"))
        else:
            name = task_project.get(tid, NO_PROJECT)
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
            if not isinstance(dec, dict):
                continue
            echo = CAPTAIN_HOLD_KEY.fullmatch(_s(dec.get("key")))
            if echo and echo.group(1) in hold_ids:  # the hold card already asks this question
                continue
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
        proj.mate = proj.name in mate_projects
        proj.rows.sort(key=lambda r: STATE_ORDER.index(r.state) if r.state in STATE_ORDER else 99)
    needs = sorted(
        (n for p in projects.values() for n in p.needs_you),
        key=lambda n: _KIND_ORDER.get(n.kind, 9),
    )
    ordered = dict(
        sorted(projects.items(), key=lambda kv: (-len(kv[1].needs_you), kv[0] == NO_PROJECT, kv[0]))
    )
    generated = data.get("generated") if isinstance(data.get("generated"), str) else None
    return Desk(generated, ordered, needs, more, _backlog(ordered))


def _backlog(projects: dict[str, Project]) -> list[BacklogGroup]:
    """The lane: per project, captain holds first, then queued tickets that are not holds."""
    groups = []
    for p in projects.values():
        waiting = [n for n in p.needs_you if n.kind == "hold"]
        held = {n.ref for n in waiting}
        queued = [r for r in p.rows if r.state == "queued" and r.id not in held]
        if waiting or queued:
            groups.append(BacklogGroup(p.name, p.mate, waiting, queued))
    groups.sort(key=lambda g: (-len(g.waiting), g.project == NO_PROJECT, g.project))
    return groups


def _list_of(data: dict, section: str, key: str) -> object:
    sect = data.get(section)
    return sect.get(key) if isinstance(sect, dict) else None
