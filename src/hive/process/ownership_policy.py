"""Writable-policy resolver for the ownership guard (Ticket 024)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _is_under(child: str, parent: str) -> bool:
    """True iff ``child`` is ``parent`` itself or nested under it.

    Both paths are normalized first (collapsing ``..`` and ``.``) so a
    relative or traversal-laden target can't escape the root. The
    trailing-sep guard stops a sibling prefix like ``/p/acme-evil`` from
    matching the root ``/p/acme``.
    """
    parent_norm = os.path.abspath(parent)
    child_norm = os.path.abspath(child)
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm.rstrip(os.sep) + os.sep)


@dataclass(frozen=True)
class WritablePolicy:
    allow_only: str | None
    deny_under: tuple[str, ...]


def writable_policy(
    *,
    is_pa: bool,
    own_root: str | None,
    owned_roots: list[str],
) -> WritablePolicy:
    if is_pa:
        return WritablePolicy(allow_only=None, deny_under=tuple(sorted(owned_roots)))
    return WritablePolicy(allow_only=own_root, deny_under=())


def is_write_allowed(policy: WritablePolicy, target_path: str) -> bool:
    if policy.allow_only is not None:
        return _is_under(target_path, policy.allow_only)
    for root in policy.deny_under:
        if _is_under(target_path, root):
            return False
    return True


def settings_payload(
    policy: WritablePolicy,
    *,
    guard_command: str = "python3 -m hive.hooks.ownership_guard",
) -> dict:
    if policy.allow_only is not None:
        env_prefix = f"HIVE_WRITE_ALLOW={policy.allow_only}"
    else:
        env_prefix = f"HIVE_WRITE_DENY={':'.join(policy.deny_under)}"
    command = f"{env_prefix} {guard_command}"
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit|MultiEdit|NotebookEdit",
                    "hooks": [{"type": "command", "command": command}],
                }
            ]
        }
    }
