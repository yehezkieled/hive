"""Ticket 039 — the per-card ``awaiting_you`` flag + "awaiting-you" badge.

``build_landing_view_model`` rolls up "blocked on a human" state onto each
maestro card: the maestro's own 029 ``awaiting_decision`` OR an interactive
gate (003), OR the same on any lead beneath it (the ``maestro.`` name prefix,
the rollup idiom ``_open_tasks_for`` already uses). The flag also rides the
``idle`` rows and the cold-start otter stub.

These are server-side view-model + Jinja render unit tests. The "Waiting on me"
filter chip is a CSS/JS body-class toggle verified by the deployed iPad smoke —
out of scope here, exactly like the Ticket 017 run-card tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from hive.models.entity import Entity, EntityState
from hive.models.maestro import Maestro
from hive.web.view_model import build_landing_view_model

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "hive" / "web" / "templates"


def _render(partial: str, view: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    return env.get_template(partial).render(view=view)


class _FakePM:
    """Minimal ProcessManager stand-in: ``entities`` + ``is_parked_at_gate``.

    No ``progress_store`` attribute, so ``_runs_for`` degrades to ``[]`` via its
    defensive ``getattr`` — keeps the test independent of Ticket 017's store.
    """

    def __init__(self, entities: list[Entity], gated: set[str] | None = None) -> None:
        self._entities = {e.name: e for e in entities}
        self._gated = gated or set()

    @property
    def entities(self) -> dict[str, Entity]:
        return dict(self._entities)

    def is_parked_at_gate(self, name: str) -> bool:
        return name in self._gated


def _active_maestro(name: str = "dev", *, awaiting: bool = False) -> Maestro:
    return Maestro(
        name=name,
        model="sonnet",
        state=EntityState.RUNNING,
        last_activity_at=datetime.now(UTC),
        awaiting_decision=awaiting,
    )


def _idle_maestro(name: str = "dev", *, awaiting: bool = False) -> Maestro:
    # IDLE + no recent activity → lands in the idle bucket (_display_state).
    return Maestro(name=name, model="sonnet", state=EntityState.IDLE, awaiting_decision=awaiting)


def _lead(name: str, *, awaiting: bool = False) -> Entity:
    return Entity(name=name, role="lead", model="sonnet", awaiting_decision=awaiting)


class TestAwaitingYouFlag:
    @pytest.mark.asyncio
    async def test_own_awaiting_decision_sets_flag(self) -> None:
        dev = _active_maestro("dev", awaiting=True)
        view = await build_landing_view_model(process_manager=_FakePM([dev]))
        assert view["active"][0]["awaiting_you"] is True

    @pytest.mark.asyncio
    async def test_gate_parked_sets_flag_without_decision(self) -> None:
        dev = _active_maestro("dev", awaiting=False)
        view = await build_landing_view_model(process_manager=_FakePM([dev], gated={"dev"}))
        assert view["active"][0]["awaiting_you"] is True

    @pytest.mark.asyncio
    async def test_lead_under_maestro_rolls_up(self) -> None:
        dev = _active_maestro("dev", awaiting=False)
        backend = _lead("dev.backend", awaiting=True)
        view = await build_landing_view_model(process_manager=_FakePM([dev, backend]))
        # The maestro itself is not blocked, but a lead beneath it is.
        assert view["active"][0]["awaiting_you"] is True

    @pytest.mark.asyncio
    async def test_no_one_awaiting_is_false(self) -> None:
        dev = _active_maestro("dev", awaiting=False)
        backend = _lead("dev.backend", awaiting=False)
        view = await build_landing_view_model(process_manager=_FakePM([dev, backend]))
        assert view["active"][0]["awaiting_you"] is False

    @pytest.mark.asyncio
    async def test_unrelated_maestros_lead_does_not_leak(self) -> None:
        # ``ops.backend`` must not flip ``dev``'s flag — prefix match is scoped.
        dev = _active_maestro("dev", awaiting=False)
        ops_lead = _lead("ops.backend", awaiting=True)
        view = await build_landing_view_model(process_manager=_FakePM([dev, ops_lead]))
        assert view["active"][0]["awaiting_you"] is False


class TestOtterStubAndIdle:
    @pytest.mark.asyncio
    async def test_otter_stub_carries_flag_false(self) -> None:
        # Otter absent (cold start) → the hardcoded stub must still carry the key.
        dev = _active_maestro("dev")
        view = await build_landing_view_model(process_manager=_FakePM([dev]))
        assert view["otter"]["awaiting_you"] is False

    @pytest.mark.asyncio
    async def test_idle_row_reflects_awaiting(self) -> None:
        waiting = _idle_maestro("dev", awaiting=True)
        calm = _idle_maestro("ops", awaiting=False)
        view = await build_landing_view_model(process_manager=_FakePM([waiting, calm]))
        rows = {r["name"]: r for r in view["idle"]}
        assert rows["dev"]["awaiting_you"] is True
        assert rows["ops"]["awaiting_you"] is False


class TestBadgeRender:
    @pytest.mark.asyncio
    async def test_active_card_renders_badge_when_awaiting(self) -> None:
        dev = _active_maestro("dev", awaiting=True)
        view = await build_landing_view_model(process_manager=_FakePM([dev]))
        html = _render("_partials/active.html", view)
        assert 'class="awaits"' in html
        assert "is-awaiting" in html

    @pytest.mark.asyncio
    async def test_active_card_no_badge_when_calm(self) -> None:
        dev = _active_maestro("dev", awaiting=False)
        view = await build_landing_view_model(process_manager=_FakePM([dev]))
        html = _render("_partials/active.html", view)
        assert 'class="awaits"' not in html
        assert "is-awaiting" not in html

    @pytest.mark.asyncio
    async def test_idle_row_renders_badge_when_awaiting(self) -> None:
        waiting = _idle_maestro("dev", awaiting=True)
        view = await build_landing_view_model(process_manager=_FakePM([waiting]))
        html = _render("_partials/idle.html", view)
        assert 'class="awaits"' in html
        assert "is-awaiting" in html

    @pytest.mark.asyncio
    async def test_idle_row_no_badge_when_calm(self) -> None:
        calm = _idle_maestro("dev", awaiting=False)
        view = await build_landing_view_model(process_manager=_FakePM([calm]))
        html = _render("_partials/idle.html", view)
        assert 'class="awaits"' not in html
        assert "is-awaiting" not in html
