#!/usr/bin/env python3
"""Read every plan-quota transport's OWN band, and compare it against this session's.

Three currencies exist, and two of them expire unused: this session's Anthropic plan quota,
a provider's plan quota (Codex), and real dollars (DeepSeek). Plan-quota-vs-plan-quota is a
BAND COMPARISON, not a spend decision -- neither side bills marginally, so the question is
only which allowance is being wasted faster. The normalized quota view comes from
`provider_capacity.py`; this module projects its routing band for comparison.

Lives here rather than inside workflow_provider_guard.py because the hook is not the only
caller that needs it: the pre-dispatch checklist asks the same question for a sidecar
invocation, which no PreToolUse hook sees.

FAIL POSTURE: every failure returns an empty/None reading. A budget advisory that raises
would block a dispatch over a hint, and a probe that spawns a subprocess has many ways to
fail that have nothing to do with the allowance it was asked about.

Provider-capacity execution, cache freshness and fallback live in `provider_capacity.py`; this file
is only the quota-band projection used by routing comparisons.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402
import provider_capacity  # noqa: E402
import quota_bands  # noqa: E402

DEFAULT_TTL_S = 600  # compatibility parameter; provider_capacity owns freshness


def probe(transport, data=None):
    """Read normalized live-first capacity and return its routing-band view."""
    try:
        capacity = provider_capacity.reading(transport, data=data)
    except Exception:
        return None
    quota = capacity.get("quota") if isinstance(capacity, dict) else None
    if not isinstance(quota, dict):
        return None
    band = "Exhausted" if capacity.get("status") == "exhausted" else quota.get("routingBand")
    if not band:
        return None
    windows = quota.get("windows") or []
    routing = next((w for w in windows if w.get("name") == "seven_day"), None)
    binding = next((w for w in windows if w.get("name") == quota.get("bindingWindow")), None)
    pressure = (routing or binding or {}).get("pressure")
    return {
        "transport": transport,
        "band": band,
        "pressure": pressure,
        "planType": capacity.get("planType"),
        "sourceKind": capacity.get("sourceKind"),
        "observedAt": capacity.get("observedAt"),
    }


def reading(transport, ttl=DEFAULT_TTL_S, data=None):
    """Compatibility entry point; provider_capacity owns the only cache and freshness policy."""
    _ = ttl
    return probe(transport, data)


def plan_quota_transports(data=None):
    """Transports that spend a plan allowance AND have at least one selectable model.

    A transport with no available model is out of consideration entirely -- advising a route
    to it would name a launcher that refuses at its own availability gate.
    """
    data = data if data is not None else model_registry.load()
    live = {m["transport"] for m in model_registry.available_models(data)}
    out = []
    for name in (data.get("transports") or {}):
        if name not in live:
            continue
        try:
            if model_registry.cost_model_for(name, data) == "plan-quota":
                out.append(name)
        except Exception:
            continue
    return sorted(out)


def slacker_than(session_band, ttl=DEFAULT_TTL_S, data=None, session_transport=None):
    """Plan-quota transports whose own band has MORE headroom than this session's.

    Returns [{transport, band, pressure, planType, launchers}], most-slack first. An
    uncomputable provider band is omitted rather than treated as Surplus: Surplus is the most
    permissive answer, and defaulting an unknown to it would advise routing work toward an
    allowance nobody measured.

    `session_transport` is EXCLUDED from the comparison. Since the host transport became a
    registry row (`anthropic`, 2026-09-04) a session can otherwise be advised to route work to
    itself: its own band is by definition equal to the session band, and an off-by-one in either
    rank would surface as "route to anthropic" on an Anthropic session. The exclusion is by
    identity, not by name -- a codex session compares the anthropic row normally, which is what
    makes host-transport dispatch band-aware in the direction that matters.
    """
    data = data if data is not None else model_registry.load()
    try:
        here = quota_bands.band_rank(session_band)
    except Exception:
        return []
    rows = []
    for name in plan_quota_transports(data):
        if session_transport and name == session_transport:
            continue
        got = reading(name, ttl, data)
        band = (got or {}).get("band")
        if not band:
            continue
        try:
            rank = quota_bands.band_rank(band)
        except Exception:
            continue
        if rank >= here:
            continue
        cfg = (data.get("transports") or {}).get(name) or {}
        launchers = [cfg[k] for k in ("launcher", "launcherProxy") if cfg.get(k)]
        rows.append({
            "transport": name, "band": band, "pressure": (got or {}).get("pressure"),
            "planType": (got or {}).get("planType"), "launchers": launchers, "rank": rank,
        })
    return sorted(rows, key=lambda r: r["rank"])


def main(argv):
    if not argv or argv[0] == "list":
        for name in plan_quota_transports():
            got = reading(name) or {}
            print(f"{name}\t{got.get('band') or 'uncomputable'}\t{got.get('pressure')}"
                  f"\t{got.get('planType') or '?'}")
        return 0
    if argv[0] == "compare":
        if len(argv) != 2:
            print("usage: provider_bands.py compare <session-band>", file=sys.stderr)
            return 2
        for row in slacker_than(argv[1]):
            print(f"{row['transport']}\t{row['band']}\t{row['pressure']}\t"
                  f"{','.join(row['launchers'])}")
        return 0
    if argv[0] == "probe":
        if len(argv) != 2:
            print("usage: provider_bands.py probe <transport>", file=sys.stderr)
            return 2
        got = probe(argv[1])
        print(json.dumps(got, indent=2) if got else "null")
        return 0 if got else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
