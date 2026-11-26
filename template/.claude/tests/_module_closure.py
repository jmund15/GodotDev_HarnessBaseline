"""Copy a scratch `.claude` tree's modules by entry point: each entry plus every sibling module it imports.

A proof fixture that hand-lists the modules it copies breaks the next time a listed module gains a sibling import,
far from the edit. `copy_closure` names only the entries and follows their imports (a module under `hooks/`, `tools/`
or `scripts/`, or a package there such as `scripts/_proof_runner/`), so the set stays whole. Imports are read with
`ast`: a module-level import, or a `_helper.py` file name a module loads, is followed; a lazy import inside a
function is not, so a fixture that drives such a branch names that module as an entry too. A fixture that proves a
module degrades without a sibling passes that sibling in `omit`, which is then neither copied nor followed.
"""
import ast
import re
import shutil
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
SEARCH = ("hooks", "tools", "scripts")
_HELPER_FILE = re.compile(r"_\w+\.py")


def _module_file(claude, parts):
    """The `.claude`-relative file a dotted module name resolves to under SEARCH, or None for anything else."""
    for top in SEARCH:
        base = Path(claude) / top
        for stop in range(len(parts), 0, -1):
            path = base.joinpath(*parts[:stop])
            for candidate in (path.with_suffix(".py"), path / "__init__.py"):
                if candidate.is_file():
                    return candidate
    return None


def _imports(source_file):
    """Each dotted module name a file imports at module level (inside `if`/`try`/class bodies too, not inside a
    function), a `from pkg import name` also naming `pkg.name`, plus each `_helper.py` file name it mentions anywhere
    (`_hook_state` loads `_file_lock.py` so, inside a function). A function-level import is a lazy, optional path:
    following it would pull most of the tree in through one rarely-run branch."""
    tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))
    names = [node.value[:-3] for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and _HELPER_FILE.fullmatch(node.value)]
    todo = list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names.extend("%s.%s" % (node.module, alias.name) for alias in node.names)
        elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todo.extend(ast.iter_child_nodes(node))
    return names


def closure(entries, claude=CLAUDE, omit=()):
    """The `.claude`-relative POSIX paths of `entries` and everything they import, sorted."""
    claude, found, todo = Path(claude), set(), [Path(claude) / entry for entry in entries]
    while todo:
        path = todo.pop()
        rel = path.relative_to(claude).as_posix()
        if rel in found:
            continue
        found.add(rel)
        for name in _imports(path):
            target = _module_file(claude, name.split("."))
            if target and target.name not in omit:
                todo.append(target)
                if target.name != "__init__.py" and (target.parent / "__init__.py").is_file() \
                        and target.parent.parent.name in SEARCH:
                    todo.append(target.parent / "__init__.py")
    return sorted(found)


def copy_closure(claude_dir, *entries, src=CLAUDE, omit=(), with_entries=True, flat=False):
    """Copy `entries` (`.claude`-relative, e.g. "hooks/_wide_runs.py") and their import closure from `src` to the
    same relative paths under `claude_dir`; `with_entries=False` copies only what they import, for a fixture that
    plants its own copy of an entry; `flat=True` puts every file directly in `claude_dir`, for a fixture that stages
    a tool and its siblings in one folder. Returns the paths copied."""
    paths = [rel for rel in closure(entries, src, omit) if with_entries or rel not in entries]
    for rel in paths:
        target = Path(claude_dir) / (Path(rel).name if flat else rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(src) / rel, target)
    return paths
