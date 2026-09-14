#!/usr/bin/env python3
"""Proof for hooks/budget_posture.py's TRANSPORT branch (S5).

A provider session's in-harness dispatches spend the PROVIDER's allowance. The posture line used
to print only the Anthropic 7-day band, unlabelled, so a provider seat read a number that governed
nothing it was about to do. This proves the seat's own currency leads, the Anthropic one is labelled
as the hop's, and -- the load-bearing negative -- that the HOST path is byte-identical, because
three consumers parse it (`sc_gate_band`, `workflow_provider_guard.band_and_pressure`,
`tools/verify_transport_status.py`).

Fixture strategy follows test_budget_posture_cadence.py: the real hook as a subprocess, with
HARNESS_HOOK_STATE_DIR and TMP/TEMP redirected. The provider seat is set with CLAUDE_CODE_TRANSPORT,
which is the same signal a launcher exports.

    python3 .claude/tests/test_budget_posture_transport.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "budget_posture.py")
SEVEN_DAY = 7 * 24 * 3600


def write_capture(tmpdir, session, used_pct=45):
    now = time.time()
    with open(os.path.join(tmpdir, "cc-cachestat-%s.json" % session), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": now,
            # Half the window elapsed, so pressure == used_percentage / 50.
            "seven_day": {"used_percentage": used_pct, "resets_at": now + SEVEN_DAY / 2},
            "five_hour": {"used_percentage": 30, "resets_at": now + 5 * 3600 / 2},
        }}, fh)


def write_band_cache(tmp, entries):
    """Plant provider band readings so the hook reads THEM, not the machine's real cache.

    Without this the proof is non-hermetic in both directions: it can read a stale repo entry, and
    on a cold cache it spawns the transport's real quota probe (45s) and writes the repo cache.
    """
    p = os.path.join(tmp, "provider_bands.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({t: {"at": time.time(),
                       "reading": {"transport": t, "band": b, "pressure": pr}}
                   for t, (b, pr) in entries.items()}, fh)
    return p


def run(transport=None, session="bpt00001", used_pct=45, bands=None):
    """One fresh session per call, so every run is a first-prompt emission."""
    state = tempfile.mkdtemp(prefix="bptstate_")
    tmp = tempfile.mkdtemp(prefix="bpttmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp,
               PYTHONIOENCODING="utf-8",
               PROVIDER_BAND_CACHE=write_band_cache(tmp, bands if bands is not None
                                                    else {"codex": ("Ahead", 1.31)}))
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    write_capture(tmp, session, used_pct)
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": session, "prompt": "go"}),
                       capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


HOST = run()
CODEX = run(transport="codex", session="bpt00002")
UNKNOWN_SEAT = run(transport="notatransport", session="bpt00003")


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
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, PYTHONIOENCODING="utf-8",
               PROVIDER_BAND_CACHE=write_band_cache(tmp, bands if bands is not None else {}))
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"session_id": session, "prompt": "go"}),
                       capture_output=True, text=True, timeout=90, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


NO_RL_CODEX = run_no_ratelimits("codex", "bptnorl1", {"codex": ("Ahead", 1.31)})
NO_RL_COLD = run_no_ratelimits("codex", "bptnorl2", {})
NO_RL_HOST = run_no_ratelimits(None, "bptnorl3", {})


def smr_band_only():
    """currency_bits with a band but no numeric pressure -- the shape that used to raise."""
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("bp", HOOK)
    bp = ilu.module_from_spec(spec)
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    spec.loader.exec_module(bp)
    real = bp.provider_band
    bp.provider_band = lambda t: ("Ahead", None)
    try:
        bits, _ = bp.currency_bits("codex", 0.9, "On pace", "x", True)
        return "; ".join(bits)
    finally:
        bp.provider_band = real


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
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp,
                   PYTHONIOENCODING="utf-8", CLAUDE_CODE_TRANSPORT=transport,
                   PROVIDER_BAND_CACHE=write_band_cache(tmp, bands))
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env.pop("ANTHROPIC_BASE_URL", None)
        r = subprocess.run([sys.executable, HOOK],
                           input=json.dumps({"session_id": session, "prompt": "go"}),
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

CASES = [
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
     lambda: "Ahead" in CODEX and "1.31" in CODEX),

    ("with NO planted reading the hook says unreadable -- so the plant is load-bearing",
     lambda: "unreadable" in CODEX_UNREADABLE and "Ahead" not in CODEX_UNREADABLE),

    # ---- the host contract is the thing that must not move ------------------
    ("host seat still emits a posture line",
     lambda: HOST.startswith("[budget-posture]")),

    ("host line leads with `7d pressure` exactly as before",
     lambda: "] 7d pressure" in HOST.replace("[budget-posture];", "]")),

    ("host line names no seat -- byte-identical to the pre-S5 shape",
     lambda: "seat:" not in HOST),

    ("host line still carries the never-delegated floor",
     lambda: "Never delegated" in HOST),

    # ---- a provider seat gets BOTH currencies, each labelled ----------------
    ("codex seat names the seat first",
     lambda: "seat: codex" in CODEX),

    ("codex seat says its own quota is what a dispatch here spends",
     lambda: "own quota is what a Workflow/Agent dispatch here spends" in CODEX),

    ("codex seat labels the Anthropic band as the CROSS-HOP currency",
     lambda: "CROSS-HOP currency" in CODEX),

    ("codex seat never prints a bare unlabelled `7d pressure` clause",
     lambda: "; 7d pressure" not in CODEX),

    ("codex seat still carries the never-delegated floor and the 5h width clause",
     lambda: "Never delegated" in CODEX and "5h pressure" in CODEX),

    # The anthropic figure must appear ONCE, labelled as the hop's. The seat's own band may be
    # absent (the hook reads cache only -- see below), so counting both is the wrong invariant.
    ("the anthropic band is stated once, never twice under two names",
     lambda: sum(CODEX.count(f"{b} band") for b in ("Surplus", "On pace", "Ahead", "Hot")) ==
             (2 if "codex pressure" in CODEX else 1)),

    ("codex seat tiers off ITS OWN band, not the Anthropic one",
     lambda: "(from the codex band)" in CODEX),

    # An Anthropic-derived tier default on a provider seat reads as advice for a currency nothing
    # here can spend. With no seat band there is no tier default, so the clause is dropped.
    ("an unreadable seat band drops the tier clause rather than mislabelling it",
     lambda: "codex pressure" in CODEX or "tier: " not in CODEX),

    ("the 5h window is Anthropic's too, so it is labelled cross-hop rather than left bare",
     lambda: "5h pressure" not in CODEX or "[cross-hop]" in CODEX),

    # `pressure` is optional in a probe reading; an unguarded format raised into main()'s blanket
    # except and deleted the ENTIRE line -- band, floor and rating-debt clause included.
    ("a band with no numeric pressure still renders the band",
     lambda: "band" in smr_band_only()),

    ("codex seat reports its own band with its pressure",
     lambda: "codex pressure" in CODEX),

    # ---- fail-closed seat resolution is SAID, not silently absorbed ---------
    ("an unresolvable transport says the band may not be this seat's currency",
     lambda: "seat UNIDENTIFIED" in UNKNOWN_SEAT),

    ("...and still emits the Anthropic band rather than nothing",
     lambda: "7d pressure" in UNKNOWN_SEAT),

    # ---- NO rate_limits at all: the silence a provider seat used to get -------------------
    # A provider seat launches with entrypoint 'cli', so it matched no TELEMETRY_LESS_ENTRYPOINT
    # and the hook returned without printing anything -- on the one seat where the Anthropic band
    # is not the governing currency anyway. The session then reported its quota as unknown while
    # `provider_bands.py` could read it the whole time.
    ("a provider seat with NO rate_limits still emits a posture line",
     lambda: NO_RL_CODEX != ""),

    ("...it names the seat's OWN band, which is what governs there",
     lambda: "OWN band is Ahead" in NO_RL_CODEX),

    ("...and labels the unreadable one as Anthropic's, not as the quota",
     lambda: "Anthropic band UNREADABLE" in NO_RL_CODEX),

    # The load-bearing negative: a cold band cache must not silently read as no-quota-data.
    ("a provider seat with no cached band names the command that reads it",
     lambda: "provider_bands.py list" in NO_RL_COLD),

    ("...and does not claim a band it does not have",
     lambda: "OWN band is" not in NO_RL_COLD),

    # HOST behaviour is unchanged: no rate_limits and no provider seat is still silence, because
    # there is nothing to say and three consumers parse this line.
    ("a HOST seat with no rate_limits stays silent, as before",
     lambda: NO_RL_HOST == ""),
]


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
    ok = code in (0, 3) and (code == 3 or out in ("Surplus", "On pace", "Ahead", "Hot"))
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

    # --- F3: off-quota means "not this seat's own currency" -------------------
    # The registry gained the four `anthropic` host rows on 2026-09-04, so a bare
    # `bool(available_models())` became unconditionally true and the no-transport branch died.
    import importlib.util as ilu
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    _sp = ilu.spec_from_file_location("bp2", HOOK)
    bp = ilu.module_from_spec(_sp)
    _sp.loader.exec_module(bp)
    import model_registry as mr

    real_avail, real_seat = mr.available_models, bp.seat_transport
    try:
        only_host = [{"transport": "anthropic", "id": "claude-opus-5", "alias": "opus"}]
        mr.available_models = lambda *a, **k: only_host
        bp.seat_transport = lambda: "anthropic"
        ok = bp.sidecar_available() is False
        failed += not ok
        print("%s a roster holding ONLY this seat's own rows is not 'off-quota available'"
              % ("ok  " if ok else "FAIL"))

        bp.seat_transport = lambda: "codex"
        ok = bp.sidecar_available() is True
        failed += not ok
        print("%s ...and the same roster IS off-quota for a codex seat"
              % ("ok  " if ok else "FAIL"))
    finally:
        mr.available_models, bp.seat_transport = real_avail, real_seat

    total = len(CASES) + 4 + 2
    print("\n%d/%d passed" % (total - failed, total))
    if failed:
        print("\nHOST    : %s" % HOST)
        print("CODEX   : %s" % CODEX)
        print("UNKNOWN : %s" % UNKNOWN_SEAT)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
