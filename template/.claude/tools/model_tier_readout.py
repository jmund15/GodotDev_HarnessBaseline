#!/usr/bin/env python3
"""Model x role-tier readout over archived agent rows.

One cell per (served registry row, role tier). A cell counts the rated rows launched at that tier and
prints a verdict from the counts alone; a verdict may propose a `roles` edit for the registry owner
(`external_models.json`, applied through `/ladder_ingest`). Every archived row lands in exactly one
bucket, so the buckets reconcile to the row total.

Each cell also carries `spec` sub-counts (n and falsified per tight, judged, unrecorded) for the `spec`
its launch recorded. The sub-counts never split a cell: a second key would halve the sample under
`MIN_ROWS`, and the verdict is about the model at the tier, not about who wrote the brief.

Pure functions over row dicts: no archive, session or roster is read here except the registry.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_registry  # noqa: E402

OUTCOMES = ('clean', 'defects', 'rework', 'discarded')
MIN_ROWS = 8
MIN_SESSIONS = 3
CONTRADICT_MIN_FALSIFIED = 2
CONTRADICT_MIN_SHARE = 0.20
FALSIFYING = ('defects', 'rework')
CONSTANTS = {'MIN_ROWS': MIN_ROWS, 'MIN_SESSIONS': MIN_SESSIONS, 'CONTRADICT_MIN_FALSIFIED': CONTRADICT_MIN_FALSIFIED,
             'CONTRADICT_MIN_SHARE': CONTRADICT_MIN_SHARE}
SYNTHETIC = '<synthetic>'
MODEL_FAMILIES = ('opus', 'sonnet', 'haiku', 'fable')
SPEC_NOTE = 'audit the spec, not the model'
# The pin basis a launch records beside its model and effort (`orchestration` §5): `pick` says why the
# row was pinned and `spec` whether the slice's test intent is clearly defined. The same two enums gate
# the engines' job fields and the sidecar's `-Y`. One owner: the ladder readout's per-cell sub-counts
# and the `pins.spec-unrecorded.all` metric both bucket through `spec_state`.
PICK_KINDS = ('cell', 'room', 'owner', 'default')
SPEC_KINDS = ('tight', 'judged')
SPEC_BUCKETS = SPEC_KINDS + ('unrecorded',)
BUCKETS = ('unrated', 'sampled_out', 'no_outcome', 'outcome_unparseable', 'tier_absent', 'tier_unparseable',
           'requested_only', 'mixed', 'measurement_off_role', 'members')
FOOTER = (('unrated', 'unrated'), ('no_outcome', 'no outcome'), ('outcome_unparseable', 'outcome unparseable'),
          ('tier_absent', 'tier absent'), ('tier_unparseable', 'tier unparseable'),
          ('requested_only', 'requested-only'), ('mixed', 'mixed'),
          ('measurement_off_role', 'off-role measurement'), ('members', 'cell members'))
OWNER = 'external_models.json roles (apply through /ladder_ingest)'


def bare_model(model):
    """The id a row groups on: a client context-window suffix (`[1m]`) is a request modifier, not part of it.
    A row that names no model is `?`. The one owner of this rule: archived rows, new rows and pin checks use it."""
    return re.sub(r'\[[^\]]*\]$', '', str(model or '')) or '?'


def bare_effort(effort):
    """The effort a value names: the PINS line once wrote `model/effort`, and the effort is the part after `/`."""
    text = str(effort)
    return text.split('/', 1)[1] if '/' in text else text


def _strict_outcome(row):
    return row.get('outcome') or '?'


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _session_name(path):
    return str(path).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]


def _served(row):
    """('id', bare id) | ('requested_only', None) | ('mixed', None) from the attested model field."""
    raw = row.get('model') if row.get('source') == 'sidecar' else row.get('served_model')
    text = str(raw or '').strip()
    if text.startswith('mixed:'):
        text = text[len('mixed:'):]
    ids = {token.strip() for token in text.split(',') if token.strip() and token.strip() != SYNTHETIC}
    if not ids:
        return 'requested_only', None
    if len(ids) > 1:
        return 'mixed', None
    return 'id', next(iter(ids))


def model_identity(row, data=None):
    """(name, registry row or None): the model a row groups on. The model it records as served wins, else its
    requested pin; either resolves to the registry's canonical id through its id, served-id and alias data, so a
    dated and an undated spelling of one model share a name. A bare family name with no served id cannot name a
    version and reads `<family> (version unrecorded)`; an absent model is `?`; an unregistered id names itself.
    The one owner of this rule: the agent cells, the floor cells and the ladder readout all group on it."""
    if data is None:
        try:
            data = model_registry.load()
        except model_registry.RegistryError:
            data = {'models': []}
    kind, served = _served(row)
    transport = row.get('transport')
    if kind == 'id':
        name = bare_model(served)
        entry = model_registry.row_by_id(name, data, transport=transport)
        return (entry['id'] if entry else name), entry
    pin = bare_model(row.get('model'))
    if pin == '?':
        return '?', None
    entry = model_registry.row_by_id(pin, data, transport=transport)
    family = model_registry.family_named(pin, data)
    if entry is None and family is None:
        entry = model_registry.row_for_alias(pin, data)
    if entry:
        return entry['id'], entry
    return ('%s (version unrecorded)' % family if family else pin), None


def pin_differs(row, served):
    """True when a Workflow row's requested pin is not honored by the served id. A pin naming a family
    (`sonnet`) is honored by any id carrying that token; a full-id pin must match exactly once a trailing
    `[...]` context suffix is dropped. A sidecar row, and a pin that is empty or `?`, never differ."""
    pin = bare_model(row.get('model'))
    if row.get('source') == 'sidecar' or pin == '?':
        return False
    return pin not in served.split('-') if pin in MODEL_FAMILIES else pin != served


def _tier_state(value):
    """(state, canonical tier): 'absent' | 'unparseable' | 'ok'."""
    if value is None or str(value).strip() == '':
        return 'absent', None
    try:
        return 'ok', model_registry.canonical_tier(str(value))
    except model_registry.RegistryError:
        return 'unparseable', None


def spec_state(value):
    """One of SPEC_BUCKETS for a row's recorded `spec`. Absent, blank and a value no enum names all read
    `unrecorded`: a launch that refused an illegal value leaves the field absent, so anything else is a
    hand-written row the readout must count against the recording, never launder into a bucket."""
    text = '' if value is None else str(value).strip()
    return text if text in SPEC_KINDS else 'unrecorded'


def _new_cell(model, tier, entry):
    return {'model': model, 'label': entry.get('label') or model if entry else model, 'tier': tier,
            'registered': entry is not None, 'claimed': bool(entry) and tier in model_registry.entry_tiers(entry),
            'falsified': [], 'n': 0, 'sessions': 0,
            'spec': {kind: {'n': 0, 'falsified': 0} for kind in SPEC_BUCKETS},
            'clean': 0, 'defects': 0, 'rework': 0, 'discarded': 0, 'mismatch': 0, 'qcost': 0.0, 'qcost_n': 0, 'usd': 0.0,
            'usd_n': 0, 'verdict': '', 'proposal': [], 'notes': [], '_entry': entry, '_sessions': set(),
            '_failed_efforts': set()}


def _roles_line(entry, tier, add):
    current = list(entry.get('roles') or [])
    wanted = {t for t in current if t in model_registry.TIER_ORDER}
    (wanted.add if add else wanted.discard)(tier)
    ordered = [t for t in model_registry.TIER_ORDER if t in wanted] + [t for t in current if t not in model_registry.TIER_ORDER]
    return f'"roles": {json.dumps(ordered)}'


def _judge(cell, entry):
    failed = cell['defects'] + cell['rework']
    if cell['discarded'] >= 2 and cell['n'] >= MIN_ROWS:
        cell['notes'].append(SPEC_NOTE)
    if cell['n'] < MIN_ROWS or cell['sessions'] < MIN_SESSIONS:
        cell['verdict'] = 'insufficient'
        return
    if entry is None:
        cell['verdict'] = 'unproven'
        return
    claimed = cell['tier'] in model_registry.entry_tiers(entry)
    model, tier = cell['model'], cell['tier']
    if not claimed and tier in model_registry.supplementary_tiers(entry):
        cell['verdict'] = 'unproven'
        cell['notes'].append(f'{tier} is a supplementary tier for {model}: a review seat beside a qualified seat, '
                             f'so no plain claim is proposed ({OWNER})')
        return
    if claimed:
        if failed < CONTRADICT_MIN_FALSIFIED or failed < CONTRADICT_MIN_SHARE * cell['n']:
            cell['verdict'] = 'holds'
            return
        cell['verdict'] = 'contradicts'
        efforts = ', '.join(sorted(cell['_failed_efforts'])) or 'unrecorded'
        cell['proposal'] = [
            f'ADVISORY: {model} at {tier} has falsified rows that ran at effort {efforts}; raise the pinned effort '
            f'and check the Agents tab before touching roles ({OWNER}).',
            f'ADVISORY: if a higher effort still falsifies, drop {tier} from {model}: {_roles_line(entry, tier, False)} '
            f'in {OWNER}.']
    elif failed == 0 and cell['discarded'] == 0:
        cell['verdict'] = 'candidate'
        cell['proposal'] = [
            f'ADVISORY: {model} has clean rated rows at {tier} without claiming it; consider '
            f'{_roles_line(entry, tier, True)} in {OWNER}.']
    else:
        cell['verdict'] = 'unproven'


def _unavailable(rows, exc):
    return {'cells': [], 'buckets': dict.fromkeys(BUCKETS, 0), 'total': len(rows), 'constants': dict(CONSTANTS),
            'degraded': str(exc)}


def cells(rows, *, run_sessions, registry=None, outcome_of=None):
    """The readout: {cells, buckets, total, constants}. An unreadable registry returns no cell and a
    `degraded` line naming it, so a caller keeps its other output."""
    try:
        data = registry if registry is not None else model_registry.load()
    except model_registry.RegistryError as exc:
        return _unavailable(rows, exc)
    read = outcome_of or _strict_outcome
    buckets = dict.fromkeys(BUCKETS, 0)
    table = {}
    for row in rows:
        outcome = read(row)
        if row.get('sampled_out') or outcome == 'unrated':
            buckets['unrated'] += 1
            buckets['sampled_out'] += 1 if row.get('sampled_out') else 0
            continue
        if outcome == '?':
            buckets['no_outcome'] += 1
            continue
        if outcome not in OUTCOMES:
            buckets['outcome_unparseable'] += 1
            continue
        state, tier = _tier_state(row.get('tier'))
        if state != 'ok':
            buckets['tier_absent' if state == 'absent' else 'tier_unparseable'] += 1
            continue
        kind, served = _served(row)
        if kind != 'id':
            buckets['requested_only' if kind == 'requested_only' else 'mixed'] += 1
            continue
        model, entry = model_identity(row, data)
        if row.get('measurement') and (entry is None or tier not in (entry.get('roles') or [])):
            buckets['measurement_off_role'] += 1
            continue
        cell = table.setdefault((model, tier), _new_cell(model, tier, entry))
        buckets['members'] += 1
        cell['n'] += 1
        cell[outcome] += 1
        # The launch's `spec` sub-counts stay INSIDE the cell: splitting a cell by spec would halve
        # its sample under MIN_ROWS, so the sub-counts are read beside the verdict, never as a verdict.
        spec = cell['spec'][spec_state(row.get('spec'))]
        spec['n'] += 1
        spec['falsified'] += outcome in FALSIFYING
        cell['mismatch'] += pin_differs(row, served)
        session = row.get('session_id') or (_session_name(run_sessions[row.get('run')]) if row.get('run') in run_sessions else '')
        if session:
            cell['_sessions'].add(session)
        if outcome in FALSIFYING:
            if len(cell['falsified']) < 3:
                cell['falsified'].append(f"{row.get('run') or '?'}:{row.get('label') or '?'}")
            cell['_failed_efforts'].add(str(row.get('requested_effort') or row.get('effort') or 'unrecorded'))
        if _is_number(row.get('qcost')):
            cell['qcost'] += float(row['qcost'])
            cell['qcost_n'] += 1
        if _is_number(row.get('cost_usd')):
            cell['usd'] += float(row['cost_usd'])
            cell['usd_n'] += 1
    order = model_registry.TIER_ORDER
    out = []
    for (model, tier), cell in sorted(table.items(), key=lambda item: (order.index(item[0][1]), item[0][0])):
        cell['sessions'] = len(cell.pop('_sessions'))
        _judge(cell, cell.pop('_entry'))
        cell.pop('_failed_efforts')
        out.append(cell)
    return {'cells': out, 'buckets': buckets, 'total': len(rows), 'constants': dict(CONSTANTS)}


def _average(total, priced):
    return f'{total / priced:,.2f}' if priced else 'n/a'


def report(rows, *, run_sessions, registry=None, outcome_of=None, write=print):
    result = cells(rows, run_sessions=run_sessions, registry=registry, outcome_of=outcome_of)
    write('')
    write('-- Model x role tier (rated rows by served registry row and the tier recorded at launch; '
          'counts only) --')
    if result.get('degraded'):
        write('readout unavailable: ' + result['degraded'])
        return result
    table = result['cells']
    if table:
        width = max(len(c['model']) for c in table + [{'model': 'model'}])
        write(f"{'model':<{width}} {'tier':<12} {'n':>4} {'sess':>4} {'clean':>5} {'defect':>6} {'rework':>6} "
              f"{'discard':>7} {'mism':>4} {'spec t/j/u':>10} {'verdict':<12} {'qcost/agent':>12} {'USD/agent':>10}")
        for c in table:
            qcost = f"{_average(c['qcost'], c['qcost_n'])} ({c['qcost_n']})"
            usd = f"{_average(c['usd'], c['usd_n'])} ({c['usd_n']})"
            spec = '/'.join(str(c['spec'][kind]['n']) for kind in SPEC_BUCKETS)
            write(f"{c['model']:<{width}} {c['tier']:<12} {c['n']:>4} {c['sessions']:>4} {c['clean']:>5} "
                  f"{c['defects']:>6} {c['rework']:>6} {c['discarded']:>7} {c['mismatch']:>4} {spec:>10} {c['verdict']:<12} {qcost:>12} {usd:>10}")
    else:
        write('no cell yet: no rated row carries both a role tier and a served model.')
    b = result['buckets']
    parts = []
    for key, name in FOOTER:
        text = f'{name} {b[key]}'
        if key == 'unrated':
            text += f" (of which sampled-out {b['sampled_out']})"
        parts.append(text)
    write('buckets: ' + ', '.join(parts) + f"; total {result['total']}")
    write('verdict constants: ' + ', '.join(f'{k} {v}' for k, v in result['constants'].items()))
    for c in table:
        if c['verdict'] == 'contradicts':
            write(f"evidence: {c['model']} at {c['tier']}: falsified {c['defects'] + c['rework']} of {c['n']}; "
                  + ', '.join(c['falsified']))
        for note in c['notes']:
            write(f"note: {c['model']} at {c['tier']}: {note}")
        for line in c['proposal']:
            write(line)
    return result
