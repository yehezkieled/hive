"""Wraps a single claude -p subprocess for bidirectional communication."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# asyncio's default StreamReader buffer is 64 KB. Stream-json events
# from ``claude -p`` routinely exceed that when a single assistant
# message, tool result, or (since Sprint 11) auto-retrieved blueprint
# block lands on one line — triggering LimitOverrunError on readline().
# 10 MB per line is generous enough to survive any realistic payload
# without inviting runaway memory use.
_STREAM_LIMIT = 10 * 1024 * 1024


class ClaudeSession:
    """Manages a single claude -p subprocess with stream-json I/O.

    Uses --output-format stream-json to get structured output.
    For multi-turn: sends prompts via stdin, reads JSON lines from stdout.
    """

    def __init__(
        self,
        args: list[str],
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.args = args
        self.cwd = str(cwd) if cwd else None
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.started_at: datetime | None = None
        self._session_id: str | None = None
        self._last_usage: dict | None = None

    async def start(self) -> None:
        """Spawn the claude -p subprocess."""
        logger.info("Starting Claude session: %s", " ".join(self.args[:6]))
        self.process = await asyncio.create_subprocess_exec(
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            limit=_STREAM_LIMIT,
        )
        self.pid = self.process.pid
        self.started_at = datetime.now(UTC)
        logger.info("Claude session started with PID %d", self.pid)

    async def send_prompt(self, prompt: str) -> str:
        """Send a prompt and collect the full text response.

        For the MVP, this uses one-shot mode: writes prompt to stdin,
        closes stdin, reads all output until the process exits.
        Returns the concatenated text content from the stream-json output.
        """
        if self.process is None:
            raise RuntimeError("Session not started. Call start() first.")

        if self.process.stdin is None:
            raise RuntimeError("No stdin available for the session.")

        # Write prompt and close stdin to signal end of input
        self.process.stdin.write(prompt.encode())
        self.process.stdin.close()

        # Collect text content from stream-json output
        text_parts: list[str] = []
        cost_usd: float | None = None

        if self.process.stdout:
            async for line_bytes in self.process.stdout:
                line = line_bytes.decode().strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_type = event.get("type")

                    if event_type == "assistant":
                        # With --verbose, assistant messages contain a nested message object
                        msg = event.get("message", {})
                        for block in msg.get("content", []):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))

                    elif event_type == "result":
                        self._session_id = event.get("session_id")
                        cost_usd = event.get("total_cost_usd")
                        # Capture the usage sub-object for token tracking.
                        # Field names match the Anthropic API response; we
                        # keep only the numeric counts + id + cost, not the
                        # nested cache_creation / iterations detail.
                        raw_usage = event.get("usage", {}) or {}
                        self._last_usage = {
                            "session_id": self._session_id,
                            "input_tokens": raw_usage.get("input_tokens", 0),
                            "output_tokens": raw_usage.get("output_tokens", 0),
                            "cache_creation_input_tokens": raw_usage.get(
                                "cache_creation_input_tokens", 0
                            ),
                            "cache_read_input_tokens": raw_usage.get("cache_read_input_tokens", 0),
                            "cost_usd": cost_usd,
                        }
                        # Result also contains the full text as fallback
                        result_text = event.get("result", "")
                        if result_text and not text_parts:
                            text_parts.append(result_text)

                except json.JSONDecodeError:
                    logger.debug("Non-JSON line from claude: %s", line[:100])

        await self.process.wait()

        # Log stderr on failure for debugging
        if self.process.returncode != 0:
            stderr_output = await self.get_stderr()
            if stderr_output:
                logger.error(
                    "Claude session PID %d stderr: %s",
                    self.pid or 0,
                    stderr_output.strip()[:500],
                )

        response = "".join(text_parts)
        logger.info(
            "Claude session PID %d completed (exit=%s, cost=$%s)",
            self.pid or 0,
            self.process.returncode,
            f"{cost_usd:.4f}" if cost_usd else "?",
        )
        return response

    @property
    def session_id(self) -> str | None:
        """The session ID from the last response, for use with --resume."""
        return self._session_id

    @property
    def last_usage(self) -> dict | None:
        """Usage dict from the most recent send_prompt call, if any.

        Keys: session_id, input_tokens, output_tokens,
        cache_creation_input_tokens, cache_read_input_tokens, cost_usd.
        None if send_prompt hasn't completed yet or no result event was seen.
        """
        return self._last_usage

    @property
    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        return self.process is not None and self.process.returncode is None

    async def kill(self) -> None:
        """Terminate the subprocess. SIGTERM first, SIGKILL after 5s."""
        if self.process is None or self.process.returncode is not None:
            return

        logger.info("Killing Claude session PID %d", self.pid or 0)
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
        except TimeoutError:
            logger.warning("SIGTERM timeout, sending SIGKILL to PID %d", self.pid or 0)
            self.process.kill()
            await self.process.wait()

    async def get_stderr(self) -> str:
        """Read any stderr output (for debugging)."""
        if self.process and self.process.stderr:
            data = await self.process.stderr.read()
            return data.decode()
        return ""
