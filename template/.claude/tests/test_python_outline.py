import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
TOOL = CLAUDE_DIR / "tools" / "python_outline.py"


class PythonOutlineTests(unittest.TestCase):
    def run_tool(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL), *map(str, args)],
            text=True,
            capture_output=True,
        )

    def test_nested_async_decorated_and_multiline_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(
                "@outer\n@inner(1)\nasync def work(\n    value: int,\n) -> None:\n"
                "    class Nested:\n        def method(self):\n            pass\n",
                encoding="utf-8",
            )
            result = self.run_tool(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            # decl_line is the `def` line; the range starts at the first decorator.
            self.assertIn(f"{path}:3 function work [1:8]", result.stdout)
            self.assertIn(f"{path}:6 class work.Nested [6:8]", result.stdout)
            self.assertIn(f"{path}:7 function work.Nested.method [7:8]", result.stdout)

    def test_definitions_inside_try_star_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trystar.py"
            path.write_text(
                "try:\n    def body():\n        pass\nexcept* ValueError:\n"
                "    def handler():\n        pass\n",
                encoding="utf-8",
            )
            result = self.run_tool(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"{path}:2 function body [2:3]", result.stdout)
            self.assertIn(f"{path}:5 function handler [5:6]", result.stdout)

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.py"
            path.write_text("", encoding="utf-8")
            result = self.run_tool(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_non_utf8_source_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad_encoding.py"
            path.write_bytes(b"def ok():\n    pass\n\xff")
            result = self.run_tool(path, "--json")
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload[0]["path"], str(path))
            self.assertTrue(payload[0]["error"])

    def test_syntax_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.py"
            path.write_text("def broken(:\n", encoding="utf-8")
            result = self.run_tool(path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(path), result.stderr)

    def test_missing_path_does_not_hide_good_path_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.py"
            good.write_text("def present():\n    pass\n", encoding="utf-8")
            missing = Path(directory) / "missing.py"
            result = self.run_tool(missing, good)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"{good}:1 function present [1:2]", result.stdout)
            self.assertIn(str(missing), result.stderr)

    def test_json_shape_has_one_entry_per_input_and_error(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.py"
            good.write_text("def present():\n    pass\n", encoding="utf-8")
            missing = Path(directory) / "missing.py"
            result = self.run_tool(good, missing, "--json")
            self.assertNotEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertEqual([row["path"] for row in data], [str(good), str(missing)])
            self.assertEqual(data[0]["error"], None)
            self.assertIsNone(data[1]["rows"])
            self.assertTrue(data[1]["error"])

    def test_module_import_is_not_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "side_effect.py"
            marker = Path(directory) / "ran.txt"
            path.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
                "def declared():\n    pass\n",
                encoding="utf-8",
            )
            result = self.run_tool(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn("function declared", result.stdout)


if __name__ == "__main__":
    unittest.main()
