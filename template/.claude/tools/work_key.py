#!/usr/bin/env python3
"""work_key.py — the key a paid call's work is known by, and the refusal to pay for finished work twice.

    python3 .claude/tools/work_key.py key --model M --effort E --label L [--prompt-file F | --prompt-text-file F] [--harness H]
    python3 .claude/tools/work_key.py check --key K [--ledger PATH]

`key` prints sha256 over the JSON list [caller part, model, effort, label]. The caller part is the
SIDECAR_WORK_KEY a job-starting tool declared for its unit of work, else {"brief": sha256 of the prompt FILE's
bytes, "harness": H}: the bytes the caller wrote, before any text the launcher appends. A tool that knows
what its work is (the score daemon: the cell and its judged inputs) declares it; every other launch is known
by its brief, its harness revision, its pin and its label.

`check` looks the key up in the spend ledger (default SIDECAR_LEDGER_PATH, else ~/.claude/sidecar_ledger.jsonl)
and exits 15 with one stderr line naming the matching row's timestamp, label and launchId when the ledger holds
a FINISHED row with that `workKey`; exit 0 otherwise. Finished means exitCode 0, costUSD above 0, schemaValid
not false, and no loopStop: a call that failed, spent nothing, returned an invalid result or was
stopped did not finish its work, so repeating it is not a re-pay. A missing ledger holds nothing (exit 0). A
ledger that exists but cannot be read exits 15 and says so, because spend that cannot be read is not proven new.
A row without a `workKey` never matches.

The decision rests on the ledger's recorded rows and the key, never on a reason a caller writes. Caller:
scripts/lib/sidecar_common.sh sc_gate_repay, which refuses a launch with exit 15.
"""
import argparse
import hashlib
import json
import os
import sys

REPAID = 15
KEY_ENV = "SIDECAR_WORK_KEY"


def default_ledger():
    return os.environ.get("SIDECAR_LEDGER_PATH") or os.path.join(os.path.expanduser("~"), ".claude", "sidecar_ledger.jsonl")


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def work_key(model, effort, label, prompt_file=None, harness=""):
    declared = os.environ.get(KEY_ENV)
    if declared:
        caller = declared
    else:
        caller = {"brief": _sha256_file(prompt_file) if prompt_file else None, "harness": harness or ""}
    text = json.dumps([caller, model or "", effort or "", label or ""], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def finished(row):
    cost = row.get("costUSD")
    return (row.get("exitCode") == 0 and isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0
            and row.get("schemaValid") is not False and not row.get("loopStop"))


def find_finished(key, ledger):
    """The first finished ledger row holding this key, or None. A missing ledger holds nothing; OSError when
    the ledger exists but cannot be read."""
    if not os.path.exists(ledger):
        return None
    with open(ledger, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a partial line from a concurrent append
            if isinstance(row, dict) and row.get("workKey") == key and finished(row):
                return row
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("key")
    k.add_argument("--model", required=True)
    k.add_argument("--effort", default="")
    k.add_argument("--label", default="")
    k.add_argument("--harness", default="")
    group = k.add_mutually_exclusive_group()
    group.add_argument("--prompt-file")
    group.add_argument("--prompt-text-file")
    c = sub.add_parser("check")
    c.add_argument("--key", required=True)
    c.add_argument("--ledger")
    args = parser.parse_args(argv)
    if args.cmd == "key":
        print(work_key(args.model, args.effort, args.label, args.prompt_file or args.prompt_text_file, args.harness))
        return 0
    ledger = args.ledger or default_ledger()
    try:
        row = find_finished(args.key, ledger)
    except OSError as exc:
        print("[sidecar] the spend ledger %s exists but cannot be read (%s); unchecked spend is not proven new" % (ledger, exc),
              file=sys.stderr)
        return REPAID
    if row:
        print("[sidecar] this work already finished and was paid: ledger row %s, label %s, launchId %s"
              % (row.get("timestamp"), row.get("label"), row.get("launchId")), file=sys.stderr)
        return REPAID
    return 0


if __name__ == "__main__":
    sys.exit(main())
