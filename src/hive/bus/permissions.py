"""Inter-agent messaging and lifecycle-action permission checks.

Messaging rules:
- Maestro can message any entity in its own org (shared name prefix)
- Lead can message its parent maestro and peer leads
- Cross-org messaging is denied

Lifecycle rules (Sprint 19 — autonomous spawn/kill; amended by ADR 0013):
- Maestro can spawn teams in its own org. It can kill anything in its
  own org except itself and the orchestrator-protected default maestro.
- Worker creation is retired for every actor (ADR 0013, Ticket 016) and
  the Worker entity deleted (Ticket 018): leaf work fans out through the
  Workflow tool instead.
- Lead can kill entities in its own team only.
"""

from __future__ import annotations


def can_message(
    sender_role: str,
    sender_name: str,
    recipient_role: str,
    recipient_name: str,
) -> bool:
    """Check whether sender is allowed to message recipient.

    Uses the dotted naming convention to determine org membership:
    dev.backend.w1 belongs to lead dev.backend, which belongs to maestro dev.

    Peer-to-peer rules (Sprint 22):
    - Maestros can message any other maestro.
    - Leads can message any other lead (cross-maestro routes are CC'd).
    """
    # Self-message is never allowed.
    if sender_name == recipient_name:
        return False

    sender_maestro = sender_name.split(".")[0]

    # ---- Peer-to-peer rules (same role on both ends) ----
    if sender_role == recipient_role:
        if sender_role == "maestro":
            return True
        if sender_role == "lead":
            return True

    # ---- Existing parent-child rules ----
    if sender_role == "maestro":
        return recipient_name.startswith(f"{sender_name}.")

    if sender_role == "lead":
        if recipient_name.startswith(f"{sender_name}."):
            return True
        return recipient_name == sender_maestro

    return False


def cc_targets_for(
    sender_role: str,
    sender_name: str,
    recipient_role: str,
    recipient_name: str,
) -> list[str]:
    """Return parent names that should be CC'd when sender messages recipient.

    Cross-parent peer messages get a CC to each peer's direct parent so
    the parent retains visibility. Same-parent peers and parent-child
    routes get no CC.
    """
    if sender_role != recipient_role:
        return []

    sender_maestro = sender_name.split(".")[0]
    recipient_maestro = recipient_name.split(".")[0]

    if sender_role == "maestro":
        return []

    if sender_role == "lead":
        if sender_maestro == recipient_maestro:
            return []
        return [sender_maestro, recipient_maestro]

    return []


def can_request_decision(
    sender_role: str,
    sender_name: str,
    target_name: str,
) -> bool:
    """Escalation gate for ``request_decision``.

    Leads can request_decision from their direct maestro. Maestros have no
    parent Entity, but escalate a decision to ``user`` — the top rung of the
    conversational decision channel (Ticket 029, ADR 0018). A maestro may not
    request_decision from a peer entity, and a lead never escalates directly to
    the user (it goes through its maestro).
    """
    if sender_role == "lead":
        sender_maestro = sender_name.split(".")[0]
        return target_name == sender_maestro

    if sender_role == "maestro":
        return target_name == "user"

    return False


def can_message_user(sender_role: str) -> bool:
    """Gate for a ``message`` action to ``user`` (Ticket 021).

    Only a maestro may message the user directly — the one-way report channel
    that mirrors ``can_request_decision``'s user rule. A lead reports through
    its maestro, never straight to the user. Role-only: the rule needs no name
    or target.
    """
    return sender_role == "maestro"


def can_spawn_team(actor_role: str, actor_name: str) -> bool:
    """Maestros can spawn teams in their own org. The team name is scoped
    automatically (lead becomes ``<actor>.<team_name>``), so org boundary
    is enforced by construction — only the role check matters here.
    """
    return actor_role == "maestro"


def can_kill(
    actor_role: str,
    actor_name: str,
    target_name: str,
    default_maestro: str,
) -> bool:
    """Lifecycle kill permission gate.

    The default maestro and the actor itself are never killable through
    autonomous actions — even by themselves — so a misbehaving entity
    cannot disable the org head or self-terminate the orchestration loop.
    """
    if target_name == default_maestro or target_name == actor_name:
        return False
    if actor_role == "maestro":
        return target_name.startswith(f"{actor_name}.")
    if actor_role == "lead":
        # Leads can kill only their own workers (one level below)
        if not target_name.startswith(f"{actor_name}."):
            return False
        # Disallow killing across team boundaries: target must be a direct
        # child (no further dots after the lead-name prefix)
        suffix = target_name[len(actor_name) + 1 :]
        return "." not in suffix
    return False
