#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|PowerShell), run by pre_bash_dispatch -- advises when a commit's changed
instruction text states a rule that an existing rule already states. A commit with no net-new
sentence in its authored scope paths skips the scan, including heading-only edits and moves.

It runs `tools/restate_scan.py pairs --changed [--staged] --base HEAD --json --paths <the commit's
in-scope paths>` as a subprocess, so the budget below can stop it, and prints one line per unruled
pair: the new location against the existing one, its channel, and the cited owner when the scan
names one. It closes with the ruling command `/rule_consistency` Step 0 states, carrying the
pre-commit HEAD sha, and names `/rule_consistency` as the adjudicator. This hook only advises:
`additionalContext` with exit 0, never a deny. Its own failure never reaches the model: an exception,
a read error or an import failure exits non-zero and the dispatcher reports it on the user's stderr.

It reads the source the commit publishes (`_git_commit.commit_source`): the index for an index
commit, the worktree for a pathspec, `-a`, `-i` or `--pathspec-from-file` commit. A merge is judged
on the paths it authors (`_commit_scope.authored_paths`), so a merge-arrived sentence is not centered
here. It prints `{}` when no authored path is published or the scan has no advice. Malformed duplicate scope
uses the advisory failure channel. Over budget the skipped scan names its manual command. Only duplicate pairs with two or
more agreeing channels are printed; the complete duplicate report is content-hashed under REPORT_DIR.

Matches the ACTION: commit invocations come from `_git_commit.commit_invocations`, so `git log
--grep commit`, an `echo` and a heredoc body pass. Only a commit in this project
(`_git_commit.in_project`, under the invocation's own `git_env`) is judged; another repo that holds its
own `.claude/` stays silent. The scan, the scope and the report all use the invocation's own top level, so a
commit in a linked worktree is judged against that worktree's index and HEAD.

A declared `instruction_scope` that is malformed (a missing `rows_dir`, empty `paths`) is reported on the
advisory failure channel; only an undeclared scope is silent.
"""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
for _d in (HERE, TOOLS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from _commit_scope import authored_paths, publishes_index  # noqa: E402
from _git_commit import (commit_invocations, commit_source, git_environ, in_project, resolve_index,  # noqa: E402
                         run_git, staged_paths)
import extract_numbers as en  # noqa: E402

RESTATE_SCAN = os.path.join(TOOLS, "restate_scan.py")
# One file per distinct report (named by its content hash), so concurrent commits never share a file; the
# newest REPORT_KEEP stay.
REPORT_DIR = ".claude/logs"
REPORT_STEM = "duplicate_scan_"
REPORT_KEEP = 20
RULING = "python3 .claude/tools/restate_scan.py rule %s distinct --reason \"<why>\" --changed --base %s"

# The `timeout` the composed settings.json gives `pre_bash_dispatch.py`. This hook's budget must fit
# inside it: the harness kills a hook that runs past its timeout, and a killed scan is an advisory the
# model never sees. `tests/test_duplicate_commit_check.py` reads that timeout from the composed
# settings.json, and fails when settings.base.json and settings.project.json disagree on it.
TIMEOUT_SECONDS = 75
# Median cost of the sub-hooks that run after this one in `pre_bash_dispatch.HOOKS`
# (sidecar_dispatch_context, approving_gates, bash_backslash_fidelity), measured on a loaded machine.
LATER_HOOK_SECONDS = 2.2
BUDGET_SECONDS = TIMEOUT_SECONDS - LATER_HOOK_SECONDS
_STARTED = time.monotonic()  # the budget covers the whole hook run


class _ReadError(Exception):
    """The content this commit publishes could not be read: reported on stderr with exit 1, never to the model."""


def _project_root():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.normpath(os.path.join(HERE, "..", ".."))


def _covered(root, paths):
    """The members of `paths` the project's `instruction_scope` covers, or None when the adaptation
    file declares no scope -- then no instruction text is covered and the scan is not run at all."""
    claude_dir = os.path.join(root, ".claude")
    try:
        raw = json.loads(en.adaptation.adaptation_path(claude_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict) or "instruction_scope" not in raw:
        return None
    scope = en.load_scope(claude_dir)
    if scope.invalid:
        raise _ReadError("instruction_scope is declared but invalid (%s); the duplicate scan did not run"
                         % "; ".join(scope.invalid))
    return [p for p in paths if en.in_scope(scope, p)]


def _manual(staged, paths, sha):
    """The scan command a reader runs by hand, from the project root, for this commit's paths."""
    return ("python3 .claude/tools/restate_scan.py pairs --changed%s --base %s --json --paths %s"
            % (" --staged" if staged else "", sha, " ".join(shlex.quote(p) for p in paths)))


def _skip_line(staged, paths, sha, why="no time left in the budget"):
    return "[duplicates] near-duplicate scan skipped for this commit (%s). Run it by hand: %s" % (
        why, _manual(staged, paths, sha))


def _pair_lines(groups):
    """[(line, group id, strong)] for every unruled pair; strong when two or more channels agree."""
    out = []
    for g in groups:
        members = {m["hash"]: m for m in g["members"]}
        center = members.get(g["center"])
        if center is None:
            continue
        for p in g["pairs"]:
            other = members.get(p["b"] if p["a"] == g["center"] else p["a"])
            if other is None:
                continue
            line = ("new %s:%d ⇄ existing %s:%d  [%s]%s"
                    % (center["path"], center["line"], other["path"], other["line"],
                       ",".join(p["channels"]), " cited owner " + g["citedOwner"] if g["citedOwner"] else ""))
            out.append((line, g["id"], len(p["channels"]) >= 2))
    return out


def _write_report(path, text):
    """Write `text` to `path` atomically, then drop all but the newest REPORT_KEEP reports beside it."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, scratch = tempfile.mkstemp(dir=folder, prefix=".dup_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(scratch, path)
    except BaseException:
        try:
            os.unlink(scratch)
        except OSError:
            pass
        raise

    def _mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    reports = sorted((os.path.join(folder, n) for n in os.listdir(folder)
                      if n.startswith(REPORT_STEM) and n.endswith(".txt")),
                     key=_mtime, reverse=True)
    for stale in reports[REPORT_KEEP:]:
        try:
            os.unlink(stale)
        except OSError:
            pass


def _block(groups, sha, root):
    """The advisory for one commit, or None when nothing is near-duplicated. Every pair and ruling
    command goes to a hash-named report under `root`; the context prints only the pairs two or more channels agree
    on, since one channel alone is the scan's recall tail, and names the report for the rest."""
    pairs = _pair_lines(groups)
    if not pairs:
        return None
    ruling = {g["id"]: "record group %s as two distinct decisions: %s" % (g["id"], RULING % (g["id"], sha))
              for g in groups}
    text = "\n".join([line for line, _g, _s in pairs]
                     + [ruling[g] for g in dict.fromkeys(g for _l, g, _s in pairs)]) + "\n"
    report = "%s/%s%s.txt" % (REPORT_DIR, REPORT_STEM, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
    try:
        _write_report(os.path.join(root, *report.split("/")), text)
    except OSError as exc:
        raise _ReadError("cannot write the duplicate report %s (%s)" % (report, exc))
    strong = [(line, g) for line, g, is_strong in pairs if is_strong]
    weak = len(pairs) - len(strong)
    head = ("[duplicates] %d near-duplicate sentence pair(s) this commit adds to existing rule text; "
            "/rule_consistency adjudicates them." % len(pairs))
    lines = [head] + [line for line, _g in strong] + [ruling[g] for g in dict.fromkeys(g for _l, g in strong)]
    if weak:
        lines.append("%d %spair(s) share one channel only; every pair and ruling command: %s"
                     % (weak, "more " if strong else "", report))
    return "\n".join(lines)


def _adds_sentence(root, paths, staged, git_env):
    before, after = set(), set()
    for path in paths:
        old = run_git(["show", "HEAD:" + path], root, git_env) or ""
        if staged:
            text = run_git(["show", ":" + path], root, git_env)
            if text is None:
                tracked = run_git(["ls-files", "--", path], root, git_env)
                if tracked is None or tracked.strip():
                    raise _ReadError("cannot read staged instruction text %s" % path)
                text = ""
        else:
            try:
                with open(os.path.join(root, path), encoding="utf-8") as handle:
                    text = handle.read()
            except FileNotFoundError:
                text = ""
            except (OSError, UnicodeError) as exc:
                raise _ReadError("cannot read instruction text %s (%s)" % (path, exc))
        before.update(unit.hash for unit in en.units(path, old))
        after.update(unit.hash for unit in en.units(path, text))
    return bool(after - before)


def _check(invocation, root):
    """The advisory block for one commit, or None when nothing it publishes is near-duplicated."""
    paths, failed = staged_paths(invocation.rest, invocation.cwd, invocation.git_env)
    if paths is None:
        raise _ReadError("cannot read the content this commit publishes (%s)" % failed)
    paths = sorted(authored_paths(sorted(paths), invocation.cwd, invocation.git_env))
    if not paths:
        return None
    root = (run_git(["rev-parse", "--show-toplevel"], invocation.cwd, invocation.git_env) or "").strip() or root
    covered = _covered(root, paths)
    env, problem = resolve_index(invocation.git_env, invocation.cwd)
    if problem:
        raise _ReadError(problem)
    source = commit_source(invocation.rest, invocation.cwd, invocation.git_env)
    if source is None:
        raise _ReadError("this -i commit stages paths outside its pathspec that differ from their working copies")
    staged = source == "index"
    sha = (run_git(["rev-parse", "HEAD"], invocation.cwd, invocation.git_env) or "").strip() or "HEAD"
    if not covered or not _adds_sentence(root, covered, staged, env):
        return None
    remaining = BUDGET_SECONDS - (time.monotonic() - _STARTED)
    if remaining <= 0:
        return _skip_line(staged, covered, sha)
    argv = [sys.executable, RESTATE_SCAN, "--repo", root, "pairs", "--changed"]
    if staged:
        argv.append("--staged")
    argv += ["--base", "HEAD", "--json", "--paths"] + covered
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=remaining,
                           cwd=invocation.cwd, env=git_environ(env))
    except subprocess.TimeoutExpired:
        return _skip_line(staged, covered, sha, "it ran past its budget")
    except OSError as exc:
        raise _ReadError("cannot run the near-duplicate scan (%s)" % exc)
    if r.returncode != 0:
        raise _ReadError("the near-duplicate scan exited %d (%s)"
                         % (r.returncode, (r.stderr or "").strip()[:200]))
    try:
        groups = json.loads(next(line for line in (r.stdout or "").splitlines() if line.strip()))
    except (StopIteration, ValueError) as exc:
        raise _ReadError("the near-duplicate scan printed no readable groups (%s)" % exc)
    return _block(groups, sha, root)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    root = _project_root()
    blocks = []
    try:
        for invocation in commit_invocations(command, payload.get("cwd") or os.getcwd()):
            if not publishes_index(invocation.sub, invocation.rest) or not in_project(invocation.cwd, root, invocation.git_env):
                continue
            block = _check(invocation, root)
            if block:
                blocks.append(block)
    except _ReadError as exc:
        sys.stderr.write("[duplicates] %s\n" % exc)
        return 1
    if blocks:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": "\n\n".join(blocks)}}))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
