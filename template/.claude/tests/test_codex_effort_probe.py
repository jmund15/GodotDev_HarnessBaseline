#!/usr/bin/env python3
"""Proof for codex_effort_probe — is a codex session's reasoning effort what it thinks it is?

The load-bearing cases are the two NEGATIVES: a session with no capture must say so rather than
guess, and a capture belonging to a PEER session must not be reported as this session's own. The
second is the one that would quietly mislead -- a machine with any codex traffic on it always has
some reading to print, and printing it unqualified is how "attested" becomes a word for "adjacent".

Run: python3 .claude/tests/test_codex_effort_probe.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "scripts", "codex_effort_probe.py")
spec = importlib.util.spec_from_file_location("cep", MOD)
cep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cep)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def traffic(root, session, req, client_effort, upstream_effort, model="gpt-6-astra",
            with_upstream=True):
    d = os.path.join(root, session, req)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "002-010-anthropic-request.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": model, "thinking": {"effort": client_effort}}, fh)
    if with_upstream:
        with open(os.path.join(d, "004-020-upstream-request.json"), "w", encoding="utf-8") as fh:
            json.dump({"model": model, "reasoning": {"effort": upstream_effort}}, fh)
    return d


@case("--help exits zero before probing traffic")
def c_help_before_probe():
    cep.TRAFFIC = os.path.join(tempfile.mkdtemp(prefix="cep_"), "absent")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cep.main(["--help"])
    return code == 0 and "usage:" in out.getvalue() and "NOT ATTESTABLE" not in out.getvalue()


@case("no traffic root at all -> not attested, and says why")
def c_none():
    cep.TRAFFIC = os.path.join(tempfile.mkdtemp(prefix="cep_"), "absent")
    r = cep.probe("s1")
    return r["attested"] is False and "CCP_TRAFFIC_LOG" in r["why"]


@case("the session's OWN capture is attested, upstream value wins")
def c_own():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "s1", "000001-codex-a", "low", "high")
    r = cep.probe("s1")
    return (r["attested"] and r["own"] and r["upstreamEffort"] == "high"
            and r["clientEffort"] == "low")


@case("client != upstream is reported as OVERRIDDEN (the definitive direction)")
def c_overridden():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "s1", "000001-codex-a", "low", "max")
    r = cep.probe("s1")
    return r["effortOverridden"] is True and r["modelOverridden"] is False


@case("client == upstream is NOT claimed as passthrough -- it is indistinguishable")
def c_agrees():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "s1", "000001-codex-a", "high", "high")
    r = cep.probe("s1")
    return r["effortOverridden"] is False


@case("a PEER's capture is NOT reported as this session's own")
def c_peer():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "peer", "000001-codex-a", "low", "max")
    r = cep.probe("mine")
    return r["attested"] and r["own"] is False


@case("a request with no upstream capture is skipped, not reported as effort-absent")
def c_no_upstream():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "s1", "000001-codex-a", "low", None, with_upstream=False)
    return cep.probe("s1")["attested"] is False


@case("with both a complete and an incomplete request, the complete one is used")
def c_mixed():
    root = tempfile.mkdtemp(prefix="cep_")
    cep.TRAFFIC = root
    traffic(root, "s1", "000001-codex-a", "low", "medium")
    traffic(root, "s1", "000002-codex-b", "low", None, with_upstream=False)
    r = cep.probe("s1")
    return r["attested"] and r["upstreamEffort"] == "medium"


@case("exit 3 for a peer-only reading on BOTH the text and --json paths")
def c_exit_parity():
    root = tempfile.mkdtemp(prefix="cep_")
    traffic(root, "peer", "000001-codex-a", "low", "max")
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID="mine", PYTHONIOENCODING="utf-8")
    codes = []
    for extra in ([], ["--json"]):
        src = open(MOD, encoding="utf-8").read().replace(
            'TRAFFIC = os.path.join(os.path.expanduser("~"), ".local", "state", '
            '"claude-code-proxy", "traffic")', "TRAFFIC = %r" % root)
        tmp = os.path.join(root, "probe_copy.py")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        codes.append(subprocess.run([sys.executable, tmp, *extra], env=env,
                                    capture_output=True, text=True, timeout=30).returncode)
    return codes == [3, 3]


@case("the refusal names the relaunch line, not just the problem")
def c_actionable():
    root = tempfile.mkdtemp(prefix="cep_")
    src = open(MOD, encoding="utf-8").read().replace(
        'TRAFFIC = os.path.join(os.path.expanduser("~"), ".local", "state", '
        '"claude-code-proxy", "traffic")', "TRAFFIC = %r" % os.path.join(root, "absent"))
    tmp = os.path.join(root, "probe_copy.py")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    out = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    t = out.stdout
    return ("CCP_CODEX_EFFORT" in t and "CCP_TRAFFIC_LOG=1" in t and "CCP_CODEX_MODEL" in t
            and out.returncode == 3)


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
