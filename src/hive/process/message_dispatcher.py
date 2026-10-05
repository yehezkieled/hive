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

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from hive.bus.actions import Action, neutralize_action_tags, parse_actions
from hive.models.entity import Entity
from hive.runtime.harness import NoUsableHarnessError

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

# At most one fleet-wide "no usable harness" alert per this many seconds: a dozen
# entities all failing the same turn window must not spam Telegram.
_NO_HARNESS_ALERT_INTERVAL_S = 600.0

# Parse-failure feedback loop. When an entity's <hive_actions> block
# is malformed, the orchestrator routes a system->entity message with
# the error so the sender can retry. To avoid feedback->bad-retry
# loops chewing tokens silently, we cap retries to 3 per 5 minutes
# per entity. Beyond the cap we stop sending feedback and escalate
# one notification to the user so a human can intervene.
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
        # Last "harness (mode)" label each entity ran on — a notification fires
        # only when it CHANGES (default calm: steady state is silent).
        self._last_run: dict[str, str] = {}
        # Monotonic time of the last "no usable harness" alert (fleet-wide dedup).
        self._no_harness_alerted_at: float | None = None

    async def _note_run(self, entity_name: str, usage: dict) -> None:
        """Surface which harness + mode served the turn when it changed (ADR 0029)."""
        harness, mode = usage.get("harness"), usage.get("mode")
        if not harness:
            return
        label = f"{harness} ({mode})"
        previous = self._last_run.get(entity_name)
        self._last_run[entity_name] = label
        fell_back = usage.get("fell_back") or []
        unfenced = usage.get("unfenced")
        if previous == label:
            return
        if previous is None and not fell_back and not unfenced:
            return
        text = f"{entity_name} is now running on {label}"
        if fell_back:
            text += f" — fell back from: {'; '.join(fell_back)}"
        if unfenced:
            text += f"\n⚠️ {entity_name}'s {unfenced} is NOT enforced on {harness}."
        await self._mgr._notify(
            text,
            kind="harness_run",
            data={
                "entity": entity_name,
                "harness": harness,
                "mode": mode,
                "fell_back": fell_back,
                "unfenced": unfenced,
            },
        )

    async def _notify_no_harness(self, entity_name: str, err: NoUsableHarnessError) -> None:
        now = time.monotonic()
        last = self._no_harness_alerted_at
        if last is not None and now - last < _NO_HARNESS_ALERT_INTERVAL_S:
            return
        self._no_harness_alerted_at = now
        await self._mgr._notify(str(err), kind="harness_unavailable", data={"entity": entity_name})

    async def send_to_entity(
        self, entity_name: str, prompt: str, *, seed_goal: bool = False
    ) -> str:
        """Send a prompt to an entity and get the response.

        Each call spawns a fresh subprocess. If the entity has a stored
        session_id from a previous call, ``--resume`` is passed so the
        Claude CLI resumes the prior conversation context.

        Pending inter-agent messages are prepended to the prompt.
        After the response, any ``<hive_actions>`` block is parsed and
        routed to the appropriate recipients.

        ``seed_goal`` (T007): when this is the entity's first turn *and* the
        caller marks it a genuine task delivery, the fully-assembled prompt is
        wrapped as Claude Code's native ``/goal <completion condition>``. Only
        the user/command task entrypoint sets this; internal machine sends —
        scheduler pokes, peer mail, compact reseeds — leave it ``False`` so
        they never redefine the entity's loop goal (this is a shared
        chokepoint, so "first turn" alone does not mean "spawn task").
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

        # T007: seed Claude Code's native /goal on the entity's first *task*
        # turn, replacing the retired LOOP_PROMPTS framework. The slash command
        # must lead the message, so this wraps the fully-assembled prompt. It
        # fires only when the caller marked this a genuine task delivery
        # (seed_goal) AND it is the first turn of the activation (session_id
        # still None) — never on a poke, peer poke, or compact reseed that
        # merely happens to be the first send.
        if seed_goal and is_first_turn and prompt.strip():
            from hive.process.loops import seed_goal as _seed_goal

            prompt = _seed_goal(prompt)

        if _mgr_mod.mcp_servers_enabled():
            _mgr_mod.generate_mcp_config(entity.name, entity.mcp_config_path)

        adapter = await self._mgr._get_or_create_adapter(entity)
        # --- Ticket 020: auto-bounce a jammed session ---
        # The 180s no-progress timeout surfaces here as a TimeoutError. The
        # manager decides whether this is a genuine stall (kill + respawn,
        # conversation preserved via --continue, then retry once on the fresh
        # adapter) or a legitimate wait / sub-threshold blip that should
        # propagate unchanged. A successful turn — first try or retry — resets
        # the consecutive-stall count.
        try:
            try:
                response, usage = await adapter.send_turn(prompt)
            except TimeoutError:
                if not await self._mgr._maybe_bounce_on_timeout(entity, adapter):
                    raise
                adapter = await self._mgr._get_or_create_adapter(entity)
                response, usage = await adapter.send_turn(prompt)
        except NoUsableHarnessError as e:
            # Claude Code logged out, Pi unconfigured, ...: say so loudly in the
            # notification channels too — a scheduler poke has no reply to carry it.
            await self._notify_no_harness(entity_name, e)
            raise
        await self._note_run(entity_name, usage)
        self._mgr._note_turn_success(entity_name)
        await self._mgr._record_usage(entity, usage)

        if "codex_usage" in usage:
            entity.codex_usage = usage["codex_usage"]

        # Store session_id for resume on next call
        if usage.get("session_id"):
            entity.session_id = usage["session_id"]
            await self._mgr._persist(entity)

        context_tokens = usage.get("context_tokens")

        # Auto-compact if context is too large
        if (
            _mgr_mod.AUTO_COMPACT_ENABLED
            and entity_name not in self._mgr._compacting
            and context_tokens is not None
            and context_tokens > _mgr_mod.AUTO_COMPACT_THRESHOLD
        ):
            input_tokens = context_tokens
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

        # --- Phase 3: parse and route actions from response ---
        clean_text, actions, parse_errors = parse_actions(response)
        result = await self._mgr._handle_actions(
            entity_name, clean_text, actions, parse_errors=parse_errors
        )

        # --- Turn-end inbox check (Ticket 023, design D4) ---
        # Wake-on-inbound is single-shot: a wake landing while this turn
        # was in flight was swallowed and nothing retries — the mail would
        # park until the next wake. The drain phase above ran at
        # turn START, so anything still queued now arrived DURING the turn.
        # Runs on every completion path — a turn that parked at an
        # interactive gate and resumed returns through this same line.
        # Budget-exhausted recipients are throttled by the scheduler (no
        # spin); the next wake remains the backstop.
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
        self._mgr._last_vault_requests = []
        for action in actions:
            if action.type == "request_mode_change":
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
           message and notify the user once. This breaks the loop
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
            escalation_msg = neutralize_action_tags(
                f"{entity.name} has emitted {len(window)} malformed "
                f"<hive_actions> blocks in the last "
                f"{_PARSE_FAILURE_WINDOW_SECONDS // 60} min. "
                "Suppressing parse-feedback to avoid a loop. "
                "Please intervene — kill, reset, or guide the entity. "
                f"Latest errors:\n" + "\n".join(f"- {err}" for err in parse_errors)
            )
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
                    "escalated_to": "user",
                },
            )
            logger.warning(
                "Parse-failure cap hit for %s (%d in window) — escalated to user",
                entity.name,
                len(window),
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
