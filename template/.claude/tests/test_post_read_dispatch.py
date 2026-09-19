#!/usr/bin/env python3
"""Re-runnable proof for hooks/post_read_dispatch.py — the single PostToolUse entry that chains
three sub-hooks (retroactive grep nudge, routing audit, memory-hits logger).

Feeds one real PostToolUse payload through the dispatcher by subprocess and asserts on the
side effects each sub-hook owns: the memory-hits log line (4th), the routing-audit row (3rd),
and exit 0 with parseable-or-empty stdout (the additionalContext contract). A malformed payload
must still exit 0 (fail-open dispatcher).

    python3 .claude/tests/test_post_read_dispatch.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH = os.path.join(HERE, "..", "hooks", "post_read_dispatch.py")
VAULT_DOC = (r"C:\Users\x\ObsidianVault\DevProjects\{{PROJECT_NAME}}\Claude\Design\Push System Design.md")


def run(payload_text, env):
    return subprocess.run([sys.executable, DISPATCH], input=payload_text, capture_output=True,
                          text=True, timeout=90, env=env, encoding="utf-8")


def valid_hook_output(stdout):
    if not stdout.strip():
        return True
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return False
    hook = payload.get("hookSpecificOutput") if isinstance(payload, dict) else None
    return (isinstance(hook, dict)
            and hook.get("hookEventName") == "PostToolUse"
            and isinstance(hook.get("additionalContext"), str))


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="prd_")
    log_mem = os.path.join(tmp, "memory_hits.jsonl")
    log_audit = os.path.join(tmp, "routing_audit.jsonl")
    env = dict(os.environ, HARNESS_MEMORY_HITS_LOG=log_mem, HARNESS_ROUTING_AUDIT_LOG_PATH=log_audit,
               HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "state"), CLAUDE_PROJECT_DIR=tmp)

    def check(label, cond, detail=""):
        check.calls += 1
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail if detail else ""))
    check.calls = 0

    with open(DISPATCH, encoding="utf-8") as fh:
        dispatch_source = fh.read()
    check("dispatcher no longer imports the retired cumulative counter",
          "tool_routing_cumulative" not in dispatch_source)

    # 1. A Read of an auto-memory file reaches the last sub-hook.
    r = run(json.dumps({"tool_name": "Read", "session_id": "prd00001", "agent_id": "",
                        "tool_input": {"file_path": os.path.join(tmp, ".claude", "auto-memory", "g.md")},
                        "tool_response": {"content": "x"}}), env)
    check("dispatcher exits 0 on a memory Read", r.returncode == 0, r.stderr[:200])
    lines = [json.loads(ln) for ln in open(log_mem, encoding="utf-8")] if os.path.exists(log_mem) else []
    check("memory-hits sub-hook logged the read", any(l.get("via") == "read" for l in lines), repr(lines))
    check("stdout is empty or a valid PostToolUse JSON object",
          valid_hook_output(r.stdout) and "Traceback" not in (r.stderr or ""),
          r.stdout[:200])

    # 2. A vault Read reaches the routing-audit sub-hook. The path alone is not a verdict; the
    #    parent request asking for bulk copyable extraction is, and a subagent inherits it.
    r = run(json.dumps({"tool_name": "Read", "session_id": "prd00002", "agent_id": "agent001",
                        "tool_input": {"file_path": VAULT_DOC}, "tool_response": {"content": "x"}}), env)
    check("dispatcher exits 0 on a vault Read", r.returncode == 0, r.stderr[:200])
    rows = [json.loads(ln) for ln in open(log_audit, encoding="utf-8")] if os.path.exists(log_audit) else []
    check("a vault Read with no bulk-copyable request logs no routing-audit row",
          not any(row.get("session_id") == "prd00002" for row in rows), repr(rows)[:300])
    state_dir = os.path.join(tmp, "state")
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "prd00003.json"), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": "Extract the same raw fields from every file and return one "
                                  "copyable entry per input path."}, fh)
    r = run(json.dumps({"tool_name": "Read", "session_id": "prd00003", "agent_id": "agent001",
                        "tool_input": {"file_path": VAULT_DOC}, "tool_response": {"content": "x"}}), env)
    rows = [json.loads(ln) for ln in open(log_audit, encoding="utf-8")] if os.path.exists(log_audit) else []
    check("routing-audit sub-hook logged the subagent's bulk-copyable vault read as cue-exempt",
          any(row.get("session_id") == "prd00003" and row.get("classification") == "cue-exempt"
              and row.get("rule") == "native-read-bulk-copyable" for row in rows), repr(rows)[:300])

    # 3. Malformed payload: the dispatcher fails open.
    r = run("{not json", env)
    check("malformed payload exits 0 with an empty or valid hook payload",
          r.returncode == 0 and valid_hook_output(r.stdout)
          and "Traceback" not in (r.stderr or ""),
          "stdout=%r stderr=%r" % (r.stdout[:200], r.stderr[:200]))

    total = check.calls
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
