"""Proof for hooks/file_size_preblock.py: an unbounded Read of a file at or over CLAMP_BYTES gets its head
and a way on; every other Read is left to the native Read tool.

The decision is the file's size and whether the call is bounded, nothing else: no extension list, no
instruction-directory carve-out, no prompt cue, no per-agent state. The dispatcher's spawned case
(test_pre_read_dispatch.py) proves it runs from its registered command.

Run: python3 .claude/tests/test_file_size_preblock.py  (pytest-compatible; no pytest needed)
"""
import importlib.util
import os
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
spec = importlib.util.spec_from_file_location("file_size_preblock", os.path.join(HOOKS, "file_size_preblock.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

LINE = b"0123456789" * 7 + b"\n"


def _read(path, **extra):
    return {"tool_name": "Read", "tool_input": {"file_path": path, **extra}}


def _file(dirpath, rel, size, line=LINE):
    p = os.path.join(dirpath, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(line * (size // len(line) + 1))
    return p


def test_the_clamp_starts_at_the_native_ceiling_not_below_it():
    """Below 100 KB the clamp only added a call, and the native
    Read already refuses past 25,000 tokens: the hook acts at the size where it turns a refusal into a head."""
    with tempfile.TemporaryDirectory() as d:
        under = _file(d, "a/under.md", mod.CLAMP_BYTES - len(LINE))
        at = _file(d, "a/at.md", mod.CLAMP_BYTES)
        assert os.path.getsize(under) < mod.CLAMP_BYTES <= os.path.getsize(at)
        assert mod.process(_read(under)) is None
        assert mod.process(_read(at)) is not None


def test_a_log_is_held_to_the_same_size_as_any_file():
    with tempfile.TemporaryDirectory() as d:
        log = _file(d, "run/build.log", 30 * 1024)
        assert mod.process(_read(log)) is None
        assert mod.process(_read(_file(d, "run/big.log", mod.CLAMP_BYTES))) is not None


def test_the_clamp_bounds_the_same_read_to_whole_lines_of_the_head():
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, "a/big.md", 3 * mod.CLAMP_BYTES)
        got = mod.process(_read(big, pages="1-2"))
        rewrite = got["rewrite"]
        assert rewrite["file_path"] == big and rewrite["pages"] == "1-2"
        assert "offset" not in rewrite and rewrite["limit"] > 0
        head = open(big, "rb").read().split(b"\n")[:rewrite["limit"]]
        assert sum(len(x) + 1 for x in head) <= mod.HEAD_BYTES
        assert "offset=%d" % (rewrite["limit"] + 1) in got["note"]


def test_an_instruction_file_is_clamped_like_any_other():
    """The exemption by directory is gone: an agent still holds the head and pages on, and a file this
    size is refused by the native Read anyway."""
    with tempfile.TemporaryDirectory() as d:
        for rel in (".claude/rules/r.md", ".claude/skills/x/SKILL.md", "CLAUDE.md"):
            assert mod.process(_read(_file(d, rel, mod.CLAMP_BYTES))) is not None, rel


def test_a_subagent_gets_the_same_decision_as_the_main_session():
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, "a/big.md", mod.CLAMP_BYTES)
        main = mod.process(_read(big))
        sub = mod.process(dict(_read(big), agent_id="agent-9", agent_type="workflow-subagent"))
        assert main == sub and main is not None


def test_reads_the_hook_must_leave_alone():
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, "a/big.md", mod.CLAMP_BYTES)
        assert mod.process(_read(big, limit=100)) is None, "bounded by limit"
        assert mod.process(_read(big, offset=10)) is None, "bounded by offset"
        assert mod.process(_read(os.path.join(d, "absent.md"))) is None, "missing file: Read words its own error"
        assert mod.process({"tool_name": "Grep", "tool_input": {"file_path": big}}) is None
        assert mod.process({"tool_name": "Read", "tool_input": {}}) is None
        for name in ("a.pdf", "n.ipynb", "i.png"):
            assert mod.process(_read(_file(d, "fmt/" + name, 2 * mod.CLAMP_BYTES))) is None, name


def test_a_file_with_no_line_in_its_head_is_left_to_the_native_refusal():
    """Single-line JSON: no line range fits the head, so there is nothing to clamp to; the native Read
    refuses a file this large with its own advice."""
    with tempfile.TemporaryDirectory() as d:
        one_line = _file(d, "a/min.json", 2 * mod.CLAMP_BYTES, line=b"x" * 1000)
        assert b"\n" not in open(one_line, "rb").read(mod.HEAD_BYTES)
        assert mod.process(_read(one_line)) is None


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # a crash is a failure, never a pass
                failed += 1
                print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if failed else 0)
