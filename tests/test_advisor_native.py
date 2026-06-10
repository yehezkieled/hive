"""Native /advisor enablement (Ticket 013).

Covers the new behaviour that replaces the retired custom advisor:
- per-Entity advisor resolution with a model-aware default,
- the `**Advisor**:` role-file field,
- the `--advisor` flag on the live PTY spawn path.
"""

from __future__ import annotations

from pathlib import Path

from hive.models.entity import (
    Entity,
    parse_personality,
    resolve_advisor,
)
from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig


class TestResolveAdvisor:
    """Model-aware default: an advisor only helps when stronger than the main."""

    def test_sub_opus_main_defaults_to_opus(self) -> None:
        assert resolve_advisor("sonnet", None) == "opus"
        assert resolve_advisor("haiku", None) == "opus"
        assert resolve_advisor("claude-sonnet-4-6", None) == "opus"

    def test_opus_main_defaults_to_off(self) -> None:
        assert resolve_advisor("opus", None) is None
        assert resolve_advisor("claude-opus-4-8", None) is None

    def test_explicit_off_overrides_default(self) -> None:
        # even a sub-Opus main is silenced when the field says off
        assert resolve_advisor("sonnet", "off") is None

    def test_explicit_model_overrides_default(self) -> None:
        # force an advisor on an Opus main for high-stakes work
        assert resolve_advisor("opus", "opus") == "opus"
        assert resolve_advisor("sonnet", "sonnet") == "sonnet"

    def test_field_is_case_insensitive(self) -> None:
        assert resolve_advisor("sonnet", "OFF") is None
        assert resolve_advisor("opus", "Opus") == "opus"

    def test_worker_role_defaults_off(self) -> None:
        # short leaf tasks: a Sonnet worker would otherwise get an Opus advisor
        assert resolve_advisor("sonnet", None, role="worker") is None

    def test_worker_explicit_field_overrides_role_default(self) -> None:
        assert resolve_advisor("sonnet", "opus", role="worker") == "opus"

    def test_lead_sonnet_defaults_on(self) -> None:
        assert resolve_advisor("sonnet", None, role="lead") == "opus"

    def test_maestro_opus_defaults_off(self) -> None:
        assert resolve_advisor("opus", None, role="maestro") is None


class TestAdvisorPersonalityField:
    """The `**Advisor**:` field is parsed and applied like `**Model**:`."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "role.md"
        p.write_text(body)
        return p

    def test_parses_advisor_field(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "## Identity\n- **Name**: Lead\n- **Role**: lead\n"
            "- **Model**: sonnet\n- **Advisor**: opus\n",
        )
        config = parse_personality(path)
        assert config.advisor == "opus"

    def test_absent_field_is_empty(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "## Identity\n- **Name**: Lead\n- **Role**: lead\n- **Model**: sonnet\n",
        )
        config = parse_personality(path)
        assert config.advisor == ""

    def test_load_personality_applies_advisor(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "## Identity\n- **Name**: W\n- **Role**: worker\n"
            "- **Model**: sonnet\n- **Advisor**: off\n",
        )
        e = Entity(name="W", role="worker", personality_path=path)
        e.load_personality()
        assert e.advisor == "off"


class TestAdvisorSpawnArg:
    """`--advisor <model>` reaches the live PTY spawn args iff resolved on."""

    def _adapter(self, advisor: str | None) -> ClaudeAdapter:
        cfg = ClaudeAdapterConfig(model="sonnet", name="x", role="lead", advisor=advisor)
        return ClaudeAdapter(cfg)

    def test_advisor_arg_present_when_set(self) -> None:
        args = self._adapter("opus")._build_pty_extra_args()
        assert "--advisor" in args
        assert args[args.index("--advisor") + 1] == "opus"

    def test_advisor_arg_absent_when_off(self) -> None:
        args = self._adapter(None)._build_pty_extra_args()
        assert "--advisor" not in args
