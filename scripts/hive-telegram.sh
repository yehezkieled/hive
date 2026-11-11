#!/usr/bin/env bash
# Run the standalone Telegram bot (python -m hive.telegram: fixed commands only,
# outbound polling, no database) only when its config is present and sound and
# no other Hive process polls the same token. See docs/telegram.md.
#
#   hive-telegram.sh          run it; exit 0 with a log line when it must not start
#   hive-telegram.sh --check  exit 0 if it may start, 1 (with a log line) if not
#
# The token and allowlist come from the environment, then the private config
# HIVE_TELEGRAM_ENV_FILE (default ~/.config/hive/telegram.env, written by
# scripts/telegram-setup.sh, mode 0600), then Hive's own $HIVE_DIR/.env. Files are
# read, never sourced. No log line ever holds the token. Bash 3.2 compatible (macOS).
set -u

ENV_FILE="${HIVE_TELEGRAM_ENV_FILE:-$HOME/.config/hive/telegram.env}"
STATE_DIR="${HIVE_TELEGRAM_STATE_DIR:-$HOME/.local/state/hive-telegram}"
HIVE_DIR="${HIVE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Command line of the Vault runtime (python -m hive), which polls the same token when
# it has one: never two pollers on one bot token.
RUNTIME_PATTERN=' -m hive$'
RUNTIME_PATTERN="${HIVE_RUNTIME_PATTERN:-$RUNTIME_PATTERN}"

log() { printf 'hive-telegram: %s\n' "$*" >&2; }

skip() {
  log "skipping: $*"
  [ "$MODE" = "--check" ] && exit 1
  exit 0
}

MODE="${1:-}"

# dotenv_value KEY FILE: KEY's value in a dotenv file (read, never sourced).
dotenv_value() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=[[:space:]]*//p" "$2" \
    | tail -n 1 | sed -e 's/[[:space:]]*$//' -e "s/^[\"']\(.*\)[\"']\$/\1/"
}

file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -d ' ' -f 1
  else
    printf '%s' "$1" | shasum -a 256 | cut -d ' ' -f 1
  fi
}

from_file() {
  local key
  for key in TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_IDS; do
    [ -n "${!key:-}" ] || printf -v "$key" '%s' "$(dotenv_value "$key" "$1")"
  done
}

if [ -f "$ENV_FILE" ]; then
  case "$(file_mode "$ENV_FILE")" in
    600 | 400) ;;
    *) skip "$ENV_FILE is readable by other users; run scripts/telegram-setup.sh set again (or chmod 600 it)" ;;
  esac
  from_file "$ENV_FILE"
fi
[ -f "$HIVE_DIR/.env" ] && from_file "$HIVE_DIR/.env"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_ALLOWED_USER_IDS:-}" ]; then
  skip "TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS must both be set (run scripts/telegram-setup.sh set)"
fi
if ! [[ "$TELEGRAM_BOT_TOKEN" =~ ^[0-9]{3,}:[A-Za-z0-9_-]{30,}$ ]]; then
  skip "TELEGRAM_BOT_TOKEN does not look like a BotFather token (run scripts/telegram-setup.sh set)"
fi
if ! [[ "$TELEGRAM_ALLOWED_USER_IDS" =~ ^[0-9]{1,20}(,[0-9]{1,20})*$ ]]; then
  skip "TELEGRAM_ALLOWED_USER_IDS must be numeric user ids, comma separated (run scripts/telegram-setup.sh set)"
fi
if [[ ",$TELEGRAM_ALLOWED_USER_IDS," =~ ,0+, ]]; then
  skip "TELEGRAM_ALLOWED_USER_IDS must not contain 0 (run scripts/telegram-setup.sh set)"
fi
if [ -f "$STATE_DIR/rejected-token.sha256" ] \
  && [ "$(cat "$STATE_DIR/rejected-token.sha256")" = "$(sha256 "$TELEGRAM_BOT_TOKEN")" ]; then
  skip "Telegram rejected this bot token; set a new one with scripts/telegram-setup.sh set"
fi

other="$(pgrep -f "$RUNTIME_PATTERN" 2>/dev/null | tr '\n' ' ')"
if [ -n "$other" ]; then
  skip "a Hive runtime is already running (pid ${other% }); never two pollers on one bot token"
fi

[ "$MODE" = "--check" ] && exit 0

export TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_IDS HIVE_TELEGRAM_STATE_DIR="$STATE_DIR"
cd "$HIVE_DIR" || exit 1
exec "$HIVE_DIR/.venv/bin/python" -m hive.telegram
