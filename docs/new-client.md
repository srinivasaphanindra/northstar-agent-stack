# Spinning up a client instance

This repo holds only the Northstar system. Each client runs as its own agent instance (its own state helper, n8n bot workflows, guardrails, audit log and Chrome profile) **outside the repo**, on the server. Use a short key such as `acme`.

1. **Instance dir:** `mkdir -p ~/agency/clients/acme` and copy `templates/guardrails.example.yaml` to `guardrails.yaml` and `templates/notes.example.md` to `notes.md` there. Fill in the placeholders (handles, Slack admin ids, plan, start date). The tier caps (trial / starter / growth / viral, warm-up weeks) are in the template; change `plan:` only.
2. **State helper:** `CLIENT_KEY=acme CS_PORT=8781 CS_GUARDRAILS=~/agency/clients/acme/guardrails.yaml bin/state-helper-up.sh start` (one port per instance; `CS_PUBLISH_DRY_RUN=1` keeps publishing dry). Check `curl 127.0.0.1:8781/health`.
3. **n8n bot:** import `workflows/client-bot/*.json` (`n8n/import-workflows.sh`), then for this client rename "Example Client" to the client's name, set the Slack channel id, the helper URL (`http://127.0.0.1:8781`) and the credentials. Publish the sub-workflows (tools, read receipt) before the bot. Keep schedule workflows (batches, coach, drafter) **inactive** until the client is approved for them.
4. **Browser (optional):** `CLIENT_KEY=acme CLIENT_CDP_PORT=9360 CLIENT_DISPLAY=:21 bin/client-chrome.sh start`. The client signs in themselves; the stack never signs in.
5. **Dashboard:** add an entry to `app/config/clients.yaml` (key, plan, start date, guardrails/audit paths, `state_helper_url`, `n8n_inbound_workflow`).
6. **Self-heal:** add `acme:8781` to `SELFHEAL_INSTANCES` (and the CDP port to `SELFHEAL_CDP_PORTS`).
7. **Test round trip** in the client's Slack channel with a TEST post: the audit log must show `publish_result ... dry_run` and the caps must stay at 0.

Never commit an instance dir, a `.env`, tokens, Slack ids or handles to this repo.
