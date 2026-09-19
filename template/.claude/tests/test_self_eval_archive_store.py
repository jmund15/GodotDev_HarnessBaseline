"""Re-runnable proof for tools/self_eval_archive_store.py.

    python3 .claude/tests/test_self_eval_archive_store.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(HERE, "..", "tools", "self_eval_archive_store.py")


def entry(session_id, title, outcome="clean", pattern=None):
    return {
        "session_id": session_id,
        "title": title,
        "date": "2026-09-11",
        "outcome": outcome,
        "pattern": pattern,
        "domains": ["meta"],
        "corrections": [],
    }


def independent_ledger_paths(archive):
    """Enumerate all ledger-shaped siblings without using the store's rotation list."""
    active = os.path.basename(archive[:-5] + ".jsonl" if archive.endswith(".json")
                              else archive + ".jsonl")
    parent = os.path.dirname(os.path.abspath(archive))
    return sorted(os.path.join(parent, name) for name in os.listdir(parent)
                  if name == active or name.startswith(active + "."))


def main():
    spec = importlib.util.spec_from_file_location("self_eval_archive_store", STORE_PATH)
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)

    root = tempfile.mkdtemp(prefix="selfevalstore_")
    archive = os.path.join(root, "self_evaluate_archive.json")
    legacy = {
        "description": "fixture",
        "Self_Evaluate_Themes": {"patterns": {"A": "fixture"}},
        "legacy_entries": ["legacy clean"],
        "structured_entries": [dict(entry("old-session", "old"), id=7)],
    }
    with open(archive, "w", encoding="utf-8") as handle:
        json.dump(legacy, handle)

    cases = []
    cases.append(("legacy structured entries remain visible",
                  [row["session_id"] for row in store.effective_entries(archive)] == ["old-session"]))

    first = store.upsert_entry(archive, entry("new-session", "new"))
    cases.append(("a new session receives the next id", first["id"] == 8))
    replaced = store.upsert_entry(archive, entry("new-session", "revised", "correction", "A"))
    visible = {row["session_id"]: row for row in store.effective_entries(archive)}
    cases.append(("upsert replaces the effective session row and preserves its id",
                  replaced["id"] == 8 and visible["new-session"]["title"] == "revised"
                  and len(visible) == 2))

    identical_before = open(store.ledger_path(archive), "rb").read()
    identical = store.upsert_entry(archive, dict(replaced))
    identical_after = open(store.ledger_path(archive), "rb").read()
    cases.append(("an identical upsert is a storage no-op",
                  identical == replaced and identical_after == identical_before))

    missing_session = entry("", "missing identity")
    before_missing_session = open(store.ledger_path(archive), "rb").read()
    try:
        store.upsert_entry(archive, missing_session)
        missing_session_rejected = False
    except ValueError:
        missing_session_rejected = True
    cases.append(("new ledger rows require exact session identity",
                  missing_session_rejected
                  and open(store.ledger_path(archive), "rb").read() == before_missing_session))

    malformed_archive = os.path.join(root, "malformed-archive.json")
    with open(malformed_archive, "w", encoding="utf-8") as handle:
        json.dump({"Self_Evaluate_Themes": {}, "structured_entries": {}}, handle)
    try:
        store.effective_entries(malformed_archive)
        malformed_rejected = False
    except ValueError:
        malformed_rejected = True
    cases.append(("malformed snapshot population is unknown rather than empty",
                  malformed_rejected))

    non_object_archive = os.path.join(root, "non-object-entry.json")
    with open(non_object_archive, "w", encoding="utf-8") as handle:
        json.dump({"Self_Evaluate_Themes": {}, "structured_entries": ["not-an-entry"]}, handle)
    try:
        store.effective_entries(non_object_archive)
        non_object_rejected = False
    except ValueError:
        non_object_rejected = True
    cases.append(("malformed structured rows are not silently dropped",
                  non_object_rejected))

    malformed_selected_archive = os.path.join(root, "malformed-selected.json")
    with open(malformed_selected_archive, "w", encoding="utf-8") as handle:
        json.dump({
            "Self_Evaluate_Themes": {"patterns": {"A": "fixture"}},
            "legacy_entries": [],
            "structured_entries": [{"id": 900, "session_id": "malformed-session"}],
        }, handle)
    try:
        store.find_session(malformed_selected_archive, "malformed-session")
        malformed_selected_rejected = False
    except ValueError:
        malformed_selected_rejected = True
    malformed_lookup = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", malformed_selected_archive,
         "--lookup", "malformed-session"],
        capture_output=True, text=True, timeout=60,
    )
    cases.append(("lookup never accepts a malformed selected row",
                  malformed_selected_rejected and malformed_lookup.returncode == 2
                  and "REFUSED" in malformed_lookup.stdout))
    empty_lookup = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", archive, "--lookup", ""],
        capture_output=True, text=True, timeout=60,
    )
    cases.append(("lookup requires an exact non-empty session identity",
                  empty_lookup.returncode == 2 and "REFUSED" in empty_lookup.stdout))

    malformed_themes_archive = os.path.join(root, "malformed-themes.json")
    with open(malformed_themes_archive, "w", encoding="utf-8") as handle:
        json.dump({
            "Self_Evaluate_Themes": {"patterns": []},
            "legacy_entries": [],
            "structured_entries": [dict(entry("bad-themes", "bad themes"), id=901)],
        }, handle)
    malformed_themes_lookup = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", malformed_themes_archive,
         "--lookup", "bad-themes"],
        capture_output=True, text=True, timeout=60,
    )
    cases.append(("lookup reports malformed enum evidence without a traceback",
                  malformed_themes_lookup.returncode == 2
                  and "REFUSED" in malformed_themes_lookup.stdout
                  and not malformed_themes_lookup.stderr.strip()))

    collision = dict(entry("id-collision", "collision"), id=7)
    before_collision = open(store.ledger_path(archive), "rb").read()
    try:
        store.upsert_entry(archive, collision)
        collision_rejected = False
    except ValueError:
        collision_rejected = True
    after_collision = open(store.ledger_path(archive), "rb").read()
    cases.append(("a new session cannot claim an existing id",
                  collision_rejected and before_collision == after_collision))

    original_cap = store.ARCHIVE_MAX_BYTES
    original_rotations = store.ARCHIVE_MAX_ROTATIONS
    store.ARCHIVE_MAX_BYTES = 700
    store.ARCHIVE_MAX_ROTATIONS = 2
    try:
        for index in range(8):
            store.upsert_entry(archive, entry(f"rotate-{index}", "x" * 90))
        paths = independent_ledger_paths(archive)
        cases.append(("ledger rotation stays within the file-count and byte caps",
                      len(paths) <= original_rotations + 1
                      and all(os.path.getsize(path) <= 700 for path in paths)))
        before_paths = independent_ledger_paths(archive)
        before = {path: open(path, "rb").read() for path in before_paths}
        try:
            store.upsert_entry(archive, entry("oversized", "z" * 1000))
            rejected = False
        except ValueError:
            rejected = True
        after_paths = independent_ledger_paths(archive)
        after = {path: open(path, "rb").read() for path in after_paths}
        cases.append(("one impossible row is rejected before any ledger write",
                      rejected and before_paths == after_paths and before == after))
    finally:
        store.ARCHIVE_MAX_BYTES = original_cap
        store.ARCHIVE_MAX_ROTATIONS = original_rotations

    compact_archive = os.path.join(root, "compact_archive.json")
    with open(compact_archive, "w", encoding="utf-8") as handle:
        json.dump(legacy, handle)
    store.ARCHIVE_MAX_BYTES = 300
    store.ARCHIVE_MAX_ROTATIONS = 2
    try:
        anchor = store.upsert_entry(compact_archive, entry("anchor-session", "anchor"))
        for index in range(18):
            store.upsert_entry(compact_archive, entry("churn-session", "revision-%02d" % index))
        compact_visible = {row["session_id"]: row for row in store.effective_entries(compact_archive)}
        cases.append(("bounded compaction preserves the full effective population",
                      set(compact_visible) == {"old-session", "anchor-session", "churn-session"}
                      and compact_visible["anchor-session"]["id"] == anchor["id"]
                      and compact_visible["churn-session"]["title"] == "revision-17"))
        reader_script = (
            "import importlib.util,sys; "
            "s=importlib.util.spec_from_file_location('store',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "print(len(m.effective_entries(sys.argv[2])))"
        )
        reader_lock = store.ledger_path(compact_archive) + ".lock"
        with store._lock(reader_lock):
            reader = subprocess.Popen(
                [sys.executable, "-c", reader_script, STORE_PATH, compact_archive],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            time.sleep(0.2)
            reader_blocked = reader.poll() is None
        reader_stdout, reader_stderr = reader.communicate(timeout=10)
        cases.append(("ledger readers wait for a compaction generation lock",
                      reader_blocked and reader.returncode == 0
                      and reader_stdout.strip() == "3" and not reader_stderr.strip()))
    finally:
        store.ARCHIVE_MAX_BYTES = original_cap
        store.ARCHIVE_MAX_ROTATIONS = original_rotations

    bad_path = os.path.join(root, "bad.json")
    with open(bad_path, "w", encoding="utf-8") as handle:
        json.dump(entry("bad", "bad", outcome="success"), handle)
    run = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", archive, "--upsert", bad_path],
        capture_output=True, text=True, timeout=60,
    )
    cases.append(("CLI rejects malformed entries without a traceback",
                  run.returncode == 2 and "REFUSED" in run.stdout and not run.stderr.strip()))

    missing_lookup = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", archive, "--lookup", "absent-session"],
        capture_output=True, text=True, timeout=60,
    )
    cases.append(("CLI keeps an absent selected row distinct from malformed evidence",
                  missing_lookup.returncode == 1
                  and not missing_lookup.stdout.strip() and not missing_lookup.stderr.strip()))

    good_path = os.path.join(root, "good.json")
    with open(good_path, "w", encoding="utf-8") as handle:
        json.dump(entry("cli-session", "cli"), handle)
    write_run = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", archive, "--upsert", good_path],
        capture_output=True, text=True, timeout=60,
    )
    lookup = subprocess.run(
        [sys.executable, STORE_PATH, "--archive", archive, "--lookup", "cli-session"],
        capture_output=True, text=True, timeout=60,
    )
    looked_up = json.loads(lookup.stdout) if lookup.returncode == 0 else {}
    cases.append(("CLI upsert and lookup expose one effective session row",
                  write_run.returncode == 0 and lookup.returncode == 0
                  and looked_up.get("session_id") == "cli-session"))

    concurrent_archive = os.path.join(root, "concurrent_archive.json")
    with open(concurrent_archive, "w", encoding="utf-8") as handle:
        json.dump(legacy, handle)
    processes = []
    for index in range(8):
        candidate = os.path.join(root, "concurrent-%d.json" % index)
        with open(candidate, "w", encoding="utf-8") as handle:
            json.dump(entry("concurrent-%d" % index, "concurrent"), handle)
        processes.append(subprocess.Popen(
            [sys.executable, STORE_PATH, "--archive", concurrent_archive, "--upsert", candidate],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
    results = [process.communicate(timeout=60) + (process.returncode,) for process in processes]
    concurrent = [row for row in store.effective_entries(concurrent_archive)
                  if row.get("session_id", "").startswith("concurrent-")]
    cases.append(("cross-process writers publish every row without duplicate ids",
                  all(result[2] == 0 for result in results) and len(concurrent) == 8
                  and len({row.get("id") for row in concurrent}) == 8))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
