"""Tests for the A.2 Paper Ops landing page and its htmx fragments."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hive.models.entity import EntityState
from hive.models.maestro import Maestro
from hive.web.app import create_app
from hive.web.view_model import build_landing_view_model


def _bare_pm() -> MagicMock:
    """ProcessManager mock with no entities registered."""
    pm = MagicMock()
    pm.entities = {}
    return pm


def _client(**stores) -> TestClient:
    return TestClient(create_app(process_manager=_bare_pm(), **stores))


class TestLandingPage:
    def test_renders_with_empty_stores(self) -> None:
        client = _client()
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Hive" in resp.text
        assert "The hive is" in resp.text

    def test_static_css_served(self) -> None:
        client = _client()
        resp = client.get("/static/landing.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]


class TestFragmentEndpoints:
    def test_hero_fragment(self) -> None:
        resp = _client().get("/api/landing/hero")
        assert resp.status_code == 200
        assert "hero__title" in resp.text

    def test_vault_fragment(self) -> None:
        resp = _client().get("/api/landing/vault")
        assert resp.status_code == 200
        assert "vault-card" in resp.text

    def test_active_fragment_renders_empty(self) -> None:
        resp = _client().get("/api/landing/active")
        assert resp.status_code == 200
        assert "maestro-card" not in resp.text


class TestViewModelShape:
    @pytest.mark.asyncio
    async def test_empty_view_model_keys(self) -> None:
        view = await build_landing_view_model(process_manager=_bare_pm())

        for key in (
            "approvals_count",
            "health",
            "hero",
            "chat",
            "pa",
            "vault",
            "active",
            "idle",
            "dormant",
            "terminal",
        ):
            assert key in view, f"missing key {key}"

        assert view["approvals_count"] == 0
        assert view["hero"]["active_count"] == 0
        assert view["hero"]["mood"] == "asleep"
        assert view["pa"]["state"] == "dormant"
        assert view["vault"]["pending_approvals"] == 0
        assert view["vault"]["highest"] is None
        assert view["active"] == []
        assert view["idle"] == []
        assert view["dormant"] == []

    @pytest.mark.asyncio
    async def test_registered_maestro_shows_in_active(self) -> None:
        dev = Maestro(
            name="dev",
            model="sonnet",
            state=EntityState.RUNNING,
            last_activity_at=datetime.now(UTC),
        )
        pm = MagicMock()
        pm.entities = {"dev": dev}

        view = await build_landing_view_model(process_manager=pm)

        assert view["hero"]["active_count"] == 1
        assert view["hero"]["mood"] == "buzzing"
        assert len(view["active"]) == 1
        card = view["active"][0]
        assert card["name"] == "dev"
        assert card["state"] == "active"
        assert card["model"] == "sonnet"

    @pytest.mark.asyncio
    async def test_vault_pending_counted(self) -> None:
        vault = MagicMock()
        vault.pending = AsyncMock(
            return_value=[{"id": 1, "description": "transfer 50 USD", "requester": "dev"}]
        )
        vault.log = AsyncMock(return_value=[])

        view = await build_landing_view_model(process_manager=_bare_pm(), vault_store=vault)

        assert view["vault"]["pending_approvals"] == 1
        assert view["approvals_count"] == 1
        assert view["vault"]["highest"]["desc"] == "transfer 50 USD"

    @pytest.mark.asyncio
    async def test_dormant_lists_unregistered_personalities(self, tmp_path: Path) -> None:
        (tmp_path / "maestro-pa.md").write_text("# Entity: PA")
        (tmp_path / "maestro-dev.md").write_text("# Entity: Dev")
        (tmp_path / "_template.md").write_text("# template")

        dev = Maestro(name="dev", state=EntityState.RUNNING)
        pm = MagicMock()
        pm.entities = {"dev": dev}

        view = await build_landing_view_model(process_manager=pm, personalities_dir=tmp_path)

        names = {d["name"] for d in view["dormant"]}
        assert names == {"pa"}
