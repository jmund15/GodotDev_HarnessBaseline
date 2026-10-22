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
import adaptation  # noqa: E402


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


def main():
    cases = []
    failures = []
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
            "memory_domains": [{"name": "X", "triggers": ["x"], "memory_keywords": [],
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
        # --- instruction_scope: the number check and duplicate scan's scope ----------
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
        doc = open(os.path.join(TOOLS, "adaptation.py"), encoding="utf-8").read().split('"""')[1]
        cases.append(("the module docstring lists instruction_scope with its readers",
                      "instruction_scope" in doc and "extract_numbers" in doc and "restate_scan" in doc))
        cases.append(("the module docstring lists the review keys with their readers",
                      "review_mandates" in doc and "review_scope" in doc and "review_ledger" in doc
                      and "escape_log" in doc))
        for content, expected in (({}, {}), ({"bench_liveness": {"root": "planted"}}, {"root": "planted"}),
                                  ({"bench_liveness": []}, {})):
            claude_dir = _claude_dir_with(tmp, json.dumps(content))
            result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
            cases.append(("bench_liveness reader: " + repr(content),
                          result.get("bench_liveness") == expected
                          and ("bench_liveness" in err if isinstance(content.get("bench_liveness"), list) else err == "")))
        cases.append(("the module docstring names bench_liveness and its reader",
                      "bench_liveness" in doc and "sync_plan_config" in doc))
        # --- later_round_roles: the grounding and coherence mandates a later review round seats, per stage ----
        roles = {"plan-check": {"grounding": "g", "coherence": {"meta": "m", "code": "c"}}}
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("later_round_roles defaults to {}: every stage omits the roles",
                      adaptation.DEFAULTS.get("later_round_roles") == {}
                      and adaptation.get(claude_dir, "later_round_roles") == {}))
        claude_dir = _claude_dir_with(tmp, json.dumps({"later_round_roles": roles}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("an object loads under later_round_roles", result.get("later_round_roles") == roles and err == ""))
        claude_dir = _claude_dir_with(tmp, json.dumps({"later_round_roles": ["a"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("a wrong-typed later_round_roles falls back to {} and names itself",
                      result.get("later_round_roles") == {} and "later_round_roles" in err
                      and len(err.strip().splitlines()) == 1))
        cases.append(("the module docstring lists later_round_roles with its reader",
                      "later_round_roles" in doc and "post_check" in doc))
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
        cases.append(("the module docstring lists lens_prefixes with its reader",
                      "lens_prefixes" in doc and "lens.py" in doc))
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
            json.dump({"index_prebuild": [bad_cmd, ok_cmd], "tests_root": "T", "usage_reset": {"weekday": "monday", "hour": 2}}, fh)
        tools_copy = os.path.join(cdir, "tools")
        os.makedirs(tools_copy)
        shutil.copy(script, tools_copy)

        def cli(*args):
            done = subprocess.run([sys.executable, os.path.join(tools_copy, "adaptation.py"), *args],
                                  capture_output=True, text=True, encoding="utf-8", cwd=tmp)
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
        code, out, _ = cli("run", "tests_root")
        cases.append(("run of a non-list key exits 2", code == 2))
        code, out, err = cli("run", "index_prebuild")
        with open(marker, encoding="utf-8") as fh:
            ran = fh.read()
        cases.append(("run executes every entry once, reports the failing one and exits 0",
                      code == 0 and ran == "x" and "3" in (out + err)))
        cases.append(("the module docstring names the defaults files, their auto sync and index_prebuild",
                      "adaptation.<layer>.defaults.json" in doc and "auto" in doc and "index_prebuild" in doc))
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


def _capture_stderr(fn):
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = fn()
    return result, buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
