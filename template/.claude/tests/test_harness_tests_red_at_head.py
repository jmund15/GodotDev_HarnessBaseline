"""Re-runnable proof for scripts/harness_tests.py run_scoped: a bound proof that is also red at HEAD
is a pre-existing red and does not block the stamp when its failure-marked lines are non-empty and all appear
at HEAD; one green at HEAD, absent there, failing differently, or showing no failure line still blocks.
A HEAD run resolves the repo from the HEAD checkout, through cwd and CLAUDE_PROJECT_DIR alike.

The verdict (`_is_pre_existing`) and the run's flow over it (what is stamped, whether HEAD reruns, the judge the
run's retries read) are decided in process: the working-tree launch and the HEAD rerun (`_reds_at`) answer planted
outputs. The real boundary keeps one live run: real children in the working tree and in a real HEAD checkout, which
resolve their repo through cwd and CLAUDE_PROJECT_DIR, leave no registered worktree behind, and a failure line that
names the comparison checkout.

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
import _git_fixture  # noqa: E402
import _hook_call  # noqa: E402
from _case_pool import check, checked_cases, run_cases  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def _seed(path):
    _git_fixture.init_repo(path)
    for name in NAMES:
        write(path, "hooks/%s.py" % name, "X = 1\n")
    write(path, "tests/test_alpha.py", proof(1, "FAIL: same failure"))
    write(path, "tests/test_zeta.py", proof(1, "FAIL: old failure"))
    write(path, "tests/test_delta.py", rooted_proof("env"))
    write(path, "tests/test_epsilon.py", rooted_proof("cwd") + "# epsilon.py\n")
    write(path, "tests/test_beta.py", proof(0, "green at head"))
    subprocess.run(["git", "add", "-A", "-f"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True, capture_output=True)


def make_repo():
    """A copy of the seed repository (six hooks, five proofs), built once, with the runner's module state pointed at it."""
    repo = tempfile.mkdtemp(prefix="harnessredhead_")
    _hook_call.copy_repo(str(_git_fixture.built("red_at_head.seed", _seed)), repo)
    harness_tests.EXCLUDED = {}
    harness_tests.HEAVY = {}
    harness_tests.PROOF_BINDINGS = {}
    harness_tests.KNOWN_RED_LOG = None
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    for attr, name in (("DURATIONS_PATH", "durations.json"), ("FLAKY_LOG", "flaky.jsonl")):
        if hasattr(harness_tests, attr):
            setattr(harness_tests, attr, os.path.join(repo, name))
    return repo


def touch_hooks(repo, names=NAMES):
    for name in names:
        with open(os.path.join(repo, ".claude", "hooks", name + ".py"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("Y = 2\n")


def scoped(repo, names=NAMES, base=None, env=None):
    """run_scoped over the hooks `names` from `repo`: (return code, output). The run's working directory and
    CLAUDE_PROJECT_DIR are the checkout, as a proof resolves its repo from them."""
    buf = io.StringIO()
    old_cwd, old_env = os.getcwd(), dict(os.environ)
    os.chdir(repo)
    os.environ.update({"CLAUDE_PROJECT_DIR": repo}, **(env or {}))
    try:
        with contextlib.redirect_stdout(buf):
            rc = harness_tests.run_scoped(repo, [".claude/hooks/%s.py" % n for n in names], base=base)
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
    return rc, buf.getvalue()


def stamped(repo, before, name):
    """True when the stamp now holds hook `name` as the tree has it."""
    after = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    rel = ".claude/hooks/%s.py" % name
    return after.get(rel) == harness_tests.tree_entries(repo)[rel] and before.get(rel) != after.get(rel)


class Planted:
    """The two launches of a run, answered with planted outputs: the working tree's proofs (`work`: {proof name: output of
    its red; a name not listed passes}) and the rerun at the comparison revision (`head`: {proof name: output of its red,
    `PASS`, or absent for a proof the revision cannot run}). `reruns` counts the HEAD comparisons that started."""
    PASS = object()

    def __init__(self, work, head):
        self.work, self.head, self.reruns, self.judges = work, head, 0, []

    def launch(self, path, timeout=None, root=None, **_):
        text = self.work.get(os.path.basename(path))
        return ("fail", 0.0, text) if text is not None else ("pass", 0.0, "")

    def reds_at(self, repo_root, rev, failing, answers=None):
        self.reruns += 1
        reds = {}
        for path in failing:
            seen = self.head.get(os.path.basename(path))
            if seen is None:
                continue
            if answers is not None:
                answers[path] = "pass" if seen is self.PASS else "red"
            if seen is not self.PASS:
                reds[path] = seen
        return reds

    def __enter__(self):
        self.saved = harness_tests.run_proof, harness_tests._reds_at, harness_tests._run_selected
        harness_tests.run_proof, harness_tests._reds_at = self.launch, self.reds_at
        real = self.saved[2]

        def capture(selected, verbose, statuses=None, details=None, options=harness_tests.RunOptions()):
            self.judges.append(options.red)
            return real(selected, verbose, statuses, details, options)
        harness_tests._run_selected = capture
        return self

    def __exit__(self, *exc):
        harness_tests.run_proof, harness_tests._reds_at, harness_tests._run_selected = self.saved


def lines_proof(lines):
    return "import sys\nprint(%r)\nsys.exit(1)\n" % "\n".join(lines)


# The verdict: the working tree's failure lines against HEAD's, each as the proof printed them.
OLD = ["FAIL: test_old (suite.Case)", "AssertionError: 1 != 2", "FAILED (failures=1)"]
VERDICTS = (
    ("(g) the same count tail over a different failing test blocks",
     ["FAIL: test_new (suite.Case)", "AssertionError: 1 != 2", "FAILED (failures=1)"], OLD, False),
    ("(h) a new failure printed before the old one blocks even under an equal last line",
     ["FAIL: test_new (suite.Case)"] + OLD, OLD, False),
    ("(i) an output with no failure-marked line never excuses", ["8 cases"], ["8 cases"], False),
    ("(i) a timeout tail never excuses", ["TIMEOUT after 300s"], ["TIMEOUT after 300s"], False),
    ("(j) failure lines equal after durations, temp paths and hex ids are normalized excuse",
     ["FAIL: test_old (suite.Case) 3.40s", "ERROR: C:/Temp/harness_proof_ff99ee/x.py", "FAIL id 0xcafef00d run 9a1b2c3d"],
     ["FAIL: test_old (suite.Case) 1.20s", "ERROR: C:/Temp/harness_proof_ab12cd/x.py", "FAIL id 0xdeadbeef run 4ee3c043"], True),
    ("(j) a worktree failure set that is a subset of HEAD's excuses", OLD, OLD + ["FAIL: test_gone (suite.Case)"], True),
    ("a worktree failure set with a failure HEAD lacks blocks", OLD + ["FAIL: test_gone (suite.Case)"], OLD, False),
)


def case_the_verdict_excuses_only_failure_lines_all_at_head():
    wrong = [label for label, work, head, expected in VERDICTS
             if harness_tests._is_pre_existing("\n".join(work), "\n".join(head)) is not expected]
    check("the verdict excuses a red only when its failure lines are non-empty and all appear at HEAD, normalized",
          not wrong, "; ".join(wrong))


def scenario(head_lines, work_lines, env=None, commit=False, base=False):
    """One hook and one proof, red in the working tree. `head_lines` is the proof's red at HEAD and `work_lines` in the
    working tree; `commit` commits the change, so HEAD carries it; `base` passes the seed commit as --base. Returns
    (alpha stamped, output, HEAD reruns started, the run's red judge, the proof's path)."""
    repo = make_repo()
    harness_tests._write_stamp(repo, 1, 1, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    seed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    touch_hooks(repo, ("alpha",))
    if commit:
        subprocess.run(["git", "commit", "-q", "-a", "-m", "change"], cwd=repo, check=True, capture_output=True)
    with Planted({"test_alpha.py": "\n".join(work_lines)}, {"test_alpha.py": "\n".join(head_lines)}) as planted:
        rc, out = scoped(repo, ("alpha",), base=seed if base else None, env=env)
    path = os.path.join(repo, ".claude", "tests", "test_alpha.py")
    return stamped(repo, before, "alpha"), out, planted.reruns, planted.judges[0] if planted.judges else None, path


def case_the_run_flow_over_the_verdict():
    ok, out, reruns, _, _ = scenario(OLD, OLD, env={"HARNESS_NO_HEAD_RERUN": "1"})
    check("(k) with HARNESS_NO_HEAD_RERUN a red that also fails at HEAD is still excused: the variable has no effect",
          ok and "pre-existing red" in out and reruns == 1 and "HEAD rerun skipped" not in out,
          "stamped=%s reruns=%s out=%r" % (ok, reruns, out[-600:]))
    ok, out, reruns, _, _ = scenario(OLD, OLD, commit=True)
    check("(l) a change already committed at HEAD excuses no red: HEAD carries it, and the line names --base",
          not ok and reruns == 0 and "--base" in out, "stamped=%s reruns=%s out=%r" % (ok, reruns, out[-600:]))
    ok, out, reruns, judge, path = scenario(OLD, OLD, commit=True, base=True)
    check("(l) --base names the revision before the change: a red there too is pre-existing",
          ok and "pre-existing red" in out, out[-600:])
    verdict = judge.makes_run_red({path: "\n".join(OLD)}) if judge else "no judge"
    check("(o) a --base run's red judge excuses a red pre-existing at base, so it never cuts the timeout retries",
          verdict is False and ok and reruns == 1, "verdict=%r stamped=%s reruns=%s" % (verdict, ok, reruns))


def case_each_red_is_stamped_or_blocked_by_head_s_answer():
    """One run over four proofs, each red in the working tree, with HEAD's answer planted: the same failure, a green, a
    different failure, and a proof HEAD cannot run (never committed)."""
    repo = make_repo()
    harness_tests._write_stamp(repo, 2, 2, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    touch_hooks(repo, ("alpha", "beta", "gamma", "zeta"))
    write(repo, "tests/test_gamma.py", proof(1, "new, absent at HEAD"))
    work = {"test_alpha.py": "FAIL: same failure", "test_zeta.py": "FAIL: new failure",
            "test_beta.py": "FAIL: red in the working tree", "test_gamma.py": "FAIL: new, absent at HEAD"}
    head = {"test_alpha.py": "FAIL: same failure", "test_zeta.py": "FAIL: old failure", "test_beta.py": Planted.PASS}
    with Planted(work, head):
        rc, out = scoped(repo, ("alpha", "beta", "gamma", "zeta"))
    rows = []
    log = os.path.join(repo, "harness_known_red.jsonl")
    if os.path.exists(log):
        rows = [json.loads(line) for line in open(log, encoding="utf-8")]
    line = [l for l in out.splitlines() if "pre-existing red at HEAD" in l and "test_alpha.py" in l]
    check("(a) a proof red at HEAD and in the working tree stamps its input and prints the pre-existing line",
          stamped(repo, before, "alpha") and bool(line), "out=%r" % out[-600:])
    check("(a) the pre-existing red is logged with proof, head and both tails",
          len(rows) == 1 and rows[0].get("proof") == "test_alpha.py"
          and {"ts", "head", "tail_worktree", "tail_head"} <= set(rows[0]), rows)
    check("(a) a pre-existing red with no earlier row says so",
          bool(line) and "no earlier row in harness_known_red.jsonl" in line[0], line)
    check("(b) a proof green at HEAD but red in the working tree leaves its input unstamped",
          not stamped(repo, before, "beta"), out[-600:])
    check("(c) a red proof absent at HEAD leaves its input unstamped", not stamped(repo, before, "gamma"), out[-600:])
    check("(f) a proof red at HEAD with a different last line blocks its input and says so",
          not stamped(repo, before, "zeta") and "fails differently" in out, out[-600:])
    check("the run still fails: the blocked inputs are red", rc == 1, "rc=%s" % rc)


def case_real_children_resolve_their_repo_at_head():
    """Real children in the working tree and in a real HEAD checkout: a proof that resolves its repo through
    CLAUDE_PROJECT_DIR or cwd sees HEAD's files there, not the working tree's edit."""
    repo = make_repo()
    harness_tests._write_stamp(repo, 2, 2, [])
    before = json.load(open(harness_tests.STAMP_PATH, encoding="utf-8"))["files"]
    touch_hooks(repo)
    write(repo, "tests/test_gamma.py", proof(1, "new, absent at HEAD"))
    rc, out = scoped(repo, ("alpha", "delta", "epsilon", "gamma"))
    line = [l for l in out.splitlines() if "pre-existing red at HEAD" in l and "test_alpha.py" in l]
    check("(a) a real proof red at HEAD and in the working tree stamps its input", stamped(repo, before, "alpha") and bool(line),
          "out=%r" % out[-600:])
    check("(c) a real red proof absent at HEAD leaves its input unstamped", not stamped(repo, before, "gamma"), out[-600:])
    check("(e) a proof green at a HEAD it resolves through CLAUDE_PROJECT_DIR blocks its input",
          not stamped(repo, before, "delta"), out[-600:])
    check("(e) a proof green at a HEAD it resolves through cwd blocks its input",
          not stamped(repo, before, "epsilon"), out[-600:])
    check("the run still fails: the blocked inputs are red", rc == 1, "rc=%s" % rc)
    listed = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    check("(d) no temp worktree stays registered", listed.count("worktree ") == 1, listed)
    seen = {}

    def launch(path, root=None, **_kw):
        seen["root"] = root
        return "fail", 0.0, "FAIL: seed tree absent (%s/runs) and %s" % (root.replace("\\", "/"), root)
    real_make = harness_tests._run_context

    def make(*args, **kwargs):
        ctx = real_make(*args, **kwargs)
        ctx.launch = lambda job: launch(job.proof, job.root)
        return ctx
    harness_tests._run_context = make
    try:
        proof_path = os.path.join(repo, ".claude", "tests", "test_alpha.py")
        detail = harness_tests._reds_at(repo, "HEAD", [proof_path]).get(proof_path, "")
    finally:
        harness_tests._run_context = real_make
    checkout = seen.get("root") or "?"
    check("(m) a failure line naming the real comparison checkout reads as the repo's own root, in either slash form",
          checkout not in detail and checkout.replace("\\", "/") not in detail
          and repo.replace("\\", "/") + "/runs" in detail and repo in detail, (checkout, detail))


def case_a_known_red_names_its_first_recorded_date():
    """A proof already in the known-red log prints the date it was first recorded red there and how many
    closes recorded it. The prior rows are cut from the real .claude/logs/harness_known_red.jsonl."""
    repo = make_repo()
    harness_tests._write_stamp(repo, 1, 1, [])
    seed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    touch_hooks(repo, ("alpha",))
    prior = [
        {"ts": 1794236832.92, "proof": "test_alpha.py", "head": "d2e4689185d0eebf9093d4cccd85fd5559d42f6d",
         "tail_worktree": "FAILED (failures=1)", "tail_head": "FAILED (failures=1)"},
        {"ts": 1794247152.326748, "proof": "test_alpha.py", "head": "b88443c4b4adf3900c86875c536e1b24d488425e",
         "tail_worktree": "FAILED (failures=1)", "tail_head": "FAILED (failures=1)"},
        {"ts": 1794247152.33156, "proof": "test_vault_sync_config.py", "head": "b88443c4b4adf3900c86875c536e1b24d488425e",
         "tail_worktree": "FAILED (failures=3, errors=10)", "tail_head": "FAILED (failures=3, errors=10)"},
    ]
    with open(os.path.join(repo, "harness_known_red.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in prior))
    with Planted({"test_alpha.py": "\n".join(OLD)}, {"test_alpha.py": "\n".join(OLD)}):
        rc, out = scoped(repo, ("alpha",), base=seed)
    note = [l for l in out.splitlines() if "pre-existing red" in l and "test_alpha.py" in l]
    check("(n) a pre-existing red already in the known-red log names its first recorded date and close count",
          bool(note) and "(first recorded red 2026-11-09, 2 earlier closes)" in note[0], out[-600:])


def case_the_known_red_log_stays_bounded():
    # A full log: one early red of test_rare.py, then 399 of test_daily.py; the next red trims it.
    log = os.path.join(tempfile.mkdtemp(prefix="harnessknownred_"), "harness_known_red.jsonl")
    rows = [{"ts": 1790708822.85, "proof": "test_rare.py", "head": "a" * 40}] + [
        {"ts": 1790708900.0 + n, "proof": "test_daily.py", "head": "b" * 40} for n in range(399)]
    with open(log, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in rows))
    harness_tests.KNOWN_RED_LOG = log
    try:
        harness_tests._record_known_red("test_daily.py", "c" * 40, "FAILED", "FAILED")
        harness_tests._record_known_red("test_daily.py", "d" * 40, "FAILED", "FAILED")
        with open(log, encoding="utf-8") as fh:
            kept = fh.read().splitlines()
        check("(o) the known-red log stays bounded", len(kept) <= 400, len(kept))
        check("(o) a trimmed known-red log keeps a proof's first red and its close count",
              harness_tests._known_red_history("test_rare.py", log) == (1790708822.85, 1)
              and harness_tests._known_red_history("test_daily.py", log) == (1790708900.0, 401),
              (harness_tests._known_red_history("test_rare.py", log), harness_tests._known_red_history("test_daily.py", log)))
        check("(o) the newest rows stay whole", json.loads(kept[-1]).get("head") == "d" * 40, kept[-1])
    finally:
        harness_tests.KNOWN_RED_LOG = None


def main():
    """Every `case_*` function in the pool. Each case builds its own repo and sets the runner's module state."""
    return run_cases(checked_cases(globals()))


if __name__ == "__main__":
    sys.exit(main())
