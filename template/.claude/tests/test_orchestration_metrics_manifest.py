#!/usr/bin/env python3
"""Re-runnable proof for orchestration_metrics.py manifest mode.

The proof uses synthetic Workflow and sidecar records. It asserts exact label matching,
source separation, expanded job counts, unattested effort, and archive isolation.

    python3 .claude/tests/test_orchestration_metrics_manifest.py
"""
import contextlib
import importlib.util
import io
import json
import multiprocessing
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
spec = importlib.util.spec_from_file_location("orchestration_metrics_manifest", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


def dump(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh)


def write_workflow(session, run_id, label, model="opus", state="done", served=None):
    workflow_dir = os.path.join(session, "workflows")
    transcript_dir = os.path.join(session, "subagents", "workflows", run_id)
    os.makedirs(workflow_dir, exist_ok=True)
    os.makedirs(transcript_dir, exist_ok=True)
    dump(os.path.join(workflow_dir, run_id + ".json"), {
        "runId": run_id,
        "workflowName": "dispatch",
        "logs": ['PINS {"%s":"%s/low/general-purpose"}' % (label, model)],
        "workflowProgress": [{
            "type": "workflow_agent", "agentId": "a1", "label": label,
            "model": model, "state": state, "phaseTitle": "Dispatch",
        }],
    })
    with open(os.path.join(transcript_dir, "agent-a1.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2026-09-09T10:00:00Z",
            "message": {
                "id": "m1",
                **({"model": served} if served else {}),
                "usage": {"input_tokens": 11, "output_tokens": 7,
                          "cache_creation_input_tokens": 3, "cache_read_input_tokens": 13},
                "content": [{"type": "tool_use"}],
            },
        }) + "\n")


def write_sidecar(record_dir, label, exit_code=0):
    path = os.path.join(record_dir, label + ".record.json")
    dump(path, {
        "label": label, "transport": "anthropic", "costModel": "plan-quota",
        "servedModel": "claude-sonnet-5", "requestedModel": "claude-sonnet-5",
        "effort": "medium", "inputTokens": 17, "outputTokens": 9,
        "cacheReadTokens": 19, "numTurns": 2, "durationMs": 321,
        "costUSD": None, "exitCode": exit_code,
    })
    return path


def seed(run_key, jobs, route="parallel"):
    return {"schemaVersion": 1, "runKey": run_key, "route": route, "jobs": jobs}


def job(label, transport, currency):
    return {
        "label": label,
        "requested": {
            "role": "fanout", "model": "requested-" + label, "effort": "low",
            "transport": transport, "currency": currency,
            "agentType": "general-purpose", "shape": "any",
        },
        "spillPath": None,
    }


def expect_error(label, fn, cases):
    try:
        fn()
    except om.ManifestError as exc:
        cases.append((label, True, str(exc)))
    else:
        cases.append((label, False, "no ManifestError"))


def captured_report(rows):
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            om.report(rows)
        return True, stream.getvalue()
    except Exception as exc:
        return False, repr(exc)


def archive_worker(module_path, archive, rows, start, results):
    worker_spec = importlib.util.spec_from_file_location(
        "orchestration_metrics_archive_worker", module_path)
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    worker.ARCHIVE = archive
    worker.ARCHIVE_LOCK_TIMEOUT_SECONDS = 5.0
    start.wait()
    try:
        fresh, skipped, conflicts = worker._archive_rows_atomic(rows)
        results.put((len(fresh), skipped, conflicts))
    except Exception as exc:
        results.put(("error", repr(exc)))


def main():
    root = tempfile.mkdtemp(prefix="om_manifest_")
    session = os.path.join(root, "session")
    records = os.path.join(root, "records")
    os.makedirs(records)
    verdicts = os.path.join(root, "verdicts.json")
    archive = os.path.join(root, "archive.jsonl")
    with open(archive, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"sentinel":true}\n')
    before = open(archive, "rb").read()
    om.ARCHIVE = archive

    native_label = "rk-native"
    side_label = "rk-side"
    write_workflow(session, "wf_native", native_label)
    write_sidecar(records, side_label)
    dump(verdicts, {native_label: "clean", side_label: "defects"})

    cases = []

    mixed_seed = seed("rk", [
        job(native_label, "codex", "plan-quota"),
        job(side_label, "anthropic", "plan-quota"),
    ])
    workflow_evidence = om.collect(session)
    sidecar_evidence = om.collect_sidecar_records(records)
    manifest = om.build_manifest(
        mixed_seed,
        workflow_rows=workflow_evidence,
        sidecar_rows=sidecar_evidence,
        verdicts=om._read_verdict_file(verdicts),
    )
    cases.append(("mixed manifest keeps exact expanded job count",
                  len(manifest["jobs"]) == len(mixed_seed["jobs"]) == 2,
                  json.dumps(manifest)))
    cases.append(("mixed sources stay distinct",
                  {row["evidence"]["source"] for row in manifest["jobs"]} == {"workflow", "sidecar"},
                  json.dumps(manifest)))
    cases.append(("Workflow usage is normalized",
                  manifest["jobs"][0]["usage"]["inputTokens"] == 11
                  and manifest["jobs"][0]["usage"]["toolCalls"] == 1,
                  json.dumps(manifest["jobs"][0])))
    cases.append(("sidecar requested and served models stay distinct",
                  manifest["jobs"][1]["requested"]["model"] == "requested-rk-side"
                  and manifest["jobs"][1]["effective"]["model"] == "claude-sonnet-5"
                  and manifest["jobs"][1]["evidence"]["recordPath"].endswith("rk-side.record.json"),
                  json.dumps(manifest["jobs"][1])))
    cases.append(("requested effort is not copied into effective effort",
                  all(row["effective"]["effort"] is None for row in manifest["jobs"]),
                  json.dumps(manifest)))
    cases.append(("collector rows separate requested from observed effort",
                  all(row.get("requested_effort") in ("low", "medium")
                      and row.get("observed_effort") is None
                      for row in workflow_evidence + sidecar_evidence),
                  json.dumps(workflow_evidence + sidecar_evidence)))
    cases.append(("verdict outcomes join by exact label",
                  [row["outcome"] for row in manifest["jobs"]] == ["clean", "defects"],
                  json.dumps(manifest)))

    seed_path = os.path.join(root, "seed.json")
    output_path = os.path.join(root, "output", "manifest.json")
    dump(seed_path, mixed_seed)
    written = om.write_manifest(
        seed_path, output_path, session=session,
        sidecar_record_dir=records, verdicts_path=verdicts,
    )
    with open(output_path, encoding="utf-8") as fh:
        output = json.load(fh)
    cases.append(("write_manifest writes and returns the exact joined manifest",
                  output == written == manifest,
                  json.dumps(output)))
    cases.append(("write_manifest creates only its requested output",
                  os.path.isfile(output_path) and open(archive, "rb").read() == before,
                  open(archive, encoding="utf-8").read()))
    cases.append(("manifest mode does not touch archive",
                  open(archive, "rb").read() == before,
                  open(archive, encoding="utf-8").read()))

    workflow_only = om.build_manifest(
        seed("one", [job(native_label, "codex", "plan-quota")], route="single"),
        workflow_rows=om.collect(session), sidecar_rows=[], verdicts={},
    )
    cases.append(("Workflow-only manifest completes", workflow_only["status"] == "completed", json.dumps(workflow_only)))

    sidecar_only = om.build_manifest(
        seed("side", [job(side_label, "anthropic", "plan-quota")], route="single"),
        workflow_rows=[], sidecar_rows=om.collect_sidecar_records(records), verdicts={},
    )
    cases.append(("sidecar-only manifest completes", sidecar_only["status"] == "completed", json.dumps(sidecar_only)))

    # C11: a Workflow row attests the served model from its transcript; a pin that names a family
    # (sonnet) is honored by any id in that family, a full-id pin must match exactly.
    write_workflow(session, "wf_served_ok", "rk-served-ok", model="sonnet", served="claude-sonnet-5")
    write_workflow(session, "wf_served_bad", "rk-served-bad", model="sonnet", served="claude-opus-5")
    write_workflow(session, "wf_served_full", "rk-served-full", model="claude-sonnet-5", served="claude-opus-5")
    write_workflow(session, "wf_served_ctx", "rk-served-ctx", model="claude-opus-5[1m]", served="claude-opus-5")
    served_rows = {row["label"]: row for row in om.collect(session)}
    mixed_row = dict(served_rows["rk-served-ok"], label="rk-served-mixed", model="claude-opus-5",
                     served_model="mixed:claude-opus-4-8,claude-opus-5")
    served_rows["rk-served-mixed"] = mixed_row
    mismatch_fn = getattr(om, "model_mismatches", None)
    mismatches = mismatch_fn(list(served_rows.values())) if mismatch_fn else []
    cases.append(("a Workflow row records the served model from its transcript",
                  served_rows["rk-served-ok"].get("served_model") == "claude-sonnet-5",
                  json.dumps(served_rows["rk-served-ok"])))
    cases.append(("a Workflow row without message.model reads served_model None, key present",
                  "served_model" in served_rows[native_label] and served_rows[native_label]["served_model"] is None,
                  json.dumps(served_rows[native_label])))
    cases.append(("an alias pin served by its own family raises no mismatch",
                  mismatch_fn is not None and not any("rk-served-ok" in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("an alias pin served another family is a mismatch naming both models",
                  any("rk-served-bad" in line and "sonnet" in line and "claude-opus-5" in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("a full-id pin served a different id is a mismatch",
                  any("rk-served-full" in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("a full-id pin with a context-window suffix ([1m]) is honored by the bare id",
                  mismatch_fn is not None and not any("rk-served-ctx" in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("a mixed served set is a mismatch when any member fails the pin",
                  any("rk-served-mixed" in line and "claude-opus-4-8" in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("a row with no served model is never a mismatch",
                  not any(native_label in line for line in mismatches),
                  json.dumps(mismatches)))
    cases.append(("sidecar rows never carry served_model (regression guard)",
                  all("served_model" not in row for row in om.collect_sidecar_records(records)),
                  json.dumps(om.collect_sidecar_records(records))))
    # C20: a row claimed by a task record's active_jobs carries that task_id; an unclaimed row reads None;
    # the archive summary prints per-task totals with explicit denominators.
    tasks_dir = os.path.join(root, "tasks")
    os.environ["HARNESS_TASK_RECORD_DIR"] = tasks_dir
    tr_spec = importlib.util.spec_from_file_location("task_record", os.path.join(os.path.dirname(om.__file__), "task_record.py"))
    tr = importlib.util.module_from_spec(tr_spec)
    tr_spec.loader.exec_module(tr)
    tr.open_record("sess-task", session_id="sess", title="Task")
    tr.add_job("sess-task", run_id="wf_native", label=native_label, source="workflow", state="done")
    tr.add_job("sess-task", run_id=side_label, label=side_label, source="sidecar", state="done")
    joined = {row["label"]: row for row in om.collect(session)}
    side_joined = {row["label"]: row for row in om.collect_sidecar_records(records)}
    cases.append(("a Workflow row claimed by a record's active_jobs carries its task_id",
                  joined[native_label].get("task_id") == "sess-task", json.dumps(joined[native_label])))
    cases.append(("a sidecar row claimed by label carries its task_id",
                  side_joined[side_label].get("task_id") == "sess-task", json.dumps(side_joined[side_label])))
    cases.append(("an unclaimed row reads task_id None, key present",
                  "task_id" in joined["rk-served-ok"] and joined["rk-served-ok"]["task_id"] is None,
                  json.dumps(joined["rk-served-ok"])))
    task_manifest = om.build_manifest(
        seed("task", [job(native_label, "codex", "plan-quota")]),
        workflow_rows=list(joined.values()), sidecar_rows=[], verdicts={},
    )
    cases.append(("the manifest carries task_id through to the job",
                  task_manifest["jobs"][0].get("task_id") == "sess-task", json.dumps(task_manifest["jobs"][0])))
    totals_fn = getattr(om, "task_totals", None)
    totals = totals_fn(list(joined.values()) + list(side_joined.values())) if totals_fn else None
    cases.append(("task totals report rows joined per task and the two denominators",
                  isinstance(totals, dict) and totals.get("tasks", {}).get("sess-task", {}).get("rows") == 2
                  and "rows_without_task" in totals and "rows_without_cost" in totals
                  and totals["rows_without_task"] == len(joined) + len(side_joined) - 2,
                  json.dumps(totals)))
    os.environ.pop("HARNESS_TASK_RECORD_DIR", None)

    served_manifest = om.build_manifest(
        seed("served", [job("rk-served-ok", "codex", "plan-quota"), job(native_label, "codex", "plan-quota")]),
        workflow_rows=list(served_rows.values()), sidecar_rows=[], verdicts={},
    )
    cases.append(("manifest effective.model for a Workflow row is the served model, not the pin",
                  served_manifest["jobs"][0]["effective"]["model"] == "claude-sonnet-5"
                  and served_manifest["jobs"][1]["effective"]["model"] is None,
                  json.dumps(served_manifest)))

    expect_error("missing evidence fails instead of picking another row", lambda: om.build_manifest(
        seed("missing", [job("rk-missing", "codex", "plan-quota")]),
        workflow_rows=om.collect(session), sidecar_rows=om.collect_sidecar_records(records), verdicts={},
    ), cases)

    write_workflow(session, "wf_duplicate", native_label, model="sonnet")
    expect_error("duplicate exact label fails", lambda: om.build_manifest(
        seed("duplicate", [job(native_label, "codex", "plan-quota")]),
        workflow_rows=om.collect(session), sidecar_rows=[], verdicts={},
    ), cases)

    sparse_label = "rk-sparse"
    sparse_path = os.path.join(records, sparse_label + ".record.json")
    dump(sparse_path, {
        "label": sparse_label, "timestamp": "2026-09-09T11:00:00Z",
        "requestedModel": "requested-only", "exitCode": 0,
    })
    sparse_rows = om.collect_sidecar_records(records)
    sparse_row = next((row for row in sparse_rows if row.get("label") == sparse_label), {})
    sparse_manifest = om.build_manifest(
        seed("sparse", [job(sparse_label, "anthropic", "marginal-usd")]),
        workflow_rows=[], sidecar_rows=[sparse_row], verdicts={},
    )
    sparse_usage = sparse_manifest["jobs"][0]["usage"]
    cases.append(("missing servedModel remains unknown",
                  sparse_manifest["jobs"][0]["effective"]["model"] is None,
                  json.dumps(sparse_manifest)))
    cases.append(("missing usage remains null through row and manifest",
                  sparse_row.get("inp") is None
                  and sparse_usage["inputTokens"] is None
                  and sparse_usage["durationMs"] is None,
                  json.dumps(sparse_manifest)))

    replace_calls = []
    original_replace = om.os.replace

    def track_replace(source, destination):
        replace_calls.append((source, destination))
        return original_replace(source, destination)

    atomic_output = os.path.join(root, "atomic", "manifest.json")
    atomic_seed = os.path.join(root, "atomic-seed.json")
    dump(atomic_seed, seed("atomic", [job(side_label, "anthropic", "plan-quota")]))
    om.os.replace = track_replace
    try:
        om.write_manifest(atomic_seed, atomic_output, session=None,
                          sidecar_record_dir=records, verdicts_path=verdicts)
    finally:
        om.os.replace = original_replace
    manifest_replaces = [pair for pair in replace_calls if pair[1] == atomic_output]
    manifest_tmp = manifest_replaces[0][0] if manifest_replaces else ""
    cases.append(("write_manifest uses same-directory PID temp and os.replace",
                  len(manifest_replaces) == 1
                  and os.path.dirname(manifest_tmp) == os.path.dirname(atomic_output)
                  and str(os.getpid()) in os.path.basename(manifest_tmp),
                  repr(manifest_replaces)))

    global_pending = os.path.join(root, "global-pending.json")
    bare_output = os.path.join(root, "bare-manifest.json")
    bare_seed = os.path.join(root, "bare-seed.json")
    dump(global_pending, {side_label: "defects"})
    dump(bare_seed, seed("bare", [job(side_label, "anthropic", "plan-quota")]))
    old_pending = om.PENDING_VERDICTS
    old_legacy = om.LEGACY_PENDING_VERDICTS
    old_argv = sys.argv
    om.PENDING_VERDICTS = global_pending
    om.LEGACY_PENDING_VERDICTS = os.path.join(root, "no-legacy.json")
    sys.argv = [MODULE_PATH, "--manifest-seed", bare_seed, "--manifest-out", bare_output,
                "--sidecar-record-dir", records]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            bare_rc = om.main()
        with open(bare_output, encoding="utf-8") as fh:
            bare_manifest = json.load(fh)
    finally:
        sys.argv = old_argv
        om.PENDING_VERDICTS = old_pending
        om.LEGACY_PENDING_VERDICTS = old_legacy
    cases.append(("bare manifest does not read global pending verdicts",
                  bare_rc == 0 and bare_manifest["jobs"][0]["outcome"] is None,
                  json.dumps(bare_manifest)))

    zero_row = {
        "run": "zero", "workflow": "dispatch", "phase": "", "label": "zero",
        "effort": "low", "model": "sonnet", "state": "completed",
        "cost": None, "qcost": None, "inp": None, "out": None, "cw": None,
        "cr": None, "turns": None, "tools": None, "secs": None,
    }
    report_ok, report_detail = captured_report([zero_row])
    cases.append(("report handles null usage and zero normalized total",
                  report_ok and "cost share" in report_detail,
                  report_detail))

    known_row = dict(zero_row, run="known", label="known", cost=100.0,
                     qcost=100.0, out=7, cr=3, turns=2, tools=1, secs=1.0)
    unknown_row = dict(zero_row, run="unknown", label="unknown")
    aggregate_ok, aggregate_detail = captured_report([known_row, unknown_row])
    low_line = next((line for line in aggregate_detail.splitlines()
                     if re.match(r"^low\s+2\s+", line)), "")
    cases.append(("Workflow aggregates divide by known rows only",
                  aggregate_ok and re.match(r"^low\s+2\s+100\b", low_line) is not None,
                  aggregate_detail))
    cases.append(("Workflow aggregates report unknown counts",
                  "unknown: cost 1, turns 1, out/turn 1" in aggregate_detail,
                  aggregate_detail))

    sidecar_known = {
        "source": "sidecar", "run": "side-known", "label": "side-known",
        "model": "served", "effort": "low", "state": "completed",
        "cost_usd": 1.0, "cost_basis": "", "out": 7, "cr": 3,
        "turns": 2, "secs": 1.0,
    }
    sidecar_unknown = dict(sidecar_known, run="side-unknown", label="side-unknown",
                           cost_usd=None, out=None, cr=None, turns=None, secs=None)
    side_stream = io.StringIO()
    with contextlib.redirect_stdout(side_stream):
        om.report_sidecar([sidecar_known, sidecar_unknown])
    side_detail = side_stream.getvalue()
    cases.append(("sidecar aggregates skip unknown usage and report its count",
                  "out 7 (1 unknown)" in side_detail
                  and "turns 2 (1 unknown)" in side_detail
                  and "1 unpriced" in side_detail,
                  side_detail))

    summary_archive = os.path.join(root, "summary-archive.jsonl")
    with open(summary_archive, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({
            "run": "summary-known", "label": "summary-known", "effort": "low",
            "outcome": "clean", "cost": 100.0, "turns": 2,
        }) + "\n")
        fh.write(json.dumps({
            "run": "summary-unknown", "label": "summary-unknown", "effort": "low",
            "outcome": "clean", "cost": None, "turns": None,
        }) + "\n")
    old_archive = om.ARCHIVE
    old_candidates = om.CANDIDATES_FILE
    old_argv = sys.argv
    om.ARCHIVE = summary_archive
    om.CANDIDATES_FILE = os.path.join(root, "summary-candidates.json")
    sys.argv = [MODULE_PATH, "--archive-summary"]
    summary_stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(summary_stream):
            summary_rc = om.main()
    finally:
        sys.argv = old_argv
        om.ARCHIVE = old_archive
        om.CANDIDATES_FILE = old_candidates
    summary_detail = summary_stream.getvalue()
    cases.append(("archive summary divides by known costs and reports unknown count",
                  summary_rc == 0
                  and re.search(r"^low\s+clean\s+2\s+100\s+1$",
                                summary_detail, re.MULTILINE) is not None,
                  summary_detail))

    per_model_archive = os.path.join(root, "per-model-archive.jsonl")
    with open(per_model_archive, "w", encoding="utf-8", newline="\n") as fh:
        for run_id, model, usd in (("side-one", "m-one", 1.0), ("side-two", "m-two", 3.0)):
            fh.write(json.dumps({
                "source": "sidecar", "run": run_id, "label": run_id, "model": model,
                "effort": "low", "outcome": "clean", "cost_usd": usd, "state": "completed",
            }) + "\n")
    old_archive, old_candidates, old_argv = om.ARCHIVE, om.CANDIDATES_FILE, sys.argv
    om.ARCHIVE = per_model_archive
    om.CANDIDATES_FILE = os.path.join(root, "per-model-candidates.json")
    sys.argv = [MODULE_PATH, "--archive-summary"]
    per_model_stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(per_model_stream):
            per_model_rc = om.main()
    finally:
        sys.argv, om.ARCHIVE, om.CANDIDATES_FILE = old_argv, old_archive, old_candidates
    per_model_detail = per_model_stream.getvalue()
    cases.append(("archive summary renders one sidecar table per served model, never blended",
                  per_model_rc == 0
                  and re.search(r"^-- sidecar \[m-one\]", per_model_detail, re.MULTILINE) is not None
                  and re.search(r"^-- sidecar \[m-two\]", per_model_detail, re.MULTILINE) is not None
                  and re.search(r"^low\s+clean\s+1\s+1\.0000\s+0$", per_model_detail, re.MULTILINE) is not None
                  and re.search(r"^low\s+clean\s+1\s+3\.0000\s+0$", per_model_detail, re.MULTILINE) is not None
                  and "DeepSeek" not in per_model_detail,
                  per_model_detail))

    candidate_path = os.path.join(root, "candidates.json")
    old_candidates = om.CANDIDATES_FILE
    om.CANDIDATES_FILE = candidate_path
    replace_calls.clear()
    om.os.replace = track_replace
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            om._report_candidates([])
    finally:
        om.os.replace = original_replace
        om.CANDIDATES_FILE = old_candidates
    candidate_replaces = [pair for pair in replace_calls if pair[1] == candidate_path]
    candidate_tmp = candidate_replaces[0][0] if candidate_replaces else ""
    cases.append(("candidate writes use PID-specific temp files",
                  len(candidate_replaces) == 1
                  and str(os.getpid()) in os.path.basename(candidate_tmp),
                  repr(candidate_replaces)))

    dump(os.path.join(records, "array.record.json"), [])
    with open(os.path.join(records, "broken.record.json"), "w", encoding="utf-8") as fh:
        fh.write("{")
    try:
        sidecar_diagnostics = []
        isolated_sidecar = om.collect_sidecar_records(records, sidecar_diagnostics)
        sidecar_isolated = any(row.get("label") == side_label for row in isolated_sidecar)
    except Exception as exc:
        sidecar_isolated = False
        sidecar_detail = repr(exc)
        sidecar_diagnostics = []
    else:
        sidecar_detail = json.dumps(isolated_sidecar)
    cases.append(("sidecar record reader isolates malformed and non-object JSON",
                  sidecar_isolated
                  and {os.path.basename(row["path"]) for row in sidecar_diagnostics}
                  == {"array.record.json", "broken.record.json"}
                  and {row["status"] for row in sidecar_diagnostics} == {"malformed", "non-object"},
                  sidecar_detail + " diagnostics=" + json.dumps(sidecar_diagnostics)))

    sidecar_ledger = os.path.join(root, "sidecar-ledger.jsonl")
    with open(sidecar_ledger, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[]\n{\n")
        fh.write(json.dumps({
            "label": "ledger-good", "timestamp": "2026-09-09T12:00:00Z",
            "requestedModel": "requested-only", "exitCode": 0,
        }) + "\n")
    try:
        ledger_diagnostics = []
        ledger_rows = om.collect_sidecar(sidecar_ledger, diagnostics=ledger_diagnostics)
        ledger_isolated = (len(ledger_rows) == 1
                           and ledger_rows[0]["model"] == "?"
                           and ledger_rows[0]["inp"] is None
                           and {row["status"] for row in ledger_diagnostics} == {"malformed", "non-object"})
    except Exception as exc:
        ledger_isolated = False
        ledger_detail = repr(exc)
        ledger_diagnostics = []
    else:
        ledger_detail = json.dumps(ledger_rows)
    cases.append(("sidecar ledger isolates malformed/non-object JSON and preserves null usage",
                  ledger_isolated,
                  ledger_detail + " diagnostics=" + json.dumps(ledger_diagnostics)))
    missing_ledger_diagnostics = []
    missing_ledger_rows = om.collect_sidecar(
        os.path.join(root, "missing-sidecar.jsonl"), diagnostics=missing_ledger_diagnostics)
    cases.append(("missing sidecar ledger is named as a diagnostic",
                  missing_ledger_rows == []
                  and len(missing_ledger_diagnostics) == 1
                  and missing_ledger_diagnostics[0]["status"] == "missing",
                  json.dumps(missing_ledger_diagnostics)))

    dump(os.path.join(session, "workflows", "array.json"), [])
    with open(os.path.join(session, "workflows", "broken.json"), "w", encoding="utf-8") as fh:
        fh.write("{")
    try:
        workflow_diagnostics = []
        isolated_workflows = om.collect(session, diagnostics=workflow_diagnostics)
        workflow_isolated = any(row.get("label") == native_label for row in isolated_workflows)
    except Exception as exc:
        workflow_isolated = False
        workflow_detail = repr(exc)
        workflow_diagnostics = []
    else:
        workflow_detail = json.dumps(isolated_workflows)
    cases.append(("workflow collector isolates malformed and non-object JSON",
                  workflow_isolated
                  and {os.path.basename(row["path"]) for row in workflow_diagnostics}
                  == {"array.json", "broken.json"}
                  and {row["status"] for row in workflow_diagnostics} == {"malformed", "non-object"},
                  workflow_detail + " diagnostics=" + json.dumps(workflow_diagnostics)))

    malformed_archive = os.path.join(root, "malformed-archive.jsonl")
    with open(malformed_archive, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("[]\n{\n")
        fh.write(json.dumps({"run": "archive-good", "label": "good"}) + "\n")
    old_archive = om.ARCHIVE
    om.ARCHIVE = malformed_archive
    try:
        archive_diagnostics = []
        archived_runs, ignored_runs = om.load_run_ledger(diagnostics=archive_diagnostics)
        archive_isolated = (archived_runs == {"archive-good"} and ignored_runs == set()
                            and {row["status"] for row in archive_diagnostics} == {"malformed", "non-object"})
    except Exception as exc:
        archive_isolated = False
        archive_detail = repr(exc)
        archive_diagnostics = []
    else:
        archive_detail = repr((archived_runs, ignored_runs))
    cases.append(("archive reader isolates malformed and non-object JSON",
                  archive_isolated,
                  archive_detail + " diagnostics=" + json.dumps(archive_diagnostics)))
    om.ARCHIVE = old_archive

    archive_api_exists = hasattr(om, "_archive_rows_atomic")
    cases.append(("archive writes expose one locked idempotent transaction",
                  archive_api_exists, "_archive_rows_atomic missing"))
    if archive_api_exists:
        concurrent_archive = os.path.join(root, "concurrent.jsonl")
        ctx = multiprocessing.get_context("spawn")
        start = ctx.Event()
        results = ctx.Queue()
        rows_to_archive = [{"run": "same-run", "label": "same", "effort": "low"}]
        workers = [ctx.Process(target=archive_worker,
                               args=(MODULE_PATH, concurrent_archive, rows_to_archive,
                                     start, results)) for _ in range(2)]
        for process in workers:
            process.start()
        start.set()
        outcomes = [results.get(timeout=10) for _ in workers]
        for process in workers:
            process.join(10)
        with open(concurrent_archive, encoding="utf-8") as fh:
            archived_objects = [json.loads(line) for line in fh if line.strip()]
        cases.append(("concurrent archive attempts append one run once",
                      len(archived_objects) == 1
                      and sum(outcome[0] for outcome in outcomes) == 1,
                      repr(outcomes)))

        partial_archive = os.path.join(root, "partial-run.jsonl")
        with open(partial_archive, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"run": "partial", "label": "first", "effort": "low"}) + "\n")
        old_archive = om.ARCHIVE
        om.ARCHIVE = partial_archive
        try:
            partial_fresh, partial_skipped, partial_conflicts = om._archive_rows_atomic([
                {"run": "partial", "label": "first", "effort": "low"},
                {"run": "partial", "label": "second", "effort": "low"},
            ])
            partial_rows = list(om._archive_records())
        finally:
            om.ARCHIVE = old_archive
        cases.append(("idempotent archive resume fills a partial run population",
                      [row.get("label") for row in partial_fresh] == ["second"]
                      and partial_skipped == 1 and partial_conflicts == []
                      and {row.get("label") for row in partial_rows} == {"first", "second"},
                      repr((partial_fresh, partial_skipped, partial_conflicts, partial_rows))))

        retention_archive = os.path.join(root, "retention.jsonl")
        old_archive = om.ARCHIVE
        old_max = om.ARCHIVE_MAX_BYTES
        old_rotations = om.ARCHIVE_MAX_ROTATIONS
        om.ARCHIVE = retention_archive
        om.ARCHIVE_MAX_BYTES = 128
        om.ARCHIVE_MAX_ROTATIONS = 2
        try:
            for index in range(4):
                om._archive_rows_atomic([{
                    "run": "retained-%d" % index,
                    "label": "x" * 40,
                    "effort": "low",
                }])
            archived_runs, _ = om.load_run_ledger()
        finally:
            om.ARCHIVE = old_archive
            om.ARCHIVE_MAX_BYTES = old_max
            om.ARCHIVE_MAX_ROTATIONS = old_rotations
        cases.append(("archive retention uses bounded safe rotation",
                      os.path.exists(retention_archive + ".1")
                      and os.path.exists(retention_archive + ".2")
                      and not os.path.exists(retention_archive + ".3")
                      and "retained-1" in archived_runs,
                      repr(sorted(archived_runs))))

    # review_fanout logs a second PINS line for its consolidate step; a first-line-only read left it `?`.
    two_pins = om.efforts_for({"logs": ['PINS {"review:a":"opus/high/general-purpose"}', "other log line",
                                        'PINS {"review:consolidate":"opus/low/general-purpose"}']})
    cases.append(("every PINS line in a run resolves its labels",
                  two_pins == {"review:a": "high", "review:consolidate": "low"}, repr(two_pins)))

    passed = failed = 0
    for label, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + label)
        if ok:
            passed += 1
        else:
            failed += 1
            print("     " + detail)
    print(f"orchestration manifest: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
