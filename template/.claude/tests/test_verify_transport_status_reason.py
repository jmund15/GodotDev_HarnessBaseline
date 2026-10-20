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


PLAN_PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
import verify_transport_status as v
route = {m["id"] for m in v.model_registry.models_for("anthropic")}
print(v.TRANSPORT)
v.set_transport("anthropic-secondary")
print(bool(route) and v.transport_model_ids() == route)
v.assert_roster("unavailable")
print(any(not ok for _n, ok, _d in v.results))
"""


def _plan_probe():
    """[default target, plan reads its route's rows, unavailable-roster check is non-vacuous] from a
    child that sits on the live registry's second Anthropic login."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")}
    env.update(CLAUDE_CODE_TRANSPORT="anthropic", CLAUDE_CONFIG_DIR=os.path.expanduser("~/.claude-secondary"))
    out = subprocess.run([sys.executable, "-c", PLAN_PROBE, TOOLS], capture_output=True, text=True,
                         timeout=60, env=env)
    return out.stdout.split()


PLAN = []


def plan_line(i):
    if not PLAN:
        PLAN.extend(_plan_probe() or ["<no output>"])
    return PLAN[i] if i < len(PLAN) else None


CASES += [
    ("a session on a second plan targets that plan by default, not its route",
     lambda: plan_line(0) == "anthropic-secondary"),
    ("a plan target checks its route's rows",
     lambda: plan_line(1) == "True"),
    ("a plan target's unavailable-state roster check is not vacuous while the plan is live",
     lambda: plan_line(2) == "True"),
]


def _bp_named_attrs():
    """Every `budget_posture.<name>` attribute the tool's source names."""
    tree = ast.parse(open(TOOL, encoding="utf-8").read())
    return sorted({node.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                   and node.value.id == "budget_posture"})


def _bp_missing_attrs():
    """Those of them the hook module does not actually export. Empty is the only pass."""
    import importlib.util
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    spec = importlib.util.spec_from_file_location("bp_vts", os.path.join(HERE, "..", "hooks",
                                                                        "budget_posture.py"))
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    return [name for name in _bp_named_attrs() if not hasattr(bp, name)]


def _tool_run(transport):
    """(rc, stdout, stderr) for one read-only `--transport <plan>` run of the tool.

    Read-only on purpose: `--both` mutates the registry, which is why this file's other cases are
    source-level. `--transport` prints the checks for the state the registry is already in.
    """
    import subprocess
    r = subprocess.run([sys.executable, TOOL, "--transport", transport],
                       capture_output=True, text=True, timeout=300, encoding="utf-8")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


TOOL_RUN = []
REGISTRY_BYTES = []


def tool_run_line(i):
    if not TOOL_RUN:
        path = mr.find_registry()
        before = open(path, "rb").read()
        TOOL_RUN.extend(_tool_run("anthropic-console"))
        REGISTRY_BYTES.append(before == open(path, "rb").read())
    return TOOL_RUN[i]


CASES += [
    # `budget_posture.sidecar_available()` was deleted (e614306d8) and this tool kept calling it, so
    # every run died with an AttributeError at the posture surface -- a verification tool that
    # verified nothing. The attribute it names now must exist on the hook.
    ("every budget_posture attribute the tool names exists on the hook it imports",
     lambda: bool(_bp_named_attrs()) and _bp_missing_attrs() == []),
    ("...and the posture surface it reads is the hook's own off-quota advertisement, not something "
     "that merely exists",
     lambda: "other_quota_bits" in _bp_named_attrs()),
    # The end-to-end half: the wiring above has to survive import, reload and the live registry.
    # Whether each surface agrees is live plan state (an exhausted allowance fails `--check`), so the
    # case asserts the run completes (exit 0 agree, 1 disagree), never that every check passes.
    ("a Console plan run prints its checks to the end, instead of raising on the way in",
     lambda: tool_run_line(0) in (0, 1) and "checks passed" in tool_run_line(1)
     and "AttributeError" not in tool_run_line(2) and "Traceback" not in tool_run_line(2)
     and "posture" in tool_run_line(1)),
    # A read-only run that rewrote the file in text mode turned every LF into CRLF on Windows.
    ("...and leaves the registry's bytes unchanged",
     lambda: tool_run_line(0) is not None and REGISTRY_BYTES == [True]),
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
