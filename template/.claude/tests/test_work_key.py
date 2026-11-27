#!/usr/bin/env python3
"""Proof for tools/work_key.py: the key a paid call's work is known by, and the refusal when the ledger holds it.

The key is stable and moves with each input (label, brief bytes, model, effort, harness); a caller's
SIDECAR_WORK_KEY replaces the brief part. The refusal runs in process over planted ledgers: only a FINISHED
row (exit 0, paid, schema not invalid, no loop stop, no budget stop) with the same key refuses; an unreadable
ledger refuses; a missing ledger and a row with no key do not. A few spawned `gate` cases prove the CLI's
wiring: stdout, exit codes, the default ledger and both prompt-file flags. Exit 0 pass, 1 fail.
"""
import contextlib
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


sys.path.insert(0, str(TOOL.parent))
import work_key as wk  # noqa: E402


def run(*args, env=None):
    """The CLI as the launcher spawns it: gate's argument and exit wiring only."""
    full = {k: v for k, v in os.environ.items() if k not in ("SIDECAR_WORK_KEY", "SIDECAR_LEDGER_PATH")}
    full.update(env or {})
    r = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=full, timeout=60)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


@contextlib.contextmanager
def declared(value):
    """SIDECAR_WORK_KEY as `value` (None: unset) for an in-process key."""
    old = os.environ.pop(wk.KEY_ENV, None)
    if value is not None:
        os.environ[wk.KEY_ENV] = value
    try:
        yield
    finally:
        os.environ.pop(wk.KEY_ENV, None)
        if old is not None:
            os.environ[wk.KEY_ENV] = old


def key(tmp, brief=b"the brief\n", model="m1", effort="high", label="L1", harness="h1", work=None):
    path = Path(tmp) / "brief.md"
    path.write_bytes(brief)
    with declared(work):
        out = wk.work_key(model, effort, label, str(path), harness)
    require(len(out) == 64, "key must be a sha256: %r" % out)
    return out


def keys(tmp):
    base = key(tmp)
    require(base == key(tmp), "the key is stable across calls")
    for name, other in (("label", key(tmp, label="L2")), ("brief bytes", key(tmp, brief=b"another brief\n")),
                        ("model", key(tmp, model="m2")), ("effort", key(tmp, effort="low")),
                        ("harness", key(tmp, harness="h2"))):
        require(other != base, "a different %s must change the key" % name)
    cell = key(tmp, work="cell-1")
    require(cell != base, "a declared SIDECAR_WORK_KEY replaces the brief part")
    require(cell == key(tmp, brief=b"unrelated bytes", harness="elsewhere", work="cell-1"),
            "with a declared key the brief and the harness stay out of it")
    require(cell != key(tmp, label="L2", work="cell-1"), "a declared key still moves with the label")
    require(cell != key(tmp, work="cell-2"), "a different declared key moves the key")
    require(key(tmp, work="") == base, "an empty SIDECAR_WORK_KEY declares nothing")
    with declared(None):
        require(len(wk.work_key("m1", "high", "L1", None, "h1")) == 64, "a key needs no brief file")


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
    """The re-pay decision, in process: which ledger rows refuse a key."""
    k = "a" * 64
    msg = wk.repay_refusal(k, ledger(tmp, [row(k)]))
    require(msg and "2026-10-20T10:00:00Z" in msg and "cell-a" in msg and "launch-1" in msg,
            "a finished same-key row refuses with its timestamp, label and launch id: %r" % msg)
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
        msg = wk.repay_refusal(k, ledger(tmp, [planted]))
        require(msg is None, "%s must not refuse: %r" % (name, msg))
    mixed = ledger(tmp, ["", "garbage line " + k, row("b" * 64), row(k, exitCode=1), row(k, label="the finished one")])
    msg = wk.repay_refusal(k, mixed)
    require(msg and "the finished one" in msg, "a finished row among failed ones and garbage refuses: %r" % msg)
    require(wk.repay_refusal(k, str(Path(tmp) / "absent.jsonl")) is None, "a missing ledger refuses nothing")
    unreadable = Path(tmp) / "a-directory"
    unreadable.mkdir()
    require(wk.repay_refusal(k, str(unreadable)), "an unreadable ledger refuses and says so")
    voided = ledger(tmp, [row(k)], name="voided.jsonl")
    wk.void(k, "launch-1", "no score file", voided)
    require(wk.repay_refusal(k, voided) is None, "a finished row its caller voided (no deliverable) does not refuse")
    later = ledger(tmp, [row(k), {"workKey": k, "voids": "launch-1"}, row(k, launchId="launch-2", label="paid again")],
                   name="later.jsonl")
    msg = wk.repay_refusal(k, later)
    require(msg and "paid again" in msg, "a void cancels only its own launch; a later finished launch refuses: %r" % msg)
    other = ledger(tmp, [row(k), {"workKey": "b" * 64, "voids": "launch-1"}], name="other.jsonl")
    require(wk.repay_refusal(k, other), "a void under another key cancels nothing")


def gates(tmp):
    """The launcher's one read, spawned: the key on stdout, exit 15 and no key on a refusal, the default ledger."""
    path = Path(tmp) / "brief.md"
    path.write_bytes(b"the brief\n")
    args = ("gate", "--model", "m1", "--effort", "high", "--label", "L1", "--harness", "h1", "--prompt-file", str(path))
    plain = key(tmp)
    code, out, err = run(*args, "--ledger", str(Path(tmp) / "absent.jsonl"))
    require(code == 0 and out == plain, "gate prints the key and exits 0 on new work: %r %r %r" % (code, out, err))
    done = ledger(tmp, [row(out)], name="gate-finished.jsonl")
    code, gate_out, err = run(*args, "--ledger", done)
    require(code == 15 and gate_out == "" and "cell-a" in err,
            "gate refuses finished work with no key on stdout: %r %r %r" % (code, gate_out, err))
    text_args = tuple("--prompt-text-file" if a == "--prompt-file" else a for a in args)
    code, text_out, _ = run(*text_args, "--ledger", str(Path(tmp) / "absent.jsonl"))
    require(code == 0 and text_out == plain, "a prompt-text file is hashed the same as a prompt file: %r" % text_out)
    code, gate_out, _ = run(*args, "--ledger", done, "--no-check")
    require(code == 0 and gate_out == plain,
            "a resume's --no-check computes the key and skips the refusal: %r %r" % (code, gate_out))
    code, _, _ = run(*args, env={"SIDECAR_LEDGER_PATH": done})
    require(code == 15, "the default ledger is SIDECAR_LEDGER_PATH: %r" % code)
    code, gate_out, err = run("gate", "--model", "m1", "--effort", "high", "--label", "L1", "--harness", "h1",
                              "--prompt-file", str(Path(tmp) / "missing.md"))
    require(code == 2 and gate_out == "" and err.strip(),
            "an unreadable prompt file is exit 2 with its reason: %r %r" % (code, err))
    code, _, _ = run("void", "--key", "a" * 64, "--launch-id", "launch-1", "--reason", "x")
    require(code != 0, "void has no command line: %r" % code)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        keys(tmp)
        checks(tmp)
        gates(tmp)
    for f in FAILS:
        print("FAIL " + f)
    print("test_work_key: %s" % ("PASS" if not FAILS else "%d failure(s)" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
