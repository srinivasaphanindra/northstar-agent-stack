# Sourced by every bin/*.sh. Paths are relative to the repo; runtime files go to RUN_DIR (gitignored).
STACK_DIR=${STACK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
RUN_DIR=${RUN_DIR:-$STACK_DIR/.run}            # logs, state, locks, pids, sqlite, chrome profile, backups
export STACK_DIR RUN_DIR TZ=${TZ:-Asia/Kolkata}
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/state" "$RUN_DIR/locks"
