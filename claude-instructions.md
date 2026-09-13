<!-- BEGIN perfecti-team-wiki -->
# perfecti.io — Team Wiki (shared artifacts)

The team keeps shareable work in the **Team Wiki** at https://wiki.perfecti.io so anyone at perfecti.io can find it, instead of it living only on one person's machine or in a throwaway artifact.

**Strong default:** whenever you produce something meant to be *read or shared* — a report, summary, write-up, spec, analysis, dashboard, runbook, or a self-contained HTML page/tool/artifact — **also publish a copy to the Team Wiki and give the user the URL.** You may still create the normal local file or artifact as usual; the wiki copy is *in addition*, not instead. Treat "publish a copy to the wiki" as the expected finishing step for that kind of work.

- **Internal** visibility is the default (team-only, searchable at wiki.perfecti.io).
- Use **public** (unlisted, link-only) only when the user says they want to share it *outside* the team — and confirm before publishing a public page.
- Don't publish pure scratch/throwaway output, code changes in the current repo, secrets, or things the user says are private/local-only.

**How:** use the `publish-to-wiki` skill (or `/publish-to-wiki`). It handles the format and posts to the wiki API. If publishing isn't configured yet (`WIKI_PUBLISH_TOKEN` unset), tell the user to run the team-wiki `install.sh`.
<!-- END perfecti-team-wiki -->
