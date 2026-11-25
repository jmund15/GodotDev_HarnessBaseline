"""Call a hook's `main()` in this process, one case at a time, leaving nothing behind.

`call` is the in-process stand-in for spawning `python3 <hook>` with a payload on stdin: same exit code, stdout and
stderr. The environment and the working directory are process-wide, so cases that use it run one at a time.
`copy_repo` resets a built git repository by file copy, with no spawn.
"""
import importlib.util
import itertools
import os
import shutil
import sys
import tempfile
import traceback
import json

_HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)
import _inprocess_hook  # noqa: E402

_serial = itertools.count()


def compact_start(hook_path, env, session_id, agent_id=None):
    payload = {"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return call(hook_path, payload, env=dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"],
                                             CC_CACHESTAT_DIR=env["HARNESS_HOOK_STATE_DIR"]))


def call(hook_path, payload, env=None, cwd=None, argv=()):
    """Run the hook at `hook_path` on `payload` (a JSON string, or a dict). Returns (code, stdout, stderr, output),
    `output` being the hook's `hookSpecificOutput` dict. An import-time or `main()` exception is code 1 with the
    traceback on stderr."""
    if not isinstance(payload, str):
        import json
        payload = json.dumps(payload)
    hook_path = os.path.abspath(hook_path)
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    saved_tempdir = tempfile.tempdir
    before = set(sys.modules)
    try:
        sys.path.insert(0, os.path.dirname(hook_path))
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
            # `tempfile.gettempdir` caches the first answer process-wide, so a planted TMP/TEMP/TMPDIR
            # would not reach the unit.
            tempfile.tempdir = None
        if cwd is not None:
            os.chdir(cwd)
        name = "_hook_call_%d_%s" % (next(_serial), os.path.splitext(os.path.basename(hook_path))[0])
        try:
            spec = importlib.util.spec_from_file_location(name, hook_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except BaseException:
            code, out, err = 1, "", traceback.format_exc()
        else:
            code, out, err = _inprocess_hook.run_hook(module, argv, payload)
        return code, out, err, _inprocess_hook.hook_output(out)
    finally:
        for added in set(sys.modules) - before:
            del sys.modules[added]
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_env)
        os.chdir(saved_cwd)
        tempfile.tempdir = saved_tempdir


def copy_repo(template, dest):
    """Make `dest` an exact copy of the built repository tree `template`, `.git` included, replacing what is there."""
    if os.path.lexists(dest):
        shutil.rmtree(dest, onerror=_writable)
    shutil.copytree(template, dest, symlinks=True)


def _writable(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)
