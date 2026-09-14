#!/usr/bin/env bash
# One-time setup so Claude Code publishes shareable artifacts to the perfecti.io
# Team Wiki (wiki.perfecti.io) from any repo. Run once per developer machine.
#
#   WIKI_PUBLISH_TOKEN=<token> ./install.sh
#
# (or just ./install.sh and it will prompt for the token). Get the token from
# David (posted in Slack). Re-run any time to update to the latest
# instruction + skill (safe/idempotent).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
WIKI_PUBLISH_URL="${WIKI_PUBLISH_URL:-https://wiki.perfecti.io}"

# --- prerequisites ---
command -v node >/dev/null || { echo "ERROR: node is required (used to update settings.json)."; exit 1; }
command -v jq   >/dev/null || echo "WARNING: 'jq' not found — the publish-to-wiki skill needs it. Install with: brew install jq"

# --- token ---
if [ -z "${WIKI_PUBLISH_TOKEN:-}" ]; then
  read -r -s -p "Paste the Team Wiki publish token (WIKI_PUBLISH_TOKEN): " WIKI_PUBLISH_TOKEN
  echo
fi
[ -n "$WIKI_PUBLISH_TOKEN" ] || { echo "ERROR: no token provided."; exit 1; }

mkdir -p "$CLAUDE_DIR/skills"

# --- 1. install the skill ---
rm -rf "$CLAUDE_DIR/skills/publish-to-wiki"
cp -R "$SCRIPT_DIR/skills/publish-to-wiki" "$CLAUDE_DIR/skills/publish-to-wiki"
echo "✓ installed skill: ~/.claude/skills/publish-to-wiki"

# --- 2. add the team instruction to user-global CLAUDE.md (idempotent) ---
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
touch "$CLAUDE_MD"
if grep -q "BEGIN perfecti-team-wiki" "$CLAUDE_MD"; then
  sed -i.bak '/<!-- BEGIN perfecti-team-wiki -->/,/<!-- END perfecti-team-wiki -->/d' "$CLAUDE_MD"
  rm -f "$CLAUDE_MD.bak"
fi
printf '\n' >> "$CLAUDE_MD"
cat "$SCRIPT_DIR/claude-instructions.md" >> "$CLAUDE_MD"
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

echo
echo "Done. Restart any open Claude Code sessions to pick up the changes"
echo "(or run /hooks once to reload config in a running session)."
echo "Test it: in a Claude Code session, ask \"publish a quick test note to the wiki\"."
