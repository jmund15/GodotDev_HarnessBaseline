#!/usr/bin/env python3
"""Finding bar proof: which findings count at each rigor, the merge rule, and the seat-facing bar text."""
from pathlib import Path
import json
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import finding_bar
import fold_check

LEVELS = ('light', 'normal', 'thorough')
RANK = {level: index for index, level in enumerate(LEVELS)}


def test_rank_matrix_light_normal_thorough():
    for run in LEVELS:
        for tag in LEVELS:
            expected = RANK[tag] <= RANK[run]
            assert finding_bar.counts({'minRigor': tag}, run) is expected, (run, tag)


def test_absent_minrigor_counts_at_every_level():
    for run in LEVELS:
        assert finding_bar.counts({'id': 'F1'}, run) is True, run


def test_unrecognized_minrigor_counts():
    for run in LEVELS:
        assert finding_bar.counts({'minRigor': 'major'}, run) is True, run
        assert finding_bar.counts({'minRigor': ''}, run) is True, run


def test_critical_counts_whatever_its_tag():
    for run in LEVELS:
        assert finding_bar.counts({'critical': True, 'minRigor': 'thorough'}, run) is True, run


def test_ask_counts_whatever_its_tag():
    for run in LEVELS:
        assert finding_bar.counts({'action': 'ASK', 'minRigor': 'thorough'}, run) is True, run


def test_open_plan_counts_whatever_its_tag():
    for run in LEVELS:
        assert finding_bar.counts({'action': 'PLAN', 'options': [], 'minRigor': 'thorough'}, run) is True, run


def test_optionless_plan_counts_without_a_question():
    rows = [{'action': 'PLAN', 'options': []}, {'action': 'PLAN', 'options': ['keep it']},
            {'action': 'PLAN', 'options': ['', '  ']}, {'action': 'FIX'}]
    for row in rows:
        held = fold_check.hold_class(row) == 'open-plan'
        assert finding_bar.counts(dict(row, minRigor='thorough'), 'light') is (True if held else False), row


def test_unknown_run_rigor_counts_everything():
    for tag in LEVELS:
        assert finding_bar.counts({'minRigor': tag}, None) is True, tag
    assert finding_bar.counts({'minRigor': 'thorough'}, 'bogus') is True


def test_strongest_is_lowest_rank():
    assert finding_bar.strongest([{'minRigor': 'thorough'}, {'minRigor': 'normal'}]) == 'normal'
    assert finding_bar.strongest([{'minRigor': 'thorough'}, {'minRigor': 'light'}]) == 'light'


def test_strongest_none_when_a_member_lacks_one():
    assert finding_bar.strongest([{'minRigor': 'light'}, {}]) is None
    assert finding_bar.strongest([{'minRigor': 'light'}, {'minRigor': 'major'}]) is None


def test_set_strongest_pops_the_key():
    row = {'id': 'F1', 'minRigor': 'thorough'}
    finding_bar.set_strongest(row, [{'minRigor': 'light'}, {}])
    assert 'minRigor' not in row, row
    row = {'id': 'F2'}
    finding_bar.set_strongest(row, [{'minRigor': 'thorough'}, {'minRigor': 'normal'}])
    assert row['minRigor'] == 'normal', row


def test_bar_contract_per_level_and_empty_for_none():
    assert finding_bar.bar_contract(None) == ''
    assert finding_bar.bar_contract('normal') == (
        "BAR: this run's rigor is normal. A finding counts at that rigor when it is a defect that forces rework or "
        "a wrong result, or a significant gap. Report every finding the Reporting Filter admits, counting or not, "
        "and set `minRigor` on each to the lowest of light, normal or thorough whose bar admits it; in doubt "
        "between two, take the lower level (light before normal before thorough, so the finding counts at more "
        "runs); never lower a `critical` finding. The bar decides which findings owe a later round and move a "
        "verdict, never what you report or where you look: read and search exactly as widely as your mandate asks.")
    for level in LEVELS:
        text = finding_bar.bar_contract(level)
        assert text.startswith("BAR: this run's rigor is %s." % level), text
        for phrase in ('The bar decides which findings owe a later round and move a verdict, never what you report '
                       'or where you look', 'in doubt between two, take the lower'):
            assert phrase in text, (level, phrase)


def test_run_rigor_returns_none_on_unreadable_record():
    import os
    with tempfile.TemporaryDirectory() as root:
        records = Path(root) / 'records'
        records.mkdir()
        (records / 'broken.json').write_text(json.dumps({'task_id': 'broken', 'rigor': 'bogus'}), encoding='utf-8')
        old = os.environ.get('HARNESS_TASK_RECORD_DIR')
        os.environ['HARNESS_TASK_RECORD_DIR'] = str(records)
        try:
            assert finding_bar.run_rigor('broken', Path(root)) is None
        finally:
            if old is None:
                os.environ.pop('HARNESS_TASK_RECORD_DIR', None)
            else:
                os.environ['HARNESS_TASK_RECORD_DIR'] = old


def _stamp_cli(payload, rigor):
    import os
    import subprocess
    with tempfile.TemporaryDirectory() as root:
        records = Path(root) / 'records'
        records.mkdir()
        (records / 'T1.json').write_text(json.dumps({'task_id': 'T1', 'rigor': rigor}), encoding='utf-8')
        findings = Path(root) / 'findings.json'
        findings.write_text(json.dumps(payload), encoding='utf-8')
        env = dict(os.environ, HARNESS_TASK_RECORD_DIR=str(records))
        done = subprocess.run([sys.executable, str(HERE.parent / 'tools' / 'finding_bar.py'), 'stamp', str(findings),
                               '--task', 'T1'], capture_output=True, text=True, env=env)
        return done, json.loads(findings.read_text(encoding='utf-8'))


def test_stamp_cli_writes_counts_and_prints_the_bar_line():
    rows = [{'id': 'F1', 'minRigor': 'thorough'}, {'id': 'F2', 'minRigor': 'light'}]
    done, written = _stamp_cli(rows, 'normal')
    assert done.returncode == 0, done.stderr
    assert [row['counts'] for row in written] == [False, True], written
    assert done.stdout.strip() == 'Bar: normal', done.stdout
    done, written = _stamp_cli({'findings': rows}, 'normal')
    assert [row['counts'] for row in written['findings']] == [False, True], written


def test_stamp_cli_leaves_rows_unstamped_without_a_rigor():
    done, written = _stamp_cli([{'id': 'F1', 'minRigor': 'thorough'}], 'bogus')
    assert done.returncode == 0, done.stderr
    assert 'counts' not in written[0] and done.stdout.strip() == '', (written, done.stdout)


def test_stamp_cli_tolerates_an_object_without_findings():
    done, _ = _stamp_cli({'other': 1}, 'normal')
    assert done.returncode == 0 and 'Traceback' not in done.stderr, done.stderr


def test_stamp_copies_rows():
    rows = [{'id': 'F1', 'minRigor': 'thorough'}]
    assert finding_bar.stamp(rows, 'normal') == [{'id': 'F1', 'minRigor': 'thorough', 'counts': False}]
    assert 'counts' not in rows[0] and finding_bar.stamp(rows, None) == rows


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_') and callable(value)]
    for test in tests:
        test()
        print('ok', test.__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
