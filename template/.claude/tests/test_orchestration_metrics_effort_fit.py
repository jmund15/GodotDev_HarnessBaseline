"""Re-runnable proof for orchestration_metrics.py's effort-fit verdict.

`--rate --effort-fit <under|over>` and `--rate-batch` entries record whether a consumed dispatch's effort
was too low or too high, stored under the keys `effort_fit` and `effort_fit_note`. An illegal value is
refused with the legal set and writes nothing; `--accept-rest` writes no effort fit. Every case monkeypatches
the module's path constants to a temp tree; the live verdict file and archive are never touched.

    python3 .claude/tests/test_orchestration_metrics_effort_fit.py
"""
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _om_session_fixture as fixture  # noqa: E402,F401
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")

spec = importlib.util.spec_from_file_location("orchestration_metrics", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)

MODEL = "fixture-model"


def write_run(wdir, run_id, agents, pins=True):
    progress, pin_map = [], {}
    for i, (label, state) in enumerate(agents):
        progress.append({"type": "workflow_agent", "agentId": f"{run_id}-a{i}", "label": label,
                         "state": state, "model": MODEL})
        pin_map[label] = "medium"
    run = {"runId": run_id, "workflowProgress": progress,
           "logs": ["PINS " + json.dumps(pin_map)] if pins else []}
    with open(os.path.join(wdir, f"{run_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh)


def verdicts():
    return om._read_json_object(om.PENDING_VERDICTS) or {}


def run_main(argv):
    """(exit code, stdout + stderr). argparse refusals print to stderr, so both are captured."""
    real = sys.argv
    sys.argv = [MODULE_PATH] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = om.main()
    except SystemExit as exc:
        rc = exc.code
    finally:
        sys.argv = real
    return rc, buf.getvalue()


def main():
    cases = []
    root = tempfile.mkdtemp(prefix="ommfit_")
    om.PENDING_VERDICTS = os.path.join(root, "orchestration_verdicts.json")
    om.LEGACY_PENDING_VERDICTS = os.path.join(root, "legacy_verdicts.json")
    om.ARCHIVE = os.path.join(root, "orchestration_metrics.jsonl")
    with open(om.ARCHIVE, "w", encoding="utf-8"):
        pass

    session = os.path.join(root, "session")
    wdir = os.path.join(session, "workflows")
    os.makedirs(wdir)
    write_run(wdir, "wf_1", [("review:solo", "completed"), ("review:two", "completed")])
    write_run(wdir, "wf_2", [("review:x", "completed")])
    common = ["--session", session]

    # --- --rate writes the effort fit and its note ------------------------------------
    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--rate", "wf_1:review:solo", "clean", "--effort-fit", "under",
                                 "--effort-fit-note", "missed the edge case"])
    cases.append(("--rate --effort-fit under writes effort_fit and effort_fit_note",
                  rc == 0 and verdicts().get("wf_1:review:solo") == {
                      "outcome": "clean", "effort_fit": "under", "effort_fit_note": "missed the edge case"}))

    row = {"run": "wf_1", "label": "review:solo"}
    om.apply_verdict(row, verdicts().get("wf_1:review:solo"))
    cases.append(("effort_fit and effort_fit_note reach the archived row",
                  row.get("effort_fit") == "under" and row.get("effort_fit_note") == "missed the edge case"
                  and row.get("outcome") == "clean"))

    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--rate", "wf_1:review:solo", "clean", "--effort-fit", "over"])
    cases.append(("--rate --effort-fit over writes the value without a note",
                  rc == 0 and verdicts().get("wf_1:review:solo") == {"outcome": "clean", "effort_fit": "over"}))

    # --- an illegal value is refused and names the legal set -----------------------------
    for bad in ("high", "fit"):
        write_json(om.PENDING_VERDICTS, {})
        rc, out = run_main(common + ["--rate", "wf_1:review:solo", "clean", "--effort-fit", bad])
        cases.append((f"--effort-fit {bad} is refused, names the legal set, and writes nothing",
                      rc == 2 and re.search(r"choose from '?under'?, '?over'?", out) and verdicts() == {}))

    for bad in ("high", "fit"):
        try:
            rc = om.rate(session, "wf_1:review:solo", "clean", effort_fit=bad)
        except TypeError:
            rc = None
        cases.append((f"the rate API refuses effort_fit {bad} without writing", rc == 1 and verdicts() == {}))

    # --- --rate-batch: a bad entry is refused, its sibling is written -----------------------
    batch = os.path.join(root, "batch.json")
    write_json(batch, [
        {"label": "wf_1:review:solo", "outcome": "clean", "effort_fit": "over",
         "effort_fit_note": "a low pin would do"},
        {"label": "wf_2:review:x", "outcome": "clean", "effort_fit": "too-high"}])
    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--rate-batch", batch])
    got = verdicts()
    cases.append(("--rate-batch writes a valid effort_fit entry",
                  got.get("wf_1:review:solo") == {"outcome": "clean", "effort_fit": "over",
                                                  "effort_fit_note": "a low pin would do"}))
    cases.append(("--rate-batch refuses a bad effort_fit entry, names the legal set, and writes its sibling only",
                  rc == 1 and "under|over" in out and "wf_2:review:x" not in got and len(got) == 1))

    # --- --accept-rest writes no effort fit -------------------------------------------------
    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--accept-rest"])
    got = verdicts()
    cases.append(("--accept-rest writes clean with no effort_fit key",
                  rc == 0 and got.get("wf_1:review:solo") == "clean" and got.get("wf_1:review:two") == "clean"
                  and all("effort_fit" not in v for v in got.values() if isinstance(v, dict))))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
