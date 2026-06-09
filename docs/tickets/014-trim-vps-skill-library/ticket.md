# 014 — Trim the installed Claude Code skill library on the VPS

## What

Remove, at the filesystem level on the VPS (`~/.claude/skills` and any
unused plugin skills), the Claude Code skills that **no** actor needs —
neither the developer nor any Hive Entity. This is the global,
all-or-nothing complement to Ticket 012's per-role denylist.

## Why

Ticket 012 ([ADR 0008](../../adr/0008-per-role-skill-curation-denylist.md))
curates skills *per role* via a denylist — the only layer that can express
"the Maestro keeps `grill-me` but a Worker doesn't." It deliberately does
**not** delete anything, because deletion is global.

Some installed skills, though, are dead weight for this machine entirely
(exercise scaffolding, article editing, course tooling, migration helpers
for stacks Hive doesn't use). Trimming them:

- shrinks the surface the 012 denylist has to reason about (fewer tokens,
  less rot);
- reduces skill-discovery noise in every spawned session;
- is safe — a skill deleted from disk is simply gone for everyone.

The two layers compose cleanly: filesystem trim = global removal; 012
denylist = per-role split. Denying a since-deleted skill is a harmless
no-op, so trimming never breaks 012.

## Acceptance

- A reviewed list of skills to remove (kept vs trimmed), recorded in this
  ticket, agreed before any deletion.
- The agreed skills removed from the VPS `~/.claude` (user skills and/or
  disabled plugins); the fleet still spawns and a maestro Turn completes.
- `skill_curation.py` (Ticket 012) revisited: deny tokens for any
  now-deleted skill pruned (no-ops, but kept tidy).

## Non-goals

- Per-role curation — that is Ticket 012's denylist, already shipped.
- Removing skills any Entity legitimately uses (the 012 allow set).
- Changing how skills are discovered or loaded.

## Note

Trivial/ops ticket — driven by the developer's manual VPS pass. Likely
just `ticket.md` → `plan.md` (or done directly). Follow-up to 012.
