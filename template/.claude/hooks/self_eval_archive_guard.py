#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — validates self_evaluate_archive.json after every write.

Scope: only `tool_input.file_path` ending in `self_evaluate_archive.json`; every other path
emits `{}`. The enum sources are read from the archive itself, never hardcoded, so
`/self_evaluate`'s "add a new letter" instruction passes the guard by construction:
OUTCOMES = {clean, correction, failure} (the `/self_evaluate` §5d literal);
PATTERNS = keys(Self_Evaluate_Themes.patterns) union {null}. Entries with id <= LEGACY_MAX_ID
are exempt from the pattern checks (recorded drift stays as-is).

PostToolUse cannot block (the write already happened) — violations surface on
`hookSpecificOutput.additionalContext` so the model fixes the file; clean is `{}`.

Fail-open: any error outside the archive-parsing path exits 0 silently. Wired in:
settings.json hooks.PostToolUse "Write|Edit".
"""

import json
import sys

OUTCOMES = {"clean", "correction", "failure"}
LEGACY_MAX_ID = 266
ENUM_HOMES = ("enum homes: patterns = Self_Evaluate_Themes.patterns in the archive; "
              "outcomes = clean|correction|failure (/self_evaluate §5d)")


def rel_path_ok(file_path):
    return (file_path or "").replace("\\", "/").endswith("self_evaluate_archive.json")


def check(text):
    """Return a list of `id=<n>: <violation>` strings, or [] when clean."""
    try:
        doc = json.loads(text)
    except ValueError:
        return ["id=?: archive is not valid JSON"]

    patterns = set()
    if isinstance(doc, dict):
        themes = doc.get("Self_Evaluate_Themes") or {}
        patterns = set((themes.get("patterns") or {}).keys())
    patterns.add(None)

    entries = (doc.get("structured_entries") or []) if isinstance(doc, dict) else []
    violations = []
    seen_ids = {}
    seen_sessions = {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        outcome = entry.get("outcome")
        pattern = entry.get("pattern")
        session_id = entry.get("session_id")

        if eid in seen_ids:
            violations.append(f"id={eid}: duplicate id")
        else:
            seen_ids[eid] = True

        if session_id:
            if session_id in seen_sessions:
                violations.append(f"id={eid}: duplicate session_id {session_id!r}")
            else:
                seen_sessions[session_id] = True

        if "outcome" not in entry:
            violations.append(f"id={eid}: missing outcome")
        elif outcome not in OUTCOMES:
            violations.append(f"id={eid}: outcome {outcome!r} not in {sorted(OUTCOMES)}")

        if isinstance(eid, int) and eid > LEGACY_MAX_ID:
            if pattern not in patterns:
                violations.append(f"id={eid}: pattern {pattern!r} not in enum")
            if pattern is None and outcome in ("correction", "failure"):
                violations.append(f"id={eid}: null pattern with outcome {outcome!r}")

    return violations


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Write", "Edit"):
        return
    file_path = (data.get("tool_input") or {}).get("file_path")
    if not rel_path_ok(file_path):
        return

    with open(file_path, encoding="utf-8") as fh:
        text = fh.read()

    violations = check(text)
    if not violations:
        return

    lines = [f"[self-eval-archive-guard] {len(violations)} violation(s) — fix before continuing:"]
    lines.extend(violations[:10])
    lines.append(ENUM_HOMES)
    msg = "\n".join(lines)
    sys.stdout.write(json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
