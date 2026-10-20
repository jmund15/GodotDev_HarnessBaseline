"""Meta-subject citer coverage predicate shared by native and sidecar preflight.

Process guard, blocking since plan C slice 4's live run: native panel wf_cab8a650-f87 and the
sidecar panel c4s-plc-doctrine-consistency launched materialized briefs with no false denial, and
raw prompts on both routes drew the warning. BLOCKING is the one flag both launch routes read.
Phase 2 of commands/plan_check.md owns materialization. This module validates, never sweeps.
A run that declares a ledger stage other than plan-check (a close audit, a PR review) reads a plan
as an input, not as the subject whose proposed changes need inbound refs, so it is not checked.
"""
from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[2]
BLOCKING = True
SHAPE = re.compile(r"\*\*Shape:\*\*\s*`?(\w+)")
POINTER = re.compile(r"^> Sweep file: `([^`]+)`", re.M)
REMEDY = ("(plan_check.md §Phase 2) Run lens_briefs.py with each typed --subject-plan and launch its "
          "materialized brief files; Phase 2 is the sole INBOUND_REFS producer.")


@lru_cache(maxsize=1)
def citer_keys():
    import lens
    return {key for key, item in lens.catalog()[1].items() if item.citers == "plan"}


def path_at(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


def coverage_reason(job, *, review=False, root=REPO, stage=None):
    if stage not in (None, "plan-check"):
        return None
    names = set()
    for key in (job.get("key"), job.get("seat"), *(job.get("mandates") or [])):
        if isinstance(key, str):
            names.update(key.split("+"))
    if not review and job.get("shape") != "review" and not names.intersection(citer_keys()):
        return None
    subjects = []
    for entry in job.get("mustRead") or []:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value.endswith(".md"):
            continue
        path = path_at(root, value)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        shape = SHAPE.search(text)
        if shape and shape.group(1) == "meta":
            subjects.append(path)
    if not subjects:
        return None
    label = job.get("key") or job.get("label") or job.get("seat") or "review seat"
    prompt_path = job.get("promptPath") or job.get("promptFile")
    try:
        body = path_at(root, prompt_path).read_text(encoding="utf-8") if prompt_path else str(job.get("prompt") or "")
    except (OSError, UnicodeError):
        body = ""
    problems = []
    if not body.startswith("<!-- lens brief:"):
        problems.append("raw prompt (no lens brief header)")
    recorded = {}
    for pointer in POINTER.findall(body):
        try:
            sweep = json.loads(path_at(root, pointer).read_text(encoding="utf-8"))
            for entry in sweep.get("subjects", []):
                recorded.setdefault(path_at(root, entry["path"]), set()).add(entry["hash"])
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            problems.append(f"unreadable sweep {pointer}")
    for subject in dict.fromkeys(subjects):
        run = subprocess.run(["git", "-C", str(root), "hash-object", str(subject)], capture_output=True, text=True)
        if run.returncode or run.stdout.strip() not in recorded.get(subject, set()):
            problems.append(f"missing or stale sweep for {subject}")
    return f"citer coverage {label}: {'; '.join(problems)}. {REMEDY}" if problems else None


def workflow_reason(payload):
    if payload.get("tool_name") != "Workflow":
        return None
    ti = payload.get("tool_input") or {}
    engine = str(ti.get("scriptPath") or ti.get("name") or "").replace("-", "_")
    review = "review_fanout" in engine
    if not review and "dispatch" not in engine:
        return None
    args = ti.get("args") or {}
    if isinstance(args, str):
        args = json.loads(args)
    jobs = args.get("agents" if review else "jobs") or []
    run_stage = args["ledger"].get("stage") if isinstance(args.get("ledger"), dict) else None
    reasons = [reason for job in jobs if isinstance(job, dict)
               if (reason := coverage_reason(job, review=review, stage=(
                   job["ledger"].get("stage") if isinstance(job.get("ledger"), dict) else run_stage)))]
    return "\n".join(reasons) or None
