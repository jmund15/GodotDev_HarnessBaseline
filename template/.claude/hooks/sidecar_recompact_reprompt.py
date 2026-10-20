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

from _compaction_rail import DEFAULT_RAIL, RAIL_3, RAIL_4, RAILS, rail_text, selected_rail  # noqa: F401  (re-exported)

MAX_BYTES = 160_000  # a brief past this is re-injected head-first with a pointer to the rest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file")
    args = parser.parse_args()
    path = (args.prompt_file or os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "")).strip()
    if not path:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if payload.get("hook_event_name") == "PreCompact":
        print(rail_text(selected_rail(os.environ), "sidecar"))
        return 0
    if payload.get("source") not in (None, "compact"):
        return 0
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
