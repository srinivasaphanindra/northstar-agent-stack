# Status: phase 1 (2026-10-12 00:41 IST)

Local demo running on this box: http://127.0.0.1:3100 (web) + worker, started by `~/agency/bin/northstar-app-up.sh`,
kept up by `~/agency/bin/services-watchdog.sh`. Demo logins: `~/agency/state/northstar-app-logins.env` (chmod 600).
Nothing is reachable from outside the box (no tunnel). Nothing publishes: `PUBLISH_DRY_RUN=true`, no Buffer key.
Platform OpenRouter key is set (copied from browser-agent); default model `google/gemini-3.1-flash-lite`. Live chat
smoke-tested in the demo workspace (not mock).

## Done
- App skeleton (Next.js 15 + Tailwind 4, black minimal style), mobile-first, installable PWA (manifest, icons, service
  worker with offline page, install button, web push opt-in stub).
- Auth: email + password (scrypt), DB sessions, throttle, logout, invites with roles (owner/admin/member).
- Workspaces, multi-tenant with `workspaceId` on every row, audit log (append-only).
- Onboarding wizard: business → plan pick (real UPI: `your-upi-id@bank` / YOUR NAME, PhonePe QR at
  `/pay/phonepe-qr.png`, `upi://pay` deep link; Gumroad hidden until `PAYMENT_GUMROAD_URL` is set — product
  https://gumroad.com/l/your-product not published yet) → 10-question interview as chat → connect tools → chat channel
  (web + Slack; Telegram behind `CHANNEL_TELEGRAM=false`) → done. After paying, clients share the UPI reference;
  admin marks paid by hand.
- Settings: AI key (platform / BYO OpenRouter, tested, encrypted, last 4 only), Integrations (Buffer, Hootsuite,
  GoHighLevel, generic API key/webhook, Slack; Telegram only when flag on), Guardrails, Team, Chat channels,
  Plan & billing.
- Web chat with the agent (worker job queue, polling UI) — **live OpenRouter** path confirmed; approvals inbox,
  approve → Buffer only when not dry-run, "Open in app" handoff.
- Telegram connector code kept (long polling, one-time /start link); UI + worker poller gated by `CHANNEL_TELEGRAM`.
  Slack connector (polling, typed approve).
- Admin page: workspaces, mark invoice paid, usage, failed jobs, audit log, **platform AI daily spend** card + alert
  when `PLATFORM_DAILY_AI_CAP_USD` ($3) is hit (hard stop on platform key across all workspaces; per-workspace caps
  still apply).
- Usage/spend per workspace (tokens, cost, drafts, messages) with caps (plan cap + guardrail budget; 80/100/150 %).
- Tests: 26 vitest tests (incl. platform daily AI cap). Headless E2E: 12/12 checks, screenshots in `docs/screens/`
  (payment mobile: `10-onboarding-plan-payment-mobile.png`, chat mobile: `15-chat-mobile.png`).
- Deploy kit: README guide (VPS + systemd + Caddy + Postgres), Dockerfile + compose (untested: no Docker here).

## v0.2.0 (2026-10-12, merged to master and running in the local demo; tag v0.2.0)
Buffer OAuth + PKCE (off until `BUFFER_CLIENT_ID`), channel picker, queue / now / scheduled approvals, approve-time content
check (numbers-in-notes + AI classifier), Slack harness (fake API tests + `npm run slack:live-check`). See CHANGELOG.
Demo DB already migrated (nullable `Draft.scheduledAt`); a fresh deploy needs `prisma db push`.

## Not done / next
1. **Real Slack test** with a throwaway bot token (only mocked in tests). Telegram when `CHANNEL_TELEGRAM=true`.
2. Publish Gumroad product https://gumroad.com/l/your-product then set `PAYMENT_GUMROAD_URL` to show card checkout.
3. Buffer OAuth (PKCE) instead of a personal API key; Buffer channel picker; "now"/scheduled times.
4. Web push sending (VAPID + `web-push`), email notifications (Resend), magic-link sign-in, password reset.
5. LLM content classifier at approve time (the n8n bot has it; here only the free regex check runs), "numbers must
   be in notes" rule, engagement actions (not in phase 1 scope).
6. Postgres run of the same schema (generated via `npm run db:pg-schema`, not yet exercised), rate limits on the
   API routes, CSP header, data export/delete (DPDP).
7. Slack Events API + "Add to Slack" and Telegram webhooks once on a VPS; WhatsApp later.
8. Capacitor wrapper for store apps (see README) when wanted.
