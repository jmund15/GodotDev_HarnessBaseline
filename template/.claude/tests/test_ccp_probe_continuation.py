import importlib.util
import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ccp_probe_subject', ROOT / '.claude/scripts/lib/ccp_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SIDECAR = ROOT / '.claude/scripts/codex_proxy_sidecar.sh'


class ContinuationGateTests(unittest.TestCase):
    def test_boundary_versions(self):
        self.assertFalse(probe.continuation_supported('claude-code-proxy 0.1.35'))
        self.assertTrue(probe.continuation_supported('claude-code-proxy 0.1.36'))
        self.assertTrue(probe.continuation_supported('claude-code-proxy 0.1.39\n'))
        self.assertTrue(probe.continuation_supported('claude-code-proxy 0.2.0'))

    def test_server_compaction_boundary_versions(self):
        self.assertFalse(probe.server_compaction_supported('claude-code-proxy 0.1.38'))
        self.assertTrue(probe.server_compaction_supported('claude-code-proxy 0.1.39'))
        self.assertTrue(probe.server_compaction_supported('claude-code-proxy 0.2.0'))
        self.assertFalse(probe.server_compaction_supported(''))

    def test_numeric_not_lexical_comparison(self):
        self.assertTrue(probe.continuation_supported('claude-code-proxy 0.1.100'))
        self.assertFalse(probe.continuation_supported('claude-code-proxy 0.0.99'))

    def test_unknown_version_fails_closed(self):
        for text in ('', None, 'claude-code-proxy', 'error: no such file'):
            self.assertFalse(probe.continuation_supported(text), text)

    def test_g13_prerelease_or_unrelated_semver_does_not_enable_capabilities(self):
        for text in ('claude-code-proxy 0.1.39-rc1', 'wrapper 9.9.9 failed', 'error 1.2.3'):
            self.assertFalse(probe.continuation_supported(text), text)
            self.assertFalse(probe.server_compaction_supported(text), text)

    def test_g13_failed_version_command_discards_stdout(self):
        failed = subprocess.CompletedProcess(['proxy', '--version'], 1,
                                             stdout='claude-code-proxy 9.9.9\n', stderr='failed')
        with patch.object(probe.subprocess, 'run', return_value=failed):
            self.assertEqual(probe.proxy_version_text('proxy'), '')

    def run_main(self, version_text):
        out = io.StringIO()
        with patch.object(probe, 'proxy_version_text', return_value=version_text), redirect_stdout(out):
            rc = probe.main(['ccp_probe.py', 'continuation', 'proxy.exe'])
        return rc, out.getvalue().strip()

    def test_subcommand_prints_gate(self):
        self.assertEqual(self.run_main('claude-code-proxy 0.1.39'), (0, '1'))
        self.assertEqual(self.run_main('claude-code-proxy 0.1.35'), (0, '0'))
        self.assertEqual(self.run_main(''), (0, '0'))

    def test_server_compaction_subcommand_prints_gate(self):
        out = io.StringIO()
        with patch.object(probe, 'proxy_version_text', return_value='claude-code-proxy 0.1.39'), redirect_stdout(out):
            rc = probe.main(['ccp_probe.py', 'server-compaction', 'proxy.exe'])
        self.assertEqual((rc, out.getvalue().strip()), (0, '1'))

    def test_missing_binary_reads_as_unsupported(self):
        self.assertEqual(probe.proxy_version_text(str(ROOT / 'no-such-proxy.exe')), '')

    def test_subcommand_requires_binary(self):
        self.assertEqual(probe.main(['ccp_probe.py', 'continuation']), 2)

    def test_server_compaction_subcommand_requires_binary(self):
        self.assertEqual(probe.main(['ccp_probe.py', 'server-compaction']), 2)

    def test_g14_attest_rejects_missing_or_malformed_epoch_with_exit_2(self):
        for argv in (
            ['ccp_probe.py', 'attest'],
            ['ccp_probe.py', 'attest', 'not-an-epoch'],
            ['ccp_probe.py', 'attest', 'nan'],
        ):
            with self.subTest(argv=argv), patch('sys.stdin', io.StringIO('')):
                self.assertEqual(probe.main(argv), 2)

    def test_sidecar_passes_gate_to_server_env(self):
        text = SIDECAR.read_text(encoding='utf-8')

        def body(name):
            start = text.index(name + '() {')
            return text[start:text.index('\n}\n', start)]
        self.assertEqual(text.count('launch-facts --model'), 1, '--check and dispatch share one probe read')
        self.assertIn('"$CCP_PROBE" launch-facts --model "$SC_MODEL"', body('ccp_read_facts'))
        start = body('ccp_start')
        self.assertIn('CCP_CODEX_PREVIOUS_RESPONSE_ID="$CCP_CONTINUATION"', start)
        self.assertIn('CCP_CODEX_SERVER_COMPACTION="$CCP_SERVER_COMPACTION"', start)
        self.assertIn('predates 0.1.36', start)
        self.assertLess(start.index('ccp_read_facts'), start.index('nohup "$CCP_EXE" serve'))

    def test_g9_sidecar_finalizes_the_record_with_attested_effort(self):
        text = SIDECAR.read_text(encoding='utf-8')
        start = text.index('_record_ledger=')
        body = text[start:text.index('# `[claude-code:unrecognized_model]', start)]
        self.assertIn('SC_ATTESTED_EFFORT', text)
        self.assertIn('[ "$_record_ledger" = "__default__" ]', body)
        self.assertIn("SC_LEDGER=''", body)
        self.assertIn('SC_LEDGER="$_record_ledger"', body)
        self.assertIn('"$CCP_PROBE" record-effort', body)
        self.assertLess(body.index("SC_LEDGER=''"), body.index('sc_write_record'))
        self.assertLess(body.index('sc_write_record'), body.index('"$CCP_PROBE" record-effort'))


class LaunchFactsTests(unittest.TestCase):
    """One dispatch-start read: the binary, its model list, its version and a port in one process."""

    def facts(self, version, serves=(0, ''), exe='proxy.exe'):
        with patch.object(probe, 'proxy_version_text', return_value=version), \
                patch.object(probe, 'serves_check', return_value=serves), \
                patch.object(probe, 'freeport', return_value=4321):
            return probe.launch_facts(exe, 'model')

    def test_a_tested_build_carries_both_capabilities(self):
        self.assertEqual(self.facts('claude-code-proxy 0.1.39'), ['proxy.exe', 0, '', 4321, 1, 1])

    def test_an_old_build_serves_the_model_and_loses_both_capabilities(self):
        self.assertEqual(self.facts('claude-code-proxy 0.1.35'), ['proxy.exe', 0, '', 4321, 0, 0])

    def test_an_unserved_model_carries_its_reason(self):
        self.assertEqual(self.facts('claude-code-proxy 0.1.39', serves=(1, 'does not serve model'))[1:3],
                         [1, 'does not serve model'])

    def test_resolution_prefers_the_user_scope_value_unless_this_process_is_pinned(self):
        with patch.object(probe, 'user_scope_ccp_bin', return_value='C:/scope/proxy.exe'), \
                patch.dict('os.environ', {'CCP_BIN': 'C:/env/proxy.exe'}):
            self.assertEqual(probe.resolve_bin(), 'C:/scope/proxy.exe')
            self.assertEqual(probe.resolve_bin(scope_process=True), 'C:/env/proxy.exe')

    def test_resolution_falls_through_to_the_installed_path_then_path(self):
        with patch.object(probe, 'user_scope_ccp_bin', return_value=None), \
                patch.dict('os.environ', {}, clear=True), \
                patch.object(probe.os.path, 'expanduser', return_value='/nonexistent/proxy.exe'), \
                patch.object(probe.os, 'access', return_value=False), \
                patch.object(probe.shutil, 'which', return_value='/path/proxy.exe'):
            self.assertEqual(probe.resolve_bin(), '/path/proxy.exe')

    def test_the_cli_prints_the_six_fields(self):
        out = io.StringIO()
        argv = ['ccp_probe.py', 'launch-facts', '--bin', 'proxy.exe', '--model', 'model']
        with patch.object(probe, 'launch_facts', return_value=['proxy.exe', 0, '', 4321, 1, 1]), redirect_stdout(out):
            self.assertEqual(probe.main(argv), 0)
        self.assertEqual(out.getvalue().strip().split('\x1f'), ['proxy.exe', '0', '', '4321', '1', '1'])


    def test_no_installed_proxy_prints_an_empty_row(self):
        """--check reads an empty bin as 'not installed' (exit 4); the probe itself answers, not fails."""
        out = io.StringIO()
        with patch.object(probe, 'resolve_bin', return_value=None), redirect_stdout(out):
            self.assertEqual(probe.main(['ccp_probe.py', 'launch-facts', '--model', 'model']), 0)
        self.assertEqual(out.getvalue().rstrip('\r\n').split('\x1f'), [''] * 6)

    def test_a_bad_argument_refuses_with_its_shape(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            probe.main(['ccp_probe.py', 'launch-facts', '--bin', 'proxy.exe'])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn('example: ccp_probe.py launch-facts', err.getvalue())

if __name__ == '__main__':
    unittest.main(verbosity=2)
