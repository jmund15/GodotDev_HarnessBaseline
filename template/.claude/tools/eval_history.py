"""Monthly history for /eval_dashboard: one tracked file, one snapshot per month.

`.claude/eval_history.json` = {"schema": 3, "months": {"YYYY-MM": snapshot}}. A snapshot holds counts and
sums only, never a rate, so months merge and compare exactly, and it holds no wall-clock field, so two runs
over the same sources write the same bytes on any day. The self-evaluate store and the orchestration ledger
rotate; this file is the memory that outlives them.

Schema 1 stored agents by effort and sidecar rows by served model; schema 2 stored one cell per (requested
model, requested effort); schema 3 stores one cell per (model identity, requested effort) with an `other` count
so its columns sum to `n`, the causes of the non-clean sessions, and the pay-per-token spend. `publish` rebuilds
an older file month by month from the live sources.
"""
import json
import os
import sys
from collections import namedtuple

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(TOOLS, "..", "hooks"))
from _hook_state import update_json_locked  # noqa: E402

SCHEMA = 3
PREVIOUS_SCHEMAS = (1, 2)
MIN_CMP_SESSIONS = 6
MIN_CMP_AGENTS = 8
TREND_POINTS = 10
MIN_CMP_ROWS = 2
REBUILT = "rebuilt later"
MONTH_MAX_BYTES = 32700
SERIES_MONTHS = 24
HISTORY_REL = os.path.join(".claude", "eval_history.json")


class HistoryWriteError(RuntimeError):
    pass


class HistoryRefusedError(RuntimeError):
    pass


OUTCOMES = ("clean", "defects", "rework", "discarded")
COVERAGE_BUCKETS = ("rated", "unrated", "sampled_out", "absent", "unparseable")


def _money(value):
    return round(value, 6)


CELL_KEYS = ("model", "effort", "wf", "sc", "n", "clean", "defects", "rework", "discarded", "unrated", "other",
             "qcost", "qcost_n", "usd", "usd_n")
CELL_KEYS_V2 = tuple(key for key in CELL_KEYS if key != "other")


def agents_block(facts):
    """The snapshot's `agents` block from one month's `orchestration_metrics.archive_facts`: the coverage counts,
    one cell per (model, requested effort) with each cost beside the number of rows that carry it, so a rate
    divides at read, and the pay-per-token spend."""
    total = facts["coverage"]["total"]
    cells = [dict({key: cell[key] for key in CELL_KEYS}, qcost=_money(cell["qcost"]), usd=_money(cell["usd"]))
             for cell in facts["cells"]]
    return {"rows": facts["rows"], **{bucket: total[bucket] for bucket in COVERAGE_BUCKETS}, "cells": cells,
            "paid": {"usd": _money(facts["paid"]["usd"]), "n": facts["paid"]["n"]}}


def ladder_block(readout):
    """The snapshot's `ladder` list from `model_tier_readout.cells`: counts and the verdict word only, so a
    later month re-reads the same cell without the rows that produced it."""
    keys = ("tier", "n", "sessions", "clean", "defects", "rework", "discarded", "verdict")
    return [dict({"model_id": cell["model"]}, **{key: cell[key] for key in keys}) for cell in readout["cells"]]


def orchestration_source(facts, *, open_month):
    """The month's ledger span. Rows that carry no date belong to no month, so the archive-wide count is
    stored on the open month alone; a closed month stores null."""
    return {"rows": facts["rows"], "undated": facts["undated"] if open_month else None,
            "first": facts["first"], "last": facts["last"]}


def history_path(root):
    return os.path.join(root, HISTORY_REL)


def _canonical(value):
    """Sorted keys all the way down, so equal content is equal bytes."""
    return json.loads(json.dumps(value, sort_keys=True))


def read_stored(root):
    """The stored history dict, or None when the file does not exist. Raises HistoryRefusedError for a
    file that exists but is unparseable, not a dict, or of a schema that is neither the current nor the
    previous ones: the locked writer would otherwise salvage it to `{}` and replace a tracked history with one
    month. A previous-schema file is returned as read; `publish` rebuilds it."""
    path = history_path(root)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError) as exc:
        raise HistoryRefusedError("%s is unreadable: %s" % (HISTORY_REL, exc)) from exc
    if not isinstance(stored, dict) or stored.get("schema") not in (SCHEMA,) + PREVIOUS_SCHEMAS \
            or not isinstance(stored.get("months"), dict):
        raise HistoryRefusedError("%s is not a schema-%d history" % (HISTORY_REL, SCHEMA))
    return stored


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _agents_ok(agents, schema):
    if not isinstance(agents, dict) or not all(_is_count(agents.get(key)) for key in ("rows",) + COVERAGE_BUCKETS):
        return False
    if schema == 1:
        return isinstance(agents.get("by_effort"), dict) and isinstance(agents.get("sidecar"), dict)
    keys = CELL_KEYS if schema == SCHEMA else CELL_KEYS_V2
    return isinstance(agents.get("cells"), list) and all(
        isinstance(cell, dict) and all(key in cell for key in keys) for cell in agents["cells"])


def _snapshot_problem(month, snapshot, schema=SCHEMA):
    """Why a stored month cannot be read by the merge, compare and render code, or None. `schema` is the schema
    the stored file declares, which fixes the shape of the agents and sessions blocks."""
    if not isinstance(snapshot, dict):
        return "is not an object"
    if snapshot.get("month") != month:
        return "names month %r" % (snapshot.get("month"),)
    sources, sessions, agents = snapshot.get("sources"), snapshot.get("sessions"), snapshot.get("agents")
    if not isinstance(sources, dict) or not all(
            isinstance(sources.get(name), dict) and _is_count(sources[name].get("rows"))
            for name in ("self_eval", "orchestration")):
        return "has no sources block"
    if not isinstance(sessions, dict) or not isinstance(sessions.get("patterns"), dict) or not all(
            _is_count(sessions.get(key)) for key in ("n", "clean", "correction", "failure", "unknown")):
        return "has no sessions block"
    if schema == SCHEMA and not (isinstance(sessions.get("causes"), dict) or ("causes" in sessions and sessions["causes"] is None)):
        return "has a sessions block with no causes"
    if not _agents_ok(agents, schema):
        return "has no agents block"
    for key in ("ladder", "attention"):
        if key in snapshot and (not isinstance(snapshot[key], list) or not all(isinstance(r, dict) for r in snapshot[key])):
            return "has a %s that is not a list of objects" % key
    return None


def check_months(months, schema=SCHEMA):
    """Raises HistoryRefusedError naming the first stored month the code cannot read."""
    for month in sorted(months):
        problem = _snapshot_problem(month, months[month], schema)
        if problem:
            raise HistoryRefusedError("%s month %s %s" % (HISTORY_REL, month, problem))


def _size(name, block):
    """The population a block is judged on, so a rotation cannot replace it with a smaller one."""
    if not block:
        return 0
    if name == "ladder":
        return sum(cell.get("n", 0) for cell in block)
    return block.get("n", 0) if name == "sessions" else block.get("rows", 0)


def _merged(old, new):
    """`new` with each block kept from `old` when `old` counted more: the ledgers rotate, history must not."""
    out = dict(new)
    out["sources"] = dict(new.get("sources", {}))
    for name in ("self_eval", "orchestration"):
        if _size(name, (old.get("sources") or {}).get(name)) > _size(name, out["sources"].get(name)):
            out["sources"][name] = old["sources"][name]
    for name in ("sessions", "agents", "ladder"):
        if _size(name, old.get(name)) > _size(name, out.get(name)):
            out[name] = old[name]
    return out


def _has_rows(snapshot):
    return bool(_size("self_eval", snapshot["sources"]["self_eval"]) or _size("orchestration", snapshot["sources"]["orchestration"]))


def _kept_attention(snapshot, backfilled):
    """A backfilled month stores no ladder, so it stores no ladder-tab attention row either."""
    rows = snapshot.get("attention", [])
    return [row for row in rows if row["tab"] != "ladder"] if backfilled else rows


STORED_ONLY_SIGNALS = ("skill drift",)


def _carry_stored(old, rows):
    """`rows` plus the stored rows a closing recompute cannot make: skill drift reads the analyzer's recent
    window, which only the run inside the open month has."""
    carried = [row for row in (old or {}).get("attention", []) if row["signal"] in STORED_ONLY_SIGNALS and row not in rows]
    return list(rows) + carried


def _seal(snapshot):
    """A closed month owns no undated count: rows that carry no date belong to no month."""
    sources = dict(snapshot["sources"], orchestration=dict(snapshot["sources"]["orchestration"], undated=None))
    return dict(snapshot, sources=sources)


def _refrozen(old, new, rules):
    """A closed month rewritten from `new`, keeping the flags and the verdict it stored."""
    backfilled = old.get("backfilled")
    return _seal(dict(new, closed=True, backfilled=backfilled, verdict=old.get("verdict"),
                      ladder=[] if backfilled else new.get("ladder", []), rules=None if backfilled else rules,
                      attention=_carry_stored(old, _kept_attention(new, backfilled))))


def _without_agents(old):
    """`old` minus the agent counts of the previous schema, so a merge takes them from the rebuild."""
    return dict(old, sources={key: value for key, value in old["sources"].items() if key != "orchestration"},
                agents=None)


def _upgraded(old):
    """A previous-schema month with the sessions block the current schema asks for: a month stored before causes
    were recorded has none, and nothing can rebuild them once its source rotated."""
    return dict(old, sessions=dict(old["sessions"], causes=old["sessions"].get("causes")))


def _cellless(old):
    """A previous-schema month no live source covers: its counts stay, and it stores no cells or paid spend, which
    nothing can rebuild."""
    return dict(_upgraded(old), agents=dict({key: old["agents"][key] for key in ("rows",) + COVERAGE_BUCKETS}, cells=[]))


class _Settle:
    """Decides one month's stored snapshot for a publish and records the action taken on it in `actions`."""

    def __init__(self, today, verdict, rules, refreeze):
        self.current = today[:7]
        self.verdict = verdict
        self.rules = rules
        self.refreeze = refreeze
        self.actions = {}

    def next_snapshot(self, month, old, new):
        if old is not None and old.get("closed"):
            if month not in self.refreeze or new is None:
                return old
            self.actions[month] = "refrozen"
            return _refrozen(old, new, self.rules)
        if old is None and new is None:
            return None
        if old is None and month != self.current:
            self.actions[month] = "backfilled"
            return _seal(dict(new, closed=True, backfilled=True, ladder=[], rules=None, verdict=None,
                              attention=_kept_attention(new, True)))
        base = old if new is None else new if old is None else _merged(old, new)
        if month == self.current:
            self.actions[month] = "written"
            fresh = dict(base, closed=False, backfilled=False,
                         verdict=self.verdict if self.verdict is not None else (old or {}).get("verdict"))
            fresh.pop("rules", None)
            return fresh
        self.actions[month] = "closed"
        return _seal(dict(base, closed=True, backfilled=False, rules=self.rules, verdict=old.get("verdict"),
                          attention=_carry_stored(old, base.get("attention", []))))

    def migrated(self, month, old, new):
        self.actions[month] = "migrated"
        if new is None:
            return _cellless(old)
        if old.get("closed"):
            return _refrozen(old, new, old.get("rules"))
        result = self.next_snapshot(month, _without_agents(_upgraded(old)), new)
        self.actions[month] = "migrated"
        return result


def _merge_months(months, candidates, settle, legacy):
    """Set each month's settled snapshot in `months`; a legacy-schema file is migrated, and months after the current one are skipped."""
    for month in sorted(set(months) | set(candidates)):
        old, new = months.get(month), candidates.get(month)
        if legacy and old is not None:
            result = settle.migrated(month, old, new if month <= settle.current else None)
        elif month > settle.current:
            continue
        else:
            result = settle.next_snapshot(month, old, new)
        if result is not None:
            months[month] = result


def publish(root, facts, verdict, today, *, refreeze=()):
    """Write the history under the file lock; return {"months", "actions"}.

    `facts["months"]` holds one candidate snapshot per month that has data; `facts["rules"]` is the live rules
    feed, stored only when a month closes. The current month is written open; an open earlier month is
    recomputed once and closed; an earlier month with no snapshot is backfilled; a closed month changes only
    when named in `refreeze`. A file of the previous schema is migrated: every stored month is rebuilt from
    the live sources (`facts["months"]`), keeping its verdict, its closed and backfilled flags, its rules block,
    and for a closed month its stored skill-drift rows; a month with no live source keeps its counts and gets no
    cells. Raises HistoryRefusedError for a refused stored file (left untouched) and HistoryWriteError for a
    failed lock or write."""
    candidates = {month: c for month, c in facts["months"].items() if _has_rows(c)}
    settle = _Settle(today, verdict, facts.get("rules"), tuple(refreeze))
    failures = []

    def update(state):
        try:
            # Read under the lock: a concurrent writer's replace makes an unlocked Windows read fail.
            stored = read_stored(root)
            months = state.get("months") if isinstance(state.get("months"), dict) else {}
            declared = stored.get("schema") if stored else SCHEMA
            check_months(months, declared)
            state["schema"] = SCHEMA
            _merge_months(months, candidates, settle, declared in PREVIOUS_SCHEMAS)
            state["months"] = {key: _canonical(months[key]) for key in sorted(months)}
            return state["months"]
        except Exception as exc:
            failures.append(exc)
            raise

    written, months = update_json_locked(history_path(root), update)
    if failures:
        raise failures[0]
    if not written:
        raise HistoryWriteError("could not write %s" % HISTORY_REL)
    return {"months": months, "actions": settle.actions}


FLOOR_MIN_ROWS = 8
FLOOR_MIN_FALSIFIED = 2
PROPOSAL_VERDICTS = ("contradicts", "candidate")


def attention_rows(*, ladder, floor_cells, candidates, skill_trends=()):
    """The facts-derived rows of the attention list, stored with the month: ladder proposals, an effort floor
    that moved (2 or more defects or rework in one label family on one served model at one requested effort with
    8 or more rated rows; `floor_cells` is `orchestration_metrics.archive_facts`' family -> model -> effort ->
    {rated, falsified}), over-pin
    candidates, and skills whose clean rate fell (the analyzer's DOWN with enough entries). Each row is
    {signal, evidence, tab}; rows that follow the run's date or inputs are added at render, never stored."""
    rows = []
    for verdict in PROPOSAL_VERDICTS:
        for cell in ladder:
            if cell["verdict"] == verdict:
                rows.append({"signal": "ladder proposal", "tab": "ladder",
                             "evidence": "%s at %s: %s (n %d, %d sessions)" % (
                                 cell["model_id"], cell["tier"], verdict, cell["n"], cell["sessions"])})
    for family in sorted(floor_cells):
        for model in sorted(floor_cells[family]):
            for effort in sorted(floor_cells[family][model]):
                cell = floor_cells[family][model][effort]
                if cell["falsified"] >= FLOOR_MIN_FALSIFIED and cell["rated"] >= FLOOR_MIN_ROWS:
                    rows.append({"signal": "effort floor moved", "tab": "agents",
                                 "evidence": "%s on %s at %s: %d defects or rework in %d rated agents" % (
                                     family, model, effort, cell["falsified"], cell["rated"])})
    for cand in candidates:
        rows.append({"signal": "over-pin candidate", "tab": "agents",
                     "evidence": "%s %s at %s, try %s: %.1fx cost, n %d" % (
                         cand["family"], cand.get("model") or "?", cand["effort"], cand["suggested"],
                         cand["cost_ratio"], cand["n_hi"] + cand["n_lo"])})
    for trend in skill_trends:
        if trend["direction"] == "DOWN" and not trend["low_n"]:
            rows.append({"signal": "skill drift", "tab": "sessions",
                         "evidence": "%s: clean %.0f%% to %.0f%%" % (trend["skill"], trend["prior_pct"],
                                                                     trend["recent_pct"])})
    return rows


def _months_to(current, count):
    year, month = int(current[:4]), int(current[5:7])
    out = []
    for back in range(count - 1, -1, -1):
        total = year * 12 + month - 1 - back
        out.append("%04d-%02d" % (total // 12, total % 12 + 1))
    return out


CAUSES = (("A", "rules existed, not followed"), ("B", "docs missing"), ("D", "tool and its docs disagree"),
          ("E", "tooling bug cascaded across sessions"))
KNOWN_OUTCOME = "sessions with a known outcome"


def _pct_point(count, den, unfavorable):
    return {"value": 100.0 * count / den if den else None, "n": den, "count": count, "unfav": unfavorable, "rows": den}


def _sessions_point(snapshot, key, cause):
    sessions = snapshot["sessions"]
    known = sessions["n"] - sessions["unknown"]
    if cause is None:
        count = sessions[key]
        return _pct_point(count, known, known - count if key == "clean" else count)
    non_clean = sessions["correction"] + sessions["failure"]
    causes = sessions.get("causes")
    if causes is None:
        return {"value": None, "n": non_clean, "count": None, "unfav": None, "rows": non_clean}
    return _pct_point(other_causes(snapshot) if cause == "other" else causes.get(cause, 0), non_clean, None)


def other_causes(snapshot):
    """The non-clean sessions of a month whose recorded cause is none of the named ones, or None when the month
    stores no causes. A session whose pattern is the clean code or unset is counted here, not dropped."""
    sessions = snapshot["sessions"]
    causes = sessions.get("causes")
    if causes is None:
        return None
    return sessions["correction"] + sessions["failure"] - sum(causes.get(code, 0) for code, _ in CAUSES)


def _agents_point(snapshot, kind):
    agents = snapshot["agents"]
    cells = agents["cells"]
    if kind == "defect_rework":
        rated = sum(cell[name] for cell in cells for name in OUTCOMES)
        bad = sum(cell["defects"] + cell["rework"] for cell in cells)
        return _pct_point(bad, rated, bad)
    open_rows = agents["rows"] - agents["sampled_out"]
    return _pct_point(agents["unrated"], open_rows, agents["unrated"])


class Metric(namedtuple("Metric", "key label kind min_n better agent gloss definition cause")):
    """One row of the comparison: `gloss` is the short reading printed beside the label in the note, and
    `definition` the sentence the report shows under it. `better` is None where a rise is neither good nor bad.
    `agent` marks a row over dispatched agents; `cause` is the `/self_evaluate` code a session row counts, or
    `other` for sessions with no named cause."""


def _session_metric(key, label, gloss, definition, better, cause=None):
    return Metric(key, label, "pct", MIN_CMP_SESSIONS, better, False, gloss, definition, cause)


METRICS = (
    _session_metric("clean", "Clean sessions", "the owner made no correction",
                    "Sessions where the owner made no correction, as a share of %s." % KNOWN_OUTCOME, "higher"),
    _session_metric("correction", "Sessions with a correction", "the owner caught one or more issues",
                    "Sessions where the owner caught one or more issues, as a share of %s." % KNOWN_OUTCOME, None),
    _session_metric("failure", "Failed sessions", "a critical failure: data loss, repeated frustration",
                    "Sessions with a critical failure such as data loss or repeated frustration, as a share of %s." % KNOWN_OUTCOME,
                    "lower"),
) + tuple(
    _session_metric("cause_" + code, "%s: %s" % (code, name), None,
                    "Sessions that needed a correction or failed, where `/self_evaluate` recorded cause %s (%s), as a share "
                    "of all such sessions." % (code, name), None, code)
    for code, name in CAUSES) + (
    _session_metric("cause_other", "No cause recorded", None,
                    "Sessions that needed a correction or failed, where `/self_evaluate` recorded none of the causes A, B, D or "
                    "E, as a share of all such sessions.", None, "other"),
    Metric("defect_rework", "Rated agents with defects or rework", "pct", MIN_CMP_AGENTS, "lower", True, None,
           "Dispatched agents whose rating was defects or rework, as a share of rated agents, all models pooled. "
           "The Agents tab shows each model at each effort.", None),
    Metric("unrated", "Unrated agents", "pct", MIN_CMP_AGENTS, "lower", True, None,
           "Dispatched agents nobody has rated, as a share of agents open to rating; agents sampled out of rating are "
           "not counted.", None),
)


def point(snapshot, key):
    """One month's value for a metric: {"value", "n", "count", "unfav", "rows"}, or None when the month has no snapshot."""
    if snapshot is None:
        return None
    spec = {row.key: row for row in METRICS}[key]
    if spec.agent:
        return _agents_point(snapshot, key)
    return _sessions_point(snapshot, key, spec.cause)


def shift(delta, bad_rows_delta, better):
    """`flat` inside the trend band or under the row floor, else `better` or `worse` for a metric where `better` is
    `higher` or `lower`."""
    if abs(delta) <= TREND_POINTS or abs(bad_rows_delta) < MIN_CMP_ROWS:
        return "flat"
    return "better" if (delta > 0) == (better == "higher") else "worse"


def _marker(spec, cur, prior, prior_snapshot):
    if cur is None or cur["value"] is None:
        return "no data"
    if prior is None:
        return "no prior month"
    if cur["n"] < spec.min_n or prior["n"] < spec.min_n:
        return "low N"
    if spec.agent and prior_snapshot.get("backfilled"):
        return REBUILT
    if spec.better is None:
        return None
    return shift(round(cur["value"] - prior["value"], 6), cur["unfav"] - prior["unfav"], spec.better)


def _delta(cur, prior):
    if cur is None or prior is None or cur["value"] is None or prior["value"] is None:
        return None
    return round(cur["value"] - prior["value"], 6)


def metric_rows(current, prior):
    """The comparison table of one snapshot against another, from the snapshots alone."""
    rows = []
    for spec in METRICS:
        cur, old = point(current, spec.key), point(prior, spec.key)
        rows.append({"key": spec.key, "label": spec.label, "gloss": spec.gloss, "definition": spec.definition,
                     "kind": spec.kind, "min_n": spec.min_n, "better": spec.better, "agent": spec.agent,
                     "current": cur, "prior": old, "delta": _delta(cur, old), "marker": _marker(spec, cur, old, prior)})
    return rows


def problem_cells(snapshot):
    """The month's (model, effort) cells whose rated agents came back with defects or rework at the floor rule's
    thresholds (`FLOOR_MIN_FALSIFIED` or more among `FLOOR_MIN_ROWS` or more rated), worst share first. Unrated agents
    and agents with no readable outcome are not rated."""
    out = []
    for cell in snapshot["agents"]["cells"]:
        rated = sum(cell[name] for name in OUTCOMES)
        falsified = cell["defects"] + cell["rework"]
        if rated >= FLOOR_MIN_ROWS and falsified >= FLOOR_MIN_FALSIFIED:
            out.append({"model": cell["model"], "effort": cell["effort"], "falsified": falsified, "rated": rated})
    return sorted(out, key=lambda c: (-c["falsified"] / c["rated"], -c["rated"], c["model"], c["effort"]))


def paid_spend(snapshot):
    """(dollars, runs) spent on pay-per-token routes in a month, or None when no run there was priced above zero."""
    paid = snapshot["agents"].get("paid")
    return (paid["usd"], paid["n"]) if paid and paid["n"] else None


def corrections_per_prompt(snapshot):
    """A month's corrections per 100 owner prompts, from the optional sums in its stored sessions block
    (S3/D2: shown beside the clean share, replacing nothing), or None when the month stores none — a month
    written before `facts` existed, or one whose rows carried no `facts.owner_prompts`, is typed absent,
    never a zero rate. `sessions` is the rows that carried both halves, the population the value divides."""
    sessions = (snapshot or {}).get("sessions") or {}
    corrections, prompts, rows = (sessions.get("corrections"), sessions.get("prompts"), sessions.get("facts_rows"))
    if not all(_is_count(value) for value in (corrections, prompts, rows)) or not rows:
        return None
    return {"value": 100.0 * corrections / prompts if prompts else None,
            "count": corrections, "prompts": prompts, "sessions": rows}


def closed_months(months):
    return sorted((m for m, snapshot in months.items() if snapshot.get("closed")), reverse=True)


def compare(months, today):
    """The current month against the last closed month, plus a 24-month series per metric."""
    current = today[:7]
    earlier = [m for m in closed_months(months) if m < current]
    prior = earlier[0] if earlier else None
    rows = metric_rows(months.get(current), months.get(prior) if prior else None)
    span = _months_to(current, SERIES_MONTHS)
    series = {}
    for spec in METRICS:
        entries = []
        for month in span:
            cell = point(months.get(month), spec.key)
            usable = cell is not None and cell["value"] is not None
            entries.append({"month": month, "present": cell is not None, "value": cell["value"] if usable else None,
                            "n": cell["n"] if cell else 0, "count": cell["count"] if usable else None,
                            "unfav": cell["unfav"] if cell else None, "low": not usable or cell["n"] < spec.min_n})
        series[spec.key] = entries
    return {"rows": rows, "series": series, "current": current, "prior": prior, "closed": closed_months(months)}


def closed_pairs(months):
    """(month, snapshot, previous stored snapshot or None) for each closed month, oldest first."""
    ordered = sorted(months)
    for index, month in enumerate(ordered):
        if months[month].get("closed"):
            yield month, months[month], months[ordered[index - 1]] if index else None
