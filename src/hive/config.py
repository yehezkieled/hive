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

# Web dashboard (0 = disabled)
WEB_PORT = int(os.environ.get("HIVE_WEB_PORT", "0"))

# Auto-management (Sprint 10)
AUTO_COMPACT_ENABLED = os.environ.get("HIVE_AUTO_COMPACT_ENABLED", "true").lower() == "true"
AUTO_COMPACT_THRESHOLD = int(os.environ.get("HIVE_AUTO_COMPACT_THRESHOLD", "50000"))
AUTO_KILL_IDLE_ENABLED = os.environ.get("HIVE_AUTO_KILL_IDLE_ENABLED", "true").lower() == "true"
IDLE_TIMEOUT_MINUTES = int(os.environ.get("HIVE_IDLE_TIMEOUT_MINUTES", "30"))
DAILY_SUMMARY_ENABLED = os.environ.get("HIVE_DAILY_SUMMARY_ENABLED", "true").lower() == "true"
DAILY_SUMMARY_HOUR = int(os.environ.get("HIVE_DAILY_SUMMARY_HOUR", "23"))  # UTC
SUMMARY_CHAT_ID = os.environ.get("HIVE_SUMMARY_CHAT_ID", "")

# Embeddings / semantic blueprints (Sprint 11)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))

# Auto-retrieval: inject top-K blueprints into every entity prompt.
AUTO_RETRIEVE_ENABLED = os.environ.get("AUTO_RETRIEVE_ENABLED", "true").lower() == "true"
AUTO_RETRIEVE_TOP_K = int(os.environ.get("AUTO_RETRIEVE_TOP_K", "3"))
