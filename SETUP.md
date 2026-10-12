# SETUP: fresh Linux machine → tests green → app running in dry-run

Tested on Ubuntu 24.04 / Debian 12 (x86_64). Every step runs as a normal user from the repo root unless noted.
Nothing here posts anywhere: the app runs with `PUBLISH_DRY_RUN=true` and `LLM_MODE=mock`, n8n workflows are
imported **inactive** with **no credentials**.

## 0. System packages

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates openssl python3 build-essential
# Node 24 (n8n 2.x and the app need Node >= 20.18; 24 recommended), e.g. with fnm:
curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell && export PATH="$HOME/.local/share/fnm:$PATH"
eval "$(fnm env --shell bash)" && fnm install 24 && fnm default 24
node -v && python3 -V        # node v24.x, python >= 3.11 (zoneinfo)
# E2E browser: Google Chrome (or set CHROME=/path/to/chromium later)
curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && sudo apt-get install -y /tmp/chrome.deb
```

## 1. Get the code

```bash
git clone <repo-url> northstar-agent-stack && cd northstar-agent-stack
export STACK_DIR=$PWD RUN_DIR=$PWD/.run && mkdir -p .run   # bin/ scripts default to these anyway
```

## 2. App: config, database, unit tests

```bash
cd app
cp .env.example .env && chmod 600 .env
# Pick a free port (3100 by default). Set the values below in .env:
# The standalone server needs an ABSOLUTE SQLite path:
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=file:$PWD/data/northstar.db|" .env
sed -i "s|^APP_MASTER_KEY=.*|APP_MASTER_KEY=$(openssl rand -base64 32)|" .env
sed -i "s|^ADMIN_EMAILS=.*|ADMIN_EMAILS=admin@example.com|; s|^LLM_MODE=.*|LLM_MODE=mock|; s|^PUBLISH_DRY_RUN=.*|PUBLISH_DRY_RUN=true|" .env
sed -i "s|^CHANNEL_WORKERS=.*|CHANNEL_WORKERS=false|; s|^NODE_ENV=.*|NODE_ENV=production|" .env
PORT=3100   # or any free port
sed -i "s|^PORT=.*|PORT=$PORT|; s|^APP_URL=.*|APP_URL=http://127.0.0.1:$PORT|" .env
grep -E '^(DATABASE_URL|PORT|APP_URL|LLM_MODE|PUBLISH_DRY_RUN|CHANNEL_WORKERS)=' .env

npm ci
npx prisma generate
npx prisma db push --skip-generate                       # creates data/northstar.db (SQLite)
SEED_CREDENTIALS_FILE=../.run/logins.env npm run db:seed  # demo admin + demo client; logins -> .run/logins.env (chmod 600)

npm run typecheck                                        # no output = OK
npm test                                                 # vitest: 45 tests
```

Notes: `OPENROUTER_API_KEY` stays empty (mock AI). Buffer one-click connect stays off (`BUFFER_CLIENT_ID` empty).
Leave `PAYMENT_UPI_ID` as the placeholder for testing.

## 3. App: build, run (dry-run), health

```bash
cd ..                                # repo root
bin/northstar-app-up.sh start         # builds on first run (~1-3 min), starts web + worker
bin/northstar-app-up.sh status
curl -s http://127.0.0.1:$PORT/api/health        # {"status":"ok","service":"northstar-app",...}
grep 'publish dry run' .run/logs/northstar-worker.log | tail -1   # "... llm mode mock publish dry run true"
```

## 4. App: end-to-end check (headless Chrome)

```bash
cd app
(set -a; . ../.run/logins.env; set +a; BASE=http://127.0.0.1:$PORT CHROME=${CHROME:-/usr/bin/google-chrome} npm run screens)
# expect: "[screens] 12/12 checks passed" (screenshots in app/docs/screens/, gitignored)
cd ..
```

## 5. client1-state helper (Python, no secrets)

```bash
python3 client1-state/tests/test_batches.py      # offline, temp dirs only
python3 client1-state/tests/test_viral.py       # 28/29: hourly_pacing_and_random_gap is a known time-of-day flake upstream
python3 client1-state/tests/test_fix3.py
C1_PORT=8771 bin/client1-state-up.sh start        # runtime data in .run/client1/
curl -s http://127.0.0.1:8771/health
```
The n8n workflows call the helper at `127.0.0.1:8771` and the browser agent at `127.0.0.1:8770`; keep those ports
if you run the workflows.

## 6. n8n: install and import the client1 workflows

```bash
cd n8n
npm install --no-audit --no-fund                 # n8n 2.42.6 (large: several minutes)
cp .env.example .env && chmod 600 .env
sed -i "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)|" .env
# Default port 5678. If it's taken, set N8N_PORT and N8N_WEBHOOK_URL=http://127.0.0.1:<port>/ in .env.
./import-workflows.sh                            # imports 14 workflows (inactive, no credentials) into .run/n8n
cd ..
bin/n8n-up.sh                                    # http://127.0.0.1:5678 (or your N8N_PORT)
```
Then open n8n in a browser, create the owner account, and (only when you want the bot live) create and attach
these credentials in the n8n UI, never in files:

| Credential (type) | Used by |
|---|---|
| Slack bot token (`slackApi` / header auth) | Slack poller, actions, propose-post, batches, coach, onboarding, read receipt |
| Buffer API token (`httpHeaderAuth`, `Authorization: Bearer …`) | Tool: Buffer post, actions, propose-post |
| OpenRouter via the client1-state meter (`openAiApi`, base URL `http://127.0.0.1:8771/or/v1`) | Client1 bot, drafters |

Replace the placeholder Slack channel/user ids (`C0000000001`, `U0000000001`) in the workflows and in
`clients/client1/guardrails.yaml` with your own before activating anything. Keep workflows inactive for testing;
the `Client1 bot: dry-run harness` webhooks (`/webhook/client1-dryrun-*`) work only after credentials are attached.

## 7. Optional: browser agent

Needs a Chrome with CDP on 9350 (`bin/client1-chrome.sh`, requires `xvfb` + `bubblewrap`) and an OpenRouter key.
```bash
sudo apt-get install -y xvfb bubblewrap python3-venv
python3 -m venv browser-agent/.venv && browser-agent/.venv/bin/pip install -r browser-agent/requirements.txt
cp browser-agent/.env.example browser-agent/.env && chmod 600 browser-agent/.env   # add OPENROUTER_API_KEY
bin/browser-agent-up.sh start
```

## 8. Stop and clean up

```bash
bin/northstar-app-up.sh stop
bin/client1-state-up.sh stop
[ -f .run/state/n8n.pid ] && kill "$(cat .run/state/n8n.pid)"
bin/browser-agent-up.sh stop 2>/dev/null || true
rm -rf .run app/data                              # all runtime data (DBs, logs, logins)
```

`bin/services-watchdog.sh ensure` keeps everything up (n8n, Chrome, browser agent, client1-state, app) if you want a
long-running setup. `bin/n8n-backup.sh` writes encrypted-credential backups to `.run/backups/n8n/`.

## Going live later (not part of this test)
Set `PUBLISH_DRY_RUN=false` only after real Buffer credentials are connected and you have reviewed guardrails.
See `app/README.md` (VPS deploy) and `docs/roadmap.md` (v1.0 = VPS move).
