# 033 — Questions

The unknowns going in. All resolved in the design grill (see `design.md`);
each answer is captured in `research.md` (code facts) or `design.md`
(decisions).

## Code facts to verify (don't trust the ticket)

1. **Are there really two prompt-assembly seams, and is either live?**
   → Yes, two seams exist; only one is live. `claude_adapter._build_pty_system_prompts`
   is the live PTY path; `entity.build_cli_args` (+ `Maestro.build_cli_args`) is
   **dead** — called only from tests, building the removed-headless `claude -p`
   invocation (Ticket 007). See `research.md`.

2. **Where is `is_pa` defined, and does it reach the prompt layer?**
   → Computed once at `lifecycle_manager.py:311` (`entity.name == DEFAULT_MAESTRO`),
   used only for the ownership write-fence. It never reaches prompt assembly.

3. **Does `ClaudeAdapterConfig` carry anything the prompt builder could key on?**
   → It carries `name`/`role` but no `is_pa`. The builder would have to re-derive
   identity unless we add a field.

4. **What in `role-maestro.md` mis-describes the PA?**
   → The opening (lines 3–5: "decide what teams the *project* needs… a maestro
   owns a project") and, separately, a stale "Workers do the actual coding"
   line (Workers retired — ADR 0013 / Ticket 018).

5. **Is `_build_pty_system_prompts` tested today?**
   → No. `test_claude_adapter.py` covers only `_build_pty_extra_args`. This
   ticket closes that blind spot.

## Design decisions to make

6. Single source of truth for `is_pa`, or re-derive per call site? → **property** (Q1)
7. How does `is_pa` cross the entity→config boundary? → **config field** (Q2)
8. PA-role prompt strategy: separate file / override / neutralize+append? → **neutralize + append** (Q3)
9. What to do with the dead `build_cli_args`? → **leave + note cleanup ticket** (Q4)
10. Edit scope of `role-maestro.md`? → **ownership framing + the stale Workers line** (Q5)
11. Test layers + regression guard on the file edits? → **all three layers + guard** (Q6)
12. ADR-worthy? Lane? → **no ADR; direct lane** (Q7)
