"""Restore a durable job's brief and current amendment without clipping either.

Both recovery routes use this renderer. Short text is inline; oversized or unreadable sources require full
reads before work. Amendment receipts are deliberately not consulted: a prior context's receipt is not retention.
The caller's entire message, including print's trailing newline, must fit the client's hook-text cap.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from amend_delivery import HOOK_TEXT_CAP


def restore_brief(brief, before="", after=""):
    """Return the complete recovery message; `before` and `after` hold route-specific state, not brief text."""
    path = Path(brief).resolve()
    amend = Path(str(path) + ".amend.md")
    sources = []
    for name, source in (("Original brief", path), ("Current amendment", amend)):
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            if source == amend:
                continue
            text = None
        except OSError:
            text = None
        if source == amend and text is not None and not text.strip():
            continue
        revision = hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else "unreadable"
        sources.append((name, source.as_posix(), text, revision))

    head = ("----- EFFECTIVE BRIEF -----\n"
            "The current amendment overrides the original brief where they differ. Your report names what you applied.\n")
    tail = ("----- END EFFECTIVE BRIEF -----\n"
            "Resume from on-disk progress: Read every output file the effective brief names before redoing work. "
            "Finish and return what the effective brief requires, to the same paths.\n")
    inline = head
    for name, source, text, revision in sources:
        inline += f"{name}: {source} (sha256 {revision})\n"
        inline += f"----- {name.upper()} -----\n{text}\n"
    inline += tail
    if all(text is not None for _, _, text, _ in sources) and len(before + inline + after) + 1 <= HOOK_TEXT_CAP:
        return before + inline + after

    required = head + ("Read these sources in order, in full, before doing any work. "
                       "If a read is paginated or truncated, continue in bounded reads until every line is read. "
                       "If a tool truncates within a line, use a reader that returns that line in bounded character "
                       "chunks. Do not act on a preview.\n")
    for name, source, _, revision in sources:
        status = "is unreadable; re-read it yourself" if revision == "unreadable" else f"sha256 {revision}"
        required += f"{name}: {status}\nRead in full: {json.dumps(source, ensure_ascii=False)}\n"
    return before + required + tail + after
