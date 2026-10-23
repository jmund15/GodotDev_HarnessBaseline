#!/usr/bin/env python3
"""Layer closure: no shipped harness file depends on a file its layer's consumers do not receive.

The baseline ships in layers consumed as a prefix of pure -> coding -> godot. A file of layer X
reaches every consumer whose prefix holds X, and the smallest of those holds only X and the layers
below it. So a file closes when every file it cites, imports or executes is shipped at X or lower.

Dependencies read per file type:
- markdown: backticked `/command`, `skill`, `path/file.md` and `file.py` tokens, and relative
  `[text](path)` links resolved against the file's own directory, and memory slugs, bare or
  backticked, that name a file in the memory store or its archive; script paths in fenced blocks and in
  inline spans holding `$`, when the path starts `.claude/` after an optional `$CLAUDE_PROJECT_DIR/` or
  `$ROOT/` (a `$HOME/` or other-root path is not a project reference); and `preload: <path>` header lines
  (a lens template's doctrine file, relative to `.claude/`);
- python: imports outside a `try` that catches ImportError, string literals naming a `.py`/`.sh`/
  `.js`/`.ps1` file (in a test, only whitespace-free ones: a test's command line is fixture input), string
  literals that are wholly a `.md`/`.json` file name or path (a registry's filename; not in a test, whose
  literals are plant data), and bare string literals naming a sibling module (a sub-hook chain imported
  by name). A file importing `_optional_hooks` loads by name through it, so its names count as optional;
  `OPTIONAL_DATA` lists the sources that tolerate their `.md`/`.json` literals absent;
- shell, javascript, powershell: script paths outside comments (`#` lines and trailing `# ...`, and
  PowerShell `<# #>` blocks).

A script name resolves to the shipped files with that basename, else the unshipped ones; a package
marker (`__init__.py`) names no file.

A bare word is a citation only when it names a shipped or local skill, command or memory. A path-shaped
token under a `.claude/` directory that nothing ships and no file on disk holds is `missing`. A cite
that resolves only to a local memory file is evidence a consumer may lack, never a dependency: no finding.
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
MEMORY_DIR = ".claude/auto-memory/"
CLAUDE_DIRS = {"agents", "auto-memory", "commands", "guards", "hooks", "reference", "rules", "schemas",
               "scripts", "skills", "templates", "tests", "tools", "workflows"}
SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".ps1")
DOC_SUFFIXES = (".md", ".json") + SCRIPT_SUFFIXES
# Paths relative to .claude/ that the harness writes at runtime; they exist in every consumer that
# runs the writer, so citing them is not a dangling reference.
GENERATED = (
    "baseline.lock.json", "settings.json", "settings.local.json", "worklog-pending.md",
    "pending_harness_edits.md", "orchestration_verdicts.json", "orchestration_candidates.json", "eval_history.json",
    "self_evaluate_archive.json", "logs/*", "scratch/*", ".cache/*", "cache/*", "worktrees/*",
    "sessions/*", "state/*", "generated/*", "reference/godot_class_index.md", "plugins/*", "__*__/*",
    "tools/load_census.project.json", "index_composition_baseline.json",
)
# Python sources whose string literals name a .md/.json file the source tolerates absent (a path it
# matches, a name in a message, a read behind an existence check, or a list it classifies):
# {source: (target fnmatch patterns relative to .claude/)}. A row is added only after reading the use.
OPTIONAL_DATA = {
    ".claude/tools/baseline_sync.py": ("*",),
    ".claude/tools/layer_closure.py": ("*",),
    ".claude/tools/lens.py": ("commands/agents/*_agents.md",),
    ".claude/tools/lens_briefs.py": ("commands/checklists/*.md",),
    ".claude/tools/review_ledger.py": ("skills/_brainstorm_shared/execution_depth.md",),
    ".claude/tools/workstation_profile.py": ("commands/workstation_setup.md",),
    ".claude/hooks/harness_edit_skill_reminder.py": ("commands/agents/session_audit_agents.md",),
    ".claude/hooks/semantic_search_scope_guard.py": ("reference/semantic_search.md",),
    ".claude/scripts/sync_plan_config.py": ("user_scope/*",),
    ".claude/tools/restate_scan.py": ("tests/fixtures/*",),
    ".claude/tools/sidecar_fanout.py": ("tests/fixtures/*",),
    ".claude/tools/subsys.py": ("skills/project_subsystems/reference/subsystem_details.md",),
    ".claude/tools/user_scope.py": ("user_scope/manifest.local.json",),
}
OPTIONAL_LOADER = "_optional_hooks"
PLACEHOLDERS = {"foo", "bar", "baz", "example", "slug", "name", "x", "y"}
FIXTURE_TREE = "/tests/fixtures/"
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


def _cache_clone(root: Path) -> Path:
    """The sync engine's cache clone for `root`. A linked worktree has none of its own (the cache is
    gitignored), so it reads its main checkout's: the read is pinned by sha, so either clone serves the
    same bytes."""
    import subprocess
    rel = Path(".claude") / ".cache" / "baseline-repo"
    if (Path(root) / rel).is_dir():
        return Path(root) / rel
    common = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, encoding="utf-8")
    return Path(common.stdout.strip()).parent / rel if common.returncode == 0 else Path(root) / rel


def pinned_manifest(root: Path, lock: dict) -> dict | None:
    """The manifest at the lock's pinned baseline commit, read from the sync engine's cache clone."""
    import subprocess
    clone = _cache_clone(root)
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
        self.deps: set[str] = set()
        self.by_base: dict[str, list[str]] = {}
        for rel in layers:
            self.by_base.setdefault(posixpath.basename(rel), []).append(rel)

    def on_disk(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def is_file(self, rel: str) -> bool:
        return (self.root / rel).is_file()

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
    tree.deps.update(shipped)
    tree.deps.update(c for c in candidates if c not in shipped and not c.startswith(MEMORY_DIR) and tree.is_file(c))
    rank = ORDER[tree.layers[source]]
    if shipped:
        best = min(ORDER[tree.layers[c]] for c in shipped)
        if best > rank:
            target = min(shipped, key=lambda c: ORDER[tree.layers[c]])
            return Finding(source, "needs-" + LAYERS[best], target)
        return None
    if exists:
        if all(c.startswith(MEMORY_DIR) for c in candidates if tree.on_disk(c)):
            return None
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
        # A fixture tree holds copies of real files; a suffix citation never means the copy.
        out.extend(rel for rel in tree.layers if rel.endswith(suffix) and FIXTURE_TREE not in rel)
        out.extend(rel for rel in tree.local_with_suffix(suffix) if FIXTURE_TREE not in rel)
    return out


def _doc_commands(text: str) -> str:
    """A document's fenced lines and its inline code spans holding `$`: the commands it tells a reader to run."""
    fenced, prose, inside = [], [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
        elif inside:
            fenced.append(line)
        else:
            prose.append(line)
    spans = [span for span in re.findall(r"`([^`\n]+)`", "\n".join(prose)) if "$" in span]
    return "\n".join(fenced + spans)


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
    for base, cands in _script_refs(tree, source, _doc_commands(text), set(), keep=_claude_prefixed):
        if base not in seen:
            seen.add(base)
            found = _judge(tree, source, cands, base, path_shaped=False)
            if found:
                yield found
    for token in PRELOAD_LINE.findall(text):
        joined = ".claude/" + token
        if token in seen or _generated(joined):
            continue
        seen.add(token)
        found = _judge(tree, source, _expand(tree, (joined,)), token, path_shaped=True)
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
    """(module names imported unguarded, string literals outside docstrings, optional names, whether the
    file imports OPTIONAL_LOADER at all: a guarded loader import still loads its chain by name)."""
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
    loader = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == OPTIONAL_LOADER for alias in node.names):
            loader = True
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
    return imports, strings, optional, loader or OPTIONAL_LOADER in imports


SCRIPT_REF = re.compile(r"([\w./${}-]*?)([A-Za-z_][\w-]*(?:\.py|\.sh|\.js|\.ps1))\b")
DUNDER = re.compile(r"__\w+__\.py")


def _claude_prefixed(prefix: str) -> bool:
    prefix = prefix.lstrip("\"'")
    for root in ("$CLAUDE_PROJECT_DIR/", "$ROOT/"):
        prefix = prefix.removeprefix(root)
    return prefix.startswith(".claude/")


def _script_refs(tree: Tree, source: str, text: str, skip: set[str], keep=None):
    for match in SCRIPT_REF.finditer(text):
        base = match.group(2)
        if base in skip or base == posixpath.basename(source) or DUNDER.fullmatch(base) \
                or (keep and not keep(match.group(1))):
            continue
        cands = tree.by_base.get(base) or tree.local_with_suffix("/" + base)
        if cands:
            yield base, cands


DATA_LITERAL = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.(?:md|json)")
PRELOAD_LINE = re.compile(r"^preload:[ \t]*(\S+)[ \t]*$", re.M)


def _data_refs(tree: Tree, source: str, strings, seen: set[str]):
    """A string constant that is wholly a `.md`/`.json` file name or path (a registry's filename, a
    seed): a bare name resolves like a script name, a path like a document token."""
    source_dir = posixpath.dirname(source)
    optional = OPTIONAL_DATA.get(source, ())
    for value in strings:
        value = value.strip()
        if value in seen or not DATA_LITERAL.fullmatch(value) or posixpath.basename(value) == posixpath.basename(source) \
                or _generated(value):
            continue
        seen.add(value)
        if "/" in value:
            cands, path_shaped = _doc_candidates(value, source_dir)
            cands = _expand(tree, cands) if cands else None
        else:
            cands = tree.by_base.get(value) or [c for c in tree.local_with_suffix("/" + value) if not _generated(c)]
            path_shaped = False
        if cands and not any(fnmatch.fnmatch(c, ".claude/" + pat) for c in cands for pat in optional):
            found = _judge(tree, source, cands, value, path_shaped=path_shaped)
            if found:
                yield found


def _scan_python(tree: Tree, source: str, text: str):
    try:
        imports, strings, optional, loader = _python_deps(text)
    except SyntaxError:
        return
    seen = set()
    for name in sorted(imports - optional):
        cands = tree.by_base.get(name + ".py") or tree.local_with_suffix("/" + name + ".py")
        if cands and name not in seen:
            seen.add(name)
            found = _judge(tree, source, cands, name + ".py", path_shaped=False)
            if found:
                yield found
    if not loader:
        source_dir = posixpath.dirname(source)
        for value in strings:
            sibling = f"{source_dir}/{value}.py"
            if re.fullmatch(r"[A-Za-z_]\w*", value) and value not in seen and sibling in tree.layers:
                seen.add(value)
                found = _judge(tree, source, (sibling,), value + ".py", path_shaped=False)
                if found:
                    yield found
    skip = {n + ".py" for n in optional} | {n + ".py" for n in seen}
    fixtures = source.startswith(".claude/tests/")
    paths = "\n".join(value for value in strings if not (fixtures and re.search(r"\s", value)))
    for base, cands in _script_refs(tree, source, paths, skip):
        if base not in seen:
            seen.add(base)
            found = _judge(tree, source, cands, base, path_shaped=False)
            if found:
                yield found
    if not fixtures:
        yield from _data_refs(tree, source, strings, set())


def _strip_comments(text: str, suffix: str) -> str:
    if suffix in (".sh", ".ps1"):
        if suffix == ".ps1":
            text = re.sub(r"<#.*?#>", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())
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


def _scan_text(tree: Tree, source: str, text: str):
    if source.endswith(".md"):
        return _scan_doc(tree, source, text)
    if source.endswith(".py"):
        return _scan_python(tree, source, text)
    if source.endswith((".sh", ".js", ".ps1")):
        return _scan_script(tree, source, text)
    return iter(())


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
        findings.extend(_scan_text(tree, source, text))
    return findings


def row_dependencies(root: Path, relpath: str, layers: dict[str, str] | None = None,
                     text: str | None = None) -> list[str]:
    """Every file `relpath` imports, runs or cites, shipped or local: what `scan` resolves for it, whatever its
    layer. `layers` defaults to the consumer view of the root's lock; `text` to the file on disk."""
    root = Path(root)
    if layers is None:
        lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        layers = consumer_layers(lock, pinned_manifest(root, lock))
        layers.setdefault(relpath, (lock.get("files", {}).get(relpath) or {}).get("layer") or LAYERS[0])
    layers = {**layers, relpath: layers.get(relpath) or LAYERS[0]}
    if text is None:
        try:
            text = (root / relpath).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
    tree = Tree(root, layers)
    for _ in _scan_text(tree, relpath, text):
        pass
    return sorted(tree.deps - {relpath})


def lock_findings(root: Path, lock: dict, manifest: dict | None) -> list[Finding]:
    """Findings for a consumer's tracked lock rows over what its consumers receive: `consumer_layers(lock, manifest)`."""
    return scan(Path(root), consumer_layers(lock, manifest), lock_checked(lock))


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
        findings = lock_findings(Path(args.root), data, manifest)
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
