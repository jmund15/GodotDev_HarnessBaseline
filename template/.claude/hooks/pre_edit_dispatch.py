#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Write|Edit tool family.

One settings.json entry runs the edit chain in one interpreter, in a deterministic
order. A content normalizer feeds its updatedInput to later checks; no advisory
or rewrite is emitted for a call that is denied.

Order (the first block wins; advisories emit after all checks):
  1. pattern_enforcer.process            — .cs computed attributes; GD.Print in C# → block
     tres_format_guard.process           — scene and resource file normalization and unresolved-reference advice
  2. composed_output_guard.process       — direct edit of composed or generated text → exit 2 + stderr
  3. provenance_label_guard.process      — provenance label added to loaded guidance → exit 2
     model_name_guard.process            — registry model name added to a mechanism-layer file → deny;
                                           silent where the machine has no model registry
  4. harness_edit_skill_reminder.process — harness edit → deny until instruction_quality is in context,
                                           then a once-per-compaction advisory
  5. running_script_edit_guard.process   — live script instance → advisory
     repeat_fix_notice.process           — first edit of a runtime harness file with a repeat of counted fixes
                                           → advisory naming them and the redesign route

Sub-hook contract: `process(payload)` returns one of
  {"block": <message>}   hard block — stderr + exit 2, the tool call is aborted
  {"deny": <reason>}     permissionDecision deny — stdout JSON + exit 0, also aborts
  {"context": <text>}    model-visible advisory, merged into ONE additionalContext
  {"stderr": <text>}     non-blocking note on stderr
  {"updatedInput": ...} corrected tool input, forwarded to later hooks and the client
  None                   nothing to say
Only one content writer matches a call: pattern_enforcer for .cs; tres_format_guard for .tres.
Each sub-hook self-gates on tool_name, so the union matcher is safe, and each keeps its
own main() for standalone runs and for pre_bash_dispatch.py's in-process use.

Output contract:
  - Advisories merge into one hookSpecificOutput.additionalContext payload (exit 0) —
    the only model-visible advisory channel on PreToolUse (archive_hook_gotchas.md).
  - A sub-hook fault blocks only when its module sets `FAIL_CLOSED = True` (floor),
    the same contract as pre_bash_dispatch. Other faults allow with additionalContext
    naming the fault and preserve later checks. No edit sub-hook is floor today.

Wired in: settings.json hooks.PreToolUse with matcher "Write|Edit".
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks  # noqa: E402

CHAIN = (
    ("pattern_enforcer", "enforcement"),
    ("tres_format_guard", "advisory"),
    ("composed_output_guard", "enforcement"),
    ("provenance_label_guard", "enforcement"),
    ("model_name_guard", "advisory"),
    ("harness_edit_skill_reminder", "advisory"),
    ("running_script_edit_guard", "advisory"),
    ("repeat_fix_notice", "advisory"),
)
CHAIN = _optional_hooks.adopted(CHAIN)  # a sub-hook of a layer this project did not adopt is absent

try:
    import _hook_state
    from _hook_state import give_back
except Exception:  # noqa: BLE001 - without it a dropped line keeps its once-key spent
    _hook_state = None

    def give_back(start, stop):
        return None


def _payload(**fields):
    return json.dumps({"hookSpecificOutput": dict(hookEventName="PreToolUse", **fields)})


def _fault(name, phase, exc):
    return f"[pre-edit-dispatch] {name} {phase} fault: {type(exc).__name__}: {exc}\n"


def _member_fault(name, phase, exc, fail_closed, contexts, notes):
    fault = _fault(name, phase, exc)
    notes.append(fault)
    if fail_closed:
        return 2, "", "".join(notes) + f"{name} is a floor guard; repair it before editing.\n"
    contexts.append(fault.strip() + f"; its checks did not run. Repair .claude/hooks/{name}.py.")
    return None


def dispatch(input_data, chain=None):
    """Run the chain over one payload. Returns (exit_code, stdout_text, stderr_text)."""
    contexts, notes = [], []
    updated = None
    started = _hook_state.spent_mark() if _hook_state else 0
    for entry in (chain if chain is not None else CHAIN):
        name, second = entry
        before = _hook_state.spent_mark() if _hook_state else 0
        member = second if callable(second) else None
        try:
            if member is None:
                member = importlib.import_module(name)
            process = member if callable(member) else member.process
        except Exception as exc:
            denied = _member_fault(name, "import", exc, getattr(member, "FAIL_CLOSED", False), contexts, notes)
            if denied:
                give_back(started, before)
                return denied
            continue
        fail_closed = getattr(member, "FAIL_CLOSED", False)
        try:
            result = process(input_data) or {}
            if not isinstance(result, dict):
                raise TypeError("process result is not an object")
        except Exception as exc:
            denied = _member_fault(name, "runtime", exc, fail_closed, contexts, notes)
            if denied:
                give_back(started, before)
                return denied
            continue

        if result.get("block"):
            give_back(started, before)
            return 2, "", "".join(notes) + result["block"] + "\n"
        if result.get("deny"):
            give_back(started, before)
            return 0, _payload(permissionDecision="deny",
                               permissionDecisionReason=result["deny"]), "".join(notes)
        if result.get("updatedInput") is not None:
            updated = result["updatedInput"]
            input_data = dict(input_data, tool_input=updated)
        if result.get("context"):
            contexts.append(result["context"])
        if result.get("stderr"):
            notes.append(result["stderr"])

    fields = {}
    if contexts:
        fields["additionalContext"] = "\n\n".join(contexts)
    if updated is not None:
        fields["updatedInput"] = updated
    stdout = _payload(**fields) if fields else ""
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
