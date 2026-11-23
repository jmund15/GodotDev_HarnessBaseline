"""Proof for tools/quote_check.py: a review's cited quotes, command outputs and owner words, checked against bytes.

Decision cases call `quote_check.main` in process on findings files written under `tmp_path`. Each case states
the decision it proves: a true quote holds, a misquote misses, a command output holds only when its text is in
stdout, a command off the allow-list is refused, an empty count holds only over tracked paths, an owner quote
holds only in a user-typed record, and a malformed findings file is an input error (exit 2). One spawned case
proves the CLI entry point runs and prints its misses.

    python -m pytest .claude/tests/test_quote_check.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import quote_check  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


@pytest.fixture(scope="session")
def checkout(tmp_path_factory):
    """A small repository with two tracked files; every case reads it and writes nothing to it."""
    root = tmp_path_factory.mktemp("checkout")
    (root / "notes.md").write_text("The quick brown fox\njumps over the lazy dog.\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def seam():\n    return 1\n", encoding="utf-8")
    subprocess.run(GIT + ["init", "-q", str(root)], check=True)
    subprocess.run(GIT + ["-C", str(root), "add", "."], check=True)
    subprocess.run(GIT + ["-C", str(root), "commit", "-q", "-m", "init"], check=True)
    return root


def findings(tmp_path, items, heading="Finding one"):
    """A findings file with one `##` section holding `items` as its one evidence block."""
    path = tmp_path / "findings.md"
    path.write_text("## %s\n\nThe claim.\n\n```json\n%s\n```\n" % (heading, json.dumps(items)), encoding="utf-8")
    return path


def check(capsys, path, root):
    code = quote_check.main([str(path), "--root", str(root)])
    return code, capsys.readouterr().out


def test_a_true_quote_holds(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "notes.md", "quote": "brown fox"}]), checkout)
    assert code == 0, out
    assert "0 misses" in out


def test_a_misquote_is_flagged(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "notes.md", "quote": "brown dog"}]), checkout)
    assert code == 1
    assert "MISS Finding one [item 1] notes.md: quote not in file" in out


def test_a_quote_wrapped_across_lines_holds(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "notes.md", "quote": "fox jumps over"}]), checkout)
    assert code == 0, out


def test_a_line_range_suffix_is_not_part_of_the_path(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "src/a.py:2", "quote": "return 1"}]), checkout)
    assert code == 0, out


def test_a_missing_file_is_flagged(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "gone.md", "quote": "x"}]), checkout)
    assert code == 1 and "no such file" in out


def test_a_command_output_holds_when_it_is_in_stdout(tmp_path, checkout, capsys):
    item = {"command": "git grep -n fox -- notes.md", "output": "brown fox"}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 0, out


def test_a_command_output_that_differs_is_flagged(tmp_path, checkout, capsys):
    item = {"command": "git grep -n fox -- notes.md", "output": "brown dog"}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 1 and "output not in the command's stdout" in out


@pytest.mark.parametrize("command, why", [
    ("git push origin main", "not an allow-listed subcommand"),
    ("git log --output=/tmp/x", "a write option outside the table"),
    ("git log --ext-diff", "an option the table does not list"),
    ("git -C ../elsewhere log", "-C naming a directory outside the root"),
    ("rm -rf src", "not a git command"),
    ("git log | cat", "shell syntax"),
    ("git log $(echo x)", "a command substitution"),
])
def test_a_command_off_the_allow_list_is_refused(tmp_path, checkout, capsys, command, why):
    code, out = check(capsys, findings(tmp_path, [{"command": command, "output": "x"}]), checkout)
    assert code == 1 and "refused" in out, (why, out)


def test_an_empty_count_holds_over_a_tracked_path(tmp_path, checkout, capsys):
    item = {"command": "git grep -n zzz -- notes.md", "output": ""}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 0, out


def test_an_empty_count_over_a_missing_path_is_flagged(tmp_path, checkout, capsys):
    item = {"command": "git grep -n zzz -- nope.md", "output": ""}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 1 and "matches no tracked path" in out


def test_git_minus_C_runs_inside_the_named_directory(tmp_path, checkout, capsys):
    item = {"command": "git -C src ls-files", "output": "a.py"}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 0, out


def write_transcript(tmp_path):
    lines = [
        {"type": "user", "message": {"role": "user", "content": 'we keep "the seam" thin'}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "the seam is mine"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "a tool said so"}]}},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def test_an_owner_quote_holds_after_json_unescaping(tmp_path, checkout, capsys):
    transcript = write_transcript(tmp_path)
    item = {"file": str(transcript), "quote": 'we keep "the seam" thin'}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 0, out


@pytest.mark.parametrize("quote", ["the seam is mine", "a tool said so"])
def test_words_that_are_not_the_owners_do_not_hold(tmp_path, checkout, capsys, quote):
    item = {"file": str(write_transcript(tmp_path)), "quote": quote}
    code, out = check(capsys, findings(tmp_path, [item]), checkout)
    assert code == 1 and "words not in any owner turn" in out


def test_an_empty_quote_is_flagged(tmp_path, checkout, capsys):
    code, out = check(capsys, findings(tmp_path, [{"file": "notes.md", "quote": "  "}]), checkout)
    assert code == 1 and "empty quote" in out


def test_a_malformed_json_block_is_an_input_error(tmp_path, checkout, capsys):
    path = tmp_path / "findings.md"
    path.write_text("## Broken\n\n```json\n{not a list\n```\n", encoding="utf-8")
    code, out = check(capsys, path, checkout)
    assert code == 2


def test_a_findings_file_with_no_evidence_checks_nothing(tmp_path, checkout, capsys):
    path = tmp_path / "findings.md"
    path.write_text("## A question, not a finding\n\nNo evidence block.\n", encoding="utf-8")
    code, out = check(capsys, path, checkout)
    assert code == 0 and "0 items" in out


def test_the_cli_entry_point_prints_its_misses(tmp_path, checkout):
    path = findings(tmp_path, [{"file": "notes.md", "quote": "brown dog"}])
    proc = subprocess.run([sys.executable, str(TOOLS / "quote_check.py"), str(path), "--root", str(checkout)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 1, proc.stderr
    assert "MISS Finding one [item 1] notes.md" in proc.stdout
