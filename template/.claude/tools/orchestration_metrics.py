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

Cost is normalized to base-input-token equivalents (fresh input x1, output x5,
cache-write x1.25, cache-read x0.1) so EFFORT rungs are comparable in one number.
`turns` counts API calls, deduplicated by `message.id` -- see agent_usage(). That figure
weights token types but not the model, so it measures VOLUME: across models it is
not a quota figure, because plan quota is billed per model. `qcost` applies the
per-model QUOTA_W multiplier for cross-model calls; it is None for any model with
no recorded weight, and the report names those rather than defaulting them to 1.0.

Sidecar runs are a SECOND SOURCE read from exact `-R` records named by assistant
launch calls in this session's transcript. They surface side-by-side and are NEVER
merged into Workflow tables: their cost is transport-specific and their effort is
a requested vendor coordinate, not an Anthropic rung. The global spend ledger has
no Claude Code session id, so only explicit `--sidecar-all` reports it and that mode
never archives. Dedup reuses the run ledger via the synthetic run id
`sidecar-<timestamp>-<label>`.

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
  orchestration_metrics.py --manifest-seed seed.json --manifest-out manifest.json
                                                join exact Workflow/sidecar evidence; never archive
"""
import argparse, json, os, re, shlex, sys
from datetime import datetime, timezone

IN_W, OUT_W, CW_W, CR_W = 1.0, 5.0, 1.25, 0.1


def agent_cost(u):
    """Base-input-token equivalents for one agent's usage dict.

    Fresh `input_tokens` bills at 1.0 and was previously omitted entirely. Under
    caching it is small, but it is exactly the UNCACHED portion -- so omitting it
    understated the first turn of every agent and any cache miss after it.
    """
    return (u.get('inp', 0) * IN_W + u.get('out', 0) * OUT_W
            + u.get('cw', 0) * CW_W + u.get('cr', 0) * CR_W)

# Plan-quota weight per model, relative to sonnet = 1.0.
#
# `cost` above weights token TYPES but not the MODEL, so it measures VOLUME and is
# not comparable across models on its own: plan quota is billed per model, so the
# cheapest cell by token-equivalents is not the cheapest cell by quota. Re-measured
# 2026-08-19 over the 272 surviving archived agents, opus-low and sonnet-medium are
# within 5% on volume (272,524 vs 261,948, n=21/41) while opus-low draws 2.60x the
# quota -- an inversion invisible until the weight is applied.
#
# Provenance: the opus:sonnet per-token price ratio (reference/model_ladder_evidence.md
# section sonnet). Substring match on the model id, longest key first.
#
# A model absent from this table reports qcost as None and is named in the report.
# It never silently defaults to 1.0: a quietly-wrong quota number is worse than a
# missing one, and this table is exactly the sort of hardcoded pricing fact that
# rots on the vendor's schedule (instruction_quality section 16).
QUOTA_W = {'opus': 2.5, 'sonnet': 1.0}


def quota_weight(model):
    """Plan-quota multiplier for a model id, or None when unweighted."""
    m = (model or '').lower()
    for key in sorted(QUOTA_W, key=len, reverse=True):
        if key in m:
            return QUOTA_W[key]
    return None


def quota_cost(cost, model):
    """Quota-equivalent cost, or None when the model carries no weight."""
    weight = quota_weight(model)
    return None if weight is None else cost * weight
# Anchored to the project, not the cwd: budget_posture.py imports this per prompt from
# whatever cwd the hook runs in, and a cwd-relative path would read every verdict as absent.
_PROJECT_DIR = os.environ.get('CLAUDE_PROJECT_DIR') or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(_PROJECT_DIR, '.claude', 'orchestration_metrics.jsonl')
# Falsification outcomes, recorded at consumption (see module docstring).
OUTCOMES = ('clean', 'defects', 'rework', 'discarded')
LEGACY_VERDICTS = {'right-sized': 'clean', 'overshoot': 'clean',
                   'undershoot': 'rework', 'wasted': 'discarded',
                   # fit-vocabulary used by consumption-time rich verdicts
                   'fit': 'clean', 'excellent': 'clean',
                   'fit-high-value': 'clean', 'fit-highest-value': 'clean',
                   'fit-with-one-correction': 'defects', 'misfit-input': 'discarded'}
SIDECAR_LEDGER = os.path.expanduser('~/.claude/deepseek_spend.jsonl')
# Derived over-pin candidates (recomputed on every --archive-summary run). The
# dispatch-time surface (report()) reads it so the pin decision sees the queue.
CANDIDATES_FILE = os.path.join(_PROJECT_DIR, '.claude', 'orchestration_candidates.json')
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
PENDING_VERDICTS = os.path.join(_PROJECT_DIR, '.claude', 'orchestration_verdicts.json')
# Legacy write location (pre-2026-09-05). No longer written to, but a leftover copy from an
# older session must not go silently unread -- load_pending_verdicts() merges it in, root wins.
LEGACY_PENDING_VERDICTS = os.path.join(_PROJECT_DIR, '.claude', 'scratch', 'orchestration_verdicts.json')


def _read_verdict_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return {k: v for k, v in (json.load(f) or {}).items() if v}
    except (json.JSONDecodeError, OSError):
        return {}


def load_pending_verdicts(path=None):
    """Verdicts recorded mid-session. Null/absent entries are debts, not verdicts.

    Keys are `run_id:label` (matched first) or a bare `label` (legacy; resolved by the caller
    only when it names exactly one unarchived candidate -- see pending_report()). Merges
    LEGACY_PENDING_VERDICTS when present, root winning on key collision, and prints one line
    naming how many entries came from the legacy path so a stray copy is never silently shadowed.
    """
    path = path or PENDING_VERDICTS
    merged = _read_verdict_file(path)
    legacy = _read_verdict_file(LEGACY_PENDING_VERDICTS)
    added = sum(1 for k in legacy if k not in merged)
    for k, v in legacy.items():
        merged.setdefault(k, v)
    if added:
        # stderr: budget_posture.py calls this per prompt, and its stdout is the model channel.
        sys.stderr.write(f'Merged {added} verdict(s) from legacy {LEGACY_PENDING_VERDICTS} '
                         '(root wins on collision).\n')
    return merged


def w(s):
    sys.stdout.write(str(s).encode('ascii', 'replace').decode('ascii') + '\n')


def fmt(n):
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1000:
        return f'{n/1000:.1f}k'
    return str(int(n))


def norm_outcome(v):
    """Accept the outcome vocabulary; legacy verdict words map through.
    Rich dict verdicts (consumption-time entries carrying model/effort/fit/rationale)
    unwrap via their 'fit'/'outcome' field."""
    if isinstance(v, dict):
        v = v.get('outcome') or v.get('fit') or v.get('verdict') or '?'
    return LEGACY_VERDICTS.get(v, v)


def outcome_of(r):
    """Archive outcome field, with legacy fallbacks: 'verdict' (pre-2026-08)
    and 'fit' (earliest schema) both carry verdict words."""
    v = r.get('outcome') or r.get('verdict') or r.get('fit') or '?'
    return norm_outcome(v)


def find_session_dir(session_id=None):
    root = os.path.expanduser('~/.claude/projects')
    cwd = os.path.abspath(os.getcwd())

    # Exact identity first. The mtime scan below cannot tell this session's runs from a peer's:
    # concurrent sessions share one machine, and whichever touched its workflows/ dir last wins the
    # max(). That misattributes a whole session's cost to another and is silent -- the table renders
    # normally, just with someone else's agent labels. Measured 2026-08-18: a peer session sharing
    # the same minute won the tie and 84 foreign agents were reported as this session's.
    # An explicit id is authoritative: when it names no workflows dir, the session has dispatched
    # nothing, and the scan must not answer for it with a peer's.
    sid = session_id or os.environ.get('CLAUDE_CODE_SESSION_ID')
    if sid:
        for d in (os.listdir(root) if os.path.isdir(root) else []):
            p = os.path.join(root, d, sid)
            if os.path.isdir(os.path.join(p, 'workflows')):
                return p
        if session_id:
            return None

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


# Workflows whose agents are a benchmark INSTRUMENT's internal stages (judges, verifiers, persist,
# rank per cell; reachability judges per key item), not delegated work anyone rates: their cost
# home is `Model Effort Calibration Baseline.md`, and the archive step's synthetic-arm stop-gate
# already refuses them. Keyed on the record's `workflowName` (= the script's `meta.name`).
# Measured 2026-09-09: 106 of 127 records in one session were score-cell, 681 unrated labels.
MEASUREMENT_WORKFLOWS = frozenset({'score-cell', 'key-reachability'})


def _session_candidates(session_dir):
    """[(run_id, label)] from workflows/*.json workflow_agent entries, with pairs already in
    ARCHIVE removed. Reads workflow records plus the archive JSONL -- no per-agent
    transcripts. The archive scan is O(archive size), so a line is json-parsed only when it
    names one of this session's run ids (substring prefilter); a per-turn caller pays one
    file read, not ten thousand json.loads."""
    wdir = os.path.join(session_dir, 'workflows')
    if not os.path.isdir(wdir):
        return [], set(), set()
    candidates, run_ids = [], set()
    for fn in sorted(os.listdir(wdir)):
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(wdir, fn), encoding='utf-8') as fh:
                run = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if run.get('workflowName') in MEASUREMENT_WORKFLOWS:
            continue
        rid = run.get('runId') or fn[:-5]
        run_ids.add(rid)
        for e in run.get('workflowProgress') or []:
            if e.get('type') != 'workflow_agent':
                continue
            candidates.append((rid, e.get('label') or '(unlabeled)'))
    archived_pairs = set()
    if run_ids and os.path.exists(ARCHIVE):
        with open(ARCHIVE, encoding='utf-8') as fh:
                for line in fh:
                    if not any(rid in line for rid in run_ids):
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get('run') in run_ids and rec.get('label'):
                        archived_pairs.add((rec['run'], rec['label']))
    unarchived = [p for p in candidates if p not in archived_pairs]
    return unarchived, run_ids, {lab for _, lab in candidates}


def pending_report(session_dir):
    """([(run_id, label)] still pending, [ambiguous bare labels], [unmatched bare labels]).

    Verdict keys are `run_id:label`, matched first. A bare `label` key resolves a pair only
    when that label names exactly one unarchived candidate for this session -- an ambiguous
    bare label (the same label on 2+ unarchived runs) stays pending on every match, and is
    reported separately so --pending can name the ambiguity rather than silently rating none
    of them (orchestration_metrics.md SS Incremental rating).
    """
    candidates, run_ids, session_labels = _session_candidates(session_dir)
    verdicts = load_pending_verdicts()
    # A key is `run_id:label` only when its prefix up to the first ':' is one of this
    # session's actual run ids -- labels routinely carry their own colon (the family:name
    # convention family_of() reads), so "colon present" alone cannot tell exact from bare.
    exact_keys, bare_keys = set(), set()
    for k in verdicts:
        prefix = k.split(':', 1)[0] if ':' in k else None
        (exact_keys if prefix in run_ids else bare_keys).add(k)
    bare_counts = {}
    for _, lab in candidates:
        bare_counts[lab] = bare_counts.get(lab, 0) + 1
    remaining = []
    for rid, lab in candidates:
        if f'{rid}:{lab}' in exact_keys:
            continue
        if lab in bare_keys and bare_counts.get(lab) == 1:
            continue
        remaining.append((rid, lab))
    ambiguous = sorted(lab for lab in bare_keys if bare_counts.get(lab, 0) > 1)
    # A bare key naming one of THIS session's labels but zero unarchived candidates is a verdict
    # that rated nothing (the pair was archived before it was recorded). Silence there is the
    # same defect as the ambiguous case. Keys naming no label of this session are other
    # sessions' history (the verdicts file accumulates) and are not reported.
    unmatched = sorted(lab for lab in bare_keys
                       if lab in session_labels and bare_counts.get(lab, 0) == 0)
    return remaining, ambiguous, unmatched


def pending_labels(session_dir):
    """[(run_id, label)] this session's workflow_agent dispatches not yet archived or resolved
    by a pending verdict. See pending_report() for the matching rule."""
    return pending_report(session_dir)[0]


def pending_count(session_dir=None, session_id=None):
    """Unrated-dispatch count for this session, or -1 when unknown. Never raises -- called from
    a per-turn hook, where an exception must cost a routing hint, never the turn."""
    try:
        session_dir = session_dir or find_session_dir(session_id)
        if not session_dir:
            return -1
        return len(pending_labels(session_dir))
    except Exception:
        return -1


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


def run_date_of(value):
    """UTC date (YYYY-MM-DD) of a run instant, or None when unrecoverable.

    Accepts an ISO-8601 string, epoch-millis (Workflow `startedAt`/`startTime`),
    or the synthetic sidecar run id 'sidecar-<iso>-<label>'. Never falls back to
    today: the archive date is a different fact, and a wrong run date is
    indistinguishable from a right one.
    """
    if value is None or value == '?':
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0, timezone.utc).strftime('%Y-%m-%d')
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v.startswith('sidecar-'):
        v = v[len('sidecar-'):]
    m = re.match(r'(\d{4}-\d{2}-\d{2})(?:[T ]|$)', v)
    return m.group(1) if m else None


def agent_usage(run_dir):
    """agentId -> token/turn/tool/wall usage from the transcripts.

    ONE API CALL PER `message.id`, never one per record. An assistant message
    spanning several content blocks is written as several JSONL lines that each
    repeat the SAME `message.usage`, so accumulating per record double-counts
    tokens, turns and tool calls alike. Measured 2026-08-19 over one six-agent
    run: 132 assistant records for 66 real calls -- an even 2.00x overall, and
    NOT uniform per agent (1.69x-2.47x), so it inflated effort RATIOS as well as
    absolute figures. Every median and rung multiplier published before that date
    carries the inflation.

    Grouping invariants, verified rather than assumed (all 66 groups): within one
    message.id, cache_read / cache_creation / input are constant and output is
    monotonic non-decreasing -- so the LAST record holds the finalized usage and
    the complete content array.
    """
    usage = {}
    if not os.path.isdir(run_dir):
        return usage
    for fn in os.listdir(run_dir):
        if not (fn.startswith('agent-') and fn.endswith('.jsonl')):
            continue
        aid = fn[len('agent-'):-len('.jsonl')]
        calls, order, recs = {}, [], 0
        first = last = None
        with open(os.path.join(run_dir, fn), encoding='utf-8') as fh:
            for line in fh:
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
                recs += 1
                m = o.get('message') or {}
                # No message.id -> fall back to the record's own uuid. A missing key
                # must never collapse distinct calls into one bucket, which would
                # under-count in exactly the direction this fix corrects.
                mid = m.get('id') or o.get('uuid') or ('rec-%d' % recs)
                if mid not in calls:
                    order.append(mid)
                calls[mid] = m                      # later record wins
        u = dict(inp=0, out=0, cw=0, cr=0, turns=len(calls), tools=0,
                 recs=recs, secs=0.0)
        for mid in order:
            m = calls[mid]
            us = m.get('usage') or {}
            u['inp'] += us.get('input_tokens', 0) or 0
            u['out'] += us.get('output_tokens', 0) or 0
            u['cw'] += us.get('cache_creation_input_tokens', 0) or 0
            u['cr'] += us.get('cache_read_input_tokens', 0) or 0
            for b in (m.get('content') or []):
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    u['tools'] += 1
        u['secs'] = (last - first).total_seconds() if (first and last) else 0.0
        u['first_ts'] = first.isoformat() if first else None
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
        with open(os.path.join(wdir, fn), encoding='utf-8') as fh:
            run = json.load(fh)
        rid = run.get('runId') or fn[:-5]
        eff = efforts_for(run)
        usage = agent_usage(os.path.join(session, 'subagents', 'workflows', rid))
        for e in run.get('workflowProgress') or []:
            if e.get('type') != 'workflow_agent':
                continue
            aid, lab = e.get('agentId'), e.get('label') or '(unlabeled)'
            u = usage.get(aid, dict(inp=0, out=0, cw=0, cr=0, turns=0, tools=0,
                                    recs=0, secs=0.0, first_ts=None))
            cost = agent_cost(u)
            # Run date, most specific source first: this agent's own start, the
            # workflow record's instant, then the transcript's earliest turn.
            run_date = (run_date_of(e.get('startedAt')) or run_date_of(e.get('queuedAt'))
                        or run_date_of(run.get('timestamp')) or run_date_of(run.get('startTime'))
                        or run_date_of(u.pop('first_ts', None)))
            u.pop('first_ts', None)
            rows.append(dict(
                run=rid, workflow=run.get('workflowName') or '?', phase=e.get('phaseTitle') or '',
                agent_id=aid, label=lab, model=e.get('model') or '?',
                effort=eff.get(lab, '?'), state=e.get('state') or '?',
                run_date=run_date,
                cost=cost, qcost=quota_cost(cost, e.get('model') or ''), **u))
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
    with open(ledger, encoding='utf-8') as fh:
        for line in fh:
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
                run=f'sidecar-{ts}-{lab}', run_date=run_date_of(ts),
                workflow='sidecar', phase='', agent_id='',
                label=lab, model=str(rec.get('servedModel') or rec.get('requestedModel') or '?'),
                effort=rec.get('effort') or '?',
                state=('completed' if rec.get('exitCode') == 0 else f"exit-{rec.get('exitCode')}"),
                source='sidecar', cost=0.0, cost_usd=rec.get('costUSD'),
                cost_basis=rec.get('costBasis') or '',
                inp=0, out=rec.get('outputTokens') or 0, cw=0,
                cr=rec.get('cacheReadTokens') or 0, recs=0,
                turns=rec.get('numTurns') or 0, tools=0,
                secs=(rec.get('durationMs') or 0) / 1000.0))
    return rows


class ManifestError(ValueError):
    """A seed cannot be joined to exactly one evidence row per job."""


def _sidecar_record_row(path):
    """One explicit -R record shaped for manifest and archive consumers."""
    try:
        with open(path, encoding='utf-8') as fh:
            rec = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    label = rec.get('label')
    if not label:
        return None
    exit_code = rec.get('exitCode')
    timestamp = rec.get('timestamp') or '?'
    return dict(
        source='sidecar', label=label,
        run='sidecar-' + str(timestamp) + '-' + label,
        run_date=run_date_of(timestamp), workflow='sidecar', phase='', agent_id='',
        model=str(rec.get('servedModel') or rec.get('requestedModel') or '?'),
        effort=rec.get('effort') or '?',
        state=(('completed' if exit_code == 0 else 'exit-' + str(exit_code))
               if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 'unknown'),
        transport=rec.get('transport'), currency=rec.get('costModel'),
        record_path=os.path.abspath(path), cost=0.0,
        cost_usd=rec.get('costUSD'), cost_basis=rec.get('costBasis') or '',
        inp=rec.get('inputTokens') or 0, out=rec.get('outputTokens') or 0,
        cw=rec.get('cacheWriteTokens') or 0, cr=rec.get('cacheReadTokens') or 0,
        recs=0, turns=rec.get('numTurns') or 0, tools=rec.get('toolCalls') or 0,
        secs=(rec.get('durationMs') or 0) / 1000.0)


def collect_sidecar_records(record_dir):
    """Read per-job `*.record.json` files without touching the spend ledger."""
    rows = []
    if not record_dir or not os.path.isdir(record_dir):
        return rows
    for base, _, names in os.walk(record_dir):
        for name in sorted(names):
            if not name.endswith('.record.json'):
                continue
            row = _sidecar_record_row(os.path.join(base, name))
            if row:
                rows.append(row)
    return rows


def _resolve_session_path(value, cwd):
    path = os.path.expanduser(str(value))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.abspath(path)


def _iso_instant(value):
    try:
        instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return instant.replace(tzinfo=instant.tzinfo or timezone.utc)
    except (TypeError, ValueError):
        return None


def _fanout_jobs_paths(transcript):
    """Fan-out jobs files named by assistant shell calls in one transcript."""
    paths = set()
    with open(transcript, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get('type') != 'assistant':
                continue
            cwd = entry.get('cwd') or _PROJECT_DIR
            for item in (entry.get('message') or {}).get('content') or []:
                if not isinstance(item, dict) or item.get('type') != 'tool_use':
                    continue
                tool_name = str(item.get('name') or '').split('.')[-1]
                if tool_name not in ('Bash', 'PowerShell'):
                    continue
                command = (item.get('input') or {}).get('command')
                if not isinstance(command, str):
                    continue
                try:
                    tokens = shlex.split(command, posix=True)
                except ValueError:
                    continue
                for index, token in enumerate(tokens):
                    normalized = token.replace('\\', '/')
                    if ((normalized.endswith('/sidecar_fanout.py')
                         or normalized == 'sidecar_fanout.py')
                            and index + 1 < len(tokens)):
                        paths.add(_resolve_session_path(tokens[index + 1], cwd))
    return paths


def _sidecar_record_launches(session):
    """Exact -R paths proven by assistant tool calls in this session transcript."""
    transcript = session if str(session).endswith('.jsonl') else str(session) + '.jsonl'
    launches = {}
    if not os.path.isfile(transcript):
        return launches
    fanout_jobs_paths = _fanout_jobs_paths(transcript)
    file_versions = {}
    with open(transcript, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get('type') != 'assistant':
                continue
            message = entry.get('message') or {}
            cwd = entry.get('cwd') or _PROJECT_DIR
            launched_at = _iso_instant(entry.get('timestamp'))
            for item in message.get('content') or []:
                if not isinstance(item, dict) or item.get('type') != 'tool_use':
                    continue
                tool_name = str(item.get('name') or '').split('.')[-1]
                tool_input = item.get('input') or {}
                if tool_name == 'Write' and isinstance(tool_input.get('content'), str):
                    file_path = _resolve_session_path(tool_input.get('file_path'), cwd)
                    if file_path in fanout_jobs_paths:
                        file_versions[file_path] = tool_input['content']
                    continue
                if tool_name == 'Edit':
                    file_path = _resolve_session_path(tool_input.get('file_path'), cwd)
                    if file_path not in fanout_jobs_paths:
                        continue
                    old = tool_input.get('old_string')
                    new = tool_input.get('new_string')
                    prior = file_versions.get(file_path)
                    if isinstance(prior, str) and isinstance(old, str) and isinstance(new, str):
                        count = prior.count(old)
                        if count == 1 or (count and tool_input.get('replace_all')):
                            file_versions[file_path] = prior.replace(old, new, -1 if tool_input.get('replace_all') else 1)
                    continue
                if tool_name not in ('Bash', 'PowerShell'):
                    continue
                command = tool_input.get('command')
                if not isinstance(command, str):
                    continue
                try:
                    tokens = shlex.split(command, posix=True)
                except ValueError:
                    continue
                for index, token in enumerate(tokens):
                    normalized = token.replace('\\', '/')
                    paths = []
                    if normalized.endswith('/sidecar_fanout.py') or normalized == 'sidecar_fanout.py':
                        if index + 1 >= len(tokens):
                            continue
                        jobs_path = _resolve_session_path(tokens[index + 1], cwd)
                        out_dir = os.path.dirname(jobs_path)
                        try:
                            out_index = tokens.index('--out-dir', index + 2)
                            out_dir = _resolve_session_path(tokens[out_index + 1], cwd)
                        except (ValueError, IndexError):
                            pass
                        try:
                            if jobs_path in file_versions:
                                jobs = json.loads(file_versions[jobs_path])
                            else:
                                with open(jobs_path, encoding='utf-8') as fh:
                                    jobs = json.load(fh)
                        except (OSError, json.JSONDecodeError):
                            continue
                        for job in jobs if isinstance(jobs, list) else []:
                            label = job.get('label') if isinstance(job, dict) else None
                            if label:
                                paths.append(os.path.join(out_dir, str(label) + '.record.json'))
                    elif normalized.endswith('_sidecar.sh'):
                        try:
                            record_index = tokens.index('-R', index + 1)
                            paths.append(_resolve_session_path(tokens[record_index + 1], cwd))
                        except (ValueError, IndexError):
                            continue
                    for path in paths:
                        absolute = _resolve_session_path(path, cwd)
                        prior = launches.get(absolute)
                        if prior is None or (launched_at and (not prior or launched_at > prior)):
                            launches[absolute] = launched_at
    return launches


def collect_session_sidecar(session):
    """Sidecar rows with exact record evidence launched by this session only."""
    rows = []
    for path, launched_at in sorted(_sidecar_record_launches(session).items()):
        row = _sidecar_record_row(path)
        if not row:
            continue
        prefix = row['run'][len('sidecar-'):]
        recorded_at = _iso_instant(prefix[:prefix.find('-' + row['label'])])
        if launched_at and (not recorded_at or recorded_at < launched_at):
            continue
        rows.append(row)
    return rows


def _manifest_requested(job):
    if isinstance(job.get('requested'), dict):
        return dict(job['requested'])
    keys = ('role', 'model', 'effort', 'transport', 'currency', 'agentType', 'shape')
    return {key: job.get(key) for key in keys}


def _manifest_status(state):
    value = str(state or 'unknown').lower()
    if value in ('completed', 'done', 'succeeded', 'success'):
        return 'completed'
    if value.startswith('exit-') or value in ('failed', 'error', 'cancelled', 'canceled'):
        return 'failed'
    return value


def build_manifest(seed, workflow_rows, sidecar_rows, verdicts=None):
    """Join each seed label to exactly one Workflow or sidecar evidence row."""
    if not isinstance(seed, dict) or not isinstance(seed.get('jobs'), list) or not seed['jobs']:
        raise ManifestError('manifest seed needs a non-empty jobs array')
    jobs = seed['jobs']
    labels = [job.get('label') for job in jobs if isinstance(job, dict)]
    if len(labels) != len(jobs) or any(not label for label in labels):
        raise ManifestError('every manifest seed job needs a label')
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        raise ManifestError('duplicate seed label(s): ' + ', '.join(duplicates))

    evidence = []
    for row in workflow_rows or []:
        evidence.append(dict(row, source=row.get('source') or 'workflow'))
    evidence.extend(dict(row, source='sidecar') for row in (sidecar_rows or []))
    verdicts = verdicts or {}
    out = []
    for job in jobs:
        label = job['label']
        matches = [row for row in evidence if row.get('label') == label]
        if len(matches) != 1:
            raise ManifestError('%s evidence rows for label %s; expected exactly 1'
                                % (len(matches), label))
        row = matches[0]
        source = row['source']
        outcome = verdicts.get(label)
        if isinstance(outcome, (list, tuple)):
            outcome = outcome[0] if outcome else None
        outcome = norm_outcome(outcome) if outcome else None
        if outcome not in OUTCOMES:
            outcome = None
        effective_model = row.get('model')
        if effective_model == '?':
            effective_model = None
        effective_transport = row.get('transport') if source == 'sidecar' else None
        effective_currency = row.get('currency') if source == 'sidecar' else None
        attestation = ('sidecar record attests served model, transport, and currency; '
                       'effort is a requested coordinate' if source == 'sidecar'
                       else 'Workflow record attests the agent model and state; PINS effort is requested only')
        out.append({
            'label': label,
            'requested': _manifest_requested(job),
            'effective': {
                'model': effective_model,
                'effort': None,
                'transport': effective_transport,
                'currency': effective_currency,
            },
            'status': _manifest_status(row.get('state')),
            'usage': {
                'inputTokens': row.get('inp') or 0,
                'outputTokens': row.get('out') or 0,
                'cacheReadTokens': row.get('cr') or 0,
                'cacheWriteTokens': row.get('cw') or 0,
                'turns': row.get('turns') or 0,
                'toolCalls': row.get('tools') or 0,
                'durationMs': int(round((row.get('secs') or 0) * 1000)),
                'costUSD': row.get('cost_usd'),
            },
            'spillPath': job.get('spillPath'),
            'outcome': outcome,
            'evidence': {
                'source': source,
                'runId': row.get('run'),
                'recordPath': row.get('record_path'),
                'attestation': attestation,
            },
        })
    statuses = [row['status'] for row in out]
    status = ('completed' if all(value == 'completed' for value in statuses)
              else 'failed' if all(value == 'failed' for value in statuses)
              else 'partial')
    return {
        'schemaVersion': seed.get('schemaVersion') or 1,
        'runKey': seed.get('runKey'),
        'route': seed.get('route'),
        'status': status,
        'jobs': out,
    }


def write_manifest(seed_path, output_path, session=None, sidecar_record_dir=None,
                   verdicts_path=None):
    """Build and write a manifest; never reads or appends the metrics archive."""
    with open(seed_path, encoding='utf-8') as fh:
        seed = json.load(fh)
    workflow_rows = collect(session) if session else []
    sidecar_rows = collect_sidecar_records(sidecar_record_dir)
    verdicts = _read_verdict_file(verdicts_path) if verdicts_path else {}
    manifest = build_manifest(seed, workflow_rows, sidecar_rows, verdicts)
    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write('\n')
    return manifest


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

    # Per-model: volume vs plan quota. These diverge, and the divergence is the
    # whole point -- a cross-model call read off `cost` alone reads volume as quota.
    bym, unweighted = {}, set()
    for r in rows:
        bym.setdefault(r['model'], []).append(r)
        if r.get('qcost') is None:
            unweighted.add(r['model'])
    w('')
    w(f"{'model':<24} {'n':>3} {'vol/agent':>11} {'quota/agent':>12} {'x':>5}")
    for m, g in sorted(bym.items(), key=lambda kv: -len(kv[1])):
        n = len(g)
        vol = sum(x['cost'] for x in g) // n
        weight = quota_weight(m)
        q = f"{fmt(int(vol * weight)):>12}" if weight else f"{'?':>12}"
        x = f"{weight:>5.2f}" if weight else f"{'?':>5}"
        w(f"{m[:24]:<24} {n:>3} {fmt(vol):>11} {q} {x}")
    if unweighted:
        w('')
        w("WARNING: no plan-quota weight for " + ', '.join(sorted(unweighted)) + ". Their "
          "quota column is '?' rather than a guess -- add the model to QUOTA_W (relative to "
          "sonnet = 1.0) once its ratio is known, and re-check the existing weights against "
          "current pricing while you are there.")
    report_sidecar(side)
    _report_pending_candidates()


def _report_pending_candidates():
    """Surface the derived over-pin queue where the next pin decision happens."""
    if not os.path.exists(CANDIDATES_FILE):
        return
    try:
        with open(CANDIDATES_FILE, encoding='utf-8') as fh:
            cands = json.load(fh)
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
            cu = r.get('cost_usd')
            cell = f"{cu:>8.4f}" if isinstance(cu, (int, float)) else f"{'n/a':>8}"
            w(f"{r['label'][:30]:<30} {r['effort']:<6} {cell} "
              f"{fmt(r['out']):>8} {fmt(r['cr']):>8} {r['turns']:>6} {r['secs']:>6.0f} {r['state']:<10}")
        w('-' * 92)
        priced = [r for r in rows if isinstance(r.get('cost_usd'), (int, float))]
        sub = sum(r['cost_usd'] for r in priced)
        mean = f"${sub / len(priced):.4f}/run" if priced else 'n/a'
        note = ''
        if len(priced) != len(rows):
            basis = next((r.get('cost_basis') for r in rows if r.get('cost_basis')), 'no dollar price')
            note = f" | {len(rows) - len(priced)} unpriced ({basis}) - excluded from $ figures"
        w(f"   subtotal ${sub:.4f} | out {fmt(sum(r['out'] for r in rows))} | "
          f"turns {sum(r['turns'] for r in rows)} | mean {mean}{note}")

    w('')
    priced_all = [r for r in side if isinstance(r.get('cost_usd'), (int, float))]
    unpriced_all = len(side) - len(priced_all)
    w(f"{len(side)} sidecar run(s) across {len(by_model)} model(s) | "
      f"total ${sum(r['cost_usd'] for r in priced_all):.4f} over {len(priced_all)} priced run(s)"
      + (f" ({unpriced_all} unpriced)" if unpriced_all else '') + " | "
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
        with open(ARCHIVE, encoding='utf-8') as fh:
            for line in fh:
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
    ap.add_argument('--pending', action='store_true',
                    help='print this session\'s unrated dispatches (label debts), never the archive')
    ap.add_argument('--count', action='store_true',
                    help='with --pending, print only the integer count')
    ap.add_argument('--allow-unresolved-effort', action='store_true',
                    help="archive '?' pins anyway (default: refuse -- they cannot feed Effort Calibration)")
    ap.add_argument('--ignore-run', metavar='RUN_ID',
                    help='permanently exclude a run from collection and summary (synthetic arms, '
                         "un-ratable foreign-session work); writes an 'ignored' sentinel to the archive")
    ap.add_argument('--reason', default='', help='why the run is ignored (stored on the sentinel)')
    ap.add_argument('--sidecar-ledger', default=SIDECAR_LEDGER,
                    help='global sidecar ledger used only by --sidecar-all (default: %(default)s)')
    ap.add_argument('--no-sidecar', action='store_true',
                    help='skip session-attributed sidecar records')
    ap.add_argument('--sidecar-all', action='store_true',
                    help='report the global sidecar ledger, including unlabeled history; never archive')
    ap.add_argument('--manifest-seed', help='seed JSON whose exact labels define one non-archiving manifest')
    ap.add_argument('--manifest-out', help='output path for --manifest-seed')
    ap.add_argument('--sidecar-record-dir', help='directory containing per-job *.record.json evidence')
    ap.add_argument('--manifest-verdicts', help='optional outcome JSON for manifest rows')
    a = ap.parse_args()

    if a.manifest_seed:
        if not a.manifest_out:
            w('REFUSED: --manifest-seed requires --manifest-out.')
            return 1
        try:
            manifest = write_manifest(
                a.manifest_seed, a.manifest_out, session=a.session,
                sidecar_record_dir=a.sidecar_record_dir,
                verdicts_path=a.manifest_verdicts or PENDING_VERDICTS)
        except (OSError, json.JSONDecodeError, ManifestError) as exc:
            w('REFUSED manifest: ' + str(exc))
            return 1
        w('Manifest wrote %d exact evidence row(s) to %s.'
          % (len(manifest['jobs']), a.manifest_out))
        return 0

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
        with open(ARCHIVE, encoding='utf-8') as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
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
                gp = [x for x in g if isinstance(x.get('cost_usd'), (int, float))]
                cell = f"{sum(x['cost_usd'] for x in gp)/len(gp):>9.4f}" if gp else f"{'n/a':>9}"
                w(f"{e:<8} {v:<13} {len(g):>4} {cell}")
            sp = [x for x in side if isinstance(x.get('cost_usd'), (int, float))]
            w(f"{len(side)} archived sidecar runs | total ${sum(x['cost_usd'] for x in sp):.4f}"
              f" over {len(sp)} priced")
        _report_candidates(rows)
        return 0

    if a.pending:
        session = a.session or find_session_dir()
        if not session:
            w('Could not locate a session directory with workflow runs.')
            return 1
        items, ambiguous, unmatched = pending_report(session)
        if a.count:
            w(str(len(items)))
            return 0
        w(f'unrated dispatches: {len(items)}')
        for rid, lab in items[:10]:
            w(f'  {rid}:{lab}')
        for lab in ambiguous:
            w(f'  ambiguous bare verdict "{lab}" -- matches multiple unarchived runs; '
              f'rate with run_id:label instead')
        for lab in unmatched[:10]:
            w(f'  unmatched bare verdict "{lab}" -- every run of this session carrying that '
              f'label was archived before the verdict was recorded')
        sidecar_n = len(collect_session_sidecar(session))
        w(f'sidecar: {sidecar_n} exact session record(s) attributed from transcript launches')
        return 0

    session = a.session or find_session_dir()
    if not session:
        w('Could not locate a session directory with workflow runs.')
        return 1
    if a.no_sidecar:
        sidecar_rows = []
    elif a.sidecar_all:
        sidecar_rows = collect_sidecar(a.sidecar_ledger, include_unlabeled=True)
    else:
        sidecar_rows = collect_session_sidecar(session)
    rows = collect(session) + sidecar_rows
    archived, ignored = load_run_ledger()
    skip = {r['run'] for r in rows if r['run'] in ignored or r['run'] in archived}
    rows = [r for r in rows if r['run'] not in skip]
    if skip:
        w(f"(skipping {len(skip)} run(s) already in the archive ledger: {', '.join(sorted(skip))})")
    if not rows:
        report(rows)
        return 0
    if a.sidecar_all:
        report(rows)
        if a.verdicts:
            w('\nREFUSED: --sidecar-all is a global spend report and never archives.')
            return 1
        return 0

    # Merge verdicts BEFORE reporting, so the table, the per-effort roll-up, and the
    # unresolved-pin warning all describe what would actually be archived. Reporting
    # first made the warning fire on pins the verdicts file had already supplied.
    pending = load_pending_verdicts()
    if a.verdicts or pending:
        v = dict(pending)
        if a.verdicts:
            if os.path.exists(a.verdicts):
                with open(a.verdicts, encoding='utf-8') as fh:
                    explicit = json.loads(fh.read())
            else:
                explicit = json.loads(a.verdicts)
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
                # Non-fatal, per the command doc: a malformed verdict leaves ITS row unrated and the
                # archive proceeds; aborting here threw away every other rated row in the session.
                w(f"REJECTED unknown outcome '{r['outcome']}' for {r['label']} -- archived as unrated; "
                  f'allowed: {", ".join(OUTCOMES)} (legacy verdict words map through)')
                r['outcome'] = 'unrated'

    report(rows)
    if not (a.verdicts or pending):
        return 0

    # Idempotency: archiving is the default path, so re-invocation is expected. Duplicate
    # run records would silently inflate --archive-summary's n and skew cost/agent.
    archived_runs = set()
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE, encoding='utf-8') as fh:
            for line in fh:
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
            r['date'] = stamp              # legacy key: archive date, kept for consumers
            r['archived_date'] = stamp
            r.setdefault('run_date', None)  # null is a legible gap; never the archive date
            f.write(json.dumps(r) + '\n')
    w(f'\nArchived {len(fresh)} agent records to {ARCHIVE}.'
      + (f' Skipped {skipped} already-archived.' if skipped else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
