"""Cached plan refusals need a live confirmation without bypassing endpoint cooldowns."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude' / p) for p in ('tools', 'scripts', 'hooks')]
import anthropic_capacity_probe as probe
import model_registry
import provider_bands
import provider_capacity as pc
import sidecar_fanout as fanout


class StaleCapacity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='capacity_stale_')
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'capacity.json'
        self.now = time.time()
        self.data = model_registry.load()
        self.plan = model_registry.plans_of('anthropic', self.data)[0]
        self.env = patch.dict(os.environ, {'PROVIDER_CAPACITY_CACHE': str(self.cache),
                                          'HARNESS_USAGE_SAMPLES': str(self.cache) + '.samples'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def value(self, used, age=0):
        value = probe.summarize({
            'five_hour': {'utilization': 5, 'resets_at': self.now + 10000},
            'seven_day': {'utilization': used, 'resets_at': self.now + 3 * 86400},
            'extra_usage': {'is_enabled': False}, 'limits': [],
        }, now=self.now)
        value['transport'] = self.plan
        value['observedAt'] = pc._utc(self.now - age)
        return value

    def store(self, used, cooldown=None, age=240):
        blob = {self.plan: self.value(used, age=age)}
        if cooldown is not None:
            blob[pc.RATE_LIMIT_KEY] = {self.plan: cooldown}
        self.cache.write_text(json.dumps(blob), encoding='utf-8')

    def failure(self, kind):
        return {'schemaVersion': 1, 'transport': self.plan, 'costModel': 'plan-quota',
                'sourceKind': 'live', 'observedAt': pc._utc(self.now), 'status': 'network-error',
                'error': {'kind': kind, 'message': 'endpoint unavailable'},
                'liveFailure': None, 'quota': None, 'balance': None}

    def rate_limited(self):
        value = self.failure('rate-limit')
        value['status'] = 'rate-limited'
        return value

    def test_a_cached_plan_refusal_is_refreshed_before_the_ttl_or_reset(self):
        for used in (99, 100):
            with self.subTest(used=used):
                self.store(used)
                calls = []
                def runner(*args):
                    calls.append(args)
                    return self.value(55)
                got = pc.reading(self.plan, data=self.data, now=self.now, runner=runner)
                self.assertEqual(len(calls), 1)
                self.assertEqual(got['status'], 'available')
                self.assertEqual(pc.cached(self.plan, data=self.data)['quota']['windows'][1]['usedPercent'], 55)

    def test_a_live_refusal_still_refuses(self):
        self.store(100)
        with patch.object(pc, '_run_probe', return_value=self.value(100)) as live:
            got = provider_bands.read_capacity(self.plan, self.data)
        live.assert_called_once()
        self.assertEqual(got['status'], 'exhausted')

    def test_fanout_uses_the_refreshed_reading(self):
        self.store(100)
        alias = next(m['alias'] for m in model_registry.available_models(self.data)
                     if m['transport'] == 'anthropic')
        job = {'label': 'reset-plan', 'alias': alias, 'kind': 'lens', 'plan': self.plan,
               'effort': 'medium'}
        with patch.object(pc, '_run_probe', return_value=self.value(55)) as live:
            refusal = fanout.admission_refusal([job], self.data, load={})
        self.assertIsNone(refusal)
        live.assert_called_once()

    def test_cache_only_admission_defers_both_blocking_plan_statuses(self):
        for used in (99, 100):
            self.store(used)
            with patch.object(pc, '_run_probe', side_effect=AssertionError('probe started')):
                self.assertIsNone(provider_bands.read_capacity(self.plan, self.data, live=False))

    def test_available_readings_still_reuse_the_cache(self):
        self.store(55)
        with patch.object(pc, '_run_probe', side_effect=AssertionError('probe started')):
            self.assertEqual(provider_bands.read_capacity(self.plan, self.data)['status'], 'available')

    def test_cooldown_never_reprobes_or_moves_its_deadline(self):
        until = self.now + 300
        self.store(100, cooldown=until)
        with patch.object(pc, '_run_probe', side_effect=AssertionError('probe started')):
            got = pc.reading(self.plan, data=self.data, now=self.now)
        self.assertEqual(got['status'], 'rate-limited')
        self.assertEqual(got['error']['kind'], 'rate-limit')
        self.assertEqual(json.loads(self.cache.read_text())[pc.RATE_LIMIT_KEY][self.plan], until)

    def test_a_rate_limited_read_with_a_fresh_good_reading_places_the_job(self):
        """A 429 is not exhaustion: the last good reading, its windows unreset, still places the job."""
        self.store(55, age=pc.CACHE_TTL_SECONDS + 100)
        with patch.object(pc, '_run_probe', return_value=self.rate_limited()):
            got = pc.reading(self.plan, data=self.data, now=self.now)
        self.assertEqual(got['sourceKind'], 'cache')
        self.assertEqual((got.get('liveFailure') or {}).get('kind'), 'rate-limit')
        alias = next(m['alias'] for m in model_registry.available_models(self.data)
                     if m['transport'] == 'anthropic')
        job = {'label': 'J1', 'alias': alias, 'kind': 'lens', 'plan': self.plan, 'effort': 'medium'}
        read = lambda name, _data=None: got
        self.assertIsNone(fanout.admission_refusal([job], self.data, read=read, load={}))
        self.assertEqual(fanout.pick_plan('J1', 'anthropic', [self.plan], self.data, read=read,
                                          load={}, quiet=True), self.plan)

    def test_failed_confirmation_does_not_report_stale_exhaustion_as_fact(self):
        for kind in ('rate-limit', 'network'):
            with self.subTest(kind=kind):
                self.store(100)
                with patch.object(pc, '_run_probe', return_value=self.failure(kind)) as live, \
                        patch.object(pc, 'RETRY_SLEEP_SECONDS', 0):
                    got = pc.reading(self.plan, data=self.data, now=self.now)
                self.assertEqual(got['status'], 'rate-limited' if kind == 'rate-limit' else 'network-error')
                self.assertEqual(got['error']['kind'], kind)
                self.assertEqual(live.call_count, 1 if kind == 'rate-limit' else pc.LIVE_TRIES)

    def test_two_concurrent_confirmations_share_the_live_read(self):
        self.store(100)
        started = threading.Event()
        release = threading.Event()
        calls, results = [], []
        def runner(*args):
            calls.append(args)
            started.set()
            self.assertTrue(release.wait(5))
            return self.value(100)
        def read():
            results.append(pc.reading(self.plan, data=self.data, now=self.now, runner=runner))
        first = threading.Thread(target=read)
        second = threading.Thread(target=read)
        waiting = threading.Event()
        locks = pc._load_file_lock()
        acquire = locks.acquire
        def tracked_acquire(path, timeout):
            handle = acquire(path, timeout)
            if timeout == 0 and handle is None:
                waiting.set()
            return handle
        with patch.object(locks, 'acquire', tracked_acquire), \
                patch.object(pc, '_load_file_lock', return_value=locks):
            first.start()
            self.assertTrue(started.wait(2))
            second.start()
            try:
                self.assertTrue(waiting.wait(2))
            finally:
                release.set()
            first.join(5)
            second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual([r['status'] for r in results], ['exhausted', 'exhausted'])


if __name__ == '__main__':
    unittest.main()
