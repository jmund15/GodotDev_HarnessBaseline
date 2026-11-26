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
            "tool_response": {"filePath": path, "type": "create"}}


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


def test_write_reports_only_net_new_lines():
    module = load_hook_module()
    before = 'JmoLogger.Info(this, "old plain message");\n'
    event = written(before + 'var unrelated = 1;\n')
    event['tool_response']['originalFile'] = before
    assert module.process(event) is None
    event['tool_input']['content'] += 'JmoLogger.Debug(this, "new plain message");\n'
    result = module.process(event)
    assert result and 'new plain message' in result['context'] and 'old plain message' not in result['context']


def test_edit_retained_context_is_not_a_new_call():
    module = load_hook_module()
    before = 'JmoLogger.Info(this, "old plain message");\nvar unrelated = 1;'
    event = {'tool_name': 'Edit', 'tool_input': {'file_path': 'Scripts/Probe.cs',
             'old_string': before, 'new_string': before.replace('= 1', '= 2')}}
    assert module.process(event) is None
    event['tool_input']['new_string'] += '\nJmoLogger.Debug(this, "new plain message");'
    result = module.process(event)
    assert result and 'new plain message' in result['context'] and 'old plain message' not in result['context']


# Captured Write update from top-level session 00d8a771-c98d-41a8-814a-ff440c801325.jsonl.
LIVE_WRITE = json.loads('{"content": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    found = data.find(needle)\\n    print(f\\"=== {needle.decode()} offset={found} ===\\")\\n    if found < 0:\\n        continue\\n    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]\\n    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n    text = \\" \\".join(text.split())\\n    print(text[:1300])\\n", "type": "update", "originalFile": "from pathlib import Path\\n\\npath = Path(r\\"C:\\\\Users\\\\dev\\\\.local\\\\bin\\\\claude.exe\\")\\ndata = path.read_bytes()\\nterms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]\\nfor needle in terms:\\n    print(f\\"=== {needle.decode()} ===\\")\\n    start = 0\\n    count = 0\\n    while count < 6:\\n        found = data.find(needle, start)\\n        if found < 0:\\n            break\\n        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]\\n        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)\\n        text = \\" \\".join(text.split())\\n        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")\\n        count += 1\\n        start = found + len(needle)\\n", "structuredPatch": [{"oldStart": 4, "oldLines": 16, "newStart": 4, "newLines": 11, "lines": [" data = path.read_bytes()", " terms = [b\\"function kwt\\", b\\"function Swt\\", b\\"function wwt\\", b\\"function y8\\", b\\"function uWe\\", b\\"function zQ\\", b\\"function vf\\", b\\"subagent_estimate\\", b\\"reactiveUnsupported\\"]", " for needle in terms:", "-    print(f\\"=== {needle.decode()} ===\\")", "-    start = 0", "-    count = 0", "-    while count < 6:", "-        found = data.find(needle, start)", "-        if found < 0:", "-            break", "-        raw = data[max(0, found - 300):min(len(data), found + len(needle) + 2200)]", "-        text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "-        text = \\" \\".join(text.split())", "-        print(f\\"[{count + 1}] offset={found} {text[:2500]}\\")", "-        count += 1", "-        start = found + len(needle)", "+    found = data.find(needle)", "+    print(f\\"=== {needle.decode()} offset={found} ===\\")", "+    if found < 0:", "+        continue", "+    raw = data[max(0, found - 180):min(len(data), found + len(needle) + 1100)]", "+    text = \\"\\".join(chr(b) if 32 <= b <= 126 or b in (9, 10, 13) else \\" \\" for b in raw)", "+    text = \\" \\".join(text.split())", "+    print(text[:1300])"]}]}')


def test_live_write_patch_reconstructs_preimage():
    module = load_hook_module()
    # The producer's originalFile is an independent oracle for its structuredPatch.
    response = dict(LIVE_WRITE, originalFile=None)
    assert module.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) == LIVE_WRITE['originalFile']
    assert module.pre_edit_text({'tool_name': 'Write'}, LIVE_WRITE['content']) is None
    response['structuredPatch'] = [dict(response['structuredPatch'][0], newStart=999)]
    assert module.pre_edit_text({'tool_name': 'Write', 'tool_response': response}, LIVE_WRITE['content']) is None


def test_added_lines_counts_net_new_occurrences():
    assert load_hook_module().added_lines("a\nb\n", "a\nc\nb\n") == ["c"]


if __name__ == "__main__":
    rc = main()
    test_write_reports_only_net_new_lines()
    test_edit_retained_context_is_not_a_new_call()
    test_live_write_patch_reconstructs_preimage()
    test_added_lines_counts_net_new_occurrences()
    sys.exit(rc)
