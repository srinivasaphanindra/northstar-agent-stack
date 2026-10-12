#!/bin/bash
# state-helper-up.sh [start|stop|restart|status] — local state helper for ONE client instance's n8n bot.
# Env: CLIENT_KEY (default example-client), CS_PORT (default 8771). One helper per client instance.
# Memory (SQLite), client notes, approval proposals, engagement queue + ledger + kill switch. No secrets used.
set -uo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
# Runtime data (SQLite memory, notes, ledgers, audit) lives in $RUN_DIR/<client key>, templates in the repo.
K=${CLIENT_KEY:-example-client}
C1=$RUN_DIR/$K; mkdir -p "$C1"
[ -f "$C1/notes.md" ] || cp "$STACK_DIR/templates/notes.example.md" "$C1/notes.md"
export CS_CLIENT_DIR="$C1" CS_DB="$C1/bot-state.sqlite" CS_NOTES="$C1/notes.md" CS_PAUSE="$RUN_DIR/state/PAUSE-$K-engage" \
  CS_PAUSE_ALL="$RUN_DIR/state/PAUSE-$K-all" CS_LEDGER="$RUN_DIR/state/$K-engage-ledger.tsv" CS_LOG="$RUN_DIR/logs/$K-state-helper.log" \
  CS_AUDIT="$C1/audit-log.tsv" CS_UPSELL="$C1/upsell-log.md" CS_GUARDRAILS="${CS_GUARDRAILS:-$STACK_DIR/templates/guardrails.example.yaml}" \
  CS_NOTIFY="${CS_NOTIFY:-0}"
APP=$STACK_DIR/state-helper; PIDF=$C1/server.pid; LOG=$RUN_DIR/logs/$K-state-helper.out; PORT=${CS_PORT:-8771}; export CS_PORT=$PORT
health(){ curl -s -m 4 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"service": *"state-helper"'; }
running(){ [ -s "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-start}" in
  status) health && curl -s -m 4 "http://127.0.0.1:$PORT/health" && echo || { echo "state-helper DOWN"; exit 1; }; exit 0 ;;
  stop) running && kill "$(cat "$PIDF")" && echo "stopped $(cat "$PIDF")"; rm -f "$PIDF"; exit 0 ;;
  restart) "$0" stop; sleep 1; exec "$0" start ;;
  start) ;;
  *) echo "usage: $0 [start|stop|restart|status]"; exit 2 ;;
esac
health && { echo "already up: 127.0.0.1:$PORT"; exit 0; }
cd "$APP"; setsid nohup python3 "$APP/server.py" >> "$LOG" 2>&1 < /dev/null & echo $! > "$PIDF"
for i in $(seq 1 10); do sleep 1; health && { echo "state-helper up: 127.0.0.1:$PORT (pid $(cat "$PIDF"))"; exit 0; }; done
echo "ERROR: state-helper not healthy (see $LOG)" >&2; exit 1
