#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — one-line size/density report for harness markdown.

Scope: `.claude/**/*.md` outside auto-memory/, scratch/, tests/, worktrees/ — `.py`/`.sh`/`.js`
harness comments carry the same doctrine (CLAUDE.md's Harness file edits rule) but are NOT
scanned; a silent run on a code file is not a clean verdict, it is out of scope. Reports bytes,
delta vs `git cat-file -s HEAD:<path>` (0 for a new file), and bytes per rule-unit (bullet,
numbered step, table row, bold-lead rule). Emits only when something is worth knowing: growth
>GROW_BYTES or >GROW_PCT, density >DENSITY_AUDIT B/unit, or size >SPLIT_BYTES. Numbers only —
the rule is `instruction_quality` §5. Never blocks.

Cadence: one line per file per turn. A drive rewrites the same file many times and the
measurement does not change between them, so a repeat is pure noise. A threshold the file
had not crossed before re-arms it within the same turn. The turn key is the uuid of the latest
row `_starts_turn` accepts: an owner prompt, including one after a leading injection, or a command
with arguments. Repeated text is still two turns; sidechain, runtime-envelope, injection-only and
empty-args command rows start none (`_owner_text` owns the table).

Fail-open: any error exits 0 silently. Wired in: settings.json hooks.PostToolUse "Write|Edit".
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import state_path, update_json_locked
from _owner_text import classify

GROW_BYTES = 1500
GROW_PCT = 10.0
DENSITY_AUDIT = 350      # B per rule-unit; ideal ≤ ~300
PER_UNIT_MAX = 700       # a single unit past this is a standalone offender even if the
                         # file-wide average and per-edit delta both stay clean (a table row
                         # bloated across several small commits, each under GROW_BYTES/GROW_PCT)
PER_CELL_MAX = 120       # a >=4-cell table row is judged per-cell instead: the cap is
                         # max(PER_UNIT_MAX, cells * PER_CELL_MAX), so narrow rows keep the flat cap.
SPLIT_BYTES = 32_000     # detail layers move to supporting files past this
# plans/ and generated/ are execution/derived docs, not loaded doctrine — density rules don't bind them.
EXCLUDED = ("auto-memory", "scratch", "tests", "worktrees", "__pycache__", "cache", "benchmark_runs",
            "plans", "generated")
UNIT = re.compile(r"^\s*(?:[-*] |\d+\. |\|(?!\s*-)|\*\*[^*]+\*\*)")
# Verdict-vs-evidence litmus (instruction_quality §5), content-shaped: a unit matching this is
# very likely inlined measurement narrative rather than a verdict, regardless of its byte count.
# NO bare section-number pattern here (`§\d`) — a doctrine cross-reference like `orchestration §5`
# is normal, encouraged style everywhere in this harness and would false-positive constantly
# (measured 2026-09-04: this file's own line 3 tripped a `§\d` draft of this check).
NARRATIVE = re.compile(r"\b20\d\d-\d\d-\d\d\b|\bn\s*=\s*\d|\bmeasured\b|\bobserved\b|scratch/",
                        re.IGNORECASE)
BACKTICKED = re.compile(r"`[^`]*`")


def rel_claude_path(file_path, root):
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, file_path))
    try:
        if os.path.normcase(os.path.commonpath((root, path))) != os.path.normcase(root):
            return None
        rel = os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return None
    return rel if rel.startswith(".claude/") else None


def _unit_cap(line):
    s = line.strip()
    if s.startswith("|"):
        cells = s.count("|") - 1
        if cells >= 4:
            return max(PER_UNIT_MAX, cells * PER_CELL_MAX)
    return PER_UNIT_MAX


def head_size(root, rel):
    try:
        out = subprocess.run(["git", "cat-file", "-s", f"HEAD:{rel}"],
                             cwd=root, capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except Exception:
        return 0


SEEN_FIELD = "harness_growth_seen"
SEEN_CAP = 50  # bounded state — oldest files drop off
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024


def _latest_turn_key(transcript_path):
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_BYTES:
                fh.seek(size - MAX_TRANSCRIPT_BYTES)
            content = fh.read()
        if size > MAX_TRANSCRIPT_BYTES and b"\n" in content:
            content = content.split(b"\n", 1)[1]
    except OSError:
        return None

    lines = content.splitlines()
    for number in range(len(lines), 0, -1):
        raw_line = lines[number - 1]
        try:
            entry = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        row = classify(entry, number, raw=raw_line)
        if _starts_turn(row):
            return row.uuid
    return None


def _starts_turn(row):
    """An owner prompt or a command with arguments; the `_owner_text` table's harness_growth_guard column."""
    return bool(row is not None and not row.meta and not row.sidechain and not row.tool_results
                and row.text and row.envelope is None
                and not (row.kind == "command" and not row.command_args))


def _claim_flags(session_id, rel, flags, transcript_path=None):
    """Return the flags in `flags` this turn has not already reported for `rel`.

    Best-effort and fail-open toward reporting: unreadable transcript or state costs a
    repeated advisory, never a silent one.
    """
    turn = _latest_turn_key(transcript_path)
    if turn is None:
        return flags
    def mark(state):
        seen = state.get(SEEN_FIELD)
        if not isinstance(seen, dict) or seen.get("turn") != turn:
            seen = {"turn": turn, "files": {}}
        files = seen.get("files")
        if not isinstance(files, dict):
            files = {}
        already = files.get(rel)
        already = set(already) if isinstance(already, list) else set()
        new = [flag for flag in flags if flag not in already]
        if new:
            files[rel] = sorted(already | set(flags))
            seen["files"] = dict(list(files.items())[-SEEN_CAP:])
            state[SEEN_FIELD] = seen
        return new

    written, new = update_json_locked(state_path(session_id), mark)
    return new if written else flags


def main():
    data = json.load(sys.stdin)
    result = process(data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": result["context"]}}))


def process(data):
    """Dispatcher entry: `{"context": measurement}` the first time this turn a file
    crosses a threshold, else None. `main()` keeps the standalone channel."""
    if data.get("tool_name") not in ("Write", "Edit"):
        return None
    fp = (data.get("tool_input") or {}).get("file_path") or ""
    root = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    rel = rel_claude_path(fp, root)
    if not rel or not rel.endswith(".md"):
        return None
    parts = rel.split("/")
    if any(seg in EXCLUDED for seg in parts[1:-1]):
        return None
    with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    now = len(text.encode("utf-8"))
    unit_lines = [ln for ln in text.splitlines() if UNIT.match(ln)]
    units = len(unit_lines) or 1
    density = now // units
    sized = [(ln, len(ln.encode("utf-8")), _unit_cap(ln)) for ln in unit_lines]
    worst, worst_bytes, worst_cap = max(sized, key=lambda t: t[1] - t[2], default=("", 0, PER_UNIT_MAX))
    # Strip backtick-quoted spans before matching: a bare-name citation like `fable51-2026-09-01`
    # or `scratch/pin_ab/parity.jsonl` is doctrine's OWN prescribed form ("cited by name") and must
    # not itself trip the check meant to catch narrative living OUTSIDE a citation, in plain prose.
    narrative_hit = next((ln for ln in unit_lines if NARRATIVE.search(BACKTICKED.sub("", ln))), None)
    base = head_size(root, rel)
    delta = now - base
    pct = (delta / base * 100.0) if base else 100.0
    grew = delta > GROW_BYTES or (base and pct > GROW_PCT)
    dense = density > DENSITY_AUDIT
    split = now > SPLIT_BYTES
    outlier = worst_bytes > worst_cap
    # Narrative content is the actual doctrine violation regardless of byte count — a short
    # dated/n=/scratch-path sentence is still evidence that belongs in Obsidian, cited by name.
    if not (grew or dense or split or outlier or narrative_hit):
        return None
    # Name each crossed threshold so the cadence gate can tell a repeat measurement from a
    # newly crossed cap: the keys are what is claimed, the strings are what is reported.
    crossed = {}
    if grew:
        crossed["grew"] = ""
    if dense:
        crossed["dense"] = f"density {density} B/unit > {DENSITY_AUDIT} audit trigger"
    if split:
        crossed["split"] = f"> {SPLIT_BYTES // 1000}KB split trigger"
    if outlier:
        crossed["outlier"] = (f'1 unit at {worst_bytes} B > {worst_cap} per-unit cap: '
                              f'"{worst.strip()[:60]}…"')
    if narrative_hit:
        crossed["narrative"] = (f'narrative-shaped unit (date/n=/measured/observed/scratch-path) '
                                f'— cite by name, don\'t inline: "{narrative_hit.strip()[:60]}…"')

    if not _claim_flags(
        data.get("session_id") or "",
        rel,
        sorted(crossed),
        data.get("transcript_path"),
    ):
        return None

    flags = [text for text in crossed.values() if text]
    tail = (" — " + "; ".join(flags)) if flags else ""
    return {"context": (f"[harness-growth] {rel}: {now:,} B ({delta:+,} vs HEAD, {pct:+.0f}%), "
                        f"{units} rule-units, {density} B/unit{tail} (instruction_quality §5).")}


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
