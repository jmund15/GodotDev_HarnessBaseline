#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the Write|Edit tool family.

One settings.json entry replacing seven separate hook commands
(check_logger_tag_prefix.py, plan_memory_reminder.py, design_surface_reminder.py,
load_steps_validator.py --hook, tres_format_guard.py --hook, harness_growth_guard.py,
self_eval_archive_guard.py). One interpreter spawn per matched call instead of seven,
and the advisories merge into ONE additionalContext payload instead of seven competing
ones.

Order (cheapest text scans first, then the file-reading guards):
  1. check_logger_tag_prefix.process — untagged JmoLogger call on an added line
  2. design_surface_reminder.process — [Export] bool / null strategy arg / new abstract rung
  3. plan_memory_reminder.process    — domain reminders for a .claude/plans/*.md write
  4. load_steps_validator.process    — engine resource load_steps header mismatch
  5. tres_format_guard.process       — engine resource non-canonical serialization
  6. harness_growth_guard.process    — harness markdown size/density measurement
  7. self_eval_archive_guard.process — self-evaluation archive validity
  8. retire_trigger_advisory.process — malformed retire_when metadata on a new memory or rules file
  9. runaway_scan_reaper.check       — throttled orphaned-search reaper (adapter: _reaper_process)

Sub-hook contract: `process(payload)` returns `{"context": <text>}` or None; the
dispatcher also honors `{"block": ...}` / `{"deny": ...}` / `{"stderr": ...}` so a future
blocking sub-hook needs no dispatcher change. Each sub-hook self-gates on tool_name and
file suffix, so the union matcher is safe, and each keeps its own main()/hook() for
standalone runs.

Output contract:
  - additionalContext is the only model-visible advisory channel on PostToolUse
    (stderr on an exit-0 PostToolUse path is a dead channel — archive_hook_gotchas.md).
  - Each sub-hook imports and runs alone. Import, runtime, and timeout faults stay
    non-blocking, name the sub-hook on stderr, and cannot erase another advisory.

Wired in: settings.json hooks.PostToolUse with matcher "Write|Edit".
"""

import importlib
import json
import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks  # noqa: E402

CHAIN = (
    ("check_logger_tag_prefix", "advisory"),
    ("design_surface_reminder", "advisory"),
    ("plan_memory_reminder", "advisory"),
    ("load_steps_validator", "advisory"),
    ("tres_format_guard", "advisory"),
    ("harness_growth_guard", "advisory"),
    ("self_eval_archive_guard", "advisory"),
    ("retire_trigger_advisory", "advisory"),
    ("runaway_scan_reaper", "reaper"),
)
CHAIN = _optional_hooks.adopted(CHAIN)  # a sub-hook of a layer this project did not adopt is absent
SUBHOOK_TIMEOUT = 5.0
MAX_CONTEXT_CHARS = 8_000


def _payload(**fields):
    return json.dumps({"hookSpecificOutput": dict(hookEventName="PostToolUse", **fields)})


def _fault(name, phase, detail):
    return f"[post-edit-dispatch] {name} {phase} fault: {detail}\n"


def _reaper_process(input_data):
    """Adapter: runaway_scan_reaper.check returns lines, the chain contract wants {"context"}."""
    lines = importlib.import_module("runaway_scan_reaper").check(input_data)
    return {"context": "\n".join(lines)} if lines else None


def _invoke(name, process, input_data):
    result_queue = queue.Queue(maxsize=1)

    def run():
        phase = "import"
        try:
            chosen = _reaper_process if process is None and name == "runaway_scan_reaper" else process
            target = chosen or importlib.import_module(name).process
            phase = "runtime"
            result_queue.put(("ok", target(input_data) or {}))
        except Exception as exc:
            result_queue.put(("fault", phase, f"{type(exc).__name__}: {exc}"))

    thread = threading.Thread(target=run, name=f"post-edit-{name}", daemon=True)
    thread.start()
    thread.join(SUBHOOK_TIMEOUT)
    if thread.is_alive():
        return "timeout", None
    try:
        return result_queue.get_nowait()
    except queue.Empty:
        return "fault", "runtime", "sub-hook returned no result"


def dispatch(input_data, chain=None):
    """Run the chain over one payload. Returns (exit_code, stdout_text, stderr_text)."""
    contexts, notes = [], []
    for entry in (chain if chain is not None else CHAIN):
        name, second = entry
        process = second if callable(second) else None
        result = _invoke(name, process, input_data)
        if result[0] == "timeout":
            notes.append(_fault(name, "timeout", f"exceeded {SUBHOOK_TIMEOUT:g} s"))
            continue
        if result[0] == "fault":
            notes.append(_fault(name, result[1], result[2]))
            continue
        result = result[1]
        if not isinstance(result, dict):
            continue

        if result.get("block"):
            return 2, "", "".join(notes) + result["block"] + "\n"
        if result.get("deny"):
            return 0, _payload(permissionDecision="deny",
                               permissionDecisionReason=result["deny"]), "".join(notes)
        if result.get("context"):
            text = str(result["context"])
            if len(text) > MAX_CONTEXT_CHARS:
                notes.append(_fault(name, "context", f"truncated from {len(text)} characters"))
                text = text[:MAX_CONTEXT_CHARS] + "\n[truncated by post_edit_dispatch]"
            contexts.append(text)
        if result.get("stderr"):
            notes.append(str(result["stderr"]))

    stdout = _payload(additionalContext="\n\n".join(contexts)) if contexts else ""
    return 0, stdout, "".join(notes)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    code, stdout, stderr = dispatch(input_data)
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
