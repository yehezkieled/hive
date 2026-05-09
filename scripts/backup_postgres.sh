#!/usr/bin/env bash
# Daily logical backup of the Hive Postgres database (Sprint 29).
#
# Runs ``pg_dump`` inside the ``hive-postgres`` container so the dumper
# version always matches the server, then gzips the SQL to
# ``${HIVE_BACKUP_DIR:-$HOME/backups/hive}`` and prunes anything older
# than 14 days. Driven by ``hive-backup.timer`` (systemd-user, daily).

set -euo pipefail

# Resolve repo root (this script lives in scripts/ relative to it).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(dirname -- "$SCRIPT_DIR")"

# Load .env so POSTGRES_USER / POSTGRES_DB are available.
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
else
    echo "ERR: $REPO_ROOT/.env not found" >&2
    exit 1
fi

: "${POSTGRES_USER:?POSTGRES_USER missing from .env}"
: "${POSTGRES_DB:?POSTGRES_DB missing from .env}"

BACKUP_DIR="${HIVE_BACKUP_DIR:-$HOME/backups/hive}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
OUT="$BACKUP_DIR/$TIMESTAMP.sql.gz"

# pg_dump → gzip in one pipe. ``set -o pipefail`` makes the whole
# pipeline fail if pg_dump exits non-zero, so systemd marks the unit
# failed and the journal records why.
docker exec hive-postgres pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --no-owner \
    --no-acl \
    | gzip -9 > "$OUT"

# Retention: keep the last 14 daily dumps.
find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' -mtime +14 -delete

SIZE="$(stat -c %s "$OUT")"
echo "OK $OUT $SIZE"
