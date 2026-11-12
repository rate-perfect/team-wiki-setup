# Team Wiki setup: guide for Claude

This repo is the one-time setup each person runs so Claude Code publishes to the team wiki.
`install.sh` copies the skills, hooks and tools here into `~/.claude` and edits the person's
`~/.claude/CLAUDE.md` and `~/.claude/settings.json`. `README.md` says what each part does.

## This repo is public

Anyone can read every file, commit, PR and review comment here.

- Never add secrets, tokens, people's email addresses, internal host names, private repo
  names, customer names or business details. Test data uses obvious fakes.
- The same goes for PR titles, descriptions and commit messages.

## Work in a worktree

The main checkout is shared by every developer and agent session. An edit, commit or branch
switch there can wipe out another session's work, or land on whatever branch it is on. So make
every change in a worktree cut from the latest `main`:

```bash
git -C <main checkout> fetch origin
git -C <main checkout> worktree add ../team-wiki-setup-<short-name> origin/main -b <type>/<short-name>
```

- In the main checkout, only read, `git fetch`, `git worktree add/remove/prune`, and refresh with
  `git checkout --detach origin/main` or `git merge --ff-only origin/main`.
- **Enforced:** `.claude/hooks/guard-main-checkout.mjs`, wired in `.claude/settings.json`, refuses
  Claude's edits, commits, branch switches and releases in a main checkout. Files git ignores
  (`CLAUDE.local.md`, local settings, plans) stay editable. The hook guards any repo whose main
  checkout contains the hook file. The same file is in every guarded repo of the team; change
  every copy together.
- This is not the optional main-checkout guard in `hooks/repo-rules-first.py`. That one is
  installed into `~/.claude` for each person and is off unless they turn it on.

## Before opening a PR

- `python3 tests/test_worktree_cleanup.py` passes. New or changed `worktree-cleanup` behaviour
  has a test.
- `python3 tests/test_repo_rules_first.py` passes. New or changed `hooks/repo-rules-first.py`
  behaviour has a test.
- `shellcheck` is clean on the shell scripts you changed.
- `README.md` still says what the code does.
- Re-running `install.sh` is still safe, and it still never loses the person's own settings.

## No automated review here

This repo is not on the team's shared Claude review. That review's workflow lives in a private
repo, and GitHub doesn't let a public repo call it. There is no CI either: nothing runs the tests
on a PR, so run the checks above yourself before you ask for a review.

A merge deploys nothing by itself. People get a change when they pull and re-run `install.sh`.
