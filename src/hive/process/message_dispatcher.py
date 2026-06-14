"""Message dispatcher — outbound sends and inbound action routing lifted
out of ProcessManager.

Collaborator object (Ticket 004): holds a back-reference to the owning
ProcessManager (``self._mgr``) and reaches all shared state and sibling
methods through it. It imports nothing from ``manager.py`` at runtime; the
manager type hint is under ``TYPE_CHECKING`` only.

The single most fragile seam in the whole split lives here:
``_handle_actions`` resets the eight ``_last_*`` introspection lists by
**rebinding** (``self._mgr._last_routed_actions = []``), not ``.clear()``.
Those attributes are facade-owned, so the rebind MUST go through
``self._mgr`` — a local rebind would leave the facade attribute the tests
read stale and silently break every ``_last_*`` assertion.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from hive.bus.actions import Action, neutralize_action_tags, parse_actions
from hive.bus.permissions import (
    can_kill,
    can_message,
    can_request_decision,
    can_spawn_team,
    cc_targets_for,
)
from hive.config import DEFAULT_MAESTRO
from hive.models.entity import Entity

if TYPE_CHECKING:
    from hive.process.manager import ProcessManager

# ``send_to_entity`` reads the auto-retrieve / auto-compact / advisor config
# flags and ``generate_mcp_config`` through the ``hive.process.manager``
# module namespace (resolved lazily at call time, see ``_manager_module``)
# rather than binding them at this module's load. The flags are re-exported
# from ``manager.py``; existing tests patch them as
# ``hive.process.manager.AUTO_COMPACT_ENABLED`` etc., and resolving through
# that module is what makes the patch take effect on the moved code. The
# import is function-scoped, so it never creates a load-time cycle
# (``manager.py`` already imports this module at load).

logger = logging.getLogger(__name__)

# Parse-failure feedback loop. When an entity's <hive_actions> block
# is malformed, the orchestrator routes a system->entity message with
# the error so the sender can retry. To avoid feedback->bad-retry
# loops chewing tokens silently, we cap retries to 3 per 5 minutes
# per entity. Beyond the cap we stop sending feedback and escalate
# one notification to the entity's parent (lead->maestro,
# maestro->user) so a human can intervene.
_PARSE_FAILURE_WINDOW_SECONDS = 300
_PARSE_FAILURE_MAX_PER_WINDOW = 3


def _manager_module():
    """Return the ``hive.process.manager`` module, imported lazily.

    Config flags and ``generate_mcp_config`` are read off this module at
    call time so ``patch("hive.process.manager.X")`` (used by existing
    tests) affects the moved ``send_to_entity`` code. The import is
    function-scoped to avoid a load-time cycle.
    """
    from hive.process import manager

    return manager


class MessageDispatcher:
    """Outbound sends and inbound ``<hive_actions>`` routing.

    One responsibility cluster lifted out of ProcessManager. All shared
    state lives on the facade and is reached via ``self._mgr``.
    """

    def __init__(self, mgr: ProcessManager) -> None:
        self._mgr = mgr

    async def send_to_entity(self, entity_name: str, prompt: str) -> str:
        """Send a prompt to an entity and get the response.

        Each call spawns a fresh subprocess. If the entity has a stored
        session_id from a previous call, ``--resume`` is passed so the
        Claude CLI resumes the prior conversation context.

        Pending inter-agent messages are prepended to the prompt.
        After the response, any ``<hive_actions>`` block is parsed and
        routed to the appropriate recipients.
        """
        # Read config flags + generate_mcp_config through the manager module
        # so tests patching ``hive.process.manager.X`` affect this code.
        _mgr_mod = _manager_module()

        entity = self._mgr._entities.get(entity_name)
        if entity is None:
            raise KeyError(f"Entity {entity_name!r} not found.")

        # --- Ticket 028: pending-gate guard ---
        # If a Turn is parked on an interactive gate, the PTY is sitting on a
        # TUI menu — typing a new-turn prompt into it submits the highlighted
        # default (the gate's "answer"). Refuse to inject from this shared
        # chokepoint (scheduler poke / peer mail / user text / eval all flow
        # here), BEFORE draining the inbox so queued peer mail survives and is
        # re-delivered after the gate resolves. Gate answers take a separate,
        # menu-aware path (ring → resolve → inject keys) and are unaffected.
        if self._mgr.is_parked_at_gate(entity_name):
            request_id = self._mgr.gate_coordinator.pending_request_id(entity_name)
            logger.info(
                "send_to_entity: %s parked at gate %s — not injecting", entity_name, request_id
            )
            return (
                f"<{entity_name} is parked at gate {request_id}; answer it with "
                f"/approve gate {request_id} or /deny gate {request_id} before sending more>"
            )

        # Track activity for idle-kill detection
        entity.last_activity_at = datetime.now(UTC)

        # --- Phase 2: drain pending inter-agent messages ---
        pending: list[str] = []
        while self._mgr.router.has_pending(entity_name):
            msg = await self._mgr.router.get_next(entity_name, timeout=0.1)
            if msg:
                pending.append(f"[Message from {msg.sender}]: {msg.content}")
        if pending:
            inbox = "\n".join(pending)
            prompt = f"You have pending messages from other entities:\n{inbox}\n\n---\n\n{prompt}"

        # --- Sprint 11: auto-retrieve top-K blueprints as context ---
        # --- Sprint 18: also pull semantically-related uploaded files. ---
        # --- Sprint 27: dialed down — top_k=1, first-turn only. Smarter
        #     agents call the ``search_knowledge`` MCP tool when they need
        #     more or different context.
        prepended_blocks: list[str] = []
        directory_block = self._mgr._peer_directory_for(entity_name)
        if directory_block:
            prepended_blocks.append(directory_block)

        # ``session_id`` is set after the first prompt of an activation, so
        # ``is None`` is the cheapest signal for "first turn this session."
        is_first_turn = entity.session_id is None
        auto_retrieve_active = (
            _mgr_mod.AUTO_RETRIEVE_ENABLED
            and prompt.strip()
            and (is_first_turn or not _mgr_mod.AUTO_RETRIEVE_FIRST_TURN_ONLY)
        )

        if auto_retrieve_active:
            knowledge_blocks: list[str] = []

            if self._mgr.blueprint_store is not None:
                try:
                    blueprint_hits = await self._mgr.blueprint_store.search(
                        prompt,
                        limit=_mgr_mod.AUTO_RETRIEVE_TOP_K,
                        max_distance=_mgr_mod.AUTO_RETRIEVE_MAX_DISTANCE,
                    )
                except Exception:
                    logger.exception("auto-retrieve failed; continuing without blueprints")
                    blueprint_hits = []
                if blueprint_hits:
                    bp_lines = ["Relevant past blueprints (retrieved automatically):"]
                    for h in blueprint_hits:
                        # Sprint 26: render the matching chunk only, not the
                        # full body — sharper context, less prompt bloat.
                        bp_lines.append(f"\n### {h['title']}\n{h['chunk_text']}")
                    knowledge_blocks.append("\n".join(bp_lines))

            if (
                _mgr_mod.AUTO_RETRIEVE_INCLUDE_ATTACHMENTS
                and self._mgr.attachment_store is not None
            ):
                try:
                    attachment_hits = await self._mgr.attachment_store.search(
                        prompt,
                        limit=_mgr_mod.AUTO_RETRIEVE_TOP_K,
                        max_distance=_mgr_mod.AUTO_RETRIEVE_MAX_DISTANCE,
                    )
                except Exception:
                    logger.exception("auto-retrieve failed; continuing without attachments")
                    attachment_hits = []
                if attachment_hits:
                    file_lines = ["Relevant uploaded files (retrieved automatically):"]
                    for h in attachment_hits:
                        # Sprint 28: render the matching chunk text instead
                        # of a 200-char prefix of the whole embed_text.
                        chunk = (h.get("chunk_text") or "").replace("\n", " ").strip()
                        name = h.get("original_name") or h["file_path"]
                        mime = h.get("mime_type") or "unknown"
                        file_lines.append(
                            f"- {h['file_path']} ({mime}, original: {name})"
                            + (f' — snippet: "{chunk}"' if chunk else "")
                        )
                    knowledge_blocks.append("\n".join(file_lines))

            if knowledge_blocks:
                # Sprint 27: nudge the agent toward search_knowledge for
                # mid-conversation lookups when the auto-block doesn't match.
                knowledge_blocks.append(
                    "(Need different context? Call the `search_knowledge` "
                    "MCP tool with your own query.)"
                )
                prepended_blocks.extend(knowledge_blocks)

        if prepended_blocks:
            context_block = "\n\n---\n\n".join(prepended_blocks)
            prompt = f"{context_block}\n\n---\n\n{prompt}"

        if _mgr_mod.mcp_servers_enabled():
            _mgr_mod.generate_mcp_config(entity.name, entity.mcp_config_path)

        adapter = await self._mgr._get_or_create_adapter(entity)
        response, usage = await adapter.send_turn(prompt)
        await self._mgr._record_usage(entity, usage)

        # Auto-compact if context is too large
        if (
            _mgr_mod.AUTO_COMPACT_ENABLED
            and entity_name not in self._mgr._compacting
            and usage.get("input_tokens", 0) > _mgr_mod.AUTO_COMPACT_THRESHOLD
        ):
            input_tokens = usage["input_tokens"]
            logger.info(
                "Auto-compacting %s (input_tokens=%d > threshold=%d)",
                entity_name,
                input_tokens,
                _mgr_mod.AUTO_COMPACT_THRESHOLD,
            )
            self._mgr._compacting.add(entity_name)
            try:
                await self._mgr.compact_entity(entity_name)
                await self._mgr._notify(
                    f"Auto-compacted {entity_name} (context: {input_tokens:,} tokens)"
                )
                await self._mgr._audit(
                    "entity.auto_compact",
                    target=entity_name,
                    details={"input_tokens": input_tokens},
                )
            except Exception:
                logger.exception("Auto-compact failed for %s", entity_name)
            finally:
                self._mgr._compacting.discard(entity_name)

        # Store session_id for resume on next call
        if usage.get("session_id"):
            entity.session_id = usage["session_id"]
            await self._mgr._persist(entity)

        # --- Phase 3: parse and route actions from response ---
        clean_text, actions, parse_errors = parse_actions(response)
        result = await self._mgr._handle_actions(
            entity_name, clean_text, actions, parse_errors=parse_errors
        )

        # --- Turn-end inbox check (Ticket 023, design D4) ---
        # Wake-on-inbound is single-shot: a wake landing while this turn
        # was in flight was swallowed and nothing retries — the mail would
        # park until the 120m scheduler tick. The drain phase above ran at
        # turn START, so anything still queued now arrived DURING the turn.
        # Runs on every completion path — a turn that parked at an
        # interactive gate and resumed returns through this same line.
        # Budget-exhausted recipients are throttled by the scheduler (no
        # spin); the 120m tick remains the backstop.
        self._mgr.wake.schedule_wake_if_pending(entity_name)

        return result

    async def _handle_actions(
        self,
        entity_name: str,
        clean_text: str,
        actions: list[Action],
        *,
        parse_errors: list[str] | None = None,
    ) -> str:
        """Route parsed actions to the appropriate handlers.

        Extracted from ``send_to_entity`` so tests can drive the
        dispatch loop directly without going through a real Claude
        subprocess.

        ``parse_errors`` is the list returned by ``parse_actions`` when
        an <hive_actions> block was malformed. Each entry is a
        human-readable description (bad JSON, missing field, unknown
        type). When non-empty, after action dispatch we either route a
        ``system -> entity`` feedback message so the sender can retry,
        or, if the entity has hit ``_PARSE_FAILURE_MAX_PER_WINDOW`` in
        the rolling window, escalate to the parent and stop sending
        feedback.
        """
        entity = self._mgr._entities.get(entity_name)
        if entity is None:
            return clean_text

        self._mgr._last_routed_actions = []
        self._mgr._last_mode_requests = []
        self._mgr._last_failure_reports = []
        self._mgr._last_spawned_teams = []
        self._mgr._last_killed_entities = []
        self._mgr._last_vault_requests = []
        self._mgr._last_kickoffs = []
        pending_kickoffs: list[str] = []
        for action in actions:
            if action.type == "message":
                requested_to = action.to or ""
                recipient_name = self._resolve_message_alias(entity, requested_to)
                recipient = self._mgr._entities.get(recipient_name) if recipient_name else None
                if not recipient:
                    logger.warning("Unknown recipient: %s", requested_to)
                    await self._reject_action(
                        entity,
                        "message",
                        requested_to,
                        f"unknown recipient {recipient_name or requested_to!r}. "
                        + self._addressing_hint(entity),
                    )
                    continue
                if not can_message(entity.role, entity.name, recipient.role, recipient.name):
                    logger.warning("Permission denied: %s -> %s", entity.name, recipient_name)
                    if recipient.name == entity.name:
                        # The self-message ban (bus/permissions.py) caught an
                        # alias that resolved back to the sender — explain the
                        # resolution, not just the denial.
                        reason = (
                            f"{requested_to!r} resolves to yourself "
                            f"({entity.name}); self-messages are not allowed. "
                            + self._addressing_hint(entity)
                        )
                    else:
                        reason = (
                            f"permission denied: {entity.name} may not message "
                            f"{recipient_name!r}. " + self._addressing_hint(entity)
                        )
                    await self._reject_action(entity, "message", requested_to, reason)
                    continue
                body = action.text or ""
                await self._mgr.router.route(entity_name, recipient_name, body)
                self._mgr._last_routed_actions.append(recipient_name)
                await self._mgr._audit(
                    "peer_message_sent",
                    target=recipient_name,
                    details={"sender": entity_name, "text": body[:200]},
                    actor=entity_name,
                )
                cc_targets = cc_targets_for(
                    entity.role, entity.name, recipient.role, recipient.name
                )
                cc_body = f"[CC: {entity.name} -> {recipient_name}] {body}"
                for cc_name in cc_targets:
                    if cc_name not in self._mgr._entities:
                        continue
                    await self._mgr.router.route(entity_name, cc_name, cc_body)
                    await self._mgr._audit(
                        "peer_message_cc_inserted",
                        target=cc_name,
                        details={
                            "sender": entity_name,
                            "recipient": recipient_name,
                            "text": body[:200],
                        },
                        actor=entity_name,
                    )
            elif action.type == "request_decision":
                if not action.to:
                    continue
                # Permission first — covers both the user target (maestro→user,
                # Ticket 029) and the lead→own-maestro escalation.
                if not can_request_decision(entity.role, entity.name, action.to):
                    logger.warning("request_decision denied: %s -> %s", entity.name, action.to)
                    await self._mgr._audit(
                        "request_decision_blocked",
                        target=action.to,
                        details={"sender": entity_name, "reason": "permission_denied"},
                        actor=entity_name,
                    )
                    await self._reject_action(
                        entity,
                        "request_decision",
                        action.to,
                        f"request_decision to {action.to!r} is not permitted for your role.",
                    )
                    continue
                if action.to == "user":
                    # Ticket 029 (ADR 0018): the conversational decision channel.
                    # Deliver the question to the user (Telegram) via the
                    # notification path, park the maestro on the durable
                    # awaiting_decision flag so the scheduler won't poke it into
                    # acting, and END THE TURN — break so any trailing actions in
                    # the same block are dropped (no ask-then-act).
                    if self._mgr.notification_dispatcher is None:
                        # No path to the user — do NOT claim delivery (the
                        # maestro must not narrate fictional success), and do NOT
                        # park (it would wait forever for a reply that can't be
                        # prompted).
                        logger.warning(
                            "request_decision to user from %s: no notification path", entity_name
                        )
                        await self._reject_action(
                            entity,
                            "request_decision",
                            "user",
                            "no notification path to the user is configured — your "
                            "decision request was not delivered.",
                        )
                        continue
                    text = action.text or ""
                    await self._mgr._notify(
                        f"[decision needed] {text}",
                        kind="decision_request",
                        data={"entity": entity_name},
                    )
                    entity.awaiting_decision = True
                    await self._mgr._persist(entity)
                    self._mgr._last_routed_actions.append("user")
                    await self._mgr._audit(
                        "request_decision_sent",
                        target="user",
                        details={"sender": entity_name, "text": text[:200]},
                        actor=entity_name,
                    )
                    break
                # Non-user target: lead→maestro escalation, routed as peer mail.
                recipient = self._mgr._entities.get(action.to)
                if not recipient:
                    logger.warning("Unknown request_decision recipient: %s", action.to)
                    await self._reject_action(
                        entity,
                        "request_decision",
                        action.to,
                        f"unknown recipient {action.to!r}.",
                    )
                    continue
                body = f"[DECISION REQUEST] {action.text or ''}"
                await self._mgr.router.route(entity_name, action.to, body)
                self._mgr._last_routed_actions.append(action.to)
                await self._mgr._audit(
                    "request_decision_sent",
                    target=action.to,
                    details={"sender": entity_name, "text": (action.text or "")[:200]},
                    actor=entity_name,
                )
            elif action.type == "request_mode_change":
                if not action.requested_mode:
                    continue
                try:
                    req_id = await self._mgr.request_mode_change(
                        entity_name,
                        action.requested_mode,
                        reason=action.reason,
                    )
                    self._mgr._last_mode_requests.append(req_id)
                except (KeyError, ValueError) as exc:
                    logger.warning("request_mode_change from %s failed: %s", entity_name, exc)
            elif action.type == "request_payment":
                try:
                    action_id = await self._mgr.request_payment(
                        entity_name,
                        amount_cents=action.amount_cents or 0,
                        currency=action.currency or "USD",
                        recipient=action.recipient or "",
                        idempotency_key=action.idempotency_key or "",
                        reason=action.reason or "",
                    )
                    if action_id is not None:
                        self._mgr._last_vault_requests.append(action_id)
                except (KeyError, ValueError, PermissionError) as exc:
                    logger.warning("request_payment from %s rejected: %s", entity_name, exc)
            elif action.type == "report_failure":
                reason = action.reason or "(no reason given)"
                task_id = action.task_id
                if task_id is None:
                    logger.warning(
                        "report_failure from %s with no task_id",
                        entity_name,
                    )
                    continue
                try:
                    await self._mgr.handle_task_failure(task_id, reason)
                    self._mgr._last_failure_reports.append(task_id)
                except Exception:
                    logger.exception("handle_task_failure failed for task %s", task_id)
            elif action.type == "spawn_team":
                if not action.team_name:
                    continue
                if not can_spawn_team(entity.role, entity.name):
                    logger.warning("spawn_team denied: %s (role=%s)", entity.name, entity.role)
                    await self._mgr._audit(
                        "entity.spawn_team_denied",
                        target=action.team_name,
                        details={"reason": "role_not_permitted", "role": entity.role},
                        actor=entity_name,
                    )
                    continue
                if self._mgr.scheduler is not None and not self._mgr.scheduler.can_autospawn(
                    entity_name
                ):
                    logger.warning("spawn_team rate-limited: %s", entity_name)
                    await self._mgr._audit(
                        "entity.spawn_rate_limited",
                        target=action.team_name,
                        details={
                            "action_type": "spawn_team",
                            "limit": self._mgr.scheduler.spawn_limit,
                        },
                        actor=entity_name,
                    )
                    continue
                try:
                    lead = await self._mgr.create_team(
                        entity_name,
                        action.team_name,
                        # Opus is the fleet default for every spawn (the Opus
                        # advisor that Sonnet leads relied on is unavailable —
                        # Ticket 013 post-mortem). A maestro may still pin a
                        # cheaper model explicitly via the action.
                        model=action.model or "opus",
                        display_name=action.display_name,
                        personality=action.personality,
                    )
                    self._mgr._last_spawned_teams.append(lead.name)
                    if self._mgr.scheduler is not None:
                        self._mgr.scheduler.record_autospawn(entity_name)
                    await self._mgr._audit(
                        "entity.spawn_team",
                        target=lead.name,
                        details={"team": action.team_name, "maestro": entity_name},
                        actor=entity_name,
                    )
                    pending_kickoffs.append(lead.name)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("spawn_team from %s failed: %s", entity_name, exc)
            elif action.type == "kill_entity":
                if not action.target:
                    continue
                if not can_kill(entity.role, entity.name, action.target, DEFAULT_MAESTRO):
                    logger.warning("kill_entity denied: %s -> %s", entity.name, action.target)
                    await self._mgr._audit(
                        "entity.kill_denied",
                        target=action.target,
                        details={"reason": "permission_denied", "role": entity.role},
                        actor=entity_name,
                    )
                    continue
                if action.target not in self._mgr._entities:
                    logger.warning("kill_entity target not found: %s", action.target)
                    continue
                try:
                    await self._mgr.kill_entity(action.target)
                    self._mgr._last_killed_entities.append(action.target)
                    await self._mgr._audit(
                        "entity.autonomous_kill",
                        target=action.target,
                        details={"actor_role": entity.role},
                        actor=entity_name,
                    )
                except Exception:
                    logger.exception("kill_entity from %s failed", entity_name)

        if pending_kickoffs:
            self._mgr._last_kickoffs = list(pending_kickoffs)
            for target in pending_kickoffs:
                task = asyncio.create_task(self._mgr._auto_kickoff(target))
                self._mgr._kickoff_tasks.add(task)
                task.add_done_callback(self._mgr._kickoff_tasks.discard)

        if parse_errors:
            await self._mgr._handle_parse_errors(entity, parse_errors)

        return clean_text

    async def _handle_parse_errors(self, entity: Entity, parse_errors: list[str]) -> None:
        """Route parse-error feedback to the sender, with overflow escalation.

        Two paths:
        1. Under cap: route a ``system -> entity`` message containing
           the human-readable parse errors. The wake-on-inbound hook
           auto-spawns the entity, the drain phase prepends the message
           to its next prompt, and it can retry with corrected JSON.
        2. At cap (>= ``_PARSE_FAILURE_MAX_PER_WINDOW`` in
           ``_PARSE_FAILURE_WINDOW_SECONDS``): suppress the feedback
           message and notify the parent (lead -> maestro,
           maestro -> user) once. This breaks the loop
           when a model is stuck producing the same malformed output.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=_PARSE_FAILURE_WINDOW_SECONDS)
        window = self._mgr._parse_failure_budget[entity.name]
        while window and window[0] < cutoff:
            window.popleft()
        window.append(now)

        # Tag names rendered with spaces (`< hive_actions >`) so this
        # feedback cannot be re-parsed when the entity's terminal
        # screen-echoes it back into the next turn's prompt — the
        # every-2h self-feedback loop in prod. See
        # ``neutralize_action_tags`` for the rationale.
        feedback_body = neutralize_action_tags(
            "Your last response contained a malformed <hive_actions> "
            "block. The orchestrator could not parse it, so the actions "
            "did NOT execute. Errors:\n"
            + "\n".join(f"- {err}" for err in parse_errors)
            + "\n\nFix the JSON and resend the actions in a new "
            "<hive_actions> block. Common causes: unescaped newlines/"
            "quotes inside multi-line `personality` strings (use \\n "
            'and \\"), wrong closing tag (must be </hive_actions>, not '
            "</invoke>), or missing required fields. (Tag names above "
            "are shown with spaces — emit them without spaces, exactly "
            "as in the protocol spec.)"
        )

        if len(window) > _PARSE_FAILURE_MAX_PER_WINDOW:
            # Cap exceeded — escalate once, drop the feedback message
            # so we don't keep waking a stuck entity.
            parent = self._mgr._parent_of(entity)
            escalation_msg = neutralize_action_tags(
                f"{entity.name} has emitted {len(window)} malformed "
                f"<hive_actions> blocks in the last "
                f"{_PARSE_FAILURE_WINDOW_SECONDS // 60} min. "
                "Suppressing parse-feedback to avoid a loop. "
                "Please intervene — kill, reset, or guide the entity. "
                f"Latest errors:\n" + "\n".join(f"- {err}" for err in parse_errors)
            )
            if parent and parent in self._mgr._entities:
                await self._mgr.router.route("system", parent, escalation_msg)
            else:
                # Maestro (or detached entity) — surface to the user.
                await self._mgr._notify(
                    escalation_msg,
                    kind="warning",
                    data={"entity": entity.name, "kind": "parse_failure_cap"},
                )
            await self._mgr._audit(
                "entity.parse_failure_capped",
                target=entity.name,
                details={
                    "window_size": len(window),
                    "escalated_to": parent or "user",
                },
            )
            logger.warning(
                "Parse-failure cap hit for %s (%d in window) — escalated to %s",
                entity.name,
                len(window),
                parent or "user",
            )
            return

        # Under cap — send feedback so the sender can self-correct.
        await self._mgr.router.route("system", entity.name, feedback_body)
        await self._mgr._audit(
            "entity.parse_failure_feedback",
            target=entity.name,
            details={
                "window_size": len(window),
                "error_count": len(parse_errors),
            },
        )
        logger.warning(
            "Parse-failure feedback sent to %s (%d errors, window=%d)",
            entity.name,
            len(parse_errors),
            len(window),
        )

    def _resolve_message_alias(self, sender: Entity, to: str) -> str:
        """Resolve the ``maestro``/``parent`` addressing aliases.

        ``to:"maestro"`` resolves to the sender's org root (the first dotted
        segment); ``to:"parent"`` to its immediate parent (the sender's name
        minus the last segment), so an entity never has to remember — or
        invent — a dotted name (Ticket 023, design D2). An org root has no
        parent, so its ``to:"parent"`` resolves to the empty string and is
        rejected by the recipient lookup. Any other value passes through
        unchanged: no fuzzy matching, because a silent misdelivery is worse
        than a drop.
        """
        if to == "maestro":
            return sender.name.split(".")[0]
        if to == "parent":
            return ".".join(sender.name.split(".")[:-1])
        return to

    async def _reject_action(
        self,
        sender: Entity,
        action_type: str,
        attempted_to: str,
        reason: str,
    ) -> None:
        """Audit a rejected action and feed the failure back to the sender.

        Two halves (Ticket 023, design D2 — failure F2 was a silent drop):
        an ``action_rejected`` audit entry (actor = sender, target = the
        recipient as the sender wrote it), and a ``system -> sender`` note
        naming what failed and the correct form. The note lands in the
        sender's queue, wake-on-inbound delivers it, and the sender can
        self-correct next turn. Runaway correction loops are capped by the
        existing wake budget.
        """
        await self._mgr._audit(
            "action_rejected",
            target=attempted_to,
            details={
                "sender": sender.name,
                "action_type": action_type,
                "reason": reason,
            },
            actor=sender.name,
        )
        note = neutralize_action_tags(
            f"[action rejected] your {action_type} to {attempted_to!r} was not delivered: {reason}"
        )
        await self._mgr.router.route("system", sender.name, note)

    def _addressing_hint(self, sender: Entity) -> str:
        """One-line 'correct form' hint appended to rejection feedback."""
        parts = sender.name.split(".")
        if len(parts) == 1:
            return (
                "You are an org root: address entities in your org by "
                'their full dotted name (e.g. "yourname.team").'
            )
        parent = ".".join(parts[:-1])
        if sender.role == "lead":
            return f'Your maestro is {parent!r} — address it as to:"maestro" (no name needed).'
        return f'Your parent is {parent!r} — address it as to:"parent" (no name needed).'
