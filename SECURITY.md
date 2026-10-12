# Security policy

## Supported versions

| Version | Supported |
|---|---|
| v0.2.x | Yes |
| < v0.2 | No |

## Reporting a vulnerability

**Please don't open a public issue, discussion or pull request for security problems.**

Report privately through GitHub's **"Report a vulnerability"** button (Security tab → Advisories) on this repository.
<!-- PLACEHOLDER: private security contact to be added by the owner. Do not put a personal email here. -->

Please include the affected component (`app/`, `workflows/client-bot/`, `state-helper/`, `browser-agent/`, `bin/`), the
steps to reproduce, and the impact. We aim to reply within 5 working days.

## Scope and notes

- Secrets are never committed. Every `.env` comes from its `.env.example` and should be `chmod 600`.
- Exported n8n workflows contain no credentials, and Slack IDs are zero-filled placeholders.
- Workspace secrets are envelope-encrypted with `APP_MASTER_KEY` (AES-256-GCM, bound to workspace and purpose).
- If you find a real token, phone number, email or customer data in this repository, please report it privately as above.
