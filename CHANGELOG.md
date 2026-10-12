# Changelog

All notable changes to this project. Versions follow the Northstar app tags. Dates are in IST.

## Unreleased
- Public docs: README, `docs/` pages, CONTRIBUTING, SECURITY, LICENSE (proprietary, all rights reserved), issue/PR templates, release notes.
- Removed the internal ops log and business plan from `docs/`. Roadmap scrubbed.

## v0.5.0 — 2026-10-12

Local only: `PUBLISH_DRY_RUN=true`. No schema change; Northstar app unchanged.

### Added
- `selfheal/`: light self-heal supervisor (5 min loop via `services-watchdog.sh`): health checks, backoff-limited restarts through the service scripts, safe Chrome/tab reaping, one retry for allowlisted idempotent n8n workflows only, draft fixes (re-queue for rebuild, block `<<<`), audit TSV and JSON alerts. Hard limits enforced in code and tests.

### Fixed
- Chrome pile-up: under load the 3 s CDP check failed and `start` removed the profile's Singleton lock and launched another sandboxed Chrome on the same profile (bwrap's pid namespace hides the lock owner). `bin/client1-chrome.sh` now waits 10 s (`CDP_TIMEOUT`) and refuses to launch while a Chrome for the profile is alive.
- client0-state: dry-run publishes are logged as `dry_run`, not `issued`, so they no longer count toward the daily caps (documented in `clients/client0/README.md`). client1-state unchanged (no dry-run publish path).

## v0.4.0 — 2026-10-12

Local demo only: `PUBLISH_DRY_RUN=true`. No schema change.

### Added
- Clients collector reads the new client0 helper (`client0-state`, `CLIENT0_STATE_URL`, default 127.0.0.1:8772, GET `/health`, `/guard/limits`, `/engage/status`), client0's audit log and the n8n "Client0 bot" executions: client 0 now runs through Slack #client0 like client 1 (dry run).
- Dry-run approvals (`publish_result dry_run`, `engage dry_run`) are a separate `dry_run` kind: shown as "dry-run approvals (not posted)", never counted as posts or replies.
- A client's own helper AI meter takes precedence over the shared OpenRouter spend log.

### Added (stack)
- `clients/client0/`: template for running the owner's own accounts through Slack like client 1 (guardrails with placeholders, notes example, README: client0-state dry-run patch, n8n cloning rules, test round trip).

### Tests
- vitest 61/61 (new: client0 dry-run rows, helper spend precedence, client0 helper caps). Headless E2E not re-run (box overloaded, no new Chrome); unchanged pages.

## v0.3.0 — 2026-10-12

Local demo only: `PUBLISH_DRY_RUN=true`. Existing databases need `npx prisma db push` (4 new tables).

### Added
- Admin-only, mobile-first **Clients** dashboard (`/admin/clients`): each client's profile links (read from existing config at collect time, never stored in the repo) and today's posts, replies and comments against its caps.
- Per-client analytics (`/admin/clients/<key>`, 7 or 30 days): posts, replies and comments per day per platform; approvals approved/rejected/edited; inbound messages handled; impressions (Buffer, where available); follower trend; AI spend against budget ($35/month on Viral); recent activity with links.
- Viral 4-week warm-up caps in `config/clients.yaml` (X 4/8 → 15/30 posts/replies a day; LinkedIn and Facebook ramps), with week math in IST.
- Read-only collector (`src/core/clients/`), every 15 min in the worker (`CLIENTS_COLLECTOR`, `CLIENTS_COLLECT_SECONDS`) or `npm run collect`. Sources: X post ledger and inbound log, the bots' sync-log entries, followers log, AI spend log, client helper GET endpoints (`/health`, `/guard/limits`, `/engage/status`), client audit log, n8n executions read API, and Buffer analytics read queries only when a key file is already configured. No platform write APIs, no X API calls; missing files and failed sources are recorded in `CollectorRun` and skipped.
- New tables: `ClientProfile`, `ClientActivity`, `ClientMetric`, `CollectorRun`.

### Tests
- vitest 60/60 (15 new: parsers, aggregation, caps math, collector idempotency/read-only, admin-only access), typecheck clean, headless E2E 19/19 (new admin and non-admin Clients checks).

## v0.2.0 — 2026-10-12

First version of this stack repo: Northstar app v0.2.0, 14 client 1 n8n workflows (inactive, no credentials), the
`client1-state` helper, `browser-agent`, guardrails templates, service scripts and SETUP.md. Local demo only:
`PUBLISH_DRY_RUN=true`, Buffer OAuth off (no `BUFFER_CLIENT_ID`), no Slack token.

### Added
- Buffer OAuth 2.0 + PKCE ("Connect with Buffer"): single-use state (15 min), encrypted verifier, encrypted access and rotating refresh token, one refresher per workspace. Hidden until `BUFFER_CLIENT_ID` is set. The personal API key still works.
- Buffer channel picker (Settings → Integrations): one channel per platform. Publishing refuses a disconnected or locked pick.
- Approve as Buffer queue / post now / at a time (IST, 5 min to 180 days ahead). New `Draft.scheduledAt` column.
- Approve-time content check: the "numbers must be in notes" rule plus an AI classifier (`CONTENT_CHECK_MODEL`). It fails open on AI errors, demo mode or a spent budget, and records that in the audit log. Blocks keep the draft pending with the reason.
- Slack bot harness: a local fake Slack API plus tests, and `npm run slack:live-check` for a throwaway workspace.

### Tests
- vitest 45/45 (26 existing + 19 new), typecheck clean, headless E2E 12/12.

### Not yet verified live
- Real Buffer OAuth / scheduled posting and a real Slack bot run.

### Upgrade
- Existing databases need `prisma db push` (adds the nullable `Draft.scheduledAt`).

## v0.1.0 — 2026-10-12

First tagged build of the Northstar app (phase 1). It isn't a separate commit in this repo (see `release-notes/v0.1.0.md`).

### Added
- Next.js 15 + Tailwind 4 mobile-first installable PWA (manifest, service worker, offline page, install button, web push opt-in stub).
- Auth: email + password (scrypt), DB sessions, throttle, invites with owner/admin/member roles.
- Multi-tenant workspaces and an append-only audit log.
- Onboarding wizard: business → plan pick with manual payment (admin marks paid) → 10-question interview as chat → connect tools → chat channel (web + Slack; Telegram behind `CHANNEL_TELEGRAM`).
- Settings: AI key (platform or BYO OpenRouter, encrypted), integrations (Buffer, Hootsuite, GoHighLevel, generic webhook, Slack), guardrails, team, chat channels, plan & billing.
- Web chat with the agent via the worker job queue, an approvals inbox, and approve → Buffer only when not in dry-run.
- Slack connector (polling, typed approve). Telegram connector (gated).
- Admin page with the platform daily AI hard stop (`PLATFORM_DAILY_AI_CAP_USD`). Per-workspace spend caps (80/100/150 %).
- Deploy kit: VPS + systemd + Caddy + Postgres guide, Dockerfile + compose (untested).

### Tests
- 26 vitest tests, headless E2E 12/12.

### Known gaps
- Slack only mocked, Buffer API key only (no OAuth), regex-only content check, no web push/email, Postgres not exercised, no API rate limits / CSP / DPDP export.
