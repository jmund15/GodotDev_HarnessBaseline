#!/usr/bin/env python3
"""Executed review-preparation conformance cases."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import review_result
import dispatch_size
import lens_briefs
import extract_numbers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extract_numbers import mkrepo, write, _rm


class ReviewPreparation(unittest.TestCase):
    def test_grounding_carries_full_typed_behavior_artifact(self):
        tmp = tempfile.mkdtemp(prefix='grounding_j4_')
        try:
            doc = '.claude/rules/current.md'
            plan = '.claude/plans/proposal.md'
            sentence = 'The emitter prints accepted.'
            proof = '.claude/tests/test_emitter.py'
            proof_text = ('import sys, pathlib, unittest\n'
                          'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))\n'
                          'from emitter import emit\n'
                          'class Emit(unittest.TestCase):\n'
                          '    def test_accepted(self):\n'
                          '        self.assertEqual(emit(), "accepted")\n'
                          'unittest.main(verbosity=2)\n')
            repo = Path(mkrepo(tmp, 'repo', {
                doc: sentence + '\n',
                '.claude/tools/emitter.py': 'def emit():\n    return "accepted"\n',
                proof: proof_text,
                plan: '# Proposal\n\nThe future checker will reject empty inputs.\n\n'
                      '```surfaces\n' + json.dumps({'kind': 'path', 'surface': doc, 'change': 'rewrite'})
                      + '\n```\n',
            }, scope={'paths': ['.claude/rules/*.md'], 'rows_dir': '.claude/instruction_rows'}))
            row = {'path': doc, 'sentence': extract_numbers.norm(sentence),
                   'hash': extract_numbers.sentence_hash(extract_numbers.norm(sentence)),
                   'section': '__module__', 'class': 'behavior', 'source': 'author:J4',
                   'classificationQuote': sentence, 'claimId': 'emitter.accepted',
                   'implementation': [{'path': '.claude/tools/emitter.py', 'symbol': 'emit',
                                       'quote': 'def emit():'}], 'proofs': []}
            write(str(repo), '.claude/instruction_rows/behaviors.jsonl', json.dumps(row) + '\n')
            out = Path(tmp) / 'briefs'
            out.mkdir()
            section = lens_briefs.grounding_behavior_section(
                [('meta', str(repo / plan))], [], out=out, prefix='fixture', repo=repo)
            artifact = json.loads((out / 'fixture_behavior.json').read_text(encoding='utf-8'))
            self.assertEqual(artifact['claims'][0]['claimId'], 'emitter.accepted')
            self.assertEqual(artifact['claims'][0]['status'], 'unbound')
            self.assertEqual(artifact['scope']['paths'], [plan, doc])
            self.assertEqual(artifact['proposalSubjects'][0]['status'], 'pending')
            self.assertIn('fixture_behavior.json', section)
            self.assertIn('input/default/branch/output', section)
            self.assertIn('pending', section)
            self.assertEqual(lens_briefs.grounding_behavior_section(
                [('code', str(repo / plan))], [doc], out=out, prefix='code', repo=repo), '')
            self.assertFalse((out / 'code_behavior.json').exists())
            row['proofs'] = [{'path': proof, 'caseId': 'Emit.test_accepted',
                              'selector': {'type': 'unittest-case'},
                              'assertion': 'self.assertEqual(emit(), "accepted")'}]
            write(str(repo), '.claude/instruction_rows/behaviors.jsonl', json.dumps(row) + '\n')
            runner = extract_numbers._proof_runner()
            runs = Path(tmp) / 'observed.json'
            with mock.patch.object(runner, 'runs_path', return_value=runs), \
                    mock.patch.object(runner, 'PASSES_PATH', Path(tmp) / 'passes.json'):
                for expected, emitted, skip in [('pass', 'accepted', False), ('fail', 'WRONG', False),
                                                 ('pass', 'accepted', False), ('uncovered', 'accepted', True)]:
                    write(str(repo), '.claude/tools/emitter.py', f'def emit():\n    return "{emitted}"\n')
                    write(str(repo), proof, proof_text.replace('    def test_accepted',
                          '    @unittest.skip("runtime unavailable")\n    def test_accepted') if skip else proof_text)
                    # Prevent Python's timestamp/size bytecode reuse from hiding a restored branch.
                    import shutil
                    shutil.rmtree(repo / '.claude/tools/__pycache__', ignore_errors=True)
                    run = subprocess.run([sys.executable, str(repo / proof)], capture_output=True,
                                         text=True, encoding='utf-8')
                    observed = 'skip' if skip else 'pass' if run.returncode == 0 else 'fail'
                    print(f'grounding replay: expected={expected} observed={observed}\n{run.stdout}{run.stderr}')
                    self.assertIn('skipped' if skip else 'OK' if expected == 'pass' else 'FAILED', run.stderr)
                    descriptor = extract_numbers.behavior_proofs(repo, [doc])['cases'][proof][0]
                    runs.write_text(json.dumps({'observations': [{'proof': proof,
                        'caseId': 'Emit.test_accepted', 'status': observed, 'inputKey': descriptor['inputKey'],
                        'evidence': run.stdout + run.stderr}]}), encoding='utf-8')
                    lens_briefs.grounding_behavior_section([('meta', str(repo / plan))], [],
                                                          out=out, prefix='fixture', repo=repo)
                    current = json.loads((out / 'fixture_behavior.json').read_text(encoding='utf-8'))
                    self.assertEqual(current['claims'][0]['status'], expected, current)
        finally:
            _rm(tmp)

    def test_grounding_artifact_counts_unchanged_unclassified_units_per_path(self):
        """Before the census every unit is unclassified, and listing each one made the grounding seat's
        must-read 6 MB for one plan (2026-10-07). An unchanged unclassified unit is a per-path count;
        a classified or changed unit stays a full row."""
        tmp = tempfile.mkdtemp(prefix='grounding_count_')
        try:
            doc = '.claude/rules/current.md'
            plan = '.claude/plans/proposal.md'
            sentence = 'The emitter prints accepted.'
            repo = Path(mkrepo(tmp, 'repo', {
                doc: sentence + '\n\nThe runner retries once.\n\nThe gate writes a stamp.\n',
                '.claude/tools/emitter.py': 'def emit():\n    return "accepted"\n',
                plan: '# Proposal\n\n```surfaces\n' + json.dumps({'kind': 'path', 'surface': doc, 'change': 'rewrite'})
                      + '\n```\n',
            }, scope={'paths': ['.claude/rules/*.md'], 'rows_dir': '.claude/instruction_rows'}))
            row = {'path': doc, 'sentence': extract_numbers.norm(sentence),
                   'hash': extract_numbers.sentence_hash(extract_numbers.norm(sentence)),
                   'section': '__module__', 'class': 'behavior', 'source': 'author:J4',
                   'classificationQuote': sentence, 'claimId': 'emitter.accepted',
                   'implementation': [{'path': '.claude/tools/emitter.py', 'symbol': 'emit',
                                       'quote': 'def emit():'}], 'proofs': []}
            write(str(repo), '.claude/instruction_rows/behaviors.jsonl', json.dumps(row) + '\n')
            out = Path(tmp) / 'briefs'
            out.mkdir()
            lens_briefs.grounding_behavior_section([('meta', str(repo / plan))], [], out=out, prefix='c', repo=repo)
            artifact = json.loads((out / 'c_behavior.json').read_text(encoding='utf-8'))
            listed = [u['sentence'] for u in artifact['units'] if u['path'] == doc]
            self.assertEqual(listed, [extract_numbers.norm(sentence)], artifact['units'])
            self.assertEqual(artifact['unclassifiedByPath'][doc], 2, artifact.get('unclassifiedByPath'))
            self.assertFalse([u for u in artifact['uncovered'] if u.get('reason') == 'unclassified unit'
                              and not u.get('changed')], artifact['uncovered'])
            self.assertFalse([u for u in artifact['scope'].get('initialDebt') or [] if u.get('class') is None],
                             artifact['scope'].get('initialDebt'))
            self.assertEqual(artifact['claims'][0]['claimId'], 'emitter.accepted')
        finally:
            _rm(tmp)

    def test_native_fold_preparation(self):
        proof = Path(__file__).with_name('review_fanout_spill_test.js')
        result = subprocess.run(['node', str(proof)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ok   native fold has a stable promptPath', result.stdout)

    def test_unicode_reading_uses_decoded_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = '漢🙂é' * 1000
            (root / 'subject.md').write_text(text, encoding='utf-8')
            for job in ({'prompt': text}, {'mustRead': [{'path': 'subject.md', 'lines': 1}]},
                        {'contextFiles': ['subject.md']}):
                with self.subTest(job=list(job)):
                    row = dispatch_size.dispatch_size([job], root)['perJob'][0]
                    self.assertEqual(row['tokens'], 1200)
                    self.assertEqual(row['bytes'], len(text.encode('utf-8')))
                    self.assertEqual(row['characters'], len(text))
            row = dispatch_size.dispatch_size([{'prompt': '`subject.md`'}], root)['perJob'][0]
            self.assertEqual(row['namedCharacters'], len(text))
            self.assertEqual(row['namedKb'], round(len(text.encode('utf-8')) / 1024))
            context = root / 'context.md'
            context.write_text('', encoding='utf-8')
            with mock.patch.object(dispatch_size.model_registry, 'resolve', return_value={}), \
                    mock.patch.object(dispatch_size.model_registry, 'compaction_point', return_value=(1300, 'fixture')):
                arguments = dict(key='unicode', token='fixture', body='', context_path=str(context),
                                 entries=[{'path': str(root / 'subject.md'), 'lines': 1}], data={})
                self.assertIsNone(lens_briefs.seat_overage(**arguments))
                arguments['body'] = 'x' * 3000
                self.assertIn('over by', lens_briefs.seat_overage(**arguments))

    def test_sidecar_requires_must_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = Path(tmp) / 'context.md'
            context.write_text('subject\n', encoding='utf-8')
            job = {'label': 'seat', 'shape': 'review', 'mandates': ['lens'],
                   'contextFiles': [str(context)]}
            for value in ('omitted', None, [], [{'path': 'subject', 'lines': True}]):
                candidate = dict(job)
                if value != 'omitted':
                    candidate['mustRead'] = value
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'mustRead.*non-empty'):
                    review_result.validate_review_jobs([candidate])
            job['mustRead'] = [{'path': 'subject', 'lines': 0}]
            review_result.validate_review_jobs([job])


if __name__ == '__main__':
    unittest.main()
