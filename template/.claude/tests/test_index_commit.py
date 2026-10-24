#!/usr/bin/env python3
r"""Proof for tools/index_commit.py, the private-index commit cycle.

A throwaway git repo under TMP holds the whole fixture: the tool and the two modules it
imports are copied in, and `baseline_sync.py classify` is replaced by a planted script named
through INDEX_COMMIT_CLASSIFY_CMD. Nothing here touches the real checkout's index.

Cases:
- a new gitignored file under `.claude/tests/` is force-added and gains its lock row; the
  staged lock blob holds HEAD's rows plus that row only, even when the working lock carries
  an extra planted peer row;
- a tracked path deleted from the working tree is staged as a deletion and its lock row removed;
- a peer file staged in the shared index is absent from the private index and is still staged
  in the shared index after resync;
- resync after a real commit leaves no staged change for the committed paths, the working
  lock equals HEAD for their rows, the planted peer row survives, and it prints RESYNC OK;
- resync before any commit refuses with a named error and a non-zero exit;
- `--blob` stages the given content, not the working file, and leaves the working file alone;
- the tool's source carries no `commit` argv in any list/tuple literal (planted control first),
  and the printed command is the GIT_INDEX_FILE commit line;
- usage errors: an unknown path, a path outside the repo, and an unreadable lock each exit
  non-zero with a named error and stage nothing in the shared index;
- a submodule path with a checkout ahead of its gitlink stages as a gitlink at the checkout's
  HEAD and deletes nothing; a submodule with no checkout is refused and names the pathspec route.

    python3 .claude/tests/test_index_commit.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOL = REPO / ".claude" / "tools" / "index_commit.py"
HOOKS = REPO / ".claude" / "hooks"

LOCK = ".claude/baseline.lock.json"
HEAD_ROWS = {"keep_a.py", "keep_b.py"}

STUB = '''"""Planted stand-in for `baseline_sync.py classify`: one `local` row per named path."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

assert sys.argv[1] == "classify", "the tool must call the classifier's own CLI shape: %r" % sys.argv
assert "--status" in sys.argv, "baseline_sync.py classify refuses without --status: %r" % sys.argv
root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           check=True).stdout.strip())
rest = sys.argv[2:]
paths = [a for i, a in enumerate(rest) if not a.startswith("-") and (i == 0 or rest[i - 1] != "--status")]
lock_path = root / ".claude" / "baseline.lock.json"
lock = json.loads(lock_path.read_text(encoding="utf-8"))
for relpath in paths:
    blob = subprocess.run(["git", "show", ":" + relpath], capture_output=True, check=True).stdout
    lock.setdefault("files", {})[relpath] = {
        "status": "local", "hash": hashlib.sha256(blob).hexdigest()}
lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\\n",
                     encoding="utf-8", newline="\\n")
'''

LOCK_SCAFFOLD = {
    "baseline_repo": "https://example.invalid/baseline.git",
    "baseline_ref": "main",
    "files": {name: {"status": "local", "hash": "0" * 64} for name in sorted(HEAD_ROWS)},
}

failures = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append("%s: %s" % (label, detail))
    return ok


def git_env(home):
    env = dict(os.environ)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("INDEX_COMMIT_CLASSIFY_CMD", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "proof"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "proof@example.invalid"
    return env


def git(root, args, env, check_rc=True, **kw):
    proc = subprocess.run(["git"] + args, cwd=str(root), env=env, capture_output=True,
                          encoding="utf-8", timeout=120, **kw)
    if check_rc and proc.returncode != 0:
        raise AssertionError("git %s failed: %s%s" % (args, proc.stdout, proc.stderr))
    return proc


def scene(tmp, name):
    """A fresh repo: the tool plus its two imports, a lock with HEAD_ROWS, and a peer file."""
    root = Path(tmp) / name
    (root / ".claude" / "tools").mkdir(parents=True)
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "tests").mkdir(parents=True)
    shutil.copy2(TOOL, root / ".claude" / "tools" / "index_commit.py")
    for module in ("_hook_state.py", "_file_lock.py"):
        shutil.copy2(HOOKS / module, root / ".claude" / "hooks" / module)
    (root / ".claude" / "tools" / "classify_stub.py").write_text(STUB, encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text(".claude/tests/\n", encoding="utf-8", newline="\n")
    (root / LOCK).write_text(json.dumps(LOCK_SCAFFOLD, indent=2) + "\n", encoding="utf-8", newline="\n")
    for relpath in sorted(HEAD_ROWS) + ["gone.py", "peer.py"]:
        (root / relpath).write_text("head %s\n" % relpath, encoding="utf-8", newline="\n")
    env = git_env(root)
    git(root, ["init", "-q", "-b", "main"], env)
    git(root, ["add", "-A"], env)
    git(root, ["commit", "-q", "-m", "seed"], env)
    return root, env


def tool(root, env, args, extra_env=None):
    run_env = dict(env)
    run_env["INDEX_COMMIT_CLASSIFY_CMD"] = "python3 .claude/tools/classify_stub.py classify"
    if extra_env:
        run_env.update(extra_env)
    return subprocess.run([sys.executable, str(root / ".claude" / "tools" / "index_commit.py")] + args,
                          cwd=str(root), env=run_env, capture_output=True, encoding="utf-8",
                          timeout=180)


def idx_env(env, root, name):
    run_env = dict(env)
    run_env["GIT_INDEX_FILE"] = str(root / ".git" / ("index-" + name))
    return run_env


def blob_at(root, env, relpath):
    """The blob the given index (env's GIT_INDEX_FILE) holds for a path, or None."""
    listing = git(root, ["ls-files", "-s", "--", relpath], env).stdout.split()
    if not listing:
        return None
    return git(root, ["cat-file", "-p", listing[1]], env).stdout


def lock_at(root, ref, env):
    """`ref` is "HEAD" or "" for the index env names; returns the parsed lock that ref holds."""
    if ref == "HEAD":
        return json.loads(git(root, ["show", "HEAD:" + LOCK], env).stdout)
    return json.loads(blob_at(root, env, LOCK) or "")


def names(root, env, cached):
    args = ["diff", "--cached", "--name-only"] if cached else ["diff", "--name-only"]
    return sorted(x for x in git(root, args, env).stdout.splitlines() if x.strip())


def commit_index(root, env, name, message):
    return subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(root),
                          env=idx_env(env, root, name), capture_output=True, encoding="utf-8",
                          timeout=120)


def case_new_gitignored_file(tmp):
    root, env = scene(tmp, "newfile")
    (root / ".claude" / "tests" / "new_proof.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))
    work["files"]["planted_peer_row.py"] = {"status": "local", "hash": "f" * 64}
    (root / LOCK).write_text(json.dumps(work, indent=2) + "\n", encoding="utf-8", newline="\n")

    proc = tool(root, env, ["stage", "--name", "newfile", "--", ".claude/tests/new_proof.py"])
    check("new gitignored path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    staged = names(root, idx_env(env, root, "newfile"), cached=True)
    check("new gitignored path is force-added to the private index",
          staged == [".claude/baseline.lock.json", ".claude/tests/new_proof.py"], staged)
    private = lock_at(root, "", idx_env(env, root, "newfile"))
    check("lock blob holds HEAD's rows plus the new row only",
          sorted(private["files"]) == sorted(["keep_a.py", "keep_b.py", ".claude/tests/new_proof.py"]),
          sorted(private["files"]))
    check("the planted peer row is not in the lock blob",
          "planted_peer_row.py" not in private["files"], sorted(private["files"]))
    check("the new row is a local row written by classify",
          private["files"][".claude/tests/new_proof.py"].get("status") == "local",
          private["files"].get(".claude/tests/new_proof.py"))
    check("stage reports the added row", ".claude/tests/new_proof.py" in proc.stdout, proc.stdout)
    check("stage prints the GIT_INDEX_FILE commit line",
          "GIT_INDEX_FILE=.git/index-newfile git commit" in proc.stdout, proc.stdout)
    commit = commit_index(root, env, "newfile", "new proof")
    check("new-file scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "newfile"])
    check("new-file scene: resync exits 0", res.returncode == 0, res.stdout + res.stderr)
    check("a lock the commit changed is not left as a staged revert in the shared index",
          names(root, env, cached=True) == [], names(root, env, cached=True))
    text = (root / LOCK).read_text(encoding="utf-8")
    check("resync keeps the working lock in its authored indent-2 format, never one compact line",
          text.startswith("{\n  \"") and text.endswith("}\n"), text[:80])


def case_deleted_path(tmp):
    root, env = scene(tmp, "deleted")
    (root / "gone.py").unlink()
    proc = tool(root, env, ["stage", "--name", "deleted", "--", "gone.py"])
    check("deleted path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "deleted")
    tracked = git(root, ["ls-files", "--", "gone.py"], private).stdout.strip()
    check("deleted path is staged as a deletion in the private index", tracked == "", tracked)
    lock = lock_at(root, "", private)
    check("the deleted path's lock row is removed and HEAD's other rows stay",
          sorted(lock["files"]) == ["keep_a.py", "keep_b.py"], sorted(lock["files"]))
    check("stage names the removed row", "gone.py" in proc.stdout, proc.stdout)


def case_forget_row_of_earlier_deletion(tmp):
    """A path an earlier commit deleted keeps its lock row; naming it stages only that row's removal."""
    root, env = scene(tmp, "forget")
    seeded = json.loads((root / LOCK).read_text(encoding="utf-8"))
    seeded["files"]["gone.py"] = {"status": "tracked", "hash": "e" * 64}
    (root / LOCK).write_text(json.dumps(seeded, indent=2) + "\n", encoding="utf-8", newline="\n")
    git(root, ["commit", "-q", "-m", "row for gone.py", "--", LOCK], env)
    git(root, ["rm", "-q", "gone.py"], env)
    git(root, ["commit", "-q", "-m", "delete gone.py, lock untouched"], env)
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))
    work["files"]["planted_peer_row.py"] = {"status": "local", "hash": "f" * 64}
    (root / LOCK).write_text(json.dumps(work, indent=2) + "\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "forget", "--", "gone.py"])
    check("forget scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "forget")
    check("forget scene: only the lock is staged", names(root, private, cached=True) == [LOCK],
          names(root, private, cached=True))
    check("forget scene: the lock blob drops the row and keeps HEAD's others",
          sorted(lock_at(root, "", private)["files"]) == ["keep_a.py", "keep_b.py"],
          sorted(lock_at(root, "", private)["files"]))
    commit = commit_index(root, env, "forget", "forget row")
    check("forget scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "forget"])
    check("forget scene: resync exits 0", res.returncode == 0, res.stdout + res.stderr)
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))["files"]
    check("forget scene: the working lock drops the row and keeps the peer row",
          "gone.py" not in work and "planted_peer_row.py" in work, sorted(work))


def case_lock_path_refused(tmp):
    """The tool composes the lock blob itself, so a lock named as a path is refused, never silently rewritten."""
    root, env = scene(tmp, "selflock")
    before = (root / LOCK).read_bytes()
    replacement = Path(tmp) / "selflock_blob.json"
    replacement.write_bytes(before)
    for args in (["--", LOCK], ["--blob", "%s=%s" % (LOCK, replacement), "--", LOCK]):
        proc = tool(root, env, ["stage", "--name", "selflock"] + args)
        out = proc.stdout + proc.stderr
        check("a lock named as a path exits non-zero and names the path to give instead (%s)" % args[0],
              proc.returncode != 0 and LOCK in out and "deleted" in out, out)
    check("the refused stage leaves the working lock unchanged", (root / LOCK).read_bytes() == before)
    check("the refused stage leaves no private index", not (root / ".git" / "index-selflock").exists())


def case_peer_and_resync(tmp):
    root, env = scene(tmp, "resync")
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))
    work["files"]["planted_peer_row.py"] = {"status": "local", "hash": "f" * 64}
    (root / LOCK).write_text(json.dumps(work, indent=2) + "\n", encoding="utf-8", newline="\n")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    (root / "peer.py").write_text("peer staged\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "peer.py"], env)

    proc = tool(root, env, ["stage", "--name", "resync", "--", "keep_a.py"])
    check("peer scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "resync")
    check("the peer's staged change is absent from the private index",
          blob_at(root, private, "peer.py") == "head peer.py\n"
          and git(root, ["show", "HEAD:peer.py"], env).stdout == "head peer.py\n",
          repr(blob_at(root, private, "peer.py")))
    check("the peer's file is still staged in the shared index before resync",
          "peer.py" in names(root, env, cached=True), names(root, env, cached=True))

    commit = commit_index(root, env, "resync", "mine")
    check("the private-index commit lands", commit.returncode == 0, commit.stdout + commit.stderr)

    res = tool(root, env, ["resync", "--name", "resync"])
    check("resync exits 0 after a real commit", res.returncode == 0, res.stdout + res.stderr)
    check("resync prints RESYNC OK", "RESYNC OK" in res.stdout, res.stdout)
    check("the committed path has no staged change left in the shared index",
          "keep_a.py" not in names(root, env, cached=True), names(root, env, cached=True))
    check("the lock the commit changed is not left as a staged revert in the shared index",
          LOCK not in names(root, env, cached=True), names(root, env, cached=True))
    check("the peer's file is still staged in the shared index after resync",
          "peer.py" in names(root, env, cached=True), names(root, env, cached=True))
    check("the peer's working copy is untouched",
          (root / "peer.py").read_text(encoding="utf-8") == "peer staged\n")
    head_lock = lock_at(root, "HEAD", env)
    work_lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    check("the working lock equals HEAD for the committed path's row",
          work_lock["files"].get("keep_a.py") == head_lock["files"].get("keep_a.py"),
          (work_lock["files"].get("keep_a.py"), head_lock["files"].get("keep_a.py")))
    check("the planted peer row survives in the working lock",
          work_lock["files"].get("planted_peer_row.py") == {"status": "local", "hash": "f" * 64},
          work_lock["files"].get("planted_peer_row.py"))
    check("resync removes the private index and its sidecar",
          not (root / ".git" / "index-resync").exists()
          and not (root / ".git" / "index-resync.paths.json").exists())


def case_changed_row_travels_with_its_path(tmp):
    """A committed path's existing row that changed in the working lock (a pull's new hash) is committed and kept."""
    root, env = scene(tmp, "rowchange")
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))
    pulled = dict(work["files"]["keep_a.py"], hash="a" * 64)
    work["files"]["keep_a.py"] = pulled
    work["files"]["keep_b.py"] = dict(work["files"]["keep_b.py"], hash="b" * 64)
    (root / LOCK).write_text(json.dumps(work, indent=2) + "\n", encoding="utf-8", newline="\n")
    (root / "keep_a.py").write_text("pulled\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "rowchange", "--", "keep_a.py"])
    check("row-change scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = lock_at(root, "", idx_env(env, root, "rowchange"))["files"]
    check("the committed path's changed row is in the lock blob", private.get("keep_a.py") == pulled,
          private.get("keep_a.py"))
    check("an unnamed path's changed row stays out of the lock blob",
          private.get("keep_b.py", {}).get("hash") != "b" * 64, private.get("keep_b.py"))
    commit = commit_index(root, env, "rowchange", "pull")
    check("row-change scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "rowchange"])
    check("row-change scene: resync exits 0", res.returncode == 0, res.stdout + res.stderr)
    after = json.loads((root / LOCK).read_text(encoding="utf-8"))["files"]
    check("resync keeps the committed row change in the working lock", after.get("keep_a.py") == pulled,
          after.get("keep_a.py"))
    check("resync keeps the unnamed row change in the working lock",
          after.get("keep_b.py", {}).get("hash") == "b" * 64, after.get("keep_b.py"))


def case_peer_staged_same_path(tmp):
    """The peer staged its own content of a path this commit carries through --blob."""
    root, env = scene(tmp, "samepath")
    (root / "keep_a.py").write_text("peer version\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "keep_a.py"], env)
    mine = Path(tmp) / "samepath_mine.txt"
    mine.write_text("my hunk only\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "samepath", "--blob", "keep_a.py=%s" % mine])
    check("same-path scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    commit = commit_index(root, env, "samepath", "mine")
    check("same-path scene: the private-index commit lands", commit.returncode == 0, commit.stderr)
    res = tool(root, env, ["resync", "--name", "samepath"])
    check("a committed path left peer-staged is reported, not a mismatch: resync exits 0",
          res.returncode == 0 and "left (peer-staged" in res.stdout and "MISMATCH" not in res.stdout,
          res.stdout + res.stderr)
    check("the peer's staged content of the committed path survives resync",
          blob_at(root, env, "keep_a.py") == "peer version\n", repr(blob_at(root, env, "keep_a.py")))


def case_head_moved(tmp):
    """A commit lands between stage and the private-index commit, which then reverts it."""
    root, env = scene(tmp, "moved")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "moved", "--", "keep_a.py"])
    check("moved scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    (root / "keep_b.py").write_text("a peer's commit\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", "keep_b.py"], env)
    git(root, ["commit", "-q", "-m", "peer"], env)
    commit_index(root, env, "moved", "mine")
    res = tool(root, env, ["resync", "--name", "moved"])
    out = res.stdout + res.stderr
    check("resync after HEAD moved exits non-zero and names the reverted path",
          res.returncode != 0 and "keep_b.py" in out and "revert" in out, out)


def case_resync_after_later_commit(tmp):
    """The private-index commit landed, then a later commit changed the same path before resync."""
    root, env = scene(tmp, "later")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "later", "--", "keep_a.py"])
    check("later scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    commit_index(root, env, "later", "mine")
    (root / "keep_a.py").write_text("later fix\n", encoding="utf-8", newline="\n")
    git(root, ["commit", "-q", "-m", "later", "--", "keep_a.py"], env)
    res = tool(root, env, ["resync", "--name", "later"])
    out = res.stdout + res.stderr
    check("resync after a later commit on the same path exits 0, not 'did not land'",
          res.returncode == 0 and "did not land" not in out, out)
    check("the later commit's path has no staged change left", "keep_a.py" not in names(root, env, cached=True),
          names(root, env, cached=True))
    check("later scene: resync removes the private index and its sidecar",
          not (root / ".git" / "index-later").exists()
          and not (root / ".git" / "index-later.paths.json").exists())


RACE_DRIVER = """
import subprocess, sys
sys.argv = ["index_commit.py", "stage", "--name", "race", "--", "keep_a.py"]
sys.path.insert(0, ".claude/tools")
import index_commit
real = index_commit._git
moved = []
def racing(root, args, index=None, **kw):
    if args and args[0] == "read-tree" and not moved:
        moved.append(1)
        open("raced.py", "w").write("a peer's commit\\n")
        subprocess.run(["git", "add", "--", "raced.py"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "peer"], check=True)
    return real(root, args, index=index, **kw)
index_commit._git = racing
sys.exit(index_commit.main(sys.argv[1:]))
"""


def case_seeded_from_recorded_base(tmp):
    """A commit landing inside `stage` must not change the tree the private index starts from: the
    index is the recorded base plus the staged paths, so the guard's base check describes it exactly."""
    root, env = scene(tmp, "race")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    run_env = dict(env)
    run_env["INDEX_COMMIT_CLASSIFY_CMD"] = "python3 .claude/tools/classify_stub.py classify"
    proc = subprocess.run([sys.executable, "-c", RACE_DRIVER], cwd=str(root), env=run_env,
                          capture_output=True, encoding="utf-8", timeout=180)
    check("race scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    sidecar = root / ".git" / "index-race.paths.json"
    base = json.loads(sidecar.read_text(encoding="utf-8")).get("base_head")
    check("the stage record sits at <index>.paths.json and names the base",
          bool(base) and base != git(root, ["rev-parse", "HEAD"], env).stdout.strip(), str(base))
    listed = git(root, ["ls-files", "--", "raced.py"], idx_env(env, root, "race")).stdout.strip()
    check("the private index holds the recorded base's tree, not the HEAD that moved during stage",
          listed == "", "private index lists %r" % listed)


def case_head_moved_lock_rows(tmp):
    """A lock-only commit lands between stage and a private-index commit that adds a row; its rows are reverted."""
    root, env = scene(tmp, "lockmoved")
    (root / ".claude" / "tests" / "row_proof.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "lockmoved", "--", ".claude/tests/row_proof.py"])
    check("lock-moved scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    peer = json.loads(git(root, ["show", "HEAD:" + LOCK], env).stdout)
    peer["files"]["keep_b.py"] = {"status": "local", "hash": "e" * 64}
    work = json.loads((root / LOCK).read_text(encoding="utf-8"))
    work["files"]["keep_b.py"] = peer["files"]["keep_b.py"]
    (root / LOCK).write_text(json.dumps(peer, indent=2) + "\n", encoding="utf-8", newline="\n")
    git(root, ["add", "--", LOCK], env)
    git(root, ["commit", "-q", "-m", "peer reclassifies keep_b.py"], env)
    (root / LOCK).write_text(json.dumps(work, indent=2) + "\n", encoding="utf-8", newline="\n")
    commit_index(root, env, "lockmoved", "mine")
    res = tool(root, env, ["resync", "--name", "lockmoved"])
    out = res.stdout + res.stderr
    check("resync after a lock-only commit exits non-zero and names the reverted lock row",
          res.returncode != 0 and "keep_b.py" in out and "revert" in out, out)


def case_resync_before_commit(tmp):
    root, env = scene(tmp, "early")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "early", "--", "keep_a.py"])
    check("early scene: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    res = tool(root, env, ["resync", "--name", "early"])
    check("resync before any commit exits non-zero", res.returncode != 0, res.stdout)
    check("resync before any commit names the unlanded commit",
          "did not land" in (res.stdout + res.stderr), res.stdout + res.stderr)
    check("resync before any commit stages nothing in the shared index",
          "keep_a.py" not in names(root, env, cached=True), names(root, env, cached=True))


def case_blob(tmp):
    root, env = scene(tmp, "blob")
    (root / "keep_a.py").write_text("WORKTREE CONTENT\n", encoding="utf-8", newline="\n")
    (root / "mine.blob").write_text("BLOB CONTENT\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "blob", "--blob", "keep_a.py=mine.blob", "--", "keep_a.py"])
    check("--blob: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "blob")
    shown = git(root, ["show", ":keep_a.py"], private).stdout
    check("--blob stages the given content, not the working file",
          shown == "BLOB CONTENT\n", repr(shown))
    check("--blob leaves the working file alone",
          (root / "keep_a.py").read_text(encoding="utf-8") == "WORKTREE CONTENT\n")
    check("--blob leaves the working file as an unstaged modification",
          "keep_a.py" in names(root, env, cached=False) and "keep_a.py" not in names(root, env, cached=True))


def case_blob_keeps_head_mode(tmp):
    root, env = scene(tmp, "blobmode")
    git(root, ["update-index", "--chmod=+x", "--", "keep_a.py"], env)
    git(root, ["commit", "-q", "-m", "keep_a.py is executable"], env)
    (root / "mine.blob").write_text("BLOB CONTENT\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "blobmode", "--blob", "keep_a.py=mine.blob", "--", "keep_a.py"])
    check("--blob on a 100755 path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    listing = git(root, ["ls-files", "-s", "--", "keep_a.py"], idx_env(env, root, "blobmode")).stdout.split()
    check("--blob keeps HEAD's file mode 100755", listing[:1] == ["100755"], listing)


def case_no_commit_argv(tmp):
    control = Path(tmp) / "planted_commit_argv.py"
    control.write_text('import subprocess\nsubprocess.run(["git", "commit", "-m", "x"])\n',
                       encoding="utf-8", newline="\n")

    def commit_argv_literals(source):
        tree = ast.parse(source)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                if any(isinstance(e, ast.Constant) and e.value == "commit" for e in node.elts):
                    hits.append(getattr(node, "lineno", 0))
        return hits

    check("control: the detector finds a planted `commit` argv",
          commit_argv_literals(control.read_text(encoding="utf-8")) != [])
    source = TOOL.read_text(encoding="utf-8")
    check("the tool holds no `commit` argv in any list/tuple literal",
          commit_argv_literals(source) == [], commit_argv_literals(source))
    check("the tool never passes shell=True",
          not re.search(r"shell\s*=\s*True", source))
    check("the tool prints the GIT_INDEX_FILE commit command as text",
          "git commit -F" in source)


def case_usage_errors(tmp):
    root, env = scene(tmp, "usage")
    proc = tool(root, env, ["stage", "--name", "u", "--", "nope.py"])
    check("an unknown path exits non-zero with a named error",
          proc.returncode != 0 and "unknown path" in (proc.stdout + proc.stderr),
          proc.stdout + proc.stderr)
    proc = tool(root, env, ["stage", "--name", "u", "--", "../outside.py"])
    check("a path outside the repo exits non-zero with a named error",
          proc.returncode != 0 and "outside the repo" in (proc.stdout + proc.stderr),
          proc.stdout + proc.stderr)
    check("a refused stage discards the private index",
          not (root / ".git" / "index-u").exists())
    check("a refused stage stages nothing in the shared index",
          names(root, env, cached=True) == [],
          names(root, env, cached=True))

    (root / LOCK).write_text("{ not json\n", encoding="utf-8", newline="\n")
    (root / "keep_a.py").write_text("mine\n", encoding="utf-8", newline="\n")
    proc = tool(root, env, ["stage", "--name", "u", "--", "keep_a.py"])
    check("an unreadable lock exits non-zero with a named error",
          proc.returncode != 0 and "baseline.lock.json" in (proc.stdout + proc.stderr),
          proc.stdout + proc.stderr)
    check("nothing is staged in the shared index after the lock error",
          names(root, env, cached=True) == [], names(root, env, cached=True))


def case_entries_keep_modes_and_peer(tmp):
    root, env = scene(tmp, 'entries')
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    git(root, ['update-index', '--chmod=+x', '--', 'peer.py'], env)
    before_peer = git(root, ['ls-files', '-s', '--', 'peer.py'], env).stdout
    for mode, rel in [('100755', '.claude/tests/executable.py'), ('120000', '.claude/tests/link.py')]:
        (root / rel).write_text('working content differs\n', encoding='utf-8')
    proc = tool(root, env, ['stage', '--name', 'entries',
                           '--entry', '.claude/tests/executable.py=100755,' + sha,
                           '--entry', '.claude/tests/link.py=120000,' + sha,
                           '--entry', 'keep_a.py=100755,' + sha])
    check('--entry stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode:
        return
    private = idx_env(env, root, 'entries')
    for rel, mode in [('.claude/tests/executable.py', '100755'), ('.claude/tests/link.py', '120000'), ('keep_a.py', '100755')]:
        listing = git(root, ['ls-files', '-s', '--', rel], private).stdout.split()
        check('--entry keeps mode/blob for ' + rel, listing[:2] == [mode, sha], listing)
    commit = commit_index(root, env, 'entries', 'entries')
    check('--entry commit lands', commit.returncode == 0, commit.stderr)
    res = tool(root, env, ['resync', '--name', 'entries'])
    check('--entry resync succeeds', res.returncode == 0, res.stdout + res.stderr)
    check('unselected peer chmod survives resync', git(root, ['ls-files', '-s', '--', 'peer.py'], env).stdout == before_peer)


def case_resync_keeps_same_blob_peer_mode(tmp):
    root, env = scene(tmp, 'peermode')
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    proc = tool(root, env, ['stage', '--name', 'peermode', '--entry', 'keep_a.py=120000,' + sha])
    check('peer-mode stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode:
        return
    early = tool(root, env, ['resync', '--name', 'peermode'])
    check('same-blob changed mode is still an unlanded commit', early.returncode != 0 and 'did not land' in early.stderr, early.stdout + early.stderr)
    git(root, ['update-index', '--chmod=+x', '--', 'keep_a.py'], env)
    peer_before = git(root, ['ls-files', '-s', '--', 'keep_a.py'], env).stdout
    commit = commit_index(root, env, 'peermode', 'modes')
    check('peer-mode commit lands', commit.returncode == 0, commit.stderr)
    res = tool(root, env, ['resync', '--name', 'peermode'])
    check('peer-mode resync reports peer staging', res.returncode == 0 and 'left (peer-staged' in res.stdout, res.stdout + res.stderr)
    check('same-blob peer mode survives resync', git(root, ['ls-files', '-s', '--', 'keep_a.py'], env).stdout == peer_before)


def case_resync_nochange_entry(tmp):
    root, env = scene(tmp, 'nochange')
    (root / 'peer.py').write_text('previous committed change\n', encoding='utf-8')
    git(root, ['commit', '-q', '-m', 'previous', '--', 'peer.py'], env)
    sha = git(root, ['rev-parse', 'HEAD:keep_a.py'], env).stdout.strip()
    proc = tool(root, env, ['stage', '--name', 'nochange', '--entry', 'keep_a.py=100644,' + sha])
    check('unchanged entry stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
    res = tool(root, env, ['resync', '--name', 'nochange'])
    check('unchanged entry resync does not mistake HEAD parent for an intervening commit', res.returncode == 0, res.stdout + res.stderr)


FAILED_STAGE_DRIVER = '''
import json, sys
from pathlib import Path
sys.path.insert(0, '.claude/tools')
import index_commit
real = index_commit._classify

def peer_then_fail(root, index, paths):
    real(root, index, paths)
    lock_path = Path(root) / index_commit.LOCK_RELPATH
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    lock['files']['peer_after_start.py'] = {'status': 'local', 'peer': True}
    lock['files']['keep_b.py'] = {'status': 'local', 'changed_by_peer': True}
    lock_path.write_text(json.dumps(lock), encoding='utf-8', newline='\\n')
    raise index_commit.IndexCommitError('planted later stage failure')

index_commit._classify = peer_then_fail
sys.exit(index_commit.main(['stage', '--name', 'failedstage', '--', '.claude/tests/failedstage.py']))
'''


def case_failed_stage_keeps_peer_rows(tmp):
    root, env = scene(tmp, 'failedstage')
    (root / '.claude/tests/failedstage.py').write_text('x = 1\n', encoding='utf-8')
    run_env = dict(env, INDEX_COMMIT_CLASSIFY_CMD='python3 .claude/tools/classify_stub.py classify')
    proc = subprocess.run([sys.executable, '-c', FAILED_STAGE_DRIVER], cwd=root, env=run_env,
                          capture_output=True, text=True)
    check('later stage failure is reported', proc.returncode != 0 and 'planted later stage failure' in proc.stderr, proc.stdout + proc.stderr)
    rows = json.loads((root / LOCK).read_text(encoding='utf-8'))['files']
    check('failed stage keeps peer-added lock row', rows.get('peer_after_start.py') == {'status': 'local', 'peer': True}, rows)
    check('failed stage keeps peer-modified lock row', rows.get('keep_b.py') == {'status': 'local', 'changed_by_peer': True}, rows)
    check('failed stage keeps valid classification', '.claude/tests/failedstage.py' in rows, rows)
    check('failed stage cleans only private artifacts', not (root / '.git/index-failedstage').exists() and not (root / '.git/index-failedstage.paths.json').exists())
    check('failed stage leaves shared index untouched', names(root, env, cached=True) == [])


RESYNC_DRIVER = '''
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, '.claude/tools')
import index_commit
mode = sys.argv[1]
real_entries = index_commit._index_entries
real_git = index_commit._git
real_replace = os.replace
fired = []

def entries(root, paths, index=None):
    result = real_entries(root, paths, index=index)
    if mode == 'peer' and not fired:
        fired.append(True)
        Path('keep_a.py').write_text('peer at boundary\\n', encoding='utf-8')
        proc = subprocess.run(['git', 'add', '--', 'keep_a.py'], capture_output=True, text=True)
        print('PEER_RC', proc.returncode)
        print(proc.stderr)
    return result

def run_git(root, args, index=None, **kw):
    result = real_git(root, args, index=index, **kw)
    if mode == 'reset-failure' and args[0] == 'reset':
        raise index_commit.IndexCommitError('planted failure after reset before publish')
    return result

def replace(src, dst):
    if mode == 'publish-failure' and Path(dst).name == 'index':
        raise OSError('planted publication failure')
    return real_replace(src, dst)

index_commit._index_entries = entries
index_commit._git = run_git
index_commit.os.replace = replace
index_commit.INDEX_LOCK_TIMEOUT = 0.05
sys.exit(index_commit.main(['resync', '--name', 'transaction']))
'''


def case_resync_transaction(tmp):
    for mode in ('peer', 'reset-failure', 'publish-failure', 'timeout'):
        root, env = scene(tmp, 'transaction-' + mode)
        (root / 'keep_a.py').write_text('mine\n', encoding='utf-8')
        proc = tool(root, env, ['stage', '--name', 'transaction', '--', 'keep_a.py'])
        check(mode + ': stage succeeds', proc.returncode == 0, proc.stdout + proc.stderr)
        commit = commit_index(root, env, 'transaction', 'mine')
        check(mode + ': private commit lands', commit.returncode == 0, commit.stderr)
        index = root / '.git/index'
        lock = root / '.git/index.lock'
        before = index.read_bytes()
        if mode == 'timeout':
            lock.write_bytes(b'peer-owned lock')
        proc = subprocess.run([sys.executable, '-c', RESYNC_DRIVER, mode], cwd=root, env=env,
                              capture_output=True, text=True, timeout=30)
        detail = proc.stdout + proc.stderr
        if mode == 'peer':
            check('peer add at old boundary fails with Git index.lock error',
                  'PEER_RC 128' in detail and 'index.lock' in detail and 'File exists' in detail, detail)
            check('resync succeeds without silently clearing an accepted peer add',
                  proc.returncode == 0 and 'RESYNC OK' in detail
                  and blob_at(root, env, 'keep_a.py') == 'mine\n', detail)
        else:
            check(mode + ': failure is reported without traceback',
                  proc.returncode != 0 and 'error:' in detail and 'Traceback' not in detail
                  and ('index.lock' if mode == 'timeout' else 'planted') in detail, detail)
            check(mode + ': shared index remains byte-identical', index.read_bytes() == before)
        if mode == 'timeout':
            check('timeout leaves the peer lock untouched', lock.read_bytes() == b'peer-owned lock')
            lock.unlink()
        else:
            check(mode + ': owned index.lock and transaction copies are removed',
                  not lock.exists() and not list((root / '.git').glob('index-resync-*')))


def case_submodule_gitlink(tmp):
    root, env = scene(tmp, "gitlink")
    sub = root / "Paired"
    sub.mkdir()
    git(sub, ["init", "-q", "-b", "main"], env)
    (sub / "one.gd").write_text("one\n", encoding="utf-8", newline="\n")
    git(sub, ["add", "-A"], env)
    git(sub, ["commit", "-q", "-m", "sub one"], env)
    pinned = git(sub, ["rev-parse", "HEAD"], env).stdout.strip()
    git(root, ["update-index", "--add", "--cacheinfo", "160000,%s,Paired" % pinned], env)
    git(root, ["commit", "-q", "-m", "pin Paired"], env)
    (sub / "two.gd").write_text("two\n", encoding="utf-8", newline="\n")
    git(sub, ["add", "-A"], env)
    git(sub, ["commit", "-q", "-m", "sub two"], env)
    ahead = git(sub, ["rev-parse", "HEAD"], env).stdout.strip()

    proc = tool(root, env, ["stage", "--name", "gitlink", "--", "Paired"])
    check("submodule path: stage exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return
    private = idx_env(env, root, "gitlink")
    listing = git(root, ["ls-files", "-s", "--", "Paired"], private).stdout.split()
    check("private index holds the submodule as a gitlink at its checkout HEAD",
          listing[:2] == ["160000", ahead], listing)
    status = git(root, ["diff", "--cached", "--name-status"], private).stdout.split()
    check("stage deletes nothing: the only staged change is a modification of the gitlink",
          status == ["M", "Paired"], status)
    commit = commit_index(root, env, "gitlink", "move Paired")
    check("submodule path: the private-index commit lands", commit.returncode == 0, commit.stderr)
    landed = git(root, ["ls-tree", "HEAD", "--", "Paired"], env).stdout.split()
    check("HEAD holds the gitlink at the new sha", landed[:3] == ["160000", "commit", ahead], landed)


def case_submodule_without_checkout(tmp):
    root, env = scene(tmp, "nocheckout")
    git(root, ["update-index", "--add", "--cacheinfo", "160000,%s,Paired" % ("1" * 40)], env)
    git(root, ["commit", "-q", "-m", "pin Paired with no checkout"], env)
    proc = tool(root, env, ["stage", "--name", "nocheckout", "--", "Paired"])
    check("submodule without a checkout: stage refuses with a non-zero exit", proc.returncode == 2,
          proc.returncode)
    check("the refusal names the pathspec-commit route",
          "git commit -m <msg> -- Paired" in proc.stderr, proc.stderr)
    check("a refused submodule stage leaves no private index",
          not (root / ".git" / "index-nocheckout").exists(), "")


def main():
    if not TOOL.exists():
        print("FAIL tool missing: %s" % TOOL)
        return 1
    with tempfile.TemporaryDirectory(prefix="index_commit_proof_") as tmp:
        for case in (case_new_gitignored_file, case_deleted_path, case_forget_row_of_earlier_deletion,
                     case_lock_path_refused, case_peer_and_resync, case_changed_row_travels_with_its_path,
                     case_peer_staged_same_path, case_head_moved, case_resync_after_later_commit,
                     case_seeded_from_recorded_base, case_head_moved_lock_rows,
                     case_resync_before_commit, case_blob, case_blob_keeps_head_mode, case_no_commit_argv,
                     case_usage_errors, case_entries_keep_modes_and_peer, case_resync_keeps_same_blob_peer_mode,
                     case_resync_nochange_entry, case_failed_stage_keeps_peer_rows, case_resync_transaction,
                     case_submodule_gitlink, case_submodule_without_checkout):
            try:
                case(tmp)
            except Exception as exc:  # a case that cannot stage its fixture is a failure
                check("%s crashed" % case.__name__, False, "%r" % (exc,))
    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for line in failures:
        print("  " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
