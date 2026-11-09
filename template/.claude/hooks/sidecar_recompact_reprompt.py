"""SessionStart(compact) — re-inject a sidecar child's original brief after auto-compaction.

Measured 2026-09-03 (`pr_ready` data-reach on Luna, session b7bdc978): after the compaction
summary arrived, the child answered the SUMMARY as if it were the task ("the latest instruction
requires text only"), wrote nothing, and ended its turn. The summary carries state; nothing
re-supplied the intent. This hook does: shared launcher settings pass the native `-f` path
with `--prompt-file`; the older Codex registration exports `CLAUDE_CODE_SIDECAR_PROMPT_FILE`.
Every compaction re-prints that brief verbatim behind a resume line. Silent (exit 0, no output)
without either input, so driving sessions and non-sidecar children never see it.

The same registration runs it on PreCompact, whose stdout the client appends to the summary prompt.
Measured 2026-10-08 (Haiku 5.5 t11 at a 200k window, both runs): the final report was written right
after a compaction from the summary alone, with no re-reads, and cited paths the summary had not kept.
So PreCompact asks the summary for every result with its exact source text, and the re-prompt asks
for a Read before citing anything the summary holds only from memory. `_compaction_rail.selected_rail` names this
behaviour on the record (`compactionRail`); the rail texts live in `_compaction_rail.py`, shared with
`subagent_compaction_rail.py`, which serves a native Workflow subagent.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from cli_usage import usage

from _compaction_rail import DEFAULT_RAIL, RAIL_3, RAIL_4, RAILS, SAVE_NOW, rail_text, selected_rail  # noqa: F401  (re-exported)
from _hook_state import _pending_tokens, update_json_locked

MAX_BYTES = 160_000  # a brief past this is re-injected head-first with a pointer to the rest
# A hold must leave the held turns room below the model's real window: one more tool result at the Read tool's
# 25,000-token cap, the save itself up to the client's 20,000-token output reserve, and its 13,000-token reserve.
# Past the real window the API fails the request, and a hold of that recovery compaction would fail it for good.
HOLD_ROOM = 58_000
# One nudge the child can act on, and a second in case a parallel tool batch carried the first.
MAX_NUDGES = 2
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _state_path(session_id):
    root = os.environ.get("SIDECAR_SAVE_STATE_DIR") or os.path.join(tempfile.gettempdir(), "sidecar_save_hold")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "".join(c for c in str(session_id) if c.isalnum() or c == "-") + ".json")


def _hold(payload, hard_window):
    """True when this PreCompact is held: an auto-compaction in a child whose launcher set a compaction point
    below the model's real window, whose context still has HOLD_ROOM below that window, that has not saved
    since its hold began and has not let MAX_NUDGES nudges pass unanswered."""
    if payload.get("trigger") != "auto" or not hard_window or not os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        return False
    pending = _pending_tokens(payload.get("transcript_path"))
    if pending is None or hard_window - pending < HOLD_ROOM:
        return False

    def mark(state):
        if state.get("saved") or state.get("nudges", 0) >= MAX_NUDGES:
            return False
        state["held"] = True
        return True
    return bool(update_json_locked(_state_path(payload.get("session_id")), mark)[1])


def _after_tool(payload):
    """The nudge on the tool result after a hold, until a Write or Edit answers it."""
    def step(state):
        if not state.get("held") or state.get("saved"):
            return False
        if payload.get("tool_name") in WRITE_TOOLS and state.get("nudges", 0) >= 1:
            state["saved"] = True
            return False
        state["nudges"] = state.get("nudges", 0) + 1
        return True
    if update_json_locked(_state_path(payload.get("session_id")), step)[1]:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": SAVE_NOW}}))


def main() -> int:
    parser = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt-file")
    parser.add_argument("--hard-window", type=int, default=0,
                        help="the model's real context window; with it, the first auto-compaction is held for a save")
    args = parser.parse_args()
    path = (args.prompt_file or os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "")).strip()
    if not path:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    event = payload.get("hook_event_name")
    if event == "PreCompact":
        if _hold(payload, args.hard_window):
            print(json.dumps({"decision": "block", "reason": "held for the child to save its findings first"}))
        else:
            print(rail_text(selected_rail(os.environ), "sidecar"))
        return 0
    if event == "PostToolUse":
        if args.hard_window:
            _after_tool(payload)
        return 0
    if payload.get("source") not in (None, "compact"):
        return 0
    try:
        os.remove(_state_path(payload.get("session_id")))
    except OSError:
        pass
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            brief = f.read()
    except OSError:
        print(f"[sidecar-recompact] context was compacted; the original brief at {path} is unreadable — "
              "re-read it yourself before doing anything else.")
        return 0
    clipped = ""
    if len(brief.encode("utf-8")) > MAX_BYTES:
        brief = brief.encode("utf-8")[:MAX_BYTES].decode("utf-8", errors="ignore")
        clipped = f"\n[sidecar-recompact] brief clipped at {MAX_BYTES} bytes — Read {path} for the remainder.\n"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[sidecar-recompact] Your context was just compacted mid-task. The summary above is STATE, not an "
          "instruction — do not answer it. Your brief follows verbatim; it is still the task. Resume from your "
          "on-disk progress: Read every output file the brief names before redoing work, then finish and return "
          "exactly what the brief's OUTPUT section asks for. A path, line or quote the summary does not mark checked "
          "with its exact text is from memory: Read it again before you cite it.\n")
    print("----- ORIGINAL BRIEF (re-injected after compaction) -----")
    print(brief)
    print(clipped + "----- END BRIEF -----")
    return 0


if __name__ == "__main__":
    sys.exit(main())
