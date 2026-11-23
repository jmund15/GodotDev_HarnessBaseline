"""One test style: a new test file is plain pytest. `unittest.TestCase` and a hand-rolled `def expect(` are frozen.

`LEGACY` is the shrinking list of files still written that way (each has a `setUp`/`setUpClass` or class helpers the
pytest form replaces with a fixture). Convert a file, delete its row; never add one. The test also fails when a listed
file is already clean, so the list cannot go stale.
"""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
LEGACY = frozenset("""
test_agy_hook_bridge.py
test_bounded_run.py
test_bounded_run_timing.py
test_checkout_root.py
test_digest_final_review.py
test_full_digest_evidence.py
test_hook_call.py
test_large_read_reminder.py
test_machine_slots.py
test_overnight_ask_guard.py
test_peer_message_guard.py
test_plugin_registry.py
test_python_outline.py
test_root_strays.py
test_scratch_prune.py
test_session_digest_cli.py
test_session_stores.py
test_skill_listing_review.py
test_stray_search_index_sweep.py
test_transcript_scan.py
test_user_scope.py
test_vault_mirror_start.py
test_vault_sync_config.py
test_vault_sync_guard.py
test_vault_sync_mirror.py
test_vault_sync_scan.py
test_vault_sync_task.py
test_verify_doc_citations.py
test_video_frames.py
""".split())


def old_style(source):
    """Names of the `TestCase` subclasses and `expect` helpers `source` defines."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and any(getattr(b, "attr", getattr(b, "id", "")) == "TestCase"
                                                  for b in node.bases):
            found.append(node.name)
        elif isinstance(node, ast.FunctionDef) and node.name == "expect":
            found.append(node.name)
    return found


def _offenders():
    return {p.relative_to(TESTS).as_posix() for p in TESTS.rglob("test_*.py")
            if old_style(p.read_text(encoding="utf-8"))}


def test_the_scan_sees_both_old_styles():
    src = "NL".join(["import unittest", "class A(unittest.TestCase): pass", "class B(TestCase): pass",
                     "def expect(a): pass", "def test_x(): pass"])
    assert old_style(src.replace("NL", chr(10))) == ["A", "B", "expect"]


def test_no_new_test_file_uses_testcase_or_expect():
    assert not _offenders() - LEGACY, "write plain pytest (functions, fixtures, parametrize), not TestCase or expect()"


def test_the_legacy_list_holds_only_files_that_still_offend():
    assert not LEGACY - _offenders(), "converted: delete these rows from LEGACY"
