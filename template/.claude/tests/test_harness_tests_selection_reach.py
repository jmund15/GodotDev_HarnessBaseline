"""Proof: a proof is selected by what its code reaches, never by prose.

`bindings` binds a changed file to a proof whose runnable text names it (a path it runs or reads, a string it
passes) or whose code loads it (an import, a `-m` module, a JS `require`). A comment or docstring that names the
file runs nothing, so it selects nothing: a runner edit used to select every proof that named the runner in prose.
A file whose text does not tokenize keeps its whole text, so a mention there still selects.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("harness_tests", HERE.parent / "scripts" / "harness_tests.py")
ht = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ht)

TARGET = ".claude/tools/target.py"
DATA = ".claude/data/target_data.json"
JS_TARGET = ".claude/tools/target_mod.js"
DECLARED = ".claude/tools/declared.py"

FILES = {
    TARGET: "x = 1\n",
    DATA: "{}\n",
    JS_TARGET: "module.exports = {};\n",
    DECLARED: "y = 1\n",
    # Real dependencies.
    ".claude/tests/test_imports.py": "import sys\nsys.path.insert(0, 'tools')\nimport target\n",
    ".claude/tests/test_from_import.py": "def case():\n    from target import x\n    return x\n",
    ".claude/tests/test_runs.py": ("import os, subprocess, sys\n"
                                   "subprocess.run([sys.executable, os.path.join('tools', 'target.py')])\n"),
    ".claude/tests/test_module.py": "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'target'])\n",
    ".claude/tests/test_reads.py": "import json\nDATA = json.load(open('data/target_data.json'))\n",
    ".claude/tests/test_names_in_code.py": "NAMES = ['target.py', 'target_data.json']\n",
    ".claude/tests/test_runs.sh": "#!/bin/bash\npython3 .claude/tools/target.py\n",
    ".claude/tests/requires_test.js": "const m = require('../tools/target_mod');\n",
    ".claude/tests/test_imports_part2.py": "import test_imports\n",
    ".claude/tests/test_imports_declared.py": "import declared\n",
    ".claude/tests/test_names_declared.py": "FIXTURE = 'declared.py'\n",
    ".claude/tests/test_broken.py": "# target.py\ndef broken(:\n",
    # Prose mentions only.
    ".claude/tests/test_comment.py": "# reads target.py and data/target_data.json\nx = 1  # target_mod.js\n",
    ".claude/tests/test_docstring.py": ('"""Proves target.py against target_data.json and target_mod.js."""\n'
                                        "def case():\n    '''target.py'''\n    return 1\n"),
    ".claude/tests/test_comment.sh": "#!/bin/bash\n# runs target.py on target_data.json\ntrue\n",
    ".claude/tests/comment_test.js": "// loads target_mod.js\n/* target_data.json\n   target.py */\nconst x = 1;\n",
    ".claude/tests/test_comment.ps1": "# target.py\n<#\ntarget_data.json\n#>\n$x = 1\n",
}


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def case_selection_writes_nothing(root):
    store = root / "legacy.json"
    store.write_bytes(b'{".claude/tests/test_imports.py": []}\n')
    before = store.read_bytes(), store.stat().st_mtime_ns
    layout = root / "per-proof"
    from unittest.mock import patch
    with patch.object(ht, "passes_path", return_value=str(store)), patch.object(
        ht, "pass_store_dir", return_value=str(layout)
    ), patch.object(ht, "case_passes_path", return_value=str(root / "no-cases.json")):
        selected = ht.select_for(str(root), [TARGET])
    return ("test_imports.py" in {Path(p).name for p in selected} and store.exists()
            and before == (store.read_bytes(), store.stat().st_mtime_ns) and not layout.exists())


def case_catalog_views(root):
    from _proof_runner.catalog import Catalog, View

    tool = ".claude/tools/sensor.py"
    named = ".claude/tests/test_sensor.py"
    importer = ".claude/tests/test_adapter.py"
    mentioned = ".claude/tests/test_weak.py"
    part = ".claude/tests/test_weak_part2.py"
    declared = ".claude/tests/test_declared.py"
    corpus = ".claude/tests/test_scan.py"
    missing = ".claude/tests/test_missing.py"
    excluded = ".claude/tests/test_excluded.py"
    policy = ".claude/skills/project_subsystems/adaptation.json"
    files = {
        tool: "sensor = True\n",
        ".claude/tools/adapter.py": "import sensor\n",
        named: "", importer: "", mentioned: "INPUT = 'sensor.py'\n", part: "",
        declared: "", corpus: "", excluded: "",
        policy: ('{"proof_bindings": {".claude/data/options.json": ["test_declared.py", "test_missing.py"]},'
                 ' "proof_corpus": {"test_scan.py": [".claude/data"]},'
                 ' "proof_excluded": {"test_excluded.py": "fixture"}}'),
        **{f".claude/tests/test_ext.{ext}": "" for ext in ("py", "js", "mjs", "sh", "ps1")},
    }
    for rel, text in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture", "commit", "-qm", "policy")
    (root / policy).write_text("{}", encoding="utf-8")
    (root / named).unlink()
    history = Catalog.open(View.revision(str(root), "HEAD"))
    index = Catalog.open(View.index(str(root)))
    local = Catalog.open(View.worktree(str(root)))
    return {
        "all five interpreter suffixes": set(history.proofs) & {p for p in files if '/test_ext.' in p}
        == {p for p in files if '/test_ext.' in p},
        "weak mentions select and inherit but do not ship": {named, mentioned, part} <= set(history.select([tool]).proofs)
        and set(history.shipped_for(tool)) == {named, importer},
        "revision and index use their own policy and bytes": history.select([".claude/data/options.json"]).proofs
        == index.select([".claude/data/options.json"]).proofs == [declared, corpus]
        and not local.select([".claude/data/options.json"]).proofs and named in history.proofs and named not in local.proofs,
        "candidate states distinguish exclusion, missing declaration and unavailable checkout bytes":
        {c.proof_id: c.state for c in history.candidates([".claude/data/options.json", excluded])}
        == {declared: "usable", corpus: "usable", missing: "missing", excluded: "excluded"}
        and {c.proof_id: c.state for c in local.candidates([named])}.get(named) == "unavailable",
    }


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
        root = Path(tmp)
        for rel, text in FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture", "commit", "-q", "-m", "fixture")

        saved = dict(ht.PROOF_BINDINGS)
        ht.PROOF_BINDINGS[DECLARED] = ["test_runs.py"]
        try:
            got = {t: sorted(os.path.basename(p) for p in v)
                   for t, v in ht.bindings(str(root), [TARGET, DATA, JS_TARGET, DECLARED]).items()}
        finally:
            ht.PROOF_BINDINGS.clear()
            ht.PROOF_BINDINGS.update(saved)

        def selects(t, name):
            return name in got[t]

        cases = [
            ("case_selection_writes_nothing", case_selection_writes_nothing(root)),
            ("an import selects the module it imports", selects(TARGET, "test_imports.py")),
            ("a function-local `from` import selects its module", selects(TARGET, "test_from_import.py")),
            ("a path the proof runs selects the script", selects(TARGET, "test_runs.py")),
            ("a `-m` module the proof runs selects the module", selects(TARGET, "test_module.py")),
            ("a data file the proof opens selects the proof", selects(DATA, "test_reads.py")),
            ("a name in a code string selects", selects(TARGET, "test_names_in_code.py") and
             selects(DATA, "test_names_in_code.py")),
            ("a shell proof's command line selects the script", selects(TARGET, "test_runs.sh")),
            ("a JS `require` without the extension selects the module", selects(JS_TARGET, "requires_test.js")),
            ("a part file follows its main proof's import", selects(TARGET, "test_imports_part2.py")),
            ("a file that does not tokenize keeps its whole text", selects(TARGET, "test_broken.py")),
            ("a declared binding replaces the naming rule, never the import rule",
             got[DECLARED] == ["test_imports_declared.py", "test_runs.py"]),
            ("a Python comment selects nothing", not any(selects(t, "test_comment.py") for t in got)),
            ("a docstring selects nothing", not any(selects(t, "test_docstring.py") for t in got)),
            ("a shell comment selects nothing", not any(selects(t, "test_comment.sh") for t in got)),
            ("a JS line or block comment selects nothing", not any(selects(t, "comment_test.js") for t in got)),
            ("a PowerShell line or block comment selects nothing", not any(selects(t, "test_comment.ps1") for t in got)),
        ]
        for label, ok in cases:
            print(("ok   " if ok else "FAIL ") + label + ("" if ok else "   bound=%s" % got))
            failures += not ok
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
        root = Path(tmp)
        _git(root, "init", "-q")
        try:
            decisions = case_catalog_views(root)
        except ModuleNotFoundError as exc:
            decisions = {f"catalog views: {exc}": False}
        for label, ok in decisions.items():
            print(("ok   " if ok else "FAIL ") + label)
            failures += not ok
    print("%d failure(s)" % failures if failures else "all cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
