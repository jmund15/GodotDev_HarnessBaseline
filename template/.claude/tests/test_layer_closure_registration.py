#!/usr/bin/env python3
"""Proof for tools/layer_closure.py: every shipped hook is registered by a shipped settings file of its
layer or lower, chained by a registered dispatcher, imported by a reachable hook, or a CLI tool.

    python3 .claude/tests/test_layer_closure_registration.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import layer_closure as lc  # noqa: E402

FAILURES = []
H = ".claude/hooks/"
HOOK = "import sys\npayload = sys.stdin.read()\n"


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail) if detail else ""))


def settings(*names):
    cmds = [{"hooks": [{"type": "command", "command": f'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/{n}.py"'}]}
            for n in names]
    return json.dumps({"hooks": {"PreToolUse": cmds}})


def planted(files, layers):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return [f.render() for f in lc.scan(Path(root), layers) if f.kind == "unregistered"]


def main():
    base = ".claude/settings.base.json"
    coding = ".claude/settings.coding.json"

    out = planted({H + "lonely.py": HOOK, base: settings("other")}, {H + "lonely.py": "pure", base: "pure"})
    check("unregistered hook is a finding naming hook, layer and settings searched",
          len(out) == 1 and out[0].startswith(H + "lonely.py: unregistered") and "pure" in out[0]
          and "settings.base.json" in out[0], out)

    out = planted({H + "a.py": HOOK, base: settings("a")}, {H + "a.py": "pure", base: "pure"})
    check("hook registered in its own layer's settings has no finding", out == [], out)

    out = planted({H + "a.py": HOOK, base: settings("a")}, {H + "a.py": "coding", base: "pure"})
    check("hook registered in a lower-layer settings file has no finding", out == [], out)

    out = planted({H + "a.py": HOOK, coding: settings("a")}, {H + "a.py": "pure", coding: "coding"})
    check("hook registered only in a higher-layer settings file is a finding", len(out) == 1, out)

    disp = "CHAIN = (('member', 5), ('member2', 5))\n" + HOOK
    files = {H + "disp.py": disp, H + "member.py": HOOK, H + "member2.py": HOOK, base: settings("disp")}
    layers = {H + "disp.py": "pure", H + "member.py": "coding", H + "member2.py": "pure", base: "pure"}
    check("members of a registered dispatcher have no finding", planted(files, layers) == [], planted(files, layers))

    files[base] = settings()
    out = planted(files, layers)
    check("an unregistered dispatcher and its members are all findings", len(out) == 3, out)

    files = {H + "a.py": HOOK + "import helper_hook\n", H + "helper_hook.py": HOOK, base: settings("a")}
    layers = {H + "a.py": "pure", H + "helper_hook.py": "pure", base: "pure"}
    check("a hook imported by a registered hook has no finding", planted(files, layers) == [], planted(files, layers))

    files = {H + "a.py": HOOK + "import b\n", H + "b.py": HOOK + "import a\n", base: settings()}
    layers = {H + "a.py": "pure", H + "b.py": "pure", base: "pure"}
    check("hooks importing only each other stay findings", len(planted(files, layers)) == 2, planted(files, layers))

    out = planted({H + "_lib.py": HOOK, base: settings()}, {H + "_lib.py": "pure", base: "pure"})
    check("a _ library has no finding", out == [], out)

    out = planted({H + "tool.py": "import sys\nprint(sys.argv)\n", base: settings()},
                  {H + "tool.py": "pure", base: "pure"})
    check("a CLI tool with no stdin payload handling has no finding", out == [], out)

    out = planted({".claude/tools/t.py": HOOK, base: settings()}, {".claude/tools/t.py": "pure", base: "pure"})
    check("a non-hook script has no finding", out == [], out)

    launch = ".claude/scripts/launch.sh"
    arm = "python3 {root}/hooks/a.py {workdir}\n"
    out = planted({H + "a.py": HOOK, launch: arm}, {H + "a.py": "coding", launch: "coding"})
    check("hook armed by a same-layer shipped script has no finding", out == [], out)

    out = planted({H + "a.py": HOOK, launch: arm}, {H + "a.py": "coding", launch: "pure"})
    check("hook armed by a lower-layer shipped script has no finding", out == [], out)

    out = planted({H + "a.py": HOOK, launch: arm}, {H + "a.py": "pure", launch: "coding"})
    check("hook armed only by a higher-layer shipped script is a finding", len(out) == 1, out)

    out = planted({H + "a.py": HOOK, launch: 'BUDGET_HOOK="$SC_ROOT/hooks/a.py"\n'},
                  {H + "a.py": "pure", launch: "pure"})
    check("hook path assigned in a shipped script but never invoked is a finding", len(out) == 1, out)

    doc = ".claude/reference/sidecar_dispatch.md"
    out = planted({H + "a.py": HOOK, doc: "the hooks/a.py guard"}, {H + "a.py": "pure", doc: "pure"})
    check("hook named only in a non-script file is a finding", len(out) == 1, out)

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
