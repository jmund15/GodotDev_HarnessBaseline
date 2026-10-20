"""Re-runnable proof for tools/eval_render.py: the report script parses and draws under a stub DOM.

The report's <script> block is extracted and (1) passes `node --check`, (2) runs against a stub DOM
that loads the embedded data and records which panels and KPI cards were drawn, so a syntax error or a
missing element fails here instead of showing a blank report. Node is required.

    python3 .claude/tests/test_eval_render.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
import eval_history  # noqa: E402
import eval_render  # noqa: E402

STUB = r"""
const fs = require('fs'), vm = require('vm');
const [scriptPath, dataPath, actionsPath] = process.argv.slice(2);
const listeners = {};
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.className = '';
    this._html = ''; this.textContent = ''; const set = new Set();
    this.classList = { toggle: (c, on) => { on ? set.add(c) : set.delete(c); }, contains: c => set.has(c) }; }
  appendChild(c) { this.children.push(c); return c; }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
}
const byId = {};
for (const id of ['data', 'tip', 'nav', 'main', 'kpis', 'hdr']) byId[id] = new El('x');
byId.data.textContent = fs.readFileSync(dataPath, 'utf8');
globalThis.document = { getElementById: id => byId[id], createElement: t => new El(t),
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); } };
globalThis.location = { hash: '' };
globalThis.innerWidth = 1200;
const script = fs.readFileSync(scriptPath, 'utf8');
vm.runInThisContext(script + '\n;globalThis.__panels = panels;');
const panels = globalThis.__panels;
const htmlOf = el => el._html + el.children.map(htmlOf).join('');
const selectCards = panel => panel ? panel.children.filter(c => c.children.some(k => k.tag === 'select')) : [];
const pick = (card, index, value) => { card.children.filter(k => k.tag === 'select')[index].value = value;
  card.children.filter(k => k.tag === 'select')[index].onchange(); return card.children.find(k => k.tag === 'div').innerHTML; };
const sessionCards = selectCards(panels.sessions), logCards = selectCards(panels.log);
const html0 = Object.fromEntries(Object.entries(panels).map(([k, p]) => [k, htmlOf(p)]));
const driven = {};
if (sessionCards.length >= 2) {
  driven.domainsAll = sessionCards[0].children.find(k => k.tag === 'div').innerHTML;
  driven.domainsHi = pick(sessionCards[0], 0, '3');
  driven.skillsLoads = sessionCards[1].children.find(k => k.tag === 'div').innerHTML;
  driven.skillsName = pick(sessionCards[1], 0, 'name');
}
if (logCards.length >= 1) {
  driven.logA = pick(logCards[0], 0, 'A');
  driven.logAll = pick(logCards[0], 0, '');
  driven.logFail = pick(logCards[0], 1, 'failure');
}
const trend = panels.trend;
const cmpEl = trend && trend.children.find(c => c.children.some(k => k.tag === 'select'));
let cmpHtml = null;
if (cmpEl) { const out = cmpEl.children.find(k => k.tag === 'div'); cmpEl.children.find(k => k.tag === 'select').onchange(); cmpHtml = out.innerHTML; }
const outOf = card => card.children.find(k => k.tag === 'div').innerHTML;
const actions = actionsPath ? JSON.parse(fs.readFileSync(actionsPath, 'utf8')) : [];
const acted = actions.map(a => {
  if (a.op === 'click') {
    const ev = { prevented: false, preventDefault() { this.prevented = true; },
      target: { closest: sel => (sel === 'a[data-tab]' && a.tab ? { dataset: { tab: a.tab } } : null) } };
    (listeners.click || []).forEach(fn => fn(ev));
    return { active: Object.entries(panels).filter(([, p]) => p.classList.contains('on')).map(([k]) => k),
      hash: globalThis.location.hash, prevented: ev.prevented };
  }
  if (a.op === 'tip') {
    const ev = { clientX: 100, clientY: 50,
      target: { closest: sel => (sel === '[data-tip]' && a.tip != null ? { dataset: { tip: a.tip } } : null) } };
    (listeners.mousemove || []).forEach(fn => fn(ev));
    return { display: byId.tip.style.display, html: byId.tip.innerHTML };
  }
  if (a.op === 'cmp') {
    const card = panels.trend.children.find(c => c.children.some(k => k.tag === 'select'));
    const selects = card.children.filter(k => k.tag === 'select');
    selects[0].value = a.a; selects[1].value = a.b; selects[0].onchange();
    return outOf(card);
  }
  if (a.op === 'values') {
    const card = panels.trend.children.find(c => c.children.some(k => k.tag === 'select'));
    return card.children.filter(k => k.tag === 'select').map(k => k.value);
  }
  if (a.op === 'options') {
    const card = panels.trend.children.find(c => c.children.some(k => k.tag === 'select'));
    return card.children.filter(k => k.tag === 'select')[0].innerHTML;
  }
  if (a.op === 'pick') {
    const card = selectCards(panels[a.panel])[a.card];
    card.children.filter(k => k.tag === 'select').forEach(k => { k.value = k.title === 'sort' ? 'newest' : ''; });
    return pick(card, a.select, a.value);
  }
  return null;
});
console.log(JSON.stringify({
  acted,
  cmpHtml,
  driven,
  panels: Object.keys(panels), panelHtml: Object.fromEntries(Object.entries(panels).map(([k, p]) => [k, htmlOf(p).length])),
  html: html0,
  tabs: byId.nav.children.map(b => b.textContent), kpis: byId.kpis.children.map(k => k.innerHTML),
  hdr: byId.hdr.textContent, active: Object.entries(panels).filter(([, p]) => p.classList.contains('on')).map(([k]) => k),
}));
"""

SESSIONS = {"n": 8, "clean": 5, "correction": 2, "failure": 1, "unknown": 0,
            "patterns": {"A": 2, "B": 0, "C": 5, "D": 0, "E": 0, "none": 1},
            "causes": {"A": 2, "B": 0, "C": 0, "D": 0, "E": 0, "none": 1},
            "corrections": 12, "prompts": 150, "facts_rows": 6,
            "domains": {"proof-domain": {"n": 4, "clean": 3}}, "skills": {"proof-skill": {"n": 3, "clean": 1}},
            "drivers": {"m-one / medium": {"n": 2, "clean": 2, "correction": 0, "failure": 0, "unknown": 0,
                                           "corrections": 5, "prompts": 40, "facts_rows": 2},
                        "m-two / high": {"n": 1, "clean": 0, "correction": 0, "failure": 1, "unknown": 0,
                                         "corrections": 3, "prompts": 20, "facts_rows": 1}},
            "duration": {"lt300": {"n": 3, "clean": 2, "corrections": 4, "prompts": 16, "facts_rows": 1},
                         "300_700": {"n": 1, "clean": 0},
                         "gt700": {"n": 0, "clean": 0}, "unknown": 4}}
STATS = {"total_sessions": 8, "total_clean": 5, "total_correction": 2, "current_streak": 1,
         "recent_window": {"population": 8, "known": 8, "unknown": 0, "clean": 5, "clean_pct": 62.5},
         "timeline": [{"date": "2026-09-01", "outcome": "clean", "count": 3}, {"date": "2026-09-02", "outcome": "correction",
                                                                             "count": 1},
                      {"date": "2026-09-08", "outcome": "clean", "count": 2}],
         "domain_perf": [{"domain": "proof-domain", "total": 4, "known": 4, "unknown": 0, "clean": 3, "correction": 1,
                          "failure": 0, "clean_pct": 75.0, "corrections": 3, "prompts": 40, "facts_rows": 2},
                         {"domain": "thin-domain", "total": 2, "known": 2, "unknown": 0, "clean": 0, "correction": 2,
                          "failure": 0, "clean_pct": 0.0, "corrections": 0, "prompts": 0, "facts_rows": 0}],
         "skill_perf": [{"skill": "proof-skill", "total": 3, "known": 3, "unknown": 0, "clean": 1, "correction": 2,
                         "failure": 0, "clean_pct": 33.3, "corrections": 6, "prompts": 60, "facts_rows": 3},
                        {"skill": "zeta-skill", "total": 9, "known": 9, "unknown": 0, "clean": 8, "correction": 1,
                         "failure": 0, "clean_pct": 88.9, "corrections": 0, "prompts": 0, "facts_rows": 0}],
         "rule_misses": [{"rule": ".claude/CLAUDE.md", "kind": "not-read", "sessions": 2,
                          "ids": ["golden-001", "golden-002"]},
                         {"rule": "rules/one-off.md", "kind": "not-followed", "sessions": 1, "ids": ["golden-003"]}],
         "skill_trends": [{"skill": "proof-skill", "recent_clean": 1, "recent_total": 6, "recent_population": 6,
                           "recent_pct": 16.7, "prior_clean": 4, "prior_total": 8, "prior_population": 8,
                           "prior_pct": 50.0, "direction": "DOWN", "low_n": False}],
         "all_corrections": [{"id": 7, "date": "2026-09-02", "outcome": "correction", "pattern": "A",
                              "title": "Proof correction seven", "n_corrections": 2, "key_takeaway": "read the source first"},
                             {"id": 9, "date": "2026-09-05", "outcome": "failure", "pattern": "C",
                              "title": "Proof failure nine", "n_corrections": 1, "key_takeaway": "gate before merge"}],
         "top_memory_hits": [{"entity": "Proof_Gotchas", "count": 12}]}


def agent_cell(model, effort, *, wf=0, sc=0, unrated=0, other=0, qcost=0.0, qcost_n=0, usd=0.0, usd_n=0, **outcomes):
    cell = {"model": model, "effort": effort, "wf": wf, "sc": sc, "n": wf + sc, "unrated": unrated, "other": other,
            "qcost": qcost, "qcost_n": qcost_n, "usd": usd, "usd_n": usd_n}
    cell.update({name: outcomes.get(name, 0) for name in eval_history.OUTCOMES})
    return cell


COVERAGE = {"rated": 6, "unrated": 2, "sampled_out": 1, "absent": 0, "unparseable": 1}
AGENTS = {
    "rows": 10, "undated": 0, "first": "2026-09-01", "last": "2026-09-20", "runs": 3, "sampled_n": 1,
    "unrated_reasons": {"sidecar": 4, "panel-seat": 2, "session-never-rated": 1},
    "coverage": {"workflow": dict(COVERAGE, rated=4), "sidecar": {"rated": 2, "unrated": 0, "sampled_out": 0,
                                                                   "absent": 0, "unparseable": 0},
                 "total": COVERAGE},
    "cells": [
        agent_cell("m-one", "low", sc=2, clean=2, usd=2.0, usd_n=1),
        agent_cell("m-two", "high", sc=1, defects=1, usd=3.0, usd_n=1),
        agent_cell("proof-model", "medium", wf=2, sc=1, clean=2, defects=1, qcost=4000.0, qcost_n=2, usd=1.0, usd_n=1),
        agent_cell("proof-model", "high", wf=1, clean=1, qcost=9000.0, qcost_n=1)],
    "route_cells": [
        agent_cell("m-one", "low", sc=2, clean=2, usd=2.0, usd_n=1),
        agent_cell("m-two", "high", sc=1, defects=1, usd=3.0, usd_n=1),
        agent_cell("proof-model", "medium", wf=2, clean=1, defects=1, qcost=4000.0, qcost_n=2),
        agent_cell("proof-model", "medium", sc=1, clean=1, usd=1.0, usd_n=1),
        agent_cell("proof-model", "high", wf=1, clean=1, qcost=9000.0, qcost_n=1)],
    "totals": {"tasks": {}, "rows_without_task": 10, "rows_without_cost": 1, "rows": 10},
    "candidates": [{"family": "proof", "model": "proof-model", "effort": "high", "suggested": "medium",
                    "cost_ratio": 2.5, "n_hi": 2, "n_lo": 2}],
}


def snapshot(month):
    return {"month": month, "closed": False, "backfilled": False, "sessions": SESSIONS, "attention": [],
            "sources": {"self_eval": {"rows": 8, "first": month + "-01", "last": month + "-20"},
                        "orchestration": {"rows": 10, "undated": 0, "first": month + "-01", "last": month + "-20"}},
            "agents": {"rows": 10, "rated": 6, "unrated": 2, "sampled_out": 1, "absent": 0, "unparseable": 1,
                       "cells": [agent_cell("m-one", "low", sc=3, clean=3, usd=2.0, usd_n=3),
                                 agent_cell("proof-model", "medium", wf=5, clean=4, defects=1, qcost=5000.0, qcost_n=5),
                                 agent_cell("m-bad", "high", wf=10, clean=7, defects=2, rework=1, qcost=9000.0, qcost_n=10)],
                       "paid": {"usd": 6.5, "n": 134}},
            "verdict": None}


def cell(model, tier, verdict, proposal=(), **extra):
    return dict({"model": model, "label": model.title(), "tier": tier, "registered": True, "claimed": True,
                 "falsified": [], "n": 9, "sessions": 3, "clean": 9, "defects": 0, "rework": 0, "discarded": 0,
                 "mismatch": 0, "qcost": 18000.0, "qcost_n": 9, "usd": 0.0, "usd_n": 0, "verdict": verdict,
                 "proposal": list(proposal), "notes": []}, **extra)


ADVISORY = ('ADVISORY: proof-c has clean rated rows at architect without claiming it; consider "roles": '
            '["architect", "executor"] in external_models.json roles (apply through /ladder_ingest).')
LADDER = {"cells": [cell("proof-a", "executor", "holds"), cell("proof-c", "architect", "candidate", [ADVISORY], mismatch=4),
                    cell("proof-d", "fanout", "insufficient", n=3, sessions=1, clean=3)],
          "buckets": {"unrated": 2, "sampled_out": 1, "no_outcome": 1, "outcome_unparseable": 1, "tier_absent": 40,
                      "tier_unparseable": 2, "requested_only": 3, "mixed": 1, "members": 21},
          "total": 71, "constants": {"MIN_ROWS": 8, "MIN_SESSIONS": 3, "CONTRADICT_MIN_FALSIFIED": 2}}


def months_of():
    prior = snapshot("2026-08")
    prior["sessions"] = dict(SESSIONS, clean=2, correction=5)
    return {"2026-08": dict(prior, closed=True), "2026-09": snapshot("2026-09")}


INPUTS = [
    {"source": "self-evaluate archive", "path": ".claude/self_evaluate_archive.json", "rows": 8, "first": "2026-09-01",
     "last": "2026-09-20", "age_days": 8, "status": "ok"},
    {"source": "orchestration ledger", "path": ".claude/orchestration_metrics.jsonl", "rows": 10, "first": "2026-09-01",
     "last": "2026-09-20", "age_days": 8, "status": "ok"},
    {"source": "routing audit", "path": ".claude/logs/routing_audit_stats.json", "rows": 40, "first": None,
     "last": "2026-09-10", "age_days": 18, "status": "stale"},
    {"source": "rules feed", "path": ".claude/scratch/eval_out/rule_retirement.json", "rows": 0, "first": None,
     "last": None, "age_days": None, "status": "absent"},
    {"source": "history", "path": ".claude/eval_history.json", "rows": 2, "first": "2026-08", "last": "2026-09",
     "age_days": None, "status": "unparseable"},
]
RULES = {"scanned": 30, "rules": 4, "live": [], "fired": [{"path": "rules/proof.md", "source": "file",
                                                          "trigger": "review-by: 2026-09-01", "kind": "review-by",
                                                          "reason": "review date 2026-09-01 reached"}],
         "undecidable": [{"path": "rules/u.md"}, {"path": "rules/v.md"}], "malformed": [{"path": "rules/m.md"}]}
DENIALS_INPUT = {"source": "denials scan", "path": "session transcripts", "rows": 1, "first": None, "last": None, "age_days": None,
     "status": "ok", "detail": "1 root(s) scanned, 1 file(s) read; missing root(s): /x/.claude/projects"}
ROUTING = {"generated_at": "2026-09-27T10:00:00", "total_entries": 40, "silent_misses": 5, "active_window_days": 30,
           "by_week": {"2026-W37": {"silent_miss": 2}, "2026-W38": {"silent_miss": 3}},
           "top_5_silent_miss_rules": [{"rule": "proof-rule", "count": 3, "trend_vs_prior_4wk_avg": "+12%"}]}
VERDICT = {"month": "2026-09", "state": "Proof state sentence.",
           "moves": [{"do": "Raise the floor", "why": "two defects", "see": "agents"},
                     {"do": "Read the ladder", "why": "one candidate", "see": "ladder"}],
           "watch": ["watch one"], "milestone": "milestone one",
           "profile": {"strengths": ["strong proof"], "weaknesses": ["weak proof"]}}
STORED = [{"signal": "ladder proposal", "evidence": "proof-c at architect: candidate (n 9, 3 sessions)", "tab": "ladder"},
          {"signal": "skill drift", "evidence": "proof-skill: clean 50% to 17%", "tab": "sessions"}]


def facts(ladder=LADDER, full=True):
    out = {"today": "2026-09-28", "month": "2026-09", "stats": STATS, "months": months_of(),
           "agents": AGENTS, "ladder": ladder}
    if full:
        out["months"]["2026-09"]["attention"] = STORED
        out.update(inputs=INPUTS, rules_feed=RULES, routing=ROUTING)
    return out


def view_of(fx):
    return eval_history.compare(fx["months"], fx["today"])


GREP_KEY, SLIP_KEY = "friction.recursive-grep.sessions", "agents.proof-slip"
FINDINGS = {
    "metrics": {GREP_KEY: {"key": GREP_KEY, "label": GREP_KEY, "num": 3, "den": 8, "unit": "sessions"},
                SLIP_KEY: {"key": SLIP_KEY, "label": SLIP_KEY, "num": 0, "den": 0, "unit": "agents"}},
    "findings": {
        GREP_KEY: {"id": GREP_KEY, "kind": "friction", "metric": GREP_KEY, "num": 3, "den": 8,
                   "examples": ["<script>alert(1)</script>", {"run": "wf_9", "label": "proof run nine"}],
                   "ledger": [{"date": "2026-09-05", "outcome": "declined", "reason": "not a recurrence", "metric": GREP_KEY}]},
        SLIP_KEY: {"id": SLIP_KEY, "kind": "agents", "metric": SLIP_KEY, "num": 0, "den": 0, "examples": [],
                   "ledger": [{"date": "2026-09-10", "outcome": "applied", "reason": "proof", "fix": "tool",
                               "metric": SLIP_KEY}]},
        "cascade.unfixed": {"id": "cascade.unfixed", "kind": "cascade", "metric": "cascade.unfixed", "num": 1, "den": 2,
                            "examples": ["proof-session"], "ledger": []},
        "cascade.abc12345": {"id": "cascade.abc12345", "kind": "cascade-session", "metric": "cascade.unfixed", "num": 1, "den": 1,
                             "examples": [], "ledger": [], "date": "2026-09-12", "title": "Proof cascade",
                             "status": "not-landed", "shas": ["abc1234", "def5678"]},
    },
    "unclassified": {"friction": {"num": 2, "den": 9}}, "coverage": {"friction": {"recursive-grep": 4}},
    "diagnostics": {},
}
EFFECTS = [
    {"row": {"date": "2026-09-10", "outcome": "applied", "metric": SLIP_KEY}, "key": SLIP_KEY, "status": "measured",
     "baseline": [2, 10], "after": [0, 12], "delta": -20.0, "moved": "improved", "detail": ""},
    {"row": {"date": "2026-09-20", "outcome": "applied", "metric": GREP_KEY}, "key": GREP_KEY, "status": "waiting",
     "baseline": [3, 8], "after": [0, 1], "delta": None, "moved": None, "detail": "waiting (1 of 5)"},
]


def findings_facts(findings=FINDINGS, effects=EFFECTS):
    return dict(facts(), findings=findings, effects=effects)


MAX_VERDICT = {"month": "2026-09", "state": "S" * 300,
               "moves": [{"do": "Do %d " % i + "d" * 70, "why": "w" * 150, "see": "findings",
                          "finding": ["agents.partial-coverage.claude-sonnet-5-5"], "metric": "agents.partial-coverage.claude-sonnet-5-5"}
                         for i in range(5)],
               "watch": ["watch %d " % i + "x" * 90 for i in range(3)], "milestone": "m" * 150,
               "profile": {"strengths": ["s"] * 5, "weaknesses": ["w"] * 5}}


def maximal_facts():
    """24 months of history, 60 skills, 30 domains, 12 ladder cells and a verdict, attention and inputs at their caps."""
    months = {}
    for index in range(24):
        month = "%04d-%02d" % (2024 + (9 + index) // 12, (9 + index) % 12 + 1)
        skills = {"skill-%02d" % i: {"n": 5, "clean": 2} for i in range(60)}
        domains = {"domain-%02d" % i: {"n": 5, "clean": 2} for i in range(30)}
        snap = snapshot(month)
        snap["sessions"] = dict(SESSIONS, skills=skills, domains=domains)
        months[month] = dict(snap, closed=month < "2026-09")
    cells = [cell("proof-%02d" % i, "executor", "candidate", [ADVISORY], n=9) for i in range(12)]
    ladder = dict(LADDER, cells=cells)
    stored = [{"signal": "ladder proposal", "evidence": "proof-%02d at executor: candidate (n 9, 3 sessions) " % i + "e" * 60,
               "tab": "ladder"} for i in range(20)]
    months["2026-09"]["attention"] = stored
    months["2026-09"]["agents"] = dict(months["2026-09"]["agents"], cells=[
        agent_cell("proof-model-%d" % i, "high", wf=12, clean=8, defects=3, rework=1) for i in range(8)])
    return {"today": "2026-09-28", "month": "2026-09", "stats": STATS, "months": months, "agents": AGENTS,
            "ladder": ladder, "inputs": INPUTS, "rules_feed": RULES, "routing": ROUTING,
            "history": {"status": "unparseable", "detail": "d" * 120}, "findings": FINDINGS, "effects": EFFECTS}


DUR = {"lt300": {"n": 6, "clean": 3}, "300_700": {"n": 7, "clean": 4}, "gt700": {"n": 6, "clean": 2}, "unknown": 1}
DUR_LOW = dict(DUR, gt700={"n": 5, "clean": 2}, unknown=2)
NO_DUR = {"lt300": {"n": 0, "clean": 0}, "300_700": {"n": 0, "clean": 0}, "gt700": {"n": 0, "clean": 0}}


def month_of(month, *, n, clean, closed=True, backfilled=False, qcost=9000.0, usd=2.0, duration=None):
    """A month with `n` sessions, `clean` of them clean, and nine agents priced at `qcost` in total."""
    snap = snapshot(month)
    duration = duration or dict(NO_DUR, unknown=n)
    snap.update(closed=closed, backfilled=backfilled,
                sessions=dict(SESSIONS, n=n, clean=clean, correction=n - clean, failure=0, unknown=0, duration=duration))
    snap["agents"] = {"rows": 9, "rated": 9, "unrated": 0, "sampled_out": 0, "absent": 0, "unparseable": 0,
                      "cells": [agent_cell("m-one", "low", sc=9, clean=9, usd=usd, usd_n=9),
                                agent_cell("proof-model", "medium", wf=9, clean=9, qcost=qcost, qcost_n=9)],
                      "paid": {"usd": 0.0, "n": 0}}
    return snap


def rich_months(duration=DUR):
    older = month_of("2026-03", n=10, clean=8)
    older["sessions"]["patterns"] = {key: count for key, count in older["sessions"]["patterns"].items() if key != "E"}
    return {"2026-02": month_of("2026-02", n=10, clean=6), "2026-03": older,
            "2026-04": month_of("2026-04", n=5, clean=3), "2026-05": month_of("2026-05", n=20, clean=10),
            "2026-06": month_of("2026-06", n=20, clean=12), "2026-07": month_of("2026-07", n=6, clean=5),
            "2026-08": month_of("2026-08", n=6, clean=6, backfilled=True),
            "2026-09": month_of("2026-09", n=20, clean=12, closed=False, qcost=13500.0, usd=3.0, duration=duration)}


RICH_STATS = dict(
    STATS,
    timeline=[{"date": "2026-09-01", "outcome": "clean", "count": 3}, {"date": "2026-09-02", "outcome": "correction", "count": 2},
              {"date": "2026-09-08", "outcome": "clean", "count": 4}, {"date": "2026-09-09", "outcome": "correction", "count": 2},
              {"date": "2026-09-15", "outcome": "clean", "count": 7}],
    all_corrections=[
        {"id": 7, "date": "2026-09-02", "outcome": "correction", "pattern": "A", "title": "Row seven", "n_corrections": 2,
         "domains": ["meta", "godot"], "key_takeaway": "k7"},
        {"id": 9, "date": "2026-09-05", "outcome": "failure", "pattern": "C", "title": "Row nine", "n_corrections": 1,
         "domains": [], "key_takeaway": "k9"},
        {"id": 11, "date": "2026-09-07", "outcome": "correction", "pattern": "B", "title": "Row eleven", "n_corrections": 3,
         "domains": ["meta"], "key_takeaway": "k11"},
        {"id": 5, "date": None, "outcome": "correction", "pattern": None, "title": "Row five", "n_corrections": 0,
         "domains": ["odd"], "key_takeaway": None}],
    skill_trends=[STATS["skill_trends"][0], dict(STATS["skill_trends"][0], skill="zeta-skill", direction="UP", low_n=True)],
    domain_perf=STATS["domain_perf"] + [
        {"domain": "alpha-domain", "total": 4, "known": 4, "unknown": 0, "clean": 2, "correction": 2, "failure": 0, "clean_pct": 50.0},
        {"domain": "nullish", "total": 2, "known": 0, "unknown": 2, "clean": 0, "correction": 0, "failure": 0, "clean_pct": None},
        {"domain": "edge-domain", "total": 3, "known": 3, "unknown": 0, "clean": 1, "correction": 2, "failure": 0, "clean_pct": 33.3}],
    skill_perf=STATS["skill_perf"] + [
        {"skill": "nullish-skill", "total": 3, "known": 0, "unknown": 3, "clean": 0, "correction": 0, "failure": 0, "clean_pct": None}],
    top_memory_hits=[{"entity": "mem-%02d" % i, "count": 100 - i} for i in range(60)])
RICH_ROUTING = dict(ROUTING, by_week={"2026-W33": {"silent_miss": 1}, "2026-W34": {"silent_miss": 2},
                                       "2026-W35": {"silent_miss": 3}, "2026-W36": {"silent_miss": 4},
                                       "2026-W37": {"silent_miss": 5}, "2026-W38": {}})


RICH_LADDER = dict(LADDER, cells=LADDER["cells"] + [
    cell("proof-x", "executor", "contradicts", ["ADVISORY: raise the pinned effort"], label="proof-x", defects=2, clean=7,
         falsified=["wf_1:a", "wf_2:b"])])


RICH_AGENTS = AGENTS


def rich_facts(duration=DUR, ladder=RICH_LADDER):
    return {"today": "2026-09-28", "month": "2026-09", "stats": RICH_STATS, "months": rich_months(duration),
            "agents": RICH_AGENTS, "ladder": ladder, "inputs": INPUTS, "rules_feed": RULES, "routing": RICH_ROUTING}


def stub_html(html_text, actions=None):
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html_text, re.S).group(1)
    proc = run_stub(script_of(html_text), blob.replace("<\\/", "</"), actions)
    return proc, (json.loads(proc.stdout) if proc.returncode == 0 else {})


def change_cell(html_text, label):
    """The last cell of the compare-table row that starts with `label`."""
    start = html_text.index("<td>%s</td>" % label)
    row = html_text[start:html_text.index("</tr>", start)]
    return row[row.rindex("<td>") + 4:row.rindex("</td>")]


def chart_card(html_text, title):
    start = html_text.index("<h2>%s</h2>" % title)
    return html_text[start:html_text.index("</svg>", start)]


def table_row(html_text, first_cell):
    return next(row for row in re.findall(r"<tr>.*?</tr>", html_text, re.S) if row.startswith("<tr><td>%s</td>" % first_cell))


def table_rows(html_text, width=12):
    """The (model, effort) table's rows as cell texts: tags dropped, the note span kept as text. Other tables have
    another width."""
    rows = []
    for row in re.findall(r"<tr>.*?</tr>", html_text, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) == width:
            rows.append(tuple(re.sub(r"<[^>]+>", "", cell.replace(' <span class="note">', " ")) for cell in cells))
    return rows


def route_toggle_ok(acted, opened, merged_rows):
    """The report opens on the merged rows, the split view lists the route rows, and the merged view returns."""
    split, merged = acted.get("split") or "", acted.get("merged") or ""
    split_rows = table_rows(split)
    both = [row for row in split_rows if row[:2] == ("proof-model", "medium")]
    return (table_rows(opened) == merged_rows and table_rows(merged) == merged_rows
            and len(split_rows) == len(merged_rows) + 1
            and [row[2] for row in both] == ["wf 2", "sc 1"])


def script_of(html):
    return re.findall(r"<script>\n(.*?)\n</script>", html, re.S)[-1]


def run_stub(script, data, actions=None):
    with tempfile.TemporaryDirectory(prefix="evalrender_") as work:
        paths = [os.path.join(work, name) for name in ("stub.js", "script.js", "data.json", "actions.json")]
        for path, text in zip(paths, (STUB, script, data, json.dumps(actions or []))):
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        return subprocess.run(["node", *paths], capture_output=True, text=True, timeout=60)


def node_check(script):
    with tempfile.TemporaryDirectory(prefix="evalrender_") as work:
        path = os.path.join(work, "report.js")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        return subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)


def main():
    failures, checks = [], []

    def check(ok, label, detail=""):
        checks.append(label)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    fx = facts()
    view = view_of(fx)
    html = eval_render.report(fx, fx["months"], view, None)
    script = script_of(html)
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)

    checked = node_check(script)
    check(checked.returncode == 0, "the report script passes node --check", checked.stderr[-300:])
    broken = node_check(script + "\nfunction (")
    check(broken.returncode != 0, "node --check fails on a planted syntax error (the check can fire)")

    ran = run_stub(script, data.replace("<\\/", "</"))
    check(ran.returncode == 0, "the report script runs under the stub DOM", (ran.stderr or ran.stdout)[-400:])
    out = json.loads(ran.stdout) if ran.returncode == 0 else {}
    check(out.get("panels") == ["overview", "trend", "sessions", "agents", "ladder", "log", "inputs", "review", "findings"]
          and out.get("tabs") == ["Overview", "Trend", "Sessions", "Agents", "Ladder", "Log", "Inputs", "Review", "Findings"]
          and out.get("active") == ["overview"] and all(n > 0 for n in out.get("panelHtml", {}).values()),
          "all nine tab panels are built with content, Overview active", repr(out.get("panels")))
    trend_html = out.get("html", {}).get("trend", "")
    check(trend_html.count("<svg") == 12 and "Weekly clean rate" in trend_html and "Sessions per month" in trend_html
          and "2026-W36" in trend_html and 'fill="var(--surface)"' in trend_html,
          "the Trend tab draws 10 monthly small multiples (one per metric), the weekly line, and hollow points below min N",
          "%d svgs" % trend_html.count("<svg"))
    compare_html = out.get("cmpHtml") or ""
    check(all(eval_render.change_text(row) in compare_html for row in view["rows"]) and "+38pp better" in compare_html,
          "the Trend compare table's markers equal compare()'s for the same two months",
          compare_html[-300:])
    ladder_html = out.get("html", {}).get("ladder", "")
    check(all(text in ladder_html for text in ("proof-a", "holds", "candidate", "insufficient", "ADVISORY",
                                               "/ladder_ingest", "MIN_ROWS", "tier absent")),
          "the Ladder tab draws every cell, the ADVISORY line, the bucket footer and the constants", ladder_html[-300:])
    check(ladder_html.index("candidate") < ladder_html.index("holds") < ladder_html.index("insufficient"),
          "the Ladder tab orders cells by verdict priority: proposals first, insufficient last")
    empty_fx = facts({"cells": [], "buckets": LADDER["buckets"], "total": 71, "constants": LADDER["constants"]})
    empty = eval_render.report(empty_fx, empty_fx["months"], view_of(empty_fx), None)
    ran_empty = run_stub(script_of(empty), re.search(r'<script id="data" type="application/json">(.*?)</script>',
                                                      empty, re.S).group(1).replace("<\\/", "</"))
    empty_html = json.loads(ran_empty.stdout)["html"]["ladder"] if ran_empty.returncode == 0 else ""
    check("no cell yet" in empty_html and "tier absent" in empty_html,
          "the Ladder tab with no cell says so and still shows the buckets", empty_html[-200:])
    text = eval_render.note(fx, view, None, "file:///x")
    check("Ladder readout: 3 cells, 1 proposal" in text and "| proof-c · architect | 9 / 3 | candidate |" in text
          and text.index("proof-c · architect") < text.index("proof-a · executor") < text.index("proof-d · fanout")
          and "ADVISORY" in text,
          "the note callout counts cells and proposals and lists cells by verdict priority", text[-600:])
    none_text = eval_render.note(empty_fx, view_of(empty_fx), None, "file:///x")
    check("Ladder readout: 0 cells, 0 proposals" in none_text and "tier absent 40 of 71" in none_text,
          "the note callout with no cell names how many rows carry no tier")
    agents_html = out.get("html", {}).get("agents", "")
    check(all(text in agents_html for text in ("proof-model", "m-one", "m-two", "delegations only", "Model by effort")),
          "the Agents tab draws one table of served model by requested effort")
    check("Sidecar m-one" not in agents_html and "Effort by outcome" not in agents_html and "requested effort</th>" not in agents_html
          and "blend" in agents_html.lower() and "$5.00" not in agents_html,
          "the Agents tab holds no per-effort Workflow table, no per-sidecar-model table and no blended sidecar figure")
    check("proof-model" in agents_html and "2.5" in agents_html,
          "the Agents tab lists the over-pin candidates, labeled advisory", agents_html[-200:])
    check(out.get("panelHtml", {}).get("overview", 0) > 0, "the Overview panel has content")
    check(len(out.get("kpis", [])) == 5 and ("62%" in out["kpis"][0] or "63%" in out["kpis"][0])
          and "+38pp better" in out["kpis"][0] and "5 of 8" in out["kpis"][0]
          and view["rows"][0]["definition"] in out["kpis"][0].replace("&#39;", "'"),
          "the Overview draws five KPI cards, no pooled cost card; the first carries the clean rate, its count, its "
          "compare() marker and its definition", repr(out.get("kpis")))
    check("| Clean sessions (the owner made no correction) | 2 of 8 (25%) | 5 of 8 (62%) | +38pp better | ····▁█ |" in text
          and "| Sessions with a correction (the owner caught one or more issues) | 5 of 8 (62%) | 2 of 8 (25%) | -38pp |" in text
          and "| Failed sessions (a critical failure: data loss, repeated frustration) | 1 of 8 (12%) | 1 of 8 (12%) | +0pp flat |" in text
          and "| outcome | 2026-08 | 2026-09 (day 28) | change | share, last 6 months |" in text,
          "the note's sessions table gives each outcome with its meaning, its count and share in the prior month and this "
          "month, and the change", text[-2400:])
    check("| cause of a non-clean session | 2026-08 | 2026-09 (day 28) | change | share, last 6 months |" in text
          and "| A: rules existed, not followed | 2 of 6 (33%) | 2 of 3 (67%) | low N | ····▄· |" in text
          and "| B: docs missing | 0 of 6 (0%) | 0 of 3 (0%) | low N | ····▄· |" in text
          and "| D: tool and its docs disagree | 0 of 6 (0%) |" in text
          and "| E: tooling bug cascaded across sessions | 0 of 6 (0%) |" in text
          and "| No cause recorded | 4 of 6 (67%) | 1 of 3 (33%) | low N | ····▄· |" in text
          and "| C: " not in text and "Pattern" not in text,
          "the note splits the non-clean sessions by cause A, B, D and E in plain words, counts the rest as no cause "
          "recorded, and drops code C", text[-2400:])
    hidden = dict(fx, months=dict(fx["months"], **{"2026-09": dict(fx["months"]["2026-09"], sessions=dict(
        SESSIONS, causes={"A": 3, "B": 0, "C": 0, "D": 0, "E": 0, "none": 0})), "2026-08": dict(
        fx["months"]["2026-08"], sessions=dict(SESSIONS, clean=2, correction=5, causes={"A": 6, "B": 0, "C": 0, "D": 0,
                                                                                         "E": 0, "none": 0}))}))
    check("No cause recorded" not in eval_render.note(hidden, view_of(hidden), None, "file:///x"),
          "the note hides the no-cause row when no non-clean session in either month lacks a cause")
    check("`/self_evaluate` grades each session itself" in text and "The last 10 sessions are 62% clean." in text
          and "Change is in percentage points against the prior month: flat is a move of 10 points or less, low N is a month "
              "with fewer than 6 sessions. Bars are the shares of the last 6 months; a dot is a low N month." in text,
          "the note defines its session grading, its change words and its bars under the block")
    check("Bars are" not in eval_render.month_note(dict(fx["months"]["2026-09"], closed=True), fx["months"]["2026-08"])
          and "flat is a move of 10 points or less" in eval_render.month_note(dict(fx["months"]["2026-09"], closed=True),
                                                                              fx["months"]["2026-08"]),
          "a month note defines its change words and draws no bars")
    check("Clean sessions" in text and "backfilled" not in text.lower() and "Quota cost" not in text
          and "Sidecar USD" not in text and "Defect + rework" not in text,
          "the note prints no pooled cost row and no undefined label")
    check("- m-bad at high: 3 of 10 rated" in text and "proof-model at medium" not in text
          and "pay-per-token spend: $6.50 over 134 runs" in text
          and "% of this month's agents are unrated; the rows above read on the rated rest." in text
          and "### Dispatched agents, 2026-09" in text,
          "the note lists only the problem cells as `model at effort: k of n rated`, one unrated caveat line and one "
          "pay-per-token spend line", text[-2000:])
    no_paid = dict(fx, months=dict(fx["months"], **{"2026-09": dict(fx["months"]["2026-09"], agents=dict(
        fx["months"]["2026-09"]["agents"], paid={"usd": 0.0, "n": 0}, cells=[agent_cell("m", "low", wf=9, clean=9)]))}))
    quiet = eval_render.note(no_paid, view, None, "file:///x")
    check("pay-per-token" not in quiet and ("- none:" in quiet.lower() or "No model at any effort" in quiet),
          "a month with no pay-per-token run prints no spend line, and a month with no problem cell says so", quiet[-900:])
    fully_rated = dict(fx["months"]["2026-09"]["agents"], unrated=0)
    all_rated = eval_render.note(dict(fx, months=dict(fx["months"], **{"2026-09": dict(fx["months"]["2026-09"], agents=fully_rated)})),
                                 view, None, "file:///x")
    check("agents are unrated" not in all_rated, "a month with no unrated agent prints no unrated caveat")
    check("months: [[Eval Dashboard 2026-08]]" in text, "the note links the closed months")
    check("[[Eval Dashboard" not in eval_render.note(fx, eval_history.compare(
        {"2026-09": fx["months"]["2026-09"]}, fx["today"]), None, "file:///x"),
          "the note lists no month link before a month has closed")
    check("8 sessions" in out.get("hdr", "") and "2026-09-28" in out.get("hdr", ""), "the header names the run date and total",
          out.get("hdr", ""))

    def stubbed(html_text):
        blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html_text, re.S).group(1)
        ran_local = run_stub(script_of(html_text), blob.replace("<\\/", "</"))
        return ran_local, (json.loads(ran_local.stdout) if ran_local.returncode == 0 else {})

    sessions_html = out.get("html", {}).get("sessions", "")
    driven = out.get("driven", {})
    check(all(t in sessions_html for t in ("proof-domain", "thin-domain", "Outcomes per month", "Patterns per month",
                                            "proof-skill", "DOWN")),
          "the Sessions tab draws domain bars, the skill table with its trend, and the outcome and pattern bars",
          sessions_html[-200:])
    check("8 of 16 entries carry a duration" in sessions_html and "low N" in sessions_html
          and "under 300" in sessions_html,
          "the Sessions duration buckets print 'n of N' and the low-N note", sessions_html[-300:])
    check("thin-domain" in driven.get("domainsAll", "") and "thin-domain" not in driven.get("domainsHi", "")
          and "proof-domain" in driven.get("domainsHi", ""),
          "the domain filter 'n at least 3' drops a domain with 2 sessions")
    loads, by_name = driven.get("skillsLoads", ""), driven.get("skillsName", "")
    check("zeta-skill" in loads and "zeta-skill" in by_name
          and loads.index("zeta-skill") < loads.index("proof-skill")
          and by_name.index("proof-skill") < by_name.index("zeta-skill"),
          "the skill table sorts by loads by default and by name when asked")
    log_html = out.get("html", {}).get("log", "")
    check(all(t in log_html for t in ("Proof_Gotchas", "rules/proof.md", "review date 2026-09-01 reached",
                                      "2 undecidable", "1 malformed")),
          "the Log tab lists the top memory hits, the fired rules and the malformed and undecidable counts", log_html[-300:])
    check("Proof correction seven" in driven.get("logA", "") and "Proof failure nine" not in driven.get("logA", "")
          and "Proof failure nine" in driven.get("logFail", "") and "Proof correction seven" not in driven.get("logFail", "")
          and "read the source first" in driven.get("logAll", ""),
          "the corrections table filters by pattern and by outcome and shows the takeaway")
    inputs_html = out.get("html", {}).get("inputs", "")
    check(all(t in inputs_html for t in ("routing audit", "stale", "absent", "unparseable", "rules feed", "history",
                                          "self-evaluate archive"))
          and inputs_html.count("task-id coverage") == 1,
          "the Inputs tab has one row per producer with its status, and reports task-id coverage once", inputs_html[-300:])
    denials_fx = dict(facts(), inputs=INPUTS + [DENIALS_INPUT])
    _, denials_out = stubbed(eval_render.report(denials_fx, denials_fx["months"], view_of(denials_fx), None))
    denials_html = denials_out.get("html", {}).get("inputs", "")
    check("denials scan" in denials_html and "1 root(s) scanned, 1 file(s) read" in denials_html
          and "missing root(s): /x/.claude/projects" in denials_html,
          "test_inputs_tab_shows_denials_scan_roots_files_and_gaps", denials_html[-500:])
    check("Routing card" in inputs_html and "proof-rule" in inputs_html and "2026-W38" in inputs_html,
          "a routing_audit_stats.json in the facts draws the Routing card")
    bare_fx = dict(facts(), routing=None)
    ran_bare, bare_out = stubbed(eval_render.report(bare_fx, bare_fx["months"], view_of(bare_fx), None))
    bare_inputs = bare_out.get("html", {}).get("inputs", "?")
    check(ran_bare.returncode == 0 and "Routing card" not in bare_inputs and "proof-rule" not in bare_inputs,
          "with no routing stats the Inputs tab draws no Routing card", (ran_bare.stderr or bare_inputs)[-200:])
    overview = out.get("html", {}).get("overview", "")
    check("flat: a move of 10 points or less" in overview and "low N: a month has fewer sessions or agents" in overview
          and "rebuilt later: the last closed month was reconstructed" in overview,
          "the Overview defines its change words, the minimum N and rebuilt later", overview[-700:])
    check("Verdict not written for this run" in overview and "proof-c at architect" in overview
          and "skill drift" in overview and "rule past trigger" in overview and "input degraded" in overview,
          "the Overview shows 'Verdict not written' with no verdict, and every attention row, stored and computed",
          overview[-300:])
    ran_v, out_v = stubbed(eval_render.report(fx, fx["months"], view, VERDICT))
    over_v = out_v.get("html", {}).get("overview", "")
    check("Proof state sentence." in over_v and 'href="#agents"' in over_v and 'href="#ladder"' in over_v
          and "watch one" in over_v and "milestone one" in over_v and "strong proof" in over_v and "weak proof" in over_v,
          "the Overview verdict card shows the state, moves linking to their tabs, watch, milestone and the profile",
          (ran_v.stderr or over_v)[-300:])
    no_profile = {key: value for key, value in VERDICT.items() if key != "profile"}
    ran_p, out_p = stubbed(eval_render.report(fx, fx["months"], view, no_profile))
    check(ran_p.returncode == 0 and "Proof state sentence." in out_p["html"]["overview"]
          and "strong proof" not in out_p["html"]["overview"],
          "a verdict without a profile draws no profile card")
    stored_v = {"month": "2026-09", "state": "Stored verdict.", "moves": [{"do": "old move", "why": "old why", "see": "agents"}],
                "watch": []}
    stored_note = eval_render.note(fx, view, stored_v, "file:///x")
    ran_sv, out_sv = stubbed(eval_render.report(fx, fx["months"], view, stored_v))
    check("1. old move: old why (report: agents) [not measured]" in stored_note
          and ran_sv.returncode == 0 and "[not measured]" in out_sv.get("html", {}).get("overview", "")
          and "Stored verdict." in out_sv.get("html", {}).get("overview", ""),
          "test_stored_verdict_without_finding_still_renders: a verdict with no finding or metric renders in the note and "
          "the report as not measured, with no error", (ran_sv.stderr or stored_note)[-300:])

    cited_v = {"month": "2026-10", "state": "Cited verdict.", "watch": [],
               "moves": [{"do": "fix it", "why": "because", "see": "findings",
                          "finding": ["friction.recursive-grep.sessions", "<b>x</b>"], "metric": "friction.recursive-grep.sessions"}]}
    cited_note = eval_render.note(fx, view, cited_v, "file:///x")
    ran_cv, out_cv = stubbed(eval_render.report(fx, fx["months"], view, cited_v))
    cited_html = out_cv.get("html", {}).get("overview", "")
    check("(report: findings) [metric: friction.recursive-grep.sessions]" in cited_note
          and ran_cv.returncode == 0 and " [findings: friction.recursive-grep.sessions, &lt;b&gt;x&lt;/b&gt;; metric: friction.recursive-grep.sessions]" in cited_html
          and "<b>x</b>" not in cited_html,
          "test_cited_verdict_move_renders_its_findings_in_the_note_and_the_report_escaped", (ran_cv.stderr or cited_html)[-300:])

    every = eval_render.attention(STORED, view["rows"], RULES, INPUTS)
    signals = [row["signal"] for row in every]
    check(signals[:3] == ["ladder proposal", "skill drift", "rule past trigger"] and signals[-1] == "input degraded"
          and all(row["tab"] for row in every),
          "the attention list keeps stored order (ladder proposals before skill drift), then rules, worse, degraded inputs",
          repr(signals))
    check(signals.count("input degraded") == 2,
          "a stale and an unparseable input each make an attention row; an absent one makes none")
    many = dict(fx, months=dict(fx["months"], **{"2026-09": dict(fx["months"]["2026-09"], attention=STORED * 5)}))
    capped = eval_render.note(many, view, None, "file:///x")
    shown = [line for line in capped.splitlines() if line.startswith("> | ") and ("ladder proposal" in line or "skill drift" in line)]
    check(len(shown) == 6 and "more in the report" in capped and "Needs a decision (" in capped,
          "the note caps the attention list at 6 rows and says how many more the report holds", capped[-400:])
    check("Inputs: 5 producers, 2 degraded" in text and "| routing audit |" in text and "stale" in text,
          "the note's Inputs callout counts producers and degraded ones", text[-500:])

    july = dict(snapshot("2026-07"), closed=True, sessions=dict(SESSIONS, n=10, clean=8, correction=2, failure=0))
    august = dict(snapshot("2026-08"), closed=True, sessions=dict(SESSIONS, n=10, clean=2, correction=6, failure=2))
    frozen = eval_render.month_note(august, july)
    worse_line = next((line for line in frozen.splitlines() if "month over month worse" in line), "")
    check("Clean sessions" in worse_line and "worse" in worse_line,
          "a month note adds the month-over-month worse rows from its snapshot pair alone", frozen[-500:])
    check("| outcome | 2026-07 | 2026-08 | change |" in frozen and "### Dispatched agents, 2026-08" in frozen
          and "- m-bad at high: 3 of 10 rated" in frozen and "pay-per-token spend: $6.50 over 134 runs" in frozen
          and "Quota cost" not in frozen and "Sidecar USD" not in frozen and "share, last" not in frozen,
          "a month note carries the same two blocks as the note, without the sparkline column", frozen[:2500])
    rebuilt = eval_render.month_note(dict(august, backfilled=True), july)
    check("Rebuilt later, from the ledgers that remained" in rebuilt and "Backfilled" not in rebuilt,
          "a month note for a rebuilt month says so in plain words", rebuilt[:500])

    maximal = maximal_facts()
    big = eval_render.note(maximal, eval_history.compare(maximal["months"], maximal["today"]), MAX_VERDICT,
                           "file:///" + "x" * 80)
    size = (len(big.splitlines()), len(big.encode("utf-8")))
    check(size[0] <= eval_render.NOTE_MAX_LINES and size[1] <= eval_render.NOTE_MAX_BYTES
          and size[0] * 100 >= eval_render.NOTE_MAX_LINES * 70 and size[1] * 100 >= eval_render.NOTE_MAX_BYTES * 60,
          "a maximal note (24 months, 12 ladder cells, 5 moves, 20 attention rows, 5 inputs) fits NOTE_MAX_LINES and "
          "NOTE_MAX_BYTES, which are within 30 percent of the measure",
          "measured %r vs %r" % (size, (eval_render.NOTE_MAX_LINES, eval_render.NOTE_MAX_BYTES)))
    capped_cells = [line for line in big.splitlines() if line.startswith("- proof-model-")]
    check(len(capped_cells) == eval_render.NOTE_PROBLEM_CELLS and "- +3 more in the report" in big,
          "the note lists at most 5 problem cells and says how many more the report holds", repr(capped_cells))

    # --- review fixes: the report's scripted behavior, driven with real events and a richer archive --------
    rich = rich_facts()
    rich_view = view_of(rich)
    pairs = [("2026-02", "2026-03"), ("2026-05", "2026-06"), ("2026-07", "2026-08"), ("2026-08", "2026-09"),
             ("2026-06", "2026-09")]
    club = ["<b>2026-09</b>80% · n 10"]
    actions = ([{"id": "values", "op": "values"}]
               + [{"id": "cmp%d" % i, "op": "cmp", "a": a, "b": b} for i, (a, b) in enumerate(pairs)]
               + [{"id": "click", "op": "click", "tab": "ladder"}, {"id": "nothing", "op": "click", "tab": None},
                  {"id": "tip", "op": "tip", "tip": club[0]}, {"id": "untip", "op": "tip", "tip": None}]
               + [{"id": sort, "op": "pick", "panel": "log", "card": 0, "select": 3, "value": sort}
                  for sort in ("newest", "oldest", "corrections")]
               + [{"id": "domain-" + domain, "op": "pick", "panel": "log", "card": 0, "select": 2, "value": domain}
                  for domain in ("godot", "meta", "")]
               + [{"id": "options", "op": "options"},
                  {"id": "domains", "op": "pick", "panel": "sessions", "card": 0, "select": 0, "value": "1"},
                  {"id": "skills", "op": "pick", "panel": "sessions", "card": 1, "select": 0, "value": "clean"},
                  {"id": "edge", "op": "pick", "panel": "sessions", "card": 0, "select": 0, "value": "3"}])
    ran_rich, rich_out = stub_html(eval_render.report(rich, rich["months"], rich_view, VERDICT), actions)
    check(ran_rich.returncode == 0, "rich archive: the report script runs with eight months, 60 memory hits and real events",
          (ran_rich.stderr or ran_rich.stdout)[-300:])
    got = dict(zip([a["id"] for a in actions], rich_out.get("acted", [])))
    compared = [got.get("cmp%d" % i, "") for i in range(5)]

    def parity(a, b, html_text):
        wrong = [row["label"] for row in eval_history.metric_rows(rich["months"][b], rich["months"][a])
                 if change_cell(html_text, row["label"]) != eval_render.change_text(row)]
        return wrong

    check(all(compared) and all(not parity(a, b, html_text) for (a, b), html_text in zip(pairs, compared)),
          "compare: for five month pairs every metric's change cell equals eval_history.metric_rows and change_text",
          repr([(p, parity(*p, h)) for p, h in zip(pairs, compared) if parity(*p, h)]))
    check(all(compared)
          and change_cell(compared[0], "Clean sessions") == "+20pp better"
          and change_cell(compared[1], "Clean sessions") == "+10pp flat"
          and change_cell(compared[2], "Clean sessions") == "+17pp flat"
          and change_cell(compared[2], "A: rules existed, not followed") == "no data"
          and change_cell(compared[3], "Rated agents with defects or rework") == "+0pp rebuilt later"
          and change_cell(compared[3], "Unrated agents") == "+0pp rebuilt later"
          and change_cell(compared[4], "Rated agents with defects or rework") == "+0pp flat",
          "compare: the boundary rules read as specified: exactly 10 points is flat; 2 unfavorable rows apart is a marker, "
          "1 apart is flat; a base month rebuilt later says so on agent rows",
          repr([change_cell(compared[i], "Clean sessions") for i in range(3)]) if all(compared) else "")

    trend_html = rich_out.get("html", {}).get("trend", "")
    options = re.findall(r'<option value="([^"]+)"', got.get("options", ""))
    check(options == sorted(rich["months"]) and ">0%</text>" in chart_card(trend_html, "Clean sessions"),
          "compare: the month selects list exactly the stored months, and a percent chart's axis starts at 0%",
          repr(options))

    def dots(series, keep=lambda point: True):
        usable = [p for p in series if p["value"] is not None and keep(p)]
        return sum(1 for p in usable if p["low"]), sum(1 for p in usable if not p["low"])

    clean_chart = chart_card(trend_html, "Clean sessions")
    hollow, solid = dots(rich_view["series"]["clean"])
    check(hollow == 1 and (clean_chart.count('fill="var(--surface)"'), clean_chart.count('fill="var(--blue)"')) == (hollow, solid),
          "trend: a month below its minimum N draws a hollow point and every other month a filled one",
          repr((clean_chart.count('fill="var(--surface)"'), clean_chart.count('fill="var(--blue)"'), hollow, solid)))
    weeks = eval_render.weekly(RICH_STATS)
    week_chart = chart_card(trend_html, "Weekly clean rate")
    low_weeks = sum(1 for w in weeks if w["n"] < eval_history.MIN_CMP_SESSIONS)
    check(len(weeks) == 3 and low_weeks == 1
          and (week_chart.count('fill="var(--surface)"'), week_chart.count('fill="var(--blue)"')) == (low_weeks, len(weeks) - low_weeks),
          "trend: a week with fewer than 6 sessions is hollow and a week with exactly 6 is filled",
          repr([(w["week"], w["n"]) for w in weeks]))
    kpi = rich_out.get("kpis", [""])[0]
    tail = rich_view["series"]["clean"][-6:]
    hollow_dots = len(re.findall(r'<circle[^>]*fill="none"', kpi))
    check(kpi.count("<circle") == sum(1 for p in tail if p["value"] is not None) == 6
          and hollow_dots == sum(1 for p in tail if p["value"] is not None and p["low"]) == 1,
          "overview: a KPI sparkline draws the last six months, and a month below its minimum N is a hollow dot",
          "%d circles, %d hollow" % (kpi.count("<circle"), hollow_dots))

    agents_html = rich_out.get("html", {}).get("agents", "")
    merged_rows = table_rows(agents_html)
    check(merged_rows == [("m-one", "low", "sc 2", "2", "2", "0", "0", "0", "0", "0", "", "$2.0000 n 1"),
                          ("m-two", "high", "sc 1", "1", "0", "1", "0", "0", "0", "0", "", "$3.0000 n 1"),
                          ("proof-model", "medium", "wf 2 · sc 1", "3", "2", "1", "0", "0", "0", "0", "2.0k n 2", "$1.0000 n 1"),
                          ("proof-model", "high", "wf 1", "1", "1", "0", "0", "0", "0", "0", "9.0k n 1", "")],
          "agents: the default view is one row per model and effort in the order given, with the route mix, the outcome "
          "counts including `other`, and each cost unit per priced agent beside its priced count, empty where no row "
          "carries the unit",
          repr(merged_rows))
    check(agents_html.count("width:50.0%") == 1 and agents_html.count("width:25.0%") == 1
          and agents_html.count("width:12.5%") == 2 and agents_html.count("width:100.0%") == 1,
          "agents: each rating-coverage bar is as wide as its bucket's share of the route's rows",
          re.findall(r"width:[0-9.]+%", agents_html).__repr__())

    split_actions = [{"id": "split", "op": "pick", "panel": "agents", "card": 0, "select": 0, "value": "split"},
                     {"id": "merged", "op": "pick", "panel": "agents", "card": 0, "select": 0, "value": "merged"}]
    rich_html = eval_render.report(rich, rich["months"], rich_view, VERDICT)
    ran_split, split_out = stub_html(rich_html, split_actions)
    acted = dict(zip(("split", "merged"), split_out.get("acted", [])))
    check(ran_split.returncode == 0 and route_toggle_ok(acted, agents_html, merged_rows),
          "agents: the route control opens merged, splits every (model, effort) row by route and returns to the merged rows", (ran_split.stderr or repr(acted))[-300:])
    mutants = {
        "a toggle that ignores the control": ("sel.value==='split'?AG.route_cells:AG.cells", "AG.cells"),
        "a toggle that inverts the control": ("sel.value==='split'?AG.route_cells:AG.cells", "sel.value==='split'?AG.cells:AG.route_cells"),
        "a control that starts split": ("sel.value='merged';", "sel.value='split';"),
        "a control that never redraws": ("sel.onchange=draw;el.appendChild(sel)", "el.appendChild(sel)"),
    }
    for name, (old, new) in mutants.items():
        planted = script_of(rich_html).replace(old, new)
        _, mutant_out = stub_html(rich_html.replace(script_of(rich_html), planted), split_actions)
        mutant_acted = dict(zip(("split", "merged"), mutant_out.get("acted", [])))
        check(planted != script_of(rich_html) and not route_toggle_ok(mutant_acted, mutant_out.get("html", {}).get("agents", ""), merged_rows),
              "agents: the route toggle proof fails on %s" % name, repr(mutant_acted)[:200])
    compare_mutants = {
        "a compare that reads a rise in a cause as better or worse": ("if(r.better==null)return null;", ""),
        "a compare that still says backfilled": ("return 'rebuilt later'", "return 'backfilled'"),
        "a compare that ignores the minimum N": ("if(b.low||a.low)return 'low N';", ""),
    }
    for name, (old, new) in compare_mutants.items():
        planted = script_of(rich_html).replace(old, new)
        _, mutant_out = stub_html(rich_html.replace(script_of(rich_html), planted),
                                  [{"id": "cmp%d" % i, "op": "cmp", "a": a, "b": b} for i, (a, b) in enumerate(pairs)])
        mutant_compared = mutant_out.get("acted", [])
        check(planted != script_of(rich_html) and len(mutant_compared) == 5
              and any(parity(a, b, html_text) for (a, b), html_text in zip(pairs, mutant_compared)),
              "compare: the parity proof fails on %s" % name)
    other_mutant = script_of(rich_html).replace("c.discarded,c.unrated,c.other]", "c.discarded,c.unrated]")
    _, other_out = stub_html(rich_html.replace(script_of(rich_html), other_mutant))
    check(other_mutant != script_of(rich_html) and table_rows(other_out.get("html", {}).get("agents", "")) != merged_rows,
          "agents: the row proof fails on a table that drops the `other` column")
    outcomes_chart = chart_card(rich_out.get("html", {}).get("sessions", ""), "Outcomes per month")
    patterns_chart = chart_card(rich_out.get("html", {}).get("sessions", ""), "Patterns per month")
    wanted = sum(1 for month in rich["months"].values() for key in ("A", "B", "C", "D", "E", "none")
                 if month["sessions"]["patterns"].get(key, 0) > 0)
    check(patterns_chart.count("<rect") == wanted and "NaN" not in patterns_chart
          and ">2026-02</text>" in outcomes_chart and ">2026-09</text>" in outcomes_chart and ">2026-05</text>" not in outcomes_chart,
          "sessions: a stacked bar chart labels only its first and last month and treats a missing key as zero",
          "%d rects, wanted %d" % (patterns_chart.count("<rect"), wanted))
    inputs_rich = rich_out.get("html", {}).get("inputs", "")
    check("2026-W35: 3 silent misses" in inputs_rich and "2026-W37: 5 silent misses" in inputs_rich
          and "2026-W38: 0 silent misses" in inputs_rich and "undefined" not in inputs_rich
          and "2026-W34" not in inputs_rich and "2026-W33" not in inputs_rich,
          "routing card: the last four weeks show, a week with no silent misses reads 0, none reads undefined",
          inputs_rich[inputs_rich.find("Routing card"):][:400])

    log_rich = rich_out.get("html", {}).get("log", "")
    check(log_rich.count("<td>mem-") == 50 and "mem-49" in log_rich and "mem-50" not in log_rich,
          "log: the memory table lists the 50 most cited entities and no more", str(log_rich.count("<td>mem-")))
    newest, oldest, most = got.get("newest", ""), got.get("oldest", ""), got.get("corrections", "")
    godot, meta, every = got.get("domain-godot", ""), got.get("domain-meta", ""), got.get("domain-", "")

    def order(html_text):
        return [t for t in sorted(("Row seven", "Row nine", "Row eleven"), key=lambda t: html_text.find(t))
                if t in html_text]

    check(re.findall(r'<option value="([^"]+)">pattern ', log_rich) == ["A", "B", "C", "none"],
          "log: the pattern filter lists each pattern once and 'none' for a correction with no pattern")
    check("Monthly percent; hollow points are below min N 6." in trend_html
          and "Monthly percent; hollow points are below min N 8." in trend_html
          and all(r["definition"].replace("`", "") in trend_html.replace("&#39;", "'").replace("`", "") for r in rich_view["rows"]),
          "trend: every chart carries its metric's definition, says it plots a monthly percent and names its minimum N")
    five = next((r for r in re.findall(r"<tr>.*?</tr>", newest, re.S) if "Row five" in r), "")
    check("<td>none</td>" in five and '<td class="num">0</td><td></td></tr>' in five
          and '<option value="none">pattern none</option>' in log_rich,
          "log: a correction with no date and no pattern prints an empty date and the pattern none, and the pattern filter lists none",
          five)
    check(order(newest) == ["Row eleven", "Row nine", "Row seven"] and order(oldest) == ["Row seven", "Row nine", "Row eleven"]
          and order(most) == ["Row eleven", "Row seven", "Row nine"],
          "log: the corrections table sorts newest first, oldest first and by correction count",
          repr((order(newest), order(oldest), order(most))))
    check(order(godot) == ["Row seven"] and sorted(order(meta)) == ["Row eleven", "Row seven"] and len(order(every)) == 3
          and "1 of 4 rows" in godot and "2 of 4 rows" in meta,
          "log: the corrections table filters by domain; 'all domains' lists every row", repr((order(godot), order(meta), order(every))))
    check('<option value="godot">godot</option>' in log_rich and '<option value="">all domains</option>' in log_rich,
          "log: the corrections card offers a domain filter listing the domains its rows name")

    sessions_rich = rich_out.get("html", {}).get("sessions", "")
    domains_html, skills_html, edge_html = got.get("domains", ""), got.get("skills", ""), got.get("edge", "")
    check(got.get("values") == ["2026-08", "2026-09"] and "edge-domain" in edge_html
          and "thin-domain" not in edge_html and "edge-domain" in domains_html,
          "compare: the selects open on the last closed month and the current one; 'n at least 3' keeps a domain with exactly 3",
          repr(got.get("values")))

    def before(html_text, names):
        return all(html_text.index(a) < html_text.index(b) for a, b in zip(names, names[1:]))

    check(before(domains_html, ["alpha-domain", "proof-domain", "nullish", "thin-domain"])
          and "n/a clean · n/a · n 2" in domains_html and "75% clean · 7.5 per 100 · n 4" in domains_html
          and "width:0.0%" in domains_html,
          "sessions: domain bars order by count then name, and a domain with no known outcome reads n/a with an empty bar",
          domains_html[:300])
    check(before(skills_html, ["zeta-skill", "proof-skill", "nullish-skill"]) and "89%" in skills_html and "n/a" in skills_html,
          "sessions: the skill table sorts by clean rate with an unknown rate last", skills_html[:300])
    kpis = rich_out.get("kpis", [])
    check(len(kpis) == 5 and "Unrated agents" in kpis[4] and "0 of 9" in kpis[4] and "+0pp rebuilt later" in kpis[4]
          and not any("$" in k or "Quota" in k for k in kpis),
          "overview: the last KPI card is the unrated share with its count and change, and no card prints a cost",
          repr(kpis[3:]))
    rules_row = table_row(rich_out.get("html", {}).get("inputs", ""), "rules feed")
    check('<td class="num">0</td><td>n/a</td><td>n/a</td><td class="num">n/a</td><td><b>absent</b>' in rules_row,
          "inputs: a producer with no dates or age prints n/a in those cells", rules_row)
    check("<td>low N</td>" in table_row(sessions_rich, "zeta-skill") and "<td>low N</td>" not in table_row(sessions_rich, "proof-skill"),
          "sessions: only a skill whose trend is low N carries the low N flag in its row")
    check("low N: a bucket under" not in sessions_rich and "19 of 97 entries carry a duration" in sessions_rich,
          "sessions: with every duration bucket at 6 or more the low-N note is absent and the coverage reads n of N",
          sessions_rich[sessions_rich.find("Duration"):][:300])
    low_fx = rich_facts(DUR_LOW)
    _, low_out = stub_html(eval_render.report(low_fx, low_fx["months"], view_of(low_fx), None))
    check("low N: a bucket under 6 sessions" in low_out.get("html", {}).get("sessions", ""),
          "sessions: a duration bucket with 5 sessions adds the low-N note")

    click, cleared = got.get("click", {}), got.get("nothing", {})
    check(click.get("active") == ["ladder"] and click.get("hash") == "ladder" and click.get("prevented") is True
          and cleared.get("active") == ["ladder"] and cleared.get("prevented") is False,
          "events: a click on a tab link opens that tab and stops the jump; a click elsewhere does nothing",
          repr((click, cleared)))
    tips = [got.get("tip", {}), got.get("untip", {})]
    check(len(tips) == 2 and tips[0]["display"] == "block" and tips[0]["html"] == club[0] and tips[1]["display"] == "none",
          "events: pointing at a data point shows its tooltip, pointing away hides it", repr(tips))

    ladder_rich = rich_out.get("html", {}).get("ladder", "")
    check('<th class="num" title="rows whose requested pin is not the served model">mismatch</th>' in ladder_rich
          and '<td class="num">4</td><td><b>candidate</b></td>' in ladder_rich
          and ladder_rich.count('<td class="num">0</td><td><b>') == 3,
          "ladder: every cell shows how many of its rows were served by a model other than the requested pin",
          ladder_rich[-500:])
    check("proof-x at executor: falsified 2 of 9 (wf_1:a, wf_2:b)" in ladder_rich
          and "unrated 2 (of which sampled-out 1)" in ladder_rich and "no outcome 1 · " in ladder_rich
          and 'Proof-A</span>' in ladder_rich and '<span class="note">proof-x</span>' not in ladder_rich,
          "ladder: a contradicting cell prints its falsified rows, the footer splits sampled-out from unrated, and a label "
          "that equals the model id is not repeated", ladder_rich[-700:])
    broken_ladder = dict(LADDER, cells=[], degraded="registry unreadable at x: boom")
    degraded_fx = facts(broken_ladder)
    _, degraded_out = stub_html(eval_render.report(degraded_fx, degraded_fx["months"], view_of(degraded_fx), None))
    degraded_note = eval_render.note(degraded_fx, view_of(degraded_fx), None, "file:///x")
    check("readout unavailable: registry unreadable at x: boom" in degraded_out.get("html", {}).get("ladder", "")
          and "Ladder readout: unavailable" in degraded_note and "registry unreadable at x: boom" in degraded_note,
          "ladder: an unreadable registry says so in the tab and the note instead of drawing an empty readout")

    stamped = eval_render.weekly({"timeline": [{"date": "unknown", "outcome": "clean", "count": 5},
                                               {"date": "2026-13-45", "outcome": "clean", "count": 1},
                                               {"date": "2026-09-28T10:00", "outcome": "clean", "count": 2},
                                               {"date": "2026-09-27", "outcome": "correction", "count": 1}]})
    check(stamped == [{"week": "2026-W39", "clean": 0, "n": 1}, {"week": "2026-W40", "clean": 2, "n": 2}],
          "weekly: a timeline date that is not a calendar day is skipped and a timestamp counts on its day", repr(stamped))

    fenced = dict(fx, months=dict(fx["months"], **{"2026-09": dict(fx["months"]["2026-09"], attention=[
        {"signal": "rule past trigger", "evidence": "rules/a|b.md: line one\nline two", "tab": "log"}])}))
    fenced_note = eval_render.note(fenced, view, {"month": "2026-09", "state": "first\nsecond",
                                                  "moves": [{"do": "do\nit", "why": "why|not", "see": "agents"}],
                                                  "watch": ["w\nx"], "milestone": "m\nn"}, "file:///x")
    row_line = next((l for l in fenced_note.splitlines() if "rules/a" in l), "")
    check(len(re.split(r"(?<!\\)\|", row_line)) == 6 and "\\|" in row_line
          and not any(l.startswith(("second", "line two", "it", "x", "n")) for l in fenced_note.splitlines()),
          "note: a pipe in a table cell is escaped and a line break in verdict or evidence text cannot end its callout or row",
          repr(row_line))

    found_fx = dict(findings_facts(), trends={GREP_KEY: [
        {"start": "2026-08-20", "end": "2026-08-26", "num": 3, "den": 8},
        {"start": "2026-08-27", "end": "2026-09-02", "num": 0, "den": 0},
        {"start": "2026-09-24", "end": "2026-09-30", "num": 1, "den": 9}]})
    found_out = stub_html(eval_render.report(found_fx, found_fx["months"], view_of(found_fx), None))[1]
    found_panel = found_out.get("html", {}).get("findings", "")
    check(all(key in found_panel for key in (GREP_KEY, SLIP_KEY)) and "not-landed" in found_panel
          and "declined 2026-09-05: not a recurrence" in found_panel and "applied 2026-09-10 (tool)" in found_panel
          and "&lt;script&gt;alert(1)&lt;/script&gt;" in found_panel and "<script>" not in found_panel
          and "cascade.abc12345" in found_panel and "waiting (1 of 5)" in found_panel
          and "<td>cascade.unfixed</td>" in found_panel and "proof-session" in found_panel,
          "test_findings_tab_renders_every_finding_and_escapes_text", found_panel[:400])
    check("<th>last 6 weeks</th>" in found_panel and "3/8 · - · 1/9" in found_panel,
          "test_findings_tab_shows_the_last_six_weeks_per_metric", found_panel[:400])
    bare_out = stub_html(eval_render.report(fx, fx["months"], view, None))[1]
    check("were not computed for this run" in bare_out.get("html", {}).get("findings", ""),
          "test_findings_tab_says_so_when_findings_were_not_computed")

    none_fx = dict(fx, findings={"metrics": {}, "findings": {}, "unclassified": {}, "coverage": {}, "diagnostics": {}})
    base_note = eval_render.note(fx, view, None, "file:///x")
    empty_note = eval_render.note(dict(none_fx, effects=[]), view, None, "file:///x")
    metrics_note = eval_render.note(findings_facts(effects=[]), view, None, "file:///x")
    effects_note = eval_render.note(found_fx, view, None, "file:///x")
    check("see the Findings tab" not in base_note and "measured fixes" not in base_note
          and "see the Findings tab" not in empty_note and "measured fixes" not in empty_note,
          "test_note_findings_lines_appear_only_when_nonzero: neither line when findings are absent or empty")
    check("findings: 2 metrics — see the Findings tab\n\n" in metrics_note and "measured fixes" not in metrics_note,
          "test_note_findings_lines_appear_only_when_nonzero: the findings line alone with metrics and no effects")
    check("findings: 2 metrics — see the Findings tab\n\n" in effects_note
          and "measured fixes: 1 improved, 0 flat, 0 worse, 1 waiting, 0 baseline short\n\n" in effects_note
          and effects_note.index("measured fixes") < effects_note.index("Inputs"),
          "test_note_findings_lines_appear_only_when_nonzero: both lines, before Inputs, each followed by a blank line")
    short_note = eval_render.note(dict(found_fx, effects=[{"status": "baseline-thin", "moved": None},
                                                         {"status": "baseline-partial", "moved": None},
                                                         {"status": "waiting", "moved": None}]), view, None, "file:///x")
    check("measured fixes: 0 improved, 0 flat, 0 worse, 1 waiting, 2 baseline short\n\n" in short_note,
          "test_note_counts_baseline_limited_fixes_apart_from_waiting", short_note[-400:])
    added = len(effects_note.splitlines()) - len(base_note.splitlines())
    check(0 < added <= 4, "test_note_stays_within_its_line_cap: findings and effects add at most 4 note lines", repr(added))

    layered = dict(fx, ) if False else None
    layer_out = stub_html(eval_render.report(fx, fx["months"], view, {"month": "2026-09", "state": "s", "moves": [],
                                                                  "layers": {"pins": "move", "guards": "no move: <b>x</b>"}}))[1]
    layer_html = layer_out.get("html", {}).get("overview", "")
    check("pins" in layer_html and "no move: &lt;b&gt;x&lt;/b&gt;" in layer_html and "<b>x</b>" not in layer_html,
          "test_verdict_card_lists_each_layer_result_escaped", layer_html[:300])

    # S3/S5 reader surfaces: the corrections-per-100-owner-prompts rate beside the clean share, the driver
    # table and the rule table on the Sessions tab, and the Agents-tab footer naming why rows are unrated.
    check(all(t in text for t in ("Corrections per 100 owner prompts: 8.0",
                                  "(12 corrections over 150 owner prompts in 6 sessions)")),
          "the note prints the corrections-per-100-owner-prompts rate beside the clean share", text[-1600:])
    check("7.5 per 100" in driven.get("domainsAll", "") and "n/a" in driven.get("domainsAll", ""),
          "the Sessions domain rows show the rate beside the clean share; a domain with no prompts reads n/a",
          driven.get("domainsAll", "")[:400])
    check("10.0 per 100" in driven.get("skillsLoads", ""),
          "the Sessions skill rows show the rate beside the clean share", driven.get("skillsLoads", "")[:400])
    check("25.0 per 100" in sessions_html and "8 of 16 entries carry a duration" in sessions_html,
          "the Sessions duration buckets show the rate beside the clean share", sessions_html[-400:])
    check("m-one / medium" in sessions_html and "12.5 per 100" in sessions_html and "m-two / high" in sessions_html
          and "15.0 per 100" in sessions_html and "Driver model and effort" in sessions_html,
          "the Sessions tab draws the driver table: outcome and rate per (model, effort)", sessions_html[-500:])
    check(".claude/CLAUDE.md" in sessions_html and "not-read" in sessions_html and "golden-001" in sessions_html
          and "rules/one-off.md" in sessions_html and "Rules named but not followed" in sessions_html,
          "the Sessions tab draws the rule table: rule, kind, sessions and example ids", sessions_html[-500:])
    check("Unrated rows by why" in agents_html and "sidecar 4" in agents_html and "panel-seat 2" in agents_html
          and "session-never-rated 1" in agents_html,
          "the Agents tab's footer names why the unrated rows are unrated", agents_html[-400:])
    no_reasons = {key: value for key, value in AGENTS.items() if key != "unrated_reasons"}
    _, bare_out = stub_html(eval_render.report(dict(fx, agents=no_reasons), fx["months"], view, None))
    check("Unrated rows by why" not in bare_out.get("html", {}).get("agents", ""),
          "a row set with no unrated reason renders no footer line", bare_out.get("html", {}).get("agents", "")[-300:])

    broken_data = run_stub(script, "{}")
    check(broken_data.returncode != 0, "a report whose data lacks the expected fields fails the smoke run, not silently")

    total = len(checks)
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for line in failures:
        print("  FAIL " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
