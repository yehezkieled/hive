#!/usr/bin/env bash
# Turn the "gone dark" alarm on, change its ping address, or turn it off. See
# docs/gone-dark.md.
#
#   gonedark-setup.sh set [--every SECONDS]  ask for the ping address (hidden), save, restart the sender
#   gonedark-setup.sh remove                 delete the config and stop the sender
#   gonedark-setup.sh status                 say whether it is on and how the last ping went (never the address)
#
# The config is HIVE_GONEDARK_ENV_FILE (default ~/.config/hive/gonedark.env): outside
# git, mode 0600 in a 0700 directory, replaced atomically. The address is a secret: it
# is read with a hidden prompt (or from stdin when it is not a terminal) and is never
# printed, logged or passed on a command line. The interval is kept when --every is
# not given (default 300). Bash 3.2 compatible (macOS).
set -u

ENV_FILE="${HIVE_GONEDARK_ENV_FILE:-$HOME/.config/hive/gonedark.env}"
STATE_DIR="${HIVE_GONEDARK_STATE_DIR:-$HOME/.local/state/hive-gonedark}"
if [ "$(uname -s)" = Darwin ]; then
  DEFAULT_RESTART=""
  DEFAULT_STOP=""
else
  DEFAULT_RESTART='systemctl --user restart hive-gonedark.service'
  DEFAULT_STOP='systemctl --user stop hive-gonedark.service'
fi
RESTART_CMD="${HIVE_GONEDARK_RESTART_CMD:-$DEFAULT_RESTART}"
STOP_CMD="${HIVE_GONEDARK_STOP_CMD:-$DEFAULT_STOP}"
# The same pattern as hive/gonedark.py URL_RE.
URL_RE='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%!&+,;=:@/?#-]*)?$'

say() { printf '%s\n' "$*" >&2; }
die() { say "gonedark-setup: $*"; exit 1; }

usage() { sed -n '5,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

restart_service() {
  if [ -n "$RESTART_CMD" ]; then
    bash -c "$RESTART_CMD"
    return $?
  fi
  local domain label plist
  domain="gui/$(id -u)"
  label="$domain/com.hive.gonedark"
  plist="$HOME/Library/LaunchAgents/com.hive.gonedark.plist"
  if ! launchctl print "$label" >/dev/null 2>&1; then
    launchctl bootstrap "$domain" "$plist" >/dev/null 2>&1 || {
      launchctl print "$label" >/dev/null 2>&1 || return 1
    }
  fi
  launchctl kickstart -k "$label"
}

stop_service() {
  if [ -n "$STOP_CMD" ]; then
    bash -c "$STOP_CMD"
    return $?
  fi
  launchctl bootout "gui/$(id -u)/com.hive.gonedark" >/dev/null 2>&1
}

service_is_active() {
  if [ "$(uname -s)" = Darwin ]; then
    launchctl list 2>/dev/null | awk '$3 == "com.hive.gonedark" {found=1} END {exit !found}'
    return $?
  fi
  systemctl --user is-active --quiet hive-gonedark.service >/dev/null 2>&1
}

current_interval() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n 's/^HIVE_GONEDARK_INTERVAL_S=//p' "$ENV_FILE" | tail -n 1
}

valid_interval() {
  [[ "$1" =~ ^[0-9]{2,4}$ ]] && [ "$1" -ge 60 ] && [ "$1" -le 3600 ]
}

cmd_set() {
  local every="" url=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --every) shift; every="${1:?--every needs a number of seconds}" ;;
      *) usage ;;
    esac
    shift
  done
  if [ -z "$every" ]; then
    every="$(current_interval)"
    valid_interval "${every:-x}" || every=300
  fi
  valid_interval "$every" || die "--every must be whole seconds, 60 to 3600. Nothing was saved."

  if [ -t 0 ]; then
    say "Paste the ping address from your dead-man's-switch service. It is not shown while you type."
    IFS= read -r -s -p "Ping address: " url
    printf '\n' >&2
  else
    IFS= read -r url || true
  fi
  url="$(printf '%s' "$url" | tr -d '[:space:]')"
  if [ "${#url}" -gt 2048 ] || ! [[ "$url" =~ $URL_RE ]]; then
    url=""
    die "that is not a plain https:// address (https only, no spaces or quotes). Nothing was saved."
  fi

  local dir tmp
  dir="$(dirname "$ENV_FILE")"
  umask 077
  mkdir -p "$dir" || die "cannot create $dir"
  chmod 700 "$dir"
  tmp="$(mktemp "$dir/.gonedark.env.XXXXXX")" || die "cannot write in $dir"
  {
    printf '# Gone-dark alarm config, written by scripts/gonedark-setup.sh. Never commit or share.\n'
    printf 'HIVE_GONEDARK_URL=%s\n' "$url"
    printf 'HIVE_GONEDARK_INTERVAL_S=%s\n' "$every"
  } >"$tmp" || { rm -f "$tmp"; die "cannot write $ENV_FILE"; }
  chmod 600 "$tmp"
  mv -f "$tmp" "$ENV_FILE" || { rm -f "$tmp"; die "cannot replace $ENV_FILE"; }
  url=""
  say "Saved to $ENV_FILE (mode 600). One ping every $every s while the desk, firstmate and Tailscale are up."
  if restart_service >/dev/null 2>&1; then
    say "The sender was restarted. Check with: $0 status"
  else
    say "Could not restart the sender service; fleet-up starts it within 5 minutes once the service is installed (docs/gone-dark.md)."
  fi
}

cmd_remove() {
  [ $# -eq 0 ] || usage
  local stopped=1
  if ! stop_service >/dev/null 2>&1 && service_is_active; then
    stopped=0
  fi
  if [ -f "$ENV_FILE" ]; then
    rm -f "$ENV_FILE" || die "cannot remove $ENV_FILE"
    say "Removed $ENV_FILE."
  else
    say "No config at $ENV_FILE."
  fi
  if [ "$stopped" -eq 0 ]; then
    say "Could not stop the sender; it keeps pinging until stopped (systemctl --user stop hive-gonedark.service)."
    exit 1
  fi
  say "The gone-dark alarm is off. Your dead-man's-switch service will now alert: pause or delete the check there."
}

cmd_status() {
  [ $# -eq 0 ] || usage
  if [ ! -f "$ENV_FILE" ]; then
    say "Off: no $ENV_FILE. Run: $0 set"
    return 0
  fi
  local mode addr last
  mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null)"
  if grep -q '^HIVE_GONEDARK_URL=https://.' "$ENV_FILE"; then addr="set (hidden)"; else addr="missing"; fi
  say "On. Config: $ENV_FILE (mode $mode)"
  say "Ping address: $addr"
  say "Every: $(current_interval) s"
  if [ -f "$STATE_DIR/last.json" ]; then
    # Only the fixed fields the sender writes: time, result and a fixed reason phrase.
    last="$(sed -n 's/.*"at": *\([0-9]*\).*"result": *"\([a-z]*\)".*"reason": *"\([a-z ]*\)".*/\1 \2 \3/p' \
      "$STATE_DIR/last.json")"
    if [ -n "$last" ]; then
      local when result reason
      read -r when result reason <<<"$last"
      when="$(date -d "@$when" '+%Y-%m-%d %H:%M' 2>/dev/null || date -r "$when" '+%Y-%m-%d %H:%M' 2>/dev/null)"
      say "Last tick: $when, $result${reason:+ ($reason)}"
    fi
  else
    say "Last tick: none yet"
  fi
  return 0
}

case "${1:-}" in
  set) shift; cmd_set "$@" ;;
  remove) shift; cmd_remove "$@" ;;
  status) shift; cmd_status "$@" ;;
  *) usage ;;
esac
