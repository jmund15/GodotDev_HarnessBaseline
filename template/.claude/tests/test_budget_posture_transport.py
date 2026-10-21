#!/usr/bin/env python3
"""Proof for hooks/budget_posture.py's TRANSPORT branch (S5).

A provider session's in-harness dispatches spend the PROVIDER's allowance. The posture line used
to print only the Anthropic 7-day band, unlabelled, so a provider seat read a number that governed
nothing it was about to do. This proves the seat's own currency leads, the Anthropic one is labelled
as the hop's. The CLI band fields stay unchanged for gate consumers; the posture line uses
plain quota-left and time-left figures.

Fixture strategy follows test_budget_posture_cadence.py: the real hook as a subprocess, with
HARNESS_HOOK_STATE_DIR and TMP/TEMP/TMPDIR redirected. The provider seat is set with CLAUDE_CODE_TRANSPORT,
which is the same signal a launcher exports. HARNESS_CAPACITY_WARM=0 keeps a cold planted cache
from starting a live probe; the warm itself is proven in-process with its spawner planted.

ROOM, NOT A PICK: the line states each plan's quota left and window left and never prints a `pick:`
clause or band-labelled tier advice; the session weighs room in the ladder's selection line. A plan
going cold re-emits nothing; a cold cache starts one warm.

    python3 .claude/tests/test_budget_posture_transport.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

from _display_words import banned_in

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import quota_bands  # noqa: E402

# The hook's plan-usage sampler writes here, never to the real ~/.claude/usage_samples.jsonl.
os.environ["HARNESS_USAGE_SAMPLES"] = os.path.join(tempfile.mkdtemp(prefix="bpt_samples_"), "samples.jsonl")
# A cold planted cache must not start a live capacity probe from a proof.
os.environ["HARNESS_CAPACITY_WARM"] = "0"
# The fixtures expect the default login as the host plan; a proof run from a secondary-plan session must not change it.
os.environ.pop("CLAUDE_CONFIG_DIR", None)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "budget_posture.py")
SEVEN_DAY = 7 * 24 * 3600


def dispatched_prompt(session):
    path = os.path.join(os.environ['HARNESS_USAGE_SAMPLES'].rsplit(os.sep, 1)[0], 'dispatch.jsonl')
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Agent', 'input': {'prompt': 'review'}}]}}) + '\n')
    return {'session_id': session, 'prompt': 'go', 'transcript_path': path}


def write_capture(tmpdir, session, used_pct=45, five_pct=30):
    now = time.time()
    with open(os.path.join(tmpdir, "cc-cachestat-%s.json" % session), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": now,
            # Half the window elapsed, so pressure == used_percentage / 50.
            "seven_day": {"used_percentage": used_pct, "resets_at": now + SEVEN_DAY / 2},
            "five_hour": {"used_percentage": five_pct, "resets_at": now + 5 * 3600 / 2},
        }}, fh)


def write_band_cache(tmp, entries):
    """Plant normalized provider-capacity readings; the prompt hook remains cache-only."""
    p = os.path.join(tmp, "provider_capacity.json")
    now = time.time()
    blob = {}
    for transport, entry in entries.items():
        band, pressure = entry[:2]
        binding = entry[2] if len(entry) > 2 else None   # (band, used %) of a five_hour window that binds
        status = "exhausted" if band == "Exhausted" else "available"
        row_band, routing_band = band, band
        if status == "exhausted":
            # What a probe emits for a week spent at its start: Exhausted is the reading's status
            # and dispatch band, while the row keeps its risk band (pace 10.0, run-out 0 -> Hot).
            row_band, routing_band = "Hot", "Hot"
            pressure = 10.0 if pressure is None else pressure
        blob[transport] = {
            "schemaVersion": 1, "transport": transport, "costModel": "plan-quota",
            "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "status": status, "error": None, "liveFailure": None, "balance": None,
            "quota": {
                "windows": [{"name": "seven_day", "usedPercent": 100 if status == "exhausted" else 50,
                             "resetsAt": now + SEVEN_DAY, "windowDurationMins": 10080,
                             "pressure": pressure, "band": row_band}],
                "dispatchBand": band, "routingBand": routing_band, "routingWindow": "seven_day",
                "bindingWindow": "seven_day",
                "credits": {"enabled": False, "available": False, "balance": None},
            },
        }
        if binding:
            quota = blob[transport]["quota"]
            quota["windows"].append({"name": "five_hour", "usedPercent": binding[1], "resetsAt": now + 3600,
                                     "windowDurationMins": 300, "pressure": 2.7, "band": binding[0]})
            quota["bindingWindow"], quota["dispatchBand"] = "five_hour", binding[0]
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    return p


def run(transport=None, session="bpt00001", used_pct=45, bands=None, five_pct=30):
    """One fresh session per call, so every run is a first-prompt emission."""
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
               PYTHONIOENCODING="utf-8",
               PROVIDER_CAPACITY_CACHE=write_band_cache(tmp, bands if bands is not None
                                                    else {"codex": ("Ahead", 1.31)}))
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    write_capture(tmp, session, used_pct, five_pct)
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps(dispatched_prompt(session)),
                       capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


HOST = run()
CODEX = run(transport="codex", session="bpt00002")
UNKNOWN_SEAT = run(transport="notatransport", session="bpt00003")
# A2, 2026-09-18: a spent Codex allowance was invisible at boot, and its dispatch then hung.
# `codex` is the SEAT in CODEX above, so the "other transport" clause is only reachable from a
# seat that is NOT codex -- the host path is the one that must name it.
HOST_CODEX_SPENT = run(session="bpt00009", bands={"codex": ("Exhausted", None)})
HOST_COLD_CACHE = run(session="bpt00010", bands={})
# A weekly Surplus once hid a five-hour window at 96%: unattended lanes ran into it and nobody noticed.
HOST_BINDING_HOT = run(session="bpt00011", bands={"codex": ("Surplus", 0.8, ("Hot", 96))})
HOST_WIDTH = run(session="bptwidth1", five_pct=60)
CODEX_WIDTH = run("codex", session="bptwidth2", five_pct=60)
HOST_EQUAL = run(session="bptequal1", used_pct=45, five_pct=45)
CODEX_SPENT_SEAT = run("codex", session="bptspentseat", bands={"codex": ("Exhausted", None)})


def run_no_ratelimits(transport=None, session="bptnorl", bands=None):
    """The provider-seat reality: a capture file exists, but carries no `rate_limits` block.

    The proxy endpoint is Anthropic-compatible and does not send Anthropic quota, so this is the
    NORMAL shape on a codex seat -- not a broken writer, which is why the hook must say something
    useful rather than treat it as missing data.
    """
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    with open(os.path.join(tmp, "cc-cachestat-%s.json" % session), "w", encoding="utf-8") as fh:
        json.dump({"some_other_field": 1}, fh)
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp, PYTHONIOENCODING="utf-8",
               PROVIDER_CAPACITY_CACHE=write_band_cache(tmp, bands if bands is not None else {}))
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps(dispatched_prompt(session)),
                       capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


NO_RL_CODEX = run_no_ratelimits("codex", "bptnorl1", {"codex": ("Ahead", 1.31)})
NO_RL_COLD = run_no_ratelimits("codex", "bptnorl2", {})
NO_RL_HOST = run_no_ratelimits(None, "bptnorl3", {})


def smr_band_only():
    """A normalized window with a band but no optional numeric pressure still renders."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("bp", HOOK)
    bp = ilu.module_from_spec(spec)
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    spec.loader.exec_module(bp)
    now = time.time()
    return bp.provider_entry("codex", label="this session", now=now, capacity={"quota": {
        "routingWindow": "seven_day", "windows": [{"name": "seven_day", "usedPercent": 50,
        "resetsAt": now + SEVEN_DAY, "windowDurationMins": 10080, "band": "Ahead"}]}})


def two_prompts(bands1, bands2, transport="codex", session="bptdedupe"):
    """Two prompts in ONE session, with the provider band moving between them.

    The dedupe key held only the Anthropic band, so a seat could cross from Surplus into Hot and
    this hook stayed silent -- the band that governs its dispatches was not one it watched.
    """
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    write_capture(tmp, session, 45)
    out = []
    for bands in (bands1, bands2):
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
                   PYTHONIOENCODING="utf-8", CLAUDE_CODE_TRANSPORT=transport,
                   PROVIDER_CAPACITY_CACHE=write_band_cache(tmp, bands))
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        r = subprocess.run([sys.executable, HOOK],
                           input=json.dumps(dispatched_prompt(session)),
                           capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
        out.append((r.stdout or "").strip())
    return out


def seat_on_resolver_failure():
    """seat_transport() when the resolver itself raises."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("bp2", HOOK)
    bp = ilu.module_from_spec(spec)
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    spec.loader.exec_module(bp)
    real = bp._session_transport.resolve

    def boom(*a, **k):
        raise RuntimeError("resolver down")

    bp._session_transport.resolve = boom
    try:
        return bp.seat_transport(), bp._session_transport.UNKNOWN, bp._session_transport.HOST_TRANSPORT
    finally:
        bp._session_transport.resolve = real


DEDUPE = two_prompts({"codex": ("Surplus", 0.4)}, {"codex": ("Hot", 1.7)})
DEDUPE_QUIET = two_prompts({"codex": ("Surplus", 0.4)}, {"codex": ("Surplus", 0.4)})


def band_cli(*args):
    r = subprocess.run([sys.executable, HOOK, "--band", *args],
                       capture_output=True, text=True, timeout=90, encoding="utf-8")
    return (r.stdout or "").strip(), r.returncode


# The planted band must be VISIBLE in the output, or the three cases above are asserting against
# whatever the machine happened to have. `Ahead`/1.31 appears in no other fixture here.
CODEX_UNREADABLE = run("codex", session="bpt00009", bands={})

def binding_pressure():
    import importlib.util
    spec = importlib.util.spec_from_file_location("budget_binding_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import provider_capacity
    original = provider_capacity.cached
    provider_capacity.cached = lambda _transport: {
        "status": "available", "quota": {
            "routingBand": "Hot", "bindingWindow": "thirty_day",
            "windows": [{"name": "seven_day", "pressure": 0.1},
                        {"name": "thirty_day", "pressure": 1.9}],
        },
    }
    try:
        return mod.provider_band("go")
    finally:
        provider_capacity.cached = original


def write_room_cache(tmp, shares):
    """A fresh cache of plan readings: {transport: (used %, fraction of the week left)}."""
    now = time.time()
    blob = {}
    for transport, (used, left) in shares.items():
        pressure, band = quota_bands.compute_band(used, now + left * SEVEN_DAY, SEVEN_DAY, now)
        blob[transport] = {
            "schemaVersion": 1, "transport": transport, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "status": "available",
            "error": None, "liveFailure": None, "balance": None,
            "quota": {"windows": [{"name": "seven_day", "usedPercent": used, "resetsAt": now + left * SEVEN_DAY,
                                   "windowDurationMins": 10080, "pressure": pressure, "band": band}],
                      "dispatchBand": band, "routingBand": band, "routingWindow": "seven_day",
                      "bindingWindow": "seven_day", "credits": None}}
    path = os.path.join(tmp, "provider_capacity.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    return path


TODAY = {"anthropic": (22, 6.1 / 7), "go": (30, 6.3 / 7), "codex": (77, 5.1 / 7)}
GO_ROOMIEST = {"anthropic": (80, 0.9), "go": (10, 0.9), "codex": (77, 0.9)}


def prompts(caches, session, transport=None):
    """One posture line per planted room cache, all in ONE session with one Anthropic capture."""
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    write_capture(tmp, session, 45)
    out = []
    for shares in caches:
        folder = tempfile.mkdtemp(prefix="bptroom_")
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
                   PYTHONIOENCODING="utf-8", PROVIDER_CAPACITY_CACHE=write_room_cache(folder, shares))
        for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
            env.pop(key, None)
        if transport:
            env["CLAUDE_CODE_TRANSPORT"] = transport
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(dispatched_prompt(session)),
                           capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
        out.append((r.stdout or "").strip())
    return out


CUR_TODAY = prompts([TODAY], "bptcur01")[0]
CUR_GO = prompts([GO_ROOMIEST], "bptcur02")[0]
CUR_SAME = prompts([TODAY, TODAY], "bptcur04")
# A plan whose cached reading aged past the TTL reads unknown: the change it causes is no news.
CUR_COLD = prompts([TODAY, {"go": TODAY["go"], "codex": TODAY["codex"]}, TODAY], "bptcur06")
CUR_CODEX_SEAT = prompts([TODAY], "bptcur05", transport="codex")[0]
SPARE = prompts([{"anthropic": (22, 6.1 / 7), "codex": (40, 0.2)}], "bptspare1")[0]


def cold_warm():
    """Two prompts, in-process, on a cache holding only anthropic: [(line, [warm calls])] per prompt."""
    import contextlib
    import importlib.util
    import io
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    spec = importlib.util.spec_from_file_location("budget_posture_warm", HOOK)
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    import provider_bands  # noqa: F401  imported before stdout is captured: provider_capacity reconfigures it
    if not hasattr(bp, "start_warm"):
        return [("no start_warm", [])]
    tmp = tempfile.mkdtemp(prefix="bptwarm_")
    write_capture(tmp, "bptwarm1", 45)
    folder = tempfile.mkdtemp(prefix="bptroom_")
    calls = []
    saved = (tempfile.tempdir, dict(os.environ), bp.start_warm, sys.stdin)
    out = []
    try:
        tempfile.tempdir = tmp
        os.environ.update(HARNESS_HOOK_STATE_DIR=tempfile.mkdtemp(prefix="bptstate_"),
                          PROVIDER_CAPACITY_CACHE=write_room_cache(folder, {"anthropic": TODAY["anthropic"],
                                                                   "anthropic-secondary": TODAY["anthropic"]}))
        for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
            os.environ.pop(key, None)
        bp.start_warm = lambda transports: calls.append(sorted(transports)) or True
        for _ in range(2):
            before = len(calls)
            sys.stdin = io.StringIO(json.dumps(dispatched_prompt('bptwarm1')))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                bp.main()
            out.append((buf.getvalue().strip(), calls[before:]))
    finally:
        tempfile.tempdir = saved[0]
        os.environ.clear()
        os.environ.update(saved[1])
        bp.start_warm, sys.stdin = saved[2], saved[3]
    return out


WARM = cold_warm()


def spawns_nothing_when_disabled():
    """HARNESS_CAPACITY_WARM=0 is the only thing that keeps start_warm from spawning."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("budget_posture_nowarm", HOOK)
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    if not hasattr(bp, "start_warm"):
        return False
    import subprocess as sp
    seen = []
    real = sp.Popen
    sp.Popen = lambda *a, **k: seen.append(a)
    try:
        started = bp.start_warm(["go"])
    finally:
        sp.Popen = real
    return started is False and seen == []


CASES = [
    ("a Go reading whose binding window is monthly gives the posture its band", lambda: binding_pressure() == "Hot"),

    # ---- room is a fact on the line, never a pick ----
    ("no reading prints a pick, on the host seat or a provider seat",
     lambda: all("budget-posture" in line and "pick" not in line.lower()
                 for line in (CUR_TODAY, CUR_GO, CUR_CODEX_SEAT))),
    ("the line carries no band-labelled tier advice and no sidecar clause",
     lambda: "tier: " not in CUR_TODAY and "off-quota" not in CUR_TODAY and "(sidecar:" not in CUR_TODAY
     and "7d 55% left, 50% to go" in CUR_TODAY),
    ("no line names a tier's route",
     lambda: not any(t + " →" in CUR_GO for t in ("executor", "fanout", "architect", "orchestrator", "scout"))),
    ("an unchanged posture stays silent on the second prompt",
     lambda: "budget-posture" in CUR_SAME[0] and "budget-posture" not in CUR_SAME[1]),
    ("a plan going cold stays silent, and so does its return",
     lambda: "budget-posture" in CUR_COLD[0] and "budget-posture" not in CUR_COLD[1]
     and "budget-posture" not in CUR_COLD[2]),

    # ---- a plan with more quota than time left: one plan entry, no derived clause ----
    ("a plan with more quota than time left prints its figures by its own band, with no reset and no spare clause",
     lambda: "codex Surplus: 7d 60% left, 20% to go" in SPARE and "resets" not in SPARE.split("codex Surplus")[1]
     and "spare" not in SPARE and "ahead of pace" not in SPARE),
    # The roster is longer than QUOTA_CLAUSE_CAP, so the LINE names the cold plans inside the cap
    # while the WARM covers every cold one: a plan hidden behind "+N more" is still a currency this
    # session may spend, so its cache is still read.
    ("a cold cache names `warming` for the cold plans inside the cap and starts one warm for all of them",
     lambda: len(WARM) == 2
     and all("%s band unknown (cache cold); warming" % n in WARM[0][0]
             for n in ("anthropic-console", "anthropic-console-secondary", "codex"))
     and WARM[0][1] == [["anthropic-console", "anthropic-console-secondary", "codex", "go"]]),
    ("...and a second prompt inside the cache TTL starts no second warm",
     lambda: len(WARM) == 2 and WARM[1][1] == []),
    ("HARNESS_CAPACITY_WARM=0 starts nothing", spawns_nothing_when_disabled),

    # ---- the seat's OWN band is part of the dedupe key ---------------------
    ("a provider band moving Surplus -> Hot re-emits the posture",
     lambda: "Hot" in DEDUPE[1]),

    ("...and the first prompt did print the OLD band, so the pair is a real transition",
     lambda: "Surplus" in DEDUPE[0]),

    # The load-bearing negative: without it, "re-emits" is satisfied by a hook that emits every
    # prompt and dedupes nothing.
    ("an UNCHANGED provider band stays silent on the second prompt",
     lambda: DEDUPE_QUIET[1] == "" or "budget-posture" not in DEDUPE_QUIET[1]),

    # ---- a resolver crash is not the host identity ------------------------
    ("seat_transport returns UNKNOWN when the resolver raises, never the host",
     lambda: (lambda got, unk, host: got == unk and got != host)(*seat_on_resolver_failure())),

    ("the PLANTED codex band is what the hook reports -- not the machine's cache",
     lambda: "codex (this session) Ahead: 7d 50% left, 100% to go" in CODEX),

    ("with NO planted reading the hook says cache-cold unknown -- so the plant is load-bearing",
     lambda: "band unknown" in CODEX_UNREADABLE and "Ahead" not in CODEX_UNREADABLE),

    # ---- the host contract is the thing that must not move ------------------
    ("host seat still emits a posture line",
     lambda: HOST.startswith("[budget-posture]")),

    ("an exhausted provider seat routes each job to another plan serving its tier",
     lambda: "codex (this session) Exhausted:" in CODEX_SPENT_SEAT
     and "route each job to another plan serving its tier" in CODEX_SPENT_SEAT),
    ("the width rule appears only above its unchanged threshold and carries no repeated figures",
     lambda: "5h used faster than its time: one native job at a time; the next waits for it or goes to a row on another plan (orchestration §5b)" in HOST_WIDTH
     and HOST_WIDTH.count("5h 40% left, 50% to go") == 1
     and "5h used faster" not in HOST),
    ("a provider seat labels the triggered width rule as a cross-hop rule",
     lambda: "(orchestration §5b) [cross-hop]" in CODEX_WIDTH),
    ("equal-band windows show only the weekly figures and no reset time",
     lambda: "7d 55% left, 50% to go" in HOST_EQUAL and "5h 55% left" not in HOST_EQUAL
     and "resets" not in HOST_EQUAL),
    ("host line leads with its plan and plain window figures",
     lambda: HOST.startswith("[budget-posture] anthropic (this session) On pace: 7d 55% left, 50% to go")),
    ("all emitted posture lines avoid the banned display dialects",
     lambda: all(not banned_in(line)
                 for line in (HOST, CODEX, UNKNOWN_SEAT, HOST_WIDTH, CODEX_WIDTH, HOST_EQUAL, CODEX_SPENT_SEAT,
                              HOST_BINDING_HOT, HOST_CODEX_SPENT,
                              HOST_COLD_CACHE, NO_RL_CODEX, NO_RL_COLD, SPARE, CUR_TODAY, CUR_GO, WARM[0][0]))),
    ("each plan has exactly one entry: named once inside the cap, or counted once in its overflow row",
     lambda: all(sum(part.startswith(name + " ") for part in HOST.split(" | ")) == 1
                 for name in ("anthropic-console", "anthropic-console-secondary", "anthropic-secondary",
                              "codex"))
     and "+%d more plan-quota transports" % len([n for n in (
         "anthropic-console", "anthropic-console-secondary", "anthropic-secondary", "codex", "go")
         if not any(part.startswith(n + " ") for part in HOST.split(" | "))]) in HOST
     and HOST.count("anthropic (this session)") == 1 and "currency:" not in HOST),
    ("the HOST fixture is shorter than the 604-byte baseline",
     lambda: len(HOST.encode("utf-8")) < 604),

    # ---- every spendable plan-quota transport is named (A2, 2026-09-18) -----
    # The incident: a Codex account at 100% used, invisible on the boot line, dispatched a sidecar
    # that hung 13 minutes. Naming only the seat and Anthropic is what made it invisible.
    ("the host line names codex -- another plan-quota transport it can spend",
     lambda: "codex Ahead: 7d 50% left, 100% to go" in HOST),

    ("a binding five-hour window in a worse band than the weekly routing band is named with its reset",
     lambda: "codex Hot: 5h 4% left, 20% to go, resets " in HOST_BINDING_HOT and "; 7d 50% left, 100% to go" in HOST_BINDING_HOT),

    ("a non-Hot plan with one window carries no reset time",
     lambda: "codex Ahead: 7d 50% left, 100% to go" in HOST and "resets" not in HOST),

    ("a SPENT codex allowance reads as Exhausted on the boot line",
     lambda: "codex Exhausted: 7d 0% left, 100% to go, resets " in HOST_CODEX_SPENT),

    # The load-bearing negative: omitting an unknown transport is the failure mode, because an
    # absent clause reads as "no such currency" -- which is how the spent account stayed hidden.
    ("a cold codex cache NAMES it as unknown rather than omitting the transport",
     lambda: "codex band unknown" in HOST_COLD_CACHE),

    ("the host line carries no separate seat clause",
     lambda: "seat:" not in HOST),

    ("host line still points at the never-delegated floor's owner",
     lambda: "rules: orchestration §5/§5b" in HOST),

    # ---- a provider seat gets BOTH currencies, each labelled ----------------
    ("codex seat names the seat first",
     lambda: CODEX.startswith("[budget-posture] codex (this session) Ahead:")),

    ("codex seat says its own quota is what a dispatch here spends",
     lambda: "codex (this session) Ahead:" in CODEX),

    ("codex seat labels the Anthropic band as the CROSS-HOP currency",
     lambda: "anthropic (hop) On pace:" in CODEX),

    ("codex seat never prints a bare unlabelled `7d pressure` clause",
     lambda: "; 7d pressure" not in CODEX),

    ("codex seat carries the policy owner and no untriggered width clause",
     lambda: "rules: orchestration §5/§5b" in CODEX and "5h used faster" not in CODEX),

    # The anthropic figure must appear ONCE, labelled as the hop's. The seat's own band may be
    # absent (the hook reads cache only -- see below), so counting both is the wrong invariant.
    ("the anthropic band is stated once, never twice under two names",
     lambda: CODEX.count("anthropic (hop) On pace:") == 1),

    ("codex seat prints no band-labelled tier advice",
     lambda: "tier: " not in CODEX and "(from the codex band)" not in CODEX),

    ("the 5h window is Anthropic's too, so it is labelled cross-hop rather than left bare",
     lambda: "anthropic (hop) On pace: 7d 55% left, 50% to go; 5h 70% left, 50% to go" in CODEX),

    # `pressure` is optional in a probe reading; an unguarded format raised into main()'s blanket
    # except and deleted the ENTIRE line -- band, floor and rating-debt clause included.
    ("a band with no numeric pressure still renders the band",
     lambda: "codex (this session) Ahead: 7d 50% left, 100% to go" in smr_band_only()),

    ("codex seat reports its own band with plain figures",
     lambda: "codex (this session) Ahead:" in CODEX),

    ("provider seat names the window for its figures",
     lambda: "7d 50% left, 100% to go" in CODEX),

    ("the cache-only gap notice shows the seat's plain figures",
     lambda: "codex (this session) Ahead: 7d 50% left, 100% to go" in NO_RL_CODEX),

    # ---- fail-closed seat resolution is SAID, not silently absorbed ---------
    ("an unresolvable transport says the band may not be this seat's currency",
     lambda: "seat UNIDENTIFIED" in UNKNOWN_SEAT),

    ("...and still emits the Anthropic band rather than nothing",
     lambda: "anthropic (this session) On pace:" in UNKNOWN_SEAT),

    # ---- NO rate_limits at all: the silence a provider seat used to get -------------------
    # A provider seat launches with entrypoint 'cli', so it matched no TELEMETRY_LESS_ENTRYPOINT
    # and the hook returned without printing anything -- on the one seat where the Anthropic band
    # is not the governing currency anyway. The session then reported its quota as unknown while
    # `provider_bands.py` could read it the whole time.
    ("a provider seat with NO rate_limits still emits a posture line",
     lambda: NO_RL_CODEX != ""),

    ("...it names the seat's OWN band, which is what governs there",
     lambda: "codex (this session) Ahead:" in NO_RL_CODEX),

    ("...and labels the unreadable one as Anthropic's, not as the quota",
     lambda: "Anthropic session telemetry UNREADABLE" in NO_RL_CODEX),

    # The load-bearing negative: a cold band cache must not silently read as no-quota-data.
    ("a provider seat with no cached band names the command that reads it",
     lambda: "provider_bands.py list" in NO_RL_COLD),

    ("...and does not claim a band it does not have",
     lambda: "Ahead:" not in NO_RL_COLD),

    # HOST behaviour is unchanged: no rate_limits and no provider seat is still silence, because
    # there is nothing to say and three consumers parse this line.
    ("a HOST seat with no rate_limits stays silent, as before",
     lambda: NO_RL_HOST == ""),
]


def secondary_session_line(session="bptsec01"):
    """One posture line from a `~/.claude-secondary` session against a planted registry copy in
    which the secondary plan is available, with both Anthropic plans equally roomy."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    data = json.loads(json.dumps(model_registry.load()))
    data["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
    folder = tempfile.mkdtemp(prefix="bptsec_")
    reg = os.path.join(folder, "external_models.json")
    with open(reg, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    write_capture(tmp, session, 45)
    shares = {"anthropic": (22, 6.1 / 7), "anthropic-secondary": (22, 6.1 / 7), "codex": (77, 5.1 / 7)}
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
               PYTHONIOENCODING="utf-8", HARNESS_MODEL_REGISTRY=reg,
               PROVIDER_CAPACITY_CACHE=write_room_cache(folder, shares),
               CLAUDE_CONFIG_DIR=os.path.join(os.path.expanduser("~"), ".claude-secondary"))
    for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
        env.pop(key, None)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(dispatched_prompt(session)),
                       capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


def inprocess_line(bp, session, used_pct, entry="ENTRY", five_pct=30):
    """One in-process first prompt on a host seat, with `provider_entry` replaced by a fixed string, so a
    decision that read the rendered plan text would see none of it."""
    import contextlib
    import io
    tmp = tempfile.mkdtemp(prefix="bptin_")
    write_capture(tmp, session, used_pct, five_pct)
    saved = (tempfile.tempdir, dict(os.environ), bp.provider_entry, sys.stdin)
    try:
        tempfile.tempdir = tmp
        os.environ.update(HARNESS_HOOK_STATE_DIR=tempfile.mkdtemp(prefix="bptstate_"),
                          PROVIDER_CAPACITY_CACHE=write_band_cache(tmp, {}))
        for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
            os.environ.pop(key, None)
        bp.provider_entry = lambda *_a, **_k: entry
        sys.stdin = io.StringIO(json.dumps(dispatched_prompt(session)))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bp.main()
        return buf.getvalue().strip()
    finally:
        tempfile.tempdir = saved[0]
        os.environ.clear()
        os.environ.update(saved[1])
        bp.provider_entry, sys.stdin = saved[2], saved[3]


def structure_cases(bp):
    """[(name, ok)]: structured quota rows, the CLI's used-percent field, and decisions read from state."""
    import inspect
    cases = []
    blocked = "native Workflow/Agent dispatch is blocked until the reset"
    cases.append(("a spent week blocks native dispatch from the exhaustion flag, whatever the plan entry prints",
                  blocked in inprocess_line(bp, "bptexh001", 100)))
    cases.append(("...and a live week does not",
                  blocked not in inprocess_line(bp, "bptexh002", 45)))
    cases.append(("a spent 5h window blocks native dispatch as it did when the plan text was read",
                  blocked in inprocess_line(bp, "bptexh003", 45, five_pct=100)))

    real_band, real_fp = bp.provider_band, bp.binding_fingerprint
    try:
        bp.provider_band = lambda t: "Hot" if t == "codex" else None
        bp.binding_fingerprint = lambda t, b: " (fp)"
        rows = bp.other_quota_bits("anthropic")
        keys = [bp.quota_key(r) for r in rows]
    finally:
        bp.provider_band, bp.binding_fingerprint = real_band, real_fp
    named = {r.get("name"): r for r in rows}
    # WHICH plan holds the cold slot is the roster's business, not this proof's: the shipped
    # plan-quota roster is now longer than QUOTA_CLAUSE_CAP, so only the first rows are named and
    # the rest ride the overflow row. The cold row is therefore read off the emitted rows.
    in_cap = [r["name"] for r in rows if "name" in r]
    cases.append(("a quota row carries name, band and fingerprint as data",
                  isinstance(named.get("codex"), dict) and named["codex"].get("band") == "Hot"
                  and named["codex"].get("fingerprint") == " (fp)"
                  and all(named[n].get("band") is None for n in in_cap if n != "codex"), repr(rows)))
    cold = "%s band unknown (cache cold; python3 .claude/tools/provider_bands.py reads and warms it)" % next(
        n for n in in_cap if n != "codex")
    cases.append(("the emission key keeps its legacy text for a read and a cold plan",
                  "codex Hot band (fp)" in keys and cold in keys, repr(keys)))
    real_cap = bp.QUOTA_CLAUSE_CAP
    try:
        bp.QUOTA_CLAUSE_CAP = 1
        bp.provider_band = lambda t: None
        capped = bp.other_quota_bits("anthropic")
        capped_keys = [bp.quota_key(r) for r in capped]
    finally:
        bp.QUOTA_CLAUSE_CAP, bp.provider_band = real_cap, real_band
    cases.append(("a capped plan list ends in its '+N more' row, keyed as before",
                  len(capped) == 2 and capped_keys[-1].startswith("+") and capped_keys[-1].endswith(
                      " more plan-quota transports"), repr(capped_keys)))
    shown = (bp.quota_clause({"name": "go", "band": None, "fingerprint": ""}, set(), 0),
             bp.quota_clause({"name": "go", "band": None, "fingerprint": ""}, {"go"}, 0),
             bp.quota_clause({"more": 3}, set(), 0))
    cases.append(("the printed clause for a cold plan, a warming plan and the overflow row is rendered in one place",
                  shown == ("go band unknown (cache cold)", "go band unknown (cache cold); warming",
                            "+3 more plan-quota transports"), repr(shown)))

    cache = write_band_cache(tempfile.mkdtemp(prefix="bptpb_"), {"codex": ("Ahead", 1.31)})
    saved = os.environ.get("PROVIDER_CAPACITY_CACHE")
    os.environ["PROVIDER_CAPACITY_CACHE"] = cache
    try:
        got, cold_got = bp.provider_band("codex"), bp.provider_band("nonesuch")
    finally:
        if saved is None:
            os.environ.pop("PROVIDER_CAPACITY_CACHE", None)
        else:
            os.environ["PROVIDER_CAPACITY_CACHE"] = saved
    cases.append(("provider_band returns the band alone: no caller reads a pressure from it",
                  got == "Ahead" and cold_got is None, repr((got, cold_got))))
    params = inspect.signature(bp.provider_entry).parameters
    cases.append(("provider_entry's optionals follow plan_entry's order and are keyword-only",
                  list(params) == ["plan", "capacity", "now", "label"]
                  and all(params[n].kind is inspect.Parameter.KEYWORD_ONLY for n in ("capacity", "now", "label")),
                  repr(list(params))))
    cases.append(("the hook-side window helpers state their units",
                  all("0 to 100" in (inspect.getdoc(fn) or "") or "percent" in (inspect.getdoc(fn) or "")
                      for fn in (bp.provider_entry, bp.capture_capacity))))
    return cases


def ordering_cases(bp):
    """[(name, ok, detail)]: the cap must not spend a slot on a currency nobody can spend.

    Hermetic on purpose. The roster is synthetic and only its four plans are live, so the emitted
    rows are exactly those four whatever this machine's registry holds; the bands come from a
    planted map, so the split does not depend on what the cache happens to hold today.
    """
    import model_registry as mr
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    data = json.loads(json.dumps(mr.load()))
    for cfg in data["transports"].values():
        if isinstance(cfg, dict):
            cfg["status"] = {"state": "unavailable", "reason": "ordering proof: synthetic roster only"}
    data["transports"]["anthropic"]["status"] = {"state": "available"}
    # Registry order (these names sort alphabetically, which is also the order the roster prints in):
    # a spent plan first, then a cold one, then the two with room -- the order that hides `go` today.
    bands = {"zz-a-spent": "Exhausted", "zz-b-cold": None, "zz-c-room": "Surplus", "zz-d-room": "Hot"}
    for name in bands:
        data["transports"][name] = {"planOf": "anthropic", "costModel": "plan-quota",
                                    "status": {"state": "available"}}
    real_load, real_band, real_cap = mr.load, bp.provider_band, bp.QUOTA_CLAUSE_CAP
    try:
        mr.load = lambda *a, **k: data
        bp.provider_band = lambda t: bands.get(t)
        bp.QUOTA_CLAUSE_CAP = 2
        rows = bp.other_quota_bits("anthropic")
        keys = [bp.quota_key(r) for r in rows]
    finally:
        mr.load, bp.provider_band, bp.QUOTA_CLAUSE_CAP = real_load, real_band, real_cap
    named = [r["name"] for r in rows if "name" in r]
    return [
        ("with the cap full, a plan with room is named before a spent or cold one",
         named == ["zz-c-room", "zz-d-room"], repr(rows)),
        ("...and the cap and its '+N more' row are kept, counting every plan left out",
         len(rows) == 3 and rows[-1] == {"more": 2}, repr(rows)),
        ("...with each named row carrying its OWN plan's band, not a neighbour's after the move",
         [r["band"] for r in rows[:2]] == ["Surplus", "Hot"], repr(rows)),
        ("...and the emission key wording is unchanged, so a running session's saved key still matches",
         keys[:2] == ["zz-c-room Surplus band", "zz-d-room Hot band"], repr(keys)),
    ]


def live_cap_case():
    """(name, ok, detail) on the LIVE roster: what the cap hides must never be a plan with room.

    Derived from the emitted rows and the roster, never from a pinned plan name: `go` is the plan
    that falls behind `+N more` today, and pinning it would make the proof about this month's
    registry rather than about the ordering rule.
    """
    import importlib.util
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    spec = importlib.util.spec_from_file_location("bp_order", HOOK)
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    import provider_bands
    rows = bp.other_quota_bits("anthropic")
    named = [r["name"] for r in rows if "name" in r]
    names = [t for t in provider_bands.plan_quota_transports() if t != "anthropic"]
    hidden = [t for t in names if t not in set(named)]
    room = lambda name: bp.provider_band(name) not in (None, "Exhausted")
    # A room plan behind the cap while a spent-or-cold one is named is the defect, whatever the
    # roster's size: the two claims below are the whole rule.
    ok = not (any(room(t) for t in hidden) and any(not room(t) for t in named))
    return ("...and on the live roster no plan with room is hidden while a spent or cold one is named",
            bool(named) and ok, "named=%s hidden=%s" % (named, hidden))


def band_fields(session, used_pct=45, five_pct=30, *flags):
    tmp = tempfile.mkdtemp(prefix="bptfld_")
    write_capture(tmp, session, used_pct, five_pct)
    env = dict(os.environ, TMP=tmp, TEMP=tmp, TMPDIR=tmp)
    r = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--five-hour", *flags, "--session", session],
                       capture_output=True, text=True, timeout=90, encoding="utf-8", env=env)
    return r.returncode, (r.stdout or "").rstrip("\r\n").split("\t")


def used_field_cases():
    """[(name, ok)] for `--used`: the 5h window's used percent as a sixth field, the five-field shape untouched."""
    rc, plain = band_fields("bptusd001")
    rc2, with_used = band_fields("bptusd002", 45, 30, "--used")
    rc3, with_ratio = band_fields("bptusd003", 90, 30, "--used")
    return [("without `--used` the five-hour fields keep their shape", rc == 0 and len(plain) == 4, repr(plain)),
            ("`--used` appends the 5h used percent after an empty run-out slot",
             rc2 == 0 and len(with_used) == 6 and with_used[2] == "0.60" and with_used[4] == ""
             and with_used[5] == "30.0", repr(with_used)),
            ("`--used` keeps the run-out ratio in its own slot when the week is headroom-Hot",
             rc3 == 0 and len(with_ratio) == 6 and with_ratio[4] != "" and with_ratio[5] == "30.0",
             repr(with_ratio))]


# ---- the owner's lean spend line (S3) -----------------------------------------------------------
# The line is the posture's record of the owner's lean condition. The owner's turn is planted in a
# transcript shaped like a captured prompt row (session 7b8b1489, test_spend_mode.py's owner_prompt),
# and the posture is written by the real spend_mode.set_spend / clear_spend, never a hand-built record.
SPEND_SID = "bptspend1"
SPEND_QUOTE = "Build job J3 of the lean-spend plan"
SPEND_FUTURE = "2031-01-01T00:00:00Z"
SPEND_PAST = "2026-01-01T00:00:00Z"
SPEND_LINE = ("spend: lean (owner, session, until %s) — pin rule: orchestration reference "
              "spend_lean.md; rows: provider_bands.py lean <tier>")


def spend_owner_row(uuid, text):
    return {"type": "user", "uuid": uuid, "parentUuid": None, "isSidechain": False, "promptSource": "sdk",
            "entrypoint": "sdk-cli", "userType": "external", "sessionId": SPEND_SID,
            "timestamp": "2026-10-11T00:54:07.277Z", "message": {"role": "user", "content": text}}


class SpendSession:
    """One planted session: a hook-state directory shared by its prompts, and a transcript holding the owner's quote."""

    def __init__(self, sm):
        self.sm = sm
        self.root = tempfile.mkdtemp(prefix="bptspend_")
        self.state = os.path.join(self.root, "state")
        # One TMP for every prompt of this session: the dedupe state lives there, as it does in a real session.
        self.tmp = os.path.join(self.root, "tmp")
        os.makedirs(self.tmp)
        self.transcript = os.path.join(self.root, "owner.jsonl")
        with open(self.transcript, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(spend_owner_row("bptspend-owner-1", SPEND_QUOTE)) + "\n")

    def _in_state(self, fn):
        # The owner's session, not a delegate's: the planted environment drops CLAUDE_CODE_SIDECAR, which
        # set_spend refuses (a delegate cannot set the owner's posture).
        saved = {key: os.environ.get(key) for key in ("HARNESS_HOOK_STATE_DIR", "CLAUDE_CODE_SIDECAR")}
        os.environ["HARNESS_HOOK_STATE_DIR"] = self.state
        os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        try:
            return fn()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def set(self, mode, until=None):
        return self._in_state(lambda: self.sm.set_spend(mode, SPEND_QUOTE, until=until, session_id=SPEND_SID,
                                                        transcript=self.transcript))

    def clear(self):
        return self._in_state(lambda: self.sm.clear_spend(session_id=SPEND_SID))

    def compact(self):
        """What PreCompact does to this session's once-per-compaction keys."""
        sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
        import _hook_state
        return self._in_state(lambda: _hook_state.clear_compaction_keys(SPEND_SID))

    def prompt(self, used_pct=45, capture=True):
        """One prompt through the real hook: (exit code, stdout). A dispatched session, so the posture line's first turn fires."""
        tmp = self.tmp
        if capture:
            write_capture(tmp, SPEND_SID, used_pct)
        else:
            for name in os.listdir(tmp):
                if name.startswith("cc-cachestat-"):
                    os.remove(os.path.join(tmp, name))
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=self.state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
                   PYTHONIOENCODING="utf-8", PROVIDER_CAPACITY_CACHE=write_band_cache(tmp, {"codex": ("Ahead", 1.31)}))
        for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "CLAUDE_CODE_SIDECAR", "ANTHROPIC_BASE_URL"):
            env.pop(key, None)
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(dispatched_prompt(SPEND_SID)),
                           capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
        return r.returncode, (r.stdout or "").strip()


def spend_cases():
    """(name, ok, detail) for the spend line's four states, the mid-session set, the telemetry-less path and clear."""
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import spend_mode as sm

    s = SpendSession(sm)
    s.set("lean", SPEND_FUTURE)
    rc, out = s.prompt()
    case("lean set: the spend line names the owner, the session scope, the expiry and both pointers",
         rc == 0 and SPEND_LINE % SPEND_FUTURE in out, out)
    emitted = [line for line in out.splitlines() if "spend: lean" in line]
    case("lean set: the emitted spend line carries no banned display word",
         rc == 0 and len(emitted) == 1 and not banned_in(emitted[0]), (emitted, banned_in(" ".join(emitted))))

    s = SpendSession(sm)
    s.set("lean", SPEND_PAST)
    rc, out = s.prompt()
    rc2, out2 = s.prompt()
    case("lean past its until: one `expired` line, then no spend line",
         rc == 0 and ("spend: lean expired %s" % SPEND_PAST) in out and "spend: lean (owner" not in out
         and "spend:" not in out2, (out, out2))

    s = SpendSession(sm)
    rc, out = s.prompt(used_pct=45)
    case("normal at Surplus: no spend line and no `available` line",
         rc == 0 and "spend:" not in out and "lean available" not in out, out)

    s = SpendSession(sm)
    rc, out = s.prompt(used_pct=90)
    case("normal with the session's own plan Hot: `lean available — owner only`",
         rc == 0 and "lean available — owner only" in out, out)

    s = SpendSession(sm)
    s.set("lean", SPEND_FUTURE)
    rc, out = s.prompt(used_pct=90)
    case("lean set on a Hot plan: the lean line replaces the `available` line",
         rc == 0 and SPEND_LINE % SPEND_FUTURE in out and "lean available" not in out, out)

    s = SpendSession(sm)
    rc1, out1 = s.prompt()
    rc2, out2 = s.prompt()
    s.set("lean", SPEND_FUTURE)
    rc3, out3 = s.prompt()
    rc4, out4 = s.prompt()
    case("set mid-session, band unchanged: the spend line emits once and the posture line does not re-emit",
         rc1 == 0 and "rules: orchestration" in out1 and out2 == ""
         and rc3 == 0 and SPEND_LINE % SPEND_FUTURE in out3 and "rules: orchestration" not in out3
         and rc4 == 0 and "spend:" not in out4, (out1, out2, out3, out4))

    s = SpendSession(sm)
    s.set("lean", SPEND_FUTURE)
    rc, out = s.prompt(capture=False)
    case("no rate-limit telemetry: the spend line still emits",
         rc == 0 and SPEND_LINE % SPEND_FUTURE in out, out)

    s = SpendSession(sm)
    s.set("lean", SPEND_FUTURE)
    s.prompt()
    s.prompt()
    s.compact()
    rc, out = s.prompt()
    # A compaction drops the lean line from context; unchanged text must not keep it out.
    case("lean set, then a compaction: the next prompt re-emits the lean line",
         rc == 0 and SPEND_LINE % SPEND_FUTURE in out, out)

    s = SpendSession(sm)
    s.set("lean", SPEND_FUTURE)
    s.prompt()
    s.clear()
    rc, out = s.prompt()
    case("clear after lean: the next prompt carries no lean line",
         rc == 0 and "spend: lean" not in out, out)
    # A clear says so once: the lean line already in context would otherwise keep steering pins after lean ended.
    case("clear after lean: one `lean cleared` line, then silence",
         "spend: normal (lean cleared)" in out and "lean cleared" not in s.prompt()[1], out)
    return cases


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    # ---- the --band contract sc_gate_band depends on -----------------------
    # Bare `--band` gates ANTHROPIC-currency spend, so it must keep reading the Anthropic band on
    # every seat. A `--transport`-aware default here would silently regate the sidecar.
    out, code = band_cli()
    ok = code in (0, 3) and (code == 3 or out in ("Surplus", "On pace", "Ahead", "Hot", "Exhausted"))
    failed += not ok
    print("%s bare `--band` returns a band name or exit 3: %r/%d"
          % ("ok  " if ok else "FAIL", out, code))

    out, code = band_cli("--pressure")
    ok = code == 3 or "\t" in out
    failed += not ok
    print("%s `--band --pressure` keeps its tab-separated shape: %r" % ("ok  " if ok else "FAIL", out))

    out, code = band_cli("--transport", "nosuchtransport")
    ok = out == "unregistered" and code == 4
    failed += not ok
    print("%s `--band --transport <unknown>` says `unregistered`/4, distinct from an unreadable `unknown`/3: %r/%d"
          % ("ok  " if ok else "FAIL", out, code))

    out, code = band_cli("--transport")
    ok = out == "unknown" and code == 3
    failed += not ok
    print("%s `--band --transport` with no name fails closed: %r/%d"
          % ("ok  " if ok else "FAIL", out, code))

    # `--session <id>` reads that session's own capture, never the newest-file scan over TEMP. The
    # provider guard's band read timed out on that scan (209k TEMP entries) and lost its injection.
    # The planted own capture is OLDER than a peer's, so the scan would answer the peer's 0.90.
    tmp = tempfile.mkdtemp(prefix="bpt_session_")
    write_capture(tmp, "bptown01", used_pct=90)
    write_capture(tmp, "bptpeer1", used_pct=45)
    old_time = time.time() - 3600
    os.utime(os.path.join(tmp, "cc-cachestat-bptown01.json"), (old_time, old_time))
    env = dict(os.environ, TMP=tmp, TEMP=tmp, TMPDIR=tmp)
    r = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--session", "bptown01"],
                       capture_output=True, text=True, timeout=90, encoding="utf-8", env=env)
    out = (r.stdout or "").strip()
    ok = r.returncode == 0 and out.endswith("\t1.80")
    failed += not ok
    print("%s `--band --pressure --session <id>` reads that session's capture: %r/%d"
          % ("ok  " if ok else "FAIL", out, r.returncode))
    r = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--five-hour", "--session", "bptown01"],
                       capture_output=True, text=True, timeout=90, encoding="utf-8", env=env)
    fields = (r.stdout or "").strip().split("\t")
    ok = (r.returncode == 0 and fields[:3] == ["Hot", "1.80", "0.60"]
          and len(fields) == 5 and fields[3].isdigit())
    if ok:
        try:
            ok = 0.10 <= float(fields[4]) <= 0.12
        except ValueError:
            ok = False
    failed += not ok
    print("%s `--five-hour` preserves four fields and appends weekly run-out: %r/%d"
          % ("ok  " if ok else "FAIL", fields, r.returncode))
    r = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--session", "bptnone1"],
                       capture_output=True, text=True, timeout=90, encoding="utf-8", env=env)
    out = (r.stdout or "").strip()
    ok = r.returncode == 0 and out.endswith("\t0.90")
    failed += not ok
    print("%s ...and a session with no capture falls back to the newest one: %r/%d"
          % ("ok  " if ok else "FAIL", out, r.returncode))

    # An absent 5h window must print two empty fields, which the provider guard reads as unreadable.
    no5 = tempfile.mkdtemp(prefix="bpt_no5h_")
    with open(os.path.join(no5, "cc-cachestat-bptno5h1.json"), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"seven_day": {"used_percentage": 45,
                                                 "resets_at": time.time() + SEVEN_DAY / 2}}}, fh)
    r = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--five-hour", "--session", "bptno5h1"],
                       capture_output=True, text=True, timeout=90, encoding="utf-8",
                       env=dict(os.environ, TMP=no5, TEMP=no5, TMPDIR=no5))
    fields = (r.stdout or "").rstrip("\r\n").split("\t")
    ok = r.returncode == 0 and fields == ["On pace", "0.90", "", ""]
    failed += not ok
    print("%s ...and an absent 5h window leaves both 5h fields empty: %r/%d"
          % ("ok  " if ok else "FAIL", fields, r.returncode))

    import importlib.util as ilu
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    _sp = ilu.spec_from_file_location("bp2", HOOK)
    bp = ilu.module_from_spec(_sp)
    _sp.loader.exec_module(bp)

    real_band = bp.provider_band
    try:
        bp.provider_band = lambda t: None
        cold = [bp.quota_key(r) for r in bp.other_quota_bits("anthropic") if r.get("band") is None
                and "more" not in r]
    finally:
        bp.provider_band = real_band
    ok = bool(cold) and all("python3 .claude/tools/provider_bands.py" in c for c in cold)
    failed += not ok
    print("%s a cold-cache transport clause names the command that reads and warms it: %r"
          % ("ok  " if ok else "FAIL", cold))

    # A plan no route reaches (its own status unavailable) never earns a posture clause: the line
    # would name a currency the orchestrator cannot spend.
    import model_registry as _mr
    _data = json.loads(json.dumps(_mr.load()))
    _data["transports"].setdefault("anthropic-secondary", {}).update(
        {"planOf": "anthropic", "costModel": "plan-quota",
         "status": {"state": "unavailable", "reason": "test: launcher pending"}})
    real_load = _mr.load
    try:
        _mr.load = lambda *a, **k: _data
        bp.provider_band = lambda t: None
        bits = [bp.quota_key(r) for r in bp.other_quota_bits("anthropic")]
    finally:
        _mr.load = real_load
        bp.provider_band = real_band
    ok = not any("anthropic-secondary" in c for c in bits)
    failed += not ok
    print("%s an unavailable plan gets no posture clause: %r" % ("ok  " if ok else "FAIL", bits))

    # A session on the second Anthropic plan: its own band is the capture's (the payload is its own
    # account's), the currency tie goes to it, and the PRIMARY is the other plan named in the line.
    line = secondary_session_line()
    ok = ("pick" not in line.lower() and "anthropic Hot:" in line
          and "anthropic-secondary Hot:" not in line and "anthropic-secondary (this session) On pace: 7d 55% left, 50% to go" in line
          and "UNIDENTIFIED" not in line)
    failed += not ok
    print("%s a secondary session's own band reads its plan and the primary is the other plan: %r"
          % ("ok  " if ok else "FAIL", line))

    extra = used_field_cases() + structure_cases(bp) + ordering_cases(bp) + [live_cap_case()] + spend_cases()
    for name, ok, *_detail in extra:
        failed += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", name))
    total = len(CASES) + 11 + len(extra)
    print("\n%d/%d passed" % (total - failed, total))
    if failed:
        print("\nHOST    : %s" % HOST)
        print("CODEX   : %s" % CODEX)
        print("UNKNOWN : %s" % UNKNOWN_SEAT)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
