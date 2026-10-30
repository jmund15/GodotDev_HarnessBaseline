#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|PowerShell), run by pre_bash_dispatch — owns how a session waits on a job.

Rule homes: CLAUDE.core.md §Shell Discipline (the orchestrator's wait; no repeated status check) and
guards/any.md (a never-woken delegate's wait recipe). Evidence for every bound below:
auto-memory/archive/gotcha_delegate_gets_no_background_wake.md. Counts are per caller (the
orchestrator, or one subagent by payload `agent_id`; `session_id` is shared with the parent). The wait
a caller should use depends on whether a backgrounded job wakes it:
- woken: the interactive session (CLAUDE_CODE_ENTRYPOINT=cli) and an Agent-tool subagent, known by its
  `.meta.json` under the session's `subagents/` dir (`_agent_tool_subagent`);
- never woken: a Workflow subagent (agent_type "workflow-subagent", or a job's own agentType such as
  "<job>-author", whose `.meta.json` sits under `subagents/workflows/<run>/`) and a print-mode `claude -p`
  child (CLAUDE_CODE_ENTRYPOINT=sdk-cli: the session ends at its turn's end). A subagent whose kind is
  unknown counts as never woken, the safe side: one bounded foreground call, never a trap.

1. Foreground wait loop (`until`/`while` with `sleep`/`Start-Sleep` inside). An interactive
   orchestrator's timeout above SUBAGENT_WAIT_MS becomes `run_in_background: true`; a shorter loop
   stays foreground to avoid the extra dispatch and wake. That orchestrator's backgrounded Bash loop, its own or
   rewritten, runs under a `timeout` wrapper of ORCHESTRATOR_WAIT_MS whatever its timeout field says, so its wake
   lands inside the one-hour prompt cache even when the job never ends; a backgrounded job that is not a loop is
   untouched. When the loop's done-test reads a file that a still-running `run_in_background` command of this session
   writes (a `>`/`>>`/`tee` target of its command, or its task output file), that command's completion already wakes
   the orchestrator, so the conversion carries an advisory naming the task and the file (`_live_writer`); the call is
   still converted. A loop whose whole job is one `grep -q` for a
   run's END line (or ROSTER DONE/HALT) in one bench roster or run log is rewritten to the bench suite's `wait <log>` subcommand, which owns
   the check-in deadline (`_bench_wait`); any doubt (a second test, an unresolvable path, a log outside bench's roots)
   leaves the command as it was. A subagent's timeout is clamped to
   SUBAGENT_WAIT_MS (under its 5-minute prompt cache), and so is a print-mode child's: the sidecar
   launchers set its cache to 5 minutes. Neither is backgrounded: guards/any.md owns its bounded foreground wait.
2. A never-woken caller's `run_in_background` call draws an advisory naming the missing wake.
3. Repeated status poll. A status-only command (every segment a read: date, tail, wc, ls, grep,
   Get-CimInstance, Start-Sleep …, no `>` write) whose shape (only count and delay arguments normalized) matches the caller's
   previous POLL_LIMIT - 1 consecutive Bash calls is denied: each call is a full model turn that
   re-reads the whole context. POLL_LIMIT is a litmus bound: a deliberate re-check before and after
   one action repeats a shape at most twice, so a fourth identical read with nothing run between is a
   poll. Any other command, a wait loop included, resets the count. The deny names that caller's wait.
4. Foreground proof batch (advisory only, woken callers only). A segment that executes the runner
   `harness_tests.py` in `--close`, `--all` or no mode, or that executes proofs under a `tests/` directory directly,
   draws one `additionalContext` line when its expected time exceeds the call's own timeout (BASH_DEFAULT_MS when it
   sets none): always for the three runner modes, else the summed recorded seconds (`harness_proof_durations.json` beside
   HARNESS_TEST_STAMP, else `.claude/logs/`) of the proofs a `--for`/`--proofs-for` run selects or
   the proofs named. An unknown or in-budget time stays silent, and so do `--help`, `--tiers` and a
   command that only names the runner. It never denies; a poll deny replaces it.
6. Raised foreground timeout (rewrite, interactive orchestrator only). A foreground call that is not a wait loop,
   whose timeout exceeds BASH_DEFAULT_MS, becomes `run_in_background: true` with a note: the raise says the job may
   run long, and a raised call holds every other wake. The one exception is a proof batch whose every proof has a
   recorded time summing within the timeout (`_timed_fit`), the only job length the harness records. A delegate
   or print-mode child keeps its timeout: no wake reaches it, so one bounded call is its wait.
5. Blind pipe (denies, background calls only). A `run_in_background` pipeline whose stdout reaches a later `tail` or `head` unredirected and unteed is denied (`blind_pipe`): the task's output file already keeps every line, so the pipe only holds them until the job exits and drops the ones past its count. `tail -f`, a `tee` stage and a `tail` of a file pass. Rule home: CLAUDE.core.md §Shell Discipline.

7. Monitor past its cap. A Monitor call whose `timeout_ms` exceeds MONITOR_CAP_MS is denied: the tool ends every watch
   at that ceiling with no wake, so the request cannot do what it says. The deny names the waiter for a detached
   job's end (the launch's printed WAIT-CMD, or the session watcher) and the Monitor's own bound. It reads the
   requested number only, never the command: a Monitor at or under the cap, or naming none, passes.
8. Session-watcher arm outside run_in_background (denies, interactive orchestrator only). A Bash call without
   `run_in_background` whose segment runs `watch_records.py` with `--session` is denied (`foreground_watch_arm`):
   in the foreground, or detached with `&`/`nohup`/`disown`, its wake reaches no task notification, and while it lives
   every correct arm exits COVERED. Rule home: reference/sidecar_dispatch.md step 6.
9. Trailing background `&` (advises, never denies). A Bash call without `run_in_background` whose command ends a line
   in a single `&` (not `&&`, `2>&1`, `>&`) draws one advisory naming `run_in_background` (`DETACHED_ADVICE`).
   Rule home: CLAUDE.core.md §Shell Discipline. Class: process guard, advisory first.
10. Covered session-watcher arm (denies, interactive orchestrator only). A `run_in_background` call whose segment
   runs `watch_records.py --session` while the session's hook state (key `sidecar_watchers`) lists a live
   session-wide watcher is denied (`COVERED_REASON`): the arm exits COVERED and its wake is waste. A gone watcher
   or a record watcher passes. Rule home: reference/sidecar_dispatch.md step 6. Class: process guard.

Matches the ACTION, never the noun: a command that only names `sleep` passes, and a Monitor (except item 7) or a
backgrounded call is never counted. Class: process guard. State: the shared per-session file
(_hook_state), key `wait_polls`, one entry per caller; an entry idle past STALE_POLL_S is swept.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _background_tasks  # noqa: E402
import _hook_state  # noqa: E402
from _win_path import drive_path  # noqa: E402

# Shell syntax, not prose: a bash `until|while <cond>; do ... sleep ... done` whose `; do` follows the
# condition on its line, or a PowerShell `while (...) { ... Start-Sleep ... }`.
_LOOP_SLEEP = re.compile(
    r"\b(?:until|while)\b[^\n;]*;\s*do\b(?:(?!\bdone\b).)*?\bsleep\b"
    r"|\bwhile\s*\([^\n]*\)\s*\{(?:(?!\}).)*?\bStart-Sleep\b",
    re.IGNORECASE | re.DOTALL,
)

# A heredoc body is stdin for the program before `<<`; only a shell runs it as code.
_HEREDOC = re.compile(r"(?P<head>[^\n]*?)<<-?\s*(?P<q>['\"]?)(?P<tag>\w+)(?P=q)[^\n]*\n(?P<body>.*?)^[ \t]*(?P=tag)[ \t]*$",
                      re.DOTALL | re.MULTILINE)
_SHELL_WORDS = frozenset(("bash", "sh", "zsh", "dash", "pwsh", "powershell"))

POLL_LIMIT = 4
SUBAGENT_WAIT_MS = _hook_state.SUBAGENT_CACHE_WARM_SECONDS * 1000
BASH_DEFAULT_MS = 120000
TIMEOUT_BIN = "/usr/bin/timeout"  # Git Bash's coreutils; a bare `timeout` can resolve to Windows' timeout.exe (a pause)
_DEADLINE = re.compile(r"^\s*(?:/usr/bin/)?timeout\s+\d")
ORCHESTRATOR_WAIT_MS = int(_hook_state.ORCHESTRATOR_CHECKIN_SECONDS * 1000)  # the watcher's check-in
MONITOR_CAP_MS = 1800000  # the Monitor tool's own ceiling: it stops a watch there whatever `timeout_ms` asks
STATE_KEY = "wait_polls"
STALE_POLL_S = 3600  # the longest prompt cache: a caller idle this long re-reads uncached either way
_STATUS_VERBS = frozenset((
    "date", "tail", "head", "wc", "cat", "ls", "stat", "test", "[", "[[", "grep", "rg", "echo", "printf",
    "sleep", "ps", "tasklist", "pgrep", "du", "sort", "uniq", "cut", "tr", "true", "cd", "pwd",
    "get-content", "gc", "get-item", "get-childitem", "get-process", "get-ciminstance", "start-sleep",
    "test-path", "select-string", "select-object", "where-object", "measure-object", "format-list",
    "out-string", "write-host", "write-output", "set-location",
))
_HARMLESS_REDIRECT = re.compile(r"\d?>\s*(?:/dev/null|\$null|nul)\b|\d?>&\d", re.IGNORECASE)
_DETACHED_LINE = re.compile(r"(?<![&>|<])&[ \t]*(?=\r?\n|\Z)")

_WAIT_RECIPE = (
    "wait in foreground calls: `until <done-test>; do sleep 30; done` with a Bash timeout of at most "
    "%d ms in a subagent or a `claude -p` child (its cache lives 5 minutes), repeated only while "
    "the job runs (guards/any.md)" % SUBAGENT_WAIT_MS
)
MONITOR_REASON = (
    "BLOCKED Monitor timeout_ms %d: the tool caps every Monitor at %d ms (%d minutes) and ends it there with no "
    "wake, so a longer request is a watch that dies silently. To wait on a detached job's end, use its own waiter "
    "with Bash run_in_background and a 7200000 ms timeout: the `WAIT-CMD` its launch printed, "
    "or `%s`; each checks in at %d minutes, "
    "inside the one-hour prompt cache. Keep a Monitor for intermediate events "
    "(CLAUDE.core.md §Shell Discipline), with timeout_ms at most %d."
)
POLL_REASON_MAIN = (
    "BLOCKED repeated status poll: this is the %d%s consecutive run of the same status check, and each "
    "run is a full turn that re-reads your whole context. Run the job with run_in_background: true and "
    "act on its one wake, or use a Monitor filtered to the lines that change your next action "
    "(CLAUDE.core.md §Shell Discipline)."
)
POLL_REASON_AGENT = (
    "BLOCKED repeated status poll: this is the %%d%%s consecutive run of the same status check, and each "
    "run is a full turn that re-reads your whole context. No completion notification reaches this "
    "session, so a backgrounded job never wakes you: %s." % _WAIT_RECIPE
)
NO_WAKE_ADVICE = (
    "[wait] No completion notification reaches this session for a backgrounded job. To wait for this "
    "one, keep working, then %s; never re-check it call after call." % _WAIT_RECIPE
)


WATCH_REASON = (
    "BLOCKED session-watcher arm outside run_in_background: a watcher started in a foreground call, or detached with "
    "&, nohup or disown, delivers its wake to no task notification, and while it lives every correct arm exits COVERED. "
    "Run `%s` %s (reference/sidecar_dispatch.md step 6)."
)
COVERED_REASON = (
    "BLOCKED session-watcher arm: this session's watcher is already armed and live, and it covers every job this "
    "session launches, so this arm would exit COVERED and spend a wake on nothing. Launch the job without an arm "
    "(reference/sidecar_dispatch.md step 6)."
)
BATCH_ADVICE = (
    "[wait] This foreground proof batch is expected to take %s, past this call's %d s timeout. Run it with "
    "run_in_background: true and act on its one wake; a batch that ends inside its call stays in the foreground, "
    "which costs no wake (CLAUDE.core.md §Shell Discipline)."
)
RAISED_NOTE = (
    "[wait] Backgrounded this foreground call: its %d ms timeout says it may run long, and no recorded time says it "
    "fits. Keep working and act on its one wake (CLAUDE.core.md §Shell Discipline)."
)
DETACHED_ADVICE = (
    "[wait] A trailing `&` detaches this job outside the run_in_background wake (CLAUDE.core.md §Shell Discipline). "
    "Set run_in_background: true on this call instead and act on its one wake."
)
_PYTHON = re.compile(r"^(?:python[\d.]*|py)$")
_PROOF_RUNNERS = frozenset(("bash", "sh", "node", "pwsh", "powershell"))
_PROOF_NAME = re.compile(r"^(?:test_.+|.+_test)\.(?:py|sh|mjs|js|ps1)$")


def _ordinal(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _segments(command):
    return [s.strip(" \t()`$") for s in re.split(r"[;&|\n]+", command) if s.strip(" \t()`$")]


def is_status_only(command):
    """True when every segment of `command` only reads state (and nothing writes through `>`)."""
    text = str(command or "")
    if not text.strip() or ">" in _HARMLESS_REDIRECT.sub("", text):
        return False
    for seg in _segments(_HARMLESS_REDIRECT.sub("", text)):
        words = seg.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        if not words:
            continue
        seg = " ".join(words)
        verb = words[0].strip("'\"").lower()
        if verb.rsplit("/", 1)[-1].removesuffix(".exe") in _SHELL_WORDS:
            inner = re.split(r"\s-(?:Command|c)\s+", seg, maxsplit=1, flags=re.IGNORECASE)
            if len(inner) != 2 or not is_status_only(inner[1].strip().strip("\"'")):
                return False
            continue
        if verb not in _STATUS_VERBS:
            return False
    return True


def shell_code(command):
    """`command` with every heredoc body blanked whose reader is not a shell: that body is data."""
    def blank(m):
        words = re.findall(r"[\w.-]+", m.group("head"))
        reader = next((w.lower() for w in reversed(words) if not w.startswith("-")), "")
        if reader.rsplit("/", 1)[-1].removesuffix(".exe") in _SHELL_WORDS:
            return m.group(0)
        return m.group(0)[:m.start("body") - m.start()] + "\n" * m.group("body").count("\n") + \
            m.group(0)[m.end("body") - m.start():]
    return _HEREDOC.sub(blank, str(command or ""))


# Only a display count or a delay varies between polls of one target; file names and process ids keep their digits.
_COUNT_ARG = re.compile(
    r"(?<!\S)-\d+(?![\w.])"
    r"|(?<!\S)(?P<opt>-{1,2}(?:n|c|lines|bytes|tail|last|first|totalcount|seconds|s|milliseconds|m)(?:=|\s+)?)"
    r"\d+(?:\.\d+)?(?![\w.])"
    r"|(?<![\w-])(?P<sleep>(?:start-)?sleep\s+)\d+(?:\.\d+)?(?![\w.])",
    re.IGNORECASE)


def _shape(command):
    """The poll identity: the whole command with only count and delay arguments normalized, hashed to bound state."""
    text = _COUNT_ARG.sub(lambda m: (m.group("opt") or m.group("sleep") or "-") + "N",
                          re.sub(r"\s+", " ", str(command).strip()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _caller(payload):
    return str(payload.get("agent_id") or "").strip()


def _agent_tool_subagent(payload):
    """True when the caller's .meta.json sits where the runtime keeps an Agent-tool subagent's:
    <session>/subagents/agent-<id>.meta.json beside the orchestrator's <session>.jsonl. A Workflow job's sits under
    <session>/subagents/workflows/<run>/, and its agent_type is the job's agentType (e.g. <job>-author), so the type
    alone cannot tell the two apart."""
    transcript = str(payload.get("transcript_path") or "")
    if not transcript.endswith(".jsonl"):
        return False
    meta = os.path.join(transcript[:-len(".jsonl")], "subagents", "agent-%s.meta.json" % _caller(payload))
    return os.path.isfile(meta)


def never_woken(payload):
    """True when no completion notification reaches this caller for a backgrounded job."""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return True
    if not _caller(payload):
        return False
    kind = str(payload.get("agent_type") or "").strip()
    return kind in ("", "workflow-subagent") or not _agent_tool_subagent(payload)


def _count_poll(payload, command):
    """Record this call for its caller and return how many consecutive identical status polls it makes (0 when it
    is not one). Fails open to 0 when the state cannot be locked."""
    key = _caller(payload) or "main"
    poll = is_status_only(command) and not _LOOP_SLEEP.search(shell_code(command))
    shape = _shape(command) if poll else None

    def update(state):
        polls = state.setdefault(STATE_KEY, {})
        now = time.time()
        for other in [k for k, v in polls.items() if now - float((v or {}).get("at") or 0) > STALE_POLL_S]:
            del polls[other]
        prior = polls.get(key) or {}
        if not poll:
            polls.pop(key, None)
            return 0
        count = prior.get("count", 0) + 1 if prior.get("shape") == shape else 1
        polls[key] = {"shape": shape, "count": count, "at": now}
        return count

    path = _hook_state.state_path(payload.get("session_id"))
    if not poll:
        existing = _hook_state.read_json_salvage(path) if os.path.exists(path) else {}
        if key not in (existing.get(STATE_KEY) or {}):
            return 0
    written, count = _hook_state.update_json_locked(path, update)
    return count if written and count else 0


def _words(segment):
    """The segment's words after any leading `NAME=value` assignments, the program name normalized."""
    words = segment.split()
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words = words[1:]
    if words:
        words[0] = words[0].strip("'\"").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    return words


_QUOTED_SPAN = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def foreground_watch_arm(command):
    """True when a segment of `command` runs `watch_records.py --session` (a python or direct run, not a mention).
    A quoted span that holds a command separator is data (a payload, an echoed command), so it is blanked first."""
    code = _QUOTED_SPAN.sub(lambda m: "''" if re.search(r"[;&|\n]", m.group(0)) else m.group(0), shell_code(command))
    for segment in _segments(code):
        words = [w for w in _words(segment) if w not in ("nohup", "setsid", "exec")]
        if not words or "--session" not in words:
            continue
        script = words[1] if _PYTHON.match(words[0]) and len(words) > 1 else words[0]
        if script.strip("'\"").replace("\\", "/").endswith("watch_records.py"):
            return True
    return False


def _recorded(runner):
    try:
        with open(runner.durations_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _runner_args(words):
    """The runner's arguments when `words` is `python <...>harness_tests.py ...`, else None."""
    if not words or not _PYTHON.match(words[0]):
        return None
    rest = words[1:]
    while rest and rest[0].startswith("-") and rest[0] != "-":
        rest = rest[1:]
    if rest and rest[0].replace("\\", "/").rstrip("\"'").endswith("harness_tests.py"):
        return rest[1:]
    return None


def _proof_names(words):
    """Basenames of the `.claude/tests/` proofs `words` executes directly, else []."""
    program = words[0] if words else ""
    if not (_PYTHON.match(program) or program in _PROOF_RUNNERS):
        return []
    found = []
    for word in words[1:]:
        path = word.strip("'\"").replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        if "/tests/" in "/" + path and _PROOF_NAME.match(name):
            found.append(name)
    return found


def _parse_runner_args(args):
    """The runner's own parse of `args`, or None when it exits (`--help`, a rejected line). A leftover
    word that is not an option (a redirection) is tolerated; an unknown option, a retired flag
    included, makes a line the runner rejects."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            parsed, extra = _runner().build_parser().parse_known_args(args)
    except SystemExit:
        return None
    return None if any(word.startswith("-") for word in extra) else parsed


def _selected_proofs(parsed):
    """Basenames of the proofs a parsed `--for`/`--proofs-for` runner invocation selects."""
    harness_tests = _runner()
    paths = parsed.for_paths or parsed.proofs_for_paths or []
    root = os.path.abspath(parsed.repo or _PROJECT_ROOT)
    touched = [os.path.relpath(p, root).replace(os.sep, "/") if os.path.isabs(p) else p.replace("\\", "/")
               for p in paths]
    return [os.path.basename(p) for p in harness_tests.select_for(root, touched)] if touched else []


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _watch_records():
    """The module that owns the watcher arm text, loaded only when a deny needs it."""
    tools = os.path.join(_PROJECT_ROOT, ".claude", "tools")
    sys.path.insert(0, tools)
    try:
        import watch_records
    finally:
        sys.path.remove(tools)
    return watch_records


def _watcher_live(payload):
    """True when this session's hook state lists a live session-wide watcher."""
    path = _hook_state.state_path(payload.get("session_id"))
    return os.path.exists(path) and _watch_records().session_watcher_live(_hook_state.read_json_salvage(path))


def _runner():
    """The proof runner module, which owns proof selection and the latency budgets."""
    tools = os.path.join(_PROJECT_ROOT, ".claude", "tools")
    sys.path.insert(0, tools)
    try:
        import session_timeline
    finally:
        sys.path.remove(tools)
    return session_timeline.runner()


def _batch_estimate(command):
    """(whole battery, recorded seconds, every proof recorded) for a proof batch command, else None."""
    always, direct = False, []
    for segment in _segments(shell_code(command)):
        words = _words(segment)
        args = _runner_args(words)
        if args is not None:
            parsed = _parse_runner_args(args)
            if parsed is None or parsed.tiers:
                continue
            if parsed.for_paths or parsed.proofs_for_paths:
                direct += _selected_proofs(parsed)
            else:
                always = True
        else:
            direct += _proof_names(words)
    if not always and not direct:
        return None
    recorded = _recorded(_runner())
    return always, sum(float(recorded.get(name) or 0) for name in direct), all(recorded.get(n) for n in direct)


def _batch_advice(command, timeout_ms):
    """The advisory text when a woken caller's foreground command is a proof batch that can outlast its call."""
    try:
        estimate = _batch_estimate(command)
        if estimate is None:
            return None
        always, total, _known = estimate
        budget = int(timeout_ms or BASH_DEFAULT_MS) // 1000
        if not always and total <= budget:
            return None
        return BATCH_ADVICE % ("minutes (the whole battery)" if always else "%d s from its recorded proof times" % total,
                               budget)
    except Exception:  # an advisory never costs the call
        return None


def _timed_fit(command, timeout_ms):
    """True when the command is a proof batch whose every proof has a recorded time and the sum fits the timeout."""
    try:
        estimate = _batch_estimate(command)
    except Exception:
        return False
    return bool(estimate) and not estimate[0] and estimate[2] and estimate[1] <= int(timeout_ms) // 1000


# A redirect or `tee` target in a shell command; `2>&1` and `>&2` name a descriptor, not a file.
_WRITE_TARGET = re.compile(
    r"(?:\d?>>?|&>>?)\s*(?!&)(?P<redirect>\"[^\"]+\"|'[^']+'|[^\s;&|<>()]+)"
    r"|\btee\s+(?:-\w+\s+)*(?P<tee>\"[^\"]+\"|'[^']+'|[^\s;&|<>()]+)")
_LEADING_CD = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*&&")
_LOOP_CONDITION = re.compile(r"\b(?:until|while)\b([^\n;]*);\s*do\b")
_BENCH_LOOP = re.compile(r"\buntil\s+(?P<cond>[^\n;]*?)\s*;\s*do\s+sleep\s+\d+\s*;?\s*done\b")
_BENCH_END_WORDS = frozenset(("END", "ROSTER DONE", "HALT"))
_OUTPUT_PATH = re.compile(r"written to: (\S[^\n]*?\.output)")
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024


def _resolved(path, command, cwd):
    """One comparable absolute form of a file named in `command`, run from `cwd`; None for a path that is not a plain file
    name (a variable, a substitution, /dev/null)."""
    path = path.strip().strip("\"'").replace("\\", "/")
    if not path or "$" in path or "`" in path or path == "/dev/null":
        return None
    path = drive_path(path)
    cd = _LEADING_CD.match(command)
    base = drive_path(cd.group(1).strip("\"'").replace("\\", "/")) if cd else cwd
    return os.path.normcase(os.path.abspath(os.path.join(base or "", path)))


def _live_background_writes(payload):
    """{resolved file: (task id, description)} for every file a still-running `run_in_background` command of this
    session writes: its redirect and `tee` targets and its task output file. Read from the transcript, as
    kill_guard does: a launch is the tool_use row plus the tool_result row carrying `toolUseResult.backgroundTaskId`;
    a task ended once any `<task-id>` notification names it. A sidechain row (a subagent's) is not this session's."""
    transcript = str(payload.get("transcript_path") or "")
    try:
        with open(transcript, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_BYTES:
                fh.seek(size - MAX_TRANSCRIPT_BYTES)
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return {}
    launches, tasks, ended = {}, {}, set()
    for line in lines:
        if "task-notification" in line:
            ended.update(_background_tasks.ENDED_RE.findall(line))
        if "run_in_background" not in line and "backgroundTaskId" not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("isSidechain"):
            continue
        for item in (row.get("message") or {}).get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and (item.get("input") or {}).get("run_in_background"):
                launches[item.get("id")] = (item["input"], row.get("cwd"))
            elif item.get("type") == "tool_result" and _background_tasks.launched_task_id(row):
                found = _OUTPUT_PATH.search(str(item.get("content")))
                tasks[_background_tasks.launched_task_id(row)] = (item.get("tool_use_id"), found.group(1) if found else None)
    cwd = str(payload.get("cwd") or "")
    writes = {}
    for task, (use_id, output) in tasks.items():
        if task in ended or use_id not in launches:
            continue
        given, launch_cwd = launches[use_id]
        command = str(given.get("command") or "")
        named = [m.group("redirect") or m.group("tee") for m in _WRITE_TARGET.finditer(command)]
        for path in [*(_resolved(n, command, launch_cwd or cwd) for n in named), _resolved(output or "", "", cwd)]:
            if path:
                writes.setdefault(path, (task, str(given.get("description") or command)[:60]))
    return writes


def _live_writer(payload, command):
    """(task id, description, file) of a live background task of this session that writes a file `command`'s loop
    condition reads, else None. Exact on the file: a path the condition does not name, or a task that has ended, never
    matches. Fails open to None."""
    try:
        condition = _LOOP_CONDITION.search(shell_code(command))
        if not condition:
            return None
        try:
            words = shlex.split(condition.group(1).replace("\\", "/"))
        except ValueError:
            words = condition.group(1).split()
        writes = _live_background_writes(payload)
        cwd = str(payload.get("cwd") or "")
        for word in words:
            found = writes.get(_resolved(word, command, cwd) or "")
            if found:
                return found[0], found[1], word
    except Exception:  # a guard never costs the call
        pass
    return None


WAKE_NOTE = (
    "[wait] Background task %s (%s) is still running and writes %s, so its completion already wakes you and "
    "this wait is a second wake. Act on that wake (CLAUDE.core.md §Shell Discipline \"act on the one completion wake\")."
)
BENCH_NOTE = (
    "[wait] Rewrote the hand-written loop on bench log %s to the bench's own `wait` subcommand (the rewritten call), "
    "which owns the check-in deadline (its `wait --help`)."
)


def _bench_roots():
    """bench's own root accessors (core/roots.py), or None when the bench suite cannot be loaded."""
    bench = os.path.join(_PROJECT_ROOT, ".claude", "scripts", "benchmark_campaign")
    sys.path.insert(0, bench)
    try:
        from core import roots
        return roots
    except Exception:
        return None
    finally:
        sys.path.remove(bench)


def _bench_log(path, roots):
    """`path` in posix spelling when it is a roster log (`<logs>/<name>.log`) or a run log directly under a run
    directory of the logs or results root, else None."""
    key = roots.path_key(path)
    if not key.endswith(".log"):
        return None
    parent = os.path.dirname(key)
    logs = roots.path_key(roots.logs_dir())
    results = roots.path_key(roots.path(roots.VAULT_RESULTS))
    if parent == logs or os.path.dirname(parent) in (logs, results):
        return roots.posix_path(roots.native_path(path.replace("\\", "/")))
    return None


def _bench_wait(command):
    """(rewritten command, log) when `command` holds one until-loop whose only test is `grep -q[E] '<END pattern>' <log>`
    on a bench roster or run log, with the loop replaced by the bench suite's `wait <log>` subcommand; else None. Fails open to None."""
    try:
        code = shell_code(command)
        loops = _BENCH_LOOP.findall(code)
        if len(loops) != 1 or len(_LOOP_SLEEP.findall(code)) != 1:
            return None
        match = _BENCH_LOOP.search(code)
        words = shlex.split(match.group("cond").replace("\\", "/"))
        if words and words[-1] == "2>/dev/null":
            words = words[:-1]
        if len(words) != 4 or words[0] != "grep" or not re.fullmatch(r"-(?:qE|Eq|q)", words[1]):
            return None
        parts = [p.strip().lstrip("^").strip() for p in re.sub(r"^\(|\)$", "", words[2]).split("|")]
        if not set(parts) <= _BENCH_END_WORDS:
            return None
        target = words[3]
        var = re.fullmatch(r"\$(\w+)|\$\{(\w+)\}", target)
        if var:
            name = var.group(1) or var.group(2)
            assigned = re.findall(r"(?:^|[;&\n]\s*)%s=(\"[^\"$`]+\"|'[^']+'|[^\s;&|$`\"']+)\s*(?=[;&\n])" % re.escape(name), code)
            if len(assigned) != 1:
                return None
            target = assigned[0].strip("\"'")
        elif "$" in target or "`" in target:
            return None
        roots = _bench_roots()
        log = _bench_log(target, roots) if roots and os.path.isabs(roots.native_path(target.replace("\\", "/"))) else None
        if not log:
            return None
        bench = roots.posix_path(roots.BENCH_PY)
        call = "python3 %s wait %s" % (shlex.quote(bench), shlex.quote(log))
        return command[:match.start()] + call + command[match.end():], log
    except Exception:  # a guard never costs the call
        return None


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def _advise(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))


def _check_in(payload, command, tool_input, note, bounded=False):
    """A woken orchestrator's backgrounded wait loop ends by ORCHESTRATOR_WAIT_MS, so its wake lands inside the prompt
    cache even when the job it waits on never ends; the wake is the check-in. The client enforces no `timeout` on a
    background call, so the deadline is the command's own: a Bash loop runs
    under coreutils `timeout`, exit 124. Remove the wrapper when the client enforces a background call's timeout.
    PowerShell has no such wrapper and keeps its command."""
    seconds = ORCHESTRATOR_WAIT_MS // 1000
    if payload.get("tool_name") == "Bash" and not bounded and not _DEADLINE.match(command):
        tool_input = dict(tool_input, command="%s %d bash -c %s" % (TIMEOUT_BIN, seconds, shlex.quote(command)))
        note = ((note + ' ') if note else '') + (
            '[wait] Bounded the background wait at %d s by its own command, inside the one-hour prompt cache: exit 124 '
            'is the check-in: judge the awaited job from its progress, then act or re-arm '
            '(the rule: tools/watch_records.py CHECKIN_RULE).' % seconds)
    if note is None:
        return 0
    _count_poll(payload, command)
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
          'updatedInput': tool_input, 'additionalContext': note}}))
    return 0


BLIND_REASON = (
    "[wait] DENIED: this backgrounded %s pipes its output into `%s`, which holds every line until the job exits and "
    "drops the lines past its count (CLAUDE.core.md §Shell Discipline). Drop the `| %s`: the background task's output "
    "file keeps every line, and a bounded Read of it after the wake takes the part you need."
)


def _invoked_script(words):
    """The basename of the script `words` execute (`python <script>`, `pwsh [-File] <script>`, `./x.ps1`), else None.
    A command that only names a script (a git pathspec, a grep target) executes none."""
    if not words:
        return None
    program = words[0].strip("'\"").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if _PYTHON.match(words[0]) or program in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        rest = words[1:]
        while rest and rest[0].startswith("-") and rest[0].lower() != "-file":
            rest = rest[1:]
        if rest and rest[0].lower() == "-file":
            rest = rest[1:]
        target = rest[0] if rest else None
    else:
        target = words[0] if program.endswith(".ps1") else None
    return target.strip("'\"").replace("\\", "/").rsplit("/", 1)[-1] if target else None


def _producer(words):
    """A label when `words` run a job that streams progress for as long as it runs, else None."""
    args = _runner_args(words)
    if args is not None:
        return "harness_tests.py run" if (not args or "--close" in args or "--all" in args) else None
    script = _invoked_script(words)
    if not script:
        return None
    flags = {w.lower() for w in words if w.startswith("-")}
    if script.lower().endswith(".ps1") and "-watch" in flags:
        return "%s -Watch" % script
    if script == "sidecar_fanout.py" and not flags & {"--detach", "--status", "--dry-run", "--review-artifact", "--help"}:
        return "sidecar_fanout.py run"
    return None


def blind_pipe(command):
    """(producer, consumer) when a backgrounded pipeline's stdout reaches a later `tail` or `head` stage unredirected
    and unteed, else None. The task's output file already keeps every line for a later bounded Read, so the pipe only
    hides progress until exit and drops the lines past its count. `tail -f` follows a file and stays live."""
    for pipeline in re.split(r"\|\||&&|[;\n]|(?<![>&])&(?![&>])", shell_code(command)):
        stages = [s.strip(" \t()`$") for s in re.split(r"(?<![|>])\|(?!\|)", pipeline)]
        for i, stage in enumerate(stages[:-1]):
            words = _words(stage)
            if words and words[0] == "tee":
                break
            producer = _producer(words) or (words[0] if words else None)
            if not producer or ">" in re.sub(r"\d?>&\d|2>\s*/dev/null|2>\S+", "", stage):
                continue
            for later in stages[i + 1:]:
                words = _words(later)
                if words and words[0] == "tee":
                    break
                if words and words[0] in ("tail", "head") and not {"-f", "-F", "--follow"} & set(words[1:]):
                    return producer, words[0]
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Monitor":
        try:
            asked = int(tool_input.get("timeout_ms") or 0)
        except (TypeError, ValueError):
            return 0
        if asked > MONITOR_CAP_MS:
            return _deny(MONITOR_REASON % (asked, MONITOR_CAP_MS, MONITOR_CAP_MS // 60000, _watch_records().ARM_COMMAND,
                                           ORCHESTRATOR_WAIT_MS // 60000, MONITOR_CAP_MS))
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str(tool_input.get("command") or "")
    unwoken = never_woken(payload)
    if tool_input.get("run_in_background"):
        blind = blind_pipe(command)
        if blind:
            return _deny(BLIND_REASON % (blind[0], blind[1], blind[1]))
        if not (unwoken or _caller(payload)) and foreground_watch_arm(command) and _watcher_live(payload):
            return _deny(COVERED_REASON)
        if unwoken:
            _advise(NO_WAKE_ADVICE)
        elif _LOOP_SLEEP.search(shell_code(command)):
            bench = _bench_wait(command)
            if bench:
                _check_in(payload, bench[0], dict(tool_input, command=bench[0]), BENCH_NOTE % bench[1], bounded=True)
            else:
                _check_in(payload, command, tool_input, None)
        return 0
    if not (unwoken or _caller(payload)) and foreground_watch_arm(command):
        return _deny(WATCH_REASON % (_watch_records().ARM_COMMAND, _watch_records().ARM_CALL))
    advice = None if unwoken else _batch_advice(command, tool_input.get("timeout"))
    if _LOOP_SLEEP.search(shell_code(command)):
        try:
            budget = int(tool_input.get("timeout") or BASH_DEFAULT_MS)
        except (TypeError, ValueError):
            budget = BASH_DEFAULT_MS
        updated, note = None, None
        if _caller(payload) or unwoken:
            if budget > SUBAGENT_WAIT_MS:
                updated = dict(tool_input, timeout=SUBAGENT_WAIT_MS)
                note = '[wait] Clamped the foreground wait timeout from %d to %d ms (guards/any.md).' % (budget, SUBAGENT_WAIT_MS)
        elif budget > SUBAGENT_WAIT_MS:
            writer = _live_writer(payload, command)
            notes = ['[wait] Backgrounded the long foreground wait; act on its one wake (CLAUDE.core.md §Shell Discipline).']
            if writer:
                notes.insert(0, WAKE_NOTE % writer)
            bench = _bench_wait(command)
            if bench:
                command = bench[0]
                notes.append(BENCH_NOTE % bench[1])
            return _check_in(payload, command, dict(tool_input, command=command, run_in_background=True),
                             ' '.join(notes), bounded=bool(bench))
        if updated:
            _count_poll(payload, command)
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                  'updatedInput': updated, 'additionalContext': note}}))
            return 0
    elif not (_caller(payload) or unwoken):
        try:
            budget = int(tool_input.get("timeout") or BASH_DEFAULT_MS)
        except (TypeError, ValueError):
            budget = BASH_DEFAULT_MS
        if budget > BASH_DEFAULT_MS and not _timed_fit(command, budget):
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                  'updatedInput': dict(tool_input, run_in_background=True), 'additionalContext': RAISED_NOTE % budget}}))
            return 0
    count = _count_poll(payload, command)
    if count >= POLL_LIMIT:
        return _deny((POLL_REASON_AGENT if unwoken else POLL_REASON_MAIN) % (count, _ordinal(count)))
    detached = payload.get("tool_name") == "Bash" and _DETACHED_LINE.search(shell_code(command))
    notes = [note for note in (advice, DETACHED_ADVICE if detached else None) if note]
    if notes:
        _advise(" ".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
