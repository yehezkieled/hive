# Outline — Ticket 013

Implementation structure, grouped so each group is independently testable. Order
matters: do the **gate decouple (B)** before the **delete (C)** so knowledge
search never breaks mid-change.

## A. Native advisor enablement (per-Entity, model-aware)

- **Role-file field** — extend the personality parser (`models/entity.py:91`,
  alongside `**Model**:`) to read an optional `**Advisor**:` field → `entity.advisor`.
- **Default resolution** — a helper `resolve_advisor(model, advisor_field)`:
  unset → `opus` if `model` weaker than Opus else `off`; explicit value wins.
- **Spawn flag** — in `entity.py` build-args, append `--advisor <model>` when
  resolved ≠ `off`.
- **Delete suppression** — remove `_suppress_advisor`/`_restore_advisor` and
  their call sites (`pty_session.py:175,187,189,241,255,356-385`) and the
  `advisorModel` global from fleet settings; the per-spawn flag is now authoritative.

## B. Decouple the `--mcp-config` gate (do before C)

- **`mcp/config.py`** — drop the hard-coded `"hive"` advisor server; `servers`
  starts `{}`, adds `"hive-knowledge"` only when `HIVE_KNOWLEDGE_MCP_ENABLED`.
  `generate_mcp_config` writes the file only when `servers` is non-empty and
  returns the path or `None`.
- **Call sites** — `entity.py:280-283`, `lifecycle_manager.py:25,127`,
  `message_dispatcher.py:198-199`: stop importing `ADVISOR_ENABLED`; gate
  `--mcp-config` on "a config with ≥1 server was written".
- **`manager.py:34`** — drop the `ADVISOR_ENABLED` re-export; keep
  `generate_mcp_config`.

## C. Delete the custom advisor

- `git rm` `mcp/advisor_server.py`, `bus/advisor_store.py`.
- `config.py:202-206` — remove all four `ADVISOR_*` vars.
- `tests/test_advisor_mcp.py` — `git rm`, but first **migrate** its
  `generate_mcp_config` / `mcp_config_path` / gate assertions (`:54-115`) into
  `tests/mcp/test_knowledge_server.py` (rewritten knowledge-only).

## D. Drop the table

- New `bus/migrations/028_drop_advisor_calls.sql` → `DROP TABLE IF EXISTS advisor_calls;`
  (015 + 025 untouched — append-only).

## E. Touched tests (beyond the deletes)

- `tests/process/test_thin_core_smoke.py:127,135,205` — drop `ADVISOR_ENABLED`
  from the expected re-export surface.
- `tests/mcp/test_knowledge_server.py:204-229` — assert knowledge-only servers;
  absorb the migrated coverage from C.
- `tests/process/test_lifecycle_manager.py:6` — fix the stale docstring.
- New: a test that resolves the model-aware default (Sonnet→opus, Opus→off) and
  that `--advisor` reaches the spawn args; a test that `--mcp-config` is present
  with knowledge on and absent with knowledge off + advisor gone.

## F. Role files + nudge

- `personalities/role-worker.md` → `**Advisor**: off`.
- `personalities/role-lead.md` → rely on default (or explicit `**Advisor**: opus`)
  + one-line nudge ("consult the advisor before locking in a plan / before
  declaring done").
- `personalities/role-maestro.md` → leave at default `off` unless the human opts
  in (`**Advisor**: opus`); `_template.md` documents the field.

## G. Cross-cutting docs

- `docs/DEPLOYMENT.md` — edit the 6 advisor spots (`:34,:92,:190,:1105,:1109-1112,:1152`).
- `README.md:154` — source-tree map line.

## H. Verification

`ruff check src/ tests/ && ruff format --check src/ tests/`; full
`pytest -m "not integration"`; grep clean for `advisor_server`, `advisor_store`,
`ADVISOR_ENABLED`, `claude -p`; a maestro turn end-to-end on deployed code with
knowledge search still answering.
