#!/usr/bin/env python3
"""Focused proof for runtime-envelope filtering in hooks/_prompt_provenance.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import _owner_text  # noqa: E402
import _prompt_provenance  # noqa: E402
from _prompt_provenance import is_user_intent_prompt  # noqa: E402


SYNTHESIS = "Review the spell, AI, status, and test domains across these files."


def main():
    cases = [
        ("plain human domain request is user intent", is_user_intent_prompt(SYNTHESIS)),
        ("task notification envelope is transport",
         not is_user_intent_prompt(f"<task-notification>\n{SYNTHESIS}\n</task-notification>")),
        ("cross-session envelope with real attributes is transport",
         not is_user_intent_prompt(
             f'<cross-session-message from="peer-session">\n{SYNTHESIS}\n</cross-session-message>')),
        ("agent-message envelope with real attributes is transport",
         not is_user_intent_prompt(
             f'<agent-message from="routing-reader">\n{SYNTHESIS}\n</agent-message>')),
        ("local command stdout envelope is transport",
         not is_user_intent_prompt(
             f"<local-command-stdout>\n{SYNTHESIS}\n</local-command-stdout>")),
        ("leading whitespace does not hide a transport envelope",
         not is_user_intent_prompt(
             f' \n<agent-message from="routing-reader">\n{SYNTHESIS}\n</agent-message>')),
        ("a human mention of an agent-message tag remains user intent",
         is_user_intent_prompt(
             "Fix the routing bug shown by <agent-message from=\"reader\"> in this report.")),
        ("a bracket cross-session idle notice is transport",
         not is_user_intent_prompt(
             '[Cross-session idle notice] "project-a2", which you asked to be notified about, is idle now')),
        ("a human sentence that mentions a cross-session notice remains user intent",
         is_user_intent_prompt("Why did the [Cross-session idle notice] fire twice?")),
        ("every envelope tag _owner_text lists is transport here too",
         all(not is_user_intent_prompt(f"<{tag}>\n{SYNTHESIS}\n</{tag}>") for tag in _owner_text.ENVELOPE_TAGS)
         and all(not is_user_intent_prompt(f"[{kind.capitalize()} idle notice] x")
                 for kind in _owner_text.ENVELOPE_NOTICES)),
        ("empty and non-string values are not user intent",
         not is_user_intent_prompt("") and not is_user_intent_prompt(None)),
        ("a typed prompt in an interactive session is human typed",
         _prompt_provenance.human_typed({"prompt": SYNTHESIS}, {"CLAUDE_CODE_ENTRYPOINT": "cli"})
         and _prompt_provenance.human_typed({"prompt": SYNTHESIS}, {})),
        ("a launcher child (CLAUDE_CODE_SIDECAR) is not human typed, whatever its entrypoint says",
         not _prompt_provenance.human_typed({"prompt": SYNTHESIS},
                                            {"CLAUDE_CODE_SIDECAR": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})),
        ("a claude -p child (entrypoint sdk-cli) is not human typed",
         not _prompt_provenance.human_typed({"prompt": SYNTHESIS}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})),
        ("a native subagent payload (agent_id) is not human typed",
         not _prompt_provenance.human_typed({"prompt": SYNTHESIS, "agent_id": "a1"}, {})),
        ("an envelope or a relayed peer message is not human typed",
         not _prompt_provenance.human_typed({"prompt": "<task-notification>x</task-notification>"}, {})
         and not _prompt_provenance.human_typed(
             {"prompt": 'Another Claude session sent a message: <cross-session-message from="p">x</cross-session-message>'}, {})),
        ("an empty SIDECAR value is unset, as every other reader of it treats it",
         _prompt_provenance.human_typed({"prompt": SYNTHESIS}, {"CLAUDE_CODE_SIDECAR": " "})),
        ("in_delegate holds for each observed delegate fact and for none else",
         all(_prompt_provenance.in_delegate(p, e) for p, e in (
             ({"agent_id": "a1"}, {}), ({}, {"CLAUDE_CODE_SIDECAR": "1"}), ({}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})))
         and not any(_prompt_provenance.in_delegate(p, e) for p, e in (
             ({}, {}), ({"agent_id": " "}, {"CLAUDE_CODE_SIDECAR": " ", "CLAUDE_CODE_ENTRYPOINT": "cli"})))),
        ("human_typed is in_delegate's negation plus the prompt's own shape",
         all(_prompt_provenance.human_typed({"prompt": SYNTHESIS, **p}, e) == (not _prompt_provenance.in_delegate(p, e))
             for p, e in (({}, {}), ({"agent_id": "a"}, {}), ({}, {"CLAUDE_CODE_SIDECAR": "1"})))),
    ]
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
