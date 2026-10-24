#!/usr/bin/env python3
"""Print one owner-facing view of plan quota, pay-per-token balances, tier routes and models.

A view, not a store: every value is read from its owner on each run and nothing is written.
- Plans: `provider_bands.plan_rows` and `provider_bands.display_windows` (band, % left and time left
  per window).
- Pay per token: the registry's `marginal-usd` transports, their recorded status, and the balance
  their capacity reading reports. An unavailable transport is not read.
- Tiers: the plans able to pay for each tier's rows, in registry order (`provider_bands.room_facts`), and
  `model_registry.rows_for_tier`. No column picks one: the Plans table carries each plan's figures.
- Models: `model_registry.available_models` rows that claim a tier.

Usage:
    python3 .claude/tools/quota_dashboard.py [--cache-only]

Readings are cache-first and read live only when the cache is cold; `--cache-only` never reads
live, so a cold plan shows as unreadable. Exit 0, or 2 when the registry is unreadable; an unreadable
capacity reading is printed, not raised.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402
import provider_bands  # noqa: E402
import provider_capacity  # noqa: E402


def _state(cfg, got=None):
    """(state, reason): the registry's recorded status, else the capacity reading's, else unrecorded."""
    status = (cfg or {}).get("status") or {}
    live = got.get("status") if isinstance(got, dict) else None
    return status.get("state") or live or "unrecorded", status.get("reason") or ""


def _band(got):
    if not isinstance(got, dict):
        return "unknown"
    if got.get("status") == "exhausted":
        return "Exhausted"
    quota = got.get("quota") if isinstance(got.get("quota"), dict) else {}
    return quota.get("routingBand") or "unknown"


def build(data=None, read=None, session_plan=None, now=None):
    """{plans, pay, tiers, models}: plain rows for `render`."""
    data = data if data is not None else model_registry.load()
    read = read or provider_bands.read_capacity
    now = time.time() if now is None else now
    seen, errors = {}, {}

    def cached_read(transport, _data=None):
        if transport not in seen:
            try:
                seen[transport] = read(transport, data)
            except Exception as exc:  # noqa: BLE001
                seen[transport] = None
                errors[transport] = exc
        return seen[transport]

    plans = []
    for row in provider_bands.plan_rows(data, cached_read, now):
        got = cached_read(row["transport"])
        windows = provider_bands.display_windows(got, now)
        plans.append({"plan": row["transport"],
                      "type": row.get("planType") or (got or {}).get("planType") or "?",
                      "band": windows[0]["band"] if windows else _band(got),
                      "windows": windows, "now": now,
                      "note": ("unreadable: %s" % errors[row["transport"]]) if row["transport"] in errors
                      else row.get("why") or ""})

    pay = []
    for name, cfg in (data.get("transports") or {}).items():
        if (cfg or {}).get("costModel") != "marginal-usd":
            continue
        state, reason = _state(cfg)
        balance = None
        if state != "unavailable":
            got = cached_read(name)
            state = _state(cfg, got)[0]
            amount = ((got or {}).get("balance") or {}) if isinstance(got, dict) else {}
            if isinstance(amount.get("amount"), (int, float)):
                balance = "%.2f %s" % (amount["amount"], amount.get("currency") or "")
        pay.append({"transport": name, "state": state, "balance": balance,
                    "note": reason if state == "unavailable" else ""})

    tiers = []
    for tier in model_registry.TIER_ORDER:
        try:
            facts = provider_bands.room_facts(provider_bands.facts(tier, session_plan, data=data, read=cached_read,
                                                                   now=now), read=cached_read, now=now)
            pays = ", ".join(p["transport"] + ("" if p.get("room") is not None else " (%s)" % p.get("why"))
                             for p in facts["plans"])
        except Exception as exc:  # noqa: BLE001
            pays = "error: %s" % (str(exc)[:80] or type(exc).__name__)
        rows = [m["alias"] for m in model_registry.rows_for_tier(tier, data)]
        tiers.append({"tier": tier, "pays": pays, "rows": rows})

    transports = data.get("transports") or {}
    models = []
    for m in model_registry.available_models(data):
        roles = model_registry.entry_tiers(m)
        if roles:
            models.append({"alias": m["alias"], "transport": m["transport"], "roles": roles,
                           "state": _state(transports.get(m["transport"]),
                                           cached_read(m["transport"]))[0]})
    return {"plans": plans, "pay": pay, "tiers": tiers, "models": models}


def _num(value, fmt):
    return fmt % value if isinstance(value, (int, float)) else "—"


def render(view):
    out = ["## Plans", "", "| plan | type | band | quota left | window left | reset in | note |",
           "|---|---|---|---|---|---|---|"]
    for r in view["plans"]:
        windows = r.get("windows") or []
        labels = len(windows) > 1
        left, window, days, resets = [], [], [], []
        for w in windows:
            prefix = (provider_bands.window_name(w) + " ") if labels else ""
            left.append(prefix + _num(w["left"], "%.0f%%"))
            window.append(prefix + _num(w["timeLeft"], "%.0f%%"))
            reset = provider_capacity._epoch(w.get("resetsAt"))
            days.append(prefix + (_num((reset - r["now"]) / 86400, "%.1fd") if reset else "—"))
            clause = provider_bands.reset_clause(w, r["now"])
            if clause:
                resets.append(prefix + clause)
        note = "; ".join(filter(None, [r["note"], *resets]))
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["plan"], r["type"], r["band"], "; ".join(left) or "—",
            "; ".join(window) or "—", "; ".join(days) or "—", note))
    out += ["", "## Pay per token", "",
            "| transport | state | balance | note |", "|---|---|---|---|"]
    for r in view["pay"]:
        out.append("| %s | %s | %s | %s |" % (r["transport"], r["state"], r["balance"] or "—", r["note"]))
    out += ["", "## Tiers", "", "| tier | plans that can pay | rows serving it |", "|---|---|---|"]
    for r in view["tiers"]:
        out.append("| %s | %s | %s |" % (r["tier"], r["pays"] or "none", ", ".join(r["rows"]) or "—"))
    out += ["", "## Models", "", "| alias | transport | roles | transport state |", "|---|---|---|---|"]
    for r in view["models"]:
        out.append("| %s | %s | %s | %s |" % (r["alias"], r["transport"], ", ".join(r["roles"]), r["state"]))
    return "\n".join(out) + "\n"


def main(argv):
    if any(a not in ("--cache-only",) for a in argv):
        print(__doc__.split("Usage:")[1].split("\n\n")[0].strip(), file=sys.stderr)
        return 2
    read = None
    if "--cache-only" in argv:
        def read(transport, data=None):
            return provider_capacity.cached(transport, data=data)
    try:
        view = build(read=read, session_plan=provider_bands.session_plan())
    except model_registry.RegistryError as exc:
        print("registry unreadable: %s" % exc, file=sys.stderr)
        return 2
    print(render(view), end="")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
