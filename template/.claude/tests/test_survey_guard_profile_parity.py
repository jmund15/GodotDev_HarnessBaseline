#!/usr/bin/env python3
"""Proof: a delegate's rails name the LSP tool only when its kind's profile grants it.

Every kind's rails are composed by tools/guard_text.py from guards/common*.md plus its own guard's files. A
kind whose `builtins` list (guards/tool_profiles.json) lacks LSP cannot call it, so a rail telling it to
anchor with LSP, or to avoid LSP under a concurrent dispatch, is an instruction it cannot follow. A kind
with `builtins: null` runs the full tool set and may be told about LSP.
"""
import os
import re
import sys

TOOLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, TOOLS)

import guard_text  # noqa: E402
import tool_profiles  # noqa: E402

LSP = re.compile(r"\bLSP\b")


def main():
    failures = []
    kinds = tool_profiles.load_table()["kinds"]
    checked = 0
    for kind, row in sorted(kinds.items()):
        if not row.get("guard"):
            continue
        # A kind whose guard file this checkout does not hold is a consumer's own kind (the benchmark `arm`).
        if not os.path.isfile(os.path.join(guard_text._GUARDS, row["guard"] + ".md")):
            continue
        builtins = row.get("builtins")
        granted = builtins is None or "LSP" in builtins
        for tier in guard_text.VALID_TIERS:
            if tier == "none":
                continue
            text = guard_text.guard_text(kind, tier)
            checked += 1
            if not granted:
                hits = [line.strip()[:120] for line in text.splitlines() if LSP.search(line)]
                if hits:
                    failures.append("%s/%s names LSP, which its profile lacks: %s" % (kind, tier, hits))
    if checked == 0:
        failures.append("no kind with a guard was checked")
    for failure in failures:
        print("FAIL " + failure)
    print("test_survey_guard_profile_parity: %d checked, %d failed" % (checked, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
