#!/usr/bin/env bash
# Idempotent n8n launcher (self-hosted, 127.0.0.1:5678). Logs: $RUN_DIR/logs/n8n.log
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
N8N_HOME="$STACK_DIR/n8n"
LOG="$RUN_DIR/logs/n8n.log"
PIDFILE="$RUN_DIR/state/n8n.pid"
mkdir -p "$RUN_DIR/logs"
[ -f "$N8N_HOME/.env" ] || { echo "ERROR: create n8n/.env from n8n/.env.example" >&2; exit 1; }
set -a; . "$N8N_HOME/.env"; set +a
NP=${N8N_PORT:-5678}

# Optional: use fnm-managed Node 24 when fnm is installed; otherwise the node on PATH is used.
if command -v fnm >/dev/null 2>&1; then eval "$(fnm env --shell bash)"; fnm use 24 >/dev/null 2>&1 || true; fi

# Already running?
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "n8n already running (pid $(cat "$PIDFILE"))"; exit 0
fi
if curl -fsS -m 3 http://127.0.0.1:$NP/healthz >/dev/null 2>&1; then
  echo "n8n already answering on 127.0.0.1:$NP"; exit 0
fi

export N8N_USER_FOLDER=${N8N_USER_FOLDER:-$RUN_DIR/n8n}; mkdir -p "$N8N_USER_FOLDER"
cd "$N8N_HOME"
echo "=== $(date '+%F %T %Z') starting n8n ===" >> "$LOG"
setsid nohup "$N8N_HOME/node_modules/.bin/n8n" start >> "$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

for i in $(seq 1 90); do
  if curl -fsS -m 2 http://127.0.0.1:$NP/healthz >/dev/null 2>&1; then
    echo "n8n up (pid $(cat "$PIDFILE")) after ${i}s"; exit 0
  fi
  kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { echo "n8n exited; see $LOG"; exit 1; }
  sleep 1
done
echo "n8n started (pid $(cat "$PIDFILE")) but healthz not ok after 90s; see $LOG"; exit 1
