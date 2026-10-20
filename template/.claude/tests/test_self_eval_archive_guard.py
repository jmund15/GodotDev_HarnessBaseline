"""Re-runnable proof for hooks/self_eval_archive_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds the
hook a real PostToolUse payload naming a real file on disk and asserts on the channel it
emits, so a regression shows up as a failing case rather than as a surprise silence.

    python3 .claude/tests/test_self_eval_archive_guard.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "hooks", "self_eval_archive_guard.py")
LIVE_ARCHIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "self_evaluate_archive.json")


def base_doc():
    return {
        "Self_Evaluate_Themes": {"patterns": {"A": "discipline", "B": "docs"}},
        "structured_entries": [
            {"id": 1, "outcome": "clean", "pattern": "A"},
            {"id": 100, "outcome": "correction", "pattern": None},
        ],
    }


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0 or (result.stderr or "").strip() or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]
        ))
    return result


def run_on_path(file_path, tool_name="Write", script=None):
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path},
               "tool_response": {"type": "create"}}
    r = checked_run([sys.executable, script or HOOK], input=json.dumps(payload),
                        capture_output=True, text=True, timeout=30)
    out = (r.stdout or "").strip()
    if not out:
        return "{}", ""
    try:
        doc = json.loads(out)
    except ValueError:
        return "UNPARSED", out
    hook = doc.get("hookSpecificOutput") or {}
    context = hook.get("additionalContext")
    return ("nudge" if context else "{}"), (context or "")


def write_archive(tmpdir, name, text):
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def main():
    failures = []
    cases_run = []
    tmpdir = tempfile.mkdtemp(prefix="archguard_")

    def case(label, path, expected_channel, needle=""):
        cases_run.append(label)
        got_channel, reason = run_on_path(path)
        ok = got_channel == expected_channel and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                             % (label, expected_channel, got_channel, reason[:300]))

    bad_exit = write_archive(tmpdir, "bad_exit.py", "raise SystemExit(7)\n")
    bad_trace = write_archive(tmpdir, "bad_trace.py", "print('Traceback (most recent call last):')\n")
    bad_stderr = write_archive(tmpdir, "bad_stderr.py", "import sys; print('planted error', file=sys.stderr)\n")
    for label, script in (
        ("E14: the proof rejects a nonzero hook exit", bad_exit),
        ("E14: the proof rejects a traceback on stderr or stdout", bad_trace),
        ("E14: the proof rejects stderr even with exit zero", bad_stderr),
    ):
        cases_run.append(label)
        try:
            run_on_path(os.path.join(tmpdir, "self_evaluate_archive.json"), script=script)
            ok = False
        except RuntimeError:
            ok = True
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    # 1. valid file -> {}
    valid_path = write_archive(tmpdir, "self_evaluate_archive.json",
                                json.dumps(base_doc()))
    case("valid file -> {}", valid_path, "{}")

    # 2. other path -> {} (never opened, never parsed)
    case("other path -> {}", os.path.join(tmpdir, "unrelated.json"), "{}")

    # 3. bad outcome -> nudge naming the id
    doc = base_doc()
    doc["structured_entries"].append({"id": 2, "outcome": "bogus", "pattern": "A"})
    p = write_archive(tmpdir, "bad_outcome_self_evaluate_archive.json", json.dumps(doc))
    case("bad outcome -> nudge naming the id", p, "nudge", "id=2")

    # 4. null pattern on correction id 300 (past legacy) -> flagged
    doc = base_doc()
    doc["structured_entries"].append({"id": 300, "outcome": "correction", "pattern": None})
    p = write_archive(tmpdir, "null_pattern_new_self_evaluate_archive.json", json.dumps(doc))
    case("null pattern on correction id 300 -> flagged", p, "nudge", "id=300")

    # 5. null pattern on correction id 100 (legacy) -> allowed
    case("null pattern on correction id 100 (legacy) -> allowed", valid_path, "{}")

    # 6. new letter F added to patterns and used on id 300 -> allowed
    doc = base_doc()
    doc["Self_Evaluate_Themes"]["patterns"]["F"] = "new pattern"
    doc["structured_entries"].append({
        "id": 300, "session_id": "session-300", "outcome": "correction", "pattern": "F"
    })
    p = write_archive(tmpdir, "new_letter_self_evaluate_archive.json", json.dumps(doc))
    case("new letter F used on id 300 -> allowed", p, "{}")

    # 7. free-text pattern on id 300 -> flagged
    doc = base_doc()
    doc["structured_entries"].append({"id": 300, "outcome": "clean", "pattern": "free-text-value"})
    p = write_archive(tmpdir, "freetext_self_evaluate_archive.json", json.dumps(doc))
    case("free-text pattern on id 300 -> flagged", p, "nudge", "id=300")

    # 8. duplicate id -> flagged
    doc = base_doc()
    doc["structured_entries"].append({"id": 1, "outcome": "clean", "pattern": "A"})
    p = write_archive(tmpdir, "dup_id_self_evaluate_archive.json", json.dumps(doc))
    case("duplicate id -> flagged", p, "nudge", "id=1: duplicate id")

    # 9. unparseable -> flagged
    p = write_archive(tmpdir, "unparseable_self_evaluate_archive.json", "{not json")
    case("unparseable -> flagged", p, "nudge", "not valid JSON")
    unreadable = os.path.join(tmpdir, "unreadable", "self_evaluate_archive.json")
    os.makedirs(unreadable)
    case("E14: an input read fault is a nudge, not silent clean", unreadable, "nudge", "cannot read")

    # Malformed population and enum evidence must be visible, never a silent clean result.
    p = write_archive(tmpdir, "root_shape_self_evaluate_archive.json", "[]")
    case("non-object archive root -> nudge", p, "nudge", "root must be an object")
    doc = base_doc()
    doc["Self_Evaluate_Themes"]["patterns"] = []
    p = write_archive(tmpdir, "patterns_shape_self_evaluate_archive.json", json.dumps(doc))
    case("non-object pattern enum -> nudge", p, "nudge", "patterns must be an object")
    doc = base_doc()
    doc["structured_entries"] = {}
    p = write_archive(tmpdir, "population_shape_self_evaluate_archive.json", json.dumps(doc))
    case("non-list structured population -> nudge", p, "nudge", "structured_entries must be a list")
    doc = base_doc()
    doc["structured_entries"].append("not-an-entry")
    p = write_archive(tmpdir, "entry_shape_self_evaluate_archive.json", json.dumps(doc))
    case("non-object structured row -> nudge", p, "nudge", "entry 2 must be an object")
    doc = base_doc()
    doc["structured_entries"].append({
        "id": [], "session_id": [], "outcome": [], "pattern": {},
    })
    p = write_archive(tmpdir, "field_shape_self_evaluate_archive.json", json.dumps(doc))
    case("malformed structured fields -> nudge", p, "nudge", "id must be an integer")

    for present, value, label in (
        (False, None, "absent"),
        (True, None, "null"),
        (True, "", "empty"),
        (True, 0, "zero"),
    ):
        doc = base_doc()
        entry = {"id": 300, "outcome": "clean", "pattern": "A"}
        if present:
            entry["session_id"] = value
        doc["structured_entries"].append(entry)
        p = write_archive(tmpdir, f"{label}_session_self_evaluate_archive.json", json.dumps(doc))
        case(f"E10: new row with {label} session_id -> nudge", p, "nudge", "session_id")

    # facts block: store-derived, so a malformed value is refused and absence is valid anywhere.
    def facts_doc(entry_id, **fields):
        doc = base_doc()
        row = {"id": entry_id, "session_id": "session-%d" % entry_id, "outcome": "clean", "pattern": "A",
               "spend": {"unavailable": "fixture: no rollup here"}}
        row.update(fields)
        doc["structured_entries"].append(row)
        return doc

    good_facts = {"driver": {"model": "claude-fable-5", "effort": "high"},
                  "owner_prompts": 2, "duration_min": 550}
    p = write_archive(tmpdir, "facts_absent_self_evaluate_archive.json", json.dumps(facts_doc(6000)))
    case("facts: absent on a new row -> {}", p, "{}")
    p = write_archive(tmpdir, "facts_ok_self_evaluate_archive.json",
                      json.dumps(facts_doc(6000, facts=good_facts)))
    case("facts: a well-formed store block -> {}", p, "{}")
    p = write_archive(tmpdir, "facts_ok_legacy_self_evaluate_archive.json",
                      json.dumps(facts_doc(200, facts={"driver": {"model": None, "effort": None},
                                                       "owner_prompts": 0, "duration_min": 0})))
    case("facts: a no-effort driver and a zero duration are well formed", p, "{}")
    p = write_archive(tmpdir, "facts_unavail_self_evaluate_archive.json",
                      json.dumps(facts_doc(6000, facts={"unavailable": "no transcript for session"})))
    case("facts: the store's unavailable reason -> {}", p, "{}")
    for label, bad in (("a string", "facts"),
                       ("a list", []),
                       ("owner_prompts as text", {"owner_prompts": "many"}),
                       ("a negative duration", {"duration_min": -1}),
                       ("a driver that is not an object", {"driver": "claude-opus-5"}),
                       ("a model that is not text", {"driver": {"model": 7}}),
                       ("a blank model", {"driver": {"model": "  "}}),
                       ("an unknown driver field", {"driver": {"tier": "high"}}),
                       ("driver_mixed false", {"driver_mixed": False}),
                       ("an unknown fact", {"compactions": 3}),
                       ("an unavailable reason that is not alone", {"unavailable": "x", "owner_prompts": 1}),
                       ("a blank unavailable reason", {"unavailable": "   "})):
        p = write_archive(tmpdir, "facts_bad_self_evaluate_archive.json",
                          json.dumps(facts_doc(6000, facts=bad)))
        case("facts: %s -> nudge" % label, p, "nudge", "facts")

    # S8: a classless `accepted:` disposition warns above ACCEPTED_CLASS_MIN_ID (316 at landing)
    # and is silent below it, so a row published before the class existed is never warned at.
    def accepted_doc(entry_id, disposition):
        doc = base_doc()
        doc["structured_entries"].append({
            "id": entry_id, "session_id": "session-%d" % entry_id, "outcome": "clean", "pattern": "A",
            "spend": {"unavailable": "fixture: no rollup here"},
            "friction": [{"what": "transient lock", "disposition": disposition}],
        })
        return doc

    p = write_archive(tmpdir, "accepted_above_self_evaluate_archive.json",
                      json.dumps(accepted_doc(317, "accepted: transient lock")))
    case("S8: a classless accepted above the cutoff -> WARN", p, "nudge", "WARN")
    p = write_archive(tmpdir, "accepted_below_self_evaluate_archive.json",
                      json.dumps(accepted_doc(316, "accepted: transient lock")))
    case("S8: a classless accepted below the cutoff -> silent", p, "{}")
    p = write_archive(tmpdir, "accepted_classed_self_evaluate_archive.json",
                      json.dumps(accepted_doc(317, "accepted: outside: transient lock")))
    case("S8: an accepted row carrying a class -> silent", p, "{}")

    # Bounded ledger files are parsed and validated against snapshot pattern enums.
    snapshot = write_archive(tmpdir, "self_evaluate_archive.json", json.dumps(base_doc()))
    ledger = write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 300, "session_id": "session-300", "outcome": "correction", "pattern": "A"}) + "\n",
    )
    case("valid ledger -> {}", ledger, "{}")
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 301, "session_id": "session-301", "outcome": "bogus", "pattern": "A"}) + "\n",
    )
    case("bad ledger outcome -> nudge naming the id", ledger, "nudge", "id=301")
    write_archive(tmpdir, "self_evaluate_archive.jsonl", "{not json\n")
    case("unparseable ledger row -> nudge", ledger, "nudge", "ledger row 1")
    write_archive(tmpdir, "self_evaluate_archive.json", "[]")
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 302, "session_id": "session-302", "outcome": "clean", "pattern": "A"}) + "\n",
    )
    case("malformed ledger snapshot -> nudge", ledger, "nudge", "snapshot root must be an object")

    write_archive(tmpdir, "self_evaluate_archive.json", json.dumps(base_doc()))
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 303, "outcome": "clean", "pattern": "A"}) + "\n",
    )
    case("E10: ledger row without session_id -> nudge", ledger, "nudge", "session_id")
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 303, "session_id": ["bad"], "outcome": "clean", "pattern": "A"}) + "\n",
    )
    case("E10: malformed ledger session_id -> nudge without a crash",
         ledger, "nudge", "session_id")

    revisions = "\n".join((
        json.dumps({"id": 304, "session_id": "session-304", "outcome": "correction", "pattern": "A"}),
        json.dumps({"id": 304, "session_id": "session-304", "outcome": "clean", "pattern": "B"}),
    )) + "\n"
    write_archive(tmpdir, "self_evaluate_archive.jsonl", revisions)
    case("E9: same-session ledger revisions use newest-row-wins", ledger, "{}")

    conflict_snapshot = base_doc()
    conflict_snapshot["structured_entries"].extend((
        {"id": 305, "session_id": "snapshot-a", "outcome": "clean", "pattern": "A"},
        {"id": 305, "session_id": "snapshot-b", "outcome": "clean", "pattern": "A"},
    ))
    write_archive(tmpdir, "self_evaluate_archive.json", json.dumps(conflict_snapshot))
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 306, "session_id": "ledger-c", "outcome": "clean", "pattern": "A"}) + "\n",
    )
    case("S17: an unrelated snapshot ID conflict is not this ledger edit's advisory",
         ledger, "{}")

    write_archive(tmpdir, "self_evaluate_archive.json", json.dumps(base_doc()))
    rotation = write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl.1",
        json.dumps({"id": 307, "session_id": "rotation-a", "outcome": "clean", "pattern": "A"}) + "\n",
    )
    write_archive(
        tmpdir,
        "self_evaluate_archive.jsonl",
        json.dumps({"id": 307, "session_id": "active-b", "outcome": "clean", "pattern": "A"}) + "\n",
    )
    case("E9: active ledger validation catches rotation ID ownership conflicts",
         ledger, "nudge", "id=307")
    os.unlink(rotation)

    label = "the guard's ledger rotation count is the store's only rotation count"
    cases_run.append(label)
    store_path = os.path.join(os.path.dirname(HOOK), "..", "tools", "self_eval_archive_store.py")
    with open(store_path, encoding="utf-8") as fh:
        store_source = fh.read()
    probe = checked_run([sys.executable, "-c", (
        "import importlib.util,sys; "
        "s=importlib.util.spec_from_file_location('store',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "import self_eval_archive_guard as g; "
        "print(m.ARCHIVE_MAX_ROTATIONS == g.LEDGER_ROTATIONS)"
    ), store_path], capture_output=True, text=True, timeout=30)
    restated = re.search(r"^ARCHIVE_MAX_ROTATIONS\s*=\s*\d", store_source, re.MULTILINE)
    ok = probe.stdout.strip() == "True" and restated is None
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append("%s\n     store restates the count: %r" % (label, restated and restated.group(0)))

    # spend block: required above SPEND_MIN_ID, fixed keys, row ids that name a rollup row
    def spend_doc(entry_id, **spend_fields):
        doc = base_doc()
        entry = {"id": entry_id, "session_id": "session-%d" % entry_id, "outcome": "clean", "pattern": "A"}
        entry.update(spend_fields)
        doc["structured_entries"].append(entry)
        return doc

    def good_spend():
        return {"rollup": ".claude/scratch/self_evaluate-rollup-x.json",
                "total": {"list": 12.5, "wall_min": 80, "rows": ["T"],
                          "split": {"orchestrator": 30, "dispatches": 20, "proofs": 15, "owner": 15}},
                "waste": [{"rows": ["X", "D29.waste"], "class": "rerun", "list": 3.1, "min": 6,
                           "cause": "round 2 re-ran every seat", "preventer": "none known"}],
                "costliest": {"rows": ["D17"], "list": 4.2, "bought": "the engine rewrite"},
                "change": {"text": "none", "rows": ["O"]}}

    p = write_archive(tmpdir, "legacy_nospend_self_evaluate_archive.json", json.dumps(spend_doc(300)))
    case("spend: a legacy id without spend passes", p, "{}")
    p = write_archive(tmpdir, "new_nospend_self_evaluate_archive.json", json.dumps(spend_doc(5000)))
    case("spend: a new id without spend -> nudge naming spend", p, "nudge", "spend")
    p = write_archive(tmpdir, "unavail_self_evaluate_archive.json",
                      json.dumps(spend_doc(5000, spend={"unavailable": "rollup could not run"})))
    case("spend: unavailable with a reason passes", p, "{}")
    p = write_archive(tmpdir, "emptyunavail_self_evaluate_archive.json",
                      json.dumps(spend_doc(5000, spend={"unavailable": ""})))
    case("spend: unavailable with no reason -> nudge", p, "nudge", "unavailable")
    p = write_archive(tmpdir, "goodspend_self_evaluate_archive.json",
                      json.dumps(spend_doc(5000, spend=good_spend())))
    case("spend: a complete block passes", p, "{}")
    broken = good_spend()
    broken["waste"][0]["rows"] = ["Z9"]
    p = write_archive(tmpdir, "badrow_self_evaluate_archive.json", json.dumps(spend_doc(5000, spend=broken)))
    case("spend: a malformed row id -> nudge naming it", p, "nudge", "Z9")
    broken = good_spend()
    broken["costliest"]["rows"] = []
    p = write_archive(tmpdir, "emptyrows_self_evaluate_archive.json", json.dumps(spend_doc(5000, spend=broken)))
    case("spend: an empty rows list -> nudge", p, "nudge", "costliest")
    broken = good_spend()
    del broken["change"]
    p = write_archive(tmpdir, "nochange_self_evaluate_archive.json", json.dumps(spend_doc(5000, spend=broken)))
    case("spend: a missing key -> nudge naming it", p, "nudge", "change")
    write_archive(tmpdir, "self_evaluate_archive.json", json.dumps(base_doc()))
    ledger = write_archive(
        tmpdir, "self_evaluate_archive.jsonl",
        json.dumps({"id": 5001, "session_id": "session-5001", "outcome": "clean", "pattern": "A"}) + "\n")
    case("spend: a new ledger row without spend -> nudge", ledger, "nudge", "spend")

    # 13. the live archive is clean under the current guard
    if os.path.exists(LIVE_ARCHIVE):
        case("live archive -> {}", LIVE_ARCHIVE, "{}")
    else:
        print("skip live archive check (not found at %s)" % LIVE_ARCHIVE)

    print("\n%d/%d cases pass" % (len(cases_run) - len(failures), len(cases_run)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


def test_ledger_reports_only_changed_rows_and_relevant_enum_help(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('archive_probe', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = tmp_path / 'self_evaluate_archive.json'
    snapshot.write_text(json.dumps(base_doc()), encoding='utf-8')
    ledger = tmp_path / 'self_evaluate_archive.jsonl'
    old = json.dumps({'id': 300, 'session_id': 'old', 'outcome': 'bogus', 'pattern': 'A'}) + '\n'
    valid = json.dumps({'id': 301, 'session_id': 'new', 'outcome': 'clean', 'pattern': 'A'}) + '\n'
    text = old.replace(': ', ':') + valid
    ledger.write_text(text, encoding='utf-8')
    event = {'tool_name': 'Write', 'tool_input': {'file_path': str(ledger), 'content': text},
             'tool_response': {'originalFile': old}}
    assert module.process(event) is None
    assert any('id=300' in v for v in module.check_ledger(text, str(snapshot), str(ledger)))
    missing_spend = json.dumps({'id': 500, 'session_id': 'spend', 'outcome': 'clean', 'pattern': 'A'}) + '\n'
    event['tool_response']['originalFile'] = text
    text += missing_spend
    ledger.write_text(text, encoding='utf-8')
    event['tool_input']['content'] = text
    result = module.process(event)
    assert result and 'id=500' in result['context'] and 'id=300' not in result['context']
    assert 'enum homes:' not in result['context']
    event['tool_response']['originalFile'] = text
    text += json.dumps({'id': 302, 'session_id': 'enum', 'outcome': 'bogus', 'pattern': 'A'}) + '\n'
    ledger.write_text(text, encoding='utf-8')
    event['tool_input']['content'] = text
    result = module.process(event)
    assert result and 'id=302' in result['context'] and 'enum homes:' in result['context']
    assert 'id=500' not in result['context'] and 'id=300' not in result['context']


MIRROR = (
    "# Worklog Titles\n\n"
    "- fix · 2 · Real open item\n"
    "- refactor · 1 · Tagged open item [after: a peer commits] [quick-win]\n"
)


def _guard():
    import importlib.util
    spec = importlib.util.spec_from_file_location('archive_guard_fix', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path, mirror=None):
    """A temp git repo with one commit; `mirror` writes .claude/worklog-titles.md when given."""
    root = tmp_path / "repo"
    root.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q")
    git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "fixture")
    sha = git("rev-parse", "HEAD")
    if mirror is not None:
        (root / ".claude").mkdir()
        (root / ".claude" / "worklog-titles.md").write_text(mirror, encoding="utf-8")
    return str(root), sha


def _row(eid, outcome, pattern, **fields):
    row = {"id": eid, "session_id": "session-%d" % eid, "outcome": outcome, "pattern": pattern,
           "spend": {"unavailable": "fixture"}}
    row.update(fields)
    return row


def _archive(*rows):
    doc = base_doc()
    doc["Self_Evaluate_Themes"]["patterns"]["E"] = "cause E"
    doc["structured_entries"].extend(rows)
    return json.dumps(doc)


def _fix_violations(violations, eid):
    return [v for v in violations if v.startswith("id=%d:" % eid) and "records no fix" in v]


def test_cause_e_without_a_resolving_sha_or_mirrored_title_is_refused_above_the_cutoff(tmp_path):
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)
    assert _fix_violations(guard.check(_archive(_row(315, "failure", "E")), root=root), 315)
    assert not _fix_violations(guard.check(_archive(_row(314, "failure", "E")), root=root), 314)


def test_invented_sha_and_invented_title_are_refused(tmp_path):
    guard = _guard()
    root, sha = _repo(tmp_path, MIRROR)
    rows = (
        _row(320, "failure", "A", notes="fixed in deadbeef0042"),
        _row(321, "failure", "A", friction=[{"what": "x", "disposition": "worklog: Not on the mirror"}]),
        _row(322, "failure", "A", session_id=sha[:12] + "-session", notes="fixed in " + sha[:12]),
        _row(323, "failure", "A", notes="fixed in " + sha),
    )
    violations = guard.check(_archive(*rows), root=root)
    assert _fix_violations(violations, 320)
    assert _fix_violations(violations, 321)
    assert _fix_violations(violations, 322), "a token that prefixes the row's own session_id must be ignored"
    assert not _fix_violations(violations, 323)


def test_accepted_reason_no_longer_excuses_a_failed_row(tmp_path):
    guard = _guard()
    root, sha = _repo(tmp_path, MIRROR)
    outside = [{"what": "vendor CLI blocked the call", "disposition": "accepted: outside service"}]
    worklog = outside + [{"what": "x", "disposition": "worklog: TAGGED open item"}]
    violations = guard.check(_archive(
        _row(330, "failure", "A", friction=outside),
        _row(331, "failure", "E", friction=outside),
        _row(332, "failure", "A", friction=outside, notes="fix " + sha),
        _row(333, "failure", "A", friction=worklog),
        _row(334, "correction", "A", friction=outside),
    ), root=root)
    assert _fix_violations(violations, 330)
    assert _fix_violations(violations, 331)
    assert not _fix_violations(violations, 332)
    assert not _fix_violations(violations, 333)
    assert not [v for v in violations if v.startswith("id=334:")]
    assert not [v for v in violations if v.startswith("id=330:") and "accepted" in v]


def test_resolving_sha_and_mirrored_title_each_satisfy_it(tmp_path):
    guard = _guard()
    root, sha = _repo(tmp_path, MIRROR)
    violations = guard.check(_archive(
        _row(340, "failure", "E", notes="fix " + sha),
        _row(341, "failure", "A", friction=[{"what": "x", "disposition": "worklog:   TAGGED  open item "}]),
    ), root=root)
    assert not _fix_violations(violations, 340)
    assert not _fix_violations(violations, 341)


def test_legacy_row_is_exempt(tmp_path):
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)
    violations = guard.check(_archive(_row(266, "failure", "E"), _row(314, "failure", "E")), root=root)
    assert not _fix_violations(violations, 266)
    assert not _fix_violations(violations, 314)


def test_clean_and_correction_rows_are_untouched(tmp_path):
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)
    violations = guard.check(_archive(_row(350, "clean", None), _row(351, "correction", "A")), root=root)
    assert not _fix_violations(violations, 350)
    assert not _fix_violations(violations, 351)


def _cause_field_violations(violations):
    return [v for v in violations if "cause_counts" in v or "rule_misses" in v]


def test_cause_counts_and_rule_misses_shapes_are_refused_but_a_clean_row_is_allowed(tmp_path):
    """S4: a malformed value is wrong at every id, so the shape checks refuse; a clean row passes."""
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)

    # length mismatch: the counts must sum to len(corrections)
    violations = guard.check(_archive(_row(400, "correction", "A", corrections=["x", "y"],
                                          cause_counts={"A": 1})), root=root)
    assert [v for v in violations if "cause_counts" in v and "sums to" in v]

    # bad letter: not a member of Self_Evaluate_Themes.patterns
    violations = guard.check(_archive(_row(400, "correction", "A", corrections=["x"],
                                          cause_counts={"Z": 1})), root=root)
    assert [v for v in violations if "cause_counts" in v and "pattern letter" in v]

    # not an object
    violations = guard.check(_archive(_row(400, "correction", "A", corrections=["x"],
                                          cause_counts=["A"])), root=root)
    assert [v for v in violations if "cause_counts must be an object" in v]

    # bad kind, a missing field and a vacuous evidence string are each refused
    good_miss = {"rule": ".claude/CLAUDE.md", "kind": "not-followed", "evidence": "U1"}
    for label, miss in (("bad kind", dict(good_miss, kind="ignored")),
                        ("missing evidence", {"rule": ".claude/CLAUDE.md", "kind": "not-read"}),
                        ("blank evidence", dict(good_miss, evidence="  ")),
                        ("an unknown field", dict(good_miss, cause="A"))):
        violations = guard.check(_archive(_row(400, "correction", "A", rule_misses=[miss])), root=root)
        assert [v for v in violations if "rule_misses" in v], label

    # must-allow: a clean row with no corrections and neither new field
    assert not _cause_field_violations(guard.check(_archive(_row(400, "clean", None)), root=root))

    # must-allow: a well-formed pair, and a row with neither new field
    ok = guard.check(_archive(_row(
        400, "correction", "A", corrections=["x"], cause_counts={"A": 1}, rule_misses=[good_miss])), root=root)
    assert not _cause_field_violations(ok)
    assert not _cause_field_violations(guard.check(
        _archive(_row(400, "correction", "A", corrections=["x"])), root=root))


def test_cause_a_without_a_rule_miss_is_a_warn_never_a_refusal(tmp_path):
    """S4: the coupling binds above RULE_FIELDS_MIN_ID as a WARN; a malformed value is still refused."""
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)

    row = _row(400, "correction", "A", corrections=["x"], cause_counts={"A": 1})
    assert not _cause_field_violations(guard.check(_archive(row), root=root)), "the coupling is not a refusal"
    assert any("WARN" in w for w in guard.entry_warnings(row, root=root))

    # must-allow: causes B/D/E only
    for letter in ("B", "D", "E"):
        clean = _row(400, "correction", letter, corrections=["x"], cause_counts={letter: 1})
        assert guard.entry_warnings(clean, root=root) == [], letter

    # must-allow: a not-followed/not-read rule_miss satisfies the coupling
    satisfied = _row(400, "correction", "A", corrections=["x"], cause_counts={"A": 1},
                     rule_misses=[{"rule": ".claude/worklog-titles.md", "kind": "not-read",
                                   "evidence": "F2"}])
    assert guard.entry_warnings(satisfied, root=root) == []

    # must-allow: a row below the cutoff is never warned at
    below = _row(guard.RULE_FIELDS_MIN_ID, "correction", "A", corrections=["x"], cause_counts={"A": 1})
    assert guard.entry_warnings(below, root=root) == []


def test_a_rule_miss_path_that_is_not_under_the_repo_root_warns(tmp_path):
    """S4: `rule` must name a file that exists; the check is a WARN (a store concern, surfaced here)."""
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)
    missing = _row(400, "correction", "B", rule_misses=[
        {"rule": "no/such/rule.md", "kind": "not-read", "evidence": "no/such/rule.md was not opened"}])
    present = _row(401, "correction", "B", rule_misses=[
        {"rule": ".claude/worklog-titles.md", "kind": "not-read", "evidence": "U1"}])
    warnings = guard.entry_warnings(missing, root=root)
    assert any("WARN" in w and "no/such/rule.md" in w for w in warnings)
    assert guard.entry_warnings(present, root=root) == []


def test_classless_accepted_warns_above_its_cutoff_and_is_silent_below(tmp_path):
    """S8: the class is required only above ACCEPTED_CLASS_MIN_ID, and only as a WARN."""
    guard = _guard()
    root, _sha = _repo(tmp_path, MIRROR)
    text = "accepted: transient lock"
    above = _row(guard.ACCEPTED_CLASS_MIN_ID + 1, "clean", None,
                 friction=[{"what": "x", "disposition": text}])
    below = _row(guard.ACCEPTED_CLASS_MIN_ID, "clean", None,
                 friction=[{"what": "x", "disposition": text}])
    assert any("WARN" in w and "accepted" in w for w in guard.entry_warnings(above, root=root))
    assert guard.entry_warnings(below, root=root) == []
    for disposition in ("accepted: outside: transient lock",
                        "accepted: owner-held: the owner owns this call",
                        "accepted: guard-working: the gate fired as designed",
                        "accepted: one-off: a one-time client bug",
                        "fixed"):
        classed = _row(guard.ACCEPTED_CLASS_MIN_ID + 1, "clean", None,
                       friction=[{"what": "x", "disposition": disposition}])
        assert guard.entry_warnings(classed, root=root) == [], disposition


if __name__ == "__main__":
    legacy_rc = main()
    import pytest
    pytest_rc = pytest.main([__file__, '-q', '-s'])
    sys.exit(legacy_rc or pytest_rc)
