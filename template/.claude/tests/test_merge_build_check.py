#!/usr/bin/env python3
"""Proof: a merge whose tree changes C# builds before its commit exists (githooks/pre-merge-commit, pre-commit).

Two lane branches can each compile while their textual merge does not, and `git merge --no-edit` commits a clean merge
at once. Each case builds a throwaway repo carrying the real hooks, with a stub build command in place of dotnet
(`HARNESS_MERGE_BUILD_CMD`) that logs each call and exits `STUB_RC`. The editor-live probe is planted through
`HARNESS_LIVE_EDITOR_CHECKOUTS`. Real git runs every merge and commit.

    python3 .claude/tests/test_merge_build_check.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAILS = []
ERROR_LINE = "Foo.cs(143,34): error CS7036: There is no argument given that corresponds to the required parameter"
STUB = (
    "import os, sys\n"
    "open(os.environ['STUB_LOG'], 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
    "if os.environ.get('STUB_RC', '0') != '0':\n"
    "    print(%r)\n"
    "    print(%r)\n"
    "    sys.exit(1)\n"
) % (ERROR_LINE, ERROR_LINE)


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


def git(repo, *args, env=None, check_rc=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=dict(os.environ, GIT_AUTHOR_NAME="proof", GIT_AUTHOR_EMAIL="proof@example.invalid",
                                                  GIT_COMMITTER_NAME="proof", GIT_COMMITTER_EMAIL="proof@example.invalid",
                                                  **(env or {})))
    if check_rc and r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r


def write(repo, name, text):
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo, name, text, msg):
    write(repo, name, text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", msg)


def make_repo(tmp, live=False):
    """A repo carrying the real hooks, a root csproj, a stub build and a base commit; returns (repo, env, log)."""
    tmp = Path(tmp)
    repo = tmp / "repo"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    shutil.copytree(ROOT / ".claude" / "githooks", repo / ".claude" / "githooks",
                    ignore=shutil.ignore_patterns("__pycache__", "commit-msg"))
    for name in ("merge_build_check.py", "editor_live_build_guard.py"):
        src = ROOT / ".claude" / "hooks" / name
        if src.is_file():
            shutil.copy2(src, repo / ".claude" / "hooks" / name)
    stub = tmp / "stub_build.py"
    stub.write_text(STUB, encoding="utf-8", newline="\n")
    log = tmp / "build.log"
    env = {"HARNESS_MERGE_BUILD_CMD": "%s %s" % (Path(sys.executable).as_posix(), stub.as_posix()),
           "STUB_LOG": str(log), "STUB_RC": "0",
           "HARNESS_LIVE_EDITOR_CHECKOUTS": str(repo) if live else ""}
    git(repo, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false"),
                       ("core.hooksPath", ".claude/githooks")):
        git(repo, "config", key, value)
    write(repo, "Game.csproj", "<Project/>\n")
    write(repo, "a.cs", "class A {}\n")
    write(repo, "n.txt", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "base")
    return repo, env, log


def calls(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


def parents(repo):
    return len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) - 1


def head(repo):
    return git(repo, "rev-parse", "HEAD").stdout


def diverge(repo, side_file, side_text, main_file="n.txt", main_text="main\n"):
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, side_file, side_text, "side")
    git(repo, "checkout", "-q", "main")
    commit(repo, main_file, main_text, "main")


def run_merge(repo, env, rc="0", *extra):
    return git(repo, "merge", "--no-edit", *extra, "side", env=dict(env, STUB_RC=rc), check_rc=False)


def main():
    if not (ROOT / ".claude" / "githooks" / "pre-merge-commit").is_file():
        check(False, ".claude/githooks/pre-merge-commit exists")
        return 1
    tmp = tempfile.mkdtemp(prefix="merge_build_check_")
    try:
        repo, env, log = make_repo(Path(tmp) / "a")
        diverge(repo, "b.cs", "class B {}\n")
        r = run_merge(repo, env)
        check(r.returncode == 0 and parents(repo) == 2 and calls(log) == 1,
              "a clean C# merge builds once and commits (rc=%d, parents=%d, builds=%d) %s"
              % (r.returncode, parents(repo), calls(log), r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "b")
        diverge(repo, "b.cs", "class B {}\n")
        before = head(repo)
        r = run_merge(repo, env, "1")
        errs = [line for line in r.stderr.splitlines() if "error CS7036" in line]
        check(r.returncode != 0 and head(repo) == before and len(errs) == 1
              and "git commit" in r.stderr and "--no-verify" in r.stderr
              and git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check_rc=False).returncode == 0,
              "a failing build refuses the merge commit, prints the error once and leaves the merge in progress "
              "(rc=%d, errs=%d) %s" % (r.returncode, len(errs), r.stderr[-300:]))
        r = git(repo, "commit", "--no-edit", env=dict(env, STUB_RC="1"), check_rc=False)
        check(r.returncode != 0 and head(repo) == before,
              "git commit while MERGE_HEAD exists runs the same build and refuses (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))
        r = git(repo, "commit", "--no-edit", env=dict(env, STUB_RC="0"), check_rc=False)
        check(r.returncode == 0 and parents(repo) == 2,
              "must-allow: the fixed tree commits and finishes the merge (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "c")
        diverge(repo, "b.cs", "class B {}\n")
        r = run_merge(repo, env, "1", "--no-verify")
        check(r.returncode == 0 and calls(log) == 0,
              "--no-verify bypasses the build (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "d")
        diverge(repo, "s.txt", "side\n")
        r = run_merge(repo, env, "1")
        check(r.returncode == 0 and calls(log) == 0 and parents(repo) == 2,
              "must-allow: a merge with no C# path builds nothing (rc=%d, builds=%d) %s"
              % (r.returncode, calls(log), r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "e")
        write(repo, "c.cs", "class C {}\n")
        git(repo, "add", "c.cs")
        r = git(repo, "commit", "-q", "-m", "plain commit", env=dict(env, STUB_RC="1"), check_rc=False)
        check(r.returncode == 0 and calls(log) == 0,
              "must-allow: pre-commit with no MERGE_HEAD builds nothing, a C# path included (rc=%d, builds=%d) %s"
              % (r.returncode, calls(log), r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "f", live=True)
        diverge(repo, "b.cs", "class B {}\n")
        before = head(repo)
        r = run_merge(repo, env)
        check(r.returncode != 0 and calls(log) == 0 and head(repo) == before
              and "editor" in r.stderr and "--no-verify" in r.stderr,
              "a live editor on the checkout refuses a C# merge without building in place (rc=%d, builds=%d) %s"
              % (r.returncode, calls(log), r.stderr[-300:]))

        repo, env, log = make_repo(Path(tmp) / "g", live=True)
        diverge(repo, "s.txt", "side\n")
        r = run_merge(repo, env)
        check(r.returncode == 0 and calls(log) == 0,
              "must-allow: a non-C# merge is untouched while the editor is live (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "h")
        git(repo, "checkout", "-q", "-b", "side")
        git(repo, "update-index", "--add", "--cacheinfo", "160000,%s,Jmodot" % ("1" * 40))
        git(repo, "commit", "-q", "--no-verify", "-m", "bump")
        git(repo, "checkout", "-q", "main")
        commit(repo, "n.txt", "main\n", "main")
        before = head(repo)
        r = run_merge(repo, env)
        check(r.returncode != 0 and calls(log) == 0 and head(repo) == before
              and "Jmodot" in r.stderr and "submodule update" in r.stderr,
              "a merge that moves the Jmodot pointer counts as C# and is refused while the checkout is not at it "
              "(rc=%d, builds=%d) %s" % (r.returncode, calls(log), r.stderr[-300:]))

        for label, at_new, want_rc in (("is at the moved pointer", True, 0), ("is behind the moved pointer", False, 1)):
            repo, env, log = make_repo(Path(tmp) / ("k%d" % want_rc))
            sub = Path(tmp) / ("sub%d" % want_rc)
            sub.mkdir()
            git(sub, "init", "-q", "-b", "main")
            for key, value in (("user.email", "t@t"), ("user.name", "t")):
                git(sub, "config", key, value)
            commit(sub, "j.cs", "class J {}\n", "j1")
            old = git(sub, "rev-parse", "HEAD").stdout.strip()
            git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "Jmodot")
            git(repo, "commit", "-q", "--no-verify", "-m", "add Jmodot")
            git(repo, "checkout", "-q", "-b", "side")
            commit(repo / "Jmodot", "j.cs", "class J2 {}\n", "j2")
            git(repo, "add", "Jmodot")
            git(repo, "commit", "-q", "--no-verify", "-m", "bump Jmodot")
            git(repo, "checkout", "-q", "main")
            commit(repo, "n.txt", "main\n", "main")
            if not at_new:
                git(repo / "Jmodot", "checkout", "-q", "--detach", old)
            r = run_merge(repo, env)
            check(r.returncode == want_rc and calls(log) == (1 if want_rc == 0 else 0),
                  "a real Jmodot checkout that %s: %s (rc=%d, builds=%d) %s"
                  % (label, "built and committed" if want_rc == 0 else "refused", r.returncode, calls(log), r.stderr[-300:]))

        repo, env, log = make_repo(Path(tmp) / "i")
        diverge(repo, "n.txt", "side\n")
        run_merge(repo, env)
        write(repo, "n.txt", "resolved\n")
        git(repo, "add", "n.txt")
        r = git(repo, "merge", "--continue", env=dict(env, GIT_EDITOR="true"), check_rc=False)
        check(r.returncode == 0 and parents(repo) == 2 and calls(log) == 0,
              "must-allow: merge --continue after a conflict with no C# change (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        repo, env, log = make_repo(Path(tmp) / "j")
        diverge(repo, "b.cs", "class B {}\n")
        wt = Path(tmp) / "j" / "wt"
        git(repo, "worktree", "add", "-q", "-b", "fresh", str(wt), "main")
        r = git(wt, "merge", "--no-edit", "side", env=env, check_rc=False)
        check(r.returncode == 0 and parents(wt) == 2 and calls(log) == 1,
              "must-allow: a C# merge in a fresh worktree builds that worktree and commits (rc=%d, builds=%d) %s"
              % (r.returncode, calls(log), r.stderr[-200:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d failed" % len(FAILS) if FAILS else "all passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
