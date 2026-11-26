"""Throwaway git repositories for proofs, built without spawning git.

A fixture repository costs `git init`, `git add -A` and `git commit` (three spawns) and a proof builds one or more
per case. `init_repo` copies the layout one real `git init` wrote and
`commit_all` writes the blobs, trees, commit, branch ref and index for the working tree in this process, so a case
pays no spawn for its fixture and only the subject's own git reads remain.

`commit_all` takes its real-git fallback (`git add -A`, `git commit`) for every tree whose result git decides, not
this module: a `.gitignore` or `.gitattributes`, a nested repository, a symlink, a CR in a file, a linked
worktree, a branch held only in `packed-refs`, an index written by something other than this module or git's
default version 2. `test_git_fixture.py` proves both paths write what `git add -A && git commit` writes.

`built` is the one build-once helper: any proof's template tree (a seeded repository, a planted checkout) is built through
it and copied per case, and the skeleton `init_repo` copies is the first of them. `warm()` pre-builds that skeleton."""
import atexit
import contextlib
import hashlib
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

_IDENTITY = "fixture <fixture@example.invalid>"
# gc and maintenance off: every commit otherwise spawns `maintenance run --auto` (~20 ms) that never has work here.
_CONFIG = ("[user]\n\temail = fixture@example.invalid\n\tname = fixture\n"
           "[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n")
_EMPTY_TREE = hashlib.sha1(b"tree 0\0").hexdigest()


def _repo_local_env():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from harness_tests import GIT_REPO_ENV
    return GIT_REPO_ENV


def git(cwd, *args):
    """`git <args>` in `cwd` (the process's cwd when None), its stdout as bytes; a failure raises. The repo-local
    variables of a caller committing from a temporary index are dropped, and git stops looking for a repository at the
    folder above `cwd`, so a fixture never reaches the checkout the proof runs in."""
    env = os.environ.copy()
    for key in _repo_local_env():
        env.pop(key, None)
    env["GIT_CEILING_DIRECTORIES"] = str((Path(cwd) if cwd else Path.cwd()).parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, check=True).stdout


def built(name, build):
    """The folder `build(folder)` filled, as a `Path`: built once per run of the proof and removed at exit. A case copies
    it (`_hook_call.copy_repo`) instead of paying the build again, so no commit or ref crosses cases. The folder is
    published in the environment under a key made from `name`, so the case processes a pool spawns afterwards, and a
    sharded run's children, find it; a proof's `main` calls `built` for each tree before it starts them, and a case
    run alone builds on first use. `name` is `<proof>.<what>`: it is the key, and a pytest worker holds every file's keys."""
    key = "HARNESS_BUILT_" + re.sub(r"\W", "_", name).upper()
    folder = os.environ.get(key)
    if not folder or not os.path.isdir(folder):
        folder = tempfile.mkdtemp(prefix="built_" + re.sub(r"\W", "_", name) + "_")
        atexit.register(remove, folder)
        os.environ[key] = folder
        build(Path(folder))
    return Path(folder)


def _build_skeleton(root):
    empty = root / "template"  # no sample hooks, no description: nothing a fixture reads
    empty.mkdir()
    git(root, "init", "-q", "-b", "main", "--template=" + str(empty), str(root / "work"))
    git(root, "init", "-q", "--bare", "-b", "main", "--template=" + str(empty), str(root / "bare"))


def warm():
    """The skeleton folder holding the two layouts `init_repo` copies (one `git init` each), built once."""
    return built("git_fixture.skeleton", _build_skeleton)


def init_repo(path, *, bare=False, branch="main"):
    """`git init -q -b <branch>` at `path` plus the fixture identity (a bare repository gets none), no spawn."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git_dir = path if bare else path / ".git"
    shutil.copytree(warm() / ("bare" if bare else "work/.git"), git_dir, dirs_exist_ok=True)
    if branch != "main":
        (git_dir / "HEAD").write_text("ref: refs/heads/%s\n" % branch, encoding="utf-8", newline="\n")
    if not bare:
        with open(git_dir / "config", "a", encoding="utf-8", newline="\n") as handle:
            handle.write(_CONFIG)


def _make_writable(path):
    """Owner read/write/execute on `path` and below: git leaves loose objects and packs read-only."""
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            try:
                os.chmod(os.path.join(current, name), stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass


def remove(path):
    """Delete `path` and everything below it, read-only git files included."""
    if os.path.exists(path):
        _make_writable(path)
        shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def scratch(prefix="baseline_sync_"):
    """A temporary folder (a `Path`) removed on exit."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        remove(path)


def bare_remote(path):
    """An empty bare repository `remote.git` under `path`."""
    remote = Path(path) / "remote.git"
    init_repo(remote, bare=True)
    return remote


def push_main(work, remote):
    """Publish `work`'s one-branch history to the empty bare `remote` and name it `origin`. The objects and the branch
    ref are copied in place of a `git push`: the result is the same repository state, and the push spawn was the
    most expensive step of every baseline fixture."""
    shutil.copytree(Path(work) / ".git" / "objects", Path(remote) / "objects", dirs_exist_ok=True)
    (Path(remote) / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (Path(remote) / "refs" / "heads" / "main").write_text(branch_sha(work) + "\n", encoding="utf-8", newline="\n")
    with open(Path(work) / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        handle.write('[remote "origin"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                     % Path(remote).as_uri())


def branch_sha(path):
    """HEAD's commit id: the loose branch ref read in place of a `rev-parse` spawn when there is one."""
    path = Path(path)
    if (path / ".git").is_dir():  # a linked worktree's `.git` is a file; it takes the spawn below
        head = (path / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        ref = path / ".git" / head[5:] if head.startswith("ref: ") else None
        if ref is not None and ref.is_file():
            return ref.read_text(encoding="utf-8").strip()
    return git(path, "rev-parse", "HEAD").decode().strip()


def commit_all(path, message):
    """`git add -A && git commit -q -m <message>` in `path`; returns the new commit id."""
    path = Path(path)
    if not _commit_in_process(path, message):
        commit_with_git(path, message)
    return branch_sha(path)


def history(repo, *steps, ref=None, root=False, force=False):
    """Commit each step, `(files, subject[, when])`, with one `git fast-import` and check the tree out: the dated commits
    `commit_all` cannot write (it stamps the clock), for two spawns in all where each commit cost three. `files` maps a
    path to its text, None deleting it; `when` is a datetime or epoch seconds (now when absent). `repo` is a work tree
    or a bare repository; the commits land on `ref` (default: the branch HEAD names) on top of its tip, or as a
    parentless history with `root`, replacing the ref with `force`. Returns the commit ids in order."""
    repo = Path(repo)
    git_dir = repo / ".git" if (repo / ".git").is_dir() else repo
    ref = ref or (git_dir / "HEAD").read_text(encoding="utf-8").split("ref:", 1)[1].strip()
    marks = git_dir / "history.marks"
    stream = []
    for number, (files, subject, *when) in enumerate(steps, 1):
        moment = when[0] if when and when[0] is not None else time.time()
        stamp = int(moment if isinstance(moment, (int, float)) else moment.timestamp())
        message = subject.encode("utf-8")
        stream.append(b"commit %s\nmark :%d\ncommitter t <t@example.invalid> %d +0000\ndata %d\n%s\n"
                      % (ref.encode(), number, stamp, len(message), message))
        if number == 1 and not root and (git_dir / Path(*ref.split("/"))).exists():
            stream.append(b"from %s^0\n" % ref.encode())
        for path, text in files.items():
            if text is None:
                stream.append(b"D %s\n" % path.encode())
            else:
                data = text.encode("utf-8")
                stream.append(b"M 100644 inline %s\ndata %d\n%s\n" % (path.encode(), len(data), data))
    command = ["fast-import", "--quiet", "--export-marks=" + str(marks)] + (["--force"] if force else [])
    done = subprocess.run(["git", "--git-dir", str(git_dir), *command], input=b"".join(stream), capture_output=True)
    if done.returncode != 0:
        raise AssertionError("git fast-import: %s" % done.stderr.decode("utf-8", "replace").strip())
    if git_dir != repo:
        git(repo, "reset", "-q", "--hard")
    ids = dict(line.split()[:2] for line in marks.read_text(encoding="utf-8").splitlines() if line.strip())
    marks.unlink()
    return [ids[":%d" % n] for n in range(1, len(steps) + 1)]


def commit_with_git(path, message):
    """The same commit made by git itself: the fallback, and the proof's reference."""
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", message)
    return branch_sha(path)


# --- the in-process commit -------------------------------------------------------------------------------------------

class _Unusual(Exception):
    """The tree is one git, not this module, must decide."""


def _object(git_dir, kind, data):
    body = b"%s %d\0" % (kind, len(data)) + data
    sha = hashlib.sha1(body).digest()
    hexsha = sha.hex()
    target = git_dir / "objects" / hexsha[:2] / hexsha[2:]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zlib.compress(body, 1))
    return sha


def _index_modes(git_dir):
    """{path: mode} of the index this module or git's version 2 wrote, {} when there is none."""
    index = git_dir / "index"
    if not index.is_file():
        return {}
    data = index.read_bytes()
    signature, version, count = struct.unpack(">4sII", data[:12])
    if signature != b"DIRC" or version != 2:
        raise _Unusual("index version")
    modes, offset = {}, 12
    for _ in range(count):
        mode = struct.unpack(">I", data[offset + 24:offset + 28])[0]
        name = data[offset + 62:data.index(b"\0", offset + 62)]
        modes[name.decode("utf-8")] = mode
        offset += (62 + len(name) + 8) & ~7
    return modes


def _files(path, git_dir):
    """[(relative posix path, stat result, bytes)] of the working tree `git add -A` would stage."""
    found, pending = [], [path]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name in (".gitignore", ".gitattributes") or entry.is_symlink():
                    raise _Unusual(entry.name)
                if entry.name == ".git":
                    if folder != path or not entry.is_dir(follow_symlinks=False):
                        raise _Unusual("nested repository")
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                    continue
                data = Path(entry.path).read_bytes()
                if b"\r" in data:
                    raise _Unusual("CR")
                found.append((Path(entry.path).relative_to(path).as_posix(), entry.stat(follow_symlinks=False), data))
    exclude = git_dir / "info" / "exclude"
    if exclude.is_file() and any(line.strip() and not line.startswith("#")
                                 for line in exclude.read_text(encoding="utf-8").splitlines()):
        raise _Unusual("exclude")
    return sorted(found)


def _tree(git_dir, entries, prefix=""):
    """The tree object id of `entries` [(path, mode, sha)] below `prefix`."""
    rows, subtrees = {}, {}
    for rel, mode, sha in entries:
        name, _, rest = rel[len(prefix):].partition("/")
        if rest:
            subtrees.setdefault(name, []).append((rel, mode, sha))
        else:
            rows[name] = (b"%o" % mode, sha)
    for name, members in subtrees.items():
        rows[name] = (b"40000", _tree(git_dir, members, prefix + name + "/"))
    # git orders a tree's entries by name, a folder as if its name ended in `/`
    ordered = sorted(rows, key=lambda n: n.encode("utf-8") + (b"/" if n in subtrees else b""))
    body = b"".join(rows[n][0] + b" " + n.encode("utf-8") + b"\0" + rows[n][1] for n in ordered)
    return _object(git_dir, b"tree", body)


def _index(entries, stats):
    rows = b""
    for rel, mode, sha in entries:
        st = stats[rel]
        name = rel.encode("utf-8")
        ident = (0, 0, 0, 0) if os.name == "nt" else (st.st_dev & 0xFFFFFFFF, st.st_ino & 0xFFFFFFFF, st.st_uid, st.st_gid)
        row = struct.pack(">10I", st.st_ctime_ns // 10**9, st.st_ctime_ns % 10**9, st.st_mtime_ns // 10**9,
                          st.st_mtime_ns % 10**9, ident[0], ident[1], mode, ident[2], ident[3], st.st_size & 0xFFFFFFFF)
        row += sha + struct.pack(">H", min(len(name), 0xFFF)) + name
        rows += row + b"\0" * ((8 - len(row) % 8) or 8)
    body = struct.pack(">4sII", b"DIRC", 2, len(entries)) + rows
    return body + hashlib.sha1(body).digest()


def _parent_tree(path, commit):
    """The tree id of `commit`: read from its loose object, from one `git rev-parse` when it is packed."""
    loose = path / ".git" / "objects" / commit[:2] / commit[2:]
    if loose.is_file():
        return zlib.decompress(loose.read_bytes()).partition(b"\0")[2].split(b"\n", 1)[0][5:].decode()
    return git(path, "rev-parse", commit + "^{tree}").decode().strip()


def _commit_in_process(path, message):
    """True when the commit is written; False when git must decide: a tree it must stage, or a tree equal to the
    parent's (an empty root included), which `git commit` refuses and the fallback then raises. No ref or index is
    changed in that case."""
    git_dir = path / ".git"
    try:
        if not git_dir.is_dir():
            raise _Unusual("linked worktree")
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: refs/heads/"):
            raise _Unusual("detached")
        ref = git_dir / head[5:]
        parent = ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
        packed = git_dir / "packed-refs"
        if parent is None and packed.is_file() and head[5:] in packed.read_text(encoding="utf-8"):
            raise _Unusual("packed ref")
        modes_from_index = "filemode = false" in (git_dir / "config").read_text(encoding="utf-8")
        held = _index_modes(git_dir) if modes_from_index else {}
        files = _files(path, git_dir)
    except _Unusual:
        return False
    entries, stats = [], {}
    for rel, st, data in files:
        if modes_from_index:
            mode = held.get(rel, 0o100644)
            mode = 0o100755 if mode == 0o100755 else 0o100644
        else:
            mode = 0o100755 if st.st_mode & stat.S_IXUSR else 0o100644
        entries.append((rel, mode, _object(git_dir, b"blob", data)))
        stats[rel] = st
    tree = _tree(git_dir, entries).hex()
    if tree == (_parent_tree(path, parent) if parent else _EMPTY_TREE):
        return False
    stamp = "%d +0000" % int(time.time())
    text = "tree %s\n%s" % (tree, "parent %s\n" % parent if parent else "")
    text += "author %s %s\ncommitter %s %s\n\n%s\n" % (_IDENTITY, stamp, _IDENTITY, stamp, message)
    commit = _object(git_dir, b"commit", text.encode("utf-8")).hex()
    (git_dir / "index").write_bytes(_index(entries, stats))
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(commit + "\n", encoding="utf-8", newline="\n")
    return True
