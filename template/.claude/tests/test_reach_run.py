"""Re-runnable proof for tools/reach_run.py: a slice runs only the checks its changed paths reach.

    python3 .claude/tests/test_reach_run.py

The tool is copied into a planted `.claude` tree beside a planted `gate_cadence` table, so the proof
reads no real project config. Commands record their argv into a log, so "ran once with these paths"
is observed, not inferred.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _module_closure import copy_closure  # noqa: E402
TOOLS = os.path.join(HERE, "..", "tools")
TMP = tempfile.mkdtemp(prefix="reach_run_")
failures = []

RECORDER = ("import json, sys\n"
            "with open('reach.log', 'a', encoding='utf-8') as fh:\n"
            "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "sys.exit(1 if sys.argv[1:2] == ['bad'] else 0)\n")
TABLE = {
    "reach": [
        {"name": "app", "paths": ["src/**", "shared/**"], "cmd": "python3 rec.py app {paths}"},
        {"name": "types", "paths": ["e2e/**", "src/**"], "cmd": "python3 rec.py types"},
        {"name": "bad", "paths": ["bad/**"], "cmd": "python3 rec.py bad {paths}"},
    ],
    "reach_exempt": ["docs/**", "*.md", "rec.py", ".claude/**"],
    "reach_fallback": "python3 rec.py fallback",
}


WIDE_TABLE = {
    "reach": TABLE["reach"] + [{"name": "second project", "paths": ["review/**"], "cmd": "python3 rec.py wide {paths}"}],
    "reach_exempt": TABLE["reach_exempt"],
    "reach_fallback": "python3 rec.py wide fallback",
    "wide": [{"runner": "python3\\s+rec\\.py\\s+wide\\b", "exempt": [], "narrow": None}],
}


def check(name, ok, detail=""):
    if not ok:
        failures.append("%s %s" % (name, detail))


def plant(name, table):
    root = os.path.join(TMP, name)
    os.makedirs(os.path.join(root, ".claude", "skills", "project_subsystems"))
    copy_closure(os.path.join(root, ".claude"), "tools/reach_run.py")
    if table is not None:
        with open(os.path.join(root, ".claude", "skills", "project_subsystems", "adaptation.json"),
                  "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"gate_cadence": table}, fh)
    with open(os.path.join(root, "rec.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(RECORDER)
    return root


def run(root, *args, env=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # a delegate's caller env must not leak in
    done = subprocess.run([sys.executable, os.path.join(root, ".claude", "tools", "reach_run.py")] + list(args),
                          cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**base, **(env or {})})
    log = os.path.join(root, "reach.log")
    calls = []
    if os.path.exists(log):
        with open(log, encoding="utf-8") as fh:
            calls = [json.loads(line) for line in fh if line.strip()]
        os.remove(log)
    return done.returncode, done.stdout + done.stderr, calls


def case_matched_entries():
    root = plant("matched", TABLE)
    code, out, calls = run(root, "--paths", "src/a.ts", "shared/b.ts", "src/dir/c with space.ts")
    check("a matching run passes", code == 0, out)
    check("each entry runs once", sorted(c[0] for c in calls) == ["app", "types"], str(calls))
    app = next((c for c in calls if c[0] == "app"), [])
    check("the entry gets every matched path, quoted whole",
          app[1:] == ["src/a.ts", "shared/b.ts", "src/dir/c with space.ts"], str(app))
    check("an entry without {paths} runs as written", ["types"] in calls, str(calls))
    check("a line per command", "reach: app 3 path(s) -> exit 0 in " in out and "reach: types " in out, out)
    check("the verdict line", out.strip().splitlines()[-1] == "reach: pass", out)


def case_fallback_and_exempt():
    root = plant("fallback", TABLE)
    code, out, calls = run(root, "--paths", "package.json", "src/a.ts")
    check("an unmatched path runs the fallback", ["fallback"] in calls, str(calls))
    check("the fallback run names the path that sent it there", "package.json" in out and "fallback" in out, out)
    check("the matched entry still runs", any(c[0] == "app" for c in calls), str(calls))
    code, out, calls = run(root, "--paths", "docs/x.png", "README.md")
    check("exempt paths run nothing", calls == [] and code == 0, "%s %s" % (calls, out))
    check("exempt paths still end in a verdict", out.strip().splitlines()[-1] == "reach: pass", out)
    code, out, calls = run(root, "--paths", "docs/x.png", "vite.config.ts")
    check("one unmatched path among exempt ones runs the fallback", calls == [["fallback"]], str(calls))
    code, out, calls = run(root, "--paths")
    check("no paths run nothing", calls == [] and code == 0, "%s %s" % (calls, out))


def case_wide_entries_in_a_dispatched_job():
    root = plant("wide", WIDE_TABLE)
    paths = ("--paths", "src/a.ts", "review/x.mjs")
    job = {"CLAUDE_CODE_SIDECAR": "1", "CLAUDE_CODE_SIDECAR_KIND": "author"}
    code, out, calls = run(root, *paths, env=job)
    check("a non-gate sidecar skips the wide entry", not any(c[0] == "wide" for c in calls), str(calls))
    check("its narrow entries still run", any(c[0] == "app" for c in calls), str(calls))
    check("the skip names the entry as owed to the gate", "owed to the gate" in out and "second project" in out, out)
    check("an owed entry is not a failure", code == 0, out)
    check("the verdict names what is owed", out.strip().splitlines()[-1] == "reach: pass (owed to the gate: second project)", out)
    code, out, calls = run(root, "--paths", "package.json", env=job)
    check("the wide fallback is owed too, never run in a job", calls == [] and "fallback" in out and "owed to the gate" in out, "%s %s" % (calls, out))
    code, out, calls = run(root, *paths, env={**job, "CLAUDE_CODE_SIDECAR_KIND": "gate"})
    check("a gate sidecar runs the wide entry", any(c[0] == "wide" for c in calls) and "owed" not in out, "%s %s" % (calls, out))
    code, out, calls = run(root, *paths)
    check("the session (no sidecar marker) runs the wide entry", any(c[0] == "wide" for c in calls) and "owed" not in out, "%s %s" % (calls, out))
    code, out, calls = run(root, "--dry-run", *paths, env=job)
    check("a dry run in a job names the owed entry and runs nothing", calls == [] and "owed to the gate" in out, "%s %s" % (calls, out))


def case_failure():
    root = plant("failing", TABLE)
    code, out, calls = run(root, "--paths", "bad/x.ts", "src/a.ts")
    check("a failing command fails the run", code == 1, out)
    check("the verdict names the failed entry", "reach: fail (bad)" in out, out)
    check("the other entries still ran", any(c[0] == "app" for c in calls), str(calls))
    check("the failing line shows its exit", "reach: bad 1 path(s) -> exit 1" in out, out)


def case_dry_run():
    root = plant("dry", TABLE)
    code, out, calls = run(root, "--dry-run", "--paths", "src/a.ts", "package.json")
    check("a dry run runs nothing", calls == [] and code == 0, "%s %s" % (calls, out))
    check("the plan names each entry, its paths and the fallback",
          "app" in out and "src/a.ts" in out and "fallback" in out and "package.json" in out, out)


def case_no_config():
    for label, table in (("an absent table", None), ("a table with no reach key", {"slice": []})):
        root = plant("noconfig-" + label.split()[1], table)
        code, out, calls = run(root, "--paths", "src/a.ts")
        check(label + " exits 2", code == 2, out)
        check(label + " says so", "reach: not configured" in out, out)
        check(label + " runs nothing", calls == [], str(calls))


def case_refusal():
    root = plant("refusal", TABLE)
    code, out, calls = run(root, "--nonsense")
    check("an unknown flag exits 2", code == 2, out)
    check("the refusal prints accepts: and example:", "accepts:" in out and "example:" in out, out)


def case_table_validation():
    sys.path.insert(0, TOOLS)
    import adaptation
    check("a well-formed reach table validates", adaptation.gate_cadence_errors(TABLE) == [],
          str(adaptation.gate_cadence_errors(TABLE)))
    for label, bad, needle in (
            ("a reach entry with no cmd", {"reach": [{"name": "a", "paths": ["x/**"]}]}, "reach[0].cmd"),
            ("a reach entry with no globs", {"reach": [{"name": "a", "paths": [], "cmd": "c"}]}, "reach[0].paths"),
            ("a non-list exempt", {"reach": [], "reach_exempt": "docs/**"}, "reach_exempt"),
            ("a non-string fallback", {"reach": [], "reach_fallback": 3}, "reach_fallback")):
        errors = adaptation.gate_cadence_errors(bad)
        check(label + " is named", any(needle in e for e in errors), str(errors))


def git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def case_git_collection():
    root = plant("repo", TABLE)
    git(root, "init", "-q", "-b", "main")
    put(root, "src/old.ts", "1")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    git(root, "checkout", "-qb", "work")
    put(root, "src/committed.ts", "1")
    git(root, "add", "src/committed.ts")
    git(root, "commit", "-qm", "work")
    put(root, "src/old.ts", "2")
    put(root, "shared/new.ts", "1")
    code, out, calls = run(root)
    app = next((c for c in calls if c[0] == "app"), [])
    check("collection takes committed, modified and untracked paths",
          sorted(app[1:]) == ["shared/new.ts", "src/committed.ts", "src/old.ts"], "%s %s" % (app, out))
    code, out, calls = run(root, "--base", "work")
    app = next((c for c in calls if c[0] == "app"), [])
    check("--base moves the comparison point",
          sorted(app[1:]) == ["shared/new.ts", "src/old.ts"], "%s %s" % (app, out))
    os.remove(os.path.join(root, "src", "committed.ts"))
    os.remove(os.path.join(root, "src", "old.ts"))
    os.remove(os.path.join(root, "shared", "new.ts"))
    code, out, calls = run(root)
    check("an entry whose paths were all deleted is skipped, not run path-less",
          not any(c[0] == "app" for c in calls) and "skipped (all deleted)" in out, "%s %s" % (calls, out))


if __name__ == "__main__":
    try:
        for fn in (case_matched_entries, case_fallback_and_exempt, case_wide_entries_in_a_dispatched_job, case_failure, case_dry_run,
                   case_no_config, case_refusal, case_table_validation, case_git_collection):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - a crash is a failure
                failures.append("CRASH %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    for line in failures:
        print("FAIL", line)
    print("FAILED %d" % len(failures) if failures else "OK")
    sys.exit(1 if failures else 0)
