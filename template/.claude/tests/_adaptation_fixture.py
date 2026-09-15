"""Shared scaffold for the per-seam `adaptation.json` proofs (Design Doc §8): builds a
scratch `.claude`-shaped tree (`hooks/`, `tools/`, `skills/project_subsystems/`) under the
system temp directory so each proof can run a hook copy — either the real one or a saved
pristine (pre-S7) copy — against a real, absent or malformed `adaptation.json`, without
touching the actual worktree. Teardown clears read-only bits before removing the tree.
"""
import json
import os
import shutil
import stat
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_HOOKS = os.path.join(HERE, "..", "hooks")
REAL_TOOLS = os.path.join(HERE, "..", "tools")


def rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def make_scratch(hook_name, hook_source_path, seed=None, extra_hook_files=()):
    """A scratch `.claude`-shaped tree: `<tmp>/hooks/<hook_name>` copied from
    `hook_source_path` (real file or pristine backup), `<tmp>/tools/adaptation.py` copied
    from the real loader, and `<tmp>/skills/project_subsystems/adaptation.json` written from
    `seed` (a dict; `None` means no file at all -- the "absent" case). `extra_hook_files`
    copies sibling modules a hook imports (e.g. `_hook_state.py`) into the scratch `hooks/`
    dir too. Returns the scratch root."""
    tmp = tempfile.mkdtemp(prefix="adaptation_seam_")
    hooks_dir = os.path.join(tmp, "hooks")
    tools_dir = os.path.join(tmp, "tools")
    skill_dir = os.path.join(tmp, "skills", "project_subsystems")
    os.makedirs(hooks_dir, exist_ok=True)
    os.makedirs(tools_dir, exist_ok=True)
    os.makedirs(skill_dir, exist_ok=True)

    shutil.copy(hook_source_path, os.path.join(hooks_dir, hook_name))
    shutil.copy(os.path.join(REAL_TOOLS, "adaptation.py"), os.path.join(tools_dir, "adaptation.py"))
    for name in extra_hook_files:
        shutil.copy(os.path.join(REAL_HOOKS, name), os.path.join(hooks_dir, name))

    if seed is not None:
        with open(os.path.join(skill_dir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(seed if isinstance(seed, str) else json.dumps(seed))

    return tmp
