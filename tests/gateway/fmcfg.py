"""A synthetic firstmate home's config files, for tests (never the real home)."""

from __future__ import annotations

import json
from pathlib import Path

PROJECTS = (
    "# registry\n"
    "- alpha [no-mistakes +yolo] - alpha project (added 2026-01-01)\n"
    "- beta [direct-PR] - beta project\n"
    "- gamma [local-only +yolo] - gamma project\n"
    "- legacy - no annotation\n"
)
DISPATCH = {
    "rules": [
        {
            "when": "UI work",
            "use": [
                {"harness": "claude", "model": "claude-opus-x", "effort": "medium"},
                {"harness": "codex", "model": "gpt-x", "effort": "high"},
            ],
        },
        {"when": "Simple fix", "use": {"harness": "claude", "model": "claude-sonnet-x"}},
    ],
    "default": {"harness": "codex", "model": "gpt-y", "effort": "medium"},
}


def write_config(
    home: Path,
    *,
    projects: str | None = PROJECTS,
    dispatch: object = DISPATCH,
    harness: str | None = "codex\n",
    permission: str | None = None,
) -> Path:
    """Write the four sources; ``None`` leaves one out, a string is written verbatim."""
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "config").mkdir(parents=True, exist_ok=True)
    files = {
        home / "data" / "projects.md": projects,
        home / "config" / "crew-dispatch.json": (
            dispatch if isinstance(dispatch, str | None) else json.dumps(dispatch)
        ),
        home / "config" / "crew-harness": harness,
        home / "config" / "claude-permission-mode": permission,
    }
    for path, text in files.items():
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text)
    return home
