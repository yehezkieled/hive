"""Surface-agnostic command dispatch.

Both Telegram bridge and web endpoints route parsed commands through the
:class:`CommandDispatcher` defined here. The dispatcher executes against
ProcessManager + stores and returns a :class:`CommandResult` — formatting
for any specific transport happens at the edges.
"""

from hive.commands.dispatch import KNOWN_COMMANDS, CommandDispatcher, CommandResult

__all__ = ["KNOWN_COMMANDS", "CommandDispatcher", "CommandResult"]
