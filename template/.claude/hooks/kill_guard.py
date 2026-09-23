#!/usr/bin/env python3
"""Guard irreversible stops.

Bash/PowerShell kills resolve each PID before acting and refuse harness-critical processes. TaskStop
requires a stop request in the owner's latest exact message whose object is this task by name, a
generic reference ("stop it", "stop that agent") or nothing (a bare "stop"); a phrase naming a
different label-shaped task, or a later keep-running instruction, blocks. A mechanism correction
applies to future dispatches and never justifies discarding work already spent.

One exception needs no owner wording (owner decision 2026-09-15): a background Bash/PowerShell shell
or a Monitor THIS session launched (not a subagent's) whose command, and the readable text of the
local scripts it runs (MAX_SCRIPT_DEPTH levels), matches nothing in PROTECTED. A Monitor is a poll
loop by construction; a superseded one keeps re-emitting stale lines until it is stopped. A script it cannot read, at the top level
or built from a variable, fails the exception closed. That is a waiter, monitor or poll loop; stopping it discards no paid
work, and demanding consent for it leaves superseded jobs running.

WHY THIS EXISTS: `shell_census.py` lists long-running shells with their command tails TRUNCATED, and
its own text hands over `taskkill /PID <pid> /T /F`. Measured 2026-09-08: a session read that list,
meant to kill a stray home-directory `grep`, matched the wrong row, and killed its own
`sidecar_fanout.py` run mid-flight -- destroying a paid comparison arm. A later session stopped a
healthy Agent after correcting its dispatch mechanism, then proposed paying for the same work again.

A stop is irreversible and the display is lossy, so this gate fails CLOSED (instruction_quality
section 16). Bash/PowerShell bypass, per target: `HARNESS_ALLOW_KILL=<pid> taskkill /PID <pid> /T /F`.
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
from _owner_text import classify  # noqa: E402

# Killing any of these takes down work that cannot be resumed from where it stopped: a billed
# sidecar arm, a gate/suite run, or a peer's whole session.
PROTECTED = (
    (r"sidecar_fanout\.py", "a sidecar fan-out — its in-flight arms are billed and unresumable"),
    (r"_sidecar\.sh|sidecar_launch\.py", "a sidecar launcher child — the arm dies with it"),
    (r"harness_tests\.py", "the harness proof runner"),
    (r"regression_gate|gate_chain|verify\.ps1", "a gate/verification run"),
    (r"vstest|dotnet\s+test", "a test run"),
    # NOT \bclaude\b — that matches `.claude/` (present in nearly every harness command) and
    # `claude-gpt`. Match the EXECUTABLE: `claude.exe`, or `claude` standing as the command word.
    (r"(?:^|[\s/\\\"])claude\.exe|(?:^|[\s/\\\"])claude(?:\s|$)",
     "a Claude Code session — possibly a peer's"),
    (r"claude-code-proxy", "a transport proxy other sessions may be routing through"),
    (r"run_grid(?:\.snapshot[\w.-]*)?\.sh", "a benchmark grid — its in-flight cell is a billed arm"),
)

# Local scripts a shell command runs. Their TEXT is scanned too: a background `bash waiter.sh` that
# calls a sidecar holds a billed arm exactly as a direct launch does.
# Glob characters are excluded: `find -name "*.py"` names a pattern, not a script to read.
SCRIPT_REF_RE = re.compile(r"[^\s\"'|;&<>()=*?\[\]]+\.(?:sh|py|ps1|js|cmd|bat)(?![\w.])", re.I)
MAX_SCRIPT_BYTES = 512 * 1024
MAX_SCRIPT_DEPTH = 2

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
    r"|(?:^|[;,]|\band\b|\bbut\b)\s*(?:also|then|now|please|just)\s+" + STOP_VERB + r"\b",
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
    r"(?:(?:running|existing|background|current)\s+)?(?:agent|task|job|delegate|subagent|workflow|run|lane)s?)\b",
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


CANNOT_VERIFY = ("BLOCKED TaskStop — cannot verify an explicit owner instruction to stop this task. "
                 "Let it finish, or ask the owner and re-issue TaskStop after their answer.")
NOT_NAMED = ("BLOCKED TaskStop — the owner's latest message or question answer does not name this task, "
             "refer to it generically (\"stop it\", \"stop that agent\") or ask for a bare stop. Let it "
             "finish and consume the result. A request naming another task, or a later keep-running "
             "instruction, grants no consent.")


def _launch_of(transcript_path, task_id):
    """(tool name, tool input) of the main-session call that launched background task `task_id`, else None.

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
        result = row.get("toolUseResult") or {}
        if row.get("isSidechain") or task_id not in (result.get("backgroundTaskId"), result.get("taskId")):
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
                return item.get("name"), item.get("input") or {}
    return None


def _read_script(token, cwd):
    """A referenced script's text, or None when it cannot be located and read."""
    path = token.strip("\"'")
    drive = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if drive and os.name == "nt":
        path = "%s:/%s" % (drive.group(1), drive.group(2))
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    try:
        if os.path.getsize(path) > MAX_SCRIPT_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _work_texts(command, cwd):
    """(texts, unreadable): the command plus the local scripts it runs, MAX_SCRIPT_DEPTH deep.
    `unreadable` names the first script that blocks the exception: one the command itself runs, or a
    nested one built from a variable (`$HERE/x.sh`), whose body could launch anything. Any other nested
    reference that cannot be read is skipped; only its name, in the parent text, meets PROTECTED."""
    texts, frontier, seen = [command], [(command, 0)], set()
    while frontier:
        text, depth = frontier.pop()
        if depth >= MAX_SCRIPT_DEPTH:
            continue
        for token in SCRIPT_REF_RE.findall(text):
            if token in seen:
                continue
            seen.add(token)
            body = _read_script(token, cwd)
            if body is None:
                if depth == 0 or "$" in token:
                    return texts, token
                continue
            texts.append(body)
            frontier.append((body, depth + 1))
    return texts, None


def quiet_shell_blocker(transcript_path, tool_input, cwd=None):
    """None when the task is a background shell this session launched that runs nothing PROTECTED;
    otherwise the reason the quiet-shell exception does not apply."""
    task_id = (tool_input or {}).get("task_id") if isinstance(tool_input, dict) else None
    launch = _launch_of(transcript_path, task_id)
    if not launch:
        return "not a background shell or monitor this session launched"
    own_shell = launch[0] in ("Bash", "PowerShell") and launch[1].get("run_in_background")
    own_monitor = launch[0] == "Monitor" and launch[1].get("command")
    if not (own_shell or own_monitor):
        return "not a background shell or monitor this session launched"
    texts, unreadable = _work_texts(str(launch[1].get("command") or ""), cwd)
    for text in texts:
        for pat, why in PROTECTED:
            if re.search(pat, text, re.I):
                return "it runs %s" % why
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


def verdict(cmd, env):
    if "taskkill" not in cmd and not re.search(r"(^|[\s;&|])kill\s", cmd):
        return None
    targets = pid_forms(cmd)
    if not targets:
        return None

    allowed = {p.strip() for p in (env.get("HARNESS_ALLOW_KILL") or "").replace(",", " ").split() if p}
    problems = []
    deadline = _now() + HOOK_BUDGET_S
    for pid, is_kill in targets:
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
    if not problems:
        return None
    return ("BLOCKED kill — the target is not what a truncated shell list shows.\n"
            + "\n".join(problems)
            + "\n\nStop it through the reaper (`python3 .claude/tools/reap.py --help`): ownership proof, a verified reason, a ledger row.\n"
              "A Bash-tool background task is NOT an OS process to taskkill — stop it with "
              "KillShell/TaskStop by its task id.\n"
              "Deliberate raw kill: prefix `HARNESS_ALLOW_KILL=<pid>` (naming the pid a second time).")


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
        _emit_deny("BLOCKED stop/kill — cannot verify the target because the hook payload is not an object.")
        return 0
    tool = payload.get("tool_name")
    if tool == "TaskStop":
        why = taskstop_verdict(payload.get("transcript_path"), payload.get("tool_input"), payload.get("cwd"))
    elif tool in ("Bash", "PowerShell"):
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        inline = dict(os.environ)
        for m in re.finditer(r"HARNESS_ALLOW_KILL=(\S+)", cmd):
            inline["HARNESS_ALLOW_KILL"] = inline.get("HARNESS_ALLOW_KILL", "") + " " + m.group(1)
        why = verdict(cmd, inline)
    else:
        return 0
    if why:
        _emit_deny(why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
