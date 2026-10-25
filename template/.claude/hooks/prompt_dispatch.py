#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit dispatcher. One process runs the CHAIN members concurrently.

Replaces one hook command per member. `runaway_scan_reaper` keeps its own registration and is not
a member. Each member stays a standalone script with its own main().

Sub-hook contract (what a CHAIN member may assume and must guarantee):
  - The module is imported inside its own thread, so an import fault is that member's alone.
  - `sys.stdin` is a per-thread StringIO of the payload; `sys.stdout` / `sys.stderr` are per-thread
    captures whose `reconfigure` is a no-op. A thread-local router installed once on the real
    streams sends each call to the calling thread's capture; any other thread reaches the real
    stream. Encoding is applied once, when this file writes the real stdout.
  - `sys.argv` is [this file].
  - A member never writes to fd 1, `sys.__stdout__`, or re-reads stdin after main() returns, and
    never rebinds `sys.stdout`/`sys.stderr` (`redirect_stdout` included): that replaces the router
    for every member. `sys.stdout.buffer` and `.fileno()` do not exist on a capture. The proof scans
    each member and the local modules it imports for these.
  - A main() that returns a non-zero int is `exit<n>`.
  - SystemExit(0|None) is `ok`; another exit code is `exit<n>`; any other exception, BaseException
    included, is `fault`; a member still running at its budget is abandoned as `timeout`, and one
    that finishes after its budget is `timeout` too; a timed-out member's output is discarded.

Output: each member's stdout is normalized (empty and `{}` dropped; a JSON object yields its
hookSpecificOutput.additionalContext, dropped with a stderr note when absent; anything else
verbatim), joined in CHAIN
order with a newline, and printed; `{}` when nothing remains (Claude Code #10463). Faults,
timeouts and exits are named on stderr and in the timing record; none blocks the prompt or erases
another member's output. A `fault` or `exit<n>` member's output is dropped (a crashing standalone
hook delivers none), except members in _SWALLOWS, whose own `__main__` block swallows the
exception and exits 0. `main()` flushes and calls os._exit(0) so an abandoned thread cannot latch
a once-per-session flag or write state after delivery.

Timing: one record per dispatch appended to <project>/logs/prompt_dispatch_timing.jsonl, the
`_hook_state.log_path` home (HARNESS_PROMPT_DISPATCH_TIMING overrides); a failed or stalled write is
named on stderr. `--report [N]` prints per-member median, p95 and non-ok counts over the last N
records; any other argument prints usage and exits 2.

Wired in: settings.base.json hooks.UserPromptSubmit (composed into settings.json).
"""

import importlib
import io
import json
import math
import os
import statistics
import sys
import threading
import time
import traceback

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import _optional_hooks  # noqa: E402 - after the sys.path insert above

# (module basename, budget seconds). A plain literal: other tools read it with ast.literal_eval.
CHAIN = (
    ("prompt_memory_loader", 30),
    ("critical_analysis_reminder", 30),
    ("prompt_git_state_delta", 15),
    ("tool_routing_cumulative_reset", 15),
    ("tool_routing_prompt_synthesis", 15),
    ("shell_census", 25),
    ("sidecar_watch_rearm", 5),
    ("budget_posture", 15),
    ("activity_registry", 15),
    ("overnight_ask_guard", 5),
)

# Members whose standalone `__main__` block swallows main()'s exceptions and exits 0, so text they
# printed before the exception is still delivered there.
_SWALLOWS = frozenset({"budget_posture", "activity_registry"})

DEFAULT_REPORT_RECORDS = 100
_TIMING_WRITE_BUDGET = 1.5


class _Capture(io.StringIO):
    """StringIO that tolerates the stream calls a hook makes on a real console handle."""

    def reconfigure(self, **_kwargs):
        pass


class _Router:
    """Stands in for one sys stream. Attribute access goes to the calling thread's capture, or to
    the real stream on any thread that has none."""

    def __init__(self, real, local, slot):
        self._real, self._local, self._slot = real, local, slot

    def _target(self):
        return getattr(self._local, self._slot, self._real)

    def __getattr__(self, name):
        return getattr(self._target(), name)

    def __iter__(self):
        return iter(self._target())


class _Member:
    def __init__(self, name, budget, payload_text):
        self.name, self.budget = name, budget
        self.inp = io.StringIO(payload_text)
        self.out, self.err = _Capture(), _Capture()
        self.status, self.trace, self.ms = "ok", "", 0
        self.abandoned = self.done = False
        self.lock = threading.Lock()
        self.thread = None


def _run_member(member, local):
    local.inp, local.out, local.err = member.inp, member.out, member.err
    started = time.monotonic()
    status, trace = "ok", ""
    phase = "import"
    try:
        module = importlib.import_module(member.name)
        phase = "runtime"
        returned = module.main()
        if isinstance(returned, int) and not isinstance(returned, bool) and returned != 0:
            status = "exit%d" % returned
    except SystemExit as exc:
        if exc.code not in (None, 0):
            status = "exit%d" % exc.code if isinstance(exc.code, int) else "exit1"
            if not isinstance(exc.code, int):
                member.err.write(str(exc.code) + "\n")
    except BaseException:
        status = "fault"
        trace = "(%s) %s" % (phase, traceback.format_exc())
    with member.lock:
        if not member.abandoned:
            member.status, member.trace = status, trace
            member.ms = round((time.monotonic() - started) * 1000)
            member.done = True


def _normalize(text):
    """(text for the model, True when a JSON object with no additionalContext was dropped)."""
    text = text.strip()
    if not text or text == "{}":
        return "", False
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return text, False
        if isinstance(parsed, dict):
            specific = parsed.get("hookSpecificOutput")
            context = specific.get("additionalContext") if isinstance(specific, dict) else None
            if isinstance(context, str):
                return context.strip(), False
            return "", True
    return text, False


def _timing_path():
    from _hook_state import log_path

    return log_path("prompt_dispatch_timing.jsonl", "HARNESS_PROMPT_DISPATCH_TIMING")


def _write_timing(path, payload_text, wall_ms, members, result):
    """Append one record; `result["ok"]` says whether it landed. Imports lazily so a broken
    _hook_state cannot stop the dispatcher importing."""
    try:
        from _hook_state import append_jsonl_rotating

        try:
            session = str(json.loads(payload_text).get("session_id") or "")[:8]
        except (ValueError, AttributeError):
            session = ""
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": session,
                  "wall_ms": wall_ms,
                  "hooks": {m.name: {"ms": m.ms, "status": m.status} for m in members}}
        result["ok"] = bool(append_jsonl_rotating(path or _timing_path(), [record]))
    except Exception:
        pass


def _render(members):
    """(text parts, stderr notes) for finished members. Pure: no streams, no threads."""
    parts, notes = [], []
    for member in members:
        if member.status == "timeout":
            notes.append("[prompt-dispatch] %s timeout: exceeded %g s\n" % (member.name, member.budget))
            continue
        if member.status == "fault":
            notes.append("[prompt-dispatch] %s fault %s\n" % (member.name, member.trace.rstrip()))
        elif member.status != "ok":
            notes.append("[prompt-dispatch] %s %s\n" % (member.name, member.status))
        if member.err.getvalue().strip():
            notes.append("[prompt-dispatch] %s stderr: %s\n" % (member.name, member.err.getvalue().rstrip()))
        if member.status == "ok" or (member.status == "fault" and member.name in _SWALLOWS):
            text, dropped = _normalize(member.out.getvalue())
            if dropped:
                notes.append("[prompt-dispatch] %s emitted JSON without additionalContext; dropped\n" % member.name)
            if text:
                parts.append(text)
    return parts, notes


def dispatch(payload_text, chain=None, timing_path=None):
    """Run the chain over one payload. Returns (exit_code, stdout_text, stderr_text).

    The real streams are restored on return, unless a member was abandoned: then the router stays
    so that member's late writes land in its own discarded capture, and the caller is expected to
    leave through os._exit (main does).
    """
    started = time.monotonic()
    local = threading.local()
    members = [_Member(name, budget, payload_text)
               for name, budget in _optional_hooks.adopted(CHAIN if chain is None else chain)]
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    abandoned = False
    try:
        sys.stdin = _Router(saved[0], local, "inp")
        sys.stdout = _Router(saved[1], local, "out")
        sys.stderr = _Router(saved[2], local, "err")
        sys.argv = [os.path.abspath(__file__)]
        for member in members:
            member.thread = threading.Thread(target=_run_member, args=(member, local), daemon=True,
                                             name="prompt-dispatch-%s" % member.name)
        for member in members:
            member.thread.start()
        for member in members:
            member.thread.join(max(0.0, started + member.budget - time.monotonic()))
            with member.lock:
                if not member.done:
                    abandoned = member.abandoned = True
                    member.status, member.ms = "timeout", round(member.budget * 1000)
                elif member.ms > member.budget * 1000:
                    member.status = "timeout"
    finally:
        sys.argv = saved[3]
        if not abandoned:
            sys.stdin, sys.stdout, sys.stderr = saved[:3]

    parts, notes = _render(members)
    result = {}
    writer = threading.Thread(
        target=_write_timing, daemon=True, name="prompt-dispatch-timing",
        args=(timing_path, payload_text, round((time.monotonic() - started) * 1000), members, result))
    writer.start()
    writer.join(_TIMING_WRITE_BUDGET)
    if not result.get("ok"):
        notes.append("[prompt-dispatch] timing log write failed or stalled\n")
    return 0, "\n".join(parts) or "{}", "".join(notes)


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def report(n=DEFAULT_REPORT_RECORDS):
    """Per-member median, p95 and non-ok counts over the last `n` timing records."""
    try:
        path = _timing_path()
    except Exception as exc:  # noqa: BLE001 - the reader names the fault instead of a traceback
        return "prompt_dispatch: cannot resolve the timing log (%s: %s)" % (type(exc).__name__, exc)
    records, skipped = [], 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("hooks"), dict):
                    records.append(parsed)
                else:
                    skipped += 1
    except OSError:
        return "prompt_dispatch: no timing records at %s" % path
    records = records[-max(1, n):]
    if not records:
        return "prompt_dispatch: no timing records at %s" % path

    walls = [r["wall_ms"] for r in records if isinstance(r.get("wall_ms"), (int, float))]
    header = "prompt_dispatch timing: last %d record(s) from %s" % (len(records), path)
    if walls:
        header += "; wall median %.0fms p95 %.0fms" % (statistics.median(walls), _percentile(walls, 0.95))
    if skipped:
        header += "; %d unreadable line(s) skipped" % skipped
    lines = [header]

    per_member = {}
    for record in records:
        for name, entry in record["hooks"].items():
            if isinstance(entry, dict) and isinstance(entry.get("ms"), (int, float)):
                per_member.setdefault(name, []).append((entry["ms"], str(entry.get("status"))))
    order = [name for name, _budget in CHAIN]
    for name in sorted(per_member, key=lambda k: (order.index(k) if k in order else len(order), k)):
        samples = per_member[name]
        times = [ms for ms, _status in samples]
        bad = {}
        for _ms, status in samples:
            if status != "ok":
                bad[status] = bad.get(status, 0) + 1
        breakdown = " [%s]" % " ".join("%s:%d" % item for item in sorted(bad.items())) if bad else ""
        lines.append("%-30s n=%-4d median=%.0fms p95=%.0fms non_ok=%d%s" % (
            name, len(samples), statistics.median(times), _percentile(times, 0.95),
            sum(bad.values()), breakdown))
    return "\n".join(lines)


def _read_payload():
    stream = sys.stdin
    raw = getattr(stream, "buffer", None)
    return raw.read().decode("utf-8", errors="replace") if raw is not None else stream.read()


def _write_real(stream, text):
    try:
        data = text.encode("utf-8", errors="replace")
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
        else:
            stream.write(text)
            stream.flush()
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if args and args[0] == "--report":
        count = int(args[1]) if len(args) > 1 and args[1].isdigit() else DEFAULT_REPORT_RECORDS
        print(report(count))
        return
    if args:
        sys.stderr.write("usage: prompt_dispatch.py [--report [N]]  (no argument: read the payload from stdin)\n")
        sys.stderr.flush()
        os._exit(2)

    try:
        _code, stdout, stderr = dispatch(_read_payload())
    except Exception:
        stdout, stderr = "{}", "[prompt-dispatch] dispatcher fault %s\n" % traceback.format_exc()
    _write_real(sys.stdout, stdout)
    if stderr:
        _write_real(sys.stderr, stderr)
    os._exit(0)


if __name__ == "__main__":
    main()
