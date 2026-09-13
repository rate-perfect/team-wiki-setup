<!-- BEGIN perfecti-team-wiki -->
# perfecti.io — Team Wiki (shared artifacts)

The team keeps shareable work in the **Team Wiki** at https://wiki.perfecti.io so anyone at perfecti.io can find it, instead of it living only on one person's machine, one Claude account, or a throwaway artifact.

**Look in the wiki first.** People here use several Claude accounts, and artifacts live per-account — but the wiki aggregates them. Before you write a new shareable doc, and whenever the user refers to something they think they already made ("the doc about X", "my write-up on Y", "didn't I already do this?"), search the wiki so you find an existing copy from any account instead of duplicating it. With `WIKI_PUBLISH_TOKEN` set:

```bash
curl -sS "$WIKI_PUBLISH_URL/api/pages?q=SEARCH%20TERMS" -H "Authorization: Bearer $WIKI_PUBLISH_TOKEN"
```

returns matching **internal (team)** pages as JSON (`{slug,title,summary,tags,author,url}`). If you find it, point the user to `https://wiki.perfecti.io` + that `url` instead of remaking it. (Private pages are owner-only and never appear in this index — see below.)

**Publish shareable output (strong default).** Whenever you produce something meant to be read or shared — a report, summary, write-up, spec, analysis, dashboard, runbook, or self-contained HTML artifact — also publish a copy to the wiki and give the user the URL, *in addition* to any local file/artifact.

**Visibility — three tiers:**
- **internal** (default): team-only, listed + searchable at wiki.perfecti.io.
- **private**: owner-only. Only the person whose email is the page's `owner` can see it (via the dashboard's **All / Team / Private** filter and at its URL); to everyone else it 404s. Use it when the user wants something on their own wiki space but not shared with the team. Requires an `owner` email. The owner can flip it to team-shared any time with the **Make visible to team** button on the page — and back with **Make private** — so private-first is a safe default when unsure.
- **public**: unlisted, link-only, for sharing *outside* the team.

Publish **internal** without a prompt (just mention you're doing it). **Confirm before public.** If the user says something is personal/not-for-the-team, publish it **private** with their email as `owner` — or don't publish it at all if it's truly sensitive (a private page is still committed to the wiki's git repo, so it's hidden on the site but not from repo collaborators).

Don't publish pure scratch/throwaway output, in-repo code changes, or secrets.

**How:** use the `publish-to-wiki` skill (or `/publish-to-wiki`). If publishing isn't configured yet (`WIKI_PUBLISH_TOKEN` unset), tell the user to run the team-wiki `install.sh`.
<!-- END perfecti-team-wiki -->
