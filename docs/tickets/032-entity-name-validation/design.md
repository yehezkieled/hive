# Design — Ticket 032: Validate entity/team names

Three decisions, each settled in the design grill and grounded in `research.md`.

## D1 — Reject, don't normalize

Bad names are **rejected** with a clear error; Hive never silently rewrites a
name.

**Why:** a name is also an identity and an **address** (`maestro.team`), not a
display label. Normalizing risks two inputs (`my team`, `my-team`) collapsing
onto one identity, and an entity whose real name differs from what the user
typed. Fail-loud is predictable and teaches the rule. Matches the ticket's
stated lean ("fail loud over silently rewrite").

**Alternative (rejected):** normalize / slugify — convenient, but hides the
mistake and creates collision / identity-drift on something that doubles as an
address.

Case is **preserved** (`Otter` stays `Otter`); duplicate detection stays
exact-match as today — case-folding collisions are out of scope.

## D2 — Allowlist `[A-Za-z0-9_-]` per component; no dots

A valid name component is one or more of: ASCII letters, digits, `-`, `_`.
Plus structural rules:
- **non-empty** (and not all-whitespace),
- **may not start with `-`** (so a name can't be read as a CLI flag),
- **length ≤ 64** (filesystem-path / git-ref headroom).

**Why no dot:** the `.` is the hierarchy separator Hive inserts between maestro
and team; `permissions.py` parses identity by splitting on it (research §3). A
dot inside a component corrupts org parsing. Excluding the dot also blocks `.`
and `..` (path traversal) for free.

**Why these structural rules:** together they close every boundary case the
ticket names — `/`, space, `..`, leading `-`, empty, shell-meta — under one
rule: *charset + non-empty + no-leading-dash + length cap*.

**Alternative (rejected):** the ticket's first-draft `[A-Za-z0-9._-]` (with
dot) — breaks addressing; dropped after research §3.

All existing names stay valid (research §5).

## D3 — Validate at the two chokepoints; feed the error back on every path

Validate `name` in `register_maestro` and `team_name` in `create_team`, at the
**top** of each — before any worktree dir, git branch, or address is derived.

- The two **human paths already surface** the `ValueError` (research §4) — no
  extra work; the rejection reaches the user verbatim.
- The maestro **`spawn_team` path is log-only** today; route the rejection back
  to the maestro through the existing `_handle_parse_errors` feedback channel
  (research §4) so it can self-correct and retry, instead of silently failing.

**Why one shared validator:** one rule, two call sites, no drift. New module
`src/hive/process/names.py` keeps it beside its consumers and trivially
unit-testable in isolation.

### Shape
```python
def validate_name(name: str, *, kind: str) -> None:
    """Return on success; raise ValueError on a bad name.

    kind is "maestro name" / "team name" so the message is specific:
    ValueError("Invalid team name 'my team': ' ' not allowed — names may use
                letters, digits, '-', '_' only.")
    """
```

## Not an ADR

Small, well-bounded, and easily reversible (loosening the charset later is
trivial; the rule isn't an architectural commitment). The S7 sprint doc flags
033 and 034 as ADR-worthy but **not** 032. The reasoning lives here.

## Glossary impact

`CONTEXT.md` gains one term — the **name component vs. address** distinction
(currently only implicit). Glossary-pure: no charset / implementation detail.
