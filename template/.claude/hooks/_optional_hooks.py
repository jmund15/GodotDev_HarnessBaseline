"""Import hook modules only where this project has them.

A project installs a subset of the hook files (a project without the C# steps has no `session_context_loader_csharp`),
so a caller loads each by name: an absent file yields None and the caller skips it. A present file's import failure
raises unless the caller supplies `on_error(name, exception)` to return its action-scoped fault stub. Each import has
its own boundary, so one broken member cannot prevent its siblings from loading.

    csharp, godot = _optional_hooks.load("session_context_loader_csharp", "session_context_loader_godot")
    names = _optional_hooks.members("pre_edit", shared)   # `_chain`: the shared members plus the project's, installed
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent


def present(name: str) -> bool:
    return (HOOKS_DIR / f"{name}.py").is_file()


def members(event, shared):
    """The module names `_chain` runs for `event` (the dispatcher's stem: `pre_edit`, `post_edit`, `pre_read`,
    `post_read`, `prompt`): the `shared` names, then each name `adaptation.json` `hook_members[event]` lists, keeping
    only installed modules. A shared member is shared code and lives in `_chain.EVENTS`; the key holds project-local
    members only, so an absent or malformed key leaves the shared members running."""
    tools = str(HOOKS_DIR.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import adaptation
        named = (adaptation.get(HOOKS_DIR.parent, "hook_members") or {}).get(event) or []
    except Exception:
        named = []
    return tuple(name for name in tuple(shared) + tuple(n for n in named if isinstance(n, str)) if present(name))


def load(*names: str, on_error=None):
    """One module or None per name, in order; a single name returns the bare value."""
    modules = []
    for name in names:
        try:
            module = importlib.import_module(name) if present(name) else None
        except Exception as exc:
            if on_error is None:
                raise
            module = on_error(name, exc)
        modules.append(module)
    return modules[0] if len(modules) == 1 else tuple(modules)
