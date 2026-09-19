#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('sidecar_drift_subject', ROOT / '.claude/tools/sidecar_fanout.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class DriftMessageTests(unittest.TestCase):
    def invoke(self, compare, moved):
        with tempfile.TemporaryDirectory(prefix='sidecar-drift-') as tmp:
            planned = ('fixture', ['launcher'], Path(tmp) / 'out.json')
            result = {'fixture': {'exit': 0, 'stdout': str(planned[2]), 'frozenInput': {'held': not moved}}}
            with patch.object(subject, 'load_jobs', return_value=[{}]), \
                 patch.object(subject.model_registry, 'load', return_value={}), \
                 patch.object(subject, 'plan_job', return_value=planned), \
                 patch.object(subject, 'run_jobs', return_value=result), \
                 patch.object(subject, 'frozen_input_error', return_value=None), \
                 patch.object(subject, 'freeze_fingerprint', return_value='same'), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                code = subject.main(['jobs.json', '--out-dir', tmp] + (['--compare'] if compare else []))
            return code, output.getvalue()

    def test_author_changes_are_not_an_invalid_comparison(self):
        code, text = self.invoke(compare=False, moved=True)
        self.assertEqual(code, 0)
        self.assertNotIn('FROZEN INPUT', text)
        self.assertNotIn('not comparable', text)
        self.assertNotIn('Restore the worktree', text)
        self.assertIn('changed', text.lower())

    def test_changed_comparison_still_fails_without_destructive_recovery_advice(self):
        code, text = self.invoke(compare=True, moved=True)
        self.assertEqual(code, 1)
        self.assertIn('comparison', text.lower())
        self.assertNotIn('Restore the worktree', text)

    def test_unchanged_comparison_passes(self):
        code, text = self.invoke(compare=True, moved=False)
        self.assertEqual(code, 0)
        self.assertIn('1/1 exited 0', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
