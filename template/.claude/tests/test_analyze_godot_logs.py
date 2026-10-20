#!/usr/bin/env python3
"""Proof for hooks/analyze_godot_logs.py.

1. `--log test` reads the newest godot_test.log among TestResults/ and TestResults/runs/**/, and an
   explicit path wins over it.
2. `--mode summary` names a call site whose Info/Debug lines flood the log, and stays silent when the
   volume is spread across sites, under the share floor, or in a small log.
Usage: test_analyze_godot_logs.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANALYZE = os.path.join(ROOT, ".claude", "hooks", "analyze_godot_logs.py")


def norm(path):
    return os.path.normcase(os.path.normpath(path))


def write_log(path, age_s, text="ERROR: fixture line\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    stamp = 1_800_000_000 - age_s
    os.utime(path, (stamp, stamp))


def analyze(cwd, *args):
    proc = subprocess.run([sys.executable, ANALYZE, "--json", "--mode", "summary", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(proc.stdout), ""
    except ValueError:
        return None, "CRASH: " + (proc.stdout + proc.stderr).strip()[:300]


def resolved_path(cwd, *args):
    result, err = analyze(cwd, *args)
    return err if result is None else norm(os.path.join(cwd, result["log_path"]))


def info(msg, src):
    return f"INFO: {msg} @ {src} in Method()\n"


def flood_recs(lines):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "godot.log")
        write_log(path, 0, "".join(lines))
        result, err = analyze(tmp, path)
    if result is None:
        return None, err
    recs = [r for r in result.get("recommendations", []) if "flood" in r.get("issue", "").lower()]
    return recs, ""


def main():
    cases = []
    with tempfile.TemporaryDirectory(prefix="gl_") as tmp:
        older = os.path.join(tmp, "TestResults", "godot_test.log")
        newer = os.path.join(tmp, "TestResults", "runs", "gate-1", "Logic", "godot_test.log")
        explicit = os.path.join(tmp, "elsewhere", "custom.log")
        write_log(older, 500)
        write_log(newer, 100)
        write_log(explicit, 900)
        fixed_only = os.path.join(tmp, "fixed_only")
        write_log(os.path.join(fixed_only, "TestResults", "godot_test.log"), 100)
        fixed_newest = os.path.join(tmp, "fixed_newest")
        write_log(os.path.join(fixed_newest, "TestResults", "godot_test.log"), 10)
        write_log(os.path.join(fixed_newest, "TestResults", "runs", "gate-1", "Logic", "godot_test.log"), 400)
        cases += [
            ("--log test: the newer tagged-run log beats the older fixed-path log",
             resolved_path(tmp, "--log", "test") == norm(newer)),
            ("--log test: an explicit path wins over both",
             resolved_path(tmp, "--log", explicit) == norm(explicit)),
            ("a positional path wins over --log test",
             resolved_path(tmp, explicit, "--log", "test") == norm(explicit)),
            ("--log test: the fixed-path log alone is still read",
             resolved_path(fixed_only, "--log", "test")
             == norm(os.path.join(fixed_only, "TestResults", "godot_test.log"))),
            ("--log test: a newer fixed-path log beats older tagged logs",
             resolved_path(fixed_newest, "--log", "test")
             == norm(os.path.join(fixed_newest, "TestResults", "godot_test.log"))),
        ]

    flood = [info(f"[Health] {20 - i * 0.01:.2f} (-0.01) from 'Decay'", "HealthComponent.cs:442") for i in range(400)]
    flood += [info(f"[Cast] event {i}", f"Caster.cs:{100 + i}") for i in range(40)]
    recs, err = flood_recs(flood)
    cases.append(("flood: a call site owning 400 of 440 blocks is flagged and named",
                  recs is not None and bool(recs) and "HealthComponent.cs:442" in json.dumps(recs)))
    recs, err = flood_recs([info(f"[Cast] event {i}", f"Caster.cs:{i}") for i in range(440)])
    cases.append(("flood: the same volume spread over many sites is not a flood", recs == []))
    busy = [info("[Cast] tick", "Busy.cs:7") for _ in range(250)]
    busy += [info(f"[Cast] event {i}", f"Caster.cs:{i}") for i in range(1100)]
    recs, err = flood_recs(busy)
    cases.append(("flood: a site holding 250 of 1350 blocks is under the share floor", recs == []))
    recs, err = flood_recs([info("[Match] tick", "Match.cs:1") for _ in range(30)])
    cases.append(("flood: a dominant site in a 30-block log is not a flood", recs == []))

    failed = 0
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
        failed += not ok
    print("\n%d/%d passed" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
