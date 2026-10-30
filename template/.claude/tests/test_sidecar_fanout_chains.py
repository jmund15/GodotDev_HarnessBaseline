#!/usr/bin/env python3
"""`chains:[{name, jobs}]` on the fan-out: the contract of workflows/dispatch_chains.js. Lanes run in parallel,
each lane's jobs in sequence; a later job is told its position and its predecessors; a job that fails ends its
lane and leaves the lane's later jobs unrun, while other lanes continue."""
import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import model_registry
import sidecar_fanout as sf

TMP = Path(tempfile.mkdtemp(prefix='sf_chains_'))
PROMPT = TMP / 'p.md'
PROMPT.write_text('body\n', encoding='utf-8')


ALIAS = next(m['alias'] for m in model_registry.available_models(model_registry.load())
             if m['transport'] == 'anthropic' and 'executor' in model_registry.entry_tiers(m))


def job(label):
    return {'label': label, 'model': ALIAS, 'promptPath': str(PROMPT), 'effort': 'high', 'kind': 'author'}


SPEC = {'contextPath': str(PROMPT), 'spillDir': '.claude/scratch/x/spill',
        'chains': [{'name': 'A', 'jobs': [job('a1'), job('a2'), job('a3')]},
                   {'name': 'B', 'jobs': [job('b1'), job('b2')]}]}


class ChainSpec(unittest.TestCase):
    def test_chains_flatten_in_lane_order_and_keep_the_shared_fields(self):
        jobs, shared, spill = sf.parse_jobs(SPEC, 'spec')
        self.assertEqual([j['label'] for j in jobs], ['a1', 'a2', 'a3', 'b1', 'b2'])
        self.assertEqual((shared, spill), (SPEC['contextPath'], SPEC['spillDir']))

    def test_a_lane_job_gets_the_lane_guard_not_the_flat_pools_single_flight_bar(self):
        spec = {'sharedSingleFlight': ['csharp-ls LSP'],
                'chains': [{'name': 'A', 'exclusiveResource': 'worktree .claude/worktrees/a', 'jobs': [job('a1')]},
                           {'name': 'B', 'jobs': [job('b1')]}]}
        jobs, _shared, _spill = sf.parse_jobs(spec, 'spec')
        planned = [(j['label'], ['-P', str(PROMPT)], None) for j in jobs]
        out = Path(tempfile.mkdtemp(prefix='laneguard_', dir=TMP))
        got = sf.add_lane_guard(planned, jobs, sf.job_lanes(jobs), out)
        text = {label: Path(argv[argv.index('-C') + 1]).read_text(encoding='utf-8') for label, argv, _ in got}
        self.assertIn('ONLY agent in your own lane', text['a1'])
        self.assertIn('worktree .claude/worktrees/a', text['a1'])
        self.assertIn('csharp-ls LSP', text['b1'])
        for t in text.values():
            self.assertNotIn('a test runner', t)
        self.assertLess(got[0][1].index('-C'), got[0][1].index('-P'))

    def test_lanes_are_name_and_labels(self):
        self.assertEqual(sf.job_lanes(sf.parse_jobs(SPEC, 'spec')[0]), [('A', ['a1', 'a2', 'a3']), ('B', ['b1', 'b2'])])
        self.assertIsNone(sf.job_lanes(sf.parse_jobs({'jobs': [job('x')]}, 'spec')[0]))
        self.assertIsNone(sf.job_lanes(sf.parse_jobs([job('x')], 'spec')[0]))

    def test_malformed_chains_refuse(self):
        for bad in ({'chains': []}, {'chains': [{'jobs': [job('x')]}]}, {'chains': [{'name': 'A', 'jobs': []}]},
                    {'chains': [{'name': 'A', 'jobs': ['x']}]}, {'chains': 'A'},
                    {'chains': [{'name': 'A', 'jobs': [job('x')]}, {'name': 'A', 'jobs': [job('y')]}]},
                    {'chains': [{'name': 'A', 'jobs': [job('x')]}], 'jobs': [job('y')]}):
            with self.assertRaises(sf.JobError, msg=bad):
                sf.parse_jobs(bad, 'spec')


class ChainHandoff(unittest.TestCase):
    def test_a_later_job_is_told_its_position_and_predecessors_the_first_is_not(self):
        out = Path(tempfile.mkdtemp(prefix='handoff_', dir=TMP))
        planned = [(label, ['bash', 'l.sh', '-l', label, '-P', str(out / (label + '.stream'))], str(out / (label + '.out.json')))
                   for label in ('a1', 'a2', 'a3', 'b1')]
        got = sf.add_chain_handoff(planned, [('A', ['a1', 'a2', 'a3']), ('B', ['b1'])], out)
        self.assertEqual([p[1] for p in got if p[0] in ('a1', 'b1')], [p[1] for p in planned if p[0] in ('a1', 'b1')])
        a3 = dict((p[0], p[1]) for p in got)['a3']
        at = a3.index('-C')
        self.assertLess(at, a3.index('-P'))
        text = Path(a3[at + 1]).read_text(encoding='utf-8')
        self.assertIn('step 3 of 3 in lane `A`', text)
        self.assertIn('`a1`, `a2`', text)
        self.assertIn('ALREADY edited this working tree', text)


class ChainRun(unittest.TestCase):
    def run_lanes(self, outcomes, lanes, parallel=2, blocked=()):
        """run_jobs over `lanes` whose launcher exits outcomes[label]; -> (results, call order, out dir)."""
        out = Path(tempfile.mkdtemp(prefix='lanes_', dir=TMP))
        planned = [(l, ['bash', 'l.sh', '-l', l, '-R', str(out / (l + '.record.json')), '-X'], str(out / (l + '.out.json')))
                   for _n, labels in lanes for l in labels]
        order, lock = [], threading.Lock()
        started = threading.Barrier(2, timeout=5) if parallel == 2 else None

        def call(argv, **_kw):
            label = argv[argv.index('-l') + 1]
            with lock:
                order.append(label)
            if started is not None and label in ('a1', 'b1'):
                started.wait()          # both lanes' first jobs are in flight together, or this times out
            return 0

        waits = lambda record, *_a: (outcomes.get(Path(record).name.split('.')[0], 0), False)
        with patch.object(sf.subprocess, 'call', side_effect=call), \
                patch.object(sf, 'supervised_launch', lambda l, child, e=None, a=None: child()), \
                patch.object(sf, '_wait_detached', side_effect=waits), \
                patch.object(sf, '_blocked', side_effect=lambda p: 'blocked' if Path(p).name.split('.')[0] in blocked else None), \
                patch.object(sf, 'freeze_fingerprint', return_value=None), \
                patch.object(sf, '_delivery', return_value={'deliverable': None}), \
                contextlib.redirect_stdout(io.StringIO()):
            results = sf.run_jobs(planned, parallel, out, lanes=lanes)
        return results, order, out

    LANES = [('A', ['a1', 'a2', 'a3']), ('B', ['b1', 'b2'])]

    def test_lanes_run_in_parallel_and_each_lane_in_sequence(self):
        results, order, _out = self.run_lanes({}, self.LANES)
        for lane in ('a1', 'a2', 'a3'), ('b1', 'b2'):
            self.assertEqual([l for l in order if l in lane], list(lane))
        self.assertEqual(sorted(order), ['a1', 'a2', 'a3', 'b1', 'b2'])
        self.assertTrue(all(r['exit'] == 0 for r in results.values()))

    def test_a_failed_job_ends_its_lane_and_only_its_lane(self):
        results, order, _out = self.run_lanes({'a2': 1}, self.LANES)
        self.assertNotIn('a3', order)
        self.assertEqual(results['a2']['exit'], 1)
        self.assertIsNone(results['a3']['exit'])
        self.assertIn('lane `A`', results['a3']['error'])
        self.assertIn('a2', results['a3']['error'])
        self.assertEqual([results[l]['exit'] for l in ('b1', 'b2')], [0, 0])

    def test_an_exit_zero_job_that_reports_itself_blocked_ends_its_lane(self):
        results, order, _out = self.run_lanes({}, self.LANES, blocked=('b1',))
        self.assertNotIn('b2', order)
        self.assertIsNone(results['b2']['exit'])
        self.assertEqual(results['a3']['exit'], 0)

    def test_an_unrun_job_leaves_the_inventory_so_the_watcher_does_not_wait_for_it(self):
        _results, _order, out = self.run_lanes({'a1': 1}, self.LANES)
        inventory = json.loads(next(out.glob('fanout-*.inventory.json')).read_text(encoding='utf-8'))
        self.assertEqual(sorted(j['label'] for j in inventory['jobs']), ['a1', 'b1', 'b2'])

    def test_no_lanes_is_the_flat_pool_as_before(self):
        out = Path(tempfile.mkdtemp(prefix='flat_', dir=TMP))
        planned = [(l, ['bash', 'l.sh', '-l', l, '-R', str(out / (l + '.record.json')), '-X'], str(out / (l + '.out.json')))
                   for l in ('x', 'y')]
        with patch.object(sf.subprocess, 'call', return_value=0), \
                patch.object(sf, 'supervised_launch', lambda l, child, e=None, a=None: child()), \
                patch.object(sf, '_wait_detached', return_value=(1, False)), \
                patch.object(sf, 'freeze_fingerprint', return_value=None), \
                patch.object(sf, '_delivery', return_value={'deliverable': None}), \
                contextlib.redirect_stdout(io.StringIO()):
            results = sf.run_jobs(planned, 2, out)
        self.assertEqual({l: r['exit'] for l, r in results.items()}, {'x': 1, 'y': 1})  # y ran though x failed


class ChainMain(unittest.TestCase):
    def write(self, spec):
        path = TMP / ('spec_%d.json' % len(list(TMP.glob('spec_*.json'))))
        path.write_text(json.dumps(spec), encoding='utf-8')
        return str(path)

    def test_dry_run_plans_every_lane_job_and_hands_off_the_later_ones(self):
        spec = {'chains': [{'name': 'A', 'jobs': [dict(job('d1'), plan='anthropic'), dict(job('d2'), plan='anthropic')]}]}
        out = TMP / 'dry'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = sf.main([self.write(spec), '--dry-run', '--out-dir', str(out)])
        text = buf.getvalue()
        self.assertEqual(code, 0, text)
        lines = {l.split()[0]: l for l in text.splitlines() if l.startswith(('d1', 'd2'))}
        self.assertNotIn('d1.handoff', lines['d1'])
        self.assertIn('d2.handoff', text.split('d2\n', 1)[1].splitlines()[0])

    def test_chains_do_not_combine_with_review(self):
        spec = {'chains': [{'name': 'A', 'jobs': [dict(job('r1'), plan='anthropic')]}]}
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = sf.main([self.write(spec), '--review', '--dry-run', '--out-dir', str(TMP / 'rev')])
        self.assertEqual(code, 2)
        self.assertIn('chains', err.getvalue())


if __name__ == '__main__':
    unittest.main()
