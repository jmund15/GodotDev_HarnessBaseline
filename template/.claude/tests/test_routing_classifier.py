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
from routing_classifier import classify_call  # noqa: E402

SYNTHESIS_PATH = (
    "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/"
    "Design/some_design_doc.md"
)
READ = {"file_path": SYNTHESIS_PATH}


def main():
    cases = []

    c = classify_call("Read", READ, "")
    cases.append(("main loop, no cue: native read of a synthesis doc is nudge-warranted",
                  c.severity == "nudge-warranted" and c.rule == "native-read-synthesis-doc"))

    c = classify_call("Read", READ, "", agent_id="agent0001")
    cases.append(("subagent: the same read is cue-exempt, tagged as the bundling delegate",
                  c.severity == "cue-exempt" and c.rule == "native-read-synthesis-doc"
                  and "[subagent: bundling delegate]" in (c.reason or "")))

    c = classify_call("Read", READ, "audit this design doc against the spec")
    cases.append(("main loop with an audit cue: cue-exempt by the prompt, not by agent_id",
                  c.severity == "cue-exempt" and "[subagent" not in (c.reason or "")))

    c = classify_call("Read", {"file_path": "C:/repo/README.md"}, "", agent_id="agent0001")
    cases.append(("subagent reading a non-synthesis .md: compliant, exemption never fires",
                  c.severity == "compliant"))

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
