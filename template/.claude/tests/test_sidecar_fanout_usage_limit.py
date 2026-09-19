#!/usr/bin/env python3
"""Proof: `tools/sidecar_fanout.py` reports a provider usage-limit stop as `usage-limit`, names the
stream to salvage from, and `--status <out-dir>` classifies each job's latest stream events.

A fake launcher writes a planted body to `-P` and stdout and exits with a chosen code, so nothing
reaches a provider.

Run: python3 .claude/tests/test_sidecar_fanout_usage_limit.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "sidecar_fanout.py")
spec = importlib.util.spec_from_file_location("sf_usage_limit", MOD)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_ul_")


def line(obj):
    return json.dumps(obj)


INIT = line({"type": "system", "subtype": "init", "session_id": "S1"})
WORK = line({"type": "assistant", "session_id": "S1",
             "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}})
TOOL = line({"type": "user", "session_id": "S1",
             "message": {"content": [{"type": "tool_result", "content": "x"}]}})
RETRY = line({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10,
              "error_status": 429, "error": "rate_limit", "session_id": "S1"})
DONE = line({"type": "result", "subtype": "success", "is_error": False, "session_id": "S1",
             "result": "the deliverable"})
LIMIT = line({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429,
              "terminal_reason": "api_error", "session_id": "S1",
              "result": "API Error: Request rejected (429) · The usage limit has been reached"})
SYNTH = line({"type": "result", "subtype": "provider_usage_limit", "is_error": True,
              "terminal_reason": "provider-usage-limit", "synthetic": True, "session_id": "S1",
              "result": "USAGE LIMIT"})

FAKE = os.path.join(TMP, "fake_launcher.py")
with open(FAKE, "w", encoding="utf-8") as fh:
    fh.write(
        "import sys\n"
        "body = open(sys.argv[1], encoding='utf-8').read()\n"
        "if '-P' in sys.argv:\n"
        "    open(sys.argv[sys.argv.index('-P') + 1], 'w', encoding='utf-8').write(body)\n"
        "sys.stdout.write(body)\n"
        "sys.exit(int(sys.argv[2]))\n"
    )

CASES = []


def case(name, fn):
    CASES.append((name, fn))


def run_fanout():
    out_dir = os.path.join(TMP, "fan")
    os.makedirs(out_dir, exist_ok=True)
    planned = []
    for label, body, code in (("spent", "\n".join([INIT, WORK, RETRY, LIMIT, SYNTH]) + "\n", 10),
                              ("broken", "\n".join([INIT, WORK]) + "\n", 1),
                              ("good", "\n".join([INIT, WORK, DONE]) + "\n", 0)):
        body_path = os.path.join(TMP, label + ".body")
        open(body_path, "w", encoding="utf-8").write(body)
        stream = os.path.join(out_dir, label + ".stream")
        planned.append((label, [sys.executable, FAKE, body_path, str(code), "-P", stream],
                        os.path.join(out_dir, label + ".out.json")))
    results = sf.run_jobs(planned, 2, out_dir)
    text, code = sf.summarize(planned, results, compare=False)
    return out_dir, results, text, code


try:
    OUT_DIR, RESULTS, SUMMARY, SUMMARY_CODE = run_fanout()
    SETUP_ERROR = None
except Exception as exc:  # the RED state: report every case as failing, never crash
    OUT_DIR, RESULTS, SUMMARY, SUMMARY_CODE = None, {}, "", None
    SETUP_ERROR = "%s: %s" % (type(exc).__name__, exc)


def summary_line(label):
    return next((ln for ln in SUMMARY.splitlines() if (" %s " % label) in (" " + ln + " ")), "")


case("a usage-limit stop is reported as `usage-limit`, not FAIL",
     lambda: summary_line("spent").startswith("usage-limit") and "FAIL  spent" not in SUMMARY)
case("...and names the stream path to salvage from",
     lambda: os.path.join(OUT_DIR, "spent.stream").replace("\\", "/") in SUMMARY.replace("\\", "/"))
case("...and that stream still holds the full run (not bounded to a pointer)",
     lambda: "provider-usage-limit" in open(os.path.join(OUT_DIR, "spent.stream"), encoding="utf-8").read())
case("an ordinary failure still reads FAIL",
     lambda: "FAIL  broken" in SUMMARY)
case("the tally separates usage-limit from failed, and the fan-out still exits 1",
     lambda: "usage-limit: spent" in SUMMARY and "failed: broken" in SUMMARY
     and "failed: broken, spent" not in SUMMARY and SUMMARY_CODE == 1)


def status_fixture():
    d = os.path.join(TMP, "status")
    os.makedirs(d, exist_ok=True)
    bodies = {
        "w-working": [INIT, WORK, TOOL],
        "w-retrying": [INIT, WORK, RETRY, RETRY],
        "w-stalled": [INIT, WORK],
        "w-finished": [INIT, WORK, DONE],
        "w-limited": [INIT, WORK, RETRY, LIMIT],
    }
    for label, lines in bodies.items():
        with open(os.path.join(d, label + ".stream"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    old = time.time() - 4000
    os.utime(os.path.join(d, "w-stalled.stream"), (old, old))
    r = subprocess.run([sys.executable, MOD, "--status", d], capture_output=True, text=True,
                       timeout=60, encoding="utf-8", env=dict(os.environ, SIDECAR_STALL_SEC="900"))
    states = {}
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 2:
            states[parts[0]] = parts[1]
    return r, states


STATUS_RUN, STATES = status_fixture()
for label, want in (("w-working", "working"), ("w-retrying", "retrying"), ("w-stalled", "stalled"),
                    ("w-finished", "finished"), ("w-limited", "usage-limit")):
    case("--status classifies %s as %s" % (label, want),
         lambda label=label, want=want: STATUS_RUN.returncode == 0 and STATES.get(label) == want)


def main():
    failed = 0
    if SETUP_ERROR:
        print("setup raised %s" % SETUP_ERROR)
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    if failed:
        print("--status stdout:\n" + STATUS_RUN.stdout + STATUS_RUN.stderr[-600:])
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
