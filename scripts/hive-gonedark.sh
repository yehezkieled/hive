#!/usr/bin/env bash
# Run the "gone dark" alarm sender (python -m hive.gonedark: one outbound HTTPS GET
# per interval while the desk and firstmate are up) only when its private config holds
# a sound ping address. See docs/gone-dark.md.
#
#   hive-gonedark.sh          run it; exit 0 with a log line when it must not start
#   hive-gonedark.sh --check  exit 0 if it may start, 1 (with a log line) if not
#
# The address comes only from HIVE_GONEDARK_ENV_FILE (default
# ~/.config/hive/gonedark.env, written by scripts/gonedark-setup.sh, mode 0600). The
# file is read, never sourced. No log line ever holds the address. Bash 3.2 compatible.
set -u

ENV_FILE="${HIVE_GONEDARK_ENV_FILE:-$HOME/.config/hive/gonedark.env}"
STATE_DIR="${HIVE_GONEDARK_STATE_DIR:-$HOME/.local/state/hive-gonedark}"
HIVE_DIR="${HIVE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# The same pattern as hive/gonedark.py URL_RE.
URL_RE='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%!&+,;=:@/?#-]*)?$'

log() { printf 'hive-gonedark: %s\n' "$*" >&2; }

MODE="${1:-}"

skip() {
  log "skipping: $*"
  [ "$MODE" = "--check" ] && exit 1
  exit 0
}

# dotenv_value KEY FILE: KEY's value in a dotenv file (read, never sourced).
dotenv_value() {
  sed -n "s/^[[:space:]]*$1=//p" "$2" | tail -n 1 | sed -e 's/[[:space:]]*$//'
}

file_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null
}

[ -f "$ENV_FILE" ] || skip "off: no ping address configured (run scripts/gonedark-setup.sh set)"
case "$(file_mode "$ENV_FILE")" in
  600 | 400) ;;
  *) skip "$ENV_FILE is readable by other users; run scripts/gonedark-setup.sh set again (or chmod 600 it)" ;;
esac
HIVE_GONEDARK_URL="$(dotenv_value HIVE_GONEDARK_URL "$ENV_FILE")"
[ -n "${HIVE_GONEDARK_INTERVAL_S:-}" ] \
  || HIVE_GONEDARK_INTERVAL_S="$(dotenv_value HIVE_GONEDARK_INTERVAL_S "$ENV_FILE")"
[ -n "$HIVE_GONEDARK_INTERVAL_S" ] || HIVE_GONEDARK_INTERVAL_S=300

[ -n "$HIVE_GONEDARK_URL" ] || skip "off: no ping address in $ENV_FILE (run scripts/gonedark-setup.sh set)"
if [ "${#HIVE_GONEDARK_URL}" -gt 2048 ] || ! [[ "$HIVE_GONEDARK_URL" =~ $URL_RE ]]; then
  skip "the ping address in $ENV_FILE is not a plain https:// address (run scripts/gonedark-setup.sh set)"
fi
if ! [[ "$HIVE_GONEDARK_INTERVAL_S" =~ ^[0-9]{2,4}$ ]] \
  || [ "$HIVE_GONEDARK_INTERVAL_S" -lt 60 ] || [ "$HIVE_GONEDARK_INTERVAL_S" -gt 3600 ]; then
  skip "HIVE_GONEDARK_INTERVAL_S must be whole seconds, 60 to 3600"
fi

[ "$MODE" = "--check" ] && exit 0

export HIVE_GONEDARK_URL HIVE_GONEDARK_INTERVAL_S HIVE_GONEDARK_STATE_DIR="$STATE_DIR"
cd "$HIVE_DIR" || exit 1
exec "$HIVE_DIR/.venv/bin/python" -m hive.gonedark
