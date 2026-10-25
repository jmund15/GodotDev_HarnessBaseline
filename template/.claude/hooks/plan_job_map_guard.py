#!/usr/bin/env python3
"""Plan job-map advisory: a production write while a plan this session wrote lacks `## Job map`.

CLASS: process guard, advisory from its first commit. It turns blocking only by a commit that cites a live
session which exercised its matcher with no false advisory (`rules/harness_tooling.md` §A new guard starts
advisory). It protects slice routing, not peer work, owner authority or the gate. It enforces a fact only:
the heading exists. `/plan_check` judges the routes.

Wired in: `process(payload)` is a member of `pre_edit_dispatch.py` and `post_edit_dispatch.py` (matcher
Write|Edit); `main()` runs the same logic standalone.

Events (one script, `hook_event_name` picks the branch):
  PostToolUse Write|Edit            records a plan path in this session's hook state. A plan is a `.md`
                                    under `<project>/.claude/plans/`, or any `.md` whose basename starts
                                    `plan-` or `plan_`, and never under `<project>/.claude/scratch/`.
  PreToolUse  Write|Edit            allows every call. A production write while a recorded plan
                                    that still exists has no heading matching `^#{2,6}\\s*Job map\\b` emits
                                    one advisory through `additionalContext` naming that plan. The advisory
                                    fires once per plan per session: the state records each plan advised, so
                                    a later write for the same plan is silent. A plan that gains the heading
                                    never advises; a newly recorded mapless plan advises once for itself.

Production path: inside the project, not `.md`, not under the top-level `.claude/`, `logs/`, `.git/`,
`node_modules/`, `.search-index/`, and not git-ignored. The string tests run first; a git spawn happens
only when a recorded plan that lacks the heading exists.

FAIL POSTURE: not a floor guard. An internal error allows the call and reports the lost check through
`additionalContext` (`instruction_quality` §16), exit 0.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_state  # noqa: E402

JOB_MAP_RE = re.compile(r"^#{2,6}\s*Job map\b", re.IGNORECASE | re.MULTILINE)
STATE_KEY = "job_map_plans"
ADVISED_KEY = "job_map_advised"
MAX_PLANS = 32
WRITE_TOOLS = ("Write", "Edit")
SKIP_PREFIXES = (".claude/", "logs/", ".git/", "node_modules/", ".search-index/")
ADVISORY_TEXT = ("[plan-job-map] advisory: plan without a job map: {paths}. Add a `## Job map` section "
                 "(`plan_file_format.md` §Job map) routing each slice.")


def has_job_map(text):
    """True when `text` has a `## Job map`-style heading (levels 2 to 6, case-insensitive)."""
    return JOB_MAP_RE.search(text or "") is not None


def _project_dir(payload):
    return os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def _is_inside(path, directory):
    norm = os.path.normcase(os.path.abspath(path))
    base = os.path.normcase(os.path.abspath(directory)).rstrip(os.sep)
    return norm == base or norm.startswith(base + os.sep)


def _posix(path):
    return os.path.abspath(path).replace("\\", "/")


def _is_plan(abs_path, root):
    if not abs_path.lower().endswith(".md"):
        return False
    if _is_inside(abs_path, os.path.join(root, ".claude", "scratch")):
        return False
    if _is_inside(abs_path, os.path.join(root, ".claude", "plans")):
        return True
    return os.path.basename(abs_path).lower().startswith(("plan-", "plan_"))


def _production_rel(abs_path, root):
    """The project-relative path when `abs_path` is a production write target, else None. String tests only."""
    if not _is_inside(abs_path, root):
        return None
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    if rel.lower().endswith(".md") or rel.lower().startswith(SKIP_PREFIXES):
        return None
    return rel


def _git_ignored(rel, root):
    done = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", rel],
                          capture_output=True, timeout=10)
    if done.returncode not in (0, 1):
        raise RuntimeError("git check-ignore exited %d for %s" % (done.returncode, rel))
    return done.returncode == 0


def _target_path(payload, root, key):
    target = (payload.get("tool_input") or {}).get(key) or ""
    return os.path.abspath(os.path.join(root, target)) if target else ""


def _record(payload):
    root = _project_dir(payload)
    target = _target_path(payload, root, "file_path")
    if not target or not _is_plan(target, root):
        return
    plan = _posix(target)

    def update(state):
        kept = state.get(STATE_KEY)
        kept = [item for item in kept if isinstance(item, str) and item != plan] if isinstance(kept, list) else []
        state[STATE_KEY] = (kept + [plan])[-MAX_PLANS:]
        return True

    _hook_state.update_json_locked(_hook_state.state_path(payload.get("session_id")), update)


def _lacking_plans(recorded):
    """The recorded plan paths that still exist and lack the heading, in recorded order."""
    lacking = []
    for plan in recorded:
        if not isinstance(plan, str) or not os.path.exists(plan):
            continue
        with open(plan, encoding="utf-8", errors="replace") as fh:
            if not has_job_map(fh.read()):
                lacking.append(plan)
    return lacking


def _claim_advice(session_id, plans):
    """Mark `plans` advised in this session and return those not advised before.

    A state that cannot be locked or written returns every plan: a broken state layer repeats an
    advisory rather than staying silent.
    """
    def update(state):
        advised = state.get(ADVISED_KEY)
        advised = advised if isinstance(advised, list) else []
        fresh = [plan for plan in plans if plan not in advised]
        state[ADVISED_KEY] = (advised + fresh)[-MAX_PLANS:]
        return fresh

    written, fresh = _hook_state.update_json_locked(_hook_state.state_path(session_id), update)
    return fresh if written else plans


def _advice(payload):
    """The advisory text for recorded mapless plans this production write has not advised yet, else None."""
    if (payload.get("tool_name") or "") not in WRITE_TOOLS:
        return None
    root = _project_dir(payload)
    target = _target_path(payload, root, "file_path")
    if not target:
        return None
    rel = _production_rel(target, root)
    if rel is None:
        return None
    session_id = payload.get("session_id")
    recorded = _hook_state.read_json_salvage(_hook_state.state_path(session_id)).get(STATE_KEY)
    if not isinstance(recorded, list) or not recorded:
        return None
    lacking = _lacking_plans(recorded)
    if not lacking:
        return None
    if _git_ignored(rel, root):
        return None
    fresh = _claim_advice(session_id, lacking)
    if not fresh:
        return None
    return ADVISORY_TEXT.format(paths=", ".join(fresh))


def _emit(event, body):
    sys.stdout.write(json.dumps({"hookSpecificOutput": dict(hookEventName=event, **body)}))


def _lost_text(exc):
    return ("[plan-job-map] guard failed open (%s): the job-map check did not run for this call. "
            "Repair hooks/plan_job_map_guard.py before relying on the check." % exc)


def _lost(event, exc):
    _emit(event if event in ("PreToolUse", "PostToolUse") else "PreToolUse", {"additionalContext": _lost_text(exc)})


def process(payload):
    """Edit-dispatcher sub-hook: `{"context": <text>}` for an advisory or a lost check, else None."""
    event = payload.get("hook_event_name")
    try:
        if event == "PostToolUse" and payload.get("tool_name") in WRITE_TOOLS:
            _record(payload)
        elif event == "PreToolUse":
            text = _advice(payload)
            if text:
                return {"context": text}
    except Exception as exc:
        return {"context": _lost_text(exc)}
    return None


def main():
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        return
    result = process(payload)
    if result:
        _emit(payload.get("hook_event_name") or "PreToolUse", {"additionalContext": result["context"]})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _lost("PreToolUse", exc)
    sys.exit(0)
