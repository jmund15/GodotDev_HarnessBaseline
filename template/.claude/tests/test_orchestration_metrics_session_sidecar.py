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

    # Real-session fixtures trimmed from session b9a508a5-bcd1-46de-a460-e64e9c86d48e:
    # 1. `for n in hooks-fix runner-fix; do D=.../$n; ... sidecar_fanout.py $D/jobs.json --detach ...; done`
    #    where a script wrote jobs.json and each out dir holds its fanout-*.inventory.json and record.
    # 2. Two native-only probe launches (`jobs_old.json`, `jobs_old2.json`) sharing probe/fanout/native.args.json.
    # 3. A sidecar launched via an opaque wrapper script (`bash close_call_run.sh`) registered by dispatch_table
    #    and already pruned from dispatch_table_pending after completion.
    real_root = os.path.join(root, "b9a508a5_case")
    real_sid = "b9a508a5-bcd1-46de-a460-e64e9c86d48e"
    real_session = os.path.join(real_root, real_sid)
    os.makedirs(real_session)
    real_state_dir = os.path.join(real_root, "routing_state")
    os.makedirs(real_state_dir)

    hooks_dir = os.path.join(real_root, ".claude", "scratch", "overhaul", "hooks-fix", "fanout")
    runner_dir = os.path.join(real_root, ".claude", "scratch", "overhaul", "runner-fix", "fanout")
    hooks_rec = os.path.join(hooks_dir, "hooks-fix.record.json")
    runner_rec = os.path.join(runner_dir, "runner-fix.record.json")
    dump(hooks_rec, {
        "launchId": "1b47bc30-4266-4eed-bf5f-bf33ad72ab7c",
        "parentSessionId": real_sid,
        "sessionId": "859301d1-2701-47cb-b65c-58662a40d412",
        "label": "hooks-fix",
        "transport": "codex",
        "costModel": "plan-quota",
        "servedModel": "gpt-6.2-sol",
        "requestedModel": "gpt-6.2-sol",
        "effort": "high",
        "inputTokens": 169750,
        "cacheReadTokens": 7649408,
        "cacheWriteTokens": 0,
        "outputTokens": 21381,
        "costUSD": 1.318251,
        "costBasis": "list-price (plan-quota; not spend)",
        "costTierBasis": "per-request",
        "priceMultiplier": 1.0,
        "numTurns": 90,
        "durationMs": 725961,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:22:04Z",
        "attestedModel": "gpt-6.2-sol",
        "attestedEffort": "high",
    })
    dump(os.path.join(hooks_dir, "fanout-fd9ea951-479f-4f5b-84a3-f3aa64b61b2a.inventory.json"), {
        "schemaVersion": 1,
        "fanoutId": "fd9ea951-479f-4f5b-84a3-f3aa64b61b2a",
        "parentSessionId": real_sid,
        "writtenAt": "2026-11-25T02:08:33Z",
        "jobs": [{
            "label": "hooks-fix",
            "launchId": "1b47bc30-4266-4eed-bf5f-bf33ad72ab7c",
            "recordPath": hooks_rec,
            "launcher": "codex_proxy",
            "requestedEffort": "high",
            "effort": "high",
        }],
    })
    dump(runner_rec, {
        "launchId": "51cd8ce8-6820-46d2-9960-1961aed6c525",
        "parentSessionId": real_sid,
        "sessionId": "931e3aba-ed00-4deb-b7a9-c82663a6f12c",
        "label": "runner-fix",
        "transport": "codex",
        "costModel": "plan-quota",
        "servedModel": "gpt-6.2-sol",
        "requestedModel": "gpt-6.2-sol",
        "effort": "medium",
        "inputTokens": 129672,
        "cacheReadTokens": 6724608,
        "cacheWriteTokens": 0,
        "outputTokens": 21234,
        "costUSD": 1.144145,
        "costBasis": "list-price (plan-quota; not spend)",
        "costTierBasis": "per-request",
        "priceMultiplier": 1.0,
        "numTurns": 93,
        "durationMs": 868161,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:25:35Z",
        "attestedModel": "gpt-6.2-sol",
        "attestedEffort": "medium",
    })
    dump(os.path.join(runner_dir, "fanout-6b143fe7-9c0f-40fb-be28-1ee9ac94f9ea.inventory.json"), {
        "schemaVersion": 1,
        "fanoutId": "6b143fe7-9c0f-40fb-be28-1ee9ac94f9ea",
        "parentSessionId": real_sid,
        "writtenAt": "2026-11-25T02:09:07Z",
        "jobs": [{
            "label": "runner-fix",
            "launchId": "51cd8ce8-6820-46d2-9960-1961aed6c525",
            "recordPath": runner_rec,
            "launcher": "codex_proxy",
            "requestedEffort": "medium",
            "effort": "medium",
        }],
    })
    probe_dir = os.path.join(real_root, ".claude", "scratch", "codify_b9a508a5_1", "probe")
    dump(os.path.join(probe_dir, "jobs_old.json"), [
        {"label": "probe-old-1", "kind": "reviewer", "tier": "orchestrator", "rows": {"opus": "medium"}, "probe": True}
    ])
    dump(os.path.join(probe_dir, "jobs_old2.json"), [
        {"label": "probe-old-2", "kind": "reviewer", "tier": "orchestrator", "rows": {"opus": "medium"}}
    ])
    dump(os.path.join(probe_dir, "fanout", "native.args.json"), {
        "jobs": [{"label": "probe-old-2", "model": "opus", "effort": "medium", "kind": "reviewer"}],
        "spillDir": ".claude/scratch/codify_b9a508a5_1/probe/fanout",
    })
    mixed_dir = os.path.join(real_root, ".claude", "scratch", "mixed_probe")
    dump(os.path.join(mixed_dir, "jobs.json"), [{"label": "m-native"}, {"label": "m-sidecar"}])
    dump(os.path.join(mixed_dir, "fanout", "native.args.json"), {"jobs": [{"label": "m-native"}]})
    dump(os.path.join(mixed_dir, "fanout", "sidecar.jobs.json"), {"jobs": [{"label": "m-sidecar"}]})

    script_rec = os.path.join(real_root, ".claude", "scratch", "overhaul", "sa_code", "arch", "review-fold-1.record.json")
    dump(script_rec, {
        "launchId": "44739ddd-8399-4c60-9dd8-47efef189f36",
        "parentSessionId": real_sid,
        "label": "review-fold-1",
        "transport": "anthropic",
        "costModel": "plan-quota",
        "servedModel": "claude-haiku-5-5",
        "requestedModel": "haiku",
        "effort": "low",
        "inputTokens": 100,
        "cacheReadTokens": 500,
        "cacheWriteTokens": 0,
        "outputTokens": 50,
        "costUSD": 0.01,
        "costBasis": "list-price (plan-quota; not spend)",
        "numTurns": 2,
        "durationMs": 1200,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:34:03Z",
    })
    stale_dt_rec = os.path.join(real_root, ".claude", "scratch", "overhaul", "stale-dt.record.json")
    dump(stale_dt_rec, {
        "launchId": "00000000-0000-4000-8000-000000000001",
        "parentSessionId": real_sid,
        "label": "stale-dt",
        "transport": "anthropic",
        "costModel": "plan-quota",
        "servedModel": "claude-haiku-5-5",
        "requestedModel": "haiku",
        "effort": "low",
        "inputTokens": 10,
        "outputTokens": 5,
        "numTurns": 1,
        "durationMs": 100,
        "exitCode": 0,
        "timestamp": "2026-11-25T01:00:00Z",
    })
    relaunch_dt_rec = os.path.join(real_root, ".claude", "scratch", "overhaul", "relaunch-dt.record.json")
    dump(relaunch_dt_rec, {
        "launchId": "00000000-0000-4000-8000-000000000004",
        "parentSessionId": real_sid,
        "label": "relaunch-dt",
        "transport": "anthropic",
        "costModel": "plan-quota",
        "servedModel": "claude-haiku-5-5",
        "requestedModel": "haiku",
        "effort": "low",
        "inputTokens": 10,
        "outputTokens": 5,
        "numTurns": 1,
        "durationMs": 100,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:10:00Z",
    })
    foreign_dt_rec = os.path.join(real_root, ".claude", "scratch", "overhaul", "foreign-dt.record.json")
    dump(foreign_dt_rec, {
        "launchId": "00000000-0000-4000-8000-000000000002",
        "parentSessionId": "other-session-id",
        "label": "foreign-dt",
        "transport": "anthropic",
        "costModel": "plan-quota",
        "servedModel": "claude-haiku-5-5",
        "requestedModel": "haiku",
        "effort": "low",
        "inputTokens": 10,
        "outputTokens": 5,
        "numTurns": 1,
        "durationMs": 100,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:40:00Z",
    })
    adopted_rec = os.path.join(real_root, ".claude", "scratch", "overhaul", "adopted.record.json")
    dump(adopted_rec, {
        "launchId": "00000000-0000-4000-8000-000000000003",
        "parentSessionId": "dead-parent-session",
        "label": "adopted",
        "transport": "anthropic",
        "costModel": "plan-quota",
        "servedModel": "claude-haiku-5-5",
        "requestedModel": "haiku",
        "effort": "low",
        "inputTokens": 10,
        "outputTokens": 5,
        "numTurns": 1,
        "durationMs": 100,
        "exitCode": 0,
        "timestamp": "2026-11-25T02:40:00Z",
    })

    real_rows = [
        tool_use(
            "for n in hooks-fix runner-fix; do D=.claude/scratch/overhaul/$n; "
            "python3 .claude/tools/dispatch_size.py $D/jobs.json 2>&1 | grep -E '^preflight'; "
            "python3 .claude/tools/sidecar_fanout.py $D/jobs.json --detach 2>&1 | grep -E 'detached'; done",
            real_root,
            "2026-11-25T02:08:29.226Z",
        ),
        tool_use(
            "P=.claude/scratch/codify_b9a508a5_1/probe && "
            "python3 .claude/tools/sidecar_fanout.py $P/jobs_old.json --detach 2>&1 | grep -E '^NATIVE'",
            real_root,
            "2026-11-25T02:27:04.203Z",
        ),
        tool_use(
            "P=.claude/scratch/codify_b9a508a5_1/probe && "
            "python3 .claude/tools/sidecar_fanout.py $P/jobs_old2.json --detach 2>&1 | grep -E '^NATIVE'",
            real_root,
            "2026-11-25T02:28:27.054Z",
        ),
        tool_use(
            "python3 .claude/tools/sidecar_fanout.py .claude/scratch/mixed_probe/jobs.json --detach",
            real_root,
            "2026-11-25T02:29:00.000Z",
        ),
        tool_use(
            "bash .claude/scratch/overhaul/sa_code/arch/close_call_run.sh",
            real_root,
            "2026-11-25T02:33:00.000Z",
        ),
    ]
    with open(real_session + ".jsonl", "w", encoding="utf-8", newline="\n") as handle:
        for row in real_rows:
            handle.write(json.dumps(row) + "\n")

    old_state_env = os.environ.get("HARNESS_HOOK_STATE_DIR")
    os.environ["HARNESS_HOOK_STATE_DIR"] = real_state_dir
    try:
        import sys
        hooks_dir_path = os.path.join(HERE, "..", "hooks")
        if hooks_dir_path not in sys.path:
            sys.path.insert(0, hooks_dir_path)
        import dispatch_table
        # Register an opaque-script sidecar through dispatch_table's real actual=True path, then
        # let complete() prune it from dispatch_table_pending: attribution must still find it.
        t_launch = om._iso_instant("2026-11-25T02:33:10Z").timestamp()
        t_stale = om._iso_instant("2026-11-25T02:00:00Z").timestamp()
        t_relaunch_1 = om._iso_instant("2026-11-25T02:09:00Z").timestamp()
        t_relaunch_2 = om._iso_instant("2026-11-25T02:35:00Z").timestamp()
        dt_payload = {"session_id": real_sid, "transcript_path": real_session + ".jsonl", "cwd": real_root}
        dispatch_table.register_sidecars(dt_payload, [
            {"kind": "sidecar", "id": script_rec, "record": script_rec, "fanout_dir": None,
             "task_id": None, "launched_at": t_launch, "jobs": [{"label": "review-fold-1", "record": script_rec}]},
            {"kind": "sidecar", "id": stale_dt_rec, "record": stale_dt_rec, "fanout_dir": None,
             "task_id": None, "launched_at": t_stale, "jobs": [{"label": "stale-dt", "record": stale_dt_rec}]},
            {"kind": "sidecar", "id": relaunch_dt_rec, "record": relaunch_dt_rec, "fanout_dir": None,
             "task_id": None, "launched_at": t_relaunch_1, "jobs": [{"label": "relaunch-dt", "record": relaunch_dt_rec}]},
            {"kind": "sidecar", "id": foreign_dt_rec, "record": foreign_dt_rec, "fanout_dir": None,
             "task_id": None, "launched_at": t_launch, "jobs": [{"label": "foreign-dt", "record": foreign_dt_rec}]},
            {"kind": "sidecar", "id": adopted_rec, "record": adopted_rec, "fanout_dir": None,
             "adopted_from": "dead-parent-session", "task_id": None, "launched_at": t_launch,
             "jobs": [{"label": "adopted", "record": adopted_rec}]},
        ], actual=True)
        # Second actual launch of the same record path advances its launch timestamp past the old record.
        dispatch_table.register_sidecars(dt_payload, [
            {"kind": "sidecar", "id": relaunch_dt_rec, "record": relaunch_dt_rec, "fanout_dir": None,
             "task_id": None, "launched_at": t_relaunch_2, "jobs": [{"label": "relaunch-dt", "record": relaunch_dt_rec}]},
        ], actual=True)
        # Simulate Stop pruning dispatch_table_pending once those runs finish.
        dispatch_table._update_pending(dt_payload, lambda _pending: [])

        real_collected = om.collect_session_sidecar(real_session)
        real_labels = {r["label"] for r in real_collected}
        real_uncaptured = {os.path.normcase(p) for p in om.uncaptured_fanout_jobs(real_session)}
    finally:
        if old_state_env is None:
            os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
        else:
            os.environ["HARNESS_HOOK_STATE_DIR"] = old_state_env

    cases.append(("for-loop fanout assigning D=.../$n after do attributes both hooks-fix and runner-fix",
                  {"hooks-fix", "runner-fix"} <= real_labels))
    cases.append(("for-loop fanout whose inventories attributed its records is not reported uncaptured",
                  not any("$d" in p or "hooks-fix" in p or "runner-fix" in p for p in real_uncaptured)))
    cases.append(("native-only fanout launches (jobs_old, jobs_old2) are not reported uncaptured",
                  os.path.normcase(os.path.join(probe_dir, "jobs_old.json")) not in real_uncaptured
                  and os.path.normcase(os.path.join(probe_dir, "jobs_old2.json")) not in real_uncaptured))
    cases.append(("mixed native+sidecar fanout without inventory or Write is still reported uncaptured",
                  os.path.normcase(os.path.join(mixed_dir, "jobs.json")) in real_uncaptured))
    cases.append(("opaque-script sidecar registered in dispatch_table is attributed after Stop prunes pending",
                  "review-fold-1" in real_labels))
    cases.append(("dispatch_table entry older than its launch timestamp is excluded (stale identity rule)",
                  "stale-dt" not in real_labels))
    cases.append(("relaunching the same record path advances launch timestamp and excludes the earlier record",
                  "relaunch-dt" not in real_labels))
    cases.append(("dispatch_table entry whose record carries another parentSessionId is excluded",
                  "foreign-dt" not in real_labels))
    cases.append(("adopted sidecar entry in dispatch_table is never attributed to the adopting session",
                  "adopted" not in real_labels))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
