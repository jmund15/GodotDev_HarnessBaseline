#!/usr/bin/env python3
"""Regression proof that the archive's atomic replace retries a transient Windows lock."""
import importlib.util
import os
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
spec = importlib.util.spec_from_file_location("orchestration_metrics_replace_retry", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)

real_replace = os.replace


def locked_for(count):
    calls = {'n': 0}

    def replace(src, dst):
        calls['n'] += 1
        if calls['n'] <= count:
            raise PermissionError(5, 'Access is denied', dst)
        return real_replace(src, dst)
    return replace, calls


class ReplaceRetryTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='om_replace_')
        self.path = os.path.join(self.root, 'archive.jsonl')

    def test_transient_lock_is_retried(self):
        replace, calls = locked_for(2)
        with mock.patch('os.replace', side_effect=replace), mock.patch('time.sleep'):
            om._atomic_replace_bytes(self.path, b'row\n')
        self.assertEqual(calls['n'], 3)
        with open(self.path, 'rb') as fh:
            self.assertEqual(fh.read(), b'row\n')

    def test_persistent_lock_still_raises(self):
        replace, calls = locked_for(10 ** 6)
        clock = {'t': 0.0}

        def tick():
            clock['t'] += 5.0
            return clock['t']

        # replace_path's budget is wall-clock (`wait_s`), not an attempt count.
        with mock.patch('os.replace', side_effect=replace), mock.patch('time.sleep'), \
                mock.patch('time.monotonic', side_effect=tick):
            with self.assertRaises(PermissionError):
                om._atomic_replace_bytes(self.path, b'row\n')
        self.assertGreater(calls['n'], 1)

    def test_rotation_retries_a_transient_lock(self):
        with open(self.path, 'wb') as fh:
            fh.write(b'old\n')
        replace, calls = locked_for(1)
        with mock.patch.object(om, 'ARCHIVE', self.path), \
                mock.patch('os.replace', side_effect=replace), mock.patch('time.sleep'):
            om._rotate_archive()
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(os.path.exists(self.path + '.1'))
        self.assertEqual(calls['n'], 2)


if __name__ == '__main__':
    unittest.main()
