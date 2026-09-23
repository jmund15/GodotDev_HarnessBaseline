#!/usr/bin/env python3
"""Re-runnable proof for hooks/routing_classifier.py — the one home of every routing verdict
both `tool_routing_nudge.py` (PreToolUse advisory) and `routing_audit.py` (log) consume.

Drives `classify_call` in-process. The subagent exemption on the native-read rule lives HERE so
the nudge and the audit row cannot disagree; the other cases pin the rules around it.

    python3 .claude/tests/test_routing_classifier.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from routing_classifier import classify_call, is_harness_path  # noqa: E402

READ_PATH = (
    "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/"
    "Design/some_design_doc.md"
)
READ = {"file_path": READ_PATH}
FOCUSED = "Read this one file for the exact paragraph that defines the existing contract."
DERIVED = (
    "Compare these modules, judge the architecture, and recommend which design should remain."
)
BULK_COPYABLE = (
    "Extract the same raw fields from every file and return one copyable entry per input path."
)


def main():
    cases = []

    c = classify_call("Read", READ, "")
    cases.append(("path shape alone does not infer worker routing",
                  c.severity == "compliant" and c.rule is None))

    c = classify_call("Read", READ, FOCUSED)
    cases.append(("focused source Read is compliant even on a design-shaped path",
                  c.severity == "compliant" and c.rule is None))

    c = classify_call("Read", READ, DERIVED)
    cases.append(("derived judgment is not automatically routed to a copyable-I/O worker",
                  c.severity == "compliant" and c.rule is None))

    c = classify_call("Read", READ, BULK_COPYABLE)
    cases.append(("explicit bulk-copyable extraction makes direct Read nudge-warranted",
                  c.severity == "nudge-warranted" and c.rule == "native-read-bulk-copyable"))

    for extension in (".json", ".yaml", ".py", ".cs", ".tres"):
        c = classify_call("Read", {"file_path": "C:/repo/input" + extension}, BULK_COPYABLE)
        cases.append(("bulk-copyable Read routes regardless of %s file type" % extension,
                      c.severity == "nudge-warranted"
                      and c.rule == "native-read-bulk-copyable"))

    # Owner decision R4 (2026-09-14): a bare "audit" is not a literal-scan request.
    grep_cs = {"pattern": "SpellFactory", "glob": "*.cs"}
    c = classify_call("Grep", grep_cs, "Audit SpellFactory for bugs")
    cases.append(("a bare audit request keeps the C# navigation advice",
                  c.severity == "nudge-warranted"))
    c = classify_call("Grep", grep_cs, "Run a documentation audit of SpellFactory mentions")
    cases.append(("documentation audit stays a literal-intent cue", c.severity == "cue-exempt"))

    bulk_then_edit = (
        "Extract the same raw fields from every file into one copyable entry per input path, "
        "then update the report with those rows."
    )
    c = classify_call("Read", READ, bulk_then_edit)
    cases.append(("an unrelated edit cue does not erase positive bulk-copyable evidence",
                  c.severity == "nudge-warranted" and c.rule == "native-read-bulk-copyable"))

    c = classify_call("Read", READ, BULK_COPYABLE, agent_id="agent0001")
    cases.append(("native subagent is exempt from the same bulk-copyable direct-Read nudge",
                  c.severity == "cue-exempt" and c.rule == "native-read-bulk-copyable"
                  and "[subagent: bundling delegate]" in (c.reason or "")))

    c = classify_call("Read", {"file_path": "C:/repo/.claude/Design/rule.md"}, BULK_COPYABLE)
    cases.append(("agent-runtime instructions remain direct-read only",
                  c.severity == "not-routable"))
    cases.append(("routing shares case-insensitive Windows harness classification",
                  is_harness_path(r"C:\\repo\\.CLAUDE\\worktrees\\SYNC-PR110\\.CLAUDE\\Hooks\\Rule.md")))
    cases.append(("routing excludes a worktree project path after dot-segment escape",
                  not is_harness_path("C:/repo/.claude/worktrees/sync-pr110/./.claude/./hooks/../../Spells/Fire.cs")))

    c = classify_call("Read", dict(READ, limit=40), BULK_COPYABLE)
    cases.append(("a bounded verification Read remains direct",
                  c.severity == "not-routable"))

    obsidian = {"target": {"path": "Claude/Design/some_design_doc.md"}}
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, FOCUSED)
    cases.append(("focused Obsidian read is not classified from its path",
                  c.severity == "compliant" and c.rule is None))
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, BULK_COPYABLE)
    cases.append(("explicit bulk-copyable Obsidian read remains a positive control",
                  c.severity == "nudge-warranted" and c.rule == "obsidian-read-bulk-copyable"))
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, bulk_then_edit)
    cases.append(("an edit cue does not erase positive bulk-copyable Obsidian evidence",
                  c.severity == "nudge-warranted" and c.rule == "obsidian-read-bulk-copyable"))

    c = classify_call("Grep", {"pattern": "DomainCore", "glob": "*.cs"}, "", agent_id="agent0001")
    cases.append(("subagent bare-PascalCase Grep on .cs: still nudge-warranted (agent_id is read-only scope)",
                  c.severity == "nudge-warranted" and c.rule == "pascal-grep-on-cs"))

    # --- vault doc writes: a CENSUS row, never a miss ---------------------------------
    # Measured 2026-09-08: one model wrote a 40KB review straight into the vault with zero
    # deliberation while another routed everything through write_doc; the split was only
    # visible by hand-counting transcripts. Both routes now log at the census tier.
    vault_doc = ("C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/"
                 "Claude/BrainstormingDesigns/x/Review.md")
    c = classify_call("Write", {"file_path": vault_doc, "content": "x" * 4000}, "")
    cases.append(("doc-sized direct Write into the vault logs as census/vault-write-direct",
                  c.severity == "census" and c.rule == "vault-write-direct"))
    c = classify_call("Write", {"file_path": vault_doc, "content": "x" * 200}, "")
    cases.append(("a small vault Write (touch-up) is not routable",
                  c.severity == "not-routable"))
    c = classify_call("Write", {"file_path": "C:/repo/.claude/scratch/spill.md", "content": "x" * 9000}, "")
    cases.append(("a large Write OUTSIDE the vault is not routable",
                  c.severity == "not-routable"))
    c = classify_call("mcp__ai-worker__write_doc", {"doc_path": vault_doc, "spec": "..."}, "")
    cases.append(("write_doc into the vault logs as census/vault-write-worker",
                  c.severity == "census" and c.rule == "vault-write-worker"))
    c = classify_call("mcp__ai-worker__write_doc", {"doc_path": "C:/repo/README.md", "spec": "..."}, "")
    cases.append(("write_doc outside the vault is not routable",
                  c.severity == "not-routable"))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
