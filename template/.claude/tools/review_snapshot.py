#!/usr/bin/env python3
"""One address for reviewed bytes: where a file's content lives, which revision it denotes and what history
reaches it.

A snapshot opens over one source — a commit, the index, the worktree, or a ledger row's recorded blobs — and
answers `blobs(paths)` with one typed result per path:

    Blob(sha)          the content id at that source
    Deleted(tombstone) absent there, with a committed deletion (`deleted:<commit>`)
    Missing            absent there, with no committed deletion
    Unreadable(reason) the object store or the checkout could not be read

The three absent results stay distinct: an unreadable read is never reported as a deletion, because a tombstone
credits a deletion no reviewer recorded. A path under a linked worktree reads that worktree; a path under a
submodule reads that submodule's object store at the pin the gitlink holds. Results are cached for the snapshot's
lifetime, never beyond it.

The module also owns the history facts a coverage check asks of the same repository — whether a commit's line
reaches HEAD (`reaches`), whether a merge rewrote a path (`merge_touched`), the commits in a range
(`range_commits`) — and the path identity helpers that name a reviewed file (`worktree_split`, `normalize_files`,
`resolve_citation`). Git process execution belongs to `hooks/_git_run`: this module spawns nothing itself.

Proof: tests/test_review_snapshot.py.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

CLAUDE_DIR = Path(__file__).resolve().parent.parent
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
if str(CLAUDE_DIR / "hooks") not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR / "hooks"))

from _git_run import root_relative as _root_relative, run as _git  # noqa: E402


class Blob(NamedTuple):
    sha: str


class Deleted(NamedTuple):
    tombstone: str


class Missing(NamedTuple):
    pass


class Unreadable(NamedTuple):
    reason: str


MISSING = Missing()


class SnapshotError(OSError):
    """An input this snapshot cannot read; an absent object is a `Missing` result, not this."""


class Commit(NamedTuple):
    """The source is a committed revision (a sha, a ref, or `HEAD`)."""
    rev: str


class Index(NamedTuple):
    """The source is the repository's index (staged content)."""


class Disk(NamedTuple):
    """The source is the worktree's bytes; a missing file is `Missing`, never a committed deletion."""


class Recorded(NamedTuple):
    """The source is a ledger row's recorded blob map."""
    row: dict


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _call(args, root, input=None):
    try:
        return _git(args, cwd=str(root), input=b"" if input is None else input)
    except OSError as exc:
        return Unreadable(f"git {args[0]} unavailable: {exc}")
    except Exception as exc:  # subprocess.TimeoutExpired and anything the launcher raises
        return Unreadable(f"git {args[0]} failed: {exc}")


_FACTS = None  # {(fact, folder): answer} while a `repo_facts()` block runs


@contextmanager
def repo_facts():
    """Inside the block each `fact` is asked once per folder. One command's repository does not move while it runs,
    yet each snapshot it opened asked again (a lineage check opens three): those repeats were a third of the review
    ledger proof's git processes."""
    global _FACTS
    outer = _FACTS
    if outer is None:
        _FACTS = {}
    try:
        yield
    finally:
        if outer is None:
            _FACTS = None


def fact(name, root, ask):
    """`ask()`, answered once per `name` and folder inside a `repo_facts()` block, and every time outside one."""
    if _FACTS is None:
        return ask()
    key = (name, os.path.normcase(os.path.abspath(str(root))))
    if key not in _FACTS:
        _FACTS[key] = ask()
    return _FACTS[key]


def _in_git(root) -> bool:
    def ask():
        probe = _call(["rev-parse", "--git-dir"], root)
        return not isinstance(probe, Unreadable) and probe.returncode == 0
    return fact("in_git", root, ask)


def _out(args, root, input=None):
    """The GitResult of a read, or the Unreadable result when the process could not run."""
    done = _call(args, root, input=input)
    return done


# ---- path identity ----------------------------------------------------------------------------------------


def submodule_paths(top: Path) -> list[str]:
    """The gitlink paths (mode 160000) in `top`'s index, longest first."""
    done = _call(["ls-files", "--stage"], top)
    if isinstance(done, Unreadable) or done.returncode != 0:
        return []
    found = [line.split("\t", 1)[1] for line in _text(done.stdout).splitlines()
             if line.startswith("160000 ") and "\t" in line]
    return sorted(found, key=len, reverse=True)


def split_submodule(top: Path, path: str):
    """`(gitlink path, path inside it)` when `path` lies under a submodule of `top`, else None."""
    for sub in submodule_paths(top):
        if path.startswith(sub + "/"):
            return sub, path[len(sub) + 1:]
    return None


def worktree_split(path: str):
    """`(".claude/worktrees/<name>", path inside it)` when a repo-relative `path` lies under a linked worktree's
    root, else None. Text only: the caller checks the worktree exists."""
    parts = path.split("/", 3)
    if len(parts) == 4 and parts[:2] == [".claude", "worktrees"]:
        return "/".join(parts[:3]), parts[3]
    return None


def split_worktree(top: Path, path: str):
    """`(linked worktree directory, path inside it)` when `path` lies under `.claude/worktrees/<name>/` of `top`,
    else None. A linked worktree shares `top`'s object store, so its files hash at any commit of `top`."""
    found = worktree_split(path)
    if found and (top / found[0] / ".git").exists():
        return found
    return None


def owning_repo(top: Path, path: str) -> tuple[Path, str]:
    """`(checkout, path inside it)` for the deepest submodule checkout that holds `path`, else `(top, path)`."""
    found = split_submodule(top, path)
    return owning_repo(top / found[0], found[1]) if found else (top, path)


def normalize_files(toplevel: Path, files) -> list[str]:
    """Repo-relative POSIX paths: an absolute path inside `toplevel` loses that prefix, and `./` goes."""
    out = []
    for raw in files:
        path = Path(str(raw).replace("\\", "/"))
        if path.is_absolute():
            try:
                path = path.relative_to(toplevel)
            except ValueError:
                pass
        text = path.as_posix()
        out.append(text[2:] if text.startswith("./") else text)
    return list(dict.fromkeys(out))


def _key(value):
    return os.path.normcase(os.path.normpath(str(value).replace("\\", "/"))).replace("\\", "/").lower()


def _cited_text(cited):
    text = str(cited or "").strip().replace("\\", "/")
    head, sep, tail = text.rpartition(":")
    if sep and tail.isdigit() and head:
        text = head
    return text[2:] if text.startswith("./") else text


def resolve_citation(root, cited, candidates):
    """The one candidate a finding's citation names, or None.

    A citation that already names a candidate exactly resolves to it. A relative citation that omits the
    repo-relative prefix — `plan.md` for `.claude/plans/plan.md` — resolves only when the candidate set holds one
    file and the citation is that file's path suffix; any other relative or absolute citation is left to the
    caller's exact match. The narrowness is deliberate: with several files in play a bare name is ambiguous, and
    a suffix pass would route a finding about another file into the subject.
    """
    text = _cited_text(cited)
    if not text or Path(text).is_absolute():
        return None
    keys = {item: _key(item) for item in candidates}
    wanted = _key(text)
    exact = [item for item in candidates if keys[item] == wanted]
    if exact:
        return exact[0]
    if len(candidates) != 1:
        return None
    only = candidates[0]
    return only if keys[only].endswith("/" + wanted) else None


def _literal(path):
    return ":(literal)" + path


# ---- object reads -----------------------------------------------------------------------------------------


def cat_blob(root: Path, sha: str):
    """The bytes of blob `sha` in `root`'s object store, or None when it is absent or unreadable."""
    done = _call(["cat-file", "blob", sha], root)
    return None if isinstance(done, Unreadable) or done.returncode != 0 else done.stdout


def hash_object(root: Path, data: bytes, rel: str | None = None, *, write=False, filters=False) -> str:
    """The blob id of `data`. `rel` with `filters=True` applies the checkout's clean filters to the bytes; `write`
    stores the object. Raises OSError when git cannot hash."""
    args = ["hash-object"] + (["-w"] if write else []) + (["--path", rel] if filters and rel else []) + ["--stdin"]
    done = _call(args, root, input=data)
    if isinstance(done, Unreadable) or done.returncode != 0:
        reason = done.reason if isinstance(done, Unreadable) else _text(done.stderr).strip()
        raise OSError(reason or "git hash-object failed")
    return _text(done.stdout).strip()


def hash_objects(root: Path, paths, *, write=False) -> dict:
    """path -> blob id for each worktree file, raw bytes, in one call."""
    ordered = list(paths)
    if not ordered:
        return {}
    # An id is compare-only unless the caller stores it for a later reader; the frozen-blob proof lists this line.
    # `--stdin-paths` resolves a relative path from the toplevel, not the cwd, so the input is absolute.
    args = ["hash-object", "--stdin-paths"]
    if write:
        args.insert(1, "-w")
    absolute = [(Path(root) / path).as_posix() for path in ordered]
    done = _call(args, root, input=("\n".join(absolute) + "\n").encode("utf-8"))
    if isinstance(done, Unreadable) or done.returncode != 0:
        reason = done.reason if isinstance(done, Unreadable) else _text(done.stderr).strip()
        raise OSError(reason or "git hash-object failed")
    ids = _text(done.stdout).split()
    if len(ids) != len(ordered):
        raise OSError(f"hash-object gave {len(ids)} ids for {len(ordered)} files")
    return dict(zip(ordered, ids))


def object_kinds(root: Path, shas) -> dict:
    """sha -> object type in `root`'s store (`missing` when absent)."""
    ordered = sorted(set(shas))
    if not ordered:
        return {}
    done = _call(["cat-file", "--batch-check"], root, input=("\n".join(ordered) + "\n").encode("utf-8"))
    if isinstance(done, Unreadable) or done.returncode != 0:
        return {sha: "unreadable" for sha in ordered}
    out = {}
    for line in _text(done.stdout).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return {sha: out.get(sha, "missing") for sha in ordered}


def _diff_lines(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def hunks(before: bytes, after: bytes) -> list[dict]:
    """The unified-diff hunks between two texts, each `{"id": <sha1>, "text": <hunk>}`.

    The id is the sha1 of the hunk's normalized lines (line endings read as LF, trailing whitespace
    stripped), so the same hunk text yields the same id wherever it is read. `prepare` writes the ids,
    and the ledger recomputes them at consume, so both call this one function."""
    out, current = [], None
    for line in difflib.unified_diff(_diff_lines(before), _diff_lines(after)):
        if line.startswith("@@"):
            if current is not None:
                out.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        out.append(current)
    return [{"id": hashlib.sha1("\n".join(line.replace("\r", "").rstrip() for line in group).encode("utf-8")
                                ).hexdigest(), "text": "".join(group)} for group in out]


def diff(before: bytes, after: bytes, fromfile: str = "before", tofile: str = "after") -> str:
    """The unified diff between two texts, for a seat to read."""
    return "".join(difflib.unified_diff(_diff_lines(before), _diff_lines(after),
                                        fromfile=fromfile, tofile=tofile))


def _object_problem(root: Path, shas) -> str | None:
    kinds = object_kinds(root, shas)
    bad = sorted(sha for sha, kind in kinds.items() if kind != "blob")
    return f"unreadable object(s): {', '.join(bad)}" if bad else None


# ---- the snapshot -----------------------------------------------------------------------------------------


class Snapshot:
    """One opened source. `blobs` answers per path; every result is cached for this snapshot only."""

    def __init__(self, repo: Path, source=None, rev: str | None = None):
        self.repo = Path(repo)
        self.source = Disk() if source is None else source
        self._head = None
        self._cache: dict = {}
        self._ancestor: dict = {}
        self._merges: dict = {}
        self._resolved: dict = {}
        self._revision = None
        if isinstance(self.source, Commit):
            # Resolve the revision once, in this repository, so a linked or foreign checkout is read at the
            # commit it holds rather than at whatever ref the string happens to name there.
            self._revision = rev or self.commit_for(self.source.rev) or self.source.rev
        elif isinstance(self.source, Index):
            self._revision = "INDEX"
        elif isinstance(self.source, Recorded):
            self._revision = self.source.row.get("head")

    # -- repository facts --

    @property
    def toplevel(self) -> Path:
        """The directory every path here is relative to: the repo as opened, which is the harness root and may sit
        below the git toplevel. Git forms that resolve from the toplevel (`rev:path`, `--stdin-paths`, `--full-tree`,
        unqualified `--name-only`) are never handed these paths; use `rev:./path`, an absolute path or `--relative`."""
        return self.repo

    @property
    def head(self):
        """HEAD's commit as the repository reports it, or None when it cannot be read."""
        if self._head is None:
            self._head = self.commit_for("HEAD")
        return self._head

    @property
    def revision(self):
        """The commit this snapshot reads: the source's revision, `HEAD` for a disk read, or None."""
        if isinstance(self.source, Commit):
            return self._revision
        if isinstance(self.source, Recorded):
            return self.source.row.get("head")
        return self.head

    def commit_for(self, rev: str):
        def ask():
            done = _call(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], self.repo)
            return None if isinstance(done, Unreadable) or done.returncode != 0 else _text(done.stdout).strip()
        # No command moves HEAD; a fetch can make another revision resolvable, so only HEAD is asked once.
        return fact("HEAD", self.repo, ask) if rev == "HEAD" else ask()

    # -- content --

    def blobs(self, paths) -> dict:
        ordered = list(dict.fromkeys(str(path) for path in paths))
        todo = [path for path in ordered if path not in self._cache]
        if todo:
            self._fill(todo)
        return {path: self._cache[path] for path in ordered}

    def hash(self, path: str):
        return self.blobs([path])[path]

    def deleted(self, path: str):
        """The path's content result where `Deleted` means a committed deletion and `Missing` means none."""
        result = self.hash(path)
        if isinstance(result, (Blob, Deleted, Unreadable)):
            return result
        return self._tombstone(path) or MISSING

    def _fill(self, paths):
        if isinstance(self.source, Disk):
            self._fill_disk(paths)
        elif isinstance(self.source, Recorded):
            self._fill_recorded(paths)
        else:
            worktrees, links, flat = {}, {}, []
            for path in paths:
                home = split_worktree(self.repo, path)
                if home:
                    worktrees[path] = home
                else:
                    links[path] = split_submodule(self.repo, path)
                    if links[path] is None:
                        flat.append(path)
            found = self._entries(self.repo, self._revision, flat) if flat else {}
            by_home = {}
            for path, (home, rest) in worktrees.items():
                by_home.setdefault(home, {})[rest] = path
            for home, rests in by_home.items():
                listed = self._entries(self.repo / home, self._revision, list(rests))
                for rest, path in rests.items():
                    found[path] = listed[rest]
            for path in paths:
                if path in worktrees:
                    continue
                elif links[path] is not None:
                    found[path] = self._linked_entry(*links[path], path)
            for path, value in found.items():
                self._cache[path] = value
            for path in paths:
                if isinstance(self._cache.get(path), Missing):
                    tombstone = self._tombstone(path)
                    if tombstone is not None:
                        self._cache[path] = tombstone

    def _entries(self, root: Path, revision: str, paths) -> dict:
        """{path: Blob|Missing|Unreadable} for `paths` that are not under a worktree or submodule."""
        if not paths:
            return {}
        if revision == "INDEX":
            return self._index_entries(root, paths)
        done = _call(["ls-tree", "-r", "-z", revision, "--"] + [_literal(p) for p in paths], root)
        if isinstance(done, Unreadable) or done.returncode != 0:
            reason = done.reason if isinstance(done, Unreadable) else (
                _text(done.stderr).strip() or f"cannot list {revision}")
            return {path: Unreadable(reason) for path in paths}
        seen = {}
        for entry in _text(done.stdout).split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                seen[path] = parts[2]
        out = {}
        blobs = {path: sha for path, sha in seen.items() if path in paths}
        problem = _object_problem(root, blobs.values()) if blobs else None
        for path in paths:
            if path not in seen:
                out[path] = MISSING
            elif problem:
                out[path] = Unreadable(problem)
            else:
                out[path] = Blob(seen[path])
        return out

    def _index_entries(self, root: Path, paths) -> dict:
        done = _call(["ls-files", "-s", "-z", "--"] + [_literal(p) for p in paths], root)
        if isinstance(done, Unreadable) or done.returncode != 0:
            reason = done.reason if isinstance(done, Unreadable) else _text(done.stderr).strip()
            return {path: Unreadable(reason or "cannot read the index") for path in paths}
        staged = {}
        for entry in _text(done.stdout).split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) >= 3 and str(parts[2]) == "0":
                staged[path] = parts[1]
        problem = _object_problem(root, staged.values()) if staged else None
        return {path: (Unreadable(problem) if problem else Blob(staged[path])) if path in staged else MISSING
                for path in paths}

    def _linked_entry(self, link: str, rest: str, path: str):
        pinned = _call(["rev-parse", "--verify", "--quiet", f"{self._revision}:./{link}"], self.repo)
        if isinstance(pinned, Unreadable):
            return Unreadable(pinned.reason)
        pin = "" if pinned.returncode != 0 else _text(pinned.stdout).strip()
        if not pin:
            return MISSING
        inner = self.repo / link
        if not (inner / ".git").exists():
            return Unreadable(f"cannot read submodule {link}: it is not checked out")
        found = Snapshot(inner, Commit(pin)).hash(rest)
        if isinstance(found, Unreadable):
            return Unreadable(f"cannot read submodule {link} at {pin}: {found.reason}")
        return found

    def _fill_disk(self, paths):
        present = []
        for path in paths:
            if (self.toplevel / path).is_file():
                present.append(path)
            else:
                self._cache[path] = MISSING
        if not present:
            return
        top = self.toplevel
        if _in_git(top):
            try:
                for path, sha in hash_objects(top, present, write=True).items():
                    self._cache[path] = Blob(sha)
            except OSError as exc:
                for path in present:
                    self._cache[path] = Unreadable(str(exc))
        else:
            for path in present:
                try:
                    data = (top / path).read_bytes()
                except OSError as exc:
                    self._cache[path] = Unreadable(str(exc))
                    continue
                self._cache[path] = Blob("sha256:" + hashlib.sha256(data).hexdigest())

    def _fill_recorded(self, paths):
        files = self.source.row.get("files") or {}
        checkout = row_checkout(self.source.row)
        for path in paths:
            value = files.get(path)
            if value is None:
                found = worktree_split(path)
                if found and checkout in (None, found[0]):
                    value = files.get(found[1])
            if value is None:
                self._cache[path] = MISSING
            elif isinstance(value, str) and value.startswith("deleted:"):
                self._cache[path] = Deleted(value)
            elif isinstance(value, str):
                self._cache[path] = Blob(value)
            else:
                self._cache[path] = Unreadable(f"recorded value for {path} is not a blob id")

    # -- deletion history --

    def _tombstone(self, path: str):
        head = self.revision
        if not head or isinstance(self.source, Disk):
            return None
        if self.commit_for(head) is None:
            return None
        found = split_submodule(self.toplevel, path)
        pinned = None
        if found:
            done = _call(["rev-parse", "--verify", "--quiet", f"{head}:./{found[0]}"], self.toplevel)
            if isinstance(done, Unreadable):
                return Unreadable(done.reason)
            pinned = None if done.returncode != 0 else _text(done.stdout).strip()
        if pinned:
            inner = self.toplevel / found[0]
            if not (inner / ".git").exists():
                return Unreadable(f"cannot read submodule {found[0]}: run `git submodule update "
                                  f"--init {found[0]}` so the pinned commits are local")
            commits = _log(
                ["log", "-1", "--format=%H", "--no-renames", "--diff-filter=D", pinned, "--", found[1]], inner)
        else:
            commits = _log(["log", "-1", "--format=%H", "--no-renames", "--diff-filter=D", head, "--",
                            found[0] if found else path], self.toplevel)
        return Deleted("deleted:" + commits[0]) if commits else None

    # -- history --

    def reaches(self, sha: str, path: str | None = None) -> bool:
        """HEAD holds the history `sha` reviewed of `path` (of every file when None)."""
        if sha not in self._ancestor:
            unmatched = None
            if sha == self.head or _is_ancestor(self.repo, sha, "HEAD"):
                unmatched = set()
            elif _commit_present(self.repo, sha):
                cherry = _call(["cherry", "HEAD", sha], self.repo)
                if not isinstance(cherry, Unreadable) and cherry.returncode == 0:
                    unmatched = set()
                    replays = self._unmatched_by_subject(sha)
                    for line in _text(cherry.stdout).splitlines():
                        if line.startswith("+"):
                            unmatched.update(self._changed_paths(line[2:].strip(), replays))
            self._ancestor[sha] = unmatched
        unmatched = self._ancestor[sha]
        return unmatched is not None and (not unmatched if path is None else path not in unmatched)

    def _unmatched_by_subject(self, sha: str) -> dict:
        cherry = _call(["cherry", "-v", sha, "HEAD"], self.repo)
        found: dict = {}
        if isinstance(cherry, Unreadable) or cherry.returncode != 0:
            return found
        for line in _text(cherry.stdout).splitlines():
            if line.startswith("+"):
                commit, _, subject = line[2:].partition(" ")
                subject = subject.strip()
                found[subject] = None if subject in found else commit
        return found

    def _changed_paths(self, old: str, replays: dict) -> set:
        paths = set(_lines(["show", "--no-renames", "--relative", "--name-only", "--format=", old], self.repo))
        subject = _text(_call(["log", "-1", "--format=%s", old], self.repo).stdout).strip()
        new = replays.get(subject)
        if not new:
            return paths
        paths |= set(_lines(["show", "--no-renames", "--relative", "--name-only", "--format=", new], self.repo))
        ids = self.__dict__.setdefault("_patch_ids", {})
        return {path for path in paths if _file_patch_id(self.repo, old, path, ids)
                != _file_patch_id(self.repo, new, path, ids)}

    def merge_touched(self, sha: str, path: str) -> bool:
        """True when a merge between `sha` and HEAD rewrote `path` in its conflict resolution."""
        if sha not in self._merges:
            done = _call(["rev-list", "--merges", f"{sha}..HEAD"], self.repo)
            self._merges[sha] = _text(done.stdout).split() if not isinstance(done, Unreadable) and done.returncode == 0 else []
        for merge in self._merges[sha]:
            if merge not in self._resolved:
                shown = _call(["show", "--remerge-diff", "--relative", "--name-only", "--format=", merge], self.repo)
                self._resolved[merge] = set(_text(shown.stdout).split("\n")) if not isinstance(shown, Unreadable) else set()
            if path in self._resolved[merge]:
                return True
        return False


def open(repo, source=None):  # noqa: A001 - the module's constructor name, matching the plan's seam
    """Open `repo` over `source` (default: the worktree)."""
    return Snapshot(repo, source)


# ---- module-level history reads ---------------------------------------------------------------------------


def _lines(args, root):
    done = _call(args, root)
    return [] if isinstance(done, Unreadable) or done.returncode != 0 else _text(done.stdout).splitlines()


def _log(args, root):
    return [line for line in _lines(args, root) if line.strip()]


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    done = _call(["merge-base", "--is-ancestor", ancestor, descendant], repo)
    return not isinstance(done, Unreadable) and done.returncode == 0


def _commit_present(repo: Path, sha: str) -> bool:
    done = _call(["cat-file", "-e", sha + "^{commit}"], repo)
    return not isinstance(done, Unreadable) and done.returncode == 0


def _patch_id(repo: Path, sha: str, cache: dict):
    """A commit's `git patch-id --stable`, memoized; None when it has no patch."""
    if sha not in cache:
        show = _call(["show", "--no-color", "--no-renames", "--format=", sha], repo)
        if isinstance(show, Unreadable) or show.returncode != 0 or not show.stdout.strip():
            cache[sha] = None
        else:
            out = _call(["patch-id", "--stable"], repo, input=show.stdout)
            words = _text(out.stdout).split() if not isinstance(out, Unreadable) and out.returncode == 0 else []
            cache[sha] = words[0] if words else "unreadable:" + sha
    return cache[sha]


def _file_patch_id(repo: Path, commit: str, path: str, cache: dict):
    key = (commit, path)
    if key not in cache:
        show = _call(["show", "--no-color", "--no-renames", "--format=", commit, "--", path], repo)
        if isinstance(show, Unreadable) or show.returncode != 0 or not show.stdout.strip():
            cache[key] = None
        else:
            out = _call(["patch-id", "--stable"], repo, input=show.stdout)
            words = _text(out.stdout).split() if not isinstance(out, Unreadable) and out.returncode == 0 else []
            cache[key] = words[0] if words else "unreadable:" + commit
    return cache[key]


def merge_base(repo: Path, base_ref: str, head: str | None):
    done = _call(["merge-base", base_ref, head or "HEAD"], repo)
    return None if isinstance(done, Unreadable) or done.returncode != 0 else _text(done.stdout).strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _is_ancestor(Path(repo), ancestor, descendant)


def commit_present(repo: Path, sha: str) -> bool:
    return _commit_present(Path(repo), sha)


def patch_id(repo: Path, sha: str, cache: dict):
    return _patch_id(Path(repo), sha, cache)


def blob_at(top: Path, revision: str, path: str):
    """The blob sha of `path` at `revision` in `top`, or None when absent or unreadable. A path under a submodule
    is read from that submodule's object store at the commit the gitlink pins at `revision`."""
    found = split_submodule(top, path)
    if found:
        pinned = _call(["rev-parse", "--verify", "--quiet", f"{revision}:./{found[0]}"], top)
        commit = "" if isinstance(pinned, Unreadable) or pinned.returncode != 0 else _text(pinned.stdout).strip()
        if not commit or not (top / found[0] / ".git").exists():
            return None
        return blob_at(top / found[0], commit, found[1])
    result = open(top, Commit(revision)).hash(path)
    return result.sha if isinstance(result, Blob) else None


def readable_submodule(top: Path, link: str, pins) -> Path:
    """The checkout of submodule `link` when it holds every commit in `pins`."""
    inside = top / link
    if not (inside / ".git").exists() or any(
            not _commit_present(inside, pin) for pin in pins):
        raise SnapshotError(f"cannot read submodule {link} at {', '.join(pins)}: run `git submodule update "
                            f"--init {link}` and fetch it so the pinned commits are local")
    return inside


def gitlink_files(top: Path, link: str, old: str, new: str) -> set:
    """`<link>/<file>` for each file submodule `link` changed between pins `old` and `new`; an all-zero pin is
    absent, so every file the other pin holds changed."""
    pins = [pin for pin in (old, new) if pin.strip("0")]
    inside = readable_submodule(top, link, pins)
    args = (["diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACDMR", old, new] if len(pins) == 2
            else ["ls-tree", "-r", "--name-only", "-z", pins[0]])
    done = _call(args, inside)
    if isinstance(done, Unreadable) or done.returncode != 0:
        raise SnapshotError(f"cannot list the files submodule {link} changed between {old} and {new}")
    return {f"{link}/{name}" for name in _text(done.stdout).split("\0") if name}


def range_commits(repo: Path, base: str, rev: str) -> list[dict]:
    """Commits in base..rev as {sha, subject, files}, oldest first; merges list only authored resolutions."""
    rng = f"{base}..{rev}"
    done = _call(["log", "--reverse", "--cc", "--no-renames", "--diff-filter=ACDMR", "--raw", "--no-abbrev",
                  "--format=@@%H\t%s", rng], repo)
    if isinstance(done, Unreadable) or done.returncode != 0:
        detail = done.reason if isinstance(done, Unreadable) else _text(done.stderr).strip()
        raise OSError(f"cannot list commits in {rng}: {detail}")
    commits, current, raws = [], None, []
    for line in _text(done.stdout).splitlines():
        if line.startswith("@@"):
            sha, _, subject = line[2:].partition("\t")
            current = {"sha": sha, "subject": subject, "files": []}
            commits.append(current)
        elif line.startswith(":") and current is not None:
            meta, _, path = line.partition("\t")
            raws.append((current, meta, path))
    # `--relative` is ignored for a merge's combined diff, so every name arrives toplevel-relative and maps back here.
    prefix = _call(["rev-parse", "--show-prefix"], repo)
    prefix = "" if isinstance(prefix, Unreadable) or prefix.returncode != 0 else _text(prefix.stdout).strip()
    inside = _root_relative(prefix, [path for _, _, path in raws])
    for current, meta, raw in raws:
        if raw not in inside:
            continue
        path = inside[raw]
        parents = len(meta) - len(meta.lstrip(":"))
        fields = meta.lstrip(":").split()
        modes, pins = fields[:parents + 1], fields[parents + 1:2 * parents + 2]
        if "160000" in modes:
            changed = set.intersection(*(gitlink_files(repo, path, old, pins[-1]) for old in pins[:-1]))
            current["files"].extend(sorted(changed))
        else:
            current["files"].append(path)
    return commits


# ---- ledger rows ------------------------------------------------------------------------------------------


def row_checkout(row: dict):
    """The checkout a row recorded: None for a row written before the `repo` field, `.claude/worktrees/<name>` for a
    linked worktree, `.` for the main checkout."""
    repo = row.get("repo")
    if not repo:
        return None
    parts = str(repo).replace("\\", "/").rstrip("/").split("/")
    if parts[-3:-1] == [".claude", "worktrees"]:
        return "/".join(parts[-3:])
    return "."


def row_blob(row: dict, path: str):
    """The blob `row` recorded for `path`, with the worktree alias fallback."""
    files = row.get("files") or {}
    blob = files.get(path)
    found = worktree_split(path)
    if blob is None and found and row_checkout(row) in (None, found[0]):
        blob = files.get(found[1])
    return blob
