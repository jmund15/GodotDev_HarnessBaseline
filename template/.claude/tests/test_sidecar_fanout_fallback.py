#!/usr/bin/env python3
"""A job that names no plan moves to the next plan with room when the picked plan's launcher refuses
before spawning (capacity exits, nothing billed); a named plan never moves; a refusal after spend never
retries."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sidecar_fanout as sf

TMP = Path(tempfile.mkdtemp(prefix='sf_fallback_'))
PROMPT = TMP / 'p.md'
PROMPT.write_text('body\n', encoding='utf-8')
DATA = {
    'transports': {
        'anthropic': {'costModel': 'plan-quota', 'launcher': '.claude/scripts/anthropic_sidecar.sh'},
        'anthropic-secondary': {'costModel': 'plan-quota', 'planOf': 'anthropic',
                                'launcher': '.claude/scripts/anthropic_secondary_sidecar.sh',
                                'status': {'state': 'available'}},
        'anthropic-third': {'costModel': 'plan-quota', 'planOf': 'anthropic',
                            'launcher': '.claude/scripts/anthropic_third_sidecar.sh',
                            'status': {'state': 'available'}},
    },
    'models': [{'transport': 'anthropic', 'id': 'host-exec-1', 'alias': 'h-exec', 'roles': ['executor']}],
}
JOB = {'label': 'fb', 'alias': 'h-exec', 'promptFile': str(PROMPT), 'kind': 'lens'}


def rooms(table):
    def room_of(transport, data, read, now):
        value = table[transport]
        return ({'transport': transport, 'room': value} if value is not None
                else {'transport': transport, 'room': None, 'why': 'exhausted'})
    return patch.object(sf, 'plan_room', room_of)


def launcher_of(argv):
    return argv[1]


class PlanFallback(unittest.TestCase):
    def plan(self, job):
        with contextlib.redirect_stderr(io.StringIO()):
            return sf.plan_job(dict(job), DATA, TMP, False)

    def test_unnamed_job_records_the_other_plans_with_room_roomiest_first(self):
        sf.FALLBACKS.clear()
        with rooms({'anthropic': 0.5, 'anthropic-secondary': 2.0, 'anthropic-third': 1.0}):
            _label, argv, _out = self.plan(JOB)
        self.assertTrue(launcher_of(argv).endswith('anthropic_secondary_sidecar.sh'))
        self.assertEqual([plan for plan, _l in sf.FALLBACKS['fb']], ['anthropic-third', 'anthropic'])
        self.assertTrue(sf.FALLBACKS['fb'][0][1].endswith('anthropic_third_sidecar.sh'))

    def test_a_plan_without_room_is_no_fallback(self):
        sf.FALLBACKS.clear()
        with rooms({'anthropic': None, 'anthropic-secondary': 2.0, 'anthropic-third': 1.0}):
            self.plan(JOB)
        self.assertEqual([plan for plan, _l in sf.FALLBACKS['fb']], ['anthropic-third'])

    def test_a_named_plan_records_no_fallback(self):
        sf.FALLBACKS.clear()
        with rooms({'anthropic': 0.5, 'anthropic-secondary': 2.0, 'anthropic-third': 1.0}):
            self.plan(dict(JOB, plan='anthropic'))
        self.assertEqual(sf.FALLBACKS, {})

    def run_job(self, calls, waits=(), fallbacks=None, argv_extra=('-X',)):
        """run_jobs over one job whose launcher returns `calls` in turn; -> (result, argvs, stderr)."""
        out = Path(tempfile.mkdtemp(prefix='run_', dir=TMP))
        stream = out / 'fb.stream'
        argv = [sys.executable, 'primary.sh', '-l', 'fb', '-R', str(out / 'fb.record.json'),
                '-P', str(stream), *argv_extra]
        seen = []

        def call(a, **_kw):
            seen.append(list(a))
            # The launcher truncates -P once it passes its last gate, before the model runs: a written
            # stream is what marks a detached exit as a mid-run stop rather than a pre-spawn refusal.
            stream.write_text('', encoding='utf-8')
            return calls[len(seen) - 1]

        def admitted(label, child, env=None, argv=None):
            return child()

        err = io.StringIO()
        with patch.object(sf.subprocess, 'call', side_effect=call), \
                patch.object(sf, 'supervised_launch', admitted), \
                patch.object(sf, '_wait_detached', side_effect=list(waits) or [(0, False)] * 3), \
                patch.object(sf, 'freeze_fingerprint', return_value=None), \
                contextlib.redirect_stderr(err):
            result = sf.run_jobs([('fb', argv, str(out / 'fb.out.json'))], 1, out, fallbacks=fallbacks)
        return result['fb'], seen, err.getvalue()

    ALT = {'fb': [('anthropic-third', '.claude/scripts/anthropic_third_sidecar.sh'),
                  ('anthropic', '.claude/scripts/anthropic_sidecar.sh')]}

    def test_a_pre_spawn_capacity_refusal_moves_to_the_next_plan_and_says_so(self):
        for code in (5, 6, 8, 10):
            result, seen, err = self.run_job([code, 0], fallbacks=self.ALT)
            self.assertEqual(len(seen), 2, code)
            self.assertEqual(seen[1][1], '.claude/scripts/anthropic_third_sidecar.sh')
            self.assertEqual(seen[1][2:4], seen[0][2:4])  # the rest of the argv is unchanged
            self.assertEqual(result['exit'], 0)
            self.assertIn('anthropic-third', err)
            self.assertIn('exit %d' % code, err)

    def test_the_record_path_of_a_refused_attempt_is_not_reused(self):
        out = Path(tempfile.mkdtemp(prefix='rec_', dir=TMP))
        argv = [sys.executable, 'primary.sh', '-R', str(out / 'fb.record.json'), '-X']
        seen = []

        def call(a, **_kw):
            seen.append(list(a))
            if len(seen) == 1:
                (out / 'fb.record.json.exit').write_text('8\n')   # what the refused claim leaves behind
                return 8
            return 0

        with patch.object(sf.subprocess, 'call', side_effect=call), \
                patch.object(sf, 'supervised_launch', lambda l, child, e=None, a=None: child()), \
                patch.object(sf, '_wait_detached', return_value=(0, False)), \
                patch.object(sf, 'freeze_fingerprint', return_value=None), \
                contextlib.redirect_stderr(io.StringIO()):
            sf.run_jobs([('fb', argv, str(out / 'fb.out.json'))], 1, out, fallbacks=self.ALT)
        self.assertNotEqual(seen[0][seen[0].index('-R') + 1], seen[1][seen[1].index('-R') + 1])

    def test_each_refusal_walks_on_and_the_last_refusal_is_reported(self):
        result, seen, _err = self.run_job([8, 10, 5], fallbacks=self.ALT)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[2][1], '.claude/scripts/anthropic_sidecar.sh')
        self.assertEqual(result['exit'], 5)

    def test_a_job_with_no_fallback_runs_once(self):
        result, seen, _err = self.run_job([8], fallbacks={})
        self.assertEqual((len(seen), result['exit']), (1, 8))
        result, seen, _err = self.run_job([8], fallbacks=None)
        self.assertEqual((len(seen), result['exit']), (1, 8))

    def test_a_failure_that_is_not_a_refusal_never_moves(self):
        for code in (1, 2, 3, 7, 11, 13):
            _result, seen, _err = self.run_job([code], fallbacks=self.ALT)
            self.assertEqual(len(seen), 1, code)

    def test_final_floor_refusal_is_not_a_billed_usage_limit(self):
        result, _seen, _err = self.run_job([10], fallbacks={})
        self.assertTrue(result.get('preSpawnRefusal'))
        text, code = sf.summarize([('fb', [], '')], {'fb': result}, False)
        self.assertIn('refused-before-spawn', text)
        self.assertNotIn('usage-limit  fb', text)
        path, _text = sf.resume_plan([JOB], {'fb': result}, TMP, [])
        self.assertIsNone(path)
        self.assertEqual(code, 1)

    def test_a_refusal_after_spend_never_retries(self):
        # the launch spawned (call exit 0) and the detached run later ended 10: the provider's usage limit, billed
        result, seen, _err = self.run_job([0], waits=[(10, False)], fallbacks=self.ALT)
        self.assertEqual((len(seen), result['exit']), (1, 10))
        self.assertFalse(result.get('preSpawnRefusal'))
        text, _code = sf.summarize([('fb', [], '')], {'fb': result}, False)
        self.assertIn('usage-limit  fb', text)

    def test_a_foreground_launch_never_moves(self):
        # no -X: the exit is the run's own, after any spend
        _result, seen, _err = self.run_job([10], fallbacks=self.ALT, argv_extra=())
        self.assertEqual(len(seen), 1)


if __name__ == '__main__':
    unittest.main()
