#!/usr/bin/env python3
"""Per-record checked output, source identity, and completion ordering."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
COMMON = TOOLS.parent / 'scripts/lib/sidecar_common.sh'


class Completion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sidecar_checked_')
        self.root = Path(self.tmp.name)
        (self.root / 'source.txt').write_text('the true quote\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def module(self):
        self.assertTrue((TOOLS / 'sidecar_completion.py').is_file(), 'host completion checker is missing')
        import sidecar_completion
        return sidecar_completion

    def rows(self):
        return [{'id': 'C1', 'confidence': 'verified', 'evidence': [{'file': 'source.txt', 'quote': quote}]}
                for quote in ('the true quote', 'fabricated quote')]

    def raw(self, record):
        raw = json.dumps({'type': 'result', 'result': '```claims\n' + '\n'.join(map(json.dumps, self.rows())) + '\n```'})
        Path(str(record) + '.out').write_text(raw, encoding='utf-8')
        return raw

    def test_direct_distinct_records_raw_unchanged(self):
        mod = self.module()
        for name in ('first', 'second'):
            record = self.root / (name + '.record.json')
            raw = self.raw(record)
            self.assertEqual(mod.complete_record(record, root=self.root), 1)
            checked = json.loads(Path(str(record) + '.claims.json').read_text())
            self.assertEqual([r['verdict'] for r in checked['claims']], ['VERIFIED', 'UNVERIFIED'])
            self.assertEqual([r['sourceKey'] for r in checked['claims']], [name + '.record#1', name + '.record#2'])
            self.assertEqual(checked['claims'][1]['confidence'], 'unverified')
            self.assertEqual(Path(str(record) + '.out').read_text(), raw)
            self.assertTrue(Path(str(record) + '.post_check.json').is_file())

    def test_structured_overlaps_result_is_checked_not_passed_empty(self):
        """The worklog sidecar returns `{overlaps, checked}` JSON: every cite is checked, `checked` included."""
        mod = self.module()
        record = self.root / 'worklog.record.json'
        cite = lambda quote: [{'file': 'source.txt', 'quote': quote}]
        result = {'overlaps': [{'title': 'real', 'evidence': cite('the true quote')},
                               {'title': 'planted', 'evidence': cite('fabricated quote')}],
                  'checked': {'indexRead': True, 'stoppedAt': 'verified-overlaps', 'evidence': cite('the true quote')}}
        Path(str(record) + '.out').write_text(json.dumps({'type': 'result', 'result': json.dumps(result)}), encoding='utf-8')
        self.assertEqual(mod.complete_record(record, root=self.root), 1)
        checked = json.loads(Path(str(record) + '.claims.json').read_text())
        self.assertEqual(checked['verification']['verdicts'], {
            'worklog.record#1': 'VERIFIED', 'worklog.record#2': 'UNVERIFIED', 'worklog.record#checked': 'VERIFIED'})

    def test_non_claim_json_keeps_answer_without_checker(self):
        mod = self.module()
        record = self.root / 'plain.json'
        Path(str(record) + '.out').write_text(json.dumps({'result': {'answer': 'a document'}}))
        with patch.object(mod.post_check, 'main') as check:
            self.assertEqual(mod.complete_record(record, root=self.root), 0)
            check.assert_not_called()
        checked = json.loads(Path(str(record) + '.claims.json').read_text())
        self.assertEqual(json.loads(checked['result']), {'answer': 'a document'})

    def test_unchecked_direct_and_inconsistent_pass_never_pass(self):
        mod = self.module()
        record = self.root / 'unchecked.json'
        Path(str(record) + '.out').write_text(json.dumps({'result': {'claims': [
            {'evidence': []}]}}))
        self.assertEqual(mod.complete_record(record, root=self.root), 1)
        body = json.loads(Path(str(record) + '.claims.json').read_text())
        self.assertEqual(body['verification']['status'], 'fail')
        report = self.root / 'bad-report.json'
        mod.post_check._write(report, dict(status='pass', counts=dict(VERIFIED=0, UNVERIFIED=0, UNCHECKED=1),
                                          verdicts={'x': {'verdict': 'UNCHECKED'}}, errors=[]))
        body = mod.publish([{'id': 'x'}], report, self.root / 'published.json')
        self.assertEqual(body['verification']['status'], 'unavailable')

    def test_command_once(self):
        mod = self.module()
        record = self.root / 'cmd.json'
        Path(str(record) + '.out').write_text(json.dumps({'result': {'claims': [
            {'evidence': [{'command': 'git status --short', 'output': 'M source.txt'}]}]}}))
        import verify_claims
        with patch.object(verify_claims, '_run_git', return_value=(0, 'M source.txt', '')) as run:
            self.assertEqual(mod.complete_record(record, root=self.root), 0)
            self.assertEqual(run.call_count, 1)

    def test_missing_and_unreadable_report_unchecked(self):
        mod = self.module()
        for corrupt in (False, True):
            record = self.root / ('bad' + str(corrupt) + '.json')
            self.raw(record)
            def absent(argv):
                if corrupt:
                    Path(argv[argv.index('--out') + 1]).write_text('{')
                return 0
            with patch.object(mod.post_check, 'main', side_effect=absent):
                self.assertEqual(mod.complete_record(record, root=self.root), 1)
            checked = json.loads(Path(str(record) + '.claims.json').read_text())
            self.assertEqual(checked['verification']['status'], 'unavailable')
            self.assertEqual(checked['verification']['counts']['UNCHECKED'], 2)
            self.assertTrue(all(r['verdict'] == 'UNCHECKED' for r in checked['claims']))

    def test_consumer_reads_checked_deliverable(self):
        doc = (TOOLS.parent / 'reference/sidecar_dispatch.md').read_text(encoding='utf-8')
        step = doc.split('7. **Consume:', 1)[1].split('A fan-out is', 1)[0]
        self.assertIn('<record>.claims.json', step)
        self.assertNotIn('last `result` event', step)

    def test_detached_publishes_checked_before_exit(self):
        import time
        import sidecar_launch
        launcher = self.root / 'launcher.sh'
        capture = self.root / 'capture.json'
        record = self.root / 'detached.json'
        capture.write_text(json.dumps({'type': 'result', 'result': {'claims': self.rows()}}))
        launcher.write_text('source "' + COMMON.as_posix() + '"\n'
                            'sc_check_duplicate_launch() { return 0; }\n'
                            'sc_reexec_snapshot "$@"\nsc_parse_flags "$@"\n'
                            'OUTPUT="$(<"$CAPTURE")"\nprintf "%s" "$OUTPUT"\n', newline='\n')
        env = dict(os.environ, CAPTURE=capture.as_posix())
        env.pop('SIDECAR_FANOUT_ID', None)
        run = subprocess.run([sidecar_launch.git_bash(), launcher.as_posix(), '-X', '-R', record.as_posix(),
                              '-d', self.root.as_posix()], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        deadline = time.monotonic() + 10
        while not Path(str(record) + '.exit').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(Path(str(record) + '.exit').exists(), 'detached completion did not land')
        self.assertTrue(Path(str(record) + '.claims.json').exists(), 'completion beat checked output')
        checked = json.loads(Path(str(record) + '.claims.json').read_text())
        self.assertEqual([r['verdict'] for r in checked['claims']], ['VERIFIED', 'UNVERIFIED'])
        self.assertEqual(Path(str(record) + '.out').read_text(), capture.read_text())

    def test_record_publication_follows_check(self):
        body = COMMON.read_text().split('sc_write_record() {', 1)[1]
        self.assertTrue('sc_check_completion' in body, 'record publishes before host check')
        self.assertLess(body.index('sc_check_completion'), body.index('RAW_V='))

    def test_shell_checks_before_exit_and_skips_children(self):
        self.module()
        for child in (False, True, 'nested'):
            record = self.root / ('shell' + str(child) + '.json')
            raw = self.raw(record)
            script = self.root / ('case' + str(child) + '.sh')
            script.write_text('source "$1"\nSC_RECORD="$2"\nSC_WORKDIR="$3"\n'
                              'SC_LAUNCH_ID=fixture\nOUTPUT="$(<"${SC_RECORD}.out")"\nsc_write_durable_files 0\n'
                              '[ -f "${SC_RECORD}.exit" ]\n'
                              + ('[ ! -f "${SC_RECORD}.claims.json" ]\n' if child is True else
                                 '[ -f "${SC_RECORD}.claims.json" ]\n'), newline='\n')
            env = dict(os.environ)
            env.pop('SIDECAR_FANOUT_ID', None)
            env.pop('SIDECAR_CHECK_OWNER', None)
            if child:
                env['SIDECAR_FANOUT_ID'] = 'owned-fanout'
                env['SIDECAR_CHECK_OWNER'] = 'fixture' if child is True else 'outer-launch'
            import sidecar_launch
            run = subprocess.run([sidecar_launch.git_bash(), script.as_posix(), COMMON.as_posix(), record.as_posix(), self.root.as_posix()],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(Path(str(record) + '.out').read_text(), raw)
            if child is not True:
                self.assertIn('VERIFIED=1 UNVERIFIED=1 UNCHECKED=0', run.stdout + run.stderr)


def live_record(record, progress):
    import hashlib
    record = Path(record)
    raw = Path(str(record) + '.out').read_bytes()
    assert raw == Path(progress).read_bytes().rstrip(b'\r\n'), 'raw capture differs from pre-check progress'
    checked_path = Path(str(record) + '.claims.json')
    checked = json.loads(checked_path.read_text())
    assert checked['verification']['counts'] == {'VERIFIED': 1, 'UNVERIFIED': 1, 'UNCHECKED': 0}
    assert [(c['verdict'], c['confidence']) for c in checked['claims']] == [
        ('VERIFIED', 'verified'), ('UNVERIFIED', 'unverified')]
    assert checked_path.stat().st_mtime_ns <= record.stat().st_mtime_ns
    assert checked_path.stat().st_mtime_ns <= Path(str(record) + '.exit').stat().st_mtime_ns
    assert Path(str(record) + '.post_check.json').is_file()
    print('PASS live direct: VERIFIED=1 UNVERIFIED=1 UNCHECKED=0; checked before record/exit; raw unchanged ' +
          hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--live-record':
        live_record(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
