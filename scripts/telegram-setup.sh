#!/usr/bin/env bash
# Place, rotate or remove the Telegram bot's token and allowlist. See docs/telegram.md.
#
#   telegram-setup.sh set [--user-id ID[,ID...]]  ask for the token (hidden), save, restart the bot
#   telegram-setup.sh remove                      delete the config and stop the bot
#   telegram-setup.sh status                      say what is configured (never the token)
#
# The config is HIVE_TELEGRAM_ENV_FILE (default ~/.config/hive/telegram.env): outside
# git, mode 0600 in a 0700 directory, replaced atomically. The token is read with a
# hidden prompt (or from stdin when it is not a terminal) and is never printed,
# logged or passed on a command line. Rotating is `set` again; the allowlist is kept
# when you press Enter. Bash 3.2 compatible (macOS).
set -u

ENV_FILE="${HIVE_TELEGRAM_ENV_FILE:-$HOME/.config/hive/telegram.env}"
STATE_DIR="${HIVE_TELEGRAM_STATE_DIR:-$HOME/.local/state/hive-telegram}"
if [ "$(uname -s)" = Darwin ]; then
  DEFAULT_RESTART="launchctl kickstart -k gui/$(id -u)/com.hive.telegram"
  DEFAULT_STOP="launchctl kill TERM gui/$(id -u)/com.hive.telegram"
else
  DEFAULT_RESTART='systemctl --user restart hive-telegram.service'
  DEFAULT_STOP='systemctl --user stop hive-telegram.service'
fi
RESTART_CMD="${HIVE_TELEGRAM_RESTART_CMD:-$DEFAULT_RESTART}"
STOP_CMD="${HIVE_TELEGRAM_STOP_CMD:-$DEFAULT_STOP}"

say() { printf '%s\n' "$*" >&2; }
die() { say "telegram-setup: $*"; exit 1; }

usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

current_ids() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n 's/^TELEGRAM_ALLOWED_USER_IDS=//p' "$ENV_FILE" | tail -n 1
}

cmd_set() {
  local ids="" token="" old
  while [ $# -gt 0 ]; do
    case "$1" in
      --user-id) shift; ids="${1:?--user-id needs an id}" ;;
      *) usage ;;
    esac
    shift
  done

  if [ -t 0 ]; then
    say "Paste the bot token from BotFather. It is not shown while you type."
    IFS= read -r -s -p "Bot token: " token
    printf '\n' >&2
  else
    IFS= read -r token || true
  fi
  token="$(printf '%s' "$token" | tr -d '[:space:]')"
  [[ "$token" =~ ^[0-9]{3,}:[A-Za-z0-9_-]{30,}$ ]] \
    || die "that does not look like a BotFather token (digits, a colon, then 30+ letters). Nothing was saved."

  if [ -z "$ids" ]; then
    old="$(current_ids)"
    if [ -t 0 ]; then
      if [ -n "$old" ]; then
        read -r -p "Allowed Telegram user id(s) [Enter keeps $old]: " ids
      else
        read -r -p "Allowed Telegram user id(s), comma separated: " ids
      fi
    else
      IFS= read -r ids || true
    fi
    ids="$(printf '%s' "$ids" | tr -d '[:space:]')"
    [ -n "$ids" ] || ids="$old"
  fi
  [[ "$ids" =~ ^[0-9]{1,20}(,[0-9]{1,20})*$ ]] \
    || die "the allowed id must be your numeric Telegram user id (comma separated for more). Nothing was saved."

  local dir tmp
  dir="$(dirname "$ENV_FILE")"
  umask 077
  mkdir -p "$dir" || die "cannot create $dir"
  chmod 700 "$dir"
  tmp="$(mktemp "$dir/.telegram.env.XXXXXX")" || die "cannot write in $dir"
  {
    printf '# Telegram bot config, written by scripts/telegram-setup.sh. Never commit or share.\n'
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token"
    printf 'TELEGRAM_ALLOWED_USER_IDS=%s\n' "$ids"
  } >"$tmp" || { rm -f "$tmp"; die "cannot write $ENV_FILE"; }
  chmod 600 "$tmp"
  mv -f "$tmp" "$ENV_FILE" || { rm -f "$tmp"; die "cannot replace $ENV_FILE"; }
  token=""
  rm -f "$STATE_DIR/rejected-token.sha256"
  say "Saved to $ENV_FILE (mode 600). Allowed id(s): $ids."
  if bash -c "$RESTART_CMD" >/dev/null 2>&1; then
    say "The bot was restarted with the new config. Send it /health to check."
  else
    say "Could not restart the bot service; fleet-up starts it within 5 minutes once the service is installed (docs/telegram.md)."
  fi
}

cmd_remove() {
  [ $# -eq 0 ] || usage
  bash -c "$STOP_CMD" >/dev/null 2>&1 || true
  if [ -f "$ENV_FILE" ]; then
    rm -f "$ENV_FILE" || die "cannot remove $ENV_FILE"
    say "Removed $ENV_FILE and stopped the bot. It stays off until you run: $0 set"
  else
    say "No config at $ENV_FILE. The bot is stopped."
  fi
  say "To also cut off the old token, revoke it in BotFather (/revoke)."
}

cmd_status() {
  [ $# -eq 0 ] || usage
  if [ ! -f "$ENV_FILE" ]; then
    say "Not configured: no $ENV_FILE. Run: $0 set"
    return 0
  fi
  local mode token_state
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null)"
  if grep -q '^TELEGRAM_BOT_TOKEN=.' "$ENV_FILE"; then token_state="set (hidden)"; else token_state="missing"; fi
  say "Config: $ENV_FILE (mode $mode)"
  say "Token: $token_state"
  say "Allowed id(s): $(current_ids)"
  [ -f "$STATE_DIR/rejected-token.sha256" ] && say "Telegram rejected the last token tried; run: $0 set"
  return 0
}

case "${1:-}" in
  set) shift; cmd_set "$@" ;;
  remove) shift; cmd_remove "$@" ;;
  status) shift; cmd_status "$@" ;;
  *) usage ;;
esac
