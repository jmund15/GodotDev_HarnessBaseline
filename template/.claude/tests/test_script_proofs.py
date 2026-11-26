"""Proof of the conftest's proof bridge: under `python -m pytest`, every proof `scripts/harness_tests.py` discovers in
this folder runs, so one `pytest -n auto` run covers the whole battery.

A pytest file is collected by pytest itself. Every other proof (a script-style or hybrid `test_*.py`, and each
`.js`, `.mjs`, `.sh` and `.ps1` proof) is one `proof` case that runs the file the way `harness_tests.py` does
(`_proof_runner.attempt.run_proof`). The cases plant a folder holding a copy of the conftest and run pytest on it in
a child; the runner modules resolve through PYTHONPATH because the planted folder has none beside it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path[:0] = [str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")]
from _proof_runner import catalog  # noqa: E402

SCRIPT_OK = "import sys\n\nif __name__ == '__main__':\n    print('ok ran')\n"
SCRIPT_BAD = "import sys\n\nif __name__ == '__main__':\n    print('FAIL planted script failure')\n    sys.exit(1)\n"


def plant(tmp_path, files, adaptation=None):
    tests = tmp_path / ".claude" / "tests"
    tests.mkdir(parents=True)
    shutil.copy(HERE / "conftest.py", tests / "conftest.py")
    for name, text in files.items():
        (tests / name).write_text(text, encoding="utf-8", newline="\n")
    if adaptation is not None:
        folder = tmp_path / ".claude" / "skills" / "project_subsystems"
        folder.mkdir(parents=True)
        (folder / "adaptation.json").write_text(json.dumps(adaptation), encoding="utf-8")
    return tests


def run_pytest(tests, *args, env=None):
    """pytest over the planted folder in a child. `env` overrides the child's environment; a None value removes it."""
    env = {k: v for k, v in {**os.environ, **(env or {})}.items()
           if v is not None and k not in ("HARNESS_PROOF_TIMEOUT_S", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = os.pathsep.join([str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")])
    done = subprocess.run([sys.executable, "-m", "pytest", "-v", "-rs", "-p", "no:cacheprovider", *args,
                           "--rootdir", str(tests), str(tests)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(tests), env=env,
                          timeout=240)
    return done.returncode, done.stdout + done.stderr


def test_a_script_proof_is_one_case_that_passes_or_fails_with_its_output(tmp_path):
    tests = plant(tmp_path, {"test_ok.py": SCRIPT_OK, "test_bad.py": SCRIPT_BAD})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 1, out
    assert "test_ok.py::proof PASSED" in out, out
    assert "test_bad.py::proof FAILED" in out, out
    assert "FAIL planted script failure" in out, out
    assert re.search(r"^_+ test_bad\.py _+$", out, re.M), out


def test_a_failure_far_above_the_tail_is_in_the_report(tmp_path):
    early = ("import sys\n\nif __name__ == '__main__':\n    print('  FAIL  planted early case')\n"
             "    for n in range(100):\n        print('  PASS  case %d' % n)\n    sys.exit(1)\n")
    tests = plant(tmp_path, {"test_early.py": early})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 1 and "FAIL  planted early case" in out, out


def test_a_pytest_file_is_collected_by_pytest_and_a_hybrid_runs_whole(tmp_path):
    hybrid = ("def test_reached():\n    pass\n\n\ndef test_unreached():\n    assert False, 'unreached case ran'\n\n\n"
              "if __name__ == '__main__':\n    test_reached()\n")
    tests = plant(tmp_path, {"test_native.py": "def test_a():\n    pass\n", "test_hybrid.py": hybrid})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_native.py::test_a PASSED" in out, out
    assert "test_hybrid.py::proof FAILED" in out and "unreached case ran" in out, out
    assert code == 1, out


def test_a_case_named_by_its_node_id_runs_as_a_pytest_case(tmp_path):
    hybrid = "def test_unreached():\n    pass\n\n\nif __name__ == '__main__':\n    print('main')\n"
    tests = plant(tmp_path, {"test_hybrid.py": hybrid})
    env = {k: v for k, v in os.environ.items() if k != "HARNESS_PROOF_TIMEOUT_S"}
    env["PYTHONPATH"] = os.pathsep.join([str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")])
    done = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-p", "no:xdist",
                           "--rootdir", str(tests), "test_hybrid.py::test_unreached"],
                          capture_output=True, text=True, cwd=str(tests), env=env, timeout=120)
    assert done.returncode == 0 and "test_hybrid.py::test_unreached PASSED" in done.stdout, done.stdout + done.stderr


def test_a_main_that_hands_its_file_to_pytest_runs_its_cases_once(tmp_path):
    handoff = ("import sys\n\nimport pytest\n\n\ndef test_case():\n    print('CASE RAN')\n\n\n"
               "if __name__ == '__main__':\n    sys.exit(pytest.main([__file__, '-q', '-s', '-p', 'no:cacheprovider']))\n")
    tests = plant(tmp_path, {"test_handoff.py": handoff})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 0 and "test_handoff.py::proof PASSED" in out, out


@pytest.mark.parametrize("name,text", [
    ("planted_test.js", "throw new Error('planted js')\n"),
    ("test_planted.sh", "echo 'FAIL planted sh'\nexit 1\n"),
    ("test_planted.ps1", "Write-Output 'FAIL planted ps1'\nexit 1\n"),
])
def test_each_script_kind_is_a_proof_case_run_by_its_interpreter(tmp_path, name, text):
    tests = plant(tmp_path, {name: text})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert f"{name}::proof FAILED" in out and ("planted" in out or "cannot-run" in out), out
    assert code == 1, out


def test_a_proof_whose_interpreter_is_missing_fails_as_cannot_run_never_green(tmp_path):
    """v1's runner: a cannot-run result is missing coverage, never green. A planted failing `.js` proof must not read
    as a skip when `node` is not on PATH."""
    path = os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                           if not shutil.which("node", path=entry))
    assert shutil.which("node", path=path) is None, path
    tests = plant(tmp_path, {"test_planted.js": "throw new Error('planted js')\n", "test_ok.py": SCRIPT_OK})
    code, out = run_pytest(tests, "-p", "no:xdist", env={"PATH": path})
    assert "test_planted.js::proof FAILED" in out and "cannot-run" in out, out
    assert "SKIPPED" not in out and code == 1, out


def test_a_platform_skip_proof_skips_and_an_exit_2_proof_fails(tmp_path):
    skip = ("import sys\n\nif __name__ == '__main__':\n    print('PLATFORM-SKIP-PROOF test_skip.py: not here')\n"
            "    sys.exit(0)\n")
    unbound = "import sys\n\nif __name__ == '__main__':\n    print('CANNOT-RUN: no target')\n    sys.exit(2)\n"
    tests = plant(tmp_path, {"test_skip.py": skip, "test_unbound.py": unbound})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_skip.py::proof SKIPPED" in out and "not here" in out, out
    assert "test_unbound.py::proof FAILED" in out and "cannot-run" in out, out
    assert code == 1, out


def test_excluded_and_heavy_proofs_skip_with_their_reason_unless_harness_all(tmp_path):
    adaptation = {"proof_excluded": {"test_ok.py": "a report generator"},
                  "proof_heavy": {"test_bad.py": "minutes of real backends"}}
    tests = plant(tmp_path, {"test_ok.py": SCRIPT_OK, "test_bad.py": SCRIPT_BAD}, adaptation)
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 0, out
    assert "a report generator" in out and "minutes of real backends" in out, out
    code, out = run_pytest(tests, "-p", "no:xdist", "--harness-all")
    assert "test_ok.py::proof PASSED" in out and "test_bad.py::proof FAILED" in out, out


def test_a_proof_timeout_cap_ends_the_proof(tmp_path):
    slow = "import time\n\nif __name__ == '__main__':\n    time.sleep(30)\n"
    tests = plant(tmp_path, {"test_slow.py": slow}, {"proof_timeouts": {"test_slow.py": 2}})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_slow.py::proof FAILED" in out and "TIMEOUT" in out, out


def test_a_serial_proof_skips_when_workers_share_the_machine(tmp_path):
    tests = plant(tmp_path, {"test_ok.py": SCRIPT_OK, "test_alone.py": SCRIPT_OK},
                  {"proof_serial": {"test_alone.py": "asserts a deadline another proof would stretch"}})
    code, out = run_pytest(tests, "-n", "2")
    assert code == 0, out
    assert "asserts a deadline another proof would stretch" in out and "-p no:xdist" in out, out
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_alone.py::proof PASSED" in out, out


def test_a_proof_under_xdist_runs_one_case_job_at_a_time(tmp_path):
    """The runner gives a proof two case jobs (`HARNESS_CASE_JOBS`); under `-n` every worker already holds a CPU, so the
    bridge hands a worker's proofs one, and a run without xdist keeps the runner's two."""
    record = ("import os\n\nif __name__ == '__main__':\n"
              "    open(os.path.join(os.environ['PLANT_MARK'], os.path.basename(__file__)), 'w').write(os.environ['HARNESS_CASE_JOBS'])\n")
    tests = plant(tmp_path, {"test_a.py": record, "test_b.py": record})
    marks = tmp_path / "jobs"
    marks.mkdir()
    env = {"PLANT_MARK": str(marks), "HARNESS_CASE_JOBS": None}

    def recorded():
        return sorted(mark.read_text() for mark in marks.iterdir())

    code, out = run_pytest(tests, "-n", "2", env=env)
    assert code == 0 and recorded() == ["1", "1"], (recorded(), out)
    for mark in marks.iterdir():
        mark.unlink()
    code, out = run_pytest(tests, "-p", "no:xdist", env=env)
    assert code == 0 and recorded() == ["2", "2"], (recorded(), out)


NATIVE_ENV = (
    "import os\n\n\n"
    "def test_a_native_case_sees_the_proof_environment():\n"
    "    assert os.environ['GIT_CONFIG_GLOBAL'] == os.devnull\n"
    "    assert os.environ['HARNESS_CAPACITY_WARM'] == '0'\n"
    "    assert 'CLAUDE_CODE_SESSION_ID' not in os.environ and 'GIT_INDEX_FILE' not in os.environ\n"
    "    assert os.path.basename(os.path.dirname(os.environ['HOME'])).startswith('harness_proof_'), os.environ['HOME']\n"
    "    assert os.environ['HOME'] != os.environ['HARNESS_CALLER_HOME']\n"
    "    os.environ['PLANT_LEAK'] = '1'\n\n\n"
    "def test_a_native_case_leaves_nothing_for_the_next():\n"
    "    assert 'PLANT_LEAK' not in os.environ\n"
)
NATIVE_GIT = (
    "import os\n\nSEEN = {k: v for k, v in os.environ.items() if k.startswith('GIT_CONFIG_')}\n\n\n"
    "def test_the_callers_git_config_survives_the_conftest():\n"
    "    pairs = {(SEEN['GIT_CONFIG_KEY_%d' % n], SEEN['GIT_CONFIG_VALUE_%d' % n]) for n in range(int(SEEN['GIT_CONFIG_COUNT']))}\n"
    "    assert ('user.name', 'planted') in pairs and ('gc.auto', '0') in pairs, pairs\n"
)


def test_a_native_pytest_file_runs_in_the_proof_environment(tmp_path):
    """`run_proof` gave each pytest file a clean runner's world; a file collected in process gets the same one per case:
    no ambient `CLAUDE_*` or `GIT_*` index, no global git config, a home of its own, and nothing left behind for the
    next case. The machine's temp folder and system git config stay: the floor's delete rows judge the real temp."""
    tests = plant(tmp_path, {"test_env.py": NATIVE_ENV})
    code, out = run_pytest(tests, "-p", "no:xdist",
                           env={"CLAUDE_CODE_SESSION_ID": "planted-ambient", "GIT_INDEX_FILE": "planted-index"})
    assert code == 0 and "2 passed" in out, out


def test_the_conftest_appends_to_the_callers_git_config(tmp_path):
    tests = plant(tmp_path, {"test_git.py": NATIVE_GIT})
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "planted"}
    code, out = run_pytest(tests, "-p", "no:xdist", env=env)
    assert code == 0 and "1 passed" in out, out


def test_a_native_case_that_hangs_ends_the_run_at_its_files_cap(tmp_path):
    """A pytest file ran under `run_proof`'s timeout; collected in process, a hang has no one to end it. The case gets
    its file's `proof_timeouts` cap (default 120 s) and a hang names the case and ends the process, which under xdist is
    one worker that the controller reports as crashed."""
    hang = "import time\n\n\ndef test_hangs():\n    time.sleep(30)\n"
    tests = plant(tmp_path, {"test_hang.py": hang}, {"proof_timeouts": {"test_hang.py": 2}})
    for args, named in ((("-p", "no:xdist"), "HANG: test_hang.py::test_hangs ran longer than 2s"),
                        (("-n", "2"), "crashed while running 'test_hang.py::test_hangs'")):
        started = time.monotonic()
        code, out = run_pytest(tests, *args)
        assert code != 0 and named in out, out
        assert time.monotonic() - started < 25, out


def test_a_quarantined_proof_still_runs_and_reports_but_its_failure_is_no_red(tmp_path):
    """`proof_quarantine` names a seen-flaky proof and its cause: it runs as xfail(strict=False), so a failure passes the
    run and shows as xfailed with the cause, and a proof beside it still fails the run."""
    native = "def test_a():\n    assert False\n"
    quarantine = {"proof_quarantine": {"test_flaky.py": "races its own fixture", "test_native.py": "order dependent"}}
    tests = plant(tmp_path / "quarantined", {"test_flaky.py": SCRIPT_BAD, "test_native.py": native}, quarantine)
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_flaky.py::proof XFAIL" in out and "quarantined: races its own fixture" in out, out
    assert "test_native.py::test_a XFAIL" in out and code == 0, out
    tests = plant(tmp_path / "beside", {"test_flaky.py": SCRIPT_BAD, "test_steady.py": SCRIPT_BAD}, quarantine)
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert "test_steady.py::proof FAILED" in out and code == 1, out


def test_a_proof_with_a_longer_bound_starts_before_the_short_ones(tmp_path):
    """Under `-n` the workers take cases in collection order, so a long proof collected late (`test_r...`) sets the run's
    tail. The `proof_timeouts` rows name the long proofs, so their order is the start order: longest bound first."""
    tests = plant(tmp_path, {"test_a_short.py": SCRIPT_OK, "test_0_native.py": "def test_a():\n    pass\n",
                             "test_b_short.py": SCRIPT_OK, "test_z_long.py": SCRIPT_OK, "test_m_longer.py": SCRIPT_OK},
                  {"proof_timeouts": {"test_z_long.py": 300, "test_m_longer.py": 600}})
    code, out = run_pytest(tests, "-p", "no:xdist", "--collect-only", "-q")
    order = re.findall(r"<(?:ProofFile|Module) (test_\w+\.py)>", out)
    assert code == 0 and order == ["test_m_longer.py", "test_z_long.py", "test_a_short.py", "test_b_short.py",
                                   "test_0_native.py"], out


def test_under_xdist_the_longest_proofs_start_on_different_workers(tmp_path):
    """xdist's `load` schedule hands every worker two consecutive items up front (its floor), and a case once sent never
    moves: with the long proofs sorted first, the two longest both went to worker 0 and ran in series. The collection
    order puts one long proof at the head of each worker's opening pair, so the longest proofs run side by side."""
    marks = tmp_path / "marks"
    marks.mkdir()
    record = ("def test_long(request):\n    open(%r + '/' + __name__, 'w').write(request.config.workerinput['workerid'])\n"
              % str(marks).replace("\\", "/"))
    longs = {"test_long_a.py": 600, "test_long_b.py": 500, "test_long_c.py": 400, "test_long_d.py": 300}
    short = "".join("def test_short_%d():\n    pass\n\n\n" % n for n in range(20))
    tests = plant(tmp_path / "run", {**{name: record for name in longs}, "test_short.py": short},
                  {"proof_timeouts": longs})
    code, out = run_pytest(tests, "-n", "2")
    worker = {mark.name: mark.read_text() for mark in marks.iterdir()}
    assert code == 0 and len(worker) == 4, (worker, out)
    assert worker["test_long_a"] != worker["test_long_b"], (worker, out)


def test_a_script_proof_that_outruns_its_bound_is_a_named_red(tmp_path):
    """The bound is the file's `proof_timeouts` entry, else 120 s; the red names the proof and the expiry."""
    slow = "import time\n\nif __name__ == '__main__':\n    time.sleep(30)\n"
    tests = plant(tmp_path, {"test_slow.py": slow}, {"proof_timeouts": {"test_slow.py": 2}})
    started = time.monotonic()
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 1 and "test_slow.py: fail" in out and re.search(r"TIMEOUT after [23]s", out), out
    assert time.monotonic() - started < 25, out


MARK = "open(os.path.join(os.environ['PLANT_MARKS'], %r), 'a').write('x\\n')"
# Every shape the runner (`attempt._pytest_cases`) reads as more than a plain pytest file: its result, and the one
# process that may run the script or the main (a second run is an import at collection).
HYBRIDS = {
    "test_main_and_unreached_case.py": (
        "import os\n\n\ndef test_reached():\n    pass\n\n\ndef test_unreached():\n    assert False, 'unreached case ran'\n\n\n"
        "if __name__ == '__main__':\n    " + MARK % "test_main_and_unreached_case" + "\n    test_reached()\n", "FAILED"),
    "test_main_hands_the_file_to_pytest.py": (
        "import os\nimport sys\n\nimport pytest\n\n\ndef test_case():\n    pass\n\n\nif __name__ == '__main__':\n    "
        + MARK % "test_main_hands_the_file_to_pytest" + "\n    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))\n", "PASSED"),
    "test_main_collects_from_globals.py": (
        "import os\n\n\ndef test_plain():\n    pass\n\n\ndef test_needs_a_fixture(tmp_path):\n    assert False, 'case with an argument ran'\n\n\n"
        "def main():\n    " + MARK % "test_main_collects_from_globals" + "\n    for name, fn in sorted(globals().items()):\n"
        "        if name.startswith('test_') and callable(fn) and fn.__code__.co_argcount == 0:\n            fn()\n\n\n"
        "if __name__ == '__main__':\n    main()\n", "FAILED"),
    "test_script_body_and_a_case.py": (
        "import os\n\n" + MARK % "test_script_body_and_a_case" + "\n\n\ndef test_case():\n    pass\n", "PASSED"),
    "test_script_body_and_a_def_in_a_string.py": (
        "import os\n\nEXAMPLE = \"\"\"\ndef test_example():\n    pass\n\"\"\"\n" + MARK % "test_script_body_and_a_def_in_a_string" + "\n", "FAILED"),
}


def test_a_hybrid_runs_as_the_runner_runs_it_once_and_never_at_collection(tmp_path):
    """The runner runs a hybrid as a script, then its unreached cases, in one isolated process. Imported by pytest
    instead, its script body ran at collection in the controller and in every xdist worker: a consumer's pytest file
    with a script body raced itself there, one `PermissionError` made the workers collect different tests, and xdist
    stopped all 24. Each shape is one `proof` case, and its script or main runs once."""
    marks = tmp_path / "marks"
    marks.mkdir()
    tests = plant(tmp_path, {name: text for name, (text, _) in HYBRIDS.items()})
    code, out = run_pytest(tests, "-n", "2", env={"PLANT_MARKS": str(marks)})
    assert code == 1 and "3 failed, 2 passed in" in out, out
    for name, (_, result) in HYBRIDS.items():
        assert re.search(r"%s %s::proof" % (result, re.escape(name)), out), (name, out)
        assert (marks / name[:-3]).read_text().split() == ["x"], (name, out)


def test_the_serial_pass_runs_only_the_serial_proofs_alone(tmp_path):
    """CI's second pass: `--harness-serial` under `-p no:xdist` runs the `proof_serial` proofs and nothing else, and a
    folder with none passes (pytest's own exit 5 for an empty selection would fail every lane that has none)."""
    tests = plant(tmp_path, {"test_ok.py": SCRIPT_OK, "test_alone.py": SCRIPT_OK},
                  {"proof_serial": {"test_alone.py": "asserts a deadline another proof would stretch"}})
    code, out = run_pytest(tests, "-p", "no:xdist", "--harness-serial")
    assert code == 0 and "test_alone.py::proof PASSED" in out and "test_ok.py" not in out, out
    none = plant(tmp_path / "none", {"test_ok.py": SCRIPT_OK})
    code, out = run_pytest(none, "-p", "no:xdist", "--harness-serial")
    assert code == 0 and "PASSED" not in out, out


def test_every_proof_the_runner_discovers_is_collected():
    """The real folder: each file `harness_tests.py` discovers yields at least one pytest case."""
    env = {k: v for k, v in os.environ.items() if k not in ("HARNESS_PROOF_TIMEOUT_S", "PYTEST_ADDOPTS")}
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                           "-p", "no:xdist", "--harness-all", str(HERE)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(HERE), env=env,
                          timeout=240)
    collected = {Path(line.split("::", 1)[0]).name for line in done.stdout.splitlines() if "::" in line}
    discovered = {os.path.basename(p) for p in catalog.discover(str(HERE))}
    assert discovered and not discovered - collected, sorted(discovered - collected)[:40]


def test_a_part_file_run_directly_shards_its_main_under_the_bridge_conftest(tmp_path):
    """`python test_x_part2.py` with no runner around it: `run_sharded`'s collect and shard children are pytest runs
    inside a proof, so the bridge conftest collects the main's real cases, not one `::proof` item. The main holds a
    script body, so the bridge would otherwise take it as one proof case."""
    part = ("import os\nimport sys\n\nHERE = os.path.dirname(os.path.abspath(__file__))\nsys.path.insert(0, HERE)\n"
            "from _case_pool import run_sharded  # noqa: E402\n\nif __name__ == '__main__':\n"
            "    sys.exit(run_sharded(os.path.join(HERE, 'test_x.py'), 1, 2, jobs=1))\n")
    main = "print('script body')\n\n\ndef test_a():\n    pass\n\n\ndef test_b():\n    pass\n"
    tests = plant(tmp_path, {"test_x.py": main,
                             "test_x_part2.py": part})
    shutil.copy(HERE / "_case_pool.py", tests / "_case_pool.py")
    env = {k: v for k, v in os.environ.items() if k not in ("HARNESS_PROOF_TIMEOUT_S", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = os.pathsep.join([str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")])
    done = subprocess.run([sys.executable, "test_x_part2.py"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(tests), env=env, timeout=240)
    out = done.stdout + done.stderr
    assert done.returncode == 0 and "names no case" not in out and "1 cases in 1 shards" in out, out


def test_pytest_proofs_take_no_lane_and_never_borrow_the_live_pool(tmp_path):
    # A malformed pool path makes any attempt to take or borrow a lane observable without taking a live lane.
    blocker = tmp_path / "not-a-pool"
    blocker.write_text("not a directory")
    check = """
import os
from _case_pool import _budget

def check():
    # A pytest run holds no lane and the code a proof exercises must take none (RuntimeSlots.ps1 queues for one when the
    # budget is not '1'), so the proof's environment says a lane is loaned, whatever the caller held.
    assert os.environ['HARNESS_SLOT_BUDGET'] == '1'
    assert 'HARNESS_LANE_CORES' not in os.environ
    assert os.environ['HARNESS_MACHINE_ROOT'] != os.environ['PLANT_LIVE_POOL']
    with _budget(2) as width:
        assert width == 2

"""
    tests = plant(tmp_path, {"test_native.py": check + "def test_slots():\n    check()\n",
                            "test_script.py": check + "if __name__ == '__main__':\n    check()\n"})
    shutil.copy(HERE / "_case_pool.py", tests / "_case_pool.py")
    code, out = run_pytest(tests, "-p", "no:xdist", env={
        "HARNESS_MACHINE_ROOT": str(blocker), "HARNESS_SLOT_DIR": str(blocker),
        "HARNESS_SLOT_BUDGET": None, "HARNESS_LANE_CORES": "1", "PLANT_LIVE_POOL": str(blocker),
    })
    assert code == 0 and "2 passed" in out, out


def test_pytest_default_bound_uses_the_120_second_proof_limit(bridge):
    assert bridge._timeout(Path("test_no_override.py")) == 120


def test_serial_pass_rejects_xdist_instead_of_reporting_an_empty_green(tmp_path):
    tests = plant(tmp_path, {"test_alone.py": SCRIPT_OK}, {"proof_serial": {"test_alone.py": "exclusive fixture"}})
    code, out = run_pytest(tests, "-n", "2", "--harness-serial")
    assert code == pytest.ExitCode.USAGE_ERROR and "--harness-serial" in out, out


def test_a_proof_never_waits_on_this_machines_free_memory(tmp_path):
    # A proof that launches a real child through the sidecar launchers met the live memory floors: with a local model
    # server holding 19 GiB, test_sidecar_fanout.py waited out its timeout. No proof tests this machine's memory; a proof
    # of the gate itself sets its own floors.
    from _proof_runner import attempt
    env = attempt.proof_env(str(tmp_path), 60, root=str(CLAUDE.parent))
    assert env.get("LAUNCH_MIN_COMMIT_GIB") == "0" and env.get("LAUNCH_MIN_PHYSICAL_GIB") == "0", env.get("LAUNCH_MIN_COMMIT_GIB")
