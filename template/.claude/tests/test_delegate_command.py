#!/usr/bin/env python3
"""Structural proof for commands/delegate.md.

This proves required text branches exist and planted forbidden shortcuts are detected. It does
not prove that Claude follows the route at runtime; the plan's live smoke supplies that evidence.

    python3 .claude/tests/test_delegate_command.py
"""
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / ".claude" / "commands" / "delegate.md"

REQUIRED = {
    "one-line command description": r"^---\n(?:[^\n]+\n)*description: [^\n]+\n(?:[^\n]+\n)*---$",
    "inline task form": r"(?:/delegate )?<task>`? creates one `single` job",
    "job-file form": r"(?:/delegate )?--jobs <path>`? reads",
    "proceed preflight": r"`PROCEED`:.*scope.*inputs.*pins.*currency",
    "hold preflight": r"`HOLD`:.*required.*missing",
    "abort preflight": r"`ABORT`:.*authorization.*forbids",
    "route and guard shape stay separate": r"route[\s\S]+single\|parallel\|chain\|review[\s\S]+shape[\s\S]+any\|survey\|review\|author",
    "native executor owners": r"dispatch\.js[\s\S]+dispatch_chains\.js[\s\S]+review_fanout\.js",
    "cross-transport owner": r"alias[\s\S]+promptFile[\s\S]+sidecar_fanout\.py",
    "specialized command refusal": r"own coverage contract[\s\S]+exploration, plan drafting or plan review",
    "one arm per delivery job": r"One suitable arm per independently needed job, including review lenses",
    "provider availability cannot add jobs": r"Adding an available provider does not add jobs",
    "comparison is explicit and bounded": r"explicit comparison request[\s\S]+named arms, frozen inputs and a finite budget",
    "manifest finalization": r"--manifest-seed[\s\S]+--manifest-out[\s\S]+--session",
    "outcomes recorded before manifest": r"orchestration_verdicts\.json[\s\S]+manifest",
    "exact label count": r"seed count is the required result count",
    "review mapping retains agentType": r"Native review[^\n]+agents:[^\n]+agentType",
    "review manifest label matches workflow": r"review:<key>",
}

FORBIDDEN = {
    "bare Agent exact-pin shortcut": r"Agent\s*\(\s*\{",
    "Workflow cross-transport shortcut": r"Workflow\s*\(\s*\{[\s\S]{0,240}?transport\s*:",
    "automatic provider multiplication": r"Add (?:one Anthropic arm|every other available roster model)",
}


def missing(text):
    return [name for name, pattern in REQUIRED.items()
            if not re.search(pattern, text, re.MULTILINE)]


def violations(text):
    return [name for name, pattern in FORBIDDEN.items()
            if re.search(pattern, text, re.MULTILINE)]


def main():
    cases = []
    cases.append(("presence checks fail against an empty command",
                  len(missing("")) == len(REQUIRED), str(missing(""))))

    planted = {
        "bare Agent exact-pin shortcut": "Agent({model: 'x', prompt: 'do it'})",
        "Workflow cross-transport shortcut": "Workflow({args: {transport: 'anthropic'}})",
        "automatic provider multiplication": "Add every other available roster model that claims the fan-out role.",
    }
    for expected, text in planted.items():
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            temp = Path(fh.name)
        found = violations(temp.read_text(encoding="utf-8"))
        temp.unlink(missing_ok=True)
        cases.append((f"planted violation fires: {expected}", expected in found, str(found)))

    if not COMMAND.exists():
        cases.append(("delegate command exists", False, str(COMMAND)))
    else:
        text = COMMAND.read_text(encoding="utf-8")
        missing_real = missing(text)
        violations_real = violations(text)
        cases.append(("all required branches are present", not missing_real, str(missing_real)))
        cases.append(("no prohibited shortcut is present", not violations_real, str(violations_real)))
        for name, pattern in REQUIRED.items():
            mutant = re.sub(pattern, '', text, flags=re.MULTILINE)
            cases.append((f"removing contract is detected: {name}", name in missing(mutant), name))

    passed = failed = 0
    for label, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + label)
        if ok:
            passed += 1
        else:
            failed += 1
            print("     " + detail)
    print(f"delegate command: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
