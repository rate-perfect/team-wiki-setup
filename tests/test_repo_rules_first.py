#!/usr/bin/env python3
"""Tests for hooks/repo-rules-first.py. Run: python3 tests/test_repo_rules_first.py

Each test builds throwaway repos in a temp folder and runs the hook as Claude Code does: the PreToolUse JSON on
stdin, a decision (or nothing) on stdout. HOME points at the temp folder, so the hook's state and the global
CLAUDE.md it reads are the test's own.

The repos sit next to this file, not under /tmp: the hook ignores scratch folders (/tmp, /var/folders) on purpose.
A loose script outside any repo does go under /tmp, as a real scratch script would.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "repo-rules-first.py")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def read(path):
    with open(path) as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class RepoRulesFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="rrf-test-", dir=HERE))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scratch = os.path.realpath(tempfile.mkdtemp(prefix="rrf-scratch-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        # Repo A: where the session works.
        self.a = os.path.join(self.tmp, "repo-a")
        write(os.path.join(self.a, "CLAUDE.md"), "# A rules\nRepo A's own rule.\n")
        write(os.path.join(self.a, "a.ts"), 'import { readFileSync } from "node:fs";\nreadFileSync;\n')
        # Repo B: the one the session reaches into.
        self.b = os.path.join(self.tmp, "repo-b")
        write(os.path.join(self.b, "CLAUDE.md"), "# B rules\nRepo B's own rule.\n")
        write(os.path.join(self.b, "src", "lib.ts"), "export const x = 1;\n")
        write(os.path.join(self.b, "scripts", "run.ts"), 'import { x } from "../src/lib";\nconsole.log(x);\n')
        write(os.path.join(self.b, ".claude", "skills", "ship", "SKILL.md"),
              "---\nname: ship\ndescription: Ship a release the way repo B does it\n---\nSteps.\n")
        for repo in (self.a, self.b):
            git("init", "-q", "-b", "main", cwd=repo)
            git("add", "-A", cwd=repo)
            git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init", cwd=repo)
        self.transcript = os.path.join(self.tmp, "transcript.jsonl")
        write(self.transcript, "")

    def mark_read(self, *files):
        with open(self.transcript, "a") as fh:
            for f in files:
                fh.write(json.dumps({"text": f"Contents of {f} (project instructions)"}) + "\n")

    def run_hook(self, command, cwd=None, tool="Bash", session=None):
        data = {"session_id": session or uuid.uuid4().hex, "transcript_path": self.transcript, "cwd": cwd or self.a,
                "tool_name": tool, "tool_input": {"command": command} if tool == "Bash" else command}
        env = {**os.environ, "HOME": self.home}
        # 15 s: Claude Code's timeout for this hook (install.sh).
        done = subprocess.run([sys.executable, HOOK], input=json.dumps(data), capture_output=True, text=True,
                              env=env, timeout=15)
        out = done.stdout.strip()
        # The hook fails open: a crash prints nothing, which would pass any "no pause" check. A crash inside main()
        # is logged with its exception's name; one while loading the module shows on stderr.
        self.assertEqual(done.stderr, "", "the hook failed to load")
        log = os.path.join(self.home, ".claude", "hooks", "state", "repo-rules-first", "errors.log")
        if os.path.exists(log):
            # The log holds crashes and "gave up after N blocks" notes; anything else is a crash.
            crashes = [line for line in read(log).splitlines() if line.strip() and " gave up after " not in line]
            self.assertEqual(crashes, [], "the hook crashed")
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None

    def assert_paused_for(self, reason, repo, what):
        self.assertIsNotNone(reason, f"{what}: should pause")
        self.assertIn(f"run code in {repo}", reason, what)

    # --- running code in repo B from repo A counts as working in repo B ---

    def test_script_in_other_repo(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        r = self.run_hook("npx tsx ../repo-b/scripts/run.ts")
        self.assert_paused_for(r, self.b, "a script that lives in repo B")
        self.assertIn("Repo B's own rule.", r)

    def test_loose_script_importing_other_repo(self):
        script = os.path.join(self.scratch, "probe.ts")
        rel = os.path.relpath(os.path.join(self.b, "src", "lib"), self.scratch)
        write(script, f'import {{ x }} from "{rel}";\nconsole.log(x);\n')
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        self.assert_paused_for(self.run_hook(f"node --import tsx {script}"), self.b, "a scratch script importing repo B")
        abs_script = os.path.join(self.scratch, "probe2.mjs")
        write(abs_script, f'const m = await import("{self.b}/src/lib.ts");\n')
        self.assert_paused_for(self.run_hook(f"node {abs_script}"), self.b, "a dynamic import by absolute path")

    def test_builds_pointed_at_other_repo(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        for cmd in ("npm --prefix ../repo-b test", "npm --prefix ../repo-b run build", "pnpm --dir=../repo-b build",
                    "npm run build --prefix ../repo-b", "npm i -D x --prefix ../repo-b", "pnpm add x --dir ../repo-b",
                    "yarn add x --cwd ../repo-b", "pnpm --filter web --dir ../repo-b build",
                    "pnpm -F web -C ../repo-b test", "pnpm test --dir ../repo-b",
                    "yarn --network-timeout 100000 --cwd ../repo-b install",
                    "pnpm --filter web --dir ../repo-b exec tsx scripts/run.ts", "pnpm -C ../repo-b test",
                    "yarn --cwd ../repo-b test", "make -C ../repo-b", "cd ../repo-b && npm test",
                    "cd ../repo-b && npx tsc --noEmit"):
            self.assert_paused_for(self.run_hook(cmd), self.b, cmd)

    # --- what doesn't count ---

    def test_reading_and_text_dont_count(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        for cmd in ("cat ../repo-b/src/lib.ts", "grep -r x ../repo-b",
                    "python3 - <<'EOF'\nprint('npm --prefix ../repo-b test; npx tsx ../repo-b/scripts/run.ts')\nEOF",
                    "git commit --allow-empty -m 'ran npm --prefix ../repo-b test'"):
            r = self.run_hook(cmd)
            self.assertTrue(r is None or self.b not in r, f"{cmd!r} only reads repo B or mentions it in text")

    def test_script_with_package_imports_stays_in_its_own_repo(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        self.assertIsNone(self.run_hook("npx tsx a.ts"), "repo A's rules are read; bare package imports are no repo")

    def test_rules_read_once_then_skills_listed_once(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"), os.path.join(self.b, "CLAUDE.md"))
        session = uuid.uuid4().hex
        r = self.run_hook("npx tsx ../repo-b/scripts/run.ts", session=session)
        self.assertIsNotNone(r, "repo B's skills haven't been shown yet")
        self.assertIn(".claude/skills/ship: Ship a release the way repo B does it", r)
        self.assertIsNone(self.run_hook("npx tsx ../repo-b/scripts/run.ts", session=session), "only once")

    def test_wrapped_and_direct_runs(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        write(os.path.join(self.b, "scripts", "tool.sh"), "#!/bin/sh\necho hi\n")
        os.chmod(os.path.join(self.b, "scripts", "tool.sh"), 0o755)
        for cmd in ("npx -y tsx ../repo-b/scripts/run.ts", "npm exec -- tsx ../repo-b/scripts/run.ts",
                    "env FOO=1 node ../repo-b/scripts/run.ts", "timeout 5 node ../repo-b/scripts/run.ts",
                    "pnpm dlx tsx ../repo-b/scripts/run.ts", "../repo-b/scripts/tool.sh", "npm -C ../repo-b test",
                    "make -C../repo-b", "nice -n 10 node ../repo-b/scripts/run.ts",
                    "env -u HOME node ../repo-b/scripts/run.ts", "timeout -k 5 10 node ../repo-b/scripts/run.ts",
                    "/usr/bin/env node ../repo-b/scripts/run.ts", "for f in 1; do node ../repo-b/scripts/run.ts; done",
                    "bash -e ../repo-b/scripts/tool.sh", "node --import ./a.ts ../repo-b/scripts/run.ts",
                    "env CI=1 npm --prefix ../repo-b test", "cd ../repo-b && time npm test",
                    "cd ../repo-b && timeout 600 npm test", "cd ../repo-b && timeout 600 npm run test",
                    "cd ../repo-b && time pnpm build", "env CI=1 pnpm --dir ../repo-b build",
                    "timeout 300 make -C ../repo-b", "if node ../repo-b/scripts/run.ts; then :; fi",
                    "until node ../repo-b/scripts/run.ts; do :; done", "npm exec -y tsx ../repo-b/scripts/run.ts",
                    "pnpm exec --silent tsx ../repo-b/scripts/run.ts", "while node ../repo-b/scripts/run.ts; do :; done",
                    "time -p node ../repo-b/scripts/run.ts", "npm exec -p tsx -- tsx ../repo-b/scripts/run.ts",
                    "bash -euo pipefail ../repo-b/scripts/tool.sh", "npm --prefix ../repo-b -w pkg run build",
                    "npm --prefix ../repo-b --silent test", "cd ../repo-b && npm cit",
                    "env -C ../repo-b node scripts/run.ts", "{ cd ../repo-b && npm test; }",
                    "env --chdir=../repo-b node scripts/run.ts", "env -C../repo-b node scripts/run.ts",
                    "env -u FOO -C ../repo-b node scripts/run.ts", "pnpm -C ../repo-b exec tsx scripts/run.ts",
                    "cd ../repo-b && npm restart", "npm --prefix ../repo-b --omit dev ci",
                    "timeout 60 env -C ../repo-b npm test", "nice env -C ../repo-b node scripts/run.ts",
                    "env -iC ../repo-b node scripts/run.ts", "env -uC node ../repo-b/scripts/run.ts",
                    "npx --package tsx --prefix ../repo-b tsx scripts/run.ts", "env -iC../repo-b node scripts/run.ts",
                    "env -P /usr/bin node ../repo-b/scripts/run.ts"):
            self.assert_paused_for(self.run_hook(cmd), self.b, cmd)

    def test_a_scripts_own_input_isnt_code(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        write(os.path.join(self.b, "data.py"), "x = 1\n")
        write(os.path.join(self.b, "scripts", "lookup.sh"), "#!/bin/sh\necho hi\n")
        write(os.path.join(self.b, "x.sh"), "echo hi\n")
        write(os.path.join(self.a, "a.ts"), "console.log(1);\n")
        for cmd in ("npx tsx a.ts ../repo-b/src/lib.ts", "node -e 'require(1)' ../repo-b/scripts/run.ts",
                    "python3 -m json.tool ../repo-b/data.py", "command -v ../repo-b/scripts/lookup.sh",
                    "python3 -mjson.tool ../repo-b/data.py", "python3 - ../repo-b/data.py", "bash -s ../repo-b/x.sh",
                    "node a.ts --test ../repo-b/scripts/run.ts", "node a.ts build ../repo-b/scripts/run.ts",
                    "node build ../repo-b/scripts/run.ts", "npm run build -- --prefix ../repo-b",
                    "pnpm run build --dir ../repo-b", "node a.ts env -C ../repo-b",
                    "npx prettier --prefix ../repo-b --check x"):
            self.assertIsNone(self.run_hook(cmd), f"{cmd!r}: repo B's file is only input")

    def test_nothing_from_outside_any_checkout(self):
        for cmd in ("npx prettier --check x.ts", "npm token list", "node tool.js"):
            self.assertIsNone(self.run_hook(cmd, cwd=self.scratch), cmd)
        # npm commands that run nothing don't count, even inside a checkout whose rules aren't read.
        for cmd in ("npm token list", "npm team ls", "npm view left-pad version", "npm info left-pad"):
            self.assertIsNone(self.run_hook(cmd, cwd=self.b), cmd)

    def test_a_runner_given_by_path_is_not_the_code_run(self):
        # Like Homebrew: a toolchain that is itself a checkout with rules, whose node and python are run by path.
        tools = os.path.join(self.tmp, "toolchain")
        write(os.path.join(tools, "CLAUDE.md"), "# Toolchain contributor rules\n")
        for name in ("node", "python3"):
            write(os.path.join(tools, "bin", name), "#!/bin/sh\n")
            os.chmod(os.path.join(tools, "bin", name), 0o755)
        git("init", "-q", cwd=tools)
        self.assertIsNone(self.run_hook(f"{tools}/bin/python3 -V", cwd=self.scratch))
        r = self.run_hook(f"{tools}/bin/node {self.b}/scripts/run.ts", cwd=self.scratch)
        self.assert_paused_for(r, self.b, "the script, not the runner")
        self.assertNotIn("Toolchain", r)

    @unittest.skipUnless(os.path.isdir("/opt/homebrew/.git") and os.path.isfile("/opt/homebrew/bin/brew"),
                         "needs Homebrew installed at /opt/homebrew (a git checkout)")
    def test_homebrew_is_a_tool_not_a_work_repo(self):
        self.assertIsNone(self.run_hook("/opt/homebrew/bin/brew --version", cwd=self.scratch))

    def test_installed_tools_run_for_the_current_folder(self):
        # A worktree whose node_modules and .venv link to its main checkout's: their tools run for the worktree.
        for rel in ("node_modules/.bin/vitest", "node_modules/vitest/vitest.mjs", ".venv/bin/pytest"):
            write(os.path.join(self.a, rel), "#!/usr/bin/env node\n")
        wt = os.path.join(self.tmp, "repo-a-wt")
        git("worktree", "add", "-q", "-b", "wt", wt, cwd=self.a)
        os.symlink(os.path.join(self.a, "node_modules"), os.path.join(wt, "node_modules"))
        os.symlink(os.path.join(self.a, ".venv"), os.path.join(wt, ".venv"))
        self.mark_read(os.path.join(wt, "CLAUDE.md"))
        for cmd in ("./node_modules/.bin/vitest run", "node node_modules/vitest/vitest.mjs", ".venv/bin/pytest -q"):
            self.assertIsNone(self.run_hook(cmd, cwd=wt), f"{cmd}: not the main checkout's code")
        # Another repo's installed tool is a dependency, not that repo's code: it runs for the current folder.
        write(os.path.join(self.b, "node_modules", ".bin", "prettier"), "#!/usr/bin/env node\n")
        write(os.path.join(self.b, "node_modules", "prettier", "bin.js"), "1;\n")
        for cmd in ("../repo-b/node_modules/.bin/prettier --check src", "node ../repo-b/node_modules/prettier/bin.js"):
            self.assertIsNone(self.run_hook(cmd, cwd=wt), cmd)
            # From a checkout whose rules aren't read, it pauses for that checkout, not for repo B.
            r = self.run_hook(cmd, cwd=self.a)
            self.assertIn(f"run code in {self.a}", r or "", cmd)
        # A dependency linked from another repo's source (`npm link`) is that repo's code.
        os.symlink(self.b, os.path.join(self.a, "node_modules", "b"))
        self.assert_paused_for(self.run_hook("node node_modules/b/scripts/run.ts", cwd=wt), self.b, "npm link")

    def test_a_file_run_by_path_counts_only_as_a_script(self):
        tools = os.path.join(self.tmp, "binaries")
        write(os.path.join(tools, "CLAUDE.md"), "# Binaries repo rules\n")
        write(os.path.join(tools, "python3.12"), "\x7fELF not a script\n")
        git("init", "-q", cwd=tools)
        self.assertIsNone(self.run_hook(f"{tools}/python3.12 -V", cwd=self.scratch), "a binary, not a #! script")

    def test_imports_follow_the_real_path_and_stop_at_the_cap(self):
        # A script in repo B, reached through a symlink, imports repo A by a path that only works from its real folder.
        write(os.path.join(self.b, "scripts", "uses-a.ts"), 'import { y } from "../../repo-a/a";\n')
        os.makedirs(os.path.join(self.scratch, "x"))
        os.symlink(os.path.join(self.b, "scripts"), os.path.join(self.scratch, "x", "link"))
        self.mark_read(os.path.join(self.b, "CLAUDE.md"))
        session = uuid.uuid4().hex
        cmd = f"node {self.scratch}/x/link/uses-a.ts"
        self.run_hook(cmd, cwd=self.scratch, session=session)  # lists repo B's skills once
        self.assert_paused_for(self.run_hook(cmd, cwd=self.scratch, session=session), self.a, "the real path")
        # Imports inside the script's own checkout don't use up the cap: 400 within repo A, then one into repo B.
        inside = os.path.join(self.a, "many.ts")
        write(inside, "".join(f'import {{ m{i} }} from "./m{i}";\n' for i in range(400))
              + f'import {{ z }} from "{self.b}/src/lib";\n')
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        self.assert_paused_for(self.run_hook(f"node {inside}", cwd=self.scratch), self.b, "import 401, out of repo A")
        # Past the cap, imports aren't followed: 300 into repo A (read), then one into repo B (not read).
        cap = os.path.join(self.scratch, "cap.ts")
        lines = [f'import {{ m{i} }} from "{self.a}/d{i}/m";' for i in range(300)]
        for i in range(300):
            os.makedirs(os.path.join(self.a, f"d{i}"))
        lines.append(f'import {{ z }} from "{self.b}/src/lib";')
        write(cap, "\n".join(lines) + "\n")
        fresh = os.path.join(self.tmp, "fresh-transcript.jsonl")
        write(fresh, json.dumps({"text": f"Contents of {self.a}/CLAUDE.md"}) + "\n")
        self.transcript = fresh
        start = time.monotonic()
        self.assertIsNone(self.run_hook(f"node {cap}", cwd=self.scratch), "the 301st import isn't followed")
        self.assertLess(time.monotonic() - start, 5)

    def test_claude_code_folder_is_left_alone(self):
        plugin = os.path.join(self.home, ".claude", "plugins", "marketplaces", "p")
        write(os.path.join(plugin, "CLAUDE.md"), "# Plugin contributor rules\n")
        write(os.path.join(plugin, "run.js"), "console.log(1);\n")
        git("init", "-q", cwd=plugin)
        self.assertIsNone(self.run_hook(f"node {plugin}/run.js", cwd=self.scratch))

    def test_many_imports_and_long_files_stay_fast(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        files = []
        for i in range(20):
            f = os.path.join(self.a, "t", f"f{i}.test.ts")
            write(f, "".join(f'import {{ m{j} }} from "../lib/m{j}";\n' for j in range(200)))
            files.append(f)
        blank = os.path.join(self.a, "blank.ts")
        write(blank, "\n" * 900_000)
        start = time.monotonic()
        self.assertIsNone(self.run_hook("node --test " + " ".join(files)), "imports within repo A count only for A")
        self.assertIsNone(self.run_hook(f"npx tsx {blank}"))
        self.assertLess(time.monotonic() - start, 5, "well inside the hook's 15 s timeout")

    def test_script_cap(self):
        self.mark_read(os.path.join(self.a, "CLAUDE.md"))
        files = []
        for i in range(200):
            f = os.path.join(self.a, "t", f"s{i}.test.js")
            write(f, "1;\n")
            files.append(f)
        files.append(os.path.join(self.b, "scripts", "run.ts"))
        self.assertIsNone(self.run_hook("node --test " + " ".join(files)), "the 201st script isn't looked at")

    def test_scripts_run_through_a_folder_flag_and_repos_in_odd_folders(self):
        # `pnpm -C <repo> exec tsx <script>`: the script is found in <repo>, and its imports are followed.
        c = os.path.join(self.tmp, "repo-c")
        write(os.path.join(c, "CLAUDE.md"), "# C rules\n")
        write(os.path.join(c, "x.ts"), "export const x = 1;\n")
        git("init", "-q", cwd=c)
        write(os.path.join(self.b, "scripts", "uses-c.ts"), 'import { x } from "../../repo-c/x";\n')
        self.mark_read(os.path.join(self.a, "CLAUDE.md"), os.path.join(self.b, "CLAUDE.md"))
        session = uuid.uuid4().hex
        cmd = "pnpm -C ../repo-b exec tsx scripts/uses-c.ts"
        self.run_hook(cmd, session=session)  # lists repo B's skills once
        self.assert_paused_for(self.run_hook(cmd, session=session), c, "the imported repo")
        # Flags between pnpm and exec don't hide the script it runs.
        self.assert_paused_for(self.run_hook("pnpm --filter web exec tsx ../repo-c/x.ts"), c, "pnpm --filter web exec")
        # A flag after an implicit script: the script runs here, so this folder counts too.
        r = self.run_hook(f"cd ../repo-b && pnpm build --dir {self.scratch}", session=uuid.uuid4().hex)
        self.assertIn(f"run code in {self.b}", r or "")
        # A repo that sits in a folder named venv keeps its scripts: only folders below a checkout are dependencies.
        odd = os.path.join(self.tmp, "venv", "tools")
        write(os.path.join(odd, "CLAUDE.md"), "# Tools rules\n")
        write(os.path.join(odd, "run.py"), "print(1)\n")
        git("init", "-q", cwd=odd)
        self.assert_paused_for(self.run_hook("python3 ../venv/tools/run.py"), odd, "a repo under venv/")

    # --- the main checkout guard ---

    def test_main_checkout_guard_refuses_builds_there_but_not_running_its_code(self):
        write(os.path.join(self.home, ".claude", "CLAUDE.md"), "# Never change a main checkout\nWork in a worktree.\n")
        self.mark_read(os.path.join(self.a, "CLAUDE.md"), os.path.join(self.b, "CLAUDE.md"))
        session = uuid.uuid4().hex
        # Run from a scratch folder, which is no checkout, so repo B is the only one the hook can see.
        outside = self.scratch
        r = self.run_hook(f"pnpm --dir {self.b} build", cwd=outside, session=session)
        self.assertIsNotNone(r)
        self.assertTrue(r.startswith("MAIN CHECKOUT"), "a build in a main checkout is refused, as before")
        self.assertIn(self.b, r, "in the folder --dir names, not the current one")
        for cmd in (f"npm --prefix {self.b} install", f"npm --prefix {self.b} run-script build",
                    f"npm --prefix {self.b} cit", f"npm --prefix {self.b} --silent ci",
                    f"npm --prefix {self.b} isntall", f"npm --prefix {self.b} clean-install",
                    f"npm --prefix {self.b} init -y", f"pnpm --filter web --dir {self.b} build"):
            r = self.run_hook(cmd, cwd=outside, session=session)
            self.assertTrue(r and r.startswith("MAIN CHECKOUT"), f"{cmd}: refused there too")
        r = self.run_hook(f"cd {self.b} && npm ci", cwd=outside, session=session)
        self.assertTrue(r and r.startswith("MAIN CHECKOUT"), "a build after cd into it is refused")
        # The guard reads segments as main does: a build behind a shell keyword or a wrapper isn't refused at all.
        # The rules check still follows the `cd` into the worktree and asks for the worktree's rules.
        wt = os.path.join(self.tmp, "repo-b-wt")
        git("worktree", "add", "-q", "-b", "wt", wt, cwd=self.b)
        # pnpm and yarn ignore their own flags after an implicit script (`pnpm build --dir x` builds here), so a flag
        # after the subcommand doesn't take the guard's eye off the main checkout.
        for cmd in ("pnpm build --dir ../repo-b-wt", "yarn test --cwd ../repo-b-wt", "pnpm lint --fix -C ../repo-b-wt",
                    "yarn workspace web build --cwd ../repo-b-wt"):
            r = self.run_hook(cmd, cwd=self.b, session=uuid.uuid4().hex)
            self.assertTrue(r and r.startswith("MAIN CHECKOUT"), f"{cmd}: refused for the main checkout")
        # Repeated or attached folder flags: the tools use the last one, or ignore `-C<dir>`. The guard refuses all
        # that main refused, so they can't slip a build in the main checkout past it.
        for cmd in ("pnpm --dir ../repo-b-wt install --dir .", "pnpm -C ../repo-b-wt -C . install",
                    "yarn --cwd ../repo-b-wt add foo --cwd .", "pnpm -C../repo-b-wt build", "npm run build -C../repo-b-wt",
                    "npm run-scr build", "pnpm --dir ../repo-b-wt build", f"make -C ../repo-b-wt -C {self.b}",
                    f"npm ci --prefix ../repo-b-wt --prefix {self.b}"):
            r = self.run_hook(cmd, cwd=self.b, session=uuid.uuid4().hex)
            self.assertTrue(r and r.startswith("MAIN CHECKOUT"), f"{cmd}: refused for the main checkout")
        r = self.run_hook(f"make -C ../repo-b-wt -C {self.b}", cwd=wt, session=uuid.uuid4().hex)
        self.assertTrue(r and r.startswith("MAIN CHECKOUT"), "make's -C flags chain, even from the worktree")
        # npm reads a folder flag fully: pointed at a worktree, it works there.
        for cmd in ("if cd ../repo-b-wt; then pnpm install; fi", "env -C ../repo-b-wt npm ci",
                    "npm --prefix ../repo-b-wt ci"):
            r = self.run_hook(cmd, cwd=self.b, session=uuid.uuid4().hex)
            self.assertIsNotNone(r, cmd)
            self.assertIn(f"run code in {wt}", r, f"{cmd}: the worktree's rules, not a main checkout refusal")
        # Repo B's rules are read and its skills listed by the first pause below; after that nothing pauses.
        self.run_hook(f"npx tsx {self.b}/scripts/run.ts", cwd=outside, session=session)
        self.assertIsNone(self.run_hook(f"npx tsx {self.b}/scripts/run.ts", cwd=outside, session=session),
                          "running its code only reads it")
        self.assertIsNone(self.run_hook(f"npm --prefix {self.b} test", cwd=outside, session=session),
                          "npm test isn't refused there, as before")


if __name__ == "__main__":
    unittest.main(verbosity=2)
