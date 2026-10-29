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
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)['admitted'] is False
    assert len(task_record.load('admission')['admissions']) == 2
    green = subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'admits', 'candidate-lenses',
                            '--task', 'admission', '--gate-green', '--json'],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    assert green.returncode == 0 and json.loads(green.stdout)['width'] == 'none', green.stderr
    unknown = subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'admits', 'candidate-lenses',
                              '--task', 'admission', '--no-gate-green', '--json'],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
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


def _rigor_cli(args, env):
    return subprocess.run([sys.executable, str(HERE.parent / 'tools/triage.py'), 'rigor'] + args,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', env=env)


def _rigor_env(tmp_path, session=None):
    env = dict(os.environ, HARNESS_TASK_RECORD_DIR=str(tmp_path / 'records'),
               CLAUDE_PROJECT_DIR=str(tmp_path))
    env.pop('CLAUDE_CODE_SESSION_ID', None)
    if session:
        env['CLAUDE_CODE_SESSION_ID'] = session
    return env


def test_rigor_cli_prints_recorded_then_default(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('cli-recorded', 'cli-session-one', 'Recorded')
    task_record.set_fields('cli-recorded', rigor='thorough')
    task_record.open_record('cli-plain', 'cli-session-one', 'Plain')
    task_record._mutate('cli-plain', lambda state: state.update(triage={
        'fields': {'reach': 'local'}, 'paths': ['docs/unmatched.md']}))
    env = _rigor_env(tmp_path)
    recorded = _rigor_cli(['--task', 'cli-recorded'], env)
    assert recorded.returncode == 0 and recorded.stdout.strip() == 'thorough', recorded.stderr
    default = _rigor_cli(['--task', 'cli-plain'], env)
    assert default.returncode == 0 and default.stdout.strip() == 'normal', default.stderr


def test_rigor_set_with_task_writes_that_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('set-named', 'set-session-one', 'Named')
    env = _rigor_env(tmp_path, session='set-session-one')
    done = _rigor_cli(['--set', 'light', '--task', 'set-named'], env)
    assert done.returncode == 0, done.stderr
    assert task_record.load('set-named')['rigor'] == 'light'


def test_rigor_set_without_task_writes_the_latest_session_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('set-older', 'set-session-two', 'Older')
    task_record._mutate('set-older', lambda state: state.update(updated='2026-01-01T00:00:00'))
    task_record.open_record('set-newer', 'set-session-two', 'Newer')
    task_record._mutate('set-newer', lambda state: state.update(updated='2026-02-01T00:00:00'))
    env = _rigor_env(tmp_path, session='set-session-two')
    done = _rigor_cli(['--set', 'light'], env)
    assert done.returncode == 0, done.stderr
    assert task_record.load('set-newer')['rigor'] == 'light'
    assert task_record.load('set-older').get('rigor') is None


def test_rigor_set_without_any_record_opens_a_session_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    env = _rigor_env(tmp_path, session='abcdef1234567890')
    done = _rigor_cli(['--set', 'light'], env)
    assert done.returncode == 0, done.stderr
    record = task_record.load('session-abcdef12')
    assert record['rigor'] == 'light' and record['title'] == 'session rigor', record


def test_rigor_read_prefers_the_session_level_over_a_later_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'readfirst-session')
    task_record.open_record('read-level', 'readfirst-session', 'Level')
    task_record.set_fields('read-level', rigor='light')
    task_record._mutate('read-level', lambda state: state.update(updated='2026-01-01T00:00:00'))
    task_record.open_record('read-later', 'readfirst-session', 'Later')
    task_record._mutate('read-later', lambda state: state.update(updated='2026-02-01T00:00:00'))
    task_record.open_record('read-other', 'readsecond-session', 'Other session')
    task_record.set_fields('read-other', rigor='thorough')
    assert triage.rigor(root=tmp_path) == 'light'
    assert triage.rigor(root=tmp_path, session_id='readsecond-session') == 'thorough'


def test_rigor_read_keeps_the_last_named_level_when_an_older_level_record_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('named-first', 'named-session', 'First')
    task_record.set_fields('named-first', rigor='light')
    task_record._mutate('named-first', lambda state: state.update(rigorAt='2026-01-01T00:00:00'))
    task_record.open_record('named-last', 'named-session', 'Last')
    task_record.set_fields('named-last', rigor='normal')
    task_record.set_fields('named-first', next_action='unrelated write')
    assert triage.rigor(root=tmp_path, session_id='named-session') == 'normal'


def test_rigor_read_survives_a_write_to_another_record(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_TASK_RECORD_DIR', str(tmp_path / 'records'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    task_record.open_record('survive-level', 'survive-session', 'Level')
    task_record.set_fields('survive-level', rigor='light')
    task_record.open_record('survive-other', 'survive-session', 'Other')
    task_record.set_fields('survive-other', next_action='unrelated write')
    assert triage.rigor(root=tmp_path, session_id='survive-session') == 'light'


def main():
    from types import SimpleNamespace
    from unittest.mock import patch
    if pytest.main(['-q', str(Path(__file__).resolve())]) != 0:
        return 1
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
