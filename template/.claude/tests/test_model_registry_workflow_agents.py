"""A child's Workflow agents are priced at their own model, not the driver's.

fixtures/pricing/wfspend is cut from a real bench score-daemon child (61a50469, 2026-10-13): a
haiku driver whose Workflow ran two sonnet judges. Only message content was removed. The
record's token totals are the ledger row's, which come from the result event's modelUsage and so
already include the judges; the transcript of the driver alone covers 3 of the 7 requests.
"""
import contextlib
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
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


@contextlib.contextmanager
def counted_probes(root):
    """`(kind, path)` for every filesystem probe under `root` while the block runs. `stat` and `isfile`
    called from inside the patched `isfile` are the same lookup, so they count once; the flag is
    per-thread, so one thread's nesting never hides another thread's probe."""
    seen = []
    real_scandir, real_stat, real_isfile = mr.os.scandir, mr.os.stat, mr.os.path.isfile
    nested = threading.local()

    def under(path):
        return str(root) in str(path)

    def scandir(path='.', *args, **kwargs):
        if under(path):
            seen.append(('scandir', str(path)))
        return real_scandir(path, *args, **kwargs)

    def stat(path, *args, **kwargs):
        if under(path) and not getattr(nested, 'on', False):
            seen.append(('stat', str(path)))
        return real_stat(path, *args, **kwargs)

    def isfile(path):
        outer = getattr(nested, 'on', False)
        if under(path) and not outer:
            seen.append(('isfile', str(path)))
        nested.on = True
        try:
            return real_isfile(path)
        finally:
            nested.on = outer

    with patch.object(mr.os, 'scandir', scandir), patch.object(mr.os, 'stat', stat), \
            patch.object(mr.os.path, 'isfile', isfile):
        yield seen


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
        projects = self.root / 'login/projects'
        with counted_probes(projects) as seen:
            for n in range(5):
                self.assertIsNone(mr.session_transcript('absent-%d' % n, 'anthropic-secondary', data=self.data))
        scans = [p for kind, p in seen if kind == 'scandir' and Path(p).parent.name == 'projects']
        self.assertEqual(len(scans), 1, scans)

    def test_a_miss_probes_each_project_directory_once(self):
        """Proving a session absent costs one probe per project directory, not a stat of each and then a probe
        of each."""
        projects = self.root / 'login/projects'
        for n in range(40):
            (projects / ('extra-%d' % n)).mkdir()
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data).name, SID + '.jsonl')
        with counted_probes(projects) as probes:
            self.assertIsNone(mr.session_transcript('absent-probe', 'anthropic-secondary', data=self.data))
        lookups = [probe for probe in probes if probe[0] in ('stat', 'isfile')]
        self.assertLessEqual(len(lookups), 41, lookups[:3])

    def test_a_finished_sessions_miss_costs_no_probe_once_the_root_was_scanned_after_it_finished(self):
        """A record's session had finished before its record was written, so a root scanned after that moment
        holds its transcript or never will, and the lookup costs no probe once the root was scanned."""
        projects = self.root / 'login/projects'
        for n in range(40):
            (projects / ('extra-%d' % n)).mkdir()
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data).name, SID + '.jsonl')
        finished = time.time() - 60
        with counted_probes(projects) as probes:
            for n in range(30):
                self.assertIsNone(mr.session_transcript('absent-%d' % n, 'anthropic-secondary', data=self.data,
                                                        settled_by=finished))
        self.assertEqual([probe for probe in probes if probe[0] in ('stat', 'isfile')], [])
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data,
                                               settled_by=finished).name, SID + '.jsonl')

    def test_a_transcript_written_after_the_scan_and_before_the_session_finished_is_found(self):
        later = 'finished-after-the-scan'
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data).name, SID + '.jsonl')
        (self.root / 'login/projects/project' / (later + '.jsonl')).write_text('{}')
        found = mr.session_transcript(later, 'anthropic-secondary', data=self.data, settled_by=time.time())
        self.assertEqual(found.name, later + '.jsonl')
        self.assertIsNone(mr.session_transcript('never-written', 'anthropic-secondary', data=self.data,
                                                settled_by=time.time()))

    def test_a_settled_reprice_takes_the_record_files_mtime_as_the_moment_its_session_finished(self):
        self.assertEqual(mr.session_transcript(SID, 'anthropic-secondary', data=self.data).name, SID + '.jsonl')
        old = time.time() - 60
        os.utime(self.path, (old, old))
        record = dict(self.record, sessionId='absent-session')
        seen = []
        real = mr.session_transcript

        def watching(session_id, transport, data=None, settled_by=None):
            seen.append(settled_by)
            return real(session_id, transport, data=data, settled_by=settled_by)

        with patch.object(mr, 'session_transcript', watching):
            mr.reprice_record(record, self.path, data=self.data, settled=True)
            mr.reprice_record(dict(record, sessionId='absent-two'), self.path, data=self.data)
        self.assertTrue(seen[:2] == [seen[0]] * 2 and abs(seen[0] - old) < 1, seen)
        self.assertEqual(seen[-1], None, 'an unsettled reprice keeps probing')

    def test_a_directory_created_after_a_miss_is_searched(self):
        later = 'later-in-a-new-directory'
        self.assertIsNone(mr.session_transcript(later, 'anthropic-secondary', data=self.data))
        fresh = self.root / 'login/projects/fresh-project'
        fresh.mkdir()
        (fresh / (later + '.jsonl')).write_text('{}')
        self.assertEqual(mr.session_transcript(later, 'anthropic-secondary', data=self.data).name, later + '.jsonl')

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
