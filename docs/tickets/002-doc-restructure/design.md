# Design

## Chosen approach

Adopt three altitudes for project-management docs plus a reference
layer for everything else. Full rationale in
[`docs/adr/0003-three-altitudes-doc-structure.md`](../../adr/0003-three-altitudes-doc-structure.md).

```
.
├── README.md              # front door (humans, GitHub)
├── CLAUDE.md              # agent hub — inlines CONVENTIONS, @-refs live docs
├── CONTEXT.md             # glossary
└── docs/
    ├── DEPLOYMENT.md      # runbook
    ├── CHANGELOG.md       # 1 line per shipped sprint
    ├── roadmap.md         # months / themes
    ├── sprints/           # 2-week windows, peer files
    ├── tickets/           # work units, artifact folders
    │   └── INDEX.md
    ├── adr/               # decisions, append-only
    └── archive/           # PROJECT_PLAN, AUDIT
```

## Time-tense layer (three altitudes)

| Altitude | File | Tense | Edit cadence |
|---|---|---|---|
| Roadmap | `docs/roadmap.md` | months | phase done, new theme, priority shift |
| Sprint | `docs/sprints/YYYY-QN-SN.md` | weeks | written at sprint start, frozen at close |
| Ticket | `docs/tickets/NNN-slug/` | days | continuously while open, frozen at close |

## Reference layer (orthogonal, differentiated rules)

| Doc | Job | Edit rule |
|---|---|---|
| `CONTEXT.md` | glossary | **Free edits**, no Ticket needed |
| `docs/ARCHITECTURE.md` / `README.md` / `docs/DEPLOYMENT.md` | system maps + runbook | **Edited inside the Ticket** that changed the code (cross-cutting label, declared in `plan.md`) |
| `docs/adr/*.md` | decisions | **Append-only** — new decision = new file |
| `docs/CHANGELOG.md` | shipped history | One line per sprint, at sprint close |

Same rule for all three doc-categories breaks at least two of them.
See `research.md` and the ADR for the worked argument.

## Key trade-offs

- **Folder-only, no workflow enforcement** — the full ticket
  workflow requires per-stage sandboxed CC sessions which do not
  exist. Folder structure goes in now; enforcement deferred.
- **Fresh Ticket numbering (001+)** — past Sprints 0–31 collapse into
  one-line CHANGELOG entries. `PROJECT_PLAN.md` stays as lossless
  historical record in `archive/`. Backfilling 31 Tickets retroactively
  would be fiction (those artifacts never existed at the time).
- **Differentiated reference-doc rules** — each ref doc has a different
  *job*; one rule for all distorts at least two of them.
- **No `ARCHITECTURE.md` in this Ticket** — desirable but not minimum
  viable. Can be its own future Ticket when the runtime migration has
  stabilised.

## What stays vs goes

- **Stays at root**: `README.md`, `CLAUDE.md`, `CONTEXT.md` — front-door
  files, hit before anything else.
- **Stays in docs/**: `DEPLOYMENT.md`.
- **Moved to archive/**: `PROJECT_PLAN.md`, `AUDIT_2026-05-05.md`.
- **Renamed and trimmed**: `ROADMAP.md` → `docs/roadmap.md`.
- **Removed**: `STATUS.md` — its current-state info lives in the
  active sprint file from now on.

## Cross-cutting impact (this Ticket)

| Reference doc | Edit |
|---|---|
| `CONTEXT.md` | Add `Sprint`, `Ticket` to the Project management section |
| `README.md` | Drop the stale architecture diagram and update the file-structure block |
| `CLAUDE.md` | Inline the CONVENTIONS text; add `@`-references to live docs |
| `docs/adr/0003-…` | New ADR capturing this decision |
