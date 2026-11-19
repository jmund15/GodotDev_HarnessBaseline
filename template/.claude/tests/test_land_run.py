"""Proof: land_run.ps1 integrates a pushed land/<name> branch onto main and runs no proof. It fetches, rebases onto a
moved main (the lock driver resolves lock rows; any other conflict reds at stage=rebase), reads the submodules and
fast-forwards main, retrying a push that loses a race to a peer's push until it lands. A commit main already holds
lands as a no-op; the land branch is deleted only while its tip is still the landed commit; and the land workflow has
no concurrency group (a group keeps one pending run and cancels the rest, dropping a land).

Runs the real script in a temp clone whose origin is a temp bare repo. The harness close and the regression gate are
tripwires that fail the run and leave a marker if the script starts either, so a land that proves anything is caught.

    python3 .claude/tests/test_land_run.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _case_pool import run_cases  # noqa: E402

SCRIPT = os.path.join(HERE, "..", "scripts", "land_run.ps1")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

TRIPWIRE_CLOSE = '''import sys
open("close_ran.txt", "w").write("1")
print("harness_tests: close red: 1 paths, 1 proofs, 1 new red, 0 need uncommitted inputs, 2 pre-existing red")
raise SystemExit(1)
'''
TRIPWIRE_GATE = '''Set-Content -Path gate_ran.txt -Value 1
Write-Output 'VERDICT=FAIL'
exit 1
'''
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
        fh.write(TRIPWIRE_CLOSE)
    with open(os.path.join(work, ".claude", "scripts", "regression_gate.ps1"), "w", newline="\n") as fh:
        fh.write(TRIPWIRE_GATE)
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


# Each case builds its own temp origin and clone, so the pool runs them side by side in separate processes.
_FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label), flush=True)
    if not ok:
        _FAILURES.append(label + (": " + str(detail)[-400:] if detail else ""))


def case(fn):
    """A pool case: its checks all run, then any failed one raises."""
    def run():
        fn()
        if _FAILURES:
            raise AssertionError("; ".join(_FAILURES))
    run.__name__ = fn.__name__
    run.__qualname__ = fn.__name__
    return run


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="landrun_")




def proved_nothing(work, out):
    """True when neither tripwire ran: no close or gate stage line, no marker the stubs leave."""
    return ("stage=close" not in out and "stage=gate" not in out
            and not os.path.exists(os.path.join(work, "close_ran.txt"))
            and not os.path.exists(os.path.join(work, "gate_ran.txt")))


@case
def case_land_runs_no_close_or_gate():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp, changed="game.cs")
        rc, out = land(work, sha, "land/t1")
        check("a land of a non-harness change whose close would be red pushes main anyway",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out, out)
        check("... and its log shows no close or gate stage, and neither ran", proved_nothing(work, out), out)
        check("... and deletes the land branch", git("branch", "--list", "land/*", cwd=origin) == "")
        check("a green land carries no pre-existing field", "pre-existing=" not in out, out)


@case
def case_args_refused():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "main")
        check("a branch outside land/ is refused before main moves",
              rc == 1 and "LAND_RESULT=red stage=args" in out and git("rev-parse", "main", cwd=origin) == before, out)
        rc, out = land(work, "not-a-sha", "land/t1")
        check("a malformed sha is refused before main moves",
              rc == 1 and "LAND_RESULT=red stage=args" in out and git("rev-parse", "main", cwd=origin) == before, out)


@case
def case_commit_main_holds_is_noop():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        git("push", "-q", "origin", sha + ":refs/heads/main", cwd=work)
        rc, out = land(work, sha, "land/t1")
        check("a commit main already holds lands green as a no-op",
              rc == 0 and "LAND_RESULT=green" in out and "already-on-main=1" in out and proved_nothing(work, out), out)
        branches = git("branch", "--list", "land/*", cwd=origin)
        check("... and deletes its land branch", branches == "", branches)


@case
def case_same_branch_pushed_again_still_lands():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        with open(os.path.join(work, "later.md"), "w", newline="\n") as fh:
            fh.write("later\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "later", cwd=work)
        newer = git("rev-parse", "HEAD", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/t1", cwd=work)
        rc, out = land(work, sha, "land/t1")
        check("a land whose branch was pushed again still lands its own commit",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out
              and "superseded" not in out, out)
        check("... and keeps the branch at the newer push for its own run",
              git("for-each-ref", "--format=%(objectname)", "refs/heads/land/t1", cwd=origin) == newer
              and "branch-delete=kept" in out, out)
        check("a branch delete that loses its lease to a newer push is a rejection, not a dropped connection: no retry",
              "LAND retry" not in out, out)


@case
def case_stacked_branch_still_lands_its_base():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        with open(os.path.join(work, "later.md"), "w", newline="\n") as fh:
            fh.write("later\n")
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", "later", cwd=work)
        git("push", "-q", "origin", "HEAD:refs/heads/land/t2", cwd=work)
        rc, out = land(work, sha, "land/t1")
        check("a land whose sha another land branch's tip contains still lands itself",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out
              and "superseded" not in out, out)
        check("... and deletes its own land branch and leaves the stacked one",
              git("branch", "--list", "land/t1", cwd=origin) == ""
              and git("branch", "--list", "land/t2", cwd=origin) != "", out)


@case
def case_leftover_never_blocks_checkout():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        git("checkout", "-q", "--detach", "HEAD~1", cwd=work)
        with open(os.path.join(work, "notes.md"), "w", newline="\n") as fh:
            fh.write("a prior run's byproduct\n")
        rc, out = land(work, sha, "land/t1")
        check("a prior run's leftover in the land worktree never blocks the checkout",
              rc == 0 and git("rev-parse", "main", cwd=origin) == sha and "LAND_RESULT=green" in out, out)


def dropped_connection_retried(side, key, program):
    with tmpdir() as tmp:
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


@case
def case_fetch_drop_retried():
    dropped_connection_retried("fetch", "uploadpack", "git-upload-pack")


@case
def case_push_drop_retried():
    dropped_connection_retried("push", "receivepack", "git-receive-pack")


@case
def case_rebase_disjoint_lands():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        moved = push_peer(origin, tmp, {"peer.md": "peer\n"}, "peer")
        rc, out = land(work, sha, "land/t1")
        landed = git("rev-parse", "main", cwd=origin)
        check("a branch cut before main moved, with disjoint changes, lands as its rebased tip",
              rc == 0 and landed != sha and git("rev-parse", "main^", cwd=origin) == moved
              and "LAND_RESULT=green" in out and ("landed=" + landed) in out, out)


@case
def case_rebase_conflict_stops():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        moved = push_peer(origin, tmp, {"notes.md": "elsewhere\n"}, "peer")
        rc, out = land(work, sha, "land/t1")
        check("a branch that conflicts with the moved main on a non-lock file stops at stage=rebase, leaves main unmoved, names the path",
              rc == 1 and "LAND_RESULT=red stage=rebase" in out and "notes.md" in out
              and git("rev-parse", "main", cwd=origin) == moved, out)


@case
def case_lock_rows_both_land():
    with tmpdir() as tmp:
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


def lost_races(moves):
    """A land whose push loses to a peer's push `moves` times in a row: the receive-pack wrapper pushes one empty peer
    commit to main before it takes the land's push, so each such push is rejected at the update."""
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        peer = os.path.join(tmp, "peer")
        git("clone", "-q", origin, peer, cwd=tmp)
        peer_sh = peer.replace("\\", "/")
        count = os.path.join(tmp, "moves").replace("\\", "/")
        wrapper = os.path.join(tmp, "race.sh").replace("\\", "/")
        with open(wrapper, "w", newline="\n") as fh:
            fh.write('n=$(cat "%s" 2>/dev/null || echo 0)\n'
                     'if [ "$n" -lt %d ]; then\n'
                     '  n=$((n+1)); echo $n > "%s"\n'
                     '  git -C "%s" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "m$n"\n'
                     '  git -C "%s" push -q origin HEAD:main\n'
                     'fi\n'
                     'exec git-receive-pack "$@"\n' % (count, moves, count, peer_sh, peer_sh))
        git("config", "remote.origin.receivepack", "sh " + wrapper, cwd=work)
        rc, out = land(work, sha, "land/t1")
        def subject(rev):
            shown = subprocess.run(["git", "log", "-1", "--format=%s", rev], cwd=origin, capture_output=True, text=True)
            return shown.stdout.strip()
        return git("rev-parse", "main", cwd=origin), subject("main^"), subject("main"), sha, rc, out


@case
def case_push_losing_twice_lands_on_the_next_round():
    landed, parent, subject, sha, rc, out = lost_races(2)
    check("a push that loses twice to a peer's push at the update lands on the next round, green",
          rc == 0 and "LAND_RESULT=green" in out and landed != sha and ("landed=" + landed) in out, out)
    check("... as the branch's commit rebased onto the peer's last commit",
          parent == "m2" and subject == "change", (parent, subject))
    check("a push rejected by a moved main is not retried as a dropped connection", "LAND retry" not in out, out)
    check("... and the lost races name no red stage", "LAND_RESULT=red" not in out, out)


@case
def case_push_refused_with_main_unmoved_is_red():
    with tmpdir() as tmp:
        origin, work, sha = make(tmp)
        hook = os.path.join(origin, "hooks", "pre-receive")
        with open(hook, "w", newline="\n") as fh:
            fh.write('#!/bin/sh\nwhile read old new ref; do [ "$ref" = refs/heads/main ] && exit 1; done\nexit 0\n')
        os.chmod(hook, 0o755)
        before = git("rev-parse", "main", cwd=origin)
        rc, out = land(work, sha, "land/t1")
        check("a push the remote refuses while main has not moved stops at stage=push instead of retrying forever",
              rc == 1 and "LAND_RESULT=red stage=push" in out and git("rev-parse", "main", cwd=origin) == before, out)


CASES = [case_land_runs_no_close_or_gate, case_args_refused, case_commit_main_holds_is_noop,
         case_same_branch_pushed_again_still_lands, case_stacked_branch_still_lands_its_base,
         case_leftover_never_blocks_checkout, case_fetch_drop_retried, case_push_drop_retried,
         case_rebase_disjoint_lands, case_rebase_conflict_stops, case_lock_rows_both_land,
         case_push_losing_twice_lands_on_the_next_round, case_push_refused_with_main_unmoved_is_red]

PARTS = 3


def main(part=1):
    """Part `part` of PARTS: each case builds its own repos, so the cases split round-robin over
    processes that run beside each other. A missing part file fails the run, not silently drops cases."""
    for other in range(2, PARTS + 1):
        sibling = os.path.join(HERE, "test_land_run_part%d.py" % other)
        if not os.path.isfile(sibling):
            print("missing part %d: %s" % (other, sibling))
            return 1
    return run_cases(CASES[part - 1::PARTS])


if __name__ == "__main__":
    sys.exit(main())
