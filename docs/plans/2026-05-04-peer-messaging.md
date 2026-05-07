# Peer Messaging at All Tiers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable peer-to-peer messaging between maestros, between leads under the same maestro, and between workers (with the lead/maestro automatically CC'd on cross-parent peer messages). Add a new `request_decision` action type so escalation stays distinct from peer chatter.

**Architecture:** Extend `bus/permissions.py` with peer rules and a CC-target resolver. Update the `message` handler in `process/manager.py` to consult the new resolver and enqueue CC copies. Add a `request_decision` action handler. Inject a per-entity "peers you can message" directory into every prompt sent through `send_to_entity` so entities discover their peers without a new tool. All routing reuses the existing `MessageRouter` and pending-messages queue — no new tables.

**Tech Stack:** Python 3.11+, pytest, asyncio, dataclasses. PostgreSQL (no schema changes; new audit-event strings only).

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `src/hive/bus/permissions.py` | Add peer-allow rules to `can_message`; add `cc_targets_for`; add `can_request_decision`. |
| `src/hive/bus/actions.py` | Add `request_decision` action type to dataclass + parser. |
| `src/hive/process/manager.py` | Update `_handle_actions` message branch to use peer rules + CC; add `request_decision` branch; add `_peer_directory_for` helper; prepend peer directory in `send_to_entity`. |
| `tests/test_peer_messaging.py` | New: permission matrix, CC routing, request_decision routing, peer-directory injection. |
| `docs/PROJECT_PLAN.md` | Mark this sprint done; update roadmap. |
| `docs/DEPLOYMENT.md` | (Likely no change — confirm during step.) |

---

## Task 1: Add `can_message` peer rules + tests

**Files:**
- Modify: `src/hive/bus/permissions.py:21-49`
- Test: `tests/test_peer_messaging.py` (new file)

- [ ] **Step 1.1: Write failing tests for permission matrix**

Create `tests/test_peer_messaging.py`:

```python
"""Permission matrix and routing tests for tier-aware peer messaging."""

from __future__ import annotations

import pytest

from hive.bus.permissions import can_message, cc_targets_for, can_request_decision


# ---- can_message peer rules ----

class TestPeerMessagingPermissions:
    def test_worker_to_worker_same_team_allowed(self):
        # dev.backend.w1 -> dev.backend.w2 (same lead dev.backend)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w2") is True

    def test_worker_to_worker_cross_team_same_maestro_allowed(self):
        # dev.backend.w1 -> dev.payments.w1 (different leads, same maestro dev)
        assert can_message("worker", "dev.backend.w1", "worker", "dev.payments.w1") is True

    def test_worker_to_worker_cross_maestro_denied(self):
        # dev.backend.w1 -> ops.deploy.w1 (different maestros)
        assert can_message("worker", "dev.backend.w1", "worker", "ops.deploy.w1") is False

    def test_lead_to_lead_same_maestro_allowed(self):
        # dev.backend -> dev.payments (same maestro dev)
        assert can_message("lead", "dev.backend", "lead", "dev.payments") is True

    def test_lead_to_lead_cross_maestro_allowed(self):
        # dev.backend -> ops.deploy (different maestros — allowed but with CC)
        assert can_message("lead", "dev.backend", "lead", "ops.deploy") is True

    def test_maestro_to_maestro_allowed(self):
        # dev -> ops (top-tier peers)
        assert can_message("maestro", "dev", "maestro", "ops") is True

    def test_existing_parent_child_still_allowed(self):
        # Regression: existing rules must keep working.
        assert can_message("worker", "dev.backend.w1", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "worker", "dev.backend.w1") is True
        assert can_message("maestro", "dev", "lead", "dev.backend") is True
        assert can_message("lead", "dev.backend", "maestro", "dev") is True

    def test_worker_to_worker_self_denied(self):
        # Self-message is disallowed (also no CC would be valid).
        assert can_message("worker", "dev.backend.w1", "worker", "dev.backend.w1") is False
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerMessagingPermissions -v`

Expected: ImportError on `cc_targets_for, can_request_decision` (don't exist yet) OR most peer tests FAIL.

- [ ] **Step 1.3: Extend `can_message` in `permissions.py`**

Replace the body of `can_message` (lines 32-49) with:

```python
    # Self-message is never allowed.
    if sender_name == recipient_name:
        return False

    sender_maestro = sender_name.split(".")[0]
    recipient_maestro = recipient_name.split(".")[0]

    # ---- Peer-to-peer rules (new) ----
    if sender_role == recipient_role:
        if sender_role == "maestro":
            # Maestros are top-tier peers — always allowed.
            return True
        if sender_role == "lead":
            # Leads can message any other lead (same or cross-maestro).
            return True
        if sender_role == "worker":
            # Workers can message workers in the same maestro org.
            return sender_maestro == recipient_maestro

    # ---- Existing parent-child rules ----
    if sender_role == "maestro":
        return recipient_name.startswith(f"{sender_name}.")

    if sender_role == "lead":
        if recipient_name.startswith(f"{sender_name}."):
            return True
        return recipient_name == sender_maestro

    if sender_role == "worker":
        lead_name = ".".join(sender_name.split(".")[:-1])
        return recipient_name == lead_name

    return False
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerMessagingPermissions -v`

Expected: All 8 tests PASS.

- [ ] **Step 1.5: Run regression on existing permission tests**

Run: `cd /home/hezki/projects/hive && pytest tests/test_permissions.py -v`

Expected: All existing tests still PASS.

- [ ] **Step 1.6: Commit**

```bash
cd /home/hezki/projects/hive
git checkout -b feat/peer-messaging
git add src/hive/bus/permissions.py tests/test_peer_messaging.py
git commit -m "feat(bus): allow peer messaging in can_message"
```

---

## Task 2: Add `cc_targets_for` resolver + tests

**Files:**
- Modify: `src/hive/bus/permissions.py` (append new function)
- Test: `tests/test_peer_messaging.py` (extend)

- [ ] **Step 2.1: Write failing tests for CC targets**

Append to `tests/test_peer_messaging.py`:

```python
class TestCcTargetsFor:
    def test_no_cc_for_same_team_workers(self):
        assert cc_targets_for("worker", "dev.backend.w1", "worker", "dev.backend.w2") == []

    def test_cross_team_workers_cc_both_leads(self):
        result = cc_targets_for("worker", "dev.backend.w1", "worker", "dev.payments.w1")
        assert sorted(result) == ["dev.backend", "dev.payments"]

    def test_no_cc_for_same_maestro_leads(self):
        assert cc_targets_for("lead", "dev.backend", "lead", "dev.payments") == []

    def test_cross_maestro_leads_cc_both_maestros(self):
        result = cc_targets_for("lead", "dev.backend", "lead", "ops.deploy")
        assert sorted(result) == ["dev", "ops"]

    def test_no_cc_for_maestro_peers(self):
        assert cc_targets_for("maestro", "dev", "maestro", "ops") == []

    def test_no_cc_for_parent_child_routes(self):
        # Existing parent-child routes carry no CC.
        assert cc_targets_for("worker", "dev.backend.w1", "lead", "dev.backend") == []
        assert cc_targets_for("lead", "dev.backend", "maestro", "dev") == []
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestCcTargetsFor -v`

Expected: ImportError (function doesn't exist yet).

- [ ] **Step 2.3: Implement `cc_targets_for`**

Append to `src/hive/bus/permissions.py`:

```python
def cc_targets_for(
    sender_role: str,
    sender_name: str,
    recipient_role: str,
    recipient_name: str,
) -> list[str]:
    """Return parent names that should be CC'd when sender messages recipient.

    Cross-parent peer messages get a CC to each peer's direct parent so the
    parent retains visibility. Same-parent peers and parent-child messages
    get no CC. Parents themselves are never recipients of CCs they would
    have received as the direct route.
    """
    if sender_role != recipient_role:
        return []

    sender_maestro = sender_name.split(".")[0]
    recipient_maestro = recipient_name.split(".")[0]

    if sender_role == "maestro":
        return []

    if sender_role == "lead":
        # Same-maestro leads chat directly. Cross-maestro leads CC both maestros.
        if sender_maestro == recipient_maestro:
            return []
        return [sender_maestro, recipient_maestro]

    if sender_role == "worker":
        sender_lead = ".".join(sender_name.split(".")[:-1])
        recipient_lead = ".".join(recipient_name.split(".")[:-1])
        # Same-team workers chat directly. Cross-team CCs both leads.
        if sender_lead == recipient_lead:
            return []
        return [sender_lead, recipient_lead]

    return []
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestCcTargetsFor -v`

Expected: All 6 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/hive/bus/permissions.py tests/test_peer_messaging.py
git commit -m "feat(bus): add cc_targets_for resolver for peer messages"
```

---

## Task 3: Add `can_request_decision` + tests

**Files:**
- Modify: `src/hive/bus/permissions.py`
- Test: `tests/test_peer_messaging.py`

- [ ] **Step 3.1: Write failing tests**

Append to `tests/test_peer_messaging.py`:

```python
class TestCanRequestDecision:
    def test_worker_to_own_lead_allowed(self):
        assert can_request_decision("worker", "dev.backend.w1", "dev.backend") is True

    def test_lead_to_own_maestro_allowed(self):
        assert can_request_decision("lead", "dev.backend", "dev") is True

    def test_worker_to_other_lead_denied(self):
        assert can_request_decision("worker", "dev.backend.w1", "dev.payments") is False

    def test_worker_skipping_lead_to_maestro_denied(self):
        assert can_request_decision("worker", "dev.backend.w1", "dev") is False

    def test_lead_to_other_maestro_denied(self):
        assert can_request_decision("lead", "dev.backend", "ops") is False

    def test_maestro_cannot_request_decision(self):
        # Maestros are top-tier — no parent to escalate to.
        assert can_request_decision("maestro", "dev", "user") is False
        assert can_request_decision("maestro", "dev", "ops") is False
```

- [ ] **Step 3.2: Run to verify failure**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestCanRequestDecision -v`

Expected: ImportError.

- [ ] **Step 3.3: Implement `can_request_decision`**

Append to `src/hive/bus/permissions.py`:

```python
def can_request_decision(
    sender_role: str,
    sender_name: str,
    target_name: str,
) -> bool:
    """Strict parent-only escalation gate.

    Workers can only request_decision from their direct lead; leads only
    from their direct maestro; maestros have no parent to escalate to.
    """
    if sender_role == "worker":
        sender_lead = ".".join(sender_name.split(".")[:-1])
        return target_name == sender_lead

    if sender_role == "lead":
        sender_maestro = sender_name.split(".")[0]
        return target_name == sender_maestro

    return False
```

- [ ] **Step 3.4: Run tests to verify pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestCanRequestDecision -v`

Expected: All 6 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/hive/bus/permissions.py tests/test_peer_messaging.py
git commit -m "feat(bus): add can_request_decision permission gate"
```

---

## Task 4: Add `request_decision` action type to parser

**Files:**
- Modify: `src/hive/bus/actions.py`
- Test: `tests/test_actions.py` (extend)

- [ ] **Step 4.1: Write failing test for parser**

Append to `tests/test_actions.py`:

```python
def test_parse_request_decision_action():
    response = '''Some text.
<hive_actions>
[{"type": "request_decision", "to": "dev.backend", "text": "Should I use JWT or sessions?"}]
</hive_actions>'''
    clean, actions = parse_actions(response)
    assert clean == "Some text."
    assert len(actions) == 1
    assert actions[0].type == "request_decision"
    assert actions[0].to == "dev.backend"
    assert actions[0].text == "Should I use JWT or sessions?"


def test_parse_request_decision_missing_fields():
    response = '''<hive_actions>
[{"type": "request_decision", "to": "dev.backend"}]
</hive_actions>'''
    clean, actions = parse_actions(response)
    assert actions == []  # missing `text` is rejected
```

- [ ] **Step 4.2: Run to verify failure**

Run: `cd /home/hezki/projects/hive && pytest tests/test_actions.py::test_parse_request_decision_action tests/test_actions.py::test_parse_request_decision_missing_fields -v`

Expected: 2 FAILs — the parser silently drops unknown action types.

- [ ] **Step 4.3: Add `request_decision` to parser**

In `src/hive/bus/actions.py`:

After line 44 (`_KILL_ENTITY_REQUIRED = {"target"}`), add:
```python
_REQUEST_DECISION_REQUIRED = {"to", "text"}
```

After the `kill_entity` block (after line 196), add:
```python
        if atype == "request_decision":
            missing = _REQUEST_DECISION_REQUIRED - item.keys()
            if missing:
                logger.warning("request_decision missing fields %s: %s", missing, item)
                continue
            actions.append(Action(type=atype, to=item["to"], text=item["text"]))
            continue
```

Also update the module docstring (lines 7-22) to document `request_decision`. After the `message` line, insert:
```
- ``request_decision``: escalate a directional decision to the direct
  parent (worker → own lead, lead → own maestro). Fields: ``to``, ``text``.
```

- [ ] **Step 4.4: Run tests to verify pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_actions.py -v`

Expected: All actions tests PASS, including the 2 new ones.

- [ ] **Step 4.5: Commit**

```bash
git add src/hive/bus/actions.py tests/test_actions.py
git commit -m "feat(actions): add request_decision action type"
```

---

## Task 5: Update `_handle_actions` message branch — peer routing + CC

**Files:**
- Modify: `src/hive/process/manager.py:559-574`
- Test: `tests/test_peer_messaging.py`

- [ ] **Step 5.1: Write failing integration test**

Append to `tests/test_peer_messaging.py`:

```python
import pytest

from hive.bus.actions import Action
from tests.fixtures.process_manager import build_test_manager  # see Step 5.2 if missing


@pytest.mark.asyncio
class TestPeerMessageRouting:
    async def test_same_team_worker_message_no_cc(self, tmp_path):
        # 1 maestro, 1 team, 2 workers in same team.
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.spawn_worker("dev.backend", worker_name="w1")
        await mgr.spawn_worker("dev.backend", worker_name="w2")

        action = Action(type="message", to="dev.backend.w2", text="hello peer")
        await mgr._handle_actions("dev.backend.w1", "", [action])

        # w2 has the message; lead has nothing.
        assert mgr.router.has_pending("dev.backend.w2")
        assert not mgr.router.has_pending("dev.backend")

    async def test_cross_team_worker_message_ccs_both_leads(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.create_team("dev", "payments")
        await mgr.spawn_worker("dev.backend", worker_name="w1")
        await mgr.spawn_worker("dev.payments", worker_name="w1")

        action = Action(type="message", to="dev.payments.w1", text="cross-team")
        await mgr._handle_actions("dev.backend.w1", "", [action])

        assert mgr.router.has_pending("dev.payments.w1")  # direct
        assert mgr.router.has_pending("dev.backend")      # CC sender's lead
        assert mgr.router.has_pending("dev.payments")     # CC recipient's lead

    async def test_cross_maestro_worker_blocked(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.register_maestro("ops")
        await mgr.create_team("dev", "backend")
        await mgr.create_team("ops", "deploy")
        await mgr.spawn_worker("dev.backend", worker_name="w1")
        await mgr.spawn_worker("ops.deploy", worker_name="w1")

        action = Action(type="message", to="ops.deploy.w1", text="leak")
        await mgr._handle_actions("dev.backend.w1", "", [action])

        assert not mgr.router.has_pending("ops.deploy.w1")
```

- [ ] **Step 5.2: Add fixture if missing**

Check `tests/fixtures/process_manager.py`. If a `build_test_manager` factory does not exist, add one (see existing patterns in `tests/conftest.py` or `tests/test_process_manager.py` — match their style). Minimum requirements: returns a `ProcessManager` with an in-memory `MessageRouter` + `MessageStore`, `EntityStore` set to None or in-memory equivalent, and `audit_log` set to None or a no-op.

If existing tests already use a different fixture (e.g. `make_manager`), import that instead and adjust the test imports above.

- [ ] **Step 5.3: Run to verify failure**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerMessageRouting -v`

Expected: tests fail because cross-team peer routing is denied (current `can_message` blocks peers; if Tasks 1-2 are merged this will deny CC).

- [ ] **Step 5.4: Update message handler**

In `src/hive/process/manager.py`, replace lines 559-574 (the `if action.type == "message":` block) with:

```python
            if action.type == "message":
                recipient = self._entities.get(action.to) if action.to else None
                if not recipient:
                    logger.warning("Unknown recipient: %s", action.to)
                    continue
                if not can_message(entity.role, entity.name, recipient.role, recipient.name):
                    logger.warning("Permission denied: %s -> %s", entity.name, action.to)
                    await self._audit(
                        "peer_message_blocked",
                        target=action.to,
                        details={"sender": entity_name, "reason": "permission_denied"},
                        actor=entity_name,
                    )
                    continue
                body = action.text or ""
                await self.router.route(entity_name, action.to, body)
                self._last_routed_actions.append(action.to)
                await self._audit(
                    "peer_message_sent",
                    target=action.to,
                    details={"sender": entity_name, "text": body[:200]},
                    actor=entity_name,
                )
                # CC any cross-parent observers.
                cc_targets = cc_targets_for(
                    entity.role, entity.name, recipient.role, recipient.name
                )
                cc_body = f"[CC: {entity.name} -> {action.to}] {body}"
                for cc_name in cc_targets:
                    if cc_name not in self._entities:
                        continue
                    await self.router.route(entity_name, cc_name, cc_body)
                    await self._audit(
                        "peer_message_cc_inserted",
                        target=cc_name,
                        details={
                            "sender": entity_name,
                            "recipient": action.to,
                            "text": body[:200],
                        },
                        actor=entity_name,
                    )
```

Also update the import at line 14:

```python
from hive.bus.permissions import (
    can_kill,
    can_message,
    can_request_decision,
    can_spawn_team,
    can_spawn_worker,
    cc_targets_for,
)
```

- [ ] **Step 5.5: Run tests to verify pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerMessageRouting -v`

Expected: All 3 tests PASS.

- [ ] **Step 5.6: Run regression**

Run: `cd /home/hezki/projects/hive && pytest tests/test_process_manager.py tests/test_router.py -v`

Expected: existing tests still PASS. (Audit-event name was renamed from `message.autonomous` to `peer_message_sent`. If any existing test asserts on the old name, update that test in this commit too.)

- [ ] **Step 5.7: Commit**

```bash
git add src/hive/process/manager.py tests/test_peer_messaging.py tests/fixtures/
git commit -m "feat(manager): peer message routing with CC to parents"
```

---

## Task 6: Add `request_decision` handler in `_handle_actions`

**Files:**
- Modify: `src/hive/process/manager.py:558-731`
- Test: `tests/test_peer_messaging.py`

- [ ] **Step 6.1: Write failing test**

Append to `tests/test_peer_messaging.py`:

```python
@pytest.mark.asyncio
class TestRequestDecision:
    async def test_worker_to_own_lead_allowed(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.spawn_worker("dev.backend", worker_name="w1")

        action = Action(type="request_decision", to="dev.backend", text="JWT or sessions?")
        await mgr._handle_actions("dev.backend.w1", "", [action])

        assert mgr.router.has_pending("dev.backend")

    async def test_worker_skipping_to_maestro_blocked(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.spawn_worker("dev.backend", worker_name="w1")

        action = Action(type="request_decision", to="dev", text="bypass attempt")
        await mgr._handle_actions("dev.backend.w1", "", [action])

        assert not mgr.router.has_pending("dev")

    async def test_lead_to_own_maestro_allowed(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")

        action = Action(type="request_decision", to="dev", text="add new team?")
        await mgr._handle_actions("dev.backend", "", [action])

        assert mgr.router.has_pending("dev")
```

- [ ] **Step 6.2: Run to verify failure**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestRequestDecision -v`

Expected: 3 FAILs — `request_decision` not yet handled in `_handle_actions`.

- [ ] **Step 6.3: Add the handler branch**

In `src/hive/process/manager.py`, inside `_handle_actions`, after the `message` block (after the new code added in Task 5), insert:

```python
            elif action.type == "request_decision":
                if not action.to:
                    continue
                recipient = self._entities.get(action.to)
                if not recipient:
                    logger.warning("Unknown request_decision recipient: %s", action.to)
                    continue
                if not can_request_decision(entity.role, entity.name, action.to):
                    logger.warning(
                        "request_decision denied: %s -> %s", entity.name, action.to
                    )
                    await self._audit(
                        "request_decision_blocked",
                        target=action.to,
                        details={"sender": entity_name, "reason": "permission_denied"},
                        actor=entity_name,
                    )
                    continue
                body = f"[DECISION REQUEST] {action.text or ''}"
                await self.router.route(entity_name, action.to, body)
                self._last_routed_actions.append(action.to)
                await self._audit(
                    "request_decision_sent",
                    target=action.to,
                    details={"sender": entity_name, "text": (action.text or "")[:200]},
                    actor=entity_name,
                )
```

- [ ] **Step 6.4: Run tests to verify pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestRequestDecision -v`

Expected: 3 PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/hive/process/manager.py tests/test_peer_messaging.py
git commit -m "feat(manager): handle request_decision action with parent-only routing"
```

---

## Task 7: Inject peer directory at spawn time

**Files:**
- Modify: `src/hive/process/manager.py` — add `_peer_directory_for`; modify `send_to_entity`
- Test: `tests/test_peer_messaging.py`

- [ ] **Step 7.1: Write failing test for directory builder**

Append to `tests/test_peer_messaging.py`:

```python
@pytest.mark.asyncio
class TestPeerDirectory:
    async def test_worker_directory_lists_peers_and_parent(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.create_team("dev", "payments")
        await mgr.spawn_worker("dev.backend", worker_name="w1")
        await mgr.spawn_worker("dev.backend", worker_name="w2")
        await mgr.spawn_worker("dev.payments", worker_name="w1")

        directory = mgr._peer_directory_for("dev.backend.w1")

        assert "dev.backend.w2" in directory
        assert "same team" in directory
        assert "dev.payments.w1" in directory
        assert "cross-team" in directory
        assert "dev.backend" in directory  # parent for request_decision

    async def test_lead_directory_lists_other_leads(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.create_team("dev", "payments")

        directory = mgr._peer_directory_for("dev.backend")
        assert "dev.payments" in directory
        assert "same maestro" in directory

    async def test_maestro_directory_lists_other_maestros(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.register_maestro("ops")

        directory = mgr._peer_directory_for("dev")
        assert "ops" in directory

    async def test_directory_empty_when_alone(self, tmp_path):
        mgr = await build_test_manager(tmp_path)
        await mgr.register_maestro("dev")
        await mgr.create_team("dev", "backend")
        await mgr.spawn_worker("dev.backend", worker_name="w1")

        directory = mgr._peer_directory_for("dev.backend.w1")
        # No other workers — should still mention parent, not bomb out.
        assert "dev.backend" in directory
```

- [ ] **Step 7.2: Run to verify failure**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerDirectory -v`

Expected: AttributeError — `_peer_directory_for` not defined.

- [ ] **Step 7.3: Implement `_peer_directory_for`**

In `src/hive/process/manager.py`, add this method on `ProcessManager` (place it near `_audit` / `_persist`, around line 175):

```python
    def _peer_directory_for(self, entity_name: str) -> str:
        """Build a 'peers you can message' block for an entity's prompt.

        Lists peers grouped by reach (same-parent direct, cross-parent
        with CC) plus the entity's direct parent for request_decision.
        Returns empty string if the entity is unknown.
        """
        entity = self._entities.get(entity_name)
        if entity is None:
            return ""

        same_parent: list[str] = []
        cross_parent: list[str] = []
        parent: str | None = None
        scope_label = ""

        if entity.role == "maestro":
            for name, e in self._entities.items():
                if e.role == "maestro" and name != entity_name:
                    same_parent.append(f"{name} (peer maestro — direct)")
            scope_label = "maestro peer-to-peer"
        elif entity.role == "lead":
            sender_maestro = entity_name.split(".")[0]
            parent = sender_maestro
            for name, e in self._entities.items():
                if e.role != "lead" or name == entity_name:
                    continue
                their_maestro = name.split(".")[0]
                if their_maestro == sender_maestro:
                    same_parent.append(f"{name} (same maestro — direct)")
                else:
                    cross_parent.append(f"{name} (cross-maestro — both maestros CC'd)")
            scope_label = "lead peer-to-peer"
        elif entity.role == "worker":
            sender_lead = ".".join(entity_name.split(".")[:-1])
            sender_maestro = entity_name.split(".")[0]
            parent = sender_lead
            for name, e in self._entities.items():
                if e.role != "worker" or name == entity_name:
                    continue
                their_lead = ".".join(name.split(".")[:-1])
                their_maestro = name.split(".")[0]
                if their_lead == sender_lead:
                    same_parent.append(f"{name} (same team — direct)")
                elif their_maestro == sender_maestro:
                    cross_parent.append(f"{name} (cross-team — both leads CC'd)")
                # cross-maestro workers: deliberately omitted (not allowed)
            scope_label = "worker peer-to-peer"

        lines = [f"## Peers you can message ({scope_label})"]
        if same_parent:
            lines.extend(f"- {p}" for p in sorted(same_parent))
        if cross_parent:
            lines.extend(f"- {p}" for p in sorted(cross_parent))
        if not same_parent and not cross_parent:
            lines.append("- (none registered yet)")

        if parent:
            lines.append("")
            lines.append("## Direct parent (use request_decision for escalations)")
            lines.append(f"- {parent}")

        return "\n".join(lines)
```

- [ ] **Step 7.4: Run directory tests to verify pass**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py::TestPeerDirectory -v`

Expected: 4 PASS.

- [ ] **Step 7.5: Inject directory in `send_to_entity`**

In `src/hive/process/manager.py`, in `send_to_entity` (around lines 440–482, where `prepended_blocks` is built), add the directory as one of the prepended blocks. Insert after line 440 (`prepended_blocks: list[str] = []`):

```python
        directory_block = self._peer_directory_for(entity_name)
        if directory_block:
            prepended_blocks.append(directory_block)
```

This places the peer directory at the head of every prompt sent to the entity, so the agent always knows who it can DM.

- [ ] **Step 7.6: Add an integration test that the prompt contains the directory**

Append to `tests/test_peer_messaging.py`:

```python
@pytest.mark.asyncio
async def test_send_to_entity_prepends_peer_directory(monkeypatch, tmp_path):
    mgr = await build_test_manager(tmp_path)
    await mgr.register_maestro("dev")
    await mgr.create_team("dev", "backend")
    await mgr.spawn_worker("dev.backend", worker_name="w1")
    await mgr.spawn_worker("dev.backend", worker_name="w2")

    captured: dict[str, str] = {}

    class FakeSession:
        session_id = None
        last_usage = None

        def __init__(self, args, cwd=None):
            self.is_alive = True

        async def start(self):
            return None

        async def send_prompt(self, prompt):
            captured["prompt"] = prompt
            return ""

        async def kill(self):
            self.is_alive = False

    monkeypatch.setattr("hive.process.manager.ClaudeSession", FakeSession)

    await mgr.send_to_entity("dev.backend.w1", "do work")

    assert "dev.backend.w2" in captured["prompt"]
    assert "Peers you can message" in captured["prompt"]
```

- [ ] **Step 7.7: Run all peer-messaging tests**

Run: `cd /home/hezki/projects/hive && pytest tests/test_peer_messaging.py -v`

Expected: every test in the file PASSES.

- [ ] **Step 7.8: Run full regression**

Run: `cd /home/hezki/projects/hive && pytest tests/ -x`

Expected: full suite passes. Triage and fix any cascading failures (most likely candidates: `tests/test_process_manager.py` if it asserts on the old `message.autonomous` audit name; `tests/test_scheduler.py` if it asserts that no extra blocks are prepended; `tests/test_router.py` if it counts pending messages).

- [ ] **Step 7.9: Commit**

```bash
git add src/hive/process/manager.py tests/test_peer_messaging.py
git commit -m "feat(manager): inject peer directory into entity spawn prompts"
```

---

## Task 8: Update PROJECT_PLAN.md and DEPLOYMENT.md

**Files:**
- Modify: `docs/PROJECT_PLAN.md`
- Modify: `docs/DEPLOYMENT.md` (only if needed)

- [ ] **Step 8.1: Open PROJECT_PLAN.md and add a sprint entry**

Read `docs/PROJECT_PLAN.md`. Add a new sprint entry following the established format (date, sprint number, summary, files changed, audit-log event names introduced). Reference: this plan's date `2026-05-04`, feature name `peer-messaging`, audit events `peer_message_sent`, `peer_message_cc_inserted`, `peer_message_blocked`, `request_decision_sent`, `request_decision_blocked`.

- [ ] **Step 8.2: Confirm DEPLOYMENT.md needs no change**

Read `docs/DEPLOYMENT.md`. This sprint introduces no new env vars, migrations, or infra changes. If you confirm none, skip the edit. If something does need a doc update (e.g. a feature flag for staged rollout), add a section.

- [ ] **Step 8.3: Commit**

```bash
git add docs/PROJECT_PLAN.md docs/DEPLOYMENT.md  # only files actually changed
git commit -m "docs: log peer-messaging sprint in PROJECT_PLAN"
```

---

## Task 9: Manual end-to-end test via Telegram

- [ ] **Step 9.1: Push branch and restart service**

```bash
cd /home/hezki/projects/hive
git push -u origin feat/peer-messaging
# Open a PR review in your usual flow before merging to main, OR if you
# trust the test suite + plan, fast-forward merge:
# git checkout main && git merge --ff-only feat/peer-messaging && git push
systemctl --user restart hive.service
```

- [ ] **Step 9.2: Watch service logs in another shell**

```bash
journalctl --user -u hive.service -f
```

- [ ] **Step 9.3: Drive the scenario via Telegram**

Send these messages to `hive_maestro` from your Telegram chat:

1. `/m:hive_maestro spawn 2 teams: alpha and beta, each with 2 workers`
2. Wait for spawn confirmation in chat.
3. `/m:hive_maestro tell hive_maestro.alpha.w1 to send a status message to hive_maestro.beta.w1: "have you finished the schema?"`

(Adjust entity names if hive_maestro uses a different naming convention.)

- [ ] **Step 9.4: Inspect audit log**

```bash
psql "$HIVE_DATABASE_URL" -c "SELECT created_at, actor, action, target, details FROM audit_log ORDER BY created_at DESC LIMIT 20;"
```

Expected entries (most recent first):
- `peer_message_cc_inserted` (×2 — both leads)
- `peer_message_sent` (direct)

- [ ] **Step 9.5: Confirm CC delivery**

The next time `hive_maestro.alpha` or `hive_maestro.beta` is spawned (e.g. by a follow-up message or scheduler tick), confirm via logs that the CC body `[CC: <sender> -> <recipient>] ...` appears in the prepended pending-messages section of its prompt.

- [ ] **Step 9.6: Smoke-test request_decision**

Send: `/m:hive_maestro tell hive_maestro.alpha.w1 to issue a request_decision to its lead asking whether to use library X or Y.`

In the audit log, expect: `request_decision_sent` (target = `hive_maestro.alpha`).

If you see `request_decision_blocked`, the worker tried to bypass the lead (e.g. addressed the maestro directly). That's the gate working — but it also means the agent didn't follow the prompt. Try again with explicit guidance.

---

## Out of scope (deliberate YAGNI — do not implement)

- Mailbox UI / dashboard for inspecting peer message queues
- Message threading / `reply_to` semantics
- Broadcast (one-to-many) — multiple individual `send_message` calls instead
- Shared task list / claim mechanic (CC Teams has this; we picked CC over shared state)
- Cross-maestro worker direct messaging (must go through maestro chain)
- Persisting peer message bodies beyond the existing audit log + message store
