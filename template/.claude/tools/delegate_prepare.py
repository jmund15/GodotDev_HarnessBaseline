#!/usr/bin/env python3
"""The ad-hoc dispatch shape's prepare route: `lens_briefs.py prepare --spec <spec.json> --out <run dir>` with
`"mode": "delegate"` (`commands/delegate.md`).

One call writes `<run dir>/jobs.json` for `sidecar_fanout.py`, makes the author jobs' worktrees, runs the size
preflight on the file and prints the launch call and each job's return call. A bad spec refuses before any
worktree exists.

Spec: `{"mode": "delegate", "jobs": [<job>], "spillDir"?, "contextPath"?, "base"?, "root"?}`. A job carries the
fan-out's own fields (`tools/sidecar_fanout.py` docstring) plus:
    brief      the brief file (becomes `promptFile`; `promptFile` is accepted as the same field)
    worktree   `true` (named for the label) or a name: the job works in a new worktree
               `<root>/.claude/worktrees/<name>` on branch `scratch/<name>`, cut from `base` (default `HEAD`)
Required per job: `label` (`[A-Za-z0-9._-]`, unique), a brief file that exists, `kind`
(scout|lens|reviewer|author|judge|gate), `tier`, and `rows` or `model` + `effort`, never both. `role` defaults to
`tier`. Every other field passes through unchanged, so the fan-out's own checks (`pick`/`spec`, `tools`,
`window`, ...) stay its own.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = ("scout", "lens", "reviewer", "author", "judge", "gate")
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
LINE_CHARS = 400


def _git(root, *args):
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout + done.stderr).strip()


def _posix(path):
    return Path(path).resolve().as_posix()


def _rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _kinds():
    """The job kinds a delegate may name: the kinds the command documents, that `guards/tool_profiles.json` declares."""
    declared = json.loads((HERE.parent / "guards" / "tool_profiles.json").read_text(encoding="utf-8")).get("kinds") or {}
    return [kind for kind in KINDS if kind in declared]


def _problems(spec, root):
    """Every reason the spec cannot run, as lines. The worktree names a job asks for come back as {label: name}."""
    jobs, problems, names = spec.get("jobs"), [], {}
    if not isinstance(jobs, list) or not jobs or not all(isinstance(j, dict) for j in jobs):
        return ["`jobs` must be a non-empty list of job objects"], names
    kinds, seen = _kinds(), set()
    for index, job in enumerate(jobs):
        label = job.get("label")
        who = f"job {label}" if isinstance(label, str) and label else f"job #{index + 1}"
        if not isinstance(label, str) or not _SAFE.match(label):
            problems.append(f"{who}: `label` must match [A-Za-z0-9._-]")
        elif label in seen:
            problems.append(f"{who}: duplicate label")
        seen.add(label)
        brief = job.get("brief") or job.get("promptFile")
        if not brief or not Path(brief).is_file():
            problems.append(f"{who}: brief {brief!r} is no file")
        if job.get("kind") not in kinds:
            problems.append(f"{who}: `kind` must be one of {', '.join(kinds)}")
        if not job.get("tier"):
            problems.append(f"{who}: `tier` is required (the role the pin must serve)")
        has_rows, has_pair = bool(job.get("rows")), bool(job.get("model") and job.get("effort"))
        if has_rows == has_pair:
            problems.append(f"{who}: give `rows` or `model` + `effort`, one of the two")
        wt = job.get("worktree")
        if wt:
            name = label if wt is True else wt
            if not isinstance(name, str) or not _SAFE.match(name):
                problems.append(f"{who}: `worktree` must be true or a name matching [A-Za-z0-9._-]")
            elif name in names.values():
                problems.append(f"{who}: worktree name {name} is taken by another job of this spec")
            else:
                names[label] = name
                if (root / ".claude" / "worktrees" / name).exists():
                    problems.append(f"{who}: {_rel(root / '.claude' / 'worktrees' / name, root)} already exists")
                if _git(root, "rev-parse", "--verify", "-q", f"refs/heads/scratch/{name}")[0] == 0:
                    problems.append(f"{who}: branch scratch/{name} already exists")
    return problems, names


def _preflight(jobs_path, root):
    done = subprocess.run([sys.executable, str(HERE / "dispatch_size.py"), str(jobs_path)], cwd=str(root),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line[:LINE_CHARS] for line in (done.stdout + done.stderr).splitlines() if line.strip()]


def run(spec, out, root):
    """Prepare one delegate run. Prints the summary and returns the exit code (0 prepared, 2 refused)."""
    root, out = Path(root).resolve(), Path(out).resolve()
    problems, names = _problems(spec, root)
    if problems:
        print("prepare (delegate): refused, nothing was created:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    base = spec.get("base") or "HEAD"
    made = {}
    for label, name in names.items():
        target = root / ".claude" / "worktrees" / name
        code, text = _git(root, "worktree", "add", "-q", "-b", f"scratch/{name}", str(target), base)
        if code != 0:
            print(f"prepare (delegate): git worktree add for {label} failed: {text}", file=sys.stderr)
            if made:
                print("  already made, retire by hand: " + ", ".join(sorted(made.values())), file=sys.stderr)
            return 2
        made[label] = _posix(target)
    jobs = []
    for job in spec["jobs"]:
        entry = {k: v for k, v in job.items() if k not in ("brief", "worktree", "promptFile")}
        entry["promptFile"] = _posix(job.get("brief") or job.get("promptFile"))
        entry.setdefault("role", entry["tier"])
        if job["label"] in made:
            entry["workdir"] = made[job["label"]]
        jobs.append(entry)
    written = {"jobs": jobs}
    for key in ("spillDir", "contextPath"):
        if spec.get(key):
            written[key] = spec[key]
    out.mkdir(parents=True, exist_ok=True)
    jobs_path = out / "jobs.json"
    jobs_path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8", newline="\n")
    record_dir = Path(spec["spillDir"]) if spec.get("spillDir") else out / "fanout"
    print(f"prepared {len(jobs)} job(s): {_rel(jobs_path, root)}")
    for entry in jobs:
        where = entry.get("workdir") or "the repo root"
        print(f"  {entry['label']}: {entry['kind']} {entry['tier']}, works in {where}")
    for line in _preflight(jobs_path, root):
        print(line)
    launch = f"python3 .claude/tools/sidecar_fanout.py {_rel(jobs_path, root)}"
    launch += "" if spec.get("spillDir") else f" --out-dir {_rel(record_dir, root)}"
    print(f"next: {launch} --detach")
    import watch_records
    print(f"then, with a sidecar job running and no watcher armed, {watch_records.ARM_CALL}: "
          f"{watch_records.ARM_COMMAND} --await-launch")
    for entry in jobs:
        label = entry["label"]
        if entry.get("workdir"):
            print(f"return {label}: python3 .claude/tools/dispatch_result.py take {_rel(record_dir, root)} --label {label}")
        else:
            print(f"return {label}: python3 .claude/tools/dispatch_result.py consume {_rel(record_dir, root)} "
                  f"--label {label} --brief {entry['promptFile']}")
    return 0
