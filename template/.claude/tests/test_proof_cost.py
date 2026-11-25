"""Proof that tools/proof_cost.py measures the run time a change adds, base against change on one machine.

- `side_orders` flips which side runs first each round.
- `summarize` reports each side's median and range from planted times, the delta, and whether it exceeds the
  two sides' combined spread; an even count takes the middle pair's mean.
- A proof absent at the base revision has no base side: only its own times are reported.
- A failing side is reported failing and never carries a delta: no pass timing is invented for it.
- The base side is a real private checkout of the base revision, holding the base bytes, and removed after.
- `measure` drives real runner attempts in alternating order, skips proofs absent from a real base, and rejects bad inputs.
- The CLI resolves a remote default, refuses a clean comparison against an implicit HEAD, and reports status and JSON.

    python3 .claude/tests/test_proof_cost.py
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "scripts"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
import harness_tests  # noqa: E402
import proof_cost  # noqa: E402
from _hook_call import copy_repo  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def close(a, b):
    return abs(a - b) < 1e-9


# ---------------------------------------------------------------- pure logic

def case_alternation():
    orders = proof_cost.side_orders(3)
    check("round 0 runs the base first", orders[0] == ("base", "change"), orders)
    check("round 1 runs the change first", orders[1] == ("change", "base"), orders)
    check("round 2 flips back to the base first", orders[2] == ("base", "change"), orders)
    check("one round is requested by a count of one", proof_cost.side_orders(1) == [("base", "change")])


def case_summary_from_planted_timings():
    got = proof_cost.summarize("p.py", [1.0, 1.2, 1.1], [2.0, 2.2, 2.1])
    check("base median is the middle sample", close(got["base"]["median"], 1.1), got["base"])
    check("base range spans its samples", close(got["base"]["min"], 1.0) and close(got["base"]["max"], 1.2),
          got["base"])
    check("change median is the middle sample", close(got["change"]["median"], 2.1), got["change"])
    check("delta is change median minus base median", close(got["delta"], 1.0), got["delta"])
    check("combined spread is the two sides' ranges added", close(got["combined_spread"], 0.4),
          got["combined_spread"])
    check("a delta past the combined spread reports it", got["exceeds_spread"] is True, got)


def case_summary_inside_spread_and_even_count():
    got = proof_cost.summarize("p.py", [1.0, 1.6], [1.2, 1.5])
    check("an even count takes the mean of the middle pair", close(got["base"]["median"], 1.3), got["base"])
    check("a delta inside the combined spread reports it", got["exceeds_spread"] is False, got)
    single = proof_cost.summarize("p.py", [174.9], [159.8])
    check("one run on a side has no spread to judge a delta against: no verdict",
          single["exceeds_spread"] is None and close(single["delta"], -15.1), single)
    check("a delta with no verdict prints as unjudged, never as exceeding",
          any("unjudged" in line for line in proof_cost.render(single)), proof_cost.render(single))


def case_new_proof_has_no_base():
    got = proof_cost.summarize("new.py", None, [1.0, 1.1, 1.2])
    check("a proof absent at base reports no base side", got["base"]["absent"] is True, got["base"])
    check("its own times are still reported", close(got["change"]["median"], 1.1), got["change"])
    check("a new proof carries no delta", got["delta"] is None, got)
    check("a new proof gives no spread verdict", got["exceeds_spread"] is None, got)


def case_a_failing_run_is_not_a_pass():
    got = proof_cost.summarize("p.py", [1.0, 1.2], [1.1], change_failure="fail: planted red")
    check("a failing side is reported failing", got["change"]["failed"] is True, got["change"])
    check("a failing side keeps its failure detail", "planted red" in got["change"]["detail"], got["change"])
    check("a failing side reports no median", got["change"]["median"] is None, got["change"])
    check("a failing side carries no delta", got["delta"] is None, got)
    check("a failing side gives no spread verdict", got["exceeds_spread"] is None, got)
    base = proof_cost.summarize("p.py", [1.0], [1.1], base_failure="cannot-run: planted")
    check("a failing base side is reported failing", base["base"]["failed"] is True, base["base"])


# ------------------------------------------------------------ real fixtures

def git(repo, *args):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], result.stderr))
    return result.stdout.strip()


def make_repo():
    """A committed base holding a passing and a passing-then-red proof, and a working tree that changes both."""
    repo = tempfile.mkdtemp(prefix="proof_cost_fixture_")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    tests = os.path.join(repo, ".claude", "tests")
    os.makedirs(tests)
    pass_proof = os.path.join(tests, "test_tiny_pass.py")
    red_proof = os.path.join(tests, "test_tiny_red.py")
    with open(pass_proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    with open(red_proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    git(repo, "add", "-f", "--all")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    with open(pass_proof, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n# changed\n")
    with open(red_proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(1)\n")
    return repo, base, pass_proof, red_proof


def copy_fixture(template):
    source, base, passing, failing = template
    repo = tempfile.mkdtemp(prefix='proof_cost_case_')
    copy_repo(source, repo)
    return repo, base, os.path.join(repo, os.path.relpath(passing, source)), os.path.join(repo, os.path.relpath(failing, source))


def case_base_checkout_is_real_and_removed(template):
    repo, base, pass_proof, _ = copy_fixture(template)
    try:
        with harness_tests._revision_checkout(repo, base) as checkout:
            copied = os.path.join(checkout, ".claude", "tests", "test_tiny_pass.py")
            check("the base checkout holds the proof", os.path.isfile(copied), copied)
            with open(copied, encoding="utf-8") as fh:
                text = fh.read()
            check("the base checkout holds the base bytes, not the change", "changed" not in text, text)
            built = os.path.isdir(checkout)
        check("the base checkout is removed after its block", built and not os.path.exists(checkout), checkout)
    finally:
        harness_tests._remove_temp_root(repo)


def case_measure_drives_real_runs_in_alternated_order(template):
    repo, base, pass_proof, red_proof = copy_fixture(template)
    saved = {name: os.environ.get(name) for name in ("HARNESS_SLOT_DIR", "HARNESS_MACHINE_SLOTS")}
    real_durations = harness_tests.durations_path
    try:
        os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
        os.environ["HARNESS_MACHINE_SLOTS"] = "1"
        real = harness_tests._run_context(root=repo)
        repo_norm = os.path.normcase(os.path.abspath(repo))
        order, timeouts = [], {}
        durations = os.path.join(repo, "planted_durations.json")
        with open(durations, "w", encoding="utf-8") as fh:
            json.dump({os.path.basename(pass_proof): 100.0}, fh)
        harness_tests.durations_path = lambda: durations

        def launch(job):
            side = "change" if os.path.normcase(os.path.abspath(job.root)) == repo_norm else "base"
            order.append(side)
            timeouts.setdefault(os.path.basename(job.proof), set()).add(job.timeout)
            return real.launch(job)

        ctx = harness_tests._attempt.RunContext(
            root=repo, deadline=None, pace=real.pace, take=real.take, release=real.release,
            slot_error=real.slot_error, stop=None, launch=launch, default_timeout=real.default_timeout,
            inherited=real.inherited, held=False)
        new_proof = os.path.join(os.path.dirname(pass_proof), 'test_new.py')
        with open(new_proof, 'w', encoding='utf-8') as fh:
            fh.write('pass\n')
        git(repo, 'add', '--all')
        git(repo, 'commit', '-qm', 'change')
        results = proof_cost.measure(repo, [pass_proof, red_proof, new_proof], 'HEAD^', rounds=2, ctx=ctx)
        check('each round alternates the two comparable proofs and skips the absent base',
              order == ['base', 'change', 'base', 'change', 'change',
                        'change', 'base', 'change', 'base', 'change'], order)
        passing, failing, new = results
        check('a real new proof has no base timing and two change runs',
              new['base']['absent'] and not new['base']['runs'] and len(new['change']['runs']) == 2, new)
        check('render shows the absent base line', 'absent' in proof_cost.render(new)[1])
        check('render shows the failed change line', 'FAILED' in proof_cost.render(failing)[2])
        with patch.object(proof_cost, 'measure', return_value=[passing, new]):
            code, out, err = invoke_main(repo, '--base', base, '--json', pass_proof, new_proof)
            payload = json.loads(out)
            check('main exits 0 and emits the complete JSON report list', code == 0 and not err
                  and payload == [passing, new]
                  and set(payload[0]) == {'proof', 'base', 'change', 'delta', 'combined_spread', 'exceeds_spread'}
                  and set(payload[0]['base']) == {'absent', 'failed', 'detail', 'median', 'min', 'max', 'runs'})
        with patch.object(proof_cost, 'measure', return_value=[failing]):
            code, out, err = invoke_main(repo, '--base', base, '--json', red_proof)
            check('main exits 1 and still emits JSON for a failed proof',
                  code == 1 and not err and json.loads(out) == [failing])
        check("a passing proof gets both sides timed", passing["base"]["failed"] is False
              and passing["change"]["failed"] is False
              and len(passing["base"]["runs"]) == 2 and len(passing["change"]["runs"]) == 2, passing)
        check("a passing proof gets a delta and a spread verdict",
              passing["delta"] is not None and isinstance(passing["exceeds_spread"], bool), passing)
        check("a red on the change side is reported failing", failing["change"]["failed"] is True,
              failing["change"])
        check("its base side still passes", failing["base"]["failed"] is False, failing["base"])
        check("a red proof carries no delta and no spread verdict",
              failing["delta"] is None and failing["exceeds_spread"] is None, failing)
        check("a proof whose recorded pass took 100 s runs both sides under the runner's 300 s cap, not 120 s",
              timeouts.get(os.path.basename(pass_proof)) == {300.0}, timeouts)
    finally:
        harness_tests.durations_path = real_durations
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        harness_tests._remove_temp_root(repo)


def invoke_main(repo, *args):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(proof_cost, 'REPO_ROOT', repo), redirect_stdout(out), redirect_stderr(err):
        code = proof_cost.main(args)
    return code, out.getvalue(), err.getvalue()


def case_default_base_uses_remote_default_and_rejects_clean_head(template):
    repo, base, passing, unrelated = copy_fixture(template)
    try:
        git(repo, 'branch', '-M', 'main')
        git(repo, 'add', '--all')
        git(repo, 'commit', '-qm', 'change')
        head = git(repo, 'rev-parse', 'HEAD')
        git(repo, 'update-ref', 'refs/remotes/origin/trunk', base)
        git(repo, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        check('default base follows origin/HEAD rather than local main', proof_cost._default_base(repo) == base)
        git(repo, 'update-ref', 'refs/remotes/origin/trunk', head)
        with open(unrelated, 'a', encoding='utf-8') as fh:
            fh.write('# unrelated working tree change\n')
        with patch.object(proof_cost, 'measure', return_value=[]) as measure:
            code, out, err = invoke_main(repo, passing)
            check('clean proof at default base HEAD asks for --base without measuring',
                  code == 2 and not out and '--base' in err and not measure.called, (code, out, err))
            code, _, err = invoke_main(repo, '--base', 'HEAD', passing)
            check('an explicit HEAD base is allowed', code == 0 and measure.called, err)
            measure.reset_mock()
            with open(passing, 'a', encoding='utf-8') as fh:
                fh.write('# working tree change\n')
            code, _, err = invoke_main(repo, passing)
            check('dirty named proof can use default base HEAD', code == 0 and measure.called, err)
            measure.reset_mock()
            git(repo, 'add', '--', passing)
            code, _, err = invoke_main(repo, passing)
            check('a staged change to the named proof can use default base HEAD', code == 0 and measure.called, err)
    finally:
        harness_tests._remove_temp_root(repo)


def case_invalid_inputs_exit_two(template):
    repo, base, passing, _ = copy_fixture(template)
    try:
        missing = os.path.join(repo, 'missing.py')
        outside = os.path.join(os.path.dirname(repo), 'outside.py')
        for proof, rounds, error_type, token in (
                (passing, 0, ValueError, '--runs'), (outside, 1, ValueError, outside),
                (missing, 1, FileNotFoundError, missing)):
            try:
                proof_cost.measure(repo, [proof], base, rounds=rounds)
            except (ValueError, FileNotFoundError) as exc:
                check('measure rejects invalid input: ' + token, type(exc) is error_type and token in str(exc), exc)
            else:
                check('measure rejects invalid input: ' + token, False)
            code, out, err = invoke_main(repo, '--base', base, '--runs', str(rounds), proof)
            check('main exits 2 for invalid input: ' + token, code == 2 and not out and token in err, err)
        git(repo, 'branch', '-M', 'fixture')
        code, out, err = invoke_main(repo, passing)
        check('main exits 2 and names --base when there is no default merge-base',
              code == 2 and not out and '--base' in err, err)
    finally:
        harness_tests._remove_temp_root(repo)


def run_case(case, *args):
    try:
        case(*args)
    except Exception as exc:  # noqa: BLE001 - a crashing case is a failed case
        check(case.__name__, False, "%s: %s" % (type(exc).__name__, exc))


def main():
    for case in (case_alternation, case_summary_from_planted_timings,
                 case_summary_inside_spread_and_even_count, case_new_proof_has_no_base,
                 case_a_failing_run_is_not_a_pass):
        run_case(case)
    template = make_repo()
    try:
        for case in (case_base_checkout_is_real_and_removed, case_default_base_uses_remote_default_and_rejects_clean_head,
                     case_invalid_inputs_exit_two, case_measure_drives_real_runs_in_alternated_order):
            run_case(case, template)
    finally:
        harness_tests._remove_temp_root(template[0])
    if FAILURES:
        print("\n%d failure(s)" % len(FAILURES))
        for failure in FAILURES:
            print("  FAIL " + failure)
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
