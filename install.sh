#!/usr/bin/env bash
# One-time setup so Claude Code publishes shareable artifacts to the perfecti.io
# Team Wiki (wiki.perfecti.io) from any repo. Run once per developer machine.
#
#   WIKI_PUBLISH_TOKEN=<token> ./install.sh
#
# (or just ./install.sh and it will prompt for the token). Get the token from
# David (posted in Slack). Re-run any time to update to the latest
# instruction + skills (safe/idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
WIKI_PUBLISH_URL="${WIKI_PUBLISH_URL:-https://wiki.perfecti.io}"

# --- prerequisites ---
command -v node >/dev/null || { echo "ERROR: node is required (used to update settings.json)."; exit 1; }
command -v jq   >/dev/null || echo "WARNING: 'jq' not found — the publish-to-wiki skill needs it. Install with: brew install jq"
command -v python3 >/dev/null || echo "WARNING: 'python3' not found — the repo-rules hook needs it (it lets every call through without it). Install with: brew install python"

# --- token ---
if [ -z "${WIKI_PUBLISH_TOKEN:-}" ]; then
  read -r -s -p "Paste the Team Wiki publish token (WIKI_PUBLISH_TOKEN): " WIKI_PUBLISH_TOKEN
  echo
fi
[ -n "$WIKI_PUBLISH_TOKEN" ] || { echo "ERROR: no token provided."; exit 1; }

mkdir -p "$CLAUDE_DIR/skills"

# --- 1. install the team skills (every folder under skills/) ---
# User-level (~/.claude/skills), so each one is available in every repo.
for skill_dir in "$SCRIPT_DIR"/skills/*/; do
  skill="$(basename "$skill_dir")"
  rm -rf "$CLAUDE_DIR/skills/$skill"
  cp -R "$skill_dir" "$CLAUDE_DIR/skills/$skill"
  echo "✓ installed skill: ~/.claude/skills/$skill"
done

# --- 2. add the team instruction to user-global CLAUDE.md (idempotent) ---
# The team block sits between BEGIN/END markers and is replaced on every run;
# everything outside the markers is yours and is kept. A backup of the previous
# file is kept at ~/.claude/CLAUDE.md.bak. Written through a symlink if
# CLAUDE.md is one (e.g. kept in a dotfiles repo).
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
BEGIN_MARK='<!-- BEGIN perfecti-team-wiki -->'
END_MARK='<!-- END perfecti-team-wiki -->'
touch "$CLAUDE_MD"
HAS_BEGIN=0; HAS_END=0
if grep -qF "$BEGIN_MARK" "$CLAUDE_MD"; then HAS_BEGIN=1; fi
if grep -qF "$END_MARK" "$CLAUDE_MD"; then HAS_END=1; fi
if [ "$HAS_BEGIN" != "$HAS_END" ]; then
  echo "ERROR: ~/.claude/CLAUDE.md has only one of the $BEGIN_MARK / $END_MARK lines."
  echo "       Put the missing line back (or delete the whole team block), then re-run."
  exit 1
fi
cp "$CLAUDE_MD" "$CLAUDE_MD.bak"
TMP_MD="$(mktemp)"
awk -v b="$BEGIN_MARK" -v e="$END_MARK" 'index($0,b){skip=1} !skip{print} index($0,e){skip=0}' "$CLAUDE_MD" > "$TMP_MD"
REST="$(cat "$TMP_MD")"   # $(...) also drops trailing blank lines, so re-runs don't add one each time
{
  if [ -n "$REST" ]; then printf '%s\n\n' "$REST"; fi
  cat "$SCRIPT_DIR/claude-instructions.md"
} > "$TMP_MD"
cat "$TMP_MD" > "$CLAUDE_MD"
rm -f "$TMP_MD"
echo "✓ updated instruction block in ~/.claude/CLAUDE.md (previous copy: ~/.claude/CLAUDE.md.bak)"

# --- 3. copy the team hooks ---
# remind-wiki-mirror.sh: a PostToolUse hook on the Artifact tool. After you
# publish an artifact it reminds Claude to also mirror shareable output to the
# wiki. A reminder only (never an auto-publish), so judgment on
# scratch/secret/public stays in the loop. Fires only on a publish; needs jq.
#
# repo-rules-first.py: a PreToolUse hook on Edit|Write|NotebookEdit|Bash.
# Before Claude changes a git checkout, it must have read that checkout's own
# CLAUDE.md (and related rule files), even when the session started in another
# folder. The hook holds no rules of its own; each repo stays the source of
# truth. A SessionStart hook (on compact) makes it check again after the
# conversation is compacted. It fails open and never blocks the same repo more
# than 4 times. Needs python3.
mkdir -p "$CLAUDE_DIR/hooks"
for hook in remind-wiki-mirror.sh repo-rules-first.py; do
  cp "$SCRIPT_DIR/hooks/$hook" "$CLAUDE_DIR/hooks/$hook"
  chmod +x "$CLAUDE_DIR/hooks/$hook"
done

# --- 4. update user-global settings.json: env vars + hooks (merge, don't clobber) ---
# Stops without writing if settings.json exists but isn't valid JSON, so your
# other settings are never lost. A backup is kept at ~/.claude/settings.json.bak.
WIKI_PUBLISH_URL="$WIKI_PUBLISH_URL" WIKI_PUBLISH_TOKEN="$WIKI_PUBLISH_TOKEN" node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const p=path.join(os.homedir(),".claude","settings.json");
let s={}, raw=null;
try{ raw=fs.readFileSync(p,"utf8"); }catch(e){ if(e.code!=="ENOENT") throw e; }
if(raw!==null && raw.trim()!==""){
  try{ s=JSON.parse(raw); }catch(e){
    console.error("ERROR: ~/.claude/settings.json is not valid JSON ("+e.message+").");
    console.error("       settings.json was not changed. Fix it, then re-run.");
    process.exit(1);
  }
  if(!s||typeof s!=="object"||Array.isArray(s)){
    console.error("ERROR: ~/.claude/settings.json is not a JSON object. It was not changed. Fix it, then re-run.");
    process.exit(1);
  }
  fs.writeFileSync(p+".bak", raw);
}
s.env=s.env||{};
s.env.WIKI_PUBLISH_URL=process.env.WIKI_PUBLISH_URL;
s.env.WIKI_PUBLISH_TOKEN=process.env.WIKI_PUBLISH_TOKEN;
s.hooks=s.hooks||{};
function add(event, matcher, hook){
  s.hooks[event]=s.hooks[event]||[];
  let grp=s.hooks[event].find(g=>g&&g.matcher===matcher);
  if(!grp){ grp={matcher,hooks:[]}; s.hooks[event].push(grp); }
  grp.hooks=grp.hooks||[];
  if(!grp.hooks.some(h=>h&&h.command===hook.command)) grp.hooks.push(hook);
}
add("PostToolUse","Artifact",
    {type:"command",command:"~/.claude/hooks/remind-wiki-mirror.sh",timeout:10,statusMessage:"wiki mirror reminder"});
add("PreToolUse","Edit|Write|NotebookEdit|Bash",
    {type:"command",command:"python3 ~/.claude/hooks/repo-rules-first.py",timeout:15,statusMessage:"repo rules check"});
add("SessionStart","compact",
    {type:"command",command:"python3 ~/.claude/hooks/repo-rules-first.py --reset",timeout:10});
fs.mkdirSync(path.dirname(p),{recursive:true});
fs.writeFileSync(p, JSON.stringify(s,null,2)+"\n");
'
echo "✓ set WIKI_PUBLISH_URL + WIKI_PUBLISH_TOKEN in ~/.claude/settings.json (previous copy: ~/.claude/settings.json.bak)"
echo "✓ installed wiki-mirror reminder hook (PostToolUse · Artifact)"
echo "✓ installed repo-rules-first hook (PreToolUse · Edit|Write|NotebookEdit|Bash)"

# --- 5. copy the team tools (~/.claude/bin) ---
# worktree-cleanup: lists git worktrees whose GitHub PR is merged and that hold
# nothing else (no changes, no newer commits, no open PR, no running process),
# and removes them only when you pass --apply. Not a hook: you run it yourself.
# ~/.claude/bin is not on your PATH; call it by its full path. Needs python3,
# gh and lsof.
mkdir -p "$CLAUDE_DIR/bin"
for tool in worktree-cleanup; do
  cp "$SCRIPT_DIR/bin/$tool" "$CLAUDE_DIR/bin/$tool"
  chmod +x "$CLAUDE_DIR/bin/$tool"
  echo "✓ installed tool: ~/.claude/bin/$tool"
done

echo
echo "Done. Restart any open Claude Code sessions to pick up the changes"
echo "(or run /hooks once to reload config in a running session)."
echo "Test it: in a Claude Code session, ask \"publish a quick test note to the wiki\"."
