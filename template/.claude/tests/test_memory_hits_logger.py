"""Re-runnable proof for hooks/memory_hits_logger.py + tools/memory_hits.py.

instruction_quality §14: registration proves wiring, not matching. Each case
feeds the logger a real PostToolUse payload via subprocess (through
post_read_dispatch.py, the wired entry point) and asserts on the JSONL it
writes, then checks the reader tool groups/orders it.

    python3 .claude/tests/test_memory_hits_logger.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH = os.path.join(HERE, "..", "hooks", "post_read_dispatch.py")
MEMORY_HITS_TOOL = os.path.join(HERE, "..", "tools", "memory_hits.py")


def run_dispatch(payload, log_file, repo):
    env = dict(os.environ)
    env["HARNESS_MEMORY_HITS_LOG"] = log_file
    env["CLAUDE_PROJECT_DIR"] = repo
    env["HARNESS_HOOK_STATE_DIR"] = os.path.join(repo, ".routing_state")
    subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload),
                    capture_output=True, text=True, timeout=90, env=env)


def read_lines(log_file):
    if not os.path.exists(log_file):
        return []
    with open(log_file, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def run_tool(session, log_file):
    env = dict(os.environ)
    env["HARNESS_MEMORY_HITS_LOG"] = log_file
    r = subprocess.run([sys.executable, MEMORY_HITS_TOOL, "--session", session, "--json"],
                       capture_output=True, text=True, timeout=90, env=env)
    return json.loads(r.stdout.strip() or "[]")


def main():
    failures = []
    repo = tempfile.mkdtemp(prefix="memhits_")
    log_file = os.path.join(tempfile.mkdtemp(prefix="memhitslog_"), "memory_hits.jsonl")

    def check(label, cond, detail=""):
        check.calls += 1
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail if detail else ""))
    check.calls = 0

    # 1. Read of an auto-memory path -> one "read" line.
    run_dispatch({
        "tool_name": "Read", "session_id": "sidone01", "agent_id": "",
        "tool_input": {"file_path": "C:\\repo\\.claude\\auto-memory\\gotcha_foo.md"},
        "tool_response": {"content": "..."},
    }, log_file, repo)
    lines = read_lines(log_file)
    read_lines_for_sid = [r for r in lines if r["session_id"] == "sidone01"]
    check("Read of auto-memory path logs one read line",
          len(read_lines_for_sid) == 1 and read_lines_for_sid[0]["via"] == "read"
          and read_lines_for_sid[0]["path"] == ".claude/auto-memory/gotcha_foo.md",
          repr(read_lines_for_sid))

    # 2. Read of a non-memory .cs path -> nothing.
    before = len(read_lines(log_file))
    run_dispatch({
        "tool_name": "Read", "session_id": "sidtwo02", "agent_id": "",
        "tool_input": {"file_path": "C:\\repo\\Scripts\\Foo.cs"},
        "tool_response": {"content": "..."},
    }, log_file, repo)
    after = len(read_lines(log_file))
    check("Read of non-memory .cs path logs nothing", after == before)

    # 3. Search response embedding two memory paths -> two search lines.
    run_dispatch({
        "tool_name": "mcp__plugin_semantic-search_semantic-search__search",
        "session_id": "sidthr03", "agent_id": "",
        "tool_input": {"query": "gotcha about foo"},
        "tool_response": {
            "results": [
                {"path": ".claude/auto-memory/archive/arch_rule_bar.md", "score": 0.9},
                {"path": ".claude/auto-memory/gotcha_baz.md", "score": 0.8},
                {"path": "Scripts/Unrelated.cs", "score": 0.1},
            ]
        },
    }, log_file, repo)
    search_lines = [r for r in read_lines(log_file) if r["session_id"] == "sidthr03"]
    check("search response with two memory paths logs two search lines",
          len(search_lines) == 2 and all(r["via"] == "search" for r in search_lines),
          repr(search_lines))

    # 4. Log redirect env (HARNESS_MEMORY_HITS_LOG) honored -> file exists at that exact path.
    check("log redirect env honored", os.path.exists(log_file))

    # 5. The tool groups and orders them: read beats search-only.
    rows = run_tool("sidone01", log_file)
    check("tool lists the read-provenance path for sidone01",
          len(rows) == 1 and rows[0]["path"] == ".claude/auto-memory/gotcha_foo.md" and rows[0]["read"] == 1,
          repr(rows))

    rows3 = run_tool("sidthr03", log_file)
    check("tool lists both search hits for sidthr03, ordered",
          len(rows3) == 2 and all(r["search"] == 1 for r in rows3),
          repr(rows3))

    # 7. Search response with Windows backslash paths -> still logged, normalized to posix.
    run_dispatch({
        "tool_name": "mcp__plugin_semantic-search_semantic-search__search",
        "session_id": "sidfou04", "agent_id": "",
        "tool_input": {"query": "gotcha about win"},
        "tool_response": {"results": [{"path": "C:\\repo\\.claude\\auto-memory\\gotcha_win.md", "score": 0.9}]},
    }, log_file, repo)
    win_lines = [r for r in read_lines(log_file) if r["session_id"] == "sidfou04"]
    check("search response with backslash path logs a posix-normalized search line",
          len(win_lines) == 1 and win_lines[0]["path"] == ".claude/auto-memory/gotcha_win.md",
          repr(win_lines))

    # 7b. A read_files bundle (the routed-correct path for 3+ files) logs each memory path as
    #     via="read", through the hook's own direct wiring (settings.json read_files matcher).
    env = dict(os.environ, HARNESS_MEMORY_HITS_LOG=log_file, CLAUDE_PROJECT_DIR=repo)
    subprocess.run([sys.executable, os.path.join(HERE, "..", "hooks", "memory_hits_logger.py")],
                   input=json.dumps({
                       "tool_name": "mcp__ai-worker__read_files", "session_id": "sidfiv05", "agent_id": "",
                       "tool_input": {"paths": ["C:\\repo\\.claude\\auto-memory\\gotcha_a.md",
                                                ".claude/auto-memory/archive/gotcha_b.md",
                                                "Scripts/Unrelated.cs"],
                                      "question": "what bit us?"},
                       "tool_response": {"content": "digest"},
                   }), capture_output=True, text=True, timeout=90, env=env)
    bundle_lines = [r for r in read_lines(log_file) if r["session_id"] == "sidfiv05"]
    check("read_files bundle logs its two memory paths as read-provenance",
          len(bundle_lines) == 2 and all(r["via"] == "read" for r in bundle_lines)
          and {r["path"] for r in bundle_lines} == {".claude/auto-memory/gotcha_a.md",
                                                     ".claude/auto-memory/archive/gotcha_b.md"},
          repr(bundle_lines))

    # 8. No --session -> the tool refuses; an unscoped run would report every session as this one.
    env = dict(os.environ, HARNESS_MEMORY_HITS_LOG=log_file)
    r = subprocess.run([sys.executable, MEMORY_HITS_TOOL, "--json"],
                       capture_output=True, text=True, timeout=90, env=env)
    check("tool without --session exits non-zero instead of reporting every session",
          r.returncode != 0 and "--session" in (r.stderr or ""), r.stderr[-200:])

    total = check.calls
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
