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
`node`, `jq` (`brew install jq`) and `python3`. Restart Claude Code afterward. To update,
run `git pull --ff-only && ./install.sh` in your clone any time — it's idempotent.

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
as `git status`, `cat`, `grep` or `npm view`) are not paused. Builds, installs
and package commands count as changes: `npm install`/`ci`/`run`, `make`,
`dotnet test`, and every `pnpm` or `yarn` command (even `pnpm list`; the hook
doesn't tell their subcommands apart).

Running a repo's code also counts, from any folder, so work started in one repo
follows another repo's rules when it touches it:
- a build or test pointed at it (`npm --prefix <repo> test`, `npm -C`,
  `pnpm --dir <repo> build`, `yarn --cwd`, `make -C <repo>`, `env -C <repo>`),
  and `npm test`, `npm start`/`restart`/`stop`, `npm exec` or `npx` run inside it, also behind
  `timeout`, `time` or `env`;
- a script run with `node`, `tsx`, `python`, `bash` and the like (also through
  `npx`, `npm exec`, `pnpm dlx`, `env`, `nice` or `timeout`, inside `for`,
  `while`, `until` or `if`, or a `#!` script run directly by its path) that lives
  in the repo. Only the script counts, not the files it is given to work on;
  with `node --test`, every test file counts (up to 200 per command). A
  JavaScript or TypeScript script also counts for each repo it imports code from
  by a relative or absolute path (only the script's own imports; up to 300
  imports leaving the script's repo per command).
- a tool installed in the repo you're in, run by path (`./node_modules/.bin/vitest`,
  `.venv/bin/pytest`, or another repo's), counts for the folder the command
  runs in, even when a worktree links `node_modules` from its main checkout. A
  package linked from another repo's source (`npm link`) counts for that repo.

Text (a heredoc, a commit message) never counts. Runners are known by name, so
`/opt/homebrew/bin/node` or a virtualenv's `python` is just the runner. Tool
folders that happen to be git checkouts are left alone: Claude Code's own
`~/.claude` (where plugins are cloned) and Homebrew's prefix, compared by real
path. If `~/.claude` is a link into a dotfiles repo, changing files there still
counts for that repo. The hook also lists the repo's
commands, agents and skills (`.claude/skills`), so their steps are followed
rather than improvised. Subagents keep their own record, so a subagent that
runs a repo's tests is paused once for its rules too. It needs
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
- **Main checkout guard (off by default).** (Not the same as this repo's own guard for
  contributors, which is always on here; see `CLAUDE.md`.) If you run several Claude sessions
  on the same repo, you can make Claude work only in git worktrees. Add a
  section headed `# Never change a main checkout` to your own
  `~/.claude/CLAUDE.md`, outside the team block, saying what you allow. The
  hook then refuses edits, commits, branch switches, installs and builds in a
  repo's main checkout and quotes your section back: the common npm commands
  that install, remove, link, rebuild, create a package or run a script (such
  as `npm ci`, `npm add` or `npm run build`; not `npm publish`, `npm pack` or
  `npm audit fix`), every `pnpm` or `yarn` command including `pnpm test`,
  `make` and `dotnet`, also when `--prefix`, `-C`, `--dir`, `--cwd` or
  `--directory` points at it. Like before, it reads each command as written: a
  build behind `timeout`, `env` or a shell keyword (`if …; then`) isn't
  refused. Running its code with `npm test`,
  `npm start`, `npx` or a script only reads it, so that's allowed. Delete the section to turn it
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
# Dry run: list every worktree with REMOVE or KEEP and the reasons. Changes nothing.
~/.claude/bin/worktree-cleanup --parent ~/projects/rate-perfect-workspace
~/.claude/bin/worktree-cleanup ~/m3/deals-module-api ~/m3/deals-view

# Remove the REMOVE ones.
~/.claude/bin/worktree-cleanup --apply --parent ~/projects/rate-perfect-workspace
```

- `REPO` is a git repo, or any of its worktrees. Give as many as you like.
- `--parent DIR` checks every git repo directly inside `DIR` (and `DIR`
  itself if it is a repo). Sibling worktree folders such as `../<repo>-<name>`
  are matched back to their repo, so each repo shows once.
- Only worktrees inside a `--parent` folder, or inside the folder that holds a
  `REPO`, can be removed. A repo can also have worktrees somewhere else (for
  example in `/tmp`, or a repo in `~/m3` with a worktree in your workspace).
  Those are KEEP, "outside the scanned folders". Pass `--include-outside` to
  allow them.

A worktree is REMOVE only when all of these are true:

1. It is a linked worktree. It is not the main checkout and not locked.
2. It is on a branch. A detached HEAD is kept.
3. The branch's GitHub PR is merged into the repo's default branch, and the
   worktree's last commit is the PR's last commit. So nothing was added after
   the merge. (Repos squash-merge, so the script asks GitHub, not git.)
4. `git status` is empty, untracked files and submodules included. No file is
   marked skip-worktree or assume-unchanged (those hide edits from
   `git status`).
5. Every ignored file in it is rebuildable (see below).
6. No other worktree or git repo (any `.git` file or folder) is inside it.
   To stay fast, it does not walk all of `node_modules`. It looks only for a
   `.git` in `node_modules/<pkg>/` and `node_modules/@scope/<pkg>/`.
7. No open PR uses the branch.
8. No running process has its current folder inside the worktree. The script
   asks `lsof`.

**Removing a worktree deletes its ignored files.** So the script keeps a
worktree that has any ignored file that is not on its rebuildable list, and
names the first few. A `.env`, a `terraform.tfvars` or a notes file keeps it.
The rebuildable list (the `REBUILDABLE` constant in the script) is:
`node_modules/`, `.next/`, `.expo/`, `dist/`, `build/`, `src/generated/`,
`.terraform/`, `coverage/`, `__pycache__/`, `.venv/`, `*.egg-info/`, `.mypy_cache/`,
`.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `next-env.d.ts`,
`expo-env.d.ts`, `*.tsbuildinfo` and `.coverage`. .NET `bin/` and `obj/` are
not on it, because `bin/` often holds hand-written scripts. A folder on the
list counts only when it holds no tracked files. So an ignored `build/.env`
in a tracked `build/` folder keeps the worktree.

If it can't check something, it keeps the worktree and says why. For example:
no GitHub remote (Azure DevOps repos), `gh` failed, or you are offline. If
`lsof` fails, it keeps every worktree. Pass `--no-process-check` to skip that
check on purpose.

The `lsof` check only sees a process whose current folder is inside the
worktree. Claude sessions often work in a worktree by its full path from
another folder, so the check rarely sees them. That is why `--apply` checks
again just before each remove (next paragraph).

With `--apply`, for each REMOVE it first checks again: `lsof`, `git status`,
ignored files, nested repos, and that HEAD and the branch still point at the
checked commit. If anything changed, it skips that worktree. Then it runs
`git worktree remove` (never `--force`), then
`git update-ref -d refs/heads/<branch> <checked commit>`, which deletes the
local branch only if it still points at that commit. It also removes the
branch's settings (`git config --remove-section branch.<branch>`). Then it
runs `git worktree prune`. It never touches remote branches.

Good to know before you run `--apply`:

- Stashes are shared by all worktrees of a repo. They are kept when a worktree
  is removed.
- Removing a worktree and its branch also drops their reflogs. Commits that
  were reset away in that worktree can no longer be found with `git reflog`.

Needs `python3`, `gh` (logged in) and `lsof`.
Tests: `python3 tests/test_worktree_cleanup.py`.
