"""Re-runnable proof for hooks/self_eval_archive_guard.py.

instruction_quality §14: registration proves wiring, not matching. Each case feeds the
hook a real PostToolUse payload naming a real file on disk and asserts on the channel it
emits, so a regression shows up as a failing case rather than as a surprise silence.

    python3 .claude/tests/test_self_eval_archive_guard.py
"""
import json
import os
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


def run_on_path(file_path, tool_name="Write"):
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path}}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
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
    tmpdir = tempfile.mkdtemp(prefix="archguard_")

    def case(label, path, expected_channel, needle=""):
        got_channel, reason = run_on_path(path)
        ok = got_channel == expected_channel and (not needle or needle in reason)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append("%s\n     expected=%s got=%s reason=%r"
                             % (label, expected_channel, got_channel, reason[:300]))

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
    doc["structured_entries"].append({"id": 300, "outcome": "correction", "pattern": "F"})
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

    # 10. the live archive is clean under the current guard
    if os.path.exists(LIVE_ARCHIVE):
        case("live archive -> {}", LIVE_ARCHIVE, "{}")
    else:
        print("skip live archive check (not found at %s)" % LIVE_ARCHIVE)

    print("\n%d cases pass" % (10 - len(failures) if os.path.exists(LIVE_ARCHIVE) else 9 - len(failures)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
