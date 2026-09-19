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
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
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
    case("E9: ledger validation catches snapshot ID ownership conflicts",
         ledger, "nudge", "id=305")

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

    # 13. the live archive is clean under the current guard
    if os.path.exists(LIVE_ARCHIVE):
        case("live archive -> {}", LIVE_ARCHIVE, "{}")
    else:
        print("skip live archive check (not found at %s)" % LIVE_ARCHIVE)

    print("\n%d/%d cases pass" % (len(cases_run) - len(failures), len(cases_run)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
