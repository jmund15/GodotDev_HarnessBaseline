"""`hooks/_session_lineage.lineage`: which transcript files hold this session's rows.

A hook's `transcript_path` names one file while the process's session can span several (a `/clear` or a compaction
starts a new id and file in the same `claude.exe`). Every row below is planted in the shape of the real `derived-from`
rows: `{"type": "derived-from", "sessionId": <self>, "sessions": [{"id": ..., "via": ...}, ...]}`, newest entry last.

    python -m pytest .claude/tests/test_session_lineage.py -q
"""
import builtins
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _session_lineage as sl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "floor"))
from case import derived  # noqa: E402

STAMPS = {n: "2026-11-2%dT10:00:00.000Z" % n for n in range(1, 10)}


def epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def stamped(day=1):
    return {"type": "user", "timestamp": STAMPS[day], "message": {"role": "user", "content": "hi"}}


class Folder:
    def __init__(self, tmp_path):
        self.dir = tmp_path

    def put(self, sid, *rows):
        path = self.dir / (sid + ".jsonl")
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return str(path)

    def path(self, sid):
        return os.path.join(str(self.dir), sid + ".jsonl")


@pytest.fixture
def folder(tmp_path):
    return Folder(tmp_path)


def ids(pairs):
    return [os.path.splitext(os.path.basename(path))[0] for path, _until in pairs]


# --- the chain ---------------------------------------------------------------------------------------------------
def test_a_three_file_clear_chain_yields_all_three_newest_first_each_until_the_childs_first_stamp(folder):
    folder.put("origin", stamped(1))
    folder.put("middle", derived("middle", ("origin", "clear")), stamped(2))
    new = folder.put("new", derived("new", ("middle", "clear")), stamped(3))
    assert list(sl.lineage(new)) == [(new, None), (folder.path("middle"), epoch(STAMPS[3])),
                                     (folder.path("origin"), epoch(STAMPS[2]))]


def test_a_clear_then_a_compaction_keeps_the_clear_parent(folder):
    folder.put("a", stamped(1))
    new = folder.put("new", derived("new", ("a", "clear")), derived("new", ("a", "clear"), ("new", "compact")), stamped(2))
    assert ids(sl.lineage(new)) == ["new", "a"]


def test_a_resume_between_the_clear_and_the_compaction_cuts_the_parent_off(folder):
    folder.put("a", stamped(1))
    new = folder.put("new", derived("new", ("a", "clear"), ("a", "resume"), ("new", "compact")), stamped(2))
    assert ids(sl.lineage(new)) == ["new"]


@pytest.mark.parametrize("rows", [
    [derived("new", ("a", "clear")), derived("new", ("a", "clear"), ("new", "compact")), derived("new", ("new", "resume"))],
    [derived("new", ("a", "clear"), ("new", "compact"), ("new", "resume"))],
    [derived("new", ("new", "resume"))],
], ids=["file-order rows", "one cumulative row", "a resume alone"])
def test_a_self_resume_entry_ends_the_walk_before_the_clear_parent(folder, rows):
    folder.put("a", stamped(1))
    new = folder.put("new", *rows, stamped(2))
    assert ids(sl.lineage(new)) == ["new"]


def test_the_last_derived_from_row_decides_even_when_a_resume_sits_mid_file(folder):
    folder.put("a", stamped(1))
    new = folder.put("new", derived("new", ("a", "clear")), stamped(2), derived("new", ("new", "resume")), stamped(3))
    assert ids(sl.lineage(new)) == ["new"]


@pytest.mark.parametrize("via", ["subprocess", "resume", "fork"])
def test_an_entry_that_is_not_the_same_process_stops_the_walk(folder, via):
    folder.put("a", stamped(1))
    folder.put("b", stamped(1))
    new = folder.put("new", derived("new", ("b", "clear"), ("a", via), ("new", "compact")), stamped(2))
    assert ids(sl.lineage(new)) == ["new"]


@pytest.mark.parametrize("head", [("grand", "resume"), ("middle", "resume"), ("grand", "subprocess")])
def test_a_cleared_parent_that_was_itself_resumed_is_not_yielded(folder, head):
    """Its rows before the resume are an earlier process's, and its derived-from row has no stamp to split them by."""
    folder.put("grand", stamped(1))
    folder.put("middle", derived("middle", head), stamped(2))
    new = folder.put("new", derived("new", ("middle", "clear")), stamped(3))
    assert ids(sl.lineage(new)) == ["new"]
    assert ids(sl.lineage(folder.path("middle"))) == ["middle"]


def test_a_parent_whose_row_ends_in_a_same_process_entry_after_a_resume_is_not_yielded(folder):
    folder.put("middle", derived("middle", ("middle", "resume"), ("middle", "compact")), stamped(2))
    new = folder.put("new", derived("new", ("middle", "clear")), stamped(3))
    assert ids(sl.lineage(new)) == ["new"]


# --- laziness and failure ----------------------------------------------------------------------------------------
def test_no_ancestor_file_is_opened_before_the_caller_asks_for_it(folder, monkeypatch):
    folder.put("b", stamped(1))
    folder.put("a", derived("a", ("b", "clear")), stamped(2))
    new = folder.put("new", derived("new", ("a", "clear")), stamped(3))
    opened = []
    real = builtins.open

    def recording(path, *args, **kwargs):
        opened.append(os.path.basename(str(path)))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording)
    walk = iter(sl.lineage(new))
    assert os.path.basename(next(walk)[0]) == "new.jsonl" and opened == []
    assert os.path.basename(next(walk)[0]) == "a.jsonl" and opened == ["new.jsonl", "a.jsonl"]
    assert os.path.basename(next(walk)[0]) == "b.jsonl" and opened == ["new.jsonl", "a.jsonl", "b.jsonl"]
    assert list(walk) == [] and opened == ["new.jsonl", "a.jsonl", "b.jsonl"]


def test_a_cycle_ends_cleanly(folder):
    a = folder.put("a", derived("a", ("b", "clear")), stamped(1))
    folder.put("b", derived("b", ("a", "clear")), stamped(2))
    assert ids(sl.lineage(a)) == ["a", "b"]


def test_a_missing_parent_ends_the_branch_and_an_older_listed_one_still_counts(folder):
    folder.put("older", stamped(1))
    new = folder.put("new", derived("new", ("older", "clear"), ("gone", "clear"), ("new", "compact")), stamped(2))
    assert ids(sl.lineage(new)) == ["new", "older"]
    alone = folder.put("alone", derived("alone", ("gone", "clear")), stamped(2))
    assert ids(sl.lineage(alone)) == ["alone"]


def test_an_unreadable_or_absent_transcript_yields_only_itself(folder):
    nothing = folder.path("nothing")
    assert list(sl.lineage(nothing)) == [(nothing, None)]
    assert list(sl.lineage(None)) == [] and list(sl.lineage("")) == []


def test_a_depth_past_the_limit_ends_the_walk_with_the_cut_flag_set(folder):
    folder.put("s0", stamped(1))
    for n in range(1, 6):
        folder.put("s%d" % n, derived("s%d" % n, ("s%d" % (n - 1), "clear")), stamped(2))
    walk = sl.lineage(folder.path("s5"), max_depth=2)
    assert ids(walk) == ["s5", "s4", "s3"] and walk.cut is True


def test_a_chain_within_the_limit_is_not_cut(folder):
    folder.put("s0", stamped(1))
    folder.put("s1", derived("s1", ("s0", "clear")), stamped(2))
    walk = sl.lineage(folder.path("s1"), max_depth=1)
    assert ids(walk) == ["s1", "s0"] and walk.cut is False


def test_an_ancestor_that_exists_beyond_the_limit_sets_cut_only_when_it_could_have_been_followed(folder):
    folder.put("s0", stamped(1))
    folder.put("s1", derived("s1", ("s0", "resume")), stamped(2))
    walk = sl.lineage(folder.path("s1"), max_depth=0)
    assert ids(walk) == ["s1"] and walk.cut is False
    folder.put("s2", derived("s2", ("s1", "clear")), stamped(3))
    walk = sl.lineage(folder.path("s2"), max_depth=0)
    assert ids(walk) == ["s2"] and walk.cut is True


# --- fail closed -------------------------------------------------------------------------------------------------
def test_a_child_with_no_timestamped_row_has_no_bound_so_its_parent_is_not_yielded(folder):
    folder.put("a", stamped(1))
    new = folder.put("new", derived("new", ("a", "clear")), {"type": "file-history-snapshot", "snapshot": {"timestamp": STAMPS[2]}})
    assert ids(sl.lineage(new)) == ["new"]


def test_an_id_that_is_not_a_plain_session_id_is_never_followed(folder, tmp_path):
    outside = tmp_path.parent / ("outside_%s" % tmp_path.name)
    outside.mkdir()
    (outside / "evil.jsonl").write_text(json.dumps(stamped(1)) + "\n", encoding="utf-8")
    new = folder.put("new", derived("new", ("../%s/evil" % outside.name, "clear"), ("a/b", "clear"), ("", "clear")),
                     stamped(2))
    assert ids(sl.lineage(new)) == ["new"]


def test_a_quoted_derived_from_mention_is_not_a_derived_from_row(folder):
    folder.put("a", stamped(1))
    quote = {"type": "assistant", "timestamp": STAMPS[2],
             "message": {"role": "assistant", "content": [{"type": "text", "text": json.dumps(derived("new", ("a", "clear")))}]}}
    new = folder.put("new", quote)
    assert ids(sl.lineage(new)) == ["new"]


def test_the_first_stamp_is_the_top_level_one_not_a_nested_snapshot_stamp(folder):
    folder.put("a", stamped(1))
    snapshot = {"type": "file-history-snapshot", "snapshot": {"timestamp": STAMPS[1]}}
    new = folder.put("new", derived("new", ("a", "clear")), snapshot, stamped(4))
    assert list(sl.lineage(new))[1] == (folder.path("a"), epoch(STAMPS[4]))


def test_the_module_imports_only_the_standard_library():
    import ast
    tree = ast.parse(Path(sl.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= set(sys.stdlib_module_names), names
