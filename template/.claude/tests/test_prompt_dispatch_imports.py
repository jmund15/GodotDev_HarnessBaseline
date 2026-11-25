"""prompt_dispatch imports its members on concurrent threads. `dataclasses` reads `sys.modules['typing']` without the
import lock, so a member defining a dataclass while another member's thread is still importing `typing` saw it half
loaded and faulted on import (overnight_ask_guard, 1 in 6 runs of test_prompt_dispatch.py). Each case runs the real
dispatcher in a fresh interpreter, where nothing has imported `typing` yet."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.abspath(os.path.join(HERE, "..", "hooks"))
FAILURES = []

MEMBER = """import sys
_typing = sys.modules.get("typing")
COMPLETE = _typing is not None and hasattr(_typing, "ClassVar")


def main():
    print("typing complete at import" if COMPLETE else "typing absent or partial at import")
    return 0
"""

DRIVER = """import json, os, sys
sys.path[:0] = [%(stubs)r, %(hooks)r]
import prompt_dispatch as pd
pd._optional_hooks.present = lambda name: os.path.isfile(os.path.join(%(stubs)r, name + ".py"))
code, out, err = pd.dispatch(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}),
                             chain=(("pd_typing_probe", 10),), timing_path=os.path.join(%(stubs)r, "t.jsonl"))
print(json.dumps({"code": code, "out": out, "err": err}))
"""


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def case_a_member_sees_typing_whole_when_it_imports():
    stubs = tempfile.mkdtemp(prefix="pd_imports_")
    with open(os.path.join(stubs, "pd_typing_probe.py"), "w", encoding="utf-8") as fh:
        fh.write(MEMBER)
    driver = os.path.join(stubs, "driver.py")
    with open(driver, "w", encoding="utf-8") as fh:
        fh.write(DRIVER % {"stubs": stubs, "hooks": HOOKS})
    env = {k: v for k, v in os.environ.items() if k != "PYTHONSTARTUP"}
    run = subprocess.run([sys.executable, "-I", driver], capture_output=True, text=True, timeout=60, env=env,
                         stdin=subprocess.DEVNULL)
    check("the dispatcher runs in a fresh interpreter", run.returncode == 0, run.stderr[-800:])
    out = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
    check("a member's module code finds typing fully loaded before any member thread runs",
          "typing complete at import" in out, out or run.stderr[-800:])


def main():
    case_a_member_sees_typing_whole_when_it_imports()
    print()
    if FAILURES:
        for f in FAILURES:
            print("  FAIL " + f)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
