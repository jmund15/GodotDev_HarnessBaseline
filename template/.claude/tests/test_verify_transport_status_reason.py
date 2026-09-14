#!/usr/bin/env python3
"""Proof that verify_transport_status.py still satisfies the registry's suspension contract.

`set_transport_state` gained a REQUIRED reason for the `unavailable` transition. This tool's
`--both` mode flips a transport to the opposite state and back, so it was the one caller that broke
— and it broke by raising, from a tool whose whole job is to verify that transport handling works.

Two halves, because either alone is satisfiable by an accident:
  1. the contract really does reject a reasonless suspension (else nothing here is being enforced);
  2. every `set_transport_state` call in this tool passes a non-empty reason.

Source-level for (2) on purpose: `--both` mutates the real registry, and a proof that runs it would
have to either touch the shipped file or reimplement the tool.

Run: python3 .claude/tests/test_verify_transport_status_reason.py
"""
import ast
import importlib.util
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
TOOL = os.path.join(TOOLS, "verify_transport_status.py")

spec = importlib.util.spec_from_file_location("mr", os.path.join(TOOLS, "model_registry.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def _calls():
    """Every `set_transport_state(...)` call node in the tool's source."""
    tree = ast.parse(open(TOOL, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name == "set_transport_state":
            out.append(node)
    return out


def _reason_of(call):
    for kw in call.keywords:
        if kw.arg == "reason":
            return kw.value
    return None


def _copy_registry():
    tmp = os.path.join(tempfile.mkdtemp(prefix="vts_"), "external_models.json")
    shutil.copyfile(str(mr.find_registry()), tmp)
    return tmp


def _raises(fn):
    try:
        fn()
    except mr.RegistryError:
        return True
    return False


CALLS = _calls()

CASES = [
    # (1) the contract is real -- without this, (2) proves only that a string is present.
    ("suspending a transport with NO reason is refused by the registry",
     lambda: _raises(lambda: mr.set_transport_state("codex", "unavailable",
                                                    path=_copy_registry()))),

    ("...and an empty/whitespace reason is refused too, not accepted as present",
     lambda: _raises(lambda: mr.set_transport_state("codex", "unavailable",
                                                    path=_copy_registry(), reason="   "))),

    # (2) the caller satisfies it.
    ("the tool still calls set_transport_state at all",
     lambda: len(CALLS) >= 2),

    ("every call passes a `reason=` keyword",
     lambda: all(_reason_of(c) is not None for c in CALLS)),

    ("...and every reason is a non-empty literal, not None or ''",
     lambda: all(isinstance(_reason_of(c), ast.Constant)
                 and isinstance(_reason_of(c).value, str)
                 and _reason_of(c).value.strip()
                 for c in CALLS)),

    ("the reasons say this is a probe flip, so a reader of the registry is not misled",
     lambda: all("verify_transport_status" in _reason_of(c).value for c in CALLS)),
]


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
