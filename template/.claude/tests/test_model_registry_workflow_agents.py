"""A child's Workflow agents are priced at their own model, not the driver's.

fixtures/pricing/wfspend is cut from a real bench score-daemon child (61a50469, 2026-10-13): a
haiku driver whose Workflow ran two sonnet judges. Only message content was removed. The
record's token totals are the ledger row's, which come from the result event's modelUsage and so
already include the judges; the transcript of the driver alone covers 3 of the 7 requests.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/tools'))
import model_registry as mr
import run_cost
from unittest.mock import patch

FIX = Path(__file__).parent / 'fixtures/pricing/wfspend'
FIELDS = dict(fresh='inputTokens', cache_read='cacheReadTokens',
              cache_write='cacheWriteTokens', output='outputTokens')
SID = '61a50469-e2df-4f40-a2dc-abaa08dc2480'


def requests_of(path):
    return mr.request_usage(mr.usage_events(Path(path).read_text()))


class WorkflowAgentPricingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = copy.deepcopy(mr.load())
        self.data['transports']['anthropic-secondary']['configDir'] = str(self.root / 'login')
        project = self.root / 'login/projects/project'
        project.mkdir(parents=True)
        shutil.copy(FIX / (SID + '.jsonl'), project / (SID + '.jsonl'))
        shutil.copytree(FIX / 'subagents', project / SID / 'subagents')
        self.record = json.loads((FIX / 'record.json').read_text())
        self.path = self.root / 'cell.record.json'
        self.path.write_text(json.dumps(self.record))
        self.main = requests_of(FIX / (SID + '.jsonl'))
        self.agents = [r for f in sorted((FIX / 'subagents').rglob('agent-*.jsonl')) for r in requests_of(f)]
        self.expected = (mr.price_requests('claude-haiku-5-5', self.main, data=self.data, multiplier=1)
                         + mr.price_requests('claude-sonnet-5-5', self.agents, data=self.data, multiplier=1))

    def view(self):
        return mr.reprice_record(self.record, self.path, data=self.data)

    def test_judges_are_priced_at_sonnet_not_the_driver(self):
        rec = self.view()
        self.assertAlmostEqual(rec['costUSD'], self.expected)
        all_haiku = mr.price_requests('claude-haiku-5-5', self.main + self.agents, data=self.data, multiplier=1)
        self.assertGreater(rec['costUSD'], 2 * all_haiku)

    def test_requests_cover_the_aggregate_with_no_remainder(self):
        view = mr.record_requests(self.record, self.path, data=self.data)
        self.assertEqual(len(view['requests']), len(self.main) + len(self.agents))
        self.assertEqual(view['costCompactionBasis'], 'none')
        for key, field in FIELDS.items():
            self.assertEqual(sum(r[key] + (r['cache_write_1h'] if key == 'cache_write' else 0)
                                 for r in view['requests']), self.record[field])

    def test_a_compaction_remainder_stays_on_the_driver_model(self):
        self.record['outputTokens'] += 100
        self.path.write_text(json.dumps(self.record))
        view = mr.record_requests(self.record, self.path, data=self.data)
        last = view['requests'][-1]
        self.assertEqual((last['model'], last['output']), ('claude-haiku-5-5', 100))

    def test_a_transcript_written_after_a_miss_is_still_found(self):
        later = 'later-session'
        self.assertIsNone(mr.session_transcript(later, 'anthropic-secondary', data=self.data))
        (self.root / 'login/projects/project' / (later + '.jsonl')).write_text('{}')
        self.assertEqual(mr.session_transcript(later, 'anthropic-secondary', data=self.data).name, later + '.jsonl')
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data).name, SID + '.jsonl')

    def test_repeated_misses_on_an_unchanged_root_scan_the_session_files_once(self):
        """bench doctor looked up hundreds of absent sessions and rescanned every project per miss (>10 min)."""
        files_scanned = []
        real = mr.os.scandir

        def counting(path='.'):
            if Path(path).parent.name == 'projects':
                files_scanned.append(str(path))
            return real(path)

        with patch.object(mr.os, 'scandir', counting):
            for n in range(5):
                self.assertIsNone(mr.session_transcript('absent-%d' % n, 'anthropic-secondary', data=self.data))
        self.assertEqual(len(files_scanned), 1)

    def test_a_second_pass_over_more_records_than_a_pass_holds_reuses_every_parse(self):
        """The gate prices ~600 records per task; a 256-entry cache cleared whole on overflow hit about 30%."""
        parses = []
        real = mr._reprice_record
        paths = []
        for n in range(600):
            path = self.root / ('bulk-%d.record.json' % n)
            path.write_text('{}')
            paths.append(path)

        def counting(record, path, data, events):
            parses.append(path)
            return real(record, path, data, events)

        mr._reprice_cache.clear()
        with patch.object(mr, '_reprice_record', counting):
            for _ in range(2):
                for path in paths:
                    mr.reprice_record({'n': str(path)}, path, data=self.data)
        self.assertEqual(len(parses), 600)

    def test_run_cost_prices_the_same_row_the_same_way(self):
        with patch.object(mr, 'load', return_value=self.data):
            runs = [run_cost.price(r, self.data) for r in run_cost.load_runs(str(self.path))]
        self.assertEqual(sorted(r['model'] for r in runs), ['claude-haiku-5-5', 'claude-sonnet-5-5'])
        self.assertAlmostEqual(sum(r['usd'] for r in runs), self.view()['costUSD'])


if __name__ == '__main__':
    unittest.main()
