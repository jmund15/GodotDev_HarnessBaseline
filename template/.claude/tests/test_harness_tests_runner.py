"""Re-runnable proof for scripts/harness_tests.py.

Each case builds an isolated temp git repo shaped like the real `.claude/` tree, points
the runner at it via `run_all(repo_root)`, and asserts on the exit code and the stamp
file — RED before the runner exists.

    python3 .claude/tests/test_harness_tests_runner.py
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unittest  # noqa: E402
from _harness_repo_case import RepoCase  # noqa: E402



def make_repo():
    repo = tempfile.mkdtemp(prefix="harnesstests_")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    for d in ("hooks", "tools", "scripts", "workflows", "tests"):
        os.makedirs(os.path.join(repo, ".claude", d), exist_ok=True)
    with open(os.path.join(repo, ".claude", "settings.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{}\n")
    return repo


def seed_commit(repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def write_proof(repo, name, exit_code):
    path = os.path.join(repo, ".claude", "tests", name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(%d)\n" % exit_code)
    return path


def case_pass_and_fail(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    write_proof(repo, "test_fail.py", 1)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")

    rc = harness_tests.run_all(repo)
    ok = rc == 1 and not os.path.exists(harness_tests.STAMP_PATH)
    print("%-4s one pass + one fail proof -> exit 1, no stamp" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("one pass + one fail: rc=%s stamp_exists=%s" % (rc, os.path.exists(harness_tests.STAMP_PATH)))


def case_all_pass(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    stamp = os.path.join(repo, "stamp.json")
    harness_tests.STAMP_PATH = stamp

    rc = harness_tests.run_all(repo)
    stamp_ok = os.path.isfile(stamp)
    hash_matches = False
    if stamp_ok:
        with open(stamp, encoding="utf-8") as fh:
            data = json.load(fh)
        hash_matches = data.get("tree_hash") == harness_tests.tree_hash(repo)

    ok = rc == 0 and stamp_ok and hash_matches
    print("%-4s removing the failing proof -> exit 0, stamp tree_hash == tree_hash()" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("all pass: rc=%s stamp_ok=%s hash_matches=%s" % (rc, stamp_ok, hash_matches))


def case_zero_proofs(failures):
    """No proof discovered -> exit 1, and the output names the directory it searched."""
    import contextlib
    import io
    repo = make_repo()
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = harness_tests.run_all(repo)
    out = buf.getvalue()
    tests_dir = os.path.join(repo, ".claude", "tests")
    ok = rc == 1 and not os.path.exists(harness_tests.STAMP_PATH) and "no proofs" in out and tests_dir in out
    print("%-4s zero proofs -> exit 1, output names the searched directory" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("zero proofs: rc=%s out=%r" % (rc, out[-300:]))


def case_absent_excluded_is_skipped(failures):
    """An EXCLUDED name with no file on disk is reported and skipped; discovery still runs (the excluded
    proofs are untracked, so a worktree never has them)."""
    import contextlib
    import io
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {"test_ghost.ps1": "does not exist"}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = harness_tests.run_all(repo)
    out = buf.getvalue()
    ok = (rc == 0 and os.path.isfile(harness_tests.STAMP_PATH)
          and "absent EXCLUDED entry: test_ghost.ps1" in out and "1 run, 1 pass" in out)
    print("%-4s absent EXCLUDED entry -> reported, skipped, other proofs run" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("absent excluded: rc=%s out=%r" % (rc, out[-300:]))


def case_ignored_proof_changes_hash(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(".claude/tests/\n")
    seed_commit(repo)
    before = harness_tests.tree_hash(repo)
    write_proof(repo, "test_new_ignored.py", 0)
    after = harness_tests.tree_hash(repo)
    ok = before != after
    print("%-4s a new proof under a gitignored tests/ dir changes tree_hash" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("ignored proof: hash unchanged (%s)" % before[:12])


def case_data_file_does_not_change_hash(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    seed_commit(repo)
    before = harness_tests.tree_hash(repo)
    data = os.path.join(repo, ".claude", "scripts", "campaign.env")
    with open(data, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("ARM=x\n")
    after_data = harness_tests.tree_hash(repo)
    code = os.path.join(repo, ".claude", "scripts", "tool.py")
    with open(code, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("x = 1\n")
    after_code = harness_tests.tree_hash(repo)
    ok = before == after_data and after_code != before
    print("%-4s a data file under scripts/ leaves tree_hash unchanged; a .py changes it" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("data vs code: data_changed=%s code_changed=%s" % (before != after_data, after_code != before))


def case_failure_output_surfaces(failures):
    """A red proof's own output reaches the runner's stdout — the gate names its reason."""
    import contextlib
    import io
    repo = make_repo()
    path = os.path.join(repo, ".claude", "tests", "test_loud.py")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("print('ASSERTION-MARKER-7f3a')\nimport sys\nsys.exit(1)\n")
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = harness_tests.run_all(repo)
    out = buf.getvalue()
    ok = rc == 1 and "ASSERTION-MARKER-7f3a" in out and "FAIL" in out
    print("%-4s a failing proof's output is printed under its FAIL line" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("failure output: rc=%s out=%r" % (rc, out[-300:]))


def case_git_failure_raises(failures):
    """A failed git enumeration must never produce a stampable hash."""
    not_repo = tempfile.mkdtemp(prefix="harnesstests_norepo_")
    os.makedirs(os.path.join(not_repo, ".claude", "tests"))
    try:
        harness_tests.tree_hash(not_repo)
        ok = False
    except RuntimeError:
        ok = True
    print("%-4s tree_hash outside a git repo raises instead of hashing nothing" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("tree_hash on a non-repo returned a hash")


def case_snapshot_precedes_proof_selection(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    stamp = os.path.join(repo, "stamp.json")
    harness_tests.STAMP_PATH = stamp
    original = harness_tests.tree_entries
    injected = False

    def inject_during_first_snapshot(root):
        nonlocal injected
        if not injected:
            injected = True
            write_proof(repo, "test_late_fail.py", 1)
        return original(root)

    harness_tests.tree_entries = inject_during_first_snapshot
    try:
        rc = harness_tests.run_all(repo)
    finally:
        harness_tests.tree_entries = original
    ok = rc == 1 and not os.path.exists(stamp)
    print("%-4s G1 snapshot happens before proof selection, so a late proof cannot earn a green stamp"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("G1 snapshot ordering: rc=%s stamp_exists=%s" % (rc, os.path.exists(stamp)))


def case_peer_edit_mid_run_stamps_the_unchanged_files(failures):
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    peer = os.path.join(repo, ".claude", "tools", "peer.py")
    with open(peer, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("x = 1\n")
    with open(os.path.join(repo, ".claude", "tests", "test_peer_edit.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("open(%r, 'a', encoding='utf-8').write('y = 2\\n')\n" % peer)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    stamp = os.path.join(repo, "stamp.json")
    harness_tests.STAMP_PATH = stamp
    rc = harness_tests.run_all(repo)
    files = {}
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as fh:
            files = json.load(fh).get("files") or {}
    ok = (rc == 1 and ".claude/tools/peer.py" not in files and ".claude/tests/test_pass.py" in files
          and ".claude/settings.json" in files)
    print("%-4s a peer edit during the run exits 1 and leaves only that file unstamped; the unchanged files keep their stamp"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("peer edit mid-run: rc=%s stamped=%s" % (rc, sorted(files)))


def case_a_scoped_stamp_landed_mid_run_survives_the_full_stamp(failures):
    """A scoped run that certifies a file the full run saw change must keep its newer claim."""
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    peer = os.path.join(repo, ".claude", "tools", "peer.py")
    with open(peer, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("x = 1\n")
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    stamp = os.path.join(repo, "stamp.json")
    harness_tests.STAMP_PATH = stamp
    real = harness_tests._run_selected

    def peer_certifies_during_the_run(*args, **kwargs):
        result = real(*args, **kwargs)
        with open(peer, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("y = 2\n")
        newer = harness_tests.tree_entries(repo)[".claude/tools/peer.py"]
        sys.path.insert(0, os.path.join(_SCRIPTS_DIR, "..", "hooks"))
        from _hook_state import update_json_locked

        def merge(doc):
            doc.setdefault("files", {})[".claude/tools/peer.py"] = newer
        update_json_locked(stamp, merge, raising=True)
        return result

    harness_tests._run_selected = peer_certifies_during_the_run
    try:
        rc = harness_tests.run_all(repo)
    finally:
        harness_tests._run_selected = real
    files = {}
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as fh:
            files = json.load(fh).get("files") or {}
    ok = (rc == 1 and files.get(".claude/tools/peer.py") == harness_tests.tree_entries(repo)[".claude/tools/peer.py"]
          and ".claude/tests/test_pass.py" in files)
    print("%-4s a scoped claim certified mid-run at the file's newer bytes survives the full run's stamp"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped claim erased by the full stamp: rc=%s peer=%s" % (rc, files.get(".claude/tools/peer.py")))


def case_head_move_touching_no_input_keeps_the_full_stamp(failures):
    """A peer commit during the run that touches no stamped input (docs, ledgers, benchmark results) moves
    HEAD and nothing else. The per-file check is the judge: every input is unchanged, so the run is green
    and the full stamp is written."""
    repo = make_repo()
    write_proof(repo, "test_pass.py", 0)
    doc = os.path.join(repo, "notes.md")
    with open(doc, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("v1\n")
    with open(os.path.join(repo, ".claude", "tests", "test_peer_commit.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import subprocess\n"
                 "open(%r, 'a', encoding='utf-8').write('v2\\n')\n"
                 "subprocess.run(['git', 'add', 'notes.md'], cwd=%r, check=True)\n"
                 "subprocess.run(['git', 'commit', '-q', '-m', 'docs'], cwd=%r, check=True)\n" % (doc, repo, repo))
    seed_commit(repo)
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    harness_tests.EXCLUDED = {}
    stamp = os.path.join(repo, "stamp.json")
    harness_tests.STAMP_PATH = stamp
    rc = harness_tests.run_all(repo)
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    data = {}
    if os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as fh:
            data = json.load(fh)
    files = data.get("files") or {}
    ok = (head_before != head_after and rc == 0 and ".claude/tests/test_pass.py" in files
          and ".claude/tests/test_peer_commit.py" in files and data.get("head") == head_before)
    print("%-4s a HEAD move mid-run that touches no input keeps the full stamp (exit 0, head = the tested one)"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("head move mid-run: rc=%s moved=%s stamped=%s head=%s" % (rc, head_before != head_after, sorted(files), data.get("head")))


def _scoped_repo():
    """hooks/alpha.py with a name-bound proof; hooks/beta.py with a proof that only mentions it by
    basename; tools/gamma.py with no proof; a dir-scanning proof (test_hook_state.py) that must run
    on every scoped run; an unrelated proof that must not."""
    repo = make_repo()
    for rel in ("hooks/alpha.py", "hooks/beta.py", "tools/gamma.py"):
        with open(os.path.join(repo, ".claude", rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
    write_proof(repo, "test_alpha.py", 0)
    with open(os.path.join(repo, ".claude", "tests", "test_beta_behaviour.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\n# exercises beta.py through its stdin contract\nsys.exit(0)\n")
    write_proof(repo, "test_hook_state.py", 0)
    write_proof(repo, "test_unrelated.py", 1)  # RED if it is ever selected for a scoped run
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    return repo


def case_scoped_run_selects_bound_proofs_only(failures):
    repo = _scoped_repo()
    assert harness_tests.run_all(repo) == 1  # the unrelated proof is red: no full stamp
    # a scoped run needs a base stamp: fake one from the current tree (as a green full run would leave)
    harness_tests._write_stamp(repo, 4, 4, [])
    selected = harness_tests.select_for(repo, [".claude/hooks/alpha.py", ".claude/hooks/beta.py"])
    names = sorted(os.path.basename(p) for p in selected)
    ok = names == ["test_alpha.py", "test_beta_behaviour.py", "test_hook_state.py"]
    print("%-4s scoped selection: name-bound, mention-bound and dir-scanning proofs, nothing else" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped selection: %s" % names)


def case_scoped_run_refreshes_only_the_touched_entries(failures):
    repo = _scoped_repo()
    harness_tests._write_stamp(repo, 4, 4, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))
    with open(os.path.join(repo, ".claude", "hooks", "alpha.py"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("Y = 2\n")
    with open(os.path.join(repo, ".claude", "tools", "gamma.py"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("Y = 2\n")  # changed but NOT in the scope: must stay at its old digest
    rc = harness_tests.run_scoped(repo, [".claude/hooks/alpha.py"])
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))
    cur = harness_tests.tree_entries(repo)
    ok = (rc == 0 and after["files"][".claude/hooks/alpha.py"] == cur[".claude/hooks/alpha.py"]
          and after["files"][".claude/tools/gamma.py"] == before["files"][".claude/tools/gamma.py"]
          and after["files"][".claude/tools/gamma.py"] != cur[".claude/tools/gamma.py"]
          and after.get("scoped", {}).get("paths") == [".claude/hooks/alpha.py"]
          and after["tree_hash"] != before["tree_hash"])
    print("%-4s scoped run refreshes the touched entry, leaves the untouched-but-changed entry stale, records its scope" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped refresh: rc=%s alpha=%s gamma_kept=%s scoped=%s" % (
            rc, after["files"].get(".claude/hooks/alpha.py") == cur.get(".claude/hooks/alpha.py"),
            after["files"].get(".claude/tools/gamma.py") == before["files"].get(".claude/tools/gamma.py"), after.get("scoped")))


def case_scoped_run_with_a_red_proof_stamps_nothing(failures):
    repo = _scoped_repo()
    harness_tests._write_stamp(repo, 4, 4, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))
    write_proof(repo, "test_alpha.py", 1)
    with open(os.path.join(repo, ".claude", "hooks", "alpha.py"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("Y = 2\n")
    rc = harness_tests.run_scoped(repo, [".claude/hooks/alpha.py"])
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))
    ok = rc == 1 and after["files"] == before["files"]
    print("%-4s scoped run with a red proof leaves the stamp untouched" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped red: rc=%s files_changed=%s" % (rc, after["files"] != before["files"]))


def case_scoped_run_without_a_base_stamp_records_a_scoped_one(failures):
    """A fresh worktree -- the PR-sync job's -- has no full-run stamp, so refusing here made the
    commit guard's own prescribed `--staged` step impossible to follow there. The scoped run now
    issues a stamp holding exactly the in-scope paths, which is all the guard's per-file check reads."""
    repo = _scoped_repo()
    if os.path.exists(harness_tests.STAMP_PATH):
        os.remove(harness_tests.STAMP_PATH)
    rc, out = _run_quiet(harness_tests.run_scoped, repo, [".claude/hooks/alpha.py"])
    data = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8")) if os.path.exists(harness_tests.STAMP_PATH) else {}
    files = data.get("files") or {}
    ok = (rc == 0 and list(files) == [".claude/hooks/alpha.py"] and bool(data.get("tree_hash"))
          and data.get("scoped", {}).get("paths") == [".claude/hooks/alpha.py"])
    print("%-4s scoped run with no base stamp writes one scoped to exactly the given paths" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped no-base: rc=%s files=%s hash=%s out=%r"
                        % (rc, sorted(files), bool(data.get("tree_hash")), out[-300:]))


def case_proof_does_not_inherit_git_repo_env(failures):
    """A proof's temp-repo fixture must not see the caller's private commit index.

    With GIT_INDEX_FILE inherited, a fixture's `git add` rewrote the caller's index
    (gotcha_concurrent_session_hazards #16)."""
    repo = make_repo()
    path = os.path.join(repo, ".claude", "tests", "test_env.py")
    # The runner's own command-scope config (GIT_CONFIG_*) is no caller value; every other local name locates a repo.
    keys = tuple(k for k in (harness_tests.GIT_REPO_ENV if hasattr(harness_tests, "GIT_REPO_ENV") else (
        "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"))
        if not k.startswith("GIT_CONFIG"))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nsys.exit(1 if any(k in os.environ for k in %r) else 0)\n" % (tuple(keys),))
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["GIT_INDEX_FILE"] = os.path.join(repo, "private.index")
    os.environ["GIT_WORK_TREE"] = repo
    try:
        status, _, detail = harness_tests.run_proof(path)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ok = status == "pass"
    print("%-4s proof runs without the caller's GIT_INDEX_FILE / GIT_WORK_TREE" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("git repo env leaked into proof: status=%s %s" % (status, detail[:200]))


def _run_proof_with_env(path, env_set):
    """run_proof with `env_set` applied to os.environ for the call, then restored."""
    saved = {k: os.environ.get(k) for k in env_set}
    for k, v in env_set.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        return harness_tests.run_proof(path)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def case_proof_drops_every_git_local_env_var(failures):
    """The stripped list is git's own (`git rev-parse --local-env-vars`), not a hand-picked five:
    GIT_CONFIG_PARAMETERS (what `git -c` exports) must not reach a proof's fixture either."""
    repo = make_repo()
    path = os.path.join(repo, ".claude", "tests", "test_cfg_env.py")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nsys.exit(1 if 'GIT_CONFIG_PARAMETERS' in os.environ else 0)\n")
    status, _, detail = _run_proof_with_env(path, {"GIT_CONFIG_PARAMETERS": "'core.bare=true'"})
    ok = status == "pass"
    print("%-4s proof runs without the caller's GIT_CONFIG_PARAMETERS" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("GIT_CONFIG_PARAMETERS leaked into proof: status=%s %s" % (status, detail[:200]))


def case_local_env_vars_come_from_git_with_a_fallback(failures):
    """The list is git's authoritative one; a git that cannot answer yields the 15 known names."""
    derive = getattr(harness_tests, "git_local_env_vars", lambda: ())
    derived = derive()
    real_run = harness_tests.subprocess.run

    def broken(*_a, **_k):
        raise OSError("no git")

    harness_tests.subprocess.run = broken
    try:
        fallback = derive()
    finally:
        harness_tests.subprocess.run = real_run
    ok = ("GIT_CONFIG_PARAMETERS" in derived and "GIT_INDEX_FILE" in derived and len(fallback) == 15
          and "GIT_CONFIG_PARAMETERS" in fallback and "GIT_SHALLOW_FILE" in fallback)
    print("%-4s git_local_env_vars asks git and falls back to the 15 names" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("local env vars: derived=%s fallback=%s" % (derived, fallback))


def case_proof_receives_the_stamp_index(failures):
    """A proof that reads the REAL repo's index gets the caller's private index as
    HARNESS_STAMP_INDEX, while GIT_INDEX_FILE itself stays stripped."""
    repo = make_repo()
    index = os.path.join(repo, "private.index")
    path = os.path.join(repo, ".claude", "tests", "test_stamp_index.py")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nok = os.environ.get('HARNESS_STAMP_INDEX') == %r and 'GIT_INDEX_FILE' not in os.environ\n"
                 "sys.exit(0 if ok else 1)\n" % index)
    status, _, detail = _run_proof_with_env(path, {"GIT_INDEX_FILE": index})
    absent_path = os.path.join(repo, ".claude", "tests", "test_no_stamp_index.py")
    with open(absent_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nsys.exit(1 if 'HARNESS_STAMP_INDEX' in os.environ else 0)\n")
    absent_status, _, _ = _run_proof_with_env(absent_path, {"GIT_INDEX_FILE": None, "HARNESS_STAMP_INDEX": "stale"})
    ok = status == "pass" and absent_status == "pass"
    print("%-4s run_proof passes the caller's GIT_INDEX_FILE as HARNESS_STAMP_INDEX, and none without one"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("stamp index: with=%s without=%s %s" % (status, absent_status, detail[:200]))


def case_a_touched_path_binds_its_own_proofs_not_its_importers(failures):
    """A touched tool binds its name, mention and declared proofs; the proofs of the modules that import
    it (directly, in a function, or through a `_` hooks helper) stay unselected. A declared binding
    replaces only the touched path's own mention rule."""
    repo = make_repo()
    sources = {
        "tools/a.py": "X = 1\n",
        "hooks/b.py": "def load():\n    from a import X\n",
        "hooks/_helper.py": "import a\n",
        "hooks/via_helper.py": "import _helper\n",
    }
    for rel, text in sources.items():
        with open(os.path.join(repo, ".claude", rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    for name, text in (("test_a_core.py", ""), ("test_b.py", "B = 'b.py'\n"), ("test_mentions_alpha.py", "A = 'a.py'\n"),
                       ("test_declared.py", ""), ("test_via_helper.py", ""), ("test_other_bystander.py", "")):
        with open(os.path.join(repo, ".claude", "tests", name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    touched = ".claude/tools/a.py"
    names = sorted(os.path.basename(p) for p in harness_tests.bindings(repo, [touched])[touched])
    saved = dict(harness_tests.PROOF_BINDINGS)
    harness_tests.PROOF_BINDINGS[touched] = ["test_declared.py"]
    try:
        declared = sorted(os.path.basename(p) for p in harness_tests.bindings(repo, [touched])[touched])
    finally:
        harness_tests.PROOF_BINDINGS.clear()
        harness_tests.PROOF_BINDINGS.update(saved)
    ok = (names == ["test_a_core.py", "test_mentions_alpha.py"]
          and declared == ["test_a_core.py", "test_declared.py"])
    print("%-4s a touched path binds its name, mention and declared proofs, never an importer's"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("own proofs only: fixture=%s declared=%s" % (names, declared))


def case_settings_and_jsonl_paths_bind_only_their_own_proofs(failures):
    """A settings file binds the proofs that name it, not every proof that names a sibling settings file;
    a `.jsonl` record binds no proof by name or mention."""
    repo = make_repo()
    for rel, text in ((".claude/settings.base.json", "{}\n"), (".claude/orchestration_metrics.jsonl", "{}\n")):
        with open(os.path.join(repo, *rel.split("/")), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    for name, text in (("test_names_base.py", "S = 'settings.base.json'\n"), ("test_names_main.py", "S = 'settings.json'\n"),
                       ("test_orchestration_metrics.py", ""), ("test_reads_metrics.py", "M = 'orchestration_metrics.jsonl'\n")):
        with open(os.path.join(repo, ".claude", "tests", name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    bound = harness_tests.bindings(repo, [".claude/settings.base.json", ".claude/orchestration_metrics.jsonl"])
    got = {t: sorted(os.path.basename(p) for p in paths) for t, paths in bound.items()}
    ok = (got[".claude/settings.base.json"] == ["test_names_base.py"]
          and got[".claude/orchestration_metrics.jsonl"] == [])
    print("%-4s a settings file binds the proofs naming it; a .jsonl record binds none" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("settings/jsonl binding: %s" % got)


def _leak_probe(repo, name, marker, tail):
    """A planted proof that creates a temp folder, records its path in `marker`, then runs `tail`."""
    path = os.path.join(repo, ".claude", "tests", name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys, tempfile, time\n"
                 "d = tempfile.mkdtemp(prefix='leakprobe_')\n"
                 "open(os.path.join(d, 'f.txt'), 'w').write('x')\n"
                 "open(%r, 'w', encoding='utf-8').write(d)\n%s\n" % (marker, tail))
    return path


def case_proof_temp_folders_do_not_reach_the_real_temp(failures):
    """A proof's mkdtemp lands in a per-proof temp root the runner deletes on every exit path,
    a timeout included, so nothing the proof created is left in the runner's own TEMP."""
    repo = make_repo()
    real_temp = tempfile.gettempdir()
    before = {n for n in os.listdir(real_temp) if n.startswith("leakprobe_")}
    passing_marker = os.path.join(repo, "passing.txt")
    hanging_marker = os.path.join(repo, "hanging.txt")
    _leak_probe(repo, "test_leaky.py", passing_marker, "sys.exit(0)")
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        rc = harness_tests.run_all(repo)
    hanging = _leak_probe(repo, "test_hanging.py", hanging_marker, "time.sleep(60)")
    status, _, _ = harness_tests.run_proof(hanging, timeout=5)
    created = []
    for marker in (passing_marker, hanging_marker):
        if os.path.isfile(marker):
            with open(marker, encoding="utf-8") as fh:
                created.append(fh.read().strip())
    after = {n for n in os.listdir(real_temp) if n.startswith("leakprobe_")}
    leaked = sorted(after - before)
    survivors = [d for d in created if os.path.exists(d)]
    ok = rc == 0 and status == "fail" and len(created) == 2 and not leaked and not survivors
    print("%-4s a proof's temp folder is gone after the run, a timed-out proof's too; the real TEMP gains nothing"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("temp leak: rc=%s timeout_status=%s created=%s leaked=%s survivors=%s"
                        % (rc, status, created, leaked, survivors))
    for name in leaked:
        import shutil
        shutil.rmtree(os.path.join(real_temp, name), ignore_errors=True)


def _heavy_repo():
    """tools/sub.py is bound only to the heavy proof test_h.py (by mention); tools/other.py is bound
    to nothing; test_pass.py is an ordinary green proof. test_h.py writes a marker when it runs."""
    repo = make_repo()
    for rel in ("tools/sub.py", "tools/other.py"):
        with open(os.path.join(repo, ".claude", rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
    write_proof(repo, "test_pass.py", 0)
    marker = os.path.join(repo, "heavy-ran.txt")
    with open(os.path.join(repo, ".claude", "tests", "test_h.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nSUB = 'sub.py'\nopen(%r, 'w').write('ran')\nsys.exit(0)\n" % marker)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.HEAVY = {"test_h.py": "slow"}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    return repo, marker


def _run_quiet(fn, *args, **kwargs):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


def _stamped_files():
    with open(harness_tests.STAMP_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("files") or {}


def case_mjs_proofs_are_discovered_and_run_by_node(failures):
    repo = make_repo()
    tests = os.path.join(repo, ".claude", "tests")
    for name, body in (("test_x.mjs", "test('x', () => {})"), ("test_y.mjs", "test('y', () => { throw new Error('planted') })")):
        with open(os.path.join(tests, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("import test from 'node:test'\n%s\n" % body)
    found = sorted(os.path.basename(p) for p in harness_tests.discover(tests))
    green = harness_tests.run_proof(os.path.join(tests, "test_x.mjs"))[0] if "test_x.mjs" in found else None
    red = harness_tests.run_proof(os.path.join(tests, "test_y.mjs"))[0] if "test_y.mjs" in found else None
    ok = green == "pass" and red == "fail" and harness_tests._is_code(".claude/tools/shim.mjs")
    print("%-4s a test_*.mjs proof is discovered and run: a green one passes, a throwing one fails; .mjs is harness code"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("mjs: found=%s green=%s red=%s" % (found, green, red))


def case_heavy_proof_runs_when_bound(failures):
    repo, _marker = _heavy_repo()
    names = sorted(os.path.basename(p) for p in harness_tests.select_for(repo, [".claude/tools/sub.py"]))
    ok = "test_h.py" in names
    print("%-4s a heavy proof is selected for a staged run when its file is touched" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("heavy bound: %s" % names)


def case_default_battery_skips_heavy_and_leaves_its_inputs_unstamped(failures):
    repo, marker = _heavy_repo()
    rc, out = _run_quiet(harness_tests.run_all, repo)
    files = _stamped_files() if os.path.isfile(harness_tests.STAMP_PATH) else {}
    ok = (rc == 0 and not os.path.exists(marker) and "SKIP heavy: test_h.py" in out and "1 heavy skipped" in out
          and ".claude/tools/other.py" in files and ".claude/tools/sub.py" not in files
          and ".claude/tests/test_h.py" not in files)
    print("%-4s default battery: heavy proof skipped, its bound inputs unstamped, others stamped" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("default heavy: rc=%s ran=%s files=%s out=%r"
                        % (rc, os.path.exists(marker), sorted(files), out[-300:]))


def case_all_runs_heavy_and_stamps_its_inputs(failures):
    repo, marker = _heavy_repo()
    rc, out = _run_quiet(harness_tests.run_all, repo, include_excluded=True)
    files = _stamped_files() if os.path.isfile(harness_tests.STAMP_PATH) else {}
    ok = rc == 0 and os.path.exists(marker) and ".claude/tools/sub.py" in files
    print("%-4s --all runs the heavy proof and stamps its inputs" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("all heavy: rc=%s ran=%s files=%s" % (rc, os.path.exists(marker), sorted(files)))


def case_cannot_run_heavy_is_incomplete_and_stamps_nothing(failures):
    repo, _marker = _heavy_repo()
    with open(os.path.join(repo, ".claude", "tests", "test_h.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\n# exercises sub.py\nsys.exit(2)\n")
    subprocess.run(["git", "commit", "-qam", "cannot-run"], cwd=repo, check=True)
    rc, out = _run_quiet(harness_tests.run_all, repo, include_excluded=True)
    ok = rc == 2 and not os.path.isfile(harness_tests.STAMP_PATH)
    print("%-4s a cannot-run heavy proof leaves the run incomplete and writes no stamp" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("cannot-run heavy: rc=%s stamp=%s out=%r"
                        % (rc, os.path.isfile(harness_tests.STAMP_PATH), out[-300:]))


def case_a_proof_both_heavy_and_excluded_fails_the_run(failures):
    repo, _marker = _heavy_repo()
    harness_tests.EXCLUDED = {"test_h.py": "also excluded"}
    rc, out = _run_quiet(harness_tests.run_all, repo)
    ok = rc == 2 and "test_h.py" in out and not os.path.exists(harness_tests.STAMP_PATH)
    print("%-4s a proof named in both proof_heavy and proof_excluded fails the run" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("heavy+excluded: rc=%s out=%r" % (rc, out[-300:]))
    harness_tests.EXCLUDED = {}


def case_an_absent_heavy_proof_is_reported_like_an_absent_excluded_one(failures):
    # A fixture repo or sparse checkout lacks the project's proofs, so absence cannot mean deletion.
    repo, _marker = _heavy_repo()
    os.remove(os.path.join(repo, ".claude", "tests", "test_h.py"))
    rc, out = _run_quiet(harness_tests.run_all, repo)
    ok = rc == 0 and "SKIP absent HEAVY entry: test_h.py" in out
    print("%-4s a configured heavy proof that is absent is reported and the battery runs" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("absent heavy: rc=%s out=%r" % (rc, out[-300:]))


def case_a_proof_both_heavy_and_excluded_fails_a_scoped_run(failures):
    repo, _marker = _heavy_repo()
    harness_tests._write_stamp(repo, 1, 1, [])
    before = open(harness_tests.STAMP_PATH, encoding="utf-8").read()
    harness_tests.EXCLUDED = {"test_h.py": "also excluded"}
    rc, out = _run_quiet(harness_tests.run_scoped, repo, [".claude/tools/sub.py"])
    after = open(harness_tests.STAMP_PATH, encoding="utf-8").read()
    ok = rc == 2 and "test_h.py" in out and after == before
    print("%-4s a proof named in both proof_heavy and proof_excluded fails a scoped run" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("scoped heavy+excluded: rc=%s out=%r" % (rc, out[-300:]))
    harness_tests.EXCLUDED = {}


def case_overlapping_scoped_run_keeps_the_other_runs_entries(failures):
    """Two stamp runs overlap: the other one stamps gamma while this one runs for alpha. This run's
    write must keep gamma's entry, never write back the stamp it read at its start."""
    repo = make_repo()
    for name in ("alpha", "gamma"):
        with open(os.path.join(repo, ".claude", "tools", name + ".py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
        write_proof(repo, "test_%s.py" % name, 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    harness_tests._write_stamp(repo, 2, 2, [])
    for name in ("alpha", "gamma"):
        with open(os.path.join(repo, ".claude", "tools", name + ".py"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("Y = 2\n")
    gamma = harness_tests.tree_entries(repo)[".claude/tools/gamma.py"]
    real = harness_tests._run_selected

    def with_a_peer_run(*args, **kwargs):
        result = real(*args, **kwargs)
        with open(harness_tests.STAMP_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["files"][".claude/tools/gamma.py"] = gamma
        with open(harness_tests.STAMP_PATH, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh)
        return result

    harness_tests._run_selected = with_a_peer_run
    try:
        rc, out = _run_quiet(harness_tests.run_scoped, repo, [".claude/tools/alpha.py"])
    finally:
        harness_tests._run_selected = real
    with open(harness_tests.STAMP_PATH, encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    ok = (rc == 0 and files.get(".claude/tools/gamma.py") == gamma
          and files.get(".claude/tools/alpha.py") == harness_tests.tree_entries(repo)[".claude/tools/alpha.py"])
    print("%-4s a scoped run that overlaps another keeps the entry the other run stamped meanwhile"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("overlapping scoped run: rc=%s gamma_kept=%s out=%r"
                        % (rc, files.get(".claude/tools/gamma.py") == gamma, out[-300:]))


def _durations(repo, value):
    with open(os.path.join(repo, "harness_proof_durations.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh)


def _run_stubbed(repo, path, default_timeout, outcomes, held=None):
    """_run_selected over `path` with run_proof stubbed: each attempt pops the next outcome and the
    timeout it was handed is recorded, and into `held` whether the run held the whole machine then.
    Returns (status, timeouts)."""
    seen = []
    holder = {}
    real_make = harness_tests._run_context

    def make(*args, **kwargs):
        holder["ctx"] = ctx = real_make(*args, **kwargs)
        return ctx

    def stub(proof, timeout=None, root=None, **_):
        seen.append(timeout)
        if held is not None:
            held.append(holder["ctx"].held)
        return outcomes.pop(0)

    real_run, real_timeout, saved = harness_tests.run_proof, harness_tests._TIMEOUT_SEC, dict(os.environ)
    harness_tests.run_proof, harness_tests._TIMEOUT_SEC = stub, default_timeout
    harness_tests._run_context = make
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    statuses = {}
    try:
        _run_quiet(harness_tests._run_selected, [path], False, statuses)
    finally:
        harness_tests.run_proof, harness_tests._TIMEOUT_SEC = real_run, real_timeout
        harness_tests._run_context = real_make
        os.environ.clear()
        os.environ.update(saved)
    return statuses.get(path), seen


def _timing_repo(name, durations):
    repo = make_repo()
    path = write_proof(repo, name, 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    _durations(repo, durations)
    return repo, path


def case_a_recorded_slow_proof_gets_three_times_its_recorded_time(failures):
    """A proof that once passed in 1 s runs with 3 s, not the 1 s default."""
    repo, path = _timing_repo("test_slow.py", {"test_slow.py": 1.0})
    status, seen = _run_stubbed(repo, path, 1, [("pass", 0.5, "")])
    ok = status == "pass" and seen == [3.0]
    print("%-4s a proof with a recorded pass runs with three times that time when that beats the default"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("recorded-duration timeout: status=%s timeouts=%s" % (status, seen))


def case_a_timeout_with_no_record_is_retried_with_a_longer_timeout(failures):
    """A proof never recorded that times out in the pool is retried at three times the timeout."""
    repo, path = _timing_repo("test_unrecorded.py", {})
    status, seen = _run_stubbed(repo, path, 1, [("fail", 1.0, "TIMEOUT after 1s"), ("pass", 0.5, "")])
    ok = status == "pass" and seen == [1, 3]
    print("%-4s a pool timeout with no recorded pass is retried with three times the timeout"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("unrecorded timeout retry: status=%s timeouts=%s" % (status, seen))


def case_a_timeout_keeps_the_recorded_duration(failures):
    """A timeout says nothing about correctness, so the proof keeps its record and its retry."""
    repo, path = _timing_repo("test_hang.py", {"test_hang.py": 0.1})
    timeout = ("fail", 1.0, "TIMEOUT after 1s")
    status, seen = _run_stubbed(repo, path, 1, [timeout, timeout])
    with open(os.path.join(repo, "harness_proof_durations.json"), encoding="utf-8") as fh:
        kept = json.load(fh).get("test_hang.py")
    ok = status == "fail" and kept == 0.1 and len(seen) == 2
    print("%-4s a proof that times out keeps its recorded duration" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("timeout keeps record: status=%s record=%s timeouts=%s" % (status, kept, seen))


def case_an_assertion_confirmation_holds_no_machine(failures):
    held_timeout, held_assert = [], []
    repo, path = _timing_repo("test_slowed.py", {"test_slowed.py": 0.1})
    timed = _run_stubbed(repo, path, 1, [("fail", 1.0, "TIMEOUT after 1s"), ("pass", 0.5, "")], held_timeout)[0]
    repo, path = _timing_repo("test_raced.py", {"test_raced.py": 0.1})
    raced = _run_stubbed(repo, path, 1, [("fail", 0.1, "AssertionError: boom"), ("pass", 0.1, "")], held_assert)[0]
    ok = timed == "pass" and held_timeout == [False, False] and raced == "pass" and held_assert == [False, False]
    print("%-4s timeout and assertion confirmations hold no whole-machine lease" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("retry hold: timeout=%s %s assertion=%s %s" % (timed, held_timeout, raced, held_assert))


def case_live_pace_is_the_median_slowdown_of_passes_and_never_below_one(failures):
    """A run's pace is how much slower than recorded its passing proofs ran: the median ratio, floored at 1."""
    pace = harness_tests._attempt.LivePace()
    empty = pace.factor()
    pace.observe(0.5, 1.0)
    floored = pace.factor()
    pace.observe(4.0, 1.0)
    pace.observe(12.0, 2.0)
    ok = empty == 1.0 and floored == 1.0 and pace.factor() == 4.0
    print("%-4s the live pace is the median slowdown of passes, never below one" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("live pace: empty=%s floored=%s three=%s" % (empty, floored, pace.factor()))


def case_a_run_feeds_each_pass_into_its_live_pace(failures):
    """A pass that took five times its recorded time sets the pace the run's later deadlines stretch by."""
    repo, path = _timing_repo("test_paced.py", {"test_paced.py": 2.0})
    holder = {}
    real_make = harness_tests._run_context

    def make(*args, **kwargs):
        holder["ctx"] = ctx = real_make(*args, **kwargs)
        return ctx

    harness_tests._run_context = make
    try:
        status = _run_stubbed(repo, path, 1, [("pass", 10.0, "")])[0]
    finally:
        harness_tests._run_context = real_make
    factor = holder["ctx"].pace.factor()
    ok = status == "pass" and factor == 5.0
    print("%-4s a run feeds each pass into its live pace" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("pace feed: status=%s factor=%s" % (status, factor))


def case_the_live_pace_stretches_a_running_proofs_deadline(failures):
    """A child past its timeout but inside the stretched deadline finishes; unstretched, it times out."""
    out = os.path.join(tempfile.mkdtemp(), "out.txt")
    cmd = [sys.executable, "-c", "import time; time.sleep(3)"]
    stretched = harness_tests._attempt._run_child(cmd, dict(os.environ), 1, out, stretch=lambda: 20.0)[0]
    flat = harness_tests._attempt._run_child(cmd, dict(os.environ), 1, out, stretch=lambda: 1.0)[0]
    ok = stretched == 0 and flat is None
    print("%-4s the live pace stretches a running proof's deadline" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("stretch: stretched rc=%s flat rc=%s" % (stretched, flat))


def case_a_stretched_deadline_never_passes_harness_deadline(failures):
    """HARNESS_DEADLINE caps a proof's timeout, and the live pace stretches it only up to that cap."""
    seen, saved = [], os.environ.get("HARNESS_DEADLINE")
    env = {"HARNESS_DEADLINE": str(time.time() + 2)}

    def launch(job):
        seen.append((job.timeout, job.stretch))
        return ("pass", 0.1, "")

    with patch.dict(os.environ, env):
        ctx = harness_tests._run_context(root=None)
        ctx.launch = launch
        harness_tests._attempt.run(ctx, harness_tests._attempt.AttemptJob(proof="/x/test_slow.py", timeout=120))
    if saved is None:
        os.environ.pop("HARNESS_DEADLINE", None)
    else:
        os.environ["HARNESS_DEADLINE"] = saved
    waited = seen[0][0] * seen[0][1]() if seen and seen[0][1] else None
    ok = waited is not None and waited <= 2.5
    print("%-4s a stretched deadline never passes HARNESS_DEADLINE" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("deadline: a proof cut to the deadline may wait %s s" % waited)


def _attempt_proof(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def case_an_attempts_status_follows_its_argv_and_exit(failures):
    """The attempt backend's decision table: interpreter and exit to the status word. A pytest collection
    error is a red (pytest's exit 2), a proof's own exit 2 is cannot-run, and a passing interpreter is pass."""
    tmp = tempfile.mkdtemp(prefix="ht_attempt_table_")
    cases = {
        "pass-py": (_attempt_proof(tmp, "test_ok.py", "import sys\nsys.exit(0)\n"), "pass"),
        "fail-py": (_attempt_proof(tmp, "test_red.py", "import sys\nsys.exit(1)\n"), "fail"),
        "cannot-py": (_attempt_proof(tmp, "test_cannot.py", "import sys\nsys.exit(2)\n"), "cannot-run"),
        "pass-js": (_attempt_proof(tmp, "test_ok.js", "process.exit(0)\n"), "pass"),
        "pass-mjs": (_attempt_proof(tmp, "test_ok.mjs", "import test from 'node:test';\n"), "pass"),
        "pass-sh": (_attempt_proof(tmp, "test_ok.sh", "true\n"), "pass"),
        "pass-ps1": (_attempt_proof(tmp, "test_ok.ps1", "exit 0\n"), "pass"),
        "platform-skip": (_attempt_proof(tmp, "test_skip.py", "print('PLATFORM-SKIP-PROOF planted')\n"),
                          "platform-skip"),
        "pytest-red": (_attempt_proof(tmp, "test_pytest_red.py", "def test_x():\n    assert 0\n"), "fail"),
        "pytest-collect": (_attempt_proof(tmp, "test_pytest_collect.py", "import no_such_module_planted\n"),
                           "fail"),
    }
    seen = {}
    for label, (path, _want) in cases.items():
        seen[label] = harness_tests._attempt.run_proof(path, timeout=60)[0]
    bad = {label: (seen[label], want) for label, (_path, want) in cases.items() if seen[label] != want}
    ok = not bad
    print("%-4s an attempt's status follows its interpreter and exit" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("attempt status table: %s" % bad)


def case_the_timeout_is_a_field_not_a_text_match(failures):
    """`AttemptResult.timeout` is the runner's own deadline kill. A proof that raises its own
    `subprocess.TimeoutExpired`, or merely prints TIMEOUT, is a failure with timeout False."""
    tmp = tempfile.mkdtemp(prefix="ht_attempt_timeout_")
    ctx = harness_tests._run_context(root=tmp)
    own = _attempt_proof(tmp, "test_own_timeout.py",
                         "import subprocess\nraise subprocess.TimeoutExpired(['sleep'], 60)\n")
    said = _attempt_proof(tmp, "test_says_timeout.py",
                          "import sys\nprint('TIMEOUT after 1s: the proof words it')\nsys.exit(1)\n")
    slow = _attempt_proof(tmp, "test_slow.py", "import time\ntime.sleep(5)\n")
    killed = harness_tests._attempt.run(ctx, harness_tests._attempt.AttemptJob(proof=slow, timeout=1))
    raised = harness_tests._attempt.run(ctx, harness_tests._attempt.AttemptJob(proof=own, timeout=30))
    printed = harness_tests._attempt.run(ctx, harness_tests._attempt.AttemptJob(proof=said, timeout=30))
    ok = (killed.status == "fail" and killed.timeout is True and killed.detail.startswith("TIMEOUT")
          and raised.status == "fail" and raised.timeout is False
          and printed.status == "fail" and printed.timeout is False)
    print("%-4s the timeout is a typed field, not a text match" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("typed timeout: killed=%r raised=%r printed=%r"
                        % ((killed.status, killed.timeout), (raised.status, raised.timeout),
                           (printed.status, printed.timeout)))


HEAVY_CASES = (
    case_mjs_proofs_are_discovered_and_run_by_node,
    case_heavy_proof_runs_when_bound,
    case_default_battery_skips_heavy_and_leaves_its_inputs_unstamped,
    case_all_runs_heavy_and_stamps_its_inputs,
    case_cannot_run_heavy_is_incomplete_and_stamps_nothing,
    case_a_proof_both_heavy_and_excluded_fails_the_run,
    case_an_absent_heavy_proof_is_reported_like_an_absent_excluded_one,
    case_a_proof_both_heavy_and_excluded_fails_a_scoped_run,
)

CASES = (
    case_proof_temp_folders_do_not_reach_the_real_temp,
    case_proof_drops_every_git_local_env_var,
    case_local_env_vars_come_from_git_with_a_fallback,
    case_proof_receives_the_stamp_index,
    case_a_touched_path_binds_its_own_proofs_not_its_importers,
    case_settings_and_jsonl_paths_bind_only_their_own_proofs,
    case_proof_does_not_inherit_git_repo_env,
    case_scoped_run_selects_bound_proofs_only,
    case_scoped_run_refreshes_only_the_touched_entries,
    case_scoped_run_with_a_red_proof_stamps_nothing,
    case_scoped_run_without_a_base_stamp_records_a_scoped_one,
    case_head_move_touching_no_input_keeps_the_full_stamp,
    case_peer_edit_mid_run_stamps_the_unchanged_files,
    case_a_scoped_stamp_landed_mid_run_survives_the_full_stamp,
    case_pass_and_fail,
    case_all_pass,
    case_zero_proofs,
    case_absent_excluded_is_skipped,
    case_ignored_proof_changes_hash,
    case_data_file_does_not_change_hash,
    case_failure_output_surfaces,
    case_git_failure_raises,
    case_snapshot_precedes_proof_selection,
    case_overlapping_scoped_run_keeps_the_other_runs_entries,
    case_a_recorded_slow_proof_gets_three_times_its_recorded_time,
    case_a_timeout_with_no_record_is_retried_with_a_longer_timeout,
    case_an_assertion_confirmation_holds_no_machine,
    case_live_pace_is_the_median_slowdown_of_passes_and_never_below_one,
    case_a_run_feeds_each_pass_into_its_live_pace,
    case_the_live_pace_stretches_a_running_proofs_deadline,
    case_a_stretched_deadline_never_passes_harness_deadline,
    case_a_timeout_keeps_the_recorded_duration,
    case_an_attempts_status_follows_its_argv_and_exit,
    case_the_timeout_is_a_field_not_a_text_match,
)


def case_commit_advisory_launches_no_proof(failures):
    """The commit advisory launches no bound proof: a proof with no recorded pass is left unrun, and its body never
    executes at commit time."""
    repo = make_repo()
    marker = os.path.join(repo, "ran.marker")
    path = os.path.join(repo, ".claude", "tests", "test_bound_quick.py")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("open(%r, 'w').close()\nCONTRACT = 'commit_contract.md'\n" % marker)
    seed_commit(repo)
    saved = harness_tests.PASSES_PATH, harness_tests.STAMP_PATH
    harness_tests.PASSES_PATH = os.path.join(repo, "passes.json")
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    try:
        with open(harness_tests.durations_path(), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"test_bound_quick.py": 1}, fh)
        result = harness_tests.run_commit(repo, [".claude/reference/commit_contract.md"])
    finally:
        harness_tests.PASSES_PATH, harness_tests.STAMP_PATH = saved
    ok = not os.path.exists(marker) and result["proven"] == []
    print("%-4s the commit advisory launches no bound proof" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("commit advisory launched a proof: marker=%s result=%r" % (os.path.exists(marker), result))


def case_commit_advisory_reports_a_cached_pass_as_proven(failures):
    """A bound proof whose recorded pass still holds is reported as already passed for these inputs, and it is
    not named as unproven."""
    repo = make_repo()
    name = "test_bound_cached.py"
    with open(write_proof(repo, name, 0), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("CONTRACT = 'commit_contract.md'\n")
    seed_commit(repo)
    saved = harness_tests.PASSES_PATH, harness_tests.STAMP_PATH
    harness_tests.PASSES_PATH = os.path.join(repo, "passes.json")
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    try:
        with open(harness_tests.durations_path(), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({name: 500}, fh)
        proof = os.path.join(repo, ".claude", "tests", name)
        with harness_tests._policy_from(os.path.join(repo, ".claude")), contextlib.redirect_stdout(io.StringIO()):
            recorder = harness_tests._PassCache(repo)  # the commit reads under the same policy: its keys match
            recorder.snapshot_git()
            recorder.record(proof, key=recorder.key(proof))
        result = harness_tests.run_commit(repo, [".claude/reference/commit_contract.md"])
    finally:
        harness_tests.PASSES_PATH, harness_tests.STAMP_PATH = saved
    ok = result["proven"] == [name] and result["unproven"] is None
    print("%-4s a recorded pass is reported as already passed, not as unproven" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("cached pass: proven=%r unproven=%r" % (result.get("proven"), result.get("unproven")))


def case_commit_advisory_prints_the_command_for_the_rest(failures):
    """A bound proof with no recorded pass is named by one line that carries the `--proofs-for` command for the
    commit's paths."""
    repo = make_repo()
    name = "test_bound_rest.py"
    with open(write_proof(repo, name, 0), "a", encoding="utf-8", newline="\n") as fh:
        fh.write("CONTRACT = 'commit_contract.md'\n")
    seed_commit(repo)
    saved = harness_tests.PASSES_PATH, harness_tests.STAMP_PATH
    harness_tests.PASSES_PATH = os.path.join(repo, "passes.json")
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    try:
        with open(harness_tests.durations_path(), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({name: 1}, fh)
        result = harness_tests.run_commit(repo, [".claude/reference/commit_contract.md"])
    finally:
        harness_tests.PASSES_PATH, harness_tests.STAMP_PATH = saved
    line = (result["unproven"] or {}).get("line", "")
    ok = ("`python3 .claude/scripts/harness_tests.py --proofs-for .claude/reference/commit_contract.md` runs them."
          in line) and result["proven"] == []
    print("%-4s an unpassed bound proof is named with the command that proves it" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("commit command: %r" % result)


def case_the_unproven_cause_is_the_set_of_proofs_not_its_size(failures):
    """Two commits that each leave one different bound proof to the close are two causes: the second commit's
    advisory names its own `--proofs-for` command, so it must not share the first's once-per-cause signature."""
    repo = make_repo()
    docs = {"a": ".claude/reference/cause_a.md", "b": ".claude/reference/cause_b.md"}
    for key in docs:
        with open(write_proof(repo, "test_slow_%s.py" % key, 0), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("CONTRACT = 'cause_%s.md'\n" % key)
    seed_commit(repo)
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    with open(harness_tests.durations_path(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"test_slow_a.py": 500, "test_slow_b.py": 500}, fh)
    unproven = [harness_tests.run_commit(repo, [doc])["unproven"] for doc in docs.values()]
    ok = all(unproven) and unproven[0]["signature"] != unproven[1]["signature"]
    print("%-4s the unproven cause names which proofs are left, not how many" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("unproven cause: %r" % unproven)


def case_commit_runner_timeout(failures):
    repo = make_repo()
    hanging = os.path.join(repo, "hanging_runner.py")
    with open(hanging, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import time\ntime.sleep(60)\n")
    real_file, real_budget = harness_tests.__file__, harness_tests.COMMIT_BUDGET_S
    harness_tests.__file__, harness_tests.COMMIT_BUDGET_S = hanging, 1
    try:
        advisories = harness_tests.commit_advisories(repo, [".claude/reference/doc.md"])
    finally:
        harness_tests.__file__, harness_tests.COMMIT_BUDGET_S = real_file, real_budget
    ok = len(advisories) == 1 and "timeout after 1s" in advisories[0]
    print("%-4s a hung commit runner returns one timeout advisory" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("commit timeout: %r" % advisories)


def case_commit_runs_the_committed_checkouts_runner(failures):
    """A commit in another checkout (a worktree) is proved by that checkout's runner, the one its close uses, so its
    advisory reuses the passes that runner recorded; the hook's own runner keys every pass differently."""
    repo = make_repo()
    own = os.path.join(repo, ".claude", "scripts", "harness_tests.py")
    os.makedirs(os.path.dirname(own), exist_ok=True)
    with open(own, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import json\nprint(json.dumps({'advisories': ['own runner'], 'unproven': None}))\n")
    advisories = harness_tests.commit_advisories(repo, [".claude/reference/doc.md"])
    ok = advisories == ["own runner"]
    print("%-4s a commit is proved by the committed checkout's own runner" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("commit runner: %r" % advisories)


def case_a_failure_line_above_the_tail_stays_in_the_excerpt(failures):
    detail = "\n".join(["ok   case %d" % i for i in range(3)] + ["FAIL the case that broke"]
                       + ["ok   later case %d" % i for i in range(harness_tests._DETAIL_LINES + 5)])
    excerpt = harness_tests._detail_excerpt(detail)
    if "FAIL the case that broke" not in excerpt:
        failures.append("a FAIL line above the last %d lines was dropped from the excerpt" % harness_tests._DETAIL_LINES)
    if excerpt[-harness_tests._DETAIL_LINES:] != detail.splitlines()[-harness_tests._DETAIL_LINES:]:
        failures.append("the excerpt no longer ends with the detail's tail")
    if harness_tests._detail_excerpt("ok   a\nok   b") != ["ok   a", "ok   b"]:
        failures.append("a short detail is not printed whole and once")
    indented = "\n".join(["  PASS  case %d" % i for i in range(3)] + ["  FAIL  T8 the case that broke"]
                         + ["  PASS  later case %d" % i for i in range(harness_tests._DETAIL_LINES + 5)])
    if "  FAIL  T8 the case that broke" not in harness_tests._detail_excerpt(indented):
        failures.append("an indented FAIL line above the tail (test_runtime_slots.ps1's shape) was dropped from the excerpt")
    detailed = "\n".join(["  PASS  case %d" % i for i in range(3)]
                         + ["  FAIL  T37 the case that broke", "        coldWaitMs=9 warmWaitMs=1", "  PASS  a case after"]
                         + ["  PASS  later case %d" % i for i in range(harness_tests._DETAIL_LINES + 5)])
    kept = harness_tests._detail_excerpt(detailed)
    if "        coldWaitMs=9 warmWaitMs=1" not in kept or "  PASS  a case after" in kept:
        failures.append("a failing case's deeper-indented detail line must ride with its FAIL line, and only that line: %r" % kept[:3])


def case_platform_skip_is_counted_not_passed(failures):
    repo = make_repo()
    tests = os.path.join(repo, ".claude", "tests")
    helper_dir = os.path.dirname(os.path.abspath(__file__))
    proofs = {
        "test_whole_skip.py": "_platform_skip.skip_proof('whole', 'planted')\n",
        "test_case_skip.py": "_platform_skip.skip('case', 'planted')\nprint('ok   other case')\n",
    }
    statuses = {}
    for name, body in proofs.items():
        with open(os.path.join(tests, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport _platform_skip\n%s" % (helper_dir, body))
        statuses[name] = harness_tests.run_proof(os.path.join(tests, name))[0]
    ok = statuses == {"test_whole_skip.py": "platform-skip", "test_case_skip.py": "pass"}
    print("%-4s a whole-proof platform skip is reported skipped, never passed; a skipped case leaves its proof's"
          " verdict to the cases that ran" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("platform skip: %s" % statuses)


def case_a_node_proof_platform_skip_survives_the_tap_comment_prefix(failures):
    repo = make_repo()
    path = os.path.join(repo, ".claude", "tests", "test_node_skip.mjs")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("console.log('PLATFORM-SKIP-PROOF test_node_skip.mjs: planted')\nprocess.exit(0)\n")
    status = harness_tests.run_proof(path)[0]
    ok = status == "platform-skip"
    print("%-4s a node proof's platform skip, which TAP prints as a comment line, is reported skipped"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("node platform skip: %s" % status)


class _CloseUntrackedRed(RepoCase):
    PROOF = ("from pathlib import Path\nimport sys\n"
             "text = (Path(__file__).resolve().parents[1] / 'hooks/unit.py').read_text()\n"
             "if 'bad' in text:\n    print('FAIL: unit regressed')\n    sys.exit(1)\n")

    def outgoing_bad_unit(self, track_proof):
        self.write('.claude/hooks/unit.py', "VALUE = 'good'\n")
        self.base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'bad'\n")
        self.write('.claude/tests/test_unit.py', self.PROOF)
        if track_proof:
            self.commit('break unit with its proof')
        else:
            self.git('add', '-f', '.claude/hooks/unit.py')
            self.git('commit', '-qm', 'break unit')

    def test_untracked_red_proof_is_advisory(self):
        self.outgoing_bad_unit(False)
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('UNCOMMITTED PROOF RED test_unit.py (not in this push)', out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'green')

    def test_tracked_red_proof_still_blocks(self):
        self.outgoing_bad_unit(True)
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertNotIn('UNCOMMITTED PROOF RED', out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'red')

    def outgoing_only(self, rel):
        self.write('.claude/hooks/unit.py', "VALUE = 'good'\n")
        self.write('.claude/tests/test_unit.py', self.PROOF)
        self.base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write(rel, '{}\n')
        self.commit('outgoing ' + rel)

    def test_record_only_push_closes_green(self):
        self.outgoing_only('.claude/orchestration_metrics.jsonl')
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'green')

    def test_unbound_code_path_stays_incomplete(self):
        self.outgoing_only('.claude/hooks/orphan.py')
        rc, out = self.close()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('no proof bound to outgoing harness paths', out)

    def test_incomplete_close_names_the_proof_that_neither_passed_nor_failed(self):
        helper = os.path.dirname(os.path.abspath(__file__))
        self.write('.claude/hooks/unit.py', "VALUE = 'good'\n")
        self.write('.claude/tests/test_unit.py',
                   "import sys\n" "sys.path.insert(0, %r)\n" "import _platform_skip\n"
                   "open(__file__.replace('tests/test_unit.py', 'hooks/unit.py')).read()\n"
                   "_platform_skip.skip_proof('whole', 'planted')\n" % helper)
        self.base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'next'\n")
        self.commit('outgoing unit')
        rc, out = self.close()
        self.assertEqual(rc, 2, out)
        self.assertIn('harness_tests: close incomplete', out)
        self.assertIn('harness_tests: not passed or failed: test_unit.py (platform-skip)', out.splitlines())


def case_close_untracked_red_is_advisory_tracked_red_blocks(failures):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_CloseUntrackedRed)
    result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    ok = result.wasSuccessful()
    print("%-4s close: untracked red proof is advisory, tracked red blocks, a records-only push is green"
          % ("ok" if ok else "FAIL"))
    for _, trace in result.failures + result.errors:
        failures.append("close untracked red: " + trace.strip().splitlines()[-1])


def _run_stubbed_row(repo, path, outcomes):
    """_run_selected over `path` with run_proof stubbed and the run recorded: (printed text, the recorded row)."""
    def stub(proof, timeout=None, root=None, **_):
        return outcomes.pop(0)

    real_run, saved, row = harness_tests.run_proof, dict(os.environ), {}
    harness_tests.run_proof = stub
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    try:
        _, out = _run_quiet(harness_tests._run_selected, [path], False, None, None,
                            harness_tests.RunOptions(mode="test", row=row))
    finally:
        harness_tests.run_proof = real_run
        os.environ.clear()
        os.environ.update(saved)
    return out, row


def case_each_alone_retry_prints_a_progress_line_and_the_row_counts_them(failures):
    """A pool failure retried alone prints `alone retry k of n: <proof>` with its first failure's last line before
    the retry starts, and the recorded row carries the pool seconds and the retries' count and seconds."""
    repo, path = _timing_repo("test_flaky_case.py", {"test_flaky_case.py": 1.0})
    out, row = _run_stubbed_row(repo, path, [("fail", 1.0, "setup\nAssertionError: boom"), ("pass", 7.5, "")])
    line = "harness_tests: alone retry 1 of 1: test_flaky_case.py; first failure: AssertionError: boom"
    ok = line in out and out.index(line) < out.index("FLAKY") and row.get("alone_n") == 1 \
        and row.get("alone_s") == 7.5 and "pool_s" in row
    print("%-4s each alone retry prints a progress line before it starts, and the row counts the retries"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("alone retry line: row=%s out=%s" % (row, out[-300:]))


def case_confirmations_run_in_parallel_unless_a_red_policy_can_stop_them(failures):
    """A confirmation is admitted as a pool proof was, so confirmations run at the pool's width when no red can
    stop a later one (`RedRule.never_red()`, a ranged close), and one at a time when the rule may stop them.
    Each start still prints its line."""
    def run(red):
        repo = make_repo()
        names = ["test_c%d.py" % i for i in range(3)]
        for name in names:
            write_proof(repo, name, 1)
        seed_commit(repo)
        harness_tests.EXCLUDED = {}
        harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
        _durations(repo, {name: 0.1 for name in names})
        selected = [os.path.join(repo, ".claude", "tests", name) for name in names]
        state, lock, calls = {"live": 0, "peak": 0, "confirmed": 0}, threading.Lock(), {}

        def stub(proof, timeout=None, root=None, **_):
            name = os.path.basename(proof)
            with lock:
                calls[name] = calls.get(name, 0) + 1
                attempt = calls[name]
            if attempt == 1:
                return "fail", 0.1, "AssertionError: boom"
            with lock:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            time.sleep(0.4)
            with lock:
                state["live"] -= 1
                state["confirmed"] += 1
            return "pass", 0.2, ""

        real_run, saved = harness_tests.run_proof, dict(os.environ)
        harness_tests.run_proof = stub
        os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
        # This case states the pool width it tests: an inherited lane (`HARNESS_SLOT_BUDGET=1`, a proof run through
        # run_proof) forces `_jobs()` to 1, and the parallel arm would prove nothing.
        os.environ.pop("HARNESS_SLOT_BUDGET", None)
        os.environ["HARNESS_TEST_JOBS"] = "3"
        os.environ["HARNESS_MACHINE_SLOTS"] = "3"
        try:
            _, out = _run_quiet(harness_tests._run_selected, selected, False, None, None,
                                harness_tests.RunOptions(mode="test", red=red))
        finally:
            harness_tests.run_proof = real_run
            os.environ.clear()
            os.environ.update(saved)
        lines = [line for line in out.splitlines() if " retry " in line]
        return state, lines, names

    serial, serial_lines, serial_names = run(harness_tests.RedRule.judge(lambda reds: False))
    parallel, parallel_lines, parallel_names = run(harness_tests.RedRule.never_red())
    starts = lambda lines, names: all(any(name in line for line in lines) for name in names)
    ok = serial["peak"] == 1 and serial["confirmed"] == 3 and starts(serial_lines, serial_names) \
        and parallel["peak"] > 1 and parallel["confirmed"] == 3 and starts(parallel_lines, parallel_names)
    print("%-4s confirmations run at the pool's width unless a red policy can stop them, and each start prints"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("confirmation pool: serial=%s %s parallel=%s %s"
                        % (serial, serial_lines, parallel, parallel_lines))


def case_the_close_time_line_states_pool_retries_and_load_against_the_fastest_history(failures):
    """Load = this run's summed proof time over the same proofs' fastest earlier passes; proofs with no history
    stay out of both sums and are counted."""
    def proofs(*rows):
        return [{"proof": n, "status": s, "run_s": t, "reused": r} for n, s, t, r in rows]

    history = [{"ts": 1, "proofs": proofs(("a.py", "pass", 10.0, False), ("b.py", "pass", 20.0, False))},
               {"ts": 2, "proofs": proofs(("a.py", "pass", 12.0, False), ("b.py", "fail", 1.0, False),
                                          ("c.py", "pass", 0.0, True))},
               {"ts": 3, "proofs": proofs(("a.py", "pass", 99.0, False))}]
    row = {"ts": 3, "pool_s": 40.0, "alone_n": 2, "alone_s": 30.5,
           "proofs": proofs(("a.py", "pass", 30.0, False), ("b.py", "pass", 40.0, False),
                            ("d.py", "pass", 5.0, False), ("c.py", "pass", 0.0, True))}
    line = harness_tests._time_line(row, history)
    want = ("harness_tests: close time: pool 40.0s; alone retries 2, 30.5s; "
            "load x2.3 (70.0s over 30.0s fastest, 2 of 3 proofs with history)")
    ok = line == want
    none = harness_tests._time_line(dict(row, ts=9, proofs=proofs(("d.py", "pass", 5.0, False))), [])
    ok = ok and none.endswith("load unknown (0 of 1 proofs with history)")
    print("%-4s the close time line states pool, alone retries and load against the fastest history"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("time line: %r / %r" % (line, none))


PARTS = 5


def _lookup_repo(exit_code):
    """A repo with one proof that exits `exit_code`, a private pass store, and the proof's path."""
    repo = make_repo()
    proof = write_proof(repo, "test_lazy.py", exit_code)
    seed_commit(repo)
    harness_tests.PASSES_PATH = os.path.join(repo, "passes.json")
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    return repo, proof


def _reach_calls(fn):
    """(`fn()`'s result, how many times the real `_PassCache.reach` ran meanwhile)."""
    real, calls = harness_tests._PassCache.reach, []

    def counted(self, proof):
        calls.append(proof)
        return real(self, proof)
    harness_tests._PassCache.reach = counted
    try:
        return fn(), len(calls)
    finally:
        harness_tests._PassCache.reach = real


def _both_lookups(repo, proof):
    """Each reuse check on `proof`: the commit advisory's, then a scoped run's `attempt` (reused when its status is
    pass, since the planted proof fails if it runs). Returns {lookup: (reused, reach calls)}."""
    out = {}
    held, n = _reach_calls(lambda: harness_tests._recorded_passes(
        repo, [proof], harness_tests._pass_cache(repo, [proof])))
    out["advisory"] = (proof in held, n)

    def run():
        statuses, details = {}, {}
        with harness_tests.redirect_stdout(io.StringIO()):
            harness_tests._run_selected([proof], False, statuses, details, harness_tests.RunOptions(
                cache=harness_tests._pass_cache(repo, [proof]), root=repo, repo=repo))
        return statuses[proof]
    status, n = _reach_calls(run)
    out["attempt"] = (status == "pass", n)
    return out


def case_a_row_with_an_observed_key_is_reused_without_the_static_reach(failures):
    """A reuse check judges a row that carries `okey` by it alone, so it never builds the static key: `reach` is
    what costs seconds. A row with no `okey` is still judged by the static key, which is computed and compared."""
    saved = harness_tests.PASSES_PATH, harness_tests.STAMP_PATH
    try:
        repo, proof = _lookup_repo(1)
        rel = os.path.relpath(proof, repo).replace(os.sep, "/")
        recorder = harness_tests._PassCache(repo)
        recorder.snapshot_git()
        manifest = {"files": [rel], "probes": [], "dirs": [], "static": [], "git": False, "opaque": False}
        recorder.record(proof, key="static-key-never-compared", manifest=manifest, observable=True)
        observed = _both_lookups(repo, proof)
        repo, proof = _lookup_repo(1)
        recorder = harness_tests._PassCache(repo)
        recorder.snapshot_git()
        recorder.record(proof, key=recorder.key(proof))
        static = _both_lookups(repo, proof)
        repo, proof = _lookup_repo(1)
        recorder = harness_tests._PassCache(repo)
        recorder.snapshot_git()
        recorder.record(proof, key="a-key-no-input-holds")
        stale = _both_lookups(repo, proof)
    finally:
        harness_tests.PASSES_PATH, harness_tests.STAMP_PATH = saved
    ok = (observed == {"advisory": (True, 0), "attempt": (True, 0)}
          and all(reused and n > 0 for reused, n in static.values())
          and not any(reused for reused, _ in stale.values()))
    print("%-4s a row with an observed key is reused without the static reach; a row without one is judged by it"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("lazy key: observed=%s static=%s stale=%s" % (observed, static, stale))


def case_binding_parses_only_the_proofs_whose_text_holds_the_token(failures):
    """A proof binds by a name its runnable text holds, and a comment holds none. The raw text holds every name the
    runnable text does, so a proof whose raw text lacks it binds nothing and is never parsed for its code."""
    repo = make_repo()
    with open(os.path.join(repo, ".claude", "hooks", "alpha.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("X = 1\n")
    bodies = {"test_runs_it.py": "NAME = 'alpha.py'\n", "test_comment_only.py": "# alpha.py\n", "test_other.py": "Y = 2\n"}
    for name, body in bodies.items():
        with open(os.path.join(repo, ".claude", "tests", name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    seed_commit(repo)
    real, parsed = harness_tests._code_text, []

    def counted(rel, text):
        parsed.append(os.path.basename(rel))
        return real(rel, text)
    harness_tests._code_text = counted
    try:
        bound = sorted(os.path.basename(p) for p in harness_tests.select_for(repo, [".claude/hooks/alpha.py"]))
    finally:
        harness_tests._code_text = real
    ok = bound == ["test_runs_it.py"] and "test_other.py" not in parsed and "test_comment_only.py" in parsed
    print("%-4s binding parses only the proofs whose raw text holds the name" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("binding parse: bound=%s parsed=%s" % (bound, parsed))


def _state_ref(repo, text):
    """Plants `text` as main_check.json on the local ref the commit advisory reads, with no fetch."""
    def plumb(args, data=None):
        return subprocess.run(["git", "-C", repo, *args], input=None if data is None else data.encode(),
                              capture_output=True, check=True).stdout.decode().strip()
    blob = plumb(["hash-object", "-w", "--stdin"], text)
    tree = plumb(["mktree"], "100644 blob %s\tmain_check.json\n" % blob)
    commit = plumb(["commit-tree", tree, "-m", "planted state"])
    plumb(["update-ref", "refs/remotes/origin/main-check-state", commit])


def case_open_main_rows_advise_bound_proofs_from_the_local_ref(failures):
    repo = make_repo()
    write_proof(repo, "test_alpha.py", 0)  # bound to alpha.py by name
    write_proof(repo, "test_unbound.py", 0)
    with open(os.path.join(repo, ".claude", "hooks", "alpha.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("X = 1\n")
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    bound = ["test_alpha.py"]  # what run_commit reports as the bound proofs
    rows = [{"name": "test_alpha.py", "kind": "proof", "platform": "linux", "commit": None, "session": None,
             "firstLine": "FAIL", "firstSeen": "2026-11-18T00:00:00Z", "clearedAt": None, "clearedBy": None},
            {"name": "test_unbound.py", "kind": "proof", "platform": "linux", "commit": None, "session": None,
             "firstLine": "FAIL", "firstSeen": "2026-11-18T00:00:00Z", "clearedAt": None, "clearedBy": None},
            {"name": "test_cleared.py", "kind": "proof", "platform": "linux", "commit": None, "session": None,
             "firstLine": "FAIL", "firstSeen": "2026-11-18T00:00:00Z", "clearedAt": "2026-11-18T01:00:00Z",
             "clearedBy": "abc"}]
    results = {}
    _state_ref(repo, json.dumps({"version": 1, "checkedTo": "x", "rows": rows}))
    results["bound"] = harness_tests.open_main_lines(repo, bound)
    subprocess.run(["git", "-C", repo, "update-ref", "-d", "refs/remotes/origin/main-check-state"], check=True)
    results["absent"] = harness_tests.open_main_lines(repo, bound)
    _state_ref(repo, "not json\n")
    results["malformed"] = harness_tests.open_main_lines(repo, bound)
    probe = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); import harness_tests as h; "
                            "h.open_main_lines(%r, %r); print('main_check' in sys.modules)"
                            % (_SCRIPTS_DIR, repo, bound)], capture_output=True, text=True)
    ok = (results["bound"] == ["[harness commit] open on main: test_alpha.py linux"]
          and results["absent"] == [] and results["malformed"] == []
          and probe.stdout.strip() == "False")
    print("%-4s open on main: bound open rows print, absent and malformed refs print nothing, no main_check import"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("open on main: %s probe=%s %s" % (results, probe.stdout, probe.stderr[-300:]))


def case_parallel_confirmations_count_every_queue_second(failures):
    """Confirmations run on threads, and each reports the time it waited for a lane. A lost update would
    under-report the run's queue; every confirmation's seconds must land in the totals once."""
    repo = make_repo()
    names = ["test_q%02d.py" % i for i in range(12)]
    for name in names:
        write_proof(repo, name, 1)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    _durations(repo, {name: 0.1 for name in names})
    selected = [os.path.join(repo, ".claude", "tests", name) for name in names]
    calls, lock, gate = {}, threading.Lock(), threading.Barrier(len(names))
    split = {"behind-%02d" % i: 1.0 for i in range(100)}
    broken = []

    def stub_launch(path, timeout=None, root=None, recheck=None, timing=None, **_):
        name = os.path.basename(path)
        with lock:
            calls[name] = calls.get(name, 0) + 1
            attempt = calls[name]
        if attempt == 1:
            return harness_tests._attempt.AttemptResult("fail", 0.1, "AssertionError: boom")
        try:
            gate.wait(timeout=20)  # every confirmation reaches its accounting together
        except threading.BrokenBarrierError:
            broken.append(name)
        if timing is not None:
            timing["wait_s"] = 0.5
            timing["wait_split"] = dict(split)
        return harness_tests._attempt.AttemptResult("pass", 0.1, "")

    real, saved = harness_tests._launch, dict(os.environ)
    harness_tests._launch = stub_launch
    os.environ.pop("HARNESS_SLOT_BUDGET", None)
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_TEST_JOBS"] = "12"
    os.environ["HARNESS_MACHINE_SLOTS"] = "12"
    row = {}
    switch = sys.getswitchinterval()
    try:
        sys.setswitchinterval(0.000001)  # a thread switch inside the read-modify-write is the race under test
        _run_quiet(harness_tests._run_selected, selected, False, None, None,
                   harness_tests.RunOptions(mode="test", row=row, red=harness_tests.RedRule.never_red()))
    finally:
        sys.setswitchinterval(switch)
        harness_tests._launch = real
        os.environ.clear()
        os.environ.update(saved)
    totals = row.get("queue_split_s") or {}
    ok = (not broken and len(totals) == 100 and all(v == 12.0 for v in totals.values())
          and row.get("queue_s") == 6.0)
    print("%-4s parallel confirmations count every queue second exactly once" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("parallel queue accounting: broken=%s totals=%d sample=%s queue_s=%s"
                        % (broken, len(totals), sorted(totals.items())[:3], row.get("queue_s")))


def case_progress_lines_from_threads_do_not_interleave(failures):
    """Pool and confirmation threads print start lines at once; the log must hold every line whole, never
    half of one spliced into another."""
    class Slow(io.StringIO):
        def write(self, text):
            time.sleep(0.002)
            return super().write(text)

    stream = Slow()

    def worker(tag):
        for i in range(10):
            harness_tests._progress("%s %d %s" % (tag, i, "x" * 60))

    with contextlib.redirect_stdout(stream):
        threads = [threading.Thread(target=worker, args=("T%d" % t,)) for t in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    lines = stream.getvalue().splitlines()
    pattern = re.compile(r"^harness_tests: T\d+ \d+ x{60}$")
    ok = len(lines) == 40 and all(pattern.match(line) for line in lines)
    print("%-4s progress lines from threads stay whole" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("progress interleave: %d lines, %s" % (len(lines), lines[:3]))


def case_concurrent_flaky_rows_are_not_lost(failures):
    """Confirmations log their flaky rows from threads: every row must land once, parseable, with no other
    thread's row spliced into it."""
    repo = make_repo()
    log = os.path.join(repo, "harness_flaky.jsonl")
    saved = harness_tests.FLAKY_LOG
    harness_tests.FLAKY_LOG = log
    try:
        def worker(i):
            harness_tests._record_flaky(os.path.join(repo, "test_w%d.py" % i),
                                        "FAIL: x\nAssertionError: y", 0.1)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        harness_tests.FLAKY_LOG = saved
    rows, unparsed = [], 0
    with open(log, encoding="utf-8") as fh:
        for line in fh.read().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                unparsed += 1
    ok = len(rows) == 24 and len({row["proof"] for row in rows}) == 24 and not unparsed
    print("%-4s concurrent flaky rows all land whole" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("flaky rows: %d rows, %d unparsed, %d proofs"
                        % (len(rows), unparsed, len({row.get("proof") for row in rows})))


def case_append_diagnostic_prints_row_when_write_fails(failures):
    """A diagnostic row that cannot land on disk is printed to stderr rather than dropped in silence."""
    repo = make_repo()
    log = os.path.join(repo, "harness_flaky.jsonl")
    row = '{"proof": "test_dropped.py", "first": "AssertionError: boom"}'
    real_locked = harness_tests._file_lock.locked

    def fail_lock(*args, **kwargs):
        raise TimeoutError("lock busy")

    harness_tests._file_lock.locked = fail_lock
    err = io.StringIO()
    saved_err = sys.stderr
    try:
        sys.stderr = err
        harness_tests._append_diagnostic(log, row, lambda rows: rows)
    finally:
        sys.stderr = saved_err
        harness_tests._file_lock.locked = real_locked
    ok = row in err.getvalue()
    print("%-4s _append_diagnostic prints the row when the write fails" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("append_diagnostic dropped row: stderr=%r" % err.getvalue())


def main(part=1):
    """Part `part` of PARTS: the cases build their own temp repos, so they split round-robin over processes that run
    beside each other. The heavy cases set `harness_tests.HEAVY` themselves and run first in part 1; every other case
    expects it empty."""
    for other in range(2, PARTS + 1):  # a deleted part file would silently drop its cases
        assert os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "test_harness_tests_runner_part%d.py" % other)), "missing part %d" % other
    failures, failed_cases = [], 0
    plain = list(CASES) + [case_each_alone_retry_prints_a_progress_line_and_the_row_counts_them,
                           case_confirmations_run_in_parallel_unless_a_red_policy_can_stop_them,
                           case_the_close_time_line_states_pool_retries_and_load_against_the_fastest_history,
                           case_commit_advisory_launches_no_proof,
                           case_commit_advisory_reports_a_cached_pass_as_proven,
                           case_commit_advisory_prints_the_command_for_the_rest, case_commit_runner_timeout,
                           case_commit_runs_the_committed_checkouts_runner,
                           case_the_unproven_cause_is_the_set_of_proofs_not_its_size,
                           case_a_failure_line_above_the_tail_stays_in_the_excerpt,
                           case_platform_skip_is_counted_not_passed,
                           case_a_node_proof_platform_skip_survives_the_tap_comment_prefix,
                           case_close_untracked_red_is_advisory_tracked_red_blocks,
                           case_a_row_with_an_observed_key_is_reused_without_the_static_reach,
                           case_binding_parses_only_the_proofs_whose_text_holds_the_token,
                           case_open_main_rows_advise_bound_proofs_from_the_local_ref,
                           case_parallel_confirmations_count_every_queue_second,
                           case_progress_lines_from_threads_do_not_interleave,
                           case_concurrent_flaky_rows_are_not_lost,
                           case_append_diagnostic_prints_row_when_write_fails]
    cases = list(HEAVY_CASES) * (part == 1) + plain[part - 1::PARTS]
    if part != 1:
        harness_tests.HEAVY = {}
    for case in cases:
        before = len(failures)
        case(failures)
        failed_cases += len(failures) > before
        if case is HEAVY_CASES[-1]:
            harness_tests.HEAVY = {}
    total = len(cases)
    print("\n%d/%d cases pass" % (total - failed_cases, total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
