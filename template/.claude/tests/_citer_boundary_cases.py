"""Shared planted Runner 4 cases; launchers are never run."""
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
    bare = write("bare.md", "# Plan\n**Shape:** meta\n")
    code = write("code.md", "# Plan\n**Shape:** code\n")
    # The header form real plans carry: Shape is one field of the Task line, its value in backticks.
    task_line = write("task_line.md", "# Plan\n\n- **Task:** `t`. **Shape:** `meta`. **Covers:** D1.\n")
    code_prose = write("code_prose.md", "# Plan\n\n- **Task:** `t`. **Shape:** `code`.\n\n**Shape:** meta is a header value.\n")
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
    def job(prompt, subjects, **fields):
        return {"label": "seat", "shape": "review", "promptPath": str(prompt),
                "mustRead": [{"path": str(p), "lines": 1} for p in subjects], **fields}
    return [
        ("raw unlabelled review", job(raw, [meta]), True),
        ("raw non-lens key", job(raw, [meta], key="custom-seat"), True),
        ("missing block", job(raw, [bare]), True),
        ("stale content hash", job(stale, [stale_plan]), True),
        ("incomplete split subjects", job(incomplete, [meta, second]), True),
        ("missing lens brief header", job(noheader, [meta]), True),
        ("current after unrelated HEAD move", job(current, [meta]), False),
        ("current split subjects", job(current, [meta, second]), False),
        ("merged mandates", job(current, [meta], key="plc-doctrine-consistency+plc-evidence-grounding",
                                 mandates=["plc-doctrine-consistency", "plc-evidence-grounding"]), False),
        ("code-only", job(raw, [code]), False),
        ("raw review of a task-line meta header", job(raw, [task_line]), True),
        ("code header, meta only in later prose", job(raw, [code_prose]), False),
        ("non-plan review", job(raw, [raw]), False),
        ("declared citer non-review", job(raw, [meta], shape="author", key="plc-doctrine-consistency"), True),
        ("non-review without citer", job(raw, [meta], shape="author"), False),
    ]


def stage_cases(root):
    """(label, job, ledger stage, bad): a declared non-plan-check stage reads a plan as input, not subject."""
    return [(label + " under " + stage, job, stage, bad) for label, job, bad in cases(root)
            if label in ("raw unlabelled review", "raw review of a task-line meta header")
            for stage, bad in (("candidate", False), ("pr-review", False), ("plan-check", True))]
