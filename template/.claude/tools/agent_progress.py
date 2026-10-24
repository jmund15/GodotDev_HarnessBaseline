#!/usr/bin/env python3
"""Measures of one running agent's course, read from its stream.

Inputs: a Claude stream-json stream (`<record>.stream` of a sidecar, which the codex and deepseek launchers
also write) or a Workflow seat's `agent-<id>.jsonl` (both are `assistant`/`user` rows with a `timestamp`), and an
opencode `--format json` stream (`tool_use`, `text`, `step_finish` events). Each row folds into one `Progress`;
`read` parses only the bytes appended since the last call and caches per (size, mtime), as
`watch_records.agent_compaction` does. The reader prints measures and counts, never a verdict:
what they mean for the job is the orchestrator's judgment (`watch_records.CHECKIN_RULE`).

`python3 .claude/tools/watch_records.py --digest <record|stream|transcript>` prints `digest(...)`.
"""
import calendar
import collections
import json
import math
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS_PATH = os.path.join(os.path.dirname(HERE), "orchestration_metrics.jsonl")

WRITE_TOOLS = {"edit", "write", "multiedit", "notebookedit", "patch", "apply_patch"}
READ_TOOLS = {"read", "view"}
# A p90 over fewer runs is the maximum itself: nearest-rank p90 first differs from the maximum at ten.
MIN_P90_SAMPLE = 10
SERIES_BUCKETS = 6  # the clause shows at most this many windows, newest last
TOP_FILES = 3
CHECKLIST_FILES = 5  # the newest markdown files the job wrote that a checklist is looked for in
NOTE_KEEP = 40
SAID_KEEP = 400
GIT_TIMEOUT = 10
LOG_KEEP = 500  # the newest commits of a worktree's log a job's commits are read from

# (pattern, group holding the failed count). A summary line a test runner or the project's gate prints.
TEST_LINES = (
    (re.compile(r"(?:Passed|Failed)!\s+-\s+Failed:\s*(\d+),\s*Passed:\s*(\d+)[^\n]*"), 1),
    (re.compile(r"SUITE \S+ passed=(\d+) failed=(\d+)[^\n]*"), 2),
    (re.compile(r"\b(\d+) passed, (\d+) failed\b"), 2),
    (re.compile(r"\b(\d+) failed, (\d+) passed\b"), 1),
)
BUILD_FAILED = re.compile(r"Build FAILED|error (?:CS|MSB)\d+")
BUILD_OK = re.compile(r"Build succeeded")
COMMIT_COMMAND = re.compile(r"\bgit\b[^|;&\n]*\bcommit\b")
COMMIT_LINE = re.compile(r"^\[[^\]\s]+(?: \(root-commit\))? ([0-9a-f]{7,40})\] ", re.M)
CHECKLIST_ITEM = re.compile(r"^\s*[-*]\s*\[( |x|X)\]", re.M)


def _stamp(value):
    """Epoch seconds from an ISO `...Z` string or an epoch in milliseconds; None otherwise."""
    if isinstance(value, str) and len(value) >= 19:
        try:
            whole = calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return None
        fraction = re.match(r"\.(\d+)", value[19:])
        return whole + (float("0." + fraction.group(1)) if fraction else 0.0)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1e11:
        return value / 1000.0
    return None


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _norm(path):
    return str(path).replace("\\", "/")


class Progress:
    def __init__(self):
        self.offset = 0
        self.rows = 0
        self.tools = None  # the init row's tool list, when the stream has one
        self.first = self.last = None
        self.calls = []  # (epoch, tool name)
        self.contexts = []  # (epoch, prompt tokens of the main thread's request)
        self.edits = collections.defaultdict(list)  # path -> epochs
        self.reads = collections.Counter()  # path -> Read calls
        self.tests = []  # (epoch, failing, line)
        self.builds = []  # (epoch, failing)
        self.commits = []  # (epoch, short sha) printed by a commit command
        self.cwd = None  # the init row's working directory, when the stream has one
        self.said = []  # (epoch, text)
        self.notes = collections.deque(maxlen=NOTE_KEEP)  # (epoch, a tool call's own description)
        self.written = {}  # markdown path -> epoch of the job's latest write to it
        self._made, self._made_key = [], None
        self._pending = {}  # tool_use id -> (tool name, command)

    @property
    def readable(self):
        return self.rows > 0

    def made_commits(self):
        """The job's commits: its linked worktree's git log since its first row, else the printed `[branch sha]` lines."""
        key = (self.rows, self.cwd, self.first)
        if self._made_key != key:
            own = _worktree_commits(self.cwd, self.first)
            self._made = self.commits if own is None else own
            self._made_key = key
        return self._made

    @property
    def writer(self):
        """True when the job holds or used an edit tool, False when its tool list has none, None when unknown."""
        if self.tools is not None and any(t.lower() in WRITE_TOOLS for t in self.tools if isinstance(t, str)):
            return True
        if self.edits:
            return True
        return False if self.tools is not None else None

    def fold(self, row):
        kind = row.get("type")
        if not isinstance(kind, str):
            return
        self.rows += 1
        ts = _stamp(row.get("timestamp"))
        if ts is None:
            ts = self.last
        if ts is not None:
            self.first = ts if self.first is None else self.first
            self.last = ts if self.last is None or ts > self.last else self.last
        if kind == "system":
            tools = row.get("tools")
            if row.get("subtype") == "init" and isinstance(tools, list):
                self.tools = tools
                self.cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
        elif kind == "assistant":
            self._assistant(row, ts)
        elif kind == "user":
            self._user(row, ts)
        elif kind == "tool_use" and isinstance(row.get("part"), dict):
            part = row["part"]
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            self._call(ts, str(part.get("tool") or "?"), inp, None)
            self._result(ts, str(part.get("tool") or ""), inp.get("command"), _text(state.get("output")))
        elif kind == "text" and isinstance(row.get("part"), dict):
            self._say(ts, row["part"].get("text"))
        elif kind == "step_finish" and isinstance(row.get("part"), dict):
            tokens = row["part"].get("tokens")
            cache = tokens.get("cache") if isinstance(tokens, dict) and isinstance(tokens.get("cache"), dict) else {}
            if isinstance(tokens, dict):
                total = sum(v for v in (tokens.get("input"), cache.get("read"), cache.get("write")) if isinstance(v, (int, float)))
                if total > 0 and ts is not None:
                    self.contexts.append((ts, int(total)))

    def _assistant(self, row, ts):
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        main = row.get("parent_tool_use_id") is None
        usage = message.get("usage")
        if main and isinstance(usage, dict) and ts is not None:
            total = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                        if isinstance(usage.get(k), (int, float)))
            if total > 0:
                self.contexts.append((ts, int(total)))
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and main:
                self._say(ts, block.get("text"))
            elif block.get("type") == "tool_use":
                self._call(ts, str(block.get("name") or "?"), block.get("input") if isinstance(block.get("input"), dict) else {},
                           block.get("id"))

    def _user(self, row, ts):
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                name, command = self._pending.pop(block.get("tool_use_id"), (None, None))
                self._result(ts, name or "", command, _text(block.get("content")))

    def _say(self, ts, text):
        if isinstance(text, str) and text.strip() and ts is not None:
            self.said.append((ts, text.strip()))
            del self.said[:-SAID_KEEP]

    def _call(self, ts, name, inp, call_id):
        if ts is None:
            return
        self.calls.append((ts, name))
        low = name.lower()
        path = inp.get("file_path") or inp.get("filePath") or inp.get("path")
        if low in WRITE_TOOLS and path:
            self.edits[_norm(path)].append(ts)
            if str(path).lower().endswith(".md"):
                self.written[_norm(path)] = ts
        elif low in READ_TOOLS and path:
            self.reads[_norm(path)] += 1
        if isinstance(inp.get("description"), str) and inp["description"].strip():
            self.notes.append((ts, inp["description"].strip()))
        if call_id:
            self._pending[call_id] = (name, inp.get("command") if isinstance(inp.get("command"), str) else None)

    def _result(self, ts, name, command, text):
        if ts is None or not text:
            return
        if "ailed" in text or "passed" in text or "SUITE " in text:
            for pattern, failed_group in TEST_LINES:
                for found in pattern.finditer(text):
                    self.tests.append((ts, int(found.group(failed_group)) > 0, found.group(0)[:160]))
        if "Build" in text or "error CS" in text or "error MSB" in text:
            if BUILD_FAILED.search(text):
                self.builds.append((ts, True))
            elif BUILD_OK.search(text):
                self.builds.append((ts, False))
        if name.lower() in ("bash", "shell") and command and COMMIT_COMMAND.search(command):
            for found in COMMIT_LINE.finditer(text):
                self.commits.append((ts, found.group(1)[:9]))


def _worktree_commits(cwd, since):
    """(epoch, short sha) of the commits at or after `since` in the git log of the linked worktree at `cwd`; None when
    `cwd` is not a linked worktree or git cannot answer."""
    if not cwd or since is None or not os.path.isdir(cwd):
        return None
    try:
        dirs = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
        log = subprocess.run(["git", "-C", cwd, "log", "--format=%ct %h", "-n", str(LOG_KEEP)],
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    git_dir, common = (dirs.stdout.splitlines() + ["", ""])[:2]
    if dirs.returncode != 0 or log.returncode != 0 or not git_dir or git_dir == common:
        return None
    commits = []
    for line in log.stdout.splitlines():
        stamp, _, sha = line.partition(" ")
        if stamp.isdigit() and int(stamp) >= since:
            commits.append((float(stamp), sha[:9]))
    return commits


_CACHE = {}


def read(path):
    """The `Progress` of the stream at `path`; None when the file is absent or empty. Parses only the bytes appended
    since the last call (cached per path); a file that shrank is read from its start. A last line without its
    newline waits for it unless it already parses. Rows of an unsupported shape leave `readable` False."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat.st_size == 0:
        return None
    stamp = (stat.st_size, stat.st_mtime_ns)
    cached = _CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    state = cached[1] if cached and stat.st_size >= cached[1].offset else Progress()
    try:
        with open(path, "rb") as fh:
            fh.seek(state.offset)
            data = fh.read()
    except OSError:
        return state if cached else None
    end = data.rfind(b"\n") + 1
    tail = data[end:]
    if tail.strip():
        try:
            json.loads(tail)
            end = len(data)
        except ValueError:
            pass
    for raw in data[:end].splitlines():
        # The thinking_tokens heartbeat rows are nearly every row of a long run and carry no progress.
        if not raw.strip() or b'"thinking_tokens"' in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            state.fold(row)
    state.offset += end
    _CACHE[path] = (stamp, state)
    return state


def _shape_of_builtins(builtins):
    return "writer" if any(isinstance(b, str) and b.lower() in WRITE_TOOLS for b in builtins or []) else "read-only"


_SHAPE_OF_RECORD = {}
_HISTORY = {}


def history(shape, metrics_path=None):
    """(n, p90 minutes or None): the completed sidecar runs of `shape` in the metrics file, their duration (`secs`),
    shape read from the run's record (`toolProfile.builtins`). A run whose record is gone is not in the sample.
    p90 is the nearest-rank 90th percentile, and None below `MIN_P90_SAMPLE` runs."""
    metrics_path = metrics_path or METRICS_PATH
    try:
        stat = os.stat(metrics_path)
    except OSError:
        return 0, None
    key = (metrics_path, stat.st_size, stat.st_mtime_ns)
    if key not in _HISTORY:
        durations = collections.defaultdict(list)
        with open(metrics_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("workflow") != "sidecar" or row.get("state") != "completed":
                    continue
                secs, record = row.get("secs"), row.get("record_path")
                if not isinstance(secs, (int, float)) or secs <= 0 or not isinstance(record, str):
                    continue
                if record not in _SHAPE_OF_RECORD:
                    try:
                        with open(record, encoding="utf-8") as rec:
                            profile = (json.load(rec) or {}).get("toolProfile")
                        builtins = profile.get("builtins") if isinstance(profile, dict) else None
                        _SHAPE_OF_RECORD[record] = _shape_of_builtins(builtins) if isinstance(builtins, list) else None
                    except (OSError, ValueError, AttributeError):
                        _SHAPE_OF_RECORD[record] = None
                if _SHAPE_OF_RECORD[record]:
                    durations[_SHAPE_OF_RECORD[record]].append(secs / 60.0)
        _HISTORY[key] = durations
    values = sorted(_HISTORY[key].get(shape, []))
    if len(values) < MIN_P90_SAMPLE:
        return len(values), None
    return len(values), values[math.ceil(0.9 * len(values)) - 1]


def _window_label(window):
    return "%d min" % (window // 60) if window >= 60 else "%g s" % window


def _series(stamps, now, window, buckets):
    counts = [0] * buckets
    for ts in stamps:
        index = max(int((now - ts) // window), 0)
        if index < buckets:
            counts[buckets - 1 - index] += 1
    return counts


def _context_series(contexts, now, window, buckets):
    out = []
    for j in range(buckets):
        end = now - (buckets - 1 - j) * window
        value = None
        for ts, tokens in contexts:
            if ts <= end:
                value = tokens
        out.append(value)
    return out


def _kilo(tokens):
    return "-" if tokens is None else "%dK" % round(tokens / 1000.0)


def _quote(text, width=100):
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    first = first.replace('"', "'")
    return first if len(first) <= width else first[:width - 1].rstrip() + "…"


def checklist(progress):
    """(done, total, file name) of the newest markdown file the job wrote that holds at least two checklist items."""
    for path in sorted(progress.written, key=progress.written.get, reverse=True)[:CHECKLIST_FILES]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                items = CHECKLIST_ITEM.findall(fh.read(1 << 20))
        except OSError:
            continue
        if len(items) >= 2:
            return sum(1 for i in items if i != " "), len(items), os.path.basename(path)
    return None


def clause(progress, *, now, window, age_min=None, shape=None, history=None, writes=None):
    """One line of facts, each present only when the stream has it. `history` is `history(shape)`'s pair.
    `writes` is the dispatch's own writer flag, used when the stream's tool list does not say."""
    if progress is None:
        return None
    if not progress.readable:
        return "progress unreadable"
    first = progress.first if progress.first is not None else now
    buckets = max(1, min(SERIES_BUCKETS, math.ceil(max(now - first, 0) / window)))
    label = _window_label(window)
    parts = []
    calls = _series([ts for ts, _ in progress.calls], now, window, buckets)
    text = "tool calls %d" % len(progress.calls)
    if buckets > 1:
        text += " (per %s, oldest first: %s)" % (label, " ".join(map(str, calls)))
    parts.append(text)
    if progress.contexts:
        text = "context %s" % _kilo(progress.contexts[-1][1])
        if buckets > 1:
            text += " (per %s: %s)" % (label, " ".join(_kilo(c) for c in _context_series(progress.contexts, now, window, buckets)))
        parts.append(text)
    if progress.edits:
        ranked = sorted(progress.edits.items(), key=lambda kv: (-len(kv[1]), -kv[1][-1]))
        total = sum(len(v) for v in progress.edits.values())
        parts.append("edits %d in %d files: %s" % (total, len(ranked), ", ".join(
            "%s %d" % (os.path.basename(p), len(v)) for p, v in ranked[:TOP_FILES])))
    elif progress.reads:
        parts.append("reads %d (%d files)" % (sum(progress.reads.values()), len(progress.reads)))
    if progress.tests:
        text = "test result lines %d (%d failing" % (len(progress.tests), sum(1 for t in progress.tests if t[1]))
        if buckets > 1:
            recent = [t for t in progress.tests if now - t[0] <= window]
            text += "; last %s %d, %d failing" % (label, len(recent), sum(1 for t in recent if t[1]))
        parts.append(text + ")")
    if progress.builds:
        parts.append("build results %d (%d failing)" % (len(progress.builds), sum(1 for b in progress.builds if b[1])))
    writer = progress.writer if progress.writer is not None else writes
    if writer:
        parts.append("commits %d" % len(progress.made_commits()))
    items = checklist(progress)
    if items:
        parts.append("items %d of %d in %s" % items)
    stated = [(ts, text) for ts, text in progress.said[-1:]] + list(progress.notes)[-1:]
    if stated:
        parts.append('said "%s"' % _quote(max(stated, key=lambda s: s[0])[1]))
    if history is not None and shape and age_min is not None:
        count, p90 = history
        if p90 is None:
            parts.append("duration: no basis (n=%d %s jobs)" % (count, shape))
        elif age_min > p90:
            parts.append("duration: past p90 of n=%d %s jobs (%d min)" % (count, shape, round(p90)))
    return "progress: " + "; ".join(parts)


def _hour_rows(progress, hours):
    """Per hour since the first row: calls, edits, test lines, failing test lines, commits, context at the hour's end."""
    start = progress.first
    commits = progress.made_commits()
    rows = []
    for h in range(hours):
        low, high = start + h * 3600, start + (h + 1) * 3600
        inside = lambda ts: low <= ts < high or (h == hours - 1 and ts == high)  # noqa: E731
        context = None
        for ts, tokens in progress.contexts:
            if ts < high:
                context = tokens
        rows.append({
            "start": low,
            "calls": sum(1 for ts, _ in progress.calls if inside(ts)),
            "edits": sum(1 for stamps in progress.edits.values() for ts in stamps if inside(ts)),
            "tests": sum(1 for t in progress.tests if inside(t[0])),
            "failing": sum(1 for t in progress.tests if inside(t[0]) and t[1]),
            "commits": sum(1 for c in commits if inside(c[0])),
            "context": context,
        })
    return rows


def _clock(ts):
    return time.strftime("%H:%M", time.gmtime(ts))


def digest(progress, label=None, max_hours=24, max_lines=30):
    """The whole-run view `clause` summarizes: per-hour calls, edits, test lines, commits and context; edits per file
    by hour; each test result line, commit, stated intent. Lists longer than `max_lines` keep their newest."""
    if progress is None or not progress.readable:
        return "progress unreadable"
    first, last = progress.first, progress.last
    if first is None or last is None:
        return "digest %s: %d rows, no timestamps" % (label or "", progress.rows)
    hours = max(1, math.ceil((last - first) / 3600.0))
    shown = list(range(max(0, hours - max_hours), hours))
    rows = _hour_rows(progress, hours)
    span = int(last - first)
    out = ["digest %s: %dh%02dm from %s UTC, %d rows, %d tool calls, %s" % (
        label or "stream", span // 3600, span % 3600 // 60, _clock(first), progress.rows, len(progress.calls),
        {True: "writer", False: "read-only", None: "writer unknown"}[progress.writer])]
    out.append("per hour from the first row (hour start UTC; calls, edits, test result lines (failing), commits, context at hour end)%s:"
               % ("" if len(shown) == hours else ", newest %d of %d" % (len(shown), hours)))
    for h in shown:
        r = rows[h]
        out.append("  h%d %s  calls %d  edits %d  tests %d (%d)  commits %d  context %s" % (
            h, _clock(r["start"]), r["calls"], r["edits"], r["tests"], r["failing"], r["commits"], _kilo(r["context"])))
    if progress.edits:
        out.append("edits per file by hour (columns h%d..h%d):" % (shown[0], shown[-1]))
        ranked = sorted(progress.edits.items(), key=lambda kv: (-len(kv[1]), -kv[1][-1]))
        for path, stamps in ranked[:12]:
            columns = [sum(1 for ts in stamps if first + h * 3600 <= ts < first + (h + 1) * 3600
                           or (h == hours - 1 and ts == first + hours * 3600)) for h in shown]
            out.append("  %s  %d | %s" % (os.path.basename(path), len(stamps), " ".join(map(str, columns))))
        if len(ranked) > 12:
            out.append("  (%d more files, each edited %d times or fewer)" % (len(ranked) - 12, len(ranked[12][1])))

    def listing(title, items, fmt):
        if not items:
            return
        shown_lines = []
        for item in items:
            text = fmt(item)
            if shown_lines and shown_lines[-1][0] == text:
                shown_lines[-1][1] += 1
            else:
                shown_lines.append([text, 1])
        out.append("%s (%d%s):" % (title, len(items), "" if len(shown_lines) <= max_lines else ", newest %d lines" % max_lines))
        out.extend("  %s%s" % (text, " x%d" % n if n > 1 else "") for text, n in shown_lines[-max_lines:])

    listing("test result lines", progress.tests, lambda t: "%s %s" % (_clock(t[0]), t[2]))
    listing("builds", progress.builds, lambda b: "%s %s" % (_clock(b[0]), "failing" if b[1] else "ok"))
    listing("commits", progress.made_commits(), lambda c: "%s %s" % (_clock(c[0]), c[1]))
    listing("said", progress.said, lambda s: '%s "%s"' % (_clock(s[0]), _quote(s[1], 160)))
    listing("tool call descriptions", list(progress.notes), lambda s: '%s "%s"' % (_clock(s[0]), _quote(s[1], 160)))
    items = checklist(progress)
    if items:
        out.append("items %d of %d in %s" % items)
    return "\n".join(out)
