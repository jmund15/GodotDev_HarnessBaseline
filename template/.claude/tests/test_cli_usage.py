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

    def test_refusal_states_the_shape_and_one_valid_call(self):
        self.assertEqual(cli_usage.refusal("PID... --reason X", "tool.py 7 --reason X"),
                         "accepts: PID... --reason X\nexample: tool.py 7 --reason X\n")

    def test_repo_lint_and_positive_control(self):
        import ast

        def raw_doc(node):
            if isinstance(node, ast.Name):
                return node.id == "__doc__"
            return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "strip" and not node.args and raw_doc(node.func.value))

        def violations(paths):
            found = []
            for path in paths:
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        name = (node.func.id if isinstance(node.func, ast.Name) else
                                node.func.attr if isinstance(node.func, ast.Attribute) else "")
                        if name in {"print", "exit", "SystemExit", "write"} and node.args and raw_doc(node.args[0]):
                            found.append(str(path))
                            break
                        if any(keyword.arg == "description" and raw_doc(keyword.value)
                               for keyword in node.keywords):
                            found.append(str(path))
                            break
            return found

        source = [p for folder in ("tools", "scripts", "hooks")
                  for p in (ROOT / ".claude" / folder).rglob("*.py")]
        self.assertEqual(violations(source), [])
        with tempfile.TemporaryDirectory() as temp:
            planted = pathlib.Path(temp) / "bad.py"
            planted.write_text("print(__doc__)\n", encoding="utf-8")
            self.assertEqual(violations([planted]), [str(planted)])


if __name__ == "__main__":
    unittest.main()
