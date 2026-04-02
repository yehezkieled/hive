"""Wraps a single claude -p subprocess for bidirectional communication."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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

    async def start(self) -> None:
        """Spawn the claude -p subprocess."""
        logger.info("Starting Claude session: %s", " ".join(self.args[:6]))
        self.process = await asyncio.create_subprocess_exec(
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
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
                    # stream-json emits various event types
                    if event.get("type") == "assistant" and event.get("subtype") == "text":
                        text_parts.append(event.get("content", ""))
                    elif event.get("type") == "result":
                        # Final result — extract session ID for --resume
                        self._session_id = event.get("session_id")
                        if "cost_usd" in event:
                            cost_usd = event["cost_usd"]
                        # Result also contains the full text
                        result_text = event.get("result", "")
                        if result_text and not text_parts:
                            text_parts.append(result_text)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line from claude: %s", line[:100])

        await self.process.wait()

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
