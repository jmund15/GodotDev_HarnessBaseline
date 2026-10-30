"""wake_digest.sidecar_lines names an amendment a fan-out job never read (`orchestration` §4)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import wake_digest  # noqa: E402

LABEL = "job-a"


def read_events(path, ok):
    use = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_a", "name": "Read", "input": {"file_path": path}}]}}
    res = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_a", "is_error": not ok,
         "content": "1\tamended" if ok else "File does not exist."}]}}
    return json.dumps(use) + "\n" + json.dumps(res) + "\n"


class SidecarLinesAmend(unittest.TestCase):
    def lines(self, read_ok, amend=True, brief_given=True):
        with tempfile.TemporaryDirectory() as out:
            brief = os.path.join(out, "brief.md")
            with open(brief, "w", encoding="utf-8") as fh:
                fh.write("brief\n")
            if amend:
                with open(brief + ".amend.md", "w", encoding="utf-8") as fh:
                    fh.write("amendment\n")
            record = os.path.join(out, LABEL + ".record.json")
            with open(record, "w", encoding="utf-8") as fh:
                json.dump({"label": LABEL}, fh)
            with open(record + ".pid", "w", encoding="utf-8") as fh:
                fh.write("pid=1\n")
            with open(os.path.join(out, LABEL + ".md"), "w", encoding="utf-8") as fh:
                fh.write("done\n")
            with open(os.path.join(out, LABEL + ".out.json"), "w", encoding="utf-8") as fh:
                fh.write(read_events(brief + ".amend.md", read_ok) + '{"type":"result","result":"done"}\n')
            return "\n".join(wake_digest.sidecar_lines(LABEL, record, out, brief if brief_given else None))

    def test_an_amendment_whose_only_read_errored_is_named(self):
        text = self.lines(read_ok=False)
        self.assertIn("AMEND UNREAD", text)
        self.assertIn("brief.md.amend.md: the job never read this amendment; its work follows the brief alone", text)

    def test_a_read_amendment_adds_no_line(self):
        self.assertNotIn("AMEND UNREAD", self.lines(read_ok=True))

    def test_no_amend_file_adds_no_line(self):
        self.assertNotIn("AMEND UNREAD", self.lines(read_ok=False, amend=False))

    def test_no_brief_adds_no_line(self):
        self.assertNotIn("AMEND UNREAD", self.lines(read_ok=False, brief_given=False))


if __name__ == "__main__":
    unittest.main()
