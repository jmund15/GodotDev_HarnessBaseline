#!/usr/bin/env python3
"""Materialize lens templates as per-job brief FILES for dispatch.js.

A Workflow-dispatched agent has neither the Workflow tool nor the Agent tool, and an Agent-tool
subagent has no Workflow tool (measured 2026-09-03, both arms). So a batch orchestrator cannot
delegate "run /review_pr" and expect the inner lens fan-out to happen under pins. The flat shape is:
the orchestrator materializes every lens prompt as a file, dispatches all lenses in ONE dispatch.js
run (readOnly, spillDir), and a second run consolidates. This tool does the materialization so no
prompt byte travels through Workflow `args` (gotcha_workflow_args_generation_fidelity).

Usage:
  python3 .claude/tools/lens_briefs.py --keys code-reviewer error-hunter ... \
      --out .claude/scratch/pr_pipeline/briefs_pr116 --prefix pr116 \
      --pr-num 116 --branch claude/x --context-path <abs>/context_pr116.md \
      [--sub TRANSCRIPT_CORRECTIONS=<file>] [--effort low] [--agent-type general-purpose]

Writes <out>/<prefix>_<key>.md per lens (shared registry preamble + the template's fenced prompt with
{{PLACEHOLDERS}} substituted) and <out>/jobs_<prefix>.json — the dispatch.js `jobs` array, model taken
from each template's own pin. {{CHECKLIST_CDS}}/{{CHECKLIST_RP}}/{{CHECKLIST_I}} come from
commands/checklists/code_quality.md by section letter; {{TEST_QUALITY_CHECKLIST}} and
{{CODE_QUALITY_CHECKLIST}} are those files whole. {{CONTEXT}} becomes a pointer to --context-path
(dispatch.js `contextPath` already tells the agent to read it first). Unresolved placeholders are an
error, never silently left in a brief.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
sys.path.insert(0, str(HERE))
import lens  # noqa: E402  (sibling module: registry parsing)

CHECKLISTS = CLAUDE / "commands" / "checklists"
FENCE_RE = re.compile(r"```\n(.*?)\n```", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


def sections(path: Path, letters: set[str]) -> str:
    """Return the `## Name (X)` sections of a checklist whose letter X is in `letters`."""
    out, keep = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        m = re.match(r"^## .*\(([A-Z])\)\s*$", line)
        if m:
            keep = m.group(1) in letters
        if keep:
            out.append(line)
    return "".join(out).rstrip() + "\n"


def lens_facing(preamble: str) -> str:
    """Drop the `## Agent Spawn Rules` section: it addresses the CALLER (which engine fans the lenses
    out), and inside a brief it reads as an instruction to dispatch — the shape the nested-fan-out
    guard denies (hooks/dispatch_mechanism_guard.py)."""
    out, skip = [], False
    for line in preamble.splitlines(keepends=True):
        if line.startswith("## "):
            skip = line.startswith("## Agent Spawn Rules")
        if not skip:
            out.append(line)
    return "".join(out).rstrip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--pr-num", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--context-path", required=True, help="absolute path of the shared CONTEXT file")
    ap.add_argument("--sub", action="append", default=[], help="PLACEHOLDER=<file> extra substitutions")
    ap.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh"],
                    help="one effort for every lens; default derives per lens from its model tier "
                         "(orchestration §5b tier-within-quota: executor-tier lenses low, fan-out-tier medium)")
    ap.add_argument("--effort-for", action="append", default=[], metavar="KEY=EFFORT",
                    help="per-lens override, e.g. --effort-for code-reviewer=medium (names the ambiguity in --justification at dispatch)")
    ap.add_argument("--agent-type", default="general-purpose", choices=["general-purpose", "Explore", "Plan"])
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    found: dict[str, lens.Lens] = {}
    preambles: dict[str, str] = {}
    for reg in lens.REGISTRIES:
        pre, lenses = lens.parse(reg)
        preambles[reg] = pre
        for l in lenses:
            found[l.key] = l
    missing = [k for k in a.keys if k not in found]
    if missing:
        sys.exit(f"no such lens: {', '.join(missing)} — run `lens.py index`")

    cq = CHECKLISTS / "code_quality.md"
    tq = CHECKLISTS / "test_quality.md"
    subs = {
        "PR_NUM": str(a.pr_num),
        "BRANCH": a.branch,
        "CONTEXT": f"## Context\nThe shared CONTEXT for this review is the file `{a.context_path}` — read it first (dispatch already told you to). It carries the diff, the changed-file list, the worktree path where every changed file sits at the PR head, and the Jmodot pointer pair.",
        "CHECKLIST_CDS": sections(cq, {"C", "D", "S"}),
        "CHECKLIST_RP": sections(cq, {"R", "P"}),
        "CHECKLIST_I": sections(cq, {"I"}),
        "CODE_QUALITY_CHECKLIST": cq.read_text(encoding="utf-8"),
        "TEST_QUALITY_CHECKLIST": tq.read_text(encoding="utf-8"),
    }
    for s in a.sub:
        k, _, p = s.partition("=")
        subs[k] = Path(p).read_text(encoding="utf-8")

    # Effort is per lens, never one flag for the panel: the registry's model pin classifies the lens's
    # SHAPE (open judgment → executor tier, enumerable → fan-out tier), and orchestration §5b hangs the
    # effort cell off that shape — executor `low`, fan-out `medium`. Model stays hard-coded in the
    # registry because it IS the shape classification; effort varies with band and ambiguity.
    tier_effort = {"opus": "low", "fable": "low", "sonnet": "medium", "haiku": "low"}
    overrides = {}
    for s in a.effort_for:
        k, _, e = s.partition("=")
        if e not in ("low", "medium", "high", "xhigh"):
            sys.exit(f"--effort-for {s}: effort must be low|medium|high|xhigh")
        overrides[k] = e

    jobs = []
    for k in a.keys:
        l = found[k]
        model = l.model if l.model != "-" else "sonnet"
        effort = overrides.get(k) or a.effort or tier_effort.get(model, "medium")
        m = FENCE_RE.search("".join(l.lines))
        if not m:
            sys.exit(f"{k}: no fenced prompt block in its template")
        prompt = m.group(1)
        # Check the TEMPLATE's placeholders, not the substituted text: a substitution that carries
        # a diff of a registry file legitimately contains `{{NAME}}` strings.
        left = sorted(set(PLACEHOLDER_RE.findall(prompt)) - set(subs))
        prompt = PLACEHOLDER_RE.sub(lambda mm: subs.get(mm.group(1), mm.group(0)), prompt)
        if left:
            sys.exit(f"{k}: unresolved placeholders {left} — pass --sub NAME=<file>")
        body = (
            f"<!-- lens brief: {k} for PR #{a.pr_num} — generated by tools/lens_briefs.py; do not edit -->\n\n"
            "# Shared rules for every lens (registry preamble)\n\n" + lens_facing(preambles[l.registry]) + "\n\n"
            "# Your lens\n\n" + prompt.rstrip() + "\n"
        )
        path = out / f"{a.prefix}_{k}.md"
        path.write_text(body, encoding="utf-8")
        jobs.append({
            "label": f"{a.prefix}-{k}",
            "promptPath": str(path.resolve()).replace("\\", "/"),
            "model": model,
            "effort": effort,
            "agentType": a.agent_type,
            "readOnly": True,
        })
    jp = out / f"jobs_{a.prefix}.json"
    jp.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    print(f"wrote {len(jobs)} briefs under {out} and {jp.name}")
    for j in jobs:
        print(f"  {j['label']:36} {j['model']:7} {j['effort']:6} {Path(j['promptPath']).stat().st_size:7,}B")


if __name__ == "__main__":
    main()
