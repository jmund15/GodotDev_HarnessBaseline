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


# Rows 16, 261, 263 of the live transcript 0db74049-9379-4d61-94b6-18b6bef3ba46.jsonl (trimmed,
# bytes unchanged): one fable-5/high turn, the plumbed `/model` command, and the owner's
# "continue". Counted by hand: one substantive owner row, one driver, 550 whole minutes.
FACTS_ROWS = [
    r"""{"parentUuid":"4a909478-c780-41dd-a505-2719f27bdeac","isSidechain":false,"message":{"model":"claude-fable-5","id":"msg_011CdLvG4KmTAnmjATe6XkEc","type":"message","role":"assistant","content":[{"type":"text","text":"I'll start by loading the refactor_procedure skill and searching memory for refactoring/submodule gotchas."}],"stop_reason":"tool_use","stop_sequence":null,"stop_details":null,"usage":{"input_tokens":2,"cache_creation_input_tokens":45495,"cache_read_input_tokens":28034,"output_tokens":531,"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":45495,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":2,"output_tokens":531,"cache_read_input_tokens":28034,"cache_creation_input_tokens":45495,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":45495},"type":"message"}],"speed":"standard"},"diagnostics":null},"requestId":"req_011CdLvG2PgoMfUftcMQhMDU","type":"assistant","uuid":"9a673795-bfbb-43ec-9cfe-1c157feaab00","timestamp":"2026-07-24T12:56:26.916Z","effort":"high","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\Users\\dev\\work\\project","sessionId":"0db74049-9379-4d61-94b6-18b6bef3ba46","version":"2.1.217","gitBranch":"main"}""",
    r"""{"parentUuid":"bc754e63-8da4-4103-b5eb-624d4e0f9f2f","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"<command-name>/model</command-name>\n            <command-message>model</command-message>\n            <command-args>claude-opus-5</command-args>"},"uuid":"91b4192a-070f-4e6d-b27b-6351811bdb2f","timestamp":"2026-07-24T22:06:47.255Z","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\Users\\dev\\work\\project","sessionId":"0db74049-9379-4d61-94b6-18b6bef3ba46","version":"2.1.219","gitBranch":"main"}""",
    r"""{"parentUuid":"14cfc194-78b3-4611-abbd-c706e0f6f966","isSidechain":false,"promptId":"b2851b5a-fc43-4230-8bb5-81b3358a9e91","type":"user","message":{"role":"user","content":"continue"},"uuid":"9c0f90a3-e1df-47dd-8f42-e5ebed841f03","timestamp":"2026-07-24T22:06:51.736Z","permissionMode":"auto","origin":{"kind":"human"},"promptSource":"sdk","userType":"external","entrypoint":"claude-desktop","cwd":"C:\\Users\\dev\\work\\project","sessionId":"0db74049-9379-4d61-94b6-18b6bef3ba46","version":"2.1.219","gitBranch":"main"}""",
]

DERIVED_FACTS = {"driver": {"model": "claude-fable-5", "effort": "high"},
                 "owner_prompts": 1, "duration_min": 550}


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
        "Self_Evaluate_Themes": {"patterns": {"A": "fixture", "B": "fixture", "D": "fixture",
                                              "E": "fixture"}},
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
    # 900, not 700: a row now carries its store-derived `facts` (~280 B for these fixtures), and
    # the case asserts the cap holds, not a particular row size.
    store.ARCHIVE_MAX_BYTES = 900
    store.ARCHIVE_MAX_ROTATIONS = 2
    try:
        for index in range(8):
            store.upsert_entry(archive, entry(f"rotate-{index}", "x" * 90))
        paths = independent_ledger_paths(archive)
        cases.append(("ledger rotation stays within the file-count and byte caps",
                      len(paths) <= original_rotations + 1
                      and all(os.path.getsize(path) <= 900 for path in paths)))
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

    # `facts` is store-derived: the entry's own value is discarded, a missing transcript becomes a
    # stated reason and never a silent zero, and --refresh-facts re-derives through the store's own
    # cwd lookup. The derived numbers are counted from FACTS_ROWS by hand, not read back from it.
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import session_digest
    facts_config = os.path.join(root, "facts-config")
    facts_repo = os.path.join(root, "facts-repo")
    os.makedirs(facts_repo, exist_ok=True)
    previous_config = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = facts_config
    try:
        projects = session_digest.projects_dir(facts_repo)
        os.makedirs(projects, exist_ok=True)
        with open(os.path.join(projects, "facts-session.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(FACTS_ROWS) + "\n")
        facts_archive = os.path.join(root, "facts_archive.json")
        with open(facts_archive, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        planted = dict(entry("facts-session", "filled"),
                       facts={"driver": {"model": "planted-lie", "effort": "max"},
                              "owner_prompts": 999, "duration_min": 1})
        try:
            filled = store.upsert_entry(facts_archive, planted, projects_root=projects)["facts"]
        except TypeError as exc:
            filled = "upsert cannot derive facts: %s" % exc
        cases.append(("upsert fills facts from the transcript and discards the entry's own",
                      filled == DERIVED_FACTS))
        try:
            absent = store.upsert_entry(facts_archive, entry("no-transcript", "absent"),
                                        projects_root=projects)["facts"]
        except TypeError as exc:
            absent = "upsert cannot derive facts: %s" % exc
        cases.append(("a session with no transcript publishes a stated unavailable reason",
                      isinstance(absent, dict) and set(absent) == {"unavailable"}
                      and isinstance(absent["unavailable"], str) and absent["unavailable"].strip()))
        env = dict(os.environ, CLAUDE_CONFIG_DIR=facts_config)
        refreshed = subprocess.run(
            [sys.executable, STORE_PATH, "--archive", facts_archive,
             "--refresh-facts", "facts-session"],
            cwd=facts_repo, env=env, capture_output=True, text=True, timeout=120)
        cases.append(("CLI --refresh-facts re-derives an existing row in place",
                      refreshed.returncode == 0
                      and json.loads(refreshed.stdout or "{}").get("facts") == DERIVED_FACTS))
        missing = subprocess.run(
            [sys.executable, STORE_PATH, "--archive", facts_archive,
             "--refresh-facts", "absent-session"],
            cwd=facts_repo, env=env, capture_output=True, text=True, timeout=120)
        cases.append(("CLI --refresh-facts keeps an absent row distinct from a refusal",
                      missing.returncode == 1 and not missing.stdout.strip()
                      and not missing.stderr.strip()))
    finally:
        if previous_config is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = previous_config

    # S4: a cause A without a not-followed/not-read rule_miss is a store WARN, never a refusal;
    # causes B/D/E only and a rule path that exists are silent; a rule path that is not under the
    # repo root is a WARN. The anchor row holds id 316 (= the landing RULE_FIELDS_MIN_ID), so each
    # new row lands above the cutoff where the coupling and the path check bind.
    warn_archive = os.path.join(root, "warn_archive.json")
    with open(warn_archive, "w", encoding="utf-8") as handle:
        json.dump(legacy, handle)
    with open(store.ledger_path(warn_archive), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": 316, "session_id": "anchor-316", "outcome": "clean",
                                 "pattern": "A", "spend": {"unavailable": "fixture"}}) + "\n")

    def upsert_cli(candidate, name):
        path = os.path.join(root, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle)
        return subprocess.run(
            [sys.executable, STORE_PATH, "--archive", warn_archive, "--upsert", path],
            capture_output=True, text=True, timeout=120)

    cause_a = upsert_cli(dict(entry("warn-a", "cause a", "correction", "A"),
                              corrections=["missed a rule"], cause_counts={"A": 1},
                              spend={"unavailable": "fixture"}), "warn-a.json")
    cases.append(("a cause A without a rule_miss is a store WARN, never a refusal",
                  cause_a.returncode == 0 and "WARN" in cause_a.stderr
                  and json.loads(cause_a.stdout or "{}").get("id") == 317))
    cause_b = upsert_cli(dict(entry("warn-b", "cause b", "correction", "B"),
                              corrections=["missed a rule"], cause_counts={"B": 1},
                              spend={"unavailable": "fixture"}), "warn-b.json")
    cases.append(("causes B/D/E only are never warned at",
                  cause_b.returncode == 0 and "WARN" not in cause_b.stderr))
    missing_rule = upsert_cli(dict(entry("warn-c", "missing rule", "correction", "B"),
                                   spend={"unavailable": "fixture"},
                                   rule_misses=[{"rule": "no/such/rule.md", "kind": "not-read",
                                                 "evidence": "no/such/rule.md was never opened"}]),
                              "warn-c.json")
    cases.append(("a rule_miss path that is not under the repo root is a store WARN",
                  missing_rule.returncode == 0 and "WARN" in missing_rule.stderr))
    present_rule = upsert_cli(dict(entry("warn-d", "present rule", "correction", "B"),
                                   spend={"unavailable": "fixture"},
                                   rule_misses=[{"rule": ".claude/CLAUDE.md", "kind": "not-followed",
                                                 "evidence": "U1"}]), "warn-d.json")
    cases.append(("a rule_miss path that exists is silent",
                  present_rule.returncode == 0 and "WARN" not in present_rule.stderr
                  and json.loads(present_rule.stdout or "{}").get("id") == 320))

    # S7: a consumer with no snapshot file reads an empty document and the default enum; a
    # directory at the snapshot path stays refused.
    bare = os.path.join(root, "bare", ".claude")
    os.makedirs(bare)
    bare_archive = os.path.join(bare, "self_evaluate_archive.json")
    lookup = subprocess.run([sys.executable, STORE_PATH, "--archive", bare_archive,
                             "--lookup", "nobody"], capture_output=True, text=True, timeout=120)
    cases.append(("lookup with no snapshot exits 1", lookup.returncode == 1))
    dir_archive = os.path.join(root, "dir-archive.json")
    os.makedirs(dir_archive)
    dir_lookup = subprocess.run([sys.executable, STORE_PATH, "--archive", dir_archive,
                                 "--lookup", "nobody"], capture_output=True, text=True, timeout=120)
    cases.append(("a directory at the snapshot path stays refused",
                  dir_lookup.returncode == 2 and "REFUSED" in dir_lookup.stdout))
    spec_a = importlib.util.spec_from_file_location(
        "analyze_eval_archive", os.path.join(HERE, "..", "tools", "analyze_eval_archive.py"))
    analyze = importlib.util.module_from_spec(spec_a)
    spec_a.loader.exec_module(analyze)
    try:
        stats = analyze.compute(os.path.join(root, "bare"))
        analyzed = isinstance(stats, dict)
    except analyze.AnalysisError:
        analyzed = False
    cases.append(("analyze_eval_archive with no snapshot loads the empty document", analyzed))
    try:
        empty_doc, empty_rows = store.load_archive(bare_archive)
        default_enum = (empty_rows == [] and empty_doc["structured_entries"] == []
                        and set(empty_doc["Self_Evaluate_Themes"]["patterns"]) == set("ABCDE")
                        and empty_doc["Self_Evaluate_Themes"]["patterns"] == store.DEFAULT_PATTERNS)
    except (ValueError, AttributeError, KeyError):
        default_enum = False
    cases.append(("the empty document carries the default enum", default_enum))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
