---
name: publish-to-wiki
description: Publish (mirror) a report, summary, doc, spec, analysis, dashboard, runbook, or self-contained HTML artifact to the perfecti.io Team Wiki (wiki.perfecti.io) so the team can find and share it, and return the URL. Use this whenever you create or are asked to create shareable read-or-share output — as the default finishing step, in addition to any local file/artifact. Not for scratch files, in-repo code changes, or secrets.
---

# Publish to the perfecti.io Team Wiki

Publishes a page to https://wiki.perfecti.io via its API. Content is stored as
Markdown or a full HTML document. The team's strong default is to mirror a copy
of any shareable artifact here.

## When to use
Use it as the finishing step whenever you produce something meant to be read or
shared (report, summary, write-up, spec, analysis, dashboard, runbook, HTML
tool/artifact). Keep creating the normal local file/artifact too — this is an
*additional* shared copy. Skip it for scratch output, in-repo code changes,
secrets, or anything the user marks private/local-only.

## Inputs to decide
- **title** — a clear human title.
- **visibility** — `internal` (default: team-only, searchable) or `public`
  (unlisted, link-only, for sharing *outside* the team). **Confirm with the user
  before publishing anything `public`.** Internal pages you may publish without
  a prompt as the default finishing step (mention that you're doing it).
- **format** — `markdown` for written content, or `html` when the artifact is a
  complete `<!doctype html>` document (it renders as-is in a sandboxed frame).
- **summary** — one line shown in the dashboard list.
- **tags** — optional list.
- **author** — who the page is from: the name (or email) of the person you're
  doing this for, so the wiki shows and can filter by who created it. Use their
  name if you know it, else their email.
- **body** — the Markdown text, or the full HTML document.

## How to publish
Requires the env vars `WIKI_PUBLISH_URL` and `WIKI_PUBLISH_TOKEN` (set by the
team `install.sh`). If `WIKI_PUBLISH_TOKEN` is empty, stop and tell the user to
run the team-wiki `install.sh` first.

Write the body to a temp file to avoid shell-escaping issues, then POST it:

```bash
# body already written to "$BODY_FILE"
jq -n \
  --arg title "$TITLE" \
  --arg visibility "$VISIBILITY" \
  --arg format "$FORMAT" \
  --arg summary "$SUMMARY" \
  --arg author "$AUTHOR" \
  --rawfile body "$BODY_FILE" \
  --argjson tags "$TAGS_JSON" \
  '{title:$title, visibility:$visibility, format:$format, summary:$summary, author:$author, tags:$tags, body:$body}' \
| curl -sS -X POST "$WIKI_PUBLISH_URL/api/publish" \
    -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN" \
    -H "Content-Type: application/json" \
    --data @-
```

`TAGS_JSON` is a JSON array, e.g. `["planning","q3"]` (use `[]` if none).

## After publishing
The API returns `{ "ok": true, "url": "/wiki/<slug>" | "/p/<id>", ... }`.
Give the user the full URL: `https://wiki.perfecti.io` + that path. If it
returns `ok:false`, report the error (a 401 means the token is wrong/unset; a
501/GitHub error means the server publish credential isn't configured — tell the
user to check the wiki's Vercel env).
