#!/usr/bin/env python3
"""Proof for tools/layer_closure.py: a file never depends on a file its layer's consumers lack.

Planted trees prove each finding kind fires and each exemption holds; the live check proves every
tracked row of this project's lock closes over its own layer prefix.

    python3 .claude/tests/test_layer_closure.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / "tools"))
import layer_closure as lc  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail) if detail else ""))


def planted(files, layers, extra=()):
    """`extra` files exist on disk but ship in no layer."""
    with tempfile.TemporaryDirectory() as root:
        for rel, text in list(files.items()) + [(rel, "x\n") for rel in extra]:
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return lc.scan(Path(root), layers)


def kinds(findings):
    return sorted((f.source, f.kind, f.target) for f in findings)


def main():
    skill = ".claude/skills/testing/SKILL.md"
    cmd = ".claude/commands/regression_gate.md"
    base = {skill: "# t\n", cmd: "# g\n"}

    doc = ".claude/commands/doc.md"
    got = planted({**base, doc: "Run `/regression_gate`, load `testing`.\n"},
                  {doc: "pure", skill: "godot", cmd: "godot"})
    check("a pure doc citing godot command and skill fails twice", len(got) == 2, kinds(got))
    check("the finding names the missing layer", all(f.kind == "needs-godot" for f in got), kinds(got))

    got = planted({**base, doc: "Run `/regression_gate`.\n"}, {doc: "godot", cmd: "godot"})
    check("the same citation closes from a godot file", got == [], kinds(got))

    got = planted({doc: "Load `spell_authoring`.\n"}, {doc: "pure"},
                  extra=(".claude/skills/spell_authoring/SKILL.md",))
    check("a skill only this project carries fails as local-only",
          kinds(got) == [(doc, "local-only", "spell_authoring")], kinds(got))

    got = planted({doc: "Read `reference/absent.md`.\n"}, {doc: "pure"})
    check("a path cited but shipped by no layer fails as missing",
          kinds(got) == [(doc, "missing", "reference/absent.md")], kinds(got))

    got = planted({doc: "Plain words `testing` and `true` cite nothing here.\n"}, {doc: "pure"})
    check("a bare word naming no shipped skill is not a citation", got == [], kinds(got))

    mem = ".claude/auto-memory/feedback_local_rule.md"
    arch = ".claude/auto-memory/archive/gotcha_local_trap.md"
    got = planted({doc: "Cite `feedback_local_rule` and (gotcha_local_trap.md).\n"}, {doc: "pure"},
                  extra=(mem, arch))
    check("a memory slug this project alone carries fails as local-only, cited bare or backticked",
          kinds(got) == [(doc, "local-only", "feedback_local_rule"), (doc, "local-only", "gotcha_local_trap")],
          kinds(got))

    tool = ".claude/tools/engine_only_check.py"
    got = planted({doc: "Run `python .claude/tools/engine_only_check.py <doc> --repo <root>` first.\n",
                   tool: "x\n"}, {doc: "coding", tool: "godot"})
    check("a path inside a multi-word backticked command is a citation",
          kinds(got) == [(doc, "needs-godot", tool)], kinds(got))

    shipped_mem = ".claude/auto-memory/feedback_shared_rule.md"
    got = planted({doc: "Per feedback_shared_rule, and feedback_never_written.\n", shipped_mem: "m\n"},
                  {doc: "pure", shipped_mem: "pure"})
    check("a shipped memory slug closes; a slug naming no memory is not a citation", got == [], kinds(got))

    got = planted({doc: "Writes `logs/pre_compact.json` and `baseline.lock.json`.\n"}, {doc: "pure"})
    check("generated runtime state is exempt", got == [], kinds(got))

    got = planted({doc: "Shape: `[text](../rules/nowhere.md)` is a link.\n"}, {doc: "pure"})
    check("link syntax inside inline code is not a link", got == [], kinds(got))

    ref = ".claude/skills/a/reference/x.md"
    rule = ".claude/rules/csharp.md"
    got = planted({ref: "See [rule](../../../rules/csharp.md).\n", rule: "# r\n"},
                  {ref: "pure", rule: "godot"})
    check("a relative markdown link resolves against its own directory",
          kinds(got) == [(ref, "needs-godot", ".claude/rules/csharp.md")], kinds(got))

    tpl = ".claude/skills/a/templates/x.py"
    got = planted({ref: "Copy from [templates](../templates).\n", tpl: "x = 1\n"}, {ref: "pure", tpl: "pure"})
    check("a link to a directory resolves to the files shipped under it", got == [], kinds(got))

    hook = ".claude/hooks/dispatch.py"
    guard = ".claude/hooks/tres_guard.py"
    got = planted({hook: "import os\nimport tres_guard\n", guard: "x = 1\n"},
                  {hook: "pure", guard: "godot"})
    check("a hard import of a higher-layer module fails",
          kinds(got) == [(hook, "needs-godot", ".claude/hooks/tres_guard.py")], kinds(got))

    optional = "import os\ntry:\n    import tres_guard\nexcept ImportError:\n    tres_guard = None\n"
    got = planted({hook: optional, guard: "x = 1\n"}, {hook: "pure", guard: "godot"})
    check("an import guarded by ImportError is optional", got == [], kinds(got))

    loader = "import _optional_hooks\nHOOKS = _optional_hooks.load('tres_guard')\n"
    helper = ".claude/hooks/_optional_hooks.py"
    got = planted({hook: loader, guard: "x = 1\n", helper: "def load(*n): pass\n"},
                  {hook: "pure", guard: "godot", helper: "pure"})
    check("a name passed to the optional loader is optional", got == [], kinds(got))

    chain = 'CHAIN = (("tres_guard", "enforcement"),)\n'
    got = planted({hook: chain, guard: "x = 1\n"}, {hook: "pure", guard: "godot"})
    check("a module named by a bare string literal is a dependency",
          kinds(got) == [(hook, "needs-godot", ".claude/hooks/tres_guard.py")], kinds(got))

    got = planted({hook: "import _optional_hooks\n" + chain + "CHAIN = _optional_hooks.adopted(CHAIN)\n",
                   guard: "x = 1\n", helper: "def adopted(c): pass\n"},
                  {hook: "pure", guard: "godot", helper: "pure"})
    check("a string-named chain filtered through _optional_hooks is optional", got == [], kinds(got))

    got = planted({hook: "# mentions tres_guard.py in a comment only\n", guard: "x = 1\n"},
                  {hook: "pure", guard: "godot"})
    check("a comment mention of a module is not a dependency", got == [], kinds(got))

    sh = ".claude/scripts/run.sh"
    got = planted({sh: 'python3 "$HERE/../hooks/tres_guard.py" --x\n', guard: "x = 1\n"},
                  {sh: "pure", guard: "godot"})
    check("a script executing a higher-layer file fails",
          kinds(got) == [(sh, "needs-godot", ".claude/hooks/tres_guard.py")], kinds(got))

    got = lc.consumer_layers({"files": {doc: {"status": "tracked", "layer": "coding"}}},
                             {"files": [{"path": doc, "layer": "pure"}, {"path": skill, "layer": "pure"}]})
    check("a seed the manifest ships counts, and a lock layer overrides the pinned one",
          got == {doc: "coding", skill: "pure"}, got)

    lock_path = REPO / ".claude" / "baseline.lock.json"
    if not lock_path.exists():
        print("skip live: no consumer lock in this tree (a baseline checkout runs audit_baseline instead)")
        return report()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    manifest = lc.pinned_manifest(REPO, lock)
    if manifest is None:
        print("CANNOT-RUN: the pinned baseline manifest is unreadable from .claude/.cache/baseline-repo")
        return 2
    layers = lc.consumer_layers(lock, manifest)
    check("the consumer view holds the manifest and the lock", len(layers) > 500, len(layers))
    found = lc.scan(REPO, layers, lc.lock_checked(lock))
    check("live: every tracked row closes over its own layer", found == [],
          "\n  " + "\n  ".join(f.render() for f in found[:40]) + (f"\n  ... {len(found)} total" if len(found) > 40 else ""))

    return report()


def report():
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
