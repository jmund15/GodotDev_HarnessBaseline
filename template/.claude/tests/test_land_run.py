"""Proof: land_run.ps1 lands a proven land/<name> branch on main, and refuses what it must not land. A commit
main already holds lands as a no-op; the land branch is deleted only while its tip is still the landed commit; and
the land workflow has no concurrency group (a group keeps one pending run and cancels the rest, dropping a land).

Runs the real script in a temp clone whose origin is a temp bare repo; the harness close is a stub
that prints a green close for the checked-out commit, so the run never needs the real proof suite.

    python3 .claude/tests/test_land_run.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "land_run.ps1")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

ENV_PROBES = ("LAND_USER_PROBE", "LAND_PROJECT_PROBE", "LAND_BOTH", "HOME",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
STUB_CLOSE = '''import json, os, subprocess
with open("close_env.json", "w") as fh:
    json.dump({k: os.environ.get(k) for k in %r}, fh)
if os.path.exists("close_hooks.txt"):
    hooks = open("close_hooks.txt").read().splitlines()
    if hooks:
        for argv in json.loads(hooks[0]):
            subprocess.run(argv, check=True)
        open("close_hooks.txt", "w").write("".join(h + "\\n" for h in hooks[1:]))
if os.path.exists("close_incomplete.txt"):
    left = int(open("close_incomplete.txt").read())
    if left > 0:
        open("close_incomplete.txt", "w").write(str(left - 1))
        print("harness_tests: close incomplete: 1 paths, 1 proofs, 0 new red, 0 need uncommitted inputs, 0 pre-existing red")
        raise SystemExit(2)
# Pre-existing red lines cut from the real land log (land_42727930589.log) with the history clause the close adds.
if os.path.exists("close_pre_existing.txt"):
    print("harness_tests: pre-existing red at base a04b1e39: test_instruction_quality_split.py (first recorded red 2026-11-09, 5 earlier closes)")
    print("harness_tests: pre-existing red at base a04b1e39: workflow_amend_line_test.js (no earlier row in harness_known_red.jsonl)")
if os.path.exists("close_red.txt"):
    print("harness_tests: close red: 1 paths, 1 proofs, 1 new red, 0 need uncommitted inputs, 2 pre-existing red")
    raise SystemExit(1)
sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
# Lines the real close prints while it works, cut from a land run's log.
print("harness_tests: close: 436 selected proof(s), ~694s at recorded times on 12 job(s), 4 with no recorded time (unknown)")
print("PASS 2.7s test_dispatch_size.py")
print("harness_tests: 24 of 436 proofs done (0 reused), 61s; ~633s left at recorded times, pace x1.0")
print("harness_tests: classifying red 1 of 3: rerun test_dispatch_size.py at a04b1e39")
print("harness_tests: close green: 1 paths, 1 proofs, 0 new red")
print("harness_tests: push with: git push origin %%s:main" %% sha)
''' % (ENV_PROBES,)
LOCK_DRIVER = "python3 .claude/tools/lock_merge.py %O %A %B"
LOCK_BASE = {
    ".gitattributes": ".claude/baseline.lock.json merge=baseline-lock\n",
    ".claude/tools/lock_merge.py": open(os.path.join(REPO, ".claude", "tools", "lock_merge.py"),
                                        encoding="utf-8").read(),
    ".claude/baseline.lock.json": json.dumps({"files": {}}, indent=2) + "\n",
}


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="\n") as fh:
        fh.write(text)


def make(tmp, changed="notes.md", base_files=None, text="change\n"):
    origin = os.path.join(tmp, "origin.git")
    work = os.path.join(tmp, "work")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    subprocess.run(["git", "clone", "-q", origin, work], check=True, capture_output=True)
    os.makedirs(os.path.join(work, ".claude", "scripts"))
    shutil.copy(SCRIPT, os.path.join(work, ".claude", "scripts", "land_run.ps1"))
    with open(os.path.join(work, ".claude", "scripts", "harness_tests.py"), "w", newline="\n") as fh:
        fh.write(STUB_CLOSE)
    with open(os.path.join(work, ".claude", "settings.json"), "w", newline="\n") as fh:
        json.dump({"env": {"LAND_PROJECT_PROBE": "p", "LAND_BOTH": "project"}}, fh)
    for rel, body in (base_files or {}).items():
        write(os.path.join(work, rel), body)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "base", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    with open(os.path.join(work, changed), "a", newline="\n") as fh:
        fh.write(text)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "change", cwd=work)
    sha = git("rev-parse", "HEAD", cwd=work)
    git("push", "-q", "origin", "HEAD:refs/heads/land/t1", cwd=work)
    return origin, work, sha


def land(work, sha, branch, env=None):
    # The runner service has no git identity: the script runs with an empty global config, as it does there.
    no_identity = os.path.join(os.path.dirname(work), "no_identity.gitconfig")
    open(no_identity, "a").close()
    env = dict(os.environ if env is None else env, GIT_CONFIG_GLOBAL=no_identity)
    p = subprocess.run(["pwsh", "-NoProfile", "-File", os.path.join(work, ".claude", "scripts", "land_run.ps1"),
                        "-Sha", sha, "-Branch", branch], cwd=work, capture_output=True, text=True, timeout=120,
                       env=env)
    return p.returncode, p.stdout + p.stderr


def push_peer(origin, tmp, edits, message):
    """Moves origin/main by one commit from a second clone that writes `edits`; returns the new main."""
    peer = os.path.join(tmp, "peer_%s" % message)
    git("clone", "-q", origin, peer, cwd=tmp)
    for rel, text in edits.items():
        write(os.path.join(peer, rel), text)
    git("add", "-A", cwd=peer)
    git("commit", "-q", "-m", message, cwd=peer)
    git("push", "-q", "origin", "HEAD:main", cwd=peer)
    return git("rev-parse", "HEAD", cwd=peer)


def main():
    failures = []

    def check(label, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + str(detail)[-400:] if detail else ""))

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        rc, out = land(work, sha, "land/t1")
        main_now = git("rev-parse", "main", cwd=origin)
        check("a green close lands the proven commit on main", rc == 0 and main_now == sha
              and "LAND_RESULT=green" in out, out)
        branches = git("branch", "--list", "land/*", cwd=origin)
        check("... and deletes the land branch", branches == "", branches)
        check("... and the land log carries every line the close prints about its own progress, and no per-proof pass",
              "proofs done (0 reused)" in out and "classifying red 1 of 3" in out and "436 selected" in out
              and "PASS 2.7s" not in out, out)
        check("a green land whose close excused no pre-existing red carries no pre-existing field",
              "pre-existing=" not in out, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        write(os.path.join(work, "close_pre_existing.txt"), "")
        rc, out = land(work, sha, "land/t1")
        check("a green land names the close's pre-existing reds, how many and which, on its result line",
              rc == 0 and "LAND_RESULT=green" in out
              and "pre-existing=2 test_instruction_quality_split.py,workflow_amend_line_test.js" in out, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        write(os.path.join(work, "close_pre_existing.txt"), "")
        write(os.path.join(work, "close_red.txt"), "")
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "land/t1")
        check("a red close names the same pre-existing reds on its result line, and main stays put",
              rc == 1 and "LAND_RESULT=red stage=close" in out and "pre-existing=2 " in out
              and "workflow_amend_line_test.js" in out and git("rev-parse", "main", cwd=origin) == before, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "main")
        check("a branch outside land/ is refused before main moves",
              rc == 1 and "LAND_RESULT=red stage=args" in out and git("rev-parse", "main", cwd=origin) == before, out)
        rc, out = land(work, "not-a-sha", "land/t1")
        check("a malformed sha is refused before main moves",
              rc == 1 and "LAND_RESULT=red stage=args" in out and git("rev-parse", "main", cwd=origin) == before, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp, changed="project.godot")
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "land/t1")
        check("a change outside the harness owes the gate (here absent, so it stays red)",
              rc == 1 and "LAND stage=gate" in out and "skipped=1" not in out
              and git("rev-parse", "main", cwd=origin) == before, out)

    for ignore_file in (".gitignore", ".searchignore"):
        with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
            origin, work, sha = make(tmp, changed=ignore_file)
            rc, out = land(work, sha, "land/t1")
            check("an ignore file no build or test reads (%s) skips the gate" % ignore_file,
                  rc == 0 and "skipped=1" in out and git("rev-parse", "main", cwd=origin) == sha, out)

    # The base attributes line every checkout reads; a change keeps it and adds below it.
    attrs = {".gitattributes": "* text=auto eol=lf\n"}
    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp, changed=".gitattributes", base_files=attrs,
                                 text=".claude/baseline.lock.json merge=baseline-lock\n# harness merge drivers\n")
        rc, out = land(work, sha, "land/t1")
        check("a .gitattributes change whose every line names a .claude/ path skips the gate",
              rc == 0 and "skipped=1" in out and git("rev-parse", "main", cwd=origin) == sha, out)
    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp, changed=".gitattributes", base_files=attrs,
                                 text=".claude/baseline.lock.json merge=baseline-lock\n*.cs eol=crlf\n")
        rc, out = land(work, sha, "land/t1")
        check("a .gitattributes line outside .claude/ owes the gate: it can change the bytes a build checks out",
              rc == 1 and "LAND stage=gate" in out and "skipped=1" not in out
              and git("rev-parse", "main", cwd=origin) != sha, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        git("push", "-q", "origin", sha + ":refs/heads/main", cwd=work)
        rc, out = land(work, sha, "land/t1")
        check("a commit main already holds lands green as a no-op, without a close",
              rc == 0 and "LAND_RESULT=green" in out and "already-on-main=1" in out and "stage=close" not in out, out)
        branches = git("branch", "--list", "land/*", cwd=origin)
        check("... and deletes its land branch", branches == "", branches)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        with open(os.path.join(work, "later.md"), "w", newline="\n") as fh:
            fh.write("later\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "later", cwd=work)
        newer = git("rev-parse", "HEAD", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/t1", cwd=work)
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "land/t1")
        check("a land whose branch was pushed again is superseded by the newer push, without a close",
              rc == 0 and git("rev-parse", "main", cwd=origin) == before and "LAND_RESULT=superseded" in out
              and "by=land/t1" in out and "stage=close" not in out, out)
        check("... and keeps the branch at the newer push for its own run",
              git("for-each-ref", "--format=%(objectname)", "refs/heads/land/t1", cwd=origin) == newer and "branch-delete=kept" in out, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        with open(os.path.join(work, "later.md"), "w", newline="\n") as fh:
            fh.write("later\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "later", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/t2", cwd=work)
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "land/t1")
        check("a land another land branch already contains is superseded by it, without a close",
              rc == 0 and git("rev-parse", "main", cwd=origin) == before and "LAND_RESULT=superseded" in out
              and "by=land/t2" in out and "stage=close" not in out, out)
        check("... and deletes its own land branch",
              git("branch", "--list", "land/t1", cwd=origin) == "", out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        git("checkout", "-q", "--detach", "HEAD~1", cwd=work)
        with open(os.path.join(work, "other.md"), "w", newline="\n") as fh:
            fh.write("other\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "other", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/t3", cwd=work)
        git("checkout", "-q", "--detach", sha, cwd=work)
        rc, out = land(work, sha, "land/t1")
        check("a land branch that does not contain the commit supersedes nothing",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        user_claude = os.path.join(tmp, "userhome", ".claude")
        os.makedirs(user_claude)
        with open(os.path.join(user_claude, "settings.json"), "w", newline="\n") as fh:
            json.dump({"env": {"LAND_USER_PROBE": "u", "LAND_BOTH": "user"}}, fh)
        env = {k: v for k, v in os.environ.items() if k not in ENV_PROBES}
        env["USERPROFILE"] = os.path.join(tmp, "userhome")
        rc, out = land(work, sha, "land/t1", env=env)
        try:
            with open(os.path.join(work, "close_env.json"), encoding="utf-8") as fh:
                seen = json.load(fh)
        except OSError:
            seen = {}
        check("the close runs with the user settings env a session gets", seen.get("LAND_USER_PROBE") == "u", seen)
        check("... and the project settings env, which overrides the user's",
              seen.get("LAND_PROJECT_PROBE") == "p" and seen.get("LAND_BOTH") == "project", seen)
        check("... and HOME, set from USERPROFILE when the service has none",
              seen.get("HOME") == env["USERPROFILE"], seen)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        git("checkout", "-q", "--detach", "HEAD~1", cwd=work)
        with open(os.path.join(work, "notes.md"), "w", newline="\n") as fh:
            fh.write("a prior run's byproduct\n")
        rc, out = land(work, sha, "land/t1")
        check("a prior run's leftover in the land worktree never blocks the checkout",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out, out)

    for side, key, program in (("fetch", "uploadpack", "git-upload-pack"), ("push", "receivepack", "git-receive-pack")):
        with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
            origin, work, sha = make(tmp)
            flaky = os.path.join(tmp, "flaky_%s.sh" % key).replace("\\", "/")
            marker = os.path.join(tmp, "failed_once").replace("\\", "/")
            with open(flaky, "w", newline="\n") as fh:
                fh.write('if [ ! -f "%s" ]; then touch "%s"; echo "Recv failure: Connection was reset" >&2; exit 128; fi\n'
                         'exec %s "$@"\n' % (marker, marker, program))
            git("config", "remote.origin." + key, "sh " + flaky, cwd=work)
            rc, out = land(work, sha, "land/t1")
            check("a %s that drops its connection once is retried, and the land goes green" % side,
                  rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out
                  and "LAND retry" in out, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        moved = push_peer(origin, tmp, {"peer.md": "peer\n"}, "peer")
        env = {k: v for k, v in os.environ.items() if k not in ENV_PROBES}
        rc, out = land(work, sha, "land/t1", env=env)
        landed = git("rev-parse", "main", cwd=origin)
        check("a branch cut before main moved, with disjoint changes, lands as its rebased tip",
              rc == 0 and landed != sha and git("rev-parse", "main^", cwd=origin) == moved
              and "LAND_RESULT=green" in out and ("landed=" + landed) in out, out)
        try:
            with open(os.path.join(work, "close_env.json"), encoding="utf-8") as fh:
                seen = json.load(fh)
        except OSError:
            seen = {}
        check("a rebase's committer identity does not reach the close",
              seen.get("GIT_COMMITTER_NAME") is None and seen.get("GIT_COMMITTER_EMAIL") is None, seen)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp)
        moved = push_peer(origin, tmp, {"notes.md": "elsewhere\n"}, "peer")
        rc, out = land(work, sha, "land/t1")
        check("a branch that conflicts with the moved main on a non-lock file stops at stage=rebase, leaves main unmoved, names the path",
              rc == 1 and "LAND_RESULT=red stage=rebase" in out and "notes.md" in out
              and git("rev-parse", "main", cwd=origin) == moved, out)

    with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
        origin, work, sha = make(tmp, base_files=LOCK_BASE)
        git("config", "merge.baseline-lock.driver", LOCK_DRIVER, cwd=work)
        base = git("rev-parse", "HEAD", cwd=work)
        write(os.path.join(work, ".claude", "baseline.lock.json"), json.dumps({"files": {"a.md": {"n": 1}}}) + "\n")
        git("commit", "-q", "-am", "row a", cwd=work)
        sha_a = git("rev-parse", "HEAD", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/ta", cwd=work)
        rc_a, out_a = land(work, sha_a, "land/ta")
        git("checkout", "-q", "--detach", base, cwd=work)
        write(os.path.join(work, ".claude", "baseline.lock.json"), json.dumps({"files": {"b.md": {"n": 2}}}) + "\n")
        git("commit", "-q", "-am", "row b", cwd=work)
        sha_b = git("rev-parse", "HEAD", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/tb", cwd=work)
        rc_b, out_b = land(work, sha_b, "land/tb")
        rows = json.loads(git("show", "main:.claude/baseline.lock.json", cwd=origin))["files"]
        check("two branches that each add a different lock row both land, the second rebased through the lock driver",
              rc_a == 0 and rc_b == 0 and sorted(rows) == ["a.md", "b.md"], out_a + out_b)

    for moves, want_green in ((1, True), (2, False)):
        with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
            origin, work, sha = make(tmp)
            peer = os.path.join(tmp, "peer")
            git("clone", "-q", origin, peer, cwd=tmp)
            # Each hook line is a JSON list of argv lists: the runner service has no `sh` on its PATH.
            hooks = [json.dumps([["git", "-C", peer, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                  "--allow-empty", "-m", "m%d" % n],
                                 ["git", "-C", peer, "push", "-q", "origin", "HEAD:main"]])
                     for n in range(1, moves + 1)]
            write(os.path.join(work, "close_hooks.txt"), "\n".join(hooks) + "\n")
            rc, out = land(work, sha, "land/t1")
            label = "once" if want_green else "twice"
            if want_green:
                parent = git("log", "-1", "--format=%s", "main^", cwd=origin)
                check("a main that moves once during the close is rebased and re-proved, and lands (%s)" % label,
                      rc == 0 and "LAND_RESULT=green" in out and parent == "m1"
                      and ("landed=" + git("rev-parse", "main", cwd=origin)) in out, out)
            else:
                check("a main that moves a second time during the re-proved close stops at stage=push (%s)" % label,
                      rc == 1 and "LAND_RESULT=red stage=push" in out
                      and git("log", "-1", "--format=%s", "main", cwd=origin) == "m2", out)

    for left, want_green in ((1, True), (2, False)):
        with tempfile.TemporaryDirectory(prefix="landrun_") as tmp:
            origin, work, sha = make(tmp)
            write(os.path.join(work, "close_incomplete.txt"), str(left))
            rc, out = land(work, sha, "land/t1")
            if want_green:
                check("a close left incomplete with no new red is run once more, and lands",
                      rc == 0 and "LAND_RESULT=green" in out and out.count("LAND stage=close") == 2, out)
            else:
                check("a close incomplete twice stops at stage=close after one rerun",
                      rc == 1 and "LAND_RESULT=red stage=close" in out and out.count("LAND stage=close") == 2, out)

    if failures:
        print("\n%d FAILED" % len(failures))
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print("\nall passed")


if __name__ == "__main__":
    main()
