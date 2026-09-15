"""Re-runnable proof for hooks/plan_memory_reminder.py's `adaptation.json` `memory_domains`
seam (Design Doc §8).

Three shapes: adaptation.json absent -> only the built-in floor (Refactoring, Obsidian/Docs)
can match; present with a valid row -> that domain matches on its own triggers and its
`rules` entries surface in the reminder; a wrong-typed `memory_domains` (not a list) -> the
default (built-in floor only) plus one stderr line. Validation cases: a row missing `name` or
`triggers` is skipped with one stderr line, and a row with a non-list field is skipped too --
in both cases a valid sibling row still matches.

`find_recent_plan()` reads `Path.home() / ".claude" / "plans"`, so each case points
USERPROFILE/HOME at a scratch directory rather than touching the real one.

    python3 .claude/tests/test_plan_memory_reminder_adaptation.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "plan_memory_reminder.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)

PLAN_FILLER = (
    "This plan covers a routine change with no special domain content beyond the trigger "
    "phrase below. It touches a handful of files and needs no special handling, but the plan "
    "body must clear the fifty word floor the hook enforces before it will infer any domain "
    "at all, so this filler sentence pads the word count safely past that mark for the case."
)


def write_plan(home, text):
    plans_dir = os.path.join(home, ".claude", "plans")
    os.makedirs(plans_dir, exist_ok=True)
    path = os.path.join(plans_dir, "probe.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def run_hook(hook_path, home):
    payload = {"tool_name": "ExitPlanMode"}
    env = dict(os.environ)
    env["USERPROFILE"] = home
    env["HOME"] = home
    r = subprocess.run([sys.executable, hook_path], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30, env=env)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def additional_context(stdout):
    if not stdout:
        return ""
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("additionalContext", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def main():
    cases = []
    failures = []
    scratches = []
    homes = []

    def scratch(seed, plan_text):
        tmp = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=seed)
        scratches.append(tmp)
        home = tempfile.mkdtemp(prefix="pmr_home_")
        homes.append(home)
        write_plan(home, plan_text)
        return os.path.join(tmp, "hooks", HOOK_NAME), home

    trigger_plan = "The spellcraft catalog needs restructuring. " + PLAN_FILLER

    try:
        # --- absent: the custom domain never matches --------------------------------------
        hook, home = scratch(None, trigger_plan)
        out, err = run_hook(hook, home)
        cases.append(("absent adaptation.json: custom domain does not match",
                      "SpellCraft" not in additional_context(out)))
        cases.append(("absent adaptation.json: no stderr", err == ""))

        # --- present: the custom domain matches and carries its rules ---------------------
        seed = {"memory_domains": [
            {"name": "SpellCraft", "triggers": ["spellcraft"], "memory_keywords": ["spell"],
             "skills": ["spell_authoring"], "rules": [".claude/rules/spell_rules.md"]},
        ]}
        hook, home = scratch(seed, trigger_plan)
        out, err = run_hook(hook, home)
        ctx = additional_context(out)
        cases.append(("present adaptation.json: custom domain matches", "SpellCraft" in ctx))
        cases.append(("present adaptation.json: its rules surface in the reminder",
                      ".claude/rules/spell_rules.md" in ctx))
        cases.append(("present adaptation.json: no stderr on a valid row", err == ""))

        # --- wrong-typed: memory_domains is not a list -------------------------------------
        hook, home = scratch({"memory_domains": {"name": "SpellCraft"}}, trigger_plan)
        out, err = run_hook(hook, home)
        cases.append(("wrong-typed memory_domains: custom domain never applies",
                      "SpellCraft" not in additional_context(out)))
        cases.append(("wrong-typed memory_domains: one stderr line names the key",
                      err.count("\n") == 0 and "memory_domains" in err))

        # --- validation: a row missing 'name'/'triggers' is skipped, sibling still fires --
        seed = {"memory_domains": [
            {"memory_keywords": ["x"]},
            {"name": "SpellCraft", "triggers": ["spellcraft"], "memory_keywords": [],
             "skills": [], "rules": []},
        ]}
        hook, home = scratch(seed, trigger_plan)
        out, err = run_hook(hook, home)
        cases.append(("validation: a row missing name/triggers is skipped with stderr",
                      "skipped" in err))
        cases.append(("validation: the valid sibling row still matches",
                      "SpellCraft" in additional_context(out)))

        # --- validation: a row with a non-list field is skipped, sibling still fires ------
        seed = {"memory_domains": [
            {"name": "Bad", "triggers": "not-a-list"},
            {"name": "SpellCraft", "triggers": ["spellcraft"], "memory_keywords": [],
             "skills": [], "rules": []},
        ]}
        hook, home = scratch(seed, trigger_plan)
        out, err = run_hook(hook, home)
        cases.append(("validation: a non-list field row is skipped with stderr", "Bad" in err))
        cases.append(("validation: the valid sibling row still matches after a bad row",
                      "SpellCraft" in additional_context(out)))

        # --- a seeded row named after a built-in floor entry REPLACES it, never duplicates -
        refactor_plan = "Refactor the abstraction layer for consistency. " + PLAN_FILLER
        seed = {"memory_domains": [
            {"name": "Refactoring", "triggers": ["refactor"], "memory_keywords": ["refactor"],
             "skills": ["refactor_procedure"], "rules": []},
        ]}
        hook, home = scratch(seed, refactor_plan)
        out, err = run_hook(hook, home)
        ctx = additional_context(out)
        cases.append(("same-name row replaces the floor entry, not a duplicate match",
                      ctx.count("Refactoring") == 1))
        cases.append(("the replacing row's own skills take effect",
                      "refactor_procedure" in ctx))
    finally:
        for tmp in scratches:
            fx.rm(tmp)
        for home in homes:
            fx.rm(home)

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
