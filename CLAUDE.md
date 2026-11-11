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
- `shellcheck` is clean on the shell scripts you changed.
- `README.md` still says what the code does.
- Re-running `install.sh` is still safe, and it still never loses the person's own settings.

## CI: Automated Review

Every PR gets an automated review from the shared Claude review, rate-perfect/claude-review.
`.github/workflows/claude-code-review.yml` is a thin caller of its reusable workflow, pinned to a
release commit (v1.1.1). The review arrives as one summary comment by `github-actions[bot]`, and
each finding names its `path:line`. **The comment is public.** The reviewer is told never to quote
a secret or an internal detail it sees in a diff, only to name the file and line.

It is advisory: no branch rule requires it, and `main` has no required checks. There is no other
CI here: nothing runs the tests. Two kinds of PR are skipped, because the review would fail on
them: Dependabot's (their runs have no Claude token) and PRs from a fork (GitHub gives them no
secrets).

- **The checks on a PR:** `review / review-session` runs the model (read-only).
  `review / claude-review` posts the comment. `review / shadow-review` shows as skipped (it runs
  only on promotion PRs, and this repo has none).
- **How it reviews:** the diff is split into batches, so a PR of any size is reviewed. The reviewer
  has `Read`, `Glob` and `Grep` only. The comment has a coverage section that names any file no
  model reviewed, and a review with unreviewed files is never `CLEAN`.
- **Read it with `bash scripts/claude-review-summary.sh <pr>`.**
  - It prints the summary for the PR's head commit, or exits 3 with "NOT REVIEWED".
  - A summary starts with `<!-- REVIEW_STATUS: CLEAN -->` or `<!-- REVIEW_STATUS: HAS_ISSUES -->`,
    then `<!-- REVIEW_HEAD: <sha> -->` and `<!-- REVIEW_RUN: <run id>/<attempt> -->`. A
    `HAS_CRITICAL` verdict shows as `HAS_ISSUES` on line 1, with
    `<!-- REVIEW_VERDICT: HAS_CRITICAL -->` on line 4.
  - A green `review / claude-review` check means a summary was posted, whether it says `CLEAN` or
    `HAS_ISSUES`. Read the summary, not the colour.
  - Red means the head wasn't reviewed. Re-run the whole run (`gh run rerun <run-id>`, not
    `--failed`) or push a commit.
  - A re-run can change the verdict for the same commit. The script then prints a line starting
    with `VERDICT CHANGED:`. Read the earlier summary too (`--all`).
- **CI reviews but doesn't fix.**
  - Whoever opened the PR fixes every Critical and Important finding, plus the Suggestions that are
    cheap and safe, and pushes again.
  - To answer a finding you won't fix, write the reason in the PR description or in a code comment.
    This holds for any finding, a Critical included; the reviewer judges each answer. It doesn't
    see PR comments.
  - **Edit the description, then re-run the whole review run** (`gh run rerun <run-id>`; the run id
    is on the summary's `REVIEW_RUN` line). The review reads the description when it runs, so no
    new commit is needed. Re-running only the `review / claude-review` job keeps the old text.
  - A later round always lists an open Critical again, but an open Important only when its fix is
    small and clear. So a later `CLEAN` doesn't prove every earlier Important was fixed.
  - Stop after about 3 rounds without `CLEAN`, and ask a human.
- **The reviewer's rules come from the base branch:** `.github/review-instructions.md`,
  `.github/claude-review/environment.md` and `.github/claude-review/config.json`, and also this
  file, `README.md` and `.claude/`. An edit to them takes effect once it's merged.
- **The review itself lives in rate-perfect/claude-review.** Change it there, then move this repo's
  pin to the new release. Anything specific to this repo goes in the three files above.
- **`scripts/claude-review-summary.sh` is the same script** as in the team's other repos that use
  the shared review. This copy differs only in one comment, which names no private repo. Change
  every copy together.
- A merge deploys nothing by itself. People get a change when they pull and re-run `install.sh`.
