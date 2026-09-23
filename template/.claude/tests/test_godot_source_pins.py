#!/usr/bin/env python3
"""Proof for hooks/godot_source_pins.py through routing_classifier's WebFetch rule.

    python3 .claude/tests/test_godot_source_pins.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))

import routing_classifier  # noqa: E402

CASES = [
    ("https://docs.godotengine.org/en/stable/classes/class_node.html",
     "nudge-warranted", "webfetch-godot-class-page-not-pinned"),
    ("https://docs.godotengine.org/en/stable/tutorials/scripting/index.html",
     "nudge-warranted", "webfetch-godot-stable-alias-unpinnable"),
    ("https://docs.godotengine.org/en/4.7/tutorials/scripting/index.html", "compliant", None),
    ("https://example.com/page", "compliant", None),
]


def main():
    fails = 0
    for url, severity, rule in CASES:
        got = routing_classifier.classify_call("WebFetch", {"url": url}, "")
        ok = (got.severity, got.rule) == (severity, rule)
        fails += not ok
        print("%-4s %s -> %s %s" % ("ok" if ok else "FAIL", url, got.severity, got.rule))
    print("\n%d/%d cases pass" % (len(CASES) - fails, len(CASES)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
