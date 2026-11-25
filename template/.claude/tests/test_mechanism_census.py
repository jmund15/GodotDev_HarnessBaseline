"""Re-runnable proof for `.claude/tools/mechanism_census.py` (its own docstring owns the census contract).

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
        res = subprocess.run([sys.executable, TOOL, "--line", path, "--main-check", state, "--now", _iso(NOW),
                              "--repo", tmp], capture_output=True, text=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()
    old_day = (NOW - datetime.timedelta(days=33)).date().isoformat()
    fresh_day = (NOW - datetime.timedelta(days=2)).date().isoformat()
    stale = line_for(old_day + "T00:00:00+00:00")
    assert stale.startswith("census stale (%s)" % old_day), "stale line: %r" % stale
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
    res = subprocess.run([sys.executable, TOOL, "--line", census, "--main-check", state, "--now", _iso(NOW),
                          "--repo", tmp], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    line = res.stdout.strip()
    assert "1 item" in line and "main check wall 13.5 min" in line, "wall line: %r" % line
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"checkedTo": "a", "run": {"id": "r", "end": None, "wall": {"close": 600, "gate": None,
                                                                              "finalize": None}}}, fh)
    res = subprocess.run([sys.executable, TOOL, "--line", census, "--main-check", state, "--now", _iso(NOW),
                          "--repo", tmp], capture_output=True, text=True, encoding="utf-8")
    assert "main check: no record" in res.stdout and "13.5" not in res.stdout, "unfinished wall: %r" % res.stdout
    repo = _repo()
    sha = _commit(repo, {"mechanism_census.json": json.dumps({"generatedAt": _iso(NOW - datetime.timedelta(days=1)),
                                                               "changedSinceRuling": [{"item": "x"}]}),
                         "main_check.json": json.dumps(finished)}, "state")
    _git(repo, "update-ref", "refs/remotes/origin/main-check-state", sha)
    res = _tool(repo, "--line", "--now", _iso(NOW))
    assert res.returncode == 0, res.stderr
    assert "1 item" in res.stdout and "main check wall 13.5 min" in res.stdout, "ref line: %r" % (res.stdout, res.stderr)


WALL_SUITES = ("main check", "harness proofs", "publish", "C# gate")
WALL_SIZE = {"totals": {"code": 1234, "tests": 567, "codeFiles": 3, "testsFiles": 2},
             "areas": {"Alpha": {"code": 1000, "tests": 500, "change": {"code": 5, "tests": 0}}},
             "unassigned": {"code": 234, "tests": 67, "change": {"code": 0, "tests": 0}}}


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh)


def _wall_row(when, mode, wall_s):
    """A `harness_runs.json` row with the keys the real log holds (`ts`, `mode`, `wall_s`, `proofs`)."""
    return {"ts": when.timestamp(), "session": "s1", "mode": mode, "head": "h", "wall_s": wall_s, "selected": 1,
            "reused": 0, "queue_s": 0, "proofs": [{"proof": "test_a.py", "status": "pass", "run_s": 1.0,
                                                   "wait_s": 0.0, "reused": False}]}


def _walls_repo(drop=()):
    """A planted repo with one record per wall suite, each shaped like the real record; a suite in `drop` has none."""
    repo = _repo()
    _commit(repo, {"README.md": "x\n"}, "init")
    state = os.path.join(repo, "main_check.json")
    if "main check" not in drop:
        _write_json(state, {"checkedTo": "a", "run": {"id": "r", "end": _iso(NOW),
                                                      "wall": {"close": 600, "gate": 210, "finalize": 0}}})
    if "harness proofs" not in drop:
        rows = [_wall_row(NOW - datetime.timedelta(hours=2), "close", 90.0),
                _wall_row(NOW - datetime.timedelta(hours=1), "close", 200.0),
                _wall_row(NOW - datetime.timedelta(minutes=1), "for", 999.0)]
        _write_json(os.path.join(repo, ".claude", "logs", "harness_runs.json"), {"runs": rows})
    if "publish" not in drop:
        base = os.path.join(repo, ".claude", ".cache", "baseline-publish")
        _write_json(os.path.join(base, "20261120T021057Z-6e0fe2ed.json"),
                    {"id": "20261120T021057Z-6e0fe2ed", "dry_run": False, "ci_wait_s": 341.726,
                     "started_at": "2026-11-20T02:10:57.418440Z", "finished_at": "2026-11-20T02:31:42.845090Z",
                     "steps": []})
        _write_json(os.path.join(base, "20261121T090000Z-dryrun00.json"),
                    {"id": "20261121T090000Z-dryrun00", "dry_run": True, "ci_wait_s": 0,
                     "started_at": "2026-11-21T09:00:00.000000Z", "finished_at": "2026-11-21T09:01:00.000000Z",
                     "steps": []})
    if "C# gate" not in drop:
        ledger = os.path.join(repo, ".git", "harness", "gate_ledger")
        _write_json(os.path.join(ledger, "20261119-123015-29992.result.json"),
                    {"id": "20261119-123015-29992", "mode": "full", "status": "PASS", "exitCode": 0,
                     "startedAt": "2026-11-19T18:30:15Z", "finishedAt": "2026-11-19T18:34:37.1864288Z",
                     "reusedFrom": None})
        _write_json(os.path.join(ledger, "20261121-090000-1.result.json"),
                    {"id": "20261121-090000-1", "mode": "full", "status": "PASS", "exitCode": 0,
                     "startedAt": "2026-11-21T09:00:00Z", "finishedAt": "2026-11-21T09:01:00.0000000Z",
                     "reusedFrom": "8a24e26a14ecee6d1de9aa0d2decde66e2e85392"})
    return repo, state


def case_census_line_prints_totals_and_each_suite_wall():
    mod = _module()
    repo, state = _walls_repo()
    census = {"generatedAt": _iso(NOW - datetime.timedelta(days=1)), "changedSinceRuling": [], "size": WALL_SIZE}
    line = mod.census_line(census, NOW, mod.last_walls(repo, state))
    for part in ("code 1,234 lines (+5)", "tests 567 lines (+0)", "main check wall 13.5 min",
                 "harness proofs wall 3.3 min (close)", "publish wall 20.8 min", "C# gate wall 4.4 min (full)"):
        assert part in line, "%r missing from %r" % (part, line)


def case_a_suite_with_no_record_says_so():
    mod = _module()
    census = {"generatedAt": _iso(NOW - datetime.timedelta(days=1)), "changedSinceRuling": [], "size": WALL_SIZE}
    for suite in WALL_SUITES:
        repo, state = _walls_repo(drop=(suite,))
        line = mod.census_line(census, NOW, mod.last_walls(repo, state))
        assert "%s: no record" % suite in line, "%s: %r" % (suite, line)
        assert line.count("no record") == 1 and line.count(" wall ") == 3, "%s: %r" % (suite, line)


def case_dry_run_and_reused_records_are_skipped():
    mod = _module()
    repo, state = _walls_repo()
    walls = {suite: (sec, label) for suite, sec, label in mod.last_walls(repo, state)}
    assert walls["harness proofs"] == (200.0, "close"), walls
    assert walls["main check"][0] == 810.0, walls
    assert walls["publish"][1] is None and abs(walls["publish"][0] - 1245.4) < 0.5, "dry run read: %r" % (walls,)
    assert walls["C# gate"][1] == "full" and abs(walls["C# gate"][0] - 262.2) < 0.5, "reused read: %r" % (walls,)


def case_a_helper_or_sibling_edit_is_not_the_proofs_own():
    """The proof file is the exact file: a fix touching a `tests/_*` helper or a sibling proof beside it is another
    file, so the clearing kind is `both`, never `proof-only`."""
    repo = _repo()
    base = _commit(repo, {".claude/tests/test_a.py": "a1", ".claude/tests/_helper.py": "h1",
                          ".claude/tests/test_ab.py": "x1"}, "base")
    c_helper = _commit(repo, {".claude/tests/test_a.py": "a2", ".claude/tests/_helper.py": "h2"},
                       "fix(tests): proof and helper")
    c_sibling = _commit(repo, {".claude/tests/test_a.py": "a3", ".claude/tests/test_ab.py": "x2"},
                        "fix(tests): proof and sibling")
    runs = [
        _run(1.0, "close", base, [("test_a.py", "fail")]),
        _run(2.0, "close", c_helper, [("test_a.py", "pass")]),
        _run(3.0, "close", base, [("test_a.py", "fail")]),
        _run(4.0, "close", c_sibling, [("test_a.py", "pass")]),
    ]
    logs = _checkout_logs(repo, runs)
    census, _ = _census(repo, "--checkout", logs, "--transcripts", os.path.join(repo, "none"))
    got = _proof(census, "test_a.py")["clearing"]
    assert got.get("both") == 2 and got.get("proof-only") == 0, "helper or sibling counted as the proof's own: %s" % got


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


def _module():
    spec = importlib.util.spec_from_file_location("mechanism_census", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _census_inproc(repo, *args):
    """The census built in process by the module's `main`; decision cases call functions, not the CLI."""
    out = os.path.join(repo, "census.json")
    empty = os.path.join(repo, "no-transcripts")
    os.makedirs(empty, exist_ok=True)
    rc = _module().main(["--repo", repo, "--ref", "HEAD", "--days", "30", "--transcripts", empty, "--out", out]
                        + list(args))
    if rc != 0:
        raise AssertionError("census main exit %d" % rc)
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


AREA_ROWS = [["Alpha", ".claude/tools/alpha"], ["Beta", ".claude/tests/test_beta"]]
ADAPTATION = ".claude/skills/project_subsystems/adaptation.json"


def case_area_totals_reconcile_with_an_unassigned_row():
    repo = _repo()
    _commit(repo, {ADAPTATION: json.dumps({"areas": AREA_ROWS}), ".claude/tools/alpha_a.py": "1\n2\n3\n",
                   ".claude/tools/stray.py": "1\n2\n", ".claude/tests/test_beta.py": "1\n2\n3\n4\n",
                   ".claude/tests/test_none.py": "1\n"}, "base")
    size = _census_inproc(repo)["size"]
    areas, un, totals = size["areas"], size["unassigned"], size["totals"]
    assert areas["Alpha"]["code"] == 3 and areas["Alpha"]["tests"] == 0, areas
    assert areas["Beta"]["tests"] == 4 and areas["Beta"]["code"] == 0, areas
    assert un["code"] == 2 and un["tests"] == 1, un
    assert sorted(un["files"]) == [".claude/tests/test_none.py", ".claude/tools/stray.py"], un["files"]
    assert totals["code"] == areas["Alpha"]["code"] + areas["Beta"]["code"] + un["code"], totals
    assert totals["tests"] == areas["Alpha"]["tests"] + areas["Beta"]["tests"] + un["tests"], totals


def case_absent_areas_key_puts_every_file_in_unassigned():
    repo = _repo()
    _commit(repo, {".claude/tools/a.py": "1\n2\n", ".claude/tests/test_a.py": "1\n"}, "base")
    size = _census_inproc(repo)["size"]
    assert size["areas"] == {}, size["areas"]
    assert size["unassigned"]["code"] == 2 and size["unassigned"]["tests"] == 1, size["unassigned"]


def case_longest_prefix_wins():
    repo = _repo()
    rows = [["Outer", ".claude/tools/"], ["Inner", ".claude/tools/alpha"]]
    _commit(repo, {ADAPTATION: json.dumps({"areas": rows}), ".claude/tools/alpha_x.py": "1\n",
                   ".claude/tools/beta.py": "1\n2\n"}, "base")
    size = _census_inproc(repo)["size"]
    assert size["areas"]["Inner"]["code"] == 1, size["areas"]
    assert size["areas"]["Outer"]["code"] == 2, size["areas"]
    assert size["unassigned"]["code"] == 0, size["unassigned"]


def case_change_since_last_reading():
    repo = _repo()
    _commit(repo, {ADAPTATION: json.dumps({"areas": AREA_ROWS}), ".claude/tools/alpha_a.py": "1\n2\n3\n",
                   ".claude/tools/stray.py": "1\n2\n", ".claude/tests/test_beta.py": "1\n2\n3\n4\n"}, "base")
    readings = os.path.join(repo, "census_readings.jsonl")
    prior = {"date": "2026-11-01", "ref": "HEAD", "head": "0" * 40, "windowDays": 30,
             "totals": {"code": 3, "tests": 4},
             "areas": {"Alpha": {"code": 1, "tests": 0}, "Beta": {"code": 0, "tests": 4},
                       "unassigned": {"code": 2, "tests": 0}}, "mostFixed": []}
    with open(readings, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(prior) + "\n")
    size = _census_inproc(repo, "--readings", readings)["size"]
    assert size["areas"]["Alpha"]["change"] == {"code": 2, "tests": 0}, size["areas"]["Alpha"]
    assert size["areas"]["Beta"]["change"] == {"code": 0, "tests": 0}, size["areas"]["Beta"]
    assert size["unassigned"]["change"] == {"code": 0, "tests": 0}, size["unassigned"]
    size = _census_inproc(repo, "--readings", os.path.join(repo, "absent.jsonl"))["size"]
    assert size["areas"]["Alpha"]["change"] is None and size["unassigned"]["change"] is None, size


def case_record_appends_once():
    import contextlib
    import io
    repo = _repo()
    _commit(repo, {ADAPTATION: json.dumps({"areas": AREA_ROWS}), ".claude/tools/alpha_a.py": "1\n"}, "base")
    census = _census_inproc(repo)
    census_path = os.path.join(repo, "census.json")
    readings = os.path.join(repo, "census_readings.jsonl")
    mod = _module()
    assert mod.main(["--record", census_path, "--repo", repo, "--readings", readings]) == 0
    with open(readings, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    assert len(rows) == 1 and rows[0]["head"] == census["reading"]["head"], rows
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mod.main(["--record", census_path, "--repo", repo, "--readings", readings]) == 0
    with open(readings, encoding="utf-8") as fh:
        again = [line for line in fh if line.strip()]
    assert len(again) == 1, "second record wrote a row: %d rows" % len(again)
    assert "already recorded" in buf.getvalue(), buf.getvalue()


def case_record_cli_names_already_recorded_on_a_second_run():
    repo = _repo()
    _commit(repo, {ADAPTATION: json.dumps({"areas": AREA_ROWS}), ".claude/tools/alpha_a.py": "1\n"}, "base")
    _census_inproc(repo)
    census_path = os.path.join(repo, "census.json")
    readings = os.path.join(repo, "census_readings.jsonl")
    cmd = [sys.executable, TOOL, "--record", census_path, "--repo", repo, "--readings", readings]
    first = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    second = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    assert "already recorded" in second.stdout, second.stdout


def _fixed_history(repo, targets):
    """Each path gets as many fix commits as its target: one base commit, then round i touches every path whose target
    is at least i."""
    _commit(repo, {p: "base\n" for p in targets}, "chore: base")
    for i in range(1, max(targets.values()) + 1):
        _commit(repo, {p: "round %d\n" % i for p, t in targets.items() if t >= i}, "fix(hooks): round %d" % i)


def _most_fixed(repo):
    return {r["path"]: r["fixes"] for r in _census_inproc(repo)["reading"]["mostFixed"]}


def _fix_hist(repo, paths, reading=None, days=30):
    return _module().fix_history(repo, paths, "HEAD", days, reading)


def case_top_five_percent_of_fixed_code_files():
    targets = {".claude/hooks/f%02d.py" % i: 3 for i in range(40)}
    targets[".claude/hooks/f00.py"] = 10
    targets[".claude/hooks/f01.py"] = 9
    repo = _repo()
    _fixed_history(repo, targets)
    got = _most_fixed(repo)
    assert got == {".claude/hooks/f00.py": 10, ".claude/hooks/f01.py": 9}, got
    tied = dict(targets)
    tied[".claude/hooks/f02.py"] = 9
    repo = _repo()
    _fixed_history(repo, tied)
    got = _most_fixed(repo)
    assert set(got) == {".claude/hooks/f00.py", ".claude/hooks/f01.py", ".claude/hooks/f02.py"}, got


def case_single_fix_files_are_never_listed():
    repo = _repo()
    _fixed_history(repo, {".claude/hooks/f%02d.py" % i: 1 for i in range(40)})
    assert _most_fixed(repo) == {}, _most_fixed(repo)


def _redesign_repo(body):
    """One file with three fixes, then a commit with commit-message `body` that carries the redesign."""
    x = ".claude/hooks/x.py"
    repo = _repo()
    _commit(repo, {x: "base\n"}, "chore: base")
    for i in range(3):
        _commit(repo, {x: "fix %d\n" % i}, "fix(hooks): round %d" % i)
    _commit(repo, {x: "redesigned\n", ".claude/plans/x-plan.md": "plan\n"}, "refactor(hooks): x\n\n" + body)
    return repo, x


def case_redesign_commit_restarts_the_count():
    repo, x = _redesign_repo("Redesigns: .claude/hooks/x.py\nPlan: .claude/plans/x-plan.md")
    assert _fix_hist(repo, [x])[x]["count"] == 0, _fix_hist(repo, [x])[x]
    assert x not in _most_fixed(repo), _most_fixed(repo)


def case_redesigns_without_plan_trailer_restarts_nothing():
    repo, x = _redesign_repo("Redesigns: .claude/hooks/x.py")
    assert _fix_hist(repo, [x])[x]["count"] == 3, _fix_hist(repo, [x])[x]


def case_plan_trailer_naming_a_missing_file_restarts_nothing():
    repo, x = _redesign_repo("Redesigns: .claude/hooks/x.py\nPlan: .claude/plans/absent.md")
    assert _fix_hist(repo, [x])[x]["count"] == 3, _fix_hist(repo, [x])[x]


def case_plan_cited_share_since_listing():
    x = ".claude/hooks/x.py"
    repo = _repo()
    _commit(repo, {x: "base\n"}, "chore: base")
    listed_day = (NOW - datetime.timedelta(days=3)).date().isoformat()
    _commit(repo, {x: "1\n"}, "fix(hooks): one")
    _commit(repo, {x: "2\n"}, "fix(hooks): [abc S1] two")
    _commit(repo, {x: "3\n"}, "fix(hooks): three")
    reading = {"date": listed_day, "head": "0" * 40,
               "mostFixed": [{"path": x, "fixes": 3, "listedSince": listed_day, "sinceListing": {"fixes": 0, "cited": 0}}]}
    hist = _fix_hist(repo, [x], reading)[x]
    assert hist["listed"] is True and hist["sinceListing"] == {"fixes": 3, "cited": 1}, hist


def case_share_since_listing_reaches_past_the_window():
    x = ".claude/hooks/x.py"
    repo = _repo()
    _commit(repo, {x: "base\n"}, "chore: base", when=NOW - datetime.timedelta(days=50))
    _commit(repo, {x: "a\n"}, "fix(hooks): old", when=NOW - datetime.timedelta(days=40))
    _commit(repo, {x: "b\n"}, "fix(hooks): newer", when=NOW - datetime.timedelta(days=10))
    listed_day = (NOW - datetime.timedelta(days=45)).date().isoformat()
    reading = {"date": listed_day, "head": "0" * 40,
               "mostFixed": [{"path": x, "fixes": 2, "listedSince": listed_day, "sinceListing": {"fixes": 0, "cited": 0}}]}
    hist = _fix_hist(repo, [x], reading)[x]
    assert hist["count"] == 1 and hist["sinceListing"]["fixes"] == 2, hist


def case_review_audit_fold_fixes_stay_excluded():
    x = ".claude/hooks/x.py"
    repo = _repo()
    _commit(repo, {x: "1\n"}, "base")
    _commit(repo, {x: "2\n"}, "fix(hooks): real")
    for i, subj in enumerate(("fix(review): a", "fix(audit): b", "fix(fold): c", "fix(review-fold): d")):
        _commit(repo, {x: "%d\n" % (i + 3)}, subj)
    assert _fix_hist(repo, [x])[x]["count"] == 1, _fix_hist(repo, [x])[x]


def case_unresolved_path_returns_unresolved_flag():
    repo = _repo()
    _commit(repo, {".claude/hooks/x.py": "1\n"}, "base")
    hist = _fix_hist(repo, [".claude/hooks/missing.py"])
    assert hist[".claude/hooks/missing.py"] == {"unresolved": True}, hist


def case_no_reading_leaves_listing_unknown():
    repo = _repo()
    _commit(repo, {".claude/hooks/x.py": "1\n"}, "base")
    hist = _fix_hist(repo, [".claude/hooks/x.py"], reading=None)
    assert hist[".claude/hooks/x.py"]["listed"] is None, hist
    hist = _fix_hist(repo, [".claude/hooks/x.py"], reading={"date": "2026-11-21", "mostFixed": []})
    assert hist[".claude/hooks/x.py"]["listed"] is False, hist


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_") and callable(v)]


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except (Exception, SystemExit) as exc:  # the proof reports every red, not the first; a CLI usage exit is one
            failed += 1
            last = (str(exc).strip().splitlines() or ["?"])[-1][:300]
            print("FAIL %s: %s" % (fn.__name__, last))
    print("%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
