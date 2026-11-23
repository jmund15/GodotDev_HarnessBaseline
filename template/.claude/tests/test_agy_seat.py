#!/usr/bin/env python3
"""Proof for `tools/agy_seat.py`: the antigravity seat's settings, granted tool names, live stop and
result envelope.

Fixtures under `tests/fixtures/agy/` are cut from live `agy` 1.5.14 runs (2026-11-21): `base.jsonl` a
one-turn answer, `stream.jsonl` a run whose write and command were denied and whose read succeeded,
`deny_probe.jsonl` a run that started `schedule` and then idled. Planted cases reuse those captured
shapes. The `run` cases spawn a stub process because stopping a live process tree is what they decide.

    python3 .claude/tests/test_agy_seat.py
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys

import pytest
import tempfile
import textwrap
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "agy")
TOOL = os.path.join(HERE, "..", "tools", "agy_seat.py")
TMP = tempfile.mkdtemp(prefix="agy_seat_")
ROOT = r"C:\Users\jmund\Game_Dev\Godot_Projects\PushinPotions\.claude\scratch\agy_probe"
MAIN = r"C:\Users\jmund\Game_Dev\Godot_Projects\PushinPotions"
SCOUT = ["Read", "Glob", "Grep"]

CASES = []


def case(fn):
    CASES.append((fn.__name__.replace("_", " "), fn))
    return fn


def load():
    spec = importlib.util.spec_from_file_location("agy_seat", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def lines(name):
    return [json.loads(line) for line in text(name).splitlines() if line.strip()]


def joined(events):
    return "\n".join(json.dumps(e) for e in events) + "\n"


def with_tool(tool, path=None, state="DONE"):
    """`stream.jsonl` with its successful read replaced by one `tool` call ending in `state`."""
    events = lines("stream.jsonl")
    for event in events:
        step = event.get("step_update") or {}
        if step.get("tool_name") == "read_file":
            step["tool_name"] = step["tool_info"]["name"] = tool
            if path:
                step["tool_info"]["parameters"] = {"Path": path}
            if step["state"] == "DONE":
                step["state"] = state
    return joined(events)


def parse(seat, stream, builtins=SCOUT, rc=0, roots=(ROOT,), schema=None, model="gemini-3.8-flash"):
    return seat.parse(stream, model, rc, seat.allowed(builtins), list(roots), schema)


@case
def granted_keeps_mapped_builtins_less_the_disallowed():
    got = load().granted(["Read", "Glob", "TodoWrite", "mcp__x__y", "Bash", "WebFetch"], ["WebFetch"])
    return got == ["Read", "Glob", "Bash"], str(got)


@case
def scout_settings_grant_reads_and_the_shell_for_search_and_deny_the_rest():
    # agy has no list, glob or grep tool: its model searches through run_command (live tool list, 2026-11-21).
    s = load().settings(SCOUT)
    p = s["permissions"]
    return ({"read_file(*)", "command(*)"} <= set(p["allow"])
            and {"write_file(*)", "read_url(*)", "execute_url(*)"} <= set(p["deny"])
            and "command(*)" not in p["deny"]
            and s["allowNonWorkspaceAccess"] is False and "disableSlashCommands" not in s), str(s)


@case
def a_read_only_kind_without_search_gets_no_shell():
    p = load().settings(["Read"])["permissions"]
    return "command(*)" not in p["allow"] and "command(*)" in p["deny"], str(p)


@case
def a_read_kind_gets_the_read_only_mcp_servers_and_no_other():
    seat = load()
    p = seat.settings(SCOUT)["permissions"]
    return ({"mcp(semantic-search/*)", "mcp(git/*)"} <= set(p["allow"]) and "mcp(*)" not in p["deny"]
            and "call_mcp_tool" in seat.allowed(SCOUT)), str(p)


@case
def a_kind_without_read_keeps_mcp_denied():
    seat = load()
    p = seat.settings(["Bash"])["permissions"]
    return ("mcp(*)" in p["deny"] and not any(r.startswith("mcp(") for r in p["allow"])
            and "call_mcp_tool" not in seat.allowed(["Bash"])), str(p)


@case
def author_settings_grant_writes_and_bash_grants_commands():
    seat = load()
    author = seat.settings(["Read", "Write", "Edit"])["permissions"]
    bash = seat.settings(["Read", "Bash"])["permissions"]
    return ("write_file(*)" in author["allow"] and "write_file(*)" not in author["deny"]
            and "command(*)" in author["deny"]
            and "command(*)" in bash["allow"] and "write_file(*)" in bash["deny"]), f"{author} {bash}"


@case
def allowed_names_hold_the_runtime_read_and_write_names():
    seat = load()
    scout, author = seat.allowed(SCOUT), seat.allowed(["Read", "Write"])
    return ("read_file" in scout and "view_file" in scout and "write_file" not in scout
            and "run_command" in scout and "finish" in scout
            and "write_file" in author and "write_to_file" in author
            and "run_command" not in author), f"{scout} {author}"


@case
def edit_file_is_a_governed_write_tool_not_a_live_stop():
    # The model-facing tool list (`agy` 1.5.14, scout settings, 2026-11-21) names edit_file, which init.tools omits.
    seat = load()
    line = json.dumps({"event": "step_update", "step_update": {"step_index": 3, "state": "ACTIVE", "step_type": "tool",
                                                               "tool_name": "edit_file"}})
    return ("edit_file" in seat.allowed(SCOUT + ["Edit"]) and "edit_file" not in seat.allowed(SCOUT)
            and seat.ungoverned_start(line) is None), str(seat.allowed(SCOUT + ["Edit"]))


@case
def manage_task_is_a_governed_command_tool_not_a_live_stop():
    # agy backgrounds a run_command and waits on it through manage_task (kill, status, send_input); a live author
    # job that ran its tests in the background was stopped here (agy 1.5.14, 2026-11-22).
    seat = load()
    line = json.dumps({"event": "step_update", "step_update": {"step_index": 9, "state": "ACTIVE", "step_type": "tool",
                                                               "tool_name": "manage_task"}})
    return ("manage_task" in seat.allowed(["Read", "Bash"]) and "manage_task" not in seat.allowed(["Read"])
            and seat.ungoverned_start(line) is None), str(seat.allowed(["Read", "Bash"]))


@case
def an_mcp_call_outside_the_read_only_servers_is_a_live_stop():
    # The ACTIVE step names its server (live agy 1.5.14 stream, 2026-11-21): ServerName "semantic-search".
    seat = load()

    def mcp(server):
        params = {"Arguments": {}, "ToolName": "t"}
        if server is not None:
            params["ServerName"] = server
        return json.dumps({"event": "step_update", "step_update": {
            "step_index": 2, "state": "ACTIVE", "step_type": "tool", "tool_name": "call_mcp_tool",
            "tool_info": {"name": "call_mcp_tool", "parameters": params}}})
    got = [seat.ungoverned_start(mcp(s)) for s in ("semantic-search", "git", "godot", None)]
    return got[0] is None and got[1] is None and got[2] and got[3], str(got)


@case
def a_kind_gets_only_the_read_only_servers_its_profile_lists():
    # guards/tool_profiles.json: author lists semantic-search and ai-worker (no git); arm lists semantic-search only.
    seat = load()
    servers = seat.granted_servers(["semantic-search", "ai-worker"])
    p = seat.settings(["Read", "Write"], servers)["permissions"]
    none = seat.settings(["Read"], [])["permissions"]
    return (servers == ["semantic-search"] and "mcp(semantic-search/*)" in p["allow"]
            and "mcp(git/*)" not in p["allow"] and "mcp(*)" in none["deny"]
            and "call_mcp_tool" not in seat.allowed(["Read"], [])), f"{servers} {p} {none}"


@case
def every_mcp_tool_naming_a_server_outside_the_jobs_list_is_a_live_stop():
    seat = load()

    def step(tool, server):
        params = {} if server is None else {"ServerName": server}
        return json.dumps({"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE", "step_type": "tool",
                                                                   "tool_name": tool, "tool_info": {"parameters": params}}})
    only = ["semantic-search"]
    got = [seat.ungoverned_start(step("call_mcp_tool", "git"), only),
           seat.ungoverned_start(step("read_resource", "godot"), only),
           seat.ungoverned_start(step("list_resources", None), only),
           seat.ungoverned_start(step("read_resource", "semantic-search"), only)]
    return bool(got[0]) and bool(got[1]) and got[2] is None and got[3] is None, str(got)


@case
def a_read_of_the_runs_own_spilled_tool_output_is_inside():
    # agy spills a large tool output under brain/<conversation>/.system_generated and reads it back (live capture,
    # rootcause-neon 2026-11-22). Another conversation's spill, and a write there, stay outside.
    seat = load()
    conv = "3fb35385-f1bd-413f-aec9-e7965c576ecd"
    brain = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "brain")

    def stream(tool, path, conversation=conv):
        rows = [{"event": "init", "conversation_id": conversation, "init": {"model": "m"}},
                {"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "tool",
                                                         "tool_name": tool, "tool_info": {"parameters": {"AbsolutePath": path}}}},
                {"event": "result", "result": {"status": "SUCCESS", "response": "ok"}}]
        return "\n".join(json.dumps(r) for r in rows)
    own = os.path.join(brain, conv, ".system_generated", "steps", "31", "output.txt")
    other = os.path.join(brain, "00000000-0000-0000-0000-000000000000", ".system_generated", "steps", "1", "output.txt")
    ok = seat.parse(stream("read_file", own), "m", 0, ["read_file", "write_file", "finish"], [ROOT])
    foreign = seat.parse(stream("read_file", other), "m", 0, ["read_file", "finish"], [ROOT])
    written = seat.parse(stream("write_file", own), "m", 0, ["read_file", "write_file", "finish"], [ROOT])
    return (ok["is_error"] is False and foreign["is_error"] is True and written["is_error"] is True), \
        f"{ok['outsideProfile']} {foreign['outsideProfile']} {written['outsideProfile']}"


@case
def a_one_turn_answer_parses_clean_with_its_tokens():
    env = parse(load(), text("base.jsonl"))
    usage = env["modelUsage"].get("gemini-3.8-flash") or {}
    return (env["is_error"] is False and env["result"] == "OK" and usage.get("inputTokens") == 9734
            and usage.get("outputTokens") == 1 and "session_id" not in env), str(env)


@case
def denied_write_and_command_with_an_in_root_read_parse_clean():
    env = parse(load(), text("stream.jsonl"), model="gemini-3.8-flash")
    return (env["is_error"] is False and env["outsideProfile"] == []
            and list(env["modelUsage"]) == ["gemini-3.8-flash"]), str(env)[:600]


@case
def an_effort_suffixed_model_id_maps_back_to_the_passed_model():
    env = parse(load(), text("stream.jsonl"), model="gemini-3.8-flash")
    env2 = parse(load(), text("base.jsonl"), model="gemini-4-hydrogen")
    return (list(env["modelUsage"]) == ["gemini-3.8-flash"]
            and list(env2["modelUsage"]) == ["gemini-3.8-flash"]), f"{list(env['modelUsage'])} {list(env2['modelUsage'])}"


@case
def a_scheduled_task_is_out_of_profile_and_an_error():
    env = parse(load(), text("deny_probe.jsonl"), rc=86)
    return (env["is_error"] is True
            and [o["tool"] for o in env["outsideProfile"]] == ["schedule"]), str(env)


@case
def browser_and_notebook_calls_are_out_of_profile():
    seat = load()
    got = [parse(seat, with_tool(t))["outsideProfile"] for t in ("execute_browser_javascript", "notebook_execution")]
    return all(g and g[0]["why"] == "ungoverned tool" for g in got), str(got)


@case
def a_read_outside_the_root_is_out_of_profile():
    env = parse(load(), with_tool("view_file", r"C:\Users\jmund\secrets.txt"))
    return env["is_error"] and env["outsideProfile"][0]["why"] == "outside the root", str(env["outsideProfile"])


@case
def a_read_in_a_sibling_worktree_is_out_of_profile():
    path = MAIN + r"\.claude\worktrees\other\notes.md"
    inside = parse(load(), with_tool("view_file", MAIN + r"\README.md"), roots=(MAIN,))
    nested = parse(load(), with_tool("view_file", path), roots=(MAIN,))
    return (not inside["is_error"] and nested["is_error"]
            and nested["outsideProfile"][0].get("path") == path), f"{inside['outsideProfile']} {nested['outsideProfile']}"


@case
def a_completed_write_on_a_scout_is_out_of_profile_and_a_denied_one_is_not():
    seat = load()
    done = parse(seat, with_tool("write_file", ROOT + r"\x.txt"))
    denied = parse(seat, text("stream.jsonl"))
    return (done["outsideProfile"] and done["outsideProfile"][0]["why"] == "not granted to this kind"
            and not denied["outsideProfile"]), f"{done['outsideProfile']}"


@case
def an_error_status_or_a_nonzero_rc_is_an_error():
    seat = load()
    events = lines("base.jsonl")
    events[-1]["result"]["status"] = "ERROR"
    status = parse(seat, joined(events))
    rc = parse(seat, text("base.jsonl"), rc=1)
    return status["is_error"] and rc["is_error"], f"{status['is_error']} {rc['is_error']}"


@case
def an_answer_that_breaks_the_schema_is_an_error_and_a_match_is_structured():
    seat = load()
    schema = {"type": "object", "required": ["verdict"], "properties": {"verdict": {"type": "string"}}}
    events = lines("base.jsonl")
    bad = parse(seat, joined(events), schema=schema)
    events[-1]["result"]["response"] = '{"verdict": "ok"}'
    good = parse(seat, joined(events), schema=schema)
    return (bad["is_error"] and bad["structured_output"] is None
            and not good["is_error"] and good["structured_output"] == {"verdict": "ok"}), f"{bad} {good}"


STUB = textwrap.dedent('''
    import json, sys, time
    lines = [json.dumps(json.loads(l)) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    for line in lines:
        print(line, flush=True)
    time.sleep(float(sys.argv[2]))
''')


def stub_run(seat, fixture, linger):
    script = os.path.join(TMP, "stub.py")
    with open(script, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(STUB)
    stream = os.path.join(TMP, fixture + ".out")
    start = time.time()
    rc, stopped = seat.run(stream, [sys.executable, script, os.path.join(FIX, fixture), str(linger)],
                           stdin=subprocess.DEVNULL)
    return rc, stopped, time.time() - start, stream


@case
def run_stops_the_process_at_an_ungoverned_tool_start():
    rc, stopped, took, stream = stub_run(load(), "deny_probe.jsonl", 60)
    with open(stream, encoding="utf-8") as fh:
        kept = fh.read()
    return (rc == 86 and stopped == "schedule" and took < 30
            and '"schedule"' in kept), f"rc={rc} stopped={stopped} took={took:.1f}"


@case
def run_stamps_each_json_line_it_tees():
    _rc, _stopped, _took, stream = stub_run(load(), "stream.jsonl", 0)
    with open(stream, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return rows and all(isinstance(r.get("timestamp"), str) and r["timestamp"].endswith("Z") for r in rows), str(rows[:1])


@case
def run_passes_a_governed_stream_through_and_keeps_its_rc():
    rc, stopped, _took, stream = stub_run(load(), "stream.jsonl", 0)
    with open(stream, encoding="utf-8") as fh:
        kept = fh.read()
    return rc == 0 and stopped is None and '"event": "result"' in kept, f"rc={rc} stopped={stopped}"


@case
def the_cli_prints_settings_and_the_envelope():
    import subprocess
    s = subprocess.run([sys.executable, TOOL, "settings", "--builtins", "Read,Grep"], capture_output=True, text=True)
    p = subprocess.run([sys.executable, TOOL, "parse", "--stream", os.path.join(FIX, "base.jsonl"),
                        "--model", "gemini-3.8-flash", "--rc", "0", "--allowed", "read_file,finish", "--root", ROOT],
                       capture_output=True, text=True, encoding="utf-8")
    try:
        ok = (sorted(json.loads(s.stdout)["permissions"]["allow"])
              == sorted(["read_file(*)", "mcp(semantic-search/*)", "mcp(git/*)", "command(*)"])
              and json.loads(p.stdout)["is_error"] is False and p.returncode == 0)
    except ValueError:
        ok = False
    return ok, (s.stdout + s.stderr + p.stdout + p.stderr)[-400:]


def _run(fn):
    try:
        got = fn()
        return got if isinstance(got, tuple) else (bool(got), "")
    except Exception as exc:  # a crash is a failure, never a pass
        return False, "%s: %s" % (type(exc).__name__, exc)


@pytest.mark.parametrize("name,fn", CASES, ids=[name for name, _ in CASES])
def test_case(name, fn):
    ok, detail = _run(fn)
    assert ok, str(detail)[:400]


if __name__ == "__main__":
    failed = []
    for name, fn in CASES:
        ok, detail = _run(fn)
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:400]))
        if not ok:
            failed.append(name)
    print("%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
    sys.exit(1 if failed else 0)
