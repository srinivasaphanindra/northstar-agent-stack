# northstar-agent-stack

Private test bundle of the Northstar agent stack, snapshot **v0.2.0 (2026-10-12)**. Runs fully local and in
**dry-run**: nothing is posted, liked or commented anywhere.

| Path | What |
|---|---|
| `app/` | Northstar app (Next.js 15 + Prisma, web + worker) at tag v0.2.0. Own `README.md`, `STATUS.md`, `CHANGELOG.md`. |
| `n8n/workflows/` | 14 client1 n8n workflows exported from the live instance: inactive, credentials, webhook ids and pinned data removed. |
| `n8n/` | `package.json` (n8n 2.42.6), `.env.example`, `owner.env.example`, `import-workflows.sh`. |
| `client1-state/` | Python helper for the client1 bot (127.0.0.1:8771): memory, guardrails, approvals, batches, coach. Stdlib only. Offline tests in `tests/`. |
| `clients/client1/` | Client 1 guardrails yaml (v3) + an example notes file. |
| `guardrails/` | Guardrails template (`guardrails-default.yaml`) for new clients. The app's own copy is `app/config/guardrails-default.yaml`. |
| `browser-agent/` | browser-use HTTP service (127.0.0.1:8770) used by the "Tool: Browser task" workflow. |
| `bin/` | Service scripts (start/stop/status, watchdog, backup). All paths are repo-relative; runtime files go to `.run/`. |
| `docs/` | `roadmap.md`, `northstar-aaas-platform.md`, `n8n-client-bot.md` (ops log; IDs/handles replaced by placeholders). |

Start with **[SETUP.md](SETUP.md)**.

Secrets: none are committed. Every `.env` is created from its `.env.example`; personal handles, Slack ids, phone
numbers, emails and account ids were replaced with placeholders such as `your_x_handle`, `U0000000001`,
`0000000000`, `admin@example.com`.
