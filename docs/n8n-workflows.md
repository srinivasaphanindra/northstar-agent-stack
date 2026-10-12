# n8n workflows (client 1 pilot)

The 14 workflows in `n8n/workflows/` were exported from the pilot instance. They are **inactive**, and credentials,
webhook IDs and pinned data were removed. Slack channel and user IDs are zero-filled placeholders.

| File | What it does |
|---|---|
| `client1-bot-slack-poller-1-min.json` | Reads the client's Slack channel every minute and turns messages into work items. |
| `client1-bot.json` | The agent: builds context from client1-state memory + notes, calls the LLM, replies in the thread. |
| `client1-bot-actions-approvals-commands-notify.json` | Approve / discard / edit commands, plan switch, owner notifications. |
| `client1-batches-hourly-drafts-15-min-check.json` | Hourly-style approval batches within active hours (2–4 items, max 1 post). |
| `client1-coach-action-lists-2x-day.json` | Viral coach: manual-action task lists twice a day. |
| `client1-coach-reminders-checks-30-min.json` | Coach reminders and checks. |
| `client1-engage-drafter-x-2x-day.json` | Drafts X replies to chosen or followed accounts (approval-gated). |
| `client1-bot-dry-run-harness.json` | Test harness that runs the bot without posting. |
| `client-bot-read-receipt-reusable.json` | 👀 → ✅ read receipts on the human's message. |
| `client-onboarding-chat-interview-reusable.json` | 10-question onboarding interview in chat. |
| `client-onboarding-connect-accounts-client1.json` | Guided account connection. |
| `tool-buffer-post-client1.json` | Sub-workflow: send an **approved** post to Buffer (draft / queue / now). |
| `tool-propose-post-client1.json` | Sub-workflow: create a draft for approval. |
| `tool-browser-*.json` (browser task) | Sub-workflow: read-only browser task via `browser-agent`. |

## Import
```bash
cd n8n && cp .env.example .env && chmod 600 .env   # set N8N_ENCRYPTION_KEY
npm install --no-audit --no-fund && cd .. && bin/n8n-up.sh
n8n/import-workflows.sh                              # imports inactive
```
Then create your own credentials (Slack, Buffer `httpHeaderAuth`, OpenRouter), replace the placeholder IDs, and
test with the dry-run harness before you activate anything. See [SETUP.md](../SETUP.md).
