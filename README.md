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
`node`, `jq` (`brew install jq`) and `python3`. Restart Claude Code afterward. Re-run any
time to update — it's idempotent.

## What it does

It installs the team skills in `skills/` into `~/.claude/skills` so they work
in every repo: `publish-to-wiki`, and `verification` (run `/verification` to
make Claude prove or disprove a claim, such as a suspected bug cause, instead of
guessing). It also adds a strong-default instruction to your
`~/.claude/CLAUDE.md`, sets the publish endpoint + token in your
`~/.claude/settings.json`, and installs a small **reminder hook** (PostToolUse
on the Artifact tool) that nudges Claude to mirror shareable output to the wiki
right after it publishes an artifact — a reminder, never an auto-publish, so
you still confirm anything public and scratch/secret pages are skipped. Needs
`jq`. Re-running `install.sh` is safe and idempotent.

It also installs the **repo rules hook** (`hooks/repo-rules-first.py`). Before
Claude changes any git repo, it must first read that repo's own rules
(`CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md`, `docs/team-knowledge.md`
and files they import). This works even when the Claude session started in a
different folder. If Claude hasn't read them, the hook pauses the change and
shows the rules (or names the files to read). The hook holds no rules of its
own, so each repo stays the source of truth. Commands that only read (such
as `git status`, `cat` or `grep`) are not paused; builds, tests and package
commands (`npm run`, `pnpm`, `make`, `dotnet test`) count as changes. It needs
`python3`, lets everything through if it hits an error, and never pauses the
same repo more than 4 times per session.

The installer keeps a copy of your previous `~/.claude/CLAUDE.md` and
`~/.claude/settings.json` as `.bak` files next to them. It leaves
`settings.json` untouched and stops if that file isn't valid JSON.

Optional settings for the repo rules hook:

- **Where your clones live.** The hook uses this only to find the repo named
  in a PR command run from outside it (`gh pr create --repo ...`,
  `az repos pr create ...`). The default is
  `~/projects/rate-perfect-workspace:~/projects:~/m3:~/m3/boss-modernization:~/m3/boss5`.
  To change it, set `REPO_RULES_CLONE_ROOTS` (colon-separated) in the `env`
  block of `~/.claude/settings.json`.
- **Main checkout guard (off by default).** If you run several Claude sessions
  on the same repo, you can make Claude work only in git worktrees. Add a
  section headed `# Never change a main checkout` to your own
  `~/.claude/CLAUDE.md`, outside the team block, saying what you allow. The
  hook then refuses edits, commits, branch switches and builds in a repo's
  main checkout and quotes your section back. Delete the section to turn it
  off.

After that, whenever you ask Claude for a report, doc, dashboard, or HTML tool,
it also **mirrors a copy to the wiki and gives you the URL** — internal
(team-only) by default, public (unlisted link) only when you ask for it.

No GitHub access is required — publishing goes through the wiki's API.
