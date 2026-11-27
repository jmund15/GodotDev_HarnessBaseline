"""The lease history: a bounded append, and the query that attributes each wait to the leases holding what it asked for."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / '.claude/tools'
sys.path.insert(0, str(TOOLS))
import lease_log

T = '2026-01-01T10:%02d:%02dZ'


def at(minute, second=0):
    return T % (minute, second)


def run_rows(lid, start, *, ck=0.0, lane=0.0, rt=0.0, held=None, end=None, ended='released', lock='ck-a', slots=(),
             runtime='one', label=None, kind='run'):
    """A runtime-file lease: a take row at `held` (none when it never held) and an end row at `end` (none when absent)."""
    base = {'v': 1, 'id': lid, 'pool': 'runtime', 'kind': kind, 'pid': 100, 'label': label or lid, 'checkout': 'C:/' + lock,
            'checkoutLock': lock, 'start': start, 'ask': {'checkout': lock is not None, 'runtime': runtime, 'of': 6},
            'wait': {'checkout': ck, 'lane': lane, 'runtime': rt}, 'got': {'slots': list(slots)}}
    rows = []
    if held:
        rows.append(dict(base, event='take', at=held))
    if end:
        rows.append(dict(base, event='end', at=end, ended=ended))
    return rows


def lane_rows(lid, start, wait, *, held=None, end=None, lanes=(0,), ask=1, ended='released'):
    base = {'v': 1, 'id': lid, 'pool': 'lane', 'kind': 'lane', 'pid': 200, 'label': lid, 'checkout': '', 'start': start,
            'ask': {'lanes': ask, 'width': 4}, 'wait': {'lane': wait}, 'got': {'lanes': list(lanes)}}
    rows = []
    if held:
        rows.append(dict(base, event='take', at=held))
    if end:
        rows.append(dict(base, event='end', at=end, ended=ended))
    return rows


def by_waiter(result):
    return {(w['id'], w['stage']): w for w in result}


class Append(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lease_log_')
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'leases.jsonl')

    def test_a_file_past_its_cap_keeps_its_newest_rows_before_the_next_one(self):
        for n in range(3):
            lease_log.append(self.path, {'id': str(n), 'at': at(n), 'n': n}, cap=1, keep=1)
        self.assertEqual([r['n'] for r in lease_log.read(self.path)[0]], [1, 2], 'the history stays bounded, newest rows kept')

    def test_an_unparseable_line_or_a_row_without_an_id_and_time_is_counted_apart_from_the_rows(self):
        lease_log.append(self.path, {'id': '1', 'at': at(1), 'n': 1})
        with open(self.path, 'a', encoding='utf-8') as fh:
            fh.write('{torn\n{"id": "2"}\n')
        rows, bad = lease_log.read(self.path)
        self.assertEqual(([r['n'] for r in rows], bad), ([1], 2))

    def test_a_missing_file_reads_as_no_rows(self):
        self.assertEqual(lease_log.read(self.path), ([], 0))


class Attribute(unittest.TestCase):
    def attribute(self, lane=(), runtime=(), since=at(0), until=at(59), min_wait=0):
        return lease_log.attribute(list(lane), list(runtime), lease_log.parse_time(since), lease_log.parse_time(until), min_wait)

    def test_a_runtime_wait_goes_to_the_slot_holders_and_never_to_a_checkout_only_holder(self):
        runtime = (run_rows('holder', at(0), held=at(0), end=at(10), slots=[0], lock='ck-h')
                   + run_rows('ckonly', at(0), held=at(0), end=at(10), lock='ck-w', runtime=None)
                   + run_rows('waiter', at(2), rt=180, held=at(5), end=at(6), slots=[0], lock='ck-x'))
        w = by_waiter(self.attribute(runtime=runtime))[('waiter', 'runtime')]
        self.assertEqual([(h['id'], h['overlap']) for h in w['holders']], [('holder', 180.0)])
        self.assertEqual(w['unheld'], 0.0)

    def test_a_checkout_wait_goes_to_the_holder_of_that_lock_for_its_whole_lease(self):
        runtime = (run_rows('first', at(0), rt=120, held=at(2), end=at(10), slots=[1], lock='ck-a')
                   + run_rows('other', at(0), held=at(0), end=at(10), slots=[2], lock='ck-b')
                   + run_rows('second', at(1), ck=60, held=at(2), end=at(3), slots=[1], lock='ck-a'))
        w = by_waiter(self.attribute(runtime=runtime))[('second', 'checkout')]
        self.assertEqual([(h['id'], h['overlap']) for h in w['holders']], [('first', 60.0)],
                         'a lease holds its checkout lock while it still waits for a slot, and only its own lock counts')

    def test_a_lease_with_no_end_row_ends_where_the_next_taker_of_its_unit_begins(self):
        lane = (lane_rows('dead', at(0), 0, held=at(0), lanes=[0])
                + lane_rows('next', at(0), 600, held=at(10), end=at(30), lanes=[0])
                + lane_rows('waiter', at(5), 900, held=at(20), end=at(21), lanes=[1]))
        w = by_waiter(self.attribute(lane=lane))[('waiter', 'lane')]
        got = {h['id']: h for h in w['holders']}
        self.assertEqual(got['dead']['overlap'], 300.0, 'the dead holder ends when lane 0 is taken again')
        self.assertTrue(got['dead']['open'])
        self.assertEqual(got['next']['overlap'], 600.0)

    def test_a_whole_machine_waiter_ahead_counts_against_a_lane_wait(self):
        lane = (lane_rows('drain', at(0), 300, held=at(5), end=at(8), lanes=[0, 1, 2, 3], ask='all')
                + lane_rows('waiter', at(1), 600, held=at(11), end=at(12), lanes=[0]))
        w = by_waiter(self.attribute(lane=lane))[('waiter', 'lane')]
        got = {h['id']: h for h in w['holders']}
        self.assertEqual(got['drain']['overlap'], 420.0, 'queued ahead from 10:01 to 10:05, then holding to 10:08')

    def test_a_wait_nothing_recorded_held_is_reported_unheld(self):
        runtime = run_rows('alone', at(0), rt=60, end=at(1), ended='timeout:runtime')
        w = by_waiter(self.attribute(runtime=runtime))[('alone', 'runtime')]
        self.assertEqual((w['holders'], w['unheld'], w['ended']), ([], 60.0, 'timeout:runtime'))

    def test_the_window_and_the_minimum_select_the_waits(self):
        runtime = (run_rows('early', at(0), rt=60, held=at(1), end=at(2))
                   + run_rows('late', at(30), rt=60, held=at(31), end=at(32))
                   + run_rows('short', at(30), rt=2, held=at(30, 2), end=at(32)))
        got = {w['id'] for w in self.attribute(runtime=runtime, since=at(20), min_wait=5)}
        self.assertEqual(got, {'late'})

    def test_a_brokered_lane_wait_is_listed_once_from_the_lane_file(self):
        runtime = run_rows('run', at(0), lane=60, rt=0, held=at(1), end=at(2), slots=[0])
        got = {(w['id'], w['stage']) for w in self.attribute(runtime=runtime)}
        self.assertNotIn(('run', 'lane'), got, 'the broker that waited writes the lane wait; the run repeats it')


class Render(unittest.TestCase):
    def test_leases_of_one_holder_print_as_one_line_covering_their_union(self):
        lane = (lane_rows('a', at(0), 0, held=at(0), end=at(3), lanes=[0]) + lane_rows('b', at(0), 0, held=at(2), end=at(5), lanes=[1])
                + lane_rows('waiter', at(0), 360, held=at(6), end=at(7), lanes=[2]))
        for row in lane:
            if row['id'] in ('a', 'b'):
                row['label'] = 'battery'
        waits = lease_log.attribute(lane, [], lease_log.parse_time(at(0)), lease_log.parse_time(at(59)), 5)
        held = [l for l in lease_log.render(waits, 0).splitlines() if l.strip().startswith('HELD_BY')]
        self.assertEqual(len(held), 1, held)
        self.assertIn('overlap=300s', held[0])
        self.assertIn('leases=2', held[0])


class Cli(unittest.TestCase):
    def test_waits_prints_each_wait_its_holders_and_the_unparseable_count(self):
        with tempfile.TemporaryDirectory(prefix='lease_cli_') as tmp:
            runtime = os.path.join(tmp, 'runtime.jsonl')
            lanes = os.path.join(tmp, 'lanes.jsonl')
            rows = (run_rows('holder', at(0), held=at(0), end=at(10), slots=[0], lock='ck-h')
                    + run_rows('waiter', at(2), rt=180, held=at(5), end=at(6), slots=[0], lock='ck-x'))
            Path(runtime).write_text(''.join(json.dumps(r) + '\n' for r in rows) + 'not json\n', encoding='utf-8')
            out = subprocess.run([sys.executable, str(TOOLS / 'lease_log.py'), 'waits', '--runtime', runtime, '--lanes', lanes,
                                  '--since', at(0), '--until', at(59)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        wait = next(i for i, l in enumerate(lines) if l.startswith('WAIT ') and 'waiter' in l)
        self.assertTrue(lines[wait + 1].strip().startswith('HELD_BY holder'), out.stdout)
        self.assertTrue(any(l.startswith('UNPARSEABLE') and l.split()[-1] == '1' for l in lines), out.stdout)


if __name__ == '__main__':
    unittest.main()
