#!/usr/bin/env python3
"""The C# logging rule binds C# files and exempts only `#if TOOLS` bodies, not any file that mentions the marker.

Violation samples are assembled from fragments so this proof's own source is never a violation.

    python3 .claude/tests/test_pattern_enforcer_logging_scope.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import pattern_enforcer as pe  # noqa: E402

PRINT_ERR = "GD" + ".PrintErr(\"x\");"
EDITOR_HELPER = "#if TOOLS\n    internal void Probe() { " + PRINT_ERR + " }\n#endif\n"
RUNTIME_CALL = "    void Run() { " + PRINT_ERR + " }\n"


def write(path, content):
    return pe.process({"tool_name": "Write", "tool_input": {"file_path": path, "content": content}})


def edit(path, old, new, replace_all=False):
    return pe.process({"tool_name": "Edit", "tool_input": {
        "file_path": path, "old_string": old, "new_string": new, "replace_all": replace_all}})


class LoggingScopeTests(unittest.TestCase):
    def test_s14_getnode_process_advises_on_write_and_edit_live_channels(self):
        content = 'public override void _Process(double dt) { Get' + 'Node<Node>("X"); }'
        for tool, field in (("Write", "content"), ("Edit", "new_string")):
            payload = {"tool_name": tool, "tool_input": {"file_path": "C:/repo/Spells/Probe.cs",
                       field: content, "old_string": "x"}}
            result = subprocess.run([sys.executable, pe.__file__], input=json.dumps(payload),
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            out = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertNotIn("permissionDecision", out)
            self.assertIn("each frame on a stable path", out["additionalContext"])

    def test_s14_getnode_note_survives_an_attribute_fix(self):
        content = ('[GlobalClass] public partial class P : Resource { '
                   'public override void _Process(double dt) { Get' + 'Node<Node>("X"); } }')
        result = write("C:/repo/Spells/Probe.cs", content)
        self.assertNotIn("block", result)
        self.assertIn("[GlobalClass, Tool]", result["updatedInput"]["content"])
        self.assertIn("each frame on a stable path", result["context"])

    def test_s14_lookup_outside_process_and_non_cs_quotes_stay_silent(self):
        self.assertIsNone(write("C:/repo/Spells/Probe.cs", 'void Ready() { Get' + 'Node<Node>("X"); }'))
        self.assertIsNone(write("C:/repo/notes.md", 'public override void _Process(double dt) { Get'
                                + 'Node<Node>("X"); }'))

    def test_markdown_and_python_may_quote_the_call(self):
        self.assertIsNone(write("C:/repo/docs/logging.md", "Avoid `" + PRINT_ERR + "` in code.\n"))
        self.assertIsNone(write("C:/repo/tools/fixture.py", "SAMPLE = '" + PRINT_ERR + "'\n"))

    def test_a_cs_file_is_still_blocked(self):
        result = write("C:/repo/Spells/Bad.cs", "class Bad {\n" + RUNTIME_CALL + "}\n")
        self.assertIn("Use JmoLogger", (result or {}).get("block", ""))

    def test_a_pathless_content_check_still_runs(self):
        blocked, _ = pe.check_code_patterns("class Bad {\n" + RUNTIME_CALL + "}\n")
        self.assertTrue(blocked)

    def test_a_tools_body_is_exempt(self):
        self.assertIsNone(write("C:/repo/Spells/Probe.cs", "class P {\n" + EDITOR_HELPER + "}\n"))

    def test_a_tools_marker_elsewhere_does_not_exempt_a_runtime_call(self):
        result = write("C:/repo/Spells/Bad.cs", "class Bad {\n" + EDITOR_HELPER + RUNTIME_CALL + "}\n")
        self.assertIn("Use JmoLogger", (result or {}).get("block", ""))

    def test_the_else_branch_of_a_tools_block_is_not_editor_only(self):
        body = "class Bad {\n#if TOOLS\n    void A() {}\n#else\n" + RUNTIME_CALL + "#endif\n}\n"
        self.assertIn("Use JmoLogger", (write("C:/repo/Spells/Bad.cs", body) or {}).get("block", ""))

    def test_a_tools_directory_file_outside_a_tools_marker_is_blocked(self):
        result = write("C:/repo/Jmodot/Tools/Probe.cs", "class P {\n" + RUNTIME_CALL + "}\n")
        self.assertIn("Use JmoLogger", (result or {}).get("block", ""))

    def test_a_tools_directory_file_inside_a_tools_marker_is_exempt(self):
        self.assertIsNone(write("C:/repo/Jmodot/Tools/Probe.cs", "class P {\n" + EDITOR_HELPER + "}\n"))

    def test_an_edit_is_exempt_only_where_it_replaces_editor_only_text(self):
        source = ("class Mixed {\n#if TOOLS\n    internal void Probe() { int a = 1; }\n#endif\n"
                  "    void Run() { int b = 2; }\n}\n")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, "Mixed.cs")
            Path(path).write_text(source, encoding="utf-8")
            inside = edit(path, "int a = 1;", PRINT_ERR)
            outside = edit(path, "int b = 2;", PRINT_ERR)
        self.assertIsNone(inside)
        self.assertIn("Use JmoLogger", (outside or {}).get("block", ""))

    def test_an_edit_that_runs_past_the_endif_is_not_editor_only(self):
        source = ("class Mixed {\n#if TOOLS\n    internal void Probe() { int a = 1; }\n#endif\n"
                  "    void Run() { int b = 2; }\n}\n")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, "Mixed.cs")
            Path(path).write_text(source, encoding="utf-8")
            crossing = edit(path, "int a = 1; }\n#endif\n    void Run() { int b = 2; }",
                            "int a = 1; }\n#endif\n    void Run() { " + PRINT_ERR + " }")
        self.assertIn("Use JmoLogger", (crossing or {}).get("block", ""))


if __name__ == "__main__":
    unittest.main()
