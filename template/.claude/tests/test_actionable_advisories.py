"""S18 proofs for hooks/budget_posture.py, hooks/shell_census.py,
hooks/run_call_router.py and hooks/running_script_edit_guard.py.
"""
import io
import json
import os
import sys
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'
sys.path.insert(0, str(HOOKS))
import budget_posture as budget
import shell_census as shells
import run_call_router as router
import running_script_edit_guard as running


def test_main_background_run_has_no_started_line(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('CLAUDE_CODE_ENTRYPOINT', 'cli')
    payload = {'tool_name': 'Bash', 'session_id': 'main',
               'tool_input': {'command': 'pwsh -File .claude/scripts/regression_gate.ps1'}}
    changed, notes = router.route(payload)
    assert changed['run_in_background'] is True and notes == []


def test_unknown_shell_is_only_a_count_and_peers_watchers_are_silent():
    def row(pid, owner, watcher=False):
        return {'pid': pid, 'age': 90, 'stream': None, 'tail': 'private command',
                'owner': owner, 'watcher': watcher}
    unknown = shells.render([row(111, 'unknown')])
    assert len(unknown.splitlines()) == 1 and '1' in unknown and 'unknown' in unknown
    assert 'pid' not in unknown and 'decide' not in unknown and 'private command' not in unknown
    assert shells.render([row(222, 'other'), row(333, 'own', True)]) == ''
    assert shells.emission_key([row(111, 'unknown')]) == shells.emission_key([row(444, 'unknown')])
    assert shells.emission_key([row(222, 'other')]) == shells.emission_key([])
    assert 'pid 555' in shells.render([row(555, 'own')])


def _budget_input(tmp_path, monkeypatch, **payload_extra):
    now = time.time()
    capture = tmp_path / 'capture.json'
    capture.write_text(json.dumps({'rate_limits': {'captured_at': now,
        'seven_day': {'used_percentage': 1, 'resets_at': now + 604800},
        'five_hour': {'used_percentage': 1, 'resets_at': now + 18000}}}))
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('CLAUDE_CODE_ENTRYPOINT', 'cli')
    monkeypatch.setattr(budget, 'find_state', lambda _: str(capture))
    monkeypatch.setattr(budget, 'dedupe_path', lambda _: str(tmp_path / 'dedupe.json'))
    monkeypatch.setattr(budget, 'prune_stale', lambda: None)
    monkeypatch.setattr(budget, 'seat_transport', lambda: 'anthropic')
    monkeypatch.setattr(budget, 'seat_plan', lambda: 'anthropic')
    monkeypatch.setattr(budget, 'cold_plans', lambda: [])
    monkeypatch.setattr(budget, 'other_quota_bits', lambda _: [])
    monkeypatch.setattr(budget, 'warming', lambda *args: [])
    monkeypatch.setattr(budget, 'provider_entry', lambda *args, **kw: 'spendable')
    monkeypatch.setattr(budget, 'sample_usage', lambda *args: None)
    payload = {'session_id': 'budget', **payload_extra}
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, 'stdout', out)
    budget.main()
    return out.getvalue(), capture


def test_delegate_budget_is_silent(tmp_path, monkeypatch):
    text, _ = _budget_input(tmp_path, monkeypatch, agent_id='executor')
    assert text == ''


def test_sdk_child_budget_is_silent(tmp_path, monkeypatch):
    _budget_input(tmp_path, monkeypatch)
    monkeypatch.setenv('CLAUDE_CODE_ENTRYPOINT', 'sdk-cli')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"session_id":"sdk"}'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    budget.main()
    assert out.getvalue() == ''


def test_spendable_first_turn_speaks_before_any_dispatch(tmp_path, monkeypatch):
    text, _ = _budget_input(tmp_path, monkeypatch)
    assert '[budget-posture] spendable' in text


def test_nonspendable_band_emits_without_dispatch(tmp_path, monkeypatch):
    _budget_input(tmp_path, monkeypatch)
    monkeypatch.setattr(budget.quota_bands, 'compute_band', lambda *args: (1, 'Exhausted'))
    monkeypatch.setattr(budget.quota_bands, 'is_exhausted', lambda *args: True)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"session_id":"budget"}'))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    budget.main()
    assert '[budget-posture]' in out.getvalue()


def test_powershell_scripts_are_not_corruptible_edit_targets():
    payload = {'tool_name': 'Edit', 'tool_input': {'file_path': '.claude/scripts/example.ps1'}}
    assert running.process(payload, scan=lambda: '123|1|pwsh -File example.ps1', alive=lambda ids: set(ids)) is None
    assert running.is_guarded_script('.claude/scripts/example.sh')


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-q', '-s']))
