"""Re-runnable proof for tools/adaptation.py, the shared `adaptation.json` reader (Design
Doc §8, plan `sync-baseline-v2.md`).

Three shapes per the contract: absent file -> every default, no message; present file ->
project values, no message; a known key of the wrong type -> that key's default plus one
stderr line naming the file. Unknown keys are ignored. Fixtures live under the system temp
directory and are removed on teardown.

    python3 .claude/tests/test_adaptation.py
"""
import json
import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import adaptation  # noqa: E402
from _module_closure import copy_closure  # noqa: E402


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def _claude_dir_with(tmp, content):
    adaptation._CACHE.clear()  # a same-size rewrite inside one clock tick keeps its (mtime, size) key
    claude_dir = os.path.join(tmp, ".claude")
    skill_dir = os.path.join(claude_dir, "skills", "project_subsystems")
    os.makedirs(skill_dir, exist_ok=True)
    if content is not None:
        with open(os.path.join(skill_dir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    return claude_dir


def case_areas_key_reads_and_wrong_type_falls_back():
    tmp = tempfile.mkdtemp(prefix="adaptation_areas_")
    try:
        rows = [["Alpha", ".claude/tools/alpha"]]
        claude_dir = _claude_dir_with(tmp, json.dumps({"areas": rows}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        assert adaptation.DEFAULTS["areas"] == [], adaptation.DEFAULTS.get("areas")
        assert result["areas"] == rows and err == "", (result.get("areas"), err)
        claude_dir = _claude_dir_with(tmp, json.dumps({"areas": "not-a-list"}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        assert result["areas"] == [] and "areas" in err, (result.get("areas"), err)
    finally:
        _rm(tmp)


def case_retired_keys_are_named_with_where_and_replacement():
    rows = [{"name": "Old", "triggers": ["x"], "paths": ["a/**"]}, {"name": "New", "paths": ["b/**"]},
            {"name": "Older", "triggers": []}]
    lines = adaptation.retired_keys({"memory_domains": rows, "drive_commands": [], "tests_root": "Tests"})
    assert len(lines) == 2, lines  # one line per retired key present, not per row; an empty value is present
    by_key = {line.split()[0]: line for line in lines}
    triggers = by_key["memory_domains[].triggers"]
    assert "Old" in triggers and "Older" in triggers and "New" not in triggers, triggers
    assert "memory_domains[].paths" in triggers, triggers
    assert "drive_commands" in by_key and "delete" in by_key["drive_commands"], lines
    assert adaptation.retired_keys({"memory_domains": [{"name": "New", "paths": []}], "tests_root": "T"}) == []
    assert adaptation.retired_keys({}) == [] and adaptation.retired_keys({"memory_domains": "wrong"}) == []


def case_a_table_row_is_one_path_and_walks_nested_lists():
    table = [{"path": "rows[].inner[].old", "replacement": "rows[].inner[].new"}]
    data = {"rows": [{"name": "A", "inner": [{"old": 1}, {"new": 2}]}, {"inner": [{"new": 1}]}, "stray"]}
    got = adaptation.retired_keys(data, table)
    assert len(got) == 1 and got[0].startswith("rows[].inner[].old") and "A" in got[0] and "rows[].inner[].new" in got[0], got


def case_a_path_ending_in_a_list_names_each_row_and_never_crashes():
    table = [{"path": "rows[]", "replacement": "delete the list"}]
    got = adaptation.retired_keys({"rows": [{"name": "A"}, {"name": "B"}]}, table)
    assert len(got) == 1 and "2 rows" in got[0] and "A" in got[0] and "B" in got[0], got


def case_the_shipped_table_is_well_formed_and_no_shipped_adaptation_file_holds_a_retired_key():
    table = adaptation.RETIRED_KEYS
    assert table and all(isinstance(r.get("path"), str) and isinstance(r.get("replacement"), str)
                         and set(r) == {"path", "replacement"} for r in table), table
    assert len({r["path"] for r in table}) == len(table), "a path listed twice"
    skill_dir = os.path.join(TOOLS, "..", "skills", "project_subsystems")
    for name in sorted(os.listdir(skill_dir)):
        if name.startswith("adaptation") and name.endswith(".json"):
            with open(os.path.join(skill_dir, name), encoding="utf-8") as fh:
                assert adaptation.retired_keys(json.load(fh)) == [], name


def main():
    cases = []
    failures = []
    try:
        case_areas_key_reads_and_wrong_type_falls_back()
        cases.append(("areas key reads and wrong type falls back", True))
    except (AssertionError, KeyError) as exc:
        cases.append(("areas key reads and wrong type falls back: %s" % (exc,), False))
    for case in (case_retired_keys_are_named_with_where_and_replacement,
                 case_a_table_row_is_one_path_and_walks_nested_lists,
                 case_a_path_ending_in_a_list_names_each_row_and_never_crashes,
                 case_the_shipped_table_is_well_formed_and_no_shipped_adaptation_file_holds_a_retired_key):
        try:
            case()
            cases.append((case.__name__, True))
        except (AssertionError, KeyError, AttributeError) as exc:
            cases.append(("%s: %r" % (case.__name__, exc), False))
    doc = open(os.path.join(TOOLS, "adaptation.py"), encoding="utf-8").read().split('"""')[1]
    tmp = tempfile.mkdtemp(prefix="adaptation_proof_")
    try:
        # --- absent: file missing entirely -----------------------------------
        claude_dir = _claude_dir_with(tmp, None)
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("absent file returns every default", result == adaptation.DEFAULTS))
        cases.append(("absent file prints nothing to stderr", err == ""))
        cases.append(("doc_roots defaults to no roots", result.get("doc_roots") == []))
        claude_dir = _claude_dir_with(tmp, json.dumps({"doc_roots": "D:/not-a-list"}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a doc_roots that is not a list falls back to no roots", result.get("doc_roots") == []
                      and "doc_roots" in err))

        # --- present: valid project values are used ---------------------------
        seed = {
            "read_only_commands": ["make"],
            "tests_root": "MyTests",
            "memory_domains": [{"name": "X", "paths": ["x/**"], "memory_keywords": [],
                                 "skills": [], "rules": []}],
        }
        claude_dir = _claude_dir_with(tmp, json.dumps(seed))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("present file: known key overrides default",
                      result["read_only_commands"] == ["make"] and result["tests_root"] == "MyTests"))
        cases.append(("present file: key absent from seed keeps its default",
                      result["git_submodules"] == []))
        cases.append(("present file prints nothing to stderr", err == ""))

        # --- wrong-typed known key: default + one stderr line ------------------
        claude_dir = _claude_dir_with(tmp, json.dumps({"tests_root": ["not", "a", "string"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("wrong-typed key falls back to its default", result["tests_root"] == "Tests"))
        cases.append(("wrong-typed key names itself in stderr", "tests_root" in err))
        cases.append(("wrong-typed key: exactly one stderr line", len(err.strip().splitlines()) == 1))

        # --- unparseable file: every key defaults, one stderr line --------------
        claude_dir = _claude_dir_with(tmp, "{not json")
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("unparseable file returns every default", result == adaptation.DEFAULTS))
        cases.append(("unparseable file: exactly one stderr line", len(err.strip().splitlines()) == 1))

        # --- unknown keys are ignored -------------------------------------------
        claude_dir = _claude_dir_with(tmp, json.dumps({"totally_unknown_key": 123}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("unknown key is ignored, not an error", result == adaptation.DEFAULTS and err == ""))

        # --- get() defaults the same way -----------------------------------------
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("get() on an absent file returns the key's default",
                      adaptation.get(claude_dir, "tests_root") == "Tests"))

        # --- sensitivity_tags: the triage registry key -----------------------------
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("sensitivity_tags defaults to []",
                      adaptation.DEFAULTS.get("sensitivity_tags") == []
                      and adaptation.get(claude_dir, "sensitivity_tags") == []))
        entry = {"id": "t", "paths": ["a"], "sets": {"reach": "shared"}, "source": "s"}
        claude_dir = _claude_dir_with(tmp, json.dumps({"sensitivity_tags": [entry]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a list of entries loads under sensitivity_tags",
                      result.get("sensitivity_tags") == [entry] and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"sensitivity_tags": {"id": "t"}}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed sensitivity_tags falls back to []", result.get("sensitivity_tags") == []))
        cases.append(("a wrong-typed sensitivity_tags names itself in one stderr line",
                      "sensitivity_tags" in err and len(err.strip().splitlines()) == 1))
        # --- source_extensions / symbol_stopwords: the citation checker's keys -----
        for key in ("source_extensions", "symbol_stopwords"):
            claude_dir = _claude_dir_with(tmp, None)
            cases.append((key + " defaults to []",
                          adaptation.DEFAULTS.get(key) == [] and adaptation.get(claude_dir, key) == []))
            claude_dir = _claude_dir_with(tmp, json.dumps({key: [".x"]}))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append(("a list loads under " + key, result.get(key) == [".x"] and err == ""))
            claude_dir = _claude_dir_with(tmp, json.dumps({key: ".x"}))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append(("a wrong-typed " + key + " falls back to []", result.get(key) == []))
            cases.append(("a wrong-typed " + key + " names itself in one stderr line",
                          key in err and len(err.strip().splitlines()) == 1))
        # --- instruction_scope: the behavior check and duplicate scan's scope ----------
        default_scope = {"paths": [], "rows_dir": None}
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("instruction_scope defaults to an empty scope",
                      adaptation.DEFAULTS.get("instruction_scope") == default_scope
                      and adaptation.get(claude_dir, "instruction_scope") == default_scope))
        scope = {"paths": ["a/*.md"], "rows_dir": "rows"}
        claude_dir = _claude_dir_with(tmp, json.dumps({"instruction_scope": scope}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("an object loads under instruction_scope",
                      result.get("instruction_scope") == scope and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"instruction_scope": ["a"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed instruction_scope falls back to the empty scope",
                      result.get("instruction_scope") == default_scope))
        cases.append(("a wrong-typed instruction_scope names itself in one stderr line",
                      "instruction_scope" in err and len(err.strip().splitlines()) == 1))
        # --- review_mandates / review_scope: the review ledger's catalog and path scope ----
        for key, value in (("review_mandates", {"cq.C": {"home": "h", "interaction": False}}),
                           ("review_scope", {"exclude_dirs": [".claude"], "exclude_suffixes": [".png"]})):
            claude_dir = _claude_dir_with(tmp, None)
            cases.append((key + " defaults to {}",
                          adaptation.DEFAULTS.get(key) == {} and adaptation.get(claude_dir, key) == {}))
            claude_dir = _claude_dir_with(tmp, json.dumps({key: value}))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append(("an object loads under " + key, result.get(key) == value and err == ""))
            claude_dir = _claude_dir_with(tmp, json.dumps({key: ["a"]}))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append(("a wrong-typed " + key + " falls back to {}",
                          result.get(key) == {} and key in err and len(err.strip().splitlines()) == 1))
        # --- paired_repo_rules: the rule file each paired repo's commit path reads ----
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("paired_repo_rules defaults to {}",
                      adaptation.DEFAULTS.get("paired_repo_rules") == {}
                      and adaptation.get(claude_dir, "paired_repo_rules") == {}))
        rules = {"Jmodot": ".claude/rules/jmodot_submodule.md"}
        claude_dir = _claude_dir_with(tmp, json.dumps({"paired_repo_rules": rules}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("an object loads under paired_repo_rules", result.get("paired_repo_rules") == rules and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"paired_repo_rules": ["a"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed paired_repo_rules falls back to {} and names itself",
                      result.get("paired_repo_rules") == {} and "paired_repo_rules" in err
                      and len(err.strip().splitlines()) == 1))
        # --- lens_prefixes: the key prefix a project's lens registry headings share ----
        prefixes = {"review": "xx-"}
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("lens_prefixes defaults to {}: the review registry takes any heading",
                      adaptation.DEFAULTS.get("lens_prefixes") == {}
                      and adaptation.get(claude_dir, "lens_prefixes") == {}))
        claude_dir = _claude_dir_with(tmp, json.dumps({"lens_prefixes": prefixes}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("an object loads under lens_prefixes", result.get("lens_prefixes") == prefixes and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"lens_prefixes": "xx-"}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed lens_prefixes falls back to {} and names itself",
                      result.get("lens_prefixes") == {} and "lens_prefixes" in err
                      and len(err.strip().splitlines()) == 1))
        # --- lens_preloads: doctrine files a project attaches to a lens beyond its header `preload:` lines ----
        pre = {"plc-x": ["skills/a.md", "skills/b.md"]}
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("lens_preloads defaults to {}: a lens preloads only its header lines",
                      adaptation.DEFAULTS.get("lens_preloads") == {}
                      and adaptation.get(claude_dir, "lens_preloads") == {}))
        claude_dir = _claude_dir_with(tmp, json.dumps({"lens_preloads": pre}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("an object loads under lens_preloads", result.get("lens_preloads") == pre and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"lens_preloads": ["a"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed lens_preloads falls back to {} and names itself",
                      result.get("lens_preloads") == {} and "lens_preloads" in err
                      and len(err.strip().splitlines()) == 1))
        cases.append(("the module docstring lists lens_preloads with its reader",
                      "lens_preloads" in doc and "lens.py" in doc))
        # --- gate_cadence: the harness' one cadence data key --------------------------------
        empty = {"slice": [], "merge": [], "close": [], "wide": []}
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("gate_cadence defaults to the empty table",
                      adaptation.DEFAULTS.get("gate_cadence") == empty
                      and adaptation.get(claude_dir, "gate_cadence") == empty
                      and adaptation.gate_cadence_errors(empty) == []))
        good = {"slice": ["run the edited unit's proof file"],
                "merge": ["npm run verify:merge"],
                "close": ["python3 .claude/scripts/harness_tests.py --close"],
                "wide": [{"runner": "(?:python3?\\s+)?\\S*harness_tests\\.py\\b", "exempt": ["^--help$"],
                          "narrow": None},
                         {"runner": "npm run verify\\b", "exempt": [],
                          "narrow": {"requires": ["^--reach$", "\\.spec\\.ts$"]}},
                         {"runner": "runtests\\.ps1", "exempt": [],
                          "narrow": {"declared": {"arg": "Filter", "glob": "**/*.cs",
                                                  "decl": ["class\\s+(\\w+)"], "admit": []}}}]}
        claude_dir = _claude_dir_with(tmp, json.dumps({"gate_cadence": good}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a valid gate_cadence table loads", result.get("gate_cadence") == good and err == ""))
        cases.append(("a valid gate_cadence table validates", adaptation.gate_cadence_errors(good) == []))
        cases.append(("a wide-row hint validates",
                      adaptation.gate_cadence_errors(
                          dict(good, wide=[dict(good["wide"][0], hint="name one scenario")])) == []))
        for label, value, needle in (
                ("a non-string wide-row hint",
                 dict(good, wide=[dict(good["wide"][0], hint=5)]), "wide[0].hint"),
                ("a non-object", ["slice"], "not an object"),
                ("an unknown key", dict(good, slices=[]), "slices"),
                ("a non-list slice", dict(good, slice="run"), "slice"),
                ("an empty command form", dict(good, slice=[""]), "slice[0]"),
                ("a wide row that is not an object", dict(good, wide=["run"]), "wide[0]"),
                ("an unknown wide-row key", dict(good, wide=[dict(good["wide"][0], narrowish=None)]), "wide[0]"),
                ("a runner that does not compile",
                 dict(good, wide=[{"runner": "[", "exempt": [], "narrow": None}]), "wide[0].runner"),
                ("an exempt term that does not compile",
                 dict(good, wide=[{"runner": "x", "exempt": ["("], "narrow": None}]), "wide[0].exempt[0]"),
                ("a narrow object with neither branch",
                 dict(good, wide=[{"runner": "x", "exempt": [], "narrow": {}}]), "wide[0].narrow"),
                ("a narrow object with both branches",
                 dict(good, wide=[{"runner": "x", "exempt": [],
                                   "narrow": {"requires": ["a"], "declared": {}}}]), "wide[0].narrow"),
                ("a requires term that does not compile",
                 dict(good, wide=[{"runner": "x", "exempt": [], "narrow": {"requires": ["["]}}]),
                 "wide[0].narrow.requires[0]"),
                ("a declared decl without one group",
                 dict(good, wide=[{"runner": "x", "exempt": [], "narrow": {"declared": {
                     "arg": "Filter", "glob": "**/*.cs", "decl": ["class \\w+"], "admit": []}}}]),
                 "wide[0].narrow.declared.decl[0]"),
                ("a declared row missing its glob",
                 dict(good, wide=[{"runner": "x", "exempt": [], "narrow": {"declared": {
                     "arg": "Filter", "decl": ["class (\\w+)"], "admit": []}}}]),
                 "wide[0].narrow.declared.glob")):
            errors = adaptation.gate_cadence_errors(value)
            ok = bool(errors) and any(needle in e for e in errors)
            cases.append((("gate_cadence rejects " + label) if ok else
                          ("gate_cadence rejects %s: %s" % (label, errors)), ok))
        claude_dir = _claude_dir_with(tmp, json.dumps({"gate_cadence": good["slice"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed gate_cadence falls back to the empty table",
                      result.get("gate_cadence") == empty and "gate_cadence" in err
                      and len(err.strip().splitlines()) == 1))
        cases.append(("the module docstring names gate_cadence and its three readers",
                      "gate_cadence" in doc and "guard_text" in doc and "_dispatch_checks" in doc
                      and "wide_test_guard" in doc))
        reach_good = dict(good, reach=[{"name": "harness", "paths": [".claude/**"], "cmd": "run {paths}"}],
                          reach_exempt=["docs/**"], reach_fallback="npm run verify")
        cases.append(("a reach table validates", adaptation.gate_cadence_errors(reach_good) == []))
        for label, bad, needle in (
                ("a reach entry with no cmd", {"reach": [{"name": "a", "paths": ["x/**"]}]}, "reach[0].cmd"),
                ("a reach entry with no globs", {"reach": [{"name": "a", "paths": [], "cmd": "c"}]}, "reach[0].paths"),
                ("a non-list exempt", {"reach": [], "reach_exempt": "docs/**"}, "reach_exempt"),
                ("a non-string fallback", {"reach": [], "reach_fallback": 3}, "reach_fallback")):
            errors = adaptation.gate_cadence_errors(bad)
            cases.append(((label + " is named") if any(needle in e for e in errors)
                          else ("%s is named: %s" % (label, errors)), any(needle in e for e in errors)))
        main_good = {"main": {"command": ["pwsh", "-File", "g.ps1"], "retry_exits": [7, 8], "outputs": ["a.json"]}}
        cases.append(("a main gate validates", adaptation.gate_cadence_errors(main_good) == []))
        cases.append(("a main gate with only a command validates",
                      adaptation.gate_cadence_errors({"main": {"command": ["x"]}}) == []))
        for label, bad, needle in (
                ("a main that is not an object", {"main": "x"}, "main"),
                ("a main with no command", {"main": {}}, "main.command"),
                ("a main command that is a string", {"main": {"command": "run it"}}, "main.command"),
                ("a main command with an empty element", {"main": {"command": ["x", ""]}}, "main.command"),
                ("a main retry_exits that is not ints", {"main": {"command": ["x"], "retry_exits": ["7"]}}, "main.retry_exits"),
                ("a main retry_exits holding a bool", {"main": {"command": ["x"], "retry_exits": [True]}}, "main.retry_exits"),
                ("a main outputs that is not a path list", {"main": {"command": ["x"], "outputs": [1]}}, "main.outputs"),
                ("an unknown main key", {"main": {"command": ["x"], "retries": 1}}, "main")):
            errors = adaptation.gate_cadence_errors(bad)
            cases.append(((label + " is named") if any(needle in e for e in errors)
                          else ("%s is named: %s" % (label, errors)), any(needle in e for e in errors)))
        cases.append(("drift_scan defaults to {}", adaptation.DEFAULTS.get("drift_scan") == {}
                      and adaptation.get(_claude_dir_with(os.path.join(tmp, "drift_bare"), None), "drift_scan") == {}))
        drift_dir = _claude_dir_with(os.path.join(tmp, "drift_bad"), json.dumps({"drift_scan": ["x"]}))
        result, err = _capture_stderr(lambda: adaptation.load(drift_dir))
        cases.append(("a non-dict drift_scan is an error and keeps {}", result["drift_scan"] == {} and "drift_scan" in err))
        # --- layered defaults: DEFAULTS, then pure/coding/godot defaults files, then adaptation.json ----
        def put(claude_dir, name, doc):
            with open(os.path.join(claude_dir, "skills", "project_subsystems", name), "w",
                      encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(doc))

        claude_dir = _claude_dir_with(tmp, json.dumps({
            "tests_root": "Project", "paired_repo_rules": {"a": "project", "p": "p"},
            "memory_domains": [{"name": "x"}, {"name": "proj"}],
            "index_prebuild": ["cmd-proj", "cmd-pure"],
            "lens_preloads": {"sa-x": ["b.md", "a.md"], "sa-z": ["z.md"]}}))
        put(claude_dir, "adaptation.godot.defaults.json", {"tests_root": "Third", "index_prebuild": ["cmd-godot"],
                                                           "paired_repo_rules": {"a": "godot", "g": "g"}})
        put(claude_dir, "adaptation.pure.defaults.json", {"tests_root": "Pure", "index_prebuild": ["cmd-pure"],
                                                          "memory_domains": [{"name": "x"}],
                                                          "lens_preloads": {"sa-x": ["a.md"], "sa-y": ["y.md"]},
                                                          "paired_repo_rules": {"a": "pure", "u": "u"}})
        put(claude_dir, "adaptation.coding.defaults.json", {"land_prefix": "coding/", "tests_root": "Coding"})
        adaptation._CACHE.clear()
        result = adaptation.load(claude_dir)
        cases.append(("a scalar takes the last file that sets it: adaptation.json beats the defaults files",
                      result["tests_root"] == "Project"))
        cases.append(("a scalar set only in a layer defaults file loads", result["land_prefix"] == "coding/"))
        cases.append(("a dict key merges key by key, later file winning",
                      result["paired_repo_rules"] == {"a": "project", "u": "u", "g": "g", "p": "p"}))
        cases.append(("a list key is the defaults items then the project's, exact duplicates dropped",
                      result["index_prebuild"] == ["cmd-pure", "cmd-godot", "cmd-proj"]
                      and result["memory_domains"] == [{"name": "x"}, {"name": "proj"}]))
        cases.append(("a list inside a dict key is the earlier files' items then the later's, duplicates dropped",
                      result["lens_preloads"] == {"sa-x": ["a.md", "b.md"], "sa-y": ["y.md"], "sa-z": ["z.md"]}))
        os.remove(os.path.join(claude_dir, "skills", "project_subsystems", "adaptation.json"))
        adaptation._CACHE.clear()
        result = adaptation.load(claude_dir)
        cases.append(("layer order is pure, coding, godot: godot wins a scalar", result["tests_root"] == "Third"))
        cases.append(("an absent adaptation.json leaves the defaults files in force",
                      result["index_prebuild"] == ["cmd-pure", "cmd-godot"]))
        before = adaptation.get(claude_dir, "land_prefix")
        put(claude_dir, "adaptation.coding.defaults.json", {"land_prefix": "coding2/"})
        cases.append(("rewriting a defaults file invalidates the cache (same-size edit)",
                      before == "coding/" and adaptation.get(claude_dir, "land_prefix") == "coding2/"))
        put(claude_dir, "adaptation.coding.defaults.json", {"land_prefix": ["bad"]})
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed key in a defaults file keeps the default and names that file",
                      result["land_prefix"] == "" and "adaptation.coding.defaults.json" in err))
        cases.append(("index_prebuild defaults to []", adaptation.DEFAULTS.get("index_prebuild") == []
                      and adaptation.get(_claude_dir_with(os.path.join(tmp, "bare"), None), "index_prebuild") == []))
        main_dir = _claude_dir_with(os.path.join(tmp, "main_check"), json.dumps({"main_check": ".github/workflows/m.yml"}))
        cases.append(("main_check defaults to the empty string: no main check declared",
                      adaptation.DEFAULTS.get("main_check") == ""
                      and adaptation.get(_claude_dir_with(os.path.join(tmp, "bare2"), None), "main_check") == ""))
        cases.append(("a main_check string loads", adaptation.get(main_dir, "main_check") == ".github/workflows/m.yml"))
        bad_main = _claude_dir_with(os.path.join(tmp, "main_check_bad"), json.dumps({"main_check": ["x"]}))
        result, err = _capture_stderr(lambda: adaptation.load(bad_main))
        cases.append(("a wrong-typed main_check keeps the default and names the file",
                      result["main_check"] == "" and "adaptation.json" in err))

        # --- pr_pipeline: the project steps of the shared batch PR loop (commands/pr_pipeline.md) ---------
        pipe = os.path.join(tmp, "pr_pipeline")  # its own dir: the shared one holds earlier cases' defaults files
        points = ("discover", "sync", "pre_review", "gate", "checklist", "post_merge")
        empty_points = {point: [] for point in points}
        claude_dir = _claude_dir_with(pipe, None)
        cases.append(("pr_pipeline defaults to six empty hook points",
                      adaptation.DEFAULTS.get("pr_pipeline") == empty_points
                      and adaptation.get(claude_dir, "pr_pipeline") == empty_points))
        rows = {"gate": ["python3 tools/gate.py --full"], "post_merge": ["/update_roadmap"]}
        claude_dir = _claude_dir_with(pipe, json.dumps({"pr_pipeline": rows}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a pr_pipeline naming two points loads and leaves the other four empty",
                      result.get("pr_pipeline") == dict(empty_points, **rows) and err == ""))
        cases.append(("a dotted name reads one hook point; an unset point reads empty",
                      adaptation.get(claude_dir, "pr_pipeline.gate") == rows["gate"]
                      and adaptation.get(claude_dir, "pr_pipeline.post_merge") == rows["post_merge"]
                      and adaptation.get(claude_dir, "pr_pipeline.sync") == []))
        for label, bad in (("a point that is not a list", {"gate": "make gate"}),
                           ("a point holding a non-string entry", {"gate": [5]}),
                           ("a point holding a blank entry", {"gate": [" "]}),
                           ("an unknown hook point", {"gates": ["make gate"]})):
            claude_dir = _claude_dir_with(pipe, json.dumps({"pr_pipeline": bad}))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append((label + " keeps every default and names pr_pipeline in one stderr line",
                          result.get("pr_pipeline") == empty_points and "pr_pipeline" in err
                          and len(err.strip().splitlines()) == 1))
        claude_dir = _claude_dir_with(pipe, json.dumps({"pr_pipeline": {"gate": ["make gate"], "sync": [7]}}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a good pr_pipeline point beside a bad one keeps only the good one",
                      result.get("pr_pipeline") == dict(empty_points, gate=["make gate"])
                      and "pr_pipeline.sync" in err))
        claude_dir = _claude_dir_with(pipe, json.dumps({"pr_pipeline": ["gate"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a pr_pipeline that is not an object keeps the default and names the key",
                      result.get("pr_pipeline") == empty_points and "'pr_pipeline'" in err))
        claude_dir = _claude_dir_with(pipe, json.dumps({"pr_pipeline": {"gate": ["b"], "sync": ["s"]}}))
        defaults_file = os.path.join(claude_dir, "skills", "project_subsystems", "adaptation.coding.defaults.json")
        with open(defaults_file, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"pr_pipeline": {"gate": ["a"]}}, fh)
        adaptation._CACHE.clear()
        cases.append(("a layer defaults file's hook entries come before adaptation.json's, duplicates dropped",
                      adaptation.get(claude_dir, "pr_pipeline.gate") == ["a", "b"]
                      and adaptation.get(claude_dir, "pr_pipeline.sync") == ["s"]))

        # --- CLI verbs -----------------------------------------------------------
        import subprocess
        script = os.path.join(TOOLS, "adaptation.py")
        root = os.path.join(tmp, "cli")
        cdir = os.path.join(root, ".claude")
        sdir = os.path.join(cdir, "skills", "project_subsystems")
        os.makedirs(sdir)
        marker = os.path.join(root, "ran.txt")
        ok_cmd = "%s -c \"open(r'%s','a').write('x')\"" % (sys.executable.replace("\\", "/"), marker.replace("\\", "/"))
        bad_cmd = "%s -c \"import sys; sys.exit(3)\"" % sys.executable.replace("\\", "/")
        with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"index_prebuild": [bad_cmd, ok_cmd], "tests_root": "T", "usage_reset": {"weekday": "monday", "hour": 2},
                       "gate_cadence": good}, fh)
        tools_copy = os.path.join(cdir, "tools")
        copy_closure(cdir, "tools/adaptation.py", "hooks/_msys.py")

        def cli(*args, env=None):
            done = subprocess.run([sys.executable, os.path.join(tools_copy, "adaptation.py"), *args],
                                  capture_output=True, text=True, encoding="utf-8", cwd=tmp, env=env)
            return done.returncode, done.stdout, done.stderr

        code, out, _ = cli("get", "index_prebuild")
        cases.append(("get prints a list one item per line", code == 0 and out.splitlines() == [bad_cmd, ok_cmd]))
        code, out, _ = cli("get", "usage_reset")
        cases.append(("get prints a dict as one JSON line",
                      code == 0 and len(out.splitlines()) == 1 and json.loads(out) == {"weekday": "monday", "hour": 2}))
        code, out, _ = cli("get", "tests_root")
        cases.append(("get prints a scalar as is", code == 0 and out.strip() == "T"))
        code, _, _ = cli("get", "no_such_key")
        cases.append(("get of an unknown key exits 2", code == 2))
        code, out, _ = cli("get", "gate_cadence")
        cases.append(("get gate_cadence prints the table as one JSON line",
                      code == 0 and len(out.splitlines()) == 1 and json.loads(out) == good))
        with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"gate_cadence": dict(good, wide=[{"runner": "[", "exempt": [], "narrow": None}])}, fh)
        code, _, err = cli("get", "gate_cadence")
        cases.append(("a malformed gate_cadence row exits 1 naming the row",
                      code == 1 and "wide[0].runner" in err))
        with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"index_prebuild": [bad_cmd, ok_cmd], "tests_root": "T", "usage_reset": {"weekday": "monday", "hour": 2},
                       "gate_cadence": good}, fh)
        code, out, _ = cli("run", "tests_root")
        cases.append(("run of a non-list key exits 2", code == 2))
        code, out, err = cli("run", "index_prebuild")
        with open(marker, encoding="utf-8") as fh:
            ran = fh.read()
        cases.append(("run executes every entry once, reports the failing one and exits 0",
                      code == 0 and ran == "x" and "3" in (out + err)))

        if os.name == "nt":
            # CreateProcess applies no PATHEXT: a .cmd tool named bare is found through the shared resolver, and an
            # argument cmd.exe would re-parse is refused rather than run as a second command.
            bin_dir = os.path.join(root, "bin")
            os.makedirs(bin_dir)
            planted = os.path.join(root, "planted.txt")
            with open(os.path.join(bin_dir, "plant_tool.cmd"), "w", newline="\r\n") as fh:
                fh.write("@echo off\necho tool-ran %1>>\"" + planted + "\"\n")
            env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))
            with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"index_prebuild": ["plant_tool abc"]}, fh)
            code, out, err = cli("run", "index_prebuild", env=env)
            ran = open(planted, encoding="utf-8").read() if os.path.exists(planted) else ""
            cases.append(("run finds a .cmd entry through PATH", code == 0 and "tool-ran abc" in ran and "failed" not in err))
            os.remove(planted) if os.path.exists(planted) else None
            injected = os.path.join(root, "injected.txt")
            with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"index_prebuild": ['plant_tool "x&echo INJECTED>%s"' % injected.replace("\\", "/")]}, fh)
            code, out, err = cli("run", "index_prebuild", env=env)
            cases.append(("run refuses a .cmd entry whose argument cmd.exe would re-parse",
                          code == 0 and not os.path.exists(injected) and "plant_tool" in err and "Traceback" not in err))
            with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"index_prebuild": ["no_such_tool_xyz a"]}, fh)
            code, out, err = cli("run", "index_prebuild", env=env)
            cases.append(("run names a missing command and exits 0", code == 0 and "no_such_tool_xyz" in err and "Traceback" not in err))

        with open(os.path.join(sdir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"pr_pipeline": {"gate": ["a b", "/c"]}, "instruction_scope": {"paths": [], "rows_dir": None}}, fh)
        code, out, _ = cli("get", "pr_pipeline.gate")
        cases.append(("get pr_pipeline.<point> prints one entry per line", code == 0 and out.splitlines() == ["a b", "/c"]))
        code, out, _ = cli("get", "pr_pipeline.sync")
        cases.append(("get of an unset hook point prints nothing and exits 0", code == 0 and out == ""))
        code, out, _ = cli("get", "pr_pipeline")
        cases.append(("get pr_pipeline prints the six points as one JSON line",
                      code == 0 and len(out.splitlines()) == 1 and json.loads(out) == dict(empty_points, gate=["a b", "/c"])))
        code, _, err = cli("get", "pr_pipeline.gates")
        cases.append(("get of an unknown hook point exits 2 naming it", code == 2 and "pr_pipeline.gates" in err))
        code, _, _ = cli("get", "no_such_key.gate")
        cases.append(("get of a dotted name under an unknown key exits 2", code == 2))
        code, out, _ = cli("get", "instruction_scope.rows_dir")
        cases.append(("a dotted entry that holds null prints it rather than exiting 2", code == 0 and out.strip() == "None"))
        plugin_cases(cases, tmp)
    finally:
        _rm(tmp)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


REGISTRATION = {"schema": 1, "core_root": ".", "entry": ".claude/scripts/plugin_entry.py",
                "sidecar_hooks": [{"matcher": "*", "command": "python3 {plugin_root}/.claude/hooks/g.py"}]}


def _plugin_root(tmp, label, registration=REGISTRATION, entry_text="def live_runs(table):\n    return []\n"):
    """A bench plugin checkout under tmp/plugin_<label> holding `.claude/plugin.bench.json` and, when entry_text is not None, its entry."""
    root = os.path.join(tmp, "plugin_" + label)
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    if registration is not None:
        with open(os.path.join(root, ".claude", "plugin.bench.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(registration if isinstance(registration, str) else json.dumps(registration))
    if entry_text is not None and isinstance(registration, dict) and isinstance(registration.get("entry"), str):
        entry = os.path.join(root, registration["entry"])
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        with open(entry, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(entry_text)
    return root


def plugin_cases(cases, tmp):
    from pathlib import Path
    claude_dir = _claude_dir_with(tmp, None)
    cases.append(("plugins defaults to {}", adaptation.DEFAULTS.get("plugins") == {}
                  and adaptation.get(claude_dir, "plugins") == {}))
    cases.append(("bench_liveness is no longer a known key", "bench_liveness" not in adaptation.DEFAULTS))

    def state_of(plugins, name="bench"):
        claude = _claude_dir_with(tmp, json.dumps({"plugins": plugins}))
        return adaptation.plugin(claude, name)

    absent = state_of({})
    cases.append(("a plugin the key does not name is absent", absent.state == "absent"))
    claude = _claude_dir_with(tmp, json.dumps({"plugins": {}}))
    cases.append(("plugin_entry of an absent plugin is None", adaptation.plugin_entry(claude, "bench") is None))

    ready_root = _plugin_root(tmp, "ready")
    ready = state_of({"bench": ready_root})
    cases.append(("a complete registration with its entry is ready",
                  ready.state == "ready" and Path(ready.root) == Path(ready_root)
                  and ready.manifest["entry"] == REGISTRATION["entry"] and not ready.error))
    cases.append(("plugin_at reads the same state from a plugin root",
                  adaptation.plugin_at(ready_root, "bench").state == "ready"))
    rel = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": "plugin_ready"}}))
    cases.append(("a relative root resolves against the checkout holding the adaptation file",
                  adaptation.plugin(rel, "bench").state == "ready"))

    broken = [
        ("root is not a directory", {"bench": os.path.join(tmp, "no_such_root")}, None),
        ("registration file missing", {"bench": _plugin_root(tmp, "nofile", registration=None)}, None),
        ("registration does not parse", {"bench": _plugin_root(tmp, "badjson", registration="{not json")}, None),
        ("schema is not 1", {"bench": _plugin_root(tmp, "schema2", registration=dict(REGISTRATION, schema=2))}, None),
        ("a required key is missing",
         {"bench": _plugin_root(tmp, "nokey", registration={k: v for k, v in REGISTRATION.items() if k != "entry"})}, "entry"),
        ("a key has the wrong type",
         {"bench": _plugin_root(tmp, "wrongtype", registration=dict(REGISTRATION, sidecar_hooks="x"))}, "sidecar_hooks"),
        ("a bench key has the wrong type",
         {"bench": _plugin_root(tmp, "wrongmw", registration=dict(REGISTRATION, measurement_workflows="x"))},
         "measurement_workflows"),
        ("the entry file is missing", {"bench": _plugin_root(tmp, "noentry", entry_text=None)}, "entry"),
    ]
    for label, plugins, needle in broken:
        got = state_of(plugins)
        cases.append(("unreadable: " + label,
                      got.state == "unreadable" and bool(got.error) and (needle is None or needle in got.error)))
        claude = _claude_dir_with(tmp, json.dumps({"plugins": plugins}))
        try:
            adaptation.plugin_entry(claude, "bench")
            raised = None
        except adaptation.PluginUnreadable as exc:
            raised = exc
        cases.append(("plugin_entry raises PluginUnreadable: " + label,
                      raised is not None and raised.name == "bench" and raised.error == got.error))

    edit_root = _plugin_root(tmp, "edit")
    claude = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": edit_root}}))
    first = adaptation.plugin(claude, "bench").state
    _plugin_root(tmp, "edit", registration=dict(REGISTRATION, schema=2))
    second = adaptation.plugin(claude, "bench").state
    _plugin_root(tmp, "edit")
    cases.append(("an edit to the registration between two calls in one process is seen",
                  (first, second, adaptation.plugin(claude, "bench").state) == ("ready", "unreadable", "ready")))

    entry_root = _plugin_root(tmp, "entry", entry_text="def live_runs(table):\n    return ['ran']\n")
    claude = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": entry_root}}))
    entry = adaptation.plugin_entry(claude, "bench")
    cases.append(("plugin_entry loads the entry by path", entry.live_runs({}) == ["ran"]))
    crash_root = _plugin_root(tmp, "crash", entry_text="raise RuntimeError('boom')\n")
    claude = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": crash_root}}))
    try:
        adaptation.plugin_entry(claude, "bench")
        err = None
    except adaptation.PluginUnreadable as exc:
        err = exc
    cases.append(("an entry that fails to import is unreadable", err is not None and "boom" in err.error))

    claude = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": ready_root, "other": entry_root}}))
    listed = adaptation.plugins(claude)
    cases.append(("plugins lists each configured plugin in key order with its state",
                  [name for name, _ in listed] == ["bench", "other"] and listed[0][1].state == "ready"
                  and listed[1][1].state == "unreadable"))

    import subprocess
    planted = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": ready_root}}))
    copy_closure(planted, "tools/adaptation.py", "hooks/_msys.py")
    here = os.path.join(planted, "tools", "adaptation.py")
    done = subprocess.run([sys.executable, here, "plugin", "bench"], capture_output=True, text=True, encoding="utf-8")
    cases.append(("CLI: plugin bench prints a ready state and the root, exit 0",
                  done.returncode == 0 and "state: ready" in done.stdout and "root: " in done.stdout))
    done = subprocess.run([sys.executable, here, "plugin", "nosuchplugin"], capture_output=True, text=True, encoding="utf-8")
    cases.append(("CLI: an unconfigured plugin prints absent, exit 0",
                  done.returncode == 0 and "state: absent" in done.stdout))

    calls = getattr(adaptation, "ENTRY_CALLS", {}).get("bench", {})
    cases.append(("ENTRY_CALLS names the bench's ten calls with their parameter names",
                  calls == {"roster_summary": [], "ladder_banner": [], "has_rosters": [], "rosters": ["pid_alive"],
                            "running_cells": ["name"], "detached_units": ["session_id", "pid_alive"],
                            "campaign_of": ["label"], "grid_running": ["campaign"], "wait_call": ["target"],
                            "live_runs": ["process_table"]}))

    seen_root = _plugin_root(tmp, "corevar", entry_text="import os\nSEEN = os.environ.get('HARNESS_CORE_ROOT')\n"
                                                         "def live_runs(table):\n    return []\n")
    claude = _claude_dir_with(tmp, json.dumps({"plugins": {"bench": seen_root}}))
    inherited = os.environ.get("HARNESS_CORE_ROOT")
    os.environ["HARNESS_CORE_ROOT"] = os.path.join(tmp, "some_other_core")
    try:
        loaded = adaptation.plugin_entry(claude, "bench")
    finally:
        if inherited is None:
            os.environ.pop("HARNESS_CORE_ROOT", None)
        else:
            os.environ["HARNESS_CORE_ROOT"] = inherited
    checkout = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(adaptation.__file__))))
    cases.append(("plugin_entry names the running checkout in HARNESS_CORE_ROOT at import, over an inherited value",
                  bool(loaded.SEEN) and os.path.normcase(os.path.abspath(loaded.SEEN)) == os.path.normcase(checkout)))

    first = _plugin_root(tmp, "twoA", entry_text="WHO = 'A'\ndef live_runs(table):\n    return []\n")
    second = _plugin_root(tmp, "twoB", entry_text="WHO = 'B'\ndef live_runs(table):\n    return []\n")
    got = []
    for root in (first, second, first):
        got.append(adaptation.plugin_entry(_claude_dir_with(tmp, json.dumps({"plugins": {"bench": root}})), "bench"))
    cases.append(("two plugin roots in one process load two distinct entry modules, and a root reloads from the cache",
                  [m.WHO for m in got] == ["A", "B", "A"] and got[0] is got[2] and got[0] is not got[1]))


def _capture_stderr(fn):
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = fn()
    return result, buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
