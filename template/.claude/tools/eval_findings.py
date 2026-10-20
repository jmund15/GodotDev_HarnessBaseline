#!/usr/bin/env python3
"""Deterministic keyword findings; counts are not recurrences or causal claims.

python3 .claude/tools/eval_findings.py [--root DIR] [--since D] [--until D] [--json FILE]
"""
import argparse
import datetime
import json
import os
import re
import sys
import subprocess
from collections import Counter

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hooks')
sys.path.insert(0, HOOKS)
import self_eval_archive_guard
import analyze_eval_archive
import autolearn_outcomes
import orchestration_metrics
import self_eval_archive_store
import eval_history
import model_tier_readout
import guard_denials

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRICTION = {
    'recursive-grep': [r'recursive.?grep|grep -r|unbounded.{0,20}(grep|scan)'],
    'shell-escape': [r'heredoc.{0,40}(escape|backslash|quot|mangl|collaps|broke|body|stdin|parsing|EOF|traceback|spun|executed|arriv|\\\\)|backslash|nested.?quote|backtick|\\\\'],
    'index-write': [r'add.?\+?.?commit|(private|temporary|temp|stale|shared).?(git )?index|index write|index\.lock|stage\W{0,3}(then|and|\+)\W{0,3}commit'],
    'commit-stamp': [r'stamp|lock row|number[- ]row|unit.?size|peer.?revert|rule.?number|(commit|push)[ -]?(time )?guard|triage|(blocked|refused) (a |an |the )?(\w+ ){0,2}commit\b'],
    'sleep-wait': [r'sleep|foreground wait|blocking.{0,20}wait|wait[- ]loop|wait guard|waits? denied'],
    'kill-denied': [r'taskkill|stop-process|taskstop|\bkill(ed|s|ing)?\b|survived|\bstop\w* .{0,30}\b(task|process(es)?)\b'],
    'classifier-denied': [r'classifier|auto.?mode'],
}
# `/self_evaluate` §5d: an accepted friction row names the class that explains it. A row that
# predates the class is counted as coverage, never dropped and never read as a zero.
ACCEPTED_CLASSES = ('guard-working', 'outside', 'owner-held', 'one-off')
ACCEPTED_CLASS_RE = re.compile(r'^accepted\s*:\s*(%s)\s*:' % '|'.join(ACCEPTED_CLASSES), re.I)

# A rule named in this many sessions of the window becomes a `rule.<slug>` finding (S5).
RULE_FINDING_MIN_SESSIONS = 2


def load(root):
    """Read effective session rows, rotated agents and the outcome ledger once."""
    root = os.path.abspath(root)
    archive = os.path.join(root, '.claude', 'self_evaluate_archive.json')
    sessions = self_eval_archive_store.effective_entries(archive) if os.path.isfile(archive) else []
    diagnostics = []
    path = os.path.join(root, '.claude', 'orchestration_metrics.jsonl')
    rows, side, ignored = orchestration_metrics.archive_rows(diagnostics, archive=path)
    ledger_path = os.path.join(root, '.claude', 'autolearn_outcomes.jsonl')
    ledger, bad = autolearn_outcomes.read_rows(ledger_path)
    first = {}
    for unit, records, date_of in [('sessions', sessions, lambda r: analyze_eval_archive.calendar_day(r.get('date'))),
                                  ('agents', rows + side, orchestration_metrics.row_date)]:
        days = [date_of(r) for r in records if date_of(r)]
        first[unit] = min(days) if days else None
    denials = guard_denials.scan(guard_denials.project_roots(root),
                                 cache_path=os.path.join(root, '.claude', 'logs', 'guard_denials_cache.json'))
    return {'root': root, 'sessions': sessions, 'agents': rows + side, 'ledger': ledger, 'first': first, 'denials': denials,
            'diagnostics': {'sessions': {'absent': int(not os.path.isfile(archive))},
                            'agents': {'absent': int(not os.path.isfile(path)), 'unparseable': len(diagnostics),
                                       'ignored': ignored},
                            'ledger': {'absent': int(not os.path.isfile(ledger_path)), 'unparseable': bad, 'rows': len(ledger),
                                       'first': min((r['date'] for r in ledger if isinstance(r.get('date'), str)), default=None),
                                       'last': max((r['date'] for r in ledger if isinstance(r.get('date'), str)), default=None)},
                            'denials': denials['coverage']}}


def _day(value):
    if value is None:
        return None
    day = analyze_eval_archive.calendar_day(value)
    if day != value:
        raise ValueError('expected a real date YYYY-MM-DD: %r' % value)
    return day


def _window(src, unit, since, until):
    date_of = (lambda r: analyze_eval_archive.calendar_day(r.get('date'))) if unit == 'sessions' else orchestration_metrics.row_date
    return [r for r in src[unit] if (day := date_of(r)) and (since is None or day >= since)
            and (until is None or day <= until)]


def _matches(text, table):
    return [name for name, patterns in table.items() if any(re.search(p, text, re.I) for p in patterns)]


def _example(row, unit):
    return row.get('session_id') or str(row.get('id', '?')) if unit == 'sessions' else {'run': row.get('run'), 'label': row.get('label')}


def _add(counts, examples, key, row, unit):
    counts[key] += 1
    example = _example(row, unit)
    bucket = examples.setdefault(key, [])
    if len(bucket) < 3 and example not in bucket:
        bucket.append(example)


def _friction(src, since, until):
    rows = _window(src, 'sessions', since, until)
    counts = Counter(dict.fromkeys(
        ['friction.%s.sessions' % cat for cat in FRICTION]
        + ['friction.accepted.rows']
        + ['friction.accepted.%s.rows' % cls for cls in ACCEPTED_CLASSES], 0))
    examples, coverage = {}, Counter()
    unknown, strings, total, carrying = 0, 0, 0, 0
    for row in rows:
        seen = set()
        if 'friction' not in row:
            coverage['absent'] += 1
            continue
        items = row['friction']
        if not isinstance(items, list):
            coverage['unparseable'] += 1
            continue
        carrying += 1
        if not items:
            coverage['unattempted'] += 1
        for item in items:
            total += 1
            if not isinstance(item, dict):
                coverage['unparseable'] += 1
                continue
            if 'what' not in item:
                coverage['what-absent'] += 1
            elif item['what'] is None:
                coverage['what-unattempted'] += 1
            elif not isinstance(item['what'], str):
                coverage['what-unparseable'] += 1
            else:
                strings += 1
                cats = _matches(item['what'], FRICTION)
                unknown += not cats
                seen.update(cats)
            disposition = item.get('disposition')
            if disposition is None:
                coverage['disposition-absent'] += 1
            elif not isinstance(disposition, str) or not re.match(r'^(accepted|fixed|harness|worklog)\b', disposition, re.I):
                coverage['disposition-unparseable'] += 1
            elif disposition.lower().startswith('accepted'):
                _add(counts, examples, 'friction.accepted.rows', row, 'sessions')
                class_match = ACCEPTED_CLASS_RE.match(disposition)
                if class_match:
                    _add(counts, examples, 'friction.accepted.%s.rows' % class_match.group(1).lower(),
                         row, 'sessions')
                else:
                    coverage['accepted-classless'] += 1
        for cat in seen:
            _add(counts, examples, 'friction.%s.sessions' % cat, row, 'sessions')
    metrics = {key: (n, total if key.startswith('friction.accepted') else carrying)
               for key, n in counts.items()}
    return metrics, examples, {'num': unknown, 'den': strings}, dict(coverage)


def _session_state(row):
    if 'outcome' not in row:
        return 'absent'
    value = row['outcome']
    if value is None:
        return 'unattempted'
    return value if isinstance(value, str) and value in analyze_eval_archive.OUTCOMES else 'unparseable'


def _owner_prompts(row):
    """The store-derived owner-prompt denominator, or None when the row carries no usable
    `facts.owner_prompts` (absent, an `unavailable` reason, or a value the store would not write).
    None is typed absent, never a zero-prompt session."""
    facts = row.get('facts')
    value = facts.get('owner_prompts') if isinstance(facts, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _outcome(src, since, until):
    rows = _window(src, 'sessions', since, until)
    counts = Counter(dict.fromkeys(('outcome.failure', 'outcome.correction', 'outcome.cause-e'), 0))
    examples, coverage = {}, Counter()
    known = 0
    corrections_n, prompts, rate_sessions = 0, 0, 0
    for row in rows:
        # The rate's population is the rows carrying both halves of it; a row missing either is typed
        # absent so a backfilled denominator cannot pair with a numerator it never counted.
        row_prompts = _owner_prompts(row)
        if row_prompts is None:
            coverage['prompts-absent'] += 1
        if not isinstance(row.get('corrections'), list):
            coverage['corrections-absent'] += 1
        if row_prompts is not None and isinstance(row.get('corrections'), list):
            rate_sessions += 1
            prompts += row_prompts
            corrections_n += len(row['corrections'])
        state = _session_state(row)
        if state in ('absent', 'unparseable', 'unattempted'):
            coverage[state] += 1
            continue
        known += 1
        if state != 'clean':
            _add(counts, examples, 'outcome.' + state, row, 'sessions')
        if row.get('pattern') == 'E':
            _add(counts, examples, 'outcome.cause-e', row, 'sessions')
    metrics = {key: (n, known) for key, n in counts.items()}
    # (num, den, units): the rate divides by prompts but its floor counts the sessions that carry them.
    metrics['outcome.corrections-per-prompt'] = (corrections_n, prompts, rate_sessions)
    return metrics, examples, {'num': len(rows) - known, 'den': len(rows)}, dict(coverage)


DEFECT_CLASSES = {
    'partial-coverage': [r'(?<!->)\bpartial|unread|read part|not opened|self-reported gap',
                         r'missed(?! nothing)|skipped (the|its|a|an|some|every|all)\b|did not (raise|read|open|cover|report|flag)',
                         r'\bended\b|\bexited\b|\bquit\b|no report|\bwaiting\b',
                         r'\bowed\b|undone',
                         r'left [^;]{0,40}\bopen\b|left [^;.]{0,30}\bto (the )?parent\b'],
    'red-proofs': [r'not RED-first|RED-first[^;]{0,25}\b(gaps?|missing|owed|skipped|omitted|absent)\b|\bno\b[^;]{0,30}\bRED\b|\bRED\b[^;]{0,15}\b(owed|missing|partly|after)\b|after (the )?implementation|written after',
                   r'\b(left|stayed|still|remained)\b[^;]{0,40}\bred\b',
                   r'(broken|failing) (\w+ ){0,2}(tests?|proofs?|suites?)|shipped (red|failing)|(test|proof|suite|check|closure|gate)[\w-]* red\b|(?-i:\breds\b)|\bbroke\w* [^;]{0,30}(tests?|fixtures?|proofs?|suites?)|(check|verify)[^;]{0,25}\bred\b|\bno (tracked |new |unit |regression |production )?(proofs?|tests?|suites?|cases?)\b'],
    'parent-spec': [r"parent'?s (error|fault|mistake|defect|brief|spec|plan)|defect (was|is) (the )?parent|\bparent (over|under|mis)[\w-]*",
                    r'\b(brief|spec|plan|template)\b[^;.]{0,40}\b(gap|omitted|missing|listed)\b|\bmy (brief|spec|plan|template)\b'],
}


def _agent_state(row):
    try:
        bucket = orchestration_metrics.coverage_of(row)
    except (TypeError, AttributeError):
        return 'unparseable'
    return 'unattempted' if bucket == 'sampled_out' else bucket


def _agents(src, since, until):
    rows = _window(src, 'agents', since, until)
    coverage, rated, models = Counter(), [], Counter()
    counts = Counter(dict.fromkeys(['agents.%s.%s' % (cat, model) for cat in DEFECT_CLASSES
                                    for model in ['all']] + ['agents.unrated.all', 'agents.tier-absent.all'], 0))
    examples = {}
    for row in rows:
        state = _agent_state(row)
        coverage[state] += 1
        if 'note' not in row or row['note'] is None:
            coverage['note-absent'] += 1
        elif not isinstance(row['note'], str):
            coverage['note-unparseable'] += 1
        elif not row['note']:
            coverage['note-unattempted'] += 1
        if state in ('unrated', 'unattempted'):
            # `unattempted` is `_agent_state`'s name for a sampled-out row; the Agents tab reads both as
            # unrated, so the metric does too.
            _add(counts, examples, 'agents.unrated.all', row, 'agents')
        if state == 'rated':
            identity = model_tier_readout.model_identity(row)[0]
            slug = re.sub(r'[^a-z0-9.]+', '-', identity.lower()).strip('-')
            rated.append((row, slug))
            models[slug] += 1
            if model_tier_readout._tier_state(row.get('tier'))[0] == 'absent':
                _add(counts, examples, 'agents.tier-absent.all', row, 'agents')
    keyed = {model: n for model, n in models.items() if model}
    for cat in DEFECT_CLASSES:
        for model in sorted(keyed):
            counts['agents.%s.%s' % (cat, model)] = 0
    unknown, strings = 0, 0
    for row, model in rated:
        if orchestration_metrics.outcome_of(row) not in orchestration_metrics.FALSIFYING or not isinstance(row.get('note'), str):
            continue
        strings += 1
        cats = _matches(row['note'], DEFECT_CLASSES)
        unknown += not cats
        for cat in cats:
            _add(counts, examples, 'agents.%s.all' % cat, row, 'agents')
            if model in keyed:
                _add(counts, examples, 'agents.%s.%s' % (cat, model), row, 'agents')
    metrics = {key: (n, len(rated) if key.endswith('.all') else keyed[key.split('.', 2)[2]])
               for key, n in counts.items()}
    # `agents.unrated.all` divides by every archived row, rated or not: the share nobody rated.
    metrics['agents.unrated.all'] = (metrics['agents.unrated.all'][0], len(rows))
    return metrics, examples, {'num': unknown, 'den': strings}, dict(coverage)


def _pins(src, since, until):
    """Archived dispatches whose launch recorded no `spec` (`orchestration` §5), over every archived row
    of the window. `spec` is the slice's test intent — clearly defined or ambiguous — and the dispatcher
    is the only one who can state it at launch: after compaction the reason is gone. A row with a legal
    value is not counted; an absent, blank or illegal one is, so a window from before the field existed
    reads as a baseline of absence, never as a zero that looks healthy."""
    rows = _window(src, 'agents', since, until)
    counts = Counter({'pins.spec-unrecorded.all': 0})
    examples = {}
    for row in rows:
        if model_tier_readout.spec_state(row.get('spec')) == 'unrecorded':
            _add(counts, examples, 'pins.spec-unrecorded.all', row, 'agents')
    return {key: (n, len(rows)) for key, n in counts.items()}, examples, {'num': 0, 'den': 0}, {}


def _cascade_rows(src, since, until):
    return [r for r in _window(src, 'sessions', since, until)
            if r.get('pattern') == 'E' or _session_state(r) == 'failure']


def _fix_status(src, row):
    cache = src.setdefault('_fix_status', {})
    if id(row) not in cache:
        cache[id(row)] = _read_fix_status(src, row)
    return cache[id(row)]


def _read_fix_status(src, row):
    friction = row.get('friction')
    dispositions = [r.get('disposition') for r in friction if isinstance(r, dict)] if isinstance(friction, list) else []
    fields = dispositions + [row.get('notes'), row.get('key_takeaway')]
    strings = [text for text in fields if isinstance(text, str)]
    if '_session_ids' not in src:
        src['_session_ids'] = [r['session_id'].lower() for r in src['sessions'] if isinstance(r.get('session_id'), str)]
    session_ids = src['_session_ids']
    shas = self_eval_archive_guard.shas_in(strings, session_ids)
    exits = []
    for sha in shas:
        result = subprocess.run(['git', '-C', src['root'], 'merge-base', '--is-ancestor', sha, 'HEAD'],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode not in (0, 1, 128):
            raise ValueError('git ancestry check failed for %s: %s' % (sha, result.stderr.strip()))
        exits.append(result.returncode)
    if 0 in exits:
        status = 'landed'
    elif 1 in exits:
        status = 'not-landed'
    elif shas:
        status = 'unverified'
    else:
        status = 'deferred' if self_eval_archive_guard.fix_evidence(row, session_ids, src['root'])['worklog'] else 'none-recorded'
    return {'date': row.get('date'), 'title': row.get('title'), 'status': status, 'shas': shas}


def _cascade(src, since, until):
    rows = _cascade_rows(src, since, until)
    examples, counts = {}, Counter({'cascade.unfixed': 0})
    for row in rows:
        if _fix_status(src, row)['status'] != 'landed':
            _add(counts, examples, 'cascade.unfixed', row, 'sessions')
    return {'cascade.unfixed': (counts['cascade.unfixed'], len(rows))}, examples, {'num': 0, 'den': len(rows)}, {}


def _in_span(day, since, until):
    return day != 'unknown' and (since is None or day >= since) and (until is None or day <= until)


def _denials(src, since, until):
    """Sessions with a guard denial of each category, over sessions that made any tool call in the window."""
    if 'denials' not in src:
        return {}, {}, {'num': 0, 'den': 0}, {'absent': 1}
    rows, calls = src['denials']['denials'], src['denials']['calls']
    hits = {(row['cat'], row['session']) for row in rows if _in_span(row['day'], since, until)}
    sessions = {key.split('|', 1)[1] for key in calls if _in_span(key.split('|', 1)[0], since, until)}
    counts = Counter(dict.fromkeys(['denials.%s.sessions' % cat for cat, _ in guard_denials.SIGNATURES], 0))
    examples = {}
    for cat, session in sorted(hits):
        _add(counts, examples, 'denials.%s.sessions' % cat, {'session_id': session}, 'sessions')
    return {key: (n, len(sessions)) for key, n in counts.items()}, examples, {'num': 0, 'den': 0}, {}


def _rule_miss_rows(row):
    """The row's well-formed `rule_misses` entries as (rule text, kind) pairs, or None when the row
    carries no list at all. A row with no field is typed absent, never a session that missed no rule."""
    items = row.get('rule_misses')
    if not isinstance(items, list):
        return None
    return [(item['rule'], item.get('kind')) for item in items
            if isinstance(item, dict) and isinstance(item.get('rule'), str) and item['rule'].strip()]


def _rule_slug(rule):
    """A rule's metric token: its repo-relative path with any `§<heading>` dropped, lowercased, every run
    of characters outside `[a-z0-9.]` folded to one dash. `.claude/CLAUDE.md` -> `claude-claude.md`."""
    path = rule.split('§', 1)[0].strip().replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return re.sub(r'[^a-z0-9.]+', '-', path.lower()).strip('.-')


def _rules(src, since, until):
    """Session counts per rule: one metric per rule named in any row of the source, zero-filled in a window
    that names it in no session, with the sessions that recorded `rule_misses` as the denominator. A rule
    named in `RULE_FINDING_MIN_SESSIONS` or more sessions also becomes a `rule.<slug>` finding."""
    rows = _window(src, 'sessions', since, until)
    named = {}                      # slug -> [one row per session that named it]
    for row in src['sessions']:
        for rule, _kind in (_rule_miss_rows(row) or ()):
            slug = _rule_slug(rule)
            if slug:
                named.setdefault(slug, [])
    counts = Counter(dict.fromkeys(['rules.%s.sessions' % slug for slug in named], 0))
    examples, coverage = {}, Counter()
    den = 0
    for row in rows:
        entries = _rule_miss_rows(row)
        if entries is None:
            coverage['rule-misses-absent'] += 1
            continue
        den += 1
        seen = set()
        for rule, _kind in entries:
            slug = _rule_slug(rule)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            _add(counts, examples, 'rules.%s.sessions' % slug, row, 'sessions')
            named.setdefault(slug, []).append(row)
    metrics = {key: (n, den) for key, n in counts.items()}
    for slug, rows_named in named.items():
        if len(rows_named) >= RULE_FINDING_MIN_SESSIONS:
            examples['rule.' + slug] = [_example(row, 'sessions') for row in rows_named[:3]]
    return metrics, examples, {'num': 0, 'den': 0}, dict(coverage)


REDUCERS = {'friction': _friction, 'outcome': _outcome, 'agents': _agents, 'rules': _rules,
            'pins': _pins, 'cascade': _cascade, 'denials': _denials}
FAMILIES = {name: (lambda src, since, until, reducer=reducer: reducer(src, since, until)[0])
            for name, reducer in REDUCERS.items()}


def measure(src, key, since, until):
    """One bad-row count and its denominator; unknown keys fail closed."""
    since, until = _day(since), _day(until)
    family = FAMILIES.get(key.split('.')[0])
    if family is None:
        raise ValueError('unknown metric: ' + key)
    metrics = family(src, since, until)
    if key not in metrics:
        raise ValueError('metric has no rows in the window: ' + key)
    return metrics[key]


def compute(src, *, today=None, since=None, until=None):
    """Every metric in sorted key order, with capped examples and typed coverage."""
    since, until = _day(since), _day(until or today)
    metrics, findings, unclassified, coverage = {}, {}, {}, {}
    for name, family in REDUCERS.items():
        counts, examples, unknown, typed = family(src, since, until)
        if name in ('friction', 'agents'):
            unclassified[name] = unknown
        coverage[name] = typed
        for key, value in counts.items():
            num, den = value[0], value[1]
            units = value[2] if len(value) > 2 else den      # the floor counts units: prompts are not sessions
            if name == 'agents' and not key.endswith('.all') and den < eval_history.MIN_CMP_AGENTS:
                continue
            if name == 'denials' and den == 0:
                continue
            metric = {'key': key, 'label': key, 'num': num, 'den': den, 'units': units,
                      'unit': 'agents' if name in ('agents', 'pins') else 'sessions'}
            metrics[key] = metric
            findings[key] = {'id': key, 'kind': name, 'metric': key, 'num': num, 'den': den, 'units': units,
                             'examples': examples.get(key, []),
                             'ledger': [r for r in src['ledger'] if r.get('metric') == key]}
        if name == 'rules':
            # One finding per rule named in 2+ sessions of the window; the per-rule metric is its row.
            for key, named in examples.items():
                if not key.startswith('rule.'):
                    continue
                metric = metrics['rules.%s.sessions' % key[len('rule.'):]]
                findings[key] = {'id': key, 'kind': 'rule', 'metric': metric['key'],
                                 'num': metric['num'], 'den': metric['den'], 'units': metric['units'],
                                 'examples': named,
                                 'ledger': [r for r in src['ledger'] if r.get('metric') == key]}
    for row in _cascade_rows(src, since, until):
        session = row.get('session_id')
        if isinstance(session, str) and session:
            key = 'cascade.' + session[:8]
        elif isinstance(row.get('id'), int) and not isinstance(row.get('id'), bool):
            key = 'cascade.n%d' % row['id']
        else:
            coverage['cascade']['identity-absent'] = coverage['cascade'].get('identity-absent', 0) + 1
            continue
        findings[key] = {'id': key, 'kind': 'cascade-session', 'metric': 'cascade.unfixed',
                         'num': int(_fix_status(src, row)['status'] != 'landed'), 'den': 1,
                         'examples': [_example(row, 'sessions')], 'ledger': [], **_fix_status(src, row)}
    return {'metrics': dict(sorted(metrics.items())), 'findings': dict(sorted(findings.items())),
            'unclassified': unclassified, 'coverage': coverage, 'diagnostics': src.get('diagnostics', {})}


BASELINE_DAYS = 30


def _shift(day, days):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=days)).isoformat()


def _counts(src, key, since, until):
    try:
        return measure(src, key, since, until)
    except ValueError:
        return (0, 0)


def _units(entry):
    """The floor's units count of a `(num, den[, units])` measurement: the third element when the family
    publishes one (`outcome.corrections-per-prompt` divides by prompts but floors on sessions), else den."""
    return entry[2] if len(entry) > 2 else entry[1]


def effects(src, *, today):
    """Each metric-carrying ledger row, read against the days before it and the days after it."""
    today = _day(today)
    metrics = compute(src, today=today)['metrics']
    out = []
    for row in src['ledger']:
        key = row.get('metric')
        if not key:
            continue
        result = {'row': row, 'key': key, 'status': None, 'baseline': None, 'after': None,
                  'delta': None, 'moved': None, 'detail': ''}
        out.append(result)
        if row.get('outcome') != 'applied':
            result.update(status='not-measured', detail='%s rows are linked, not measured' % (row.get('outcome') or 'unreadable-outcome'))
            continue
        if not isinstance(key, str) or key not in metrics:
            result.update(status='unmeasurable', detail='unmeasurable metric: %s' % (key,))
            continue
        if key.startswith('cascade.'):
            result.update(status='unmeasurable', detail='a recent session reads unfixed until its fix lands; measure outcome.cause-e')
            continue
        try:
            day = _day(row.get('date'))
        except ValueError:
            day = None
        if day is None:
            result.update(status='unmeasurable', detail='ledger row date unreadable')
            continue
        unit = metrics[key]['unit']
        floor = eval_history.MIN_CMP_AGENTS if unit == 'agents' else eval_history.MIN_CMP_SESSIONS
        base = _counts(src, key, _shift(day, -BASELINE_DAYS), _shift(day, -1))
        after = _counts(src, key, _shift(day, 1), today)
        result.update(baseline=base, after=after)
        if _units(after) < floor:
            result.update(status='waiting', detail='waiting (%d of %d)' % (_units(after), floor))
        elif _units(base) == 0:
            result.update(status='baseline-unavailable')
        elif _units(base) < floor:
            result.update(status='baseline-thin', detail='baseline thin (%d of %d)' % (_units(base), floor))
        else:
            delta = 100 * (after[0] / after[1] - base[0] / base[1])
            result['delta'] = delta
            first = src['first'][unit]
            if first is None or first > _shift(day, -BASELINE_DAYS):
                result['status'] = 'baseline-partial'
            else:
                moved = eval_history.shift(delta, after[0] - base[0], 'lower')
                result.update(status='measured', moved={'flat': 'flat', 'better': 'improved', 'worse': 'worse'}[moved])
    return out


MEASURED_STATUSES = ('measured', 'waiting', 'baseline-partial', 'baseline-unavailable', 'baseline-thin')


def summarize(effects):
    """Counts of the effects that carry a measurement state: the moved words, waiting and baseline-limited rows."""
    states = [e['moved'] or e['status'] for e in effects if e['status'] in MEASURED_STATUSES]
    return {'improved': states.count('improved'), 'flat': states.count('flat'), 'worse': states.count('worse'),
            'waiting': states.count('waiting'), 'baseline_short': sum(s.startswith('baseline-') for s in states),
            'total': len(states)}


def dashboard_since(today):
    """First day of the month before `today`'s: the window the dashboard's Findings tab and note read."""
    year, month = int(today[:4]), int(today[5:7]) - 1
    return '%04d-%02d-01' % ((year, month) if month else (year - 1, 12))


def trends(src, *, today, weeks=6):
    """Each metric's contiguous 7-day windows, oldest first; window 0 ends on today."""
    today = _day(today)
    keys = compute(src, today=today)['metrics']
    out = {key: [] for key in keys}
    for i in reversed(range(weeks)):
        end = _shift(today, -7 * i)
        start = _shift(end, -6)
        window = {k: v for family in FAMILIES.values() for k, v in family(src, start, end).items()}
        for key in keys:
            value = window.get(key, (0, 0))
            out[key].append({'start': start, 'end': end, 'num': value[0], 'den': value[1]})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', default=ROOT)
    parser.add_argument('--since')
    parser.add_argument('--dashboard', action='store_true', help='read the dashboard window: the prior month onward')
    parser.add_argument('--until')
    parser.add_argument('--json', metavar='FILE')
    args = parser.parse_args(argv)
    try:
        src = load(args.root)
        since = dashboard_since(args.until or datetime.date.today().isoformat()) if args.dashboard else args.since
        found = compute(src, since=since, until=args.until)
        print('Keyword counts; not recurrences or causes. Lower is better.')
        for key, metric in found['metrics'].items():
            finding = found['findings'][key]
            print('%s %d/%d examples=%s' % (key, metric['num'], metric['den'], json.dumps(finding['examples'], ensure_ascii=False)))
            for row in finding['ledger']:
                print('  ledger ' + json.dumps(row, ensure_ascii=False))
        for key, finding in found['findings'].items():
            if finding['kind'] == 'cascade-session':
                print('%s %s %s %s shas=%s' % (key, finding['date'], finding['status'], finding['title'], ','.join(finding['shas'])))
        for family, share in found['unclassified'].items():
            print('%s unclassified %d/%d (%.1f%%)' % (family, share['num'], share['den'],
                  100 * share['num'] / share['den'] if share['den'] else 0))
            print('  coverage ' + json.dumps(found['coverage'][family], sort_keys=True))
        print('sources ' + json.dumps(found['diagnostics'], sort_keys=True))
        print('trend (7-day windows, oldest first):')
        today = args.until or datetime.date.today().isoformat()
        for key, windows in trends(src, today=today).items():
            print('%s %s' % (key, ' '.join('%d/%d' % (w['num'], w['den']) if w['den'] else '-' for w in windows)))
        if args.json:
            with open(args.json, 'w', encoding='utf-8', newline='\n') as handle:
                json.dump(found, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
        return 0
    except (ValueError, OSError, TimeoutError) as exc:
        print('REFUSED: ' + str(exc))
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
