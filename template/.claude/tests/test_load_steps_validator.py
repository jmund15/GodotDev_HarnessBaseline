#!/usr/bin/env python3
"""Re-runnable proof for hooks/load_steps_validator.py.

Moved behind post_edit_dispatch.py in lane B2: it now exposes `process(payload)` (dict
consumed by the dispatcher) and keeps the standalone `--hook` stdin/stdout channel used by
settings.json directly. This proof feeds real PostToolUse payloads through both surfaces
and asserts on the emitted channel, per rules/harness_tooling.md.

    python3 .claude/tests/test_load_steps_validator.py
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
HOOK = os.path.join(HOOKS, "load_steps_validator.py")

BAD_TRES = """[gd_resource type="Resource" load_steps=9 format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
"""

CLEAN_TRES = """[gd_resource type="Resource" load_steps=2 format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
"""


def load_hook_module():
    spec = importlib.util.spec_from_file_location("load_steps_validator_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plant(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def written(path, content, session="post0001"):
    return {"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": session,
            "tool_input": {"file_path": path, "content": content},
            "tool_response": {"filePath": path}}


def run_hook_stdin(text, env):
    r = subprocess.run([sys.executable, HOOK, "--hook"], input=text, capture_output=True,
                        text=True, timeout=60, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def main():
    cases = []
    module = load_hook_module()
    tmp = tempfile.mkdtemp(prefix="load_steps_validator_proof_")
    state_dir = os.path.join(tmp, "state")
    os.makedirs(state_dir, exist_ok=True)

    # --- (a) clean payload: no emission via process() ---------------------
    clean_path = plant(os.path.join(tmp, "Clean.tres"), CLEAN_TRES)
    result = module.process(written(clean_path, CLEAN_TRES))
    cases.append(("clean file: process() returns None", result is None))

    # --- (b) planted violation: process() emits the advisory context ------
    bad_path = plant(os.path.join(tmp, "Bad.tres"), BAD_TRES)
    result = module.process(written(bad_path, BAD_TRES))
    cases.append(("planted mismatch: process() returns a context dict",
                  isinstance(result, dict) and "context" in result))
    cases.append(("planted mismatch: context names the file and the guard",
                  result is not None and "[load_steps_validator]" in result["context"]
                  and "load_steps=9" in result["context"]))
    cases.append(("planted mismatch: never a deny (posture is warn-only)",
                  result is not None and "deny" not in result))

    # --- (c) restored clean: no emission again -----------------------------
    plant(bad_path, CLEAN_TRES)
    result = module.process(written(bad_path, CLEAN_TRES))
    cases.append(("restored clean: process() returns None again", result is None))

    # --- non-.tres/.tscn path: no emission (guard scopes by extension) -----
    other_path = plant(os.path.join(tmp, "Clean.cs"), "public class Clean {}\n")
    result = module.process(written(other_path, "public class Clean {}\n"))
    cases.append(("non-resource file: process() returns None", result is None))

    # --- (d) standalone --hook channel via subprocess, same planted payload
    env = dict(os.environ, CLAUDE_PROJECT_DIR=os.path.abspath(os.path.join(CLAUDE, "..")),
               HARNESS_HOOK_STATE_DIR=state_dir, PYTHONIOENCODING="utf-8")
    bad_path2 = plant(os.path.join(tmp, "Bad2.tres"), BAD_TRES)
    rc, out, err = run_hook_stdin(json.dumps(written(bad_path2, BAD_TRES)), env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("standalone --hook on planted violation: no crash", False,
                      f"rc={rc} err={err[:400]}"))
    else:
        try:
            payload_out = json.loads(out) if out else {}
        except json.JSONDecodeError:
            payload_out = None
        specific = (payload_out or {}).get("hookSpecificOutput") or {}
        ctx = specific.get("additionalContext") or ""
        cases.append(("standalone --hook: exits 0", rc == 0))
        cases.append(("standalone --hook: emits PostToolUse additionalContext",
                      specific.get("hookEventName") == "PostToolUse" and
                      "[load_steps_validator]" in ctx))

    # standalone clean case -> exits 0 with "{}" (no context)
    clean_path2 = plant(os.path.join(tmp, "Clean2.tres"), CLEAN_TRES)
    rc, out, err = run_hook_stdin(json.dumps(written(clean_path2, CLEAN_TRES)), env)
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("standalone --hook on clean file: no crash", False,
                      f"rc={rc} err={err[:400]}"))
    else:
        cases.append(("standalone --hook on clean file: exits 0 with no context",
                      rc == 0 and out in ("", "{}")))

    # --- (e) malformed / non-dict payload does not crash -------------------
    rc, out, err = run_hook_stdin("not json at all", env)
    if "Traceback" in err:
        cases.append(("malformed stdin: no crash (CRASH classified)", False,
                      f"rc={rc} err={err[:400]}"))
    else:
        cases.append(("malformed stdin: exits 0 with no output",
                      rc == 0 and out in ("", "{}")))

    # malformed payload shape into process() directly: a list, not a dict
    try:
        result = module.process({"tool_input": None})
        cases.append(("process() with tool_input=None does not crash", result is None))
    except Exception as exc:  # pragma: no cover - proof records the defect, does not hide it
        cases.append(("process() with tool_input=None does not crash", False, repr(exc)))

    failed = [c for c in cases if not c[1]]
    for name, ok, *extra in cases:
        tag = "PASS" if ok else "FAIL"
        suffix = f" -- {extra[0]}" if extra else ""
        print(f"[{tag}] {name}{suffix}")

    if failed:
        print(f"\nRESULT: FAIL ({len(failed)}/{len(cases)} failed)")
        return 1
    print(f"\nRESULT: PASS ({len(cases)}/{len(cases)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
