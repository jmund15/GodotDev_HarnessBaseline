#!/usr/bin/env python3
"""Proof: the fan-out's mid-run pacing (`Waves`) charges a window only the drain that lands before its reset.

The hold check (`_holds`) already charges a job the share of its run that falls before the window's reset
(`provider_bands.charge`), and a running job only the share still ahead. `Waves` priced whole drains: with a
window of 5 points left and its reset 36 minutes on, one fan-out of 0.57-point jobs stopped at the whole-drain
count while jobs that end after the reset spend part of their drain in the next window.

Every case plants the reading and the job's drain; no process is spawned and nothing is billed.

Run: python3 .claude/tests/test_sidecar_fanout_waves_reset.py
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import provider_bands as pb
import sidecar_fanout as sf

NOW = 1_800_000_000.0
HOUR = 3600.0
SIG = ('m-1', 'high', 'author')
PLAN = 'anthropic'
DATA = {
    'transports': {PLAN: {'costModel': 'plan-quota', 'launcher': '.claude/scripts/anthropic_sidecar.sh',
                          'status': {'state': 'available'}}},
    'models': [{'transport': PLAN, 'id': 'm-1', 'alias': 'm', 'roles': ['executor']}],
}


def reading(headroom, resets_in=None):
    """An available reading of two windows: a roomy five_hour that routes, and seven_day with `headroom` points left
    that resets `resets_in` seconds on (a window that states no reset reads only beside a routing one)."""
    routing = {'name': 'five_hour', 'usedPercent': 10.0, 'resetsAt': NOW + 3 * HOUR, 'windowDurationMins': 300,
               'pressure': 1.0, 'band': 'Hot'}
    window = {'name': 'seven_day', 'usedPercent': 100.0 - headroom, 'windowDurationMins': 10080,
              'pressure': 1.0, 'band': 'Hot'}
    if resets_in is not None:
        window['resetsAt'] = NOW + resets_in
    return {'schemaVersion': 1, 'transport': PLAN, 'costModel': 'plan-quota', 'sourceKind': 'live',
            'observedAt': '2027-01-15T08:00:00Z', 'status': 'available',
            'quota': {'windows': [routing, window], 'routingWindow': 'five_hour', 'bindingWindow': 'five_hour',
                      'routingBand': 'Hot', 'dispatchBand': 'Hot'}}


class Plan:
    """Waves over planted readings and drains. `headroom` and `resets_in` are read live, so a case moves them; `clock`
    is the epoch `time.time()` answers."""

    def __init__(self, jobs=6, headroom=3.5, resets_in=HOUR / 2, pct=3.0, span=HOUR):
        self.headroom, self.resets_in, self.clock, self.said = headroom, resets_in, [NOW], []
        self.pct, self.span = pct, span
        self.labels = ['j%d' % n for n in range(jobs)]
        self.waves = sf.Waves({label: sf.Slot(PLAN, SIG, ()) for label in self.labels}, DATA, self._read,
                              say=self.said.append)

    def _read(self, plan, data=None):
        return reading(self.headroom, self.resets_in)

    def drain(self, *_args, **_kw):
        return {'pct': self.pct, 'basis': 'measured', 'usd': 1.0, 'rate': 1.0, 'why': None, 'span': self.span}

    def at(self, minutes):
        self.clock[0] = NOW + 60.0 * minutes

    def __enter__(self):
        self.patches = [patch.object(pb, 'job_drain', self.drain), patch('time.time', lambda: self.clock[0])]
        for each in self.patches:
            each.start()
        self.waves.pace()
        return self

    def __exit__(self, *_exc):
        for each in self.patches:
            each.stop()

    def launches(self, labels=None):
        """Labels `decide` launches in order before the first wait."""
        got = []
        for label in labels or self.labels:
            if self.waves.decide(label).kind != 'launch':
                break
            got.append(label)
        return got


class ResetShare(unittest.TestCase):
    def test_a_job_that_runs_past_the_reset_is_priced_the_share_before_it(self):
        # 3 points over an hour, the window resets in half an hour: 1.5 land here. 3.5 left holds two of them.
        with Plan(headroom=3.5, resets_in=HOUR / 2) as plan:
            self.assertEqual(len(plan.launches()), 2)

    def test_the_wave_line_prices_each_job_at_the_share_before_the_reset(self):
        with Plan(jobs=3, headroom=3.5, resets_in=HOUR / 2) as plan:
            self.assertIn('summed forecast 4.5, first wave 2', plan.said[0])

    def test_a_queued_job_is_priced_the_share_before_the_reset(self):
        with Plan(headroom=3.5, resets_in=HOUR / 2) as plan:
            self.assertAlmostEqual(plan.waves.price('j0'), 1.5)

    def test_a_window_that_runs_out_before_its_reset_still_waits(self):
        with Plan(headroom=2.0, resets_in=HOUR / 2) as plan:
            self.assertEqual(plan.launches(), ['j0'])

    def test_a_job_that_ends_before_the_reset_is_priced_whole(self):
        with Plan(headroom=3.5, resets_in=2 * HOUR) as plan:
            self.assertEqual(len(plan.launches()), 1)

    def test_a_window_with_no_reset_prices_the_whole_drain(self):
        with Plan(headroom=3.5, resets_in=None) as plan:
            self.assertEqual(len(plan.launches()), 1)

    def test_a_group_with_no_known_span_prices_the_whole_drain(self):
        with Plan(headroom=3.5, resets_in=HOUR / 2, span=None) as plan:
            self.assertEqual(len(plan.launches()), 1)

    def test_the_observed_drain_still_replaces_the_forecast(self):
        # j0 ends having moved the reading 2 points: that observed drain prices the next job, not the 1.5 forecast.
        with Plan(headroom=9.0, resets_in=HOUR / 2) as plan:
            self.assertEqual(plan.launches(['j0']), ['j0'])
            plan.headroom = 7.0
            plan.waves.ended('j0', {'exit': 0})
            self.assertTrue(any('observed drain 2.0/job replaces forecast 1.5' in line for line in plan.said))
            plan.waves.round()
            self.assertAlmostEqual(plan.waves.price('j1'), 2.0)


class FreeRoute(unittest.TestCase):
    """A queued job moves from a plan that does not hold it beside its running jobs to an open free slot."""
    BOX = {'transports': dict(DATA['transports'], box={'costModel': 'free', 'capacityUnsupported': {'reason': 'x'},
                                                       'launcher': '.claude/scripts/box.sh',
                                                       'maxChildren': {'count': 1, 'basis': 'one GPU'}}),
           'models': DATA['models'] + [{'transport': 'box', 'id': 'b-1', 'alias': 'b', 'roles': ['executor']}]}
    PICKS = {PLAN: ('m', 'high', SIG), 'box': ('b', 'high', ('b-1', 'high', 'author'))}

    def waves(self, plan, start, busy=()):
        """(verdict kinds, plans) of one job per plan named in `start`, with `busy` jobs of other sessions on the slot."""
        slots = {'j%d' % n: sf.Slot(name, self.PICKS[name][2], (PLAN, 'box'), self.PICKS)
                 for n, name in enumerate(start)}
        waves = sf.Waves(slots, self.BOX, plan._read, say=plan.said.append)
        with patch.dict(sf._LIVE, {'load': {'box': list(busy)}}):
            waves.pace()
            kinds = [waves.decide(label).kind for label in slots]
        return kinds, [waves.plan_of(label) for label in slots]

    def test_one_job_moves_to_the_open_slot_and_the_rest_wait(self):
        with Plan(headroom=3.5, resets_in=2 * HOUR) as plan:
            kinds, plans = self.waves(plan, [PLAN] * 4)
        self.assertEqual(kinds, ['launch', 'launch', 'wait', 'wait'])
        self.assertEqual(plans, [PLAN, 'box', PLAN, PLAN])

    def test_a_slot_another_session_holds_takes_no_job(self):
        with Plan(headroom=3.5, resets_in=2 * HOUR) as plan:
            kinds, plans = self.waves(plan, [PLAN] * 4, busy=[('b-1', 'high', 'arm')])
        self.assertEqual(kinds, ['launch', 'wait', 'wait', 'wait'])
        self.assertEqual(plans, [PLAN] * 4)

    def test_a_job_the_planner_put_on_the_slot_counts_as_taking_it(self):
        with Plan(headroom=3.5, resets_in=2 * HOUR) as plan:
            kinds, _plans = self.waves(plan, ['box', PLAN, PLAN])
        self.assertEqual(kinds, ['launch', 'launch', 'wait'])
        self.assertFalse(any(line.startswith('[fanout] wave box') for line in plan.said), plan.said)


class RunningShare(unittest.TestCase):
    """A running job's elapsed share is already in the reading, so it holds back only the share still ahead."""

    def test_a_job_half_through_its_run_holds_back_half_its_drain(self):
        # Two 3-point hour-long jobs start with 9 points left. Half an hour on the reading reads 6 and each job
        # has 1.5 still to spend: 6 - 3 holds a third job's 3 points. Charging each whole read 6 - 6 and waited.
        with Plan(headroom=9.0, resets_in=None) as plan:
            self.assertEqual(plan.launches(['j0', 'j1']), ['j0', 'j1'])
            plan.at(30)
            plan.headroom = 6.0
            plan.waves.round()
            self.assertEqual(plan.waves.decide('j2').kind, 'launch')

    def test_a_job_short_of_headroom_for_what_is_still_ahead_waits(self):
        with Plan(headroom=9.0, resets_in=None) as plan:
            plan.launches(['j0', 'j1'])
            plan.at(30)
            plan.headroom = 5.5
            plan.waves.round()
            self.assertEqual(plan.waves.decide('j2').kind, 'wait')

    def test_a_job_past_its_span_holds_back_nothing(self):
        with Plan(headroom=9.0, resets_in=None) as plan:
            plan.launches(['j0', 'j1'])
            plan.at(70)
            plan.headroom = 3.0
            plan.waves.round()
            self.assertEqual(plan.waves.decide('j2').kind, 'launch')

    def test_a_running_job_holds_back_only_the_share_before_the_reset(self):
        # Reset 45 minutes on; at 30 minutes each running job has 15 of its 60 minutes before it (0.75 points, 1.5
        # for both) and a new job 0.75: 2.5 - 1.5 holds it. Charged at launch (2.25 each) the two read 4.5 and waited.
        with Plan(headroom=9.0, resets_in=45 * 60.0) as plan:
            plan.launches(['j0', 'j1'])
            plan.at(30)
            plan.headroom = 2.5
            plan.waves.round()
            self.assertEqual(plan.waves.decide('j2').kind, 'launch')

    def test_a_running_job_is_priced_at_its_share_ahead_under_an_observed_drain(self):
        # j0 ends and moves the reading 3 points: 3 / 1.5 jobs = 2.0 a job. j1 is half through: it holds back half.
        with Plan(headroom=9.0, resets_in=None) as plan:
            plan.launches(['j0', 'j1'])
            plan.at(30)
            plan.headroom = 6.0
            plan.waves.ended('j0', {'exit': 0})
            self.assertAlmostEqual(plan.waves.states[PLAN].reserve()['seven_day'], 1.0)

    def test_the_placement_of_a_moved_job_sees_a_running_job_with_its_start(self):
        # `place_job` prices a sibling's load through `_holds`, which charges a running job from its `started`.
        seen = {}
        with Plan(headroom=9.0, resets_in=None) as plan:
            plan.launches(['j0'])
            plan.at(30)
            plan.waves.slots['j1'] = sf.Slot(PLAN, SIG, ('other',))
            with patch.object(sf, 'place_job', lambda names, data, read, now, load, sign: seen.update(load) or 'placed'):
                self.assertEqual(plan.waves._siblings(plan.waves.slots['j1'], {PLAN}), 'placed')
        self.assertEqual([job.started for job in seen[PLAN]], [NOW])


if __name__ == '__main__':
    unittest.main()
