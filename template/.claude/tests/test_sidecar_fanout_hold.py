#!/usr/bin/env python3
"""Proof: the fan-out's plan-hold check charges a window only the drain that will land in it.

Cut from the 2026-11-15 15:35Z placement: `anthropic` projected ~8% of its seven_day window unused at its reset
two hours on, yet `anthropic does not hold it: headroom 8, drain 1.8 measured on seven_day` sent two jobs to
`anthropic-secondary`. The window's reading already holds what the running jobs spent, and a job that runs past
the reset spends the rest of its drain in the next window. A window that would run out before its reset
still refuses.

Run: python3 .claude/tests/test_sidecar_fanout_hold.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import provider_bands as pb
import sidecar_fanout as sf

NOW = 1_800_000_000.0
HOUR = 3600.0
SIG = ('m-1', 'high', 'author')
DATA = {
    'transports': {'anthropic': {'costModel': 'plan-quota', 'launcher': '.claude/scripts/anthropic_sidecar.sh',
                                 'status': {'state': 'available'}}},
    'models': [{'transport': 'anthropic', 'id': 'host-exec-1', 'alias': 'h-exec', 'roles': ['executor']}],
}


def row(headroom, resets_in=None, plan='p'):
    reset = None if resets_in is None else NOW + resets_in
    return {'transport': plan, 'room': 1.0, 'headroom': headroom, 'headroomWindow': 'seven_day',
            'windows': [{'name': 'seven_day', 'headroom': headroom, 'resetsAt': reset, 'minutes': 10080}]}


def drain(pct=3.0, span=HOUR):
    """What `provider_bands.job_drain` returns for a job of SIG: its points and the seconds a job of its group runs."""
    return {'pct': pct, 'basis': 'measured', 'usd': 1.0, 'rate': 1.0, 'why': None, 'span': span}


def holds(plan_row, load=None, now=NOW, **drain_kw):
    with patch.object(pb, 'job_drain', lambda *a, **k: drain(**drain_kw)):
        return sf._holds(plan_row, {'p': load or []}, SIG, now)


class ResetShare(unittest.TestCase):
    def test_a_job_that_runs_past_the_reset_is_charged_the_share_before_it(self):
        # 3 points over an hour, the window resets in half an hour: 1.5 land here, the rest in the next window.
        self.assertTrue(holds(row(2.0, resets_in=HOUR / 2))[0])

    def test_a_window_that_runs_out_before_its_reset_still_refuses(self):
        self.assertFalse(holds(row(1.0, resets_in=HOUR / 2))[0])

    def test_a_job_that_ends_before_the_reset_is_charged_whole(self):
        self.assertFalse(holds(row(2.0, resets_in=2 * HOUR))[0])

    def test_a_window_with_no_reset_charges_the_whole_drain(self):
        self.assertFalse(holds(row(2.0))[0])

    def test_a_group_with_no_known_span_charges_the_whole_drain(self):
        self.assertFalse(holds(row(2.0, resets_in=HOUR / 2), span=None)[0])

    def test_a_running_job_is_charged_the_same_share_before_the_reset(self):
        # Two 3-point hour-long jobs, 1.5 of each before the reset: 3.0 in all.
        running = [SIG]
        self.assertTrue(holds(row(3.5, resets_in=HOUR / 2), running)[0])
        self.assertFalse(holds(row(2.5, resets_in=HOUR / 2), running)[0])


def running(minutes_in):
    """A live job of SIG that began `minutes_in` minutes before NOW."""
    return sf.Running(SIG, NOW - 60.0 * minutes_in)


class SpentShare(unittest.TestCase):
    """The window's reading already holds what a running job spent, so only what it has still to spend commits."""

    def test_a_job_half_through_its_span_commits_half_its_drain(self):
        # 3 points over an hour, 30 minutes in: 1.5 are in the reading, 1.5 to come. New job 3.0 + 1.5 = 4.5.
        self.assertTrue(holds(row(4.6), [running(30)])[0])
        self.assertFalse(holds(row(4.4), [running(30)])[0])
        self.assertEqual(holds(row(4.6), [running(30)])[2], 1.5)

    def test_a_job_past_its_span_commits_nothing(self):
        self.assertEqual(holds(row(4.0), [running(90)])[2], 0.0)

    def test_a_job_just_launched_commits_its_whole_drain(self):
        self.assertEqual(holds(row(9.0), [running(0)])[2], 3.0)

    def test_a_job_with_no_known_start_commits_its_whole_drain(self):
        # This run's own placements carry no start: nothing of them is in the reading yet.
        self.assertEqual(holds(row(9.0), [SIG])[2], 3.0)

    def test_a_job_whose_group_has_no_span_commits_its_whole_drain(self):
        self.assertEqual(holds(row(9.0), [running(30)], span=None)[2], 3.0)

    def test_the_spent_share_and_the_reset_share_compose(self):
        # 30 minutes in, 30 ahead; the window resets in 15 minutes: a quarter of the hour-long job lands here.
        self.assertEqual(holds(row(9.0, resets_in=HOUR / 4), [running(30)])[2], 0.75)


class LiveStart(unittest.TestCase):
    def test_a_live_launcher_carries_the_time_its_process_began(self):
        import datetime
        began = datetime.datetime(2026, 11, 15, 9, 0, 3)
        table = {1: {'pid': 1, 'ppid': 1, 'name': 'bash.exe', 'start': '2026-11-15 09:00:03', 'cmd': (
            '"C:\\Program Files\\Git\\bin\\bash.exe" .claude/scripts/anthropic_sidecar.sh -m h-exec -f /p/a.md '
            '-l a -X -R /o/a.record.json')},
            2: {'pid': 2, 'ppid': 1, 'name': 'bash.exe', 'start': '2026-11-15 09:00:09', 'cmd': (
                '"C:\\Program Files\\Git\\bin\\bash.exe" /tmp/sidecar-snapshots/anthropic_sidecar.sh.7.sh -m h-exec '
                '-f /p/a.md -l a -X -R /o/a.record.json')}}
        load = sf.live_plan_load(table, DATA)
        self.assertEqual(len(load['anthropic']), 1)
        self.assertEqual(load['anthropic'][0].started, began.timestamp())

    def test_a_launcher_whose_start_is_unreadable_has_none(self):
        table = {1: {'pid': 1, 'ppid': 1, 'name': 'bash.exe', 'start': '', 'cmd': (
            '"C:\\Program Files\\Git\\bin\\bash.exe" .claude/scripts/anthropic_sidecar.sh -m h-exec -f /p/a.md '
            '-l a -X -R /o/a.record.json')}}
        self.assertIsNone(sf.live_plan_load(table, DATA)['anthropic'][0].started)


class FinishedJob(unittest.TestCase):
    """A launcher process can outlive the job it ran (cleanup after the end): its record's journal says so."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='sf_hold_'))

    def table(self, record):
        cmd = ('"C:\\Program Files\\Git\\bin\\bash.exe" .claude/scripts/anthropic_sidecar.sh -m h-exec -f /p/a.md '
               '-l a -X -R %s' % record)
        return {1: {'pid': 1, 'ppid': 1, 'name': 'bash.exe', 'start': '2026-11-15 09:00:03', 'cmd': cmd}}

    def journal(self, *events):
        record = self.dir / 'a.record.json'
        record.with_name(record.name + '.journal.jsonl').write_text(
            ''.join(json.dumps({'ts': '2026-11-15T15:00:00Z', 'event': event, 'code': 0}) + '\n' for event in events),
            encoding='utf-8')
        return str(record)

    def test_a_launcher_whose_record_has_ended_is_not_load(self):
        self.assertEqual(sf.live_plan_load(self.table(self.journal('started', 'ended')), DATA), {})

    def test_a_running_launcher_is_load(self):
        self.assertEqual(len(sf.live_plan_load(self.table(self.journal('started')), DATA)['anthropic']), 1)

    def test_a_relaunch_after_an_end_is_load(self):
        self.assertEqual(len(sf.live_plan_load(self.table(self.journal('started', 'ended', 'started')), DATA)['anthropic']), 1)

    def test_a_record_with_no_journal_is_load(self):
        self.assertEqual(len(sf.live_plan_load(self.table(str(self.dir / 'none.record.json')), DATA)['anthropic']), 1)


class Span(unittest.TestCase):
    def jobs(self, n, minutes, **kw):
        return [dict({'plan': 'p', 'start': NOW, 'end': NOW + 60.0 * minutes, 'usd': 1.0, 'model': 'm-1',
                      'effort': 'high', 'shape': 'author', 'truncated': False}, **kw) for _ in range(n)]

    def test_a_groups_span_is_the_drain_quantile_of_its_finished_run_times(self):
        jobs = [dict(j, end=NOW + 60.0 * m) for j, m in zip(self.jobs(8, 0), (10, 20, 30, 40, 50, 60, 70, 80))]
        self.assertEqual(pb.job_span('m-1', 'high', 'author', jobs), 70 * 60.0)

    def test_a_group_under_the_minimum_has_no_span_and_a_cut_short_job_is_left_out(self):
        few = self.jobs(pb.GROUP_MIN_JOBS - 1, 30)
        self.assertIsNone(pb.job_span('m-1', 'high', 'author', few))
        cut = self.jobs(pb.GROUP_MIN_JOBS, 30) + self.jobs(3, 600, truncated=True)
        self.assertEqual(pb.job_span('m-1', 'high', 'author', cut), 30 * 60.0)

    def test_job_drain_carries_the_span_even_with_no_measured_rate(self):
        got = pb.job_drain('p', 'seven_day', 'm-1', 'high', 'author', jobs=self.jobs(6, 30), samples=[])
        self.assertEqual((got['basis'], got['span']), ('default', 30 * 60.0))


if __name__ == '__main__':
    unittest.main()
