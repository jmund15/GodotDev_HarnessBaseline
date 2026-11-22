"""Proof for hooks/triage_commit_check.py: an unreadable Triage trailer is advised; a commit with no trailer gets a derived one; other commands pass silently.

A planted git repo in a temp root is the project. Each decision case calls the hook's `main()` in this
process through `_hook_call`; one spawned case proves the registered command still runs and exits 0.
    python3 .claude/tests/test_triage_commit_check.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "hooks", "triage_commit_check.py")
sys.path.insert(0, HERE)
import _hook_call  # noqa: E402

FAILURES = []
PROJECT = tempfile.mkdtemp(prefix="triage_commit_proof_")
STATE = tempfile.mkdtemp(prefix="triage_commit_state_")
RECORDS = tempfile.mkdtemp(prefix="triage_commit_records_")
ENV = dict(os.environ, CLAUDE_PROJECT_DIR=PROJECT, HARNESS_HOOK_STATE_DIR=STATE, HARNESS_TASK_RECORD_DIR=RECORDS)


def git(*args):
    subprocess.run(["git", "-C", PROJECT] + list(args), capture_output=True, text=True, check=True)


def run(command, tool="Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": PROJECT, "session_id": "proof-triage-1"}
    code, out, err, _ = _hook_call.call(HOOK, payload, env=ENV, cwd=PROJECT)
    return code, out, err


def spawned(command):
    """The registered command: one spawned run proves the hook imports and exits 0 from settings.json."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": PROJECT, "session_id": "proof-triage-1"}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=ENV)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


git("init", "-q")
git("config", "user.email", "proof@example.invalid")
git("config", "user.name", "proof")
with open(os.path.join(PROJECT, "notes.md"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("A proof note.\n")
git("add", "--", "notes.md")

rc, out, err = run('git commit -q -m "proof note"')
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a commit with no trailer and no record is left as written: nothing reads a derived trailer, and a rewrite costs a turn in auto mode",
      rc == 0 and "updatedInput" not in output and "Triage-Derived" not in out, out + err)

rc, out, err = run('git commit -q -m "proof note" -m "Triage: reach=bogus"')
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("an unreadable Triage trailer is advised as unreadable",
      rc == 0 and "trailer unreadable" in output.get("additionalContext", ""), out + err)

MESSAGE_FILE = os.path.join(STATE, "message.txt").replace("\\", "/")


def _write_message(text):
    with open(MESSAGE_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _read_message():
    with open(MESSAGE_FILE, encoding="utf-8") as fh:
        return fh.read()


_write_message("proof note\n")
command = 'git commit -q -F "%s"' % MESSAGE_FILE
rc, out, err = run(command)
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a -F commit leaves the reviewed command unchanged", rc == 0 and "updatedInput" not in output, out + err)
check("a -F commit gets its Triage-Derived trailer appended into the message file",
      _read_message() == "proof note\n\nTriage-Derived: reach=local oracle=none\n", _read_message())
rc, out, err = run(command)
check("a second -F run adds no second trailer", _read_message().count("Triage-Derived:") == 1, _read_message())

_write_message("proof note\n\nClaude-Session: https://example.invalid/s\n")
run(command)
check("a -F trailer joins an existing trailer block",
      _read_message() == "proof note\n\nClaude-Session: https://example.invalid/s\nTriage-Derived: reach=local oracle=none\n",
      _read_message())

missing = os.path.join(STATE, "absent.txt").replace("\\", "/")
rc, out, err = run('git commit -q -F "%s"' % missing)
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a -F file that is not readable yet keeps the -m-style command rewrite",
      rc == 0 and "Triage-Derived:" in output.get("updatedInput", {}).get("command", ""), out + err)


rc, out, err = run("ls -la")
check("a non-commit command passes silently", rc == 0 and out.strip() == "{}", out + err)

rc, out, err = spawned("ls -la")
check("the registered command runs and exits 0", rc == 0 and out.strip() == "{}" and err == "", out + err)


sys.path[:0] = [os.path.dirname(HOOK), os.path.join(os.path.dirname(os.path.dirname(HOOK)), "tools")]
import triage  # noqa: E402
import triage_commit_check as tcc  # noqa: E402


class _NoTags:
    valid = []
    invalid = []


DIFF = "diff --git a/notes.md b/notes.md\n--- a/notes.md\n+++ b/notes.md\n@@ -0,0 +1 @@\n+A proof note.\n"


def _advice(message, reach=None):
    """The pure verdict for a one-file diff string. No tag is loaded, so `reach` stands in for the reach a tag derives."""
    changes = triage._changes(triage._name_status("A\0notes.md\0"), lambda: DIFF, lambda p: None)
    derived = triage.derive(changes, _NoTags())
    derived = triage.Result(reach or derived.reach, derived.oracle, derived.matches, derived.invalid)
    fields, error = triage.parse_trailer(message) if message is not None else (None, None)
    return tcc.triage_verdict(derived, fields, error, message, "commit", lambda: None)


advice, trailer = _advice("proof note")
check("a diff with no trailer and local reach gets no advice and no trailer",
      advice is None and trailer is None, (advice, trailer))
advice, trailer = _advice("proof note\n\nTriage: reach=bogus")
check("a diff with an unreadable trailer is advised as unreadable",
      advice is not None and "trailer unreadable" in advice and trailer is None, (advice, trailer))
advice, trailer = _advice("proof note\n\nTriage: reach=local design=settled grounding=verified oracle=none size=one-context", reach="external")
check("a diff deriving external reach over a local trailer raises the triage",
      advice is not None and tcc.RAISE_HEAD in advice and "reach local -> external" in advice, advice)
advice, trailer = _advice("proof note\n\nTriage: reach=external design=settled grounding=verified oracle=none size=one-context", reach="external")
check("a diff whose derived reach the trailer already records raises nothing",
      advice is None and trailer is None, (advice, trailer))

RECORDED = {"reach": "shared", "reversibility": "clean", "design": "settled", "grounding": "verified",
            "oracle": ["gate", "new-test"], "size": "milestones", "kind": "decision"}
with open(os.path.join(RECORDS, "proof-task.json"), "w", encoding="utf-8", newline="\n") as fh:
    json.dump({"task_id": "proof-task", "session_id": "proof-triage-1", "updated": "2026-01-01T00:00:00Z",
               "triage": {"fields": RECORDED, "paths": ["notes.md"]}}, fh)
rc, out, err = run('git commit -q -m "proof note"')
command = (json.loads(out).get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
           if out.strip() else "")
written = command.partition('--trailer "')[2].partition('"')[0]
fields, error = triage.parse_trailer("proof note\n\n" + written)
check("a commit with no trailer under a session triage carries that triage as its Triage trailer",
      rc == 0 and written.startswith("Triage: ") and error is None and fields is not None
      and fields.get("reach") == "shared" and fields.get("size") == "milestones", (command, err))
rc, out, err = run('git commit -q -m "proof note" -m "Triage: reach=local design=settled grounding=verified oracle=none size=one-context"')
check("a commit that writes its own Triage trailer keeps it and gets none added",
      rc == 0 and "--trailer" not in (out or ""), out)
_write_message("proof note\n")
rc, out, err = run('git commit -q -F "%s"' % MESSAGE_FILE)
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a -F commit under a session triage gets its Triage trailer in the file, command unchanged",
      rc == 0 and "updatedInput" not in output and "\nTriage: " in _read_message()
      and "Triage-Derived:" not in _read_message(), (_read_message(), out, err))
os.remove(os.path.join(RECORDS, "proof-task.json"))


class _Adm:
    admitted = True


def _premises_note(message, session):
    saved = (tcc.triage.admits, tcc.commit_message)
    tcc.triage.admits = lambda *a, **k: _Adm()
    tcc.commit_message = lambda rest, cwd: message
    try:
        inv = type("Inv", (), {"rest": [], "cwd": PROJECT, "git_env": None})()
        return tcc._premises(inv, session, None)
    finally:
        tcc.triage.admits, tcc.commit_message = saved


note = _premises_note("x\n\n```premises\nthis is not json\n```\n", "premises-bad-1") or ""
check("a premises block that is not one JSON object per line is named malformed, not unavailable",
      "malformed" in note and "evidence_contract.md" in note and "unavailable" not in note, note)
import verify_claims  # noqa: E402
orig_check = verify_claims.check_claims
verify_claims.check_claims = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    note = _premises_note('x\n\n```premises\n{"claim": "c"}\n```\n', "premises-crash-2") or ""
finally:
    verify_claims.check_claims = orig_check
check("a crash inside verify_claims still reads as check unavailable",
      "check unavailable: RuntimeError" in note, note)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
