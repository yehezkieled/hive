"""Tests for the writable-policy resolver (Ticket 024, Slice B).

Pure functions: derive a WritablePolicy from an Entity's role + the
registry's owned roots, decide whether a target path is writable, and
emit the per-spawn settings.json PreToolUse hook payload.
"""

from __future__ import annotations

from hive.process.ownership_policy import (
    WritablePolicy,
    is_write_allowed,
    settings_payload,
    writable_policy,
)


def test_project_maestro_policy_allows_only_own_root() -> None:
    """A project maestro: allow_only=own_root, empty deny_under."""
    policy = writable_policy(
        is_pa=False,
        own_root="/home/hezki/projects/acme",
        owned_roots=["/home/hezki/projects/acme"],
    )
    assert policy == WritablePolicy(
        allow_only="/home/hezki/projects/acme",
        deny_under=(),
    )


def test_pa_policy_denies_sorted_owned_roots() -> None:
    """The PA: allow_only=None, deny_under=sorted(owned_roots)."""
    policy = writable_policy(
        is_pa=True,
        own_root=None,
        owned_roots=["/p/zeta", "/p/acme", "/p/mid"],
    )
    assert policy.allow_only is None
    assert policy.deny_under == ("/p/acme", "/p/mid", "/p/zeta")


def test_project_maestro_write_allowed_only_under_own_root() -> None:
    """allow_only set: under-root path True, outside path False."""
    policy = WritablePolicy(allow_only="/p/acme", deny_under=())
    assert is_write_allowed(policy, "/p/acme/src/main.py") is True
    assert is_write_allowed(policy, "/p/other/file.py") is False


def test_pa_write_denied_under_owned_root_allowed_elsewhere() -> None:
    """deny_under set: under an owned root False, elsewhere True."""
    policy = WritablePolicy(allow_only=None, deny_under=("/p/acme", "/p/beta"))
    assert is_write_allowed(policy, "/p/acme/src/main.py") is False
    assert is_write_allowed(policy, "/p/beta/x.py") is False
    assert is_write_allowed(policy, "/p/ownerless/y.py") is True
    assert is_write_allowed(policy, "/home/hezki/projects/hive/z.py") is True


def test_is_write_allowed_normalizes_and_blocks_prefix_escape() -> None:
    """`..` is collapsed and a sibling-prefix root can't masquerade.

    Two escape vectors the guard must close:
    - ``/p/acme/../other`` resolves to ``/p/other`` → outside an
      allow_only of ``/p/acme`` → denied.
    - ``/p/acme-evil`` shares the string prefix ``/p/acme`` but is a
      different directory → must NOT count as under ``/p/acme``.
    """
    allow = WritablePolicy(allow_only="/p/acme", deny_under=())
    assert is_write_allowed(allow, "/p/acme/../other/main.py") is False
    assert is_write_allowed(allow, "/p/acme-evil/main.py") is False
    assert is_write_allowed(allow, "/p/acme/sub/../ok.py") is True

    deny = WritablePolicy(allow_only=None, deny_under=("/p/acme",))
    # A sibling-prefix dir must stay writable for the PA.
    assert is_write_allowed(deny, "/p/acme-evil/main.py") is True
    # A traversal that lands back inside an owned root is still denied.
    assert is_write_allowed(deny, "/p/ownerless/../acme/main.py") is False


def test_settings_payload_matcher_and_command() -> None:
    """Matcher is exact; command carries the policy env + guard module."""
    policy = WritablePolicy(allow_only="/p/acme", deny_under=())
    payload = settings_payload(policy)

    hook_block = payload["hooks"]["PreToolUse"]
    assert isinstance(hook_block, list)
    entry = hook_block[0]
    assert entry["matcher"] == "Write|Edit|MultiEdit|NotebookEdit"

    command = entry["hooks"][0]["command"]
    assert entry["hooks"][0]["type"] == "command"
    # Project maestro → allow env, guard module both present.
    assert "HIVE_WRITE_ALLOW=/p/acme" in command
    assert "python3 -m hive.hooks.ownership_guard" in command


def test_settings_payload_pa_encodes_deny_roots_colon_joined() -> None:
    """PA policy encodes deny roots as a ':'-joined HIVE_WRITE_DENY."""
    policy = WritablePolicy(allow_only=None, deny_under=("/p/acme", "/p/beta"))
    entry = settings_payload(policy)["hooks"]["PreToolUse"][0]
    command = entry["hooks"][0]["command"]
    assert "HIVE_WRITE_DENY=/p/acme:/p/beta" in command
    assert "HIVE_WRITE_ALLOW" not in command


def test_settings_payload_honours_custom_guard_command() -> None:
    """A custom guard_command is the invoked module in the command."""
    policy = WritablePolicy(allow_only="/p/acme", deny_under=())
    payload = settings_payload(policy, guard_command="/abs/guard.py")
    entry = payload["hooks"]["PreToolUse"][0]
    command = entry["hooks"][0]["command"]
    assert command.endswith("/abs/guard.py")
    assert "HIVE_WRITE_ALLOW=/p/acme" in command
