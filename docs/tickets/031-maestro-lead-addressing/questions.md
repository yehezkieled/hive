# Questions — Ticket 031

The unknowns going in. Answered by the grill (2026-06-14) + code exploration;
each carries its resolution.

1. **Does the spawn auto-kickoff already deliver the maestro's goal, making the
   delegation message redundant?**
   → **No.** `spawn_team` fires `_auto_kickoff`, which sends a *generic*
   `_SPAWN_KICKOFF_TEXT` that only wakes the lead
   (`wake_scheduler.py:55-68`). The maestro's contract/goal is a separate
   `message` action — the one that failed on `self.smoke`. So the bug is real
   and the delegation hop genuinely exists.

2. **Primary mechanism: resolve a `self`/`me` alias, or hand the maestro the
   exact dotted name (spawn confirmation / JD)?**
   → **Both, alias load-bearing.** The JD already tells the maestro the name
   format (`role-maestro.md:98`) and the model *still* guessed `self` — so
   guidance-only has already failed once. The alias guarantees first-attempt
   delivery regardless of phrasing; the JD sharpening makes `self.<team>` the
   documented path. (D1)

3. **Alias scope: generic (any sender) or maestro-only? One word or both
   `self`/`me`?**
   → **Generic, both.** The resolver has no role logic today —
   `maestro`/`parent` apply to any sender; `self`/`me` mirror that. Both words,
   to stay robust to phrasing (acceptance prong 2). (D2)

4. **What does a bare `self`/`me` (no suffix) do?**
   → **Resolves to the sender → caught by the existing self-message ban**
   (`message_dispatcher.py:326-334`), which already returns "resolves to
   yourself" feedback. No special-casing. (D2)

5. **Shadow risk: a team literally named `self`/`me` would be shadowed by the
   alias. Reserve the words?**
   → **Accept and document.** Names are validated only against `/` and `..`
   (`entity.py:335`); `maestro`/`parent` already carry the identical shadow
   risk, accepted by Ticket 023. Reserving is a broader, separate change. (D2)

6. **Where does the supporting guidance land?**
   → **Two touch points:** the `spawn_team` bullet in `role-maestro.md`
   (proactive) and `_addressing_hint()`'s org-root branch (reactive — a rejected
   message teaches the alias). (D3)

7. **Docs side-effects — CONTEXT.md term? New ADR?**
   → **Neither.** `self`/`me` are addressing mechanics, not domain entities
   (CONTEXT.md stays a pure glossary); the alias-vs-force-full-name trade-off is
   the same one Ticket 023 already decided, so no new ADR — 031 extends it. (D4)

8. **Lane — direct or fan-out?**
   → **Direct.** ~10 lines in the resolver + two guidance-text edits + tests =
   one PR, one tracking issue. (D4)
