# Outline — Ticket 008: Track untracked fire-and-forget tasks

Implementation order. Each step is independent; do them in sequence, run the
gate at the end. One branch, one PR (direct lane).

1. **Declare the gate set.** `process/manager.py` `__init__` — add
   `self._gate_tasks: set[asyncio.Task] = set()` next to `_wake_tasks` (~:143).

2. **Track the gate task.** `process/approval_handler.py` `_on_gate_state`
   (:457) — capture the task, `add` it to `self._mgr._gate_tasks`, and
   `add_done_callback(self._mgr._gate_tasks.discard)`.

3. **Reorg + track the server task.** `__main__.py`:
   - Hoist `server: uvicorn.Server | None = None` and the `background_tasks`
     list declaration to just above the `if WEB_PORT > 0:` block.
   - Inside the block: assign the outer `server`, and
     `background_tasks.append(asyncio.create_task(server.serve()))`.
   - In the cleanup (before the `for task in background_tasks` cancel loop):
     `if server is not None: server.should_exit = True`.

4. **Test the gate-task lifetime.** `tests/process/test_approval_handler.py`:
   - Add `self._gate_tasks: set[asyncio.Task] = set()` to `StubManager`.
   - New async test: gated → task in set → `await` task → set empty.

5. **Verify.** ruff check + format; targeted approval-handler test; full
   `pytest -m "not integration"`. See [`plan.md`](plan.md).
