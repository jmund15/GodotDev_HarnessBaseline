#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Write|Edit tool family.

One settings.json entry replacing four separate hook commands (pattern_enforcer.py,
harness_edit_skill_reminder.py, readonly_lens_write_guard.py,
running_script_edit_guard.py). One interpreter spawn per matched call instead of four,
and deterministic in-process ordering instead of relying on matcher-block conventions
(per docs, matching hooks run in parallel), which is what the two blocks below need:
an advisory must never be emitted for a call that is about to be denied.

Order (blocks before advisories; the first block wins and short-circuits):
  1. pattern_enforcer.process            — dangerous content/command → exit 2 + stderr
  2. harness_edit_skill_reminder.process — harness edit without the skill → deny;
                                           otherwise the once-per-session advisory
  3. readonly_lens_write_guard.process   — off-lens subagent write → stderr note, no block
  4. running_script_edit_guard.process   — live script instance → advisory

Sub-hook contract: `process(payload)` returns one of
  {"block": <message>}   hard block — stderr + exit 2, the tool call is aborted
  {"deny": <reason>}     permissionDecision deny — stdout JSON + exit 0, also aborts
  {"context": <text>}    model-visible advisory, merged into ONE additionalContext
  {"stderr": <text>}     non-blocking note on stderr
  None                   nothing to say
Each sub-hook self-gates on tool_name, so the union matcher is safe, and each keeps its
own main() for standalone runs and for pre_bash_dispatch.py's in-process use.

Output contract:
  - Advisories merge into one hookSpecificOutput.additionalContext payload (exit 0) —
    the only model-visible advisory channel on PreToolUse (archive_hook_gotchas.md).
  - Each sub-hook imports and runs alone. Enforcement faults deny the edit, except an edit under
    `.claude/hooks/`, which proceeds with an advisory so the broken guard can be repaired.
    Advisory faults stay non-blocking. Both name the faulted sub-hook on stderr.

Wired in: settings.json hooks.PreToolUse with matcher "Write|Edit".
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENFORCEMENT = frozenset({"pattern_enforcer", "harness_edit_skill_reminder"})
CHAIN = (
    ("pattern_enforcer", "enforcement"),
    ("harness_edit_skill_reminder", "enforcement"),
    ("readonly_lens_write_guard", "advisory"),
    ("running_script_edit_guard", "advisory"),
)


def _payload(**fields):
    return json.dumps({"hookSpecificOutput": dict(hookEventName="PreToolUse", **fields)})


def _fault(name, phase, exc):
    return f"[pre-edit-dispatch] {name} {phase} fault: {type(exc).__name__}: {exc}\n"


def _repairs_hook(input_data):
    """True when the edit targets `.claude/hooks/`, where a faulted guard gets repaired."""
    tool_input = input_data.get("tool_input") if isinstance(input_data, dict) else None
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str):
        return False
    normalized = path.replace("\\", "/").lower()
    return normalized.startswith(".claude/hooks/") or "/.claude/hooks/" in normalized


def _enforcement_fault(input_data, name, detail, contexts, notes):
    """The deny result for a faulted enforcement sub-hook, or None for an edit that repairs a hook.

    Denying every edit would also deny the edit that repairs the guard, so an edit under
    `.claude/hooks/` proceeds with an advisory naming the fault."""
    if _repairs_hook(input_data):
        contexts.append(f"Enforcement sub-hook {name} {detail}; its checks did not run on this hook edit. "
                        f"Repair .claude/hooks/{name}.py.")
        return None
    return 0, _payload(
        permissionDecision="deny",
        permissionDecisionReason=(
            f"Edit denied because enforcement sub-hook {name} {detail}. Repair .claude/hooks/{name}.py "
            f"first; edits under .claude/hooks/ stay allowed while it is broken."
        ),
    ), "".join(notes)


def dispatch(input_data, chain=None):
    """Run the chain over one payload. Returns (exit_code, stdout_text, stderr_text)."""
    contexts, notes = [], []
    for entry in (chain if chain is not None else CHAIN):
        name, second = entry
        if callable(second):
            posture, process = ("enforcement" if name in ENFORCEMENT else "advisory"), second
        else:
            posture, process = second, None
            try:
                process = importlib.import_module(name).process
            except Exception as exc:
                notes.append(_fault(name, "import", exc))
                if posture == "enforcement":
                    denied = _enforcement_fault(input_data, name, "failed to import", contexts, notes)
                    if denied:
                        return denied
                continue
        try:
            result = process(input_data) or {}
        except Exception as exc:
            notes.append(_fault(name, "runtime", exc))
            if posture == "enforcement":
                denied = _enforcement_fault(input_data, name, "faulted", contexts, notes)
                if denied:
                    return denied
            continue
        if not isinstance(result, dict):
            notes.append(_fault(name, "runtime", TypeError("process result is not an object")))
            if posture == "enforcement":
                denied = _enforcement_fault(input_data, name, "returned an invalid result", contexts, notes)
                if denied:
                    return denied
            continue

        if result.get("block"):
            return 2, "", "".join(notes) + result["block"] + "\n"
        if result.get("deny"):
            return 0, _payload(permissionDecision="deny",
                               permissionDecisionReason=result["deny"]), "".join(notes)
        if result.get("context"):
            contexts.append(result["context"])
        if result.get("stderr"):
            notes.append(result["stderr"])

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
