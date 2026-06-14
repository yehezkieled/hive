"""Ownership-fence integration at the spawn seam (Ticket 024, ADR 0017).

Covers the wiring that turns the registry + guard modules into a per-spawn
fence: ``_maestro_fence`` (which entities get a policy), the async
``_ownership_spawn_overrides`` (settings file + project-home cwd), and the
``--settings`` flag on the adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.process.lifecycle_manager import LifecycleManager, _maestro_fence
from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig

PA = "otter"  # config.DEFAULT_MAESTRO


class FakeProjectStore:
    def __init__(self, *, owned: list[str], by_maestro: dict[str, object]) -> None:
        self._owned = owned
        self._by_maestro = by_maestro

    async def owned_roots(self) -> list[str]:
        return list(self._owned)

    async def for_maestro(self, maestro: str):  # noqa: ANN201
        return self._by_maestro.get(maestro)


def _lifecycle(project_store: object | None) -> LifecycleManager:
    return LifecycleManager(SimpleNamespace(project_store=project_store))  # type: ignore[arg-type]


# --- _maestro_fence (pure) ---------------------------------------------------


def test_fence_project_maestro_allows_only_own_root() -> None:
    policy = _maestro_fence(
        Maestro(name="acme-lead"), is_pa=False, own_root="/p/acme", owned_roots=["/p/acme"]
    )
    assert policy is not None
    assert policy.allow_only == "/p/acme"
    assert policy.deny_under == ()


def test_fence_pa_denies_every_owned_root() -> None:
    policy = _maestro_fence(
        Maestro(name=PA), is_pa=True, own_root=None, owned_roots=["/p/acme", "/p/foo"]
    )
    assert policy is not None
    assert policy.allow_only is None
    assert policy.deny_under == ("/p/acme", "/p/foo")


def test_fence_none_for_non_maestro() -> None:
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    assert _maestro_fence(lead, is_pa=False, own_root="/p/x", owned_roots=[]) is None


def test_fence_none_for_pa_with_no_owned_projects() -> None:
    assert _maestro_fence(Maestro(name=PA), is_pa=True, own_root=None, owned_roots=[]) is None


def test_fence_none_for_project_maestro_with_no_project() -> None:
    assert (
        _maestro_fence(Maestro(name="acme-lead"), is_pa=False, own_root=None, owned_roots=[])
        is None
    )


# --- _ownership_spawn_overrides (async) -------------------------------------


async def test_overrides_project_maestro_writes_allow_settings_and_homes_cwd() -> None:
    project = SimpleNamespace(root_path=Path("/p/acme"))
    store = FakeProjectStore(owned=["/p/acme"], by_maestro={"acme-lead": project})
    lifecycle = _lifecycle(store)

    settings_path, cwd = await lifecycle._ownership_spawn_overrides(Maestro(name="acme-lead"))

    assert cwd == Path("/p/acme")
    assert settings_path is not None and settings_path.exists()
    payload = json.loads(settings_path.read_text())
    hook = payload["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Write|Edit|MultiEdit|NotebookEdit"
    command = hook["hooks"][0]["command"]
    assert "HIVE_WRITE_ALLOW=/p/acme" in command
    assert "hive.hooks.ownership_guard" in command


async def test_overrides_pa_writes_deny_settings_no_cwd() -> None:
    store = FakeProjectStore(owned=["/p/acme", "/p/foo"], by_maestro={})
    lifecycle = _lifecycle(store)

    settings_path, cwd = await lifecycle._ownership_spawn_overrides(Maestro(name=PA))

    assert cwd is None
    assert settings_path is not None
    command = json.loads(settings_path.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "HIVE_WRITE_DENY=/p/acme:/p/foo" in command


async def test_overrides_none_for_team_lead() -> None:
    store = FakeProjectStore(owned=["/p/acme"], by_maestro={})
    lifecycle = _lifecycle(store)
    lead = TeamLead(name="dev.backend", team_name="backend", maestro_name="dev")
    assert await lifecycle._ownership_spawn_overrides(lead) == (None, None)


async def test_overrides_none_without_project_store() -> None:
    lifecycle = _lifecycle(None)
    assert await lifecycle._ownership_spawn_overrides(Maestro(name="acme-lead")) == (None, None)


# --- adapter --settings flag -------------------------------------------------


def test_build_pty_extra_args_includes_settings_when_set(tmp_path: Path) -> None:
    settings = tmp_path / "x.settings.json"
    settings.write_text("{}")
    adapter = ClaudeAdapter(ClaudeAdapterConfig(name="acme-lead", settings_path=settings))
    args = adapter._build_pty_extra_args()
    assert "--settings" in args
    assert str(settings) in args


def test_build_pty_extra_args_omits_settings_when_none() -> None:
    adapter = ClaudeAdapter(ClaudeAdapterConfig(name="otter"))
    assert "--settings" not in adapter._build_pty_extra_args()
