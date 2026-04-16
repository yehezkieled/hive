"""Tests for Vault entity and kill protection."""

from hive.models.vault import Vault


class TestVaultEntity:
    """Test Vault entity defaults and CLI args."""

    def test_vault_default_role(self) -> None:
        v = Vault(name="vault")
        assert v.role == "vault"

    def test_vault_disallows_dangerous_tools(self) -> None:
        v = Vault(name="vault")
        assert "Bash" in v.disallowed_tools
        assert "Write" in v.disallowed_tools
        assert "Edit" in v.disallowed_tools

    def test_vault_builds_cli_args_without_bash(self) -> None:
        v = Vault(name="vault")
        args = v.build_cli_args()
        assert "--disallowedTools" in args
        idx = args.index("--disallowedTools")
        tool_args = args[idx + 1:]
        assert "Bash" in tool_args
