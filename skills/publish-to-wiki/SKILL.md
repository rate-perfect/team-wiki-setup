---
name: publish-to-wiki
description: Publish (mirror) a report, summary, doc, spec, analysis, dashboard, runbook, or self-contained HTML artifact to the perfecti.io Team Wiki (wiki.perfecti.io) so the team (or just the author, privately) can find it, and return the URL. Also how to SEARCH the wiki for an existing page across everyone's accounts before making a new one, and how to file a page under a topic (Topics, the wiki's opt-in page hierarchy) and keep that topic's parent page up to date. Use as the default finishing step for shareable output — in addition to any local file/artifact. Not for scratch files, in-repo code changes, or secrets.
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

Returns `{ ok, count, pages: [{slug,title,summary,tags,author,created,updated,url,parent?,placeholder?}] }`
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
format,body,visibility,owner?,parent?,placeholder?} }` — `body` is the raw stored source and
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
    private**), so private-first is a safe default when unsure. If the user
    asks you to move a private page to the team **and you have its full current
    content** (for example, you published it in this session), you can do it
    for them: republish it with `visibility: internal`, the same `slug`, and
    their email as `owner`. The move publishes the body you send and deletes
    the private copy (it keeps the created date, parent and owner; the answer
    says `moved: true`). You can't read private pages, so otherwise point them
    to **Make visible to team**.
  - `public`: unlisted, link-only, for sharing *outside* the team. **Confirm
    with the user before publishing anything public.**
- **share** *(optional, internal/private only)* — `true` also creates (or
  refreshes) an unlisted `/p/<id>` link, returned as `sharedUrl`. Use it when the
  user wants to share a **private** page with specific people without making it
  visible to the team; the page itself stays private. The link always shows
  the page's latest version, so later edits need no re-share. Confirm before
  sharing, same as public. The owner can revoke it with the page's **Revoke**.
- **owner** — the owner's email. **Required when `visibility` is `private`**
  (that person becomes the sole viewer). For an `internal` publish, send it when
  you are moving the user's own private page to the team (see above), and when
  you republish a team page you read back that has an `owner` (send that same
  `owner`, or its owner loses the page's **Make private** button). Omit it
  otherwise.
- **format** — `markdown` for written content, or `html` when the artifact is a
  complete `<!doctype html>` document (renders as-is in a sandboxed frame).
- **Make it easy to consume.** Lead with the conclusion or plan, and put supporting detail below it. Markdown pages support tables and **collapsible sections** (`<details><summary>Title</summary>` … `</details>`, with a blank line after `</summary>` and before `</details>`), which suit long technical detail.
- **summary** — one line shown in the dashboard list.
- **tags** — optional list.
- **author** *(required)* — who the page is from: the name (or email) of the
  person you're doing this for. Use their name if you know it, else their email;
  if you truly can't tell, ask — the API rejects a publish with no author.
- **body** — the Markdown text, or the full HTML document.
- **slug** *(optional)* — leave it out for a new page (the wiki derives it from
  the title). **Pass the existing slug whenever you republish a page you read
  back**, because a page's slug does not always match its current title, and a
  mismatch would create a second page instead of updating the first.
- **parent** *(optional, internal/private only)* — the slug of the page to file
  this one under in Topics. See the next section for how to choose it.

## Topics (filing a page under a parent page)
The wiki has one flat list of every page, plus **Topics**: an opt-in tree where a
page names another page as its `parent`. People browse Topics to find everything
about a subject in one place, so a page filed in the right spot is easier to find
than one that only exists in the flat list. A page with no `parent` is simply not
in the tree; nothing breaks.

**See the tree.** Every page in the index carries its `parent` slug if it has
one, so the tree can be read straight from the list:

```bash
curl -sS "$WIKI_PUBLISH_URL/api/pages?limit=500" \
  -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN" \
| jq -r '.pages as $p
    | ($p | map(select(.parent) | .parent) | unique) as $parents
    | $p[] | select(.parent or (.slug | IN($parents[])))
    | "\(.slug)\t<- \(.parent // "(top level)")\t\(.title)\(if .placeholder then "  [section]" else "" end)\t(\(.author))"'
```

Each line is `slug <- parent-slug  title  (author)`. `[section]` marks a
placeholder: a page someone created only to group others, with no content of its
own yet.

**Topics is opt-in for people as well as pages.** Some of the team organise
their work in Topics and want new pages to land in the right place without
having to ask each time. Others never use it, and for them it should stay out of
the way entirely. So first work out which kind of user this is. Treat them as a
**Topics user** if any of these is true:

- they have mentioned Topics, a topic, a section or filing in this session, or
  asked for a page to go "under" or "with" something;
- their instructions or your memory of them say they use Topics;
- pages they authored are already filed (their name appears in the tree above).

Otherwise treat them as someone who doesn't use it. Running the tree command to
check the last point is fine: it is one quick read and the user never sees it.

**For a Topics user, lean towards filing.**

1. **They name a topic** ("put it under Onboarding", "file this with the
   release-notes pages"). Find the page they mean in the tree or by search and
   pass its slug as `parent`. If nothing matches, say so and ask whether to
   create a new section or publish unfiled; don't guess at a near miss.
2. **They don't say, and there is a logical place.** Look at the tree and at
   what they have been working on in this session: the product, project,
   experiment or series the page belongs to. If an existing topic is where a
   teammate would reasonably look for this page, file it there without asking,
   and say where it went in the same sentence as the URL. Pick the most specific
   topic that fits (the experiment's own section rather than the product's
   top-level page). Sibling pages are the best evidence: the next page in a
   series belongs with the rest of the series. When two topics are both
   plausible, pick the better one and name the other, rather than asking.
3. **There is no logical place.** Publish it unfiled and say so in a few words,
   so they can file it or ask for a new section. Don't create a section on your
   own initiative, and don't stretch a loosely related topic to fit.

Filing is easy to undo (the page has a **Move…** button), which is why a wrong
guess here costs little and a question each time would cost more.

**For someone who doesn't use Topics, stay out of the way.** Publish exactly as
before: no `parent`, no mention of Topics, no question about where the page
belongs, no offer to file it. Two things still
happen on their behalf, quietly, because they keep other people's work intact:

- a page that is already filed keeps its place when they republish it (leave
  `parent` out, as always; see **Limits** for the one case that gets a 400);
- if they explicitly name a topic, they have just opted in: treat them as a
  Topics user from then on.

**Creating a section.** Only when the user asks for a new topic or section.
Publish a page with the section's title, a one-sentence body saying what belongs
under it, `"placeholder": true`, and its own `parent` if it goes under an
existing topic. `placeholder` keeps the empty section out of the flat list.
Then publish the real page with the section's slug as its `parent`.

**Republishing a filed page.** Leave `parent` out and the page stays where it is.
Only send `parent` again to move it, and only move a page when asked. To take a
page out of Topics, send `"parent": null`.

**Limits.** Public pages have no parent. A team page can only be filed under a
team page. A private page can be filed under a team page or under the same
owner's private page. The shared token cannot see private pages, so it can't
find or read a private parent. A page can't be filed under itself or under one
of its own sub-pages. A parent that breaks these rules gets a 400. If you left
`parent` out and the 400 says the parent it kept is a private page, the page
can't stay there. For a Topics user, offer the nearest team topic (or file it
there, as above). Otherwise send `"parent": null` to take it out of Topics, and
tell the user in one line.

## Keeping the parent page current
A topic's parent page is often a summary of what sits under it: current status,
headline numbers, a list of its pages, what is still open. When you publish a
new page under a topic, or update a page that is already under one, that summary
can go stale without anyone noticing. So after the publish succeeds, if the
response includes a `parent`, read that page (`?slug=<parent>`) and check it
against what you just published.

- **A placeholder parent** (`placeholder: true`) states no facts. Leave it alone.
  Don't fill it in unless the user asks for a summary; doing so turns it into a
  normal page.
- **Look for statements the new page changes:** a status ("not yet tested"), a
  number or date the new page supersedes, an open item the new page closes, a
  "latest" or "next step" that is no longer the latest, and any list of the
  topic's pages that should now include this one.
- **If nothing is affected, do nothing.** Most publishes won't touch the parent.
  Don't republish it just to reword it.

How to correct it depends on what kind of document the parent is:

- **A living summary or status page** (it describes how things stand now):
  replace the outdated statement with the current one, so the page reads as true
  today. Update its page list and its "as of" date if it has one.
- **A dated record** (a report, a decision, a write-up of a specific run or
  meeting): the old statement was true when written and should stay. Add a short
  dated note beside it pointing to the newer page, rather than rewriting what
  was found at the time.
- **If you can't tell, or the change is more than a few statements,** don't
  rewrite. Tell a Topics user what looks outdated and let them decide. For
  someone who doesn't use Topics, leave the parent as it is rather than handing
  them a question about a page they never asked about.

When you do update the parent, republish it with everything else unchanged: the
same `slug`, `title`, `format`, `visibility`, `tags`, `summary`, `author` and
`owner` if it has one (keep the original author; you are correcting their page, not taking it over).
Leave `parent` out so it stays where it is. For an HTML parent, edit the
specific text in the existing document rather than regenerating the page. Change
only what the new page makes wrong, and keep the parent's wording and structure
otherwise.

Then tell the user, alongside the new page's URL, which statements you changed
in the parent and why, or which ones you left for them to decide. Keep it to a
line or two. This check only ever runs for a page that is filed, so someone who
doesn't use Topics meets it only when they update a page that somebody else
filed; for them, make the clear-cut correction, mention it in one line, and ask
nothing.

## How to publish
Write the body to a temp file to avoid shell-escaping issues, then POST it
(`$OWNER` empty for team and public pages, except when moving the user's own
private page to the team or republishing a team page that has an `owner`; `$SLUG` empty for a new page; `$PARENT`
empty to leave the page's place in Topics as it is, or `-` to take it out):

```bash
# body already written to "$BODY_FILE"
jq -n \
  --arg title "$TITLE" \
  --arg visibility "$VISIBILITY" \
  --arg format "$FORMAT" \
  --arg summary "$SUMMARY" \
  --arg author "$AUTHOR" \
  --arg owner "$OWNER" \
  --arg slug "$SLUG" \
  --arg parent "$PARENT" \
  --rawfile body "$BODY_FILE" \
  --argjson tags "$TAGS_JSON" \
  '{title:$title, visibility:$visibility, format:$format, summary:$summary, author:$author, tags:$tags, body:$body}
   + (if $owner == "" then {} else {owner:$owner} end)
   + (if $slug == "" then {} else {slug:$slug} end)
   + (if $parent == "" then {} elif $parent == "-" then {parent:null} else {parent:$parent} end)' \
| curl -sS -X POST "$WIKI_PUBLISH_URL/api/publish" \
    -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN" \
    -H "Content-Type: application/json" \
    --data @-
```

`TAGS_JSON` is a JSON array, e.g. `["planning","q3"]` (use `[]` if none). For a
new section page, also add `+ {placeholder:true}` to the object.

## After publishing
The API returns `{ "ok": true, "url": "/wiki/<slug>" | "/p/<id>", ... }`.
Give the user the full URL: `https://wiki.perfecti.io` + that path. The response
includes `parent` when the page is filed; say which topic it went under, and
then do the parent check above. A `private`
page's URL works only for its owner while signed in. If it returns `ok:false`,
report the error:

- **401**: the token is wrong or unset.
- **400**: a missing or wrong field (for example `owner` is required for a
  private page), or a `parent` that isn't allowed (see **Limits** above).
- **409**: the address belongs to another page that this publish must not
  replace or hide. Nothing was published. What to do depends on what the user
  meant:
  - **A new page:** send it again with a different `slug` (for example add
    `-2`) and give the user the new URL. Don't send `owner` to get past the
    409: on a team publish that would replace their private page with this
    body.
  - **Moving their private page to the team:** send `owner` only if they asked
    for the move and the body is that page's full content (see `private`
    above). Otherwise point them to **Make visible to team**.
  - **Updating or hiding a page at that address:** don't pick a new slug. Read
    it with `?slug=`. If it is now a team page, republish it as `internal` to
    update it, or point them to **Make private** to hide it (only the page's
    `owner` has that button; if it has no `owner`, tell the user it can't be
    made private). If it isn't found,
    it's a private page: check with the user which email owns it.
- **502 "Wrote the internal copy but could not remove the old one"**: the team
  page is live, but the private copy is still there. Send the same request
  again to finish the move.
- **502 "GitHub commit failed"**: the page may be live already. Check with
  `?slug=`; sending the same request again is safe.
- **500 or 501**: the server isn't set up to publish. Tell the user to check
  the wiki's Vercel env.

The request and answer fields are in the wiki's API contract (operation
`publishPage`).
