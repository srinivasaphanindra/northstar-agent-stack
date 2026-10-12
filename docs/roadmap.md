# Northstar app roadmap

Sources: `~/apps/northstar-app/STATUS.md` (phase 1, 2026-10-12), `northstar-aaas-platform.md` (§5 Option B, §8),
`n8n-client-bot.md` (client 1 bot v2: guardrails, Viral coach, tier update + hourly approval batches).

---

## v0.1 — today's build (2026-10-12) · tagged `v0.1.0`

**Scope (what exists now)**
- Next.js 15 + Tailwind 4 mobile-first PWA (manifest, service worker, offline page, install button, web push opt-in stub).
- Auth (email + password, scrypt, DB sessions, throttle), invites with roles owner/admin/member; multi-tenant workspaces
  (`workspaceId` on every row), append-only audit log.
- Onboarding wizard: business → plan + real UPI/PhonePe QR (admin marks paid by hand; Gumroad hidden) → 10-question
  interview as chat → connect tools → chat channel (web + Slack; Telegram behind `CHANNEL_TELEGRAM=false`).
- Settings: AI key (platform / BYO OpenRouter, encrypted), integrations (Buffer, Hootsuite, GoHighLevel, webhook, Slack),
  guardrails, team, chat channels, plan & billing.
- Web chat with the agent via worker job queue (live OpenRouter confirmed), approvals inbox, approve → Buffer only when not dry-run.
- Admin page (workspaces, mark paid, usage, failed jobs, audit log, platform AI daily spend with $3 hard stop);
  per-workspace usage/spend caps (80/100/150 %).
- Deploy kit (README VPS + systemd + Caddy + Postgres guide, Dockerfile + compose — untested).
- Runs locally only on 127.0.0.1:3100; `PUBLISH_DRY_RUN=true`, no Buffer key, no tunnel.

**Tests**
- 26 vitest tests (incl. platform daily AI cap); headless E2E 12/12 with screenshots in `docs/screens/`.

**Done when**
- Already done: tests green, demo up on the box, CHANGELOG entry + annotated tag `v0.1.0` in the app repo.

---

## v0.2 — Buffer OAuth, real Slack test, AI content check

**Scope**
- Buffer OAuth (PKCE) replacing the personal API key; Buffer channel picker; "now" / scheduled times (STATUS next #3).
- Real Slack bot test with a throwaway bot token (today only mocked) (STATUS next #1).
- LLM content classifier at approve time, as the n8n bot has (today only the free regex check runs), plus the
  "numbers must be in notes" rule (STATUS next #5).

**Tests**
- Unit tests for the OAuth flow (state/PKCE verifier, token store, refresh) and the classifier (pass / block / LLM-error fallback to regex).
- Manual: connect a real Buffer account, pick a channel, approve one draft with dry-run on, then one scheduled post with dry-run off.
- Manual: Slack bot in a test workspace — message the agent, receive a draft, typed approve, check audit log.
- E2E suite stays 12/12+; vitest count grows and stays green.

**Done when**
- A draft approved from Slack lands in the chosen Buffer channel at the chosen time via OAuth (no personal key), a
  flagged draft is blocked by the classifier with a reason, and all tests pass.

---

## v0.3 — port client-1 n8n features into the app

**Scope**
- **Tier caps** per plan, enforced in code (not prompt):
  - Starter (₹4,999/mo): X 5 posts + 10 replies/day; LinkedIn 4 posts/week + 2 comments/day; Facebook 1 post + 3 comments/day.
  - Growth (₹14,999/mo): 2x Starter.
  - Viral (₹24,999/mo): 3x Starter, plus image creation, support and CRM integration.
  - All tiers approval-gated, 4-week warm-up ramp and pacing (gaps, per-hour limits, quiet/active hours); no automated
    likes on X; warnings pause a platform and step the ramp back (as in `guardrails.yaml` v3 / `coach.py`).
- **Approval batches**: hourly-style batches within active hours with jitter, 2–4 items, max 1 post per batch,
  skip after ignored, item expiry, typed-answer parsing (port of `batches.py`).
- **Viral coach**: manual-action coach for Viral — Open-in-app links, task texts, reminders, streaks/wins,
  coordination score + tip in the weekly report (port of `coach.py`).
- Plan switch updates the workspace caps; usage page shows plan, warm-up week, caps and ceilings.

**Tests**
- Port the client1-state offline tests (`test_viral.py`, 29 tests) to vitest: ramp steps, caps per tier/platform,
  gaps/hourly limits, like refusal, batch decisions (run/wait/skip_ignored/still_there), coach tasks and score.
- E2E: Starter workspace hits a cap and gets the refusal message; batch appears in approvals inbox; Viral workspace sees coach tasks.

**Done when**
- The three tiers with the numbers above are selectable in onboarding/billing, caps and warm-up are enforced in the
  worker, batches and coach run in the app with the same behaviour as the n8n client 1 bot, tests green, and
  client 1 could be moved off n8n.

---

## v1.0 — ready for the VPS move

**Scope**
- Deployment: run on a VPS with the existing kit (systemd + Caddy HTTPS, or the Docker compose once tested); domain.
- Database: switch to Postgres (schema from `npm run db:pg-schema`) and exercise it.
- Secrets: env files chmod 600 outside the repo, encryption key for stored AI/Buffer keys, no secrets in git.
- Security: rate limits on API routes, CSP header, data export/delete (DPDP).
- Channels needing a public URL: Slack Events API + "Add to Slack", Telegram webhooks; production Buffer redirect URI.
- Notifications: web push sending (VAPID), email (Resend), password reset / magic link.
- Backups: scheduled DB dumps (like `n8n-backup.sh`: consistent snapshot, chmod 600, keep last 14, encrypted off-box copy)
  with a written restore procedure.
- Monitoring: health check + watchdog/systemd restart, failed-jobs and platform-spend alerts from the admin page, logs.

**Tests**
- Full vitest + E2E suite run against Postgres.
- Restore drill: restore last backup onto a fresh DB and log in.
- Smoke on the VPS: HTTPS, sign-up → onboarding → chat → approve → Buffer, Slack event round-trip, rate-limit and CSP checks.

**Done when**
- The app runs on the VPS over HTTPS on Postgres, survives a reboot, backups run daily and a restore has been
  tested, alerts reach the admin, and one friendly business completes a week end to end before sign-ups open.
