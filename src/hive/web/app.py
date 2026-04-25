"""FastAPI application for the Hive web dashboard.

Serves the A.2 Paper Ops landing page at `/` and JSON/HTML endpoints
that power live refresh of the hero, vault, and active-maestros sections.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from hive.web.auth import require_token

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.bus.mode_request_store import ModeRequestStore
    from hive.bus.store import MessageStore
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.bus.vault_store import VaultStore
    from hive.commands.dispatch import CommandDispatcher
    from hive.process.manager import ProcessManager


class CommandRequest(BaseModel):
    text: str


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
    default_maestro: str = "dev",
    personalities_dir: Path | None = None,
    command_dispatcher: CommandDispatcher | None = None,
    message_store: MessageStore | None = None,
) -> FastAPI:
    """Build and return a configured FastAPI application."""
    from hive.web.view_model import build_landing_view_model

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

    # ─── Write surface (Sprint 15) ─────────────────────────────────────
    @app.post("/api/command")
    async def api_command(
        body: CommandRequest,
        _: None = Depends(require_token),
    ):
        if command_dispatcher is None:
            return {"text": "Command surface not configured."}
        result = await command_dispatcher.dispatch(body.text, actor="web:user")
        return {"text": result.text, "metadata": result.metadata}

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

    # ─── Landing page ──────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        view = await _build_view()
        return templates.TemplateResponse(request, "dashboard.html", {"view": view})

    return app
