"""Re-runnable proof for hooks/prompt_memory_loader.py's MEMORY CHECK cadence (B5).

The full MEMORY CHECK text is news once. After that the only new information is a domain
the session has not searched, so the hook drops to the one-line NEW DOMAIN form, and a
compaction re-arms the full text. Every case feeds a real UserPromptSubmit payload and
asserts on stdout.

The `strict` tier keeps the old repeat cap instead; both paths are exercised by planting
`session_tier` in the state file the way `_model_tier.write_session_tier` does at
SessionStart.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_prompt_memory_loader.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "prompt_memory_loader.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "pml00001"
# Standard-execution prompts: past the 20-char floor, no high-risk / conversational /
# execution cue, so each lands on the branch this test governs.
SPELL = "Extract the ability trait tier into the assembly roster"
SPELL_AGAIN = "Consolidate one more ability archetype beside that trait tier"
VFX = "Attach the particle tint design doc to the Obsidian vault"
UNKNOWN = "Adjust the columns in the exported summary table"


def run(hook, payload, env):
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=90, env=env)
    return (r.stdout or "").strip()


def prompt(text, env, session=SID):
    return run(HOOK, {"prompt": text, "session_id": session,
                      "permission_mode": "default"}, env)


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


def main():
    tmp = tempfile.mkdtemp(prefix="pmlstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    strict_env = env
    plant_tier(tmp, SID, "opus")               # opus/fable: one delivery, state-gated
    failures = []

    cases = []
    out = prompt(SPELL, env)
    cases.append(("first standard prompt gets the full MEMORY CHECK",
                  "MEMORY CHECK" in out))

    out = prompt(SPELL_AGAIN, env)
    cases.append(("a second prompt in an already-searched domain is silent",
                  out in ("", "{}")))

    out = prompt(VFX, env)
    cases.append(("a prompt naming an unsearched domain gets the short NEW DOMAIN form",
                  "NEW DOMAIN" in out and "MEMORY CHECK" not in out))

    out = prompt(VFX, env)
    cases.append(("the same new domain does not fire twice", out in ("", "{}")))

    out = prompt(UNKNOWN, env)
    cases.append(("a prompt matching no domain nudges — unknown fails toward nudging",
                  "NEW DOMAIN" in out))

    # PreCompact through the real hook, not a hand-written state edit: the wiring is
    # what this case is for.
    run(PRECOMPACT, {"session_id": SID, "trigger": "auto"}, env)
    out = prompt(SPELL, env)
    cases.append(("the full text lands again after a compaction",
                  "MEMORY CHECK" in out))

    # Negatives: branches this cadence must not touch.
    out = prompt("thanks", env)
    cases.append(("a prompt under the 20-char floor stays silent", out in ("", "{}")))

    out = prompt("Debug why the freeze effect is not working on entities", env,
                 session="pml00002")
    cases.append(("the high-risk branch is uncapped and unaffected",
                  "HIGH-RISK TASK" in out))

    out = run(HOOK, {"prompt": SPELL, "session_id": "pml00003",
                     "permission_mode": "plan"}, env)
    cases.append(("the plan-mode branch is uncapped and unaffected",
                  "PLAN-PERMISSION MODE" in out))

    # --- strict tier: the repeat cap, not the state gate --------------------
    plant_tier(tmp, "pml00004", "strict")
    repeats = [prompt(SPELL, strict_env, session="pml00004") for _ in range(6)]
    cases.append(("strict tier repeats the full text up to the cap",
                  all("MEMORY CHECK" in r for r in repeats[:5])))
    cases.append(("strict tier stops at the cap",
                  "MEMORY CHECK" not in repeats[5]))

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
