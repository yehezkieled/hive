"""Hive configuration — paths, defaults, environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file (must happen before reading env vars)
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PERSONALITIES_DIR = PROJECT_ROOT / "personalities"
WORKTREES_DIR = PROJECT_ROOT / "worktrees"
BLUEPRINTS_DIR = DATA_DIR / "blueprints"

# Ensure runtime directories exist
DATA_DIR.mkdir(exist_ok=True)
WORKTREES_DIR.mkdir(exist_ok=True)
BLUEPRINTS_DIR.mkdir(exist_ok=True)

# Database — PostgreSQL via asyncpg
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "hive")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "hive")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "hive")
POSTGRES_DSN = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_IDS: list[int] = [
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]

# Claude CLI defaults
DEFAULT_MODEL = os.environ.get("HIVE_DEFAULT_MODEL", "sonnet")
MAX_CONCURRENT_SESSIONS = int(os.environ.get("HIVE_MAX_SESSIONS", "3"))

# Default maestro
DEFAULT_MAESTRO = os.environ.get("HIVE_DEFAULT_MAESTRO", "dev")

# Web dashboard (0 = disabled). WEB_HOST defaults to 127.0.0.1 so the
# dashboard is only reachable locally (and via Tailscale) unless explicitly
# bound to 0.0.0.0. Do not flip to 0.0.0.0 until auth ships (Sprint 14).
WEB_PORT = int(os.environ.get("HIVE_WEB_PORT", "0"))
WEB_HOST = os.environ.get("HIVE_WEB_HOST", "127.0.0.1")
# Bearer token required for write endpoints (POST /api/command, SSE).
# Empty/unset disables the write surface entirely — read-only landing
# still works because the Tailscale bind already gates network access.
WEB_TOKEN = os.environ.get("HIVE_WEB_TOKEN", "")

# Auto-management (Sprint 10)
AUTO_COMPACT_ENABLED = os.environ.get("HIVE_AUTO_COMPACT_ENABLED", "true").lower() == "true"
AUTO_COMPACT_THRESHOLD = int(os.environ.get("HIVE_AUTO_COMPACT_THRESHOLD", "50000"))
AUTO_KILL_IDLE_ENABLED = os.environ.get("HIVE_AUTO_KILL_IDLE_ENABLED", "true").lower() == "true"
IDLE_TIMEOUT_MINUTES = int(os.environ.get("HIVE_IDLE_TIMEOUT_MINUTES", "30"))
DAILY_SUMMARY_ENABLED = os.environ.get("HIVE_DAILY_SUMMARY_ENABLED", "true").lower() == "true"
DAILY_SUMMARY_HOUR = int(os.environ.get("HIVE_DAILY_SUMMARY_HOUR", "23"))  # UTC
SUMMARY_CHAT_ID = os.environ.get("HIVE_SUMMARY_CHAT_ID", "")

# Heartbeat (Sprint 13)
HEARTBEAT_ENABLED = os.environ.get("HIVE_HEARTBEAT_ENABLED", "false").lower() == "true"
HEARTBEAT_INTERVAL_MINUTES = int(os.environ.get("HIVE_HEARTBEAT_INTERVAL_MINUTES", "30"))

# Embeddings / semantic blueprints (Sprint 11)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))

# Auto-retrieval: inject top-K blueprints into every entity prompt.
AUTO_RETRIEVE_ENABLED = os.environ.get("AUTO_RETRIEVE_ENABLED", "true").lower() == "true"
AUTO_RETRIEVE_TOP_K = int(os.environ.get("AUTO_RETRIEVE_TOP_K", "3"))

# Git workflow (Sprint 12 Phase 3). /merge is off by default — set the env
# var to "1" to allow the Telegram bridge to execute `gh pr merge --squash`.
ALLOW_AUTO_MERGE = os.environ.get("HIVE_ALLOW_AUTO_MERGE", "0") == "1"

# Advisor MCP server (Sprint 13)
ADVISOR_ENABLED = os.environ.get("HIVE_ADVISOR_ENABLED", "true").lower() == "true"
ADVISOR_COOLDOWN_SECONDS = int(os.environ.get("HIVE_ADVISOR_COOLDOWN_SECONDS", "300"))
ADVISOR_DAILY_LIMIT = int(os.environ.get("HIVE_ADVISOR_DAILY_LIMIT", "20"))
ADVISOR_CONTEXT_MESSAGES = int(os.environ.get("HIVE_ADVISOR_CONTEXT_MESSAGES", "5"))
