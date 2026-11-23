"""Unit proof for tools/cli_usage.py, which tracked tools import. The repo-wide --help lint lives in
test_cli_usage.py beside the tool migration that satisfies it."""
import importlib.util
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".claude" / "tools"
spec = importlib.util.spec_from_file_location("cli_usage", TOOLS / "cli_usage.py")
cli_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli_usage)


def test_usage_block_with_internal_blank_lines():
    assert cli_usage.usage("Summary\n\nUsage:\n  tool x\n\n  tool y\n\nDetails\ntext") == "Summary\n\nUsage:\n  tool x\n\n  tool y"

def test_indented_usage_stops_at_next_section():
    assert cli_usage.usage("Summary\n\n  Usage:\n    tool x\n  Options:\n    --x") == "Summary\n\n  Usage:\n    tool x"

def test_cli_and_subcommands_headings():
    assert cli_usage.usage("Summary\n\nCLI:\n  tool --help\n\nDetails") == "Summary\n\nCLI:\n  tool --help"
    assert cli_usage.usage("Summary\n\nSubcommands:\n  tool run\nNotes") == "Summary\n\nSubcommands:\n  tool run"

def test_no_usage_is_summary():
    assert cli_usage.usage("Summary\nDetails") == "Summary"

def test_empty_doc():
    assert cli_usage.usage(None) == ""
    assert cli_usage.usage("") == ""

def test_refusal_states_the_shape_and_one_valid_call():
    assert cli_usage.refusal("PID... --reason X", "tool.py 7 --reason X") == "accepts: PID... --reason X\nexample: tool.py 7 --reason X\n"
