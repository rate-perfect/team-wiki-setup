# Team Wiki setup

One-time setup so Claude Code publishes shareable artifacts to the perfecti.io
**Team Wiki** (https://wiki.perfecti.io) from any repo.

## Install (per developer, ~1 minute)

```bash
git clone https://github.com/rate-perfect/team-wiki-setup.git
cd team-wiki-setup
WIKI_PUBLISH_TOKEN=<token> ./install.sh
```

Get the token from the Slack message (ask David). Prerequisites:
`node` and `jq` (`brew install jq`). Restart Claude Code afterward. Re-run any
time to update — it's idempotent.

## What it does

It installs a `publish-to-wiki` skill, adds a strong-default instruction to your
`~/.claude/CLAUDE.md`, sets the publish endpoint + token in your
`~/.claude/settings.json`, and installs a small **reminder hook** (PostToolUse
on the Artifact tool) that nudges Claude to mirror shareable output to the wiki
right after it publishes an artifact — a reminder, never an auto-publish, so
you still confirm anything public and scratch/secret pages are skipped. Needs
`jq`. Re-running `install.sh` is safe and idempotent.

After that, whenever you ask Claude for a report, doc, dashboard, or HTML tool,
it also **mirrors a copy to the wiki and gives you the URL** — internal
(team-only) by default, public (unlisted link) only when you ask for it.

No GitHub access is required — publishing goes through the wiki's API.
