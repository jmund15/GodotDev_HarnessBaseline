#!/usr/bin/env python3
"""Parity proof for the seat outcome rule kept in two copies: `seatMandates` in workflows/review_fanout.js
(engine runs) and `_seat_outcome` in tools/review_ledger.py (sidecar runs). Each `ROWS` row hands its
per-key token to both, and the per-mandate outcomes must match. `ROUTE_ROWS` feeds each seat return, the
capability-partial one and its negative controls, and the must-read coverage cases, through the engine's `perAgent`
and through `review_result.ingest` plus `review_ledger._sidecar_seats`, so each route's capability-partial fact and
must-read verdict are computed from the same return rather than handed in, and both routes must keep the same
number of findings.

    python3 .claude/tests/test_seat_outcome_parity.py
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
FANOUT = os.path.join(HERE, "..", "workflows", "review_fanout.js")
sys.path.insert(0, TOOLS)
import review_ledger  # noqa: E402

# name, status, keys, tokens (the per-key token the seat carries), stoppedAt, valid finding count,
# map (the typed checked.mandates handed to the engine, or None for a missing map), capabilityPartial (the route's fact)
ROWS = [
    ("clean one-key", "ok", ["a"], {"a": "clean"}, "exhausted-leads", 0, {"a": "clean"}, False),
    ("findings one-key", "ok", ["a"], {"a": "findings"}, "exhausted-leads", 2, {"a": "findings"}, False),
    ("mixed across mandates", "ok", ["a", "b", "c"], {"a": "findings", "b": "clean", "c": "not-reached"},
     "exhausted-leads", 1, {"a": "findings", "b": "clean", "c": "not-reached"}, False),
    ("partial covers nothing", "partial", ["a", "b"], {"a": "clean", "b": "findings"}, "budget", 1,
     {"a": "clean", "b": "findings"}, False),
    ("partial keeps a not-reached token", "partial", ["a", "b"], {"a": "clean", "b": "not-reached"}, "budget", 0,
     {"a": "clean", "b": "not-reached"}, False),
    ("not-reached token stopped blocked", "ok", ["a", "b"], {"a": "clean", "b": "not-reached"}, "blocked", 0,
     {"a": "clean", "b": "not-reached"}, False),
    ("uncovered seat reaches nothing", "uncovered", ["a", "b"], {"a": "clean", "b": "findings"}, "exhausted-leads",
     1, {"a": "clean", "b": "findings"}, False),
    ("uncovered seat stopped blocked", "uncovered", ["a"], {"a": "clean"}, "blocked", 0, {"a": "clean"}, False),
    ("failed seat fails every mandate", "failed", ["a", "b"], {"a": "clean", "b": "findings"}, None, 0,
     {"a": "clean", "b": "findings"}, False),
    ("partial seat with a missing map", "partial", ["a"], {"a": "clean"}, "budget", 0, None, False),
    ("missing map, one key, findings", "ok", ["a"], {"a": "findings"}, "exhausted-leads", 3, None, False),
    ("missing map, one key, clean", "ok", ["a"], {"a": "clean"}, "exhausted-leads", 0, None, False),
    ("missing map, several keys reach nothing", "ok", ["a", "b"], {"a": "not-reached", "b": "not-reached"},
     "exhausted-leads", 0, None, False),
    ("missing map, several keys stopped blocked", "ok", ["a", "b"], {"a": "not-reached", "b": "not-reached"},
     "blocked", 0, None, False),
    ("failed seat with a missing map", "failed", ["a"], {"a": "clean"}, None, 0, None, False),
    ("uncovered seat with a missing map", "uncovered", ["a", "b"], {"a": "not-reached", "b": "not-reached"},
     "blocked", 0, None, False),
    ("capability-partial seat keeps its map tokens", "partial", ["a", "b", "c"],
     {"a": "clean", "b": "findings", "c": "not-reached"}, "exhausted-leads", 1,
     {"a": "clean", "b": "findings", "c": "not-reached"}, True),
    ("capability-partial one-key seat keeps its token", "partial", ["a"], {"a": "clean"}, "exhausted-leads", 0,
     {"a": "clean"}, True),
    ("rejected partial covers nothing", "partial", ["a", "b"], {"a": "clean", "b": "findings"}, "exhausted-leads", 1,
     {"a": "clean", "b": "findings"}, False),
]

JS = r"""
const fs = require('fs')
const src = fs.readFileSync(process.argv[1], 'utf8')
const grab = (start, end) => { const i = src.indexOf(start); const j = src.indexOf(end, i); if (i < 0 || j < 0) throw new Error('anchor missing: ' + start); return src.slice(i, j) }
const code = [
  grab('const isRecord =', '\n'),
  grab('const isMandateMap', 'const isLocation'),
  grab('const mandateKeysOf', '// No pin is filled'),
  grab('const seatMandates', '\nconst sources = []'),
].join('\n')
const rows = JSON.parse(fs.readFileSync(0, 'utf8'))
const run = new Function('LENS_MANDATES', 'rows', code + '\nreturn rows.map(r => seatMandates(r.lens, r.status, r.valid, r.checked, r.map, r.capabilityPartial))')
const lensMandates = {}
rows.forEach(r => { lensMandates[r.lens] = r.keys })
console.log(JSON.stringify(run(lensMandates, rows)))
"""


def js_outcomes():
    payload = [{"lens": "lens%d" % i, "status": status, "keys": keys, "valid": valid, "map": mapping,
                "checked": {"stoppedAt": stopped} if stopped else None, "capabilityPartial": partial}
               for i, (_, status, keys, _tokens, stopped, valid, mapping, partial) in enumerate(ROWS)]
    done = subprocess.run(["node", "-e", JS, FANOUT], input=json.dumps(payload), capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("node failed: " + done.stderr.strip())
    return json.loads(done.stdout)


def py_outcomes():
    return [{key: review_ledger._seat_outcome(status, tokens[key], stopped, partial) for key in keys}
            for _, status, keys, tokens, stopped, _valid, _map, partial in ROWS]


ENGINE_RUN = os.path.join(HERE, "_engine_run.js")


def engine_return(reply, seat, keys, must_read):
    """`review_fanout.js`'s full return for one seat `seat` holding `keys` and `must_read`, whose agent returns `reply`."""
    agent = {"key": seat, "prompt": "Review fixture.", "model": "fixture-model", "effort": "medium",
             "agentType": "general-purpose", "mustRead": must_read}
    args = {"agents": [agent], "consolidate": False, "resultMode": "full", "__lensMandates": {seat: keys},
            "checker": {"model": "fixture-model", "effort": "medium"}}
    done = subprocess.run(["node", ENGINE_RUN], capture_output=True, text=True,
                          input=json.dumps({"script": FANOUT, "args": args, "reply": reply}))
    if done.returncode != 0:
        raise SystemExit("node failed: " + done.stderr.strip())
    return json.loads(done.stdout)


# One seat return: a valid map with a `not-reached` key, one legal `capability` gap, zero findings.
A_CS = {"path": "src/a.cs", "lines": 40}
CAPABILITY_GAP = {"item": "src/scene.tscn", "reason": "capability"}
CAPABILITY_PARTIAL_RETURN = {"findings": [], "gaps": [CAPABILITY_GAP],
                   "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads", "basis": "read a.cs",
                               "mandates": {"a": "clean", "b": "not-reached"}}}


def _seat_return(findings=(), map=None, gaps=(CAPABILITY_GAP,), **checked):
    """A two-key seat return: one legal gap by default, `checked` fields overriding a valid provenance."""
    provenance = dict({"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads", "basis": "read a.cs"},
                      **checked)
    if map is not None:
        provenance["mandates"] = map
    return {"findings": list(findings), "gaps": list(gaps), "checked": provenance}


def _finding(agent):
    return {"agent": agent, "action": "FIX", "category": "bug", "file": "src/a.cs:1", "description": "defect",
            "rationale": "reason"}


def _replay():
    """The plan-check seat `plc-doctrine-consistency`'s spilled return: 14 findings, three free-text gaps and a
    one-key map. It never read the row's must-read entry, so both routes fail the seat; its free-text gaps are notes."""
    text = open(os.path.join(HERE, "fixtures", "plan_check_doctrine_consistency_replay.spill.md"),
                encoding="utf-8").read()
    return review_ledger._import("review_result").text_body(text)


MAP = {"a": "clean", "b": "findings"}
REPLAY = "plc-doctrine-consistency"
# name, seat return, its keys, the outcomes both routes must give, and the seat's must-read list when the row is
# about one; otherwise the list is src/a.cs, which every return reads whole, plus each item a typed gap names. Rows 2
# to 6 are the negative controls of review_fanout_lossless_test.js.
MUST = [A_CS, {"path": "src/scene.tscn", "lines": 12}]
IMPACT = [{"path": "src/Core/ImpactPhysics.cs", "lines": 900}]
ROUTE_ROWS = [
    ("capability-partial return keeps its map", CAPABILITY_PARTIAL_RETURN, ["a", "b"], {"a": "clean", "b": "not-reached"}),
    ("rejected entry covers nothing", _seat_return([_finding("b"), None], MAP), ["a", "b"],
     {"a": "incomplete", "b": "incomplete"}),
    ("invalid checked covers nothing", _seat_return([_finding("b")], MAP, basis=""), ["a", "b"],
     {"a": "incomplete", "b": "incomplete"}),
    ("stoppedAt blocked covers nothing", _seat_return([_finding("b")], MAP, stoppedAt="blocked"), ["a", "b"],
     {"a": "incomplete", "b": "incomplete"}),
    ("stoppedAt blocked whose map names the block keeps its other tokens",
     _seat_return([_finding("b")], {"a": "not-reached", "b": "findings"}, gaps=(), stoppedAt="blocked"), ["a", "b"],
     {"a": "blocked", "b": "findings"}),
    ("stoppedAt blocked naming the block with a rejected entry covers nothing",
     _seat_return([_finding("b"), None], {"a": "not-reached", "b": "findings"}, gaps=(), stoppedAt="blocked"),
     ["a", "b"], {"a": "blocked", "b": "incomplete"}),
    ("no map reaches nothing", _seat_return(), ["a", "b"], {"a": "not-reached", "b": "not-reached"}),
    ("a finding whose agent names the lens keeps the map", _seat_return([_finding("seat")], MAP), ["a", "b"], MAP),
    ("a no-gap finding whose agent names the lens keeps the map", _seat_return([_finding("seat")], MAP, gaps=()),
     ["a", "b"], MAP),
    ("plan-check replay's unread must-read entry fails the seat", _replay(), [REPLAY], {REPLAY: "failed"}),
    ("a free-text gap on a seat that read its list is a note", _seat_return(map=MAP, gaps=("did not read the generated scene",)),
     ["a", "b"], MAP),
    ("an unread must-read entry with no legal gap fails the seat", _seat_return(map=MAP, gaps=()), ["a", "b"],
     {"a": "failed", "b": "failed"}, MUST),
    ("an outside-lens gap on the unread entry keeps the seat complete",
     _seat_return(map=MAP, gaps=({"item": "src/scene.tscn", "reason": "outside-lens"},)), ["a", "b"], MAP, MUST),
    ("a capability gap on the unread entry keeps the seat's map", _seat_return(map=MAP), ["a", "b"], MAP, MUST),
    ("no map, one key, a finding", _seat_return([_finding("a")], gaps=()), ["a"], {"a": "findings"}),
    ("no map, one key, clean", _seat_return(gaps=()), ["a"], {"a": "clean"}),
    ("a must-read entry read only through line 437 fails the seat",
     _seat_return(map=MAP, gaps=(), toolsUsed=["Read:src/Core/ImpactPhysics.cs:1-437"]), ["a", "b"],
     {"a": "failed", "b": "failed"}, IMPACT),
    ("Read ranges that together cover a must-read entry keep the map",
     _seat_return(map=MAP, gaps=(), toolsUsed=["Read:src/Core/ImpactPhysics.cs:438-900",
                                               "Read:src/Core/ImpactPhysics.cs:1-437"]), ["a", "b"], MAP, IMPACT),
]


def default_must_read(reply):
    """src/a.cs plus each item a typed gap of `reply` names, for a row whose subject is not the must-read list."""
    gaps = reply.get("gaps") if isinstance(reply, dict) and isinstance(reply.get("gaps"), list) else []
    return [A_CS] + [{"path": gap["item"], "lines": 12} for gap in gaps
                     if isinstance(gap, dict) and isinstance(gap.get("item"), str)]


def route_outcomes(reply, keys, must_read):
    """((engine perAgent mandates, kept findings), (sidecar mandates, kept findings)) for one seat return fed
    through each route."""
    engine = engine_return(reply, "seat", keys, must_read)
    review_result = review_ledger._import("review_result")
    prepared = review_result.ingest([{"label": "seat", "mandates": keys, "payload": {"structured_output": reply},
                                      "record": {"exitCode": 0, "schemaValid": True, "engagement": "proven"},
                                      "mustRead": must_read}])
    sidecar = review_ledger._sidecar_seats(prepared, {key: {} for key in keys})[0]["mandates"]
    return (engine["perAgent"][0]["mandates"], len(engine["sources"])), (sidecar, len(prepared["sources"]))


def main():
    js, py = js_outcomes(), py_outcomes()
    bad = 0
    for row, got_js, got_py in zip(ROWS, js, py):
        ok = got_js == got_py
        bad += not ok
        print("%-4s %s" % ("ok" if ok else "FAIL", row[0]) + ("" if ok else "\n       js=%s py=%s" % (got_js, got_py)))
    print("\n%d/%d rows agree" % (len(ROWS) - bad, len(ROWS)))
    for name, reply, keys, expected, *must_read in ROUTE_ROWS:
        engine, sidecar = route_outcomes(reply, keys, must_read[0] if must_read else default_must_read(reply))
        ok = engine[0] == sidecar[0] == expected and engine[1] == sidecar[1]
        bad += not ok
        print("%-4s both routes: %s" % ("ok" if ok else "FAIL", name)
              + ("" if ok else "\n       engine=%s sidecar=%s expected=%s" % (engine, sidecar, expected)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
