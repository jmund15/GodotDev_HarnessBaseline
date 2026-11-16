import importlib.util
import io
import subprocess
import unittest
from contextlib import redirect_stdout
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
        start = text.index('ccp_start() {')
        body = text[start:text.index('\n}\n', start)]
        self.assertIn('"$CCP_PROBE" continuation "$ccp"', body)
        self.assertIn('"$CCP_PROBE" server-compaction "$ccp"', body)
        self.assertIn('CCP_CODEX_PREVIOUS_RESPONSE_ID="$continuation"', body)
        self.assertIn('CCP_CODEX_SERVER_COMPACTION="$server_compaction"', body)
        self.assertIn('continuation capability probe failed', body)
        self.assertIn('server-compaction capability probe failed', body)
        self.assertLess(body.index('continuation "$ccp"'), body.index('nohup "$ccp" serve'))

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
