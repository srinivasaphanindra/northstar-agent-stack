#!/bin/bash
# browser-agent-up.sh — idempotent start of the browser-use HTTP service on 127.0.0.1:8770.
# Usage: browser-agent-up.sh [start|stop|restart|status]
# Needs: example-client Chrome on CDP 9350 (bin/client-chrome.sh, started here if down), OPENROUTER_API_KEY
# (from $STACK_DIR/browser-agent/.env chmod 600, else from the environment; never printed). Model override: BROWSER_AGENT_MODEL.
set -uo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
APP=$STACK_DIR/browser-agent
LOG=$RUN_DIR/logs/browser-agent.log
PIDF=$APP/service.pid
PORT=8770
mkdir -p "$(dirname "$LOG")"
health(){ curl -s -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"service":"browser-agent"'; }
running(){ [ -s "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-start}" in
  status) if health; then curl -s -m 5 "http://127.0.0.1:$PORT/health"; echo; else echo "browser-agent DOWN"; exit 1; fi; exit 0 ;;
  stop) running && kill "$(cat "$PIDF")" && echo "stopped $(cat "$PIDF")"; rm -f "$PIDF"; exit 0 ;;
  restart) "$0" stop; sleep 2; exec "$0" start ;;
  start) ;;
  *) echo "usage: $0 [start|stop|restart|status]"; exit 2 ;;
esac
if health; then echo "already up: 127.0.0.1:$PORT"; exit 0; fi
$STACK_DIR/bin/client-chrome.sh start >/dev/null 2>&1 || echo "WARN: example-client Chrome (CDP 9350) not up; service will report degraded"
# Secrets/config: $STACK_DIR/browser-agent/.env (chmod 600, KEY=VALUE lines) wins, so this works from any shell/cron.
ENVF=$APP/.env
if [ -f "$ENVF" ]; then
  [ "$(stat -c %a "$ENVF")" = 600 ] || chmod 600 "$ENVF"
  while IFS='=' read -r k v; do
    case "$k" in OPENROUTER_API_KEY|BROWSER_AGENT_MODEL|BROWSER_AGENT_CDP_URL|BROWSER_AGENT_MAX_STEPS_CAP|BROWSER_AGENT_TASK_TIMEOUT|BROWSER_AGENT_BASE_URL|BROWSER_AGENT_ALLOWLIST_URL)
      v=${v%$'\r'}; v=${v#\"}; v=${v%\"}; [ -n "$v" ] && export "$k=$v";; esac
  done < <(grep -E '^[A-Z_]+=' "$ENVF")
fi
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "ERROR: OPENROUTER_API_KEY not available (put it in $ENVF, chmod 600)" >&2; exit 1; }
# 2026-10-12 guardrails: meter LLM spend through state-helper (127.0.0.1:8771) and enforce its domain allow-list.
# Set BROWSER_AGENT_BASE_URL=https://openrouter.ai/api/v1 in .env to bypass the meter (not recommended).
export BROWSER_AGENT_BASE_URL="${BROWSER_AGENT_BASE_URL:-http://127.0.0.1:8771/or/browser/v1}"
export BROWSER_AGENT_ALLOWLIST_URL="${BROWSER_AGENT_ALLOWLIST_URL:-http://127.0.0.1:8771/guard/allowlist}"
export BROWSER_AGENT_LOG_TSV="${BROWSER_AGENT_LOG_TSV:-$RUN_DIR/logs/browser-agent.tsv}"
export ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false PYTHONUNBUFFERED=1
echo "[$(date '+%F %T IST')] start browser-agent port=$PORT" >> "$LOG"
cd "$APP"
setsid nohup "$APP/.venv/bin/uvicorn" service:app --host 127.0.0.1 --port $PORT --log-level info \
  >> "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDF"
for i in $(seq 1 20); do sleep 1; if health; then echo "browser-agent up: 127.0.0.1:$PORT (pid $(cat "$PIDF"))"; exit 0; fi; done
echo "ERROR: browser-agent did not become healthy (see $LOG)" >&2; exit 1
