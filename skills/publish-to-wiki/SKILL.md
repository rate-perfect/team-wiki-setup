---
name: publish-to-wiki
description: Publish (mirror) a report, summary, doc, spec, analysis, dashboard, runbook, or self-contained HTML artifact to the perfecti.io Team Wiki (wiki.perfecti.io) so the team (or just the author, privately) can find it, and return the URL. Also how to SEARCH the wiki for an existing page across everyone's accounts before making a new one. Use as the default finishing step for shareable output — in addition to any local file/artifact. Not for scratch files, in-repo code changes, or secrets.
---

# Publish to (and search) the perfecti.io Team Wiki

Publishes a page to https://wiki.perfecti.io via its API. Content is stored as
Markdown or a full HTML document. The team's strong default is to mirror a copy
of any shareable artifact here.

Requires the env vars `WIKI_PUBLISH_URL` and `WIKI_PUBLISH_TOKEN` (set by the
team `install.sh`). If `WIKI_PUBLISH_TOKEN` is empty, stop and tell the user to
run the team-wiki `install.sh` first.

## Search first (avoid duplicates across accounts)
People use several Claude accounts and artifacts live per-account, but the wiki
aggregates them. Before creating a new shareable page — and whenever the user
refers to something they may have already made — search the wiki:

```bash
curl -sS "$WIKI_PUBLISH_URL/api/pages?q=SEARCH%20TERMS" \
  -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN"
```

Returns `{ ok, count, pages: [{slug,title,summary,tags,author,created,updated,url}] }`
for matching **internal (team)** pages (all terms must match; omit `q` to list
everything). If you find the page, give the user `https://wiki.perfecti.io` +
its `url` instead of remaking it. (Private/owner-only pages never appear here.)

## Read a page (get its content)
To fetch one page's stored source, add `slug=` to the same endpoint:

```bash
curl -sS "$WIKI_PUBLISH_URL/api/pages?slug=THE-SLUG" \
  -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN"
```

Returns `{ ok:true, page:{slug,title,summary,tags,author,created,updated,url,
format,body,visibility,owner?} }` — `body` is the raw stored source and
`format` is `"markdown"` or `"html"`. A missing slug returns
`{ ok:false, error:"not found" }` (HTTP 404). Use it to read back a page you
found via search — e.g. to update or extend it, or to reuse its content.

Only `internal` and `public` pages are readable this way. The token is a shared
service token with no per-user identity, so it can never be a page's owner:
`private` (owner-only) pages return 404 here, exactly as a non-owner sees on the
site. (Read a private page's content by signing in as its owner on the site.)

## When to publish
As the finishing step whenever you produce something meant to be read or shared
(report, summary, write-up, spec, analysis, dashboard, runbook, HTML
tool/artifact). Keep creating the normal local file/artifact too — this is an
*additional* shared copy. Skip it for scratch output, in-repo code changes,
secrets, or anything the user marks private/local-only.

## Inputs to decide
- **title** — a clear human title.
- **visibility** — one of:
  - `internal` (default): team-only, listed + searchable. Publish without a
    prompt (mention you're doing it).
  - `private`: **owner-only** — only the `owner` email can see it; 404 for
    everyone else. Use when the user wants it on their own wiki space, not
    shared with the team. **Requires `owner`.** They flip it to team-shared any
    time with the page's **Make visible to team** button (and back with **Make
    private**), so private-first is a safe default when unsure.
  - `public`: unlisted, link-only, for sharing *outside* the team. **Confirm
    with the user before publishing anything public.**
- **owner** — the owner's email. **Required when `visibility` is `private`**
  (that person becomes the sole viewer). Omit otherwise.
- **format** — `markdown` for written content, or `html` when the artifact is a
  complete `<!doctype html>` document (renders as-is in a sandboxed frame).
- **summary** — one line shown in the dashboard list.
- **tags** — optional list.
- **author** *(required)* — who the page is from: the name (or email) of the
  person you're doing this for. Use their name if you know it, else their email;
  if you truly can't tell, ask — the API rejects a publish with no author.
- **body** — the Markdown text, or the full HTML document.

## How to publish
Write the body to a temp file to avoid shell-escaping issues, then POST it
(`$OWNER` empty for non-private pages):

```bash
# body already written to "$BODY_FILE"
jq -n \
  --arg title "$TITLE" \
  --arg visibility "$VISIBILITY" \
  --arg format "$FORMAT" \
  --arg summary "$SUMMARY" \
  --arg author "$AUTHOR" \
  --arg owner "$OWNER" \
  --rawfile body "$BODY_FILE" \
  --argjson tags "$TAGS_JSON" \
  '{title:$title, visibility:$visibility, format:$format, summary:$summary, author:$author, tags:$tags, body:$body}
   + (if $owner == "" then {} else {owner:$owner} end)' \
| curl -sS -X POST "$WIKI_PUBLISH_URL/api/publish" \
    -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN" \
    -H "Content-Type: application/json" \
    --data @-
```

`TAGS_JSON` is a JSON array, e.g. `["planning","q3"]` (use `[]` if none).

## After publishing
The API returns `{ "ok": true, "url": "/wiki/<slug>" | "/p/<id>", ... }`.
Give the user the full URL: `https://wiki.perfecti.io` + that path. A `private`
page's URL works only for its owner while signed in. If it returns `ok:false`,
report the error (401 = token wrong/unset; 400 = missing field, e.g. `owner`
required for a private page; 501/GitHub error = the server publish credential
isn't configured — tell the user to check the wiki's Vercel env).
