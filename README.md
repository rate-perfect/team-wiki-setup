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

It also copies the **worktree cleanup** tool to `~/.claude/bin`. It finds
git worktrees whose PR is merged and that hold nothing else. See
[Worktree cleanup](#worktree-cleanup) below.

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

## Worktree cleanup

Claude sessions make a git worktree for each change. A session that ends
before its PR merges never cleans up, so worktrees pile up. The installer
copies `bin/worktree-cleanup` to `~/.claude/bin/worktree-cleanup`. You run it
yourself; it is not a hook.

```bash
# Dry run: list every worktree with REMOVE or KEEP and the reason. Changes nothing.
~/.claude/bin/worktree-cleanup --parent ~/projects/rate-perfect-workspace
~/.claude/bin/worktree-cleanup ~/m3/deals-module-api ~/m3/deals-view

# Remove the REMOVE ones.
~/.claude/bin/worktree-cleanup --apply --parent ~/projects/rate-perfect-workspace
```

- `REPO` is a git repo, or any of its worktrees. Give as many as you like.
- `--parent DIR` checks every git repo directly inside `DIR` (and `DIR`
  itself if it is a repo). Sibling worktree folders such as `../<repo>-<name>`
  are matched back to their repo, so each repo shows once.

A worktree is REMOVE only when all of these are true:

1. It is a linked worktree. It is not the main checkout and not locked.
2. It is on a branch. A detached HEAD is kept.
3. The branch's GitHub PR is merged, and the worktree's last commit is the
   PR's last commit. So nothing was added after the merge. (Repos
   squash-merge, so the script asks GitHub, not git.)
4. `git status --porcelain` is empty: no changes and no untracked files.
   Ignored files are fine.
5. No open PR uses the branch.
6. No running process has its current folder inside the worktree. Another
   Claude session may still be working there. The script asks `lsof`.

If it can't check something, it keeps the worktree and says why. For example:
no GitHub remote (Azure DevOps repos), `gh` failed, or you are offline. If
`lsof` fails, it keeps every worktree. Pass `--no-process-check` to skip that
check on purpose.

With `--apply`, for each REMOVE it runs `git worktree remove` (never
`--force`), then `git branch -D` on the local branch (`-D` because git does not
see a squash-merged branch as merged). Then it runs `git worktree prune`. It
never touches remote branches. Needs `python3`, `gh` (logged in) and `lsof`.
Tests: `python3 tests/test_worktree_cleanup.py`.
