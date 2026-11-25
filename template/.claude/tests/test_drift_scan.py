"""Proof for tools/drift_scan.py: a removed string literal that a consumer file still holds is reported.

Fixtures are planted git repos under the system temp directory, each with its own planted adaptation file.

    python3 .claude/tests/test_drift_scan.py
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "tools", "drift_scan.py"))
ADAPT = ".claude/skills/project_subsystems/adaptation.json"
CFG = {"sources": ["src/**"], "consumers": ["e2e/**"], "min_length": 4, "ignore": ["^data-"]}


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


def mkrepo(tmp, name, cfg, src, spec):
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    if cfg is not None:
        write(repo, ADAPT, json.dumps({"drift_scan": cfg}))
    write(repo, "src/Page.tsx", src)
    write(repo, "e2e/page.spec.ts", spec)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "work")
    return repo


def run(repo, *args):
    r = subprocess.run([sys.executable, TOOL, "--repo", repo, *args], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


SRC = '<button aria-label="Add key">Add key</button>\nconst x = "ab";\nconst y = "data-old";\nconst k = \'Keep label\';\n'
SPEC = "await page.getByRole('button', { name: 'Add key' }).click();\nawait x('ab'); x('data-old'); x('Keep label');\n"


def main():
    tmp = tempfile.mkdtemp(prefix="drift_scan_")
    cases = []
    try:
        repo = mkrepo(tmp, "a", CFG, SRC, SPEC)
        write(repo, "src/Page.tsx", '<button aria-label="New key">New key</button>\nconst x = "cd";\nconst y = "data-new";\nconst k = \'Keep label\';\n')
        git(repo, "commit", "-q", "-am", "rename")
        code, out = run(repo)
        cases.append(("renamed label still in a consumer is reported",
                      code == 1 and 'drift: "Add key" left src/Page.tsx; still in e2e/page.spec.ts:1' in out))
        cases.append(("summary line printed", "drift_scan: 1 literal" in out))
        cases.append(("label kept in the added lines is not reported", "Keep label" not in out))
        cases.append(("literal shorter than min_length is not reported", '"ab"' not in out))
        cases.append(("literal matching ignore is not reported", "data-old" not in out))
        code2, out2 = run(repo, "--paths", "src/Other.tsx")
        cases.append(("--paths limits the sources", code2 == 0 and "drift:" not in out2))
        diff = git(repo, "diff", "main...HEAD")
        write(repo, "scratch.diff", diff)
        code3, out3 = run(repo, "--diff-file", os.path.join(repo, "scratch.diff"))
        cases.append(("--diff-file gives the same report", code3 == 1 and 'drift: "Add key"' in out3))
        write(repo, "e2e/page.spec.ts", "await x('Keep label');\n")
        git(repo, "commit", "-q", "-am", "spec updated")
        code4, out4 = run(repo)
        cases.append(("a consumer that dropped the label is clean", code4 == 0 and "drift:" not in out4))

        repo3 = mkrepo(tmp, "c", CFG, "const a = 'Task';\n", "")
        write(repo3, "e2e/page.spec.ts",
              "x('Tasks'); x('Task list'); y(`Task force`);\n"
              "x(\"Task\");\n"
              "x(`Task`);\n"
              "x(/Task/i);\n"
              "x(/^Task$/);\n"
              "x('Task ');\n")
        git(repo3, "add", "-A")
        git(repo3, "commit", "-q", "-m", "spec")
        write(repo3, "src/Page.tsx", "const a = 'Job';\n")
        git(repo3, "commit", "-q", "-am", "rename")
        code7, out7 = run(repo3)
        cases.append(("a longer quoted string holding the literal is not a hit", "e2e/page.spec.ts:1\n" not in out7))
        cases.append(("a double-quoted, backtick and regex literal that is the whole text each hit",
                      all("e2e/page.spec.ts:%d\n" % n in out7 for n in (2, 3, 4, 5, 6))))
        cases.append(("the summary counts only the whole-string lines", "5 consumer line(s)" in out7))

        repo2 = mkrepo(tmp, "b", None, SRC, SPEC)
        write(repo2, "src/Page.tsx", "nothing\n")
        git(repo2, "commit", "-q", "-am", "x")
        code5, out5 = run(repo2)
        cases.append(("no config exits 0 with the not-configured line", code5 == 0 and "drift_scan: not configured" in out5))

        code6, out6 = run(repo, "--bogus")
        cases.append(("a bad argument refuses with accepts and example",
                      code6 == 2 and "accepts:" in out6 and "example:" in out6))
    finally:
        _rm(tmp)
    failed = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(("PASS " if ok else "FAIL ") + n)
    print("%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
