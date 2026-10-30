#!/usr/bin/env python3
"""RED-first proof: a fan-out job lists the rows that meet its bar (`rows: {alias: effort}`); the fan-out ranks
the union of those rows' plans with `place_job`, then runs the cheapest listed row on the winning plan.

The captured failure (2026-10-27): 71 of 71 overnight jobs pinned an Anthropic row while Codex and Go read Surplus,
because a job's alias fixed its transport and so the plans `place_job` ranked. Every case plants the capacity
readings, the registry's live plans and the finished-job history; no process is a real launcher and nothing is billed.
The provider_bands cases at the end prove the amendment: a rate measured over a span forecasts no further than it.

Run: python3 .claude/tests/test_sidecar_fanout_rows.py
"""
import contextlib
import copy
import importlib.util
import io
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import model_registry  # noqa: E402

spec = importlib.util.spec_from_file_location("sf_rows", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)
pb = sf.provider_bands
import dispatch_size  # noqa: E402

DATA = model_registry.load()
NOW = time.time()
WEEK_MIN = 7 * 24 * 60
LEFT = 0.45
TMP = tempfile.mkdtemp(prefix="sfanout_rows_")
PROMPT = os.path.join(TMP, "brief.md")
with open(PROMPT, "w", encoding="utf-8") as fh:
    fh.write("Do the slice.\n")


def live(*keep):
    """The registry with only `keep` of the anthropic route's plans available."""
    data = copy.deepcopy(DATA)
    for name, cfg in data["transports"].items():
        if cfg.get("planOf") == "anthropic" and name not in keep:
            cfg["status"] = {"state": "unavailable", "reason": "planted"}
    return data


def reading(plan, room, band="Hot", left=LEFT):
    """An available reading whose 7d routing window has `room` (share of allowance left over share of time left)."""
    used = 100.0 - room * left * 100.0
    window = {"name": "seven_day", "usedPercent": used, "resetsAt": NOW + left * WEEK_MIN * 60,
              "windowDurationMins": WEEK_MIN, "pressure": 1.0, "band": band}
    return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": "2027-01-15T08:00:00Z", "status": "available",
            "quota": {"windows": [window], "routingWindow": "seven_day", "bindingWindow": "seven_day",
                      "routingBand": band, "dispatchBand": band}}


def reader(readings):
    def read(plan, _data=None, **_kw):
        value = readings.get(plan)
        if isinstance(value, Exception):
            raise value
        return value
    return read


def fresh():
    sf.FALLBACKS.clear()
    sf.PLACED.clear()
    sf.SLOTS.clear()
    sf.FULL_WINDOW.clear()
    sf._LIVE["load"] = {}
    pb._ledger_jobs = lambda: []
    pb._samples = lambda: []
    pb.reset_drain_cache()


def job(label="j", **extra):
    return dict({"label": label, "kind": "author", "promptFile": PROMPT}, **extra)


def plan(readings, jobspec, data):
    """(argv or the JobError text, stderr) of one `plan_job` over planted readings."""
    fresh()
    saved = pb.read_capacity
    pb.read_capacity = reader(readings)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            try:
                got = sf.plan_job(jobspec, data, TMP, False)[1]
            except sf.JobError as exc:
                got = "REFUSED: " + str(exc)
    finally:
        pb.read_capacity = saved
    return got, err.getvalue()


def flag(argv, name):
    return argv[argv.index(name) + 1] if isinstance(argv, list) and name in argv[:-1] else None


def launcher_of(plan_name):
    return (model_registry.transport_meta(plan_name, DATA) or {}).get("launcher")


def argv_plan(argv):
    return os.path.basename(argv[1]) if isinstance(argv, list) else None


CASES = []


def case(fn):
    CASES.append((fn.__name__.replace("_", " "), fn))
    return fn


@case
def rows_land_on_the_surplus_plan_with_that_rows_alias_and_effort():
    argv, err = plan({"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1, "Surplus")},
                     job(rows={"sonnet": "medium", "sol": "medium"}), live("anthropic"))
    return (flag(argv, "-m") == "sol" and flag(argv, "-e") == "medium"
            and argv_plan(argv) == os.path.basename(launcher_of("codex"))), str(argv)[:300] + err[-300:]


@case
def rows_over_three_routes_land_on_go_when_go_ranks_first():
    readings = {"anthropic": reading("anthropic", 0.87), "anthropic-secondary": None,
                "codex": reading("codex", 0.98), "go": reading("go", 1.11)}
    argv, err = plan(readings, job(rows={"sonnet": "medium", "sol": "medium", "go-deepseek-flash": "high"}),
                     live("anthropic", "anthropic-secondary"))
    return (flag(argv, "-m") == "go-deepseek-flash" and flag(argv, "-e") == "high"
            and argv_plan(argv) == os.path.basename(launcher_of("go"))), str(argv)[:300] + err[-300:]


@case
def two_rows_on_one_plan_run_the_cheaper_one():
    argv, err = plan({"anthropic": reading("anthropic", 0.9)},
                     job(rows={"sonnet": "high", "haiku": "medium"}), live("anthropic"))
    return flag(argv, "-m") == "haiku" and flag(argv, "-e") == "medium", str(argv)[:300] + err[-300:]


@case
def the_admission_line_names_the_row():
    _, err = plan({"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1, "Surplus")},
                  job(rows={"sonnet": "medium", "sol": "medium"}), live("anthropic"))
    return "[fanout] j: plan codex, row sol (medium) (" in err, err[-400:]


@case
def a_one_row_job_places_as_before_and_names_its_row():
    readings = {"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1, "Surplus")}
    argv, err = plan(readings, job(alias="sonnet", effort="medium"), live("anthropic"))
    same = plan(readings, job(rows={"sonnet": "medium"}), live("anthropic"))[0]
    return (flag(argv, "-m") == "sonnet" and flag(argv, "-e") == "medium"
            and argv_plan(argv) == os.path.basename(launcher_of("anthropic"))
            and same == argv and "plan anthropic, row sonnet (medium)" in err), str(argv)[:200] + err[-300:]


@case
def a_one_row_job_prints_the_spare_plan_fact_when_another_plan_has_room():
    data = live("anthropic")
    readings = {"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1, "Surplus")}
    one = sf.spare_facts([job(alias="sonnet", effort="medium")], data, now=NOW, read=reader(readings))
    many = sf.spare_facts([job(rows={"sonnet": "medium", "sol": "medium"})], data, now=NOW,
                          read=reader(readings))
    quiet = sf.spare_facts([job(alias="sonnet", effort="medium")], data, now=NOW,
                           read=reader({"anthropic": reading("anthropic", 0.8)}))
    return (len(one) == 1 and "`j`" in one[0] and "on codex: ~" in one[0] and "unused at reset" in one[0]
            and many == [] and quiet == []), repr((one, many, quiet))


@case
def a_listed_rows_plan_refused_at_launch_moves_the_job_to_the_next_rows_plan():
    fresh()
    saved = pb.read_capacity
    pb.read_capacity = reader({"anthropic": reading("anthropic", 1.2), "codex": reading("codex", 0.9)})
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            argv = sf.plan_job(job(rows={"sonnet": "medium", "sol": "high"}), live("anthropic"), TMP, False)[1]
        fallbacks = list(sf.FALLBACKS.get("j") or [])
    finally:
        pb.read_capacity = saved
    first = (flag(argv, "-m"), flag(argv, "-e"))
    moved = [(entry[0], entry[1] == launcher_of("codex"), entry[2:]) for entry in fallbacks]
    return first == ("sonnet", "medium") and moved == [("codex", True, ("sol", "high"))], repr((first, moved))


FAKE = os.path.join(TMP, "fake_launcher.py")
with open(FAKE, "w", encoding="utf-8") as fh:
    fh.write(
        "import os, sys\n"
        "a = sys.argv\n"
        "rec = a[a.index('-R') + 1]\n"
        "mode = os.environ['FAKE_MODE']\n"
        "open(rec + '.pid', 'w').write('2026-10-27T20:00:00Z pid=4242\\n')\n"
        "open(os.path.join(os.path.dirname(rec), 'calls.log'), 'a').write(\n"
        "    mode + ' ' + a[a.index('-m') + 1] + ' ' + (a[a.index('-e') + 1] if '-e' in a else '-') + '\\n')\n"
        "if mode == 'refuse':\n"
        "    open(rec + '.err', 'w').write('[sidecar] REFUSING: at its dispatch floor\\n')\n"
        "    open(rec + '.exit', 'w').write('10\\n')\n"
        "else:\n"
        "    open(a[a.index('-P') + 1], 'w').write('{}\\n')\n"
        "    open(rec + '.exit', 'w').write('0\\n')\n"
    )


def fake(name, mode):
    path = os.path.join(TMP, name + "_sidecar.sh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import os, runpy, sys\nos.environ['FAKE_MODE'] = %r\nsys.argv[0] = %r\nrunpy.run_path(%r, "
                 "run_name='__main__')\n" % (mode, path, FAKE))
    return path


@case
def the_launch_fallback_switches_alias_and_effort_to_the_next_rows():
    out = tempfile.mkdtemp(dir=TMP)
    refuser, spare = fake("refuse", "refuse"), fake("admit", "admit")
    rec = sf._posix(os.path.join(out, "j.record.json"))
    argv = [sys.executable, refuser, "-m", "sonnet", "-e", "medium", "-l", "j", "-R", rec,
            "-P", sf._posix(os.path.join(out, "j.stream")), "-X"]
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        results = sf.run_jobs([("j", argv, os.path.join(out, "j.out.json"))], 1, out,
                              fallbacks={"j": [("codex", spare, "sol", "high")]})
    calls = open(os.path.join(out, "calls.log"), encoding="utf-8").read().split("\n")
    return results["j"].get("exit") == 0 and calls[:2] == ["refuse sonnet medium", "admit sol high"], repr((calls, err.getvalue()))


@case
def a_waves_move_to_another_route_takes_that_plans_row_and_signature():
    fresh()
    data = live("anthropic")
    picks = {"anthropic": ("sonnet", "medium", ("claude-sonnet-5-5", "medium", "author")),
             "codex": ("sol", "high", ("gpt-6.1-sol", "high", "author"))}
    slot = sf.Slot("anthropic", picks["anthropic"][2], ("anthropic", "codex"), picks)
    spent = reading("anthropic", 0.5)
    spent["quota"]["windows"][0]["usedPercent"] = 100.0
    spent["status"] = "exhausted"
    readings = {"anthropic": spent, "codex": reading("codex", 1.1)}
    waves = sf.Waves({"j": slot}, data, reader(readings), say=lambda _line: None)
    waves.states["anthropic"] = sf._PlanWave()
    verdict = waves.decide("j")
    return (verdict.kind == "launch" and waves.plan_of("j") == "codex"
            and waves.slots["j"].signature == picks["codex"][2] and waves.row_of("j") == ("sol", "high")), repr(
        (verdict, waves.slots["j"]))


@case
def alias_and_rows_together_are_refused_with_a_clear_message():
    got, _ = plan({"anthropic": reading("anthropic", 0.8)},
                  job(alias="sonnet", effort="medium", rows={"sonnet": "medium"}), live("anthropic"))
    dispatch = plan({"anthropic": reading("anthropic", 0.8)},
                    job(model="sonnet", rows={"sonnet": "medium"}), live("anthropic"))[0]
    return all(isinstance(g, str) and g.startswith("REFUSED:") and "`rows`" in g and "not both" in g
               for g in (got, dispatch)), repr((got, dispatch))


@case
def every_listed_row_is_validated_like_alias():
    r = {"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1)}
    unknown = plan(r, job(rows={"sonnet": "medium", "no-such-row": "medium"}), live("anthropic"))[0]
    effort = plan(r, job(rows={"sonnet": "medium", "sol": "bogus"}), live("anthropic"))[0]
    tier = plan(r, job(rows={"sonnet": "medium", "haiku": "medium"}, tier="architect"), live("anthropic"))[0]
    named = plan(r, job(rows={"sonnet": "medium"}, plan="codex"), live("anthropic"))[0]
    empty = plan(r, job(rows={}), live("anthropic"))[0]
    return (all(isinstance(g, str) and g.startswith("REFUSED:") for g in (unknown, effort, tier, named, empty))
            and "no-such-row" in unknown and "bogus" in effort and "haiku" in tier and "tier" in tier
            and "codex" in named), repr(
        (unknown, effort, tier, named, empty))


@case
def a_named_plan_of_a_listed_row_runs_that_plans_row():
    argv, _ = plan({"anthropic": reading("anthropic", 1.4), "codex": reading("codex", 0.9)},
                   job(rows={"sonnet": "medium", "sol": "high"}, plan="codex"), live("anthropic"))
    return flag(argv, "-m") == "sol" and flag(argv, "-e") == "high" and not sf.SLOTS["j"].candidates, str(argv)[:200]


@case
def the_drain_forecast_uses_the_row_that_would_run_on_each_plan():
    fresh()
    seen = []
    saved = pb.job_drain
    pb.job_drain = lambda plan_name, window, model, effort, shape: (
        seen.append((plan_name, model, effort)) or {"pct": 4.0, "basis": "default"})
    try:
        plan({"anthropic": reading("anthropic", 0.8), "codex": reading("codex", 1.1)},
             job(rows={"sonnet": "medium", "sol": "high"}), live("anthropic"))
    finally:
        pb.job_drain = saved
    return ("anthropic", "claude-sonnet-5-5", "medium") in seen and ("codex", "gpt-6.1-sol", "high") in seen, repr(seen)


@case
def fact_lines_attach_the_spare_plan_fact_with_no_cheaper_same_transport_claimant():
    data = {"transports": {"anthropic": {"costModel": "plan-quota"}, "go": {"costModel": "plan-quota"}},
            "models": [
                {"transport": "anthropic", "id": "pin-x", "alias": "pin-a", "roles": ["executor"],
                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}},
                {"transport": "go", "id": "go-exec", "alias": "go-exec", "roles": ["executor"]}]}
    ladder = os.path.join(TMP, "ladder.md")
    with open(ladder, "w", encoding="utf-8") as fh:
        fh.write("# Ladder\n\n## Role guidance\n\n| model | cost | ± |\n|---|---|---|\n| pin-a | 10 | + cheapest |\n")
    prompt = os.path.join(TMP, "p.md")
    with open(prompt, "w", encoding="utf-8") as fh:
        fh.write("x" * 2048)
    one = {"label": "exec", "promptPath": prompt, "model": "pin-x", "effort": "medium", "tier": "executor"}
    now = 1_800_000_000.0
    go = {"schemaVersion": 1, "transport": "go", "costModel": "plan-quota", "sourceKind": "live",
          "observedAt": "2027-01-15T08:00:00Z", "status": "available",
          "quota": {"windows": [{"name": "seven_day", "usedPercent": 20, "resetsAt": now + 2 * 86400,
                                 "windowDurationMins": 10080}],
                    "routingWindow": "seven_day", "bindingWindow": "seven_day"}}
    lines = dispatch_size.fact_lines({"jobs": [one]}, TMP, data=data, ladder=ladder, now=now,
                                     read=lambda name, _d=None: {"go": go}.get(name))
    quiet = dispatch_size.fact_lines({"jobs": [one]}, TMP, data=data, ladder=ladder, now=now,
                                     read=lambda name, _d=None: None)
    return (any("go-exec on go: ~72% unused at reset in 2d" in line and "`exec`" in line for line in lines)
            and quiet == ["pin is the cheapest claimant: exec"]), repr((lines, quiet))


def window(used, elapsed_s, minutes=WEEK_MIN):
    """A window `used`% spent, `elapsed_s` seconds into its length."""
    return {"name": "seven_day", "usedPercent": used, "resetsAt": NOW + minutes * 60 - elapsed_s,
            "windowDurationMins": minutes}


@case
def a_week_six_hours_old_at_one_percent_projects_nothing_and_ranks_by_room():
    week = window(1, 6 * 3600)
    row = pb.plan_room("p", {}, reader({"p": {"status": "available", "quota": {
        "routingWindow": "seven_day", "windows": [week]}}}), NOW)
    got = pb.unused_at_reset("p", week, NOW, samples=[], windows=[week])
    return got is None and "unusedAtReset" not in row and pb.pick_key(row)[1] == 0, repr((got, row))


@case
def a_week_five_days_old_at_thirty_percent_still_projects():
    week = window(30, 5 * 86400)
    got = pb.unused_at_reset("p", week, NOW, samples=[], windows=[week])
    return got is not None and got["basis"] == "average" and round(got["pct"]) == 58, repr(got)


@case
def a_week_three_days_old_projects_from_its_average():
    week = window(20, 3 * 86400)
    got = pb.unused_at_reset("p", week, NOW, samples=[], windows=[week])
    return got is not None and got["basis"] == "average" and round(got["pct"]) == 53, repr(got)


@case
def a_days_samples_forecast_past_a_day():
    week = window(20, 3 * 86400)
    base = {"plan": "p", "provider": "p", "window": "seven_day", "resets_at": week["resetsAt"]}
    day = [dict(base, ts=NOW - 86000, used=15.0), dict(base, ts=NOW - 600, used=20.0)]
    got = pb.unused_at_reset("p", week, NOW, samples=day, windows=[week])
    return got is not None and got["basis"] == "recent", repr(got)


@case
def a_rate_over_less_than_a_day_forecasts_no_further_than_its_span():
    week = window(20, 5 * 86400)
    base = {"plan": "p", "provider": "p", "window": "seven_day", "resets_at": week["resetsAt"]}
    hour = [dict(base, ts=NOW - 7200, used=10.0), dict(base, ts=NOW - 3600, used=10.4)]
    short = pb.unused_at_reset("p", week, NOW, samples=hour, windows=[week])
    near = window(20, 6.5 * 86400)
    base["resets_at"] = near["resetsAt"]
    day = [dict(base, ts=NOW - 86000, used=10.0), dict(base, ts=NOW - 600, used=15.0)]
    long = pb.unused_at_reset("p", near, NOW, samples=day, windows=[near])
    return short["basis"] == "average" and long["basis"] == "recent", repr((short, long))


@case
def an_idle_full_plan_without_a_projection_ranks_below_a_plan_about_to_lose_quota_by_design():
    idle = pb.plan_room("idle", {}, reader({"idle": reading("idle", 1.0, left=1.0)}), NOW)
    losing = {"transport": "losing", "room": 0.7, "unusedAtReset": {"pct": 5.0, "basis": "average", "daysLeft": 1.0}}
    return ("unusedAtReset" not in idle and max([idle, losing], key=pb.pick_key)["transport"] == "losing"
            and pb.pick_key(idle)[2] == 1.0), repr((idle, pb.pick_key(idle), pb.pick_key(losing)))


failed = []
for name, fn in CASES:
    try:
        got = fn()
        ok, detail = got if isinstance(got, tuple) else (bool(got), "")
    except Exception as exc:  # a crash is a failure, never a pass
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:400]))
    if not ok:
        failed.append(name)
print("%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
sys.exit(1 if failed else 0)
