#!/usr/bin/env python3
"""Proof for tools/model_registry.py -- the `for-role` resolver and the ladder tier reader.

First coverage for this file. The resolver's job is to make a provider seat SEE the row it should
hop to; the failure it exists to remove is a seat quietly downgrading itself to stay in-transport.
So the load-bearing cases are the ones where in-transport is EMPTY and the answer lives across a
hop, and the one where a weaker in-transport row is offered with its tier delta stated.

Fixtures are literal in-memory dicts and literal ladder markdown -- no temp files, no live-file
coupling in the fixture cases. The live registry and the live ladder are checked at the bottom,
because a resolver that only ever sees its own fixture cannot tell a working anchor from one that
no longer matches the SSOT.

Run: python3 .claude/tests/test_model_registry.py
"""
import contextlib
import copy
import importlib.util
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "model_registry.py")
spec = importlib.util.spec_from_file_location("mr", MOD)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

# ---------------------------------------------------------------- fixtures
LADDER = """## Pick by work shape

| work shape | pin | also | never |
|---|---|---|---|
| deep review | `opus.xhigh` | `luna.max` | sonnet |

## Role guidance

| model | role | effort |
|---|---|---|
| fable | `orchestrator` - ideal design | high |
| opus | `executor` - architect & executor | xhigh |
| sonnet | `fanout` - fan-out, validation | high |
| haiku | `scout` - read-only locate | low |
| luna | scoped planning + spec-tight execution | max |
"""

TIERS = mr.parse_role_tiers(LADDER.splitlines(True))

DATA = {
    "transports": {
        "anthropic": {"costModel": "plan-quota"},
        "codex": {"costModel": "plan-quota", "launcher": ".claude/scripts/codex_proxy_sidecar.sh"},
        "opencode": {"costModel": "marginal-usd", "launcher": ".claude/scripts/opencode_sidecar.sh"},
    },
    "models": [
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus", "roles": ["opus"]},
        {"transport": "anthropic", "id": "claude-sonnet-5", "alias": "sonnet", "roles": ["sonnet"]},
        {"transport": "anthropic", "id": "claude-haiku-4-5", "alias": "haiku", "roles": ["haiku"]},
        {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable", "roles": ["fable"]},
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna", "roles": ["sonnet", "haiku"],
         "effort": {"converged": "low", "open": "max"}},
        {"transport": "codex", "id": "gpt-6-astra", "alias": "astra",
         # `opus` is load-bearing: without a TIER role the role predicate drops this row and the
         # availability gate below is never reached, so the case passes even with the gate removed.
         "roles": ["astra", "opus", "debugging", "review"],
         "status": {"state": "unavailable", "reason": "owner decision"}},
        {"transport": "opencode", "id": "muse-spark-1.3", "alias": "muse", "roles": ["sonnet"]},
    ],
}


def aliases(rows):
    return sorted(m["alias"] for m in rows)


def res(role, seat):
    return mr.for_role(role, seat, data=DATA, tiers=TIERS)


def raises(fn):
    try:
        fn()
    except mr.RegistryError:
        return True
    return False


def _astra_available():
    """DATA with astra's exclusion lifted, for the availability control case."""
    d = copy.deepcopy(DATA)
    for m in d["models"]:
        if m.get("alias") == "astra":
            m.pop("status", None)
    return d


def _fresh_registry_copy():
    """A throwaway copy of the live registry, so a state-flip case never touches the real file."""
    import shutil
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
    shutil.copyfile(str(mr.find_registry()), tmp)
    return tmp


def _only_tiers(keep):
    """DATA where the codex seat serves only `keep` -- so `executor` has no in-transport row and the
    nearest search has a genuine tie to break."""
    d = copy.deepcopy(DATA)
    role_for = {"orchestrator": "fable", "executor": "opus", "fanout": "sonnet", "scout": "haiku"}
    for m in d["models"]:
        if m["transport"] == "codex":
            m.pop("status", None)
            m["roles"] = [role_for[t] for t in keep]
    return d


def _roundtrip_bytes():
    """Bytes `set_transport_state` writes, flipping a copy of the LIVE registry off to the side."""
    import shutil
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
    shutil.copyfile(str(mr.find_registry()), tmp)
    mr.set_transport_state("codex", "unavailable", path=tmp, reason="proof: a state flip")
    mr.set_transport_state("codex", "available", path=tmp)
    return open(tmp, "rb").read()


def _astra_sidecar_scoped():
    """DATA with astra excluded for the HOP only -- the one-word change its own reason text names."""
    d = copy.deepcopy(DATA)
    for m in d["models"]:
        if m.get("alias") == "astra":
            m["status"]["scope"] = "sidecar"
    return d


def _capture_context(argv, limits):
    """Run the context-window CLI branch against a supplied limits block."""
    original = mr.resolve
    mr.resolve = lambda _name: {"limits": limits}
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = mr._cmd_context_window(argv)
    finally:
        mr.resolve = original
    return code, output.getvalue().strip()


CASES = [
    # ---- the ladder reader -------------------------------------------------
    ("ladder tier tokens parse to the four Anthropic rows",
     lambda: TIERS == {"orchestrator": ["fable"], "executor": ["opus"],
                       "fanout": ["sonnet"], "scout": ["haiku"]}),

    ("a work-shape row never contributes a tier",
     lambda: not any("deep review" in v for vs in TIERS.values() for v in vs)),

    ("a row with no leading tier token claims no tier",
     lambda: not any("luna" in v for vs in TIERS.values() for v in vs)),

    ("a ladder missing a tier is an ERROR, not an empty answer",
     lambda: raises(lambda: mr.role_tiers(path=os.path.join(HERE, "does-not-exist.md")))),

    # ---- the whole point: an empty in-transport set names the hop ----------
    ("codex seat, executor: nothing in-transport",
     lambda: res("executor", "codex")["inTransport"] == []),

    ("codex seat, executor: opus is named across the hop",
     lambda: aliases(res("executor", "codex")["crossTransport"]) == ["opus"]),

    ("codex seat, executor: luna offered as nearest, ONE tier below",
     lambda: [(m["alias"], t, d) for m, t, d in res("executor", "codex")["nearest"]]
             == [("luna", "fanout", 1)]),

    ("codex seat, orchestrator: nearest states TWO tiers below",
     lambda: [(m["alias"], t, d) for m, t, d in res("orchestrator", "codex")["nearest"]]
             == [("luna", "fanout", 2)]),

    ("nearest is EMPTY when in-transport already serves the tier",
     lambda: res("fanout", "codex")["nearest"] == []),

    # ---- claiming an alias IS the agnostic route --------------------------
    ("a row claiming `sonnet` serves the fanout tier",
     lambda: aliases(res("fanout", "codex")["inTransport"]) == ["luna"]),

    ("fanout across the hop reaches BOTH the Anthropic row and the free one",
     lambda: aliases(res("fanout", "codex")["crossTransport"]) == ["muse", "sonnet"]),

    ("anthropic seat, executor: opus is in-transport and nothing hops",
     lambda: aliases(res("executor", "anthropic")["inTransport"]) == ["opus"]
             and res("executor", "anthropic")["crossTransport"] == []),

    # ---- availability gates dispatch --------------------------------------
    ("an unavailable row never serves a tier",
     lambda: not any(m["alias"] == "astra"
                     for r in ("orchestrator", "executor", "fanout", "scout")
                     for m in mr.rows_for_tier(r, DATA, TIERS))),

    # The control that makes the gate OBSERVABLE: the same row, available, serves the tier its
    # `opus` role claims. Without this, "never serves" is satisfied by any predicate that drops the
    # row for any reason -- including one that no longer checks availability at all.
    ("...and the SAME row serves it once available -- so the gate, not the role match, excluded it",
     lambda: any(m["alias"] == "astra"
                 for m in mr.rows_for_tier("executor", _astra_available(), TIERS))),

    # ---- a sidecar-scoped exclusion is about the HOP, and the tier filter must agree ------
    # `rows_for_tier` was seat-blind while `workflow_provider_guard` filtered with the seat-aware
    # `dispatchable`. The row then vanished from role resolution on the one seat that may still pin
    # it, while the guard went on allowing the pin -- excluded and broken emitting one evidence.
    ("a sidecar-scoped row still serves its tier FROM ITS OWN transport",
     lambda: any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped(), TIERS, seat="codex"))),

    ("...and is gone for every other seat -- the scope word is what does it",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped(), TIERS, seat="anthropic"))),

    ("a scope:all exclusion is gone from its own transport too",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", DATA, TIERS, seat="codex"))),

    ("for_role threads the seat, so a codex seat sees its own sidecar-scoped row",
     lambda: "astra" in aliases(mr.for_role(
         "executor", "codex", _astra_sidecar_scoped(), TIERS)["inTransport"])),

    ("...and an anthropic seat is not offered it across the hop",
     lambda: "astra" not in aliases(mr.for_role(
         "executor", "anthropic", _astra_sidecar_scoped(), TIERS)["crossTransport"])),

    ("seat=None keeps the seat-blind answer for callers reporting on no session",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped(), TIERS))),

    # ---- an equidistant tie resolves toward the STRONGER tier -------------
    # `fanout` is one rung below `executor` and `orchestrator` one above, so both are distance 1.
    # Sorting on abs(distance) alone let list position decide, which could offer the weaker row --
    # a silent downgrade, the exact failure for_role exists to make visible.
    ("an equidistant nearest tie offers the STRONGER tier, not list order",
     lambda: set(t for _, t, _ in mr.for_role(
         "executor", "codex", _only_tiers(("orchestrator", "fanout")), TIERS)["nearest"])
         == {"orchestrator"}),

    ("...and the weaker side is genuinely available, so the tie was real",
     lambda: any(m["transport"] == "codex" for m in mr.rows_for_tier(
         "fanout", _only_tiers(("orchestrator", "fanout")), TIERS, seat="codex"))),

    # ---- a status flip is a ONE-LINE diff ---------------------------------
    # Windows text mode turns every \n into CRLF, so a one-field flip rewrote all ~700 lines and the
    # flip itself became unreviewable. Bytes, not the source text: only the file settles this.
    ("set_transport_state writes LF, so the diff shows the field that moved",
     lambda: b"\r\n" not in _roundtrip_bytes()),

    ("...and the flip actually landed, so the LF check is not passing on an unwritten file",
     lambda: b'"codex"' in _roundtrip_bytes()),

    # The validator demands a reason on any unavailable transport, and the function had no parameter
    # to supply one -- so the ONE state it exists to set was unreachable through its own API.
    ("suspending a transport without a reason is refused, not written",
     lambda: raises(lambda: mr.set_transport_state(
         "codex", "unavailable", path=_fresh_registry_copy()))),

    ("...and the same call WITH a reason succeeds -- the parameter, not the state, was missing",
     lambda: mr.set_transport_state("codex", "unavailable", path=_fresh_registry_copy(),
                                    reason="proof")["state"] == "unavailable"),

    # ---- one reader for the ladder table ----------------------------------
    ("parse_ladder_rows keys by column NAME and skips the separator row",
     lambda: mr.parse_ladder_rows(
         ["## Role guidance", "| model | role | effort |", "|---|---|---|",
          "| opus | `executor` x | high |"], ("model", "role", "effort"))
         == [{"model": "opus", "role": "`executor` x", "effort": "high"}]),

    ("...and returns nothing when a required column is absent, rather than guessing",
     lambda: mr.parse_ladder_rows(
         ["## Role guidance", "| model | role |", "|---|---|", "| opus | `executor` |"],
         ("model", "role", "effort")) == []),

    # ---- validation is not a fifth tier -----------------------------------
    ("`validation` resolves to the fanout rows",
     lambda: res("validation", "codex")["tier"] == "fanout"),

    ("`validation` says it is an EFFORT move, not a different row",
     lambda: "effort" in (res("validation", "codex")["note"] or "").lower()),

    ("`validation` is absent from the closed tier set",
     lambda: "validation" not in mr.TIER_ORDER),

    # ---- context-window modes ----------------------------------------------
    ("the default context query uses contextTokens and the provider percentage",
     lambda: _capture_context(["luna"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95}) == (0, "258400")),

    ("the max context query uses maxContextTokens and the same percentage",
     lambda: _capture_context(["luna", "--max"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95}) == (0, "828400")),

    ("max context prints nothing when the registry declares no max",
     lambda: _capture_context(["luna", "--max"], {
         "contextTokens": 272000, "effectiveContextPercent": 95}) == (0, "")),

    ("context-window rejects an unknown mode instead of treating it as max",
     lambda: _capture_context(["luna", "--huge"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95})[0] == 2),

    # ---- refusals ---------------------------------------------------------
    ("an unknown role raises and names the legal set",
     lambda: raises(lambda: mr.canonical_tier("reviewer"))),

    # ---- the sidecar line is the hop, spelled out -------------------------
    ("a cross-transport row prints its launcher and alias",
     lambda: ".claude/scripts/opencode_sidecar.sh" in mr._sidecar_line(DATA["models"][6], DATA)
             and "-m muse" in mr._sidecar_line(DATA["models"][6], DATA)),

    ("a transport with no launcher says so rather than printing a broken command",
     lambda: "cannot hop" in mr._sidecar_line(DATA["models"][0], DATA)),
]


SCHEDULE = {
    "offPeakMultiplier": 0.5,
    "peakWindowsUTC": [
        {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "01:00", "end": "04:00"},
        {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "06:00", "end": "10:00"},
    ],
    "source": "https://api-docs.deepseek.com/quick_start/pricing",
    "asOf": "2026-09-15",
}


def _schedule_cases(live):
    """Pricing schedule, peak policy, measuredVersion and effort-values. Returns (failed, total).

    2026-09-15 is a Tuesday; 2026-09-19 a Saturday; 2026-09-21 the following Monday.
    """
    # Local so this block stands alone against a file whose module imports differ (separability).
    import contextlib
    import io
    import json
    import tempfile

    def planted():
        d = copy.deepcopy(live)
        d["transports"]["deepseek"]["pricingSchedule"] = copy.deepcopy(SCHEDULE)
        return d

    def _overnight():
        d = planted()
        d["transports"]["deepseek"]["pricingSchedule"]["peakWindowsUTC"] = [
            {"days": ["Fri"], "start": "22:00", "end": "02:00"}]
        return d

    def deepseek_row(d, alias="flash"):
        return next(m for m in d["models"] if m["alias"] == alias)

    def rejects(mutate, needle):
        d = planted()
        mutate(d)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def accepts(mutate):
        d = planted()
        mutate(d)
        mr._validate(d, "fixture")
        return True

    def window(at, transport="deepseek"):
        return mr.price_window(transport, at=at, data=planted())

    def cli(argv, data):
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        old = os.environ.get(mr.ENV_OVERRIDE)
        os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mr.main(argv)
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return code, out.getvalue().strip()

    def with_env_at(value, fn):
        old = os.environ.get("SC_PRICE_AT")
        os.environ["SC_PRICE_AT"] = value
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("SC_PRICE_AT", None)
            else:
                os.environ["SC_PRICE_AT"] = old

    def set_measured(d, version):
        row = deepseek_row(d)
        row["version"] = "DeepSeek-V4.1-Flash"
        row["effort"] = {"evidence": "measured", "measuredVersion": version}

    def run_price(at):
        return mr.price_run("flash", 1_000_000, 0, 1_000_000, data=planted(), at=at)

    cases = [
        ("Tue 02:00Z is peak at full price, ending 04:00Z",
         lambda: window("2026-09-15T02:00:00Z") == {
             "window": "peak", "multiplier": 1.0, "changesAt": "2026-09-15T04:00:00Z"}),
        ("Tue 05:00Z is off-peak at the multiplier, ending 06:00Z",
         lambda: window("2026-09-15T05:00:00Z") == {
             "window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-15T06:00:00Z"}),
        ("a window END is exclusive: Tue 04:00Z is off-peak",
         lambda: window("2026-09-15T04:00:00Z")["window"] == "off-peak"),
        ("a window START is inclusive: Tue 06:00Z is peak",
         lambda: window("2026-09-15T06:00:00Z")["window"] == "peak"),
        ("Saturday inside the hours is off-peak, changing at Monday 01:00Z",
         lambda: window("2026-09-19T02:00:00Z") == {
             "window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-21T01:00:00Z"}),
        ("a transport with no schedule is flat, full price, no change",
         lambda: window("2026-09-15T02:00:00Z", transport="codex") == {
             "window": "flat", "multiplier": 1.0, "changesAt": None}),
        ("price_run halves every rate off-peak",
         lambda: abs(run_price("2026-09-15T05:00:00Z") * 2 - run_price("2026-09-15T02:00:00Z")) < 1e-9
                 and run_price("2026-09-15T02:00:00Z") > 0),
        ("price_run with no `at` reads SC_PRICE_AT",
         lambda: with_env_at("2026-09-15T05:00:00Z", lambda: abs(
             mr.price_run("flash", 1_000_000, 0, 0, data=planted())
             - run_price_fresh_off_peak(planted())) < 1e-9)),
        ("the shipped schedule shape validates",
         lambda: accepts(lambda d: None)),
        ("offPeakMultiplier 0 is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             offPeakMultiplier=0), "offPeakMultiplier")),
        ("offPeakMultiplier above 1 is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             offPeakMultiplier=1.5), "offPeakMultiplier")),
        ("an unknown day name is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(days=["Funday"]), "days")),
        ("a zero-length window (start equal to end) is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(start="04:00", end="04:00"), "start")),
        # A window crossing midnight belongs to the day it STARTS: Fri 22:00-02:00 covers Fri 22:00-24:00
        # and Sat 00:00-02:00. 2026-09-18 is a Friday.
        ("a cross-midnight window validates",
         lambda: accepts(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             peakWindowsUTC=[{"days": ["Fri"], "start": "22:00", "end": "02:00"}]))),
        ("cross-midnight: Fri 23:00Z is peak, ending Sat 02:00Z",
         lambda: mr.price_window("deepseek", at="2026-09-18T23:00:00Z", data=_overnight())
                 == {"window": "peak", "multiplier": 1.0, "changesAt": "2026-09-19T02:00:00Z"}),
        ("cross-midnight: Sat 01:00Z (the tail on the next day) is peak",
         lambda: mr.price_window("deepseek", at="2026-09-19T01:00:00Z", data=_overnight())["window"] == "peak"),
        ("cross-midnight: Sat 02:00Z is off-peak, and Fri 21:59Z is off-peak until 22:00Z",
         lambda: mr.price_window("deepseek", at="2026-09-19T02:00:00Z", data=_overnight())["window"] == "off-peak"
                 and mr.price_window("deepseek", at="2026-09-18T21:59:00Z", data=_overnight())
                 == {"window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-18T22:00:00Z"}),
        ("cross-midnight: Thu 01:00Z is off-peak (the tail belongs to the listed day's NEXT day only)",
         lambda: mr.price_window("deepseek", at="2026-09-17T01:00:00Z", data=_overnight())["window"] == "off-peak"),
        ("a malformed HH:MM is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(start="25:00"), "HH:MM")),
        ("a schedule on a plan-quota transport is rejected",
         lambda: rejects(lambda d: d["transports"]["codex"].update(
             pricingSchedule=copy.deepcopy(SCHEDULE)), "pricingSchedule")),
        ("peakPolicy refuse on a transport with no schedule is rejected",
         lambda: rejects(lambda d: next(m for m in d["models"] if m["alias"] == "muse")[
             "gate"].update(peakPolicy="refuse"), "peakPolicy")),
        ("peakPolicy outside the closed set is rejected",
         lambda: rejects(lambda d: deepseek_row(d)["gate"].update(peakPolicy="sometimes"),
                         "peakPolicy")),
        ("peakPolicy refuse on a scheduled transport validates",
         lambda: accepts(lambda d: deepseek_row(d)["gate"].update(peakPolicy="refuse"))),
        ("measured evidence without measuredVersion on a versioned row is rejected",
         lambda: rejects(lambda d: set_measured(d, None), "measuredVersion")),
        ("measured evidence from an unregistered version is rejected",
         lambda: rejects(lambda d: set_measured(d, "DeepSeek-V3-Flash"), "measuredVersion")),
        # Owner ruling 2026-09-22: a new version takes over its family's claims until measured, so
        # evidence measured on a registered SAME-FAMILY row stands (inherited); another family's does not.
        ("measured evidence inherited from a same-family predecessor validates",
         lambda: accepts(lambda d: set_measured(d, "DeepSeek-V4-Flash-0731"))),
        ("measured evidence from another family's version is rejected",
         lambda: rejects(lambda d: set_measured(d, "gpt-5.6-luna"), "measuredVersion")),
        ("measured evidence matching the version validates",
         lambda: accepts(lambda d: set_measured(d, "DeepSeek-V4.1-Flash"))),
        ("`price-window` CLI prints the window and effective rates",
         lambda: (lambda r: r[0] == 0 and json.loads(r[1])["window"] == "off-peak"
                  and abs(json.loads(r[1])["rates"]["outputPer1M"]
                          - deepseek_row(planted())["price"]["outputPer1M"] * 0.5) < 1e-9)(
             with_env_at("2026-09-15T05:00:00Z", lambda: cli(["price-window", "flash"], planted())))),
        ("`effort-values` prints a transport's declared vocabulary",
         lambda: (lambda d: (d["transports"]["deepseek"].update(effortValues=["low", "high", "max"]),
                             cli(["effort-values", "flash"], d))[1])(planted())
                 == (0, '["low", "high", "max"]')),
        ("`effort-values` prints [] for a row and transport that declare none",
         lambda: cli(["effort-values", "opus"], planted()) == (0, "[]")),
    ]

    failed = 0
    for name, fn in cases:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def _identity_cases(live):
    """Model identity (S1): versioned aliases, dated alias history, record resolution, lifecycle.

    Live cases pin the owner's O-alias ruling (bare alias = current version; a superseded version
    gets a versioned alias). Planted cases carry the arm-record shapes the census found, so a
    resolver that only ever sees the live roster cannot pass them by accident.
    """
    import contextlib
    import io
    import json
    import re
    import tempfile

    RUNG_FLAG = re.compile(r" -e (none|low|medium|high|xhigh|max|ultra) ")

    def rid(name):
        return mr.resolve(name, live)["id"]

    def rejects(mutate, needle):
        d = copy.deepcopy(live)
        mutate(d)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def raises_registry(fn):
        try:
            fn()
        except mr.RegistryError:
            return True
        return False

    def row(d, alias):
        return next(m for m in d["models"] if m["alias"] == alias)

    def cli(argv, data=None):
        old = os.environ.get(mr.ENV_OVERRIDE)
        if data is not None:
            tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh)
            os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mr.main(argv)
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return code, out.getvalue()

    def routes_to(model_id):
        """Every (tier, seat) whose for-role report names model_id in any section."""
        tiers = mr.role_tiers()
        hits = []
        for tier in mr.TIER_ORDER:
            for seat in live["transports"]:
                res = mr.for_role(tier, seat, live, tiers)
                named = [m["id"] for m in res["inTransport"] + res["crossTransport"]]
                named += [m["id"] for m, _t, _d in res["nearest"]]
                if model_id in named:
                    hits.append((tier, seat))
        return hits

    # Arm-record shapes observed in the 2026-09-22 census of every *.record.json under the six data
    # roots: a proxied child whose own modelUsage names its client pin, a direct child, a record
    # with no served id at all, and the served spellings that are not registry ids verbatim.
    proxied = {"transport": "codex", "servedModel": "gpt-5.4-mini", "attestedModel": "gpt-5.6-luna"}
    direct = {"transport": "anthropic", "servedModel": "claude-opus-5"}
    bare = {"transport": "anthropic"}
    census_ids = ["big-pickle", "claude-fable-5-1", "claude-haiku-4-5", "claude-haiku-4-5-20251001",
                  "claude-opus-5", "claude-sonnet-5", "deepseek-flash", "deepseek-v4-flash",
                  "gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra", "hy3-free", "laguna-s-2.1-free",
                  "mimo-v2.5-free", "muse-spark-1.2-contributor-free",
                  "muse-spark-1.3-contributor-free", "nemotron-3-ultra-free",
                  "nemotron-3.5-lightning-free", "x-preview-f-free"]

    cases = [
        # ---- effortParam: a row declaring `effortParam: false` takes no effort, by alias, id or served id ----
        ("takes_effort: the `haiku` alias takes none", lambda: mr.takes_effort("haiku", live) is False),
        ("takes_effort: haiku's served id `claude-haiku-4-5` takes none",
         lambda: mr.takes_effort("claude-haiku-4-5", live) is False),
        ("takes_effort: haiku's registry id with a context suffix takes none",
         lambda: mr.takes_effort("claude-haiku-4-5-20251001[1m]", live) is False),
        ("takes_effort: `opus` and its id take an effort",
         lambda: mr.takes_effort("opus", live) is True and mr.takes_effort("claude-opus-5-5", live) is True),
        ("takes_effort: an unknown name, `inherit` or `?` takes an effort",
         lambda: all(mr.takes_effort(n, live) is True for n in ("no-such-model", "inherit", "?", None))),
        # ---- O-alias: the bare alias names the CURRENT version on every route ----
        ("`opus` resolves to claude-opus-5-5", lambda: rid("opus") == "claude-opus-5-5"),
        ("`opus5` resolves to claude-opus-5", lambda: rid("opus5") == "claude-opus-5"),
        ("`sol` resolves to gpt-6-sol and `sol56` to gpt-5.6-sol",
         lambda: rid("sol") == "gpt-6-sol" and rid("sol56") == "gpt-5.6-sol"),
        ("`luna` resolves to gpt-6-luna and `luna56` to gpt-5.6-luna",
         lambda: rid("luna") == "gpt-6-luna" and rid("luna56") == "gpt-5.6-luna"),
        ("`resolve gpt-6-sol` succeeds by exact id", lambda: rid("gpt-6-sol") == "gpt-6-sol"),
        ("`claude-opus-5-5` resolves by exact id", lambda: rid("claude-opus-5-5") == "claude-opus-5-5"),

        # ---- owner ruling 2026-09-22: a new version takes over its family ----
        ("the current luna/sol rows carry their family's roles, effort measured on the predecessor",
         lambda: mr.resolve("luna", live)["roles"] == ["sonnet", "haiku"]
                 and mr.resolve("sol", live)["roles"] == ["sol", "expansiveArchitecting", "scopedArchitecting", "thinPlanning"]
                 and all(mr.resolve(a, live)["effort"].get("evidence") == "measured"
                         and mr.resolve(a, live)["effort"].get("measuredVersion") == "gpt-5.6-" + a
                         for a in ("sol", "luna"))),
        ("for-role routes the fanout tier to gpt-6-luna, never to the superseded row",
         lambda: ("fanout", "codex") in routes_to("gpt-6-luna") and routes_to("gpt-5.6-luna") == []),
        ("...and the SAME report routes to claude-opus-5-5 (the probe reaches a routed row)",
         lambda: ("executor", "codex") in routes_to("claude-opus-5-5")),
        ("the superseded 5.6 rows claim no role and keep their own measured evidence",
         lambda: all(mr.resolve(a, live)["roles"] == []
                     and mr.resolve(a, live)["effort"].get("measuredVersion") == mr.resolve(a, live)["version"]
                     for a in ("sol56", "luna56"))),

        # ---- the dated alias history ----
        ("resolve_alias_at(opus, 2026-09-10) -> claude-opus-5",
         lambda: mr.resolve_alias_at("opus", "2026-09-10", live) == "claude-opus-5"),
        ("resolve_alias_at(opus, 2026-07-20) -> claude-opus-4-8",
         lambda: mr.resolve_alias_at("opus", "2026-07-20", live) == "claude-opus-4-8"),
        ("resolve_alias_at(opus, a time after the last boundary) -> claude-opus-5-5",
         lambda: mr.resolve_alias_at("opus", "2026-09-23T01:00:00Z", live) == "claude-opus-5-5"),
        ("a date before the first evidenced service is unresolved, never guessed",
         lambda: mr.resolve_alias_at("opus", "2026-05-01", live) is None),
        ("fable before its 5.1 boundary -> claude-fable-5",
         lambda: mr.resolve_alias_at("fable", "2026-08-15", live) == "claude-fable-5"),
        ("an alias with no history serves its current row", lambda: mr.resolve_alias_at(
            "laguna", "2026-08-25", live) == "laguna-s-2.1-free"),
        ("an unknown alias resolves to None", lambda: mr.resolve_alias_at("mythos", "2026-09-10", live) is None),
        ("every history id is a registered row",
         lambda: all(mr.row_by_id(e["id"], live) for h in live["aliasHistory"].values() for e in h)),

        # ---- record identity: attested, then version, then direct served, then history ----
        ("a proxied record labels by attestedModel, not its servedModel",
         lambda: mr.record_model_id(proxied, "luna", "2026-09-01", live) == "gpt-5.6-luna"
                 and mr.label(mr.record_model_id(proxied, "luna", "2026-09-01", live), live)
                 == mr.row_by_id("gpt-5.6-luna", live)["label"]),
        ("...and a proxied servedModel alone is never identity (codex is not direct)",
         lambda: not mr.transport_is_direct("codex", live)
                 and mr.record_model_id({"transport": "codex", "servedModel": "gpt-5.4-mini"},
                                        "luna", "2026-09-01", live) == "gpt-5.6-luna"),
        ("a direct record labels by servedModel", lambda: mr.transport_is_direct("anthropic", live)
         and mr.record_model_id(direct, "opus", "2026-09-23", live) == "claude-opus-5"),
        ("a record with no served id falls back to the dated alias history",
         lambda: mr.record_model_id(bare, "opus", "2026-09-10", live) == "claude-opus-5"),
        ("an unregistered modelVersion is returned raw, never relabelled by the alias history",
         lambda: mr.record_model_id({"transport": "anthropic", "modelVersion": "claude-opus-6"},
                                    "opus", "2026-09-23", live) == "claude-opus-6"
                 and mr.row_by_id("claude-opus-6", live) is None),
        ("an unknown transport is never direct", lambda: not mr.transport_is_direct(None, live)
         and not mr.transport_is_direct("anthropic (Workflow)", live)),
        ("every model id in the census record set resolves to a row",
         lambda: [i for i in census_ids if mr.row_by_id(i, live) is None] == []),
        ("row_by_id strips the context suffix", lambda: mr.row_by_id("claude-opus-5-5[1m]", live)["id"]
         == "claude-opus-5-5"),
        ("row_by_id is None for an unregistered id, and label falls back to it",
         lambda: mr.row_by_id("mythos-x", live) is None and mr.label("mythos-x", live) == "mythos-x"),

        # ---- one model table ----
        ("lifecycle: opus-5-5 current, opus-5 superseded, opus-4-8 retired",
         lambda: [mr.lifecycle(i, live) for i in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8")]
                 == ["current", "superseded", "retired"]),
        ("family groups the versions of one line",
         lambda: mr.family("claude-opus-5-5", live) == mr.family("claude-opus-5", live) == "opus"
                 and mr.family("gpt-6-sol", live) == mr.family("gpt-5.6-sol", live)),
        ("rows(lifecycle='current') holds each current row and no superseded one",
         lambda: {"claude-opus-5-5", "gpt-6-sol", "gpt-6-luna"} <= {m["id"] for m in mr.rows("current", live)}
                 and not {"claude-opus-5", "gpt-5.6-sol"} & {m["id"] for m in mr.rows("current", live)}),
        ("anthropic_ids lists the dispatchable Anthropic rows only",
         lambda: {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5"} <= set(mr.anthropic_ids(live))
                 and not any(i.startswith("gpt-") for i in mr.anthropic_ids(live))
                 and "claude-opus-4-8" not in mr.anthropic_ids(live)),
        ("every row carries label, family, version, lifecycle, order, costRank and prior",
         lambda: all(all(k in m for k in ("label", "family", "version", "lifecycle", "order",
                                          "costRank", "prior")) for m in live["models"])),
        ("codex effort-values come from the row, bounded by the transport's pin vocabulary",
         lambda: cli(["effort-values", "sol"])[1].strip() == '["low", "medium", "high", "xhigh", "max"]'),
        ("a row whose own effortValues share no rung with its transport is refused, never all rungs",
         lambda: raises_registry(lambda: mr.effort_values(
             dict(mr.resolve("sol", live), effortValues=["ultra"]), live))),
        ("a row declaring an empty effortValues list is refused, never all rungs",
         lambda: raises_registry(lambda: mr.effort_values(
             dict(mr.resolve("sol", live), effortValues=[]), live))),
        ("`available` names both versions of each moved alias",
         lambda: all(s in cli(["available"])[1] for s in (
             "opus     claude-opus-5-5", "opus5    claude-opus-5", "sol      gpt-6-sol",
             "sol56    gpt-5.6-sol", "luna     gpt-6-luna", "luna56   gpt-5.6-luna"))),

        # ---- A3: for-role picks no effort from ladder prose ----
        ("an Anthropic hop line carries no -e rung", lambda: not RUNG_FLAG.search(mr._sidecar_line(
            mr.resolve("opus", live), live))),
        ("...and points at the ladder cell instead", lambda: "ladder" in mr._sidecar_line(
            mr.resolve("opus", live), live)),
        ("an unmeasured row's placeholder rungs never become a printed -e",
         lambda: not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("terra", live), live))),
        ("a measured provider row prints no -e either: the ladder cell owns the rung",
         lambda: not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("flash", live), live))
                 and not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("luna56", live), live))),
        ("`for-role scout` and `fanout` print no `-e` on any hop line",
         lambda: not any(RUNG_FLAG.search(ln + " ") for r in ("scout", "fanout")
                         for ln in cli(["for-role", r, "--from", "anthropic"])[1].splitlines())),
        ("`for-role executor --from codex` prints no `-e` on the opus hop",
         lambda: "-m opus -e" not in cli(["for-role", "executor", "--from", "codex"])[1]),

        # ---- validation of the new fields ----
        ("aliasHistory naming an unregistered id is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][0].update(id="claude-opus-9"), "aliasHistory")),
        ("aliasHistory whose last entry is not the alias's current row is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"].pop(), "aliasHistory")),
        ("aliasHistory with non-increasing boundaries is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][1].update(
             until=d["aliasHistory"]["opus"][0]["until"]), "aliasHistory")),
        ("an aliasHistory boundary without evidence is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][0].pop("evidence"), "evidence")),
        ("a lifecycle outside current|superseded|retired is rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(lifecycle="old"), "lifecycle")),
        ("two current rows in one family are rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(lifecycle="current"), "current")),
        ("a row without a label is rejected", lambda: rejects(lambda d: row(d, "opus").pop("label"), "label")),
        ("a prior outside frontier|mid|small is rejected",
         lambda: rejects(lambda d: row(d, "opus").update(prior="huge"), "prior")),
        ("a duplicate display order is rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(order=row(d, "opus")["order"]), "order")),
    ]

    failed = 0
    for name, fn in cases:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def run_price_fresh_off_peak(data):
    """1M fresh tokens at half the list cache-miss rate -- the expected SC_PRICE_AT answer."""
    return next(m for m in data["models"] if m["alias"] == "flash")["price"]["cacheMissPer1M"] * 0.5


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

    # ---- live SSOT coupling ------------------------------------------------
    live_tiers = mr.role_tiers()
    ok = sorted(live_tiers) == sorted(mr.TIER_ORDER)
    failed += not ok
    print("%s the live ladder claims every tier: %r" % ("ok  " if ok else "FAIL", sorted(live_tiers)))

    # A publication context (or any consumer whose external_models.json is not this project's own)
    # can carry a registry `load()` rejects for reasons unrelated to this suite's own cases -- a
    # bare crash here would abort every case below with a raw traceback and no tally instead of
    # one clean FAIL, the same "proof ran to completion" shape guarded elsewhere in this battery.
    try:
        live = mr.load()
    except Exception as exc:
        failed += 1
        total = len(CASES) + 2
        print("FAIL the live registry loads for the SSOT-coupling cases below  raised %s: %s"
              % (type(exc).__name__, exc))
        print("\n%d/%d passed" % (total - failed, total))
        return 1 if failed else 0
    ok = all(mr.rows_for_tier(t, live, live_tiers) for t in mr.TIER_ORDER)
    failed += not ok
    print("%s every tier has at least one available row in the live registry"
          % ("ok  " if ok else "FAIL"))

    # `--check` must stay green: the harness_tests stamp the commit needs depends on it, and this
    # slice deliberately adds no validation that the shipped `roles` free-text would red.
    ok = mr._cmd_check([]) == 0
    failed += not ok
    print("%s `--check` still accepts the shipped registry" % ("ok  " if ok else "FAIL"))

    # An unvalidated closed set is unenforceable: `sidcar` is not `all`, so a typo would silently
    # narrow the exclusion to hops and leave the row dispatchable where it was meant to be gone.
    # Built by planting the typo in the SHIPPED registry, not in a minimal dict: the validator has
    # transport-level rules that fire first, so a hand-built fixture reds on an unrelated field and
    # the case would pass without ever reaching the scope check.
    import copy

    def why(scope):
        d = copy.deepcopy(live)
        for m in d["models"]:
            if m.get("alias") == "astra":
                m["status"]["scope"] = scope
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        return ""

    ok = "status.scope" in why("sidcar")
    failed += not ok
    print("%s `--check` rejects an unknown status.scope: %r"
          % ("ok  " if ok else "FAIL", why("sidcar")[:70]))

    ok = why("sidecar") == "" and why("all") == ""
    failed += not ok
    print("%s ...and accepts both legal scopes" % ("ok  " if ok else "FAIL"))

    def why_limits(patch):
        d = copy.deepcopy(live)
        row = next(m for m in d["models"] if m.get("alias") == "luna")
        row["limits"].update(patch)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        return ""

    invalid_limit_cases = [
        ({"contextTokens": "272k"}, "limits.contextTokens"),
        ({"contextTokens": 0}, "limits.contextTokens"),
        ({"maxContextTokens": -1}, "limits.maxContextTokens"),
        ({"effectiveContextPercent": "95"}, "limits.effectiveContextPercent"),
        ({"effectiveContextPercent": 0}, "limits.effectiveContextPercent"),
        ({"effectiveContextPercent": 101}, "limits.effectiveContextPercent"),
        ({"contextTokens": 872000, "maxContextTokens": 272000}, "maxContextTokens"),
    ]
    for patch, field in invalid_limit_cases:
        reason = why_limits(patch)
        ok = field in reason
        failed += not ok
        print("%s `--check` rejects malformed limits %r: %r"
              % ("ok  " if ok else "FAIL", patch, reason[:90]))

    ok = mr.status_scope({"status": {"state": "unavailable", "reason": "r"}}) == "all"
    failed += not ok
    print("%s an exclusion with no scope defaults to `all`" % ("ok  " if ok else "FAIL"))

    # The arity check used to run on the UNSTRIPPED argv, so `--from codex` with no role passed it
    # and then raised IndexError at args[0]. main() catches only RegistryError -> raw traceback.
    ok = mr._cmd_for_role(["--from", "codex"]) == 2
    failed += not ok
    print("%s `for-role --from codex` with no role exits 2, not a traceback"
          % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["--from"]) == 2
    failed += not ok
    print("%s `--from` with no name exits 2" % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["executor", "--from", "nosuchtransport"]) == 2
    failed += not ok
    print("%s an unregistered transport exits 2" % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["executor", "--from", "codex"]) == 0
    failed += not ok
    print("%s a well-formed for-role still exits 0" % ("ok  " if ok else "FAIL"))

    # ---- time-of-day pricing, peak gate policy, version-bound evidence, effort vocabulary ----
    # Fixture = the SHIPPED registry with a schedule planted on the deepseek transport, for the same
    # reason as the scope cases: transport-level rules fire first on a hand-built dict.
    sched_failed, sched_total = _schedule_cases(live)
    failed += sched_failed

    ident_failed, ident_total = _identity_cases(live)
    failed += ident_failed

    total = len(CASES) + 3 + 3 + len(invalid_limit_cases) + 4
    total += sched_total + ident_total
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
