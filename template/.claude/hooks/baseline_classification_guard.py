#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- normalize a plain Bash commit adding an unclassified
`.claude/` file through index_commit's private index and local rows, with resync after commit.
Deny remaining unclassified additions, dropped HEAD rows whose file still exists
(`_dropped_lock_rows`), and repeated row keys (`_repeated_lock_rows`).

Why: `baseline_sync.py`'s `candidates` sweep finds an unclassified addition only after it
already shipped, so an unreviewed universal-shaped file can sit unclassified and drift from
the baseline it should have joined. Classifying AT CREATION ties every `.claude/` addition to
a status decision the moment it becomes committable (Design Doc §4, plan `sync-baseline-v2.md`).

Scope: the invocations that publish the index -- `git commit`, and the `merge` / `cherry-pick` /
`revert` `--continue` that closes one. A bare `merge`/`cherry-pick`/`revert` only re-commits
content judged on its source ref. Commit detection reuses `_git_commit.commit_invocations`, so a
quoted mention (`git log --grep="git commit"`), a heredoc body, or an adjacent subcommand
(`git commit-graph write`) is never treated as a commit.

Added-file scope: `git diff --cached --name-only --no-renames --diff-filter=A`, run under the
commit's pathspec when one is given without `-i`/`--include` (so a pathspec commit is judged
only on what it actually publishes), intersected with `_git_commit.staged_paths` -- the same
seam the S4 fix corrected -- so a file merely staged by another session never gets attributed
to this commit. `--no-renames` makes a `git mv` into `.claude/` read as an addition of the new
path, which is exactly the shape this guard must catch. `_commit_scope.authored_paths` then drops
what a merge commit did not author: a `.claude/` file arriving from `MERGE_HEAD` was classified
where it was committed, so the first merge after an upstream release does not re-demand its rows.

Target repository: resolved via `git rev-parse --show-toplevel` against the invocation's own
cwd (payload cwd + any `cd`/`-C` the command carries). The guard acts only when that top level
holds `.claude/baseline.lock.json` -- so a commit made inside a baseline author worktree under
`.claude/.cache/baseline-worktrees/<id>/` is never judged: that worktree mirrors the baseline
repo's own layout (`template/`, `tools/`, `tests/`), which has no `.claude/` at its root.

Fail-closed: `main()` catches every exception, including a `baseline.lock.json` the JSON
parser cannot read, and denies rather than allow an unclassified file through un-verified.
`pre_bash_dispatch.py` wraps the IMPORT of this module in its own try/except: a broken guard
that cannot even import registers a stub that denies every `git commit` and names the import
error, so a broken guard can never fail open.

No bypass variable -- the S9 migration classifies every row before its own commit runs, so
nothing needs one.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", via
`pre_bash_dispatch.HOOKS` before the guards that must judge its rewritten command.
"""
from __future__ import annotations

import fnmatch
import json
from collections import Counter
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
_TOOLS_DIR = os.path.join(_CLAUDE_DIR, "tools")
for _p in (_HOOKS_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _git_commit import (commit_invocations, parse_commit_args, resolve_index, run_git, staged_paths,
                         segments, git_invocation, executable_text, git_environ, resolve_cd,
                         commit_source)  # noqa: E402
from _command_text import executable_spans, command_words, executable_word, comment_offset  # noqa: E402
from _commit_scope import writes_index  # noqa: E402
from _commit_scope import authored_paths, publishes_index  # noqa: E402
import baseline_sync  # noqa: E402

CLASSIFY_HINT = (
    "Classify first, in its own Bash call before the commit (this guard reads the lock before the command runs): "
    "python3 .claude/tools/baseline_sync.py classify <relpath> "
    "--status tracked|local|forked|composed [--from <source-relpath>] [--inputs <relpath>,...]"
)


def _repo_root_for(cwd: str) -> str | None:
    root = run_git(["rev-parse", "--show-toplevel"], cwd)
    return root.strip() if root else None


def _excluded(relpath: str) -> bool:
    if any(relpath.startswith(prefix) for prefix in baseline_sync.CANDIDATE_EXCLUDE_PREFIXES):
        return True
    name = relpath.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in baseline_sync.CANDIDATE_EXCLUDE_NAMES)


def _added_claude_paths(rest: list, cwd: str, env: dict | None = None):
    """`(paths, None) | (None, failing-git-subcommand)` -- staged `.claude/` additions this
    commit will actually publish, minus the engine's own candidate exclusions."""
    _include_dirty, _amend, pathspec = parse_commit_args(rest)
    include_flag = any(a in ("-i", "--include") for a in rest)
    args = ["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=A"]
    if pathspec and not include_flag:
        args = args + ["--"] + pathspec
    out = run_git(args, cwd, env)
    if out is None:
        return None, " ".join(args)
    added = {ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()}

    committed, failed_cmd = staged_paths(rest, cwd, env)
    if committed is None:
        return None, failed_cmd

    scoped = sorted(
        p for p in added
        if p in committed and p.startswith(".claude/") and not _excluded(p)
    )
    return scoped, None


def _committed_lock_text(rest: list, cwd: str, root: str, env: dict | None = None):
    """`(text, None)`, `("", None)` when the commit does not carry the lock, or `(None, failing-step)`.
    The committed copy is the working tree's for `-a` or a pathspec naming the lock, else the index's."""
    committed, failed_cmd = staged_paths(rest, cwd, env)
    if committed is None:
        return None, failed_cmd
    lock = baseline_sync.LOCK_RELPATH
    if lock not in committed:
        return "", None
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    named = run_git(["diff", "--name-only", "HEAD", "--"] + pathspec, cwd, env) if pathspec else ""
    if named is None:
        return None, "diff --name-only HEAD -- " + " ".join(pathspec)
    if include_dirty or lock in {ln.strip() for ln in named.splitlines()}:
        return (Path(root) / lock).read_text(encoding="utf-8"), None
    new_text = run_git(["show", ":" + lock], cwd, env)
    return (new_text, None) if new_text is not None else (None, "show :" + lock)


def _repeated_lock_rows(new_text: str) -> list:
    """Row keys the committed lock text holds more than once. `json.loads` keeps the last copy
    silently, so a text merge that re-adds a row a peer committed elsewhere passes every parsed check."""
    doc = json.loads(new_text, object_pairs_hook=list)
    files = next((value for key, value in doc if key == "files"), None)
    keys = [key for key, _value in files] if isinstance(files, list) else []
    return sorted(key for key, count in Counter(keys).items() if count > 1)


def _dropped_lock_rows(rest: list, cwd: str, root: str, env: dict | None = None):
    """`(rows, None) | (None, failing-step)` -- rows HEAD's lock holds that the committed lock
    lacks while their file still exists. A peer's temp-index commit moves HEAD but leaves the
    shared working-tree lock behind, so committing that lock reverts the peer's rows. A row whose
    file is gone left with its file, except a declined row (`absent`), which never has one.
    A declined row also left upstream when the same commit moves `synced_commit`: the engine is
    consuming a baseline publication that deleted the declined file."""
    new_text, failed_cmd = _committed_lock_text(rest, cwd, root, env)
    if new_text is None:
        return None, failed_cmd
    if new_text == "":
        return [], None
    lock = baseline_sync.LOCK_RELPATH
    head_text = run_git(["show", "HEAD:" + lock], cwd, env)
    if head_text is None:
        listed = run_git(["ls-tree", "--name-only", "HEAD", "--", lock], cwd, env)
        if not (listed or "").strip():
            return [], None  # no HEAD, or HEAD has no lock: nothing to drop
        return None, "show HEAD:" + lock
    head_doc, new_doc = json.loads(head_text), json.loads(new_text)
    head_rows, new_rows = head_doc.get("files") or {}, new_doc.get("files") or {}
    pin_moved = head_doc.get("synced_commit") != new_doc.get("synced_commit")
    return sorted(p for p, entry in head_rows.items() if p not in new_rows and (
        (Path(root) / p).exists()
        or (isinstance(entry, dict) and entry.get("absent") and not pin_moved))), None


def _judge_commit(rest: list, cwd: str, env: dict | None = None) -> str | None:
    """The deny message for one `git commit` invocation, or `None` to let it through."""
    root = _repo_root_for(cwd)
    if root is None:
        return None  # not inside a git repository at all -- nothing to judge
    env, unreadable = resolve_index(env, cwd)
    if unreadable:
        return "BLOCKED git commit -- " + unreadable

    lock_path = Path(root) / baseline_sync.LOCK_RELPATH
    if not lock_path.is_file():
        return None  # not a baseline consumer repo (e.g. a baseline author worktree)

    try:
        lock = baseline_sync.load_lock(Path(root))
    except (Exception, SystemExit) as exc:  # a v1 read helper `sys.exit`s on bad JSON
        return (
            "BLOCKED git commit -- .claude/baseline.lock.json could not be read "
            "(%s: %s). Fix the lock before committing." % (type(exc).__name__, exc)
        )

    lock_text, failed_cmd = _committed_lock_text(rest, cwd, root, env)
    repeated = _repeated_lock_rows(lock_text) if lock_text else []
    if repeated:
        lines = ["BLOCKED git commit -- baseline.lock.json holds these row keys more than once:"]
        lines.extend("  " + p for p in repeated)
        lines.append("A text merge re-added rows HEAD already holds. Keep one copy of each, then commit.")
        return "\n".join(lines)

    dropped, failed_cmd = _dropped_lock_rows(rest, cwd, root, env)
    if dropped is None:
        return (
            "BLOCKED git commit -- `git %s` failed; cannot verify baseline.lock.json keeps HEAD's rows."
            % failed_cmd
        )
    if dropped:
        lines = ["BLOCKED git commit -- baseline.lock.json drops row(s) HEAD holds while the file exists:"]
        lines.extend("  " + p for p in dropped)
        lines.append("A peer likely committed them from a private index. Restore each row from "
                     "`git show HEAD:.claude/baseline.lock.json`, then commit.")
        return "\n".join(lines)

    added, failed_cmd = _added_claude_paths(rest, cwd, env)
    if added is None:
        return (
            "BLOCKED git commit -- `git %s` failed; cannot verify .claude/ classification."
            % failed_cmd
        )
    # A merge commit re-commits its parents: a file arriving from MERGE_HEAD was already
    # classified where it was committed, so only what this commit authors needs a row.
    added = authored_paths(added, cwd, env)
    if not added:
        return None

    # A row counts in the working lock (classified, committed later) or in the lock this commit
    # publishes (a private index carries rows the shared working lock does not have yet).
    files = dict(lock.get("files") or {})
    if lock_text:
        files.update(json.loads(lock_text).get("files") or {})
    unclassified = [p for p in added if p not in files]
    if not unclassified:
        return None

    lines = ["BLOCKED git commit (CLAUDE.core.md §10: a lock row for each new .claude/ file) -- "
             "new .claude/ file(s) have no baseline.lock.json row:"]
    lines.extend("  " + p for p in unclassified)
    lines.append(CLASSIFY_HINT)
    return "\n".join(lines)


def normalize(payload):
    """Stage missing local rows through index_commit, without publishing peer lock rows.
    Only a single plain commit is rewritten; earlier index writes still need their own call."""
    command = (payload.get('tool_input') or {}).get('command') or ''
    cwd = payload.get('cwd') or '.'
    if payload.get('tool_name') != 'Bash' or executable_text(command) != command:
        return None
    invocations = commit_invocations(command, cwd)
    if len(invocations) != 1 or invocations[0].sub != 'commit':
        return None
    invocation = invocations[0]
    parsed_segments = [(s, git_invocation(s)) for s in segments(command)]
    spans = [(start, end, text) for start, end, text in executable_spans(command)
             if (git_invocation(text) or ([],))[0][:1] == ['commit']]
    if len(spans) != 1:
        return None
    at, end, segment = spans[0]
    layout = command_words(segment)
    if layout is None:
        return None
    words, insertion = layout
    if not words or insertion < words[-1].end:
        return None  # Interleaved redirects need shell evaluation, not an argv rebuild.
    git_at = executable_word(words, segment)
    inline = [segment[w.start:w.end] for w in words[:git_at] if not w.value.startswith('GIT_')]
    if any('$' in value or '`' in value for value in (invocation.git_env or {}).values() if value):
        return None
    if any(p and writes_index(p[0]) for s, p in parsed_segments):
        return None
    dirty, amend, pathspec = parse_commit_args(invocation.rest)
    if (pathspec and '--' not in invocation.rest) or dirty or amend or any(
            a in ('-i', '--include') or a.startswith('--pathspec-from-file') for a in invocation.rest):
        return None
    root = _repo_root_for(invocation.cwd)
    if not root or not (Path(root) / baseline_sync.LOCK_RELPATH).is_file():
        return None
    env, problem = resolve_index(invocation.git_env, invocation.cwd)
    if problem:
        return None
    added, problem = _added_claude_paths(invocation.rest, invocation.cwd, env)
    if problem or not added:
        return None
    added = authored_paths(added, invocation.cwd, env)
    head_lock = run_git(['show', 'HEAD:' + baseline_sync.LOCK_RELPATH], root, env)
    files = (json.loads(head_lock).get('files') or {}) if head_lock else {}
    lock_text, problem = _committed_lock_text(invocation.rest, invocation.cwd, root, env)
    if problem:
        return None
    if lock_text:
        files = dict(files, **(json.loads(lock_text).get('files') or {}))
    missing = [p for p in added if p not in files]
    if not missing:
        return None
    paths, problem = staged_paths(invocation.rest, invocation.cwd, env)
    if problem or not paths or baseline_sync.LOCK_RELPATH in paths:
        return None  # The existing dropped/repeated-row checks own explicit lock edits.
    source = commit_source(invocation.rest, invocation.cwd, env)
    if source is None:
        return None
    tool = (Path(_TOOLS_DIR) / 'index_commit.py').as_posix()
    name = 'normalize-' + uuid.uuid4().hex
    stage_args = [sys.executable, tool, 'stage', '--name', name]
    stage_env = dict(git_environ(env) or os.environ)
    stage_env.setdefault('INDEX_COMMIT_CLASSIFY_CMD', shlex.join(
        [sys.executable, str(Path(_TOOLS_DIR) / 'baseline_sync.py'), 'classify']))
    if source == 'index':
        listing = run_git(['ls-files', '--stage', '-z', '--', *sorted(paths)], root, env)
        if listing is None:
            return None
        entries = {}
        for record in listing.split('\0'):
            if not record:
                continue
            meta, sep, path = record.partition('\t')
            mode, sha, stage = meta.split()
            if not sep or stage != '0':
                return None
            entries[path] = mode + ',' + sha
        for path in sorted(paths):
            stage_args.extend(['--entry', path + '=' + entries.get(path, '0,')])
    proc = subprocess.run(stage_args + ['--', *sorted(paths)], cwd=root,
                          env=stage_env, capture_output=True, text=True,
                          encoding='utf-8', stdin=subprocess.DEVNULL)
    if proc.returncode:
        raise RuntimeError('index_commit stage failed: ' + (proc.stderr or proc.stdout).strip())
    printed = next((line for line in proc.stdout.splitlines() if line.startswith('GIT_INDEX_FILE=')), None)
    if not printed:
        raise RuntimeError('index_commit stage printed no commit command')
    index = shlex.split(printed)[0].split('=', 1)[1]
    rest = invocation.rest[:invocation.rest.index('--')] if '--' in invocation.rest else invocation.rest
    # The owner's printed private-index command selects its staged lock blob, never a pathspec.
    env_prefix = ['%s=%s' % (key, shlex.quote(value)) for key, value in (env or {}).items()
                  if key != 'GIT_INDEX_FILE' and value is not None]
    env_prefix.append('GIT_INDEX_FILE=' + shlex.quote(resolve_cd(root, index)))
    resync = '(cd ' + shlex.quote(root) + ' && ' + shlex.join(
        [Path(sys.executable).as_posix(), tool, 'resync', '--name', name]) + ')'
    tokens = [word.value for word in words]
    comment = comment_offset(segment)
    redirects = segment[insertion:comment].strip()
    cut = len(tokens) - len(invocation.rest) - 1
    globals_, i = [], git_at + 1
    while i < cut:
        token = tokens[i]
        if token in ('-C', '--git-dir', '--work-tree'):
            i += 2
            continue
        if token.startswith(('--git-dir=', '--work-tree=')):
            i += 1
            continue
        globals_.append(segment[words[i].start:words[i].end])
        if token in ('-c', '--namespace', '--exec-path') and i + 1 < cut:
            globals_.append(segment[words[i + 1].start:words[i + 1].end])
            i += 1
        i += 1
    raw_rest = [segment[word.start:word.end] for word in words[cut + 1:cut + 1 + len(rest)]]
    commit = (' '.join(inline) + ' ' if inline else '') + ' '.join(env_prefix) + ' ' + ' '.join(
        ['git', *globals_, '-C', shlex.quote(invocation.cwd.replace('\\', '/')), 'commit', *raw_rest])
    updated = (command[:at] + commit + (' ' + redirects if redirects else '') + ' && ' + resync
               + (' ' + segment[comment:] if comment < len(segment) else '') + command[end:])
    return updated, missing


def _run() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or "."
    normalized = normalize(payload)
    if normalized:
        command, missing = normalized
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
              'updatedInput': dict(payload.get('tool_input') or {}, command=command),
              'additionalContext': 'Classified local rows in a private index: ' + ', '.join(missing)
                                   + '. The commit is followed by index_commit resync.'}}))

    for invocation in commit_invocations(command, cwd):
        if not publishes_index(invocation.sub, invocation.rest):
            continue
        reason = _judge_commit(invocation.rest, invocation.cwd, invocation.git_env)
        if reason:
            print(reason, file=sys.stderr)
            return 2
    return 0


# A floor guard: pre_bash_dispatch blocks the call when it crashes.
FAIL_CLOSED = True


def main() -> int:
    try:
        return _run()
    except (Exception, SystemExit) as exc:  # fail CLOSED -- an unhandled crash must not allow
        print(
            "BLOCKED git commit -- baseline_classification_guard crashed (%s: %s). "
            "Fix the guard before committing to .claude/." % (type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
