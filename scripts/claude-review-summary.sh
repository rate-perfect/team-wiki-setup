#!/usr/bin/env bash
# Prints the automated review summary for a PR's CURRENT head commit.
#
#   bash scripts/claude-review-summary.sh <pr-number>          # the newest one
#   bash scripts/claude-review-summary.sh --all <pr-number>    # every one for this head
#
# Exit 0: a summary for the current head exists; it is printed. Its first line is
#         <!-- REVIEW_STATUS: CLEAN --> or <!-- REVIEW_STATUS: HAS_ISSUES -->.
# Exit 3: none. The head has NOT been reviewed: the review is still running, failed,
#         or has not started. The reason is printed on stderr.
#
# One commit can be reviewed more than once (a re-run), and the verdicts can differ.
# When the newest summary says CLEAN but an earlier one for the SAME commit said
# HAS_ISSUES, the exit code is still 0 and the newest is still printed, and a line
# starting with "VERDICT CHANGED:" goes to stderr, naming both runs. Do not treat that
# as clean without reading the earlier summary (--all prints them oldest first).
#
# The rule: the newest comment by github-actions[bot] whose first non-empty line is a
# status tag and whose second is `<!-- REVIEW_HEAD: <the PR's head sha> -->`. That is
# what the shared review (.github/workflows/claude-code-review.yml, which calls
# rate-perfect/claude-review) posts. Its poster job writes the head line itself, so a
# summary left by a run on an older commit is never read as the current one, and
# neither is a comment by a person or by another bot.
#
# Not defended against: someone with push access to the repository. Every workflow
# posts as github-actions[bot], and a branch can carry a workflow that posts a comment
# of this shape. This script is for reading honest CI output, not for proving it.
#
# The same script, with the same parsing, is in the team's other repos that use the
# shared review. Change every copy together. (This public copy differs only in this
# comment: the other repos are private, so it does not name them.)
# The shared review writes its HAS_CRITICAL verdict as HAS_ISSUES on line 1 (and keeps
# the real word on a later line), so the two statuses below cover every review.
set -euo pipefail

ALL=""
if [ "${1:-}" = "--all" ]; then ALL="yes"; shift; fi
PR="${1:?usage: claude-review-summary.sh [--all] <pr-number>}"
REPO="${GH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
HEAD_SHA=$(gh api "repos/${REPO}/pulls/${PR}" --jq .head.sha)
COMMENTS=$(gh api --paginate "repos/${REPO}/issues/${PR}/comments?per_page=100")

# Every summary for this head, oldest first.
FOR_HEAD=$(printf '%s\n' "$COMMENTS" | jq -c -s --arg head "$HEAD_SHA" '
  def lines: (.body // "") | split("\n") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0));
  add // []
  | map(select(.user.login == "github-actions[bot]") | . as $c | ($c | lines) as $l
        | (($l[0] // "") | capture("^<!-- REVIEW_STATUS: (?<status>CLEAN|HAS_ISSUES) -->$")) as $s
        | select(($l[1] // "") == "<!-- REVIEW_HEAD: \($head) -->")
        | {status: $s.status, body: $c.body,
           run: ((($l[2] // "") | capture("^<!-- REVIEW_RUN: (?<run>[0-9]+/[0-9]+) -->$") | .run) // "unknown")})')
COUNT=$(printf '%s' "$FOR_HEAD" | jq 'length')

if [ "$COUNT" -gt 0 ]; then
  NEWEST_STATUS=$(printf '%s' "$FOR_HEAD" | jq -r 'last | .status')
  EARLIER_ISSUES=$(printf '%s' "$FOR_HEAD" | jq -r '.[0:-1] | map(select(.status == "HAS_ISSUES")) | last | .run // ""')
  if [ "$NEWEST_STATUS" = "CLEAN" ] && [ -n "$EARLIER_ISSUES" ]; then
    echo "VERDICT CHANGED: commit ${HEAD_SHA} was reviewed more than once. Run ${EARLIER_ISSUES} said HAS_ISSUES; the newest, run $(printf '%s' "$FOR_HEAD" | jq -r 'last | .run'), says CLEAN. Read the earlier findings before treating this commit as clean: bash scripts/claude-review-summary.sh --all ${PR}" >&2
  fi
  if [ -n "$ALL" ]; then
    printf '%s' "$FOR_HEAD" | jq -r 'length as $n | to_entries[] | "===== review \(.key + 1) of \($n) for this commit (run \(.value.run), \(.value.status)) =====\n\(.value.body)\n"'
  else
    printf '%s' "$FOR_HEAD" | jq -r 'last | .body'
  fi
  exit 0
fi

ALERT=$(printf '%s\n' "$COMMENTS" | jq -r -s --arg head "$HEAD_SHA" '
  add // [] | map(select(.user.login == "github-actions[bot]"
                         and ((.body // "") | test("^<!-- AUTOMATED-SKILL: claude-review-[a-z-]+ SHA=\($head) -->"))))
  | last | (.body // "") | split("\n") | .[1] // ""')
if [ -n "$ALERT" ]; then
  echo "NOT REVIEWED: the review of ${HEAD_SHA} failed. ${ALERT}" >&2
else
  echo "NOT REVIEWED: no review summary for ${HEAD_SHA} on PR #${PR}. The review / claude-review check is still running, has not started, or failed before it could post." >&2
fi
exit 3
