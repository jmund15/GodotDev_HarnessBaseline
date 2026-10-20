#!/usr/bin/env python3
"""Admission table proof: every rigor cell, gate unknown/false, API/CLI and durable task rows."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
import triage
import task_record

RIGORS = ('light', 'normal', 'thorough')
# Explicit expected cells; never computed from the implementation's predicate. Candidate width is
# 'none' (not admitted), 'all' (thorough's full roster) or 'sized' (the session sizes the lenses).
CELLS = (
    ('harness-quality-seat', 'one-context', 'standing', 'decision', None, (True, True, True)),
    ('harness-quality-seat', 'one-context', 'shared', 'decision', None, (False, False, True)),
    ('harness-quality-seat', 'milestones', 'shared', 'decision', None, (False, True, True)),
    ('harness-quality-seat', 'milestones', 'standing', 'decision', None, (True, True, True)),
    ('candidate-lenses', 'one-context', 'external', 'mechanical', True, ('none', 'none', 'all')),
    ('candidate-lenses', 'one-context', 'external', 'mechanical', False, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'one-context', 'external', 'mechanical', None, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'one-context', 'shared', 'decision', None, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'milestones', 'local', 'mechanical', True, ('none', 'none', 'all')),
    ('candidate-lenses', 'milestones', 'local', 'mechanical', False, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'milestones', 'local', 'mechanical', None, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'milestones', 'local', 'decision', None, ('sized', 'sized', 'all')),
    ('candidate-lenses', 'milestones', 'shared', 'mechanical', True, ('sized', 'sized', 'all')),
    ('premises', 'one-context', 'local', 'decision', None, (True, True, True)),
    ('iq-audit', 'one-context', 'local', 'decision', None, (True, True, True)),
)


def test_table_cells():
    for check, size, reach, kind, gate, expected in CELLS:
        fields = triage.Fields(reach=reach, kind=kind, size=size, paths=('.claude/rules/example.md',))
        for rigor, value in zip(RIGORS, expected):
            adm = triage.admits(check, fields, rigor, gate_green=gate)
            if check == 'candidate-lenses':
                assert adm.width == value, (check, fields, rigor, gate, adm, value)
                assert adm.admitted == (value != 'none')
            else:
                assert adm.admitted == value, (check, fields, rigor, gate, adm, value)
                assert adm.width is None
            assert isinstance(adm.reason, str) and adm.reason


def test_boundaries():
    for rigor in RIGORS:
        for size in ('one-context', 'milestones', 'parts'):
            for reach in ('local', 'shared', 'standing', 'external'):
                fields = triage.Fields(reach=reach, size=size, kind='mechanical', paths=('.claude/rules/example.md',))
                assert not triage.admits('harness-quality-seat', fields, rigor).admitted
                assert not triage.admits('premises', fields, rigor).admitted
                assert not triage.admits('iq-audit', fields, rigor).admitted
    for reach, expected in [('local', (False, False, True)), ('external', (False, True, True))]:
        fields = triage.Fields(reach=reach, size='parts')
        for rigor, value in zip(RIGORS, expected):
            assert triage.admits('harness-quality-seat', fields, rigor).admitted == value
    for size in ('milestones', 'parts'):
        for reach in ('shared', 'standing', 'external'):
            for rigor in RIGORS:
                for kind in ('mechanical', 'decision'):
                    assert triage.admits('candidate-lenses', triage.Fields(reach=reach, size=size, kind=kind), rigor,
                                         gate_green=True).width == ('all' if rigor == 'thorough' else 'sized')
    for path in ('notes.md', '.claude/plans/example.md', '.claude/tests/example.md'):
        assert not triage.admits('premises', triage.Fields(paths=(path,)), 'normal').admitted
    invalid = (
        ('unknown', triage.Fields(), 'normal', None),
        ('iq-audit', triage.Fields(), 'unknown', None),
        ('iq-audit', triage.Fields(reach='unknown'), 'normal', None),
        ('iq-audit', triage.Fields(kind='unknown'), 'normal', None),
        ('iq-audit', triage.Fields(size='unknown'), 'normal', None),
        ('candidate-lenses', triage.Fields(), 'normal', 'yes'),
    )
    for check, fields, rigor, gate in invalid:
        try:
            triage.admits(check, fields, rigor, gate_green=gate)
        except ValueError:
            pass
        else:
            raise AssertionError(('invalid admission accepted', check, fields, rigor, gate))


@pytest.mark.parametrize('level', RIGORS)
def test_boundaries_ignore_live_task_rigor(tmp_path, monkeypatch, level):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'ambient-admission-session')
    task_record.open_record('ambient', 'ambient-admission-session', 'Ambient rigor')
    task_record.set_fields('ambient', rigor=level)
    assert triage.rigor(root=tmp_path) == level
    test_boundaries()


def test_record_and_cli(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('admission', 'admit-test', 'Admission')
    fields = dict(reach='local', reversibility='clean', design='settled', grounding='verified',
                  oracle=['gate'], size='one-context', kind='mechanical')
    task_record.record_triage('admission', fields, ['notes.txt'])
    current = triage.current(tmp_path, task_id='admission')
    adm = triage.admits('candidate-lenses', current, 'normal', gate_green=True, task_id='admission')
    assert adm.width == 'none' and not adm.admitted
    rows = task_record.load('admission')['admissions']
    assert len(rows) == 1
    assert rows[0] == dict(check='candidate-lenses', admitted=False, width='none', rigor='normal',
                           reach='local', kind='mechanical', size='one-context')
    cli = subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'admits', 'iq-audit',
                          '--task', 'admission', '--json'],
                         capture_output=True, text=True, encoding='utf-8')
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)['admitted'] is False
    assert len(task_record.load('admission')['admissions']) == 2
    green = subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'admits', 'candidate-lenses',
                            '--task', 'admission', '--gate-green', '--json'],
                           capture_output=True, text=True, encoding='utf-8')
    assert green.returncode == 0 and json.loads(green.stdout)['width'] == 'none', green.stderr
    unknown = subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'admits', 'candidate-lenses',
                              '--task', 'admission', '--no-gate-green', '--json'],
                             capture_output=True, text=True, encoding='utf-8')
    assert unknown.returncode == 0 and json.loads(unknown.stdout)['width'] == 'sized', unknown.stderr
    assert 'admissions' in task_record.open_record('empty', 'admit-test', 'Empty')
    # Legacy records acquire the list through the same locked mutation, without erasing their state.
    task_record._mutate('empty', lambda state: state.pop('admissions'))
    triage.admits('iq-audit', triage.Fields(), 'normal', task_id='empty')
    assert len(task_record.load('empty')['admissions']) == 1


def test_unrecorded_size_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'unknown-records'))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'unknown-size-session')
    unknown = triage.current(tmp_path)
    assert (unknown.kind, unknown.size) == ('decision', 'milestones'), unknown
    task_record.open_record('legacy', 'unknown-size-session', 'Legacy missing size')
    task_record._mutate('legacy', lambda state: state.update(triage={
        'fields': {'reach': 'shared'}, 'paths': ['.claude/rules/example.md']}))
    fields = triage.current(tmp_path, task_id='legacy')
    assert (fields.kind, fields.size) == ('decision', 'milestones'), fields
    for check, expected in [('harness-quality-seat', True), ('candidate-lenses', True),
                            ('premises', True), ('iq-audit', True)]:
        assert triage.admits(check, fields, 'normal', gate_green=True).admitted == expected, check
    task_record._mutate('legacy', lambda state: state['triage']['fields'].update(size='one-context'))
    explicit = triage.current(tmp_path, task_id='legacy')
    assert explicit.size == 'one-context'
    assert not triage.admits('harness-quality-seat', explicit, 'normal').admitted


def main():
    from types import SimpleNamespace
    from unittest.mock import patch
    tests = (test_table_cells, test_boundaries)
    for test in tests:
        test()
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ):
        monkeypatch = SimpleNamespace(setenv=lambda k, v: os.environ.__setitem__(k, v))
        test_record_and_cli(Path(root), monkeypatch)
        test_unrecorded_size_fails_closed(Path(root), monkeypatch)
    print('all admission cells and boundaries pass; task rows and JSON CLI pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
