"""Re-runnable proof for tools/adaptation.py, the shared `adaptation.json` reader (Design
Doc §8, plan `sync-baseline-v2.md`).

Three shapes per the contract: absent file -> every default, no message; present file ->
project values, no message; a known key of the wrong type -> that key's default plus one
stderr line naming the file. Unknown keys are ignored. Fixtures live under the system temp
directory and are removed on teardown.

    python3 .claude/tests/test_adaptation.py
"""
import json
import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)
import adaptation  # noqa: E402


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def _claude_dir_with(tmp, content):
    claude_dir = os.path.join(tmp, ".claude")
    skill_dir = os.path.join(claude_dir, "skills", "project_subsystems")
    os.makedirs(skill_dir, exist_ok=True)
    if content is not None:
        with open(os.path.join(skill_dir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    return claude_dir


def main():
    cases = []
    failures = []
    tmp = tempfile.mkdtemp(prefix="adaptation_proof_")
    try:
        # --- absent: file missing entirely -----------------------------------
        claude_dir = _claude_dir_with(tmp, None)
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("absent file returns every default", result == adaptation.DEFAULTS))
        cases.append(("absent file prints nothing to stderr", err == ""))

        # --- present: valid project values are used ---------------------------
        seed = {
            "read_only_commands": ["dotnet"],
            "tests_root": "MyTests",
            "memory_domains": [{"name": "X", "triggers": ["x"], "memory_keywords": [],
                                 "skills": [], "rules": []}],
        }
        claude_dir = _claude_dir_with(tmp, json.dumps(seed))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("present file: known key overrides default",
                      result["read_only_commands"] == ["dotnet"] and result["tests_root"] == "MyTests"))
        cases.append(("present file: key absent from seed keeps its default",
                      result["git_submodules"] == []))
        cases.append(("present file prints nothing to stderr", err == ""))

        # --- wrong-typed known key: default + one stderr line ------------------
        claude_dir = _claude_dir_with(tmp, json.dumps({"tests_root": ["not", "a", "string"]}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("wrong-typed key falls back to its default", result["tests_root"] == "Tests"))
        cases.append(("wrong-typed key names itself in stderr", "tests_root" in err))
        cases.append(("wrong-typed key: exactly one stderr line", len(err.strip().splitlines()) == 1))

        # --- unparseable file: every key defaults, one stderr line --------------
        claude_dir = _claude_dir_with(tmp, "{not json")
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("unparseable file returns every default", result == adaptation.DEFAULTS))
        cases.append(("unparseable file: exactly one stderr line", len(err.strip().splitlines()) == 1))

        # --- unknown keys are ignored -------------------------------------------
        claude_dir = _claude_dir_with(tmp, json.dumps({"totally_unknown_key": 123}))
        result, err = _capture_stderr(lambda: adaptation.load(claude_dir))
        cases.append(("unknown key is ignored, not an error", result == adaptation.DEFAULTS and err == ""))

        # --- get() defaults the same way -----------------------------------------
        claude_dir = _claude_dir_with(tmp, None)
        cases.append(("get() on an absent file returns the key's default",
                      adaptation.get(claude_dir, "tests_root") == "Tests"))
    finally:
        _rm(tmp)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


def _capture_stderr(fn):
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = fn()
    return result, buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
