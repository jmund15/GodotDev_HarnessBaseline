#!/usr/bin/env python3
"""Proof for tools/citer_coverage.py: launch coverage of `citers: plan` lenses, and their keys come from
every shipped registry. A registry whose file a layer-stripped consumer never received is skipped, not fatal.

    python3 .claude/tests/test_citer_coverage.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "tools")]
import citer_coverage  # noqa: E402
import lens  # noqa: E402
from _citer_boundary_cases import cases, stage_cases  # noqa: E402

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"{'ok' if ok else 'FAIL'} {label}" + ("" if ok or not detail else f" :: {detail}"))
    failures += not ok


with tempfile.TemporaryDirectory(prefix="citer_coverage_") as root:
    for label, job, bad in cases(root):
        reason = citer_coverage.coverage_reason(job)
        check(label, bool(reason) == bad and (not bad or "Phase 2" in reason and "lens_briefs.py" in reason))
    for label, job, stage, bad in stage_cases(root):
        check(label, bool(citer_coverage.coverage_reason(job, stage=stage)) == bad)
text_args = {"tool_name": "Workflow", "tool_input": {"name": "worklog-relevance", "args": "Combat feel drive: C13"}}
try:
    ok = citer_coverage.workflow_reason(text_args) is None
except Exception:
    ok = False
check("free-text args on a non-review engine allow", ok)
review_text = {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/review_fanout.js", "args": "not json"}}
try:
    citer_coverage.workflow_reason(review_text)
    ok = False
except ValueError:
    ok = True
check("unparseable args on a review engine stay closed", ok)
check("shipped default blocks", getattr(citer_coverage, "BLOCKING", None) is True)

registry = ("# Review agents\n\n## Agent Templates\n\n### citing-lens -- `model: \"opus\"`\nciters: plan\nbody\n\n"
            "### plain-lens -- `model: \"opus\"`\nbody\n")
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "review_agents.md").write_text(registry, encoding="utf-8")
    saved = lens.AGENTS, lens.REGISTRIES
    lens.AGENTS = Path(tmp)
    lens.REGISTRIES = {"review": ("review_agents.md", ""), "structure_audit": ("structure_audit_agents.md", "stra-")}
    citer_coverage.citer_keys.cache_clear()
    try:
        keys = citer_coverage.citer_keys()
    except SystemExit as exc:
        keys = f"SystemExit: {exc.code}"
    finally:
        lens.AGENTS, lens.REGISTRIES = saved
        citer_coverage.citer_keys.cache_clear()
check("citer keys resolve while another registry's file is absent", keys == {"citing-lens"}, keys)
print("all ok" if not failures else f"{failures} failure(s)")
sys.exit(int(bool(failures)))
