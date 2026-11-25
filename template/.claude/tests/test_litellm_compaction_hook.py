"""Proof: lib/litellm_compaction_hook.py marks only a compaction request, with the effort and budget it was given."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import litellm_compaction_hook as hook  # noqa: E402

COMPACT = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": [{"type": "text", "text":
                         "Your task is to create a detailed summary of the conversation so far, paying close attention"}]}]}
ORDINARY = {"messages": [{"role": "user", "content": "read the file"}]}
QUOTED_EARLIER = {"messages": [{"role": "user", "content": "create a detailed summary of the conversation"},
                               {"role": "assistant", "content": "done"}, {"role": "user", "content": "now fix the bug"}]}

fails = 0


def check(got, want, label):
    global fails
    if got != want:
        fails += 1
        print("FAIL %s: got %r want %r" % (label, got, want))
    else:
        print("PASS %s" % label)


check(hook.compaction_overrides(COMPACT, "low", "2048"),
      {"chat_template_kwargs": {"reasoning_effort": "low"}, "thinking_budget_tokens": 2048},
      "a compaction request takes the effort and the budget")
check(hook.compaction_overrides(COMPACT, "low", ""), {"chat_template_kwargs": {"reasoning_effort": "low"}},
      "an unset budget adds no budget")
check(hook.compaction_overrides(ORDINARY, "low", "2048"), None, "an ordinary request is unchanged")
check(hook.compaction_overrides(QUOTED_EARLIER, "low", "2048"), None,
      "only the newest user turn decides, so an earlier compaction turn does not mark the next request")
check(hook.compaction_overrides(COMPACT, "", ""), None, "no effort and no budget adds nothing")
print("test_litellm_compaction_hook: %d failed" % fails)
sys.exit(1 if fails else 0)
