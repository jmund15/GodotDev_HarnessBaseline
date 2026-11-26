#!/usr/bin/env python3
"""The worktree and clone deletion rule of the loss floor (T1). `dangerous_shell_guard` asks it whether a recursive
delete of a checkout loses work. It replaces `_regenerable_clone.py`.

A checkout (a linked worktree directly under `<project>/.claude/worktrees/`, or a standalone clone under a
`.claude/scratch/` of the project or of one of its worktrees) may go only when every condition holds; each failed one
is a reason, and one deny names them all:
- `git status --porcelain --untracked-files=all --ignored=matching` lists nothing but regenerable entries: a tracked
  file missing from the checkout (HEAD holds its blob), an untracked Godot `.import` file or `desktop.ini` and an
  ignored Godot import temp file (the tool writes each again, `regenerated`), an ignored cache or `desktop.ini`
  (`regenerable_segments`), an ignored `.agents`, `bin` or `obj` folder (`regenerable_when_ignored`; a tracked or
  unignored one is work), an ignored entry the project lists in `adaptation.json` `regenerable_ignored`
  (`project_patterns`), an ignored directory holding no file, and a job's scratch output (`scratch_problem`);
- a standalone clone has one worktree, a remote unless it has no commit, and no stash entry (a linked worktree's stash
  lives in the shared repository and survives it);
- every submodule is clean and its HEAD pushed; a submodule whose admin directory git cannot read denies;
- every local commit sits on a remote ref, or its work is on the default branch (`_landed_on`: by hash, by patch, as a
  squash or merged in another form);
- a linked worktree is registered, unlocked, and its branch heads no open PR.
`dangerous_shell_guard` adds the last condition: no live session works in it.

A saved copy under the project's `.claude/scratch` the rule can verify stands in for a push (v1's
`_saved_copy_holds`): a bundle named for the checkout that holds every commit no remote holds, and a tar holding the
same files or a patch that reverses cleanly from its tree for every file the status would lose. It lets a scratch
clone, a nested repository in a job's scratch output, or a retired worktree's unpushed HEAD go.

A linked worktree this session provably created (`own`, proved by `_own_worktree.session_created_worktree`) keeps
every condition but the status one, which reads `unstored_losses`: its ignored files go with it, and so does a dirty
path whose working file is gone or whose content is already a blob in the object store (CLAUDE.core.md §Shell Discipline:
"Ownership means this session provably created the worktree; only then may every ignored file go with it").
A submodule whose admin directory is gone is then judged against the main checkout's `.git/modules/<path>` store.

An orphan (git pruned its admin directory, or its `.git` file is gone) has no status to read: it goes when every file
in it is a blob in the repository's object store or a regenerable ignored entry (`_orphan_verdict`).

Anything else is the owner's call. A read that fails is a reason, so missing evidence denies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path

import _git_commit
import _git_run
from _git_commit import resolve_cd

_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
ANY_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_GIT_TIMEOUT_S = 10
# Hashing a whole orphaned checkout is the slow read; the decision's deadline (`_floor.BUDGET_S`) still bounds it.
_ORPHAN_HASH_TIMEOUT_S = 30


def _git(args: list[str], cwd: Path, stdin: str | None = None, env: dict | None = None,
         timeout: float = _GIT_TIMEOUT_S) -> str | None:
    """stdout of a read-only `git <args>` in `cwd`, or None on any failure; one answer per floor decision.

    `--no-optional-locks` keeps `status` from taking the index lock (a killed git would leave it behind), and
    `GIT_CEILING_DIRECTORIES` stops git climbing into an enclosing repository when the target's `.git` is gone."""
    env = dict(env or {}, GIT_CEILING_DIRECTORIES=str(cwd.parent))
    key = ("delete_rule", tuple(args), os.path.normcase(str(cwd)), stdin, tuple(sorted(env.items())))
    return _git_commit.cached(key, lambda: _git_run.soft_text(
        args, cwd=str(cwd), env=env, optional_locks=False, timeout=timeout,
        input=stdin.encode() if isinstance(stdin, str) else stdin))


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


# --- What a delete may discard without asking ---------------------------------------------------------------------
_FOLDER_METADATA = "desktop.ini"  # Windows Explorer writes it again; no project holds work in it
_REGENERABLE_SEGMENTS = frozenset({".godot", "__pycache__", ".pytest_cache", ".search-index", ".cache", "TestResults",
                                   _FOLDER_METADATA})
_REGENERABLE_PAIRS = frozenset({(".claude", "cache"), (".claude", "logs")})
# Names that regenerate only where git observes the entry as ignored output, never by name alone (`bin` and `obj`
# also name source folders outside C#, `.agents` another tool's tracked skills): an ignored `.agents/` is the
# Antigravity workspace its launcher rewrites before every run, `bin/` and `obj/` are what `dotnet build` recreates.
# One data row per name; a project's own outputs are `regenerable_ignored` (root-relative patterns).
_IGNORED_OUTPUT_NAMES = frozenset({".agents", "bin", "obj"})


# Godot writes these again on every import: an untracked `.import` sidecar beside an asset, and an import temp file.
_REGENERATED_UNTRACKED = (".import",)
_GODOT_IMPORT_TEMP = re.compile(r"\.import(?:\d+|~RF[0-9a-f]+)\.tmp$", re.I)


def _regenerated_untracked(entry: str) -> bool:
    return entry.endswith(_REGENERATED_UNTRACKED) or entry.rsplit("/", 1)[-1].lower() == _FOLDER_METADATA


def regenerated(line: str, entry: str) -> bool:
    """True for a status line a tool regenerates: an untracked `.import` file or `desktop.ini`, or an ignored Godot
    import temp file."""
    return ((line.startswith("?? ") and _regenerated_untracked(entry))
            or (line.startswith("!! ") and bool(_GODOT_IMPORT_TEMP.search(entry))))


def regenerable_segments(parts) -> bool:
    """True when a path's components hold a regenerable-cache segment or adjacent pair, compared without case.
    The one list: this rule and the delete guard's cache allow both read it."""
    lowered = [p.lower() for p in parts]
    return (any(seg.lower() in lowered for seg in _REGENERABLE_SEGMENTS)
            or any(pair in _REGENERABLE_PAIRS for pair in zip(lowered, lowered[1:])))


def regenerable_when_ignored(parts) -> bool:
    """`regenerable_segments`, plus the `_IGNORED_OUTPUT_NAMES`, for an entry git already reports ignored (a `!!`
    status line, an `ls-files --ignored` row): there the ignore is the observed fact."""
    return regenerable_segments(parts) or names_ignored_output(parts)


def _first_output_name(parts) -> int | None:
    return next((i for i, p in enumerate(parts) if p.lower() in _IGNORED_OUTPUT_NAMES), None)


def names_ignored_output(parts) -> bool:
    """True when a path's parts hold an `_IGNORED_OUTPUT_NAMES` folder: the cheap test before git is asked."""
    return _first_output_name(parts) is not None


def holds_only_ignored_output(root: Path, parts: list[str]) -> bool:
    """True when `parts` (below checkout `root`) lies in an `_IGNORED_OUTPUT_NAMES` folder and git sees no work under
    it: no tracked file and no untracked unignored file. An ancestor ignored for its own reasons (scratch, a clone)
    does not count, so the name's own ignore is what decides; git failing denies."""
    first = _first_output_name(parts)
    if first is None:
        return False
    if first and _git(["check-ignore", "-q", "--", "/".join(parts[:first])], root) is not None:
        return False
    return _git(["--literal-pathspecs", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
                 "/".join(parts)], root) == ""


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _adaptation(project_root) -> dict:
    """The project's `adaptation.json` as a dict; an absent, unreadable or non-object file is `{}`."""
    path = Path(project_root) / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_data(*keys):
    """The floor's one reader of project values: the entry under `keys` in this checkout's `adaptation.json`, or
    None. A stdlib read on purpose: the floor does not import `tools/adaptation.py`, so a fault there cannot deny
    every call. The caller validates the shape and keeps what is well formed, so a broken file only narrows."""
    value = _adaptation(PROJECT_ROOT)
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def project_patterns(project_root: Path) -> tuple[str, ...]:
    """The project's `adaptation.json` `regenerable_ignored` patterns. A non-list, an unreadable file and an entry
    that is empty, absolute, climbs with `..` or opens with a wildcard contribute nothing, so a broken list only
    narrows what may go."""
    listed = _adaptation(project_root).get("regenerable_ignored")
    return tuple(p for p in (listed if isinstance(listed, list) else [])
                 if isinstance(p, str) and p and not p.startswith(("/", "*", "\\")) and ".." not in p.split("/")
                 and ":" not in p)


def _listed(entry: str, patterns: tuple[str, ...]) -> bool:
    parts = entry.split("/")
    return any(fnmatchcase("/".join(parts[:n]), pattern) for n in range(1, len(parts) + 1) for pattern in patterns)


def _only_listed_files(path: Path, entry: str, patterns: tuple[str, ...]) -> bool:
    """True when every file under the ignored directory `path` (`entry` from the checkout root) is listed; git
    reports an ignored directory as one line. A link inside it is a loss: it is not followed."""
    if not patterns or path.is_symlink() or not path.is_dir():
        return False
    try:
        with os.scandir(path) as children:
            for child in children:
                name = entry + "/" + child.name
                if _listed(name, patterns):
                    continue
                if child.is_symlink() or not child.is_dir(follow_symlinks=False) \
                        or not _only_listed_files(Path(child.path), name, patterns):
                    return False
    except OSError:
        return False
    return True


def _holds_no_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        return not any(p.is_file() or p.is_symlink() for p in path.rglob("*"))
    except OSError:
        return False


def empty_leftover(path: str) -> bool:
    """True when `path` is a directory holding no file and no `.git` at any depth: an interrupted removal's empty
    shell, which loses nothing."""
    root = Path(path)
    return _holds_no_file(root) and not any(p.name == ".git" for p in root.rglob("*"))


# `.claude/tmp` holds the owner's loose files beside the folders a proof plants there. Only a folder whose name a proof
# builds from `PROOF_PLANT_PREFIX`, a kind and `tempfile.mkdtemp`'s eight-character suffix is a proof's own fixture.
PROOF_PLANT_PREFIX = "pe_absent_"
_PROOF_PLANT = re.compile("^" + re.escape(PROOF_PLANT_PREFIX) + r"[a-z]+_[a-z0-9_]{8}$")
# The id git gives a tree with no entry, per object format (SHA-1, SHA-256).
_EMPTY_TREES = frozenset({"4b825dc642cb6eb9a060e54bf8d69288fbee4904",
                          "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"})


def is_proof_plant(name: str) -> bool:
    """True when `name`, a folder directly under a checkout's `.claude/tmp`, is one a proof planted."""
    return bool(_PROOF_PLANT.match(name))


def _holds_only_empty_commits(repo: Path) -> bool:
    """A standalone repository with no remote, no changed or untracked file, whose every commit has the empty tree:
    a proof's `git commit --allow-empty` fixture, which loses only messages."""
    if not (repo / ".git").is_dir() or _git(["remote"], repo) != "":
        return False
    if _git(["status", "--porcelain", "--untracked-files=all"], repo) != "":
        return False
    trees = _git(["log", "--all", "--format=%T"], repo)
    return trees is not None and all(line in _EMPTY_TREES for line in trees.split())


def is_plant_exhaust(path: Path) -> bool:
    """True when `path` is a proof's planted folder that is no link and holds no git repository at any depth except
    one `_holds_only_empty_commits`. A folder that cannot be read is not exhaust."""
    if not is_proof_plant(path.name) or path.is_symlink():
        return False
    try:
        for here, dirs, files in os.walk(path, followlinks=False):
            if ".git" in files:
                return False
            if ".git" in dirs:
                if not _holds_only_empty_commits(Path(here)):
                    return False
                dirs.remove(".git")
    except OSError:
        return False
    return True


def _scratch_start(parts: list[str]) -> int | None:
    """The index just past the first `.claude/scratch` or `.claude/tmp` pair in `parts`, or None."""
    for index, pair in enumerate(zip(parts, parts[1:])):
        if pair in ((".claude", "scratch"), (".claude", "tmp")):
            return index + 2
    return None


def _names_no_commit(git_dir: Path) -> bool:
    """A `.git` directory git cannot open (no `HEAD`) that holds no ref: only loose objects remain, which no commit
    names. A directory that cannot be read is not judged empty."""
    if (git_dir / "HEAD").exists() or (git_dir / "packed-refs").exists():
        return False
    try:
        return not any(p.is_file() or p.is_symlink() for p in (git_dir / "refs").rglob("*"))
    except OSError:
        return False


def _session_folder(name: str, folder: bool) -> str | None:
    if name == ".git":
        return "holds a git repository that is not a clean pushed clone"
    if folder and ANY_SESSION_ID.search(name):
        return "holds another session's output folder"
    return None


def scratch_problem(entry: str, root: Path | None = None, project_root: Path | None = None) -> str | None:
    """Why an ignored `.claude/scratch` or `.claude/tmp` entry (`entry`, from the checkout `root`) keeps the delete
    denied, or None when it is a job's own output: no link, no folder named by a session id (that session's output;
    a file a session id names is no folder), no loose file directly in a `.claude/scratch` (evidence, not a job's
    output folder), and no git repository except a clean pushed standalone clone or a `.git` that names no commit. A
    folder a proof planted under `.claude/tmp` (`is_plant_exhaust`) is its own fixture. Without `root`, the entry's
    name alone is judged."""
    parts = entry.split("/")
    start = _scratch_start(parts)
    if start is None:
        return None
    if parts[start - 1] == "tmp" and start < len(parts) and is_proof_plant(parts[start]) \
            and (root is None or is_plant_exhaust(root.joinpath(*parts[:start + 1]))):
        return None
    leaf_is_file = root is not None and (root / entry).is_file()
    for index, name in enumerate(parts[start:], start):
        reason = _session_folder(name, folder=not (leaf_is_file and index == len(parts) - 1))
        if reason:
            return reason
        if index == start == len(parts) - 1 and parts[start - 1] == "scratch" and (root is None or leaf_is_file):
            return "is a loose file in the scratch folder, not a job output folder"
    if root is None:
        return None
    top = root / entry
    if top.is_symlink():
        return "is a link, so its contents cannot be judged"
    if not top.is_dir():
        return None
    try:
        for here, dirs, files in os.walk(top, followlinks=False):
            rel = os.path.relpath(here, root).replace("\\", "/")
            if rel.endswith(".claude/tmp"):  # a proof's planted folder is its own fixture, never walked
                dirs[:] = [d for d in dirs if not is_plant_exhaust(Path(here) / d)]
            if ".git" in files:
                if project_root is not None and _saved_copy_holds(Path(here), project_root, root):
                    dirs[:] = []
                    continue
                return rel + " holds a linked worktree, not a clean pushed clone" + _save_step(Path(here),
                                                                                                project_root)
            if ".git" in dirs and _names_no_commit(Path(here) / ".git"):
                dirs.remove(".git")
            if ".git" in dirs:
                why = clone_reasons(Path(here), project_root, root)
                if not why:
                    dirs[:] = []  # a clean clone's own ignored files were judged; do not descend
                    continue
                return rel + " holds a git repository that loses work: " + why[0] + _save_step(Path(here),
                                                                                                 project_root)
            direct_scratch = rel.split("/")[-2:] == [".claude", "scratch"]
            for name, folder in [(d, True) for d in dirs] + [(f, False) for f in files]:
                reason = _session_folder(name, folder)
                if reason:
                    return rel + "/" + name + " " + reason
                if direct_scratch and not folder:
                    return rel + "/" + name + " is a loose file in the scratch folder, not a job output folder"
            for name in dirs:
                if (Path(here) / name).is_symlink():
                    return rel + "/" + name + " is a link, so its contents cannot be judged"
    except OSError:
        return "cannot read the job scratch tree"
    return None


def _status_entry(line: str) -> str:
    entry = line[3:]
    if len(entry) > 1 and entry[0] == entry[-1] == '"' and "\\" not in entry:
        entry = entry[1:-1]  # git quotes a path holding a space; an escaped path stays quoted and denies
    return entry.rstrip("/")


def losses(status: str, root: Path, patterns: tuple[str, ...] = (), project_root: Path | None = None,
           own: bool = False) -> list[str]:
    """Every `git status --porcelain --ignored=matching` line whose deletion loses work, with the scratch reason
    when one applies. A tracked file missing from the checkout (` D`), Godot's regenerated output (`regenerated`) and
    an ignored regenerable entry lose nothing; in a worktree this session created (`own`) no ignored entry does."""
    out = []
    for line in status.splitlines():
        entry = _status_entry(line)
        if line.startswith(" D ") or regenerated(line, entry):
            continue
        if line.startswith("!! "):
            parts = entry.split("/")
            if (own or regenerable_when_ignored(parts) or _listed(entry, patterns) or _holds_no_file(root / entry)
                    or _only_listed_files(root / entry, entry, patterns)):
                continue
            problem = scratch_problem(entry, root, project_root)
            if problem is None and _scratch_start(parts) is not None:
                continue
            out.append(line + (" (" + problem + ")" if problem else ""))
            continue
        out.append(line)
    return out


_LOSS_LIST_CAP = 8


def _format_losses(found: list[str]) -> str:
    """The losses as one list for a deny: the first `_LOSS_LIST_CAP`, then how many more."""
    more = len(found) - _LOSS_LIST_CAP
    return "; ".join(found[:_LOSS_LIST_CAP]) + (" (and %d more)" % more if more > 0 else "")


# --- Commits: on a remote, or landed on the default branch ---------------------------------------------------------
# Each candidate costs one git read inside the decision's deadline; every real landing observed sat within 14 commits
# of the tip on its paths, and an older holder fails closed.
_HISTORY_SCAN = 20
_REAPPLY_SCAN = 8


def _patch_lines(repo: Path, rev: str) -> tuple[Counter, Counter] | None:
    """The added and removed lines of `rev`'s own change, or None for a merge or an unreadable diff."""
    parents = _git(["rev-list", "--parents", "-n", "1", rev], repo)
    if parents is None or len(parents.split()) != 2:
        return None
    text = _git(["diff", "--no-renames", "--no-color", "--no-ext-diff", "-U0", rev + "^", rev], repo)
    if text is None:
        return None
    added, removed, in_hunk = Counter(), Counter(), False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk:
            in_hunk = line.startswith("@@")
        elif line.startswith("+"):
            added[line[1:]] += 1
        elif line.startswith("-"):
            removed[line[1:]] += 1
    return added, removed


def _diff_paths(repo: Path, rev: str) -> set[str] | None:
    out = _git(["diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", rev], repo)
    return (set(out.split("\n")) - {""}) if out is not None else None


def _reapplied(repo: Path, ref: str, commit: str) -> bool:
    """A commit on `ref` changed the same paths with the same added and removed lines, on a different base.
    Candidates come from the commit's new-file blobs, else from the changed path `ref` touched least."""
    mine = _patch_lines(repo, commit)
    paths = _diff_paths(repo, commit)
    if mine is None or not paths:
        return False
    candidates: set[str] = set()
    added = _git(["diff-tree", "--no-commit-id", "--diff-filter=A", "--name-only", "--no-renames", "-r", commit], repo)
    for path in (added or "").splitlines():
        blob = _git(["rev-parse", commit + ":" + path], repo)
        if blob and blob.strip():
            found = _git(["log", "--format=%H", "--no-color", "--find-object=" + blob.strip(), ref, "--", path], repo)
            candidates.update((found or "").split())
    if not candidates:
        counts = []
        for path in paths:
            count = _git(["rev-list", "--count", ref, "--", path], repo)
            if count is None or not count.strip().isdigit():
                return False
            counts.append((int(count), path))
        listing = _git(["rev-list", "--max-count=%d" % _REAPPLY_SCAN, ref, "--", min(counts)[1]], repo)
        candidates.update((listing or "").split())
    return any(other != commit and _diff_paths(repo, other) == paths and _patch_lines(repo, other) == mine
               for other in candidates)


def _landed_on(repo: Path, ref: str) -> bool:
    """Every HEAD commit's work is on `ref`, cheapest test first: HEAD is reachable from it; each commit's patch is
    on it and none is a merge (a cherry-pick, a rebased lane); no file the branch changed since the merge base differs
    from it (a squash); a merge of HEAD into it changes nothing; a commit on it carries HEAD's content for every
    changed path; or each of HEAD's commits is a re-application of one on it."""
    if _git(["rev-list", "HEAD", "--not", ref], repo) == "":
        return True
    if (_git(["rev-list", "--merges", "HEAD", "--not", ref], repo) == ""
            and _git(["rev-list", "--cherry-pick", "--right-only", "--no-merges", ref + "...HEAD"], repo) == ""):
        return True
    base = (_git(["merge-base", ref, "HEAD"], repo) or "").strip()
    changed = _git(["diff", "--name-only", "--no-renames", "-z", base, "HEAD"], repo) if base else None
    differs = _git(["diff", "--name-only", "--no-renames", "-z", ref, "HEAD"], repo)
    if (changed is not None and differs is not None
            and not (set(changed.split("\0")) & set(differs.split("\0"))) - {""}):
        return True
    paths = [path for path in (changed or "").split("\0") if path]
    if not paths:
        return False
    merged = _git(["merge-tree", "--write-tree", ref, "HEAD"], repo)
    want = _git(["rev-parse", ref + "^{tree}"], repo)
    if merged and want and merged.split("\n", 1)[0].strip() == want.strip():
        return True
    for sha in (_git(["rev-list", "--max-count=%d" % _HISTORY_SCAN, ref, "--", *paths], repo) or "").split():
        if _git(["diff", "--quiet", sha, "HEAD", "--", *paths], repo) is not None:
            return True
    unique = _git(["rev-list", "HEAD", "--not", ref], repo)
    return unique is not None and all(_reapplied(repo, ref, commit) for commit in unique.split())


def _pushed(repo: Path, reasons: list[str], prefix: str = "", also: Path | None = None,
            local_default: bool = False, preserved=None) -> bool:
    """Every HEAD commit is on a remote, or its work is on the remote's default branch (`_landed_on`). `also` is the
    main checkout's copy of the same submodule: a worktree's submodule clone keeps its own remote refs, which go stale.
    `local_default` (a worktree of the project, whose branches outlast it in the shared repository) also accepts
    work on the local default branch. `preserved(head)` accepts a HEAD that a saved copy holds instead."""
    out = _git(["rev-list", "HEAD", "--not", "--remotes"], repo)
    if out is None:
        reasons.append(prefix + "cannot verify HEAD reachability")
        return False
    if not out.strip():
        return True
    head = (_git(["rev-parse", "HEAD"], repo) or "").strip()
    if also is not None and head and (_git(["branch", "-r", "--contains", head], also) or "").strip():
        return True
    if preserved is not None and head and preserved(head):
        return True
    default = (_git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], repo) or "").strip()
    refs = [default] if default else []
    local = default.split("/", 1)[-1]
    if refs and local_default and "/" in default:
        # A job's work lands on the local default first. The remote's scan is skipped when both name one commit:
        # every read of `_landed_on` depends only on the ref's commit, so it would answer the same.
        mine = (_git(["rev-parse", "--verify", "-q", "refs/heads/" + local + "^{commit}"], repo) or "").strip()
        if mine:
            theirs = (_git(["rev-parse", "--verify", "-q", default + "^{commit}"], repo) or "").strip()
            refs = ["refs/heads/" + local] + ([default] if theirs != mine else [])
    if any(_landed_on(repo, ref) for ref in refs):
        return True
    reasons.append(prefix + "HEAD " + (_git(["rev-parse", "--short", "HEAD"], repo) or "(unreadable)").strip()
                   + " is on no remote branch" + (" (save a bundle of its refs under .claude/scratch named for the "
                                                  "worktree, then retry)" if preserved is not None
                                                  else " (push it to a remote, then retry)"))
    return False


# --- Standalone clones under scratch -------------------------------------------------------------------------------
def clone_reasons(target: Path, project_root: Path | None = None, deleting: Path | None = None) -> list[str]:
    """Why deleting the standalone clone at `target` loses work, or [] when it loses nothing: a saved copy under the
    project's `.claude/scratch` holds everything it would lose (`_saved_copy_holds`; `deleting` is the whole delete,
    default `target`), or it is its own top level with one worktree, a remote unless it has no commit, a status
    listing only regenerable entries, no stash, and no branch or tag commit off every remote-tracking ref."""
    if project_root is not None and _saved_copy_holds(target, project_root, deleting):
        return []
    if not (target / ".git").is_dir():
        return ["target is not a standalone clone (no .git directory)"]
    top = _git(["rev-parse", "--show-toplevel"], target)
    try:
        if top is None or not _same_path(Path(top.strip()).resolve(strict=True), target):
            return ["target is not its own repository root"]
    except OSError:
        return ["target is not its own repository root"]
    worktrees = _git(["worktree", "list", "--porcelain"], target)
    if worktrees is None or sum(1 for line in worktrees.splitlines() if line.startswith("worktree ")) != 1:
        return ["clone has linked worktrees"]
    remotes = _git(["remote"], target)
    if (not remotes or not remotes.strip()) and _git(["rev-list", "--all", "-n", "1"], target) != "":
        return ["clone has no remote"]
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching"], target)
    if status is None:
        return ["cannot read clone status"]
    found = losses(status, target, project_root=project_root)
    if found:
        return ["clone has local files or changes: " + found[0]]
    if _git(["stash", "list"], target) != "":
        return ["clone has a stash"]
    if _git(["rev-list", "--branches", "--tags", "--not", "--remotes"], target) != "":
        return ["clone has commits on no remote branch"]
    return []


def _literal(token: str) -> str | None:
    """Why `token` cannot name a delete target this rule judges, or None for a literal path."""
    if not token or _UNSAFE_PATH_CHARS.search(token):
        return "target is not a literal path"
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return "target contains path traversal or has no path"
    return None


def _scratch_clone_path(target: Path, project_root: Path) -> bool:
    """`target` lies strictly under a `.claude/scratch` of the project or of one of its linked worktrees."""
    for parent in target.parents:
        if parent.name == "scratch" and parent.parent.name == ".claude":
            holder = parent.parent.parent
            if _same_path(holder, project_root):
                return True
            return _same_path(holder.parent, project_root / ".claude" / "worktrees")
    return False


def _scratch_clone_target(token: str, cwd: str, project_root: str) -> Path | None:
    if _literal(token):
        return None
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
    except OSError:
        return None
    return target if _scratch_clone_path(target, root) else None


def is_regenerable_clone(token: str, cwd: str, project_root: str, reasons: list[str] | None = None) -> bool:
    """True only when `token` names a standalone clone under a project or worktree `.claude/scratch` whose deletion
    loses nothing (`clone_reasons`). `reasons` receives the first failed condition."""
    reasons = [] if reasons is None else reasons
    why = _literal(token)
    if why:
        reasons.append(why)
        return False
    found = _scratch_clone_target(token, cwd, project_root)
    if found is None:
        reasons.append("target is not inside a .claude/scratch of the project or its worktrees")
        return False
    why = clone_reasons(found, Path(project_root))
    reasons.extend(why[:1])
    return not why


# --- Saved copies (v1 `_regenerable_clone._saved_copy_holds`) --------------------------------------------------------
# A saved copy is a file under the project's `.claude/scratch` in one of these formats whose name carries the
# target's directory name; the name only narrows candidates, and every check below reads the artifact's content.
_SAVED_COPY_SUFFIXES = (".bundle", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
                        ".patch", ".diff")
# Files at most this many path components below `.claude/scratch`: the deepest copy observed is a job's `bundles/`,
# and a scandir walk to that depth measured 1.3 s over a 4,616-directory scratch.
_SAVED_COPY_LEVELS = 3


def _save_step(repo: Path, project_root: Path | None) -> str:
    """The deny's next step for a repository `_saved_copy_holds` judges: a tar alone never saves a commit no remote
    holds, so the step names the bundle for its commits and the tar for its files."""
    scratch = (project_root / ".claude" / "scratch").as_posix() if project_root else ".claude/scratch"
    listing = _git(["worktree", "list", "--porcelain"], repo) or ""
    more = (" Every linked worktree must lie inside the delete: bundle after its last commit (`--all` "
            "carries each worktree's HEAD), and tar the whole folder so it holds every worktree's files."
            if sum(1 for line in listing.splitlines() if line.startswith("worktree ")) > 1 else "")
    return (' (save its commits: `git -C "%s" bundle create "%s/%s.bundle" --all`; and its uncommitted files as '
            '"%s/%s.tar"; then retry.%s)' % (repo.as_posix(), scratch, repo.name, scratch, repo.name, more))


def _saved_copies(project_root: Path, stem: str, target: Path) -> list[Path]:
    """Files under the project's `.claude/scratch` whose name carries `stem` and whose suffix is a saved-copy format,
    never inside `target` (it dies with the delete)."""
    base = project_root / ".claude" / "scratch"
    if not base.is_dir():
        return []
    needle = stem.lower()
    found: list[Path] = []
    level: list[tuple[Path, int]] = [(base, 0)]
    while level:
        nxt: list[tuple[Path, int]] = []
        for here, depth in level:
            try:
                entries = list(os.scandir(here))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != ".git" and depth + 1 < _SAVED_COPY_LEVELS \
                                and not _same_path(path, target):
                            nxt.append((path, depth + 1))
                    elif (needle in entry.name.lower() and entry.name.lower().endswith(_SAVED_COPY_SUFFIXES)
                          and not any(_same_path(parent, target) for parent in path.parents)):
                        found.append(path)
                except OSError:
                    continue
        level = nxt
    return found


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _tar_name(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _archive_contents(path: Path) -> dict[str, str] | None:
    """`{relative path: sha256}` for every regular file in a tar archive, with one shared top directory
    (`pubcopy/...`) removed. None when the file is not a readable tar or holds a link or unreadable entry."""
    try:
        with tarfile.open(path) as archive:
            members = [member for member in archive if member.isfile()]
            raw = [_tar_name(member.name) for member in members]
            tops = {name.split("/", 1)[0] for name in raw}
            cut = next(iter(tops)) + "/" if len(tops) == 1 and all("/" in name for name in raw) else ""
            contents: dict[str, str] = {}
            for member, name in zip(members, raw):
                handle = archive.extractfile(member)
                if handle is None or not name:
                    return None
                contents[name[len(cut):]] = hashlib.sha256(handle.read()).hexdigest()
        return contents
    except (OSError, tarfile.TarError):
        return None


def _archive_holds(path: Path, wanted: dict[str, str]) -> bool:
    contents = _archive_contents(path)
    return contents is not None and all(contents.get(rel) == digest for rel, digest in wanted.items())


def _patch_paths(path: Path, repo: Path) -> set[str] | None:
    """The paths a patch touches, or None when git cannot read it."""
    out = _git(["apply", "--numstat", "-z", str(path)], repo)
    if out is None:
        return None
    return {record.split("\t")[-1] for record in out.split("\0") if record.strip()}


def _patch_holds(path: Path, target: Path, wanted: set[str]) -> bool:
    """True when the patch covers exactly the target's changed paths and reverses cleanly from its working tree:
    applying it to the base commit reconstructs the target's content. The caller has already required every commit
    to be on a remote or in a bundle, so the base survives the delete."""
    return _patch_paths(path, target) == wanted and _git(["apply", "--reverse", "--check", str(path)],
                                                         target) is not None


def _bundle_covers(target: Path, bundle: Path, commits: list[str]) -> bool:
    """True when the bundle verifies against `target` and every commit is at or behind one of its heads."""
    if _git(["bundle", "verify", str(bundle)], target) is None:
        return False
    listing = _git(["bundle", "list-heads", str(bundle)], target)
    heads = [line.split()[0] for line in (listing or "").splitlines() if line.split()]
    if not heads:
        return not commits
    left = _git(["rev-list", "--stdin"], target, stdin="\n".join([*commits, "--not", *heads]) + "\n")
    return left is not None and not left.strip()


def _saved_copy_holds(target: Path, project_root: Path, deleting: Path | None = None) -> bool:
    """True when deleting `target` loses nothing: its git state survives the delete (every ref on a remote or in a
    bundle named for it, and for a `.git` file a repository lying outside `deleting`; a repository's linked worktrees
    must all lie inside `deleting`, each HEAD held like a ref and each worktree's losses counted), and every file
    `losses` reports is held by a saved copy under the project's `.claude/scratch`: a tar with the same files, or a
    patch covering exactly the target's changes that reverses cleanly from its tree. A clean tree has no file loss,
    so its refs decide it and no file copy is needed."""
    deleting = deleting or target
    copies = _saved_copies(project_root, target.name, target)
    bundles = [copy for copy in copies if copy.name.lower().endswith(".bundle")]
    copy_is_worktree = not (target / ".git").is_dir()
    linked: list[Path] = []
    if copy_is_worktree:
        for flag in ("--git-dir", "--git-common-dir"):
            found = (_git(["rev-parse", "--path-format=absolute", flag], target) or "").strip()
            if not found or any(_same_path(Path(found), spot)
                                or any(_same_path(parent, spot) for parent in Path(found).parents)
                                for spot in (target, deleting)):
                return False  # the repository holding this copy goes with the delete
        off_remote = (_git(["rev-list", "HEAD", "--not", "--remotes"], target) or "").strip()
        required = off_remote.split()
    else:
        listing = _git(["worktree", "list", "--porcelain"], target)
        if listing is None:
            return False
        linked = [Path(line[9:]) for line in listing.splitlines() if line.startswith("worktree ")][1:]
        if any(not any(_same_path(parent, deleting) for parent in (spot, *spot.parents)) for spot in linked):
            return False  # a linked worktree outside the delete keeps its own state
        required = (_git(["rev-list", "--branches", "--tags", "--not", "--remotes"], target) or "").split()
        for spot in linked:
            required += (_git(["rev-list", "HEAD", "--not", "--remotes"], spot) or "").split()
        stash = (_git(["rev-parse", "--verify", "-q", "refs/stash"], target) or "").strip()
        if stash:
            required.append(stash)
    if required and not any(_bundle_covers(target, bundle, required) for bundle in bundles):
        return False  # a commit no remote holds dies with the target; only a bundle saves it
    wanted: dict[str, str] = {}
    patterns = project_patterns(project_root)
    inside = tuple(spot.as_posix().lower() + "/" for spot in linked)
    for tree in [target, *linked]:
        status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching"], tree)
        if status is None:
            return False
        for line in losses(status, tree, patterns=patterns):
            entry = line[3:].split(" (")[0]
            if len(entry) > 1 and entry[0] == entry[-1] == '"' and "\\" not in entry:
                entry = entry[1:-1]
            path = tree / entry.rstrip("/")
            files = [path] if path.is_file() else (sorted(p for p in path.rglob("*") if p.is_file())
                                                   if path.is_dir() else [])
            if not files:
                return False
            for file in files:
                if tree is target and file.as_posix().lower().startswith(inside):
                    continue  # a linked worktree's files are judged by its own status
                digest = None if file.is_symlink() else _sha256(file)
                if digest is None:
                    return False
                wanted[Path(os.path.relpath(file, target)).as_posix()] = digest
    if not wanted:
        return not copy_is_worktree  # a clean checkout of another repository's worktree has no loss here
    for copy in copies:
        name = copy.name.lower()
        if name.endswith(".bundle"):
            continue
        if name.endswith((".patch", ".diff")):
            if _patch_holds(copy, target, set(wanted)):
                return True
        elif _archive_holds(copy, wanted):
            return True
    return False


def _bundle_holds(path: Path, head: str, repo: Path) -> bool:
    """True when the bundle verifies against `repo` (every prerequisite is there) and lists `head`."""
    if _git(["bundle", "verify", str(path)], repo) is None:
        return False
    listing = _git(["bundle", "list-heads", str(path)], repo)
    return any(line.split()[:1] == [head] for line in (listing or "").splitlines())


def _bundle_preserves(project_root: Path, target: Path, head: str) -> bool:
    """True when a saved bundle named for `target` holds `head`: its commits can be restored from it."""
    return any(_bundle_holds(copy, head, project_root)
               for copy in _saved_copies(project_root, target.name, target)
               if copy.name.lower().endswith(".bundle"))


# --- Orphaned worktrees ----------------------------------------------------------------------------------------------
def _orphan_admin(target: Path) -> Path | None:
    """The admin directory a worktree's `.git` file names, when that directory is gone, else None."""
    try:
        text = (target / ".git").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("gitdir:"):
        return None
    admin = Path(text[len("gitdir:"):].strip())
    admin = admin if admin.is_absolute() else target / admin
    return None if admin.exists() else admin


def unstored_files(files: list[str], run) -> list[str] | None:
    """The files in `files` whose working-tree content the object store lacks, in order; None when git cannot say.
    `run(args, stdin)` returns git's stdout, or None on failure."""
    ids = run(["hash-object", "--stdin-paths"], "\n".join(files) + "\n")
    if ids is None or len(ids.split()) != len(files):
        return None
    held = run(["cat-file", "--batch-check"], ids)
    if held is None:
        return None
    return [path for path, line in zip(files, held.splitlines()) if line.endswith(" missing")]


def worktree_unstored(cwd: str, files: list[str]) -> list[str] | None:
    """`unstored_files` for the repository checked out at `cwd`; `files` are relative to `cwd`, which may lie below the
    checkout's top. `_git` cannot read a repository from below its top (its ceiling is the cwd's parent) and
    `hash-object --stdin-paths` reads paths from the top, so git runs at the top with absolute paths."""
    top = next((p for p in (Path(cwd), *Path(cwd).parents) if (p / ".git").exists()), None)
    if top is None:
        return None
    absolute = [os.path.join(cwd, f) for f in files]
    lost = unstored_files(absolute, lambda args, stdin: _git(args, top, stdin))
    return None if lost is None else [f for f, a in zip(files, absolute) if a in lost]


def unstored_losses(status: str, root: Path) -> list[str]:
    """`losses` for a worktree this session created: every ignored entry goes, and a dirty path loses nothing when its
    working file is gone (an index blob is always in the store) or its content is already a blob in the object store;
    a submodule's gitlink is judged by that submodule's own checks. A missing working file git does not report deleted
    (a path git quoted, say) is a loss: the check fails closed."""
    out = []
    for line in status.splitlines():
        if not losses(line, root, own=True):
            continue
        entry = line[3:].split(" -> ")[-1].strip().strip('"')
        path = root / entry
        if (path / ".git").exists():
            continue
        if not (path.is_file() or path.is_symlink()):
            if "D" not in line[:2]:
                out.append(line)
            continue
        unstored = worktree_unstored(str(root), [entry])
        if unstored is None or unstored:
            out.append(line)
    return out


def _unheld_files(git_dir: Path, work_tree: Path, root: Path, absent_index: Path,
                  patterns: tuple[str, ...] = (), own: bool = False) -> list[str]:
    """Every file under `work_tree` whose deletion loses content: a non-ignored file `git_dir`'s object store lacks,
    and an ignored file that is no regenerable entry. `absent_index` names no file, so git reads an empty index and
    writes nothing. A populated submodule is judged against `root`'s `.git/modules/<path>` store."""
    env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(work_tree), "GIT_INDEX_FILE": str(absent_index)}

    def run(args, stdin=None):
        return _git(args, work_tree, stdin, env=env, timeout=_ORPHAN_HASH_TIMEOUT_S)

    found: list[str] = []
    subs = []
    if (work_tree / ".gitmodules").is_file():
        subs = _gitmodules_paths(run(_GITMODULES_PATHS))
    for sub in subs:
        if not (work_tree / sub).is_dir() or not any((work_tree / sub).iterdir()):
            continue
        store = root / ".git" / "modules" / sub
        if not store.is_dir():
            found.append(sub + "/ (a populated submodule with no store at .git/modules/" + sub + ")")
            continue
        found.extend(sub + "/" + loss for loss in _unheld_files(store, work_tree / sub, root, absent_index, patterns,
                                                                 own))
    ignored = run(["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])
    if ignored is None:
        return found + ["(cannot list ignored files)"]
    for entry in filter(None, ignored.split("\0")):
        if any(entry == sub or entry.startswith(sub + "/") for sub in subs):
            continue
        if not (own or regenerable_when_ignored(entry.split("/")) or _listed(entry, patterns)
                or _GODOT_IMPORT_TEMP.search(entry)
                or (_scratch_start(entry.split("/")) is not None and scratch_problem(entry) is None)):
            found.append(entry + " (ignored, and not a regenerable cache)")
    listed = run(["ls-files", "--others", "--exclude-standard", "-z"])
    if listed is None:
        return found + ["(cannot list files)"]
    files = [entry for entry in filter(None, listed.split("\0"))
             if not any(entry == sub or entry.startswith(sub + "/") for sub in subs)]
    found.extend(entry + " (a nested repository)" for entry in files if entry.endswith("/"))
    files = [entry for entry in files if not entry.endswith("/") and not _regenerated_untracked(entry)]
    if not files:
        return found
    missing = unstored_files(files, run)
    return found + (["(cannot read the object store)"] if missing is None else missing)


_NOT_A_WORKTREE = ("target is not a linked worktree directly under .claude/worktrees, nor an orphaned worktree "
                   "of this repository")


def _common_git_dir(root: Path) -> Path | None:
    found = (_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], root) or "").strip()
    return Path(found) if found else None


def _orphan_of_repo(target: Path, root: Path) -> bool:
    """`target`'s `.git` file names `<this repository's git dir>/worktrees/<name>` and git pruned that admin
    directory: an orphaned worktree of this repository, wherever it sits. A directory holding the project is none."""
    admin = _orphan_admin(target)
    if admin is None or any(_same_path(parent, target) for parent in (root, *root.parents)):
        return False
    common = _common_git_dir(root)
    return common is not None and _same_path(admin.parent.resolve(), (common / "worktrees").resolve())


def _worktree_target(token: str, cwd: str, project_root: str, reasons: list[str]) -> tuple[Path, Path] | None:
    """`(target, project root)` when `token` is a literal path to a directory directly under
    `<project>/.claude/worktrees/` or to an orphaned worktree of the project's repository."""
    why = _literal(token)
    if why:
        reasons.append(why)
        return None
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
        home = (root / ".claude" / "worktrees").resolve()
    except OSError:
        reasons.append("target or worktree home cannot be resolved")
        return None
    if not _same_path(target.parent, home) and not _orphan_of_repo(target, root):
        reasons.append(_NOT_A_WORKTREE)
        return None
    return target, root


def _orphan_verdict(target: Path, root: Path, reasons: list[str], own: bool = False) -> bool | None:
    """None when `target` has a live `.git` link (judge it by status). For an orphan (git pruned the admin directory,
    or no `.git` link remains) True when nothing is lost, else False after naming every loss."""
    if not (target / ".git").exists() and target.is_dir():
        admin = target / ".git-absent"
    elif (target / ".git").is_file():
        admin = _orphan_admin(target)
        if admin is None:
            return None
    else:
        reasons.append(_NOT_A_WORKTREE)
        return False
    found = _unheld_files(_common_git_dir(root) or root / ".git", target, root, admin / "index", project_patterns(root),
                          own)
    if not found:
        return True
    reasons.append("orphaned worktree holds content git does not have: " + _format_losses(found)
                   + " (save those files elsewhere or delete them, then retry)")
    return False


def lossless_orphan_worktree(token: str, cwd: str, project_root: str, reasons: list[str] | None = None,
                             own: bool = False) -> bool:
    """True only for an orphaned worktree of this repository (under `.claude/worktrees/` or anywhere its `.git` file
    names a pruned admin directory of this repository) that holds nothing git lacks. `reasons` receives the loss of
    an orphan, or the not-a-worktree reason for a target that is neither an orphan nor a live worktree."""
    located = _worktree_target(token, cwd, project_root, [])
    return located is not None and _orphan_verdict(*located, [] if reasons is None else reasons, own) is True


# --- Linked worktrees ------------------------------------------------------------------------------------------------
def _registered_unlocked(listing: str, target: Path, reasons: list[str]) -> bool:
    for block in listing.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        try:
            path = Path(lines[0][len("worktree "):]).resolve(strict=True)
        except OSError:
            continue
        if _same_path(path, target):
            if any(line == "locked" or line.startswith("locked ") for line in lines):
                reasons.append("worktree is locked")
                return False
            return True
    reasons.append("worktree is not registered")
    return False


def _open_prs(root: Path) -> list[tuple[int, str]] | None:
    """`(number, head branch)` per open PR of the project's GitHub remote, [] when origin is not on GitHub, None when
    `gh` cannot answer (the caller denies)."""
    origin = _git(["remote", "get-url", "origin"], root)
    if origin is None or "github.com" not in origin:
        return []
    try:
        proc = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json",
                               "number,headRefName"], cwd=str(root), capture_output=True, text=True,
                              timeout=_git_run.remaining(_GIT_TIMEOUT_S))
        return [(int(row["number"]), str(row["headRefName"])) for row in json.loads(proc.stdout)] \
            if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def _open_pr_reason(target: Path, root: Path) -> str | None:
    """Why `target` holds an open PR's branch (its head, or `sync/pr<N>` of open PR N): a PR under test or review
    needs its checkout until it closes or merges."""
    branch = (_git(["symbolic-ref", "-q", "--short", "HEAD"], target) or "").strip()
    if not branch:
        return None
    prs = _open_prs(root)
    if prs is None:
        return "cannot read open PRs (gh failed), so cannot tell whether branch " + branch + " heads one; fix gh and retry"
    match = re.fullmatch(r"sync/pr(\d+)", branch)
    for number, head in prs:
        if branch == head or (match and int(match.group(1)) == number):
            return ("branch " + branch + " belongs to open PR #" + str(number)
                    + ", which still needs this checkout; remove it after the PR closes or merges")
    return None


# `-z` rows are `key NL value NUL`: a submodule name or path holding a space stays whole (the plain rows split on it).
_GITMODULES_PATHS = ["config", "-f", ".gitmodules", "-z", "--get-regexp", r"^submodule\..*\.path$"]


def _gitmodules_paths(listed: str | None) -> list[str]:
    """The submodule paths in `_GITMODULES_PATHS` output; none when it is None or empty."""
    rows = (row.partition("\n") for row in (listed or "").split("\0"))
    return [path.strip("/") for _key, sep, path in rows if sep]


def _declared_submodules(repo: Path) -> list[str] | None:
    """The submodule paths `repo`'s `.gitmodules` declares (none without the file), or None when git cannot read it."""
    if not (repo / ".gitmodules").is_file():
        return []
    listed = _git(_GITMODULES_PATHS, repo)
    if listed is None and _git(["config", "-f", ".gitmodules", "--list"], repo) != "":
        return None
    return _gitmodules_paths(listed)


def _populated_submodules(repo: Path, declared: list[str] | None = None, prefix: str = "") -> list[str] | None:
    """Every populated submodule under `repo`, each followed by its own, as paths from the top (the rows `git submodule
    status --recursive` gave, read from the index instead: that command runs through git's shell wrapper, ten times the
    cost of `ls-files`). A gitlink `.gitmodules` does not map makes that command die, so it is None here too, as is any
    unreadable index. An uninitialized submodule, or one whose `.git` names a missing admin directory, holds no rows."""
    listed = _git(["ls-files", "--stage", "-z"], repo)
    if listed is None:
        return None
    links = list(dict.fromkeys(row.split("\t", 1)[1] for row in listed.split("\0") if row.startswith("160000 ")))
    if not links:
        return []
    declared = _declared_submodules(repo) if declared is None else declared
    if declared is None or any(path not in declared for path in links):
        return None
    rows: list[str] = []
    for path in links:
        sub = repo / path
        if not (sub / ".git").exists() or _orphan_admin(sub):
            continue
        inner = _populated_submodules(sub, prefix=prefix + path + "/")
        if inner is None:
            return None
        rows += [prefix + path] + inner
    return rows


OWNERSHIP_UNPROVEN = ("this session's transcript shows no literal, visible create of it (a `git worktree add` or a "
                      "`worktree_create_tools` call run first in its list, its errors in the output), so its ignored "
                      "files and stored edits count as losses; without them it would be retired")


def is_retired_worktree(token: str, cwd: str, project_root: str, reasons: list[str] | None = None,
                        own: bool = False) -> bool:
    """True only when `token` names a checkout of `project_root` whose deletion loses nothing (the module rule): a
    standalone clone under scratch (`clone_reasons`), an orphan (`_orphan_verdict`), or a registered, unlocked linked
    worktree directly under `<project>/.claude/worktrees/` whose branch heads no open PR, whose status and every
    submodule's lists only regenerable entries, and whose HEAD and every submodule HEAD is pushed or landed.
    `git worktree remove` refuses a worktree carrying a submodule, so a retired one goes by a recursive delete and
    `git worktree prune`. `own` (this session created it) reads `unstored_losses` and judges a submodule whose admin
    directory is gone against the main checkout's store. `reasons` receives every failed condition; a target or
    registration that cannot be judged stops at that reason."""
    reasons = [] if reasons is None else reasons
    clone = _scratch_clone_target(token, cwd, project_root)
    if clone is not None:
        why = clone_reasons(clone, Path(project_root))
        reasons.extend(why[:1])
        return not why
    located = _worktree_target(token, cwd, project_root, reasons)
    if located is None:
        return False
    target, root = located
    verdict = _orphan_verdict(target, root, reasons, own)
    if verdict is not None:
        return verdict
    listing = _git(["worktree", "list", "--porcelain"], root)
    if listing is None:
        reasons.append("cannot read worktree registration")
        return False
    if not _registered_unlocked(listing, target, reasons):
        return False
    pr_reason = _open_pr_reason(target, root)
    if pr_reason:
        reasons.append(pr_reason)
        return False
    declared = _declared_submodules(target)
    if declared is None:
        reasons.append("cannot read submodule status")
        return False
    broken = [name for name in declared if (target / name / ".git").is_file() and _orphan_admin(target / name)]
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                   "--ignore-submodules=" + ("all" if broken else "none")], target)
    if status is None:
        reasons.append("cannot read worktree status")
        return False
    failed: list[str] = []
    patterns = project_patterns(root)
    found = unstored_losses(status, target) if own else losses(status, target, patterns, root)
    if found:
        failed.append("worktree has local files or changes: " + _format_losses(found))
        if not own and not unstored_losses(status, target):
            failed.append(OWNERSHIP_UNPROVEN)  # the files are the loss only because creation is unproven
    _pushed(target, failed, local_default=True, preserved=lambda head: _bundle_preserves(root, target, head))
    for name in broken:
        if not own:
            failed.append("submodule " + name + ": its .git names a missing admin directory, so its state is "
                          "unreadable, and this session did not create the worktree")
            continue
        store = root / ".git" / "modules" / name
        lost = (_unheld_files(store, target / name, root, target / name / ".git-absent" / "index", own=True)
                if store.is_dir() else ["(no store at .git/modules/" + name + ")"])
        if lost:
            failed.append("submodule " + name + ": its .git names a missing admin directory, and it holds content "
                          "git does not have: " + _format_losses(lost) + " (save it elsewhere, then retry)")
    if broken or not declared:
        rows = [name for name in declared if name not in broken]
    else:
        rows = _populated_submodules(target, declared)
        if rows is None:
            reasons.append("cannot read submodule status")
            return False
    for name in rows:
        sub = target / name
        if not (sub / ".git").exists():
            continue
        sub_status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                           "--ignore-submodules=none"], sub)
        if sub_status is None:
            failed.append("submodule " + name + ": cannot read status")
            continue
        _pushed(sub, failed, "submodule " + name + ": ", also=root / name)
        found = unstored_losses(sub_status, sub) if own else losses(sub_status, sub, patterns)
        if found:
            failed.append("submodule " + name + " has local files or changes: " + _format_losses(found))
    reasons.extend(failed)
    return not failed


def _session_transcript() -> str | None:
    """This session's transcript path from its id in the environment, or None (then nothing is proven)."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    tools = str(Path(__file__).resolve().parents[1] / "tools")
    if not sid:
        return None
    if tools not in sys.path:
        sys.path.append(tools)
    try:
        from model_registry import find_session_transcript
        found = find_session_transcript(sid)
    except Exception:  # a registry fault leaves the worktree unproven, the safe side
        return None
    return str(found) if found else None


def main(argv: list[str]) -> int:
    """`[--created] <path>...`: the delete guard's verdict per checkout, `<path>: retired` or `<path>: blocked:
    <reasons>`. Ownership is read from this session's transcript as the guard reads it; `--created` instead assumes
    this session created every path (a hypothetical, labelled in the output). Exit 1 when any is blocked, 2 with no
    path. A live session working in it is the guard's check, not this one."""
    hypothetical = "--created" in argv
    argv = [a for a in argv if a != "--created"]
    if not argv:
        print(main.__doc__)
        return 2
    root = (os.environ.get("CLAUDE_PROJECT_DIR") or _git(["rev-parse", "--show-toplevel"], Path.cwd()) or ".").strip()
    blocked = 0
    transcript = None if hypothetical else _session_transcript()
    with _git_commit.memoized():
        for token in argv:
            reasons: list[str] = []
            own = hypothetical
            if not own:
                from _own_worktree import session_created_worktree
                own = session_created_worktree(resolve_cd(os.getcwd(), token), transcript)
            if (lossless_orphan_worktree(token, os.getcwd(), root, own=own)
                    or is_retired_worktree(token, os.getcwd(), root, reasons, own=own)):
                print("%s: retired%s" % (token, " (--created: assumed, not proven)" if hypothetical else ""))
            else:
                blocked += 1
                print("%s: blocked: %s" % (token, "; ".join(reasons)))
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
