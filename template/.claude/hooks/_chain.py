# -*- coding: utf-8 -*-
"""The one chain runner behind the five event dispatchers (`pre_edit`, `post_edit`, `pre_read`, `post_read`,
`prompt` `_dispatch.py`). Each dispatcher file is a stub that calls `main(event)`; EVENTS holds what differs.
Two dispatchers deliberately do not run a chain, and `tests/test_chain.py` pins that no third appears:
`_floor.py` answers the loss floor from its own `FLOOR_MEMBERS`, whose members must deny on their own fault and
return one merged verdict, not an advisory list; `pre_workflow_dispatch.py` runs ordered steps that each read the
call the step before rewrote, which a chain of independent members cannot express.

Member contract (every event but `prompt`): `process(payload)` on a module, or a callable, returns None or a dict
with any of
  {"block": text}        PreToolUse: stderr + exit 2, the call is aborted
  {"deny": text}         PreToolUse: permissionDecision deny, exit 0, the call is aborted
  {"updatedInput": obj}  PreToolUse: merged over `tool_input`, seen by later members, forwarded to the client
  {"context": text}      model-visible advisory; all advisories merge into one additionalContext
  {"stderr": text}       a note on stderr
A PostToolUse chain cannot undo a call, so it ignores block, deny and updatedInput.
`prompt` members are standalone scripts (stdin payload, stdout text) run through `_inprocess_hook`, because each
is also a registered or proven script of its own.

Members: EVENTS[event].shared, then the project's own, named in `adaptation.json` `hook_members[event]`
(`_optional_hooks.members`). A member whose file is absent is skipped silently.

Per-event differences, each with its reason (the EVENTS rows):
  fail posture   a PreToolUse member that sets `FAIL_CLOSED = True` blocks on its own fault, because it guards;
                 every other fault allows, names the member and keeps the rest of the chain. PostToolUse and
                 prompt members only advise, so their faults always allow.
  timeout        only PostToolUse bounds each member (EVENTS[...].timeout): a late advisory is worth nothing,
                 and a thread cannot interrupt a guard's verdict, which the registration's timeout cuts instead.
  channel        PreToolUse/PostToolUse answer in `hookSpecificOutput`; UserPromptSubmit stdout is the text itself
                 (`{}` when empty, Claude Code #10463), and its member faults go to stderr only so a fault line is
                 never mistaken for prompt text.
"""
import importlib
import json
import os
import queue
import sys
import threading
import traceback
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks  # noqa: E402

Event = namedtuple("Event", "hook_event shared guards timeout script")
EVENTS = {
    "pre_edit": Event("PreToolUse", ("running_script_edit_guard",), True, None, False),
    "pre_read": Event("PreToolUse", ("semantic_search_scope_guard",), True, None, False),
    # compact_directive_anchor: a state-file write points the session at it (a worktree session included).
    "post_edit": Event("PostToolUse", ("compact_directive_anchor",), False, 5.0, False),
    "post_read": Event("PostToolUse", (), False, None, False),
    "prompt": Event("UserPromptSubmit", ("prompt_git_state_delta", "overnight_ask_guard"), False, None, True),
}
MAX_CONTEXT_CHARS = 8_000

try:
    from _hook_state import give_back, spent_mark
except Exception:  # noqa: BLE001 - without it a dropped line keeps its once-key spent
    def spent_mark():
        return 0

    def give_back(start, stop):
        return None


def _name(member):
    return member if isinstance(member, str) else getattr(member, "__name__", repr(member))


def _call(row, member, payload):
    """(module-or-callable, result). A module is imported by name; the timeout bounds the whole call."""
    def work():
        target = importlib.import_module(member) if isinstance(member, str) else member
        process = target if callable(target) else target.process
        return target, process(payload)
    if not row.timeout:
        return work()
    box = queue.Queue(maxsize=1)

    def run_in_thread():
        try:
            box.put(("ok", work()))
        except Exception as exc:  # noqa: BLE001
            box.put(("fault", exc))
    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(row.timeout)
    if thread.is_alive():
        raise TimeoutError("timeout: exceeded %g s" % row.timeout)
    kind, value = box.get_nowait()
    if kind == "fault":
        raise value
    return value


def _script_text(out):
    """(text for the model, True when a JSON object with no additionalContext was dropped)."""
    text = out.strip()
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
            return (context.strip(), False) if isinstance(context, str) else ("", True)
    return text, False


def _run_scripts(label, members, payload):
    import _inprocess_hook
    parts, notes = [], []
    raw = json.dumps(payload)
    for member in members:
        name = _name(member)
        try:
            module = importlib.import_module(member) if isinstance(member, str) else member
        except BaseException:
            notes.append("[%s] %s fault (import) %s\n" % (label, name, traceback.format_exc().rstrip()))
            continue
        code, out, err = _inprocess_hook.run_hook(module, [], raw)
        if code or err.strip():
            notes.append("[%s] %s exit%d%s\n" % (label, name, code, ": " + err.rstrip() if err.strip() else ""))
        if code:
            continue
        text, dropped = _script_text(out)
        if dropped:
            notes.append("[%s] %s emitted JSON without additionalContext; dropped\n" % (label, name))
        if text:
            parts.append(text)
    return 0, "\n".join(parts) or "{}", "".join(notes)


def _output(row, **fields):
    return json.dumps({"hookSpecificOutput": dict(hookEventName=row.hook_event, **fields)})


def run(event, payload, members=None):
    """Run `event`'s chain over one payload. Returns (exit_code, stdout_text, stderr_text).

    `members` (names or callables) replaces the real chain, for a proof."""
    row = EVENTS[event]
    label = event.replace("_", "-") + "-dispatch"
    if members is None:
        members = _optional_hooks.members(event, row.shared)
    if row.script:
        return _run_scripts(label, members, payload)
    contexts, notes, updated = [], [], None
    started = spent_mark()
    for member in members:
        name = _name(member)
        before = spent_mark()
        try:
            target, result = _call(row, member, payload)
            result = result or {}
            if not isinstance(result, dict):
                raise TypeError("process result is not an object")
        except Exception as exc:  # noqa: BLE001
            fault = "[%s] %s fault: %s: %s" % (label, name, type(exc).__name__, exc)
            notes.append(fault + "\n")
            guard = None if isinstance(member, str) else member
            if guard is None and not isinstance(exc, ImportError):
                guard = sys.modules.get(member)
            if row.guards and getattr(guard, "FAIL_CLOSED", False):
                give_back(started, before)
                return 2, "", "".join(notes) + "%s is a floor guard; repair it before editing.\n" % name
            contexts.append("%s; its checks did not run. Repair .claude/hooks/%s.py." % (fault, name))
            continue
        if row.guards:
            if result.get("block"):
                give_back(started, before)
                return 2, "", "".join(notes) + str(result["block"]) + "\n"
            if result.get("deny"):
                give_back(started, before)
                return 0, _output(row, permissionDecision="deny",
                                  permissionDecisionReason=str(result["deny"])), "".join(notes)
            if isinstance(result.get("updatedInput"), dict):
                updated = dict(payload.get("tool_input") or {}, **result["updatedInput"])
                payload = dict(payload, tool_input=updated)
        if result.get("context"):
            text = str(result["context"])
            if len(text) > MAX_CONTEXT_CHARS:
                notes.append("[%s] %s context truncated from %d characters\n" % (label, name, len(text)))
                text = text[:MAX_CONTEXT_CHARS] + "\n[truncated by _chain]"
            contexts.append(text)
        if result.get("stderr"):
            notes.append(str(result["stderr"]))
    fields = {}
    if contexts:
        fields["additionalContext"] = "\n\n".join(contexts)
    if updated is not None:
        fields["updatedInput"] = updated
    return 0, _output(row, **fields) if fields else "", "".join(notes)


def main(event):
    """The dispatcher entry: the payload on stdin, the verdict on stdout and stderr, the exit code."""
    quiet = "{}" if EVENTS[event].script else ""
    raw = getattr(sys.stdin, "buffer", None)
    text = raw.read().decode("utf-8", errors="replace") if raw is not None else sys.stdin.read()
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError
    except ValueError:
        code, stdout, stderr = 0, quiet, ""
    else:
        try:
            code, stdout, stderr = run(event, payload)
        except Exception:  # noqa: BLE001 - a runner fault never blocks the call
            code, stdout, stderr = 0, quiet, "[%s] runner fault %s\n" % (event, traceback.format_exc())
    for stream, data in ((sys.stdout, stdout), (sys.stderr, stderr)):
        if not data:
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(data.encode("utf-8", errors="replace"))
            buffer.flush()
        else:
            stream.write(data)
    sys.exit(code)
