#!/usr/bin/env python3
"""Proof that harness_tests.py runs every proof with the environment a clean runner has.

A proof that reads the ambient environment (HOME, the global git config, a `CLAUDE_*` variable, the live
stores under `~/.claude`) without declaring it is green in a session and red on the land runner. Every run,
local or land, gives each proof a home under its own temp root, no global or system git config, and no
`CLAUDE_*` variable but the declared ones, so such a read goes red at the writer's own run, and a local pass
answers the land's run of the same inputs.

Run: python3 .claude/tests/test_harness_tests_runner_env.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "scripts", "harness_tests.py")
spec = importlib.util.spec_from_file_location("ht", MOD)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)

PROBE = r'''
import json, os, subprocess, sys
temp = os.environ["TMP"]
out = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                                      "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "CLAUDE_PROJECT_DIR",
                                      "CLAUDE_PLANTED_AMBIENT", "HARNESS_CALLER_HOME", "TMP")}
out["temp"] = temp
out["expanduser"] = os.path.expanduser("~")
out["claude_names"] = sorted(k for k in os.environ if k.startswith("CLAUDE_"))
git = subprocess.run(["git", "config", "--global", "--list"], capture_output=True, text=True)
out["global_git"] = git.stdout
out["fscache"] = subprocess.run(["git", "config", "--get", "core.fscache"], capture_output=True, text=True).stdout.strip()
out["autocrlf"] = subprocess.run(["git", "config", "--get", "core.autocrlf"], capture_output=True, text=True).stdout.strip()
print("PROBE" + json.dumps(out))
'''


def probe_env(planted):
    """The environment the probe saw, read from the file it prints through (stdout of a pass is dropped)."""
    tmp = tempfile.mkdtemp(prefix="ht_renv_")
    report = os.path.join(tmp, "report.json")
    proof = os.path.join(tmp, "test_env_probe.py")
    body = PROBE.replace('print("PROBE" + json.dumps(out))', "open(%r, 'w').write(json.dumps(out))" % report)
    with open(proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    saved = {k: os.environ.get(k) for k in planted}
    os.environ.update(planted)
    try:
        status, _seconds, detail = ht.run_proof(proof)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if status != "pass" or not os.path.isfile(report):
        return None, status + ": " + detail
    with open(report, encoding="utf-8") as fh:
        return json.load(fh), ""


def inside(path, root):
    return os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(os.path.abspath(root)) + os.sep)


failures = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print("     " + str(detail))


def main():
    # The caller is a session: it carries an ambient CLAUDE_* name and no runner flag. The run is clean anyway.
    planted = {"CLAUDE_PLANTED_AMBIENT": "1", "CLAUDE_PROJECT_DIR": HERE}
    saved_flag = os.environ.pop("HARNESS_RUNNER_ENV", None)
    try:
        on, why = probe_env(planted)
    finally:
        if saved_flag is not None:
            os.environ["HARNESS_RUNNER_ENV"] = saved_flag
    check("a probe proof runs", on is not None, why)
    if on is None:
        return 1
    temp = on["temp"]
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        check("%s sits under the proof's temp root" % name, bool(on[name]) and inside(on[name], temp), on[name])
    check("`~` expands inside the temp root", inside(on["expanduser"], temp), on["expanduser"])
    # A proof whose subject is the machine's own live state (its session registry beside the real process table)
    # reads the caller's home by this declared name, never by `~`.
    check("the caller's own home stays readable as HARNESS_CALLER_HOME",
          on.get("HARNESS_CALLER_HOME") == os.path.expanduser("~"), on.get("HARNESS_CALLER_HOME"))
    check("the system git config is off", on["GIT_CONFIG_NOSYSTEM"] == "1", on["GIT_CONFIG_NOSYSTEM"])
    check("git reads no global config", on["global_git"].strip() == "", on["global_git"])
    # Git for Windows' file-system cache is speed only: without it each git call in a proof runs several times
    # slower than the runner's own git does, while the system config's behaviour settings stay off.
    if os.name == "nt":
        check("git keeps its file-system cache on Windows", on["fscache"] == "true", on["fscache"])
    check("no system behaviour setting reaches git", on["autocrlf"] == "", on["autocrlf"])
    # CLAUDE_CODE_EXECPATH is the installed binary's location, set only where this machine has one.
    check("only the declared CLAUDE_* names stay",
          set(on["claude_names"]) <= {"CLAUDE_PROJECT_DIR", "CLAUDE_CODE_EXECPATH"}
          and "CLAUDE_PROJECT_DIR" in on["claude_names"], on["claude_names"])
    check("the declared CLAUDE_PROJECT_DIR keeps its value", on["CLAUDE_PROJECT_DIR"] == planted["CLAUDE_PROJECT_DIR"],
          on["CLAUDE_PROJECT_DIR"])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
