#!/usr/bin/env python3
"""Re-runnable proof for hooks/repeat_fix_notice.py.

The hook decides on a file's own counted fix history (`mechanism_census.fix_history`) at the first edit of
it in a compaction window: two or more counted fixes in the window name the file, its latest fix
subjects and the clause's pointer. Each case plants a real git repository and a payload for the file.

    python3 .claude/tests/test_repeat_fix_notice.py
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOK = os.path.join(CLAUDE, "hooks", "repeat_fix_notice.py")
TMP = tempfile.mkdtemp(prefix="repeat_fix_notice_")
os.environ["HARNESS_HOOK_STATE_DIR"] = os.path.join(TMP, "state")  # the one state redirect every hook honours
os.makedirs(os.environ["HARNESS_HOOK_STATE_DIR"])
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import repeat_fix_notice as hook  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def git(repo, *args, date=None):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                          "-c", "commit.gpgsign=false", "-C", repo, *args],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, "git %s: %s" % (" ".join(args), out.stderr.strip())
    return out.stdout.strip()


def commit(repo, files, subject, days_ago=0):
    for rel, text in files.items():
        full = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    git(repo, "add", "-A")
    when = (NOW - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject, date=when)


def fixed(repo, rel, subjects, days_ago=0):
    commit(repo, {rel: "base\n"}, "chore: base " + rel, days_ago=days_ago + 1)
    for i, subject in enumerate(subjects):
        commit(repo, {rel: "round %d\n" % i}, subject, days_ago=days_ago)


def payload(path, session, tool="Edit", **extra):
    return {"tool_name": tool, "session_id": session, "tool_input": {"file_path": path, "new_string": "x"}, **extra}


def context(result):
    return (result or {}).get("context") or ""


def main():
    repo = os.path.join(TMP, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    twice = ".claude/hooks/twice.py"
    once = ".claude/hooks/once.py"
    old = ".claude/hooks/old.py"
    scoped = ".claude/hooks/scoped.py"
    redone = ".claude/hooks/redone.py"
    proof = ".claude/tests/test_twice.py"
    fixed(repo, old, ["fix(hooks): ancient one", "fix(hooks): ancient two"], days_ago=60)  # oldest first: git's --since stops at a skewed date
    fixed(repo, twice, ["fix(hooks): first defect in twice", "fix(hooks): second defect in twice"])
    fixed(repo, once, ["fix(hooks): only defect"])
    fixed(repo, scoped, ["fix(review): a", "fix(audit): b", "fix(fold): c"])
    fixed(repo, proof, ["fix(tests): a", "fix(tests): b"])
    fixed(repo, redone, ["fix(hooks): r1", "fix(hooks): r2", "fix(hooks): r3"])
    commit(repo, {redone: "new\n", ".claude/plans/redone.md": "plan\n"},
           "refactor(hooks): redone\n\nRedesigns: %s\nPlan: .claude/plans/redone.md" % redone)
    nested = "template/.claude/hooks/nested.py"  # the baseline's own layout: the checkout root sits below the git root
    fixed(repo, nested, ["fix(hooks): nested one", "fix(hooks): nested two"])
    commit(repo, {"Scripts/Thing.cs": "a\n"}, "fix: one")
    commit(repo, {"Scripts/Thing.cs": "b\n"}, "fix: two")
    path = lambda rel: os.path.join(repo, *rel.split("/"))  # noqa: E731

    cases = []
    ctx = context(hook.process(payload(path(twice), "s-twice")))
    cases.append(("a file with two counted fixes in the window is named with its fix subjects and the clause pointer",
                  twice in ctx and "second defect" in ctx and "first defect" in ctx and "mechanism_litmus" in ctx
                  and "2" in ctx))
    cases.append(("the same path again in the same window is silent",
                  hook.process(payload(path(twice), "s-twice")) is None))
    cases.append(("a subagent's first edit of the path gets its own notice",
                  twice in context(hook.process(payload(path(twice), "s-twice", agent_id="agent-7")))))
    cases.append(("a Write names it too",
                  twice in context(hook.process(payload(path(twice), "s-write", tool="Write")))))
    cases.append(("a checkout root below the git root still reads its own history",
                  ".claude/hooks/nested.py" in context(hook.process(payload(path(nested), "s-nested")))))
    cases.append(("one counted fix is silent", hook.process(payload(path(once), "s-once")) is None))
    cases.append(("fixes older than the window are not counted", hook.process(payload(path(old), "s-old")) is None))
    cases.append(("review, audit and fold fixes count like any other", scoped in context(hook.process(payload(path(scoped), "s-scoped")))))
    cases.append(("a redesign commit with its plan restarts the count",
                  hook.process(payload(path(redone), "s-redone")) is None))
    import mechanism_census as census  # the census owns the floor and the window; the hook follows them
    floor, window = census.MIN_REPEAT_FIXES, census.DEFAULT_WINDOW_DAYS
    try:
        census.MIN_REPEAT_FIXES = 3
        raised = hook.process(payload(path(twice), "s-floor")) is None
        census.MIN_REPEAT_FIXES = floor
        census.DEFAULT_WINDOW_DAYS = 90
        widened = old in context(hook.process(payload(path(old), "s-window")))
    finally:
        census.MIN_REPEAT_FIXES, census.DEFAULT_WINDOW_DAYS = floor, window
    cases.append(("the repeat floor is the census's", raised))
    cases.append(("the fix window is the census's", widened))
    cases.append(("a proof file is not a runtime harness file", hook.process(payload(path(proof), "s-proof")) is None))
    cases.append(("a path outside .claude is silent, whatever its history",
                  hook.process(payload(path("Scripts/Thing.cs"), "s-cs")) is None))
    cases.append(("an untracked new file has no history and is silent",
                  hook.process(payload(path(".claude/hooks/new.py"), "s-new")) is None))
    cases.append(("a path under a directory that does not exist is silent",
                  hook.process(payload(os.path.join(repo, "nope", ".claude", "hooks", "x.py"), "s-gone")) is None))
    cases.append(("a Read is not an edit", hook.process(payload(path(twice), "s-read", tool="Read")) is None))
    for label, bad in (("an empty payload", {}), ("a non-dict tool_input", {"tool_name": "Edit", "tool_input": "x"})):
        try:
            ok = hook.process(bad) is None
        except Exception:
            ok = False
        cases.append((label + " does not crash process()", ok))

    env = dict(os.environ)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload(path(twice), "s-main")),
                       capture_output=True, text=True, timeout=60, env=env, encoding="utf-8", errors="replace")
    try:
        specific = json.loads(r.stdout).get("hookSpecificOutput") or {}
        ok = (r.returncode == 0 and specific.get("hookEventName") == "PreToolUse"
              and twice in (specific.get("additionalContext") or ""))
    except ValueError:
        ok = False
    cases.append(("the registered command emits the notice as PreToolUse additionalContext", ok))
    r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True, timeout=60,
                       env=env, encoding="utf-8", errors="replace")
    cases.append(("the registered command exits 0 silently on malformed stdin",
                  r.returncode == 0 and r.stdout.strip() in ("", "{}") and "Traceback" not in r.stderr))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
