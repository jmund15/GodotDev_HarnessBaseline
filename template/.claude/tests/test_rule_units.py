"""Proof for tools/rule_units.py: the rule-text units `restate_scan` reads, the scope and changed-line selection,
and the behavior-claim check's revision and changed-unit decisions. Fixtures are planted git repos under pytest's
tmp_path with a planted `instruction_scope`. The behavior ledger's wider decisions live in
`test_conformance_j1.py` and `test_conformance_j4.py`, which import the repo helpers below.

    python3 .claude/tests/test_rule_units.py
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
TOOLS = os.path.join(CLAUDE, "tools")
TOOL = os.path.join(TOOLS, "rule_units.py")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import _hook_call  # noqa: E402
import adaptation  # noqa: E402
import rule_units as en  # noqa: E402

SCOPE = {"paths": [".claude/rules/*.md", ".claude/workflows/*.js"], "rows_dir": ".claude/instruction_rows"}
LAYER = ".claude/skills/project_subsystems/adaptation.pure.defaults.json"


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def mkrepo(tmp, name, files, scope=SCOPE):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    if scope is not None:
        write(repo, ".claude/skills/project_subsystems/adaptation.json", json.dumps({"instruction_scope": scope}))
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def run(repo, *args):
    code, out, err, _ = _hook_call.call(TOOL, {}, argv=["--repo", repo, *args])
    assert "Traceback" not in err, err[-800:]
    return code, out, err


SAMPLE = ("---\nname: x\n---\n# Heading 5\n\nFirst sentence here. Second one; Third?\n\n"
          "```\nfenced 7 line.\n```\n\n| a | b |\n|---|---|\n| 3 | four |\n\n- item e.g. lower case stays.\n")


def test_units_split_sentences_and_rows():
    us = en.units("x.md", SAMPLE)
    assert [u.raw for u in us if u.kind == "prose"][:3] == ["First sentence here.", "Second one;", "Third?"]
    assert any(u.raw.endswith("lower case stays.") for u in us)
    assert [u.raw for u in us if u.kind == "table"] == ["| a | b |", "| 3 | four |"]
    assert not any(w in u.raw for u in us for w in ("Heading", "fenced", "name: x"))


def test_unit_hash_follows_the_text_not_the_line():
    first = en.units("x.md", SAMPLE)[0]
    moved = [u for u in en.units("x.md", "Intro.\n\nMore text.\n\nFirst sentence here.\n") if u.raw == first.raw][0]
    assert moved.hash == first.hash and moved.line_start == 5 and first.line_start == 6
    assert en.units("x.md", "First sentence here now.\n")[0].hash != first.hash
    assert re.fullmatch(r"[0-9a-f]{16}", first.hash)
    assert en.norm("- **Never** use `X`.  Twice") == "never use x. twice"
    tricky = "- **- 2026-05-13** initial roadmap. 7 parts."
    assert en.norm(en.norm(tricky)) == en.norm(tricky)


def test_js_units_are_comments_and_code_shapes():
    js = en.units("w.js", "const a = 1;\n// keep the cap at 40 rows. Second.\nif (n > 5) go();\n")
    assert [u.kind for u in js] == ["js", "js"]


def test_scope_loads_and_matches(tmp_path):
    bare = mkrepo(str(tmp_path), "noscope", {".claude/rules/a.md": "Keep it.\n"}, scope=None)
    assert en.load_scope(os.path.join(bare, ".claude")).invalid
    for command in (("check",), ("list", ".claude/rules/a.md")):
        rc, out, err = run(bare, *command)
        assert rc == 2 and "instruction_scope" in out + err, (command, rc, out, err)
    good = mkrepo(str(tmp_path), "scope", {".claude/rules/a.md": "Plain.\n"})
    sc = en.load_scope(os.path.join(good, ".claude"))
    assert sc.paths == SCOPE["paths"] and sc.rows_dir == SCOPE["rows_dir"] and not sc.invalid
    assert en.in_scope(en.Scope(["a/**/*.md"], "r", []), "a/b/c/d.md")
    assert not en.in_scope(en.Scope(["a/*.md"], "r", []), "a/b/d.md")
    assert en.in_scope(en.Scope(["a/*.md"], "r", []), "a/d.md")


def test_changed_lines_selects_the_added_side_and_untracked_files(tmp_path):
    base = {".claude/rules/a.md": "Intro line.\n", ".claude/other/b.md": "Intro line.\n"}
    repo = mkrepo(str(tmp_path), "c13", base)
    write(repo, ".claude/rules/a.md", "Intro line.\n\nAdded.\n")
    write(repo, ".claude/rules/u.md", "u1\nu2\n")
    sc = en.load_scope(os.path.join(repo, ".claude"))
    changed = en.changed_lines(repo, "HEAD", sc)
    assert changed.get(".claude/rules/a.md") == {2, 3} and changed.get(".claude/rules/u.md") == {1, 2}
    assert ".claude/rules/u.md" not in en.changed_lines(repo, "HEAD", sc, staged=True)
    assert list(en.changed_lines(repo, "HEAD", sc, paths=[".claude/rules/u.md"])) == [".claude/rules/u.md"]


def test_cli_imports_and_runs_from_its_registered_command():
    spawned = subprocess.run([sys.executable, TOOL, "--help"], capture_output=True, text=True, encoding="utf-8")
    assert spawned.returncode == 0 and "Traceback" not in spawned.stderr


def test_revision_binding_ignores_later_proof_and_dependency_inventory(tmp_path):
    path = ".claude/rules/a.md"
    proof = ".claude/tests/test_bound.py"
    dependency = ".claude/tools/dep.py"
    implementation = ".claude/tools/bound.py"
    text = "# Behavior\n\nBound behavior stays covered.\n"
    repo = mkrepo(str(tmp_path), "history", {
        path: text, implementation: "import dep\nDECISION = True\n",
        dependency: "VALUE = 1\n",
        proof: "import bound\ncase_id = 'bound-case'\nassertion = 'assert bound.DECISION'\n",
    })
    unit = next(u for u in en.behavior_units(path, text) if u.norm == "bound behavior stays covered.")
    binding = {"path": path, "sentence": unit.norm, "hash": unit.hash, "section": unit.heading,
               "class": "behavior", "source": "proof", "classificationQuote": unit.raw,
               "claimId": "bound", "implementation": [{"path": implementation, "symbol": "DECISION",
                                                          "quote": "DECISION = True"}],
               "proofs": [{"path": proof, "caseId": "bound-case", "assertion": "assert bound.DECISION",
                           "selector": {"type": "check-label"}}]}
    heading = en.behavior_units(path, text)[0]
    nonclaim = {"path": path, "sentence": heading.norm, "hash": heading.hash, "section": heading.heading,
                "class": "nonclaim", "source": "proof", "classificationQuote": heading.raw}
    write(repo, ".claude/instruction_rows/behaviors.jsonl", json.dumps(binding) + "\n" + json.dumps(nonclaim) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "bind")
    rev = git(repo, "rev-parse", "HEAD").strip()
    cases, dependencies, missing = en._behavior_binding_state(Path(repo), [binding], rev)
    assert not missing
    assert dependency in dependencies[proof]["paths"]
    assert LAYER in dependencies[proof]["paths"], "a layer defaults file is an input of the merged scope"
    key = dependencies[proof]["inputKey"]
    receipt = [{"proof": proof, "caseId": "bound-case", "inputKey": key, "status": "pass",
                "evidence": "ok bound-case"}]
    before = en.behavior_report(repo, paths=[path], rev=rev, proof_results=receipt)
    assert before["status"] == "pass", before
    git(repo, "rm", proof)
    git(repo, "mv", dependency, ".claude/tools/renamed.py")
    write(repo, implementation, "import renamed\nDECISION = True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "remove proof and rename dependency")
    after = en.behavior_report(repo, paths=[path], rev=rev, proof_results=receipt)
    assert after == before
    cases, dependencies, missing = en._behavior_binding_state(Path(repo), [binding], rev)
    assert not missing
    assert dependencies[proof]["inputKey"] == key
    assert dependency in dependencies[proof]["paths"]
    assert ".claude/tools/renamed.py" not in dependencies[proof]["paths"]


def test_behavior_scope_reads_the_layered_value_at_its_revision(tmp_path):
    project = {"instruction_scope": {"paths": [".claude/extra/*.md"]}}
    repo = mkrepo(str(tmp_path), "layered", {
        LAYER: json.dumps({"instruction_scope": SCOPE}),
        ".claude/skills/project_subsystems/adaptation.json": json.dumps(project)}, scope=None)
    rev = git(repo, "rev-parse", "HEAD").strip()
    write(repo, LAYER, "{}")
    at_rev = en._behavior_scope(Path(repo), rev)
    assert (at_rev.paths, at_rev.rows_dir, at_rev.invalid) == (
        SCOPE["paths"] + [".claude/extra/*.md"], SCOPE["rows_dir"], []), at_rev
    live = en._behavior_scope(Path(repo))
    layered = adaptation.get(os.path.join(repo, ".claude"), "instruction_scope")
    assert (live.paths, live.rows_dir) == (layered["paths"], layered.get("rows_dir")) == ([".claude/extra/*.md"], None)


def test_behavior_check_reports_only_changed_units(tmp_path):
    path = ".claude/rules/a.md"
    before = "# Stable\n\nKeep old behavior.\n\n# Changed\n\nKeep former behavior. Keep stable adjacent behavior.\n"
    after = before.replace("former", "new")
    repo = mkrepo(str(tmp_path), "delta", {path: before})
    stable = next(u for u in en.behavior_units(path, before) if u.norm == "keep old behavior.")
    binding = {"path": path, "sentence": stable.norm, "hash": stable.hash, "section": stable.heading,
               "class": "behavior", "source": "proof", "classificationQuote": stable.raw,
               "claimId": "unchanged", "implementation": [], "proofs": []}
    write(repo, ".claude/instruction_rows/behaviors.jsonl", json.dumps(binding) + "\n")
    git(repo, "add", ".claude/instruction_rows/behaviors.jsonl")
    git(repo, "commit", "-qm", "seed binding")
    write(repo, path, after)
    rc, out, err = run(repo, "check", "--paths", path, "--json")
    assert rc == 1, (rc, err)
    report = json.loads(out)
    assert [u["sentence"] for u in report["units"]] == ["keep new behavior."], report["counts"]
    assert report["scope"]["initialDebt"] == []
    assert report["claims"] == [] and report["scope"]["affected"] == []
    assert report["counts"]["changedUnits"] == 1
    # The runner and list caller still need whole-file conformance evidence.
    full = en.behavior_report(repo, paths=[path], proof_results=[])
    assert len(full["units"]) == 5
    rc, out, err = run(repo, "list", path, "--json")
    assert rc == 0 and len(json.loads(out)["units"]) == 5, err
    git(repo, "add", path)
    rc, out, err = run(repo, "check", "--staged", "--paths", path, "--json")
    assert len(json.loads(out)["units"]) == 1, err
    git(repo, "commit", "-qm", "change")
    revision = git(repo, "rev-parse", "HEAD").strip()
    write(repo, path, before.replace("former", "dirty"))
    rc, out, err = run(repo, "check", "--rev", revision, "--paths", path, "--json")
    assert rc == 1, (rc, err)
    report = json.loads(out)
    assert [u["sentence"] for u in report["units"]] == ["keep new behavior."]
    assert report["scope"]["base"] == revision + "^"
    assert report["scope"]["source"] == "revision"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
