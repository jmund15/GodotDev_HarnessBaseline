"""Import sub-hooks from higher baseline layers only where this project adopted them.

A dispatcher of layer X may run guards of a higher layer. A consumer that adopted only X has no
file for them, so the dispatcher loads each by name: an absent file yields None and the
dispatcher skips it. A present file's import failure raises unless the caller supplies
`on_error(name, exception)` to return its action-scoped fault stub. Each import has its
own boundary, so one broken member cannot prevent its siblings from loading.
`tools/layer_closure.py` reads the names passed here as optional dependencies.

    tres_guard, lint_guard = _optional_hooks.load("tres_guard", "lint_guard")
    CHAIN = _optional_hooks.adopted(CHAIN)   # a chain of (name, ...) entries imported later by name
"""
from __future__ import annotations

import importlib
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent


def present(name: str) -> bool:
    return (HOOKS_DIR / f"{name}.py").is_file()


def adopted(chain):
    """The entries of a (name, ...) chain whose sub-hook is installed, in order."""
    return tuple(entry for entry in chain if present(entry[0]))


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
