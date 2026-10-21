#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block git operations that destroy local state, and
require a green harness close before push (floor guard), remind about admitted doctrine reviews, and advise a harness commit authored without
`instruction_quality` loaded (`_skill_verdict`; retire-when review-by 2027-03-15), name a staged skill or
command body past the client's re-attach cap (`_reattach_cap_verdict`), report cheap bound
proofs at commit that failed, ran incomplete, could not run or were left to the close (process
advisory), and deny a commit
that a git index write (`add`, `rm`, `reset`, `stash`, ...; `_commit_scope.writes_index`) precedes in the
same command: every commit guard reads the index before the command runs, so it would judge the commit
without that write. A repo the same command creates with `git init` is exempt.

Why:
- Uncommitted and unpushed work has no remote copy and no undo. A single
  `git reset --hard` / `git clean -f` / force-push discards work that no other
  session, branch, or reflog entry can recover. Concurrent sessions share one
  checkout, so the loss is not even necessarily the caller's own work.
- Every blocked operation has a non-destructive alternative that preserves the
  same intent (stash, --keep, dry-run, unstage), so blocking costs one extra
  step and never a re-do.

Scope: destructive local actions, history rewrites and harness push coverage. `git
status`, `git restore --staged`, `git clean -n`, `git branch -d` stay allowed —
they are recoverable or non-destructive. So does `git branch -D` of a local branch whose every
commit `git cherry` finds already applied to HEAD or origin/HEAD (a squash-merged branch), or of a
`scratch/<name>` session fixture branch (`orchestration` reference `worktrees.md`).

Matching: `_git_commit.py` owns the command parse (heredoc bodies stripped, segments split,
`git -C <path>` and a preceding `cd <path>` retarget the repo, inline `VAR=1` prefixes read).
Pathological quoting may over-block; the message names the alternative, so the recovery is
one line.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
sys.path.insert(0, os.path.join(_CLAUDE_DIR, "scripts"))
sys.path.insert(0, _HOOKS_DIR)
# Guard imports so a broken helper cannot crash the destructive-state checks. State failure denies
# commits; runner failure denies pushes and only advises local commits.
try:
    from _hook_state import read_json_salvage, state_path, SETUP_SURFACE_FILES as SURFACES, \
        in_harness_dirs  # noqa: E402
    _STATE_IMPORT_ERROR = None
except Exception as _exc:
    read_json_salvage, state_path, SURFACES, in_harness_dirs = None, None, None, (lambda _rel: False)
    _STATE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
try:
    from harness_edit_skill_reminder import is_harness_file  # noqa: E402
except Exception:
    is_harness_file = None
try:
    from _owner_text import classify as owner_row, owner_consent, owner_quote_text  # noqa: E402
except Exception:
    owner_row = owner_consent = owner_quote_text = None
try:
    from harness_tests import close_base, close_paths, commit_advisories, push_destination, unproven_note  # noqa: E402
    _HARNESS_IMPORT_ERROR = None
except Exception as _exc:
    close_base = close_paths = commit_advisories = push_destination = unproven_note = None
    _HARNESS_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
# Which paths a merge commit authors, shared with `baseline_classification_guard.py`. Guarded like
# the two above: a missing helper must deny commit-like invocations, never crash the other git guards.
try:
    from _commit_scope import authored_paths, publishes_index, writes_index  # noqa: E402
    _SCOPE_IMPORT_ERROR = None
except Exception as _exc:
    authored_paths = publishes_index = writes_index = None
    _SCOPE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
from _git_commit import (  # noqa: E402
    git_environ,
    git_invocation, git_invocations, executable_text, run_git,
    staged_paths, incoming_paths, bypass_declared, parse_commit_args, resolve_index,
    resolve_cd, expand_literal, ref_resolves, refs_created, ref_names_created,
)

REF_MAKERS = ("fetch", "update-ref", "branch", "tag")  # subcommands that can create the ref a later merge names

try:
    from _regenerable_clone import session_created_worktree
except Exception:
    session_created_worktree = None
try:
    sys.path.insert(0, os.path.join(_CLAUDE_DIR, 'tools'))
    from sidecar_argv import command_substitutions, shell_segments, executable_index, literal_launcher_body
    _OPAQUE_IMPORT_ERROR = None
except Exception as _exc:
    command_substitutions = shell_segments = executable_index = None
    _OPAQUE_IMPORT_ERROR = '%s: %s' % (type(_exc).__name__, _exc)

BYPASS_VAR = "HARNESS_ALLOW_UNSTAMPED_HARNESS"
# Every commit guard reads the index before the command runs, so a write earlier in the same command
# lands after they judged the commit.
SAME_COMMAND_WRITE = ("BLOCKED commit (CLAUDE.core.md §Shell Discipline: an index write runs in its own Bash call) — `%s` "
                      "runs earlier in this command, and every commit guard reads the index before the command "
                      "runs, so they would judge this commit without that write. Run the `%s` in its own Bash "
                      "call, then commit.")


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
    or has no difference from its source beyond CR line endings, or (checked out of another ref) is a
    path HEAD tracks with no staged or unstaged edit, or a path absent from HEAD, index and worktree.
    Any doubt reads as a loss."""
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
            or any(p in (".", ":/", "/") or re.search(r"[*?\[\]$`%]", p) for p in paths)):
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
        if run_git(diff, cwd, git_env) is None and not (base and _clean_in_head(path, cwd, git_env)):
            return False
    return True


def _clean_in_head(path, cwd, git_env=None):
    """True when HEAD tracks `path` and neither the index nor the worktree holds an edit to it: overwriting it
    from another commit loses nothing git does not still hold (landing a merge onto a shared checkout)."""
    if run_git(["cat-file", "-e", "HEAD:" + path], cwd, git_env) is None:
        # Absent from HEAD, the index and the worktree: the checkout creates it and overwrites nothing.
        return (not os.path.lexists(os.path.join(cwd, path))
                and run_git(["ls-files", "--", path], cwd, git_env) == "")
    return (run_git(["diff", "--quiet", "--ignore-cr-at-eol", "HEAD", "--", path], cwd, git_env) is not None
            and run_git(["diff", "--quiet", "--cached", "HEAD", "--", path], cwd, git_env) is not None)


def _stash_preserved(args, cwd, git_env=None):
    """True when the one stash entry `git stash drop` targets is still held by a ref outside
    refs/stash, so dropping it loses nothing git cannot reach."""
    args = [a for a in args if a not in ("-q", "--quiet")]
    targets = [a for a in args if not a.startswith("-")]
    if len(targets) > 1 or len(targets) != len(args) or not cwd:
        return False
    sha = run_git(["rev-parse", "--verify", "--quiet", (targets or ["stash@{0}"])[0] + "^{commit}"], cwd, git_env)
    if not sha or not sha.strip():
        return False
    holders = run_git(["for-each-ref", "--contains", sha.strip(), "--format=%(refname)"], cwd, git_env)
    return any(ref and ref != "refs/stash" for ref in (holders or "").splitlines())


def _branches_fully_applied(rest, cwd, git_env=None):
    """True when `branch -D` deletes only local branches that are `scratch/<name>` session fixtures
    (`orchestration` reference `worktrees.md`) or whose every commit `git cherry` finds already applied to HEAD or to
    origin/HEAD (a squash-merged or cherry-picked branch, which `-d` refuses).
    Any other flag, a missing branch or a failed git call fails closed."""
    names = [a for a in rest if not a.startswith("-")]
    if not cwd or not names or any(a not in ("-D", "--delete", "--force", "-f") for a in rest if a.startswith("-")):
        return False
    bases = ["HEAD"] + (["refs/remotes/origin/HEAD"]
                        if run_git(["rev-parse", "--verify", "-q", "refs/remotes/origin/HEAD"], cwd, git_env) else [])
    for name in names:
        if run_git(["rev-parse", "--verify", "-q", "refs/heads/" + name], cwd, git_env) is None:
            return False
        if name.startswith("scratch/"):
            continue
        if not any(out is not None and not any(l.startswith("+") for l in out.splitlines())
                   for out in (run_git(["cherry", base, "refs/heads/" + name], cwd, git_env) for base in bases)):
            return False
    return True


def _own_worktree_discard(sub, rest, cwd, git_env, transcript_path):
    if (not session_created_worktree or not cwd or not transcript_path or any((git_env or {}).get(k) for k in
            ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'))):
        return False
    if sub == 'checkout' and '--' not in rest:
        return False
    if sub not in ('checkout', 'restore') or not rest:
        return False
    top = run_git(['rev-parse', '--show-toplevel'], cwd, git_env)
    # A submodule checked out inside a session-created worktree is that worktree's own clone.
    while top and top.strip():
        if session_created_worktree(top.strip(), transcript_path):
            return True
        top = run_git(['rev-parse', '--show-superproject-working-tree'], top.strip(), git_env)
    return False


_APPROVE_LABEL = re.compile(r"\b(?:reset|discard|restore|revert)\b", re.I)
_DECLINE_LABEL = re.compile(r"\b(?:no|not|don't|dont|stop|keep|leave|abort|cancel|skip)\b", re.I)
_OWNER_TAIL_BYTES = 4_000_000


_DELETE_LABEL = re.compile(r"\b(?:delete|remove)\b", re.I)


def _owner_tail(transcript_path):
    """The transcript's last _OWNER_TAIL_BYTES as lines, or None when it cannot be read."""
    try:
        with open(transcript_path, 'rb') as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(max(0, size - _OWNER_TAIL_BYTES))
            return fh.read().decode('utf-8', errors='replace').splitlines()
    except (OSError, TypeError, ValueError):
        return None


def _owner_approved_branch_delete(rest, transcript_path, cwd=None, git_env=None):
    """True when the owner's latest input approves deleting every branch this `branch -D` names
    (`_owner_text.owner_consent`, with a delete or remove verb), and each is a local branch of this repo."""
    names = [a for a in rest if not a.startswith('-')]
    if (not owner_consent or not transcript_path or not cwd or not names
            or any(a not in ('-D', '--delete', '--force', '-f') for a in rest if a.startswith('-'))
            or any(run_git(['rev-parse', '--verify', '-q', 'refs/heads/' + n], cwd, git_env) is None for n in names)):
        return False
    lines = _owner_tail(transcript_path)
    return bool(lines) and owner_consent(lines, names, _DELETE_LABEL, _DECLINE_LABEL)


def _owner_approved_discard(sub, rest, transcript_path, cwd=None, git_env=None):
    """True when the owner's latest input approves discarding every file this checkout/restore names, each
    by its absolute path in this checkout (`_owner_text.owner_consent`, with a reset, discard, restore or
    revert verb). Literal tracked-file paths only: a pathspec of `.`, a directory or a glob is never named
    exactly enough to consent to, and a relative name would also match the same file in every other
    checkout."""
    if not owner_consent or not transcript_path or sub not in ('checkout', 'restore'):
        return False
    if sub == 'checkout':
        if '--' not in rest:
            return False
        paths = rest[rest.index('--') + 1:]
    else:
        paths = [a for a in rest if not a.startswith('-')]
    if not cwd or not paths or any(
            p in ('.', '..', ':/') or re.search(r'[*?\[]', p) or p.endswith('/') or '..' in Path(p).parts
            or os.path.isdir(os.path.join(cwd, p))
            or run_git(['ls-files', '--error-unmatch', '--', p], cwd, git_env) is None for p in paths):
        return False
    lines = _owner_tail(transcript_path)
    norm = [os.path.abspath(os.path.join(cwd, p)).replace('\\', '/') for p in paths]
    return bool(lines) and owner_consent(lines, norm, _APPROVE_LABEL, _DECLINE_LABEL)


def verdict(args, cwd=None, git_env=None, transcript_path=None):
    """Return a one-line block message, or None if allowed. `cwd` is the repo the command
    targets; without it a checkout/restore discard is judged unsafe."""
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub in ("checkout", "restore"):
        redirected = any((git_env or {}).get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE'))
        if not redirected and (_discards_nothing(sub, rest, cwd, git_env)
                or _own_worktree_discard(sub, rest, cwd, git_env, transcript_path)
                or _owner_approved_discard(sub, rest, transcript_path, cwd, git_env)):
            return None

    if sub == "reset" and "--hard" in rest:
        return ("BLOCKED `git reset --hard` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "discards every uncommitted change in the shared worktree with no undo. Use "
                "`git reset --keep` to move HEAD and keep local edits.")

    if sub == "clean":
        flags = short_flag_chars(rest)
        dry = "n" in flags or "--dry-run" in rest
        if not dry and ("f" in flags or "--force" in rest):
            return ("BLOCKED `git clean -f` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                    "deletes untracked files permanently (git has no copy). Run the same command with "
                    "`-n` first, then ask the user to confirm.")

    if sub == "checkout" and ("--" in rest or "." in rest):
        return ("BLOCKED `git checkout` worktree discard (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "overwrites uncommitted edits to those paths, which may be another session's. Copy the file "
                "aside (`cp <path> .claude/scratch/`), then edit your own change back out in place. For an edit "
                "that is not yours, ask the owner in one message naming each file by its absolute path and the word "
                "discard, reset, restore or revert; their next reply approving it lets this exact-path discard through. Never `git stash`: every session "
                "in this checkout shares its list.")

    staged_only = ({"--staged", "-S"} & set(rest)) and not ("--worktree" in rest or "W" in short_flag_chars(rest))
    if sub == "restore" and not staged_only:
        return ("BLOCKED `git restore` without `--staged` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "discards uncommitted worktree edits, which may be another session's. Copy the file aside "
                "(`cp <path> .claude/scratch/`), then edit your own change back out in place; for an edit "
                "that is not yours, ask the owner in one message naming each file by its absolute path and the word "
                "discard, reset, restore or revert (their next reply approving it lets this exact-path discard through). "
                "`git restore --staged` alone is "
                "allowed.")

    if sub == "push":
        forcing = {"--force", "--force-with-lease", "--force-if-includes"} & set(rest)
        forcing = forcing or any(
            a.startswith(("--force-with-lease=", "--force-if-includes=")) for a in rest
        )
        forcing = forcing or "f" in short_flag_chars(rest)
        if forcing:
            return ("BLOCKED force-push (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites remote "
                    "history and can erase commits other checkouts still depend on. Rebase onto the remote and "
                    "push normally.")

    if (sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest))
            and not _branches_fully_applied(rest, cwd, git_env)
            and not _owner_approved_branch_delete(rest, transcript_path, cwd, git_env)):
        return ("BLOCKED `git branch -D` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — force-deletes a "
                "branch whose commits are not applied to HEAD. Ask the owner in one message naming each branch exactly and "
                "the word delete; their next reply approving it lets this exact delete through.")

    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        if rest[0] == "drop" and _stash_preserved(rest[1:], cwd, git_env):
            return None
        if rest[0] == "drop":
            target = next((a for a in rest[1:] if not a.startswith("-")), "stash@{0}")
            return ("BLOCKED `git stash drop` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — stashed work "
                    "is unreferenced once dropped. With the owner's go-ahead, keep it reachable and drop: "
                    f"`git update-ref refs/preserve/<name> {target}`, then the same drop passes.")
        return ("BLOCKED `git stash clear` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "stashed work is unreferenced once dropped. Leave it; ask the user before discarding.")

    if sub == "reflog" and "expire" in rest:
        return ("BLOCKED `git reflog expire` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — the reflog "
                "is the last recovery path for lost commits. Leave expiry to git's own schedule.")

    if sub == "gc" and any(a == "--prune" or a.startswith("--prune=") for a in rest):
        return ("BLOCKED `git gc --prune` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — permanently "
                "removes unreachable objects that reflog recovery depends on. Run `git gc` without `--prune`.")

    if sub in ("filter-branch", "filter-repo"):
        return (f"BLOCKED `git {sub}` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites every "
                "commit in place, invalidating all existing clones and hashes. Ask the user for explicit "
                "go-ahead before rewriting history; never do it in-session.")

    return None


def _setup_surface(path):
    """One of `_hook_state.SETUP_SURFACE_FILES` (the project's `reference/setup_surfaces.txt`). Not
    harness code, so the harness-edit rules above do not apply to it
    -- its close proof still reads the same constant."""
    return path.replace("\\", "/") in (SURFACES or ())


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


def _md_density_verdict(repo_root, touched, git_env=None):
    """The advisory for a commit whose staged `.claude/**.md` ADDS units denser than the §5 audit trigger.

    Judges the DELTA, never the whole file: a surface that was already dense is not this commit's
    doing. Reuses
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
    lines = ["[harness commit] a staged .md adds a rule-unit over the %d B/unit audit trigger "
             "(instruction_quality §5; a review trigger, never a cap):" % hg.DENSITY_AUDIT]
    for norm, n, head in over:
        lines.append("  %s: %d B — %s…" % (norm, n, head))
    lines.append("Apply §5's split test and §6's cuts; leave a unit whose every sentence is needed.")
    return "\n".join(lines)


def _reattach_cap_verdict(repo_root, touched):
    """The advisory for a commit that touches a skill or command body longer than the client's re-attach cap.

    The client cuts a re-attached body at `SKILL_REATTACH_CHARS` characters, so an over-cap body returns
    truncated after a compaction. The count is `load_census.reattach_over_cap`'s, the
    one definition of over-cap, so this report and the census never disagree. It names only bodies this
    commit touches: a body an earlier commit left over is on the census, not on this commit. A report,
    never a deny.
    """
    try:
        tools = os.path.join(_CLAUDE_DIR, "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import load_census as lc
        rows = lc.reattach_over_cap(Path(repo_root) / ".claude")
        cap = lc.SKILL_REATTACH_CHARS
    except Exception:
        return None                      # advisory machinery missing: never block a commit on it
    wanted = {p.replace("\\", "/") for p in touched}
    over = [r for r in rows if r["path"] in wanted]
    if not over:
        return None
    lines = ["[harness commit] a staged skill or command body is past the client's re-attach cap "
             "(%s characters), so its re-attached copy returns truncated after a compaction:"
             % format(cap, ",")]
    for r in over:
        lines.append("  %s: %s characters (%s over)"
                     % (r["path"], format(r["chars"], ","), format(r["chars"] - cap, ",")))
    lines.append("Split by when the text is read: move detail layers into on-demand reference/ files cited "
                 "from the step that reads them (instruction_quality §5).")
    return "\n".join(lines)


def _removed(repo_root, norm, git_env=None):
    """True when the commit deletes `norm`: absent from both worktree and index. Its proof may
    leave in the same commit. A git failure reads as present, so the proof is still required."""
    if os.path.exists(os.path.join(repo_root, norm)):
        return False
    listed = run_git(["ls-files", "--", norm], repo_root, git_env)
    return listed is not None and not listed.strip()


def _code_ast(source, *, ignore_strings=False):
    """Module AST without docstrings/comments; optionally erase string constant values."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    if ignore_strings:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = ""
    return ast.dump(tree)


def _behavior_unchanged(repo_root, norm, git_env=None, *, ignore_strings=False):
    """True when HEAD, index and worktree ASTs agree, without docstrings/comments.

    `ignore_strings` also erases string values for the proof advisory, never an exemption.
    A new, unparseable or unreadable file reads as changed. A pathspec or `-a` commit
    publishes the worktree, so both copies must match.
    """
    head = run_git(["show", "HEAD:" + norm], repo_root, git_env)
    staged = run_git(["show", ":" + norm], repo_root, git_env)
    try:
        with open(os.path.join(repo_root, norm), encoding="utf-8") as fh:
            work = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    if head is None or staged is None:
        return False
    if any("__doc__" in text for text in (head, staged, work)):
        return False  # the file emits its docstring, so a docstring edit is behavior
    before = _code_ast(head, ignore_strings=ignore_strings)
    return (before is not None and before == _code_ast(staged, ignore_strings=ignore_strings)
            == _code_ast(work, ignore_strings=ignore_strings))


def _baseline_unchanged(repo_root, norm, git_env=None):
    """True when the staged and working copies equal the `tracked` row of the staged baseline lock:
    adopted or pulled baseline content, authored and owed its proof upstream.

    The hash is the lock's: sha256 of the LF-normalized bytes (`tools/baseline_sync.py` `sha`). A missing
    or unparseable lock, a row that is not `tracked`, or a differing copy reads as authored here.
    """
    lock = run_git(["show", ":.claude/baseline.lock.json"], repo_root, git_env)
    try:
        row = json.loads(lock)["files"][norm] if lock else None
    except (ValueError, KeyError, TypeError):
        return False
    if not isinstance(row, dict) or row.get("status") != "tracked" or not row.get("hash"):
        return False
    staged = run_git(["show", ":" + norm], repo_root, git_env)
    try:
        with open(os.path.join(repo_root, norm), "rb") as fh:
            work = fh.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    except OSError:
        return False
    return (staged is not None
            and hashlib.sha256(staged.encode("utf-8")).hexdigest() == row["hash"]
            == hashlib.sha256(work).hexdigest())


def _skill_verdict(session_id, touched):
    """Advise a harness commit from a session that never loaded `instruction_quality`.

    The staged set covers writes that the Write|Edit reminder cannot see.

    Silent when no session state file exists: there is no session to judge, and CI runs this
    battery that way. Damaged or skill-less state yields the advisory.
    """
    if read_json_salvage is None or state_path is None:
        return None
    path = state_path(session_id)
    if not os.path.exists(path):
        return None
    loaded = (read_json_salvage(path) or {}).get("skills_loaded")
    if isinstance(loaded, list) and "instruction_quality" in loaded:
        return None
    # The skill governs what the Write|Edit reminder classifies plus the harness code set; a plan,
    # a memory or the worklog mirror has its own owner.
    surfaces = [p for p in touched if is_harness_file is None or is_harness_file(p)
                or in_harness_dirs(p)]
    if not surfaces:
        return None
    home = ("skills/instruction_quality/SKILL.md" if any(p.endswith(".md") for p in surfaces)
            else "rules/harness_tooling.md")
    return ("[harness commit] (%s) `instruction_quality` was never loaded this session and "
            "these staged paths are harness surfaces:\n  %s%s\n"
            "Load the skill, check the edits against the sections for their file class."
            % (home, ", ".join(surfaces[:4]), " …" if len(surfaces) > 4 else ""))


STAGE_RECORD_SUFFIX = ".paths.json"  # tools/index_commit.py writes `<index>.paths.json` beside its index


def base_verdict(cwd, git_env):
    """Deny publishing a private index over a HEAD other than the base it was read from.

    `index_commit.py stage` records `base_head` beside its index. Any commit after that base is lost
    by publishing the index, deletions of peer-added files included, which `revert_verdict` cannot
    see. Git's own HEAD update then compares against the HEAD the commit read, so a move after this
    check fails the commit instead of losing work. An index without a stage record is left to
    `revert_verdict`; a record that cannot be read denies."""
    index = (git_env or {}).get("GIT_INDEX_FILE")
    if not index or not os.path.isfile(index + STAGE_RECORD_SUFFIX):
        return None
    try:
        with open(index + STAGE_RECORD_SUFFIX, encoding="utf-8") as fh:
            base = (json.load(fh) or {}).get("base_head")
    except (OSError, ValueError, AttributeError):
        base = None
    if not isinstance(base, str):
        base = None
    head = run_git(["rev-parse", "HEAD"], cwd, git_env)
    if base and head is not None and head.strip() == base:
        return None
    reason = ("HEAD is %s but the index was read from base %s" % (head.strip()[:9], base[:9])
              if base and head is not None else "its stage record %s%s cannot be read"
              % (os.path.basename(index), STAGE_RECORD_SUFFIX))
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — %s, so publishing this "
            "private index would undo every commit since its base. Rerun `python3 "
            ".claude/tools/index_commit.py stage` (index_commit) with the same paths, then commit the "
            "new index." % reason)


REVERT_SCAN_CAP = 500  # staged paths checked; one stale file is enough to deny


def revert_verdict(rest, cwd, git_env, session_id=None, own_trailer=None):
    """Deny an index commit that reverts a peer's commit because its index was read from an older HEAD.

    Three signatures, each a path the commit changes: its working tree still matches HEAD (2026-09-23,
    69 files); its staged blob is the version one of HEAD's last REVERT_HISTORY commits replaced, which
    holds even when a peer kept editing the file (d31b4eeb4 reverted 7 metrics rows); or it is a staged
    addition the working tree lacks and HEAD's history deleted. Only index-publishing commits are
    judged; a pathspec or `-a` commit takes the working tree. Staged deletions are skipped, because
    `git rm --cached` has the same shape on purpose. A git failure skips that signature. A blob only
    this session's own commits replaced (its `Claude-Session` trailer, `_own_trailer`) is not a peer's,
    and neither is a merge staging the merged-in parent's own blob: that is the parent's history."""
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    if include_dirty or (pathspec and not ({"-i", "--include"} & set(rest))):
        return None
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        return None
    root = root.strip()
    added = run_git(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"], root, git_env)
    readded = [p for p in (added or "").split("\0") if p and not os.path.lexists(os.path.join(root, p))
               and run_git(["log", "-1", "--format=%h", "--diff-filter=D", "HEAD", "--", p], root)]
    out = run_git(["diff", "--cached", "--name-only", "--diff-filter=M", "-z"], root, git_env)
    paths = [p for p in (out or "").split("\0") if p][:REVERT_SCAN_CAP]
    if not paths:
        return _revert_message(readded)
    head = run_git(["ls-tree", "-z", "HEAD", "--"] + paths, root)
    work = run_git(["hash-object", "--"] + paths, root)
    if head is None or work is None:
        return _revert_message(readded)
    head_blob = {}
    for entry in head.split("\0"):
        meta, _, path = entry.partition("\t")
        if path:
            head_blob[path] = meta.split()[-1]
    stale = [p for p, blob in zip(paths, work.split()) if head_blob.get(p) == blob]
    replaced = _replaced_blobs(root, own_trailer or _own_trailer(session_id))
    staged = _raw_blobs(run_git(["diff", "--cached", "--raw", "-z", "--no-abbrev", "--diff-filter=M"],
                                root, git_env))
    merging = run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root, git_env)
    theirs = {}
    if merging:
        for entry in (run_git(["ls-tree", "-z", "MERGE_HEAD", "--"] + paths, root) or "").split("\0"):
            meta, _, path = entry.partition("\t")
            if path:
                theirs[path] = meta.split()[-1]
    stale += [p for p in paths if p not in stale and staged.get(p, (None, None))[1] in replaced.get(p, ())
              and staged[p][1] != theirs.get(p)]
    return _revert_message(stale + readded)


REVERT_HISTORY = 30  # recent HEAD commits whose replaced blobs count as stale


def _raw_blobs(out):
    """{path: (old blob, new blob)} from `git ... --raw -z --no-abbrev` output; {} on None."""
    tokens = (out or "").split("\0")
    found = {}
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split()
        if meta.startswith(":") and len(fields) >= 4 and path:
            found[path] = (fields[2], fields[3])
    return found


def _own_trailer(session_id):
    """This session's commit trailer, `claude.ai/code/<bridgeSessionId>`, read from the runtime's
    local registry (~/.claude/sessions/<pid>.json) for `session_id`; None when unresolvable."""
    if not session_id:
        return None
    for path in (Path.home() / ".claude" / "sessions").glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("sessionId") == session_id and doc.get("bridgeSessionId"):
            return "claude.ai/code/" + doc["bridgeSessionId"]
    return None


def _replaced_by(root, revs):
    """{path: {blob}} each commit in `revs` replaced (modifications only)."""
    out = run_git(["show", "--raw", "-z", "--no-abbrev", "--no-renames", "--format="] + revs, root) if revs else ""
    replaced = {}
    tokens = [t for t in (out or "").replace("\n", "").split("\0")]
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split()
        if meta.startswith(":") and len(fields) >= 5 and fields[4] == "M":
            replaced.setdefault(path, set()).add(fields[2])
    return replaced


def _replaced_blobs(root, own_trailer=None):
    """{path: {blob}}: every blob one of HEAD's last REVERT_HISTORY commits replaced, except those
    replaced only by commits carrying `own_trailer`."""
    revs = (run_git(["rev-list", "-n", str(REVERT_HISTORY), "HEAD"], root) or "").split()
    own = set()
    if own_trailer and revs:
        own = set((run_git(["log", "--format=%H", "-F", "--grep=" + own_trailer, "-n",
                            str(REVERT_HISTORY), "HEAD"], root) or "").split())
    return _replaced_by(root, [r for r in revs if r not in own])


def _revert_message(stale):
    """The denial for `stale` paths, the signatures `revert_verdict` names."""
    if not stale:
        return None
    shown = "\n  ".join(stale[:10]) + ("\n  ... %d more" % (len(stale) - 10) if len(stale) > 10 else "")
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — it would revert a peer's commit "
            "to %d file(s):\n  %s\n"
            "The index was built from an older HEAD. Rebuild it from the current HEAD, or commit "
            "your files with a pathspec (`git commit -F <msg> -- <paths>`)." % (len(stale), shown))


ADVISORIES = []  # commit advisories `main()` prints as additionalContext when no check denies


def harness_verdict(sub, rest, cwd, inline_env=None, git_env=None, session_id=None, refs_pending=()):
    """Quick commit checks and an advisory, budgeted run of authored harness paths' bound proofs."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None

    if _STATE_IMPORT_ERROR:
        return ("BLOCKED commit — harness guard state unavailable (%s). Repair "
                "hooks/_hook_state.py; for that repair commit prefix `%s=1`."
                % (_STATE_IMPORT_ERROR, BYPASS_VAR))

    if _SCOPE_IMPORT_ERROR:
        return ("BLOCKED commit-like git invocation — merge-scope state unavailable (%s). Repair "
                "hooks/_commit_scope.py; for that repair commit prefix `%s=1`."
                % (_SCOPE_IMPORT_ERROR, BYPASS_VAR))

    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        return ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot "
                "verify commit scope.")
    repo_root = root.strip()

    if publishes_index(sub, rest):
        paths, failed_cmd = staged_paths(rest, cwd, git_env)
    else:
        paths, failed_cmd = incoming_paths(sub, rest, cwd, git_env)
    if (paths is None and not publishes_index(sub, rest) and ref_names_created(refs_pending, rest)
            and not ref_resolves(rest, cwd, git_env)):
        # An earlier fetch / branch of this command creates the ref in this repo; it does not exist
        # yet, and a bare merge is never denied on content below.
        return None
    if paths is None:
        return "BLOCKED harness commit — `git %s` failed; cannot verify commit scope." % failed_cmd

    touched = sorted(p for p in paths if p.replace("\\", "/").startswith(".claude/"))
    surfaces = sorted(p for p in paths if _setup_surface(p))
    if not touched and not surfaces:
        return None

    if not publishes_index(sub, rest):
        # A bare merge / cherry-pick / revert re-commits content that was judged when it was
        # committed on its source ref; a conflict stops it before any commit, and the commit that
        # closes it — here or through `--continue` — is judged below.
        return None

    touched = authored_paths(touched, cwd, git_env)
    surfaces = authored_paths(surfaces, cwd, git_env)
    if not touched and not surfaces:
        return None

    # Advisories, never denies: whether the skill's checks were applied and whether a dense unit is
    # needed are judgments (instruction_quality §3). A setup surface alone never reaches them.
    skill = _skill_verdict(session_id, touched) if touched else None
    if skill:
        ADVISORIES.append(skill)

    changed = [p for p in touched if not _baseline_unchanged(repo_root, p, git_env)]
    dense = _md_density_verdict(repo_root, changed, git_env)
    if dense:
        ADVISORIES.append(dense)

    reattach = _reattach_cap_verdict(repo_root, changed)
    if reattach:
        ADVISORIES.append(reattach)

    tests_dir = os.path.join(repo_root, ".claude", "tests")
    missing = []
    for norm in touched:
        for prefix in (".claude/hooks/", ".claude/tools/"):
            if norm.startswith(prefix) and norm.endswith(".py"):
                name = os.path.basename(norm)[:-3]
                if (not name.startswith("_") and not _has_proof(tests_dir, name, git_env)
                        and not _removed(repo_root, norm, git_env)
                        and not _behavior_unchanged(repo_root, norm, git_env)
                        and not _baseline_unchanged(repo_root, norm, git_env)):
                    if _behavior_unchanged(repo_root, norm, git_env, ignore_strings=True):
                        ADVISORIES.append("[harness commit] no proof for %s: if a changed string is a pattern "
                                          "it matches or a value it decides on, add the proof "
                                          "(rules/harness_tooling.md)." % norm)
                    else:
                        missing.append((norm, name))
                break

    if missing:
        lines = ["BLOCKED harness commit (rules/harness_tooling.md) — no re-runnable proof; add a tracked proof for:"]
        for norm, name in missing:
            lines.append("  %s -> expected .claude/tests/test_%s*.py" % (norm, name))
        return "\n".join(lines)

    if changed:
        if _HARNESS_IMPORT_ERROR:
            ADVISORIES.append("[harness commit] bound proofs could not run: runner import error: %s; "
                              "unproven, not passed, and the close cannot prove it until the runner imports."
                              % _HARNESS_IMPORT_ERROR)
        else:
            try:
                ADVISORIES.extend(commit_advisories(repo_root, changed, git_environ(git_env)))
            except Exception as exc:
                ADVISORIES.append("[harness commit] bound proofs could not run: %s: %s; %s"
                                  % (type(exc).__name__, exc, unproven_note(changed)))
    return None


def _push_ranges(rest, root, git_env=None, targets=None):
    """Local evidence for each published source; no network calls in the hook. A `targets` list
    receives each range's (remote, destination branch), in range order."""
    if "--dry-run" in rest or "-n" in rest or "--delete" in rest or "-d" in rest:
        return []
    operands, remote, option = [], None, None
    for arg in rest:
        if option:
            if option == "--repo":
                remote = arg
            option = None
        elif arg in ("--repo", "--receive-pack", "--exec", "--push-option", "-o"):
            option = arg
        elif arg.startswith("--repo="):
            remote = arg.partition("=")[2]
        elif not arg.startswith("-"):
            operands.append(arg)
    branch = (run_git(["branch", "--show-current"], root, git_env) or "").strip()
    def config(key):
        return (run_git(["config", "--get", key], root, git_env) or "").strip()
    if remote is None:
        remote = operands.pop(0) if operands else push_destination(root, git_env)[0]
    specs, args = [], iter(operands)
    for arg in args:
        if arg == "tag":
            name = next(args, None)
            if name is None:
                raise RuntimeError("missing push tag")
            specs.append("refs/tags/" + name)
        else:
            specs.append(arg)
    if "--all" in rest or "--mirror" in rest:
        namespace = "refs" if "--mirror" in rest else "refs/heads"
        specs += (run_git(["for-each-ref", "--format=%(refname)", namespace], root, git_env) or "").splitlines()
    if "--tags" in rest:
        specs += (run_git(["for-each-ref", "--format=%(refname)", "refs/tags"], root, git_env) or "").splitlines()
    if not specs:
        specs = (run_git(["config", "--get-all", "remote." + remote + ".push"], root, git_env) or "").splitlines()
    if not specs:
        mode = config("push.default") or "simple"
        if mode == "matching":
            specs = [":"]
        elif mode == "nothing":
            return []
        else:
            target = config("branch." + branch + ".merge") if mode in ("simple", "upstream") else ""
            specs = ["HEAD:" + (target or "refs/heads/" + branch)]
    expanded = []
    for spec in specs:
        if spec.lstrip("+") == ":":
            heads = (run_git(["for-each-ref", "--format=%(refname)", "refs/heads"], root, git_env) or "").splitlines()
            expanded += [ref for ref in heads if run_git(["rev-parse", "--verify", "refs/remotes/" + remote + "/"
                         + ref.removeprefix("refs/heads/")], root, git_env)]
        else:
            expanded.append(spec)
    ranges = []
    for spec in expanded:
        source, _, target = spec.lstrip("+").partition(":")
        if not source:
            continue
        rev = run_git(["rev-parse", "--verify", source + "^{commit}"], root, git_env)
        if not rev:
            raise RuntimeError("cannot resolve push source " + source)
        target = (target or source).removeprefix("refs/heads/")
        if target == "HEAD":
            target = (run_git(["branch", "--show-current"], root, git_env) or "").strip()
        ref = "refs/remotes/" + remote + "/" + target
        base = close_base(root, push_ref=ref, head=rev.strip(), git_env=git_env, remote=remote)
        ranges.append((base, rev.strip()))
        if targets is not None:
            targets.append((remote, target))
    return ranges


def doctrine_audit_verdict(rest, cwd, inline_env=None, git_env=None):
    """Advisory: name admitted outgoing doctrine commits with no harness-quality coverage.

    Range and ledger failures report unavailable coverage, never deny a push.
    """
    if bypass_declared(inline_env, BYPASS_VAR):
        return None
    next_step = ("[review] doctrine review owed (execution_depth.md §Admission): %s; "
                 "run `/session_audit --lens sa-harness-quality` over the named range.")
    saved_env = dict(os.environ) if git_env else None
    try:
        sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
        try:
            import review_ledger
            import lens
            from harness_edit_skill_reminder import is_doctrine_path
        finally:
            sys.path.pop(0)
        root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
        if not root:
            raise RuntimeError("cannot resolve the push checkout")
        root = root.strip()
        ranges = _push_ranges(rest, root, git_env)
        if not ranges:
            return None
        tier = lens.seat_tier("sa-harness-quality")
        if tier is None:
            raise RuntimeError("cannot resolve sa-harness-quality tier")
        # Effort is model-specific; require valid observed effort, not a new model-specific floor.
        roster = [{"key": "sa-harness-quality", "mandates": ["harness-quality"],
                   "tier": tier, "effort": "none"}]
        if git_env:
            effective = git_environ(git_env)
            os.environ.clear()
            os.environ.update(effective)
        missing = []
        for base, rev in ranges:
            owed = review_ledger.owed_range(Path(root), base, rev, ["harness-quality"], roster,
                                            is_doctrine_path, exclude_remotes=True)
            if owed:
                missing.append(f"{base}..{rev}: " + ", ".join(
                    f"{commit['sha'][:7]} {commit['subject']}" for commit in owed))
        return next_step % ("unaudited doctrine in " + "; ".join(missing)) if missing else None
    except Exception as exc:
        return next_step % f"doctrine audit check unavailable: {type(exc).__name__}: {exc}"
    finally:
        if saved_env is not None:
            os.environ.clear()
            os.environ.update(saved_env)


_ADAPTATION_DIR = _CLAUDE_DIR


def _land_prefix():
    """`adaptation.json` `land_prefix`: the branch prefix the project's land runner proves and lands; empty when
    the project has none."""
    sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
    try:
        import adaptation
    finally:
        sys.path.pop(0)
    prefix = adaptation.load(_ADAPTATION_DIR).get("land_prefix")
    return prefix if isinstance(prefix, str) else ""


def harness_push_verdict(rest, cwd, inline_env=None, git_env=None):
    """Floor guard: each outgoing harness ref must be the commit a green close proved, with its base
    and paths. A commit landed after the close stays local: the push names the proven commit. A push to a
    branch under `adaptation.json` `land_prefix` passes: the project's land runner runs the close itself
    before the default branch moves."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None
    next_step = ("BLOCKED harness push (rules/harness_tooling.md) — %s; run `python3 "
                 ".claude/scripts/harness_tests.py --close` in the background, then push the commit it proves.")
    if _STATE_IMPORT_ERROR or _HARNESS_IMPORT_ERROR:
        return next_step % "close machinery unavailable"
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if not root:
        return next_step % "cannot resolve the push checkout"
    root = root.strip()
    targets = []
    try:
        ranges = _push_ranges(rest, root, git_env, targets)
        outgoing = [(base, rev, close_paths(root, base, rev, remote))
                    for (base, rev), (remote, _target) in zip(ranges, targets)]
    except (RuntimeError, OSError, ValueError):
        return next_step % "cannot resolve the outgoing harness range"
    land = _land_prefix()
    harness = [(base, rev, paths, dest) for (base, rev, paths), dest in zip(outgoing, targets)
               if paths and not (land and dest[1].removeprefix("refs/heads/").startswith(land))]
    if not harness:
        return None
    close = (read_json_salvage(_stamp_path(root)) or {}).get("close")
    green = isinstance(close, dict) and close.get("verdict") == "green" and not close.get("red")
    proven = close.get("head") if green else None
    for base, rev, paths, (remote, target) in harness:
        if proven and rev != proven:
            return ("BLOCKED harness push (rules/harness_tooling.md) — the green close proved %s, not the pushed "
                    "%s; push the proven commit: `git push %s %s:%s`, or check that ref out and run `python3 "
                    ".claude/scripts/harness_tests.py --close` in the background there."
                    % (proven[:12], rev[:12], remote, proven, target))
        if rev != proven or close.get("base") != base or not set(paths) <= set(close.get("paths") or []):
            return next_step % ("no green close result covers %s, its outgoing base and paths" % rev[:12])
    return None


TRAILER_RE = re.compile(r"claude\.ai/code/session_\w+")


def commit_msg_hook(message_path, environ=None, cwd=None):
    """Deny text for the commit git is about to write, or None: `base_verdict` and `revert_verdict` re-run inside git's commit-msg
    hook (`.claude/githooks/commit-msg`), because the PreToolUse check ran before the commit hooks and a peer's
    commit can land in between. A pathspec (`next-index-*`) or `-a` (`index.lock`) commit takes the working tree,
    and a merge has its own guard, so those pass. The message's own Claude-Session trailer is the session whose
    commits it may revert."""
    environ = os.environ if environ is None else environ
    cwd = cwd or os.getcwd()
    index = environ.get("GIT_INDEX_FILE") or ""
    name = os.path.basename(index)
    if name.startswith("next-index") or name == "index.lock":
        return None
    git_dir = run_git(["rev-parse", "--git-dir"], cwd)
    if git_dir and os.path.exists(os.path.join(cwd, git_dir.strip(), "MERGE_HEAD")):
        return None
    try:
        with open(message_path, encoding="utf-8", errors="replace") as fh:
            trailer = TRAILER_RE.search(fh.read())
    except OSError:
        trailer = None
    git_env = {"GIT_INDEX_FILE": os.path.abspath(os.path.join(cwd, index))} if index else None
    return (base_verdict(cwd, git_env)
            or revert_verdict([], cwd, git_env, own_trailer=trailer.group(0) if trailer else ""))


def _opaque_discard(command, nested=False):
    """Return a floor verdict for opaque git calls; they cannot borrow cwd/ownership exceptions."""
    import shlex
    if command_substitutions is None:
        return ('BLOCKED git call (CLAUDE.core.md §Shell Discipline): the nested-command parser '
                'failed to import (%s). Repair sidecar_argv.py before running git.' % _OPAQUE_IMPORT_ERROR)
    plain, bodies = command_substitutions(command)
    for body in bodies:
        why = _opaque_discard(body, nested=True)
        if why:
            return why
    for raw in shell_segments(plain):
        tokens, index = executable_index(raw)
        if index >= len(tokens):
            continue
        head = os.path.basename(tokens[index].replace('\\', '/')).lower()
        wrapped = index > 0 and any(t in ('env', 'sudo', 'xargs') for t in tokens[:index])
        if head in ('sudo', 'xargs'):
            values = ({'-u', '-g', '-h', '-p', '-C', '--user', '--group', '--host', '--prompt', '--chdir'}
                      if head == 'sudo' else {'-I', '-i', '-L', '-l', '-n', '-P', '-a', '-d', '-E', '-e',
                                             '--replace', '--max-lines', '--max-args', '--max-procs',
                                             '--arg-file', '--delimiter', '--eof'})
            index += 1
            while index < len(tokens) and tokens[index].startswith('-'):
                option = tokens[index]
                index += 2 if option in values else 1
                if option == '--':
                    break
            tokens, index = executable_index(tokens[index:])
            wrapped = True
        if index >= len(tokens):
            continue
        body = literal_launcher_body(tokens, index)
        if body is not None:
            why = _opaque_discard(body.body, nested=True)
            if why:
                return why
            continue
        parsed = git_invocation(shlex.join(tokens[index:]))
        if parsed and (nested or wrapped):
            why = verdict(parsed[0], git_env=parsed[2])
            if why:
                return why
    return None


def main() -> None:
    if sys.argv[1:2] == ["--commit-msg"]:
        try:
            why = commit_msg_hook(sys.argv[2] if len(sys.argv) > 2 else "")
        except Exception as exc:  # a floor guard: a fault refuses the commit
            print("BLOCKED commit: the commit-msg peer-revert guard crashed (%s: %s). Repair "
                  ".claude/hooks/git_guardrails.py commit_msg_hook until .claude/tests/test_git_commit_msg_hook.py "
                  "passes, then commit again." % (type(exc).__name__, exc), file=sys.stderr)
            sys.exit(1)
        if why:
            print(why, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    ADVISORIES.clear()
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = executable_text((input_data.get("tool_input") or {}).get("command") or "")
    if 'git' not in command:
        sys.exit(0)
    opaque = _opaque_discard(command)
    if opaque:
        print(opaque, file=sys.stderr)
        sys.exit(2)

    cwd = input_data.get("cwd") or "."
    born = set()  # repos this same command creates with `git init`: no harness history to stamp
    index_write = None  # the latest git index write seen so far in this command
    ref_makers = {}  # repo -> ref names an earlier invocation of this command creates there

    for call in git_invocations(command, cwd):
        args = [call.sub] + call.rest
        target, inline_env, git_env = call.cwd, call.inline_env, call.git_env

        def expand(token):
            return expand_literal(token, call.assignments) or token
        key = os.path.normcase(os.path.abspath(target))
        if args and args[0] == "init":
            named = [a for a in args[1:] if not a.startswith("-")]
            made = resolve_cd(target, expand(named[0])) if named else target
            top = run_git(["rev-parse", "--show-toplevel"], made, git_env)
            made_key = os.path.normcase(os.path.abspath(made))
            if top is None or os.path.normcase(os.path.abspath(top.strip())) != made_key:
                born.add(made_key)  # a new repo; `git init` on an existing root changes nothing
            continue
        judged_args = [expand(arg) for arg in args] if args and args[0] in ('checkout', 'restore') else args
        message = verdict(judged_args, target, git_env, input_data.get('transcript_path'))
        if not message and args and args[0] == "push":
            message = harness_push_verdict(args[1:], target, inline_env, git_env)
        if not message and args and args[0] == "push":
            reminder = doctrine_audit_verdict(args[1:], target, inline_env, git_env)
            if reminder:
                ADVISORIES.append(reminder)
        if (index_write and not message and key not in born and publishes_index and args
                and publishes_index(args[0], args[1:])):
            message = SAME_COMMAND_WRITE % (index_write, index_write)
        if writes_index and writes_index(args):
            index_write = "git " + args[0]
        if (not message and args and args[0] in ("commit", "merge", "cherry-pick", "revert")
                and key not in born):
            git_env, problem = resolve_index(git_env, target)
            if problem:
                message = "BLOCKED commit (memory gotcha_concurrent_session_hazards #16) — %s." % problem
            elif args[0] == "commit":
                message = (base_verdict(target, git_env)
                           or revert_verdict(args[1:], target, git_env, input_data.get("session_id")))
            if not message:
                message = harness_verdict(args[0], args[1:], target, inline_env, git_env,
                                          input_data.get("session_id"), ref_makers.get(key, ()))
        if args and args[0] in REF_MAKERS:
            ref_makers.setdefault(key, set()).update(refs_created(args[0], args[1:]))
        if message:
            print(message, file=sys.stderr)
            sys.exit(2)

    if ADVISORIES:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": "\n\n".join(ADVISORIES)}}))
    sys.exit(0)


# A floor guard: pre_bash_dispatch blocks the call when it crashes, since a crash here must never
# read as allow.
FAIL_CLOSED = True

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        sys.stderr.write("git_guardrails is a floor guard and crashed, so the call is blocked:\n"
                         + traceback.format_exc())
        sys.exit(2)
