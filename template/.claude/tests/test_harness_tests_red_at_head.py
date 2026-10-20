"""Re-runnable proof for scripts/harness_tests.py run_scoped: a bound proof that is also red at HEAD
is a pre-existing red and does not block the stamp when its failure-marked lines are non-empty and all appear
at HEAD; one green at HEAD, absent there, failing differently, or showing no failure line still blocks.
HARNESS_NO_HEAD_RERUN skips the optional HEAD comparison. A HEAD run resolves the repo from the HEAD checkout, through
cwd and CLAUDE_PROJECT_DIR alike.

    python3 .claude/tests/test_harness_tests_red_at_head.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def proof(exit_code, note=""):
    return "import sys\nprint(%r)\nsys.exit(%d)\n" % (note, exit_code)


def rooted_proof(how):
    """Red when the checkout it resolves (by CLAUDE_PROJECT_DIR or cwd) carries the working-tree edit."""
    base = "os.environ['CLAUDE_PROJECT_DIR']" if how == "env" else "os.getcwd()"
    return ("import os, sys\ntext = open(os.path.join(%s, '.claude', 'hooks', 'delta.py')).read()\n"
            "print('saw edit' if 'Y = 2' in text else 'no edit')\nsys.exit(1 if 'Y = 2' in text else 0)\n" % base)


def write(repo, rel, text):
    path = os.path.join(repo, ".claude", *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


NAMES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")


def make_repo():
    repo = tempfile.mkdtemp(prefix="harnessredhead_")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=repo, check=True)
    for name in NAMES:
        write(repo, "hooks/%s.py" % name, "X = 1\n")
    write(repo, "tests/test_alpha.py", proof(1, "FAIL: same failure"))
    write(repo, "tests/test_zeta.py", proof(1, "FAIL: old failure"))
    write(repo, "tests/test_delta.py", rooted_proof("env"))
    write(repo, "tests/test_epsilon.py", rooted_proof("cwd") + "# epsilon.py\n")
    write(repo, "tests/test_beta.py", proof(0, "green at head"))
    subprocess.run(["git", "add", "-A", "-f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    harness_tests.EXCLUDED = {}
    harness_tests.HEAVY = {}
    harness_tests.PROOF_BINDINGS = {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    for attr, name in (("DURATIONS_PATH", "durations.json"), ("FLAKY_LOG", "flaky.jsonl")):
        if hasattr(harness_tests, attr):
            setattr(harness_tests, attr, os.path.join(repo, name))
    return repo


def run_case():
    repo = make_repo()
    harness_tests._write_stamp(repo, 2, 2, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    for name in NAMES:
        with open(os.path.join(repo, ".claude", "hooks", name + ".py"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("Y = 2\n")
    write(repo, "tests/test_alpha.py", proof(1, "FAIL: same failure"))
    write(repo, "tests/test_zeta.py", proof(1, "FAIL: new failure"))
    write(repo, "tests/test_beta.py", proof(1, "red in the working tree"))
    write(repo, "tests/test_gamma.py", proof(1, "new, absent at HEAD"))
    buf = io.StringIO()
    old_cwd, old_env = os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR")
    os.chdir(repo)
    os.environ["CLAUDE_PROJECT_DIR"] = repo
    try:
        with contextlib.redirect_stdout(buf):
            rc = harness_tests.run_scoped(repo, [".claude/hooks/%s.py" % n for n in NAMES])
    finally:
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old_env
    out = buf.getvalue()
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    return repo, rc, out, before, after, harness_tests.tree_entries(repo)


def lines_proof(lines):
    return "import sys\nprint(%r)\nsys.exit(1)\n" % "\n".join(lines)


def scenario(head_lines, work_lines, env=None, commit=False, base=False):
    """One hook and one proof, red in the working tree; `head_lines` is the proof's output at HEAD.
    `commit` commits the change, so HEAD carries it; `base` passes the seed commit as --base.
    Returns (input stamped, output, HEAD reruns started, registered worktree count)."""
    repo = tempfile.mkdtemp(prefix="harnessredhead_")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=repo, check=True)
    write(repo, "hooks/alpha.py", "X = 1\n")
    write(repo, "tests/test_alpha.py", lines_proof(head_lines))
    subprocess.run(["git", "add", "-A", "-f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    harness_tests.EXCLUDED, harness_tests.HEAVY, harness_tests.PROOF_BINDINGS = {}, {}, {}
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    harness_tests._write_stamp(repo, 1, 1, [])
    seed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    write(repo, "hooks/alpha.py", "X = 1\nY = 2\n")
    write(repo, "tests/test_alpha.py", lines_proof(work_lines))
    if commit:
        subprocess.run(["git", "add", "-A", "-f"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)
    reruns = []
    real_reds_at = harness_tests._reds_at
    harness_tests._reds_at = lambda *a, **k: (reruns.append(1), real_reds_at(*a, **k))[1]
    buf = io.StringIO()
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    os.chdir(repo)
    os.environ.update({"CLAUDE_PROJECT_DIR": repo}, **(env or {}))
    try:
        with contextlib.redirect_stdout(buf):
            harness_tests.run_scoped(repo, [".claude/hooks/alpha.py"], base=seed if base else None)
    finally:
        harness_tests._reds_at = real_reds_at
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    stamped = after.get(".claude/hooks/alpha.py") == harness_tests.tree_entries(repo)[".claude/hooks/alpha.py"]
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True,
                            text=True).stdout
    return stamped, buf.getvalue(), len(reruns), listed.count("worktree ")


def main():
    try:
        old =["FAIL: test_old (suite.Case)", "AssertionError: 1 != 2", "FAILED (failures=1)"]
        stamped, out, _, _ = scenario(old, ["FAIL: test_new (suite.Case)", "AssertionError: 1 != 2",
                                            "FAILED (failures=1)"])
        check("(g) the same count tail over a different failing test blocks", not stamped, out[-600:])
        stamped, out, _, _ = scenario(old, ["FAIL: test_new (suite.Case)"] + old)
        check("(h) a new failure printed before the old one blocks even under an equal last line",
              not stamped, out[-600:])
        stamped, out, _, _ = scenario(["8 cases"], ["8 cases"])
        check("(i) an output with no failure-marked line never excuses", not stamped, out[-600:])
        stamped, out, _, _ = scenario(["TIMEOUT after 300s"], ["TIMEOUT after 300s"])
        check("(i) a timeout tail never excuses", not stamped, out[-600:])
        stamped, out, _, _ = scenario(["FAIL: test_old (suite.Case) 1.20s", "ERROR: C:/Temp/harness_proof_ab12cd/x.py",
                                       "FAIL id 0xdeadbeef run 4ee3c043"],
                                      ["FAIL: test_old (suite.Case) 3.40s", "ERROR: C:/Temp/harness_proof_ff99ee/x.py",
                                       "FAIL id 0xcafef00d run 9a1b2c3d"])
        check("(j) failure lines equal after durations, temp paths and hex ids are normalized excuse",
              stamped and "pre-existing red" in out, out[-600:])
        stamped, out, _, _ = scenario(old + ["FAIL: test_gone (suite.Case)"], old)
        check("(j) a worktree failure set that is a subset of HEAD's excuses", stamped, out[-600:])
        stamped, out, reruns, worktrees = scenario(old, old, env={"HARNESS_NO_HEAD_RERUN": "1"})
        check("(k) with HARNESS_NO_HEAD_RERUN a red bound proof stays red and no HEAD checkout starts",
              not stamped and reruns == 0 and worktrees == 1 and "HEAD rerun skipped" in out,
              "stamped=%s reruns=%s worktrees=%s out=%r" % (stamped, reruns, worktrees, out[-600:]))
        stamped, out, reruns, _ = scenario(old, old, commit=True)
        check("(l) a change already committed at HEAD excuses no red: HEAD carries it, and the line names --base",
              not stamped and reruns == 0 and "--base" in out, "stamped=%s reruns=%s out=%r" % (stamped, reruns, out[-600:]))
        stamped, out, _, _ = scenario(old, old, commit=True, base=True)
        check("(l) --base names the revision before the change: a red there too is pre-existing",
              stamped and "pre-existing red" in out, out[-600:])
    except Exception as exc:
        check("scenarios", False, "%s: %s" % (type(exc).__name__, exc))
    try:
        repo = make_repo()
        proof = os.path.join(repo, ".claude", "tests", "test_alpha.py")
        seen = {}
        def launch(path, root=None, **_kw):
            seen["root"] = root
            return "fail", 0.0, "FAIL: seed tree absent (%s/runs) and %s" % (root.replace("\\", "/"), root)
        real_launch, harness_tests._launch = harness_tests._launch, launch
        try:
            detail = harness_tests._reds_at(repo, "HEAD", [proof]).get(proof, "")
        finally:
            harness_tests._launch = real_launch
        checkout = seen.get("root") or "?"
        check("(m) a failure line naming the comparison checkout reads as the repo's own root, in either slash form",
              checkout not in detail and checkout.replace("\\", "/") not in detail
              and repo.replace("\\", "/") + "/runs" in detail and repo in detail, (checkout, detail))
    except Exception as exc:
        check("root mapping", False, "%s: %s" % (type(exc).__name__, exc))
    try:
        repo, rc, out, before, after, cur = run_case()
        stamped = lambda n: after.get(".claude/hooks/%s.py" % n) == cur[".claude/hooks/%s.py" % n]
        log = os.path.join(repo, "harness_known_red.jsonl")
        rows = [json.loads(line) for line in open(log, encoding="utf-8")] if os.path.exists(log) else []
        line = [l for l in out.splitlines() if "pre-existing red at HEAD" in l and "test_alpha.py" in l]
        check("(a) a proof red at HEAD and in the working tree stamps its input and prints the pre-existing line",
              stamped("alpha") and bool(line), "stamped=%s out=%r" % (stamped("alpha"), out[-600:]))
        check("(a) the pre-existing red is logged with proof, head and both tails",
              len(rows) == 1 and rows[0].get("proof") == "test_alpha.py"
              and {"ts", "head", "tail_worktree", "tail_head"} <= set(rows[0]), rows)
        check("(b) a proof green at HEAD but red in the working tree leaves its input unstamped",
              not stamped("beta"), out[-600:])
        check("(c) a red proof absent at HEAD leaves its input unstamped", not stamped("gamma"), out[-600:])
        check("(e) a proof green at a HEAD it resolves through CLAUDE_PROJECT_DIR blocks its input",
              not stamped("delta"), out[-600:])
        check("(e) a proof green at a HEAD it resolves through cwd blocks its input",
              not stamped("epsilon"), out[-600:])
        check("(f) a proof red at HEAD with a different last line blocks its input and says so",
              not stamped("zeta") and "fails differently" in out, out[-600:])
        check("run still fails: the blocked inputs are red", rc == 1, "rc=%s" % rc)
        listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True,
                                text=True).stdout
        check("(d) no temp worktree stays registered", listed.count("worktree ") == 1, listed)
    except Exception as exc:
        check("run_case", False, "%s: %s" % (type(exc).__name__, exc))
    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
