#!/usr/bin/env python3
"""Live-tree proof: no live file under `.claude/` still cites the retired `guards/any.md` and `guards/any.godot.md`.

The subject is this repository's own references, so the sweep runs `citers.py` on the real checkout and stays keyed on
its state. `test_tool_profiles.py` proves the sweep itself against a planted repository."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HISTORY = (".claude/auto-memory/archive/", ".claude/tests/fixtures/", ".claude/scripts/rail_battery/reports/",
            ".claude/plans/", ".claude/scratch/", ".claude/instruction_rows/",
            ".claude/autolearn_outcomes.jsonl", ".claude/orchestration_verdicts.json",
            ".claude/orchestration_metrics.jsonl", ".claude/self_evaluate_archive.json",
            ".claude/baseline.lock.json", ".claude/tests/test_tool_profiles.py",
            ".claude/tests/test_tool_profiles_live_citers.py")
# `delete`, not `rename`: a rename resolves its source in history, which a fresh or shallow clone lacks.
_surf = [json.dumps({"kind": "path", "surface": ".claude/guards/%s" % o, "change": "delete"})
         for o in ("any.md", "any.godot.md")]
_out = subprocess.run([sys.executable, os.path.join(ROOT, ".claude", "tools", "citers.py"), "--json", "--repo", ROOT] + _surf,
                      capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
if _out.returncode != 0 or not _out.stdout.strip():
    print("FAIL the citer sweep did not run: %r" % (_out.stderr or _out.stdout)[-300:])
    sys.exit(1)
_stale = sorted({"%s:%s" % (c["path"], c["line"]) for c in json.loads(_out.stdout or '{"citers": []}')["citers"]
                 if c["path"].startswith(".claude/") and not c["path"].startswith(_HISTORY)})
if _stale:
    print("FAIL the citer sweep reports a live reference to guards/any*.md: %r" % _stale)
    sys.exit(1)
print("PASS test_tool_profiles_live_citers (no live reference to guards/any*.md)")
