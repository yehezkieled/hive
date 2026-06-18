"""Entity / team name validation (Ticket 032).

Names flow raw into filesystem paths (worktree dirs), git refs
(``hive/<name>``) and the dotted ``maestro.team`` address. An unvalidated
name with a path/ref-hostile character breaks worktree or branch creation —
or escapes the intended directory. This module is the single chokepoint that
rejects such names *before* any path or ref is derived from them.

Policy (see ``docs/tickets/032-entity-name-validation/design.md``):

- **Reject, never normalize** — a name is also an address, so silently
  rewriting it would collide identities and drift from what the user typed.
- **Allowlist ``[A-Za-z0-9_-]`` per component** — letters, digits, ``-``,
  ``_``. No dot: the ``.`` is the hierarchy separator Hive inserts between a
  maestro and its team (``bus/permissions.py`` parses identity by splitting
  on it). Excluding the dot also blocks ``.``/``..`` path traversal for free.
- Plus: non-empty, may not start with ``-`` (so a name can't read as a CLI
  flag), and length ``<= MAX_NAME_LEN``.
"""

from __future__ import annotations

import re

#: One name component — no dot (dot is the ``maestro.team`` separator).
_VALID_NAME = re.compile(r"[A-Za-z0-9_-]+")
_VALID_CHAR = re.compile(r"[A-Za-z0-9_-]")

#: Conservative cap for filesystem-path / git-ref headroom.
MAX_NAME_LEN = 64


def validate_name(name: str, *, kind: str = "name") -> None:
    """Validate an entity or team name component.

    Returns ``None`` on success; raises :class:`ValueError` with a clear,
    actionable message on failure. ``kind`` (e.g. ``"team name"``,
    ``"maestro name"``) is woven into the message so the caller can surface it
    verbatim.
    """
    if not name:
        raise ValueError(f"Invalid {kind}: cannot be empty.")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(
            f"Invalid {kind} {name!r}: too long ({len(name)} > {MAX_NAME_LEN} characters)."
        )
    if name.startswith("-"):
        raise ValueError(f"Invalid {kind} {name!r}: cannot start with '-'.")
    if not _VALID_NAME.fullmatch(name):
        bad = next(ch for ch in name if not _VALID_CHAR.fullmatch(ch))
        raise ValueError(
            f"Invalid {kind} {name!r}: {bad!r} not allowed — names may use "
            f"letters, digits, '-', '_' only."
        )
