#!/usr/bin/env python3
"""Proof for hooks/critical_analysis_reminder.py.

The hook reminds once per session on a suggestion-shaped prompt, and on every prompt that questions a decision
or result already taken (a challenge), with its own text and no session flag. Its fired flag lives in the shared
routing state file (`_hook_state.state_path`, which honors HARNESS_HOOK_STATE_DIR) and is set under the
shared lock, so a field another hook wrote survives. Each case runs the real hook as a process; a
traceback or an exit outside {0} is a CRASH, never a pass.

    python3 .claude/tests/test_critical_analysis_reminder.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "critical_analysis_reminder.py")
SID = "cafe1234-0000-4000-8000-criticalproof"
FLAG = "critical_analysis_session_fired"


def run(state_dir, prompt):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({"session_id": SID, "prompt": prompt}),
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    return r.stdout or "", r.returncode != 0 or "Traceback" in (r.stderr or "")


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    cases = []
    with tempfile.TemporaryDirectory() as state_dir:
        path = os.path.join(state_dir, SID[:8] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_prompt": "sibling field"}, fh)

        out, crashed = run(state_dir, "I think we should split this hook, right?")
        cases.append(("a suggestion-shaped prompt gets the reminder", not crashed and "Critically analyze" in out))
        state = read(path)
        cases.append(("the fired flag lands in the shared state file", state.get(FLAG) is True))
        cases.append(("a field another hook wrote survives the update", state.get("last_prompt") == "sibling field"))

        out, crashed = run(state_dir, "What about a second suggestion?")
        cases.append(("the reminder fires once per session", not crashed and "Critically analyze" not in out))

    with tempfile.TemporaryDirectory() as list_dir:
        with open(os.path.join(list_dir, SID[:8] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(["not", "an", "object"], fh)
        out, crashed = run(list_dir, "I think we should split this hook, right?")
        cases.append(("a non-object state file does not crash the hook",
                      not crashed and "Critically analyze" in out))

    with tempfile.TemporaryDirectory() as fresh_dir:
        out, crashed = run(fresh_dir, "yes")
        cases.append(("a bare acknowledgement is not a suggestion", not crashed and "Critically analyze" not in out))
        out, crashed = run(fresh_dir, "not json")
        cases.append(("a plain prompt without a suggestion shape stays silent",
                      not crashed and "Critically analyze" not in out))

    challenges = ["fyi what made you select deepseek api over go usage?",
                  "don't believe this, this was probably wrongly extrapolated from few benchmark runs no?",
                  "isn't that wasteful, four executors for four small items",
                  "that's a fatal completely unacceptable error no?",
                  "are you sure that holds for the Go plan?",
                  "why did you run it on the hot plan"]
    quiet = ["why is the build failing", "please split this hook into two files", "what made the cache expire",
             "yes", "/codify why did you pick that",
             "Another Claude session sent a message: <agent-message from=\"a1\"> you missed the failing test </agent-message>",
             "<task-notification> the run was wasteful </task-notification>"]
    with tempfile.TemporaryDirectory() as ch_dir:
        for prompt in challenges:
            out, crashed = run(ch_dir, prompt)
            cases.append(("a challenge gets the evidence reminder: " + prompt[:48],
                          not crashed and "Challenge-shape prompt" in out and "run the check" in out))
        cases.append(("a challenge does not set the suggestion flag",
                      read(os.path.join(ch_dir, SID[:8] + ".json")).get(FLAG) is not True))
        out, crashed = run(ch_dir, "I think we should split this hook, right?")
        cases.append(("a suggestion after challenges still gets its reminder",
                      not crashed and "Critically analyze" in out))
        out, crashed = run(ch_dir, "are you sure about that?")
        cases.append(("a challenge still fires after the suggestion reminder fired",
                      not crashed and "Challenge-shape prompt" in out))
        out, crashed = run(ch_dir, '<pasted_content id="1">the report was wasteful and you missed it</pasted_content id="1">\nplease continue')
        cases.append(("words inside pasted content are not the owner's challenge",
                      not crashed and "Challenge-shape prompt" not in out))
        out, crashed = run(ch_dir, '<pasted_content id="1">report</pasted_content id="1">\nwhy did you pick that row?')
        cases.append(("a challenge written beside pasted content still fires",
                      not crashed and "Challenge-shape prompt" in out))
        for prompt in quiet:
            out, crashed = run(ch_dir, prompt)
            cases.append(("no challenge reminder for: " + prompt[:48], not crashed and "Challenge-shape prompt" not in out))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
