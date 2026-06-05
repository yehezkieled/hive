# 010 — Design

## Decision

Re-base the test onto the `FakeAdapter` seam, set the hierarchy up
through the facade, make the round-trip deterministic, and **drop the
`@pytest.mark.integration` marker** so CI runs it. Rewrite the
`pyproject.toml` marker description to describe real-external tests
going forward; the marker stays declared but reserved (no test uses it
for now).

### Why drop the marker (Option A)

The marker meant "needs real `claude -p`, skip in CI." 007 deleted that
path; once the test is hermetic there is no reason to hide it from CI.
Keeping it marked (Option B) means `-m "not integration"` — the command
CI and `CLAUDE.md` both run — never executes it, so it can silently rot
again. A test that never runs is the failure this ticket exists to fix.
Unmarking makes every push re-verify the lead→worker round-trip.

Rejected alternatives:
- **B — keep it marked, only fix the description.** Literal to the
  ticket, but leaves the test CI-invisible. Rejected: reintroduces the
  rot risk.
- **Delete it, lean on `TestActionRouting`.** Tempting given the
  overlap, but loses the worker→own-lead path and the audit assertion
  no unit test covers. Rejected: that coverage is the point.

## Shape of the repaired test

One obvious shape — facade setup, fake-injected single turn, assert
routing + delivery + audit:

```python
async def test_worker_replies_to_lead_via_hive_actions(
    router: MessageRouter,
    audit_log: AuditLog,
) -> None:
    mgr = ProcessManager(router=router, audit_log=audit_log, max_sessions=4)
    try:
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")          # lead dev.backend
        await mgr.spawn_worker("dev.backend", worker_name="w1")  # dev.backend.w1

        reply = (
            "Acknowledged.\n"
            "<hive_actions>\n"
            '[{"type": "message", "to": "dev.backend", "text": "task complete"}]\n'
            "</hive_actions>"
        )
        with using_adapter(mgr, FakeAdapter(reply)):
            await mgr.send_to_entity(
                "dev.backend.w1", "Ping your lead via hive_actions."
            )

        assert "dev.backend" in mgr._last_routed_actions
        delivered = await router.get_next("dev.backend", timeout=1.0)
        assert delivered is not None
        assert delivered.sender == "dev.backend.w1"
        assert "complete" in delivered.content.lower()

        events = await audit_log.recent(action_prefix="peer_message_")
        assert any(
            e["action"] == "peer_message_sent"
            and e["actor"] == "dev.backend.w1"
            and e["target"] == "dev.backend"
            for e in events
        )
    finally:
        await mgr.kill_all()
```

Notes:
- Setup via the facade fixes both root causes — correct hierarchy
  (maestro registered, dotted names so `can_message` worker→lead
  passes) and a fake on the dispatch path.
- `FakeAdapter(reply)` makes the turn deterministic; the worker no
  longer has to "decide" to message its lead.
- No `@pytest.mark.integration`. The file stays in `tests/integration/`
  (minimal diff, keeps the scaffold); being unmarked, CI now runs it.
- Docstring rewritten — it currently claims "spawns a real `claude -p`
  subprocess," which is false post-007.

## Marker rewrite (`pyproject.toml`)

```
markers = [
    "integration: tests that drive real external processes or services "
    "(e.g. a live Harness); skipped in CI. Hermetic multi-component "
    "round-trips do NOT use this marker.",
]
```

## Side effects

- **CONTEXT.md** — no new terms.
- **ADR** — no decision of architectural weight; the marker-semantics
  choice is recorded here, not in an ADR.
- **Cross-cutting reference docs** — none (`pyproject.toml` is project
  config, edited normally).
