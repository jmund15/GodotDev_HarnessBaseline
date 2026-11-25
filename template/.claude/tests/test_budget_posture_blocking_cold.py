"""A cached refusal is cold to the posture: a background warm confirms it live, like a missing reading."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '.claude' / p) for p in ('tools', 'scripts', 'hooks')]
import anthropic_capacity_probe as probe
import budget_posture
import model_registry
import provider_capacity as pc


class BlockingIsCold(unittest.TestCase):
    def cold(self, used):
        data = model_registry.load()
        plan = model_registry.plans_of('anthropic', data)[0]
        now = time.time()
        value = probe.summarize({'five_hour': {'utilization': 5, 'resets_at': now + 10000},
                                 'seven_day': {'utilization': used, 'resets_at': now + 3 * 86400},
                                 'extra_usage': {'is_enabled': False}, 'limits': []}, now=now)
        value['transport'] = plan
        with tempfile.TemporaryDirectory(prefix='posture_cold_') as folder:
            cache = Path(folder) / 'capacity.json'
            cache.write_text(json.dumps({plan: value}), encoding='utf-8')
            with patch.dict(os.environ, {'PROVIDER_CAPACITY_CACHE': str(cache)}):
                return plan in budget_posture.cold_plans()

    def test_exhausted_and_floor_readings_are_cold(self):
        self.assertTrue(self.cold(100))
        self.assertTrue(self.cold(99))

    def test_an_available_reading_stays_warm(self):
        self.assertFalse(self.cold(55))


if __name__ == '__main__':
    unittest.main()
