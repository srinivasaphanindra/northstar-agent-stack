#!/bin/bash
# client1-chrome.sh — dedicated, SANDBOXED Chrome for client 1 (the owner's second account set:
# LinkedIn your-linkedin-handle, Facebook share/<fb-share-id>, X @your_x_handle).
# v2 2026-10-11: own Xvfb display :20 (nothing else on it, so the "Sign in" link can expose it safely) and Chrome
# runs inside bubblewrap: it sees only its own profile dir + read-only system dirs (no ~/apps, ~/agency, /workspace,
# /cursor, /run secrets). CDP 127.0.0.1:9350 still reachable from the host (network not unshared).
# Idempotent: if CDP on 9350 already answers, does nothing. Never logs in.
# Usage: client1-chrome.sh [start] | status | stop
# Env: CLIENT1_DISPLAY (default :20), CLIENT1_SANDBOX=0 to run without bubblewrap (not recommended).
set -euo pipefail
for _fd in /proc/$$/fd/*; do _n=${_fd##*/}
  case "$_n" in 0|1|2|255) ;; *[!0-9]*) ;; *) eval "exec $_n>&-" 2>/dev/null || true;; esac
done
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
DISP=${CLIENT1_DISPLAY:-:20}; DNUM=${DISP#:}
export DISPLAY=$DISP
PORT=9350
DIR=$RUN_DIR/chrome-profile/client1-profile
LOG=$RUN_DIR/logs/client1-chrome.log
W=1366; H=900
mkdir -p "$DIR" "$(dirname "$LOG")"
ts(){ date '+%F %T IST'; }
cdp_ok(){ curl -s -m ${CDP_TIMEOUT:-10} "http://127.0.0.1:$PORT/json/version" 2>/dev/null | grep -q Browser; }
pids(){ pgrep -u "$(id -un)" -f -- "--remote-debugging-port=$PORT" 2>/dev/null | while read -r p; do
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -qF -- "--user-data-dir=$DIR" && echo "$p"; done || true; }
xvfb_ok(){ [ -S /tmp/.X11-unix/X$DNUM ] && pgrep -u "$(id -un)" -f "^Xvfb $DISP( |$)" >/dev/null; }
ensure_xvfb(){
  xvfb_ok && return 0
  rm -f /tmp/.X11-unix/X$DNUM /tmp/.X$DNUM-lock 2>/dev/null || true
  echo "[$(ts)] start Xvfb $DISP ${W}x${H}" >> "$LOG"
  setsid nohup Xvfb "$DISP" -screen 0 ${W}x${H}x24 -nolisten tcp -noreset >> "$LOG" 2>&1 < /dev/null &
  for i in $(seq 1 20); do sleep 0.5; [ -S /tmp/.X11-unix/X$DNUM ] && return 0; done
  echo "ERROR: Xvfb $DISP did not start" >&2; return 1
}
CHROME_ARGS=(--remote-debugging-port=$PORT --remote-debugging-address=127.0.0.1
  --user-data-dir="$DIR" --no-first-run --no-default-browser-check
  --disable-gpu --disable-dev-shm-usage --window-position=0,0 --window-size=$W,$H
  --disable-background-timer-throttling --disable-renderer-backgrounding
  --disable-backgrounding-occluded-windows --disable-features=CalculateNativeWinOcclusion)
SANDBOX=(bwrap --unshare-pid --unshare-uts --new-session   # no --unshare-ipc: X MIT-SHM needs the shared IPC namespace (blank window otherwise)
  --ro-bind /usr /usr --ro-bind /etc /etc --ro-bind /opt/google /opt/google
  --symlink usr/bin /bin --symlink usr/sbin /sbin --symlink usr/lib /lib --symlink usr/lib64 /lib64
  --proc /proc --dev /dev --tmpfs /dev/shm --tmpfs /tmp --tmpfs /run --ro-bind /sys /sys
  --ro-bind /tmp/.X11-unix/X$DNUM /tmp/.X11-unix/X$DNUM
  --tmpfs /home --dir /home/sandbox --dir /home/sandbox/Downloads --bind "$DIR" "$DIR"
  --clearenv --setenv HOME /home/sandbox --setenv DISPLAY "$DISP" --setenv PATH /usr/bin:/bin
  --setenv TZ Asia/Kolkata --setenv LANG C.UTF-8 --)

case "${1:-start}" in
  status)
    if cdp_ok; then echo "client1 chrome UP port=$PORT display=$DISP sandbox=$([ "${CLIENT1_SANDBOX:-1}" = 0 ] && echo off || echo bwrap) pids=$(pids | tr '\n' ' ')"; else echo "client1 chrome DOWN port=$PORT"; exit 1; fi ;;
  stop)
    p=$(pids); [ -n "$p" ] || { echo "not running"; exit 0; }
    echo "[$(ts)] stop pids=$(echo $p)" >> "$LOG"; kill $p 2>/dev/null || true
    for i in $(seq 1 10); do sleep 1; [ -z "$(pids)" ] && break; done; echo "stopped $p" ;;
  start)
    if cdp_ok; then echo "already up: port=$PORT"; exit 0; fi
    # v0.5 fix (2026-10-12): a slow CDP under load is NOT "down". If a Chrome for this profile is still alive, never
    # delete its Singleton lock and never launch a second one (bwrap's own pid namespace hides the lock owner, so
    # Chrome would happily start a duplicate on the same profile: 14 copies piled up this way).
    alive=$(pids | tr '\n' ' ')
    if [ -n "${alive// /}" ]; then
      echo "[$(ts)] start skipped: chrome alive (pids $alive) but CDP slow/not answering; not launching a duplicate" >> "$LOG"
      echo "WARN: client1 chrome alive (pids $alive) but CDP $PORT not answering; not launching a duplicate" >&2; exit 1
    fi
    ensure_xvfb
    rm -f "$DIR"/Singleton* 2>/dev/null || true
    echo "[$(ts)] launch port=$PORT dir=$DIR display=$DISP sandbox=${CLIENT1_SANDBOX:-1}" >> "$LOG"
    if [ "${CLIENT1_SANDBOX:-1}" = 0 ]; then PRE=(); else PRE=("${SANDBOX[@]}"); fi
    setsid nohup "${PRE[@]}" /usr/bin/google-chrome "${CHROME_ARGS[@]}" \
      https://www.linkedin.com/feed/ https://www.facebook.com/ https://x.com/home \
      >> "$LOG" 2>&1 < /dev/null &
    for i in $(seq 1 25); do sleep 2
      if cdp_ok; then echo "[$(ts)] up port=$PORT" >> "$LOG"; echo "launched OK: port=$PORT display=$DISP"; exit 0; fi
    done
    echo "[$(ts)] ERROR: CDP did not answer" >> "$LOG"; echo "ERROR: CDP $PORT not answering (see $LOG)" >&2; exit 1 ;;
  *) echo "usage: $0 [start|status|stop]"; exit 2 ;;
esac
