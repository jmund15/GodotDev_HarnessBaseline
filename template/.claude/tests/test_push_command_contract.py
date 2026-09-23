#!/usr/bin/env python3
"""Structural proof for commands/commit_push.md and commands/clean_push.md.

Both push commands run the opt-in baseline drift gate (`check --strict`) in a step before their push
step, allow the `python3` call that gate makes, and stop on an unknown argument before any Git command.
Each rule is also proven to fire on a planted mutant. It does not prove runtime behavior.

    python3 .claude/tests/test_push_command_contract.py
"""
from pathlib import Path
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lock_rows import project_owned  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = [ROOT / ".claude" / "commands" / name for name in ("commit_push.md", "clean_push.md")]

PUSH_STEP = re.compile(r"[Pp]ush (?:all commits )?to the current branch on origin")
UNKNOWN_ARG = re.compile(r"\s+".join("Any other argument: report it and stop before the first Git command".split()))
GATE_CALL = "baseline_sync.py check --strict"


def steps(text):
    task = text.split("## Your task", 1)[-1]
    return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^(\d+)\. (.*)$", task, re.MULTILINE)]


def gate_and_push(text):
    numbered = steps(text)
    gate = next((n for n, body in numbered if "Baseline drift gate" in body), None)
    push = next((n for n, body in numbered if PUSH_STEP.search(body)), None)
    return gate, push


def problems(text):
    found = []
    front = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    tools = re.search(r"^allowed-tools: (.*)$", front.group(1), re.MULTILINE) if front else None
    if not tools or "Bash(python3:*)" not in tools.group(1):
        found.append("allowed-tools lacks Bash(python3:*)")
    if not UNKNOWN_ARG.search(text):
        found.append("an unknown argument does not stop before the first Git command")
    gate, push = gate_and_push(text)
    if gate is None:
        found.append("no baseline drift gate step")
    if push is None:
        found.append("no push step")
    if gate is not None and push is not None and gate >= push:
        found.append("baseline gate step %d is not before push step %d" % (gate, push))
    if GATE_CALL not in text:
        found.append("the gate does not run check --strict")
    return found


def mutants(text):
    gate, push = gate_and_push(text)
    late_gate = text
    if gate is not None and push is not None:
        late_gate = re.sub(r"^%d\. (\*\*Baseline drift gate)" % gate, r"%d. \1" % (push + 1), text, flags=re.MULTILINE)
    return {
        "gate moved after push": late_gate,
        "python3 dropped from allowed-tools": text.replace(", Bash(python3:*)", ""),
        "unknown-argument stop removed": UNKNOWN_ARG.sub("Any other argument is ignored", text),
        "gate runs a non-strict check": text.replace(GATE_CALL, "baseline_sync.py check"),
    }


def main():
    cases = [("every rule fails against an empty command", len(problems("")) >= 4, str(problems("")))]
    for command in COMMANDS:
        name = command.name
        if not command.exists():
            cases.append(("%s exists" % name, False, str(command)))
            continue
        text = command.read_text(encoding="utf-8")
        real = problems(text)
        cases.append(("%s meets the push contract" % name, not real, str(real)))
        for label, mutant in mutants(text).items():
            new = [p for p in problems(mutant) if p not in real]
            cases.append(("%s: planted mutant detected: %s" % (name, label), mutant != text and bool(new), str(new)))

    overnight = (ROOT / '.claude/commands/overnight.md').read_text(encoding='utf-8')
    park = overnight.split('## Step 2', 1)[-1].split('## Step 3', 1)[0].lower()
    cases.extend([
        ('overnight authorizes active-branch push including main',
         'active branch' in overnight.lower() and 'including `main`' in overnight.lower(), ''),
        ('overnight no longer parks main push', 'push to main' not in park, park),
    ])
    if project_owned('.claude/commands/session_end.md'):
        session_end = (ROOT / '.claude/commands/session_end.md').read_text(encoding='utf-8')
        cases.append(('session_end is the push authority for the active branch, main included',
                      'push authority for the active branch, `main` included' in session_end, ''))

    passed = failed = 0
    for label, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + label)
        if ok:
            passed += 1
        else:
            failed += 1
            print("     " + detail)
    print(f"push command contract: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
