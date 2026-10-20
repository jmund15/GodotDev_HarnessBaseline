"""Planted-violation proof for hooks/file_size_preblock.py: the block must be OBSERVED firing.

Run: python3 .claude/tests/test_file_size_preblock.py  (pytest-compatible; no pytest needed)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
sys.path.insert(0, HOOKS)  # the hook imports its sibling _claude_scope
HOOK = os.path.join(HOOKS, "file_size_preblock.py")
spec = importlib.util.spec_from_file_location("file_size_preblock", HOOK)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _read(path, **extra):
    return {"tool_name": "Read", "tool_input": {"file_path": path, **extra}}


def _run_hook(payload):
    result = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode in (0, 2), result.stderr
    return result


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
        blocked = _run_hook(_read(big))
        assert blocked.returncode == 2 and not blocked.stdout
        assert "CLAUDE.core.md §9" in blocked.stderr
        assert "bounded `Read" in blocked.stderr


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
        blocked = _run_hook(_read(big))
        assert blocked.returncode == 2 and not blocked.stdout
        assert "CLAUDE.core.md §9" in blocked.stderr
        assert "Recover: `Grep" in blocked.stderr


def test_worktree_checkout_measures_against_its_own_dot_claude():
    """A checkout at <root>/.claude/worktrees/<name>/ carries a `.claude` segment in
    every path inside it, so an inline `/.claude/` prefix test exempts nothing there —
    an instruction file over the threshold read as an unexempted artifact and blocked."""
    with tempfile.TemporaryDirectory() as d:
        wt = ".claude/worktrees/wt"
        for rel in (f"{wt}/.claude/skills/x/SKILL.md", f"{wt}/.claude/rules/r.md",
                    f"{wt}/.claude/CLAUDE.md"):
            p = _file(d, rel, mod.LARGE_FILE_BYTE_THRESHOLD + 1)
            assert mod.process(_read(p)) is None, rel
        # ...and the checkout's own artifact dirs stay gated, as they do outside one.
        art = _file(d, f"{wt}/.claude/scratch/review.md", mod.LARGE_FILE_BYTE_THRESHOLD + 1)
        assert mod.process(_read(art)) is not None


def test_pretooluse_subprocess_blocks_through_stderr_and_exit_code():
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, "run/build.log", mod.LOG_BYTE_THRESHOLD + 1)
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "file-size-live",
            "tool_name": "Read",
            "tool_input": {"file_path": big},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "[file-size-block]" in result.stderr
        assert result.stdout == ""

        payload["tool_input"]["limit"] = 40
        allowed = _run_hook(payload)
        assert allowed.returncode == 0
        assert allowed.stderr == ""


def _lines_file(dirpath, rel, size, line=b"0123456789" * 7 + b"\n"):
    p = os.path.join(dirpath, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(line * (size // len(line) + 1))
    return p


def _profile_env(tier, ai_worker):
    """Environment where this machine's tier and ai-worker registration read as planted."""
    root = tempfile.mkdtemp(prefix="fsp_profile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=cfg, SC_WORKDIR=os.path.join(root, "nowhere"),
               WORKSTATION_PROFILE_PATH=os.path.join(root, "workstation_profile.json"))
    if tier is not None:
        with open(env["WORKSTATION_PROFILE_PATH"], "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    return env


def _with_env(env, fn):
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_block_message_names_a_route_this_machine_actually_has():
    """C7: `guards/any.md` and this message both route bulk copyable I/O to ai-worker; on a machine
    where it is not registered the message must name the route that IS there (bounded Read or a
    subagent), and it must not lose the bounded Read that always passes this gate."""
    with tempfile.TemporaryDirectory() as d:
        big = _file(d, ".claude/scratch/review.md", mod.LARGE_FILE_BYTE_THRESHOLD + 1)

        absent = _with_env(_profile_env("full", "absent"), lambda: mod.process(_read(big)))
        assert "read_files" not in absent, absent
        assert "bounded `Read" in absent and "subagent" in absent, absent

        present = _with_env(_profile_env("full", "present"), lambda: mod.process(_read(big)))
        assert "mcp__ai-worker__read_files" in present, present

        unknown = _with_env(_profile_env(None, "present"), lambda: mod.process(_read(big)))
        assert unknown == present, (unknown, present)


def test_clamp_tail_names_a_route_this_machine_actually_has():
    with tempfile.TemporaryDirectory() as d:
        big = _lines_file(d, ".claude/scratch/review.md", 2 * mod.LARGE_FILE_BYTE_THRESHOLD)
        absent = _with_env(_profile_env("full", "absent"), lambda: mod.process(_read(big)))
        assert "read_files" not in absent["additionalContext"], absent
        assert "subagent" in absent["additionalContext"], absent
        assert "offset=%d" % (absent["updatedInput"]["limit"] + 1) in absent["additionalContext"]

        present = _with_env(_profile_env("full", "present"), lambda: mod.process(_read(big)))
        assert "mcp__ai-worker__read_files" in present["additionalContext"], present


def test_multiline_oversized_read_is_clamped_not_denied():
    """A file whose lines fit the budget gets a bounded read of its head instead of a denial;
    a first line over the budget (single-line JSON, the fixtures above) keeps the deny."""
    with tempfile.TemporaryDirectory() as d:
        big = _lines_file(d, ".claude/scratch/review.md", 2 * mod.LARGE_FILE_BYTE_THRESHOLD)
        got = mod.process(_read(big))
        assert isinstance(got, dict), got
        updated = got["updatedInput"]
        assert updated["file_path"] == big and updated["limit"] > 0
        assert "offset" not in updated
        head = open(big, "rb").read().split(b"\n")[:updated["limit"]]
        assert sum(len(x) + 1 for x in head) <= mod.LARGE_FILE_BYTE_THRESHOLD
        assert "offset=%d" % (updated["limit"] + 1) in got["additionalContext"]


def test_multiline_log_is_clamped_to_the_log_budget():
    with tempfile.TemporaryDirectory() as d:
        big = _lines_file(d, "run/build.log", 3 * mod.LOG_BYTE_THRESHOLD)
        got = mod.process(_read(big))
        assert isinstance(got, dict), got
        head = open(big, "rb").read().split(b"\n")[:got["updatedInput"]["limit"]]
        assert sum(len(x) + 1 for x in head) <= mod.LOG_BYTE_THRESHOLD
        assert "Grep" in got["additionalContext"]


def test_dispatcher_emits_updated_input_on_exit_zero():
    dispatcher = os.path.join(HOOKS, "pre_read_dispatch.py")
    with tempfile.TemporaryDirectory() as d:
        big = _lines_file(d, ".claude/scratch/review.md", 2 * mod.LARGE_FILE_BYTE_THRESHOLD)
        payload = {"hook_event_name": "PreToolUse", "session_id": "file-size-clamp",
                   "tool_name": "Read", "tool_input": {"file_path": big}}
        result = subprocess.run([sys.executable, dispatcher], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse"
        assert out["updatedInput"]["file_path"] == big and out["updatedInput"]["limit"] > 0
        assert "permissionDecision" not in out


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
