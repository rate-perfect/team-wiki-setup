---
name: verification
description: Exhaustively confirm or refute whatever claim is currently in context — a suspected bug cause, a hypothesis, a "this is how it works" statement — pursuing 100% certainty. Invoke when the user wants proof, not a best guess. On trigger, chase every available angle (read code, run read-only commands, read staging/production read-only logs and endpoints, ask the user for auth tokens / run IDs / log dumps) before returning a verdict. Only answers "100% not possible" after every angle is exhausted.
---

# verify — pursue 100% confirmation

The user has triggered this skill to get **certainty**, not a plausible explanation. Your
job is to confirm or refute the specific thing in context, and to keep going until you
either **prove it to 100%** or have genuinely exhausted every angle available to you.

## 1. Pin down the exact claim

Restate what you are verifying as a single, falsifiable proposition. The subject is
**whatever is in context** — the current bug hypothesis, the cause you just proposed, a
statement the user made, or something they stated immediately before triggering `verify`.
If there is any ambiguity about *which* claim, ask one sharp clarifying question before
spending effort. Otherwise, state the proposition back in one line and proceed.

A claim is at 100% only when there is **direct evidence** that it is true and no
plausible alternative explanation survives. "Consistent with the symptoms" is not 100%.
"The only remaining explanation" is not 100% unless you have actually ruled the others out.

### When the context is a plan (or a proposed fix)

If the thing in context is a **plan** or a proposed solution — e.g. "write up a plan for
XYZ /verify" — there are **two** distinct propositions to verify, and both must reach 100%
before the plan is trustworthy:

1. **Root cause** — the diagnosis the plan is built on. Verify this *first* and to the full
   standard below. A plan built on an unverified root cause is a guess in disguise; do not
   present it as sound until the root cause is confirmed.
2. **Solution** — that the proposed change actually resolves that root cause, and does not
   break adjacent behavior. Verify the mechanism (why the fix works, in the real code
   path), the assumptions it depends on, and the blast radius.

Write the plan, but gate its confidence on this: state clearly which of the two are at
100% and which are not. If the root cause cannot be confirmed to 100%, say so explicitly
and mark the plan as **provisional / contingent on that gap** — never let an unverified
root cause be presented as settled.

## 2. Inventory what would prove it

Before gathering, write down (briefly) what evidence would constitute *proof* versus mere
correlation. Enumerate the competing explanations. 100% means: the proof-grade evidence is
in hand **and** every competing explanation has been positively excluded.

## 3. Gather from every available source

Pursue these actively — do not stop at the first source that is merely suggestive:

- **The code.** Read the actual implementation, not your memory of it. Trace the real
  path. Check git history/blame for when behavior changed. Verify referenced symbols,
  fields, flags, and enum values still exist.
- **Match the evidence to the code that produced it (version skew).** Before trusting any
  source, confirm the code you are reasoning about is the code that actually produced the
  behavior. This trap has many forms: prod/staging running a different commit than the
  branch you're reading, logs from an older deploy, a contract/generated-client version
  ahead of or behind the backend, or — when you read **another local repo** for evidence —
  a stale or wrong-branch checkout. For cross-repo reads, don't trust whatever is checked
  out: the main branch is **`dev`** for essentially every repo, **except
  `lender-match-web`, whose main branch is `staging`** — fetch and read the
  remote-tracking ref (`origin/dev`, or `origin/staging`) rather than a possibly stale
  working tree. In every case, note in your evidence chain the exact branch/commit/version
  you actually read.
- **Read-only commands.** Run anything non-mutating that yields evidence — tests, type
  checks, builds, `grep`, log inspection, existing smoke scripts, CLI read calls.
- **Logs.** Read-only application/CI/deploy logs. If you need a specific log the user has
  access to, ask them to paste it.
- **Staging and production.** Read-only inspection is fair game — staging or prod
  endpoints, read-only smoke scripts against a real environment, GET calls via existing
  generated clients. Never perform writes/mutations against any environment.
- **The user.** They can provide things you cannot obtain yourself. Ask for them
  explicitly and specifically when they would close the gap:
  - auth tokens / bearer credentials (for read-only calls)
  - run IDs, request IDs, correlation IDs, deal/entity IDs
  - pasted log excerpts or console output
  - which environment to look at, and any URLs/hostnames
  When you ask, say *exactly* what you need and *why it gets you to 100%*, so the user can
  hand it over in one step.

Be adversarial: actively try to **disprove** the claim. A claim that survives a real
attempt to break it is far stronger than one you only looked for support for.

## 4. Hard boundary — no database access

**You cannot get direct database access, and you must not ask for it or assume it.**
Work around it with everything else (APIs, logs, code, the user's inputs). If — and only
if — the *only* thing that could push a claim to 100% is a direct DB read, that is a
legitimate reason to land on "100% not possible" (see below), and you must name it as the
specific missing piece.

## 5. Verdict

Return one of:

- **✅ CONFIRMED (100%)** — state the claim, then the direct evidence chain that proves it,
  and note that the competing explanations were excluded (how). Make the evidence
  **reproducible**: give the exact read-only command, run/request ID, query, or file:line
  that produced each proof, so the user can re-run it and land on the same verdict. A
  proof the user can reproduce beats one they have to take on trust.
- **❌ REFUTED** — the claim is false; give the evidence that disproves it and the actual
  finding if you have it.
- **⚠️ NOT 100% POSSIBLE** — only after you have genuinely exhausted every angle above.
  You must then provide:
  1. Your best current assessment and the confidence level you *can* stand behind.
  2. Exactly what evidence is missing and why each avenue you tried fell short.
  3. The specific thing that *would* have closed the gap (e.g. "a direct DB read of
     table X", or "a credential you don't have"), so the user knows precisely what the
     ceiling was.

Do not settle for "⚠️ NOT 100% POSSIBLE" out of convenience. It is only valid when the
remaining gap is truly unbridgeable with the code, read-only tooling, the environments,
and everything the user is able to provide. Ask for what you need before conceding.
