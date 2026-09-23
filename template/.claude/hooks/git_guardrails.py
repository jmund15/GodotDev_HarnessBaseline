#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block git operations that destroy local state, and
deny a harness commit that lacks a fresh proof stamp or was authored without
`instruction_quality` loaded (`_skill_verdict`; retire-when review-by 2027-03-15).

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
import time

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
    from _hook_state import read_json_salvage, state_path, HARNESS_DIRS as DIRS  # noqa: E402
    _STATE_IMPORT_ERROR = None
except Exception as _exc:
    read_json_salvage, state_path, DIRS = None, None, None
    _STATE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
try:
    from harness_tests import tree_entries  # noqa: E402
    _HARNESS_IMPORT_ERROR = None
except Exception as _exc:
    tree_entries = None
    _HARNESS_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
from _git_commit import (  # noqa: E402
    command_git_env, export_assignments, git_environ,
    segments, git_invocation, cd_target, executable_text, run_git,
    staged_paths, incoming_paths, bypass_declared,
    resolve_cd, literal_assignments, expand_literal,
)

BYPASS_VAR = "HARNESS_ALLOW_UNSTAMPED_HARNESS"


def short_flag_chars(args):
    """Characters of single-dash bundled flags, e.g. -fdx -> {'f','d','x'}."""
    chars = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            chars.update(a[1:])
    return chars


_SIDE_UNSAFE = (".tscn", ".tres")


# Options a path-scoped discard may carry and still be judged by the worktree diff alone. Any other
# (`--staged`/`-S`, `--worktree`/`-W`, a short `-s <tree>`, `-f`, a bundle) reads as a loss.
_NO_LOSS_OPTS = {"--theirs", "--ours", "-q", "--quiet"}


def _discards_nothing(sub, rest, cwd, git_env=None):
    """True when a path-scoped checkout/restore loses no edit: every named path is an unmerged
    path taken with --theirs/--ours (not .tscn/.tres, where one side silently drops Export wiring),
    or has no difference from its source beyond CR line endings. Any doubt reads as a loss."""
    if cwd is None or (sub == "checkout" and "--" not in rest):
        return False
    split = rest.index("--") if "--" in rest else len(rest)
    head = rest[:split]
    paths = rest[split + 1:] if "--" in rest else [a for a in rest if not a.startswith("-")]
    opts = [a for a in head if a.startswith("-")]
    refs = [a for a in head if not a.startswith("-")] if sub == "checkout" else []
    source = [a.split("=", 1)[1] for a in opts if a.startswith("--source=")]
    if (not paths or len(refs) > 1
            or any(o not in _NO_LOSS_OPTS and not o.startswith("--source=") for o in opts)
            or any(p in (".", ":/", "/") or re.search(r"[*?\[\]]", p) for p in paths)):
        return False
    side = "--theirs" in opts or "--ours" in opts
    base = refs[0] if refs else (source[0] if source else None)
    for path in paths:
        if side:
            if path.lower().endswith(_SIDE_UNSAFE):
                return False
            unmerged = run_git(["ls-files", "-u", "--", path], cwd, git_env)
            if not (unmerged or "").strip():
                return False
            continue
        diff = ["diff", "--quiet", "--ignore-cr-at-eol"] + ([base] if base else []) + ["--", path]
        if run_git(diff, cwd, git_env) is None:
            return False
    return True


def verdict(args, cwd=None, git_env=None):
    """Return a one-line block message, or None if allowed. `cwd` is the repo the command
    targets; without it a checkout/restore discard is judged unsafe."""
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub in ("checkout", "restore") and _discards_nothing(sub, rest, cwd, git_env):
        return None

    if sub == "reset" and "--hard" in rest:
        return ("BLOCKED `git reset --hard` — discards every uncommitted change in the shared worktree "
                "with no undo. Use `git reset --keep` to move HEAD and keep local edits.")

    if sub == "clean":
        flags = short_flag_chars(rest)
        dry = "n" in flags or "--dry-run" in rest
        if not dry and ("f" in flags or "--force" in rest):
            return ("BLOCKED `git clean -f` — deletes untracked files permanently (git has no copy). "
                    "Run the same command with `-n` first, then ask the user to confirm.")

    if sub == "checkout" and ("--" in rest or "." in rest):
        return ("BLOCKED `git checkout` worktree discard — overwrites uncommitted edits to those paths, "
                "which may be another session's. Copy the file aside (`cp <path> .claude/scratch/`), then "
                "edit your own change back out in place; ask the user before removing an edit that is not "
                "yours. Never `git stash`: every session in this checkout shares its list.")

    staged_only = ({"--staged", "-S"} & set(rest)) and not ("--worktree" in rest or "W" in short_flag_chars(rest))
    if sub == "restore" and not staged_only:
        return ("BLOCKED `git restore` without `--staged` — discards uncommitted worktree edits, which may "
                "be another session's. Copy the file aside (`cp <path> .claude/scratch/`), then edit your "
                "own change back out in place; `git restore --staged` alone is allowed.")

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


def _has_proof(tests_dir, name, git_env=None):
    """A proof counts only when git tracks it: `.claude/tests/` is gitignored, so a file that
    exists on disk but was never `git add -f`ed reaches no other checkout."""
    import glob
    hits = (glob.glob(os.path.join(tests_dir, "test_%s*.py" % name))
            or glob.glob(os.path.join(tests_dir, "%s_test.*" % name)))
    return any(run_git(["ls-files", "--error-unmatch", "--", h], tests_dir, git_env) is not None
               for h in hits)


def _stamp_path(repo_root):
    """The target repo's stamp, unless the runner explicitly selected another path."""
    return os.environ.get("HARNESS_TEST_STAMP") or os.path.join(
        repo_root, ".claude", "logs", "harness_tests_stamp.json")


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


def _md_density_verdict(repo_root, touched, git_env=None):
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
            head = subprocess.run(["git", "show", "HEAD:" + norm], cwd=repo_root, env=git_environ(git_env),
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


def _removed(repo_root, norm, git_env=None):
    """True when the commit deletes `norm`: absent from both worktree and index. Its proof may
    leave in the same commit. A git failure reads as present, so the proof is still required."""
    if os.path.exists(os.path.join(repo_root, norm)):
        return False
    listed = run_git(["ls-files", "--", norm], repo_root, git_env)
    return listed is not None and not listed.strip()


def _skill_verdict(session_id, touched):
    """Deny a harness commit from a session that never loaded `instruction_quality`.

    `harness_edit_skill_reminder.py` gates the Write|Edit call, which every other write route
    dodges: a Bash heredoc, `sed -i`, a python script run through Bash. The staged set is the one
    signal no write route can dodge, so the commit is the backstop.

    Silent when no session state file exists: there is no session to judge, and CI runs this
    battery that way. Damaged or skill-less state denies — the enforcement side fails closed.
    """
    if read_json_salvage is None or state_path is None:
        return None
    path = state_path(session_id)
    if not os.path.exists(path):
        return None
    loaded = (read_json_salvage(path) or {}).get("skills_loaded")
    if isinstance(loaded, list) and "instruction_quality" in loaded:
        return None
    return ("BLOCKED harness commit — `instruction_quality` was never loaded this session and "
            "these staged paths are harness surfaces:\n  %s%s\n"
            "Load the skill, check the edits against the sections for their file class, then commit."
            % (", ".join(touched[:4]), " …" if len(touched) > 4 else ""))


def harness_verdict(sub, rest, cwd, inline_env=None, git_env=None, session_id=None):
    """Deny a commit that touches `.claude/{hooks,tools,scripts,workflows,tests}` or
    `.claude/settings.json` without a fresh `harness_tests.py` stamp and per-hook proof
    coverage. Every failure mode here denies — an unrecognized git state is not a pass. `git_env` is
    the command's GIT_* variables: a commit through another index is judged against that index."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None

    if _STATE_IMPORT_ERROR:
        return ("BLOCKED commit — harness guard state unavailable (%s). Repair "
                "hooks/_hook_state.py; for that repair commit prefix `%s=1`."
                % (_STATE_IMPORT_ERROR, BYPASS_VAR))

    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        return ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot "
                "verify the harness stamp.")
    repo_root = root.strip()

    if sub == "commit":
        paths, failed_cmd = staged_paths(rest, cwd, git_env)
    else:
        paths, failed_cmd = incoming_paths(sub, rest, cwd, git_env)
    if paths is None:
        return "BLOCKED harness commit — `git %s` failed; cannot verify the harness stamp." % failed_cmd

    touched = sorted(p for p in paths if _under_dirs(p))
    if not touched:
        return None

    if sub != "commit":
        # A merge / cherry-pick / revert only re-commits content that was judged when it was
        # committed on its source ref; a conflict stops it before any commit, and the commit that
        # closes the merge is judged below against MERGE_HEAD.
        return None

    touched = _not_from_merge_head(touched, cwd, git_env)
    if not touched:
        return None

    if _HARNESS_IMPORT_ERROR:
        return ("BLOCKED harness commit — stamp machinery unavailable (%s). Repair "
                "scripts/harness_tests.py; for that repair commit prefix `%s=1`."
                % (_HARNESS_IMPORT_ERROR, BYPASS_VAR))

    skill = _skill_verdict(session_id, touched)
    if skill:
        return skill

    dense = _md_density_verdict(repo_root, touched, git_env)
    if dense:
        return dense

    tests_dir = os.path.join(repo_root, ".claude", "tests")
    missing = []
    for norm in touched:
        for prefix in (".claude/hooks/", ".claude/tools/"):
            if norm.startswith(prefix) and norm.endswith(".py"):
                name = os.path.basename(norm)[:-3]
                if (not name.startswith("_") and not _has_proof(tests_dir, name, git_env)
                        and not _removed(repo_root, norm, git_env)):
                    missing.append((norm, name))
                break

    if missing:
        lines = ["BLOCKED harness commit — no re-runnable proof for:"]
        for norm, name in missing:
            lines.append("  %s -> expected .claude/tests/test_%s*.py" % (norm, name))
        return "\n".join(lines)

    # The runner must be its OWN tool call: this guard judges the command before it runs, so a
    # `harness_tests.py && git commit` chain is denied against the stamp that exists now.
    rerun = ("run `python3 .claude/scripts/harness_tests.py --staged` (proofs bound to the staged files, "
             "seconds) or the full runner as its own call, then commit")
    stamp = read_json_salvage(_stamp_path(repo_root))
    reason = "no stamp" if not stamp or not stamp.get("tree_hash") else _stale(stamp, repo_root, touched)
    if not reason:
        return None
    red = _auto_stamp(repo_root, touched)
    if red is None:
        stamp = read_json_salvage(_stamp_path(repo_root))
        if stamp and stamp.get("tree_hash") and not _stale(stamp, repo_root, touched):
            return None
    elif red:
        return "BLOCKED harness commit — a proof bound to the committed files failed:\n%s" % red
    return "BLOCKED harness commit — %s; %s." % (reason, rerun)


# Seconds the guard may spend stamping a stale commit itself. It must stay under the PreToolUse
# timeout registered for pre_bash_dispatch.py (75 s): a hook the harness kills is a non-blocking
# error, and the commit would then run unstamped.
AUTO_STAMP_BUDGET = 55
_STARTED = time.monotonic()  # the budget covers the whole hook run, shared by every commit segment


def _auto_stamp(repo_root, touched):
    """Run the proofs bound to `touched` and refresh their stamp entries. None when they passed;
    the failing tail when a proof failed; "" when the run was skipped or ran over budget, which
    leaves today's deny in place."""
    try:
        budget = float(os.environ.get("HARNESS_AUTO_STAMP_BUDGET", AUTO_STAMP_BUDGET))
    except ValueError:
        budget = 0
    budget -= time.monotonic() - _STARTED
    if budget <= 0 or not os.path.exists(_stamp_path(repo_root)):  # a scoped run extends a full one
        return ""
    runner =os.path.join(_CLAUDE_DIR, "scripts", "harness_tests.py")
    env = dict(os.environ, HARNESS_TEST_STAMP=_stamp_path(repo_root))
    try:
        r = subprocess.run([sys.executable, runner, "--repo", repo_root, "--for"] + touched,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=budget, cwd=repo_root, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode == 0:
        return None
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    return "\n".join(lines[-8:]) or "harness_tests.py exited %d" % r.returncode


def _not_from_merge_head(touched, cwd, git_env):
    """The touched harness paths whose staged blob differs from MERGE_HEAD's, i.e. what THIS commit
    authors. During a merge the rest arrived from a ref where they were already judged; without a
    MERGE_HEAD every path is this commit's own."""
    if run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd, git_env) is None:
        return touched
    own = []
    for norm in touched:
        staged = run_git(["rev-parse", "-q", "--verify", ":" + norm], cwd, git_env)
        theirs = run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD:" + norm], cwd, git_env)
        if staged is None or theirs is None or staged.strip() != theirs.strip():
            own.append(norm)
    return own


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
    exported = {}
    assigned_once = literal_assignments(command)
    born = set()  # repos this same command creates with `git init`: no harness history to stamp

    def expand(token):
        return expand_literal(token, assigned_once) or token

    for segment in segments(command):
        moved = cd_target(segment)
        if moved is not None:
            cwd = resolve_cd(cwd, expand(moved))
            continue
        assigned = export_assignments(segment)
        if assigned is not None:
            exported.update(assigned)
            continue
        parsed = git_invocation(segment)
        if parsed is None:
            continue
        args, chdir, inline_env = parsed
        target = resolve_cd(cwd, expand(chdir)) if chdir else cwd
        key = os.path.normcase(os.path.abspath(target))
        if args and args[0] == "init":
            named = [a for a in args[1:] if not a.startswith("-")]
            made = resolve_cd(target, expand(named[0])) if named else target
            top = run_git(["rev-parse", "--show-toplevel"], made, command_git_env(exported, inline_env))
            made_key = os.path.normcase(os.path.abspath(made))
            if top is None or os.path.normcase(os.path.abspath(top.strip())) != made_key:
                born.add(made_key)  # a new repo; `git init` on an existing root changes nothing
            continue
        message = verdict(args, target, command_git_env(exported, inline_env))
        if (not message and args and args[0] in ("commit", "merge", "cherry-pick", "revert")
                and key not in born):
            message = harness_verdict(args[0], args[1:], target, inline_env,
                                      command_git_env(exported, inline_env),
                                      input_data.get("session_id"))
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
