# Changelog

All notable changes to this project. Versions follow the Northstar app tags. Dates are in IST.

## Unreleased
- Public docs: README, `docs/` pages, CONTRIBUTING, SECURITY, LICENSE (proprietary, all rights reserved), issue/PR templates, release notes.
- Removed the internal ops log and business plan from `docs/`. Roadmap scrubbed.

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
