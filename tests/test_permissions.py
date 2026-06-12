"""Tests for inter-agent messaging and lifecycle permission checks."""

from hive.bus.permissions import can_kill, can_message, can_spawn_team, can_spawn_worker


class TestPermissions:
    """Test the can_message permission matrix."""

    def test_maestro_can_message_own_lead(self) -> None:
        assert can_message("maestro", "dev", "lead", "dev.backend") is True

    def test_maestro_cannot_message_other_org_lead(self) -> None:
        assert can_message("maestro", "dev", "lead", "ops.backend") is False

    def test_lead_can_message_own_workers(self) -> None:
        assert can_message("lead", "dev.backend", "worker", "dev.backend.w1") is True

    def test_lead_can_message_own_maestro(self) -> None:
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_lead_cannot_message_other_team_worker(self) -> None:
        assert can_message("lead", "dev.backend", "worker", "dev.frontend.w1") is False

    def test_worker_can_message_own_lead(self) -> None:
        assert can_message("worker", "dev.backend.w1", "lead", "dev.backend") is True

    def test_worker_cannot_message_other_lead(self) -> None:
        assert can_message("worker", "dev.backend.w1", "lead", "dev.frontend") is False

    def test_worker_cannot_message_cross_maestro_worker(self) -> None:
        # Sprint 22: same-maestro worker peer messaging is now allowed.
        # Cross-maestro is still denied (must escalate via the chain).
        assert can_message("worker", "dev.backend.w1", "worker", "ops.deploy.w1") is False


class TestSpawnTeamPermissions:
    """Test can_spawn_team — only maestros."""

    def test_maestro_can_spawn_team(self) -> None:
        assert can_spawn_team("maestro", "dev") is True

    def test_lead_cannot_spawn_team(self) -> None:
        assert can_spawn_team("lead", "dev.backend") is False

    def test_worker_cannot_spawn_team(self) -> None:
        assert can_spawn_team("worker", "dev.backend.w1") is False


class TestSpawnWorkerPermissions:
    """Test can_spawn_worker — Worker creation is retired for ALL actors (ADR 0013)."""

    def test_maestro_cannot_spawn_worker_under_own_lead(self) -> None:
        assert can_spawn_worker("maestro", "dev", "dev.backend") is False

    def test_maestro_cannot_spawn_worker_under_other_org_lead(self) -> None:
        assert can_spawn_worker("maestro", "dev", "ops.backend") is False

    def test_lead_cannot_spawn_worker_under_self(self) -> None:
        # Even under itself — the lead arm is retired with the rest (ADR 0013).
        assert can_spawn_worker("lead", "dev.backend", "dev.backend") is False

    def test_lead_cannot_spawn_worker_under_other_lead(self) -> None:
        assert can_spawn_worker("lead", "dev.backend", "dev.frontend") is False

    def test_worker_cannot_spawn_worker(self) -> None:
        assert can_spawn_worker("worker", "dev.backend.w1", "dev.backend") is False


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

    def test_lead_can_kill_direct_worker(self) -> None:
        assert can_kill("lead", "dev.backend", "dev.backend.w1", default_maestro="other") is True

    def test_lead_cannot_kill_other_team_worker(self) -> None:
        assert can_kill("lead", "dev.backend", "dev.frontend.w1", default_maestro="other") is False

    def test_lead_cannot_kill_across_team_boundary(self) -> None:
        # Hypothetical nested grandchild — lead can only kill direct workers
        assert (
            can_kill("lead", "dev.backend", "dev.backend.sub.w1", default_maestro="other") is False
        )

    def test_lead_cannot_kill_parent_maestro(self) -> None:
        assert can_kill("lead", "dev.backend", "dev", default_maestro="other") is False

    def test_worker_cannot_kill(self) -> None:
        assert (
            can_kill("worker", "dev.backend.w1", "dev.backend.w2", default_maestro="other") is False
        )
