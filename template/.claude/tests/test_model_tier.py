#!/usr/bin/env python3
"""Cases for hooks/_model_tier.py and the tier line session_model_rails.py injects.

Run: python3 .claude/tests/test_model_tier.py   (plain asserts, no pytest dependency)

The load-bearing case is the LAST one: a SessionStart payload with no `model` (what /clear and
conversation recovery send) must resolve to `strict`, not silently promote the session.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, HOOKS)

import _model_tier  # noqa: E402

RAILS = os.path.join(HOOKS, "session_model_rails.py")
failures = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def rails_stdout(payload, env_extra=None):
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SIDECAR", None)
    env.pop("CLAUDE_CODE_SIDECAR_SHAPE", None)
    env.pop("CLAUDE_CODE_SIDECAR_TIER", None)
    env["HARNESS_HOOK_STATE_DIR"] = STATE_DIR
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, RAILS], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)
    return proc.stdout


def tier_lines(out):
    return [ln for ln in out.splitlines() if "Session tier:" in ln]


STATE_DIR = tempfile.mkdtemp(prefix="harness_tier_test_")

print("=" * 78)
print("MODEL TIER — tier_of() and the rails tier line")
print("=" * 78)

# --- tier_of ------------------------------------------------------------------------------
for model, expected in [
    ("fable", "fable"),
    ("claude-mythos-5-1", "fable"),
    ("claude-opus-5[1m]", "opus"),
    ("claude-opus-5", "opus"),
    ("sonnet", "strict"),
    ("claude-haiku-4-5", "strict"),
    (None, "strict"),
    ("", "strict"),
]:
    got = _model_tier.tier_of(model)
    check(f"tier_of({model!r}) == {expected}", got == expected, got)

# --- session_tier round trip --------------------------------------------------------------
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE_DIR
check("session_tier() of an unseen session is strict",
      _model_tier.session_tier("unseen-session") == "strict")
_model_tier.write_session_tier("sess-fable-1", "fable")
check("session_tier() reads back the cached tier",
      _model_tier.session_tier("sess-fable-1") == "fable")

# --- rails stdout: exactly one tier line, with the right tier-only clauses ------------------
CASES = [
    ("fable", "fable", True),
    ("claude-opus-5[1m]", "opus", False),
    ("sonnet", "strict", False),
    ("claude-mythos-5-1", "fable", True),
    (None, "strict", False),
]
for model, expected, wants_verify in CASES:
    payload = {"session_id": f"sid-{expected}-{model}", "hook_event_name": "SessionStart"}
    if model is not None:
        payload["model"] = model
    out = rails_stdout(payload)
    lines = tier_lines(out)
    label = model if model is not None else "<no model field>"
    check(f"[{label}] exactly one tier line", len(lines) == 1, f"{len(lines)} lines")
    check(f"[{label}] tier line names `{expected}`",
          bool(lines) and f"`{expected}`" in lines[0], lines[0] if lines else "")

    open_tier = expected in ("opus", "fable")
    check(f"[{label}] shortest-response clause {'present' if open_tier else 'absent'}",
          ("shortest response that fully answers" in out) == open_tier)
    check(f"[{label}] voice clause {'present' if open_tier else 'absent'}",
          ("no sentence whose job is to sound good" in out) == open_tier)
    check(f"[{label}] verify-the-name clause {'present' if wants_verify else 'absent'}",
          ("Recognizing a name is not knowing its current state" in out) == wants_verify)
    check(f"[{label}] strict-only instruction {'present' if expected == 'strict' else 'absent'}",
          ("read every `## strict` section" in out) == (expected == "strict"))

# --- delegate branch: tier comes from CLAUDE_CODE_SIDECAR_TIER, never the payload model -----
for env_tier, expected in [("terse", "opus"), ("none", "fable"), ("fable", "fable"),
                           ("strict", "strict"), ("", "strict"), ("bogus", "strict")]:
    out = rails_stdout(
        {"session_id": "sid-delegate", "model": "fable", "hook_event_name": "SessionStart"},
        {"CLAUDE_CODE_SIDECAR": "1", "CLAUDE_CODE_SIDECAR_SHAPE": "review",
         "CLAUDE_CODE_SIDECAR_TIER": env_tier},
    )
    lines = tier_lines(out)
    check(f"[delegate TIER={env_tier or '<unset>'}] exactly one tier line", len(lines) == 1,
          f"{len(lines)} lines")
    check(f"[delegate TIER={env_tier or '<unset>'}] tier is `{expected}`",
          bool(lines) and f"`{expected}`" in lines[0], lines[0] if lines else "")

print("-" * 78)
print(f"{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
