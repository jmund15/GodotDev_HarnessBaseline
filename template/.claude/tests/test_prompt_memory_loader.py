"""Re-runnable proof for hooks/prompt_memory_loader.py's MEMORY CHECK cadence (B5).

The full MEMORY CHECK text is news once. After that the only new information is a domain
the session has not been reminded about, so the hook drops to the one-line NEW DOMAIN form,
and a compaction re-arms the full text. Reminder state must never claim a search happened.
Every case feeds a real UserPromptSubmit payload and asserts on stdout.

The `detailed` tier keeps the old repeat cap instead; both paths are exercised by planting
`session_tier` in the state file the way `_model_tier.write_session_tier` does at
SessionStart.

The hook runs from a scratch `.claude` tree (`_adaptation_fixture`) whose `adaptation.json`
plants two neutral domains, so the cadence is proven against the same table in every
checkout. A project's own domain triggers are that project's data, proven beside its table.
State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_prompt_memory_loader.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "prompt_memory_loader.py"
EXTRA = ("_hook_state.py", "_prompt_provenance.py", "plan_memory_reminder.py",
         "_claude_scope.py", "_model_tier.py", "_file_lock.py")
PRECOMPACT = os.path.join(fx.REAL_HOOKS, "transcript_backup.py")
SEED = {"memory_domains": [
    {"name": "Widgets", "triggers": ["widget", "gizmo"], "memory_keywords": ["widget"]},
    {"name": "Pipelines", "triggers": ["pipeline", "conveyor"], "memory_keywords": ["pipeline"]},
]}

SID = "pml00001"
# Standard-execution prompts: past the 20-char floor, no high-risk / conversational /
# execution cue, so each lands on the branch this test governs.
WIDGET = "Add a new widget tier to the gizmo roster"
WIDGET_AGAIN = "Add one more widget beside that gizmo tier"
PIPELINE = "Attach the conveyor onto the new pipeline stage"
UNKNOWN = "Rename the columns in the exported summary table"
# Matches no entry in the domain table, so the reminder it would earn is the generic one.
GENERIC = "Please widen the column headers in the exported summary listing"
GENERIC_AGAIN = "Please shorten the column headers in that exported summary listing"
NOTIFICATION = """<task-notification>
Background worker completed a widget inventory with statuses and returns from every hook.
</task-notification>"""
AGENT_MESSAGE = """<agent-message from="recall-auditor">
Widget, pipeline and refactoring checks returned from the agent.
</agent-message>"""
CROSS_SESSION_MESSAGE = """<cross-session-message from="peer-session">
Widget pipeline review completed in another session.
</cross-session-message>"""


def run(hook, payload, env):
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=90, env=env)
    return (r.stdout or "").strip()


def plant_tier(state_dir, session, tier):
    """Cache a session tier the way `_model_tier.write_session_tier` does at SessionStart."""
    path = os.path.join(state_dir, session[:8] + ".json")
    state = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    state["session_tier"] = tier
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def read_state(state_dir, session):
    path = os.path.join(state_dir, session[:8] + ".json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    scratch = fx.make_scratch(HOOK_NAME, os.path.join(fx.REAL_HOOKS, HOOK_NAME), seed=SEED,
                              extra_hook_files=EXTRA)
    hook = os.path.join(scratch, "hooks", HOOK_NAME)
    tmp = tempfile.mkdtemp(prefix="pmlstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")

    def prompt(text, session=SID):
        return run(hook, {"prompt": text, "session_id": session, "permission_mode": "default"}, env)

    plant_tier(tmp, SID, "condensed")               # condensed/minimal: one delivery, state-gated
    failures = []

    cases = []
    out = prompt(WIDGET)
    cases.append(("first standard prompt gets the full MEMORY CHECK",
                  "MEMORY CHECK" in out))
    state = read_state(tmp, SID)
    cases.append(("an emitted reminder is recorded as reminded, not searched",
                  "Widgets" in state.get("reminded_domains", [])
                  and "searched_domains" not in state))

    out = prompt(WIDGET_AGAIN)
    cases.append(("a second prompt in an already-reminded domain is silent",
                  out in ("", "{}")))

    out = prompt(PIPELINE)
    cases.append(("a prompt naming a not-yet-reminded domain gets the short NEW DOMAIN form",
                  "NEW DOMAIN" in out and "MEMORY CHECK" not in out))

    out = prompt(PIPELINE)
    cases.append(("the same new domain does not fire twice", out in ("", "{}")))

    out = prompt(UNKNOWN)
    cases.append(("a prompt matching a new domain nudges",
                  "NEW DOMAIN" in out))

    # Runtime notifications are user-role transport rows, not user intent. They must
    # neither emit a reminder nor consume the first-reminder/domain cadence.
    plant_tier(tmp, "pml00005", "condensed")
    out = prompt(NOTIFICATION, session="pml00005")
    cases.append(("a background task notification is silent", out in ("", "{}")))
    state = read_state(tmp, "pml00005")
    cases.append(("a notification records no reminded or searched domain",
                  "reminded_domains" not in state and "searched_domains" not in state))
    out = prompt("Review the widget behavior for the new gizmo effect", session="pml00005")
    cases.append(("the equivalent real user request still gets the first reminder",
                  "MEMORY CHECK" in out))

    for session, wrapper, label in (
        ("pml00007", AGENT_MESSAGE, "an actual agent-message wrapper"),
        ("pml00008", CROSS_SESSION_MESSAGE, "an actual cross-session-message wrapper"),
    ):
        plant_tier(tmp, session, "condensed")
        out = prompt(wrapper, session=session)
        state = read_state(tmp, session)
        cases.append((label + " is silent", out in ("", "{}")))
        cases.append((label + " records no reminder or search state",
                      "reminded_domains" not in state and "searched_domains" not in state))
        out = prompt("Review the real widget request from the human user", session=session)
        cases.append((label + " does not consume the next human reminder",
                      "MEMORY CHECK" in out))

    # Legacy `searched_domains` was written on reminder emission, not on an observed
    # search. It is not valid evidence and must not suppress the renamed reminder state.
    plant_tier(tmp, "pml00006", "condensed")
    legacy_path = os.path.join(tmp, "pml00006.json")
    with open(legacy_path, "r+", encoding="utf-8") as fh:
        legacy = json.load(fh)
        legacy["searched_domains"] = ["Widgets"]
        fh.seek(0)
        json.dump(legacy, fh)
        fh.truncate()
    out = prompt(WIDGET, session="pml00006")
    state = read_state(tmp, "pml00006")
    cases.append(("legacy reminder-as-search state cannot prove a completed search",
                  "MEMORY CHECK" in out and "Widgets" in state.get("reminded_domains", [])
                  and "searched_domains" not in state))

    # PreCompact through the real hook, not a hand-written state edit: the wiring is
    # what this case is for.
    run(PRECOMPACT, {"session_id": SID, "trigger": "auto"}, env)
    out = prompt(WIDGET)
    cases.append(("the full text lands again after a compaction",
                  "MEMORY CHECK" in out))

    # Negatives: branches this cadence must not touch.
    out = prompt("thanks")
    cases.append(("a prompt under the 20-char floor stays silent", out in ("", "{}")))

    out = prompt("Debug why the widget is not working on the gizmo", session="pml00002")
    cases.append(("the high-risk branch is uncapped and unaffected",
                  "HIGH-RISK TASK" in out))

    out = run(hook, {"prompt": WIDGET, "session_id": "pml00003",
                     "permission_mode": "plan"}, env)
    cases.append(("the plan-mode branch is uncapped and unaffected",
                  "PLAN-PERMISSION MODE" in out))

    # --- detailed tier: the repeat cap, not the state gate --------------------
    plant_tier(tmp, "pml00004", "detailed")
    repeats = [prompt(WIDGET, session="pml00004") for _ in range(6)]
    cases.append(("detailed tier repeats the full text up to the cap",
                  all("MEMORY CHECK" in r for r in repeats[:5])))
    cases.append(("detailed tier stops at the cap",
                  "MEMORY CHECK" not in repeats[5]))

    # --- the generic line: once per session, whatever the tier ---------------
    # With no domain matched there is no new information to carry, so the repeat cap
    # that serves a domain prompt would just reprint the same paragraph.
    plant_tier(tmp, "pml00009", "detailed")
    generic = [prompt(GENERIC if i % 2 == 0 else GENERIC_AGAIN, session="pml00009")
               for i in range(6)]
    cases.append(("the generic memory check matches no domain",
                  "MEMORY CHECK" in generic[0]))
    cases.append(("the generic memory check fires once per session on the detailed tier",
                  not any("MEMORY CHECK" in r for r in generic[1:])))

    plant_tier(tmp, "pml00010", "condensed")
    generic_condensed = [prompt(GENERIC, session="pml00010") for _ in range(2)]
    cases.append(("the generic memory check fires once per session on the condensed tier",
                  "MEMORY CHECK" in generic_condensed[0]
                  and "MEMORY CHECK" not in generic_condensed[1]))

    # The generic delivery already carried the full text, so a later domain prompt must
    # not reprint it — it takes the short NEW DOMAIN form, which is the new information.
    out = prompt(WIDGET, session="pml00010")
    cases.append(("a domain prompt after a generic fire gets the NEW DOMAIN form",
                  "NEW DOMAIN" in out and "MEMORY CHECK" not in out))

    # A status question after a primed domain carries no new domain: silent.
    plant_tier(tmp, "pml00011", "condensed")
    prompt(WIDGET, session="pml00011")
    out = prompt("Continue driving this to completion. What's the status currently?",
                 session="pml00011")
    cases.append(("a status-check prompt after a primed domain fires nothing", out in ("", "{}")))

    out = prompt("What's the status?", session="pml00012")
    cases.append(('"What\'s the status?" alone stays silent (short-circuit)',
                  out in ("", "{}")))

    raw_results = [subprocess.run([sys.executable, hook], input=raw, capture_output=True,
                                  text=True, timeout=90, env=env)
                   for raw in ('"notice"', "[1, 2]", "null", "42")]
    cases.append(("valid non-object JSON payloads are advisory no-ops",
                  all(r.returncode == 0 and (r.stdout or "").strip() in ("", "{}")
                      and not (r.stderr or "").strip() for r in raw_results)))

    fx.rm(scratch)
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
