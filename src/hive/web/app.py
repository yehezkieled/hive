"""FastAPI application for Hive web dashboard.

Provides JSON API endpoints for entity status, org tree, tasks, cost,
and audit events. Serves an htmx-powered HTML dashboard at /.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from hive.bus.audit_log import AuditLog
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.process.manager import ProcessManager

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(
    process_manager: ProcessManager,
    token_store: TokenStore | None = None,
    task_store: TaskStore | None = None,
    audit_log: AuditLog | None = None,
) -> FastAPI:
    """Build and return a configured FastAPI application."""
    app = FastAPI(title="Hive Dashboard", version="0.1.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/api/status")
    async def api_status():
        """Entity statuses."""
        return process_manager.get_status()

    @app.get("/api/org")
    async def api_org():
        """Org tree as JSON."""
        from hive.models.maestro import Maestro

        entities = process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        result = {"maestros": []}
        for m in sorted(maestros, key=lambda x: x.name):
            maestro_data = {
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
                        workers.append({
                            "name": wn,
                            "state": w.state.value,
                            "model": w.model,
                        })
                maestro_data["teams"][team_name] = {
                    "lead": team.lead,
                    "workers": workers,
                }
            result["maestros"].append(maestro_data)
        return result

    @app.get("/api/tasks")
    async def api_tasks():
        """Open tasks list."""
        if task_store is None:
            return []
        from hive.models.task import TaskStatus

        pending = await task_store.list(status=TaskStatus.PENDING)
        in_progress = await task_store.list(status=TaskStatus.IN_PROGRESS)
        return [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority,
            }
            for t in pending + in_progress
        ]

    @app.get("/api/cost")
    async def api_cost(window: str = "24h"):
        """Token usage totals."""
        if token_store is None:
            return {"call_count": 0}
        windows = {
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
        }
        delta = windows.get(window, timedelta(hours=24))
        since = datetime.now(UTC) - delta
        totals = await token_store.totals(since=since)
        return dict(totals)

    @app.get("/api/audit")
    async def api_audit(limit: int = 20):
        """Recent audit events."""
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

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Serve the HTML dashboard."""
        return templates.TemplateResponse(request, "dashboard.html")

    return app
