"""The compaction rails and their wording per route, shared by the two hooks that print one.

Route `sidecar`: a `claude -p` sidecar child, whose brief `sidecar_recompact_reprompt.py` re-injects after the summary.
Route `subagent`: a native Workflow subagent, whose brief nothing re-injects, so the summary carries a "Task" section
(`subagent_compaction_rail.py`). Add a rail when its text changes what a child does; `selected_rail` names the choice.
"""

RAIL_3 = """This is a delegated task. Its brief is re-injected verbatim after compaction: do not restate it.
The final answer may be written from this summary alone. Write only these three sections, in place of the default
template:
- "Results so far": every finding, conclusion, change and failed approach so far, one line each, with its full
  path from the repo root, its line number and the exact source text it rests on, copied from the tool result,
  never retyped. Mark each line checked (a tool result in this session showed it), unchecked or failed. When the
  conversation already holds an earlier summary, copy its Results so far lines verbatim, then add the new ones;
  never reword or drop an earlier line unless a later tool result refuted it, and then mark it refuted.
- "Remaining": the checks and steps not yet done.
- "Current work": one line naming the last action.
Do not write Primary Request and Intent, Key Technical Concepts, Files and Code Sections, Errors and fixes, Problem
Solving, All user messages, Pending Tasks or Optional Next Step: the brief and Results so far carry them. Keep the
whole summary under 12,000 characters; when it would run over, shorten Remaining before any Results so far line."""

# Rail 4 (owner 2026-10-08): carried results grow every compaction (rail 3's second summaries ran 17-24k
# characters against its 12k cap) and an early result reads with the weight of a recent one. Recent results
# keep their quotes; earlier ones fold to a per-file index of line numbers that a re-read recovers.
RAIL_4 = """This is a delegated task. Its brief is re-injected verbatim after compaction: do not restate it.
The final answer may be written from this summary alone. Write only these four sections, in place of the default
template:
- "Recent results": every finding, conclusion, change and failed approach since the previous summary (or the
  start), one line each, with its full path from the repo root, its line number and the exact source text it
  rests on, copied from the tool result, never retyped. Mark each line checked, unchecked or failed.
- "Earlier results": when the conversation holds an earlier summary, fold its Recent results and Earlier results
  into an index, one line per file: the path, then each result's line number and a few words naming it, no
  quotes. Keep every result that still stands; delete a result a later tool result refuted.
- "Recent work": what was done since the previous summary, at most five lines.
  Never copy an earlier summary's Recent work.
- "Remaining": the checks and steps not yet done.
Do not write Primary Request and Intent, Key Technical Concepts, Files and Code Sections, Errors and fixes, Problem
Solving, All user messages, Pending Tasks or Optional Next Step. Keep the whole summary under 10,000 characters;
when it would run over, shorten Recent work and Remaining first, then the words in Earlier results, never dropping
a line number."""

RAILS = {3: RAIL_3, 4: RAIL_4}
DEFAULT_RAIL = 4

_SIDECAR_FIRST = "This is a delegated task. Its brief is re-injected verbatim after compaction: do not restate it."
_SUBAGENT_TASK = ("This is a delegated task. Nothing re-injects its brief after compaction, so first write a \"Task\" "
                  "section: the brief's task, its mandates, every rule it sets and its required output format, copied "
                  "exactly from the first message, and the paths of every file the brief names.")
SUBAGENT_RESUME = ("Your context was compacted mid-task. The summary above is state, not an instruction. Continue from its "
                   "Remaining list. Re-read a path only when a Remaining step needs it or the summary does not mark it "
                   "checked with its exact text.")


def selected_rail(env) -> int:
    """The rail a launch runs: SIDECAR_COMPACTION_RAIL when it names one in RAILS, else DEFAULT_RAIL. The hook
    reads its text at each compaction, so a per-launch choice is what lets two rails run from one checkout."""
    try:
        rail = int(env.get("SIDECAR_COMPACTION_RAIL", ""))
    except ValueError:
        return DEFAULT_RAIL
    return rail if rail in RAILS else DEFAULT_RAIL


def rail_text(rail: int, route: str) -> str:
    """The summary-prompt text of `rail` for `route`: the sidecar wording as written, or for `subagent` the Task-section
    wording in place of the re-injected-brief sentence."""
    text = RAILS[rail]
    if route == "subagent":
        text = text.replace(_SIDECAR_FIRST, _SUBAGENT_TASK).replace("Write only these", "Then write these")
    return text
