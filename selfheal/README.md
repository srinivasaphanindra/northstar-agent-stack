# selfheal — light self-heal supervisor (v0.5.0)

Stdlib Python, one pass every 5 min (`selfheal.py loop`, flock; `services-watchdog.sh` calls `selfheal.py ensure`). `selfheal.py once` runs one pass and prints it.

Checks: n8n health and task-runner "Offer expired" storms in the n8n log, client0-state (:8772), client1-state (:8771), the app (:3100) and its worker, the browser agent, the Chrome CDP endpoints.

Recovery: restarts only through the existing `bin/*-up.sh` scripts, exponential backoff (5, 10, 20 min), max 3 tries per service per rolling hour, state in `selfheal-state.json`. Reaps only duplicate Chromes on a profile whose CDP another live instance holds, and orphan headless/temp-profile Chromes with a dead parent; never a CDP holder, its launcher or its children; closes only about:blank / duplicate tabs. Retries a failed n8n execution once, only for workflows on `SELFHEAL_RETRY_ALLOWLIST` (default: the read receipt) and never anything matching publish/post/reply/send/buffer/approve. Buffer drafts that failed for a missing post type/text go back to `pending` for the approval flow to rebuild (never posted); drafts with `<<<`/`>>>` are blocked and alerted. First run records existing audit rows as a baseline and does not act on them.

Every action: a row in `$STATE/selfheal-audit.tsv`. Unresolved problems, platform warnings, captcha or sign-in pages: a JSON file in `$STATE/alerts/`, no retries while a warning is open.

Hard limits (code + tests): never writes its own code, guardrails or caps, never signs in, never retries past platform warnings.

Paths are env-overridable (`SELFHEAL_STATE_DIR`, `SELFHEAL_BIN`, `SELFHEAL_N8N_LOG`, `SELFHEAL_N8N_ENV`, ...); defaults are under `~/agency` and `~/apps`. Tests: `python3 selfheal/tests/test_selfheal.py`.
