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


def record(path, label, timestamp, parent_session="session-id"):
    dump(path, {
        "timestamp": timestamp,
        "parentSessionId": parent_session,
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
    with tempfile.TemporaryDirectory(prefix="om_session_sidecar_") as root:
        return _run(root)


def _run(root):
    session = os.path.join(root, "session-id")
    os.makedirs(session)
    transcript = session + ".jsonl"
    records = os.path.join(root, "records")
    jobs = os.path.join(root, "jobs.json")
    options_jobs = os.path.join(root, "options", "jobs.json")
    default_jobs = os.path.join(root, "default", "jobs.json")
    live_jobs = os.path.join(root, "live", "jobs.json")
    noun_jobs = os.path.join(root, "noun", "jobs.json")
    redirect_jobs = os.path.join(root, "redirect", "jobs.json")
    dump(redirect_jobs, [{"label": "redirected"}])
    record(os.path.join(records, "redirected.record.json"), "redirected", "2026-09-10T10:01:58Z")
    dump(jobs, [{"label": "mine"}])
    dump(options_jobs, [{"label": "options"}])
    dump(default_jobs, [{"label": "default"}])
    dump(live_jobs, [{"label": "live-only"}])
    var_jobs = os.path.join(root, "sub2", "live2", "jobs.json")
    dump(var_jobs, [{"label": "var-only"}])
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
    inv_jobs = os.path.join(root, "inv", "jobs.json")
    inv_out = os.path.join(root, "inv", "out")
    dump(inv_jobs, [{"label": "inv-only"}])
    record(os.path.join(inv_out, "inv-only.record.json"), "inv-only", "2026-09-10T10:01:59Z")
    record(os.path.join(inv_out, "inv-foreign.record.json"), "inv-foreign", "2026-09-10T10:01:59Z",
           parent_session="other-session")
    retry_jobs = os.path.join(root, "retry", "jobs.json")
    retry_out = os.path.join(root, "retry", "out")
    dump(retry_jobs, [{"label": "retry"}])
    record(os.path.join(retry_out, "retry.a2.record.json"), "retry", "2026-09-10T10:01:59Z")
    dump(os.path.join(retry_out, "fanout-r.inventory.json"),
         {"schemaVersion": 1, "fanoutId": "r", "parentSessionId": "session-id",
          "jobs": [{"label": "retry", "launchId": "r",
                    "recordPath": os.path.join(retry_out, "retry.a2.record.json")}]})
    for fanout_id, parent, label in (("a", "session-id", "inv-only"), ("b", "other-session", "inv-foreign")):
        dump(os.path.join(inv_out, "fanout-%s.inventory.json" % fanout_id),
             {"schemaVersion": 1, "fanoutId": fanout_id, "parentSessionId": parent,
              "jobs": [{"label": label, "launchId": fanout_id,
                        "recordPath": os.path.join(inv_out, label + ".record.json")}]})

    direct = os.path.join(root, "direct.record.json")
    stale = os.path.join(root, "stale.record.json")
    timestamp_label = os.path.join(root, "timestamp-label.record.json")
    unknown_model = os.path.join(root, "unknown-model.record.json")
    noun_direct = os.path.join(root, "noun-direct.record.json")
    same_second = os.path.join(root, "same-second.record.json")
    foreign_overwrite = os.path.join(root, "foreign-overwrite.record.json")
    record(direct, "direct", "2026-09-10T10:03:00Z")
    varpath = os.path.join(root, "sub", "recs", "varpath.record.json")
    record(varpath, "varpath", "2026-09-10T10:03:00Z")
    record(os.path.join(root, "glued.record.json"), "glued", "2026-09-10T10:03:00Z")
    record(os.path.join(root, "eqform.record.json"), "eqform", "2026-09-10T10:03:00Z")
    record(stale, "stale", "2026-09-10T09:00:00Z")
    record(timestamp_label, "09", "2026-09-10T10:03:10Z")
    record(noun_direct, "noun-direct", "2026-09-10T10:03:20Z")
    record(same_second, "same-second", "2026-09-10T10:03:40Z")
    record(foreign_overwrite, "foreign-overwrite", "2026-09-10T10:04:10Z",
           parent_session="another-session")
    dump(unknown_model, {
        "timestamp": "2026-09-10T10:03:30Z", "label": "unknown-model",
        "parentSessionId": "session-id",
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
            "cd sub2 && J=live2; python3 .claude/tools/sidecar_fanout.py $J/jobs.json --authorize",
            root,
            "2026-09-10T10:00:51Z",
        ),
        {
            "type": "assistant", "timestamp": "2026-09-10T10:00:52Z", "cwd": root,
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "name": "Write",
                "input": {"file_path": retry_jobs, "content": json.dumps([{"label": "retry"}])},
            }]},
        },
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py retry/jobs.json --out-dir retry/out --authorize",
            root,
            "2026-09-10T10:00:52Z",
        ),
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py inv/jobs.json --out-dir inv/out --authorize",
            root,
            "2026-09-10T10:00:52Z",
        ),
        tool_use(
            "python3 inspect.py .claude/tools/sidecar_fanout.py noun/jobs.json "
            "--out-dir records",
            root,
            "2026-09-10T10:00:55Z",
        ),
        {
            "type": "assistant", "timestamp": "2026-09-10T10:00:56Z", "cwd": root,
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "name": "Write",
                "input": {"file_path": redirect_jobs, "content": json.dumps([{"label": "redirected"}])},
            }]},
        },
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py redirect/jobs.json --authorize --max-parallel 4 "
            "--out-dir records > redirect/fanout.log 2>&1",
            root,
            "2026-09-10T10:00:57Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l direct -R direct.record.json",
            root,
            "2026-09-10T10:02:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l glued -Rglued.record.json",
            root,
            "2026-09-10T10:02:00Z",
        ),
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l eqform -R=eqform.record.json",
            root,
            "2026-09-10T10:02:00Z",
        ),
        tool_use(
            "cd sub && P=recs; bash .claude/scripts/opencode_sidecar.sh -m muse -l varpath -R $P/varpath.record.json",
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
        tool_use(
            "bash .claude/scripts/opencode_sidecar.sh -m muse -l foreign-overwrite "
            "-R foreign-overwrite.record.json",
            root,
            "2026-09-10T10:04:00Z",
        ),
        {
            "type": "user",
            "timestamp": "2026-09-10T10:04:20Z",
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
    authority = {"label": "identity-probe", "transport": "codex", "attestedModel": "proxy-runtime",
                 "servedModel": "client-claim", "requestedModel": "requested-pin", "exitCode": 0}
    probe_path = os.path.join(root, "identity.record.json")
    dump(probe_path, authority)
    probe = om._sidecar_record_row(probe_path)
    cases.append(("E1 metrics uses attestation instead of client or recorder", probe.get("model") == "proxy-runtime"
                  and probe.get("model_observed") is True and probe.get("identity_basis") == "attestedModel"))
    spend_path = os.path.join(root, "identity-ledger.jsonl")
    with open(spend_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(authority) + "\n")
    spend = om.collect_sidecar(spend_path)[0]
    cases.append(("E1 spend and explicit record collectors share identity authority", spend.get("model") == "proxy-runtime"
                  and spend.get("identity_basis") == "attestedModel"))
    # S9: a launcher `-Y <pick>,<spec>` lands on the record as `pinBasis`; the row reader maps it to
    # `pick`/`spec`, and records neither for an absent or illegal value (the launch itself refuses those).
    basis_path = os.path.join(root, "basis.record.json")
    dump(basis_path, dict(authority, pinBasis="cell,tight"))
    basis_row = om._sidecar_record_row(basis_path)
    cases.append(("a record's pinBasis maps to the row's pick and spec",
                  basis_row.get("pick") == "cell" and basis_row.get("spec") == "tight"))
    cases.append(("a record with no pinBasis records neither pick nor spec",
                  "pick" not in probe and "spec" not in probe))
    for value in ("cell", "vibes,tight", "cell,vague", ",tight", "cell,tight,extra"):
        bad_path = os.path.join(root, "basis-bad.record.json")
        dump(bad_path, dict(authority, pinBasis=value))
        bad_row = om._sidecar_record_row(bad_path)
        cases.append(("a malformed or illegal pinBasis (%r) records neither pick nor spec" % value,
                      "pick" not in bad_row and "spec" not in bad_row))

    got = om.collect_session_sidecar(session)
    labels = {row["label"] for row in got}
    transcript_labels = {row["label"] for row in om.collect_session_sidecar(transcript)}
    cases.append(("a .jsonl session path preserves parent ownership", transcript_labels == labels))
    cases.append(("fanout record launched by this session is included", "mine" in labels))
    cases.append(("options before fanout jobs positional are parsed", "options" in labels))
    cases.append(("default fanout output is jobs-parent/fanout", "default" in labels))
    cases.append(("fanout attribution never reads uncaptured live jobs", "live-only" not in labels))
    uncaptured_fn = getattr(om, "uncaptured_fanout_jobs", None)
    uncaptured = uncaptured_fn(session) if uncaptured_fn else None
    cases.append(("a launched fanout whose jobs file no Write captured is named, not silent",
                  uncaptured is not None
                  and os.path.normcase(live_jobs) in {os.path.normcase(p) for p in uncaptured}))
    cases.append(("an uncaptured fanout named through a leading cd and a shell variable resolves",
                  uncaptured is not None
                  and os.path.normcase(var_jobs) in {os.path.normcase(p) for p in uncaptured}))
    cases.append(("an uncaptured fanout is attributed from this session's launch inventory",
                  "inv-only" in labels))
    cases.append(("a captured fanout's attempt-numbered record comes from its inventory, not <label>.record.json",
                  "retry" in labels))
    cases.append(("another session's inventory in the same out-dir attributes nothing",
                  "inv-foreign" not in labels))
    cases.append(("a fanout its inventory attributed is not reported uncaptured",
                  uncaptured is not None
                  and os.path.normcase(inv_jobs) not in {os.path.normcase(p) for p in uncaptured}))
    cases.append(("a captured jobs file is not reported uncaptured",
                  uncaptured is not None
                  and os.path.normcase(jobs) not in {os.path.normcase(p) for p in uncaptured}))
    cases.append(("launcher path used as another program argument is ignored",
                  "noun-only" not in labels and "noun-direct" not in labels))
    cases.append(("a fanout launch whose output is redirected to a log is still attributed",
                  "redirected" in labels))
    cases.append(("shell redirections are not launcher arguments",
                  om._launcher_calls("python3 x/sidecar_fanout.py j.json --out-dir r > log 2>/dev/null")
                  == [("fanout", ["j.json", "--out-dir", "r"])]))
    cases.append(("a redirection before the executable does not hide a launch",
                  om._launcher_calls("2>/dev/null python3 .claude/tools/sidecar_fanout.py jobs.json")
                  == [("fanout", ["jobs.json"])]))
    cases.append(("fd duplication in the middle keeps later launcher arguments",
                  om._launcher_calls(
                      "python3 .claude/tools/sidecar_fanout.py j.json 2>&1 --out-dir r")
                  == [("fanout", ["j.json", "--out-dir", "r"])]))
    cases.append(("direct launcher record is included", "direct" in labels))
    cases.append(("a -R path built from a leading cd and a shell variable resolves", "varpath" in labels))
    cases.append(("a glued -R<path> is read the way getopts reads it", "glued" in labels))
    cases.append(("-R=<path> names no record (getopts reads the value as '=<path>')", "eqform" not in labels))
    cases.append(("timestamp is parsed by the final label suffix", "09" in labels))
    unknown_row = next((row for row in got if row["label"] == "unknown-model"), None)
    cases.append(("missing servedModel remains unknown",
                  unknown_row is not None and unknown_row["model"] == "?"))
    cases.append(("unmentioned record beside session output is excluded", "foreign" not in labels))
    cases.append(("record older than its launch call is excluded", "stale" not in labels))
    cases.append(("record and launch in the same whole second are accepted",
                  "same-second" in labels))
    cases.append(("newer record from another parent session is excluded",
                  "foreign-overwrite" not in labels))
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

    live_session = os.path.join(root, "live-session")
    os.makedirs(live_session)
    running = os.path.join(root, "running.record.json")
    ended = os.path.join(root, "ended.record.json")
    refused = os.path.join(root, "refused.record.json")
    preflight = os.path.join(root, "preflight.record.json")  # refused before the launcher created it
    for path in (running, ended, refused):
        open(path, "w").close()  # a launcher creates its -R record empty and fills it at exit
    for path, code in ((ended, "0"), (refused, "2"), (preflight, "12")):
        with open(path + ".exit", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(code + "\n")
    with open(live_session + ".jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for label in ("running", "ended", "refused", "preflight"):
            handle.write(json.dumps(tool_use(
                "bash .claude/scripts/opencode_sidecar.sh -l %s -R %s.record.json" % (label, label),
                root,
                "2026-09-10T10:06:00Z",
            )) + "\n")
    live_diagnostics = []
    live_rows = om.collect_session_sidecar(live_session, live_diagnostics)
    flagged = {os.path.normcase(row["path"]): row["status"] for row in live_diagnostics}
    cases.append(("an empty record with no .exit is a running launch, not a malformed input",
                  os.path.normcase(os.path.abspath(running)) not in flagged))
    ended_rows = [r for r in live_rows if r["label"] == "ended"]
    cases.append(("an empty record beside a zero .exit is an ended dispatch with no body, not a malformed input",
                  len(ended_rows) == 1 and ended_rows[0]["state"] == "no-record"
                  and ended_rows[0]["inp"] is None and ended_rows[0]["cost_usd"] is None
                  and os.path.normcase(os.path.abspath(ended)) not in flagged))
    cases.append(("an empty record beside a nonzero .exit is a failed launch, not a malformed input",
                  flagged.get(os.path.normcase(os.path.abspath(refused))) == "launch-failed"))
    cases.append(("an absent record beside a nonzero .exit is a failed launch, not a missing input",
                  flagged.get(os.path.normcase(os.path.abspath(preflight))) == "launch-failed"))

    one_write_peak = unrelated_write_peak(root, 1)
    many_write_peak = unrelated_write_peak(root, 5)
    cases.append(("unrelated Write bodies do not accumulate in memory",
                  many_write_peak < one_write_peak * 2))

    # S1: transcript_identity drops <synthetic> and sidechain rows. Rows below are cut from live
    # Claude Code transcripts (a session transcript's API-error row, a run dir's sidechain row).
    identity_a = os.path.join(root, "identity-session.jsonl")
    with open(identity_a, "w", encoding="utf-8", newline="\n") as handle:
        for row in [
            {"type": "assistant", "isSidechain": False, "isApiErrorMessage": True, "effort": None,
             "message": {"role": "assistant", "model": "<synthetic>",
                         "content": [{"type": "text", "text": "Failed to authenticate. API Error: 401"}]}},
            {"type": "assistant", "isSidechain": True, "effort": "medium",
             "message": {"role": "assistant", "model": "claude-haiku-5-5",
                         "content": [{"type": "text", "text": "delegated subagent turn"}]}},
            {"type": "assistant", "isSidechain": False, "effort": "xhigh",
             "message": {"role": "assistant", "model": "claude-sonnet-5-5",
                         "content": [{"type": "text", "text": "session turn"}]}},
        ]:
            handle.write(json.dumps(row) + "\n")
    cases.append(("a session transcript reads one model, dropping its <synthetic> and sidechain rows",
                  om.transcript_identity(identity_a) == ("claude-sonnet-5-5", "xhigh")))

    identity_b = os.path.join(root, "identity-subagent.jsonl")
    with open(identity_b, "w", encoding="utf-8", newline="\n") as handle:
        for row in [
            {"type": "assistant", "isSidechain": True, "effort": "medium",
             "message": {"role": "assistant", "model": "claude-haiku-5-5",
                         "content": [{"type": "text", "text": "subagent turn one"}]}},
            {"type": "assistant", "isSidechain": True, "effort": "medium",
             "message": {"role": "assistant", "model": "claude-haiku-5-5",
                         "content": [{"type": "text", "text": "subagent turn two"}]}},
        ]:
            handle.write(json.dumps(row) + "\n")
    cases.append(("a subagent transcript, whose every row is a sidechain, still reads its own model",
                  om.transcript_identity(identity_b) == ("claude-haiku-5-5", "medium")))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
