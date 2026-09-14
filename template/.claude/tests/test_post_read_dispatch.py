#!/usr/bin/env python3
"""Re-runnable proof for hooks/post_read_dispatch.py — the single PostToolUse entry that chains
four sub-hooks (cumulative counter, retroactive grep nudge, routing audit, memory-hits logger).

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

    # 1. A Read of an auto-memory file reaches the 4th sub-hook.
    r = run(json.dumps({"tool_name": "Read", "session_id": "prd00001", "agent_id": "",
                        "tool_input": {"file_path": os.path.join(tmp, ".claude", "auto-memory", "g.md")},
                        "tool_response": {"content": "x"}}), env)
    check("dispatcher exits 0 on a memory Read", r.returncode == 0, r.stderr[:200])
    lines = [json.loads(ln) for ln in open(log_mem, encoding="utf-8")] if os.path.exists(log_mem) else []
    check("memory-hits sub-hook logged the read", any(l.get("via") == "read" for l in lines), repr(lines))
    check("stdout is empty or a JSON object", not r.stdout.strip() or isinstance(json.loads(r.stdout), dict), r.stdout[:200])

    # 2. A subagent Read of a synthesis-shaped vault doc reaches the routing-audit sub-hook.
    r = run(json.dumps({"tool_name": "Read", "session_id": "prd00002", "agent_id": "agent001",
                        "tool_input": {"file_path": VAULT_DOC}, "tool_response": {"content": "x"}}), env)
    check("dispatcher exits 0 on a vault Read", r.returncode == 0, r.stderr[:200])
    rows = [json.loads(ln) for ln in open(log_audit, encoding="utf-8")] if os.path.exists(log_audit) else []
    check("routing-audit sub-hook logged a row for the vault read",
          any(row.get("session_id") == "prd00002" for row in rows), repr(rows)[:300])

    # 3. Malformed payload: the dispatcher fails open.
    r = run("{not json", env)
    check("malformed payload exits 0", r.returncode == 0, r.stderr[:200])

    total = check.calls
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
