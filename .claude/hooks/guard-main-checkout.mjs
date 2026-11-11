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
//     `gh pr checkout`, and `pnpm release` / `pnpm version` (or release.sh). Allowed: reads, fetch,
//     worktree add/remove/prune, branch, tag, push, `checkout --detach <ref>` (without --force),
//     `merge --ff-only origin/<branch>` and `pull --ff-only`.
// A main checkout is recognised from the target path itself, so the guard works whichever directory
// the session started in. Worktrees are never affected. It does nothing in GitHub Actions.
//
// It is a guardrail against mistakes, not a security boundary. It reads commands the way Claude
// writes them, not as a full shell: a path built from a variable it can't resolve, `$(...)` or
// `xargs git ...`, or a file written by sed/redirection/a generator, is not caught.
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
const PASSTHROUGH_COMMANDS = new Set(["env", "command", "sudo", "time", "nice", "nohup", "exec", "builtin"]);
const SHELLS = new Set(["bash", "sh", "zsh"]);
const RELEASE_SCRIPTS = new Set(["release", "version"]);
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

// Expands ~, $HOME and variables assigned earlier in the same command. Returns null when the value
// still depends on something unknown ($VAR, $(...), backticks), so the caller can skip it.
function expandWord(word, vars) {
  let out = word.replace(/^~(?=$|\/)/, homedir());
  out = out.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, a, b) => {
    const name = a ?? b;
    return Object.prototype.hasOwnProperty.call(vars, name) ? vars[name] : m;
  });
  return /[$`]/.test(out) ? null : out;
}

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
    const re = /<<-?\s*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1/g;
    let m;
    while ((m = re.exec(line)) !== null) pending.push(m[2]);
  }
  return kept.join("\n");
}

// Splits a shell command into simple commands. Good enough for the commands Claude writes; it is
// not a shell parser (quoted separators stay whole).
function splitCommands(command) {
  const parts = [];
  let current = "";
  let quote = null;
  for (let i = 0; i < command.length; i++) {
    const ch = command[i];
    if (quote) {
      current += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      current += ch;
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
  return parts.map((p) => p.trim()).filter(Boolean);
}

function tokenize(simple) {
  const tokens = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m;
  while ((m = re.exec(simple)) !== null) tokens.push(m[1] ?? m[2] ?? m[3]);
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

// Returns a reason when a package-manager or script invocation would release from this directory.
function releaseInvocation(tokens, dir, vars) {
  const cmd = basename(tokens[0]);
  if (/release\.sh$/.test(cmd)) return { target: dir, what: tokens[0] };
  if (SHELLS.has(cmd) && tokens[1] && /release\.sh$/.test(tokens[1])) return { target: dir, what: `${cmd} ${tokens[1]}` };
  if (!["pnpm", "npm", "yarn"].includes(cmd)) return null;
  let target = dir;
  let i = 1;
  while (i < tokens.length && tokens[i].startsWith("-")) {
    const opt = tokens[i];
    if (opt === "-C" || opt === "--dir" || opt === "--prefix" || opt === "--cwd") {
      target = resolvePath(tokens[i + 1] ?? ".", dir, vars);
      i += 2;
      continue;
    }
    const eq = opt.match(/^--(dir|prefix|cwd)=(.*)$/);
    if (eq) target = resolvePath(eq[2], dir, vars);
    i += 1;
  }
  let script = tokens[i];
  if (script === "run" || script === "run-script") script = tokens[i + 1];
  return script && RELEASE_SCRIPTS.has(script) ? { target, what: `${cmd} ${script}` } : null;
}

function checkBash(command, cwd, depth = 0) {
  const vars = { HOME: homedir() };
  let dir = cwd;
  const dirStack = [];
  for (const simple of splitCommands(stripHeredocs(command))) {
    let tokens = tokenize(simple);
    // VAR=value (alone, or after export/local/declare/readonly) records a variable; before a command
    // it only sets that command's environment.
    if (["export", "local", "declare", "readonly"].includes(tokens[0])) tokens = tokens.slice(1).filter((t) => !t.startsWith("-"));
    if (tokens.length && tokens.every((t) => ASSIGNMENT.test(t))) {
      for (const t of tokens) {
        const [, name, raw] = t.match(ASSIGNMENT);
        const quoted = raw.match(/^(["'])(.*)\1$/s);
        const expanded = quoted && quoted[1] === "'" ? quoted[2] : expandWord(quoted ? quoted[2] : raw, vars);
        if (expanded === null) delete vars[name];
        else vars[name] = expanded;
      }
      continue;
    }
    // Skip `VAR=value cmd` prefixes and pass-through wrappers (`env -i git ...`, `command git ...`),
    // remembering a GIT_WORK_TREE prefix, which points git at another checkout.
    let workTree = vars.GIT_WORK_TREE;
    while (tokens.length && (ASSIGNMENT.test(tokens[0]) || PASSTHROUGH_COMMANDS.has(tokens[0]))) {
      const wrapper = PASSTHROUGH_COMMANDS.has(tokens[0]);
      const prefix = tokens[0].match(ASSIGNMENT);
      if (prefix && prefix[1] === "GIT_WORK_TREE") workTree = prefix[2].replace(/^(["'])(.*)\1$/s, "$2");
      tokens = tokens.slice(1);
      if (wrapper) while (tokens.length && tokens[0].startsWith("-")) tokens = tokens.slice(1);
    }
    if (!tokens.length) continue;
    const cmd = basename(tokens[0]);

    if (cmd === "cd" || cmd === "pushd") {
      if (cmd === "pushd") dirStack.push(dir);
      dir = tokens[1] ? resolvePath(tokens[1], dir, vars) : homedir();
      continue;
    }
    if (cmd === "popd") {
      dir = dirStack.length ? dirStack.pop() : null;
      continue;
    }
    if (SHELLS.has(cmd) && tokens[1] === "-c" && tokens[2] && depth < 2) {
      const hit = dir ? checkBash(tokens[2], dir, depth + 1) : null;
      if (hit) return hit;
      continue;
    }
    if (cmd === "git") {
      const start = workTree === undefined ? dir : resolvePath(workTree, dir, vars);
      const { target, args } = parseGit(tokens, start, vars);
      const reason = unsafeGitReason(args);
      if (!reason) continue;
      const top = mainCheckoutContaining(target);
      if (top) return { top, what: `\`${reason}\`` };
      continue;
    }
    if (cmd === "gh" && ((tokens[1] === "pr" && tokens[2] === "checkout") || (tokens[1] === "repo" && tokens[2] === "sync"))) {
      const top = mainCheckoutContaining(dir);
      if (top) return { top, what: `\`gh ${tokens[1]} ${tokens[2]}\`` };
      continue;
    }
    const release = releaseInvocation(tokens, dir, vars);
    if (release) {
      const top = mainCheckoutContaining(release.target);
      if (top) return { top, what: `\`${release.what}\`` };
    }
  }
  return null;
}

function refuse(top, what) {
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
