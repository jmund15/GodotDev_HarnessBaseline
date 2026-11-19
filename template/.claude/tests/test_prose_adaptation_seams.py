#!/usr/bin/env python3
"""Re-runnable proof for the 8 `adaptation.json` prose seams: each seam file names `adaptation.json` and
its own key, so the reader loads project values at run time. A shared seam file also holds no string value
of the real `adaptation.json[key]` (a hardcoded project value would drift from the file); a `forked` or
`local` file belongs to this project and may name its own values.

    python3 .claude/tests/test_prose_adaptation_seams.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
from _lock_rows import project_owned  # noqa: E402

ADAPTATION = os.path.join(CLAUDE_DIR, "skills", "project_subsystems", "adaptation.json")

# file (relative to CLAUDE_DIR) -> (the adaptation.json key it names, the layer that ships the file).
SEAMS = {
    "commands/clean_pull.md": ("paired_repos", "pure"),
    "commands/clean_push.md": ("paired_repos", "pure"),
    "commands/commit_push.md": ("paired_repos", "pure"),
    "commands/create_pr.md": ("paired_repos", "pure"),
    "commands/doc_start_here_update.md": ("content_domains", "pure"),
    "commands/agents/pr_test_checklist_conventions.md": ("content_scopes", "godot"),
    "skills/architecture_philosophy/structure_rules.md": ("structure_exceptions", "coding"),
    "commands/agents/pr_classification.md": ("pr_domains", "coding"),
}


def strings_of(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_of(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_of(v)]
    return []


def seam_errors(rel, text, key, values):
    errors = []
    if "adaptation.json" not in text:
        errors.append("%s does not name adaptation.json" % rel)
    if key not in text:
        errors.append("%s does not name its key %s" % (rel, key))
    # A value counts as a whole word: a short one (`UI`) inside a longer word (`BUILD`) is not that value, and a
    # consuming project's own values meet this check on prose it never wrote.
    errors += ["%s hardcodes the %s value %r" % (rel, key, v) for v in values
               if v and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(v), text)]
    return errors


def main():
    with open(ADAPTATION, encoding="utf-8") as fh:
        adaptation = json.load(fh)
    cases = []
    for rel, (key, layer) in SEAMS.items():
        if layer != "pure" and not os.path.exists(os.path.join(CLAUDE_DIR, "CLAUDE.%s.md" % layer)):
            print("skip %s: the %s layer is not adopted here" % (rel, layer))
            continue
        path = os.path.join(CLAUDE_DIR, *rel.split("/"))
        if not os.path.exists(path):
            cases.append(("%s: exists in an adopted %s layer" % (rel, layer), False))
            continue
        text = open(path, encoding="utf-8").read()
        values = [] if project_owned(".claude/" + rel) else strings_of(adaptation.get(key))
        cases.append(("%s: names adaptation.json and %s, and a shared copy hardcodes no value of it" % (rel, key),
                      seam_errors(rel, text, key, values) == []))

    planted = seam_errors("x.md", "reads adaptation.json paired_repos", "paired_repos", ["Zq-planted"])
    cases.append(("a doc naming both tokens and no value is clean", planted == []))
    cases.append(("mutant: a doc without adaptation.json fails",
                  seam_errors("x.md", "paired_repos", "paired_repos", []) != []))
    cases.append(("mutant: a doc without its key fails",
                  seam_errors("x.md", "adaptation.json", "paired_repos", []) != []))
    cases.append(("mutant: a doc holding a value of the key fails",
                  seam_errors("x.md", "adaptation.json paired_repos Zq-planted", "paired_repos", ["Zq-planted"]) != []))
    cases.append(("a short value inside a longer word is not that value",
                  seam_errors("x.md", "adaptation.json paired_repos: BUILD the GUI", "paired_repos", ["UI"]) == []))
    cases.append(("mutant: the same short value as a whole word fails",
                  seam_errors("x.md", "adaptation.json paired_repos: the UI scope", "paired_repos", ["UI"]) != []))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
