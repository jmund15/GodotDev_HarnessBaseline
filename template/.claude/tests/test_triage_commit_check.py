"""Proof for hooks/triage_commit_check.py: a commit made under a session triage carries it as its `Triage:` trailer,
and every other commit passes silently.

A planted git repo in a temp root is the project, with a registry whose tag derives shared reach from `hook.py`, so a
case that stays silent while the diff outruns the trailer is a real silence. Each decision case calls the hook's
`main()` in this process through `_hook_call`; one spawned case proves the registered command still runs.
    python3 .claude/tests/test_triage_commit_check.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
HOOK = os.path.join(CLAUDE, "hooks", "triage_commit_check.py")
TASK_RECORD = os.path.join(CLAUDE, "tools", "task_record.py")
sys.path[:0] = [HERE, os.path.join(CLAUDE, "hooks"), os.path.join(CLAUDE, "tools")]
import _git_commit  # noqa: E402
import _hook_call  # noqa: E402
import triage  # noqa: E402

FAILURES = []
TMP = tempfile.mkdtemp(prefix="triage_commit_proof_")
PROJECT = os.path.join(TMP, "project")
OTHER = os.path.join(TMP, "other")
STATE = os.path.join(TMP, "state")
RECORDS = os.path.join(TMP, "records")
ENV = dict(os.environ, CLAUDE_PROJECT_DIR=PROJECT, HARNESS_HOOK_STATE_DIR=STATE, HARNESS_TASK_RECORD_DIR=RECORDS,
           PYTHONIOENCODING="utf-8")
OWN = "Triage: reach=local design=settled grounding=verified oracle=none size=one-context"
REGISTRY = {"sensitivity_tags": [{"id": "hook", "paths": ["hook.py"], "sets": {"reach": "shared"}, "source": "proof"}]}


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.email=p@example.invalid", "-c", "user.name=proof"] + list(args),
                   capture_output=True, text=True, check=True)


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_repo(root, registry=True):
    os.makedirs(root)
    git(root, "init", "-q")
    if registry:
        write(root, ".claude/skills/project_subsystems/adaptation.json", json.dumps(REGISTRY))
    write(root, "hook.py", "x = 1\n")
    write(root, "notes.md", "A proof note.\n")
    write(root, ".claude/rules/example.md", "A rule.\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")


def run(command, session="proof-triage-1", cwd=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or PROJECT, "session_id": session}
    code, out, err, _ = _hook_call.call(HOOK, payload, env=ENV, cwd=cwd or PROJECT)
    output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
    return code, out, err, output


def rewritten(output):
    return output.get("updatedInput", {}).get("command", "")


def trailers_of(command):
    return [t for call in _git_commit.commit_invocations(command, PROJECT) for t in _git_commit.commit_trailer_lines(call.rest)]


def record(session, reach="shared"):
    env = dict(ENV, CLAUDE_CODE_SESSION_ID=session)
    subprocess.run([sys.executable, TASK_RECORD, "open", session, "--session", session, "--title", session],
                   capture_output=True, text=True, encoding="utf-8", env=env, check=True)
    done = subprocess.run([sys.executable, TASK_RECORD, "triage", session, "--reach", reach, "--reversibility",
                           "clean", "--design", "settled", "--grounding", "verified", "--oracle", "gate+new-test",
                           "--size", "milestones", "--paths", "hook.py", "notes.md"],
                          capture_output=True, text=True, encoding="utf-8", env=env)
    assert done.returncode == 0, done.stderr


MESSAGE_FILE = os.path.join(TMP, "message.txt").replace("\\", "/")
FILE_COMMAND = 'git commit -q -F "%s"' % MESSAGE_FILE


def message(text=None):
    if text is not None:
        write(TMP, "message.txt", text)
    with open(MESSAGE_FILE, encoding="utf-8") as fh:
        return fh.read()


make_repo(PROJECT)
make_repo(OTHER)
os.makedirs(STATE)
os.makedirs(RECORDS)
write(PROJECT, "hook.py", "x = 2\n")  # derives shared reach, above every trailer this proof writes
write(OTHER, "hook.py", "x = 2\n")
write(PROJECT, ".claude/rules/example.md", "A rule that changed.\n")
git(PROJECT, "add", "-A")
git(OTHER, "add", "-A")

# --- no session triage: the check says nothing, whatever the diff derives ---------------------------------
for label, command in (
        ("a commit with no trailer and no record", 'git commit -q -m "proof note"'),
        ("a trailer below the diff's derived reach", 'git commit -q -m "proof note" -m "%s"' % OWN),
        ("an unreadable trailer", 'git commit -q -m "proof note" -m "Triage: reach=bogus"')):
    rc, out, err, output = run(command)
    check(label + " passes silently: a triage the diff outruns is `triage.py rederive`'s at close, not this hook's",
          rc == 0 and out.strip() == "{}", out + err)
message("proof note\n")
rc, out, err, output = run(FILE_COMMAND)
check("a -F commit with no trailer and no record is left as written and its file untouched: no reader parses a derived trailer",
      rc == 0 and out.strip() == "{}" and message() == "proof note\n", (out, message()))

# --- a session triage travels with the commit -------------------------------------------------------------
record("proof-triage-1")
rc, out, err, output = run('git commit -q -m "proof note"')
written = trailers_of(rewritten(output))
fields, error = triage.parse_trailer("proof note\n\n" + (written[0] if written else ""))
check("a -m commit under a session triage gets that triage as one --trailer, and no advice",
      rc == 0 and len(written) == 1 and error is None and fields["reach"] == "shared" and fields["size"] == "milestones"
      and "additionalContext" not in output, (out, err))

message("proof note\n")
rc, out, err, output = run(FILE_COMMAND)
check("a -F commit gets its trailer in the message file and its command is not rewritten: auto mode denies a call a hook changed",
      rc == 0 and "updatedInput" not in output and message().startswith("proof note\n\nTriage: reach=shared"), (out, message()))
once = message()
run(FILE_COMMAND)
check("a second run adds no second trailer", message() == once, message())

message("proof note\n\nClaude-Session: https://example.invalid/s\n")
run(FILE_COMMAND)
lines = message().splitlines()
check("a -F trailer joins an existing trailer block",
      lines[2].startswith("Claude-Session:") and lines[3].startswith("Triage: reach=shared") and len(lines) == 4, message())

rc, out, err, output = run('git commit -q -F "%s"' % os.path.join(TMP, "absent.txt").replace("\\", "/"))
check("a -F file not readable yet takes the --trailer rewrite, the only route left",
      rc == 0 and len(trailers_of(rewritten(output))) == 1, out + err)

for label, command in (
        ("-m", 'git commit -q -m "proof note" -m "%s"' % OWN),
        ("--trailer", 'git commit -q -m "proof note" --trailer "%s"' % OWN)):
    rc, out, err, output = run(command)
    check("a commit that carries its own trailer by %s keeps it and gets none added" % label,
          rc == 0 and out.strip() == "{}", out)
message("proof note\n\n%s\n" % OWN)
before = message()
rc, out, err, output = run(FILE_COMMAND)
check("a -F file that carries its own trailer is left as written", out.strip() == "{}" and message() == before, (out, message()))

rc, out, err, output = run("git merge --no-edit side")
check("a merge gets no trailer: it authors no triage of its own", rc == 0 and out.strip() == "{}", out)
rc, out, err, output = run('git commit -q -m "proof note"', cwd=OTHER)
check("a commit in another repository holding its own .claude/ stays silent", rc == 0 and out.strip() == "{}", out + err)
rc, out, err, output = run("ls -la")
check("a non-commit command passes silently", rc == 0 and out.strip() == "{}", out + err)

# --- launcher shapes: each nested commit keeps its own trailer --------------------------------------------
record("launcher", reach="shared")
for command, expected in (
        ("bash -c 'git commit -m nested -- notes.md'", [True]),
        ("bash -c 'git commit -m nested -- notes.md'; git commit -m direct -- hook.py", [True, True]),
        ('env sh -c "git commit -m nested -- notes.md"; git commit -m direct -- hook.py', [True, True]),
        ('bash -c "sh -c \'git commit -m nested -- notes.md\'"; git commit -m direct -- hook.py', [True, True]),
        ("eval 'git commit -m nested -- notes.md'; git commit -m direct -- hook.py", [True, True]),
        ("bash -n -c 'git commit -m ignored -- notes.md'; git commit -m direct -- hook.py", [True]),
        ("bash -c 'git com'\"mit -m nested -- notes.md\"; git commit -m direct -- hook.py", [False, True]),
        ("BODY='git commit -m nested -- notes.md'; bash -c \"$BODY\"; git commit -m direct -- hook.py", [False, True])):
    rc, out, err, output = run(command, session="launcher")
    calls = _git_commit.commit_invocations(rewritten(output), PROJECT)
    got = [bool(_git_commit.commit_trailer_lines(call.rest)) for call in calls]
    original = [call.rest for call in _git_commit.commit_invocations(command, PROJECT)]
    stripped = [[a for a in call.rest if a != "--trailer" and not a.startswith("Triage: ")] for call in calls]
    check("a launcher shape rewrites only the commits it can place, and no other argument: " + command,
          rc == 0 and got == expected and stripped == original, (got, expected, rewritten(output)))

spawned = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": "Bash", "cwd": PROJECT, "session_id": "s",
                         "tool_input": {"command": "ls -la"}}), capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=ENV)
check("the registered command imports and exits 0 from its own path",
      spawned.returncode == 0 and spawned.stdout.strip() == "{}" and spawned.stderr == "", (spawned.stdout, spawned.stderr))

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
