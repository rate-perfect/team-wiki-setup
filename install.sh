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
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
touch "$CLAUDE_MD"
if grep -q "BEGIN perfecti-team-wiki" "$CLAUDE_MD"; then
  sed -i.bak '/<!-- BEGIN perfecti-team-wiki -->/,/<!-- END perfecti-team-wiki -->/d' "$CLAUDE_MD"
  rm -f "$CLAUDE_MD.bak"
fi
# Trim trailing blank lines first so re-running doesn't add one each time.
REST="$(cat "$CLAUDE_MD")"
{ [ -n "$REST" ] && printf '%s\n\n' "$REST"; cat "$SCRIPT_DIR/claude-instructions.md"; } > "$CLAUDE_MD.tmp"
mv "$CLAUDE_MD.tmp" "$CLAUDE_MD"
echo "✓ updated instruction block in ~/.claude/CLAUDE.md"

# --- 3. set env vars in user-global settings.json (merge, don't clobber) ---
WIKI_PUBLISH_URL="$WIKI_PUBLISH_URL" WIKI_PUBLISH_TOKEN="$WIKI_PUBLISH_TOKEN" node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const p=path.join(os.homedir(),".claude","settings.json");
let s={};
try{ s=JSON.parse(fs.readFileSync(p,"utf8")); }catch(e){}
s.env=s.env||{};
s.env.WIKI_PUBLISH_URL=process.env.WIKI_PUBLISH_URL;
s.env.WIKI_PUBLISH_TOKEN=process.env.WIKI_PUBLISH_TOKEN;
fs.mkdirSync(path.dirname(p),{recursive:true});
fs.writeFileSync(p, JSON.stringify(s,null,2)+"\n");
'
echo "✓ set WIKI_PUBLISH_URL + WIKI_PUBLISH_TOKEN in ~/.claude/settings.json"

# --- 4. install the "mirror to the wiki" reminder hook (idempotent) ---
# A PostToolUse hook on the Artifact tool: after you publish an artifact it
# injects a reminder to also mirror shareable output to the wiki. It's a
# reminder only (never an auto-publish), so judgment on scratch/secret/public
# stays in the loop — but the prompt can't be silently skipped, because the
# harness runs hooks deterministically. Fires only on a publish; needs `jq`.
mkdir -p "$CLAUDE_DIR/hooks"
cp "$SCRIPT_DIR/hooks/remind-wiki-mirror.sh" "$CLAUDE_DIR/hooks/remind-wiki-mirror.sh"
chmod +x "$CLAUDE_DIR/hooks/remind-wiki-mirror.sh"
node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const p=path.join(os.homedir(),".claude","settings.json");
let s={}; try{ s=JSON.parse(fs.readFileSync(p,"utf8")); }catch(e){}
s.hooks=s.hooks||{};
s.hooks.PostToolUse=s.hooks.PostToolUse||[];
const CMD="~/.claude/hooks/remind-wiki-mirror.sh";
let grp=s.hooks.PostToolUse.find(g=>g&&g.matcher==="Artifact");
if(!grp){ grp={matcher:"Artifact",hooks:[]}; s.hooks.PostToolUse.push(grp); }
grp.hooks=grp.hooks||[];
if(!grp.hooks.some(h=>h&&h.command===CMD)){
  grp.hooks.push({type:"command",command:CMD,timeout:10,statusMessage:"wiki mirror reminder"});
}
fs.mkdirSync(path.dirname(p),{recursive:true});
fs.writeFileSync(p, JSON.stringify(s,null,2)+"\n");
'
echo "✓ installed wiki-mirror reminder hook (PostToolUse · Artifact)"

# --- 5. install the "repo rules first" hook (idempotent) ---
# A PreToolUse hook on Edit|Write|NotebookEdit|Bash: before Claude changes a git
# checkout, it must have read that checkout's own CLAUDE.md (and related rule
# files), even when the session started in another folder. The hook holds no
# rules of its own; each repo stays the source of truth. A SessionStart hook
# (on compact) makes it check again after the conversation is compacted. It
# fails open and never blocks the same repo more than 4 times. Needs python3.
cp "$SCRIPT_DIR/hooks/repo-rules-first.py" "$CLAUDE_DIR/hooks/repo-rules-first.py"
chmod +x "$CLAUDE_DIR/hooks/repo-rules-first.py"
node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const p=path.join(os.homedir(),".claude","settings.json");
let s={}; try{ s=JSON.parse(fs.readFileSync(p,"utf8")); }catch(e){}
s.hooks=s.hooks||{};
function add(event, matcher, hook){
  s.hooks[event]=s.hooks[event]||[];
  let grp=s.hooks[event].find(g=>g&&g.matcher===matcher);
  if(!grp){ grp={matcher,hooks:[]}; s.hooks[event].push(grp); }
  grp.hooks=grp.hooks||[];
  if(!grp.hooks.some(h=>h&&h.command===hook.command)) grp.hooks.push(hook);
}
add("PreToolUse","Edit|Write|NotebookEdit|Bash",
    {type:"command",command:"python3 ~/.claude/hooks/repo-rules-first.py",timeout:15,statusMessage:"repo rules check"});
add("SessionStart","compact",
    {type:"command",command:"python3 ~/.claude/hooks/repo-rules-first.py --reset",timeout:10});
fs.mkdirSync(path.dirname(p),{recursive:true});
fs.writeFileSync(p, JSON.stringify(s,null,2)+"\n");
'
echo "✓ installed repo-rules-first hook (PreToolUse · Edit|Write|NotebookEdit|Bash)"

echo
echo "Done. Restart any open Claude Code sessions to pick up the changes"
echo "(or run /hooks once to reload config in a running session)."
echo "Test it: in a Claude Code session, ask \"publish a quick test note to the wiki\"."
