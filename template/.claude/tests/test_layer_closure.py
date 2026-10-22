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


def worktree_pinned_manifest():
    """`pinned_manifest` called on a linked worktree of a main checkout whose cache clone holds the pin."""
    import subprocess

    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        main, clone, tree = Path(tmp, "main"), Path(tmp, "main", ".claude", ".cache", "baseline-repo"), Path(tmp, "wt")
        clone.mkdir(parents=True)
        git(clone, "init", "-q")
        (clone / "baseline.manifest.json").write_text('{"files": []}', encoding="utf-8")
        git(clone, "add", "baseline.manifest.json")
        git(clone, "commit", "-q", "-m", "pin")
        sha = git(clone, "rev-parse", "HEAD")
        (main / "README").write_text("x\n", encoding="utf-8")
        git(main, "init", "-q")
        git(main, "add", "README")
        git(main, "commit", "-q", "-m", "init")
        git(main, "worktree", "add", "-q", "--detach", str(tree))
        try:
            return lc.pinned_manifest(tree, {"synced_commit": sha})
        finally:
            git(main, "worktree", "remove", "--force", str(tree))


def dependencies(files, layers, relpath):
    """`row_dependencies(root, relpath)` over a planted tree whose lock ships `layers`."""
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            path = Path(root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        lock = {"files": {rel: {"status": "tracked", "layer": layer} for rel, layer in layers.items()}}
        Path(root, ".claude", "baseline.lock.json").parent.mkdir(parents=True, exist_ok=True)
        Path(root, ".claude", "baseline.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        return lc.row_dependencies(Path(root), relpath)


def kinds(findings):
    return sorted((f.source, f.kind, f.target) for f in findings)


def main():
    row, helper, fenced = ".claude/tools/row.py", ".claude/tools/helper.py", ".claude/tools/fenced.py"
    got = dependencies({row: "import helper\nimport json\n", helper: "x = 1\n"},
                       {row: "pure", helper: "pure"}, row)
    check("row_dependencies returns the shipped module a python row imports", got == [helper], got)
    got = dependencies({row: "import helper\n", helper: "x = 1\n"}, {row: "pure"}, row)
    check("row_dependencies returns an imported module no layer ships", got == [helper], got)
    got = dependencies({row: "import json, os\n"}, {row: "pure"}, row)
    check("row_dependencies is empty for stdlib-only imports", got == [], got)
    doc_row = ".claude/commands/runs.md"
    got = dependencies({doc_row: "Run:\n```\npython3 \"$CLAUDE_PROJECT_DIR/.claude/tools/fenced.py\"\n```\n",
                        fenced: "x = 1\n"}, {doc_row: "pure", fenced: "pure"}, doc_row)
    check("row_dependencies returns the path a fenced doc block cites", got == [fenced], got)
    got = dependencies({}, {row: "pure"}, row)
    check("row_dependencies is empty for a row with no file", got == [], got)

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
    check("a memory this project alone carries is evidence, not a dependency: no finding, cited bare or backticked",
          got == [], kinds(got))
    got = planted({doc: "Read `auto-memory/feedback_gone.md`.\n"}, {doc: "pure"})
    check("a memory path no file holds still fails as missing",
          kinds(got) == [(doc, "missing", "auto-memory/feedback_gone.md")], kinds(got))

    tool = ".claude/tools/engine_only_check.py"
    got = planted({doc: "Run `python .claude/tools/engine_only_check.py <doc> --repo <root>` first.\n",
                   tool: "x\n"}, {doc: "coding", tool: "godot"})
    check("a path inside a multi-word backticked command is a citation",
          kinds(got) == [(doc, "needs-godot", tool)], kinds(got))

    sh = ".claude/scripts/lib/runner.sh"
    helper = ".claude/tools/helper.py"
    got = planted({sh: 'python3 "$ROOT/tools/helper.py"\n'}, {sh: "pure"}, extra=(helper,))
    check("a shipped script executing a file no layer ships fails as local-only",
          kinds(got) == [(sh, "local-only", "helper.py")], kinds(got))

    py = ".claude/tools/caller.py"
    got = planted({py: 'import subprocess\nsubprocess.run(["python3", "tools/helper.py"])\n'}, {py: "pure"},
                  extra=(helper,))
    check("a python string naming a file no layer ships fails as local-only",
          kinds(got) == [(py, "local-only", "helper.py")], kinds(got))

    got = planted({sh: 'python3 "$ROOT/tools/helper.py"\n', helper: "x\n"}, {sh: "pure", helper: "pure"})
    check("a shipped script executing a shipped file closes", got == [], kinds(got))

    test_py = ".claude/tests/test_caller.py"
    rows = 'ROWS = ["bash tools/helper.py --x", "see helper.py (pid 1)"]\n'
    got = planted({test_py: rows}, {test_py: "pure"}, extra=(helper,))
    check("a test's string holding whitespace is fixture input, not a file reference", got == [], kinds(got))

    got = planted({py: rows}, {py: "pure"}, extra=(helper,))
    check("a tool's command or message string naming a file no layer ships fails as local-only",
          kinds(got) == [(py, "local-only", "helper.py")], kinds(got))

    pkg_init = ".claude/scripts/pkg/__init__.py"
    got = planted({py: 'FOUND = ("__init__.py", "/__init__.py")\n'}, {py: "pure"}, extra=(pkg_init,))
    check("a package marker basename names no file", got == [], kinds(got))

    ps1 = ".claude/scripts/run.ps1"
    got = planted({ps1: "<#\n.SYNOPSIS\n  Wraps tools/helper.py.\n#>\nparam([int]$N)\n<# inline helper.py #> $x = 1\n"},
                  {ps1: "pure"}, extra=(helper,))
    check("a file named only inside a powershell block comment is not a dependency", got == [], kinds(got))

    got = planted({ps1: "$x = 1  # see tools/helper.py\n", sh: 'echo ok # runs tools/helper.py\n'},
                  {ps1: "pure", sh: "pure"}, extra=(helper,))
    check("a file named only in a trailing comment is not a dependency", got == [], kinds(got))

    got = planted({ps1: '& python3 "$PSScriptRoot/helper.py" # call it\n'}, {ps1: "pure"}, extra=(helper,))
    check("code before a trailing comment still counts",
          kinds(got) == [(ps1, "local-only", "helper.py")], kinds(got))

    shipped_mem = ".claude/auto-memory/feedback_shared_rule.md"
    got = planted({doc: "Per feedback_shared_rule, and feedback_never_written.\n", shipped_mem: "m\n"},
                  {doc: "pure", shipped_mem: "pure"})
    check("a shipped memory slug closes; a slug naming no memory is not a citation", got == [], kinds(got))

    got = planted({doc: "Writes `logs/pre_compact.json` and `baseline.lock.json`.\n"}, {doc: "pure"})
    check("generated runtime state is exempt", got == [], kinds(got))
    got = planted({doc: "Record it in `tools/load_census.project.json` or `index_composition_baseline.json`.\n"},
                  {doc: "pure"})
    check("a project data file a harness step writes is exempt", got == [], kinds(got))
    writer = ".claude/tools/writer.py"
    got = planted({writer: 'OUT = "families.md"\n'}, {writer: "pure"}, extra=(".claude/generated/families.md",))
    check("a bare data literal whose only local match is generated output is exempt", got == [], kinds(got))
    got = planted({writer: 'OUT = "families.md"\n'}, {writer: "pure"}, extra=(".claude/reference/families.md",))
    check("...while a non-generated local match still fails as local-only",
          kinds(got) == [(writer, "local-only", "families.md")], kinds(got))

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

    # 2026-10-08 (43c852757): a dispatcher that denies shell calls when its bootstrap imports fail imports the
    # loader inside a try; the chain still loads by name through it.
    guarded_loader = ("try:\n    import _optional_hooks\nexcept Exception:\n    raise SystemExit(2)\n"
                      + chain + "CHAIN = _optional_hooks.adopted(CHAIN)\n")
    got = planted({hook: guarded_loader, guard: "x = 1\n", helper: "def adopted(c): pass\n"},
                  {hook: "pure", guard: "godot", helper: "pure"})
    check("a string-named chain stays optional when the loader import is guarded", got == [], kinds(got))

    got = planted({hook: "# mentions tres_guard.py in a comment only\n", guard: "x = 1\n"},
                  {hook: "pure", guard: "godot"})
    check("a comment mention of a module is not a dependency", got == [], kinds(got))

    sh = ".claude/scripts/run.sh"
    got = planted({sh: 'python3 "$HERE/../hooks/tres_guard.py" --x\n', guard: "x = 1\n"},
                  {sh: "pure", guard: "godot"})
    check("a script executing a higher-layer file fails",
          kinds(got) == [(sh, "needs-godot", ".claude/hooks/tres_guard.py")], kinds(got))

    x_hook = ".claude/hooks/x.py"
    got = planted({doc: '```\npython3 "$CLAUDE_PROJECT_DIR/.claude/hooks/x.py"\n```\n', x_hook: "x = 1\n"},
                  {doc: "pure", x_hook: "godot"})
    check("a fenced $CLAUDE_PROJECT_DIR/.claude path to a higher-layer file fails",
          kinds(got) == [(doc, "needs-godot", x_hook)], kinds(got))

    got = planted({doc: "```\n$ROOT/.claude/hooks/x.py\n```\n", x_hook: "x = 1\n"}, {doc: "pure", x_hook: "godot"})
    check("a fenced $ROOT/.claude path to a higher-layer file fails",
          kinds(got) == [(doc, "needs-godot", x_hook)], kinds(got))

    got = planted({doc: "Run `python3 $CLAUDE_PROJECT_DIR/.claude/hooks/x.py` first.\n", x_hook: "x = 1\n"},
                  {doc: "pure", x_hook: "godot"})
    check("an inline $CLAUDE_PROJECT_DIR/.claude span to a higher-layer file fails",
          kinds(got) == [(doc, "needs-godot", x_hook)], kinds(got))

    got = planted({doc: '```\npython3 "$CLAUDE_PROJECT_DIR/.claude/hooks/x.py"\n```\n', x_hook: "x = 1\n"},
                  {doc: "pure", x_hook: "pure"})
    check("a fenced path in the doc's own layer closes", got == [], kinds(got))

    got = planted({doc: '```\npython3 "$HOME/.claude/hooks/x.py"\n```\n', x_hook: "x = 1\n"},
                  {doc: "pure", x_hook: "godot"})
    check("a fenced $HOME path is not a project reference", got == [], kinds(got))

    got = planted({doc: '```\npython3 "$ROOT/tools/x.py"\n```\n', x_hook: "x = 1\n"},
                  {doc: "pure", x_hook: "godot"})
    check("a fenced path outside .claude/ is not a reference", got == [], kinds(got))

    registry = ".claude/tools/lens_registry.py"
    agents = ".claude/commands/agents/scene_agents.md"
    got = planted({registry: 'REGISTRIES = {"structure": ("scene_agents.md", "stra-")}\n', agents: "# a\n"},
                  {registry: "pure", agents: "godot"})
    check("a python string literal naming a higher-layer .md by basename fails",
          kinds(got) == [(registry, "needs-godot", agents)], kinds(got))

    got = planted({registry: 'ROOT = "commands/agents/scene_agents.md"\n', agents: "# a\n"},
                  {registry: "pure", agents: "godot"})
    check("a python string literal naming a higher-layer .md by path fails",
          kinds(got) == [(registry, "needs-godot", agents)], kinds(got))

    seed = ".claude/skills/project_subsystems/adaptation.godot.json"
    got = planted({registry: 'SEED = "adaptation.godot.json"\n', seed: "{}\n"},
                  {registry: "pure", seed: "godot"})
    check("a python string literal naming a higher-layer .json fails",
          kinds(got) == [(registry, "needs-godot", seed)], kinds(got))

    got = planted({registry: 'REGISTRIES = {"structure": ("scene_agents.md", "stra-")}\n', agents: "# a\n"},
                  {registry: "godot", agents: "godot"})
    check("a .md string literal whose target is in the same layer closes", got == [], kinds(got))

    got = planted({registry: 'DOC = "scene_agents.md"\n', agents: "# a\n"}, {registry: "godot", agents: "pure"})
    check("a .md string literal whose target is a lower layer closes", got == [], kinds(got))

    got = planted({registry: '"""Reads scene_agents.md and adaptation.godot.json."""\nMSG = "see scene_agents.md for the lens list"\n',
                   agents: "# a\n", seed: "{}\n"},
                  {registry: "pure", agents: "godot", seed: "godot"})
    check("a docstring mention and a sentence holding a file name are prose, not dependencies",
          got == [], kinds(got))

    got = planted({registry: 'OUT = ("results.json", "notes.md")\n'}, {registry: "pure"})
    check("a .md/.json string naming no file anywhere is not a dependency", got == [], kinds(got))

    got = planted({registry: 'REGISTRIES = ("scene_agents.md",)\n'}, {registry: "pure"}, extra=(agents,))
    check("a .md string naming a file no layer ships fails as local-only",
          kinds(got) == [(registry, "local-only", "scene_agents.md")], kinds(got))

    got = planted({test_py: 'PLAN = "scene_agents.md"\nROW = "commands/agents/scene_agents.md"\nGONE = "rules/absent.md"\n',
                   agents: "# a\n"}, {test_py: "pure", agents: "godot"})
    check("a test's .md literal is fixture data, not a dependency", got == [], kinds(got))

    optional = ".claude/tools/lens.py"
    got = planted({optional: 'REGISTRIES = ("scene_agents.md",)\n', agents: "# a\n"},
                  {optional: "pure", agents: "godot"})
    check("a source the OPTIONAL_DATA table lists tolerates its named files absent", got == [], kinds(got))

    other = ".claude/tools/lens_briefs.py"
    got = planted({other: 'REGISTRIES = ("scene_agents.md",)\n', agents: "# a\n"}, {other: "pure", agents: "godot"})
    check("a source OPTIONAL_DATA names for other files still fails on this one",
          kinds(got) == [(other, "needs-godot", agents)], kinds(got))

    got = planted({registry: 'HOME = ".claude/plugins/installed_plugins.json"\nFIX = ".claude/__selftest__/a.md"\n'},
                  {registry: "pure"})
    check("a home-plugin registry path and a dunder-directory fixture path are not dependencies",
          got == [], kinds(got))

    scene_rule = ".claude/rules/scene_authoring.md"
    lens_doc = ".claude/commands/agents/plan_agents.md"
    header = "### plc-x (audit) — code\n\nmandates: plc-x\n\npreload: rules/scene_authoring.md\npreload: reference/rules/design_litmus.md\n\n```\nbody\n```\n"
    litmus = ".claude/reference/rules/design_litmus.md"
    got = planted({lens_doc: header, scene_rule: "# r\n", litmus: "# l\n"},
                  {lens_doc: "coding", scene_rule: "godot", litmus: "coding"})
    check("a markdown preload line naming a higher-layer file fails, one naming a same-layer file closes",
          kinds(got) == [(lens_doc, "needs-godot", scene_rule)], kinds(got))

    got = planted({lens_doc: header, scene_rule: "# r\n", litmus: "# l\n"},
                  {lens_doc: "godot", scene_rule: "godot", litmus: "coding"})
    check("the same preload closes from a godot file", got == [], kinds(got))

    got = planted({lens_doc: "preload: rules/nowhere.md\n"}, {lens_doc: "coding"})
    check("a preload line naming a file nothing holds fails as missing",
          kinds(got) == [(lens_doc, "missing", "rules/nowhere.md")], kinds(got))

    got = planted({lens_doc: "A prose sentence about a preload: rules/scene_authoring.md in passing.\n",
                   scene_rule: "# r\n"}, {lens_doc: "coding", scene_rule: "godot"})
    check("a preload mention mid-sentence is prose, not a header line", got == [], kinds(got))

    got = lc.consumer_layers({"files": {doc: {"status": "tracked", "layer": "coding"}}},
                             {"files": [{"path": doc, "layer": "pure"}, {"path": skill, "layer": "pure"}]})
    check("a seed the manifest ships counts, and a lock layer overrides the pinned one",
          got == {doc: "coding", skill: "pure"}, got)

    got = worktree_pinned_manifest()
    check("a linked worktree with no cache clone reads the pin from its main checkout's clone",
          got == {"files": []}, got)

    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in ((hook, "import tres_guard\n"), (guard, "x = 1\n")):
            Path(tmp, rel).parent.mkdir(parents=True, exist_ok=True)
            Path(tmp, rel).write_text(text, encoding="utf-8")
        lock = {"files": {hook: {"status": "tracked", "layer": "pure"}, guard: {"status": "tracked", "layer": "godot"}}}
        got = lc.lock_findings(Path(tmp), lock, {"files": []})
    check("lock_findings scans the lock's tracked rows over the consumer view",
          [f.render() for f in got] == [f"{hook}: needs-godot {guard}"], [f.render() for f in got])

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
    found = lc.lock_findings(REPO, lock, manifest)
    check("live: every tracked row closes over its own layer", found == [],
          "\n  " + "\n  ".join(f.render() for f in found[:40]) + (f"\n  ... {len(found)} total" if len(found) > 40 else ""))

    return report()


def report():
    if FAILURES:
        for failure in FAILURES:
            print("\n" + failure)
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
