"""Tests for Ticket 017 slice #119 — the aggregate Workflow run-card.

The dashboard surfaces one aggregate run-card per active Workflow run under the
owning Lead/Maestro, replacing the now-always-zero ``W`` worker count (Leaf
agents are not Entities, so the per-lead worker count is always 0 after Ticket
016). These tests drive ``build_landing_view_model`` with a *fake*
``process_manager`` carrying a *fake* progress store — they never import slice
#117's real ``ProgressStore`` (built in parallel).

Browser-level rendering needs the deployed dashboard and is out of scope here:
these are server-side view-model + Jinja template render unit tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.web.view_model import build_landing_view_model

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "hive" / "web" / "templates"


def _render_active(view: dict) -> str:
    """Render ``_partials/active.html`` exactly as the app endpoint does."""
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=True)
    return env.get_template("_partials/active.html").render(view=view)


class _FakeRun:
    """Stand-in for ``WorkflowProgress`` (frozen dataclass, contract #116).

    Only the fields the card consumes are needed; using a tiny fake keeps the
    test independent of the runtime module's import path.
    """

    def __init__(
        self,
        *,
        run_id: str,
        name: str,
        phase: str | None,
        agent_count: int,
        done_count: int,
        status: str,
        partials: list[str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.name = name
        self.phase = phase
        self.agent_count = agent_count
        self.done_count = done_count
        self.status = status
        self.partials = partials


class _FakeProgressStore:
    """Fake of #117's ProgressStore: ``runs_for(name) -> list[WorkflowProgress]``."""

    def __init__(self, runs_by_entity: dict[str, list[_FakeRun]]) -> None:
        self._runs = runs_by_entity

    def runs_for(self, entity_name: str) -> list[_FakeRun]:
        return list(self._runs.get(entity_name, []))


def _active_maestro(name: str = "dev") -> Maestro:
    return Maestro(
        name=name,
        model="sonnet",
        state=EntityState.RUNNING,
        last_activity_at=datetime.now(UTC),
    )


def _pm_with_store(maestro: Maestro, store: object) -> MagicMock:
    pm = MagicMock()
    pm.entities = {maestro.name: maestro}
    pm.progress_store = store
    # Ticket 039: view_model now reads this predicate; a bare MagicMock would
    # return a truthy mock and falsely flag every card as awaiting-you.
    pm.is_parked_at_gate = MagicMock(return_value=False)
    return pm


class TestRunsAttachedToCard:
    @pytest.mark.asyncio
    async def test_active_run_attached_with_fields(self) -> None:
        dev = _active_maestro("dev")
        store = _FakeProgressStore(
            {
                "dev": [
                    _FakeRun(
                        run_id="wf_abc",
                        name="research-consolidate",
                        phase="fan-out",
                        agent_count=4,
                        done_count=2,
                        status="running",
                    )
                ]
            }
        )
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, store))

        card = view["active"][0]
        assert "runs" in card
        assert len(card["runs"]) == 1
        run = card["runs"][0]
        assert run["name"] == "research-consolidate"
        assert run["phase"] == "fan-out"
        assert run["status"] == "running"
        assert run["done_count"] == 2
        assert run["agent_count"] == 4


class TestGracefulWithoutStore:
    @pytest.mark.asyncio
    async def test_progress_store_none_yields_empty_runs(self) -> None:
        dev = _active_maestro("dev")
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, None))

        card = view["active"][0]
        assert card["runs"] == []
        assert card["active_runs"] == 0

    @pytest.mark.asyncio
    async def test_progress_store_attribute_absent_yields_empty_runs(self) -> None:
        dev = _active_maestro("dev")
        # A bare object with no ``progress_store`` attribute at all.

        class _NoStorePM:
            def __init__(self) -> None:
                self.entities = {"dev": dev}

        view = await build_landing_view_model(process_manager=_NoStorePM())

        card = view["active"][0]
        assert card["runs"] == []
        assert card["active_runs"] == 0


class TestStaleWorkerCountReplaced:
    @pytest.mark.asyncio
    async def test_card_has_no_worker_count_and_uses_active_runs(self) -> None:
        dev = _active_maestro("dev")
        store = _FakeProgressStore(
            {
                "dev": [
                    _FakeRun(
                        run_id="wf_a",
                        name="run-a",
                        phase=None,
                        agent_count=3,
                        done_count=1,
                        status="running",
                    ),
                    _FakeRun(
                        run_id="wf_b",
                        name="run-b",
                        phase=None,
                        agent_count=2,
                        done_count=0,
                        status="running",
                    ),
                ]
            }
        )
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, store))

        card = view["active"][0]
        # The always-zero ``workers`` count is gone; replaced by an active-run
        # indicator that reflects the number of live runs.
        assert "workers" not in card
        assert card["active_runs"] == 2


class TestTemplateRendersRunCard:
    @pytest.mark.asyncio
    async def test_active_partial_renders_aggregate_run_card(self) -> None:
        dev = _active_maestro("dev")
        store = _FakeProgressStore(
            {
                "dev": [
                    _FakeRun(
                        run_id="wf_abc",
                        name="research-consolidate",
                        phase="fan-out",
                        agent_count=4,
                        done_count=2,
                        status="running",
                    )
                ]
            }
        )
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, store))
        html = _render_active(view)

        # One aggregate run-card per run — its container class appears once.
        assert html.count("run-card") >= 1
        # Name, phase, and the N/M count line are rendered on the card.
        assert "research-consolidate" in html
        assert "fan-out" in html
        assert "2" in html and "4" in html
        # The stale ``…W`` worker count must not be rendered anymore.
        assert "W</span>" not in html

    @pytest.mark.asyncio
    async def test_no_run_card_when_no_active_runs(self) -> None:
        dev = _active_maestro("dev")
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, None))
        html = _render_active(view)

        # No active runs → no run-card markup at all (still renders the card).
        assert "run-card" not in html
        assert "maestro-card" in html

    @pytest.mark.asyncio
    async def test_one_card_per_run(self) -> None:
        dev = _active_maestro("dev")
        store = _FakeProgressStore(
            {
                "dev": [
                    _FakeRun(
                        run_id="wf_a",
                        name="run-a",
                        phase=None,
                        agent_count=3,
                        done_count=1,
                        status="running",
                    ),
                    _FakeRun(
                        run_id="wf_b",
                        name="run-b",
                        phase=None,
                        agent_count=2,
                        done_count=0,
                        status="running",
                    ),
                ]
            }
        )
        view = await build_landing_view_model(process_manager=_pm_with_store(dev, store))
        html = _render_active(view)

        # Exactly one card per run — both names present, two run-card blocks.
        assert "run-a" in html
        assert "run-b" in html
        # The outer container is ``class="run-card"`` (BEM sub-elements are
        # ``run-card__*``); match the container precisely, one per run.
        assert html.count('class="run-card"') == 2
