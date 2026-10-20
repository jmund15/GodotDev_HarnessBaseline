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
per-model quota weight (registry list price relative to sonnet, see quota_weight) for
cross-model calls; it is None for any model with no weight, and the report names those
rather than defaulting them to 1.0. `list_usd` is the row's list-price USD through
`model_registry.price_run`; `cost_usd` is money SPENT, None for a plan-quota run.

Sidecar runs are a SECOND SOURCE read from exact `-R` records named by assistant
launch calls in this session's transcript. They surface side-by-side and are NEVER
merged into Workflow tables: their cost is transport-specific and their effort is
a requested vendor coordinate, not an Anthropic rung. The global spend ledger has
no Claude Code session id, so only explicit `--sidecar-all` reports it and that mode
never archives. Dedup reuses the run ledger via the synthetic run id
`sidecar-<timestamp>-<label>`.

Each review seat row joins the review ledger before it archives (`attach_review_ledger`): it gains
`author_model`, `domain` (`escape_log.domain_keys`), `review_stage`, `ledger_new` and `ledger_join`
(`joined`, `absent` or `unreadable`), and the report prints one line with the three counts.

Appending requires --archive or --verdicts (the report forms are read-only, however many pending
verdicts wait): an unoutcomed record has no denominator, so recording the falsification outcome is
the price of archiving. Re-appending an
already-archived run is a no-op (duplicates would silently skew
--archive-summary), and an unresolved '?' pin is refused rather than persisted
-- such a record cannot feed Effort Calibration, which is the only reason this
store exists.

Outcomes record whether output was accepted (clean), corrected (defects),
reworked (rework), or thrown away (discarded). An optional shape judgment is
separate: right, over (less width, model or effort would deliver the same result),
or under (the result needed more). No shape judgment is inferred from outcome.
Aggregate over-pin candidates remain comparisons to judge, not pin decisions.
Legacy verdict words (right-sized/overshoot/undershoot/wasted) map through.

Usage:
  orchestration_metrics.py                      report this session; writes nothing
  orchestration_metrics.py --session <dir>      report a specific session dir; writes nothing
  orchestration_metrics.py --archive            report, then append the session's rows to the archive
  orchestration_metrics.py --verdicts v.json    rate + append to the archive (implies --archive)
  orchestration_metrics.py --archive-summary    roll up the existing archive, with the model x role tier readout
  orchestration_metrics.py --rate LABEL OUTCOME --tier T
                                                T fills a row that recorded no role tier; a recorded tier wins
  orchestration_metrics.py --rate-batch ratings.json
                                                rate every entry in one pass over the session's records;
                                                an entry's mandates is {mandate: {outcome, unique, duplicate}}
  orchestration_metrics.py --run <runId>        per-seat usage of one panel run; never archive
  orchestration_metrics.py --manifest-seed seed.json --manifest-out manifest.json
                                                join exact Workflow/sidecar evidence; never archive
  orchestration_metrics.py --session <id> --rollup [--json out.json]
                                                price the whole session: orchestrator O, every dispatch attempt D<n>,
                                                proofs P, totals T, waste X; never archive
"""
import argparse, glob, itertools, json, os, re, sys, zlib
from contextlib import contextmanager
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks'))
from _file_lock import locked  # noqa: E402
from _win_path import drive_path  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402
import model_tier_readout  # noqa: E402
import session_timeline  # noqa: E402
import sidecar_argv  # noqa: E402
import transcript_usage  # noqa: E402
import watch_records  # noqa: E402

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
# The weight is the model's registry list OUTPUT rate over the `sonnet` alias row's, both on one
# transport (a plan's quota is its own). Output is the blend because this weight multiplies `cost`,
# which already counts output at 5x input (OUT_W), so the output rate is the price ratio that
# matches the volume unit; the 2026-08-19 opus:sonnet 2.5x was that ratio.
# A model with no row, no list price, or on another transport than the reference reports None and
# is named in the report: a quietly-wrong quota number is worse than a missing one.
QUOTA_REFERENCE_ALIAS = 'sonnet'


def quota_weight(model, data=None):
    """Plan-quota multiplier for a model id or alias, or None when unweighted."""
    data = data if data is not None else _registry()
    row = model_registry.row_for_model(model, data)
    ref = model_registry.row_for_model(QUOTA_REFERENCE_ALIAS, data)
    if row is None or ref is None or row['transport'] != ref['transport']:
        return None
    rates = [(r.get('price') or {}).get('outputPer1M') for r in (row, ref)]
    if any(_optional_number(rate) is None for rate in rates) or not rates[1]:
        return None
    return rates[0] / rates[1]


def quota_cost(cost, model, data=None):
    """Quota-equivalent cost, or None when cost/model carries no weight."""
    weight = quota_weight(model, data)
    return None if weight is None or _optional_number(cost) is None else cost * weight


def list_usd(model, usage, data=None):
    """List-price USD for one agent's usage dict (`inp`, `out`, `cw`, `cr`), or None when the
    model has no registry price or any token count is unknown. Requests apply the long-context tier;
    legacy totals use base rates and carry `tier_basis=untiered`. Recorded TTL splits are retained."""
    if any(_optional_number(usage.get(k)) is None for k in ('inp', 'out', 'cw', 'cr')):
        return None
    data = data if data is not None else _registry()
    usage['tier_basis'] = 'per-request' if usage.get('requests') is not None else 'untiered'
    return model_registry.list_price(model, usage['inp'], usage['cr'], usage['out'], data=data,
                                     cache_write=usage['cw'], requests=usage.get('requests'), multiplier=1.0)[0]


_REGISTRY = []


def _registry():
    """The live registry, read once per process; an unreadable one prices and weighs nothing."""
    if not _REGISTRY:
        try:
            _REGISTRY.append(model_registry.load())
        except model_registry.RegistryError:
            _REGISTRY.append({'models': [], 'transports': {}})
    return _REGISTRY[0]


def _spend_usd(rec):
    """A sidecar record's costUSD as SPEND: None when it is a plan-quota list-price figure."""
    return rec.get('costUSD') if model_registry.is_spend(rec.get('costModel'), rec.get('costBasis')) else None


# Anchored to the project, not the cwd: hooks import this from whatever cwd they run in, and a
# cwd-relative path would read every verdict as absent.
_PROJECT_DIR = os.environ.get('CLAUDE_PROJECT_DIR') or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(_PROJECT_DIR, '.claude', 'orchestration_metrics.jsonl')
ARCHIVE_MAX_BYTES = 5 * 1024 * 1024
ARCHIVE_MAX_ROTATIONS = 6
ARCHIVE_LOCK_TIMEOUT_SECONDS = 10.0
# Falsification outcomes, recorded at consumption (see module docstring).
OUTCOMES = ('clean', 'defects', 'rework', 'discarded')
FALSIFYING = model_tier_readout.FALSIFYING
SHAPES = ('right', 'over', 'under')
LEGACY_VERDICTS = {'right-sized': 'clean', 'overshoot': 'clean',
                   'undershoot': 'rework', 'wasted': 'discarded',
                   # fit-vocabulary used by consumption-time rich verdicts
                   'fit': 'clean', 'excellent': 'clean',
                   'fit-high-value': 'clean', 'fit-highest-value': 'clean',
                   'fit-with-one-correction': 'defects', 'misfit-input': 'discarded'}
# SIDECAR_LEDGER_PATH is the one override shared by the sidecar writer and metrics reader.
# Provider capacity is account-wide and deliberately uses its own normalized cache.
SIDECAR_LEDGER = transcript_usage.sidecar_ledger_path()
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


def _archive_paths(archive=None):
    base = archive or ARCHIVE
    rotated = [base + '.%d' % index for index in range(ARCHIVE_MAX_ROTATIONS, 0, -1)]
    return [path for path in rotated + [base] if os.path.isfile(path)]


bare_model = model_tier_readout.bare_model
bare_effort = model_tier_readout.bare_effort


def _normalized(record):
    """An archived row with the identity normalization applied; a row that never recorded a field keeps it absent."""
    if isinstance(record.get('model'), str):
        record['model'] = bare_model(record['model'])
    for key in ('effort', 'requested_effort'):
        if isinstance(record.get(key), str):
            record[key] = bare_effort(record[key])
    return record


def _archive_records(diagnostics=None, archive=None):
    for path in _archive_paths(archive):
        for record in _iter_jsonl_objects(path, diagnostics, 'archive'):
            yield _normalized(record)


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
        # stderr: hooks call this, and their stdout is the model channel.
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
    A panel seat's dict also carries `mandates` ({key: {outcome, unique, duplicate}}), which stay on the
    archived row for the panel-yield line. Its `tier` fills a row that recorded none and never replaces
    one the launch recorded. Returns the `tier` token that is not a role tier (nothing is written for it), else None."""
    rejected = None
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
            if ent.get('shape') in SHAPES:
                for field in ('shape', 'shape_profile', 'shape_model', 'shape_rated_at'):
                    if ent.get(field) is not None:
                        r[field] = ent[field]
            for field in ('mandates', 'note'):
                if ent.get(field):
                    r[field] = ent[field]
            if ent.get('tier'):
                role = _role_tier(ent['tier']) if isinstance(ent['tier'], str) else None
                if role is None:
                    rejected = ent['tier']
                elif not r.get('tier'):
                    r['tier'] = role
            if ent.get('probe'):
                r['probe'] = True
            if ent.get('measurement'):
                r['measurement'] = True
    else:
        r['outcome'] = 'unrated'
    return rejected


def latest_shape_rating(profile, model, data=None):
    """Latest explicit shape judgment for this profile/model, across pending and rotated archives."""
    data = data or model_registry.load()

    def identity(token):
        try:
            return model_registry.resolve(token, data)['id']
        except (model_registry.RegistryError, TypeError):
            return bare_model(token) if isinstance(token, str) else None

    wanted = identity(model)
    candidates = []
    for row in itertools.chain(_archive_records(), load_pending_verdicts().values()):
        if (not isinstance(row, dict) or row.get('shape') not in SHAPES
                or row.get('shape_profile') != profile or identity(row.get('shape_model')) != wanted):
            continue
        try:
            stamp = datetime.fromisoformat(row['shape_rated_at'].replace('Z', '+00:00')).timestamp()
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        candidates.append((stamp, row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _verdict_effort(ent):
    """The effort a verdict entry names, or None."""
    if isinstance(ent, (list, tuple)):
        return ent[1] if len(ent) > 1 and ent[1] else None
    if isinstance(ent, dict):
        return ent.get('effort') or None
    return None


def _select_verdict(row, verdicts, label_counts):
    """(key, entry) for one row: its exact `run:label` key first; else a bare `label` key when the
    label is unique in this collection. A bare entry naming an effort that contradicts the row's
    attested pin is another run's verdict (the pending file is shared across sessions), so it
    selects nothing and stays pending."""
    exact_key = f"{row['run']}:{row['label']}"
    if exact_key in verdicts:
        return exact_key, verdicts[exact_key]
    if label_counts.get(row['label']) != 1 or row['label'] not in verdicts:
        return None, None
    ent = verdicts[row['label']]
    named = _verdict_effort(ent)
    attested = _norm_effort(row.get('effort', '?'))
    if named and attested != '?' and _norm_effort(named) != attested:
        return None, None
    return row['label'], ent


PANEL_YIELD_WINDOW = 5


def panel_yield(rows, window=PANEL_YIELD_WINDOW):
    """(zero-unique mandates across their last `window` reached seat rows,
    [(mandate, run:label) for every not-reached mandate]) in row order.
    Single and merged seats both count; an uncovered mandate never counts as a zero."""
    reached, uncovered = {}, []
    for r in rows:
        mandates = r.get('mandates')
        if not isinstance(mandates, dict):
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


def seat_yield(rows, window=PANEL_YIELD_WINDOW):
    """Seat keys whose last `window` review runs each carry a `seat_overlap` block with unique == 0.
    One entry per (run, key), in row order. A critical two seats agree on is confirmation, so the
    flag keys on `unique` alone, never on the run's overlap %."""
    seen, by_key = set(), {}
    for r in rows:
        block = r.get('seat_overlap')
        if not isinstance(block, dict) or not block.get('key'):
            continue
        pair = (r.get('run'), block['key'])
        if pair in seen:
            continue
        seen.add(pair)
        by_key.setdefault(block['key'], []).append(_number(block.get('unique')))
    return sorted(k for k, uniques in by_key.items()
                  if len(uniques) >= window and not any(uniques[-window:]))


def report_panel_yield(rows, history=()):
    """The re-open trigger for a command's seat map (orchestration §2 *Sizing the width*).
    `history` (archived rows) extends only the seat-overlap window, so a session report counts
    earlier sessions' review runs without re-listing their mandate lines."""
    zero, uncovered = panel_yield(rows)
    for key, where in uncovered:
        w(f'UNCOVERED {key} in {where}: its seat did not reach it')
    if zero:
        w(f'Panel yield: zero unique findings in the last {PANEL_YIELD_WINDOW} reached-seat rows for '
          + ', '.join(zero) + ' -- re-open the seat map that merges it.')
    for key in seat_yield(list(history) + list(rows)):
        w(f'Panel yield: {key} added no unique finding in its last {PANEL_YIELD_WINDOW} runs -- '
          'merge it into another seat next time.')


REVIEW_WORKFLOWS = frozenset({'review-fanout'})


def seat_overlap(workflow_dir):
    """Per-seat contribution of one review run, derived from the engine journal with no rating.
    A distinct finding is one consolidator merge group; a finding in no group is a singleton.
    Returns {'seats': {lens: {label, found, unique, duplicate, unique_critical}}, 'distinct',
    'shared', 'uncovered': [lens]}, {'unreadable': reason} or {'unmeasured': reason}. No Merge
    with complete seats and no cross-seat file candidates means each source is a distinct finding;
    a missing expected Merge or incomplete seat leaves overlap unmeasured."""
    from pathlib import Path
    import session_digest
    try:
        archive = session_digest.build_workflow_result_archive(Path(workflow_dir), 'review')
    except (ValueError, OSError) as exc:
        return {'unreadable': str(exc)}
    merger = [l for l in archive['lenses'] if l['role'] == 'auxiliary']
    if not merger:
        sources = [l for l in archive['lenses'] if l['role'] == 'source']
        if any(l['status'] != 'completed' for l in sources):
            return {'unmeasured': 'a source seat did not complete, so candidate files are unknown'}
        file_seats = {}
        for item in archive['items']:
            location = item['finding'].get('file')
            if location:
                file_seats.setdefault(location.rsplit(':', 1)[0], set()).add(item['lens'])
        if any(len(seats) > 1 for seats in file_seats.values()):
            return {'unmeasured': 'cross-seat file candidates exist but no consolidation stage ran'}
    if any(l['status'] != 'completed' for l in merger):
        issues = '; '.join(i for l in merger for i in l['issues']) or 'consolidator did not complete'
        return {'unmeasured': 'expected consolidation did not complete: ' + issues}
    by_id = {item['id']: item for item in archive['items']}
    grouped = {fid for m in archive['merges'] for fid in m['merged_from']}
    groups = ([m['merged_from'] for m in archive['merges']]
              + [[fid] for fid in by_id if fid not in grouped])
    seats, uncovered = {}, []
    for l in archive['lenses']:
        if l['role'] != 'source':
            continue
        if l['status'] in ('failed', 'uncovered'):
            uncovered.append(l['lens'])
            continue
        seats[l['lens']] = dict(label=l['label'], found=0, unique=0, duplicate=0, unique_critical=0)
    shared = 0
    for group in groups:
        members = [by_id[fid] for fid in group]
        lenses = {item['lens'] for item in members}
        if len(lenses) > 1:
            shared += 1
        for lens in lenses:
            seat = seats.get(lens)
            if seat is None:
                continue
            seat['found'] += 1
            if len(lenses) > 1:
                seat['duplicate'] += 1
                continue
            seat['unique'] += 1
            if any(item['finding'].get('critical') is True for item in members):
                seat['unique_critical'] += 1
    return {'seats': seats, 'distinct': len(groups), 'shared': shared, 'uncovered': uncovered}


def session_seat_overlaps(session):
    """{run_id: seat_overlap} for every review-engine run listed under `<session>/workflows/`."""
    out = {}
    wdir = os.path.join(session, 'workflows')
    try:
        names = sorted(os.listdir(wdir))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith('.json'):
            continue
        run = _read_json_object(os.path.join(wdir, fn), None, 'workflow')
        if not run or run.get('workflowName') not in REVIEW_WORKFLOWS:
            continue
        rid = run.get('runId') or fn[:-5]
        out[rid] = seat_overlap(os.path.join(session, 'subagents', 'workflows', rid))
    return out


def report_seat_overlap(overlaps):
    for rid, got in overlaps.items():
        gap = next((k for k in ('unreadable', 'unmeasured') if k in got), None)
        if gap:
            w(f"Seat overlap {rid}: {gap} -- {got[gap]}")
            continue
        distinct, shared = got['distinct'], got['shared']
        pct = round(100 * shared / distinct) if distinct else 0
        zero = [k for k, v in got['seats'].items() if v['unique'] == 0]
        line = (f"Seat overlap {rid}: {len(got['seats'])} seats, {distinct} distinct findings, "
                f"{shared} ({pct}%) found by 2+ seats; zero-unique seats: {', '.join(zero) or 'none'}")
        if got['uncovered']:
            line += '; uncovered seats: ' + ', '.join(got['uncovered'])
        w(line)


def attach_seat_overlap(rows, overlaps):
    """Copy each seat's block onto its own agent row, so the archive carries the cross-session window."""
    for r in rows:
        got = overlaps.get(r.get('run')) or {}
        for key, seat in (got.get('seats') or {}).items():
            if seat['label'] == r.get('label'):
                r['seat_overlap'] = dict(key=key, **{f: seat[f] for f in
                                                     ('found', 'unique', 'duplicate', 'unique_critical')})


REVIEW_JOIN_STATES = ('joined', 'absent', 'unreadable')


def load_review_ledger():
    """The review ledger's rows (`review_ledger.ledger_path`), [] when it is absent, None when it exists and
    cannot be read. A corrupt line is skipped, as `review_ledger` skips it."""
    from pathlib import Path
    import review_ledger
    path = review_ledger.ledger_path(Path(_PROJECT_DIR))
    rows, _corrupt, problem = review_ledger.read_rows(path)
    return None if problem and path.exists() else rows


def _sidecar_shape(row):
    record = _read_json_object(row.get('record_path'), None, 'sidecar-record') if row.get('record_path') else None
    profile = (record or {}).get('toolProfile')
    return profile.get('shape') if isinstance(profile, dict) else None


def _ledger_run(row_id, seat):
    """The run id a ledger row id `<run>:<seat>:<mandate>[#unengaged]` carries for `seat`."""
    head, sep, _ = str(row_id).rpartition(':' + seat + ':')
    return head if sep else None


def attach_review_ledger(rows, ledger_rows):
    """Copy each review seat's ledger fields onto its row: `author_model`, `domain`, `review_stage`,
    `ledger_new` (only when a joined row carries `seatLedgerNew`) and `ledger_join`.

    A Workflow seat (`_is_review_seat`, label `review:<seat>`) joins the ledger rows whose id starts with
    `<run>:<seat>:`. A sidecar seat whose -R record's `toolProfile.shape` is `review` joins on
    (launching session, label): the ledger rows whose `session` and `seat` match, when exactly one
    prepared-result run and one row of `rows` carry that pair. `ledger_rows` None writes `unreadable`;
    no match writes `absent`. Other rows get no field."""
    import escape_log
    seats = []
    for r in rows:
        label = str(r.get('label') or '')
        if r.get('source') == 'sidecar':
            if _sidecar_shape(r) == 'review':
                seats.append((r, 'sidecar', label))
        elif _is_review_seat(r) and label.startswith('review:'):
            seats.append((r, 'workflow', label[len('review:'):]))
    pairs = {}
    for r, kind, seat in seats:
        if kind == 'sidecar':
            pairs[(r.get('session_id'), seat)] = pairs.get((r.get('session_id'), seat), 0) + 1
    for r, kind, seat in seats:
        if ledger_rows is None:
            r['ledger_join'] = 'unreadable'
            continue
        if kind == 'workflow':
            prefix = '%s:%s:' % (r.get('run'), seat)
            joined = [l for l in ledger_rows if str(l.get('id', '')).startswith(prefix)]
        else:
            joined = [l for l in ledger_rows if l.get('reviewer') == 'panel' and l.get('seat') == seat
                      and r.get('session_id') and l.get('session') == r.get('session_id')]
            if pairs[(r.get('session_id'), seat)] != 1 or len({_ledger_run(l.get('id'), seat)
                                                               for l in joined}) > 1:
                joined = []
        if not joined:
            r['ledger_join'] = 'absent'
            continue
        newest = max(str(l.get('ts') or '') for l in joined)
        joined = [l for l in joined if str(l.get('ts') or '') == newest]
        files = sorted({p for l in joined for p in (l.get('files') or {})})
        r['author_model'] = joined[0].get('authorModel')
        r['domain'] = escape_log.domain_keys(files)
        r['review_stage'] = joined[0].get('stage')
        selections = {json.dumps(l['reviewerSelection'], sort_keys=True) for l in joined
                      if isinstance(l.get('reviewerSelection'), dict)}
        r['reviewer_selection'] = json.loads(next(iter(selections))) if len(selections) == 1 else None
        identities = {(l.get('model'), bool(l.get('modelObserved')), l.get('identityBasis')) for l in joined}
        model, observed, basis = next(iter(identities)) if len(identities) == 1 else (None, False, None)
        r['reviewer_model'] = model
        r['reviewer_model_observed'] = observed
        r['reviewer_identity_basis'] = basis
        carried = [l['seatLedgerNew'] for l in joined if 'seatLedgerNew' in l]
        if carried:
            r['ledger_new'] = carried[0]
        r['ledger_join'] = 'joined'


def report_review_join(rows):
    counts = {state: sum(1 for r in rows if r.get('ledger_join') == state) for state in REVIEW_JOIN_STATES}
    if any(counts.values()):
        w('Review ledger join: ' + ', '.join('%d %s' % (counts[s], s) for s in REVIEW_JOIN_STATES))


def review_signals(rows, run_sessions):
    """Join exact journal identities; missing journals are unavailable, never zero yield."""
    overlaps = {}
    for rid in sorted({r['run'] for r in rows if r.get('workflow') in REVIEW_WORKFLOWS}):
        session = run_sessions.get(rid)
        overlaps[rid] = (seat_overlap(os.path.join(session, 'subagents', 'workflows', rid))
                         if session else {'unmeasured': 'session journal unavailable'})
    attach_seat_overlap(rows, overlaps)
    return overlaps


def attach_mandate_evidence(rows):
    """Recover consumption ratings by exact (run, label), never by an ambiguous bare label."""
    archived = {(r.get('run'), r.get('label')): r for r in _archive_records() if not r.get('ignored')}
    pending = load_pending_verdicts()
    for row in rows:
        prior = archived.get((row.get('run'), row.get('label')), {})
        ent = pending.get(f"{row.get('run')}:{row.get('label')}")
        mandates = ent.get('mandates') if isinstance(ent, dict) else None
        if mandates is None:
            mandates = prior.get('mandates')
        if isinstance(mandates, dict) and 'mandates' not in row:
            row['mandates'] = mandates


def report_run(session, run_id):
    """Per-seat usage for one Workflow run; the merge agent (phase Merge) on its own row."""
    rows = run_rows(session, run_id)
    if not rows:
        w(f'No Workflow run {run_id} in {session}.')
        return 1
    def is_merge(r):
        return r.get('phase') == 'Merge' or str(r.get('label', '')).endswith((':merge', ':consolidate'))
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
        w(f"{r['label'][:40]:<40} merge        {fmt(r.get('cw')):>8} {fmt(r.get('cr')):>8} "
          f"{fmt(r.get('out')):>7} {fmt(r.get('cost')):>9}")
    attach_mandate_evidence(rows)
    overlaps = review_signals(rows, {r['run']: session for r in rows})
    report_seat_overlap(overlaps)
    report_panel_yield(rows, [r for r in _archive_records() if not r.get('ignored')])
    if any(r.get('workflow') in REVIEW_WORKFLOWS for r in rows) and not any(r.get('mandates') for r in rows):
        w('Mandate yield unavailable: no consumption rating for this run.')
    line = superseded_line(session, run_id)
    if line:
        w(line)
    return 0


def outcome_of(r):
    """Archive outcome field, with legacy fallbacks: 'verdict' (pre-2026-08)
    and 'fit' (earliest schema) both carry verdict words."""
    v = r.get('outcome') or r.get('verdict') or r.get('fit') or '?'
    return norm_outcome(v)


def _projects_root():
    """The transcript root of the session's config dir (`CLAUDE_CONFIG_DIR`, a second account's store;
    default `~/.claude`)."""
    return model_registry.session_projects_dir()


def _candidate_project_dirs(root=None, strict=False):
    """Project dirs under _projects_root() whose slug matches this cwd -- the same
    match find_session_dir falls back to when no session id narrows it further.
    strict=False (find_session_dir's use): every project on the machine when nothing
    matches -- a wrong-but-recent single-session guess is recoverable.
    strict=True (collect_all_workflows's sweep): no match returns [] instead -- an
    all-time report silently pooling every unrelated project is a correctness defect,
    not a recoverable guess (sa-architecture-sweep F1/F2)."""
    root = root or _projects_root()
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
    root = _projects_root()

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
# home is `Model Effort Calibration Baseline.md`. They are never debt and the per-session archive
# path drops them. Keyed on the record's `workflowName` (= the script's `meta.name`).
# Measured 2026-09-09: 106 of 127 records in one session were score-cell, 681 unrated labels.
# The rest are benchmark arms, probes and corpus builders over predefined tasks (.claude/workflows/).
MEASUREMENT_WORKFLOWS = frozenset({
    'score-cell', 'key-reachability', 'agentic-cloud', 'agenttype-input-cost-probe', 'calibrate-cloud',
    'cloud-sdf', 'effort-on-agenttype-probe', 'fable51-cells', 'harness-bloat-probe', 'mine-sdf-corpus',
    'rebuild-agentic', 'rebuild-sdf', 'sdf-cloud', 'test-agents-fixtures', 'test-skill-pressure',
    'w13-cloud-w6', 'w13-cloud-ladder', 'w-axis-and-floors'})


def is_instrument_run(run):
    """True for a run that measures the harness rather than delivering work: a benchmark instrument
    workflow, or any call declaring `args.probe` (a /codify proof or text probe). A pin A/B declares
    only `args.measurement`; its arms are comparison data and stay rated."""
    args = run.get('args')
    return run.get('workflowName') in MEASUREMENT_WORKFLOWS or (isinstance(args, dict) and args.get('probe') is True)


def progress_agents(run):
    """The `workflow_agent` entries of one run's `workflowProgress`, one dispatch per label attempt.

    A spawn the client refused before it started (an `error` entry with no `agentId`, e.g. an agent type the
    session never loaded) that a later entry under the same label retried is not a dispatch: the retry is.
    A label whose only entries failed keeps them, so a failed dispatch stays visible."""
    entries = [e for e in run.get('workflowProgress') or []
               if isinstance(e, dict) and e.get('type') == 'workflow_agent']
    started = {e.get('label') for e in entries if e.get('agentId')}
    return [e for e in entries
            if e.get('agentId') or e.get('state') not in ('error', 'failed') or e.get('label') not in started]


def _agent_base(rid, run, e, efforts, pins=None):
    """The identity fields every per-agent row shares: pending candidates and archived rows group on
    these, so both are built here. The launch's own facts ride on the row where the PINS line carried
    them (`tier`, `pick`, `spec`); one it did not record stays absent, which is not the same as a value."""
    label = e.get('label') or '(unlabeled)'
    model = bare_model(e.get('model'))
    base = dict(run=rid, workflow=run.get('workflowName') or '?', label=label,
                model=model, effort=efforts.get(label, '?'), state=e.get('state') or '?')
    args = run.get('args')
    if is_instrument_run(run):
        base['instrument'] = True
    elif isinstance(args, dict) and isinstance(args.get('measurement'), str) and args['measurement']:
        base['measurement'] = True
    recorded = (pins or {}).get(label) or {}
    for key in ('tier', 'pick', 'spec'):
        if recorded.get(key):
            base[key] = recorded[key]
    return base


def _session_candidate_rows(session_dir):
    """([{run, label, model, effort, state, workflow}] unarchived, run ids, labels) from
    workflows/*.json workflow_agent entries. Reads workflow records plus the archive JSONL --
    no per-agent transcripts."""
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
        if is_instrument_run(run):
            continue
        rid = run.get('runId') or fn[:-5]
        run_ids.add(rid)
        efforts, pins = efforts_for(run), pin_tokens(run)
        for e in progress_agents(run):
            candidates.append(dict(_agent_base(rid, run, e, efforts, pins),
                                   phase=e.get('phaseTitle') or ''))
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
    unarchived = [c for c in candidates
                  if c['run'] not in ignored_runs and (c['run'], c['label']) not in archived_pairs]
    return unarchived, run_ids, {c['label'] for c in candidates}


# Saturation sampling (provisional constants): a lens group with SATURATION_N rated archive rows,
# the last SATURATION_RECENT all clean and no panel mandates, rates one pending pair in SAMPLE_EVERY.
# Only lens-engine seats sample: their label is an authored lens key, so the group is a real lens.
SATURATION_N = 10
SATURATION_RECENT = 5
SAMPLE_EVERY = 5
LENS_WORKFLOWS = frozenset({'review-fanout', 'explore-fanout'})
PENDING_WARN_KEYS = 200


def lens_of(label):
    """The label without a trailing instance number: 'p5host:plan-fold-1' -> 'p5host:plan-fold'."""
    return re.sub(r'[-_:#]?\d+$', '', str(label or ''))


def _group_key(row):
    return (lens_of(row.get('label')), row.get('model') or '?',
            _norm_effort(row.get('requested_effort') or row.get('effort') or '?'))


def _is_lens_seat(row):
    """Only recorded source-seat phases qualify; unknown legacy phases stay owed."""
    phases = {'review-fanout': 'Review', 'explore-fanout': 'Explore'}
    return row.get('workflow') in phases and row.get('phase') == phases[row['workflow']]


def saturated_groups(archive_rows):
    """{(lens, model, effort)} whose archived ratings are numerous, recently clean and mandate-free."""
    groups = {}
    for r in archive_rows:
        if r.get('ignored') or not _is_lens_seat(r):
            continue
        groups.setdefault(_group_key(r), []).append(r)
    out = set()
    for key, rows in groups.items():
        if any(r.get('mandates') for r in rows):
            continue
        rated = [outcome_of(r) for r in rows if outcome_of(r) in OUTCOMES]
        if len(rated) >= SATURATION_N and all(o == 'clean' for o in rated[-SATURATION_RECENT:]):
            out.add(key)
    return out


def sample_kept(run, label):
    """Deterministic 1-in-SAMPLE_EVERY pick, stable across --pending, --accept-rest and archive."""
    return zlib.crc32(f'{run}:{label}'.encode('utf-8')) % SAMPLE_EVERY == 0


def sampled_out(row, saturated):
    return (_is_lens_seat(row) and _group_key(row) in saturated
            and not sample_kept(row.get('run'), row.get('label')))


def pending_report(session_dir, with_sidecar=True):
    """([(run_id, label)] still pending, [ambiguous bare labels], [unmatched bare labels]).
    The first three fields of pending_detail()."""
    d = pending_detail(session_dir, with_sidecar)
    return d['remaining'], d['ambiguous'], d['unmatched']


def pending_detail(session_dir, with_sidecar=True, diagnostics=None, sidecar=None, priced=True):
    """{remaining, ambiguous, unmatched, sampled_out, rows, sidecar} for this session's unarchived dispatches.

    Verdict keys are `run_id:label`, matched first. A bare `label` key resolves a pair only
    when that label names exactly one unarchived candidate for this session -- an ambiguous
    bare label (the same label on 2+ unarchived runs) stays pending on every match, and is
    reported separately so --pending can name the ambiguity rather than silently rating none
    of them. An unrated pair in a saturated lens group outside the sample is `sampled_out`,
    not debt (orchestration_metrics.md SS Incremental rating).

    A finished sidecar dispatch this session launched and no verdict or archive row covers is debt
    too, keyed `sidecar-...:label` exactly as --rate writes it; only that exact key rates it. An
    unreadable sidecar source leaves the native debt intact and sets `sidecar_error`, so the total is
    unknown rather than clean. The result's `sidecar` is every attributed record; a caller that already
    collected them passes the rows as the `sidecar` argument and they are not read again. `priced=False`
    reads the records without repricing them: debt needs each row's run and label, never its cost.
    """
    rows, run_ids, session_labels = _session_candidate_rows(session_dir)
    candidates = [(c['run'], c['label']) for c in rows]
    verdicts = load_pending_verdicts()
    sidecar_all, sidecar_debt, sidecar_error = [], [], None
    if with_sidecar:
        try:
            sidecar_all = (sidecar if sidecar is not None
                           else collect_session_sidecar(session_dir, diagnostics) if priced
                           else collect_session_sidecar(session_dir, diagnostics, priced=False))
            archived, ignored = load_record_ledger(diagnostics)
            sidecar_debt = [r for r in sidecar_all
                            if r.get('run') not in ignored
                            and (r.get('run'), r.get('label')) not in archived
                            and f"{r.get('run')}:{r.get('label')}" not in verdicts]
        except Exception as exc:
            sidecar_error = '%s: %s' % (type(exc).__name__, exc)
            _input_diagnostic(diagnostics, 'pending-sidecar', session_dir, 'unreadable', sidecar_error)
            sidecar_all, sidecar_debt = [], []
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
    out = []
    if remaining:
        saturated = saturated_groups(_archive_records())
        by_pair = {(c['run'], c['label']): c for c in rows}
        out = [pair for pair in remaining if sampled_out(by_pair[pair], saturated)]
        dropped = set(out)
        remaining = [pair for pair in remaining if pair not in dropped]
    remaining += [(r['run'], r['label']) for r in sidecar_debt]
    ambiguous = sorted(lab for lab in bare_keys if bare_counts.get(lab, 0) > 1)
    # A bare key naming one of THIS session's labels but zero unarchived candidates is a verdict
    # that rated nothing (the pair was archived before it was recorded). Silence there is the
    # same defect as the ambiguous case. Keys naming no label of this session are other
    # sessions' history (the verdicts file accumulates) and are not reported.
    unmatched = sorted(lab for lab in bare_keys
                       if lab in session_labels and bare_counts.get(lab, 0) == 0)
    return {'remaining': remaining, 'ambiguous': ambiguous, 'unmatched': unmatched,
            'sampled_out': out, 'rows': rows, 'sidecar': sidecar_all, 'sidecar_error': sidecar_error}


def misshaped_verdict_keys(session_dir, sidecar_labels=()):
    """[(key, spelling)] for verdict keys written `label@suffix` against this session's work: a
    Workflow run id (spelled `run_id:label`) or a sidecar label (spelled as the bare label). That
    shape matches nothing, so the dispatch stays unrated while its author believes it is rated."""
    _, run_ids, session_labels = _session_candidate_rows(session_dir)
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


def _unarchived_sidecar_rows(session_dir, diagnostics=None, collected=None):
    """This session's attributed sidecar rows not yet in the archive (they carry `run` = sidecar-...),
    with the effort shape Workflow rows carry. `collected` is `collect_session_sidecar`'s rows when the
    caller already read them."""
    archived, ignored = load_record_ledger(diagnostics)
    return [dict(r, effort=_norm_effort(r.get('effort') or '?'))
            for r in (collected if collected is not None else collect_session_sidecar(session_dir, diagnostics))
            if r.get('run') not in ignored and (r.get('run'), r.get('label')) not in archived]


def _write_verdicts(entries):
    """Merge {key: verdict} into the pending file under its lock."""
    with _pending_lock():
        current = _read_json_object(PENDING_VERDICTS) or {}
        current.update(entries)
        _atomic_write_json(PENDING_VERDICTS, current)


MANDATE_OUTCOMES = ('findings', 'clean', 'not-reached')
REVIEW_INVALID_SHARE_MAX = 0.25


def _seat_precision(mandates):
    """The precision a seat's mandate counts compute, or None when no entry carries `invalid` or `fabricated`.
    Raises ValueError naming the entry whose counts cannot hold."""
    if not any('invalid' in m or 'fabricated' in m for m in mandates.values()):
        return None
    total = {'findings': 0, 'invalid': 0, 'fabricated': 0}
    for key, m in mandates.items():
        count = {k: m.get(k, 0) for k in ('unique', 'duplicate', 'invalid', 'fabricated')}
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in count.values()):
            raise ValueError(f'{key}: counts must be non-negative integers')
        findings = count['unique'] + count['duplicate']
        if count['invalid'] > findings:
            raise ValueError(f"{key}: invalid {count['invalid']} exceeds its findings {findings}")
        total['findings'] += findings
        total['invalid'] += count['invalid']
        total['fabricated'] += count['fabricated']
    bad = total['fabricated'] > 0 or total['invalid'] > REVIEW_INVALID_SHARE_MAX * total['findings']
    return dict(total, outcome='defects' if bad else 'clean')


def _is_merged_seat(label):
    """A panel seat carrying 2+ mandates is labelled `lens-a+lens-b`; its verdict needs --mandates."""
    return '+' in str(label or '')


REVIEW_STAGE_PHASES = ('Merge', 'Verify', 'Fold')


def _is_review_seat(row):
    """A review-engine agent outside its Merge, Verify and Fold stages is a panel seat; its verdict
    needs --mandates. An unrecorded phase counts as a seat, so an old record fails toward --rate,
    never toward clean."""
    return row.get('workflow') in REVIEW_WORKFLOWS and row.get('phase') not in REVIEW_STAGE_PHASES


def _same_file(a, b):
    """True when two path spellings name one file (separators, case and relative roots differ on Windows)."""
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError, TypeError):
        return False


PENDING_LIST_CAP = 10


def _rating_spec(req):
    """(spec, refusal line) for one rating request: the validated, normalized fields `_rate_row` writes."""
    label, outcome = req.get('label'), req.get('outcome')
    shape, effort, tier, mandates = req.get('shape'), req.get('effort'), req.get('tier'), req.get('mandates')
    note = req.get('note')
    if not label or not isinstance(label, str):
        return None, 'REFUSED: every rating needs a label.'
    if shape is not None and shape not in SHAPES:
        return None, f"REFUSED: unknown shape '{shape}'; use {'|'.join(SHAPES)}."
    norm = norm_outcome(outcome)
    if norm not in OUTCOMES:
        return None, f"REFUSED: unknown outcome '{outcome}'; use one of {', '.join(OUTCOMES)}."
    if effort is not None:
        pin = _norm_effort(effort)
        if pin == '?':
            return None, f"REFUSED: unknown effort '{effort}'; use low|medium|high|xhigh|max."
        effort = pin
    if tier is not None:
        role = _role_tier(tier)
        if role is None:
            return None, (f"REFUSED: unknown tier '{tier}'; use {', '.join(model_registry.TIER_ORDER)} "
                          '(validation folds to fanout).')
        tier = role
    precision = None
    if mandates is not None:
        try:
            mandates = json.loads(mandates) if isinstance(mandates, str) else mandates
        except json.JSONDecodeError as exc:
            return None, f'REFUSED: mandates is not JSON ({exc}).'
        if not isinstance(mandates, dict):
            return None, ("REFUSED: mandates (--mandates, or a --rate-batch entry's \"mandates\") must be a JSON object "
                          '{mandate: {outcome, unique, duplicate}}.')
        bad = sorted(k for k, m in mandates.items()
                     if not isinstance(m, dict) or m.get('outcome') not in MANDATE_OUTCOMES)
        if bad:
            return None, (f"REFUSED: each mandates entry needs outcome {'|'.join(MANDATE_OUTCOMES)}: "
                          + ', '.join(bad))
        try:
            precision = _seat_precision(mandates)
        except ValueError as exc:
            return None, f'REFUSED: mandates {exc}.'
    passed = norm
    if precision is not None and norm in ('clean', 'defects'):
        norm = precision['outcome']
        if norm == 'defects':
            note = note or (f"precision: {precision['invalid']} of {precision['findings']} findings invalid, "
                            f"{precision['fabricated']} fabricated")
    if norm != 'clean' and not note:
        return None, f"REFUSED: '{norm}' needs --note naming what went wrong (cite the task-record decision)."
    return dict(label=label, norm=norm, passed=passed, precision=precision, note=note, effort=effort,
                tier=tier, mandates=mandates, shape=shape, probe=bool(req.get('probe')),
                measurement=bool(req.get('measurement'))), None


def _request_matches(pending, request):
    """The pending (run, label, record path) triples a request names loosely: its text inside the key or
    record path, or its record-file stem equal to the label."""
    text = str(request).replace(chr(92), '/').lower()
    stem = text.rsplit('/', 1)[-1]
    stem = stem[:-len('.record.json')] if stem.endswith('.record.json') else stem
    return [(run, lab, path) for run, lab, path in pending
            if text in f'{run}:{lab}'.lower() or text in path.replace(chr(92), '/').lower() or stem == lab.lower()]


def _refusal_lines(label, pending_pairs, rows):
    """The lines of a refusal for a request that names no unarchived dispatch: the pending dispatches
    that match it, else the first pending ones, and the count of those not listed."""
    paths = {(r['run'], r['label']): r.get('record_path') or '' for r in rows}
    pending = [(run, lab, paths.get((run, lab), '')) for run, lab in pending_pairs]
    near = _request_matches(pending, label)
    shown = near[:PENDING_LIST_CAP] if near else pending[:PENDING_LIST_CAP]
    lines = [f'REFUSED: "{label}" names no unarchived dispatch of this session.',
             (f'Pending that match ({len(near)}):' if near
              else f'No pending dispatch matches; {len(pending)} pending:')]
    lines += [f'  {run}:{lab}' + (f'  {path}' if path else '') for run, lab, path in shown]
    hidden = len(pending) - len(shown)
    if hidden:
        lines.append(f'  {hidden} other pending not shown' if near else f'  {hidden} more not shown')
    return lines


def _rate_row(rows, pending_pairs, spec):
    """(verdict key, verdict, printed lines, refusal lines) for one validated rating against `rows`."""
    label = spec['label']
    matches = [r for r in rows if f"{r['run']}:{r['label']}" == label] \
        or [r for r in rows if r['label'] == label] \
        or [r for r in rows if r.get('record_path') and _same_file(r['record_path'], label)]
    if len(matches) != 1:
        if matches:
            lines = [f'REFUSED: "{label}" names {len(matches)} unarchived dispatches; rate one of (key or record path):']
            lines += [f"  {r['run']}:{r['label']}" + (f"  {r['record_path']}" if r.get('record_path') else "")
                      for r in matches[:PENDING_LIST_CAP]]
            return None, None, [], lines
        return None, None, [], _refusal_lines(label, pending_pairs, rows)
    row = matches[0]
    effort = spec['effort']
    if row.get('effort', '?') == '?' and not effort:
        return None, None, [], [f"REFUSED: {row['run']}:{row['label']} has no recorded effort pin; pass --effort <pin>."]
    extra = {k: v for k, v in (('note', spec['note']), ('effort', effort), ('tier', spec['tier']),
                               ('mandates', spec['mandates']), ('probe', True if spec['probe'] else None),
                               ('measurement', True if spec['measurement'] else None)) if v}
    if spec['shape'] is not None:
        extra.update(shape=spec['shape'], shape_model=row.get('model'),
                     shape_profile=_sidecar_shape(row) if row.get('source') == 'sidecar' else row.get('profile'),
                     shape_rated_at=datetime.now(timezone.utc).isoformat())
    key = f"{row['run']}:{row['label']}"
    norm, precision = spec['norm'], spec['precision']
    line = f'rated {key}: {norm}'
    if precision is not None:
        line += (f" (precision: {precision['invalid']} of {precision['findings']} invalid, "
                 f"{precision['fabricated']} fabricated)")
        if spec['passed'] != norm:
            line += f"; passed {spec['passed']}"
    return key, (dict(outcome=norm, **extra) if extra else norm), [line], []


def rate_batch(session_dir, requests, with_sidecar=True):
    """Record each request's outcome under its exact key, reading the session's dispatches once for all.
    A request is {label, outcome, note?, effort?, tier?, shape?, mandates?, probe?, measurement?}; one that
    fails validation or names no single unarchived dispatch is refused and the rest are still written, and
    a dispatch an earlier request of the batch rated is no longer a match. Returns 0 when every request
    was rated, else 1."""
    specs = [_rating_spec(req) for req in requests]
    rows, pending = [], []
    if any(spec for spec, _ in specs):
        rows, _, _ = _session_candidate_rows(session_dir)
        collected = collect_session_sidecar(session_dir) if with_sidecar else None
        if with_sidecar:
            rows = rows + _unarchived_sidecar_rows(session_dir, collected=collected)
        pending = pending_detail(session_dir, with_sidecar, sidecar=collected)['remaining']
    entries, out, refused = {}, [], 0
    for spec, refusal in specs:
        if spec is None:
            out.append(refusal)
            refused += 1
            continue
        live = [r for r in rows if f"{r['run']}:{r['label']}" not in entries]
        key, verdict, lines, refusals = _rate_row(
            live, [p for p in pending if f'{p[0]}:{p[1]}' not in entries], spec)
        if key is None:
            out.extend(refusals)
            refused += 1
            continue
        entries[key] = verdict
        out.extend(lines)
    if entries:
        _write_verdicts(entries)
    for line in out:
        w(line)
    return 1 if refused else 0


def rate(session_dir, label, outcome, note=None, effort=None, tier=None, mandates=None,
         probe=False, with_sidecar=True, shape=None, measurement=False):
    """Record one consumed dispatch's outcome and optional shape judgment under its exact key."""
    return rate_batch(session_dir, [dict(label=label, outcome=outcome, note=note, effort=effort, tier=tier,
                                         mandates=mandates, probe=probe, shape=shape,
                                         measurement=measurement)], with_sidecar)


def load_rating_batch(path):
    """([request dicts], refusal line) from a `--rate-batch` JSON file (`-` reads stdin): a non-empty list
    of objects each carrying `label` and `outcome`."""
    try:
        if path == '-':
            text = sys.stdin.read()
        else:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        return None, f'REFUSED: --rate-batch {path}: {exc}.'
    if not isinstance(data, list) or not data or not all(
            isinstance(r, dict) and r.get('label') and r.get('outcome') for r in data):
        return None, ('REFUSED: --rate-batch needs a non-empty JSON list of objects each with "label" and '
                      '"outcome" (optional note, effort, tier, shape, mandates, probe, measurement).')
    return data, None


def accept_rest(session_dir, note=None):
    """Write `clean` for every remaining pending Workflow dispatch the rater asserts was consumed and
    accepted. Skips what cannot be accepted by assertion: unfinished or failed agents, unresolved
    effort pins, review-panel seats (their mandates need --rate), and sampled-out pairs. Sidecar
    dispatches are rated only by --rate, so none is accepted unseen."""
    detail = pending_detail(session_dir, with_sidecar=False)
    by_pair = {(c['run'], c['label']): c for c in detail['rows']}
    written, skipped = {}, {}
    for r in (by_pair[p] for p in detail['remaining']):
        pair = f"{r['run']}:{r['label']}"
        if _manifest_status(r.get('state')) != 'completed':
            reason = 'not completed'
        elif r.get('effort', '?') == '?':
            reason = 'no effort pin (--rate --effort)'
        elif _is_review_seat(r) or _is_merged_seat(r['label']):
            reason = 'panel seat'
        else:
            written[pair] = {'outcome': 'clean', 'note': note} if note else 'clean'
            continue
        skipped.setdefault(reason, []).append(pair)
    if written:
        _write_verdicts(written)
    w(f'accepted {len(written)} as clean')
    for pair in list(written)[:20]:
        w(f'  {pair}')
    if len(written) > 20:
        w(f'  ... and {len(written) - 20} more')
    for reason, pairs in skipped.items():
        if reason == 'panel seat':
            w(f'skipped {len(pairs)} panel seat(s); rate each with its per-mandate yield:')
            for pair in pairs:
                w(f"  --rate {pair} <outcome> --mandates '<json>'")
            continue
        w(f'skipped {len(pairs)} ({reason}): ' + ', '.join(pairs[:10])
          + (f' +{len(pairs) - 10}' if len(pairs) > 10 else ''))
    if detail['sampled_out']:
        w(f"sampled out (saturated lens, no rating needed): {len(detail['sampled_out'])}")
    return 0


def _sessions_in(directory, diagnostics=None):
    """Every session dir with a workflows/ dir directly under `directory`."""
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        _input_diagnostic(diagnostics, 'project-directory', directory, 'unreadable', exc)
        return
    for name in names:
        session = os.path.join(directory, name)
        if os.path.isdir(os.path.join(session, 'workflows')):
            yield session


def _project_sessions(diagnostics=None):
    """Every session dir with a workflows/ dir under this project's Claude Code project dirs."""
    projects = _candidate_project_dirs(strict=True)
    if not projects:
        _input_diagnostic(diagnostics, 'project-directory', os.getcwd(), 'no-match')
    for project in projects:
        yield from _sessions_in(project, diagnostics)


def _session_of_run(sessions_root=None):
    """{run_id: session dir}. With `sessions_root`, the sessions directly under that directory; otherwise this
    project's on-disk sessions, which are matched from the cwd."""
    out = {}
    for session in (_sessions_in(sessions_root) if sessions_root else _project_sessions()):
        try:
            for fn in os.listdir(os.path.join(session, 'workflows')):
                if fn.endswith('.json'):
                    out[fn[:-5]] = session
        except OSError:
            continue
    return out


def pending_file_warning(session_dir):
    """Lines naming other sessions whose exact verdicts sit unarchived, once the file passes
    PENDING_WARN_KEYS. Deletes nothing: archiving the named session is what drains it."""
    verdicts = load_pending_verdicts()
    if len(verdicts) <= PENDING_WARN_KEYS:
        return []
    owner = _session_of_run()
    here = os.path.abspath(session_dir)
    counts = {}
    for key in verdicts:
        sess = owner.get(key.split(':', 1)[0])
        if sess and os.path.abspath(sess) != here:
            counts[sess] = counts.get(sess, 0) + 1
    lines = [f'pending verdict file holds {len(verdicts)} keys (> {PENDING_WARN_KEYS}).']
    if counts:
        lines.append('Archive these sessions (apply the Stop-gate first):')
    for sess, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {os.path.basename(sess)}: {n} verdict{'s' if n != 1 else ''} -- "
                     f"orchestration_metrics.py --session {os.path.basename(sess)} --archive")
    stray = len(verdicts) - sum(counts.values())
    if stray:
        lines.append(f'  {stray} key(s) belong to this session or name no on-disk workflow run '
                     '(sidecar, bare label, benchmark or removed session); archiving another '
                     'session will not drain them')
    return lines


def pending_labels(session_dir, with_sidecar=True):
    """[(run_id, label)] this session's workflow_agent and sidecar dispatches not yet archived or
    resolved by a pending verdict. See pending_report() for the matching rule."""
    return pending_report(session_dir, with_sidecar)[0]


def pending_count(session_dir=None, session_id=None):
    """Unrated-dispatch count for this session, or -1 when unknown. Never raises: the compaction
    hook calls it, and an advisory failure must print nothing."""
    try:
        session_dir = session_dir or find_session_dir(session_id)
        if not session_dir or not (os.path.isdir(session_dir) or os.path.isfile(session_dir + '.jsonl')):
            return -1
        detail = pending_detail(session_dir, priced=False)
        return -1 if detail['sidecar_error'] else len(detail['remaining'])
    except Exception:
        return -1


def _norm_effort(v):
    """PINS values drift in shape: 'medium', 'opus/medium', 'opus/medium x9',
    or {'model': ..., 'effort': ...}. Reduce each to the bare effort token."""
    if isinstance(v, dict):
        v = v.get('effort', '?')
    v = str(v)
    v = bare_effort(v)
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


def _role_tier(token):
    """The role tier a token names (`validation` folds onto `fanout`), else None."""
    try:
        return model_registry.canonical_tier(token)
    except model_registry.RegistryError:
        return None


def pin_tokens(run):
    """label -> {'tier', 'pick', 'spec'}: the launch facts a PINS value carries as `tier:`, `pick:` and
    `spec:` suffixes, merged over every PINS log line (an engine may log one per phase). One parser:
    `tiers_for` is its tier-only view, and the row builders take pick and spec from the same read. A role
    tier is stored canonical; the engine's `-` (no tier) and a missing suffix store nothing, and any other
    token is kept as written so the readout counts it as unparseable rather than guessing."""
    tokens = {}
    for line in run.get('logs') or []:
        s = str(line).strip()
        if not s.startswith('PINS '):
            continue
        try:
            raw = json.loads(s[5:])
        except ValueError:
            continue
        for label, value in (raw.items() if isinstance(raw, dict) else ()):
            text = str(value)
            entry = tokens.setdefault(label, {})
            tier = re.search(r'\btier:(\S+)', text)
            if tier and tier.group(1) != '-':
                entry['tier'] = _role_tier(tier.group(1)) or tier.group(1)
            for key in ('pick', 'spec'):
                found = re.search(r'\b%s:(\S+)' % key, text)
                if found and found.group(1) != '-':
                    entry[key] = found.group(1)
    return {label: entry for label, entry in tokens.items() if entry}


def tiers_for(run):
    """label -> tier: the tier-only view of `pin_tokens`."""
    return {label: entry['tier'] for label, entry in pin_tokens(run).items() if entry.get('tier')}


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
    the complete content array. The reducer is transcript_usage.reduce_records, shared with
    plan_usage_calc.py.
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
        red = transcript_usage.reduce_records(_iter_jsonl_objects(os.path.join(run_dir, fn)))
        calls = red['calls']
        first, last = red['first'], red['last']
        served, efforts, recs = red['models'], red['efforts'], red['recs']
        initial_usage = 0 if calls else None
        u = dict(inp=initial_usage, out=initial_usage, cw=initial_usage, cr=initial_usage,
                 turns=len(calls), tools=0, recs=recs, secs=None)
        usage_fields = {
            'inp': 'input_tokens', 'out': 'output_tokens',
            'cw': 'cache_creation_input_tokens', 'cr': 'cache_read_input_tokens',
        }
        for _mid, record in calls:
            m = record['message']
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
        u['requests'] = [model_registry.normalize_request(transcript_usage.call_usage(mid, record))
                         for mid, record in calls]
        u['tier_basis'] = 'per-request'
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
    same fields `agent_usage` reads, for a transcript outside a Workflow run dir (a sidecar child).

    Two kinds of row are not an identity. A `<synthetic>` model is an API-error placeholder, a
    model nobody chose (12 of 38 measured sessions read as `mixed:<synthetic>,…` without this). A
    sidechain row is a subagent's own turn: in a SESSION transcript it is delegated work and must
    not name the session's model. But a run dir's `agent-<id>.jsonl` IS a file of sidechain rows,
    and its caller (`_read_transcript`, `_contradiction`) wants that subagent's model, so when every
    assistant row is a sidechain the transcript keeps them."""
    models, efforts = set(), set()
    side_models, side_efforts = set(), set()
    for o in _iter_jsonl_objects(path):
        if o.get('type') != 'assistant':
            continue
        m = o.get('message')
        model = m.get('model') if isinstance(m, dict) and isinstance(m.get('model'), str) and m['model'] else None
        if model == model_tier_readout.SYNTHETIC:
            continue
        effort = o.get('effort') if isinstance(o.get('effort'), str) and o['effort'] else None
        into = (side_models, side_efforts) if o.get('isSidechain') else (models, efforts)
        if model:
            into[0].add(model)
        if effort:
            into[1].add(effort)
    if not models and not efforts:
        models, efforts = side_models, side_efforts
    return _one_or_mixed(models), _one_or_mixed(efforts)


def _task_claims():
    """{(source, key): task_id} from every job a task record holds, live or archived
    (`task_record.all_jobs`): a Workflow row is claimed by run id, a sidecar row by label. The record is the join key's owner; a row no
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
            task_id = name[:-5]
            for job in task_record.all_jobs(task_id):
                if job.get('source') == 'workflow' and job.get('run_id'):
                    claims[('workflow', str(job['run_id']))] = task_id
                elif job.get('source') == 'sidecar' and job.get('label'):
                    claims[('sidecar', str(job['label']))] = task_id
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


MODEL_FAMILIES = model_tier_readout.MODEL_FAMILIES


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
        served, pin = r.get('served_model'), bare_model(r.get('model'))
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
    eff, pins = efforts_for(run), pin_tokens(run)
    session_id = os.path.basename(os.path.abspath(session))
    usage = agent_usage(os.path.join(session, 'subagents', 'workflows', rid))
    for e in progress_agents(run):
        aid = e.get('agentId')
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
        base = _agent_base(rid, run, e, eff, pins)
        rows.append(dict(
            base, phase=e.get('phaseTitle') or '', agent_id=aid, session_id=session_id,
            requested_effort=base['effort'], observed_effort=u.get('served_effort'),
            run_date=run_date, task_id=claims.get(('workflow', rid)),
            cost=cost, qcost=quota_cost(cost, u.get('served_model') or e.get('model') or ''),
            list_usd=list_usd(u.get('served_model') or e.get('model') or '', u), **u))
    return rows


def collect_all_workflows(diagnostics=None):
    """Workflow rows across every session in this project, not just one -- the Workflow
    counterpart to collect_sidecar(include_unlabeled=True): both give an unconditional
    all-time view instead of the one-session default. Project-scoped, unlike the sidecar
    ledger: that file is one global path across every project on the machine, but Claude
    Code's per-session storage has no cross-project index safe to sweep from here."""
    rows = []
    for session in _project_sessions(diagnostics):
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
        rec = model_registry.reprice_record(rec, ledger)
        lab = str(rec.get('label') or '(unlabeled)')
        ts = rec.get('timestamp') or '?'
        duration = _optional_number(rec.get('durationMs'))
        requested_effort = rec.get('effort') or '?'
        identity = model_registry.record_identity(rec, token=rec.get('requestedModel'))
        rows.append(dict(
            run=f'sidecar-{ts}-{lab}', run_date=run_date_of(ts),
            workflow='sidecar', phase='', agent_id='',
            label=lab, model=str(identity['model'] or '?'), model_observed=identity['modelObserved'],
            identity_basis=identity['basis'],
            effort=requested_effort, requested_effort=requested_effort,
            observed_effort=None,
            state=_state_from_exit_code(rec.get('exitCode')),
            source='sidecar', cost=None, cost_usd=_spend_usd(rec), list_usd=rec.get('costUSD'),
            currency=rec.get('costModel'), tier_basis=rec.get('costTierBasis', 'untiered'),
            price_multiplier=rec.get('priceMultiplier') or 1.0,
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


def _pin_basis(value):
    """{'pick', 'spec'} a record's `pinBasis` names, or {} for an absent or non-enum value.
    The launcher refuses an illegal `-Y` before any spend, so a reader must not launder one into a row:
    a value the enums do not name records neither, which the readouts count as unrecorded."""
    parts = str(value).split(',') if value is not None else []
    if len(parts) != 2:
        return {}
    pick, spec = parts[0].strip(), parts[1].strip()
    if pick in model_tier_readout.PICK_KINDS and spec in model_tier_readout.SPEC_KINDS:
        return {'pick': pick, 'spec': spec}
    return {}


def _sidecar_record_row(path, record=None, diagnostics=None, claims=None, priced=True):
    """One explicit -R record shaped for manifest and archive consumers. `claims` is `_task_claims()`
    when the caller reads many records."""
    rec = (record if isinstance(record, dict) else
           _read_json_object(path, diagnostics, 'sidecar-record'))
    if not rec:
        return None
    if priced:
        rec = model_registry.reprice_record(rec, path)
    label = rec.get('label')
    if not label:
        _input_diagnostic(diagnostics, 'sidecar-record', path, 'invalid',
                          'record has no label')
        return None
    label = str(label)
    profile = rec.get('toolProfile')
    recorded = {key: value for key, value in (
        ('tier', profile.get('tier') if isinstance(profile, dict) else None),
        ('session_id', rec.get('parentSessionId'))) if value}
    recorded.update(_pin_basis(rec.get('pinBasis')))
    exit_code = rec.get('exitCode')
    timestamp = rec.get('timestamp') or '?'
    launch_id = rec.get('launchId')
    duration = _optional_number(rec.get('durationMs'))
    requested_effort = rec.get('effort') or '?'
    identity = model_registry.record_identity(rec, token=rec.get('requestedModel'))
    return dict(
        source='sidecar', label=label,
        task_id=(_task_claims() if claims is None else claims).get(('sidecar', label)),
        run=('sidecar-' + str(launch_id) if launch_id
             else 'sidecar-' + str(timestamp) + '-' + label),
        timestamp=timestamp,
        run_date=run_date_of(timestamp), workflow='sidecar', phase='', agent_id='',
        model=str(identity['model'] or '?'), model_observed=identity['modelObserved'],
        identity_basis=identity['basis'],
        effort=requested_effort, requested_effort=requested_effort,
        observed_effort=None,
        state=_state_from_exit_code(exit_code),
        transport=rec.get('transport'), currency=rec.get('costModel'),
        record_path=os.path.abspath(path), cost=None, tier_basis=rec.get('costTierBasis', 'untiered'),
        price_multiplier=rec.get('priceMultiplier') or 1.0,
        cost_usd=_spend_usd(rec), list_usd=rec.get('costUSD'), cost_basis=rec.get('costBasis') or '',
        inp=_optional_number(rec.get('inputTokens')),
        out=_optional_number(rec.get('outputTokens')),
        cw=_optional_number(rec.get('cacheWriteTokens')),
        cr=_optional_number(rec.get('cacheReadTokens')), recs=None,
        turns=_optional_number(rec.get('numTurns')),
        tools=_optional_number(rec.get('toolCalls')),
        secs=duration / 1000.0 if duration is not None else None, **recorded)


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


def _launcher_calls(command):
    """Actual top-level launcher calls as (kind, launcher arguments), read through the one launch
    reader so a caller cannot skip its `cd` and variable resolution."""
    return [(kind, args) for kind, _, args in _resolved_launcher_calls(command, _PROJECT_DIR)[1]]


def sidecar_launches(command, cwd=None):
    """[{kind, launcher, label, model, effort, record}] per sidecar job this shell command EXECUTES:
    a registered `.claude/scripts/*_sidecar.sh` launcher, or each job of a `sidecar_fanout.py` jobs
    file. [] for a command that only mentions one, or a `<launcher> --check` probe. Omitted values are None; the reader labels them."""
    cwd, calls = _resolved_launcher_calls(command, cwd or _PROJECT_DIR)
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
    out = []
    for kind, script, args in calls:
        if kind == 'sidecar':
            if args[:1] == ['--check']:
                continue
            if not os.path.isfile(os.path.join(scripts, script)):
                continue
            f = sidecar_argv.sidecar_flags(args)
            record = f.get('R')
            out.append(dict(kind='sidecar', launcher=script[:-len('_sidecar.sh')],
                            label=f.get('l') if isinstance(f.get('l'), str) else None,
                            model=f.get('m') if isinstance(f.get('m'), str) else None,
                            effort=f.get('e') if isinstance(f.get('e'), str) else None,
                            record=_resolve_session_path(record, cwd) if isinstance(record, str) else None))
            continue
        if '--dry-run' in args:
            continue
        parsed = _parse_fanout_args(args, cwd)
        if not parsed:
            continue
        jobs_path, out_dir = parsed
        try:
            with open(jobs_path, encoding='utf-8') as fh:
                jobs = fanout_spec_jobs(json.load(fh), jobs_path)
        except (OSError, ValueError):
            jobs = None
        if jobs is None:
            # `missing`: the file does not exist YET (a backgrounded call writes it after the hook reads);
            # an existing file that does not parse is refused by sidecar_fanout too.
            out.append(dict(kind='fanout', launcher='sidecar_fanout', label=os.path.basename(jobs_path),
                            model='unresolved', effort='unresolved', record=None, jobs_path=jobs_path,
                            out_dir=out_dir, missing=not os.path.exists(jobs_path)))
            continue
        for job in jobs:
            label = job.get('label')
            out.append(dict(kind='fanout', launcher=str(job.get('transport') or 'sidecar_fanout'),
                            label=label, model=job.get('alias'), effort=job.get('effort'),
                            record=os.path.join(out_dir, '%s.record.json' % label) if label else None))
    return out


def fanout_spec_jobs(spec, where):
    """The jobs of a parsed `sidecar_fanout.py` jobs spec in its own field names, through its own
    loader (a list or dispatch.js args object, dispatch.js field names renamed); None where
    sidecar_fanout refuses the spec."""
    import sidecar_fanout
    try:
        jobs, shared, _spill = sidecar_fanout.parse_jobs(spec, where)
        jobs = [sidecar_fanout.normalize_job(job, None, shared) for job in jobs]
    except sidecar_fanout.JobError:
        return None
    return jobs if all(isinstance(job, dict) for job in jobs) else None


def _command_cwd(command, cwd):
    """(cwd, variables, loops) a command's first launcher runs with (see `_launcher_states`)."""
    states, end = _launcher_states(command, cwd)
    return states[0] if states else end


def _launcher_states(command, cwd):
    """([(cwd, variables, loops)] per launcher segment, in order; the state after the last segment).
    A state holds `cwd` moved by each `cd <dir>` segment, each `NAME=value` segment, and each
    `for NAME in <words>` loop still open, that precede that launcher segment. A loop whose words hold
    an unexpanded variable, a glob or a substitution maps to None, and an assignment whose value holds
    one is dropped, so `$NAME` stays as written. An MSYS drive path (/c/...) maps to its Windows form."""
    env, loops, opened, states = {}, {}, [], []
    for tokens in sidecar_argv.shell_segments(command):
        if any(t.endswith(('sidecar_fanout.py', '_sidecar.sh')) for t in tokens):
            states.append((cwd, dict(env), dict(loops)))
            continue
        if len(tokens) >= 3 and tokens[0] == 'for' and tokens[2] == 'in':
            words = [_expand_vars(w, env) for w in tokens[3:]]
            loops[tokens[1]] = None if any(re.search(r'[$*?\[`]', w) for w in words) else words
            opened.append(tokens[1])
        elif tokens[0] in ('while', 'until'):
            opened.append(None)
        elif tokens[0] == 'done' and opened:
            loops.pop(opened.pop(), None)
        elif len(tokens) == 2 and tokens[0] == 'cd':
            target = drive_path(_expand_vars(tokens[1], env))
            cwd = _resolve_session_path(target, cwd)
        elif len(tokens) == 1 and re.match(r'^[A-Za-z_]\w*=', tokens[0]):
            name, _, value = tokens[0].partition('=')
            value = _expand_vars(value, env)
            if re.search(r'[$`]', value):
                env.pop(name, None)
            else:
                env[name] = value
    return states, (cwd, env, loops)


def _resolved_launcher_calls(command, cwd):
    """(run cwd, [(kind, script, args)]) for a command's launcher calls, with the leading `cd` and
    `NAME=value` segments applied, and one call per value of each open `for` loop the arguments name:
    the one launch reader every attribution path uses. Each call takes its own segment's variables and
    loops when calls and launcher segments pair one to one, else the first launcher's."""
    states, end = _launcher_states(command, cwd)
    named = _launcher_calls_named(command)
    run_cwd = (states[0] if states else end)[0]
    calls = []
    for index, (kind, script, args) in enumerate(named):
        _cwd, env, loops = states[index] if len(states) == len(named) else (states[0] if states else end)
        names = [n for n, words in loops.items() if words
                 and any(re.search(r'\$(?:\{%s\}|%s(?!\w))' % (n, n), a) for a in args)]
        for values in itertools.product(*(loops[n] for n in names)):
            scope = dict(env, **dict(zip(names, values)))
            calls.append((kind, script, [_expand_vars(a, scope) for a in args]))
    return run_cwd, calls


def _expand_vars(token, env):
    """`$NAME` and `${NAME}` in `token` replaced from `env`; an unknown name stays as written."""
    return re.sub(r'\$(?:\{(\w+)\}|(\w+))',
                  lambda m: env.get(m.group(1) or m.group(2), m.group(0)), token)


def _launcher_calls_named(command):
    """Actual top-level launcher calls as (kind, script basename, launcher arguments)."""
    return sidecar_argv.launcher_calls(command)


def _fanout_jobs_path(args, cwd):
    """The jobs path a fan-out call names, resolved against cwd; None when its arguments do not parse.
    Reads no file, so the transcript launch scan can cache it."""
    parsed = sidecar_argv.parse_fanout_args(args)
    return _resolve_session_path(parsed[0], cwd) if parsed else None


def _parse_fanout_args(args, cwd):
    """(jobs path, out dir) resolved against cwd; the out dir follows `sidecar_argv.fanout_out_dir`."""
    parsed = sidecar_argv.parse_fanout_args(args)
    if not parsed:
        return None
    jobs_value, out_value = parsed
    jobs_path = _resolve_session_path(jobs_value, cwd)
    spill = None
    if out_value is None:
        try:
            with open(jobs_path, encoding='utf-8') as fh:
                spec = json.load(fh)
            spill = spec.get('spillDir') if isinstance(spec, dict) and isinstance(spec.get('spillDir'), str) else None
        except (OSError, ValueError):
            spill = None
    out_dir = sidecar_argv.fanout_out_dir(
        jobs_path, _resolve_session_path(out_value, cwd) if out_value is not None else None, spill, _PROJECT_DIR)
    return jobs_path, out_dir


def _launch_feed(state, index, line):
    """Fold one transcript line into the launch scan: each launcher call an assistant shell call makes,
    in order, as [timestamp, run cwd, kind, args, jobs-file text then], the current text of every file
    a Write or Edit produced, and every path a Write named. Only transcript facts: the fan-out inventories,
    jobs files and records on disk are read when the scan is consumed."""
    if not line.strip():
        return
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(entry, dict) or entry.get('type') != 'assistant':
        return
    message = entry.get('message')
    if not isinstance(message, dict):
        return
    cwd = entry.get('cwd') or _PROJECT_DIR
    versions, launches = state['versions'], state['launches']
    for item in message.get('content') or []:
        if not isinstance(item, dict) or item.get('type') != 'tool_use':
            continue
        tool_name = str(item.get('name') or '').split('.')[-1]
        tool_input = item.get('input')
        if not isinstance(tool_input, dict):
            continue
        if tool_name == 'Write':
            file_path = _resolve_session_path(tool_input.get('file_path'), cwd)
            state['written'][file_path] = True
            if isinstance(tool_input.get('content'), str):
                versions[file_path] = tool_input['content']
            continue
        if tool_name == 'Edit':
            file_path = _resolve_session_path(tool_input.get('file_path'), cwd)
            old, new, prior = tool_input.get('old_string'), tool_input.get('new_string'), versions.get(file_path)
            if isinstance(prior, str) and isinstance(old, str) and isinstance(new, str):
                count = prior.count(old)
                if count == 1 or (count and tool_input.get('replace_all')):
                    versions[file_path] = prior.replace(old, new, -1 if tool_input.get('replace_all') else 1)
            continue
        if tool_name not in ('Bash', 'PowerShell'):
            continue
        command = tool_input.get('command')
        if not isinstance(command, str):
            continue
        run_cwd, calls = _resolved_launcher_calls(command, cwd)
        for kind, _script, args in calls:
            jobs_path = _fanout_jobs_path(args, run_cwd) if kind == 'fanout' else None
            launches.append([entry.get('timestamp'), run_cwd, kind, list(args),
                             versions.get(jobs_path) if jobs_path else None])


_LAUNCH_SCAN = None


def _launch_scan(transcript):
    """The launch scan of one session transcript, resumed from its `hooks/_transcript_scan.py` cache."""
    global _LAUNCH_SCAN
    if _LAUNCH_SCAN is None:
        import _transcript_scan
        import _win_path
        _LAUNCH_SCAN = _transcript_scan.Scanner(
            'metrics_launches', lambda: {'launches': [], 'versions': {}, 'written': {}}, _launch_feed,
            sources=[__file__, sidecar_argv.__file__, _win_path.__file__], errors='replace',
            params={'project_dir': _PROJECT_DIR})
    import _transcript_scan
    try:
        return _transcript_scan.scan(transcript, _LAUNCH_SCAN)
    except (OSError, UnicodeError):
        return {'launches': [], 'versions': {}, 'written': {}}


def _fanout_jobs_paths(transcript):
    """Fan-out jobs files named by assistant shell calls in one transcript, each mapped to its out dir."""
    paths = {}
    for _ts, run_cwd, kind, args, _text in _launch_scan(transcript)['launches']:
        if kind != 'fanout':
            continue
        parsed = _parse_fanout_args(args, run_cwd)
        if parsed:
            paths[parsed[0]] = parsed[1]
    return paths


def _session_parent(session):
    name = os.path.basename(os.path.normpath(str(session)))
    return name[:-len('.jsonl')] if name.endswith('.jsonl') else name


def _inventory_records(out_dir, parent):
    """Record paths the launcher's own fan-out inventories in out_dir list for this parent session.
    `sidecar_fanout.py` writes each inventory once at launch, so it is exact evidence even when the
    jobs file was written by a shell command the transcript never captured."""
    paths = []
    for path in sorted(glob.glob(os.path.join(glob.escape(out_dir), 'fanout-*.inventory.json'))):
        inventory = _read_json_object(path)
        if not inventory or inventory.get('parentSessionId') != parent:
            continue
        for job in inventory.get('jobs') or []:
            if isinstance(job, dict) and isinstance(job.get('recordPath'), str):
                paths.append(job['recordPath'])
    return paths


def uncaptured_fanout_jobs(session):
    """Fanout jobs files this session launched that no Write in the transcript captured.
    Attribution reads only captured jobs content, so their records stay unattributed; --pending
    names them rather than let a smaller sidecar count read as all of the session's work."""
    transcript = session if str(session).endswith('.jsonl') else str(session) + '.jsonl'
    if not os.path.isfile(transcript):
        return []
    jobs_paths = _fanout_jobs_paths(transcript)
    parent = _session_parent(session)
    captured = {path for path, out_dir in jobs_paths.items() if _inventory_records(out_dir, parent)}
    captured |= {path for path in _launch_scan(transcript)['written'] if path in jobs_paths}
    return sorted(set(jobs_paths) - captured)


def _sidecar_record_launches(session):
    """Exact -R paths proven by assistant tool calls in this session transcript."""
    transcript = session if str(session).endswith('.jsonl') else str(session) + '.jsonl'
    launches = {}
    if not os.path.isfile(transcript):
        return launches
    parent = _session_parent(session)
    inventory_launches = {}
    for timestamp, run_cwd, kind, args, content in _launch_scan(transcript)['launches']:
        launched_at = _iso_instant(timestamp)
        paths = []
        if kind == 'fanout':
            parsed = _parse_fanout_args(args, run_cwd)
            if not parsed:
                continue
            jobs_path, out_dir = parsed
            # The launcher's inventory lists each job's real -R, attempt-numbered relaunches
            # included, so it outranks rebuilding `<label>.record.json` from the jobs file.
            # Several launches can share one out dir, so an inventory record keeps its
            # earliest launch time: a later launch must not age out an earlier run's record.
            inventoried = _inventory_records(out_dir, parent)
            for path in inventoried:
                absolute = _resolve_session_path(path, run_cwd)
                prior = inventory_launches.get(absolute)
                if prior is None or (launched_at and prior and launched_at < prior):
                    inventory_launches[absolute] = launched_at
            if inventoried or not isinstance(content, str):
                continue
            try:
                jobs = fanout_spec_jobs(json.loads(content), jobs_path)
            except json.JSONDecodeError:
                continue
            for job in jobs or []:
                label = job.get('label')
                if label:
                    paths.append(os.path.join(out_dir, str(label) + '.record.json'))
        else:
            record = sidecar_argv.sidecar_flags(args).get('R')
            if isinstance(record, str):
                paths.append(_resolve_session_path(record, run_cwd))
        for path in paths:
            absolute = _resolve_session_path(path, run_cwd)
            prior = launches.get(absolute)
            if prior is None or (launched_at and (not prior or launched_at > prior)):
                launches[absolute] = launched_at
    for path, launched_at in inventory_launches.items():
        launches.setdefault(path, launched_at)
    return launches


def _inventory_job(out_dir, parent, path):
    """The `{label, launchId}` job a fan-out inventory of this parent session lists for `path`, else {}."""
    for inventory in sorted(glob.glob(os.path.join(glob.escape(out_dir), 'fanout-*.inventory.json'))):
        data = _read_json_object(inventory)
        if not data or data.get('parentSessionId') != parent:
            continue
        for job in data.get('jobs') or []:
            if isinstance(job, dict) and isinstance(job.get('recordPath'), str) \
                    and _same_file(job['recordPath'], path):
                return job
    return {}


def _pid_stamps(path):
    """([UTC instants], last `launch=` id or None) of a launch's `<record>.pid` lines."""
    stamps, launch = [], None
    try:
        with open(path + '.pid', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                fields = line.split()
                if fields and _iso_instant(fields[0]):
                    stamps.append(_iso_instant(fields[0]))
                launch = next((f[len('launch='):] for f in fields if f.startswith('launch=')), launch)
    except OSError:
        pass
    return stamps, launch


def _bodyless_sidecar_row(path, launched_at, parent, claims):
    """The row for a run that ended and left its record empty (stopped through `reap.py`, killed, or a
    launcher that exited before the child reported). It carries what the launch proves: the label and
    launch id from the fan-out inventory of this parent, the stem and the pid file's `launch=` otherwise,
    the launch instant, and no model, effort, token or cost, which the rater's `--effort` fills. None
    when the pid file's first stamp precedes this session's launch of the path: that is an earlier
    launch's file."""
    stamps, pid_launch = _pid_stamps(path)
    if stamps and launched_at and min(stamps).replace(microsecond=0) < launched_at.replace(microsecond=0):
        return None
    job = _inventory_job(os.path.dirname(path), parent, path)
    stem = os.path.basename(path)
    label = str(job.get('label') or (stem[:-len('.record.json')] if stem.endswith('.record.json') else stem))
    launch_id = job.get('launchId') or pid_launch
    first = min(stamps) if stamps else launched_at
    timestamp = first.strftime('%Y-%m-%dT%H:%M:%SZ') if first else '?'
    return dict(
        source='sidecar', label=label, task_id=claims.get(('sidecar', label)),
        run='sidecar-' + str(launch_id) if launch_id else 'sidecar-' + timestamp + '-' + label,
        timestamp=timestamp, run_date=run_date_of(timestamp), workflow='sidecar', phase='', agent_id='',
        model='?', model_observed=False, identity_basis=None, effort='?', requested_effort='?',
        observed_effort=None, state='no-record', transport=None, currency=None,
        record_path=os.path.abspath(path), cost=None, tier_basis='untiered', price_multiplier=1.0,
        cost_usd=None, list_usd=None, cost_basis='', inp=None, out=None, cw=None, cr=None, recs=None,
        turns=None, tools=None, secs=None, session_id=parent)


def collect_session_sidecar(session, diagnostics=None, priced=True):
    """Sidecar rows with exact record evidence launched by this session only. `priced=False` skips
    repricing each record from its transcript; rows then carry the recorded cost."""
    expected_parent = _session_parent(session)
    claims = _task_claims()
    rows = []
    for path, launched_at in sorted(_sidecar_record_launches(session).items()):
        # A launcher creates its record empty and fills it at exit, writing `.exit` on every exit path.
        # `watch_records.record_state` is the one ended-or-running ladder (`.exit`, else the launcher pid
        # stamped in `.pid`, else the fan-out's process): a running record is not yet a dispatch; a nonzero
        # `.exit` with an empty or absent record is a launch that failed before its child reported (a
        # preflight refusal exits before the record exists); an empty record that ended (`.exit` 0, or its
        # launcher gone with no `.exit`, as after `reap.py`) is a bodyless dispatch row.
        present = os.path.isfile(path)
        if not present or os.path.getsize(path) == 0:
            state, code, _ = watch_records.record_state(path)
            if state == 'DONE' and code != '0':
                _input_diagnostic(diagnostics, 'session-sidecar-record', path, 'launch-failed',
                                  f'exit {code}, record never written')
                continue
            if present and state in ('DONE', 'DIED'):
                row = _bodyless_sidecar_row(path, launched_at, expected_parent, claims)
                if row:
                    rows.append(row)
                continue
            if present and state in ('WATCH', 'NO-IDENTITY'):
                continue
        record = _read_json_object(path, diagnostics, 'session-sidecar-record')
        if not record or record.get('parentSessionId') != expected_parent:
            continue
        row = _sidecar_record_row(path, record, diagnostics, claims, priced)
        if not row:
            continue
        recorded_at = _iso_instant(row.get('timestamp'))
        if launched_at and (
                not recorded_at
                or recorded_at.replace(microsecond=0) < launched_at.replace(microsecond=0)):
            continue
        rows.append(row)
    return rows


MANIFEST_REQUEST_KEYS = ('role', 'model', 'effort', 'transport', 'currency', 'agentType', 'shape')


def _manifest_requested(job):
    if isinstance(job.get('requested'), dict):
        return dict(job['requested'])
    return {key: job.get(key) for key in MANIFEST_REQUEST_KEYS}


def seed_from_jobs(jobs):
    """The manifest seed for dispatched jobs: each job's requested fields, from a dispatch.js args object's
    `jobs`, a sidecar jobs file, or a sidecar record's requested pin (`model`, `effort`)."""
    rows = []
    for job in jobs or []:
        if isinstance(job, dict) and job.get('label'):
            rows.append(dict({'label': job['label']}, **{key: job.get(key) for key in MANIFEST_REQUEST_KEYS}))
    return {'schemaVersion': 1, 'jobs': rows}


def _manifest_status(state):
    value = str(state or 'unknown').lower()
    if value in ('completed', 'done', 'succeeded', 'success'):
        return 'completed'
    if value.startswith('exit-') or value in ('failed', 'error', 'cancelled', 'canceled'):
        return 'failed'
    return value


def _model_identity(value):
    """The registry id an alias or id names; the bare token when the registry does not know it."""
    bare = bare_model(value)
    try:
        return model_registry.resolve(bare)['id']
    except model_registry.RegistryError:
        row = model_registry.row_by_id(bare)
        return row['id'] if row else bare


def _route_identity(value):
    try:
        return model_registry.route_of(str(value))
    except model_registry.RegistryError:
        return str(value)


def manifest_drift(requested, effective):
    """[{field, requested, effective}] for `model`, `transport` and `currency` where the requested
    value is set, the effective value is attested and they differ. Models compare as registry ids
    and transports as routes, so an alias and its id, or a plan and its route, never drift. A
    `mixed:a,b` served set drifts when any member differs.
    `effort` is absent: the evidence attests only the requested rung."""
    identity = {'model': _model_identity, 'transport': _route_identity, 'currency': str}
    drift = []
    for field, resolve_value in identity.items():
        want, got = requested.get(field), effective.get(field)
        if want in (None, '') or got in (None, '', '?'):
            continue
        served = str(got)[len('mixed:'):].split(',') if str(got).startswith('mixed:') else [got]
        if any(resolve_value(want) != resolve_value(one) for one in served):
            drift.append({'field': field, 'requested': want, 'effective': got})
    return drift


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
        _, outcome = _select_verdict(row, verdicts, {label: 1})
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
        requested = _manifest_requested(job)
        effective = {
            'model': effective_model,
            'effort': None,
            'transport': effective_transport,
            'currency': effective_currency,
        }
        out.append({
            'label': label,
            'task_id': row.get('task_id'),
            'requested': requested,
            'effective': effective,
            'drift': manifest_drift(requested, effective),
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


def report(rows, diagnostics=None, history=()):
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
        report_panel_yield(side, history)
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
    for k in ('low', 'medium', 'high', 'xhigh', 'max', '?'):
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
          "quota column is '?' rather than a guess -- the weight comes from the registry list "
          "price, so give the model's row a price on the sonnet plan's transport.")
    report_panel_yield(rows + side, history)
    report_sidecar(side)
    _report_pending_candidates()


def _route_text(cell):
    return ' '.join(f'{route}:{cell[route]}' for route in ('wf', 'sc') if cell[route])


def report_model_effort(cells):
    """The one dispatch table: served model x requested effort, Workflow and sidecar rows together. Each unit
    (quota cost, USD) is averaged over the rows that carry it and left blank where none does."""
    w('Groups are served model x requested effort; observed effort is stored separately and may be unknown.')
    w(f"{'model':<32} {'effort':<7} {'route':<11} {'n':>4} {'clean':>5} {'defects':>7} {'rework':>6} "
      f"{'discarded':>9} {'unrated':>7} {'other':>5} {'qcost/agent':>11} {'q_n':>4} {'$/agent':>9} {'$_n':>4}")
    for c in cells:
        quota = fmt(c['qcost'] / c['qcost_n']) if c['qcost_n'] else ''
        usd = f"{c['usd'] / c['usd_n']:.4f}" if c['usd_n'] else ''
        w(f"{c['model']:<32} {c['effort']:<7} {_route_text(c):<11} {c['n']:>4} {c['clean']:>5} {c['defects']:>7} "
          f"{c['rework']:>6} {c['discarded']:>9} {c['unrated']:>7} {c['other']:>5} {quota:>11} {c['qcost_n'] or '':>4} "
          f"{usd:>9} {c['usd_n'] or '':>4}".rstrip())


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


COVERAGE_BUCKETS = ('rated', 'unrated', 'sampled_out', 'absent', 'unparseable')


def archive_rows(diagnostics=None, archive=None):
    """(Workflow rows, sidecar rows, ignored-run sentinel count) of the archive and its rotations."""
    rows = list(_archive_records(diagnostics, archive))
    ignored_n = sum(1 for r in rows if r.get('ignored'))
    rows = [_ensure_effort_evidence(r) for r in rows if not r.get('ignored')]
    side = [r for r in rows if r.get('source') == 'sidecar']
    return [r for r in rows if r.get('source') != 'sidecar'], side, ignored_n


def row_date(row):
    """UTC date of an archived row: its own run_date, else the date its run id carries, else None."""
    return run_date_of(row.get('run_date')) or run_date_of(row.get('run'))


def coverage_of(row):
    """The one bucket a row's rating state falls in; the five buckets partition every row."""
    if row.get('sampled_out'):
        return 'sampled_out'
    outcome = outcome_of(row)
    if outcome in OUTCOMES:
        return 'rated'
    if outcome == 'unrated':
        return 'unrated'
    return 'absent' if outcome == '?' else 'unparseable'


UNRATED_REASONS = ('sidecar', 'panel-seat', 'engine-step', 'not-completed', 'no-effort', 'sampled-out',
                   'session-never-rated')


def _is_engine_step(row):
    """A review engine's own Merge, Verify or Fold step: not a panel seat (the lens work is the seat,
    the stage is the engine's own move), and never hand-rated, so an unrated row here is the engine
    working rather than a rating debt."""
    if str(row.get('label') or '').endswith((':merge', ':consolidate')):
        return True
    return row.get('phase') in REVIEW_STAGE_PHASES


def unrated_reason(row, session_rows):
    """Why one archived row is unrated: a pure function of the row and the rows of its session (or run),
    derived at read and never stored. Precedence: the deliberate exclusion first, then what the row IS
    (its route and seat kind), then its state and pin, then the session-level fact. None when nothing
    explains it — a row in a session that was rated, otherwise rateable, is plain rating debt."""
    if row.get('sampled_out'):
        return 'sampled-out'
    if row.get('source') == 'sidecar':
        return 'sidecar'
    if _is_review_seat(row) or _is_merged_seat(row.get('label')):
        return 'panel-seat'
    if _is_engine_step(row):
        return 'engine-step'
    if _manifest_status(row.get('state')) != 'completed':
        return 'not-completed'
    if _norm_effort(row.get('effort') or row.get('requested_effort') or '?') == '?':
        return 'no-effort'
    if not any(outcome_of(r) in OUTCOMES for r in session_rows):
        return 'session-never-rated'
    return None


def _is_number(value):
    return isinstance(value, (int, float))


def _effort_rank(effort):
    """Ladder rung order (low to max), then any other requested effort by name, then `?`."""
    return (EFFORT_ORDER.get(effort, 6 if effort == '?' else 5), effort)


def _cell_column(row):
    """The one column of a cell a row counts in: its rated outcome, `unrated` (a sampled-out row is one), or
    `other` for a row whose outcome is missing or not in the vocabulary."""
    outcome = outcome_of(row)
    return outcome if outcome in OUTCOMES or outcome == 'unrated' else 'other'


def model_effort_cells(tagged, *, split_route=False, name_of=None):
    """One cell per (model, requested effort), Workflow (`wf`) and sidecar (`sc`) rows together, or one
    per route within each pair when `split_route`. `tagged` is [(route, row)]; `name_of` maps a row to its model
    (default: `model_tier_readout.model_identity`). A cell counts its route mix, its rows, one column for every
    row (clean, defects, rework, discarded, unrated, other: they sum to `n`), and each cost unit beside the number
    of rows carrying it: quota cost from Workflow rows, USD from sidecar rows. A model with no recorded identity
    is `?`, listed last."""
    name_of = name_of or (lambda row: model_tier_readout.model_identity(row)[0])
    groups = {}
    for route, row in tagged:
        key = (name_of(row), row.get('effort') or '?') + ((route,) if split_route else ())
        groups.setdefault(key, []).append((route, row))
    cells = []
    for key in sorted(groups, key=lambda k: (k[0] == '?', k[0], _effort_rank(k[1]), k[2:] == ('sc',))):
        members = groups[key]
        columns = [_cell_column(r) for _, r in members]
        quota = [r for route, r in members if route == 'wf']
        priced = [r['cost_usd'] for route, r in members if route == 'sc' and _is_number(r.get('cost_usd'))]
        qcost, qcost_n, _ = _field_stats(quota, 'qcost')
        cell = {'model': key[0], 'effort': key[1], 'wf': len(quota), 'sc': len(members) - len(quota),
                'n': len(members)}
        cell.update({name: columns.count(name) for name in OUTCOMES + ('unrated', 'other')})
        cell.update(qcost=qcost, qcost_n=qcost_n, usd=sum(priced), usd_n=len(priced))
        cells.append(cell)
    return cells


def _pays_per_token(row, entry, data):
    """True for a row that spent money on a pay-per-token route: priced above zero on a registry route whose cost
    model is `marginal-usd`. A free route prices at zero, a plan-quota route carries no marginal cost, and a model
    the registry does not know has no route to judge."""
    usd = row.get('cost_usd')
    if not _is_number(usd) or usd <= 0 or entry is None:
        return False
    try:
        return model_registry.cost_model_for(entry['transport'], data) == 'marginal-usd'
    except (model_registry.RegistryError, KeyError):
        return False


def archive_facts(rows, side, *, month=None, registry=None):
    """The numbers behind --archive-summary, for the console and the dashboard alike.

    `month` ('YYYY-MM') keeps the rows dated in it; rows with no recoverable date are counted in
    `undated` of the population passed in and belong to no month. `cells` and `route_cells` group every row
    by served model and requested effort, Workflow and sidecar together; each cost unit stays in its own field,
    so no blended figure exists. `floor_cells` is family -> model -> effort over rated Workflow rows. Every
    grouping names a model through `model_tier_readout.model_identity`. `paid` is the money spent on pay-per-token
    routes: {usd, n} over the rows priced above zero there. `registry` defaults to the live registry; an unreadable
    one leaves every id naming itself.
    """
    if registry is None:
        try:
            registry = model_registry.load()
        except model_registry.RegistryError:
            registry = {'models': []}
    identity = {}

    def who(row):
        if id(row) not in identity:
            identity[id(row)] = model_tier_readout.model_identity(row, registry)
        return identity[id(row)]

    everything = rows + side
    dates = [row_date(r) for r in everything]
    if month:
        rows = [r for r in rows if (row_date(r) or '').startswith(month)]
        side = [r for r in side if (row_date(r) or '').startswith(month)]
    kept = [d for d in (row_date(r) for r in rows + side) if d]
    coverage = {}
    for name, group in (('workflow', rows), ('sidecar', side), ('total', rows + side)):
        coverage[name] = {bucket: 0 for bucket in COVERAGE_BUCKETS}
        for r in group:
            coverage[name][coverage_of(r)] += 1
    floor_cells = {}
    for r in rows:
        if coverage_of(r) == 'rated':
            cell = floor_cells.setdefault(family_of(r.get('label') or '?'), {}).setdefault(
                who(r)[0], {}).setdefault(r.get('effort', '?'), {'rated': 0, 'falsified': 0})
            cell['rated'] += 1
            cell['falsified'] += outcome_of(r) in FALSIFYING
    tagged = [('wf', r) for r in rows] + [('sc', r) for r in side]
    spent = [r['cost_usd'] for _, r in tagged if _pays_per_token(r, who(r)[1], registry)]
    return {
        'rows': len(rows) + len(side),
        'undated': sum(1 for d in dates if d is None),
        'first': min(kept) if kept else None, 'last': max(kept) if kept else None,
        'coverage': coverage,
        'runs': len({r.get('run', '?') for r in rows}),
        'sampled_n': sum(1 for r in rows if r.get('sampled_out')),
        'cells': model_effort_cells(tagged, name_of=lambda r: who(r)[0]),
        'route_cells': model_effort_cells(tagged, split_route=True, name_of=lambda r: who(r)[0]),
        'floor_cells': floor_cells, 'paid': {'usd': sum(spent), 'n': len(spent)},
        'totals': task_totals(rows + side), 'candidates': compute_candidates(rows),
    }


def _report_candidates(rows, cands=None):
    """The aggregate surface: derive over-pin candidates from the archive and
    persist them for the next dispatch-time session. Detection lives here (the
    only place with cross-dispatch context). Candidates are advisory: a pin moves
    only after a same-model comparison is judged (/orchestration_metrics)."""
    cands = [dict(c) for c in (compute_candidates(rows) if cands is None else cands)]
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
        unspent = [r for r in rows if not isinstance(r.get('cost_usd'), (int, float))]
        if unspent:
            basis = next((r.get('cost_basis') for r in unspent if r.get('cost_basis')), 'no dollar price')
            note = f" | {len(unspent)} with no spend ({basis}) - excluded from $ figures"
        listed = [r['list_usd'] for r in rows if isinstance(r.get('list_usd'), (int, float))]
        if listed:
            note += f" | list-price ${sum(listed):.4f} over {len(listed)} run(s) (comparison, not spend)"
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


ROLLUP_SCOPE = ("this session's main transcript, its Workflow agents and its attributed sidecar "
                "records; not the account usage page")
ROLLUP_UNIT = ("tokens by column plus `list`, a per-request list-price weight, never spend; "
               "recorded cache-write TTL splits are retained; totals-only rows carry tier_basis=untiered")
# Report classifiers for --rollup: each picks what a row counts; none gates anything. The idle gap is the
# 1-hour prompt-cache TTL (F9).
IDLE_REWRITE_GAP_S = 3600
IDLE_REWRITE_MIN_TOKENS = 50000
CONTEXT_CALL_TOKENS = 400000
_EPOCH_MS_THRESHOLD = 1e11


def orchestrator_row(transcript_path, data=None):
    """Row `O`: the main transcript's deduped calls priced at list, its wall-time partition and the
    waste measures read off its calls. An unpriced model adds to `unpriced_calls`, never to `list`."""
    data = data if data is not None else _registry()
    wall = session_timeline.partition(transcript_path)
    bookkeeping_ids = set(wall.pop('bookkeeping_ids'))
    big_results = wall.pop('big_results')
    tool_messages = wall.pop('tool_messages')
    row = dict(id='O', kind='orchestrator', state=wall['state'], calls=0, fresh=0, cache_read=0,
               cache_write_5m=0, cache_write_1h=0, output=0, list=0.0, tier_basis='per-request', unpriced_calls=0,
               calls_over_400k=0, idle_rewrites=dict(n=0, tokens=0, list=0.0),
               bookkeeping=dict(messages=0, of=tool_messages, list=0.0), big_results=big_results, wall=wall)
    if wall['state'] != 'ok':
        return row
    previous_ts = None
    for c in transcript_usage.read_transcript(transcript_path, ledger={})['calls']:
        if c['model'] == transcript_usage.SYNTHETIC_MODEL:
            continue
        fresh, cache_read, output = c['inp'] or 0, c['cr'] or 0, c['out'] or 0
        written, written_1h = c['cw'] or 0, c['cw1h'] or 0
        usd, _problem = model_registry.list_price(
            c['model'] or '?', fresh, cache_read, output, data=data, cache_write=written - written_1h,
            cache_write_1h=written_1h, multiplier=1.0, requests=[c])
        row['calls'] += 1
        row['fresh'] += fresh
        row['cache_read'] += cache_read
        row['cache_write_5m'] += written - written_1h
        row['cache_write_1h'] += written_1h
        row['output'] += output
        if usd is None:
            row['unpriced_calls'] += 1
        else:
            row['list'] += usd
        if fresh + cache_read + written > CONTEXT_CALL_TOKENS:
            row['calls_over_400k'] += 1
        if (previous_ts is not None and c['ts'] is not None and c['ts'] - previous_ts > IDLE_REWRITE_GAP_S
                and written >= IDLE_REWRITE_MIN_TOKENS):
            row['idle_rewrites']['n'] += 1
            row['idle_rewrites']['tokens'] += written
            row['idle_rewrites']['list'] += usd or 0.0
        if c['id'] in bookkeeping_ids:
            row['bookkeeping']['messages'] += 1
            row['bookkeeping']['list'] += usd or 0.0
        if c['ts'] is not None:
            previous_ts = c['ts']
    return row


def journal_attempts(run_dir):
    """The `started` rows of one run's journal in file order, each with its re-run class.

    Within one label, a later start whose key equals an earlier start's is a `retry`. One whose key
    differs from the label's previous start is a `rerun` when that attempt has a `result`/`failed` row
    (finished work run again) and a `resume` when it has not. `state` is `ok`, `absent`, or
    `unreadable at line N`; an unreadable journal yields no starts, so nothing from it is merged.
    Returns {'state', 'starts': [{agentId, key, label, phase, line}], 'classes': {agentId: class}}."""
    out = dict(state='ok', starts=[], classes={})
    journal = watch_records.read_journal(run_dir)
    if journal['state'] != 'ok':
        out['state'] = journal['state']
        return out
    if journal['bad']:
        return dict(state='unreadable at line %d' % journal['bad'], starts=[], classes={})
    parsed = [(e['line'], e) for e in journal['events']]
    finished = {e['agentId'] for e in journal['events'] if e['type'] in ('result', 'failed')}
    by_label = {}
    for number, row in parsed:
        if row.get('type') != 'started':
            continue
        start = dict(agentId=row.get('agentId'), key=row.get('key'), label=row.get('label'),
                     phase=row.get('phase'), line=number)
        earlier = by_label.setdefault(start['label'], [])
        if any(e['key'] == start['key'] for e in earlier):
            out['classes'][start['agentId']] = 'retry'
        elif earlier:
            out['classes'][start['agentId']] = 'rerun' if earlier[-1]['agentId'] in finished else 'resume'
        earlier.append(start)
        out['starts'].append(start)
    return out


def attempt_rows(session, run_id, diagnostics=None):
    """Rows for agents of one run that `workflowProgress` does not list, which `run_rows` (one row per
    label) never sees: `superseded` when the journal started the agent, `orphan` (label `?`) when only a
    transcript holds it. An unreadable journal yields none: it cannot say which transcripts are orphans."""
    run = _read_json_object(os.path.join(session, 'workflows', run_id + '.json'), diagnostics, 'workflow')
    if not run:
        return []
    rid = run.get('runId') or run_id
    run_dir = os.path.join(session, 'subagents', 'workflows', rid)
    journal = journal_attempts(run_dir)
    if journal['state'] not in ('ok', 'absent'):
        return []
    listed = {e.get('agentId') for e in run.get('workflowProgress') or []
              if isinstance(e, dict) and e.get('type') == 'workflow_agent'}
    usage = agent_usage(run_dir)
    eff, pins = efforts_for(run), pin_tokens(run)
    session_id = os.path.basename(os.path.abspath(session))
    labels = {s['agentId']: s for s in journal['starts']}
    rows = []
    for aid in list(labels) + sorted(a for a in usage if a not in labels):
        if aid in listed or aid not in usage:
            continue
        start = labels.get(aid)
        u = dict(usage[aid])
        u.pop('first_ts', None)
        model = u.get('served_model') or ''
        base = _agent_base(rid, run, dict(label=start['label'] if start else '?', model=model), eff, pins)
        rows.append(dict(base, phase=(start or {}).get('phase') or '', agent_id=aid, session_id=session_id,
                         attempt='superseded' if start else 'orphan',
                         run_date=run_date_of(run.get('timestamp')), list_usd=list_usd(model, u), **u))
    return rows


def _sidecar_list(row, data=None):
    """List price from reachable requests, an already tiered record, or marked untiered totals."""
    parts = [_optional_number(row.get(k)) for k in ('inp', 'cr', 'out', 'cw')]
    if parts[0] is None and parts[2] is None:
        return None
    inp, cr, out, cw = (p or 0 for p in parts)
    requests = row.get('requests')
    if requests is None and row.get('record_path'):
        rec = _read_json_object(row['record_path'], None, 'sidecar-record')
        if rec:
            priced = model_registry.reprice_record(rec, row['record_path'], data=data if data is not None else _registry())
            if priced.get('costTierBasis') != 'untiered':
                row['tier_basis'] = priced['costTierBasis']
                row['pricing_problem'] = priced.get('costPricingProblem')
                usd = _optional_number(priced.get('costUSD'))
                return usd / (priced.get('priceMultiplier') or 1.0) if usd is not None else None
    if requests is None and row.get('tier_basis') == 'invalid':
        return None
    if requests is None and str(row.get('tier_basis')).startswith('per-request') and _optional_number(row.get('list_usd')) is not None:
        return row['list_usd'] / (row.get('price_multiplier') or 1.0)
    row['tier_basis'] = 'per-request' if requests is not None else 'untiered'
    hour = row.get('cw1h') or 0
    return model_registry.list_price(row.get('model') or '?', inp, cr, out, data=data if data is not None else _registry(), cache_write=cw - hour,
                                     cache_write_1h=hour, requests=requests, multiplier=1.0)[0]


def _row_list(row):
    return _sidecar_list(row) if row.get('source') == 'sidecar' else _optional_number(row.get('list_usd'))


def _dispatch_row(row, kind, number, rerun, journal):
    return dict(
        id='D%d' % number, kind=kind, run=row.get('run'), label=row.get('label'),
        engine='sidecar' if row.get('source') == 'sidecar' else 'workflow', workflow=row.get('workflow'),
        model=row.get('model'), effort=row.get('effort'), attempt=row.get('attempt', 'current'),
        rerun=rerun, journal=journal,
        tokens=dict(fresh=row.get('inp'), cache_read=row.get('cr'), cache_write=row.get('cw'),
                    output=row.get('out')),
        list=_row_list(row), tier_basis=row.get('tier_basis', 'untiered'), secs=row.get('secs'), outcome='n/a', waste=None, waste_list=0.0, waste_min=0.0,
        **{'yield': None})


def _rollup_outcomes(session_rows):
    """{id(row): outcome}: the pending verdict, else the archived record for (run, label), else `unrated`."""
    pending = load_pending_verdicts()
    archived = {}
    for rec in _archive_records():
        if not rec.get('ignored') and rec.get('run') and rec.get('label'):
            archived[(rec['run'], rec['label'])] = outcome_of(rec)
    label_counts = {}
    for r in session_rows:
        label_counts[r['label']] = label_counts.get(r['label'], 0) + 1
    out = {}
    for r in session_rows:
        _key, ent = _select_verdict(r, pending, label_counts)
        scratch = {}
        if ent:
            apply_verdict(scratch, ent)
        got = scratch.get('outcome') or archived.get((r['run'], r['label']))
        out[id(r)] = got if got in OUTCOMES else 'unrated'
    return out


def _seat_yield(row, overlaps):
    """A review seat's `{found, unique, duplicate}`, its run's `unmeasured`/`unreadable` state, else None."""
    if not _is_review_seat(row) or row.get('attempt', 'current') != 'current':
        return None
    got = overlaps.get(row.get('run'))
    if not got:
        return None
    gap = next((k for k in ('unreadable', 'unmeasured') if k in got), None)
    if gap:
        return dict(state=gap, reason=got[gap])
    for seat in got['seats'].values():
        if seat['label'] == row.get('label'):
            return dict(found=seat['found'], unique=seat['unique'], duplicate=seat['duplicate'])
    return None


def _harness_runs_path():
    return session_timeline.runner().runs_path()


def proofs_row(sid, o_row):
    """Row `P`: this session's harness runs (`harness_runs.json` rows whose `session` is `sid`) plus the
    foreground test and commit time read off the transcript partition. `state` is `ok`, `record-absent`,
    `unreadable`, or `window-after-start` when the retained rows begin after the session did."""
    wall = o_row['wall']
    commits = wall.get('commit_secs') or []
    budget = session_timeline.commit_budget_s()
    row = dict(id='P', kind='proofs', state='ok', runs=0, wall_s=0.0, queue_s=0.0, reused=0, ran=0,
               fg_test_s=wall['buckets'].get('tool:shell_test', 0.0),
               commits=dict(n=len(commits), secs=sum(commits),
                            over_budget=sum(1 for c in commits if c > budget), budget_s=budget))
    path = _harness_runs_path()
    if not os.path.isfile(path):
        row['state'] = 'record-absent'
        return row
    doc = _read_json_object(path)
    runs = doc.get('runs') if isinstance(doc, dict) else None
    if not isinstance(runs, list):
        row['state'] = 'unreadable'
        return row
    start = transcript_usage.parse_ts(wall.get('start'))
    stamps = [r['ts'] for r in runs if isinstance(r, dict) and _optional_number(r.get('ts')) is not None]
    if start is not None and stamps and min(stamps) > start:
        row['state'] = 'window-after-start'
    for run in runs:
        if not isinstance(run, dict) or run.get('session') != sid:
            continue
        row['runs'] += 1
        row['wall_s'] += _number(run.get('wall_s'), 0.0)
        row['queue_s'] += _number(run.get('queue_s'), 0.0)
        reused = _number(run.get('reused'), 0)
        row['reused'] += reused
        row['ran'] += _number(run.get('selected'), 0) - reused
    return row


def _wall_split(buckets):
    split = dict(orchestrator=0.0, dispatches=0.0, proofs=0.0, owner=0.0, away=0.0, other=0.0)
    for name, secs in buckets.items():
        if name in ('tool:shell_test', 'idle:shell_test_bg'):
            split['proofs'] += secs
        elif name == 'gen' or name.startswith('tool:'):
            split['orchestrator'] += secs
        elif name.startswith('idle:'):
            split['dispatches'] += secs
        elif name in ('owner', 'away'):
            split[name] += secs
        else:
            split['other'] += secs
    return split


def _union_seconds(spans):
    """Total length of the union of (start, seconds) spans; a span with no start counts its seconds alone."""
    total, end = 0.0, None
    for start, secs in sorted((s for s in spans if s[0] is not None), key=lambda s: s[0]):
        if end is None or start >= end:
            total += secs
            end = start + secs
        elif start + secs > end:
            total += start + secs - end
            end = start + secs
    return total + sum(secs for start, secs in spans if start is None)


def _stamp_seconds(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000.0 if value > _EPOCH_MS_THRESHOLD else float(value)
    return transcript_usage.parse_ts(value) or 0.0


def build_rollup(session, diagnostics=None, overlaps=None):
    """The `--rollup` document: rows O, D1..Dn, P, T and X for one session directory. Read-only.
    `overlaps` replaces the journal-derived review seat overlaps (`session_seat_overlaps`)."""
    sid = os.path.basename(os.path.normpath(str(session)))
    o_row = orchestrator_row(os.path.normpath(str(session)) + '.jsonl')
    overlaps = session_seat_overlaps(session) if overlaps is None else overlaps
    wdir = os.path.join(session, 'workflows')
    try:
        names = sorted(fn for fn in os.listdir(wdir) if fn.endswith('.json'))
    except OSError:
        names = []
    claims = _task_claims()
    groups, spans = [], {}
    for fn in names:
        run_id = fn[:-5]
        record = _read_json_object(os.path.join(wdir, fn), diagnostics, 'workflow')
        if not record:
            continue
        rid = record.get('runId') or run_id
        journal = journal_attempts(os.path.join(session, 'subagents', 'workflows', rid))
        order = {s['agentId']: s['line'] for s in journal['starts']}
        rows = ([dict(r, attempt='current') for r in run_rows(session, run_id, diagnostics, claims)]
                + attempt_rows(session, run_id, diagnostics))
        rows.sort(key=lambda r: order.get(r.get('agent_id'), float('inf')))
        kind = 'instrument' if is_instrument_run(record) else 'workflow'
        for aid, u in agent_usage(os.path.join(session, 'subagents', 'workflows', rid)).items():
            spans[(rid, aid)] = (transcript_usage.parse_ts(u.get('first_ts')), u.get('secs') or 0.0)
        groups.append((_stamp_seconds(record.get('startTime') or record.get('timestamp')), rid, kind, journal,
                       rows))
    for row in collect_session_sidecar(session, diagnostics):
        groups.append((_stamp_seconds(row.get('timestamp')), row.get('run') or '', 'sidecar',
                       dict(state='n/a', classes={}), [dict(row, attempt='current')]))
    groups.sort(key=lambda g: (g[0], g[1]))
    outcomes = _rollup_outcomes([r for g in groups if g[2] != 'instrument' for r in g[4]
                                 if r['attempt'] == 'current'])
    dispatch, number, waste_spans = [], 0, {}
    for _stamp, _rid, kind, journal, rows in groups:
        for row in rows:
            number += 1
            d_row = _dispatch_row(row, kind, number, journal['classes'].get(row.get('agent_id')),
                                  journal['state'])
            dispatch.append(d_row)
            if kind == 'instrument' or row['attempt'] != 'current':
                continue
            d_row['outcome'] = outcomes[id(row)]
            d_row['yield'] = _seat_yield(row, overlaps)
            listed = d_row['list'] or 0.0
            yielded = d_row['yield'] or {}
            if d_row['rerun'] == 'rerun':
                d_row['waste'], d_row['waste_list'] = 'rerun', listed
            elif d_row['outcome'] in ('discarded', 'rework'):
                d_row['waste'], d_row['waste_list'] = d_row['outcome'], listed
            elif yielded.get('found') and yielded.get('duplicate'):
                d_row['waste'] = 'duplicate-share'
                d_row['waste_list'] = listed * yielded['duplicate'] / yielded['found']
            if d_row['waste'] and d_row['waste'] != 'duplicate-share':
                d_row['waste_min'] = (d_row['secs'] or 0.0) / 60.0
                waste_spans.setdefault(row.get('run'), []).append(
                    spans.get((row.get('run'), row.get('agent_id')), (None, d_row['secs'] or 0.0)))
    list_groups = dict(orchestrator=o_row['list'], workflow=0.0, sidecar=0.0, instrument=0.0)
    tokens = dict(fresh=o_row['fresh'], cache_read=o_row['cache_read'], cache_write_5m=o_row['cache_write_5m'],
                  cache_write_1h=o_row['cache_write_1h'], cache_write_unsplit=0, output=o_row['output'])
    for d_row in dispatch:
        list_groups[d_row['kind']] += d_row['list'] or 0.0
        t = d_row['tokens']
        tokens['fresh'] += t['fresh'] or 0
        tokens['cache_read'] += t['cache_read'] or 0
        tokens['cache_write_unsplit'] += t['cache_write'] or 0
        tokens['output'] += t['output'] or 0
    total_list = sum(list_groups.values())
    wasted = [d for d in dispatch if d['waste']]
    waste_list = sum(d['waste_list'] for d in wasted)
    total = dict(id='T', kind='total', tokens=tokens, list=dict(list_groups, total=total_list),
                 wall=dict(span=o_row['wall']['span'], split=_wall_split(o_row['wall']['buckets'])),
                 priced_rows=len(dispatch) + 1)
    waste = dict(id='X', kind='waste', rows=[d['id'] for d in wasted], list=waste_list,
                 minutes=sum(_union_seconds(run_spans) for run_spans in waste_spans.values()) / 60.0,
                 share=waste_list / total_list if total_list else 0.0)
    return dict(session=sid, scope=ROLLUP_SCOPE, unit=ROLLUP_UNIT,
                rows=[o_row] + dispatch + [proofs_row(sid, o_row), total, waste])


def report_rollup(doc):
    w('Scope: ' + doc['scope'] + '.')
    w('Unit: ' + doc['unit'] + '.')
    for row in doc['rows']:
        if row['kind'] == 'orchestrator':
            w("O  orchestrator  calls %d  list %.2f  wall %.0fs  state %s"
              % (row['calls'], row['list'], row['wall']['span'], row['state']))
        elif row['id'].startswith('D'):
            w("%-4s %-10s %-34s %-10s %-9s list %s  %s"
              % (row['id'], row['kind'], str(row['label'])[:34], row['attempt'], row['outcome'],
                 'n/a' if row['list'] is None else '%.2f' % row['list'], row['waste'] or ''))
        elif row['id'] == 'P':
            w("P  proofs  %d run(s)  wall %.0fs  queue %.0fs  reused %d  ran %d  foreground test %.0fs  "
              "commits %d (%d over %ds)  state %s"
              % (row['runs'], row['wall_s'], row['queue_s'], row['reused'], row['ran'], row['fg_test_s'],
                 row['commits']['n'], row['commits']['over_budget'], row['commits']['budget_s'], row['state']))
        elif row['id'] == 'T':
            w("T  total list %.2f over %d row(s)" % (row['list']['total'], row['priced_rows']))
        elif row['id'] == 'X':
            w("X  waste list %.2f (%.1f min, %.1f%% of total)" % (row['list'], row['minutes'], 100 * row['share']))


def superseded_line(session, run_id):
    """One line naming a run's superseded and orphan attempts, which the per-run tables never price; None
    when it has none."""
    rows = attempt_rows(session, run_id)
    if not rows:
        return None
    listed = sum(_optional_number(r.get('list_usd')) or 0.0 for r in rows)
    return ('Superseded attempts: %s has %d superseded or orphan attempt(s) outside this table, list %.2f; '
            '`--rollup` prices every attempt.' % (run_id, len(rows), listed))


def report_superseded(session):
    wdir = os.path.join(session, 'workflows')
    try:
        names = sorted(fn[:-5] for fn in os.listdir(wdir) if fn.endswith('.json'))
    except OSError:
        return
    for run_id in names:
        line = superseded_line(session, run_id)
        if line:
            w(line)


def run_rollup(session_arg, json_path):
    session = find_session_dir(session_arg)
    if not session:
        w('Could not locate a session directory.')
        return 1
    doc = build_rollup(session, [])
    report_rollup(doc)
    if json_path:
        _atomic_write_json(json_path, doc)
    return 0


def pricier_pins(rows, data=None, costs=None):
    """Archived dispatches pinned to a row while another row claiming the same tier costs less on the ladder.

    One dict per such dispatch: {label, date, model, tier, cost, cheaper: [aliases, cheapest first]}. A fact for
    review, computed from the record and the registry: a cheaper claimant may not clear the work's `±`, so this
    flags a pin to check and never rules it wrong. A row with no tier, or a model the registry cannot resolve,
    has nothing to compare and is skipped."""
    data = data if data is not None else model_registry.load()
    costs = costs if costs is not None else model_registry.ladder_costs(data)
    out = []
    for row in rows:
        tier = row.get('tier')
        pinned = model_registry.row_for_model(row.get('model'), data) if tier else None
        if not pinned or costs.get(pinned['id']) is None:
            continue
        mine = costs[pinned['id']]
        try:
            serving = model_registry.rows_for_tier(tier, data)
        except Exception:
            continue
        cheaper = [m for m in model_registry.cheapest_first(serving, costs=costs)
                   if m['id'] != pinned['id'] and (costs.get(m['id']) or 0) > mine]
        if cheaper:
            out.append({'label': row.get('label'), 'date': row.get('date'), 'model': pinned.get('alias') or pinned['id'],
                        'tier': tier, 'cost': mine, 'cheaper': [m.get('alias') or m['id'] for m in cheaper]})
    return out


# Tiers whose rows sit close in quality, so a cheaper claimant is usually a real saving. Executor, architect and
# orchestrator rows differ widely in quality, and a cheaper claimant there says nothing without the work's +/- read.
PRICIER_PIN_TIERS = ('fanout', 'scout')


def report_pricier_pins(days, tiers=PRICIER_PIN_TIERS):
    """Print the archive's pricier pins of the last `days` days in `tiers`, grouped by tier and pinned row.

    Reads through `archive_rows`, so a rotated shard (`.jsonl.1`) counts and an ignored run stays out,
    and dates every row by its own run (`row_date`), never by when the row was archived: the archive
    date is a different fact and a twenty-day-old pin re-archived today is not a today pin."""
    since = datetime.now(timezone.utc).date().toordinal() - days
    if not _archive_paths(ARCHIVE):
        w('cannot read the archive: no file at %s' % ARCHIVE)
        return 1
    rows, side, _ = archive_rows()
    keep = []
    for r in rows + side:
        d = row_date(r)
        if not d:
            continue
        try:
            if datetime.strptime(d, '%Y-%m-%d').date().toordinal() >= since:
                keep.append(r)
        except ValueError:
            continue
    rows = [r for r in keep if r.get('tier') in tiers]
    hits = pricier_pins(rows)
    groups = {}
    for h in hits:
        groups.setdefault((h['tier'], h['model'], tuple(h['cheaper'])), []).append(h['label'])
    w('Pricier pins, last %d days, tiers %s: %d of %d dispatches had a cheaper row claiming their tier.'
      % (days, '/'.join(tiers), len(hits), len(rows)))
    w('Each is a pin to check against the ladder +/- cell, not a verdict: the cheaper row may not clear the work.')
    for (tier, model, cheaper), labels in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        w('  %-9s %-8s x%-3d cheaper: %s' % (tier, model, len(labels), ', '.join(cheaper)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rollup', action='store_true',
                    help='price the whole session (orchestrator, dispatches, proofs, waste); never archives')
    ap.add_argument('--json', metavar='PATH', help='with --rollup: also write the machine form here')
    ap.add_argument('--session')
    ap.add_argument('--archive', action='store_true',
                    help="append the session's rows to the archive and prune the pending verdicts they consumed; "
                         'a report form leaves the archive and the verdicts file untouched; '
                         '--ignore-run and --rate write their own record')
    ap.add_argument('--verdicts', help='JSON {label: verdict} or {label: [verdict, effort]}; appends to the archive')
    ap.add_argument('--archive-summary', action='store_true')
    ap.add_argument('--pending', action='store_true',
                    help='print this session\'s unrated dispatches (label debts), never the archive')
    ap.add_argument('--rate', nargs=2, metavar=('LABEL', 'OUTCOME'),
                    help='record one consumed dispatch: run_id:label or a unique bare label, and '
                         'clean|defects|rework|discarded; written under its exact run_id:label key')
    ap.add_argument('--rate-batch', metavar='FILE',
                    help='rate many consumed dispatches in one call: FILE (or - for stdin) is a JSON list of '
                         '{label, outcome, note?, effort?, tier?, shape?, mandates?, probe?, measurement?}; '
                         'each entry follows --rate, a refused entry does not stop the rest. mandates (an object '
                         'or its JSON string) is {mandate: {outcome: findings|clean|not-reached, unique, duplicate}}'
                         ', as --mandates takes it')
    ap.add_argument('--shape', choices=SHAPES,
                    help='with --rate: right, over (a cheaper width/model/effort sufficed), or under (needed more)')
    ap.add_argument('--note', help='with --rate (required for non-clean) or --accept-rest')
    ap.add_argument('--effort', help='with --rate: the effort pin when the record has none')
    ap.add_argument('--tier', help='with --rate: the row\'s role tier (orchestrator|architect|executor|fanout|'
                                   'scout; validation folds to fanout); fills only a row that recorded none')
    ap.add_argument('--mandates', help='with --rate: a panel seat\'s JSON {mandate: {outcome, unique, duplicate}}')
    ap.add_argument('--probe', action='store_true', help='with --rate: a deliberate downgrade comparison')
    ap.add_argument('--measurement', action='store_true', help='with --rate: the dispatch is a comparison arm')
    ap.add_argument('--accept-rest', action='store_true',
                    help='write clean for every remaining pending dispatch you consumed and accepted as-is')
    ap.add_argument('--allow-unresolved-effort', action='store_true',
                    help="archive '?' pins anyway (default: refuse -- they cannot feed Effort Calibration)")
    ap.add_argument('--ignore-run', metavar='RUN_ID',
                    help='permanently exclude a run from collection and summary (synthetic arms, '
                         "un-ratable foreign-session work); writes an 'ignored' sentinel to the archive")
    ap.add_argument('--reason', default='', help='why the run is ignored (stored on the sentinel)')
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
                         'the merge agent on its own row; never archives')
    ap.add_argument('--pricier-pins', type=int, nargs='?', const=30, metavar='DAYS',
                    help='list archived dispatches pinned while a cheaper row claimed their tier (default 30 days); '
                         'a fact for review, never archives')
    a = ap.parse_args()

    if a.pricier_pins is not None:
        return report_pricier_pins(a.pricier_pins)

    if a.rollup:
        return run_rollup(a.session, a.json)

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
        rows, side, ignored_n = archive_rows(diagnostics)
        report_input_diagnostics(diagnostics)
        facts = archive_facts(rows, side)
        report_model_effort(facts['cells'])
        w(f'\n{len(rows)} archived agents across {facts["runs"]} runs.'
          + (f' ({ignored_n} ignored-run sentinel(s) excluded.)' if ignored_n else ''))
        if facts['sampled_n']:
            w(f'{facts["sampled_n"]} unrated row(s) were sampled out (saturated lens), not forgotten.')
        by_session = {}
        for r in rows + side:
            by_session.setdefault(r.get('session_id') or r.get('run'), []).append(r)
        why = {}
        for r in rows + side:
            if outcome_of(r) != 'unrated':
                continue
            reason = unrated_reason(r, by_session.get(r.get('session_id') or r.get('run'), []))
            if reason and reason != 'sampled-out':
                why[reason] = why.get(reason, 0) + 1
        if why:
            w('unrated rows by why (read at collection, never stored): '
              + ', '.join('%s %d' % (name, why[name]) for name in UNRATED_REASONS if name in why))
        totals = facts['totals']
        w('\n-- Task totals (tools/task_record.py joins; quota cost and USD are separate columns) --')
        w(f"{'task':<36} {'rows':>5} {'qcost':>10} {'USD':>9} {'no-cost':>7}")
        for tid, t in sorted(totals['tasks'].items()):
            w(f"{tid[:36]:<36} {t['rows']:>5} {fmt(t['qcost']):>10} {t['cost_usd']:>9.2f} {t['rows_without_cost']:>7}")
        w(f"rows without a task: {totals['rows_without_task']} of {totals['rows']}; "
          f"rows without any cost: {totals['rows_without_cost']} of {totals['rows']}")
        model_tier_readout.report(rows + side, run_sessions=_session_of_run(), outcome_of=outcome_of, write=w)
        report_seat_overlap(review_signals(rows, _session_of_run()))
        report_panel_yield(rows + side)
        _report_candidates(rows, facts['candidates'])
        return 0

    if a.rate or a.accept_rest or a.rate_batch:
        session = find_session_dir(a.session)
        if not session:
            w('Could not locate a session directory with workflow runs.')
            return 1
        if a.accept_rest:
            return accept_rest(session, note=a.note)
        if a.rate_batch:
            requests, refusal = load_rating_batch(a.rate_batch)
            if refusal:
                w(refusal)
                return 1
            return rate_batch(session, requests)
        return rate(session, a.rate[0], a.rate[1], note=a.note, effort=a.effort, tier=a.tier,
                    mandates=a.mandates, probe=a.probe, shape=a.shape, measurement=a.measurement)

    if a.pending:
        session = find_session_dir(a.session)
        if not session:
            w('Could not locate a session directory with workflow runs.')
            return 1
        pending_diagnostics = []
        detail = pending_detail(session, diagnostics=pending_diagnostics)
        items, ambiguous, unmatched = detail['remaining'], detail['ambiguous'], detail['unmatched']
        unknown = detail['sidecar_error']
        w((f'unrated dispatches: unknown ({len(items)} listed; the sidecar census failed: {unknown})'
           if unknown else f'unrated dispatches: {len(items)}')
          + (f" (+{len(detail['sampled_out'])} sampled out: saturated lens)" if detail['sampled_out'] else ''))
        for rid, lab in items[:10]:
            w(f'  {rid}:{lab}')
        for line in pending_file_warning(session):
            w(line)
        for lab in ambiguous:
            w(f'  ambiguous bare verdict "{lab}" -- matches multiple unarchived runs; '
              f'rate with run_id:label instead')
        for lab in unmatched[:10]:
            w(f'  unmatched bare verdict "{lab}" -- every run of this session carrying that '
              f'label was archived before the verdict was recorded')
        sidecar_rows = detail['sidecar']
        report_input_diagnostics(pending_diagnostics)
        sidecar_labels = {row.get('label') for row in sidecar_rows if row.get('label')}
        for key, spelling in misshaped_verdict_keys(session, sidecar_labels):
            w(f'  misshaped verdict key "{key}" -- rates nothing; write it as "{spelling}"')
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
                + collect_sidecar(SIDECAR_LEDGER, include_unlabeled=True,
                                  diagnostics=input_diagnostics))
        rows = _finalize_rows(rows, input_diagnostics)
        w('Scope: delegated dispatch only (Workflow agents across every session in this '
          'project, plus the global sidecar ledger). NOT the account\'s global usage page. '
          'Workflow cost is normalized/plan-quota; sidecar cost is real USD -- never summed.')
        attach_mandate_evidence(rows)
        overlaps = review_signals(rows, _session_of_run())
        report(rows, input_diagnostics)
        report_seat_overlap(overlaps)
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
    if a.sidecar_all:
        sidecar_rows = collect_sidecar(SIDECAR_LEDGER, include_unlabeled=True,
                                       diagnostics=input_diagnostics)
    else:
        sidecar_rows = collect_session_sidecar(session, input_diagnostics)
    rows = collect(session, input_diagnostics) + sidecar_rows
    rows = _finalize_rows(rows, input_diagnostics)
    overlaps = session_seat_overlaps(session)
    attach_seat_overlap(rows, overlaps)
    attach_review_ledger(rows, load_review_ledger())
    history = [r for r in _archive_records() if not r.get('ignored')]
    measured = [r for r in rows if r.get('instrument') or r.get('workflow') in MEASUREMENT_WORKFLOWS]
    if measured:
        rows = [r for r in rows if not (r.get('instrument') or r.get('workflow') in MEASUREMENT_WORKFLOWS)]
        spend, _known, unknown = _field_stats(measured, 'cost')
        w(f"(excluding {len(measured)} measurement agent(s), benchmark instruments and probes: "
          f"{', '.join(sorted({r['workflow'] for r in measured}))}; their spend {fmt(spend)} normalized"
          f"{f', {unknown} unpriced' if unknown else ''})")
    if not rows:
        report(rows, input_diagnostics)
        report_seat_overlap(overlaps)
        report_panel_yield([], history)
        report_superseded(session)
        return 0
    attach_mandate_evidence(rows)
    if a.sidecar_all:
        report(rows, input_diagnostics, history)
        report_seat_overlap(overlaps)
        report_review_join(rows)
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
            selected_key, ent = _select_verdict(r, v, label_counts)
            if selected_key in pending_root:
                consumed_pending.setdefault(r['run'], {}).setdefault('root', set()).add(selected_key)
            elif selected_key in pending_legacy:
                consumed_pending.setdefault(r['run'], {}).setdefault('legacy', set()).add(selected_key)
            bad_tier = apply_verdict(r, ent)
            if bad_tier is not None:
                w(f"REJECTED unknown tier '{bad_tier}' for {r['label']} -- the row keeps no tier; "
                  f'allowed: {", ".join(model_registry.TIER_ORDER)} (validation folds to fanout)')
            if r['outcome'] not in OUTCOMES + ('unrated',):
                # Non-fatal, per the command doc: a malformed verdict leaves ITS row unrated and the
                # archive proceeds; aborting here threw away every other rated row in the session.
                w(f"REJECTED unknown outcome '{r['outcome']}' for {r['label']} -- archived as unrated; "
                  f'allowed: {", ".join(OUTCOMES)} (legacy verdict words map through)')
                r['outcome'] = 'unrated'
    saturated = saturated_groups(_archive_records())
    for r in rows:
        r.setdefault('outcome', 'unrated')
        if r['outcome'] == 'unrated' and r.get('source') != 'sidecar' and sampled_out(r, saturated):
            r['sampled_out'] = True

    report(rows, input_diagnostics, history)
    report_seat_overlap(overlaps)
    report_review_join(rows)
    report_superseded(session)
    if not (a.archive or a.verdicts):
        return 0

    # `no-record` is `_bodyless_sidecar_row`'s state, built only for a run that already ended.
    nonterminal = [row for row in rows
                   if _manifest_status(row.get('state')) not in ('completed', 'failed', 'no-record')]
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
