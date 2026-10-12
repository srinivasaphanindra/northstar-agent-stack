# Client 0: your own accounts through Slack, run like client 1 (dry run)

Client 0 is the owner's own X / LinkedIn / Facebook, handled through a Slack channel exactly like client 1:
chat with the bot, post previews with ✅ / ✏️ / ❌, `limits`, watchlist and `remember:` notes. While piloting,
**nothing is published**.

## Pieces
- `client0-state`: a copy of `client1-state` with `client1 -> client0`, `8771 -> 8772`, `C1_ -> C0_` and two changes:
  1. `C0_PUBLISH_DRY_RUN` (default on): an approved post is logged as `publish_result dry_run` (an approved reply or comment
     as `engage dry_run`) and no Buffer ops are returned.
  2. A content-check category `not_plain_text` blocks drafts that contain `<<<` or `>>>`.
  Start it with `C0_PUBLISH_DRY_RUN=1 C0_WORKER=0 C0_BROWSER=http://127.0.0.1:9` (engage worker and browser agent off).
- n8n: duplicate the client 1 workflows with a `Client0` prefix, pointing at `127.0.0.1:8772` and your `#client0` channel id.
  Replace every Buffer HTTP node and browser-agent call with a no-op Code node. Point the OpenRouter model nodes at
  `http://127.0.0.1:8772/or/n8n/v1` (options.baseURL) so spend lands on client 0's meter. Activate only the Slack poller,
  the bot, actions and the tools. Keep the schedule workflows (batches, coach, engage drafter) **inactive**, and stub
  their calls in the actions workflow.
- `guardrails.yaml` (this folder): Viral plan, start date, week-1 caps, approval-gated, inbound rules. Fill in the placeholders.
- `notes.example.md`: copy to `notes.md` next to the guardrails and fill it in.

## Test round trip
Post a clearly labelled TEST preview to `#client0`, register it with `POST /proposals`, approve it, and check that
`audit-log.tsv` shows `publish_result ... dry_run ... nothing sent to Buffer/X/LinkedIn/Facebook`.
