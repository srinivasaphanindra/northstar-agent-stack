# Changelog

## v0.3.0 — 2026-10-12

Local demo only: `PUBLISH_DRY_RUN=true`. Demo DB backed up first and migrated with `prisma db push` (4 new tables).

### Added
- Admin-only, mobile-first **Clients** dashboard (`/admin/clients`): each client's profile links (read from existing config at collect time, never stored in the repo) and today's posts, replies and comments against its caps.
- Per-client analytics (`/admin/clients/<key>`, 7 or 30 days): posts, replies and comments per day per platform; approvals approved/rejected/edited; inbound messages handled; impressions (Buffer, where available); follower trend; AI spend against budget ($35/month on Viral); recent activity with links.
- Viral 4-week warm-up caps in `config/clients.yaml` (X 4/8 → 15/30 posts/replies a day; LinkedIn and Facebook ramps), with week math in IST.
- Read-only collector (`src/core/clients/`), every 15 min in the worker (`CLIENTS_COLLECTOR`, `CLIENTS_COLLECT_SECONDS`) or `npm run collect`. Sources: X post ledger and inbound log, the bots' sync-log entries, followers log, AI spend log, client helper GET endpoints (`/health`, `/guard/limits`, `/engage/status`), client audit log, n8n executions read API, and Buffer analytics read queries only when a key file is already configured. No platform write APIs, no X API calls; missing files and failed sources are recorded in `CollectorRun` and skipped.
- New tables: `ClientProfile`, `ClientActivity`, `ClientMetric`, `CollectorRun`.

### Tests
- vitest 60/60 (15 new: parsers, aggregation, caps math, collector idempotency/read-only, admin-only access), typecheck clean, headless E2E 19/19 (new admin and non-admin Clients checks).

## v0.2.0 — 2026-10-12

Merged from branch `v0.2` (merge db0be90). Local demo only: `PUBLISH_DRY_RUN=true`, Buffer OAuth off (no `BUFFER_CLIENT_ID`), no Slack token. Demo DB migrated with `prisma db push` (adds nullable `Draft.scheduledAt`).

### Added
- Buffer OAuth 2.0 + PKCE ("Connect with Buffer"): `/api/buffer/oauth/start` + `/callback`, single-use state (15 min), encrypted verifier, encrypted access + rotating refresh token, single refresher per workspace. Hidden until `BUFFER_CLIENT_ID` is set; personal API key still works and replaces OAuth tokens when pasted.
- Buffer channel picker (Settings → Integrations): refresh channels, pick one per platform; publishing uses the pick (refuses a disconnected/locked pick).
- Approve as: Buffer queue / post now / at a time (IST, 5 min to 180 days ahead) → Buffer `customScheduled` + `dueAt`. New `Draft.scheduledAt` column.
- Approve-time content check: "numbers must be in notes" (port of the n8n rule) + AI classifier (CONTENT SAFETY CHECK v1 rubric, `CONTENT_CHECK_MODEL`). Fails open on AI errors / demo mode / spent budget, recorded in the audit log; blocks keep the draft pending with the reason.
- Slack bot harness: local fake Slack API (`tests/harness/fake-slack.ts`) + `tests/slack.test.ts`; `npm run slack:live-check` for a throwaway Slack workspace (read-only by default, `--post` sends one test message).

### Tests
- vitest 45/45 (26 existing + 19 new), typecheck clean; headless E2E 12/12 on the release build (:3101, DB copy, mock LLM, dry run).

### Not yet verified live
- Real Buffer OAuth / customScheduled (needs a Buffer app client + HTTPS redirect) and a real Slack bot run (`npm run slack:live-check` with a throwaway token).

## v0.1.0 — 2026-10-12

First tagged build (phase 1). Local demo only; nothing publishes (`PUBLISH_DRY_RUN=true`, no Buffer key, no tunnel).

### Added
- Next.js 15 + Tailwind 4 mobile-first installable PWA (manifest, icons, service worker with offline page, install button, web push opt-in stub).
- Auth: email + password (scrypt), DB sessions, throttle, logout, invites with roles (owner/admin/member).
- Multi-tenant workspaces (`workspaceId` on every row) and append-only audit log.
- Onboarding wizard: business → plan pick with UPI / PhonePe QR payment (admin marks paid; Gumroad hidden until `PAYMENT_GUMROAD_URL`) → 10-question interview as chat → connect tools → chat channel (web + Slack; Telegram behind `CHANNEL_TELEGRAM`).
- Settings: AI key (platform / BYO OpenRouter, tested, encrypted), Integrations (Buffer, Hootsuite, GoHighLevel, generic API key/webhook, Slack), Guardrails, Team, Chat channels, Plan & billing.
- Web chat with the agent via worker job queue (live OpenRouter path); approvals inbox; approve → Buffer only when not dry-run.
- Telegram connector (gated) and Slack connector (polling, typed approve).
- Admin page: workspaces, mark invoice paid, usage, failed jobs, audit log, platform AI daily spend card with hard stop at `PLATFORM_DAILY_AI_CAP_USD`.
- Per-workspace usage/spend tracking with caps (80/100/150 %).
- Deploy kit: VPS + systemd + Caddy + Postgres guide, Dockerfile + compose (untested).

### Tests
- 26 vitest tests; headless E2E 12/12 (screenshots in `docs/screens/`).

### Known gaps
- Slack only mocked; Buffer uses API key not OAuth; regex-only content check; no web push/email sending; Postgres not exercised; no API rate limits/CSP/DPDP export.
