#!/usr/bin/env python3
"""Sub-hook of post_edit_dispatch.py: the facts a `task` brief carries, at the brief write.

Class: advisory. It never denies, and a malformed `task` block is named in its context line.

A Write or Edit that leaves a file holding a ```task block gets one context line. It lists each row
claiming the block's tier, in registry order with no pick: the route, the ladder cost, each paying
plan's room (`room unknown` on a cold capacity cache, read cache-only with no probe) and the brief's
size fit on that row's window. Owners: `dispatch_result.task_block` (the block), `provider_bands.lean_candidates`
(rows, routes, rooms), `dispatch_size.window_fit` (the fit) and `model_registry.ladder_costs` (the cost of a
pay-per-token row, which `lean_candidates` lists without one).

Contract: `process(payload) -> {"context": text} | None`.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
TAG = "[task-brief-facts]"
TOOL_NAMES = ("Write", "Edit")
FENCE = "```task"


def _owners():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import dispatch_result
    import dispatch_size
    import model_registry
    import provider_bands
    return dispatch_result, dispatch_size, model_registry, provider_bands


def _room(value):
    return "room unknown" if value is None else "room %.2f" % value


def _fit(dispatch_size, model_id, characters, data):
    fit = dispatch_size.window_fit(model_id, characters, data)
    if fit["error"]:
        return "window unjudged (%s)" % fit["error"]
    if fit["over"]:
        return "over by %d tokens" % fit["over"]
    return "fits (%d of %d tokens)" % (fit["tokens"], fit["usable"])


def _route_line(row, dispatch_size, data, characters):
    plans = "; ".join("%s %s" % (p["plan"], _room(p["room"])) for p in row["plans"])
    line = "  %s %s cost %s; window %s; plans %s" % (
        row["alias"], row["id"], "unlisted" if row["cost"] is None else row["cost"],
        _fit(dispatch_size, row["id"], characters, data), plans)
    if row.get("probation"):
        line += '; probation "%s"' % row["probation"]
    return line


def _facts(text, block, data, session):
    dispatch_result, dispatch_size, model_registry, provider_bands = _owners()
    tier = block.get("tier")
    if not isinstance(tier, str) or not tier:
        return "%s the task block names no tier, so no row can be listed" % TAG

    def cache_only(name, _data=None):
        return provider_bands.read_capacity(name, data, live=False)

    got = provider_bands.lean_candidates(tier, session, data=data, read=cache_only)
    head = "%s tier %s, session plan %s, facts only in registry order" % (TAG, tier, session)
    if got.get("refused"):
        return "%s tier %s: %s" % (TAG, tier, got["refused"])
    lines = [head]
    characters = len(text)
    for route, rows in got["routes"].items():
        lines.append("route %s%s:" % (route, " (session route)" if route == got["seat"] else ""))
        lines += [_route_line(row, dispatch_size, data, characters) for row in rows]
    if got["payOptions"]:
        costs = model_registry.ladder_costs(data)
        lines.append("pays per token (no plan on any route has room):")
        for option in got["payOptions"]:
            lines.append("  %s %s (%s); cost %s; window %s; room n/a (pays per token)" % (
                option["alias"], option["id"], option["transport"],
                costs.get(option["id"], "unlisted"),
                _fit(dispatch_size, option["id"], characters, data)))
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
    dispatch_result, _size, model_registry, provider_bands = _owners()
    try:
        block = dispatch_result.task_block(text)
    except ValueError as exc:
        return {"context": "%s the task block is malformed: %s; fix it before the brief is dispatched." % (TAG, exc)}
    if block is None:
        return None
    session = provider_bands.session_plan()
    data = model_registry.load()
    return {"context": _facts(text, block, data, session)}
