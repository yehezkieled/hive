"""FastAPI application for the Hive web dashboard.

Serves the A.2 Paper Ops landing page at `/` and JSON/HTML endpoints
that power live refresh of the hero, vault, active, idle, and dormant
sections.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from hive.config import UPLOAD_MAX_BYTES, UPLOADS_DIR
from hive.knowledge.attachment_embedder import embed_attachment
from hive.telegram.commands import Command, parse_command
from hive.web.auth import require_token
from hive.web.sse import format_event

if TYPE_CHECKING:
    from hive.bus.attachment_store import AttachmentStore
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.store import MessageStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.commands.dispatch import CommandDispatcher
    from hive.observability.health_monitor import HealthMonitor
    from hive.process.manager import ProcessManager
    from hive.web.sse import SSEBroker


class CommandRequest(BaseModel):
    text: str


logger = logging.getLogger("hive.web")

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
PERSONALITIES_DIR = Path(__file__).resolve().parents[3] / "personalities"


def create_app(
    process_manager: ProcessManager,
    token_store: TokenStore | None = None,
    task_store: TaskStore | None = None,
    audit_log: AuditLog | None = None,
    vault_store: VaultStore | None = None,
    mode_request_store: ModeRequestStore | None = None,
    default_maestro: str = "otter",
    personalities_dir: Path | None = None,
    command_dispatcher: CommandDispatcher | None = None,
    message_store: MessageStore | None = None,
    sse_broker: SSEBroker | None = None,
    attachment_store: AttachmentStore | None = None,
    health_monitor: HealthMonitor | None = None,
) -> FastAPI:
    """Build and return a configured FastAPI application."""
    from hive.web.view_model import build_dashboard_view_model, build_landing_view_model

    app = FastAPI(title="Hive Dashboard", version="0.2.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    pdir = personalities_dir if personalities_dir is not None else PERSONALITIES_DIR

    # ─── JSON API (preserved from v0.1) ─────────────────────────────────
    @app.get("/api/status")
    async def api_status():
        return process_manager.get_status()

    @app.get("/api/org")
    async def api_org():
        from hive.models.maestro import Maestro

        entities = process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        result: dict = {"maestros": []}
        for m in sorted(maestros, key=lambda x: x.name):
            maestro_data: dict = {
                "name": m.name,
                "state": m.state.value,
                "model": m.model,
                "teams": {},
            }
            for team_name, team in m.teams.items():
                workers = []
                for wn in team.workers:
                    if wn in entities:
                        w = entities[wn]
                        workers.append({"name": wn, "state": w.state.value, "model": w.model})
                maestro_data["teams"][team_name] = {"lead": team.lead, "workers": workers}
            result["maestros"].append(maestro_data)
        return result

    @app.get("/api/tasks")
    async def api_tasks():
        if task_store is None:
            return []
        from hive.models.task import TaskStatus

        pending = await task_store.list(status=TaskStatus.PENDING)
        in_progress = await task_store.list(status=TaskStatus.IN_PROGRESS)
        return [
            {"id": t.id, "title": t.title, "status": t.status.value, "priority": t.priority}
            for t in pending + in_progress
        ]

    @app.get("/api/cost")
    async def api_cost(window: str = "24h"):
        if token_store is None:
            return {"call_count": 0}
        windows = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
        delta = windows.get(window, timedelta(hours=24))
        since = datetime.now(UTC) - delta
        totals = await token_store.totals(since=since)
        return dict(totals)

    @app.get("/api/audit")
    async def api_audit(limit: int = 20):
        if audit_log is None:
            return []
        events = await audit_log.recent(limit=limit)
        return [
            {
                "timestamp": str(e["timestamp"]),
                "actor": e["actor"],
                "action": e["action"],
                "target": e["target"],
            }
            for e in events
        ]

    @app.get("/api/commands")
    async def api_commands():
        from hive.telegram.help_text import HELP_TEXT

        return [
            {
                "name": name,
                "usage": entry.usage,
                "description": entry.description,
                "category": entry.category,
            }
            for name, entry in sorted(HELP_TEXT.items())
        ]

    # ─── Write surface (Sprint 15) ─────────────────────────────────────
    @app.post("/api/command")
    async def api_command(
        body: CommandRequest,
        _: None = Depends(require_token),
    ):
        if command_dispatcher is None:
            return {"text": "Command surface not configured."}
        result = await command_dispatcher.dispatch(body.text, actor="web:user")
        # Persist the round-trip so chat history survives a page refresh.
        # Skip when the dispatcher already routed through the bus (avoids the
        # duplicate user→hive / hive→user pair that shadows the entity round-trip).
        if message_store is not None and not result.routed:
            try:
                await message_store.log_message(sender="user", recipient="hive", content=body.text)
                await message_store.log_message(
                    sender=result.entity or "hive", recipient="user", content=result.text
                )
            except Exception:
                logger.exception("Failed to persist web chat message")
        return {"text": result.text, "metadata": result.metadata, "entity": result.entity}

    @app.post("/api/upload")
    async def api_upload(
        request: Request,
        file: UploadFile = File(...),
        text: str = Form(""),
        _: None = Depends(require_token),
    ) -> dict[str, Any]:
        """Multipart upload — Sprint 17 web file transit.

        Mirrors the Telegram PHOTO/DOCUMENT path: stores the file under
        ``UPLOADS_DIR``, persists metadata via the AttachmentStore, and
        (if ``text`` parses to a routable command) forwards the prompt
        to the targeted entity with the file's absolute path injected
        as a context prefix.
        """
        if attachment_store is None:
            raise HTTPException(status_code=503, detail="Attachments not configured")

        # Pre-flight size check (audit C7): reject oversized uploads via the
        # Content-Length header before opening any file. The streaming check
        # below remains as defence-in-depth for missing/wrong headers.
        content_length_raw = request.headers.get("content-length")
        if content_length_raw is not None:
            try:
                declared_size = int(content_length_raw)
            except ValueError:
                declared_size = -1
            if declared_size > UPLOAD_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File too large; max {UPLOAD_MAX_BYTES} bytes "
                        f"({UPLOAD_MAX_BYTES // (1024 * 1024)} MB)."
                    ),
                )

        original_name = file.filename
        mime_type = file.content_type

        ext = ""
        if original_name:
            ext = Path(original_name).suffix
        if not ext and mime_type:
            ext = mimetypes.guess_extension(mime_type) or ""
        if not ext:
            ext = ".bin"

        filename = f"{uuid.uuid4().hex}{ext}"
        target = UPLOADS_DIR / filename

        size_bytes = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await file.read(64 * 1024)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > UPLOAD_MAX_BYTES:
                        fh.close()
                        target.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File too large; max {UPLOAD_MAX_BYTES} bytes "
                                f"({UPLOAD_MAX_BYTES // (1024 * 1024)} MB)."
                            ),
                        )
                    fh.write(chunk)
        finally:
            await file.close()

        cmd = parse_command(text or "", default_maestro=default_maestro)
        routable = cmd.name in {"message", "team", "agent"} and cmd.target

        forwarded_to = cmd.target if routable else None
        attachment_id = await attachment_store.save(
            file_path=str(target),
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            source="web",
            actor="web:user",
            forwarded_to=forwarded_to,
        )

        try:
            chunks = await embed_attachment(str(target), mime_type)
            if chunks:
                await attachment_store.save_chunks(attachment_id, chunks)
        except Exception:
            logger.exception("Failed to embed web upload %s", target)

        if not routable or command_dispatcher is None:
            return {"id": attachment_id, "forwarded_to": forwarded_to}

        meta_parts = [mime_type or "unknown"]
        if size_bytes:
            meta_parts.append(f"{size_bytes} bytes")
        if original_name:
            meta_parts.append(f"original: {original_name}")
        prefix = f"[Attached file: {target} ({', '.join(meta_parts)})]\n\n"
        enriched_args = prefix + (cmd.args or "")
        enriched_cmd = Command(name=cmd.name, target=cmd.target, args=enriched_args)

        result = await command_dispatcher.dispatch_command(enriched_cmd, actor="web:user")

        if message_store is not None and not result.routed:
            try:
                user_log = f"[file #{attachment_id}: {original_name or filename}]"
                if text:
                    user_log = f"{user_log} {text}"
                await message_store.log_message(sender="user", recipient="hive", content=user_log)
                await message_store.log_message(
                    sender="hive", recipient="user", content=result.text
                )
            except Exception:
                logger.exception("Failed to persist web upload chat message")

        return {
            "id": attachment_id,
            "forwarded_to": cmd.target,
            "response": result.text,
            "metadata": result.metadata,
        }

    @app.get("/api/mode-requests/pending")
    async def api_mode_requests_pending(_: None = Depends(require_token)):
        if mode_request_store is None:
            return {"requests": []}
        rows = await mode_request_store.list_pending(default_maestro)
        return {"requests": rows}

    @app.post("/api/mode-request/{request_id}/approve")
    async def api_mode_approve(
        request_id: int,
        _: None = Depends(require_token),
    ):
        row = await process_manager.approve_mode_request(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found or already resolved")
        return {"ok": True, "id": row["id"], "status": row.get("status", "approved")}

    @app.post("/api/mode-request/{request_id}/deny")
    async def api_mode_deny(
        request_id: int,
        _: None = Depends(require_token),
    ):
        row = await process_manager.deny_mode_request(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Request not found or already resolved")
        return {"ok": True, "id": row["id"], "status": row.get("status", "denied")}

    @app.post("/api/vault-action/{action_id}/approve")
    async def api_vault_approve(
        action_id: int,
        _: None = Depends(require_token),
    ):
        row = await process_manager.approve_vault_action(action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Action not found")
        return {
            "ok": True,
            "id": row["id"],
            "status": row.get("status"),
            "executed_at": row.get("executed_at").isoformat() if row.get("executed_at") else None,
            "denial_reason": row.get("denial_reason"),
        }

    @app.post("/api/vault-action/{action_id}/deny")
    async def api_vault_deny(
        action_id: int,
        request: Request,
        _: None = Depends(require_token),
    ):
        reason: str | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                raw = body.get("reason")
                reason = str(raw).strip() if raw is not None else None
                if reason == "":
                    reason = None
        except Exception:
            reason = None
        row = await process_manager.deny_vault_action(action_id, reason=reason)
        if row is None:
            raise HTTPException(status_code=404, detail="Action not found or already resolved")
        return {
            "ok": True,
            "id": row["id"],
            "status": row.get("status", "denied"),
            "denial_reason": row.get("denial_reason"),
        }

    @app.get("/sse/notifications")
    async def sse_notifications(_: None = Depends(require_token)):
        if sse_broker is None:
            raise HTTPException(status_code=503, detail="SSE broker not configured")
        queue = sse_broker.subscribe()

        async def event_stream():
            # Immediate comment frame so the client confirms connectivity
            # without waiting for the first real notification.
            yield ": ready\n\n"
            try:
                while True:
                    notification = await queue.get()
                    yield format_event(notification)
            finally:
                sse_broker.unsubscribe(queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/messages")
    async def api_messages(limit: int = 20):
        if message_store is None:
            return {"messages": []}
        rows = await message_store.get_recent(limit=limit)
        return {
            "messages": [
                {
                    "from": "user" if r["sender"] == "user" else r["sender"],
                    "to": r["recipient"],
                    "text": r["content"],
                    "timestamp": str(r["timestamp"]),
                }
                for r in rows
            ]
        }

    # ─── Landing fragment endpoints (htmx) ─────────────────────────────
    async def _build_view() -> dict:
        return await build_landing_view_model(
            process_manager=process_manager,
            task_store=task_store,
            token_store=token_store,
            vault_store=vault_store,
            mode_request_store=mode_request_store,
            personalities_dir=pdir,
            default_maestro=default_maestro,
            message_store=message_store,
        )

    @app.get("/api/landing/hero", response_class=HTMLResponse)
    async def landing_hero(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "_partials/hero.html", {"view": view})

    @app.get("/api/landing/vault", response_class=HTMLResponse)
    async def landing_vault(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "_partials/vault.html", {"view": view})

    @app.get("/api/landing/active", response_class=HTMLResponse)
    async def landing_active(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "_partials/active.html", {"view": view})

    @app.get("/api/landing/idle", response_class=HTMLResponse)
    async def landing_idle(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "_partials/idle.html", {"view": view})

    @app.get("/api/landing/dormant", response_class=HTMLResponse)
    async def landing_dormant(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "_partials/dormant.html", {"view": view})

    # ─── Landing page ──────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        from hive.telegram.help_text import HELP_TEXT

        view = await _build_view()
        commands = [
            {
                "name": name,
                "usage": entry.usage,
                "description": entry.description,
                "category": entry.category,
                "display": entry.display,
            }
            for name, entry in sorted(HELP_TEXT.items())
        ]
        return templates.TemplateResponse(
            request, "landing.html", {"view": view, "commands": commands}
        )

    # ─── Dashboard tab (Sprint 20) ─────────────────────────────────────
    async def _build_dashboard() -> dict:
        return await build_dashboard_view_model(
            token_store=token_store,
            audit_log=audit_log,
            task_store=task_store,
            process_manager=process_manager,
            health_monitor=health_monitor,
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        data = await _build_dashboard()
        return templates.TemplateResponse(request, "dashboard.html", {"data": data})

    @app.get("/api/dashboard/all")
    async def api_dashboard_all(_: None = Depends(require_token)):
        return await _build_dashboard()

    return app
