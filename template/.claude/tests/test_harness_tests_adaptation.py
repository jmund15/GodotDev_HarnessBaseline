"""Re-runnable proof for scripts/harness_tests.py's `adaptation.json` `proof_excluded` and
`proof_timeouts` seam -- the runner seam (Design Doc §8, owner ruling 2026-09-15 R1).

Three shapes: adaptation.json absent -> EXCLUDED stays empty and _PROOF_TIMEOUTS stays at the runner's built-in table; present with
valid entries -> `proof_excluded` merges into EXCLUDED and `proof_timeouts` merges into
_PROOF_TIMEOUTS; a wrong-typed top-level key (not a dict) -> that table stays empty plus one
stderr line (from the shared loader). Validation case: a `proof_timeouts` value that is not a
positive int is skipped with one stderr line, and a valid sibling entry in the same dict still
merges.

Each case builds a scratch `.claude`-shaped tree (`scripts/`, `hooks/`, `tools/`,
`skills/project_subsystems/`) and imports `harness_tests.py` fresh under a unique module name
per case, since its adaptation merge runs once at import time.

    python3 .claude/tests/test_harness_tests_adaptation.py
"""
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_SCRIPTS = os.path.join(HERE, "..", "scripts")
REAL_HOOKS = os.path.join(HERE, "..", "hooks")
REAL_TOOLS = os.path.join(HERE, "..", "tools")

_counter = [0]


def rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def make_scratch(runner_source_path, seed=None):
    tmp = tempfile.mkdtemp(prefix="harness_tests_seam_")
    scripts_dir = os.path.join(tmp, "scripts")
    hooks_dir = os.path.join(tmp, "hooks")
    tools_dir = os.path.join(tmp, "tools")
    skill_dir = os.path.join(tmp, "skills", "project_subsystems")
    tests_dir = os.path.join(tmp, "tests")
    for d in (scripts_dir, hooks_dir, tools_dir, skill_dir, tests_dir):
        os.makedirs(d, exist_ok=True)
    shutil.copy(runner_source_path, os.path.join(scripts_dir, "harness_tests.py"))
    shutil.copy(os.path.join(REAL_HOOKS, "_hook_state.py"), os.path.join(hooks_dir, "_hook_state.py"))
    shutil.copy(os.path.join(REAL_TOOLS, "adaptation.py"), os.path.join(tools_dir, "adaptation.py"))
    if seed is not None:
        with open(os.path.join(skill_dir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(seed if isinstance(seed, str) else json.dumps(seed))
    return tmp


def import_fresh(tmp):
    """Import `scripts/harness_tests.py` from the scratch tree under a unique name, so
    each case's module-level adaptation merge runs against that case's own seed."""
    _counter[0] += 1
    name = f"_harness_tests_seam_probe_{_counter[0]}"
    path = os.path.join(tmp, "scripts", "harness_tests.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    buf = io.StringIO()
    with redirect_stderr(buf):
        spec.loader.exec_module(mod)
    return mod, buf.getvalue()


def main():
    cases = []
    failures = []
    scratches = []
    real_runner = os.path.join(REAL_SCRIPTS, "harness_tests.py")
    try:
        # --- absent: both tables stay empty, no message ------------------------------------
        tmp = make_scratch(real_runner, seed=None)
        scratches.append(tmp)
        mod, err = import_fresh(tmp)
        cases.append(("absent adaptation.json: EXCLUDED stays empty", mod.EXCLUDED == {}))
        cases.append(("absent adaptation.json: _PROOF_TIMEOUTS stays at the built-in table", mod._PROOF_TIMEOUTS == mod._BUILTIN_PROOF_TIMEOUTS))
        cases.append(("absent adaptation.json: no stderr", err == ""))

        # --- present: proof_excluded and proof_timeouts merge in -----------------------------
        tmp = make_scratch(real_runner, seed={
            "proof_excluded": {"test_flaky_thing.py": "known flaky on CI"},
            "proof_timeouts": {"test_slow_thing.py": 600},
        })
        scratches.append(tmp)
        mod, err = import_fresh(tmp)
        cases.append(("present adaptation.json: proof_excluded merges into EXCLUDED",
                      mod.EXCLUDED.get("test_flaky_thing.py") == "known flaky on CI"))
        cases.append(("present adaptation.json: proof_timeouts merges into _PROOF_TIMEOUTS",
                      mod._PROOF_TIMEOUTS.get("test_slow_thing.py") == 600))
        cases.append(("present adaptation.json: no stderr on valid entries", err == ""))

        # --- wrong-typed: proof_excluded is not a dict --------------------------------------
        tmp = make_scratch(real_runner, seed={"proof_excluded": ["not", "a", "dict"]})
        scratches.append(tmp)
        mod, err = import_fresh(tmp)
        cases.append(("wrong-typed proof_excluded: EXCLUDED stays empty", mod.EXCLUDED == {}))
        cases.append(("wrong-typed proof_excluded: one stderr line names the key",
                      err.count("\n") == 1 and "proof_excluded" in err))

        # --- wrong-typed: proof_timeouts is not a dict --------------------------------------
        tmp = make_scratch(real_runner, seed={"proof_timeouts": "not-a-dict"})
        scratches.append(tmp)
        mod, err = import_fresh(tmp)
        cases.append(("wrong-typed proof_timeouts: _PROOF_TIMEOUTS stays at the built-in table", mod._PROOF_TIMEOUTS == mod._BUILTIN_PROOF_TIMEOUTS))
        cases.append(("wrong-typed proof_timeouts: one stderr line names the key",
                      "proof_timeouts" in err))

        # --- validation: a non-positive/non-int timeout entry is skipped, sibling merges ---
        tmp = make_scratch(real_runner, seed={"proof_timeouts": {
            "test_bad_negative.py": -5, "test_bad_float.py": 1.5, "test_good.py": 30,
        }})
        scratches.append(tmp)
        mod, err = import_fresh(tmp)
        cases.append(("validation: a negative timeout is skipped", "test_bad_negative.py" not in mod._PROOF_TIMEOUTS))
        cases.append(("validation: a non-int (float) timeout is skipped", "test_bad_float.py" not in mod._PROOF_TIMEOUTS))
        cases.append(("validation: a valid sibling timeout still merges", mod._PROOF_TIMEOUTS.get("test_good.py") == 30))
        cases.append(("validation: each bad entry gets one stderr line",
                      "test_bad_negative.py" in err and "test_bad_float.py" in err))
    finally:
        for tmp in scratches:
            rm(tmp)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
