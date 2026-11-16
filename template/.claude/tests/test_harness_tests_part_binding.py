"""Proof: a part file (`<proof>_part<N>.<ext>`) is selected and reused exactly as the proof it is a part of.

A slow proof runs as several pool entries by keeping its cases in one file and adding thin part files that
run a share of them. A part's own text names only its main file, so without this rule a change to a file
the main proof names selected the main file and skipped the parts' cases, and a part's pass was reused
across a change only the main file's text names.
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


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
        root = Path(tmp)
        tests = root / ".claude" / "tests"
        tools = root / ".claude" / "tools"
        tests.mkdir(parents=True)
        tools.mkdir()
        files = {
            ".claude/data.json": "{}\n",
            ".claude/other.json": "{}\n",
            ".claude/tools/declared.py": "x = 1\n",
            ".claude/tests/test_big.py": "DATA = 'data.json'\ndef main(part=1):\n    return 0\n",
            ".claude/tests/test_big_part2.py": "import test_big\ntest_big.main(2)\n",
            ".claude/tests/test_solo_part2.py": "x = 1\n",
            ".claude/tests/test_unrelated.py": "x = 1\n",
        }
        for rel, text in files.items():
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture", "commit", "-q", "-m", "fixture")

        saved = dict(ht.PROOF_BINDINGS)
        ht.PROOF_BINDINGS[".claude/tools/declared.py"] = ["test_big.py"]
        try:
            touched = [".claude/data.json", ".claude/tests/test_big.py", ".claude/tools/declared.py", ".claude/other.json"]
            got = {t: sorted(os.path.basename(p) for p in v) for t, v in ht.bindings(str(root), touched).items()}
        finally:
            ht.PROOF_BINDINGS.clear()
            ht.PROOF_BINDINGS.update(saved)
        cases = [
            ("a file only the main proof's text names selects the part", "test_big_part2.py" in got[".claude/data.json"]),
            ("an edit to the main proof selects the part", "test_big_part2.py" in got[".claude/tests/test_big.py"]),
            ("a declared binding to the main proof selects the part", "test_big_part2.py" in got[".claude/tools/declared.py"]),
            ("the main proof is still selected once", got[".claude/data.json"].count("test_big.py") == 1),
            ("a file nothing names selects neither", not {"test_big.py", "test_big_part2.py"} & set(got[".claude/other.json"])),
            ("a `_part<N>` file with no main proof follows no other proof",
             all("test_solo_part2.py" not in v for v in got.values())),
            ("an unrelated proof is not selected", all("test_unrelated.py" not in v for v in got.values())),
        ]

        part = str(tests / "test_big_part2.py")
        before = ht._PassCache(str(root)).key(part)
        (root / ".claude" / "other.json").write_text('{"a": 1}\n', encoding="utf-8", newline="\n")
        unrelated = ht._PassCache(str(root)).key(part)
        (root / ".claude" / "data.json").write_text('{"a": 1}\n', encoding="utf-8", newline="\n")
        after = ht._PassCache(str(root)).key(part)
        cases += [
            ("the part has a reuse key", bool(before)),
            ("a change to a file nothing names keeps the part's key", before == unrelated),
            ("a change to a file only the main proof's text names changes the part's key", before != after),
        ]

        saved_caps = dict(ht._PROOF_TIMEOUTS)
        try:
            ht._PROOF_TIMEOUTS.clear()
            ht._PROOF_TIMEOUTS["test_big.py"] = 600
            inherited = ht._timeout_for(part, {})
            solo = ht._timeout_for(str(tests / "test_solo_part2.py"), {})
            ht._PROOF_TIMEOUTS["test_big_part2.py"] = 300
            own = ht._timeout_for(part, {})
        finally:
            ht._PROOF_TIMEOUTS.clear()
            ht._PROOF_TIMEOUTS.update(saved_caps)
        cases += [
            ("a part takes its main proof's timeout cap", inherited == 600),
            ("a part's own cap wins over its main proof's", own == 300),
            ("a `_part<N>` file with no main proof takes no other proof's cap", solo == ht._TIMEOUT_SEC),
        ]
        for label, ok in cases:
            print(("ok   " if ok else "FAIL ") + label + ("" if ok else "   bound=%s" % got))
            failures += not ok
    print("%d failure(s)" % failures if failures else "all cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
