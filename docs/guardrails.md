# Guardrails

Northstar is built so that a small business can trust it with its public accounts. The rules below hold for every plan.

## Always
- **Approval-gated.** Posts, replies and comments are drafts until a listed approver says OK (one tap in the app, or a typed `approve <code>` in Slack).
- **Never automated:** likes, follows, unfollows, DMs, mass actions, identical comments, hashtag spam.
- **Replies only to accounts the client chose or follows.**
- **4-week warm-up.** Caps ramp up over the first four weeks, with pacing (minimum gaps, per-hour limits, quiet and active hours). A platform warning pauses that platform and steps the ramp back.
- **Content check.** No fake reviews or testimonials, guaranteed results, or medical or money promises. Numbers must appear in the client's notes. Political and religious topics are off by default.
- **Kill switch.** Anyone can pause everything. Only the owner can resume.
- **AI budget.** Warning at 80 %, extras stop at 100 %, chat stops at 150 %. There's also a platform-wide daily hard stop.

## Plan caps

| Plan | X | LinkedIn | Facebook | Extras |
|---|---|---|---|---|
| Starter (₹4,999 / month) | 5 posts + 10 replies a day | 4 posts a week + 2 comments a day | 1 post + 3 comments a day | |
| Growth (₹14,999 / month) | 2× Starter | 2× Starter | 2× Starter | |
| Viral (₹24,999 / month) | 3× Starter | 3× Starter | 3× Starter | Image creation, support, CRM integration |

## Where it's enforced (v0.2.0)
- **Client 1 pilot:** `clients/client1/guardrails.yaml` (v3) enforced by `client1-state/guardrails.py`, `batches.py` and `coach.py`.
- **App:** `app/config/guardrails-default.yaml` (defaults → plan → per-workspace overrides) and the Guardrails settings page. In v0.2.0 the app has daily caps, content rules, an AI budget and the kill switch. The per-plan tier caps and warm-up are ported in v0.3 ([roadmap](roadmap.md)).
- **Template for new clients:** `guardrails/guardrails-default.yaml`.
