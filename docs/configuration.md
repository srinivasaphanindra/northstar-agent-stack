# Configuration

Every component reads a `.env` file created from its `.env.example`. Copy it, `chmod 600` it and never commit it.
The values below are copied from the examples in this repo.

## `app/.env.example`

```dotenv
# ---- Northstar app configuration (copy to .env; never commit .env) ----
# Where the app is reachable (used for links and cookie security). https:// => secure cookies.
APP_URL=http://127.0.0.1:3100
HOST=127.0.0.1
PORT=3100
NODE_ENV=production

# Database. Local: SQLite file. Prod: postgresql://user:pass@host:5432/northstar (run `npm run db:pg-schema`
# and use --schema prisma/postgres/schema.prisma, see README).
# Use an absolute path (the standalone server resolves relative paths differently).
DATABASE_URL=file:/ABSOLUTE/PATH/TO/app/data/northstar.db

# 32 random bytes, base64 or hex: `openssl rand -base64 32`. Wraps every workspace's data key.
# Losing it = every stored key/token is unreadable. Back it up outside the server.
APP_MASTER_KEY=

# Comma-separated emails that become platform admins (the /admin page) when they sign up.
ADMIN_EMAILS=
ALLOW_SIGNUP=true
SESSION_DAYS=14

# AI (OpenRouter). Platform key used by workspaces on the "platform" key mode.
OPENROUTER_API_KEY=
DEFAULT_MODEL=google/gemini-3.1-flash-lite
# auto = live when a key exists, else mock replies; mock = always offline; live = always call OpenRouter
LLM_MODE=auto

# Safety: while true, approved drafts are NOT sent to Buffer (recorded as dry runs). Set false to publish for real.
PUBLISH_DRY_RUN=true
# Buffer one-click connect (OAuth 2.0 + PKCE). Leave BUFFER_CLIENT_ID empty to hide it (personal API key still works).
# Needs Northstar's registered Buffer app client; redirect URI must be HTTPS and registered with Buffer.
BUFFER_CLIENT_ID=
BUFFER_CLIENT_SECRET=
# BUFFER_OAUTH_REDIRECT_URI=https://app.example.com/api/buffer/oauth/callback   (default: APP_URL + /api/buffer/oauth/callback)
# BUFFER_OAUTH_AUTHORIZE_URL=https://auth.buffer.com/auth
# BUFFER_OAUTH_TOKEN_URL=https://auth.buffer.com/token
# BUFFER_OAUTH_SCOPES=posts:write posts:read account:read offline_access
# Approve-time AI content check model (default: DEFAULT_MODEL)
# CONTENT_CHECK_MODEL=google/gemini-3.1-flash-lite

# Guardrails yaml template (defaults -> tiers[plan] -> per-workspace overrides).
GUARDRAILS_TEMPLATE_PATH=./config/guardrails-default.yaml

# Manual payments (no gateway in phase 1). Admin marks invoices paid in /admin.
PAYMENT_UPI_ID=your-upi-id@bank
PAYMENT_UPI_NAME="YOUR NAME"
# Optional: your own QR image (path under public/, e.g. /pay/phonepe-qr.png, or https URL). Empty = generated from the UPI ID.
PAYMENT_UPI_QR_IMAGE=
# Gumroad card checkout. Empty or *PLACEHOLDER* = hide the Gumroad option (product https://gumroad.com/l/your-product not published yet).
PAYMENT_GUMROAD_URL=

# Platform-wide OpenRouter hard stop across all workspaces (IST day). Per-workspace caps still apply.
PLATFORM_DAILY_AI_CAP_USD=3

# Channel workers (Telegram long polling, Slack polling). Set 0 on extra worker copies.
CHANNEL_WORKERS=true
# Feature flag: false hides Telegram in onboarding + settings (code kept).
CHANNEL_TELEGRAM=false
SLACK_POLL_SECONDS=60

# Web push (stub for now): VAPID keys for later
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
```

## `n8n/.env.example`

```dotenv
# n8n runtime config (copy to n8n/.env, chmod 600). Never commit .env.
# 32-byte random hex: `openssl rand -hex 32`. Losing it = stored credentials unreadable. Back it up.
N8N_ENCRYPTION_KEY=
# Where n8n keeps its SQLite DB + config. Absolute path recommended (bin/n8n-up.sh sets a default under .run/).
N8N_USER_FOLDER=
N8N_HOST=127.0.0.1
N8N_LISTEN_ADDRESS=127.0.0.1
N8N_PORT=5678
N8N_PROTOCOL=http
N8N_WEBHOOK_URL=http://127.0.0.1:5678/
GENERIC_TIMEZONE=Asia/Kolkata
TZ=Asia/Kolkata
N8N_DIAGNOSTICS_ENABLED=false
N8N_PERSONALIZATION_ENABLED=false
N8N_VERSION_NOTIFICATIONS_ENABLED=false
N8N_TEMPLATES_ENABLED=true
N8N_SECURE_COOKIE=false
N8N_PUBLIC_API_DISABLED=false
N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true
DB_TYPE=sqlite
DB_SQLITE_POOL_SIZE=2
EXECUTIONS_DATA_PRUNE=true
EXECUTIONS_DATA_MAX_AGE=336
N8N_UNVERIFIED_PACKAGES_ENABLED=false
N8N_RUNNERS_TASK_TIMEOUT=300
```

## `n8n/owner.env.example`

```dotenv
# Used by scripts that call the n8n public API (copy to n8n/owner.env, chmod 600). Never commit.
N8N_URL=http://127.0.0.1:5678
N8N_OWNER_EMAIL=admin+n8n@example.com
N8N_OWNER_PASSWORD=
# Create in n8n: Settings -> n8n API -> Create API key
N8N_API_KEY=
```

## `browser-agent/.env.example`

```dotenv
# browser-agent service (copy to browser-agent/.env, chmod 600). Never commit.
OPENROUTER_API_KEY=
BROWSER_AGENT_MODEL=google/gemini-3.1-flash-lite
BROWSER_AGENT_CDP_URL=http://127.0.0.1:9350
# Spend metering + domain allow-list go through state-helper (127.0.0.1:8771) by default:
# BROWSER_AGENT_BASE_URL=http://127.0.0.1:8771/or/browser/v1
# BROWSER_AGENT_ALLOWLIST_URL=http://127.0.0.1:8771/guard/allowlist
```

## `state-helper/.env.example`

```dotenv
# state-helper helper: no secrets needed. bin/state-helper-up.sh sets all CS_* paths under $RUN_DIR/example-client.
# Overrides (optional):
# CS_PORT=8771
# CS_GUARDRAILS=templates/guardrails.example.yaml
# CS_WORKER=1          # 0 = no background engagement worker
# CS_NOTIFY=0          # 1 = call bin/notify.sh for owner notifications (not included in this repo)
# CS_OR_UPSTREAM=https://openrouter.ai/api/v1
# CS_BROWSER=http://127.0.0.1:8770
```

## Safety switches
- `PUBLISH_DRY_RUN=true` (app): approved drafts are recorded as dry runs, not sent to Buffer.
- `LLM_MODE=mock`: offline replies, with no AI calls.
- `CHANNEL_TELEGRAM=false`: Telegram is hidden.
- `PLATFORM_DAILY_AI_CAP_USD`: hard stop on the platform AI key across all workspaces.
