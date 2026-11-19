"""Proof for hooks/rule_number_guard.py: a commit whose changed rule text holds a number with no row is warned.

Fixtures are planted git repos under the system temp directory with a planted `instruction_scope`,
removed on teardown. Each case feeds a real PreToolUse payload and classifies the result: exit 2 is a
deny, exit 0 with `additionalContext` a warn, a bare exit 0 an allow, exit 1 an error, anything else
(or a traceback) a CRASH. The decision cases call the guard's `main()` in process (`_hook_call`); one
case spawns it, proving it imports and runs from its registered command.

    python3 .claude/tests/test_rule_number_guard.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "hooks"))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
GUARD = os.path.join(HOOKS, "rule_number_guard.py")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HOOKS)
import _hook_call  # noqa: E402

SCOPE = {"paths": [".claude/rules/*.md"], "rows_dir": ".claude/instruction_rows"}
SENTENCE = "Use two seats."


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def write(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def mkrepo(tmp, name, scope=SCOPE):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    if scope is not None:
        write(repo, ".claude/skills/project_subsystems/adaptation.json", json.dumps({"instruction_scope": scope}))
    write(repo, ".claude/rules/x.md", "# X\n\nIntro sentence.\n")
    write(repo, "docs/y.md", "# Y\n\nIntro sentence.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def repo_from(template, dest):
    """A fresh copy of the built seed repo, `.git` included, so a case starts from the same commits."""
    _hook_call.copy_repo(template, dest)
    return dest


def add_row(repo):
    import extract_numbers as en
    norm = en.norm(SENTENCE)
    r = {"path": ".claude/rules/x.md", "hash": en.sentence_hash(norm), "token": "two seats", "sentence": norm,
         "kind": "non-rule", "class": "structure", "note": "t", "source": "test"}
    write(repo, ".claude/instruction_rows/numbers.jsonl", json.dumps(r) + "\n")


def run_guard(repo, command):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": repo}
    code, out, err, hso = _hook_call.call(GUARD, payload, cwd=repo)
    return classify(code, out, err, hso)


def classify(code, out, err, hso):
    if code not in (0, 1, 2) or "Traceback" in err:
        return "CRASH", err
    if code == 0 and hso.get("additionalContext"):
        return "warn", hso["additionalContext"]
    return {0: "allow", 1: "error", 2: "deny"}[code], err


_ABSENT = object()


def run_guard_without_extract_numbers(repo, command):
    """The guard's own import of `extract_numbers` fails: `sys.modules` holds None for it."""
    saved = sys.modules.get("extract_numbers", _ABSENT)
    sys.modules["extract_numbers"] = None
    try:
        return run_guard(repo, command)
    finally:
        if saved is _ABSENT:
            sys.modules.pop("extract_numbers", None)
        else:
            sys.modules["extract_numbers"] = saved


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="rule_number_guard_")
    try:
        scoped = os.path.join(tmp, "_scoped")
        mkrepo(tmp, "_scoped")
        unscoped = os.path.join(tmp, "_unscoped")
        mkrepo(tmp, "_unscoped", scope=None)
        n = [0]

        def repo(scope=True):
            n[0] += 1
            return repo_from(scoped if scope else unscoped, os.path.join(tmp, "r%d" % n[0]))

        r = repo()
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence. %s\n" % SENTENCE)
        git(r, "add", ".claude/rules/x.md")
        verdict, err = run_guard(r, "git commit -m x")
        cases.append(("index commit: an unrowed number in scope is warned, never denied, naming the rule and the fix",
                      verdict == "warn" and "no row" in err and "instruction_quality §1" in err and "stub --staged" in err))
        add_row(r)
        verdict, _ = run_guard(r, "git commit -m x")
        cases.append(("index commit: the same number with a row is allowed", verdict == "allow"))

        r = repo()
        write(r, "docs/y.md", "# Y\n\nIntro sentence. %s\n" % SENTENCE)
        git(r, "add", "docs/y.md")
        cases.append(("an out-of-scope file's number is allowed", run_guard(r, "git commit -m x")[0] == "allow"))

        r = repo(scope=False)
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence. %s\n" % SENTENCE)
        git(r, "add", ".claude/rules/x.md")
        cases.append(("a repo with no instruction_scope is not judged", run_guard(r, "git commit -m x")[0] == "allow"))

        r = repo()
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence. %s\n" % SENTENCE)
        verdict, _ = run_guard(r, "git commit -m x -- .claude/rules/x.md")
        cases.append(("pathspec commit: the unstaged working-tree number it publishes is warned", verdict == "warn"))
        write(r, "docs/y.md", "# Y\n\nChanged.\n")
        git(r, "add", "docs/y.md")
        verdict, _ = run_guard(r, "git commit -m x")
        cases.append(("index commit: an unstaged number the commit does not publish is allowed", verdict == "allow"))
        cases.append(("a command that only mentions git commit is allowed",
                      run_guard(r, 'git log --grep="git commit"')[0] == "allow"))

        r = repo()
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence. %s\n" % SENTENCE)
        cases.append(("include commit: the unstaged working-tree number `-i` stages is warned",
                      run_guard(r, "git commit -i -m x -- .claude/rules/x.md")[0] == "warn"))
        write(r, "list.txt", ".claude/rules/x.md\n")
        cases.append(("pathspec-from-file commit: an already-warned number stays silent",
                      run_guard(r, "git commit -m x --pathspec-from-file=list.txt")[0] == "allow"))

        r = repo()
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence.\n")
        write(r, "docs/y.md", "# Y\n\nStaged.\n")
        git(r, "add", "docs/y.md")
        write(r, "docs/y.md", "# Y\n\nStaged and edited again.\n")
        verdict, err = run_guard(r, "git commit -i -m x -- .claude/rules/x.md")
        cases.append(("include commit: a staged path that differs from its working copy is warned as unreadable",
                      verdict == "warn" and err.strip()))

        r = repo()
        write(r, ".claude/rules/x.md", "# X\n\nIntro sentence. %s\n" % SENTENCE)
        git(r, "add", ".claude/rules/x.md")
        verdict, err = run_guard_without_extract_numbers(r, "git commit -m x")
        cases.append(("a failed extract_numbers import exits 1 without blocking and names the crash",
                      verdict == "error" and "rule_number_guard" in err))

        spawned = subprocess.run([sys.executable, GUARD], capture_output=True, text=True, encoding="utf-8",
                                 input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": r}))
        cases.append(("the guard imports and exits 0 from its registered command",
                      spawned.returncode == 0 and not spawned.stderr.strip()))
    finally:
        _rm(tmp)

    import pre_bash_dispatch
    cases.append(("the Bash dispatcher runs the guard",
                  "rule_number_guard" in [getattr(m, "__name__", "") for m, _ in pre_bash_dispatch.HOOKS]))
    cases.append(("the dispatcher never fails closed on it", not getattr(pre_bash_dispatch.rule_number_guard, "FAIL_CLOSED", False)))

    failed = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print(("PASS " if ok else "FAIL ") + label)
    print("%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
