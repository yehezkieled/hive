"""Per-spawn ``--settings`` file every entity gets (Remote Control opt-out).

Ticket 067: Claude Code auto-connects a flag-less interactive session to
Remote Control whenever the org/rollout default is on. Hive drives its
sessions over the PTY itself, so every spawn — maestro *and* lead — must
carry an explicit ``remoteControlAtStartup: false`` in its ``--settings``
file, merged with the Ticket 024 ownership fence when one applies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hive.models.maestro import Maestro
from hive.models.team_lead import TeamLead
from hive.process import lifecycle_manager
from hive.process.lifecycle_manager import (
    LifecycleManager,
    _spawn_settings_payload,
    _write_spawn_settings,
)
from hive.process.ownership_policy import WritablePolicy, settings_payload

# --- _spawn_settings_payload (pure) -----------------------------------------


def test_payload_opts_out_of_remote_control_without_a_fence() -> None:
    assert _spawn_settings_payload(None) == {"remoteControlAtStartup": False}


def test_payload_keeps_ownership_hook_alongside_opt_out() -> None:
    fence = settings_payload(WritablePolicy(allow_only="/p/acme", deny_under=()))

    payload = _spawn_settings_payload(fence)

    assert payload["remoteControlAtStartup"] is False
    assert payload["hooks"] == fence["hooks"]


def test_write_goes_under_the_isolated_spawn_settings_dir(tmp_path: Path) -> None:
    # The autouse conftest fixture points _spawn_settings_dir at a per-test
    # tmp dir, so no test can touch the live service's spawn-settings files.
    path = _write_spawn_settings("otter", {"remoteControlAtStartup": False})

    assert path == lifecycle_manager._spawn_settings_dir() / "otter.settings.json"
    assert str(tmp_path) in str(path)
    assert json.loads(path.read_text()) == {"remoteControlAtStartup": False}


# --- _get_or_create_adapter wiring ------------------------------------------


class _FakeAdapter:
    """Stands in for ClaudeAdapter: records its config, never spawns claude."""

    def __init__(self, config, *, cwd=None, **_kwargs) -> None:  # noqa: ANN001
        self.config = config
        self.cwd = cwd

    async def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True


def _lifecycle() -> LifecycleManager:
    mgr = SimpleNamespace(
        _adapters={},
        _state_lock=asyncio.Lock(),
        worktree_mgr=None,
        project_store=None,
        gate_coordinator=None,
        _on_gate_state=None,
    )
    return LifecycleManager(mgr)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "entity",
    [
        Maestro(name="otter"),
        TeamLead(name="dev.backend", team_name="backend", maestro_name="dev"),
    ],
    ids=["maestro", "lead"],
)
async def test_every_spawn_passes_a_settings_file_that_disables_remote_control(
    monkeypatch: pytest.MonkeyPatch, entity: Maestro | TeamLead
) -> None:
    monkeypatch.setattr(lifecycle_manager, "ClaudeAdapter", _FakeAdapter)

    adapter = await _lifecycle()._get_or_create_adapter(entity)

    settings_path = adapter.config.settings_path
    assert settings_path is not None and settings_path.exists()
    assert json.loads(settings_path.read_text())["remoteControlAtStartup"] is False
