#!/usr/bin/env python3
"""Guard irreversible stops.

Bash/PowerShell kills resolve each PID before acting and refuse harness-critical processes. A kill addressed
by name or pattern (`pkill`, `killall`, `taskkill /IM` or /FI, `Stop-Process -Name` or -Id, `Get-Process <n> | Stop-Process`,
`wmic process where ... delete`, a Win32_Process Terminate, and `kill $(pgrep ...)`, `pgrep ... | xargs kill` or
`$(pidof ...)`) resolves to the live processes it would stop, read from the same process table the
KILL_GUARD_PROCESS_TABLE seam plants, and is refused when any match is protected. It is also refused when the table
cannot be read or the pattern cannot be resolved (an invalid regex, a Where-Object or unparsed /FI filter, an
`xargs kill` fed by anything but pgrep). A kill whose every match is unprotected passes. Not covered: a PID read
from a variable or file, `.Kill()` and other in-language kills, and kills inside a script file the command runs. TaskStop
requires a stop request in the owner's latest exact message whose object is this task by name, a
generic reference ("stop it", "stop that agent") or nothing (a bare "stop"); a phrase naming a
different label-shaped task, or a later keep-running instruction, blocks. A mechanism correction
applies to future dispatches and never justifies discarding work already spent.

One exception needs no owner wording (owner decision 2026-09-15): a background Bash/PowerShell shell
or a Monitor THIS session launched (not a subagent's) whose command, and the readable text of the
local scripts it runs (MAX_SCRIPT_DEPTH levels), matches nothing in PROTECTED. A stop kills the shell's live
process tree, so when that tree can be read (the processes whose command line carries the launch command, and
their descendants) its command lines replace the script text: a script that only names a launcher, as an
argument or an env value it hands a detached job, runs no billed arm. In the command, each segment runs
its first script; a script named after it is an argument, data the run reads, and its text is not scanned. A Monitor is a poll
loop by construction; a superseded one keeps re-emitting stale lines until it is stopped. A script it cannot read, at the top level
or built from a variable, fails the exception closed. That is a waiter, monitor or poll loop; stopping it discards no paid
work, and demanding consent for it leaves superseded jobs running. The same exception covers a shell whose only
PROTECTED matches are class "local" (a gate, verification, test or proof run), at any age: its stop discards only
this machine's time, and a stuck or superseded run held for consent holds its locks and slots instead. A
"billed" match keeps the owner-wording rule.

A second floor guard in the same hook (class floor: it protects peer sessions) denies a Bash/PowerShell command
that executes a terminal-window close by handle, title or host process (`window_close_verdict`). It has no
text override: a reason the agent writes into the call proves no ownership of the window.

WHY THIS EXISTS: `shell_census.py` lists long-running shells with their command tails TRUNCATED, and
its own text hands over `taskkill /PID <pid> /T /F`. Measured 2026-09-08: a session read that list,
meant to kill a stray home-directory `grep`, matched the wrong row, and killed its own
`sidecar_fanout.py` run mid-flight -- destroying a paid comparison arm. A later session stopped a
healthy Agent after correcting its dispatch mechanism, then proposed paying for the same work again.

A stop is irreversible and the display is lossy, so this gate fails CLOSED (instruction_quality
section 16). Bash/PowerShell bypass, per target: `HARNESS_ALLOW_KILL=<pid> taskkill /PID <pid> /T /F`. It covers
a PID only, never a name or pattern kill.
TaskStop has no text override: ask the user to stop the task, then re-issue it (the quiet-shell
exception above is structural, never a phrase).
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _background_tasks  # noqa: E402
from _owner_text import classify  # noqa: E402
from _win_path import native_path  # noqa: E402

# Killing any of these takes down work that cannot be resumed from where it stopped: a billed
# sidecar arm, a gate/suite run, or a peer's whole session.
# Class "billed": stopping it discards paid, unresumable work or another session's. Class "local": it
# discards only this machine's time, so a run this session launched may be stopped with no owner wording.
PROTECTED = (
    (r"sidecar_fanout\.py", "a sidecar fan-out — its in-flight arms are billed and unresumable", "billed"),
    (r"_sidecar\.sh|sidecar_launch\.py", "a sidecar launcher child — the arm dies with it", "billed"),
    (r"harness_tests\.py", "the harness proof runner", "local"),
    (r"regression_gate|gate_chain|verify\.ps1", "a gate/verification run", "local"),
    (r"vstest|dotnet\s+test", "a test run", "local"),
    # NOT \bclaude\b — that matches `.claude/` (present in nearly every harness command) and
    # `claude-gpt`. Match the EXECUTABLE: `claude.exe`, or `claude` standing as the command word.
    (r"(?:^|[\s/\\\"])claude\.exe|(?:^|[\s/\\\"])claude(?:\s|$)",
     "a Claude Code session — possibly a peer's", "billed"),
    (r"claude-code-proxy", "a transport proxy other sessions may be routing through", "billed"),
    # Bound to the command word: a grid named as a proof input (`harness_tests.py --for run_grid.sh`) is data.
    (r"(?:^|[;&|(\n]\s*)(?:(?:nohup|exec|setsid)\s+)?(?:(?:bash|sh|pwsh|powershell)\s+(?:-\S+\s+)*)?[\"']?"
     r"(?:\S*[/\\])?run_grid(?:\.snapshot[\w.-]*)?\.sh",
     "a benchmark grid — its in-flight cell is a billed arm", "billed"),
)
# A proof's own temp root (harness_tests.py mkdtemp prefix). A process running there is the proof's sandbox
# copy, not a grid this session launched, so it does not make a proof run billed.
PROOF_SANDBOX_RE = re.compile(r"[\\/]harness_(?:proof|commit)_\w+[\\/]")
CAT_SUBST_RE = re.compile(r"\$\(\s*cat\s+(\"[^\"]+\"|'[^']+'|[^\s)\"']+)\s*\)")
# Local scripts a shell command runs. Their TEXT is scanned too: a background `bash waiter.sh` that
# calls a sidecar holds a billed arm exactly as a direct launch does.
# Glob characters are excluded: `find -name "*.py"` names a pattern, not a script to read.
SCRIPT_REF_RE = re.compile(r"[^\s\"'|;&<>()=*?\[\]]+\.(?:sh|py|ps1|js|cmd|bat)(?![\w.])", re.I)
# The process-table read runs inside the hook's 20 s settings budget (HOOK_BUDGET_S below).
TABLE_TIMEOUT_S = 10
MAX_SCRIPT_BYTES = 512 * 1024
MAX_SCRIPT_DEPTH = 2
PS_BLOCK_COMMENT_RE = re.compile(r"<#.*?#>", re.S)
FULL_LINE_COMMENT_RE = re.compile(r"\s*(?:#|//|REM\s)")
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|&\n(`]|\$\(")

PID_RE = re.compile(r"(?:/PID|/pid|-PID)\s+(\d+)|(?:^|\s|;|&&|\|)kill\s+(?:-\w+\s+)?(\d+)\b")
# settings.json gives this hook 20 s. Every PID lookup runs inside one budget under it; a lookup the
# budget cannot afford resolves nothing, so the kill fails closed instead of the hook timing out open.
HOOK_BUDGET_S = 15.0
SUBPROCESS_TIMEOUT_S = 5
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
STOP_VERB = r"(?:stop|cancel|kill|terminate|abort|end)"
TASKSTOP_DIRECTIVE_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:please\s+)?" + STOP_VERB + r"\b"
    r"|\bplease\s+" + STOP_VERB + r"\b"
    r"|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?" + STOP_VERB + r"\b"
    r"|\bi\s+(?:want|need|asked)\s+you\s+to\s+" + STOP_VERB + r"\b"
    r"|\byou\s+(?:should|must)\s+" + STOP_VERB + r"\b"
    r"|\bgo\s+ahead\s+and\s+" + STOP_VERB + r"\b"
    # A second stop order appended to another instruction -- "Also stop the two hung shells", "and
    # then stop it". Without this branch the verb never matched, so the order read as a non-stop
    # (2026-09-18: two hung sidecars survived an owner message that said "also stop").
    r"|(?:^|[.!?;,]|\band\b|\bbut\b|\bso\b)\s*(?:also|then|now|just|please)\s+" + STOP_VERB + r"\b"
    # An imperative appended to an earlier instruction: "also stop the codex shells". Without this
    # branch the verb matched no pattern at all and the directive was never seen, so the phrase
    # after it was never examined either.
    r"|(?:^|[;,]|\band\b|\bbut\b)\s*(?:also|then|now|please|just)\s+" + STOP_VERB + r"\b"
    # "Yes, and stop duplicate": the verb follows "and" directly.
    r"|(?:[;,]|\band\b)\s*" + STOP_VERB + r"\b",
    re.I,
)
NEGATED_STOP_RE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+" + STOP_VERB + r"\b",
    re.I,
)
KEEP_RE = re.compile(r"\b(?:keep|let)\b", re.I)
TASK_NOUN = r"(?:agent|task|job|delegate|subagent|workflow|run)"
# Owner decision 2026-09-14: a stop may refer to its task generically or be bare; a phrase that names
# a different label-shaped task (`task-a`, `gap_fix`) refers to that task instead.
GENERIC_OBJECT_RE = re.compile(
    r"\s+(?:it|that|this|them|(?:(?:the|this|that|these|those|all|any)\s+)?"
    r"(?:(?:running|existing|background|current|old|extra|other)\s+)?"
    r"(?:agent|task|job|delegate|subagent|workflow|run|lane|duplicate|copy)s?)\b",
    re.I,
)
BARE_TAIL_RE = re.compile(r"(?:\s*,?\s*(?:now|please|everything|all))*\s*", re.I)
LABEL_TOKEN_RE = re.compile(r"(?<![\w-])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+(?![\w-])")
KEEP_WORDS_RE = re.compile(r"\b(?:running|run|finish|continue|alive|going)\b", re.I)
# An owner describes a task by what it is doing, not by its id: "stop the two hung codex shells"
# names no task_id, label, generic noun or bare tail, so every earlier branch rejected it and a
# clearly-addressed stop was refused. Match the description against the task's own launch text.
# Only a word shared with that launch counts, so a phrase naming some OTHER thing still misses.
# Split on EVERY non-alphanumeric, underscore included: a launch command carries its words inside
# filenames (`codex_proxy_sidecar.sh`), and keeping `_` whole would tokenize that as one word so an
# owner's natural "the codex shells" shares nothing with it for no good reason.
DESCRIPTION_TOKEN_RE = re.compile(r"[A-Za-z0-9]{4,}")
# Function words and state adjectives an owner adds freely; they carry no identity of their own.
DESCRIPTION_STOPWORDS = frozenset((
    "that", "this", "these", "those", "them", "they", "their", "there", "then", "than",
    "with", "from", "into", "over", "under", "about", "just", "only", "also", "still",
    "stop", "cancel", "terminate", "abort", "please", "hung", "stuck", "idle", "dead",
    "running", "background", "existing", "current", "shell", "shells", "task", "tasks",
))


def _descriptive_tokens(text):
    """Identity-bearing words of a phrase: long enough, and not a free-floating function word."""
    return {token for token in DESCRIPTION_TOKEN_RE.findall((text or "").lower())
            if token not in DESCRIPTION_STOPWORDS}


def _launch_text(launch):
    """What the task actually runs, for matching how an owner would describe it."""
    if not launch:
        return ""
    tool_input = launch[1] if isinstance(launch, tuple) and len(launch) > 1 else {}
    if not isinstance(tool_input, dict):
        return ""
    return " ".join(str(tool_input.get(key) or "")
                    for key in ("command", "task_id", "label", "description"))


def _describes_launch(phrase, launch):
    """True when the owner's object phrase shares a distinctive word with this task's launch."""
    return bool(_descriptive_tokens(phrase) & _descriptive_tokens(_launch_text(launch)))
# AskUserQuestion answers are picked options, not prose: the question names the task, the label
# carries the stance. A negation or wait word wins over a stop verb, so "Don't stop it" never counts.
STOP_LABEL_RE = re.compile(r"\b" + STOP_VERB + r"\b", re.I)
KEEP_LABEL_RE = re.compile(r"\b(?:no|not|don't|dont|leave|keep|let|wait|finish)\b", re.I)
# Neutral follow-ups ("Try now", "ok", a question about something else) between an owner's stop
# order and the TaskStop that acts on it. Kept deliberately wide: a consent window of 2 made an
# owner's clear stop expire while the target was STILL RUNNING, so re-issuing TaskStop was refused
# and two hung sidecars could not be stopped at all (2026-09-18). Staleness is also redundant here
# -- `taskstop_authorized` compares the LAST positive stop against the LAST keep, so a later "keep
# it running" revokes regardless of how far back the stop was found, and `_target_patterns` pins
# the match to this task so an old consent cannot leak onto a different one.
MAX_NEUTRAL_SKIP = 20
# Runtime text injected anywhere in an owner row. It is never owner consent, and its hyphenated tag
# name would otherwise read as another task's label.
INJECTION_BLOCK_RE = re.compile(
    r"<(system-reminder|user-prompt-submit-hook|local-command-caveat)\b[^>]*>.*?</\1>", re.I | re.S)


def _consent_text(row):
    """The owner text a stop request is read from; the `_owner_text` table's kill_guard column."""
    if row is None or row.meta or row.sidechain or row.tool_results:
        return None
    if not row.text or row.envelope:
        return None
    if "<command-name>" in row.text:
        return row.command_args or None
    return INJECTION_BLOCK_RE.sub(" ", row.text).strip() or None


def _answer_pairs(row):
    if row is None or row.meta or row.sidechain or row.pairs is None:
        return None
    return list(row.pairs)


def _message_text(entry):
    return _consent_text(classify(entry, 0))


def _target_patterns(tool_input):
    if not isinstance(tool_input, dict):
        return []
    values = []
    for key in ("task_id", "label", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in values:
            values.append(value.strip())
    patterns = []
    for value in values:
        exact = re.escape(value)
        human = re.escape(re.sub(r"[-_/]+", " ", value))
        human = human.replace(r"\ ", r"[\s_-]+")
        patterns.append(re.compile(r"(?<!\w)(?:" + exact + "|" + human + r")(?!\w)", re.I))
    return patterns


def _names_target(message, object_start, patterns, *, keep=False):
    tail = message[object_start:object_start + 512]
    prefix = r"\s+(?:(?:the|this|that)\s+)?(?:(?:running|existing|background)\s+)?(?:" + TASK_NOUN + r"\s+)?(?:(?:named|called)\s+)?"
    for pattern in patterns:
        match = re.match(prefix + r"(?:" + pattern.pattern + r")", tail, re.I)
        if not match:
            continue
        if keep and not re.search(r"\b(?:running|run|finish|continue|alive)\b", tail[match.end():match.end() + 80], re.I):
            continue
        return True
    return False


def _object_phrase(message, start):
    """The words after a stop or keep word, up to the end of their clause (at most 160 chars).

    A later clause ("stop it, it is off-track") is not the verb's object, so its hyphenated words
    never read as another task's label."""
    sentence = re.split(r"[.!?\n]", message[start:start + 160], maxsplit=1)[0]
    return re.split(r"[,;:]|\s(?:and|but|then|so|because|since|while)\s", sentence, maxsplit=1)[0]


def _refers_to_task(message, start, patterns, *, keep=False, launch=None):
    """True when the phrase after a stop (or keep) word is this task by name, by a distinctive word
    shared with what this task actually runs, a generic reference ("it", "that agent"), or, for a
    stop, nothing at all. A phrase naming another label-shaped task refers to that task instead."""
    if _names_target(message, start, patterns, keep=keep):
        return True
    phrase = _object_phrase(message, start)
    if LABEL_TOKEN_RE.search(phrase):
        return False
    if _describes_launch(phrase, launch):
        return True
    generic = GENERIC_OBJECT_RE.match(phrase)
    if keep:
        return bool(generic and KEEP_WORDS_RE.search(phrase))
    return bool(generic or BARE_TAIL_RE.fullmatch(phrase))


def _engages_task(message, tool_input, launch=None):
    """True when this owner message carries a stop or keep directive ABOUT THIS TASK.

    A message that merely CONTAINS a stop-ish word while talking about something else -- "let it
    finish" about a workflow, or a quoted error block -- must not decide this task's consent. The
    walk treats a decisive row as final, so without this an unrelated keep ended the search before
    the owner's actual stop order was reached (2026-09-18: two hung sidecars stayed unstoppable
    through repeated stop orders)."""
    targets = _target_patterns(tool_input)
    if not targets:
        return False
    for pattern, keep in ((TASKSTOP_DIRECTIVE_RE, False), (NEGATED_STOP_RE, False), (KEEP_RE, True)):
        for match in pattern.finditer(message):
            if _refers_to_task(message, match.end(), targets, keep=keep, launch=launch):
                return True
    return False


def taskstop_authorized(message, tool_input, launch=None):
    if not message:
        return False
    targets = _target_patterns(tool_input)
    if not targets:
        return False
    positive = [
        match.start()
        for match in TASKSTOP_DIRECTIVE_RE.finditer(message)
        if _refers_to_task(message, match.end(), targets, launch=launch)
    ]
    revoked = [
        match.start()
        for match in NEGATED_STOP_RE.finditer(message)
        if _refers_to_task(message, match.end(), targets, launch=launch)
    ]
    revoked.extend(
        match.start()
        for match in KEEP_RE.finditer(message)
        if _refers_to_task(message, match.end(), targets, keep=True, launch=launch)
    )
    return bool(positive) and max(positive) > max(revoked, default=-1)


def _askuserquestion_answers(entry):
    """(question, chosen label) pairs from an owner AskUserQuestion answer row, else None. Any other
    tool_result lacks `toolUseResult.questions` plus `answers`, so a Bash output never reads as consent."""
    return _answer_pairs(classify(entry, 0))


def _owner_inputs(transcript_path):
    """Owner messages and AskUserQuestion answers in transcript order, or None when unreadable."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_BYTES:
                fh.seek(size - MAX_TRANSCRIPT_BYTES)
            content = fh.read()
        if size > MAX_TRANSCRIPT_BYTES and b"\n" in content:
            content = content.split(b"\n", 1)[1]
    except (OSError, TypeError, ValueError):
        return None
    inputs = []
    for line in content.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        row = classify(entry, len(inputs), raw=line)
        text = _consent_text(row)
        if text is not None:
            inputs.append(("text", text))
            continue
        pairs = _answer_pairs(row)
        if pairs is not None:
            inputs.append(("answer", pairs))
    return inputs


def _is_neutral(message):
    return not (TASKSTOP_DIRECTIVE_RE.search(message) or NEGATED_STOP_RE.search(message)
                or KEEP_RE.search(message))


def _answer_verdict(pairs, tool_input):
    """True or False for the last answer whose question names this task; None when none does."""
    targets = _target_patterns(tool_input)
    verdict = None
    for question, label in pairs:
        if not any(pattern.search(question) for pattern in targets):
            continue
        verdict = bool(STOP_LABEL_RE.search(label)) and not KEEP_LABEL_RE.search(label)
    return verdict


CANNOT_VERIFY = ("BLOCKED TaskStop (CLAUDE.core.md §Shell Discipline) — cannot verify an explicit owner instruction to stop this task. "
                 "Let it finish, or ask the owner and re-issue TaskStop after their answer.")
NOT_NAMED = ("BLOCKED TaskStop (CLAUDE.core.md §Shell Discipline) — the owner's latest message or question answer does not name this task, "
             "refer to it generically (\"stop it\", \"stop that agent\") or ask for a bare stop. Let it "
             "finish and consume the result. A request naming another task, or a later keep-running "
             "instruction, grants no consent.")


def _launch_of(transcript_path, task_id):
    """(tool name, tool input, launch timestamp or None) of the main-session call that launched background
    task `task_id`, else None.

    The launch is the tool_result row whose `toolUseResult.backgroundTaskId` (Bash/PowerShell) or
    `toolUseResult.taskId` (Monitor) is the id; its tool_use_id names the assistant tool_use. Either row on a sidechain (a subagent) is not this session's launch."""
    if not transcript_path or not task_id:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_BYTES:
                fh.seek(size - MAX_TRANSCRIPT_BYTES)
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, TypeError, ValueError):
        return None
    use_id = None
    for line in lines:
        if task_id not in line or ("backgroundTaskId" not in line and "taskId" not in line):
            continue
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
    for line in lines:
        if use_id not in line or '"tool_use"' not in line:
            continue
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


def _local_path(token, cwd):
    path = native_path(token.strip("\"'"))
    return path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)


def _fanout_calls(command):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    import sidecar_argv
    return [call for call in sidecar_argv.launcher_invocations(command) if call.kind == "fanout"]


def _shell_segments(command):
    """Executing stages, with quoted separators preserved and substitutions visited separately."""
    def scan(start, stop=None):
        stages, substitutions, text, quote, depth = [], [], [], None, 0
        i = start
        while i < len(command):
            char = command[i]
            if char == "\\" and quote != "'" and i + 1 < len(command):
                text.append(command[i:i + 2])
                i += 2
                continue
            if stop == ")" and quote is None:
                if char == "(":
                    depth += 1
                elif char == ")" and depth:
                    depth -= 1
                    text.append(char)
                    i += 1
                    continue
            if char == stop and quote is None:
                stages.append("".join(text))
                return stages + substitutions, i + 1
            if quote != "'" and (command.startswith("$(", i) or char == "`"):
                backtick = char == "`"
                nested, i = scan(i + (1 if backtick else 2), "`" if backtick else ")")
                substitutions.extend(nested)
                text.append("''")
                continue
            if char in ("'", '"'):
                if quote == char:
                    quote = None
                elif quote is None:
                    quote = char
            boundary = SEGMENT_SPLIT_RE.match(command, i) if quote is None else None
            # Parentheses in inline Python/PowerShell are calls, not shell stage boundaries.
            if boundary and boundary.group() != "(":
                if boundary.group() == "|":
                    text.append("|")
                stages.append("".join(text))
                text = []
                i = boundary.end()
            else:
                text.append(char)
                i += 1
        stages.append("".join(text))
        return stages + substitutions, i

    return [stage for stage in scan(0)[0] if stage.strip()]


def _run_refs(command):
    """The script each executing stage runs: its first script reference. Later ones are arguments."""
    refs = []
    for segment in _shell_segments(command):
        found = [token for token in SCRIPT_REF_RE.findall(segment)
                 if 'sidecar_fanout.py' not in token.lower() or _fanout_calls(segment)]
        if found:
            refs.append(found[0])
    return refs


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


def _work_texts(command, cwd):
    """(texts, unreadable): the command plus the local scripts it runs, MAX_SCRIPT_DEPTH deep. In the command
    those are each segment's first script (`_run_refs`); in a script's text, every script it names.
    `unreadable` names the first script that blocks the exception: one the command itself runs, or a
    nested one built from a variable (`$HERE/x.sh`), whose body could launch anything. Any other nested
    reference that cannot be read is skipped; only its name, in the parent text, meets PROTECTED. A `$(cat file)`
    substitution runs that file's text, so the file is read as a script the command runs."""
    texts, frontier, seen = [command], [(command, 0)], set()
    for token in CAT_SUBST_RE.findall(command):
        body = _read_script(token.strip("\"'"), cwd)
        if body is None:
            return texts, token
        texts.append(body)
        frontier.append((body, 0))
    while frontier:
        text, depth = frontier.pop()
        if depth >= MAX_SCRIPT_DEPTH:
            continue
        for token in (_run_refs(text) if depth == 0 else SCRIPT_REF_RE.findall(text)):
            if token in seen:
                continue
            seen.add(token)
            body = _read_script(token, cwd)
            if body is None:
                if depth == 0 or "$" in token:
                    return texts, token
                continue
            # A comment runs nothing: scanning it matches a noun ("the Claude Code process").
            if token.lower().endswith(".ps1"):
                body = PS_BLOCK_COMMENT_RE.sub("", body)
            body = "\n".join(line for line in body.splitlines() if not FULL_LINE_COMMENT_RE.match(line))
            texts.append(body)
            frontier.append((body, depth + 1))
    return texts, None


def _flat(text):
    """Command text with quotes and backslashes dropped and whitespace collapsed: a launch command and the
    escaped copy a shell wrapper's command line carries compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"[\\\"']", "", text or "")).strip()


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
        return reap.process_table(timeout=TABLE_TIMEOUT_S)
    except Exception:
        return {}


def _live_tree(command):
    """The command lines a stop of this shell kills: every live process whose command line carries the launch
    command, and their descendants. None when the table is unreadable or no process carries the command."""
    key = _flat(command)
    table = _process_table() if key else {}
    roots = [pid for pid, row in table.items() if key in _flat(row.get("cmd"))]
    if not roots:
        return None
    import reap
    pids = set(roots)
    for root in roots:
        pids.update(reap.descendants(table, root))
    return [table[pid].get("cmd") or "" for pid in sorted(pids)]


def quiet_shell_blocker(transcript_path, tool_input, cwd=None):
    """None when the task is a background shell this session launched that runs nothing PROTECTED;
    otherwise the reason the quiet-shell exception does not apply. When the shell's live process tree can be
    read, what it runs is that tree's command lines plus the launch command; otherwise it is the launch
    command and the text of the scripts it runs."""
    task_id = (tool_input or {}).get("task_id") if isinstance(tool_input, dict) else None
    launch = _launch_of(transcript_path, task_id)
    if not launch:
        return "not a background shell or monitor this session launched"
    # _launch_of matched this session's backgroundTaskId, so a Bash/PowerShell launch is a background
    # shell whether it asked for run_in_background or timed out into the background.
    own_shell = launch[0] in ("Bash", "PowerShell")
    own_monitor = launch[0] == "Monitor" and launch[1].get("command")
    if not (own_shell or own_monitor):
        return "not a background shell or monitor this session launched"
    command = str(launch[1].get("command") or "")
    live = _live_tree(command)
    if live is not None:
        live = [cmd for cmd in live if not PROOF_SANDBOX_RE.search(cmd)]
    texts, unreadable = ([command] + live, None) if live is not None else _work_texts(command, cwd)
    local = None
    for text in texts:
        for pat, why, cls in PROTECTED:
            matched = bool(_fanout_calls(text)) if pat == PROTECTED[0][0] else re.search(pat, text, re.I)
            if matched:
                if cls == "billed":
                    return "it runs %s" % why
                local = local or why
    if unreadable:
        return "script %s cannot be read, so what it runs is unknown" % unreadable
    return None


def own_quiet_shell(transcript_path, tool_input, cwd=None):
    """True when the task is a background shell this session launched that runs nothing PROTECTED."""
    return quiet_shell_blocker(transcript_path, tool_input, cwd) is None


def taskstop_verdict(transcript_path, tool_input, cwd=None):
    """The latest owner input that says anything decides. Up to MAX_NEUTRAL_SKIP neutral inputs
    (a follow-up with no stop or keep words, or an answer to a question not about this task) are
    skipped to reach it. A question answer counts only when its question names this task.
    A background shell this session launched that runs nothing PROTECTED needs no owner input; a block
    names why that exception did not apply."""
    blocker = quiet_shell_blocker(transcript_path, tool_input, cwd)
    if blocker is None:
        return None
    note = " Quiet-shell exception not applied: %s." % blocker
    inputs = _owner_inputs(transcript_path)
    if not inputs:
        return CANNOT_VERIFY + note
    task_id = (tool_input or {}).get("task_id") if isinstance(tool_input, dict) else None
    launch = _launch_of(transcript_path, task_id)
    skipped = 0
    for kind, value in reversed(inputs):
        if kind == "text":
            # A row decides only when it actually engages THIS task; anything else is walked past,
            # so an unrelated "let it finish" cannot end the search short of the owner's real order.
            if not _engages_task(value, tool_input, launch) and skipped < MAX_NEUTRAL_SKIP:
                skipped += 1
                continue
            return None if taskstop_authorized(value, tool_input, launch) else NOT_NAMED + note
        verdict = _answer_verdict(value, tool_input)
        if verdict is None:
            if skipped < MAX_NEUTRAL_SKIP:
                skipped += 1
                continue
            return NOT_NAMED + note
        return None if verdict else NOT_NAMED + note
    return NOT_NAMED + note


def pid_forms(cmd):
    """[(pid, is_kill)]: `is_kill` marks the Git Bash `kill <pid>` form, whose number is an MSYS pid;
    `/PID <pid>` (taskkill, Stop-Process) is a Windows pid."""
    return [(m.group(1) or m.group(2), m.group(2) is not None) for m in PID_RE.finditer(cmd)
            if m.group(1) or m.group(2)]


def pids_in(cmd):
    return [pid for pid, _ in pid_forms(cmd)]


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
        r = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S)
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


NAME_KILL_PREFILTER_RE = re.compile(
    r"pkill|pgrep|killall|pidof|taskkill|stop-process|spps|wmic|win32_process|xargs|\bgps\b|get-process", re.I)
NAME_WRAPPERS = ("sudo", "env", "nohup", "xargs", "exec", "command", "time", "start", "call")
NAME_BOUNDARY = (r"(?:^|(?<=[;&|(`\"'])|" + "|".join(r"(?<=\b%s\s)" % w for w in NAME_WRAPPERS)
                 + r"|(?<=\s)(?:/c|-c|-lc|-Command|-ec)\s+|(?:^|(?<=\s))\w+=\S*\s+)\s*")
NAME_VERB_RE = re.compile(
    NAME_BOUNDARY + r"(?P<verb>pkill|pgrep|killall|pidof|taskkill|stop-process|spps|get-process|gps|wmic|xargs|kill)"
    r"(?:\.exe)?(?=[\s\"']|$)", re.I)
NAME_EXTRA_READERS = frozenset(("echo", "printf", "man", "which", "type", "help"))
PKILL_ARG_OPTS = frozenset("uUgGPstTFOdL")
PKILL_ARG_LONG = frozenset((
    "uid", "euid", "group", "pgroup", "parent", "session", "signal", "terminal", "ns", "nslist", "delimiter",
    "pidfile", "runstates", "env", "cgroup"))
PS_NAME_FLAG_RE = re.compile(r"-n(?:a(?:m(?:e)?)?)?", re.I)
WHERE_CLAUSE_RE = re.compile(
    r"(name|commandline|executablepath|caption|processid)\s*(=|like)\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s)]+))", re.I)
MAX_MATCH_LINES = 5


def _kill_args(rest):
    """Tokens of the text after a kill verb, quotes removed. They end at the first unquoted `; | & newline` or at a
    quote that never closes: the end of a `bash -c "..."` or `powershell -Command "..."` payload."""
    tokens, buf, i, has = [], [], 0, False
    while i < len(rest):
        ch = rest[i]
        if ch == "\\" and i + 1 < len(rest) and rest[i + 1] in "\"'":
            break
        if ch in "\"'":
            end = rest.find(ch, i + 1)
            if end < 0:
                break
            buf.append(rest[i + 1:end])
            has = True
            i = end + 1
            continue
        if ch in ";|&\n\r":
            break
        if ch.isspace():
            if buf or has:
                tokens.append("".join(buf))
            buf, has = [], False
        else:
            buf.append(ch)
        i += 1
    if buf or has:
        tokens.append("".join(buf))
    return tokens


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
    full = exact = icase = invert = False
    positional, i = [], 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            positional.extend(args[i:])
            break
        if tok.startswith("--"):
            flag = tok[2:].split("=")[0]
            full |= flag == "full"
            exact |= flag == "exact"
            icase |= flag == "ignore-case"
            invert |= flag == "inverse"
            if "=" not in tok and flag in PKILL_ARG_LONG:
                i += 1
        elif tok.startswith("-") and len(tok) > 1:
            if re.fullmatch(r"-(?:\d+|(?:SIG)?[A-Z]{2,7}\d*)", tok):
                continue
            letters = tok[1:]
            full |= "f" in letters
            exact |= "x" in letters
            icase |= "i" in letters
            invert |= "v" in letters
            if letters[-1] in PKILL_ARG_OPTS:
                i += 1
        else:
            positional.append(tok)
    pattern = positional[0] if positional else ""
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
    regexp, names, i = False, [], 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok.startswith("-") and len(tok) > 1 and not tok.startswith("--"):
            regexp |= "r" in tok[1:]
            if tok[-1] in "su":
                i += 1
        elif not tok.startswith("-"):
            names.append(tok)
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


def _taskkill_selectors(args):
    out, i = [], 0
    while i < len(args):
        flag = args[i].lstrip("/").lower() if args[i].startswith("/") else ""
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
        if PS_NAME_FLAG_RE.fullmatch(low):
            names += [n for n in (args[i] if i < len(args) else "").split(",") if n]
            i += 1
        elif low == "-id":
            ids += [n for n in (args[i] if i < len(args) else "").split(",") if n]
            i += 1
        elif re.fullmatch(r"-in\w*", low):
            opaque = True
            i += 1
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


def _get_process_names(piece):
    """Names a bare `Get-Process ...` pipeline head selects, or None when the piece is anything else."""
    m = NAME_VERB_RE.search(piece)
    if not m or m.group("verb").lower() not in ("get-process", "gps"):
        return None
    args, names, i = _kill_args(piece[m.end():]), [], 0
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


def _name_selectors(cmd):
    """[(label, predicate-or-None)] for every name- or pattern-addressed kill in the executing stages of `cmd`.
    None marks a form whose target set cannot be resolved."""
    readers = READ_ONLY_VERBS | NAME_EXTRA_READERS
    stages = [s for s in _shell_segments(cmd) if _segment_verb(s) not in readers
              and not (_segment_verb(s) in ("python", "python3", "py") and re.search(r"(?:^|[\s/\\\"'])reap\.py\b", s))]
    verbs = [m.group("verb").lower() for s in stages for m in NAME_VERB_RE.finditer(s)]
    kills = any(v in ("kill", "taskkill", "stop-process", "spps") for v in verbs) or any(
        re.search(r"\bxargs\b[^|;&]*\b(?:kill|taskkill)\b", s, re.I) for s in stages)
    out = []
    for n, stage in enumerate(stages):
        piped = n > 0 and stages[n - 1].rstrip().endswith("|")
        hits = list(NAME_VERB_RE.finditer(stage))
        for k, m in enumerate(hits):
            verb = m.group("verb").lower()
            rest = stage[m.end():hits[k + 1].start() if k + 1 < len(hits) else len(stage)]
            args = _kill_args(rest)
            head = stage[:m.start()]
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
            elif verb in ("stop-process", "spps") or (verb == "kill" and not any(a.isdigit() for a in args)
                                                      and (piped or re.search(r"\|\s*$", head))):
                if re.search(r"\|\s*$", head):
                    pieces = [p for p in head.rstrip().rstrip("|").split("|") if p.strip()]
                    producer = _get_process_names(pieces[0]) if len(pieces) == 1 else None
                elif piped:
                    chain, j = [], n - 1
                    while j >= 0 and stages[j].rstrip().endswith("|"):
                        chain.insert(0, stages[j].rstrip()[:-1])
                        j -= 1
                    producer = _get_process_names(chain[0]) if len(chain) == 1 else None
                else:
                    producer = None
                out.extend(_stop_process_selectors(args, producer))
            elif verb == "wmic":
                w = re.search(r"\bprocess\b\s+where\b(?P<cond>.*?)\b(?:delete|call\s+terminate)\b", rest, re.I | re.S)
                if w:
                    out.append(_where_selector("wmic process where%s" % w.group("cond").rstrip(), w.group("cond")))
                elif re.search(r"\bprocess\b.*\b(?:delete|call\s+terminate)\b", rest, re.I | re.S):
                    out.append(("wmic process with no condition", None))
            elif verb == "xargs":
                if any(a.lower() in ("kill", "taskkill") for a in args) and not any(
                        v in ("pkill", "pgrep") for v in verbs):
                    out.append(("xargs kill fed by a pipeline", None))
    joined = " ".join(stages)
    if re.search(r"win32_process", joined, re.I) and re.search(
            r"\bTerminate\b|Remove-(?:Cim|Wmi)(?:Instance|Object)", joined, re.I):
        f = re.search(r"-(?:Filter|Query)\s+(?:\"([^\"]*)\"|'([^']*)')", joined, re.I)
        if f and not re.search(r"where-object|\|\s*\?|\|\s*where\b", joined, re.I):
            out.append(_where_selector("Win32_Process Terminate", f.group(1) or f.group(2)))
        else:
            out.append(("Win32_Process Terminate with no readable filter", None))
    return out


def _protected_reason(row):
    line = row.get("cmd") or row.get("name") or ""
    for pat, why, _cls in PROTECTED:
        if re.search(pat, line, re.I):
            return why
    return None


def _name_kill_problems(cmd):
    if not NAME_KILL_PREFILTER_RE.search(cmd):
        return []
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
    if "taskkill" not in cmd and not re.search(r"(^|[\s;&|])kill\s", cmd):
        return []
    allowed = {p.strip() for p in (env.get("HARNESS_ALLOW_KILL") or "").replace(",", " ").split() if p}
    problems = []
    deadline = _now() + HOOK_BUDGET_S
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
        for pat, why, _cls in PROTECTED:
            if re.search(pat, line, re.I):
                problems.append("  PID %s is %s\n      %s" % (pid, why, line[:160]))
                break
    return problems


def verdict(cmd, env):
    pid_problems = _pid_problems(cmd, env)
    name_problems = _name_kill_problems(cmd)
    if not pid_problems and not name_problems:
        return None
    return ("BLOCKED kill (CLAUDE.core.md §Shell Discipline) — the target is not what a truncated shell list shows.\n"
            + "\n".join(pid_problems + name_problems)
            + "\n\nStop it through the reaper (`python3 .claude/tools/reap.py --help`): ownership proof, a verified reason, a ledger row.\n"
              "A Bash-tool background task is NOT an OS process to taskkill — stop it with "
              "KillShell/TaskStop by its task id.\n"
            + ("A kill by name or pattern stops every process that matches; `HARNESS_ALLOW_KILL` covers a PID only."
               if name_problems else
               "Deliberate raw kill: prefix `HARNESS_ALLOW_KILL=<pid>` (naming the pid a second time)."))


# Closing terminal windows by handle, title or host process. Every Windows Terminal window belongs to
# one WindowsTerminal.exe, and a title follows the foreground command, so a `python.exe` title is a
# live session as often as a dead launch-error window.
WINDOW_CLOSE_RE = re.compile(
    r"CloseMainWindow|\bWM_CLOSE\b|\bSC_CLOSE\b|0xF060"
    r"|\b(?:Post|Send)Message\w*\s*\([^)]*0x(?:0*10|112)\b"
    r"|taskkill[^\n;|]*?/IM\s+['\"]?(?:WindowsTerminal|wt|OpenConsole|conhost)\b"
    r"|Stop-Process[^\n;|]*?-Name\s+['\"]?(?:WindowsTerminal|wt|OpenConsole|conhost)\b"
    r"|(?:WindowsTerminal|OpenConsole|conhost)\b[^\n]*\|\s*Stop-Process", re.I)


# Reader arguments are data; _shell_segments visits their executing substitutions separately.
READ_ONLY_VERBS = frozenset((
    "grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "less", "more", "wc", "ls", "stat", "file", "diff",
    "select-string", "get-content", "gc"))


def _segment_verb(segment):
    words = [w for w in segment.split() if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
    return words[0].strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") if words else ""


def window_close_verdict(cmd, cwd=None):
    """Deny terminal-window closes in executing stages and their scratch scripts, or None.

    Quoted reader arguments are data; substitutions and every non-reader stage are inspected.
    Proof and hook bodies outside `.claude/scratch/` are not scanned.
    """
    stages = [segment for segment in _shell_segments(cmd)
              if _segment_verb(segment) not in READ_ONLY_VERBS]
    texts = ["\n".join(stages)]
    for segment in stages:
        for token in _run_refs(segment):
            scratch = "/.claude/scratch/" in "/" + token.replace("\\", "/").lstrip("/")
            body = _read_script(token, cwd) if scratch else None
            if body:
                texts.append(body)
    if not any(WINDOW_CLOSE_RE.search(t) for t in texts):
        return None
    return ("BLOCKED window close (CLAUDE.core.md §Shell Discipline) — terminal windows share one WindowsTerminal.exe, "
            "and a window's title follows its foreground command, so no title or process name tells a dead error "
            "window from a live session, possibly this one.\n"
            "Ask the owner to close them.")


def _emit_deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        _emit_deny("BLOCKED stop/kill (CLAUDE.core.md §Shell Discipline) — the hook could not read its input or transcript. "
                   "Re-issue the same call; if it repeats, report it.")
        return 0
    tool = payload.get("tool_name")
    if tool == "TaskStop":
        why = taskstop_verdict(payload.get("transcript_path"), payload.get("tool_input"), payload.get("cwd"))
    elif tool in ("Bash", "PowerShell"):
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        inline = dict(os.environ)
        for m in re.finditer(r"HARNESS_ALLOW_KILL=(\S+)", cmd):
            inline["HARNESS_ALLOW_KILL"] = inline.get("HARNESS_ALLOW_KILL", "") + " " + m.group(1)
        why = window_close_verdict(cmd, payload.get("cwd")) or verdict(cmd, inline)
    else:
        return 0
    if why:
        _emit_deny(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
