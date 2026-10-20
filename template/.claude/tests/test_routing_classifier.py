#!/usr/bin/env python3
"""Re-runnable proof for hooks/routing_classifier.py — the one home of every routing verdict
both `tool_routing_nudge.py` (PreToolUse advisory) and `routing_audit.py` (log) consume.

Drives `classify_call` in-process. The subagent exemption on the native-read rule lives HERE so
the nudge and the audit row cannot disagree; the other cases pin the rules around it.

    python3 .claude/tests/test_routing_classifier.py
"""
import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from routing_classifier import classify_call, is_harness_path  # noqa: E402


@contextlib.contextmanager
def _planted_profile(tier, ai_worker):
    """This machine's tier and ai-worker registration, planted for the block.

    Both are read LIVE by tools/workstation_profile.py, so the variables the module documents are
    the only lever: WORKSTATION_PROFILE_PATH for the tier, CLAUDE_CONFIG_DIR/SC_WORKDIR for the
    registration walk. `tier=None` plants no profile file at all.
    """
    root = tempfile.mkdtemp(prefix="rcprofile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    profile = os.path.join(root, "workstation_profile.json")
    if tier is not None:
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    planted = {"WORKSTATION_PROFILE_PATH": profile, "CLAUDE_CONFIG_DIR": cfg,
               "SC_WORKDIR": os.path.join(root, "nowhere")}
    saved = {k: os.environ.get(k) for k in planted}
    os.environ.update(planted)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

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

    # C7: the verdict's reason names the route the nudge will print, so it must name one this
    # machine has. The VERDICT is unchanged — the rule is the call shape, not the machine.
    with _planted_profile("full", "absent"):
        absent = classify_call("Read", READ, BULK_COPYABLE)
    cases.append(("an absent ai-worker renders the verdict's reason as the local route",
                  absent.severity == "nudge-warranted"
                  and absent.rule == "native-read-bulk-copyable"
                  and "mcp__ai-worker__" not in (absent.reason or "")
                  and "bounded Read" in (absent.reason or "")))
    with _planted_profile("full", "present"):
        present = classify_call("Read", READ, BULK_COPYABLE)
    cases.append(("a present ai-worker keeps the reason's worker route",
                  "mcp__ai-worker__read_files" in (present.reason or "")))
    with _planted_profile(None, "present"):
        unknown = classify_call("Read", READ, BULK_COPYABLE)
    cases.append(("no profile keeps the reason's worker route",
                  "mcp__ai-worker__read_files" in (unknown.reason or "")))

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
    # Reversed 2026-09-23: the obsidian MCP server is no longer registered in this project, so the
    # branch was dead wiring. The positive control is now the inverse — a call to the retired tool
    # is not routable at all, whatever the prompt says (path-only and focused included).
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, FOCUSED)
    cases.append(("the retired Obsidian read tool is not routable",
                  c.severity == "not-routable" and c.rule is None))
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, BULK_COPYABLE)
    cases.append(("a bulk-copyable prompt does not revive the retired Obsidian read branch",
                  c.severity == "not-routable" and c.rule is None))
    c = classify_call("mcp__obsidian__obsidian_get_note", obsidian, bulk_then_edit)
    cases.append(("an edit cue does not revive the retired Obsidian branch",
                  c.severity == "not-routable" and c.rule is None))
    c = classify_call("mcp__obsidian__obsidian_search_notes", {"maxMatchesPerHit": 50}, BULK_COPYABLE)
    cases.append(("the retired Obsidian search tool carries no broad-search advisory",
                  c.severity == "not-routable" and c.rule is None))

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
