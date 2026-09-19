"""Re-runnable proof for hooks/prompt_memory_loader.py's `adaptation.json`
`high_risk_patterns` and `drive_commands` seams (Design Doc §8).

Three shapes each: adaptation.json absent -> the built-in floor only; present with a valid
entry -> that entry's behavior fires; a wrong-typed value (not a list) -> the default plus
one stderr line. Validation case: an entry that does not compile as a regex is skipped with
one stderr line, and a valid sibling entry in the same list still takes effect. Each case
runs the hook as a real subprocess against a scratch `.claude` tree (`_adaptation_fixture`),
with HARNESS_HOOK_STATE_DIR redirected so no run touches ~/.claude/.routing_state.

    python3 .claude/tests/test_prompt_memory_loader_adaptation.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "prompt_memory_loader.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)
EXTRA = ("_hook_state.py", "_prompt_provenance.py")

CUSTOM_DOMAIN_PROMPT = "The spellcraft catalog needs consistent ordering please"


def run_hook(hook_path, prompt, state_dir, session="pml_seam1", permission_mode="default"):
    payload = {"prompt": prompt, "session_id": session, "permission_mode": permission_mode}
    env = dict(os.environ)
    env["HARNESS_HOOK_STATE_DIR"] = state_dir
    r = subprocess.run([sys.executable, hook_path], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30, env=env)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def main():
    cases = []
    failures = []
    scratches = []
    state_dirs = []

    def scratch(seed):
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=seed, extra_hook_files=EXTRA)
        scratches.append(tmp)
        sd = tempfile.mkdtemp(prefix="pml_state_")
        state_dirs.append(sd)
        return os.path.join(tmp, "hooks", HOOK_NAME), sd

    try:
        # --- high_risk_patterns: absent -> the custom domain does not trigger HIGH-RISK --
        hook, sd = scratch(None)
        out, err = run_hook(hook, CUSTOM_DOMAIN_PROMPT, sd)
        cases.append(("absent adaptation.json: custom domain word is not high-risk",
                      "HIGH-RISK TASK" not in out))
        cases.append(("absent adaptation.json: no stderr", err == ""))

        # --- high_risk_patterns: present -> the custom entry fires -----------------------
        hook, sd = scratch({"high_risk_patterns": [r"\bspellcraft\b"]})
        out, err = run_hook(hook, CUSTOM_DOMAIN_PROMPT, sd)
        cases.append(("present adaptation.json: custom high_risk_patterns entry fires",
                      "HIGH-RISK TASK" in out))
        cases.append(("present adaptation.json: no stderr on a valid pattern", err == ""))

        # --- high_risk_patterns: wrong-typed -> default + one stderr line ----------------
        hook, sd = scratch({"high_risk_patterns": r"\bspellcraft\b"})
        out, err = run_hook(hook, CUSTOM_DOMAIN_PROMPT, sd)
        cases.append(("wrong-typed high_risk_patterns: custom entry never applies",
                      "HIGH-RISK TASK" not in out))
        cases.append(("wrong-typed high_risk_patterns: one stderr line names the key",
                      err.count("\n") == 0 and "high_risk_patterns" in err))

        # --- high_risk_patterns: an invalid regex is skipped, a valid sibling still fires -
        hook, sd = scratch({"high_risk_patterns": ["(unclosed", r"\bspellcraft\b"]})
        out, err = run_hook(hook, CUSTOM_DOMAIN_PROMPT, sd)
        cases.append(("invalid regex entry: still triggers via the valid sibling entry",
                      "HIGH-RISK TASK" in out))
        cases.append(("invalid regex entry: named on stderr", "unclosed" in err))

        # --- drive_commands: absent -> a slash command is not drive-special -------------
        hook, sd = scratch(None)
        out, err = run_hook(hook, "/mydrive P1 do the assigned scope", sd, session="pml_seam2")
        cases.append(("absent adaptation.json: unlisted slash command is not drive-special",
                      "drive-memory-obligation" not in out))

        # --- drive_commands: present -> the listed command is drive-special -------------
        hook, sd = scratch({"drive_commands": ["/mydrive"]})
        out, err = run_hook(hook, "/mydrive P1 do the assigned scope", sd, session="pml_seam3")
        cases.append(("present adaptation.json: listed slash command is drive-special",
                      "drive-memory-obligation" in out))
        cases.append(("present adaptation.json: no stderr on a valid drive command", err == ""))

        # --- drive_commands: wrong-typed -> default + one stderr line -------------------
        hook, sd = scratch({"drive_commands": "/mydrive"})
        out, err = run_hook(hook, "/mydrive P1 do the assigned scope", sd, session="pml_seam4")
        cases.append(("wrong-typed drive_commands: entry never applies",
                      "drive-memory-obligation" not in out))
        cases.append(("wrong-typed drive_commands: one stderr line names the key",
                      "drive_commands" in err))
    finally:
        for tmp in scratches:
            fx.rm(tmp)
        for sd in state_dirs:
            fx.rm(sd)

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
