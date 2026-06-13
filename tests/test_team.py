"""Tests for the Team model and hierarchical entity naming."""

from hive.models.maestro import Maestro
from hive.models.team import Team
from hive.models.team_lead import TeamLead


class TestTeamModel:
    """Test Team dataclass."""

    def test_create_team(self) -> None:
        team = Team(name="backend", maestro="dev")
        assert team.name == "backend"
        assert team.maestro == "dev"
        assert team.lead is None
        assert team.workers == []

    def test_team_with_lead_and_workers(self) -> None:
        team = Team(
            name="backend",
            maestro="dev",
            lead="dev.backend",
            workers=["dev.backend.w1", "dev.backend.w2"],
        )
        assert team.lead == "dev.backend"
        assert len(team.workers) == 2


class TestMaestroTeamManagement:
    """Test Maestro's team management methods."""

    def test_create_team(self) -> None:
        m = Maestro(name="dev")
        team = m.create_team("backend")
        assert team.name == "backend"
        assert team.maestro == "dev"
        assert "backend" in m.teams

    def test_create_duplicate_team_raises(self) -> None:
        m = Maestro(name="dev")
        m.create_team("backend")
        import pytest

        with pytest.raises(ValueError, match="already exists"):
            m.create_team("backend")

    def test_get_team(self) -> None:
        m = Maestro(name="dev")
        m.create_team("backend")
        team = m.get_team("backend")
        assert team is not None
        assert team.name == "backend"

    def test_get_missing_team_returns_none(self) -> None:
        m = Maestro(name="dev")
        assert m.get_team("nope") is None

    def test_remove_team(self) -> None:
        m = Maestro(name="dev")
        m.create_team("backend")
        m.remove_team("backend")
        assert m.get_team("backend") is None
        assert "backend" not in m.teams


class TestTeamLeadFields:
    """Test TeamLead hierarchy fields."""

    def test_lead_has_team_and_maestro(self) -> None:
        lead = TeamLead(
            name="dev.backend",
            team_name="backend",
            maestro_name="dev",
        )
        assert lead.role == "lead"
        assert lead.team_name == "backend"
        assert lead.maestro_name == "dev"
        assert lead.workers == []
        assert lead.max_workers == 2

    def test_lead_default_max_workers(self) -> None:
        lead = TeamLead(name="dev.ops")
        assert lead.max_workers == 2
