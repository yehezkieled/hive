"""Two-home contract: real firstmate scripts, throwaway primary + ``hive`` second mate.

Proves the ticket-ownership model the Hive desk relies on (ADR 0030): one owning home
per ticket, handoff in both directions, roll-up read back through the same snapshot
script the gateway runs, owner-aware decisions, and the pinned snapshot schemas. A
firstmate update that breaks any of these fails here, in CI, before it reaches the desk.
"""

from __future__ import annotations

import json
import re

import pytest

from hive.gateway import actions
from hive.gateway.desk import build_desk
from hive.gateway.snapshot import PINNED_SCHEMA_MAJOR, Snapshot, parse_snapshot, run_snapshot
from tests.firstmate_contract.conftest import MATE_ID, World

pytestmark = [pytest.mark.firstmate_contract, pytest.mark.timeout(180)]

SNAPSHOT_SCHEMA = "fm-fleet-snapshot.v1"
BUCKETS_SCHEMA = "fm-captain-hold-buckets.v1"


def _add(world: World, which: str, key: str, title: str, repo: str = "hive") -> None:
    world.tasks(which, "add", key, title, "--repo", repo, "--queue")


def _mate_record(snap: Snapshot) -> dict:
    assert snap.data is not None, snap.reason
    records = snap.data["secondmate_current"]["records"]
    return next(r for r in records if r["id"] == MATE_ID)


def _owner(world: World, which: str, key: str) -> dict[str, str]:
    out = world.run(which, "fm-ticket.sh", "owner", key, check=False).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _queued_ids(record: dict) -> list[str]:
    return [q["id"] for q in record["queued"]]


async def test_handoff_main_to_mate_rolls_up_through_the_gateway_snapshot(world: World) -> None:
    _add(world, "main", "hive-t1", "First")
    _add(world, "main", "hive-t2", "Second")
    _add(world, "main", "other-t1", "Not hive", repo="other")

    out = world.run("main", "fm-backlog-handoff.sh", MATE_ID, "hive-t1", "hive-t2").stdout
    assert "handed off 2 item(s)" in out

    assert "hive-t1" in world.backlog("mate") and "hive-t2" in world.backlog("mate")
    assert "hive-t1" not in world.backlog("main") and "other-t1" in world.backlog("main")
    assert _owner(world, "main", "hive-t1")["owner"] == MATE_ID

    # A key that matches neither backlog moves nothing (atomic refusal).
    bad = world.run("main", "fm-backlog-handoff.sh", MATE_ID, "other-t1", "ghost", check=False)
    assert bad.returncode != 0
    assert "other-t1" in world.backlog("main") and "other-t1" not in world.backlog("mate")

    world.refresh_mate()
    snap = await run_snapshot(world.settings())  # the exact path the desk takes
    assert snap.ok, snap.reason
    record = _mate_record(snap)
    assert sorted(_queued_ids(record)) == ["hive-t1", "hive-t2"]
    assert {q["owner"] for q in record["queued"]} == {MATE_ID}
    # The primary's own rows no longer carry the moved tickets.
    assert [r["id"] for r in snap.data["backlog"]["records"]] == ["other-t1"]


async def test_desk_shows_a_ticket_after_it_moves_to_the_mate(world: World) -> None:
    _add(world, "main", "hive-t1", "First")
    world.run("main", "fm-backlog-handoff.sh", MATE_ID, "hive-t1")
    world.refresh_mate()
    snap = await run_snapshot(world.settings())
    desk = build_desk(snap.data or {})
    rows = [row for p in desk.projects.values() for row in p.rows]
    assert [(r.id, r.owner) for r in rows if r.id == "hive-t1"] == [("hive-t1", MATE_ID)]


async def test_desk_lists_a_mate_held_decision_once_with_its_owner(world: World) -> None:
    _hold_in_mate(world)
    world.refresh_mate()
    snap = await run_snapshot(world.settings())
    desk = build_desk(snap.data or {})
    [need] = [n for n in desk.needs_you if n.ref == "hive-d1"]
    assert need.kind == "hold" and need.owner == MATE_ID


def _hold_in_mate(world: World, key: str = "hive-d1") -> None:
    world.run(
        "mate", "fm-captain-hold.sh", "hold", key,
        "--title", "Pick one", "--reason", "needs the captain", "--repo", "hive",
    )  # fmt: skip


async def test_mate_held_decision_is_answered_from_the_primary_and_closed_in_the_mate(
    world: World,
) -> None:
    _hold_in_mate(world)
    channel = world.main / "state" / f"{MATE_ID}.status"
    assert "needs-decision [key=captain-hold-hive-d1-1]" in channel.read_text()

    world.refresh_mate()
    record = _mate_record(await run_snapshot(world.settings()))
    [decision] = record["decisions_open"]
    assert decision["key"] == "hive-d1" and decision["owner"] == MATE_ID
    assert decision["hold_bucket"] == "live"

    # The primary's keyed-answer intake (the one every channel feeds) routes by owner.
    answer = "hive-d1\tgo with B\tOption B\n"
    out = world.run(
        "main", "fm-captain-hold.sh", "answers", "--any-origin", "--source", "hive contract test",
        stdin=answer,
    ).stdout  # fmt: skip
    assert "closed: hive-d1" in out

    assert "state: done" in world.tasks("mate", "show", "hive-d1")
    assert "hive-d1" not in world.backlog("main")  # never recorded in the wrong home
    assert re.search(r"resolved \[key=captain-hold-hive-d1-1\]", channel.read_text())

    # Replaying or re-answering never reopens or overwrites, and never reports "absent".
    again = world.run(
        "main", "fm-captain-hold.sh", "answers", "--any-origin", "--source", "hive contract test",
        stdin="hive-d1\tgo with C\tOption C\n", check=False,
    )  # fmt: skip
    assert "absent" not in again.stdout + again.stderr
    assert "hive-d1" not in world.backlog("main")
    assert channel.read_text().count("resolved [key=captain-hold-hive-d1-1]") == 1

    world.refresh_mate()
    assert _mate_record(await run_snapshot(world.settings()))["decisions_open"] == []


async def test_gateway_answer_action_reaches_a_mate_held_decision(world: World) -> None:
    _hold_in_mate(world)
    body = actions.answer_body("go with B")
    await actions.answer_hold(world.settings(), "hive-d1", body, release=False)
    assert "state: done" in world.tasks("mate", "show", "hive-d1")


async def test_reverse_handoff_mate_to_main(world: World) -> None:
    _add(world, "main", "hive-t1", "First")
    world.run("main", "fm-backlog-handoff.sh", MATE_ID, "hive-t1")
    assert _owner(world, "main", "hive-t1")["owner"] == MATE_ID

    world.run("main", "fm-backlog-handoff.sh", "--from", MATE_ID, "main", "hive-t1")

    assert "hive-t1" in world.backlog("main")
    assert "hive-t1" not in world.backlog("mate")
    assert _owner(world, "main", "hive-t1")["owner"] == "main"
    world.refresh_mate()
    snap = await run_snapshot(world.settings())
    assert [r["id"] for r in snap.data["backlog"]["records"]] == ["hive-t1"]
    assert _queued_ids(_mate_record(snap)) == []


async def test_new_ticket_in_one_step_lands_in_the_mate_and_is_editable_from_main(
    world: World,
) -> None:
    out = world.run(
        "main", "fm-ticket.sh", "new", "hive", "Fresh ticket", "--key", "hive-new",
        "--body", "first line",
    ).stdout  # fmt: skip
    assert "hive-new" in out
    assert "hive-new" in world.backlog("mate") and "hive-new" not in world.backlog("main")
    assert _owner(world, "main", "hive-new")["owner"] == MATE_ID

    # Routed edit from the primary is applied by the owner, with a receipt, idempotently.
    edit = world.run(
        "main", "fm-ticket.sh", "edit", "hive-new", "--request-id", "req-1",
        "--title", "Fresh ticket v2", "--note", "prefer X",
    ).stdout  # fmt: skip
    assert "status=applied" in edit and f"owner={MATE_ID}" in edit
    shown = world.tasks("mate", "show", "hive-new", "--full")
    assert "Fresh ticket v2" in shown and "prefer X" in shown and "first line" in shown
    assert (world.mate / "state" / "ticket-receipts" / "req-1.receipt").is_file()
    world.run(
        "main", "fm-ticket.sh", "edit", "hive-new", "--request-id", "req-1",
        "--title", "Fresh ticket v2", "--note", "prefer X",
    )  # fmt: skip
    assert world.tasks("mate", "show", "hive-new", "--full").count("prefer X") == 1
    assert "hive-new" not in world.backlog("main")

    world.refresh_mate()
    record = _mate_record(await run_snapshot(world.settings()))
    assert _queued_ids(record) == ["hive-new"]
    assert record["queued"][0]["title"] == "Fresh ticket v2"


async def test_snapshot_schemas_the_desk_pins_still_match(world: World) -> None:
    world.run(
        "mate", "fm-captain-hold.sh", "hold", "hive-d1",
        "--title", "Pick", "--reason", "needs the captain", "--repo", "hive",
    )  # fmt: skip
    world.refresh_mate()

    raw = world.run("main", "fm-fleet-snapshot.sh", "--json").stdout
    assert json.loads(raw)["schema"] == SNAPSHOT_SCHEMA
    snap = parse_snapshot(raw)
    assert snap.ok and snap.schema == SNAPSHOT_SCHEMA
    assert SNAPSHOT_SCHEMA == f"fm-fleet-snapshot.v{PINNED_SCHEMA_MAJOR}"

    ledger = json.loads((world.mate / "state" / "home-summary.json").read_text())
    assert ledger["schema"] == "fm-secondmate-home-summary.v1"
    assert ledger["hold_classifier_schema"] == BUCKETS_SCHEMA
    [decision] = _mate_record(snap)["decisions_open"]
    assert decision["hold_bucket"] in {"live", "blocked", "dated", "aged"}


async def test_unknown_major_makes_the_desk_read_only(world: World, tmp_path) -> None:
    """A real snapshot re-labelled v2 (the recorded-fixture form) takes the fallback."""
    raw = world.run("main", "fm-fleet-snapshot.sh", "--json").stdout
    newer = json.dumps({**json.loads(raw), "schema": "fm-fleet-snapshot.v2"})
    parsed = parse_snapshot(newer)
    assert not parsed.ok and parsed.data is None
    assert parsed.schema == "fm-fleet-snapshot.v2"
    assert "newer than this desk" in (parsed.reason or "")

    # And end to end: the gateway runs a script that now speaks v2.
    home = tmp_path / "newer-home"
    (home / "bin").mkdir(parents=True)
    script = home / "bin" / "fm-fleet-snapshot.sh"
    fixture = home / "snapshot.json"
    fixture.write_text(newer)
    script.write_text(f"#!/usr/bin/env bash\ncat {fixture}\n")
    script.chmod(0o755)
    settings = world.settings()
    settings = type(settings)(fm_home=home, live=False, snapshot_ttl_s=0)
    snap = await run_snapshot(settings)
    assert not snap.ok and snap.schema == "fm-fleet-snapshot.v2"

    # Added fields within the pinned major are tolerated.
    assert parse_snapshot(json.dumps({**json.loads(raw), "added_later": {"x": 1}})).ok
