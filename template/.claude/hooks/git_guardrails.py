#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block git operations that destroy local state, and
deny a harness commit that lacks a fresh proof stamp.

Why:
- Uncommitted and unpushed work has no remote copy and no undo. A single
  `git reset --hard` / `git clean -f` / force-push discards work that no other
  session, branch, or reflog entry can recover. Concurrent sessions share one
  checkout, so the loss is not even necessarily the caller's own work.
- Every blocked operation has a non-destructive alternative that preserves the
  same intent (stash, --keep, dry-run, unstage), so blocking costs one extra
  step and never a re-do.

Scope: destructive-LOCAL and history-rewrite only. Plain `git push`, `git
status`, `git restore --staged`, `git clean -n`, `git branch -d` stay allowed —
they are recoverable or non-destructive.

Matching: `_git_commit.py` owns the command parse (heredoc bodies stripped, segments split,
`git -C <path>` and a preceding `cd <path>` retarget the repo, inline `VAR=1` prefixes read).
Pathological quoting may over-block; the message names the alternative, so the recovery is
one line.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import os
import re
import subprocess
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
sys.path.insert(0, os.path.join(_CLAUDE_DIR, "scripts"))
sys.path.insert(0, _HOOKS_DIR)
# Two import tiers, because they fail differently. A broken runner or state helper must deny
# HARNESS commits, never take the other git guards down with it: an import traceback here exits
# 1, which the harness treats as a non-blocking hook error, and force-push / reset --hard would
# sail through unguarded. `DIRS` has one home (`_hook_state.HARNESS_DIRS`); without it the guard
# cannot tell a harness commit from any other, so every commit is denied until it is repaired.
try:
    from _hook_state import read_json_salvage, HARNESS_DIRS as DIRS  # noqa: E402
    _STATE_IMPORT_ERROR = None
except Exception as _exc:
    read_json_salvage, DIRS = None, None
    _STATE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
try:
    from harness_tests import tree_entries, STAMP_PATH  # noqa: E402
    _HARNESS_IMPORT_ERROR = None
except Exception as _exc:
    tree_entries, STAMP_PATH = None, None
    _HARNESS_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
from _git_commit import (  # noqa: E402
    segments, git_invocation, cd_target, executable_text, run_git,
    staged_paths, incoming_paths, bypass_declared,
)

BYPASS_VAR = "HARNESS_ALLOW_UNSTAMPED_HARNESS"


def short_flag_chars(args):
    """Characters of single-dash bundled flags, e.g. -fdx -> {'f','d','x'}."""
    chars = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            chars.update(a[1:])
    return chars


def verdict(args):
    """Return a one-line block message, or None if allowed."""
    if not args:
        return None
    sub, rest = args[0], args[1:]

    if sub == "reset" and "--hard" in rest:
        return ("BLOCKED `git reset --hard` — discards every uncommitted change with no undo. "
                "Use `git stash` to keep them, or `git reset --keep` to move HEAD safely.")

    if sub == "clean":
        flags = short_flag_chars(rest)
        dry = "n" in flags or "--dry-run" in rest
        if not dry and ("f" in flags or "--force" in rest):
            return ("BLOCKED `git clean -f` — deletes untracked files permanently (git has no copy). "
                    "Run the same command with `-n` first, then ask the user to confirm.")

    if sub == "checkout" and ("--" in rest or "." in rest):
        return ("BLOCKED `git checkout` worktree discard — overwrites uncommitted edits to those paths. "
                "Use `git stash push <path>` instead.")

    if sub == "restore" and not ({"--staged", "-S"} & set(rest)):
        return ("BLOCKED `git restore` without `--staged` — discards uncommitted worktree edits. "
                "Use `git stash push <path>`; `git restore --staged` alone is allowed.")

    if sub == "push":
        forcing = {"--force", "--force-with-lease", "--force-if-includes"} & set(rest)
        forcing = forcing or any(
            a.startswith(("--force-with-lease=", "--force-if-includes=")) for a in rest
        )
        forcing = forcing or "f" in short_flag_chars(rest)
        if forcing:
            return ("BLOCKED force-push — rewrites remote history and can erase commits other "
                    "checkouts still depend on. Rebase onto the remote and push normally.")

    if sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
        return ("BLOCKED `git branch -D` — force-deletes a branch whose commits may be unmerged "
                "and unpushed. Use `git branch -d`; if it refuses, the work is not merged.")

    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        return (f"BLOCKED `git stash {rest[0]}` — stashed work is unreferenced once dropped. "
                "Apply it (`git stash pop`) or leave it; ask the user before discarding.")

    if sub == "reflog" and "expire" in rest:
        return ("BLOCKED `git reflog expire` — the reflog is the last recovery path for lost "
                "commits. Leave expiry to git's own schedule.")

    if sub == "gc" and any(a == "--prune" or a.startswith("--prune=") for a in rest):
        return ("BLOCKED `git gc --prune` — permanently removes unreachable objects that reflog "
                "recovery depends on. Run `git gc` without `--prune`.")

    if sub in ("filter-branch", "filter-repo"):
        return (f"BLOCKED `git {sub}` — rewrites every commit in place, invalidating all existing "
                "clones and hashes. Ask the user; this is never an in-session operation.")

    return None


def _under_dirs(path):
    norm = path.replace("\\", "/")
    return any(norm == d or norm.startswith(d + "/") for d in DIRS)


def _has_proof(tests_dir, name):
    """A proof counts only when git tracks it: `.claude/tests/` is gitignored, so a file that
    exists on disk but was never `git add -f`ed reaches no other checkout."""
    import glob
    hits = (glob.glob(os.path.join(tests_dir, "test_%s*.py" % name))
            or glob.glob(os.path.join(tests_dir, "%s_test.*" % name)))
    return any(run_git(["ls-files", "--error-unmatch", "--", h], tests_dir) is not None
               for h in hits)


def _stale(stamp, repo_root, touched):
    """None when the stamp covers `touched`, else the reason. A stamp is fresh for a commit when
    every touched harness file's digest matches the green run — a peer's edit to an untouched
    hook does not invalidate this commit's proof."""
    try:
        current = tree_entries(repo_root)
    except Exception as exc:
        return "tree hash failed (%s); cannot verify the stamp." % exc
    stamped = stamp.get("files")
    if not isinstance(stamped, dict):
        # Legacy stamp (whole-tree hash only): fall back to the coarse comparison.
        import hashlib
        blob = json.dumps(sorted(current.items())).encode("utf-8")
        if stamp.get("tree_hash") == hashlib.sha256(blob).hexdigest():
            return None
        return "stale stamp. Harness files changed since the last green run"
    changed = [p for p in touched if stamped.get(p) != current.get(p)]
    if changed:
        return "stale stamp. Changed since the last green run: " + ", ".join(sorted(changed)[:6])
    return None


def _md_density_verdict(repo_root, touched):
    """Block a commit whose staged `.claude/**.md` ADDS units denser than the §5 audit trigger.

    Judges the DELTA, never the whole file: a surface that was already dense is not this commit's
    fault, and blocking on it would make the gate unpassable and therefore bypassed. Reuses
    harness_growth_guard's own UNIT regex and EXCLUDED list so there is one definition of a
    "rule-unit", not a second that drifts.
    """
    try:
        sys.path.insert(0, os.path.join(repo_root, ".claude", "hooks"))
        import harness_growth_guard as hg
    except Exception:
        return None                      # advisory machinery missing: never block a commit on it

    over = []
    for norm in touched:
        if not norm.startswith(".claude/") or not norm.endswith(".md"):
            continue
        if any(seg in hg.EXCLUDED for seg in norm.split("/")[1:-1]):
            continue                     # auto-memory/, plans/, scratch/ are evidence homes (§5)
        try:
            head = subprocess.run(["git", "show", "HEAD:" + norm], cwd=repo_root,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace")
            before = head.stdout if head.returncode == 0 else ""
            with open(os.path.join(repo_root, norm), encoding="utf-8", errors="replace") as fh:
                after = fh.read()
        except Exception:
            continue

        def units(t):
            return [ln for ln in t.splitlines() if hg.UNIT.match(ln)]

        def key(ln):
            """A unit's identity: its bold lead, else its first words. Lets a unit be tracked
            across an edit, so SHRINKING an already-dense paragraph is not read as adding one."""
            m = re.match(r"^\s*(?:[-*] |\d+\. )?\*\*([^*]+)\*\*", ln)
            return (m.group(1) if m else ln.strip()[:60]).lower()

        was_size = {}
        for ln in units(before):
            was_size[key(ln)] = len(ln.encode())
        # Flag a unit only if it is over cap AND this commit made it that way -- new, or grown.
        # A pre-existing dense paragraph is not this commit's fault, and blocking on it would make
        # the gate unpassable, which is how a gate gets bypassed instead of obeyed.
        for ln in units(after):
            n = len(ln.encode())
            if n <= hg.DENSITY_AUDIT:
                continue
            prev = was_size.get(key(ln))
            if prev is not None and n <= prev:
                continue
            over.append((norm, n, ln.strip()[:90]))

    if not over:
        return None
    lines = ["BLOCKED harness commit — a staged .md adds a rule-unit over the "
             "%d B/unit audit trigger (instruction_quality §5):" % hg.DENSITY_AUDIT]
    for norm, n, head in over:
        lines.append("  %s: %d B — %s…" % (norm, n, head))
    lines.append("Fix per §6: delete whole sentences (restatement, the summary of its own headline,")
    lines.append("provenance narrative) or split one packed unit into two. Evidence belongs in")
    lines.append("auto-memory/archive/, cited by name — not inline on a loaded surface.")
    lines.append("Deliberate exception: prefix the commit `%s=1`." % BYPASS_VAR)
    return "\n".join(lines)


def harness_verdict(sub, rest, cwd, inline_env=None):
    """Deny a commit that touches `.claude/{hooks,tools,scripts,workflows,tests}` or
    `.claude/settings.json` without a fresh `harness_tests.py` stamp and per-hook proof
    coverage. Every failure mode here denies — an unrecognized git state is not a pass."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None

    if _STATE_IMPORT_ERROR:
        return ("BLOCKED commit — harness guard state unavailable (%s). Repair "
                "hooks/_hook_state.py; for that repair commit prefix `%s=1`."
                % (_STATE_IMPORT_ERROR, BYPASS_VAR))

    root = run_git(["rev-parse", "--show-toplevel"], cwd)
    if root is None:
        return ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot "
                "verify the harness stamp.")
    repo_root = root.strip()

    if sub == "commit":
        paths, failed_cmd = staged_paths(rest, cwd)
    else:
        paths, failed_cmd = incoming_paths(sub, rest, cwd)
    if paths is None:
        return "BLOCKED harness commit — `git %s` failed; cannot verify the harness stamp." % failed_cmd

    touched = sorted(p for p in paths if _under_dirs(p))
    if not touched:
        return None

    if sub != "commit":
        # The stamp describes THIS tree; the merged result does not exist yet to be proven.
        return ("BLOCKED `git %s` — it brings harness changes (%s%s) in unproven. Run it with "
                "`--no-commit`, run `python3 .claude/scripts/harness_tests.py`, then `git commit`."
                % (sub, ", ".join(touched[:4]), "…" if len(touched) > 4 else ""))

    if _HARNESS_IMPORT_ERROR:
        return ("BLOCKED harness commit — stamp machinery unavailable (%s). Repair "
                "scripts/harness_tests.py; for that repair commit prefix `%s=1`."
                % (_HARNESS_IMPORT_ERROR, BYPASS_VAR))

    dense = _md_density_verdict(repo_root, touched)
    if dense:
        return dense

    tests_dir = os.path.join(repo_root, ".claude", "tests")
    missing = []
    for norm in touched:
        for prefix in (".claude/hooks/", ".claude/tools/"):
            if norm.startswith(prefix) and norm.endswith(".py"):
                name = os.path.basename(norm)[:-3]
                if not name.startswith("_") and not _has_proof(tests_dir, name):
                    missing.append((norm, name))
                break

    if missing:
        lines = ["BLOCKED harness commit — no re-runnable proof for:"]
        for norm, name in missing:
            lines.append("  %s -> expected .claude/tests/test_%s*.py" % (norm, name))
        return "\n".join(lines)

    # The runner must be its OWN tool call: this guard judges the command before it runs, so a
    # `harness_tests.py && git commit` chain is denied against the stamp that exists now.
    rerun = ("run `python3 .claude/scripts/harness_tests.py` as its own call, then commit")
    stamp = read_json_salvage(STAMP_PATH)
    if not stamp or not stamp.get("tree_hash"):
        return "BLOCKED harness commit — no stamp; %s." % rerun

    reason = _stale(stamp, repo_root, touched)
    if reason:
        return "BLOCKED harness commit — %s; %s." % (reason, rerun)

    return None


_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(/|$)")


def _resolve_cd(cwd: str, moved: str) -> str:
    """`cwd` after a `cd <moved>`, understanding Git Bash's `/c/...` drive form.

    The Bash tool runs Git Bash, so a `cd` operand is routinely MSYS-absolute. `os.path.join` on
    Windows treats a leading `/` as root-relative to the CURRENT drive and produces
    `C:\\repo\\c\\Users\\...` — a path that does not exist, so `git rev-parse` there fails and this
    guard BLOCKS a commit for a repo it simply failed to find. It blocked a real one.
    """
    moved = os.path.expanduser(moved)
    m = _MSYS_DRIVE.match(moved.replace("\\", "/"))
    if m:
        moved = "%s:\\%s" % (m.group(1).upper(), moved.replace("\\", "/")[3:].replace("/", os.sep))
    return moved if os.path.isabs(moved) else os.path.join(cwd, moved)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = executable_text((input_data.get("tool_input") or {}).get("command") or "")
    if "git" not in command:
        sys.exit(0)

    cwd = input_data.get("cwd") or "."

    for segment in segments(command):
        moved = cd_target(segment)
        if moved is not None:
            cwd = _resolve_cd(cwd, moved)
            continue
        parsed = git_invocation(segment)
        if parsed is None:
            continue
        args, chdir, inline_env = parsed
        message = verdict(args)
        if not message and args and args[0] in ("commit", "merge", "cherry-pick", "revert"):
            target = os.path.join(cwd, chdir) if chdir else cwd
            message = harness_verdict(args[0], args[1:], target, inline_env)
        if message:
            print(message, file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A broken guard must not block every command in the session.
        sys.exit(0)
