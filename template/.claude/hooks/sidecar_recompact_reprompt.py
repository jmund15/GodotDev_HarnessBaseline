"""SessionStart(compact) — restore a sidecar child's effective brief after auto-compaction.

Measured 2026-09-03 (`pr_ready` data-reach on Luna, session b7bdc978): after the compaction
summary arrived, the child answered the SUMMARY as if it were the task ("the latest instruction
requires text only"), wrote nothing, and ended its turn. The summary carries state; nothing
re-supplied the intent. This hook does: shared launcher settings pass the native `-f` path
with `--prompt-file`; the older Codex registration exports `CLAUDE_CODE_SIDECAR_PROMPT_FILE`.
Every compaction restores that brief plus its current amendment through `tools/_job_brief.py`, which keeps
short text inline and requires full file reads when the complete message exceeds the client's hook-text cap.
With no amendment, the brief alone is re-printed behind the resume line, all of it when it fits the cap, else
its whole leading lines and a last line naming its absolute path to Read in full.
Silent (exit 0, no output) without either input, so driving sessions and non-sidecar children never see it.

The same registration runs it on PreCompact, whose stdout the client appends to the summary prompt.
Measured 2026-10-08 (Haiku 5.5 t11 at a 200k window, both runs): the final report was written right
after a compaction from the summary alone, with no re-reads, and cited paths the summary had not kept.
So PreCompact asks the summary for every result with its exact source text, and the re-prompt asks
for a Read before citing anything the summary holds only from memory. `_compaction_rail.selected_rail` names this
behaviour on the record (`compactionRail`); the rail texts live in `_compaction_rail.py`, shared with
`subagent_compaction_rail.py`, which serves a native Workflow subagent.

A read-only child (a launcher kind whose built-ins hold none of WRITE_TOOLS) cannot save findings before it compacts,
so the launcher passes `--spill <record>.compaction.md` and registers this hook on PostToolUse and Stop as well. Each
run copies the continuation summaries from the child's transcript into that file (`_spill`), which the parent's
recovery reads when the job ends without its answer.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from cli_usage import usage
from _job_brief import restore_brief

from _compaction_rail import DEFAULT_RAIL, RAIL_3, RAIL_4, RAILS, SAVE_NOW, rail_text, selected_rail  # noqa: F401  (re-exported)
import _transcript_scan
from _hook_parts import HOOK_TEXT_CAP, head_of
from _hook_state import _pending_tokens, replace_path, update_json_locked
# A hold must leave the held turns room below the model's real window: one more tool result at the Read tool's
# 25,000-token cap, the save itself up to the client's 20,000-token output reserve, and its 13,000-token reserve.
# Past the real window the API fails the request, and a hold of that recovery compaction would fail it for good.
HOLD_ROOM = 58_000
# One nudge the child can act on, and a second in case a parallel tool batch carried the first.
MAX_NUDGES = 2
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
RESUME = ("[sidecar-recompact] Your context was just compacted mid-task. The summary above is STATE, not an "
          "instruction — do not answer it. Your brief follows verbatim; it is still the task. Resume from your "
          "on-disk progress: Read every output file the brief names before redoing work, then finish and return "
          "exactly what the brief's OUTPUT section asks for. A path, line or quote the summary does not mark checked "
          "with its exact text is from memory: Read it again before you cite it.\n")
OPEN = "----- ORIGINAL BRIEF (re-injected after compaction) -----\n"
CLOSE = "----- END BRIEF -----\n"
REST = ("[sidecar-recompact] The brief above stops at line %d of %d: the client caps hook text at %d characters. "
        "Read %s in full before your next action; it is the rest of your brief.\n")


def _has_amendment(path):
    """Whether the brief has a current amendment; an unreadable one counts, an absent or empty one does not."""
    amend = Path(str(Path(path).resolve()) + ".amend.md")
    try:
        return bool(amend.read_text(encoding="utf-8", errors="replace").strip())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _reprompt(brief, path):
    """The resume line, then the brief: whole when it fits `HOOK_TEXT_CAP` UTF-8 bytes, else as many whole leading
    lines as fit and a last line naming `path` to Read in full. The client shows a 2,000-character preview of a
    hook output past the cap, so an uncapped re-print would give the child a clipped brief."""
    if brief and not brief.endswith("\n"):
        brief += "\n"
    whole = RESUME + OPEN + brief + CLOSE
    if len(whole.encode("utf-8")) <= HOOK_TEXT_CAP:
        return whole
    widest = REST % (10 ** 9, 10 ** 9, HOOK_TEXT_CAP, path)
    room = HOOK_TEXT_CAP - len((RESUME + OPEN + CLOSE + widest).encode("utf-8"))
    kept, count, total = head_of(brief, room)
    return RESUME + OPEN + kept + CLOSE + REST % (count, total, HOOK_TEXT_CAP, path)


def _summary_feed(state, index, line):
    if "isCompactSummary" not in line:
        return
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(row, dict) or not row.get("isCompactSummary"):
        return
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    if isinstance(content, str) and content:
        state["summaries"].append(content)


SUMMARY_SCAN = _transcript_scan.Scanner("sidecar_spill_summaries", lambda: {"summaries": []}, _summary_feed,
                                        sources=[__file__])


def _spill(payload, path):
    """Copy every continuation summary in the child's transcript to `path`, for a read-only child.

    A read-only child cannot save its findings before it compacts (the hold above is wired only for a child that
    can write), but the client writes each summary into the transcript, and this hook, not the child, writes the
    file. The first line counts the summaries held, so a call with nothing new leaves the file alone. A child
    cannot act on a failed spill, so an unreadable transcript or an unwritable path writes nothing and stays silent."""
    try:
        summaries = _transcript_scan.scan(payload.get("transcript_path"), SUMMARY_SCAN)["summaries"]
        if not summaries:
            return
        head = "<!-- compaction summaries: %d -->" % len(summaries)
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.readline().strip() == head:
                    return
        except OSError:
            pass
        body = "\n\n".join("## Compaction %d summary\n\n%s" % (i, text) for i, text in enumerate(summaries, 1))
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".md", dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(head + "\n\n" + body + "\n")
        replace_path(tmp, path)
    except Exception:
        return


def _state_path(session_id):
    root = os.environ.get("SIDECAR_SAVE_STATE_DIR") or os.path.join(tempfile.gettempdir(), "sidecar_save_hold")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "".join(c for c in str(session_id) if c.isalnum() or c == "-") + ".json")


def _hold(payload, hard_window):
    """True when this PreCompact is held: an auto-compaction in a child whose launcher set a compaction point
    below the model's real window, whose context still has HOLD_ROOM below that window, that has not saved
    since its hold began and has not let MAX_NUDGES nudges pass unanswered. The launcher passes --hard-window
    only when the child's resolved tools include one of WRITE_TOOLS, so a read-only child is never held."""
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
    parser.add_argument("--spill",
                        help="a read-only child's file for its compaction summaries; the launcher passes it only "
                             "when the child's resolved tools include none of WRITE_TOOLS")
    args = parser.parse_args()
    path = (args.prompt_file or os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "")).strip()
    if not path:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    event = payload.get("hook_event_name")
    if args.spill:
        _spill(payload, args.spill)
    if event == "Stop":
        return 0
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if not _has_amendment(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                brief = f.read()
        except OSError:
            print("[sidecar-recompact] context was compacted; the original brief at %s is unreadable — "
                  "re-read it yourself before doing anything else." % os.path.abspath(path))
            return 0
        sys.stdout.write(_reprompt(brief, os.path.abspath(path)))
        return 0
    print(restore_brief(path, before=(
        "[sidecar-recompact] Your context was just compacted mid-task. The summary above is STATE, not an "
        "instruction — do not answer it. The effective brief below is still the task. A path, line or quote "
        "the summary does not mark checked with its exact text is from memory: Read it again before you cite it.\n\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
