"""Proof for tests/_module_closure.py: a fixture names its entry modules, and the copy follows their imports.

The fixtures that hand-listed their modules broke whenever a listed module gained a sibling import. A planted source
tree (`src=`) holds the shapes the real tree has: a chain of module-level imports across `hooks/` and `tools/`, a
`_helper.py` loaded by file name inside a function, a package under `scripts/`, a lazy function-level import, and a
module nobody imports.

    python3 -m pytest .claude/tests/test_module_closure.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _module_closure as mc  # noqa: E402

TREE = {
    "hooks/entry.py": "import sys\nimport _mid\nfrom adaptation import load\n",
    "hooks/_mid.py": "try:\n    import _leaf\nexcept ImportError:\n    _leaf = None\n\n\ndef lazy():\n    import _lazy_only\n",
    "hooks/_leaf.py": "def load_lock():\n    return '_lock.py'\n",
    "hooks/_lock.py": "",
    "hooks/_lazy_only.py": "",
    "hooks/_unrelated.py": "",
    "tools/adaptation.py": "from _proof_runner import attempt\nimport json\n",
    "scripts/_proof_runner/attempt.py": "",
    "scripts/_proof_runner/other.py": "",
}


def plant(tmp_path):
    for rel, text in TREE.items():
        path = tmp_path / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path / "src"


def test_an_entry_brings_its_module_level_imports_across_hooks_tools_and_a_package(tmp_path):
    src = plant(tmp_path)
    assert mc.closure(["hooks/entry.py"], src) == [
        "hooks/_leaf.py", "hooks/_lock.py", "hooks/_mid.py", "hooks/entry.py",
        "scripts/_proof_runner/attempt.py", "tools/adaptation.py"]


def test_a_lazy_import_and_an_unrelated_module_are_not_copied(tmp_path):
    found = mc.closure(["hooks/entry.py"], plant(tmp_path))
    assert "hooks/_lazy_only.py" not in found and "hooks/_unrelated.py" not in found
    assert "scripts/_proof_runner/other.py" not in found


def test_a_fixture_that_drives_a_lazy_branch_names_that_module_as_an_entry(tmp_path):
    assert "hooks/_lazy_only.py" in mc.closure(["hooks/entry.py", "hooks/_lazy_only.py"], plant(tmp_path))


def test_omit_drops_a_sibling_and_everything_only_it_brought(tmp_path):
    found = mc.closure(["hooks/entry.py"], plant(tmp_path), omit=("_leaf.py",))
    assert "hooks/_leaf.py" not in found and "hooks/_lock.py" not in found and "hooks/_mid.py" in found


def test_copy_lands_each_file_at_its_relative_path_and_returns_what_it_copied(tmp_path):
    src, dest = plant(tmp_path), tmp_path / "dest" / ".claude"
    copied = mc.copy_closure(dest, "hooks/entry.py", src=src)
    assert all((dest / rel).is_file() for rel in copied) and (dest / "tools" / "adaptation.py").is_file()
    assert not (dest / "hooks" / "_unrelated.py").exists()


def test_with_entries_false_leaves_the_fixtures_own_copy_of_the_entry_alone(tmp_path):
    src, dest = plant(tmp_path), tmp_path / "dest"
    mc.copy_closure(dest, "hooks/entry.py", src=src, with_entries=False)
    assert not (dest / "hooks" / "entry.py").exists() and (dest / "hooks" / "_mid.py").is_file()


def test_flat_stages_every_file_in_one_folder(tmp_path):
    src, dest = plant(tmp_path), tmp_path / "stage"
    mc.copy_closure(dest, "hooks/entry.py", src=src, flat=True)
    assert sorted(p.name for p in dest.iterdir()) == ["_leaf.py", "_lock.py", "_mid.py", "adaptation.py", "attempt.py",
                                                        "entry.py"]


def test_the_runner_closure_is_what_the_runner_loads_at_import():
    # The real tree: harness_tests.py imports these at module level; a new top-level import joins the set unedited.
    found = mc.closure(["scripts/harness_tests.py"])
    assert "scripts/harness_tests.py" in found and "hooks/_hook_state.py" in found and "tools/machine_slots.py" in found
    assert all((mc.CLAUDE / rel).is_file() for rel in found)
