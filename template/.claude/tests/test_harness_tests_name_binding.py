"""Proof: a `<stem>_<suffix>_test.*` proof binds to `<stem>`, the way `test_<stem><suffix>.py` does.

`review_fanout_spill_test.js` fingerprints `workflows/review_fanout.js`'s output, yet a change to the
engine never selected it: the name rule matched only `review_fanout_test.*`, and the engine's
PROOF_BINDINGS entry replaces the mention rule. The close then passed a commit that broke it.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("harness_tests", HERE.parent / "scripts" / "harness_tests.py")
ht = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ht)


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMP")) as tmp:
        root = Path(tmp)
        tests = root / ".claude" / "tests"
        tests.mkdir(parents=True)
        (root / ".claude" / "workflows").mkdir()
        (root / ".claude" / "workflows" / "engine.js").write_text("x\n", encoding="utf-8")
        names = ("engine_test.js", "engine_spill_test.js", "test_engine_more.py", "engineering_test.js",
                 "other_test.js")
        for name in names:
            (tests / name).write_text("// fixture\n", encoding="utf-8")
        saved = dict(ht.PROOF_BINDINGS)
        ht.PROOF_BINDINGS[".claude/workflows/engine.js"] = ["engine_test.js"]
        try:
            got = ht.bindings(str(root), [".claude/workflows/engine.js"],
                              proofs=[str(tests / n) for n in names])[".claude/workflows/engine.js"]
        finally:
            ht.PROOF_BINDINGS.clear()
            ht.PROOF_BINDINGS.update(saved)
        bound = sorted(os.path.basename(p) for p in got)
        cases = [
            ("`<stem>_<suffix>_test.js` binds to its stem", "engine_spill_test.js" in bound),
            ("`<stem>_test.js` and `test_<stem>*.py` still bind", {"engine_test.js", "test_engine_more.py"} <= set(bound)),
            ("a longer word sharing the prefix does not bind", "engineering_test.js" not in bound),
            ("an unrelated proof does not bind", "other_test.js" not in bound),
        ]
        (root / ".claude" / "tools").mkdir()
        (root / ".claude" / "tools" / "engine.py").write_text("x = 1\n", encoding="utf-8")
        (root / ".claude" / "tools" / "user.py").write_text("import engine\n", encoding="utf-8")
        for name, text in (("test_user.py", "x\n"), ("_test_engine_helper.py", "x\n"),
                           ("test_mentions.py", "engine.py\n"), ("test_engine_core.py", "x\n")):
            (tests / name).write_text(text, encoding="utf-8")
        shipped = getattr(ht, "shipped_proofs", None)
        got = shipped(str(root), ".claude/tools/engine.py") if shipped else None
        cases += [
            ("shipped_proofs binds by name and by importer, as repo-relative paths",
             got is not None and {".claude/tests/test_engine_core.py", ".claude/tests/test_user.py",
                                  ".claude/tests/test_engine_more.py"} <= set(got)),
            ("shipped_proofs skips a mention and a `_` fixture",
             got is not None and not {".claude/tests/test_mentions.py", ".claude/tests/_test_engine_helper.py"} & set(got)),
        ]
        by_tool = getattr(ht, "shipped_proofs_by_tool", None)
        tools3 = [".claude/tools/engine.py", ".claude/tools/user.py", ".claude/tools/none.py"]
        calls = {"discover": 0, "sources": 0}
        real_discover, real_sources = ht.discover, ht._module_sources

        def counted_discover(*a, **k):
            calls["discover"] += 1
            return real_discover(*a, **k)

        def counted_sources(*a, **k):
            calls["sources"] += 1
            return real_sources(*a, **k)

        ht.discover, ht._module_sources = counted_discover, counted_sources
        try:
            many = by_tool(str(root), tools3) if by_tool else None
        finally:
            ht.discover, ht._module_sources = real_discover, real_sources
        literal = {
            ".claude/tools/engine.py": [".claude/tests/engine_spill_test.js", ".claude/tests/engine_test.js",
                                        ".claude/tests/test_engine_core.py", ".claude/tests/test_engine_more.py",
                                        ".claude/tests/test_user.py"],
            ".claude/tools/user.py": [".claude/tests/test_user.py"],
            ".claude/tools/none.py": [],
        }
        cases += [
            ("shipped_proofs_by_tool scans modules and discovers proofs once for three tools",
             many is not None and calls == {"discover": 1, "sources": 1}),
            ("shipped_proofs_by_tool returns each tool's literal proof list",
             many is not None and {t: sorted(v) for t, v in many.items()} == literal),
        ]
        for label, ok in cases:
            print(("ok   " if ok else "FAIL ") + label + ("" if ok else "   bound=%s" % bound))
            failures += not ok
    print("%d failure(s)" % failures if failures else "all cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
