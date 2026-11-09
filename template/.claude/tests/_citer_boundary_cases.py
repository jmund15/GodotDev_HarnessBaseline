"""Shared planted Runner 4 cases; launchers are never run.

The predicate is keyed on the declared subject set: the subjects recorded in the brief's sweep file,
which `lens_briefs.py --subject-plan` writes. A must-read file carrying a `**Shape:**` header
declares nothing.
"""
import json
import subprocess
from pathlib import Path


def cases(root):
    root = Path(root)
    def write(name, text):
        p = root / name
        p.write_text(text, encoding="utf-8", newline="\n")
        return p
    meta = write("meta.md", "# Plan\n\n**Shape:** meta\n```surfaces\n{}\n```\n")
    second = write("second.md", meta.read_text())
    raw = write("raw.md", "Review this plan.\n")
    def brief(name, subjects, header=True):
        sweep = root / (name + "_citers.json")
        rows = [{"path": str(p.resolve()), "hash": subprocess.check_output(
            ["git", "hash-object", str(p)], text=True).strip(), "head": "unrelated-old-head"} for p in subjects]
        sweep.write_text(json.dumps({"subjects": rows}), encoding="utf-8")
        return write(name + ".md", ("<!-- lens brief: planted -->\n" if header else "")
                     + f"> Sweep file: `{sweep.as_posix()}`. Grep that file for the surfaces you judge.\n")
    current = brief("current", [meta, second])
    incomplete = brief("incomplete", [meta])
    noheader = brief("noheader", [meta], False)
    stale_plan = write("stale.md", meta.read_text())
    stale = brief("stale_brief", [stale_plan])
    stale_plan.write_text(stale_plan.read_text() + "changed\n", encoding="utf-8")
    gone_sweep = root / "gone_plan_citers.json"
    gone_sweep.write_text(json.dumps({"subjects": [
        {"path": str(root / "gone-plan.md"), "hash": "0" * 40, "head": "old"}]}), encoding="utf-8")
    gone_brief = write("gone_plan_brief.md", "<!-- lens brief: planted -->\n> Sweep file: `"
                       + gone_sweep.as_posix() + "`. Grep that file for the surfaces you judge.\n")
    def job(prompt, subjects, **fields):
        return {"label": "seat", "kind": "reviewer", "promptPath": str(prompt),
                "mustRead": [{"path": str(p), "lines": 1} for p in subjects], **fields}
    return [
        ("raw review with no declared sweep", job(raw, [meta]), False),
        ("raw review of a task-line meta must-read, no sweep", job(raw, [meta]), False),
        ("declared sweep, current", job(current, [meta, second]), False),
        ("current after unrelated HEAD move", job(current, [meta]), False),
        ("merged mandates with a current sweep", job(current, [meta], key="plc-doctrine-consistency+plc-evidence-grounding",
                                                     mandates=["plc-doctrine-consistency", "plc-evidence-grounding"]), False),
        ("declared sweep without a lens brief header", job(noheader, [meta]), True),
        ("declared sweep, stale content hash", job(stale, [stale_plan]), True),
        ("declared sweep naming a subject that is gone", job(gone_brief, [root / "gone-plan.md"]), True),
        ("declared citer seat with no swept subject is not checked", job(raw, [meta], kind="author", key="plc-doctrine-consistency"), False),
        ("non-review without a citer lens", job(raw, [meta], kind="author"), False),
    ]


def stage_cases(root):
    """(label, job, ledger stage, bad): a declared non-plan-check stage reads a plan as input, not subject."""
    return [(label + " under " + stage, job, stage, bad) for label, job, bad in cases(root)
            if label == "declared sweep, stale content hash"
            for stage, bad in (("candidate", False), ("pr-review", False), ("plan-check", True))]
