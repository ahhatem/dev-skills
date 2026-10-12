#!/usr/bin/env python3
"""Notes beside a branch's diff: map the diff to its commits, check a notes file, open it for review.

    python3 review_branch.py map   <base> <head> [--repo DIR] [--ignore SHA]...
    python3 review_branch.py check <base> <head> <notes.json> [--repo DIR] [--show]
    python3 review_branch.py open  <base> <head> <notes.json> [--repo DIR]

map prints JSON: the merge-base, every commit in base...head with its full message, and for
each changed file its hunks, each hunk split into blocks of added lines attributed to the
commit that last wrote them (git blame restricted to the range). --ignore SHA, repeatable,
credits a mechanical commit's lines (a rename, a format sweep) to the commit before it.

check validates a notes file (Hunk's --agent-context format) against base...head: schema,
paths that exist in the diff, and ranges that land on a displayed hunk. It warns on
overlapping notes and on notes that cover no changed line. --show prints the first and last
line under each note, to confirm it sits on the code it describes. Exit 1 on any error;
warnings alone exit 0.

open runs check, starts `plannotator review` on the committed diff only (--diff-type
merge-base, so uncommitted edits never shift the notes), pushes the notes once the page is up,
and waits. When the user submits, it prints Plannotator's JSON
({"decision": "approved" | "annotated" | "dismissed", "message": ...}), saves it beside the
notes as <notes>.feedback-<time>.json, and exits with Plannotator's status. Run it in the
background: it blocks until the user submits. If DIR's HEAD is not <head>, it reviews a
temporary detached worktree at <head> under ~/.cache/review-branch/worktrees and removes it
after. Sharing and update checks are off; PLANNOTATOR_SKIP_BROWSER_OPEN=1 suppresses the tab.

map and check are read-only: only git diff, log, blame, merge-base, rev-parse and show are run.
"""
import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
NOTE_KEYS = {"newRange", "summary", "rationale", "author"}
SOURCE = "review-branch"
WORKTREES = Path.home() / ".cache" / "review-branch" / "worktrees"
READY_TIMEOUT = 90


# --- map ---------------------------------------------------------------------------------

def git(repo, *args):
    cmd = ["git", "-C", repo, "-c", "core.quotePath=false", *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"error: {' '.join(cmd)}\n{out.stderr.strip()}")
    return out.stdout


def file_statuses(repo, mb, head):
    """Return [(status, old_path, new_path)] for mb..head, renames detected."""
    fields = git(repo, "diff", "-z", "--name-status", "-M", mb, head).split("\0")
    entries, i = [], 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            entries.append((status[0], fields[i + 1], fields[i + 2]))
            i += 3
        else:
            entries.append((status[0], fields[i + 1], fields[i + 1]))
            i += 2
    return entries


def parse_hunks(repo, mb, head, *paths):
    """Hunks of one file at the default 3 lines of context, which is what a viewer shows."""
    text = git(repo, "diff", "--no-color", "--no-ext-diff", "-M", mb, head, "--", *paths)
    hunks, cur, old_no, new_no = [], None, 0, 0
    for line in text.splitlines():
        m = HUNK_HEADER.match(line)
        if m:
            a, b, c, d = (int(x) if x is not None else 1 for x in m.groups())
            cur = {
                "header": line.split(" @@")[0] + " @@",
                "oldRange": [a, a + b - 1] if b else None,
                "newRange": [c, c + d - 1] if d else None,
                "addedLines": [],
                "removedLines": [],
            }
            hunks.append(cur)
            old_no, new_no = a, c
            continue
        if cur is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            cur["addedLines"].append(new_no)
            new_no += 1
        elif line.startswith("-"):
            cur["removedLines"].append(old_no)
            old_no += 1
        elif line.startswith(" ") or line == "":
            old_no += 1
            new_no += 1
    return hunks


def blame(repo, mb, head, path, ignore=()):
    """Map new-file line number -> commit sha for lines written inside mb..head.

    ignore: commits (renames, format sweeps) whose lines are credited to the commit before.
    """
    skip = [arg for sha in ignore for arg in ("--ignore-rev", sha)]
    text = git(repo, "blame", "--line-porcelain", *skip, f"{mb}..{head}", "--", path)
    owner, sha, final, boundary = {}, None, None, False
    for line in text.splitlines():
        if line.startswith("\t"):
            if not boundary:
                owner[final] = sha
            sha = None
            continue
        if sha is None:
            parts = line.split()
            sha, final, boundary = parts[0], int(parts[2]), False
        elif line == "boundary":
            boundary = True
    return owner


def blocks(added, owner):
    """Group added lines into runs that are contiguous and written by the same commit."""
    runs = []
    for n in added:
        sha = owner.get(n, "unknown")
        if runs and runs[-1]["commit"] == sha[:7] and runs[-1]["newRange"][1] == n - 1:
            runs[-1]["newRange"][1] = n
        else:
            runs.append({"newRange": [n, n], "commit": sha[:7]})
    return runs


def commits(repo, mb, head):
    text = git(repo, "log", "--reverse", "--format=%h%x00%s%x00%b%x1e", f"{mb}..{head}")
    out = []
    for rec in text.split("\x1e"):
        rec = rec.strip("\n")
        if rec:
            sha, subject, body = rec.split("\0")
            out.append({"commit": sha, "subject": subject, "body": body.strip()})
    return out


def build_map(repo, base, head, ignore=(), lines=False):
    mb = git(repo, "merge-base", base, head).strip()
    files = []
    for status, old, new in file_statuses(repo, mb, head):
        path = old if status == "D" else new
        hunks = parse_hunks(repo, mb, head, *((old, new) if status == "R" else (path,)))
        owner = blame(repo, mb, head, new, ignore) if status != "D" else {}
        entry = {"path": path, "status": status, "hunks": []}
        if status == "R":
            entry["oldPath"] = old
        for h in hunks:
            entry["hunks"].append({
                "header": h["header"],
                "newRange": h["newRange"],
                "oldRange": h["oldRange"],
                "added": len(h["addedLines"]),
                "removed": len(h["removedLines"]),
                "blocks": blocks(h["addedLines"], owner),
                **({"addedLines": h["addedLines"]} if lines else {}),
            })
        files.append(entry)
    return {
        "range": f"{base}...{head}",
        "mergeBase": mb[:7],
        "head": git(repo, "rev-parse", "--short", head).strip(),
        "commits": commits(repo, mb, head),
        "files": files,
    }


# --- check -------------------------------------------------------------------------------

def check(repo, base, head, notes_path, show=False):
    with open(notes_path) as f:
        notes = json.load(f)
    diff = {f["path"]: f for f in build_map(repo, base, head, lines=True)["files"]}
    errors, warnings = [], []

    if notes.get("version") != 1:
        errors.append("top level: version must be 1")
    if not isinstance(notes.get("summary"), str) or not notes["summary"].strip():
        errors.append("top level: summary must be a non-empty string")
    entries = notes.get("files")
    if not isinstance(entries, list):
        errors.append("top level: files must be a list")
        entries = []

    seen = set()
    for entry in entries:
        path = entry.get("path")
        if path not in diff:
            errors.append(f"{path}: not a changed file in {base}...{head}")
            continue
        seen.add(path)
        if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
            errors.append(f"{path}: file summary must be a non-empty string")
        shown = [h["newRange"] for h in diff[path]["hunks"] if h["newRange"]]
        added = {n for h in diff[path]["hunks"] for n in h["addedLines"]}
        text = git(repo, "show", f"{head}:{path}").splitlines() if show and added else []
        for i, note in enumerate(entry.get("annotations", [])):
            where = f"{path} annotation {i}"
            extra = set(note) - NOTE_KEYS
            if extra:
                warnings.append(f"{where}: keys Hunk may ignore: {sorted(extra)}")
            for key in ("summary", "rationale", "author"):
                if not isinstance(note.get(key), str) or not note[key].strip():
                    errors.append(f"{where}: {key} must be a non-empty string")
            r = note.get("newRange")
            if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r) and 1 <= r[0] <= r[1]):
                errors.append(f"{where}: newRange must be [start, end] with 1 <= start <= end")
                continue
            if not any(r[0] <= hi and lo <= r[1] for lo, hi in shown):
                errors.append(f"{where}: newRange {r} falls outside every displayed hunk {shown}")
            elif not any(r[0] <= n <= r[1] for n in added):
                warnings.append(f"{where}: newRange {r} covers no changed line")
            if text:
                first = text[r[0] - 1].strip() if r[0] <= len(text) else "<past end>"
                last = text[r[1] - 1].strip() if r[1] <= len(text) else "<past end>"
                print(f"{path} {r} {note.get('author')}: {note.get('summary')}\n    {r[0]}: {first[:90]}\n    {r[1]}: {last[:90]}")
        ranges = sorted(
            (n["newRange"], i) for i, n in enumerate(entry.get("annotations", []))
            if isinstance(n.get("newRange"), list) and len(n["newRange"]) == 2
        )
        for (a, i), (b, j) in zip(ranges, ranges[1:]):
            if b[0] <= a[1]:
                warnings.append(f"{path}: annotations {i} {a} and {j} {b} overlap")

    for path, f in diff.items():
        if path not in seen:
            warnings.append(f"{path}: changed ({f['status']}) but has no entry in the notes")

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print(f"{len(errors)} error(s), {len(warnings)} warning(s), {len(seen)}/{len(diff)} files covered")
    return 1 if errors else 0


# --- open --------------------------------------------------------------------------------

def log(msg):
    print(msg, file=sys.stderr, flush=True)


def annotations(notes):
    """The notes as Plannotator external annotations: one general, one per file, one per note."""
    out = [{"source": SOURCE, "scope": "general", "author": SOURCE, "text": notes["summary"]}]
    for f in notes["files"]:
        out.append({"source": SOURCE, "scope": "file", "filePath": f["path"],
                    "author": SOURCE, "text": f["summary"]})
        for a in f.get("annotations", []):
            out.append({
                "source": SOURCE,
                "scope": "line",
                "filePath": f["path"],
                "lineStart": a["newRange"][0],
                "lineEnd": a["newRange"][1],
                "side": "new",
                "type": "concern" if "review" in a["author"] else "comment",
                "author": a["author"],
                "text": f"[{a['author']}] {a['summary']}\n\n{a['rationale']}",
            })
    return out


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode()


def push(url, notes):
    """Replace this tool's earlier annotations on the page with the notes; return the count."""
    body = {"annotations": annotations(notes)}
    api = url.rstrip("/") + "/api/external-annotations"
    call("DELETE", f"{api}?source={SOURCE}")
    status, text = call("POST", api, body)
    if status != 201:
        sys.exit(f"error: pushing notes returned {status}: {text}")
    return len(body["annotations"])


def review_dir(repo, head):
    """Return (directory to review, temporary worktree to remove or None)."""
    sha = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    if git(repo, "rev-parse", "HEAD").strip() == sha:
        return repo, None
    top = Path(git(repo, "rev-parse", "--show-toplevel").strip()).name
    wt = WORKTREES / f"{top}-{sha[:12]}"
    if wt.exists():
        if git(str(wt), "rev-parse", "HEAD").strip() != sha:
            sys.exit(f"error: {wt} exists at another commit; remove it with `git worktree remove {wt}`")
        return str(wt), None  # left by an earlier run that may still be open; not ours to remove
    WORKTREES.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "--detach", str(wt), sha)
    log(f"Reviewing a temporary worktree at {head} ({sha[:12]}): {wt}")
    return str(wt), str(wt)


def wait_ready(proc, ready_file):
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"error: plannotator exited with {proc.returncode} before the page was up")
        try:
            return json.loads(Path(ready_file).read_text())["url"]
        except (OSError, ValueError, KeyError):
            time.sleep(0.3)
    sys.exit(f"error: the page was not up after {READY_TIMEOUT}s")


def open_review(repo, base, head, notes_path):
    report = io.StringIO()
    with contextlib.redirect_stdout(report):
        failed = check(repo, base, head, notes_path)
    if failed:
        sys.exit(f"error: the notes do not match {base}...{head}; regenerate them\n{report.getvalue()}")
    with open(notes_path) as f:
        notes = json.load(f)
    target, temp_wt = review_dir(repo, head)

    ready_file = Path(tempfile.mkdtemp(prefix="review-branch-")) / "ready.json"
    env = {**os.environ, "PLANNOTATOR_SHARE": "disabled", "PLANNOTATOR_UPDATE_CHECK": "0",
           "PLANNOTATOR_READY_FILE": str(ready_file)}
    cmd = ["plannotator", "review", "--json", "--no-git-remote-check",
           "--base", base, "--diff-type", "merge-base", target]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        url = wait_ready(proc, ready_file)
        count = push(url, notes)
        log(f"Review page: {url} ({count} notes, {base}...{head}). Waiting for the user to submit.")
        out, _ = proc.communicate()
        out = out.strip()
        print(out, flush=True)
        if out:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            saved = Path(notes_path).with_suffix(f".feedback-{stamp}.json")
            saved.write_text(out + "\n")
            log(f"Feedback saved: {saved}")
        return proc.returncode
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        ready_file.unlink(missing_ok=True)
        ready_file.parent.rmdir()
        if temp_wt:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", temp_wt],
                           capture_output=True)


# --- cli ---------------------------------------------------------------------------------

def main(argv):
    repo, ignore, rest, show = ".", [], [], False
    args = iter(argv)
    for arg in args:
        if arg == "--repo":
            repo = next(args)
        elif arg == "--ignore":
            ignore.append(next(args))
        elif arg == "--show":
            show = True
        else:
            rest.append(arg)
    argv = rest
    if len(argv) == 3 and argv[0] == "map":
        json.dump(build_map(repo, argv[1], argv[2], ignore), sys.stdout, indent=1)
        print()
        return 0
    if len(argv) == 4 and argv[0] == "check":
        return check(repo, argv[1], argv[2], argv[3], show)
    if len(argv) == 4 and argv[0] == "open":
        return open_review(str(Path(repo).resolve()), argv[1], argv[2], str(Path(argv[3]).resolve()))
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
