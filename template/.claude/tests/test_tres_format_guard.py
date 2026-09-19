#!/usr/bin/env python3
"""Re-runnable proof for hooks/tres_format_guard.py.

Covers both the dispatcher `process(payload)` entry point (in-process) and the
standalone `--hook` stdin->stdout channel (subprocess), with clean, planted-violation
and restored-clean cases plus a malformed-payload crash guard.

    python3 .claude/tests/test_tres_format_guard.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(CLAUDE, ".."))
HOOK = os.path.join(CLAUDE, "hooks", "tres_format_guard.py")

# Same fixture as test_post_edit_dispatch.py's BAD_TRES: load_steps mismatch is
# load_steps_validator's case, missing script_class= is this guard's case.
BAD_TRES = """[gd_resource type="Resource" load_steps=9 format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
"""

CLEAN_TRES = """[gd_resource type="Resource" script_class="Probe" load_steps=2 format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
"""


def load_module():
    spec = importlib.util.spec_from_file_location("tres_format_guard_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plant(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def written(path, content, session="tresfmt0001", tool="Write"):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": session,
            "tool_input": {"file_path": path, "content": content},
            "tool_response": {"filePath": path}}


def run_hook(text, env):
    r = subprocess.run([sys.executable, HOOK, "--hook"], input=text,
                        capture_output=True, text=True, timeout=60, env=env,
                        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def context_from_stdout(stdout):
    if not stdout or stdout == "{}":
        return ""
    data = json.loads(stdout)
    specific = data.get("hookSpecificOutput") or {}
    assert specific.get("hookEventName") == "PostToolUse", specific
    return specific.get("additionalContext") or ""


def main():
    tmp = tempfile.mkdtemp(prefix="tres_format_guard_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")

    module = load_module()
    cases = []

    # --- (a) clean payload via process() -> no emission --------------------
    clean_path = plant(os.path.join(tmp, "repo", "Data", "clean.tres"), CLEAN_TRES)
    result = module.process(written(clean_path, CLEAN_TRES))
    cases.append(("clean .tres via process() emits nothing", result is None))

    # --- (b) planted violation via process() -> expected channel/content ---
    bad_path = plant(os.path.join(tmp, "repo", "Data", "bad.tres"), BAD_TRES)
    result = module.process(written(bad_path, BAD_TRES))
    cases.append(("planted violation via process() returns a context dict",
                  isinstance(result, dict) and "[tres-format-guard]" in (result.get("context") or "")
                  and "script_class=" in result.get("context", "")))

    # --- (c) restored clean via process() -> no emission again -------------
    plant(bad_path, CLEAN_TRES)
    result = module.process(written(bad_path, CLEAN_TRES))
    cases.append(("restoring the same path to canonical form emits nothing", result is None))

    # non-.tres/.tscn path is ignored outright
    cs_path = plant(os.path.join(tmp, "repo", "Scripts", "Probe.cs"),
                    "public class Probe {}\n")
    result = module.process(written(cs_path, "public class Probe {}"))
    cases.append(("a .cs path is ignored by process()", result is None))

    # --- (d) standalone --hook channel via subprocess -----------------------
    bad2_path = plant(os.path.join(tmp, "repo", "Data", "bad2.tres"), BAD_TRES)
    rc, out, err = run_hook(json.dumps(written(bad2_path, BAD_TRES, session="tresfmt0002")), env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("--hook on planted violation: CRASH", False))
    else:
        ctx = context_from_stdout(out)
        cases.append(("--hook on planted violation reports the guard's channel",
                      rc == 0 and "[tres-format-guard]" in ctx))

    clean2_path = plant(os.path.join(tmp, "repo", "Data", "clean2.tres"), CLEAN_TRES)
    rc, out, err = run_hook(json.dumps(written(clean2_path, CLEAN_TRES, session="tresfmt0003")), env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("--hook on clean payload: CRASH", False))
    else:
        cases.append(("--hook on clean payload emits nothing",
                      rc == 0 and out in ("", "{}")))

    # --- (e) malformed / non-dict payload does not crash --------------------
    rc, out, err = run_hook("not json at all", env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("--hook on malformed stdin: CRASH", False))
    else:
        cases.append(("--hook on malformed stdin exits clean with no output",
                      rc == 0 and out in ("", "{}")))

    # process() with a non-dict tool_input / missing keys should not crash
    try:
        result = module.process({"tool_input": {}})
        cases.append(("process() with an empty tool_input does not crash", result is None))
    except Exception as e:
        cases.append((f"process() with empty tool_input CRASHED: {e!r}", False))

    try:
        result = module.process({})
        cases.append(("process() with an empty payload does not crash", result is None))
    except Exception as e:
        cases.append((f"process() with empty payload CRASHED: {e!r}", False))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
