"""Proof: the main check (`.claude/scripts/main_check.py`) proves a landed range on `main` and records its verdict as
rows on the `main-check-state` branch (rules/harness_tooling.md §Land and main check).

Each case plants a repo whose `origin` is a bare `file://` remote under a temp root: nothing reaches the network or
the real `origin`. The repo carries a planted `.claude/scripts/harness_tests.py` that either forwards its argv to the
real runner (`--repo` the planted repo) or, when a close plan is present, prints the plan and writes its record to the
stamp, and a planted gate script that replays gate scenarios: the project declares it as `gate_cadence.main` in the
planted repo's `adaptation.json`, and its verdict is its exit code plus `FAILED_TEST=<id>` stdout lines. The close record
comes from a captured real output under `fixtures/main_check/`, with only the fields a case must control replaced (the
tree's head, the range's base). A project-local proof replays that project's real gate output and exit table.

    python3 .claude/tests/test_main_check.py
"""
import contextlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _harness_repo_case import RepoCase, ROOT  # noqa: E402

SCRIPT = ROOT / '.claude' / 'scripts' / 'main_check.py'
REAL_RUNNER = ROOT / '.claude' / 'scripts' / 'harness_tests.py'
FIXTURES = HERE / 'fixtures' / 'main_check'
PLATFORM = 'windows' if os.name == 'nt' else 'linux' if sys.platform.startswith('linux') else 'macos'
JOB_MINUTES = 777
EXIT_CODES = {'PASS': 0, 'FAIL': 1, 'INVALID': 2, 'WARN': 3, 'BLOCKED': 4, 'INCOMPLETE': 6, 'BUSY': 7,
              'CONTENTION': 8, 'HANG': 124}
GATE_ARGV = ['pwsh', '-NoProfile', '-File', '.claude/scripts/planted_gate.ps1', '-NoBaselineUpdate', '-RetryOnly']
GATE_OUTPUTS = ['out/baseline.json', 'out/durations.json']
ADAPTATION = '.claude/skills/project_subsystems/adaptation.json'
SUB_ENV = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always'}

WORKFLOW = """name: main-check
on:
  push:
    branches: [main]
  workflow_dispatch:
jobs:
  check:
    runs-on: ${{ fromJSON(vars.MAIN_CHECK_RUNNER) }}
    timeout-minutes: %d
    steps:
      - run: python3 .claude/scripts/main_check.py close
""" % JOB_MINUTES

SHIM = '''import json, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
scratch = os.path.join(here, "..", "scratch")
os.makedirs(scratch, exist_ok=True)
with open(os.path.join(scratch, "close_calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "probe": os.environ.get("MC_PROBE"), "both": os.environ.get("MC_BOTH"),
                         "deadline": os.environ.get("HARNESS_DEADLINE")}) + "\\n")
plan_path = os.path.join(scratch, "close_plan.json")
if not os.path.exists(plan_path):
    sys.exit(subprocess.run([sys.executable, %(real)r, "--repo", os.getcwd()] + sys.argv[1:]).returncode)
plan = json.load(open(plan_path, encoding="utf-8"))
time.sleep(plan.get("sleep", 0))
for line in plan.get("lines", []):
    print(line)
record = plan.get("record")
if record is not None:
    if not plan.get("literal"):
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        base = sys.argv[sys.argv.index("--range-start") + 1] if "--range-start" in sys.argv else None
        record = dict(record, head=head, base=base, ts=time.time())
    stamp = os.environ["HARNESS_TEST_STAMP"]
    doc = json.load(open(stamp)) if os.path.exists(stamp) else {}
    doc["close"] = record
    json.dump(doc, open(stamp, "w"))
sys.exit(plan.get("exit", 0))
''' % {'real': str(REAL_RUNNER)}

BASELINE_SLUG = 'jmund15/GodotDev_HarnessBaseline'
BASELINE_SHA = '2caaab4e5d6c80520127847e0aab8e8b6661a1ce'

FAKE_GH_SOURCE = '''#!/usr/bin/env python3
"""Fake `gh` for the baseline Linux row source: every answer comes from one planted plan
(MC_GH_PLAN), so no case reaches the network. `unreadable` makes every call fail."""
import json
import os
import sys

plan = json.load(open(os.environ["MC_GH_PLAN"], encoding="utf-8"))
argv = sys.argv[1:]
if plan.get("unreadable"):
    print("gh: HTTP 500", file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["run", "list"]:
    print(json.dumps(plan.get("runs", [])))
    sys.exit(0)
if argv[:2] == ["run", "view"]:
    sys.stdout.write(open(plan["log"], encoding="utf-8", errors="replace").read())
    sys.exit(0)
if argv[:1] == ["api"]:
    print(plan.get("message", ""))
    sys.exit(0)
print("fake gh: unsupported " + " ".join(argv), file=sys.stderr)
sys.exit(1)
'''

GATE_STUB = r'''[CmdletBinding()]
param([string] $Repo = '', [switch] $NoBaselineUpdate, [switch] $RetryOnly, [switch] $NoReuse, [switch] $StaticOnly,
      [int] $WaitSec = 1800, [switch] $Detach)
$root = (& git rev-parse --show-toplevel).Trim()
$scratch = Join-Path $root '.claude/scratch'
$plan = @(Get-Content -Raw (Join-Path $scratch 'gate_plan.json') | ConvertFrom-Json)
$calls = Join-Path $scratch '@NAME@_calls.txt'
$n = if (Test-Path $calls) { @(Get-Content $calls).Count } else { 0 }
Add-Content $calls (($PSBoundParameters.Keys | Sort-Object) -join ',')
$step = $plan[[Math]::Min($n, $plan.Count - 1)]
if ($step.sleep) { Start-Sleep -Seconds $step.sleep }
foreach ($line in ($step.stdout -split "`n")) { Write-Output $line }
exit ([int] $step.exit)
'''


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))['record']


def fixture_lines(name):
    return [l for l in (FIXTURES / name).read_text(encoding='utf-8').splitlines() if not l.startswith('#')]


def iso(seconds_ago=0):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - seconds_ago))


class MainCheckCase(RepoCase):
    def setUp(self):
        super().setUp()
        self.aux = tempfile.TemporaryDirectory(prefix='main_check_aux_')
        self.addCleanup(self.aux.cleanup)
        self.remote = Path(self.aux.name) / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(self.remote)], check=True)
        self.git('config', 'remote.origin.url', self.remote.as_uri())
        for key, value in SUB_ENV.items():
            os.environ[key] = value
        self.write('.github/workflows/main_check.yml', WORKFLOW)
        self.write('.claude/scripts/harness_tests.py', SHIM)
        self.write('.claude/scripts/planted_gate.ps1', GATE_STUB.replace('@NAME@', 'gate'))
        self.declare_gate()
        self.write('.claude/tests/test_main_check_planted.py', 'print("planted proof ran")\n')
        self.write('.gitignore', '.claude/scratch/\n/stamp.json\n/slots/\n/harness_*\n*.lock\n')
        self.commit('planted main check inputs')
        self.base = self.git('rev-parse', 'HEAD')
        self.push()
        self.seed_state(self.base)
        self.tips = [self.base]

    def declare_gate(self, declared=True, **main):
        """Plant the project's `gate_cadence.main`: no keyword is the stub's own declaration, a None value drops that field,
        `declared=False` declares no gate."""
        gate = dict({'command': GATE_ARGV, 'retry_exits': [7, 8, 124], 'outputs': GATE_OUTPUTS}, **main)
        gate = {k: v for k, v in gate.items() if v is not None}
        self.write(ADAPTATION, json.dumps({'gate_cadence': {'main': gate}} if declared else {}))

    @contextlib.contextmanager
    def variant(self, label):
        try:
            yield
        except AssertionError as exc:
            raise AssertionError('[%s] %s' % (label, exc)) from None

    def push(self):
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        return self.git('rev-parse', 'HEAD')

    def commit_as(self, message, session=None, **files):
        for rel, text in files.items():
            self.write(rel.replace('__', '/'), text)
        self.git('add', '--all')
        args = ['commit', '-q', '-m', message] + (['-m', 'Claude-Session: ' + session] if session else [])
        self.git(*args)
        return self.git('rev-parse', 'HEAD')

    def land(self, message='landed', session=None, **files):
        self.commit_as(message, session, **files)
        sha = self.push()
        self.tips.append(sha)
        return sha

    def plumb(self, args, text=None):
        result = subprocess.run(['git', '-C', str(self.repo), *args], input=None if text is None else text.encode(),
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode().strip()

    def state_doc(self, checked_to, rows=(), run=None):
        return {'version': 1, 'checkedTo': checked_to, 'run': run,
                'sources': {'baselineLinux': {'readAt': None, 'status': 'pending', 'reason': 'planted', 'pendingSha': None}},
                'rows': list(rows)}

    def state_commit(self, doc, parent=None):
        blob = self.plumb(['hash-object', '-w', '--stdin'], json.dumps(doc, indent=1) + '\n')
        tree = self.plumb(['mktree'], '100644 blob %s\tmain_check.json\n' % blob)
        return self.plumb(['commit-tree', tree] + (['-p', parent] if parent else []) + ['-m', 'planted state'])

    def seed_state(self, checked_to, rows=(), run=None):
        old = self.remote_ref('main-check-state')
        commit = self.state_commit(self.state_doc(checked_to, rows, run), old)
        self.git('push', '-q', '-f', 'origin', commit + ':refs/heads/main-check-state')

    def remote_ref(self, branch):
        result = subprocess.run(['git', '--git-dir', str(self.remote), 'rev-parse', '-q', '--verify', 'refs/heads/' + branch],
                                capture_output=True, text=True)
        return result.stdout.strip() or None

    def remote_state(self):
        text = subprocess.run(['git', '--git-dir', str(self.remote), 'show', 'main-check-state:main_check.json'],
                              capture_output=True, text=True, check=True).stdout
        return json.loads(text)

    def row(self, name, kind='proof', commit=None, session=None, cleared_at=None, cleared_by=None, platform=PLATFORM,
            first_line='FAIL: planted', cause=None):
        return {'name': name, 'kind': kind, 'platform': platform, 'commit': commit, 'session': session,
                'firstLine': first_line, 'firstSeen': iso(3600), 'clearedAt': cleared_at, 'clearedBy': cleared_by,
                'cause': cause}

    def open_rows(self, platform=PLATFORM):
        return [r for r in self.remote_state()['rows'] if r['clearedAt'] is None and r['platform'] == platform]

    def plan_close(self, record='green', **plan):
        if record == 'green':
            record = fixture('close_record_green.json')
        elif record is not None:
            record = dict(fixture('close_record_green.json'), **record)
        self.write('.claude/scratch/close_plan.json', json.dumps(dict(plan, record=record)))

    def red_record(self, *reds, cannot_run=(), pre_existing=(), proofs=None):
        return {'verdict': 'red', 'red': list(reds), 'cannotRun': list(cannot_run), 'preExisting': list(pre_existing),
                'proofs': proofs or [r['proof'] for r in reds]}

    def red(self, proof, commit, detail='FAIL: unit regressed\n'):
        return {'proof': proof, 'commit': commit, 'detail': detail}

    def gate_step(self, kind, ident=None, tip=None, failed=None, exit=None):
        """One gate invocation: the verdict's exit code and the `FAILED_TEST=` lines a FAIL names."""
        if kind == 'IMPORT_FAIL':
            kind, failed = 'FAIL', []
        if failed is None and kind == 'FAIL':
            failed = ['Planted.Tests.Default_Fails']
        lines = ['STAGE planted gate'] + ['FAILED_TEST=' + i for i in failed or []] + ['VERDICT=' + kind]
        return {'stdout': '\n'.join(lines) + '\n', 'exit': EXIT_CODES[kind] if exit is None else exit}

    def plan_gate(self, *steps):
        self.write('.claude/scratch/gate_plan.json', json.dumps(list(steps)))

    def gate_calls(self):
        path = self.repo / '.claude' / 'scratch' / 'gate_calls.txt'
        return path.read_text().splitlines() if path.exists() else []

    def close_calls(self):
        path = self.repo / '.claude' / 'scratch' / 'close_calls.jsonl'
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def run_dir(self, run_id):
        return self.repo / '.claude' / 'scratch' / 'main_check' / run_id

    def record(self, run_id, name):
        path = self.run_dir(run_id) / (name + '.json')
        if not path.exists():
            self.fail('no %s record for run %s; stage output:\n%s' % (name, run_id, self.last_out))
        return json.loads(path.read_text(encoding='utf-8'))

    last_out = ''

    def mc(self, *args, env=None):
        full = dict(os.environ, **(env or {}))
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=600,
                                cwd=str(self.repo))
        return result.returncode, result.stdout + result.stderr

    def stage(self, name, run_id='run1', *extra, env=None):
        return self.mc(name, '--repo', str(self.repo), '--run-id', run_id, *extra, env=env)

    def whole_run(self, run_id='run1', *extra):
        outs = [self.stage(name, run_id, *(extra if name == 'close' else ())) for name in ('close', 'gate', 'finalize')]
        self.last_out = '\n'.join(o[1] for o in outs)
        return outs[-1][0], self.last_out


class StateAndVerdict(MainCheckCase):
    def test_a_red_proof_planted_in_the_range_is_a_new_red_with_its_culprit(self):
        self.plant('good')
        self.commit_as('plant a proof and its input')
        good = self.push()
        self.seed_state(good)
        self.land('unrelated edit', session='sess-a', **{'.claude__hooks__other.py': 'X = 1\n'})
        culprit = self.land('break the unit', session='sess-b', **{'.claude__hooks__unit.py': 'VALUE = "bad"\n'})
        later = self.land('doc edit', session='sess-c', **{'.claude__notes.md': 'doc\n'})
        runner = [sys.executable, str(self.repo / '.claude' / 'scripts' / 'harness_tests.py'), '--close', '--now']
        alone = subprocess.run(runner + ['--base', good], cwd=self.repo, capture_output=True, text=True)
        stamp = json.loads(Path(os.environ['HARNESS_TEST_STAMP']).read_text())['close']
        self.assertEqual(stamp['paths'], [], 'today --base alone selects nothing on a range already on origin/main: ' + alone.stdout)
        rc, out = self.whole_run('r1')
        close = self.record('r1', 'close')['record']
        self.assertEqual([r['proof'] for r in close['red']], ['test_unit.py'], out)
        self.assertEqual(close['red'][0]['commit'], culprit, out)
        state = self.remote_state()
        self.assertEqual(state['checkedTo'], later, 'a red with nothing unrun moves checkedTo: ' + out)
        rows = [r for r in state['rows'] if r['name'] == 'test_unit.py']
        self.assertEqual([(r['kind'], r['platform'], r['commit'], r['session']) for r in rows],
                         [('proof', PLATFORM, culprit, 'sess-b')], out)
        self.assertTrue(rows[0]['firstLine'] and rows[0]['firstSeen'] and rows[0]['clearedAt'] is None)

    def test_checked_to_moves_on_green_and_on_red_with_nothing_unrun(self):
        sha = self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        self.assertEqual(self.remote_state()['checkedTo'], sha, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)
        sha = self.land('second', **{'.claude__notes.md': 'b\n'})
        self.plan_close(self.red_record(self.red('test_unit.py', sha)))
        rc, out = self.whole_run('r2')
        self.assertEqual(self.remote_state()['checkedTo'], sha, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'red', out)

    def test_checked_to_moves_on_a_red_with_unrun_proofs_and_writes_a_row_per_entry(self):
        sha = self.land('first', **{'.claude__tests__test_other.py': 'print("ok")\n'})
        self.plan_close(self.red_record(self.red('test_unit.py', sha), cannot_run=['test_other.py']))
        rc, out = self.whole_run('r1')
        state = self.remote_state()
        self.assertEqual(state['checkedTo'], sha, out)
        rows = {r['name']: r for r in self.open_rows()}
        self.assertEqual(sorted(rows), ['test_other.py', 'test_unit.py'], out)
        self.assertEqual((rows['test_unit.py']['commit'], rows['test_unit.py']['cause']), (sha, None), out)
        self.assertEqual((rows['test_other.py']['commit'], rows['test_other.py']['firstLine']),
                         (None, 'could not run in the close'), out)

    def test_an_incomplete_close_holding_behavior_claims_advances_with_an_uncovered_row(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close({'verdict': 'incomplete', 'red': [], 'cannotRun': ['<behavior claims>'], 'proofs': []})
        rc, out = self.whole_run('r1')
        self.assertEqual(self.remote_state()['checkedTo'], self.tips[-1], out)
        rows = self.open_rows()
        self.assertEqual([(r['name'], r['cause']) for r in rows], [('<behavior claims>', 'uncovered')], out)
        self.assertIn('r1', rows[0]['firstLine'], 'the uncovered row names the run that could not name the proof: ' + out)

    def test_a_timeout_red_gets_the_timeout_cause_and_no_commit(self):
        self.land('first', **{'.claude__tests__test_slow_unit.py': 'print("ok")\n',
                              '.claude__tests__test_unit.py': 'print("ok")\n'})
        sha = self.tips[-1]
        record = fixture('close_record_timeout.json')
        self.plan_close(record)
        rc, out = self.whole_run('r1')
        rows = {r['name']: r for r in self.open_rows()}
        self.assertEqual((rows['test_slow_unit.py']['cause'],
                          rows['test_slow_unit.py']['commit']), ('timeout', None), out)
        self.assertEqual((rows['test_unit.py']['cause'], rows['test_unit.py']['commit']),
                         (None, record['red'][1]['commit']), out)
        self.assertEqual(self.remote_state()['checkedTo'], sha, out)

    def test_consecutive_ranges_chain(self):
        first = self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.whole_run('r1')
        second = self.land('second', **{'.claude__notes.md': 'b\n'})
        self.whole_run('r2')
        starts = [c['argv'][c['argv'].index('--range-start') + 1] for c in self.close_calls()]
        self.assertEqual(starts, [self.base, first])
        self.assertEqual(self.remote_state()['checkedTo'], second)

    def test_a_gate_outputs_only_range_owes_no_gate(self):
        self.land('gate outputs', **{'out__baseline.json': '{}\n', 'out__durations.json': '{}\n'})
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS', '20261118-000001-1'))
        rc, out = self.whole_run('r1')
        self.assertEqual(self.gate_calls(), [], out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)

    def test_a_dispatched_run_owes_the_gate_on_an_unchanged_tree(self):
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS', '20261118-000002-2', tip=self.base))
        rc, out = self.whole_run('r1', '--dispatched')
        self.assertEqual(self.gate_calls(), ['NoBaselineUpdate,RetryOnly'], out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)

    def test_live_until_is_the_start_plus_the_job_timeout(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        rc, out = self.stage('close', 'r1')
        run = self.remote_state()['run']
        self.assertEqual(run['stage'], 'close', out)
        self.assertIsNone(run['end'], 'a run whose finalize never ran leaves its end null: ' + out)
        started = time.mktime(time.strptime(run['start'], '%Y-%m-%dT%H:%M:%SZ'))
        live = time.mktime(time.strptime(run['liveUntil'], '%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(live - started, JOB_MINUTES * 60, out)

    def test_a_close_that_raises_after_its_start_write_still_gets_a_finalize_that_leaves_checked_to(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        sys.path.insert(0, str(SCRIPT.parent))
        import main_check
        from unittest.mock import patch
        args = ['close', '--repo', str(self.repo), '--run-id', 'r1']
        with patch.object(main_check, 'run_logged', side_effect=RuntimeError('planted raise')):
            with self.assertRaises(RuntimeError):
                main_check.main(args)
        self.assertEqual(self.remote_state()['run']['stage'], 'close')
        rc, out = self.stage('finalize', 'r1')
        state = self.remote_state()
        self.assertEqual(state['checkedTo'], self.base, out)
        self.assertEqual(state['run']['outcome'], 'no-verdict', out)
        self.assertIn('close', state['run']['detail'], out)
        self.assertIsNotNone(state['run']['end'], out)

    def test_finalize_takes_only_its_own_runs_close_and_gate_records(self):
        self.land('project edit', **{'project.godot': 'config\n'})
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS', '20261118-000003-3'))
        self.stage('close', 'prior')
        self.stage('gate', 'prior')
        self.stage('close', 'new')
        rc, out = self.stage('finalize', 'new')
        state = self.remote_state()
        self.assertEqual(state['checkedTo'], self.base, 'a green gate record at the same tip from an earlier run never stands in: ' + out)
        self.assertEqual(state['run']['outcome'], 'no-verdict', out)
        self.assertIn('gate', state['run']['detail'], out)
        self.stage('close', 'again')
        self.stage('gate', 'again')
        (self.run_dir('again') / 'close.json').unlink()
        rc, out = self.stage('finalize', 'again')
        state = self.remote_state()
        self.assertEqual(state['checkedTo'], self.base, 'a green close record from an earlier run never stands in: ' + out)
        self.assertIn('close', state['run']['detail'], out)

    def test_close_records_its_wall_and_stage_times(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.whole_run('r1')
        wall = self.remote_state()['run']['wall']
        self.assertEqual(sorted(wall), ['close', 'finalize', 'gate'])
        self.assertTrue(all(wall[k] is None or wall[k] >= 0 for k in wall))

    def test_a_close_that_crashes_without_a_record_is_no_verdict_with_its_cause(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.write('.claude/scratch/close_plan.json', json.dumps({'record': None, 'exit': 3, 'lines': ['harness_tests: close INCOMPLETE: planted']}))
        rc, out = self.whole_run('r1')
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['run']['outcome']), (self.base, 'no-verdict'), out)
        self.assertIn('INCOMPLETE: planted', state['run']['detail'], out)

    def test_a_killed_close_and_a_superseded_close_leave_checked_to(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close(None, sleep=30)
        rc, out = self.stage('close', 'killed', '--cap-minutes', '0.01')
        self.assertTrue(self.record('killed', 'close')['timedOut'], out)
        rc, out = self.stage('finalize', 'killed')
        self.assertEqual(self.remote_state()['checkedTo'], self.base, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'no-verdict', out)
        self.plan_close(None, exit=2, lines=['harness_tests: a newer close superseded this one; it stops without a verdict'])
        self.stage('close', 'superseded')
        rc, out = self.stage('finalize', 'superseded')
        self.assertEqual(self.remote_state()['checkedTo'], self.base, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'no-verdict', out)

    def test_a_second_run_starts_at_the_first_runs_tip_after_a_red_with_unrun_proofs(self):
        first = self.land('first', **{'.claude__tests__test_other.py': 'print("ok")\n'})
        self.plan_close(self.red_record(self.red('test_unit.py', first), cannot_run=['test_other.py']))
        self.whole_run('r1')
        second = self.land('second', **{'.claude__notes.md': 'b\n'})
        self.whole_run('r2')
        starts = [c['argv'][c['argv'].index('--range-start') + 1] for c in self.close_calls()]
        self.assertEqual(starts, [self.base, first])
        self.assertEqual(self.remote_state()['checkedTo'], second)

    def test_each_stage_sees_the_settings_env_a_session_gets(self):
        self.write('.claude/settings.json', json.dumps({'env': {'MC_PROBE': 'project', 'MC_BOTH': 'project'}}))
        self.commit_as('settings')
        self.push()
        home = Path(self.aux.name) / 'home'
        (home / '.claude').mkdir(parents=True)
        (home / '.claude' / 'settings.json').write_text(json.dumps({'env': {'MC_BOTH': 'user'}}))
        self.plan_close('green')
        env = {'USERPROFILE': str(home), 'HOME': str(home)}
        self.mc('close', '--repo', str(self.repo), '--run-id', 'r1', env=env)
        call = self.close_calls()[-1]
        self.assertEqual((call['probe'], call['both']), ('project', 'project'))
        self.assertTrue(call['deadline'], 'the close gets a deadline short of the step cap')

    def test_run_folders_older_than_two_weeks_are_swept_at_close_start(self):
        old = self.run_dir('ancient')
        old.mkdir(parents=True)
        (old / 'start.json').write_text('{}')
        past = time.time() - 15 * 86400
        os.utime(old / 'start.json', (past, past))
        os.utime(old, (past, past))
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.stage('close', 'r1')
        self.assertFalse(old.exists())
        self.assertTrue(self.run_dir('r1').exists())


class GateStage(MainCheckCase):
    def project_range(self, session='sess-p'):
        self.land('harness edit', session='sess-h', **{'.claude__notes.md': 'a%d\n' % len(self.tips)})
        return self.land('project edit', session=session, **{'project.godot': 'config%d\n' % len(self.tips)})

    def test_a_gate_fail_writes_a_row_per_failed_test_with_the_ranges_one_non_harness_commit_and_never_reruns(self):
        culprit = self.project_range()
        self.plan_close('green')
        ids = ['Planted.Tests.Integration.First.FirstTests.First_Fails',
               'Planted.Tests.Logic.Other.SecondTests.Second_Fails']
        self.plan_gate(self.gate_step('FAIL', '20261118-000010-10', failed=ids + [ids[0].rsplit('.', 1)[1]]),
                       self.gate_step('PASS', '20261118-000011-11'))
        rc, out = self.whole_run('r1')
        self.assertEqual(len(self.gate_calls()), 1, 'a FAIL is never rerun: ' + out)
        rows = [r for r in self.remote_state()['rows'] if r['kind'] == 'gate']
        self.assertEqual(sorted(r['name'] for r in rows), sorted(ids), out)
        self.assertEqual({(r['commit'], r['session'], r['platform']) for r in rows}, {(culprit, 'sess-p', PLATFORM)})
        self.assertEqual(self.remote_state()['checkedTo'], culprit, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'red', out)

    def test_a_later_full_pass_clears_every_gate_row(self):
        self.project_range()
        self.land('a proof the row names', **{'.claude__tests__test_x.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('A.B.One', kind='gate'), self.row('A.B.Two', kind='gate'),
                                          self.row('test_x.py', kind='proof')])
        self.plan_close({'verdict': 'green', 'proofs': []})
        self.plan_gate(self.gate_step('PASS', '20261118-000012-12'))
        rc, out = self.whole_run('r1')
        rows = {r['name']: r for r in self.remote_state()['rows']}
        for name in ('A.B.One', 'A.B.Two'):
            self.assertTrue(rows[name]['clearedAt'] and rows[name]['clearedBy'] == self.tips[-1], out)
        self.assertIsNone(rows['test_x.py']['clearedAt'], 'a full gate PASS clears gate rows only: ' + out)

    def test_contention_hang_and_busy_take_the_gates_next_step_before_they_are_recorded(self):
        for verdict in ('CONTENTION', 'HANG', 'BUSY'):
            with self.variant(verdict):
                self.project_range()
                self.seed_state(self.base)
                self.write('.claude/scratch/gate_calls.txt', '')
                self.plan_close('green')
                self.plan_gate(self.gate_step(verdict, '20261118-%06d-1' % EXIT_CODES[verdict]),
                               self.gate_step('PASS', '20261118-%06d-2' % EXIT_CODES[verdict]))
                rc, out = self.whole_run('r-' + verdict)
                self.assertEqual(len(self.gate_calls()), 2, out)
                self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)
                self.assertEqual(self.remote_state()['checkedTo'], self.tips[-1], out)

    def test_each_no_verdict_gate_outcome_leaves_checked_to_writes_no_row_and_records_the_outcome(self):
        self.project_range()
        for verdict in ('WARN', 'INVALID', 'INCOMPLETE', 'BLOCKED', 'BUSY', 'CONTENTION', 'HANG', 'IMPORT_FAIL'):
            with self.variant(verdict):
                self.seed_state(self.base)
                (self.repo / '.claude' / 'scratch' / 'gate_calls.txt').unlink(missing_ok=True)
                ident = '20261118-0001%02d' % (sum(map(ord, verdict)) % 100)
                self.plan_close('green')
                self.plan_gate(self.gate_step(verdict, ident + '-1'), self.gate_step(verdict, ident + '-2'))
                rc, out = self.whole_run('r-' + verdict)
                state = self.remote_state()
                self.assertEqual(state['checkedTo'], self.base, out)
                self.assertEqual(state['rows'], [], out)
                self.assertEqual(state['run']['outcome'], 'no-verdict', out)
                self.assertIn('gate', state['run']['detail'], out)
                self.assertNotEqual(rc, 0, 'a run with no verdict ends its job red: ' + out)

    def test_a_gate_killed_by_its_cap_is_no_verdict(self):
        self.project_range()
        self.plan_close('green')
        step = self.gate_step('PASS', '20261118-000020-20')
        step['sleep'] = 30
        self.plan_gate(step)
        self.stage('close', 'r1')
        rc, out = self.stage('gate', 'r1', '--cap-minutes', '0.03')
        self.assertTrue(self.record('r1', 'gate')['timedOut'], out)
        rc, out = self.stage('finalize', 'r1')
        self.assertEqual(self.remote_state()['checkedTo'], self.base, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'no-verdict', out)

    def test_the_gate_runs_without_a_baseline_move_and_without_replaying_a_fail(self):
        self.project_range()
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS', '20261118-000021-21'))
        self.whole_run('r1')
        self.assertEqual(self.gate_calls(), ['NoBaselineUpdate,RetryOnly'])


class GateContract(MainCheckCase):
    """The gate is whatever `gate_cadence.main` declares: its exit code and its `FAILED_TEST=` lines are the verdict."""

    def land_project_change(self):
        self.land('harness edit', session='sess-h', **{'.claude__notes.md': 'a\n'})
        return self.land('project edit', session='sess-p', **{'project.godot': 'config\n'})

    def redeclare(self, **kwargs):
        self.declare_gate(**kwargs)
        self.commit_as('declare the gate')
        self.push()
        self.seed_state(self.git('rev-parse', 'HEAD'))

    def test_a_declared_command_other_than_the_default_script_runs(self):
        self.write('.claude/scripts/other_gate.ps1', GATE_STUB.replace('@NAME@', 'other'))
        self.redeclare(command=['pwsh', '-NoProfile', '-File', '.claude/scripts/other_gate.ps1'])
        self.land_project_change()
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS'))
        rc, out = self.whole_run('r1')
        other = self.repo / '.claude' / 'scratch' / 'other_calls.txt'
        self.assertTrue(other.exists() and self.gate_calls() == [], out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)

    def test_an_exit_the_declaration_lists_as_retry_reruns_once_with_no_verdict_line_needed(self):
        self.redeclare(retry_exits=[9])
        self.land_project_change()
        self.plan_close('green')
        self.plan_gate({'stdout': 'tool fault\n', 'exit': 9}, self.gate_step('PASS'))
        rc, out = self.whole_run('r1')
        self.assertEqual(len(self.gate_calls()), 2, out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'green', out)

    def test_an_exit_never_listed_for_retry_runs_once_and_is_no_verdict(self):
        self.redeclare(retry_exits=None)
        self.land_project_change()
        self.plan_close('green')
        self.plan_gate(self.gate_step('CONTENTION'), self.gate_step('PASS'))
        rc, out = self.whole_run('r1')
        self.assertEqual(len(self.gate_calls()), 1, out)
        state = self.remote_state()
        self.assertEqual((state['run']['outcome'], state['checkedTo']), ('no-verdict', self.git('rev-parse', 'HEAD~2')), out)

    def test_two_failed_test_lines_one_a_bare_duplicate_write_one_row_for_the_full_id(self):
        culprit = self.land_project_change()
        full = 'Planted.Tests.Logic.Math.Add_Fails'
        self.plan_close('green')
        self.plan_gate(self.gate_step('FAIL', failed=[full, 'Add_Fails']))
        rc, out = self.whole_run('r1')
        rows = [r for r in self.remote_state()['rows'] if r['kind'] == 'gate']
        self.assertEqual([(r['name'], r['commit']) for r in rows], [(full, culprit)], out)

    def test_a_fail_exit_naming_no_test_is_no_verdict_and_checked_to_stays(self):
        self.land_project_change()
        self.plan_close('green')
        self.plan_gate(self.gate_step('FAIL', failed=[]))
        rc, out = self.whole_run('r1')
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['rows'], state['run']['outcome']), (self.base, [], 'no-verdict'), out)

    def test_a_pass_exit_stands_with_no_ledger_file_in_the_tree(self):
        tip = self.land_project_change()
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS'))
        rc, out = self.whole_run('r1')
        self.assertFalse((self.repo / '.claude' / 'scratch' / 'gate_ledger').exists())
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['run']['outcome']), (tip, 'green'), out)

    def test_a_project_declaring_no_gate_owes_none_even_on_a_dispatched_run(self):
        self.redeclare(declared=False)
        tip = self.land_project_change()
        self.plan_close('green')
        rc, out = self.whole_run('r1', '--dispatched')
        self.assertEqual(self.gate_calls(), [], out)
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['run']['outcome'], rc), (tip, 'green', 0), out)

    def test_a_range_changing_only_a_declared_output_owes_no_gate_and_an_undeclared_one_does(self):
        self.redeclare(outputs=['out/gate_report.json'])
        sys.path.insert(0, str(SCRIPT.parent))
        import main_check
        before = self.git('rev-parse', 'HEAD')
        self.commit_as('write the output', **{'out__gate_report.json': '{}\n'})
        self.assertFalse(main_check.gate_owed(str(self.repo), before, self.git('rev-parse', 'HEAD'))[0])
        before = self.git('rev-parse', 'HEAD')
        self.commit_as('write another file', **{'out__other_report.json': '{}\n'})
        self.assertTrue(main_check.gate_owed(str(self.repo), before, self.git('rev-parse', 'HEAD'))[0])

    def test_a_malformed_declaration_is_no_verdict_not_a_silent_skip(self):
        self.write(ADAPTATION, json.dumps({'gate_cadence': {'main': {'command': 'run it'}}}))
        self.commit_as('malformed declaration')
        self.push()
        start = self.git('rev-parse', 'HEAD')
        self.seed_state(start)
        self.land_project_change()
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['run']['outcome']), (start, 'no-verdict'), out)
        self.assertEqual(self.gate_calls(), [], out)


class CloseStage(MainCheckCase):
    def test_the_submodule_moves_to_the_tips_gitlink_and_an_unreachable_pin_is_no_verdict(self):
        sub = Path(self.aux.name) / 'sub'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(sub)], check=True)
        gitc = lambda *a, cwd=sub: subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *a],
                                                   capture_output=True, text=True, check=True).stdout.strip()
        (sub / 'f.txt').write_text('1\n')
        gitc('add', '-A')
        gitc('commit', '-q', '-m', 's1')
        s1 = gitc('rev-parse', 'HEAD')
        self.git('submodule', 'add', '-q', sub.as_uri(), 'vendor/sub')
        self.commit_as('add the submodule')
        self.push()
        self.seed_state(self.git('rev-parse', 'HEAD'))
        (sub / 'f.txt').write_text('2\n')
        gitc('commit', '-q', '-am', 's2')
        s2 = gitc('rev-parse', 'HEAD')
        self.git('-C', 'vendor/sub', 'fetch', '-q', 'origin')
        self.git('-C', 'vendor/sub', 'checkout', '-q', s2)
        self.land('move the pin')
        self.git('-C', 'vendor/sub', 'checkout', '-q', s1)
        self.plan_close('green')
        rc, out = self.stage('close', 'r1')
        self.assertEqual(self.git('-C', 'vendor/sub', 'rev-parse', 'HEAD'), s2, out)
        self.assertNotEqual(s1, s2)
        self.git('update-index', '--cacheinfo', '160000,1234567890abcdef1234567890abcdef12345678,vendor/sub')
        self.git('commit', '-q', '-m', 'pin a commit the submodule remote never got')
        self.tips.append(self.push())
        rc, out = self.whole_run('r2')
        state = self.remote_state()
        self.assertEqual(state['run']['outcome'], 'no-verdict', out)
        self.assertIn('submodule', state['run']['detail'], out)
        self.assertNotEqual(state['checkedTo'], self.tips[-1], out)

    def test_a_planted_proof_that_prints_default_ref_sees_the_default_and_the_range_start_never_leaks(self):
        self.write('.claude/hooks/unit.py', 'VALUE = 1\n')
        self.write('.claude/tests/test_unit.py',
                   "import os, sys\nsys.path.insert(0, %r)\nimport harness_tests\n"
                   "ref = harness_tests._default_ref(os.getcwd())\nprint('default_ref', ref)\n"
                   "if ref != 'origin/main':\n    print('FAIL: default ref is ' + str(ref))\n    sys.exit(1)\n"
                   % str(ROOT / '.claude' / 'scripts'))
        self.commit_as('proof that prints the default ref')
        base = self.push()
        self.seed_state(base)
        self.land('edit the input', **{'.claude__hooks__unit.py': 'VALUE = 2\n'})
        rc, out = self.whole_run('r1')
        close = self.record('r1', 'close')['record']
        self.assertEqual((close['verdict'], close['proofs']), ('green', ['test_unit.py']), out)

    def test_range_start_holds_in_a_worktree_on_a_branch_with_an_upstream(self):
        self.plant('good')
        self.commit_as('plant')
        good = self.push()
        self.git('checkout', '-q', '-b', 'topic', '--track', 'origin/main')
        self.land('edit', **{'.claude__hooks__unit.py': 'VALUE = "good2"\n'})
        runner = [sys.executable, str(self.repo / '.claude' / 'scripts' / 'harness_tests.py'), '--close', '--now']
        subprocess.run(runner + ['--range-start', good], cwd=self.repo, capture_output=True, text=True)
        close = json.loads(Path(os.environ['HARNESS_TEST_STAMP']).read_text())['close']
        self.assertEqual((close['base'], close['paths']), (good, ['.claude/hooks/unit.py']))

    def test_a_range_changing_only_the_main_check_workflow_selects_its_proof(self):
        self.land('edit the workflow only', **{'.github__workflows__main_check.yml': WORKFLOW + '# edit\n'})
        rc, out = self.whole_run('r1')
        close = self.record('r1', 'close')['record']
        self.assertEqual(close['paths'], ['.github/workflows/main_check.yml'], out)
        self.assertEqual(close['proofs'], ['test_main_check_planted.py'], out)

    def test_range_start_and_base_cannot_be_combined_and_range_start_needs_close(self):
        runner = [sys.executable, str(REAL_RUNNER), '--repo', str(self.repo)]
        accepted = subprocess.run(runner + ['--close', '--now', '--range-start', self.base], capture_output=True, text=True)
        self.assertNotIn('unrecognized', accepted.stderr)
        both = subprocess.run(runner + ['--close', '--base', self.base, '--range-start', self.base], capture_output=True, text=True)
        alone = subprocess.run(runner + ['--range-start', self.base], capture_output=True, text=True)
        self.assertNotIn('unrecognized', both.stderr + alone.stderr)
        self.assertNotEqual(both.returncode, 0)
        self.assertNotEqual(alone.returncode, 0)

    def test_a_missing_state_branch_is_a_stage_failure_that_writes_nothing(self):
        self.git('push', '-q', 'origin', ':refs/heads/main-check-state')
        rc, out = self.stage('close', 'r1')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('main-check-state', out)
        self.assertIsNone(self.remote_ref('main-check-state'))


class Rows(MainCheckCase):
    def test_pre_existing_reds_without_a_row_become_commit_null_rows_and_a_red_with_a_row_adds_no_second(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.land('the known proof exists', **{'.claude__tests__test_known.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_known.py', commit='abc', session='s-known')])
        self.plan_close({'verdict': 'green', 'preExisting': ['test_known.py', 'test_old.py']})
        self.whole_run('r1')
        rows = self.open_rows()
        self.assertEqual(sorted(r['name'] for r in rows), ['test_known.py', 'test_old.py'])
        by = {r['name']: r for r in rows}
        self.assertEqual((by['test_old.py']['commit'], by['test_old.py']['session'], by['test_old.py']['kind']), (None, None, 'proof'))
        self.assertEqual(by['test_known.py']['commit'], 'abc')

    def test_a_proof_row_clears_on_a_recorded_pass_and_keeps_clearedAt_and_clearedBy(self):
        self.land('first', **{'.claude__notes.md': 'a\n', '.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', commit='abc')])
        self.plan_close({'verdict': 'green', 'proofs': ['test_unit.py']})
        self.whole_run('r1')
        row = [r for r in self.remote_state()['rows'] if r['name'] == 'test_unit.py'][0]
        self.assertTrue(row['clearedAt'])
        self.assertEqual(row['clearedBy'], self.tips[-1])
        self.assertEqual(row['commit'], 'abc')

    def test_a_proof_selected_but_not_run_stays_open(self):
        self.land('first', **{'.claude__notes.md': 'a\n', '.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py')])
        self.plan_close({'verdict': 'incomplete', 'proofs': ['test_unit.py'], 'cannotRun': ['test_unit.py']})
        self.whole_run('r1')
        self.assertEqual([r['name'] for r in self.open_rows()], ['test_unit.py'])

    def test_a_deleted_proofs_row_clears_with_the_deleting_tip(self):
        self.seed_state(self.base, rows=[self.row('test_gone.py')])
        sha = self.land('nothing of that proof exists', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.whole_run('r1')
        row = self.remote_state()['rows'][0]
        self.assertEqual((row['name'], row['clearedBy']), ('test_gone.py', sha))
        self.assertTrue(row['clearedAt'])

    def test_the_close_is_handed_every_open_proof_row_and_a_recorded_pass_clears_it(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n',
                              '.claude__tests__test_still.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', cause='timeout', first_line='TIMEOUT after 900s'),
                                          self.row('test_still.py')])
        sha = self.tips[-1]
        self.plan_close({'verdict': 'red', 'proofs': ['test_unit.py', 'test_still.py'],
                         'red': [self.red('test_still.py', sha, 'FAIL: planted\n')]})
        rc, out = self.whole_run('r1')
        close = [c for c in self.close_calls() if '--also' in c['argv']][0]
        self.assertEqual(sorted(close['argv'][close['argv'].index('--also') + 1:]),
                         ['.claude/tests/test_still.py', '.claude/tests/test_unit.py'], out)
        rows = {r['name']: r for r in self.remote_state()['rows']}
        self.assertTrue(rows['test_unit.py']['clearedAt'], 'a recorded pass clears a timeout row: ' + out)
        self.assertIsNone(rows['test_still.py']['clearedAt'], out)

    def test_a_later_red_on_a_proof_with_an_open_row_keeps_a_row_open_with_its_new_line(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', cause='timeout', first_line='TIMEOUT after 900s')])
        self.plan_close({'verdict': 'red', 'proofs': ['test_unit.py'],
                         'red': [self.red('test_unit.py', self.tips[-1], 'AssertionError: a new break\n')]})
        rc, out = self.whole_run('r1')
        lines = sorted(r['firstLine'] for r in self.open_rows() if r['name'] == 'test_unit.py')
        self.assertEqual(lines, ['AssertionError: a new break', 'TIMEOUT after 900s'], out)

    def test_rows_groups_a_timeout_row_apart_from_an_unclassified_row(self):
        self.seed_state(self.base, rows=[self.row('test_a.py', cause='timeout', first_line='TIMEOUT after 900s'),
                                          self.row('test_b.py')])
        self.git('fetch', '-q', 'origin', 'main-check-state')
        rc, out = self.mc('--rows', '--repo', str(self.repo))
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index('cause=timeout'), out.index('test_a.py'), out)
        self.assertLess(out.index('test_a.py'), out.index('cause=unclassified'), out)
        self.assertLess(out.index('cause=unclassified'), out.index('test_b.py'), out)

    def test_a_run_clears_only_its_own_platforms_rows(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', platform='linux' if PLATFORM != 'linux' else 'windows')])
        self.plan_close({'verdict': 'green', 'proofs': ['test_unit.py']})
        self.whole_run('r1')
        self.assertTrue(all(r['clearedAt'] is None for r in self.remote_state()['rows']))

    def test_a_cleared_row_stays_thirty_days_and_then_goes(self):
        recent = self.row('test_recent.py', cleared_at=iso(10 * 86400), cleared_by='x')
        stale = self.row('test_stale.py', cleared_at=iso(31 * 86400), cleared_by='x')
        self.seed_state(self.base, rows=[recent, stale])
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.whole_run('r1')
        self.assertEqual([r['name'] for r in self.remote_state()['rows']], ['test_recent.py'])

    def test_rows_prints_open_rows_and_session_filters_them(self):
        rows = [self.row('test_a.py', session='s1'), self.row('test_b.py', session='s2'),
                self.row('test_c.py', session='s1', cleared_at=iso(60), cleared_by='y')]
        self.seed_state(self.base, rows=rows)
        self.git('fetch', '-q', 'origin', 'main-check-state')
        rc, out = self.mc('--rows', '--repo', str(self.repo))
        self.assertEqual(rc, 0, out)
        self.assertEqual([n for n in ('test_a.py', 'test_b.py', 'test_c.py') if n in out], ['test_a.py', 'test_b.py'])
        rc, out = self.mc('--rows', '--session', 's1', '--repo', str(self.repo))
        self.assertEqual([n for n in ('test_a.py', 'test_b.py', 'test_c.py') if n in out], ['test_a.py'])

    def test_rows_with_no_state_ref_fails_without_printing_a_row(self):
        self.git('update-ref', '-d', 'refs/remotes/origin/main-check-state')
        rc, out = self.mc('--rows', '--repo', str(self.repo))
        self.assertNotEqual(rc, 0, out)
        self.assertIn('main-check-state', out)
        self.assertNotIn('test_', out)


class StateBranch(MainCheckCase):
    def test_finalize_pushes_the_state_and_a_second_clone_reads_the_same_rows(self):
        self.land('first', session='sess-1', **{'.claude__notes.md': 'a\n'})
        self.plan_close(self.red_record(self.red('test_unit.py', self.tips[-1])))
        self.whole_run('r1')
        second = Path(self.aux.name) / 'second'
        subprocess.run(['git', 'clone', '-q', self.remote.as_uri(), str(second)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(second), 'fetch', '-q', 'origin', 'main-check-state'], check=True)
        result = subprocess.run([sys.executable, str(SCRIPT), '--rows', '--repo', str(second)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('test_unit.py', result.stdout)
        shown = subprocess.run(['git', '-C', str(second), 'show', 'origin/main-check-state:main_check.json'],
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(shown)['rows'], self.remote_state()['rows'])

    def test_a_lost_lease_reads_the_remote_state_reapplies_the_result_and_pushes_once_more(self):
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close(self.red_record(self.red('test_unit.py', self.tips[-1])))
        self.stage('close', 'r1')
        self.stage('gate', 'r1')
        peer_row = self.row('Peer.Tests.Pending', kind='gate', session='peer')
        peer_doc = self.state_doc(self.base, rows=[peer_row])
        parent = self.remote_ref('main-check-state')
        peer = self.state_commit(peer_doc, parent)
        self.git('push', '-q', 'origin', peer + ':refs/heads/peer-state')
        wrapper = Path(self.aux.name) / 'move_state.sh'
        mark = (Path(self.aux.name) / 'moved').as_posix()
        wrapper.write_text('git-upload-pack "$@"\nrc=$?\nif [ ! -f "%s" ]; then touch "%s"; git --git-dir "%s" update-ref '
                           'refs/heads/main-check-state refs/heads/peer-state; fi\nexit $rc\n'
                           % (mark, mark, self.remote.as_posix()), newline='\n')
        self.git('config', 'remote.origin.uploadpack', 'sh ' + wrapper.as_posix())
        rc, out = self.stage('finalize', 'r1')
        self.assertTrue(Path(mark).exists(), 'the wrapper moved the remote state between fetch and push: ' + out)
        names = sorted(r['name'] for r in self.remote_state()['rows'] if r['clearedAt'] is None)
        self.assertEqual(names, ['Peer.Tests.Pending', 'test_unit.py'], out)
        self.assertEqual(self.remote_state()['run']['outcome'], 'red', out)


class WorkflowFile(unittest.TestCase):
    """The real workflow: its three steps run the stages in order, the later two always, one run at a time."""

    # Read from the commit: a close proves in a sparse `.claude` checkout that holds no `.github`. A repository
    # that has not installed the workflow (the baseline and its profiles) ships only the template it installs from.
    # `./` is relative to ROOT, which the baseline repository nests below its own root; a freshly bootstrapped
    # project has staged its install but holds no commit yet, so the index answers when HEAD cannot.
    _spec = next(rev + path for rev in ('HEAD:./', ':./')
                 for path in ('.github/workflows/main_check.yml', '.claude/templates/workflows/main_check.yml')
                 if subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', rev + path],
                                   capture_output=True).returncode == 0)
    text = subprocess.run(['git', '-C', str(ROOT), 'show', _spec],
                          capture_output=True, text=True, encoding='utf-8', check=True).stdout

    def test_the_three_steps_run_the_stages_in_order_and_gate_and_finalize_always_run(self):
        self.assertEqual(re.findall(r'main_check\.py (close|gate|finalize) ', self.text), ['close', 'gate', 'finalize'])
        steps = re.split(r'\n      - name: ', self.text)[2:]
        self.assertEqual([('if: always()' in step) for step in steps], [False, True, True])

    def test_one_run_goes_at_a_time_and_a_pending_run_is_never_cancelled(self):
        self.assertIn('group: main-check', self.text)
        self.assertIn('cancel-in-progress: false', self.text)

    def test_it_runs_on_the_declared_runner_for_pushes_to_main_and_manual_dispatch(self):
        self.assertIn('runs-on: ${{ fromJSON(vars.MAIN_CHECK_RUNNER) }}', self.text)
        self.assertIn('branches: [main]', self.text)
        self.assertIn('workflow_dispatch:', self.text)

    def test_the_job_timeout_main_check_reads_covers_every_step(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import main_check
        steps = [int(m) for m in re.findall(r'^        timeout-minutes: (\d+)$', self.text, re.M)]
        self.assertEqual(len(steps), 3)
        self.assertGreaterEqual(main_check.parse_job_minutes(self.text), sum(steps))


class GateOwed(MainCheckCase):
    """The gate-owed predicate and its cases, moved from test_land_run.py (land_run.ps1's `LAND stage=gate` branch)."""

    def owed(self, dispatched=False, **files):
        before = self.git('rev-parse', 'HEAD')
        if files:
            self.commit_as('change', **files)
        sys.path.insert(0, str(SCRIPT.parent))
        import main_check
        return main_check.gate_owed(str(self.repo), before, self.git('rev-parse', 'HEAD'), dispatched)[0]

    def test_a_change_outside_the_harness_owes_the_gate(self):
        self.assertTrue(self.owed(**{'project.godot': 'config\n'}))

    def test_harness_only_paths_owe_no_gate(self):
        for rel in ('.claude/notes.md', '.github/workflows/other.yml', 'docs/readme.md', 'NOTES.MD'):
            with self.variant(rel):
                self.assertFalse(self.owed(**{rel.replace('/', '__'): 'x\n'}))

    def test_an_ignore_file_no_build_or_test_reads_owes_no_gate(self):
        for rel in ('.gitignore', '.searchignore'):
            with self.variant(rel):
                self.assertFalse(self.owed(**{rel: 'x\n'}))

    def test_a_gitattributes_change_whose_every_line_names_a_claude_path_owes_no_gate(self):
        self.commit_as('attrs', **{'.gitattributes': '* text=auto eol=lf\n'})
        self.assertFalse(self.owed(**{'.gitattributes': '* text=auto eol=lf\n.claude/baseline.lock.json merge=baseline-lock\n# harness merge drivers\n'}))

    def test_a_gitattributes_line_outside_claude_owes_the_gate(self):
        self.commit_as('attrs', **{'.gitattributes': '* text=auto eol=lf\n'})
        self.assertTrue(self.owed(**{'.gitattributes': '* text=auto eol=lf\n.claude/baseline.lock.json merge=baseline-lock\n*.cs eol=crlf\n'}))

    def test_the_gates_declared_outputs_owe_no_gate_but_any_other_path_does(self):
        self.assertFalse(self.owed(**{'out__baseline.json': '{}\n', 'out__durations.json': '{}\n'}))
        self.assertTrue(self.owed(**{'out__baseline.json': '{"a":1}\n', 'out__Other.cs': 'class T {}\n'}))

    def test_a_dispatched_run_owes_the_gate_even_on_an_empty_range(self):
        self.assertFalse(self.owed())
        self.assertTrue(self.owed(dispatched=True))


class Status(MainCheckCase):
    """`status()` and `--status`: the one health line `/session_end` prints, read from the local state ref."""

    def health(self, now=None):
        sys.path.insert(0, str(SCRIPT.parent))
        import main_check
        subprocess.run(['git', '-C', str(self.repo), 'fetch', '-q', 'origin', 'main-check-state'], capture_output=True)
        return main_check.status(str(self.repo), now=now)

    def run_rec(self, start, outcome='green', end=True, stage='finalize', cap=3600):
        return {'id': 'r0', 'start': iso(start), 'liveUntil': iso(start - cap), 'end': iso(start - 1) if end else None,
                'stage': stage, 'outcome': outcome, 'detail': None, 'wall': None}

    def now_plus(self, seconds):
        return time.time() + seconds

    def test_clean_and_current_gives_nothing(self):
        self.seed_state(self.tips[-1], run=self.run_rec(60))
        self.assertIsNone(self.health())

    def test_a_lag_older_than_the_last_runs_cap_with_no_live_run_gives_the_lag_form(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, cap=3600))
        line = self.health(now=self.now_plus(2 * 3600))
        self.assertIsNotNone(line)
        self.assertIn('checked to ' + self.base[:8], line)
        self.assertIn('1 behind origin/main', line)
        self.assertIn('last run green', line)

    def test_a_last_run_with_no_verdict_gives_its_form_at_once(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, outcome='no-verdict'))
        line = self.health(now=self.now_plus(60))
        self.assertIsNotNone(line)
        self.assertIn('last run no-verdict', line)

    def test_a_lag_inside_the_cap_after_a_verdict_gives_nothing(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, cap=3600))
        self.assertIsNone(self.health(now=self.now_plus(60)))

    def test_no_state_branch_gives_never_checked(self):
        self.git('push', '-q', 'origin', ':refs/heads/main-check-state')
        self.git('update-ref', '-d', 'refs/remotes/origin/main-check-state')
        self.assertEqual(self.health(), 'main: never checked')

    def test_a_malformed_state_file_gives_the_unreadable_form(self):
        blob = self.plumb(['hash-object', '-w', '--stdin'], 'not json\n')
        tree = self.plumb(['mktree'], '100644 blob %s\tmain_check.json\n' % blob)
        commit = self.plumb(['commit-tree', tree, '-m', 'malformed'])
        self.git('push', '-q', '-f', 'origin', commit + ':refs/heads/main-check-state')
        line = self.health()
        self.assertIsNotNone(line)
        self.assertIn('main: check state unreadable (', line)

    def test_an_unended_run_past_its_live_until_gives_the_stopped_form(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(7200, end=False, stage='gate', cap=3600))
        self.assertEqual(self.health(), 'main: check stopped at gate')

    def test_an_unended_run_before_its_live_until_gives_nothing(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, end=False, stage='gate', cap=3600))
        self.assertIsNone(self.health())

    def test_an_unreadable_linux_source_gives_the_linux_lanes_form(self):
        doc = self.state_doc(self.tips[-1], run=self.run_rec(60))
        doc['sources']['baselineLinux'] = {'readAt': iso(0), 'status': 'unreadable', 'reason': 'planted', 'pendingSha': None}
        commit = self.state_commit(doc, self.remote_ref('main-check-state'))
        self.git('push', '-q', '-f', 'origin', commit + ':refs/heads/main-check-state')
        self.assertEqual(self.health(), 'main: linux lanes unreadable (planted)')

    def test_status_flag_prints_the_line_and_exits_zero(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, outcome='no-verdict'))
        self.git('fetch', '-q', 'origin', 'main-check-state')
        rc, out = self.mc('--status', '--repo', str(self.repo))
        self.assertEqual(rc, 0, out)
        self.assertIn('last run no-verdict', out)


class BaselineLinuxSource(MainCheckCase):
    """The finalize stage reads the baseline repo's Linux lanes for the lock's `synced_commit` and records their
    reds as `platform: linux` rows (rules/harness_tooling.md §Land and main check). Each case plants a fake `gh` on PATH; no case
    reaches the network."""

    def install_fake_gh(self, plan):
        bin_dir = Path(self.aux.name) / 'gh-bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        script = bin_dir / 'fake_gh.py'
        script.write_text(FAKE_GH_SOURCE, encoding='utf-8')
        if os.name == 'nt':
            (bin_dir / 'gh.cmd').write_text('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, script),
                                            encoding='utf-8')
        else:
            launcher = bin_dir / 'gh'
            launcher.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding='utf-8')
            os.chmod(launcher, 0o755)
        plan_path = Path(self.aux.name) / 'gh_plan.json'
        plan_path.write_text(json.dumps(plan), encoding='utf-8')
        patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''),
                                'MC_GH_PLAN': str(plan_path)}).start()

    def plant_baseline(self, sha=BASELINE_SHA, log=None, **plan):
        """Commit the lock pin for `sha` to the tip, and answer `gh` from `plan` (plus the run for `sha`)."""
        self.write('.claude/baseline.lock.json', json.dumps({
            'baseline_repo': 'https://github.com/%s.git' % BASELINE_SLUG, 'baseline_ref': 'main',
            'synced_commit': sha, 'schema': 2}))
        plan.setdefault('runs', [{'databaseId': 7, 'headSha': sha, 'status': 'completed',
                                  'conclusion': 'failure', 'event': 'push'}])
        plan.setdefault('message', 'baseline publish\n\nClaude-Session: sess-linux\n')
        if log is not None:
            plan['log'] = str(log)
        self.install_fake_gh(plan)

    def linux_rows(self):
        return [r for r in self.remote_state()['rows'] if r['platform'] == 'linux']

    def source(self):
        return self.remote_state()['sources']['baselineLinux']

    def test_two_proof_level_fail_lines_become_two_linux_rows(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log')
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n', '.claude__tests__test_case_pool.py': 'print("ok")\n',
                                         '.claude__tests__test_baseline_linux_preview.py': 'print("ok")\n'})
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        rows = self.linux_rows()
        self.assertEqual(sorted(r['name'] for r in rows), ['test_baseline_linux_preview.py', 'test_case_pool.py'],
                         out)
        self.assertEqual({(r['kind'], r['commit'], r['session'], r['cause'], r['clearedAt']) for r in rows},
                         {('proof', BASELINE_SHA, 'sess-linux', None, None)}, out)
        self.assertTrue(all(r['firstLine'] for r in rows), out)
        self.assertEqual((self.source()['status'], self.source()['pendingSha']), ('ok', None), out)

    def test_a_red_proof_the_tip_does_not_track_opens_no_linux_row(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log')
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n', '.claude__tests__test_case_pool.py': 'print("ok")\n'})
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        self.assertEqual([r['name'] for r in self.linux_rows()], ['test_case_pool.py'], out)

    def test_a_windows_pass_of_the_same_proof_leaves_the_linux_row_open(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log',
                            runs=[{'databaseId': 8, 'headSha': BASELINE_SHA, 'status': 'in_progress',
                                   'conclusion': None, 'event': 'push'}])
        self.land('the proof exists here', **{'.claude__tests__test_case_pool.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_case_pool.py', platform='linux', commit='a' * 40)])
        self.plan_close({'verdict': 'green', 'proofs': ['test_case_pool.py']})
        rc, out = self.whole_run('r1')
        rows = self.linux_rows()
        self.assertEqual([(r['name'], r['clearedAt']) for r in rows], [('test_case_pool.py', None)], out)

    def test_a_later_run_showing_one_proof_passing_clears_that_row_and_leaves_the_other(self):
        log = Path(self.aux.name) / 'later.log'
        passed = ('template\tUNKNOWN STEP\t2026-11-17T01:00:00.0000000Z OK 1.1s '
                  '/home/runner/work/GodotDev_HarnessBaseline/GodotDev_HarnessBaseline/template/.claude/tests/'
                  'test_baseline_linux_preview.py\n')
        log.write_text(passed + ''.join(
            l + '\n' for l in fixture_lines('baseline_linux_failed.log') if 'FAIL 6.8s' in l), encoding='utf-8')
        later = 'b' * 40
        self.plant_baseline(sha=later, log=log,
                            runs=[{'databaseId': 9, 'headSha': later, 'status': 'completed',
                                   'conclusion': 'failure', 'event': 'push'}])
        self.land('the baseline moved on', **{'.claude__notes.md': 'a\n'})
        self.seed_state(self.base, rows=[self.row('test_baseline_linux_preview.py', platform='linux', commit='a' * 40),
                                          self.row('test_case_pool.py', platform='linux', commit='a' * 40)])
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        rows = {r['name']: r for r in self.linux_rows()}
        self.assertEqual(rows['test_baseline_linux_preview.py']['clearedBy'], later, out)
        self.assertTrue(rows['test_baseline_linux_preview.py']['clearedAt'], out)
        self.assertIsNone(rows['test_case_pool.py']['clearedAt'], out)

    def test_absence_from_a_later_runs_log_never_clears_a_row(self):
        """A lane that never ran its proofs lists nothing in the later log: absence is not a pass, so the row stays."""
        log = Path(self.aux.name) / 'partial.log'
        log.write_text(''.join(
            l + '\n' for l in fixture_lines('baseline_linux_failed.log') if 'FAIL 6.8s' in l), encoding='utf-8')
        later = 'c' * 40
        self.plant_baseline(sha=later, log=log,
                            runs=[{'databaseId': 11, 'headSha': later, 'status': 'completed',
                                   'conclusion': 'failure', 'event': 'push'}])
        self.land('the baseline moved on', **{'.claude__notes.md': 'a\n'})
        self.seed_state(self.base, rows=[self.row('test_baseline_linux_preview.py', platform='linux', commit='a' * 40)])
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        rows = {r['name']: r for r in self.linux_rows()}
        self.assertIsNone(rows['test_baseline_linux_preview.py']['clearedAt'], out)

    def test_an_in_progress_run_sets_pending_and_writes_no_row(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log',
                            runs=[{'databaseId': 10, 'headSha': BASELINE_SHA, 'status': 'in_progress',
                                   'conclusion': None, 'event': 'push'}])
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        self.assertEqual(self.linux_rows(), [], out)
        self.assertEqual((self.source()['status'], self.source()['pendingSha']), ('pending', BASELINE_SHA), out)

    def test_an_unreadable_run_keeps_the_rows_and_sets_unreadable(self):
        self.plant_baseline(unreadable=True)
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n'})
        self.seed_state(self.base, rows=[self.row('test_case_pool.py', platform='linux', commit='a' * 40)])
        self.plan_close('green')
        rc, out = self.whole_run('r1')
        rows = self.linux_rows()
        self.assertEqual([(r['name'], r['clearedAt']) for r in rows], [('test_case_pool.py', None)], out)
        self.assertEqual(self.source()['status'], 'unreadable', out)
        self.assertTrue(self.source()['reason'], out)


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    from _case_pool import run_sharded
    sys.exit(run_sharded(__file__))
