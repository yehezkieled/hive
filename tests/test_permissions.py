"""Tests for inter-agent messaging permission checks."""

from hive.bus.permissions import can_message


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

    def test_worker_cannot_message_other_worker(self) -> None:
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w2") is False
