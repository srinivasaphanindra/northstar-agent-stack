#!/bin/bash
# client1-state-up.sh [start|stop|restart|status] — local helper for the n8n Client1 bot (127.0.0.1:8771).
# Memory (SQLite), client notes, approval proposals, engagement queue + ledger + kill switch. No secrets used.
set -uo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
# Runtime data (SQLite memory, notes, ledgers, audit) lives in $RUN_DIR/client1, config in the repo.
C1=$RUN_DIR/client1; mkdir -p "$C1"
[ -f "$C1/notes.md" ] || cp "$STACK_DIR/clients/client1/notes.example.md" "$C1/notes.md"
export C1_CLIENT_DIR="$C1" C1_DB="$C1/bot-state.sqlite" C1_NOTES="$C1/notes.md" C1_PAUSE="$RUN_DIR/state/PAUSE-client1-engage" \
  C1_PAUSE_ALL="$RUN_DIR/state/PAUSE-client1-all" C1_LEDGER="$RUN_DIR/state/client1-engage-ledger.tsv" C1_LOG="$RUN_DIR/logs/client1-state.log" \
  C1_AUDIT="$C1/audit-log.tsv" C1_UPSELL="$C1/upsell-log.md" C1_GUARDRAILS="${C1_GUARDRAILS:-$STACK_DIR/clients/client1/guardrails.yaml}" \
  C1_NOTIFY="${C1_NOTIFY:-0}"
APP=$STACK_DIR/client1-state; PIDF=$APP/server.pid; LOG=$RUN_DIR/logs/client1-state.out; PORT=${C1_PORT:-8771}; export C1_PORT=$PORT
health(){ curl -s -m 4 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"service": *"client1-state"'; }
running(){ [ -s "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-start}" in
  status) health && curl -s -m 4 "http://127.0.0.1:$PORT/health" && echo || { echo "client1-state DOWN"; exit 1; }; exit 0 ;;
  stop) running && kill "$(cat "$PIDF")" && echo "stopped $(cat "$PIDF")"; rm -f "$PIDF"; exit 0 ;;
  restart) "$0" stop; sleep 1; exec "$0" start ;;
  start) ;;
  *) echo "usage: $0 [start|stop|restart|status]"; exit 2 ;;
esac
health && { echo "already up: 127.0.0.1:$PORT"; exit 0; }
cd "$APP"; setsid nohup python3 "$APP/server.py" >> "$LOG" 2>&1 < /dev/null & echo $! > "$PIDF"
for i in $(seq 1 10); do sleep 1; health && { echo "client1-state up: 127.0.0.1:$PORT (pid $(cat "$PIDF"))"; exit 0; }; done
echo "ERROR: client1-state not healthy (see $LOG)" >&2; exit 1
