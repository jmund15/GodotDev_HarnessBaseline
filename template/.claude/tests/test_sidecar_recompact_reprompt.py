"""Proof for hooks/sidecar_recompact_reprompt.py: a compacted sidecar child gets its brief back; other sources stay silent.

Real SessionStart payloads on stdin against a planted prompt file in a temp root. Writes nothing outside it.

    python3 .claude/tests/test_sidecar_recompact_reprompt.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "sidecar_recompact_reprompt.py")
FAILURES = []
ROOT = tempfile.mkdtemp(prefix="recompact_proof_")
BRIEF = os.path.join(ROOT, "brief.md")
MARK = "PROOF-BRIEF-LINE-7f3a"
with open(BRIEF, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("# Task\n%s\nWrite the report.\n" % MARK)


def run(payload, prompt_file=BRIEF):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR_PROMPT_FILE"}
    args = [sys.executable, HOOK] + (["--prompt-file", prompt_file] if prompt_file else [])
    r = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                       timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


rc, out, err = run({"source": "compact", "hook_event_name": "SessionStart"})
check("a compacted sidecar child gets its brief re-injected verbatim",
      rc == 0 and "ORIGINAL BRIEF" in out and MARK in out and "Write the report." in out, out + err)

rc, out, err = run({"source": "compact", "hook_event_name": "SessionStart"}, prompt_file=os.path.join(ROOT, "missing.md"))
check("a compacted child whose brief is unreadable gets the re-read advisory",
      rc == 0 and "is unreadable" in out and MARK not in out, out + err)

rc, out, err = run({"source": "startup", "hook_event_name": "SessionStart"})
check("a startup session gets no re-injection", rc == 0 and out == "", out + err)

rc, out, err = run({"source": "compact", "hook_event_name": "SessionStart"}, prompt_file=None)
check("a child with no prompt file stays silent", rc == 0 and out == "", out + err)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
