"""Tests for skills/review-branch/scripts/review_branch.py, on throwaway git repositories.

    python3 -m unittest discover -s tests

The `open` tests need `plannotator` on PATH and are skipped without it; they never open a
browser tab and submit through the page's own API.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills/review-branch/scripts/review_branch.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def run(*args, **kw):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, **kw)


class Repo(unittest.TestCase):
    """main: f.txt = a b. feat: commit 1 uppercases b, commit 2 appends c."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("a\nb\n")
        git(self.repo, "add", "f.txt")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "feat")
        (self.repo / "f.txt").write_text("a\nB\n")
        git(self.repo, "commit", "-qam", "uppercase b")
        self.c1 = git(self.repo, "rev-parse", "--short=7", "HEAD")
        (self.repo / "f.txt").write_text("a\nB\nc\n")
        git(self.repo, "commit", "-qam", "append c")
        self.c2 = git(self.repo, "rev-parse", "--short=7", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def notes(self, path="f.txt", rng=(2, 3), name="notes.json"):
        p = self.tmp / name
        p.write_text(json.dumps({"version": 1, "summary": "Edits f.txt.", "files": [
            {"path": path, "summary": "Two lines.", "annotations": [
                {"newRange": list(rng), "summary": "s", "rationale": "r", "author": "inferred"}]}]}))
        return p


class TestMapCheck(Repo):
    def test_map_credits_each_line_to_its_commit(self):
        out = run("map", "main", "feat", "--repo", str(self.repo))
        self.assertEqual(out.returncode, 0, out.stderr)
        blocks = json.loads(out.stdout)["files"][0]["hunks"][0]["blocks"]
        self.assertEqual(blocks, [{"newRange": [2, 2], "commit": self.c1},
                                  {"newRange": [3, 3], "commit": self.c2}])

    def test_map_ignore_credits_a_renamed_line_to_the_commit_before(self):
        (self.repo / "f.txt").write_text("a\nBee\nc\n")
        git(self.repo, "commit", "-qam", "rename B")
        rename = git(self.repo, "rev-parse", "--short=7", "HEAD")
        out = run("map", "main", "feat", "--repo", str(self.repo), "--ignore", rename)
        blocks = json.loads(out.stdout)["files"][0]["hunks"][0]["blocks"]
        self.assertEqual(blocks, [{"newRange": [2, 2], "commit": self.c1},
                                  {"newRange": [3, 3], "commit": self.c2}])

    def test_check_accepts_notes_on_changed_lines(self):
        out = run("check", "main", "feat", str(self.notes()), "--repo", str(self.repo))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("0 error(s), 0 warning(s)", out.stdout)

    def test_check_rejects_a_file_outside_the_diff(self):
        out = run("check", "main", "feat", str(self.notes(path="gone.txt")), "--repo", str(self.repo))
        self.assertEqual(out.returncode, 1)
        self.assertIn("gone.txt: not a changed file", out.stdout)

    def test_check_warns_on_a_note_over_unchanged_lines(self):
        out = run("check", "main", "feat", str(self.notes(rng=(1, 1))), "--repo", str(self.repo))
        self.assertEqual(out.returncode, 0)
        self.assertIn("covers no changed line", out.stdout)


@unittest.skipUnless(shutil.which("plannotator"), "plannotator is not installed")
class TestOpen(Repo):
    def open_and_submit(self, head, feedback):
        """Start `open` like an agent would, submit through the page's API, return (result, stderr)."""
        env = {**os.environ, "PLANNOTATOR_SKIP_BROWSER_OPEN": "1"}
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "open", "main", head, str(self.notes()),
                                 "--repo", str(self.repo)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            url, err = None, ""
            deadline = time.monotonic() + 60
            while url is None and time.monotonic() < deadline:
                line = proc.stderr.readline()
                err += line
                if line.startswith("Review page: "):
                    url = line.split()[2]
                elif not line and proc.poll() is not None:
                    break
            self.assertIsNotNone(url, err)
            with urllib.request.urlopen(url + "/api/external-annotations", timeout=10) as r:
                pushed = json.load(r)["annotations"]
            self.assertEqual(len(pushed), 3)  # general + file + line
            req = urllib.request.Request(url + "/api/feedback", method="POST",
                                         data=json.dumps({"feedback": feedback, "annotations": []}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
            out, rest = proc.communicate(timeout=30)
            return json.loads(out.strip().splitlines()[-1]), err + rest
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_open_returns_the_feedback_and_saves_it(self):
        result, _ = self.open_and_submit("feat", "Line 2: why uppercase?")
        self.assertEqual(result["decision"], "annotated")
        self.assertIn("Line 2: why uppercase?", result["message"])
        self.assertEqual(len(list(self.tmp.glob("notes.feedback-*.json"))), 1)

    def test_open_reviews_a_head_that_is_not_checked_out_in_a_temporary_worktree(self):
        git(self.repo, "checkout", "-q", "main")
        _, err = self.open_and_submit("feat", "ok")
        self.assertIn("temporary worktree", err)
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 0)  # removed again

    def test_open_refuses_notes_that_do_not_match(self):
        out = run("open", "main", "feat", str(self.notes(path="gone.txt")), "--repo", str(self.repo))
        self.assertEqual(out.returncode, 1)
        self.assertIn("do not match", out.stderr)


if __name__ == "__main__":
    unittest.main()
