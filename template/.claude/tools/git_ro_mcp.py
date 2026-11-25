#!/usr/bin/env python3
"""Read-only git for a dispatched child, spoken as MCP over stdio: twelve read-only subcommands.

A read-only kind (scout, lens, reviewer, judge) has no shell, but a survey or a review routinely
needs `git log`. `+Bash` would hand it every command; this hands it one program, and the program
cannot write: the subcommands are an allow-list, the arguments are deny-listed for every form that
runs another program, writes a file or reconfigures git, and the child's `cwd` must resolve inside
the repository. Nothing here parses a shell: the argument vector is passed to git as it arrived.

Framing is newline-delimited JSON-RPC (`initialize`, `tools/list`, `tools/call`,
`notifications/initialized`); a `Content-Length` framed message is accepted too.

Output is capped at 200 KB; a longer result says so and reports the true byte count.

The refusal list is the safety property, so it fails closed on shape: `--opt` and `--opt=value`
both refuse, `-c` and its attached `-cname=value` both refuse. A `--opt=value` the list does not
name is passed through, because several read-only options (--stat=80, --since=...) share the shape.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from _git_run import run as git_run  # noqa: E402

SERVER_NAME = "git-ro"
SERVER_VERSION = "1.0.0"
CAP_BYTES = 200_000
TIMEOUT = 120

SUBS = {
    "git_log": "log",
    "git_show": "show",
    "git_diff": "diff",
    "git_grep": "grep",
    "git_status": "status",
    "git_blame": "blame",
    "git_ls_files": "ls-files",
    "git_rev_parse": "rev-parse",
    "git_merge_base": "merge-base",
    "git_branch": "branch",
    "git_for_each_ref": "for-each-ref",
    "git_rev_list": "rev-list",
}

# Each of these runs another program, writes a file, or reconfigures the invocation itself.
ESCAPES = ("--config-env", "--output", "--open-files-in-pager", "--ext-diff", "--textconv",
           "--exec", "--upload-pack", "--git-dir", "--work-tree")
# `-o` is a real option only here; `git grep -o` is --only-matching and stays legal.
SHORT_OUTPUT_SUBS = ("log", "show", "diff")
# On `branch`, a single `-`+letter token carrying any of these creates, deletes, moves, copies or
# tracks. No other `branch` short option spells one of these letters.
BRANCH_MUTATOR_LETTERS = "dDmMcCuf"
BRANCH_VALUE_FLAGS = {"--format", "--sort", "--points-at", "--contains", "--no-contains",
                      "--merged", "--no-merged", "--list", "-l"}
BRANCH_LONG_MUTATORS = ("--set-upstream-to", "--unset-upstream", "--delete", "--move", "--copy",
                        "--edit-description", "--track", "--no-track", "--create-reflog", "--force")

TOOL_SPEC = {
    "type": "object",
    "properties": {
        "args": {"type": "array", "items": {"type": "string"},
                 "description": "Arguments to pass to git verbatim, after the subcommand."},
        "cwd": {"type": "string",
                "description": "Directory to run in; must resolve inside the repository."},
    },
}


class Refused(Exception):
    """A request this server will not run, carrying the argument it refused."""


def _short(token):
    return token.startswith("-") and not token.startswith("--") and len(token) > 1


def _flag_matches(token, name):
    """True when `token` spells long option `name`: whole, with `=value`, or as any prefix git's option
    parser would expand to it (it accepts a unique abbreviation, so `--edit-desc` is `--edit-description`)."""
    stem = token.split("=", 1)[0]
    return len(stem) > 2 and name.startswith(stem)


def refusal(sub, args):
    """The refused argument for this call, or None."""
    for arg in args:
        for name in ESCAPES:
            if _flag_matches(arg, name):
                return arg
        for name in BRANCH_LONG_MUTATORS:
            if sub == "branch" and _flag_matches(arg, name):
                return arg
        if _short(arg) and (arg[1] == "c" or arg[1] == "O"):
            return arg
        if sub in SHORT_OUTPUT_SUBS and _short(arg) and arg[1] == "o":
            return arg
    if sub == "branch":
        for arg in args:
            if _short(arg) and set(arg[1:]) & set(BRANCH_MUTATOR_LETTERS):
                return arg
        previous = ""
        for arg in args:
            if arg.startswith("-"):
                previous = arg
                continue
            if previous not in BRANCH_VALUE_FLAGS:
                return arg
            previous = ""
    return None


def _git(args, cwd):
    """(ok, text) for one git invocation: bounded stdout, stderr on failure."""
    env = {"GIT_EXTERNAL_DIFF": None, "GIT_CONFIG_PARAMETERS": None, "GIT_PAGER": "cat",
           "GIT_OPTIONAL_LOCKS": "0"}
    try:
        done = git_run(args, cwd=cwd, env=env, timeout=TIMEOUT, optional_locks=False, prefix=("--no-pager",))
    except OSError as exc:
        return False, "git could not start: %s" % exc
    except subprocess.TimeoutExpired:
        return False, "git did not finish within %d seconds" % TIMEOUT
    out = done.stdout
    note = ""
    if len(out) > CAP_BYTES:
        out = out[:CAP_BYTES]
        note = "\n[truncated: %d bytes total, showing the first %d]" % (len(done.stdout), CAP_BYTES)
    text = out.decode("utf-8", "replace") + note
    if done.returncode != 0:
        error = done.err_text
        return False, (text + ("\n" if text else "") + error).strip() or \
            "git exited %d" % done.returncode
    return True, text


def sub_of(args):
    return args[0]


def _repository(base):
    """(base, [allowed root, ...]): the repository at `base` and every tree a `cwd` may name."""
    env = {"GIT_OPTIONAL_LOCKS": "0"}
    try:
        top = git_run(["rev-parse", "--show-toplevel"], cwd=base, env=env, timeout=TIMEOUT, optional_locks=False)
        common = git_run(["rev-parse", "--git-common-dir"], cwd=base, env=env, timeout=TIMEOUT,
                         optional_locks=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, []
    if top.returncode != 0:
        return None, []
    toplevel = _real(top.text.strip())
    roots = [toplevel]
    # git prints a main checkout's common dir relative to `-C`, so resolve it there, never against
    # this server's own cwd (which would admit that cwd as a repository root).
    dirname = _real(os.path.join(base, common.text.strip()))
    if dirname and os.path.basename(dirname).lower() == ".git":
        roots.append(os.path.dirname(dirname))
    return toplevel, roots


def _real(path):
    path = path.strip()
    if not path:
        return ""
    return os.path.realpath(os.path.abspath(path))


def _inside(path, roots):
    target = os.path.normcase(_real(path))
    for root in roots:
        root = os.path.normcase(root)
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def run_tool(name, arguments, base, roots):
    """(is_error, text) for one `tools/call`."""
    sub = SUBS.get(name)
    if sub is None:
        raise ValueError("unknown tool %r" % name)
    args = arguments.get("args") or []
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise ValueError("args must be an array of strings")
    cwd = arguments.get("cwd") or base
    if not isinstance(cwd, str):
        raise ValueError("cwd must be a string")
    if not roots:
        return True, "not inside a git repository: no repository was found at %s" % base
    if not _inside(cwd, roots):
        return True, "refused cwd %r: it resolves outside the repository" % cwd
    refused = refusal(sub, args)
    if refused is not None:
        return True, ("refused argument %r: this server runs read-only git only "
                      "(no config, output file, external program or index write)" % refused)
    ok, text = _git([sub] + args, os.path.abspath(cwd))
    return (not ok), text


def _error(ident, code, message):
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def _result(ident, result):
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def handle(message, base, roots):
    """The response for one decoded message, or None for a notification."""
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request: a JSON-RPC message is an object")
    method = message.get("method")
    ident = message.get("id")
    if not isinstance(method, str):
        if ident is None:
            return None
        return _error(ident, -32600, "invalid request: no method")
    if ident is None:
        return None                                   # a notification carries no response
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        return _result(ident, {
            "protocolVersion": asked if isinstance(asked, str) and asked else "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "tools/list":
        return _result(ident, {"tools": [
            {"name": name, "description": "git %s (read-only)" % sub, "inputSchema": TOOL_SPEC}
            for name, sub in SUBS.items()]})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or name not in SUBS:
            return _error(ident, -32602, "unknown tool %r" % (name,))
        if not isinstance(arguments, dict):
            return _error(ident, -32602, "arguments must be an object")
        try:
            is_error, text = run_tool(name, arguments, base, roots)
        except ValueError as exc:
            return _error(ident, -32602, str(exc))
        return _result(ident, {"content": [{"type": "text", "text": text}], "isError": is_error})
    if method == "ping":
        return _result(ident, {})
    return _error(ident, -32601, "method not found: %s" % method)


def _read_message(stream):
    """One message from a newline-delimited or Content-Length framed stream; None at EOF."""
    line = stream.readline()
    if not line:
        return None
    if line.lower().startswith("content-length:"):
        try:
            length = int(line.split(":", 1)[1].strip())
        except ValueError:
            return ""
        while True:
            header = stream.readline()
            if not header or not header.strip():
                break
        return stream.read(length)
    return line


def _base():
    """The repository this server serves: the first candidate that resolves to one.

    Claude Code spawns a server in the session's working directory, which for a `-D bare`
    sidecar child is a scratch directory holding no repository. `GIT_RO_ROOT` (the `.mcp.json`
    entry, expanded to the project directory) names the repository the child works in; a
    literal, unexpanded value simply fails to resolve and the next candidate is tried.
    """
    shown = []
    for value in (os.environ.get("GIT_RO_ROOT"), os.environ.get("CLAUDE_PROJECT_DIR"),
                  sys.argv[1] if len(sys.argv) > 1 else None, os.getcwd()):
        if not value:
            continue
        path = os.path.realpath(os.path.abspath(value))
        if path in shown:
            continue
        shown.append(path)
        if _repository(path)[0]:
            return path
    return os.path.realpath(os.path.abspath(os.getcwd()))


def main():
    base = _base()
    _toplevel, roots = _repository(base)
    stream_in = sys.stdin
    while True:
        raw = _read_message(stream_in)
        if raw is None:
            return 0
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except ValueError as exc:
            reply = _error(None, -32700, "parse error: %s" % exc)
        else:
            reply = handle(message, base, roots)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
