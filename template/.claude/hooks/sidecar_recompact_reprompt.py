"""SessionStart(compact) — re-inject a sidecar child's original brief after auto-compaction.

Measured 2026-09-03 (`pr_ready` data-reach on Luna, session b7bdc978): after the compaction
summary arrived, the child answered the SUMMARY as if it were the task ("the latest instruction
requires text only"), wrote nothing, and ended its turn. The summary carries state; nothing
re-supplied the intent. This hook does: the launcher exports the `-f` prompt path as
`CLAUDE_CODE_SIDECAR_PROMPT_FILE`, and every compaction re-prints that brief verbatim behind a
resume line. Silent (exit 0, no output) when the env var is absent, so the driving session and
non-sidecar children never see it. Canon: reference/sidecar_dispatch.md §Compaction.
"""
import json
import os
import sys

MAX_BYTES = 160_000  # a brief past this is re-injected head-first with a pointer to the rest


def main() -> int:
    path = os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip()
    if not path:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
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
          "exactly what the brief's OUTPUT section asks for.\n")
    print("----- ORIGINAL BRIEF (re-injected after compaction) -----")
    print(brief)
    print(clipped + "----- END BRIEF -----")
    return 0


if __name__ == "__main__":
    sys.exit(main())
