# Push and release commands (for the person who pushes)

Run these from `~/apps/northstar-agent-stack`. Prerequisites: `gh auth status` is OK with push rights, and the
working tree is clean. `main` may have new v0.3 commits from another worker, so fetch first.

```bash
cd ~/apps/northstar-agent-stack
git fetch origin
git status                         # must be clean

# 1. Merge docs into main
git checkout main
git pull --ff-only origin main
git merge --no-ff docs -m "Merge branch 'docs': public README, docs, license, templates, release notes"
# If conflicts appear (README.md, CHANGELOG.md, docs/): keep the docs-branch text, then re-add any v0.3 lines from main.
git log --oneline -5

# 2. Re-scan before pushing
~/bin/gitleaks detect --source . --redact
~/bin/gitleaks detect --source . --no-git --redact

# 3. Push
git push origin main

# 4. Releases (v0.2.0 tag already exists on origin)
gh release create v0.2.0 --repo https://github.com/srinivasaphanindra/northstar-agent-stack \
  --title "v0.2.0: Buffer OAuth, scheduled posts, content check, Slack harness" \
  --notes-file release-notes/v0.2.0.md --verify-tag --latest

# v0.1.0, recommended: no tag of its own. v0.2.0 is the root commit (it has no parent), so there is nothing earlier to
# tag. The v0.1.0 history lives in CHANGELOG.md and release-notes/v0.1.0.md. Don't run the command below unless the
# owner explicitly wants a GitHub release page for v0.1.0. It creates a v0.1.0 tag on the same commit as v0.2.0, marked
# as a historical prerelease and not as latest:
# gh release create v0.1.0 --repo https://github.com/srinivasaphanindra/northstar-agent-stack \
#   --target 8490f60918909bcdf5b8b46cc18f1af2f63ea301 --prerelease --latest=false \
#   --title "v0.1.0 (historical notes: app phase 1; code not in this repo)" \
#   --notes-file release-notes/v0.1.0.md

# 5. Repo description and topics
gh repo edit https://github.com/srinivasaphanindra/northstar-agent-stack \
  --description "AI social media agent for Indian small businesses: chat in Slack, drafts in your voice, publishes only after a one-tap OK. Next.js + n8n + Buffer." \
  --add-topic ai-agent --add-topic social-media-automation --add-topic small-business --add-topic india \
  --add-topic nextjs --add-topic n8n --add-topic slack-bot --add-topic human-in-the-loop
```
