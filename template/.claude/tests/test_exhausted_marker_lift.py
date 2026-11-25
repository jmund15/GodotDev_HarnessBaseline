"""A live usage-limit marker is confirmed against a reading newer than the marker before it refuses."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude' / p) for p in ('tools', 'scripts', 'hooks')]
import anthropic_capacity_probe as probe
import exhausted_marker as em
import model_registry
import provider_capacity as pc


class MarkerLift(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='marker_lift_')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cache = self.dir / 'capacity.json'
        self.now = time.time()
        self.data = model_registry.load()
        self.plan = model_registry.plans_of('anthropic', self.data)[0]
        self.env = patch.dict(os.environ, {'PROVIDER_CAPACITY_CACHE': str(self.cache),
                                          'SIDECAR_EXHAUSTED_DIR': str(self.dir / 'marks'),
                                          'HARNESS_USAGE_SAMPLES': str(self.cache) + '.samples'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def value(self, used, observed_ago=0):
        value = probe.summarize({
            'five_hour': {'utilization': 5, 'resets_at': self.now + 10000},
            'seven_day': {'utilization': used, 'resets_at': self.now + 3 * 86400},
            'extra_usage': {'is_enabled': False}, 'limits': [],
        }, now=self.now)
        value['transport'] = self.plan
        value['observedAt'] = pc._utc(self.now - observed_ago)
        return value

    def mark(self, written_ago=120, expires_in=1500, **extra):
        path = em.marker_path(self.plan)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {'schemaVersion': 1, 'transport': self.plan, 'writtenAt': int(self.now - written_ago),
                'expiresAt': int(self.now + expires_in), 'resetsAt': None}
        body.update(extra)
        path.write_text(json.dumps(body), encoding='utf-8')
        return path

    def cache_reading(self, used, observed_ago, cooldown=None):
        blob = {self.plan: self.value(used, observed_ago)}
        if cooldown is not None:
            blob[pc.RATE_LIMIT_KEY] = {self.plan: cooldown}
        self.cache.write_text(json.dumps(blob), encoding='utf-8')

    def lift(self, runner):
        return em.lift_if_room(self.plan, data=self.data, now=self.now, runner=runner)

    def test_a_reading_older_than_the_marker_is_replaced_by_a_live_one(self):
        path = self.mark(written_ago=120)
        self.cache_reading(10, observed_ago=240)
        calls = []
        def runner(*args):
            calls.append(args)
            return self.value(40)
        self.assertTrue(self.lift(runner))
        self.assertEqual(len(calls), 1)
        self.assertFalse(path.exists())

    def test_a_reading_newer_than_the_marker_lifts_it_without_a_probe(self):
        path = self.mark(written_ago=300)
        self.cache_reading(10, observed_ago=60)
        def runner(*args):
            raise AssertionError('probe started')
        self.assertTrue(self.lift(runner))
        self.assertFalse(path.exists())

    def test_a_live_refusal_keeps_the_marker(self):
        path = self.mark()
        self.assertFalse(self.lift(lambda *a: self.value(100)))
        self.assertTrue(path.exists())

    def test_a_failed_probe_keeps_the_marker(self):
        path = self.mark()
        failure = {'schemaVersion': 1, 'transport': self.plan, 'costModel': 'plan-quota',
                   'sourceKind': 'live', 'observedAt': pc._utc(self.now), 'status': 'network-error',
                   'error': {'kind': 'network', 'message': 'down'}, 'liveFailure': None,
                   'quota': None, 'balance': None}
        with patch.object(pc, 'RETRY_SLEEP_SECONDS', 0):
            self.assertFalse(self.lift(lambda *a: failure))
        self.assertTrue(path.exists())

    def test_a_rate_limit_cooldown_is_never_probed_and_keeps_the_marker(self):
        path = self.mark()
        self.cache_reading(10, observed_ago=240, cooldown=self.now + 300)
        def runner(*args):
            raise AssertionError('probe started')
        self.assertFalse(self.lift(runner))
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(self.cache.read_text())[pc.RATE_LIMIT_KEY][self.plan], self.now + 300)

    def test_an_auth_marker_and_an_expired_one_are_not_probed(self):
        def runner(*args):
            raise AssertionError('probe started')
        for extra, expires in (({'reason': 'auth'}, 1500), ({}, -5)):
            with self.subTest(extra=extra, expires=expires):
                path = self.mark(expires_in=expires, **extra)
                self.assertFalse(self.lift(runner))
                self.assertTrue(path.exists())

    def test_a_missing_or_unreadable_marker_lifts_nothing(self):
        self.assertFalse(self.lift(lambda *a: self.value(10)))
        path = em.marker_path(self.plan)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{bad', encoding='utf-8')
        self.assertFalse(self.lift(lambda *a: self.value(10)))
        self.assertTrue(path.exists())

    def test_the_cli_exits_zero_only_when_it_lifted(self):
        path = self.mark()
        self.cache_reading(10, observed_ago=5)
        env = dict(os.environ)
        cmd = [sys.executable, str(ROOT / '.claude/tools/exhausted_marker.py'), 'lift', self.plan]
        self.assertEqual(subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60).returncode, 0)
        self.assertFalse(path.exists())
        self.assertNotEqual(subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60).returncode, 0)


class NewerThan(unittest.TestCase):
    def test_reading_probes_when_the_cache_predates_newer_than(self):
        with tempfile.TemporaryDirectory(prefix='newer_than_') as folder:
            cache = Path(folder) / 'capacity.json'
            now = time.time()
            data = model_registry.load()
            plan = model_registry.plans_of('anthropic', data)[0]
            value = probe.summarize({'five_hour': {'utilization': 5, 'resets_at': now + 10000},
                                     'seven_day': {'utilization': 10, 'resets_at': now + 3 * 86400},
                                     'extra_usage': {'is_enabled': False}, 'limits': []}, now=now)
            value['transport'] = plan
            value['observedAt'] = pc._utc(now - 200)
            cache.write_text(json.dumps({plan: value}), encoding='utf-8')
            with patch.dict(os.environ, {'HARNESS_USAGE_SAMPLES': str(cache) + '.samples'}):
                calls = []
                def runner(*args):
                    calls.append(args)
                    fresh = dict(value, observedAt=pc._utc(now))
                    return fresh
                pc.reading(plan, data=data, now=now, cache_path=str(cache), runner=runner)
                self.assertEqual(calls, [])
                pc.reading(plan, data=data, now=now, cache_path=str(cache), runner=runner, newer_than=now - 100)
                self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
