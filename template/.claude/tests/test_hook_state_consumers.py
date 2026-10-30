"""Proof that named state-writing hooks use hooks/_hook_state.py's shared locked update, and that no hook
still writes the retired `edit_seen_this_turn` state. test_hook_state.py proves the module itself; this
file names coding-layer hooks, so it ships at that layer.

    python3 .claude/tests/test_hook_state_consumers.py
"""
import io
import os
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
failures = 0


def check(label, ok):
    global failures
    print(("ok   " if ok else "FAIL ") + label)
    failures += not ok


def source(consumer):
    return io.open(os.path.join(HOOKS, consumer), encoding="utf-8").read()


for consumer, label in (
    ("tool_routing_cumulative_reset.py", "turn reset uses the shared locked update"),
    ("prompt_memory_loader.py", "memory reminder state uses the shared locked update"),
):
    check(label, "update_json_locked(" in source(consumer))

# `edit_seen_this_turn` lost its only reader when the cumulative nudge retired.
for consumer in ("harness_edit_skill_reminder.py", "tool_routing_cumulative_reset.py"):
    check(consumer + " writes no retired edit_seen_this_turn state", "edit_seen_this_turn" not in source(consumer))

print("all ok" if not failures else "%d failure(s)" % failures)
sys.exit(int(bool(failures)))
