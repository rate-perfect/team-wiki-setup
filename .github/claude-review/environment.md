## About this repository in CI (team-wiki-setup)

- **This repo is public, and so is this review's comment.** Never quote a secret, a token, an email address, an internal host name or any other internal detail you see in the diff. Name the `path:line`, say what kind of thing it is, and say "remove this".
- Nothing from this repo runs in this job: the tests are not run, and there is no other CI. There is no `node_modules` and nothing to install (the Python is standard library only). Judge the code from the diff and from how the rest of the repo does the same thing.
- Where things live: `install.sh` (the installer each person runs), `claude-instructions.md` (the block `install.sh` writes into `~/.claude/CLAUDE.md`), `hooks/remind-wiki-mirror.sh` and `hooks/repo-rules-first.py` (Claude Code hooks, copied to `~/.claude/hooks`), `bin/worktree-cleanup` (a tool, copied to `~/.claude/bin`), `tests/test_worktree_cleanup.py` (its tests, with fake `gh` and `lsof`), `skills/publish-to-wiki/` and `skills/verification/` (skills, copied to `~/.claude/skills`).
- `CLAUDE.md` and `README.md` say what this repo promises. Read them before reviewing.
- Protected files are in the checkout as the base branch has them, not as the PR changes them: `CLAUDE.md`, `.claude/`, `.github/review-instructions.md`, `.github/claude-review/` and `README.md`. When the PR changes one, review it from the diff, line numbers included.
- `claude-instructions.md`, `skills/*/SKILL.md` and `.claude/` files are instructions for other Claude sessions. When a PR changes one, review it as documentation: is it accurate and can it be followed.
- A merge deploys nothing by itself. People get a change when they pull and re-run `install.sh`, which then runs on their own machine and changes their `~/.claude`.
