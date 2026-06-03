"""Pre-deploy smoke test: spawn real claude, verify PTY idle glyph detection.

Run to confirm the PTY harness works on this host:
  1. Claude Code starts and reaches the idle prompt within 20 seconds
  2. The '❯' glyph is present in output
  3. Trust prompt ("Do you trust") behaviour matches expectations

Usage:
    uv run python scripts/pty_smoke.py
"""

import asyncio
import re
from pathlib import Path

from ptyprocess import PtyProcess


async def main() -> None:
    proc = PtyProcess.spawn(
        ["claude", "--dangerously-skip-permissions", "--model", "sonnet"],
        dimensions=(50, 200),
    )

    buf = b""
    loop = asyncio.get_event_loop()

    async def read_for(seconds: float) -> None:
        nonlocal buf
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                chunk = await asyncio.wait_for(
                    loop.run_in_executor(None, proc.read, 1024),
                    timeout=min(remaining, 1.0),
                )
                buf += chunk
            except TimeoutError:
                pass
            except (EOFError, OSError):
                break

    print("Waiting 20s for Claude to start and show idle prompt...")
    await read_for(20)

    out_path = Path("/tmp/pty_smoke_output.txt")
    out_path.write_bytes(buf)
    print(f"Raw bytes written to {out_path}")
    print(f"Total bytes: {len(buf)}")

    decoded = buf.decode("utf-8", errors="replace")
    print("\n--- LAST 500 CHARS ---")
    print(repr(decoded[-500:]))
    print("---")

    turn_complete = re.compile(r"❯")
    if turn_complete.search(decoded):
        print("\n✓ '❯' idle glyph FOUND in output")
    else:
        print("\n✗ '❯' idle glyph NOT FOUND — check output file for actual prompt")

    if "Do you trust" in decoded:
        print("✓ 'Do you trust' FOUND in output")
    else:
        print("? 'Do you trust' not found (may already be trusted)")

    try:
        proc.write(b"/exit\r\n")
        await asyncio.sleep(1)
    except OSError:
        pass
    if proc.isalive():
        proc.terminate(force=True)
    print("Done.")


asyncio.run(main())
