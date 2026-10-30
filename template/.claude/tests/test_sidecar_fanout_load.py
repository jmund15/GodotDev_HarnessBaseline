#!/usr/bin/env python3
"""Proof: a fan-out pick counts the work already on a plan, and a job a usage limit stopped mid-run ends
with a resume file the next step runs as written.

Cases are cut from the 2026-10-14 overrun: `anthropic-secondary` read `room 0.97` on its 7d window while its
5h window had 24% left, so 13 jobs went to it and 6+ ended exit 10 (`USAGE-LIMIT ... resets
2026-10-14T18:00:00Z`). The resume record shape is the captured p22 record, trimmed.

Run: python3 .claude/tests/test_sidecar_fanout_load.py
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import provider_bands as pb
import sidecar_fanout as sf

TMP = Path(tempfile.mkdtemp(prefix='sf_load_'))
PROMPT = TMP / 'p.md'
PROMPT.write_text('body\n', encoding='utf-8')
LAUNCHERS = {'anthropic': 'anthropic_sidecar.sh', 'anthropic-secondary': 'anthropic_secondary_sidecar.sh',
             'anthropic-console-secondary': 'anthropic_console_secondary_sidecar.sh'}
DATA = {
    'transports': {
        name: dict({'costModel': 'plan-quota', 'launcher': '.claude/scripts/' + script,
                    'status': {'state': 'available'}}, **({} if name == 'anthropic' else {'planOf': 'anthropic'}))
        for name, script in LAUNCHERS.items()},
    'models': [{'transport': 'anthropic', 'id': 'host-exec-1', 'alias': 'h-exec', 'roles': ['executor']}],
}
JOB = {'label': 'j', 'alias': 'h-exec', 'promptFile': str(PROMPT), 'kind': 'lens'}
PLANS = list(LAUNCHERS)


SIG = ('host-exec-1', None, 'lens')     # what `job_signature(JOB, DATA)` gives


def row(plan, room, headroom):
    return {'transport': plan, 'room': room, 'headroom': headroom, 'headroomWindow': 'five_hour'}


def load_of(**counts):
    return {plan.replace('_', '-'): [SIG] * n for plan, n in counts.items()}


# The incident's line: secondary 0.97 with a 5h window at 24% left; anthropic 0.52; console-secondary 0.56.
INCIDENT = {'anthropic-secondary': row('anthropic-secondary', 0.97, 24.0),
            'anthropic': row('anthropic', 0.52, 60.0),
            'anthropic-console-secondary': row('anthropic-console-secondary', 0.56, 70.0)}


def rows(table):
    return patch.object(sf, 'plan_room', lambda transport, data, read, now: dict(table[transport]))


def quiet(fn, *a, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


class Headroom(unittest.TestCase):
    def test_a_plan_row_carries_the_tightest_window_left(self):
        win = lambda name, used, mins, left: {'name': name, 'usedPercent': used, 'windowDurationMins': mins,
                                              'resetsAt': 1_800_000_000 + left * mins * 60}
        reading = {'status': 'available', 'planType': 'max', 'quota': {
            'routingWindow': 'seven_day',
            'windows': [win('seven_day', 20, 10080, 0.82), win('five_hour', 76, 300, 0.19)]}}
        got = pb.plan_room('p', {}, lambda t, d: reading, 1_800_000_000)
        self.assertEqual(got['headroom'], 24.0)
        self.assertEqual(got['headroomWindow'], 'five_hour')
        self.assertEqual({w['name']: w['headroom'] for w in got['windows']}['five_hour'], 24.0)
        self.assertAlmostEqual(got['room'], 0.80 / 0.82, places=2)


class Pick(unittest.TestCase):
    def setUp(self):
        sf.PLACED.clear()
        # no ledger or samples: every job prices at the default drain, so the cases do not read live spend
        for target in ('_ledger_jobs', '_samples'):
            patcher = patch.object(pb, target, lambda: [])
            patcher.start()
            self.addCleanup(patcher.stop)

    def pick(self, load, table=INCIDENT):
        rest = []
        with rows(table):
            return quiet(sf.pick_plan, 'j', 'anthropic', PLANS, DATA, rest=rest, load=load), rest

    def test_an_idle_plan_keeps_the_pace_ranking(self):
        self.assertEqual(self.pick({})[0], 'anthropic-secondary')

    def test_a_plan_whose_window_the_work_on_it_already_spends_is_passed_over(self):
        # 24% left at pb.JOB_DRAIN_PCT a job: this job is the (24 // drain + 1)th, so it no longer fits
        full = int(24 // pb.JOB_DRAIN_PCT)
        self.assertEqual(self.pick(load_of(anthropic_secondary=full - 1))[0], 'anthropic-secondary')
        plan, rest = self.pick(load_of(anthropic_secondary=full))
        self.assertEqual(plan, 'anthropic-console-secondary')
        self.assertEqual(rest, ['anthropic'])           # a fallback must hold the job too: the full plan is no fallback

    def test_load_on_a_roomy_plan_does_not_move_a_pick_that_still_fits(self):
        self.assertEqual(self.pick(load_of(anthropic=3))[0], 'anthropic-secondary')

    def test_a_pass_over_a_thirteen_job_fan_out_cannot_overrun_the_short_window(self):
        placed = []
        with rows(INCIDENT), patch.object(sf, 'plan_load', lambda data: {p: list(s) for p, s in sf.PLACED.items()}):
            for n in range(13):
                spec = dict(JOB, label='j%d' % n)
                argv = quiet(sf.plan_job, spec, DATA, TMP, False)[1]
                placed.append(argv[1].rsplit('/', 1)[-1])
        on_short = placed.count('anthropic_secondary_sidecar.sh')
        self.assertLessEqual(on_short * pb.JOB_DRAIN_PCT, 24.0, placed)
        self.assertGreater(on_short, 0)                   # still the first choice while it fits
        self.assertEqual(len(placed), 13)

    def test_a_named_plan_is_work_on_that_plan(self):
        with rows(INCIDENT), patch.object(sf, 'plan_load', lambda data: {p: list(s) for p, s in sf.PLACED.items()}):
            quiet(sf.plan_job, dict(JOB, plan='anthropic-secondary'), DATA, TMP, False)
        self.assertEqual(sf.PLACED, {'anthropic-secondary': [SIG]})

    def test_every_plan_full_still_picks_the_one_with_most_left_over(self):
        table = {'anthropic-secondary': row('anthropic-secondary', 0.97, 8.0),
                 'anthropic': row('anthropic', 0.52, 9.0),
                 'anthropic-console-secondary': row('anthropic-console-secondary', 0.56, 8.0)}
        load = {name: [SIG] * 5 for name in table}
        self.assertEqual(self.pick(load, table)[0], 'anthropic')

    def test_a_row_with_no_headroom_reading_keeps_the_pace_ranking(self):
        table = {name: {'transport': name, 'room': value} for name, value in
                 (('anthropic-secondary', 0.97), ('anthropic', 0.52), ('anthropic-console-secondary', 0.56))}
        self.assertEqual(self.pick(load_of(anthropic_secondary=50), table)[0], 'anthropic-secondary')

    def test_a_plan_outside_plans_never_wins(self):
        table = dict(INCIDENT, paid=row('paid', 9.9, 99.0))
        self.assertNotEqual(self.pick({}, table)[0], 'paid')


class LiveLoad(unittest.TestCase):
    def launcher(self, pid, script, label, record, snapshot=False):
        name = '%s.%d.sh' % (script, pid) if snapshot else script
        cmd = ('"C:\\Program Files\\Git\\bin\\bash.exe" /tmp/sidecar-snapshots/%s -m h-exec -f /p/%s.md -l %s '
               '-X -R %s -P /o/%s.stream' % (name, label, label, record, label))
        return {'pid': pid, 'ppid': 1, 'name': 'bash.exe', 'cmd': cmd, 'start': '2026-10-14 17:30:00'}

    def test_launchers_count_once_per_job_on_the_plan_their_script_names(self):
        table = {}
        for row_ in (self.launcher(10, 'anthropic_secondary_sidecar.sh', 'a', '/o/a.record.json'),
                     self.launcher(11, 'anthropic_secondary_sidecar.sh', 'a', '/o/a.record.json', snapshot=True),
                     self.launcher(12, 'anthropic_secondary_sidecar.sh', 'b', '/o/b.record.json', snapshot=True),
                     self.launcher(13, 'anthropic_sidecar.sh', 'c', '/o/c.record.json'),
                     self.launcher(14, 'anthropic_console_secondary_sidecar.sh', 'd', '/o/d.record.json')):
            table[row_['pid']] = row_
        table[99] = {'pid': 99, 'ppid': 1, 'name': 'bash.exe', 'cmd': 'bash -c "cat anthropic_secondary_sidecar.sh"',
                     'start': ''}
        live = ('host-exec-1', None, None)      # read from the launcher's `-m`; no `-e` or `-G` on these
        self.assertEqual(sf.live_plan_load(table, DATA),
                         {'anthropic-secondary': [live, live], 'anthropic': [live], 'anthropic-console-secondary': [live]})

    def test_a_plan_wrappers_snapshots_are_named_for_the_shared_base_and_count_for_the_wrapper(self):
        # Captured 2026-10-14 from the live table: the wrapper runs by its repo path, and each re-exec snapshot of
        # it is `anthropic_sidecar.sh.<pid>.sh`, the base script every Anthropic plan shares.
        wrapper = ('"C:\\Program Files\\Git\\bin\\..\\usr\\bin\\bash.exe" '
                   '.claude/scripts/anthropic_secondary_sidecar.sh -m h-exec -f C:/p/a.md -l a -X -R C:/o/a.record.json')
        snapshot = ('"C:\\Program Files\\Git\\usr\\bin\\bash.exe" /tmp/sidecar-snapshots/anthropic_sidecar.sh.'
                    '2827347.sh -m h-exec -f C:/p/a.md -l a -X -R C:/o/a.record.json')
        row_ = lambda pid, cmd: {'pid': pid, 'ppid': 1, 'name': 'bash.exe', 'cmd': cmd, 'start': ''}
        table = {1: row_(1, wrapper), 2: row_(2, snapshot), 3: row_(3, snapshot)}
        self.assertEqual(sf.live_plan_load(table, DATA), {'anthropic-secondary': [('host-exec-1', None, None)]})

    def test_a_job_only_its_snapshot_shows_counts_for_the_script_the_snapshot_names(self):
        snapshot = ('"C:\\Program Files\\Git\\usr\\bin\\bash.exe" /tmp/sidecar-snapshots/anthropic_sidecar.sh.'
                    '9.sh -m h-exec -f C:/p/b.md -l b -X -R C:/o/b.record.json')
        table = {1: {'pid': 1, 'ppid': 1, 'name': 'bash.exe', 'cmd': snapshot, 'start': ''}}
        self.assertEqual(sf.live_plan_load(table, DATA), {'anthropic': [('host-exec-1', None, None)]})

    def test_an_unreadable_table_counts_nothing(self):
        self.assertEqual(sf.live_plan_load({}, DATA), {})


# What the launcher left for a job a usage limit stopped mid-run: the p22 record's shape, trimmed.
RECORD = {'launchId': 'L1', 'parentSessionId': None, 'sessionId': 'ab0978fb-98c0-45ab-ab80-b2b84b248ce8',
          'label': 'lim', 'transport': 'anthropic-secondary', 'stopReason': 'provider-usage-limit',
          'apiErrorStatus': 429, 'exitCode': 10, 'usageLimitResetsAt': 1792000800}


class Resume(unittest.TestCase):
    def jobs(self):
        return [dict(JOB, label='lim', contextFiles=[str(PROMPT)], extraArgs=['-Z', '1']),
                dict(JOB, label='done'), dict(JOB, label='broke')]

    def results(self, out, sid=RECORD['sessionId']):
        record = out / 'lim.record.json'
        record.write_text(json.dumps(dict(RECORD, sessionId=sid)), encoding='utf-8')
        return {'lim': {'exit': 10, 'resumeWith': sid, 'plan': 'anthropic-secondary',
                        'limitResetsAt': 1792000800, 'record': str(record)},
                'done': {'exit': 0, 'stdout': str(out / 'done.out.json')}, 'broke': {'exit': 1}}

    def test_run_jobs_keeps_the_plan_and_reset_a_limited_record_names(self):
        out = Path(tempfile.mkdtemp(prefix='rj_', dir=TMP))
        record = out / 'lim.record.json'
        argv = [sys.executable, 'x.sh', '-l', 'lim', '-R', str(record), '-X']

        def launch(_argv, env=None, **_kw):     # the launcher stamps the record with the id it was handed
            record.write_text(json.dumps(dict(RECORD, launchId=env['SIDECAR_LAUNCH_ID'])), encoding='utf-8')
            return 0

        with patch.object(sf.subprocess, 'call', side_effect=launch), \
                patch.object(sf, 'supervised_launch', lambda l, child, e, a: child()), \
                patch.object(sf, '_wait_detached', return_value=(10, False)), \
                patch.object(sf, 'freeze_fingerprint', return_value=None), \
                patch.object(sf, 'bound_finished_stream'), \
                patch.dict(sf.os.environ, {'CLAUDE_CODE_SESSION_ID': ''}):
            got = quiet(sf.run_jobs, [('lim', argv, str(out / 'lim.out.json'))], 1, out)['lim']
        self.assertEqual((got['exit'], got['resumeWith'], got['plan'], got['limitResetsAt']),
                         (10, RECORD['sessionId'], 'anthropic-secondary', 1792000800))

    def test_a_stopped_job_is_written_to_resume_jobs_pinned_to_its_plan_with_its_session(self):
        out = Path(tempfile.mkdtemp(prefix='rs_', dir=TMP))
        path, text = sf.resume_plan(self.jobs(), self.results(out), out, None)
        self.assertEqual(path, out / 'resume-jobs.json')
        spec = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([j['label'] for j in spec['jobs']], ['lim'])      # not the finished or the failed
        job = spec['jobs'][0]
        self.assertEqual(job['plan'], 'anthropic-secondary')
        self.assertEqual(job['extraArgs'], ['-Z', '1', '-r', RECORD['sessionId']])
        self.assertEqual(job['contextFiles'], [str(PROMPT)])
        self.assertEqual(spec['spillDir'], str(out))
        self.assertIn('2026-10-14T18:00:00Z', text)
        self.assertIn('sidecar_fanout.py', text)
        self.assertIn('resume-jobs.json', text)

    def test_a_stop_before_any_session_reruns_the_job_with_no_resume_flag_and_no_pin(self):
        out = Path(tempfile.mkdtemp(prefix='rn_', dir=TMP))
        results = self.results(out, sid=None)
        results['lim']['resumeWith'] = None
        path, _text = sf.resume_plan(self.jobs(), results, out, None)
        job = json.loads(path.read_text(encoding='utf-8'))['jobs'][0]
        self.assertNotIn('plan', job)
        self.assertNotIn('-r', job.get('extraArgs') or [])

    def test_a_stop_whose_record_carries_no_reset_says_so_never_the_epoch(self):
        out = Path(tempfile.mkdtemp(prefix='ru_', dir=TMP))
        results = self.results(out)
        results['lim']['limitResetsAt'] = None
        _path, text = sf.resume_plan(self.jobs(), results, out, None)
        self.assertIn('anthropic-secondary resets at an unrecorded time', text)
        self.assertNotIn('1970', text)

    def test_a_chain_resumes_the_stopped_job_and_the_jobs_its_lane_never_ran(self):
        out = Path(tempfile.mkdtemp(prefix='rc_', dir=TMP))
        jobs = [dict(JOB, label='first', _lane='L'), dict(JOB, label='lim', _lane='L'),
                dict(JOB, label='after', _lane='L'), dict(JOB, label='other', _lane='M')]
        results = self.results(out)
        results.update({'first': {'exit': 0}, 'after': {'exit': None, 'error': 'not run: lane `L` stopped'},
                        'other': {'exit': 0}})
        path, _text = sf.resume_plan(jobs, results, out, [('L', ['first', 'lim', 'after']), ('M', ['other'])])
        spec = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual([(c['name'], [j['label'] for j in c['jobs']]) for c in spec['chains']],
                         [('L', ['lim', 'after'])])
        self.assertNotIn('_lane', json.dumps(spec))

    def test_nothing_stopped_writes_nothing(self):
        out = Path(tempfile.mkdtemp(prefix='rz_', dir=TMP))
        path, text = sf.resume_plan(self.jobs(), {'lim': {'exit': 0}, 'done': {'exit': 0}}, out, None)
        self.assertEqual((path, text), (None, ''))
        self.assertFalse((out / 'resume-jobs.json').exists())

    def test_the_summary_points_at_the_file_not_at_a_launcher_to_find(self):
        out = Path(tempfile.mkdtemp(prefix='rt_', dir=TMP))
        planned = [(label, ['x'], str(out / label)) for label in ('lim', 'done', 'broke')]
        text, _code = sf.summarize(planned, self.results(out), compare=False)
        self.assertIn('resume-jobs.json', text)
        self.assertNotIn("re-run that job's launcher", text)


if __name__ == '__main__':
    unittest.main()
