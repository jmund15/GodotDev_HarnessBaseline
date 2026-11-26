"""Call a hook's `main()` in this process, one case at a time, leaving nothing behind.

`call` is the in-process stand-in for spawning `python3 <hook>` with a payload on stdin: same exit code, stdout and
stderr. `script=True` makes it the stand-in for `python3 <tool> <argv>`, where a `SystemExit("message")` prints the
message to stderr and exits 1 as the interpreter does. `run_main` is the same run on a module the caller imported once
(a proof that also calls the tool's functions). The environment and the working directory are process-wide, so
cases that use it run one at a time.
`copy_repo` resets a built git repository by file copy, with no spawn.
"""
import contextlib
import importlib.util
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import types

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


def _as_script(module):
    """`module` as the interpreter runs a tool: its `main` with `SystemExit(<text>)` read as the text on stderr and exit
    1. A stand-in around the module, so a module run many times gains no wrapper."""
    entry = module.main

    def main():
        try:
            return entry()
        except SystemExit as exc:
            if isinstance(exc.code, str):
                print(exc.code, file=sys.stderr)
                return 1
            raise

    return types.SimpleNamespace(main=main, __name__=module.__name__, __file__=getattr(module, "__file__", module.__name__))


@contextlib.contextmanager
def _process_state(env=None, cwd=None):
    """`env` (replacing the process's, as a spawned child's is) and `cwd` for the block; all put back after, with
    `tempfile.gettempdir`'s process-wide cache and `sys.argv`."""
    saved_env, saved_cwd, saved_argv, saved_tempdir = dict(os.environ), os.getcwd(), list(sys.argv), tempfile.tempdir
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
            # the cache holds the first answer, so a planted TMP/TEMP/TMPDIR would not reach the unit
            tempfile.tempdir = None
        if cwd is not None:
            os.chdir(cwd)
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        os.chdir(saved_cwd)
        sys.argv[:] = saved_argv
        tempfile.tempdir = saved_tempdir


def run_main(module, argv=(), payload="", env=None, cwd=None, script=True):
    """`module.main()`, imported by the caller, as one run of `python3 <module file> <argv>`: a `subprocess.CompletedProcess`
    with the exit code and text streams that run gives. `env` replaces the process's (a caller that wants its own layered
    over this process's passes the merged dict) and `cwd` is the working directory; both are put back. `script=False` is
    a hook's reading of `SystemExit(<text>)` (exit 1, nothing printed). `call` imports fresh each time; a proof that
    imports a tool once and runs it many times calls this."""
    with _process_state(env, cwd):
        entry = _as_script(module) if script else module
        code, out, err = _inprocess_hook.run_hook(entry, argv, payload)
    return subprocess.CompletedProcess([getattr(module, "__file__", module.__name__), *argv], code, out, err)


def call(hook_path, payload, env=None, cwd=None, argv=(), script=False):
    """Run the hook at `hook_path` on `payload` (a JSON string, or a dict). Returns (code, stdout, stderr, output),
    `output` being the hook's `hookSpecificOutput` dict. An import-time or `main()` exception is code 1 with the
    traceback on stderr."""
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    hook_path = os.path.abspath(hook_path)
    saved_path = list(sys.path)
    before = set(sys.modules)
    try:
        sys.path.insert(0, os.path.dirname(hook_path))
        with _process_state(env, cwd):
            name = "_hook_call_%d_%s" % (next(_serial), os.path.splitext(os.path.basename(hook_path))[0])
            try:
                spec = importlib.util.spec_from_file_location(name, hook_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
            except BaseException:
                done = subprocess.CompletedProcess([hook_path], 1, "", traceback.format_exc())
            else:
                done = run_main(module, argv, payload, script=script)
        return done.returncode, done.stdout, done.stderr, _inprocess_hook.hook_output(done.stdout)
    finally:
        for added in set(sys.modules) - before:
            del sys.modules[added]
        sys.path[:] = saved_path


def copy_repo(template, dest, overlay=False):
    """Make `dest` an exact copy of the built repository tree `template`, `.git` included, replacing what is there.
    `overlay` lays the tree over what `dest` already holds instead: files the case wrote first stay."""
    if overlay:
        shutil.copytree(template, dest, symlinks=True, dirs_exist_ok=True)
        return
    if os.path.lexists(dest):
        shutil.rmtree(dest, onerror=_writable)
    shutil.copytree(template, dest, symlinks=True)


def _writable(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)
