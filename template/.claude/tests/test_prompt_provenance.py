#!/usr/bin/env python3
"""Focused proof for runtime-envelope filtering in hooks/_prompt_provenance.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
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
        ("empty and non-string values are not user intent",
         not is_user_intent_prompt("") and not is_user_intent_prompt(None)),
    ]
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
