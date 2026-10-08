#!/usr/bin/env bash
# Run the Hive Telegram bot (python -m hive) only when its config is present.
#
#   hive-telegram.sh          run the bot; exit 0 with a log line when unconfigured
#   hive-telegram.sh --check  exit 0 if configured, 1 (with a log line) if not
#
# The token and allowlist come from the environment or from the env file
# HIVE_TELEGRAM_ENV_FILE (default ~/.config/hive/telegram.env, mode 0600,
# KEY=value lines). Nothing is committed. Bash 3.2 compatible (macOS).
set -u

ENV_FILE="${HIVE_TELEGRAM_ENV_FILE:-$HOME/.config/hive/telegram.env}"
HIVE_DIR="${HIVE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

log() { printf 'hive-telegram: %s\n' "$*" >&2; }

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_ALLOWED_USER_IDS:-}" ]; then
  log "skipping: TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS must both be set (env or $ENV_FILE)"
  [ "${1:-}" = "--check" ] && exit 1
  exit 0
fi

[ "${1:-}" = "--check" ] && exit 0

cd "$HIVE_DIR" || exit 1
exec "$HIVE_DIR/.venv/bin/python" -m hive
