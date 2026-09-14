#!/usr/bin/env python3
"""Per-model latency and per-tool spend accounting for the ai-worker call ledger.

Reads ~/.claude/ai_worker_calls.jsonl -- one JSON object per line, written by the
ai-worker MCP server's deposit-and-claim recorder (a MODEL-layer half carrying
model/latency/tokens/cost, merged with a TOOL-layer half carrying tool/under_floor).
Schema, all fields nullable:

    ts, tool, model, tokens, latency_ms, cost, under_floor, truncated, degenerate, chars

The headline is the per-model latency distribution: it is the only evidence that can
set `request_timeout_local` on something other than a guess. A timeout picked below
p99 silently converts slow-but-correct calls into failures, so the percentile column
is reported WITH its n and flagged when n is too small to mean anything.

Three states, never collapsed. A ledger row can be
  1. ABSENT      -- the line failed to parse (truncated write, interleaved append)
  2. NULL-FIELD  -- the row parsed but a field is null (the recorder had no value:
                    a floor-refused call legitimately has model=null, latency=null)
  3. PRESENT     -- a real measurement
A reducer that folds (1) and (2) both to zero reports a clean file that is broken.
Parse failures and null fields are counted and reported SEPARATELY, and the bucket
counts are asserted to sum to the raw line count of the file -- if they do not, the
tool says so rather than printing a confident partial number.

No composite score is printed. There is no defensible way to average a latency
against a truncation rate, so per-item profiles only.

A missing ledger is NORMAL -- nothing has been logged yet. That exits 0 with a
one-line message, not a traceback.

Usage:
  ai_worker_metrics.py                     report all history
  ai_worker_metrics.py --days 7            report the last 7 days only
  ai_worker_metrics.py --ledger PATH       read a different ledger (fixtures)
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone

LEDGER = os.path.expanduser('~/.claude/ai_worker_calls.jsonl')

# Percentiles over a handful of samples are noise: p99 of n=6 IS the max, and reads
# as a measurement. Below this, the report prints the numbers but marks them.
MIN_N_FOR_PERCENTILES = 5

# Fields whose null-ness is worth counting per row. `ts` is excluded -- it is the
# window key and its absence is handled by the window filter, not the null tally.
TRACKED_FIELDS = ('tool', 'model', 'tokens', 'latency_ms', 'cost',
                  'under_floor', 'truncated', 'degenerate', 'chars')


def w(s):
    sys.stdout.write(str(s).encode('ascii', 'replace').decode('ascii') + '\n')


def fmt(n):
    if n is None:
        return '?'
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1000:
        return f'{n/1000:.1f}k'
    return str(int(n))


def pct(part, whole):
    """Rate as a percentage string; an empty denominator reads '-', never 0%."""
    return '-' if not whole else f'{100.0 * part / whole:.1f}%'


def percentile(sorted_vals, q):
    """Nearest-rank percentile over a pre-sorted list. No interpolation: with the
    sample sizes this tool sees, an interpolated p99 invents a value between two
    real observations and reads as though it were measured."""
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def parse_ts(v):
    """Ledger timestamp -> aware datetime, or None when absent/unparseable.
    Accepts ISO-8601 (with or without trailing Z) and a numeric epoch."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load(path):
    """Read the ledger into (rows, counts).

    counts keys -- these MUST sum to the raw line count:
      blank        lines that are whitespace only
      parse_fail   lines that are not valid JSON, or are valid JSON but not an object
      out_of_window rows dropped by --days (includes rows with no usable ts)
      kept         rows returned
    """
    rows, counts = [], dict(blank=0, parse_fail=0, out_of_window=0, kept=0)
    raw_lines = 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            raw_lines += 1
            if not line.strip():
                counts['blank'] += 1
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                counts['parse_fail'] += 1
                continue
            if not isinstance(rec, dict):
                # Valid JSON, wrong shape (a bare string or list). Not a row.
                counts['parse_fail'] += 1
                continue
            rows.append(rec)
            counts['kept'] += 1
    return rows, counts, raw_lines


def apply_window(rows, counts, days):
    """Drop rows outside the window. A row whose ts is absent or unparseable cannot
    be placed in time, so it is dropped BY THE WINDOW and counted there -- it is not
    silently kept (which would date-launder it) nor counted as a parse failure
    (the line parsed fine)."""
    if not days:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = []
    for r in rows:
        ts = parse_ts(r.get('ts'))
        if ts is None or ts < cutoff:
            counts['out_of_window'] += 1
            counts['kept'] -= 1
        else:
            kept.append(r)
    return kept


def null_tally(rows):
    """Per-field count of rows that parsed but carry a null (or missing) value."""
    tally = {f: 0 for f in TRACKED_FIELDS}
    for r in rows:
        for f in TRACKED_FIELDS:
            if r.get(f) is None:
                tally[f] += 1
    return tally


def num(v):
    """Numeric field value, or None. A bool is NOT a number here -- `under_floor`
    and friends are flags and must never be summed into a latency."""
    if isinstance(v, bool) or v is None:
        return None
    return v if isinstance(v, (int, float)) else None


def truthy(v):
    """Flag field -> True only when explicitly true. Absent, null and unparseable
    all read as 'not flagged' for the numerator, but the denominator for a flag's
    rate is the count of rows where the flag was actually PRESENT -- see flag_rate."""
    return v is True or v == 1


def flag_rate(rows, field):
    """(flagged, observed, total) for a boolean health field. `observed` excludes
    rows where the field is null: a rate over rows that never recorded the flag
    understates it, and reporting 0% for a field nobody wrote is a lie."""
    observed = [r for r in rows if r.get(field) is not None]
    return sum(1 for r in observed if truthy(r.get(field))), len(observed), len(rows)


def report_latency(rows):
    """Headline: per-model latency distribution. This is the timeout evidence."""
    w('')
    w('== Per-model latency (ms) -- the evidence for request_timeout_local ==')
    by = {}
    missing = 0
    for r in rows:
        lat = num(r.get('latency_ms'))
        if lat is None:
            missing += 1
            continue
        by.setdefault(r.get('model') or '(null model)', []).append(lat)
    if not by:
        w('   No row carries a latency_ms value -- no distribution to report.')
        if missing:
            w(f'   ({missing} row(s) had a null/absent latency_ms.)')
        return
    w(f"{'model':<28} {'n':>5} {'median':>9} {'p90':>9} {'p99':>9} {'max':>9}  note")
    w('-' * 92)
    for model, vals in sorted(by.items(), key=lambda kv: -len(kv[1])):
        vals.sort()
        n = len(vals)
        note = '' if n >= MIN_N_FOR_PERCENTILES else f'n<{MIN_N_FOR_PERCENTILES}: percentiles are noise'
        w(f'{str(model)[:28]:<28} {n:>5} {percentile(vals, 0.5):>9.0f} '
          f'{percentile(vals, 0.9):>9.0f} {percentile(vals, 0.99):>9.0f} '
          f'{vals[-1]:>9.0f}  {note}')
    w('-' * 92)
    if missing:
        w(f'{missing} row(s) excluded from every distribution: null/absent latency_ms '
          '(a floor-refused call legitimately has none).')
    small = [m for m, v in by.items() if len(v) < MIN_N_FOR_PERCENTILES]
    if small:
        w('WARNING: fewer than %d samples for %s -- do NOT set a timeout from these '
          'percentiles; they are the max wearing a percentile label.'
          % (MIN_N_FOR_PERCENTILES, ', '.join(sorted(str(m) for m in small))))


def report_tools(rows):
    """Per-tool rollup: volume, spend, and the floor-gate refusal rate."""
    w('')
    w('== Per-tool rollup ==')
    by = {}
    for r in rows:
        by.setdefault(r.get('tool') or '(null tool)', []).append(r)
    if not by:
        w('   No rows.')
        return
    w(f"{'tool':<24} {'calls':>6} {'tokens':>9} {'cost':>10} "
      f"{'floor_n':>8} {'floor_obs':>10} {'floor_rate':>11}")
    w('-' * 84)
    for tool, g in sorted(by.items(), key=lambda kv: -len(kv[1])):
        toks = sum(num(r.get('tokens')) or 0 for r in g)
        cost = sum(num(r.get('cost')) or 0 for r in g)
        fl, obs, _ = flag_rate(g, 'under_floor')
        w(f'{str(tool)[:24]:<24} {len(g):>6} {fmt(toks):>9} {cost:>10.4f} '
          f'{fl:>8} {obs:>10} {pct(fl, obs):>11}')
    w('-' * 84)
    w(f"{len(rows)} call(s) | tokens {fmt(sum(num(r.get('tokens')) or 0 for r in rows))} | "
      f"cost {sum(num(r.get('cost')) or 0 for r in rows):.4f}")
    w("   floor_obs = rows that actually recorded under_floor; floor_rate is over that,")
    w("   not over all calls -- a rate over rows that never wrote the flag understates it.")


def report_health(rows):
    """Degenerate and truncated counters, each with its own observed denominator."""
    w('')
    w('== Health counters ==')
    w(f"{'counter':<14} {'flagged':>8} {'observed':>9} {'rate':>8} {'rows':>6}")
    w('-' * 50)
    for field in ('degenerate', 'truncated'):
        fl, obs, tot = flag_rate(rows, field)
        w(f'{field:<14} {fl:>8} {obs:>9} {pct(fl, obs):>8} {tot:>6}')
    w('-' * 50)
    w("   rate is flagged/observed. 'observed' < 'rows' means some rows never recorded")
    w('   the flag at all -- that is a recorder gap, not a clean result.')
    report_errors(rows)


def report_errors(rows):
    """Failed calls, by error class.

    `error` is deliberately NOT a tracked-null field and NOT a boolean flag: null
    is the healthy case, so a null-tally would read a clean ledger as ~100% missing
    and a flag_rate denominator would be meaningless. It is counted over ALL rows.

    This is the ledger's headline question -- 'has this failed before?' -- so the
    classes are listed, not just the total. One error repeated 40 times and 40
    distinct errors are different problems and a single count cannot separate them.
    """
    failed = [r for r in rows if r.get('error') not in (None, '')]
    w('')
    w('== Failed calls ==')
    if not rows:
        w('   no rows in window.')
        return
    w(f'   failed {len(failed)} of {len(rows)} rows  ({pct(len(failed), len(rows))})')
    if not failed:
        w('   NOTE: zero failures is only meaningful for calls made AFTER the error')
        w('   paths began recording. An older ledger cannot distinguish "no failures"')
        w('   from "failures were not written".')
        return
    classes = {}
    for r in failed:
        key = str(r['error'])[:60]
        classes[key] = classes.get(key, 0) + 1
    w('')
    w(f"{'count':>6}  {'tool':<12} error (first 60 chars)")
    w('-' * 78)
    for key, n in sorted(classes.items(), key=lambda kv: -kv[1]):
        tools = sorted({str(r.get('tool')) for r in failed if str(r['error'])[:60] == key})
        w(f'{n:>6}  {",".join(tools)[:12]:<12} {key}')


def report_integrity(counts, raw_lines, nulls, kept_rows, path, days):
    """Bucket accounting. The counts must reconcile against the file's raw line
    count; a mismatch means this report is describing something other than the
    file on disk, and that is said out loud rather than papered over."""
    w('')
    w('== Ledger integrity ==')
    w(f'   file            {path}')
    w(f'   raw lines       {raw_lines}')
    w(f"   blank lines     {counts['blank']}")
    w(f"   parse failures  {counts['parse_fail']}   (unparseable/non-object lines -- "
      'NOT the same as a null field)')
    w(f"   out of window   {counts['out_of_window']}"
      + (f'   (--days {days}; includes rows with no usable ts)' if days else '   (no window)'))
    w(f"   rows reported   {counts['kept']}")
    total = counts['blank'] + counts['parse_fail'] + counts['out_of_window'] + counts['kept']
    if total != raw_lines:
        w(f'   ERROR: buckets sum to {total} but the file has {raw_lines} lines. '
          'Rows are being lost or double-counted -- treat every number above as suspect.')
    else:
        w(f'   buckets sum to {total} == raw lines. OK.')
    if counts['parse_fail']:
        w(f"   WARNING: {counts['parse_fail']} line(s) could not be parsed. They are excluded "
          'from every table above and are a WRITER defect (interleaved or truncated append).')

    w('')
    w('   null fields among reported rows (parsed, but the recorder had no value):')
    if not kept_rows:
        w('     (no rows)')
    else:
        any_null = False
        for f in TRACKED_FIELDS:
            if nulls[f]:
                any_null = True
                w(f'     {f:<14} {nulls[f]:>5} / {len(kept_rows)}  ({pct(nulls[f], len(kept_rows))})')
        if not any_null:
            w('     none')
        w('     A null model+latency pair is EXPECTED on a floor-refused call: the tool')
        w('     recorder claimed an empty slot, which is the correct, non-fabricated row.')


def main():
    ap = argparse.ArgumentParser(
        description='Per-model latency and per-tool spend from the ai-worker call ledger.')
    ap.add_argument('--ledger', default=LEDGER,
                    help='ledger to read (default: %(default)s)')
    ap.add_argument('--days', type=int, metavar='N',
                    help='report only rows from the last N days (default: all history). '
                         'Rows with no usable ts are dropped and counted as out-of-window.')
    a = ap.parse_args()

    path = os.path.expanduser(a.ledger)
    if not os.path.exists(path):
        w(f'No ledger yet at {path} -- nothing has been logged. This is normal.')
        return 0

    rows, counts, raw_lines = load(path)
    rows = apply_window(rows, counts, a.days)

    if a.days:
        w(f'ai-worker call ledger -- last {a.days} day(s)')
    else:
        w('ai-worker call ledger -- all history')

    if not rows:
        w('')
        w('No rows in scope.')
        report_integrity(counts, raw_lines, null_tally(rows), rows, path, a.days)
        return 0

    report_latency(rows)
    report_tools(rows)
    report_health(rows)
    report_integrity(counts, raw_lines, null_tally(rows), rows, path, a.days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
