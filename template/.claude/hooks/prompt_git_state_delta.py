#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit - Git state delta injector

Emits a <git-state-delta> block ONLY when the working-tree state has changed
since the previous prompt. Silent on the first prompt of a session (SessionStart
already injected the initial context) and on all subsequent prompts where
nothing has moved.

Catches:
  - Cross-window commits (user committed in another shell)
  - /commit_push consequences (HEAD advanced + tree clean)
  - Submodule pointer drift (Jmodot HEAD changed)
  - Branch surprises after `git switch`

State fingerprint:
  branch | head_sha | staged | unstaged | untracked | ahead | behind | jmodot_head_sha

Cache: .claude/.cache/git-state-{cwd_hash}.txt (per worktree, gitignored).

Failure mode: every error path exits 0 with no output. This hook must NEVER
block a user prompt from being processed.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# Field separator inside the fingerprint string. Pipe is fine because no
# git ref or count we capture can contain it.
SEP = "|"

# Cache file format: a single line of pipe-separated values, no trailing newline.
# We rewrite the whole file on every change, so no schema migration needed.

# ---------------------------------------------------------------------------
# Subprocess helpers (5s budget per call; silent on failure)
# ---------------------------------------------------------------------------


def _run(args: list[str], cwd: str | None = None, timeout: int = 5) -> str:
    """Run a command and return trimmed stdout, or empty string on any failure.

    Why so defensive: this hook fires on every UserPromptSubmit. A single
    crash here would feel to the user like Claude Code itself broke. So we
    swallow everything and just emit nothing on the failure path.
    """
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# State capture
# ---------------------------------------------------------------------------


def _git_toplevel() -> str:
    """Resolve the current git toplevel (works in worktrees). Empty if not a repo."""
    return _run(["git", "rev-parse", "--show-toplevel"])


def _parse_porcelain_v2(out: str) -> dict[str, str]:
    """Parse `git status --porcelain=v2 --branch` output.

    Why v2: a single subprocess call collapses what would otherwise be five
    (branch, HEAD, ahead/behind, staged count, unstaged count, untracked count).
    On Windows/Git Bash each git invocation costs ~50ms — this matters in a
    hook that fires on every UserPromptSubmit.

    v2 format header lines:
        # branch.oid <full-sha>
        # branch.head <branch-name>
        # branch.upstream <ref>          (only present if upstream configured)
        # branch.ab +<ahead> -<behind>   (only present if upstream configured)

    Then one entry per file:
        1 <XY> ... — ordinary changed
        2 <XY> ... — renamed/copied
        u <XY> ... — unmerged
        ? <path>   — untracked

    For staged/unstaged we read the XY field of types 1, 2, u: X = staged
    state, Y = unstaged state. A non-'.' character means dirty in that slot.
    """
    branch = "unknown"
    head_full = ""
    ahead = "0"
    behind = "0"
    staged = 0
    unstaged = 0
    untracked = 0

    for line in out.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head ") :].strip() or "unknown"
        elif line.startswith("# branch.oid "):
            head_full = line[len("# branch.oid ") :].strip()
        elif line.startswith("# branch.ab "):
            # "# branch.ab +N -M" — note the - prefix is part of the format,
            # behind count itself is unsigned.
            parts = line[len("# branch.ab ") :].split()
            if len(parts) == 2:
                ahead = parts[0].lstrip("+") or "0"
                behind = parts[1].lstrip("-") or "0"
        elif line.startswith("? "):
            untracked += 1
        elif line.startswith(("1 ", "2 ", "u ")):
            # XY is the second whitespace-separated field.
            fields = line.split(None, 2)
            if len(fields) >= 2 and len(fields[1]) >= 2:
                xy = fields[1]
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    unstaged += 1

    # The "(initial commit)" case where head_full is "(initial)" — short-sha
    # would be meaningless, so just record the literal token.
    if head_full == "(initial)":
        head = "(initial)"
    elif head_full:
        head = head_full[:8]
    else:
        head = "unknown"

    return {
        "branch": branch,
        "head": head,
        "staged": str(staged),
        "unstaged": str(unstaged),
        "untracked": str(untracked),
        "ahead": ahead,
        "behind": behind,
    }


def _capture_state(repo_root: str) -> dict[str, str]:
    """Capture all fingerprint inputs in 2 git subprocess calls (3 if Jmodot).

    Empty values on failure collapse into the same fingerprint until something
    succeeds — so a transient git failure won't trigger spurious deltas.
    """
    porcelain_out = _run(
        ["git", "status", "--porcelain=v2", "--branch", "--untracked-files=normal"],
        cwd=repo_root,
    )
    if porcelain_out:
        state = _parse_porcelain_v2(porcelain_out)
    else:
        # Failed: fall back to "unknown" placeholders. These are stable across
        # prompts, so a broken git won't generate perpetual deltas.
        state = {
            "branch": "unknown",
            "head": "unknown",
            "staged": "0",
            "unstaged": "0",
            "untracked": "0",
            "ahead": "0",
            "behind": "0",
        }

    # Jmodot submodule HEAD — one more call, only if the submodule directory
    # is non-empty. Un-init submodule records empty string (stable across
    # prompts; no perpetual delta noise).
    jmodot_path = Path(repo_root) / "Jmodot"
    jmodot_head = ""
    if jmodot_path.exists() and any(jmodot_path.iterdir()):
        jmodot_full = _run(
            ["git", "rev-parse", "HEAD"], cwd=str(jmodot_path)
        )
        if jmodot_full:
            jmodot_head = jmodot_full[:8]

    state["jmodot_head"] = jmodot_head
    return state


def _fingerprint(state: dict[str, str]) -> str:
    """Pipe-joined canonical order. Order matters — must match across versions."""
    return SEP.join(
        [
            state["branch"],
            state["head"],
            state["staged"],
            state["unstaged"],
            state["untracked"],
            state["ahead"],
            state["behind"],
            state["jmodot_head"],
        ]
    )


def _parse_fingerprint(fp: str) -> dict[str, str] | None:
    """Inverse of _fingerprint. None if the cached line is malformed (treat as no prior)."""
    parts = fp.split(SEP)
    if len(parts) != 8:
        return None
    return {
        "branch": parts[0],
        "head": parts[1],
        "staged": parts[2],
        "unstaged": parts[3],
        "untracked": parts[4],
        "ahead": parts[5],
        "behind": parts[6],
        "jmodot_head": parts[7],
    }


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def _cache_path(repo_root: str, cwd: str) -> Path:
    """One cache file per CWD so worktrees don't trample each other's state."""
    cwd_hash = hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:12]
    cache_dir = Path(repo_root) / ".claude" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"git-state-{cwd_hash}.txt"


def _read_cached(path: Path) -> tuple[str, str] | None:
    """Returns (session_id, fingerprint) or None. Cache format: two lines —
    session id then fingerprint. Single-line legacy caches parse as no-prior
    (re-primed silently on next prompt)."""
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) != 2:
            return None
        return (lines[0].strip(), lines[1].strip())
    except Exception:
        return None


def _write_cached(path: Path, session_id: str, fp: str) -> None:
    try:
        path.write_text(f"{session_id}\n{fp}", encoding="utf-8")
    except Exception:
        pass  # cache write failure is non-fatal — we'll just re-emit next time


# ---------------------------------------------------------------------------
# Delta block formatting
# ---------------------------------------------------------------------------


def _format_delta(prev: dict[str, str], curr: dict[str, str]) -> str:
    """Build a <git-state-delta> block listing only the fields that actually changed.

    A field is mentioned if-and-only-if its value differs from the previous
    fingerprint. Fields that didn't move stay out of the block — keeps the
    signal sharp.
    """
    lines = ["<git-state-delta>"]

    if curr["branch"] != prev["branch"]:
        lines.append(f"Branch: {curr['branch']} (was: {prev['branch']})")

    if curr["head"] != prev["head"]:
        lines.append(f"HEAD: {curr['head']} (was: {prev['head']})")

    # Working-tree counts are reported as a single line if any of the three changed.
    wt_changed = (
        curr["staged"] != prev["staged"]
        or curr["unstaged"] != prev["unstaged"]
        or curr["untracked"] != prev["untracked"]
    )
    if wt_changed:
        lines.append(
            f"Working tree: staged={curr['staged']} unstaged={curr['unstaged']} "
            f"untracked={curr['untracked']} "
            f"(was: staged={prev['staged']} unstaged={prev['unstaged']} "
            f"untracked={prev['untracked']})"
        )

    if curr["ahead"] != prev["ahead"] or curr["behind"] != prev["behind"]:
        lines.append(
            f"Upstream: ahead={curr['ahead']} behind={curr['behind']} "
            f"(was: ahead={prev['ahead']} behind={prev['behind']})"
        )

    if curr["jmodot_head"] != prev["jmodot_head"]:
        prev_j = prev["jmodot_head"] or "(none)"
        curr_j = curr["jmodot_head"] or "(none)"
        lines.append(f"Jmodot HEAD: {curr_j} (was: {prev_j})")

    lines.append("</git-state-delta>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # We only need session_id from stdin — git state is independent of what
    # the user typed.
    session_id = ""
    try:
        data = json.load(sys.stdin)
        if isinstance(data, dict):
            session_id = (data.get("session_id") or "")[:8]
    except Exception:
        pass

    repo_root = _git_toplevel()
    if not repo_root:
        sys.exit(0)  # not in a git repo; nothing to report

    cwd = os.getcwd()
    cache_file = _cache_path(repo_root, cwd)

    curr = _capture_state(repo_root)
    curr_fp = _fingerprint(curr)

    cached = _read_cached(cache_file)

    # First-prompt-of-session path: SessionStart already injected the initial
    # context, so we'd just be repeating ourselves. The cache persists across
    # sessions, so "first prompt" is detected by session-id mismatch — without
    # this, the first prompt of a NEW session emits a delta against the
    # PREVIOUS session's end state, duplicating the SessionStart context.
    if cached is None or cached[0] != session_id:
        _write_cached(cache_file, session_id, curr_fp)
        sys.exit(0)

    prev_fp = cached[1]

    # No drift: silent.
    if prev_fp == curr_fp:
        sys.exit(0)

    # Drift detected — emit the delta block listing only changed fields.
    prev = _parse_fingerprint(prev_fp)
    if prev is None:
        # Cache was malformed; re-prime silently rather than emit a confused block.
        _write_cached(cache_file, session_id, curr_fp)
        sys.exit(0)

    print(_format_delta(prev, curr))
    _write_cached(cache_file, session_id, curr_fp)
    sys.exit(0)


if __name__ == "__main__":
    main()
