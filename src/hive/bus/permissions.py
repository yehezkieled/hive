"""Inter-agent messaging permission checks.

Rules:
- Maestro can message any entity in its own org (shared name prefix)
- Lead can message its own workers and its parent maestro
- Worker can message its own lead only
- Cross-org messaging is denied
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
    """
    if sender_role == "maestro":
        # Maestro can message any entity in its org (name starts with maestro name)
        return recipient_name.startswith(f"{sender_name}.")

    if sender_role == "lead":
        # Lead can message its own workers (name starts with lead name)
        if recipient_name.startswith(f"{sender_name}."):
            return True
        # Lead can message its parent maestro
        maestro_name = sender_name.split(".")[0]
        return recipient_name == maestro_name

    if sender_role == "worker":
        # Worker can message its own lead only
        lead_name = ".".join(sender_name.split(".")[:-1])
        return recipient_name == lead_name

    return False
