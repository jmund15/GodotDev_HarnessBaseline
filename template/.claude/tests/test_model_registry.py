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
import copy
import importlib.util
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
| gpt-5.6-luna (sidecar) | scoped planning + spec-tight execution | max |
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

    live = mr.load()
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

    total = len(CASES) + 3 + 3 + 4
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
