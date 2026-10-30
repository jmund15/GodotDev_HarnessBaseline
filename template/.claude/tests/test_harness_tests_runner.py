"""Re-runnable proof for scripts/harness_tests.py.

Each case builds an isolated temp git repo shaped like the real `.claude/` tree, points
the runner at it via `run_all(repo_root)`, and asserts on the exit code and the stamp
file — RED before the runner exists.

    python3 .claude/tests/test_harness_tests_runner.py
"""
import json
import os
import subprocess
import sys
import tempfile

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
    keys = harness_tests.GIT_REPO_ENV if hasattr(harness_tests, "GIT_REPO_ENV") else (
        "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")
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


def case_helper_module_selects_its_importers_proofs(failures):
    """Touching a `_`-prefixed hooks helper selects the proofs of every hook that imports it,
    directly or through another helper."""
    repo = make_repo()
    hooks = os.path.join(repo, ".claude", "hooks")
    sources = {
        "_scope.py": "X = 1\n",
        "_mid.py": "from _scope import X\n",
        "direct.py": "import json\nfrom _scope import X\n",
        "via_mid.py": "import _mid\n",
        "bystander.py": "# mentions _scope in a comment only\nY = 2\n",
    }
    for name, text in sources.items():
        with open(os.path.join(hooks, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    for name in ("test_direct_rm.py", "test_via_mid.py", "test_bystander.py"):
        write_proof(repo, name, 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    names = sorted(os.path.basename(p) for p in harness_tests.select_for(repo, [".claude/hooks/_scope.py"]))
    real = sorted(os.path.basename(p) for p in harness_tests.select_for(
        harness_tests.REPO_ROOT, [".claude/hooks/_claude_scope.py"]))
    ok = (names == ["test_direct_rm.py", "test_via_mid.py"]
          and "test_pattern_enforcer_autofix.py" in real)
    print("%-4s a touched hooks helper selects its importers' proofs, transitively, and not a bystander's"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("helper importers: fixture=%s real_has_autofix=%s"
                        % (names, "test_pattern_enforcer_autofix.py" in real))


def case_module_selects_its_direct_importers_proofs(failures):
    """A touched tool selects the name and mention proofs of the modules that import it, a
    function-local import included; the closure goes no further than a direct importer unless that
    importer is a `_`-prefixed hooks helper. A declared binding replaces only the touched path's own
    mention rule."""
    repo = make_repo()
    sources = {
        "tools/a.py": "X = 1\n",
        "hooks/b.py": "def load():\n    from a import X\n",
        "scripts/c.py": "import b\n",
        "tools/_non_hook.py": "import a\n",
        "hooks/via_non_hook.py": "import _non_hook\n",
        "hooks/bystander.py": "# import a\nimport another\n",
    }
    for rel, text in sources.items():
        with open(os.path.join(repo, ".claude", rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    for name, text in (("test_b.py", "# b.py\n"), ("test_contract.py", "# b.py\n"),
                       ("test_mentions_alpha.py", "# a.py\n"), ("test_declared.py", ""),
                       ("test_c.py", ""), ("test_via_non_hook.py", ""), ("test_other_bystander.py", "")):
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
    ok = (names == ["test_b.py", "test_contract.py", "test_mentions_alpha.py"]
          and declared == ["test_b.py", "test_contract.py", "test_declared.py"])
    print("%-4s a touched tool selects its direct importers' name and mention proofs, function-local imports "
          "included, and no further" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("direct importers: fixture=%s declared=%s" % (names, declared))


def case_importer_selection_covers_harness_roots_and_lib(failures):
    repo = make_repo()
    folders = ("hooks", "tools", "scripts", "hooks/lib", "tools/lib", "scripts/lib")
    touched = []
    for i, folder in enumerate(folders):
        os.makedirs(os.path.join(repo, ".claude", folder), exist_ok=True)
        rel = ".claude/%s/source%d.py" % (folder, i)
        touched.append(rel)
        with open(os.path.join(repo, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
        with open(os.path.join(repo, ".claude", folder, "consumer%d.py" % i), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("def load():\n" + "".join("    import source%d\n" % j for j in range(len(folders))))
        write_proof(repo, "test_consumer%d.py" % i, 0)
    seed_commit(repo)
    harness_tests.EXCLUDED = {}
    expected = ["test_consumer%d.py" % i for i in range(len(folders))]
    bound = harness_tests.bindings(repo, touched)
    actual = {rel: sorted(os.path.basename(p) for p in paths) for rel, paths in bound.items()}
    ok = all(names == expected for names in actual.values())
    print("%-4s modules under hooks, tools and scripts, top level and lib/, select their importers' proofs"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("importer folders: %s" % actual)


def case_proofs_for_post_check_selects_dispatch_verification(failures):
    """The live tree: `hooks/dispatch_table.py` imports `post_check` inside a function, so a
    post_check change runs the dispatch table's proof."""
    selected = [os.path.basename(p) for p in harness_tests.select_for(
        harness_tests.REPO_ROOT, [".claude/tools/post_check.py"])]
    ok = "test_dispatch_table_verification.py" in selected
    print("%-4s live selection for tools/post_check.py includes test_dispatch_table_verification.py"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("live post_check selection: %s" % selected)


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
        fh.write("import sys\n# exercises sub.py\nopen(%r, 'w').write('ran')\nsys.exit(0)\n" % marker)
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


def _run_stubbed(repo, path, default_timeout, outcomes):
    """_run_selected over `path` with run_proof stubbed: each attempt pops the next outcome and the
    timeout it was handed is recorded. Returns (status, timeouts)."""
    seen = []

    def stub(proof, timeout=None, root=None):
        seen.append(timeout)
        return outcomes.pop(0)

    real_run, real_timeout, saved = harness_tests.run_proof, harness_tests._TIMEOUT_SEC, dict(os.environ)
    harness_tests.run_proof, harness_tests._TIMEOUT_SEC = stub, default_timeout
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    statuses = {}
    try:
        _run_quiet(harness_tests._run_selected, [path], False, statuses)
    finally:
        harness_tests.run_proof, harness_tests._TIMEOUT_SEC = real_run, real_timeout
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


def case_a_timeout_with_no_record_is_retried_alone_with_a_longer_timeout(failures):
    """A proof never recorded that times out in the pool is retried alone at three times the timeout."""
    repo, path = _timing_repo("test_unrecorded.py", {})
    status, seen = _run_stubbed(repo, path, 1, [("fail", 1.0, "TIMEOUT after 1s"), ("pass", 0.5, "")])
    ok = status == "pass" and seen == [1, 3]
    print("%-4s a pool timeout with no recorded pass is retried alone with three times the timeout"
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
    case_helper_module_selects_its_importers_proofs,
    case_module_selects_its_direct_importers_proofs,
    case_importer_selection_covers_harness_roots_and_lib,
    case_proofs_for_post_check_selects_dispatch_verification,
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
    case_a_timeout_with_no_record_is_retried_alone_with_a_longer_timeout,
    case_a_timeout_keeps_the_recorded_duration,
)


def case_commit_budget_selection(failures):
    repo = make_repo()
    names = ["test_bound_%d.py" % i for i in range(7)]
    for name in names:
        path = write_proof(repo, name, 0)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("# commit_contract.md\n")
    write_proof(repo, "test_unrelated.py", 1)
    seed_commit(repo)
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    with open(harness_tests.durations_path(), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(zip(names, [10, 10, 10, 1, 0.1, 10.1, None])), fh)
    select = getattr(harness_tests, "select_for_commit", None)
    if select:
        selected, left = select(repo, [".claude/reference/commit_contract.md"])
        actual = [os.path.basename(p) for p in selected]
        empty = select(repo, [])
    else:
        actual, left, empty = [], None, None
    ok = actual == names[:3] and left == 4 and empty == ([], 0)
    print("%-4s commit selection: inner cap, total cap, unrecorded, unrelated and empty scope" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("commit selection: selected=%s left=%s empty=%s" % (actual, left, empty))


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


def case_close_untracked_red_is_advisory_tracked_red_blocks(failures):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_CloseUntrackedRed)
    result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    ok = result.wasSuccessful()
    print("%-4s close: untracked red proof is advisory, tracked red blocks" % ("ok" if ok else "FAIL"))
    for _, trace in result.failures + result.errors:
        failures.append("close untracked red: " + trace.strip().splitlines()[-1])


def main():
    failures, failed_cases = [], 0
    cases = HEAVY_CASES + CASES + (case_commit_budget_selection, case_commit_runner_timeout,
                                   case_a_failure_line_above_the_tail_stays_in_the_excerpt,
                                   case_platform_skip_is_counted_not_passed,
                                   case_a_node_proof_platform_skip_survives_the_tap_comment_prefix,
                                   case_close_untracked_red_is_advisory_tracked_red_blocks)
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
