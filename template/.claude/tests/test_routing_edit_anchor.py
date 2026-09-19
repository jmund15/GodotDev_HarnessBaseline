#!/usr/bin/env python3
"""Verify current routing boundaries: path/read count never imply bulk I/O,
while the C# symbol-navigation nudge stays intact for an edit-shaped prompt.
The per-turn edit anchor retired with the cumulative nudge; nothing reads it."""
import json, os, shutil, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS = os.path.join(REPO, ".claude", "hooks")
sys.path.insert(0, HOOKS)

import tool_routing_nudge as nudge

TMP = tempfile.mkdtemp()
os.environ["HARNESS_HOOK_STATE_DIR"] = TMP   # _hook_state.state_path reads this per call

VAULT = r"C:\Users\dev\Documents\Vault\Design\WindSystem.md"
HARNESS = os.path.join(REPO, ".claude", "skills", "architecture_brainstorm", "SKILL.md")

results = []


def seed(sid, prompt):
    p = os.path.join(TMP, f"{sid[:8]}.json")
    with open(p, "w") as f:
        json.dump({"last_prompt": prompt}, f)


def check(label, got, want):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label}  (nudged={got}, expected={want})")


def nudged(sid, tool_input, tool="Read"):
    block, msg = nudge.process(
        {"tool_name": tool, "tool_input": tool_input, "session_id": sid}
    )
    return msg is not None


# --- Path alone must stay silent; symbol-navigation advice remains ---------
seed("s1______", "summarize the wind system design")
check("synthesis-shaped path is not bulk-I/O evidence", nudged("s1______", {"file_path": VAULT}), False)

seed("s2______", "how does the spell pipeline work")
check("PascalCase Grep on .cs",
      nudged("s2______", {"pattern": "SpellFactory", "glob": "*.cs"}, "Grep"), True)

seed("s3______", "give me an overview of the architecture doc")
check("Obsidian path is not bulk-I/O evidence",
      nudged("s3______", {"target": {"path": "Claude/Architecture/Overview.md"}},
             "mcp__obsidian__obsidian_get_note"), False)

# --- Must now be silent (false-positive class) ---------------------------
seed("s4______", "read the brainstorm skill")
check("harness .claude path", nudged("s4______", {"file_path": HARNESS}), False)

seed("s5______", "summarize the wind system design")
check("bounded read (offset/limit)",
      nudged("s5______", {"file_path": VAULT, "offset": 40, "limit": 30}), False)

seed("s6______", "fix the typo in the wind design doc")
check("edit-intent prompt", nudged("s6______", {"file_path": VAULT}), False)

# Repeated path reads remain silent without needing a dedupe state write.
seed("s7______", "summarize the wind system design")
first = nudged("s7______", {"file_path": VAULT})
second = nudged("s7______", {"file_path": VAULT})
check("repeat: 1st stays silent", first, False)
check("repeat: 2nd stays silent", second, False)

# --- Incidental cue substrings do not matter because path does not route ---
for i, (prompt, label) in enumerate([
    ("explain the wind design doc, the Godot editor rebuilt it", "editor"),
    ("dispatch agents over the wind design doc", "dispatch"),
    ("summarize the attuned design doc", "attuned"),
    ("explain updates in the wind design doc", "updates"),
]):
    sid = f"o{i}______"
    seed(sid, prompt)
    check(f"path-only silence: {label}", nudged(sid, {"file_path": VAULT}), False)

# --- An edit-shaped prompt (no edit verb) leaves path reads silent and the Grep rule intact ---
seed("b1______", "please address this now using /instruction_quality")
check("edit-shaped prompt: path read stays silent", nudged("b1______", {"file_path": VAULT}), False)
check("edit-shaped prompt: Grep rule unaffected",
      nudged("b1______", {"pattern": "SpellFactory", "glob": "*.cs"}, "Grep"), True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
