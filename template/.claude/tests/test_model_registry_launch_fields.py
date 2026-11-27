#!/usr/bin/env python3
"""Proof for `model_registry.py launch-fields` (the launcher's one registry read) and `price-window --fields`.

The launcher's gates share this one read. The cases plant a
registry where the branch matters (an unavailable row, a plan that does not pay, an avoided rung, an
unreadable client-window input) and call the function in process; one case sources the CLI block in
bash, because a quoting or line-ending defect is exactly what a sourced block would break on.

Run: python3 .claude/tests/test_model_registry_launch_fields.py
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _sidecar_launch import bash_exe  # noqa: E402  (Git Bash, never WSL's, which cannot open a Win32 path)
MOD = os.path.join(HERE, "..", "tools", "model_registry.py")
spec = importlib.util.spec_from_file_location("mrl", MOD)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)


def registry_file(data, name="external_models.json"):
    path = os.path.join(tempfile.mkdtemp(prefix="mrl_"), name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    return path


@contextlib.contextmanager
def registry(data):
    """`data` as the registry this process reads, put back afterwards."""
    path = registry_file(data)
    old = os.environ.get(mr.ENV_OVERRIDE)
    os.environ[mr.ENV_OVERRIDE] = path
    try:
        yield path
    finally:
        if old is None:
            os.environ.pop(mr.ENV_OVERRIDE, None)
        else:
            os.environ[mr.ENV_OVERRIDE] = old


def cli(argv, data):
    """`mr.main(argv)` against `data`: (exit code, stdout)."""
    with registry(data):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mr.main(argv)
    return code, out.getvalue()


@contextlib.contextmanager
def env(name, value):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


REASON = "planted reason: it's \"quoted\" \\ back"


def main():
    live = mr.load()
    row = next(m for m in live["models"] if m.get("alias") == "luna")
    cases = []

    # One read: the row it resolved, both windows, the cap, and the currency it spends.
    facts = mr.launch_facts("luna")
    cases.append(("the read names the resolved row",
                  facts["SC_MODEL"] == row["id"] and facts["SC_ALIAS"] == "luna"))
    cases.append(("both windows travel, so -M needs no second read",
                  isinstance(facts["SC_WINDOW_DEFAULT"], int) and isinstance(facts["SC_WINDOW_MAX"], int)
                  and facts["SC_WINDOW_MAX"] > facts["SC_WINDOW_DEFAULT"]))
    full = mr.launch_facts("sonnet")
    cases.append(("the output cap and the currency travel",
                  isinstance(full["SC_MAX_OUTPUT_REGISTRY"], int) and full["SC_COST_MODEL"] == "plan-quota"))
    cases.append(("the row's band floor travels; an absent balance floor reads empty",
                  bool(facts["SC_MIN_BAND"]) and facts["SC_MIN_BALANCE"] == ""))
    cases.append(("an available row reads available",
                  facts["SC_TRANSPORT_STATE"] == "available" and facts["SC_TRANSPORT_REASON"] == ""))

    # A plan that does not pay for the row is the resolve gate's refusal, not a field.
    try:
        mr.launch_facts("luna", plan="deepseek")
        refused = False
    except mr.RegistryError as exc:
        refused = "does not pay" in str(exc)
    cases.append(("a plan that does not pay for the row refuses", refused))

    # An unavailable row states its own reason, with no spawn to ask again.
    unavailable = copy.deepcopy(live)
    next(m for m in unavailable["models"] if m.get("alias") == "luna").update(
        status={"state": "unavailable", "reason": REASON})
    with registry(unavailable):
        facts = mr.launch_facts("luna")
    cases.append(("an unavailable row reads unavailable with its own reason",
                  facts["SC_TRANSPORT_STATE"] == "unavailable" and facts["SC_TRANSPORT_REASON"] == REASON))

    # The effort vocabulary and avoid evidence come from the row, not a second read.
    avoided = mr.launch_facts("sonnet", effort="low")
    free = mr.launch_facts("sonnet", effort="high")
    cases.append(("an avoided rung carries its evidence", bool(avoided["SC_EFFORT_AVOIDED"])))
    cases.append(("a served rung carries no avoid evidence",
                  free["SC_EFFORT_AVOIDED"] == "" and free["SC_EFFORT_ERROR"] == ""))
    cases.append(("the rung the facts were read for travels, so a later -e fails closed",
                  free["SC_FACTS_EFFORT"] == "high"))
    cases.append(("the legal list and the JSON vocabulary agree",
                  free["SC_EFFORT_LEGAL"].split("|") == json.loads(free["SC_EFFORT_VALUES_JSON"])))
    unforwardable = copy.deepcopy(live)
    next(m for m in unforwardable["models"] if m.get("alias") == "sonnet")["effortValues"] = ["ultra"]
    with registry(unforwardable):
        broken = mr.launch_facts("sonnet", effort="high")
    cases.append(("an unreadable vocabulary is a fact for its own gate, not a failed read",
                  "no effort rung its transport can forward" in broken["SC_EFFORT_ERROR"]
                  and broken["SC_EFFORT_VALUES_JSON"] == ""))

    # The Claude client's window contract travels in the same read; a bad -K is a fact for the
    # launcher's own refusal point, not a failed read.
    client = mr.launch_facts("sonnet", client="anthropic")
    cases.append(("the client window and its source travel",
                  client["SC_AC_CLIENT_MODEL"].startswith("claude-")
                  and bool(client["SC_CONTEXT_WINDOW_SOURCE"]) and isinstance(client["SC_CONTEXT_TOKENS"], int)))
    with env("SIDECAR_COMPACT_AT_TOKENS", "not-a-number"):
        bad_override = mr.launch_facts("sonnet", client="anthropic")
    cases.append(("an unreadable compact point is reported to its own gate",
                  "positive integer" in bad_override["SC_AC_ERROR"] and bad_override["SC_CONTEXT_TOKENS"] == ""))

    # The CLI block is a shell script: quoting and line endings hold, and a space/quote/backslash
    # reason survives the source unchanged.
    code, block = cli(["launch-fields", "luna"], unavailable)
    path = registry_file({}, name="facts.sh")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(block)
    sourced = subprocess.run([bash_exe(), "-c", '. "$1"; printf "%s|%s" "$SC_MODEL" "$SC_TRANSPORT_REASON"', "_", path],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    cases.append(("the CLI block sources cleanly and keeps a quoted reason intact",
                  code == 0 and sourced.stdout == row["id"] + "|" + REASON))

    # A refused argument exits 2 and names the shape it accepts and one call that works.
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            mr.main(["launch-fields", "sonnet", "--client", "codex"])
        refused = None
    except SystemExit as stop:
        refused = stop.code
    cases.append(("a bad launch-fields argument refuses with its accepted shape and an example",
                  refused == 2 and "example: model_registry.py launch-fields" in err.getvalue()))

    # price-window --fields: the gate's six fields agree with the JSON form of the same read.
    code, line = cli(["price-window", "luna", "--fields"], live)
    fields = line.rstrip("\r\n").split("\x1f")
    code_json, blob = cli(["price-window", "luna"], live)
    info = json.loads(blob)
    cases.append(("price-window --fields prints the gate's six fields",
                  code == 0 and len(fields) == 6 and fields[0] == info["window"]
                  and fields[1] == str(info["multiplier"]) and fields[4] == info["peakPolicy"]
                  and fields[5] == str((info.get("rates") or {}).get("outputPer1M", ""))))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
