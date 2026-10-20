#!/usr/bin/env python3
"""How much of each Anthropic plan window a unit of list-price-weighted token use drains, per family.

    plan_usage_calc.py fit   [--window 5h|7d] [--days N]
    plan_usage_calc.py price <journal.json | stream.jsonl | transcript.jsonl>
    plan_usage_calc.py drift [--window 5h|7d]
    common: [--projects DIR] [--ledger PATH] [--registry PATH] [--now EPOCH]

Inputs. The window samples come from tools/usage_samples.py (HARNESS_USAGE_SAMPLES overrides the
file). Token use comes from every host-transport transcript under ~/.claude/projects through
tools/transcript_usage.py; proxied and non-host sidecar sessions are excluded and counted. List
prices come from the registry rows (reference/external_models.json, via tools/model_registry.py):
input, output, cache read, and 5-minute and 1-hour cache writes. A model with no registry row or no
complete price is reported unpriced and never defaulted.

Weights, not dollars. A call's weight is its list price, which carries each token type's relative
cost; the fit turns weight into plan-window percent. The per-token-type figures are the list-price
ratios, never fitted: the samples cannot resolve four or five coefficients per family. The tool
prints no dollar figure for this plan-quota transport; `price` prints tokens and window percent.

Fit. Within each reset period the cumulative `used` level is regressed, through the origin, on the
cumulative weight per family since the period began. Levels, not interval deltas: consecutive
deltas share endpoints, so their errors correlate. One coefficient set is shared by every period.
A drop in `used` inside one period means the window rolls instead of resetting in fixed blocks,
which voids that window's fit. A family's standard error is a bootstrap over reset periods. A
family under MIN_SHARE of the spend, or collinear with the others (VIF above MAX_VIF), prints
`unidentified`. Fewer than MIN_PERIODS reset periods print `insufficient data` and no number.

The fit is a local-attribution upper bound: use off this machine drains the same window, leaves no
local record, and lands in the coefficients as unmodelled residual.

Drift compares the last DRIFT_RECENT_DAYS of reset periods with the DRIFT_PRIOR_DAYS before them
and flags a coefficient move beyond 2*sqrt(se1^2 + se2^2); each segment needs MIN_PERIODS periods.
"""
import argparse
import bisect
import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcript_usage  # noqa: E402
import usage_samples  # noqa: E402

PROVIDER = "anthropic"
WINDOWS = {"5h": ("five_hour", 5 * 3600), "7d": ("seven_day", 7 * 86400)}
MIN_PERIODS = 5
MIN_SHARE = 0.10
MAX_VIF = 10.0
BOOTSTRAP_ROUNDS = 200
DRIFT_RECENT_DAYS = 7
DRIFT_PRIOR_DAYS = 21
DEFAULT_DAYS = usage_samples.RETENTION_DAYS
RATE_FIELDS = (("inp", "cacheMissPer1M"), ("out", "outputPer1M"), ("cr", "cacheHitPer1M"),
               ("cw5m", "cacheWrite5mPer1M"), ("cw1h", "cacheWrite1hPer1M"))
TOKEN_LABELS = (("inp", "input"), ("out", "output"), ("cr", "cache read"),
                ("cw5m", "cache write 5m"), ("cw1h", "cache write 1h"))
UPPER_BOUND_NOTE = ("note: a local-attribution upper bound. Use off this machine (claude.ai, desktop, "
                    "mobile, other machines) drains the same window, leaves no local record, and "
                    "enters the fit as an unmodelled residual.")


# ------------------------------------------------------------------------------------ pricing

def _registry(path=None):
    import model_registry
    return model_registry.load(path) if path else model_registry.load()


def price_for(model, data):
    """(family, price dict) for a served model id, or (None, reason) when it is unpriced."""
    import model_registry
    row = model_registry.row_by_id(model, data)
    if row is None:
        return None, "not in the registry"
    price = row.get("price")
    if not isinstance(price, dict):
        return None, "no registry price"
    missing = [field for _k, field in RATE_FIELDS if not isinstance(price.get(field), (int, float))]
    if missing:
        return None, "registry price lacks " + ", ".join(missing)
    return row.get("family") or row["id"], price


def tokens(call):
    """{type: count}; a cache write with no 5m/1h split lands in `unsplit_cw`, never assigned a duration."""
    t = {k: call.get(k) or 0 for k, _f in RATE_FIELDS}
    cw = call.get("cw") or 0
    split = (call.get("cw5m") or 0) + (call.get("cw1h") or 0)
    t["unsplit_cw"] = max(cw - split, 0) if call.get("cw5m") is not None or call.get("cw1h") is not None else cw
    return t


def weigh(calls, data):
    """(events [(ts, family, weight)], unpriced {model: [calls, reason]}, tokens and weight by family).

    Weight sums per call, because one family's versions can carry different rates."""
    events, unpriced, by_family = [], {}, {}
    for c in calls:
        model = c.get("model") or "?"
        family, price = price_for(model, data)
        t = tokens(c)
        if family is not None and t["unsplit_cw"]:
            family, price = None, "cache write carries no 5m/1h split"
        if family is None:
            entry = unpriced.setdefault(model, [0, price])
            entry[0] += 1
            continue
        import model_registry
        weight = model_registry.price_requests(model, [c], data=data, multiplier=1.0)
        agg = by_family.setdefault(family, dict({k: 0 for k, _l in TOKEN_LABELS}, weight=0.0))
        for k, _l in TOKEN_LABELS:
            agg[k] += t[k]
        agg["weight"] += weight
        if c.get("ts") is not None:
            events.append((c["ts"], family, weight))
    events.sort()
    return events, unpriced, by_family


# ------------------------------------------------------------------------------ linear algebra

def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def lstsq(cols, y, tol=1e-9):
    """Least squares through the origin by modified Gram-Schmidt. A column that depends on the
    earlier ones gets None; its share of the fit moves to the columns it depends on."""
    basis, r = [], {}
    for j, col in enumerate(cols):
        v = [float(x) for x in col]
        norm0 = math.sqrt(_dot(v, v))
        for b, (_i, q) in enumerate(basis):
            coef = _dot(q, v)
            r[(b, j)] = coef
            v = [vi - coef * qi for vi, qi in zip(v, q)]
        norm = math.sqrt(_dot(v, v))
        if norm0 == 0 or norm <= tol * norm0:
            continue
        r[(len(basis), j)] = norm
        basis.append((j, [vi / norm for vi in v]))
    qty = [_dot(q, y) for _j, q in basis]
    beta = [None] * len(cols)
    for m in range(len(basis) - 1, -1, -1):
        j = basis[m][0]
        acc = qty[m] - sum(r.get((m, basis[l][0]), 0.0) * beta[basis[l][0]]
                           for l in range(m + 1, len(basis)))
        beta[j] = acc / r[(m, j)]
    return beta


def vif(cols, j):
    """Uncentered variance inflation of column j against the others; inf when it is dependent."""
    others = [c for i, c in enumerate(cols) if i != j]
    target = cols[j]
    sst = _dot(target, target)
    if sst == 0:
        return math.inf
    if not others:
        return 1.0
    beta = lstsq(others, target)
    fitted = [sum((b or 0.0) * c[i] for b, c in zip(beta, others)) for i in range(len(target))]
    ssr = sum((t - f) ** 2 for t, f in zip(target, fitted))
    return math.inf if ssr <= 1e-12 * sst else sst / ssr


# ----------------------------------------------------------------------------------------- fit

def periods_of(samples, window, window_seconds):
    """{reset key: {start, resets_at, samples}} for one provider window, samples sorted by ts."""
    out = {}
    for s in samples:
        if s.get("provider") != PROVIDER or s.get("window") != window:
            continue
        ts, used, reset = s.get("ts"), s.get("used"), s.get("resets_at")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (ts, used, reset)):
            continue
        key = int(round(reset / 60.0))
        p = out.setdefault(key, {"resets_at": reset, "start": reset - window_seconds, "samples": []})
        p["samples"].append((ts, used))
    for p in out.values():
        p["samples"].sort()
    return out


def rolling_reason(periods):
    for p in sorted(periods.values(), key=lambda p: p["resets_at"]):
        prev = None
        for ts, used in p["samples"]:
            if prev is not None and used < prev:
                return ("used fell from %g%% to %g%% inside the period resetting at %s: the window "
                        "rolls rather than resetting in fixed blocks" % (
                            prev, used, time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(p["resets_at"]))))
            prev = used
    return None


class Spend:
    """Cumulative weight per family over time-sorted events, read by bisection."""

    def __init__(self, events):
        self.times = [e[0] for e in events]
        self.families = sorted({e[1] for e in events})
        self.prefix = {f: [0.0] for f in self.families}
        for _ts, fam, weight in events:
            for f in self.families:
                self.prefix[f].append(self.prefix[f][-1] + (weight if f == fam else 0.0))

    def between(self, start, end):
        i, j = bisect.bisect_left(self.times, start), bisect.bisect_right(self.times, end)
        return {f: self.prefix[f][j] - self.prefix[f][i] for f in self.families}


def _rows(periods, spend, families):
    """Per period: [(used, {family: cumulative weight since the period began})] per sample."""
    out = []
    for p in periods:
        rows = []
        for ts, used in p["samples"]:
            cum = spend.between(p["start"], ts)
            rows.append((used, {f: cum.get(f, 0.0) for f in families}))
        out.append(rows)
    return out


def _solve(period_rows, families):
    ys = [u for rows in period_rows for u, _c in rows]
    cols = [[c[f] for rows in period_rows for _u, c in rows] for f in families]
    return dict(zip(families, lstsq(cols, ys))), cols


def fit_periods(periods, events, rounds=BOOTSTRAP_ROUNDS):
    """Coefficients for one set of reset periods; `status` is ok or insufficient."""
    periods = sorted(periods, key=lambda p: p["resets_at"])
    n_samples = sum(len(p["samples"]) for p in periods)
    result = dict(periods=len(periods), samples=n_samples, families={})
    if len(periods) < MIN_PERIODS:
        result["status"] = "insufficient"
        return result
    index = Spend(events)
    spend = {}
    for p in periods:
        for fam, w in index.between(p["start"], p["samples"][-1][0]).items():
            spend[fam] = spend.get(fam, 0.0) + w
    total = sum(spend.values())
    families = sorted((f for f in spend if spend[f] > 0), key=lambda f: -spend[f])
    if not families:
        result["status"] = "insufficient"
        result["reason"] = "no priced host spend inside the sampled periods"
        return result
    period_rows = _rows(periods, index, families)
    coefs, cols = _solve(period_rows, families)
    rng = random.Random(20260924)
    draws = {f: [] for f in families}
    for _ in range(rounds):
        pick = [period_rows[rng.randrange(len(period_rows))] for _p in period_rows]
        boot, _cols = _solve(pick, families)
        for f, v in boot.items():
            if v is not None:
                draws[f].append(v)
    for i, f in enumerate(families):
        share = spend[f] / total
        inflation = vif(cols, i)
        entry = dict(share=share, coef=coefs[f], vif=inflation,
                     se=statistics.stdev(draws[f]) if len(draws[f]) > 1 else None)
        if share < MIN_SHARE:
            entry["unidentified"] = "spend share %.0f%% < %.0f%%" % (100 * share, 100 * MIN_SHARE)
        elif coefs[f] is None or inflation > MAX_VIF:
            entry["unidentified"] = ("collinear with the other families (VIF %s > %g)"
                                     % ("inf" if math.isinf(inflation) else "%.1f" % inflation, MAX_VIF))
        result["families"][f] = entry
    result["status"] = "ok"
    return result


class Context:
    """The inputs one run reads: samples, host calls, registry, clock."""

    def __init__(self, projects=None, ledger=None, registry=None, now=None, registry_path=None):
        self.now = time.time() if now is None else now
        self.projects = projects
        self.ledger = ledger
        self.data = registry if registry is not None else _registry(registry_path)
        self.samples_file = usage_samples.samples_path()
        bad = {}
        self.samples = usage_samples.read(self.samples_file, bad)
        self.bad_samples = bad.get("lines", 0)
        self._scan = None

    def scan(self, since):
        if self._scan is None or self._scan[0] > since:
            self._scan = (since, transcript_usage.scan(self.projects, since=since,
                                                       ledger_path=self.ledger))
        return self._scan[1]


def _window_periods(ctx, window_key, days):
    window, seconds = WINDOWS[window_key]
    recent = [s for s in ctx.samples if isinstance(s.get("ts"), (int, float))
              and s["ts"] >= ctx.now - days * 86400]
    return window, seconds, periods_of(recent, window, seconds)


def _events(ctx, periods):
    since = min((p["start"] for p in periods), default=None)
    if since is None:
        return [], {}, None
    scan = ctx.scan(since)
    events, unpriced, _fam = weigh(scan["calls"], ctx.data)
    return events, unpriced, scan


def fit(ctx, window_key="5h", days=DEFAULT_DAYS):
    window, _seconds, periods = _window_periods(ctx, window_key, days)
    reason = rolling_reason(periods)
    events, unpriced, scan = _events(ctx, list(periods.values()))
    result = fit_periods(list(periods.values()), events) if not reason else dict(
        status="void", reason=reason, periods=len(periods),
        samples=sum(len(p["samples"]) for p in periods.values()), families={})
    result.update(window=window, window_key=window_key, days=days, unpriced=unpriced, scan=scan)
    return result


def _inputs_lines(ctx, result):
    lines = ["samples: %s, %d unparseable lines" % (ctx.samples_file, ctx.bad_samples)]
    scan = result.get("scan")
    if scan is None:
        lines.append("transcripts: 0 files scanned (no sampled period to cover)")
    else:
        lines.append("transcripts: %d files scanned, %d unparseable lines in %d files"
                     % (scan["files"], scan["unparseable_lines"], len(scan["unparseable_files"])))
        by = {}
        for t in scan["excluded"].values():
            by[t] = by.get(t, 0) + 1
        lines.append("excluded sessions (not host transport): %d%s" % (
            len(scan["excluded"]),
            " (" + ", ".join("%s %d" % kv for kv in sorted(by.items())) + ")" if by else ""))
    for model, (count, why) in sorted((result.get("unpriced") or {}).items()):
        lines.append("unpriced: %s, %d calls (%s)" % (model, count, why))
    return lines


def format_fit(ctx, result):
    head = "fit: window %s (%s), last %d days" % (result["window_key"], result["window"], result["days"])
    lines = [head]
    if result["status"] == "void":
        lines.append("void: " + result["reason"])
    elif result["status"] == "insufficient":
        lines.append("insufficient data (%d periods, %d samples)%s" % (
            result["periods"], result["samples"],
            ": " + result["reason"] if result.get("reason") else ""))
    else:
        lines.append("%d reset periods, %d samples" % (result["periods"], result["samples"]))
        for fam, e in sorted(result["families"].items(), key=lambda kv: -kv[1]["share"]):
            if e.get("unidentified"):
                lines.append("  %-8s unidentified (%s)" % (fam, e["unidentified"]))
            else:
                se = "%.4g" % e["se"] if e["se"] is not None else "n/a"
                lines.append("  %-8s %.4g %% per list-price dollar (se %s), spend share %.0f%%"
                             % (fam, e["coef"], se, 100 * e["share"]))
    lines.extend(_inputs_lines(ctx, result))
    lines.append(UPPER_BOUND_NOTE)
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- drift

def drift(ctx, window_key="5h"):
    window, seconds, periods = _window_periods(ctx, window_key, DRIFT_RECENT_DAYS + DRIFT_PRIOR_DAYS)
    reason = rolling_reason(periods)
    split = ctx.now - DRIFT_RECENT_DAYS * 86400
    recent = [p for p in periods.values() if p["resets_at"] > split]
    prior = [p for p in periods.values() if p["resets_at"] <= split]
    events, unpriced, scan = _events(ctx, list(periods.values()))
    out = dict(window=window, window_key=window_key, unpriced=unpriced, scan=scan, reason=reason,
               recent=None, prior=None, moves={})
    if reason:
        out["status"] = "void"
        return out
    out["recent"], out["prior"] = fit_periods(recent, events), fit_periods(prior, events)
    if "insufficient" in (out["recent"]["status"], out["prior"]["status"]):
        out["status"] = "insufficient"
        return out
    for fam in sorted(set(out["recent"]["families"]) | set(out["prior"]["families"])):
        a, b = out["recent"]["families"].get(fam), out["prior"]["families"].get(fam)
        if not a or not b or a.get("unidentified") or b.get("unidentified") \
                or a["se"] is None or b["se"] is None:
            out["moves"][fam] = dict(status="unidentified")
            continue
        limit = 2 * math.sqrt(a["se"] ** 2 + b["se"] ** 2)
        move = a["coef"] - b["coef"]
        out["moves"][fam] = dict(status="DRIFT" if abs(move) > limit else "stable",
                                 recent=a["coef"], prior=b["coef"], move=move, limit=limit)
    out["status"] = "ok"
    return out


def format_drift(ctx, out):
    lines = ["drift: window %s (%s), last %d days vs the %d before"
             % (out["window_key"], out["window"], DRIFT_RECENT_DAYS, DRIFT_PRIOR_DAYS)]
    if out["status"] == "void":
        lines.append("void: " + out["reason"])
    elif out["status"] == "insufficient":
        for name in ("recent", "prior"):
            seg = out[name]
            if seg["status"] == "insufficient":
                lines.append("%s segment: insufficient data (%d periods, %d samples)"
                             % (name, seg["periods"], seg["samples"]))
    else:
        for fam, m in out["moves"].items():
            if m["status"] == "unidentified":
                lines.append("  %-8s unidentified in a segment" % fam)
            else:
                lines.append("  %-8s %s: %.4g -> %.4g (move %+.4g, limit 2*sqrt(se1^2+se2^2) = %.4g)"
                             % (fam, m["status"], m["prior"], m["recent"], m["move"], m["limit"]))
    lines.extend(_inputs_lines(ctx, out))
    lines.append(UPPER_BOUND_NOTE)
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- price

def calls_of(path, ledger_path=None):
    """(calls, transport, sources) for a workflow journal (.json), a stream or a transcript (.jsonl)."""
    ledger = transcript_usage.ledger_transports(ledger_path)
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            journal = json.load(fh)
        rid = journal.get("runId") if isinstance(journal, dict) else None
        if not rid or not isinstance(journal.get("workflowProgress"), list):
            raise ValueError("%s is not a workflow journal (no runId/workflowProgress)" % path)
        session = os.path.dirname(os.path.dirname(os.path.abspath(path)))
        run_dir = os.path.join(session, "subagents", "workflows", rid)
        files = sorted(os.path.join(run_dir, n) for n in (os.listdir(run_dir) if os.path.isdir(run_dir) else [])
                       if n.startswith("agent-") and n.endswith(".jsonl"))
        reads = [transcript_usage.read_transcript(f, ledger) for f in files]
        session_id = os.path.basename(session)
        transport = transcript_usage.session_transport(
            session_id, set().union(*(r["models"] for r in reads)) if reads else set(), ledger)
        return [c for r in reads for c in r["calls"]], transport, files
    t = transcript_usage.read_transcript(path, ledger)
    return t["calls"], t["transport"], [path]


def price(ctx, path, days=DEFAULT_DAYS):
    calls, transport, sources = calls_of(path, ctx.ledger)
    calls = [c for c in calls if c.get("model") != transcript_usage.SYNTHETIC_MODEL]
    _events_, unpriced, by_family = weigh(calls, ctx.data)
    lines = ["price: %s (%d source files, %d calls, transport %s)" % (path, len(sources), len(calls), transport)]
    for fam, agg in sorted(by_family.items()):
        lines.append("  %-8s " % fam + ", ".join("%s %d" % (label, agg[k]) for k, label in TOKEN_LABELS))
    for model, (count, why) in sorted(unpriced.items()):
        lines.append("  unpriced: %s, %d calls (%s)" % (model, count, why))
    if transport != transcript_usage.HOST_TRANSPORT:
        lines.append("window share: none; %s is not host-transport spend, so it drains no Anthropic "
                     "window" % transport)
        return "\n".join(lines)
    # The widest window first, so its transcript scan also serves the narrower one.
    results = {key: fit(ctx, key, days) for key in sorted(WINDOWS, key=lambda k: -WINDOWS[k][1])}
    for key in WINDOWS:
        result = results[key]
        if result["status"] != "ok":
            why = ("void: " + result["reason"] if result["status"] == "void"
                   else "insufficient data (%d periods, %d samples)" % (result["periods"], result["samples"]))
            lines.append("%s window: n/a (%s)" % (key, why))
            continue
        parts, total, complete = [], 0.0, not unpriced
        for fam, agg in sorted(by_family.items()):
            e = result["families"].get(fam)
            if not e or e.get("unidentified"):
                parts.append("%s unidentified" % fam)
                complete = False
                continue
            pct = e["coef"] * agg["weight"]
            total += pct
            parts.append("%s %.3g%%" % (fam, pct))
        lines.append("%s window: %.3g%%%s (%s)" % (
            key, total, "" if complete else " of the priced, identified part", "; ".join(parts)))
    lines.append(UPPER_BOUND_NOTE)
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------- CLI

def main(argv=None, registry=None):
    ap = argparse.ArgumentParser(prog="plan_usage_calc.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fit", "price", "drift"):
        p = sub.add_parser(name)
        p.add_argument("--projects")
        p.add_argument("--ledger")
        p.add_argument("--registry")
        p.add_argument("--now", type=float)
        if name != "price":
            p.add_argument("--window", choices=sorted(WINDOWS), default="5h")
        if name == "fit":
            p.add_argument("--days", type=int, default=DEFAULT_DAYS)
        if name == "price":
            p.add_argument("path")
    args = ap.parse_args(argv)
    ctx = Context(args.projects, args.ledger, registry, args.now, args.registry)
    if args.cmd == "fit":
        print(format_fit(ctx, fit(ctx, args.window, args.days)))
    elif args.cmd == "drift":
        print(format_drift(ctx, drift(ctx, args.window)))
    else:
        try:
            print(price(ctx, args.path))
        except (OSError, ValueError) as exc:
            print("price: %s" % exc, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
