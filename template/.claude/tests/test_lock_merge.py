#!/usr/bin/env python3
r"""Proof for tools/lock_merge.py, the merge driver for `.claude/baseline.lock.json`.

Every git merge runs in a throwaway repo under TMP with the driver registered through a temp
`git config` (never the worktree's or the shared clone's config). The end-to-end cases use a temp
bare remote and three temp clones.

Cases:
- two branches add different rows: the merge is clean and keeps both rows;
- one row changed to different values on both sides: exit 1, the side file is byte-intact;
- a top-level key changed on one side and a row added on the other: the merge is clean;
- a row deleted on one side and untouched on the other is deleted; deleted on one side and
  edited on the other is a conflict;
- a side or base file holding a duplicated row key exits 1 and names the key;
- the merged file has no repeated row key, keeps the rows both sides still hold or added, and drops the row one side deleted;
- `.gitattributes` declares the lock driver, and a jsonl union merge keeps both appends;
- index_commit writes the lock through the driver's serializer (`dump_lock`);
- end-to-end: a lane commit rebased onto a moved main with a different lock row is clean, and
  pushes to a land ref with no cherry-pick;
- a shared checkout with an untouched dirty file fast-forwards and keeps the file; one with a
  touched dirty file refuses and keeps its bytes.

    python3 .claude/tests/test_lock_merge.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DRIVER = REPO / ".claude" / "tools" / "lock_merge.py"
TOOLS = REPO / ".claude" / "tools"
LOCK = ".claude/baseline.lock.json"
DRIVER_CMD = "python3 %s %%O %%A %%B" % DRIVER.as_posix()
LOCK_ATTR = ".claude/baseline.lock.json merge=baseline-lock"

failures = []


def check(label, ok, detail=""):
    if ok:
        print("ok   %s" % label)
    else:
        failures.append(label)
        print("FAIL %s%s" % (label, (" -- " + detail) if detail else ""))


def git(cwd, *args, check_rc=True):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check_rc and proc.returncode != 0:
        raise RuntimeError("git %s failed: %s%s" % (" ".join(args), proc.stdout, proc.stderr))
    return proc


def lock_text(files, **top):
    obj = {"baseline_repo": "https://example.invalid/b.git", "baseline_ref": "main",
           "synced_commit": "a" * 40}
    obj.update(top)
    obj["files"] = files
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def row(hash_char):
    return {"status": "local", "hash": hash_char * 64}


def new_repo(tmp, base_text, name="repo"):
    repo = tmp / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "lock merge proof")
    git(repo, "config", "user.email", "proof@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "merge.baseline-lock.driver", DRIVER_CMD)
    (repo / ".gitattributes").write_text(LOCK_ATTR + "\n", encoding="utf-8", newline="\n")
    (repo / ".claude").mkdir()
    (repo / LOCK).write_text(base_text, encoding="utf-8", newline="\n")
    git(repo, "add", ".gitattributes", LOCK)
    git(repo, "commit", "-q", "-m", "base")
    return repo


def branch_merge(tmp, base_text, ours_text, theirs_text):
    """Merge `theirs` into `main` with the driver; return (rc, merged text, repo)."""
    repo = new_repo(tmp, base_text)
    git(repo, "checkout", "-q", "-b", "theirs")
    (repo / LOCK).write_text(theirs_text, encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "theirs")
    git(repo, "checkout", "-q", "main")
    (repo / LOCK).write_text(ours_text, encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "ours")
    rc = git(repo, "merge", "-q", "--no-edit", "theirs", check_rc=False).returncode
    return rc, (repo / LOCK).read_text(encoding="utf-8"), repo


def direct(tmp, base_text, ours_text, theirs_text):
    """Run the driver on three files the way git does; return (rc, stderr, ours bytes after)."""
    tmp.mkdir(parents=True, exist_ok=True)
    base = tmp / "O.json"
    ours = tmp / "A.json"
    theirs = tmp / "B.json"
    base.write_text(base_text, encoding="utf-8", newline="\n")
    ours.write_text(ours_text, encoding="utf-8", newline="\n")
    theirs.write_text(theirs_text, encoding="utf-8", newline="\n")
    proc = subprocess.run([sys.executable, str(DRIVER), str(base), str(ours), str(theirs)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stderr, ours.read_text(encoding="utf-8"), ours_text


BASE_FILES = {"keep_a.py": row("1"), "keep_b.py": row("2")}


def case_two_new_rows(tmp):
    base = lock_text(BASE_FILES)
    ours = lock_text({**BASE_FILES, "lane_x.py": row("3")})
    theirs = lock_text({**BASE_FILES, "lane_y.py": row("4")})
    rc, merged, _ = branch_merge(tmp / "c1", base, ours, theirs)
    rows = json.loads(merged).get("files", {}) if rc == 0 else {}
    check("two branches adding different rows merge clean and keep both",
          rc == 0 and "lane_x.py" in rows and "lane_y.py" in rows,
          "rc=%s" % rc)


def case_same_row_different_values(tmp):
    base = lock_text(BASE_FILES)
    ours = lock_text({**BASE_FILES, "keep_a.py": row("3")})
    theirs = lock_text({**BASE_FILES, "keep_a.py": row("4")})
    rc, stderr, after, original = direct(tmp / "c2", base, ours, theirs)
    check("same row, different values exits 1 with the side file byte-intact and no markers",
          rc == 1 and after == original and "<<<" not in after, "rc=%s" % rc)


def case_top_level_and_row_merge(tmp):
    base = lock_text(BASE_FILES)
    ours = lock_text({**BASE_FILES, "lane_x.py": row("3")})
    theirs = lock_text(BASE_FILES, synced_commit="b" * 40)
    rc, merged, _ = branch_merge(tmp / "c3", base, ours, theirs)
    obj = json.loads(merged) if rc == 0 else {}
    check("a top-level key changed on one side and a row added on the other merge clean",
          rc == 0 and obj.get("synced_commit") == "b" * 40 and "lane_x.py" in obj.get("files", {}),
          "rc=%s" % rc)


def case_row_deletions(tmp):
    base = lock_text(BASE_FILES)
    ours = lock_text({"keep_b.py": row("2")})
    theirs = lock_text(BASE_FILES)
    rc, stderr, after, original = direct(tmp / "c4a", base, ours, theirs)
    merged = {}
    if rc == 0:
        merged = json.loads(after)
    check("a row deleted on one side and untouched on the other stays deleted",
          rc == 0 and "keep_a.py" not in merged.get("files", {}) and "keep_b.py" in merged.get("files", {}),
          "rc=%s" % rc)
    edited = lock_text({**BASE_FILES, "keep_a.py": row("9")})
    rc2, _, after2, original2 = direct(tmp / "c4b", base, lock_text({"keep_b.py": row("2")}), edited)
    check("a row deleted on one side and edited on the other exits 1 with the side intact",
          rc2 == 1 and after2 == original2, "rc=%s" % rc2)


def case_duplicate_row_key(tmp):
    base = lock_text(BASE_FILES)
    dup_side = ('{"baseline_repo": "x", "baseline_ref": "main", "synced_commit": "%s", "files": {'
                '"keep_a.py": {"status": "local", "hash": "%s"}, '
                '"keep_a.py": {"status": "local", "hash": "%s"}}}\n') % ("a" * 40, "5" * 64, "6" * 64)
    rc, stderr, after, original = direct(tmp / "c5", base, dup_side, lock_text(BASE_FILES))
    check("a side file holding a duplicated row key exits 1 and names the key",
          rc == 1 and "keep_a.py" in stderr and after == original, "rc=%s stderr=%r" % (rc, stderr[:200]))


def case_merged_output_passes_guard_rules(tmp):
    base = lock_text(BASE_FILES)
    ours = lock_text({**BASE_FILES, "lane_x.py": row("3")})
    theirs = lock_text({"keep_b.py": row("2")}, synced_commit="c" * 40)
    rc, stderr, after, _ = direct(tmp / "c6", base, ours, theirs)
    sys.path.insert(0, str(REPO / ".claude" / "hooks"))
    import baseline_classification_guard as guard
    repeated = guard._repeated_lock_rows(after) if rc == 0 else ["rc=%s" % rc]
    kept = json.loads(after).get("files", {}) if rc == 0 else {}
    check("the merged lock has no repeated row key, keeps the surviving rows and drops the deleted one",
          rc == 0 and not repeated and "keep_b.py" in kept and "lane_x.py" in kept and "keep_a.py" not in kept,
          "rc=%s repeated=%r kept=%r" % (rc, repeated, sorted(kept)))


def case_gitattributes_and_jsonl_union(tmp):
    real = REPO / ".gitattributes"
    # The repo-root .gitattributes is each consumer's own file and never ships in the baseline.
    if real.is_file():
        check("the real .gitattributes declares the lock driver",
              LOCK_ATTR in real.read_text(encoding="utf-8").splitlines(), "missing %r" % LOCK_ATTR)
    repo = new_repo(tmp / "c7", lock_text(BASE_FILES))
    (repo / ".gitattributes").write_text(LOCK_ATTR + "\n.claude/escape_log.jsonl merge=union\n",
                                         encoding="utf-8", newline="\n")
    (repo / ".claude" / "escape_log.jsonl").write_text('{"n": 0}\n', encoding="utf-8", newline="\n")
    git(repo, "add", ".gitattributes", ".claude/escape_log.jsonl")
    git(repo, "commit", "-q", "-m", "ledger")
    git(repo, "checkout", "-q", "-b", "theirs")
    with open(repo / ".claude" / "escape_log.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"n": 2}\n')
    git(repo, "commit", "-q", "-am", "theirs append")
    git(repo, "checkout", "-q", "main")
    with open(repo / ".claude" / "escape_log.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write('{"n": 1}\n')
    git(repo, "commit", "-q", "-am", "ours append")
    rc = git(repo, "merge", "-q", "--no-edit", "theirs", check_rc=False).returncode
    lines = (repo / ".claude" / "escape_log.jsonl").read_text(encoding="utf-8").splitlines()
    check("a jsonl union merge keeps both appends", rc == 0 and '{"n": 1}' in lines and '{"n": 2}' in lines,
          "rc=%s lines=%r" % (rc, lines))


def case_index_commit_shares_lock_format(tmp):
    """Both tools write the whole lock; one serializer keeps a merge from re-formatting every row."""
    sys.path.insert(0, str(TOOLS))
    import index_commit
    import lock_merge
    check("index_commit writes the lock through the driver's serializer",
          getattr(index_commit, "dump_lock", None) is lock_merge.dump_lock)
    sample = {"files": {".claude/a.md": {"layer": "pure", "hash": "é"}}, "schema": 1}
    check("the serializer writes indent-2 UTF-8 JSON with one trailing newline",
          lock_merge.dump_lock(sample) == (json.dumps(sample, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def e2e_repos(root):
    """A bare remote, a seeded main, and two clones (`shared` tracks main; `lane` is cut from the seed)."""
    origin = root / "origin.git"
    root.mkdir(parents=True)
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = new_repo(root, lock_text(BASE_FILES), name="seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    base_sha = git(seed, "rev-parse", "HEAD").stdout.strip()
    clones = {}
    for name in ("shared", "lane"):
        clone = root / name
        git(root, "clone", "-q", str(origin), str(clone))
        git(clone, "config", "user.name", "lock merge proof")
        git(clone, "config", "user.email", "proof@example.invalid")
        git(clone, "config", "merge.baseline-lock.driver", DRIVER_CMD)
        clones[name] = clone
    return origin, clones["shared"], clones["lane"], base_sha


def case_lane_rebase_and_land(tmp):
    origin, shared, lane, base_sha = e2e_repos(tmp / "e2e")
    # Main moves: another lane landed a row first.
    (shared / LOCK).write_text(lock_text({**BASE_FILES, "landed_p.py": row("7")}), encoding="utf-8", newline="\n")
    git(shared, "commit", "-q", "-am", "main gains a row")
    git(shared, "push", "-q", "origin", "main")
    # The lane was cut before that landing and adds its own row.
    git(lane, "checkout", "-q", "-b", "lane", base_sha)
    (lane / LOCK).write_text(lock_text({**BASE_FILES, "lane_l.py": row("8")}), encoding="utf-8", newline="\n")
    git(lane, "commit", "-q", "-am", "lane adds a row")
    git(lane, "fetch", "-q", "origin")
    rebase = git(lane, "rebase", "origin/main", check_rc=False)
    merged = {}
    if rebase.returncode == 0:
        merged = json.loads((lane / LOCK).read_text(encoding="utf-8")).get("files", {})
    else:
        git(lane, "rebase", "--abort", check_rc=False)
    check("a lane rebased onto a moved main with a different lock row is clean",
          rebase.returncode == 0 and "landed_p.py" in merged and "lane_l.py" in merged,
          "rc=%s out=%s" % (rebase.returncode, (rebase.stdout + rebase.stderr)[-300:]))
    if rebase.returncode != 0:
        check("the lane commit reaches the land ref with no cherry-pick", False, "skipped: rebase refused")
        return
    lane_sha = git(lane, "rev-parse", "HEAD").stdout.strip()
    pushed = git(lane, "push", "-q", "--recurse-submodules=check", "origin", "HEAD:land/proof", check_rc=False)
    remote = git(origin, "rev-parse", "land/proof", check_rc=False).stdout.strip()
    check("the rebased lane commit reaches the land ref with no cherry-pick",
          pushed.returncode == 0 and remote == lane_sha, "push rc=%s" % pushed.returncode)


def case_shared_catch_up(tmp):
    """`merge --ff-only` on a shared checkout: an untouched dirty file survives; a touched one refuses."""
    origin, shared, lane, base_sha = e2e_repos(tmp / "ff")
    (shared / "notes.txt").write_text("base\n", encoding="utf-8", newline="\n")
    git(shared, "add", "notes.txt")
    git(shared, "commit", "-q", "-m", "notes")
    git(shared, "push", "-q", "origin", "main")
    (shared / "notes.txt").write_text("peer edit\n", encoding="utf-8", newline="\n")
    git(lane, "fetch", "-q", "origin")
    git(lane, "checkout", "-q", "--detach", "origin/main")
    (lane / "other.txt").write_text("x\n", encoding="utf-8", newline="\n")
    git(lane, "add", "other.txt")
    git(lane, "commit", "-q", "-m", "unrelated")
    untouched_sha = git(lane, "rev-parse", "HEAD").stdout.strip()
    git(lane, "push", "-q", "origin", "HEAD:refs/heads/untouched")
    git(shared, "fetch", "-q", "origin")
    ff = git(shared, "merge", "--ff-only", untouched_sha, check_rc=False)
    check("a shared checkout with an untouched dirty file fast-forwards and keeps the file",
          ff.returncode == 0 and (shared / "notes.txt").read_text(encoding="utf-8") == "peer edit\n",
          "rc=%s" % ff.returncode)
    (lane / "notes.txt").write_text("touched by lane\n", encoding="utf-8", newline="\n")
    git(lane, "add", "notes.txt")
    git(lane, "commit", "-q", "-m", "touch notes")
    touched_sha = git(lane, "rev-parse", "HEAD").stdout.strip()
    git(lane, "push", "-q", "origin", "HEAD:refs/heads/touch-notes")
    git(shared, "fetch", "-q", "origin")
    refused = git(shared, "merge", "--ff-only", touched_sha, check_rc=False)
    check("a shared checkout with a touched dirty file refuses and keeps its bytes",
          refused.returncode != 0 and (shared / "notes.txt").read_text(encoding="utf-8") == "peer edit\n",
          "rc=%s" % refused.returncode)


CASES = [
    case_two_new_rows,
    case_same_row_different_values,
    case_top_level_and_row_merge,
    case_row_deletions,
    case_duplicate_row_key,
    case_merged_output_passes_guard_rules,
    case_gitattributes_and_jsonl_union,
    case_index_commit_shares_lock_format,
    case_lane_rebase_and_land,
    case_shared_catch_up,
]


def main():
    tmp_root = Path(tempfile.mkdtemp(prefix="lock_merge_proof_"))
    try:
        for case in CASES:
            try:
                case(tmp_root)
            except Exception as exc:
                check(case.__name__, False, "CRASH %s: %s" % (type(exc).__name__, exc))
                traceback.print_exc()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    if failures:
        print("FAILED %d case(s)" % len(failures))
        return 1
    print("ok all cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
