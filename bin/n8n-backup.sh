#!/usr/bin/env bash
# Backup n8n: SQLite DB (online-safe copy), encryption key/.env, owner.env, workflow+credential exports.
# Output: $RUN_DIR/backups/n8n/n8n-YYYYmmdd-HHMM.tar.gz (chmod 600). Keeps last 14.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
N8N_HOME="$STACK_DIR/n8n"; OUT="$RUN_DIR/backups/n8n"; TS="$(date +%Y%m%d-%H%M)"
umask 077; mkdir -p "$OUT"; TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if command -v fnm >/dev/null 2>&1; then eval "$(fnm env --shell bash)"; fnm use 24 >/dev/null 2>&1 || true; fi
# Consistent DB snapshot via node sqlite3 VACUUM INTO (works while n8n runs)
node -e '
const sqlite3=require(process.argv[1]); const db=new sqlite3.Database(process.argv[2]);
db.run("VACUUM INTO ?", [process.argv[3]], e=>{ if(e){console.error(e);process.exit(1)} db.close(); });
' "$N8N_HOME/node_modules/sqlite3" "${N8N_USER_FOLDER:-$RUN_DIR/n8n}/.n8n/database.sqlite" "$TMP/database.sqlite"
cp "$N8N_HOME/.env" "$TMP/"; cp "$N8N_HOME/owner.env" "$TMP/" 2>/dev/null || true
cp "${N8N_USER_FOLDER:-$RUN_DIR/n8n}/.n8n/config" "$TMP/n8n-config" 2>/dev/null || true
cp "$N8N_HOME/package.json" "$N8N_HOME/package-lock.json" "$TMP/"
# Portable JSON exports (credentials stay encrypted with N8N_ENCRYPTION_KEY)
( set -a; . "$N8N_HOME/.env"; set +a; export N8N_USER_FOLDER=${N8N_USER_FOLDER:-$RUN_DIR/n8n}; cd "$N8N_HOME"
  ./node_modules/.bin/n8n export:workflow --all --output="$TMP/workflows.json" >/dev/null 2>&1 || echo "[warn] workflow export skipped (no workflows yet?)"
  ./node_modules/.bin/n8n export:credentials --all --output="$TMP/credentials.json" >/dev/null 2>&1 || echo "[warn] credential export skipped" )
tar -C "$TMP" -czf "$OUT/n8n-$TS.tar.gz" .
chmod 600 "$OUT/n8n-$TS.tar.gz"
ls -1t "$OUT"/n8n-*.tar.gz | tail -n +15 | xargs -r rm -f
echo "backup: $OUT/n8n-$TS.tar.gz ($(du -h "$OUT/n8n-$TS.tar.gz" | cut -f1))"
