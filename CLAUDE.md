# Hive — Project Guidelines

Project-specific rules for any Claude Code session working on Hive.

## Live context (auto-loaded)

@CONTEXT.md
@docs/roadmap.md
@docs/sprints/2026-Q2-S4.md
@docs/tickets/INDEX.md

> When the sprint rolls over, update the sprint `@`-reference above.

---

## Working method — three altitudes

This repo follows a layered "altitude" structure for planning and
execution, with a staged artifact workflow per Ticket. Read this
section before doing any non-trivial work.

### Directory layout

```
.
├── README.md
├── CLAUDE.md           ← you are here
├── CONTEXT.md          ← glossary
└── docs/
    ├── DEPLOYMENT.md
    ├── CHANGELOG.md    ← one line per shipped sprint
    ├── roadmap.md      ← vision/themes, months
    ├── sprints/        ← 2-week windows
    │   └── YYYY-QN-SN.md
    ├── tickets/        ← work units (artifact folders)
    │   ├── INDEX.md    ← registry
    │   └── NNN-slug/
    │       ├── ticket.md     ← what & why
    │       ├── questions.md  ← what to find out
    │       ├── research.md   ← what we found
    │       ├── design.md     ← chosen approach
    │       ├── outline.md    ← step-by-step structure
    │       └── plan.md       ← final actionable plan
    ├── adr/            ← decisions, append-only
    └── archive/        ← retired docs
```

### The three altitudes

Each layer has a fixed scope. Do not mix them.

- **Roadmap (months).** Vision, themes, milestones, non-goals. No
  Ticket IDs, no tech choices.
- **Sprint (weeks).** Window, goal, committed Ticket links, risks,
  definition of done, out-of-scope. Links *down* into Ticket folders.
  No API designs, no file paths, no code.
- **Ticket (days).** The six artifacts above. Implementation-level
  detail lives here and only here.

Higher layers never link down to specific Tickets — only sprints do.
This keeps the roadmap stable as Tickets churn underneath.

### Ticket workflow

For any non-trivial Ticket, produce the six artifacts (`ticket.md` →
`questions.md` → `research.md` → `design.md` → `outline.md` →
`plan.md`) in order. Each handoff must be clean: a wrong premise in
`research.md` becomes a wrong design becomes a wrong plan.

**Trivial Tickets** (one or two files touched, one obvious approach,
no questions to investigate) can skip straight to `plan.md` or just
do the work. When in doubt, lean toward producing the artifacts.

**Enforcement status:** the folder structure is live; the per-stage
sandboxed Claude Code workflow (one CC session per artifact, context
handed off via the artifact file) is **not yet built**. Until that
infrastructure exists, produce only the artifacts the work needs.

### Reference docs — different rules per category

Reference docs are orthogonal to the altitudes. Each category has its
own edit rule.

- **`CONTEXT.md` (glossary)** — free edits, anytime, no Ticket needed.
- **`README.md` / `docs/DEPLOYMENT.md` / `docs/ARCHITECTURE.md`
  (system maps + runbooks)** — edited inside the Ticket that changed
  the underlying code. Declare reference-doc impact in `plan.md` so
  it's visible upfront. This is a **cross-cutting Ticket**.
- **`docs/adr/*.md` (decisions)** — append-only. New decision = new
  file with the next number. Never edit an existing ADR.
- **`docs/CHANGELOG.md`** — one line per sprint, appended at sprint
  close.

### Operating rules

- Stay inside the Ticket folder for per-Ticket work; only cross-cutting
  Tickets edit reference docs, and they declare the impact in `plan.md`.
- Update `docs/tickets/INDEX.md` whenever a Ticket is created, changes
  state, or closes.
- Commit the docs alongside the code. Ticket artifacts *are* the
  engineering work; they're not throwaway scratch.
- Non-goals sections matter. Push back if a layer doesn't declare what
  it's *not* building.
- Match editing energy to layer stability. Don't agonise over wording
  in a sprint file that will be archived in two weeks; do agonise over
  the roadmap and ADRs.
- Ask before changing structure. If a Ticket needs a new artifact
  type, propose it — don't just add it.

### When starting any task

1. Check `docs/tickets/INDEX.md` to see if a Ticket already exists.
2. If yes, read every existing artifact in that folder before writing
   code.
3. If no, ask whether to create one and at what altitude the work
   belongs.
4. Identify which workflow stage the work is at and produce the next
   artifact — don't skip ahead.

---

## Environment

This Claude Code session runs directly on the VPS
(`ubuntu-s-4vcpu-8gb-sgp1-01`). There is no separate remote machine
to deploy to — everything runs here.

- **VPS**: DigitalOcean droplet, Tailscale hostname
  `tailfb3900.ts.net`, SSH on port 7777
- **Tailscale IP**: 100.79.194.84
- **n8n**: running in Docker
- **OpenClaw**: service stopped and disabled
- **Hive service**: systemd user service (`hive.service`)

Avoid "local" language — say "not pushed to origin yet" or "on this
host" instead.

## Deployment

After merging a Ticket to `main`, run autonomously without asking:

1. `git push`
2. `systemctl --user restart hive.service`
3. Verify with `journalctl --user -u hive.service -n 20`

Smoke-test from the Tailscale IP (`http://100.79.194.84:<port>/`),
not just loopback. Loopback bypasses bind address, firewall, and
routing — all of which can fail silently.

JS-rendered features (React, htmx, Babel-in-browser) require an
actual browser check. A `curl` returning 200 with the right HTML
markers is not sufficient — the browser still has to download,
compile, and mount.

## Code quality

Before every `git push`:

```
ruff check src/ tests/ && ruff format --check src/ tests/
```

Hive CI runs both as separate gates. Fixing lint does not fix
format — they fail independently.

## Bots

Lona and Wonder run on isolated per-bot state dirs. Always use the
`lona` and `wonder` wrapper scripts — never raw `claude --channels`.

- Lona state: `~/.claude/channels/telegram-lona/`
- Wonder state: `~/.claude/channels/telegram-wonder/`

## Active work

**Phase 2 — Restructure** (Sprint
[`2026-Q2-S4`](docs/sprints/2026-Q2-S4.md), 2026-06-04 → 2026-06-18) —
close-out + hardening. Phase 2's structural work shipped in S3: the
`process/manager.py` god object is split into a facade + four
collaborators (Ticket [`004`](docs/tickets/004-manager-py-breakup/),
[ADR 0006](docs/adr/0006-god-object-breakup-composition.md)),
`WorkerAgent` → `Worker` (Ticket
[`006`](docs/tickets/006-worker-rename/)), and the headless runtime is
gone — PTY-only (Ticket
[`007`](docs/tickets/007-remove-headless-runtime/), ADR 0007). S4
finishes the phase and hardens the live fleet: Vault config submodule
(Ticket [`005`](docs/tickets/005-vault-consolidation/) — re-scoped, 004
absorbed the rest), track untracked async tasks
([`008`](docs/tickets/008-track-background-tasks/)), pin the fleet's
Claude Code version
([`009`](docs/tickets/009-pin-claude-version/)), repair the stale
integration test
([`010`](docs/tickets/010-repair-integration-test/)), a CI coverage
floor ([`011`](docs/tickets/011-ci-coverage-floor/)), and curated
Entity skill inheritance
([`012`](docs/tickets/012-entity-skill-inheritance/)). Phase 3
(dashboard → PWA) opens next.

Phase 1 (Runtime migration) is **done**: the PTY harness is deployed
and plan-billed in production (Tickets 001 + 003); the headless
`claude -p` path is fully removed (007).

Before working on `runtime/` or the `process/` modules, read the
adapter code,
[ADR 0001](docs/adr/0001-harness-agnostic-runtime.md) (harness-agnostic
runtime) and
[ADR 0006](docs/adr/0006-god-object-breakup-composition.md)
(composition pattern for the breakup) first.
