#!/usr/bin/env bash
# Imports every workflow in n8n/workflows/ into the local n8n (inactive, no credentials attached).
# Usage: n8n/import-workflows.sh        (n8n may be stopped or running; uses the n8n CLI + n8n/.env)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../bin/_env.sh"
[ -f "$HERE/.env" ] || { echo "ERROR: create n8n/.env from n8n/.env.example first" >&2; exit 1; }
set -a; . "$HERE/.env"; set +a
export N8N_USER_FOLDER=${N8N_USER_FOLDER:-$RUN_DIR/n8n}
mkdir -p "$N8N_USER_FOLDER"
"$HERE/node_modules/.bin/n8n" import:workflow --separate --input="$HERE/workflows"
echo "imported $(ls "$HERE"/workflows/*.json | wc -l) workflow file(s) into $N8N_USER_FOLDER"
