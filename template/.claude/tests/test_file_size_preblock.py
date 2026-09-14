"""Planted-violation proof for hooks/file_size_preblock.py: the block must be OBSERVED firing.

Run: python3 .claude/tests/test_file_size_preblock.py  (pytest-compatible; no pytest needed)
"""
import importlib.util
import os
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "file_size_preblock.py")
spec = importlib.util.spec_from_file_location("file_size_preblock", HOOK)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _read(path, **extra):
    return {"tool_name": "Read", "tool_input": {"file_path": path, **extra}}


def _file(dirpath, rel, size):
    p = os.path.join(dirpath, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(b"x" * size)
    return p


def test_scratch_md_over_threshold_blocks_and_bounded_passes():
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, ".claude/scratch/review.md", mod.LARGE_FILE_BYTE_THRESHOLD + 1)
        assert mod.process(_read(big)) is not None
        assert mod.process(_read(big, limit=100)) is None


def test_instruction_dirs_and_claude_md_pass_at_any_size():
    with tempfile.TemporaryDirectory() as d:
        for rel in (".claude/skills/x/SKILL.md", ".claude/plans/p.md", ".claude/CLAUDE.md", "CLAUDE.md"):
            p = _file(d, rel, mod.LARGE_FILE_BYTE_THRESHOLD + 1)
            assert mod.process(_read(p)) is None, rel


def test_log_blocks_above_log_threshold_only():
    with tempfile.TemporaryDirectory() as d:
        small = _file(d, "run/small.log", mod.LOG_BYTE_THRESHOLD - 1)
        big = _file(d, "run/build.log", mod.LOG_BYTE_THRESHOLD + 1)
        assert mod.process(_read(small)) is None
        msg = mod.process(_read(big))
        assert msg is not None and "grep-shaped" in msg
        assert mod.process(_read(big, offset=10, limit=50)) is None


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                failed += 1
                print("FAIL", name, e)
    sys.exit(1 if failed else 0)
