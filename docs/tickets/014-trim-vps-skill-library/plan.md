# Plan — Ticket 014: Trim the VPS skill library  (issue #129)

**Lane:** direct (one issue, one small PR). The bulk filesystem trim already
happened manually; this PR finishes one trim and tidies the denylist fallout.
See [`research.md`](research.md) for the full reconciliation.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `~/.claude/skills/build-with-agent-team/` (VPS, not in repo) | delete | Trim the last superseded skill (S5 Workflow engine replaces it). |
| `src/hive/process/skill_curation.py` | edit | Annotate the 3 dormant tokens (`prototype`, `grill-me`, `triage`) as `# not installed — dormant guard`; fix `Skill(brainstorming)` → `Skill(superpowers:brainstorming)` **iff** the bare form fails to deny on 2.1.170. |
| `tests/process/test_skill_curation.py` | edit (conditional) | If the brainstorming token string changes, update the literal asserts (lines 35). Structure asserts (all-roles vs thinking split) stay as-is. |

No reference-doc edits. ADR 0008 names `build-with-agent-team` as an example
allowed-fan-out skill (line 44) — **left untouched** (append-only; historical
example, reasoning intact). No new ADR (014 operates within ADR 0008's
framework; no architectural decision).

## Verification

- **Brainstorming guard (the load-bearing check):** with `~/.local/bin/claude`
  (2.1.170), confirm a Lead's spawn args genuinely make `brainstorming`
  unreachable. If bare `Skill(brainstorming)` doesn't, switch to the
  namespaced form and re-confirm.
- **Trim took:** `ls ~/.claude/skills/` no longer lists `build-with-agent-team`.
- **Gates:** `ruff check src/ tests/ && ruff format --check src/ tests/` green;
  `pytest -m "not integration"` green (esp. `tests/process/test_skill_curation.py`).
- **Fleet liveness:** the service still spawns; a maestro Turn completes
  end-to-end on deployed code (per CLAUDE.md deploy + Tailscale-IP smoke).

## Out of scope

- Re-installing any trimmed skill for dev-only use (additive config change;
  the dormant guard tokens are already in place if a skill returns).
- Per-role curation logic — that is Ticket 012, already shipped.
- The `~/.claude/commands` loop commands and the four installed plugins — all
  in active use, nothing to remove.

## Cross-cutting impact

- None to reference docs. The only code touched is `skill_curation.py`
  (+ its test). The VPS file deletion has no repo footprint.

## Build

Direct lane — one branch, one PR that closes #129. The PR edits
`skill_curation.py` (+ test if the token changes); the
`build-with-agent-team` deletion is a one-line VPS file op done alongside.
