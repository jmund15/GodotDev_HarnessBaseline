"""Re-runnable proof for hooks/compound_cd_approver.py's `adaptation.json` `read_only_commands`
seam (Design Doc §8).

Three shapes: adaptation.json absent -> the built-in SAFE_SEGMENT_COMMANDS only; present with
a valid entry -> that command joins the allowlist; a wrong-typed `read_only_commands` (not a
list) -> the default (built-in set only) plus one stderr line. Validation cases: an entry
matching NEVER_SAFE (e.g. "rm") is rejected and never joins the allowlist; an entry failing
`^[a-z0-9_.-]+$` is rejected too. Each case runs the hook as a real subprocess against a
scratch `.claude` tree (`_adaptation_fixture`), so behavior is proven end-to-end, not by
importing internals.

    python3 .claude/tests/test_compound_cd_approver_adaptation.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "compound_cd_approver.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)


def run_hook(hook_path, command):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, hook_path], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def is_allowed(stdout):
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision") == "allow"
    except (json.JSONDecodeError, AttributeError):
        return False


def main():
    cases = []
    failures = []
    scratches = []
    try:
        # --- absent: adaptation.json missing -> "dotnet" is NOT auto-approved ------------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=None)
        scratches.append(tmp)
        out, _err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && dotnet build")
        cases.append(("absent adaptation.json: an unlisted command is not auto-approved",
                      not is_allowed(out)))

        # --- present: a valid read_only_commands entry joins the allowlist ---------------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"read_only_commands": ["dotnet"]})
        scratches.append(tmp)
        out, err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && dotnet build")
        cases.append(("present adaptation.json: a valid entry is auto-approved", is_allowed(out)))
        cases.append(("present adaptation.json: no stderr on a valid entry", err == ""))
        out, _err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && git status")
        cases.append(("a built-in command still auto-approves alongside a project entry",
                      is_allowed(out)))

        # --- wrong-typed: read_only_commands is not a list -> default + one stderr line ---
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"read_only_commands": "dotnet"})
        scratches.append(tmp)
        out, err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && dotnet build")
        cases.append(("wrong-typed read_only_commands: entry never joins the allowlist",
                      not is_allowed(out)))
        cases.append(("wrong-typed read_only_commands: one stderr line names the key",
                      err.count("\n") == 0 and "read_only_commands" in err))

        # --- validation: NEVER_SAFE rejects a destructive command -------------------------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"read_only_commands": ["rm"]})
        scratches.append(tmp)
        out, err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && rm -rf x")
        cases.append(("NEVER_SAFE: 'rm' is never added to the allowlist", not is_allowed(out)))
        cases.append(("NEVER_SAFE: the rejection names the entry on stderr", "rm" in err))

        # --- validation: shape regex rejects an entry with disallowed characters ----------
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed={"read_only_commands": ["Dotnet Build"]})
        scratches.append(tmp)
        out, err, _rc = run_hook(os.path.join(tmp, "hooks", HOOK_NAME), "cd /repo && dotnet build")
        cases.append(("shape regex: an entry with a space/uppercase is rejected",
                      not is_allowed(out) and "read_only_commands" in err))
    finally:
        for tmp in scratches:
            fx.rm(tmp)

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
