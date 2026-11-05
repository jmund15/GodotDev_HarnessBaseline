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

# The sweep is keyed on the declared `--subject-plan` set, never on must-read contents.
with tempfile.TemporaryDirectory(prefix="citer_declared_") as root:
    root = Path(root)
    meta = root / "meta.md"
    meta.write_text("# Plan\n\n**Shape:** meta\n```surfaces\n{}\n```\n", encoding="utf-8")
    declared = root / "declared.md"
    declared.write_text("x\n", encoding="utf-8")
    import subprocess
    def hash_of(path):
        return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()
    sweep = root / "declared_citers.json"
    sweep.write_text(__import__("json").dumps(
        {"subjects": [{"path": str(declared), "hash": hash_of(declared), "head": "old"}]}), encoding="utf-8")
    brief = root / "brief.md"
    brief.write_text("<!-- lens brief: planted -->\n> Sweep file: `" + sweep.as_posix()
                     + "`. Grep that file for the surfaces you judge.\n", encoding="utf-8")
    bare = root / "bare.md"
    bare.write_text("Review this plan.\n", encoding="utf-8")
    # A meta plan in must-read is not a subject unless the sweep declares it.
    job = {"label": "seat", "shape": "review", "promptPath": str(bare),
           "mustRead": [{"path": str(meta), "lines": 1}]}
    check("must-read contents alone never declare a subject", citer_coverage.coverage_reason(job) is None)
    # A declared sweep verifies its own subject, even beside an unswept meta must-read.
    job = {"label": "seat", "shape": "review", "promptPath": str(brief),
           "mustRead": [{"path": str(meta), "lines": 1}, {"path": str(declared), "lines": 1}]}
    check("a current declared sweep covers regardless of other must-read files",
          citer_coverage.coverage_reason(job) is None)
    stale = root / "declared_citers_stale.json"
    stale.write_text(sweep.read_text(encoding="utf-8").replace(hash_of(declared), "0" * 40), encoding="utf-8")
    stale_brief = root / "stale.md"
    stale_brief.write_text("<!-- lens brief: planted -->\n> Sweep file: `" + stale.as_posix()
                           + "`. Grep that file for the surfaces you judge.\n", encoding="utf-8")
    job = {"label": "seat", "shape": "review", "promptPath": str(stale_brief),
           "mustRead": [{"path": str(declared), "lines": 1}]}
    check("a declared subject whose recorded hash is stale is refused", bool(citer_coverage.coverage_reason(job)))
    # A citer lens seat declares a sweep by its lens: with nothing declared a code-only subject owes none.
    job = {"label": "seat", "key": "plc-doctrine-consistency", "shape": "author", "promptPath": str(bare),
           "mustRead": [{"path": str(declared), "lines": 1}]}
    check("a citer lens seat with no declared sweep is not checked",
          citer_coverage.coverage_reason(job, stage="plan-check") is None)
print("all ok" if not failures else f"{failures} failure(s)")
sys.exit(int(bool(failures)))
