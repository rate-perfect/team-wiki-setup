#!/usr/bin/env python3
"""Repo rules first: a session (or subagent) may not change anything in a git checkout until it has read that
checkout's own rule files.

The hook holds NO rules of its own. It finds the checkout's rule files (CLAUDE.md, .claude/CLAUDE.md,
CLAUDE.local.md, docs/team-knowledge.md, files CLAUDE.md imports with @path) and lists its commands, agents and
rule docs. It then blocks the change until each rule file shows up in this session's transcript, either loaded by
Claude Code ("Contents of <file>") or opened with Read. Small rule sets are shown in full in the block message
instead, which counts as read. The repo stays the single source of truth.

Running code in a checkout counts too, even from another folder: a build or test pointed at it (`npm --prefix
<dir> test`, `pnpm --dir <dir> build`, `make -C <dir>`), or a script run with node, tsx, python and the like that
lives in it or imports its code by a relative or absolute path. So work started in repo A follows repo B's rules
when it touches repo B. The listing also names the checkout's skills (.claude/skills/<name>/SKILL.md).

Wired as a PreToolUse hook on Edit|Write|NotebookEdit|Bash (user settings), and SessionStart(compact) with --reset
so the check runs again after the conversation is compacted.

Fails open: any error lets the call through and is logged to state/errors.log. After MAX_DENIALS blocks for the
same checkout it lets the call through (and logs it) so a session can never be stuck.

Optional main checkout guard: it is off unless your ~/.claude/CLAUDE.md has a "# Never change a main checkout"
section. With that section, it also refuses changes (edits, commits, branch switches, builds) in a repo's main
checkout and quotes the section back. Remove the section to turn it off again.

REPO_RULES_CLONE_ROOTS (optional, colon-separated folders) says where your local clones live. It is used only to
find the repo named in a PR command (gh pr create --repo ..., az repos pr create ...) run from outside that repo.

Distributed by https://github.com/rate-perfect/team-wiki-setup (install.sh). Change it there, not in ~/.claude/hooks.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

STATE = os.path.expanduser("~/.claude/hooks/state/repo-rules-first")
INLINE_LIMIT = 20000          # rule sets up to this size are shown in full in the message
MAX_DENIALS = 4
RULE_FILES = ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "docs/team-knowledge.md"]
LISTED_DIRS = [".claude/commands", ".claude/agents", "docs/claude", ".claude/rules"]
# Where local clones live, used only to find a repo named in a PR command (az/gh/curl) run outside it.
DEFAULT_CLONE_ROOTS = "~/projects/rate-perfect-workspace:~/projects:~/m3:~/m3/boss-modernization:~/m3/boss5"
CLONE_ROOTS = [r for r in (os.environ.get("REPO_RULES_CLONE_ROOTS") or DEFAULT_CLONE_ROOTS).split(":") if r]
STATE_MAX_AGE_DAYS = 14       # per-session state older than this is deleted when a session is compacted
SKIP_DIRS = {"node_modules", "bin", "obj", ".git", "dist", "build", ".venv", "venv", "Generated", "generated"}

# Bash commands that change a checkout or act on its PRs. Read-only commands pass without a check.
MUTATING = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(?:commit|push|merge|cherry-pick|rebase|reset|revert|am|apply|checkout|switch|"
    r"restore|rm|mv|add|stash|tag)\b"
    r"|\baz\s+repos\s+pr\s+(?:create|update|set-vote)\b"
    r"|\bgh\s+pr\s+(?:create|merge|edit|ready)\b"
    r"|\bcurl\b[^|;&]*(?:-X\s*|--request\s+)(?:POST|PATCH|PUT|DELETE)\b"
    r"|\bsed\s+-i|\btee\b|\b(?:cp|mv|rm|touch|mkdir|ln|patch|truncate)\s"
    r"|\b(?:dotnet\s+(?:add|remove|new|format)|npm\s+(?:install|i|uninstall|version)|pnpm\s+(?:add|install|remove)"
    r"|yarn\s+(?:add|remove)|poetry\s+(?:add|remove|lock))\b"
    r"|(?<![0-9&>])>{1,2}\s*(?!/dev/null|&)\S"
)
REPO_IN_PR_CMD = re.compile(
    r"(?:--repository\s+|--repo\s+(?:[\w.-]+/)?|_git/|/repositories/|github\.com[/:][\w.-]+/)([\w.-]+?)(?:\.git)?"
    r"(?=[/\s\"'?]|$)"
)
REPO_TOOL = re.compile(r"(?:^|[\s;&|(])(?:git|az|gh|dotnet|npm|pnpm|yarn|poetry|make)\s")
PR_TOOL = re.compile(r"\baz\s+repos\s+pr\s+(?:create|update|set-vote)\b|\bgh\s+pr\s+(?:create|merge|edit|ready)\b"
                     r"|\bcurl\b[^|;&]*(?:-X\s*|--request\s+)(?:POST|PATCH|PUT)\b")
SOURCE_BRANCH = re.compile(r"(?:--source-branch|--head|-H)\s+(?:refs/heads/)?([\w./-]+)")


def git(args, cwd):
    try:
        out = subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def existing_dir(path):
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return path


_TOPLEVELS = {}


def toplevel(path):
    """The checkout a path is in, or None. Cached by folder: one command can name many paths in the same folders."""
    d = existing_dir(path)
    if not d:
        return None
    if d not in _TOPLEVELS:
        _TOPLEVELS[d] = git(["rev-parse", "--show-toplevel"], d)
    return _TOPLEVELS[d]


def find_clone(name, branch):
    """The local checkout for a repo named in a PR command: the worktree on that branch if there is one."""
    for root in CLONE_ROOTS:
        clone = os.path.join(os.path.expanduser(root), name)
        if not os.path.exists(os.path.join(clone, ".git")):
            continue
        if branch:
            listing = git(["worktree", "list", "--porcelain"], clone) or ""
            path = None
            for line in listing.splitlines():
                if line.startswith("worktree "):
                    path = line[len("worktree "):]
                elif line == f"branch refs/heads/{branch}" and path:
                    return path
    # No worktree on that branch (or no branch named, e.g. a description PATCH): the call changes no checkout,
    # so there is nothing to gate. Never fall back to the main checkout, whose branch is unrelated.
    return None


ASSIGN = re.compile(r"(?:^|[;&|\s(])([A-Za-z_]\w*)=(\"[^\"]*\"|'[^']*'|[^\s;&|)]+)")


def expand_vars(cmd):
    """Substitute simple NAME=value assignments made earlier in the same command (W=/path; git -C $W ...)."""
    for name, value in ASSIGN.findall(cmd):
        value = value.strip("\"'")
        if "$" in value or "`" in value:
            continue
        cmd = re.sub(r"\$\{?" + name + r"\b\}?", lambda _m: value, cmd)
    return cmd


def candidate_paths(tool, ti, cwd):
    if tool in ("Edit", "Write"):
        return [ti.get("file_path")] if ti.get("file_path") else []
    if tool == "NotebookEdit":
        return [ti.get("notebook_path")] if ti.get("notebook_path") else []
    if tool != "Bash":
        return []
    cmd = expand_vars(ti.get("command") or "")
    paths = []
    if REPO_IN_PR_CMD.search(cmd) and PR_TOOL.search(cmd):
        m = SOURCE_BRANCH.search(cmd)
        for name in REPO_IN_PR_CMD.findall(cmd):
            clone = find_clone(name, m.group(1) if m else None)
            if clone:
                paths.append(clone)
    # The folders each simple command would write to (same reading as the main checkout guard).
    here = cwd
    budget = [MAX_IMPORTS_PER_COMMAND, MAX_SCRIPTS_PER_COMMAND]
    for tokens in shell_segments(cmd):
        if tokens and tokens[0] == "cd":
            if len(tokens) > 1 and "$" not in tokens[1]:
                here = resolve(tokens[1], here)
            continue
        found = segment_targets(tokens, here) + run_targets(tokens, here, budget)
        paths += [t for t in found if "$" not in t and not t.startswith(SCRATCH_PREFIXES)]
    return list(dict.fromkeys(paths))


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def describe(path):
    text = read_text(path) or ""
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    if m:
        return m.group(1).strip().strip("\"'")[:140]
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("---"):
            return line[:140]
    return ""


def rule_files(top):
    """Absolute paths of the checkout's rule files that exist, plus the .md files CLAUDE.md imports."""
    files = []
    for rel in RULE_FILES:
        p = os.path.join(top, rel)
        if os.path.isfile(p):
            files.append(p)
            for imp in re.findall(r"(?:^|\s)@([\w./-]+\.md)\b", read_text(p) or ""):
                ip = os.path.normpath(os.path.join(os.path.dirname(p), imp))
                if os.path.isfile(ip) and ip not in files:
                    files.append(ip)
    return files


def extras(top):
    rows = []
    for rel in LISTED_DIRS:
        d = os.path.join(top, rel)
        if not os.path.isdir(d):
            continue
        items = [f"  - {rel}/{n}: {describe(os.path.join(d, n))}" for n in sorted(os.listdir(d)) if n.endswith(".md")]
        if items:
            rows.append(f"{rel}/ (use the matching command or agent rather than improvising its steps):")
            rows.extend(items)
    skills_dir = os.path.join(top, ".claude", "skills")
    if os.path.isdir(skills_dir):
        skills = [f"  - .claude/skills/{n}: {describe(os.path.join(skills_dir, n, 'SKILL.md'))}"
                  for n in sorted(os.listdir(skills_dir)) if os.path.isfile(os.path.join(skills_dir, n, "SKILL.md"))]
        if skills:
            rows.append(".claude/skills/ (use the matching skill for its task rather than improvising its steps):")
            rows.extend(skills)
    nested = []
    for dirpath, dirnames, filenames in os.walk(top):
        depth = dirpath[len(top):].count(os.sep)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")] if depth < 3 else []
        if dirpath != top and "CLAUDE.md" in filenames:
            nested.append(os.path.relpath(os.path.join(dirpath, "CLAUDE.md"), top))
    if nested:
        rows.append("Nested CLAUDE.md files (read the one for any folder you change):")
        rows.extend(f"  - {p}" for p in sorted(nested))
    return rows


def transcripts(data):
    paths = []
    tp = data.get("transcript_path")
    agent = data.get("agent_id")
    if tp:
        if agent:
            sub = os.path.join(os.path.splitext(tp)[0], "subagents", f"agent-{agent}.jsonl")
            paths.append(sub)
        paths.append(tp)
    return [p for p in paths if os.path.isfile(p)]


COMPACT_BOUNDARY = '"subtype":"compact_boundary"'
READ_CONTENTS = re.compile(r"Contents of (/[^\s\"\\]+)")
READ_FILE_PATH = re.compile(r'"file_path":\s*"((?:[^"\\]|\\.)*)"')


def unread(files, data):
    """Rule files that do not yet appear in this session's (or subagent's) transcript."""
    texts = []
    for p in transcripts(data)[:1] if data.get("agent_id") else transcripts(data):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        # Compaction keeps writing to the same transcript, but drops earlier reads from Claude's context. Only reads
        # after the last compaction count (Claude Code re-loads the start folder's CLAUDE.md after it).
        b = text.rfind(COMPACT_BOUNDARY)
        texts.append(text[text.rfind("\n", 0, b) + 1:] if b >= 0 else text)
    blob = "\n".join(texts)
    # The session may have used a symlinked path (/tmp vs /private/tmp, ~/projects/x -> /Volumes/...), while the
    # rule files come from git's resolved toplevel. Compare resolved paths too.
    seen = set()
    for raw in set(READ_CONTENTS.findall(blob)):
        seen.add(raw.rstrip(":"))
    for raw in set(READ_FILE_PATH.findall(blob)):
        try:
            seen.add(json.loads(f'"{raw}"'))
        except ValueError:
            pass
    seen_real = set()
    for s in seen:
        try:
            seen_real.add(os.path.realpath(s))
        except (OSError, ValueError):
            pass
    missing = []
    for f in files:
        j = json.dumps(f)[1:-1]
        if (f"Contents of {f}" in blob or f"Contents of {j}" in blob or f'"file_path":"{j}"' in blob
                or f'"file_path": "{j}"' in blob or os.path.realpath(f) in seen_real):
            continue
        missing.append(f)
    return missing


def header(top, reason="this session has not read that checkout's own rules yet"):
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], top) or "?"
    head = git(["rev-parse", "--short", "HEAD"], top) or "?"
    return (f"REPO RULES FIRST: you are about to change or run code in {top} (branch {branch}, {head}). "
            f"The call was paused because {reason}. ")


TAIL = ("Follow them. Where they conflict with the user's instructions or memory, the user's instructions win; tell "
        "the user about the conflict so the repo can be fixed. Then retry the same call.")


# ---- Main checkout guard -------------------------------------------------------------------------------------------
# The rule itself lives in the user's global CLAUDE.md ("# Never change a main checkout"); this code only finds the
# change and quotes that section. If the section is removed, the guard switches itself off.
GLOBAL_RULES = os.path.expanduser("~/.claude/CLAUDE.md")
MAIN_RULE_HEADING = "# Never change a main checkout"
SCRATCH_PREFIXES = ("/private/tmp/", "/tmp/", "/private/var/", "/var/folders/")
# Checkouts that hold tools, not work: Claude Code's own folder (plugin marketplaces are git clones with contributor
# rules of their own) and Homebrew, whose prefix is a git checkout that every brew-installed binary resolves into.
TOOL_CHECKOUTS = tuple(os.path.realpath(os.path.expanduser(p)) for p in
                       ("~/.claude", "/opt/homebrew", "/usr/local/Homebrew", "/home/linuxbrew/.linuxbrew"))


def is_tool_checkout(top):
    return any(top == t or top.startswith(t + os.sep) for t in TOOL_CHECKOUTS)
GIT_MUTATING = {"commit", "push", "merge", "cherry-pick", "rebase", "reset", "revert", "am", "apply", "checkout",
                "switch", "restore", "rm", "mv", "add", "stash", "tag", "pull", "clean"}
FILE_WRITERS = {"rm", "touch", "mkdir", "tee", "truncate", "patch", "rmdir", "chmod"}
LAST_ARG_WRITERS = {"cp", "mv", "ln", "rsync", "install"}
PKG_TOOLS = re.compile(r"^(?:dotnet\s+(?:add|remove|new|format|build|restore|test|publish|run)|npm\s+(?:install|i|ci|"
                       r"uninstall|version|run)(?=\s|$)|pnpm\s+\S+|yarn\s*\S*|poetry\s+\S+|make\b)")
# More commands that run a checkout's code. For the rules check only: the main checkout guard keeps PKG_TOOLS.
RUN_TOOLS = re.compile(r"^(?:npm\s+(?:run-script|test|t|start|exec|x)(?=\s|$)|npx\s+\S+)")
# Flags that point a package tool at another folder: `npm --prefix <dir> test`, `pnpm --dir <dir> build`, ...
DIR_FLAGS = {"npm": ("--prefix", "-C"), "npx": ("--prefix",), "pnpm": ("--dir", "-C"), "yarn": ("--cwd",),
             "make": ("-C", "--directory")}


def main_rule_text():
    text = read_text(GLOBAL_RULES) or ""
    i = text.find(MAIN_RULE_HEADING)
    if i < 0:
        return None
    rest = text[i + len(MAIN_RULE_HEADING):]
    ends = [k for k in (rest.find("\n# "), rest.find("\n<!--")) if k >= 0]
    return (MAIN_RULE_HEADING + (rest[:min(ends)] if ends else rest)).strip()


def is_main_checkout(top):
    if not top or top.startswith(SCRATCH_PREFIXES):
        return False
    gd = git(["rev-parse", "--absolute-git-dir"], top)
    common = git(["rev-parse", "--git-common-dir"], top)
    if not gd or not common:
        return False
    common = os.path.normpath(common if os.path.isabs(common) else os.path.join(top, common))
    return os.path.normpath(gd) == common


def git_segment_allowed_in_main(args):
    """git subcommands the global rule allows in a main checkout, given the args after `git [-C dir]`."""
    if not args:
        return True
    sub, rest = args[0], args[1:]
    if sub not in GIT_MUTATING:
        return True  # fetch, worktree, status, log, branch -d, ... do not change the checkout's files
    if sub == "checkout" and "--detach" in rest and any(a.startswith("origin/") for a in rest):
        return True
    if sub == "merge" and "--ff-only" in rest and any(a.startswith("origin/") for a in rest):
        return True
    if sub == "push" and ("--delete" in rest or "-d" in rest or any(a.startswith(":") for a in rest)):
        return True  # deleting a merged remote branch
    if sub == "stash" and (not rest or rest[0] in ("list", "show")):
        return True
    return False


def resolve(path, here):
    path = os.path.expanduser(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(here or "/", path))


def segment_targets(tokens, here):
    """Folders a single shell segment would change, or [] if it changes nothing."""
    if not tokens:
        return []
    targets = []
    words = []
    skip = False
    for i, tok in enumerate(tokens):
        if skip:
            skip = False
            continue
        if tok in (">", ">>", ">|"):
            dest = tokens[i + 1] if i + 1 < len(tokens) else ""
            if dest and dest != "/dev/null":
                targets.append(resolve(dest, here))
            skip = True
            continue
        if tok in ("<", "<<", "<<<", "<<HEREDOC"):
            skip = tok != "<<HEREDOC"
            continue
        words.append(tok)
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        words = words[1:]
    if not words:
        return targets
    cmd, args = words[0], words[1:]
    if cmd == "git":
        where = here
        while args and args[0] in ("-C", "-c"):
            if args[0] == "-C" and len(args) > 1:
                where = resolve(args[1], here)
            args = args[2:]
        if not git_segment_allowed_in_main(args):
            targets.append(where)
        return targets
    plain = [a for a in args if not a.startswith("-")]
    if cmd == "sed" and any(a.startswith("-i") for a in args):
        targets += [resolve(a, here) for a in plain[1:]]
    elif cmd in FILE_WRITERS:
        targets += [resolve(a, here) for a in plain] or [here]
    elif cmd in LAST_ARG_WRITERS and plain:
        targets.append(resolve(plain[-1], here))
    elif PKG_TOOLS.match(" ".join([cmd] + without_dir_flags(cmd, args))):
        targets.append(pkg_dir(cmd, args, here))
    return targets


def without_dir_flags(cmd, args):
    """args without the folder flag and its value, so `npm --prefix <dir> test` reads as `npm test`."""
    flags = DIR_FLAGS.get(cmd, ())
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in flags:
            skip = True
        elif not any(attached_value(a, f) for f in flags):
            out.append(a)
    return out


def pkg_dir(cmd, args, here):
    """The folder a package tool works in: the one a --prefix/--dir/--cwd/-C flag names, else the current one."""
    flags = DIR_FLAGS.get(cmd, ())
    for i, a in enumerate(args):
        for f in flags:
            if a == f and i + 1 < len(args):
                return resolve(args[i + 1], here)
            value = attached_value(a, f)
            if value:
                return resolve(value, here)
    return here


def attached_value(arg, flag):
    """The value of a flag written as one word: `--dir=<x>`, or `-C<x>` for a short flag."""
    if len(flag) > 2:
        return arg[len(flag) + 1:] if arg.startswith(flag + "=") else None
    return arg[len(flag):] if arg.startswith(flag) and len(arg) > len(flag) else None


# ---- Code a command runs ---------------------------------------------------------------------------------------------
# Running a script counts as working in the checkout it lives in, and in every checkout it imports code from by path.
# So do npm test/start/exec and npx, in the folder they work in.
# This is for the rules check only; the main checkout guard ignores it (running code reads a checkout, it doesn't
# change it).
RUNNERS = {"node", "tsx", "ts-node", "bun", "deno", "python", "python3", "bash", "sh", "zsh", "ruby", "perl"}
# Words that run the rest of the line as a command, and which of their flags take a value (`nice -n 10`).
WRAPPERS = {"env": {"-u", "--unset", "-C", "--chdir", "-S"}, "time": set(), "nice": {"-n"}, "exec": {"-a"},
            "timeout": {"-k", "--kill-after", "-s", "--signal"}, "command": set(), "npx": {"-p", "--package"},
            "bunx": {"-p", "--package"}}
TWO_WORD_WRAPPERS = {("npm", "exec"), ("npm", "x"), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "exec")}
# Package-tool wrappers are stripped only after npm's own run commands are matched (`npx` is one).
PKG_WRAPPERS = {"npx", "bunx"}
SHELL_KEYWORDS = {"do", "then", "else", "elif", "!", "{", "time"}
# Per runner: flags after which the code comes from the command line or a module name (`node -e`, `python -c`,
# `python -m`), not a script file; and flags whose value is not the script (`node --import ./reg.mjs`).
INLINE_CODE_FLAGS = {"node": {"-e", "--eval", "-p", "--print"}, "bun": {"-e", "--eval", "-p", "--print"},
                     "deno": {"eval"}, "python": {"-c", "-m"}, "bash": {"-c"}, "ruby": {"-e"}, "perl": {"-e", "-E"}}
VALUE_FLAGS = {"node": {"--import", "-r", "--require", "--loader", "--experimental-loader", "--env-file", "-C",
                        "--conditions", "--test-reporter", "--test-reporter-destination", "--test-name-pattern"},
               "python": {"-X", "-W", "-Q"}, "bash": {"-o", "-O", "+o", "+O"}, "ruby": {"-r", "-I"},
               "perl": {"-M", "-I"}}
RUNNER_FAMILY = {"python3": "python", "sh": "bash", "zsh": "bash", "tsx": "node", "ts-node": "node"}
SCRIPT_EXT = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx", ".jsx", ".py", ".sh", ".rb", ".pl")
JS_EXT = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx", ".jsx")
# `[ \t]*`, not `\s*`, before a line-start import: with re.M, `\s*` would also eat newlines and make a run of blank
# lines quadratic.
IMPORT_SPEC = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*|^[ \t]*import\s+)(['"])([./][^'"]*)\1""", re.M)
MAX_SCRIPT_BYTES = 1_000_000
# Per command, so one command can't make the hook slow: imports followed out of a script's own checkout, and scripts.
MAX_IMPORTS_PER_COMMAND = 300
MAX_SCRIPTS_PER_COMMAND = 200


def strip_wrappers(words, wrappers):
    """The words from the command actually run: past env assignments, shell keywords, the given wrappers with their
    flags (and those flags' values), `timeout`'s duration and `--`. Wrappers are matched by name, so
    /usr/bin/env counts as env."""
    while words:
        w = words[0]
        name = os.path.basename(w)
        if re.match(r"^[A-Za-z_]\w*=", w) or w == "--" or w in SHELL_KEYWORDS:
            words = words[1:]
        elif tuple(words[:2]) in TWO_WORD_WRAPPERS and "npm-exec" in wrappers:
            words = words[2:]
        elif name in wrappers:
            if name == "command" and len(words) > 1 and words[1] in ("-v", "-V"):
                return []  # `command -v x` only looks x up
            value_flags = WRAPPERS.get(name, set())
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "--":
                words = words[2:] if words[0] in value_flags else words[1:]
            if name == "timeout" and words and re.match(r"^\d", words[0]):
                words = words[1:]
        else:
            break
    return words


def script_args(runner, args):
    """The script files a runner is given: the first file argument, or every one when `--test` (node's test runner,
    which takes several) comes before it. Later arguments are the script's own input, not code."""
    family = RUNNER_FAMILY.get(runner, runner)
    inline, values = INLINE_CODE_FLAGS.get(family, set()), VALUE_FLAGS.get(family, set())
    files, many, skip = [], False, False
    for a in args:
        if skip:
            skip = False
            continue
        if a in inline or (family == "perl" and re.match(r"^-\w*[eE]\w*$", a)):
            break
        if a in values:
            skip = True
            continue
        if a == "--test" and not files:
            many = True
            continue
        if a.startswith("-") or a.startswith("+"):
            continue
        if a.endswith(SCRIPT_EXT):
            files.append(a)
            if not many:
                break
        else:
            break  # a bare word that isn't a script: a subcommand or the script's own input
    return files


def is_shebang_script(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def run_targets(tokens, here, budget):
    """Paths of the code a segment runs: the folder of an npm test/start/exec or npx, the scripts it runs and the code
    they import by path. [] if it runs none. `budget` is a two-item list for this command: imports and scripts
    still allowed."""
    words, skip = [], False
    for tok in tokens:  # leave out redirects and their targets (`> out.txt`, `<<HEREDOC`)
        if skip:
            skip = False
        elif tok in (">", ">>", ">|", "<", "<<", "<<<"):
            skip = True
        else:
            words.append(tok)
    words = strip_wrappers(words, {w for w in WRAPPERS if w not in PKG_WRAPPERS})
    paths = []
    if words and RUN_TOOLS.match(" ".join([words[0]] + without_dir_flags(words[0], words[1:]))):
        paths.append(pkg_dir(words[0], words[1:], here))
    words = strip_wrappers(words, set(WRAPPERS) | {"npm-exec"})
    if not words:
        return paths
    runner = os.path.basename(words[0])
    if runner in RUNNERS:
        scripts = script_args(runner, words[1:])
    elif "/" in words[0] and os.path.isfile(resolve(words[0], here)) and is_shebang_script(resolve(words[0], here)):
        scripts = [words[0]]  # a script run directly, through its shebang
    else:
        return paths
    for a in scripts:
        if budget[1] <= 0:
            break
        budget[1] -= 1
        f = os.path.realpath(resolve(a, here))
        if not os.path.isfile(f):
            continue
        paths.append(f)
        if f.endswith(JS_EXT):
            paths += imported_paths(f, budget)
    return paths


def imported_paths(script, budget):
    """Where a JS/TS script's relative or absolute imports point outside its own checkout (bare package names, and
    imports within the same checkout, are left out). Resolved from the script's real folder, as Node does."""
    if budget[0] <= 0:
        return []
    try:
        if os.path.getsize(script) > MAX_SCRIPT_BYTES:
            return []
    except OSError:
        return []
    base = os.path.dirname(script)
    own = toplevel(script)
    out = []
    for _q, spec in IMPORT_SPEC.findall(read_text(script) or ""):
        target = resolve(spec, base)
        if own and (target == own or target.startswith(own + os.sep)):
            continue
        if budget[0] <= 0:
            break
        budget[0] -= 1
        out.append(target)
    return out


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.S)
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;"}


def shell_segments(cmd):
    """Split a shell command into simple commands, respecting quotes. Heredoc bodies are dropped (they are data,
    such as a commit message, not commands) and fd redirects like 2>/dev/null or 2>&1 are normalised."""
    cmd = HEREDOC.sub("<<HEREDOC\n", cmd)
    cmd = re.sub(r"(?<![\w/])\d*>&\d+", " ", cmd)          # 2>&1, >&2
    cmd = re.sub(r"(?<![\w/])&>", " > ", cmd)              # &> file
    cmd = re.sub(r"(?<![\w/])\d+(>{1,2})", r" \1", cmd)    # 2> file -> > file
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segs, cur = [], []
    try:
        for tok in lex:
            if tok in OPERATORS:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(tok)
    except ValueError:
        return [cmd.split()]
    if cur:
        segs.append(cur)
    return segs


def main_checkout_violation(tool, ti, cwd):
    """The main checkout this call would change, if any."""
    if tool in ("Edit", "Write", "NotebookEdit"):
        top = toplevel(ti.get("file_path") or ti.get("notebook_path") or "")
        return top if is_main_checkout(top) else None
    if tool != "Bash":
        return None
    cmd = expand_vars(ti.get("command") or "")
    here = cwd
    for tokens in shell_segments(cmd):
        if tokens and tokens[0] == "cd":
            if len(tokens) > 1 and "$" not in tokens[1]:
                here = resolve(tokens[1], here)
            continue
        for t in segment_targets(tokens, here):
            if "$" in t:
                continue
            top = toplevel(t)
            if is_main_checkout(top):
                return top
    return None


def prune_old_state():
    """Delete per-session state folders nobody has touched in STATE_MAX_AGE_DAYS."""
    cutoff = time.time() - STATE_MAX_AGE_DAYS * 86400
    try:
        names = os.listdir(STATE)
    except OSError:
        return
    for name in names:
        path = os.path.join(STATE, name)
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                subprocess.run(["rm", "-rf", path], timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def main():
    data = json.load(sys.stdin)
    session = re.sub(r"[^\w.-]", "_", data.get("session_id") or "unknown")
    if "--reset" in sys.argv:
        subprocess.run(["rm", "-rf", os.path.join(STATE, session)], timeout=5)
        prune_old_state()
        return
    rule = main_rule_text()
    if rule:
        main_top = main_checkout_violation(data.get("tool_name") or "", data.get("tool_input") or {},
                                           data.get("cwd") or "")
        if main_top:
            name = os.path.basename(main_top)
            msg = (f"MAIN CHECKOUT: this call would change {main_top}, which is a main checkout, not a worktree. "
                   "Refused by the user's own rule (from ~/.claude/CLAUDE.md, quoted below). Make the change in a "
                   f"worktree instead, e.g. git -C {main_top} fetch origin && git -C {main_top} worktree add "
                   f"../{name}-<name> origin/<target branch> -b <type>/<name>, then edit and commit there.\n\n{rule}")
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": msg},
                "systemMessage": f"Main checkout guard: refused a change in {name}'s main checkout.",
            }))
            return
    tops = []
    for p in candidate_paths(data.get("tool_name") or "", data.get("tool_input") or {}, data.get("cwd") or ""):
        top = toplevel(p)
        if top and top not in tops and not is_tool_checkout(os.path.realpath(top)):
            tops.append(top)
    if not tops:
        return
    mdir = os.path.join(STATE, session, re.sub(r"[^\w.-]", "_", data.get("agent_id") or "main"))
    os.makedirs(mdir, exist_ok=True)
    os.utime(os.path.join(STATE, session))  # mark the session as active, so pruning leaves it alone
    for top in tops:
        key = os.path.join(mdir, hashlib.sha1(top.encode()).hexdigest())
        if os.path.exists(key + ".ok"):
            continue
        files = rule_files(top)
        rows = extras(top)
        if not files and not rows:
            open(key + ".ok", "w").close()
            continue
        missing = unread(files, data)
        denials = int(read_text(key + ".n") or "0")
        if not missing:  # rule files already read (before the first change, or after an earlier block)
            open(key + ".ok", "w").close()
            if not rows or denials > 0:
                continue
            # Its rule files are read, but it also has commands, agents, rule docs or nested CLAUDE.md files that
            # nothing has shown yet. Pause once to list them.
            done = "Its rule files are already read." if files else "It has no CLAUDE.md of its own."
            msg = (header(top, "this repo has more than its main rule files") + done
                   + "\n\nAlso in this repo:\n" + "\n".join(rows) + "\n\n" + TAIL)
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": msg},
                "systemMessage": f"Repo rules first: listed {os.path.basename(top)}'s commands and rule docs.",
            }))
            return
        if denials >= MAX_DENIALS:
            open(key + ".ok", "w").close()
            with open(os.path.join(STATE, "errors.log"), "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} gave up after {denials} blocks: {top} "
                         f"unread={missing}\n")
            continue
        with open(key + ".n", "w") as fh:
            fh.write(str(denials + 1))
        total = sum(os.path.getsize(f) for f in files)
        listing = ("\n\nAlso in this repo:\n" + "\n".join(rows)) if rows else ""
        if files and total <= INLINE_LIMIT and denials == 0:
            body = "".join(f"\n\n===== {os.path.relpath(f, top)} (full text) =====\n{read_text(f).strip()}"
                           for f in files)
            msg = header(top) + "Its rules are below." + body + listing + "\n\n" + TAIL
            open(key + ".ok", "w").close()
        else:
            need = missing if denials else files
            lines = "\n".join(f"  - {f} ({os.path.getsize(f) // 1000} KB)" for f in need)
            msg = (header(top) + "Read each of these files with the Read tool first (all of it; use offset/limit "
                   "for long ones):\n" + lines + listing + "\n\n" + TAIL)
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": msg},
            "systemMessage": f"Repo rules first: {os.path.basename(top)} rules required before working in it.",
        }))
        return


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail open
        try:
            os.makedirs(STATE, exist_ok=True)
            with open(os.path.join(STATE, "errors.log"), "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")
        except OSError:
            pass
    sys.exit(0)
