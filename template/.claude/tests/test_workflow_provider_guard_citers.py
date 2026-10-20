#!/usr/bin/env python3
"""Runner 4 through the real Workflow main preflight; no engine or provider spends."""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "tools"), str(HERE.parent / "hooks")]
import workflow_provider_guard as guard
from _citer_boundary_cases import cases, stage_cases


def run():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="native_citers_") as root:
        for engine in ("dispatch", "review_fanout"):
            for blocking in (False, True):
                rows = [(label, job, None, bad) for label, job, bad in cases(root)] + stage_cases(root)
                for label, job, stage, bad in rows:
                    if engine == "review_fanout" and job.get("shape") != "review":
                        continue
                    args = {"agents" if engine == "review_fanout" else "jobs": [job]}
                    if stage:
                        args["ledger"] = {"stage": stage}
                    payload = {"tool_name": "Workflow", "tool_input": {
                        "scriptPath": f".claude/workflows/{engine}.js", "args": json.dumps(args)}}
                    out = io.StringIO()
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps(payload))))
                        stack.enter_context(contextlib.redirect_stdout(out))
                        for name, value in {
                            "tool_profile_refusal": None, "coverage_refusal": None,
                            "_session_record": (None, {"skills_loaded": ["orchestration"]}, None),
                            "_load_registry": None, "non_thinking_review_pins": [],
                            "decide_vocabulary": (None, None), "invalid_effort_pins": [],
                            "_session_plan": "anthropic", "decide_tiers": (None, [], []),
                            "transport_injection": None,
                            "band_and_pressure": (None, None), "five_hour_rule": (None, None), "_log": None,
                        }.items():
                            stack.enter_context(patch.object(guard, name, return_value=value))
                        stack.enter_context(patch.object(guard._session_transport, "resolve", return_value=("anthropic", "fixture")))
                        stack.enter_context(patch.object(guard.provider_capacity_guard, "refusal", return_value=None))
                        stack.enter_context(patch.object(guard.citer_coverage, "BLOCKING", blocking))
                        guard.main()
                    try:
                        emitted = json.loads(out.getvalue() or "{}")
                        channel = emitted.get("hookSpecificOutput", {})
                        deny = channel.get("permissionDecision") == "deny"
                        text = channel.get("permissionDecisionReason", "") if deny else channel.get("additionalContext", "")
                        # A gap denies while BLOCKING; with the flag off the guard stays silent: no advisory branch.
                        ok = deny == (bad and blocking)
                        ok = ok and (("Phase 2" in text and "lens_briefs.py" in text) if deny
                                     else "Phase 2" not in text and "citer coverage" not in text)
                    except (ValueError, AttributeError):
                        ok = False
                    print(f"{'ok' if ok else 'FAIL'} {engine} {'blocking' if blocking else 'flag off'} {label}")
                    failures += not ok
    print("all ok" if not failures else f"{failures} failure(s)")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(run())
