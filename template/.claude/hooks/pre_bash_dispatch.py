#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Bash/PowerShell/Monitor tool family.

One settings.json entry replacing twelve separate hook commands. Each sub-hook keeps its own
`main()` and CLI contract (stdin JSON payload, stdout JSON / stderr + exit 2); this module runs
them in-process with stdin/stdout/stderr/argv swapped per hook, so one interpreter spawn serves
every guard instead of twelve. Measured: twelve cold Python starts per Bash call on Windows cost
more than any guard's own work (`pre_read_dispatch.py` is the read-family sibling).

Order is cheap-deny first, then the approver, then advisories; the loop STOPS at the first hard
block (exit 2 or `permissionDecision: deny`), so a blocked command never waits on
`gate_cadence_guard`'s queue wait. Native parallel hooks would report every block at once; this
reports the first. Merge otherwise follows the native aggregation: deny > ask > allow, every
`additionalContext` concatenated.

Sub-hook self-gating on `tool_name` makes the union matcher safe: a PowerShell call reaches the
Bash-only hooks and they exit 0 silently, exactly as they did on their own matcher.

Fail posture per sub-hook is unchanged: an exception or a non-2 non-zero exit is reported on
stderr (user-visible, never model-visible) and the next hook runs — the same thing the harness
did when that hook crashed as its own process.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", timeout = the
largest sub-hook timeout (gate_cadence_guard's queue wait).
"""

import io
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import with the REAL sys.stdout bound: four sub-hooks call sys.stdout.reconfigure() at import.
import bash_shape_guard  # noqa: E402
import cloud_test_enforcer  # noqa: E402
import compound_cd_approver  # noqa: E402
import gate_cadence_guard  # noqa: E402
import git_guardrails  # noqa: E402
import pattern_enforcer  # noqa: E402
import prototype_containment_guard  # noqa: E402
import provisional_totality_guard  # noqa: E402
import sidecar_dispatch_context  # noqa: E402
import tres_nullstrip_guard  # noqa: E402
import tres_script_strip_guard  # noqa: E402
import unbounded_scan_guard  # noqa: E402

# Guarded: baseline_classification_guard also imports baseline_sync (tools/), a heavier
# dependency than any other sub-hook here. A broken import must deny every `git commit`
# rather than silently skip classification -- so a failure here registers a stub `main()`
# that does exactly that and names the import error (Design §4 fail-closed).
try:
    import baseline_classification_guard  # noqa: E402
    _BASELINE_GUARD_IMPORT_ERROR = None
except Exception as _exc:  # noqa: BLE001 - any import failure must still deny, never crash
    _BASELINE_GUARD_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

    class _BaselineGuardImportStub:
        """Stands in for `baseline_classification_guard` when it fails to import. Denies
        every `git commit` it sees rather than allow an unclassified `.claude/` addition
        through unverified -- the same fail-closed posture the real guard's `main()` keeps
        for a runtime crash, extended to cover an import-time one."""

        __name__ = "baseline_classification_guard"
        __file__ = "baseline_classification_guard.py"

        def main(self):
            try:
                payload = json.loads(sys.stdin.read())
            except (json.JSONDecodeError, ValueError):
                return 0
            if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
                return 0
            command = (payload.get("tool_input") or {}).get("command") or ""
            if "commit" not in command:
                return 0
            sys.stderr.write(
                "BLOCKED git commit -- baseline_classification_guard failed to import (%s). "
                "Repair hooks/baseline_classification_guard.py; .claude/ classification "
                "cannot be verified until it imports cleanly.\n" % _BASELINE_GUARD_IMPORT_ERROR
            )
            return 2

    baseline_classification_guard = _BaselineGuardImportStub()

# (module, argv tail). Deny-shaped guards first, cheapest first; the approver; then advisories.
HOOKS = (
    (pattern_enforcer, ()),
    (cloud_test_enforcer, ()),
    (bash_shape_guard, ()),
    (git_guardrails, ()),
    (baseline_classification_guard, ()),
    (tres_script_strip_guard, ("--hook",)),
    (tres_nullstrip_guard, ("--hook",)),
    (prototype_containment_guard, ("--hook",)),
    (provisional_totality_guard, ("--hook",)),
    (gate_cadence_guard, ()),
    (compound_cd_approver, ()),
    (unbounded_scan_guard, ()),
    (sidecar_dispatch_context, ()),
)

_DECISION_RANK = {"deny": 3, "ask": 2, "allow": 1}


class _Capture(io.StringIO):
    """StringIO that tolerates the stream calls a hook makes on a real console handle."""

    def reconfigure(self, **_kwargs):
        pass


def run_hook(module, argv_tail, raw_payload):
    """Run one sub-hook's main() in-process. Returns (exit_code, stdout, stderr)."""
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    out, err = _Capture(), _Capture()
    sys.stdin = io.StringIO(raw_payload)
    sys.stdout, sys.stderr = out, err
    sys.argv = [getattr(module, "__file__", module.__name__)] + list(argv_tail)
    code = 0
    try:
        ret = module.main()
        if isinstance(ret, int):
            code = ret
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception:
        code = 1
        err.write(traceback.format_exc())
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def _hook_output(stdout_text):
    """The hookSpecificOutput dict a sub-hook printed, or {} when it printed nothing usable."""
    text = stdout_text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    hso = parsed.get("hookSpecificOutput")
    return hso if isinstance(hso, dict) else {}


def dispatch(raw_payload, hooks=HOOKS):
    """Run the chain and return (exit_code, stdout_text, stderr_text) for the dispatcher process."""
    decision, reason, contexts, extra, user_stderr = None, None, [], {}, []
    for module, argv_tail in hooks:
        code, out, err = run_hook(module, argv_tail, raw_payload)
        if code == 2:
            return 2, "", err if err.strip() else "%s blocked the call." % module.__name__
        if code != 0:
            user_stderr.append("[pre_bash_dispatch] %s exited %s: %s" % (module.__name__, code, err.strip()))
            continue
        hso = _hook_output(out)
        ctx = hso.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            contexts.append(ctx)
        pd = hso.get("permissionDecision")
        if pd in _DECISION_RANK and _DECISION_RANK[pd] > _DECISION_RANK.get(decision, 0):
            decision, reason = pd, hso.get("permissionDecisionReason")
        for key, value in hso.items():
            if key not in ("hookEventName", "permissionDecision", "permissionDecisionReason",
                           "additionalContext") and key not in extra:
                extra[key] = value
        if decision == "deny":
            break

    hso = {"hookEventName": "PreToolUse"}
    if decision:
        hso["permissionDecision"] = decision
        if reason:
            hso["permissionDecisionReason"] = reason
    if contexts:
        hso["additionalContext"] = "\n\n".join(contexts)
    hso.update(extra)
    stdout_text = json.dumps({"hookSpecificOutput": hso}) if len(hso) > 1 else ""
    return 0, stdout_text, "\n".join(user_stderr)


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        sys.exit(0)
    code, out, err = dispatch(raw)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err + ("\n" if not err.endswith("\n") else ""))
    sys.exit(code)


if __name__ == "__main__":
    main()
