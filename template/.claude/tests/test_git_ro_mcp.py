#!/usr/bin/env python3
"""Proof for tools/git_ro_mcp.py: the read-only git MCP server read-only kinds carry.

Cases, each named for the decision it pins:
  1. the advertised tools are the twelve read-only subcommands, each taking `args` and `cwd`;
  2. every mutating or escaping argument form refuses, naming the argument it refused;
  3. a legal read runs and its output is the bounded stdout: `git_log --oneline -3` is three lines;
  4. `git_status` leaves `.git/index` untouched (no optional lock, so no index refresh);
  5. a `cwd` outside the repository refuses;
  6. a malformed request returns a JSON-RPC error instead of crashing the server.

The server is driven as a real child over stdio, never imported: the transport is half the feature.
"""
import atexit
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.path.dirname(os.path.dirname(HERE))
SERVER = os.path.join(CHECKOUT, ".claude", "tools", "git_ro_mcp.py")


def plant_repo():
    """The repository the server serves: three commits in a temp folder, never this checkout."""
    folder = tempfile.mkdtemp(prefix="git_ro_repo_")
    atexit.register(shutil.rmtree, folder, onerror=lambda func, path, _exc: (
        os.chmod(path, stat.S_IWRITE), func(path)))
    subprocess.run(["git", "-C", folder, "init", "-q"], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    for number in (1, 2, 3):
        with open(os.path.join(folder, "note.txt"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("commit %d\n" % number)
        subprocess.run(["git", "-C", folder, "add", "note.txt"], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
        subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "-m", "commit %d" % number], check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return folder


ROOT = plant_repo()

FAIL = 0
RAN = 0


def check(label, condition, detail=""):
    global FAIL, RAN
    RAN += 1
    if condition:
        print("OK   %s" % label)
    else:
        FAIL = 1
        print("FAIL %s%s" % (label, (": " + str(detail)) if detail else ""))


def git(*args):
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


class Child:
    """One server process; `call` writes one JSON-RPC message and reads one response line."""

    def __init__(self, cwd=ROOT, env_over=None):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = ROOT
        env.update(env_over or {})
        self.proc = subprocess.Popen([sys.executable, SERVER], cwd=cwd, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     bufsize=1)

    def send(self, payload):
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()

    def recv(self):
        line = self.proc.stdout.readline()
        if not line:
            return None
        return json.loads(line)

    def call(self, ident, method, params=None):
        message = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            message["params"] = params
        self.send(json.dumps(message))
        return self.recv()

    def tool(self, name, args, cwd=None):
        arguments = {"args": args}
        if cwd is not None:
            arguments["cwd"] = cwd
        message = self.call(99, "tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in message["result"].get("content", []))
        return message["result"].get("isError", False), text

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    if not os.path.isfile(SERVER):
        check("server exists", False, SERVER)
        return 1

    child = Child()
    try:
        init = child.call(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                            "clientInfo": {"name": "proof", "version": "0"}})
        check("initialize echoes the client protocol version",
              (init or {}).get("result", {}).get("protocolVersion") == "2024-11-05", init)
        child.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        listed = child.call(2, "tools/list", {})
        tools = {t["name"]: t for t in listed["result"]["tools"]}
        expected = {"git_log", "git_show", "git_diff", "git_grep", "git_status", "git_blame",
                    "git_ls_files", "git_rev_parse", "git_merge_base", "git_branch",
                    "git_for_each_ref", "git_rev_list"}
        check("tools/list advertises exactly the twelve read-only subcommands",
              set(tools) == expected, sorted(set(tools) ^ expected))
        schema = tools.get("git_log", {}).get("inputSchema", {})
        check("each tool takes args (string array) and cwd",
              schema.get("properties", {}).get("args", {}).get("type") == "array"
              and schema.get("properties", {}).get("cwd", {}).get("type") == "string", schema)

        refused = [
            ("git_log", ["-c", "core.pager=sh", "-1"]),
            ("git_log", ["--config-env=core.pager=EVIL", "-1"]),
            ("git_log", ["--output=out.txt", "-1"]),
            ("git_log", ["-oout.txt", "-1"]),
            ("git_show", ["--output=out.txt"]),
            ("git_diff", ["-o", "out.txt"]),
            ("git_log", ["-O", "pager", "-1"]),
            ("git_log", ["--open-files-in-pager=sh", "-1"]),
            ("git_diff", ["--ext-diff"]),
            ("git_show", ["--textconv"]),
            ("git_log", ["--exec=sh", "-1"]),
            ("git_ls_files", ["--upload-pack=sh"]),
            ("git_log", ["--git-dir=/tmp/other", "-1"]),
            ("git_log", ["--work-tree=/tmp/other", "-1"]),
            # git's option parser accepts any unique prefix of a long option
            ("git_grep", ["--open-files=sh", "x"]),
            ("git_grep", ["--textc", "x"]),
            ("git_log", ["--outp=out.txt", "-1"]),
        ]
        for name, args in refused:
            is_error, text = child.tool(name, args)
            check("refuses %s %s naming the argument" % (name, " ".join(args)),
                  is_error and (args[0].split("=")[0] in text), (is_error, text[:200]))
        for args in (["-D", "x"], ["-d", "x"], ["-m", "old", "new"], ["-M", "old", "new"],
                     ["-c", "old", "new"], ["-C", "old", "new"],
                     ["--set-upstream-to=origin/main"], ["-u", "origin/main"],
                     ["--unset-upstream"], ["newbranch"],
                     ["--edit-desc"], ["--set-upstream-t=origin/main"], ["--unset-up"]):
            is_error, text = child.tool("git_branch", args)
            check("git_branch refuses %s" % " ".join(args),
                  is_error and (args[0].split("=")[0] in text), (is_error, text[:200]))

        is_error, text = child.tool("git_ls_files", ["--others", "--exclude-standard", "--", "no-such-path"])
        check("a legal option that only shares letters with an escape still runs",
              not is_error, (is_error, text[:200]))

        is_error, text = child.tool("git_log", ["--oneline", "-3"])
        lines = [l for l in text.strip().splitlines() if l.strip()]
        check("git_log --oneline -3 returns three lines", not is_error and len(lines) == 3,
              (is_error, text[:300]))

        index = git("rev-parse", "--git-path", "index").stdout.strip()
        index_path = index if os.path.isabs(index) else os.path.join(ROOT, index)
        before = os.stat(index_path).st_mtime_ns
        child.tool("git_status", [])
        after = os.stat(index_path).st_mtime_ns
        check("git_status leaves .git/index untouched", before == after, (before, after))

        with tempfile.TemporaryDirectory(prefix="git-ro-outside-") as outside:
            is_error, text = child.tool("git_log", ["-1"], cwd=outside)
            check("a cwd outside the repository refuses", is_error and "cwd" in text, text[:200])

        child.send("{not json")
        reply = child.recv()
        check("a malformed request returns a JSON-RPC error, not a crash",
              reply is not None and reply.get("error", {}).get("code") == -32700, reply)
        is_error, text = child.tool("git_rev_parse", ["--show-toplevel"])
        check("the server still answers after a malformed request", not is_error and text.strip(),
              text[:200])
    finally:
        child.close()

    # A `-D bare` sidecar child runs in a scratch directory holding no repository, so the server
    # is told the repository by `GIT_RO_ROOT` (the `.mcp.json` env) and must serve it, refusing a
    # cwd outside it.
    with tempfile.TemporaryDirectory(prefix="git-ro-scratch-") as scratch:
        away = Child(cwd=scratch, env_over={"GIT_RO_ROOT": ROOT})
        try:
            is_error, text = away.tool("git_log", ["--oneline", "-3"])
            check("GIT_RO_ROOT names the repository from a scratch cwd",
                  not is_error and len(text.strip().splitlines()) == 3, (is_error, text[:200]))
            is_error, text = away.tool("git_log", ["-1"], cwd=scratch)
            check("GIT_RO_ROOT still refuses a cwd outside the repository",
                  is_error and "cwd" in text, text[:200])
        finally:
            away.close()

    print("\n%d case(s), %s" % (RAN, "FAILED" if FAIL else "passed"))
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
