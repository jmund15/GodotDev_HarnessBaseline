#!/usr/bin/env python3
"""A fan-out returns a capacity refusal it can already see in the launch call itself.

`sidecar_fanout.admission_refusal` decides, from the capacity readers `pick_plan` uses, whether each job of
a jobs file can be admitted now. `plan_job` and the PreToolUse hook call that one function: a named plan
without room refuses (a pinned plan never falls back), an unpinned job refuses only when no plan of its
route has room, and an unreadable or stale reading decides nothing (the launcher's own gate still runs).
"""
import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude' / part) for part in ('tools', 'scripts', 'hooks')]
import anthropic_capacity_probe as probe
import model_registry
import provider_bands
import provider_capacity
import sidecar_fanout as sf
import sidecar_dispatch_context as hook

TMP = Path(tempfile.mkdtemp(prefix='sf_admission_'))
PROMPT = TMP / 'p.md'
PROMPT.write_text('body\n', encoding='utf-8')
DATA = model_registry.load()
PLANS = model_registry.plans_of('anthropic', DATA)
PAID = PLANS[0]
OTHER = PLANS[1]
ALIAS = next(m['alias'] for m in model_registry.available_models(DATA) if m['transport'] == 'anthropic')


def reading(plan, five=4, seven=10, now=None):
    now = time.time() if now is None else now
    value = probe.summarize({
        'five_hour': {'utilization': five, 'resets_at': now + 13030},
        'seven_day': {'utilization': seven, 'resets_at': now + 2830},
        'extra_usage': {'is_enabled': False}, 'limits': [],
    }, now=now)
    value['transport'] = plan
    return value


def planted(table):
    """A capacity reader answering `table[plan]`: a reading, None (no fresh reading) or an exception to raise."""
    def read(transport, data=None, live=True):
        value = table.get(transport)
        if isinstance(value, Exception):
            raise value
        return value
    return read


def job(label='fix-r3', plan=None, alias=ALIAS):
    out = {'label': label, 'alias': alias, 'promptFile': str(PROMPT), 'kind': 'lens', 'effort': 'medium'}
    if plan:
        out['plan'] = plan
    return out


FLOOR = reading(PAID, seven=99)
SPENT = reading(PAID, seven=100)
ROOMY = reading(PAID, seven=10)


def everyone(value):
    return {name: dict(value, transport=name) for name in PLANS}


def plan(entry, table):
    with patch.object(provider_bands, 'read_capacity', planted(table)), \
            patch.object(sf, 'plan_load', lambda data: {}), \
            contextlib.redirect_stderr(io.StringIO()):
        return sf.plan_job(dict(entry), DATA, TMP, False)


class Admission(unittest.TestCase):
    def test_a_named_plan_without_room_raises_naming_the_job_before_any_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('process started')), \
                self.assertRaises(sf.JobError) as caught:
            plan(job(plan=PAID), {PAID: FLOOR, OTHER: dict(ROOMY, transport=OTHER)})
        text = str(caught.exception)
        for bit in ('fix-r3', '99% dispatch floor', 'seven_day', 'resets', 'plan with room'):
            self.assertIn(bit, text)

    def test_a_spent_named_plan_raises_with_its_window_and_reset(self):
        with self.assertRaises(sf.JobError) as caught:
            plan(job(plan=PAID), {PAID: SPENT})
        text = str(caught.exception)
        self.assertIn('Exhausted', text)
        self.assertIn('resets', text)

    def test_a_named_plan_with_room_plans(self):
        label, argv, _out = plan(job(plan=PAID), {PAID: ROOMY})
        self.assertEqual(label, 'fix-r3')

    def test_an_unreadable_or_stale_named_plan_decides_nothing(self):
        for value in (None, RuntimeError('probe failed')):
            label, _argv, _out = plan(job(plan=PAID), {PAID: value})
            self.assertEqual(label, 'fix-r3')

    def test_an_unpinned_job_moves_to_the_plan_with_room(self):
        table = {PAID: FLOOR, OTHER: dict(ROOMY, transport=OTHER)}
        _label, argv, _out = plan(job(), table)
        launcher = os.path.basename(model_registry.transport_meta(OTHER, DATA)['launcher'])
        self.assertTrue(any(str(part).endswith(launcher) for part in argv), argv)

    def test_an_unpinned_job_with_no_plan_in_room_is_refused_naming_each_plan(self):
        with self.assertRaises(sf.JobError) as caught:
            plan(job(), everyone(FLOOR))
        text = str(caught.exception)
        self.assertIn('no plan of anthropic has room', text)
        self.assertIn(PAID, text)
        self.assertIn(OTHER, text)

    def test_an_unpinned_job_with_an_unreadable_plan_is_not_refused_here(self):
        table = everyone(FLOOR)
        table[OTHER] = None
        self.assertIsNone(sf.admission_refusal([job()], DATA, read=planted(table)))

    def test_one_message_names_every_refused_job_and_leaves_the_rest(self):
        jobs = [job('a', plan=PAID), job('b', plan=PAID), job('c', plan=OTHER), job('d')]
        table = {PAID: FLOOR, OTHER: dict(ROOMY, transport=OTHER)}
        text = sf.admission_refusal(jobs, DATA, read=planted(table))
        self.assertIn('a, b', text)
        for label in ('c', 'd'):
            self.assertNotIn('job %s' % label, text)
        self.assertEqual(text.count('99% dispatch floor'), 1)

    def test_an_unpinned_job_on_a_route_with_one_plan_reads_no_capacity(self):
        single = next(m for m in model_registry.available_models(DATA)
                      if len(model_registry.plans_of(m['transport'], DATA)) == 1)

        def read(*args, **kwargs):
            raise AssertionError('capacity read')
        self.assertIsNone(sf.admission_refusal([job(alias=single['alias'])], DATA, read=read))

    def test_a_job_naming_a_plan_the_route_does_not_have_is_left_to_plan_job(self):
        self.assertIsNone(sf.admission_refusal([job(plan='no-such-plan')], DATA, read=planted(everyone(FLOOR))))

    def test_main_returns_the_refusal_before_the_detached_driver_starts(self):
        jobs = TMP / 'jobs.json'
        jobs.write_text(json.dumps([job(plan=PAID)]), encoding='utf-8')
        err = io.StringIO()
        with patch.object(provider_bands, 'read_capacity', planted({PAID: FLOOR})), \
                patch.object(sf, 'detach_driver', side_effect=AssertionError('driver started')), \
                patch.object(sf, 'run_jobs', side_effect=AssertionError('jobs ran')), \
                contextlib.redirect_stderr(err):
            code = sf.main([str(jobs), '--out-dir', str(TMP / 'out'), '--detach'])
        self.assertEqual(code, 2)
        self.assertIn('99% dispatch floor', err.getvalue())


class HookAdmission(unittest.TestCase):
    def call(self, table, jobs, flags='', background=False, cached=True):
        path = TMP / 'hook_jobs.json'
        path.write_text(json.dumps(jobs), encoding='utf-8')
        command = shlex.join(['python3', '.claude/tools/sidecar_fanout.py', str(path).replace('\\', '/'),
                              '--out-dir', str(TMP / 'hook_out').replace('\\', '/')]) + flags
        payload = {'tool_name': 'Bash', 'session_id': 'adm00001', 'cwd': str(ROOT),
                   'tool_input': {'command': command, **({'run_in_background': True} if background else {})}}
        out = io.StringIO()
        with patch.object(provider_capacity, 'cached', lambda t, data=None, **k: planted(table)(t)), \
                patch.object(provider_capacity, 'reading') as live, \
                patch.dict(os.environ, {'CLAUDE_PROJECT_DIR': str(ROOT), 'HARNESS_SIDECAR_PREFLIGHT': '1'}), \
                patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                patch.object(hook.sys, 'stdout', out), \
                patch.object(hook.model_ladder_gate, 'doctrine_check', return_value=(None, None)), \
                patch.object(hook, 'dispatch_checks', return_value=None), \
                patch.object(hook, 'remember_checks'), \
                patch.object(hook, 'fanout_allowed', return_value=False):
            hook.main()
        live.assert_not_called()
        return json.loads(out.getvalue() or '{}').get('hookSpecificOutput') or {}

    def test_a_backgrounded_fanout_with_a_named_no_room_plan_is_denied(self):
        got = self.call({PAID: FLOOR}, [job(plan=PAID)], background=True)
        self.assertEqual(got.get('permissionDecision'), 'deny')
        reason = got['permissionDecisionReason']
        for bit in ('fix-r3', '99% dispatch floor', 'resets'):
            self.assertIn(bit, reason)

    def test_a_foreground_detach_call_is_denied_the_same_way(self):
        got = self.call({PAID: FLOOR}, [job(plan=PAID)], flags=' --detach')
        self.assertEqual(got.get('permissionDecision'), 'deny')
        self.assertIn('fix-r3', got['permissionDecisionReason'])

    def test_an_unpinned_job_with_another_plan_in_room_is_allowed(self):
        table = {PAID: FLOOR, OTHER: dict(ROOMY, transport=OTHER)}
        self.assertNotEqual(self.call(table, [job()]).get('permissionDecision'), 'deny')

    def test_an_unpinned_job_with_every_plan_refused_is_denied(self):
        got = self.call(everyone(FLOOR), [job()])
        self.assertEqual(got.get('permissionDecision'), 'deny')

    def test_dry_run_and_status_get_no_decision(self):
        for flags in (' --dry-run', ' --status'):
            self.assertEqual(self.call({PAID: FLOOR}, [job(plan=PAID)], flags=flags), {}, flags)

    def test_an_unreadable_or_stale_capacity_gives_no_decision(self):
        for value in (None, RuntimeError('unreadable')):
            got = self.call({PAID: value}, [job(plan=PAID)])
            self.assertNotEqual(got.get('permissionDecision'), 'deny', value)

    def test_the_hook_reads_cached_capacity_only(self):
        self.call({PAID: None}, [job(plan=PAID), job('b')])

    def test_a_refold_launches_no_seat_and_is_left_alone(self):
        got = self.call({PAID: FLOOR}, [job(plan=PAID)], flags=' --refold')
        self.assertNotEqual(got.get('permissionDecision'), 'deny')


if __name__ == '__main__':
    unittest.main()
