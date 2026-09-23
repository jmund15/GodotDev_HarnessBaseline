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
and reported as an advisory same-model comparison worth running; no pin moves
until it is judged (see /orchestration_metrics "Over-pin candidates").
Legacy verdict words (right-sized/overshoot/undershoot/wasted) map through.

Usage:
  orchestration_metrics.py                      report this session
  orchestration_metrics.py --session <dir>      report a specific session dir
  orchestration_metrics.py --verdicts v.json    rate + append to the archive
  orchestration_metrics.py --archive-summary    roll up the existing archive
  orchestration_metrics.py --run <runId>        per-seat usage of one panel run; never archive
  orchestration_metrics.py --manifest-seed seed.json --manifest-out manifest.json
                                                join exact Workflow/sidecar evidence; never archive
"""
import argparse, json, os, re, shlex, sys
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks'))
from _file_lock import locked  # noqa: E402

IN_W, OUT_W, CW_W, CR_W = 1.0, 5.0, 1.25, 0.1


def _number(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _optional_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def agent_cost(u):
    """Base-input-token equivalents for one agent's usage dict.

    Fresh `input_tokens` bills at 1.0 and was previously omitted entirely. Under
    caching it is small, but it is exactly the UNCACHED portion -- so omitting it
    understated the first turn of every agent and any cache miss after it.
    """
    values = [u.get('inp'), u.get('out'), u.get('cw'), u.get('cr')]
    if any(_optional_number(value) is None for value in values):
        return None
    return (values[0] * IN_W + values[1] * OUT_W
            + values[2] * CW_W + values[3] * CR_W)

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
    """Quota-equivalent cost, or None when cost/model carries no weight."""
    weight = quota_weight(model)
    return None if weight is None or _optional_number(cost) is None else cost * weight
# Anchored to the project, not the cwd: budget_posture.py imports this per prompt from
# whatever cwd the hook runs in, and a cwd-relative path would read every verdict as absent.
_PROJECT_DIR = os.environ.get('CLAUDE_PROJECT_DIR') or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(_PROJECT_DIR, '.claude', 'orchestration_metrics.jsonl')
ARCHIVE_MAX_BYTES = 5 * 1024 * 1024
ARCHIVE_MAX_ROTATIONS = 6
ARCHIVE_LOCK_TIMEOUT_SECONDS = 10.0
# Falsification outcomes, recorded at consumption (see module docstring).
OUTCOMES = ('clean', 'defects', 'rework', 'discarded')
LEGACY_VERDICTS = {'right-sized': 'clean', 'overshoot': 'clean',
                   'undershoot': 'rework', 'wasted': 'discarded',
                   # fit-vocabulary used by consumption-time rich verdicts
                   'fit': 'clean', 'excellent': 'clean',
                   'fit-high-value': 'clean', 'fit-highest-value': 'clean',
                   'fit-with-one-correction': 'defects', 'misfit-input': 'discarded'}
# SIDECAR_LEDGER_PATH is the one override shared by the sidecar writer and metrics reader.
# Provider capacity is account-wide and deliberately uses its own normalized cache.
SIDECAR_LEDGER = (os.environ.get('SIDECAR_LEDGER_PATH')
                   or os.path.expanduser('~/.claude/sidecar_ledger.jsonl'))
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


def _input_diagnostic(diagnostics, reader, path, status, detail=''):
    if diagnostics is None:
        return
    row = {
        'reader': reader,
        'path': os.path.abspath(os.path.expanduser(str(path))),
        'status': status,
    }
    if detail:
        row['detail'] = str(detail)
    diagnostics.append(row)


def _read_json_object(path, diagnostics=None, reader='json-object'):
    try:
        with open(path, encoding='utf-8') as fh:
            value = json.load(fh)
    except FileNotFoundError as exc:
        _input_diagnostic(diagnostics, reader, path, 'missing', exc)
        return None
    except json.JSONDecodeError as exc:
        _input_diagnostic(diagnostics, reader, path, 'malformed', exc)
        return None
    except (OSError, UnicodeError) as exc:
        _input_diagnostic(diagnostics, reader, path, 'unreadable', exc)
        return None
    if not isinstance(value, dict):
        _input_diagnostic(diagnostics, reader, path, 'non-object',
                          'expected a JSON object')
        return None
    return value


def _iter_jsonl_objects(path, diagnostics=None, reader='jsonl'):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, TypeError) as exc:
                    _input_diagnostic(diagnostics, reader, path, 'malformed',
                                      f'line {line_number}: {exc}')
                    continue
                if not isinstance(value, dict):
                    _input_diagnostic(diagnostics, reader, path, 'non-object',
                                      f'line {line_number}: expected a JSON object')
                    continue
                yield value
    except FileNotFoundError as exc:
        _input_diagnostic(diagnostics, reader, path, 'missing', exc)
    except (OSError, UnicodeError) as exc:
        _input_diagnostic(diagnostics, reader, path, 'unreadable', exc)


def _atomic_write_json(path, value, indent=2):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(value, fh, indent=indent, ensure_ascii=False)
            fh.write('\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _archive_paths():
    rotated = [ARCHIVE + '.%d' % index for index in range(ARCHIVE_MAX_ROTATIONS, 0, -1)]
    return [path for path in rotated + [ARCHIVE] if os.path.isfile(path)]


def _archive_records(diagnostics=None):
    for path in _archive_paths():
        yield from _iter_jsonl_objects(path, diagnostics, 'archive')


@contextmanager
def _archive_lock():
    with locked(ARCHIVE + '.lock', ARCHIVE_LOCK_TIMEOUT_SECONDS):
        yield


def _rotate_archive():
    if ARCHIVE_MAX_ROTATIONS <= 0:
        try:
            os.unlink(ARCHIVE)
        except OSError:
            pass
        return
    oldest = ARCHIVE + '.%d' % ARCHIVE_MAX_ROTATIONS
    try:
        os.unlink(oldest)
    except OSError:
        pass
    for index in range(ARCHIVE_MAX_ROTATIONS - 1, 0, -1):
        source = ARCHIVE + '.%d' % index
        if os.path.exists(source):
            os.replace(source, ARCHIVE + '.%d' % (index + 1))
    if os.path.exists(ARCHIVE):
        os.replace(ARCHIVE, ARCHIVE + '.1')


def _atomic_replace_bytes(path, content):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, 'wb') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _archive_rows_atomic(rows):
    """Idempotently append each (run, label), preserving partial-run recovery."""
    rows = [dict(row) for row in rows if isinstance(row, dict) and row.get('run')]
    with _archive_lock():
        rated_pairs, rated_runs, ignored_runs = set(), set(), set()
        for record in _archive_records():
            run = record.get('run')
            if not run:
                continue
            if record.get('ignored'):
                ignored_runs.add(run)
            else:
                rated_runs.add(run)
                rated_pairs.add((run, record.get('label')))
        incoming_rated = {row['run'] for row in rows if not row.get('ignored')}
        incoming_ignored = {row['run'] for row in rows if row.get('ignored')}
        conflicts = sorted(incoming_ignored & (rated_runs | incoming_rated))
        fresh, seen = [], set()
        for row in rows:
            run = row['run']
            if run in conflicts:
                continue
            if row.get('ignored'):
                key = ('ignored', run)
                if run in ignored_runs or key in seen:
                    continue
            else:
                key = ('rated', run, row.get('label'))
                if run in ignored_runs or (run, row.get('label')) in rated_pairs or key in seen:
                    continue
            seen.add(key)
            fresh.append(row)
        skipped = len(rows) - len(fresh)
        if fresh:
            encoded = [(row, (json.dumps(row) + '\n').encode('utf-8')) for row in fresh]
            oversized = [row['run'] for row, payload in encoded if len(payload) > ARCHIVE_MAX_BYTES]
            if oversized:
                raise ValueError('row exceeds archive byte cap: ' + ', '.join(oversized))
            try:
                with open(ARCHIVE, 'rb') as fh:
                    current = fh.read()
            except OSError:
                current = b''
            for _, payload in encoded:
                separator = b'' if not current or current.endswith(b'\n') else b'\n'
                candidate = current + separator + payload
                if current and len(candidate) > ARCHIVE_MAX_BYTES:
                    _rotate_archive()
                    candidate = payload
                _atomic_replace_bytes(ARCHIVE, candidate)
                current = candidate
        return fresh, skipped, conflicts


def _read_verdict_file(path):
    value = _read_json_object(path) if path else None
    return {k: v for k, v in (value or {}).items() if v}


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


@contextmanager
def _pending_lock():
    with locked(PENDING_VERDICTS + '.lock', ARCHIVE_LOCK_TIMEOUT_SECONDS):
        yield


def _prune_pending_verdicts(snapshot, consumed_keys, legacy_snapshot=None,
                            legacy_consumed_keys=()):
    """Remove unchanged consumed values from both verdict sources under one lock."""
    with _pending_lock():
        current_root = _read_verdict_file(PENDING_VERDICTS)
        current_legacy = (_read_verdict_file(LEGACY_PENDING_VERDICTS)
                          if LEGACY_PENDING_VERDICTS != PENDING_VERDICTS else {})
        remaining_root = dict(current_root)
        remaining_legacy = dict(current_legacy)
        for key in consumed_keys:
            if key in snapshot and current_root.get(key) == snapshot[key]:
                remaining_root.pop(key, None)
        legacy_snapshot = legacy_snapshot or {}
        for key in legacy_consumed_keys:
            if key in legacy_snapshot and current_legacy.get(key) == legacy_snapshot[key]:
                remaining_legacy.pop(key, None)
        if remaining_root != current_root:
            _atomic_write_json(PENDING_VERDICTS, remaining_root)
        if remaining_legacy != current_legacy:
            _atomic_write_json(LEGACY_PENDING_VERDICTS, remaining_legacy)
        return (len(current_root) - len(remaining_root)
                + len(current_legacy) - len(remaining_legacy))


def w(s):
    sys.stdout.write(str(s).encode('ascii', 'replace').decode('ascii') + '\n')


def _field_stats(rows, key):
    values = [_optional_number(row.get(key)) for row in rows]
    known = [value for value in values if value is not None]
    return sum(known), len(known), len(values) - len(known)


def _total_text(rows, key):
    total, _, unknown = _field_stats(rows, key)
    return fmt(total) + (f' ({unknown} unknown)' if unknown else '')


def fmt(n):
    if _optional_number(n) is None:
        return 'n/a'
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


def apply_verdict(r, ent):
    """Write one verdict entry onto its row: `outcome`, `[outcome, effort(, "probe")]`, or a dict.
    A panel seat's dict also carries `tier` and `mandates` ({key: {outcome, unique, duplicate}}),
    which stay on the archived row for the panel-yield line."""
    if isinstance(ent, (list, tuple)):
        r['outcome'] = norm_outcome(ent[0])
        if len(ent) > 1 and ent[1]:
            r['requested_effort'] = ent[1]
            r['effort'] = ent[1]
        if len(ent) > 2 and ent[2] == 'probe':
            r['probe'] = True
    elif ent:
        r['outcome'] = norm_outcome(ent)
        if isinstance(ent, dict):
            if ent.get('effort'):
                r['requested_effort'] = ent['effort']
                r['effort'] = ent['effort']
            for field in ('tier', 'mandates'):
                if ent.get(field):
                    r[field] = ent[field]
    else:
        r['outcome'] = 'unrated'


PANEL_YIELD_WINDOW = 5


def panel_yield(rows, window=PANEL_YIELD_WINDOW):
    """(mandates with zero unique findings across their last `window` reached merged-seat rows,
    [(mandate, run:label) for every not-reached mandate]) in row order. A merged seat carries 2+
    mandates; a not-reached mandate is UNCOVERED and never counts as a zero."""
    reached, uncovered = {}, []
    for r in rows:
        mandates = r.get('mandates')
        if not isinstance(mandates, dict) or len(mandates) < 2:
            continue
        for key, m in mandates.items():
            m = m if isinstance(m, dict) else {}
            if m.get('outcome') == 'not-reached':
                uncovered.append((key, '%s:%s' % (r.get('run', '?'), r.get('label', '?'))))
            else:
                reached.setdefault(key, []).append(_number(m.get('unique')))
    zero = sorted(k for k, seen in reached.items()
                  if len(seen) >= window and not any(seen[-window:]))
    return zero, uncovered


def report_panel_yield(rows):
    """The re-open trigger for a command's seat map (orchestration §2 *Sizing the width*)."""
    zero, uncovered = panel_yield(rows)
    for key, where in uncovered[:10]:
        w(f'UNCOVERED {key} in {where}: its merged seat did not reach it')
    if len(uncovered) > 10:
        w(f'  ... {len(uncovered) - 10} more UNCOVERED mandate(s)')
    if zero:
        w(f'Panel yield: zero unique findings in the last {PANEL_YIELD_WINDOW} merged-seat rows for '
          + ', '.join(zero) + ' -- re-open the seat map that merges it.')


def report_run(session, run_id):
    """Per-seat usage for one Workflow run; the consolidator (phase Merge) on its own row."""
    rows = run_rows(session, run_id)
    if not rows:
        w(f'No Workflow run {run_id} in {session}.')
        return 1
    def is_merge(r):
        return r.get('phase') == 'Merge' or str(r.get('label', '')).endswith(':consolidate')
    seats = [r for r in rows if not is_merge(r)]
    merge = [r for r in rows if is_merge(r)]
    w(f"{'seat':<52} {'req-eff':<7} {'cacheW':>8} {'cacheR':>8} {'out':>7} {'input-eq':>9}")
    for r in seats:
        w(f"{r['label'][:52]:<52} {r['effort']:<7} {fmt(r.get('cw')):>8} {fmt(r.get('cr')):>8} "
          f"{fmt(r.get('out')):>7} {fmt(r.get('cost')):>9}")
    total, known, unknown = _field_stats(seats, 'cost')
    w(f"seats: {len(seats)} | input-equivalent {fmt(total)} over {known} known"
      + (f' ({unknown} unknown)' if unknown else ''))
    for r in merge:
        w(f"{r['label'][:40]:<40} consolidator {fmt(r.get('cw')):>8} {fmt(r.get('cr')):>8} "
          f"{fmt(r.get('out')):>7} {fmt(r.get('cost')):>9}")
    return 0


def outcome_of(r):
    """Archive outcome field, with legacy fallbacks: 'verdict' (pre-2026-08)
    and 'fit' (earliest schema) both carry verdict words."""
    v = r.get('outcome') or r.get('verdict') or r.get('fit') or '?'
    return norm_outcome(v)


def _candidate_project_dirs(root=None, strict=False):
    """Project dirs under ~/.claude/projects whose slug matches this cwd -- the same
    match find_session_dir falls back to when no session id narrows it further.
    strict=False (find_session_dir's use): every project on the machine when nothing
    matches -- a wrong-but-recent single-session guess is recoverable.
    strict=True (collect_all_workflows's sweep): no match returns [] instead -- an
    all-time report silently pooling every unrelated project is a correctness defect,
    not a recoverable guess (sa-architecture-sweep F1/F2)."""
    root = root or os.path.expanduser('~/.claude/projects')
    if not os.path.isdir(root):
        return []
    cwd = os.path.abspath(os.getcwd())
    # Claude Code's project-dir slug maps '_' to '-' too (observed: this project's own
    # cwd contains '_' and matched zero dirs without this, silently falling back to
    # EVERY project on the machine -- sa-architecture-sweep F1, reproduced 2026-09-18).
    slug = cwd.replace(':', '-').replace(os.sep, '-').replace('/', '-').replace('_', '-')
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    cands = [os.path.join(root, d) for d in entries
             if d.lower().lstrip('-') in slug.lower().lstrip('-')
             or slug.lower().endswith(d.lower())]
    if not cands and not strict:
        cands = [os.path.join(root, d) for d in entries]
    return [c for c in cands if os.path.isdir(c)]


def find_session_dir(session_id=None):
    root = os.path.expanduser('~/.claude/projects')

    # Exact identity first. The mtime scan below cannot tell this session's runs from a peer's:
    # concurrent sessions share one machine, and whichever touched its workflows/ dir last wins the
    # max(). That misattributes a whole session's cost to another and is silent -- the table renders
    # normally, just with someone else's agent labels. Measured 2026-08-18: a peer session sharing
    # the same minute won the tie and 84 foreign agents were reported as this session's.
    # An explicit id is authoritative: when it names no workflows dir, the session has dispatched
    # nothing, and the scan must not answer for it with a peer's.
    sid = session_id or os.environ.get('CLAUDE_CODE_SESSION_ID')
    if sid:
        if session_id:
            explicit_path = os.path.abspath(os.path.expanduser(session_id))
            if os.path.isdir(explicit_path):
                return explicit_path
        matches = []
        for project_name in (os.listdir(root) if os.path.isdir(root) else []):
            project = os.path.join(root, project_name)
            if not os.path.isdir(project):
                continue
            session_names = set()
            for name in os.listdir(project):
                session_names.add(name[:-6] if name.endswith('.jsonl') else name)
            for name in session_names:
                path = os.path.join(project, name)
                if not (os.path.isfile(path + '.jsonl')
                        or os.path.isdir(os.path.join(path, 'workflows'))):
                    continue
                if name == sid:
                    return path
                if name.startswith(sid):
                    matches.append(path)
        return matches[0] if len(matches) == 1 else None

    cands = _candidate_project_dirs(root)
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
    try:
        names = sorted(os.listdir(wdir))
    except OSError:
        return [], set(), set()
    for fn in names:
        if not fn.endswith('.json'):
            continue
        run = _read_json_object(os.path.join(wdir, fn))
        if not run:
            continue
        if run.get('workflowName') in MEASUREMENT_WORKFLOWS:
            continue
        rid = run.get('runId') or fn[:-5]
        run_ids.add(rid)
        for e in run.get('workflowProgress') or []:
            if not isinstance(e, dict) or e.get('type') != 'workflow_agent':
                continue
            candidates.append((rid, e.get('label') or '(unlabeled)'))
    archived_pairs, ignored_runs = set(), set()
    if run_ids:
        for rec in _archive_records():
            run = rec.get('run')
            if run not in run_ids:
                continue
            if rec.get('ignored'):
                ignored_runs.add(run)
            elif rec.get('label'):
                archived_pairs.add((run, rec['label']))
    unarchived = [pair for pair in candidates
                  if pair[0] not in ignored_runs and pair not in archived_pairs]
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


def misshaped_verdict_keys(session_dir, sidecar_labels=()):
    """[(key, spelling)] for verdict keys written `label@suffix` against this session's work: a
    Workflow run id (spelled `run_id:label`) or a sidecar label (spelled as the bare label). That
    shape matches nothing, so the dispatch stays unrated while its author believes it is rated."""
    _, run_ids, session_labels = _session_candidates(session_dir)
    found = []
    for key in load_pending_verdicts():
        if key in session_labels:
            continue
        label, sep, suffix = key.rpartition('@')
        if not (sep and label):
            continue
        if suffix in run_ids:
            found.append((key, f'{suffix}:{label}'))
        elif label in sidecar_labels:
            found.append((key, label))
    return sorted(found)


def pending_labels(session_dir):
    """[(run_id, label)] this session's workflow_agent dispatches not yet archived or resolved
    by a pending verdict. See pending_report() for the matching rule."""
    return pending_report(session_dir)[0]


def pending_count(session_dir=None, session_id=None):
    """Unrated-dispatch count for this session, or -1 when unknown. Never raises -- called from
    a per-turn hook, where an exception must cost a routing hint, never the turn."""
    try:
        session_dir = session_dir or find_session_dir(session_id)
        if not session_dir or not os.path.isdir(os.path.join(session_dir, 'workflows')):
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
    """label -> effort, merged over every PINS log line (an engine may log one per phase), else a
    static opts-literal scan."""
    pins = {}
    for line in run.get('logs') or []:
        s = str(line).strip()
        if s.startswith('PINS '):
            try:
                raw = json.loads(s[5:])
                pins.update({k: _norm_effort(v) for k, v in raw.items()})
            except Exception:
                pass
    if pins:
        return pins
    out = {}
    for blk in re.findall(r'\{[^{}]*\}', run.get('script') or ''):
        lab = re.search(r"label:\s*['\"]([^'\"]+)['\"]", blk)
        eff = re.search(r"effort:\s*['\"](\w+)['\"]", blk)
        if lab and eff:
            out[lab.group(1)] = eff.group(1)
    return out


def _ensure_effort_evidence(row):
    """Keep the legacy effort alias while naming what was and was not observed."""
    requested = row.get('requested_effort', row.get('effort', '?')) or '?'
    row['requested_effort'] = requested
    row['effort'] = requested
    row.setdefault('observed_effort', None)
    return row


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
    try:
        names = os.listdir(run_dir)
    except OSError:
        return usage
    for fn in names:
        if not (fn.startswith('agent-') and fn.endswith('.jsonl')):
            continue
        aid = fn[len('agent-'):-len('.jsonl')]
        calls, order, recs = {}, [], 0
        served, efforts = set(), set()
        first = last = None
        for o in _iter_jsonl_objects(os.path.join(run_dir, fn)):
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
            if isinstance(o.get('effort'), str) and o['effort']:
                efforts.add(o['effort'])
            m = o.get('message')
            if not isinstance(m, dict):
                continue
            if isinstance(m.get('model'), str) and m['model']:
                served.add(m['model'])
            # No message.id -> fall back to the record's own uuid.
            mid = m.get('id') or o.get('uuid') or ('rec-%d' % recs)
            if mid not in calls:
                order.append(mid)
            calls[mid] = m                      # later record wins
        initial_usage = 0 if calls else None
        u = dict(inp=initial_usage, out=initial_usage, cw=initial_usage, cr=initial_usage,
                 turns=len(calls), tools=0, recs=recs, secs=None)
        usage_fields = {
            'inp': 'input_tokens', 'out': 'output_tokens',
            'cw': 'cache_creation_input_tokens', 'cr': 'cache_read_input_tokens',
        }
        for mid in order:
            m = calls[mid]
            us = m.get('usage') if isinstance(m.get('usage'), dict) else {}
            for target, source in usage_fields.items():
                value = _optional_number(us.get(source))
                u[target] = None if u[target] is None or value is None else u[target] + value
            for b in (m.get('content') or []):
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    u['tools'] += 1
        if first and last:
            u['secs'] = (last - first).total_seconds()
        u['first_ts'] = first.isoformat() if first else None
        # The model the transcript reports as served; the requested pin lives on the workflow record.
        # Native-transport rows only: a proxied sidecar child echoes its own pin, so its self-report
        # is not authority (gotcha_self_reported_model_identity_is_not_authority).
        u['served_model'] = _one_or_mixed(served)
        # The effort the client SENT on each turn (top-level `effort` on assistant records): recorded,
        # not server-attested. Absent when the model takes no effort parameter.
        u['served_effort'] = _one_or_mixed(efforts)
        usage[aid] = u
    return usage


def _one_or_mixed(values):
    """None for no values, the value for one, 'mixed:a,b' for several."""
    return None if not values else sorted(values)[0] if len(values) == 1 else 'mixed:' + ','.join(sorted(values))


def transcript_identity(path):
    """(served model, recorded effort) over one Claude Code transcript's assistant records — the
    same fields `agent_usage` reads, for a transcript outside a Workflow run dir (a sidecar child)."""
    models, efforts = set(), set()
    for o in _iter_jsonl_objects(path):
        if o.get('type') != 'assistant':
            continue
        if isinstance(o.get('effort'), str) and o['effort']:
            efforts.add(o['effort'])
        m = o.get('message')
        if isinstance(m, dict) and isinstance(m.get('model'), str) and m['model']:
            models.add(m['model'])
    return _one_or_mixed(models), _one_or_mixed(efforts)


def _task_claims():
    """{(source, key): task_id} from every task record's active_jobs (tools/task_record.py): a Workflow
    row is claimed by run id, a sidecar row by label. The record is the join key's owner; a row no
    record claims reads task_id None, so a partial join is visible rather than silently pooled."""
    claims = {}
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import task_record
        directory = task_record.record_dir()
        for name in os.listdir(directory):
            if not name.endswith('.json'):
                continue
            rec = task_record.load(name[:-5]) or {}
            for job in rec.get('active_jobs') or []:
                if job.get('source') == 'workflow' and job.get('run_id'):
                    claims[('workflow', str(job['run_id']))] = rec.get('task_id')
                elif job.get('source') == 'sidecar' and job.get('label'):
                    claims[('sidecar', str(job['label']))] = rec.get('task_id')
    except Exception:
        return claims
    return claims


def task_totals(rows):
    """Per-task cost totals with explicit denominators: rows the join reached, rows no record claims,
    rows with no cost of either kind. Quota cost and USD stay separate columns, never summed together."""
    out = {'tasks': {}, 'rows_without_task': 0, 'rows_without_cost': 0, 'rows': len(rows)}
    for r in rows:
        tid = r.get('task_id')
        has_cost = _optional_number(r.get('cost')) is not None or _optional_number(r.get('cost_usd')) is not None
        if not has_cost:
            out['rows_without_cost'] += 1
        if not tid:
            out['rows_without_task'] += 1
            continue
        t = out['tasks'].setdefault(tid, {'rows': 0, 'qcost': 0.0, 'cost_usd': 0.0, 'rows_without_cost': 0})
        t['rows'] += 1
        if _optional_number(r.get('qcost')) is not None:
            t['qcost'] += float(r['qcost'])
        if _optional_number(r.get('cost_usd')) is not None:
            t['cost_usd'] += float(r['cost_usd'])
        if not has_cost:
            t['rows_without_cost'] += 1
    return out


MODEL_FAMILIES = ('opus', 'sonnet', 'haiku', 'fable')


def model_mismatches(rows):
    """One line per Workflow row whose served model does not honor its requested pin, then one per
    row whose recorded effort differs from a known requested effort.

    A pin naming a family (`sonnet`) is honored by any served id carrying that token
    (`claude-sonnet-5`); a full-id pin must match exactly, after dropping a client
    context-window suffix (`claude-opus-5[1m]`), which the served id never carries.
    Rows with no served model, and sidecar rows, never mismatch: there is nothing
    attested to compare.
    """
    lines = []
    for r in rows:
        served, pin = r.get('served_model'), str(r.get('model') or '')
        pin = re.sub(r'\[[^\]]*\]$', '', pin)
        if r.get('source') == 'sidecar' or not served or not pin or pin == '?':
            continue
        ids = served[len('mixed:'):].split(',') if served.startswith('mixed:') else [served]
        honored = all((pin in sid.split('-')) if pin in MODEL_FAMILIES else (pin == sid) for sid in ids)
        if not honored:
            lines.append('MODEL MISMATCH %s:%s requested %s served %s'
                         % (r.get('run', '?'), r.get('label', '?'), pin, served))
    for r in rows:
        requested, recorded = r.get('requested_effort'), r.get('served_effort')
        if r.get('source') == 'sidecar' or not recorded or requested in (None, '', '?'):
            continue
        if recorded != requested:
            lines.append('EFFORT MISMATCH %s:%s requested %s recorded %s'
                         % (r.get('run', '?'), r.get('label', '?'), requested, recorded))
    return lines


def collect(session, diagnostics=None):
    rows = []
    wdir = os.path.join(session, 'workflows')
    if not os.path.isdir(wdir):
        _input_diagnostic(diagnostics, 'workflow-directory', wdir, 'missing')
        return rows
    try:
        names = sorted(os.listdir(wdir))
    except OSError as exc:
        _input_diagnostic(diagnostics, 'workflow-directory', wdir, 'unreadable', exc)
        return rows
    claims = _task_claims()
    for fn in names:
        if not fn.endswith('.json'):
            continue
        rows.extend(run_rows(session, fn[:-5], diagnostics, claims))
    return rows


def run_rows(session, run_id, diagnostics=None, claims=None):
    """One row per agent of one Workflow run: requested pin, served model, recorded effort, usage.
    `run_id` is the journal's file stem under `<session>/workflows/`."""
    run = _read_json_object(os.path.join(session, 'workflows', run_id + '.json'), diagnostics, 'workflow')
    if not run:
        return []
    claims = _task_claims() if claims is None else claims
    rows = []
    rid = run.get('runId') or run_id
    eff = efforts_for(run)
    usage = agent_usage(os.path.join(session, 'subagents', 'workflows', rid))
    for e in run.get('workflowProgress') or []:
        if not isinstance(e, dict) or e.get('type') != 'workflow_agent':
            continue
        aid, lab = e.get('agentId'), e.get('label') or '(unlabeled)'
        u = usage.get(aid, dict(inp=None, out=None, cw=None, cr=None, turns=None,
                                tools=None, recs=None, secs=None, first_ts=None,
                                served_model=None, served_effort=None))
        cost = agent_cost(u)
        # Run date, most specific source first: this agent's own start, the
        # workflow record's instant, then the transcript's earliest turn.
        run_date = (run_date_of(e.get('startedAt')) or run_date_of(e.get('queuedAt'))
                    or run_date_of(run.get('timestamp')) or run_date_of(run.get('startTime'))
                    or run_date_of(u.pop('first_ts', None)))
        u.pop('first_ts', None)
        requested_effort = eff.get(lab, '?')
        rows.append(dict(
            run=rid, workflow=run.get('workflowName') or '?', phase=e.get('phaseTitle') or '',
            agent_id=aid, label=lab, model=e.get('model') or '?',
            effort=requested_effort, requested_effort=requested_effort,
            observed_effort=u.get('served_effort'), state=e.get('state') or '?',
            run_date=run_date, task_id=claims.get(('workflow', rid)),
            cost=cost, qcost=quota_cost(cost, e.get('model') or ''), **u))
    return rows


def collect_all_workflows(diagnostics=None):
    """Workflow rows across every session in this project, not just one -- the Workflow
    counterpart to collect_sidecar(include_unlabeled=True): both give an unconditional
    all-time view instead of the one-session default. Project-scoped, unlike the sidecar
    ledger: that file is one global path across every project on the machine, but Claude
    Code's per-session storage has no cross-project index safe to sweep from here."""
    rows = []
    projects = _candidate_project_dirs(strict=True)
    if not projects:
        _input_diagnostic(diagnostics, 'project-directory', os.getcwd(), 'no-match')
    for project in projects:
        try:
            names = sorted(os.listdir(project))
        except OSError as exc:
            _input_diagnostic(diagnostics, 'project-directory', project, 'unreadable', exc)
            continue
        for name in names:
            session = os.path.join(project, name)
            if os.path.isdir(os.path.join(session, 'workflows')):
                rows.extend(collect(session, diagnostics))
    return rows


def _state_from_exit_code(exit_code):
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return 'unknown'
    return 'completed' if exit_code == 0 else 'exit-' + str(exit_code)


def collect_sidecar(ledger=SIDECAR_LEDGER, include_unlabeled=False, diagnostics=None):
    """Sidecar spend-ledger rows shaped for the archive. Marked source='sidecar';
    cost_usd is real dollars; normalized `cost` stays unknown so sidecar rows can
    never leak into normalized-token totals. Run id is synthetic and stable across invocations.

    Unlabeled rows (no -l at dispatch: legacy history, benchmark arms, ad-hoc
    probes) are skipped by default -- they cannot be attributed or rated per
    label. --sidecar-all surfaces them for spend audits."""
    rows = []
    claims = _task_claims()
    for rec in _iter_jsonl_objects(ledger, diagnostics, 'sidecar-ledger'):
        if not rec.get('label') and not include_unlabeled:
            continue
        lab = str(rec.get('label') or '(unlabeled)')
        ts = rec.get('timestamp') or '?'
        duration = _optional_number(rec.get('durationMs'))
        requested_effort = rec.get('effort') or '?'
        rows.append(dict(
            run=f'sidecar-{ts}-{lab}', run_date=run_date_of(ts),
            workflow='sidecar', phase='', agent_id='',
            label=lab, model=str(rec.get('servedModel') or '?'),
            effort=requested_effort, requested_effort=requested_effort,
            observed_effort=None,
            state=_state_from_exit_code(rec.get('exitCode')),
            source='sidecar', cost=None, cost_usd=rec.get('costUSD'),
            cost_basis=rec.get('costBasis') or '', task_id=claims.get(('sidecar', lab)),
            inp=_optional_number(rec.get('inputTokens')),
            out=_optional_number(rec.get('outputTokens')),
            cw=_optional_number(rec.get('cacheWriteTokens')),
            cr=_optional_number(rec.get('cacheReadTokens')), recs=None,
            turns=_optional_number(rec.get('numTurns')),
            tools=_optional_number(rec.get('toolCalls')),
            secs=duration / 1000.0 if duration is not None else None))
    return rows


class ManifestError(ValueError):
    """A seed cannot be joined to exactly one evidence row per job."""


def _sidecar_record_row(path, record=None, diagnostics=None):
    """One explicit -R record shaped for manifest and archive consumers."""
    rec = (record if isinstance(record, dict) else
           _read_json_object(path, diagnostics, 'sidecar-record'))
    if not rec:
        return None
    label = rec.get('label')
    if not label:
        _input_diagnostic(diagnostics, 'sidecar-record', path, 'invalid',
                          'record has no label')
        return None
    label = str(label)
    exit_code = rec.get('exitCode')
    timestamp = rec.get('timestamp') or '?'
    launch_id = rec.get('launchId')
    duration = _optional_number(rec.get('durationMs'))
    requested_effort = rec.get('effort') or '?'
    return dict(
        source='sidecar', label=label, task_id=_task_claims().get(('sidecar', label)),
        run=('sidecar-' + str(launch_id) if launch_id
             else 'sidecar-' + str(timestamp) + '-' + label),
        timestamp=timestamp,
        run_date=run_date_of(timestamp), workflow='sidecar', phase='', agent_id='',
        model=str(rec.get('servedModel') or '?'),
        effort=requested_effort, requested_effort=requested_effort,
        observed_effort=None,
        state=_state_from_exit_code(exit_code),
        transport=rec.get('transport'), currency=rec.get('costModel'),
        record_path=os.path.abspath(path), cost=None,
        cost_usd=rec.get('costUSD'), cost_basis=rec.get('costBasis') or '',
        inp=_optional_number(rec.get('inputTokens')),
        out=_optional_number(rec.get('outputTokens')),
        cw=_optional_number(rec.get('cacheWriteTokens')),
        cr=_optional_number(rec.get('cacheReadTokens')), recs=None,
        turns=_optional_number(rec.get('numTurns')),
        tools=_optional_number(rec.get('toolCalls')),
        secs=duration / 1000.0 if duration is not None else None)


def collect_sidecar_records(record_dir, diagnostics=None):
    """Read per-job `*.record.json` files without touching the spend ledger."""
    rows = []
    if not record_dir:
        return rows
    if not os.path.isdir(record_dir):
        _input_diagnostic(diagnostics, 'sidecar-record-directory', record_dir, 'missing')
        return rows
    for base, _, names in os.walk(record_dir):
        for name in sorted(names):
            if not name.endswith('.record.json'):
                continue
            path = os.path.join(base, name)
            row = _sidecar_record_row(path, diagnostics=diagnostics)
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


_FD_REDIRECTION = re.compile(r'(?<!\S)(?:\d*>\s*&\s*(?:\d+|-)|&>>?(?:[^\s;&|]+|\s+[^\s;&|]+))')


def _shell_segments(command):
    command = _FD_REDIRECTION.sub(' ', command)
    try:
        lexer = shlex.shlex(command.replace('\n', ' ; '), posix=True,
                            punctuation_chars=';&|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return []
    segments, current = [], []
    for token in tokens:
        if token and all(char in ';&|' for char in token):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


_REDIRECTION = re.compile(r'^\d*(?:>>?|<)(.*)$')


def _strip_redirections(tokens):
    """Tokens without shell redirections such as `> log` and `2>/dev/null`. A quoted argument
    beginning with `>` is indistinguishable from a redirect after shlex removes its quotes."""
    out, skip_target = [], False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        match = _REDIRECTION.match(token)
        if match:
            skip_target = not match.group(1)
            continue
        out.append(token)
    return out


def _launcher_calls(command):
    """Actual top-level launcher calls as (kind, launcher arguments)."""
    return [(kind, args) for kind, _, args in _launcher_calls_named(command)]


# lib/sidecar_common.sh SC_OPTSTRING letters that take a value (the `:`-suffixed ones).
_SIDECAR_VALUE_FLAGS = set("metnodfTRxPSrpLlaGDCZ")


def _sidecar_flags(args):
    """{flag letter: value} for a launcher's argv, read the way getopts reads it: clustered letters,
    a value glued to its flag or in the next token, and parsing stops at `--` or the first operand."""
    flags, index = {}, 0
    while index < len(args):
        token = args[index]
        if token == '--' or not token.startswith('-') or token == '-':
            break
        for pos, letter in enumerate(token[1:], 1):
            if letter in _SIDECAR_VALUE_FLAGS:
                rest = token[pos + 1:]
                if rest:
                    flags[letter] = rest
                elif index + 1 < len(args):
                    index += 1
                    flags[letter] = args[index]
                break
            flags[letter] = True
        index += 1
    return flags


def sidecar_launches(command, cwd=None):
    """[{kind, launcher, label, model, effort, record}] per sidecar job this shell command EXECUTES:
    a registered `.claude/scripts/*_sidecar.sh` launcher, or each job of a `sidecar_fanout.py` jobs
    file. [] for a command that only mentions one. Omitted values are None; the reader labels them."""
    cwd = cwd or _PROJECT_DIR
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
    out = []
    for kind, script, args in _launcher_calls_named(command):
        if kind == 'sidecar':
            if not os.path.isfile(os.path.join(scripts, script)):
                continue
            f = _sidecar_flags(args)
            record = f.get('R')
            out.append(dict(kind='sidecar', launcher=script[:-len('_sidecar.sh')],
                            label=f.get('l') if isinstance(f.get('l'), str) else None,
                            model=f.get('m') if isinstance(f.get('m'), str) else None,
                            effort=f.get('e') if isinstance(f.get('e'), str) else None,
                            record=_resolve_session_path(record, cwd) if isinstance(record, str) else None))
            continue
        parsed = _parse_fanout_args(args, cwd)
        if not parsed:
            continue
        jobs_path, out_dir = parsed
        try:
            with open(jobs_path, encoding='utf-8') as fh:
                jobs = json.load(fh)
        except (OSError, ValueError):
            jobs = None
        if not isinstance(jobs, list):
            out.append(dict(kind='fanout', launcher='sidecar_fanout', label=os.path.basename(jobs_path),
                            model='unresolved', effort='unresolved', record=None))
            continue
        for job in jobs:
            job = job if isinstance(job, dict) else {}
            label = job.get('label')
            out.append(dict(kind='fanout', launcher=str(job.get('transport') or 'sidecar_fanout'),
                            label=label, model=job.get('alias'), effort=job.get('effort'),
                            record=os.path.join(out_dir, '%s.record.json' % label) if label else None))
    return out


def _launcher_calls_named(command):
    """Actual top-level launcher calls as (kind, script basename, launcher arguments)."""
    calls = []
    for raw_segment in _shell_segments(command):
        segment = _strip_redirections(raw_segment)
        index = 0
        while index < len(segment) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', segment[index]):
            index += 1
        if index >= len(segment):
            continue
        executable = os.path.basename(segment[index].replace('\\', '/')).lower()
        script_index = index
        if re.fullmatch(r'python(?:3(?:\.\d+)?)?|py', executable):
            script_index += 1
            while script_index < len(segment) and segment[script_index].startswith('-'):
                if segment[script_index] in ('-c', '-m'):
                    script_index = len(segment)
                    break
                script_index += 1
        elif executable in ('bash', 'sh'):
            script_index += 1
            while script_index < len(segment) and segment[script_index].startswith('-'):
                script_index += 1
        script = (os.path.basename(segment[script_index].replace('\\', '/')).lower()
                  if script_index < len(segment) else '')
        if script == 'sidecar_fanout.py':
            calls.append(('fanout', script, segment[script_index + 1:]))
        elif script.endswith('_sidecar.sh'):
            calls.append(('sidecar', script, segment[script_index + 1:]))
    return calls


def _parse_fanout_args(args, cwd):
    jobs_value = out_value = None
    index = 0
    positional_only = False
    while index < len(args):
        token = args[index]
        if not positional_only and token == '--':
            positional_only = True
            index += 1
            continue
        if not positional_only and token in ('--authorize', '--compare', '--dry-run'):
            index += 1
            continue
        if not positional_only and token in ('--max-parallel', '--out-dir'):
            if index + 1 >= len(args):
                return None
            if token == '--out-dir':
                out_value = args[index + 1]
            index += 2
            continue
        if not positional_only and token.startswith('--out-dir='):
            out_value = token.split('=', 1)[1]
            index += 1
            continue
        if not positional_only and token.startswith('--max-parallel='):
            index += 1
            continue
        if not positional_only and token.startswith('-'):
            return None
        if jobs_value is not None:
            return None
        jobs_value = token
        index += 1
    if jobs_value is None:
        return None
    jobs_path = _resolve_session_path(jobs_value, cwd)
    out_dir = (_resolve_session_path(out_value, cwd) if out_value is not None
               else os.path.join(os.path.dirname(jobs_path), 'fanout'))
    return jobs_path, out_dir


def _fanout_jobs_paths(transcript):
    """Fan-out jobs files named by assistant shell calls in one transcript."""
    paths = set()
    for entry in _iter_jsonl_objects(transcript):
        if entry.get('type') != 'assistant':
            continue
        cwd = entry.get('cwd') or _PROJECT_DIR
        message = entry.get('message')
        if not isinstance(message, dict):
            continue
        for item in message.get('content') or []:
            if not isinstance(item, dict) or item.get('type') != 'tool_use':
                continue
            tool_name = str(item.get('name') or '').split('.')[-1]
            if tool_name not in ('Bash', 'PowerShell'):
                continue
            tool_input = item.get('input')
            command = tool_input.get('command') if isinstance(tool_input, dict) else None
            if not isinstance(command, str):
                continue
            for kind, args in _launcher_calls(command):
                if kind != 'fanout':
                    continue
                parsed = _parse_fanout_args(args, cwd)
                if parsed:
                    paths.add(parsed[0])
    return paths


def uncaptured_fanout_jobs(session):
    """Fanout jobs files this session launched that no Write in the transcript captured.
    Attribution reads only captured jobs content, so their records stay unattributed; --pending
    names them rather than let a smaller sidecar count read as all of the session's work."""
    transcript = session if str(session).endswith('.jsonl') else str(session) + '.jsonl'
    if not os.path.isfile(transcript):
        return []
    jobs_paths = _fanout_jobs_paths(transcript)
    captured = set()
    for entry in _iter_jsonl_objects(transcript):
        message = entry.get('message')
        if entry.get('type') != 'assistant' or not isinstance(message, dict):
            continue
        cwd = entry.get('cwd') or _PROJECT_DIR
        for item in message.get('content') or []:
            if (isinstance(item, dict) and item.get('type') == 'tool_use'
                    and str(item.get('name') or '').split('.')[-1] == 'Write'
                    and isinstance(item.get('input'), dict)):
                path = _resolve_session_path(item['input'].get('file_path'), cwd)
                if path in jobs_paths:
                    captured.add(path)
    return sorted(jobs_paths - captured)


def _sidecar_record_launches(session):
    """Exact -R paths proven by assistant tool calls in this session transcript."""
    transcript = session if str(session).endswith('.jsonl') else str(session) + '.jsonl'
    launches = {}
    if not os.path.isfile(transcript):
        return launches
    fanout_jobs_paths = _fanout_jobs_paths(transcript)
    file_versions = {}
    for entry in _iter_jsonl_objects(transcript):
        if entry.get('type') != 'assistant':
            continue
        message = entry.get('message')
        if not isinstance(message, dict):
            continue
        cwd = entry.get('cwd') or _PROJECT_DIR
        launched_at = _iso_instant(entry.get('timestamp'))
        for item in message.get('content') or []:
            if not isinstance(item, dict) or item.get('type') != 'tool_use':
                continue
            tool_name = str(item.get('name') or '').split('.')[-1]
            tool_input = item.get('input')
            if not isinstance(tool_input, dict):
                continue
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
                        file_versions[file_path] = prior.replace(
                            old, new, -1 if tool_input.get('replace_all') else 1)
                continue
            if tool_name not in ('Bash', 'PowerShell'):
                continue
            command = tool_input.get('command')
            if not isinstance(command, str):
                continue
            for kind, args in _launcher_calls(command):
                paths = []
                if kind == 'fanout':
                    parsed = _parse_fanout_args(args, cwd)
                    if not parsed:
                        continue
                    jobs_path, out_dir = parsed
                    content = file_versions.get(jobs_path)
                    if not isinstance(content, str):
                        continue
                    try:
                        jobs = json.loads(content)
                    except json.JSONDecodeError:
                        continue
                    for job in jobs if isinstance(jobs, list) else []:
                        label = job.get('label') if isinstance(job, dict) else None
                        if label:
                            paths.append(os.path.join(out_dir, str(label) + '.record.json'))
                else:
                    try:
                        record_index = args.index('-R')
                        paths.append(_resolve_session_path(args[record_index + 1], cwd))
                    except (ValueError, IndexError):
                        record_arg = next((arg.split('=', 1)[1] for arg in args
                                           if arg.startswith('-R=')), None)
                        if record_arg:
                            paths.append(_resolve_session_path(record_arg, cwd))
                for path in paths:
                    absolute = _resolve_session_path(path, cwd)
                    prior = launches.get(absolute)
                    if prior is None or (launched_at and (not prior or launched_at > prior)):
                        launches[absolute] = launched_at
    return launches


def collect_session_sidecar(session, diagnostics=None):
    """Sidecar rows with exact record evidence launched by this session only."""
    session_name = os.path.basename(os.path.normpath(str(session)))
    expected_parent = (session_name[:-len('.jsonl')]
                       if session_name.endswith('.jsonl') else session_name)
    rows = []
    for path, launched_at in sorted(_sidecar_record_launches(session).items()):
        record = _read_json_object(path, diagnostics, 'session-sidecar-record')
        if not record or record.get('parentSessionId') != expected_parent:
            continue
        row = _sidecar_record_row(path, record, diagnostics)
        if not row:
            continue
        recorded_at = _iso_instant(row.get('timestamp'))
        if launched_at and (
                not recorded_at
                or recorded_at.replace(microsecond=0) < launched_at.replace(microsecond=0)):
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
        # A sidecar record's `model` is its attested servedModel; a Workflow row's `model` is the
        # requested pin, so its served model comes from the transcript (None when unrecorded).
        effective_model = row.get('model') if source == 'sidecar' else row.get('served_model')
        if effective_model == '?':
            effective_model = None
        effective_transport = row.get('transport') if source == 'sidecar' else None
        effective_currency = row.get('currency') if source == 'sidecar' else None
        attestation = ('sidecar record attests served model, transport, and currency; '
                       'effort is a requested coordinate' if source == 'sidecar'
                       else 'Workflow transcript attests the served model when it records one; '
                            'the record attests state; PINS effort is requested only')
        out.append({
            'label': label,
            'task_id': row.get('task_id'),
            'requested': _manifest_requested(job),
            'effective': {
                'model': effective_model,
                'effort': None,
                'transport': effective_transport,
                'currency': effective_currency,
            },
            'status': _manifest_status(row.get('state')),
            'usage': {
                'inputTokens': _optional_number(row.get('inp')),
                'outputTokens': _optional_number(row.get('out')),
                'cacheReadTokens': _optional_number(row.get('cr')),
                'cacheWriteTokens': _optional_number(row.get('cw')),
                'turns': _optional_number(row.get('turns')),
                'toolCalls': _optional_number(row.get('tools')),
                'durationMs': (int(round(row['secs'] * 1000))
                               if _optional_number(row.get('secs')) is not None else None),
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
    seed = _read_json_object(seed_path)
    if seed is None:
        raise ManifestError('manifest seed must be a JSON object')
    diagnostics = []
    workflow_rows = collect(session, diagnostics) if session else []
    sidecar_rows = collect_sidecar_records(sidecar_record_dir, diagnostics)
    verdicts = _read_verdict_file(verdicts_path) if verdicts_path else {}
    manifest = build_manifest(seed, workflow_rows, sidecar_rows, verdicts)
    if diagnostics:
        manifest['status'] = 'partial'
        manifest['diagnostics'] = diagnostics
    _atomic_write_json(output_path, manifest)
    return manifest


def report_input_diagnostics(diagnostics):
    if not diagnostics:
        return
    w(f'Input diagnostics: {len(diagnostics)}')
    for row in diagnostics:
        detail = f" ({row['detail']})" if row.get('detail') else ''
        w(f"  {row.get('status', 'unknown')}: {row.get('reader', 'input')} "
          f"{row.get('path', '?')}{detail}")


def report(rows, diagnostics=None):
    rows = [_ensure_effort_evidence(row) for row in rows]
    side = [r for r in rows if r.get('source') == 'sidecar']
    rows = [r for r in rows if r.get('source') != 'sidecar']
    report_input_diagnostics(diagnostics)
    if not rows and not side:
        if diagnostics:
            w('No complete Workflow rows were available; input sources need recovery.')
        else:
            w('No Workflow runs found for this session -- nothing to report.')
        return
    if not rows:
        w('No Anthropic Workflow runs found for this session.')
        report_sidecar(side)
        return
    w('Effort column = requested pin; observed effort remains unknown unless a source attests it.')
    w(f"{'phase':<14} {'label':<30} {'req-eff':<7} {'cost':>8} {'out':>7} {'cacheR':>8} "
      f"{'turns':>6} {'tools':>6} {'sec':>6}")
    w('-' * 100)
    for r in sorted(rows, key=lambda x: (x['run'], -_number(x.get('cost')))):
        secs = (f"{r['secs']:>6.0f}" if _optional_number(r.get('secs')) is not None
                else f"{'n/a':>6}")
        w(f"{r['phase'][:14]:<14} {r['label'][:30]:<30} {r['effort']:<7} {fmt(r.get('cost')):>8} "
          f"{fmt(r.get('out')):>7} {fmt(r.get('cr')):>8} {fmt(r.get('turns')):>6} "
          f"{fmt(r.get('tools')):>6} {secs}")
    w('-' * 100)
    cost_total, cost_known, cost_unknown = _field_stats(rows, 'cost')
    _, out_known, _ = _field_stats(rows, 'out')
    _, turns_known, _ = _field_stats(rows, 'turns')
    w(f"{len(rows)} agents | total {fmt(cost_total)} normalized over {cost_known} known"
      + (f" ({cost_unknown} unknown)" if cost_unknown else '') + " | "
      f"out {_total_text(rows, 'out')} over {out_known} known | "
      f"turns {_total_text(rows, 'turns')} over {turns_known} known")

    by = {}
    for r in rows:
        by.setdefault(r['effort'], []).append(r)
    w('')
    w(f"{'effort':<8} {'n':>3} {'cost/agent':>11} {'turns/ag':>9} {'out/turn':>9} {'cost share':>11}")
    for k in ('low', 'medium', 'high', 'xhigh', '?'):
        g = by.get(k)
        if not g:
            continue
        n = len(g)
        group_cost, known_cost, unknown_cost = _field_stats(g, 'cost')
        group_turns, known_turns, unknown_turns = _field_stats(g, 'turns')
        paired = [row for row in g
                  if _optional_number(row.get('out')) is not None
                  and _optional_number(row.get('turns')) is not None]
        paired_out, _, _ = _field_stats(paired, 'out')
        paired_turns, _, _ = _field_stats(paired, 'turns')
        unknown_ratio = n - len(paired)
        cost_average = fmt(group_cost / known_cost) if known_cost else 'n/a'
        turns_average = f'{group_turns / known_turns:.1f}' if known_turns else 'n/a'
        out_per_turn = fmt(paired_out / paired_turns) if paired and paired_turns else 'n/a'
        share = f"{group_cost / cost_total:>10.0%}" if cost_total and known_cost else f"{'n/a':>11}"
        w(f"{k:<8} {n:>3} {cost_average:>11} {turns_average:>9} "
          f"{out_per_turn:>9} {share}")
        if unknown_cost or unknown_turns or unknown_ratio:
            w(f"  {k} unknown: cost {unknown_cost}, turns {unknown_turns}, "
              f"out/turn {unknown_ratio}")
    if '?' in by:
        w('')
        w(f"WARNING: {len(by['?'])} agent(s) have no resolved effort pin. Add a PINS log line to "
          "the script (see the module docstring) or supply the effort in the verdicts file.")

    # Per-model: volume vs plan quota. These diverge, and the divergence is the
    # whole point -- a cross-model call read off `cost` alone reads volume as quota.
    bym, unweighted = {}, set()
    for r in rows:
        bym.setdefault(r['model'], []).append(r)
        if quota_weight(r.get('model')) is None:
            unweighted.add(r['model'])
    w('')
    w(f"{'model':<24} {'n':>3} {'vol/agent':>11} {'quota/agent':>12} {'x':>5}")
    for m, g in sorted(bym.items(), key=lambda kv: -len(kv[1])):
        n = len(g)
        volume, known_volume, unknown_volume = _field_stats(g, 'cost')
        vol = volume / known_volume if known_volume else None
        weight = quota_weight(m)
        q = f"{fmt(vol * weight):>12}" if weight and vol is not None else f"{'?':>12}"
        x = f"{weight:>5.2f}" if weight else f"{'?':>5}"
        w(f"{m[:24]:<24} {n:>3} {fmt(vol):>11} {q} {x}")
        if unknown_volume:
            w(f"  {m} unknown normalized cost: {unknown_volume}")
    if unweighted:
        w('')
        w("WARNING: no plan-quota weight for " + ', '.join(sorted(unweighted)) + ". Their "
          "quota column is '?' rather than a guess -- add the model to QUOTA_W (relative to "
          "sonnet = 1.0) once its ratio is known, and re-check the existing weights against "
          "current pricing while you are there.")
    report_panel_yield(rows)
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
    w('Over-pin candidates from the last archive summary (advisory; no pin changes until judged):')
    for c in cands:
        w(f"  {c['family']} on {c.get('model') or 'an unrecorded model'}: a {c['effort']} vs "
          f"{c['suggested']} comparison is worth running ({c['cost_ratio']}x cost, clean on both rungs)")


def _report_candidates(rows):
    """The aggregate surface: derive over-pin candidates from the archive and
    persist them for the next dispatch-time session. Detection lives here (the
    only place with cross-dispatch context). Candidates are advisory: a pin moves
    only after a same-model comparison is judged (/orchestration_metrics)."""
    cands = compute_candidates(rows)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for c in cands:
        c['date'] = stamp
    # Atomic replace: concurrent sessions share one checkout and may run the
    # summary in parallel -- a half-written file would corrupt report()'s read.
    _atomic_write_json(CANDIDATES_FILE, cands, indent=1)
    if cands:
        w('\n-- Over-pin candidates (computed, never rated) --')
        w(f"{'family':<28} {'model':<20} {'run at':<10} {'->':<4} {'try':<8} {'cost x':>7} {'n':>4}")
        for c in cands:
            w(f"{c['family'][:28]:<28} {str(c.get('model') or '?')[:20]:<20} {c['effort']:<10} "
              f"{'->':<4} {c['suggested']:<8} "
              f"{c['cost_ratio']:>6.1f}x {c['n_hi'] + c['n_lo']:>4}")
        w("Advisory: each row is a same-model adjacent-rung comparison worth running (/pin_ab). "
          "No pin changes until that comparison is judged; record its outcome per "
          "/orchestration_metrics Incremental rating.")
    else:
        fals = sum(1 for r in rows if outcome_of(r) in ('defects', 'rework', 'discarded'))
        unrated = sum(1 for r in rows if outcome_of(r) == 'unrated')
        if fals:
            w(f'\nNo over-pin candidates ({fals} falsification event(s) in the archive). Zero '
              'candidates does not prove the table is calibrated.')
        elif unrated:
            w(f'\nNo over-pin candidates and no falsification events, but {unrated} unrated '
              'record(s) sit outside every candidate cell -- rate them or exclude the runs.')
        else:
            w('\nNo over-pin candidates and no falsification events. Zero candidates does not '
              'prove the pin table converged; a lower pin still needs a judged same-model '
              'comparison (/orchestration_metrics).')


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
    w('-- Sidecar runs (real USD; effort = requested vendor coordinate, NOT an Anthropic rung) --')
    if len(by_model) > 1:
        w('   One table per servedModel. NEVER average across models: the tiers differ ~3.1x')
        w('   on fresh tokens, so a blended $/run describes no model that exists.')

    for model_id in sorted(by_model):
        rows = by_model[model_id]
        w('')
        w(f'   [{model_id}]  {len(rows)} run(s)')
        w(f"{'label':<30} {'eff':<6} {'$cost':>8} {'in':>8} {'out':>8} {'cacheR':>8} "
          f"{'turns':>6} {'sec':>6} {'state':<10}")
        w('-' * 101)
        for r in sorted(rows, key=lambda x: x['run']):
            cu = r.get('cost_usd')
            cell = f"{cu:>8.4f}" if isinstance(cu, (int, float)) else f"{'n/a':>8}"
            secs = (f"{r['secs']:>6.0f}" if _optional_number(r.get('secs')) is not None
                    else f"{'n/a':>6}")
            w(f"{r['label'][:30]:<30} {r['effort']:<6} {cell} "
              f"{fmt(r.get('inp')):>8} {fmt(r.get('out')):>8} {fmt(r.get('cr')):>8} "
              f"{fmt(r.get('turns')):>6} {secs} {r['state']:<10}")
        w('-' * 101)
        priced = [r for r in rows if isinstance(r.get('cost_usd'), (int, float))]
        sub = sum(r['cost_usd'] for r in priced)
        mean = f"${sub / len(priced):.4f}/run" if priced else 'n/a'
        note = ''
        if len(priced) != len(rows):
            basis = next((r.get('cost_basis') for r in rows if r.get('cost_basis')), 'no dollar price')
            note = f" | {len(rows) - len(priced)} unpriced ({basis}) - excluded from $ figures"
        w(f"   subtotal ${sub:.4f} | in {_total_text(rows, 'inp')} | out {_total_text(rows, 'out')} | "
          f"cacheR {_total_text(rows, 'cr')} | turns {_total_text(rows, 'turns')} | mean {mean}{note}")

    w('')
    priced_all = [r for r in side if isinstance(r.get('cost_usd'), (int, float))]
    unpriced_all = len(side) - len(priced_all)
    w(f"{len(side)} sidecar run(s) across {len(by_model)} model(s) | "
      f"total ${sum(r['cost_usd'] for r in priced_all):.4f} over {len(priced_all)} priced run(s)"
      + (f" ({unpriced_all} unpriced)" if unpriced_all else '') + " | "
      f"in {_total_text(side, 'inp')} | out {_total_text(side, 'out')} | "
      f"cacheR {_total_text(side, 'cr')} | turns {_total_text(side, 'turns')}")
    if len(by_model) > 1:
        w("   (total is a spend figure, not a comparison - per-model subtotals above are the "
          "comparable unit)")


def family_of(label):
    """Shape family = the label's prefix before the first ':' (the stable
    authoring convention: 'plancheck:memory-gotchas' -> 'plancheck')."""
    return label.split(':', 1)[0] if ':' in label else label


UNKNOWN_MODELS = frozenset({'', '?', 'unknown', 'none'})


def _known_model(model):
    """The row's model, or None when it was not recorded. '?' and 'unknown' are placeholders, not a
    population: pooling them compares whichever models happened to go unrecorded."""
    m = str(model or '').strip()
    return None if m.lower() in UNKNOWN_MODELS else m


def compute_candidates(rows):
    """Provisional over-pin candidates derived from the archive (Anthropic rows only).

    A candidate is a (family, model, effort) cell that is clean-only, whose next-lower
    rung cell OF THE SAME MODEL is also clean-only, but which cost >= CANDIDATE_RATIO_MIN x
    as much per agent for comparable work (mean-turns ratio <= CANDIDATE_TURNS_MAX).
    Rows of another model, or rows with no model, are a different population and never
    the cheaper rung: a candidate changes exactly one factor.
    Cost alone cannot flag a cell -- adjacent rungs cost ~1.5-2x by pricing
    design -- so the turns proxy separates 'naturally pricier rung' from 'same
    work, deeper reasoning, no better outcome'. Candidates are computed, never
    narrated. They are advisory: each names a same-model adjacent-rung comparison
    worth running, and no pin moves until that comparison is judged.
    """
    by_family = {}
    for r in rows:
        if r.get('source') == 'sidecar':
            continue
        by_family.setdefault(family_of(r.get('label', '?')), []).append(r)
    cands = []
    for fam, grp in by_family.items():
        # Rows archived without transcripts (cost 0) are unmeasurable -- exclude.
        grp = [r for r in grp if _number(r.get('cost')) > 0]
        cells = {}
        for r in grp:
            cells.setdefault((_known_model(r.get('model')), r.get('effort', '?')), []).append(r)
        models = sorted({m for m, _ in cells if m is not None})
        for model in models:
            rungs = sorted((e for (m, e) in cells if m == model and e in EFFORT_ORDER),
                           key=lambda e: EFFORT_ORDER[e])
            for hi, lo in zip(rungs[1:], rungs):
                if EFFORT_ORDER[hi] != EFFORT_ORDER[lo] + 1:
                    continue  # a missing middle rung is untested, never skipped
                cand = _rung_candidate(fam, model, hi, lo, cells[(model, hi)], cells[(model, lo)])
                if cand:
                    cands.append(cand)
    return sorted(cands, key=lambda c: -c['cost_ratio'])


def _rung_candidate(fam, model, hi, lo, g_hi, g_lo):
    """One (family, model) adjacent-rung comparison; None unless every gate passes."""
    if len(g_hi) < CANDIDATE_MIN_N or len(g_lo) < CANDIDATE_MIN_N:
        return None
    if any(outcome_of(r) != 'clean' for r in g_hi + g_lo):
        return None
    c_hi, _, _ = _field_stats(g_hi, 'cost')
    c_lo, _, _ = _field_stats(g_lo, 'cost')
    c_hi /= len(g_hi)
    c_lo /= len(g_lo)
    if c_lo <= 0 or c_hi / c_lo < CANDIDATE_RATIO_MIN:
        return None
    t_hi, n_t_hi, missing_t_hi = _field_stats(g_hi, 'turns')
    t_lo, n_t_lo, missing_t_lo = _field_stats(g_lo, 'turns')
    if missing_t_hi or missing_t_lo or not n_t_hi or not n_t_lo:
        return None
    t_hi /= n_t_hi
    t_lo /= n_t_lo
    if t_lo > 0 and t_hi / t_lo > CANDIDATE_TURNS_MAX:
        return None
    return dict(family=fam, model=model, effort=hi, suggested=lo,
                cost_ratio=round(c_hi / c_lo, 2),
                n_hi=len(g_hi), n_lo=len(g_lo))


def load_record_ledger(diagnostics=None):
    """(archived (run, label) pairs, ignored runs) from bounded shards."""
    archived, ignored = set(), set()
    for record in _archive_records(diagnostics):
        run = record.get('run')
        if not run:
            continue
        if record.get('ignored'):
            ignored.add(run)
        else:
            archived.add((run, record.get('label')))
    return archived, ignored


def load_run_ledger(diagnostics=None):
    """Compatibility view of archived and ignored run identities."""
    pairs, ignored = load_record_ledger(diagnostics)
    return {run for run, _ in pairs}, ignored


def _finalize_rows(rows, diagnostics):
    """Common post-collection pipeline shared by --all-time and the default per-session
    path: effort-evidence backfill, model-mismatch warnings, and the already-archived
    filter with its skip notice. One copy so the two paths cannot silently diverge again
    (sa-design-semantics F7/F8 -- --all-time shipped without the skip notice the first time)."""
    for row in rows:
        _ensure_effort_evidence(row)
    for line in model_mismatches(rows):
        w(line)
    archived_pairs, ignored = load_record_ledger(diagnostics)
    skipped_pairs = {(row['run'], row.get('label')) for row in rows
                     if row['run'] in ignored or (row['run'], row.get('label')) in archived_pairs}
    rows = [row for row in rows
            if row['run'] not in ignored and (row['run'], row.get('label')) not in archived_pairs]
    if skipped_pairs:
        w(f"(skipping {len(skipped_pairs)} agent record(s) already in the archive ledger)")
    return rows


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
    ap.add_argument('--all-time', action='store_true',
                    help='report every session\'s Workflow runs in this project plus the global '
                         'sidecar ledger, in one call; implies --sidecar-all scope, never archives')
    ap.add_argument('--manifest-seed', help='seed JSON whose exact labels define one non-archiving manifest')
    ap.add_argument('--manifest-out', help='output path for --manifest-seed')
    ap.add_argument('--sidecar-record-dir', help='directory containing per-job *.record.json evidence')
    ap.add_argument('--manifest-verdicts', help='optional outcome JSON for manifest rows')
    ap.add_argument('--run', metavar='RUN_ID',
                    help='per-seat cache write/read, output and input-equivalent for one Workflow run; '
                         'the consolidator on its own row; never archives')
    a = ap.parse_args()

    if a.run:
        session = find_session_dir(a.session)
        if not session:
            w('Could not locate a session directory with workflow runs.')
            return 1
        return report_run(session, a.run)

    if a.manifest_seed:
        if not a.manifest_out:
            w('REFUSED: --manifest-seed requires --manifest-out.')
            return 1
        session = find_session_dir(a.session) if a.session else None
        if a.session and not session:
            w('Could not locate a session directory with workflow runs.')
            return 1
        try:
            manifest = write_manifest(
                a.manifest_seed, a.manifest_out, session=session,
                sidecar_record_dir=a.sidecar_record_dir,
                verdicts_path=a.manifest_verdicts)
        except (OSError, json.JSONDecodeError, ManifestError) as exc:
            w('REFUSED manifest: ' + str(exc))
            return 1
        w('Manifest wrote %d exact evidence row(s) to %s.'
          % (len(manifest['jobs']), a.manifest_out))
        return 0

    if a.ignore_run:
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        sentinel = {'run': a.ignore_run, 'ignored': True,
                    'reason': a.reason, 'date': stamp}
        fresh, _, conflicts = _archive_rows_atomic([sentinel])
        if conflicts:
            w(f'REFUSED: {a.ignore_run} has rated agent records in the archive -- it is '
              'archived work, not noise. An ignore sentinel would misdescribe it.')
            return 1
        if not fresh:
            w(f'{a.ignore_run} is already ignored -- nothing appended.')
            return 0
        w(f'Ignored {a.ignore_run} -- it will no longer surface in collection or summary.')
        return 0

    if a.archive_summary:
        if not _archive_paths():
            w(f'No archive at {ARCHIVE} yet.')
            return 0
        diagnostics = []
        rows = list(_archive_records(diagnostics))
        report_input_diagnostics(diagnostics)
        ignored_n = sum(1 for r in rows if r.get('ignored'))
        rows = [_ensure_effort_evidence(r) for r in rows if not r.get('ignored')]
        side = [r for r in rows if r.get('source') == 'sidecar']
        rows = [r for r in rows if r.get('source') != 'sidecar']
        by = {}
        for r in rows:
            by.setdefault((r.get('effort', '?'), outcome_of(r)), []).append(r)
        w('Effort groups use requested pins; observed effort is stored separately and may be unknown.')
        w(f"{'req-eff':<8} {'outcome':<13} {'n':>4} {'cost/agent':>11} {'unknown':>8}")
        for (e, v), g in sorted(by.items()):
            total, known, unknown = _field_stats(g, 'cost')
            average = fmt(total / known) if known else 'n/a'
            w(f'{e:<8} {v:<13} {len(g):>4} {average:>11} {unknown:>8}')
        w(f'\n{len(rows)} archived agents across {len({r.get("run", "?") for r in rows})} runs.'
          + (f' ({ignored_n} ignored-run sentinel(s) excluded.)' if ignored_n else ''))
        totals = task_totals(rows + side)
        w('\n-- Task totals (tools/task_record.py joins; quota cost and USD are separate columns) --')
        w(f"{'task':<36} {'rows':>5} {'qcost':>10} {'USD':>9} {'no-cost':>7}")
        for tid, t in sorted(totals['tasks'].items()):
            w(f"{tid[:36]:<36} {t['rows']:>5} {fmt(t['qcost']):>10} {t['cost_usd']:>9.2f} {t['rows_without_cost']:>7}")
        w(f"rows without a task: {totals['rows_without_task']} of {totals['rows']}; "
          f"rows without any cost: {totals['rows_without_cost']} of {totals['rows']}")
        if side:
            # One table per served model: a blended $/agent describes no model that exists.
            by_model = {}
            for r in side:
                by_model.setdefault(r.get('model') or '?', []).append(r)
            for model_id in sorted(by_model):
                model_rows = by_model[model_id]
                sby = {}
                for r in model_rows:
                    sby.setdefault((r.get('effort', '?'), outcome_of(r)), []).append(r)
                w('')
                w(f'-- sidecar [{model_id}] (USD; requested-effort coordinate -- do not compare to rungs above) --')
                w(f"{'effort':<8} {'outcome':<13} {'n':>4} {'$/agent':>9} {'unpriced':>9}")
                for (e, v), g in sorted(sby.items()):
                    gp = [x for x in g if isinstance(x.get('cost_usd'), (int, float))]
                    cell = f"{sum(x['cost_usd'] for x in gp)/len(gp):>9.4f}" if gp else f"{'n/a':>9}"
                    w(f"{e:<8} {v:<13} {len(g):>4} {cell} {len(g) - len(gp):>9}")
                sp = [x for x in model_rows if isinstance(x.get('cost_usd'), (int, float))]
                w(f"{len(model_rows)} archived sidecar runs | total ${sum(x['cost_usd'] for x in sp):.4f}"
                  f" over {len(sp)} priced")
        report_panel_yield(rows)
        _report_candidates(rows)
        return 0

    if a.pending:
        session = find_session_dir(a.session)
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
        pending_diagnostics = []
        sidecar_rows = [] if a.no_sidecar else collect_session_sidecar(session, pending_diagnostics)
        report_input_diagnostics(pending_diagnostics)
        sidecar_labels = {row.get('label') for row in sidecar_rows if row.get('label')}
        for key, spelling in misshaped_verdict_keys(session, sidecar_labels):
            w(f'  misshaped verdict key "{key}" -- rates nothing; write it as "{spelling}"')
        if not a.no_sidecar:
            w(f'sidecar: {len(sidecar_rows)} exact session record(s) attributed from transcript launches')
            uncaptured = uncaptured_fanout_jobs(session)
            if uncaptured:
                w(f'  {len(uncaptured)} fanout launch(es) used a jobs file no Write captured, so their records '
                  f'are unattributed: ' + ', '.join(uncaptured[:5]))
        return 0

    if a.all_time:
        # No single session to resolve -- collect_all_workflows sweeps every session this
        # project has, so an unresolvable "current" session is not a failure here.
        input_diagnostics = []
        rows = (collect_all_workflows(input_diagnostics)
                + ([] if a.no_sidecar else
                   collect_sidecar(a.sidecar_ledger, include_unlabeled=True,
                                    diagnostics=input_diagnostics)))
        rows = _finalize_rows(rows, input_diagnostics)
        w('Scope: delegated dispatch only (Workflow agents across every session in this '
          'project, plus the global sidecar ledger). NOT the account\'s global usage page. '
          'Workflow cost is normalized/plan-quota; sidecar cost is real USD -- never summed.')
        report(rows, input_diagnostics)
        if a.verdicts:
            w('\nREFUSED: --all-time is a global spend report and never archives.')
            return 1
        return 0

    # An id is resolved to its directory; collect() and pending_report() take a path, and a raw
    # id string there silently reads as a session that dispatched nothing.
    session = find_session_dir(a.session)
    if not session:
        w('Could not locate a session directory with workflow runs.')
        return 1
    input_diagnostics = []
    if a.no_sidecar:
        sidecar_rows = []
    elif a.sidecar_all:
        sidecar_rows = collect_sidecar(a.sidecar_ledger, include_unlabeled=True,
                                       diagnostics=input_diagnostics)
    else:
        sidecar_rows = collect_session_sidecar(session, input_diagnostics)
    rows = collect(session, input_diagnostics) + sidecar_rows
    rows = _finalize_rows(rows, input_diagnostics)
    if not rows:
        report(rows, input_diagnostics)
        return 0
    if a.sidecar_all:
        report(rows, input_diagnostics)
        if a.verdicts:
            w('\nREFUSED: --sidecar-all is a global spend report and never archives.')
            return 1
        return 0

    # Merge verdicts BEFORE reporting, so the table, the per-effort roll-up, and the
    # unresolved-pin warning all describe what would actually be archived. Reporting
    # first made the warning fire on pins the verdicts file had already supplied.
    pending_root = _read_verdict_file(PENDING_VERDICTS)
    pending_legacy = (_read_verdict_file(LEGACY_PENDING_VERDICTS)
                      if LEGACY_PENDING_VERDICTS != PENDING_VERDICTS else {})
    pending = load_pending_verdicts()
    consumed_pending = {}
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
        label_counts = {}
        for r in rows:
            label_counts[r['label']] = label_counts.get(r['label'], 0) + 1
        for r in rows:
            exact_key = f"{r['run']}:{r['label']}"
            selected_key = None
            if exact_key in v:
                selected_key = exact_key
                ent = v[exact_key]
            elif label_counts[r['label']] == 1:
                selected_key = r['label']
                ent = v.get(r['label'])
            else:
                ent = None
            if selected_key in pending_root:
                consumed_pending.setdefault(r['run'], {}).setdefault('root', set()).add(selected_key)
            elif selected_key in pending_legacy:
                consumed_pending.setdefault(r['run'], {}).setdefault('legacy', set()).add(selected_key)
            apply_verdict(r, ent)
            if r['outcome'] not in OUTCOMES + ('unrated',):
                # Non-fatal, per the command doc: a malformed verdict leaves ITS row unrated and the
                # archive proceeds; aborting here threw away every other rated row in the session.
                w(f"REJECTED unknown outcome '{r['outcome']}' for {r['label']} -- archived as unrated; "
                  f'allowed: {", ".join(OUTCOMES)} (legacy verdict words map through)')
                r['outcome'] = 'unrated'

    report(rows, input_diagnostics)
    if not (a.verdicts or pending):
        return 0

    nonterminal = [row for row in rows
                   if _manifest_status(row.get('state')) not in ('completed', 'failed')]
    if nonterminal:
        w(f'\nREFUSED: {len(nonterminal)} agent record(s) are not terminal; '
          'invocation is not completion and partial usage cannot become archive evidence:')
        for row in nonterminal:
            w(f"  {row['run']}:{row['label']} state={row.get('state') or 'unknown'}")
        return 1

    unresolved = [r['label'] for r in rows if r['effort'] == '?']
    if unresolved and not a.allow_unresolved_effort:
        w(f'\nREFUSED: {len(unresolved)} agent(s) have no resolved effort pin; archiving them '
          'would write records Effort Calibration cannot use:')
        for lbl in unresolved:
            w(f'  {lbl}')
        w('Supply [verdict, effort] pairs in the verdicts file, or pass '
          '--allow-unresolved-effort to archive them as "?" anyway.')
        return 1

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    for r in rows:
        r['date'] = stamp              # legacy key: archive date, kept for consumers
        r['archived_date'] = stamp
        r.setdefault('run_date', None)  # null is a legible gap; never the archive date
    fresh, skipped, conflicts = _archive_rows_atomic(rows)
    if conflicts:
        w('\nREFUSED archive conflict for run(s): ' + ', '.join(conflicts))
        return 1
    if not fresh:
        runs = ', '.join(sorted({r['run'] for r in rows}))
        w(f'\nAlready archived ({skipped} agents, run {runs}) -- nothing appended.')
        return 0
    consumed_root = set()
    consumed_legacy = set()
    for run in {row['run'] for row in fresh}:
        source_keys = consumed_pending.get(run, {})
        consumed_root.update(source_keys.get('root', ()))
        consumed_legacy.update(source_keys.get('legacy', ()))
    if consumed_root or consumed_legacy:
        try:
            removed = _prune_pending_verdicts(
                pending_root, consumed_root, pending_legacy, consumed_legacy)
        except (OSError, TimeoutError) as exc:
            w(f'\nWARNING: archive published but pending-verdict cleanup failed: {exc}')
        else:
            if removed:
                w(f'\nRemoved {removed} archived verdict(s) from {PENDING_VERDICTS}.')
    w(f'\nArchived {len(fresh)} agent records to {ARCHIVE}.'
      + (f' Skipped {skipped} already-archived.' if skipped else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
