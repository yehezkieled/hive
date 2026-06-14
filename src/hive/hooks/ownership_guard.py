"""PreToolUse ownership guard (Ticket 024, ADR 0017).

Runs as ``python3 -m hive.hooks.ownership_guard``. CC pipes the
tool-call JSON on stdin before a ``Write|Edit|MultiEdit|NotebookEdit``;
the guard reads the target ``file_path`` and the policy from the env
(``HIVE_WRITE_ALLOW`` or ``HIVE_WRITE_DENY``), then blocks (exit 2) or
allows (exit 0). It is a guardrail against accidental cross-project
writes, not a subprocess-proof wall.

Fails OPEN: a missing ``file_path`` or an empty policy allows the write,
so a malformed call never wedges an Entity.
"""

from __future__ import annotations

import json
import os
import sys

from hive.process.ownership_policy import WritablePolicy, is_write_allowed


def decide(
    tool_input: dict,
    *,
    allow_root: str | None,
    deny_roots: list[str],
) -> tuple[int, str]:
    target = tool_input.get("file_path")
    # Fail OPEN: no target, or no policy at all → nothing to enforce.
    if not target:
        return (0, "")
    if allow_root is None and not deny_roots:
        return (0, "")

    policy = WritablePolicy(allow_only=allow_root, deny_under=tuple(deny_roots))
    if is_write_allowed(policy, target):
        return (0, "")

    if allow_root is not None:
        cause = f"outside the writable root {allow_root}"
    else:
        owned = ", ".join(deny_roots)
        cause = f"under an owned project root ({owned})"
    reason = f"ownership guard: write to {target} denied — {cause}"
    return (2, reason)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Fail OPEN on an unparseable payload — never wedge an Entity.
        return 0

    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    allow_root = os.environ.get("HIVE_WRITE_ALLOW") or None
    deny_raw = os.environ.get("HIVE_WRITE_DENY", "")
    deny_roots = [part for part in deny_raw.split(":") if part]

    code, reason = decide(tool_input, allow_root=allow_root, deny_roots=deny_roots)
    if code != 0:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
