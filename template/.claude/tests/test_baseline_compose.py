#!/usr/bin/env python3
"""Re-runnable S8 proof for `baseline_compose.py` and the `compose` operation.

    python3 .claude/tests/test_baseline_compose.py

Every case here uses a small, controlled fixture: the merge rules are exercised at a scale where
"composition equals a hand-authored settings.json" is actually achievable under the documented
shape (base entries first, then project extras -- see the S8 report's disclosed limitation for
why a consumer whose settings.json has drifted far from base cannot reach byte equality that way).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
COMPOSE_PATH = _TOOLS_DIR / "baseline_compose.py"
SYNC_PATH = _TOOLS_DIR / "baseline_sync.py"


def _load(path: Path, name: str):
    if not path.exists():
        print(f"CANNOT-RUN: {path} is missing", file=sys.stderr)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        print(f"CANNOT-RUN: {path} could not be loaded", file=sys.stderr)
        sys.exit(2)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - report, don't hide, an import crash
        print(f"CANNOT-RUN: {path} failed to import: {exc}", file=sys.stderr)
        sys.exit(2)
    return module


bc = _load(COMPOSE_PATH, "baseline_compose_under_test")


def case(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    return None if ok else f"{label}: {detail}"


def _rmtree_writable(path: Path) -> None:
    def onerror(func, p, exc_info):
        try:
            Path(p).chmod(stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


def make_tree(hooks=(), commands=(), skills=()):
    """A temp `.claude` tree with just the files a prune check needs to stat."""
    root = Path(tempfile.mkdtemp(prefix="bcompose_"))
    claude = root / ".claude"
    (claude / "hooks").mkdir(parents=True)
    (claude / "commands").mkdir(parents=True)
    (claude / "skills").mkdir(parents=True)
    for h in hooks:
        (claude / "hooks" / h).write_text("# hook\n", encoding="utf-8")
    for c in commands:
        (claude / "commands" / f"{c}.md").write_text("# cmd\n", encoding="utf-8")
    for s in skills:
        (claude / "skills" / s).mkdir(parents=True, exist_ok=True)
    return root, claude / "hooks"


def main() -> int:
    failures = []

    # 1. Small equality fixture: base entries first, then project extras, exactly reproduces a
    #    hand-authored settings.json shaped that way -- hook group order, matchers, dispatcher
    #    order, every permission list, and env.
    root, hooks_dir = make_tree(hooks=["a.py", "b.py"])
    base = {
        "env": {"X": "1", "Y": "2"},
        "permissions": {"allow": ["A", "B"], "deny": ["D1"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/a.py\""}]}]},
    }
    project = {
        "env": {"Y": "3", "Z": "4"},
        "permissions": {"allow": ["C"], "deny": []},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/b.py\""}]}]},
    }
    merged = bc.compose_settings(base, project, hooks_dir, ["pure", "coding", "godot"])
    expected = {
        "env": {"X": "1", "Y": "3", "Z": "4"},
        "permissions": {"allow": ["A", "B", "C"], "deny": ["D1"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/a.py\""},
            {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/b.py\""},
        ]}]},
    }
    failures.append(case("equality fixture: env/allow/deny/hook order", merged == expected,
                          json.dumps(merged, sort_keys=True)))
    _rmtree_writable(root)

    # 2. $disable.hooks removes exactly the named base command, never a project-added one sharing
    #    the same matcher.
    root, hooks_dir = make_tree(hooks=["a.py", "b.py"])
    base = {"permissions": {"allow": []},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/a.py\""}]}]}}
    project = {"permissions": {}, "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/b.py\""}]}]},
        "$disable": {"hooks": ["python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/a.py\""]}}
    merged = bc.compose_settings(base, project, hooks_dir, ["pure"])
    only_b = merged["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/b.py\""}]}]
    failures.append(case("$disable.hooks removes the named base command only", only_b,
                          json.dumps(merged["hooks"])))
    _rmtree_writable(root)

    # 2b. $disable.hooks naming a command not present exactly once in base -> ComposeError.
    root, hooks_dir = make_tree(hooks=["a.py"])
    base = {"permissions": {"allow": []}, "hooks": {}}
    project = {"permissions": {}, "hooks": {}, "$disable": {"hooks": ["nope"]}}
    try:
        bc.compose_settings(base, project, hooks_dir, ["pure"])
        failures.append(case("$disable.hooks unmatched entry -> ComposeError", False, "no raise"))
    except bc.ComposeError:
        failures.append(case("$disable.hooks unmatched entry -> ComposeError", True))
    _rmtree_writable(root)

    # 3. $disable.permissions.deny removes exactly the named base entry.
    root, hooks_dir = make_tree()
    base = {"permissions": {"allow": [], "deny": ["Glob(.claude/worktrees/**)", "Read(.claude/worktrees/**)"]}, "hooks": {}}
    project = {"permissions": {}, "hooks": {},
               "$disable": {"permissions.deny": ["Glob(.claude/worktrees/**)"]}}
    merged = bc.compose_settings(base, project, hooks_dir, ["pure"])
    failures.append(case("$disable.permissions.deny removes the named base entry",
                          merged["permissions"]["deny"] == ["Read(.claude/worktrees/**)"],
                          json.dumps(merged["permissions"])))
    _rmtree_writable(root)

    # 4. Prune: a hook entry whose `.claude/hooks/<file>` does not exist is dropped, and an empty
    #    group disappears.
    root, hooks_dir = make_tree(hooks=["a.py"])  # "missing.py" deliberately absent
    base = {"permissions": {"allow": []}, "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/missing.py\""}]}]}}
    project = {"permissions": {}, "hooks": {}}
    merged = bc.compose_settings(base, project, hooks_dir, ["pure"])
    failures.append(case("prune: missing hook script drops the entry and the empty group",
                          merged["hooks"] == {}, json.dumps(merged["hooks"])))
    _rmtree_writable(root)

    # 5. Prune: a `Skill(<name>)` allow entry whose command and skill are both absent is dropped;
    #    one whose skill folder exists survives.
    root, hooks_dir = make_tree(skills=["present_skill"])
    base = {"permissions": {"allow": ["Skill(present_skill)", "Skill(absent_skill)"]}, "hooks": {}}
    project = {"permissions": {}, "hooks": {}}
    merged = bc.compose_settings(base, project, hooks_dir, ["pure"])
    failures.append(case("prune: Skill() absent command+skill dropped, present skill kept",
                          merged["permissions"]["allow"] == ["Skill(present_skill)"],
                          json.dumps(merged["permissions"])))
    _rmtree_writable(root)

    # 6. Prune: a godot-stack permission entry is dropped when `godot` is not in the profile, and
    #    kept when it is.
    root, hooks_dir = make_tree()
    base = {"permissions": {"allow": ["Bash(dotnet build:*)", "Bash(git status:*)"]}, "hooks": {}}
    project = {"permissions": {}, "hooks": {}}
    merged_no_godot = bc.compose_settings(base, project, hooks_dir, ["pure", "coding"])
    merged_godot = bc.compose_settings(base, project, hooks_dir, ["pure", "coding", "godot"])
    failures.append(case("prune: godot permission dropped without the godot layer",
                          merged_no_godot["permissions"]["allow"] == ["Bash(git status:*)"],
                          json.dumps(merged_no_godot["permissions"])))
    failures.append(case("prune: godot permission kept with the godot layer",
                          merged_godot["permissions"]["allow"] == ["Bash(dotnet build:*)", "Bash(git status:*)"],
                          json.dumps(merged_godot["permissions"])))
    _rmtree_writable(root)

    # 7. `canonical_json` is sorted-key, 2-space-indent, LF-terminated, and stable regardless of
    #    input key order.
    a = bc.canonical_json({"b": 1, "a": 2})
    b = bc.canonical_json({"a": 2, "b": 1})
    failures.append(case("canonical_json is key-order independent", a == b, repr((a, b))))
    failures.append(case("canonical_json has no CR bytes", b"\r" not in a, repr(a)))

    # 8. `render_memory_domains`: the marker is replaced by a table built from the rows, and a
    #    missing/duplicated marker is a ComposeError.
    base_text = "# Memory Domain Table\n\n<!-- memory-domains-table -->\n\nTail.\n"
    domains = [{"name": "Testing", "triggers": ["unit-test"], "memory_keywords": [], "skills": ["testing"], "rules": []}]
    rendered = bc.render_memory_domains(base_text, domains)
    ok = ("<!-- memory-domains-table -->" not in rendered
          and "| Testing | unit-test |" in rendered
          and rendered.startswith("# Memory Domain Table")
          and rendered.endswith("Tail.\n"))
    failures.append(case("render_memory_domains replaces the marker with a row", ok, rendered))
    try:
        bc.render_memory_domains("no marker here", domains)
        failures.append(case("render_memory_domains: missing marker -> ComposeError", False, "no raise"))
    except bc.ComposeError:
        failures.append(case("render_memory_domains: missing marker -> ComposeError", True))
    try:
        bc.render_memory_domains(base_text + "<!-- memory-domains-table -->\n", domains)
        failures.append(case("render_memory_domains: duplicated marker -> ComposeError", False, "no raise"))
    except bc.ComposeError:
        failures.append(case("render_memory_domains: duplicated marker -> ComposeError", True))

    # 9. Through the `baseline_sync.py compose` CLI: a planted direct edit to the composed
    #    `settings.json` fails `compose --check`; a fresh `compose` then reconciles it, and a
    #    repeat prints "no change".
    root2 = Path(tempfile.mkdtemp(prefix="bcompose_cli_"))
    claude2 = root2 / ".claude"
    (claude2 / "hooks").mkdir(parents=True)
    (claude2 / "tools").mkdir(parents=True)
    for name in ("baseline_sync.py", "baseline_identity.py", "baseline_compose.py", "adaptation.py"):
        shutil.copy(_TOOLS_DIR / name, claude2 / "tools" / name)
    base_settings = {"permissions": {"allow": ["A"]}, "hooks": {}}
    project_settings = {"permissions": {"allow": ["B"]}, "hooks": {}}
    (claude2 / "settings.base.json").write_text(json.dumps(base_settings), encoding="utf-8")
    (claude2 / "settings.project.json").write_text(json.dumps(project_settings), encoding="utf-8")
    (claude2 / "reference").mkdir(parents=True)
    (claude2 / "skills" / "project_subsystems").mkdir(parents=True)
    (claude2 / "reference" / "memory_domains.base.md").write_text(
        "# Memory Domain Table\n\n<!-- memory-domains-table -->\n", encoding="utf-8")
    (claude2 / "skills" / "project_subsystems" / "adaptation.json").write_text(
        json.dumps({"memory_domains": [{"name": "Testing", "triggers": ["unit-test"]}]}), encoding="utf-8")
    (claude2 / "skills" / "project_subsystems" / "SKILL.md").write_text(
        "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: fixture-subsystem\n    paths: [fixture]\n```\n",
        encoding="utf-8")
    lock = {
        "schema": 2, "profile": "pure", "substitutions": {}, "baseline_repo": "https://example.invalid/x.git",
        "baseline_ref": "main", "synced_commit": "0" * 40, "identity": {"abbreviations": []},
        "files": {
            ".claude/settings.base.json": {"status": "tracked", "layer": "pure", "hash": None,
                                            "judged": {"sha": None, "verdict": "push", "at": "x", "borderline": False, "confirmed_at": None}},
            ".claude/settings.project.json": {"status": "local", "layer": "pure", "judged": None},
            ".claude/settings.json": {"status": "composed", "layer": "pure", "hash": None,
                                       "inputs": [".claude/settings.base.json", ".claude/settings.project.json"], "judged": None},
            ".claude/reference/memory_domains.base.md": {"status": "tracked", "layer": "pure", "hash": None,
                                                           "judged": {"sha": None, "verdict": "push", "at": "x", "borderline": False, "confirmed_at": None}},
            ".claude/skills/project_subsystems/adaptation.json": {"status": "local", "layer": "pure", "judged": None},
            ".claude/reference/memory_domains.md": {"status": "composed", "layer": "pure", "hash": None,
                                                      "inputs": [".claude/reference/memory_domains.base.md",
                                                                 ".claude/skills/project_subsystems/adaptation.json"], "judged": None},
        },
    }
    (claude2 / "baseline.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")

    def run_compose(args):
        return subprocess.run(
            [sys.executable, str(claude2 / "tools" / "baseline_sync.py"), "compose"] + args,
            cwd=str(root2), capture_output=True, text=True, timeout=30,
        )

    r_check_before = run_compose(["--check"])
    failures.append(case("compose --check exits 1 before the file exists",
                          r_check_before.returncode == 1, r_check_before.stdout + r_check_before.stderr))
    r_write = run_compose([])
    failures.append(case("compose (no --check) writes settings.json",
                          r_write.returncode == 0 and (claude2 / "settings.json").is_file(),
                          r_write.stdout + r_write.stderr))
    r_check_after = run_compose(["--check"])
    failures.append(case("compose --check exits 0 once composed",
                          r_check_after.returncode == 0, r_check_after.stdout + r_check_after.stderr))
    r_repeat = run_compose([])
    failures.append(case("a repeat compose prints no change",
                          r_repeat.returncode == 0 and "no change" in r_repeat.stdout,
                          r_repeat.stdout + r_repeat.stderr))
    # Planted direct edit to the composed file -> compose --check fails.
    (claude2 / "settings.json").write_text('{\n  "permissions": {\n    "allow": [\n      "TAMPERED"\n    ]\n  }\n}\n', encoding="utf-8")
    r_check_tampered = run_compose(["--check"])
    failures.append(case("a planted direct edit to settings.json fails compose --check",
                          r_check_tampered.returncode == 1, r_check_tampered.stdout + r_check_tampered.stderr))
    run_compose([])  # reconcile settings.json and compose memory_domains.md before tampering it
    (claude2 / "reference" / "memory_domains.md").write_text("TAMPERED\n", encoding="utf-8")
    r_check_md_tampered = run_compose(["--check"])
    failures.append(case("a planted direct edit to memory_domains.md fails compose --check",
                          r_check_md_tampered.returncode == 1, r_check_md_tampered.stdout + r_check_md_tampered.stderr))
    _rmtree_writable(root2)

    # 10. S8 review item 1: a composed row with `hash: null` (the `migrate` state) whose on-disk
    #     file ALREADY equals its composition must still get its hash recorded on `compose` — not
    #     stay null forever behind a "no change" print. A repeat then genuinely prints "no change".
    root3 = Path(tempfile.mkdtemp(prefix="bcompose_nullhash_"))
    claude3 = root3 / ".claude"
    (claude3 / "hooks").mkdir(parents=True)
    (claude3 / "tools").mkdir(parents=True)
    for name in ("baseline_sync.py", "baseline_identity.py", "baseline_compose.py", "adaptation.py"):
        shutil.copy(_TOOLS_DIR / name, claude3 / "tools" / name)
    base_settings = {"permissions": {"allow": ["A"]}, "hooks": {}}
    project_settings = {"permissions": {"allow": ["B"]}, "hooks": {}}
    (claude3 / "settings.base.json").write_text(json.dumps(base_settings), encoding="utf-8")
    (claude3 / "settings.project.json").write_text(json.dumps(project_settings), encoding="utf-8")
    # The composed file is pre-written to already equal the composition (as if `compose` had run
    # before `migrate` reset `hash` to null) -- v2_compose must not treat "file already correct" as
    # a no-op with respect to the lock row.
    already_composed = bc.canonical_json(bc.compose_settings(base_settings, project_settings, claude3 / "hooks", ["pure"]))
    (claude3 / "settings.json").write_bytes(already_composed)
    (claude3 / "skills" / "project_subsystems").mkdir(parents=True)
    (claude3 / "skills" / "project_subsystems" / "adaptation.json").write_text("{}\n", encoding="utf-8")
    (claude3 / "skills" / "project_subsystems" / "SKILL.md").write_text(
        "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: fixture-subsystem\n    paths: [fixture]\n```\n",
        encoding="utf-8")
    lock3 = {
        "schema": 2, "profile": "pure", "substitutions": {}, "baseline_repo": "https://example.invalid/x.git",
        "baseline_ref": "main", "synced_commit": "0" * 40, "identity": {"abbreviations": []},
        "files": {
            ".claude/settings.base.json": {"status": "tracked", "layer": "pure", "hash": None,
                                            "judged": {"sha": None, "verdict": "push", "at": "x", "borderline": False, "confirmed_at": None}},
            ".claude/settings.project.json": {"status": "local", "layer": "pure", "judged": None},
            ".claude/settings.json": {"status": "composed", "layer": "pure", "hash": None,
                                       "inputs": [".claude/settings.base.json", ".claude/settings.project.json"], "judged": None},
        },
    }
    (claude3 / "baseline.lock.json").write_text(json.dumps(lock3, indent=2) + "\n", encoding="utf-8", newline="\n")

    def run_compose3(args):
        return subprocess.run(
            [sys.executable, str(claude3 / "tools" / "baseline_sync.py"), "compose"] + args,
            cwd=str(root3), capture_output=True, text=True, timeout=30,
        )

    r_first = run_compose3([])
    lock3_after_first = json.loads((claude3 / "baseline.lock.json").read_text(encoding="utf-8"))
    hash_after_first = lock3_after_first["files"][".claude/settings.json"]["hash"]
    failures.append(case("compose backfills hash for an already-matching file with hash:null",
                          r_first.returncode == 0 and hash_after_first is not None,
                          f"stdout={r_first.stdout!r} stderr={r_first.stderr!r} hash={hash_after_first!r}"))
    r_second = run_compose3([])
    failures.append(case("a repeat compose after the backfill prints no change",
                          r_second.returncode == 0 and "no change" in r_second.stdout,
                          r_second.stdout + r_second.stderr))
    _rmtree_writable(root3)

    # 11. S8 review item 2: `settings_equivalent` judges by behavior, not bytes -- permission lists
    #     as sets, hook entries as a (matcher, entry) multiset regardless of grouping, every other
    #     top-level key deep-equal.
    a = {
        "env": {"X": "1"},
        "permissions": {"allow": ["A", "B"], "deny": ["D1"], "ask": []},
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "one"}]},
            {"matcher": "Write", "hooks": [{"type": "command", "command": "two"}]},
        ]},
    }
    # Reordered permissions + regrouped hooks (same (matcher, entry) pairs, different group shape).
    b_reordered = {
        "env": {"X": "1"},
        "permissions": {"allow": ["B", "A"], "deny": ["D1"], "ask": []},
        "hooks": {"PreToolUse": [
            {"matcher": "Write", "hooks": [{"type": "command", "command": "two"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "one"}]},
        ]},
    }
    ok, diffs = bc.settings_equivalent(a, b_reordered)
    failures.append(case("settings_equivalent: reordered permissions + regrouped hooks are equivalent",
                          ok, repr(diffs)))

    # A dropped permission is not equivalent.
    b_dropped_perm = json.loads(json.dumps(a))
    b_dropped_perm["permissions"]["allow"] = ["A"]
    ok, diffs = bc.settings_equivalent(a, b_dropped_perm)
    failures.append(case("settings_equivalent: a dropped permission is not equivalent",
                          not ok and diffs, repr(diffs)))

    # A changed hook command is not equivalent.
    b_changed_cmd = json.loads(json.dumps(a))
    b_changed_cmd["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "one-changed"
    ok, diffs = bc.settings_equivalent(a, b_changed_cmd)
    failures.append(case("settings_equivalent: a changed hook command is not equivalent",
                          not ok and diffs, repr(diffs)))

    # A moved matcher (same command, different matcher) is not equivalent.
    b_moved_matcher = json.loads(json.dumps(a))
    b_moved_matcher["hooks"]["PreToolUse"][0]["matcher"] = "Read"
    ok, diffs = bc.settings_equivalent(a, b_moved_matcher)
    failures.append(case("settings_equivalent: a moved matcher is not equivalent",
                          not ok and diffs, repr(diffs)))

    # A changed env value is not equivalent (every other top-level key is deep-equal).
    b_changed_env = json.loads(json.dumps(a))
    b_changed_env["env"]["X"] = "2"
    ok, diffs = bc.settings_equivalent(a, b_changed_env)
    failures.append(case("settings_equivalent: a changed env value is not equivalent",
                          not ok and diffs, repr(diffs)))

    # derive_project_settings is compose's inverse for a monolithic v1 settings.json: what base
    # already provides is dropped, including a hook base now runs inside a dispatcher.
    root, hooks_dir = make_tree(hooks=["dispatch.py", "absorbed.py", "mine.py"])
    dispatch = "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/dispatch.py\""
    base = {
        "env": {"PYTHONUTF8": "1", "SHARED": "same"},
        "permissions": {"allow": ["A", "B"], "deny": ["D1"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": dispatch}]}]},
    }
    full = {
        "env": {"SHARED": "same", "MINE": "x"},
        "permissions": {"allow": ["A", "C"], "deny": ["D1", "D2"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": dispatch},
            {"type": "command", "command": "python .claude/hooks/absorbed.py"},
            {"type": "command", "command": "python .claude/hooks/mine.py"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "python .claude/hooks/mine.py --stop"}]}]},
        "enabledPlugins": {"p": True},
    }
    owned = {"dispatch.py", "absorbed.py"}
    project = bc.derive_project_settings(base, full, owned)
    merged = bc.compose_settings(base, project, hooks_dir, ["pure", "coding", "godot"])
    commands = [h["command"] for groups in merged["hooks"].values() for g in groups for h in g["hooks"]]
    failures.append(case("derive: project keeps only what base lacks",
                          project.get("env") == {"MINE": "x"}
                          and project["permissions"] == {"allow": ["C"], "deny": ["D2"]}
                          and project.get("enabledPlugins") == {"p": True},
                          json.dumps(project, sort_keys=True)))
    failures.append(case("derive: a dispatcher-owned hook is dropped, a project hook kept",
                          not any("absorbed.py" in c for c in commands)
                          and "python .claude/hooks/mine.py" in commands
                          and "python .claude/hooks/mine.py --stop" in commands,
                          repr(commands)))
    failures.append(case("derive then compose: no base hook appears twice",
                          commands.count(dispatch) == 1, repr(commands)))
    failures.append(case("derive then compose: every project permission survives",
                          all(p in merged["permissions"]["allow"] for p in ["A", "B", "C"])
                          and merged["permissions"]["deny"] == ["D1", "D2"],
                          json.dumps(merged["permissions"])))
    _rmtree_writable(root)

    # Non-list permission keys (defaultMode, additionalDirectories) survive derive and compose.
    root, hooks_dir = make_tree()
    base = {"permissions": {"allow": ["A"]}}
    full = {"permissions": {"allow": ["A"], "defaultMode": "acceptEdits", "additionalDirectories": ["../x"]}}
    project = bc.derive_project_settings(base, full, set())
    merged = bc.compose_settings(base, project, hooks_dir, ["pure"])
    failures.append(case("derive+compose: non-list permission keys survive",
                          merged["permissions"].get("defaultMode") == "acceptEdits"
                          and merged["permissions"].get("additionalDirectories") == ["../x"],
                          json.dumps(merged["permissions"])))
    adopted = bc.base_only_entries({"env": {"K": "1"}, "permissions": {"allow": ["A", "B"]},
                                    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}},
                                   {"permissions": {"allow": ["A"]}})
    failures.append(case("base_only_entries names what compose adds that full lacked",
                          adopted == ["permissions.allow B", "hook Stop: x", "env K"], repr(adopted)))
    _rmtree_writable(root)

    total = len(failures)
    failures = [f for f in failures if f]
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
