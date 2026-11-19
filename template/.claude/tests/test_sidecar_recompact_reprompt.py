"""Proof for hooks/sidecar_recompact_reprompt.py: a compacted sidecar child gets its brief back; other sources stay silent.

Real SessionStart payloads on stdin against a planted prompt file in a temp root. Writes nothing outside it.

    python3 .claude/tests/test_sidecar_recompact_reprompt.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from amend_delivery import HOOK_TEXT_CAP  # noqa: E402

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "sidecar_recompact_reprompt.py")
FAILURES = []
ROOT = tempfile.mkdtemp(prefix="recompact_proof_")
BRIEF = os.path.join(ROOT, "brief.md")
MARK = "PROOF-BRIEF-LINE-7f3a"
with open(BRIEF, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("# Task\n%s\nWrite the report.\n" % MARK)


def run(payload, prompt_file=BRIEF, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR_PROMPT_FILE"}
    args = [sys.executable, HOOK] + (["--prompt-file", prompt_file] if prompt_file else [])
    r = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                       timeout=30, env=env, cwd=cwd)
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

def planted(name, text):
    path = os.path.join(ROOT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def size(out):
    return len(out.encode("utf-8"))


RESUME = "[sidecar-recompact] Your context was just compacted"
SESSION_START = {"source": "compact", "hook_event_name": "SessionStart"}
big = planted("big.md", "".join("brief line %05d é\n" % i for i in range(6000)))
rc, out, err = run(SESSION_START, prompt_file=big)
check("an oversize brief is delivered within the client's hook-text cap", rc == 0 and size(out) <= HOOK_TEXT_CAP,
      "%d bytes" % size(out))
check("an oversize brief leads with the resume line and names its absolute path to Read before the next action",
      out.startswith(RESUME) and big in out and "Read" in out.rstrip().splitlines()[-1], out[-400:])
check("an oversize brief keeps whole lines from its head, never a cut line",
      "brief line 00000 é\n" in out and all(l.endswith(" é") for l in out.splitlines() if l.startswith("brief line ")),
      out[-400:])

wide = planted("wide.md", "# Task\n" + "x" * 30000 + "\nlast\n")
rc, out, err = run(SESSION_START, prompt_file=wide)
check("a brief with one line past the cap still ends within it and names its path",
      rc == 0 and size(out) <= HOOK_TEXT_CAP and wide in out, "%d bytes" % size(out))

rc, out, err = run(SESSION_START, prompt_file="big.md", cwd=ROOT)
check("a relative prompt file path is named absolute", rc == 0 and big in out, out[-300:])

rc, out, err = run(SESSION_START)
check("a small brief still prints whole, with no Read-the-rest line",
      rc == 0 and "Write the report." in out and out.rstrip().endswith("----- END BRIEF -----")
      and BRIEF not in out, out)

rc, out, err = run({"hook_event_name": "PreCompact", "trigger": "manual"})
check("the PreCompact rail stays within the cap", rc == 0 and 0 < size(out) <= HOOK_TEXT_CAP, out[:200])

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
