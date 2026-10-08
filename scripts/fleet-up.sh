#!/usr/bin/env bash
# fleet-up: bring the captain's fleet up after a boot. Idempotent; safe to run
# repeatedly (the systemd timer / launchd interval does). See docs/fleet-up.md.
#
#   fleet-up.sh [--dry-run] [--conf FILE] [--only STEP[,STEP...]]
#
# Steps: gateway runtime bnm lavish preview serve firstmate
# Bash 3.2 compatible (macOS).
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
ONLY=""
CONF="${FLEET_UP_CONF:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --conf) shift; CONF="${1:?--conf needs a file}" ;;
    --only) shift; ONLY="${1:?--only needs a list}" ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "fleet-up: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# Environment wins over the config file: remember what was set, source, restore.
VARS="GATEWAY_PORT GATEWAY_START_CMD BNM_PORT BNM_DIR BNM_START_CMD LAVISH_PORT LAVISH_DIR LAVISH_START_CMD PREVIEW_PORT PREVIEW_DIR PREVIEW_START_CMD RUNTIME_START_CMD RUNTIME_STATUS_CMD TAILSCALE_BIN SERVE_MAP FM_DIR FM_LAUNCH_CMD FM_WORKSPACE_LABEL HERDR_BIN HERDR_SERVER_WAIT LOG_DIR PORT_WAIT"
SAVED=""
for v in $VARS; do
  if [ -n "${!v+x}" ]; then
    SAVED="$SAVED$v=$(printf '%q' "${!v}");"
  fi
done
if [ -z "$CONF" ]; then
  if [ -f "$HOME/.config/hive/fleet-up.conf" ]; then
    CONF="$HOME/.config/hive/fleet-up.conf"
  else
    CONF="$HERE/../deploy/fleet-up/fleet-up.conf"
  fi
fi
[ -f "$CONF" ] || { echo "fleet-up: config not found: $CONF" >&2; exit 2; }
# shellcheck disable=SC1090
. "$CONF"
eval "$SAVED"

want() {
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; esac
  return 1
}

log() { printf 'fleet-up: %s\n' "$*"; }

# port_open <port>: true when something accepts TCP on loopback.
port_open() {
  if [ -n "${FLEET_UP_FAKE_PORTS+x}" ]; then
    case " $FLEET_UP_FAKE_PORTS " in *" $1 "*) return 0 ;; esac
    return 1
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# detach <cmd...>: run in the background, surviving this script's exit.
# setsid where it exists (Linux); macOS has none, and launchd's
# AbandonProcessGroup keeps the child alive there.
detach() {
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$@" &
  else
    nohup "$@" &
  fi
}

wait_port() {
  [ "$DRY_RUN" = 1 ] && return 0
  local n=0
  while [ "$n" -lt "$PORT_WAIT" ]; do
    port_open "$1" && return 0
    sleep 1
    n=$((n + 1))
  done
  return 1
}

# ensure_service <name> <port> <dir> <start-cmd>
ensure_service() {
  local name="$1" port="$2" dir="$3" cmd="$4"
  if port_open "$port"; then
    log "$name: already listening on :$port"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "$name: :$port closed; would run (in ${dir:-.}): $cmd"
    return 0
  fi
  mkdir -p "$LOG_DIR"
  log "$name: starting on :$port"
  (
    [ -z "$dir" ] || cd "$dir" || exit 1
    detach bash -c "$cmd" >>"$LOG_DIR/$name.log" 2>&1 </dev/null
  ) || { log "$name: start failed"; return 1; }
  if wait_port "$port"; then
    log "$name: up on :$port"
  else
    log "$name: still not listening on :$port after ${PORT_WAIT}s (see $LOG_DIR/$name.log)"
    return 1
  fi
}

step_gateway() {
  if port_open "$GATEWAY_PORT"; then
    log "gateway: already listening on :$GATEWAY_PORT"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "gateway: :$GATEWAY_PORT closed; would run: $GATEWAY_START_CMD"
    return 0
  fi
  log "gateway: starting via service manager"
  bash -c "$GATEWAY_START_CMD" && wait_port "$GATEWAY_PORT" && log "gateway: up on :$GATEWAY_PORT"
}

# The Hive runtime with Telegram (python -m hive), via hive-telegram.sh.
# Independent of the gateway: a failure here is logged and never blocks a step.
# Not started when unconfigured or when another Hive runtime already runs.
step_runtime() {
  local reason
  if bash -c "$RUNTIME_STATUS_CMD" >/dev/null 2>&1; then
    log "runtime: Hive runtime with Telegram already running"
    return 0
  fi
  if ! reason="$("$HERE/hive-telegram.sh" --check 2>&1)"; then
    log "runtime: not starting; $reason"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "runtime: not running; would run: $RUNTIME_START_CMD"
    return 0
  fi
  log "runtime: starting the Hive runtime with Telegram via the service manager"
  bash -c "$RUNTIME_START_CMD" || log "runtime: start failed (ignored)"
  return 0
}

step_serve() {
  # Tailnet-only. `serve` is idempotent; `funnel` is never used.
  local pair https local_port
  for pair in $SERVE_MAP; do
    https="${pair%%=*}"
    local_port="${pair##*=}"
    if [ "$DRY_RUN" = 1 ]; then
      log "serve: would run: $TAILSCALE_BIN serve --bg --https=$https http://127.0.0.1:$local_port"
    else
      "$TAILSCALE_BIN" serve --bg --https="$https" "http://127.0.0.1:$local_port" \
        || log "serve: failed for :$https"
    fi
  done
}

# --- firstmate ---------------------------------------------------------------

# fm_pane_running: a herdr pane already runs claude in $FM_DIR.
fm_pane_running() {
  local out
  out="$("$HERDR_BIN" pane list 2>/dev/null)" || return 1
  printf '%s' "$out" | jq -e --arg d "$FM_DIR" \
    '[.result.panes[]? | select((.cwd == $d or .foreground_cwd == $d) and (.agent == "claude"))] | length > 0' \
    >/dev/null 2>&1
}

# fm_process_running: a claude process has $FM_DIR as its working directory.
fm_process_running() {
  if [ -n "${FLEET_UP_FAKE_FM_PROCESS+x}" ]; then
    [ "$FLEET_UP_FAKE_FM_PROCESS" = 1 ]
    return
  fi
  local pid cwd
  for pid in $(pgrep -x claude 2>/dev/null); do
    if [ -d "/proc/$pid" ]; then
      cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null)"
    else
      cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    fi
    [ "$cwd" = "$FM_DIR" ] && return 0
  done
  return 1
}

# fm_idle_pane: a pane in an earlier $FM_WORKSPACE_LABEL workspace whose claude
# has exited (e.g. no network at boot), so a rerun retries it in place.
fm_idle_pane() {
  local ids
  ids="$("$HERDR_BIN" workspace list 2>/dev/null | jq -c --arg l "$FM_WORKSPACE_LABEL" \
    '[.result.workspaces[]? | select(.label == $l) | .workspace_id]' 2>/dev/null)" || return 0
  "$HERDR_BIN" pane list 2>/dev/null | jq -r --argjson ids "${ids:-[]}" \
    '[.result.panes[]? | select((.workspace_id as $w | $ids | index($w)) and (.agent != "claude")) | .pane_id][0] // empty' \
    2>/dev/null
}

herdr_server_up() {
  "$HERDR_BIN" status --json 2>/dev/null | jq -e '.server.running == true' >/dev/null 2>&1
}

step_firstmate() {
  if fm_pane_running; then
    log "firstmate: already running in a herdr pane at $FM_DIR; nothing to do"
    return 0
  fi
  if fm_process_running; then
    log "firstmate: a claude process already runs in $FM_DIR; not starting a second"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "firstmate: not running; would ensure the herdr server is up, then:"
    log "  reuse an idle pane in the $FM_WORKSPACE_LABEL workspace, or"
    log "  $HERDR_BIN workspace create --label $FM_WORKSPACE_LABEL --cwd $FM_DIR --no-focus"
    log "  $HERDR_BIN pane run <pane> $FM_LAUNCH_CMD"
    return 0
  fi
  if ! herdr_server_up; then
    log "firstmate: starting the herdr server"
    mkdir -p "$LOG_DIR"
    detach "$HERDR_BIN" server >>"$LOG_DIR/herdr-server.log" 2>&1 </dev/null
    local n=0
    while [ "$n" -lt "$HERDR_SERVER_WAIT" ] && ! herdr_server_up; do
      sleep 1
      n=$((n + 1))
    done
    herdr_server_up || { log "firstmate: herdr server did not come up"; return 1; }
  fi
  local ws pane
  pane="$(fm_idle_pane)"
  if [ -n "$pane" ]; then
    log "firstmate: reusing pane $pane in the $FM_WORKSPACE_LABEL workspace"
  else
    ws="$("$HERDR_BIN" workspace create --label "$FM_WORKSPACE_LABEL" --cwd "$FM_DIR" --no-focus)" \
      || { log "firstmate: workspace create failed"; return 1; }
    pane="$(printf '%s' "$ws" | jq -r '[.. | objects | .pane_id? // empty][0] // empty')"
    [ -n "$pane" ] || { log "firstmate: could not find the new pane id"; return 1; }
  fi
  "$HERDR_BIN" pane run "$pane" "$FM_LAUNCH_CMD" && log "firstmate: started in pane $pane"
}

rc=0
log "config: $CONF$([ "$DRY_RUN" = 1 ] && echo ' (dry run)')"
want gateway && { step_gateway || rc=1; }
want runtime && step_runtime
want bnm && { ensure_service broke-no-more "$BNM_PORT" "$BNM_DIR" "$BNM_START_CMD" || rc=1; }
want lavish && { ensure_service lavish "$LAVISH_PORT" "$LAVISH_DIR" "$LAVISH_START_CMD" || rc=1; }
want preview && { ensure_service preview "$PREVIEW_PORT" "$PREVIEW_DIR" "$PREVIEW_START_CMD" || rc=1; }
want serve && { step_serve || rc=1; }
want firstmate && { step_firstmate || rc=1; }
exit "$rc"
