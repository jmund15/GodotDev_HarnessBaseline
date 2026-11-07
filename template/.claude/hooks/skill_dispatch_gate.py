#!/usr/bin/env python3
"""Process guard component: derive governing skills from a dispatch's actual reading.

Called by model_ladder_gate, not registered as another hook. The rule is orchestration §0
(Governing skills). Skill directories identify their owner; skill_dispatch.project.json maps
external artifact paths to owners. No caller-written load declaration is accepted. Uses the
existing dispatch_size reading family and sidecar invocation grammar, across transports.
Faults are reported to the caller, not silently treated as a complete scan (instruction_quality §16).
"""
import fnmatch
import json
import os
from pathlib import Path
import re
import sys

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import dispatch_size
import sidecar_argv

SKILL_PATH = re.compile(r"(?:^|/)\.claude/skills/([A-Za-z0-9_-]+)/")
PATH_TOKEN = re.compile(r"[^\s`\"'<>()[\]{}=,;]+")


def _path(value, root):
    return Path(os.path.abspath(root / value.replace("\\", "/")))


def _specs(payload, root):
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Workflow":
        args = ti.get("args") or {}
        yield json.loads(args) if isinstance(args, str) else args
    elif payload.get("tool_name") == "Bash":
        command = ti.get("command") or ""
        # Reuse the launch gate's assignment/cwd interpretation, including compound calls.
        import orchestration_metrics
        cwd, assignments, _loops = orchestration_metrics._command_cwd(sidecar_argv.strip_heredocs(command), str(root))
        base = Path(cwd or root)
        for inv in sidecar_argv.launcher_invocations(command):
            args = [orchestration_metrics._expand_vars(arg, assignments) for arg in inv.args]
            if inv.kind == "fanout":
                parsed = sidecar_argv.parse_fanout_args(args)
                if parsed:
                    path = _path(parsed[0], base)
                    # A file written by this same Bash call is checked at the foreground runtime entrypoint.
                    if path.exists():
                        yield json.loads(path.read_text(encoding="utf-8"))
            elif inv.kind == "sidecar" and args[:1] != ["--check"]:
                pairs, operand = sidecar_argv.sidecar_flag_pairs(args)
                flags = dict(pairs)
                job = {"promptFile": flags["f"]} if isinstance(flags.get("f"), str) else {
                    "prompt": " ".join(args[operand:])}
                if job.get("promptFile"):
                    job["promptFile"] = str(_path(job["promptFile"], base))
                yield [job]


def _owners(root):
    path = root / ".claude/hooks/skill_dispatch.project.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    owners = data.get("owners") if isinstance(data, dict) else None
    if not isinstance(owners, dict):
        raise ValueError(f"{path}: owners must map skill names to artifact path globs")
    for skill, paths in owners.items():
        if (not isinstance(skill, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", skill)
                or not isinstance(paths, list) or not paths
                or not all(isinstance(p, str) and p for p in paths)
                or not (root / ".claude/skills" / skill / "SKILL.md").is_file()):
            raise ValueError(f"{path}: invalid skill owner {skill!r}")
    return owners


def required_skills(payload):
    """(observed skill names, faults). Inspect each listed text once, never recurse into its references.

    Brief and context files use dispatch_size.MAX_SCAN_BYTES. Binary context files are matched by
    their path, not decoded. Unknown/unreadable reading is a reported lost check, not proof of no owner.
    """
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()
    found, faults, scanned = set(), [], set()
    try:
        owners = _owners(root)
    except (OSError, ValueError) as exc:
        owners = {}
        faults.append(f".claude/hooks/skill_dispatch.project.json: {exc}")

    def observe(raw):
        text = str(raw).replace("\\", "/")
        candidates = {token for token in PATH_TOKEN.findall(text) if "/" in token}
        candidates.update(span for span in dispatch_size.BACKTICKED.findall(text) if "/" in span)
        for candidate in candidates:
            candidate = dispatch_size.LINE_SUFFIX.sub("", candidate.strip()).rstrip("/.,;:)")
            absolute = str(_path(candidate, root)).replace("\\", "/")
            try:
                relative = os.path.relpath(absolute, root).replace("\\", "/")
            except ValueError:
                relative = absolute
            match = SKILL_PATH.match(relative + "/")
            if match and (root / ".claude/skills" / match[1] / "SKILL.md").is_file():
                found.add(match[1])
            for skill, patterns in owners.items():
                if any(fnmatch.fnmatch(os.path.normcase(value), os.path.normcase(pattern.replace("\\", "/")))
                       for pattern in patterns for value in (absolute, relative)):
                    found.add(skill)

    def read(raw):
        observe(raw)
        path = _path(raw, root)
        key = os.path.normcase(str(path))
        if key in scanned:
            return
        scanned.add(key)
        if path.suffix.lower() in dispatch_size.IMAGE_SUFFIXES + (".pdf",):
            return
        try:
            if path.stat().st_size > dispatch_size.MAX_SCAN_BYTES:
                faults.append(f"{path}: governing-skill scan omitted content above dispatch_size.MAX_SCAN_BYTES")
                return
            observe(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            faults.append(f"{path}: {exc}")

    try:
        for spec in _specs(payload, root):
            shared = [spec[k] for k in dispatch_size.SHARED_KEYS if isinstance(spec, dict) and isinstance(spec.get(k), str)]
            for job in dispatch_size._jobs_of(spec):
                if isinstance(job.get("prompt"), str):
                    observe(job["prompt"])
                for path in dispatch_size._paths_of(job) + shared:
                    read(path)
    except (OSError, ValueError, TypeError) as exc:
        faults.append(str(exc))
    return sorted(found), faults
