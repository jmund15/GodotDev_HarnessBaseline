#!/usr/bin/env python3
"""Output-cap proof for hooks/tres_script_strip_guard.py: a mass strip stays under HOOK_TEXT_CAP.

A planted repo commits .tres files with a script binding, then strips it from each. The guard's
report (worktree scan and the commit-deny hook) must hold the verdict, the instruction and the count
line, with the combined output at most HOOK_TEXT_CAP bytes. A small strip prints every row and no
count line.

Both `.tres` guards render through one `_hook_parts.bounded_decision`, so this proof imports the
output-contract cases from its nullstrip twin and supplies only the plant and the instruction token.

    python3 .claude/tests/test_tres_script_strip_guard_output.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tres_script_strip_guard.py")
sys.path.insert(0, HERE)
from test_tres_nullstrip_guard_output import Guard, git, run_guard_cases  # noqa: E402

BINDING = 'script = ExtResource("1_a")'
BODY = (
    '[gd_resource type="Resource" format=3]\n\n'
    '[ext_resource type="Script" path="res://Scripts/cfg.cs" id="1_a"]\n\n'
    "[resource]\n" + BINDING + "\nValue = 1\n"
)


def planted_repo(root, count):
    """A repo whose HEAD holds `count` .tres bindings, with every binding stripped in the worktree."""
    repo = os.path.join(root, "repo")
    os.makedirs(os.path.join(repo, "Data"))
    git(repo, "init", "-q")
    for i in range(count):
        with open(os.path.join(repo, "Data", "f%04d.tres" % i), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(BODY)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    for i in range(count):
        path = os.path.join(repo, "Data", "f%04d.tres" % i)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(BINDING + "\n", ""))
    return repo


GUARD = Guard(HOOK, "tres_script_strip_guard", planted_repo, "repair-inplace",
              lambda line: line.startswith("  Data/f"), "stdout",
              absent="Data/f0199.tres", list_prefix="tres_script_strip_findings.")


if __name__ == "__main__":
    sys.exit(run_guard_cases(GUARD, "tres_script_output_"))
