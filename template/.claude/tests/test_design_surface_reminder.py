#!/usr/bin/env python3
"""Re-runnable proof for hooks/design_surface_reminder.py.

Covers both channels lane B2 kept: the dispatcher-facing `process(payload)` entry
(exercised in-process, state redirected via HARNESS_HOOK_STATE_DIR) and the
standalone stdin->stdout `main()` channel (exercised via real subprocess, state
redirected via HOME/USERPROFILE so this never touches ~/.claude/.routing_state/).

    python3 .claude/tests/test_design_surface_reminder.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
HOOK = os.path.join(HOOKS, "design_surface_reminder.py")

if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)


def load_module(state_dir):
    spec = importlib.util.spec_from_file_location("design_surface_reminder_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # HARNESS_HOOK_STATE_DIR is the one state redirect every hook honours (`_hook_state.state_dir`).
    os.environ["HARNESS_HOOK_STATE_DIR"] = state_dir
    return module


def payload(text, path="Scripts/Probe.cs", session="probe0001", tool="Write"):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "session_id": session,
        "tool_input": {"file_path": path, "content": text},
        "tool_response": {"filePath": path},
    }


def run_subprocess(payload_obj, env):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload_obj),
                        capture_output=True, text=True, timeout=60, env=env,
                        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def run_raw(text, env):
    r = subprocess.run([sys.executable, HOOK], input=text, capture_output=True,
                        text=True, timeout=60, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="design_surface_reminder_")

    # --- (a) clean payload -> no emission ----------------------------------
    state_dir_a = os.path.join(tmp, "state_a")
    module = load_module(state_dir_a)
    result = module.process(payload("public class Clean { public int Value = 1; }"))
    cases.append(("clean payload emits nothing", result is None))

    # --- (b) planted violation -> expected channel/content ------------------
    state_dir_b = os.path.join(tmp, "state_b")
    module = load_module(state_dir_b)
    violation = payload("[Export] public bool Enabled = false;", session="probe-violation")
    result = module.process(violation)
    cases.append(("planted [Export] bool violation emits a context reminder",
                  isinstance(result, dict)
                  and "design-litmus" in (result.get("context") or "")
                  and "design_litmus.md #1" in (result.get("context") or "")))

    # second call, same file+session: one-fire-per-file-per-session should suppress it
    result_again = module.process(violation)
    cases.append(("the same violation does not re-fire in the same session",
                  result_again is None))

    # --- (c) restored clean -> no emission -----------------------------------
    state_dir_c = os.path.join(tmp, "state_c")
    module = load_module(state_dir_c)
    restored = payload("public class Clean { public int Value = 1; }",
                        path="Scripts/Probe.cs", session="probe-restored")
    result = module.process(restored)
    cases.append(("a file restored to clean content emits nothing", result is None))

    # non-.cs file with the same violating text never fires
    non_cs = payload("[Export] public bool Enabled = false;", path="Scripts/Probe.txt",
                      session="probe-noncs")
    result = module.process(non_cs)
    cases.append(("a non-.cs file never fires even with matching text", result is None))

    # non-Write/Edit tool never fires
    other_tool = payload("[Export] public bool Enabled = false;", session="probe-tool",
                          tool="Read")
    result = module.process(other_tool)
    cases.append(("a non-Write/Edit tool_name never fires", result is None))

    # --- (e) malformed/non-dict payload does not crash (in-process) --------
    try:
        result = module.process({})
        ok = result is None
    except Exception:
        ok = False
    cases.append(("an empty dict payload does not crash process()", ok))

    try:
        module.process({"tool_name": "Write", "tool_input": "not-a-dict",
                         "session_id": "probe-bad"})
        ok = True
    except Exception:
        ok = False
    cases.append(("a non-dict tool_input does not crash process()", ok))

    # --- (d) standalone stdin->stdout channel, real subprocess -------------
    home = os.path.join(tmp, "home")
    state_dir_d = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir_d, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir_d,
               PYTHONIOENCODING="utf-8")

    rc, out, err = run_subprocess(violation, env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("standalone channel: planted violation (CRASH classification)", False))
    else:
        try:
            parsed = json.loads(out) if out else {}
            specific = parsed.get("hookSpecificOutput") or {}
            ctx = specific.get("additionalContext") or ""
            ok = (rc == 0 and specific.get("hookEventName") == "PostToolUse"
                  and "design-litmus" in ctx)
        except (json.JSONDecodeError, ValueError):
            ok = False
        cases.append(("standalone channel emits the design-litmus reminder on stdout", ok))

    rc, out, err = run_subprocess(payload("public class Clean { public int Value = 1; }",
                                           session="probe-clean-standalone"), env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("standalone channel: clean payload (CRASH classification)", False))
    else:
        cases.append(("standalone channel emits nothing for a clean payload",
                      rc == 0 and out in ("", "{}")))

    # malformed (non-JSON) stdin over the standalone channel must not crash
    rc, out, err = run_raw("not json at all", env)
    if "Traceback" in err:
        cases.append(("standalone channel: malformed stdin (CRASH classification)", False))
    else:
        cases.append(("standalone channel exits 0 with no output on malformed stdin",
                      rc == 0 and out in ("", "{}")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
