#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — one-line size/density report for harness markdown.

Scope: `.claude/**/*.md` outside auto-memory/, scratch/, tests/, worktrees/ — `.py`/`.sh`/`.js`
harness comments carry the same doctrine (CLAUDE.md's Harness file edits rule) but are NOT
scanned; silence on a code file is out of scope. Reports bytes and delta vs HEAD. Growth uses
the immediate pre-edit file; density, outlier and narrative checks use only added or lengthened
rule-units (bullet, numbered step, table row, bold-lead rule). Split reports only a crossing of
SPLIT_BYTES in this edit. Without a recoverable preimage, those checks stay silent. Sentences
present at HEAD and absent now keep the removal report below. Thresholds are review triggers,
not caps (`instruction_quality` §5). Never blocks.

Removed sentences: the file's sentences (split on `. `, `; ` and line ends) are compared with
`HEAD:<path>`. A sentence whose words mostly reappear in one sentence the edit added to the same
file prints `reworded`. A sentence another `.claude/**/*.md` outside EXCLUDED carries, found by an
8-word fragment, prints `moved → <path>`. Any other prints `removed`, and a report with a `removed`
line ends with the re-home line. The report speaks again whenever the removed set changed since this
file's last report in the turn; an unchanged set runs no home search.

Home search cost: the normalized corpus is cached in the hook state directory per file
(mtime_ns, size), so a turn of edits reads each unchanged file once. Reading stops at
HOME_SEARCH_SECONDS, and the report then says a `removed` sentence may live in an unread file.

Cadence: one line per file per turn. A drive rewrites the same file many times and the
measurement does not change between them, so a repeat is pure noise. A threshold the file
had not crossed before, or a removed-sentence set that changed, re-arms it within the same turn. Threshold
flags and the removed set's signature are claimed in separate fields. The turn key is the uuid of the latest
row `_starts_turn` accepts: an owner prompt, including one after a leading injection, or a command
with arguments. Repeated text is still two turns; sidechain, runtime-envelope, injection-only and
empty-args command rows start none (`_owner_text` owns the table).

Fail-open: any error exits 0 silently. Wired in: settings.json hooks.PostToolUse "Write|Edit".
"""

import collections
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import read_json_salvage, state_dir, state_path, update_json_locked
from _owner_text import classify

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import sentence_diff
from sentence_diff import sentences

GROW_BYTES = 1500
GROW_PCT = 10.0
DENSITY_AUDIT = 350      # B per rule-unit; ideal ≤ ~300
PER_UNIT_MAX = 700       # a single unit past this is a standalone review trigger even if the
                         # file-wide average and per-edit delta both stay clean (a table row
                         # bloated across several small commits, each under GROW_BYTES/GROW_PCT)
PER_CELL_MAX = 120       # a >=4-cell table row is judged per-cell instead: the trigger is
                         # max(PER_UNIT_MAX, cells * PER_CELL_MAX), so narrow rows keep the flat trigger.
SPLIT_BYTES = 32_000     # detail layers move to supporting files past this
EXCLUDED = sentence_diff.EXCLUDED
UNIT = re.compile(r"^\s*(?:[-*] |\d+\. |\|(?!\s*-)|\*\*[^*]+\*\*)")
# Verdict-vs-evidence litmus (instruction_quality §5), content-shaped: a unit matching this is
# very likely inlined measurement narrative rather than a verdict, regardless of its byte count.
# NO bare section-number pattern here (`§\d`) — a doctrine cross-reference like `orchestration §5`
# is normal, encouraged style everywhere in this harness and would false-positive constantly
# (measured 2026-09-04: this file's own line 3 tripped a `§\d` draft of this check).
NARRATIVE = re.compile(r"\b20\d\d-\d\d-\d\d\b|\bn\s*=\s*\d|\bmeasured\b|\bobserved\b|scratch/",
                        re.IGNORECASE)
BACKTICKED = re.compile(r"`[^`]*`")
REPORT_SENTENCES = 8
CLIP_CHARS = 140
# post_edit_dispatch stops a sub-hook at its 5 s SUBHOOK_TIMEOUT; the rest covers the measurement and git reads.
HOME_SEARCH_SECONDS = 2.0
CORPUS_FILE = "harness_growth_corpus.json"
RE_HOME = "A removed rule is re-homed, never deleted to fit a size threshold (instruction_quality §5)."


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


def pre_edit_text(data, text):
    """Recover the immediate preimage from the client's result, not from HEAD."""
    response = data.get("tool_response") or {}
    original = response.get("originalFile")
    if isinstance(original, str):
        return original
    if response.get("type") == "create":
        return ""
    hunks = response.get("structuredPatch")
    if isinstance(hunks, list) and hunks:
        lines = text.splitlines()
        for hunk in reversed(hunks):
            patch = hunk.get("lines") or []
            new = [line[1:] for line in patch if line.startswith((" ", "+"))]
            old = [line[1:] for line in patch if line.startswith((" ", "-"))]
            start = max(0, hunk["newStart"] - 1)
            if lines[start:start + len(new)] != new:
                return None
            lines[start:start + len(new)] = old
        return "\n".join(lines) + ("\n" if text.endswith("\n") and lines else "")
    if data.get("tool_name") == "Edit":
        inp = data.get("tool_input") or {}
        old, new = inp.get("old_string"), inp.get("new_string")
        if isinstance(old, str) and isinstance(new, str) and new:
            if inp.get("replace_all") or text.count(new) == 1:
                return text.replace(new, old, -1 if inp.get("replace_all") else 1)
    return None


def added_lines(before, text):
    """Net-new occurrences, so a rewrite that retains a bad old line does not report it."""
    remaining = collections.Counter(before.splitlines())
    added = []
    for line in text.splitlines():
        if remaining[line]:
            remaining[line] -= 1
        else:
            added.append(line)
    return added


def touched_units(before, text):
    old = [line for line in before.splitlines() if UNIT.match(line)]
    new = [line for line in text.splitlines() if UNIT.match(line)]
    touched = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "insert":
            touched.extend(new[c:d])
        elif tag == "replace":
            for offset, line in enumerate(new[c:d]):
                if a + offset >= b or len(line.encode("utf-8")) > len(old[a + offset].encode("utf-8")):
                    touched.append(line)
    return touched


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


def head_text(root, rel):
    """`HEAD:<rel>` as text, or None when git has no such blob."""
    out = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=5)
    return out.stdout if out.returncode == 0 else None


def removed_sentences(root, rel, text):
    """Sentences in `HEAD:<rel>` and absent from `text`, in file order, each paired with whether a
    sentence the edit added to `text` rewords it. Empty on any error: this report never blocks an edit."""
    try:
        head = head_text(root, rel)
        return sentence_diff.removed(head, text) if head else []
    except Exception:
        return []


def _corpus(root):
    """([(repo-relative path, whitespace-normalized text)] of every in-scope `.claude` markdown file,
    whether every file was read). An unchanged file comes from the CORPUS_FILE cache; reading changed
    or new files stops at HOME_SEARCH_SECONDS."""
    root = os.path.realpath(root)
    cache_path = os.path.join(state_dir(), CORPUS_FILE)
    cached = read_json_salvage(cache_path)
    files = cached.get("files") if cached.get("root") == root else None
    files = files if isinstance(files, dict) else {}
    deadline = time.monotonic() + HOME_SEARCH_SECONDS
    fresh, complete, changed = {}, True, False
    for directory, dirs, names in os.walk(os.path.join(root, ".claude")):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(n for n in names if n.endswith(".md")):
            path = os.path.join(directory, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            stat = os.stat(path)
            key = [stat.st_mtime_ns, stat.st_size]
            entry = files.get(rel)
            if isinstance(entry, list) and len(entry) == 3 and entry[:2] == key and isinstance(entry[2], str):
                fresh[rel] = entry
            elif time.monotonic() > deadline:
                complete = False
            else:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    fresh[rel] = key + [" ".join(fh.read().split())]
                changed = True
    if changed or fresh.keys() != files.keys():
        snapshot = {"root": root, "files": fresh}
        update_json_locked(cache_path, lambda state: (state.clear(), state.update(snapshot)))
    return [(rel, entry[2]) for rel, entry in fresh.items()], complete


def find_homes(root, rel, gone):
    """({sentence: the other in-scope markdown file carrying its fragment}, whether the corpus read finished)."""
    corpus, complete = _corpus(root)
    return sentence_diff.homes(gone, corpus, rel), complete


def _clip(sentence):
    return sentence if len(sentence) <= CLIP_CHARS else sentence[:CLIP_CHARS - 1] + "…"


def removal_report(root, rel, removed):
    """The removed-sentence lines: `removed` first, then `reworded`, then `moved → <path>`."""
    gone = [s for s, reworded in removed if not reworded]
    try:
        homes, complete = find_homes(root, rel, gone) if gone else ({}, True)
    except Exception:
        homes, complete = {}, False
    marks = [(s, "reworded" if reworded else (f"moved → {homes[s]}" if s in homes else "removed"))
             for s, reworded in removed]
    rank = {"removed": 0, "reworded": 1}
    ordered = sorted(marks, key=lambda m: rank.get(m[1], 2))
    lines = [f"[harness-growth] {rel}: {len(removed)} sentence(s) present at HEAD are absent now:"]
    for sentence, mark in ordered[:REPORT_SENTENCES]:
        lines.append(f'- {mark}: "{_clip(sentence)}"')
    if len(ordered) > REPORT_SENTENCES:
        lines.append(f"+{len(ordered) - REPORT_SENTENCES} more")
    if any(mark == "removed" for _, mark in marks):
        if not complete:
            lines.append(f"The home search stopped at its {HOME_SEARCH_SECONDS:g} s budget, so a sentence "
                         "marked removed may live in an unread file.")
        lines.append(RE_HOME)
    return "\n".join(lines)


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
                and row.kind != "queued_prompt"
                and not (row.kind == "command" and not row.command_args))


def _claim(session_id, rel, flags, removal, transcript_path=None):
    """Return (the flags in `flags` this turn has not reported for `rel`, whether `removal` is new).

    `removal` is the removed-sentence set's signature, or None. A threshold flag is claimed once per
    turn; a removal speaks again whenever its signature differs from the one last recorded.
    Best-effort and fail-open toward reporting: unreadable transcript or state costs a
    repeated advisory, never a silent one.
    """
    unclaimed = (flags, removal is not None)
    turn = _latest_turn_key(transcript_path)
    if turn is None:
        return unclaimed
    def mark(state):
        seen = state.get(SEEN_FIELD)
        if not isinstance(seen, dict) or seen.get("turn") != turn:
            seen = {"turn": turn, "files": {}}
        files = seen.get("files")
        if not isinstance(files, dict):
            files = {}
        record = files.get(rel)
        record = record if isinstance(record, dict) else {}
        already = record.get("flags")
        already = set(already) if isinstance(already, list) else set()
        new = [flag for flag in flags if flag not in already]
        removal_new = removal is not None and record.get("removed") != removal
        if new or record.get("removed") != removal:
            files[rel] = {"flags": sorted(already | set(flags)), "removed": removal}
            seen["files"] = dict(list(files.items())[-SEEN_CAP:])
            state[SEEN_FIELD] = seen
        return new, removal_new

    written, claimed = update_json_locked(state_path(session_id), mark)
    return claimed if written else unclaimed


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
    before = pre_edit_text(data, text)
    touched = touched_units(before, text) if before is not None else []
    touched_density = sum(len(line.encode("utf-8")) for line in touched) // (len(touched) or 1)
    sized = [(ln, len(ln.encode("utf-8")), _unit_cap(ln)) for ln in touched]
    worst, worst_bytes, worst_cap = max(sized, key=lambda t: t[1] - t[2], default=("", 0, PER_UNIT_MAX))
    # Strip backtick-quoted spans before matching: a bare-name citation like `fable51-2026-09-01`
    # or `scratch/pin_ab/parity.jsonl` is doctrine's OWN prescribed form ("cited by name") and must
    # not itself trip the check meant to catch narrative living OUTSIDE a citation, in plain prose.
    narrative_hit = next((ln for ln in touched if NARRATIVE.search(BACKTICKED.sub("", ln))), None)
    removed = removed_sentences(root, rel, text)
    base = head_size(root, rel)
    delta = now - base
    pct = (delta / base * 100.0) if base else 100.0
    prior_bytes = len(before.encode("utf-8")) if before is not None else now
    edit_delta = now - prior_bytes
    grew = edit_delta > GROW_BYTES or (prior_bytes and edit_delta / prior_bytes * 100.0 > GROW_PCT)
    dense = touched_density > DENSITY_AUDIT
    split = prior_bytes <= SPLIT_BYTES < now
    outlier = worst_bytes > worst_cap
    # Narrative content is the actual doctrine violation regardless of byte count — a short
    # dated/n=/scratch-path sentence is still evidence that belongs in Obsidian, cited by name.
    if not (grew or dense or split or outlier or narrative_hit or removed):
        return None
    # Name each crossed threshold so the cadence gate can tell a repeat measurement from a
    # newly crossed one: the keys are what is claimed, the strings are what is reported.
    crossed = {}
    if grew:
        crossed["grew"] = ""
    if dense:
        crossed["dense"] = f"touched-unit density {touched_density} B/unit > {DENSITY_AUDIT} audit trigger"
    if split:
        crossed["split"] = f"> {SPLIT_BYTES // 1000}KB split trigger"
    if outlier:
        crossed["outlier"] = (f'1 unit at {worst_bytes} B > {worst_cap} per-unit review trigger: '
                              f'"{worst.strip()[:60]}…"')
    if narrative_hit:
        crossed["narrative"] = (f'narrative-shaped unit (date/n=/measured/observed/scratch-path) '
                                f'— cite by name, don\'t inline: "{narrative_hit.strip()[:60]}…"')

    signature = hashlib.sha1("\n".join(s for s, _ in removed).encode("utf-8")).hexdigest()[:8] if removed else None

    new, removal_new = _claim(
        data.get("session_id") or "",
        rel,
        sorted(crossed),
        signature,
        data.get("transcript_path"),
    )
    if not new and not removal_new:
        return None

    parts = []
    if new:
        flags = [text for text in crossed.values() if text]
        tail = (" — " + "; ".join(flags)) if flags else ""
        parts.append(f"[harness-growth] {rel}: {now:,} B ({delta:+,} vs HEAD, {pct:+.0f}%), "
                     f"{units} rule-units, {density} B/unit{tail} (instruction_quality §5).")
    if removal_new:
        parts.append(removal_report(root, rel, removed))
    return {"context": "\n".join(parts)}


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
