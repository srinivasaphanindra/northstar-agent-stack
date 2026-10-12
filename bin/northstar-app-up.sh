#!/bin/bash
# northstar-app-up.sh [start|stop|restart|status|build] — Northstar AaaS app (phase 1, local demo).
#   web:    Next.js standalone server on 127.0.0.1:3100 (APP: ~/apps/northstar-app, config in its .env)
#   worker: job queue + Telegram/Slack pollers (tsx worker/index.ts)
# Idempotent: start is a no-op when healthy. Logs: $RUN_DIR/logs/northstar-app.log, northstar-worker.log
set -uo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
export NEXT_TELEMETRY_DISABLED=1
APP=$STACK_DIR/app; RUN=$APP/data; mkdir -p "$RUN" $RUN_DIR/logs
WEB_PID=$RUN/web.pid; WK_PID=$RUN/worker.pid
LOG=$RUN_DIR/logs/northstar-app.log; WLOG=$RUN_DIR/logs/northstar-worker.log
NS_PORT=$(grep -E '^PORT=' "$APP/.env" 2>/dev/null | cut -d= -f2); NS_PORT=${NS_PORT:-3100}
health(){ curl -s -m 4 "http://127.0.0.1:$NS_PORT/api/health" 2>/dev/null | grep -q '"service":"northstar-app"'; }
alive(){ [ -s "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
build(){
  cd "$APP" || exit 1
  npx prisma generate >/dev/null && npx prisma db push --skip-generate >/dev/null || { echo "ERROR prisma"; return 1; }
  NODE_OPTIONS=--max-old-space-size=1536 npx next build >> "$LOG" 2>&1 || { echo "ERROR build failed (see $LOG)"; return 1; }
  rm -rf .next/standalone/public .next/standalone/.next/static
  cp -r public .next/standalone/public && cp -r .next/static .next/standalone/.next/static
  echo "built"
}
stop_one(){ alive "$1" && { kill "$(cat "$1")"; for i in $(seq 1 10); do alive "$1" || break; sleep 0.5; done; alive "$1" && kill -9 "$(cat "$1")"; }; rm -f "$1"; }
case "${1:-start}" in
  status)
    health && echo "web: up http://127.0.0.1:$NS_PORT (pid $(cat "$WEB_PID" 2>/dev/null))" || echo "web: DOWN"
    alive "$WK_PID" && echo "worker: up (pid $(cat "$WK_PID"))" || echo "worker: DOWN"
    health && alive "$WK_PID"; exit $? ;;
  stop) stop_one "$WEB_PID"; stop_one "$WK_PID"; echo "stopped"; exit 0 ;;
  restart) "$0" stop; exec "$0" start ;;
  build) build; exit $? ;;
  start) ;;
  *) echo "usage: $0 [start|stop|restart|status|build]"; exit 2 ;;
esac
cd "$APP" || exit 1
# .env is the only config source: drop inherited copies so a stale shell export cannot win over it
for _v in $(grep -oE '^[A-Z_][A-Z0-9_]*' "$APP/.env"); do [ "$_v" = PATH ] || [ "$_v" = HOME ] || unset "$_v"; done
[ -f .next/standalone/server.js ] || build || exit 1
if ! health; then
  stop_one "$WEB_PID"
  HOSTNAME=127.0.0.1 PORT=$NS_PORT setsid nohup node --env-file=.env .next/standalone/server.js >> "$LOG" 2>&1 < /dev/null & echo $! > "$WEB_PID"
fi
if ! alive "$WK_PID"; then
  setsid nohup node --env-file=.env node_modules/tsx/dist/cli.mjs worker/index.ts >> "$WLOG" 2>&1 < /dev/null & echo $! > "$WK_PID"
fi
for i in $(seq 1 20); do sleep 1; health && alive "$WK_PID" && { echo "northstar-app up: http://127.0.0.1:$NS_PORT (web pid $(cat "$WEB_PID"), worker pid $(cat "$WK_PID"))"; exit 0; }; done
echo "ERROR: northstar-app not healthy (see $LOG / $WLOG)" >&2; exit 1
