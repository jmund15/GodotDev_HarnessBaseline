"""S16/S30b-L5 proofs for hooks/_hook_state.py, hooks/bash_backslash_fidelity.py,
hooks/hand_pricing_advisory.py, hooks/plan_memory_reminder.py, hooks/rule_number_guard.py,
hooks/run_call_router.py and hooks/skill_load_marker.py.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'
sys.path.insert(0, str(HOOKS))
import _hook_state as state
import bash_backslash_fidelity as fidelity
import hand_pricing_advisory as pricing
import plan_memory_reminder as plans
import rule_number_guard as numbers
import run_call_router as router
import skill_load_marker as marker


def test_machine_once_serializes_processes_and_retires_old_versions(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    code = (f'import sys; sys.path.insert(0, {str(HOOKS)!r}); import _hook_state; '
            'print(_hook_state.fire_once_machine("1:win32"))')
    children = [subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for _ in range(2)]
    answers = []
    for child in children:
        out, err = child.communicate(timeout=20)
        assert child.returncode == 0, err
        answers.append(out.strip())
    assert sorted(answers) == ['False', 'True']
    assert state.fire_once_machine('1:linux')
    assert state.fire_once_machine('2:win32')
    saved = json.loads((tmp_path / 'machine.json').read_text())
    assert set(saved['fired_once']) == {'2:win32'}


def test_fidelity_fact_is_machine_wide_and_survives_compaction(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(fidelity, 'client_version', lambda _: 'new')
    monkeypatch.setattr(fidelity, 'lookup', lambda _: None)
    monkeypatch.setattr(fidelity, 'newest_on_platform', lambda: None)
    payload = {'tool_name': 'Bash', 'session_id': 'first', 'tool_input': {'command': r"printf 'x\\y'"}}
    assert fidelity.evaluate(payload)['additionalContext']
    state.clear_compaction_keys('first')
    assert fidelity.evaluate(dict(payload, session_id='second')) is None


def test_pricing_once_per_compaction(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    payload = {'tool_name': 'Bash', 'session_id': 'pricing',
               'tool_input': {'command': 'python3 -c "print(modelUsage)"'}}
    def call():
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        pricing.main()
        return capsys.readouterr().out
    assert 'run_cost.py' in call()
    assert call() == ''
    state.clear_compaction_keys('pricing')
    assert 'run_cost.py' in call()


def test_plan_domain_set_dedupes_across_files_and_omits_loaded_skills(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    state.write_json_atomic(state.state_path('plans'), {'skills_loaded': ['worklog_reference']})
    content = 'Obsidian design doc vault ' + 'word ' * 60
    def call(name, body):
        path = tmp_path / '.claude' / 'plans' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return plans.process({'tool_name': 'Write', 'session_id': 'plans',
                              'tool_input': {'file_path': str(path), 'content': body}})
    first = call('one.md', content)
    assert first and 'worklog_reference' not in first['context']
    assert call('two.md', content) is None
    assert call('one.md', 'refactor ' + 'word ' * 60)


def test_delegate_background_advice_once_per_compaction(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    payload = {'tool_name': 'Bash', 'agent_id': 'child', 'session_id': 'router',
               'tool_input': {'command': 'dotnet build'}}
    assert router.route(payload)[1]
    updated, notes = router.route(payload)
    assert updated['run_in_background'] is True and notes == []
    state.clear_compaction_keys('router')
    assert router.route(payload)[1]


def test_ladder_once_per_compaction(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(marker, 'ladder_note', lambda: 'ladder')
    payload = {'tool_name': 'Skill', 'session_id': 'ladder', 'tool_input': {'skill': 'orchestration'}}
    assert marker.process(payload) == 'ladder'
    assert marker.process(payload) == ''
    state.clear_compaction_keys('ladder')
    assert marker.process(payload) == 'ladder'


def test_bounded_read_uses_actual_coverage_not_requested_limit():
    path = str(HOOKS.parent / 'skills' / 'instruction_quality' / 'SKILL.md')
    def call(start, count, total, **inputs):
        return marker.read_skill_name({'tool_name': 'Read',
            'tool_input': {'file_path': path, 'limit': 2000, **inputs},
            'tool_response': {'type': 'text', 'file': {'startLine': start, 'numLines': count, 'totalLines': total}}})
    assert call(1, 104, 104) == 'instruction_quality'
    assert call(1, 104, 104, offset=0) == 'instruction_quality'
    assert call(1, 40, 104) == ''
    assert call(2, 104, 104) == ''
    assert call(1, True, 1) == ''


def test_number_fact_once_but_new_number_fires(tmp_path, monkeypatch):
    sys.path.insert(0, str(HOOKS.parent / 'tests'))
    import test_rule_number_guard as fixture
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    repo = fixture.mkrepo(str(tmp_path), 'repo')
    fixture.write(repo, '.claude/rules/x.md', '# X\n\nUse two seats.\n')
    fixture.git(repo, 'add', '.claude/rules/x.md')
    payload = {'tool_name': 'Bash', 'session_id': 'numbers', 'cwd': repo,
               'tool_input': {'command': 'git commit -m x'}}
    def call():
        capture = io.StringIO()
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(sys, 'stdout', capture)
        assert numbers.main() == 0
        return capture.getvalue()
    assert 'does this number bound agent behavior?' in call()
    assert call() == ''
    fixture.write(repo, '.claude/rules/x.md', '# X\n\nUse two seats. Use three rounds.\n')
    fixture.git(repo, 'add', '.claude/rules/x.md')
    out = call()
    assert 'three rounds' in out and 'two seats' not in out


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q', '-s']))
