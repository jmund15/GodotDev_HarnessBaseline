#!/usr/bin/env python3
"""Price and time any mix of runs in one table: tokens times the registry's list price.

    run_cost.py [--by <prefix-regex>] [--json] <path>...
    run_cost.py [--json] --a <path>... --b <path>...

    run_cost.py .claude/scratch/<run a>/*.out.json .claude/scratch/<run b>/*.out.json
    run_cost.py --by '^(sol|opus)cmp' .claude/scratch/.../*.record.json

A path is a Claude Code result (single JSON, a stream-json capture or a verbose array), a sidecar
`.out.json`, `.stream` or `.record.json`, or a Workflow run directory (`<session>/subagents/workflows/<runId>/`,
one row per agent transcript, read through `orchestration_metrics.agent_usage`). A sidecar `.out.json`
that is only a DETACHED stub reads its sibling `.record.json`.

Each row prints label, model, fresh input, cache-read, cache-write and output tokens, list USD, wall
minutes, API minutes when recorded, turns, output tokens per API second (per wall second, marked `w`,
when no API time is recorded) and seconds per turn. `--by` groups rows by the first regex group (else
the whole match) of the label and totals each group; `--json` prints the machine form. Every priced
row also carries `usd_parts`: its list USD split into fresh, cache-read, cache-write and output.

`--a`/`--b` compare two sides of the same work (one model's runs against another's): rows join by cell, the
label without its `#<model>` suffix, so a cell's driver and judge models sum into one cost. The B/A ratio and
each side's per-part tokens and USD cover only the cells both sides priced; a cell one side lacks is listed,
never counted. If no cells are shared, compare all priced side totals and mark them as different work.

USD uses the shared record request path: final client transcript usage, with stream usage
as a labelled partial-output fallback. Aggregate remainders use compaction boundary tiers;
mixed/unknown boundaries carry a base-rate estimate. Totals-only input is marked untiered.
List USD is the same axis
whether the transport bills per token or spends a plan quota (which currency a run spends is the
registry's `costModel`, never this tool's answer). A record's own `priceMultiplier` applies; a result
file prices at the list rates. It never reads Claude Code's `total_cost_usd`: that field prices
non-Anthropic tokens at Anthropic rates. Cache writes bill at the 5-minute rate except the 1-hour share
a result's `usage.cache_creation` accounts for exactly. A run with no usage, an unknown model or a row
with a `priceGap` prints NO USAGE or UNPRICED with its reason and is never priced as 0; the exit code
is 1 when any row is unpriced.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _events(text):
    """The JSON objects in `text`: the whole document (an object or an array of events), else one per line."""
    return model_registry.usage_events(text)


def _sibling_record(path):
    name = os.path.basename(path)
    for suffix in ('.record.json.out', '.out.json', '.progress.jsonl', '.stream.jsonl', '.raw.json', '.stream', '.record.json'):
        if name.endswith(suffix):
            sibling = os.path.join(os.path.dirname(path), name[:-len(suffix)] + '.record.json')
            return sibling if os.path.isfile(sibling) else None
    return None


def _stem(path):
    name = os.path.basename(path)
    for suffix in ('.record.json.out', '.record.json', '.out.json', '.progress.jsonl', '.stream.jsonl', '.stream', '.json'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _run(label, model, *, fresh=None, cache_read=None, cache_write=None, cache_write_1h=0, output=None,
         wall_s=None, api_s=None, turns=None, multiplier=1.0, problem=None):
    return dict(label=label, model=model, fresh=fresh, cache_read=cache_read, cache_write=cache_write,
                cache_write_1h=cache_write_1h or 0, output=output, wall_s=wall_s, api_s=api_s, turns=turns,
                multiplier=multiplier, problem=problem, requests=None, tier_basis='untiered')


def _from_record(rec, path, data=None, events=None):
    model = (model_registry.record_model_id(rec) if rec.get('attestedModel')
             else rec.get('servedModel') or rec.get('requestedModel'))
    label = rec.get('label') or _stem(path)
    try:
        view = model_registry.record_requests(rec, path, data=data, events=events)
    except (model_registry.RegistryError, OSError, ValueError) as exc:
        return [_run(label, model, problem=str(exc))]
    requests = view['requests']
    if requests:
        runs = _from_requests(requests, path, label)
        duration, api = _number(rec.get('durationMs')), _number(rec.get('apiDurationMs'))
        for i, run in enumerate(runs):
            run['multiplier'] = _number(rec.get('priceMultiplier')) if _number(rec.get('priceMultiplier')) is not None else 1.0
            run.update(tier_basis=view['costTierBasis'], usage_source=view['costUsageSource'],
                       usage_basis=view['costUsageBasis'], compaction_basis=view['costCompactionBasis'])
            if view.get('costCacheWriteBasis'):
                run['cache_write_basis'] = view['costCacheWriteBasis']
            if i == 0:
                run.update(wall_s=duration / 1000 if duration is not None else None,
                           api_s=api / 1000 if api else None, turns=_number(rec.get('numTurns')))
        return runs
    if isinstance(model, list):
        # The record sums every model's tokens (a daemon's driver and its judges); the per-model split is the
        # sibling stream's last result event, which stream-json can repeat.
        runs = _from_sibling_stream(path, label)
        for run in runs or []:
            run['multiplier'] = _number(rec.get('priceMultiplier')) or 1.0
        return runs or [_run(label, None, problem='records several served models: ' + ', '.join(map(str, model)))]
    tokens = [_number(rec.get(k)) for k in ('inputTokens', 'cacheReadTokens', 'outputTokens')]
    if not model or None in tokens:
        return [_run(label, model, problem='no usage in the record')]
    write = _number(rec.get('cacheWriteTokens')) or 0
    hour = min(_number(rec.get('cacheWrite1hTokens')) or 0, write)
    duration, api = _number(rec.get('durationMs')), _number(rec.get('apiDurationMs'))
    return [_run(label, model, fresh=tokens[0], cache_read=tokens[1], cache_write=write - hour,
                 cache_write_1h=hour, output=tokens[2],
                 wall_s=duration / 1000 if duration is not None else None,
                 api_s=api / 1000 if api else None, turns=_number(rec.get('numTurns')),
                 multiplier=_number(rec.get('priceMultiplier')) or 1.0)]


def _from_sibling_stream(path, label):
    stem = os.path.join(os.path.dirname(path), _stem(path))
    for stream in (stem + '.stream.jsonl', os.fspath(path) + '.out',
                   stem + '.record.json.out', stem + '.stream'):
        if not os.path.isfile(stream):
            continue
        with open(stream, encoding='utf-8', errors='replace') as fh:
            results = [e for e in _events(fh.read()) if e.get('type') == 'result' and e.get('modelUsage')]
        if results:
            return _from_result(results[-1], path, label=label)
    return None


def _from_requests(requests, path, label=None, model=None):
    grouped = {}
    for req in requests:
        grouped.setdefault(model or req.get('model'), []).append(req)
    runs = []
    for mid, calls in grouped.items():
        counts = {k: sum(c.get(k) or 0 for c in calls)
                  for k in ('fresh', 'cache_read', 'cache_write', 'cache_write_1h', 'output')}
        name = label or _stem(path)
        run = _run(name if len(grouped) == 1 else f'{name}#{mid}', mid, **counts)
        run.update(requests=calls, tier_basis='per-request')
        runs.append(run)
    return runs


def _from_result(result, path, label=None):
    label = label or _stem(path)
    models = {m: u for m, u in (result.get('modelUsage') or {}).items() if isinstance(u, dict)}
    if not models:
        usage = result.get('usage') or {}
        model = result.get('model')
        if not (model and _number(usage.get('output_tokens')) is not None):
            return None
        models = {model: {'inputTokens': usage.get('input_tokens'),
                          'cacheReadInputTokens': usage.get('cache_read_input_tokens'),
                          'cacheCreationInputTokens': usage.get('cache_creation_input_tokens'),
                          'outputTokens': usage.get('output_tokens')}}
    duration, api = _number(result.get('duration_ms')), _number(result.get('duration_api_ms'))
    lead = max(models, key=lambda m: _number(models[m].get('outputTokens')) or 0)
    runs = []
    for model, u in models.items():
        if _number(u.get('inputTokens')) is None or _number(u.get('outputTokens')) is None:
            runs.append(_run(label if len(models) == 1 else f'{label}#{model}', model,
                             problem='no usage for this model in the result'))
            continue
        write = _number(u.get('cacheCreationInputTokens')) or 0
        five, hour = model_registry.cache_write_split(
            write, (result.get('usage') or {}).get('cache_creation') if len(models) == 1 else None)
        timed = model == lead
        runs.append(_run(label if len(models) == 1 else f'{label}#{model}', u.get('canonicalModel') or model,
                         fresh=_number(u.get('inputTokens')) or 0, cache_read=_number(u.get('cacheReadInputTokens')) or 0,
                         cache_write=five, cache_write_1h=hour, output=_number(u.get('outputTokens')) or 0,
                         wall_s=duration / 1000 if timed and duration is not None else None,
                         api_s=api / 1000 if timed and api else None,
                         turns=_number(result.get('num_turns')) if timed else None))
    return runs


def _from_dir(path):
    import orchestration_metrics
    tag = os.path.basename(os.path.abspath(path))
    runs = []
    for aid, u in sorted(orchestration_metrics.agent_usage(path).items()):
        label = f'{tag}/{aid[:8]}'
        counts = [u.get(k) for k in ('inp', 'cr', 'cw', 'out')]
        model = u.get('served_model')
        if None in counts or not model or str(model).startswith('mixed:'):
            runs.append(_run(label, model, problem='no usage' if None in counts else f'served model is {model!r}'))
            continue
        run = _run(label, model, fresh=counts[0], cache_read=counts[1], cache_write=counts[2], output=counts[3],
                   wall_s=u.get('secs'), turns=u.get('turns'))
        run.update(requests=u.get('requests'), tier_basis=u.get('tier_basis', 'untiered'))
        runs.append(run)
    return runs or [_run(tag, None, problem='no agent transcripts in the directory')]


def load_runs(path, data=None):
    """The runs one path holds: a list of run dicts, each with a `problem` when it cannot be priced."""
    if os.path.isdir(path):
        return _from_dir(path)
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError as exc:
        return [_run(_stem(path), None, problem=f'unreadable: {exc.strerror}')]
    events = _events(text)
    for event in events:
        if 'inputTokens' in event and ('servedModel' in event or 'requestedModel' in event):
            return _from_record(event, path, data=data)
    sibling = _sibling_record(path)
    if sibling and os.path.abspath(sibling) != os.path.abspath(path):
        with open(sibling, encoding='utf-8', errors='replace') as fh:
            return _from_record(json.load(fh), sibling, data=data)
    requests = model_registry.request_usage(events)
    if not requests:
        requests, _source = model_registry.sibling_requests(path)
    results = [e for e in events if e.get('type') == 'result'] or [e for e in events if 'modelUsage' in e]
    if results:
        result = results[-1]
        runs = _from_result(result, path)
        if runs and all(not r['problem'] for r in runs) and (requests or result.get('session_id')):
            rec = dict(sessionId=result.get('session_id'), transport=result.get('transport'),
                       servedModel=runs[0]['model'] if len(runs) == 1 else [r['model'] for r in runs],
                       durationMs=result.get('duration_ms'), apiDurationMs=result.get('duration_api_ms'),
                       numTurns=result.get('num_turns'), priceMultiplier=1.0)
            for key, field in dict(fresh='inputTokens', cache_read='cacheReadTokens',
                                   cache_write='cacheWriteTokens', cache_write_1h='cacheWrite1hTokens',
                                   output='outputTokens').items():
                rec[field] = sum(r[key] for r in runs)
            rec['cacheWriteTokens'] += rec['cacheWrite1hTokens']
            return _from_record(rec, path, data=data, events=events)
        if runs:
            return runs
    if requests:
        runs = _from_requests(requests, path)
        final_transcript = any('parentUuid' in e and 'sessionId' in e for e in events)
        for run in runs:
            run['tier_basis'] = 'per-request' if final_transcript else 'per-request (stream-partial-output)'
        return runs
    sibling = _sibling_record(path)
    if sibling:
        with open(sibling, encoding='utf-8', errors='replace') as fh:
            for event in _events(fh.read()):
                return _from_record(event, sibling, data=data)
    return [_run(_stem(path), None, problem='no usage in the file')]


def _usd_parts(run, data):
    """The run's list USD split into fresh, cache-read, cache-write and output; the parts sum to `run['usd']`.
    Reads run['cache_write'] before `price` folds the 1-hour share into it."""
    if run.get('requests') is not None:
        return {part: model_registry.list_price(run['model'], data=data, multiplier=run['multiplier'],
                                               requests=run['requests'], part=part)[0] for part in PARTS}
    zero = dict(fresh=0, cache_read=0, output=0, cache_write=0, cache_write_1h=0)
    split = dict(fresh=dict(fresh=run['fresh']), cache_read=dict(cache_read=run['cache_read']),
                 cache_write=dict(cache_write=run['cache_write'], cache_write_1h=run['cache_write_1h']),
                 output=dict(output=run['output']))
    return {part: model_registry.list_price(run['model'], data=data, multiplier=run['multiplier'],
                                            **dict(zero, **counts))[0]
            for part, counts in split.items()}


def price(run, data):
    """Fill `usd` and the rate fields on `run`; a run that cannot be priced keeps usd None and a problem."""
    run['usd'] = None
    if not run['problem']:
        run['usd'], run['problem'] = model_registry.list_price(
            run['model'], run['fresh'], run['cache_read'], run['output'], data=data,
            cache_write=run['cache_write'], cache_write_1h=run['cache_write_1h'], multiplier=run['multiplier'],
            requests=run.get('requests'))
    wall, api, out = run['wall_s'], run['api_s'], run['output']
    run['wall_min'] = wall / 60 if wall is not None else None
    run['api_min'] = api / 60 if api else None
    basis_s, run['rate_basis'] = (api, 'api') if api else (wall, 'wall') if wall else (None, None)
    run['out_tok_per_sec'] = out / basis_s if basis_s and out is not None else None
    run['sec_per_turn'] = wall / run['turns'] if wall is not None and run['turns'] else None
    run['usd_parts'] = None
    if run['usd'] is not None:
        run['usd_parts'] = _usd_parts(run, data)
    if run['cache_write'] is not None:
        run['cache_write'] += run['cache_write_1h']
    return run


PARTS = ('fresh', 'cache_read', 'cache_write', 'output')


def _side(runs):
    """{cell: summed run} for one side, priced cells only, and the cells it holds that are not priced."""
    cells, unpriced = {}, set()
    for run in runs:
        cell = run['label'].split('#', 1)[0]
        if run['usd'] is None:
            unpriced.add(cell)
            continue
        c = cells.setdefault(cell, dict(usd=0.0, tokens=dict.fromkeys(PARTS, 0), usd_parts=dict.fromkeys(PARTS, 0.0)))
        c['usd'] += run['usd']
        for part in PARTS:
            c['tokens'][part] += run[part] or 0
            c['usd_parts'][part] += run['usd_parts'][part]
    for cell in unpriced:
        cells.pop(cell, None)
    return cells, unpriced


def _tier_basis(runs):
    return '; '.join(sorted({r.get('tier_basis', 'untiered') for r in runs
                            if r.get('usd') is not None})) or 'unpriced'


def pair(a_runs, b_runs):
    """The paired comparison: per shared cell A and B USD, side totals over shared cells, and the B/A ratio."""
    a, a_bad = _side(a_runs)
    b, b_bad = _side(b_runs)
    shared = sorted(set(a) & set(b))
    def total(side):
        selected = shared if shared else side
        return dict(usd=sum(side[c]['usd'] for c in selected),
                    tokens={p: sum(side[c]['tokens'][p] for c in selected) for p in PARTS},
                    usd_parts={p: sum(side[c]['usd_parts'][p] for c in selected) for p in PARTS})
    ta, tb = total(a), total(b)
    return dict(cells=[dict(cell=c, a_usd=a[c]['usd'], b_usd=b[c]['usd'],
                            ratio=b[c]['usd'] / a[c]['usd'] if a[c]['usd'] else None) for c in shared],
                a=ta, b=tb, ratio_b_over_a=tb['usd'] / ta['usd'] if ta['usd'] else None,
                comparison='shared-cells' if shared else 'side-totals (no shared cells)',
                tier_basis=_tier_basis(a_runs + b_runs),
                only_a=sorted(set(a) - set(b)), only_b=sorted(set(b) - set(a)),
                unpriced=sorted(a_bad | b_bad))


def render_pair(p):
    lines = [f"{'cell':<34} {'A USD':>9} {'B USD':>9} {'B/A':>6}"]
    for c in p['cells']:
        lines.append(f"{c['cell'][:34]:<34} {c['a_usd']:>9.4f} {c['b_usd']:>9.4f} {_fmt(c['ratio'], '.2f'):>6}")
    lines += ['', f"{'side':<6} {'part':<12} {'tokens':>13} {'list USD':>10}"]
    for name in ('a', 'b'):
        side = p[name]
        for part in PARTS:
            lines.append(f"{name.upper():<6} {part:<12} {side['tokens'][part]:>13,.0f} {side['usd_parts'][part]:>10.4f}")
        lines.append(f"{name.upper():<6} {'total':<12} {'':>13} {side['usd']:>10.4f}")
    comparison = f"over {len(p['cells'])} shared cell(s)" if p['cells'] else 'side totals; no shared cells (different work)'
    lines += ['', f"B/A {comparison}: {_fmt(p['ratio_b_over_a'], '.3f')} [{p['tier_basis']}]"]
    for name, key in (('only in A', 'only_a'), ('only in B', 'only_b'), ('unpriced, excluded', 'unpriced')):
        if p[key]:
            lines.append(f"{name}: {', '.join(p[key])}")
    return '\n'.join(lines)


def _group(runs, pattern):
    groups = {}
    for run in runs:
        found = re.match(pattern, run['label'])
        key = (found.group(1) if found and found.groups() else found.group(0)) if found else '(other)'
        g = groups.setdefault(key, dict(runs=0, usd=0.0, unpriced=0, untiered=0, output=0, wall_min=0.0, turns=0))
        g['untiered'] += run['usd'] is not None and not model_registry.tier_basis_known(run.get('tier_basis', 'untiered'))
        g['runs'] += 1
        g['unpriced'] += run['usd'] is None
        g['usd'] += run['usd'] or 0.0
        g['output'] += run['output'] or 0
        g['wall_min'] += run['wall_min'] or 0.0
        g['turns'] += run['turns'] or 0
    for g in groups.values():
        priced = g['runs'] - g['unpriced']
        g['mean_usd'] = g['usd'] / priced if priced else None
    return groups


def _fmt(value, spec):
    return format(value, spec) if value is not None else '-'


def render(runs, groups, total, unpriced):
    lines = [f"{'label':<30} {'model':<22} {'fresh':>10} {'cache-r':>11} {'cache-w':>10} {'output':>9} "
             f"{'list USD':>9} {'wall m':>7} {'api m':>6} {'turns':>5} {'out/s':>8} {'s/turn':>7}"]
    for r in runs:
        if r['usd'] is None:
            tag = 'NO USAGE' if 'no usage' in (r['problem'] or '') else 'UNPRICED'
            lines.append(f"{r['label'][:30]:<30} {str(r['model'] or '?')[:22]:<22} {tag}: {r['problem']}")
            continue
        rate = _fmt(r['out_tok_per_sec'], '.1f') + ('w' if r['rate_basis'] == 'wall' else ' ')
        lines.append(f"{r['label'][:30]:<30} {r['model'][:22]:<22} {r['fresh']:>10,.0f} {r['cache_read']:>11,.0f} "
                     f"{r['cache_write']:>10,.0f} {r['output']:>9,.0f} {r['usd']:>9.4f} {_fmt(r['wall_min'], '.1f'):>7} "
                     f"{_fmt(r['api_min'], '.1f'):>6} {_fmt(r['turns'], '.0f'):>5} {rate:>8} {_fmt(r['sec_per_turn'], '.1f'):>7} [{r['tier_basis']}]")
    if groups:
        lines.append('')
        lines.append(f"{'group':<30} {'runs':>5} {'list USD':>10} {'mean USD':>10} {'output':>10} {'wall m':>8} {'turns':>6}")
        for key, g in sorted(groups.items()):
            lines.append(f"{key[:30]:<30} {g['runs']:>5} {g['usd']:>10.4f} {_fmt(g['mean_usd'], '.4f'):>10} "
                         f"{g['output']:>10,.0f} {g['wall_min']:>8.1f} {g['turns']:>6}"
                         + (f"  ({g['unpriced']} unpriced)" if g['unpriced'] else '')
                         + (f"  ({g['untiered']} untiered)" if g['untiered'] else ''))
    lines.append('')
    basis = _tier_basis(runs)
    lines.append(f"total list USD {total:.4f} [{basis}] over {len(runs) - unpriced} priced run(s)"
                 + (f"; {unpriced} NOT priced (excluded)" if unpriced else '')
                 + ". out/s is per API second; `w` marks per wall second (no API time recorded).")
    return '\n'.join(lines)


def main(argv=None, data=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--a', nargs='+', metavar='PATH', help='side A of a paired comparison')
    ap.add_argument('--b', nargs='+', metavar='PATH', help='side B of a paired comparison')
    ap.add_argument('--by', metavar='REGEX', help='group rows by the first group of this label regex')
    ap.add_argument('--json', action='store_true', help='print the machine form')
    args = ap.parse_args(argv)
    if bool(args.a) != bool(args.b) or not (args.paths or args.a) or (args.paths and args.a) or (args.a and args.by):
        ap.error('give <path>... or both --a and --b, not both; --by groups <path>... only')
    if data is None:
        try:
            data = model_registry.load()
        except model_registry.RegistryError as exc:
            print(f'registry unreadable: {exc}', file=sys.stderr)
            return 2
    if args.a:
        a_runs = [price(run, data) for path in args.a for run in load_runs(path, data=data)]
        b_runs = [price(run, data) for path in args.b for run in load_runs(path, data=data)]
        p = pair(a_runs, b_runs)
        print(json.dumps(dict(pair=p), indent=2) if args.json else render_pair(p))
        return 1 if p['unpriced'] or not a_runs or not b_runs else 0
    runs = [price(run, data) for path in args.paths for run in load_runs(path, data=data)]
    unpriced = sum(run['usd'] is None for run in runs)
    total = sum(run['usd'] or 0.0 for run in runs)
    groups = _group(runs, args.by) if args.by else None
    if args.json:
        doc = dict(runs=runs, total_usd=total, unpriced=unpriced,
                   tier_basis=_tier_basis(runs))
        if groups is not None:
            doc['groups'] = groups
        print(json.dumps(doc, indent=2))
    else:
        print(render(runs, groups, total, unpriced))
    return 1 if unpriced else 0


if __name__ == '__main__':
    sys.exit(main())
