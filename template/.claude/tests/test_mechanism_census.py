"""Re-runnable proof for `.claude/tools/mechanism_census.py` (verifarch S7).

Each case builds its own git repo, checkouts' run logs, transcripts and state files, runs the tool's CLI, and reads
the JSON it writes. No case reads the machine's real logs.

    python3 .claude/tests/test_mechanism_census.py
"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.normpath(os.path.join(HERE, "..", "tools", "mechanism_census.py"))
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _git(repo, *args, date=None):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                          "-C", repo] + list(args), capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), out.stderr.strip()))
    return out.stdout.strip()


def _repo():
    d = tempfile.mkdtemp(prefix="census_case_")
    _git(d, "init", "-q")
    return d


def _commit(repo, files, subject, when=None):
    for path, text in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", subject, date=_iso(when) if when else None)
    return _git(repo, "rev-parse", "HEAD")


def _checkout_logs(root, runs, flaky=()):
    """A checkout's own log folder: `.claude/logs/harness_runs.json` and `harness_flaky.jsonl`."""
    logs = os.path.join(root, ".claude", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, "harness_runs.json"), "w", encoding="utf-8") as fh:
        json.dump({"runs": list(runs)}, fh)
    with open(os.path.join(logs, "harness_flaky.jsonl"), "w", encoding="utf-8") as fh:
        for row in flaky:
            fh.write(json.dumps(row) + "\n")
    return root


def _run(ts, mode, head, proofs):
    return {"ts": ts, "mode": mode, "head": head, "session": "s1", "proofs": [
        {"proof": name, "status": status, "run_s": 10.0, "wait_s": 0.0, "reused": False} for name, status in proofs]}


def _tool(repo, *args):
    empty = os.path.join(repo, "no-transcripts")
    os.makedirs(empty, exist_ok=True)
    cmd = [sys.executable, TOOL, "--repo", repo, "--ref", "HEAD", "--days", "30", "--transcripts", empty] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def _census(repo, *args):
    out = os.path.join(repo, "census.json")
    res = _tool(repo, "--out", out, *args)
    if res.returncode != 0:
        raise AssertionError("tool exit %d: %s" % (res.returncode, (res.stderr or res.stdout).strip()[-400:]))
    with open(out, encoding="utf-8") as fh:
        return json.load(fh), res


def _proof(census, name):
    for row in census["proofs"]:
        if row["name"] == name:
            return row
    raise AssertionError("no proof row for %s" % name)


def _hook(census, name):
    for row in census["hooks"]:
        if row["name"] == name:
            return row
    raise AssertionError("no hook row for %s" % name)


def case_a_proof_copy_inside_a_fixture_tree_is_no_proof_row():
    """The runner collects proofs directly under `.claude/tests/` only; a copy under a fixture tree is data."""
    repo = _repo()
    _commit(repo, {".claude/tests/test_live.py": "x",
                   ".claude/tests/fixtures/tree/.claude/tests/test_live.py": "copy",
                   ".claude/tests/fixtures/tree/.claude/tests/test_copy_only.py": "copy"}, "base")
    census, _ = _census(repo, "--transcripts", os.path.join(repo, "none"))
    paths = sorted(row["path"] for row in census["proofs"])
    assert paths == [".claude/tests/test_live.py"], paths


def case_clearing_kinds_land_in_their_buckets():
    repo = _repo()
    base = _commit(repo, {".claude/tests/test_a.py": "a1", ".claude/tests/test_b.py": "b1", ".claude/tests/test_c.py": "c1",
                          ".claude/tests/test_d.py": "d1", ".claude/tests/test_e.py": "e1", ".claude/hooks/h.py": "h1"}, "base")
    c_test = _commit(repo, {".claude/tests/test_a.py": "a2"}, "fix(tests): a proof edit")
    c_code = _commit(repo, {".claude/hooks/h.py": "h2"}, "fix(hooks): a code edit")
    c_both = _commit(repo, {".claude/tests/test_c.py": "c2", ".claude/hooks/h.py": "h3"}, "fix(hooks): both edits")
    runs = [
        _run(1.0, "close", base, [("test_a.py", "fail"), ("test_b.py", "fail"), ("test_c.py", "fail"),
                                  ("test_d.py", "fail"), ("test_e.py", "fail")]),
        _run(2.0, "close", c_test, [("test_a.py", "pass")]),
        _run(3.0, "close", c_code, [("test_b.py", "pass")]),
        _run(4.0, "close", c_both, [("test_c.py", "pass")]),
        _run(5.0, "close", base, [("test_d.py", "pass")]),
    ]
    logs = _checkout_logs(repo, runs)
    census, _ = _census(repo, "--checkout", logs, "--transcripts", os.path.join(repo, "none"))
    want = {"test_a.py": "proof-only", "test_b.py": "other-only", "test_c.py": "both"}
    for name, kind in want.items():
        got = _proof(census, name)["clearing"]
        assert got.get(kind) == 1, "%s clearing %s: %s" % (name, kind, got)
    assert _proof(census, "test_d.py")["clearing"].get("same-head") == 1, "same-head bucket"
    assert _proof(census, "test_e.py")["clearing"].get("unpaired") == 1, "unpaired bucket"


def case_proof_red_and_flaky_in_two_checkouts_count_both():
    repo = _repo()
    head = _commit(repo, {".claude/tests/test_x.py": "x"}, "base")
    now_ts = NOW.timestamp()
    main_logs = _checkout_logs(repo, [_run(now_ts, "close", head, [("test_x.py", "fail")])])
    other = _checkout_logs(_repo(), [], flaky=[{"ts": now_ts, "proof": "test_x.py", "first": "FAIL", "failures": [],
                                                "retry_seconds": 3.0}])
    census, _ = _census(repo, "--checkout", main_logs, "--checkout", other)
    row = _proof(census, "test_x.py")
    assert row["fails"] == 1, "red in one checkout: %s" % row
    assert row["closeReds"] == 1, "close red: %s" % row
    assert row["flaky"] == 1, "flaky in the other checkout: %s" % row


def case_fix_review_audit_and_fold_commits_not_counted():
    repo = _repo()
    _commit(repo, {".claude/hooks/h.py": "1"}, "base")
    _commit(repo, {".claude/hooks/h.py": "2"}, "fix(hooks): a real fix")
    _commit(repo, {".claude/hooks/h.py": "3"}, "fix(review): a review artifact")
    _commit(repo, {".claude/hooks/h.py": "4"}, "fix(audit): an audit artifact")
    _commit(repo, {".claude/hooks/h.py": "5"}, "fix(review-fold): a fold artifact")
    _commit(repo, {".claude/hooks/h.py": "6"}, "fix(fold): a fold artifact")
    census, _ = _census(repo, "--checkout", _checkout_logs(repo, []))
    assert _hook(census, "h.py")["fixCommits"] == 1, "fix count: %s" % _hook(census, "h.py")["fixCommits"]


def case_cleared_row_lists_files_and_names_no_cause():
    repo = _repo()
    c1 = _commit(repo, {".claude/tests/test_a.py": "a1", "other.py": "p1"}, "base")
    c2 = _commit(repo, {".claude/tests/test_a.py": "a2", "other.py": "p2"}, "a fix and an unrelated peer edit")
    state = os.path.join(repo, "main_check.json")
    row = {"name": "test_a.py", "kind": "proof", "platform": "windows", "commit": c1, "session": None,
           "firstSeen": _iso(NOW), "clearedAt": _iso(NOW), "clearedBy": c2}
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"checkedTo": c2, "rows": [row]}, fh)
    census, _ = _census(repo, "--checkout", _checkout_logs(repo, []), "--main-check", state)
    cleared = census["mainCheck"]["cleared"]
    assert len(cleared) == 1, "cleared rows: %s" % cleared
    files = cleared[0]["files"]
    assert ".claude/tests/test_a.py" in files and "other.py" in files, "files: %s" % files
    assert not any(k in cleared[0] for k in ("cause", "verdict")), "cause key: %s" % sorted(cleared[0])


def case_output_carries_no_verdict_column():
    repo = _repo()
    _commit(repo, {".claude/tests/test_a.py": "a1"}, "base")
    logs = _checkout_logs(repo, [_run(NOW.timestamp(), "close", "x", [("test_a.py", "pass")])])
    census, res = _census(repo, "--checkout", logs)
    table = _tool(repo, "--checkout", logs, "--table", "proofs", "--sort", "runs")
    assert table.returncode == 0, table.stderr
    header = table.stdout.splitlines()[0].lower()
    for word in ("verdict", "keep", "cut", "ruling"):
        assert word not in header.split(), "header has %r: %s" % (word, header)
    for row in census["proofs"]:
        assert "verdict" not in row, "json row has a verdict key"


def case_items_changed_since_a_ruling_are_listed_and_unchanged_are_not():
    repo = _repo()
    old = NOW - datetime.timedelta(days=10)
    _commit(repo, {".claude/hooks/k.py": "k1"}, "fix(hooks): an old fix", when=old)
    _commit(repo, {".claude/hooks/h.py": "h1"}, "fix(hooks): an earlier fix", when=old)
    _commit(repo, {".claude/hooks/h.py": "h2"}, "fix(hooks): a fix after the ruling")
    rulings = os.path.join(repo, "census_reviews.jsonl")
    day = (NOW - datetime.timedelta(days=2)).date().isoformat()
    with open(rulings, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": day, "item": ".claude/hooks/h.py", "kind": "hook", "proposal": "keep",
                             "status": "standing"}) + "\n")
        fh.write(json.dumps({"date": day, "item": ".claude/hooks/k.py", "kind": "hook", "proposal": "keep",
                             "status": "standing"}) + "\n")
    census, _ = _census(repo, "--checkout", _checkout_logs(repo, []), "--rulings", rulings)
    changed = [row["item"] for row in census["changedSinceRuling"]]
    assert ".claude/hooks/h.py" in changed, "changed item missing: %s" % changed
    assert ".claude/hooks/k.py" not in changed, "unchanged item listed: %s" % changed


def case_a_red_or_deny_after_the_ruling_lists_an_item_and_one_before_does_not():
    repo = _repo()
    old = NOW - datetime.timedelta(days=10)
    _commit(repo, {".claude/tests/test_new.py": "n", ".claude/tests/test_old.py": "o",
                   ".claude/hooks/denynew.py": "dn", ".claude/hooks/denyold.py": "do"}, "base", when=old)
    proofs = _checkout_logs(repo, [_run((NOW - datetime.timedelta(days=5)).timestamp(), "close", "h1",
                                        [("test_old.py", "fail")]),
                                    _run(NOW.timestamp(), "close", "h1", [("test_new.py", "fail")])])
    tdir = os.path.join(repo, "transcripts")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "sess1.jsonl"), "w", encoding="utf-8") as fh:
        for when, hook in ((NOW - datetime.timedelta(days=5), "denyold.py"), (NOW, "denynew.py")):
            fh.write(json.dumps({"timestamp": _iso(when), "message": {"content": [
                {"type": "tool_result", "content": "PreToolUse:hooks/%s DENIED command blocked" % hook}]}}) + "\n")
    day = (NOW - datetime.timedelta(days=2)).date().isoformat()
    rulings = os.path.join(repo, "census_reviews.jsonl")
    with open(rulings, "w", encoding="utf-8") as fh:
        for item in (".claude/tests/test_new.py", ".claude/tests/test_old.py", ".claude/hooks/denynew.py",
                     ".claude/hooks/denyold.py"):
            fh.write(json.dumps({"date": day, "item": item, "proposal": "keep", "status": "standing"}) + "\n")
    census, _ = _census(repo, "--checkout", proofs, "--transcripts", tdir, "--rulings", rulings)
    changed = {row["item"]: row for row in census["changedSinceRuling"]}
    assert set(changed) == {".claude/tests/test_new.py", ".claude/hooks/denynew.py"}, "changed: %s" % sorted(changed)
    assert changed[".claude/tests/test_new.py"]["reds"] == 1, "red count: %s" % changed[".claude/tests/test_new.py"]
    assert changed[".claude/hooks/denynew.py"]["denies"] == 1, "deny count: %s" % changed[".claude/hooks/denynew.py"]


def case_census_older_than_32_days_gives_the_stale_form():
    tmp = tempfile.mkdtemp(prefix="census_line_")
    state = os.path.join(tmp, "main_check.json")
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"checkedTo": "a", "run": {"end": None, "wall": {"close": 60, "gate": None, "finalize": None}}}, fh)
    def line_for(generated):
        path = os.path.join(tmp, "census_%s.json" % generated[:10])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generatedAt": generated, "changedSinceRuling": [{"item": "x"}]}, fh)
        res = subprocess.run([sys.executable, TOOL, "--line", path, "--main-check", state, "--now", _iso(NOW)],
                             capture_output=True, text=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()
    old_day = (NOW - datetime.timedelta(days=33)).date().isoformat()
    fresh_day = (NOW - datetime.timedelta(days=2)).date().isoformat()
    stale = line_for(old_day + "T00:00:00+00:00")
    assert stale == "census stale (%s)" % old_day, "stale line: %r" % stale
    fresh = line_for(fresh_day + "T00:00:00+00:00")
    assert "stale" not in fresh and "1 item" in fresh, "fresh line: %r" % fresh


def case_census_line_names_the_last_finished_main_check_wall():
    tmp = tempfile.mkdtemp(prefix="census_wall_")
    census = os.path.join(tmp, "census.json")
    with open(census, "w", encoding="utf-8") as fh:
        json.dump({"generatedAt": _iso(NOW - datetime.timedelta(days=1)),
                   "changedSinceRuling": [{"item": "x"}]}, fh)
    state = os.path.join(tmp, "main_check.json")
    finished = {"checkedTo": "a", "run": {"id": "r", "end": _iso(NOW),
                                          "wall": {"close": 600, "gate": 210, "finalize": 0}}}
    with open(state, "w", encoding="utf-8") as fh:
        json.dump(finished, fh)
    res = subprocess.run([sys.executable, TOOL, "--line", census, "--main-check", state, "--now", _iso(NOW)],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    line = res.stdout.strip()
    assert "1 item" in line and "main check wall 13.5 min" in line, "wall line: %r" % line
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"checkedTo": "a", "run": {"id": "r", "end": None, "wall": {"close": 600, "gate": None,
                                                                              "finalize": None}}}, fh)
    res = subprocess.run([sys.executable, TOOL, "--line", census, "--main-check", state, "--now", _iso(NOW)],
                         capture_output=True, text=True, encoding="utf-8")
    assert res.stdout.strip() == "census: 1 item changed since their last ruling", "unfinished wall: %r" % res.stdout
    repo = _repo()
    sha = _commit(repo, {"mechanism_census.json": json.dumps({"generatedAt": _iso(NOW - datetime.timedelta(days=1)),
                                                               "changedSinceRuling": [{"item": "x"}]}),
                         "main_check.json": json.dumps(finished)}, "state")
    _git(repo, "update-ref", "refs/remotes/origin/main-check-state", sha)
    res = _tool(repo, "--line", "--now", _iso(NOW))
    assert res.returncode == 0, res.stderr
    assert "1 item" in res.stdout and "main check wall 13.5 min" in res.stdout, "ref line: %r" % (res.stdout, res.stderr)


def case_fix_commit_sampling_rule():
    spec = importlib.util.spec_from_file_location("mechanism_census", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    index = {}
    for i in range(8):
        index[".claude/hooks/hot%d.py" % i] = [{"sha": "h%d_%02d" % (i, j), "date": "2026-11-01", "subject": "fix(x): y",
                                               "files": 1} for j in range(20)]
    index[".claude/hooks/warm.py"] = [{"sha": "w_%02d" % j, "date": "2026-11-01", "subject": "fix(x): y", "files": 1}
                                      for j in range(6)]
    index[".claude/hooks/cold.py"] = [{"sha": "c_%02d" % j, "date": "2026-11-01", "subject": "fix(x): y", "files": 1}
                                      for j in range(5)]
    index[".claude/tests/test_big.py"] = [{"sha": "t_%02d" % j, "date": "2026-11-01", "subject": "fix(x): y", "files": 1}
                                          for j in range(10)]
    picked = mod.sample_fix_commits(index)
    shas = [p["sha"] for p in picked]
    assert len(shas) == len(set(shas)), "a commit sampled twice"
    assert len([s for s in shas if s.startswith("h")]) == 8 * 15, "hot files take 15 each"
    assert len([s for s in shas if s.startswith("w_")]) == 2, "other files take 2 each"
    assert not any(s.startswith(("c_", "t_")) for s in shas), "under 6 fixes or a test file is sampled"


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_") and callable(v)]


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # the proof reports every red, not the first
            failed += 1
            last = (str(exc).strip().splitlines() or ["?"])[-1][:300]
            print("FAIL %s: %s" % (fn.__name__, last))
    print("%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
