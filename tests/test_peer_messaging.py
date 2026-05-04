"""Permission matrix and routing tests for tier-aware peer messaging."""

from __future__ import annotations

from hive.bus.permissions import can_message


# ---- can_message peer rules ----


class TestPeerMessagingPermissions:
    def test_worker_to_worker_same_team_allowed(self) -> None:
        # dev.backend.w1 -> dev.backend.w2 (same lead dev.backend)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w2") is True

    def test_worker_to_worker_cross_team_same_maestro_allowed(self) -> None:
        # dev.backend.w1 -> dev.payments.w1 (different leads, same maestro dev)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.payments.w1") is True

    def test_worker_to_worker_cross_maestro_denied(self) -> None:
        # dev.backend.w1 -> ops.deploy.w1 (different maestros)
        assert can_message("worker", "dev.backend.w1", "worker", "ops.deploy.w1") is False

    def test_lead_to_lead_same_maestro_allowed(self) -> None:
        # dev.backend -> dev.payments (same maestro dev)
        assert can_message("lead", "dev.backend", "lead", "dev.payments") is True

    def test_lead_to_lead_cross_maestro_allowed(self) -> None:
        # dev.backend -> ops.deploy (different maestros — allowed but with CC)
        assert can_message("lead", "dev.backend", "lead", "ops.deploy") is True

    def test_maestro_to_maestro_allowed(self) -> None:
        # dev -> ops (top-tier peers)
        assert can_message("maestro", "dev", "maestro", "ops") is True

    def test_existing_parent_child_still_allowed(self) -> None:
        # Regression: existing rules must keep working.
        assert can_message("worker", "dev.backend.w1", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "worker", "dev.backend.w1") is True
        assert can_message("maestro", "dev", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_worker_to_worker_self_denied(self) -> None:
        # Self-message is disallowed.
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w1") is False
