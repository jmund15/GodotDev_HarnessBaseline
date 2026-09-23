#!/usr/bin/env python3
"""Layer closure: no shipped harness file depends on a file its layer's consumers do not receive.

The baseline ships in layers consumed as a prefix of pure -> coding -> godot. A file of layer X
reaches every consumer whose prefix holds X, and the smallest of those holds only X and the layers
below it. So a file closes when every file it cites, imports or executes is shipped at X or lower.

Dependencies read per file type:
- markdown: backticked `/command`, `skill`, `path/file.md` and `file.py` tokens, and relative
  `[text](path)` links resolved against the file's own directory, and memory slugs, bare or
  backticked, that name a file in the memory store or its archive;
- python: imports outside a `try` that catches ImportError, string literals naming a shipped
  `.py`/`.sh`/`.js`/`.ps1` file, and bare string literals naming a sibling module (a sub-hook chain
  imported by name). A file importing `_optional_hooks` loads by name through it, so its names count
  as optional;
- shell, javascript, powershell: shipped script paths outside comments.

A bare word is a citation only when it names a shipped or local skill, command or memory. A path-shaped
token under a `.claude/` directory that nothing ships and no file on disk holds is `missing`.
Runtime state the harness writes (GENERATED) is exempt.

    python3 .claude/tools/layer_closure.py --lock .claude/baseline.lock.json [--root .]
    python3 .claude/tools/layer_closure.py --manifest baseline.manifest.json --root template

Exit 0 closed, 1 findings, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LAYERS = ("pure", "coding", "godot")
ORDER = {name: rank for rank, name in enumerate(LAYERS)}
PREFIXES = tuple(LAYERS[:n] for n in range(1, len(LAYERS) + 1))
SHIPPED_STATUSES = {"tracked", "forked", "keep-tracked"}
CLAUDE_DIRS = {"agents", "auto-memory", "commands", "guards", "hooks", "reference", "rules", "schemas",
               "scripts", "skills", "templates", "tests", "tools", "workflows"}
SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".ps1")
DOC_SUFFIXES = (".md", ".json") + SCRIPT_SUFFIXES
# Paths relative to .claude/ that the harness writes at runtime; they exist in every consumer that
# runs the writer, so citing them is not a dangling reference.
GENERATED = (
    "baseline.lock.json", "settings.json", "settings.local.json", "worklog-pending.md",
    "pending_harness_edits.md", "orchestration_verdicts.json", "orchestration_candidates.json",
    "self_evaluate_archive.json", "logs/*", "scratch/*", ".cache/*", "cache/*", "worktrees/*",
    "sessions/*", "state/*", "generated/*", "reference/godot_class_index.md",
)
OPTIONAL_LOADER = "_optional_hooks"
PLACEHOLDERS = {"foo", "bar", "baz", "example", "slug", "name", "x", "y"}
SKIP_WALK = {".cache", "cache", "logs", "scratch", "worktrees", "sessions", "__pycache__", "plans"}


@dataclass(frozen=True)
class Finding:
    source: str
    kind: str  # needs-<layer> | local-only | missing
    target: str

    def render(self) -> str:
        return f"{self.source}: {self.kind} {self.target}"


def lock_layers(lock: dict) -> dict[str, str]:
    """relpath -> layer for every row the baseline ships to a consumer."""
    out = {}
    for rel, row in (lock.get("files") or {}).items():
        if row.get("status") in SHIPPED_STATUSES and row.get("layer") in ORDER:
            out[rel] = row["layer"]
    return out


def consumer_layers(lock: dict, manifest: dict | None) -> dict[str, str]:
    """What a consumer receives: the pinned baseline manifest (seed files included), overlaid by
    this project's shipped rows, whose lock layer is the intended one until they publish."""
    out = {}
    for rel, layer in manifest_layers(manifest or {}).items():
        out[rel if rel.startswith(".claude/") else ".claude/" + rel.split(".claude/", 1)[-1]] = layer
    out.update(lock_layers(lock))
    return out


def pinned_manifest(root: Path, lock: dict) -> dict | None:
    """The manifest at the lock's pinned baseline commit, read from the sync engine's cache clone."""
    import subprocess
    clone = Path(root) / ".claude" / ".cache" / "baseline-repo"
    sha = lock.get("synced_commit")
    if not sha or not clone.is_dir():
        return None
    shown = subprocess.run(["git", "-C", str(clone), "show", f"{sha}:baseline.manifest.json"],
                           capture_output=True, text=True, encoding="utf-8")
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def lock_checked(lock: dict) -> set[str]:
    """Rows whose shipped bytes are this project's bytes: a fork's local copy never ships."""
    return {rel for rel, row in (lock.get("files") or {}).items() if row.get("status") == "tracked"}


def manifest_layers(manifest: dict) -> dict[str, str]:
    return {entry["path"]: entry["layer"] for entry in manifest.get("files", []) if entry.get("layer") in ORDER}


def _generated(rel: str) -> bool:
    rel = rel[len(".claude/"):] if rel.startswith(".claude/") else rel
    return any(fnmatch.fnmatch(rel, pat) or rel == pat.rstrip("/*") for pat in GENERATED)


class Tree:
    def __init__(self, root: Path, layers: dict[str, str]):
        self.root = root
        self.layers = layers
        self._local: list[str] | None = None
        self.by_base: dict[str, list[str]] = {}
        for rel in layers:
            self.by_base.setdefault(posixpath.basename(rel), []).append(rel)

    def on_disk(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def local_with_suffix(self, suffix: str) -> list[str]:
        """Unshipped files under .claude/ ending in `suffix`; walked once, lazily."""
        if self._local is None:
            base = self.root / ".claude"
            self._local = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in SKIP_WALK]
                for name in filenames:
                    rel = Path(dirpath, name).relative_to(self.root).as_posix()
                    if rel not in self.layers:
                        self._local.append(rel)
        return [rel for rel in self._local if rel.endswith(suffix)]

    def memories(self) -> dict[str, list[str]]:
        """Memory slug -> its files, shipped or local, from the memory store and its archive."""
        if not hasattr(self, "_memories"):
            self._memories = {}
            for rel in list(self.layers) + self.local_with_suffix(".md"):
                if re.fullmatch(r"\.claude/auto-memory/(archive/)?[^/]+\.md", rel):
                    self._memories.setdefault(posixpath.basename(rel)[:-3], []).append(rel)
        return self._memories

    def resolve(self, candidates) -> tuple[list[str], bool]:
        """(shipped matches, whether any candidate exists on disk). A directory resolves to the
        shipped files under it."""
        shipped = [c for c in candidates if c in self.layers]
        for c in candidates:
            if c not in self.layers and (self.root / c).is_dir():
                shipped.extend(rel for rel in self.layers if rel.startswith(c.rstrip("/") + "/"))
        return shipped, bool(shipped) or any(self.on_disk(c) for c in candidates)


def _judge(tree: Tree, source: str, candidates, display: str, *, path_shaped: bool) -> Finding | None:
    shipped, exists = tree.resolve(candidates)
    rank = ORDER[tree.layers[source]]
    if shipped:
        best = min(ORDER[tree.layers[c]] for c in shipped)
        if best > rank:
            target = min(shipped, key=lambda c: ORDER[tree.layers[c]])
            return Finding(source, "needs-" + LAYERS[best], target)
        return None
    if exists:
        return Finding(source, "local-only", display)
    if path_shaped:
        return Finding(source, "missing", display)
    return None


def _doc_candidates(token: str, source_dir: str):
    if re.fullmatch(r"/[a-z][a-z0-9_]*", token):
        name = token[1:]
        return (f".claude/commands/{name}.md", f".claude/skills/{name}/SKILL.md"), False
    if re.fullmatch(r"[a-z][a-z0-9_]*", token):
        return (f".claude/skills/{token}/SKILL.md", f".claude/commands/{token}.md"), False
    if not re.fullmatch(r"[\w.-]+(/[\w.-]+)*/?", token) or not token.endswith(DOC_SUFFIXES):
        return None, False
    if "/" not in token:
        return ("@base:" + token,), False
    rel = token[len(".claude/"):] if token.startswith(".claude/") else token
    first = rel.split("/", 1)[0]
    if set(re.split(r"[/.]", rel)) & PLACEHOLDERS:
        return None, False
    if token.startswith("../") or token.startswith("./"):
        return (posixpath.normpath(posixpath.join(source_dir, token)),), False
    cands = (f".claude/{rel}", f".claude/skills/{rel}", f".claude/auto-memory/{rel}",
             f".claude/auto-memory/archive/{rel}", posixpath.normpath(posixpath.join(source_dir, rel)),
             "@suffix:" + rel)
    return cands, token.startswith(".claude/") or first in CLAUDE_DIRS or first == "_brainstorm_shared"


def _expand(tree: Tree, cands):
    """Direct candidates first; a `@suffix:` form (a path a sentence scopes to a skill, such as
    "testing's `reference/running.md`") applies only when no direct candidate exists."""
    out, suffix = [], None
    for c in cands:
        if c.startswith("@base:"):
            out.extend(tree.by_base.get(c[len("@base:"):], []))
        elif c.startswith("@suffix:"):
            suffix = "/" + c[len("@suffix:"):]
        else:
            out.append(c)
    if suffix and not any(c in tree.layers or tree.on_disk(c) for c in out):
        out.extend(rel for rel in tree.layers if rel.endswith(suffix))
        out.extend(tree.local_with_suffix(suffix))
    return out


def _scan_doc(tree: Tree, source: str, text: str):
    source_dir = posixpath.dirname(source)
    seen = set()
    tokens = re.findall(r"`([^`\s]+)`", text)
    for span in re.findall(r"`([^`\n]+)`", text):
        if len(span.split()) > 1:
            tokens.extend(w.strip("\"'") for w in span.split()
                          if "/" in w and w.strip("\"'").endswith(DOC_SUFFIXES))
    links = re.findall(r"\]\(([^)\s#]+)(?:#[^)]*)?\)", re.sub(r"`[^`\n]*`", "", text))
    for token in tokens:
        if token in seen or any(m in token for m in "<>*~$=(){}[]|") or "..." in token \
                or token.startswith("/tmp") or token.endswith("/"):
            continue
        seen.add(token)
        if _generated(token):
            continue
        cands, path_shaped = _doc_candidates(token, source_dir)
        if not cands:
            continue
        cands = _expand(tree, cands)
        if not cands:
            continue
        found = _judge(tree, source, cands, token, path_shaped=path_shaped)
        if found:
            yield found
    for link in links:
        if link in seen or re.match(r"[a-z]+:", link) or "/" not in link and "." not in link:
            continue
        seen.add(link)
        joined = link.lstrip("/") if link.startswith("/.claude/") else \
            posixpath.normpath(posixpath.join(source_dir, link))
        if not joined.startswith(".claude/") or _generated(joined):
            continue
        found = _judge(tree, source, (joined,), joined, path_shaped=True)
        if found:
            yield found
    slugs = set()
    for slug in re.findall(r"(?<![\w/-])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?:\.md)?(?![\w/-])", text):
        if slug in slugs or slug not in tree.memories():
            continue
        slugs.add(slug)
        found = _judge(tree, source, tree.memories()[slug], slug, path_shaped=False)
        if found:
            yield found


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    names = []
    if handler.type is None:
        return True
    for node in (handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]):
        if isinstance(node, ast.Name):
            names.append(node.id)
    return bool({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names))


def _python_deps(text: str):
    """(module names imported unguarded, string literals outside docstrings, optional names)."""
    tree = ast.parse(text)
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_catches_import_error(h) for h in node.handlers):
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    guarded.add(id(inner))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    imports, strings, optional = set(), [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and id(node) not in guarded:
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level and id(node) not in guarded:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == OPTIONAL_LOADER:
            optional.update(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
                and id(node) not in guarded:
            strings.append(node.value)
    return imports, strings, optional


SCRIPT_REF = re.compile(r"([\w./${}-]*?)([A-Za-z_][\w-]*(?:\.py|\.sh|\.js|\.ps1))\b")


def _script_refs(tree: Tree, source: str, text: str, skip: set[str]):
    for match in SCRIPT_REF.finditer(text):
        base = match.group(2)
        if base in skip or base == posixpath.basename(source):
            continue
        cands = tree.by_base.get(base)
        if cands:
            yield base, cands


def _scan_python(tree: Tree, source: str, text: str):
    try:
        imports, strings, optional = _python_deps(text)
    except SyntaxError:
        return
    seen = set()
    for name in sorted(imports - optional):
        cands = tree.by_base.get(name + ".py")
        if cands and name not in seen:
            seen.add(name)
            found = _judge(tree, source, cands, name + ".py", path_shaped=False)
            if found:
                yield found
    if OPTIONAL_LOADER not in imports:
        source_dir = posixpath.dirname(source)
        for value in strings:
            sibling = f"{source_dir}/{value}.py"
            if re.fullmatch(r"[A-Za-z_]\w*", value) and value not in seen and sibling in tree.layers:
                seen.add(value)
                found = _judge(tree, source, (sibling,), value + ".py", path_shaped=False)
                if found:
                    yield found
    skip = {n + ".py" for n in optional} | {n + ".py" for n in seen}
    for base, cands in _script_refs(tree, source, "\n".join(strings), skip):
        if base not in seen:
            seen.add(base)
            found = _judge(tree, source, cands, base, path_shaped=False)
            if found:
                yield found


def _strip_comments(text: str, suffix: str) -> str:
    if suffix in (".sh", ".ps1"):
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())


def _scan_script(tree: Tree, source: str, text: str):
    seen = set()
    for base, cands in _script_refs(tree, source, _strip_comments(text, posixpath.splitext(source)[1]), set()):
        if base not in seen:
            seen.add(base)
            found = _judge(tree, source, cands, base, path_shaped=False)
            if found:
                yield found


def scan(root: Path, layers: dict[str, str], check: set[str] | None = None) -> list[Finding]:
    """Findings for every file in `check` (default: every shipped file) that exists under `root`."""
    tree = Tree(Path(root), layers)
    findings = []
    for source in sorted(check if check is not None else layers):
        if source not in layers:
            continue
        path = tree.root / source
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if source.endswith(".md"):
            findings.extend(_scan_doc(tree, source, text))
        elif source.endswith(".py"):
            findings.extend(_scan_python(tree, source, text))
        elif source.endswith((".sh", ".js", ".ps1")):
            findings.extend(_scan_script(tree, source, text))
    return findings


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lock", help="a consumer lock: check its tracked rows")
    source.add_argument("--manifest", help="a baseline manifest: check every file it ships")
    parser.add_argument("--root", default=".", help="directory holding .claude/ (default: cwd)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.lock or args.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"layer_closure: unreadable input: {exc}", file=sys.stderr)
        return 2
    if args.lock:
        manifest = pinned_manifest(Path(args.root), data)
        if manifest is None:
            print("layer_closure: the pinned baseline manifest is unreadable; run "
                  "`baseline_sync.py check` to refresh the cache clone", file=sys.stderr)
            return 2
        findings = scan(Path(args.root), consumer_layers(data, manifest), lock_checked(data))
    else:
        findings = scan(Path(args.root), manifest_layers(data))
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=1))
    else:
        for finding in findings:
            print(finding.render())
        print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
