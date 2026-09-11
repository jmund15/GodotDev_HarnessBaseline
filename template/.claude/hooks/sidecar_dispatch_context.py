#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash — gate sidecar launches and inject `reference/sidecar_dispatch.md`.

Fires for a direct `*_sidecar.sh` launch or a structurally validated `sidecar_fanout.py` call.

1. PREFLIGHT (deny path). Direct launchers run `<launcher> --check [-m <alias>] [-A]`
   synchronously. The same launcher gates each validated fan-out child at runtime. Availability,
   budget-band, balance, and provider-ceiling refusals remain owned by the launchers.
2. APPROVAL. A validated fan-out gets `permissionDecision: allow` because auto mode cannot inspect
   its jobs JSON. `--authorize`, `extraArgs`, shell compounds, and non-project paths fall through.
3. CONTEXT (advisory). Emits the reference once per session, and again after compaction.

Wired in: settings.json hooks.PreToolUse matcher "Bash" (via pre_bash_dispatch.py).
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import fire_once_since_compaction

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
try:
    import model_registry  # noqa: E402
except Exception:
    model_registry = None

STATE_KEY = "sidecar_dispatch_context"
# A launch INVOKES the launcher (`bash <path>`, or the path at a command start) with flags; a
# mention (grep, sed, ls, `--launcher x_sidecar.sh` as an argument) does not.
LAUNCH_RE = re.compile(r"(?:^|[;&|(]\s*|\bbash\s+)([\w./\\:-]*_sidecar\.sh)(?=\s|$)")
ALIAS_RE = re.compile(r"(?:^|\s)-m\s+[\"']?([\w.-]+)")
AUTHORIZED_RE = re.compile(r"(?:^|\s)-A(?=\s|$)")
PREFLIGHT_TIMEOUT = 45   # under pre_bash_dispatch's 75 s; the codex probe is one network call

SAFE_JOB_FIELDS = frozenset({
    "label", "alias", "promptFile", "effort", "disclosure", "shape",
    "contextFiles", "schemaFile", "workdir", "transport",
})
REQUIRED_JOB_FIELDS = frozenset({"label", "alias", "promptFile", "shape"})
SAFE_SHAPES = frozenset({"any", "survey", "review", "author"})
SAFE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
SAFE_DISCLOSURES = frozenset({"bare", "pointer", "full"})
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHELL_META_RE = re.compile(r"[\n\r;&|<>`$(){}]")


def _git_bash():
    """Git Bash, never WSL's: Windows Python resolves a bare `bash` to WSL (different filesystem)."""
    for cand in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"),
                 "C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe"):
        if cand and os.path.exists(cand):
            return cand
    return "bash" if os.name != "nt" else None


def preflight(cmd, root):
    """-> refusal text when the launcher's --check exits non-zero; None when it passes or cannot run."""
    if os.environ.get("PP_SIDECAR_PREFLIGHT") == "0":
        return None
    m = LAUNCH_RE.search(cmd)
    bash = _git_bash()
    if not m or not bash:
        return None
    launcher = m.group(1)
    # Cannot resolve the launcher from the repo root (relative path after a `cd`, typo): the
    # launcher's own gate still runs at dispatch, so fail OPEN rather than deny on exit 127.
    if not os.path.isabs(launcher) and not os.path.exists(os.path.join(root, launcher)):
        return None
    tail = cmd[m.end():]   # flags belong to THIS launcher, not to another command on the line
    args = [bash, launcher, "--check"]
    alias = ALIAS_RE.search(tail)
    if alias:
        args += ["-m", alias.group(1)]
    if AUTHORIZED_RE.search(tail):
        args.append("-A")
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=PREFLIGHT_TIMEOUT,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except Exception:
        return None
    if r.returncode == 0:
        return None
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    return ("REFUSED at preflight (%s --check exit %d) — the dispatch would have died the same way in the "
            "background:\n%s\nRe-select under the ladder, or pass -A and state the spend."
            % (os.path.basename(launcher), r.returncode, text[-1500:]))


def _inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path must be a non-empty string")
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _path_has_symlink(root, path):
    if not _inside(path, root):
        return True
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _regular_project_file(root, value):
    path = _resolve(root, value)
    return _inside(path, root) and path.is_file() and not _path_has_symlink(root, path)


def _parse_fanout_command(command, root):
    if not isinstance(command, str) or SHELL_META_RE.search(command):
        return None
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(words) < 3 or Path(words[0]).name not in {"python", "python3"}:
        return None
    script = _resolve(root, words[1])
    expected = (root / ".claude" / "tools" / "sidecar_fanout.py").resolve()
    if script != expected or not script.is_file() or _path_has_symlink(root, script):
        return None

    jobs = None
    out_dir = None
    index = 2
    while index < len(words):
        word = words[index]
        if word == "--out-dir" and index + 1 < len(words) and out_dir is None:
            out_dir = words[index + 1]
            index += 2
        elif word == "--max-parallel" and index + 1 < len(words):
            try:
                width = int(words[index + 1])
            except ValueError:
                return None
            if not 1 <= width <= 4:
                return None
            index += 2
        elif not word.startswith("-") and jobs is None:
            jobs = word
            index += 1
        else:
            return None
    if jobs is None:
        return None
    jobs_path = _resolve(root, jobs)
    scratch = (root / ".claude" / "scratch").resolve()
    if (not _inside(jobs_path, scratch) or jobs_path.suffix.lower() != ".json"
            or not jobs_path.is_file() or _path_has_symlink(root, jobs_path)):
        return None
    output_path = _resolve(root, out_dir) if out_dir else jobs_path.parent / "fanout"
    if not _inside(output_path, scratch) or _path_has_symlink(root, output_path):
        return None
    return jobs_path


def _safe_fanout_job(job, root, registry):
    if not isinstance(job, dict) or not REQUIRED_JOB_FIELDS <= job.keys():
        return False
    if not job.keys() <= SAFE_JOB_FIELDS:
        return False
    if not isinstance(job["label"], str) or not LABEL_RE.fullmatch(job["label"]):
        return False
    if job.get("shape") not in SAFE_SHAPES or job.get("workdir", ".") != ".":
        return False
    if not _regular_project_file(root, job.get("promptFile")):
        return False
    context_files = job.get("contextFiles", [])
    if not isinstance(context_files, list) or not all(
        _regular_project_file(root, path) for path in context_files
    ):
        return False
    schema = job.get("schemaFile")
    if schema is not None and not _regular_project_file(root, schema):
        return False
    for field in ("alias", "effort", "disclosure", "transport"):
        if field in job and not isinstance(job[field], str):
            return False
    if ("effort" in job and job["effort"] not in SAFE_EFFORTS
            or "disclosure" in job and job["disclosure"] not in SAFE_DISCLOSURES):
        return False
    try:
        entry = model_registry.resolve(job["alias"], registry)
        available = model_registry.model_available(job["alias"], registry)
    except Exception:
        return False
    return (available
            and job.get("transport", entry.get("transport")) == entry.get("transport"))


def fanout_allowed(payload):
    if model_registry is None:
        return False
    root_value = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    if not isinstance(root_value, str):
        return False
    root = Path(root_value).resolve()
    jobs_path = _parse_fanout_command((payload.get("tool_input") or {}).get("command"), root)
    if jobs_path is None:
        return False
    try:
        with open(jobs_path, encoding="utf-8") as handle:
            jobs = json.load(handle)
        registry = model_registry.load()
    except Exception:
        return False
    return (isinstance(jobs, list)
            and 0 < len(jobs) <= 8
            and len({job.get("label") for job in jobs if isinstance(job, dict)}) == len(jobs)
            and all(_safe_fanout_job(job, root, registry) for job in jobs))


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    direct = LAUNCH_RE.search(cmd)
    fanout_is_allowed = fanout_allowed(data)
    # `--check` must be THIS launcher's own flag; the word elsewhere on the line is not a probe.
    if direct and "--check" in cmd[direct.end():].split(";")[0]:
        direct = None
    if not direct and not fanout_is_allowed:
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if direct:
        refusal = preflight(cmd, root)
        if refusal:
            sys.stdout.write(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": refusal,
                }
            }))
            return

    output = {"hookEventName": "PreToolUse"}
    if fanout_is_allowed:
        output["permissionDecision"] = "allow"
        output["permissionDecisionReason"] = (
            "Auto-approved: the sidecar fan-out is structurally safe; "
            "its launchers retain their budget and quota gates"
        )

    ref = os.path.join(root, ".claude", "reference", "sidecar_dispatch.md")
    try:
        with open(ref, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""
    if text and fire_once_since_compaction(data.get("session_id") or "", STATE_KEY):
        output["additionalContext"] = "[sidecar dispatch — reference/sidecar_dispatch.md]\n" + text
    if len(output) > 1:
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
