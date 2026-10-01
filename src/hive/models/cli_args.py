"""Claude CLI argument construction for an Entity (ADR 0006 collaborator)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hive.models.entity import DANGEROUS_MODES, resolve_advisor

if TYPE_CHECKING:
    from hive.models.entity import Entity


class CliArgsBuilder:
    """Builds the ``claude -p`` command line for an Entity.

    Collaborator of ``Entity`` per ADR 0006: holds a back-reference and reads
    the entity's fields through it; ``Entity.build_cli_args`` is a thin
    delegation.
    """

    def __init__(self, entity: Entity) -> None:
        self._entity = entity

    def build(self) -> list[str]:
        e = self._entity
        args = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            e.model,
        ]

        if e.system_prompt:
            args.extend(["--system-prompt", e.system_prompt])

        if e.allowed_tools:
            args.extend(["--allowedTools", *e.allowed_tools])

        if e.disallowed_tools:
            args.extend(["--disallowedTools", *e.disallowed_tools])

        if e.permission_mode in DANGEROUS_MODES:
            args.append("--dangerously-skip-permissions")
        elif e.permission_mode != "default":
            args.extend(["--permission-mode", e.permission_mode])

        from hive.process.loops import load_role_jd

        # Identity preamble must be the first appended block so the model
        # reads its own name before any guidance that references it. The
        # role JD avoids placeholders the entity must substitute with its
        # own name (the orchestrator infers `lead` from the actor instead).
        identity_lines = [
            f"You are {e.name}. Your role is {e.role}.",
            "If a hive_action is denied or fails, report the failure honestly. "
            "Do not narrate fictional success.",
        ]
        args.extend(["--append-system-prompt", "\n".join(identity_lines)])

        # T007: the loop framework (LOOP_PROMPTS) is retired in favour of
        # Claude Code's native /goal, which Hive seeds on the entity's first
        # turn (see message_dispatcher.send_to_entity). No loop prompt here.

        # Role JD encodes the messaging protocol and any role-specific
        # autonomy actions. Loaded from personalities/role-<role>.md so it
        # can be edited without code changes.
        if e.role in ("maestro", "lead"):
            args.extend(["--append-system-prompt", load_role_jd(e.role)])

        from hive.mcp.config import mcp_servers_enabled

        if mcp_servers_enabled():
            args.extend(["--mcp-config", e.mcp_config_path])

        advisor = resolve_advisor(e.model, e.advisor, e.role)
        if advisor:
            args.extend(["--advisor", advisor])

        return args
