"""Does the `required`-input not-found branch actually fire on a REAL transcript shape?

The original branch matched basename and the engine's not-found string within `[^\\n]{0,400}`
-- same line. Agent transcripts are JSONL: tool_use and tool_result are separate lines, so the
branch was unreachable and the gate false-PASSED the exact T1 failure it was written for.

Prove-it-fires, per /codify Step 7: a passing gate and a gate never in the path are
indistinguishable, so these assert outcomes rather than eyeballing the regex.

Case 4 is the one that keeps the fix honest in the other direction -- a failed read followed
by a successful one is an arm RECOVERING, and voiding it would be a false alarm.
"""
import json
import os
import sys
import tempfile

# Resolve tools/ from THIS file, not from an absolute path: a committed test pinned to one
# machine's checkout passes only there, which is the same class of defect it is testing for.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), 'tools'))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import void_check

TMP = tempfile.mkdtemp(prefix='void_check_engagement_')

ROOT = 't1-luna-max'
REQ = ['.claude/stress-test/T1-addon-survey.md']
BASE = 'T1-addon-survey.md'


def w(name, records):
    p = os.path.join(TMP, name)
    with open(p, 'w', encoding='utf-8') as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return p


def call(fp):
    return {"message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": fp}}]}}


def result(txt):
    return {"message": {"content": [{"type": "tool_result", "content": txt}]}}


def chatter(n=20):
    return [{"message": {"content": [
        {"type": "text", "text": "continuing in t1-luna-max"}]}} for _ in range(n)]


CASES = [
    ("JSONL, read failed (the T1 shape)", 'VOID',
     w('eng_a.jsonl', [call(f'{ROOT}/{BASE}'), result("File does not exist.")] + chatter())),
    ("flat log, read failed", 'VOID',
     w('eng_b.txt', [f"t1-luna-max Read {BASE} -> File does not exist.", "t1-luna-max on"])),
    ("JSONL, never referenced at all", 'VOID',
     w('eng_c.jsonl', chatter())),
    ("JSONL, failed then RETRIED ok", 'VALID',
     w('eng_d.jsonl', [call(f'{ROOT}/{BASE}'), result("File does not exist."),
                       call(f'{ROOT}/{BASE}'), result("# Survey\nbody text here")] + chatter())),
    ("JSONL, read fine", 'VALID',
     w('eng_e.jsonl', [call(f'{ROOT}/{BASE}'), result("# Survey\nbody")] + chatter())),
]

fails = 0
for label, want, path in CASES:
    got, reason = void_check.engagement(path, ROOT, required=REQ)
    ok = got == want
    fails += not ok
    print("%-38s want=%-5s got=%-5s %s  | %s"
          % (label, want, got, "OK " if ok else "FAIL", reason[:80]))

print("\n%d/%d passed" % (len(CASES) - fails, len(CASES)))
sys.exit(1 if fails else 0)
