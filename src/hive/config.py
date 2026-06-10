"""Hive configuration — paths, defaults, environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv


def _mask_dsn(dsn: str) -> str:
    """Return *dsn* with any password component replaced by ``***``.

    Keeps scheme/user/host/port/path intact so the masked form is still
    readable in logs. Robust to weird passwords because ``urlparse`` does
    the splitting.
    """
    parsed = urlparse(dsn)
    if parsed.password is None:
        return dsn
    user = parsed.username or ""
    host = parsed.hostname or ""
    netloc = f"{user}:***@{host}"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


class _MaskedDSN(str):
    """``str`` subclass whose ``repr``/``str``/``format`` mask any password.

    asyncpg / psycopg consume the underlying char data directly, so the
    real DSN still works for connections. Any code path that logs or
    formats the value (``print``, ``%s``, ``%r``, ``f"{dsn}"``) sees the
    masked form.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return repr(_mask_dsn(str.__str__(self)))

    def __str__(self) -> str:
        return _mask_dsn(str.__str__(self))

    def __format__(self, format_spec: str) -> str:
        return format(_mask_dsn(str.__str__(self)), format_spec)


# Load .env file (must happen before reading env vars)
load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PERSONALITIES_DIR = PROJECT_ROOT / "personalities"
WORKTREES_DIR = PROJECT_ROOT / "worktrees"
BLUEPRINTS_DIR = DATA_DIR / "blueprints"
UPLOADS_DIR = DATA_DIR / "uploads"

# Ensure runtime directories exist
DATA_DIR.mkdir(exist_ok=True)
WORKTREES_DIR.mkdir(exist_ok=True)
BLUEPRINTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

# Database — PostgreSQL via asyncpg
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "hive")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "hive")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "hive")
POSTGRES_DSN = _MaskedDSN(
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
DEFAULT_MODEL = os.environ.get("HIVE_DEFAULT_MODEL", "opus")
MAX_CONCURRENT_SESSIONS = int(os.environ.get("HIVE_MAX_SESSIONS", "3"))
# Absolute path (or bare name) of the `claude` binary the fleet spawns. The
# service PATH omits ~/.local/bin, so a bare "claude" silently resolves to the
# stale npm global; pointing this at the native self-updating symlink keeps the
# fleet on the same install dev tests against (Ticket 009). Default "claude"
# preserves the legacy PATH-lookup behavior when the knob is unset.
CLAUDE_BINARY = os.path.expanduser(os.environ.get("HIVE_CLAUDE_BINARY", "claude"))

# Default maestro
DEFAULT_MAESTRO = os.environ.get("HIVE_DEFAULT_MAESTRO", "otter")

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

# Email digest (Sprint 15 Phase 5). When enabled but SMTP* unset, the
# digest runs in console mode — events are logged instead of sent — so
# the channel is testable on dev hosts without real credentials.
EMAIL_ENABLED = os.environ.get("HIVE_EMAIL_ENABLED", "false").lower() == "true"
EMAIL_TO = os.environ.get("HIVE_EMAIL_TO", "")
SMTP_HOST = os.environ.get("HIVE_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("HIVE_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("HIVE_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("HIVE_SMTP_PASSWORD", "")
EMAIL_DIGEST_INTERVAL_MINUTES = int(os.environ.get("HIVE_EMAIL_DIGEST_INTERVAL_MINUTES", "60"))
EMAIL_DIGEST_BUFFER_SIZE = int(os.environ.get("HIVE_EMAIL_DIGEST_BUFFER_SIZE", "20"))

# Embeddings / semantic blueprints (Sprint 11; provider switched to Voyage in Sprint 16)
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "voyage-multimodal-3")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))

# Blueprint chunking (Sprint 26). Long blueprints are split into roughly
# ``BLUEPRINT_CHUNK_TOKENS``-sized chunks before embedding, so retrieval
# matches against the most relevant section instead of one whole-body
# vector. ``BLUEPRINT_CHUNK_OVERLAP_TOKENS`` is how much of chunk N's tail
# is prepended to chunk N+1, so a fact straddling a boundary still appears
# in one full chunk. ``len(text) // 4`` is the token-count heuristic
# (Voyage ships no public tokeniser; ~4 chars/token is the standard rule).
BLUEPRINT_CHUNK_TOKENS = int(os.environ.get("HIVE_BLUEPRINT_CHUNK_TOKENS", "500"))
BLUEPRINT_CHUNK_OVERLAP_TOKENS = int(os.environ.get("HIVE_BLUEPRINT_CHUNK_OVERLAP_TOKENS", "50"))

# Auto-retrieval (Sprint 11/18, dialed down in Sprint 27). The auto-block
# is now a thin safety net — top_k=1 + first-turn only — so junior agents
# always get *some* context for their opening prompt while smarter agents
# reach for the ``search_knowledge`` MCP tool when they need more.
AUTO_RETRIEVE_ENABLED = os.environ.get("AUTO_RETRIEVE_ENABLED", "true").lower() == "true"
AUTO_RETRIEVE_TOP_K = int(os.environ.get("AUTO_RETRIEVE_TOP_K", "1"))
# Maximum cosine distance for an auto-retrieved blueprint to be prepended.
# 0 = identical, 2 = opposite. Tighter default (was 0.6) since the agent
# can search again interactively if the auto-context misses.
AUTO_RETRIEVE_MAX_DISTANCE = float(os.environ.get("AUTO_RETRIEVE_MAX_DISTANCE", "0.5"))
# Sprint 27: only auto-prepend on the first prompt of a fresh entity
# activation (when ``entity.session_id is None``). Subsequent turns rely on
# the agent calling ``search_knowledge`` itself.
AUTO_RETRIEVE_FIRST_TURN_ONLY = (
    os.environ.get("AUTO_RETRIEVE_FIRST_TURN_ONLY", "true").lower() == "true"
)

# Git workflow (Sprint 12 Phase 3). /merge is off by default — set the env
# var to "1" to allow the Telegram bridge to execute `gh pr merge --squash`.
ALLOW_AUTO_MERGE = os.environ.get("HIVE_ALLOW_AUTO_MERGE", "0") == "1"

# Attachments (Sprint 17). 20 MB matches Telegram bot API getFile cap.
UPLOAD_MAX_BYTES = int(os.environ.get("HIVE_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))

# Attachment embeddings (Sprint 18, chunked in Sprint 28). Long text/PDF
# attachments are split into roughly ``ATTACHMENT_CHUNK_TOKENS``-sized chunks
# (mirrors Sprint 26's blueprint chunking — same ``chunking.split_blueprint``
# splitter). ``ATTACHMENT_EMBED_MAX_CHARS`` is now a per-document soft cap
# applied before the splitter runs so a 200-page PDF doesn't OOM the chunker;
# the per-chunk size is bounded by ``ATTACHMENT_CHUNK_TOKENS``.
ATTACHMENT_EMBED_MAX_CHARS = int(os.environ.get("HIVE_ATTACHMENT_EMBED_MAX_CHARS", "32000"))
ATTACHMENT_CHUNK_TOKENS = int(os.environ.get("HIVE_ATTACHMENT_CHUNK_TOKENS", "500"))
ATTACHMENT_CHUNK_OVERLAP_TOKENS = int(os.environ.get("HIVE_ATTACHMENT_CHUNK_OVERLAP_TOKENS", "50"))
# When true, auto-retrieve also queries the attachments table and renders
# a separate "Relevant uploaded files" block alongside blueprint hits.
AUTO_RETRIEVE_INCLUDE_ATTACHMENTS = (
    os.environ.get("HIVE_AUTO_RETRIEVE_INCLUDE_ATTACHMENTS", "true").lower() == "true"
)

# QuotaMonitor. Polls Anthropic's plan-quota OAuth endpoint every
# HIVE_QUOTA_POLL_SECONDS and alerts at 80/90/100 thresholds on both
# the 5-hour and 7-day rolling windows. See
# docs/adr/0002-quota-from-undocumented-oauth-endpoint.md for the
# data-source decision.
HIVE_QUOTA_POLL_SECONDS = float(os.environ.get("HIVE_QUOTA_POLL_SECONDS", "180"))
HIVE_CLAUDE_CREDENTIALS_PATH = Path(
    os.environ.get(
        "HIVE_CLAUDE_CREDENTIALS_PATH",
        str(Path.home() / ".claude" / ".credentials.json"),
    )
)

# Maestro autonomy / priority scheduler (Sprint 19). The scheduler pokes
# each alive maestro every PRIORITY_EVAL_INTERVAL_MINUTES with a "facts"
# prompt (free slots, pending tasks by priority, org snapshot, 24h cost).
# The maestro decides allocation via spawn_team / spawn_worker /
# kill_entity actions. AUTONOMOUS_SPAWN_LIMIT caps how many spawns each
# maestro can do per eval window — runaway-loop guard.
PRIORITY_EVAL_INTERVAL_MINUTES = int(os.environ.get("HIVE_PRIORITY_EVAL_INTERVAL_MINUTES", "120"))
AUTONOMOUS_SPAWN_LIMIT = int(os.environ.get("HIVE_AUTONOMOUS_SPAWN_LIMIT", "3"))
