#!/usr/bin/env python3
"""Sub-hook of post_edit_dispatch.py: the facts a `task` brief carries, at the brief write.

Class: advisory. It never denies, and a malformed `task` block is named in its context line.

A Write or Edit that leaves a file holding a ```task block gets one context block: a route header per route, then
one row per claiming model on that route, in registry order, with no pick. A plan route's header names each paying
plan's room (`room unknown` on a cold capacity cache, read cache-only with no probe). A plan row states its draw: the
output-rate ratio to the cheapest row on its own route, one measured point, never compared across plans. A
pay-per-token row states its spend: its priced reading ratio to the cheapest row on its route, with the USD figure.
Every row also states the ladder's intel and speed, whether the reading fits its usable window, and the first `+` and
first `−` clause of its `±` cell, quoted verbatim. The reading is the brief plus the files it names, as
`dispatch_size` measures a job and `_dispatch_checks.price_reading` prices a claimant.

Owners: `dispatch_result.task_block` (the block), `provider_bands.lean_candidates` (rows, routes, rooms, probation),
`dispatch_size` (the reading and the fit), `_dispatch_checks` (the price and the draw ratio) and the ladder (intel,
speed, `±`).

Contract: `process(payload) -> {"context": text} | None`.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
ROOT = os.path.dirname(os.path.dirname(HERE))
TAG = "[task-brief-facts]"
TOOL_NAMES = ("Write", "Edit")
FENCE = "```task"
NOTE = ("draw ratios compare models within one plan (list-price ratio, one measured point); "
        "across plans compare room")
HEADER = "model | draw or spend | intel | speed | window | first + | first −"


def _owners():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import _dispatch_checks
    import dispatch_result
    import dispatch_size
    import model_registry
    import provider_bands
    return dispatch_result, dispatch_size, model_registry, provider_bands, _dispatch_checks


def _n(value):
    return "{:,}".format(value)


def _room(value):
    return "room unknown" if value is None else "room %.2f" % value


def _fit(dispatch_size, row_id, tokens, data):
    fit = dispatch_size.fit_tokens(row_id, tokens, data)
    if fit["error"]:
        return "window unjudged (%s)" % fit["error"]
    if fit["over"]:
        return "over by %s tokens (%s of %s tokens)" % (_n(fit["over"]), _n(tokens), _n(fit["usable"]))
    return "fits (%s of %s tokens)" % (_n(tokens), _n(fit["usable"]))


def _reading(dispatch_size, path):
    """(tokens, unreadable): the brief as `dispatch_size` measures a job, the brief plus the files it names."""
    size = dispatch_size.dispatch_size({"jobs": [{"label": "brief", "promptFile": path}]}, ROOT)
    return size["perJob"][0]["readingTokens"], size["unreadable"]


def _ladder(model_registry, data):
    """{registry id: the ladder row} for the intel, speed and `±` cells; {} when the ladder cannot be read."""
    try:
        with open(model_registry.ladder_path(), encoding="utf-8") as fh:
            rows = model_registry.parse_ladder_rows(fh, ("model", "intel", "speed", "±"))
    except OSError:
        return {}
    out = {}
    for row in rows:
        for m in model_registry.ladder_rows(row["model"], data):
            out.setdefault(m["id"], row)
    return out


def _clauses(cell):
    """(first +, first −) clause of a ladder `±` cell, verbatim; None where the cell states none."""
    plus, sep, minus = (cell or "").partition("−")
    first_plus = plus.strip().lstrip("+").strip().split(";")[0].strip()
    first_minus = minus.split(";")[0].strip() if sep else ""
    return first_plus or None, first_minus or None


def _quoted(sign, clause):
    return '%s "%s"' % (sign, clause) if clause else "%s (none stated)" % sign


def _money(price):
    return "unpriced" if price is None else "$%.4f" % price


def _route_lines(route, entries, seat, data, tokens, owners, ladder):
    """One route's header line, then one row per entry. Ratios restart on each route: a plan's draw is taken
    against the cheapest priced row of that plan's route, and a pay-per-token row's spend against its own route."""
    dispatch_size, model_registry, dispatch_checks = owners
    suffix = ", session route" if route == seat else ""
    plan = model_registry.cost_model_for(route, data) == "plan-quota"
    if plan:
        plans = entries[0]["plans"] if entries else []
        room = "; ".join("%s %s" % (p["plan"], _room(p["room"])) for p in plans) or "no plan"
        lines = ["route %s (plan%s): %s" % (route, suffix, room)]
        draws = dispatch_checks.draw_ratios([e["id"] for e in entries], data)
    else:
        lines = ["route %s (pays per token%s, room n/a)" % (route, suffix)]
        prices = {e["id"]: dispatch_checks.price_reading(e["id"], tokens, data)[0] for e in entries}
        priced = [p for p in prices.values() if p]
        base = min(priced) if priced else None
    for entry in entries:
        cell = ladder.get(entry["id"], {})
        plus, minus = _clauses(cell.get("±"))
        if plan:
            ratio = draws.get(entry["id"])
            label = "draw " + ("n/a" if ratio is None else "%.2fx" % ratio)
        else:
            price = prices[entry["id"]]
            ratio = None if price is None or not base else price / base
            label = "spend %s %s" % ("n/a" if ratio is None else "%.2fx" % ratio, _money(price))
        cells = [entry.get("alias") or entry["id"], label, cell.get("intel", "?"), cell.get("speed", "?"),
                 _fit(dispatch_size, entry["id"], tokens, data), _quoted("+", plus), _quoted("−", minus)]
        if entry.get("probation"):
            cells.append('probation "%s"' % entry["probation"])
        lines.append(" | ".join(cells))
    return lines


def _facts(block, path, data, session):
    dispatch_result, dispatch_size, model_registry, provider_bands, dispatch_checks = _owners()
    tier = block.get("tier")
    if not isinstance(tier, str) or not tier:
        return "%s the task block names no tier, so no row can be listed" % TAG

    def cache_only(name, _data=None):
        return provider_bands.read_capacity(name, data, live=False)

    got = provider_bands.lean_candidates(tier, session, data=data, read=cache_only)
    if got.get("refused"):
        return "%s tier %s: %s" % (TAG, tier, got["refused"])
    tokens, unreadable = _reading(dispatch_size, path)
    ladder = _ladder(model_registry, data)
    lines = ["%s tier %s, session plan %s; reading %s tokens (the brief plus the files it names); "
             "USD only on pay-per-token rows; facts only, registry order" % (TAG, tier, session, _n(tokens))]
    if unreadable:
        lines.append("unreadable, counted 0: " + ", ".join(os.path.basename(p) for p in unreadable[:3]))
    lines += [NOTE, HEADER]
    pay = {}
    for option in got["payOptions"]:
        pay.setdefault(option["transport"], []).append(option)
    owners = (dispatch_size, model_registry, dispatch_checks)
    for route, entries in list(got["routes"].items()) + list(pay.items()):
        lines += _route_lines(route, entries, got["seat"], data, tokens, owners, ladder)
    return "\n".join(lines)


def process(payload):
    data_in = payload if isinstance(payload, dict) else {}
    if data_in.get("tool_name") not in TOOL_NAMES:
        return None
    path = (data_in.get("tool_input") or {}).get("file_path")
    if not isinstance(path, str) or not path:
        return None
    if "/.claude/scratch/" not in path.replace("\\", "/"):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    if FENCE not in text:
        return None
    dispatch_result, _size, model_registry, provider_bands, _checks = _owners()
    try:
        block = dispatch_result.task_block(text)
    except ValueError as exc:
        return {"context": "%s the task block is malformed: %s; fix it before the brief is dispatched." % (TAG, exc)}
    if block is None:
        return None
    session = provider_bands.session_plan()
    data = model_registry.load()
    return {"context": _facts(block, path, data, session)}
