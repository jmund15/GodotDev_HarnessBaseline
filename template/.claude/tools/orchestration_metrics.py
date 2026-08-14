#!/usr/bin/env python3
"""Per-agent cost/latency accounting for this session's Workflow runs.

Joins the harness's own records -- no hand-maintained pin maps:
  <session>/workflows/<runId>.json   -> runId, workflowName, script, logs,
                                        workflowProgress[] (agentId, label, model, phase)
  <session>/subagents/workflows/<runId>/agent-<id>.jsonl -> per-turn token usage

`effort` is not recorded by the harness. Resolution order:
  1. a `PINS {...}` line in the run's logs[]  (the sanctioned convention --
     `log('PINS ' + JSON.stringify({label: effort, ...}))`)
  2. a literal `effort:` inside an `agent()` opts object that also carries a
     literal `label:`  (works only for non-data-driven dispatch)
  3. '?' -- reported as unresolved; supply it at verdict time.

Cost is normalized to base-input-token equivalents (output x5, cache-write
x1.25, cache-read x0.1) so tiers are comparable in one number.

DeepSeek sidecar runs (deepseek_sidecar.sh -R/-L) are a SECOND SOURCE read from
the spend ledger (~/.claude/deepseek_spend.jsonl). They surface side-by-side and
are NEVER merged into the Anthropic tables: their cost is real USD (not plan
quota) and their effort is a requested-string vendor coordinate, not an
Anthropic rung. Attribution joins on the record's `label` (sidecar -l flag);
unlabeled rows report as '(unlabeled)'. Dedup reuses the run ledger via the
synthetic run id 'sidecar-<timestamp>-<label>'.

Appending requires --verdicts: an unoutcomed record has no denominator, so
recording the falsification outcome is the price of archiving. Re-appending an
already-archived run is a no-op (duplicates would silently skew
--archive-summary), and an unresolved '?' pin is refused rather than persisted
-- such a record cannot feed Effort Calibration, which is the only reason this
store exists.

Outcomes, not verdicts. 'Was the effort right?' is a counterfactual no
participant can observe; what IS observable is whether the output was accepted
(clean), corrected (defects), reworked (rework), or thrown away (discarded).
Over-pin ("overshoot") is never rated at consumption -- it is derived at the
aggregate by compute_candidates() comparing clean-only adjacent rungs within a
shape family (cost gap beyond natural rung pricing at comparable work volume),
and acted on as a substituted downgrade on the family's next natural dispatch
(never a parallel probe -- see /orchestration_metrics "Over-pin candidates").
Legacy verdict words (right-sized/overshoot/undershoot/wasted) map through.

Usage:
  orchestration_metrics.py                      report this session
  orchestration_metrics.py --session <dir>      report a specific session dir
  orchestration_metrics.py --verdicts v.json    rate + append to the archive
  orchestration_metrics.py --archive-summary    roll up the existing archive
"""
import argparse, json, os, re, sys
from datetime import datetime, timezone

OUT_W, CW_W, CR_W = 5.0, 1.25, 0.1
ARCHIVE = os.path.join('.claude', 'orchestration_metrics.jsonl')
# Falsification outcomes, recorded at consumption (see module docstring).
OUTCOMES = ('clean', 'defects', 'rework', 'discarded')
LEGACY_VERDICTS = {'right-sized': 'clean', 'overshoot': 'clean',
                   'undershoot': 'rework', 'wasted': 'discarded',
                   # effort-fit vocabulary (object-shaped pending entries): accepted
                   # as-is -> clean, one-correction -> defects
                   'fit': 'clean', 'excellent': 'clean', 'fit-high-value': 'clean',
                   'fit-highest-value': 'clean', 'fit-with-one-correction': 'defects'}
SIDECAR_LEDGER = os.path.expanduser('~/.claude/deepseek_spend.jsonl')
# Derived over-pin candidates (recomputed on every --archive-summary run). The
# dispatch-time surface (report()) reads it so the pin decision sees the queue.
CANDIDATES_FILE = os.path.join('.claude', 'orchestration_candidates.json')
# Adjacent rungs cost ~1.5-2x by pricing alone (medium->high roughly doubles
# tokens), so a cost gap only indicts the rung when it EXCEEDS that structure
# AND the work volume (turns) was comparable -- same work, deeper reasoning,
# no better outcome.
CANDIDATE_RATIO_MIN = 2.0
CANDIDATE_TURNS_MAX = 1.5
CANDIDATE_MIN_N = 2
EFFORT_ORDER = {'low': 0, 'medium': 1, 'high': 2, 'xhigh': 3, 'max': 4}

# Incremental outcome ledger, written as each dispatch's result is consumed rather than
# at session end. Recording needs context ("was this reworked / discarded?") that
# compaction destroys, while the cost data on disk survives it — so a long session that
# compacts would otherwise archive its earliest and largest dispatches as permanently
# 'unrated'. Same {label: outcome | [outcome, effort] | [outcome, effort, 'probe']} shape
# as --verdicts; an explicit --verdicts file layers on top of it.
PENDING_VERDICTS = os.path.join('.claude', 'scratch', 'orchestration_verdicts.json')


def load_pending_verdicts(path=PENDING_VERDICTS):
    """Verdicts recorded mid-session. Null/absent entries are debts, not verdicts."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return {k: v for k, v in (json.load(f) or {}).items() if v}
    except (json.JSONDecodeError, OSError):
        return {}


def w(s):
    sys.stdout.write(str(s).encode('ascii', 'replace').decode('ascii') + '\n')


def fmt(n):
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1000:
        return f'{n/1000:.1f}k'
    return str(int(n))


def norm_outcome(v):
    """Accept the outcome vocabulary; legacy verdict words map through."""
    if isinstance(v, dict):
        v = v.get('outcome') or v.get('verdict') or v.get('fit') or '?'
    return LEGACY_VERDICTS.get(v, v)


def outcome_of(r):
    """Archive outcome field, with legacy fallbacks: 'verdict' (pre-2026-08)
    and 'fit' (earliest schema) both carry verdict words."""
    v = r.get('outcome') or r.get('verdict') or r.get('fit') or '?'
    return norm_outcome(v)


def find_session_dir():
    root = os.path.expanduser('~/.claude/projects')
    cwd = os.path.abspath(os.getcwd())
    slug = cwd.replace(':', '-').replace(os.sep, '-').replace('/', '-')
    cands = [os.path.join(root, d) for d in os.listdir(root)
             if d.lower().lstrip('-') in slug.lower().lstrip('-')
             or slug.lower().endswith(d.lower())] if os.path.isdir(root) else []
    if not cands:
        cands = [os.path.join(root, d) for d in os.listdir(root)] if os.path.isdir(root) else []
    sessions = []
    for c in cands:
        if not os.path.isdir(c):
            continue
        for s in os.listdir(c):
            p = os.path.join(c, s, 'workflows')
            if os.path.isdir(p):
                sessions.append((os.path.getmtime(p), os.path.join(c, s)))
    if not sessions:
        return None
    return max(sessions)[1]


def _norm_effort(v):
    """PINS values drift in shape: 'medium', 'opus/medium', 'opus/medium x9',
    or {'model': ..., 'effort': ...}. Reduce each to the bare effort token."""
    if isinstance(v, dict):
        v = v.get('effort', '?')
    v = str(v)
    if '/' in v:
        v = v.split('/', 1)[1]
    m = re.match(r'(low|medium|high|xhigh|max)\b', v)
    return m.group(1) if m else '?'


def efforts_for(run):
    """label -> effort, via the PINS log line, else a static opts-literal scan."""
    for line in run.get('logs') or []:
        s = str(line).strip()
        if s.startswith('PINS '):
            try:
                raw = json.loads(s[5:])
                return {k: _norm_effort(v) for k, v in raw.items()}
            except Exception:
                pass
    out = {}
    for blk in re.findall(r'\{[^{}]*\}', run.get('script') or ''):
        lab = re.search(r"label:\s*['\"]([^'\"]+)['\"]", blk)
        eff = re.search(r"effort:\s*['\"](\w+)['\"]", blk)
        if lab and eff:
            out[lab.group(1)] = eff.group(1)
    return out


def agent_usage(run_dir):
    """agentId -> token/turn/tool/wall usage from the transcripts."""
    usage = {}
    if not os.path.isdir(run_dir):
        return usage
    for fn in os.listdir(run_dir):
        if not (fn.startswith('agent-') and fn.endswith('.jsonl')):
            continue
        aid = fn[len('agent-'):-len('.jsonl')]
        u = dict(out=0, cw=0, cr=0, turns=0, tools=0, secs=0.0)
        first = last = None
        for line in open(os.path.join(run_dir, fn), encoding='utf-8'):
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = o.get('timestamp')
            if ts:
                try:
                    t = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
                except Exception:
                    pass
            if o.get('type') != 'assistant':
                continue
            u['turns'] += 1
            m = o.get('message') or {}
            us = m.get('usage') or {}
            u['out'] += us.get('output_tokens', 0) or 0
            u['cw'] += us.get('cache_creation_input_tokens', 0) or 0
            u['cr'] += us.get('cache_read_input_tokens', 0) or 0
            for b in (m.get('content') or []):
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    u['tools'] += 1
        u['secs'] = (last - first).total_seconds() if (first and last) else 0.0
        usage[aid] = u
    return usage


def collect(session):
    rows = []
    wdir = os.path.join(session, 'workflows')
    if not os.path.isdir(wdir):
        return rows
    for fn in sorted(os.listdir(wdir)):
        if not fn.endswith('.json'):
            continue
        run = json.load(open(os.path.join(wdir, fn), encoding='utf-8'))
        rid = run.get('runId') or fn[:-5]
        eff = efforts_for(run)
        usage = agent_usage(os.path.join(session, 'subagents', 'workflows', rid))
        for e in run.get('workflowProgress') or []:
            if e.get('type') != 'workflow_agent':
                continue
            aid, lab = e.get('agentId'), e.get('label') or '(unlabeled)'
            u = usage.get(aid, dict(out=0, cw=0, cr=0, turns=0, tools=0, secs=0.0))
            rows.append(dict(
                run=rid, workflow=run.get('workflowName') or '?', phase=e.get('phaseTitle') or '',
                agent_id=aid, label=lab, model=e.get('model') or '?',
                effort=eff.get(lab, '?'), state=e.get('state') or '?',
                cost=u['out'] * OUT_W + u['cw'] * CW_W + u['cr'] * CR_W, **u))
    return rows


def collect_sidecar(ledger=SIDECAR_LEDGER, include_unlabeled=False):
    """Sidecar spend-ledger rows shaped for the archive. Marked source='sidecar';
    cost_usd is real dollars, `cost` stays 0 so sidecar rows can never leak into
    normalized-token totals. Run id is synthetic and stable across invocations.

    Unlabeled rows (no -l at dispatch: legacy history, benchmark arms, ad-hoc
    probes) are skipped by default -- they cannot be attributed or rated per
    label. --sidecar-all surfaces them for spend audits."""
    rows = []
    if not os.path.exists(ledger):
        return rows
    for line in open(ledger, encoding='utf-8'):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not rec.get('label') and not include_unlabeled:
            continue
        lab = rec.get('label') or '(unlabeled)'
        ts = rec.get('timestamp') or '?'
        rows.append(dict(
            run=f'sidecar-{ts}-{lab}', workflow='sidecar', phase='', agent_id='',
            label=lab, model=str(rec.get('servedModel') or rec.get('requestedModel') or '?'),
            effort=rec.get('effort') or '?',
            state=('completed' if rec.get('exitCode') == 0 else f"exit-{rec.get('exitCode')}"),
            source='sidecar', cost=0.0, cost_usd=rec.get('costUSD') or 0.0,
            out=rec.get('outputTokens') or 0, cw=0, cr=rec.get('cacheReadTokens') or 0,
            turns=rec.get('numTurns') or 0, tools=0,
            secs=(rec.get('durationMs') or 0) / 1000.0))
    return rows


def report(rows):
    side = [r for r in rows if r.get('source') == 'sidecar']
    rows = [r for r in rows if r.get('source') != 'sidecar']
    if not rows and not side:
        w('No Workflow runs found for this session -- nothing to report.')
        return
    if not rows:
        w('No Anthropic Workflow runs found for this session.')
        report_sidecar(side)
        return
    w(f"{'phase':<14} {'label':<30} {'eff':<7} {'cost':>8} {'out':>7} {'cacheR':>8} "
      f"{'turns':>6} {'tools':>6} {'sec':>6}")
    w('-' * 100)
    for r in sorted(rows, key=lambda x: (x['run'], -x['cost'])):
        w(f"{r['phase'][:14]:<14} {r['label'][:30]:<30} {r['effort']:<7} {fmt(r['cost']):>8} "
          f"{fmt(r['out']):>7} {fmt(r['cr']):>8} {r['turns']:>6} {r['tools']:>6} {r['secs']:>6.0f}")
    w('-' * 100)
    tot = sum(r['cost'] for r in rows)
    w(f"{len(rows)} agents | total {fmt(tot)} normalized | "
      f"out {fmt(sum(r['out'] for r in rows))} | turns {sum(r['turns'] for r in rows)}")

    by = {}
    for r in rows:
        by.setdefault(r['effort'], []).append(r)
    w('')
    w(f"{'effort':<8} {'n':>3} {'cost/agent':>11} {'turns/ag':>9} {'out/turn':>9} {'cost share':>11}")
    for k in ('low', 'medium', 'high', 'xhigh', '?'):
        g = by.get(k)
        if not g:
            continue
        n, t = len(g), sum(x['turns'] for x in g)
        w(f"{k:<8} {n:>3} {fmt(sum(x['cost'] for x in g)//n):>11} {t/n:>9.1f} "
          f"{sum(x['out'] for x in g)//max(t,1):>9} {sum(x['cost'] for x in g)/tot:>10.0%}")
    if '?' in by:
        w('')
        w(f"WARNING: {len(by['?'])} agent(s) have no resolved effort pin. Add a PINS log line to "
          "the script (see the module docstring) or supply the effort in the verdicts file.")
    report_sidecar(side)
    _report_pending_candidates()


def _report_pending_candidates():
    """Surface the derived over-pin queue where the next pin decision happens."""
    if not os.path.exists(CANDIDATES_FILE):
        return
    try:
        cands = json.load(open(CANDIDATES_FILE, encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return
    if not cands:
        return
    w('')
    w('Over-pin candidates from the last archive summary (check before pinning):')
    for c in cands:
        w(f"  {c['family']}: run at {c['suggested']} on its next dispatch (was "
          f"{c['effort']}, {c['cost_ratio']}x cost, no falsifications on either rung)")


def _report_candidates(rows):
    """The aggregate surface: derive over-pin candidates from the archive and
    persist them for the next dispatch-time session. Detection lives here (the
    only place with cross-dispatch context); action is a SUBSTITUTED downgrade
    on the family's next natural dispatch, never a parallel probe -- no
    orchestrator spends an extra dispatch on calibration, and none is needed:
    the trial rides on work that was going to happen anyway."""
    cands = compute_candidates(rows)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for c in cands:
        c['date'] = stamp
    # Atomic replace: concurrent sessions share one checkout and may run the
    # summary in parallel -- a half-written file would corrupt report()'s read.
    tmp = CANDIDATES_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cands, f, indent=1)
    os.replace(tmp, CANDIDATES_FILE)
    if cands:
        w('\n-- Over-pin candidates (computed, never rated) --')
        w(f"{'family':<28} {'run at':<10} {'->':<4} {'try':<8} {'cost x':>7} {'n':>4}")
        for c in cands:
            w(f"{c['family'][:28]:<28} {c['effort']:<10} {'->':<4} {c['suggested']:<8} "
              f"{c['cost_ratio']:>6.1f}x {c['n_hi'] + c['n_lo']:>4}")
        w("Action: the next natural dispatch of these families runs at the suggested rung "
          "(substituted downgrade -- never an extra dispatch). Mark it in the verdicts file: "
          "{label: ['clean', '<rung>', 'probe']}. A falsification there clears the candidate; "
          "a clean one confirms the floor and the pin table moves.")
    else:
        fals = sum(1 for r in rows if outcome_of(r) in ('defects', 'rework', 'discarded'))
        unrated = sum(1 for r in rows if outcome_of(r) == 'unrated')
        if fals:
            w(f'\nNo over-pin candidates ({fals} falsification event(s) in the archive keep the '
              'table calibrated).')
        elif unrated:
            w(f'\nNo over-pin candidates and no falsification events, but {unrated} unrated '
              'record(s) block the convergence claim -- rate them or exclude the runs.')
        else:
            w('\nNo over-pin candidates and no falsification events -- the pin table has '
              'converged for the archived shape distribution. K consecutive summaries like this '
              '-> watch-mode: failures only + one substituted downgrade per session on the '
              'largest converged family.')


def report_sidecar(side):
    """Sidecar tables, ONE PER servedModel. USD, requested-effort coordinates —
    never merged with (or averaged into) the Anthropic tables above.

    Split by model, never blended: DeepSeek's tiers differ ~3.1x on fresh tokens
    (2.69x on a real historical workload), so a single averaged $/run describes no
    model that exists. A blended figure looks like information and is not.

    Ledger caveat: rows written before 2026-08-12 were priced with hardcoded FLASH
    rates, so any pro row from before then under-reports by ~3.11x, and every row
    used a 0.003 cache rate rather than 0.0028. Mixed-provenance totals are flagged
    below rather than silently summed.
    """
    if not side:
        return
    by_model = {}
    for r in side:
        by_model.setdefault(r['model'] or '?', []).append(r)

    w('')
    w('-- DeepSeek sidecar (real USD; effort = requested vendor coordinate, NOT an Anthropic rung) --')
    if len(by_model) > 1:
        w('   One table per servedModel. NEVER average across models: the tiers differ ~3.1x')
        w('   on fresh tokens, so a blended $/run describes no model that exists.')

    for model_id in sorted(by_model):
        rows = by_model[model_id]
        w('')
        w(f'   [{model_id}]  {len(rows)} run(s)')
        w(f"{'label':<30} {'eff':<6} {'$cost':>8} {'out':>8} {'cacheR':>8} "
          f"{'turns':>6} {'sec':>6} {'state':<10}")
        w('-' * 92)
        for r in sorted(rows, key=lambda x: x['run']):
            w(f"{r['label'][:30]:<30} {r['effort']:<6} {r['cost_usd']:>8.4f} "
              f"{fmt(r['out']):>8} {fmt(r['cr']):>8} {r['turns']:>6} {r['secs']:>6.0f} {r['state']:<10}")
        w('-' * 92)
        sub = sum(r['cost_usd'] for r in rows)
        w(f"   subtotal ${sub:.4f} | out {fmt(sum(r['out'] for r in rows))} | "
          f"turns {sum(r['turns'] for r in rows)} | mean ${sub / len(rows):.4f}/run")

    w('')
    w(f"{len(side)} sidecar run(s) across {len(by_model)} model(s) | "
      f"total ${sum(r['cost_usd'] for r in side):.4f} | "
      f"out {fmt(sum(r['out'] for r in side))} | turns {sum(r['turns'] for r in side)}")
    if len(by_model) > 1:
        w("   (total is a spend figure, not a comparison - per-model subtotals above are the "
          "comparable unit)")


def family_of(label):
    """Shape family = the label's prefix before the first ':' (the stable
    authoring convention: 'plancheck:memory-gotchas' -> 'plancheck')."""
    return label.split(':', 1)[0] if ':' in label else label


def compute_candidates(rows):
    """Provisional over-pin candidates derived from the archive (Anthropic rows only).

    A candidate is a (family, effort) cell that is clean-only, whose next-lower
    rung cell is also clean-only, but which cost >= CANDIDATE_RATIO_MIN x as much
    per agent for comparable work (mean-turns ratio <= CANDIDATE_TURNS_MAX).
    Cost alone cannot flag a cell -- adjacent rungs cost ~1.5-2x by pricing
    design -- so the turns proxy separates 'naturally pricier rung' from 'same
    work, deeper reasoning, no better outcome'. Candidates are computed, never
    narrated; the action is a substituted downgrade on the family's next natural
    dispatch, and a falsification there removes the candidate on the next run.
    """
    by_family = {}
    for r in rows:
        if r.get('source') == 'sidecar':
            continue
        by_family.setdefault(family_of(r.get('label', '?')), []).append(r)
    cands = []
    for fam, grp in by_family.items():
        # Rows archived without transcripts (cost 0) are unmeasurable -- exclude.
        grp = [r for r in grp if r.get('cost', 0) > 0]
        cells = {}
        for r in grp:
            cells.setdefault(r.get('effort', '?'), []).append(r)
        rungs = sorted((e for e in cells if e in EFFORT_ORDER),
                       key=lambda e: EFFORT_ORDER[e])
        for hi, lo in zip(rungs[1:], rungs):
            g_hi, g_lo = cells[hi], cells[lo]
            if len(g_hi) < CANDIDATE_MIN_N or len(g_lo) < CANDIDATE_MIN_N:
                continue
            if any(outcome_of(r) != 'clean' for r in g_hi + g_lo):
                continue
            c_hi = sum(r.get('cost', 0) for r in g_hi) / len(g_hi)
            c_lo = sum(r.get('cost', 0) for r in g_lo) / len(g_lo)
            if c_lo <= 0 or c_hi / c_lo < CANDIDATE_RATIO_MIN:
                continue
            t_hi = sum(r.get('turns', 0) for r in g_hi) / len(g_hi)
            t_lo = sum(r.get('turns', 0) for r in g_lo) / len(g_lo)
            if t_lo > 0 and t_hi / t_lo > CANDIDATE_TURNS_MAX:
                continue
            cands.append(dict(family=fam, effort=hi, suggested=lo,
                              cost_ratio=round(c_hi / c_lo, 2),
                              n_hi=len(g_hi), n_lo=len(g_lo)))
    return sorted(cands, key=lambda c: -c['cost_ratio'])


def load_run_ledger():
    """(archived_runs, ignored_runs) from the archive jsonl — one file, whole ledger.

    Archived = any agent record's run (rated work already in the store). Ignored =
    {'run': id, 'ignored': true} sentinel lines. Both are terminal states for a run,
    so collection skips them; re-reporting an archived run every invocation is noise
    (another session may archive a run between two of this session's invocations —
    observed 2026-07-27).
    """
    archived, ignored = set(), set()
    if os.path.exists(ARCHIVE):
        for line in open(ARCHIVE, encoding='utf-8'):
            if line.strip():
                try:
                    rec = json.loads(line)
                    (ignored if rec.get('ignored') else archived).add(rec.get('run'))
                except json.JSONDecodeError:
                    pass
    return archived, ignored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session')
    ap.add_argument('--verdicts', help='JSON {label: verdict} or {label: [verdict, effort]}; appends to the archive')
    ap.add_argument('--archive-summary', action='store_true')
    ap.add_argument('--allow-unresolved-effort', action='store_true',
                    help="archive '?' pins anyway (default: refuse -- they cannot feed Effort Calibration)")
    ap.add_argument('--ignore-run', metavar='RUN_ID',
                    help='permanently exclude a run from collection and summary (synthetic arms, '
                         "un-ratable foreign-session work); writes an 'ignored' sentinel to the archive")
    ap.add_argument('--reason', default='', help='why the run is ignored (stored on the sentinel)')
    ap.add_argument('--sidecar-ledger', default=SIDECAR_LEDGER,
                    help='DeepSeek spend ledger to read as the second source (default: %(default)s)')
    ap.add_argument('--no-sidecar', action='store_true',
                    help='skip the sidecar ledger entirely')
    ap.add_argument('--sidecar-all', action='store_true',
                    help='include unlabeled sidecar rows (legacy/ad-hoc; default: labeled only)')
    a = ap.parse_args()

    if a.ignore_run:
        archived, ignored = load_run_ledger()
        if a.ignore_run in ignored:
            w(f'{a.ignore_run} is already ignored -- nothing appended.')
            return 0
        if a.ignore_run in archived:
            w(f'REFUSED: {a.ignore_run} has rated agent records in the archive -- it is '
              'archived work, not noise. An ignore sentinel would misdescribe it.')
            return 1
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        with open(ARCHIVE, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'run': a.ignore_run, 'ignored': True,
                                'reason': a.reason, 'date': stamp}) + '\n')
        w(f'Ignored {a.ignore_run} -- it will no longer surface in collection or summary.')
        return 0

    if a.archive_summary:
        if not os.path.exists(ARCHIVE):
            w(f'No archive at {ARCHIVE} yet.')
            return 0
        rows = [json.loads(l) for l in open(ARCHIVE, encoding='utf-8') if l.strip()]
        ignored_n = sum(1 for r in rows if r.get('ignored'))
        rows = [r for r in rows if not r.get('ignored')]
        side = [r for r in rows if r.get('source') == 'sidecar']
        rows = [r for r in rows if r.get('source') != 'sidecar']
        by = {}
        for r in rows:
            by.setdefault((r.get('effort', '?'), outcome_of(r)), []).append(r)
        w(f"{'effort':<8} {'outcome':<13} {'n':>4} {'cost/agent':>11}")
        for (e, v), g in sorted(by.items()):
            w(f'{e:<8} {v:<13} {len(g):>4} {fmt(sum(x.get("cost", 0) for x in g)//len(g)):>11}')
        w(f'\n{len(rows)} archived agents across {len({r.get("run", "?") for r in rows})} runs.'
          + (f' ({ignored_n} ignored-run sentinel(s) excluded.)' if ignored_n else ''))
        if side:
            sby = {}
            for r in side:
                sby.setdefault((r.get('effort', '?'), outcome_of(r)), []).append(r)
            w('')
            w('-- DeepSeek sidecar (USD; requested-effort coordinate -- do not compare to rungs above) --')
            w(f"{'effort':<8} {'outcome':<13} {'n':>4} {'$/agent':>9}")
            for (e, v), g in sorted(sby.items()):
                w(f"{e:<8} {v:<13} {len(g):>4} {sum(x.get('cost_usd', 0) for x in g)/len(g):>9.4f}")
            w(f"{len(side)} archived sidecar runs | total ${sum(x.get('cost_usd', 0) for x in side):.4f}")
        _report_candidates(rows)
        return 0

    session = a.session or find_session_dir()
    if not session:
        w('Could not locate a session directory with workflow runs.')
        return 1
    rows = collect(session) + ([] if a.no_sidecar
                               else collect_sidecar(a.sidecar_ledger, a.sidecar_all))
    archived, ignored = load_run_ledger()
    skip = {r['run'] for r in rows if r['run'] in ignored or r['run'] in archived}
    rows = [r for r in rows if r['run'] not in skip]
    if skip:
        w(f"(skipping {len(skip)} run(s) already in the archive ledger: {', '.join(sorted(skip))})")
    if not rows:
        report(rows)
        return 0

    # Merge verdicts BEFORE reporting, so the table, the per-effort roll-up, and the
    # unresolved-pin warning all describe what would actually be archived. Reporting
    # first made the warning fire on pins the verdicts file had already supplied.
    pending = load_pending_verdicts()
    if a.verdicts or pending:
        v = dict(pending)
        if a.verdicts:
            explicit = json.loads(open(a.verdicts, encoding='utf-8').read()) \
                if os.path.exists(a.verdicts) else json.loads(a.verdicts)
            v.update(explicit)
        if pending:
            w(f'Merged {len(pending)} verdict(s) from {PENDING_VERDICTS}.')
        for r in rows:
            ent = v.get(r['label'])
            if isinstance(ent, (list, tuple)):
                r['outcome'] = norm_outcome(ent[0])
                if len(ent) > 1 and ent[1]:
                    r['effort'] = ent[1]
                if len(ent) > 2 and ent[2] == 'probe':
                    r['probe'] = True
            elif ent:
                r['outcome'] = norm_outcome(ent)
            else:
                r['outcome'] = 'unrated'
            if r['outcome'] not in OUTCOMES + ('unrated',):
                w(f"REJECTED unknown outcome '{r['outcome']}' for {r['label']} -- "
                  f'allowed: {", ".join(OUTCOMES)} (legacy verdict words map through)')
                return 1

    report(rows)
    if not (a.verdicts or pending):
        return 0

    # Idempotency: archiving is the default path, so re-invocation is expected. Duplicate
    # run records would silently inflate --archive-summary's n and skew cost/agent.
    archived_runs = set()
    if os.path.exists(ARCHIVE):
        for line in open(ARCHIVE, encoding='utf-8'):
            if line.strip():
                try:
                    archived_runs.add(json.loads(line).get('run'))
                except json.JSONDecodeError:
                    pass
    fresh = [r for r in rows if r['run'] not in archived_runs]
    skipped = len(rows) - len(fresh)
    if not fresh:
        runs = ', '.join(sorted({r['run'] for r in rows}))
        w(f'\nAlready archived ({skipped} agents, run {runs}) -- nothing appended.')
        return 0

    unresolved = [r['label'] for r in fresh if r['effort'] == '?']
    if unresolved and not a.allow_unresolved_effort:
        w(f'\nREFUSED: {len(unresolved)} agent(s) have no resolved effort pin; archiving them '
          'would write records Effort Calibration cannot use:')
        for lbl in unresolved:
            w(f'  {lbl}')
        w('Supply [verdict, effort] pairs in the verdicts file, or pass '
          '--allow-unresolved-effort to archive them as "?" anyway.')
        return 1

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with open(ARCHIVE, 'a', encoding='utf-8') as f:
        for r in fresh:
            r['date'] = stamp
            f.write(json.dumps(r) + '\n')
    w(f'\nArchived {len(fresh)} agent records to {ARCHIVE}.'
      + (f' Skipped {skipped} already-archived.' if skipped else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
