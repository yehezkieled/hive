"""Native /advisor enablement (Ticket 013) + the advisor post-mortem guard.

The native ``--advisor`` flag exists in no shipped CC build the fleet runs, so
advisor is now **default-off / opt-in** and the spawn flag is **capability
guarded** (see the Ticket 013 post-mortem). Covers:
- per-Entity advisor resolution (default off; explicit field opts in),
- the `**Advisor**:` role-file field,
- the `--advisor` flag reaching the spawn args only when set AND supported.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hive.models.entity import (
    Entity,
    parse_personality,
    resolve_advisor,
)
from hive.runtime.claude_adapter import ClaudeAdapter, ClaudeAdapterConfig


class TestResolveAdvisor:
    """Default off / opt-in: no role auto-resolves an advisor (the flag is
    unsupported on the fleet's CC; auto-enabling it crashed lead spawns)."""

    def test_no_field_defaults_off_for_every_role(self) -> None:
        for model in ("sonnet", "haiku", "opus", "claude-sonnet-4-6"):
            for role in (None, "maestro", "lead", "worker"):
                assert resolve_advisor(model, None, role=role) is None

    def test_explicit_off_stays_off(self) -> None:
        assert resolve_advisor("sonnet", "off") is None

    def test_explicit_model_opts_in(self) -> None:
        # explicit field is honoured; the adapter still guards the actual flag
        assert resolve_advisor("opus", "opus") == "opus"
        assert resolve_advisor("sonnet", "sonnet") == "sonnet"

    def test_field_is_case_insensitive(self) -> None:
        assert resolve_advisor("sonnet", "OFF") is None
        assert resolve_advisor("opus", "Opus") == "opus"

    def test_explicit_field_opts_in_for_any_role(self) -> None:
        assert resolve_advisor("sonnet", "opus", role="worker") == "opus"
        assert resolve_advisor("sonnet", "opus", role="lead") == "opus"


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
    """`--advisor <model>` reaches the spawn args only when set AND the binary
    actually supports the flag (capability guard — post-mortem fix)."""

    def _adapter(self, advisor: str | None) -> ClaudeAdapter:
        cfg = ClaudeAdapterConfig(model="sonnet", name="x", role="lead", advisor=advisor)
        return ClaudeAdapter(cfg)

    def test_advisor_arg_present_when_set_and_supported(self) -> None:
        with patch("hive.runtime.claude_adapter._claude_supports_advisor", return_value=True):
            args = self._adapter("opus")._build_pty_extra_args()
        assert "--advisor" in args
        assert args[args.index("--advisor") + 1] == "opus"

    def test_advisor_arg_skipped_when_unsupported(self) -> None:
        # The crash that killed every lead: flag set but binary lacks it.
        with patch("hive.runtime.claude_adapter._claude_supports_advisor", return_value=False):
            args = self._adapter("opus")._build_pty_extra_args()
        assert "--advisor" not in args

    def test_advisor_arg_absent_when_off(self) -> None:
        with patch("hive.runtime.claude_adapter._claude_supports_advisor", return_value=True):
            args = self._adapter(None)._build_pty_extra_args()
        assert "--advisor" not in args
