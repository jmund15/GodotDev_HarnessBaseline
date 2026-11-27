"""Proof: the main check (`.claude/scripts/main_check.py`) proves a landed range on `main` and records its verdict as
rows on the `main-check-state` branch (rules/harness_tooling.md §Land and main check).

Two kinds of case, split at the boundary each one decides on:

* Planted facts (`StageCase`): a verdict, a row, the gate's exit mapping, the gate-owed table and the health line are
  decided from records and git facts the case plants, so a case runs the stage function in this process on a local
  repository with no remote and no CLI. The state the stages push is held in memory (`update_state` is the seam), and
  the child a stage starts (the project's pytest, the declared gate command) replays a planted plan through
  `main_check.run_logged`.
* Live (`MainCheckCase`): one case per real transport, on a repository whose `origin` is a bare `file://` remote under a
  temp root (nothing reaches the network or the real `origin`): the state fetch and the push with its lease, a lost
  lease, the second clone that reads what was pushed, the submodule checkout, a real pytest child and its junit file,
  the env a settings file gives a child, the CLI with its console encoding and a planted `pwsh` gate, and the whole
  run's chain from one range to the next. Two live cases need no repository and are plain TestCases: a real child
  ended at its cap (`RunLogged`) and a `gh` .cmd shim (`GhRead`).

The repository and its remote (with a seeded state branch) are built once and copied per case.

    python3 .claude/tests/test_main_check.py
"""
import contextlib
import copy
import io
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
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _harness_repo_case import RepoCase, ROOT  # noqa: E402
import _git_fixture  # noqa: E402
from _hook_call import copy_repo  # noqa: E402
from _inprocess_hook import run_hook  # noqa: E402

SCRIPT = ROOT / '.claude' / 'scripts' / 'main_check.py'
sys.path.insert(0, str(SCRIPT.parent))
import main_check  # noqa: E402

REAL_RUN_LOGGED = main_check.run_logged
FIXTURES = HERE / 'fixtures' / 'main_check'
PLATFORM = 'windows' if os.name == 'nt' else 'linux' if sys.platform.startswith('linux') else 'macos'
JOB_MINUTES = 777
EXIT_CODES = {'PASS': 0, 'FAIL': 1, 'INVALID': 2, 'WARN': 3, 'BLOCKED': 4, 'INCOMPLETE': 6, 'BUSY': 7,
              'CONTENTION': 8, 'HANG': 124}
GATE_ARGV = ['pwsh', '-NoProfile', '-File', '.claude/scripts/planted_gate.ps1', '-NoBaselineUpdate', '-RetryOnly']
GATE_OUTPUTS = ['out/baseline.json', 'out/durations.json']
ADAPTATION = '.claude/skills/project_subsystems/adaptation.json'
SUB_ENV = {'MAIN_CHECK_WORKERS': '0', 'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always'}

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

BASELINE_SLUG = 'jmund15/GodotDev_HarnessBaseline'
BASELINE_SHA = '2caaab4e5d6c80520127847e0aab8e8b6661a1ce'

GATE_STUB = r'''[CmdletBinding()]
param([string] $Repo = '', [switch] $NoBaselineUpdate, [switch] $RetryOnly, [switch] $NoReuse, [switch] $StaticOnly,
      [int] $WaitSec = 1800, [switch] $Detach)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
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


def fixture_lines(name):
    return [l for l in (FIXTURES / name).read_text(encoding='utf-8').splitlines() if not l.startswith('#')]


def iso(seconds_ago=0):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - seconds_ago))


def write_junit(record, path):
    """The junit file a pytest run of `record` leaves: a case per proof that ran, a failing one per red file, an xfail
    skip per quarantined entry that failed (`main_check.close_record` reads these)."""
    red = {r['proof']: r for r in record.get('red', [])}
    suite = ElementTree.Element('testsuite', name='pytest')

    def case(name):
        return ElementTree.SubElement(suite, 'testcase', classname='x', name='case', file='.claude/tests/' + name)

    for name in record.get('proofs', []):
        if name not in red:
            case(name)
    for name, row in red.items():
        ElementTree.SubElement(case(name), 'failure', message=row['detail'])
    for name, how in record.get('quarantine', {}).items():
        if how == 'failed':
            ElementTree.SubElement(case(name), 'skipped', type='pytest.xfail', message='q')
    root = ElementTree.Element('testsuites')
    root.append(suite)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(root).write(path, encoding='utf-8')


class MainCheckCase(RepoCase):
    """A live case: a repository, a bare `file://` remote with a seeded state branch, the stages' own git."""
    live = ()  # the children ('close': pytest, 'gate': the gate command) that run as real processes, not planted plans

    def seed_repo(self):
        super().seed_repo()
        self.write('.github/workflows/main_check.yml', WORKFLOW)
        self.write('.claude/scripts/planted_gate.ps1', GATE_STUB.replace('@NAME@', 'gate'))
        self.declare_gate()
        self.write('.claude/tests/test_main_check_planted.py', 'print("planted proof ran")\n')
        self.write('.gitignore', '.claude/scratch/\n/stamp.json\n/slots/\n/harness_*\n*.lock\n')
        self.commit('planted main check inputs')
        self.base = self.git('rev-parse', 'HEAD')
        # The remote and its state branch are built here once with the repository; each live case copies both.
        self.remote = self.repo.parent / 'origin.git'
        _git_fixture.init_repo(self.remote, bare=True)
        self.git('config', 'remote.origin.url', self.remote.as_uri())
        MainCheckCase.push(self)  # the build is shared by the planted subclass, whose own `push` and `seed_state` plant nothing remote
        MainCheckCase.seed_state(self, self.base)

    def setUp(self):
        super().setUp()
        self.aux = tempfile.TemporaryDirectory(prefix='main_check_aux_')
        self.addCleanup(self.aux.cleanup)
        self.remote = Path(self.aux.name) / 'origin.git'
        copy_repo(type(self)._template()[0].parent / 'origin.git', self.remote)
        self.git('config', 'remote.origin.url', self.remote.as_uri())
        patch.dict(os.environ, SUB_ENV).start()
        patch.object(main_check, 'run_logged', self.run_child).start()
        self.gate_runs, self.caps = [], []
        self.tips = [self.base]

    def declare_gate(self, declared=True, **main):
        """Plant the project's `gate_cadence.main`: no keyword is the stub's own declaration, a None value drops that field,
        `declared=False` declares no gate."""
        gate = dict({'command': GATE_ARGV, 'retry_exits': [7, 8, 124], 'outputs': GATE_OUTPUTS}, **main)
        gate = {k: v for k, v in gate.items() if v is not None}
        self.write(ADAPTATION, json.dumps({'gate_cadence': {'main': gate}} if declared else {}))

    def redeclare(self, **kwargs):
        """Land a changed gate declaration and start the state at it."""
        self.declare_gate(**kwargs)
        self.commit_as('declare the gate')
        self.push()
        self.seed_state(self.git('rev-parse', 'HEAD'))

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
        sha = self.commit_as(message, session, **files)
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
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

    def state_commit(self, doc):
        blob = self.plumb(['hash-object', '-w', '--stdin'], json.dumps(doc, indent=1) + '\n')
        tree = self.plumb(['mktree'], '100644 blob %s\tmain_check.json\n' % blob)
        return self.plumb(['commit-tree', tree, '-m', 'planted state'])

    def seed_state(self, checked_to, rows=(), run=None):
        """The state branch of the remote reset to a root commit holding the state document: one `fast-import`."""
        text = json.dumps(self.state_doc(checked_to, rows, run), indent=1) + '\n'
        _git_fixture.history(self.remote, ({'main_check.json': text}, 'planted state', 0),
                             ref='refs/heads/main-check-state', root=True, force=True)

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
        """Plant the close's outcome: `record` names the proofs that ran (`proofs`), the files with a failing case (`red`,
        each with its text) and the quarantined entries that failed; None plants a run with no junit file."""
        if record == 'green':
            record = {}
        self.write('.claude/scratch/close_plan.json', json.dumps(dict(plan, record=record)))

    def red_record(self, *reds, proofs=None):
        return {'red': list(reds), 'proofs': proofs or [r['proof'] for r in reds]}

    def red(self, proof, detail='FAIL: unit regressed\n'):
        return {'proof': proof, 'detail': detail}

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
        """The flags of each call the planted gate script got, as the stub's own calls file lists them."""
        return [','.join(run['flags']) for run in self.gate_runs if run['script'] == 'planted_gate']

    def run_child(self, cmd, *, cwd, env, log_path, echo, cap_seconds):
        """`main_check.run_logged` for a stage's child: a planted plan answers unless the case names the child `live`."""
        kind = 'close' if echo is main_check.CLOSE_ECHO else 'gate'
        self.caps.append((kind, cap_seconds))
        if kind in self.live:
            return REAL_RUN_LOGGED(cmd, cwd=cwd, env=env, log_path=log_path, echo=echo, cap_seconds=cap_seconds)
        scratch = Path(cwd) / '.claude' / 'scratch'
        if kind == 'close':
            plan = json.loads((scratch / 'close_plan.json').read_text(encoding='utf-8'))
            junit = next(arg.split('=', 1)[1] for arg in cmd if arg.startswith('--junitxml='))
            if plan.get('record') is not None:
                write_junit(plan['record'], junit)
            elif os.path.exists(junit):
                os.remove(junit)
            text, code = ''.join(line + '\n' for line in plan.get('lines', [])), plan.get('exit', 0)
        else:
            script = next(arg for arg in cmd if arg.endswith('.ps1'))
            name = Path(script).stem
            plan = json.loads((scratch / 'gate_plan.json').read_text(encoding='utf-8'))
            step = plan[min(sum(run['script'] == name for run in self.gate_runs), len(plan) - 1)]
            self.gate_runs.append({'script': name, 'cmd': list(cmd),
                                   'flags': sorted(arg[1:] for arg in cmd[cmd.index(script) + 1:] if arg.startswith('-'))})
            text, code = step['stdout'], None if step['exit'] is None else int(step['exit'])
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(text, encoding='utf-8')
        for line in text.splitlines():
            if echo.match(line):
                print('  ' + line, flush=True)
        return code, text

    def run_dir(self, run_id):
        return self.repo / '.claude' / 'scratch' / 'main_check' / run_id

    def record(self, run_id, name):
        path = self.run_dir(run_id) / (name + '.json')
        if not path.exists():
            self.fail('no %s record for run %s; stage output:\n%s' % (name, run_id, self.last_out))
        return json.loads(path.read_text(encoding='utf-8'))

    last_out = ''

    def mc(self, *args, env=None):
        """The CLI's exit code and output, from `main_check.main` in this process under `env`."""
        with patch.dict(os.environ, env or {}):
            code, out, err = run_hook(main_check, list(args), '')
        return code, out + err

    def spawn(self, *args, env=None):
        """The same call as its own process: the CLI path, the console encoding and the planted `pwsh` gate are real."""
        result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                                env=dict(os.environ, **(env or {})), timeout=600, cwd=str(self.repo), encoding='utf-8',
                                errors='replace')
        return result.returncode, result.stdout + result.stderr

    def stage(self, name, run_id='run1', *extra, env=None, run=None):
        return (run or self.mc)(name, '--repo', str(self.repo), '--run-id', run_id, *extra, env=env)

    def whole_run(self, run_id='run1', *extra, stages=('close', 'gate', 'finalize')):
        """The stages in order (a case deciding on the gate stage's own record stops before finalize)."""
        outs = [self.stage(name, run_id, *(extra if name == 'close' else ())) for name in stages]
        self.last_out = '\n'.join(o[1] for o in outs)
        return outs[-1][0], self.last_out

    def plant_start(self, run_id='r1', dispatched=False):
        """The `start` record the close stage leaves for a range from the state's `checkedTo` to HEAD."""
        began, tip = time.time(), self.git('rev-parse', 'HEAD')
        main_check.put_record(str(self.repo), run_id, 'start', {
            'tip': tip, 'rangeStart': self.remote_state()['checkedTo'], 'dispatched': dispatched, 'startedAt': began,
            'liveUntil': began + JOB_MINUTES * 60})
        return tip

    def plant_close(self, run_id='r1', record='green', code=0, lines=(), quarantine=None):
        """The `close` record of a pytest pass that ended `code` and printed `lines`, from the junit file `record`
        describes (None: the pass left no junit file) through the close stage's own `close_doc`."""
        repo, start = str(self.repo), main_check.record(str(self.repo), run_id, 'start')
        junit = self.run_dir(run_id) / 'close_parallel.xml'
        if record is not None:
            write_junit({} if record == 'green' else record, junit)
        began = time.time()
        doc = main_check.close_doc(code, ''.join(line + '\n' for line in lines), [str(junit)], start['tip'],
                                   start['rangeStart'], quarantine or {}, began, began + 1)
        main_check.put_record(repo, run_id, 'close', doc)


class StageCase(MainCheckCase):
    """A planted case: a local repository with no remote and no CLI. A stage runs as its function in this process; the
    state it would push is `self.state`, and the commits `land` makes move `origin/main` locally."""

    def setUp(self):
        RepoCase.setUp(self)
        self.aux = tempfile.TemporaryDirectory(prefix='main_check_aux_')
        self.addCleanup(self.aux.cleanup)
        patch.dict(os.environ, SUB_ENV).start()
        patch.object(main_check, 'run_logged', self.run_child).start()
        patch.object(main_check, 'update_state', self.update_state).start()
        self.gate_runs, self.caps = [], []
        self.tips = [self.base]
        self.state = self.state_doc(self.base)

    def update_state(self, repo, mutate, held=None):
        self.state = mutate(copy.deepcopy(self.state))
        return self.state

    def seed_state(self, checked_to, rows=(), run=None):
        self.state = self.state_doc(checked_to, rows, run)

    def remote_state(self):
        return self.state

    def push(self):
        self.git('update-ref', main_check.ORIGIN_MAIN, self.git('rev-parse', 'HEAD'))
        return self.git('rev-parse', 'HEAD')

    def land(self, message='landed', session=None, **files):
        sha = self.commit_as(message, session, **files)
        self.push()
        self.tips.append(sha)
        return sha

    def redeclare(self, **kwargs):
        self.declare_gate(**kwargs)
        self.commit_as('declare the gate')
        self.seed_state(self.git('rev-parse', 'HEAD'))

    def publish_state(self, doc=None):
        """The local ref a clone holds after fetching the state branch, from the planted state (`--rows` and `--status`
        read only this ref)."""
        self.git('update-ref', main_check.STATE_REF, self.state_commit(doc or self.state))

    def call(self, function, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = function(*args)
        return code, out.getvalue() + err.getvalue()

    def stage(self, name, run_id='r1', cap=main_check.DEFAULT_CAP_MINUTES):
        repo = str(self.repo)
        if name == 'gate':
            return self.call(main_check.run_gate, repo, run_id, cap)
        return self.call(main_check.run_finalize, repo, run_id)

    def run_planted(self, run_id='r1', record='green', stages=('gate', 'finalize'), dispatched=False, **close):
        """The range from the state's `checkedTo` to HEAD, closed by a planted pass (`plant_close`), then each stage."""
        self.plant_start(run_id, dispatched)
        self.plant_close(run_id, record, **close)
        outs = [self.stage(name, run_id) for name in stages]
        self.last_out = '\n'.join(o[1] for o in outs)
        return outs[-1][0], self.last_out

    def project_range(self, session='sess-p'):
        self.land('harness edit', session='sess-h', **{'.claude__notes.md': 'a%d\n' % len(self.tips)})
        return self.land('project edit', session=session, **{'project.godot': 'config%d\n' % len(self.tips)})

    def gate_rows(self):
        return [r for r in self.state['rows'] if r['kind'] == 'gate']


class Verdict(StageCase):
    """What finalize decides from a run's records: whether `checkedTo` moves, the outcome, and the rows it opens."""

    def test_checked_to_moves_on_green_and_on_red_with_nothing_unrun_and_the_wall_names_each_stage(self):
        sha = self.land('first', **{'.claude__notes.md': 'a\n'})
        rc, out = self.run_planted('r1')
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (sha, 'green'), out)
        self.assertEqual(sorted(self.state['run']['wall']), ['close', 'finalize', 'gate'])
        self.assertTrue(all(v is None or v >= 0 for v in self.state['run']['wall'].values()))
        sha = self.land('second', **{'.claude__notes.md': 'b\n'})
        rc, out = self.run_planted('r2', self.red_record(self.red('test_unit.py')))
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (sha, 'red'), out)

    def test_a_red_row_names_the_session_only_when_one_session_landed_the_range(self):
        # The close runs no bisect, so a red names a session only when every commit of its range carries that session.
        self.land('first', session='sess-a', **{'.claude__notes.md': 'a\n'})
        self.land('second', session='sess-a', **{'.claude__notes.md': 'b\n'})
        self.run_planted('r1', self.red_record(self.red('test_unit.py')))
        rows = [r for r in self.state['rows'] if r['name'] == 'test_unit.py']
        self.assertEqual([(r['commit'], r['session']) for r in rows], [(None, 'sess-a')])
        self.land('third', session='sess-b', **{'.claude__notes.md': 'c\n'})
        self.land('fourth', session='sess-c', **{'.claude__notes.md': 'd\n'})
        self.run_planted('r2', self.red_record(self.red('test_other.py')))
        rows = [r for r in self.state['rows'] if r['name'] == 'test_other.py']
        self.assertEqual([(r['commit'], r['session']) for r in rows], [(None, None)])

    def test_a_case_that_outran_its_bound_gets_the_timeout_cause_and_a_crashed_worker_the_crash_cause(self):
        self.land('first', **{'.claude__tests__test_slow_unit.py': 'print("ok")\n',
                              '.claude__tests__test_dead_unit.py': 'print("ok")\n',
                              '.claude__tests__test_unit.py': 'print("ok")\n'})
        rc, out = self.run_planted('r1', self.red_record(
            self.red('test_slow_unit.py', 'test_slow_unit.py: fail\nTIMEOUT after 120s\n'),
            self.red('test_dead_unit.py', "worker 'gw3' crashed while running '.claude/tests/test_dead_unit.py::proof'"),
            self.red('test_unit.py')))
        rows = {r['name']: r for r in self.open_rows()}
        self.assertEqual({n: (r['cause'], r['commit']) for n, r in rows.items()},
                         {'test_slow_unit.py': ('timeout', None), 'test_dead_unit.py': ('crash', None),
                          'test_unit.py': (None, None)}, out)
        self.assertEqual(self.state['checkedTo'], self.tips[-1], out)

    def test_a_close_with_no_record_is_no_verdict_and_leaves_checked_to(self):
        # Each way a close ends without a record: a pytest that crashed (its cause line), one the cap killed, and a
        # close that raised after its start write and left no close record at all.
        for label, close in (('crashed', dict(record=None, code=3, lines=['pytest: internal error: planted'])),
                             ('killed', dict(record=None, code=None)), ('raised', None), ('errored', 'error')):
            with self.variant(label):
                self.land('first %s' % label, **{'.claude__notes.md': label + '\n'})
                before = self.state['checkedTo']
                self.plant_start(label)
                if close == 'error':  # the record a close writes when its checkout or submodule update failed
                    main_check.put_record(str(self.repo), label, 'close', {
                        'error': 'submodule: git submodule update failed: planted', 'startedAt': 0, 'endedAt': 0, 'wall': 0})
                elif close:
                    self.plant_close(label, **close)
                rc, out = self.stage('finalize', label)
                self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (before, 'no-verdict'), out)
                self.assertIn({'crashed': 'internal error: planted', 'killed': 'killed by its cap',
                               'raised': 'no record of this run', 'errored': 'submodule: git submodule'}[label],
                              self.state['run']['detail'], out)
                self.assertIsNotNone(self.state['run']['end'], out)
                self.assertNotEqual(rc, 0, out)

    def test_finalize_takes_only_its_own_runs_close_and_gate_records(self):
        self.land('project edit', **{'project.godot': 'config\n'})
        self.plan_gate(self.gate_step('PASS', '20261118-000003-3'))
        self.plant_start('prior')
        self.plant_close('prior')
        self.stage('gate', 'prior')
        self.plant_start('new')
        self.plant_close('new')
        rc, out = self.stage('finalize', 'new')
        self.assertEqual(self.state['checkedTo'], self.base, 'a green gate record at the same tip from an earlier run never stands in: ' + out)
        self.assertEqual(self.state['run']['outcome'], 'no-verdict', out)
        self.assertIn('gate', self.state['run']['detail'], out)
        self.plant_start('again')
        self.plant_close('again')
        self.stage('gate', 'again')
        (self.run_dir('again') / 'close.json').unlink()
        rc, out = self.stage('finalize', 'again')
        self.assertEqual(self.state['checkedTo'], self.base, 'a green close record from an earlier run never stands in: ' + out)
        self.assertIn('close', self.state['run']['detail'], out)

    def test_a_gate_fail_writes_a_row_per_failed_test_with_the_ranges_one_non_harness_commit_and_never_reruns(self):
        culprit = self.project_range()
        ids = ['Planted.Tests.Integration.First.FirstTests.First_Fails',
               'Planted.Tests.Logic.Other.SecondTests.Second_Fails']
        self.plan_gate(self.gate_step('FAIL', '20261118-000010-10', failed=ids + [ids[0].rsplit('.', 1)[1]]),
                       self.gate_step('PASS', '20261118-000011-11'))
        rc, out = self.run_planted('r1')
        self.assertEqual(len(self.gate_calls()), 1, 'a FAIL is never rerun: ' + out)
        self.assertEqual(sorted(r['name'] for r in self.gate_rows()), sorted(ids), out)
        self.assertEqual({(r['commit'], r['session'], r['platform']) for r in self.gate_rows()}, {(culprit, 'sess-p', PLATFORM)})
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (culprit, 'red'), out)

    def test_two_failed_test_lines_one_a_bare_duplicate_write_one_row_for_the_full_id(self):
        culprit = self.project_range()
        full = 'Planted.Tests.Logic.Math.Add_Fails'
        self.plan_gate(self.gate_step('FAIL', failed=[full, 'Add_Fails']))
        self.run_planted('r1')
        self.assertEqual([(r['name'], r['commit']) for r in self.gate_rows()], [(full, culprit)])

    def test_a_later_full_pass_clears_every_gate_row_and_only_those(self):
        self.project_range()
        self.land('a proof the row names', **{'.claude__tests__test_x.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('A.B.One', kind='gate'), self.row('A.B.Two', kind='gate'),
                                          self.row('test_x.py', kind='proof')])
        self.plan_gate(self.gate_step('PASS', '20261118-000012-12'))
        rc, out = self.run_planted('r1', {'proofs': []})
        rows = {r['name']: r for r in self.state['rows']}
        for name in ('A.B.One', 'A.B.Two'):
            self.assertTrue(rows[name]['clearedAt'] and rows[name]['clearedBy'] == self.tips[-1], out)
        self.assertIsNone(rows['test_x.py']['clearedAt'], 'a full gate PASS clears gate rows only: ' + out)

    def test_each_no_verdict_gate_outcome_leaves_checked_to_writes_no_row_and_records_the_outcome(self):
        self.project_range()
        for verdict in ('WARN', 'INVALID', 'INCOMPLETE', 'BLOCKED', 'BUSY', 'CONTENTION', 'HANG', 'IMPORT_FAIL'):
            with self.variant(verdict):
                self.seed_state(self.base)
                self.gate_runs.clear()
                self.plan_gate(self.gate_step(verdict), self.gate_step(verdict))
                rc, out = self.run_planted('r-' + verdict)
                self.assertEqual(self.state['checkedTo'], self.base, out)
                self.assertEqual(self.state['rows'], [], out)
                self.assertEqual(self.state['run']['outcome'], 'no-verdict', out)
                self.assertIn('gate', self.state['run']['detail'], out)
                self.assertNotEqual(rc, 0, 'a run with no verdict ends its job red: ' + out)

    def test_an_exit_never_listed_for_retry_runs_once_and_is_no_verdict(self):
        self.redeclare(retry_exits=None)
        self.project_range()
        self.plan_gate(self.gate_step('CONTENTION'), self.gate_step('PASS'))
        rc, out = self.run_planted('r1')
        self.assertEqual(len(self.gate_calls()), 1, out)
        self.assertEqual((self.state['run']['outcome'], self.state['checkedTo']), ('no-verdict', self.git('rev-parse', 'HEAD~2')), out)

    def test_finalize_judges_the_gate_on_the_gate_stages_own_owed_verdict(self):
        # After the gate ran, the checkout's declaration lists the range's one project path as a gate output: a
        # finalize that re-derived `owed` would call the gate unowed and drop its FAIL.
        self.project_range()
        self.plan_gate(self.gate_step('FAIL', failed=['Planted.Tests.Owed_Fails']))
        self.plant_start('r1')
        self.plant_close('r1')
        self.stage('gate', 'r1')
        self.declare_gate(outputs=GATE_OUTPUTS + ['project.godot'])
        rc, out = self.stage('finalize', 'r1')
        self.assertEqual([r['name'] for r in self.gate_rows()], ['Planted.Tests.Owed_Fails'], out)
        self.assertEqual(self.state['run']['outcome'], 'red', out)

    def test_a_fail_exit_naming_no_test_is_no_verdict_and_checked_to_stays(self):
        self.project_range()
        self.plan_gate(self.gate_step('FAIL', failed=[]))
        rc, out = self.run_planted('r1')
        self.assertEqual((self.state['checkedTo'], self.state['rows'], self.state['run']['outcome']), (self.base, [], 'no-verdict'), out)

    def test_a_pass_exit_stands_with_no_ledger_file_in_the_tree(self):
        tip = self.project_range()
        self.plan_gate(self.gate_step('PASS'))
        rc, out = self.run_planted('r1')
        self.assertFalse((self.repo / '.claude' / 'scratch' / 'gate_ledger').exists())
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (tip, 'green'), out)

    def test_a_malformed_declaration_is_no_verdict_not_a_silent_skip(self):
        self.write(ADAPTATION, json.dumps({'gate_cadence': {'main': {'command': 'run it'}}}))
        self.commit_as('malformed declaration')
        start = self.git('rev-parse', 'HEAD')
        self.seed_state(start)
        self.project_range()
        rc, out = self.run_planted('r1')
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (start, 'no-verdict'), out)
        self.assertEqual(self.gate_calls(), [], out)

    def test_failed_test_ids_collapse_a_bare_name_into_the_full_id_that_ends_with_it(self):
        self.assertEqual(main_check.failed_test_ids(['A.B.Add_Fails', 'Add_Fails', 'C.D.Other', 'A.B.Add_Fails']),
                         ['A.B.Add_Fails', 'C.D.Other'])
        self.assertEqual(main_check.failed_test_ids(None), [])


class Rows(StageCase):
    def test_a_proof_row_clears_on_a_recorded_pass_and_keeps_clearedAt_and_clearedBy(self):
        self.land('first', **{'.claude__notes.md': 'a\n', '.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', commit='abc')])
        self.run_planted('r1', {'proofs': ['test_unit.py']})
        row = [r for r in self.state['rows'] if r['name'] == 'test_unit.py'][0]
        self.assertTrue(row['clearedAt'])
        self.assertEqual(row['clearedBy'], self.tips[-1])
        self.assertEqual(row['commit'], 'abc')

    def test_a_proof_that_ran_no_case_stays_open(self):
        self.land('first', **{'.claude__notes.md': 'a\n', '.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py')])
        self.run_planted('r1', {'proofs': []})
        self.assertEqual([r['name'] for r in self.open_rows()], ['test_unit.py'])

    def test_a_deleted_proofs_row_clears_with_the_deleting_tip(self):
        self.seed_state(self.base, rows=[self.row('test_gone.py')])
        sha = self.land('nothing of that proof exists', **{'.claude__notes.md': 'a\n'})
        self.run_planted('r1')
        row = self.state['rows'][0]
        self.assertEqual((row['name'], row['clearedBy']), ('test_gone.py', sha))
        self.assertTrue(row['clearedAt'])

    def test_a_recorded_pass_clears_a_timeout_row_and_a_red_keeps_its_row(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n',
                              '.claude__tests__test_still.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', cause='timeout', first_line='TIMEOUT after 900s'),
                                          self.row('test_still.py')])
        rc, out = self.run_planted('r1', {'proofs': ['test_unit.py', 'test_still.py'],
                                          'red': [self.red('test_still.py', 'FAIL: planted\n')]})
        rows = {r['name']: r for r in self.state['rows']}
        self.assertTrue(rows['test_unit.py']['clearedAt'], 'a recorded pass clears a timeout row: ' + out)
        self.assertIsNone(rows['test_still.py']['clearedAt'], out)

    def test_a_later_red_on_a_proof_with_an_open_row_keeps_a_row_open_with_its_new_line(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', cause='timeout', first_line='TIMEOUT after 900s')])
        rc, out = self.run_planted('r1', {'proofs': ['test_unit.py'],
                                          'red': [self.red('test_unit.py', 'AssertionError: a new break\n')]})
        lines = sorted(r['firstLine'] for r in self.open_rows() if r['name'] == 'test_unit.py')
        self.assertEqual(lines, ['AssertionError: a new break', 'TIMEOUT after 900s'], out)

    def test_a_run_clears_only_its_own_platforms_rows(self):
        self.land('first', **{'.claude__tests__test_unit.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_unit.py', platform='linux' if PLATFORM != 'linux' else 'windows')])
        self.run_planted('r1', {'proofs': ['test_unit.py']})
        self.assertTrue(all(r['clearedAt'] is None for r in self.state['rows']))

    def test_a_cleared_row_stays_thirty_days_and_then_goes(self):
        recent = self.row('test_recent.py', cleared_at=iso(10 * 86400), cleared_by='x')
        stale = self.row('test_stale.py', cleared_at=iso(31 * 86400), cleared_by='x')
        self.seed_state(self.base, rows=[recent, stale])
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.run_planted('r1')
        self.assertEqual([r['name'] for r in self.state['rows']], ['test_recent.py'])

    def test_rows_groups_a_timeout_row_apart_from_an_unclassified_row(self):
        self.seed_state(self.base, rows=[self.row('test_a.py', cause='timeout', first_line='TIMEOUT after 900s'),
                                          self.row('test_b.py')])
        self.publish_state()
        rc, out = self.call(main_check.print_rows, str(self.repo), None)
        self.assertEqual(rc, 0, out)
        self.assertLess(out.index('cause=timeout'), out.index('test_a.py'), out)
        self.assertLess(out.index('test_a.py'), out.index('cause=unclassified'), out)
        self.assertLess(out.index('cause=unclassified'), out.index('test_b.py'), out)

    def test_rows_prints_open_rows_and_session_filters_them(self):
        self.seed_state(self.base, rows=[self.row('test_a.py', session='s1'), self.row('test_b.py', session='s2'),
                                          self.row('test_c.py', session='s1', cleared_at=iso(60), cleared_by='y')])
        self.publish_state()
        rc, out = self.call(main_check.print_rows, str(self.repo), None)
        self.assertEqual(rc, 0, out)
        self.assertEqual([n for n in ('test_a.py', 'test_b.py', 'test_c.py') if n in out], ['test_a.py', 'test_b.py'])
        rc, out = self.call(main_check.print_rows, str(self.repo), 's1')
        self.assertEqual([n for n in ('test_a.py', 'test_b.py', 'test_c.py') if n in out], ['test_a.py'])

    def test_rows_with_no_state_ref_fails_without_printing_a_row(self):
        rc, out = self.call(main_check.print_rows, str(self.repo), None)
        self.assertNotEqual(rc, 0, out)
        self.assertIn('main-check-state', out)
        self.assertNotIn('test_', out)


class GateRuns(StageCase):
    """The gate stage: whether the range owes it, what it runs, how its exit maps to attempts and a record."""

    def test_a_range_changing_only_the_gates_declared_outputs_owes_no_gate(self):
        self.land('gate outputs', **{'out__baseline.json': '{}\n', 'out__durations.json': '{}\n'})
        self.plan_gate(self.gate_step('PASS', '20261118-000001-1'))
        rc, out = self.run_planted('r1')
        self.assertEqual(self.gate_calls(), [], out)
        self.assertEqual(self.state['run']['outcome'], 'green', out)

    def test_a_dispatched_run_owes_the_gate_on_an_unchanged_tree_and_runs_it_with_the_declared_flags(self):
        self.plan_gate(self.gate_step('PASS', '20261118-000002-2', tip=self.base))
        rc, out = self.run_planted('r1', dispatched=True)
        self.assertEqual(self.gate_calls(), ['NoBaselineUpdate,RetryOnly'], out)
        self.assertEqual(self.state['run']['outcome'], 'green', out)

    def test_contention_hang_and_busy_take_the_gates_next_step_before_they_are_recorded(self):
        self.project_range()
        self.plant_start('r1')
        self.plant_close('r1')
        for verdict in ('CONTENTION', 'HANG', 'BUSY'):
            with self.variant(verdict):
                self.gate_runs.clear()
                self.plan_gate(self.gate_step(verdict), self.gate_step('PASS'))
                rc, out = self.stage('gate', 'r1')
                self.last_out = out
                self.assertEqual(len(self.gate_calls()), 2, out)
                self.assertEqual(self.record('r1', 'gate')['exit'], 0, 'the rerun\'s exit is the recorded verdict: ' + out)

    def test_a_declared_command_other_than_the_default_script_runs(self):
        self.redeclare(command=['pwsh', '-NoProfile', '-File', '.claude/scripts/other_gate.ps1'])
        self.project_range()
        self.plan_gate(self.gate_step('PASS'))
        rc, out = self.run_planted('r1', stages=('gate',))
        self.assertEqual([run['script'] for run in self.gate_runs], ['other_gate'], out)
        self.assertEqual(self.record('r1', 'gate')['exit'], 0, out)

    def test_an_exit_the_declaration_lists_as_retry_reruns_once_with_no_verdict_line_needed(self):
        self.redeclare(retry_exits=[9])
        self.project_range()
        self.plan_gate({'stdout': 'tool fault\n', 'exit': 9}, self.gate_step('PASS'))
        rc, out = self.run_planted('r1', stages=('gate',))
        self.assertEqual(len(self.gate_calls()), 2, out)
        self.assertEqual(self.record('r1', 'gate')['exit'], 0, 'the rerun\'s exit is the recorded verdict: ' + out)

    def test_a_project_declaring_no_gate_owes_none_even_on_a_dispatched_run(self):
        self.redeclare(declared=False)
        tip = self.project_range()
        rc, out = self.run_planted('r1', dispatched=True)
        self.assertEqual(self.gate_calls(), [], out)
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome'], rc), (tip, 'green', 0), out)

    def test_a_gate_the_cap_ends_is_recorded_once_and_is_no_verdict(self):
        # A child the cap ended answers no exit code, and nothing reruns it even though its exit would be a retry.
        start = self.git('rev-parse', 'HEAD')
        self.project_range()
        self.plan_gate({'stdout': '', 'exit': None}, self.gate_step('PASS'))
        self.plant_start('r1')
        self.plant_close('r1')
        began = time.time()
        self.last_out = self.mc('gate', '--repo', str(self.repo), '--run-id', 'r1', '--cap-minutes', '10')[1]
        wall, kill = time.time() - began, 600 * main_check.KILL_SHARE
        self.assertEqual(len(self.gate_calls()), 1, self.last_out)
        (_, cap), = self.caps
        self.assertTrue(kill - wall - 1 <= cap <= kill, 'the gate hands its child its share of --cap-minutes: %r' % cap)
        self.assertTrue(self.record('r1', 'gate')['timedOut'], self.last_out)
        rc, out = self.stage('finalize', 'r1')
        self.assertEqual((self.state['checkedTo'], self.state['run']['outcome']), (start, 'no-verdict'), out)
        self.assertIn('killed by its cap', self.state['run']['detail'], out)


class GateOwed(unittest.TestCase):
    """The gate-owed predicate (moved from test_land_run.py, land_run.ps1's `LAND stage=gate` branch): its table, on
    planted changed paths."""

    OUTPUTS = ('out/baseline.json', 'out/durations.json')

    def test_a_path_owes_the_gate_unless_it_is_harness_an_ignore_file_a_claude_only_attribute_or_a_declared_output(self):
        claude_attr = ['.claude/baseline.lock.json merge=baseline-lock']
        rows = [
            ('a change outside the harness', ['project.godot'], [], True),
            ('.claude', ['.claude/notes.md'], [], False),
            ('.github', ['.github/workflows/other.yml'], [], False),
            ('markdown', ['docs/readme.md'], [], False),
            ('markdown in capitals', ['NOTES.MD'], [], False),
            ('.gitignore', ['.gitignore'], [], False),
            ('.searchignore', ['.searchignore'], [], False),
            ('.gitattributes naming only .claude paths', ['.gitattributes'], claude_attr, False),
            ('.gitattributes naming a path outside .claude', ['.gitattributes'], claude_attr + ['*.cs eol=crlf'], True),
            ('the declared outputs only', ['out/baseline.json', 'out/durations.json'], [], False),
            ('a declared output and any other path', ['out/baseline.json', 'out/Other.cs'], [], True),
            ('an empty range', [], [], False),
        ]
        for label, changed, attrs, owed in rows:
            with self.subTest(label):
                self.assertEqual(main_check.paths_owe_gate(changed, attrs, self.OUTPUTS), owed)

    def test_a_dispatched_run_owes_the_gate_on_any_tree(self):
        self.assertEqual(main_check.gate_owed('no-repo-is-read', 'a', 'a', dispatched=True)[0], True)


class DiffReads(StageCase):
    """The git read under the gate-owed table: the names a range changes and the lines a `.gitattributes` edit changes,
    from a real diff."""

    def owed(self, dispatched=False, **files):
        before = self.git('rev-parse', 'HEAD')
        if files:
            self.commit_as('change', **files)
        return main_check.gate_owed(str(self.repo), before, self.git('rev-parse', 'HEAD'), dispatched)[0]

    def test_a_real_range_owes_the_gate_by_its_changed_paths_and_attribute_lines(self):
        self.redeclare(outputs=['out/gate_report.json'])
        self.assertTrue(self.owed(**{'project.godot': 'config\n'}))
        self.assertFalse(self.owed(**{'.claude__notes.md': 'x\n'}))
        self.assertFalse(self.owed(**{'out__gate_report.json': '{}\n'}))
        self.assertTrue(self.owed(**{'out__other_report.json': '{}\n'}))
        self.commit_as('attrs', **{'.gitattributes': '* text=auto eol=lf\n'})
        self.assertFalse(self.owed(**{'.gitattributes': '* text=auto eol=lf\n.claude/baseline.lock.json merge=baseline-lock\n# harness merge drivers\n'}))
        self.assertTrue(self.owed(**{'.gitattributes': '* text=auto eol=lf\n.claude/baseline.lock.json merge=baseline-lock\n# harness merge drivers\n*.cs eol=crlf\n'}))
        self.assertFalse(self.owed(), 'an empty range owes nothing')
        self.assertTrue(self.owed(dispatched=True), 'a dispatched run owes the gate on an empty range')


class Status(StageCase):
    """`status()` and `--status`: the one health line `/session_end` prints, read from the local state ref."""

    def health(self, now=None):
        self.publish_state()
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
        self.assertEqual(main_check.status(str(self.repo)), 'main: never checked')

    def test_a_malformed_state_file_gives_the_unreadable_form(self):
        blob = self.plumb(['hash-object', '-w', '--stdin'], 'not json\n')
        tree = self.plumb(['mktree'], '100644 blob %s\tmain_check.json\n' % blob)
        self.git('update-ref', main_check.STATE_REF, self.plumb(['commit-tree', tree, '-m', 'malformed']))
        line = main_check.status(str(self.repo))
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
        self.publish_state(doc)
        self.assertEqual(main_check.status(str(self.repo)), 'main: linux lanes unreadable (planted)')

    def test_status_flag_prints_the_line_and_exits_zero(self):
        self.land('later', **{'.claude__later.md': 'x\n'})
        self.seed_state(self.base, run=self.run_rec(60, outcome='no-verdict'))
        self.publish_state()
        rc, out = self.mc('--status', '--repo', str(self.repo))
        self.assertEqual(rc, 0, out)
        self.assertIn('last run no-verdict', out)


@unittest.skipIf(PLATFORM == 'linux', 'the baseline Linux lane stands in for Linux only where the main check runs off Linux')
class BaselineLinuxSource(StageCase):
    """The finalize stage reads the baseline repo's Linux lanes for the lock's `synced_commit` and records their
    reds as `platform: linux` rows (rules/harness_tooling.md §Land and main check). Each case plants a fake `gh`; no case
    reaches the network."""

    def install_fake_gh(self, plan):
        """Every `gh` answer comes from one planted plan, so no case reaches the network; `unreadable` fails every call.
        `GhRead` drives the real `gh_read` (a `.cmd` shim, a missing gh) that this stands in for."""
        self.gh_plan = plan
        patch.object(main_check, 'gh_read', self.fake_gh).start()

    def fake_gh(self, args, timeout=None):
        plan = self.gh_plan
        if plan.get('unreadable'):
            return 1, '', 'gh: HTTP 500'
        if args[:2] == ['run', 'list']:
            return 0, json.dumps(plan.get('runs', [])), ''
        if args[:2] == ['run', 'view']:
            return 0, Path(plan['log']).read_text(encoding='utf-8', errors='replace'), ''
        if args[:1] == ['api']:
            return 0, plan.get('message', '') + '\n', ''
        return 1, '', 'fake gh: unsupported ' + ' '.join(args)

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
        return [r for r in self.state['rows'] if r['platform'] == 'linux']

    def source(self):
        return self.state['sources']['baselineLinux']

    def test_two_proof_level_fail_lines_become_two_linux_rows(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log')
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n', '.claude__tests__test_case_pool.py': 'print("ok")\n',
                                         '.claude__tests__test_baseline_linux_preview.py': 'print("ok")\n'})
        rc, out = self.run_planted('r1')
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
        rc, out = self.run_planted('r1')
        self.assertEqual([r['name'] for r in self.linux_rows()], ['test_case_pool.py'], out)

    def test_a_windows_pass_of_the_same_proof_leaves_the_linux_row_open(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log',
                            runs=[{'databaseId': 8, 'headSha': BASELINE_SHA, 'status': 'in_progress',
                                   'conclusion': None, 'event': 'push'}])
        self.land('the proof exists here', **{'.claude__tests__test_case_pool.py': 'print("ok")\n'})
        self.seed_state(self.base, rows=[self.row('test_case_pool.py', platform='linux', commit='a' * 40)])
        rc, out = self.run_planted('r1', {'proofs': ['test_case_pool.py']})
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
        rc, out = self.run_planted('r1')
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
        rc, out = self.run_planted('r1')
        rows = {r['name']: r for r in self.linux_rows()}
        self.assertIsNone(rows['test_baseline_linux_preview.py']['clearedAt'], out)

    def test_an_in_progress_run_sets_pending_and_writes_no_row(self):
        self.plant_baseline(log=FIXTURES / 'baseline_linux_failed.log',
                            runs=[{'databaseId': 10, 'headSha': BASELINE_SHA, 'status': 'in_progress',
                                   'conclusion': None, 'event': 'push'}])
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n'})
        rc, out = self.run_planted('r1')
        self.assertEqual(self.linux_rows(), [], out)
        self.assertEqual((self.source()['status'], self.source()['pendingSha']), ('pending', BASELINE_SHA), out)

    def test_an_unreadable_run_keeps_the_rows_and_sets_unreadable(self):
        self.plant_baseline(unreadable=True)
        self.land('pin the baseline', **{'.claude__notes.md': 'a\n'})
        self.seed_state(self.base, rows=[self.row('test_case_pool.py', platform='linux', commit='a' * 40)])
        rc, out = self.run_planted('r1')
        rows = self.linux_rows()
        self.assertEqual([(r['name'], r['clearedAt']) for r in rows], [('test_case_pool.py', None)], out)
        self.assertEqual(self.source()['status'], 'unreadable', out)
        self.assertTrue(self.source()['reason'], out)


class PassCommand(unittest.TestCase):
    """What the close runs and what it prints about it: the pass commands, the record a pass leaves, and the line that
    shows each pass's own verdict."""

    def passes(self, serial=None, workers='auto'):
        return main_check.pytest_passes('folder', serial or {}, workers)

    def test_the_parallel_pass_runs_the_tests_folder_at_the_asked_width(self):
        (command, junit), = self.passes(workers='7')
        self.assertEqual(command[command.index('-n') + 1], '7')
        self.assertEqual(command[-1], '.claude/tests')
        self.assertIn('--junitxml=' + junit, command)

    def test_a_serial_pass_runs_the_conftest_selection_alone_and_no_serial_proof_means_no_pass(self):
        self.assertEqual(len(self.passes()), 1)
        (_, _), (command, junit) = self.passes({'test_a.py': 'alone'})
        self.assertEqual(command[-1], '.claude/tests')
        self.assertIn('--harness-serial', command)
        self.assertIn('no:xdist', command)
        self.assertNotIn('-n', command)
        self.assertIn('--junitxml=' + junit, command)

    def test_a_quiet_pass_ends_on_a_bare_summary_and_the_close_prints_it_with_the_exit(self):
        tail = '\n'.join(fixture_lines('close_quiet_tail.log')) + '\n'
        line = main_check.pass_line('parallel', 1, tail)
        self.assertIn('29 failed, 2673 passed, 59 skipped in 1338.64s (0:22:18)', line)
        self.assertIn('exit 1', line)
        self.assertIn('killed', main_check.pass_line('parallel', None, '..\n'))
        self.assertIn('no output', main_check.pass_line('serial', 3, ''))

    def test_the_echo_streams_a_failure_line_and_not_a_progress_line(self):
        shown = [l for l in fixture_lines('close_quiet_tail.log') if main_check.CLOSE_ECHO.match(l)]
        self.assertEqual(shown, [l for l in fixture_lines('close_quiet_tail.log') if l.startswith('FAILED ')])
        self.assertIsNone(main_check.CLOSE_ECHO.match('....s......F..                 [ 12%]'))


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
        steps = [int(m) for m in re.findall(r'^        timeout-minutes: (\d+)$', self.text, re.M)]
        self.assertEqual(len(steps), 3)
        self.assertGreaterEqual(main_check.parse_job_minutes(self.text), sum(steps))


class GhRead(unittest.TestCase):
    """`gh_read` spawns a `gh` that is a .cmd shim on Windows: CreateProcess applies no PATHEXT, and cmd.exe re-parses
    the arguments it is handed, so the call goes through `_msys.resolve_argv` and never through a shell string."""

    def setUp(self):
        self.main_check = main_check
        self.tmp = tempfile.TemporaryDirectory(prefix='gh_read_')
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.log = Path(self.tmp.name) / 'ran.txt'
        (self.bin / 'gh.cmd').write_bytes(('@echo off\r\necho gh-ran %%1>>"%s"\r\n' % self.log).encode())
        env = patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', '')})
        env.start()
        self.addCleanup(env.stop)

    @unittest.skipUnless(os.name == 'nt', 'a .cmd shim is a Windows shape')
    def test_a_gh_cmd_shim_runs_with_its_arguments(self):
        code, _out, err = self.main_check.gh_read(['run', 'list'])
        self.assertEqual(code, 0, err)
        self.assertIn('gh-ran run', self.log.read_text())

    @unittest.skipUnless(os.name == 'nt', 'cmd.exe re-parses a .cmd shim\'s arguments')
    def test_an_argument_cmd_exe_would_re_parse_is_refused_not_run(self):
        code, out, err = self.main_check.gh_read(['x"&echo INJECTED'])
        self.assertIsNone(code)
        self.assertNotIn('INJECTED', out)
        self.assertFalse(self.log.exists(), 'the shim ran')
        self.assertIn('cmd.exe', err)

    def test_a_missing_gh_reads_as_unreadable(self):
        with patch.dict(os.environ, {'PATH': str(self.tmp.name)}):
            code, _out, err = self.main_check.gh_read(['run', 'list'])
        self.assertIsNone(code)
        self.assertIn('gh', err)


class SweepRunFolders(unittest.TestCase):
    def test_run_folders_older_than_two_weeks_go_and_the_kept_one_stays(self):
        with tempfile.TemporaryDirectory(prefix='main_check_sweep_') as tmp:
            runs = Path(main_check.runs_root(tmp))
            for name, age in (('ancient', 15), ('recent', 3), ('current', 40)):
                (runs / name).mkdir(parents=True)
                (runs / name / 'start.json').write_text('{}')
                past = time.time() - age * 86400
                os.utime(runs / name / 'start.json', (past, past))
                os.utime(runs / name, (past, past))
            main_check.sweep_run_dirs(tmp, 'current')
            self.assertEqual(sorted(p.name for p in runs.iterdir()), ['current', 'recent'])


class RunLogged(unittest.TestCase):
    def test_the_cap_ends_a_real_child_with_no_exit_code_and_a_matching_line_streams_as_it_appears(self):
        child = [sys.executable, '-c', 'import time; print("FAILED planted::proof - fail", flush=True); '
                 'print("progress", flush=True); time.sleep(60)']
        shown = io.StringIO()
        with tempfile.TemporaryDirectory(prefix='run_logged_') as tmp, contextlib.redirect_stdout(shown):
            began = time.monotonic()
            code, out = REAL_RUN_LOGGED(child, cwd=tmp, env=dict(os.environ), log_path=os.path.join(tmp, 'log', 'x.log'),
                                        echo=main_check.CLOSE_ECHO, cap_seconds=3)
        self.assertIsNone(code)
        self.assertLess(time.monotonic() - began, 30, 'the cap ended the sleeping child')
        self.assertIn('FAILED planted::proof - fail', out)
        self.assertEqual(shown.getvalue().splitlines(), ['  FAILED planted::proof - fail'])


class CloseStage(MainCheckCase):
    """The close stage over a real remote: the state fetch, the checkout and the child it starts."""

    def test_a_real_pytest_run_becomes_red_rows_and_quarantine_lines_in_the_close_record(self):
        # The live child boundary once: no close plan, so a real pytest runs the planted tests and the junit file it wrote is read.
        self.live = ('close',)
        self.write(ADAPTATION, json.dumps({'proof_quarantine': {'test_wobbly.py': 'races its own fixture'}}))
        self.land('plant tests', **{
            '.claude__tests__test_good.py': 'def test_good():\n    pass\n',
            '.claude__tests__test_bad.py': 'def test_bad():\n    assert False, "FAIL: unit regressed"\n',
            '.claude__tests__test_broken.py': 'import not_a_real_module_for_this_proof\n',
            '.claude__tests__test_wobbly.py': 'import pytest\n\n\n@pytest.mark.xfail(strict=False, reason="quarantined")\n'
                                              'def test_wobbly():\n    assert False\n'})
        rc, out = self.stage('close', 'r1')
        self.assertIn('QUARANTINE test_wobbly.py: failed (cause: races its own fixture)', out)
        close = self.record('r1', 'close')['record']
        self.assertEqual([r['proof'] for r in close['red']], ['test_bad.py', 'test_broken.py'], out)
        self.assertIn('test_good.py', close['proofs'])
        self.assertNotIn('test_wobbly.py', [r['proof'] for r in close['red']])
        self.assertIn('parallel pass exit 1:', out, 'the close prints the pass\'s own verdict line')

    def test_each_stage_sees_the_settings_env_a_session_gets_and_the_pass_reads_its_width_serial_proofs_and_cap(self):
        self.write('.claude/settings.json', json.dumps({'env': {'MC_PROBE': 'project', 'MC_BOTH': 'project',
                                                                'MAIN_CHECK_WORKERS': '5'}}))
        self.write(ADAPTATION, json.dumps({'proof_serial': {'test_main_check_planted.py': 'alone',
                                                            'test_gone.py': 'a file no longer there'}}))
        self.commit_as('settings')
        self.push()
        home = Path(self.aux.name) / 'home'
        (home / '.claude').mkdir(parents=True)
        (home / '.claude' / 'settings.json').write_text(json.dumps({'env': {'MC_BOTH': 'user'}}))
        self.live = ('close',)  # a real child shows the env it was handed
        seen = Path(self.aux.name) / 'child_env.json'
        probe = [sys.executable, '-c', 'import json, os, sys; json.dump({k: os.environ.get(k) for k in ("MC_PROBE", "MC_BOTH")}, '
                 'open(sys.argv[1], "w"))', str(seen)]
        asked = []

        def passes(folder, serial, workers):
            asked.append((serial, workers))
            return [(probe, os.path.join(folder, 'close_probe.xml'))]

        env = {'USERPROFILE': str(home), 'HOME': str(home)}
        began = time.time()
        with patch.object(main_check, 'pytest_passes', passes):
            self.mc('close', '--repo', str(self.repo), '--run-id', 'r1', '--cap-minutes', '10', env=env)
        wall, deadline = time.time() - began, 600 * main_check.DEADLINE_SHARE
        (_, cap), = self.caps
        self.assertTrue(deadline - wall - 1 <= cap <= deadline, 'the close hands its child the rest of its deadline: %r' % cap)
        self.assertEqual(json.loads(seen.read_text()), {'MC_PROBE': 'project', 'MC_BOTH': 'project'})
        self.assertEqual(asked, [({'test_main_check_planted.py': 'alone', 'test_gone.py': 'a file no longer there'}, '5')])

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
        rc, out = self.stage('close', 'r2')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('submodule', self.record('r2', 'close')['error'], out)

    def test_a_missing_state_branch_is_a_stage_failure_that_writes_nothing(self):
        self.git('push', '-q', 'origin', ':refs/heads/main-check-state')
        rc, out = self.stage('close', 'r1')
        self.assertNotEqual(rc, 0, out)
        self.assertIn('main-check-state', out)
        self.assertIsNone(self.remote_ref('main-check-state'))


class StateBranch(MainCheckCase):
    """The state write over a real remote: the push and its lease, the commit it makes, and the clone that reads it."""

    def move_state_after_the_next_fetch(self):
        """A peer writes the remote state once, right after the next fetch reads it: the stage's push then loses its
        lease. Returns the mark file the wrapper leaves when it moved the state."""
        peer_row = self.row('Peer.Tests.Pending', kind='gate', session='peer')
        peer = self.state_commit(self.state_doc(self.base, rows=[peer_row]))
        self.git('push', '-q', 'origin', peer + ':refs/heads/peer-state')
        wrapper = Path(self.aux.name) / 'move_state.sh'
        mark = Path(self.aux.name) / 'moved'
        wrapper.write_text('git-upload-pack "$@"\nrc=$?\nif [ ! -f "%s" ]; then touch "%s"; git --git-dir "%s" update-ref '
                           'refs/heads/main-check-state refs/heads/peer-state; fi\nexit $rc\n'
                           % (mark.as_posix(), mark.as_posix(), self.remote.as_posix()), newline='\n')
        self.git('config', 'remote.origin.uploadpack', 'sh ' + wrapper.as_posix())
        return mark

    def test_two_whole_runs_chain_over_a_real_remote_and_the_state_commit_holds_only_the_state(self):
        old = self.run_dir('ancient')
        old.mkdir(parents=True)
        (old / 'start.json').write_text('{}')
        past = time.time() - 15 * 86400
        os.utime(old / 'start.json', (past, past))
        os.utime(old, (past, past))
        before = self.remote_ref('main-check-state')
        first = self.land('first', session='sess-1', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        self.plan_gate(self.gate_step('PASS'))
        rc, out = self.whole_run('r1')
        state = self.remote_state()
        self.assertEqual((state['checkedTo'], state['run']['outcome']), (first, 'green'), out)
        started = time.mktime(time.strptime(state['run']['start'], '%Y-%m-%dT%H:%M:%SZ'))
        live = time.mktime(time.strptime(state['run']['liveUntil'], '%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(live - started, JOB_MINUTES * 60, 'the start carries the workflow job\'s cap: ' + out)
        self.assertFalse(old.exists(), 'a close sweeps the run folders older than two weeks')
        self.assertTrue(self.run_dir('r1').exists())
        # A state commit sits on the state it read, holds only the state file and leaves no ref behind.
        after = self.remote_ref('main-check-state')
        remote = ['git', '--git-dir', str(self.remote)]
        chain = subprocess.run(remote + ['rev-list', '--first-parent', after], capture_output=True, text=True,
                               check=True).stdout.split()
        self.assertEqual(chain[-1], before, 'the writes chain onto the seeded state: ' + out)
        tree = subprocess.run(remote + ['ls-tree', '--name-only', after], capture_output=True, text=True,
                              check=True).stdout.split()
        self.assertEqual(tree, ['main_check.json'], out)
        refs = self.plumb(['for-each-ref', '--format=%(refname)']).split()
        self.assertEqual([r for r in refs if not r.startswith(('refs/heads/', 'refs/remotes/'))], [], out)
        self.land('second', **{'.claude__notes.md': 'b\n'})
        self.stage('close', 'r2')
        self.assertEqual([self.record(r, 'start')['rangeStart'] for r in ('r1', 'r2')], [self.base, first])

    def test_a_red_pushed_by_finalize_is_read_by_a_second_clone_and_its_rows_command(self):
        self.land('first', session='sess-1', **{'.claude__notes.md': 'a\n'})
        self.plant_start('r1')
        self.plant_close('r1', self.red_record(self.red('test_unit.py')))
        self.stage('finalize', 'r1')
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
        self.plant_start('r1')
        self.plant_close('r1', self.red_record(self.red('test_unit.py')))
        mark = self.move_state_after_the_next_fetch()
        rc, out = self.stage('finalize', 'r1')
        self.assertTrue(mark.exists(), 'the wrapper moved the remote state between fetch and push: ' + out)
        state = self.remote_state()
        names = sorted(r['name'] for r in state['rows'] if r['clearedAt'] is None)
        self.assertEqual(names, ['Peer.Tests.Pending', 'test_unit.py'], out)
        self.assertEqual(state['run']['outcome'], 'red', out)

    def test_a_close_whose_state_moved_after_its_read_writes_its_start_on_the_moved_state(self):
        # The close writes its start from the state its first fetch read; a peer write after that read costs the lease,
        # and the reread state carries the start.
        self.land('first', **{'.claude__notes.md': 'a\n'})
        self.plan_close('green')
        mark = self.move_state_after_the_next_fetch()
        rc, out = self.stage('close', 'r1')
        self.assertTrue(mark.exists(), out)
        state = self.remote_state()
        self.assertEqual([r['name'] for r in state['rows']], ['Peer.Tests.Pending'], out)
        self.assertEqual((state['run']['id'], state['run']['stage']), ('r1', 'close'), out)
        self.assertIsNone(state['run']['end'], 'a run whose finalize never ran leaves its end null: ' + out)
        self.assertEqual(self.record('r1', 'start')['rangeStart'], self.base, out)


class CliGate(MainCheckCase):
    def test_a_failed_test_name_the_console_cannot_encode_still_records_its_row(self):
        """The Windows runner's console is cp1252: a test title holding a character it lacks must stream and record, not crash."""
        culprit = self.land('project edit', session='sess-p', **{'project.godot': 'config\n'})
        name = '[desktop] › labels.spec.ts:137:1 › opens the ▾ menu'
        self.plan_gate(self.gate_step('FAIL', '20261121-000010-10', failed=[name]))
        self.plant_start('r1')
        self.plant_close('r1')
        console = {'PYTHONIOENCODING': 'cp1252'}
        # The CLI path once: the gate and finalize are real processes on that console, and the gate is the planted pwsh script.
        outs = [self.stage(stage, 'r1', env=console, run=self.spawn) for stage in ('gate', 'finalize')]
        self.last_out = '\n'.join(o[1] for o in outs)
        self.assertEqual(self.gate_runs, [], 'the gate ran as a real process, not through the planted plan')
        self.assertNotIn('UnicodeEncodeError', self.last_out)
        rows = [r for r in self.remote_state()['rows'] if r['kind'] == 'gate']
        self.assertEqual([r['name'] for r in rows], [name], self.last_out)
        self.assertEqual(self.remote_state()['checkedTo'], culprit, self.last_out)


if __name__ == '__main__':
    from _case_pool import run_sharded
    sys.exit(run_sharded(__file__))
