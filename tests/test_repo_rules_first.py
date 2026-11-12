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
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "repo-rules-first.py")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class RepoRulesFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="rrf-test-", dir=HERE))
        self.scratch = os.path.realpath(tempfile.mkdtemp(prefix="rrf-scratch-", dir="/tmp"))
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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.scratch, ignore_errors=True)

    def mark_read(self, *files):
        with open(self.transcript, "a") as fh:
            for f in files:
                fh.write(json.dumps({"text": f"Contents of {f} (project instructions)"}) + "\n")

    def run_hook(self, command, cwd=None, tool="Bash", session=None):
        data = {"session_id": session or uuid.uuid4().hex, "transcript_path": self.transcript, "cwd": cwd or self.a,
                "tool_name": tool, "tool_input": {"command": command} if tool == "Bash" else command}
        env = {**os.environ, "HOME": self.home}
        out = subprocess.run([sys.executable, HOOK], input=json.dumps(data), capture_output=True, text=True,
                             env=env, timeout=30).stdout.strip()
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
        for cmd in ("npm --prefix ../repo-b test", "npm --prefix ../repo-b run build", "pnpm --dir=../repo-b build", "pnpm -C ../repo-b test",
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

    # --- the main checkout guard ---

    def test_main_checkout_guard_refuses_builds_there_but_not_running_its_code(self):
        write(os.path.join(self.home, ".claude", "CLAUDE.md"), "# Never change a main checkout\nWork in a worktree.\n")
        self.mark_read(os.path.join(self.a, "CLAUDE.md"), os.path.join(self.b, "CLAUDE.md"))
        session = uuid.uuid4().hex
        # Run from a folder that isn't a checkout, so only repo B's main checkout can be the one refused.
        outside = os.path.join(self.tmp, "home")
        r = self.run_hook(f"pnpm --dir {self.b} build", cwd=outside, session=session)
        self.assertIsNotNone(r)
        self.assertTrue(r.startswith("MAIN CHECKOUT"), "a build in a main checkout is refused, as before")
        self.assertIn(self.b, r, "in the folder --dir names, not the current one")
        r = self.run_hook(f"npx tsx {self.b}/scripts/run.ts", cwd=outside, session=session)
        self.assertTrue(r is None or not r.startswith("MAIN CHECKOUT"), "running its code only reads it")
        r = self.run_hook(f"npm --prefix {self.b} test", cwd=outside, session=session)
        self.assertTrue(r is None or not r.startswith("MAIN CHECKOUT"), "npm test isn't refused there, as before")


if __name__ == "__main__":
    unittest.main(verbosity=2)
