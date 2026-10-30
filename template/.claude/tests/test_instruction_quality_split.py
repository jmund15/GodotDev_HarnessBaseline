#!/usr/bin/env python3
"""Proof that the instruction_quality skill stays under the client's re-attach cap by file class.

SKILL.md keeps §1-§6b plus one pointer line per moved section; each moved section lives whole in one
`reference/` file. The consumers that must read a moved section (plan-check lens preload, session-audit
lens, the harness-edit hook) name the file for its class.

    python3 .claude/tests/test_instruction_quality_split.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.normpath(os.path.join(HERE, ".."))
SKILL_DIR = os.path.join(CLAUDE, "skills", "instruction_quality")
STATE = tempfile.mkdtemp(prefix="iq_split_")
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))

import _hook_state  # noqa: E402
import harness_edit_skill_reminder as hesr  # noqa: E402
import skill_load_marker  # noqa: E402
import lens  # noqa: E402

REFS = {
    "reference/skills_and_commands.md": ["7", "8", "9", "10", "11"],
    "reference/orchestration.md": ["12"],
    "reference/hooks.md": ["13", "14", "15", "16"],
    "reference/claims_and_wording.md": ["17", "18"],
}
KEPT = ["1", "2", "3", "3a", "3b", "4", "5", "6", "6b"]
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, detail))


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8", newline="") as fh:
        return fh.read()


def section(text, number):
    """The `### <number>.` block of `text`: heading line through the line before the next heading."""
    m = re.search(r"(?m)^### %s\. .*\n(?:(?!#{2,3} ).*\n?)*" % re.escape(number), text)
    return m.group(0) if m else ""


def main():
    skill = read(SKILL_DIR, "SKILL.md")
    check("SKILL.md fits the client's re-attach cap (%d chars)" % _hook_state.SKILL_REATTACH_CHARS,
          len(skill) <= _hook_state.SKILL_REATTACH_CHARS, len(skill))
    check("SKILL.md still carries the cap-bound sections whole",
          all(section(skill, n).count("\n") >= 1 for n in KEPT), [n for n in KEPT if not section(skill, n)])

    all_numbers = KEPT + [n for nums in REFS.values() for n in nums]
    headings = re.findall(r"(?m)^### (\d+[ab]?)\. ", skill)
    check("SKILL.md keeps one heading per section number, none renumbered",
          sorted(headings, key=lambda s: (int(re.match(r"\d+", s).group()), s))
          == sorted(all_numbers, key=lambda s: (int(re.match(r"\d+", s).group()), s)), headings)
    check("the 3b anchor a citer links still resolves",
          "### 3b. A fix fits its cause" in skill)

    for ref, numbers in REFS.items():
        path = os.path.join(SKILL_DIR, *ref.split("/"))
        exists = os.path.isfile(path)
        body = read(path) if exists else ""
        check("%s exists and SKILL.md links it" % ref, exists and ("(%s)" % ref) in skill, ref)
        for n in numbers:
            moved = section(body, n)
            check("§%s lives whole in %s" % (n, ref), moved.count("\n") >= 1 and moved.rstrip("\n").count("\n") >= 1, n)
            pointer = section(skill, n)
            check("§%s keeps a one-line pointer in SKILL.md" % n,
                  pointer.count("\n") <= 3 and ("(%s)" % ref) in pointer and len(pointer) < 400,
                  (n, len(pointer)))

    declared = {l.key: getattr(l, "preloads", None) for l in lens.parse("plan_check")[1]}
    want = ["skills/instruction_quality/SKILL.md"] + ["skills/instruction_quality/" + r for r in REFS]
    check("the plan-check instruction-quality lens preloads SKILL.md and every moved file",
          sorted(declared.get("plc-instruction-quality") or []) == sorted(want), declared.get("plc-instruction-quality"))

    audit = read(CLAUDE, "commands", "agents", "session_audit_agents.md")
    start = audit.index("You are sa-harness-quality")
    seat = audit[start:start + 600]
    check("the session-audit harness-quality lens names every moved file in its read-first line",
          all(os.path.basename(r) in seat for r in REFS), seat[:300])

    def deny_for(tail, session):
        out = hesr.process({"tool_name": "Edit", "session_id": session,
                            "tool_input": {"file_path": "C:/repo/.claude/" + tail}})
        return (out or {}).get("deny", "")

    # Which class files apply is judged from the edit's content (instruction_quality §3), so every deny
    # names all four with their read-when lines, whatever the path.
    every = [os.path.basename(r) for r in REFS]
    tails = ["hooks/some_guard.py", "commands/some_command.md", "skills/orchestration/reference/x.md",
             "reference/some_ref.md", "rules/some_rule.md"]
    for i, tail in enumerate(tails):
        reason = deny_for(tail, "iqs-deny-%d" % i)
        named = [n for n in every if "`%s`" % n in reason]
        check("the unloaded-skill deny for %s names all four class files" % tail,
              "Read `skills/instruction_quality/SKILL.md` in full, no offset or limit" in reason
              and sorted(named) == sorted(every), (named, reason[-300:]))

    session = "iqs-loaded"
    skill_load_marker.process({"tool_name": "Skill", "session_id": session,
                               "tool_input": {"skill": "instruction_quality"}})
    out = hesr.process({"tool_name": "Edit", "session_id": session,
                        "tool_input": {"file_path": "C:/repo/.claude/hooks/some_guard.py"}}) or {}
    check("the once-per-compaction reminder names the class files after the skill loads",
          all("`%s`" % n in out.get("context", "") for n in every), out)

    print("\n%d failed" % len(FAILURES) if FAILURES else "\nall passed")
    for f in FAILURES:
        print("  FAIL", f)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
