#!/bin/bash
# services-watchdog.sh — keeps the client-bot stack up: n8n (5678), client1 Chrome (CDP 9350, Xvfb :20, bwrap),
# browser-agent (8770), client1-state helper (8771), northstar-app (web 3100 + worker).
# The box has NO cron daemon and no boot hook, so:
#   services-watchdog.sh once         one check/repair pass (idempotent, quiet when healthy)
#   services-watchdog.sh loop         foreground loop, a pass every ${WATCHDOG_INTERVAL:-300}s (single instance via flock)
#   services-watchdog.sh ensure       start the loop in the background if it is not running, then run one pass
#                                     <- call this from any scheduler / Grok Bot routine / after a reboot
#   services-watchdog.sh status
set -uo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
A=$RUN_DIR; B=$STACK_DIR/bin; L=$A/logs/services-watchdog.log; LOCK=$A/locks/services-watchdog.lock
PIDF=$A/state/services-watchdog.pid; HB=$A/state/services-watchdog.heartbeat
mkdir -p "$A/logs" "$A/locks" "$A/state"
log(){ echo "[$(date '+%F %T IST')] $*" >> "$L"; }

pass(){
  local fixed=0
  if ! curl -fsS -m 5 http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
    log "n8n DOWN -> n8n-up.sh"; "$B/n8n-up.sh" >> "$L" 2>&1 && log "n8n up" || log "ERROR n8n start failed"; fixed=1; fi
  if ! curl -s -m 4 http://127.0.0.1:9350/json/version 2>/dev/null | grep -q Browser; then
    log "client1 chrome DOWN -> start"; "$B/client1-chrome.sh" start >> "$L" 2>&1 || log "ERROR client1 chrome start failed"; fixed=1; fi
  if ! curl -s -m 5 http://127.0.0.1:8770/health 2>/dev/null | grep -q '"service":"browser-agent"'; then
    log "browser-agent DOWN -> start"; "$B/browser-agent-up.sh" start >> "$L" 2>&1 || log "ERROR browser-agent start failed"; fixed=1; fi
  if ! curl -s -m 4 http://127.0.0.1:8771/health 2>/dev/null | grep -q '"service": *"client1-state"'; then
    log "client1-state DOWN -> start"; "$B/client1-state-up.sh" start >> "$L" 2>&1 || log "ERROR client1-state start failed"; fixed=1; fi
  if ! "$B/northstar-app-up.sh" status >/dev/null 2>&1; then
    log "northstar-app DOWN -> start"; "$B/northstar-app-up.sh" start >> "$L" 2>&1 || log "ERROR northstar-app start failed"; fixed=1; fi
  # v0.5 self-heal supervisor (own flock; no-op if already running)
  python3 "$(dirname "$B")/selfheal/selfheal.py" ensure >/dev/null 2>&1 || log "ERROR selfheal ensure failed"
  date '+%F %T IST' > "$HB"
  [ "$fixed" = 1 ] || [ "${VERBOSE:-0}" = 1 ] && log "pass done (fixed=$fixed)"
  return 0
}
loop_alive(){ [ -s "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && grep -q services-watchdog "/proc/$(cat "$PIDF")/cmdline" 2>/dev/null; }

case "${1:-once}" in
  once) pass ;;
  loop)
    exec 9>"$LOCK"; flock -n 9 || { echo "loop already running"; exit 0; }
    echo $$ > "$PIDF"; log "loop start pid=$$ interval=${WATCHDOG_INTERVAL:-300}s"
    while :; do pass; sleep "${WATCHDOG_INTERVAL:-300}"; done ;;
  ensure)
    if ! loop_alive; then setsid nohup "$0" loop >> "$L" 2>&1 < /dev/null & sleep 1; log "ensure: loop (re)started"; fi
    pass; echo "watchdog loop $(loop_alive && echo "running pid $(cat "$PIDF")" || echo NOT running); last pass $(cat "$HB")" ;;
  status)
    echo "loop: $(loop_alive && echo "running pid $(cat "$PIDF")" || echo 'NOT running')  last pass: $(cat "$HB" 2>/dev/null || echo never)"
    curl -fsS -m 5 http://127.0.0.1:5678/healthz >/dev/null 2>&1 && echo "n8n: up" || echo "n8n: DOWN"
    "$B/client1-chrome.sh" status 2>/dev/null || true
    curl -s -m 5 http://127.0.0.1:8770/health 2>/dev/null | grep -q '"status":"ok"' && echo "browser-agent: up" || echo "browser-agent: DOWN/degraded"
    "$B/client1-state-up.sh" status >/dev/null 2>&1 && echo "client1-state: up" || echo "client1-state: DOWN"
    "$B/northstar-app-up.sh" status 2>/dev/null | tr '\n' ' '; echo ;;
  *) echo "usage: $0 once|loop|ensure|status"; exit 2 ;;
esac
