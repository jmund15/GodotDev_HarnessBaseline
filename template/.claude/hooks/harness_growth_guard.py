#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — one-line size/density report for harness markdown.

Scope: `.claude/**/*.md` outside auto-memory/, scratch/, tests/, worktrees/. Reports bytes,
delta vs `git cat-file -s HEAD:<path>` (0 for a new file), and bytes per rule-unit (bullet,
numbered step, table row, bold-lead rule). Emits only when something is worth knowing: growth
>GROW_BYTES or >GROW_PCT, density >DENSITY_AUDIT B/unit, or size >SPLIT_BYTES. Numbers only —
the rule is `instruction_quality` §5. Never blocks.

Fail-open: any error exits 0 silently. Wired in: settings.json hooks.PostToolUse "Write|Edit".
"""

import json
import os
import re
import subprocess
import sys

GROW_BYTES = 1500
GROW_PCT = 10.0
DENSITY_AUDIT = 350      # B per rule-unit; ideal ≤ ~300
SPLIT_BYTES = 32_000     # detail layers move to supporting files past this
# plans/ and generated/ are execution/derived docs, not loaded doctrine — density rules don't bind them.
EXCLUDED = ("auto-memory", "scratch", "tests", "worktrees", "__pycache__", "cache", "benchmark_runs",
            "plans", "generated")
UNIT = re.compile(r"^\s*(?:[-*] |\d+\. |\|(?!\s*-)|\*\*[^*]+\*\*)")


def rel_claude_path(file_path):
    norm = file_path.replace("\\", "/")
    if "/.claude/" in norm:
        return ".claude/" + norm.split("/.claude/")[-1]
    if norm.startswith(".claude/"):
        return norm
    return None


def head_size(root, rel):
    try:
        out = subprocess.run(["git", "cat-file", "-s", f"HEAD:{rel}"],
                             cwd=root, capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except Exception:
        return 0


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Write", "Edit"):
        return
    fp = (data.get("tool_input") or {}).get("file_path") or ""
    rel = rel_claude_path(fp)
    if not rel or not rel.endswith(".md"):
        return
    parts = rel.split("/")
    if any(seg in EXCLUDED for seg in parts[1:-1]):
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    now = len(text.encode("utf-8"))
    units = sum(1 for line in text.splitlines() if UNIT.match(line)) or 1
    density = now // units
    base = head_size(root, rel)
    delta = now - base
    pct = (delta / base * 100.0) if base else 100.0
    grew = delta > GROW_BYTES or (base and pct > GROW_PCT)
    dense = density > DENSITY_AUDIT
    split = now > SPLIT_BYTES
    if not (grew or dense or split):
        return
    flags = []
    if dense:
        flags.append(f"density {density} B/unit > {DENSITY_AUDIT} audit trigger")
    if split:
        flags.append(f"> {SPLIT_BYTES // 1000}KB split trigger")
    tail = (" — " + "; ".join(flags)) if flags else ""
    msg = (f"[harness-growth] {rel}: {now:,} B ({delta:+,} vs HEAD, {pct:+.0f}%), "
           f"{units} rule-units, {density} B/unit{tail} (instruction_quality §5).")
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
