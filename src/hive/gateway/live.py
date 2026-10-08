"""Change watcher behind the desk's live updates and push alerts.

One background task looks at firstmate's ``state/*.status`` files (cheap ``stat``) and the
inbox receipts. When a status file moves it re-runs the snapshot, and publishes a small
``desk`` or ``chat`` event to every connected SSE stream; pages then re-fetch themselves.
It also turns needs-you changes into push alerts: a decision or hold waiting, a PR ready
for review, blocked or failed work, and a first-mate chat reply. The first look after
start only records a baseline, so a restart never replays old alerts. It is read-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

from hive.gateway.chat import ChatView, load_chat
from hive.gateway.desk import Desk, build_desk
from hive.gateway.push import Alert
from hive.gateway.reviews import digest as reviews_digest
from hive.gateway.reviews import read_reviews
from hive.gateway.reviews import state_signature as reviews_signature
from hive.gateway.settings import GatewaySettings
from hive.gateway.snapshot import SnapshotProvider

log = logging.getLogger("hive.gateway.live")
HEARTBEAT_S = 60.0
MIN_SNAPSHOT_GAP_S = 5.0
_AT_RE = re.compile(r"\[at=(\d+)\]")
ALERT_STATES = ("blocked", "failed")
_NEED_TITLE = {
    "decision": "Decision waiting",
    "hold": "A question is waiting for you",
    "merge": "PR ready for review",
}


def status_signature(state_dir: Path) -> tuple:
    """(name, mtime, size) of every ``*.status`` file and the backlog: changes when any
    worker reports or a task or decision moves."""
    sig = []
    try:
        entries = sorted(state_dir.glob("*.status"))
        entries.append(state_dir.parent / "data" / "backlog.md")
        for entry in entries:
            try:
                st = entry.stat()
            except OSError:
                continue
            sig.append((entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        pass
    return tuple(sig)


def last_status(path: Path) -> tuple[str, str, str] | None:
    """(state, at, text) of the last line of a status file, or None."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 4096))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if line:
            state = line.split(" ", 1)[0].rstrip(":")
            m = _AT_RE.search(line)
            return state, m.group(1) if m else "", line.partition(": ")[2]
    return None


def status_alerts(state_dir: Path) -> list[Alert]:
    """Alerts for work whose latest status is blocked or failed."""
    out: list[Alert] = []
    try:
        files = sorted(state_dir.glob("*.status"))
    except OSError:
        return out
    for path in files:
        last = last_status(path)
        if last and last[0] in ALERT_STATES:
            task = path.name.removesuffix(".status")
            state, at, text = last
            out.append(
                Alert(
                    f"status:{task}:{state}:{at}",
                    f"{task} is {state}",
                    text[:160],
                    "/",
                )
            )
    return out


def need_alerts(desk: Desk) -> list[Alert]:
    out = []
    for n in desk.needs_you:
        title = n.title or n.ref.partition("/")[0]
        label = _NEED_TITLE.get(n.kind, "Needs you")
        out.append(
            Alert(
                f"need:{n.kind}:{n.ref}",
                f"{label}: {title}"[:100],
                n.text[:160],
                "/p/" + quote(n.project, safe=""),
            )
        )
    return out


def reply_alerts(view: ChatView) -> list[Alert]:
    return [
        Alert(
            f"reply:{r.id}:{r.reply_at}",
            "First mate replied",
            (r.reply or "")[:160],
            "/chat",
        )
        for r in view.receipts
        if r.reply is not None
    ]


def desk_digest(desk: Desk) -> str:
    """Hash of what the pages show, ignoring the snapshot's own timestamp."""
    shown = {k: v for k, v in desk.projects.items()}
    return hashlib.sha256(repr(shown).encode()).hexdigest()


def chat_digest(view: ChatView) -> str:
    shown = [(r.id, r.state, r.reply_at) for r in view.receipts]
    return repr((view.available, view.can_receive, shown))


class LiveHub:
    def __init__(
        self,
        settings: GatewaySettings,
        provider: SnapshotProvider,
        notify: Callable[[Alert], Awaitable[object]] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._notify = notify
        self._subs: set[asyncio.Queue[str]] = set()
        self._seen: set[str] = set()
        self._baselines: set[str] = set()
        self._task: asyncio.Task | None = None
        self._reviews_sig: tuple[int, int] | None = None
        self._reviews_hash: str | None = None

    # ---- streams ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.discard(q)

    @property
    def clients(self) -> int:
        return len(self._subs)

    def publish(self, scope: str) -> None:
        msg = f"event: {scope}\ndata: {json.dumps({'scope': scope, 'at': int(time.time())})}\n\n"
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # a stalled client; it refreshes on its next event
                pass

    # ---- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        s = self._settings
        sig: tuple | None = None
        desk_hash = chat_hash = ""
        last_snap = last_receipts = 0.0
        dirty = True
        while True:
            try:
                now = time.monotonic()
                new_sig = await asyncio.to_thread(status_signature, s.state_dir)
                if new_sig != sig:
                    sig, dirty = new_sig, True
                if dirty or now - last_snap >= HEARTBEAT_S:
                    if now - last_snap >= MIN_SNAPSHOT_GAP_S:
                        dirty, last_snap = False, now
                        desk_hash = await self._look_at_desk(desk_hash)
                await self._look_at_reviews()
                if now - last_receipts >= s.receipts_interval_s:
                    last_receipts = now
                    chat_hash = await self._look_at_chat(chat_hash)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("live watcher pass failed")
            await asyncio.sleep(s.poll_interval_s)

    async def _look_at_desk(self, before: str) -> str:
        snap = await self._provider.get(fresh=True)
        if snap.data is None:
            digest = "err:" + (snap.reason or "")
            if digest != before:
                self.publish("desk")
            return digest
        desk = build_desk(snap.data)
        alerts = need_alerts(desk) + await asyncio.to_thread(
            status_alerts, self._settings.state_dir
        )
        await self._alert(alerts, replace_prefix="need:", current={a.key for a in alerts})
        digest = desk_digest(desk)
        if digest != before:
            self.publish("desk")
        return digest

    async def _look_at_reviews(self) -> None:
        """Push ``desk`` when the Review pages list changes. One ``stat`` per pass; the state
        file is only read when it moved, and nothing here waits on a snapshot."""
        state = self._settings.lavish_state
        sig = await asyncio.to_thread(reviews_signature, state)
        if sig == self._reviews_sig:
            return
        self._reviews_sig = sig
        shown = await asyncio.to_thread(read_reviews, state, set())
        digest = reviews_digest(shown)
        before, self._reviews_hash = self._reviews_hash, digest
        if before is not None and digest != before:
            self.publish("desk")

    async def _look_at_chat(self, before: str) -> str:
        view = await load_chat(self._settings)
        if view.available:
            await self._alert(reply_alerts(view))
        digest = chat_digest(view)
        if digest != before:
            self.publish("chat")
        return digest

    async def _alert(
        self,
        alerts: list[Alert],
        replace_prefix: str | None = None,
        current: set[str] | None = None,
    ) -> None:
        """Push each alert key once. The first call for a source only records a baseline."""
        if replace_prefix is not None and current is not None:
            # A need that cleared may come back later: forget it so it can alert again.
            self._seen = {k for k in self._seen if not k.startswith(replace_prefix) or k in current}
        fresh = [a for a in alerts if a.key not in self._seen]
        self._seen.update(a.key for a in fresh)
        source = replace_prefix or "reply:"
        if source not in self._baselines:
            self._baselines.add(source)
            return
        if self._notify is None:
            return
        for a in fresh:
            try:
                await self._notify(a)
            except Exception:
                log.exception("push notify failed")
