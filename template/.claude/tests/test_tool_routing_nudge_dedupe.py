"""Re-runnable proof for hooks/tool_routing_nudge.py's repeat-suppression (B5).

The PascalCase-Grep advisory reads identically for every pattern in one target FAMILY, so it
is delivered once per session per family — a per-pattern key re-delivered known text on each
new symbol. Read/Obsidian nudges stay keyed by path, where each target is genuinely new.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_tool_routing_nudge_dedupe.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tool_routing_nudge.py")

SID = "trn00001"


def call(tool_name, tool_input, env, session=SID):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"tool_name": tool_name, "session_id": session,
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=60, env=env)
    out = (r.stdout or "").strip()
    if not out:
        return ""
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")


def grep(pattern, env, glob="*.cs", session=SID):
    return call("Grep", {"pattern": pattern, "glob": glob}, env, session)


def main():
    tmp = tempfile.mkdtemp(prefix="trnstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    first = grep("OrderInstance", env)
    cases.append(("the first bare-PascalCase Grep on .cs nudges", "tool-routing" in first))

    second = grep("WidgetTier", env)
    cases.append(("a different symbol in the same family does not re-nudge", second == ""))

    other = grep("WidgetTier", env, glob="*.tres")
    cases.append(("a different target family still nudges once",
                  "semantic-search" in other))

    cases.append(("that family is then suppressed too",
                  grep("LayoutTemplate", env, glob="*.tres") == ""))

    cases.append(("a fresh session starts clean",
                  "tool-routing" in grep("OrderInstance", env, session="trn00002")))

    # Negatives — dedupe must not swallow a rule that never fired, or widen the trigger.
    cases.append(("an anchored Grep never nudges",
                  grep("class OrderInstance", env, session="trn00003") == ""))
    cases.append(("a regex pattern never nudges",
                  grep("Order(Instance|Effect)", env, session="trn00004") == ""))
    cases.append(("a Read of a synthesis doc is keyed by path, not family",
                  "tool-routing" in call("Read", {"file_path": "C:/vault/Design/x.md"},
                                         env, session="trn00005")))
    cases.append(("the same Read path is suppressed",
                  call("Read", {"file_path": "C:/vault/Design/x.md"},
                       env, session="trn00005") == ""))
    cases.append(("a different Read path still nudges",
                  "tool-routing" in call("Read", {"file_path": "C:/vault/Design/y.md"},
                                         env, session="trn00005")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
