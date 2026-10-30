#!/usr/bin/env python3
"""Proof for tools/work_key.py: the key a paid call's work is known by, and the refusal when the ledger holds it.

`key` is stable and moves with each input (label, brief bytes, model, effort, harness); a caller's
SIDECAR_WORK_KEY replaces the brief part; the brief is hashed from the file's own bytes. `check` runs over
planted ledgers: only a FINISHED row (exit 0, paid, schema not invalid, no loop stop, no budget stop) with the
same key refuses; an unreadable ledger refuses; a missing ledger and a row with no key do not. Exit 0 pass,
1 fail.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / ".claude" / "tools" / "work_key.py"
FAILS = []


def require(ok, message):
    if not ok:
        FAILS.append(message)


def run(*args, env=None):
    full = {k: v for k, v in os.environ.items() if k not in ("SIDECAR_WORK_KEY", "SIDECAR_LEDGER_PATH")}
    full.update(env or {})
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=full, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def key(tmp, brief=b"the brief\n", model="m1", effort="high", label="L1", harness="h1", env=None, text=False):
    path = Path(tmp) / "brief.md"
    path.write_bytes(brief)
    flag = "--prompt-text-file" if text else "--prompt-file"
    code, out, err = run("key", "--model", model, "--effort", effort, "--label", label, flag, str(path),
                         "--harness", harness, env=env)
    require(code == 0 and len(out) == 64, "key must print a sha256 and exit 0: %r %r %r" % (code, out, err))
    return out


def keys(tmp):
    base = key(tmp)
    require(base == key(tmp), "the key is stable across calls")
    for name, other in (("label", key(tmp, label="L2")), ("brief bytes", key(tmp, brief=b"another brief\n")),
                        ("model", key(tmp, model="m2")), ("effort", key(tmp, effort="low")),
                        ("harness", key(tmp, harness="h2"))):
        require(other != base, "a different %s must change the key" % name)
    require(key(tmp, text=True) == base, "a prompt-text file is hashed the same as a prompt file: the file's own bytes")
    declared = key(tmp, env={"SIDECAR_WORK_KEY": "cell-1"})
    require(declared != base, "a declared SIDECAR_WORK_KEY replaces the brief part")
    require(declared == key(tmp, brief=b"unrelated bytes", harness="elsewhere", env={"SIDECAR_WORK_KEY": "cell-1"}),
            "with a declared key the brief and the harness stay out of it")
    require(declared != key(tmp, label="L2", env={"SIDECAR_WORK_KEY": "cell-1"}), "a declared key still moves with the label")
    require(declared != key(tmp, env={"SIDECAR_WORK_KEY": "cell-2"}), "a different declared key moves the key")
    require(key(tmp, env={"SIDECAR_WORK_KEY": ""}) == base, "an empty SIDECAR_WORK_KEY declares nothing")
    code, out, _ = run("key", "--model", "m1", "--effort", "high", "--label", "L1", "--harness", "h1")
    require(code == 0 and len(out) == 64, "a key needs no brief file: %r %r" % (code, out))


def row(k, **over):
    doc = {"timestamp": "2026-10-20T10:00:00Z", "label": "cell-a", "launchId": "launch-1", "workKey": k,
           "exitCode": 0, "costUSD": 0.5, "schemaValid": True, "loopStop": None}
    doc.update(over)
    return doc


def ledger(tmp, rows, name="ledger.jsonl"):
    path = Path(tmp) / name
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows), encoding="utf-8")
    return str(path)


def checks(tmp):
    k = "a" * 64
    code, out, err = run("check", "--key", k, "--ledger", ledger(tmp, [row(k)]))
    require(code == 15 and "2026-10-20T10:00:00Z" in err and "cell-a" in err and "launch-1" in err and out == "",
            "a finished same-key row refuses with its timestamp, label and launch id on stderr: %r %r" % (code, err))
    cases = {
        "a different key": row("b" * 64),
        "a failed run": row(k, exitCode=1),
        "a zero-cost run": row(k, costUSD=0),
        "an unpriced run": row(k, costUSD=None),
        "a schema-invalid run": row(k, schemaValid=False),
        "a loop stop": row(k, loopStop="loop p=2 steps=Read,Grep"),
        "a retired budget stop (its rows ended exit 14)": row(k, exitCode=14, budgetStop="spent $3"),
        "a row with no key": {key_: v for key_, v in row(k).items() if key_ != "workKey"},
    }
    for name, planted in cases.items():
        code, out, err = run("check", "--key", k, "--ledger", ledger(tmp, [planted]))
        require(code == 0, "%s must not refuse: %r %r" % (name, code, err))
    mixed = ledger(tmp, ["", "garbage line " + k, row("b" * 64), row(k, exitCode=1), row(k, label="the finished one")])
    code, _, err = run("check", "--key", k, "--ledger", mixed)
    require(code == 15 and "the finished one" in err, "a finished row among failed ones and garbage refuses: %r %r" % (code, err))
    code, _, err = run("check", "--key", k, "--ledger", str(Path(tmp) / "absent.jsonl"))
    require(code == 0, "a missing ledger refuses nothing: %r %r" % (code, err))
    unreadable = Path(tmp) / "a-directory"
    unreadable.mkdir()
    code, _, err = run("check", "--key", k, "--ledger", str(unreadable))
    require(code == 15 and "cannot be read" in err, "an unreadable ledger refuses and says so: %r %r" % (code, err))
    voided = ledger(tmp, [row(k)], name="voided.jsonl")
    sys.path.insert(0, str(TOOL.parent))
    import work_key as wk
    wk.void(k, "launch-1", "no score file", voided)
    code, _, err = run("void", "--key", k, "--launch-id", "launch-1", "--reason", "x", "--ledger", voided)
    require(code != 0, "void has no command line: %r" % code)
    code, _, err = run("check", "--key", k, "--ledger", voided)
    require(code == 0, "a finished row its caller voided (no deliverable) does not refuse: %r %r" % (code, err))
    later = ledger(tmp, [row(k), {"workKey": k, "voids": "launch-1"}, row(k, launchId="launch-2", label="paid again")],
                   name="later.jsonl")
    code, _, err = run("check", "--key", k, "--ledger", later)
    require(code == 15 and "paid again" in err, "a void cancels only its own launch; a later finished launch refuses: %r %r" % (code, err))
    other = ledger(tmp, [row(k), {"workKey": "b" * 64, "voids": "launch-1"}], name="other.jsonl")
    code, _, _ = run("check", "--key", k, "--ledger", other)
    require(code == 15, "a void under another key cancels nothing: %r" % code)
    env_ledger = ledger(tmp, [row(k)], name="from_env.jsonl")
    code, _, _ = run("check", "--key", k, env={"SIDECAR_LEDGER_PATH": env_ledger})
    require(code == 15, "the default ledger is SIDECAR_LEDGER_PATH: %r" % code)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        keys(tmp)
        checks(tmp)
    for f in FAILS:
        print("FAIL " + f)
    print("test_work_key: %s" % ("PASS" if not FAILS else "%d failure(s)" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
