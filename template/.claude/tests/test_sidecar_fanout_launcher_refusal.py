#!/usr/bin/env python3
"""RED-first proof: `tools/sidecar_fanout.py` refuses, in the caller's own call, every job its launcher
would refuse from the job row alone, before it spawns or detaches anything.

The captured failure (session 9b60b8a7, 2026-10-25T00:55Z): one Bash call wrote a jobs file (`alias: opus`,
`shape: survey`) and ran `sidecar_fanout.py <jobs> --detach`. The PreToolUse hook could not read a file the
same call wrote, `--detach` ran only the session-state doctrine check, and the detached child's launcher
refused the pin ("Sidecar launch outside the tier its shape implies") into a `.err` file nobody read. The
caller saw `fan-out detached: driver pid ...` and waited 25 minutes.

Fakes only: `detach_driver`, `run_jobs`, the duplicate-launch scan, the capacity read and the doctrine
check are replaced, so no process is spawned and nothing is billed. The unfit and fit pins are read from the
live registry and shape tiers (a pair that is unfit today stays unfit-by-construction tomorrow), never named.

Run: python3 .claude/tests/test_sidecar_fanout_launcher_refusal.py
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
import _dispatch_checks as dc  # noqa: E402
import model_registry  # noqa: E402
import sidecar_dispatch_context as ctx  # noqa: E402

spec = importlib.util.spec_from_file_location("sf_launcher_refusal", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_refusal_")
DATA = model_registry.load()
TIERS = dc.shape_tiers()
CALLS = {"detach": 0, "run": 0}
REFUSAL_HEAD = "Sidecar launch outside the tier its job implies"


def _fake_detach(argv, jobs, out_dir, claim=None, log_path=None):
    CALLS["detach"] += 1
    return 0


class Launched(Exception):
    """`run_jobs` was reached: the fake stops the call there, so nothing is summarized or billed."""


def _fake_run(*args, **kwargs):
    CALLS["run"] += 1
    raise Launched()


sf.detach_driver = _fake_detach
sf.run_jobs = _fake_run
sf.duplicate_launches = lambda planned: ""
sf.admission_refusal = lambda jobs, data, read=None, now=None: None
sf.doctrine_refusal = lambda argv: None


def anthropic_aliases(shape, fits):
    """Available anthropic aliases whose pin does (fits) or does not fit `shape`'s tier, per the real check."""
    tier = TIERS[shape]
    return sorted(row["alias"] for row in DATA["models"]
                  if row.get("transport") == "anthropic" and row.get("alias")
                  and model_registry.model_available(row["id"], DATA)
                  and (dc.tier_fit(row["alias"], tier, "anthropic", DATA) is None) is fits)


UNFIT = anthropic_aliases("survey", False)
FIT = anthropic_aliases("survey", True)
assert len(UNFIT) >= 2 and FIT, ("the registry no longer has two unfit and one fit anthropic pin for the survey "
                                 "shape; pick another shape", UNFIT, FIT)

PROMPT = os.path.join(TMP, "brief.md")
with open(PROMPT, "w", encoding="utf-8") as fh:
    fh.write("Read README and say what it is.\n")


def job(label, alias, shape="survey", **extra):
    return dict({"label": label, "alias": alias, "effort": "high", "shape": shape, "promptFile": PROMPT,
                 "plan": "anthropic"}, **extra)


def run(jobs, *flags):
    """(exit code, stdout, stderr, detach calls, run calls, out dir) for one `main` call over `jobs`."""
    work = tempfile.mkdtemp(dir=TMP)
    jobs_path = os.path.join(work, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh)
    out_dir = os.path.join(work, "out")
    CALLS.update(detach=0, run=0)
    out, err = io.StringIO(), io.StringIO()
    previous = os.getcwd()
    os.chdir(work)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = sf.main([jobs_path, "--out-dir", out_dir, *flags])
            except Launched:
                code = "launched"
    finally:
        os.chdir(previous)
    return code, out.getvalue(), err.getvalue(), CALLS["detach"], CALLS["run"], out_dir


CASES = []


def case(name, fn):
    CASES.append((name, fn))


mixed = [job("unfit-a", UNFIT[0]), job("fit-b", FIT[0]), job("unfit-c", UNFIT[1])]

for mode in ("--detach", "--dry-run", None):
    flags = (mode,) if mode else ()
    shown = mode or "foreground"

    def refused(flags=flags):
        code, out, err, detached, ran, out_dir = run(mixed, *flags)
        return (code == 2 and detached == 0 and ran == 0 and not os.path.exists(os.path.join(out_dir, "fanout-driver.log"))
                and "Nothing dispatched" not in out)

    def names_each(flags=flags):
        _code, out, err, *_ = run(mixed, *flags)
        text = out + err
        return (REFUSAL_HEAD in text and "unfit-a" in text and "unfit-c" in text and "fit-b" not in text.replace("unfit-", ""))

    case("%s: a job set holding unfit pins exits 2 and starts nothing" % shown, refused)
    case("%s: the refusal names every unfit job with the launcher's text, and not the fit one" % shown, names_each)

    def admits(flags=flags):
        code, _out, err, detached, ran, _ = run([job("fit-only", FIT[0])], *flags)
        reached = {"--detach": (0, 1, 0), "--dry-run": (0, 0, 0)}.get(flags[0] if flags else None, ("launched", 0, 1))
        return (code, detached, ran) == reached and REFUSAL_HEAD not in err

    case("%s: a job set whose pins all fit is admitted (must-allow)" % shown, admits)


def refused_text_is_the_launchers():
    """The tool prints the text `launcher_launch` prints: one source, `sidecar_dispatch_context.dispatch_checks`."""
    expected = ctx.dispatch_checks([ctx.LaunchJob("unfit-a", UNFIT[0], "survey", "anthropic")])
    _code, out, err, *_ = run([job("unfit-a", UNFIT[0])], "--detach")
    return bool(expected) and expected in (out + err)


case("the refusal text is the launcher's own, from the one check", refused_text_is_the_launchers)


def tier_less_shape_is_never_refused():
    tierless = sorted(set(("any", "arm")) - set(TIERS))
    if not tierless:
        return True
    code, _out, err, *_ = run([job("loose", UNFIT[0], shape=tierless[0])], "--dry-run")
    return code == 0 and REFUSAL_HEAD not in err


case("a shape that declares no tier is never refused for its pin", tier_less_shape_is_never_refused)

# Refusals planning already ran in the caller's own call (effort.avoid, unknown alias, unknown shape): pinned so a
# later reorder cannot move them behind `--detach`. These held before the fix; the tier fit is what moved.
avoiding = sorted((row["alias"], rung) for row in DATA["models"] if row.get("alias") and row.get("transport") == "anthropic"
                  and model_registry.model_available(row["id"], DATA) for rung in model_registry.avoided_rungs(row)
                  if dc.tier_fit(row["alias"], TIERS["survey"], "anthropic", DATA) is None)


def avoided_effort_refused():
    if not avoiding:
        return True
    alias, rung = avoiding[0]
    code, out, err, detached, ran, _ = run([job("avoided", alias, effort=rung)], "--detach")
    return code == 2 and detached == 0 and "effort.avoid" in out + err


def unknown_alias_refused():
    code, out, err, detached, ran, _ = run([job("ghost", "no-such-alias-9")], "--detach")
    return code == 2 and detached == 0 and "ghost" in out + err


def unknown_shape_refused():
    code, out, err, detached, ran, _ = run([job("odd-shape", FIT[0], shape="no-such-shape")], "--detach")
    return code == 2 and detached == 0


case("(already held) an effort the registry's effort.avoid lists is refused before detach", avoided_effort_refused)
case("(already held) an unknown alias is refused before detach", unknown_alias_refused)
case("(already held) an unknown shape is refused before detach", unknown_shape_refused)


def fold_author_pin_is_checked():
    author_unfit = anthropic_aliases("author", False)
    if not author_unfit:
        return True
    work = tempfile.mkdtemp(dir=TMP)
    jobs_path = os.path.join(work, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump([job("seat-1", FIT[0], shape="review")], fh)
    refusal = ctx.fanout_fit_refusal([jobs_path, "--review", "--fold-alias", author_unfit[0], "--fold-effort", "high",
                                      "--fold-subject", "%s::%s::a.md" % (work, os.path.join(work, "m.json"))], work)
    return bool(refusal) and "review-fold-1" in refusal and REFUSAL_HEAD in refusal


case("a --fold author is checked beside the seats, as the PreToolUse hook checks it", fold_author_pin_is_checked)

for mode in ("--detach", "--dry-run", None):
    flags = (mode,) if mode else ()

    def prints_the_fit(flags=flags):
        code, out, _err, *_ = run([job("fit-only", FIT[0])], *flags)
        return "fit: `fit-only` (%s) reading" % FIT[0] in out and "of its compaction point" in out

    def fit_lines_are_not_a_refusal(flags=flags):
        code, out, _err, *_ = run([job("fit-only", FIT[0])], *flags)
        return code != 2 and "fit: `fit-only`" in out

    case("%s: each job's window fit is printed before it detaches or spawns, beside the tier-fit check" %
         (mode or "foreground"), prints_the_fit)
    case("%s: the fit line is advisory, never a refusal" % (mode or "foreground"), fit_lines_are_not_a_refusal)


def refused_set_prints_no_fit():
    _code, out, err, *_ = run(mixed, "--detach")
    return "fit: " not in out + err


case("a refused job set prints the refusal and no fit line", refused_set_prints_no_fit)


def receipted_job_is_not_checked_twice():
    """The hook's fan-out receipt (written by the real `remember_checks`) means it already ran the tier check for
    that job, so the fan-out's own check skips it; a job with no receipt is still checked."""
    import types
    work = tempfile.mkdtemp(dir=TMP)
    jobs_path = os.path.join(work, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump([job("fit-r", FIT[0])], fh)
    args = [jobs_path, "--out-dir", os.path.join(work, "out")]
    env = {"HARNESS_HOOK_STATE_DIR": tempfile.mkdtemp(dir=TMP), "CLAUDE_CODE_SESSION_ID": "rcpt0001-session"}
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    calls, real = [], ctx._dispatch_checks.tier_fit
    ctx._dispatch_checks.tier_fit = lambda *a, **k: calls.append(a[0]) or real(*a, **k)
    try:
        unreceipted = (ctx.fanout_fit_refusal(args, work), len(calls))
        ctx.remember_checks({"session_id": env["CLAUDE_CODE_SESSION_ID"], "tool_use_id": "call-1", "cwd": work},
                            None, types.SimpleNamespace(args=args), work, "skipped")
        receipted = (ctx.fanout_fit_refusal(args, work), len(calls) - unreceipted[1])
    finally:
        ctx._dispatch_checks.tier_fit = real
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return unreceipted == (None, 1) and receipted == (None, 0)


case("a job the hook's receipt shows checked is not tier-checked again by the fan-out", receipted_job_is_not_checked_twice)


def _aliases(fits_tier, misses_tier):
    return sorted(row["alias"] for row in DATA["models"]
                  if row.get("transport") == "anthropic" and row.get("alias")
                  and model_registry.model_available(row["id"], DATA)
                  and dc.tier_fit(row["alias"], fits_tier, "anthropic", DATA) is None
                  and dc.tier_fit(row["alias"], misses_tier, "anthropic", DATA) is not None)


ARCH_NOT_SURVEY = _aliases("architect", TIERS["survey"])
REVIEW_NOT_ARCH = _aliases(TIERS["review"], "architect")
assert ARCH_NOT_SURVEY and REVIEW_NOT_ARCH, ("the registry no longer separates architect from the survey and review "
                                             "tiers on anthropic", ARCH_NOT_SURVEY, REVIEW_NOT_ARCH)


def written_tier_binds():
    code, out, err, *_ = run([job("arch-read", ARCH_NOT_SURVEY[0], tier="architect")], "--dry-run")
    return code == 0 and REFUSAL_HEAD not in out + err


def written_tier_refuses_the_pin_it_does_not_fit():
    code, out, err, *_ = run([job("fan-read", ARCH_NOT_SURVEY[0], tier=TIERS["survey"])], "--dry-run")
    text = out + err
    return code == 2 and REFUSAL_HEAD in text and "fan-read" in text and "the job's `tier`" in text


def review_shape_no_longer_admits_a_cheaper_pin_for_architect_work():
    code, out, err, *_ = run([job("arch-review", REVIEW_NOT_ARCH[0], shape="review", tier="architect")], "--dry-run")
    return code == 2 and REFUSAL_HEAD in out + err and "`architect`" in out + err


def no_tier_keeps_the_shape_default():
    unfit, _o, err, *_ = run([job("shape-unfit", ARCH_NOT_SURVEY[0])], "--dry-run")[:3]
    fit, *_ = run([job("shape-fit", FIT[0])], "--dry-run")
    return unfit == 2 and "from shape `survey`" in _o + err and fit == 0


def unknown_tier_is_refused_with_the_known_list():
    code, out, err, *_ = run([job("odd-tier", FIT[0], tier="bogus")], "--dry-run")
    text = out + err
    return code == 2 and "bogus" in text and all(t in text for t in model_registry.TIER_ORDER)


def launch_argv_carries_the_tier():
    argv = sf.plan_job(sf.normalize_job(job("argv-tier", FIT[0], tier="architect"), DATA), DATA,
                       sf.Path(tempfile.mkdtemp(dir=TMP)), False)[1]
    return "-Q" in argv and argv[argv.index("-Q") + 1] == "architect"


def launch_argv_without_a_tier_has_no_flag():
    argv = sf.plan_job(sf.normalize_job(job("argv-plain", FIT[0]), DATA), DATA,
                       sf.Path(tempfile.mkdtemp(dir=TMP)), False)[1]
    return "-Q" not in argv


case("a read-only survey job pinned to an architect-only row launches when it writes tier architect", written_tier_binds)
case("the same job writing tier fanout is refused, naming the job and the tier's source", written_tier_refuses_the_pin_it_does_not_fit)
case("a review job pinned to an executor-only row is refused when it writes tier architect",
     review_shape_no_longer_admits_a_cheaper_pin_for_architect_work)
case("a job that writes no tier keeps its shape's tier (refused when unfit, admitted when fit)", no_tier_keeps_the_shape_default)
case("an unknown written tier is refused with the known tier list", unknown_tier_is_refused_with_the_known_list)
case("a job's written tier reaches its launcher as -Q", launch_argv_carries_the_tier)
case("a job with no tier passes no -Q", launch_argv_without_a_tier_has_no_flag)


def failed_import_fails_closed():
    saved = sys.modules.get("sidecar_dispatch_context", False)
    sys.modules["sidecar_dispatch_context"] = None
    try:
        text = sf.launcher_fit_refusal([])
    finally:
        sys.modules["sidecar_dispatch_context"] = ctx if saved is False else saved
    return bool(text) and "could not load" in text and "halted" in text


case("a tier check whose module cannot import refuses, naming the cause, instead of skipping", failed_import_fails_closed)


def _call_into(out_dir, jobs, *flags):
    work = os.path.dirname(out_dir)
    jobs_path = os.path.join(work, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return sf.main([jobs_path, "--out-dir", out_dir, *flags])


def _refused_file(flags):
    work = tempfile.mkdtemp(dir=TMP)
    out_dir = os.path.join(work, "out")
    code = _call_into(out_dir, mixed, *flags)
    path = os.path.join(out_dir, "launch-refused.txt")
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    return code, text, out_dir


def refusal_is_persisted():
    for flags in (("--detach",), ("--dry-run",), ()):
        code, text, _ = _refused_file(flags)
        if not (code == 2 and text and REFUSAL_HEAD in text and "unfit-a" in text and "exit 2" in text
                and re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", text)):
            return False
    return True


def argument_error_is_persisted():
    work = tempfile.mkdtemp(dir=TMP)
    out_dir = os.path.join(work, "out")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = sf.main([os.path.join(work, "jobs.json"), "--out-dir", out_dir, "--max-parallel", "0"])
    path = os.path.join(out_dir, "launch-refused.txt")
    return code == 2 and os.path.exists(path) and "--max-parallel" in open(path, encoding="utf-8").read()


def later_launch_removes_it():
    code, text, out_dir = _refused_file(("--dry-run",))
    ok_first = code == 2 and text is not None
    results = []
    for flags in (("--dry-run",), ("--detach",)):
        open(os.path.join(out_dir, "launch-refused.txt"), "w").write("stale")
        results.append(_call_into(out_dir, [job("fit-only", FIT[0])], *flags) == 0
                       and not os.path.exists(os.path.join(out_dir, "launch-refused.txt")))
    return ok_first and all(results)


case("a refusal before any inventory writes launch-refused.txt with message, exit code and UTC time", refusal_is_persisted)
case("an argument error writes launch-refused.txt too", argument_error_is_persisted)
case("a later dry-run or detached launch into the same out dir removes launch-refused.txt", later_launch_removes_it)


failed = []
for name, fn in CASES:
    try:
        ok = bool(fn())
    except Exception as exc:  # a crash is a failure, never a pass
        ok = False
        name += "  [%s: %s]" % (type(exc).__name__, exc)
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failed.append(name)
print("%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
sys.exit(1 if failed else 0)
