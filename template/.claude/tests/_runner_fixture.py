"""The runner's import closure for a scratch `.claude` tree: what `scripts/harness_tests.py` imports, followed by `_module_closure`."""
from _module_closure import copy_closure

RUNNER = "scripts/harness_tests.py"


def copy_runner_deps(claude_dir):
    """Copy every module the runner imports into the same relative path under `claude_dir`; the runner itself is the caller's."""
    copy_closure(claude_dir, RUNNER, with_entries=False)
