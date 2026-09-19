#!/usr/bin/env python3
"""Re-runnable proof for hooks/check_logger_tag_prefix.py.

Covers both channels: `process(payload)` (the dispatcher entry, called in-process)
and the standalone `main()` stdin->stdout channel (via subprocess, matching how
post_edit_dispatch.py's own proof drives sibling sub-hooks). Clean/planted/restored
cases run through `process`; the standalone channel gets its own planted-violation
case; a malformed payload is asserted not to crash either channel.

    python3 .claude/tests/test_check_logger_tag_prefix.py
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
HOOK = os.path.join(HOOKS, "check_logger_tag_prefix.py")


def load_hook_module():
    spec = importlib.util.spec_from_file_location("check_logger_tag_prefix_probe", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def written(content, path="Scripts/Probe.cs", tool="Write"):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": "probe0001",
            "tool_input": {"file_path": path, "content": content},
            "tool_response": {"filePath": path}}


def run_standalone(payload, env):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                        capture_output=True, text=True, timeout=60, env=env,
                        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def main():
    cases = []
    module = load_hook_module()

    tmp = tempfile.mkdtemp(prefix="check_logger_tag_prefix_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               PYTHONIOENCODING="utf-8")

    # --- (a) clean payload: a tagged call -> no emission ----------------------
    clean_payload = written('JmoLogger.Info(this, "[Foo] all good");')
    result = module.process(clean_payload)
    cases.append(("clean tagged call emits nothing via process()", result is None))

    # --- (b) planted violation: untagged call -> expected channel/content -----
    violation_payload = written('JmoLogger.Info(this, "plain message");')
    result = module.process(violation_payload)
    cases.append(("planted untagged call returns a context dict",
                  isinstance(result, dict) and "context" in result))
    cases.append(("planted violation names the hook and the untagged call",
                  isinstance(result, dict)
                  and "[logger_tag_prefix]" in result["context"]
                  and "plain message" in result["context"]))

    # --- (c) restored clean: same file, tag re-added -> no emission again -----
    restored_payload = written('JmoLogger.Info(this, "[Foo] restored");')
    result = module.process(restored_payload)
    cases.append(("restored tagged call emits nothing via process()", result is None))

    # --- non-.cs file and non-Write/Edit tool are also no-ops -----------------
    other_tool = written('JmoLogger.Info(this, "plain message");')
    other_tool["tool_name"] = "Read"
    cases.append(("non-Write/Edit tool_name emits nothing",
                  module.process(other_tool) is None))

    non_cs = written('JmoLogger.Info(this, "plain message");', path="Docs/notes.md")
    cases.append(("non-.cs file_path emits nothing", module.process(non_cs) is None))

    # --- (d) standalone stdin->stdout channel, planted violation --------------
    rc, out, err = run_standalone(violation_payload, env)
    crashed = rc not in (0, 2) or "Traceback" in err
    cases.append(("standalone channel: exit code is 0 or 2, no traceback (else CRASH)",
                  not crashed))
    if not crashed:
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = None
        specific = (parsed or {}).get("hookSpecificOutput") or {}
        cases.append(("standalone channel: stdout is JSON with additionalContext",
                      specific.get("hookEventName") == "PostToolUse"
                      and "[logger_tag_prefix]" in (specific.get("additionalContext") or "")))
    else:
        cases.append(("standalone channel: stdout is JSON with additionalContext", False))

    # --- (d-clean) standalone channel, clean payload -> "{}" and exit 0 -------
    rc, out, err = run_standalone(clean_payload, env)
    crashed = rc not in (0, 2) or "Traceback" in err
    cases.append(("standalone channel: clean payload exit 0/2, no traceback (else CRASH)",
                  not crashed))
    cases.append(("standalone channel: clean payload emits {} on stdout",
                  not crashed and rc == 0 and out == "{}"))

    # --- (e) malformed / non-dict payload does not crash -----------------------
    try:
        result = module.process({})
        empty_ok = result is None
    except Exception as exc:  # noqa: BLE001
        empty_ok = False
        print(f"  ! process({{}}) raised: {exc!r}")
    cases.append(("empty dict payload does not crash process()", empty_ok))

    r = subprocess.run([sys.executable, HOOK], input="not json at all",
                        capture_output=True, text=True, timeout=60, env=env,
                        encoding="utf-8", errors="replace")
    crashed = r.returncode not in (0, 2) or "Traceback" in (r.stderr or "")
    cases.append(("malformed stdin does not crash the standalone channel (else CRASH)",
                  not crashed))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
