#!/usr/bin/env python3
"""Floor outcomes through the real probe, contract, launcher and reset waiter."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude' / part) for part in ('tools', 'scripts', 'hooks')]
import anthropic_capacity_probe as probe
import provider_capacity as capacity
import provider_capacity_guard as guard
import provider_bands as bands
from sidecar_launch import git_bash

NOW = 1792343570
RESET = NOW + 2830


def reading(five=4, seven=99, credit=False, limits=None):
    return probe.summarize({
        'five_hour': {'utilization': five, 'resets_at': NOW + 13030},
        'seven_day': {'utilization': seven, 'resets_at': RESET},
        'extra_usage': {'is_enabled': credit}, 'limits': limits or [],
    }, now=NOW)


class FloorTests(unittest.TestCase):
    def test_floor_fields(self):
        value = reading()
        self.assertEqual(value['status'], 'dispatch-floor')
        self.assertEqual(value['quota']['bindingWindow'], 'seven_day')
        row = next(w for w in value['quota']['windows'] if w['name'] == 'seven_day')
        self.assertEqual((row['usedPercent'], row['dispatchFloorPercent'], row['resetsAt']), (99, 99, RESET))
        self.assertEqual(value['quota']['dispatchBand'], 'Dispatch floor')
        self.assertEqual(capacity.validate_reading(value, 'anthropic', 'plan-quota'), [])

    def test_spent_and_credit(self):
        self.assertEqual(reading(seven=100)['status'], 'exhausted')
        self.assertEqual(reading(five=98, seven=100)['quota']['bindingWindow'], 'seven_day')
        self.assertEqual(reading(credit=True)['status'], 'available')
        self.assertEqual(reading(seven=100, credit=True)['status'], 'available')
        self.assertEqual(reading(seven=98.9)['status'], 'available')
        self.assertEqual(reading(limits=[{'kind': 'weekly_scoped', 'percent': 100, 'is_active': True}])['status'], 'exhausted')

    def test_floor_requires_quota(self):
        value = reading()
        value.update(costModel='marginal-usd', quota=None, balance={'amount': 10, 'currency': 'USD'})
        self.assertTrue(capacity.validate_reading(value, 'anthropic', 'marginal-usd'))

    def test_floor_validation(self):
        value = reading()
        value['status'] = 'dispatch-floor'
        for key, bad in [('dispatchFloorPercent', None), ('usedPercent', 100), ('resetsAt', None)]:
            broken = copy.deepcopy(value)
            row = next(w for w in broken['quota']['windows'] if w['name'] == 'seven_day')
            row[key] = bad
            self.assertTrue(capacity.validate_reading(broken, 'anthropic', 'plan-quota'), key)

    def test_consumers(self):
        value = reading()
        text = guard.assess('anthropic', reader=lambda *a, **k: value)[0]
        self.assertNotIn('exhausted', text)
        for bit in ('seven_day', '99% used', '99% dispatch floor', 'mid-step', '18:00Z'):
            self.assertIn(bit, text)
        row = bands.plan_room('anthropic', None, lambda *a: value, NOW)
        self.assertIsNone(row['room'])
        self.assertIn('dispatch floor', row['why'])
        self.assertEqual(capacity.routing_view(value)['band'], 'Dispatch floor')
        self.assertIn('dispatch floor', bands.plan_entry('anthropic', value, NOW).lower())

    def test_preflight_preserves_floor(self):
        import sidecar_dispatch_context as wrapper
        from types import SimpleNamespace
        output = self.launch(reading())
        with patch.object(wrapper, 'run_bounded', return_value=SimpleNamespace(
            returncode=10, stdout='', stderr=output.stderr
        )), patch.dict(os.environ, {'HARNESS_SIDECAR_PREFLIGHT': '1'}):
            state, text = wrapper.run_preflight('bash .claude/scripts/anthropic_sidecar.sh -m sonnet', str(ROOT))
        self.assertEqual(state, 'refused')
        self.assertNotIn('exhausted', text)
        self.assertIn('before spawn', text)
        self.assertIn('99% dispatch floor', text)

    def test_posture_floor(self):
        import budget_posture as posture
        with patch.object(capacity, 'cached', return_value=reading()):
            self.assertEqual(posture.provider_band('anthropic'), 'Dispatch floor')
            self.assertIn('99% dispatch floor', posture.provider_entry('anthropic', now=NOW))
        self.assertIsNotNone(posture.spend_line({'mode': 'normal'}, 'Dispatch floor'))
    def test_posture_sort(self):
        import budget_posture as posture
        with patch.object(bands, 'plan_quota_transports', return_value=['anthropic-secondary', 'go']), patch.object(
            posture, 'provider_band', side_effect=lambda name: 'Dispatch floor' if name == 'anthropic-secondary' else 'Hot'
        ):
            self.assertEqual(posture.other_quota_bits('anthropic')[0]['name'], 'go')

    def test_expired_floor_fallback(self):
        value = reading()
        value['observedAt'] = RESET - 10
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            path.write_text(json.dumps({'anthropic': value}), encoding='utf-8')
            self.assertIsNone(capacity._read_cache(path, 'anthropic', 'plan-quota', RESET + 1,
                                                 float('inf')))

    def test_a_console_credit_reading_is_served_from_cache_and_an_old_oauth_capture_is_not(self):
        """The old-OAuth re-probe guard binds only an OAuth capture: a console credit block names its rows."""
        value = reading()
        value['observedAt'] = NOW
        value['status'] = 'exhausted'
        for row in value['quota']['windows']:
            row.pop('dispatchFloorPercent', None)
        value['quota']['credits'] = {'enabled': True, 'available': False, 'hasCredits': False,
                                     'balance': 0.0, 'storedCostRows': []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            path.write_text(json.dumps({'anthropic': value}), encoding='utf-8')
            self.assertEqual(capacity._fresh(path, 'anthropic', 'plan-quota', NOW)['status'], 'exhausted')
            oauth = copy.deepcopy(value)
            oauth['quota']['credits'] = {'enabled': True, 'available': True, 'balance': 0.0}
            path.write_text(json.dumps({'anthropic': oauth}), encoding='utf-8')
            self.assertIsNone(capacity._fresh(path, 'anthropic', 'plan-quota', NOW))

    def test_cache(self):
        value = reading()
        value['observedAt'] = NOW
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.json'
            settled = capacity._settle(value, 'anthropic', 'plan-quota', str(path), NOW)
            self.assertEqual(settled['status'], 'dispatch-floor')
            self.assertEqual(capacity._fresh(path, 'anthropic', 'plan-quota', NOW)['status'], 'dispatch-floor')
            self.assertIsNone(capacity._fresh(path, 'anthropic', 'plan-quota', RESET + 1))
            old = copy.deepcopy(value)
            old['status'] = 'exhausted'
            for row in old['quota']['windows']:
                row.pop('dispatchFloorPercent', None)
            path.write_text(json.dumps({'anthropic': old}), encoding='utf-8')
            self.assertIsNone(capacity._fresh(path, 'anthropic', 'plan-quota', NOW))

    def launch(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / 'tools'
            tools.mkdir()
            (tools / 'provider_capacity.py').write_text('import os\nprint(os.environ["FLOOR_READING"])\n', encoding='utf-8')
            script = ROOT / '.claude/scripts/lib/sidecar_common.sh'
            cmd = 'source "$FLOOR_LIB"; SC_ROOT="$FLOOR_ROOT"; SC_TRANSPORT=anthropic; SC_ALIAS=sonnet; SC_MODEL=test; SC_COST_MODEL=plan-quota; SC_CREDENTIAL=""; sc_gate_capacity'
            env = dict(os.environ, FLOOR_LIB=str(script), FLOOR_ROOT=tmp, FLOOR_READING=json.dumps(value))
            return subprocess.run([git_bash(), '-c', cmd], env=env, capture_output=True, text=True, timeout=20)

    def test_launcher_message(self):
        result = self.launch(reading())
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertNotIn('exhausted', result.stderr)
        for bit in ('REFUSING sonnet', 'anthropic seven_day', '99% used', '99% dispatch floor', 'mid-step', '18:00Z'):
            self.assertIn(bit, result.stderr)
        print('PRINTED REFUSAL:', result.stderr.strip())


if __name__ == '__main__':
    unittest.main()
