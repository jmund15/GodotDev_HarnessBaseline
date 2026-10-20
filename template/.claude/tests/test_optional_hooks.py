#!/usr/bin/env python3
"""Proof for hooks/_optional_hooks.py: an absent sub-hook is skipped, a broken present one raises.

    python3 .claude/tests/test_optional_hooks.py
"""
import importlib
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def test_import_fault_isolated_per_member(monkeypatch, tmp_path):
    sys.path.insert(0, str(HOOKS))
    import _optional_hooks as loader
    monkeypatch.setattr(loader, 'HOOKS_DIR', tmp_path)
    for name in ('broken', 'healthy'):
        (tmp_path / (name + '.py')).write_text('', encoding='utf-8')
    real_import = importlib.import_module
    def planted(name):
        if name == 'broken':
            raise RuntimeError('planted import fault')
        if name == 'healthy':
            return 'healthy member'
        return real_import(name)
    monkeypatch.setattr(loader.importlib, 'import_module', planted)
    faults = []
    def recover(name, exc):
        faults.append((name, str(exc)))
        return 'fault stub'
    assert loader.load('broken', 'healthy', 'absent', on_error=recover) == (
        'fault stub', 'healthy member', None)
    assert faults == [('broken', 'planted import fault')]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        hooks = Path(tmp)
        (hooks / "_optional_hooks.py").write_text((HOOKS / "_optional_hooks.py").read_text(encoding="utf-8"),
                                                  encoding="utf-8")
        (hooks / "present_guard.py").write_text("VALUE = 7\n", encoding="utf-8")
        (hooks / "broken_guard.py").write_text("import module_that_does_not_exist\n", encoding="utf-8")
        sys.path.insert(0, str(hooks))
        try:
            loader = importlib.import_module("_optional_hooks")
            present, absent = loader.load("present_guard", "absent_guard")
            check("a present sub-hook loads", getattr(present, "VALUE", None) == 7, present)
            check("an absent sub-hook is None", absent is None, absent)
            check("a single name returns the bare module", loader.load("present_guard") is present)
            check("present() is true only for an installed sub-hook",
                  loader.present("present_guard") and not loader.present("absent_guard"))
            chain = (("present_guard", "advisory"), ("absent_guard", "enforcement"))
            check("adopted() keeps only installed entries of a chain",
                  loader.adopted(chain) == (("present_guard", "advisory"),), loader.adopted(chain))
            try:
                loader.load("broken_guard")
                check("a present sub-hook that cannot import raises", False, "no exception")
            except ImportError:
                check("a present sub-hook that cannot import raises", True)
        finally:
            sys.path.remove(str(hooks))
            for name in ("_optional_hooks", "present_guard", "broken_guard"):
                sys.modules.pop(name, None)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
