# Development and testing

## App (`app/`)
```bash
cd app
npm ci
npm run typecheck          # no output = OK
npm test                   # vitest: 45 tests
npm run screens            # headless E2E + screenshots (needs Chrome and a running app; see SETUP.md)
npm run slack:live-check   # optional: a throwaway Slack workspace only, read-only by default
```
Tests run with `LLM_MODE=mock` and `PUBLISH_DRY_RUN=true`. Slack is tested against the local fake API in `tests/harness/`.

## state-helper
```bash
python3 state-helper/tests/test_batches.py
python3 state-helper/tests/test_viral.py    # hourly_pacing_and_random_gap is a known time-of-day flake
python3 state-helper/tests/test_fix3.py
```

## Services
`bin/northstar-app-up.sh`, `bin/state-helper-up.sh`, `bin/n8n-up.sh`, `bin/browser-agent-up.sh` (`start|stop|status`).
`bin/services-watchdog.sh ensure` keeps them all up. Runtime files go to `.run/` (gitignored).

## Before you open a pull request
```bash
gitleaks git . --redact          # history
gitleaks dir . --redact # working tree
```
Keep IDs and contacts as placeholders, and keep screenshots on demo data.
