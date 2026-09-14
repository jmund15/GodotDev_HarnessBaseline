#!/usr/bin/env python3
"""Proof that session archival reads only sidecar records launched by that session."""
import gc
import importlib.util
import json
import os
import tempfile
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
spec = importlib.util.spec_from_file_location("orchestration_metrics_session_sidecar", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


def dump(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle)


def record(path, label, timestamp):
    dump(path, {
        "timestamp": timestamp,
        "label": label,
        "transport": "opencode",
        "costModel": "marginal-usd",
        "servedModel": "muse-spark",
        "requestedModel": "muse",
        "effort": "low",
        "inputTokens": 11,
        "outputTokens": 7,
        "cacheReadTokens": 3,
        "numTurns": 1,
        "durationMs": 250,
        "costUSD": 0.0,
        "exitCode": 0,
    })


def tool_use(command, cwd, timestamp):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "cwd": cwd,
        "message": {
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": command},
            }],
        },
    }


def unrelated_write_peak(root, count):
    transcript = os.path.join(root, "writes-%d.jsonl" % count)
    payload = "x" * (1024 * 1024)
    with open(transcript, "w", encoding="utf-8", newline="\n") as handle:
        for index in range(count):
            row = {
                "type": "assistant", "cwd": root,
                "message": {"content": [{
                    "type": "tool_use", "name": "Write",
                    "input": {"file_path": "unrelated-%d.md" % index,
                              "content": payload + str(index)},
                }]},
            }
            handle.write(json.dumps(row) + "\n")
    gc.collect()
    tracemalloc.start()
    om._sidecar_record_launches(transcript)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


def main():
    root = tempfile.mkdtemp(prefix="om_session_sidecar_")
    session = os.path.join(root, "session-id")
    os.makedirs(session)
    transcript = session + ".jsonl"
    records = os.path.join(root, "records")
    jobs = os.path.join(root, "jobs.json")
    options_jobs = os.path.join(root, "options", "jobs.json")
    default_jobs = os.path.join(root, "default", "jobs.json")
    live_jobs = os.path.join(root, "live", "jobs.json")
    noun_jobs = os.path.join(root, "noun", "jobs.json")
    dump(jobs, [{"label": "mine"}])
    dump(options_jobs, [{"label": "options"}])
    dump(default_jobs, [{"label": "default"}])
    dump(live_jobs, [{"label": "live-only"}])
    dump(noun_jobs, [{"label": "noun-only"}])
    record(os.path.join(records, "mine.record.json"), "mine", "2026-09-10T10:01:00Z")
    record(os.path.join(records, "options.record.json"), "options", "2026-09-10T10:01:30Z")
    record(os.path.join(root, "default", "fanout", "default.record.json"),
           "default", "2026-09-10T10:01:40Z")
    record(os.path.join(root, "live", "fanout", "live-only.record.json"),
           "live-only", "2026-09-10T10:01:50Z")
    record(os.path.join(records, "noun-only.record.json"),
           "noun-only", "2026-09-10T10:01:55Z")
    record(os.path.join(records, "foreign.record.json"), "foreign", "2026-09-10T10:01:00Z")

    direct = os.path.join(root, "direct.record.json")
    stale = os.path.join(root, "stale.record.json")
    timestamp_label = os.path.join(root, "timestamp-label.record.json")
    unknown_model = os.path.join(root, "unknown-model.record.json")
    noun_direct = os.path.join(root, "noun-direct.record.json")
    same_second = os.path.join(root, "same-second.record.json")
    record(direct, "direct", "2026-09-10T10:03:00Z")
    record(stale, "stale", "2026-09-10T09:00:00Z")
    record(timestamp_label, "09", "2026-09-10T10:03:10Z")
    record(noun_direct, "noun-direct", "2026-09-10T10:03:20Z")
    record(same_second, "same-second", "2026-09-10T10:03:40Z")
    dump(unknown_model, {
        "timestamp": "2026-09-10T10:03:30Z", "label": "unknown-model",
        "requestedModel": "requested-only", "exitCode": 0,
    })

    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-09-10T09:59:00Z",
            "cwd": root,
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": jobs, "content": json.dumps([{"label": "mine"}])},
                }, {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": options_jobs,
                              "content": json.dumps([{"label": "options"}])},
                }, {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": default_jobs,
                              "content": json.dumps([{"label": "default"}])},
                }, {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": noun_jobs,
                              "content": json.dumps([{"label": "noun-only"}])},
                }],
            },
        },
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py jobs.json --out-dir records --authorize",
            root,
            "2026-09-10T10:00:00Z",
        ),
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py --authorize --max-parallel 2 "
            "--out-dir records options/jobs.json",
            root,
            "2026-09-10T10:00:30Z",
        ),
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py default/jobs.json --authorize",
            root,
            "2026-09-10T10:00:40Z",
        ),
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py live/jobs.json --authorize",
            root,
            "2026-09-10T10:00:50Z",
        ),
        tool_use(
            "python3 inspect.py .claude/tools/sidecar_fanout.py noun/jobs.json "
            "--out-dir records",
            root,
            "2026-09-10T10:00:55Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l direct -R direct.record.json",
            root,
            "2026-09-10T10:02:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l stale -R stale.record.json",
            root,
            "2026-09-10T10:02:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l 09 -R timestamp-label.record.json",
            root,
            "2026-09-10T10:03:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l unknown-model "
            "-R unknown-model.record.json",
            root,
            "2026-09-10T10:03:00Z",
        ),
        tool_use(
            "python3 inspect.py .claude/scripts/opencode_sidecar.sh -R noun-direct.record.json",
            root,
            "2026-09-10T10:03:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l same-second "
            "-R same-second.record.json",
            root,
            "2026-09-10T10:03:40.900Z",
        ),
        {
            "type": "user",
            "timestamp": "2026-09-10T10:04:00Z",
            "cwd": root,
            "message": {"role": "user", "content": [{
                "type": "text",
                "text": "bash .claude/scripts/opencode_sidecar.sh -R foreign.record.json",
            }]},
        },
    ]
    with open(transcript, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    dump(jobs, [{"label": "foreign"}])

    cases = []
    got = om.collect_session_sidecar(session)
    labels = {row["label"] for row in got}
    cases.append(("fanout record launched by this session is included", "mine" in labels))
    cases.append(("options before fanout jobs positional are parsed", "options" in labels))
    cases.append(("default fanout output is jobs-parent/fanout", "default" in labels))
    cases.append(("fanout attribution never reads uncaptured live jobs", "live-only" not in labels))
    cases.append(("launcher path used as another program argument is ignored",
                  "noun-only" not in labels and "noun-direct" not in labels))
    cases.append(("direct launcher record is included", "direct" in labels))
    cases.append(("timestamp is parsed by the final label suffix", "09" in labels))
    unknown_row = next((row for row in got if row["label"] == "unknown-model"), None)
    cases.append(("missing servedModel remains unknown",
                  unknown_row is not None and unknown_row["model"] == "?"))
    cases.append(("unmentioned record beside session output is excluded", "foreign" not in labels))
    cases.append(("record older than its launch call is excluded", "stale" not in labels))
    cases.append(("record and launch in the same whole second are accepted",
                  "same-second" in labels))
    cases.append(("every row carries exact record evidence",
                  all(row.get("record_path", "").endswith(".record.json") for row in got)))
    cases.append(("missing transcript fails closed", om.collect_session_sidecar(os.path.join(root, "absent")) == []))

    bad_session = os.path.join(root, "bad-session")
    os.makedirs(bad_session)
    bad_transcript = bad_session + ".jsonl"
    bad_record = os.path.join(root, "bad.record.json")
    dump(bad_record, [])
    with open(bad_transcript, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("[]\n{\n")
        handle.write(json.dumps(tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -R bad.record.json",
            root,
            "2026-09-10T10:05:00Z",
        )) + "\n")
    try:
        bad_rows = om.collect_session_sidecar(bad_session)
        bad_isolated = bad_rows == []
    except Exception:
        bad_isolated = False
    cases.append(("session reader isolates malformed and non-object JSON", bad_isolated))

    one_write_peak = unrelated_write_peak(root, 1)
    many_write_peak = unrelated_write_peak(root, 5)
    cases.append(("unrelated Write bodies do not accumulate in memory",
                  many_write_peak < one_write_peak * 2))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
