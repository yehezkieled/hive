# Questions — Ticket 012: Curate & expose CC skills to Entities

The unknowns going into this ticket, grouped. Answers (with code
evidence) land in `research.md`; the genuine design forks are resolved
in `design.md` via the grill.

## Mechanism — how do you actually scope skills?

1. How does a spawned Claude Code session discover skills, and from how
   many sources (user `~/.claude/skills`, project, plugins, built-ins)?
2. Can you restrict *which* skills a session sees? Allowlist, denylist,
   or both? What is the exact setting/flag syntax?
3. Can the restriction differ **per spawned process** without mutating
   the shared `~/.claude`? (Different roles need different sets at the
   same time.)
4. Is the ticket's framing — "each role gets **only** its curated
   subset" (allowlist) — actually achievable, or does the platform only
   support exclusion (denylist)?
5. Are any dangerous skills **un-restrictable** (e.g. built-ins)? If so,
   which, and what's the residual exposure?

## Integration — where does it plug into Hive?

6. Does Hive already pass any tool/permission flags at spawn we can
   reuse, or is this net-new plumbing?
7. Where is the natural injection site — the auto-generated personality
   `## Tools` section, the `Entity` model, the adapter, or a new module?
8. Does the chosen syntax survive Hive's existing parse path (the
   personality `## Tools` section → `entity.disallowed_tools`)?
9. Does the fleet's **pinned** Claude Code version (Ticket 009) support
   the chosen syntax, not just the dev binary?

## Curation — what goes on the list, per role?

10. What is the per-role allowed/denied split for Maestro, Team Lead,
    Worker?
11. Is the **block** list per-role, or is safety global (block the
    dangerous buckets for everyone) with per-role differences living
    only in the JD "use these" prompting?
12. Where is the line on skills that spawn **sub-agents**? A read-only
    `research-codebase` Explore agent vs. `build-with-agent-team` /
    `dispatching-parallel-agents` mass fan-out — same bucket or not?
13. How do we keep the curation list from rotting as the user's skill
    library changes (new skills default to allowed under a denylist)?

## Prompting — making Entities actually use the safe skills

14. Where in each role JD does a "use these skills when…" block belong,
    and what should it say per role?

## Verification — proving it works

15. How do we verify hermetically in CI (no live `claude`) that denied
    skills are absent from each role's spawn — reuse the mocked-PTY seam
    (Ticket 010)?
16. What is the live smoke check — spawn a real Entity, confirm a denied
    skill is unreachable and an allowed one invokes?

## Scope

17. Direct lane (one PR) or fan-out (issues + fleet)? The sprint flags
    012 as "a feature in a hardening sprint — keep it scoped."
18. Is per-spawn config-dir isolation (à la Lona/Wonder's
    `TELEGRAM_STATE_DIR`) in scope, or explicitly deferred as overkill?
