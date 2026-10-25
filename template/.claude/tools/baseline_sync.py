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
import copy
import difflib
import hashlib
import io
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
import layer_closure  # noqa: E402

LOCK_RELPATH = ".claude/baseline.lock.json"
CACHE_CLONE = ".claude/.cache/baseline-repo"
MANIFEST_NAME = "baseline.manifest.json"
FULL_LAYERS = ("pure", "coding", "godot")
V2_STATUSES = ("tracked", "local", "forked", "composed")
VERDICTS = ("push", "keep-local", "fork")
LOCK_MUTEX_RELPATH = ".claude/.cache/baseline-lock.mutex"
CACHE_MUTEX_RELPATH = ".claude/.cache/baseline-repo.mutex"
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
SETTINGS_RELPATH = ".claude/settings.json"


def settings_composed_inputs(root: Path, recorded: list[str], layers: list[str]) -> list[str]:
    """The inputs of a `settings.json` row: its base first, its project file last, and between
    them each adopted layer's `settings.<layer>.json` overlay present on disk, in profile order."""
    if len(recorded) < 2:
        return list(recorded)
    overlays = [f".claude/{path.name}"
                for path in baseline_compose.settings_overlay_files(root / ".claude", layers)]
    return [recorded[0], *overlays, recorded[-1]]


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
    if is_binary(data):
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def is_binary(data: bytes) -> bool:
    return b"\x00" in data


class BinaryText(str):
    """A binary row inside the text pipeline: latin-1 text for hashing, the raw bytes for every write."""

    raw: bytes

    def __new__(cls, raw: bytes):
        text = super().__new__(cls, raw.decode("latin-1"))
        text.raw = raw
        return text

    def encode(self, *args, **kwargs) -> bytes:
        return self.raw  # every hash site encodes the text: a row's hash is the sha of its raw bytes


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


def read_path_bytes(path: Path | str, wait_s: float = 10.0) -> bytes:
    """Path.read_bytes that waits out a writer's `replace_path` in flight.

    Windows refuses an open of a path another process is replacing (Errno 13), so a reader racing
    a lock save fails without this. Retry until `wait_s` passes, then raise.
    """
    deadline = time.monotonic() + wait_s
    while True:
        try:
            return Path(path).read_bytes()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def sha(data: bytes) -> str:
    return hashlib.sha256(normalize(data)).hexdigest()


# Auto-memory frontmatter keys that name the writing session and its write time. They are provenance,
# never shared content: every sync reader, writer and hash, and the publish scrub, goes through this home.
MEMORY_PREFIX = ".claude/auto-memory/"
PROVENANCE_KEYS = (b"originSessionId:", b"modified:")


def strip_provenance(relpath: str, data: bytes) -> bytes:
    if not (relpath.startswith(MEMORY_PREFIX) and relpath.endswith(".md")):
        return data
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        return data
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip(b"\r\n") == b"---"), None)
    if close is None:
        return data
    kept = [line for line in lines[1:close] if not line.lstrip(b" \t").startswith(PROVENANCE_KEYS)]
    if len(kept) == close - 1:
        return data
    return b"".join([lines[0], *kept, *lines[close:]])


def content_sha(relpath: str, data: bytes) -> str:
    return sha(strip_provenance(relpath, data))


def _write_lf(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, BinaryText):
        data = data.raw
    elif isinstance(data, str):
        data = data.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    path.write_bytes(_lf(data))


def _repo_arg(path: Path) -> str:
    return str(path)


NETWORK_RETRY_DELAYS = (2, 5)
# Far above a healthy call (cloning the baseline repo takes about 3 s); a stuck call dies here and retries.
NETWORK_CALL_TIMEOUT_S = 120.0
_TRANSIENT_NETWORK = re.compile(
    r"connection (?:was )?reset|could not resolve host|timed out|timeout|early eof|"
    r"unexpected disconnect|remote end hung up|rpc failed|failed to connect|"
    r"ssl_read|ssl_error_syscall|tls handshake timeout|gnutls_handshake\(\) failed|schannel: failed to receive|"
    r"returned error: 5\d\d|http 50\d",
    re.IGNORECASE,
)
_REMOTE_GIT = ("fetch", "push", "clone", "ls-remote", "pull")


def git_subcommand(args: list[str]) -> str:
    """The subcommand of a git argument list, past leading `-c k=v` / `-C path` options."""
    items = iter(args)
    for item in items:
        if item in ("-c", "-C"):
            next(items, None)
        elif not item.startswith("-"):
            return item
    return ""


def is_remote_git(args: list[str]) -> bool:
    return git_subcommand(args) in _REMOTE_GIT


def run_with_network_retry(label: str, run, retryable: bool = True):
    """Call `run()` (returns a CompletedProcess); rerun it after a transient network failure.

    Only stderr is matched. Anything else, and the last attempt of a transient run, returns as is.
    A `subprocess.TimeoutExpired` becomes a returncode-124 "timed out" result, so it retries like any transient failure."""
    def attempt_run():
        try:
            return run()
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                args=exc.cmd, returncode=124, stdout=exc.stdout or b"",
                stderr=f"timed out after {int(exc.timeout)} s".encode())

    result = attempt_run()
    if not retryable:
        return result
    for attempt, delay in enumerate(NETWORK_RETRY_DELAYS, start=1):
        if result.returncode == 0:
            break
        stderr = result.stderr or b""
        text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
        found = _TRANSIENT_NETWORK.search(text)
        if not found:
            break
        print(f"{label}: transient network error ({found.group(0)!r}); retry {attempt}/"
              f"{len(NETWORK_RETRY_DELAYS)} in {delay}s", file=sys.stderr)
        time.sleep(delay)
        result = attempt_run()
    return result


def run_git_network(cmd: list[str], args: list[str], label: str | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run git `cmd` (its subcommand and options are `args`); a remote subcommand gets the call timeout and retries."""
    remote = is_remote_git(args)
    return run_with_network_retry(
        label or f"git {' '.join(args)}",
        lambda: subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=NETWORK_CALL_TIMEOUT_S if remote else None,
        ),
        retryable=remote,
    )


def _git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = run_git_network(["git", "-C", _repo_arg(repo), *args], args)
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
        result = run_git_network(
            ["git", "clone", "--no-checkout", repo, str(destination)], ["clone"], "git clone")
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

    def read(self, relpath: str, commit: str | None = None) -> bytes | None:
        if self.process.stdin is None or self.process.stdout is None:
            raise BaselineError("git cat-file --batch pipes are unavailable")
        query = f"{commit or self.commit}:template/{relpath}\n".encode("utf-8")
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
    _exec: frozenset[str] | None = None

    def read(self, relpath: str) -> bytes | None:
        return self.batch.read(relpath)

    def read_at(self, commit: str, relpath: str) -> bytes | None:
        """`relpath` at another commit of the same object store; None when absent or unreadable."""
        return self.batch.read(relpath, commit)

    def exec_relpaths(self) -> frozenset[str]:
        """Template relpaths the pinned commit stores as 100755."""
        if self._exec is None:
            out = _git(self.path, ["ls-tree", "-r", "-z", self.sha, "--", "template"], check=False).stdout
            self._exec = frozenset(path[len("template/"):] for path, mode in baseline_identity.file_modes(out).items()
                                   if mode == "100755")
        return self._exec

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
        lock = json.loads(_lf(read_path_bytes(lock_path)).decode("utf-8"))
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
def _file_mutex(mutex: Path, what: str, wait_s: float):
    """Serialize on an OS file lock over a persistent mutex file.

    The OS drops the lock when its holder exits, so a crashed holder never needs a staleness
    guess. The earlier pid-file mutex had two races: a waiter deleted a mutex whose creator had
    not yet written its pid, and `os.kill(pid, 0)` is not a liveness probe on Windows."""
    mutex.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    with open(mutex, "a+b") as handle:
        while not _try_os_lock(handle):
            if time.monotonic() >= deadline:
                raise BaselineError(f"{what} mutex is held for more than {wait_s:.0f} seconds")
            time.sleep(0.05)
        try:
            yield
        finally:
            _release_os_lock(handle)


def lock_mutex(root: Path):
    """Serialize lock mutations."""
    return _file_mutex(root / LOCK_MUTEX_RELPATH, "baseline lock", 30.0)


def require_v2_lock(lock: dict) -> None:
    """Refuse nonmigration writes before creating outputs, caches or journals."""
    if lock.get("schema") != 2:
        raise BaselineError("v1 lock is read-only; run migrate first")


def mutate_lock(root: Path, mutator, *, allow_v1: bool = False):
    """Apply a mutation to a fresh lock snapshot under the lock mutex."""
    if not allow_v1:
        require_v2_lock(load_lock(root))
    with lock_mutex(root):
        lock = load_lock(root)
        if not allow_v1:
            require_v2_lock(lock)
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
        # Concurrent engine runs share one cache: a second clone, replace or fetch mid-way sees a
        # half-built repository, so only one process sets up and fetches it at a time.
        with _file_mutex(root / CACHE_MUTEX_RELPATH, "baseline cache", 300.0):
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


def row_text(relpath: str, data: bytes) -> str:
    """A row's bytes as the text every hash and comparison reads: a binary row keeps its raw bytes."""
    if is_binary(data):
        return BinaryText(data)
    return strip_provenance(relpath, _lf(data)).decode("utf-8", errors="replace")


def rendered_upstream(relpath: str, data: bytes, subs: dict) -> str:
    """Baseline bytes in this project's form: placeholders substituted; a binary row is never substituted."""
    text = row_text(relpath, data)
    return text if isinstance(text, BinaryText) else forward_for(relpath, text, subs)


def rendered_for_upstream(relpath: str, data: bytes, subs: dict) -> str:
    """This project's bytes in the baseline's form: placeholders restored; a binary row is never substituted."""
    text = row_text(relpath, data)
    return text if isinstance(text, BinaryText) else reverse_for(relpath, text, subs)


def upstream_text(baseline: BaselineSource | Path, relpath: str, subs: dict) -> str | None:
    data = _read_upstream_bytes(baseline, relpath)
    return None if data is None else rendered_upstream(relpath, data, subs)


def local_text(root: Path, relpath: str) -> str | None:
    path = root / relpath
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return row_text(relpath, data)


ADAPTATION_JSON_RELPATH = ".claude/skills/project_subsystems/adaptation.json"


def check_adaptation_contract(root: Path, *, missing_ok: bool = False) -> None:
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
    if adaptation_text is None and missing_ok:
        adaptation_text = "{}"
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
    if skill_text is None and missing_ok:
        return
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
    ".claude/tests/test_baseline_identity.py",
    ".claude/tests/test_baseline_sync_doctor.py",
    ".claude/worklog-titles.md",
}


def forward_for(relpath: str, text: str, subs: dict) -> str:
    """`forward_sub`, except a PLACEHOLDER_OK file keeps its tokens: it names them rather than uses them."""
    return text if relpath in PLACEHOLDER_OK else forward_sub(text, subs)


def reverse_for(relpath: str, text: str, subs: dict) -> str:
    """`reverse_sub`, except a PLACEHOLDER_OK file publishes byte-for-byte, as `forward_for` pulls it."""
    return text if relpath in PLACEHOLDER_OK else reverse_sub(text, subs)


def residual_placeholders(root: Path, lock: dict) -> list[tuple[str, list[str]]]:
    keys = list(lock.get("substitutions", {}))
    hits = []
    for relpath, entry in lock["files"].items():
        if entry.get("status") not in ("tracked", "watch") or relpath in PLACEHOLDER_OK:
            continue
        text = local_text(root, relpath)
        if text is None or isinstance(text, BinaryText):
            continue
        found = sorted(key for key in keys if key in text)
        if found:
            hits.append((relpath, found))
    return sorted(hits)


LAYER_FILE_BY_LAYER = {"pure": "CLAUDE.core.md", "coding": "CLAUDE.coding.md", "godot": "CLAUDE.godot.md"}
LAYER_FILES = tuple(LAYER_FILE_BY_LAYER.values())


def unimported_layer_files(root: Path) -> list[str]:
    """Layer doctrine files present in `.claude/` that the project's CLAUDE.md never `@`-imports.
    A pull can deliver a new layer file; nothing loads it until CLAUDE.md names it."""
    claude = root / ".claude" / "CLAUDE.md"
    text = claude.read_text(encoding="utf-8") if claude.exists() else ""
    imported = {line.strip()[1:] for line in text.splitlines() if line.strip().startswith("@")}
    return [f".claude/{name}" for name in LAYER_FILES
            if (root / ".claude" / name).exists() and name not in imported]


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


def _ignored_untracked(root: Path, relpaths: list[str]) -> list[str]:
    """The paths a `.gitignore` rule hides from `git add` and the index does not already hold."""
    found: set[str] = set()
    for start in range(0, len(relpaths), 200):
        chunk = relpaths[start:start + 200]
        result = _git(root, ["check-ignore", "--", *chunk], check=False)
        if result.returncode == 0:
            found.update(_lf(result.stdout).decode("utf-8", errors="replace").splitlines())
    return [relpath for relpath in relpaths if relpath in found]


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
    if relpath != relpath.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in relpath):
        raise UsageError(f"invalid relpath (edge whitespace or control character): {relpath!r}")
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
    # An intent-to-add entry (`git add -N`) holds the empty blob, not the file; its content is the working copy.
    # No pathspec: a few hundred paths overrun the Windows command-line limit.
    ita = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", "--diff-filter=A"],
                         capture_output=True, check=False).stdout.decode("utf-8", "replace").split("\0")
    wanted = set(pending)
    for relpath in [p for p in ita if p in wanted]:
        try:
            found[relpath] = _lf((root / relpath).read_bytes())
        except OSError:
            continue
    pending = [relpath for relpath in pending if relpath not in found]
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
    return {relpath: (strip_provenance(relpath, data) if data is not None else None)
            for relpath, data in contents.items()}


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


def _manifest_items_v2(source: BaselineSource | Path) -> dict[str, dict]:
    raw = _source_root_bytes(source, MANIFEST_NAME)
    if raw is None:
        return {}
    try:
        value = json.loads(_lf(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {
        item["path"]: item
        for item in value.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def _manifest_layers_v2(source: BaselineSource | Path, root: Path) -> dict[str, str]:
    return {relpath: item.get("layer") for relpath, item in _manifest_items_v2(source).items()}


def _seed_rows(root: Path, relpath: str, layer: str | None, files: dict,
               layers: list[str]) -> dict[str, dict]:
    """A manifest `sync: seed` path is project-owned once it exists locally: a `local` row. Its
    `settings.project.json` seed also makes `.claude/settings.json` a `composed` row when both
    inputs are on disk and no row decides it yet (§9: settings.json is never a manifest entry);
    the row's inputs carry the adopted layers' settings overlays."""
    rows = {relpath: {"status": "local", "layer": layer, "judged": _decision(root, relpath, "keep-local")}}
    if relpath.endswith("settings.project.json"):
        composed_relpath = str(Path(relpath).parent / "settings.json").replace("\\", "/")
        composed_inputs = settings_composed_inputs(root, WATCH_COMPOSED_INPUTS.get(composed_relpath, []), layers)
        if (composed_relpath not in files and composed_inputs
                and all((root / p).is_file() for p in composed_inputs)):
            rows[composed_relpath] = {
                "status": "composed",
                "layer": layer,
                "hash": None,
                "inputs": composed_inputs,
                "judged": None,
            }
    return rows


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
        if base == source.sha:
            return "forked"
        # Every publish moves the pin; the fork moved only when its own upstream file did.
        reader = getattr(source, "read_at", None)
        at_base = reader(base, relpath) if reader else None
        at_pin = source.read(relpath) if reader else None
        if at_base is not None and at_pin is not None and (
                strip_provenance(relpath, _lf(at_base)) == strip_provenance(relpath, _lf(at_pin))):
            return "forked"
        return "forked-upstream-moved"
    if status == "composed":
        local = local_text(root, relpath)
        if entry.get("hash") is None or local is None or sha(local.encode("utf-8")) != entry.get("hash"):
            return "composed-drift"
        return "in-sync"
    upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
    local = local_text(root, relpath)
    if upstream is None:
        # No recorded upstream hash: the baseline never shipped this row, so nothing was removed.
        return "removed-upstream" if entry.get("hash") else "unpublished"
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
    items = _manifest_items_v2(source)
    for relpath in sorted(_manifest_paths_v2(source, root, layers) - set(lock.get("files", {}))):
        results[relpath] = "offered" if items[relpath].get("sync") == "offer" else "new-upstream"
    return results, outside


def _keep_local_current(entry: dict | None, current_sha: str | None) -> bool:
    """A keep-local verdict on the content the row holds now is a decision, so `check --strict` accepts it.
    A verdict on older content (the sha moved since it was judged) stays a finding."""
    judged = (entry or {}).get("judged") or {}
    return judged.get("verdict") == "keep-local" and current_sha is not None and judged.get("sha") == current_sha


def _strict_findings(root: Path, source: BaselineSource, lock: dict,
                     results: dict[str, str]) -> list[tuple[str, str]]:
    files = lock.get("files", {})
    current = _content_shas(root, [relpath for relpath in results
                                   if relpath in files and _entry_status(files[relpath]) == "tracked"])
    findings = []
    for relpath, state in sorted(results.items()):
        entry = files.get(relpath)
        if state == "local-modified" and _keep_local_current(entry, current.get(relpath)):
            continue
        if state in {
            "new-upstream", "forked-upstream-moved", "forked-base-unknown",
            "composed-drift", "watch", "removed-upstream", "missing-local",
            "local-modified", "diverged", "unpublished",
        }:
            findings.append((relpath, state))
        elif entry is not None and _triage_needed(root, source, lock, relpath, entry,
                                                 current.get(relpath, _UNREAD)):
            findings.append((relpath, "needs-judgment"))
    return findings


_TOOL_RELPATH = re.compile(r"\.claude/(hooks|tools|scripts)/(lib/)?[^/_][^/]*\.(py|sh|ps1|js|mjs)$")


def _harness_tests():
    scripts = str(_TOOLS_DIR.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import harness_tests
    return harness_tests


def _bound_proofs(root: Path, files: dict, tools: list[str]) -> dict[str, list[str]]:
    """Each tool's proof rows bound by name or importer, never by a mention, whatever their status."""
    ht = _harness_tests()
    bound = ht.shipped_proofs_by_tool(str(root), tools)
    return {tool: [rel for rel in bound[tool] if rel in files and rel.count("/") == 2] for tool in tools}


def _proves_at_layer(files: dict, tool: str, proof: str) -> bool:
    """A shipped proof counts for a tool only when every consumer of the tool's layer also receives it."""
    if _entry_status(files[proof]) not in ("tracked", "forked"):
        return False
    tool_layer, proof_layer = files[tool].get("layer"), files[proof].get("layer")
    if tool_layer in FULL_LAYERS and proof_layer in FULL_LAYERS:
        return FULL_LAYERS.index(proof_layer) <= FULL_LAYERS.index(tool_layer)
    return True


def unproven_tools(root: Path, files: dict, candidates: list[str]) -> list[str]:
    """The shipped (tracked or forked) tool rows among `candidates` with no shipped proof bound to them at or
    below their own layer."""
    tools = [rel for rel in candidates
             if _TOOL_RELPATH.search(rel) and rel in files and _entry_status(files[rel]) in ("tracked", "forked")]
    bound = _bound_proofs(root, files, tools)
    return [tool for tool in tools if not any(_proves_at_layer(files, tool, proof) for proof in bound[tool])]


def _refuse_unproven_new_tool(root: Path, files: dict, relpath: str, shas: dict[str, str | None],
                              pushing: dict[str, str], commit: str | None) -> None:
    """A new tool row publishes with a proof: one that ships, or one judged push at its current sha."""
    bound = _bound_proofs(root, files, [relpath])[relpath]
    unread = [proof for proof in bound if proof not in shas]
    if unread:
        shas.update(_commit_shas(root, commit, unread) if commit else _content_shas(root, unread))
    for proof in bound:
        judged = files[proof].get("judged") or {}
        if (_entry_status(files[proof]) in ("tracked", "forked") or proof in pushing
                or (judged.get("verdict") == "push" and judged.get("sha") == shas.get(proof))):
            return
    raise BaselineError(
        f"new tool has no shipped proof: {relpath}; judge push, in this call, a proof named "
        f"test_{os.path.splitext(relpath.rsplit('/', 1)[1])[0]}* with it")


def layer_closure_findings(root: Path, lock: dict, source: BaselineSource | Path) -> list[str]:
    """Each tracked row's citation of a file its consumers lack: the fetched manifest overlaid by this
    project's shipped rows, the view publish's `audit_baseline.py` closure step refuses."""
    manifest = {"files": list(_manifest_items_v2(source).values())}
    return [f.render() for f in layer_closure.lock_findings(root, lock, manifest)]


def layer_drift(lock: dict, source: BaselineSource | Path) -> list[tuple[str, str, str]]:
    """Shipped rows whose lock layer differs from the manifest's: a pull never rewrites an existing row's
    layer, so an upstream relayer leaves it stale, and a local relayer stays unpublished until a publish."""
    manifest = _manifest_layers_v2(source, None)
    return [(rel, entry.get("layer"), manifest[rel]) for rel, entry in sorted(lock.get("files", {}).items())
            if _entry_status(entry) in ("tracked", "forked") and manifest.get(rel)
            and entry.get("layer") != manifest[rel]]


def v2_check(root: Path, lock: dict, source: BaselineSource, as_json: bool,
              layers: list[str], strict: bool) -> int:
    results, outside = _check_results(root, lock, source, layers)
    residual = residual_placeholders(root, lock)
    unimported = unimported_layer_files(root)
    closure = layer_closure_findings(root, lock, source)
    drift = layer_drift(lock, source)
    findings =_strict_findings(root, source, lock, results) if strict or not as_json else []
    findings = list(findings) + [(rel, "layer-drift") for rel, _lock, _manifest in drift]
    findings += [(rel, "unimported-layer-file") for rel in unimported]
    findings += [(line, "layer-closure") for line in closure]
    unproven = unproven_tools(root, lock.get("files", {}), list(results)) if strict or not as_json else []
    findings += [(rel, "unproven") for rel in unproven]
    if as_json:
        payload = {
            "baseline_commit": baseline_commit(source),
            "results": results,
            "residual_placeholders": dict(residual),
            "profile": {"layers": layers, "files_outside": outside},
        }
        if unimported:
            payload["unimported_layer_files"] = unimported
        if closure:
            payload["layer_closure"] = closure
        if unproven:
            payload["unproven"] = unproven
        if drift:
            payload["layer_drift"] = [{"path": rel, "lock": was, "manifest": now} for rel, was, now in drift]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        buckets: dict[str, list[str]] = {}
        for relpath, state in results.items():
            buckets.setdefault(state, []).append(relpath)
        quiet = {"in-sync", "watch", "forked", "local", "offered"}
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
        if unimported:
            print("unimported-layer-file (CLAUDE.md never loads it; add an `@` line under `@CLAUDE.core.md`):")
            for relpath in unimported:
                print("  " + relpath)
        if closure:
            print("layer-closure (a tracked row cites a file its consumers lack; publish refuses it):")
            for line in closure:
                print("  " + line)
        for relpath in unproven:
            print("unproven: " + relpath)
        for relpath, was, now in drift:
            print(f"layer-drift: {relpath} (lock {was}, manifest {now})")
        for relpath, state in findings:
            if state == "needs-judgment":
                print("needs-judgment: " + relpath)
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


def _classify_identity_hit(root: Path, relpath: str, profile: dict, data: bytes | None = None,
                           substitutions: dict | None = None):
    """The first identity-scan hit in `relpath`'s current content (index, else HEAD, else the
    working copy -- `_content_bytes`), scanned as if every line were newly added.

    §7 F8: a hand-rolled substring check against lock substitutions/abbreviations missed every
    other profile kind (concatenation, home path, topology token, content noun) and any hit in
    a file that already existed with different content. `baseline_identity.scan_changed` is the
    one profile-aware scanner (Design §3); running it over the whole file as added lines is how
    a `classify --status tracked` proof plants a hit and gets a real refusal. The scanned text is
    the reverse-substituted form `publish` materializes, so a lock substitution value alone is
    not a hit."""
    hits = _identity_hits(root, relpath, profile, data, substitutions)
    return hits[0] if hits else None


def _identity_hits(root: Path, relpath: str, profile: dict, data: bytes | None = None,
                   substitutions: dict | None = None) -> list:
    """Every hit `_classify_identity_hit` scans for; empty when the row has no content."""
    if data is None:
        data = _content_bytes(root, relpath)
    if data is None:
        return []
    text = reverse_for(relpath, data.decode("utf-8", errors="replace"), substitutions or {})
    return baseline_identity.scan_changed(_whole_file_as_added_diff(relpath, text), profile)


def _try_source(root: Path, baseline_dir: str | None) -> tuple[BaselineSource | None, str | None]:
    """The pinned baseline for a write's checks; a failure to open it is returned, not raised."""
    try:
        return ensure_baseline(load_lock(root), root, baseline_dir), None
    except BaselineError as exc:
        return None, str(exc)


def _print_closure_delta(root: Path, before: dict | None, after: dict | None,
                         source: BaselineSource | None, source_error: str | None) -> None:
    """Print the layer-closure findings a lock write adds (after minus before); never refuses.
    The scan is skipped when the write moved no shipped layer and no tracked row."""
    if before is None or after is None:
        return

    def shape(lock: dict):
        return layer_closure.lock_layers(lock), layer_closure.lock_checked(lock)

    if shape(before) == shape(after):
        return
    if source is None:
        print(f"layer-closure: not checked ({source_error})")
        return
    added = sorted(set(layer_closure_findings(root, after, source))
                   - set(layer_closure_findings(root, before, source)))
    if not added:
        print("layer-closure: no new findings")
        return
    print(f"layer-closure: this write adds {len(added)} finding(s) (publish refuses them):")
    for line in added:
        print("  " + line)


def v2_classify(root: Path, relpaths: list[str], status: str, source_relpath: str | None,
                 inputs: list[str] | None, force: bool, baseline_dir: str | None = None,
                 layer_override: str | None = None, relayer: bool = False) -> int:
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
    # A `local` row may name a path only upstream carries: the consumer declines that file.
    absent_upstream: dict[str, str] = {}
    present = _git_contents(root, relpaths)
    missing = [relpath for relpath in relpaths if relpath not in present]
    source, source_error = _try_source(root, baseline_dir)
    snapshots: dict[str, dict] = {}
    if missing and status == "local":
        if source is None:
            raise BaselineError(source_error)
        lock_now = load_lock(root)
        for relpath in missing:
            upstream = _upstream_sha(source, relpath, lock_now.get("substitutions", {}))
            if upstream is not None:
                absent_upstream[relpath] = upstream

    def apply(lock: dict):
        snapshots["before"] = copy.deepcopy(lock)
        files = lock.setdefault("files", {})
        source_entry = files.get(source_relpath) if source_relpath else None
        if source_relpath and source_entry is None:
            raise BaselineError(f"--from is not a lock row: {source_relpath}")
        profile = None  # built on the first tracked row: it reads the pinned template tree
        committed = _git_contents(root, relpaths)
        for relpath in relpaths:
            if relpath not in committed and relpath not in absent_upstream:
                raise BaselineError(
                    f"classify requires an index or HEAD path, or a local row for a path upstream carries: {relpath}")
            if status == "tracked":
                if profile is None:
                    profile = baseline_identity.build_profile_for_repo(root, baseline_dir=baseline_dir)
                hit = _classify_identity_hit(root, relpath, profile, committed[relpath],
                                             lock.get("substitutions", {}))
                if hit is not None:
                    raise BaselineError(
                        f"identity scan hit in {relpath}:{hit.line} "
                        f"({hit.token_kind}): {hit.excerpt}"
                    )
            existing = files.get(relpath)
            # A decline of a row whose file is already gone has no local content to protect: a
            # bare pull takes every new-upstream row, so a decline usually follows it.
            declines_gone = status == "local" and relpath not in committed and relpath in absent_upstream
            if (existing is not None and _entry_status(existing) != status and not force
                    and not declines_gone):
                raise BaselineError(f"row has status {_entry_status(existing)}; use --force: {relpath}")
            if (layer_override and existing is not None and existing.get("layer") is not None
                    and existing["layer"] != layer_override and not relayer):
                raise BaselineError(
                    f"row has layer {existing['layer']}; --layer {layer_override} moves it: "
                    f"pass --relayer to confirm: {relpath}")
        changed = []
        for relpath in relpaths:
            existing = files.get(relpath)
            absent = relpath not in committed
            current_sha = absent_upstream[relpath] if absent else content_sha(relpath, committed[relpath])
            # --layer records a row the baseline manifest does not list yet; publish needs it.
            layer = layer_override or (
                (source_entry or {}).get("layer") if source_entry else (existing or {}).get("layer"))
            desired_inputs = list(inputs or []) if status == "composed" else None
            same = (
                existing is not None
                and _entry_status(existing) == status
                and (existing.get("judged") or {}).get("sha") == current_sha
                and existing.get("layer") == layer
                and existing.get("inputs") == desired_inputs
                and existing.get("from") == source_relpath
                and bool(existing.get("absent")) == absent
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
            if absent:
                entry["absent"] = True
            else:
                entry.pop("absent", None)
            entry["judged"] = _decision(root, relpath, _classify_decision_verdict(status), sha_value=current_sha)
            files[relpath] = entry
            changed.append(relpath)
        snapshots["after"] = copy.deepcopy(lock)
        return changed or NO_CHANGE

    try:
        result = mutate_lock(root, apply)
        if result is NO_CHANGE:
            print("no change")
        else:
            for relpath in result:
                print(f"classified: {relpath}")
            _print_closure_delta(root, snapshots.get("before"), snapshots.get("after"), source, source_error)
    finally:
        if source is not None:
            source.close()
    return 0


def _commit_shas(root: Path, commit: str, relpaths: list[str]) -> dict[str, str | None]:
    """Each row's sha at `commit`, never the shared index: `publish --from-commit` checks the
    judged sha against that commit's blob, and a peer's staged edit would otherwise stand in."""
    blobs = _cat_file_many(root, [f"{commit}:{relpath}" for relpath in relpaths])
    if blobs is None:
        return {relpath: None for relpath in relpaths}
    return {relpath: (content_sha(relpath, data) if data is not None else None)
            for relpath, data in zip(relpaths, blobs)}


def _commit_blob(root: Path, commit: str, relpath: str) -> bytes | None:
    blobs = _cat_file_many(root, [f"{commit}:{relpath}"])
    return blobs[0] if blobs else None


_DEPENDENCY_MEANING = {
    "local": "it never ships, so consumers lack it",
    "watch": "it never ships, so consumers lack it",
    "local-modified": "it differs from the baseline copy, so consumers get older bytes",
    "unpublished": "the baseline lacks it, so consumers lack it",
}


_ADDED_CITATION_MEANING = "this change adds the citation and consumers have older bytes"


def _refuse_unshipped_dependencies(root: Path, relpath: str, data: bytes | None, lock: dict,
                                   source: BaselineSource | None, pushing: set[str],
                                   commit: str | None) -> None:
    """A row publishes with what it imports and cites: each dependency that is a lock row is in sync
    with the baseline, judged push at its current sha (here or in this call), forked, or a `local` row
    the baseline ships (a seed): a forked or shipped-local dependency reaches every consumer as the
    baseline's copy. A `local-modified` dependency (the baseline ships it, consumers hold older bytes)
    refuses only when the baseline copy of `relpath` does not already cite it: consumers already run
    that pairing, so only a citation this change adds is new. `local`, `watch` and `unpublished`
    dependencies (consumers lack the file) refuse whatever the baseline copy cites, and a row new to
    the baseline has no old citations."""
    files = lock.get("files", {})
    text = data.decode("utf-8", errors="replace") if data is not None else None
    layers = None
    shipped: dict[str, dict] = {}
    if source is not None:
        shipped = _manifest_items_v2(source)
        layers = layer_closure.consumer_layers(lock, {"files": list(shipped.values())})
        layers.setdefault(relpath, (files.get(relpath) or {}).get("layer") or "pure")
    try:
        deps = [dep for dep in layer_closure.row_dependencies(root, relpath, layers, text) if dep in files]
    except (OSError, ValueError) as exc:
        raise BaselineError(f"dependency check failed for {relpath}: {exc}")
    old_deps: set[str] | None = None
    if source is not None:
        old_bytes = _read_upstream_bytes(source, relpath)
        if old_bytes is not None:
            try:
                old_deps = set(layer_closure.row_dependencies(
                    root, relpath, layers, old_bytes.decode("utf-8", errors="replace")))
            except (OSError, ValueError) as exc:
                raise BaselineError(f"dependency check failed for {relpath}: {exc}")
    states = {}
    for dep in deps:
        if dep in pushing:
            continue
        entry = files[dep]
        state = _v2_state(root, source, lock, dep, entry) if source is not None else _entry_status(entry)
        if state in ("in-sync", "in-sync-lock-stale", "forked") or (state == "local" and dep in shipped):
            continue
        judged = entry.get("judged") or {}
        current = (_commit_shas(root, commit, [dep]) if commit else _content_shas(root, [dep])).get(dep)
        if judged.get("verdict") == "push" and judged.get("sha") == current:
            continue
        if state == "local-modified" and old_deps is not None and dep in old_deps:
            continue
        states[dep] = state
    if states:
        lines = [f"  {dep}: {state}; "
                 + (_ADDED_CITATION_MEANING if state == "local-modified" and old_deps is not None
                    else _DEPENDENCY_MEANING.get(state, 'it is not in sync with the baseline'))
                 + f"; run `judge {dep} --verdict push`" for dep, state in states.items()]
        raise BaselineError(f"{relpath} depends on rows consumers would not receive:\n" + "\n".join(lines))


def _push_preflight(root: Path, relpath: str, entry: dict, data: bytes | None, profile: dict,
                    subs: dict, source: BaselineSource | None, source_error: str | None,
                    accept_hits: list[str] | None, lock: dict, pushing: dict[str, str],
                    commit: str | None = None) -> list[str]:
    """Refuses the row's first unaccepted identity hit; returns the accepted hit ids its content carries,
    which the verdict records so `publish` honors them."""
    content = data if data is not None else _content_bytes(root, relpath)
    text = content.decode("utf-8", errors="replace") if content is not None and not is_binary(content) else ""
    literal = sorted(key for key in subs if key.startswith("{{") and key in text) if relpath not in PLACEHOLDER_OK else []
    if literal:
        raise BaselineError(
            f"{relpath} holds {', '.join(literal)} literally, which a consumer's pull renders, so the row can never "
            f"match the baseline again; build the token from parts (\"{{{{\" + name + \"}}}}\")")
    hits = _identity_hits(root, relpath, profile, data, subs)
    for hit in hits:
        if hit.id not in (accept_hits or []):
            raise BaselineError(
                f"identity scan hit in {relpath}:{hit.line} ({hit.token_kind}): {hit.excerpt}; "
                f"fix the source, or pass --accept-hit {hit.id} for a legitimate generic use")
    if entry.get("layer") not in FULL_LAYERS:
        if source is None:
            raise BaselineError(f"row has no layer and the baseline cannot be read to see "
                                f"whether it ships: {relpath} ({source_error})")
        if _upstream_sha(source, relpath, subs) is None:
            raise BaselineError(
                f"new row has no layer and publish refuses it; record one first: "
                f"classify {relpath} --status tracked --layer pure|coding|godot")
    _refuse_unshipped_dependencies(root, relpath, data, lock, source, pushing, commit)
    if _TOOL_RELPATH.search(relpath) and source is not None and _upstream_sha(source, relpath, subs) is None:
        _refuse_unproven_new_tool(root, lock.get("files", {}), relpath, {}, pushing, commit)
    return sorted({hit.id for hit in hits})


def _carry_walk(root: Path, base: str, head: str, relpath: str,
                start_shas: set[str]) -> tuple[str | None, bytes | str | None, bytes | None]:
    """(final sha, first carried commit's blob before it, final blob), or (None, reason, None)
    where the reason is the `not carried` text. Walks first-parent commits oldest first; the first change
    must start from one of `start_shas`."""
    commits = _git_text(root, ["rev-list", "--reverse", "--first-parent", f"{base}..{head}"]).split()
    cursor, first_before, final = None, None, None
    changed = False
    for commit in commits:
        pair = _cat_file_many(root, [f"{commit}^:{relpath}", f"{commit}:{relpath}"]) or [None, None]
        before, after = pair
        if before is None and after is not None:
            return None, "added by the range", None
        if before is not None and after is None:
            return None, "deleted by the range", None
        if before is None or after is None or content_sha(relpath, before) == content_sha(relpath, after):
            continue
        changed = True
        if content_sha(relpath, before) not in ({cursor} if first_before is not None else start_shas):
            return None, "chain broken", None
        if first_before is None:
            first_before = before
        cursor, final = content_sha(relpath, after), after
    if not changed:
        return None, "unchanged by the range", None
    return cursor, first_before, final


def _diff_stat(before: bytes, after: bytes) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(before.decode("utf-8", errors="replace").splitlines(),
                              after.decode("utf-8", errors="replace").splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def v2_carry(root: Path, relpaths: list[str], carry: str, baseline_dir: str | None = None,
             accept_hits: list[str] | None = None) -> int:
    _validate_relpaths(relpaths)
    base, head = carry.split("..", 1)
    for rev in (base, head):
        if _git(root, ["rev-parse", "--verify", "-q", rev + "^{commit}"], check=False).returncode != 0:
            raise BaselineError(f"--carry names a revision git cannot read: {rev}")
    source, source_error = _try_source(root, baseline_dir)
    snapshots: dict[str, dict] = {}
    report: list[str] = []

    def apply(lock: dict):
        snapshots["before"] = copy.deepcopy(lock)
        report.clear()
        files = lock.get("files", {})
        subs = lock.get("substitutions", {})
        profile = None
        carried = []
        walks: dict[str, tuple[str, bytes | str | None, bytes | None]] = {}
        for relpath in relpaths:
            if relpath not in files:
                raise BaselineError(f"not a row: {relpath}")
            old = files[relpath].get("judged") or {}
            if old.get("verdict") != "push":
                report.append(f"not carried: {relpath} (judged {old.get('verdict') or 'never'}, not push)")
                continue
            # A pulled row's recorded upstream copy is already published, so a chain may start there too.
            starts = {sha for sha in (old.get("sha"), files[relpath].get("hash")) if sha}
            final_sha, first_before, final = _carry_walk(root, base, head, relpath, starts)
            if final_sha is None:
                report.append(f"not carried: {relpath} ({first_before})")
                continue
            walks[relpath] = (final_sha, first_before, final)
        # Only rows whose carry succeeded are being pushed at their final sha; a named dependency whose carry
        # failed is judged at its current blob like any other dependency.
        carrying = {rel: walk[0] for rel, walk in walks.items()}
        for relpath, (final_sha, first_before, final) in walks.items():
            entry = files[relpath]
            old = entry.get("judged") or {}
            if profile is None:
                profile = baseline_identity.build_profile_for_repo(root, baseline_dir=baseline_dir)
            accepted = _push_preflight(root, relpath, entry, final, profile, subs, source, source_error,
                                       [*(accept_hits or []), *old.get("accepted_hits", [])], lock, carrying)
            added, removed = _diff_stat(first_before, final)
            start = content_sha(relpath, first_before)
            report.append(f"carried: {relpath} (+{added} -{removed}) {start[:8]}..{final_sha[:8]}")
            decision = _decision(root, relpath, "push", sha_value=final_sha)
            decision["carried_from"] = start
            if accepted:
                decision["accepted_hits"] = accepted
            entry["judged"] = decision
            carried.append(relpath)
        snapshots["after"] = copy.deepcopy(lock)
        return carried or NO_CHANGE

    try:
        mutate_lock(root, apply)
        for line in report:
            print(line)
        _print_closure_delta(root, snapshots.get("before"), snapshots.get("after"), source, source_error)
    finally:
        if source is not None:
            source.close()
    return 0


def v2_judge(root: Path, relpaths: list[str], verdict: str, borderline: bool,
              confirm: bool, force: bool, commit: str | None = None,
              baseline_dir: str | None = None, accept_hits: list[str] | None = None) -> int:
    if verdict not in VERDICTS:
        raise UsageError("--verdict must be push, keep-local or fork")
    if borderline and confirm:
        raise UsageError("--borderline and --confirm are mutually exclusive")
    _validate_relpaths(relpaths)
    source, source_error = _try_source(root, baseline_dir)
    snapshots: dict[str, dict] = {}

    def apply(lock: dict):
        snapshots["before"] = copy.deepcopy(lock)
        files = lock.get("files", {})
        decisions = {}
        current = _commit_shas(root, commit, relpaths) if commit else _content_shas(root, relpaths)
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
        subs = lock.get("substitutions", {})
        pushing = {rel: current[rel] for rel in relpaths} if verdict == "push" else {}
        profile = None  # built on the first pushed row: it reads the pinned template tree
        changed = []
        for relpath, current_sha in decisions.items():
            entry = files[relpath]
            old = entry.get("judged") or {}
            confirmed_at = _utc_now() if confirm else None
            # A tracked row never shipped (no hash, upstream lacks it) is local; left tracked it
            # reads `removed-upstream` in `check --strict`.
            unshipped = (
                verdict == "keep-local" and _entry_status(entry) == "tracked" and not entry.get("hash")
                and source is not None and _upstream_sha(source, relpath, subs) is None
            )
            same = (
                old.get("sha") == current_sha
                and old.get("verdict") == verdict
                and (not confirm or old.get("confirmed_at") is not None)
                and not unshipped
            )
            if same and not (verdict == "push" and accept_hits):
                continue
            accepted = []
            if verdict == "push":
                if profile is None:
                    profile = baseline_identity.build_profile_for_repo(root, baseline_dir=baseline_dir)
                data = _commit_blob(root, commit, relpath) if commit else _content_bytes(root, relpath)
                accepted = _push_preflight(root, relpath, entry, data, profile, subs, source, source_error,
                                           accept_hits, lock, pushing, commit)
            entry["judged"] = _decision(
                root, relpath, verdict, sha_value=current_sha,
                borderline=borderline, confirmed_at=confirmed_at,
            )
            if accepted:
                entry["judged"]["accepted_hits"] = accepted
            if unshipped:
                entry["status"] = "local"
                entry.pop("hash", None)
            changed.append(relpath)
        snapshots["after"] = copy.deepcopy(lock)
        return changed or NO_CHANGE

    try:
        result = mutate_lock(root, apply)
        if result is NO_CHANGE:
            print("no change")
        else:
            for relpath in result:
                print(f"judged: {relpath}")
            _print_closure_delta(root, snapshots.get("before"), snapshots.get("after"), source, source_error)
    finally:
        if source is not None:
            source.close()
    return 0


def v2_triage(root: Path, lock: dict, source: BaselineSource, batch: int, as_json: bool,
              baseline_dir: str | None = None) -> int:
    """Each row's `identity` lists every hit `judge --verdict push` would refuse, scanned over the
    reverse-substituted file; the diff shows substituted text, so it cannot tell a judge which
    literals ship."""
    files = lock.get("files", {})
    current = _content_shas(root, [relpath for relpath, entry in files.items() if _entry_status(entry) == "tracked"])
    selected = []
    for relpath, entry in sorted(files.items()):
        if _triage_needed(root, source, lock, relpath, entry, current.get(relpath, _UNREAD)):
            selected.append((relpath, entry))
        if len(selected) >= batch:
            break
    rows = []
    profile = baseline_identity.build_profile_for_repo(root, baseline_dir=baseline_dir) if selected else None
    subs = lock.get("substitutions", {})
    for relpath, entry in selected:
        judged = entry.get("judged") or {}
        rows.append({
            "relpath": relpath,
            "status": _entry_status(entry),
            "layer": entry.get("layer"),
            "sha": current[relpath] if relpath in current else _content_sha(root, relpath),
            "prior_verdict": judged.get("verdict"),
            "diff": _diff_text(root, source, lock, relpath),
            "identity": [{"id": hit.id, "line": hit.line, "kind": hit.token_kind, "excerpt": hit.excerpt}
                         for hit in _identity_hits(root, relpath, profile, substitutions=subs)],
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
    totals = {"drop": 0, "keep-tracked": 0, "keep-declined": 0, "unreadable": 0}
    for relpath, entry in sorted(lock.get("files", {}).items()):
        if (root / relpath).exists():
            continue
        status = _entry_status(entry) if isinstance(entry, dict) else ""
        if status == "local" and entry.get("absent"):
            tag = "keep-declined"
        elif status in ("local", "forked", "watch"):
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
    print("totals: scanned=%d drop=%d keep-tracked=%d keep-declined=%d unreadable=%d" % (
        scanned, totals["drop"], totals["keep-tracked"], totals["keep-declined"], totals["unreadable"]))
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


def _merge_base_text(source: BaselineSource, relpath: str, base_hash: str | None, subs: dict) -> str | None:
    """The upstream text a row's `hash` records: the newest upstream commit of the file whose forwarded text matches."""
    if not base_hash:
        return None
    log = _git(source.path, ["log", "--format=%H", source.sha, "--", f"template/{relpath}"], check=False)
    for commit in log.stdout.decode("utf-8", errors="replace").split():
        data = source.read_at(commit, relpath)
        if data is None:
            continue
        text = rendered_upstream(relpath, data, subs)
        if sha(text.encode("utf-8")) == base_hash:
            return text
    return None


def _merge3(local: str, base: str, upstream: str) -> tuple[str, int]:
    """`git merge-file` of local and upstream over base: the merged text and its conflict count."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in (("local", local), ("base", base), ("upstream", upstream)):
            path = Path(tmp) / name
            path.write_bytes(text.encode("utf-8"))
            paths.append(str(path))
        result = subprocess.run(
            ["git", "merge-file", "-p", "-L", "local", "-L", "base", "-L", "upstream", *paths],
            capture_output=True,
        )
    if result.returncode < 0 or result.returncode > 127:
        raise BaselineError(f"git merge-file failed: {result.stderr.decode('utf-8', errors='replace')}")
    return result.stdout.decode("utf-8", errors="replace"), result.returncode


def _apply_source_mode(root: Path, source: BaselineSource, relpath: str) -> None:
    if relpath in source.exec_relpaths():
        os.chmod(root / relpath, 0o755)


def _write_row(root: Path, source: BaselineSource, relpath: str, data: bytes | str) -> None:
    """Every pull write of a delivered row: its content and the baseline's executable bit, in one call."""
    _write_lf(root / relpath, data)
    _apply_source_mode(root, source, relpath)


ENGINE_RELPATH = ".claude/tools/baseline_sync.py"
ENGINE_UPDATED_ENV = "BASELINE_SYNC_ENGINE_UPDATED"


def _pending_engine_rows(root: Path, source: BaselineSource, lock: dict, layers: list[str]) -> list[str]:
    """The `.claude/tools/*.py` rows a bare pull would write, when the running engine is this project's own and
    one of them is the engine: pulled first, so the rest of the pull runs on the code it brings (a binary row
    written by an engine that predates binary rows is rewritten as text). Empty on the rerun."""
    if os.environ.get(ENGINE_UPDATED_ENV) or Path(__file__).resolve() != (root / ENGINE_RELPATH).resolve():
        return []
    files = lock.get("files", {})
    rows = [path for path, entry in sorted(files.items())
            if path.startswith(".claude/tools/") and path.endswith(".py") and in_profile(entry, layers)
            and _v2_state(root, source, lock, path, entry) in ("upstream-updated", "new-upstream")]
    rows += [path for path, item in sorted(_manifest_items_v2(source).items())
             if path not in files and path.startswith(".claude/tools/") and path.endswith(".py")
             and item.get("sync") not in ("offer", "seed") and (not item.get("layer") or item["layer"] in layers)
             and not (root / path).exists()]
    return rows if ENGINE_RELPATH in rows else []


def v2_pull(root: Path, source: BaselineSource, relpaths: list[str], layers: list[str], force: bool,
            merge: bool = False) -> int:
    _validate_relpaths(relpaths)
    manifest_items = _manifest_items_v2(source)
    manifest_layers = {relpath: item.get("layer") for relpath, item in manifest_items.items()}
    skipped = 0
    unrowed_differs: list[str] = []
    held: list[tuple[str, str]] = []
    kept_seeds: list[str] = []
    retired: list[str] = []
    kept_local: list[str] = []
    offered: list[str] = []
    merged: list[str] = []
    conflicts: list[tuple[str, str, Path | None]] = []

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
                if state == "forked-upstream-moved" or (state == "diverged" and not merge):
                    held.append((path, state))
                if state in ("upstream-updated", "new-upstream", "in-sync-lock-stale") or (
                        merge and state == "diverged") or (
                    state == "removed-upstream" and _entry_status(entry) == "tracked"
                ):
                    targets.append(path)
            unrowed = sorted(
                path for path, layer in manifest_layers.items()
                if path not in files and (not layer or layer in layers)
            )
            offered[:] = [path for path in unrowed if manifest_items[path].get("sync") == "offer"]
            targets.extend(path for path in unrowed if path not in offered)
        targets = list(dict.fromkeys(targets))
        plans = []
        for relpath in targets:
            entry = files.get(relpath)
            # A tracked row whose upstream file is gone: the upstream deletion reaches the
            # consumer here, never as a refusal (retire an unmodified copy, keep a modified one).
            if entry is not None and _entry_status(entry) == "tracked" and (
                upstream_text(source, relpath, lock.get("substitutions", {})) is None
            ):
                plans.append((relpath, "retire", None))
                continue
            upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
            if upstream is None:
                raise _missing_error(relpath)
            if entry is None:
                local = local_text(root, relpath)
                if manifest_items.get(relpath, {}).get("sync") == "seed":
                    plans.append((relpath, "seed", upstream if local is None else None))
                    continue
                if local is not None and sha(local.encode("utf-8")) != sha(upstream.encode("utf-8")):
                    if relpaths and not force:
                        raise BaselineError(f"pull requires --force: {relpath} exists locally with "
                                            "different content and no lock row")
                    if not relpaths:
                        unrowed_differs.append(relpath)
                        continue
                plans.append((relpath, None, upstream))
                continue
            state = _v2_state(root, source, lock, relpath, entry)
            if state == "in-sync":
                plans.append((relpath, "noop", upstream))
                continue
            if state in ("composed-drift", "removed-upstream"):
                raise BaselineError(f"pull refuses {state}: {relpath}")
            if merge and state == "diverged":
                if not _git_clean_path(root, relpath):
                    conflicts.append((relpath, "uncommitted local edits; commit them, then pull --merge it", None))
                    continue
                plans.append((relpath, "merge", upstream))
                continue
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
        seeds = []
        for relpath, action, upstream in plans:
            if action == "noop":
                continue
            if action == "seed":
                if upstream is not None:
                    _write_row(root, source, relpath, upstream)
                else:
                    kept_seeds.append(relpath)
                seeds.append(relpath)
                changed.append(relpath)
                continue
            if action == "retire":
                local = local_text(root, relpath)
                if local is None or sha(local.encode("utf-8")) == files[relpath].get("hash"):
                    if local is not None:
                        (root / relpath).unlink()
                    del files[relpath]
                    retired.append(relpath)
                else:
                    files[relpath]["status"] = "local"
                    files[relpath].pop("hash", None)
                    files[relpath].pop("base", None)
                    kept_local.append(relpath)
                changed.append(relpath)
                continue
            if action == "merge":
                if isinstance(upstream, BinaryText):
                    conflicts.append((relpath, "binary row; keep the local copy or take upstream with pull --force", None))
                    continue
                subs = lock.get("substitutions", {})
                base = _merge_base_text(source, relpath, files[relpath].get("hash"), subs)
                local = local_text(root, relpath) or ""
                if base is None:
                    conflicts.append((relpath, "no upstream base matches the row's hash; merge by hand", None))
                    continue
                text, hunks = _merge3(local, base, upstream)
                if hunks:
                    copy_path = (root / MERGE_COPY_DIR / relpath).resolve()
                    _write_lf(copy_path, text)
                    _write_lf(_merge_base_marker(copy_path), sha(upstream.encode("utf-8")) + "\n")
                    conflicts.append((relpath, f"{hunks} hunks", copy_path))
                    continue
                _write_row(root, source, relpath, text)
                files[relpath]["hash"] = sha(upstream.encode("utf-8"))
                merged.append(relpath)
                changed.append(relpath)
                continue
            _write_row(root, source, relpath, upstream)
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
                    files[relpath].pop("absent", None)
                else:
                    files[relpath]["hash"] = upstream_hash
                    # A tracked row follows upstream: its verdict judges the copy just written, as a new row's does.
                    files[relpath]["judged"] = _decision(root, relpath, "push", sha_value=upstream_hash)
            changed.append(relpath)
        # Seed rows are decided after every write, so a composed settings.json row sees the
        # settings.base.json this same pull delivered.
        for relpath in seeds:
            files.update(_seed_rows(root, relpath, manifest_layers.get(relpath), files, layers))
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
            if relpath in retired:
                print(f"retired: {relpath}")
            elif relpath in kept_local:
                print(f"kept local (modified, removed upstream): {relpath}")
            elif relpath in kept_seeds:
                print(f"kept (seed, local row added): {relpath}")
            elif relpath in merged:
                print(f"merged: {relpath}")
            else:
                print(f"pulled: {relpath}")
        written = [relpath for relpath in result
                   if relpath not in retired and relpath not in kept_local and relpath not in kept_seeds]
        for relpath in _ignored_untracked(root, written):
            print(f"ignored: {relpath} (add with git add -f)")
    for relpath in dict.fromkeys(unrowed_differs):
        print(f"skipped (local file differs, no lock row): {relpath}")
    for relpath, state in dict.fromkeys(held):
        merge_hint = f"`pull --merge {relpath}` combines both, " if state == "diverged" else ""
        print(f"held ({state}): {relpath} — upstream changed it too; {merge_hint}`pull --force {relpath}` takes "
              f"upstream, `fork {relpath}` keeps yours")
    if offered:
        print(f"offered: {len(offered)} file(s) — not pulled; `check --json` lists them, `pull <relpath>` adopts one")
    if skipped:
        print(f"profile: {','.join(layers)} — {skipped} row(s) skipped")
    for relpath, reason, copy_path in conflicts:
        print(f"conflict: {relpath} ({reason})" + (f" -> {copy_path}" if copy_path else ""))
    render_lane_agents(root)
    write_harness_ignore_block(root)
    return 1 if conflicts else 0


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
                if _entry_status(entry) != "tracked" or not in_profile(entry, layers) or not entry.get("hash"):
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


MERGE_COPY_DIR = Path(".claude") / "scratch" / "baseline_merge"
MERGE_MARKER_RE = re.compile(r"^(?:<{7} local|={7}|>{7} upstream)$", re.M)


def _merge_base_marker(copy_path: Path) -> Path:
    """The file beside a conflict copy holding the upstream hash that copy was merged against."""
    return copy_path.with_name(copy_path.name + ".base")


def v2_resolve(root: Path, source: BaselineSource, relpaths: list[str]) -> int:
    """Install each `pull --merge` conflict copy the owner resolved, and record the upstream it merged as the row's
    base. Refuses a dirty working file (the copy would overwrite edits made since the merge) and an upstream that
    moved since the merge (the copy never saw those changes)."""
    _validate_relpaths(relpaths)
    if not relpaths:
        raise UsageError("resolve needs the relpaths of resolved conflict copies")
    copies = {}
    for relpath in relpaths:
        copy_path = root / MERGE_COPY_DIR / relpath
        text = local_text(root, str(MERGE_COPY_DIR / relpath))
        if text is None:
            raise BaselineError(f"no conflict copy for {relpath}: {copy_path}")
        if MERGE_MARKER_RE.search(text):
            raise BaselineError(f"conflict markers remain in {copy_path}")
        if not _git_clean_path(root, relpath):
            raise BaselineError(f"resolve refuses a dirty path: {relpath}; commit or discard its edits, then rerun "
                                f"`pull --merge {relpath}`")
        marker = _merge_base_marker(copy_path)
        merged_against = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
        copies[relpath] = (copy_path, text, merged_against)

    def apply(lock: dict):
        files = lock.get("files", {})
        for relpath in relpaths:
            if relpath not in files:
                raise BaselineError(f"not a row: {relpath}")
            upstream = upstream_text(source, relpath, lock.get("substitutions", {}))
            if upstream is None:
                raise _missing_error(relpath)
            upstream_hash = sha(upstream.encode("utf-8"))
            if upstream_hash != copies[relpath][2]:
                raise BaselineError(f"upstream moved since the merge copy of {relpath} was written; rerun "
                                    f"`pull --merge {relpath}` to merge the current upstream")
            files[relpath]["hash"] = upstream_hash
        return relpaths

    mutate_lock(root, apply)
    for relpath, (copy_path, text, _base) in copies.items():
        _write_lf(root / relpath, text)
        copy_path.unlink()
        _merge_base_marker(copy_path).unlink(missing_ok=True)
        print(f"resolved: {relpath}")
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


_CORE_REGION = re.compile(
    r"<!-- ===== BASELINE:core BEGIN.*?BASELINE:core END ===== -->\n?", re.DOTALL)


def _migrate_core_region(root: Path, layers: list[str]) -> str | None:
    """A schema-1 `CLAUDE.md` carried shared doctrine inline between BASELINE:core markers; schema 2
    carries it in the layer files. Replace the region with their imports; `None` when absent."""
    path = root / ".claude" / "CLAUDE.md"
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except UnicodeDecodeError as exc:
        raise BaselineError(f"CLAUDE.md is not UTF-8 ({exc}); convert it before migrating") from exc
    match = _CORE_REGION.search(text)
    if match is None:
        return None
    imports = "".join(f"@{LAYER_FILE_BY_LAYER[layer]}\n" for layer in FULL_LAYERS if layer in layers)
    _write_lf(path, text[:match.start()] + imports + text[match.end():])
    return f"CLAUDE.md: BASELINE:core region ({match.group(0).count(chr(10))} lines) replaced by imports"


def _base_owned_hook_files(source: BaselineSource, base_text: str, subs: dict) -> set[str]:
    """Hook files base runs: those `base_text` (settings.base.json and the layer overlays) registers, plus manifest hooks a registered
    dispatcher names in its source. A shipped hook base neither registers nor runs is opt-in."""
    registered = set(baseline_compose._HOOK_COMMAND_PATH_PATTERN.findall(base_text))
    dispatcher_text = "\n".join(upstream_text(source, ".claude/hooks/" + name, subs) or "" for name in registered)
    named = {Path(relpath).name for relpath in _manifest_items_v2(source)
             if relpath.startswith(".claude/hooks/")
             and re.search(rf"\b{re.escape(Path(relpath).stem)}\b", dispatcher_text)}
    return registered | named


def _migrate_monolithic_settings(root: Path, source: BaselineSource, lock: dict, files: dict,
                                 layers: list[str]) -> list[str]:
    """A schema-1 `settings.json` is one hand-kept file. Write `settings.project.json` with only what
    the shared input (settings.base.json plus the adopted layers' overlays) does not already provide
    and drop the settings.json row: `pull` delivers the shared input and records both rows through
    `_seed_rows`, and `compose` rebuilds settings.json."""
    composed, base_input, project_input = SETTINGS_RELPATH, *WATCH_COMPOSED_INPUTS[SETTINGS_RELPATH]
    subs = lock.get("substitutions", {})
    full_text = local_text(root, composed)
    base_text = upstream_text(source, base_input, subs)
    if full_text is None or base_text is None:
        return []
    overlay_texts = [text for text in (upstream_text(source, f".claude/settings.{layer}.json", subs)
                                       for layer in layers) if text is not None]
    if (root / project_input).exists():
        status = files.get(composed, {}).get("status")
        if status == "composed":
            return []
        return [f"settings.json: NOT split, {project_input} already exists; settings.json stays {status} for triage"]
    try:
        base = baseline_compose.merge_shared_settings(
            json.loads(base_text), [json.loads(text) for text in overlay_texts])
        full = json.loads(full_text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"settings split: {composed} or an upstream settings input is unparseable JSON ({exc})") from exc
    project = baseline_compose.derive_project_settings(
        base, full, _base_owned_hook_files(source, "\n".join([base_text, *overlay_texts]), subs))
    _write_lf(root / project_input, json.dumps(project, indent=2, ensure_ascii=False) + "\n")
    files.pop(composed, None)
    adopted = baseline_compose.base_only_entries(base, full)
    return (["settings.json: split into settings.base.json (pulled) + settings.project.json (derived)"]
            + ([f"settings.json: {len(adopted)} entries adopted from settings.base.json:"] if adopted else [])
            + [f"  {line}" for line in adopted])


MIGRATE_REWRITES = (".claude/CLAUDE.md", ".claude/settings.json", ".claude/settings.project.json")


def _refuse_uncommitted(root: Path, relpaths) -> None:
    dirty = [rel for rel in relpaths if (root / rel).exists() and not _git_clean_path(root, rel)]
    if dirty:
        raise BaselineError("uncommitted changes in " + ", ".join(dirty) + ": commit or discard them first")


def v2_migrate(root: Path, source: BaselineSource, layers: list[str], abbreviations: list[str]) -> int:
    current = load_lock(root)
    if current.get("schema") == 2:
        print("no change (already v2)")
        return 0
    _refuse_uncommitted(root, MIGRATE_REWRITES)
    manifest_layers = _manifest_layers_v2(source, root)
    notes: list[str | None] = []

    def apply(lock: dict):
        if lock.get("schema") == 2:
            return NO_CHANGE
        notes.clear()
        files = {}
        current = _content_shas(root, list(lock.get("files", {})))
        for relpath, source_row in lock.get("files", {}).items():
            files[relpath] = _migrate_row(root, source_row, relpath, manifest_layers, current)
        core = ".claude/CLAUDE.core.md"
        # An absent core file stays unrowed so `pull` delivers it as a new path.
        if core not in files and _content_sha(root, core) is not None:
            core_hash = _upstream_sha(source, core, lock.get("substitutions", {}))
            files[core] = {
                "status": "tracked",
                "layer": manifest_layers.get(core),
                "hash": core_hash,
                "judged": None,
            }
        if ".claude/settings.json" in files:
            notes.extend(_migrate_monolithic_settings(root, source, lock, files, layers))
        notes.append(_migrate_core_region(root, layers))
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
        for note in filter(None, notes):
            print(note)
    return 0


def v2_paths(lock: dict, status_filter: str | None, verdict_filter: str | None,
              judged_since: str | None, as_json: bool, unshipped: bool = False, root: Path | None = None) -> int:
    """`unshipped` keeps rows whose current content is the judged content and not the baseline copy the
    lock records (`hash`), so a verdict an earlier publish left out stays listed however long ago it was
    judged; a verdict on older content is triage's, not publish's."""
    current = _content_shas(root, list(lock.get("files", {}))) if unshipped else {}
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
        if unshipped and (current.get(relpath) is None or judged.get("sha") != current[relpath]
                          or current[relpath] == entry.get("hash")):
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


def _compose_row_output(root: Path, relpath: str, inputs: list[str]) -> bytes:
    """Dispatch by relpath shape — the same two shapes `WATCH_COMPOSED_INPUTS` enumerates."""
    if relpath.endswith("settings.json"):
        if len(inputs) < 2:
            raise BaselineError(f"compose: {relpath} needs a base and a project input, got {inputs}")
        base_relpath, *overlay_relpaths, project_relpath = inputs
        texts = {}
        for input_relpath in (base_relpath, *overlay_relpaths, project_relpath):
            texts[input_relpath] = local_text(root, input_relpath)
            if texts[input_relpath] is None:
                raise BaselineError(f"compose: missing input {input_relpath} for {relpath}")
        try:
            base = json.loads(texts[base_relpath])
            overlays = [json.loads(texts[input_relpath]) for input_relpath in overlay_relpaths]
            project = json.loads(texts[project_relpath])
        except json.JSONDecodeError as exc:
            raise BaselineError(f"compose: {relpath} input is not valid JSON: {exc}") from exc
        hooks_dir = root / ".claude" / "hooks"
        try:
            merged = baseline_compose.compose_settings(base, project, hooks_dir, overlays)
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


def _drifted_outputs(root: Path, outputs: dict[str, bytes]) -> list[str]:
    drift = []
    for relpath, output in outputs.items():
        local = local_text(root, relpath)
        local_bytes = local.encode("utf-8") if local is not None else None
        if local_bytes != _lf(output):
            drift.append(relpath)
    return sorted(drift)


def v2_compose_template(root: Path, check: bool) -> int:
    """`compose --template`: render every composed output the baseline template ships from its shipped
    inputs, with every layer adopted. The template has no consumer lock, so the rows are
    `WATCH_COMPOSED_INPUTS` itself; an output the template does not ship is skipped."""
    outputs = {}
    for relpath, inputs in WATCH_COMPOSED_INPUTS.items():
        if not (root / relpath).is_file():
            continue
        if relpath.endswith("settings.json"):
            inputs = settings_composed_inputs(root, inputs, list(FULL_LAYERS))
        outputs[relpath] = _compose_row_output(root, relpath, inputs)
    drift = _drifted_outputs(root, outputs)
    if check:
        for relpath in drift:
            print(f"drift: {relpath}")
        if drift:
            return 1
        print("no drift")
        return 0
    for relpath in drift:
        _write_lf(root / relpath, outputs[relpath])
        print(f"composed: {relpath}")
    if not drift:
        print("no change")
    return 0


def v2_compose(root: Path, lock: dict, layers: list[str], check: bool) -> int:
    if not check:
        require_v2_lock(lock)
        require_v2_lock(load_lock(root))
    check_adaptation_contract(root)
    rows = _composed_rows(lock)
    outputs: dict[str, bytes] = {}
    inputs_by_relpath: dict[str, list[str]] = {}
    for relpath, entry in rows:
        inputs = list(entry.get("inputs") or [])
        if relpath.endswith("settings.json"):
            inputs = settings_composed_inputs(root, inputs, layers)
        inputs_by_relpath[relpath] = inputs
        outputs[relpath] = _compose_row_output(root, relpath, inputs)

    drift = _drifted_outputs(root, outputs)

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


def v2_sub(root: Path, sub_args: list[str], unset_args: list[str] | None = None) -> int:
    """Set substitution pairs on an existing lock.

    `init --sub` only runs at init time, and `init --force` rebuilds every row from the manifest,
    so this is the only route that adds a pair to a populated lock. A pair renames in both
    directions: `reverse_for` maps VALUE to PLACEHOLDER when publishing, `forward_for` maps it back
    when pulling or comparing, so a consumer keeps its own names while the baseline carries generic
    ones.
    """
    unset_args = list(unset_args or [])
    if not sub_args and not unset_args:
        raise UsageError("sub requires at least one --sub PLACEHOLDER=VALUE or --unset PLACEHOLDER")
    pairs = {}
    for item in sub_args:
        if "=" not in item:
            raise UsageError("sub requires PLACEHOLDER=VALUE")
        key, value = item.split("=", 1)
        if not key:
            raise UsageError("sub requires a non-empty PLACEHOLDER")
        pairs[key] = value

    def mutator(lock):
        substitutions = dict(lock.get("substitutions") or {})
        for key in unset_args:
            if key not in substitutions:
                raise BaselineError("no such substitution: %s" % key)
            del substitutions[key]
        substitutions.update(pairs)
        lock["substitutions"] = substitutions
        return None

    mutate_lock(root, mutator)
    for key in sorted(unset_args):
        print("substitution removed: %s" % key)
    for key, value in sorted(pairs.items()):
        print("substitution: %s -> %s" % (key, value))
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
            files.update(_seed_rows(root, relpath, item.get("layer"), files, layers))
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
    render_lane_agents(root)
    write_harness_ignore_block(root)
    return 0


LANE_TABLE_RELPATH = ".claude/guards/tool_profiles.json"
LANE_AGENTS_RELPATH = ".claude/agents"


def _lane_inputs(root: Path):
    """(tool_profiles module, table, MCP registrations) for a root that ships the shape table, else None."""
    table_path = root / LANE_TABLE_RELPATH
    if not table_path.is_file():
        return None
    import tool_profiles
    return tool_profiles, tool_profiles.load_table(str(table_path)), tool_profiles.load_registrations()


def render_lane_agents(root: Path) -> list[str]:
    """Render `.claude/agents/<agentType>.md` from the shape table (`tool_profiles.sync_agents`, the one
    renderer) and record each as a `local` row. A file and its row are written only when they differ."""
    try:
        inputs = _lane_inputs(root)
        if inputs is None:
            return []
        tool_profiles, table, candidates = inputs
        rendered = tool_profiles.render_agents(table, candidates)
        tool_profiles.sync_agents(write=True, table_path=str(root / LANE_TABLE_RELPATH),
                                  agents_dir=str(root / LANE_AGENTS_RELPATH), candidates=candidates)
    except (ValueError, OSError) as exc:
        print(f"warning: lane agents not rendered: {exc}", file=sys.stderr)
        return []
    shas = {f"{LANE_AGENTS_RELPATH}/{name}": sha(text.encode("utf-8")) for name, text in rendered.items()}

    def apply(lock: dict):
        files = lock.setdefault("files", {})
        changed = []
        for relpath, digest in sorted(shas.items()):
            entry = files.get(relpath)
            if entry is not None and (_entry_status(entry) != "local"
                                      or (entry.get("judged") or {}).get("sha") == digest):
                continue
            files[relpath] = {"status": "local", "layer": None,
                              "judged": _decision(root, relpath, "keep-local", sha_value=digest)}
            changed.append(relpath)
        return changed or NO_CHANGE

    result = mutate_lock(root, apply)
    return [] if result is NO_CHANGE else result


HARNESS_BLOCK_OPEN = "# >>> harness >>>"
HARNESS_BLOCK_CLOSE = "# <<< harness <<<"
HARNESS_BLOCK_RE = re.compile(re.escape(HARNESS_BLOCK_OPEN) + r"\n.*?" + re.escape(HARNESS_BLOCK_CLOSE) + r"\n?",
                              re.DOTALL)
GITIGNORE_ENTRIES_RELPATH = ".claude/gitignore.harness"
HOOKS_PATH = ".claude/githooks"


def gitignore_with_harness_block(text: str, entries: str) -> str:
    """`text` with the harness block holding `entries`: replaced in place between its markers, else appended once."""
    entries = entries.replace("\r\n", "\n")
    block = HARNESS_BLOCK_OPEN + "\n" + entries + ("" if entries.endswith("\n") else "\n") + HARNESS_BLOCK_CLOSE + "\n"
    text = text.replace("\r\n", "\n")
    if HARNESS_BLOCK_RE.search(text):
        return HARNESS_BLOCK_RE.sub(lambda _: block, text, count=1)
    return text + ("\n" if text and not text.endswith("\n\n") else "") + block


def write_harness_ignore_block(root: Path) -> bool:
    """Write the shipped `.claude/gitignore.harness` into the root .gitignore's harness block; True when it changed."""
    entries = local_text(root, GITIGNORE_ENTRIES_RELPATH)
    if entries is None:
        return False
    current = local_text(root, ".gitignore") or ""
    updated = gitignore_with_harness_block(current, entries)
    if updated == current:
        return False
    (root / ".gitignore").write_bytes(updated.encode("utf-8"))
    print("root .gitignore: harness block written")
    return True
SEED_TOKEN_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}|<[A-Za-z][^<>\n`]*[ :][^<>\n`]*>")
WORKLOG_RE = re.compile(r"`([^`\s]*Worklog\.md)`")
ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|~)")


def _doctor_row(item: str, status: str, detail: str, fix: str = "") -> dict:
    return {"item": item, "status": status, "detail": detail, "fix": fix}


def _doctor_run(root: Path, args: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 2, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _slashes(value: str) -> str:
    return value.replace("\\", "/")


def _doctor_substitutions(lock: dict) -> dict:
    missing = []
    checked = 0
    for key, value in sorted((lock.get("substitutions") or {}).items()):
        if isinstance(value, str) and ABS_PATH_RE.match(value):
            checked += 1
            if not Path(value).expanduser().exists():
                missing.append(f"{key}={value}")
    if missing:
        return _doctor_row("substitutions", "fail", "path does not exist: " + "; ".join(missing),
                           "baseline_sync.py sub --sub <PLACEHOLDER>=<existing path>, or create the directory")
    return _doctor_row("substitutions", "ok", f"{checked} path substitution(s) resolve")


def _doctor_seeds(root: Path, lock: dict) -> dict:
    hits = []
    for relpath, entry in sorted(lock.get("files", {}).items()):
        if _entry_status(entry) != "local" or entry.get("layer") not in FULL_LAYERS:
            continue
        text = local_text(root, relpath)
        found = sorted(set(SEED_TOKEN_RE.findall(text))) if text else []
        if found:
            hits.append(f"{relpath} ({len(found)})")
    if hits:
        return _doctor_row("seeds", "pending", "owner content still unfilled: " + "; ".join(hits),
                           "edit each named seed file, replacing its {{...}} tokens and <placeholder> lines")
    return _doctor_row("seeds", "ok", "no placeholder survives in a seed file")


def _doctor_gitignore(root: Path) -> dict:
    entries = local_text(root, GITIGNORE_ENTRIES_RELPATH)
    if entries is None:
        return _doctor_row("gitignore-block", "fail", f"{GITIGNORE_ENTRIES_RELPATH} is missing",
                           "python3 .claude/tools/baseline_sync.py pull")
    text = local_text(root, ".gitignore") or ""
    if not HARNESS_BLOCK_RE.search(text):
        detail = f"root .gitignore lacks the {HARNESS_BLOCK_OPEN} ... {HARNESS_BLOCK_CLOSE} block"
    elif gitignore_with_harness_block(text, entries) != text:
        detail = f"root .gitignore's harness block differs from {GITIGNORE_ENTRIES_RELPATH}"
    else:
        return _doctor_row("gitignore-block", "ok", "root .gitignore carries the current harness block")
    return _doctor_row("gitignore-block", "fail", detail,
                       "python3 .claude/tools/baseline_sync.py pull (rewrites the block)")


def _doctor_git(root: Path) -> list[dict]:
    hooks_fix = f"git config core.hooksPath {HOOKS_PATH}"
    rc, _ = _doctor_run(root, ["git", "rev-parse", "--git-dir"])
    if rc != 0:
        return [_doctor_row("git-repo", "fail", "not a git repository", "git init"),
                _doctor_row("git-hooks-path", "fail", "no repository", "git init, then " + hooks_fix),
                _doctor_gitignore(root)]
    rows = [_doctor_row("git-repo", "ok", "repository present")]
    rc, value = _doctor_run(root, ["git", "config", "--get", "core.hooksPath"])
    if not (root / HOOKS_PATH).is_dir():
        rows.append(_doctor_row("git-hooks-path", "ok", f"{HOOKS_PATH} not shipped in this profile"))
    elif rc == 0 and value == HOOKS_PATH:
        rows.append(_doctor_row("git-hooks-path", "ok", f"core.hooksPath = {HOOKS_PATH}"))
    else:
        rows.append(_doctor_row("git-hooks-path", "fail", f"core.hooksPath is {value or 'unset'}", hooks_fix))
    rows.append(_doctor_gitignore(root))
    return rows


def _doctor_worklog(root: Path, lock: dict) -> dict:
    # /worklog acts on the path its own command names; CLAUDE.md's Worklog Location is the older home.
    sources = (".claude/commands/worklog.md", ".claude/CLAUDE.md")
    match = next(filter(None, (WORKLOG_RE.search(local_text(root, rel) or "") for rel in sources)), None)
    if not match:
        return _doctor_row("worklog", "fail", " and ".join(sources) + " name no Worklog.md path",
                           "name the vault Worklog.md path in .claude/commands/worklog.md")
    named = match.group(1)
    vault = (lock.get("substitutions") or {}).get("{{VAULT_ROOT}}")
    if ABS_PATH_RE.match(named):
        target = Path(named).expanduser()
    elif vault:
        target = Path(vault) / named
    else:
        return _doctor_row("worklog", "fail", f"{named} is vault-relative and the lock has no VAULT_ROOT",
                           "baseline_sync.py sub --sub {{VAULT_ROOT}}=<vault path>")
    if target.is_file():
        return _doctor_row("worklog", "ok", _slashes(str(target)))
    return _doctor_row("worklog", "fail", f"missing: {_slashes(str(target))}",
                       "create it with a '# Worklog' heading, or rerun bootstrap.sh")


def _doctor_script(root: Path, item: str, script: str, args: list[str], ok_detail: str) -> dict:
    path = root / ".claude" / "tools" / script
    if not path.is_file():
        return _doctor_row(item, "ok", f"{script} not shipped in this profile")
    rc, out = _doctor_run(root, [sys.executable, str(path), *args])
    if rc == 0:
        return _doctor_row(item, "ok", ok_detail)
    lines = [line for line in out.splitlines() if line.strip()]
    return _doctor_row(item, "fail", f"exit {rc}: " + " | ".join(lines[:3]),
                       f"python3 .claude/tools/{script} {' '.join(args)}")


def _norm_path(value: str) -> str:
    value = _slashes(value).rstrip("/")
    return re.sub(r"^([A-Za-z]):", lambda m: m.group(1).lower() + ":", value)


def expected_auto_memory_dir(root: Path) -> str:
    """`<main checkout root>/.claude/auto-memory`: the parent of the git common dir, so a linked
    worktree names the one shared store (`commands/workstation_setup.md` Phase 2 step 1)."""
    rc, common = _doctor_run(root, ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    base = Path(common).parent if rc == 0 and common else root
    return _slashes(str(base.resolve())) + "/.claude/auto-memory"


def _doctor_auto_memory(root: Path) -> dict:
    item, expected = "auto-memory-dir", expected_auto_memory_dir(root)
    fix = f'set "autoMemoryDirectory": "{expected}" in .claude/settings.local.json'
    path = root / ".claude" / "settings.local.json"
    if not path.is_file():
        return _doctor_row(item, "fail", "settings.local.json is missing", fix)
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("autoMemoryDirectory")
    except (OSError, ValueError, AttributeError) as exc:
        return _doctor_row(item, "fail", f"settings.local.json is unreadable: {exc}", "repair the JSON by hand")
    if not isinstance(value, str) or _norm_path(value) != _norm_path(expected):
        return _doctor_row(item, "fail", f"autoMemoryDirectory is {value!r}, expected {expected}", fix)
    rc, _ = _doctor_run(root, ["git", "check-ignore", "-q", ".claude/settings.local.json"])
    if rc != 0:
        return _doctor_row(item, "fail", "settings.local.json is not gitignored: it would commit a machine path",
                           "add .claude/settings.local.json to the root .gitignore")
    if not (Path(expected) / "MEMORY.md").is_file():
        return _doctor_row(item, "fail", f"{expected}/MEMORY.md is missing", "pull the auto-memory seed")
    return _doctor_row(item, "ok", expected)


def _doctor_lane_agents(root: Path, lock: dict) -> dict:
    item = "lane-agents"
    fix = ("python3 .claude/tools/tool_profiles.py agents --write, or baseline_sync.py pull "
           "(renders them and records the local rows)")
    try:
        inputs = _lane_inputs(root)
        if inputs is None:
            return _doctor_row(item, "ok", f"{LANE_TABLE_RELPATH} not shipped in this profile")
        tool_profiles, table, candidates = inputs
        names = sorted(tool_profiles.render_agents(table, candidates))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            drift = tool_profiles.sync_agents(check=True, table_path=str(root / LANE_TABLE_RELPATH),
                                              agents_dir=str(root / LANE_AGENTS_RELPATH), candidates=candidates)
    except (ValueError, OSError) as exc:
        return _doctor_row(item, "fail", f"cannot render: {exc}", fix)
    if drift:
        return _doctor_row(item, "fail", "; ".join(err.getvalue().strip().splitlines()[:3]), fix)
    unrowed = [n for n in names if f"{LANE_AGENTS_RELPATH}/{n}" not in lock.get("files", {})]
    if unrowed:
        return _doctor_row(item, "fail", "no lock row for " + ", ".join(unrowed), fix)
    return _doctor_row(item, "ok", f"{len(names)} lane agent file(s) current with local rows")


def _doctor_delivered_ignored(root: Path, lock: dict) -> dict:
    delivered = sorted(relpath for relpath, entry in lock["files"].items()
                       if entry.get("status") in ("tracked", "composed") and (root / relpath).is_file())
    hidden = _ignored_untracked(root, delivered)
    if not hidden:
        return _doctor_row("delivered-ignored", "ok", "every delivered file a .gitignore rule matches is tracked")
    return _doctor_row("delivered-ignored", "fail",
                       f"{len(hidden)} delivered file(s) are untracked and ignored, so a commit drops them: "
                       + ", ".join(hidden),
                       "git add -f " + " ".join(hidden))


def doctor_rows(root: Path, lock: dict) -> list[dict]:
    return [_doctor_substitutions(lock), _doctor_seeds(root, lock), *_doctor_git(root),
            _doctor_delivered_ignored(root, lock),
            _doctor_worklog(root, lock),
            _doctor_script(root, "user-scope", "user_scope.py", ["check", "--repo", str(root)],
                           "user_scope.py check is green"),
            _doctor_script(root, "lens-registry", "lens.py", ["index"], "every registered lens resolves"),
            _doctor_auto_memory(root), _doctor_lane_agents(root, lock)]


def cmd_doctor(root: Path, lock: dict, as_json: bool) -> int:
    """What adoption left undone, one row per item. Exit 1 on any `fail`; a `pending` row is owner
    content (seed files to fill in) and does not fail."""
    rows = doctor_rows(root, lock)
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"[{row['status']}] {row['item']}: {row['detail']}")
            if row["status"] != "ok":
                print(f"        fix: {row['fix']}")
    return 1 if any(row["status"] == "fail" for row in rows) else 0


AUTHOR_WORKTREES_RELPATH = ".claude/.cache/baseline-worktrees"


def cmd_author_start(root: Path) -> int:
    """§5/§7 `author start`: create or reuse this session's baseline author worktree."""
    lock = load_lock(root)
    require_v2_lock(lock)
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
    if args.carry is not None:
        if args.op != "judge":
            raise UsageError("--carry is only valid with judge")
        for flag, given in (("--verdict", args.verdict), ("--commit", args.commit),
                            ("--borderline", args.borderline), ("--confirm", args.confirm)):
            if given:
                raise UsageError(f"--carry cannot be combined with {flag}")
        parts = args.carry.split("..")
        if len(parts) != 2 or not parts[0] or not parts[1] or parts[1].startswith("."):
            raise UsageError("--carry must be <base>..<head>")
    elif args.op == "judge" and not args.verdict:
        raise UsageError("judge requires --verdict or --carry")
    if args.verdict is not None and args.op == "judge" and args.verdict not in VERDICTS:
        raise UsageError("--verdict must be push, keep-local or fork")
    if args.layer is not None:
        if args.op != "classify":
            raise UsageError("--layer is only valid with classify")
        if args.layer not in FULL_LAYERS:
            raise UsageError("--layer must be pure, coding or godot")
    if args.relayer and (args.op != "classify" or args.layer is None):
        raise UsageError("--relayer is only valid with classify --layer")
    if args.accept_hit and args.op != "judge":
        raise UsageError("--accept-hit is only valid with judge")
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
    if args.template and args.op != "compose":
        raise UsageError("--template is only valid with compose")
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


def v2_upgrade(project: str, baseline_dir: str | None, layers_arg: str | None,
               abbreviations: list[str]) -> int:
    """Bring a schema-1 consumer to schema 2 in one run from a baseline checkout's engine: migrate,
    pull (a manifest `sync: offer` path, such as another project's memory, stays offered), compose,
    then report `check --strict`.
    Rows that need a verdict are reported, never forced; `/sync_baseline` triage clears them."""
    root = Path(project).resolve()
    if not (root / ".claude").is_dir():
        raise UsageError(f"--project has no .claude/ directory: {root}")
    lock = load_lock(root)
    if lock.get("schema") == 2:
        raise BaselineError("already schema 2: use /sync_baseline")
    _refuse_uncommitted(root, MIGRATE_REWRITES + (LOCK_RELPATH,))
    # compose enforces the adaptation contract after migrate and pull have written; a file the
    # project already has must pass it before the first write, or a failure strands the upgrade.
    check_adaptation_contract(root, missing_ok=True)
    layers = resolve_layers_v2({}, layers_arg)
    source = ensure_baseline(lock, root, baseline_dir)
    try:
        v2_migrate(root, source, layers, abbreviations)
        v2_pull(root, source, [], layers, force=False)
        v2_compose(root, load_lock(root), layers, check=False)
        return v2_check(root, load_lock(root), source, as_json=False, layers=layers, strict=True)
    finally:
        source.close()


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
        try:
            return cmd_author_start(project_root())
        except BaselineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if raw_argv[:1] == ["materialize"]:
        print("error: materialize is retired; use publish --dry-run", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=[
        "check", "diff", "pull", "update-lock", "resolve", "migrate", "upgrade",
        "classify", "judge", "triage", "forget", "gc", "fork", "track",
        "ignore", "candidates", "paths", "init", "compose", "sub", "doctor",
    ])
    ap.add_argument("relpaths", nargs="*")
    ap.add_argument("--baseline-dir")
    ap.add_argument("--layers", metavar="PURE,CODING,GODOT")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--merge", action="store_true",
                    help="pull: 3-way merge each diverged row; a conflict leaves the row and prints a marker copy")
    ap.add_argument("--status")
    ap.add_argument("--from", dest="from_relpath")
    ap.add_argument("--layer")
    ap.add_argument("--relayer", action="store_true")
    ap.add_argument("--accept-hit", action="append", default=[])
    ap.add_argument("--inputs")
    ap.add_argument("--verdict")
    ap.add_argument("--borderline", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--judged-since")
    ap.add_argument("--unshipped", action="store_true")
    ap.add_argument("--commit")
    ap.add_argument("--carry", metavar="BASE..HEAD")
    ap.add_argument("--repo")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--sub", action="append", default=[], metavar="PLACEHOLDER=VALUE")
    ap.add_argument("--unset", action="append", default=[], metavar="PLACEHOLDER")
    ap.add_argument("--abbrev", action="append", default=[])
    ap.add_argument("--project")
    try:
        args = ap.parse_intermixed_args(argv)
        inputs = _validate_cli_usage(args)
        if args.op == "upgrade":
            if not args.project or args.relpaths:
                raise UsageError("upgrade takes --project <path> and no relpaths")
            return v2_upgrade(args.project, args.baseline_dir, args.layers, args.abbrev)
        root = project_root()
        if args.op == "compose" and args.template:
            return v2_compose_template(root, args.check)
        if args.op == "doctor":
            return cmd_doctor(root, load_lock(root), args.json)
        if args.op == "init":
            if (root / LOCK_RELPATH).exists():
                require_v2_lock(load_lock(root))
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
        writes = args.op in {
            "pull", "update-lock", "resolve", "classify", "judge", "forget", "fork", "track", "ignore",
        } or (args.op == "gc" and args.apply) or (
            args.op == "compose" and not args.check
        ) or (args.op == "sub" and bool(args.sub or args.unset))
        if writes:
            require_v2_lock(lock)
        layers = resolve_layers_v2(lock, args.layers)
        if args.op == "paths":
            return v2_paths(lock, args.status, args.verdict, args.judged_since, args.json, args.unshipped, root)
        if args.op == "gc":
            return v2_gc(root, args.apply)
        if args.op == "forget":
            return v2_forget(root, args.relpaths, args.force)
        if args.op == "classify":
            return v2_classify(root, args.relpaths, args.status, args.from_relpath, inputs,
                                args.force, args.baseline_dir, args.layer, args.relayer)
        if args.op == "ignore":
            return v2_classify(root, args.relpaths, "local", None, None, args.force, args.baseline_dir)
        if args.op == "judge" and args.carry:
            return v2_carry(root, args.relpaths, args.carry, args.baseline_dir, args.accept_hit)
        if args.op == "judge":
            return v2_judge(root, args.relpaths, args.verdict, args.borderline, args.confirm, args.force,
                            args.commit, args.baseline_dir, args.accept_hit)
        if args.op == "sub":
            return v2_sub(root, args.sub, args.unset)
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
                engine = _pending_engine_rows(root, source, lock, layers) if not args.relpaths else []
                if not engine:
                    return v2_pull(root, source, args.relpaths, layers, args.force, args.merge)
                engine_code = v2_pull(root, source, engine, layers, False)
                source.close()
                source = None
                if engine_code != 0:
                    return engine_code
                print("engine updated; the rest of the pull runs on it")
                return subprocess.run([sys.executable, str(root / ENGINE_RELPATH), *raw_argv], cwd=root,
                                      env=dict(os.environ, **{ENGINE_UPDATED_ENV: "1"})).returncode
            if args.op == "migrate":
                return v2_migrate(root, source, resolve_layers_v2({}, args.layers), args.abbrev)
            if args.op == "triage":
                return v2_triage(root, lock, source, args.batch, args.json, args.baseline_dir)
            if args.op == "fork":
                return v2_fork(root, args.relpaths, source)
            if args.op == "track":
                return v2_track(root, args.relpaths, source)
            if args.op == "update-lock":
                return v2_update_lock(root, source, args.relpaths, layers)
            if args.op == "resolve":
                return v2_resolve(root, source, args.relpaths)
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
