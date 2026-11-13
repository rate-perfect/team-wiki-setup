#!/usr/bin/env node
// Claude Code PreToolUse hook: refuses changes in the MAIN checkout of a Rate Perfect repo.
//
// A repo is guarded when its main checkout contains this file at .claude/hooks/guard-main-checkout.mjs:
// carrying the hook is how a repo opts in, so there is no list of repo names to keep in step. Each such
// repo has one main checkout that every developer and agent session shares. A change made there can clobber another session's uncommitted work or land on whatever branch its
// HEAD points at, so all changes go in a worktree (see the repo's CLAUDE.md). This hook enforces that:
//   - Edit / MultiEdit / Write / NotebookEdit of a file in a main checkout is refused, unless git
//     ignores the file (CLAUDE.local.md, local settings, plans, checkpoints).
//   - Bash commands that change a main checkout's files, index or branch are refused: git commit,
//     add, checkout/switch, merge, pull, reset, stash, rebase, restore, clean and the like,
//     `gh pr checkout`, and releases and publishes: `pnpm release` / `pnpm version` (or release.sh),
//     release-it, semantic-release, standard-version, `changeset version` / `changeset publish`,
//     `bun pm version`, and `npm|pnpm|yarn|bun publish` (also `pnpm m publish`), started the usual
//     ways (directly, `npx`, `pnpm exec`, `yarn <tool>`, `node .../tool.js`, `eval`). `publish --dry-run` and `release-it --dry-run` are allowed: they
//     don't publish or bump versions (lifecycle scripts like `prepack` still run). `yarn publish
//     --dry-run` is refused: Yarn 1's publish has no --dry-run.
//     Allowed: reads, fetch, worktree add/remove/prune, branch, tag, push, `checkout --detach <ref>`
//     (without --force), `merge --ff-only origin/<branch>` and `pull --ff-only`.
//   - The hook reads the command as Claude wrote it, before the shell fills in variables. When a
//     command that would change a checkout names its folder by something the hook can't work out (a
//     variable such as $REPO or ${REPO}, or command output such as $(...)), it is refused, wherever
//     it points: the hook can't tell whether that is a main checkout. Write the path out in full.
//     Read-only commands with variables are fine. Variables set earlier in the same command, $HOME,
//     ~, $PWD, $(pwd), $(git rev-parse --show-toplevel) and `cd -` are worked out. A `$` inside
//     single quotes is treated as a variable too.
// A main checkout is recognised from the target path itself, so the guard works whichever directory
// the session started in. Worktrees are never affected. It does nothing in GitHub Actions.
//
// It is a guardrail against mistakes, not a security boundary. It reads commands the way Claude
// writes them, not as a full shell. Not caught: `xargs git ...`, `find -exec git ...`, a command
// inside a quoted "$(...)", a script fed to sh by a heredoc, `make release`, `env -C <dir> git ...`,
// wrapper options it doesn't know, GIT_DIR / --git-dir pointing at a main checkout's .git from
// elsewhere, and a file written by sed/redirection/a generator. It reads `( cd <dir> && ... )` as if
// the cd still applied after the brackets, so a later change can be refused when it would be fine.
//
// The SAME file lives in every guarded repo at .claude/hooks/guard-main-checkout.mjs. Change every copy;
// to guard a new repo, copy it in unchanged and wire it in that repo's .claude/settings.json. A main
// checkout whose HEAD predates the file is not guarded until it is refreshed.
// It fails open: any unexpected error lets the tool call through, so it can never wedge a session.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { homedir } from "node:os";

const BLOCK_EXIT_CODE = 2;
// Where this file sits in a repo. A checkout that contains it is guarded.
const HOOK_PATH = join(".claude", "hooks", "guard-main-checkout.mjs");
const EDIT_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

// git subcommands that only read, or that never touch the checkout's files, index or HEAD.
// branch, symbolic-ref, config and fetch are checked separately below.
const SAFE_GIT_SUBCOMMANDS = new Set([
  "status", "log", "show", "diff", "grep", "blame", "shortlog", "describe", "rev-parse", "rev-list",
  "ls-files", "ls-tree", "ls-remote", "cat-file", "for-each-ref", "show-ref", "merge-base", "name-rev",
  "reflog", "remote", "worktree", "tag", "push", "check-ignore", "check-attr", "count-objects",
  "fsck", "gc", "prune", "help", "version", "var", "range-diff", "cherry", "whatchanged", "notes",
  "archive", "bundle", "verify-commit", "verify-tag", "credential", "maintenance", "clone",
  "merge-tree", "show-branch", "diff-tree", "diff-index", "diff-files", "annotate", "check-ref-format",
]);
// git global options that take a separate value argument.
const GIT_OPTS_WITH_VALUE = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"]);
// Prefixes that run the rest of the command unchanged.
const PASSTHROUGH_COMMANDS = new Set(["env", "command", "sudo", "time", "timeout", "nice", "nohup", "exec", "builtin", "corepack"]);
// Options of those wrappers that take a separate value: `sudo -u ann git ...`, `nice -n 5 git ...`.
const WRAPPER_OPTS_WITH_VALUE = {
  sudo: new Set(["-u", "-g", "-h", "-p", "-U", "-C"]),
  nice: new Set(["-n"]),
  timeout: new Set(["-s", "-k", "--signal", "--kill-after"]),
  env: new Set(["-u", "--unset"]),
};
// Package-manager options before the subcommand that take a separate value: `pnpm --filter x publish`.
const PM_OPTS_WITH_VALUE = new Set(["--filter", "-F", "--reporter", "--loglevel", "--registry", "--userconfig", "--cache", "--config", "--workspace-concurrency", "--tag", "--otp", "--access", "--filter-prod", "--resume-from", "--test-pattern", "--changed-files-ignore-pattern"]);
// Package-manager options known to take no value: the word after one is the subcommand, so
// `npm -s help publish` is a help command, not a publish. Yarn 1 knows only -s, --silent and --json
// of these: the others (-r, -q, -p and the like) take the next word as their value there, so
// `yarn -r x release` runs the release script.
const PM_FLAGS = new Set(["-s", "--silent", "-q", "--quiet", "--json", "--parseable", "-p", "-r", "--recursive", "-g", "--global", "-y", "--yes", "--long", "-l"]);
const YARN_FLAGS = new Set(["-s", "--silent", "--json"]);
// pnpm words before the command that mean -r: `pnpm m publish` is `pnpm -r publish`.
const PNPM_RECURSIVE_WORDS = new Set(["recursive", "multi", "m"]);
// Words that make a package-manager command a release or publish.
const RELEASE_WORDS = new Set(["publish", "release", "version"]);
const SHELLS = new Set(["bash", "sh", "zsh"]);
// Shell words that start a command inside `if`, `while`, `for` and the like: `do git add "$f"`.
const SHELL_KEYWORDS = new Set(["if", "then", "else", "elif", "while", "until", "do", "!"]);
const RELEASE_SCRIPTS = new Set(["release", "version"]);
// Script names like `release:patch` and `release:minor` release too.
const isReleaseScript = (name) => RELEASE_SCRIPTS.has(name) || /^release:/.test(name);
// A script named `publish` (often `changeset publish`) publishes when run with `run`.
const isRunReleaseScript = (name) => isReleaseScript(name) || name === "publish";
// Package-manager subcommands that never publish, even when a release word follows an unknown
// option: `npm --foo view version`. (`npm pkg set` edits package.json, but doesn't publish.)
const PM_READ_SUBCOMMANDS = new Set(["view", "info", "show", "pkg"]);
// Tools that release or publish, by command name. The value lists the subcommands that do; null
// means every run does.
const RELEASE_TOOLS = new Map([
  ["release-it", null],
  ["semantic-release", null],
  ["standard-version", null],
  ["changeset", new Set(["version", "publish"])],
  ["@changesets/cli", new Set(["version", "publish"])],
]);
const PACKAGE_MANAGERS = new Set(["pnpm", "npm", "yarn", "bun"]);
// Commands that fetch or find a package's tool and run it: `npx release-it`.
const PACKAGE_RUNNERS = new Set(["npx", "pnpx", "bunx"]);
// Package-manager subcommands that do the same: `pnpm exec release-it`, `npm x release-it`.
const EXEC_SUBCOMMANDS = new Set(["exec", "x", "dlx"]);
// Options of those runners that take a separate value: `npx -p release-it release-it`.
const RUNNER_OPTS_WITH_VALUE = new Set(["-p", "--package", "-c", "--call", "-w", "--workspace"]);
// How deep `bash -c "..."` and `$(...)` inside one another are followed.
const MAX_DEPTH = 4;
const FORCE_FLAGS = new Set(["-f", "--force", "--discard-changes", "-m", "--merge"]);
const RISKY_CONFIG_KEYS = /^(core\.(worktree|bare|hookspath))$/i;

function git(cwd, args) {
  try {
    return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return null;
  }
}

function real(p) {
  try {
    return realpathSync(p);
  } catch {
    return p;
  }
}

function nearestExistingDir(p) {
  let dir = p;
  while (!existsSync(dir)) {
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
  return dir;
}

// Returns the top-level dir when `dir` is inside the MAIN checkout of a guarded repo, else null
// (a linked worktree, another repo, or not a repo at all).
function mainCheckoutContaining(dir) {
  if (!dir) return null;
  const existing = nearestExistingDir(dir);
  if (!existing) return null;
  const top = git(existing, ["rev-parse", "--show-toplevel"]);
  if (!top || !existsSync(join(real(top), HOOK_PATH))) return null;
  const gitDir = git(existing, ["rev-parse", "--path-format=absolute", "--git-dir"]);
  const commonDir = git(existing, ["rev-parse", "--path-format=absolute", "--git-common-dir"]);
  if (!gitDir || !commonDir || real(gitDir) !== real(commonDir)) return null; // linked worktree
  return top;
}

function isIgnored(top, file) {
  try {
    execFileSync("git", ["-C", top, "check-ignore", "-q", "--", file], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// `$(git rev-parse --show-toplevel)`, which Claude often uses to name the repo it is in.
const TOPLEVEL = /\$\(\s*git\s+rev-parse\s+--show-toplevel\s*\)|`\s*git\s+rev-parse\s+--show-toplevel\s*`/g;

// Expands ~, $HOME, $PWD, $(pwd), $(git rev-parse --show-toplevel) and variables assigned earlier in the same command. Returns null
// when the value still depends on something unknown ($VAR, $(...), backticks), so the caller can
// treat the path as unknown.
function expandWord(word, vars) {
  let out = word.replace(/^~(?=$|\/)/, homedir());
  if (vars.PWD !== undefined) {
    out = out.replace(/\$\(\s*pwd\s*\)|`\s*pwd\s*`/g, () => vars.PWD);
    if (out.search(TOPLEVEL) >= 0) {
      const top = git(vars.PWD, ["rev-parse", "--show-toplevel"]);
      if (top !== null) out = out.replace(TOPLEVEL, () => top);
    }
  }
  out = out.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, a, b) => {
    const name = a ?? b;
    return Object.prototype.hasOwnProperty.call(vars, name) ? vars[name] : m;
  });
  return /[$`]/.test(out) ? null : out;
}

// Returns the absolute path a word names, or null when it can't be worked out.
function resolvePath(word, dir, vars) {
  const expanded = expandWord(word, vars);
  if (expanded === null) return null;
  if (isAbsolute(expanded)) return expanded;
  return dir ? resolve(dir, expanded) : null;
}

// Drops heredoc bodies (from a `<<TAG` line to the line `TAG`): they are data, not commands.
function stripHeredocs(command) {
  const lines = command.split("\n");
  const kept = [];
  const pending = [];
  for (const line of lines) {
    if (pending.length) {
      if (line.trim() === pending[0]) pending.shift();
      continue;
    }
    kept.push(line);
    // `<<<` is a here-string (one word of input), not a heredoc.
    const re = /(?<!<)<<(?!<)-?\s*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1/g;
    let m;
    while ((m = re.exec(line)) !== null) pending.push(m[2]);
  }
  return kept.join("\n");
}

const CLOSING = { "(": ")", "{": "}" };

// Returns the index of the bracket that closes the `(` or `{` at `open`, skipping quoted text, or
// the last index when it is never closed.
function closingBracket(s, open) {
  const stack = [CLOSING[s[open]]];
  let quote = null;
  for (let i = open + 1; i < s.length; i++) {
    const ch = s[i];
    if (quote) {
      if (ch === "\\" && quote !== "'") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "\\") i++;
    else if (ch === "'" || ch === '"' || ch === "`") quote = ch;
    else if (ch === "(" || ch === "{") stack.push(CLOSING[ch]);
    else if (ch === stack[stack.length - 1]) {
      stack.pop();
      if (!stack.length) return i;
    }
  }
  return s.length - 1;
}

// Returns the index of the backtick that closes the one at `open`, or the last index.
function closingBacktick(s, open) {
  for (let i = open + 1; i < s.length; i++) {
    if (s[i] === "\\") i++;
    else if (s[i] === "`") return i;
  }
  return s.length - 1;
}

// When `s[i]` starts a `$(...)`, `${...}` or backtick expansion, returns the index of its last
// character, else -1.
function expansionEnd(s, i) {
  if (s[i] === "$" && (s[i + 1] === "(" || s[i + 1] === "{")) return closingBracket(s, i + 1);
  if (s[i] === "`") return closingBacktick(s, i);
  return -1;
}

// Splits a shell command into simple commands. Good enough for the commands Claude writes; it is
// not a shell parser (quoted separators stay whole). An unquoted `$(...)`, `${...}` or backtick
// expansion stays inside its word; the command run by `$(...)` or backticks is also returned on its
// own, as { sub: "<command>" }, so it is checked too.
function splitCommands(command) {
  const parts = [];
  let current = "";
  let quote = null;
  for (let i = 0; i < command.length; i++) {
    const ch = command[i];
    if (quote) {
      // Inside "...", a backslash escapes the next character (`\"` doesn't end the quote).
      if (ch === "\\" && quote === '"') {
        current += command.slice(i, i + 2);
        i++;
        continue;
      }
      current += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === "\\") {
      // A backslash before a newline continues the line; before anything else it escapes it.
      current += command[i + 1] === "\n" ? " " : command.slice(i, i + 2);
      i++;
      continue;
    }
    // A `#` that starts a word starts a comment, to the end of the line.
    if (ch === "#" && (i === 0 || /\s/.test(command[i - 1]))) {
      const newline = command.indexOf("\n", i);
      if (newline < 0) break;
      i = newline - 1;
      continue;
    }
    const end = expansionEnd(command, i);
    if (end >= 0) {
      const text = command.slice(i, end + 1);
      if (ch === "`") parts.push({ sub: text.slice(1, -1) });
      else if (command[i + 1] === "(") parts.push({ sub: text.slice(2, -1) });
      // `${X:-$(cmd)}`: the commands inside a `${...}` run too.
      else parts.push(...splitCommands(text.slice(2, -1)).filter((p) => typeof p !== "string"));
      current += text;
      i = end;
      continue;
    }
    const two = command.slice(i, i + 2);
    if (two === "&&" || two === "||") {
      parts.push(current);
      current = "";
      i++;
      continue;
    }
    // A lone `&` (background) separates commands too; `&&` is handled above and `2>&1` / `&>` are
    // redirections, not separators.
    const loneAmpersand = ch === "&" && command[i - 1] !== ">" && command[i - 1] !== "<" && command[i + 1] !== ">";
    if (ch === ";" || ch === "|" || ch === "\n" || ch === "(" || ch === ")" || ch === "{" || ch === "}" || loneAmpersand) {
      parts.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  parts.push(current);
  return parts.map((p) => (typeof p === "string" ? p.trim() : p)).filter((p) => (typeof p === "string" ? p : p.sub.trim()));
}

// Splits a simple command into words, removing quotes and backslashes the way the shell does. A
// `$(...)`, `${...}` or backtick expansion stays whole inside its word, so expandWord can tell it is
// there. Redirections (`2>/dev/null`, `> out.txt`, `2>&1`, `&>log`, `<in`) are left out: they are not
// arguments, and `git symbolic-ref --short X 2>/dev/null` must not read as a two-argument write.
function tokenize(simple) {
  const tokens = [];
  let word = null;
  // True after a redirection operator: the next word is its target, not an argument.
  let dropNext = false;
  const take = (text) => {
    word = (word ?? "") + text;
  };
  const finish = () => {
    if (word !== null && !dropNext) tokens.push(word);
    else if (word !== null) dropNext = false;
    word = null;
  };
  for (let i = 0; i < simple.length; i++) {
    const ch = simple[i];
    if (/\s/.test(ch)) {
      finish();
      continue;
    }
    if (ch === ">" || ch === "<") {
      // A single unquoted digit (`2>`) or `&` (`&>`) right before the operator belongs to it. Any other
      // word before it ends there and stays an argument: `a>b` is `a`, then a redirection; `"2">x` and
      // `12>x` (zsh reads only one digit as an fd) keep `2` and `12`.
      if (word !== null && !/(?:^|\s)[\d&]$/.test(simple.slice(0, i))) finish();
      word = null;
      let j = i + 1;
      while (j < simple.length && /[>&|]/.test(simple[j])) j++;
      const op = simple.slice(i, j);
      // `2>&1`, `>&2`, `<&-`: duplicates an fd, no file word follows.
      if (/&$/.test(op) && /^(\d+|-)/.test(simple.slice(j))) {
        while (j < simple.length && /[\d-]/.test(simple[j])) j++;
      } else {
        dropNext = true;
      }
      i = j - 1;
      continue;
    }
    if (ch === "'") {
      const close = simple.indexOf("'", i + 1);
      const stop = close < 0 ? simple.length : close;
      take(simple.slice(i + 1, stop));
      i = stop;
      continue;
    }
    if (ch === '"') {
      take("");
      let j = i + 1;
      while (j < simple.length && simple[j] !== '"') {
        const end = expansionEnd(simple, j);
        if (end >= 0) {
          take(simple.slice(j, end + 1));
          j = end + 1;
        } else if (simple[j] === "\\" && /["\\$`]/.test(simple[j + 1] ?? "")) {
          take(simple[j + 1]);
          j += 2;
        } else {
          take(simple[j]);
          j += 1;
        }
      }
      i = j;
      continue;
    }
    if (ch === "\\") {
      take(simple[i + 1] ?? "");
      i++;
      continue;
    }
    const end = expansionEnd(simple, i);
    if (end >= 0) {
      take(simple.slice(i, end + 1));
      i = end;
      continue;
    }
    take(ch);
  }
  finish();
  return tokens;
}

const ASSIGNMENT = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/s;

function positional(args) {
  return args.filter((a) => !a.startsWith("-"));
}

// Returns a reason string when this git invocation would change the checkout, else null.
function unsafeGitReason(args) {
  const sub = args[0];
  const rest = args.slice(1);
  if (!sub) return null;
  if (SAFE_GIT_SUBCOMMANDS.has(sub)) return null;
  if (sub === "branch") return rest.some((a) => /^(-m|-M|--move)$/.test(a)) ? "git branch -m" : null;
  if (sub === "symbolic-ref") return positional(rest).length >= 2 ? "git symbolic-ref (write)" : null;
  if (sub === "config") {
    // Only a write is risky: a value after the key, or --unset/--add/--replace-all.
    const writes = positional(rest).length >= 2 || rest.some((a) => /^--(unset|unset-all|add|replace-all)$/.test(a));
    return writes && rest.some((a) => RISKY_CONFIG_KEYS.test(a)) ? "git config core.*" : null;
  }
  if (sub === "fetch") return rest.includes("--update-head-ok") ? "git fetch --update-head-ok" : null;
  if (sub === "stash" && (rest[0] === "list" || rest[0] === "show")) return null;
  if (sub === "submodule" && (rest[0] === "status" || rest[0] === "summary")) return null;
  if (sub === "checkout" || sub === "switch") {
    if (rest.length === 0) return null; // bare: just reports
    const detach = rest.includes("--detach");
    const forced = rest.some((a) => FORCE_FLAGS.has(a));
    const creates = rest.some((a) => /^-[bBcC]$|^-[bBcC]\S|^--orphan|^--create|^--force-create/.test(a));
    return detach && !forced && !creates && !rest.includes("--") ? null : `git ${sub}`;
  }
  if (sub === "merge") {
    const refs = positional(rest);
    return rest.includes("--ff-only") && refs.every((r) => r.startsWith("origin/")) ? null : "git merge";
  }
  if (sub === "pull") return rest.includes("--ff-only") && positional(rest).length <= 1 ? null : "git pull";
  return `git ${sub}`;
}

// Parses `git [global options] <subcommand> ...`, resolving -C and --work-tree against `dir`.
// Returns { target, args }, with target null when the directory can't be resolved.
function parseGit(tokens, dir, vars) {
  let target = dir;
  let i = 1;
  while (i < tokens.length && tokens[i].startsWith("-")) {
    const opt = tokens[i];
    const eq = opt.indexOf("=");
    const name = eq > 0 ? opt.slice(0, eq) : opt;
    const inlineValue = eq > 0 ? opt.slice(eq + 1) : undefined;
    const value = inlineValue ?? tokens[i + 1];
    if (name === "-C" || name === "--work-tree") target = resolvePath(value ?? ".", target, vars);
    i += GIT_OPTS_WITH_VALUE.has(name) && inlineValue === undefined ? 2 : 1;
  }
  return { target, args: tokens.slice(i) };
}

// Returns the release a release tool would make, e.g. "release-it" or "changeset publish", when
// `tokens` (the tool, then its arguments) runs one, else null.
function releaseToolCall(tokens) {
  if (!tokens.length) return null;
  // `npx release-it@17` and `npx @changesets/cli@2 publish` name a package and its version.
  // `node .../semantic-release/bin/semantic-release.js` names the tool's own script.
  const raw = tokens[0].replace(/(.)@[^/]*$/, "$1").replace(/\.[cm]?js$/, "");
  const name = raw.startsWith("@") ? raw : basename(raw);
  if (!RELEASE_TOOLS.has(name)) return null;
  const releasing = RELEASE_TOOLS.get(name);
  if (releasing === null) return name;
  const sub = positional(tokens.slice(1))[0];
  return sub && releasing.has(sub) ? `${name} ${sub}` : null;
}

// Skips the options of `npx` / `npm exec` / `pnpm dlx` and the like, up to the command they run.
function afterRunnerOptions(tokens, i) {
  while (i < tokens.length && tokens[i].startsWith("-")) {
    if (tokens[i] === "--") return i + 1;
    i += RUNNER_OPTS_WITH_VALUE.has(tokens[i]) ? 2 : 1;
  }
  return i;
}

// A publish or a release-it run with --dry-run doesn't publish. Not `changeset publish`, not a
// script run with `run`, where --dry-run may never reach the tool, and not `yarn publish`: Yarn 1's
// publish has no --dry-run. `yarn npm publish --dry-run` (or `-n`) dry-runs from Yarn 4.9.3; older
// Yarn refuses the flag.
function isDryRun(tokens, what) {
  const dry = tokens.some((t) => /^--dry-run(=true)?$/.test(t)) || (what === "yarn npm publish" && tokens.includes("-n"));
  if (!dry) return false;
  if (/changeset|\srun(-script)?\s/.test(what) || what === "yarn publish") return false;
  return /(^|\s)(release-it|publish)$/.test(what);
}

// Returns { target, what } when a package-manager, package-runner or script invocation would release
// or publish, with target the directory it runs in (null when that can't be worked out), else null.
function releaseInvocation(tokens, dir, vars) {
  const hit = findRelease(tokens, dir, vars);
  return hit && isDryRun(tokens, hit.what) ? null : hit;
}

function findRelease(tokens, dir, vars) {
  const cmd = basename(tokens[0]);
  if (/release\.sh$/.test(cmd)) return { target: dir, what: tokens[0] };
  if (SHELLS.has(cmd) && tokens[1] && /release\.sh$/.test(tokens[1])) return { target: dir, what: `${cmd} ${tokens[1]}` };
  // `node [options] <script> ...`: the script is the first word that isn't an option.
  const nodeScript = tokens.findIndex((t, n) => n > 0 && !t.startsWith("-"));
  const direct = releaseToolCall(cmd !== "node" ? tokens : nodeScript > 0 ? tokens.slice(nodeScript) : []);
  if (direct) return { target: dir, what: direct };
  // `npx -c "release-it --ci"` and `npm exec -c "..."` run the quoted command.
  const call = tokens.findIndex((t) => t === "-c" || t === "--call");
  if ((PACKAGE_RUNNERS.has(cmd) || (PACKAGE_MANAGERS.has(cmd) && tokens.some((t) => EXEC_SUBCOMMANDS.has(t)))) && call > 0 && tokens[call + 1]) {
    const tool = releaseToolCall(tokenize(tokens[call + 1]));
    if (tool) return { target: dir, what: `${cmd} -c ${tool}` };
  }
  if (PACKAGE_RUNNERS.has(cmd)) {
    const tool = releaseToolCall(tokens.slice(afterRunnerOptions(tokens, 1)));
    return tool ? { target: dir, what: `${cmd} ${tool}` } : null;
  }
  if (!PACKAGE_MANAGERS.has(cmd)) return null;
  let target = dir;
  let i = 1;
  while (i < tokens.length && (tokens[i].startsWith("-") || (cmd === "pnpm" && PNPM_RECURSIVE_WORDS.has(tokens[i])))) {
    const opt = tokens[i];
    if (opt === "-C" || opt === "--dir" || opt === "--prefix" || opt === "--cwd") {
      target = resolvePath(tokens[i + 1] ?? ".", dir, vars);
      i += 2;
      continue;
    }
    const eq = opt.match(/^--(dir|prefix|cwd)=(.*)$/);
    if (eq) target = resolvePath(eq[2], dir, vars);
    // `pnpm --filter <pkg> publish`, `npm -w <pkg> publish`: skip the package name too.
    const takesValue = PM_OPTS_WITH_VALUE.has(opt) || (cmd === "npm" && (opt === "-w" || opt === "--workspace"));
    i += takesValue ? 2 : 1;
  }
  // An option whose value the hook doesn't know (`npm --foo bar publish`): look one word further.
  const unknownValue = i > 1 && tokens[i - 1].startsWith("-") && !tokens[i - 1].includes("=") && !(cmd === "yarn" ? YARN_FLAGS : PM_FLAGS).has(tokens[i - 1]);
  if (unknownValue && tokens[i] && !RELEASE_WORDS.has(tokens[i]) && !PM_READ_SUBCOMMANDS.has(tokens[i]) && RELEASE_WORDS.has(tokens[i + 1])) i += 1;
  // `yarn workspace <pkg> <command>` runs the command in that package.
  if (cmd === "yarn" && tokens[i] === "workspace") i += 2;
  const sub = tokens[i];
  if (!sub) return null;
  if (sub === "run" || sub === "run-script") {
    const script = tokens[i + 1];
    if (script && isRunReleaseScript(script)) return { target, what: `${cmd} run ${script}` };
    // `yarn run release-it` runs the tool when there is no script of that name.
    const tool = releaseToolCall(tokens.slice(i + 1));
    return tool ? { target, what: `${cmd} run ${tool}` } : null;
  }
  if (isReleaseScript(sub) || sub === "publish") return { target, what: `${cmd} ${sub}` };
  if (cmd === "yarn" && sub === "npm" && tokens[i + 1] === "publish") return { target, what: "yarn npm publish" };
  // `bun pm version patch` bumps the version, then commits and tags it.
  if (cmd === "bun" && sub === "pm" && tokens[i + 1] === "version") return { target, what: "bun pm version" };
  // `pnpm exec release-it`, `npm exec -- release-it`, and `yarn release-it` / `pnpm release-it`,
  // which run a package's tool directly.
  const exec = EXEC_SUBCOMMANDS.has(sub);
  const tool = releaseToolCall(tokens.slice(exec ? afterRunnerOptions(tokens, i + 1) : i));
  return tool ? { target, what: `${cmd} ${exec ? `${sub} ` : ""}${tool}` } : null;
}

// Returns { top, what } for the first part of the command that would change a main checkout, or
// { top: null, what } for one that would change a checkout the hook can't work out; else null.
function checkBash(command, cwd, depth = 0, inherited = {}) {
  const vars = { HOME: homedir(), ...inherited };
  let dir = cwd;
  const dirStack = [];
  let previousDir = null;
  // Where a change goes: the main checkout's top, null for a path the hook can't work out, or
  // undefined when it is safe (a worktree, another repo, or not a repo).
  const hitAt = (target, what) => {
    if (target === null) return { top: null, what };
    const top = mainCheckoutContaining(target);
    return top ? { top, what } : undefined;
  };
  for (const part of splitCommands(stripHeredocs(command))) {
    if (dir === null) delete vars.PWD;
    else vars.PWD = dir;
    // The command a `$(...)` or backtick runs: checked on its own, in the current directory. A `cd`
    // inside it doesn't move the rest of the command.
    if (typeof part !== "string") {
      const hit = depth < MAX_DEPTH ? checkBash(part.sub, dir, depth + 1, vars) : null;
      if (hit) return hit;
      continue;
    }
    let tokens = tokenize(part);
    while (tokens.length && SHELL_KEYWORDS.has(tokens[0])) tokens = tokens.slice(1);
    // VAR=value (alone, or after export/local/declare/readonly) records a variable; before a command
    // it only sets that command's environment.
    if (["export", "local", "declare", "readonly"].includes(tokens[0])) tokens = tokens.slice(1).filter((t) => !t.startsWith("-"));
    if (tokens.length && tokens.every((t) => ASSIGNMENT.test(t))) {
      for (const t of tokens) {
        const [, name, raw] = t.match(ASSIGNMENT);
        const expanded = expandWord(raw, vars);
        // A value the hook can't work out stays unknown (`$NAME`), so a later use of it is unknown
        // too, and `export GIT_WORK_TREE=$WT` still counts as pointing git somewhere unknown.
        vars[name] = expanded === null ? `$${name}` : expanded;
      }
      continue;
    }
    // Skip `VAR=value cmd` prefixes and pass-through wrappers (`env -i git ...`, `command git ...`),
    // remembering a GIT_WORK_TREE prefix, which points git at another checkout.
    let workTree = vars.GIT_WORK_TREE;
    while (tokens.length && (ASSIGNMENT.test(tokens[0]) || PASSTHROUGH_COMMANDS.has(tokens[0]))) {
      const wrapper = PASSTHROUGH_COMMANDS.has(tokens[0]);
      const prefix = tokens[0].match(ASSIGNMENT);
      if (prefix && prefix[1] === "GIT_WORK_TREE") workTree = prefix[2];
      const withValue = WRAPPER_OPTS_WITH_VALUE[tokens[0]];
      const isTimeout = tokens[0] === "timeout";
      tokens = tokens.slice(1);
      if (!wrapper) continue;
      while (tokens.length && tokens[0].startsWith("-")) tokens = tokens.slice(withValue?.has(tokens[0]) ? 2 : 1);
      if (isTimeout) tokens = tokens.slice(1); // the duration
    }
    if (!tokens.length) continue;
    const cmd = basename(tokens[0]);

    if (cmd === "cd" || cmd === "pushd") {
      if (cmd === "pushd") dirStack.push(dir);
      const where = tokens.slice(1).find((t) => !/^-[LPe@]+$/.test(t));
      const from = dir;
      // `cd -` goes back to the previous folder, unknown when the command didn't set it.
      dir = where === undefined ? homedir() : where === "-" ? previousDir : resolvePath(where, dir, vars);
      previousDir = from;
      continue;
    }
    if (cmd === "popd") {
      // popd with an empty stack fails and leaves the directory as it was.
      if (dirStack.length) dir = dirStack.pop();
      continue;
    }
    if (SHELLS.has(cmd) && /^-[a-z]*c$/.test(tokens[1] ?? "") && tokens[2]) {
      const hit = depth < MAX_DEPTH ? checkBash(tokens[2], dir, depth + 1, vars) : null;
      if (hit) return hit;
      continue;
    }
    // `eval "git commit ..."` runs its words as a command.
    if (cmd === "eval" && tokens.length > 1) {
      const hit = depth < MAX_DEPTH ? checkBash(tokens.slice(1).join(" "), dir, depth + 1, vars) : null;
      if (hit) return hit;
      continue;
    }
    if (cmd === "git") {
      const start = workTree === undefined ? dir : resolvePath(workTree, dir, vars);
      const { target, args } = parseGit(tokens, start, vars);
      const reason = unsafeGitReason(args);
      const hit = reason ? hitAt(target, `\`${reason}\``) : undefined;
      if (hit) return hit;
      continue;
    }
    if (cmd === "gh" && ((tokens[1] === "pr" && tokens[2] === "checkout") || (tokens[1] === "repo" && tokens[2] === "sync"))) {
      const hit = hitAt(dir, `\`gh ${tokens[1]} ${tokens[2]}\``);
      if (hit) return hit;
      continue;
    }
    const release = releaseInvocation(tokens, dir, vars);
    const hit = release ? hitAt(release.target, `\`${release.what}\``) : undefined;
    if (hit) return hit;
  }
  return null;
}

function refuse(top, what) {
  if (top === null) {
    process.stderr.write(
      [
        `Blocked: ${what} in a folder named by a variable or by command output ($VAR, \${VAR}, $(...)).`,
        "This guard reads the command before the shell fills those in, so it can't tell whether the",
        "folder is a repo's MAIN checkout, where changes are never made.",
        "Write the folder's path out in full instead (for example `git -C /full/path/to/worktree commit ...`",
        "or `cd /full/path/to/worktree && ...`), and make changes only in a worktree.",
        "Commands that only read can use variables.",
      ].join("\n") + "\n",
    );
    process.exit(BLOCK_EXIT_CODE);
  }
  const repo = basename(top);
  process.stderr.write(
    [
      `Blocked: ${what} in ${top}, the MAIN checkout of ${repo}.`,
      "Every session shares this checkout, so changes are never made here. Use a worktree:",
      `  git -C ${top} fetch origin <default branch>`,
      `  git -C ${top} worktree add ../${repo}-<short-name> origin/<default branch> -b <type>/<short-name>`,
      "then edit, commit and run releases inside that worktree.",
      "Allowed in the main checkout: reading, fetch, worktree add/remove/prune, branch, tag, push,",
      "`checkout --detach <ref>`, `merge --ff-only origin/<branch>`, bare `pull --ff-only`, and files git ignores.",
    ].join("\n") + "\n",
  );
  process.exit(BLOCK_EXIT_CODE);
}

function main() {
  if (process.env.GITHUB_ACTIONS === "true") return;
  const input = JSON.parse(readFileSync(0, "utf8"));
  const cwd = input.cwd || process.cwd();
  const tool = input.tool_name;
  const toolInput = input.tool_input || {};

  if (EDIT_TOOLS.has(tool)) {
    const raw = toolInput.file_path || toolInput.notebook_path;
    if (!raw) return;
    const file = resolvePath(raw, cwd, {}) ?? raw;
    const top = mainCheckoutContaining(dirname(file));
    if (top && !isIgnored(top, file)) refuse(top, `${tool} of ${file}`);
    return;
  }
  if (tool === "Bash" && typeof toolInput.command === "string") {
    const hit = checkBash(toolInput.command, cwd);
    if (hit) refuse(hit.top, hit.what);
  }
}

try {
  main();
} catch {
  // Fail open: never block a tool call because the guard itself broke.
}
