#!/usr/bin/env bash
# Run the Hive runtime with Telegram (python -m hive: the full runtime, not a
# bot-only process) only when its config is present and no other Hive runtime
# is running (never two pollers on one bot token).
#
#   hive-telegram.sh          run it; exit 0 with a log line when it must not start
#   hive-telegram.sh --check  exit 0 if it may start, 1 (with a log line) if not
#
# The token and allowlist come from the environment, the env file
# HIVE_TELEGRAM_ENV_FILE (default ~/.config/hive/telegram.env, mode 0600,
# KEY=value lines), or Hive's own $HIVE_DIR/.env (which python -m hive loads
# itself). Nothing is committed. Bash 3.2 compatible (macOS).
set -u

ENV_FILE="${HIVE_TELEGRAM_ENV_FILE:-$HOME/.config/hive/telegram.env}"
HIVE_DIR="${HIVE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Command-line pattern of a running Hive runtime (hive.service or this one).
RUNTIME_PATTERN=' -m hive$'
RUNTIME_PATTERN="${HIVE_RUNTIME_PATTERN:-$RUNTIME_PATTERN}"

log() { printf 'hive-telegram: %s\n' "$*" >&2; }

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# dotenv_value KEY FILE: KEY's value in a dotenv file (read, never sourced).
dotenv_value() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=[[:space:]]*//p" "$2" \
    | tail -n 1 | sed -e 's/[[:space:]]*$//' -e "s/^[\"']\(.*\)[\"']\$/\1/"
}

if [ -f "$HIVE_DIR/.env" ]; then
  for key in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_IDS; do
    [ -n "${!key:-}" ] || printf -v "$key" '%s' "$(dotenv_value "$key" "$HIVE_DIR/.env")"
  done
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_ALLOWED_USER_IDS:-}" ]; then
  log "skipping: TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS must both be set (env, $ENV_FILE or $HIVE_DIR/.env)"
  [ "${1:-}" = "--check" ] && exit 1
  exit 0
fi

other="$(pgrep -f "$RUNTIME_PATTERN" 2>/dev/null | tr '\n' ' ')"
if [ -n "$other" ]; then
  log "skipping: a Hive runtime is already running (pid ${other% }); never two pollers on one bot token"
  [ "${1:-}" = "--check" ] && exit 1
  exit 0
fi

[ "${1:-}" = "--check" ] && exit 0

cd "$HIVE_DIR" || exit 1
exec "$HIVE_DIR/.venv/bin/python" -m hive
