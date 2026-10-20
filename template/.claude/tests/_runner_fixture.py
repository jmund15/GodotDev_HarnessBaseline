"""The runner's import closure for a scratch `.claude` tree: one home for what `scripts/harness_tests.py` loads."""
import shutil
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
RUNNER_DEPS = ("hooks/_hook_state.py", "hooks/_file_lock.py", "hooks/_bounded_run.py", "tools/adaptation.py")


def copy_runner_deps(claude_dir):
    """Copy each real runner dependency into the same relative path under `claude_dir`."""
    for rel in RUNNER_DEPS:
        target = Path(claude_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLAUDE / rel, target)
