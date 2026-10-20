"""Unit proof for tools/cli_usage.py, which tracked tools import. The repo-wide --help lint lives in
test_cli_usage.py beside the tool migration that satisfies it."""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".claude" / "tools"
spec = importlib.util.spec_from_file_location("cli_usage", TOOLS / "cli_usage.py")
cli_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_usage)


class CliUsageTests(unittest.TestCase):
    def test_usage_block_with_internal_blank_lines(self):
        self.assertEqual(cli_usage.usage("Summary\n\nUsage:\n  tool x\n\n  tool y\n\nDetails\ntext"),
                         "Summary\n\nUsage:\n  tool x\n\n  tool y")

    def test_indented_usage_stops_at_next_section(self):
        self.assertEqual(cli_usage.usage("Summary\n\n  Usage:\n    tool x\n  Options:\n    --x"),
                         "Summary\n\n  Usage:\n    tool x")

    def test_cli_and_subcommands_headings(self):
        self.assertEqual(cli_usage.usage("Summary\n\nCLI:\n  tool --help\n\nDetails"),
                         "Summary\n\nCLI:\n  tool --help")
        self.assertEqual(cli_usage.usage("Summary\n\nSubcommands:\n  tool run\nNotes"),
                         "Summary\n\nSubcommands:\n  tool run")

    def test_no_usage_is_summary(self):
        self.assertEqual(cli_usage.usage("Summary\nDetails"), "Summary")

    def test_empty_doc(self):
        self.assertEqual(cli_usage.usage(None), "")
        self.assertEqual(cli_usage.usage(""), "")

if __name__ == "__main__":
    unittest.main()
