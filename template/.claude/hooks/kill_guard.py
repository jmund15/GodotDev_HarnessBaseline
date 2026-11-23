#!/usr/bin/env python3
"""Guard irreversible stops (class floor, T1): `_floor.py` runs `check(payload)` for Bash, PowerShell and
TaskStop calls; a fault denies the call.

Bash/PowerShell kills resolve each PID before acting and refuse harness-critical processes. Every stage and each body
it runs is read through `_shell_lex`, and each kill's options as its tool reads them (`kill -s SIG -- <pid>...`,
`taskkill /PID`, `-pid`, `//PID`, `killall --regexp`, `Stop-Process -n`, any order).A kill addressed
by name or pattern (`pkill`, `killall`, `taskkill /IM` or /FI, `Stop-Process -Name` or -Id, `Get-Process <n> | Stop-Process`,
`wmic process where ... delete`, a Win32_Process Terminate, and `kill $(pgrep ...)`, `pgrep ... | xargs kill` or
`$(pidof ...)`) resolves to the live processes it would stop, read from the same process table the
KILL_GUARD_PROCESS_TABLE seam plants, and is refused when any match is protected. It is also refused when the table
cannot be read or the pattern cannot be resolved (an invalid regex, a Where-Object or unparsed /FI filter, an
`xargs kill` fed by anything but pgrep), or when the call does not name the pids it stops (a variable or a
substitution operand, `_unnamed_pid_kills`). A kill whose every match is unprotected passes. Not covered:
`.Kill()` and other in-language kills, and kills inside a script file the command runs.

TaskStop decides on facts it observes in the transcript, never on owner wording (a stop verb, a keep word, an
AskUserQuestion answer): it passes a task whose completion notice is already there (it stops nothing) and an
unpaid task this session's main thread launched. Running paid work this session launched (a Workflow, an Agent,
a shell running the seat launcher) returns `ask`: the stop is the owner's call (CLAUDE.core.md §Shell Discipline),
and reading the owner's words for consent failed repeatedly, so the permission prompt asks the owner directly.
A task this session did not launch (a subagent's, a peer's) is denied: its own launcher or session stops it.

A second floor guard in the same hook (class floor: it protects peer sessions) denies a Bash/PowerShell command
that executes a terminal-window close by handle, title or host process (`window_close_verdict`). It has no
text override: a reason the agent writes into the call proves no ownership of the window.

WHY THIS EXISTS: a process list shows command tails TRUNCATED, and a session that matched the wrong row of one
(v1's `shell_census.py`, which handed over `taskkill /PID <pid> /T /F`) killed its own paid fan-out run.

A stop is irreversible and the display is lossy, so this gate fails CLOSED (instruction_quality
section 16). Bash/PowerShell bypass, per target: `HARNESS_ALLOW_KILL=<pid> taskkill /PID <pid> /T /F`. It covers
a PID only, never a name or pattern kill. TaskStop has no text override.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _background_tasks  # noqa: E402
import _git_run  # noqa: E402
import _msys  # noqa: E402
import _shell_lex  # noqa: E402
from _win_path import native_path  # noqa: E402

# Killing any of these takes down work that cannot be resumed from where it stopped: a gate/suite run, a billed
# benchmark cell, or a peer's whole session.
PROTECTED = (
    (r"regression_gate|gate_chain|verify\.ps1", "a gate/verification run"),
    (r"vstest|dotnet\s+test", "a test run"),
    # NOT \bclaude\b — that matches `.claude/` (present in nearly every harness command) and
    # `claude-gpt`. Match the EXECUTABLE: `claude.exe`, or `claude` standing as the command word.
    (r"(?:^|[\s/\\\"])claude\.exe|(?:^|[\s/\\\"])claude(?:\s|$)",
     "a Claude Code session — possibly a peer's"),
    (r"claude-code-proxy", "a transport proxy other sessions may be routing through"),
    # Bound to the command word: a grid named as an argument (`grep run_grid.sh notes.md`) is data.
    (r"(?:^|[;&|(\n]\s*)(?:(?:nohup|exec|setsid)\s+)?(?:(?:bash|sh|pwsh|powershell)\s+(?:-\S+\s+)*)?[\"']?"
     r"(?:\S*[/\\])?run_grid(?:\.snapshot[\w.-]*)?\.sh",
     "a benchmark grid — its in-flight cell is a billed arm"),
)
# Local scripts a shell command runs; `window_close_verdict` reads the ones under `.claude/scratch/`.
# Glob characters are excluded: `find -name "*.py"` names a pattern, not a script to read.
SCRIPT_REF_RE = re.compile(r"[^\s\"'|;&<>()=*?\[\]]+\.(?:sh|py|ps1|js|cmd|bat)(?![\w.])", re.I)
# The process-table read runs inside the hook's 20 s settings budget (HOOK_BUDGET_S below).
TABLE_TIMEOUT_S = 10
MAX_SCRIPT_BYTES = 512 * 1024
# settings.json gives this hook 20 s. Every PID lookup runs inside one budget under it; a lookup the
# budget cannot afford resolves nothing, so the kill fails closed instead of the hook timing out open.
HOOK_BUDGET_S = 15.0
SUBPROCESS_TIMEOUT_S = 5
NOT_LAUNCHED = ("BLOCKED TaskStop (CLAUDE.core.md §Shell Discipline) — no launch of task %s by this session's main thread is in the transcript "
                "(a peer's or a subagent's task, or a transcript the hook cannot read). Stop it from the session or launcher that owns it.")
PAID_RUNNING = ("Stop running paid work %s (%s)? Its finished agents' results stay on disk; its in-flight work is lost. "
                "A stop of a running dispatch is the owner's call (CLAUDE.core.md §Shell Discipline).")


def _launch_of(transcript_path, task_id):
    """(tool name, tool input, launch timestamp or None) of the main-session call that launched background
    task `task_id`, else None.

    The launch is the tool_result row whose `toolUseResult` names the id (`_background_tasks.launched_task_id`:
    Bash/PowerShell, Monitor, Workflow, a background Agent); its tool_use_id names the assistant tool_use. Either
    row on a sidechain (a subagent) is not this session's launch."""
    if not transcript_path or not task_id:
        return None
    use_id = None
    for line in _lines_holding(transcript_path, task_id):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("isSidechain") or _background_tasks.launched_task_id(row) != task_id:
            continue
        for item in (row.get("message") or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                use_id = item.get("tool_use_id")
    if not use_id:
        return None
    for line in _lines_holding(transcript_path, use_id, '"tool_use"'):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("isSidechain"):
            return None
        for item in (row.get("message") or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("id") == use_id:
                return item.get("name"), item.get("input") or {}, row.get("timestamp")
    return None


def _lines_holding(transcript_path, *needles):
    """Each transcript line holding every needle, read as a stream with no size cap: a launch or completion
    notice can sit anywhere in a long session, and a fact read from a tail window misses it. Nothing when the
    file cannot be read."""
    keys = [needle.encode("utf-8") for needle in needles]
    try:
        with open(transcript_path, "rb") as fh:
            for line in fh:
                if all(key in line for key in keys):
                    yield line.decode("utf-8", errors="replace")
    except (OSError, TypeError, ValueError):
        return


def _task_ended(transcript_path, task_id):
    """True when a `<task-notification>` row of the transcript names `task_id`: the task already ended, so a
    stop discards nothing."""
    if not transcript_path or not task_id:
        return False
    for line in _lines_holding(transcript_path, task_id, "<task-notification>"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if any(notice["task_id"] == task_id for notice in _background_tasks.ended_notices(row)):
            return True
    return False


def _local_path(token, cwd):
    path = native_path(token.strip("\"'"))
    return path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)


def _stages(command):
    """(segment, words, previous segment's operator, body text) for every simple command `command` runs, its
    substitutions and launcher bodies included (`_shell_lex.walk`)."""
    for _kind, text, segments, _depth in _shell_lex.walk(command):
        previous = ""
        for seg in segments:
            yield seg, [w.value for w in seg.words], previous, text
            previous = seg.op


def _run_ref(words):
    """The script a stage runs: its first script reference. Later ones are arguments."""
    return next((m.group(0) for m in map(SCRIPT_REF_RE.search, words) if m), None)


def _read_script(token, cwd):
    """A referenced script's text, or None when it cannot be located and read."""
    path = _local_path(token, cwd)
    try:
        if os.path.getsize(path) > MAX_SCRIPT_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _process_table():
    """{pid: row} for every live process, or {} when unreadable. KILL_GUARD_PROCESS_TABLE names a planted table
    (proofs)."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    planted = os.environ.get("KILL_GUARD_PROCESS_TABLE")
    if planted:
        try:
            with open(planted, encoding="utf-8") as fh:
                return {int(pid): row for pid, row in json.load(fh).items()}
        except (OSError, ValueError, TypeError):
            return {}
    try:
        import reap
        return reap.process_table(timeout=_git_run.remaining(TABLE_TIMEOUT_S))
    except Exception:
        return {}


def _paid(launch):
    """True when the launch spends model quota: a Workflow, an Agent, or a shell running the seat launcher."""
    tool, tool_input = launch[0], launch[1] if isinstance(launch[1], dict) else {}
    if tool in ("Workflow", "Agent"):
        return True
    return tool in ("Bash", "PowerShell") and bool(_shell_lex.launcher_invocations(tool_input.get("command") or ""))


def taskstop_decision(transcript_path, tool_input):
    """(decision, reason), or None to pass. Passes a task whose completion notice is in the transcript and an
    unpaid task this session's main thread launched; asks the owner for running paid work this session launched;
    denies any other task."""
    task_id = (tool_input or {}).get("task_id") if isinstance(tool_input, dict) else None
    if _task_ended(transcript_path, task_id):
        return None
    launch = _launch_of(transcript_path, task_id)
    if not launch:
        return "deny", NOT_LAUNCHED % (task_id or "(no task id)")
    if _paid(launch):
        return "ask", PAID_RUNNING % (task_id, launch[0])
    return None


def pid_forms(cmd):
    """[(pid, is_kill)] for every pid a stage of `cmd` names to a kill: `is_kill` marks the Git Bash `kill <pid>`
    form (every operand, after any signal option), whose number is an MSYS pid; a `/PID <pid>` switch (taskkill, in
    any case, `-PID` and `//PID` included) is a Windows pid. A `kill` word anywhere in a stage counts (behind `xargs`,
    `sudo`, `timeout`), a quoted mention never does."""
    out = []
    for _seg, words, _previous, _text in _stages(cmd):
        for k, word in enumerate(words):
            if _shell_lex.base(word) == "kill":
                operands, sends = _kill_operands(words[k + 1:])
                out += [(o.lstrip("-"), True) for o in operands if sends and o.lstrip("-").isdigit()]
            elif re.fullmatch(r"(?:/{1,2}|-)pid", word, re.I) and k + 1 < len(words) and words[k + 1].isdigit():
                out.append((words[k + 1], False))
    return out


def _now():
    return time.monotonic()


def _cim_commandline(pid):
    ps = ("$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%s' -ErrorAction SilentlyContinue;"
          " if ($p) { $p.CommandLine + '|' + $p.Name }" % pid)
    for exe in ("pwsh", "powershell"):
        try:
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S)
        except Exception:
            continue
        if r.returncode == 0:
            return (r.stdout or "").strip()
    return None


def _ps_winpid(pid):
    """The WINPID Git Bash's `ps -p` reports for an MSYS pid, else None."""
    try:
        r = subprocess.run([_msys.tool("ps"), "-p", str(pid)], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S)
    except Exception:
        return None
    return _winpid_from_ps(r.stdout, pid) if r.returncode == 0 else None


def _winpid_from_ps(text, pid):
    """The WINPID column of `ps -p <pid>` output for that MSYS pid, else None."""
    lines = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or "WINPID" not in lines[0]:
        return None
    col = lines[0].index("WINPID")
    for row in lines[1:]:
        if row and row[0] == str(pid) and len(row) > col and row[col].isdigit():
            return row[col]
    return None


def commandline(pid, msys_first=False, deadline=None):
    """The process's full command line, or None. None is NOT 'harmless' — the caller fails closed.

    A Git Bash `kill` takes an MSYS pid (`msys_first`): it is mapped through `ps -p` to its WINPID
    BEFORE CIM is asked, because the same number can also be an unrelated live Windows pid. Any other
    pid asks CIM first and falls back to the mapping. A lookup the `deadline` cannot afford resolves
    nothing."""
    def affordable():
        return deadline is None or deadline - _now() >= SUBPROCESS_TIMEOUT_S

    def mapped():
        if not affordable():
            return None
        winpid = _ps_winpid(pid)
        if winpid and winpid != str(pid) and affordable():
            return _cim_commandline(winpid) or None
        return None

    if msys_first:
        line = mapped()
        if line:
            return line
    if not affordable():
        return None
    line = _cim_commandline(pid)
    if line:
        return line
    if not msys_first:
        remapped = mapped()
        if remapped:
            return remapped
    return line                        # "" = resolved, and the process is gone; None = unresolvable


# Prefixes after which the next word is the program a stage runs, beyond `_shell_lex.command_index`'s wrappers.
NAME_RUNNERS = ("xargs", "start", "call")
NAME_EXTRA_READERS = frozenset(("echo", "printf", "man", "which", "type", "help"))
PKILL_ARG_OPTS = "uUgGPstTFOdL"
PKILL_ARG_LONG = ("uid", "euid", "group", "pgroup", "parent", "session", "signal", "terminal", "ns", "nslist",
                  "delimiter", "pidfile", "runstates", "env", "cgroup", "older", "queue")
PKILL_LONG = ("full", "exact", "ignore-case", "inverse", "list-name", "list-full", "count", "newest", "oldest", "echo",
              "lightweight", "logpidfile", "require-handler")
KILLALL_ARG_OPTS = "suoynZ"
KILLALL_ARG_LONG = ("signal", "user", "older-than", "younger-than", "ns", "context")
KILLALL_LONG = ("regexp", "exact", "ignore-case", "process-group", "interactive", "quiet", "verbose", "wait",
                "version")
_SIGNAL_WORD = re.compile(r"-(?:\d+|(?:SIG)?[A-Z]{2,7}\d*)")
PS_NAME_FLAG_RE = re.compile(r"-n(?:a(?:m(?:e)?)?)?", re.I)
WHERE_CLAUSE_RE = re.compile(
    r"(name|commandline|executablepath|caption|processid)\s*(=|like)\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s)]+))", re.I)
MAX_MATCH_LINES = 5


def _verbs(words):
    """(verb, its arguments) at each command position of one stage: its program after assignments and wrapper prefixes
    (`_shell_lex.command_index`, `sudo` included), and the program each runner (`xargs`, `start`, `call`) runs. A
    runner's arguments are its own options."""
    out, k = [], 0
    while k < len(words):
        k += _shell_lex.command_index(words[k:], through=("sudo",))
        if k >= len(words):
            break
        verb = _shell_lex.base(words[k])
        if verb not in NAME_RUNNERS:
            out.append((verb, words[k + 1:]))
            break
        j = k + 1
        while j < len(words) and words[j][:1] in ("-", "/") and len(words[j]) > 1:
            j += 2 if words[j] in _shell_lex.XARGS_VALUES else 1
        out.append((verb, words[k + 1:j]))
        k = j
    return out


def _kill_operands(args):
    """(operands, sends a signal) of `kill <args>`: `-s SIG`, `-n NUM`, `--signal SIG`, `-SIG`, `-NUM`, `--`, and
    `-l`/`-L` (a listing, no kill). Signal 0 probes and sends nothing. After the first operand a `-N` is a process
    group, read as its number."""
    operands, signal, i = [], None, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            operands += args[i:]
            break
        if operands or not a.startswith("-") or a == "-":
            operands.append(a)
        elif a in ("-l", "-L", "--list", "--table"):
            return [], False
        elif a in ("-s", "-n", "--signal", "-q", "--queue", "--timeout"):
            signal = (args[i] if i < len(args) else "") if a in ("-s", "-n", "--signal") else signal
            i += 1
        elif a.startswith("--signal="):
            signal = a.split("=", 1)[1]
        else:
            signal = a[1:]
    return operands, (signal or "").upper().removeprefix("SIG") != "0"


def _bare(name):
    name = (name or "").lower()
    return name[:-4] if name.endswith(".exe") else name


def _name_is(*names):
    wanted = {_bare(n) for n in names}
    return lambda row: _bare(row.get("name")) in wanted


def _name_glob(globs):
    import fnmatch
    pats = [_bare(g) for g in globs]
    return lambda row: any(fnmatch.fnmatchcase(_bare(row.get("name")), p) for p in pats)


def _pid_in(pids):
    wanted = {int(p) for p in pids}
    return lambda row: row.get("pid") in wanted


def _pkill_selector(verb, args):
    """(label, predicate) for pkill/pgrep options, predicate None when the pattern is not a regex. Filters on user,
    group or parent are ignored, which only widens the match."""
    parsed = _shell_lex.options([a for a in args if not _SIGNAL_WORD.fullmatch(a)], short_values=PKILL_ARG_OPTS,
                                long=PKILL_LONG, long_values=PKILL_ARG_LONG)
    full, exact = bool(parsed.flags & {"f", "full"}), bool(parsed.flags & {"x", "exact"})
    icase, invert = bool(parsed.flags & {"i", "ignore-case"}), bool(parsed.flags & {"v", "inverse"})
    pattern = parsed.operands[0] if parsed.operands else ""
    label = "%s %s%s" % (verb, "-f " if full else "", pattern or "(no pattern)")
    try:
        rx = re.compile("^(?:%s)$" % pattern if exact else pattern, re.I if icase else 0)
    except re.error:
        return label, None

    def hit(row):
        if full:
            seen = bool(rx.search(row.get("cmd") or row.get("name") or ""))
        else:
            seen = bool(rx.search(_bare(row.get("name")))) or bool(rx.search(row.get("name") or ""))
        return seen != invert
    return label, hit


def _killall_selector(args):
    parsed = _shell_lex.options([a for a in args if not _SIGNAL_WORD.fullmatch(a)], short_values=KILLALL_ARG_OPTS,
                                long=KILLALL_LONG, long_values=KILLALL_ARG_LONG)
    regexp, names = bool(parsed.flags & {"r", "regexp"}), parsed.operands
    label = "killall %s" % (" ".join(names) or "(no name)")
    if not names:
        return label, None
    if regexp:
        try:
            rxs = [re.compile(n, re.I) for n in names]
        except re.error:
            return label, None
        return label, lambda row: any(r.search(row.get("name") or "") for r in rxs)
    return label, _name_is(*names)


def _like_regex(text):
    return re.compile("^" + "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in text) + "$", re.I)


def _where_selector(label, text):
    """A WMI `where` or `-Filter` condition as a predicate, None when any part of it is not a plain
    name, commandline or processid comparison."""
    text = text.replace("\\", "")
    clauses = WHERE_CLAUSE_RE.findall(text)
    leftover = re.sub(r"\b(?:and|or)\b|[\s()'\"]", "", WHERE_CLAUSE_RE.sub("", text), flags=re.I)
    if not clauses or leftover:
        return label, None
    preds = []
    for field, op, a, b, c in clauses:
        value, field = a or b or c, field.lower()
        if field == "processid":
            preds.append(lambda row, v=value: str(row.get("pid")) == v)
            continue
        key = "name" if field in ("name", "caption") else "cmd"
        rx = _like_regex(value) if op.lower() == "like" else re.compile("^" + re.escape(value) + "$", re.I)
        preds.append(lambda row, k=key, r=rx: bool(r.match(row.get(k) or "")))
    combine = any if re.search(r"\bor\b", text, re.I) else all
    return label, lambda row: combine(p(row) for p in preds)


def _taskkill_switch(word):
    """A taskkill switch name, lowercase (`/IM`, `//IM` from Git Bash, `-im`), or ''."""
    m = re.fullmatch(r"(?:/{1,2}|-)(\w+)", word)
    return m.group(1).lower() if m else ""


def _taskkill_selectors(args):
    out, i = [], 0
    while i < len(args):
        flag = _taskkill_switch(args[i])
        value = args[i + 1] if i + 1 < len(args) else ""
        if flag == "im":
            out.append(("taskkill /IM %s" % value, _name_glob([value])))
            i += 1
        elif flag == "fi":
            m = re.fullmatch(r"\s*(\w+)\s+(eq|ne)\s+(.*?)\s*", value, re.I)
            label = "taskkill /FI %s" % value
            if m and m.group(1).lower() == "imagename" and m.group(2).lower() == "eq":
                out.append((label, _name_glob([m.group(3)])))
            elif m and m.group(1).lower() == "pid" and m.group(2).lower() == "eq" and m.group(3).isdigit():
                out.append((label, _pid_in([m.group(3)])))
            else:
                out.append((label, None))
            i += 1
        i += 1
    return out


def _stop_process_selectors(args, producer_names):
    """Selectors for Stop-Process: -Name names, -Id or positional ids, else the names a piped Get-Process selects
    (`producer_names`, None when the pipeline holds anything this cannot read)."""
    names, ids, i, opaque = [], [], 0, False
    while i < len(args):
        tok = args[i]
        i += 1
        low = tok.lower()
        value = low.split(":", 1)[1] if ":" in low else None
        if _shell_lex.ps_param(tok, "name") or _shell_lex.ps_param(tok, "processname", 2):
            names += [n for n in (tok.split(":", 1)[1] if value is not None else args[i] if i < len(args) else "")
                      .split(",") if n]
            i += value is None
        elif _shell_lex.ps_param(tok, "id", 2):
            ids += [n for n in (value if value is not None else args[i] if i < len(args) else "").split(",") if n]
            i += value is None
        elif _shell_lex.ps_param(tok, "inputobject", 2):
            opaque = True
            i += value is None
        elif tok.startswith("-"):
            continue
        elif tok.isdigit():
            ids.append(tok)
        else:
            names.append(tok)
    out = []
    if names:
        out.append(("Stop-Process -Name %s" % ",".join(names), _name_glob(names)))
    if ids:
        out.append(("Stop-Process -Id %s" % ",".join(ids), _pid_in([n for n in ids if n.isdigit()])))
    if opaque:
        out.append(("Stop-Process -InputObject", None))
    if not out:
        if producer_names is None:
            out.append(("Stop-Process fed by a pipeline", None))
        else:
            out.append(("Get-Process %s | Stop-Process" % (",".join(producer_names) or "(all)"),
                        _name_glob(producer_names or ["*"])))
    return out


def _get_process_names(words):
    """Names a bare `Get-Process ...` pipeline head selects, or None when the stage is anything else."""
    verbs = _verbs(words)
    if not verbs or verbs[0][0] not in ("get-process", "gps"):
        return None
    args, names, i = verbs[0][1], [], 0
    while i < len(args):
        tok = args[i]
        i += 1
        if PS_NAME_FLAG_RE.fullmatch(tok.lower()):
            names += (args[i] if i < len(args) else "").split(",")
            i += 1
        elif tok.startswith("-"):
            if tok.lower() in ("-erroraction", "-ea"):
                i += 1
        else:
            names += tok.split(",")
    return [n for n in names if n]


def _producer(n, segments):
    """Names the one `Get-Process` stage piped into stage `n` selects, or None (no single readable producer)."""
    chain, j = [], n - 1
    while j >= 0 and segments[j].op == "|":
        chain.insert(0, segments[j])
        j -= 1
    return _get_process_names([w.value for w in chain[0].words]) if len(chain) == 1 else None


def _name_selectors(cmd):
    """[(label, predicate-or-None)] for every name- or pattern-addressed kill in the executing stages of `cmd`.
    None marks a form whose target set cannot be resolved."""
    readers = READ_ONLY_VERBS | NAME_EXTRA_READERS
    stages = []
    for _kind, text, segments, _depth in _shell_lex.walk(cmd):
        for n, seg in enumerate(segments):
            words = [w.value for w in seg.words]
            verb = _segment_verb(words)
            if not words or verb in readers or (verb in ("python", "python3", "py")
                                                and any(_shell_lex.base(w) == "reap.py" for w in words)):
                continue
            stages.append((n, segments, words, _verbs(words), text[seg.start:seg.end]))
    verbs = [v for stage in stages for v, _args in stage[3]]
    kills = any(v in ("kill", "taskkill", "stop-process", "spps") for v in verbs)
    out = []
    for n, segments, words, chain, _raw in stages:
        piped = n > 0 and segments[n - 1].op == "|"
        for k, (verb, args) in enumerate(chain):
            if verb in ("pkill", "pgrep"):
                if verb == "pkill" or kills:
                    out.append(_pkill_selector(verb, args))
            elif verb == "killall":
                out.append(_killall_selector(args))
            elif verb == "pidof":
                if kills:
                    out.append(("pidof %s" % " ".join(args), _name_is(*args)))
            elif verb == "taskkill":
                out.extend(_taskkill_selectors(args))
            elif verb in ("stop-process", "spps") or (verb == "kill" and piped and not any(a.isdigit() for a in args)):
                out.extend(_stop_process_selectors(args, _producer(n, segments) if piped else None))
            elif verb == "wmic":
                rest = " ".join(args)
                w = re.search(r"\bprocess\b\s+where\b(?P<cond>.*?)\b(?:delete|call\s+terminate)\b", rest, re.I | re.S)
                if w:
                    out.append(_where_selector("wmic process where%s" % w.group("cond").rstrip(), w.group("cond")))
                elif re.search(r"\bprocess\b.*\b(?:delete|call\s+terminate)\b", rest, re.I | re.S):
                    out.append(("wmic process with no condition", None))
            elif verb == "xargs" and k + 1 < len(chain) and chain[k + 1][0] in ("kill", "taskkill") \
                    and not any(v in ("pkill", "pgrep") for v in verbs):
                out.append(("xargs kill fed by a pipeline", None))
    joined = " ".join(raw for *_rest, raw in stages)
    if re.search(r"win32_process", joined, re.I) and re.search(
            r"\bTerminate\b|Remove-(?:Cim|Wmi)(?:Instance|Object)", joined, re.I):
        filters = [words[k + 1] for _n, _s, words, _c, _r in stages for k in range(len(words) - 1)
                   if _shell_lex.ps_param(words[k], "filter", 2) or _shell_lex.ps_param(words[k], "query", 2)]
        piped_where = any(v in ("where-object", "where", "?") for v in verbs)
        if filters and not piped_where:
            out.append(_where_selector("Win32_Process Terminate", filters[0]))
        else:
            out.append(("Win32_Process Terminate with no readable filter", None))
    return out


def _unnamed_pid_kills(cmd):
    """Labels of the kills whose pids the call does not name (K-XA, owner 2026-11-23): a `kill`, `taskkill /PID` or
    `Stop-Process -Id` operand read from a variable or a substitution. `$!` (the call's own background job), a
    `kill -0` probe and a pgrep/pidof substitution (resolved by name in `_name_selectors`) are named."""
    out = []
    for _seg, words, _previous, _text in _stages(cmd):
        if _segment_verb(words) in READ_ONLY_VERBS | NAME_EXTRA_READERS:
            continue
        for verb, args in _verbs(words):
            if verb == "kill":
                operands, sends = _kill_operands(args)
                if not sends:
                    continue
            elif verb == "taskkill":
                operands = [args[i + 1] for i, a in enumerate(args[:-1]) if _taskkill_switch(a) == "pid"]
            elif verb in ("stop-process", "spps"):
                operands = [args[i + 1] for i, a in enumerate(args[:-1]) if _shell_lex.ps_param(a, "id", 2)]
            else:
                continue
            if any(_unnamed(a) for a in operands):
                out.append("%s %s" % (verb, " ".join(args)))
    return out


def _unnamed(operand):
    """True when a pid operand is read from a variable or a substitution, other than `$!` (the call's own job) and a
    pgrep/pidof substitution (resolved by name in `_name_selectors`)."""
    if operand == "$!" or not operand.startswith(("$", "`")):
        return False
    body = operand[2:] if operand.startswith("$(") else operand[1:] if operand.startswith("`") else ""
    return not re.match(r"\s*(?:pgrep|pidof)\b", body)


def _protected_reason(row):
    line = row.get("cmd") or row.get("name") or ""
    for pat, why in PROTECTED:
        if re.search(pat, line, re.I):
            return why
    return None


def _name_kill_problems(cmd):
    selectors = _name_selectors(cmd)
    problems = ["  `%s`: the target set cannot be resolved. A kill is irreversible and an unresolved "
                "pattern is an unread target." % label for label, pred in selectors if pred is None]
    live = [(label, pred) for label, pred in selectors if pred is not None]
    if not live:
        return problems
    table = _process_table()
    if not table:
        return problems + ["  cannot read the process table, so `%s` cannot be resolved to the processes "
                           "it would stop." % live[0][0]]
    for label, pred in live:
        shown = 0
        for pid, row in sorted(table.items()):
            why = _protected_reason(row) if pred(row) else None
            if why and shown < MAX_MATCH_LINES:
                shown += 1
                problems.append("  `%s` matches PID %s, %s\n      %s"
                                % (label, pid, why, (row.get("cmd") or row.get("name") or "")[:160]))
    return problems


def _pid_problems(cmd, env):
    allowed = {p.strip() for p in (env.get("HARNESS_ALLOW_KILL") or "").replace(",", " ").split() if p}
    problems = []
    deadline = _now() + _git_run.remaining(HOOK_BUDGET_S)
    for pid, is_kill in pid_forms(cmd):
        if pid in allowed:
            continue
        line = commandline(pid, msys_first=True, deadline=deadline) if is_kill else commandline(pid, deadline=deadline)
        if line is None:
            problems.append("  PID %s: could not resolve. A kill is irreversible and an unread "
                            "PID is an unread target." % pid)
            continue
        if line == "":
            problems.append("  PID %s: no such process — it already exited, or the number is "
                            "stale. Re-read the list before killing." % pid)
            continue
        for pat, why in PROTECTED:
            if re.search(pat, line, re.I):
                problems.append("  PID %s is %s\n      %s" % (pid, why, line[:160]))
                break
    return problems


def verdict(cmd, env):
    pid_problems = _pid_problems(cmd, env)
    name_problems = _name_kill_problems(cmd) + [
        "  `%s`: the call does not name the pids it stops, so they cannot be read before it acts." % label
        for label in _unnamed_pid_kills(cmd)]
    if not pid_problems and not name_problems:
        return None
    return ("BLOCKED kill (CLAUDE.core.md §Shell Discipline) — the target is not what a truncated shell list shows.\n"
            + "\n".join(pid_problems + name_problems)
            + "\n\nStop it through the reaper (`python3 .claude/tools/reap.py --help`): it refuses shared infra and another session's process, and writes a ledger row.\n"
              "A Bash-tool background task is NOT an OS process to taskkill — stop it with "
              "KillShell/TaskStop by its task id.\n"
            + ("A kill by name or pattern stops every process that matches; `HARNESS_ALLOW_KILL` covers a PID only."
               if name_problems else
               "Deliberate raw kill: prefix `HARNESS_ALLOW_KILL=<pid>` (naming the pid a second time)."))


# Closing terminal windows by handle, title or host process. Every Windows Terminal window belongs to
# one WindowsTerminal.exe, and a title follows the foreground command, so a `python.exe` title is a
# live session as often as a dead launch-error window.
# The text is any language (a scratch .py or .ps1 the call runs), so this stays a text match; its switch spellings are
# every form taskkill (`/IM`, `//IM`, `-im`) and Stop-Process (`-Name` by any prefix, `-ProcessName`) accept.
WINDOW_CLOSE_RE = re.compile(
    r"CloseMainWindow|\bWM_CLOSE\b|\bSC_CLOSE\b|0xF060"
    r"|\b(?:Post|Send)Message\w*\s*\([^)]*0x(?:0*10|112)\b"
    r"|taskkill[^\n;|]*?(?:/{1,2}|-)IM[\s:]+['\"]?(?:WindowsTerminal|wt|OpenConsole|conhost)\b"
    r"|Stop-Process[^\n;|]*?-(?:n(?:a(?:m(?:e)?)?)?|processname)[\s:]+['\"]?(?:WindowsTerminal|wt|OpenConsole|conhost)\b"
    r"|(?:WindowsTerminal|OpenConsole|conhost)\b[^\n]*\|\s*Stop-Process", re.I)


# Reader arguments are data; `_stages` visits their executing substitutions separately.
READ_ONLY_VERBS = frozenset((
    "grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "less", "more", "wc", "ls", "stat", "file", "diff",
    "select-string", "get-content", "gc"))


def _segment_verb(words):
    words = [w for w in words if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
    return _shell_lex.base(words[0]) if words else ""


def window_close_verdict(cmd, cwd=None):
    """Deny terminal-window closes in executing stages and their scratch scripts, or None.

    Quoted reader arguments are data; substitutions and every non-reader stage are inspected.
    Proof and hook bodies outside `.claude/scratch/` are not scanned.
    """
    every = list(_stages(cmd))
    stages = [(seg, words, text) for seg, words, _previous, text in every if _segment_verb(words) not in READ_ONLY_VERBS]
    # A heredoc body is a program in any language (`python3 - <<EOF`), so its text is read whoever reads it.
    texts = ["\n".join([text[seg.start:seg.end] + ("|" if seg.op == "|" else "") for seg, _words, text in stages]
                       + [hd.body for seg, _words, _previous, _text in every for hd in seg.heredocs])]
    for _seg, words, _text in stages:
        token = _run_ref(words)
        scratch = token and "/.claude/scratch/" in "/" + token.replace("\\", "/").lstrip("/")
        body = _read_script(token, cwd) if scratch else None
        if body:
            texts.append(body)
    if not any(WINDOW_CLOSE_RE.search(t) for t in texts):
        return None
    return ("BLOCKED window close (CLAUDE.core.md §Shell Discipline) — terminal windows share one WindowsTerminal.exe, "
            "and a window's title follows its foreground command, so no title or process name tells a dead error "
            "window from a live session, possibly this one.\n"
            "Ask the owner to close them.")


def _output(decision, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def check(payload):
    """The floor's verdict on one PreToolUse payload: None (allow), a deny, or an `ask` for running paid work."""
    tool = payload.get("tool_name")
    if tool == "TaskStop":
        decision = taskstop_decision(payload.get("transcript_path"), payload.get("tool_input"))
        return _output(*decision) if decision else None
    if tool not in ("Bash", "PowerShell"):
        return None
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    inline = dict(os.environ)
    for m in re.finditer(r"HARNESS_ALLOW_KILL=(\S+)", cmd):
        inline["HARNESS_ALLOW_KILL"] = inline.get("HARNESS_ALLOW_KILL", "") + " " + m.group(1)
    why = window_close_verdict(cmd, payload.get("cwd")) or verdict(cmd, inline)
    return _output("deny", why) if why else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    out = (check(payload) if isinstance(payload, dict) else _output(
        "deny", "BLOCKED stop/kill (CLAUDE.core.md §Shell Discipline) — the hook could not read its input or "
        "transcript. Re-issue the same call; if it repeats, report it."))
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
