#!/usr/bin/env python3
"""Run the checks a change's paths reach, never the whole suite.

    python3 .claude/tools/reach_run.py [--base REF] [--dry-run] [--paths PATH [PATH ...]]

The table is `gate_cadence` in the adaptation files (`tools/adaptation.py` owns the layering):
  - `reach`: [{"name", "paths": [glob...], "cmd"}]. Each entry whose globs match a changed path runs
    once; `{paths}` in `cmd` becomes its matched paths, double-quoted and space-joined, and a `cmd`
    without it runs once as written. Collected paths that no longer exist are not passed; an entry whose matched
    paths were all deleted prints `skipped` rather than run a path-less form.
  - `reach_exempt`: globs for paths that owe no check.
  - `reach_fallback`: the project's full form. A changed path no entry matches and no exempt glob
    covers fails closed to it, and the run names those paths.
A glob with no `/` matches a file name at any depth; `**` crosses folders, `*` and `?` do not.

Paths default to `git diff --name-only <merge-base with main>...HEAD` plus uncommitted and untracked
files; `--base` replaces the merge-base ref, `--paths` replaces collection and is passed as given. Commands run from the
repo root through the platform shell. Exit 0 on pass, 1 on any failed command, 2 when the table is
absent or the arguments are refused.

A command `gate_cadence.wide` classifies as wide runs only where a wide run may start (`hooks/_wide_runs.py`
`gate_caller`: the session or a `gate` sidecar). In any other sidecar job it is printed as `owed to the gate` and not
run, and the verdict line names it: the wide guard reads the Bash command, never the table's commands this tool runs,
so the tool applies the same classifier itself (`orchestration` reference `brief_checks.md`).
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import adaptation  # noqa: E402
import cli_usage  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import _wide_runs  # noqa: E402

CLAUDE_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(CLAUDE_DIR)
ACCEPTS = "[--base <ref>] [--dry-run] [--paths <path> ...]; paths come from git when --paths is absent"
EXAMPLE = "python3 .claude/tools/reach_run.py --dry-run --paths src/ui/AddKeys.tsx shared/dates.ts"


def glob_regex(pattern):
    pattern = pattern.replace("\\", "/")
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    body = "".join(out)
    if "/" not in pattern:
        body = "(?:.*/)?" + body
    return re.compile("^" + body + "$")


def matches(path, patterns):
    return any(glob_regex(p).match(path) for p in patterns)


def git_lines(*args):
    done = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return [line.strip().replace("\\", "/") for line in done.stdout.splitlines() if line.strip()]


def collect(base):
    point = base
    if not point:
        found = git_lines("merge-base", "main", "HEAD")
        point = found[0] if found else "HEAD"
    paths = git_lines("diff", "--relative", "--name-only", "%s...HEAD" % point)
    paths += git_lines("diff", "--relative", "--name-only", "HEAD")
    paths += git_lines("ls-files", "--others", "--exclude-standard")
    return list(dict.fromkeys(paths))


def quote(path):
    return '"%s"' % path.replace('"', '\\"')


def plan(table, paths):
    """Entries that run (name, cmd template, matched paths), and the paths no entry or exemption covers."""
    steps, covered = [], set()
    for entry in table["reach"]:
        hit = [p for p in paths if matches(p, entry["paths"])]
        if hit:
            steps.append((entry["name"], entry["cmd"], hit))
            covered.update(hit)
    exempt = table.get("reach_exempt") or []
    loose = [p for p in paths if p not in covered and not matches(p, exempt)]
    return steps, loose


def table_errors(table):
    rows = table.get("reach")
    if not isinstance(rows, list):
        return "reach is not a list"
    for i, row in enumerate(rows):
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or not isinstance(row.get("cmd"), str) or not isinstance(row.get("paths"), list)):
            return "reach[%d] needs name (str), paths (list) and cmd (str)" % i
    if not isinstance(table.get("reach_exempt", []), list):
        return "reach_exempt is not a list"
    if not isinstance(table.get("reach_fallback", ""), str):
        return "reach_fallback is not a string"
    return None


def owed(cmd):
    """The wide runs `cmd` starts when this process may not start one (a dispatched job that is not a gate), else []."""
    if _wide_runs.gate_caller({}, os.environ):
        return []
    return _wide_runs.classify(cmd, ROOT, ROOT)


def execute(name, cmd, count):
    started = time.time()
    sys.stdout.flush()
    code = subprocess.run(cmd, shell=True, cwd=ROOT).returncode
    print("reach: %s %d path(s) -> exit %d in %.0fs" % (name, count, code, time.time() - started))
    sys.stdout.flush()
    return code


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = cli_usage.ShapeParser(prog="reach_run.py", add_help=False)
    parser.accepts, parser.example = ACCEPTS, EXAMPLE
    parser.add_argument("--base")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--paths", nargs="*")
    args = parser.parse_args(argv)
    table = adaptation.get(CLAUDE_DIR, "gate_cadence")
    if not isinstance(table, dict) or "reach" not in table:
        print("reach: not configured")
        sys.stdout.write(cli_usage.refusal("a `gate_cadence.reach` list in adaptation.json",
                                           'gate_cadence.reach: [{"name": "app", "paths": ["src/**"], "cmd": "npx vitest related --run {paths}"}]'))
        return 2
    error = table_errors(table)
    if error:
        print("reach: not configured (%s)" % error)
        return 2
    given = args.paths is not None
    paths = [p.replace("\\", "/") for p in (args.paths if given else collect(args.base))]
    gone = set() if given else {p for p in paths if not os.path.exists(os.path.join(ROOT, p))}
    steps, loose = plan(table, paths)
    fallback = table.get("reach_fallback") or ""
    if loose and not fallback:
        print("reach: not configured (%d path(s) match no entry and no reach_fallback is set: %s)"
              % (len(loose), ", ".join(loose)))
        return 2
    failed, left = [], []
    for name, cmd, hit in steps:
        live = [p for p in hit if p not in gone]
        if "{paths}" in cmd and not live:
            print("reach: %s %d path(s) -> skipped (all deleted)" % (name, len(hit)))
            continue
        run_cmd = cmd.replace("{paths}", " ".join(quote(p) for p in live))
        if owed(run_cmd):
            print("reach: %s %d path(s) -> owed to the gate (a wide run; this job is not a gate)" % (name, len(hit)))
            left.append(name)
        elif args.dry_run:
            print("reach: plan %s %d path(s): %s" % (name, len(hit), run_cmd))
        elif execute(name, run_cmd, len(hit)) != 0:
            failed.append(name)
    if loose:
        print("reach: %d path(s) match no entry: %s -> fallback" % (len(loose), ", ".join(loose)))
        if owed(fallback):
            print("reach: fallback %d path(s) -> owed to the gate (a wide run; this job is not a gate)" % len(loose))
            left.append("fallback")
        elif args.dry_run:
            print("reach: plan fallback %d path(s): %s" % (len(loose), fallback))
        elif execute("fallback", fallback, len(loose)) != 0:
            failed.append("fallback")
    if args.dry_run:
        print("reach: dry run, nothing ran")
        return 0
    due = " (owed to the gate: %s)" % ", ".join(left) if left else ""
    print("reach: fail (%s)%s" % (", ".join(failed), due) if failed else "reach: pass" + due)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
