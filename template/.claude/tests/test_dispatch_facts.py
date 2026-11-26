#!/usr/bin/env python3
"""dispatch_facts proof: the facts a launcher's `--check` prints (pin, brief size, named files, tier, ladder row,
shape rating), on the launch fixture `test_sidecar_common_registration.py` plants."""
import json
import os
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sidecar_common_registration import BASH, CLAUDE, LAUNCHER, ROOT, LaunchFixture, posix  # noqa: E402
import dispatch_facts  # noqa: E402


class DispatchFacts(LaunchFixture):
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
        # dispatch_size.format_line owns the sizing summary: a named file is reported as named, never as read.
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

    def test_check_no_shape_rating_and_unreadable_brief(self):
        self.assertIn('shape rating: none exists', self.facts())
        self.brief.unlink()
        missing = self.facts()
        self.assertIn('brief: unreadable', missing)
        self.assertNotIn('brief: 0 bytes', missing)


def main():
    from _case_pool import run_cases, unittest_cases
    return run_cases(unittest_cases(DispatchFacts))


if __name__ == '__main__':
    sys.exit(main())
