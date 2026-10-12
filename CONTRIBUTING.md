# Contributing

Thanks for your interest. This is a **proprietary** project (see [LICENSE](LICENSE)). Contributions are welcome only
by invitation, and opening an issue or pull request doesn't grant any rights to the code.

## Ground rules
- **Safety first.** Every change has to keep the approval gate: nothing public happens without a human OK. Never add
  automated likes, follows or DMs.
- **No secrets or personal data** in code, docs, tests, screenshots or commit messages. Use placeholders
  (`admin@example.com`, zero-filled Slack IDs, `your-upi-id@bank`).
- Keep `PUBLISH_DRY_RUN=true` and `LLM_MODE=mock` in tests.

## Workflow
1. Open an issue first (bug or feature template).
2. Branch from `main`: `feat/...`, `fix/...` or `docs/...`.
3. Run the checks in [docs/development.md](docs/development.md): `npm run typecheck`, `npm test`, the client1-state tests, and gitleaks.
4. Open a pull request using the template and update `CHANGELOG.md` under "Unreleased".

## Style
- App core (`app/src/core`) never imports from `next/*` or `src/app`, and tenant code takes a `Ctx`.
- `client1-state` stays standard-library only.
- n8n workflows are exported inactive, with credentials and pinned data removed.
