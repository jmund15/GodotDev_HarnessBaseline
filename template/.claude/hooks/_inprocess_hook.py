"""Run one hook's `main()` in this process, with its stdin, stdout, stderr and argv swapped.

A dispatcher that serves several hooks from one interpreter spawn uses this to keep each member's own CLI
contract (stdin JSON payload; stdout JSON or stderr plus exit 2) unchanged, so a member still runs and is
proven standalone.
"""
import io
import json
import sys
import traceback


class Capture(io.StringIO):
    """StringIO that tolerates the stream calls a hook makes on a real console handle."""

    def reconfigure(self, **_kwargs):
        pass


def run_hook(module, argv_tail, raw_payload):
    """Run `module.main()` on `raw_payload`. Returns (exit_code, stdout, stderr).

    An exception in the member is exit code 1 with its traceback on stderr; a `SystemExit` carries its code.
    """
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    out, err = Capture(), Capture()
    sys.stdin = io.StringIO(raw_payload)
    sys.stdout, sys.stderr = out, err
    sys.argv = [getattr(module, "__file__", module.__name__)] + list(argv_tail)
    code = 0
    try:
        ret = module.main()
        if isinstance(ret, int):
            code = ret
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception:
        code = 1
        err.write(traceback.format_exc())
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def hook_output(stdout_text):
    """The `hookSpecificOutput` dict a hook printed, or {} when it printed nothing usable."""
    text = stdout_text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    hso = parsed.get("hookSpecificOutput")
    return hso if isinstance(hso, dict) else {}
