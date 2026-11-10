"""Plain-script proof for findings, using trimmed September archive captures.

The four datasets are the September capture trimmed to the fields the readers here use
(session identity/date/outcome/pattern/friction, agent run/date/label/model/outcome/tier/
spec/note, and the label sets' text with its hand labels). The proof plants them in its
own temp folder on first `capture()`, so it reads no fixture on this machine.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
sys.path.insert(0, TOOLS)
try:
    import eval_findings as ef
except ImportError:
    ef = None

_PLANTED = None


def capture(name):
    """One planted dataset, written to this run's own temp folder on first use."""
    global _PLANTED
    if _PLANTED is None:
        _PLANTED = tempfile.mkdtemp(prefix='eval_findings_fixture_')
        atexit.register(shutil.rmtree, _PLANTED, ignore_errors=True)
        for key, value in DATA.items():
            with open(os.path.join(_PLANTED, key + '.json'), 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(value))
    with open(os.path.join(_PLANTED, name + '.json'), encoding='utf-8') as handle:
        return json.load(handle)


def source():
    return {'root': os.path.dirname(os.path.dirname(TOOLS)), 'sessions': capture('sessions'),
            'agents': capture('agents'), 'ledger': [], 'diagnostics': {}}


class Suite:
    def __init__(self):
        self.failures, self.count = [], 0

    def check(self, ok, label, detail=''):
        self.count += 1
        print('%-4s %s' % ('ok' if ok else 'FAIL', label))
        if not ok:
            self.failures.append(label + (': ' + detail if detail else ''))

    def guard(self, name, fn):
        try:
            fn()
        except Exception:
            self.check(False, name + ' raised', traceback.format_exc(limit=2).strip().splitlines()[-1])


S = Suite()


def test_friction_sessions_counts_from_trimmed_real_rows():
    found = ef.compute(source(), today='2026-09-30')
    m = found['metrics']
    S.check((m['friction.recursive-grep.sessions']['num'], m['friction.recursive-grep.sessions']['den']) == (2, 5),
            'test_friction_sessions_counts_from_trimmed_real_rows: den counts the 5 of 8 captured sessions that carry friction data')
    S.check((m['friction.accepted.rows']['num'], m['friction.accepted.rows']['den']) == (18, 46),
            'accepted counts captured dispositions, not sessions')


def test_measure_equals_compute_on_the_full_window():
    src = source()
    found = ef.compute(src, today='2026-09-30')
    S.check(all(ef.measure(src, k, None, '2026-09-30')[:2] == (m['num'], m['den'])
                for k, m in found['metrics'].items()), 'test_measure_equals_compute_on_the_full_window')
    try:
        ef.measure(src, 'friction.not-a-category.sessions', None, None)
        refused = False
    except ValueError:
        refused = True
    S.check(refused, 'unknown metric fails closed')


def test_unclassified_string_is_counted_once_and_reported():
    src = source()
    src['sessions'] = src['sessions'][:1]
    found = ef.compute(src, today='2026-09-30')
    whats = [i['what'] for i in src['sessions'][0]['friction'] if isinstance(i, dict) and isinstance(i.get('what'), str)]
    expected = {'num': sum(not ef._matches(w, ef.FRICTION) for w in whats), 'den': 12}
    S.check(len(whats) == 12 and found['unclassified']['friction'] == expected,
            'test_unclassified_string_is_counted_once_and_reported', repr(found['unclassified']))


def test_dashboard_wiring_and_cli():
    import test_eval_dashboard as td
    import eval_dashboard
    root = td.plant_root()
    found = eval_dashboard.build_facts(root, '2026-09-30')
    S.check('findings' in found and 'friction.accepted.rows' in found.get('findings', {}).get('metrics', {}),
            'dashboard facts include the tracer')
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'findings.json')
        result = subprocess.run([sys.executable, os.path.join(TOOLS, 'eval_findings.py'), '--root', root,
                                 '--until', '2026-09-30', '--json', path], capture_output=True, text=True, encoding='utf-8')
        S.check(result.returncode == 0 and 'friction unclassified' in result.stdout and os.path.isfile(path),
                'CLI runs through real load and writes full JSON', result.stderr)


def test_outcome_family_uses_known_outcomes():
    src = source()
    found = ef.compute(src, today='2026-09-30')
    known = sum(r.get('outcome') in ('clean', 'correction', 'failure') for r in src['sessions'])
    S.check(found['metrics']['outcome.failure']['num'] == 3 and found['metrics']['outcome.failure']['den'] == known,
            'test_outcome_family_uses_known_outcomes')


def test_clean_session_never_counts_a_correction():
    src = source()
    clean = next(r for r in src['sessions'] if r.get('outcome') == 'clean')
    src['sessions'] = [clean]
    found = ef.compute(src, today='2026-09-30')
    S.check(found['metrics']['outcome.correction']['num'] == 0, 'test_clean_session_never_counts_a_correction')


def test_findings_window_starts_at_the_prior_month():
    S.check(ef.dashboard_since('2026-10-09') == '2026-09-01' and ef.dashboard_since('2026-01-15') == '2025-12-01',
            'test_findings_window_starts_at_the_prior_month')


def test_typed_session_fields():
    src = source()
    rows = src['sessions'][:4]
    rows[0].pop('outcome')
    rows[1]['outcome'] = []
    rows[2]['outcome'] = None
    src['sessions'] = rows
    found = ef.compute(src, today='2026-09-30')
    S.check(found['metrics']['outcome.failure']['den'] == 1 and
            found['coverage']['outcome'] == {'absent': 1, 'unparseable': 1, 'unattempted': 1, 'prompts-absent': 4},
            'session outcome states stay distinct', repr(found['coverage']['outcome']))


def test_agent_class_den_counts_only_rated_rows():
    found = ef.compute(source(), today='2026-09-30')
    m = found['metrics']['agents.parent-spec.all']
    S.check((m['den'], m['unit']) == (18, 'agents') and 0 <= m['num'] <= m['den'],
            'test_agent_class_den_counts_only_rated_rows', repr(m))


def test_model_below_floor_is_not_keyed():
    src = source()
    src['agents'] = src['agents'][-4:]
    found = ef.compute(src, today='2026-09-30')
    S.check([k for k in found['metrics'] if k.startswith('agents.')] ==
            ['agents.parent-spec.all', 'agents.partial-coverage.all', 'agents.red-proofs.all',
             'agents.tier-absent.all', 'agents.unrated.all'],
            'test_model_below_floor_is_not_keyed')


def test_unrated_and_absent_note_are_distinct_outcomes():
    found = ef.compute(source(), today='2026-09-30')
    c = found['coverage']['agents']
    S.check(c['unrated'] == 1 and c['unattempted'] == 1 and c['note-absent'] == 16,
            'test_unrated_and_absent_note_are_distinct_outcomes', repr(c))
    src = source()
    src['agents'][0].pop('outcome')
    src['agents'][1]['outcome'] = 'unknown future outcome'
    src['agents'][2]['sampled_out'] = True
    src['agents'][3]['note'] = []
    c = ef.compute(src, today='2026-09-30')['coverage']['agents']
    S.check(c['absent'] == 1 and c['unparseable'] == 1 and c['unattempted'] == 2 and c['note-unparseable'] == 1,
            'agent unknown, absent and unattempted evidence stay distinct', repr(c))


def test_agent_metric_key_matches_the_ledger_token_shape():
    import model_registry
    import re
    models = [r['id'] for r in model_registry.load()['models']] + ['opus']
    src = source()
    rows = src['agents'][:8]
    for model in models:
        src['agents'] = [dict(r, model=model) for r in rows]
        found = ef.compute(src, today='2026-09-30')
        keys = [k for k in found['metrics'] if k.startswith('agents.') and not k.endswith('.all')]
        S.check(bool(keys) and all(re.fullmatch(r'[a-z0-9.-]+', k) for k in keys),
                'test_agent_metric_key_matches_the_ledger_token_shape: ' + model)


def git(root, *args):
    return subprocess.run(['git', '-C', root, *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def cascade_source(root, text, disposition='fixed'):
    src = source()
    row = next(r for r in src['sessions'] if r['outcome'] == 'failure')
    row['friction'] = [dict(row['friction'][0], disposition=disposition)]
    row['notes'], row['key_takeaway'] = text, ''
    src['root'], src['sessions'] = root, [row]
    return src, 'cascade.' + row['session_id'][:8]


def test_sha_resolves_to_landed_ancestor():
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        tree = git(root, 'mktree')
        def commit(message, parent=None):
            args = ['-c', 'user.name=Proof', '-c', 'user.email=proof@example.invalid', 'commit-tree', tree]
            if parent:
                args += ['-p', parent]
            return git(root, *args, '-m', message)
        landed = commit('landed')
        git(root, 'update-ref', 'HEAD', landed)
        unreachable = commit('unreachable')
        src, key = cascade_source(root, landed[:9] + ' ' + unreachable)
        found = ef.compute(src, today='2026-09-30')
        S.check(found['findings'][key]['status'] == 'landed' and
                found['metrics']['cascade.unfixed']['num'] == 0,
                'test_sha_resolves_to_landed_ancestor')


def test_unresolved_token_is_unverified_not_landed():
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        src, key = cascade_source(root, 'deadbee1')
        found = ef.compute(src, today='2026-09-30')
        S.check(found['findings'][key]['status'] == 'unverified' and
                ef.measure(src, 'cascade.unfixed', None, '2026-09-30') == (1, 1),
                'test_unresolved_token_is_unverified_not_landed')


def test_no_token_is_none_recorded():
    with tempfile.TemporaryDirectory() as root:
        src, key = cascade_source(root, '')
        S.check(ef.compute(src, today='2026-09-30')['findings'][key]['status'] == 'none-recorded',
                'test_no_token_is_none_recorded')
        src, key = cascade_source(root, '')
        src['sessions'][0]['friction'][0]['disposition'] = 'worklog: captured'
        S.check(ef.compute(src, today='2026-09-30')['findings'][key]['status'] == 'none-recorded',
                'worklog title absent from the mirror is not deferred')
        title = 'Repoint seven scenes whose res:// targets no longer exist'
        os.makedirs(os.path.join(root, '.claude'))
        with open(os.path.join(root, '.claude', 'worklog-titles.md'), 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('- fix \u00b7 2 \u00b7 ' + title + '\n')
        src, key = cascade_source(root, '')
        src['sessions'][0]['friction'][0]['disposition'] = 'worklog: ' + title
        S.check(ef.compute(src, today='2026-09-30')['findings'][key]['status'] == 'deferred',
                'worklog title on the mirror is deferred')


def test_fix_status_spawns_git_with_a_devnull_stdin():
    """X3: every git call this reader spawns carries stdin=DEVNULL.

    A runner can hand this process no stdin at all; `subprocess.run` with `stdin=None` then builds
    the child's STARTUPINFO from an invalid standard handle and raises OSError (WinError 6), so the
    status read is lost rather than wrong. The guard's `_is_commit` already passes DEVNULL; this
    case drives the real reader against a real repository and watches what each spawn was given.
    """
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        tree = git(root, 'mktree')
        sha = git(root, '-c', 'user.name=Proof', '-c', 'user.email=proof@example.invalid',
                  'commit-tree', tree, '-m', 'landed')
        git(root, 'update-ref', 'HEAD', sha)
        src, key = cascade_source(root, sha)
        seen = []
        real_run = ef.subprocess.run

        def spy(*args, **kwargs):
            seen.append(kwargs.get('stdin'))
            return real_run(*args, **kwargs)

        ef.subprocess.run = spy
        try:
            found = ef.compute(src, today='2026-09-30')
        finally:
            ef.subprocess.run = real_run
        S.check(bool(seen) and all(value is subprocess.DEVNULL for value in seen)
                and found['findings'][key]['status'] == 'landed',
                'test_fix_status_spawns_git_with_a_devnull_stdin', repr(seen))


def test_family_seam_returns_counts():
    """Every family returns `{key: (num, den)}`, or `(num, den, units)` where the floor's units count is
    not the rate's denominator (`outcome.corrections-per-prompt` divides by prompts, floors on sessions)."""
    src = source()
    S.check(all(isinstance(fn(src, None, '2026-09-30'), dict) and
                all(isinstance(v, tuple) and len(v) in (2, 3) and all(isinstance(n, int) for n in v)
                    for v in fn(src, None, '2026-09-30').values())
                for fn in ef.FAMILIES.values()), 'metric families return key-to-count pairs')


def test_friction_types_missing_and_bad_text_independently_of_disposition():
    src = source()
    row = src['sessions'][0]
    captured = row['friction'][0]
    row['friction'] = [dict(captured), dict(captured), dict(captured)]
    row['friction'][0].pop('what')
    row['friction'][1]['what'] = None
    row['friction'][2]['what'] = []
    src['sessions'] = [row]
    found = ef.compute(src, today='2026-09-30')
    S.check(found['metrics']['friction.accepted.rows']['num'] == 3,
            'accepted disposition remains counted when what is absent or malformed')
    c = found['coverage']['friction']
    S.check(c.get('what-absent') == 1 and c.get('what-unattempted') == 1 and c.get('what-unparseable') == 1,
            'friction text states stay distinct', repr(c))


def test_cascade_id_falls_back_to_the_archive_id():
    src = source()
    row = next(r for r in src['sessions'] if r['outcome'] == 'failure')
    row.pop('session_id')
    row['id'] = 29
    src['sessions'] = [row]
    found = ef.compute(src, today='2026-09-30')
    key = 'cascade.n29'
    S.check(key in found['findings'] and found['metrics']['cascade.unfixed']['den'] == 1,
            'test_cascade_id_falls_back_to_the_archive_id')


def test_cascade_without_any_identity_is_counted_not_named():
    src = source()
    row = next(r for r in src['sessions'] if r['outcome'] == 'failure')
    row.pop('session_id')
    row.pop('id', None)
    src['sessions'] = [row]
    found = ef.compute(src, today='2026-09-30')
    S.check(not [k for k in found['findings'] if k.startswith('cascade.') and k != 'cascade.unfixed']
            and found['metrics']['cascade.unfixed']['den'] == 1
            and found['coverage']['cascade'].get('identity-absent') == 1,
            'test_cascade_without_any_identity_is_counted_not_named', repr(found['coverage'].get('cascade')))


def test_non_ancestor_commit_is_not_landed():
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        tree = git(root, 'mktree')
        def commit(message, parent=None):
            args = ['-c', 'user.name=Proof', '-c', 'user.email=proof@example.invalid', 'commit-tree', tree]
            if parent:
                args += ['-p', parent]
            return git(root, *args, '-m', message)
        head = commit('head')
        git(root, 'update-ref', 'HEAD', head)
        elsewhere = commit('elsewhere')
        src, key = cascade_source(root, elsewhere[:9])
        found = ef.compute(src, today='2026-09-30')
        S.check(found['findings'][key]['status'] == 'not-landed' and
                found['metrics']['cascade.unfixed']['num'] == 1,
                'test_non_ancestor_commit_is_not_landed')


def test_friction_den_counts_only_sessions_that_carry_friction_data():
    src = source()
    with_friction = [r for r in src['sessions'] if isinstance(r.get('friction'), list)][:3]
    without = dict(with_friction[0])
    without.pop('friction')
    src['sessions'] = with_friction + [without]
    found = ef.compute(src, today='2026-09-30')
    num, den = found['metrics']['friction.recursive-grep.sessions']['num'], found['metrics']['friction.recursive-grep.sessions']['den']
    S.check(den == 3 and found['coverage']['friction'].get('absent') == 1 and num <= den,
            'test_friction_den_counts_only_sessions_that_carry_friction_data', repr((num, den)))


LABEL_BAR = 0.9


def test_tables_reproduce_the_hand_labels():
    labels = capture('labels')
    for kind, table in (('friction', ef.FRICTION), ('defects', ef.DEFECT_CLASSES)):
        rows = labels[kind]
        S.check(all(set(r['labels']) <= set(table) for r in rows), 'every %s label is a table category' % kind)
        exact = sum(set(ef._matches(r['text'], table)) == set(r['labels']) for r in rows)
        S.check(exact >= LABEL_BAR * len(rows), 'test_tables_reproduce_the_hand_labels: %s' % kind,
                '%d of %d labeled strings classified exactly' % (exact, len(rows)))


def test_session_id_prefix_is_not_a_sha():
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        src, key = cascade_source(root, 'x')
        prefix = src['sessions'][0]['session_id'][:8]
        src['sessions'][0]['notes'] = 'compared with session ' + prefix
        found = ef.compute(src, today='2026-09-30')
        S.check(found['findings'][key]['status'] == 'none-recorded' and not found['findings'][key]['shas'],
                'test_session_id_prefix_is_not_a_sha', repr(found['findings'][key]))


HOLDOUT_FLOOR = {'friction': 17, 'defects': 22}


def test_tables_hold_their_holdout_score():
    labels = capture('labels_holdout')
    for kind, table in (('friction', ef.FRICTION), ('defects', ef.DEFECT_CLASSES)):
        exact = sum(set(ef._matches(r['text'], table)) == set(r['labels']) for r in labels[kind])
        S.check(exact >= HOLDOUT_FLOOR[kind], 'test_tables_hold_their_holdout_score: ' + kind,
                '%d of %d unseen labeled strings, floor %d' % (exact, len(labels[kind]), HOLDOUT_FLOOR[kind]))


def test_hex_word_without_a_digit_is_not_a_sha():
    with tempfile.TemporaryDirectory() as root:
        git(root, 'init', '-q')
        src, key = cascade_source(root, 'defaced the guard and effaced its log')
        found = ef.compute(src, today='2026-09-30')
        S.check(found['findings'][key]['status'] == 'none-recorded' and not found['findings'][key]['shas'],
                'test_hex_word_without_a_digit_is_not_a_sha', repr(found['findings'][key]))


def dated(rows, dates):
    return [dict(r, date=d) for d in dates for r in rows]


def picked(prefixes, dates):
    return dated([r for r in capture('sessions') if r['session_id'][:8] in prefixes], dates)


def effect_source(sessions, ledger):
    src = source()
    src['sessions'], src['ledger'] = sessions, ledger
    src['first'] = {'sessions': min(r['date'] for r in sessions), 'agents': None}
    return src


def ledger_row(key, date, outcome='applied'):
    return {'date': date, 'session': 'proof', 'target': 'proof.md', 'signal': 'proof', 'recommended': 'proof',
            'outcome': outcome, 'reason': 'proof', 'metric': key}


GREP = 'friction.recursive-grep.sessions'
GREP_HITS = ('c981854e', '3259384b')
GREP_CLEAN = ('f34d6daa', 'c9aa75e3', '00d8a771')


def test_row_links_to_the_finding_with_its_metric():
    ledger = [ledger_row(GREP, '2026-09-03'), ledger_row('friction.accepted.rows', '2026-09-03')]
    found = ef.compute(effect_source(capture('sessions'), ledger), today='2026-10-09')
    S.check(found['findings'][GREP]['ledger'] == [ledger[0]],
            'test_row_links_to_the_finding_with_its_metric')


def test_effect_waits_below_the_floor():
    src = effect_source(capture('sessions'), [ledger_row(GREP, '2026-09-03')])
    row = ef.effects(src, today='2026-10-09')[0]
    S.check(row['status'] == 'waiting' and row['after'] == (2, 5) and row['detail'] == 'waiting (5 of 6)'
            and row['delta'] is None and row['moved'] is None,
            'test_effect_waits_below_the_floor', repr(row))


def test_flat_band_matches_eval_history():
    import eval_history
    base = capture('sessions') + dated(capture('sessions'), ['2026-09-03'])
    after_dates = ['2026-10-05', '2026-10-06']

    def moved(after_rows, **patch):
        saved = {k: getattr(eval_history, k) for k in patch}
        for k, v in patch.items():
            setattr(eval_history, k, v)
        try:
            return ef.effects(effect_source(base + after_rows, [ledger_row(GREP, '2026-10-01')]),
                              today='2026-10-09')[0]
        finally:
            for k, v in saved.items():
                setattr(eval_history, k, v)

    same = moved(picked(GREP_HITS + GREP_CLEAN, after_dates))
    better = moved(picked(GREP_CLEAN, after_dates))
    worse = moved(picked(GREP_HITS, ['2026-10-05', '2026-10-06', '2026-10-07']))
    S.check(same['moved'] == 'flat' and same['delta'] == 0.0, 'test_flat_band_matches_eval_history: inside band', repr(same))
    S.check(better['moved'] == 'improved' and better['delta'] < -eval_history.TREND_POINTS,
            'test_flat_band_matches_eval_history: beyond band, lower', repr(better))
    S.check(worse['moved'] == 'worse' and worse['delta'] > eval_history.TREND_POINTS,
            'test_flat_band_matches_eval_history: beyond band, higher', repr(worse))
    S.check(moved(picked(GREP_CLEAN, after_dates), TREND_POINTS=50)['moved'] == 'flat',
            'test_flat_band_matches_eval_history: TREND_POINTS is read from eval_history')
    S.check(moved(picked(GREP_CLEAN, after_dates), MIN_CMP_ROWS=5)['moved'] == 'flat',
            'test_flat_band_matches_eval_history: MIN_CMP_ROWS is read from eval_history')


def test_declined_and_recorded_rows_are_never_measured():
    ledger = [ledger_row(GREP, '2026-10-01', 'declined'), ledger_row(GREP, '2026-10-01', 'recorded')]
    rows = ef.effects(effect_source(capture('sessions'), ledger), today='2026-10-09')
    S.check([(r['status'], r['baseline'], r['after']) for r in rows] == [('not-measured', None, None)] * 2,
            'test_declined_and_recorded_rows_are_never_measured', repr(rows))


def test_row_without_an_applied_outcome_or_a_date_is_not_measured_as_a_fix():
    no_outcome = ledger_row(GREP, '2026-10-01')
    no_outcome.pop('outcome')
    no_date = ledger_row(GREP, '2026-10-01')
    no_date.pop('date')
    rows = ef.effects(effect_source(capture('sessions'), [no_outcome, no_date]), today='2026-10-09')
    S.check([r['status'] for r in rows] == ['not-measured', 'unmeasurable'] and all(r['moved'] is None for r in rows),
            'test_row_without_an_applied_outcome_or_a_date_is_not_measured_as_a_fix', repr(rows))


def test_unknown_metric_key_reads_unmeasurable():
    key = 'friction.no-such-category.sessions'
    row = ef.effects(effect_source(capture('sessions'), [ledger_row(key, '2026-10-01')]), today='2026-10-09')[0]
    S.check(row['status'] == 'unmeasurable' and key in row['detail'], 'test_unknown_metric_key_reads_unmeasurable', repr(row))


def test_baseline_unavailable_when_window_has_no_rows():
    sessions = dated(capture('sessions'), ['2026-08-05', '2026-08-06'])
    row = ef.effects(effect_source(sessions, [ledger_row(GREP, '2026-08-01')]), today='2026-10-09')[0]
    S.check(row['status'] == 'baseline-unavailable' and row['after'] == (4, 10),
            'test_baseline_unavailable_when_window_has_no_rows', repr(row))


def test_baseline_partial_when_the_window_starts_late():
    sessions = dated(capture('sessions'), ['2026-09-03', '2026-09-04', '2026-09-25', '2026-09-26'])
    row = ef.effects(effect_source(sessions, [ledger_row(GREP, '2026-09-20')]), today='2026-10-09')[0]
    S.check(row['status'] == 'baseline-partial' and row['moved'] is None and row['baseline'] and row['after'] and row['delta'] is not None,
            'test_baseline_partial_when_the_window_starts_late', repr(row))


def test_trend_windows_are_contiguous_and_never_overlap():
    import datetime
    today = '2026-09-30'
    found = ef.trends(source(), today=today)
    keys = ef.compute(source(), today=today)['metrics']
    ok = set(found) == set(keys)
    for key, windows in found.items():
        ok = ok and len(windows) == 6
        for older, newer in zip(windows[:-1], windows[1:]):
            ok = ok and newer['start'] == (datetime.date.fromisoformat(older['end']) + datetime.timedelta(days=1)).isoformat()
        for w in windows:
            ok = ok and (datetime.date.fromisoformat(w['end']) - datetime.date.fromisoformat(w['start'])).days == 6
    ok = ok and found[GREP][-1]['end'] == today
    S.check(ok, 'test_trend_windows_are_contiguous_and_never_overlap')


def test_trend_windows_add_up_to_the_span():
    src = source()
    windows = ef.trends(src, today='2026-09-30')[GREP]
    num, den = sum(w['num'] for w in windows), sum(w['den'] for w in windows)
    S.check((num, den) == ef.measure(src, GREP, windows[0]['start'], '2026-09-30') and den > 0,
            'test_trend_windows_add_up_to_the_span', repr((num, den)))


def test_empty_window_reads_zero_zero():
    windows = ef.trends(source(), today='2026-12-31')[GREP]
    S.check(all((w['num'], w['den']) == (0, 0) for w in windows), 'test_empty_window_reads_zero_zero', repr(windows))


def test_sub_floor_model_measures_its_real_count_but_is_not_published():
    src = source()
    src['agents'] = src['agents'][-4:]
    keyed = [k for k in ef.REDUCERS['agents'](src, None, None)[0] if not k.endswith('.all')]
    published = ef.compute(src, today='2026-09-30')['metrics']
    real = [ef.measure(src, k, None, '2026-09-30') for k in keyed]
    S.check(keyed and not [k for k in keyed if k in published] and all(0 < den < 8 for _num, den in real),
            'test_sub_floor_model_measures_its_real_count_but_is_not_published', repr((keyed, real)))


def test_per_session_cascade_findings_have_their_own_kind():
    found = ef.compute(source(), today='2026-09-30')['findings']
    sessions = [f for k, f in found.items() if k.startswith('cascade.') and k != 'cascade.unfixed']
    S.check(sessions and all(f['kind'] == 'cascade-session' for f in sessions) and found['cascade.unfixed']['kind'] == 'cascade',
            'test_per_session_cascade_findings_have_their_own_kind', repr([f['kind'] for f in sessions]))


def test_shift_rule_is_shared_with_the_month_comparison():
    import eval_history
    band, rows = eval_history.TREND_POINTS, eval_history.MIN_CMP_ROWS
    S.check(eval_history.shift(0, 0, 'lower') == 'flat' and eval_history.shift(band + 1, rows, 'lower') == 'worse'
            and eval_history.shift(-band - 1, rows, 'lower') == 'better'
            and eval_history.shift(band + 1, rows - 1, 'higher') == 'flat'
            and eval_history.shift(band + 1, rows, 'higher') == 'better',
            'test_shift_rule_is_shared_with_the_month_comparison')


def test_effect_on_a_non_string_metric_is_unmeasurable_not_a_crash():
    row = ledger_row(['not', 'a', 'key'], '2026-09-03')
    out = ef.effects(effect_source(capture('sessions'), [row]), today='2026-10-09')[0]
    S.check(out['status'] == 'unmeasurable', 'test_effect_on_a_non_string_metric_is_unmeasurable_not_a_crash', repr(out))


def test_cascade_metric_is_unmeasurable_as_an_effect():
    out = ef.effects(effect_source(capture('sessions'), [ledger_row('cascade.unfixed', '2026-09-03')]), today='2026-10-09')[0]
    S.check(out['status'] == 'unmeasurable' and 'cause-e' in out['detail'],
            'test_cascade_metric_is_unmeasurable_as_an_effect', repr(out))


def test_agent_effect_waits_on_the_agent_floor_with_its_real_count():
    src = effect_source(capture('sessions'), [ledger_row('agents.partial-coverage.all', '2026-09-30')])
    out = ef.effects(src, today='2026-10-09')[0]
    S.check(out['status'] == 'waiting' and out['detail'].endswith(' of 8)'),
            'test_agent_effect_waits_on_the_agent_floor_with_its_real_count', repr(out))


def test_thin_baseline_is_its_own_status():
    rows = picked(GREP_HITS + GREP_CLEAN, ['2026-08-30']) + picked(GREP_HITS + GREP_CLEAN, ['2026-09-05', '2026-09-06'])
    out = ef.effects(effect_source(rows, [ledger_row(GREP, '2026-09-03')]), today='2026-10-09')[0]
    S.check(out['status'] == 'baseline-thin' and out['detail'].startswith('baseline thin (5 of'),
            'test_thin_baseline_is_its_own_status', repr(out))


def test_summarize_counts_every_measured_state_once():
    rows = [{'status': 'measured', 'moved': 'improved'}, {'status': 'measured', 'moved': 'flat'},
            {'status': 'measured', 'moved': 'worse'}, {'status': 'waiting', 'moved': None},
            {'status': 'baseline-thin', 'moved': None}, {'status': 'baseline-partial', 'moved': None},
            {'status': 'baseline-unavailable', 'moved': None}, {'status': 'unmeasurable', 'moved': None},
            {'status': 'not-measured', 'moved': None}]
    S.check(ef.summarize(rows) == {'improved': 1, 'flat': 1, 'worse': 1, 'waiting': 1, 'baseline_short': 3, 'total': 7},
            'test_summarize_counts_every_measured_state_once', repr(ef.summarize(rows)))


def test_trend_cell_of_a_sub_floor_model_shows_its_real_count():
    src = source()
    src['agents'] = src['agents'][-4:]
    keyed = [k for k in ef.REDUCERS['agents'](src, None, None)[0] if not k.endswith('.all')]
    S.check(all(ef.measure(src, k, None, '2026-09-30')[1] > 0 for k in keyed) and keyed,
            'test_trend_cell_of_a_sub_floor_model_shows_its_real_count', repr(keyed))


def test_accepted_class_is_counted_per_class_and_classless_stays_coverage():
    """S8: `accepted: <class>: <why>`; a classless accepted row is typed as coverage, never zero."""
    src = source()
    src['sessions'] = [{
        'session_id': 'class-1', 'date': '2026-09-10', 'outcome': 'clean', 'pattern': None,
        'friction': [
            {'what': 'a', 'disposition': 'accepted: one-off: a one-time client bug'},
            {'what': 'b', 'disposition': 'accepted: outside: provider outage'},
            {'what': 'c', 'disposition': 'accepted: guard-working: the gate fired as designed'},
            {'what': 'd', 'disposition': 'accepted: owner-held: the owner holds this call'},
            {'what': 'e', 'disposition': 'accepted: no class here'},
        ],
    }]
    found = ef.compute(src, today='2026-09-30')
    m = found['metrics']
    S.check(all((m['friction.accepted.%s.rows' % cls]['num'], m['friction.accepted.%s.rows' % cls]['den']) == (1, 5)
                for cls in ('one-off', 'outside', 'guard-working', 'owner-held')),
            'test_accepted_class_is_counted_per_class_and_classless_stays_coverage', repr(
                {k: v for k, v in m.items() if 'accepted' in k}))
    S.check(m['friction.accepted.rows']['num'] == 5
            and found['coverage']['friction'].get('accepted-classless') == 1,
            'a classless accepted row still counts as accepted and is typed as coverage',
            repr(found['coverage']['friction']))


def test_outcome_and_cascade_families_report_no_unclassified_share():
    found = ef.compute(source(), today='2026-09-30')
    S.check(sorted(found['unclassified']) == ['agents', 'friction'], 'test_outcome_and_cascade_families_report_no_unclassified_share',
            repr(sorted(found['unclassified'])))


def test_falsifying_outcomes_have_one_owner():
    import orchestration_metrics
    S.check(orchestration_metrics.FALSIFYING == ('defects', 'rework'), 'test_falsifying_outcomes_have_one_owner')


DENIAL_GREP = 'denials.recursive-grep.sessions'


def denials_source(rows, calls):
    src = source()
    src['denials'] = {'denials': rows, 'calls': calls, 'coverage': {}}
    return src


def test_denials_family_counts_sessions_with_a_denial_per_window():
    rows = [{'day': '2026-09-03', 'session': 'aaa', 'cat': 'recursive-grep'},
            {'day': '2026-09-03', 'session': 'aaa', 'cat': 'recursive-grep'},
            {'day': 'unknown', 'session': 'ddd', 'cat': 'recursive-grep'}]
    calls = {'2026-09-03|aaa': 4, '2026-09-03|bbb': 2, 'unknown|ddd': 1}
    src = denials_source(rows, calls)
    inside = ef.compute(src, since='2026-09-01', until='2026-09-30')['metrics']
    outside = ef.compute(src, since='2026-10-01', until='2026-10-31')['metrics']
    S.check(inside[DENIAL_GREP]['num'] == 1 and inside[DENIAL_GREP]['den'] == 2 and DENIAL_GREP not in outside,
            'test_denials_family_counts_sessions_with_a_denial_per_window: one day', repr((inside.get(DENIAL_GREP), outside.get(DENIAL_GREP))))
    calls['2026-09-04|ccc'] = 1
    found = ef.compute(denials_source(rows, calls), since='2026-09-01', until='2026-09-30')['metrics']
    S.check(found[DENIAL_GREP]['num'] == 1 and found[DENIAL_GREP]['den'] == 3,
            'test_denials_family_counts_sessions_with_a_denial_per_window: second day extends den', repr(found.get(DENIAL_GREP)))


def test_absent_denials_source_publishes_nothing():
    found = ef.compute(source(), today='2026-10-09')
    S.check(not [key for key in found['metrics'] if key.startswith('denials.')]
            and found['coverage']['denials'] == {'absent': 1},
            'test_absent_denials_source_publishes_nothing', repr(found['coverage'].get('denials')))


def test_denials_effect_waits_on_the_session_floor():
    rows = [{'day': '2026-09-10', 'session': 'bbb', 'cat': 'recursive-grep'}]
    calls = {'2026-09-02|aaa': 1, '2026-09-10|bbb': 1, '2026-09-11|ccc': 1}
    src = effect_source(capture('sessions'), [ledger_row(DENIAL_GREP, '2026-09-03')])
    src['denials'] = {'denials': rows, 'calls': calls, 'coverage': {}}
    row = ef.effects(src, today='2026-10-09')[0]
    S.check(row['status'] == 'waiting' and row['after'] == (1, 2) and row['detail'] == 'waiting (2 of 6)',
            'test_denials_effect_waits_on_the_session_floor', repr(row))


def test_corrections_per_prompt_over_the_rows_that_carry_the_field():
    """S3: `outcome.corrections-per-prompt` = corrections per 100 owner prompts, over the rows that carry
    both a `corrections` list and `facts.owner_prompts`. A row without the field is typed absent, never a
    zero-prompt, zero-correction session."""
    src = source()
    rows = [dict(r) for r in src['sessions'][:4]]
    rows[0]['facts'] = {'owner_prompts': 10}
    rows[0]['corrections'] = ['a', 'b', 'c']
    rows[1]['facts'] = {'owner_prompts': 30}
    rows[1]['corrections'] = ['a']
    rows[2]['corrections'] = ['a', 'a']                                    # no facts at all
    rows[3]['facts'] = {'unavailable': 'no transcript on this login'}      # a typed reason, not zero prompts
    rows[3]['corrections'] = ['a']
    src['sessions'] = rows
    found = ef.compute(src, today='2026-09-30')
    m = found['metrics']['outcome.corrections-per-prompt']
    S.check((m['num'], m['den'], m['units']) == (4, 40, 2),
            'test_corrections_per_prompt_over_the_rows_that_carry_the_field', repr(m))
    S.check(found['coverage']['outcome'].get('prompts-absent') == 2
            and 'corrections-absent' not in found['coverage']['outcome'],
            'a row with no facts.owner_prompts is typed absent, never a zero-prompt session',
            repr(found['coverage']['outcome']))


def test_corrections_per_prompt_effect_floor_counts_sessions_not_prompts():
    """S3: the rate's effect floor counts the sessions carrying the field, not the owner prompts they hold,
    so 5 sessions with 100 prompts each still read `waiting`, not a measured 500-prompt window."""
    sessions = [dict(r) for r in capture('sessions')]
    for index, row in enumerate(sessions):
        row['corrections'] = ['a']
        row['date'] = '2026-09-05' if index >= 5 else '2026-09-15'   # the 5 with facts land after the ledger row
        if index < 5:
            row['facts'] = {'owner_prompts': 100}
    src = effect_source(sessions, [ledger_row('outcome.corrections-per-prompt', '2026-09-10')])
    row = ef.effects(src, today='2026-09-30')[0]
    S.check(row['status'] == 'waiting' and row['detail'] == 'waiting (5 of 6)',
            'test_corrections_per_prompt_effect_floor_counts_sessions_not_prompts', repr(row))


def test_rules_family_counts_sessions_and_zero_fills_every_named_rule():
    """S5: one metric per rule named in any row, zero-filled in a window that names it in no session, with
    the sessions that recorded `rule_misses` as the denominator; a `rule.<slug>` finding at 2+ sessions."""
    src = source()
    rows = [dict(r) for r in src['sessions'][:3]]
    rows[0]['rule_misses'] = [{'rule': '.claude/CLAUDE.md', 'kind': 'not-read', 'evidence': 'U1'}]
    rows[1]['rule_misses'] = [{'rule': '.claude/CLAUDE.md', 'kind': 'not-followed', 'evidence': 'the owner said so'}]
    rows[2]['rule_misses'] = [{'rule': 'rules/one-off.md', 'kind': 'not-followed', 'evidence': 'F1'}]
    src['sessions'] = rows
    found = ef.compute(src, today='2026-09-30')
    m = found['metrics']
    S.check((m['rules.claude-claude.md.sessions']['num'], m['rules.claude-claude.md.sessions']['den']) == (2, 3)
            and (m['rules.rules-one-off.md.sessions']['num'], m['rules.rules-one-off.md.sessions']['den']) == (1, 3),
            'test_rules_family_counts_sessions_and_zero_fills_every_named_rule', repr(m))
    S.check(sorted(k for k in found['findings'] if k.startswith('rule.')) == ['rule.claude-claude.md'],
            'a rule named in 2 or more sessions is a finding; a one-session rule is not',
            repr(sorted(found['findings'])))
    later = ef.compute(src, since='2026-09-20', until='2026-09-30')['metrics']
    S.check((later['rules.claude-claude.md.sessions']['num'], later['rules.claude-claude.md.sessions']['den']) == (0, 0)
            and later['rules.rules-one-off.md.sessions']['den'] == 0,
            'a rule named in any row is emitted zero-filled in a window that names it in no session',
            repr(later.get('rules.claude-claude.md.sessions')))
    bare = ef.compute(source(), today='2026-09-30')
    S.check(bare['coverage']['rules'] == {'rule-misses-absent': 8}
            and not [k for k in bare['metrics'] if k.startswith('rules.')],
            'rows without rule_misses are typed absent, never zero-miss sessions',
            repr((bare['coverage'].get('rules'), [k for k in bare['metrics'] if k.startswith('rules.')])))


def test_unrated_and_tier_absent_agent_keys():
    """H3 readers: the two Pass A metrics of the agents family — unrated rows (sampled-out included) over
    every archived row, and rated rows with no recorded tier over the rated rows."""
    src = source()
    rows = [dict(r) for r in src['agents'][:6]]
    for row in rows:
        row.pop('tier', None)
    rows[0]['tier'] = 'executor'
    rows[1]['tier'] = 'fanout'
    rows[2]['tier'] = '   '        # blank is absent
    rows[3]['tier'] = 7            # not blank, not a legal role: the readout calls it unparseable, not absent
    rows[4]['outcome'] = 'unrated'
    rows[5]['outcome'] = 'unrated'
    rows[5]['sampled_out'] = True
    src['agents'] = rows
    m = ef.compute(src, today='2026-09-30')['metrics']
    S.check((m['agents.unrated.all']['num'], m['agents.unrated.all']['den']) == (2, 6)
            and (m['agents.tier-absent.all']['num'], m['agents.tier-absent.all']['den']) == (1, 4),
            'test_unrated_and_tier_absent_agent_keys', repr((m.get('agents.unrated.all'), m.get('agents.tier-absent.all'))))


def test_pins_spec_unrecorded_over_every_archived_row():
    """S9: the `pins` family has one metric — archived dispatches whose launch recorded no `spec`, over
    every archived row of the window. A legal spec is not counted; an illegal value is unrecorded, never
    read as a recorded one."""
    src = source()
    rows = [dict(r) for r in src['agents'][:6]]
    rows[0]['spec'] = 'tight'
    rows[1]['spec'] = 'judged'
    rows[2]['spec'] = 'vague'          # not a legal enum value: unrecorded, never guessed
    rows[3]['spec'] = ''               # present but empty: unrecorded
    src['agents'] = rows
    m = ef.compute(src, today='2026-09-30')['metrics']['pins.spec-unrecorded.all']
    S.check((m['num'], m['den'], m['unit']) == (4, 6, 'agents'),
            'test_pins_spec_unrecorded_over_every_archived_row', repr(m))


def test_pins_spec_unrecorded_reads_a_capture_from_before_the_field():
    """The captured fixture predates `spec`: every row reads unrecorded, so the window is a baseline of
    absence, never a zero that reads as a healthy one."""
    src = source()
    m = ef.compute(src, today='2026-09-30')['metrics']['pins.spec-unrecorded.all']
    total = len(src['agents'])
    S.check(total == 20 and (m['num'], m['den']) == (total, total),
            'test_pins_spec_unrecorded_reads_a_capture_from_before_the_field', repr(m))


def test_pins_metric_does_not_add_an_agents_key():
    """The metric belongs to its own family: recording a spec on every row must not add a single
    `agents.` key, and `pins.` must hold exactly this one metric."""
    baseline = set(ef.compute(source(), today='2026-09-30')['metrics'])
    src = source()
    rows = [dict(r) for r in src['agents']]
    for row in rows:
        row['spec'] = 'tight'
    src['agents'] = rows
    with_spec = set(ef.compute(src, today='2026-09-30')['metrics'])
    S.check(with_spec - baseline == set()
            and [k for k in with_spec if k.startswith('pins.')] == ['pins.spec-unrecorded.all'],
            'test_pins_metric_does_not_add_an_agents_key', repr(sorted(with_spec - baseline)))


# The planted capture, cut from the September archive. Each session row keeps only the fields
# the readers here use; `notes`, `key_takeaway` and `title` are dropped. The label sets keep
# each string with its hand labels; `SomeClass` and `the style resource's` stand where a run
# named one of this machine's types.
DATA = {
    'sessions': [
 {'session_id': 'f34d6daa-67c2-41db-9e1c-42c139978e5e', 'date': '2026-09-11', 'outcome': 'failure', 'pattern': 'A', 'friction': [{'what': 'Digest row 1: quota and effective effort unknown', 'disposition': 'accepted: no inferred quota or effective effort'}, {'what': 'Row 2: Workflow parse error', 'disposition': 'fixed'}, {'what': 'Rows 3,7,8,10,13,14,16: auto-mode denials', 'disposition': 'harness: sync_permissions.md section 8 distinguishes denial layers; further classifier repair blocked on exact-action approval'}, {'what': 'Rows 4,6,9: oversized direct reads', 'disposition': 'fixed'}, {'what': 'Rows 5,11: source search miss or missing paths', 'disposition': 'accepted: absence was not generalized'}, {'what': 'Row 12 and checker-test setup: Windows output encoding errors', 'disposition': 'fixed'}, {'what': 'Row 15: instruction_quality runtime state missing', 'disposition': 'fixed'}, {'what': 'Initial permission recorder missed real classifier calls', 'disposition': 'fixed'}, {'what': 'session_end_check chose newest peer transcript', 'disposition': 'fixed'}, {'what': 'Close-out audit packet build denied', 'disposition': 'harness: exact build_closeout_audit_f34d6daa.py command needs permission review before audit'}, {'what': 'Local context reader returned tool markup; nuance extraction exceeded requested cap and reader context', 'disposition': 'harness: ai-worker extract_session_chat/read_files cap and output-contract investigation needed; nuance recall incomplete'}, {'what': 'Shared overnight active.json changed to another goal', 'disposition': 'harness: overnight_ask_guard.py state ownership needs per-session isolation; peer owns live goal'}], 'corrections': ['User rejected acceptEdits as a low-friction recommendation: their harness-heavy workflow incurs many manual prompts.', 'User demanded actual investigation and repairs after two advice-only responses.', 'User required unattended completion, with questions reserved for genuine user decisions.']},
 {'session_id': 'f7afabad-15ee-4498-a7e1-cca9e2ef13de', 'date': '2026-09-01', 'outcome': 'correction', 'pattern': 'D', 'corrections': ["Consolidation: the plan_check 'Consolidate' phase was deterministic file:line dedup only — user asked why no consolidation agent ran; added review_fanout Merge stage (B8) and a couldNotSatisfy line in spill digests (B9)", 'Orchestrator narration duplicated the same step twice in the visible progress lines; the lane digests were already concise — the fix was the digest contract, not another agent', 'Auto-mode classifier denied gate-loosening harness edits (delegates and driving session alike); user asked for a solution — settings.json allow rules for Edit/Write under .claude/** did NOT bypass it; reverted, recorded as a gotcha, nine edits handed back']},
 {'session_id': '6f634351-a681-48b2-9a7d-cc2cae8210ae', 'date': '2026-09-01', 'outcome': 'correction', 'pattern': 'E', 'corrections': ["ai-worker read_files hung again despite two prior 'fixes' — user demanded a robust fix; root cause was two NEW defects (FastMCP runs sync tools on the event loop; CustomStreamWrapper has no sync close so abandoned streams wedged Ollama's slot), found via netstat/GIN durations after py-spy was unavailable (process dead)", 'Ran the GPU-contending offline probe concurrently with the live verification test — the live test timed out for the wrong reason; live tests own the GPU alone']},
 {'session_id': '4ffca2ea-7c4d-4d46-8a85-46f3eee9ef78', 'date': '2026-09-02', 'outcome': 'clean', 'pattern': None, 'corrections': []},
 {'session_id': 'c981854e-0178-4897-ada9-b14c9705a527', 'date': '2026-09-08', 'outcome': 'correction', 'pattern': 'A', 'friction': [{'what': 'Edit on ~/.claude/settings.json DENIED - instruction_quality not in loaded-skill state', 'disposition': 'accepted: the gate fired as designed; loading the skill is the intended path'}, {'what': "python3 - with an empty heredoc body spun CPython 3.14's _pyrepl forever on Windows (WinError 6 from getheightwidth), writing 2.2 GB to the task output file at ~3.6 MB/s", 'disposition': 'harness: auto-memory/archive/archive_bash_windows_gotchas.md - new entry; product feedback drafted for the uncapped task-output file'}, {'what': 'taskkill //IM python.exe to stop the runaway killed the ai-worker and semantic-search MCP servers (both Python) and did NOT stop the task', 'disposition': 'harness: auto-memory/archive/archive_bash_windows_gotchas.md - new entry naming TaskStop as the instrument'}, {'what': 'Unbounded grep -r over ~/.claude/ ran 18+ min; the `| head -20` cap never fired because only 9 lines were ever emitted', 'disposition': 'harness: CLAUDE.md Tool Routing - correct the clause that recommends `| head -N` as a valid bound'}, {'what': 'ai-worker and semantic-search MCP unavailable for the rest of the session', 'disposition': 'accepted: consequence of the taskkill above; recoverable by restart'}], 'corrections': ["Reported a background grep shell as complete by reading its output file's size and mtime; it had been running 18 minutes and was still walking 3.5 GB of transcripts. User: 'this is literally still running........'", "Described the statusline `5m` badge as meaning the prompt cache had expired. `ttl` is the TIER the last request wrote, not a countdown; expiry lives in `expires_at`. User's question about a live status line exposed it."]},
 {'session_id': '3259384b-d4a7-47b7-8d52-9cded4b98af2', 'date': '2026-09-14', 'outcome': 'failure', 'pattern': 'E', 'friction': [{'what': 'heredoc_escape_guard denied a python heredoc carrying backslashes (11x across the session)', 'disposition': 'accepted: the block is the enforcement; CLAUDE.md Shell Discipline owns the rule and the agent kept ignoring it'}, {'what': 'git_guardrails blocked a harness commit: no tracked proof or stale stamp (7x)', 'disposition': 'fixed'}, {'what': 'recursive grep / recursive delete denied (8x)', 'disposition': 'accepted: guards fired as designed'}, {'what': 'git checkout / stash drop blocked (3x)', 'disposition': 'fixed: ac1fddb54 stopped the guard recommending git stash'}, {'what': 'semantic-search rebuild EBUSY (3x): a live MCP server holds search.db', 'disposition': 'accepted: /reindex_search owns the reap procedure'}, {'what': 'auto-mode classifier denied a Write (Create Unsafe Agents) and an Edit (Self-Modification)', 'disposition': 'accepted: permission boundary'}, {'what': 'instruction_quality gate denied harness edits before the skill was loaded (2x); Workflow denied before orchestration was loaded', 'disposition': 'fixed'}, {'what': 'ad-hoc scripting slips: stale old_string, unread file before Write, bad Grep flag, inline python tracebacks (~28 rows)', 'disposition': 'accepted: one-off authoring errors; F11231 was a RED-first run'}, {'what': 'harness_tests.py in a sparse worktree reported 0 run / 5 excluded and exited 1 (D1 arm burned 12 turns on it)', 'disposition': 'harness: scripts/harness_tests.py absent-EXCLUDED entries now skip (ce, uncommitted)'}], 'corrections': ["Moved toward session end before the original directive and assessment were re-traced; the owner restated 'leaving nothing unturned', and the re-trace found assessment F1 (cross-model over-pin candidates) and F7 (stale T12 ladder claim) still live", 'Handed a blocked recursive cleanup of regenerable indexes to the owner instead of making pattern_enforcer precise and doing the cleanup through it', 'Parked Codex lanes over the provider quota ceiling instead of routing to another eligible transport', "Spent the opening on reviewing the predecessor's instruction cuts instead of driving the main directive", 'Had not read the documents the owner supplied to the predecessor session (assessment, debate transcript)', 'Dispatched two opus executors to apply already-reviewed mechanical restorations (U1081)', 'Did not surface the findings of audit lenses dispatched mid-session; the owner asked what they returned (U7229)', 'First compaction-anchor design would have flattened temporary steering into session-wide directives after compaction (U8424), and a follow-up option restated the digest without telling the model to read it (U8511)', "Cut CLAUDE.md from 30 KB to 13 KB on a word-overlap review that called 31 real rule losses 'reworded' and condensed the owner's verbatim Note; the owner rejected the cut (U16518) and the Note plus 31 rules were restored after a per-file Opus lens audit (57a44004b, 82df71b4a)", "Reported the D1 result (equal quality, 2-3x turns) as a number with 'accept the residual' instead of reading the transcripts for the cause; the owner needed three messages (U19117, U19132, U19294) before the two post-fix rabbit holes and their wording causes were named and fixed (0d552a6b9), then ordered it codified (179577f0b)", 'Dispatched a 33-file converged edit sweep through Agent with a model pin instead of Workflow (orchestration §0): effort inherited, no PINS row; the owner asked why (U21397). Fixed by enforcement: dispatch_mechanism_guard denies a pinned non-exploratory Agent (6e508ddee).', "Counted the 31-rule CLAUDE.md restore as drive output; the owner rejected it as repairing this drive's own regression (U21279).", "Reported the child's turn-1 context as if the harness owned it; the platform floor (user-level MCP schemas, 19k tokens) was never measured until the owner asked what else was done (A6, U21279/U21750)."]},
 {'session_id': 'c9aa75e3-1ec0-4898-866d-4c9f2daf7c7f', 'date': '2026-09-05', 'outcome': 'correction', 'pattern': 'A', 'friction': [{'what': "codex-proxy (luna) sidecar: 7 concurrent lenses all died 'WebSocket upgrade rejected 403'", 'disposition': 'harness: cold memory gotcha_luna_sidecar_concurrency_cap + orchestration SKILL arms line (luna serial)'}, {'what': 'Anthropic 5h session limit killed 12/14 explore lenses mid-fan-out', 'disposition': 'accepted: budget hook already reports 5h pressure; width was 14 at pressure 1.1 — narrow when 5h > 1.3 (existing rule)'}, {'what': 'Edit denied x4 on .claude/plans/<plan>.md: instruction_quality not loaded (3rd session in a row with this class: #263 mirror, #264 mirror, now plan file)', 'disposition': 'harness: exempt .claude/plans/** and .claude/worklog-titles.md in harness_edit_skill_reminder.py (proposed; gate-valve edit needs acceptEdits)'}, {'what': 'Bash `sleep 75` denied by the shape guard', 'disposition': 'accepted: documented shell discipline'}, {'what': "game-engine MCP get_debug_output: 'No active engine process' for an editor launched via Start-Process; machine locked so no screenshot either", 'disposition': 'harness: line added to archive_godot_editorplugin_mainscreen (editor stdout is not user://logs; headless boot captures plugin errors)'}, {'what': 'grep/ls exit 1/2 on zero-match pipelines (x5)', 'disposition': 'accepted: expected exit codes, not failures'}, {'what': "python heredoc with nested quotes failed 'unexpected EOF' (x2)", 'disposition': 'accepted: already documented in CLAUDE.md Shell Discipline; adherence'}, {'what': 'Skill(orchestration_metrics) refused: disable-model-invocation', 'disposition': 'accepted: by design; session_end Phase 3.6 stop-gate (synthetic arms) also needs the user'}, {'what': 'Edited a running bash sidecar runner with sed; loop spliced old/new; my kill loop matched my own shell', 'disposition': 'harness: cold memory gotcha_bash_rereads_running_script'}, {'what': 'review_fanout result spilled >40KB; tool-result file was not JSON at char 0 (wrapper object)', 'disposition': 'accepted: spill discipline worked; parse the wrapper, not the raw file'}], 'corrections': ["Quoted luna sidecar runs in dollars from the CLI envelope's total_cost_usd; the record's costBasis says plan-quota (no marginal cost) — a hot MEMORY.md rule (`-R costUSD truth`) I did not apply", "Called luna 'schema-noncompliant' when my plan-check dispatch passed neither -S (schema) nor -P (stream-json), so the compaction auto-resume could not fire — a dispatcher fault attributed to the model", "Pinned the luna plan-draft arm at low from the ladder's prose cell while the registry's measured thickPlanning cell said high — the contrast arm was a confound"]},
 {'session_id': '00d8a771-c98d-41a8-814a-ff440c801325', 'date': '2026-09-14', 'outcome': 'failure', 'pattern': 'E', 'friction': [{'what': 'Many malformed path, selector, quoting, and command invocations failed before doing useful work.', 'disposition': 'fixed'}, {'what': 'Large transcript and capture reads hit size limits or timeouts.', 'disposition': 'accepted: size guards worked as designed'}, {'what': 'Early Workflow compaction probes failed on trust, permission, daemon, bare-mode, and dispatch setup before reaching compaction.', 'disposition': 'fixed'}, {'what': "The first proxy replacement stopped this session's proxy before checking every process using the shared executable, then Windows rejected the overwrite and the session lost its endpoint.", 'disposition': 'fixed'}, {'what': 'Native Workflow compaction repeatedly lost task state or bypassed detection while narrowing the request shape.', 'disposition': 'fixed'}, {'what': 'Two post-audit full proxy runs hit the same two-second blocked-handshake test deadline under parallel load.', 'disposition': 'fixed'}, {'what': 'Focused RED tests for audit findings failed as intended.', 'disposition': 'fixed'}, {'what': 'The project harness remains 140/146 due to six unchanged peer-owned gate and routing failures.', 'disposition': 'accepted: unrelated peer-owned failures'}, {'what': 'The recursive-delete guard blocked removal of prompt-bearing test traffic.', 'disposition': 'accepted: blocked by permission boundary'}, {'what': 'The local nuance-recall reader rejected the bundled transcript because it exceeded the local context window.', 'disposition': 'fixed'}], 'corrections': ['The user rejected HTTP and sidecar-only containment because the sidecar uses the same Codex WebSocket adapter; the adapter itself had to be repaired.', 'The user identified that the failed restart checked only the intended PID, not every process mapped to the executable, before stopping the session proxy.', 'The user called out that work had stopped before the requested validation and closeout were complete.']}
],
    'agents': [
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L1_core_doctrine', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L2_long_horizon_drives', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L3_orchestration_mechanics', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L4_hooks_injections', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L5_communication', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L6_memory_compaction', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_2f31b742-122', 'run_date': '2026-09-01', 'label': 'L7_skills_deprescribe', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_41053f72-005', 'run_date': '2026-09-02', 'label': 'review:plc-memory-alignment', 'model': 'opus', 'outcome': 'clean'},
 {'run': 'wf_41053f72-005', 'run_date': '2026-09-02', 'label': 'review:plc-evidence-grounding', 'model': 'opus', 'outcome': 'defects'},
 {'run': 'wf_41053f72-005', 'run_date': '2026-09-02', 'label': 'review:plc-instruction-quality', 'model': 'opus', 'outcome': 'clean'},
 {'run': 'wf_41053f72-005', 'run_date': '2026-09-02', 'label': 'review:plc-doctrine-consistency', 'model': 'opus', 'outcome': 'clean'},
 {'run': 'wf_41053f72-005', 'run_date': '2026-09-02', 'label': 'review:consolidate', 'model': 'claude-opus-5', 'outcome': 'clean'},
 {'run': 'wf_077bd725-d07', 'run_date': '2026-09-02', 'label': 'explore:exp-memory', 'model': 'claude-sonnet-5', 'outcome': 'unrated'},
 {'run': 'wf_4797c8c2-9d1', 'run_date': '2026-09-05', 'label': 'plan:stat-dashboard@sonnet', 'model': 'claude-sonnet-5', 'outcome': 'discarded'},
 {'run': 'wf_2cae05f2-1b5', 'run_date': '2026-09-11', 'label': 'combat-astra-review-B', 'model': 'gpt-5.6-sol', 'outcome': 'rework'},
 {'run': 'wf_6362b865-9d7', 'run_date': '2026-09-05', 'label': 'explore:exp-memory', 'model': 'claude-sonnet-5', 'served_model': 'claude-sonnet-5', 'outcome': 'unrated', 'sampled_out': True},
 {'run': 'wf_dd18c483-4f9', 'run_date': '2026-09-24', 'label': 'review:sa-design-semantics', 'model': 'claude-opus-5-5', 'served_model': 'claude-opus-5-5', 'outcome': 'clean', 'note': 'session_audit seats: findings read in full, 38 applied (51f57a897)'},
 {'run': 'wf_543c202b-723', 'run_date': '2026-09-24', 'label': 'a1-lean-tracer', 'model': 'claude-opus-5-5', 'served_model': 'claude-opus-5-5', 'outcome': 'defects', 'note': 'swapped every review lens to lane-review, stripping Bash from lenses whose briefs need git; hotfix 0da3879e7'},
 {'run': 'wf_99a2ff46-59b', 'run_date': '2026-09-29', 'label': 'review:api-consume', 'model': 'claude-sonnet-5-5', 'served_model': 'claude-sonnet-5-5', 'outcome': 'discarded', 'note': "returned no findings and opened no file; basis 'duplicate'"},
 {'run': 'wf_6e471856-f57', 'run_date': '2026-09-24', 'label': 'p1bench:fix-hud-overlap', 'model': 'claude-opus-5-5', 'served_model': 'claude-opus-5-5', 'outcome': 'rework', 'note': "stopped correctly: parallel dispatch barred test/engine runs (parent's error); read-only root-cause trace accepted and reused"}
],
    'labels': {
 'friction': [
 {'text': 'heredoc_escape_guard denied a python heredoc carrying backslashes (11x across the session)', 'labels': ['shell-escape']},
 {'text': 'auto-mode classifier Self-Modification denial on commit and hook edits', 'labels': ['classifier-denied']},
 {'text': 'Anthropic and provider budget bands were unknown', 'labels': []},
 {'text': 'memory commit built in a temporary index despite the hot rule against it', 'labels': ['index-write']},
 {'text': 'Bash heredoc with nested quotes broke parsing (x2)', 'labels': ['shell-escape']},
 {'text': 'ad-hoc scripting slips: stale old_string, unread file before Write, bad Grep flag, inline python tracebacks (~28 rows)', 'labels': []},
 {'text': 'ad-hoc probe scripts exiting 1/2 (wrong paths, empty finds); Edit anchor missing after compaction', 'labels': []},
 {'text': 'git_guardrails deny: stale stamp — a shared proof helper became git-tracked after the runner stamped', 'labels': ['commit-stamp']},
 {'text': 'Shared overnight active.json changed to another goal', 'labels': []},
 {'text': 'Workflow brief-coverage guard: lane-review profile lacks Bash for kfm.py', 'labels': []},
 {'text': 'settings.json direct edit blocked (composed)', 'labels': []},
 {'text': 'Unbounded Read of a 44KB overnight report was blocked', 'labels': []},
 {'text': 'Initial harness and targeted tests returned RED', 'labels': []},
 {'text': 'dispatch before orchestration skill load (F2041)', 'labels': []},
 {'text': 'A harness rerun was denied', 'labels': []},
 {'text': 'recursive-grep deny on grep -rn over named files only (3x)', 'labels': ['recursive-grep']},
 {'text': 'python heredoc on stdin mangles backslash escapes (5+ occurrences)', 'labels': ['shell-escape']},
 {'text': 'unquoted heredoc executed backticked paths as commands (ran .md files as shell scripts)', 'labels': ['shell-escape']},
 {'text': 'Bash: harness commit guard BLOCKED — stale stamp when harness_tests.py was chained in the same call as the commit (2 rows)', 'labels': ['commit-stamp']},
 {'text': 'A proxy-binary grep was denied.', 'labels': []},
 {'text': "S7 executor's HARNESS_ALLOW_TRES_NULLSTRIP merge commit denied by auto mode", 'labels': ['classifier-denied']},
 {'text': 'sidecar dispatch denied until the ladder was read in full (x2)', 'labels': []},
 {'text': "commit guard judged the session cwd index, not the cd'd worktree", 'labels': ['commit-stamp']},
 {'text': 'extract_numbers row mechanics (invalid class, missing basis quote, duplicate row) (6x)', 'labels': []},
 {'text': 'Bash: python heredoc with backslash escapes / nested quotes (23 rows: classifier BLOCKED, cp1252 traceback, unexpected EOF)', 'labels': ['classifier-denied', 'shell-escape']},
 {'text': 'A sidecar capture was parsed as one JSON document and failed with extra data; the follow-up inspection script was denied for PII handling.', 'labels': []},
 {'text': 'Local context reader returned tool markup; nuance extraction exceeded requested cap and reader context', 'labels': []},
 {'text': 'plan-level: TaskStop reported success while 4 processes survived, and reap.py could not prove ownership to clean them up', 'labels': ['kill-denied']},
 {'text': 'A guessed orchestration test path did not exist', 'labels': []},
 {'text': 'git checkout <ref> -- paths blocked as a worktree discard', 'labels': []},
 {'text': 'Bash: harness commit blocked on stale stamp x4', 'labels': ['commit-stamp']},
 {'text': 'commit guard: stale stamps, missing lock rows, ignored tests, stale private index (13x)', 'labels': ['commit-stamp', 'index-write']},
 {'text': 'commit-guard stamp proofs time out at the 120 s cap under peer load (4m28s and 2m46s standalone, both green)', 'labels': ['commit-stamp']},
 {'text': 'Self-Modification classifier denied harness edits (F317,F323,F341,F347,F359)', 'labels': ['classifier-denied']},
 {'text': 'taskkill //IM python.exe to stop the runaway killed the ai-worker and semantic-search MCP servers (both Python) and did NOT stop the task', 'labels': ['kill-denied']},
 {'text': 'auto-mode classifier no verdict', 'labels': ['classifier-denied']},
 {'text': 'recursive grep denied (twice)', 'labels': ['recursive-grep']},
 {'text': 'recursive grep guard denied grep -r (3x)', 'labels': ['recursive-grep']},
 {'text': 'Unbounded grep -r over ~/.claude/ ran 18+ min; the `| head -20` cap never fired because only 9 lines were ever emitted', 'labels': ['recursive-grep']},
 {'text': 'Bash tool collapsed \\\\n in heredoc Python twice (broken string literals)', 'labels': ['shell-escape']},
 {'text': "python3 - with an empty heredoc body spun CPython 3.14's _pyrepl forever on Windows (WinError 6 from getheightwidth), writing 2.2 GB to the task output file at ~3.6 MB/s", 'labels': ['shell-escape']},
 {'text': 'Bash: backtick or nested quote inside a double-quoted command string (F4263, F4860)', 'labels': ['shell-escape']},
 {'text': 'sleep blocked by tool guard', 'labels': ['sleep-wait']},
 {'text': 'Bash `sleep 60; tail ...` blocked as a blocking-wait pattern', 'labels': ['sleep-wait']},
 {'text': 'Bash `sleep 75` denied by the shape guard', 'labels': ['sleep-wait']},
 {'text': 'commit message heredoc in the same call as a guard-blocked commit never ran', 'labels': ['commit-stamp']},
 {'text': 'untracked RED-first proof of an unbuilt hook blocked an unrelated stamp', 'labels': ['commit-stamp']},
 {'text': "recursive-delete guard blocked this session's own scratch output", 'labels': []},
 {'text': 'recursive grep denied (repeated habit)', 'labels': ['recursive-grep']},
 {'text': 'external_models.json wipe in a delegate diff (F7806)', 'labels': []},
 {'text': 'Bash: recursive grep denied (6x across both sessions)', 'labels': ['recursive-grep']},
 {'text': 'reap killed a grid wrapper but not its MSYS child grid', 'labels': ['kill-denied']},
 {'text': 'comparators died at the 32K output cap', 'labels': []},
 {'text': 'rebase conflicts in lock/row files and a peer soft-reset (F7522)', 'labels': []},
 {'text': "kill_guard blocked TaskStop on this session's own sandboxed proof runs (child run_grid read as a billed grid), 4 times", 'labels': ['kill-denied']},
 {'text': 'DeepSeek endpoint down, preflight refused', 'labels': []},
 {'text': 'peer-revert guard false block on a rebuilt commit (F12286)', 'labels': ['commit-stamp']},
 {'text': 'own inline scratch scripts hit null fields (F300,F2956,F3158,F4243,F7800)', 'labels': []},
 {'text': 'Edit denied 3x: load instruction_quality before a harness edit', 'labels': []},
 {'text': 'Peer-revert, number-row, lock-row and unit-size commit guards', 'labels': ['commit-stamp']},
 {'text': 'harness commit blocked by stamp / stage-then-commit ordering (3x)', 'labels': ['commit-stamp', 'index-write']},
 {'text': 'Bash: add+commit in one call denied (F2856,F5658,F10174)', 'labels': ['index-write']},
 {'text': 'auto-mode classifier blocked an explained baseline drop', 'labels': ['classifier-denied']},
 {'text': 'auto-mode classifier denied process inspection (F7320, F7325)', 'labels': ['classifier-denied']},
 {'text': 'recursive grep denied 3x', 'labels': ['recursive-grep']},
 {'text': 'Recursive grep denied (5 more after the first close pass)', 'labels': ['recursive-grep']},
 {'text': 'heredoc Python with \\\\ arrived as \\ (F2554, F5505)', 'labels': ['shell-escape']},
 {'text': 'wait-loop text inside a python heredoc denied', 'labels': ['sleep-wait']},
 {'text': 'Bash: foreground wait / sleep loop denied (4x)', 'labels': ['sleep-wait']},
 {'text': 'sleep-chain waits denied 6 times', 'labels': ['sleep-wait']}
],
 'defects': [
 {'text': "Read-as-load marker import broke the reaper's <50 ms budget; parent made it lazy", 'labels': []},
 {'text': 'lint refused JSON Workflow args as brief text; parent made JSON payloads data', 'labels': []},
 {'text': 'A4-A6 complete, every plan case RED-first and green; one design defect shipped: pool backstop measured from launch at timeoutMs+tail, killing slot waits and wrapper retries as HANG (plan text had the same gap); fixed by parent. 74 min wall.', 'labels': ['parent-spec']},
 {'text': 'swapped every review lens to lane-review, stripping Bash from lenses whose briefs need git; hotfix 0da3879e7', 'labels': []},
 {'text': "seed wrote 1,039 verdict-less placeholder entries that zeroed list's none count; RED was a whole-file import failure; parent removed placeholders and narrowed bare one/single on measured precision", 'labels': []},
 {'text': "T1-T12 correct; skipped the brief's extract_numbers gate, parent wrote 8 rows", 'labels': ['partial-coverage']},
 {'text': "truncation warning fired when only unusable chars followed the cap (false 'cut'); agent noticed and left it; parent fixed RED-first (ff8180f)", 'labels': []},
 {'text': "missed plan's unbased numbers (N=8, seat capacity) against instruction_quality 1; checked arithmetic only; S3 control arm reproduced the miss", 'labels': ['partial-coverage']},
 {'text': 'parameterized unit-test cases split into pseudo-classes (className=Class.Method); fixture shape never exercised it; parent fixed RED-first', 'labels': []},
 {'text': "stopped correctly: parallel dispatch barred test/engine runs (parent's error); read-only root-cause trace accepted and reused", 'labels': ['parent-spec']},
 {'text': 'fold_check: F59 took option 2 (drop treeDigest) but labelled source=finding, failing the mechanical check; X1/X2 cross-plan edits left to session (write scope). Design delta D1-D9 needs round 3.', 'labels': []},
 {'text': "stopped correctly: parallel dispatch barred test/engine runs (parent's error); read-only root-cause trace accepted and reused", 'labels': ['parent-spec']},
 {'text': "judge-scored: about 96% of findings valid but it over-marks critical, and it missed most of the original review's findings", 'labels': ['partial-coverage']},
 {'text': 'cache-first, lock and advisory retirement correct; cache store and cooldown record ran after the lock release, a race its own concurrency case caught as a flake and the job reported unexplained; parent moved both inside the lock (154c821aa)', 'labels': []},
 {'text': "17 FIX on the pre-trim hot set incl. 2 inside the owner's note in CLAUDE.md, which must stay verbatim (template gap, fixed in session_audit_agents)", 'labels': ['parent-spec']},
 {'text': '0 findings; saw the stale skill_load_marker docstring (F5) but did not raise it', 'labels': ['partial-coverage']},
 {'text': 'F7/F9/F10 correct with RED transcripts; its watcher proof created a temp dir inside the repo root (harness_tooling: proofs write only under TMP); parent moved it to a temp PROJECT_ROOT', 'labels': []},
 {'text': 'an Opus-high review found 18 defects (5 major: root identity, tool grant not keyed, loose served-id attestation, persist refusals read as engine refusals, cache exceptions blocking the refusal write); a fix round repaired all', 'labels': []},
 {'text': "code correct with RED transcripts; ended before its brief's pins run finished; parent stamped", 'labels': ['partial-coverage']},
 {'text': "depended on a peer session's uncommitted RESERVED_BUILTINS; parent replaced it with a canonicalOrder subset check", 'labels': []},
 {'text': "judge-scored: about 96% of findings valid but it over-marks critical, and it missed most of the original review's findings", 'labels': ['partial-coverage']},
 {'text': 'all 7 outcomes met, suites green; but grouped on requested pin not served model (one model splits across rows) and dropped non-rated outcomes so columns no longer sum to n; RED partly after the fact', 'labels': ['red-proofs']},
 {'text': 'root cause and fail-closed fix accepted, RED/GREEN real; left the hook-kill path (R1) open though fixable inside the guard, and its proofs wrote to the real log; parent closed both RED-first', 'labels': ['partial-coverage']},
 {'text': 'ended its turn waiting on background runs, no report; resumed as s8-B-r1', 'labels': ['partial-coverage']},
 {'text': '3 of 13 surprise grades wrong on first-party check: semantic_search_scope (CLAUDE.core §2 states restrictToDir), seam guards (SomeClass class doc states the seam), destructive git/delete (system prompt hard-to-reverse rule); harness-stamp group graded as one unit. Ladder, composed, xvfb, GetNode, gate-prefix gaps held.', 'labels': []},
 {'text': "judge-scored: about 96% of findings valid but it over-marks critical, and it missed most of the original review's findings", 'labels': ['partial-coverage']},
 {'text': 'all proofs green on parent rerun; S1 proof written after implementation (not RED-first); bench-grid stall-void dependency surfaced, not resolved', 'labels': ['red-proofs']},
 {'text': 'fixes landed but final message was a wait note, and a new test reused fixture repo c12 (parent fixed)', 'labels': []},
 {'text': "proposed resource blobs put Look before Connector, against the style resource's export declaration order; parent re-authored", 'labels': []},
 {'text': 'work complete and correct after parent review, but the job exited while waiting on its proof battery and wrote no report; parent recovered progress notes and reviewed the diff', 'labels': ['partial-coverage']},
 {'text': 'parent review: wrong plan-check list (S1b for S8, against D27) and the retired --staged stamp rule; fixed in place', 'labels': []},
 {'text': 'blocks fenced as jsonl instead of pairs/groups; retagged by parent, content accepted', 'labels': []},
 {'text': '5 real findings, but checked.basis did not name its mandate (coverage partial)', 'labels': ['partial-coverage']},
 {'text': 'all scoped items done with REDs; F19 has no production RED and F22 consumer repoint blocked by main-checkout uid cache (reported, correct)', 'labels': ['red-proofs']},
 {'text': 'go-deepseek-flash max, cross-family red-team: 13 findings, 12 accepted (1 critical: doc claimed held-out walk done); X12 rejected, wrong premise (protocol Step 4 defers to caller approval)', 'labels': []},
 {'text': 'no build or test run (believed barred by a concurrency rail); G5 2/8 pins and R-RED owed; carried into ready-117', 'labels': ['partial-coverage', 'red-proofs']},
 {'text': 'left test_eval_render red on its new eighth tab; parent fixed two list lines', 'labels': ['red-proofs']},
 {'text': 'all 4 items RED->GREEN, verify 0 failed; duplicated 0.8 restitution literal, fixed by orchestrator (Jmodot 75c277d)', 'labels': []},
 {'text': 'G9/G10/G12 RED-first with mutations and live census; G11 blocked on an out-of-lane daemon defect (correctly reported) but its unreached assertions assumed wrong schemas (armModelId, board run) and it missed the live-vault leak via sealed_cells _root', 'labels': ['partial-coverage']},
 {'text': '8 premise findings cited the packet folder instead of the batch folder for doctrine_commits_mine1.md, so their quotes read UNVERIFIED though the text exists', 'labels': []},
 {'text': "Blind Sol max seat on S28's thorough diff: returned clean on all three lenses, but missed that the over-cap funnel test passes on the old config re-clamp (cap held at 4 both ways), which the S28 judge found. Stopped blocked on the RED-ordering check my brief gave it no shell for.", 'labels': ['parent-spec', 'partial-coverage']},
 {'text': "Spec listed surfaces from memory (parent's defect); delegate left dead code (headroom note, spare slot, _trail alias), printed thirty_day, duplicated the capacity read in _currency_note, and left 4 sibling proofs red; parent fixed all in b0ce12db8", 'labels': ['parent-spec', 'red-proofs']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': 'shipped red proofs; multi-job rail blocked its final harness run', 'labels': ['red-proofs']},
 {'text': 'partial: 11 valid findings but 3 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'partial: 9 valid findings but 2 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'haiku55 xhigh, standing files: 5 of 11 held; 3 FIXes would cut rules from delegate rails or pre-trigger text (reach), 1 undid a deliberate rewrite, 1 claimed an existing gitignored test absent', 'labels': []},
 {'text': 'applied a plugin update to the live machine while a daemon ran; missed sidecar concurrency', 'labels': ['partial-coverage']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': 'converged after 4 plan-check rounds (17, 8, 8, 4 changed units)', 'labels': []},
 {'text': "daemon exit 3 on any refusal broke t2q's expected-refusal probe (walk only covered t3); 3.5h wall; parent fixed RED-first", 'labels': []},
 {'text': 'partial: 7 valid findings but 3 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'partial: 7 valid findings but 3 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'S3b/S3d built and proofs green, but two proofs still planted the composite t1 (test_bench_coverage name case, test_bench_doctor_derived two cases), which it reported as pre-existing reds by comparing against its own HEAD; also edited the main checkout twice through a stray cd (reverted).', 'labels': ['red-proofs']},
 {'text': "28 review findings fixed; bound run killed at 2h; parent's run found 9 reds (existing-task behaviour drift, seam rules, facts.sh re-source) and a live t7 RED->PARTIAL regression", 'labels': ['red-proofs']},
 {'text': "added a fallback resolver that duplicated owed_staged's own default; parent deleted it as dead code", 'labels': []},
 {'text': "brief defect was the parent's: the extractor captured only '>-', so the seat reviewed no real text; rerun as hotset3", 'labels': ['parent-spec']},
 {'text': 'Froze inputs and stopped as briefed on real field additions; its frozen-input guard was order-dependent (core.outcome) and missed the gitignored probes dir read by null_arm.floors, so main stayed red; dispatcher fixed both (437ba340b, follow-up)', 'labels': ['partial-coverage', 'red-proofs']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': 'seat stopped at exhausted-leads with in-lens files unread (e.g. ImpactPhysics.cs past line 437); the failure the seat coverage contract b43aeb270/01f2f6a87 now fails', 'labels': ['partial-coverage']},
 {'text': 'partial: 8 valid findings but 3 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'R1-R4 delivered with RED-first proofs and a real wrapper observation; but its parent-side preflight turned test_launch_memory red on its own branch and the report blamed the layer cite instead; repair folded into launch-facts', 'labels': ['red-proofs']},
 {'text': 'no regression cases for F17/F23/F33 (parent added); did not check the results_data red at HEAD', 'labels': ['red-proofs']},
 {'text': 'R06 mustRead broke ~20 test_sidecar_review_cli fixtures; J4b follow-up', 'labels': ['red-proofs']},
 {'text': 'code correct, 9 proofs green; two RED-first gaps self-reported (selection materializer, metrics join); parent closed them by mutation runs', 'labels': ['red-proofs']},
 {'text': 'session audit F4 F6 F7 F8 fixed in audit-fixes', 'labels': []},
 {'text': 'Sonnet 5.5 medium fold: 10/10 ruled; F7 spliced into a sentence and broke its grammar, and the exit-4 store bullet was left contradicting the new contract; session fixed both', 'labels': []},
 {'text': "judge-scored: about 96% of findings valid but it over-marks critical, and it missed most of the original review's findings", 'labels': ['partial-coverage']},
 {'text': 'shipped two F6 scenes that played the same seed-1 floor; orchestrator collapsed them (7acefdddc)', 'labels': []},
 {'text': "killed by the 5h session limit during Closing step 1, after Slices 4-7 went GREEN; parent over-dispatched past the posture line's narrow-fan-out signal; work in tree, reconstructed by p8-close wf_83ac4558-98b", 'labels': ['parent-spec']},
 {'text': 'rails for the new verify/fold stages fall back (guard injects review/<tier> only); pure engine names coding-layer plan_fold.md; lock rows left to parent', 'labels': ['partial-coverage']},
 {'text': 'sidecar hit 14400s cap waiting on gate (runtime-slot contention: 3 parallel gates); fix + merge done at 713a6f496, gate attempt 4 detached; parent finishes', 'labels': ['partial-coverage']},
 {'text': 'cut G1-G02 would have removed the only statement of two architecture_brainstorm constraints (line 65); G1-G09 named the wrong home; both corrected in reconcile_gap.json', 'labels': []},
 {'text': "judge-scored: about 96% of findings valid but it over-marks critical, and it missed most of the original review's findings", 'labels': ['partial-coverage']},
 {'text': '12 findings, 12 verified (S3 fixed-since by T1, S11 partly), 0 FP, 5 unique, 10 consumer rework; resumed seat failed review_result engagement check (unproven), findings salvaged by hand', 'labels': []},
 {'text': "missed plan's unbased numbers (N=8, seat capacity) against instruction_quality 1; checked arithmetic only; S3 control arm reproduced the miss", 'labels': ['partial-coverage']},
 {'text': "All 11 doc edits per spec with grep dispositions and number rows; one wording defect: regression_gate.md exit-6 row advised 're-run when less loaded' for HELD_REFUSED/SKIP_RECOMPILE_IGNORED harness faults; fixed by parent.", 'labels': []},
 {'text': "D19: VOID cells rendered blank (board halt); merged void reasons lost the owner's em dashes; test_bench_lifecycle (f) red at HEAD after commit, fixed in m4.3 job", 'labels': ['red-proofs']},
 {'text': 'P8 closing 1-3 verified; shipped a run_test_suite.ps1 long-label hash fix with no tracked proof, so it cannot commit until one lands', 'labels': ['red-proofs']},
 {'text': 'C25 claims 8 test files reference proto_challenge but lists 6; rest verified (C24 inbound edges confirmed by git grep)', 'labels': []},
 {'text': 'test host stack overflow in its worktree plus lock timeouts; RED read-only; carried into ready-118', 'labels': []},
 {'text': "M4.3+M4.4 both green, thorough report (RED quotes, function table, 6 could-not-satisfy). One defect found in review: schema_for short-circuited on an absent seed tree before checking the cell's own schema (fixed parent-side, RED-first). Caught a real red proof at HEAD (test_bench_lifecycle f). 121 min, 444 turns, 156M cache reads.", 'labels': []},
 {'text': 'posture clause named an unavailable plan; fixed inline RED-first before commit', 'labels': []},
 {'text': 'A1-A3 implemented with RED/GREEN but left uncommitted: full battery lost 2-4 proofs to load timeouts on 3 runs; environment, not code', 'labels': []},
 {'text': 'Sonnet 5.5 high: 6 slices green and live run clean, but review found 16 FIX defects incl. 3 broken tracked tests it never ran, crash paths, an uncurated verdict persisted, JS largely untested; 2 REDs unrecoverable', 'labels': ['red-proofs']},
 {'text': 'sound plan with measured prototypes; K2 stale (S1 already landed) and the planted-violation step switched branches in a shared checkout; parent fixed', 'labels': []},
 {'text': 'parent review: --staged stamp, no job classes (Job B is ambiguous), and E2 lacked the owner-request override that rule_number_guard needs; fixed in place', 'labels': []},
 {'text': "parent review: wrong plan-check list, --staged stamp, and 'every job clearly defined' where Job C is ambiguous under orchestration §5; fixed in place", 'labels': []},
 {'text': "S3 R3 fold: applied F5/F10 without verified evidence (fold_check fail) and extended E18's channel design a third time; parent ruled E18 instead", 'labels': []},
 {'text': "3 proofs under 55 s solo, PASS sets identical, planted REDs shown. Defect: moved the two upper wall-clock-bound cases into the case pool, against _case_pool's contract and the file's own comment; reverted parent-side (exec_battery 46 -> 52 s solo). 22 min, 86k tokens.", 'labels': []},
 {'text': "consumed into 91589fb60 after the parent re-verified every pin green: the job ended its turn 'Waiting on pins-after' without the final run. Tier fit: opus on a tight spec, sonnet was the cell", 'labels': ['partial-coverage']}
]
},
    'labels_holdout': {
 'friction': [
 {'text': 'rm -rf of an owned unpushed worktree denied three times with no reason (F8894, F8905, F8907)', 'labels': []},
 {'text': 'merge -F value read as the ref by the commit-scope guard (F12219)', 'labels': ['commit-stamp']},
 {'text': 'long-run brief advisory fired on file names in review briefs', 'labels': []},
 {'text': 'own Bash script slips (KeyError, quoting, /tmp path, tail flag)', 'labels': []},
 {'text': 'own throwaway command errors (F132, F201, F596, F980, F1087, F1453, F2314, F3029, F3707, F5986, F6731, F7651, F8053, F9214, F12305, F12405, F12515)', 'labels': []},
 {'text': 'delegate polling under the wait guard', 'labels': ['sleep-wait']},
 {'text': 'Workflow pin outside the lens tier (sa-harness-quality pinned opus, twice)', 'labels': []},
 {'text': 'git add of .claude/tests refused as ignored (2x)', 'labels': []},
 {'text': 'git branch -D denied for fully applied branches', 'labels': []},
 {'text': 'half-removed worktree stub cannot be deleted under the recursive-delete guard', 'labels': []},
 {'text': 'rule-number guard blocked three commits', 'labels': ['commit-stamp']},
 {'text': 'one-off script errors (8)', 'labels': []},
 {'text': "harness push blocked: harness_tests --close --repo <worktree> wrote its stamp into the runner's checkout, not the target the push guard reads (and overwrote main's stamp)", 'labels': ['commit-stamp']},
 {'text': 'client refill breaker ended 3 of 3 t11 runs at -K 100000', 'labels': []},
 {'text': 'TaskStop needs the owner to name a running task', 'labels': ['kill-denied']},
 {'text': 'harness commit blocked by a red bound proof or stale staged blob (6x)', 'labels': ['commit-stamp']},
 {'text': 'guards that named the ready retry (F347, F1346, F2301, F2379, F3304, F3535, F5455, F3577, F5684)', 'labels': []},
 {'text': 'misc exit-1 greps, missing paths, index.lock, enum typo (F723,F1360,F3795,F4642,F9814,F10200,F10222,F11511)', 'labels': []},
 {'text': 'recursive-delete guard on worktree removal needed a named owner pick', 'labels': []},
 {'text': 'commit guards: unstamped harness files, invalid Triage values, number rows without basis', 'labels': ['commit-stamp']}
],
 'defects': [
 {'text': 'plan check found 22 findings, 1 owner fork; folded', 'labels': []},
 {'text': 'haiku55 high: 3 of 8 held; proposed deleting a rule+pointer pair that already discloses progressively', 'labels': []},
 {'text': 'reported no stale callers, but session_audit.md:182 still named REPRO-FAIL; the dispatcher synced it', 'labels': ['partial-coverage']},
 {'text': 'partial coverage: read part of its diff batch (unread ranges re-audit owed); findings usable after verification, a few false (F63, F136)', 'labels': ['partial-coverage']},
 {'text': '6/6 fixes RED->GREEN, but ran only targeted proofs: broke test_succession_units F31/F33, and the panel-log fix dropped run.log for owning launches (corrected 8949119d2)', 'labels': ['red-proofs']},
 {'text': 'partial: 7 valid findings but 4 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'stopped on anthropic-secondary 5-hour limit after 211 calls with no report; edits left unreviewed (launch-side cause: pick ignored the 5-hour window, since fixed)', 'labels': ['partial-coverage']},
 {'text': 'partial: 4 valid findings but 4 self-reported gaps (subject files not opened past the diff); residue owed by the review ledger', 'labels': ['partial-coverage']},
 {'text': 'seam fix sound and RED-first; left 6 merge-scope hooks on the stale index (flagged 4, missed tres_nullstrip and tres_script_strip); caller cases written after the edits. Parent added shared arm E (RED on 5 guards) and fixed all 6.', 'labels': ['partial-coverage', 'red-proofs']},
 {'text': 'owner: Yes, re-rate. Sealed v3.0 items carry two pre-fix CLAIMs (F2.3 drops its Edgar sentence, F3.8 adds the false RNG-wrapper clause) that holdout commits ba0e9c5/f481aa7 had fixed in v2.x; its byte-equality check and the ledger row claimed all 44 rows equal to v2.4. Caught by the t1e/t1d PERFECT probes.', 'labels': []},
 {'text': '16 docs migrated; 2 claims post-check lines could not run (claudemd_compact, pr_pipeline conflicts), hand-written rev manifests, an args.ledger.head field the engine lacks, bare later_rounds.md cites (fixed in 2ab91b65b)', 'labels': []},
 {'text': "15 findings applied with passing proofs, but nothing committed and the bound --for gate never ran: its worktree's git metadata was removed mid-run by an outside gc/prune (external cause); O3 deferred on the model-name guard. Rebuilt and committed by the parent.", 'labels': ['partial-coverage']},
 {'text': "did no work: parent's 6-job dispatch rail barred harness_tests the brief required", 'labels': ['parent-spec', 'partial-coverage']},
 {'text': 'partial BENCH_ROOTS override broke lib.sh sourcing (4 reconstruct reds it did not run); MSYS_NO_PATHCONV path; hard-coded live roots. Parent fixed RED-first', 'labels': ['red-proofs']},
 {'text': 'R09 broke plan-1 replay; J2b reverted it', 'labels': []},
 {'text': 'created and switched the shared checkout to a new branch to commit; work itself correct with RED proof', 'labels': []},
 {'text': 'closeCheck used Date.now() and Math.random(), which the Workflow runtime forbids; its node proof did not emulate the runtime, so live run (a) failed', 'labels': ['red-proofs']},
 {'text': 'partial: 509 caller repairs and live-citation admission landed with 77 GREEN and a green 12-proof close, but R03 still refuses (live 71, cold 5,397); the remaining rows are code/schema/validator contract migrations; one Sol lane hit 240K of its 242K window and compacted once in 3.9 h', 'labels': ['partial-coverage']},
 {'text': 'G5/G7 walks green, 57/57 bound; review found 3 critical (hard-coded task sets missed by G7 route, rederive silent skip) and a STALE-over-RED ordering regression', 'labels': []},
 {'text': "5 items landed with proofs, but it left _bench_sandbox's copy list without CSharpSource.ps1 and called the queue-scenario failures pre-existing without checking HEAD; both found and fixed by the orchestrator", 'labels': ['partial-coverage']},
 {'text': 'applied 26; 3 marked applied failed the mechanical check (reworded); F1 conflict deferred correctly', 'labels': []},
 {'text': 'coverage failed (gap named no listed item); 5 of 8 findings applied as variants; a lone Haiku review seat is below the ladder floor', 'labels': ['partial-coverage']},
 {'text': '36 edge-case defects incl. 2 critical (non-transactional promotion, re-judge resume re-pays); broke test_bench_walk_probes and mislabelled it HEAD-red; fix round s2c', 'labels': ['red-proofs']},
 {'text': 'ended turn with its harness run pending; parent ran bound proofs', 'labels': ['partial-coverage']},
 {'text': 'made pre-identity and standing-instrument records ready again (mass paid re-judge on next campaign daemon) and rewrote the F11 assertions to match; parent restored', 'labels': []},
 {'text': 'Doctrine collapses correct and obligation-preserving; ran git stash in the shared checkout against the brief, which reset a stale index entry (no peer work lost).', 'labels': []},
 {'text': 'C1-C2 green; parent fixed: duplicated 30 s budget (second home, flagged by job) and durations read on every woken Bash call', 'labels': []},
 {'text': "sentence cut in harness_tooling.md fused two sentences ('--all`.A bound'); fixed in parent", 'labels': []},
 {'text': 'G4/G6/D9 green with mutations; template path fatal on failure and stub diverges from real plugin; bound run unfinished (2h tool limit)', 'labels': ['partial-coverage']},
 {'text': "stopped on the secondary plan's 5h limit with partial uncommitted work; build-j1c on codex continued it", 'labels': ['partial-coverage']},
 {'text': 'applied F73/F80 against deliberate fail-closed design that E24 proves; parent reverted; rest clean', 'labels': []},
 {'text': "work complete and proven, but router ran unguarded in pre_bash_dispatch (a fault skipped every guard); orchestrator fixed RED-first; did not commit (branch-first tool rule conflict, brief's fault)", 'labels': ['parent-spec']}
]
},
}


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            S.guard(name, fn)
    for failure in S.failures:
        print('FAIL detail: ' + failure)
    print('%d checks, %d failures' % (S.count, len(S.failures)))
    sys.exit(bool(S.failures))
