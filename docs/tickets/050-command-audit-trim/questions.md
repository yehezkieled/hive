# Questions — Ticket 050: audit & trim the command set

The unknowns going in. Answered in `research.md` (inventory + evidence) and
`design.md` (the keep/consolidate/cut decision table).

1. **What is the complete command set, and where is each defined?** Registry
   handler (`commands/dispatch.py`), `help_text` entry
   (`telegram/help_text.py`), web autocomplete (`web/templates/landing.html`).
   Which commands exist in one place but not another (drift — e.g. `heartbeat`
   is in `help_text` but not the dispatcher)?

2. **What does each command actually do**, and is it still meaningful given the
   architecture changes — Workers retired (018/ADR 0013), advisor retired (013),
   Workflow-native leaf dispatch (015/016), the conversational decision channel
   (029)? A command tied to a retired concept is a cut candidate.

3. **Classify each: keep / consolidate / cut**, with a one-line rationale.
   Keep = core control/read surface; consolidate = overlaps another and could
   merge; cut = vestigial/rarely-useful/dead.

4. **For each CUT candidate, is removal safe?** Does anything depend on the
   command token — `personalities/` role files (do Entities emit it?), `docs/`,
   `tests/`, the web autocomplete, or other code paths? A cut that breaks a role
   file or a live path is a regression, not a trim.

5. **What is the exact mechanical surface to remove one command?** Registry
   entry + its `_h_<name>` handler (or group method) + `help_text` entry +
   autocomplete token + `tests/test_help.py` drift assertion. Any shared
   helpers only that command uses?

6. **Cross-cutting impact:** does the trim touch `CONTEXT.md`, the `/help`
   drift test contract, or any reference doc?
