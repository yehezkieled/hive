# 010 — Questions

The unknowns going in, before code research answered them.

1. **What seam replaces the deleted `claude -p` path for faking a turn?**
   007 removed the headless subprocess wrapper the test mocked. What is
   the current injection point, and is there a shared test double?

2. **Does the facade API the old test pokes still exist post-004?**
   The test reaches into `mgr._entities[...]`, `send_to_entity`,
   `_last_routed_actions`, `kill_all`. After the manager breakup
   (Ticket 004) these may have moved to collaborators.

3. **What exact `<hive_actions>` text must the fake return** so the
   worker→lead message routes deterministically (no real model)?

4. **Is the test now redundant** with existing unit tests that already
   drive `send_to_entity` through a fake? If so, what — if anything —
   does it uniquely cover that justifies repair over deletion?

5. **What should `@pytest.mark.integration` mean post-007**, once the
   only real-external dependency (`claude -p`) is gone? This decides
   whether the repaired test stays CI-skipped or runs in CI.
