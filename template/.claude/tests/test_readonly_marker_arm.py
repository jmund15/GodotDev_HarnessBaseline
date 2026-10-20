#!/usr/bin/env python3
"""Proof for hooks/readonly_marker_arm.py: a Workflow dispatch arms the read-only marker exactly
when its script file declares the `// READONLY-FANOUT-ENGINE` line, and allows the fold stage's
write to each `args.fold.subjects[]` file and declared new path while every other subagent write still warns.

    python3 .claude/tests/test_readonly_marker_arm.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
HOOK = REPO / ".claude" / "hooks" / "readonly_marker_arm.py"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def armed(home: Path, tool_input: dict, tool_name: str = "Workflow") -> bool:
    """Run the hook in a fresh HOME and report whether it wrote the session marker."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PROJECT_DIR=str(REPO))
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "armtest1",
               "cwd": str(REPO)}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    marker = home / ".claude" / ".routing_state" / "readonly-armtest1.json"
    ok = proc.returncode == 0 and marker.is_file()
    if marker.is_file():
        marker.unlink()
    return ok


GUARD = REPO / ".claude" / "hooks" / "readonly_lens_write_guard.py"


def arm_state(home: Path, tool_input: dict):
    """Run the hook from cwd `home` and return the marker it wrote (None when none); the marker stays."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PROJECT_DIR=str(REPO))
    env.pop("HARNESS_HOOK_STATE_DIR", None)
    payload = {"tool_name": "Workflow", "tool_input": tool_input, "session_id": "armtest1",
               "cwd": str(home)}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    marker = home / ".claude" / ".routing_state" / "readonly-armtest1.json"
    if proc.returncode != 0 or not marker.is_file():
        return None
    return json.loads(marker.read_text(encoding="utf-8"))


def lens_write_note(home: Path, target: str) -> str:
    """Feed a subagent Write of `target` to the lens write guard; return its stderr ('CRASH' on a crash)."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PROJECT_DIR=str(REPO))
    env.pop("HARNESS_HOOK_STATE_DIR", None)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "armtest1",
               "agent_id": "agent-fold", "cwd": str(REPO),
               "tool_input": {"file_path": target, "content": "x"}}
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    if proc.returncode != 0 or "Traceback" in proc.stderr:
        return "CRASH"
    return proc.stderr


def fold_cases(home: Path):
    plan_rel = "plans/part-plan.md"
    (home / "plans").mkdir()
    (home / plan_rel).write_text("# plan\n", encoding="utf-8")
    plan_abs = str(home / plan_rel)
    engine = ".claude/workflows/review_fanout.js"

    def fold_of(*subjects, **extra):
        return dict({"templatePath": "t.md", "tier": "fanout", "subjects": list(subjects)}, **extra)

    def subject(files, new_paths=None, root="."):
        value = {"root": root, "snapshot": "snap.json", "files": files}
        if new_paths is not None:
            value["newPaths"] = new_paths
        return value

    plain = arm_state(home, {"scriptPath": engine, "args": {"agents": []}})
    base_prefixes = (plain or {}).get("allow_prefixes") or []
    fold = fold_of(subject([plan_rel]))
    for label, args in (("object", {"agents": [], "fold": fold}),
                        ("JSON string", json.dumps({"agents": [], "fold": fold}))):
        state = arm_state(home, {"scriptPath": engine, "args": args})
        prefixes = (state or {}).get("allow_prefixes") or []
        check(f"args.fold.subjects[].files ({label} args) arms each file as an allow prefix",
              any(os.path.normcase(p) == os.path.normcase(plan_abs) for p in prefixes), str(state))
    check("a dispatch without args.fold arms no subject file",
          plain is not None and not any("plans" in p for p in base_prefixes), str(plain))

    for bad in (".", "plans", "plans/absent-plan.md", plan_abs, "../plans/part-plan.md"):
        state = arm_state(home, {"scriptPath": engine, "args": {"agents": [], "fold": fold_of(subject([bad]))}})
        check(f"subject file {bad!r} (a directory, absent, absolute or escaping) widens nothing",
              state is not None and state.get("allow_prefixes") == base_prefixes, str(state))
    state = arm_state(home, {"scriptPath": engine, "args": {"agents": [], "fold": {
        "planPath": plan_rel, "planPaths": [plan_rel], "templatePath": "t.md", "tier": "fanout"}}})
    check("the retired planPath and planPaths arm nothing",
          state is not None and state.get("allow_prefixes") == base_prefixes, str(state))

    plan2_rel = "plans/part-plan-2.md"
    (home / plan2_rel).write_text("# plan 2\n", encoding="utf-8")
    plan2_abs = str(home / plan2_rel)
    new_rel = "Tests/Logic/NewTest.cs"
    new_abs = str(home / new_rel)
    (home / "Tests" / "Logic").mkdir(parents=True)
    joint = fold_of(subject([plan_rel, plan2_rel, "plans/absent-plan.md"], [new_rel, "Tests", "plans"]))
    state = arm_state(home, {"scriptPath": engine, "args": {"agents": [], "fold": joint}})
    prefixes = [os.path.normcase(p) for p in (state or {}).get("allow_prefixes") or []]
    check("subjects arm each existing file and each declared new path, no missing file and no directory",
          os.path.normcase(plan_abs) in prefixes and os.path.normcase(plan2_abs) in prefixes
          and os.path.normcase(new_abs) in prefixes and len(prefixes) == len(base_prefixes) + 3, str(state))
    note = lens_write_note(home, plan2_abs)
    check("a fold write to the second subject file draws no read-only warning", note == "", note)
    note = lens_write_note(home, new_abs)
    check("a fold write to a declared new path draws no read-only warning", note == "", note)
    note = lens_write_note(home, str(home / "Tests" / "Logic" / "OtherTest.cs"))
    check("a write to another file under Tests/ still warns", "[readonly-lens-write]" in note, note)

    arm_state(home, {"scriptPath": engine, "args": {"agents": [], "fold": fold}})
    note = lens_write_note(home, plan_abs)
    check("a fold write to a subject file draws no read-only warning", note == "", note)
    note = lens_write_note(home, str(home / "plans" / "some-other-plan.md"))
    check("a seat write to another plan still warns", "[readonly-lens-write]" in note, note)
    note = lens_write_note(home, plan_abs + ".bak")
    check("a write beside a subject file (same stem) still warns", "[readonly-lens-write]" in note, note)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        engine = home / "custom_engine.js"
        engine.write_text("// header\n// READONLY-FANOUT-ENGINE\nexport default 1\n", encoding="utf-8")
        plain = home / "plain.js"
        plain.write_text("// mentions READONLY-FANOUT-ENGINE inside prose only\n", encoding="utf-8")

        check("a shipped read-only engine arms by relative scriptPath",
              armed(home, {"scriptPath": ".claude/workflows/review_fanout.js"}))
        check("dispatch.js does not arm", not armed(home, {"scriptPath": ".claude/workflows/dispatch.js"}))
        check("any script file declaring the marker arms by absolute path",
              armed(home, {"scriptPath": str(engine)}))
        check("a script that only mentions the marker in prose does not arm",
              not armed(home, {"scriptPath": str(plain)}))
        check("an inline script body is not inspected",
              not armed(home, {"script": "// READONLY-FANOUT-ENGINE\n"}))
        check("a missing script file does not arm",
              not armed(home, {"scriptPath": str(home / "absent.js")}))
        check("a non-Workflow tool does not arm",
              not armed(home, {"scriptPath": ".claude/workflows/review_fanout.js"}, tool_name="Agent"))
    with tempfile.TemporaryDirectory() as tmp:
        fold_cases(Path(tmp))
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
