#!/usr/bin/env bash
# PostToolUse hook for the Artifact tool.
# Reminds to mirror a shareable artifact to the Perfecti Team Wiki (wiki.perfecti.io).
# Fires only on a publish (action absent or "publish"); silent on read/list/status/etc.
input="$(cat)"
action="$(printf '%s' "$input" | jq -r '.tool_input.action // "publish"' 2>/dev/null || echo publish)"
[ "$action" = "publish" ] || exit 0
jq -n '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: "An Artifact was just published. Per the Perfecti wiki rule in the user CLAUDE.md, also mirror shareable output to the Team Wiki (wiki.perfecti.io) using the publish-to-wiki skill: search the wiki first to avoid a duplicate, publish internal by default (just mention it), and confirm before public. Skip only for scratch, secret, or explicitly local-only pages."
  }
}'
