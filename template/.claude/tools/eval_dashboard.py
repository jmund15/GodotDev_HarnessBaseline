#!/usr/bin/env python3
"""Self-evaluation dashboard: one pass over the archives writes the note, the offline report and the history.

  python3 .claude/tools/eval_dashboard.py [--root DIR] [--vault DIR] [--today YYYY-MM-DD] [--refreeze YYYY-MM]
                                          [--verdict FILE] [--rules-json FILE] [--sessions-root DIR]

Exit codes: 0 published, 1 write or vault failure, 2 unreadable input, invalid argument or invalid verdict,
3 published with a stale or unparseable input listed (an absent optional producer does not degrade),
4 history refused (the stored history file is untouched; the note and report are still written).
--verdict is removed after exit 0 or 3 and kept after 1, 2 and 4.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import analyze_eval_archive  # noqa: E402
import escape_log  # noqa: E402
import eval_history  # noqa: E402
import eval_findings  # noqa: E402
import eval_render  # noqa: E402
import model_registry  # noqa: E402
import model_tier_readout  # noqa: E402
import orchestration_metrics  # noqa: E402
import review_calibration  # noqa: E402
import review_ledger  # noqa: E402

VAULT = os.path.join(r"{{VAULT_ROOT}}", "DevProjects", "{{PROJECT_NAME}}", "Claude", "Meta")
NOTE_NAME = "Self Evaluation Dashboard.md"
REPORT_NAME = "EVAL-DASHBOARD.html"
ARCHIVE_DIR = "Eval Dashboard Archive"
ROOT = os.path.dirname(os.path.dirname(TOOLS))
ORCHESTRATION_REL = os.path.join(".claude", "orchestration_metrics.jsonl")
SELF_EVAL_REL = ".claude/self_evaluate_archive.json"
ROUTING_REL = ".claude/logs/routing_audit_stats.json"
REGISTRY_REL = ".claude/reference/external_models.json"
RULES_REL = ".claude/scratch/eval_out/rule_retirement.json"
STALE_DAYS = 7
DEGRADED = ("stale", "unparseable")
TABS = ("overview", "trend", "sessions", "agents", "ladder", "log", "inputs", "review", "findings")
VERDICT_KEYS = ("month", "state", "moves", "watch", "layers", "milestone", "profile")
LAYERS = ("sessions", "guards", "cascades", "pins", "ladder", "review")
STATE_MAX = 300
MOVES_MAX = 5
WATCH_MAX = 3
PROFILE_MAX = 5


class InputError(ValueError):
    """An argument file or field that cannot be used; the message names it."""


def _one_line(value):
    """A non-empty string that holds no line break: the note renders it inside a callout line or a table cell."""
    return isinstance(value, str) and bool(value) and not any(mark in value for mark in ("\n", "\r"))


def _strings(field, value, limit):
    if not isinstance(value, list) or len(value) > limit or not all(_one_line(v) for v in value):
        raise InputError("verdict: %s must be a list of at most %d non-empty single-line strings" % (field, limit))
    return value


def check_verdict(doc, month):
    """The validated verdict, with `moves` and `watch` defaulted. Raises InputError naming the field."""
    if not isinstance(doc, dict):
        raise InputError("verdict: the file must hold one JSON object")
    unknown = sorted(set(doc) - set(VERDICT_KEYS))
    if unknown:
        raise InputError("verdict: unknown key %r; allowed: %s" % (unknown[0], ", ".join(VERDICT_KEYS)))
    if doc.get("month") != month:
        raise InputError("verdict: month %r must equal the month of this run, %r" % (doc.get("month"), month))
    state = doc.get("state")
    if not _one_line(state) or len(state) > STATE_MAX:
        raise InputError("verdict: state must be a single-line string of 1 to %d characters" % STATE_MAX)
    out = dict(doc, moves=doc.get("moves", []), watch=_strings("watch", doc.get("watch", []), WATCH_MAX))
    moves = out["moves"]
    if not isinstance(moves, list) or len(moves) > MOVES_MAX:
        raise InputError("verdict: moves must be a list of at most %d entries" % MOVES_MAX)
    for index, move in enumerate(moves):
        if not isinstance(move, dict):
            raise InputError("verdict: moves[%d] must have exactly do, why and see" % index)
        if ("finding" in move) != ("metric" in move):
            raise InputError("verdict: moves[%d] must carry finding and metric together, or neither" % index)
        if set(move) not in ({"do", "why", "see"}, {"do", "why", "see", "finding", "metric"}):
            raise InputError("verdict: moves[%d] must have exactly do, why and see" % index)
        for key in ("do", "why"):
            if not _one_line(move[key]):
                raise InputError("verdict: moves[%d].%s must be a non-empty single-line string" % (index, key))
        if move["see"] not in TABS:
            raise InputError("verdict: moves[%d].see %r must be one of %s" % (index, move["see"], ", ".join(TABS)))
        if "finding" in move:
            found = move["finding"]
            if not isinstance(found, list) or not found or not all(_one_line(item) for item in found):
                raise InputError("verdict: moves[%d].finding must be a non-empty list of non-empty single-line strings"
                                 % index)
            if not _one_line(move["metric"]):
                raise InputError("verdict: moves[%d].metric must be a non-empty single-line string" % index)
    if "milestone" in out and not _one_line(out["milestone"]):
        raise InputError("verdict: milestone must be a non-empty single-line string")
    if "layers" in out:
        layers = out["layers"]
        if not isinstance(layers, dict) or not layers or not set(layers) <= set(LAYERS):
            raise InputError("verdict: layers keys must be among %s" % ", ".join(LAYERS))
        for name, text in layers.items():
            if not _one_line(text) or not (text == "move" or (text.startswith("no move: ") and text[len("no move: "):].strip())):
                raise InputError('verdict: layers.%s must be "move" or "no move: <evidence>" on one line' % name)
        if not moves and "move" in layers.values():
            raise InputError("verdict: layers names a move but moves is empty")
    if "profile" in out:
        profile = out["profile"]
        if not isinstance(profile, dict) or set(profile) != {"strengths", "weaknesses"}:
            raise InputError("verdict: profile must have exactly strengths and weaknesses")
        for key in profile:
            _strings("profile.%s" % key, profile[key], PROFILE_MAX)
    return out


def check_citations(verdict, findings):
    """Every finding id and metric key a move cites must be one this run computed. Raises InputError naming the move."""
    for index, move in enumerate(verdict.get("moves", [])):
        if "metric" not in move:
            continue
        if findings is None:
            raise InputError("verdict: moves[%d] cites findings but this run computed none" % index)
        for finding in move["finding"]:
            if finding not in findings.get("findings", {}):
                raise InputError("verdict: moves[%d].finding %r is not a finding of this run" % (index, finding))
        if move["metric"] not in findings.get("metrics", {}):
            raise InputError("verdict: moves[%d].metric %r is not a metric of this run" % (index, move["metric"]))


def _load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise InputError("%s file %s cannot be read: %s" % (what, path, exc.strerror or exc))
    except ValueError as exc:
        raise InputError("%s file %s is not valid JSON: %s" % (what, path, exc))


def load_verdict(path, month):
    return check_verdict(_load_json(path, "verdict"), month)


def load_rules(path):
    """The `rule_retirement --json` feed; raises InputError naming the file when it cannot serve."""
    feed = _load_json(path, "rules")
    if not isinstance(feed, dict) or not all(isinstance(feed.get(key), list) for key in ("fired", "undecidable", "malformed")):
        raise InputError("rules file %s must hold fired, undecidable and malformed lists" % path)
    return feed


def _is_day(text):
    """True for a real calendar day written exactly YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _days(later, earlier):
    return max(0, (datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier[:10])).days)


def read_routing(root, today):
    """(inputs row, card data) for the routing audit stats: absent when the file is missing, unparseable when it
    lacks a generated_at date and a total, stale when older than STALE_DAYS."""
    row = {"source": "routing audit", "path": ROUTING_REL, "rows": 0, "first": None, "last": None, "age_days": None}
    path = os.path.join(root, *ROUTING_REL.split("/"))
    if not os.path.isfile(path):
        return dict(row, status="absent"), None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        day = data["generated_at"][:10]
        card = {"generated_at": data["generated_at"], "total_entries": int(data["total_entries"]),
                "silent_misses": int(data.get("silent_misses", 0)), "active_window_days": data.get("active_window_days"),
                "by_week": dict(data.get("by_week") or {}),
                "top_5_silent_miss_rules": list(data.get("top_5_silent_miss_rules") or [])}
        age = _days(today, day)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return dict(row, status="unparseable", detail=str(exc)[:120]), None
    return dict(row, rows=card["total_entries"], last=day, age_days=age,
                status="stale" if age > STALE_DAYS else "ok"), card


def rules_row(path, feed, today):
    if feed is None:
        return {"source": "rules feed", "path": RULES_REL, "rows": 0, "first": None, "last": None, "age_days": None,
                "status": "absent"}
    day = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    age = _days(today, day)
    return {"source": "rules feed", "path": path.replace(os.sep, "/"), "rows": int(feed.get("rules") or 0),
            "first": None, "last": day, "age_days": age, "status": "stale" if age > STALE_DAYS else "ok"}


def load_registry():
    """(registry data or None, inputs row): the registry the ladder readout and the Review tab read. It is
    unparseable when it cannot be loaded, which degrades the run."""
    row = {"source": "model registry", "path": REGISTRY_REL, "rows": 0, "first": None, "last": None, "age_days": None}
    try:
        data = model_registry.load()
    except model_registry.RegistryError as exc:
        return None, dict(row, status="unparseable", detail=str(exc)[:160])
    return data, dict(row, rows=len(data["models"]), status="ok")


def ledger_inputs(facts):
    """The lesson-outcome ledger the Findings tab links and measures: absent is optional, unreadable lines degrade."""
    ledger = ((facts.get("findings") or {}).get("diagnostics") or {}).get("ledger")
    if ledger is None:
        return []
    row = {"source": "lesson-outcome ledger", "path": ".claude/autolearn_outcomes.jsonl", "rows": ledger.get("rows", 0),
           "first": ledger.get("first"), "last": ledger.get("last"),
           "age_days": _days(facts["today"], ledger["last"]) if ledger.get("last") else None, "status": "ok"}
    if ledger.get("absent"):
        return [dict(row, status="absent")]
    if ledger.get("unparseable"):
        return [dict(row, status="unparseable", detail="%d unreadable line(s)" % ledger["unparseable"])]
    return [row]


def denials_inputs(facts):
    """The transcript scan behind the denials metrics: roots scanned, files read and each missing root. No files read is
    `absent` (the metrics publish nothing), which does not degrade the run."""
    cov = ((facts.get("findings") or {}).get("diagnostics") or {}).get("denials")
    if cov is None:
        return []
    gaps = cov.get("gaps") or []
    detail = "%d root(s) scanned, %d file(s) read" % (cov.get("roots", 0), cov.get("files", 0))
    if gaps:
        detail += "; missing root(s): " + ", ".join(gaps)
    return [{"source": "denials scan", "path": "session transcripts", "rows": cov.get("files", 0), "first": None,
             "last": None, "age_days": None, "status": "ok" if cov.get("files") else "absent", "detail": detail[:400]}]


def _days_span(stamps, today):
    days = sorted(str(stamp)[:10] for stamp in stamps if stamp)
    return (days[0], days[-1], _days(today, days[-1])) if days else (None, None, None)


def review_inputs(root, today):
    """(escape rows, escape file bytes or None, escape-log inputs row, ledger inputs row). Both producers are
    optional: a file that does not exist is `absent` and does not degrade the run; a ledger or escape log that exists and
    cannot be read in full, corrupt lines included, is `unparseable`."""
    escape_path = review_ledger.escape_log_path(pathlib.Path(root))
    ledger_path = review_ledger.ledger_path(pathlib.Path(root))

    def shown(path):
        try:
            return pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
        except ValueError:
            return str(path).replace(os.sep, "/")

    def row(source, path, stamps, status, **extra):
        first, last, age = _days_span(stamps, today)
        return dict({"source": source, "path": shown(path), "rows": len(stamps), "first": first, "last": last,
                     "age_days": age, "status": status}, **extra)

    escapes = escape_log.rows(str(escape_path)) if escape_path.is_file() else []
    size = escape_path.stat().st_size if escape_path.is_file() else None
    if size is None:
        escape_row = row("escape log", escape_path, [], "absent")
    else:
        _rows, escape_problem = review_ledger.read_escapes(pathlib.Path(root))
        escape_row = row("escape log", escape_path, [r.get("ts") for r in escapes],
                         "unparseable" if escape_problem else "ok",
                         **({"detail": str(escape_problem)[:160]} if escape_problem else {}))
    if not ledger_path.is_file():
        return escapes, size, escape_row, row("review ledger", ledger_path, [], "absent")
    rows, corrupt, problem = review_ledger.read_rows(ledger_path)
    if problem:
        return escapes, size, escape_row, row("review ledger", ledger_path, [], "unparseable", detail=str(problem)[:160])
    if corrupt:
        return escapes, size, escape_row, row("review ledger", ledger_path, [r.get("ts") for r in rows], "unparseable",
                                              detail="%d corrupt line(s)" % corrupt)
    return escapes, size, escape_row, row("review ledger", ledger_path, [r.get("ts") for r in rows], "ok")


def ledger_row(root, agents, diagnostics, today):
    """The orchestration ledger: absent without its file, unparseable when any line could not be read."""
    row = {"source": "orchestration ledger", "path": ORCHESTRATION_REL.replace(os.sep, "/"), "rows": agents["rows"],
           "first": agents["first"], "last": agents["last"],
           "age_days": _days(today, agents["last"]) if agents["last"] else None}
    if diagnostics:
        first = diagnostics[0]
        return dict(row, status="unparseable", detail="%d unreadable: %s%s" % (
            len(diagnostics), first.get("status", "unknown"), " (%s)" % first["detail"] if first.get("detail") else ""))
    return dict(row, status="ok" if os.path.isfile(os.path.join(root, ORCHESTRATION_REL)) else "absent")


def build_inputs(root, facts, today, rules, routing, history):
    """One row per producer, in a fixed order. Only stale and unparseable rows degrade the run."""
    dates = [day for day in (analyze_eval_archive.calendar_day(item["date"]) for item in facts["stats"].get("timeline", []))
             if day]
    agents = facts["agents"]
    months = sorted(facts["months"])
    history_row = {"source": "history", "path": eval_history.HISTORY_REL.replace(os.sep, "/"), "rows": len(months),
                   "first": months[0] if months else None, "last": months[-1] if months else None,
                   "age_days": None, "status": history["status"]}
    if history.get("detail"):
        history_row["detail"] = history["detail"]
    return [
        {"source": "self-evaluate archive", "path": SELF_EVAL_REL, "rows": facts["stats"]["total_sessions"],
         "first": min(dates) if dates else None, "last": max(dates) if dates else None,
         "age_days": _days(today, max(dates)) if dates else None, "status": "ok"},
        facts["ledger"], facts["registry"],
        routing, rules, *facts["review_inputs"], *([facts["findings_error"]] if facts.get("findings_error") else []),
        *ledger_inputs(facts), *denials_inputs(facts), history_row,
    ]


def build_facts(root, today, run_sessions=None):
    """Everything the renderers and the history read, computed once. `run_sessions` maps a run id to its
    session directory for archived rows that predate the `session_id` field."""
    stats = analyze_eval_archive.compute(root)
    found = {}
    for month in analyze_eval_archive.dated_months(stats):
        block = analyze_eval_archive.compute(root, month=month)
        found[month] = {"self_eval": {"rows": block["unique_count"], "first": block["date_first"][:10],
                                      "last": block["date_last"][:10]}, "sessions": block["sessions"]}
    diagnostics = []
    rows, side, _ = orchestration_metrics.archive_rows(diagnostics, archive=os.path.join(root, ORCHESTRATION_REL))
    dated = {day[:7] for day in (orchestration_metrics.row_date(r) for r in rows + side) if day}
    run_sessions = run_sessions or {}

    def readout(subset):
        return model_tier_readout.cells(subset, run_sessions=run_sessions, outcome_of=orchestration_metrics.outcome_of)

    months = {}
    for month in sorted(set(found) | dated):
        agents = orchestration_metrics.archive_facts(rows, side, month=month)
        sessions = found.get(month)
        in_month = [r for r in rows + side if (orchestration_metrics.row_date(r) or "")[:7] == month]
        block, ladder = eval_history.agents_block(agents), eval_history.ladder_block(readout(in_month))
        months[month] = {
            "month": month,
            "sources": {"self_eval": sessions["self_eval"] if sessions else {"rows": 0, "first": None, "last": None},
                        "orchestration": eval_history.orchestration_source(agents, open_month=month == today[:7])},
            "sessions": sessions["sessions"] if sessions else analyze_eval_archive.empty_sessions(),
            "agents": block, "ladder": ladder,
            "attention": eval_history.attention_rows(
                ladder=ladder, floor_cells=agents["floor_cells"], candidates=agents["candidates"],
                skill_trends=stats["skill_trends"] if month == today[:7] else ())}
    overall = orchestration_metrics.archive_facts(rows, side)
    overall["unrated_reasons"] = unrated_reasons(rows + side)
    registry, registry_input = load_registry()
    escapes, escape_bytes, escape_input, ledger_input = review_inputs(root, today)
    review = review_calibration.build(escapes=escapes, escape_bytes=escape_bytes, yield_rows=rows + side,
                                      registry=registry or {"models": []}, root=root, today=today)
    facts = {"today": today, "month": today[:7], "stats": stats, "months": months, "agents": overall,
            "ladder": readout(rows + side), "ledger": ledger_row(root, overall, diagnostics, today),
            "registry": registry_input, "review": review, "review_inputs": [escape_input, ledger_input],
            "effort_fit_rows": rows + side}
    try:
        findings_src = eval_findings.load(root)
        facts["findings"] = eval_findings.compute(findings_src, today=today, since=eval_findings.dashboard_since(today))
        facts["effects"] = eval_findings.effects(findings_src, today=today)
        facts["trends"] = eval_findings.trends(findings_src, today=today)
    except (ValueError, OSError) as exc:
        facts["findings"] = facts["effects"] = facts["trends"] = None
        facts["findings_error"] = {"source": "findings", "path": SELF_EVAL_REL, "rows": 0, "first": None, "last": None,
                                   "age_days": None, "status": "unparseable", "detail": str(exc)[:160]}
    return facts


def unrated_reasons(rows):
    """Why the archive's unrated rows are unrated, read at collection and never stored (the Agents tab's
    footer and `orchestration_metrics.py --archive-summary` show the same thing). `sampled-out` is left
    out because the coverage line above it already reports the deliberate exclusions, and a row whose
    reason is None is plain rating debt, not one of the named reasons. Reasons come from
    `orchestration_metrics.unrated_reason`, never re-derived here."""
    by_session = {}
    for row in rows:
        by_session.setdefault(row.get('session_id') or row.get('run'), []).append(row)
    counts = {}
    for row in rows:
        if orchestration_metrics.outcome_of(row) != 'unrated':
            continue
        reason = orchestration_metrics.unrated_reason(row, by_session.get(row.get('session_id') or row.get('run'), []))
        if reason and reason != 'sampled-out':
            counts[reason] = counts.get(reason, 0) + 1
    return {name: counts[name] for name in orchestration_metrics.UNRATED_REASONS if name in counts}


def effort_fit_rollup(rows):
    """({(model, role, effort): {under, over}}, count of rated rows with no effort miss). The role is the
    tier a row records, else its job shape, else '?'. A rated row is one whose outcome is not unrated; a legacy
    `fit` value counts as no miss."""
    groups, missing = {}, 0
    for row in rows:
        if orchestration_metrics.outcome_of(row) == 'unrated':
            continue
        fit = row.get('effort_fit')
        if fit not in orchestration_metrics.EFFORT_FITS:
            missing += 1
            continue
        key = (row.get('model') or '?', row.get('tier') or row.get('shape') or '?', row.get('effort') or '?')
        groups.setdefault(key, {name: 0 for name in orchestration_metrics.EFFORT_FITS})[fit] += 1
    return groups, missing


def effort_fit_lines(rows):
    """The printed effort-fit rollup: one line per group, then the rated rows that carry no effort miss."""
    groups, missing = effort_fit_rollup(rows)
    lines = ['effort fit (model, tier or shape, effort):']
    for key in sorted(groups):
        counts = groups[key]
        lines.append(f"  {key[0]:<24} {key[1]:<10} {key[2]:<7} "
                     + "  ".join(f"{name} {counts[name]}" for name in orchestration_metrics.EFFORT_FITS))
    if not groups:
        lines.append('  no rated row carries an effort miss')
    lines.append(f"  rated rows with no effort miss: {missing}")
    return lines


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT, help="repo root holding .claude/")
    parser.add_argument("--vault", default=VAULT, help="vault folder that receives the note and the report")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD; default the local date")
    parser.add_argument("--refreeze", action="append", default=[], metavar="YYYY-MM",
                        help="rewrite a closed month from the live sources (repeatable)")
    parser.add_argument("--verdict", default=None, metavar="FILE",
                        help="verdict.json for this run's month; removed after a successful publish")
    parser.add_argument("--rules-json", default=None, metavar="FILE", help="rule_retirement.py --json output")
    parser.add_argument("--sessions-root", default=None, metavar="DIR",
                        help="directory of session dirs (each with workflows/<run>.json) that resolves ledger rows "
                             "recorded before session_id; default: this project's Claude Code sessions, matched from the cwd")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    today = args.today or datetime.date.today().isoformat()
    if not _is_day(today):
        print("--today must be a real date, YYYY-MM-DD: %r" % today)
        return 2
    bad = [month for month in args.refreeze if not _is_day(month + "-01")]
    if bad:
        print("--refreeze must be YYYY-MM: %r" % bad)
        return 2
    if args.sessions_root and not os.path.isdir(args.sessions_root):
        print("--sessions-root is not a directory: %s" % args.sessions_root)
        return 2
    try:
        verdict = load_verdict(args.verdict, today[:7]) if args.verdict else None
        rules_feed = load_rules(args.rules_json) if args.rules_json else None
    except InputError as exc:
        print(exc)
        return 2
    if not os.path.isdir(args.vault):
        print("vault folder does not exist: %s" % args.vault)
        return 1
    try:
        facts = build_facts(args.root, today, orchestration_metrics._session_of_run(args.sessions_root))
    except analyze_eval_archive.AnalysisError as exc:
        print("UNKNOWN: %s" % exc)
        return 2
    if verdict is not None:
        try:
            check_citations(verdict, facts.get("findings"))
        except InputError as exc:
            print(exc)
            return 2
    if args.refreeze and facts["registry"]["status"] != "ok":
        print("--refreeze needs a readable model registry, or a closed month would lose its ladder: %s"
              % facts["registry"].get("detail", "unreadable"))
        return 2
    facts["rules_feed"] = rules_feed
    facts["rules"] = {key: len(rules_feed[key]) for key in ("fired", "malformed", "undecidable")} if rules_feed else None
    routing_row, facts["routing"] = read_routing(args.root, today)
    code = 0
    try:
        result = eval_history.publish(args.root, facts, verdict, today, refreeze=tuple(args.refreeze))
        months, actions = result["months"], result["actions"]
        facts["history"] = {"status": "ok"}
    except eval_history.HistoryRefusedError as exc:
        print("history refused: %s" % exc)
        months, actions, code = {m: dict(c, closed=False, backfilled=False, verdict=None)
                                 for m, c in facts["months"].items()}, {}, 4
        facts["history"] = {"status": "unparseable", "detail": str(exc)}
    except eval_history.HistoryWriteError as exc:
        print("history write failed: %s" % exc)
        return 1
    facts["months"] = months
    facts["inputs"] = build_inputs(args.root, facts, today,
                                   rules_row(args.rules_json, rules_feed, today), routing_row, facts["history"])
    if code == 0 and any(item["status"] in DEGRADED for item in facts["inputs"]):
        code = 3
    shown = verdict or (months.get(today[:7]) or {}).get("verdict")
    report_path = os.path.join(args.vault, REPORT_NAME)
    note_path = os.path.join(args.vault, NOTE_NAME)
    view = eval_history.compare(months, today)
    try:
        _write(report_path, eval_render.report(facts, months, view, shown))
        _write(note_path, eval_render.note(facts, view, shown, pathlib.Path(os.path.abspath(report_path)).as_uri()))
        print("wrote %s" % note_path)
        print("wrote %s" % report_path)
        if code != 4:
            for month, snapshot, prev in eval_history.closed_pairs(months):
                _write(os.path.join(args.vault, ARCHIVE_DIR, "Eval Dashboard %s.md" % month),
                       eval_render.month_note(snapshot, prev))
            print("month notes: %d in %s" % (len(view["closed"]), ARCHIVE_DIR))
            print("history: %s" % ", ".join("%s %s" % item for item in sorted(actions.items())))
    except OSError as exc:
        print("write failed: %s" % exc)
        return 1
    for line in effort_fit_lines(facts["effort_fit_rows"]):
        print(line)
    for item in facts["inputs"]:
        if item["status"] in DEGRADED:
            print("input %s: %s" % (item["source"], item["status"]))
    if args.verdict and code in (0, 3):
        os.remove(args.verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
