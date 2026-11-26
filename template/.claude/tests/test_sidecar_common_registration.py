#!/usr/bin/env python3
"""Real launcher proof: wrappers, hook parity, fan-out, refusals and sessionless callers.

Only external capacity/proxy/client seams are planted. The launcher, hooks, state writer,
fan-out inventory and watcher are real. Run with Python; no model request is made.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

CLAUDE = Path(__file__).resolve().parents[1]
ROOT = CLAUDE.parent
sys.path[:0] = [str(CLAUDE / 'hooks'), str(CLAUDE / 'tools'), str(CLAUDE / 'tests')]
import _mcp_plant
import _transport_fixture
import dispatch_facts
import sidecar_launch

# The launcher validates its tool profile against the MCP servers the caller's config registers; a runner has none.
_mcp_plant.plant()

BASH = sidecar_launch.git_bash()
LAUNCHER = CLAUDE / 'scripts/codex_proxy_sidecar.sh'
HOOK = CLAUDE / 'hooks/sidecar_dispatch_context.py'
TABLE = CLAUDE / 'hooks/dispatch_table.py'


def posix(path):
    text = str(path).replace('\\', '/')
    return '/' + text[0].lower() + text[2:] if len(text) > 1 and text[1] == ':' else text


class Registration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='launch-registration-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.sid = str(uuid.uuid4())
        self.state = self.work / 'state' / (self.sid[:8] + '.json')
        # The dispatch doctrine check is not under test: the ladder is read and the orchestration skill loaded.
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({'model_ladder_ready': True, 'fired_since_compaction': {
            'skill_in_context:orchestration': True}}), encoding='utf-8')
        self.calls = self.work / 'checks.jsonl'
        registry = json.loads((CLAUDE / 'reference/external_models.json').read_text(encoding='utf-8'))
        row = next(row for row in registry['models'] if row.get('transport') == 'codex' and 'fanout' in row.get('roles', []))
        registry['aliasHistory'] = {}
        registry['models'] = [dict(row, alias='fixture-sol', id='fixture-model', version='fixture-model', roles=['fanout'])]
        for transport in registry['transports'].values():
            transport['imageRoute'] = {'state': 'unverified'}
        roster = self.work / 'registry.json'
        roster.write_text(json.dumps(registry), encoding='utf-8')
        self.brief = self.work / 'brief.md'
        self.brief.write_text('Reply READY.\n', encoding='utf-8')
        self.client = self.work / 'stub_claude'
        self.client.write_text('#!/usr/bin/env bash\ncat >/dev/null\nprintf \'%s\\n\' \'{"type":"result","subtype":"success","is_error":false,"result":"READY","session_id":"stub-sid","usage":{"input_tokens":1,"output_tokens":1}}\'\n', encoding='utf-8', newline='\n')
        self.proxy = self.work / 'proxy-stub'
        self.proxy.write_text('#!/usr/bin/env bash\nexit 0\n', encoding='utf-8', newline='\n')
        self.client.chmod(0o755)
        self.proxy.chmod(0o755)
        auth = self.work / '.config/claude-code-proxy/codex/auth.json'
        auth.parent.mkdir(parents=True)
        auth.write_text('{}', encoding='utf-8')
        # Profile resolution refuses a server the config does not define. A POSIX runner reads this HOME's
        # `.claude.json`, so register every server a kind names there; Windows reads the developer's own.
        import tool_profiles
        servers = sorted({name for shape in tool_profiles.load_table()['kinds'].values() for name in shape['mcpServers']})
        (self.work / '.claude.json').write_text(json.dumps({'mcpServers': {name: {'command': 'true'} for name in servers}}),
                                                encoding='utf-8')
        plant = self.work / 'plant'
        plant.mkdir()
        # Every python process the launch starts reads this file, and most need none of it: each patch applies when
        # its module is first imported, so a process that never imports the module pays nothing.
        (plant / 'sitecustomize.py').write_text('''import importlib.abc, importlib.util, io, json, os, sys
sys.path[:0] = [os.environ['PROOF_HOOKS'], os.environ['PROOF_TOOLS']]
reading = {'schemaVersion':1, 'transport':'codex', 'costModel':'plan-quota', 'sourceKind':'live', 'observedAt':'2099-01-01T00:00:00Z', 'status':'available', 'error':None, 'liveFailure':None, 'quota':{'windows':[], 'dispatchBand':'Hot', 'routingBand':'Hot', 'bindingWindow':None, 'credits':{'enabled':False, 'available':False, 'balance':None}}, 'balance':None}
def note(kind):
    with open(os.environ['PROOF_CALLS'], 'a', encoding='utf-8') as fh: fh.write(json.dumps(kind)+'\\n')
def provider_capacity(module):
    module.reading = module.cached = lambda transport, *args, **kwargs: dict(reading, transport=transport)
def _dispatch_checks(module):
    real = module.tier_fit
    def tier(*args, **kwargs):
        note('tier')
        return real(*args, **kwargs)
    module.tier_fit = tier
def _bounded_run(module):
    real = module.run_bounded
    def bounded(args, *rest, **kwargs):
        if '--check' in args: note('preflight')
        return real(args, *rest, **kwargs)
    module.run_bounded = bounded
def subprocess(module):
    real = module.run
    def run(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and 'proxy-stub' in str(args[0]):
            answer = 'claude-code-proxy 0.1.36' if '--version' in args else 'fixture-model'
            return module.CompletedProcess(args, 0, answer, '')
        return real(args, *rest, **kwargs)
    module.run = run
def urllib_request(module):
    real = module.urlopen
    def urlopen(url, *args, **kwargs):
        if isinstance(url, str) and '/healthz' in url: return io.BytesIO(b'{"ok":true}')
        return real(url, *args, **kwargs)
    module.urlopen = urlopen
PATCHES = {'provider_capacity': provider_capacity, '_dispatch_checks': _dispatch_checks, '_bounded_run': _bounded_run,
           'subprocess': subprocess, 'urllib.request': urllib_request}
class Patched(importlib.abc.MetaPathFinder):
    busy = set()
    def find_spec(self, name, path=None, target=None):
        if name not in PATCHES or name in self.busy: return None
        self.busy.add(name)
        try: spec = importlib.util.find_spec(name)
        finally: self.busy.discard(name)
        if spec is None or spec.loader is None: return None
        real = spec.loader
        class Loader(importlib.abc.Loader):
            def create_module(self, spec): return real.create_module(spec)
            def exec_module(self, module):
                real.exec_module(module)
                PATCHES[name](module)
            def __getattr__(self, attr): return getattr(real, attr)
        spec.loader = Loader()
        return spec
sys.meta_path.insert(0, Patched())
for loaded in [name for name in PATCHES if name in sys.modules]: PATCHES[loaded](sys.modules[loaded])
''', encoding='utf-8', newline='\n')
        self.env = dict(os.environ, CLAUDE_CODE_SESSION_ID=self.sid,
                        CLAUDE_PROJECT_DIR=str(ROOT), HARNESS_HOOK_STATE_DIR=str(self.state.parent),
                        HOME=posix(self.work), CLAUDE_BIN=posix(self.client), CCP_BIN=posix(self.proxy),
                        CCP_BIN_SCOPE='process', SIDECAR_NO_SHIM='1', CCP_LOG=posix(self.work / 'proxy.log'),
                        SIDECAR_LEDGER_PATH=str(self.work / 'ledger.jsonl'),
                        SIDECAR_EXHAUSTED_DIR=str(self.work / 'exhausted'),
                        LAUNCH_MIN_COMMIT_GIB='0', LAUNCH_MIN_PHYSICAL_GIB='0',
                        HARNESS_MODEL_REGISTRY=str(roster),
                        PROVIDER_CAPACITY_CACHE=_transport_fixture._available_capacity_cache(),
                        PYTHONPATH=os.pathsep.join(([os.environ['HARNESS_READ_RECORDER']] if os.environ.get('HARNESS_READ_LOG') and os.environ.get('HARNESS_READ_RECORDER') else []) + [str(plant)]), PROOF_HOOKS=str(CLAUDE / 'hooks'),
                        PROOF_TOOLS=str(CLAUDE / 'tools'), PROOF_CALLS=str(self.calls),
                        PYTHONIOENCODING='utf-8', HARNESS_SIDECAR_PREFLIGHT='1')
        for key in ('SC_LAUNCHER_DIR', 'SC_SNAPSHOT_OF', 'SIDECAR_DETACHED', 'SIDECAR_LAUNCH_ID', 'SIDECAR_SEAT_LAUNCH_ID', 'SIDECAR_FANOUT_ID'):
            self.env.pop(key, None)

    def run_cmd(self, args, payload=None, env=None):
        proc = subprocess.run(args, cwd=ROOT, env=env or self.env, input=json.dumps(payload) if payload else None,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertNotIn('Traceback', proc.stderr, proc.stderr)
        return proc

    def env_without_preflight(self):
        """For a case whose subject is the journal or the claim: the launch's own `--check` pass is what the
        registration cases count, so these skip it rather than pay for a second launcher start."""
        return dict(self.env, HARNESS_SIDECAR_PREFLIGHT='0')

    def argv(self, label='wrapped', model='fixture-sol', shape='scout'):
        return ['-m', model, '-G', shape, *(['-D', 'bare'] if shape == 'arm' else []), '-l', label, '-R',
                str(self.work / (label + '.record.json')).replace('\\', '/'),
                '-f', str(self.brief).replace('\\', '/'), '-d', str(ROOT).replace('\\', '/')]

    def payload(self, command):
        return dict(tool_name='Bash', session_id=self.sid, cwd=str(ROOT), tool_use_id='call-proof',
                    tool_input={'command': command})

    def pending(self):
        return json.loads(self.state.read_text(encoding='utf-8')).get('dispatch_table_pending', []) if self.state.exists() else []

    def counts(self):
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return {kind: rows.count(kind) for kind in ('tier', 'preflight')}

    def wrapper(self, argv):
        wrapper = self.work / 'wrapper.sh'
        wrapper.write_text('bash ' + shlex.join([posix(LAUNCHER), *argv]) + '\n', encoding='utf-8', newline='\n')
        return self.run_cmd([BASH, posix(wrapper)])

    def facts_args(self, shape):
        return ['--check', '-m', 'fixture-sol', '-e', 'high', '-G', shape, *(['-D', 'bare'] if shape == 'arm' else [])]

    def facts_cli(self, shape='scout'):
        """The launcher's own `--check` answer, spawned: the one case that proves the CLI path."""
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.work))
        return self.run_cmd([BASH, posix(LAUNCHER), *self.facts_args(shape), '-f', posix(self.brief)], env=env)

    def facts(self, shape='scout'):
        """The facts `--check` prints, from the unit that owns them, in process: the cases below decide what the
        facts say, not how the launcher starts. The verdict module reads its verdict files from constants fixed at
        import, so they are pointed at this case's project dir."""
        import orchestration_metrics
        data = self.work / '.claude'
        planted = dict(ARCHIVE=str(data / 'orchestration_metrics.jsonl'),
                       PENDING_VERDICTS=str(data / 'orchestration_verdicts.json'),
                       LEGACY_PENDING_VERDICTS=str(data / 'scratch' / 'orchestration_verdicts.json'))
        here = os.getcwd()
        os.chdir(ROOT)
        try:
            with mock.patch.dict(os.environ, HARNESS_MODEL_REGISTRY=self.env['HARNESS_MODEL_REGISTRY']), \
                    mock.patch.multiple(orchestration_metrics, **planted):
                return '\n'.join(dispatch_facts.fact_lines(
                    CLAUDE, 'fixture-sol', [*self.facts_args(shape), '-f', str(self.brief)]))
        finally:
            os.chdir(here)

    def test_check_brief_size_and_requested_pin(self):
        self.brief.write_bytes('é文abc'.encode('utf-8'))
        proc = self.facts_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('brief: 8 bytes; estimated tokens=2', proc.stdout)
        self.assertIn('requested pin: fixture-sol·high', proc.stdout)
        self.assertNotRegex(proc.stdout.lower(), r'recommend|right pin|wrong pin|use instead')
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.facts().splitlines()[:2], proc.stdout.splitlines()[:2], 'the unit and the CLI print the same facts')

    def test_check_sizes_files_the_brief_names(self):
        named = self.work / 'big_reading.md'
        named.write_bytes(b'x' * 5000)
        self.brief.write_text('Read `%s` whole.\n' % named.as_posix(), encoding='utf-8')
        self.assertRegex(self.facts(), r'dispatch size: 1 job .*names 5 KB more')
        named.write_bytes(b'x' * 3000000)
        over = self.facts()
        # dispatch_size.format_line owns the sizing summary (the launcher no longer prints its
        # `brief + named files` line): a named file is reported as named, never as read.
        self.assertRegex(over, r'names \d{4} KB more')
        self.assertNotIn('OVER by', over)

    def test_check_ladder_row_and_unmapped_profile(self):
        facts = self.facts()
        self.assertIn('profile=scout; tier=fanout', facts)
        self.assertRegex(facts, r'ladder row: .*; rung: .+')
        unmapped = self.facts('arm')
        self.assertIn('profile=arm; no tier mapping exists', unmapped)
        self.assertNotRegex(unmapped.lower(), r'recommend|right pin|wrong pin|use instead')

    def test_check_latest_shape_rating_and_note(self):
        data = self.work / '.claude'
        data.mkdir()
        archived = {'shape': 'under', 'shape_model': 'fixture-model', 'shape_profile': 'survey',
                    'shape_rated_at': '2026-01-01T00:00:00Z', 'note': 'older archive'}
        (data / 'orchestration_metrics.jsonl.1').write_text(json.dumps(archived) + '\n', encoding='utf-8')
        pending = {str(i): dict(archived, **extra) for i, extra in enumerate([
            {'shape': 'over', 'shape_rated_at': '2026-02-01T00:00:00Z', 'note': 'less width sufficed'},
            {'shape': 'right', 'shape_profile': 'author', 'shape_rated_at': '2026-03-01T00:00:00Z'},
            {'shape': 'right', 'shape_model': 'other-model', 'shape_rated_at': '2026-04-01T00:00:00Z'},
            {'shape': None, 'shape_rated_at': '2026-05-01T00:00:00Z'}])}
        (data / 'orchestration_verdicts.json').write_text(json.dumps(pending), encoding='utf-8')
        facts = self.facts()
        self.assertIn('shape rating: over; note: less width sufficed', facts)
        self.assertNotIn('older archive', facts)
        (data / 'orchestration_verdicts.json').unlink()
        self.assertIn('shape rating: under; note: older archive', self.facts())

    def test_check_refuses_an_enum_flag_the_launch_refuses(self):
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.work))
        for flag, value, word in (('-D', 'standard', 'disclosure tier'), ('-G', 'writer', 'unknown kind'),
                                  ('-p', 'yolo', 'permission mode'), ('-Y', 'vibes,tight', 'pin basis'),
                                  ('-Y', 'cell,vague', 'pin basis'), ('-Y', 'cell', 'pin basis'),
                                  ('-Y', ',tight', 'pin basis')):
            proc = self.run_cmd([BASH, posix(LAUNCHER), '--check', '-m', 'fixture-sol', '-e', 'high',
                                 flag, value, '-f', posix(self.brief)], env=env)
            self.assertEqual(proc.returncode, 2, (flag, proc.stdout, proc.stderr))
            self.assertIn(word, proc.stderr)
        ok = self.run_cmd([BASH, posix(LAUNCHER), '--check', '-m', 'fixture-sol', '-e', 'high',
                           '-G', 'lens', '-f', posix(self.brief)], env=env)
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_check_no_shape_rating_and_unreadable_brief(self):
        self.assertIn('shape rating: none exists', self.facts())
        self.brief.unlink()
        missing = self.facts()
        self.assertIn('brief: unreadable', missing)
        self.assertNotIn('brief: 0 bytes', missing)

    def test_wrapper_registers_checks_and_a_reused_record_keeps_the_new_pin(self):
        # -Y <pick>,<spec> records why the pin was chosen; an omitted -Y records null, never a guessed
        # value, and a relaunch on the same record carries the new pin and the new (absent) basis.
        argv = self.argv()
        proc = self.wrapper([*argv, '-e', 'low', '-Y', 'cell,tight'])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = self.pending()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['jobs'][0]['label'], 'wrapped')
        self.assertEqual(Path(rows[0]['record']).resolve(), (self.work / 'wrapped.record.json').resolve())
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})
        # A clean DONE is no decision event, so the session watch prints it and stays armed; stop it once printed.
        output = self.work / 'watch.out'
        with output.open('w', encoding='utf-8') as sink:
            watch = subprocess.Popen([sys.executable, str(CLAUDE / 'tools/watch_records.py'), '--session', '--wake'],
                                     cwd=ROOT, env=self.env, stdout=sink, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 120
            while time.time() < deadline and watch.poll() is None and 'wrapped' not in output.read_text(encoding='utf-8'):
                time.sleep(0.1)
        finally:
            watch.kill()
            watch.wait()
        self.assertIn('wrapped', output.read_text(encoding='utf-8'))
        record = self.work / 'wrapped.record.json'
        self.assertEqual(json.loads(record.read_text(encoding='utf-8')).get('pinBasis'), 'cell,tight')
        self.assertEqual(self.wrapper([*argv, '-e', 'high']).returncode, 0)
        rows = self.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['jobs'][0]['effort'], 'high')
        self.assertIsNone(json.loads(record.read_text(encoding='utf-8')).get('pinBasis'))
        self.assertEqual(self.counts(), {'tier': 2, 'preflight': 2})

    def test_fanout_one_entry_per_job(self):
        jobs = self.work / 'jobs.json'
        cell = self.work / 'cell'  # the jobs' workdir: a planted repo, so fanout's fingerprint reads no live checkout
        cell.mkdir()
        subprocess.run(['git', 'init', '-q', str(cell)], check=True, capture_output=True, cwd=self.work)
        briefs = []
        for label in ('one', 'two'):
            brief = self.work / (label + '.md')
            brief.write_text('Reply READY.\n', encoding='utf-8')
            briefs.append(dict(label=label, alias='fixture-sol', promptFile=str(brief), kind='scout',
                               workdir=str(cell)))
        jobs.write_text(json.dumps(briefs), encoding='utf-8')
        out = self.work / 'fanout'
        command = shlex.join(['python3', str(CLAUDE / 'tools/sidecar_fanout.py').replace('\\', '/'),
                             str(jobs).replace('\\', '/'), '--out-dir', str(out).replace('\\', '/')])
        payload = self.payload(command)
        self.run_cmd([sys.executable, str(HOOK)], payload)
        proc = self.run_cmd([sys.executable, str(CLAUDE / 'tools/sidecar_fanout.py'), str(jobs), '--out-dir', str(out)])
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.run_cmd([sys.executable, str(TABLE), '--launch'], payload)
        self.assertEqual(sorted(row['jobs'][0]['label'] for row in self.pending()), ['one', 'two'])
        self.assertEqual(self.counts(), {'tier': 2, 'preflight': 2}, self.state.read_text())

    def test_wrapper_refusal_uses_hook_reason(self):
        argv = self.argv('refused', 'fixture-sol', 'author')
        payload = self.payload('bash ' + shlex.join([str(LAUNCHER).replace('\\', '/'), *argv]))
        hook = self.run_cmd([sys.executable, str(HOOK)], payload)
        reason = json.loads(hook.stdout)['hookSpecificOutput']['permissionDecisionReason']
        proc = self.wrapper(argv)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(reason, proc.stderr)
        self.assertEqual(self.pending(), [])
        self.assertFalse((self.work / 'refused.record.json').exists())

    def test_session_launch_without_identity_refuses(self):
        argv = self.argv()
        for flag in ('-l', '-R'):
            index = argv.index(flag)
            del argv[index:index + 2]
        proc = self.wrapper(argv)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('literal -R and -l', proc.stderr)
        self.assertEqual(self.pending(), [])

    def test_no_session_runs_untracked_with_note(self):
        self.env.pop('CLAUDE_CODE_SESSION_ID')
        proc = self.wrapper(self.argv('untracked'))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.pending(), [])
        self.assertIn('CLAUDE_CODE_SESSION_ID', proc.stderr)
        self.assertIn('not tracked', proc.stderr)
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})

    def test_launch_journal_records_start_and_end(self):
        # X1 S2 U2: a real foreground launch writes a started row at claim and an ended row with its rc.
        proc = self.run_cmd([BASH, posix(LAUNCHER), *self.argv('journaled')], env=self.env_without_preflight())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = self.work / 'journaled.record.json'
        journal = Path(str(record) + '.journal.jsonl')
        rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()] if journal.exists() else []
        events = [row.get('event') for row in rows]
        self.assertIn('started', events, rows)
        self.assertEqual(events[-1], 'ended', rows)
        started = next(row for row in rows if row['event'] == 'started')
        for key in ('ts', 'launchId', 'pid', 'session', 'label', 'record', 'call'):
            self.assertIn(key, started, started)
        self.assertEqual(started['label'], 'journaled')
        ended = rows[-1]
        self.assertEqual(ended['code'], 0, ended)
        self.assertEqual(Path(record.parent / (record.name + '.exit')).read_text().strip(), '0')

    def test_direct_hook_launch_registers_once_checks_once_and_records_the_call_its_intent_names(self):
        # X1 S2 U6 / D3: the hook writes the intent at the record path, and the launch claims it, so the
        # journal's rows carry the call a direct launch lost before the intent existed.
        argv = self.argv('direct')
        payload = self.payload('bash ' + shlex.join([str(LAUNCHER).replace('\\', '/'), *argv]))
        before = self.run_cmd([sys.executable, str(HOOK)], payload)
        # an admitted launch carries no advisory, so the hook may print nothing at all
        decision = (json.loads(before.stdout)['hookSpecificOutput'] if before.stdout.strip() else {}).get('permissionDecision')
        self.assertNotEqual(decision, 'deny', before.stdout)
        record = self.work / 'direct.record.json'
        intent = Path(str(record) + '.launch-intent-call-proof.json')
        self.assertTrue(intent.exists(), sorted(p.name for p in self.work.iterdir()))
        intent_row = json.loads(intent.read_text(encoding='utf-8'))
        self.assertEqual((intent_row.get('call'), intent_row.get('session')), ('call-proof', self.sid), intent_row)
        proc = self.run_cmd([BASH, posix(LAUNCHER), *argv])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        journal = Path(str(record) + '.journal.jsonl')
        rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(next(row for row in rows if row['event'] == 'started')['call'], 'call-proof', rows)
        self.assertFalse(intent.exists(), 'the intent was not claimed')
        self.run_cmd([sys.executable, str(TABLE), '--launch'], payload)
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})

    def test_direct_launch_claims_the_call_its_intent_names(self):
        # X1 S1 U4 / D3: the hook's intent at the record path names the call; the launch's rows carry it.
        record = self.work / 'claimed.record.json'
        intent = Path(str(record) + '.launch-intent-toolu_direct.json')
        intent.write_text(json.dumps({'call': 'toolu_direct', 'session': self.sid}), encoding='utf-8')
        proc = self.run_cmd([BASH, posix(LAUNCHER), *self.argv('claimed')], env=self.env_without_preflight())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(intent.exists(), 'the intent was not claimed')
        self.assertTrue(Path(str(intent) + '.claimed').exists())
        journal = Path(str(record) + '.journal.jsonl')
        rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(next(row for row in rows if row['event'] == 'started')['call'], 'toolu_direct', rows)
        self.assertEqual(rows[-1]['call'], 'toolu_direct', rows)


PARTS = 2


def main(part=1):
    """Run this part's share of the cases, round-robin; `test_sidecar_common_registration_part2.py` runs the other."""
    from _case_pool import run_cases, unittest_cases
    for n in range(2, PARTS + 1):
        name = 'test_sidecar_common_registration_part%d.py' % n
        assert (CLAUDE / 'tests' / name).is_file(), 'missing part file ' + name
    return run_cases(unittest_cases(Registration)[part - 1::PARTS])


if __name__ == '__main__':
    sys.exit(main())
