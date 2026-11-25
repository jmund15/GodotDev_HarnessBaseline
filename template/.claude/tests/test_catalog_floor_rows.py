"""Proof: the catalog binds the loss floor's table rows (`tests/floor/test_floor_*.py`) to the floor's modules.

`catalog.discover` lists `tests/`'s own files and does not recurse, so a scoped run, a close or the Linux preview that
edited `kill_guard.py` or `dangerous_shell_guard.py` selected generic proofs and none of the floor's rows (the review's
S-F4). A row judges the whole floor through `pre_bash_dispatch`, so a change to any floor module binds every row.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path[:0] = [str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")]
from _proof_runner import catalog  # noqa: E402

ROWS = [".claude/tests/floor/test_floor_a.py", ".claude/tests/floor/test_floor_b.py"]
NO_BEHAVIOR = lambda _root, _paths: {"proofs": []}  # noqa: E731


def plant(tmp_path):
    for rel in (*ROWS, ".claude/tests/floor/case.py", ".claude/tests/floor/test_floor_ported.md",
                ".claude/tests/test_unrelated.py", ".claude/hooks/prompt_dispatch.py"):
        path = tmp_path.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return catalog.Catalog.open(catalog.View.worktree(str(tmp_path)))


def selected(tmp_path, rel):
    return plant(tmp_path).select([rel], behavior=NO_BEHAVIOR).proofs


@pytest.mark.parametrize("module", getattr(catalog, "FLOOR_MODULES", ()) or ["FLOOR_MODULES is not defined"])
def test_a_floor_module_binds_every_floor_row_and_nothing_else(tmp_path, module):
    assert selected(tmp_path, module) == ROWS


def test_the_floor_modules_are_named():
    assert getattr(catalog, "FLOOR_MODULES", None), "catalog.FLOOR_MODULES names the modules the floor rows judge"


def test_an_ordinary_hook_binds_no_floor_row(tmp_path):
    assert selected(tmp_path, ".claude/hooks/prompt_dispatch.py") == []


def test_a_floor_row_or_its_helper_binds_the_rows_and_its_notes_bind_none(tmp_path):
    assert selected(tmp_path, ".claude/tests/floor/case.py") == ROWS
    assert selected(tmp_path, ROWS[0]) == [ROWS[0]]
    assert selected(tmp_path, ".claude/tests/floor/test_floor_ported.md") == []


def test_a_catalog_given_its_proofs_selects_only_them(tmp_path):
    only = catalog.Catalog.open(catalog.View.worktree(str(plant(tmp_path).view.root)),
                                proofs=[".claude/tests/test_unrelated.py"])
    assert only.select([getattr(catalog, "FLOOR_MODULES", ["x"])[0]], behavior=NO_BEHAVIOR).proofs == []


def test_every_floor_member_the_dispatcher_names_is_a_floor_module_and_a_module_gone_is_a_member():
    """A floor member may be absent from an install (the godot layer's editor guard in a pure one); a helper never."""
    import pre_bash_dispatch
    modules = getattr(catalog, "FLOOR_MODULES", ())
    members = set(pre_bash_dispatch._FLOOR_MEMBERS)
    assert members <= {Path(rel).stem for rel in modules}, sorted(members - {Path(rel).stem for rel in modules})
    assert not [rel for rel in modules if not (CLAUDE.parent / rel).is_file() and Path(rel).stem not in members]
