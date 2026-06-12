"""Inter-agent messaging and lifecycle-action permission checks.

Messaging rules:
- Maestro can message any entity in its own org (shared name prefix)
- Lead can message its own workers and its parent maestro
- Worker can message its own lead only
- Cross-org messaging is denied

Lifecycle rules (Sprint 19 — autonomous spawn/kill; amended by ADR 0013):
- Maestro can spawn teams in its own org. It can kill anything in its
  own org except itself and the orchestrator-protected default maestro.
- Worker creation is retired for every actor (ADR 0013, Ticket 016):
  ``can_spawn_worker`` denies unconditionally. Leads fan out leaf work
  with the Workflow tool instead.
- Lead can kill workers in its own team only.
- Workers cannot spawn or kill anything.
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
    - Workers can message workers within the same maestro org. Cross-team
      routes within the same maestro are CC'd to both leads.
    - Cross-maestro worker-to-worker is denied; must escalate via the chain.
    """
    # Self-message is never allowed.
    if sender_name == recipient_name:
        return False

    sender_maestro = sender_name.split(".")[0]
    recipient_maestro = recipient_name.split(".")[0]

    # ---- Peer-to-peer rules (same role on both ends) ----
    if sender_role == recipient_role:
        if sender_role == "maestro":
            return True
        if sender_role == "lead":
            return True
        if sender_role == "worker":
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

    if sender_role == "worker":
        sender_lead = ".".join(sender_name.split(".")[:-1])
        recipient_lead = ".".join(recipient_name.split(".")[:-1])
        if sender_lead == recipient_lead:
            return []
        return [sender_lead, recipient_lead]

    return []


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


def can_spawn_team(actor_role: str, actor_name: str) -> bool:
    """Maestros can spawn teams in their own org. The team name is scoped
    automatically (lead becomes ``<actor>.<team_name>``), so org boundary
    is enforced by construction — only the role check matters here.
    """
    return actor_role == "maestro"


def can_spawn_worker(actor_role: str, actor_name: str, lead_name: str) -> bool:
    """Worker creation is retired on every path (ADR 0013) — denied for
    all actors: lead, maestro, everyone. Leads fan out leaf work with the
    Workflow tool instead. The function survives only so the dispatcher
    branch stays intact until Ticket 018 deletes both together.
    """
    return False


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
