"""The Run page over HTTP: the page itself, and what ``POST /act/run`` does.

What may run, the limits and the record of every run belong to ``runqueue.py``; the page's
HTML to ``pages/run.py``. This module is the part between them: the owner's confirm steps.
The owner gate and the CSRF check have already run when anything here is called.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.responses import HTMLResponse, Response

from hive.gateway import actions, pages, runqueue
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.responses import Confirm, confirm_response, outcome_response, refused
from hive.gateway.routes import Services
from hive.gateway.runqueue import RunQueue
from hive.gateway.settings import GatewaySettings


def add_run_routes(app: FastAPI, s: Services, runq: RunQueue) -> None:
    @app.get("/run", response_class=HTMLResponse)
    async def run_page() -> HTMLResponse:
        """The fix buttons and the last runs. Rendering it runs nothing; a run the desk did
        not see finish is settled from the logs."""
        snap = await s.provider.get()
        view = await runq.view()
        return HTMLResponse(pages.render_run(view, await s.ctx_for(snap, "/run")))


RUN_PENDING = "Running. The result shows in the list of runs when it finishes."


async def _run(
    form: dict[str, str],
    runq: RunQueue,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    wants_json: bool,
) -> Response:
    """One Run page action: a confirm step, a second one for the actions that take things
    down, then one run. Each step-up is bound to exactly this action, number and request id,
    so a link, a GET or another page cannot start one, and a confirmed tap runs only what
    it confirmed."""
    key = "-"  # the journal names only a listed action, never text from the request
    try:
        action, n = runqueue.parse(form)
        key = action.key
        rid = form.get("rid", "")
        subject = f"{action.key}:{'-' if n is None else n}:{rid}"
        step = form.get("step", "")
        good_rid = actions.REQUEST_ID_RE.fullmatch(rid) is not None
        hidden = {"do": action.key, **({"n": str(n)} if n is not None else {})}
        if good_rid and action.down and tokens.check_step_up(step, "run-down", subject):
            pass  # both confirms passed
        elif good_rid and tokens.check_step_up(step, "run", subject):
            if action.down:  # the extra confirm: what goes down and how it comes back
                return confirm_response(
                    Confirm(
                        f"{action.label}: are you sure?",
                        action.down,
                        "run",
                        {**hidden, "rid": rid, "step": tokens.step_up("run-down", subject)},
                        f"Yes, {action.label[0].lower()}{action.label[1:]}",
                        warn=True,
                    ),
                    nxt,
                    tokens,
                    wants_json,
                )
        else:
            why, status = await runq.refusal(action)
            if why:  # not offered now: say so at the first tap, not after the confirm
                raise ActionError(why, status)
            rid = actions.new_request_id()
            subject = f"{action.key}:{'-' if n is None else n}:{rid}"
            when = action.when(n)
            detail = (
                f"{action.plain}{' It runs ' + when + '.' if when else ''} "
                f"Exactly: {action.command(n)}. Time-out {int(action.timeout_s)} s."
            )
            return confirm_response(
                Confirm(
                    action.label,
                    detail,
                    "run",
                    {**hidden, "rid": rid, "step": tokens.step_up("run", subject)},
                    "Tap again: next step" if action.down else "Tap again to run",
                    warn=True,
                ),
                nxt,
                tokens,
                wants_json,
            )

        async def start() -> Outcome:
            done = await runq.run(action, n, rid, settings.owner_login)
            if done.result != runqueue.OK:
                raise ActionError(done.message, runqueue.RESULT_STATUS.get(done.result, 502))
            return Outcome("run", action.key, done.message)

        result = await runs.run(
            rid, start, actions.ACT_WAIT_S, ("run", action.key), summary=RUN_PENDING
        )
    except ActionError as exc:
        actions.audit("run", key, "not-run-or-failed", status=exc.status)
        return refused("Run", str(exc), nxt, exc.status, wants_json)
    reload_s = action.reload_s if not result.pending else 0
    return outcome_response(result, nxt, wants_json, {"reload": reload_s} if reload_s else None)
