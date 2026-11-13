"""What ``POST /act/{name}`` runs for each action name, behind the owner gate and the CSRF
check the route has already made. The Run, Sign-ins and Terminal actions are not here: they
are in ``runroutes.py``, ``signinroutes.py`` and ``terminalroutes.py``."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable

from starlette.responses import HTMLResponse, JSONResponse, Response

from hive.gateway import actions, fmconfig, pages
from hive.gateway.actions import ActionError, Outcome, RunOnce, Tokens
from hive.gateway.desk import Desk, build_desk
from hive.gateway.responses import Confirm, outcome_response, refused
from hive.gateway.reviews import Review, read_reviews, session_file
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider
from hive.gateway.wake import WakeService
from hive.gateway.watchdog import ON_DETAIL, ConfigError, Watchdog

REVIEW_ACTIONS = ("review-close", "review-close-old")
MAX_REVIEW_KEYS = 100


async def _chat(
    form: dict[str, str], settings: GatewaySettings, nxt: str, wants_json: bool
) -> Response:
    try:
        text = actions.check_text(form.get("text", ""), "message")
        result = await actions.send_note_bounded(settings, text, form.get("rid", ""), "chat", "-")
    except ActionError as exc:
        actions.audit("chat", "-", "refused", status=exc.status)
        return refused("chat", str(exc), nxt, exc.status, wants_json)
    return outcome_response(result, nxt, wants_json)


def _step_subject(name: str, keys: list[str]) -> str:
    """What a confirm step is bound to: the action and exactly the pages it will end."""
    return f"{name}:{hashlib.sha256(chr(31).join(sorted(keys)).encode()).hexdigest()[:24]}"


def _titles(reviews: list[Review], keys: list[str]) -> str:
    names = {r.key: r.title for r in reviews}
    shown = [f"“{names.get(k, k)}”" for k in keys[:8]]
    more = f" and {len(keys) - 8} more" if len(keys) > 8 else ""
    return ", ".join(shown) + more


async def _review_action(
    name: str,
    form: dict[str, str],
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    wants_json: bool,
    publish: Callable[[str], None],
) -> Response:
    """End Lavish review sessions: ``review-close`` one page, ``review-close-old`` every page
    that looks done. Nothing runs until the owner's confirm step returns the step token for
    exactly these pages. The session's file is read from Lavish's state, never from the request.
    """

    def fail(exc: ActionError) -> Response:
        actions.audit(name, "-", "refused", status=exc.status)
        return refused(name, str(exc), nxt, exc.status, wants_json)

    def done(summary: str, pending: bool = False) -> Response:
        if not pending:
            publish("desk")
        return outcome_response(Outcome(name, "-", summary, pending), nxt, wants_json)

    try:
        reviews = await asyncio.to_thread(read_reviews, settings.lavish_state, set())
        if name == "review-close":
            key = actions.check_id(form.get("key", ""), "review page")
            keys = [key] if any(r.key == key for r in reviews) else []
            if not keys:
                return done("That review page is already closed.")
            label, what = (
                "Close review page",
                f"end the Lavish review session {_titles(reviews, keys)}",
            )
        else:
            if "keys" in form:  # the confirmed list; pages closed since are skipped in run()
                keys = [k for k in form["keys"].split(",") if k]
                if len(keys) > MAX_REVIEW_KEYS or not all(actions.ID_RE.fullmatch(k) for k in keys):
                    raise ActionError("invalid review page list")
            else:
                keys = [r.key for r in reviews if r.stale]
            if not keys:
                return done("No review pages look done.")
            label, what = (
                "Close old review pages",
                (
                    f"end {len(keys)} Lavish review session{'s' if len(keys) != 1 else ''}: "
                    f"{_titles(reviews, keys)}"
                ),
            )
        subject = _step_subject(name, keys)
        rid = form.get("rid", "")
        if not tokens.check_step_up(form.get("step", ""), "review", subject):
            step = tokens.step_up("review", subject)
            rid = rid if actions.REQUEST_ID_RE.fullmatch(rid) else actions.new_request_id()
            extra = {"keys": ",".join(keys)} if name == "review-close-old" else {}
            if wants_json:
                return JSONResponse(
                    {
                        "ok": True,
                        "confirm": True,
                        "step": step,
                        "rid": rid,
                        "extra": extra,
                        "label": "Tap again to close"
                        if len(keys) == 1
                        else f"Tap again: close {len(keys)}",
                    }
                )
            ctx = pages.Ctx(tokens.csrf(), True, nxt, settings.board_url, settings.default_tz)
            return HTMLResponse(
                pages.render_confirm(
                    label,
                    f"This will {what}. The pages themselves are kept.",
                    name,
                    ctx,
                    {"key": keys[0], "rid": rid, "step": step, **extra}
                    if name == "review-close"
                    else {"rid": rid, "step": step, **extra},
                )
            )
        if not actions.REQUEST_ID_RE.fullmatch(rid):
            raise ActionError("invalid request id")

        async def run() -> Outcome:
            ended = failed = 0
            for key in keys:
                file = await asyncio.to_thread(session_file, settings.lavish_state, key)
                if file is None:  # closed meanwhile: nothing to do
                    continue
                if await actions.end_review(settings, key, str(file)):
                    ended += 1
                else:
                    failed += 1
            text = f"Closed {ended} review page{'s' if ended != 1 else ''}."
            if failed:
                text += f" {failed} could not be closed."
                if not ended:
                    raise ActionError(text, 502)
            return Outcome(name, "-", text)

        result = await runs.run(rid, run, actions.ACT_WAIT_S, (name, "-"), lambda: publish("desk"))
    except ActionError as exc:
        return fail(exc)
    return done(result.summary, result.pending)


WAKE_STATUS = {"refused": 409, "rate-limited": 429, "unavailable": 503, "failed": 502}

# action -> (confirm title, confirm text, armed button label); both run through the same owner
# gate and step-up.
FIRSTMATE_ACTIONS = {
    "wake": (
        "Wake firstmate",
        "This runs the fleet-up firstmate step: it starts a firstmate session only if "
        "none is running, and never a second one.",
        "Tap again to wake",
    ),
    "restart": (
        "Restart firstmate session",
        "This STOPS the running firstmate session (the one claude process in the firstmate "
        "home; no worker or crew is touched) and then starts a fresh one with the fleet-up "
        "firstmate step. Anything it was doing in that session is lost. It refuses, "
        "stopping nothing, if firstmate was mid-turn in the last few minutes.",
        "Tap again to restart",
    ),
}


async def _firstmate(
    name: str,
    form: dict[str, str],
    wake: WakeService,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
) -> Outcome | Confirm:
    """Confirm step, then one wake or restart. The owner gate already ran in the middleware."""
    rid = form.get("rid", "")
    if not actions.REQUEST_ID_RE.fullmatch(rid) or not tokens.check_step_up(
        form.get("step", ""), name, f"firstmate:{rid}"
    ):
        rid = actions.new_request_id()
        step = tokens.step_up(name, f"firstmate:{rid}")
        title, detail, label = FIRSTMATE_ACTIONS[name]
        return Confirm(title, detail, name, {"rid": rid, "step": step}, label, warn=True)

    async def run() -> Outcome:
        act = wake.restart if name == "restart" else wake.wake
        result = await act(settings.owner_login, "desk")
        if not result.ok:
            raise ActionError(result.message, WAKE_STATUS.get(result.outcome, 502))
        return Outcome(name, "firstmate", result.message)

    return await runs.run(rid, run)


async def _watchdog(
    form: dict[str, str],
    watchdog: Watchdog,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
) -> Outcome | Confirm:
    """Turn automatic recovery on (after a confirm step) or off (at once: the kill switch)."""
    rid = form.get("rid", "")
    on = form.get("to") == "on"
    if form.get("to") not in ("on", "off"):
        raise ActionError("Choose on or off.", 400)
    if on and (
        not actions.REQUEST_ID_RE.fullmatch(rid)
        or not tokens.check_step_up(form.get("step", ""), "watchdog", f"firstmate:{rid}")
    ):
        rid = actions.new_request_id()
        step = tokens.step_up("watchdog", f"firstmate:{rid}")
        return Confirm(
            "Turn on automatic recovery",
            ON_DETAIL,
            "watchdog",
            {"rid": rid, "step": step, "to": "on"},
            "Tap again to turn on",
            warn=True,
        )
    if not actions.REQUEST_ID_RE.fullmatch(rid):
        rid = actions.new_request_id()

    async def run() -> Outcome:
        try:
            text = await watchdog.set_enabled(on, settings.owner_login, "desk")
        except ConfigError as exc:
            raise ActionError(f"Not turned on: {exc}. Fix the watchdog config first.", 409) from exc
        return Outcome("watchdog", "firstmate", text)

    return await runs.run(rid, run)


async def _snapshot_action(
    name: str,
    form: dict[str, str],
    provider: SnapshotProvider,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    publish: Callable[[], None],
) -> Outcome | Confirm:
    """An action that is checked against firstmate's snapshot before it runs."""
    rid = form.get("rid", "")
    try:
        # The cached snapshot is enough to check an id against; a write never waits on a
        # fresh fleet snapshot (about 5 s on a real fleet).
        snap = await provider.quick(actions.SNAPSHOT_WAIT_S)
        if snap is None or snap.data is None:
            raise ActionError("The desk is read-only until firstmate's snapshot is readable.", 503)
        return await _dispatch(
            name, form, build_desk(snap.data), settings, tokens, runs, nxt, publish
        )
    except ActionError as exc:
        # A repeat of a request already taken (the card is gone by now, or the snapshot
        # moved on) reports that request's outcome instead of a stale refusal.
        if exc.status not in (409, 503) or not runs.known(rid):
            raise
        return await runs.run(rid, None, actions.ACT_WAIT_S)


def _need(desk: Desk, kind: str, ref: str):
    for n in desk.needs_you:
        if n.kind == kind and n.ref == ref:
            return n
    raise ActionError("That item is no longer waiting on you. Refresh the desk.", 409)


async def _dispatch(
    name: str,
    form: dict[str, str],
    desk: Desk,
    settings: GatewaySettings,
    tokens: Tokens,
    runs: RunOnce,
    nxt: str,
    publish: Callable[[], None],
) -> Outcome | Confirm:
    """Run one action. Returns its outcome, or a ``Confirm`` for step-up actions.

    Nothing here waits longer than ``ACT_WAIT_S``: the work runs once per request id in
    ``runs``, and a run still going answers a pending outcome. The same id asked again gets
    the finished result, or the failure.
    """
    owner = settings.owner_login
    rid = form.get("rid", "")

    def submit(action: str, subject: str, start: Callable[[], Awaitable[Outcome]]):
        if not actions.REQUEST_ID_RE.fullmatch(rid):
            raise ActionError("invalid request id")
        return runs.run(rid, start, actions.ACT_WAIT_S, (action, subject), publish)

    def note(body: str, action: str, subject: str):
        return submit(
            action, subject, lambda: actions.send_note(settings, body, rid, action, subject)
        )

    if name == "answer":
        task = actions.check_id(form.get("task", ""), "task id")
        release = form.get("release") == "1"
        answer_body = actions.answer_body(form.get("text", ""))

        async def answer() -> Outcome:
            _need(desk, "hold", task)
            return await actions.answer_hold(settings, task, answer_body, release)

        return await submit("answer", f"{task}{' release' if release else ''}", answer)

    if name == "decision":
        task = actions.check_id(form.get("task", ""), "task id")
        key = actions.check_id(form.get("key", ""), "decision key")
        _need(desk, "decision", f"{task}/{key}")
        text = actions.check_text(form.get("text", ""), "answer")
        body = actions.decision_note_body(task, key, text, owner)
        return await note(body, "decision", f"{task}/{key}")

    if name == "delegate":
        text = actions.check_text(form.get("text", ""), "goal")
        focus = form.get("project", "")
        if not focus:  # no project focus: a plain message to the first mate
            return await note(text, "delegate", "-")
        await _worker_gate(settings)
        project = desk.projects.get(focus)
        if project is None:
            raise ActionError("Unknown project. Refresh the desk.", 404)
        to = "second mate" if project.mate else "first mate"
        body = actions.delegate_body(project.name, to, text, owner)
        return await note(body, "delegate", project.name)

    if name == "ticket":
        return await _ticket(form, desk, settings, note)

    if name == "merge":
        task = actions.check_id(form.get("task", ""), "task id")
        need = _need(desk, "merge", task)
        if not tokens.check_step_up(form.get("step", ""), "merge", task):
            return Confirm(
                "Give merge word",
                f"This records your word to merge {task}"
                f"{' (' + need.url + ')' if need.url else ''} as a note to the first mate. "
                "The website never merges; the first mate does.",
                "merge",
                {
                    "task": task,
                    "rid": rid or actions.new_request_id(),
                    "step": tokens.step_up("merge", task),
                },
                "Tap again to give the merge word",
            )
        projects = await asyncio.to_thread(fmconfig.read_projects, settings.fm_home)
        body = actions.merge_word_body(
            task,
            need.url or "",
            owner,
            fmconfig.posture_label(projects, need.project),
            "no (the website cannot see checks; firstmate decides)",
        )
        return await note(body, "merge", task)

    if name == "control":
        task = actions.check_id(form.get("task", ""), "task id")
        verb = form.get("verb", "")
        if verb not in actions.CONTROL_VERBS:
            raise ActionError("unsupported control verb")
        if not any(c.id == task for p in desk.projects.values() for c in p.crews):
            raise ActionError("That worker is not listed. Refresh the desk.", 409)
        if not actions.REQUEST_ID_RE.fullmatch(rid) or not tokens.check_step_up(
            form.get("step", ""), "control", f"{task}:{verb}:{rid}"
        ):
            rid = actions.new_request_id()
            step = tokens.step_up("control", f"{task}:{verb}:{rid}")
            hidden = {"task": task, "verb": verb, "rid": rid, "step": step}
            if verb == "relaunch":
                hidden["note"] = (
                    form.get("note") or "Relaunched from the Hive website by the owner."
                )
            return Confirm(
                f"{verb.capitalize()} {task}",
                f"This will {verb} the worker {task}."
                + (" Its agent is replaced in the same worktree." if verb == "relaunch" else ""),
                "control",
                hidden,
                f"Tap again to {verb}",
            )
        note_text = (
            actions.check_text(form.get("note", ""), "relaunch note", 1000)
            if verb == "relaunch"
            else None
        )
        return await submit(
            "control", f"{task} {verb}", lambda: actions.control(settings, task, verb, note_text)
        )

    raise ActionError("unknown action", 404)


async def _worker_gate(settings: GatewaySettings) -> None:
    """A request that can start a worker is refused, not guessed, when firstmate's worker
    settings are missing or unreadable. The captain is told to fix the config."""
    cfg = await asyncio.to_thread(fmconfig.load, settings.fm_home)
    why = cfg.workers_ready
    if why:
        actions.audit("worker-config", "-", "refused", reason=why[:120])
        raise ActionError(
            f"Not sent: firstmate's worker settings are unusable ({why}). "
            "Ask the captain to fix them; Hive will not pick a harness or model itself.",
            409,
        )


async def _ticket(
    form: dict[str, str],
    desk: Desk,
    settings: GatewaySettings,
    note: Callable[[str, str, str], Awaitable[Outcome]],
) -> Outcome:
    mode = form.get("mode", "")
    project = desk.projects.get(form.get("project", ""))
    if project is None:
        raise ActionError("Unknown project.", 404)
    if mode == "create":
        await _worker_gate(settings)
        title = actions.check_line(form.get("title", ""), "title")
        details = form.get("text", "").replace("\r\n", "\n").strip()
        text = actions.check_text(f"{title}\n\n{details}" if details else title, "ticket")
        body = actions.ticket_request_body(
            "create", "(new)", project.name, "new", text, settings.owner_login
        )
        return await note(body, "ticket-create", project.name)
    if mode == "edit":
        ticket = actions.check_id(form.get("ticket", ""), "ticket id")
        row = next((r for r in project.rows if r.id == ticket and r.state != "done"), None)
        if row is None:
            raise ActionError("That ticket is not editable here. Refresh the desk.", 409)
        field = form.get("field", "")
        if field not in actions.TICKET_FIELDS:
            raise ActionError("unsupported ticket field")
        text = actions.check_text(form.get("text", ""), "new text")
        body = actions.ticket_request_body(
            "edit", ticket, project.name, field, text, settings.owner_login
        )
        return await note(body, "ticket-edit", ticket)
    raise ActionError("unknown ticket action")
