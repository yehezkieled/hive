"""Tests for inter-agent messaging and lifecycle permission checks."""

from hive.bus.actions import parse_actions
from hive.bus.permissions import can_kill, can_message, can_spawn_team


class TestPermissions:
    """Test the can_message permission matrix."""

    def test_maestro_can_message_own_lead(self) -> None:
        assert can_message("maestro", "dev", "lead", "dev.backend") is True

    def test_maestro_cannot_message_other_org_lead(self) -> None:
        assert can_message("maestro", "dev", "lead", "ops.backend") is False

    def test_lead_can_message_own_maestro(self) -> None:
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_lead_can_message_peer_lead(self) -> None:
        # Sprint 22: leads can message any other lead (cross-maestro CC'd).
        assert can_message("lead", "dev.backend", "lead", "dev.frontend") is True


class TestSpawnTeamPermissions:
    """Test can_spawn_team — only maestros."""

    def test_maestro_can_spawn_team(self) -> None:
        assert can_spawn_team("maestro", "dev") is True

    def test_lead_cannot_spawn_team(self) -> None:
        assert can_spawn_team("lead", "dev.backend") is False

    def test_non_maestro_role_cannot_spawn_team(self) -> None:
        # Any non-maestro role is denied; the team scope is enforced by
        # construction so only the role check matters here.
        assert can_spawn_team("vault", "dev.backend.v1") is False


class TestSpawnWorkerRetired:
    """Worker creation is retired (ADR 0013 / Ticket 018).

    ``spawn_worker`` is no longer a recognised action type — it parses as a
    generic *unknown* action: no Action is produced and the sender is told
    so via an error string. This is the drainage proof that no leaf path can
    still create a persistent Worker.
    """

    def test_spawn_worker_parses_as_unknown_action(self) -> None:
        block = '<hive_actions>[{"type":"spawn_worker","task":"do x"}]</hive_actions>'
        _clean_text, actions, errors = parse_actions(block)
        # No Action is produced from a spawn_worker request.
        assert actions == []
        # The sender gets a generic "unknown action type" rejection.
        assert any("Unknown action type 'spawn_worker'" in err for err in errors)


class TestKillPermissions:
    """Test can_kill — never default maestro, never self, scope-restricted."""

    def test_default_maestro_never_killable(self) -> None:
        # Even by another maestro
        assert can_kill("maestro", "ops", "dev", default_maestro="dev") is False

    def test_self_never_killable(self) -> None:
        assert can_kill("maestro", "dev", "dev", default_maestro="other") is False
        assert can_kill("lead", "dev.backend", "dev.backend", default_maestro="other") is False

    def test_maestro_can_kill_own_org_member(self) -> None:
        assert can_kill("maestro", "dev", "dev.backend", default_maestro="other") is True
        assert can_kill("maestro", "dev", "dev.backend.w1", default_maestro="other") is True

    def test_maestro_cannot_kill_other_org(self) -> None:
        assert can_kill("maestro", "dev", "ops.backend", default_maestro="other") is False

    def test_lead_can_kill_direct_child(self) -> None:
        assert can_kill("lead", "dev.backend", "dev.backend.w1", default_maestro="other") is True

    def test_lead_cannot_kill_other_team_child(self) -> None:
        assert can_kill("lead", "dev.backend", "dev.frontend.w1", default_maestro="other") is False

    def test_lead_cannot_kill_across_team_boundary(self) -> None:
        # Nested grandchild — a lead can only kill direct children
        assert (
            can_kill("lead", "dev.backend", "dev.backend.sub.w1", default_maestro="other") is False
        )

    def test_lead_cannot_kill_parent_maestro(self) -> None:
        assert can_kill("lead", "dev.backend", "dev", default_maestro="other") is False

    def test_other_role_cannot_kill(self) -> None:
        assert (
            can_kill("vault", "dev.backend.v1", "dev.backend.v2", default_maestro="other") is False
        )
