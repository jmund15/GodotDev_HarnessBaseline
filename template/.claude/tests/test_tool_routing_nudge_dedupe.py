"""Re-runnable proof for hooks/tool_routing_nudge.py's repeat-suppression (B5).

The PascalCase-Grep advisory reads identically for every pattern in one target FAMILY, so it
is delivered once per session per family. Read/Obsidian path-shape advisories are retired:
path is not evidence that the caller needs bulk copyable I/O.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_tool_routing_nudge_dedupe.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "tool_routing_nudge.py")
sys.path.insert(0, HOOKS)
from _hook_state import clear_compaction_keys  # noqa: E402

SID = "trn00001"


def call(tool_name, tool_input, env, session=SID):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"tool_name": tool_name, "session_id": session,
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=60, env=env)
    out = (r.stdout or "").strip()
    if not out:
        return ""
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")


def grep(pattern, env, glob="*.cs", session=SID):
    return call("Grep", {"pattern": pattern, "glob": glob}, env, session)


def plant_prompt(state_dir, session, prompt):
    with open(os.path.join(state_dir, session[:8] + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": prompt}, fh)


def main():
    tmp = tempfile.mkdtemp(prefix="trnstate_")
    os.environ["HARNESS_HOOK_STATE_DIR"] = tmp
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    first = grep("SpellInstance", env)
    cases.append(("the first bare-PascalCase Grep on .cs nudges", "tool-routing" in first))

    second = grep("TagTier", env)
    cases.append(("a different symbol in the same family does not re-nudge", second == ""))

    other = grep("TagTier", env, glob="*.tres")
    cases.append(("a different target family still nudges once",
                  "semantic-search" in other))

    cases.append(("that family is then suppressed too",
                  grep("AreaTemplate", env, glob="*.tres") == ""))

    clear_compaction_keys(SID)
    cases.append(("compaction re-arms advice that left the model context",
                  "tool-routing" in grep("AbilityBuilder", env)))

    cases.append(("a fresh session starts clean",
                  "tool-routing" in grep("SpellInstance", env, session="trn00002")))

    # Negatives — dedupe must not swallow a rule that never fired, or widen the trigger.
    cases.append(("an anchored Grep never nudges",
                  grep("class SpellInstance", env, session="trn00003") == ""))
    cases.append(("a regex pattern never nudges",
                  grep("Spell(Instance|Effect)", env, session="trn00004") == ""))
    cases.append(("a synthesis-shaped Read path alone never nudges",
                  call("Read", {"file_path": "C:/vault/Design/x.md"},
                       env, session="trn00005") == ""))
    cases.append(("a second synthesis-shaped Read also stays silent",
                  call("Read", {"file_path": "C:/vault/Architecture/y.md"},
                       env, session="trn00005") == ""))
    cases.append(("an Obsidian path alone never nudges",
                  call("mcp__obsidian__obsidian_get_note",
                       {"target": {"path": "Claude/Design/x.md"}},
                       env, session="trn00006") == ""))

    focused = "Read this one design file for the exact paragraph that defines the contract."
    plant_prompt(tmp, "trn00007", focused)
    cases.append(("a focused Read stays silent despite a design-shaped path",
                  call("Read", {"file_path": "C:/vault/Design/x.md"},
                       env, session="trn00007") == ""))

    derived = "Compare these modules, judge the architecture, and recommend which design should remain."
    plant_prompt(tmp, "trn00008", derived)
    cases.append(("derived judgment is not nudged toward a copyable-I/O worker",
                  call("Read", {"file_path": "C:/vault/Architecture/x.md"},
                       env, session="trn00008") == ""))

    bulk = "Extract the same raw fields from every file and return one copyable entry per input path."
    plant_prompt(tmp, "trn00009", bulk)
    bulk_msg = call("Read", {"file_path": "C:/vault/Design/x.md"},
                    env, session="trn00009")
    cases.append(("explicit bulk-copyable extraction gets a Read advisory",
                  "bulk copyable" in bulk_msg.lower() and "read_files" in bulk_msg))

    plant_prompt(tmp, "trn00010", bulk)
    # Standalone hook payload with agent_id exercises the same native-subagent seam
    # used by the registered pre-read dispatcher. No main-loop call pre-consumes dedupe state.
    payload = {"tool_name": "Read", "session_id": "trn00010", "agent_id": "agent0001",
               "tool_input": {"file_path": "C:/vault/Design/x.md"}}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=60, env=env)
    cases.append(("native subagent receives no bulk-copyable Read advisory",
                  not r.stdout.strip() and r.returncode == 0))

    plant_prompt(tmp, "trn00011", bulk)
    obsidian_msg = call("mcp__obsidian__obsidian_get_note",
                        {"target": {"path": "Claude/Design/x.md"}},
                        env, session="trn00011")
    cases.append(("explicit bulk-copyable Obsidian read gets read_files advice",
                  "bulk copyable" in obsidian_msg.lower() and "read_files" in obsidian_msg))

    verified_unique = "The SpellFactory name is verified unique; locate its authored resource use."
    plant_prompt(tmp, "trn00012", verified_unique)
    cases.append(("verified-unique cue still exempts a C# symbol Grep",
                  grep("SpellFactory", env, glob="*.cs", session="trn00012") == ""))
    cases.append(("verified-unique cue does not exempt an indexed resource Grep",
                  "semantic-search" in grep("SpellFactory", env, glob="*.tres",
                                            session="trn00012")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
