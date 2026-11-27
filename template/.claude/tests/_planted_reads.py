"""Shared proof helper: the repository reads of a plain fixture repo, answered in process.

A review proof builds repositories by writing loose objects and refs (`write_object`), then drives a tool that asks git
about them. Each question here is decided by those planted facts, so a proof that is not about git's own answer skips
the process. A name or shape this module does not fully understand falls through to the real read (`NotPlanted`), and
`PlantedReadParityTests` (test_review_ledger.py) runs every answer beside real git's, so a plant cannot drift from the read it stands in for.

    write_object(root, kind, data)   the loose object `git hash-object -w` writes, as its id
    blob_id(data)                    the id `git hash-object` gives raw bytes
    Planted(root).ask(verb, name)    `review_snapshot._Objects.ask`'s answer for a `cat-file --batch-command` question
    planted_reads(review_ledger)     the tool-level reads a fixture repo's own facts decide, as override tables
    planted(review_ledger)           those overrides applied for a `with` block, for a proof that drives a caller
"""
import contextlib
import hashlib
import re
import sys
import zlib
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}")
_NAME = re.compile(r"[A-Za-z0-9_./-]+")


class NotPlanted(Exception):
    """The question is outside what the planted facts decide; the caller asks real git."""


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def write_object(root: Path, kind: str, data: bytes) -> str:
    body = b"%s %d\0" % (kind.encode(), len(data)) + data
    sha = hashlib.sha1(body).hexdigest()
    path = Path(root) / ".git" / "objects" / sha[:2] / sha[2:]
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(zlib.compress(body))
    return sha


def is_plain(root) -> bool:
    """A repository with its own `.git` folder: no submodule checkout, no linked worktree."""
    return (Path(root) / ".git").is_dir()


class Planted:
    """A plain repository's loose objects and refs, read as `git cat-file --batch-command` answers them."""

    def __init__(self, root):
        self.git = Path(root) / ".git"

    def packed(self):
        objects = self.git / "objects"
        return (objects / "info" / "alternates").exists() or any((objects / "pack").glob("*.pack"))

    def _object(self, sha):
        path = self.git / "objects" / sha[:2] / sha[2:]
        if not path.is_file():
            if self.packed():  # the object may live in a pack this plant does not read
                raise NotPlanted(sha)
            return None
        head, _, data = zlib.decompress(path.read_bytes()).partition(b"\0")
        return head.split()[0].decode(), data

    def _ref(self, name, depth=0):
        """The id `name` points at, or None; a name that could be an abbreviated id or a path is not planted."""
        if depth > 5 or not _NAME.fullmatch(name) or ".." in name:
            raise NotPlanted(name)
        for prefix in ("", "refs/", "refs/tags/", "refs/heads/", "refs/remotes/"):
            path = self.git / (prefix + name)
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text.startswith("ref: "):
                    return self._ref(text[5:], depth + 1)
                if not _SHA.fullmatch(text):
                    raise NotPlanted(name)
                return text
        if (self.git / "packed-refs").exists() or re.fullmatch(r"[0-9a-f]{4,39}", name):
            raise NotPlanted(name)  # a ref may be packed; a short hex may be an abbreviated id
        return None

    def _base(self, token):
        return token if _SHA.fullmatch(token) else self._ref(token)

    def _tree_entry(self, tree, path):
        sha = tree
        for part in path.split("/"):
            found = self._object(sha)
            if found is None or found[0] != "tree":
                return None
            data, sha = found[1], None
            while data:
                head, _, rest = data.partition(b"\0")
                mode, _, name = head.partition(b" ")
                if name.decode("utf-8") == part:
                    sha = rest[:20].hex()
                    break
                data = rest[20:]
            if sha is None:
                return None
        return sha

    def resolve(self, name):
        """The id `name` names, or None when it names nothing here; NotPlanted for a form not understood."""
        path = None
        if ":" in name:
            name, _, path = name.partition(":")
            if not path.startswith("./") or len(path) < 3:
                raise NotPlanted(name)
            path = path[2:]
        peel = None
        match = re.fullmatch(r"(.+)\^\{(commit|blob)\}", name)
        if match:
            name, peel = match.group(1), match.group(2)
        parent = None
        match = re.fullmatch(r"(.+)\^(\d?)", name)
        if match:
            name, parent = match.group(1), int(match.group(2) or 1)
        sha = self._base(name)
        if sha is None:
            return None
        if parent is not None:
            found = self._object(sha)
            if found is None or found[0] != "commit":
                return None
            parents = [line[7:] for line in found[1].decode("utf-8").split("\n\n")[0].splitlines()
                       if line.startswith("parent ")]
            if parent < 1 or parent > len(parents):
                return None
            sha = parents[parent - 1]
        if peel is not None:
            found = self._object(sha)
            if found is None:
                return None
            if found[0] == "tag":
                raise NotPlanted(name)
            if found[0] != peel:
                return None
        if path is not None:
            found = self._object(sha)
            if found is None:
                return None
            if found[0] == "commit":
                tree = found[1].decode("utf-8").split("\n", 1)[0]
                if not tree.startswith("tree "):
                    raise NotPlanted(name)
                sha = self._tree_entry(tree[5:], path)
            elif found[0] == "tree":
                sha = self._tree_entry(sha, path)
            else:
                return None
        return sha

    def ask(self, verb, name):
        """`(oid, type, bytes or None)`, or None when git would name the object missing; NotPlanted when unsure."""
        sha = self.resolve(name)
        found = self._object(sha) if sha else None
        if found is None:
            return None
        if found[0] == "tag":
            raise NotPlanted(name)
        return sha, found[0], found[1] if verb == "contents" else None


def planted_reads(module):
    """`(module overrides, snapshot overrides, object-reader answer)` for the reads of a plain fixture repo that its
    own facts decide: the working tree's rigor (a fixture holds no task and no rigor default, so `normal`), the
    checkout layout, a worktree file's blob id, a rule's blob id and an object question. A root that is not a plain
    repo (a submodule checkout, a linked worktree) and a question the plant does not understand fall through to the
    real read. `PlantedReadParityTests` (test_review_ledger.py) runs each plant beside the real read."""
    snapshot = module.review_snapshot
    real_layout, real_hash, real_rule_blob = snapshot.layout, snapshot.hash_objects, module.rule_blob
    real_ask = snapshot._Objects.ask

    def layout(root):
        if not is_plain(root):
            return real_layout(root)
        git_dir = (Path(root) / ".git").as_posix()
        return snapshot._checkout_root.Layout(git_dir, git_dir, "", "")

    def hash_objects(root, paths, *, write=False):
        if not is_plain(root):
            return real_hash(root, paths, write=write)
        out = {}
        for path in list(paths):
            data = (Path(root) / path).read_bytes()
            if b"\r" in data:  # git hashes the checkout's clean filter (autocrlf) of the file, which a plant does not model
                return real_hash(root, paths, write=write)
            out[path] = write_object(Path(root), "blob", data) if write else blob_id(data)
        return out

    def rule_blob(repo, key, claude_dir=None, catalog=None):
        data = module.rule_text(key, claude_dir, catalog)
        if data is None or not is_plain(repo):
            return real_rule_blob(repo, key, claude_dir, catalog)
        return write_object(Path(repo), "blob", data)

    def ask(reader, verb, name):
        if is_plain(reader.root) and name.strip() and "\n" not in name:
            try:
                return Planted(reader.root).ask(verb, name)
            except NotPlanted:
                pass
        return real_ask(reader, verb, name)

    # A case that has already replaced the reader's `ask` (a failing reader) keeps its own.
    own = ask if real_ask.__qualname__ == "_Objects.ask" else None
    return ({"_rigor": lambda task_id, repo: "normal", "rule_blob": rule_blob},
            {"layout": layout, "hash_objects": hash_objects}, own)


@contextlib.contextmanager
def planted(module):
    """`planted_reads(module)` applied for the block: a proof that drives a tool calling `review_ledger` in process
    plants the same reads without owning the call."""
    overrides, snap_overrides, ask = planted_reads(module)
    snapshot = module.review_snapshot
    finding_bar = sys.modules.get("finding_bar")
    saved = [(module, name, getattr(module, name)) for name in overrides]
    saved += [(snapshot, name, getattr(snapshot, name)) for name in snap_overrides]
    saved.append((snapshot._Objects, "ask", snapshot._Objects.ask))
    if finding_bar is not None:
        saved.append((finding_bar, "run_rigor", finding_bar.run_rigor))
        finding_bar.run_rigor = lambda task_id, root, session_id=None: "normal"
    for name, value in overrides.items():
        setattr(module, name, value)
    for name, value in snap_overrides.items():
        setattr(snapshot, name, value)
    if ask:
        snapshot._Objects.ask = ask
    try:
        yield
    finally:
        for owner, name, value in saved:
            setattr(owner, name, value)
