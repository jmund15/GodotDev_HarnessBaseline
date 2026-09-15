#!/usr/bin/env python3
"""
baseline_sync.py — sync a project's .claude/ harness with a shared baseline repo.

Upstream content is read from one pinned commit through git's object store. The
baseline checkout is only a repository handle; its working tree is never used
for upstream comparisons.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

# `baseline_identity` lives beside this file in the same `tools/` directory; it only imports
# `baseline_sync` back lazily, inside a function body (`_load_baseline_sync`, called from
# `_pinned_template_tokens`), never at its own module scope -- so this top-level import can
# never race a half-initialized sibling.
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import baseline_identity  # noqa: E402
import baseline_compose  # noqa: E402
import adaptation  # noqa: E402

LOCK_RELPATH = ".claude/baseline.lock.json"
CACHE_CLONE = ".claude/.cache/baseline-repo"
MANIFEST_NAME = "baseline.manifest.json"
FULL_LAYERS = ("pure", "coding", "godot")
V2_STATUSES = ("tracked", "local", "forked", "composed")
VERDICTS = ("push", "keep-local", "fork")
LOCK_MUTEX_RELPATH = ".claude/.cache/baseline-lock.mutex"
WATCH_LOCAL_RELPATHS = {
    ".claude/skills/game_vision/SKILL.md",
    ".claude/worklog-titles.md",
    ".claude/commands/checklists/known_failure_modes.md",
    ".claude/auto-memory/MEMORY.md",
}
WATCH_COMPOSED_INPUTS = {
    ".claude/settings.json": [".claude/settings.base.json", ".claude/settings.project.json"],
    ".claude/reference/memory_domains.md": [
        ".claude/reference/memory_domains.base.md",
        ".claude/skills/project_subsystems/adaptation.json",
    ],
}
WATCH_OTHER_RELPATHS = {
    ".claude/skills/_brainstorm_shared/common.md",
    ".claude/skills/architecture_brainstorm/SKILL.md",
    ".claude/skills/debugging/SKILL.md",
    ".claude/hooks/plan_memory_reminder.py",
    ".claude/skills/architecture_philosophy/structure_rules.md",
    ".claude/commands/structure_audit.md",
    ".claude/commands/plan_part.md",
    ".claude/commands/agents/structure_audit_agents.md",
    ".claude/commands/session_end.md",
    ".claude/commands/pr_test_checklist.md",
    ".claude/commands/merge_pr.md",
    ".claude/commands/reindex_search.md",
    ".claude/skills/worklog_reference/SKILL.md",
    ".claude/rules/godot_files.md",
    ".claude/commands/audit_test_accessors.md",
    ".claude/commands/sync_subsystems.md",
    ".claude/tools/extract_subagent_tools.py",
    ".claude/commands/sync_baseline.md",
    ".claude/commands/aiworker_write_doc_audit.md",
}


class BaselineError(RuntimeError):
    """A baseline repository could not provide a usable pinned source."""


class UsageError(RuntimeError):
    """Command arguments are invalid and no repository read or write is allowed."""


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def normalize(data: bytes) -> bytes:
    return _lf(data)


def replace_path(source: Path | str, target: Path | str, wait_s: float = 10.0) -> None:
    """os.replace that waits out a reader holding the target.

    Windows refuses a rename onto a path another process holds open (WinError 5); an indexer
    or scanner reading a journal or lock is enough. Retry until `wait_s` passes, then raise.
    """
    deadline = time.monotonic() + wait_s
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def sha(data: bytes) -> str:
    return hashlib.sha256(normalize(data)).hexdigest()


def _write_lf(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    path.write_bytes(_lf(data))


def _repo_arg(path: Path) -> str:
    return str(path)


def _git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", _repo_arg(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BaselineError(f"git {' '.join(args)} failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = _lf(result.stderr).decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise BaselineError(f"git {' '.join(args)} failed{suffix}")
    return result


def _git_text(repo: Path, args: list[str]) -> str:
    return _lf(_git(repo, args).stdout).decode("utf-8", errors="replace").strip()


def _git_sha(repo: Path, rev: str) -> str:
    value = _git_text(repo, ["rev-parse", "--verify", f"{rev}^{{commit}}"])
    if not value:
        raise BaselineError(f"could not resolve baseline revision {rev}")
    return value.splitlines()[0]


def _git_repo(path: Path) -> bool:
    result = _git(path, ["rev-parse", "--git-dir"], check=False)
    return result.returncode == 0


def _normalize_repo_url(value: str) -> str:
    """Normalize URL and local-path forms returned by git on each platform."""
    text = value.strip().replace("\\", "/")
    parsed = urlsplit(text)
    if parsed.scheme.lower() == "file":
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            path = "//" + parsed.netloc + path
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        text = path
    text = text.rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.casefold()


def _origin(path: Path) -> str:
    result = _git(path, ["remote", "get-url", "origin"], check=False)
    if result.returncode != 0:
        raise BaselineError(f"baseline checkout at {path} has no origin remote")
    return _lf(result.stdout).decode("utf-8", errors="replace").strip()


def _assert_origin(path: Path, expected: str) -> None:
    actual = _origin(path)
    if _normalize_repo_url(actual) != _normalize_repo_url(expected):
        raise BaselineError(
            f"baseline origin mismatch at {path}: expected {expected}, got {actual}"
        )


def _has_linked_worktree(path: Path) -> bool:
    result = _git(path, ["worktree", "list", "--porcelain"], check=False)
    if result.returncode != 0:
        return False
    entries = [line for line in _lf(result.stdout).decode(errors="replace").splitlines()
               if line.startswith("worktree ")]
    return len(entries) > 1


def _clone_no_checkout(repo: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--no-checkout", repo, str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BaselineError(f"could not clone baseline: {exc}") from exc
    if result.returncode != 0:
        detail = _lf(result.stderr).decode("utf-8", errors="replace").strip()
        raise BaselineError(f"could not clone baseline{(': ' + detail) if detail else ''}")


def _remove_tree(path: Path) -> None:
    def _chmod_retry(function, target, _exc_info):
        os.chmod(target, 0o700)
        function(target)

    if path.exists():
        hook = "onexc" if sys.version_info >= (3, 12) else "onerror"
        shutil.rmtree(path, **{hook: _chmod_retry})
    if path.exists():
        raise BaselineError(f"could not remove {path}")


def _replace_cache(cache: Path, repo: str) -> Path:
    temporary = cache.with_name(cache.name + ".replace")
    _remove_tree(temporary)
    _clone_no_checkout(repo, temporary)
    _remove_tree(cache)
    replace_path(temporary, cache)
    return cache


def _resolve_default(cache: Path, repo: str, ref: str) -> str:
    fetched = _git(cache, ["fetch", "origin", ref], check=False)
    if fetched.returncode != 0:
        detail = _lf(fetched.stderr).decode("utf-8", errors="replace").strip()
        raise BaselineError(
            f"could not fetch baseline ref {ref}{(': ' + detail) if detail else ''}"
        )
    try:
        pinned = _git_sha(cache, "FETCH_HEAD")
    except BaselineError as exc:
        raise BaselineError(f"could not resolve baseline ref {ref}") from exc
    commit_check = _git(cache, ["cat-file", "-e", f"{pinned}^{{commit}}"], check=False)
    if commit_check.returncode != 0:
        fetched_commit = _git(cache, ["fetch", "--depth", "1", "origin", pinned], check=False)
        if fetched_commit.returncode != 0:
            detail = _lf(fetched_commit.stderr).decode("utf-8", errors="replace").strip()
            raise BaselineError(
                f"could not fetch pinned baseline {pinned}{(': ' + detail) if detail else ''}"
            )
        if _git(cache, ["cat-file", "-e", f"{pinned}^{{commit}}"], check=False).returncode != 0:
            raise BaselineError(f"baseline commit {pinned} is not readable")
    return pinned


def _batch_header(header: bytes) -> tuple[bytes, int] | None:
    """(object type, size) from one `git cat-file --batch` header line; None for a missing object."""
    fields = header.rstrip(b"\n").split(b" ")
    if fields[-1] == b"missing":
        return None
    if len(fields) != 3 or fields[1] not in (b"blob", b"commit", b"tree"):
        detail = header.decode("utf-8", errors="replace").strip()
        raise BaselineError(f"unexpected git cat-file response: {detail}")
    try:
        return fields[1], int(fields[2])
    except ValueError as exc:
        raise BaselineError("git cat-file returned an invalid object size") from exc


class _BatchReader:
    """One persistent `git cat-file --batch` reader for an invocation."""

    def __init__(self, repo: Path, commit: str):
        self.commit = commit
        try:
            self.process = subprocess.Popen(
                ["git", "-C", str(repo), "cat-file", "--batch"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise BaselineError(f"could not start git cat-file --batch: {exc}") from exc

    def read(self, relpath: str) -> bytes | None:
        if self.process.stdin is None or self.process.stdout is None:
            raise BaselineError("git cat-file --batch pipes are unavailable")
        query = f"{self.commit}:template/{relpath}\n".encode("utf-8")
        try:
            self.process.stdin.write(query)
            self.process.stdin.flush()
            header = self.process.stdout.readline()
        except OSError as exc:
            raise BaselineError(f"git cat-file --batch read failed: {exc}") from exc
        if not header:
            raise BaselineError("git cat-file --batch exited before returning a result")
        parsed = _batch_header(header)
        if parsed is None:
            return None
        _kind, size = parsed
        try:
            body = self.process.stdout.read(size)
            separator = self.process.stdout.read(1)
        except OSError as exc:
            raise BaselineError(f"git cat-file --batch body read failed: {exc}") from exc
        if len(body) != size or separator != b"\n":
            raise BaselineError("git cat-file returned a truncated object")
        return body

    def close(self) -> None:
        stdin = self.process.stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


@dataclass
class BaselineSource:
    path: Path
    sha: str
    batch: _BatchReader

    def read(self, relpath: str) -> bytes | None:
        return self.batch.read(relpath)

    def close(self) -> None:
        self.batch.close()

    def __fspath__(self) -> str:
        return str(self.path)

    def __truediv__(self, value: str) -> Path:
        return self.path / value


def resolve_layers(lock: dict, layers_arg: str | None) -> list[str]:
    """Resolve --layers, the lock profile, or all supported layers."""
    if layers_arg:
        parts = [x.strip() for x in layers_arg.split(",")]
    else:
        profile = lock.get("profile")
        parts = [x.strip() for x in profile.split(",")] if profile else list(FULL_LAYERS)
    bad = [x for x in parts if x not in FULL_LAYERS]
    if bad:
        sys.exit("error: invalid layer value(s): %s — expected pure,coding,godot" % ", ".join(bad))
    seen, out = set(), []
    for item in parts:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    if not out:
        sys.exit("error: --layers resolved to an empty set — expected pure,coding,godot")
    return out


def in_profile(entry: dict, layers: list[str], *, legacy_v1: bool = False) -> bool:
    layer = entry.get("layer")
    if not layer or layer in layers:
        return True
    if layer == "project":
        # A v2 local-row marker, never a wildcard, on either read path.
        return False
    # A v1 lock's other layer names (e.g. "universal", "jmodot") predate the
    # v2 FULL_LAYERS vocabulary; only the v1-lock read path treats one of
    # those as a wildcard. Any other non-profile v2 layer stays excluded.
    return legacy_v1 and layer not in FULL_LAYERS


def project_root() -> Path:
    """The nearest directory holding `.claude/`, searched upward from cwd but never above the git
    top level: an ancestor `.claude/` outside the repository (a home directory has one) is not
    this project's."""
    path = Path.cwd().resolve()
    top = None
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path,
                           capture_output=True, text=True)
    if probe.returncode == 0 and probe.stdout.strip():
        top = Path(probe.stdout.strip()).resolve()
    while True:
        if (path / ".claude").is_dir():
            return path
        if path == top or path == path.parent:
            break
        path = path.parent
    sys.exit("error: no .claude/ directory found upward from cwd")


def load_lock(root: Path) -> dict:
    lock_path = root / LOCK_RELPATH
    if not lock_path.exists():
        sys.exit(f"error: {LOCK_RELPATH} not found — run 'init' first")
    try:
        lock = json.loads(_lf(lock_path.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.exit(f"error: could not read {LOCK_RELPATH}: {exc}")
    subs = lock.get("substitutions")
    if isinstance(subs, dict) and "{{PROJECT_ROOT}}" in subs:
        subs["{{PROJECT_ROOT}}"] = str(root).replace("\\", "/")
    return lock


def save_lock(root: Path, lock: dict) -> None:
    """Write the lock through a same-directory temp file and atomic replace."""
    to_write = dict(lock)
    subs = lock.get("substitutions")
    if isinstance(subs, dict) and "{{PROJECT_ROOT}}" in subs:
        to_write["substitutions"] = {**subs, "{{PROJECT_ROOT}}": ""}
    payload = (json.dumps(to_write, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
    lock_path = root / LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=lock_path.name + ".", suffix=".tmp", dir=str(lock_path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_lf(payload.encode("utf-8")))
            stream.flush()
            os.fsync(stream.fileno())
        replace_path(temporary, lock_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass



def _try_os_lock(handle) -> bool:
    """Take an exclusive, non-blocking OS lock on the open mutex file; False when another holds it."""
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_os_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def lock_mutex(root: Path):
    """Serialize lock mutations with an OS file lock on a persistent mutex file.

    The OS drops the lock when its holder exits, so a crashed holder never needs a staleness
    guess. The earlier pid-file mutex had two races: a waiter deleted a mutex whose creator had
    not yet written its pid, and `os.kill(pid, 0)` is not a liveness probe on Windows."""
    mutex = root / LOCK_MUTEX_RELPATH
    mutex.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30.0
    with open(mutex, "a+b") as handle:
        while not _try_os_lock(handle):
            if time.monotonic() >= deadline:
                raise BaselineError("baseline lock mutex is held for more than 30 seconds")
            time.sleep(0.05)
        try:
            yield
        finally:
            _release_os_lock(handle)


def mutate_lock(root: Path, mutator, *, allow_v1: bool = False):
    """Apply a mutation to a fresh lock snapshot under the lock mutex."""
    with lock_mutex(root):
        lock = load_lock(root)
        if lock.get("schema") != 2 and not allow_v1:
            raise BaselineError("v1 lock is read-only; run migrate first")
        result = mutator(lock)
        if result is not NO_CHANGE:
            save_lock(root, lock)
        return result


NO_CHANGE = object()
def forward_sub(text: str, subs: dict) -> str:
    for placeholder, value in subs.items():
        if value is not None:
            text = text.replace(placeholder, str(value))
    return text


def reverse_sub(text: str, subs: dict) -> str:
    for placeholder, value in sorted(subs.items(), key=lambda kv: -len(str(kv[1] or ""))):
        value = str(value or "")
        if not value:
            continue
        if re.search(r"[\\/:]", value):
            text = text.replace(value, placeholder)
        else:
            text = re.sub(rf"\b{re.escape(value)}\b", placeholder, text)
    return text


def _open_source(lock: dict, root: Path, baseline_dir: str | None) -> BaselineSource:
    repo = str(lock.get("baseline_repo", ""))
    ref = str(lock.get("baseline_ref", "main"))
    if not repo:
        raise BaselineError("lock has no baseline_repo")
    if baseline_dir:
        path = Path(baseline_dir).resolve()
        if not _git_repo(path):
            raise BaselineError(f"{path} is not a git baseline repository")
        _assert_origin(path, repo)
        pinned = _git_sha(path, "HEAD")
    else:
        cache = (root / CACHE_CLONE).resolve()
        if cache.exists():
            if not _git_repo(cache):
                raise BaselineError(f"baseline cache at {cache} is not a git repository")
            try:
                _assert_origin(cache, repo)
            except BaselineError:
                if _has_linked_worktree(cache):
                    raise BaselineError(
                        f"baseline cache origin mismatch at {cache} while linked worktrees exist"
                    )
                path = _replace_cache(cache, repo)
            else:
                path = cache
        else:
            path = cache
            _clone_no_checkout(repo, path)
        pinned = _resolve_default(path, repo, ref)
    return BaselineSource(path, pinned, _BatchReader(path, pinned))


def ensure_baseline(lock: dict, root: Path, baseline_dir: str | None) -> BaselineSource:
    """Resolve one pinned commit and open one object-store batch reader."""
    return _open_source(lock, root, baseline_dir)


def baseline_commit(baseline: BaselineSource | Path) -> str | None:
    if isinstance(baseline, BaselineSource):
        return baseline.sha
    try:
        return _git_sha(Path(baseline), "HEAD")
    except BaselineError:
        return None


def _read_upstream_bytes(baseline: BaselineSource | Path, relpath: str) -> bytes | None:
    if isinstance(baseline, BaselineSource):
        return baseline.read(relpath)
    path = Path(baseline)
    commit = baseline_commit(path)
    if not commit:
        return None
    batch = _BatchReader(path, commit)
    try:
        return batch.read(relpath)
    finally:
        batch.close()


def upstream_text(baseline: BaselineSource | Path, relpath: str, subs: dict) -> str | None:
    data = _read_upstream_bytes(baseline, relpath)
    if data is None:
        return None
    text = _lf(data).decode("utf-8", errors="replace")
    return forward_sub(text, subs)


def local_text(root: Path, relpath: str) -> str | None:
    path = root / relpath
    try:
        return _lf(path.read_bytes()).decode("utf-8", errors="replace")
    except OSError:
        return None


ADAPTATION_JSON_RELPATH = ".claude/skills/project_subsystems/adaptation.json"


def check_adaptation_contract(root: Path) -> None:
    """Design §8: `classify`, `compose` and `publish` exit 1 -- naming the file, or the
    file and key -- when the consumer lock exists and the `project_subsystems` adaptation
    contract is missing or malformed:
      - `adaptation.json` is absent, unparseable, or not a JSON object;
      - a known key (`adaptation.DEFAULTS`/`_TYPES`) is wrong-typed; unknown keys are
        ignored, matching `adaptation.load()`;
      - `SKILL.md` is absent, or its `subsystems:` YAML block is absent or unparseable.

    Advisory hooks keep `adaptation.load()`'s lenient (default-plus-warning) behavior --
    this is the harder contract the three operations enforce directly, without calling
    `load()`, because a hook must never block and these three must. Called before those
    operations read or write anything else, so a refusal leaves no lock write, journal or
    output. A consumer with no lock yet is not checked here -- `load_lock` refuses that on
    its own path, with its own message, before any of the three operations run.
    """
    if not (root / LOCK_RELPATH).is_file():
        return

    adaptation_text = local_text(root, ADAPTATION_JSON_RELPATH)
    if adaptation_text is None:
        raise BaselineError(f"adaptation contract: {ADAPTATION_JSON_RELPATH} is missing")
    try:
        raw = json.loads(adaptation_text)
    except json.JSONDecodeError as exc:
        raise BaselineError(
            f"adaptation contract: {ADAPTATION_JSON_RELPATH} is unparseable: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise BaselineError(f"adaptation contract: {ADAPTATION_JSON_RELPATH} is not a JSON object")
    for key, expected in adaptation._TYPES.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, expected):
            raise BaselineError(
                f"adaptation contract: {ADAPTATION_JSON_RELPATH} key '{key}' is wrong-typed "
                f"(want {expected.__name__})"
            )

    skill_relpath = baseline_identity.DEFAULT_SUBSYSTEMS_PATH
    skill_text = local_text(root, skill_relpath)
    if skill_text is None:
        raise BaselineError(f"adaptation contract: {skill_relpath} is missing")
    if not baseline_identity.parse_subsystems_yaml(skill_text):
        raise BaselineError(
            f"adaptation contract: {skill_relpath} subsystems YAML block is absent or unparseable"
        )


CANDIDATE_EXCLUDE_PREFIXES = (
    ".claude/.cache/", ".claude/logs/", ".claude/worktrees/",
    ".claude/sessions/", ".claude/plans/", ".claude/scratch/",
)
CANDIDATE_EXCLUDE_NAMES = (
    "baseline.lock.json", "settings.local.json", "tool_resource_classes.txt",
    "self_evaluate_archive*", "worklog-pending*", "worklog-tackle-history*",
    "*.pyc", "*.bak", ".gitkeep",
)


def classify(root: Path, baseline: BaselineSource | Path, lock: dict,
             relpath: str, entry: dict) -> str:
    if entry.get("status") in ("forked", "watch", "local"):
        return entry["status"]
    up = upstream_text(baseline, relpath, lock["substitutions"])
    lo = local_text(root, relpath)
    if up is None:
        return "removed-upstream"
    if lo is None:
        return "missing-local"
    lock_hash = entry.get("hash")
    up_h, lo_h = sha(up.encode()), sha(lo.encode())
    if lo_h == up_h:
        return "in-sync" if lock_hash == up_h else "in-sync-lock-stale"
    if lo_h == lock_hash:
        return "upstream-updated"
    if up_h == lock_hash:
        return "local-modified"
    return "diverged"


PLACEHOLDER_OK = {
    ".claude/commands/sync_baseline.md",
    ".claude/tools/baseline_sync.py",
    ".claude/tools/extract_subagent_tools.py",
    ".claude/worklog-titles.md",
}


def residual_placeholders(root: Path, lock: dict) -> list[tuple[str, list[str]]]:
    keys = list(lock.get("substitutions", {}))
    hits = []
    for relpath, entry in lock["files"].items():
        if entry.get("status") not in ("tracked", "watch") or relpath in PLACEHOLDER_OK:
            continue
        text = local_text(root, relpath)
        if text is None:
            continue
        found = sorted(key for key in keys if key in text)
        if found:
            hits.append((relpath, found))
    return sorted(hits)


def cmd_check(root: Path, lock: dict, baseline: BaselineSource, as_json: bool,
              layers: list[str]) -> None:
    results = {}
    outside = 0
    for relpath, entry in sorted(lock["files"].items()):
        if not in_profile(entry, layers):
            outside += 1
            continue
        results[relpath] = classify(root, baseline, lock, relpath, entry)
    residual = residual_placeholders(root, lock)
    if as_json:
        print(json.dumps({
            "baseline_commit": baseline_commit(baseline),
            "results": results,
            "residual_placeholders": dict(residual),
            "profile": {"layers": layers, "files_outside": outside},
        }, indent=2, ensure_ascii=False))
        return
    buckets: dict[str, list[str]] = {}
    for relpath, state in results.items():
        buckets.setdefault(state, []).append(relpath)
    quiet = {"in-sync", "watch", "forked", "local"}
    for state in sorted(buckets, key=lambda item: (item in quiet, item)):
        files = buckets[state]
        if state in quiet:
            print(f"{state}: {len(files)} file(s)")
        else:
            print(f"{state}:")
            for relpath in files:
                print(f"  {relpath}")
    if residual:
        print("unsubstituted-placeholder (install did not expand these tokens):")
        for relpath, keys in residual:
            print(f"  {relpath}  [{', '.join(keys)}]")
    actionable = [state for state in buckets if state not in quiet]
    verdict = []
    if actionable:
        verdict.append(f"actionable states: {', '.join(sorted(actionable))}")
    if residual:
        verdict.append(f"{len(residual)} file(s) with unsubstituted placeholders — "
                       "re-run bootstrap substitution or fix per file")
    print("\nclean" if not verdict else "\n" + "\n".join(verdict))
    if set(layers) != set(FULL_LAYERS):
        print(f"profile: {','.join(layers)} — {outside} file(s) outside profile not shown")


def _missing_error(relpath: str) -> BaselineError:
    return BaselineError(f"missing upstream: {relpath}")


def cmd_diff(root: Path, lock: dict, baseline: BaselineSource, relpath: str,
             layers: list[str]) -> None:
    entry = lock["files"].get(relpath)
    if entry is not None and not in_profile(entry, layers, legacy_v1=lock.get("schema") != 2):
        print(f"skip (outside profile): {relpath}")
        return
    up = upstream_text(baseline, relpath, lock["substitutions"])
    if up is None:
        raise _missing_error(relpath)
    lo = local_text(root, relpath) or ""
    sys.stdout.writelines(difflib.unified_diff(
        up.splitlines(keepends=True), lo.splitlines(keepends=True),
        fromfile=f"baseline/{relpath}", tofile=f"local/{relpath}"
    ))


def _pull_targets(root: Path, lock: dict, baseline: BaselineSource,
                  relpaths: list[str], layers: list[str]) -> list[str]:
    if relpaths:
        return relpaths
    targets = []
    for relpath, entry in sorted(lock["files"].items()):
        if not in_profile(entry, layers):
            continue
        state = classify(root, baseline, lock, relpath, entry)
        if state in ("upstream-updated", "in-sync-lock-stale", "removed-upstream"):
            targets.append(relpath)
    return targets


def cmd_pull(root: Path, lock: dict, baseline: BaselineSource,
             relpaths: list[str], layers: list[str]) -> None:
    targets = _pull_targets(root, lock, baseline, relpaths, layers)
    for relpath in targets:
        up = upstream_text(baseline, relpath, lock["substitutions"])
        if up is None:
            raise _missing_error(relpath)
        dest = root / relpath
        _write_lf(dest, up)
        lock["files"][relpath]["hash"] = sha(up.encode())
        print(f"pulled: {relpath}")
    lock["synced_commit"] = baseline_commit(baseline)
    save_lock(root, lock)


def cmd_update_lock(root: Path, lock: dict, baseline: BaselineSource,
                    relpaths: list[str], layers: list[str]) -> None:
    if not relpaths:
        for relpath, entry in sorted(lock["files"].items()):
            if not in_profile(entry, layers) or entry.get("status") != "tracked":
                continue
            if upstream_text(baseline, relpath, lock["substitutions"]) is None:
                raise _missing_error(relpath)
    targets = relpaths or [
        relpath for relpath, entry in lock["files"].items()
        if in_profile(entry, layers)
        and classify(root, baseline, lock, relpath, entry) == "in-sync-lock-stale"
    ]
    for relpath in targets:
        up = upstream_text(baseline, relpath, lock["substitutions"])
        if up is None:
            raise _missing_error(relpath)
        lock["files"][relpath]["hash"] = sha(up.encode())
        print(f"lock updated: {relpath}")
    lock["synced_commit"] = baseline_commit(baseline)
    lock["profile"] = ",".join(layers)
    save_lock(root, lock)


def cmd_set_status(root: Path, lock: dict, relpaths: list[str], status: str) -> None:
    for relpath in relpaths:
        entry = lock["files"].setdefault(relpath, {})
        entry["status"] = status
        if status in ("forked", "local"):
            entry.pop("hash", None)
        print(f"{relpath}: status={status}")
    save_lock(root, lock)


def cmd_paths(lock: dict, status_filter: str | None) -> None:
    for relpath, entry in sorted(lock["files"].items()):
        status = entry.get("status", "tracked")
        if status_filter:
            if status != status_filter:
                continue
        elif status not in ("tracked", "watch"):
            continue
        print(relpath)


def _manifest_layer_map(root: Path, baseline_dir: str | None) -> dict[str, str]:
    for candidate in (Path(baseline_dir).resolve() if baseline_dir else None,
                      root / CACHE_CLONE):
        if candidate is None or not (candidate / MANIFEST_NAME).is_file():
            continue
        try:
            manifest = json.loads(_lf((candidate / MANIFEST_NAME).read_bytes()).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        return {entry["path"]: entry["layer"] for entry in manifest["files"]}
    return {}


def cmd_candidates(root: Path, lock: dict, layers: list[str], baseline_dir: str | None) -> None:
    import fnmatch
    result = _git(root, ["ls-files", "--", ".claude"], check=False)
    if result.returncode != 0:
        sys.exit("error: candidates requires the project to be a git repository "
                 "(it scans committed .claude/ files) — run git init + commit first")
    manifest_layers = _manifest_layer_map(root, baseline_dir)
    known = set(lock["files"])
    found = False
    outside = 0
    for relpath in sorted(_lf(result.stdout).decode(errors="replace").splitlines()):
        if relpath in known:
            continue
        if any(relpath.startswith(prefix) for prefix in CANDIDATE_EXCLUDE_PREFIXES):
            continue
        name = relpath.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(name, pattern) for pattern in CANDIDATE_EXCLUDE_NAMES):
            continue
        layer = manifest_layers.get(relpath)
        if layer is not None and layer not in layers:
            outside += 1
            continue
        print(relpath)
        found = True
    if not found and outside == 0:
        print("(no candidates — every committed .claude/ artifact is known to the lock)")
    if set(layers) != set(FULL_LAYERS):
        print(f"profile: {','.join(layers)} — {outside} candidate(s) outside profile not shown")


def cmd_init(root: Path, baseline_dir: str, repo: str, ref: str,
             subs: dict[str, str], profile: str,
             source: BaselineSource | Path | None = None) -> None:
    baseline = Path(baseline_dir).resolve()
    manifest_path = baseline / MANIFEST_NAME
    if not manifest_path.exists():
        sys.exit(f"error: {manifest_path} not found — run tools/gen_manifest.py in the baseline first")
    try:
        manifest = json.loads(_lf(manifest_path.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.exit(f"error: invalid baseline manifest: {exc}")
    files = {}
    upstream = source if source is not None else baseline
    for entry in manifest["files"]:
        relpath = entry["path"]
        lo = local_text(root, relpath)
        if lo is None:
            continue
        if entry.get("sync") == "seed":
            files[relpath] = {"status": "watch", "layer": entry["layer"]}
            continue
        up = upstream_text(upstream, relpath, subs)
        if up is not None and sha(up.encode()) == sha(lo.encode()):
            files[relpath] = {
                "status": "tracked", "hash": sha(up.encode()), "layer": entry["layer"]
            }
        else:
            files[relpath] = {"status": "watch", "layer": entry["layer"]}
    lock = {
        "baseline_repo": repo,
        "baseline_ref": ref,
        "synced_commit": baseline_commit(upstream),
        "profile": profile,
        "substitutions": subs,
        "files": files,
    }
    save_lock(root, lock)
    tracked = sum(1 for entry in files.values() if entry["status"] == "tracked")
    watch = sum(1 for entry in files.values() if entry["status"] == "watch")
    print(f"lock written: {tracked} tracked, {watch} watch, "
          f"{len(manifest['files']) - len(files)} not adopted")



def _validate_relpath(relpath: str) -> str:
    if not isinstance(relpath, str) or not relpath.startswith(".claude/"):
        raise UsageError(f"invalid relpath: {relpath!r}")
    if "\\" in relpath or re.match(r"^[A-Za-z]:", relpath):
        raise UsageError(f"invalid relpath: {relpath!r}")
    parts = relpath.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UsageError(f"invalid relpath: {relpath!r}")
    normalized = "/".join(parts)
    if normalized != relpath:
        raise UsageError(f"invalid relpath: {relpath!r}")
    return relpath


def _validate_relpaths(relpaths: list[str]) -> None:
    for relpath in relpaths:
        _validate_relpath(relpath)


def resolve_layers_v2(lock: dict, layers_arg: str | None) -> list[str]:
    if layers_arg is not None:
        value = layers_arg
    else:
        profile = lock.get("profile")
        profile_parts = [part.strip() for part in str(profile).split(",")] if profile else []
        value = ",".join(profile_parts) if profile_parts and all(part in FULL_LAYERS for part in profile_parts) else ",".join(FULL_LAYERS)
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(part not in FULL_LAYERS for part in parts if part):
        bad = [part for part in parts if part not in FULL_LAYERS]
        raise UsageError("invalid layer value(s): " + ", ".join(bad))
    result = []
    for part in parts:
        if part and part not in result:
            result.append(part)
    if not result:
        raise UsageError("--layers resolved to an empty set")
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cat_file_many(repo: Path, specs: list[str]) -> list[bytes | None] | None:
    """Blob bytes for each object spec from one `git cat-file --batch` run: None for a spec that is
    missing or not a blob, and None overall when git cannot read `repo` (not a repository)."""
    if not specs:
        return []
    try:
        result = subprocess.run(
            ["git", "-C", _repo_arg(repo), "cat-file", "--batch"],
            input="".join(spec + "\n" for spec in specs).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BaselineError(f"git cat-file --batch failed: {exc}") from exc
    if result.returncode != 0:
        return None
    out, pos, blobs = result.stdout, 0, []
    for _spec in specs:
        end = out.find(b"\n", pos)
        if end < 0:
            raise BaselineError("git cat-file --batch returned fewer results than queries")
        parsed = _batch_header(out[pos:end + 1])
        pos = end + 1
        if parsed is None:
            blobs.append(None)
            continue
        kind, size = parsed
        if out[pos + size:pos + size + 1] != b"\n":
            raise BaselineError("git cat-file returned a truncated object")
        blobs.append(out[pos:pos + size] if kind == b"blob" else None)
        pos += size + 1
    return blobs


def _git_contents(root: Path, relpaths: list[str]) -> dict[str, bytes]:
    """LF bytes of each path the index holds, else HEAD, from two `git cat-file --batch` runs in all.
    One `git show` per path, two for an uncommitted one, cost 2,300 spawns and 35 s on a 575-file
    bootstrap under Windows process creation."""
    found: dict[str, bytes] = {}
    pending = list(dict.fromkeys(relpaths))
    for prefix in (":", "HEAD:"):
        if not pending:
            break
        blobs = _cat_file_many(root, [prefix + relpath for relpath in pending])
        if blobs is None:
            break
        found.update((relpath, _lf(data)) for relpath, data in zip(pending, blobs) if data is not None)
        pending = [relpath for relpath in pending if relpath not in found]
    return found


def _content_bytes_many(root: Path, relpaths: list[str]) -> dict[str, bytes | None]:
    """Read each index blob, then HEAD, then an uncommitted bootstrap copy."""
    contents: dict[str, bytes | None] = dict(_git_contents(root, relpaths))
    for relpath in relpaths:
        if relpath not in contents:
            try:
                contents[relpath] = _lf((root / relpath).read_bytes())
            except OSError:
                contents[relpath] = None
    return contents


def _content_bytes(root: Path, relpath: str) -> bytes | None:
    """Read the index blob, then HEAD, then an uncommitted bootstrap copy."""
    return _content_bytes_many(root, [relpath])[relpath]


def _content_shas(root: Path, relpaths: list[str]) -> dict[str, str | None]:
    return {relpath: (sha(data) if data is not None else None)
            for relpath, data in _content_bytes_many(root, relpaths).items()}


def _content_sha(root: Path, relpath: str) -> str | None:
    data = _content_bytes(root, relpath)
    return sha(data) if data is not None else None


def _git_clean_path(root: Path, relpath: str) -> bool:
    result = _git(root, ["status", "--porcelain", "--", relpath], check=False)
    return result.returncode == 0 and not result.stdout.strip()


def _source_root_bytes(source: BaselineSource | Path, relpath: str) -> bytes | None:
    if isinstance(source, BaselineSource):
        if relpath.startswith("template/"):
            spec = relpath[len("template/"):]
            return _read_upstream_bytes(source, spec)
        result = _git(source.path, ["show", f"{source.sha}:{relpath}"], check=False)
        return _lf(result.stdout) if result.returncode == 0 else None
    path = Path(source)
    commit = baseline_commit(path)
    if not commit:
        return None
    result = _git(path, ["show", f"{commit}:{relpath}"], check=False)
    return _lf(result.stdout) if result.returncode == 0 else None


def _manifest_layers_v2(source: BaselineSource | Path, root: Path) -> dict[str, str]:
    raw = _source_root_bytes(source, MANIFEST_NAME)
    if raw is None:
        return {}
    try:
        value = json.loads(_lf(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {
        item["path"]: item.get("layer")
        for item in value.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def _manifest_paths_v2(source: BaselineSource | Path, root: Path, layers: list[str]) -> set[str]:
    mapping = _manifest_layers_v2(source, root)
    return {relpath for relpath, layer in mapping.items() if not layer or layer in layers}


def _upstream_sha(source: BaselineSource | Path, relpath: str, substitutions: dict) -> str | None:
    text = upstream_text(source, relpath, substitutions)
    return sha(text.encode("utf-8")) if text is not None else None


def _decision(root: Path, relpath: str, verdict: str, *, sha_value: str | None = None,
              borderline: bool = False, confirmed_at: str | None = None) -> dict | None:
    if sha_value is None:
        sha_value = _content_sha(root, relpath)
    if sha_value is None:
        return None
    return {
        "sha": sha_value,
        "verdict": verdict,
        "at": _utc_now(),
        "borderline": bool(borderline),
        "confirmed_at": confirmed_at,
    }


def _entry_status(entry: dict) -> str:
    return str(entry.get("status", "tracked"))


def _v2_state(root: Path, source: BaselineSource | Path, lock: dict,
              relpath: str, entry: dict) -> str:
    status = _entry_status(entry)
    if status in ("local", "watch"):
        return status
    if status == "forked":
        if lock.get("schema") != 2:
            return "forked"
        upstream = _upstream_sha(source, relpath, lock.get("substitutions", {}))
        if upstream is None:
            return "removed-upstream"
        base = entry.get("base")
        if not base:
            return "forked-base-unknown"
        return "forked" if base == source.sha else "forked-upstream-moved"
    if status == "composed":
        local = local_text(root, relpath)
        if entry.get("hash") is None or local is None or sha(local.encode("utf-8")) != entry.get("hash"):
            return "composed-drift"
        return "in-sync"
    upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
    local = local_text(root, relpath)
    if upstream is None:
        return "removed-upstream"
    if local is None:
        return "missing-local"
    upstream_hash = sha(upstream.encode("utf-8"))
    local_hash = sha(local.encode("utf-8"))
    lock_hash = entry.get("hash")
    if local_hash == upstream_hash:
        return "in-sync" if lock_hash == upstream_hash else "in-sync-lock-stale"
    if local_hash == lock_hash:
        return "upstream-updated"
    if upstream_hash == lock_hash:
        return "local-modified"
    return "diverged"


_UNREAD = object()


def _triage_needed(root: Path, source: BaselineSource | Path, lock: dict,
                   relpath: str, entry: dict, current_sha: object = _UNREAD) -> bool:
    """`current_sha` is the row's content sha when the caller read many rows in one batch."""
    status = _entry_status(entry)
    if status == "forked":
        return entry.get("judged") is None
    if status != "tracked":
        return False
    current = _content_sha(root, relpath) if current_sha is _UNREAD else current_sha
    return current is not None and current != entry.get("hash") and current != (entry.get("judged") or {}).get("sha")


def _diff_text(root: Path, source: BaselineSource | Path, lock: dict, relpath: str) -> str:
    upstream = upstream_text(source, relpath, lock.get("substitutions", {})) or ""
    local = local_text(root, relpath) or ""
    lines = list(difflib.unified_diff(
        upstream.splitlines(keepends=True), local.splitlines(keepends=True),
        fromfile=f"baseline/{relpath}", tofile=f"local/{relpath}",
    ))
    if len(lines) <= 120:
        return "".join(lines)
    remaining = len(lines) - 120
    lines = lines[:120] + [f"... truncated {remaining} more lines\n"]
    return "".join(lines)


def _check_results(root: Path, lock: dict, source: BaselineSource,
                   layers: list[str]) -> tuple[dict[str, str], int]:
    results: dict[str, str] = {}
    outside = 0
    legacy_v1 = lock.get("schema") != 2
    for relpath, entry in sorted(lock.get("files", {}).items()):
        if not in_profile(entry, layers, legacy_v1=legacy_v1):
            outside += 1
            continue
        results[relpath] = _v2_state(root, source, lock, relpath, entry)
    for relpath in sorted(_manifest_paths_v2(source, root, layers) - set(lock.get("files", {}))):
        results[relpath] = "new-upstream"
    return results, outside


def _strict_findings(root: Path, source: BaselineSource, lock: dict,
                     results: dict[str, str]) -> list[tuple[str, str]]:
    files = lock.get("files", {})
    current = _content_shas(root, [relpath for relpath in results
                                   if relpath in files and _entry_status(files[relpath]) == "tracked"])
    findings = []
    for relpath, state in sorted(results.items()):
        entry = files.get(relpath)
        if state in {
            "new-upstream", "forked-upstream-moved", "forked-base-unknown",
            "composed-drift", "watch", "removed-upstream", "missing-local",
            "local-modified", "diverged",
        }:
            findings.append((relpath, state))
        elif entry is not None and _triage_needed(root, source, lock, relpath, entry,
                                                 current.get(relpath, _UNREAD)):
            findings.append((relpath, "needs-judgment"))
    return findings


def v2_check(root: Path, lock: dict, source: BaselineSource, as_json: bool,
              layers: list[str], strict: bool) -> int:
    results, outside = _check_results(root, lock, source, layers)
    residual = residual_placeholders(root, lock)
    findings =_strict_findings(root, source, lock, results) if strict or not as_json else []
    if as_json:
        print(json.dumps({
            "baseline_commit": baseline_commit(source),
            "results": results,
            "residual_placeholders": dict(residual),
            "profile": {"layers": layers, "files_outside": outside},
        }, indent=2, ensure_ascii=False))
    else:
        buckets: dict[str, list[str]] = {}
        for relpath, state in results.items():
            buckets.setdefault(state, []).append(relpath)
        quiet = {"in-sync", "watch", "forked", "local"}
        for state in sorted(buckets, key=lambda value: (value in quiet, value)):
            paths = buckets[state]
            if state in quiet:
                print(f"{state}: {len(paths)} file(s)")
            else:
                print(state + ":")
                for relpath in paths:
                    print("  " + relpath)
        if residual:
            print("unsubstituted-placeholder (install did not expand these tokens):")
            for relpath, keys in residual:
                print(f"  {relpath}  [{', '.join(keys)}]")
        print("\nclean" if not residual and not findings else "\nfindings")
        if set(layers) != set(FULL_LAYERS):
            print(f"profile: {','.join(layers)} — {outside} file(s) outside profile not shown")
    return 1 if strict and findings else 0

def _classify_decision_verdict(status: str) -> str:
    return {"tracked": "push", "local": "keep-local", "forked": "fork", "composed": "keep-local"}[status]


def _whole_file_as_added_diff(relpath: str, text: str) -> str:
    """A synthetic unified diff whose every line of `text` is an ADDED line at `relpath`, so
    `baseline_identity.scan_changed` -- built for a real diff's added lines only -- can scan a
    whole file's CURRENT content (not just what changed against some other revision)."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    body = ["+++ b/" + relpath, "@@ -0,0 +1,%d @@" % max(len(lines), 1)]
    body.extend("+" + ln for ln in lines)
    return "\n".join(body) + "\n"


def _classify_identity_hit(root: Path, relpath: str, profile: dict, data: bytes | None = None):
    """The first identity-scan hit in `relpath`'s current content (index, else HEAD, else the
    working copy -- `_content_bytes`), scanned as if every line were newly added.

    §7 F8: a hand-rolled substring check against lock substitutions/abbreviations missed every
    other profile kind (concatenation, home path, topology token, content noun) and any hit in
    a file that already existed with different content. `baseline_identity.scan_changed` is the
    one profile-aware scanner (Design §3); running it over the whole file as added lines is how
    a `classify --status tracked` proof plants a hit and gets a real refusal."""
    if data is None:
        data = _content_bytes(root, relpath)
    if data is None:
        return None
    diff_text = _whole_file_as_added_diff(relpath, data.decode("utf-8", errors="replace"))
    hits = baseline_identity.scan_changed(diff_text, profile)
    return hits[0] if hits else None


def v2_classify(root: Path, relpaths: list[str], status: str, source_relpath: str | None,
                 inputs: list[str] | None, force: bool, baseline_dir: str | None = None) -> int:
    check_adaptation_contract(root)
    if status not in V2_STATUSES:
        raise UsageError("--status must be tracked, local, forked or composed")
    _validate_relpaths(relpaths)
    if source_relpath:
        _validate_relpath(source_relpath)
    if status == "composed" and not inputs:
        raise UsageError("--inputs is required with --status composed")
    if status != "composed" and inputs:
        raise UsageError("--inputs is only valid with --status composed")
    if inputs:
        _validate_relpaths(inputs)

    def apply(lock: dict):
        files = lock.setdefault("files", {})
        source_entry = files.get(source_relpath) if source_relpath else None
        if source_relpath and source_entry is None:
            raise BaselineError(f"--from is not a lock row: {source_relpath}")
        profile = None  # built on the first tracked row: it reads the pinned template tree
        committed = _git_contents(root, relpaths)
        for relpath in relpaths:
            if relpath not in committed:
                raise BaselineError(f"classify requires an index or HEAD path: {relpath}")
            if status == "tracked":
                if profile is None:
                    profile = baseline_identity.build_profile_for_repo(root, baseline_dir=baseline_dir)
                hit = _classify_identity_hit(root, relpath, profile, committed[relpath])
                if hit is not None:
                    raise BaselineError(
                        f"identity scan hit in {relpath}:{hit.line} "
                        f"({hit.token_kind}): {hit.excerpt}"
                    )
            existing = files.get(relpath)
            if existing is not None and _entry_status(existing) != status and not force:
                raise BaselineError(f"row has status {_entry_status(existing)}; use --force: {relpath}")
        changed = []
        for relpath in relpaths:
            existing = files.get(relpath)
            current_sha = sha(committed[relpath])
            layer = (source_entry or {}).get("layer") if source_entry else (existing or {}).get("layer")
            desired_inputs = list(inputs or []) if status == "composed" else None
            same = (
                existing is not None
                and _entry_status(existing) == status
                and (existing.get("judged") or {}).get("sha") == current_sha
                and existing.get("layer") == layer
                and existing.get("inputs") == desired_inputs
                and existing.get("from") == source_relpath
            )
            if same:
                continue
            entry = dict(existing or {})
            entry["status"] = status
            entry["layer"] = layer
            if source_relpath:
                entry["from"] = source_relpath
            else:
                entry.pop("from", None)
            if status == "composed":
                entry["inputs"] = desired_inputs
                entry["hash"] = None
            else:
                entry.pop("inputs", None)
            if status in ("local", "forked"):
                entry.pop("hash", None)
            if status == "forked":
                entry["base"] = None
            else:
                entry.pop("base", None)
            entry["judged"] = _decision(root, relpath, _classify_decision_verdict(status), sha_value=current_sha)
            files[relpath] = entry
            changed.append(relpath)
        return changed or NO_CHANGE

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"classified: {relpath}")
    return 0


def v2_judge(root: Path, relpaths: list[str], verdict: str, borderline: bool,
              confirm: bool, force: bool) -> int:
    if verdict not in VERDICTS:
        raise UsageError("--verdict must be push, keep-local or fork")
    if borderline and confirm:
        raise UsageError("--borderline and --confirm are mutually exclusive")
    _validate_relpaths(relpaths)

    def apply(lock: dict):
        files = lock.get("files", {})
        decisions = {}
        current = _content_shas(root, relpaths)
        for relpath in relpaths:
            if relpath not in files:
                raise BaselineError(f"not a row: {relpath}")
            current_sha = current[relpath]
            if current_sha is None:
                raise BaselineError(f"could not read content for {relpath}")
            decisions[relpath] = current_sha
            old = files[relpath].get("judged") or {}
            if old.get("sha") == current_sha and old.get("verdict") not in (None, verdict) and not force:
                raise BaselineError(f"different verdict for the same content; use --force: {relpath}")
        changed = []
        for relpath, current_sha in decisions.items():
            old = files[relpath].get("judged") or {}
            confirmed_at = _utc_now() if confirm else None
            same = (
                old.get("sha") == current_sha
                and old.get("verdict") == verdict
                and (not confirm or old.get("confirmed_at") is not None)
            )
            if same:
                continue
            files[relpath]["judged"] = _decision(
                root, relpath, verdict, sha_value=current_sha,
                borderline=borderline, confirmed_at=confirmed_at,
            )
            changed.append(relpath)
        return changed or NO_CHANGE

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"judged: {relpath}")
    return 0


def v2_triage(root: Path, lock: dict, source: BaselineSource, batch: int, as_json: bool) -> int:
    files = lock.get("files", {})
    current = _content_shas(root, [relpath for relpath, entry in files.items() if _entry_status(entry) == "tracked"])
    selected = []
    for relpath, entry in sorted(files.items()):
        if _triage_needed(root, source, lock, relpath, entry, current.get(relpath, _UNREAD)):
            selected.append((relpath, entry))
        if len(selected) >= batch:
            break
    rows = []
    for relpath, entry in selected:
        judged = entry.get("judged") or {}
        rows.append({
            "relpath": relpath,
            "status": _entry_status(entry),
            "layer": entry.get("layer"),
            "sha": current[relpath] if relpath in current else _content_sha(root, relpath),
            "prior_verdict": judged.get("verdict"),
            "diff": _diff_text(root, source, lock, relpath),
        })
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            print(f"{row['relpath']} status={row['status']} layer={row['layer']} sha={row['sha']}")
            print(row["diff"], end="" if row["diff"].endswith("\n") else "\n")
        if not rows:
            print("no change")
    return 0


def v2_forget(root: Path, relpaths: list[str], force: bool) -> int:
    _validate_relpaths(relpaths)

    def apply(lock: dict):
        files = lock.get("files", {})
        known = [relpath for relpath in relpaths if relpath in files]
        unknown = [relpath for relpath in relpaths if relpath not in files]
        for relpath in known:
            if (root / relpath).exists() and not force:
                raise BaselineError(f"row file exists; use --force: {relpath}")
        for relpath in known:
            del files[relpath]
        return (known, unknown) if known else NO_CHANGE

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change (not a row)")
        for relpath in relpaths:
            print(f"not a row: {relpath}")
        return 0
    known, unknown = result
    for relpath in known:
        print(f"forgot: {relpath}")
    for relpath in unknown:
        print(f"not a row: {relpath}")
    return 0


def _gc_rows(root: Path, lock: dict) -> tuple[list[tuple[str, str]], dict[str, int]]:
    rows = []
    totals = {"drop": 0, "keep-tracked": 0, "unreadable": 0}
    for relpath, entry in sorted(lock.get("files", {}).items()):
        if (root / relpath).exists():
            continue
        status = _entry_status(entry) if isinstance(entry, dict) else ""
        if status in ("local", "forked", "watch"):
            tag = "drop"
        elif status == "tracked":
            tag = "keep-tracked"
        else:
            tag = "unreadable"
        rows.append((relpath, tag))
        totals[tag] += 1
    return rows, totals


def v2_gc(root: Path, apply_changes: bool) -> int:
    lock = load_lock(root)
    rows, totals = _gc_rows(root, lock)
    scanned = sum(totals.values())
    for relpath, tag in rows:
        print(f"{tag}: {relpath}")
    print("totals: scanned=%d drop=%d keep-tracked=%d unreadable=%d" % (
        scanned, totals["drop"], totals["keep-tracked"], totals["unreadable"]))
    if not apply_changes:
        if not rows:
            print("no change")
        return 0
    if totals["unreadable"] or sum(totals.values()) != scanned:
        raise BaselineError("gc refuses unreadable or unreconciled rows")

    def apply(lock_value: dict):
        current_rows, current_totals = _gc_rows(root, lock_value)
        if current_totals["unreadable"] or sum(current_totals.values()) != len(current_rows):
            raise BaselineError("gc refuses unreadable or unreconciled rows")
        dropped = [relpath for relpath, tag in current_rows if tag == "drop"]
        for relpath in dropped:
            lock_value["files"].pop(relpath, None)
        return dropped or NO_CHANGE

    dropped = mutate_lock(root, apply)
    if dropped is NO_CHANGE:
        print("no change")
    else:
        for relpath in dropped:
            print(f"dropped: {relpath}")
    return 0


def v2_fork(root: Path, relpaths: list[str], source: BaselineSource) -> int:
    _validate_relpaths(relpaths)

    def apply(lock_value: dict):
        changed = []
        current = _content_shas(root, relpaths)
        for relpath in relpaths:
            entry = lock_value.get("files", {}).get(relpath)
            if entry is None:
                raise BaselineError(f"not a row: {relpath}")
            current_sha = current[relpath]
            if current_sha is None:
                raise BaselineError(f"could not read content for {relpath}")
            if (
                _entry_status(entry) == "forked"
                and entry.get("base") == source.sha
                and (entry.get("judged") or {}).get("sha") == current_sha
                and (entry.get("judged") or {}).get("verdict") == "fork"
            ):
                continue
            entry["status"] = "forked"
            entry["base"] = source.sha
            entry.pop("hash", None)
            entry["judged"] = _decision(root, relpath, "fork", sha_value=current_sha)
            changed.append(relpath)
        return changed or NO_CHANGE

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"forked: {relpath}")
    return 0


def v2_track(root: Path, relpaths: list[str], source: BaselineSource) -> int:
    _validate_relpaths(relpaths)

    def apply(lock_value: dict):
        changed = []
        for relpath in relpaths:
            entry = lock_value.get("files", {}).get(relpath)
            if entry is None:
                raise BaselineError(f"not a row: {relpath}")
            if _entry_status(entry) not in ("forked", "tracked"):
                raise BaselineError(f"track requires a forked row: {relpath}")
            kept_hash = entry.get("hash")
            if kept_hash is None:
                raise BaselineError(
                    f"track requires a pulled hash: {relpath} — first run pull --force {relpath}"
                )
            old = entry.get("judged") or {}
            if (
                _entry_status(entry) == "tracked"
                and entry.get("hash") == kept_hash
                and old.get("sha") == kept_hash
                and old.get("verdict") == "push"
            ):
                continue
            entry["status"] = "tracked"
            entry["hash"] = kept_hash
            entry.pop("base", None)
            entry["judged"] = {
                "sha": kept_hash,
                "verdict": "push",
                "at": _utc_now(),
                "borderline": False,
                "confirmed_at": None,
            }
            changed.append(relpath)
        return changed or NO_CHANGE

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"tracked: {relpath}")
    return 0


def v2_pull(root: Path, source: BaselineSource, relpaths: list[str], layers: list[str], force: bool) -> int:
    _validate_relpaths(relpaths)
    manifest_layers = _manifest_layers_v2(source, root)
    skipped = 0

    def apply(lock: dict):
        nonlocal skipped
        files = lock.setdefault("files", {})
        if relpaths:
            targets = []
            for path in relpaths:
                entry = files.get(path)
                layer = entry.get("layer") if entry is not None else manifest_layers.get(path)
                if layer and layer not in layers:
                    skipped += 1
                    continue
                targets.append(path)
        else:
            targets = []
            for path, entry in sorted(files.items()):
                if not in_profile(entry, layers):
                    continue
                state = _v2_state(root, source, lock, path, entry)
                if state in ("upstream-updated", "new-upstream"):
                    targets.append(path)
            targets.extend(sorted(
                path for path, layer in manifest_layers.items()
                if path not in files and (not layer or layer in layers)
            ))
        targets = list(dict.fromkeys(targets))
        plans = []
        for relpath in targets:
            upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
            if upstream is None:
                raise _missing_error(relpath)
            entry = files.get(relpath)
            if entry is None:
                plans.append((relpath, None, upstream))
                continue
            state = _v2_state(root, source, lock, relpath, entry)
            if state == "in-sync":
                plans.append((relpath, "noop", upstream))
                continue
            if state in ("composed-drift", "removed-upstream"):
                raise BaselineError(f"pull refuses {state}: {relpath}")
            needs_force = state in {
                "local-modified", "diverged", "forked", "forked-upstream-moved",
                "forked-base-unknown", "local", "watch",
            }
            if needs_force and not force:
                raise BaselineError(f"pull requires --force for {state}: {relpath}")
            if force and not _git_clean_path(root, relpath):
                raise BaselineError(f"forced pull refuses a dirty path: {relpath}")
            plans.append((relpath, "update", upstream))
        changed = []
        for relpath, action, upstream in plans:
            if action == "noop":
                continue
            destination = root / relpath
            _write_lf(destination, upstream)
            upstream_hash = sha(upstream.encode("utf-8"))
            if action is None:
                files[relpath] = {
                    "status": "tracked",
                    "layer": manifest_layers.get(relpath),
                    "hash": upstream_hash,
                    "judged": {
                        "sha": upstream_hash,
                        "verdict": "push",
                        "at": _utc_now(),
                        "borderline": False,
                        "confirmed_at": None,
                    },
                }
            else:
                if _entry_status(files[relpath]) == "local":
                    files[relpath].pop("hash", None)
                else:
                    files[relpath]["hash"] = upstream_hash
            changed.append(relpath)
        if not changed:
            return NO_CHANGE
        lock["synced_commit"] = source.sha
        lock["profile"] = ",".join(layers)
        return changed

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"pulled: {relpath}")
    if skipped:
        print(f"profile: {','.join(layers)} — {skipped} row(s) skipped")
    return 0


def v2_update_lock(root: Path, source: BaselineSource, relpaths: list[str], layers: list[str]) -> int:
    _validate_relpaths(relpaths)
    skipped = 0

    def apply(lock: dict):
        nonlocal skipped
        files = lock.get("files", {})
        if relpaths:
            targets = []
            for relpath in relpaths:
                entry = files.get(relpath)
                if entry is None:
                    raise BaselineError(f"not a row: {relpath}")
                if not in_profile(entry, layers):
                    skipped += 1
                    continue
                targets.append(relpath)
        else:
            for relpath, entry in sorted(files.items()):
                if _entry_status(entry) != "tracked" or not in_profile(entry, layers):
                    continue
                if upstream_text(source, relpath, lock.get("substitutions", {})) is None:
                    raise _missing_error(relpath)
            targets = [
                relpath for relpath, entry in files.items()
                if _entry_status(entry) == "tracked"
                and in_profile(entry, layers)
                and _v2_state(root, source, lock, relpath, entry) == "in-sync-lock-stale"
            ]
        changed = []
        for relpath in targets:
            upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
            if upstream is None:
                raise _missing_error(relpath)
            upstream_hash = sha(upstream.encode("utf-8"))
            if files[relpath].get("hash") == upstream_hash:
                continue
            files[relpath]["hash"] = upstream_hash
            changed.append(relpath)
        if not changed:
            return NO_CHANGE
        lock["synced_commit"] = source.sha
        lock["profile"] = ",".join(layers)
        return changed

    result = mutate_lock(root, apply)
    if result is NO_CHANGE:
        print("no change")
    else:
        for relpath in result:
            print(f"lock updated: {relpath}")
    if skipped:
        print(f"profile: {','.join(layers)} — {skipped} row(s) skipped")
    return 0


def _migrated_layer(manifest_layers: dict[str, str], relpath: str, status: str) -> str | None:
    if relpath in manifest_layers:
        return manifest_layers[relpath]
    return "project" if status == "local" else None


def _migrated_decision(root: Path, relpath: str, verdict: str,
                       current: dict[str, str | None]) -> dict | None:
    sha_value = current[relpath] if relpath in current else _content_sha(root, relpath)
    return _decision(root, relpath, verdict, sha_value=sha_value) if sha_value is not None else None


def _migrate_row(root: Path, source_row: dict, relpath: str,
                 manifest_layers: dict[str, str], current: dict[str, str | None]) -> dict:
    old_status = source_row.get("status", "tracked")
    local_exists = (root / relpath).is_file()
    if old_status == "tracked" and relpath == ".claude/reference/memory_domains.md":
        layer = _migrated_layer(manifest_layers, relpath, "composed")
        row: dict = {"layer": layer}
        row.update({"status": "composed", "hash": None, "inputs": WATCH_COMPOSED_INPUTS[relpath], "judged": None})
        return row
    if old_status == "watch" and relpath == ".claude/CLAUDE.md":
        layer = _migrated_layer(manifest_layers, relpath, "local")
        row = {"layer": layer}
        row.update({"status": "local", "judged": _migrated_decision(root, relpath, "keep-local", current) if local_exists else None})
        return row
    if old_status == "watch" and relpath in WATCH_COMPOSED_INPUTS:
        inputs = WATCH_COMPOSED_INPUTS[relpath]
        if all((root / item).is_file() for item in inputs):
            final_status = "composed"
            layer = _migrated_layer(manifest_layers, relpath, final_status)
            row = {"layer": layer}
            row.update({"status": "composed", "hash": None, "inputs": inputs, "judged": None})
        else:
            final_status = "forked"
            layer = _migrated_layer(manifest_layers, relpath, final_status)
            row = {"layer": layer}
            row.update({"status": "forked", "base": None, "judged": None})
        return row
    if old_status == "watch":
        if relpath in WATCH_LOCAL_RELPATHS or not local_exists:
            final_status = "local"
            layer = _migrated_layer(manifest_layers, relpath, final_status)
            row = {"layer": layer}
            row.update({"status": "local", "judged": _migrated_decision(root, relpath, "keep-local", current) if local_exists else None})
        else:
            final_status = "forked"
            layer = _migrated_layer(manifest_layers, relpath, final_status)
            row = {"layer": layer}
            row.update({"status": "forked", "base": None, "judged": None})
        return row
    layer = _migrated_layer(manifest_layers, relpath, old_status)
    row = {"layer": layer}
    if old_status == "local":
        row.update({"status": "local", "judged": _migrated_decision(root, relpath, "keep-local", current) if local_exists else None})
        return row
    if old_status == "forked":
        row.update({"status": "forked", "base": None, "judged": None})
        return row
    if old_status == "composed":
        row.update({"status": "composed", "hash": source_row.get("hash"), "inputs": source_row.get("inputs", []), "judged": None})
        return row
    row.update({"status": "tracked", "hash": source_row.get("hash"), "judged": None})
    return row


def v2_migrate(root: Path, source: BaselineSource, layers: list[str], abbreviations: list[str]) -> int:
    current = load_lock(root)
    if current.get("schema") == 2:
        print("no change (already v2)")
        return 0
    manifest_layers = _manifest_layers_v2(source, root)

    def apply(lock: dict):
        if lock.get("schema") == 2:
            return NO_CHANGE
        files = {}
        current = _content_shas(root, list(lock.get("files", {})))
        for relpath, source_row in lock.get("files", {}).items():
            files[relpath] = _migrate_row(root, source_row, relpath, manifest_layers, current)
        core = ".claude/CLAUDE.core.md"
        if core not in files and (_content_sha(root, core) is not None or _upstream_sha(source, core, lock.get("substitutions", {})) is not None):
            core_hash = _upstream_sha(source, core, lock.get("substitutions", {}))
            files[core] = {
                "status": "tracked",
                "layer": manifest_layers.get(core),
                "hash": core_hash,
                "judged": None,
            }
        lock["schema"] = 2
        lock["profile"] = ",".join(layers)
        lock["identity"] = {"abbreviations": list(abbreviations)}
        lock["files"] = files
        return files

    result = mutate_lock(root, apply, allow_v1=True)
    if not abbreviations:
        print("warning: identity.abbreviations defaulted to []")
    if result is NO_CHANGE:
        print("no change (already v2)")
    else:
        print(f"migrated: {len(result)} row(s)")
    return 0


def v2_paths(lock: dict, status_filter: str | None, verdict_filter: str | None,
              judged_since: str | None, as_json: bool) -> int:
    if status_filter is not None and status_filter not in V2_STATUSES:
        raise UsageError("unknown status")
    if verdict_filter is not None and verdict_filter not in VERDICTS:
        raise UsageError("unknown verdict")
    since = None
    if judged_since is not None:
        try:
            since = datetime.fromisoformat(judged_since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise UsageError("unparseable --judged-since") from exc
        if since.tzinfo is None:
            raise UsageError("--judged-since must include a timezone")
    paths = []
    for relpath, entry in sorted(lock.get("files", {}).items()):
        if status_filter is not None and entry.get("status") != status_filter:
            continue
        judged = entry.get("judged") or {}
        if verdict_filter is not None and judged.get("verdict") != verdict_filter:
            continue
        if since is not None:
            at = judged.get("at")
            if not at:
                continue
            try:
                parsed = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed <= since:
                continue
        paths.append(relpath)
    if as_json:
        print(json.dumps(paths, ensure_ascii=False))
    else:
        for relpath in paths:
            print(relpath)
    return 0


def _composed_rows(lock: dict) -> list[tuple[str, dict]]:
    return [(relpath, entry) for relpath, entry in lock.get("files", {}).items()
            if _entry_status(entry) == "composed"]


def _compose_row_output(root: Path, relpath: str, inputs: list[str], layers: list[str]) -> bytes:
    """Dispatch by relpath shape — the same two shapes `WATCH_COMPOSED_INPUTS` enumerates."""
    if relpath.endswith("settings.json"):
        if len(inputs) != 2:
            raise BaselineError(f"compose: {relpath} needs exactly 2 inputs, got {inputs}")
        base_relpath, project_relpath = inputs
        base_text = local_text(root, base_relpath)
        project_text = local_text(root, project_relpath)
        if base_text is None:
            raise BaselineError(f"compose: missing input {base_relpath} for {relpath}")
        if project_text is None:
            raise BaselineError(f"compose: missing input {project_relpath} for {relpath}")
        try:
            base = json.loads(base_text)
            project = json.loads(project_text)
        except json.JSONDecodeError as exc:
            raise BaselineError(f"compose: {relpath} input is not valid JSON: {exc}") from exc
        hooks_dir = root / ".claude" / "hooks"
        try:
            merged = baseline_compose.compose_settings(base, project, hooks_dir, layers)
        except baseline_compose.ComposeError as exc:
            raise BaselineError(f"compose: {relpath}: {exc}") from exc
        return baseline_compose.canonical_json(merged)
    if relpath.endswith("memory_domains.md"):
        base_relpath = next((p for p in inputs if p.endswith(".base.md")), None)
        adaptation_relpath = next((p for p in inputs if p.endswith("adaptation.json")), None)
        if base_relpath is None or adaptation_relpath is None:
            raise BaselineError(f"compose: {relpath} needs a *.base.md and an adaptation.json input, got {inputs}")
        base_text = local_text(root, base_relpath)
        adaptation_text = local_text(root, adaptation_relpath)
        if base_text is None:
            raise BaselineError(f"compose: missing input {base_relpath} for {relpath}")
        if adaptation_text is None:
            raise BaselineError(f"compose: missing input {adaptation_relpath} for {relpath}")
        try:
            adaptation = json.loads(adaptation_text)
        except json.JSONDecodeError as exc:
            raise BaselineError(f"compose: {relpath} input is not valid JSON: {exc}") from exc
        if not isinstance(adaptation.get("memory_domains", []), list):
            raise BaselineError(f"compose: {adaptation_relpath} memory_domains must be a list")
        try:
            rendered = baseline_compose.render_memory_domains(base_text, adaptation.get("memory_domains", []))
        except baseline_compose.ComposeError as exc:
            raise BaselineError(f"compose: {relpath}: {exc}") from exc
        return rendered.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    raise BaselineError(f"compose: no composition rule for {relpath}")


def v2_compose(root: Path, lock: dict, layers: list[str], check: bool) -> int:
    check_adaptation_contract(root)
    rows = _composed_rows(lock)
    outputs: dict[str, bytes] = {}
    inputs_by_relpath: dict[str, list[str]] = {}
    for relpath, entry in rows:
        inputs = list(entry.get("inputs") or [])
        inputs_by_relpath[relpath] = inputs
        outputs[relpath] = _compose_row_output(root, relpath, inputs, layers)

    drift = []
    for relpath, output in outputs.items():
        local = local_text(root, relpath)
        local_bytes = local.encode("utf-8") if local is not None else None
        if local_bytes != _lf(output):
            drift.append(relpath)
    drift.sort()

    if check:
        for relpath in drift:
            print(f"drift: {relpath}")
        if drift:
            return 1
        print("no drift")
        return 0

    for relpath in drift:
        _write_lf(root / relpath, outputs[relpath])

    # A row whose file already equals its composition (e.g. `migrate` reset `hash` to null on an
    # already-correct file) still needs its `hash`/`inputs` recorded -- `drift` alone tracks only
    # files that needed WRITING, not rows whose lock metadata disagrees with the composed truth.
    rows_by_relpath = dict(rows)
    stale_rows = []
    for relpath, output in outputs.items():
        row = rows_by_relpath[relpath]
        new_hash = sha(output)
        new_inputs = inputs_by_relpath[relpath]
        if row.get("hash") != new_hash or row.get("inputs") != new_inputs:
            stale_rows.append(relpath)
    stale_rows.sort()

    if not drift and not stale_rows:
        print("no change")
        return 0

    def mutator(current: dict):
        changed = False
        for relpath in stale_rows:
            row = current.get("files", {}).get(relpath)
            if row is None:
                continue
            new_hash = sha(outputs[relpath])
            new_inputs = inputs_by_relpath[relpath]
            if row.get("hash") != new_hash or row.get("inputs") != new_inputs:
                row["hash"] = new_hash
                row["inputs"] = new_inputs
                changed = True
        return None if changed else NO_CHANGE

    if stale_rows:
        mutate_lock(root, mutator)
    for relpath in sorted(set(drift) | set(stale_rows)):
        print(f"composed: {relpath}")
    return 0


def v2_init(root: Path, baseline_dir: str, repo: str, ref: str,
            substitutions: dict[str, str], layers: list[str], source: BaselineSource,
            force: bool) -> int:
    lock_path = root / LOCK_RELPATH
    if lock_path.exists():
        existing = load_lock(root)
        if existing.get("schema") != 2:
            raise BaselineError("v1 lock is read-only; run migrate first")
        if not force:
            raise BaselineError(f"{LOCK_RELPATH} already exists; use --force")
    manifest_raw = _source_root_bytes(source, MANIFEST_NAME)
    if manifest_raw is None:
        raise BaselineError("baseline manifest is missing")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid baseline manifest: {exc}") from exc
    files = {}
    for item in manifest.get("files", []):
        relpath = item.get("path")
        if not isinstance(relpath, str) or not in_profile(item, layers):
            continue
        local = local_text(root, relpath)
        if local is None:
            continue
        upstream = upstream_text(source, relpath, substitutions)
        if item.get("sync") == "seed":
            files[relpath] = {
                "status": "local",
                "layer": item.get("layer"),
                "judged": _decision(root, relpath, "keep-local"),
            }
            # `.claude/settings.json` is `composed`, never a manifest entry of its own (§9): once
            # its seed input (`settings.project.json`) is seen, synthesize its row here if the
            # tracked input (`settings.base.json`) also landed on disk.
            if relpath.endswith("settings.project.json"):
                composed_relpath = str(Path(relpath).parent / "settings.json").replace("\\", "/")
                composed_inputs = WATCH_COMPOSED_INPUTS.get(composed_relpath, [])
                if composed_inputs and all((root / p).is_file() for p in composed_inputs):
                    files[composed_relpath] = {
                        "status": "composed",
                        "layer": item.get("layer"),
                        "hash": None,
                        "inputs": composed_inputs,
                        "judged": None,
                    }
        elif upstream is not None:
            upstream_hash = sha(upstream.encode("utf-8"))
            files[relpath] = {
                "status": "tracked",
                "layer": item.get("layer"),
                "hash": upstream_hash,
                "judged": {
                    "sha": upstream_hash,
                    "verdict": "push",
                    "at": _utc_now(),
                    "borderline": False,
                    "confirmed_at": None,
                },
            }
    lock = {
        "schema": 2,
        "profile": ",".join(layers),
        "substitutions": substitutions,
        "baseline_repo": repo,
        "baseline_ref": ref,
        "synced_commit": source.sha,
        "identity": {"abbreviations": []},
        "files": files,
    }
    with lock_mutex(root):
        if lock_path.exists():
            existing = load_lock(root)
            if existing.get("schema") != 2:
                raise BaselineError("v1 lock is read-only; run migrate first")
            if not force:
                raise BaselineError(f"{LOCK_RELPATH} already exists; use --force")
        save_lock(root, lock)
    print(f"lock written: {len(files)} row(s)")
    return 0


AUTHOR_WORKTREES_RELPATH = ".claude/.cache/baseline-worktrees"


def cmd_author_start(root: Path) -> int:
    """§5/§7 `author start`: create or reuse this session's baseline author worktree."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if len(session) < 8:
        print("error: CLAUDE_CODE_SESSION_ID is unset or shorter than 8 characters",
              file=sys.stderr)
        return 2
    session8 = session[:8]
    branch = f"author/{session8}"
    worktrees_dir = root / AUTHOR_WORKTREES_RELPATH
    worktree = worktrees_dir / session8
    owner_file = worktrees_dir / f"{session8}.owner"
    if worktree.exists():
        if not owner_file.is_file():
            print(f"error: worktree exists without an owner file: {worktree}", file=sys.stderr)
            return 1
        owner = _lf(owner_file.read_bytes()).decode("utf-8", errors="replace").strip()
        current_branch = _git_text(worktree, ["rev-parse", "--abbrev-ref", "HEAD"])
        if owner != session or current_branch != branch:
            print(f"error: worktree is owned by another session: {worktree}", file=sys.stderr)
            return 1
        print(str(worktree))
        return 0
    lock = load_lock(root)
    cache = (root / CACHE_CLONE).resolve()
    source = ensure_baseline(lock, root, None)
    try:
        pinned = source.sha
    finally:
        source.close()
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    _git(cache, ["worktree", "add", "-b", branch, str(worktree), pinned])
    _write_lf(owner_file, session + "\n")
    print(str(worktree))
    return 0


def _validate_cli_usage(args) -> list[str] | None:
    _validate_relpaths(args.relpaths)
    if args.op == "diff" and len(args.relpaths) != 1:
        raise UsageError("diff requires exactly one relpath")
    if args.op in ("classify", "judge", "forget", "gc", "fork", "track", "paths", "triage", "migrate", "ignore"):
        if args.op not in ("gc", "paths", "triage", "migrate") and not args.relpaths:
            raise UsageError(f"{args.op} requires at least one relpath")
    if args.op == "classify" and not args.status:
        raise UsageError("classify requires --status")
    if args.status is not None and args.op == "classify" and args.status not in V2_STATUSES:
        raise UsageError("--status must be tracked, local, forked or composed")
    if args.op == "judge" and not args.verdict:
        raise UsageError("judge requires --verdict")
    if args.verdict is not None and args.op == "judge" and args.verdict not in VERDICTS:
        raise UsageError("--verdict must be push, keep-local or fork")
    if args.from_relpath is not None:
        if args.op != "classify":
            raise UsageError("--from is only valid with classify")
        _validate_relpath(args.from_relpath)
    if args.inputs is not None:
        if args.op != "classify":
            raise UsageError("--inputs is only valid with classify")
        values = [value for value in args.inputs.split(",") if value]
        _validate_relpaths(values)
        if args.status == "composed" and not values:
            raise UsageError("--inputs is required with --status composed")
        if args.status != "composed" and values:
            raise UsageError("--inputs is only valid with --status composed")
    if args.op == "classify" and args.status == "composed" and not args.inputs:
        raise UsageError("--inputs is required with --status composed")
    if args.batch is not None and (args.batch <= 0 or args.batch > 100000):
        raise UsageError("--batch must be a positive integer")
    if args.status is not None and args.op != "classify" and args.op != "paths":
        raise UsageError("--status is only valid with classify or paths")
    if args.verdict is not None and args.op not in ("judge", "paths"):
        raise UsageError("--verdict is only valid with judge or paths")
    if args.check and args.op != "compose":
        raise UsageError("--check is only valid with compose")
    if args.layers is not None:
        resolve_layers_v2({}, args.layers)
    if args.op == "paths":
        if args.status is not None and args.status not in V2_STATUSES:
            raise UsageError("unknown status")
        if args.verdict is not None and args.verdict not in VERDICTS:
            raise UsageError("unknown verdict")
        if args.judged_since is not None:
            try:
                parsed_since = datetime.fromisoformat(args.judged_since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise UsageError("unparseable --judged-since") from exc
            if parsed_since.tzinfo is None:
                raise UsageError("--judged-since must include a timezone")
    if args.op == "migrate" and args.relpaths:
        raise UsageError("migrate takes no relpaths")
    return [value for value in (args.inputs.split(",") if args.inputs else []) if value]

def _force_lf_stream(stream) -> None:
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")


def main(argv=None) -> int:
    _force_lf_stream(sys.stdout)
    _force_lf_stream(sys.stderr)
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    # `publish` and `author start` take their own flag shapes (not this module's
    # relpaths-plus-flags grammar), and `materialize` is retired -- all three are
    # intercepted before the shared ArgumentParser below, so the merge here stays small.
    if raw_argv[:1] == ["publish"]:
        import baseline_publish
        rest = raw_argv[1:]
        if "--root" not in rest:
            # every other op resolves its root by walking up from cwd (`project_root()`);
            # `baseline_publish.main`'s own `--root` defaults to a bare ".", so this keeps
            # `publish` consistent when invoked from a subdirectory.
            rest = ["--root", str(project_root())] + rest
        return baseline_publish.main(rest)
    if raw_argv[:1] == ["author"]:
        if raw_argv[1:2] != ["start"]:
            print("error: author requires a subcommand: start", file=sys.stderr)
            return 2
        return cmd_author_start(project_root())
    if raw_argv[:1] == ["materialize"]:
        print("error: materialize is retired; use publish --dry-run", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=[
        "check", "diff", "pull", "update-lock", "migrate",
        "classify", "judge", "triage", "forget", "gc", "fork", "track",
        "ignore", "candidates", "paths", "init", "compose",
    ])
    ap.add_argument("relpaths", nargs="*")
    ap.add_argument("--baseline-dir")
    ap.add_argument("--layers", metavar="PURE,CODING,GODOT")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--from", dest="from_relpath")
    ap.add_argument("--inputs")
    ap.add_argument("--verdict")
    ap.add_argument("--borderline", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--judged-since")
    ap.add_argument("--commit")
    ap.add_argument("--repo")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--sub", action="append", default=[], metavar="PLACEHOLDER=VALUE")
    ap.add_argument("--abbrev", action="append", default=[])
    try:
        args = ap.parse_args(argv)
        inputs = _validate_cli_usage(args)
        root = project_root()
        if args.op == "init":
            if not args.baseline_dir or not args.repo or not args.sub:
                raise UsageError("init requires --baseline-dir, --repo and at least one --sub")
            try:
                substitutions = {}
                for item in args.sub:
                    if "=" not in item:
                        raise UsageError("init --sub requires PLACEHOLDER=VALUE")
                    key, value = item.split("=", 1)
                    substitutions[key] = value
            except ValueError as exc:
                raise UsageError("init --sub requires PLACEHOLDER=VALUE") from exc
            layers = resolve_layers_v2({}, args.layers)
            init_lock = {
                "baseline_repo": args.repo,
                "baseline_ref": args.ref,
            }
            source = ensure_baseline(init_lock, root, args.baseline_dir)
            try:
                return v2_init(root, args.baseline_dir, args.repo, args.ref,
                               substitutions, layers, source, args.force)
            finally:
                source.close()

        lock = load_lock(root)
        layers = resolve_layers_v2(lock, args.layers)
        if args.op == "paths":
            return v2_paths(lock, args.status, args.verdict, args.judged_since, args.json)
        if args.op == "gc":
            return v2_gc(root, args.apply)
        if args.op == "forget":
            return v2_forget(root, args.relpaths, args.force)
        if args.op == "classify":
            return v2_classify(root, args.relpaths, args.status, args.from_relpath, inputs,
                                args.force, args.baseline_dir)
        if args.op == "ignore":
            return v2_classify(root, args.relpaths, "local", None, None, args.force, args.baseline_dir)
        if args.op == "judge":
            return v2_judge(root, args.relpaths, args.verdict, args.borderline, args.confirm, args.force)
        if args.op == "candidates":
            cmd_candidates(root, lock, layers, args.baseline_dir)
            return 0
        if args.op == "compose":
            return v2_compose(root, lock, layers, args.check)

        source = None
        try:
            source = ensure_baseline(lock, root, args.baseline_dir)
            if args.op == "check":
                return v2_check(root, lock, source, args.json, layers, args.strict)
            if args.op == "diff":
                entry = lock.get("files", {}).get(args.relpaths[0])
                if entry is not None and not in_profile(entry, layers, legacy_v1=lock.get("schema") != 2):
                    print(f"skip (outside profile): {args.relpaths[0]}")
                    return 1
                cmd_diff(root, lock, source, args.relpaths[0], layers)
                return 0
            if args.op == "pull":
                return v2_pull(root, source, args.relpaths, layers, args.force)
            if args.op == "migrate":
                return v2_migrate(root, source, resolve_layers_v2({}, args.layers), args.abbrev)
            if args.op == "triage":
                return v2_triage(root, lock, source, args.batch, args.json)
            if args.op == "fork":
                return v2_fork(root, args.relpaths, source)
            if args.op == "track":
                return v2_track(root, args.relpaths, source)
            if args.op == "update-lock":
                return v2_update_lock(root, source, args.relpaths, layers)
            raise UsageError(f"unsupported operation: {args.op}")
        finally:
            if source is not None:
                source.close()
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BaselineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
