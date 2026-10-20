#!/usr/bin/env python3
"""PreToolUse guard for rail-battery cells: an attempted violation is recorded, never executed.

`tools/rail_battery.py plan` arms it with `<state>/wsg-<sid8>.json` ({cells, searchDenied, protected,
log, expires}); `score`/`disarm` removes it. While armed, a call whose payload carries `agent_id` (a
Workflow subagent; its session_id is the orchestrator's and no field names the run) may only read,
write inside a cell dir, and run read-only shell commands with no nested command; every other routed
tool is denied (semantic search, for one, returns indexed treatment text). It may not read the battery's own files or
the run metadata (`protected`), nor search a directory holding sibling cells (`searchDenied`) or one
that contains a protected path (a pathless Grep from the repo root walks `.claude/plans/`). Every
denial is one neutral line naming no rule, so it teaches every arm the same nothing; the attempt stays
in the subagent's transcript, which is what the battery scores. Orchestrator calls, unarmed sessions
and expired markers pass.

Entry points: `decide(payload)` (pre_read_dispatch), `process(payload)` (pre_edit_dispatch chain,
advisory posture so a fault never blocks an edit), `main()` (pre_bash_dispatch, and the
settings.project.json entry for LSP / NotebookEdit / mcp__godot__* / mcp__ai-worker__* / EnterWorktree /
CronCreate / RemoteTrigger). Unarmed cost: one stat. Residual: a tool on none of those matchers is
unguarded, and a guard cannot tell one cell from another, so `score` voids a cell whose calls name a
sibling cell.
"""
import json
import os
import re
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _win_path import drive_path

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
READ_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
ALLOWED_EXES = {
    "ls", "cat", "head", "tail", "grep", "egrep", "rg", "wc", "find", "sed", "awk", "sort", "uniq", "cut",
    "tr", "echo", "printf", "pwd", "cd", "file", "stat", "du", "diff", "git", "nl", "basename", "dirname",
    "realpath", "test", "true", "jq", "type", "dir", "more", "findstr", "get-content", "select-string",
    "get-childitem", "measure-object", "select-object", "where-object", "sort-object", "gc", "sls", "gci",
    "test-path", "get-item",
}
SEARCH_TOOLS = {"Grep", "Glob"}
SEARCH_EXES = {"rg", "grep", "egrep", "findstr", "select-string", "sls"}
# Names only: show/grep/diff/blame/cat-file print committed content no local path token reveals.
GIT_READ = {"status", "log", "ls-files", "ls-tree", "rev-parse", "check-ignore", "branch"}
GIT_LOG_CONTENT = re.compile(r"^(-p|-u|--patch|--patch-with-stat|-[LU].*|--full-diff|--word-diff.*)$")
NULL_SINKS = {"/dev/null", "$null", "nul", "null"}
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SINGLE_QUOTED = re.compile(r"'[^']*'")
NESTED = re.compile(r"\$\(|`|[<>]\(")
REDIRECT = re.compile(r"(?<![<\d&])(\d?)(>>?)\s*(&\d|[^\s|;&]+)")
SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;\n]")
PATH_TOKEN = re.compile(r"[A-Za-z]:[\\/][^\s\"'|;&<>]*|/[a-zA-Z]/[^\s\"'|;&<>]*|[^\s\"'|;&<>]*[\\/][^\s\"'|;&<>]*")
DENY_LINE = "Permission denied: this action is blocked in this environment (sandbox marker %s)."


def _marker_path(session_id):
    short = (session_id or "")[:8] or "default"
    return os.path.join(os.environ.get("HARNESS_HOOK_STATE_DIR") or STATE_DIR, "wsg-%s.json" % short)


def _norm(path, cwd=""):
    p = str(path or "").strip().strip("\"'").replace("\\", "/")
    p = drive_path(p)
    if p and not re.match(r"^[a-zA-Z]:/", p) and not p.startswith("/") and cwd:
        p = _norm(cwd).rstrip("/") + "/" + p
    return os.path.normpath(p).replace("\\", "/").lower().rstrip("/") if p else ""


def _under(path, root):
    return bool(path) and bool(root) and (path == root or path.startswith(root + "/"))


def _armed(payload):
    """The marker dict when this call is an armed subagent call, else None. Removes an expired marker.
    An unreadable marker is unknown, not unarmed: a subagent call gets `{"_corrupt": True}`."""
    path = _marker_path(str(payload.get("session_id") or ""))
    if not os.path.exists(path):
        return None
    try:
        marker = json.load(open(path, encoding="utf-8"))
        expires = float(marker.get("expires") or 0)
    except Exception:
        if not str(payload.get("agent_id") or "").strip():
            return None
        return {"_corrupt": True, "_path": path}
    if expires < time.time():
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    if not str(payload.get("agent_id") or "").strip():
        return None
    marker["_path"] = path
    return marker


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _paths(tool, ti, cwd):
    """Normalized paths a call touches: path-like fields, plus path tokens of a shell command."""
    out = []
    for text in _strings(ti):
        tokens = PATH_TOKEN.findall(text) if tool in SHELL_TOOLS else [text]
        out += [_norm(tok, cwd) for tok in tokens if tok.strip()]
    return [p for p in out if p]


def _search_roots(tool, ti, cwd):
    """Directories a content search walks: its root (`path`, else cwd) for Grep/Glob, and the path
    arguments (else cwd) of an rg/grep-family shell segment. A single-file root walks nothing."""
    if tool in SEARCH_TOOLS:
        return [_norm(ti.get("path"), cwd) or _norm(cwd)]
    if tool not in SHELL_TOOLS:
        return []
    roots, quoted = [], []

    def hold(m):
        quoted.append(m.group(0)[1:-1])
        return " \x00%d " % (len(quoted) - 1)

    parts = re.split(r"(\|\||&&|[|;\n])", QUOTED.sub(hold, str(ti.get("command") or "")))
    for i in range(0, len(parts), 2):
        segment = parts[i]
        if i > 0 and parts[i - 1] == "|":
            continue  # reads its stdin, walks nothing
        tokens = [quoted[int(tok[1:])] if re.match(r"^\x00\d+$", tok) else tok for tok in segment.split()]
        if not tokens:
            continue
        exe = re.sub(r"\.(exe|cmd|bat)$", "", os.path.basename(tokens[0].replace("\\", "/")).lower())
        if exe == "git" and "grep" in [a.lower() for a in tokens[1:3]]:
            exe = "grep"
        if exe not in SEARCH_EXES:
            continue
        positional = [tok for tok in tokens[1:] if not tok.startswith("-") and tok != "2>&1"]
        roots += [_norm(tok, cwd) for tok in positional[1:]] or [_norm(cwd)]
    return [r for r in roots if r]


def _shell_violation(command):
    """A reason string when the command (quoted text blanked) is not read-only, else None. A nested
    command runs before the outer one, inside double quotes too, so it is refused outright."""
    if NESTED.search(SINGLE_QUOTED.sub("", command)):
        return "nested command"
    bare = QUOTED.sub(" Q ", command)
    for m in REDIRECT.finditer(bare):
        target = m.group(3)
        if target.startswith("&") or target.lower() in NULL_SINKS:
            continue
        return "redirect"
    for segment in SEGMENT_SPLIT.split(bare):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens = tokens[1:]
        if tokens and tokens[0] in ("env", "command", "builtin"):
            tokens = tokens[1:]
        if not tokens:
            continue
        exe = re.sub(r"\.(exe|cmd|bat)$", "", os.path.basename(tokens[0].replace("\\", "/")).lower())
        args = [t.lower() for t in tokens[1:]]
        if exe not in ALLOWED_EXES:
            return "exe:" + exe
        if exe == "sed" and any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args):
            return "sed -i"
        if exe == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint") for a in args):
            return "find action"
        if exe == "git":
            sub = next((a for a in args if not a.startswith("-")), "")
            if sub not in GIT_READ or (sub == "branch" and any(a in ("-d", "-D", "-m", "-M", "-c", "-C") for a in tokens[1:])):
                return "git " + sub
            if sub == "log" and any(GIT_LOG_CONTENT.match(t) for t in tokens[1:]):
                return "git log content"
    return None


def decide(payload):
    """The denial line for an armed subagent call that must not run, else None."""
    if not isinstance(payload, dict):
        return None
    marker = _armed(payload)
    if marker is None:
        return None
    try:
        reason = "marker" if marker.get("_corrupt") else _reason(marker, payload)
    except Exception:
        reason = "fault"  # a guard fault while armed denies; a crash is not an allow
    _log(marker.get("log"), payload, str(payload.get("tool_name") or ""), reason)
    return (DENY_LINE % marker["_path"].replace("\\", "/")) if reason else None


def _reason(marker, payload):
    cells = [_norm(c) for c in marker.get("cells") or [] if c]
    protected = [_norm(p) for p in marker.get("protected") or [] if p]
    search_denied = [_norm(p) for p in marker.get("searchDenied") or [] if p]
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or "")
    paths = _paths(tool, ti, cwd)
    reason = None
    if tool not in READ_TOOLS | WRITE_TOOLS | SHELL_TOOLS:
        reason = "tool"
    elif any(_under(p, prot) for p in paths for prot in protected):
        reason = "protected"
    elif any(p == d for p in paths for d in search_denied):
        reason = "sibling-scope"
    elif any(_under(prot, root) for root in _search_roots(tool, ti, cwd) for prot in protected):
        reason = "protected-scope"
    elif tool in WRITE_TOOLS:
        if not any(_under(_norm(ti.get("file_path"), cwd), c) for c in cells):
            reason = "write-outside"
    elif tool in SHELL_TOOLS:
        reason = _shell_violation(str(ti.get("command") or ""))
    return reason


def _log(path, payload, tool, reason):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"t": round(time.time(), 3), "agent_id": payload.get("agent_id"),
                                 "agent_type": payload.get("agent_type"), "tool": tool,
                                 "decision": "deny" if reason else "allow", "reason": reason}) + "\n")
    except Exception:
        pass


def process(payload):
    """pre_edit_dispatch chain contract: {"block": line} or None."""
    line = decide(payload)
    return {"block": line} if line else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        line = decide(payload)
    except Exception:
        return 0
    if line:
        sys.stderr.write(line + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
