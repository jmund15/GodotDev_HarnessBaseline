"""Synthetic producer identity tests. No provider calls, live logs, or checkout writes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
COMMON = Path(os.environ.get('SIDECAR_COMMON_MODULE', ROOT / '.claude/scripts/lib/sidecar_common.sh'))
FANOUT = Path(os.environ.get('SIDECAR_FANOUT_MODULE', ROOT / '.claude/tools/sidecar_fanout.py'))
sys.path.insert(0, str(ROOT / '.claude/tools'))
spec = importlib.util.spec_from_file_location('identity_fanout', FANOUT)
fanout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fanout)
BASH = fanout.sidecar_launch.git_bash()


CHILD = '11111111-1111-4111-8111-111111111111'


class ProducerIdentityTests(unittest.TestCase):
    def test_verbose_json_array_output_keeps_child_identity_and_usage(self):
        """`-o json` under the user setting `"verbose": true` is an ARRAY of events (live 2026-09-22:
        every launch died in the record writer with AttributeError: 'list' object has no attribute 'get')."""
        raw = json.dumps([{"type": "system", "subtype": "init", "session_id": CHILD},
                          {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
                          {"type": "result", "session_id": CHILD, "usage": {"input_tokens": 3, "output_tokens": 2}}])
        record, _, _ = self.record('parent-fixture', str(uuid.uuid4()), 0, raw=raw)
        self.assertEqual(CHILD, record.get('sessionId'))


    def record(self, parent, launch, rc, raw=None):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            script = home / 'fixture.sh'
            script.write_text('''set -euo pipefail
source "$COMMON"
SC_RECORD="$FIXTURE/run.record.json"
SC_LEDGER="$FIXTURE/ledger.jsonl"
SC_LEDGER_DEFAULT="$FIXTURE/default.jsonl"
SC_WORKDIR="$FIXTURE"
SC_MODEL="fixture-model"
SC_TRANSPORT="fixture"
SC_COST_MODEL="plan-quota"
SC_REGISTRY_CLI="$REGISTRY"
SC_RECORD_PARSER="claude"
SC_LABEL="same-label"
sc_scrub_env
env "${SC_SCRUB[@]}" python3 -c 'import os; assert "SIDECAR_LAUNCH_ID" not in os.environ'
RAW="$RAW_JSON"
sc_write_record "$RAW" "$RC"
cp "$SC_RECORD" "$FIXTURE/first.json"
sc_write_record "$RAW" "$RC"
''', encoding='utf-8', newline='\n')
            env = dict(os.environ, COMMON=COMMON.as_posix(), FIXTURE=home.as_posix(),
                       REGISTRY=(ROOT / '.claude/tools/model_registry.py').as_posix(),
                       RC=str(rc), HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE='1',
                       RAW_JSON=raw or json.dumps({"type": "result", "session_id": CHILD,
                                                   "usage": {"input_tokens": 3, "output_tokens": 2}}))
            env.pop('SIDECAR_LAUNCH_ID', None)
            env.pop('CLAUDE_CODE_SESSION_ID', None)
            if parent is not None:
                env['CLAUDE_CODE_SESSION_ID'] = parent
            if launch is not None:
                env['SIDECAR_LAUNCH_ID'] = launch
            result = subprocess.run([BASH, str(script)], env=env, cwd=temp,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(0, result.returncode, result.stderr)
            return (json.loads((home / 'run.record.json').read_text()),
                    json.loads((home / 'first.json').read_text()),
                    [json.loads(line) for line in (home / 'ledger.jsonl').read_text().splitlines()])

    def test_failed_record_keeps_parent_launch_and_child_identity(self):
        launch = str(uuid.uuid4())
        record, first, ledger = self.record('parent-fixture', launch, 9)
        self.assertEqual('parent-fixture', record.get('parentSessionId'))
        self.assertEqual(launch, record.get('launchId'))
        self.assertEqual('11111111-1111-4111-8111-111111111111', record.get('sessionId'))
        self.assertEqual(9, record['exitCode'])
        self.assertEqual(record['launchId'], first['launchId'])
        self.assertEqual([launch, launch], [row['launchId'] for row in ledger])

    def test_direct_launch_generates_stable_id_without_guessing_parent(self):
        record, first, _ = self.record(None, None, 0)
        self.assertIsNone(record.get('parentSessionId'))
        self.assertEqual(str(uuid.UUID(record['launchId'])), record['launchId'])
        self.assertEqual(record['launchId'], first['launchId'])

    def test_fanout_records_distinct_launch_ids_even_for_failed_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def failed(argv, **kwargs):
                calls.append(kwargs.get('env', {}))
                return 9
            planned = [(label, ['fixture'], str(Path(temp) / (label + '.out.json')))
                       for label in ('first', 'second')]
            with patch.object(fanout.subprocess, 'call', side_effect=failed), \
                    patch.object(fanout, 'freeze_fingerprint', return_value=None), \
                    patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'parent-fixture', 'SIDECAR_LAUNCH_ID': 'stale-inherited'}):
                result = fanout.run_jobs(planned, 1, temp)
            ids = [row.get('SIDECAR_LAUNCH_ID') for row in calls]
            self.assertEqual(2, len(set(ids)))
            self.assertNotIn('stale-inherited', ids)
            for label, call in zip(('first', 'second'), calls):
                self.assertEqual(call['SIDECAR_LAUNCH_ID'], result[label]['launchId'])
                self.assertEqual('parent-fixture', result[label]['parentSessionId'])
                self.assertEqual(9, result[label]['exit'])

    def test_launch_exception_keeps_expected_child_identity(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(fanout.subprocess, 'call', side_effect=OSError('fixture spawn failure')), \
                patch.object(fanout, 'freeze_fingerprint', return_value=None), \
                patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'parent-fixture'}):
            result = fanout.run_jobs([('job', ['fixture'], str(Path(temp) / 'job.out.json'))], 1, temp)['job']
            self.assertIsNone(result['exit'])
            self.assertEqual('parent-fixture', result.get('parentSessionId'))
            self.assertEqual(str(uuid.UUID(result['launchId'])), result['launchId'])

    def test_inventory_is_written_before_dispatch_and_contains_all_identities(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            events = []
            sync = os.fsync
            def fsync(fd):
                events.append('fsync')
                sync(fd)
            def failed(argv, **kwargs):
                events.append('launch')
                paths = list(Path(temp).glob('*.inventory.json'))
                calls.append((kwargs['env']['SIDECAR_LAUNCH_ID'],
                              [json.loads(p.read_text()) for p in paths]))
                return 9
            planned = [(label, ['fixture'], str(Path(temp) / (label + '.out.json')))
                       for label in ('first', 'second')]
            with patch.object(fanout.subprocess, 'call', side_effect=failed), \
                    patch.object(fanout, 'freeze_fingerprint', return_value=None), \
                    patch.object(fanout.os, 'fsync', side_effect=fsync), \
                    patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'parent-fixture'}):
                result = fanout.run_jobs(planned, 1, temp)
            self.assertEqual(['fsync', 'launch', 'launch'], events)
            self.assertEqual(2, len(calls))
            inventory_path = next(Path(temp).glob('*.inventory.json'))
            inventory = json.loads(inventory_path.read_text())
            self.assertEqual(1, inventory['schemaVersion'])
            self.assertEqual('parent-fixture', inventory['parentSessionId'])
            self.assertEqual(['first', 'second'], [j['label'] for j in inventory['jobs']])
            self.assertEqual([r['launchId'] for r in result.values()], [j['launchId'] for j in inventory['jobs']])
            self.assertEqual([c[0] for c in calls], [j['launchId'] for j in inventory['jobs']])
            for _, snapshots in calls:
                self.assertEqual([inventory], snapshots)
            for job in inventory['jobs']:
                self.assertEqual(str(Path(temp) / (job['label'] + '.record.json')), job['recordPath'])
                self.assertEqual(str(inventory_path), result[job['label']]['inventoryPath'])

    def test_inventory_write_failure_launches_zero_jobs(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(fanout.subprocess, 'call') as call, \
                patch.object(fanout, 'open', side_effect=OSError('fixture inventory failure'), create=True):
            with self.assertRaises(RuntimeError) as raised:
                fanout.run_jobs([('job', ['fixture'], str(Path(temp) / 'job.out.json'))], 1, temp)
            self.assertIn('cannot persist sidecar inventory', str(raised.exception))
            call.assert_not_called()

    def test_inventory_fsync_failure_launches_zero_jobs(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(fanout.subprocess, 'call') as call, \
                patch.object(fanout.os, 'fsync', side_effect=OSError('fixture fsync failure')):
            with self.assertRaises(RuntimeError):
                fanout.run_jobs([('job', ['fixture'], str(Path(temp) / 'job.out.json'))], 1, temp)
            call.assert_not_called()

    def test_inventory_collision_does_not_overwrite_or_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            fixed = uuid.uuid4()
            path = Path(temp) / ('fanout-' + str(fixed) + '.inventory.json')
            original = b'{"prior":"immutable fixture"}'
            path.write_bytes(original)
            with patch.object(fanout.uuid, 'uuid4', return_value=fixed), \
                    patch.object(fanout.subprocess, 'call') as call:
                with self.assertRaises(RuntimeError):
                    fanout.run_jobs([('job', ['fixture'], str(Path(temp) / 'job.out.json'))], 1, temp)
                call.assert_not_called()
            self.assertEqual(original, path.read_bytes())

    def test_inventory_unknown_parent_stays_unknown(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(fanout.subprocess, 'call', return_value=0), \
                patch.object(fanout, 'freeze_fingerprint', return_value=None):
            result = fanout.run_jobs([('job', ['fixture'], str(Path(temp) / 'job.out.json'))], 1, temp)
            inventory = json.loads(Path(result['job']['inventoryPath']).read_text())
            self.assertIsNone(inventory['parentSessionId'])
            self.assertIsNone(result['job']['parentSessionId'])

    def test_previous_record_cannot_be_resumed_by_new_failed_launch(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(fanout.subprocess, 'call', return_value=9), \
                patch.object(fanout, 'freeze_fingerprint', return_value=None):
            path=Path(temp)/'job.record.json'
            path.write_text(json.dumps({'launchId':str(uuid.uuid4()),'sessionId':'old-child'}),encoding='utf-8')
            result=fanout.run_jobs([('job',['fixture'],str(Path(temp)/'job.out.json'))],1,temp)['job']
            self.assertIsNone(result['record'])
            self.assertIsNone(result['resumeWith'])
            self.assertEqual('old-child',json.loads(path.read_text())['sessionId'])

    def test_matching_record_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'job.record.json'
            def failed(argv, **kwargs):
                path.write_text(json.dumps({'launchId':kwargs['env']['SIDECAR_LAUNCH_ID'],
                    'parentSessionId':kwargs['env'].get('CLAUDE_CODE_SESSION_ID'), 'sessionId':'current-child'}),encoding='utf-8')
                return 9
            with patch.object(fanout.subprocess,'call',side_effect=failed), \
                    patch.object(fanout,'freeze_fingerprint',return_value=None):
                result=fanout.run_jobs([('job',['fixture'],str(Path(temp)/'job.out.json'))],1,temp)['job']
            self.assertEqual(str(path),result['record'])
            self.assertEqual('current-child',result['resumeWith'])

    def test_direct_run_persists_identity_before_child_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); script=home/'direct.sh'
            script.write_text('''set -euo pipefail
source "$COMMON"
SC_RECORD="$FIXTURE/job.record.json"
SC_LABEL="job"
SC_STALL_SEC=0
run_fixture() {
  python3 -c 'import json,pathlib,os; p=pathlib.Path(os.environ["FIXTURE"]); rows=list(p.glob("*.inventory.json")); assert len(rows)==1; d=json.loads(rows[0].read_text()); assert d["jobs"][0]["label"]=="job"; print("{}")'
}
sc_run_watched run_fixture ""
sc_run_watched run_fixture ""
printf '%s' "$SC_LAUNCH_ID" > "$FIXTURE/id.txt"
''',encoding='utf-8',newline='\n')
            env=dict(os.environ,COMMON=COMMON.as_posix(),FIXTURE=home.as_posix(),HOME=str(home),USERPROFILE=str(home))
            env.pop('SIDECAR_LAUNCH_ID',None)
            result=subprocess.run([BASH,str(script)],env=env,cwd=temp,capture_output=True,text=True,timeout=60)
            self.assertEqual(0,result.returncode,result.stderr)
            inventories=list(home.glob('*.inventory.json'))
            self.assertEqual(1,len(inventories))
            data=json.loads(inventories[0].read_text())
            self.assertEqual((home/'id.txt').read_text(),data['jobs'][0]['launchId'])
            self.assertEqual(str(home/'job.record.json'),data['jobs'][0]['recordPath'])

    def test_direct_preflight_failures_never_execute_child(self):
        for failure in ('collision', 'invalid-id', 'blocked-parent'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                launch = str(uuid.uuid4())
                record = home / 'job.record.json'
                inventory = home / ('fanout-' + launch + '.inventory.json')
                original = b'{"prior":"preserve"}'
                if failure == 'collision':
                    inventory.write_bytes(original)
                elif failure == 'invalid-id':
                    launch = 'not-a-uuid'
                else:
                    (home / 'blocked').write_bytes(original)
                    record = home / 'blocked/job.record.json'
                script = home / 'refused.sh'
                script.write_text('''set -euo pipefail
source "$COMMON"
SC_RECORD="$RECORD"
SC_LABEL="fixture"
SC_STALL_SEC=0
run_fixture() { touch "$FIXTURE/child-called"; }
if sc_run_watched run_fixture ""; then exit 91; else actual=$?; fi
[ "$actual" -eq 2 ]
[ "$rc" -eq 2 ]
[ -z "$OUTPUT" ]
[ "${SC_LAUNCH_PREPARED:-0}" = 0 ]
''', encoding='utf-8', newline='\n')
                env = dict(os.environ, COMMON=COMMON.as_posix(), RECORD=record.as_posix(),
                           FIXTURE=home.as_posix(), SIDECAR_LAUNCH_ID=launch,
                           HOME=str(home), USERPROFILE=str(home))
                workdir = home / 'work'
                workdir.mkdir()
                result = subprocess.run([BASH, str(script)], env=env, cwd=workdir,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse((home / 'child-called').exists())
                self.assertFalse(record.exists())
                if failure == 'collision':
                    self.assertEqual(original, inventory.read_bytes())
                elif failure == 'blocked-parent':
                    self.assertEqual(original, (home / 'blocked').read_bytes())

    def test_empty_planned_list_does_not_create_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual({}, fanout.run_jobs([], 1, temp))
            self.assertEqual([], list(Path(temp).glob('*.inventory.json')))

    def test_repeated_calls_get_distinct_inventory_files(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(fanout.subprocess, 'call', return_value=0), \
                patch.object(fanout, 'freeze_fingerprint', return_value=None):
            planned = [('job', ['fixture'], str(Path(temp) / 'job.out.json'))]
            first = fanout.run_jobs(planned, 1, temp)
            second = fanout.run_jobs(planned, 1, temp)
            self.assertNotEqual(first['job']['inventoryPath'], second['job']['inventoryPath'])
            self.assertEqual(2, len(list(Path(temp).glob('*.inventory.json'))))


if __name__ == '__main__':
    unittest.main()
