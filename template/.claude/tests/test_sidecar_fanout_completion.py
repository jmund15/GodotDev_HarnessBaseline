#!/usr/bin/env python3
"""Aggregate once; preserve each producing seat's source key."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sidecar_fanout as sf
import verify_claims


class FanoutCompletion(unittest.TestCase):
    def test_inventory_states_whether_main_runs_after_its_seats(self):
        from contextlib import ExitStack

        class InventoryWritten(Exception):
            pass

        real_run = sf.run_jobs
        for mode in ('author', 'review', 'fold'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                jobs = root / 'jobs.json'
                jobs.write_text('[{"label":"seat"}]')
                template = root / 'fold.md'
                template.write_text('fold')
                out = root / 'out'
                subject = SimpleNamespace(root=root, files=['fold.md'])
                observed = []

                def launch(planned, parallel, directory, **kwargs):
                    inert = [('seat', [sys.executable, '-c', 'pass', '-R',
                                      str(out / 'seat.record.json')], str(out / 'seat.out.json'))]
                    real_run(inert, parallel, directory, **kwargs)
                    observed.extend(json.loads(path.read_text())
                                    for path in out.glob('fanout-*.inventory.json'))
                    raise InventoryWritten

                with ExitStack() as stack:
                    for owner, name, value in [
                        (sf.model_registry, 'load', {}),
                        (sf, 'normalize_job', {'label': 'seat'}),
                        (sf, 'plan_job', ('seat', ['unused'], str(out / 'seat.out.json'))),
                        (sf, 'add_concurrency_guard', [('seat', ['unused'], str(out / 'seat.out.json'))]),
                        (sf.review_result, 'validate_review_jobs', None),
                        (sf.fold_check, 'parse_subject_spec', subject),
                        (sf, 'fold_probe', {}),
                    ]:
                        stack.enter_context(patch.object(owner, name, return_value=value))
                    stack.enter_context(patch.object(sf, 'run_jobs', side_effect=launch))
                    args = [str(jobs), '--out-dir', str(out)]
                    if mode != 'author':
                        args += ['--review']
                    if mode == 'fold':
                        args += ['--fold', str(template), '--fold-alias', 'any',
                                 '--fold-effort', 'medium', '--fold-subject', 'unused']
                    with self.assertRaises(InventoryWritten):
                        sf.main(args)
                self.assertEqual(len(observed), 1)
                self.assertIs(observed[0].get('afterSeats'), mode != 'author', observed)

    def test_inventory_rewrite_keeps_fold_flag(self):
        for folds in (False, True):
            with self.subTest(after_seats=folds), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                planned = [(label, [sys.executable, '-c', 'raise SystemExit(1)', '-R',
                                    str(root / (label + '.record.json'))],
                            str(root / (label + '.out.json'))) for label in ('first', 'later')]
                sf.run_jobs(planned, 1, root, lanes=[('chain', ['first', 'later'])], after_seats=folds)
                paths = list(root.glob('fanout-*.inventory.json'))
                self.assertEqual(len(paths), 1)
                data = json.loads(paths[0].read_text())
                self.assertIs(data['afterSeats'], folds)
                self.assertEqual([job['label'] for job in data['jobs']], ['first'])

    def test_inventory_creation_stamp_survives_a_rewrite(self):
        """`run_jobs` stamps `writtenAt` on the inventory it creates, and a later `save_inventory` (here the
        lane stopping after its first job) keeps it: the watcher times an attempt by this field, not the file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seen = root / 'seen.json'
            capture = root / 'capture.py'
            # The child holds the lane one second past the stamp, so a save that re-stamped would differ.
            capture.write_text('import glob, json, sys, time\n'
                               'doc = json.load(open(glob.glob(sys.argv[1] + "/fanout-*.inventory.json")[0]))\n'
                               'json.dump(doc, open(sys.argv[2], "w"))\n'
                               'time.sleep(1.1)\n'
                               'raise SystemExit(1)\n', encoding='utf-8')
            planned = [(label, [sys.executable, str(capture), str(root), str(seen), '-R',
                                str(root / (label + '.record.json'))], str(root / (label + '.out.json')))
                       for label in ('first', 'later')]
            sf.run_jobs(planned, 1, root, lanes=[('chain', ['first', 'later'])], after_seats=False)
            path = next(root.glob('fanout-*.inventory.json'))
            created = json.loads(seen.read_text(encoding='utf-8'))
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual([job['label'] for job in saved['jobs']], ['first'], 'the lane did not rewrite')
            self.assertIn('writtenAt', created)
            created_at = datetime.fromisoformat(created['writtenAt'].replace('Z', '+00:00')).timestamp()
            self.assertGreaterEqual(os.path.getmtime(path) - created_at, 1,
                                    'the rewrite landed inside the stamp second, so a re-stamp would read equal')
            self.assertEqual(saved['writtenAt'], created['writtenAt'])

    def test_claims_once_root_keys_and_failure(self):
        self.assertTrue(hasattr(sf, 'close_claims'), 'claims fan-out has no in-process close')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subject = root / 'subject'
            subject.mkdir()
            (subject / 'e.txt').write_text('genuine source')
            for label, quote in [('z', 'genuine source'), ('a', 'fabricated source')]:
                (root / (label + '.out.json')).write_text('{}')
                (root / (label + '.md')).write_text(json.dumps({'claims': [
                    {'id': 'C1', 'evidence': [{'file': 'e.txt', 'quote': quote},
                     {'command': 'git status --short', 'output': ' M e.txt'}]}]}))
            with patch.object(verify_claims, '_run_git', return_value=(0, ' M e.txt', '')) as run:
                records = {label: {'record': str(root / (label + '.record.json'))} for label in ('a', 'z')}
                self.assertEqual(sf.close_claims(SimpleNamespace(root=str(subject)), root, results=records), 1)
                self.assertTrue(Path(records['z']['record'] + '.claims.json').is_file(), 'Stop has no checked child deliverable')
                self.assertEqual(run.call_count, 2)  # once per command-evidence item, never once per child + host
                self.assertTrue(all(call.args[1] == subject for call in run.call_args_list))
            payload = json.loads((root / 'claims.json').read_text())
            self.assertEqual([(c['sourceKey'], c['verdict']) for c in payload['claims']],
                             [('a#1', 'UNVERIFIED'), ('z#1', 'VERIFIED')])
            self.assertTrue((root / 'post_check_claims.json').is_file())

    def test_unchecked_subset_does_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label in ('unchecked', 'verified'):
                (root / (label + '.out.json')).write_text('{}')
                evidence = [] if label == 'unchecked' else [{'file': str(root / 'source'), 'quote': 'real'}]
                (root / (label + '.md')).write_text(json.dumps({'claims': [{'evidence': evidence}]}))
            (root / 'source').write_text('real')
            records = {label: {'record': str(root / (label + '.json'))} for label in ('unchecked', 'verified')}
            self.assertEqual(sf.close_claims(SimpleNamespace(root=tmp), root, results=records), 1)
            bad = json.loads(Path(records['unchecked']['record'] + '.claims.json').read_text())
            good = json.loads(Path(records['verified']['record'] + '.claims.json').read_text())
            self.assertEqual(bad['verification']['status'], 'fail')
            self.assertEqual(good['verification']['status'], 'pass')

    def test_claims_close_judges_only_its_own_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'mine.out.json').write_text('{}')
            (root / 'mine.md').write_text('no claims block')
            (root / 'other.out.json').write_text('')
            records = {'mine': {'record': str(root / 'mine.record.json')}}
            self.assertEqual(sf.close_claims(SimpleNamespace(root=tmp), root, results=records), 0)
            self.assertFalse(Path(records['mine']['record'] + '.post_check.json').exists())

    def test_missing_report_never_passes(self):
        self.assertTrue(hasattr(sf, 'close_claims'), 'claims fan-out has no in-process close')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'seat.out.json').write_text('{}')
            (root / 'seat.md').write_text('```claims\n{"evidence": []}\n```')
            with patch.object(sf.post_check, 'main', return_value=0):
                self.assertEqual(sf.close_claims(SimpleNamespace(root=tmp), root), 1)
            body = json.loads((root / 'claims.json').read_text())
            self.assertEqual(body['verification']['status'], 'unavailable')
            self.assertEqual(body['claims'][0]['verdict'], 'UNCHECKED')

    def test_review_command_once_and_applied(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_sidecar_review_cli as fixture
        case = fixture.SidecarReviewCliTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = fixture.reply
        def reply(description):
            body = original(description)
            body['structured_output']['findings'][0]['evidence'] = [
                {'command': 'git status --short', 'output': ' M evidence.txt'}]
            return body
        with patch.object(fixture, 'reply', side_effect=reply), \
             patch.object(verify_claims, '_run_git', return_value=(0, ' M evidence.txt', '')) as check:
            code, review = case.run_review()
            self.assertEqual(code, 0, case.printed + json.dumps(review))
            self.assertEqual(check.call_count, 1, 'review checks command evidence more than once')
        self.assertEqual(review['view'][0]['verdict'], 'VERIFIED')
        self.assertEqual(review['verification']['counts']['VERIFIED'], 1)
        child = case.out / 'one.record.json.claims.json'
        self.assertTrue(child.is_file(), 'review child has no checked Stop deliverable')
        self.assertEqual(json.loads(child.read_text())['claims'][0]['sourceKey'], 'one#1')

    def test_children_are_host_check_exempt(self):
        source = Path(sf.__file__).read_text()
        self.assertIn('SIDECAR_FANOUT_ID=fanout_id', source)
        common = Path(sf.__file__).parents[1] / 'scripts/lib/sidecar_common.sh'
        self.assertIn('SIDECAR_CHECK_OWNER=launch_id', source)
        self.assertIn('[ "$SIDECAR_CHECK_OWNER" = "${SC_LAUNCH_ID:-}" ]', common.read_text())


if __name__ == '__main__':
    unittest.main()
