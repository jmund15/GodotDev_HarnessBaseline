#!/usr/bin/env python3
"""Proof for hooks/_session_transport.py.

Every branch plus the negatives that matter. The negatives ARE the point: this resolver's job is
to fail closed, and a resolver that returns `anthropic` when it should return `unknown` hands a
non-Anthropic session the permissive path with nothing downstream to notice.

Run: python3 .claude/tests/test_session_transport.py
"""
import importlib.util
import os
import sys
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "hooks", "_session_transport.py")
spec = importlib.util.spec_from_file_location("st", MOD)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

DATA = {
    "transports": {
        "anthropic": {"roleSource": "reference/model_ladder_evidence.md"},
        "codex": {
            "launcher": ".claude/scripts/codex_proxy_sidecar.sh",
            "serverSidePins": {
                "effortValues": ["none", "low", "medium", "high", "xhigh", "max"],
            },
        },
        "deepseek": {"baseUrl": "https://api.deepseek.com/anthropic"},
        "opencode": {"baseUrl": "https://opencode.ai/zen/v1"},
    },
    "models": [
        {"transport": "codex", "id": "gpt-5.6-terra", "alias": "terra"},
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna"},
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus"},
    ],
}

# A second registry, identical in shape but carrying every `status` variant. Kept separate so the
# cases above keep proving that a registry with NO status field resolves exactly as it did before.
AVAIL = {
    "transports": {
        "anthropic": {"roleSource": "reference/model_ladder_evidence.md"},
        "codex": {"launcher": ".claude/scripts/codex_proxy_sidecar.sh"},
    },
    "models": [
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna"},
        # Excluded everywhere: not a delegation target from any seat, its own included.
        {"transport": "codex", "id": "gpt-6-astra", "alias": "astra",
         "status": {"state": "unavailable", "scope": "all", "reason": "owner decision"}},
        # Excluded only across a HOP: still pinnable from a codex seat.
        {"transport": "codex", "id": "gpt-5.6-sol", "alias": "sol",
         "status": {"state": "unavailable", "scope": "sidecar", "reason": "hop only"}},
        # No scope stated: `all` is the default.
        {"transport": "codex", "id": "gpt-5.6-terra", "alias": "terra",
         "status": {"state": "unavailable", "reason": "no scope stated"}},
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus"},
    ],
}

CASES = [
    # (name, env, expected transport, expected source)
    ("explicit env wins", {"CLAUDE_CODE_TRANSPORT": "codex"}, "codex", "env"),
    ("env wins over a conflicting base URL",
     {"CLAUDE_CODE_TRANSPORT": "codex", "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
     "codex", "env"),
    ("env is case-insensitive", {"CLAUDE_CODE_TRANSPORT": "CODEX"}, "codex", "env"),
    ("base URL host match", {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"},
     "deepseek", "baseUrl"),
    ("base URL host match, different path",
     {"ANTHROPIC_BASE_URL": "https://opencode.ai/zen/v1/messages"}, "opencode", "baseUrl"),
    ("no signal at all is the host transport", {}, "anthropic", "default"),
    ("empty base URL is the host transport", {"ANTHROPIC_BASE_URL": ""}, "anthropic", "default"),

    # --- negatives: each of these MUST fail closed ---
    ("env naming an unregistered transport → unknown",
     {"CLAUDE_CODE_TRANSPORT": "gemini"}, "unknown", "unmatched"),
    ("env typo → unknown, not the host transport",
     {"CLAUDE_CODE_TRANSPORT": "codexx"}, "unknown", "unmatched"),
    ("a loopback proxy with no env declaration → unknown",
     {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18771"}, "unknown", "unmatched"),
    ("transport name in the PATH is not a host match",
     {"ANTHROPIC_BASE_URL": "https://example.com/api/deepseek/v1"}, "unknown", "unmatched"),
    ("transport name in the QUERY is not a host match",
     {"ANTHROPIC_BASE_URL": "https://example.com/v1?upstream=api.deepseek.com"},
     "unknown", "unmatched"),
    ("a lookalike host is not a host match",
     {"ANTHROPIC_BASE_URL": "https://api.deepseek.com.evil.test/anthropic"},
     "unknown", "unmatched"),
]


def main():
    failed = 0
    for name, env, want_t, want_s in CASES:
        got_t, got_s = st.resolve(env=env, data=DATA)
        ok = (got_t, got_s) == (want_t, want_s)
        failed += not ok
        print("%s %s: want %s/%s, got %s/%s"
              % ("ok  " if ok else "FAIL", name, want_t, want_s, got_t, got_s))

    # An unreadable registry must NOT resolve to the permissive host transport.
    got_t, got_s = st.resolve(env={}, data={})
    ok = (got_t, got_s) == ("unknown", "registry-unreadable")
    failed += not ok
    print("%s unreadable registry → unknown (never anthropic): got %s/%s"
          % ("ok  " if ok else "FAIL", got_t, got_s))

    # The fail-closed state must expose NO legal pins, or the deny it drives allows everything.
    ids = st.legal_model_ids("unknown", data=DATA)
    ok = ids == []
    failed += not ok
    print("%s unknown transport has no legal pins: got %r" % ("ok  " if ok else "FAIL", ids))

    ids = st.legal_model_ids("codex", data=DATA)
    ok = ids == ["gpt-5.6-luna", "gpt-5.6-terra", "luna", "terra"]
    failed += not ok
    print("%s codex legal pins are its ids AND aliases: got %r" % ("ok  " if ok else "FAIL", ids))

    ok = st.roster("codex", data=DATA) == [("luna", "gpt-5.6-luna"), ("terra", "gpt-5.6-terra")]
    failed += not ok
    print("%s roster names alias/id pairs" % ("ok  " if ok else "FAIL"))

    efforts = st.legal_effort_values("codex", data=DATA)
    ok = efforts == ["none", "low", "medium", "high", "xhigh", "max"]
    failed += not ok
    print("%s codex effort vocabulary comes from the transport registry: got %r"
          % ("ok  " if ok else "FAIL", efforts))

    efforts = st.legal_effort_values("unknown", data=DATA)
    ok = efforts == []
    failed += not ok
    print("%s unknown transport has no legal efforts: got %r"
          % ("ok  " if ok else "FAIL", efforts))

    # ---- S4: availability gates DISPATCH ----------------------------------
    # `legal_model_ids` and `roster` must filter IDENTICALLY. Filtering one desyncs the guard's
    # deny message from its deny behaviour, so the refusal names the very pin it just refused.
    def check(ok, msg):
        nonlocal_failed[0] += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", msg))

    nonlocal_failed = [failed]

    ids = st.legal_model_ids("codex", data=AVAIL)
    check("astra" not in ids and "gpt-6-astra" not in ids,
          "a scope:all exclusion is absent from its OWN transport's legal pins: %r" % (ids,))

    roster = dict((a, i) for a, i in st.roster("codex", data=AVAIL))
    check("astra" not in roster,
          "...and absent from the roster too, so the deny message cannot name it")

    check(sorted(roster) == sorted(a for a in ids if not a.startswith("gpt-")),
          "roster and legal_model_ids filter identically: %r vs %r" % (sorted(roster), ids))

    check("terra" not in ids, "an exclusion with no scope defaults to `all`")

    check("sol" in st.legal_model_ids("codex", data=AVAIL, seat="codex"),
          "a scope:sidecar row stays pinnable from its OWN transport")

    check("sol" not in st.legal_model_ids("codex", data=AVAIL, seat="anthropic"),
          "...and is denied across a hop, which is what `sidecar` scopes")

    # The seat is the SESSION's transport, not the row's. Defaulting it to `transport` made
    # `scope == "sidecar" and row.transport == seat` unconditionally true, so a sidecar-scoped
    # exclusion could never fire anywhere in the harness. Production call sites omit `seat` and
    # ask about their OWN transport, where the two coincide -- so nothing there changes.
    check(st.legal_model_ids("codex", data=AVAIL, seat="codex")
          == ["gpt-5.6-luna", "gpt-5.6-sol", "luna", "sol"],
          "a codex seat asking about codex still sees luna + sol")

    check("sol" not in st.legal_model_ids("codex", data=AVAIL, seat="anthropic"),
          "an anthropic seat asking about codex does NOT see the sidecar-scoped row")

    # One malformed row used to abort the whole comprehension into a bare `except` that returned
    # the UNFILTERED roster -- turning every excluded model back into a legal pin.
    torn = {"transports": AVAIL["transports"],
            "models": AVAIL["models"] + [{"transport": "codex", "id": None, "alias": None}]}
    ids = st.legal_model_ids("codex", data=torn, seat="codex")
    check("astra" not in ids and "terra" not in ids,
          "a malformed row does not disable the filter for every other row: %r" % (ids,))

    check(st.legal_model_ids("anthropic", data=AVAIL) == ["claude-opus-5", "opus"],
          "a row with no status at all is untouched")

    with patch.dict(os.environ, {"CLAUDE_CODE_TRANSPORT": "codex"}, clear=True), \
            patch.object(st, "_registry", return_value={}):
        check("sol" in st.legal_model_ids("codex", data=AVAIL),
              "default seat resolves against the supplied registry, not an unreadable live one")
        check(("sol", "gpt-5.6-sol") in st.roster("codex", data=AVAIL),
              "roster uses the same supplied registry for seat resolution")
        check("sol" not in st.legal_model_ids("codex", data=AVAIL, seat="anthropic"),
              "explicit seat still overrides the environment with a supplied registry")

    import model_registry
    real_dispatchable = model_registry.dispatchable

    def broken_row(row, seat, data):
        if row.get("id") == "gpt-5.6-luna":
            raise ValueError("torn registry row")
        return real_dispatchable(row, seat, data)

    with patch("model_registry.dispatchable", side_effect=broken_row):
        ids = st.legal_model_ids("codex", data=AVAIL, seat="codex")
        check("luna" not in ids and "gpt-5.6-luna" not in ids,
              "a dispatchability exception excludes that row instead of re-admitting it")

    failed = nonlocal_failed[0]

    total = len(CASES) + 4 + 16
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
