#!/usr/bin/env python3
"""Prove registered Grep routing emits once, not again after the tool result."""
import json
import os
import subprocess
import sys
import tempfile

import _settings_probe

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETTINGS = _settings_probe.settings_path(os.path.join(ROOT, ".claude"))


def registered_dispatch(event, name):
    with open(SETTINGS, encoding="utf-8") as fh:
        settings = json.load(fh)
    for group in settings["hooks"][event]:
        if "Grep" not in group.get("matcher", ""):
            continue
        for hook in group.get("hooks", []):
            if name in hook.get("command", ""):
                return os.path.join(ROOT, ".claude", "hooks", name)
    raise AssertionError(f"{name} is not registered for {event} Grep")


def run(path, payload, env):
    return subprocess.run([sys.executable, path], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)


def additional_context(result, event):
    if not result.stdout.strip():
        return ""
    payload = json.loads(result.stdout)
    specific = payload.get("hookSpecificOutput") or {}
    assert specific.get("hookEventName") == event
    return specific.get("additionalContext") or ""


def main():
    pre = registered_dispatch("PreToolUse", "pre_read_dispatch.py")
    post = registered_dispatch("PostToolUse", "post_read_dispatch.py")
    tmp = tempfile.mkdtemp(prefix="routing_post_grep_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               HARNESS_ROUTING_AUDIT_LOG_PATH=os.path.join(tmp, "routing.jsonl"),
               HARNESS_MEMORY_HITS_LOG=os.path.join(tmp, "memory.jsonl"),
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8",
               HARNESS_ROUTING_HARD_BLOCK_CS_GREP="false")
    sid = "trgrep01"
    with open(os.path.join(state_dir, sid[:8] + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": "Locate the SpellFactory declaration and callers."}, fh)
    tool_input = {"pattern": "SpellFactory", "glob": "*.cs"}
    pre_payload = {"hook_event_name": "PreToolUse", "tool_name": "Grep",
                   "session_id": sid, "tool_input": tool_input}
    post_payload = {"hook_event_name": "PostToolUse", "tool_name": "Grep",
                    "session_id": sid, "tool_input": tool_input,
                    "tool_response": {"content": "src/SpellFactory.cs:10:class SpellFactory"}}
    before = run(pre, pre_payload, env)
    after = run(post, post_payload, env)
    pre_context = additional_context(before, "PreToolUse")
    post_context = additional_context(after, "PostToolUse")
    audit_path = env["HARNESS_ROUTING_AUDIT_LOG_PATH"]
    with open(audit_path, encoding="utf-8") as fh:
        audit_rows = [json.loads(line) for line in fh if line.strip()]
    main_audit = [row for row in audit_rows if row.get("session_id") == sid[:8]]

    # Positive control for the fallback channel: if no pre-call receipt exists,
    # the post hook still emits once through PostToolUse additionalContext.
    fallback_sid = "trgrep02"
    with open(os.path.join(state_dir, fallback_sid[:8] + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": "Locate the SpellFactory declaration and callers."}, fh)
    fallback_payload = dict(post_payload, session_id=fallback_sid)
    fallback = run(post, fallback_payload, env)
    fallback_context = additional_context(fallback, "PostToolUse")

    indexed_sid = "trgrep03"
    with open(os.path.join(state_dir, indexed_sid[:8] + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": "The SpellFactory name is verified unique; locate its resource use."}, fh)
    indexed_payload = dict(post_payload, session_id=indexed_sid,
                           tool_input={"pattern": "SpellFactory", "glob": "*.tres"})
    indexed = run(post, indexed_payload, env)
    indexed_context = additional_context(indexed, "PostToolUse")

    cases = [
        ("registered pre-call route keeps the C# symbol nudge",
         before.returncode == 0 and "LSP" in pre_context),
        ("the post-call route does not repeat advice already delivered",
         after.returncode == 0 and post_context == ""),
        ("routing audit records the delivered pre-call Grep advisory",
         bool(main_audit and main_audit[-1].get("classification") == "nudge-warranted"
              and main_audit[-1].get("nudge_fired") is True)),
        ("post-call fallback still uses the registered additionalContext channel",
         fallback.returncode == 0 and "NEXT PascalCase lookup" in fallback_context),
        ("verified-unique only exempts C# and does not suppress indexed-resource fallback",
         indexed.returncode == 0 and "semantic-search" in indexed_context),
        ("all advisory routes stay off stderr",
         not before.stderr.strip() and not after.stderr.strip() and not fallback.stderr.strip()
         and not indexed.stderr.strip()),
    ]
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
