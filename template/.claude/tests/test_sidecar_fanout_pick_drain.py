#!/usr/bin/env python3
"""RED-first proof: forecasts pace a fan-out per plan, observed drain re-sizes each plan's waves, and only
observed spent allowance stops work.

The captured failure (2026-10-25 17:03Z): two `sonnet` `high` `author` jobs went to `anthropic-secondary` at 92%
used of its 5h window (8 points headroom; the flat 4-point charge read 8 - 2x4 = 0 as a fit). The plan hit 100%
at 17:34Z and both jobs stopped with `provider-usage-limit` before writing anything. Every case plants the
capacity readings and the history (samples, finished jobs); no process is spawned and nothing is billed.

Run: python3 .claude/tests/test_sidecar_fanout_pick_drain.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import threading
import time
import json
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import _dispatch_checks as dc  # noqa: E402
import model_registry  # noqa: E402

spec = importlib.util.spec_from_file_location("sf_pick_drain", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)
pb = sf.provider_bands

import copy  # noqa: E402

DATA = model_registry.load()
SHAPE = "survey"
KIND = "scout"  # the job kind whose guard shape is SHAPE; a job's signature carries its kind
ALIAS = next(row["alias"] for row in DATA["models"]
             if row.get("transport") == "anthropic" and row.get("alias") and model_registry.model_available(row["id"], DATA)
             and dc.tier_fit(row["alias"], dc.shape_tiers()[SHAPE], "anthropic", DATA) is None)
MODEL = model_registry.resolve(ALIAS, DATA)["id"]
PA, PB, PC = "anthropic", "anthropic-secondary", "anthropic-console"


def route(*live):
    """The registry with only `live` of the anthropic route's plans available."""
    data = copy.deepcopy(DATA)
    for name, cfg in data["transports"].items():
        if cfg.get("planOf") == "anthropic" and name not in live:
            cfg["status"] = {"state": "unavailable", "reason": "planted"}
    return data
NOW = time.time()
RATE = 1.5   # points per list-price dollar of a planted plan's finished jobs
USD = 4.0    # what every planted finished job cost: the group quantile and so the job's expected cost
DRAIN = RATE * USD
SIG = (MODEL, "high", KIND)  # the signature of one planted job: its model id, effort and kind
TMP = tempfile.mkdtemp(prefix="sfanout_drain_")
PROMPT = os.path.join(TMP, "brief.md")
with open(PROMPT, "w", encoding="utf-8") as fh:
    fh.write("Draft the plan.\n")


def reading(plan, used, hours_left=3.0, seven=None):
    """An available reading whose routing window is a 5h window `used`% spent; `seven` adds a 7-day window that
    much spent."""
    window = {"name": "five_hour", "usedPercent": used, "resetsAt": NOW + hours_left * 3600,
              "windowDurationMins": 300, "pressure": 1.0, "band": "Hot"}
    windows = [window]
    if seven is not None:
        windows.append({"name": "seven_day", "usedPercent": seven, "resetsAt": NOW + 6 * 86400,
                        "windowDurationMins": 10080, "pressure": 1.0, "band": "Hot"})
    return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": "2027-01-15T08:00:00Z", "status": "available",
            "quota": {"windows": windows, "routingWindow": "five_hour", "bindingWindow": "five_hour",
                      "routingBand": "Hot", "dispatchBand": "Hot"}}


def read_as(plan, value):
    """The reading of a planted `value`: a number is the 5h window's used%, a (five, seven) tuple adds the 7-day window."""
    return reading(plan, value[0], seven=value[1]) if isinstance(value, tuple) else reading(plan, value)


def reader(readings):
    def read(plan, _data=None):
        value = readings.get(plan)
        if isinstance(value, Exception):
            raise value
        return value
    return read


def measured(plan, periods=3, jobs_per_period=6, rate=RATE, usds=None):
    """(jobs, samples): `periods` reset periods on `plan`, each holding `jobs_per_period` finished survey jobs per
    effort in `usds` ({effort: dollars}, default high-effort at USD) inside two samples that differ by `rate`
    points per job dollar."""
    usds = usds or {"high": USD}
    jobs, samples = [], []
    cost = sum(usds.values()) * jobs_per_period
    for k in range(periods):
        t0 = NOW - (periods - k) * 30000.0
        n = 0
        for effort, usd in usds.items():
            for _ in range(jobs_per_period):
                start = t0 + 300 + n * 300
                jobs.append({"plan": plan, "start": start, "end": start + 200, "usd": usd,
                             "model": MODEL, "effort": effort, "shape": KIND, "truncated": False})
                n += 1
        for ts, used in ((t0, 0.0), (t0 + 7200, rate * cost)):
            samples.append({"ts": ts, "plan": plan, "provider": plan, "window": "five_hour", "used": used,
                            "resets_at": t0 + 18000})
    return jobs, samples


def plant(*plans, history=True, rates=None, usds=None):
    jobs, samples = [], []
    for plan in plans if history else ():
        j, s = measured(plan, rate=(rates or {}).get(plan, RATE), usds=usds)
        jobs += j
        samples += s
    pb._ledger_jobs = lambda: jobs
    pb._samples = lambda: samples
    pb.reset_drain_cache()


def job(label="j", **extra):
    return dict({"label": label, "alias": ALIAS, "effort": "high", "kind": KIND, "promptFile": PROMPT}, **extra)


def run_pick(plans, readings, load=None, jobspec=None, rest=None):
    """(plan or the JobError text, stderr) of one `pick_plan` over planted readings."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            got = sf.pick_plan("j", "anthropic", plans, route(*plans), read=reader(readings), load=load if load is not None else {},
                               job=jobspec or job(), rest=rest)
        except sf.JobError as exc:
            got = "REFUSED: " + str(exc)
    return got, err.getvalue()


CASES = []


def case(name, fn):
    CASES.append((name, fn))


def incident_goes_to_the_plan_that_holds_it():
    plant(PA, PB)
    got, _ = run_pick([PA, PB], {PA: reading(PA, 96), PB: reading(PB, 3)})
    return got == PB


def incident_alone_is_admitted_with_four_points_left():
    plant(PA)
    got, _ = run_pick([PA], {PA: reading(PA, 96)})
    return got == PA


def the_second_job_of_the_incident_is_admitted_over_forecast():
    plant(PA)
    one, _ = run_pick([PA], {PA: reading(PA, 92)}, load={})
    two, _ = run_pick([PA], {PA: reading(PA, 92)}, load={PA: [SIG]})
    return one == PA and two == PA


def a_plan_admits_beyond_the_summed_forecast():
    plant(PA)
    readings = {PA: reading(PA, 100 - (DRAIN * 3))}
    holds, _ = run_pick([PA], readings, load={PA: [SIG, SIG]})
    over, _ = run_pick([PA], readings, load={PA: [SIG, SIG, SIG]})
    return holds == PA and over == PA


def no_forecast_fit_picks_the_most_headroom():
    plant(PA, PB)
    rest = []
    got, _ = run_pick([PA, PB], {PA: reading(PA, 96), PB: reading(PB, 95)}, rest=rest)
    return got == PB and rest == [PA]


def rest_lists_only_plans_that_hold_the_job():
    plant(PA, PB, PC)
    rest = []
    got, _ = run_pick([PA, PB, PC], {PA: reading(PA, 20), PB: reading(PB, 96), PC: reading(PC, 30)},
                      rest=rest)
    return got in (PA, PC) and rest == [({PA, PC} - {got}).pop()]


def the_pick_line_prints_the_drain_beside_the_headroom():
    plant(PA, PB)
    _, err = run_pick([PA, PB], {PA: reading(PA, 96), PB: reading(PB, 3)})
    return "[fanout] j: plan %s" % PB in err and "headroom 97" in err and "drain %.1f" % DRAIN in err and "measured" in err


def the_pick_line_says_when_the_drain_is_the_default():
    plant(PA, PB, history=False)
    _, err = run_pick([PA, PB], {PA: reading(PA, 20), PB: reading(PB, 30)})
    return "drain %.1f" % pb.JOB_DRAIN_PCT in err and "default" in err


def a_one_plan_route_with_room_launches_on_it():
    plant(PA)
    got, err = run_pick([PA], {PA: reading(PA, 20)})
    return got == PA and "drain" in err


def a_one_plan_route_with_an_unreadable_reading_keeps_the_launchers_gate():
    plant(PA)
    got, _ = run_pick([PA], {PA: None})
    raised, _ = run_pick([PA], {PA: RuntimeError("probe failed")})
    return got == PA and raised == PA


def admission_admits_a_one_plan_job_over_forecast():
    plant(PA)
    text = sf.admission_refusal([job()], route(PA), read=reader({PA: reading(PA, 96)}),
                                now=NOW, load={})
    return text is None


def admission_admits_a_one_plan_job_that_fits_and_one_it_cannot_read():
    plant(PA)
    fits = sf.admission_refusal([job()], route(PA), read=reader({PA: reading(PA, 20)}),
                                now=NOW, load={})
    blind = sf.admission_refusal([job()], route(PA), read=reader({PA: None}),
                                 now=NOW, load={})
    return fits is None and blind is None


def admission_admits_a_file_beyond_its_summed_forecast():
    plant(PA)
    jobs = [job("a"), job("b"), job("c")]
    text = sf.admission_refusal(jobs, route(PA), read=reader({PA: reading(PA, 100 - DRAIN * 2)}),
                                now=NOW, load={})
    return text is None


def a_named_plan_stays_named_over_forecast():
    plant(PA, PB)
    text = sf.admission_refusal([job(plan=PA)], route(PA, PB), read=reader({PA: reading(PA, 96), PB: reading(PB, 3)}),
                                now=NOW, load={})
    got, _ = run_pick([PA], {PA: reading(PA, 96), PB: reading(PB, 3)})
    return text is None and got == PA


def plan_job_counts_placements_without_refusing_a_forecast():
    """PLACED carries both jobs' signatures even when only the first fits the forecast."""
    plant(PA)
    saved = (pb.read_capacity, dict(sf._LIVE))
    pb.read_capacity = lambda transport, data=None, live=True: reading(transport, 100 - DRAIN * 1.5)
    sf._LIVE.clear()
    sf._LIVE["load"] = {}
    sf.PLACED.clear()
    out = sf.Path(tempfile.mkdtemp(dir=TMP))
    data = route(PA)
    try:
        sf.plan_job(job("first", plan=PA), data, out, False)
        placed = copy.deepcopy(sf.PLACED)
        try:
            sf.plan_job(job("second", plan=PA), data, out, False)
            second = "launched"
        except sf.JobError as exc:
            second = str(exc)
        final = copy.deepcopy(sf.PLACED)
    finally:
        pb.read_capacity = saved[0]
        sf._LIVE.clear()
        sf._LIVE.update(saved[1])
        sf.PLACED.clear()
    return placed.get(PA) == [SIG] and final.get(PA) == [SIG, SIG] and second == "launched"


def live_launchers_charge_their_own_estimate():
    plant(PA)
    table = {1: {"cmd": "bash .claude/scripts/anthropic_sidecar.sh -m %s -e high -G %s -l x -f p.md -R r.json" % (ALIAS, KIND),
                 "name": "bash.exe", "pid": 1}}
    load = sf.live_plan_load(table, DATA)
    return load.get(PA) == [SIG]


def pick_and_admission_agree_on_the_same_readings():
    """The same planted readings and load: `pick_plan` and `admission_refusal` admit or refuse together, and the
    plan admission charges a job to is the plan `pick_plan` names."""
    scenes = [([PA, PB], {PA: reading(PA, 96), PB: reading(PB, 3)}, {}),
              ([PA, PB], {PA: reading(PA, 96), PB: reading(PB, 95)}, {}),
              ([PA], {PA: reading(PA, 92)}, {PA: [SIG]}),
              ([PA], {PA: reading(PA, 20)}, {}),
              ([PA, PB], {PA: reading(PA, 100), PB: reading(PB, 100)}, {}),
              ([PA], {PA: reading(PA, 100)}, {}),
              ([PA, PB, PC], {PA: reading(PA, 20), PB: reading(PB, 96), PC: reading(PC, 30)}, {})]
    for plans, readings, load in scenes:
        plant(*plans)
        data = route(*plans)
        picked, _ = run_pick(plans, readings, load=load)
        winners, real = [], sf.place_job

        def spy(*args, **kwargs):
            placement = real(*args, **kwargs)
            winners.extend(row["transport"] for row in placement.fit[:1])
            return placement
        sf.place_job = spy
        try:
            text = sf.admission_refusal([job()], data, read=reader(readings), now=NOW,
                                        load={plan: list(sigs) for plan, sigs in load.items()})
        finally:
            sf.place_job = real
        if picked.startswith("REFUSED") != bool(text):
            return False
        if not text and winners != [picked]:
            return False
    return True


def zero_headroom_on_every_plan_still_refuses():
    plant(PA, PB)
    readings = {PA: reading(PA, 100), PB: reading(PB, 100)}
    picked, _ = run_pick([PA, PB], readings)
    admitted = sf.admission_refusal([job()], route(PA, PB), read=reader(readings), now=NOW, load={})
    named = sf.admission_refusal([job(plan=PA)], route(PA, PB),
                                 read=reader({PA: reading(PA, 100), PB: reading(PB, 0)}), now=NOW, load={})
    pinned, _ = run_pick([PA], readings)
    return picked.startswith("REFUSED") and pinned.startswith("REFUSED") and bool(admitted) and bool(named)


def hook_admits_forecasts_but_refuses_spent_allowance():
    import sidecar_dispatch_context as hook
    from types import SimpleNamespace
    fanout = object()
    with patch.object(hook, "sidecar_fanout", sf), patch.object(hook, "_read_fanout",
            return_value=(SimpleNamespace(refold=False), None, route(PA), [job(plan=PA)], None)), \
            patch.dict(os.environ, {"HARNESS_SIDECAR_PREFLIGHT": "1"}):
        with patch.object(pb, "read_capacity", return_value=reading(PA, 96)):
            admit = hook.fanout_capacity_refusal(fanout, sf.REPO, time.monotonic() + 10)
        with patch.object(pb, "read_capacity", return_value=reading(PA, 100)):
            refuse = hook.fanout_capacity_refusal(fanout, sf.REPO, time.monotonic() + 10)
    return admit is None and bool(refuse)


def slot(plan, effort="high", candidates=()):
    return sf.Slot(plan, (MODEL, effort, KIND), tuple(candidates))


def waves_over(slots, used, plans, **plant_kw):
    """(Waves, said lines) over planted readings: `used` {plan: used%} is read live, so a case moves it between
    calls. Nothing is launched."""
    plant(*plans, **plant_kw)
    said = []

    def read(plan, data=None):
        value = used.get(plan)
        return None if value is None else read_as(plan, value)
    return sf.Waves(slots, route(*plans), read, say=said.append), said


def first_wave(waves, labels):
    """Labels `decide` launches in order before the first wait."""
    got = []
    for label in labels:
        verdict = waves.decide(label)
        if verdict.kind != "launch":
            break
        got.append(label)
    return got


def each_plan_gets_its_own_first_wave_from_its_own_rate():
    a, b = ["a%d" % n for n in range(1, 7)], ["b%d" % n for n in range(1, 7)]
    slots = {**{label: slot(PA) for label in a}, **{label: slot(PB) for label in b}}
    waves, said = waves_over(slots, {PA: 75, PB: 75}, (PA, PB), rates={PB: 2 * RATE})
    waves.pace()
    got_a, got_b = first_wave(waves, a), first_wave(waves, b)
    text = "\n".join(said)
    return (len(got_a) == 4 and len(got_b) == 2
            and "wave %s: 6 jobs, headroom 25, summed forecast %.1f, first wave 4" % (PA, 6 * DRAIN) in text
            and "wave %s: 6 jobs, headroom 25, summed forecast %.1f, first wave 2" % (PB, 6 * DRAIN * 2) in text)


def a_plan_with_nothing_in_flight_launches_even_when_its_headroom_is_under_one_forecast():
    labels = ["a1", "a2", "a3"]
    used = {PA: 97}
    waves, _ = waves_over({label: slot(PA) for label in labels}, used, (PA,))
    waves.pace()
    first = waves.decide("a1").kind
    second = waves.decide("a2").kind
    waves.ended("a1", {"exit": 0})
    waves.round()
    third = waves.decide("a2").kind
    return first == "launch" and second == "wait" and third == "launch"


def a_bigger_forecast_job_is_priced_bigger_under_the_same_observed_rate():
    labels = ["h1", "h2", "h3", "h4", "m1"]
    slots = {label: slot(PA, "medium" if label == "m1" else "high") for label in labels}
    used = {PA: 80}
    waves, _ = waves_over(slots, used, (PA,), usds={"high": 4.0, "medium": 8.0})
    waves.pace()
    launched = first_wave(waves, ["h1", "h2", "h3"])
    used[PA] = 87.5
    waves.ended("h1", {"exit": 0})
    waves.round()
    big, small = waves.price("m1"), waves.price("h4")
    verdicts = (waves.decide("m1").kind, waves.decide("h4").kind)
    return (launched == ["h1", "h2", "h3"] and abs(small - 3.75) < 1e-6 and abs(big - 2 * small) < 1e-6
            and verdicts == ("wait", "launch"))


def a_spent_plan_moves_a_job_to_a_route_sibling_with_room_at_its_decision():
    both = (PA, PB)
    waves, said = waves_over({"x": slot(PA, candidates=both), "y": slot(PA, candidates=both)},
                             {PA: 100, PB: 40}, both)
    waves.pace()
    got = waves.decide("x").kind, waves.plan_of("x")
    return got == ("launch", PB) and waves.plan_of("y") == PA and any(PB in line for line in said)


def a_named_plan_job_never_moves_and_stops_when_its_plan_is_spent():
    waves, _ = waves_over({"x": slot(PA)}, {PA: 100, PB: 10}, (PA, PB))
    waves.pace()
    verdict = waves.decide("x")
    return (verdict.kind == "stop" and waves.plan_of("x") == PA
            and verdict.error.startswith("not run: plan %s has no room (read before launch); rerun after " % PA))


def a_route_with_every_plan_spent_stops_the_job_naming_the_reset():
    both = (PA, PB)
    waves, _ = waves_over({"x": slot(PA, candidates=both)}, {PA: 100, PB: 100}, both)
    waves.pace()
    verdict = waves.decide("x")
    reset = pb.reset_text(NOW + 3 * 3600, time.time())
    return verdict.kind == "stop" and verdict.error.endswith("rerun after " + reset)


def a_sibling_that_reads_blind_takes_a_moved_job_and_the_launcher_decides():
    both = (PA, PB)
    waves, _ = waves_over({"x": slot(PA, candidates=both)}, {PA: 100, PB: None}, both)
    waves.pace()
    return waves.decide("x").kind == "launch" and waves.plan_of("x") == PB


def an_unreadable_plan_launches_every_job_and_leaves_the_launchers_gate():
    waves, said = waves_over({"x": slot(PA), "y": slot(PA)}, {PA: None}, (PA,))
    waves.pace()
    return [waves.decide("x").kind, waves.decide("y").kind] == ["launch", "launch"] and not said


def a_usage_limit_exit_marks_the_plan_spent_for_its_unlaunched_jobs():
    waves, _ = waves_over({"x": slot(PA), "y": slot(PA)}, {PA: 90}, (PA,))
    waves.pace()
    first = waves.decide("x").kind
    waves.ended("x", {"exit": sf.USAGE_LIMIT_EXIT})
    waves.round()
    verdict = waves.decide("y")
    return first == "launch" and verdict.kind == "stop" and "plan %s has no room" % PA in verdict.error


def a_pre_spawn_refusal_is_not_a_job_that_drew_quota():
    waves, said = waves_over({"x": slot(PA), "y": slot(PA), "z": slot(PA)}, {PA: 90}, (PA,))
    waves.pace()
    waves.decide("x")
    waves.ended("x", {"exit": 5, "preSpawnRefusal": True})
    return waves.states[PA].ended == [] and not any("resize" in line for line in said)


LAUNCHERS = {plan: sf._posix(model_registry.transport_meta(plan, DATA)["launcher"]) for plan in (PA, PB)}


def scene(jobs=None, chains=None, start=None, used=None, max_parallel=1, plans=(PA,), exit_of=None,
          on_launch=None, on_decide=None, detached=False, refused=None, **plant_kw):
    """`main` over a planted jobs file, its worker pool real and its launcher stubbed. `start` {plan: used%} is
    what planning and the wave start read; once `run_jobs` is called, `used(plan, ctx)` answers every read (None =
    unreadable). `exit_of` is {label: exit} or a function of (label, plan). `ctx` collects launches (label, plan),
    each launch's argv, who was running at each launch, ended labels, each `decide` verdict, the reads made during
    the run, and named events (`ctx["event"](name)`). `detached` keeps `-X` as production passes it: the launch
    exits 0, its record gets an `.exit` and its `-P` stream is written, unless `refused(label, plan)` (the
    launcher's gate stopped it before the model)."""
    plant(*plans, **plant_kw)
    out = sf.Path(tempfile.mkdtemp(dir=TMP))
    jobsfile = out / "jobs.json"
    jobsfile.write_text(json.dumps({"chains": chains} if chains else jobs), encoding="utf-8")
    events, guard = {}, threading.Lock()
    codes = {}
    ctx = {"launches": [], "argv": {}, "running": set(), "running_at": {}, "ended": [], "decisions": [], "reads": [],
           "event": lambda name: events.setdefault(name, threading.Event()), "out": out}
    ctx["wait"] = lambda name: ctx["event"](name).wait(5)
    running = []
    real_run, real_decide = sf.run_jobs, sf.Waves.decide

    def capacity(plan, data=None, live=True):
        if not running:
            value = start.get(plan)
        else:
            ctx["reads"].append(plan)
            value = used(plan, ctx) if used else start.get(plan)
        return None if value is None else read_as(plan, value)

    def launch(argv, **kwargs):
        label = argv[argv.index("-l") + 1]
        plan = next(name for name, path in LAUNCHERS.items() if path == argv[1])
        with guard:
            ctx["launches"].append((label, plan))
            ctx["argv"].setdefault(label, []).append(list(argv))
            ctx["running_at"][label] = sorted(ctx["running"])
            ctx["running"].add(label)
        if on_launch:
            on_launch(label, ctx)
        with guard:
            ctx["running"].discard(label)
            ctx["ended"].append(label)
        code = exit_of(label, plan) if callable(exit_of) else (exit_of or {}).get(label, 0)
        if not detached:
            return code
        record = argv[argv.index("-R") + 1]
        codes[record] = code
        sf.Path(record + ".exit").write_text(str(code), encoding="utf-8")
        if not (refused and refused(label, plan)):
            sf.Path(argv[argv.index("-P") + 1]).write_text("stream of launch %d of %s on %s\n" % (
                len(ctx["argv"][label]), label, plan), encoding="utf-8")
        return 0

    def decide(self, label):
        verdict = real_decide(self, label)
        ctx["decisions"].append((label, verdict.kind))
        if on_decide:
            on_decide(label, verdict.kind, ctx)
        return verdict

    def run(planned, parallel, out_dir, **kwargs):
        running.append(parallel)
        ctx.update(parallel=parallel, kwargs=kwargs)
        flat = [(label, argv if detached else [a for a in argv if a != "-X"], path) for label, argv, path in planned]
        with patch.object(sf.subprocess, "call", side_effect=launch), \
                patch.object(sf, "supervised_launch", side_effect=lambda label, child, env, argv=None: child()):
            ctx["results"] = real_run(flat, parallel, out_dir, **kwargs)
        return ctx["results"]

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(model_registry, "load", return_value=route(*plans)), \
            patch.object(sf, "plan_load", return_value={}), \
            patch.object(pb, "read_capacity", side_effect=capacity), \
            patch.object(sf, "launcher_fit_refusal", return_value=None), \
            patch.object(sf, "fit_facts", return_value=[]), \
            patch.object(sf, "duplicate_launches", return_value=[]), \
            patch.object(sf, "run_jobs", side_effect=run), \
            patch.object(sf.Waves, "decide", decide), \
            patch.object(sf, "_wait_detached", side_effect=lambda record, poll_interval=None: (codes[record], False)), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = sf.main([str(jobsfile), "--out-dir", str(out), "--max-parallel", str(max_parallel)])
    ctx.update(code=code, text=stdout.getvalue() + stderr.getvalue())
    return ctx


def named(count, plan=PA):
    return [job("j%d" % n, plan=plan) for n in range(1, count + 1)]


def inventory_labels(got):
    return {row["label"] for row in json.loads(next(got["out"].glob("*.inventory.json")).read_text(encoding="utf-8"))["jobs"]}


def an_over_forecast_plan_prints_its_wave_and_keeps_the_global_bound():
    got = scene(named(7), start={PA: 82}, max_parallel=5)
    return (got.get("parallel") == 5 and len(got["launches"]) == 7
            and "wave %s: 7 jobs, headroom 18, summed forecast 42.0, first wave 3" % PA in got["text"])


def a_plan_that_fits_is_still_read_at_each_launch_and_keeps_the_global_bound():
    got = scene(named(3), start={PA: 10}, max_parallel=5)
    return (got.get("parallel") == 5 and len(got["launches"]) == 3 and len(got["reads"]) >= 3
            and "wave %s: 3 jobs, headroom 90, summed forecast %.1f, first wave 3" % (PA, 3 * DRAIN) in got["text"]
            and "resize" not in got["text"])


def an_unreadable_reading_during_the_run_launches_and_keeps_the_launcher_gate():
    got = scene(named(7), start={PA: 82}, used=lambda plan, ctx: None)
    return len(got["launches"]) == 7 and "resize" not in got["text"]


def an_unreadable_other_plan_does_not_hide_the_picked_plans_wave():
    jobs = [job("j%d" % n) for n in range(1, 8)]
    got = scene(jobs, start={PA: 82, PB: None}, used=lambda plan, ctx: 82 if plan == PA else None,
                plans=(PA, PB), max_parallel=2)
    return (len(got["launches"]) == 7 and {plan for _, plan in got["launches"]} == {PA}
            and "wave %s:" % PA in got["text"] and "first wave 3" in got["text"])


def a_first_wave_is_at_least_one_and_the_global_bound_stays():
    low = scene(named(7), start={PA: 96}, max_parallel=5)
    high = scene(named(7), start={PA: 82}, max_parallel=2)
    return ("first wave 1" in low["text"] and len(low["launches"]) == 7
            and high.get("parallel") == 2 and len(high["launches"]) == 7)


def observed_half_the_forecast_launches_the_next_job_beside_the_running_one():
    """first 14 points, forecast 6 a job: j1 and j2 start; j1 ends with the plan 4.5 points lower, so 3 a job. The
    forecast (9.5 - 6 = 3.5 < 6) would hold j3 until j2 ends; the observed 3 starts it beside j2."""
    def on_launch(label, ctx):
        if label == "j1":
            ctx["wait"]("j2 started")
        elif label == "j2":
            ctx["event"]("j2 started").set()
            ctx["wait"]("j3 started")
        elif label == "j3":
            ctx["event"]("j3 started").set()
    got = scene(named(5), start={PA: 86}, used=lambda plan, ctx: 90.5 if ctx["ended"] else 86, max_parallel=2,
                on_launch=on_launch)
    return ("j2" in got["running_at"].get("j3", []) and len(got["launches"]) == 5
            and "first wave 2" in got["text"]
            and "resize %s: observed drain 3.0/job replaces forecast 6.0" % PA in got["text"])


def observed_double_the_forecast_waits_for_the_running_job_instead_of_overdrafting():
    """first 36 points, forecast 6 a job: j1 ends with the plan 18 lower, so 12 a job. The forecast (18 - 6 = 12 >= 6)
    would start j3 beside j2; the observed 12 holds it (18 - 12 < 12) until j2 ends and wakes the waiting worker."""
    def on_launch(label, ctx):
        if label == "j1":
            ctx["wait"]("j2 started")
        elif label == "j2":
            ctx["event"]("j2 started").set()
            ctx["wait"]("j3 waits")

    def on_decide(label, kind, ctx):
        if label == "j3" and kind == "wait":
            ctx["event"]("j3 waits").set()
    got = scene(named(8), start={PA: 64}, used=lambda plan, ctx: 82 if ctx["ended"] else 64, max_parallel=2,
                on_launch=on_launch, on_decide=on_decide)
    kinds = [kind for label, kind in got["decisions"] if label == "j3"]
    return (kinds[:1] == ["wait"] and "launch" in kinds and got["running_at"].get("j3") == []
            and len(got["launches"]) == 8
            and "resize %s: observed drain 12.0/job replaces forecast 6.0" % PA in got["text"])


def spent_plan_moves_the_remaining_jobs_to_the_route_sibling_through_the_worker_pool():
    jobs = [job("j%d" % n) for n in range(1, 7)]
    got = scene(jobs, start={PA: 70, PB: 95},
                used=lambda plan, ctx: (100 if ctx["ended"] else 70) if plan == PA else 95, plans=(PA, PB))
    return ([plan for _, plan in got["launches"]] == [PA] + [PB] * 5
            and all(row["exit"] == 0 for row in got["results"].values()))


def a_moved_job_leaves_the_inventory_pointing_at_its_new_launcher():
    jobs = [job("j%d" % n) for n in range(1, 7)]
    got = scene(jobs, start={PA: 70, PB: 95},
                used=lambda plan, ctx: (100 if ctx["ended"] else 70) if plan == PA else 95, plans=(PA, PB))
    rows = {row["label"]: row.get("launcher") for row in json.loads(
        next(got["out"].glob("*.inventory.json")).read_text(encoding="utf-8"))["jobs"]}
    return rows.get("j1") != rows.get("j2") and rows.get("j2") == rows.get("j6") and rows.get("j2")


def spent_plan_without_a_sibling_ends_the_rest_not_run_and_off_the_inventory():
    got = scene(named(7), start={PA: 82}, used=lambda plan, ctx: 100 if len(ctx["ended"]) >= 3 else 82)
    results = got["results"]
    expected = "not run: plan %s has no room (read before launch); rerun after %s" % (
        PA, pb.reset_text(NOW + 3 * 3600, time.time()))
    return (sorted(label for label, _ in got["launches"]) == ["j1", "j2", "j3"]
            and all(results["j%d" % n]["exit"] is None and results["j%d" % n]["error"] == expected
                    for n in range(4, 8))
            and inventory_labels(got) == {"j1", "j2", "j3"})


def a_named_plan_job_ends_not_run_when_its_plan_is_spent_though_a_sibling_has_room():
    got = scene(named(6), start={PA: 70, PB: 5}, plans=(PA, PB),
                used=lambda plan, ctx: (100 if ctx["ended"] else 70) if plan == PA else 5)
    return ([label for label, _ in got["launches"]] == ["j1"]
            and all("not run: plan %s has no room" % PA in got["results"]["j%d" % n]["error"] for n in range(2, 7)))


def a_usage_limit_exit_stops_the_plans_unlaunched_jobs_with_the_not_run_error():
    got = scene(named(5), start={PA: 80}, exit_of={"j1": sf.USAGE_LIMIT_EXIT})
    results = got["results"]
    return ([label for label, _ in got["launches"]] == ["j1"]
            and all(results["j%d" % n]["exit"] is None
                    and results["j%d" % n]["error"] == "not run: plan %s has no room (read before launch); "
                    "rerun after the plan resets" % PA for n in range(2, 6))
            and inventory_labels(got) == {"j1"})


def a_usage_limit_exit_moves_unlaunched_jobs_to_a_sibling_with_room():
    jobs = [job("j%d" % n) for n in range(1, 7)]
    got = scene(jobs, start={PA: 70, PB: 95}, plans=(PA, PB),
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if plan == PA else 0,
                used=lambda plan, ctx: 70 if plan == PA else 95)
    return got["launches"] == [("j1", PA), ("j1", PB)] + [("j%d" % n, PB) for n in range(2, 7)]


def a_stopped_chain_head_gives_its_successors_the_same_not_run_error():
    lane = [job(label, plan=PA) for label in ("a", "b", "c")]
    other = [job(label, plan=PA) for label in ("d", "e", "f", "g")]
    got = scene(chains=[{"name": "L", "jobs": lane}, {"name": "M", "jobs": other}], start={PA: 70},
                used=lambda plan, ctx: 100 if ctx["ended"] else 70)
    results = got["results"]
    errors = {label: results[label].get("error") for label in "bcdefg"}
    return ([label for label, _ in got["launches"]] == ["a"] and all(errors.values())
            and len(set(errors.values())) == 1 and next(iter(errors.values())).startswith("not run: plan " + PA))


def a_window_is_priced_by_its_own_history_not_the_one_with_fewest_points_left():
    """2026-10-28: anthropic-secondary read five_hour 4% used (96 left) and seven_day 28% (72 left). The job was
    priced on seven_day (2 reset periods of samples: the default) while five_hour, the window that later stopped
    the run, has the history. The binding window is the one that holds the fewest of this job."""
    plant(PB)
    placed = sf.place_job([PB], route(PB), reader({PB: reading(PB, 4, seven=28)}), NOW, {}, SIG)
    ok, drain, _ = placed.held[PB]
    return (ok and drain.get("window") == "five_hour" and drain["basis"] == "measured"
            and abs(drain["pct"] - DRAIN) < 1e-6 and drain.get("headroom") == 96)


def a_plan_does_not_hold_a_job_when_any_window_cannot_though_another_has_fewer_points():
    """five_hour has 17 points left against two jobs at 6 and this one at 6 (18); seven_day has fewer points left
    (15) but holds its jobs at the default 4 (8 + 4)."""
    plant(PB)
    load = {PB: [SIG, SIG]}
    placed = sf.place_job([PB], route(PB), reader({PB: reading(PB, 83, seven=85)}), NOW, load, SIG)
    ok, drain, _ = placed.held[PB]
    return not ok and drain.get("window") == "five_hour" and PB in placed.unfit and "five_hour" in placed.unfit[PB]


def the_pick_line_names_the_window_whose_headroom_and_drain_it_prints():
    plant(PB)
    _, err = run_pick([PB], {PB: reading(PB, 4, seven=28)})
    return "headroom 96" in err and "five_hour" in err and "drain %.1f" % DRAIN in err


def a_plan_whose_summed_forecast_fits_is_still_paced_and_the_observed_draw_holds_the_fifth_job():
    """The 2026-10-28 incident, compressed: eight jobs, the forecast fits the headroom (8 x 6 = 48 of 96), so the
    plan was never paced and no job's launch read it. Four launch; when the first has ended the plan reads 58% used,
    which is 21.6 points a job, and the fifth waits for a running job instead of launching into 42 points."""
    labels = ["j%d" % n for n in range(1, 9)]
    used = {PB: (4, 28)}
    waves, said = waves_over({label: slot(PB) for label in labels}, used, (PB,))
    waves.pace()
    first = first_wave(waves, labels[:4])
    used[PB] = (58, 30)
    waves.ended("j1", {"exit": 0})
    waves.round()
    fifth = waves.decide("j5").kind
    used[PB] = (69, 31)
    waves.ended("j2", {"exit": 0})
    waves.round()
    later = waves.decide("j5").kind
    text = "\n".join(said)
    return (first == labels[:4] and fifth == "wait" and later == "wait"
            and "wave %s: 8 jobs, headroom 96, summed forecast %.1f" % (PB, 8 * DRAIN) in text
            and "resize %s: observed drain 21.6/job replaces forecast %.1f" % (PB, DRAIN) in text)


def each_window_is_observed_against_its_own_first_reading():
    """first read in seven_day (72) and later the tightest window is five_hour (42): the old comparison took 72 - 42
    as one window's draw. Per window, five_hour drew 96 - 42 and seven_day 72 - 70."""
    labels = ["j%d" % n for n in range(1, 5)]
    used = {PB: (4, 28)}
    waves, said = waves_over({label: slot(PB) for label in labels}, used, (PB,))
    waves.pace()
    first_wave(waves, labels)
    used[PB] = (58, 30)
    waves.ended("j1", {"exit": 0})
    text = "\n".join(said)
    return ("observed drain 21.6/job replaces forecast %.1f on five_hour" % DRAIN in text
            and "observed drain 0.8/job replaces forecast %.1f on seven_day" % pb.JOB_DRAIN_PCT in text)


def a_job_its_plan_cannot_hold_beside_the_running_ones_moves_to_a_sibling_that_holds_it():
    both = (PA, PB)
    waves, said = waves_over({"x": slot(PA, candidates=both), "y": slot(PA, candidates=both)}, {PA: 92, PB: 10}, both)
    waves.pace()
    first = waves.decide("x").kind
    verdict = waves.decide("y")
    return (first == "launch" and verdict.kind == "launch" and waves.plan_of("y") == PB
            and any("moved to plan %s" % PB in line for line in said))


def a_named_plan_job_waits_for_the_running_job_though_a_sibling_has_room():
    both = (PA, PB)
    waves, _ = waves_over({"x": slot(PA), "y": slot(PA)}, {PA: 92, PB: 10}, both)
    waves.pace()
    return waves.decide("x").kind == "launch" and waves.decide("y").kind == "wait" and waves.plan_of("y") == PA


def a_sibling_that_cannot_hold_it_either_leaves_the_job_waiting_on_its_plan():
    both = (PA, PB)
    waves, _ = waves_over({"x": slot(PA, candidates=both), "y": slot(PA, candidates=both)}, {PA: 92, PB: 96}, both)
    waves.pace()
    return waves.decide("x").kind == "launch" and waves.decide("y").kind == "wait" and waves.plan_of("y") == PA


def a_plan_read_blind_at_the_start_still_stops_its_jobs_once_a_job_hits_the_usage_limit():
    both = (PA, PB)
    waves, _ = waves_over({"x": slot(PA, candidates=both), "y": slot(PA, candidates=both)}, {PA: None, PB: 10}, both)
    waves.pace()
    waves.decide("x")
    waves.ended("x", {"exit": sf.USAGE_LIMIT_EXIT})
    waves.round()
    verdict = waves.decide("y")
    return verdict.kind == "launch" and waves.plan_of("y") == PB


def continued(got, label="j1"):
    return got["results"][label].get("continuedFrom") or []


def a_job_a_usage_limit_stops_mid_run_continues_as_a_fresh_session_on_a_sibling_plan():
    got = scene([job("j1")], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if plan == PA else 0)
    first, second = got["argv"]["j1"]
    record = second[second.index("-R") + 1]
    return (got["launches"] == [("j1", PA), ("j1", PB)] and got["results"]["j1"]["exit"] == 0
            and "-r" not in second and second[second.index("-f") + 1] == first[first.index("-f") + 1]
            and record.endswith("j1.attempt2.record.json") and record != first[first.index("-R") + 1]
            and [(c["plan"], c["exit"]) for c in continued(got)] == [(PA, sf.USAGE_LIMIT_EXIT)]
            and inventory_labels(got) == {"j1"}
            and next(row for row in json.loads(next(got["out"].glob("*.inventory.json")).read_text(encoding="utf-8"))
                     ["jobs"] if row["label"] == "j1")["recordPath"].endswith("j1.attempt2.record.json"))


def the_continuation_brief_points_the_child_at_its_brief_its_workdir_and_its_report():
    workdir = os.path.join(TMP, "work")
    os.makedirs(workdir, exist_ok=True)
    got = scene([job("j1", workdir=workdir)], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if plan == PA else 0)
    first, second = got["argv"]["j1"]
    contexts = [second[i + 1] for i, tok in enumerate(second) if tok == "-C"]
    fresh = [c for c in contexts if c not in first]
    text = open(fresh[0], encoding="utf-8").read() if len(fresh) == 1 else ""
    return (text.startswith("=== CONTINUATION ===") and sf._posix(PROMPT) in text and sf._posix(workdir) in text
            and "%s/j1.md" % got["out"].name in text and "uncommitted" in text and "same paths" in text
            and "fresh session" in text and "plan %s" % PA in text and "Nothing it did is lost" not in text and second.index(fresh[0]) < second.index("-P"))


def the_stopped_runs_stream_is_kept_beside_the_continuation():
    got = scene([job("j1")], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if plan == PA else 0)
    kept = got["out"] / "j1.attempt1.salvage.jsonl"
    fresh = got["out"] / "j1.stream"
    return (kept.is_file() and "launch 1 of j1 on %s" % PA in kept.read_text(encoding="utf-8")
            and fresh.is_file() and "launch 2 of j1 on %s" % PB in fresh.read_text(encoding="utf-8")
            and continued(got) and continued(got)[0].get("stream", "").endswith("j1.attempt1.salvage.jsonl"))


def the_summary_names_a_continued_job_and_its_stopped_attempt():
    got = scene([job("j1")], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if plan == PA else 0)
    line = next((ln for ln in got["text"].splitlines() if ln.startswith("ok    j1")), "")
    return ("continued after a usage limit on %s" % PA in line and "ran on %s" % PB in line
            and "usage-limit  j1" not in got["text"] and "resume-jobs.json" not in got["text"])


def a_named_plan_job_stopped_mid_run_is_not_moved_and_keeps_the_resume_file():
    got = scene([job("j1", plan=PA)], start={PA: 10, PB: 5}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT)
    return (got["launches"] == [("j1", PA)] and got["results"]["j1"]["exit"] == sf.USAGE_LIMIT_EXIT
            and not continued(got) and (got["out"] / sf.RESUME_FILE).is_file()
            and "usage-limit  j1" in got["text"])


def a_job_every_plan_stops_mid_run_is_continued_once_per_plan_and_then_reported():
    got = scene([job("j1")], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT)
    return (got["launches"] == [("j1", PA), ("j1", PB)] and got["results"]["j1"]["exit"] == sf.USAGE_LIMIT_EXIT
            and (got["out"] / sf.RESUME_FILE).is_file() and "usage-limit  j1" in got["text"])


def a_continuation_the_sibling_refuses_before_spawn_leaves_the_stopped_run_reported():
    got = scene([job("j1")], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT,
                refused=lambda label, plan: plan == PB)
    result = got["results"]["j1"]
    return (got["launches"] == [("j1", PA), ("j1", PB)] and result["exit"] == sf.USAGE_LIMIT_EXIT
            and not result.get("preSpawnRefusal") and (got["out"] / sf.RESUME_FILE).is_file()
            and "usage-limit  j1" in got["text"])


def a_chain_lane_goes_on_after_its_stopped_job_continues():
    lane = [job(label) for label in ("a", "b")]
    got = scene(chains=[{"name": "L", "jobs": lane}], start={PA: 10, PB: 60}, plans=(PA, PB), detached=True,
                exit_of=lambda label, plan: sf.USAGE_LIMIT_EXIT if (label, plan) == ("a", PA) else 0)
    return (got["launches"] == [("a", PA), ("a", PB), ("b", PB)]
            and all(got["results"][label]["exit"] == 0 for label in "ab"))


def plan_job_records_each_jobs_plan_signature_and_route_candidates():
    plant(PA, PB)
    saved = (pb.read_capacity, dict(sf._LIVE))
    pb.read_capacity = lambda transport, data=None, live=True: reading(transport, 20)
    sf._LIVE.clear()
    sf._LIVE["load"] = {}
    sf.PLACED.clear()
    sf.SLOTS.clear()
    out = sf.Path(tempfile.mkdtemp(dir=TMP))
    data = route(PA, PB)
    try:
        sf.plan_job(job("fixed", plan=PB), data, out, False)
        sf.plan_job(job("free"), data, out, False)
        fixed, free = sf.SLOTS["fixed"], sf.SLOTS["free"]
    finally:
        pb.read_capacity = saved[0]
        sf._LIVE.clear()
        sf._LIVE.update(saved[1])
        sf.PLACED.clear()
        sf.SLOTS.clear()
    return (fixed == sf.Slot(PB, SIG, ()) and free.plan in (PA, PB) and free.signature == SIG
            and set(free.candidates) == {PA, PB})


def the_wave_side_channel_and_the_private_epoch_helper_are_gone():
    source = open(os.path.join(CLAUDE, "tools", "sidecar_fanout.py"), encoding="utf-8").read()
    return (callable(getattr(pb, "spent_reset", None)) and "provider_capacity._epoch" not in source
            and '_LIVE["wave"]' not in source and "capacity_wave" not in source)


for fn in (incident_goes_to_the_plan_that_holds_it, incident_alone_is_admitted_with_four_points_left,
           the_second_job_of_the_incident_is_admitted_over_forecast,
           a_plan_admits_beyond_the_summed_forecast, no_forecast_fit_picks_the_most_headroom,
           rest_lists_only_plans_that_hold_the_job, the_pick_line_prints_the_drain_beside_the_headroom,
           the_pick_line_says_when_the_drain_is_the_default, a_one_plan_route_with_room_launches_on_it,
           a_one_plan_route_with_an_unreadable_reading_keeps_the_launchers_gate,
           admission_admits_a_one_plan_job_over_forecast,
           admission_admits_a_one_plan_job_that_fits_and_one_it_cannot_read,
           admission_admits_a_file_beyond_its_summed_forecast, a_named_plan_stays_named_over_forecast,
           plan_job_counts_placements_without_refusing_a_forecast, live_launchers_charge_their_own_estimate,
           pick_and_admission_agree_on_the_same_readings, zero_headroom_on_every_plan_still_refuses,
           hook_admits_forecasts_but_refuses_spent_allowance,
           plan_job_records_each_jobs_plan_signature_and_route_candidates,
           each_plan_gets_its_own_first_wave_from_its_own_rate,
           a_plan_with_nothing_in_flight_launches_even_when_its_headroom_is_under_one_forecast,
           a_bigger_forecast_job_is_priced_bigger_under_the_same_observed_rate,
           a_spent_plan_moves_a_job_to_a_route_sibling_with_room_at_its_decision,
           a_named_plan_job_never_moves_and_stops_when_its_plan_is_spent,
           a_route_with_every_plan_spent_stops_the_job_naming_the_reset,
           a_sibling_that_reads_blind_takes_a_moved_job_and_the_launcher_decides,
           an_unreadable_plan_launches_every_job_and_leaves_the_launchers_gate,
           a_usage_limit_exit_marks_the_plan_spent_for_its_unlaunched_jobs,
           a_pre_spawn_refusal_is_not_a_job_that_drew_quota,
           an_over_forecast_plan_prints_its_wave_and_keeps_the_global_bound,
           a_plan_that_fits_is_still_read_at_each_launch_and_keeps_the_global_bound,
           an_unreadable_reading_during_the_run_launches_and_keeps_the_launcher_gate,
           an_unreadable_other_plan_does_not_hide_the_picked_plans_wave,
           a_first_wave_is_at_least_one_and_the_global_bound_stays,
           observed_half_the_forecast_launches_the_next_job_beside_the_running_one,
           observed_double_the_forecast_waits_for_the_running_job_instead_of_overdrafting,
           spent_plan_moves_the_remaining_jobs_to_the_route_sibling_through_the_worker_pool,
           a_moved_job_leaves_the_inventory_pointing_at_its_new_launcher,
           spent_plan_without_a_sibling_ends_the_rest_not_run_and_off_the_inventory,
           a_named_plan_job_ends_not_run_when_its_plan_is_spent_though_a_sibling_has_room,
           a_usage_limit_exit_stops_the_plans_unlaunched_jobs_with_the_not_run_error,
           a_usage_limit_exit_moves_unlaunched_jobs_to_a_sibling_with_room,
           a_stopped_chain_head_gives_its_successors_the_same_not_run_error,
           a_window_is_priced_by_its_own_history_not_the_one_with_fewest_points_left,
           a_plan_does_not_hold_a_job_when_any_window_cannot_though_another_has_fewer_points,
           the_pick_line_names_the_window_whose_headroom_and_drain_it_prints,
           a_plan_whose_summed_forecast_fits_is_still_paced_and_the_observed_draw_holds_the_fifth_job,
           each_window_is_observed_against_its_own_first_reading,
           a_job_its_plan_cannot_hold_beside_the_running_ones_moves_to_a_sibling_that_holds_it,
           a_named_plan_job_waits_for_the_running_job_though_a_sibling_has_room,
           a_sibling_that_cannot_hold_it_either_leaves_the_job_waiting_on_its_plan,
           a_job_a_usage_limit_stops_mid_run_continues_as_a_fresh_session_on_a_sibling_plan,
           the_continuation_brief_points_the_child_at_its_brief_its_workdir_and_its_report,
           the_stopped_runs_stream_is_kept_beside_the_continuation,
           the_summary_names_a_continued_job_and_its_stopped_attempt,
           a_named_plan_job_stopped_mid_run_is_not_moved_and_keeps_the_resume_file,
           a_job_every_plan_stops_mid_run_is_continued_once_per_plan_and_then_reported,
           a_continuation_the_sibling_refuses_before_spawn_leaves_the_stopped_run_reported,
           a_chain_lane_goes_on_after_its_stopped_job_continues,
           a_plan_read_blind_at_the_start_still_stops_its_jobs_once_a_job_hits_the_usage_limit,
           the_wave_side_channel_and_the_private_epoch_helper_are_gone):
    case(fn.__name__.replace("_", " "), fn)

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
