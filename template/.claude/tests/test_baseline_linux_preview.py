#!/usr/bin/env python3
"""Proof for tools/baseline_linux_preview.py: the CI workflow parser, the WSL script it builds and
the worktree resolver. WSL itself is not started; the script text is asserted instead.

    python3 .claude/tests/test_baseline_linux_preview.py
"""
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import baseline_linux_preview as blp  # noqa: E402
from _sidecar_launch import bash_exe  # noqa: E402

WORKFLOW = """name: baseline

on:
  pull_request:

jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      # a comment between steps
      - name: verify pwsh is on PATH
        run: pwsh -NoProfile -Command 'exit 0'

      - name: baseline-repo-root proofs
        run: |
          set -e
          for f in tests/test_*.py; do
            python3 "$f"
          done

  b:
    runs-on: ubuntu-latest
    steps:
      - name: template harness battery
        run: python3 -m pytest -n auto -q --durations=20 template/.claude/tests

  baseline:
    name: baseline
    needs: [a, b]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: gate
        env:
          RESULTS: ${{ join(needs.*.result, ' ') }}
        run: |
          for r in $RESULTS; do [ "$r" = success ] || exit 1; done
"""


def script_of_two(blp):
    return blp.preview_script("/mnt/c/w/tree", [("a", [("s", "true")]), ("b", [("s", "true")])])


def main():
    cases = []
    slot_root = tempfile.mkdtemp(prefix='preview_pool_')
    os.environ.update(HARNESS_SLOT_DIR=slot_root, HARNESS_MACHINE_ROOT=slot_root)

    def local_script(*args):
        # Run the same lane script under this host's Bash; WSL path translation is proven separately.
        from unittest.mock import patch
        if os.name == 'nt':
            with patch.object(blp, 'wsl_path', lambda p: str(p).replace('\\', '/')):
                return blp.preview_script(*args)
        return blp.preview_script(*args)


    jobs = blp.ci_jobs(WORKFLOW)
    steps = [step for _, job_steps in jobs for step in job_steps]
    cases.append(("each lane job keeps its steps; a needs: gate job is skipped",
                  [j for j, _ in jobs] == ["a", "b"] and [len(s) for _, s in jobs] == [2, 1]))
    cases.append(("uses: steps are skipped; every run: step is kept, in order",
                  [n for n, _ in steps] == ["verify pwsh is on PATH", "baseline-repo-root proofs",
                                            "template harness battery"]))
    cases.append(("a one-line run keeps its command",
                  steps[0][1] == "pwsh -NoProfile -Command 'exit 0'"))
    cases.append(("a block run is dedented and keeps its lines",
                  steps[1][1] == 'set -e\nfor f in tests/test_*.py; do\n  python3 "$f"\ndone'))
    cases.append(("a workflow with no run: step parses to []", blp.ci_jobs("jobs: {}\n") == []))

    cases.append(("a drive path maps under /mnt",
                  blp.wsl_path(r"C:\Users\x\a b") == "/mnt/c/Users/x/a b"
                  and blp.wsl_path("D:/w/t") == "/mnt/d/w/t"))

    script = blp.preview_script("/mnt/c/w/tree", jobs)
    cases.append(("the snapshot lives under $HOME, never /tmp (delete guards admit temp paths)",
                  '"$HOME/baseline-linux-preview"' in script))
    cases.append(("ci-tools python, node, pwsh and dotnet lead PATH",
                  'PATH="$HOME/.local/ci-tools/py/bin:$HOME/.local/ci-tools/node/bin:$HOME/.local/ci-tools/pwsh:'
                  '$HOME/.local/ci-tools/dotnet:$PATH"' in script))
    cases.append(("setup installs the dotnet host a runner ships (the runtime-slot proof launches `dotnet pwsh.dll`)",
                  "dotnet-install.sh" in blp.SETUP_SCRIPT and "$T/dotnet" in blp.SETUP_SCRIPT))
    stripped = subprocess.run([bash_exe(), "--noprofile", "--norc", "-c",
                               "PATH=/usr/bin:/mnt/c/Win/npm:/bin; %s; printf %%s \"$PATH\"" % blp.STRIP_HOST_PATH],
                              capture_output=True, text=True)
    cases.append(("the host's /mnt PATH entries are dropped, as a CI runner has no Windows binaries",
                  stripped.stdout == "/usr/bin:/bin"
                  and script.index(blp.STRIP_HOST_PATH) < script.index('export PATH="$HOME/.local/ci-tools/py/bin:')))
    cases.append(("the snapshot excludes .git and is committed to a fresh repo, as checkout gives",
                  "--exclude=.git" in script and "git init -q -b main" in script
                  and "commit -qm snapshot" in script))
    cases.append(("the snapshot drops drvfs's executable bits before it is committed, as checkout gives 100644 files",
                  "chmod 644" in script and script.index("chmod 644") < script.index("git init")))
    cases.append(("each step runs under the Actions default shell flags",
                  script.count("bash --noprofile --norc -eo pipefail") == len(steps)))
    cases.append(("every step reports pass or fail by name",
                  all("::step %s" % n in script for n, _ in steps)))
    cases.append(("a step body with quotes survives as a heredoc, not an argument",
                  "<<'PREVIEW_STEP_EOF'" in script and "pwsh -NoProfile -Command 'exit 0'" in script))
    with_exec = blp.preview_script("/mnt/c/w/tree", jobs, None, ["a/run.sh", "it's.sh"])
    cases.append(("a 100755 entry gets chmod +x after the 644 reset and before the commit",
                  "chmod +x 'a/run.sh' 'it'\\''s.sh'" in with_exec
                  and with_exec.index("chmod 644") < with_exec.index("chmod +x") < with_exec.index("git init")))
    cases.append(("no 100755 entry means no chmod +x", "chmod +x" not in script))
    index_repo = Path(tempfile.mkdtemp(prefix="blp_index_"))
    subprocess.run(["git", "-C", str(index_repo), "init", "-q"], check=True)
    (index_repo / "run.sh").write_text("true\n", encoding="utf-8", newline="\n")
    (index_repo / "plain.txt").write_text("x\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(index_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(index_repo), "update-index", "--chmod=+x", "run.sh"], check=True)
    cases.append(("exec_paths_of lists the 100755 index entries and not the 100644 ones",
                  blp.exec_paths_of(index_repo) == ["run.sh"]))
    with_origin = blp.preview_script("/mnt/c/w/tree", jobs, "https://example.invalid/b.git")
    cases.append(("the snapshot carries the worktree's origin, as checkout does (test_bootstrap reads it)",
                  "git remote add origin 'https://example.invalid/b.git'" in with_origin
                  and with_origin.index("git remote add origin") < with_origin.index("::step")))
    cases.append(("no origin adds no remote", "git remote add" not in script))

    cache = Path(tempfile.mkdtemp(prefix="blp_cache_"))
    older = cache / "20260101T000000Z-aaaaaaaa-worktree"
    newer = cache / "20260202T000000Z-bbbbbbbb-worktree"
    for d in (older, newer):
        (d / ".github" / "workflows").mkdir(parents=True)
    cases.append(("no journal id picks the newest worktree", blp.resolve_worktree(cache, None) == newer))
    cases.append(("a journal id picks its worktree",
                  blp.resolve_worktree(cache, "20260101T000000Z-aaaaaaaa") == older))
    cases.append(("an unknown journal id resolves to None",
                  blp.resolve_worktree(cache, "20990101T000000Z-cccccccc") is None))
    cases.append(("an empty cache resolves to None",
                  blp.resolve_worktree(Path(tempfile.mkdtemp(prefix="blp_empty_")), None) is None))

    quoted = blp.preview_script("/mnt/c/w/tree", [("j", [("say \"hi\" 'now'", "true")])])
    cases.append(("one sanitized label names a step on its ::step, ::pass and ::fail lines",
                  "::step say hi now'" in quoted and '::pass say hi now ' in quoted
                  and '::fail say hi now rc=' in quoted))

    real_cache = blp.CACHE
    stepless = Path(tempfile.mkdtemp(prefix="blp_nosteps_"))
    wt = stepless / "20260303T000000Z-dddddddd-worktree"
    (wt / ".github" / "workflows").mkdir(parents=True)
    (wt / ".github" / "workflows" / "baseline.yml").write_text("jobs: {}\n", encoding="utf-8")
    blp.CACHE = stepless
    try:
        rc = blp.main(["20260303T000000Z-dddddddd"])
    finally:
        blp.CACHE = real_cache
    cases.append(("a workflow with no run: step exits 3, never a green preview of nothing", rc == 3))

    import contextlib
    import io
    direct = Path(tempfile.mkdtemp(prefix="blp_direct_")) / "anywhere"
    (direct / ".github" / "workflows").mkdir(parents=True)
    (direct / ".github" / "workflows" / "baseline.yml").write_text("jobs: {}\n", encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = blp.main(["--worktree", str(direct)])
    cases.append(("--worktree PATH previews a tree outside the cache (it reached the workflow parse)",
                  rc == 3 and "run:" in err.getvalue()))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = blp.main(["--worktree", str(direct.parent / "missing")])
    cases.append(("--worktree to a missing path exits 3", rc == 3 and "worktree" in err.getvalue()))

    (direct / ".github" / "workflows" / "baseline.yml").write_text(
        "jobs:\n  t:\n    steps:\n      - run: echo hi\n", encoding="utf-8")
    seen = []
    real_run, real_no_wsl, real_cwd = blp._run_wsl, blp._no_wsl, os.getcwd()
    blp._run_wsl, blp._no_wsl = (lambda script, log, *rest: seen.append(script) or 0), (lambda: False)
    try:
        os.chdir(direct.parent)
        with contextlib.redirect_stdout(io.StringIO()):
            blp.main(["--worktree", direct.name])
    finally:
        os.chdir(real_cwd)
        blp._run_wsl, blp._no_wsl = real_run, real_no_wsl
    cases.append(("a relative --worktree reaches WSL as the absolute path of that tree",
                  len(seen) == 1 and blp._quote(blp.wsl_path(str(direct.resolve()))) in seen[0]))

    cases.append(("a pwsh hash missing from hashes.sha256 stops setup instead of skipping the check",
                  '[ -n "$want" ] &&' not in blp.SETUP_SCRIPT
                  and '[ -n "$want" ] ||' in blp.SETUP_SCRIPT and 'exit 1' in blp.SETUP_SCRIPT))
    cases.append(("setup verifies both downloads before unpacking",
                  "sha256sum -c" in blp.SETUP_SCRIPT and blp.SETUP_SCRIPT.count("sha256sum") >= 2
                  and "iconv -f UTF-16" in blp.SETUP_SCRIPT))


    posix = lambda p: (subprocess.run([bash_exe(), "-c", 'cygpath -u "$1"', "_", str(p)],
                                      capture_output=True, text=True).stdout.strip()
                       if os.name == "nt" else str(p))

    def run_lanes(job_list, tag):
        home = Path(tempfile.mkdtemp(prefix="blp_home_"))
        tree = Path(tempfile.mkdtemp(prefix="blp_tree_"))
        (tree / "f.txt").write_text("x\n", encoding="utf-8", newline="\n")
        env = dict(os.environ, HOME=str(home))
        env.pop("HARNESS_MACHINE_SLOTS", None)
        out = subprocess.run([bash_exe(), "--noprofile", "--norc", "-c", local_script(posix(tree), job_list(posix(home))) ],
                             capture_output=True, text=True, env=env, timeout=120)
        return out

    wait_a = ('for i in $(seq 1 100); do [ -e "$M" ] && exit 0; sleep 0.1; done; exit 1')
    two = lambda h: [("a", [("wait for b", 'M=%s/marker\n%s' % (h, wait_a))]),
                     ("b", [("write marker", "touch %s/marker" % h)])]
    out = run_lanes(two, "two")
    cases.append(("two jobs run at once: A sees B's marker, so the run passes", out.returncode == 0))
    cases.append(("a two-lane run's stdout holds both ::lane lines",
                  "::lane a " in out.stdout and "::lane b " in out.stdout
                  and out.stdout.index("::lane a ") < out.stdout.index("::lane b ")))
    cases.append(("a pass line carries elapsed seconds", re.search(r"::pass write marker \d+s", out.stdout) is not None))
    red = lambda h: [("ok", [("fine", "true")]), ("bad", [("boom", "exit 7"), ("after", "true")])]
    out = run_lanes(red, "red")
    cases.append(("a red lane exits 1 and later steps in that lane still run",
                  out.returncode == 1 and "::pass after" in out.stdout))
    cases.append(("the fail line sits inside its own lane's log block",
                  re.search(r"::lane ok \d+s\n(?:.*\n)*?::fail boom rc=7 \d+s\n(?:.*\n)*?::lane bad \d+s", out.stdout) is not None))
    expr = lambda h: [("t", [("totals", 'echo "base ${{ github.event.before || github.event.pull_request.base.sha }}!"')])]
    out = run_lanes(expr, "expr")
    cases.append(("a ${{ }} expression in a run: step expands empty, as an unset context does, instead of failing bash",
                  out.returncode == 0 and "::pass totals" in out.stdout and "base !" in out.stdout
                  and "bad substitution" not in out.stdout + out.stderr))
    cases.append(("each lane gets its own HOME and joins the Windows machine pool through the bridge",
                  'HOME="$D/a.home"' in script_of_two(blp) and 'export HARNESS_MACHINE_ROOT=' in script_of_two(blp)
                  and 'HARNESS_WINDOWS_SLOT_SCRIPT' in script_of_two(blp)
                  and 'HARNESS_SLOT_DIR="$R/.slots"' not in script_of_two(blp)))
    cases.append(("a lane holds no machine slot, so its runner takes one per proof instead of running serially",
                  '"$HARNESS_SLOT_SCRIPT" run --' not in script_of_two(blp)))

    home = Path(tempfile.mkdtemp(prefix="blp_home_"))
    tree = Path(tempfile.mkdtemp(prefix="blp_tree_"))
    (tree / "f.txt").write_text("x\n", encoding="utf-8", newline="\n")
    env = dict(os.environ, HOME=str(home))
    env.pop("HARNESS_MACHINE_SLOTS", None)
    launch = lambda job_list: subprocess.Popen(
        [bash_exe(), "--noprofile", "--norc", "-c", local_script(posix(tree), job_list)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    hold = ("touch %s/first_up\nfor i in $(seq 1 300); do [ -e %s/second_done ] && exit 0; sleep 0.1; done; exit 1"
            % (posix(home), posix(home)))
    first = launch([("a", [("hold", hold)])])
    deadline = time.time() + 30
    while not (home / "first_up").exists() and time.time() < deadline:
        time.sleep(0.1)
    second = launch([("b", [("quick", "true")])])
    second_out = second.communicate(timeout=60)[0]
    (home / "second_done").write_text("", encoding="utf-8")
    first_out = first.communicate(timeout=60)[0]
    cases.append(("a preview started while another runs leaves the first run's lanes and logs whole",
                  first.returncode == 0 and "::pass hold" in first_out and "::lane a " in first_out))
    cases.append(("the second preview passes beside the first",
                  second.returncode == 0 and "::lane b " in second_out))
    third = launch([("c", [("quick", "true")])])
    third.communicate(timeout=60)
    cases.append(("a green run removes its own tree and the trees of runs that ended, so none is left",
                  len(list((home / "baseline-linux-preview").glob("preview.*"))) == 0))
    here = Path(__file__).resolve().parent
    # Recorded from the preview itself: a planted worktree whose three lanes run `python3 -m pytest -n auto -q
    # --durations=20` in WSL, one tree all green, one with a collection error (root) and two failing tests (the others).
    red_lines = (here / "linux_preview_red.fixture.log").read_text(encoding="utf-8").splitlines()
    green_lines = (here / "linux_preview_green.fixture.log").read_text(encoding="utf-8").splitlines()
    lane_names = ["root", "template", "pure"]

    green = blp.summarize(green_lines, lane_names)
    cases.append(("a recorded all-green preview log summarizes green with three lanes and their seconds",
                  green["status"] == "green" and [l["lane"] for l in green["lanes"]] == lane_names
                  and green["lanes"][1]["seconds"] == 5 and not any(l["failed_steps"] for l in green["lanes"])))
    cases.append(("a green lane's counts come from pytest's closing line",
                  [l["counts"] for l in green["lanes"]] == [
                      {"run": 2, "pass": 2, "fail": 0, "cannot_run": 0}, {"run": 4, "pass": 4, "fail": 0, "cannot_run": 0},
                      {"run": 4, "pass": 4, "fail": 0, "cannot_run": 0}]))
    red_sum = blp.summarize(red_lines, lane_names)
    by_lane = {l["lane"]: l for l in red_sum["lanes"]}
    cases.append(("a recorded red preview log names each red lane and its failed step",
                  red_sum["status"] == "red"
                  and by_lane["template"]["failed_steps"] == ["template harness battery"]
                  and by_lane["root"]["failed_steps"] == ["totals (code, test, prose) and their change",
                                                          "baseline-repo-root proofs"]
                  and by_lane["pure"]["status"] == "red"))
    cases.append(("a red lane names its failing tests' files from pytest's FAILED and ERROR lines, once each",
                  by_lane["template"]["failed_proofs"] == ["test_red.py", "floor/test_floor_row.py"]
                  and by_lane["pure"]["failed_proofs"] == ["floor/test_floor_row.py", "test_red.py"]
                  and by_lane["root"]["failed_proofs"] == ["test_root_broken.py"]))
    cases.append(("a red lane's counts come from pytest's closing line, a collection error counting as a failure",
                  by_lane["template"]["counts"] == {"run": 6, "pass": 4, "fail": 2, "cannot_run": 0}
                  and by_lane["root"]["counts"] == {"run": 3, "pass": 2, "fail": 1, "cannot_run": 0}))
    cases.append(("a lane the workflow declares but the log never closes is incomplete, never green",
                  blp.summarize(green_lines, lane_names + ["ghost"])["status"] == "incomplete"
                  and blp.summarize([], lane_names)["status"] == "incomplete"))

    import contextlib
    import io

    scripts: list[str] = []

    def run_main(argv, sink_lines, rc, no_wsl=False):
        """main() against a stubbed WSL launch that replays recorded output; returns (rc, stdout)."""
        saved = (blp._run_wsl, blp.shutil.which)

        def fake_run(script, log_path, sink=None):
            scripts.append(script)
            for line in sink_lines:
                if sink is not None:
                    sink.append(line)
            return rc
        blp._run_wsl = fake_run
        blp.shutil.which = (lambda *_a, **_k: None) if no_wsl else (lambda *_a, **_k: "wsl.exe")
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = blp.main(argv)
        finally:
            blp._run_wsl, blp.shutil.which = saved
        return code, out.getvalue()

    tree = Path(tempfile.mkdtemp(prefix="blp_run_")) / "wt"
    (tree / ".github" / "workflows").mkdir(parents=True)
    (tree / ".github" / "workflows" / "baseline.yml").write_text(
        "jobs:\n" + "".join("  %s:\n    steps:\n      - run: true\n" % n for n in lane_names), encoding="utf-8")
    code, text = run_main(["--worktree", str(tree)], green_lines, 0)
    result = json.loads(next(l for l in text.splitlines() if l.startswith("::result "))[len("::result "):])
    cases.append(("recorded green output exits 0 and prints one ::result line with status green",
                  code == 0 and result["status"] == "green" and result["lanes"][1]["lane"] == "template"))
    code, text = run_main(["--worktree", str(tree), "--lanes", "pure,template"], green_lines, 0)
    only_script = scripts[-1]
    cases.append(("--lanes runs only the named lanes: no other lane is copied or run",
                  code == 0 and 'cp -a "$D/src" "$D/pure"' in only_script and 'cp -a "$D/src" "$D/template"' in only_script
                  and '"$D/root"' not in only_script))
    code, _text = run_main(["--worktree", str(tree), "--lanes", "ghost"], green_lines, 0)
    cases.append(("--lanes naming a lane the workflow lacks exits 3", code == 3))
    code, text = run_main(["--worktree", str(tree)], red_lines, 1)
    result = json.loads(next(l for l in text.splitlines() if l.startswith("::result "))[len("::result "):])
    cases.append(("recorded red output exits 1 and its ::result names the red lanes and their failing tests",
                  code == 1 and result["status"] == "red"
                  and [l["failed_proofs"] for l in result["lanes"] if l["lane"] == "template"] == [
                      ["test_red.py", "floor/test_floor_row.py"]]))
    code, _text = run_main(["--worktree", str(tree)], ["wsl: no distribution installed"], 1)
    cases.append(("a nonzero exit with no ::fail line is a preview that could not run: exit 3, never a red verdict",
                  code == 3))
    code, text = run_main(["--worktree", str(tree)], [], 0, no_wsl=True)
    cases.append(("no WSL exits 4 and ::result says unavailable with the reason",
                  code == 4 and '"status": "unavailable"' in text and "no WSL" in text))

    tally = "15 failed, 2083 passed, 1 skipped in 650.97s (0:10:50)"
    cases.append(("pytest's closing line reaches the verdict sink", blp.VERDICT_LINE.match(tally) is not None
                  and blp.VERDICT_LINE.match("2 passed, 1 error in 3.08s") is not None
                  and blp.VERDICT_LINE.match("no tests ran in 0.01s") is not None))
    counted = blp.summarize([tally, "::lane pure 40s"], ["pure"])
    cases.append(("a skipped test is counted as not run, and the lane's verdict still comes from its steps",
                  counted["status"] == "green"
                  and counted["lanes"][0]["counts"] == {"run": 2099, "pass": 2083, "fail": 15, "cannot_run": 1}))
    cases.append(("a run that collects nothing reports zero run",
                  blp.summarize(["no tests ran in 0.01s", "::lane pure 1s"], ["pure"])["lanes"][0]["counts"]
                  == {"run": 0, "pass": 0, "fail": 0, "cannot_run": 0}))
    cases.append(("a lane with no pytest closing line reports no counts",
                  blp.summarize(["::lane root 3s"], ["root"])["lanes"][0]["counts"] is None))
    nested = blp.summarize(["1 passed in 0.15s", "FAILED inner/tests/test_inner.py::t - boom",
                            "=========================== short test summary info ============================",
                            "FAILED tests/test_outer.py::t - AssertionError", "ERROR tests/test_broken.py - ImportError",
                            "3 failed, 2 passed in 9.00s", "::fail battery rc=1 9s", "::lane root 9s"], ["root"])
    cases.append(("a nested run's lines inside a failing test's captured output name no proof and set no counts",
                  nested["lanes"][0]["failed_proofs"] == ["test_outer.py", "test_broken.py"]
                  and nested["lanes"][0]["counts"] == {"run": 5, "pass": 2, "fail": 3, "cannot_run": 0}))

    helped = io.StringIO()
    with contextlib.redirect_stdout(helped), contextlib.redirect_stderr(io.StringIO()):
        helped_rc = blp.main(["--help"])
    cases.append(("--help prints usage and exits 0; it is never read as a journal id",
                  helped_rc == 0 and "--worktree" in helped.getvalue() and "--setup" in helped.getvalue()))
    unknown_err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(unknown_err):
        unknown_rc = blp.main(["--bogus"])
    cases.append(("an unknown flag exits 2 with usage, never read as a journal id",
                  unknown_rc == 2 and "usage" in unknown_err.getvalue().lower()))

    mine = blp.preview_script("/mnt/c/w/tree", [("a", [("s", "true")])], run="mine")
    theirs = blp.preview_script("/mnt/c/w/tree", [("a", [("s", "true")])], run="theirs")
    cases.append(("each run owns its WSL directory: two previews never share one",
                  'D="$R/mine.$$"' in mine and 'D="$R/theirs.$$"' in theirs
                  and 'rm -rf "$D"' in mine and 'rm -rf "$R"' not in mine
                  and 'rm -rf "$HOME/baseline-linux-preview"' not in mine))
    cases.append(("a green run removes its own directory; a red one keeps it for inspection",
                  '[ "$fail" = 0 ] && rm -rf "$D"' in mine))
    cases.append(("a tree whose run ended is swept while a live peer's is kept, and the shared slot dir is not",
                  '! kill -0 "${n##*.}"' in mine and 'case "$n" in .*) continue ;; esac' in mine
                  and mine.index("kill -0") < mine.index('rm -rf "$D" &&')))
    guarded = blp.preview_script("/mnt/c/w/tree", [("a", [("s", "true")])], run="mine", preflight=True)
    cases.append(("preflight names the tool a machine lacks and exits 4, before any copy",
                  "exit 4" in guarded and "::unavailable" in guarded
                  and guarded.index("exit 4") < guarded.index("tar -C")))
    empty_home = Path(tempfile.mkdtemp(prefix="blp_nohome_"))
    probe = subprocess.run([bash_exe(), "--noprofile", "--norc", "-c", guarded], capture_output=True, text=True,
                           env=dict(os.environ, HOME=str(empty_home)), timeout=60)
    cases.append(("run for real in a HOME with no ci-tools, preflight exits 4 and prints ::unavailable",
                  probe.returncode == 4 and "::unavailable" in probe.stdout and "--setup" in probe.stdout))
    cases.append(("without preflight the script carries no tool check",
                  "::unavailable" not in mine))

    call = "python3 -m pytest -n auto -q --durations=20 "
    pytest_jobs = [("root", [("install pytest", "python3 -m pip install pytest pytest-xdist"),
                             ("baseline-repo-root proofs", call + "tests")]),
                   ("template", [("template harness battery", call + "template/.claude/tests")]),
                   ("pure", [("pure profile battery", 'set -euo pipefail\ncd "$t" && ' + call + ".claude/tests")]),
                   ("other", [("elsewhere", "python3 -m pytest -q somewhere/else")])]
    two = ["test_a_tool.py", "test_b_tool.py"]
    in_template = ["template/.claude/tools/a_tool.py", "template/.claude/tools/b_tool.py"]
    template_tree = Path(tempfile.mkdtemp(prefix="blp_lane_t_"))
    profile_tree = Path(tempfile.mkdtemp(prefix="blp_lane_p_"))
    for tree_root, tests in ((template_tree / "template" / ".claude" / "tests", two),
                             (profile_tree / ".claude" / "tests", two[:1])):
        tree_root.mkdir(parents=True)
        for name in tests:
            (tree_root / name).write_text("x = 1\n", encoding="utf-8")

    def lane_body(lane, paths, bound, step=-1):
        return dict(blp.scope_to_paths(pytest_jobs, paths, bound))[lane][step][1]

    def run_lane(body, cwd):
        """The step body under the Actions shell flags, with `python3` standing in as a function that prints its arguments."""
        stub = "python3() { printf 'pytest: %s\\n' \"$*\"; }\n"
        return subprocess.run([bash_exe(), "--noprofile", "--norc", "-eo", "pipefail", "-c", stub + body], cwd=cwd,
                              capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, t=Path(profile_tree).as_posix()))

    held = run_lane(lane_body("template", in_template, two), template_tree)
    cases.append(("a template lane runs pytest on exactly the bound proofs, under the lane's own options",
                  held.returncode == 0 and held.stdout.splitlines() == [
                      "pytest: -m pytest -n auto -q --durations=20 template/.claude/tests/test_a_tool.py"
                      " template/.claude/tests/test_b_tool.py"]))
    stripped = run_lane(lane_body("pure", in_template, two), profile_tree)
    cases.append(("a profile lane runs only the bound proofs its install holds",
                  stripped.returncode == 0 and stripped.stdout.splitlines() == [
                      "pytest: -m pytest -n auto -q --durations=20 .claude/tests/test_a_tool.py"]))
    absent = run_lane(lane_body("pure", in_template, two[1:]), profile_tree)
    cases.append(("a profile lane holding none of the bound proofs runs nothing and is green, with pytest's zero-run line",
                  absent.returncode == 0 and "pytest:" not in absent.stdout
                  and [l for l in absent.stdout.splitlines() if blp.VERDICT_LINE.match(l)] != []
                  and blp.summarize(absent.stdout.splitlines() + ["::lane pure 1s"], ["pure"])["lanes"][0]["counts"]
                  == {"run": 0, "pass": 0, "fail": 0, "cannot_run": 0}))
    unbound = run_lane(lane_body("template", ["template/.claude/tools/zzz_unbound.py"], []), template_tree)
    cases.append(("a change that binds no proof is green with zero run, not a failed step",
                  unbound.returncode == 0 and "pytest:" not in unbound.stdout and "no tests ran" in unbound.stdout))
    outside = lane_body("template", ["tests/test_root.py"], [])
    cases.append(("a change outside template/ leaves the template lane nothing to run", "python3 -m pytest" not in outside
                  and "no tests ran" in outside))
    only_template = lane_body("root", in_template, two)
    root_change = lane_body("root", in_template + ["tools/gen_manifest.py"], two)
    cases.append(("the baseline-repo root proofs run only when a path outside template/ changed",
                  "python3 -m pytest" not in only_template and "no tests ran" in only_template
                  and root_change == call + "tests"))
    cases.append(("paths=None keeps every call whole, and only a pytest call over a tests folder is ever rewritten",
                  dict(blp.scope_to_paths(pytest_jobs, None, None)) == dict(pytest_jobs)
                  and lane_body("root", in_template, two, 0) == "python3 -m pip install pytest pytest-xdist"
                  and lane_body("other", in_template, two) == "python3 -m pytest -q somewhere/else"
                  and lane_body("pure", in_template, two).startswith('set -euo pipefail\ncd "$t" && ')))

    worktree = Path(tempfile.mkdtemp(prefix="blp_bound_"))
    (worktree / "template" / ".claude" / "tools").mkdir(parents=True)
    (worktree / "template" / ".claude" / "tests").mkdir()
    for stem in ("a_tool", "b_tool", "c_tool"):
        (worktree / "template" / ".claude" / "tools" / (stem + ".py")).write_text("x = 1\n", encoding="utf-8")
        (worktree / "template" / ".claude" / "tests" / ("test_" + stem + ".py")).write_text(
            "import sys\nsys.exit(0)\n", encoding="utf-8")
    cases.append(("bound_proofs names the proofs the runner's select_for binds to the changed template paths",
                  blp.bound_proofs(worktree, in_template + ["tests/test_root.py"]) == two
                  and blp.bound_proofs(worktree, ["template/.claude/tools/zzz_unbound.py"]) == []
                  and blp.bound_proofs(worktree, ["tests/test_root.py"]) == []
                  and blp.bound_proofs(worktree, None) is None))
    repo = Path(tempfile.mkdtemp(prefix="blp_changed_"))
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo)] + args, check=True)
    (repo / "keep.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    (repo / "template").mkdir()
    (repo / "template" / "n.py").write_text("1\n", encoding="utf-8")
    (repo / "keep.txt").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "publish"], check=True)
    cases.append(("changed_paths lists what the publish commit adds, changes and deletes",
                  sorted(blp.changed_paths(repo)) == ["keep.txt", "template/n.py"]))

    failed = 0
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        failed += not ok
    print("\n%d/%d cases pass" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
