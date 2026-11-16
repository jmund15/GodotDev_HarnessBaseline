#!/usr/bin/env python3
"""Proof of harness_tests.py's runner-env mode (`--runner-env`, or `HARNESS_RUNNER_ENV=1`).

A proof that reads the ambient environment (HOME, the global git config, a `CLAUDE_*` variable, the live
stores under `~/.claude`) without declaring it is green in a session and red on the land runner or CI.
The mode runs each proof with the environment a clean runner has: a home under the proof's own temp root,
no global or system git config, and no `CLAUDE_*` variable but the declared ones. It joins the pass key, so
a pass recorded without it never answers a run with it.

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
print("PROBE" + json.dumps(out))
'''


def probe_env(scrub, planted):
    """The environment the probe saw, read from the file it prints through (stdout of a pass is dropped)."""
    tmp = tempfile.mkdtemp(prefix="ht_renv_")
    report = os.path.join(tmp, "report.json")
    proof = os.path.join(tmp, "test_env_probe.py")
    body = PROBE.replace('print("PROBE" + json.dumps(out))', "open(%r, 'w').write(json.dumps(out))" % report)
    with open(proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    saved = {k: os.environ.get(k) for k in planted}
    os.environ.update(planted)
    was = dict(ht._RUNNER_ENV)
    ht._RUNNER_ENV["scrub"] = scrub
    try:
        status, _seconds, detail = ht.run_proof(proof)
    finally:
        ht._RUNNER_ENV.clear()
        ht._RUNNER_ENV.update(was)
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
    planted = {"CLAUDE_PLANTED_AMBIENT": "1", "CLAUDE_PROJECT_DIR": HERE}
    on, why = probe_env(True, planted)
    check("a probe proof runs in runner-env mode", on is not None, why)
    if on is not None:
        temp = on["temp"]
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
            check("runner-env: %s sits under the proof's temp root" % name,
                  bool(on[name]) and inside(on[name], temp), on[name])
        check("runner-env: `~` expands inside the temp root", inside(on["expanduser"], temp), on["expanduser"])
        # A proof whose subject is the machine's own live state (its session registry beside the real process table)
        # reads the caller's home by this declared name, never by `~`.
        check("runner-env: the caller's own home stays readable as HARNESS_CALLER_HOME",
              on.get("HARNESS_CALLER_HOME") == os.path.expanduser("~"), on.get("HARNESS_CALLER_HOME"))
        check("runner-env: the system git config is off", on["GIT_CONFIG_NOSYSTEM"] == "1", on["GIT_CONFIG_NOSYSTEM"])
        check("runner-env: git reads no global config", on["global_git"].strip() == "", on["global_git"])
        # CLAUDE_CODE_EXECPATH is the installed binary's location, set only where this machine has one.
        check("runner-env: only the declared CLAUDE_* names stay",
              set(on["claude_names"]) <= {"CLAUDE_PROJECT_DIR", "CLAUDE_CODE_EXECPATH"}
              and "CLAUDE_PROJECT_DIR" in on["claude_names"], on["claude_names"])
        check("runner-env: the declared CLAUDE_PROJECT_DIR keeps its value", on["CLAUDE_PROJECT_DIR"] == planted["CLAUDE_PROJECT_DIR"], on["CLAUDE_PROJECT_DIR"])

    off, why = probe_env(False, planted)
    check("a probe proof runs without the mode", off is not None, why)
    if off is not None:
        check("no mode: HOME stays the caller's", off["HOME"] == os.environ.get("HOME"), off["HOME"])
        check("no mode: no HARNESS_CALLER_HOME is declared", off.get("HARNESS_CALLER_HOME") is None, off.get("HARNESS_CALLER_HOME"))
        check("no mode: a CLAUDE_* name stays", "CLAUDE_PLANTED_AMBIENT" in off["claude_names"], off["claude_names"])
        check("no mode: the git config stays the caller's", off["GIT_CONFIG_NOSYSTEM"] == os.environ.get("GIT_CONFIG_NOSYSTEM"))

    was = dict(ht._RUNNER_ENV)
    try:
        ht._RUNNER_ENV["scrub"] = False
        plain = ht._runner_launch_digest()
        ht._RUNNER_ENV["scrub"] = True
        scrubbed = ht._runner_launch_digest()
    finally:
        ht._RUNNER_ENV.clear()
        ht._RUNNER_ENV.update(was)
    check("the mode joins the pass key's runner part", plain is not None and plain != scrubbed)

    for flag_env, expect in (("", False), ("1", True)):
        saved = os.environ.get("HARNESS_RUNNER_ENV")
        os.environ["HARNESS_RUNNER_ENV"] = flag_env
        try:
            spec2 = importlib.util.spec_from_file_location("ht2", MOD)
            fresh = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(fresh)
        finally:
            if saved is None:
                os.environ.pop("HARNESS_RUNNER_ENV", None)
            else:
                os.environ["HARNESS_RUNNER_ENV"] = saved
        check("HARNESS_RUNNER_ENV=%r selects the mode: %s" % (flag_env, expect), fresh._RUNNER_ENV["scrub"] is expect)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
