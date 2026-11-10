#!/usr/bin/env python3
"""Tests for bin/worktree-cleanup. Run: python3 tests/test_worktree_cleanup.py

Each test builds throwaway repos and worktrees in a temp folder. Fake gh and lsof programs go first on PATH,
so nothing talks to GitHub and no real process is looked at.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "worktree-cleanup")

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
data = json.load(open(os.environ["FAKE_GH_FILE"]))
if args[:2] == ["repo", "view"]:
    print(json.dumps({"defaultBranchRef": {"name": data.get("__default__", "main")}}))
    sys.exit(0)
branch = args[args.index("--head") + 1]
answer = data.get(branch, [])
if answer == "fail":
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)
print(json.dumps(answer))
'''

FAKE_LSOF = r'''#!/usr/bin/env python3
import os, subprocess, sys
if os.environ.get("FAKE_LSOF_FAIL"):
    sys.exit(1)
# FAKE_LSOF_HOOK: a shell command run once, on the second call (the re-check just before the first remove).
counter = os.environ.get("FAKE_LSOF_COUNT")
if counter:
    n = int(open(counter).read() or 0) + 1 if os.path.exists(counter) else 1
    open(counter, "w").write(str(n))
    if n == 2 and os.environ.get("FAKE_LSOF_HOOK"):
        subprocess.run(os.environ["FAKE_LSOF_HOOK"], shell=True, check=True)
print("p1\ncinit\nn/")
for i, path in enumerate(filter(None, os.environ.get("FAKE_LSOF_CWDS", "").split(":"))):
    print(f"p{100 + i}\ncclaude\nn{path}")
'''


class Fixture:
    """A temp workspace: ws/widgets (GitHub remote) with sibling worktrees, ws/adorepo (Azure DevOps remote)."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="wt-cleanup-test-"))
        self.ws = os.path.join(self.tmp, "ws")
        self.bin = os.path.join(self.tmp, "fakebin")
        os.makedirs(self.ws)
        os.makedirs(self.bin)
        for name, body in (("gh", FAKE_GH), ("lsof", FAKE_LSOF)):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.gh_file = os.path.join(self.tmp, "gh.json")
        self.gh = {}
        self.env = dict(os.environ, HOME=self.tmp, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", PATH=self.bin + os.pathsep + os.environ["PATH"],
                        FAKE_GH_FILE=self.gh_file)
        self.env.pop("FAKE_LSOF_FAIL", None)
        self.env.pop("FAKE_LSOF_CWDS", None)
        self.main = self.make_repo("widgets", "https://github.com/acme/widgets.git")

    def git(self, cwd, *args):
        out = subprocess.run(["git", "-C", cwd, *args], env=self.env, check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def make_repo(self, name, url):
        path = os.path.join(self.ws, name)
        os.makedirs(path)
        self.git(path, "init", "-q", "-b", "main")
        with open(os.path.join(path, ".gitignore"), "w") as f:
            f.write("*.log\n*.tfvars\n.terraform/\nnode_modules/\n*.tsbuildinfo\nbuild/\n.claude/\n")
        with open(os.path.join(path, "a.txt"), "w") as f:
            f.write("a\n")
        self.git(path, "add", ".")
        self.git(path, "commit", "-q", "-m", "base")
        self.git(path, "remote", "add", "origin", url)
        return path

    def worktree(self, name, repo=None, commit=True):
        """Adds ../<repo>-<name> on branch feat/<name> with one commit. Returns (path, HEAD sha)."""
        repo = repo or self.main
        path = f"{repo}-{name}"
        self.git(repo, "worktree", "add", "-q", path, "-b", f"feat/{name}")
        if commit:
            with open(os.path.join(path, f"{name}.txt"), "w") as f:
                f.write(name + "\n")
            self.git(path, "add", ".")
            self.git(path, "commit", "-q", "-m", name)
        return path, self.git(path, "rev-parse", "HEAD")

    def prs(self, name, *prs):
        self.gh[f"feat/{name}"] = list(prs) if prs != ("fail",) else "fail"

    def run(self, *args, **env):
        with open(self.gh_file, "w") as f:
            json.dump(self.gh, f)
        p = subprocess.run([sys.executable, SCRIPT, *args], env=dict(self.env, **env),
                           capture_output=True, text=True)
        return p

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def merged(sha, number=1, base="main"):
    return {"number": number, "state": "MERGED", "headRefOid": sha, "baseRefName": base}


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def parse(output):
    """{real path: (verdict, reason line)} from the script's output."""
    result, lines = {}, output.splitlines()
    for i, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("KEEP", "REMOVE"):
            result[os.path.realpath(parts[1])] = (parts[0], lines[i + 1].strip())
    return result


class DryRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fx = cls.fx = Fixture()
        p = {}
        p["done"], sha = fx.worktree("done")
        fx.prs("done", merged(sha))
        p["two"], sha = fx.worktree("two")
        fx.prs("two", merged("0" * 40, 1), merged(sha, 2))
        p["locked"], sha = fx.worktree("locked")
        fx.prs("locked", merged(sha))
        fx.git(fx.main, "worktree", "lock", p["locked"])
        p["detached"] = f"{fx.main}-detached"
        fx.git(fx.main, "worktree", "add", "-q", "--detach", p["detached"])
        p["dirty"], sha = fx.worktree("dirty")
        fx.prs("dirty", merged(sha))
        with open(os.path.join(p["dirty"], "a.txt"), "a") as f:
            f.write("changed\n")
        p["untracked"], sha = fx.worktree("untracked")
        fx.prs("untracked", merged(sha))
        open(os.path.join(p["untracked"], "new.txt"), "w").close()
        p["ignored"], sha = fx.worktree("ignored")
        fx.prs("ignored", merged(sha))
        write(os.path.join(p["ignored"], "node_modules", "x", "index.js"))
        write(os.path.join(p["ignored"], "tsconfig.tsbuildinfo"))
        p["tfvars"], sha = fx.worktree("tfvars")
        fx.prs("tfvars", merged(sha))
        write(os.path.join(p["tfvars"], "infra", "azure", ".terraform", "providers.txt"))
        write(os.path.join(p["tfvars"], "infra", "azure", "terraform.tfvars"), "secret = 1\n")
        p["log"], sha = fx.worktree("log")
        fx.prs("log", merged(sha))
        write(os.path.join(p["log"], "notes.log"))
        # 1. nested worktrees and repos
        p["outer"], sha = fx.worktree("outer")
        fx.prs("outer", merged(sha))
        p["inner"] = os.path.join(p["outer"], ".claude", "worktrees", "agent-x")
        fx.git(fx.main, "worktree", "add", "-q", p["inner"], "-b", "feat/inner")
        write(os.path.join(p["inner"], "UNSAVED.txt"), "work\n")
        p["nestrepo"], sha = fx.worktree("nestrepo")
        fx.prs("nestrepo", merged(sha))
        fx.git(fx.tmp, "init", "-q", os.path.join(p["nestrepo"], "build", "vendor"))
        p["nestfile"], sha = fx.worktree("nestfile")
        fx.prs("nestfile", merged(sha))
        other = os.path.join(fx.tmp, "other")
        fx.git(fx.tmp, "init", "-q", "-b", "main", other)
        fx.git(other, "commit", "-q", "--allow-empty", "-m", "base")
        fx.git(other, "worktree", "add", "-q", os.path.join(p["nestfile"], "build", "otherwt"), "-b", "x")
        # 4. git settings that hide changes
        p["skipwt"], sha = fx.worktree("skipwt")
        fx.prs("skipwt", merged(sha))
        fx.git(p["skipwt"], "update-index", "--skip-worktree", "a.txt")
        write(os.path.join(p["skipwt"], "a.txt"), "local edit\n")
        p["assume"], sha = fx.worktree("assume")
        fx.prs("assume", merged(sha))
        fx.git(p["assume"], "update-index", "--assume-unchanged", "a.txt")
        write(os.path.join(p["assume"], "a.txt"), "local edit\n")
        # 6. a worktree outside the scanned folder
        p["outside"] = os.path.join(fx.tmp, "elsewhere", "widgets-outside")
        fx.git(fx.main, "worktree", "add", "-q", p["outside"], "-b", "feat/outside")
        fx.prs("outside", merged(fx.git(p["outside"], "rev-parse", "HEAD")))
        # 7. merged into a branch that is not the default
        p["base"], sha = fx.worktree("base")
        fx.prs("base", merged(sha, base="release"))
        p["open"], sha = fx.worktree("open")
        fx.prs("open", {"number": 3, "state": "OPEN", "headRefOid": sha})
        p["reopened"], sha = fx.worktree("reopened")
        fx.prs("reopened", merged(sha, 4), {"number": 5, "state": "OPEN", "headRefOid": sha})
        p["nopr"], _ = fx.worktree("nopr")
        p["closed"], sha = fx.worktree("closed")
        fx.prs("closed", {"number": 6, "state": "CLOSED", "headRefOid": sha})
        p["ahead"], sha = fx.worktree("ahead")
        fx.prs("ahead", merged(sha))
        with open(os.path.join(p["ahead"], "more.txt"), "w") as f:
            f.write("after the merge\n")
        fx.git(p["ahead"], "add", ".")
        fx.git(p["ahead"], "commit", "-q", "-m", "after merge")
        p["ghfail"], _ = fx.worktree("ghfail")
        fx.prs("ghfail", "fail")
        p["inuse"], sha = fx.worktree("inuse")
        fx.prs("inuse", merged(sha))
        os.makedirs(os.path.join(p["inuse"], "sub"))
        p["gone"], sha = fx.worktree("gone")
        fx.prs("gone", merged(sha))
        shutil.rmtree(p["gone"])
        ado = fx.make_repo("adorepo", "https://someone:SECRET123@dev.azure.com/org/proj/_git/adorepo")
        p["ado"], sha = fx.worktree("ado", repo=ado)
        fx.prs("ado", merged(sha))
        os.makedirs(os.path.join(fx.ws, "notes"))  # a plain folder: must be skipped quietly
        p["main"], p["adomain"] = fx.main, ado
        cls.paths = p
        cls.proc = fx.run("--parent", fx.ws, FAKE_LSOF_CWDS=os.path.join(p["inuse"], "sub"))
        cls.out = parse(cls.proc.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def check(self, name, want, reason=None):
        got, why = self.out[os.path.realpath(self.paths[name])]
        self.assertEqual(got, want, f"{name}: {why}\n{self.proc.stdout}")
        if reason:
            self.assertIn(reason, why)

    def test_exit_code(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_remove(self):
        self.check("done", "REMOVE")
        self.check("two", "REMOVE")      # an older merged PR, then the merged PR that matches HEAD
        self.check("ignored", "REMOVE")  # only rebuildable ignored files

    def test_keep_reasons(self):
        self.check("main", "KEEP", "main checkout")
        self.check("adomain", "KEEP", "main checkout")
        self.check("locked", "KEEP", "locked")
        self.check("detached", "KEEP", "detached HEAD")
        self.check("dirty", "KEEP", "uncommitted changes")
        self.check("untracked", "KEEP", "untracked files")
        self.check("open", "KEEP", "open PR #3")
        self.check("reopened", "KEEP", "open PR #5")
        self.check("nopr", "KEEP", "no PR for this branch")
        self.check("closed", "KEEP", "PR not merged")
        self.check("ahead", "KEEP", "HEAD is not the last commit")
        self.check("ghfail", "KEEP", "gh pr list failed")
        self.check("inuse", "KEEP", "in use by claude")
        self.check("gone", "KEEP", "folder is gone")
        self.check("ado", "KEEP", "not on GitHub (dev.azure.com)")
        self.check("tfvars", "KEEP", "ignored files that would be deleted: infra/azure/terraform.tfvars")
        self.check("log", "KEEP", "ignored files that would be deleted: notes.log")
        self.check("outer", "KEEP", "another worktree is inside it (.claude/worktrees/agent-x)")
        self.check("inner", "KEEP", "uncommitted changes")
        self.check("nestrepo", "KEEP", "a git repo or worktree is inside it (build/vendor)")
        self.check("nestfile", "KEEP", "a git repo or worktree is inside it (build/otherwt)")
        self.check("skipwt", "KEEP", "skip-worktree or assume-unchanged: a.txt")
        self.check("assume", "KEEP", "skip-worktree or assume-unchanged: a.txt")
        self.check("outside", "KEEP", "outside the scanned folders")
        self.check("base", "KEEP", "merged into release, not main")

    def test_each_repo_once_and_no_secret(self):
        self.assertEqual(self.proc.stdout.count("== "), 2, self.proc.stdout)  # widgets + adorepo, not notes
        self.assertIn("Total: 2 repos, 3 REMOVE,", self.proc.stdout)
        self.assertNotIn("SECRET123", self.proc.stdout + self.proc.stderr)

    def test_dry_run_changes_nothing(self):
        self.assertTrue(os.path.isdir(self.paths["done"]))
        self.assertTrue(self.fx.git(self.fx.main, "branch", "--list", "feat/done"))


class ProcessCheckTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.path, sha = self.fx.worktree("done")
        self.fx.prs("done", merged(sha))

    def tearDown(self):
        self.fx.cleanup()

    def test_lsof_failure_keeps_everything(self):
        p = self.fx.run(self.fx.main, FAKE_LSOF_FAIL="1")
        self.assertIn("Process check: FAILED", p.stdout)
        verdict, why = parse(p.stdout)[os.path.realpath(self.path)]
        self.assertEqual(verdict, "KEEP")
        self.assertIn("could not check for running processes", why)

    def test_no_process_check_flag(self):
        p = self.fx.run("--no-process-check", self.fx.main, FAKE_LSOF_FAIL="1")
        self.assertIn("Process check: OFF", p.stdout)
        self.assertEqual(parse(p.stdout)[os.path.realpath(self.path)][0], "REMOVE")

    def test_repo_given_by_worktree_path(self):
        p = self.fx.run(self.path)
        self.assertEqual(p.stdout.count("== "), 1)
        self.assertEqual(parse(p.stdout)[os.path.realpath(self.path)][0], "REMOVE")


class ApplyTest(unittest.TestCase):
    def test_apply_removes_only_remove(self):
        fx = Fixture()
        try:
            done, sha = fx.worktree("done")
            fx.prs("done", merged(sha))
            fx.git(fx.main, "update-ref", "refs/remotes/origin/feat/done", sha)
            dirty, sha2 = fx.worktree("dirty")
            fx.prs("dirty", merged(sha2))
            open(os.path.join(dirty, "new.txt"), "w").close()
            p = fx.run("--apply", fx.main)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("Removed 1, failed or skipped 0", p.stdout)
            self.assertFalse(os.path.exists(done))
            self.assertEqual(fx.git(fx.main, "branch", "--list", "feat/done"), "")
            self.assertEqual(fx.git(fx.main, "rev-parse", "refs/remotes/origin/feat/done"), sha)  # remote ref kept
            self.assertTrue(os.path.isdir(dirty))
            self.assertTrue(fx.git(fx.main, "branch", "--list", "feat/dirty"))
            self.assertNotIn(os.path.realpath(done), fx.git(fx.main, "worktree", "list"))
            self.assertTrue(os.path.isfile(os.path.join(fx.main, "a.txt")))
        finally:
            fx.cleanup()

    def test_usage_error(self):
        p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)


class SafetyTest(unittest.TestCase):
    """Cases that each need their own fixture."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.cleanup()

    def verdict(self, proc, path):
        return parse(proc.stdout)[os.path.realpath(path)]

    def test_show_untracked_files_setting_is_ignored(self):
        wt, sha = self.fx.worktree("u")
        self.fx.prs("u", merged(sha))
        self.fx.git(self.fx.main, "config", "status.showUntrackedFiles", "no")
        write(os.path.join(wt, "NOTES.md"), "only copy\n")
        verdict, why = self.verdict(self.fx.run(self.fx.main), wt)
        self.assertEqual(verdict, "KEEP")
        self.assertIn("untracked files", why)

    def test_include_outside(self):
        wt = os.path.join(self.fx.tmp, "elsewhere", "w")
        self.fx.git(self.fx.main, "worktree", "add", "-q", wt, "-b", "feat/w")
        self.fx.prs("w", merged(self.fx.git(wt, "rev-parse", "HEAD")))
        self.assertEqual(self.verdict(self.fx.run(self.fx.main), wt)[0], "KEEP")
        self.assertEqual(self.verdict(self.fx.run("--include-outside", self.fx.main), wt)[0], "REMOVE")

    def test_apply_keeps_nested_worktree(self):
        outer, sha = self.fx.worktree("outer")
        self.fx.prs("outer", merged(sha))
        inner = os.path.join(outer, ".claude", "worktrees", "agent-x")
        self.fx.git(self.fx.main, "worktree", "add", "-q", inner, "-b", "feat/inner")
        write(os.path.join(inner, "UNSAVED.txt"), "work\n")
        p = self.fx.run("--apply", self.fx.main)
        self.assertIn("Removed 0,", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(inner, "UNSAVED.txt")))

    def race(self, hook):
        """--apply where hook runs after the check, just before the remove. Returns (proc, worktree, sha)."""
        wt, sha = self.fx.worktree("late")
        self.fx.prs("late", merged(sha))
        p = self.fx.run("--apply", self.fx.main, FAKE_LSOF_COUNT=os.path.join(self.fx.tmp, "lsof-count"),
                        FAKE_LSOF_HOOK=hook.format(wt=wt))
        return p, wt, sha

    def test_race_late_commit(self):
        p, wt, sha = self.race("cd {wt} && echo new > late.txt && git add late.txt && git commit -qm late")
        self.assertIn("SKIPPED", p.stdout)
        self.assertIn("HEAD moved since the check", p.stdout)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(os.path.isfile(os.path.join(wt, "late.txt")))
        self.assertNotEqual(self.fx.git(self.fx.main, "rev-parse", "feat/late"), sha)

    def test_race_late_file(self):
        p, wt, _ = self.race("echo new > {wt}/late.txt")
        self.assertIn("SKIPPED", p.stdout)
        self.assertIn("untracked files", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(wt, "late.txt")))
        self.assertTrue(self.fx.git(self.fx.main, "branch", "--list", "feat/late"))


class RebuildableTest(unittest.TestCase):
    def test_patterns(self):
        sys.dont_write_bytecode = True  # no __pycache__ next to bin/worktree-cleanup
        loader = importlib.machinery.SourceFileLoader("worktree_cleanup", SCRIPT)
        spec = importlib.util.spec_from_loader("worktree_cleanup", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        for path in ["node_modules/", "apps/mobile/node_modules/", "apps/mobile/.expo/", ".next/", "next-env.d.ts",
                     "tsconfig.tsbuildinfo", "infra/azure/.terraform/", "src/generated/", "web/src/generated/",
                     "src/generated/api.ts", "pkg.egg-info/", "api/__pycache__/", ".venv/", ".coverage",
                     "dist/index.js"]:
            self.assertTrue(mod.rebuildable(path), path)
        for path in [".env", "infra/azure/terraform.tfvars", "generated/", "notes.md", "bin/", "obj/",
                     ".claude/", "node_modules", "next-env.d.ts/", "build.log"]:
            self.assertFalse(mod.rebuildable(path), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
