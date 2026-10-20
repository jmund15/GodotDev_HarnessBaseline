"""Executable regressions for mandate reporting and actual lens sampling."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import orchestration_metrics as metrics


class MetricsConformance(unittest.TestCase):
    def test_single_mandate_yield(self):
        rows = [dict(run=str(i), label='review:one', mandates={'one': {'outcome': 'clean', 'unique': 0}})
                for i in range(metrics.PANEL_YIELD_WINDOW)]
        self.assertEqual(metrics.panel_yield(rows)[0], ['one'])

    def test_all_uncovered_are_visible(self):
        rows = [dict(run='run', label='seat', mandates={f'm{i}': {'outcome': 'not-reached'}
                                                     for i in range(15)})]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            metrics.report_panel_yield(rows)
        for i in range(15):
            self.assertIn(f'UNCOVERED m{i} in run:seat', output.getvalue())

    def test_run_reports_journal_or_unavailable(self):
        row = dict(run='run', workflow='review-fanout', phase='Review', label='review:one', effort='medium')
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root, patch.object(metrics, 'run_rows', return_value=[row]), \
                patch.object(metrics, 'superseded_line', return_value=None), contextlib.redirect_stdout(output):
            self.assertEqual(metrics.report_run(root, 'run'), 0)
        self.assertIn('Seat overlap run:', output.getvalue())
        self.assertRegex(output.getvalue(), 'unavailable|unreadable|unmeasured')

    def test_auxiliary_never_samples(self):
        for workflow, phase in [('review-fanout', 'Merge'), ('review-fanout', 'Verify'),
                                ('review-fanout', 'Fold'), ('explore-fanout', 'Consolidate'),
                                ('explore-fanout', 'Verify'), ('explore-fanout', '')]:
            row = dict(run='run', workflow=workflow, phase=phase, label='aux', model='sonnet', effort='medium')
            with patch.object(metrics, 'sample_kept', return_value=False):
                self.assertFalse(metrics.sampled_out(row, {metrics._group_key(row)}), (workflow, phase))

    def test_sidecar_only_mandates_are_reported(self):
        row = dict(source='sidecar', run='sidecar-run', label='one', mandates={'one': {'outcome': 'not-reached'}})
        output = io.StringIO()
        with patch.object(metrics, 'report_sidecar'), contextlib.redirect_stdout(output):
            metrics.report([row])
        self.assertIn('UNCOVERED one in sidecar-run:one', output.getvalue())

    def test_report_modes_preserve_complete_yield(self):
        row = dict(run='run', workflow='review-fanout', phase='Review', label='review:one',
                   effort='medium', model='sonnet', mandates={f'm{i}': {'outcome': 'not-reached'} for i in range(15)})
        for args in [['--all-time'], ['--archive-summary'],
                     ['--session', 'fixture', '--sidecar-all']]:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as root:
                output = io.StringIO()
                with patch.object(sys, 'argv', ['metrics'] + args), \
                        patch.object(metrics, 'collect_all_workflows', return_value=[dict(row)]), \
                        patch.object(metrics, 'collect', return_value=[dict(row)]), \
                        patch.object(metrics, 'collect_sidecar', return_value=[]), \
                        patch.object(metrics, 'find_session_dir', return_value=root), \
                        patch.object(metrics, 'session_seat_overlaps', return_value={'run': {'unmeasured': 'fixture'}}), \
                        patch.object(metrics, '_archive_paths', return_value=['fixture']), \
                        patch.object(metrics, 'archive_rows', return_value=([dict(row)], [], 0)), \
                        patch.object(metrics, '_archive_records', return_value=iter([])), \
                        patch.object(metrics, 'load_pending_verdicts', return_value={}), \
                        patch.object(metrics, 'load_review_ledger', return_value=[]), \
                        patch.object(metrics, '_session_of_run', return_value={}), \
                        patch.object(metrics, '_report_candidates'), \
                        patch.object(metrics, '_report_pending_candidates'), contextlib.redirect_stdout(output):
                    self.assertEqual(metrics.main(), 0)
                self.assertIn('UNCOVERED m14 in run:review:one', output.getvalue())
                self.assertIn('Seat overlap run:', output.getvalue())

    def test_auxiliary_history_cannot_saturate_lens(self):
        row = dict(workflow='review-fanout', phase='Merge', label='aux', model='sonnet', effort='medium', outcome='clean')
        self.assertEqual(metrics.saturated_groups([dict(row) for _ in range(metrics.SATURATION_N)]), set())
        row['phase'] = 'Review'
        self.assertIn(metrics._group_key(row), metrics.saturated_groups([dict(row) for _ in range(metrics.SATURATION_N)]))

    def test_apply_verdict_copies_measurement(self):
        row = dict(run='run', label='arm')
        metrics.apply_verdict(row, {'outcome': 'clean', 'measurement': True})
        self.assertIs(row.get('measurement'), True)
        bare = dict(run='run', label='arm')
        metrics.apply_verdict(bare, {'outcome': 'clean'})
        self.assertNotIn('measurement', bare)

    def test_agent_base_flags_comparison_arm(self):
        entry = dict(label='arm:a', model='proof-a', state='completed')
        arm = metrics._agent_base('wf', dict(workflowName='review-fanout', args={'measurement': 'pin-ab-r1'}),
                                  entry, {})
        self.assertIs(arm.get('measurement'), True)
        for args in [{'measurement': ''}, {}, None]:
            with self.subTest(args=args):
                row = metrics._agent_base('wf', dict(workflowName='review-fanout', args=args), entry, {})
                self.assertNotIn('measurement', row)

    def test_agent_base_instrument_run_is_not_a_measurement_arm(self):
        entry = dict(label='arm:a', model='proof-a', state='completed')
        instrument = next(iter(metrics.MEASUREMENT_WORKFLOWS))
        row = metrics._agent_base('wf', dict(workflowName=instrument, args={'measurement': 'pin-ab-r1'}), entry, {})
        self.assertNotIn('measurement', row)

    def test_actual_lenses_still_sample(self):
        for workflow, phase in [('review-fanout', 'Review'), ('explore-fanout', 'Explore')]:
            row = dict(run='run', workflow=workflow, phase=phase, label='lens', model='sonnet', effort='medium')
            with patch.object(metrics, 'sample_kept', return_value=False):
                self.assertTrue(metrics.sampled_out(row, {metrics._group_key(row)}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
