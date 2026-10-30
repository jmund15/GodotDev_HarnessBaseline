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
import unittest
import uuid

CLAUDE = Path(__file__).resolve().parents[1]
ROOT = CLAUDE.parent
sys.path[:0] = [str(CLAUDE / 'hooks'), str(CLAUDE / 'tools'), str(CLAUDE / 'tests')]
import _transport_fixture
import sidecar_launch

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
        plant = self.work / 'plant'
        plant.mkdir()
        (plant / 'sitecustomize.py').write_text('''import io, json, os, sys, subprocess, urllib.request
from pathlib import Path
sys.path[:0] = [os.environ['PROOF_HOOKS'], os.environ['PROOF_TOOLS']]
import provider_capacity, _bounded_run, _dispatch_checks
reading = {'schemaVersion':1, 'transport':'codex', 'costModel':'plan-quota', 'sourceKind':'live', 'observedAt':'2099-01-01T00:00:00Z', 'status':'available', 'error':None, 'liveFailure':None, 'quota':{'windows':[], 'dispatchBand':'Hot', 'routingBand':'Hot', 'bindingWindow':None, 'credits':{'enabled':False, 'available':False, 'balance':None}}, 'balance':None}
def capacity(transport, *args, **kwargs):
    return dict(reading, transport=transport)
provider_capacity.reading = provider_capacity.cached = capacity
def note(kind):
    with open(os.environ['PROOF_CALLS'], 'a', encoding='utf-8') as fh: fh.write(json.dumps(kind)+'\\n')
real_tier = _dispatch_checks.tier_fit
def tier(*args, **kwargs):
    note('tier')
    return real_tier(*args, **kwargs)
_dispatch_checks.tier_fit = tier
real_bounded = _bounded_run.run_bounded
def bounded(args, *rest, **kwargs):
    if '--check' in args: note('preflight')
    return real_bounded(args, *rest, **kwargs)
_bounded_run.run_bounded = bounded
real_run = subprocess.run
def run(args, *rest, **kwargs):
    if isinstance(args, (list, tuple)) and 'proxy-stub' in str(args[0]):
        answer = 'claude-code-proxy 0.1.36' if '--version' in args else 'fixture-model'
        return subprocess.CompletedProcess(args, 0, answer, '')
    return real_run(args, *rest, **kwargs)
subprocess.run = run
real_urlopen = urllib.request.urlopen
def urlopen(url, *args, **kwargs):
    if isinstance(url, str) and '/healthz' in url: return io.BytesIO(b'{"ok":true}')
    return real_urlopen(url, *args, **kwargs)
urllib.request.urlopen = urlopen
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
                        PYTHONIOENCODING='utf-8', HARNESS_SIDECAR_PREFLIGHT='1',
                        SIDECAR_WATCH_POLL_SEC='0.2')
        for key in ('SC_LAUNCHER_DIR', 'SC_SNAPSHOT_OF', 'SIDECAR_DETACHED', 'SIDECAR_LAUNCH_ID', 'SIDECAR_SEAT_LAUNCH_ID', 'SIDECAR_FANOUT_ID'):
            self.env.pop(key, None)

    def run_cmd(self, args, payload=None, env=None):
        proc = subprocess.run(args, cwd=ROOT, env=env or self.env, input=json.dumps(payload) if payload else None,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertNotIn('Traceback', proc.stderr, proc.stderr)
        return proc

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

    def facts(self, shape='scout'):
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(self.work))
        return self.run_cmd([BASH, posix(LAUNCHER), '--check', '-m', 'fixture-sol',
                             '-e', 'high', '-G', shape, *(['-D', 'bare'] if shape == 'arm' else []),
                             '-f', posix(self.brief)], env=env)

    def test_check_brief_size_and_requested_pin(self):
        self.brief.write_bytes('é文abc'.encode('utf-8'))
        proc = self.facts()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('brief: 8 bytes; estimated tokens=2', proc.stdout)
        self.assertIn('requested pin: fixture-sol·high', proc.stdout)
        self.assertNotRegex(proc.stdout.lower(), r'recommend|right pin|wrong pin|use instead')
        self.assertEqual(self.pending(), [])

    def test_check_sizes_files_the_brief_names(self):
        named = self.work / 'big_reading.md'
        named.write_bytes(b'x' * 5000)
        self.brief.write_text('Read `%s` whole.\n' % named.as_posix(), encoding='utf-8')
        proc = self.facts()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r'dispatch size: 1 job .*names 5 KB more')
        named.write_bytes(b'x' * 3000000)
        over = self.facts()
        # dispatch_size.format_line owns the sizing summary (sidecar_common.sh 557d7ca dropped its
        # `brief + named files` line): a named file is reported as named, never as read.
        self.assertRegex(over.stdout, r'names \d{4} KB more')
        self.assertNotIn('OVER by', over.stdout)

    def test_check_ladder_row_and_unmapped_profile(self):
        proc = self.facts()
        self.assertIn('profile=scout; tier=fanout', proc.stdout)
        self.assertRegex(proc.stdout, r'ladder row: .*; rung: .+')
        unmapped = self.facts('arm')
        self.assertIn('profile=arm; no tier mapping exists', unmapped.stdout)
        self.assertNotRegex(unmapped.stdout.lower(), r'recommend|right pin|wrong pin|use instead')

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
        proc = self.facts()
        self.assertIn('shape rating: over; note: less width sufficed', proc.stdout)
        self.assertNotIn('older archive', proc.stdout)
        (data / 'orchestration_verdicts.json').unlink()
        archived_proc = self.facts()
        self.assertIn('shape rating: under; note: older archive', archived_proc.stdout)

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
        proc = self.facts()
        self.assertIn('shape rating: none exists', proc.stdout)
        self.brief.unlink()
        missing = self.facts()
        self.assertIn('brief: unreadable', missing.stdout)
        self.assertNotIn('brief: 0 bytes', missing.stdout)

    def test_wrapper_registers_and_checks(self):
        proc = self.wrapper(self.argv())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = self.pending()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['jobs'][0]['label'], 'wrapped')
        self.assertEqual(Path(rows[0]['record']).resolve(), (self.work / 'wrapped.record.json').resolve())
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})
        watch = self.run_cmd([sys.executable, str(CLAUDE / 'tools/watch_records.py'), '--session', '--wake'])
        self.assertEqual(watch.returncode, 0, watch.stderr)
        self.assertIn('wrapped', watch.stdout)

    def test_direct_hook_launch_registers_once_checks_once(self):
        argv = self.argv('direct')
        payload = self.payload('bash ' + shlex.join([str(LAUNCHER).replace('\\', '/'), *argv]))
        before = self.run_cmd([sys.executable, str(HOOK)], payload)
        # an admitted launch carries no advisory, so the hook may print nothing at all
        decision = (json.loads(before.stdout)['hookSpecificOutput'] if before.stdout.strip() else {}).get('permissionDecision')
        self.assertNotEqual(decision, 'deny', before.stdout)
        proc = self.run_cmd([BASH, posix(LAUNCHER), *argv])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.run_cmd([sys.executable, str(TABLE), '--launch'], payload)
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})

    def test_fanout_one_entry_per_job(self):
        jobs = self.work / 'jobs.json'
        briefs = []
        for label in ('one', 'two'):
            brief = self.work / (label + '.md')
            brief.write_text('Reply READY.\n', encoding='utf-8')
            briefs.append(dict(label=label, alias='fixture-sol', promptFile=str(brief), kind='scout',
                               workdir=str(ROOT)))
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

    def test_reused_record_keeps_the_new_pin(self):
        argv = self.argv('reused')
        self.assertEqual(self.wrapper([*argv, '-e', 'low']).returncode, 0)
        self.assertEqual(self.wrapper([*argv, '-e', 'high']).returncode, 0)
        rows = self.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['jobs'][0]['effort'], 'high')
        self.assertEqual(self.counts(), {'tier': 2, 'preflight': 2})

    def test_no_session_runs_untracked_with_note(self):
        self.env.pop('CLAUDE_CODE_SESSION_ID')
        proc = self.wrapper(self.argv('untracked'))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.pending(), [])
        self.assertIn('CLAUDE_CODE_SESSION_ID', proc.stderr)
        self.assertIn('not tracked', proc.stderr)
        self.assertEqual(self.counts(), {'tier': 1, 'preflight': 1})

    def test_record_carries_the_pin_basis(self):
        # S9: -Y <pick>,<spec> records why this pin was chosen and whether the slice's test intent is
        # clearly defined. An omitted -Y is valid and records null, never a guessed value.
        proc = self.wrapper([*self.argv('basis'), '-Y', 'cell,tight'])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads((self.work / 'basis.record.json').read_text(encoding='utf-8'))
        self.assertEqual(record.get('pinBasis'), 'cell,tight')
        plain = self.wrapper(self.argv('nobasis'))
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIsNone(json.loads((self.work / 'nobasis.record.json').read_text(encoding='utf-8')).get('pinBasis'))


if __name__ == '__main__':
    from _case_pool import run_cases, unittest_cases
    sys.exit(run_cases(unittest_cases(Registration)))
