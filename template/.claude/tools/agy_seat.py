#!/usr/bin/env python3
"""Every Antigravity-CLI-specific decision of the `antigravity` sidecar seat, so the launcher stays on the
shared seams and each decision is tested in process.

    agy_seat.py granted --resolved <csv> [--disallowed <csv>]   the kind's built-ins the seat serves, less -x
    agy_seat.py servers --profile <tool profile JSON>           the read-only servers the kind's profile lists
    agy_seat.py settings --builtins <csv> [--servers <csv>]     the run's --settings JSON
    agy_seat.py allowed --builtins <csv> [--servers <csv>]      agy tool names the granted built-ins map to
    agy_seat.py run --stream <file> [--servers <csv>] -- <agy argv>...   spawn agy, tee its stream, live-stop
    agy_seat.py parse --stream <file> --model <id> --rc <n> --allowed <csv> --root <dir>
                      [--add-dir <dir>]... [--schema <file>]    the result envelope, as one JSON line

agy's `--settings` deny rules reach only its file, command, URL and MCP tools (the GOVERNED families);
a browser, schedule, message or subagent tool runs whatever they say. `run` therefore stops the CLI
at the first step that starts an ungoverned tool, and `parse` audits every call afterward. `run`
spawns the installed binary unchanged and reads only its stdout; nothing here touches `~/.gemini`.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "lib"))
from json_schema_lite import validate  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agy_workspace import READ_ONLY_SERVERS  # noqa: E402

READ = ("view_file", "read_file", "list_dir", "find_by_name", "grep_search")
WRITE = ("write_to_file", "write_file", "edit_file", "replace_file_content", "multi_replace_file_content", "sed_file")
# manage_task kills, polls or feeds the background tasks a run_command started, so it is part of the shell.
COMMAND = ("run_command", "command_status", "send_command_input", "manage_task")
WEB = ("read_url_content", "search_web")
BASE = ("finish", "wait", "wait_5_seconds")
MCP = ("call_mcp_tool", "list_resources", "read_resource")
GOVERNED = frozenset(READ + WRITE + COMMAND + WEB + BASE + MCP)


def agy_bin():
    """The installed Antigravity CLI: AGY_BIN (proofs plant a stub there), else agy's own install path."""
    return os.environ.get("AGY_BIN") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")

# built-in -> (agy tool family, the settings rules that grant it). `read_file` and `write_file` are runtime
# names absent from `init.tools`; the stream emits them for reads and writes. agy gives its model no list,
# glob or grep tool, so search runs through the shell: Glob, Grep and LS grant `command(*)`. A read kind is
# held to reading by its rail, not by settings: no settings rule confines a shell.
SEARCH = (READ + COMMAND, ("read_file(*)", "command(*)"))
FAMILIES = {
    "Read": (READ, ("read_file(*)",)), "Glob": SEARCH, "Grep": SEARCH, "LS": SEARCH,
    "Write": (WRITE, ("write_file(*)",)), "Edit": (WRITE, ("write_file(*)",)),
    "MultiEdit": (WRITE, ("write_file(*)",)),
    "Bash": (COMMAND, ("command(*)",)),
    "WebFetch": (WEB, ("read_url(*)",)), "WebSearch": (WEB, ("read_url(*)",)),
}
FAMILY_RULES = ("read_file(*)", "write_file(*)", "command(*)", "read_url(*)")
ALWAYS_DENY = ("execute_url(*)",)
STOPPED_RC = 86


def _builtins(csv):
    return [b.strip() for b in (csv or "").split(",") if b.strip()]


def granted(resolved, disallowed=()):
    """The resolved built-ins this seat maps, in order, less the disallowed ones. A built-in with no agy family
    (an MCP tool, TodoWrite, ToolSearch) is dropped: the run's settings deny everything they do not grant."""
    out = []
    for name in resolved:
        if name in FAMILIES and name not in disallowed and name not in out:
            out.append(name)
    return out


def granted_servers(profile_servers):
    """The workspace's read-only servers the kind's profile lists (guards/tool_profiles.json `mcpServers`)."""
    return [server for server in READ_ONLY_SERVERS if server in profile_servers]


def _mcp_servers(builtins, servers):
    """The servers a job may call: its granted servers, only for a kind that may read."""
    return list(servers) if "Read" in builtins else []


def settings(builtins, servers=READ_ONLY_SERVERS):
    """The run's settings: each granted family and each granted server allowed, every other family, URL
    execution and MCP denied, no access outside the workspace folders (the `-d` root and each `--add-dir`).
    Slash commands are off by the launcher's `--disable-slash-commands` flag, which is not a settings key."""
    allow = []
    for name in builtins:
        for rule in FAMILIES.get(name, (None, ()))[1]:
            if rule not in allow:
                allow.append(rule)
    allow += [f"mcp({server}/*)" for server in _mcp_servers(builtins, servers)]
    deny = [rule for rule in FAMILY_RULES if rule not in allow] + list(ALWAYS_DENY)
    if not any(rule.startswith("mcp(") for rule in allow):
        deny.append("mcp(*)")
    return {"permissions": {"allow": allow, "deny": deny},
            "allowNonWorkspaceAccess": False}


def allowed(builtins, servers=READ_ONLY_SERVERS):
    """The agy tool names a kind with `builtins` may complete: its families, the MCP tools when it holds a server,
    plus the always-on BASE."""
    names = list(BASE)
    for name in builtins:
        for tool in FAMILIES.get(name, ((), None))[0]:
            if tool not in names:
                names.append(tool)
    if _mcp_servers(builtins, servers):
        names += [tool for tool in MCP if tool not in names]
    return names


def _step(event):
    step = event.get("step_update") if isinstance(event, dict) else None
    return step if isinstance(step, dict) and step.get("step_type") == "tool" else None


def ungoverned_start(line, servers=READ_ONLY_SERVERS):
    """The tool name when a stream line starts a tool no settings rule reaches, or an MCP tool naming a server
    outside the job's `servers` (as `<tool> <server>`), else None. A tool call with no server named is a stop too;
    a resource listing with none lists every server and reads nothing. The server list binds here, at the start,
    whatever agy's own default is for a server its allow rules do not name."""
    try:
        step = _step(json.loads(line))
    except ValueError:
        return None
    if not step or step.get("state") != "ACTIVE":
        return None
    name = step.get("tool_name")
    if name not in GOVERNED:
        return name or "?"
    if name in MCP:
        server = ((step.get("tool_info") or {}).get("parameters") or {}).get("ServerName")
        if server is None and name == "list_resources":
            return None
        if server not in servers:
            return f"{name} {server or '(no server)'}"
    return None


def _kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


def _stamped(raw):
    try:
        row = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(row, dict):
        return raw
    row["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".%03dZ" % (int(time.time() * 1000) % 1000)
    return (json.dumps(row) + "\n").encode("utf-8")


def run(stream_path, argv, stdin=None, servers=READ_ONLY_SERVERS):
    """Spawn `argv` (the agy binary and its flags), pass stdin through, write each stdout line to
    `stream_path`, and stop the process tree at the first ungoverned tool start. Returns (rc, stopped tool).
    agy's rows carry no time, so each JSON row is written with a `timestamp` for the watcher's progress reading."""
    proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE)
    stopped = None
    with open(stream_path, "wb") as out:
        for raw in proc.stdout:
            out.write(_stamped(raw))
            out.flush()
            stopped = ungoverned_start(raw.decode("utf-8", "replace"), servers)
            if stopped:
                _kill_tree(proc)
                break
    proc.stdout.close()
    rc = proc.wait()
    return (STOPPED_RC if stopped else rc), stopped


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _inside(path, roots):
    """True when `path` lies under a root and not inside a nested worktree under it."""
    target = _norm(path)
    for root in roots:
        base = _norm(root)
        if target == base or target.startswith(base.rstrip(os.sep) + os.sep):
            nested = os.path.join(base, ".claude", "worktrees") + os.sep
            if not target.startswith(nested):
                return True
    return False


def _own_spill(path, conversation):
    """True when `path` lies in this run's own `brain/<conversation>/.system_generated` dir, where agy spills a
    large tool output and reads it back. Another conversation's spill stays outside."""
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep)]
    conv = (conversation or "").lower()
    return bool(conv) and any(parts[i:i + 3] == ["brain", conv, ".system_generated"] for i in range(len(parts) - 2))


def _paths(parameters):
    out = []
    for key, value in (parameters or {}).items():
        if isinstance(value, str) and "path" in key.lower() and value:
            out.append(value)
    return out


def _events(text):
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _served(init_model, model):
    """The passed `--model` when agy echoed it, with or without an `-<effort>` suffix, else agy's own id."""
    if not init_model or init_model == model or (model and init_model.startswith(model + "-")):
        return model or init_model
    return init_model


def parse(text, model, rc, allowed_names, roots, schema=None):
    events = _events(text)
    init = next((e.get("init") for e in events if e.get("event") == "init"), None) or {}
    conversation = next((e.get("conversation_id") for e in events if e.get("event") == "init"), None)
    result = next((e.get("result") for e in reversed(events) if e.get("event") == "result"), None) or {}
    steps = {}
    for event in events:
        step = _step(event)
        if step:
            steps[step.get("step_index")] = step
    granted = set(allowed_names)
    outside = []
    for step in steps.values():
        tool = step.get("tool_name")
        state = step.get("state")
        if tool not in GOVERNED:
            outside.append({"tool": tool, "state": state, "why": "ungoverned tool"})
            continue
        if state != "DONE":
            continue
        if tool not in granted:
            outside.append({"tool": tool, "state": state, "why": "not granted to this kind"})
        for path in _paths((step.get("tool_info") or {}).get("parameters")):
            if not _inside(path, roots) and not (tool in READ and _own_spill(path, conversation)):
                outside.append({"tool": tool, "state": state, "why": "outside the root", "path": path})
    answer = (result.get("response") or "").strip()
    status = result.get("status")
    failed = status != "SUCCESS" or rc != 0 or not answer or bool(outside)
    structured = None
    if schema is not None and not failed:
        try:
            structured = json.loads(answer)
            validate(structured, schema)
        except ValueError as exc:
            failed, structured = True, None
            answer = f"Schema validation failed: {exc}\n{answer}"
    usage = result.get("usage") or {}
    served = _served(init.get("model"), model)
    model_usage = {served: {"canonicalModel": served,
                            "inputTokens": int(usage.get("input_tokens") or 0),
                            "cacheReadInputTokens": int(usage.get("cache_read_tokens") or 0),
                            "outputTokens": int(usage.get("output_tokens") or 0)
                            + int(usage.get("thinking_tokens") or 0)}} if result else {}
    if not answer:
        answer = f"agy returned no answer (status {status}, exit {rc})"
    return {"type": "result", "subtype": "error" if failed else "success", "is_error": failed,
            "result": answer, "structured_output": structured, "modelUsage": model_usage,
            "outsideProfile": outside, "agyStatus": status}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agy_seat.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("granted")
    g.add_argument("--resolved", default="")
    g.add_argument("--disallowed", default="")
    sv = sub.add_parser("servers")
    sv.add_argument("--profile", required=True)
    s = sub.add_parser("settings")
    s.add_argument("--builtins", default="")
    s.add_argument("--servers", default=",".join(READ_ONLY_SERVERS))
    a = sub.add_parser("allowed")
    a.add_argument("--builtins", default="")
    a.add_argument("--servers", default=",".join(READ_ONLY_SERVERS))
    r = sub.add_parser("run")
    r.add_argument("--stream", required=True)
    r.add_argument("--servers", default=",".join(READ_ONLY_SERVERS))
    r.add_argument("agy", nargs=argparse.REMAINDER)
    p = sub.add_parser("parse")
    p.add_argument("--stream", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--rc", type=int, required=True)
    p.add_argument("--allowed", default="")
    p.add_argument("--root", required=True)
    p.add_argument("--add-dir", action="append", default=[])
    p.add_argument("--schema")
    sub.add_parser("bin")
    args = ap.parse_args(argv)
    if args.cmd == "bin":
        print(agy_bin())
    elif args.cmd == "granted":
        print(",".join(granted(_builtins(args.resolved), _builtins(args.disallowed))))
    elif args.cmd == "servers":
        print(",".join(granted_servers(json.loads(args.profile).get("mcpServers") or [])))
    elif args.cmd == "settings":
        print(json.dumps(settings(_builtins(args.builtins), _builtins(args.servers))))
    elif args.cmd == "allowed":
        print(",".join(allowed(_builtins(args.builtins), _builtins(args.servers))))
    elif args.cmd == "run":
        cmd = args.agy[1:] if args.agy[:1] == ["--"] else args.agy
        if not cmd:
            ap.error("run needs the agy argv after --")
        rc, stopped = run(args.stream, cmd, servers=_builtins(args.servers))
        if stopped:
            print(f"[sidecar] agy started `{stopped}`, a tool no settings rule reaches; stopped the run.",
                  file=sys.stderr)
        return rc
    else:
        with open(args.stream, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        schema = None
        if args.schema:
            with open(args.schema, encoding="utf-8") as fh:
                schema = json.load(fh)
        envelope = parse(text, args.model, args.rc, _builtins(args.allowed), [args.root] + args.add_dir, schema)
        sys.stdout.buffer.write((json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
