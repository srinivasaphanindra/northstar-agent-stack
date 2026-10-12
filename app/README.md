# Northstar Agents app (agent-as-a-service), phase 1

A multi-tenant web app where a small business signs up, picks a plan (manual UPI / Gumroad payment), does the
10-question onboarding interview in chat, connects its tools, and then talks to its social media assistant on the
web or Slack (Telegram behind CHANNEL_TELEGRAM flag). The assistant drafts; a human approves; only then is anything sent to Buffer. On phones,
approved drafts open straight in the client's own signed-in X / LinkedIn / Facebook / WhatsApp app.

Plan: `~/agency/product/northstar-aaas-platform.md`. Status and next steps: `STATUS.md`.

## Stack
| Part | Choice |
|---|---|
| Web | Next.js 15 (App Router, TypeScript), Tailwind 4, black minimal style of the landing page. Mobile-first, installable PWA |
| DB | Prisma 6. SQLite locally, Postgres in production, **same models** (`npm run db:pg-schema` writes the Postgres copy) |
| Auth | Email + password (scrypt), DB sessions (httpOnly cookie, only the token's SHA-256 stored), login throttle. Magic link later |
| Jobs | Poll-based queue in the DB (`Job` table, atomic claim), one worker process: agent replies, draft notifications, Telegram long polling, Slack polling |
| AI | OpenRouter chat completions + tool calls, default `google/gemini-3.1-flash-lite`. Workspace BYO key or platform key. Offline mock when no key (`LLM_MODE`) |
| Secrets | Envelope encryption: `APP_MASTER_KEY` (env) wraps a per-workspace AES-256-GCM data key; secrets are bound to workspace + purpose (AAD) |

**Why not Auth.js:** v5 is still beta and its Credentials provider can't use DB sessions. A ~150-line session module
(`src/core/auth.ts`) gives revocable sessions, is unit-tested, and has no framework lock-in. Magic link drops in there.

## Layout (core is separate from the frontend)
```
src/core/          framework-free TypeScript: the product (usable by the worker, tests, a CLI, or another frontend)
  auth.ts tenancy.ts crypto.ts integrations.ts guardrails.ts usage.ts billing.ts
  approvals.ts agent.ts llm.ts notes.ts onboarding.ts chat.ts jobs.ts buffer.ts handoff.ts
  channels/ telegram.ts slack.ts index.ts
src/app/           Next.js pages + route handlers (thin: read cookie -> Ctx -> call core)
src/components/    UI components (HandoffButtons, ChatThread, PwaRegister, ...)
src/lib/           Next glue (session cookie, server-action helper)
worker/index.ts    job runner + channel pollers
prisma/            schema.prisma (+ seed.ts); prisma/postgres/ generated for prod
config/            guardrails-default.yaml snapshot (prod default template)
deploy/            Dockerfile, docker-compose.yml, Caddyfile, systemd units
tests/             vitest: auth, tenant isolation, encryption, approvals, onboarding, payments, spend caps, Telegram
scripts/           screenshots.mjs (headless E2E + screenshots), cleanup-e2e.ts, demo-drafts.ts, make-postgres-schema.mjs
```
Self-hosted "template" edition: ship `src/core` + `worker` + `prisma` as-is; the `src/app` UI can be swapped or rebranded
without touching the core. Rules: core never imports from `next/*` or `src/app`; tenant code takes a `Ctx`.

## Safety rules enforced in code
- Every tenant row has `workspaceId`; every core function filters on `ctx.workspaceId` (tests in `tests/tenancy.test.ts`).
- The agent's tools are `propose_post`, `save_note`, `list_pending_drafts`. There is **no approve/publish tool**.
- Approve needs an owner/admin, an active (paid) workspace, not paused, content re-check, daily cap per platform; the
  `pending -> approved` change is atomic (a draft can't publish twice).
- `PUBLISH_DRY_RUN=true` (default) never calls Buffer, even after approval.
- Pasted passwords/keys never reach the model or memory. Integration secrets are only shown as last 4 characters.
- Spend caps per workspace (guardrails `spend` block + plan cap): warn 80 %, pause extras 100 %, fixed reply at 150 %.
- Platform-wide daily OpenRouter cap (`PLATFORM_DAILY_AI_CAP_USD`, default $3): hard stop on the platform key across all workspaces; admin page shows an alert.
- Audit log (append-only) for logins, keys, approvals, payments, guardrail changes.

## Run locally (this box)
```bash
~/agency/bin/northstar-app-up.sh start     # builds if needed; web http://127.0.0.1:3100 + worker
~/agency/bin/northstar-app-up.sh status|stop|restart|build
npm test                                   # vitest (uses data/test.db)
set -a; . ~/agency/state/northstar-app-logins.env; set +a; npm run screens   # E2E + screenshots -> docs/screens/
```
Dev server: `npm run dev` (port 3100) + `npm run worker`. Seed: `npm run db:push && SEED_CREDENTIALS_FILE=... npm run db:seed`.
Config: `.env` (see `.env.example`; keep `APP_MASTER_KEY` backed up off the server: losing it makes stored keys unreadable).

## Deploy to a VPS (Ubuntu, systemd + Caddy + Postgres)
1. `apt install postgresql caddy` and Node 24 (NodeSource or fnm). `adduser --system --group northstar`.
2. `createuser northstar -P && createdb -O northstar northstar`.
3. Copy the repo to `/opt/northstar-app` (owned by `northstar`). Create `.env` from `.env.example`:
   `APP_URL=https://app.<domain>`, `DATABASE_URL=postgresql://northstar:<pw>@127.0.0.1:5432/northstar`,
   `APP_MASTER_KEY=$(openssl rand -base64 32)`, `ADMIN_EMAILS`, `OPENROUTER_API_KEY`, payment fields, `PUBLISH_DRY_RUN=false` when ready. `chmod 600 .env`.
4. Build:
   ```bash
   npm ci && npm rebuild @prisma/engines prisma @prisma/client esbuild @tailwindcss/oxide
   npm run db:pg-schema && npx prisma generate --schema prisma/postgres/schema.prisma
   npx prisma db push --skip-generate --schema prisma/postgres/schema.prisma
   npx next build && cp -r public .next/standalone/public && cp -r .next/static .next/standalone/.next/static
   ```
5. `cp deploy/systemd/*.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now northstar-web northstar-worker`.
6. `deploy/Caddyfile` → `/etc/caddy/Caddyfile` (your domain), `systemctl reload caddy`. Caddy gets the TLS certificate.
7. Backups: nightly `pg_dump` off the server + the `.env` (encrypted). Uptime check on `/api/health`.
Moving data from the box: the SQLite demo data is disposable; start prod clean (or export with a script).

**Docker (later):** `docker compose -f deploy/docker-compose.yml --env-file .env up -d --build` (Postgres + migrate + web + worker),
then Caddy in front of `127.0.0.1:3100`. Docker can't run on the dev box, so these files are untested there.

## Mobile, PWA and native apps later
- Mobile-first layout: bottom tab bar, big approve buttons in thumb reach, sticky chat composer, safe-area insets.
- PWA: `app/manifest.ts` (standalone, icons incl. maskable), `public/sw.js` (static asset cache, offline page,
  push handler), install prompt (`InstallButton`), web push opt-in **stub** (`PushStub` + `/api/push/subscribe`
  records the opt-in; sending needs VAPID keys + the `web-push` package: next step).
- "Open in app" handoff (`src/core/handoff.ts`, `HandoffButtons`): X intent URL (text prefilled), LinkedIn
  share-offsite + copy, Facebook sharer + copy, WhatsApp `wa.me/?text=`, native Web Share sheet, copy text.
- **Capacitor later:** the UI is a normal web app, so a Capacitor shell (`npx cap init`, `server.url` pointing at the
  production URL, or a static export of a client-only shell) can wrap it for Play Store / App Store. Prepared:
  handoff logic is a pure function, `PwaRegister` skips the service worker inside Capacitor, push goes through one
  endpoint that can also accept FCM/APNs tokens. Swap `navigator.share` for `@capacitor/share` and deep links for
  `@capacitor/app-launcher` when wrapping. Not built now.

## Channels
- **Web chat**: always on. Messages queue an `agent.reply` job; the page polls until the reply arrives.
- **Telegram** (optional, `CHANNEL_TELEGRAM=true`): per-tenant bot token (Integrations) → Settings → Channels → one-time `t.me/<bot>?start=<code>` link.
  Only the linked private chat is answered. Drafts arrive with ✅ / ❌ buttons + "Open in X / WhatsApp" links. Long polling, no inbound URL. Hidden in UI when the flag is false.
- **Slack**: per-tenant bot token + channel id; polled every 60 s (`conversations.history` + replies of active threads).
  Approve by typing `approve <code>` (approver Slack ids set in Integrations). Phase 2: Events API + "Add to Slack" on the VPS.
- WhatsApp: later, optional.
