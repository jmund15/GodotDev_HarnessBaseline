#!/usr/bin/env python3
"""Hook: show dispatch pins to the user; audit completed evidence on the model's Stop channel.

Evidence audit: process guard, blocking from its first commit by the explicit owner request:
"Shouldn't it be run automatically within the workflow itself?" and
"I need you to give me full confidence and fix this comprehensively."
Plan egr-a-in-run-verification.md D1 / Seam 7 owns this backstop. Every checked engine,
and every other dispatch with claims/findings, gets a recomputed verification_audit.json.
A match is silent. Missing/unavailable verification, unapplied verdicts or a mismatch
defers Stop once through decision:block / reason, naming the report and the duty to
read non-VERIFIED sources (orchestrator_action_protocol.md Step 1.5).
A VERIFIED file quote downgraded to UNVERIFIED is drift only when each refuted file's
current blob differs from its in-run fingerprint. Unchanged bytes and missing fingerprints
block. Audited outcomes live on the existing table entry, atomically claimed
before checking; the next Stop prunes it. Command checks share the registered Stop timeout,
less elapsed time and the measured report/state-write reserve. Exhausted commands are
unattempted, retain applied verdicts, and never alone cause a block. Audit crashes are advisory.

--launch   PostToolUse on Workflow | Agent | Bash (sidecar launcher or sidecar_fanout.py). The moment
           the dispatch starts: `▶ <name> · N agents`, then one line per distinct
           `model · effort · type: agent, agent`. Lines describe what RUNS: the hook waits up to
           DISPATCH_TABLE_LAUNCH_WAIT (3 s) for started agents' first API response and reads served
           model + applied effort from their transcripts in the run dir (the per-run
           workflows/<runId>.json is only written when the run ends). Args-enumerated jobs not
           started yet show their exact pins. A started agent whose label the script computes
           waits up to DISPATCH_TABLE_UNRESOLVED_WAIT (12 s) for its first answer, else shows the
           script's declared efforts (`low|medium`). Such rows, and agents a script starts later,
           print at the next Stop as `▶ <name> · update`. An effort nothing declares shows `?`.
           Sidecars show the registry version of the pinned alias. TYPE is the agent type, which
           sets how much harness the child loads. A dispatch that DECLARED a tool profile gets one
           `tools <label>:` line under the table (`_profile_lines`): the shape, the final built-ins
           and servers, the `+`/`-` delta, and `strict`, `inherited (MCP not restricted)` or
           `agent-type (<type>)`, the last stamped by an engine that launched the generated type.
           A call that declared none gets no line — that is an absent decision, never the `any`
           default — and a recorded dispatch whose profile cannot be read says `unknown`.
           Registers the dispatch as pending. The shared launcher also registers before spending,
           through the same sidecar_entries builder; record path and label dedupe both observations.
--complete Stop. Checks each finished pending dispatch against what actually served it and prints a
           line ONLY for a contradiction: `⚠ lens ran sonnet-5 · medium, asked opus · high`. A
           dispatch that ran as requested finishes silently. A sidecar whose launcher process, named
           in `<record>.pid`, is gone with no record or exit file prints `⚠ sidecar <label> ended
           without an exit file` and leaves. A sidecar whose spend-ledger finish row says
           `ended-before-result` prints `⚠ sidecar <label> ended before a result (exit <n>): <reason>`
           once and leaves. A fan-out job that never started prints `<label>: never started (<why>)`: its
           fan-out process ended before launching it, or its call wrote no inventory within FANOUT_PREFLIGHT_S.
           No event fires when background work ends, but its
           completion notification gives the main model a turn and every turn ends in Stop.
           Last, while any registered dispatch is unfinished: `⏳ still running · N · ctx 250k (25%)`
           (the session's context size, from the status line's `cc-cachestat-<sid>.json` in
           CC_CACHESTAT_DIR or the temp dir; omitted when that file is absent or unreadable), then one line
           each (`name · model · effort · label · elapsed`; a Workflow shows its agent count and
           pins ×count). Nothing unfinished, no block.
           A running sidecar that no live watcher covers ALSO blocks the Stop: nothing would wake
           this session when that sidecar needs a decision, so the reason names
           `python3 .claude/tools/watch_records.py --session --wake` to arm. Coverage is
           `watch_records._covered`; a payload with `stop_hook_active` true never blocks. A live bench
           roster this session started blocks the same way unless a session-wide watcher runs: it
           registers no sidecar entry, so `watch_records.session_rosters` finds it. Gated on one `ps -e`
           over the roster pid files.

CHANNEL: `systemMessage` — "Warning message shown to the user" (hooks.md, JSON output) — and for
`--complete` only, `decision: block` with `reason`, which reaches the model. No additionalContext.
Must stay a SYNCHRONOUS hook: an async hook's systemMessage is delivered to Claude instead.

EVIDENCE for the finish check, strongest first:
  codex sidecar      attestedModel / attestedEffort — the proxy's server-side record
  Anthropic agents   message.model from each API response; top-level `effort` as sent each turn
                     (orchestration_metrics.run_rows / transcript_identity). A model that takes no
                     effort parameter records none, which is not a contradiction.
  other sidecars     servedModel from the -R record. Effort only for the native anthropic
                     transport, from the child's own transcript (record sessionId): a proxied child
                     (codex, deepseek, opencode) records its client setting, not what the upstream
                     served. Unreported evidence is never a contradiction.
Requested pins: _dispatch_pins.dispatch_jobs, else (a Workflow whose args declare no job) the script's literal
pins from _dispatch_pins.script_jobs; sidecar launches:
orchestration_metrics.sidecar_launches (argv read by tools/sidecar_argv.py). Inherited effort: the payload's `effort.level`.

STATE: `dispatch_table_pending`, `dispatch_table_gone` and `dispatch_table_unknown` in
_hook_state.state_path(sid), written only via update_json_locked. Pending entries carry `writes`,
classified by _dispatch_pins.writes for close's author/fold wait.
Entries leave on completion; an audited entry keeps its outcome until the following Stop prunes it.
Older than 24 h they print once as stale and leave. Capped at 50. A
sidecar entry carries `issued_at`, its call's transcript time; a fan-out job's record is found by
`resolve_sidecar_record`'s identity rule, the one resolver the Stop table, the session watcher and
the re-arm hook share.

FAIL POSTURE: advisory. A crash prints nothing, no block, writes its traceback to stderr (debug log
only) and exits 0; each dispatch is checked in its own try, so one unreadable entry cannot hide the
others. The no-watcher block fails open with it: a missing decision lets the turn end, never traps
the session. Proof: tests/test_dispatch_table.py.
"""
import functools
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, HERE)
import _hook_state  # noqa: E402
from _win_path import native_path  # noqa: E402
sys.path.insert(0, TOOLS)
import sidecar_argv  # noqa: E402
from sidecar_argv import _SHELL_VAR  # noqa: E402
import watch_records  # noqa: E402
from watch_records import (ENDED, _covered, _launcher_gone, _process_line_gone, epoch as _epoch,  # noqa: E402
                           inventory_written, ledger_path, read_journal, writer_launcher_running)
from _dispatch_pins import (  # noqa: E402
    STRICT, agent_profile_request, dispatch_jobs, profile_line, profile_request, resolve_request,
    script_blobs, script_jobs, writes, author_writes)

STATE_KEY = "dispatch_table_pending"
GONE_KEY = "dispatch_table_gone"  # expand_deferred's report lines (dropped or revived fan-outs) for complete
ROSTER_OWNER_KEY = "dispatch_table_roster_owner"  # "<roster>:<pid>" -> whether this session started it; a pid's environment never changes
UNKNOWN_KEY = "dispatch_table_unknown"  # deferred fan-outs expired with no checkable producer, revived if the file appears
NO_WATCHER_REASON = (
    "%d sidecar(s) running with no live watcher, so nothing wakes you when one needs you. "
    "Run `" + watch_records.ARM_COMMAND + "` " + watch_records.ARM_CALL + ", "
    "then end the turn (reference/sidecar_dispatch.md §Procedure).")
NO_WATCHER_ROSTER_REASON = (
    "Bench roster(s) this session started are running with no live watcher (%s), so nothing wakes you when "
    "one ends or fails. Run `" + watch_records.ARM_COMMAND + "` " + watch_records.ARM_CALL + ", "
    "then end the turn (reference/sidecar_dispatch.md §Procedure).")
STALE_SECONDS = 24 * 3600
MAX_PENDING = 50
DONE_STATES = ("completed", "failed", "killed", "cancelled", "error", "stopped")
UNKNOWN = "?"
DEFAULT = "default"      # a sidecar flag left to the launcher
NO_EFFORT = "none"       # the served model takes no effort parameter
NOT_A_PIN = (UNKNOWN, DEFAULT, "inherit")
UNLABELED = "(unlabeled)"
QUIET_AGENT_SECONDS = 300  # a background agent gone from background_tasks and silent this long has ended
DIED = {"died": True}      # a sidecar whose launcher is gone without an exit file
_ISSUED = {}               # (session id, tool_use id) -> issue epoch or None, per process
_STOP_STARTED = time.monotonic()
# test_dispatch_table_verification.py --measure: 12 fixed-cost Stops, max 0.127 s.
# Twice that maximum, rounded up, reserves startup/source/report/state costs after commands.
AUDIT_RESERVE = 0.26
CHECKED_ENGINES = {"explore_fanout", "review_fanout", "doc_architecture_audit",
                   "key_reachability", "worklog_relevance"}


def _stop_deadline():
    settings = _read_json(os.path.join(os.path.dirname(HERE), "settings.json"))
    timeouts = [hook["timeout"] for group in (settings.get("hooks") or {}).get("Stop", [])
                for hook in group.get("hooks", [])
                if "dispatch_table.py" in hook.get("command", "") and "--complete" in hook["command"]]
    return _STOP_STARTED + min(timeouts or [10]) - AUDIT_RESERVE


def _audit_check(rows, root, session_id, deadline):
    import verify_claims as vc

    class Expired(Exception):
        pass

    class Context(vc._Context):
        @property
        def timeout(self):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Expired()
            return remaining

        @timeout.setter
        def timeout(self, value):
            pass

    ctx = Context(root, session_id, None, None)
    report = {"status": "pass", "counts": {v: 0 for v in (vc.VERIFIED, vc.UNVERIFIED, vc.UNCHECKED)},
              "verdicts": {}, "errors": [], "unattempted": []}
    for row in rows:
        key = row.get("id")
        if not key or key in report["verdicts"]:
            report["errors"].append("missing or duplicate source key: %s" % key)
            continue
        if row.get("root"):
            ctx.root = vc.Path(row["root"])
        else:
            ctx.root = vc.Path(root)
        items = []
        for evidence in row.get("evidence") or []:
            try:
                item = vc.check_item(evidence, ctx)
            except Expired:
                item = {"kind": "command", "verdict": vc.UNCHECKED, "reason": "unattempted"}
            if item.get("reason") == "timeout" and vc.kind_of(evidence) == "command":
                item["reason"] = "unattempted"
            if item["reason"] == "unattempted" and key not in report["unattempted"]:
                report["unattempted"].append(key)
            items.append(item)
        verdict = vc.claim_verdict([i["verdict"] for i in items], row.get("polarity"))
        report["verdicts"][key] = {"verdict": verdict, "items": items}
        report["counts"][verdict] += 1
    if report["counts"][vc.UNVERIFIED] or report['counts'][vc.UNCHECKED] or report["errors"]:
        report["status"] = "fail"
    return report


def _audit_rows(value, label="", *, mode="claims"):
    """Keep engine source keys, not consolidated display ids or output positions."""
    import post_check
    if isinstance(value, str):
        # Like the in-run reader, a text result holds claims only in fenced blocks; JSON text such as a
        # fold digest's `applied` and `deferred` rows is never a claim.
        return post_check.result_claims(label, value, preserve=True)
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        # The host's list of agents' text returns: each holds claims only in fenced blocks, like one text result.
        rows, errors = [], []
        for index, item in enumerate(value, 1):
            found, bad = _audit_rows(item, "%s#%d" % (label, index), mode=mode)
            rows.extend(found)
            errors.extend(bad)
        return rows, errors
    if isinstance(value, list):
        values = value
    elif isinstance(value, dict):
        # An item the engine stamped (its key fields and applied verdict) is one row, never a container.
        stamped = value.get("lens") is not None and value.get("index") is not None and "verdict" in value
        values = [value] if stamped else value.get("claims", value.get("findings"))
        if not isinstance(values, list):
            rows, errors = [], []
            children = value.get("digests", value)
            for name, child in children.items():
                if name in ("verification", "perLens", "counts", "perAgent", "perTask"):
                    continue
                if isinstance(child, (dict, str, list)):
                    found, bad = _audit_rows(child, name, mode=mode)
                    rows.extend(found)
                    errors.extend(bad)
            return rows, errors
    else:
        return [], []
    rows, errors = post_check.result_claims(label, values, preserve=True)
    for row in rows:
        if mode == "review":
            key = row.get("id")
        elif row.get("lens") is not None and row.get("index") is not None:
            key = "%s#%s" % (row["lens"], row["index"])
        else:
            key = row.get("__key") or row.get("id")
        row["id"] = key
    return rows, errors


def _in_run_report(verification, payload):
    """The complete report the run's close check wrote, or {}."""
    from pathlib import Path
    if not (verification and verification.get("report")):
        return {}
    record = Path(native_path(verification["report"]))
    if not record.is_absolute():
        record = Path(payload.get("cwd") or os.path.dirname(os.path.dirname(HERE))) / record
    return _read_json(str(record))


def _fold_subjects(specs, payload):
    """Fold subjects from `{root, snapshot, files, newPaths?}` specs: a sidecar's fold_jobs.json or a native
    review's args.fold.subjects. A relative root or snapshot is the dispatching session's cwd."""
    from pathlib import Path
    import fold_check
    base = Path(payload.get("cwd") or os.path.dirname(os.path.dirname(HERE)))

    def absolute(value):
        path = Path(str(value))
        return str(path if path.is_absolute() else base / path)

    return [fold_check.load_subject(absolute(spec["root"]), absolute(spec["snapshot"]), spec["files"],
                                   spec.get("newPaths") or []) for spec in specs]


def _audit_snapshot_rows(rows, subjects, report_path):
    """Replay a folded review's file evidence against the subjects' pre-fold bytes, as post_check read them."""
    from pathlib import Path
    artifacts, copied = {}, []
    for row in rows:
        evidence = []
        for item in row.get("evidence") or []:
            item = dict(item)
            for index, subject in enumerate(subjects if isinstance(item.get("file"), str) else []):
                hits = subject.hits(item["file"])
                if len(hits) != 1:
                    continue
                key = index, hits[0]
                if key not in artifacts:
                    target = Path(str(report_path) + ".snap") / str(len(artifacts))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = subject.pre_bytes(hits[0])
                    if data is None:
                        target = target.with_suffix(".absent")
                    else:
                        target.write_bytes(data)
                    artifacts[key] = str(target)
                item["artifact"] = artifacts[key]
                break
            evidence.append(item)
        copied.append(dict(row, evidence=evidence))
    return copied


def _sidecar_fold_specs(verification, payload):
    """The sidecar's recorded fold jobs, or [] when its close check read no sidecar directory."""
    from pathlib import Path
    flags = _in_run_report(verification, payload).get("source") or []
    if "--sidecar-dir" not in flags:
        return []
    directory = Path(flags[flags.index("--sidecar-dir") + 1])
    if not directory.is_absolute():
        directory = Path(payload.get("cwd") or os.path.dirname(os.path.dirname(HERE))) / directory
    jobs_path = directory / "fold_jobs.json"
    return json.loads(jobs_path.read_text(encoding="utf-8")) if jobs_path.is_file() else []


def _audit_source(entry, session, payload):
    """(rows, applied rows, verification, report path, source errors), or None for no claims."""
    from pathlib import Path
    import post_check
    kind = entry.get("kind")
    args = entry.get("audit_args") or {}
    errors, original, specs = [], [], []
    if kind == "workflow":
        host = _read_json(os.path.join(session or "", "workflows", str(entry["id"]) + ".json"))
        value = host.get("result")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        # The host names a run by its engine's meta.name, which hyphenates the script basename.
        name = (entry.get("name") or host.get("workflowName") or "").replace("-", "_")
        directory = Path(entry.get("run_dir") or os.path.join(session, "subagents/workflows", entry["id"]))
        if isinstance(value, dict) and "error" in value and "verification" not in value:
            try:
                returned = post_check._journal_results(directory)
            except (post_check.Unusable, OSError, ValueError):
                returned = None
            if returned == []:
                return None  # the engine refused its args before any agent ran: nothing came back to check
        verification = value.get("verification") if isinstance(value, dict) else None
        # The close check records the mode and source it read; replaying them keeps the recompute on the
        # same rows and keys. A report from before that record falls back to what the engines' checks read.
        in_run = _in_run_report(verification, payload) if isinstance(verification, dict) else {}
        recorded = in_run.get("source")
        mode = "review" if name == "review_fanout" or in_run.get("mode") == "review" else "claims"
        applied, bad = _audit_rows(value, mode=mode)
        errors.extend(bad)
        if isinstance(verification, dict):
            verification = dict(verification, __previewIncomplete=(value.get('delivery') or {}).get('payloadComplete') is False,
                                __nonVerifiedInline=((value.get('delivery') or {}).get('processedState') or {}).get('nonVerifiedInline'),
                                __ownSource=not (recorded and "--findings" in recorded))
        required = (name in CHECKED_ENGINES or entry.get("verification_expected") or verification is not None
                    or bool(applied) or bool(bad))
        if not required:
            return None
        try:
            flags = list(recorded or ["--workflow-dir", str(directory)])
            if "--workflow-dir" in flags:
                named = Path(flags[flags.index("--workflow-dir") + 1])
                if named.resolve() != directory.resolve():
                    raise ValueError("in-run source names another run: %s" % named)
            if mode == "review":
                source_args = post_check.build_parser().parse_args([
                    "review", *flags, "--out", str(directory / "unused.json")])
                src = post_check.read_source(source_args)
                original = src.rows
                errors.extend(str(e) for e in src.errors)
                for job in src.jobs:
                    for row in job.get("rows") or []:
                        for candidate in original:
                            if candidate.get("id") == row.get("id"):
                                candidate["root"] = str(job["subject"].root)
            else:
                if not recorded and args.get("spillDir"):
                    spill = Path(args["spillDir"])
                    if not spill.is_absolute():
                        spill = Path(payload.get("cwd") or os.path.dirname(os.path.dirname(HERE))) / spill
                    flags += ["--spill-dir", str(spill)]
                found, bad, _ = post_check.claims_rows(post_check.build_parser().parse_args([
                    "claims", *flags, "--out", str(directory / "unused.json")]))
                original.extend(found)
                errors.extend(bad)
        except (post_check.Unusable, OSError, ValueError) as exc:
            errors.append(str(exc))
        except SystemExit:
            errors.append("in-run source flags do not parse: %s" % flags)
        if not original and applied:
            # Structured engines may consolidate directly; check every returned item, and keep
            # the source-read error rather than pretending the original set was recovered.
            original = applied
        if mode == "review" and isinstance(value, dict):
            counts = value.get("counts") or {}
            # `raw` counts the host's valid source findings; `total` counts consolidated views, fewer after a merge.
            # The journal also holds the findings the engine rejected, so recovery reads `raw` plus `rejected`.
            raw = counts.get("raw", counts.get("total"))
            if raw is not None:
                raw += counts.get("rejected") or 0
                if raw != len(original):
                    errors.append("host source count %s differs from %s recovered source findings" % (raw, len(original)))
        report_path = directory / "verification_audit.json"
        specs = ((args.get("fold") or {}).get("subjects") or []) if mode == "review" else []
    elif kind == "sidecar":
        record = entry.get("record")
        if not record:
            return None
        checked = Path(record + ".claims.json")
        if not checked.is_file():
            # Raw capture is only a presence detector; never a checked deliverable.
            import dispatch_result
            raw = Path(record + ".out")
            if not raw.is_file():
                return None
            capture = raw.read_text(encoding="utf-8", errors="replace")
            text = dispatch_result.final_message(capture) or capture
            if not post_check._BLOCK.search(text):
                return None
            import watch_records
            driver = watch_records.inventory_process(record, entry.get("inventory"))
            if driver and watch_records._process_line_gone(driver) is False:
                return None  # its fan-out driver writes the checked file after the seat exits
            value = {}
            errors.append("claims block exists but checked deliverable is absent: %s" % checked)
        else:
            value = json.loads(checked.read_text(encoding="utf-8"))
        applied, bad = _audit_rows(value)
        original = applied
        errors.extend(bad)
        verification = value.get("verification") if isinstance(value, dict) else None
        report_path = Path(record + ".verification_audit.json")
        try:
            specs = _sidecar_fold_specs(verification, payload)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            errors.append("fold snapshot audit unavailable: %s" % exc)
    elif kind == "agent":
        transcript = Path(session) / "subagents" / ("agent-%s.jsonl" % entry["id"])
        text = ""
        with transcript.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("type") == "assistant":
                    content = (row.get("message") or {}).get("content") or []
                    if isinstance(content, list):
                        last = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                        if last:
                            text = last
        applied, bad = _audit_rows(text, _label(entry) or "agent")
        if not applied and not bad:
            return None
        original, errors, verification = applied, bad, None
        report_path = transcript.with_suffix(".verification_audit.json")
    else:
        return None
    if specs:
        import fold_check
        try:
            original = _audit_snapshot_rows(original, _fold_subjects(specs, payload), report_path)
        except (OSError, ValueError, KeyError, TypeError, IndexError, fold_check.Unusable) as exc:
            errors.append("fold snapshot audit unavailable: %s" % exc)
    return original, applied, verification, report_path, errors


def _command_moved(old, new):
    before, now = old.get('inputs'), new.get('inputs')
    return (old.get('kind') == 'command' and old.get('verdict') == 'VERIFIED'
            and new.get('reason') == 'output-differs' and bool(before) and isinstance(now, dict)
            and before.keys() == now.keys() and before != now)


def _file_drift(before, checked):
    if checked['verdict'] != 'UNVERIFIED':
        return False
    original = before.get('items') or []
    current = checked['items']
    if len(original) != len(current) or not current:
        return False
    changed = False
    for old, new in zip(original, current):
        if new['verdict'] == 'VERIFIED':
            continue
        if new['kind'] == 'command':
            if not _command_moved(old, new):
                return False
            changed = True
            continue
        if (new['kind'] not in ('file', 'url') or new['verdict'] != 'UNVERIFIED'
                or new.get('reason') not in ('quote-absent', 'missing-file')
                or old.get('verdict') != 'VERIFIED' or not old.get('blobHash') or not old.get('path')
                or old['path'] != new.get('path') or old['blobHash'] == new.get('blobHash')):
            return False
        changed = True
    return changed


def _compare_applied(report, applied, verification, in_run):
    """Add to `report["mismatches"]` each way the verdicts the run applied differ from the audit's recompute."""
    supplied = (verification or {}).get("verdicts") or {}
    bounded = verification is not None and 'nonVerified' in verification
    if bounded:
        stored = in_run.get('verdicts') or {}
        supplied = {key: value.get('verdict') for key, value in stored.items() if isinstance(value, dict)}
        expected_ids = [key for key, verdict in supplied.items() if verdict != 'VERIFIED']
        inline_ids = verification.get('nonVerified')
        ids_match = (isinstance(inline_ids, list) and inline_ids == expected_ids[:len(inline_ids)]
                     if verification.get('__nonVerifiedInline') is False else inline_ids == expected_ids)
        if (not in_run or in_run.get('status') != verification.get('status')
                or in_run.get('counts') != verification.get('counts') or not ids_match):
            report['mismatches'].append('bounded verification differs from complete report')
    applied_by_key = {row["id"]: row for row in applied}
    if len(applied_by_key) != len(applied):
        report["mismatches"].append("duplicate applied source keys")
    if set(supplied) != set(report["verdicts"]):
        report["mismatches"].append("verification source keys differ")
    preview = bounded and verification.get('__previewIncomplete')
    # A fold job's items are its author's claims, not the findings file its check read: there every
    # applied key must still hold a verdict, but not every verdict is applied.
    partial = preview or (verification and verification.get('__ownSource') is False)
    if (set(applied_by_key) - set(report['verdicts']) if partial else set(applied_by_key) != set(report['verdicts'])):
        report["mismatches"].append("applied source keys differ")
    expected_counts = {v: list(supplied.values()).count(v) for v in report["counts"]}
    if verification and verification.get("counts") != expected_counts:
        report["mismatches"].append("verification counts differ")
    if verification and verification.get("status") == "pass" and (expected_counts["UNVERIFIED"] or expected_counts['UNCHECKED']):
        report["mismatches"].append("pass contains non-VERIFIED claims")
    for key, checked in report["verdicts"].items():
        before = supplied.get(key)
        row = applied_by_key.get(key)
        if not (partial and row is None) and (row is None or row.get("verdict") != before or row.get("confidence") != ("verified" if before == "VERIFIED" else "unverified")):
            report["mismatches"].append("%s: verdict/confidence not applied" % key)
        items = checked["items"]
        if key in report["unattempted"]:
            # Preserve the applied item only where no completed item already refutes it.
            if before in report["counts"] and not any(i["verdict"] == "UNVERIFIED" for i in items):
                report["counts"][checked["verdict"]] -= 1
                checked["verdict"] = before
                report["counts"][before] += 1
        if checked["verdict"] != before:
            drift = before == "VERIFIED" and _file_drift((in_run.get('verdicts') or {}).get(key) or {}, checked)
            if drift:
                report["drift"].append(key)
            else:
                report["mismatches"].append("%s: applied %s, recomputed %s" % (key, before, checked["verdict"]))


def _verifier_gap(entry, verification, in_run, report_path, payload):
    """Why the run's close check produced no verdicts: its status, the error text its report carries (or
    that the report is unreadable), and the command that reruns the check over the run's directory."""
    status = (verification or {}).get("status")
    named = (verification or {}).get("report")
    errors = [str(error) for error in in_run.get("errors") or []]
    if named and not in_run:
        errors.append("its report is unreadable at %s (resolved against %s)"
                      % (named, payload.get("cwd") or os.path.dirname(os.path.dirname(HERE))))
    gap = {"status": status or "absent", "errors": errors}
    if entry.get("kind") == "workflow":
        mode = "review" if (entry.get("name") or "").replace("-", "_") == "review_fanout" else "claims"
        directory = os.path.dirname(str(report_path))
        gap["rerun"] = "python3 .claude/tools/post_check.py %s --workflow-dir %s --out %s" % (
            mode, directory, os.path.join(directory, "verification_rerun.json"))
    return gap


def _verification_audit(entry, session, payload):
    """Recompute, persist, then defer Stop once when D1's comparison requires it."""
    source = _audit_source(entry, session, payload)
    if source is None:
        return None
    path = _hook_state.state_path(payload.get("session_id"))
    claimed, existing = [], []
    key = entry.get("kind"), entry.get("id")

    def claim(state):
        for current in state.get(STATE_KEY) or []:
            if (current.get("kind"), current.get("id")) != key:
                continue
            if current.get("verification_audit"):
                existing.append(current["verification_audit"])
            else:
                current["verification_audit"] = {"outcome": "running"}
                claimed.append(True)
            break
    _hook_state.update_json_locked(path, claim)
    if not claimed:
        if existing:
            entry["verification_audit"] = existing[0]
        return None
    rows, applied, verification, report_path, errors = source
    root = (entry.get("audit_args") or {}).get("root") or payload.get("cwd") or os.path.dirname(os.path.dirname(HERE))
    report = _audit_check(rows, root, payload.get("session_id"), payload.get("_audit_deadline", _stop_deadline()))
    report["errors"].extend(errors)
    report.update(drift=[], mismatches=[], dispatchId=entry["id"])
    in_run = _in_run_report(verification, payload)
    if not verification or verification.get("status") not in ("pass", "fail"):
        report["verifier"] = _verifier_gap(entry, verification, in_run, report_path, payload)
    else:
        _compare_applied(report, applied, verification, in_run)
    report["outcome"] = "block" if report["mismatches"] or report["errors"] or report.get("verifier") else "match"
    if report["errors"] or report["counts"]["UNVERIFIED"] or report['counts']['UNCHECKED']:
        report["status"] = "fail"
    from pathlib import Path
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    _hook_state.write_json_atomic(str(report_path), report)
    outcome = {"outcome": report["outcome"], "report": str(report_path)}

    def finish(state):
        for current in state.get(STATE_KEY) or []:
            if (current.get("kind"), current.get("id")) == (entry.get("kind"), entry.get("id")):
                current["verification_audit"] = outcome
    _hook_state.update_json_locked(path, finish)
    entry["verification_audit"] = outcome
    if report["outcome"] != "block":
        return None
    counts = ", ".join("%s=%s" % (v, n) for v, n in report["counts"].items())
    gap = report.get("verifier")
    if gap:
        return ("Verification audit: the verifier did not run (%s), so no claim carries a verdict. %s%s Report: %s. "
                "Read the source of every claim before acting (commands/agents/orchestrator_action_protocol.md "
                "Step 1.5). Recomputed against the current text, which a fold may have rewritten: %s.%s" % (
                    "status: " + gap["status"] if verification else "no verification block",
                    "In-run error: %s." % "; ".join(gap["errors"]) if gap["errors"] else "No error text was recorded.",
                    " Rerun it: %s." % gap["rerun"] if gap.get("rerun") else "",
                    report_path, counts,
                    " Source errors: %s." % "; ".join(report["errors"]) if report["errors"] else ""))
    return ("Verification audit: %s. %s. Report: %s. Read the source of every non-VERIFIED claim before acting "
            "(commands/agents/orchestrator_action_protocol.md Step 1.5).%s" % (
                counts, "%s mismatch(es), %s source error(s)" % (len(report["mismatches"]), len(report["errors"])), report_path,
                " Unattempted commands (applied verdict retained): " + ", ".join(report["unattempted"]) if report["unattempted"] else ""))


def _om():
    sys.path.insert(0, TOOLS)
    import orchestration_metrics
    return orchestration_metrics


def _mr():
    sys.path.insert(0, TOOLS)
    import model_registry
    return model_registry


def _session_dir(payload):
    t = str(payload.get("transcript_path") or "")
    return t[:-len(".jsonl")] if t.endswith(".jsonl") else None


def _effort_level(payload):
    e = payload.get("effort")
    return e.get("level") if isinstance(e, dict) else None


def short(model):
    """`claude-haiku-4-5-20251001` → `haiku-4-5`; `claude-opus-5-5[1m]` → `opus-5-5`."""
    if not model:
        return UNKNOWN
    m = re.sub(r"\[[^\]]*\]$", "", str(model))
    m = re.sub(r"-\d{8}$", "", m)
    return m[len("claude-"):] if m.startswith("claude-") else m


LABELS_SHOWN = 8


def _table(title, rows):
    """`▶ title`, then one line per distinct model · effort · type with its agents after it.

    No column alignment: the same plain text reaches the terminal and the phone, and the phone
    prefixes every line, so fewer lines that wrap cleanly read better than padded columns."""
    groups = {}
    for label, model, effort, agent_type in rows:
        groups.setdefault((str(model), str(effort), str(agent_type)), []).append(str(label))
    lines = ["▶ " + title]
    for (model, effort, agent_type), labels in groups.items():
        shown = ", ".join(labels[:LABELS_SHOWN]) + (" +%d" % (len(labels) - LABELS_SHOWN) if len(labels) > LABELS_SHOWN else "")
        lines.append("  %s · %s · %s: %s" % (model, effort, agent_type, shown))
    return "\n".join(lines)


def _launch_wait():
    """Seconds the launch hook may block the tool call. Effort exists nowhere until an agent's
    first API response, and a slow model answers well after a fast one, so the table never waits
    for every response."""
    return float(os.environ.get("DISPATCH_TABLE_LAUNCH_WAIT") or 3)


def _read_transcript(path):
    """(served model, recorded effort, responded) from an agent transcript. `responded` is True once
    an assistant record exists; a responded transcript with no effort means the model takes none."""
    if not os.path.isfile(path):
        return None, None, False
    served, effort = _om().transcript_identity(path)
    return served, effort, bool(served)


def _live_agents(run_dir, skip=()):
    """Agents a running Workflow has STARTED, in start order: [{id, label, type, requested, served,
    effort, responded}], from the run dir Claude Code writes as each agent starts (the per-run
    workflows/<runId>.json only appears when the run ends)."""
    if not run_dir or not os.path.isdir(run_dir):
        return []
    order = []
    for e in read_journal(run_dir)["events"]:
        if e["type"] == "started" and e["agentId"] and e["agentId"] not in order:
            order.append(e["agentId"])
    metas = {os.path.basename(p)[len("agent-"):-len(".meta.json")]: p
             for p in glob.glob(os.path.join(glob.escape(run_dir), "agent-*.meta.json"))}
    order += [aid for aid in sorted(metas) if aid not in order]
    agents = []
    for aid in order:
        if aid in skip:
            continue
        try:
            with open(metas[aid], encoding="utf-8") as fh:
                meta = json.load(fh)
        except (KeyError, OSError, ValueError):
            continue
        served, effort, responded = _read_transcript(os.path.join(run_dir, "agent-%s.jsonl" % aid))
        agents.append({"id": aid, "label": meta.get("description") or aid, "type": meta.get("agentType") or "?",
                       "requested": meta.get("model"), "served": served, "effort": effort, "responded": responded})
    return agents


QUIET_SECONDS = 0.75


def _unresolved_wait():
    """Seconds the launch may wait for agents whose effort only their first answer can show."""
    return float(os.environ.get("DISPATCH_TABLE_UNRESOLVED_WAIT") or 12)


def launch_grace():
    """Seconds after a launch call before its table entry is surely written: the launch wait plus the unresolved wait.
    `tools/watch_records.py`'s bare `--await-launch` waits this long."""
    return _launch_wait() + _unresolved_wait()


def _wait_for(predicate, seconds=None):
    """The value of `predicate()` — a (done, value) pair — once done, or when the wait runs out."""
    deadline = time.time() + (_launch_wait() if seconds is None else seconds)
    while True:
        done, value = predicate()
        if done or time.time() >= deadline:
            return value
        time.sleep(0.25)


def _wait_for_agents(run_dir, expected):
    """The started agents once the set is complete — `expected` agents, or (count unknown) no new agent
    for QUIET_SECONDS — and every started agent has responded, or when the launch wait runs out."""
    seen = {"count": -1, "since": time.time()}

    def settled():
        agents = _live_agents(run_dir)
        now = time.time()
        if len(agents) != seen["count"]:
            seen["count"], seen["since"] = len(agents), now
        all_in = (len(agents) >= expected) if expected else (agents and now - seen["since"] >= QUIET_SECONDS)
        return bool(all_in and all(a["responded"] for a in agents)), agents

    return _wait_for(settled)


def _job_for(label, jobs):
    """The args job a started agent belongs to: same label, or an engine prefix (`review:<key>`)."""
    for j in jobs:
        if label == j["label"] or str(label).endswith(":" + j["label"]):
            return j
    return None


def _row(label, display_model, pin_effort, agent_type, recorded=None, responded=False, model_for_effort=None):
    """One table row. Effort, first found: `recorded` → `none` once the agent responded without one, or
    for a model that takes no effort → `pin_effort` (a `?` pin stays `?`) → `?`."""
    if recorded:
        effort = recorded
    elif responded or not _mr().takes_effort(model_for_effort or display_model):
        effort = NO_EFFORT
    else:
        effort = pin_effort or UNKNOWN
    return (label, display_model, effort, agent_type)


def _agent_row(agent, jobs, declared=None):
    """(row, settled) for a started agent. Settled: it has answered, or its effort is an exact pin, so
    the row will not change; an unsettled row is re-reported at the next Stop.

    Model: the served id, else what the same pin resolved to for another agent of this run that
    already answered (`declared["models"]`), else the pin. Effort per `_row`, the pin taken from the
    args job → the script's literal pin for this label → the script's only literal effort → every
    literal effort the script declares (`low|medium`), for labels the script computes at runtime."""
    declared = declared or {}
    job = _job_for(agent["label"], jobs) or {}
    pin = job.get("model") or agent["requested"]
    model = agent["served"] or (declared.get("models") or {}).get(pin) or _resolved_id(pin) or pin
    candidates = (job.get("effort"), (declared.get("by_label") or {}).get(agent["label"]), declared.get("only"))
    pin_effort = next((c for c in candidates if c not in (None, UNKNOWN)), None)
    exact = pin_effort is not None
    if not exact and declared.get("all"):
        pin_effort = "|".join(declared["all"])
    row = _row(agent["label"], short(model), pin_effort, agent["type"], recorded=agent["effort"],
               responded=agent["responded"], model_for_effort=model)
    return row, bool(agent["responded"] or exact)


def _declared(payload, agents):
    """What the dispatch declared beyond args: literal efforts in the script, and each model pin's
    resolved id. A pin resolves the same way for every agent of a run, so one answered agent settles
    it for all."""
    try:
        text = "\n".join(t for _, t in script_blobs(payload)[0])
    except Exception:
        text = ""
    by_label = _om().efforts_for({"script": text, "logs": []}) if text else {}
    literals = set(re.findall(r"\beffort\s*:\s*['\"`](\w+)['\"`]", text))
    models = {}
    for a in agents:
        if a["served"] and a["requested"]:
            models.setdefault(a["requested"], a["served"])
    return {"by_label": by_label, "only": next(iter(literals)) if len(literals) == 1 else None,
            "all": sorted(literals), "models": models}


def _resolved_id(pin):
    """The registry id `pin` (alias or id) names, or None when it names none (`inherit`, `?`, a typo)."""
    try:
        return _mr().resolve(pin)["id"]
    except Exception:
        return None


def _shown(pin):
    """The table's model cell for a pin no agent has answered yet: its registry version, shortened,
    so every row of a run shows one version per model (`opus` → `opus-5-5`); an unknown pin as written."""
    return short(_resolved_id(pin) or pin)


def shown_model(pin):
    """`_shown` for a caller outside this module."""
    return _shown(pin)


def workflow_pins(jobs):
    """`<model> · <effort> ×<count>, ...` for a Workflow's jobs, in first-seen order; empty with no jobs."""
    pins = {}
    for j in jobs or []:
        key = "%s · %s" % (_shown(j.get("model")), j.get("effort") or UNKNOWN)
        pins[key] = pins.get(key, 0) + 1
    return ", ".join("%s ×%d" % item for item in pins.items())


def workflow_finished(status):
    """True when a Workflow host record's `status` is a terminal state."""
    return status in DONE_STATES


def _registry_version(alias):
    """The registry's `version` (else id) for a sidecar alias: the model the launcher sends."""
    rid = _resolved_id(alias)
    if not rid:
        return alias
    try:
        return (_mr().row_by_id(rid) or {}).get("version") or rid
    except Exception:
        return rid


def _sidecar_shown(model):
    """A sidecar row's model cell: the launcher default as written, else the registry version."""
    return model if model == DEFAULT else short(_registry_version(model))


# ---------------------------------------------------------------- launch

def _jobs(payload):
    """[{label, model, effort, record?}] — `effort` is the value the job runs at: its pin, the engine
    default, the inherited session level, or UNKNOWN."""
    tool = payload.get("tool_name")
    if tool == "Bash":
        launches = _om().sidecar_launches(str((payload.get("tool_input") or {}).get("command") or ""),
                                         payload.get("cwd"))
        return [{"label": s.get("label") or UNLABELED, "model": s.get("model") or DEFAULT,
                 "effort": s.get("effort") or DEFAULT, "launcher": s.get("launcher"),
                 "record": s.get("record") if s.get("kind") != "fanout" else None,
                 "fanout_dir": os.path.dirname(s["record"]) if s.get("kind") == "fanout" and s.get("record") else None,
                 "kind": s.get("kind"), "jobs_path": s.get("jobs_path"), "out_dir": s.get("out_dir"),
                 "missing": s.get("missing")} for s in launches]
    level = _effort_level(payload) or UNKNOWN
    raw = dispatch_jobs(payload) or (script_jobs(payload) if tool == "Workflow" else [])
    out = []
    for j in raw:
        effort = j.get("effort")
        agent_type = j.get("agentType")
        out.append({"label": j["label"],
                    "model": j.get("model") if j.get("model") not in (None, "unresolved") else
                    ("inherit" if tool == "Agent" else UNKNOWN),
                    # A Workflow engine fills no omitted effort (it refuses the job); an Agent inherits the session's.
                    "effort": UNKNOWN if effort == "unresolved" else (effort or (level if tool == "Agent" else UNKNOWN)),
                    "agentType": UNKNOWN if agent_type == "unresolved" else (agent_type or "general-purpose")})
    return out


def _workflow_name(payload):
    resp = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
    ti = payload.get("tool_input") or {}
    path = str(ti.get("scriptPath") or ti.get("name") or "")
    return resp.get("workflowName") or (os.path.splitext(os.path.basename(path))[0] if path else "Workflow")


def _profile_lines(payload):
    """One `tools <label>:` line per dispatch that DECLARED a tool profile, or [] when none did.

    Silence is the honest reading of a call that named no shape, but it must not be mistaken for
    the `any` default: `_dispatch_pins.profile_request` returns None there, and a recorded dispatch
    whose profile cannot be read is reported `unknown` rather than assumed. Native rows are
    `inherited` — the runtime restricts no per-child MCP — while a sidecar's `-G`/`-t` on the
    command line is the same strict profile its record carries, so the two are reconciled once the
    record exists.
    """
    out = []
    tool = payload.get("tool_name")
    if tool in ("Workflow", "Agent"):
        try:
            if tool == "Agent":
                # A standalone Agent declares its profile in the PROMPT header; the row carries no
                # args to read it from, so the header is the only place it can come from -- and an
                # absent header is the `any` row, because that IS the shape a caller who named none
                # inherits. A present-but-usable-shaped header still wins; a malformed one raises into
                # the caller's handler rather than being read as "no profile".
                ti = payload.get("tool_input") or {}
                request = agent_profile_request(ti) or {"shape": None, "add": [], "remove": []}
                out.append(profile_line(str(ti.get("description") or "agent"),
                                        resolve_request(request)))
                return out
            for job in dispatch_jobs(payload):
                # Per JOB, not per call: one job whose profile cannot resolve must not erase the
                # lines of the jobs beside it, which reads exactly like a call that declared none.
                try:
                    request = profile_request(job)
                    if request:
                        out.append(profile_line(job["label"], resolve_request(request)))
                except Exception as exc:
                    out.append("  tools %s: unresolved (%s)" % (job.get("label"), exc))
        except Exception as exc:
            out.append("  tools: unreadable (%s)" % exc)
        return out
    if payload.get("tool_name") != "Bash":
        return out
    command = str((payload.get("tool_input") or {}).get("command") or "")
    try:
        invocations = sidecar_argv.launcher_invocations(command)
    except Exception as exc:
        return ["  tools: unreadable (%s)" % exc]
    for inv in invocations:
        flags = sidecar_argv.sidecar_flags(inv.args)
        shape = flags.get("G") if isinstance(flags.get("G"), str) else None
        tools = flags.get("t") if isinstance(flags.get("t"), str) else ""
        if not shape and not tools:
            continue
        label = flags.get("l") if isinstance(flags.get("l"), str) else UNLABELED
        try:
            out.append(profile_line(label, resolve_request(
                {"shape": shape, "toolsCsv": tools}, enforcement=STRICT)))
        except Exception as exc:
            out.append("  tools %s: unresolved (%s)" % (label, exc))
    return out


def sidecar_entries(payload, jobs=None, now=None):
    """The entry builder shared by PostToolUse and the launcher's actual argv path."""
    jobs = _jobs(payload) if jobs is None else jobs
    now = time.time() if now is None else now
    resp = payload.get("tool_response") or {}
    unreadable = [j for j in jobs if j.get("kind") == "fanout" and not j.get("fanout_dir") and not j.get("missing")]
    deferred = [j for j in jobs if j.get("kind") == "fanout" and not j.get("fanout_dir") and j.get("missing")]
    trackable = [j for j in jobs if not _SHELL_VAR.search(j.get("record") or j["label"])
                 and (j.get("record") or j["label"] != UNLABELED) and j not in unreadable and j not in deferred]
    call = payload.get("tool_use_id") or str(now)
    issued = _call_issued_at(payload.get("session_id"), call, payload.get("transcript_path"))
    if issued is None and payload.get("tool_use_id"):
        from sidecar_dispatch_context import CHECKS_KEY
        state = _hook_state.read_json_salvage(_hook_state.state_path(payload.get("session_id"))) or {}
        issued = next((r["entry"].get("issued_at") for r in state.get(CHECKS_KEY) or []
                       if r.get("call") == call), None)
    entries = [{"kind": "sidecar", "id": _fanout_entry_id(j["fanout_dir"], j["label"], call)
                if j.get("fanout_dir") else (j.get("record") or j["label"]),
                "record": j.get("record"), "fanout_dir": j.get("fanout_dir"),
                "task_id": None, "launched_at": now, "jobs": [j],
                "call": payload.get("tool_use_id")} for j in trackable]
    entries += [{"kind": "sidecar", "id": "deferred:%s:%s" % (j["jobs_path"], call), "record": None,
                 "fanout_dir": None, "task_id": resp.get("backgroundTaskId"), "launched_at": now, "jobs": [j],
                 "deferred": {"jobs_path": j["jobs_path"], "out_dir": j["out_dir"], "call": call}}
                for j in deferred]
    for entry in entries:
        entry["writes"] = writes("Bash", payload.get("tool_input") or {})
        if issued is not None:
            entry["issued_at"] = issued
    untracked = ["⚠ %s: not tracked — its record path names a shell variable the hook cannot expand"
                 % j["label"] for j in jobs if _SHELL_VAR.search(j.get("record") or j["label"])]
    untracked += ["⚠ %s: not tracked — sidecar_fanout cannot read this jobs file, so it launches nothing"
                  % j["label"] for j in unreadable]
    untracked += ["%s: not written yet when this call returned; its jobs are tracked once it appears" % j["label"]
                  for j in deferred]
    return entries, untracked


def same_sidecar(left, right, session_id, pending):
    """Use the resolver's record path and label, including fan-out inventory ownership."""
    if left.get("kind") != "sidecar" or right.get("kind") != "sidecar":
        return False
    a = resolve_sidecar_record(left, session_id, pending)
    b = resolve_sidecar_record(right, session_id, pending)
    if a and b:
        return (os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
                and _label(left) == _label(right))
    return left.get("id") == right.get("id")


def register_sidecars(payload, entries, actual=False):
    """Merge hook and launcher observations under the state lock; keep one row per run. Returns True when
    every entry was already registered by this same tool call (a second hook run for it)."""
    repeat = []

    def merge(pending):
        for entry in entries:
            existing = next((old for old in pending if same_sidecar(old, entry, payload.get("session_id"),
                                                                   pending + entries)), None)
            if actual:
                entry["call"] = existing.get("call") if existing else None
            repeat.append(bool(existing and entry.get("call") and existing.get("call") == entry["call"]))
            if existing is None:
                pending.append(entry)
                continue
            if not actual and entry.get("call"):
                existing["call"] = entry["call"]
            if actual:
                if existing.get("fanout_dir"):
                    existing.update(record=entry["record"], jobs=entry["jobs"], writes=entry["writes"])
                else:
                    pending[pending.index(existing)] = entry
            elif entry.get("fanout_dir") and not existing.get("fanout_dir"):
                entry["launched_at"] = existing["launched_at"]
                pending[pending.index(existing)] = entry
        return pending
    _update_pending(payload, merge, raising=True)
    return bool(repeat) and all(repeat)


def launch(payload):
    tool = payload.get("tool_name")
    if tool not in ("Workflow", "Agent", "Bash"):
        return None
    resp = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
    jobs = _jobs(payload)
    now = time.time()
    session = _session_dir(payload)
    if tool == "Workflow":
        run_id = resp.get("runId")
        run_dir = resp.get("transcriptDir") or os.path.join(session or "", "subagents", "workflows", str(run_id))
        agents = _wait_for_agents(run_dir, len(jobs) or None) if run_id else []
        declared = _declared(payload, agents)
        if any(not _agent_row(a, jobs, declared)[1] for a in agents):
            # An agent with no exact pin shows its real effort only once it answers: wait for it.
            agents = _wait_for(lambda: (all(x["responded"] for x in _live_agents(run_dir)),
                                        _live_agents(run_dir)), _unresolved_wait())
            declared = _declared(payload, agents)
        rows, reported = [], []
        for agent in agents:
            row, settled = _agent_row(agent, jobs, declared)
            rows.append(row)
            if settled:  # an unsettled row is re-reported at the next Stop
                reported.append(agent["id"])
        started = {_job_for(a["label"], jobs)["label"] for a in agents if _job_for(a["label"], jobs)}
        # Enumerated jobs not started yet: their pins are exactly what the engine will send.
        rows += [_row(j["label"], _shown(j["model"]), j["effort"], j["agentType"], model_for_effort=j["model"])
                 for j in jobs if j["label"] not in started and j["effort"] != UNKNOWN]
        reported += ["label:" + j["label"] for j in jobs]
        total = max(len(jobs), len(agents))
        title = "%s · %d agent%s" % (_workflow_name(payload), total, "" if total == 1 else "s")
        if not jobs:
            title = "%s · %d started" % (_workflow_name(payload), len(agents))
        raw_args = (payload.get("tool_input") or {}).get("args") or {}
        if isinstance(raw_args, str):
            raw_args = json.loads(raw_args)
        audit_args = {k: raw_args[k] for k in ("root", "spillDir", "fold") if k in raw_args}
        entries = [{"kind": "workflow", "id": run_id, "task_id": resp.get("taskId"), "launched_at": now,
                    "name": _workflow_name(payload), "run_dir": run_dir, "reported": reported,
                    "declared": declared, "jobs": jobs, "audit_args": audit_args,
                    "verification_expected": bool(raw_args.get("__returns")) or any(
                        isinstance(job, dict) and (job.get("fold") or job.get("ledger"))
                        for job in raw_args.get("jobs") or [])}] if run_id else []
    elif tool == "Agent":
        title = "Agent"
        entries = [{"kind": "agent", "id": resp.get("agentId"), "task_id": None, "launched_at": now,
                    "jobs": jobs}] if resp.get("agentId") else []
        transcript = os.path.join(session or "", "subagents", "agent-%s.jsonl" % resp.get("agentId"))

        def responded_yet():
            read = _read_transcript(transcript)
            return read[2] or not resp.get("agentId"), read

        served, effort, responded = _wait_for(responded_yet)
        served = served or resp.get("resolvedModel")
        rows = [_row(j["label"], short(served) if served else _shown(j["model"]), j["effort"], j["agentType"],
                     recorded=effort, responded=responded, model_for_effort=served or j["model"]) for j in jobs]
    else:
        if not jobs:
            return None
        launchers = {j["launcher"] for j in jobs}
        title = "%s sidecar" % (launchers.pop() if len(launchers) == 1 else "mixed")
        entries, untracked = sidecar_entries(payload, jobs, now)
        rows = [_row(j["label"], _sidecar_shown(j["model"]),
                     j["effort"], "sidecar", model_for_effort=j["model"]) for j in jobs]
    for entry in entries:
        entry["writes"] = writes(tool, payload.get("tool_input") or {})
    lines = [_table(title, rows) if rows else
             "▶ %s — the script computes its pins; each agent's row prints when it starts" % title]
    lines.extend(_profile_lines(payload))
    lines.extend(untracked if tool == "Bash" else [])
    still_pending = []
    for entry in entries:
        # Workflows always launch async, and a background agent's transcript exists from its first
        # turn, so only a foreground result or a finished sidecar record can be complete here.
        if entry["kind"] == "workflow" or (entry["kind"] == "agent" and resp.get("status") != "completed"):
            still_pending.append(entry)
            continue
        found = _contradictions(entry, session, payload, foreground=True)
        if found is None:
            still_pending.append(entry)
        else:
            lines.extend(found)
            if _audit_source(entry, session, payload) is not None:
                still_pending.append(entry)
    if still_pending:
        if tool == "Bash":
            if register_sidecars(payload, still_pending) and len(still_pending) == len(entries):
                return None
        else:
            fresh = {(e["kind"], e["id"]) for e in still_pending}
            _update_pending(payload, lambda pending: [e for e in pending if (e.get("kind"), e.get("id")) not in fresh]
                            + still_pending)
    return "\n".join(lines)


# ---------------------------------------------------------------- completion

def _model_ok(requested, resolved, served, transport=None):
    """True / False, or None when there is nothing to compare."""
    if not served or requested in NOT_A_PIN:
        return None
    strip = lambda m: re.sub(r"\[[^\]]*\]$", "", str(m or ""))
    if resolved and strip(resolved) == strip(served):
        return True
    requested_id = _resolved_id(requested)
    if requested_id:
        try:
            if (_mr().row_by_id(served, transport=transport) or {}).get("id") == requested_id:
                return True
        except Exception:
            pass
    if str(requested).lower() in re.split(r"[-.:/_]", str(served).lower()):
        return True
    return not _om().model_mismatches([{"model": requested, "served_model": served}])


def _effort_ok(requested, served):
    if not served or requested in NOT_A_PIN:
        return None
    return served == requested


def _contradiction(job, resolved, served_model, served_effort, recorded, transport=None):
    """The ⚠ line for one job, or None when it ran as requested (or nothing contradicts it).
    `recorded`: the effort came from a transcript, so its absence means the model takes none."""
    model, effort = job.get("model") or UNKNOWN, job.get("effort") or UNKNOWN
    if _model_ok(model, resolved, served_model, transport=transport) is not False and _effort_ok(effort, served_effort) is not False:
        return None
    ran_effort = served_effort or (NO_EFFORT if recorded else UNKNOWN)
    return "⚠ %s ran %s · %s, asked %s · %s" % (job.get("label"), short(served_model), ran_effort,
                                               short(model) if model not in NOT_A_PIN else model, effort)


def _background_ids(payload, kind):
    ids = set()
    for t in payload.get("background_tasks") or []:
        if isinstance(t, dict) and (kind is None or t.get("type") == kind):
            ids.add(str(t.get("id")))
            if t.get("description"):
                ids.add(str(t.get("description")))
    return ids


def _contradictions(entry, session, payload, foreground=False):
    """None while the dispatch is still running; once finished, its ⚠ lines ([] when it ran as asked)."""
    kind = entry.get("kind")
    job = (entry.get("jobs") or [{}])[0]
    if kind == "workflow":
        return _workflow_contradictions(entry, session, payload)
    if kind == "agent":
        if not session:
            return None
        transcript = os.path.join(session, "subagents", "agent-%s.jsonl" % entry["id"])
        if not os.path.isfile(transcript):
            return None
        if not foreground and not _agent_finished(transcript, payload, {entry["id"], job.get("label")}):
            return None
        served, effort = _om().transcript_identity(transcript)
        line = _contradiction(job, None, served, effort, recorded=True)
        return [line] if line else []
    if kind == "sidecar":
        data = _sidecar_record(entry, payload)
        if data is None:
            return None
        if data is DIED:
            return ["⚠ sidecar %s ended without an exit file: its launcher was killed, so no record will "
                    "be written" % (job.get("label") or UNLABELED)]
        if data.get("outcome") == ENDED:
            return ["⚠ sidecar %s ended before a result (exit %s): %s" % (
                job.get("label") or UNLABELED, data.get("exitCode"), data.get("reason") or "no reason recorded")]
        if data.get("attestedModel") or data.get("attestedEffort"):
            served, effort, recorded = data.get("attestedModel") or data.get("servedModel"), data.get("attestedEffort"), False
        else:
            served = data.get("servedModel")
            served = served if isinstance(served, str) or not served else "mixed:" + ",".join(served)
            effort, recorded = None, False
            # Only a native anthropic child talks to the model itself. A proxied child (codex,
            # deepseek, opencode) records its own client setting, not what the upstream served.
            # The record's transport is the paying plan; any plan of the anthropic route is native.
            plan = data.get("transport") or job.get("launcher")
            native = _route_of(plan) == "anthropic"
            child = _transcript(data.get("sessionId"), plan) if native else None
            if child:
                child_model, effort = _om().transcript_identity(child)
                served, recorded = served or child_model, True
        line = _contradiction(job, data.get("requestedModel"), served, effort, recorded, transport=data.get("transport"))
        return [line] if line else []
    return None


def _workflow_contradictions(entry, session, payload):
    if not session:
        return None
    journal = os.path.join(session, "workflows", "%s.json" % entry["id"])
    if not os.path.isfile(journal):
        return None
    try:
        with open(journal, encoding="utf-8", errors="replace") as fh:
            run = json.load(fh)
    except ValueError:
        return None  # still being written at run end; the next Stop reads it whole
    status = (run if isinstance(run, dict) else {}).get("status")
    if status:
        if not workflow_finished(status):
            return None
    elif entry.get("task_id") in _background_ids(payload, "workflow"):
        return None
    requested = {j["label"]: j for j in entry.get("jobs") or []}
    lines = []
    for r in _om().run_rows(session, entry["id"]):
        job = dict(requested.get(r["label"]) or {"label": r["label"]})
        job.setdefault("model", r.get("model") or UNKNOWN)
        if job.get("effort") in (None, UNKNOWN) and r.get("requested_effort") not in (None, "?"):
            job["effort"] = r["requested_effort"]
        line = _contradiction(job, r.get("model"), r.get("served_model"), r.get("served_effort"), recorded=True)
        if line:
            lines.append(line)
    return lines


def resolve_sidecar_record(entry, session_id, pending=None):
    """A fan-out job's record: the `recordPath` its label has in the inventory its own launch wrote.

    Identity rule: an inventory belongs to the latest call issued before it was written, among this
    session's calls for its out dir and label, and a job's record is its label's path in the
    earliest inventory that belongs to its call. A call's issue time is its tool_use row in the
    session transcript, written before any PreToolUse hook or the command runs, so a launch never
    writes its inventory before its own call, and an inventory written earlier is an earlier
    launch's. `launched_at` plays no part: PostToolUse stamps it after a foreground call has
    finished or timed out, minutes after the fan-out wrote its inventory.

    A job whose record ended (its `.exit` exists) and whose call owns a later inventory naming its
    label resolves to that inventory's record: relaunching the same jobs file into the same out dir
    is the re-plan path (`sidecar_fanout.attempt_record_path`), and a process no hook saw may run it.

    `pending` supplies the other calls that can own an inventory. An entry whose issue time cannot
    be read stays unresolved. The answer is kept on the entry as `record` and `inventory`.
    """
    if not entry.get("fanout_dir"):
        return entry.get("record")
    record = entry.get("record")
    if record and not os.path.isfile(record + ".exit"):
        return record
    after = inventory_written(entry) if record else None
    if record and after is None:
        return record
    for written, path, found, launcher in _owned_records(entry, session_id, pending):
        if after is not None and written <= after:
            continue
        entry["record"], entry["inventory"] = found, path
        if launcher:
            for job in entry.get("jobs") or []:
                job["launcher"] = launcher
        if not os.path.isfile(found + ".exit"):
            break
        after = written
    return entry.get("record")


def _owned_records(entry, session_id, pending):
    """[(written, inventory, record, launcher)] for each inventory owned by `entry`'s call that names its
    label, oldest first (`resolve_sidecar_record`'s identity rule). `launcher` is None for an inventory
    written before jobs named one."""
    issued = _issued_at(entry, session_id)
    label, fanout_dir = _label(entry), os.path.normcase(os.path.abspath(entry["fanout_dir"]))
    rivals = [other for other in pending or ()
              if other is not entry and other.get("fanout_dir") and _label(other) == label
              and os.path.normcase(os.path.abspath(other["fanout_dir"])) == fanout_dir]
    if issued is None:
        # A launch whose hook payload carried no call id has no issue time. Alone for its out dir and
        # label, every inventory naming the label belongs to it; with a rival the owner is unknowable.
        if rivals:
            return []
        issued = 0.0
    later = [t for t in (_issued_at(other, session_id) for other in rivals) if t is not None and t > issued]
    owned = []
    for written, path in sorted(_inventories(entry["fanout_dir"])):
        if written < issued or any(t <= written for t in later):
            continue
        inventory = _read_json(path)
        if inventory.get("parentSessionId") != session_id:
            continue
        for job in inventory.get("jobs") or []:
            if job.get("label") == label and isinstance(job.get("recordPath"), str):
                record = job["recordPath"]
                if not os.path.isabs(record):
                    # An older inventory holds the path relative to the fan-out's cwd, not this
                    # hook's; the fan-out writes every record beside its inventory.
                    record = os.path.join(os.path.dirname(os.path.abspath(path)), os.path.basename(record))
                launcher = job.get("launcher")
                owned.append((written, path, record, launcher if isinstance(launcher, str) else None))
                break
    return owned


# A false "never started" tells the owner a launched fan-out failed; a late notice costs only the wait. So the
# window is a multiple of the slowest measured preflight (52 s over 57 fan-out runs, issue to inventory write),
# not its typical time: 600 s is over 10x that. A change in the preflight (process-table scan, machine lock)
# needs a re-measure before this moves.
FANOUT_PREFLIGHT_S = 600
# A refused call whose label a later call launched is answered by that launch: the entry leaves without a line.
SUPERSEDED = "superseded"
# The command that writes the jobs file runs before the fan-out in the same call, so the file lands inside the
# same window the fan-out has to write its inventory: one bound, not a second guess. Past it, the producing
# call's liveness decides (`expand_deferred`), so a late file costs a keep, never a drop.
DEFERRED_GIVE_UP_S = FANOUT_PREFLIGHT_S


def _deferred_jobs(jobs_path):
    """(jobs, why): the parsed jobs list, or None with why it is missing (absent, or refused)."""
    try:
        with open(jobs_path, encoding="utf-8") as fh:
            return _om().fanout_spec_jobs(json.load(fh), jobs_path), "its jobs file was refused"
    except OSError:
        return None, "its jobs file never appeared"
    except ValueError:
        return None, "its jobs file was refused"


def _spill_dir(jobs_path):
    try:
        with open(jobs_path, encoding="utf-8") as fh:
            spill = json.load(fh).get("spillDir")
    except (OSError, ValueError, AttributeError):
        return None
    return spill if isinstance(spill, str) and spill else None


def _expanded(entry, labeled):
    d = entry["deferred"]
    # The launch read no spillDir from a file not yet written; sidecar_fanout refuses an --out-dir that differs from it.
    spill = _spill_dir(d["jobs_path"])
    out_dir = sidecar_argv.fanout_out_dir(d["jobs_path"], None, spill, _om()._PROJECT_DIR) if spill else d["out_dir"]
    return [{"kind": "sidecar", "id": _fanout_entry_id(out_dir, job.get("label"), d["call"]),
             "record": None, "fanout_dir": out_dir, "task_id": None,
             "writes": bool(entry.get("writes") or author_writes(job)),
             "launched_at": entry.get("launched_at"), "issued_at": entry.get("issued_at"),
             "jobs": [{"label": job.get("label"), "model": job.get("alias") or DEFAULT,
                       "effort": job.get("effort") or DEFAULT,
                       "launcher": str(job.get("transport") or "sidecar_fanout"), "record": None,
                       "fanout_dir": out_dir, "kind": "fanout"}]} for job in labeled]


def expand_deferred(session_id, now=None, live=None):
    """Replace each deferred fan-out entry whose jobs file now parses with one entry per job (the shape a
    readable launch registers); drop one whose file lists no labeled job. A file still absent or refused after
    DEFERRED_GIVE_UP_S (a writer may be mid-write until then) is judged by its producing call, the background
    task `task_id`, against `live` (the ids a Stop payload's `background_tasks` lists; None for a reader
    without them, which keeps the entry for one that has them): still running keeps the entry until
    STALE_SECONDS; ended means the fan-out launched nothing. With no task id, or past STALE_SECONDS, the
    outcome is unknown and the entry moves to UNKNOWN_KEY, where it expands if its file appears before
    STALE_SECONDS. Each outcome is queued under GONE_KEY as a report line for the next
    `complete`, whichever reader decided it. Readers go through `read_table`, which calls this first."""
    now = time.time() if now is None else now
    notes = []

    def change(entries, unknown):
        notes.clear()
        out, parked = [], []
        for e in entries:
            d = e.get("deferred") if isinstance(e, dict) else None
            if not d:
                out.append(e)
                continue
            name = os.path.basename(d["jobs_path"])
            jobs, why = _deferred_jobs(d["jobs_path"])
            labeled = [job for job in jobs or [] if job.get("label")]
            if jobs is not None:
                out += _expanded(e, labeled)
                if not labeled:
                    notes.append("%s: its jobs file lists no labeled job, so the fan-out launched nothing" % name)
                continue
            age = now - float(e.get("launched_at") or now)
            task = e.get("task_id")
            if age <= DEFERRED_GIVE_UP_S or (task and (live is None or str(task) in live) and age <= STALE_SECONDS):
                out.append(e)
            elif task and live is not None and str(task) not in live:
                notes.append("%s: %s, so the fan-out launched nothing" % (name, why))
            else:
                parked.append(e)
                notes.append("%s: %s after %d min, and its producing call cannot be checked, so whether the "
                             "fan-out launched is unknown; its jobs are tracked if the file appears"
                             % (name, why, DEFERRED_GIVE_UP_S // 60))
        for e in unknown:
            name = os.path.basename(e["deferred"]["jobs_path"])
            jobs, _why = _deferred_jobs(e["deferred"]["jobs_path"])
            labeled = [job for job in jobs or [] if job.get("label")]
            if labeled:
                out += _expanded(e, labeled)
                notes.append("%s: its jobs file appeared late; its %d job(s) are tracked now" % (name, len(labeled)))
            elif jobs is None and now - float(e.get("launched_at") or now) <= STALE_SECONDS:
                parked.append(e)
        return out, parked

    state = _hook_state.read_json_salvage(_hook_state.state_path(session_id)) or {}
    if state.get(UNKNOWN_KEY) or any(isinstance(e, dict) and e.get("deferred") for e in state.get(STATE_KEY) or []):
        def updater(state):
            pending, parked = change(state.get(STATE_KEY) or [], state.get(UNKNOWN_KEY) or [])
            state[STATE_KEY] = pending[-MAX_PENDING:]
            if parked:
                state[UNKNOWN_KEY] = parked[-MAX_PENDING:]
            else:
                state.pop(UNKNOWN_KEY, None)
            if notes:
                state[GONE_KEY] = (state.get(GONE_KEY) or []) + notes
        _hook_state.update_json_locked(_hook_state.state_path(session_id), updater)


def read_table(session_id, live=None):
    """This session's hook state with every deferred fan-out entry expanded or dropped: the one way to
    read the pending table, so no reader sees a deferred entry that can never resolve. `live` is
    expand_deferred's set of running background task ids, when the caller has one."""
    expand_deferred(session_id, live=live)
    return _hook_state.read_json_salvage(_hook_state.state_path(session_id)) or {}


def _take_gone(path):
    gone = []

    def updater(state):
        gone[:] = state.pop(GONE_KEY, None) or []
    _hook_state.update_json_locked(path, updater)
    return gone


def _label(entry):
    return (entry.get("jobs") or [{}])[0].get("label")


def _inventories(fanout_dir):
    """[(mtime, path)] for every fan-out inventory in `fanout_dir`."""
    found = []
    for path in glob.glob(os.path.join(glob.escape(fanout_dir), "fanout-*.inventory.json")):
        try:
            found.append((os.path.getmtime(path), path))
        except OSError:
            continue
    return found


def _read_json(path):
    """The JSON object at `path`, or {} when it is absent, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fanout_entry_id(fanout_dir, label, call):
    return "%s:%s:%s" % (fanout_dir, label, call)


def _issued_at(entry, session_id):
    """Epoch seconds at which the call that registered `entry` was issued, or None.

    Launch stamps `issued_at`. A fan-out entry without it (registered before the stamp existed)
    is looked up by the call its id names."""
    if entry.get("issued_at") is not None:
        return float(entry["issued_at"])
    prefix = _fanout_entry_id(entry.get("fanout_dir"), _label(entry), "")
    ident = str(entry.get("id") or "")
    if not entry.get("fanout_dir") or not ident.startswith(prefix):
        return None
    issued = _call_issued_at(session_id, ident[len(prefix):])
    if issued is not None:
        entry["issued_at"] = issued
    return issued


def _call_issued_at(session_id, call, transcript=None):
    """The timestamp of the assistant row whose tool_use block has id `call`, or None when the
    transcript (`transcript`, else the session's own under the projects root) has no such row."""
    key = (session_id, call)
    if key not in _ISSUED:
        path = transcript if transcript and os.path.isfile(transcript) else _transcript(session_id)
        _ISSUED[key] = _tool_use_time(path, call) if path and call else None
    return _ISSUED[key]


def _tool_use_time(path, call):
    needle = call.encode("utf-8")
    try:
        with open(path, "rb") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
                if row.get("type") == "assistant" and isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") == call
                        for b in content):
                    return _epoch(row.get("timestamp")) or None
    except OSError:
        return None
    return None


def _never_started(entry, payload):
    """Why this job will never start, or None: its record has no launcher pid and the fan-out process
    whose inventory allocated it is gone, or its fan-out call ended without writing an inventory."""
    record = resolve_sidecar_record(entry, payload.get("session_id"), payload.get("_pending_sidecars"))
    if not record and entry.get("fanout_dir"):
        return _launched_nothing(entry, payload)
    if not record or not entry.get("inventory") or os.path.isfile(record + ".pid"):
        return None
    inventory = _read_json(entry["inventory"])
    if _process_line_gone(inventory.get("fanoutProcess")) is not True:
        return None
    fanout = inventory.get("fanoutId") or os.path.basename(entry["inventory"])[len("fanout-"):-len(".inventory.json")]
    return "fan-out %s ended before launching it" % fanout


def _launched_nothing(entry, payload):
    """The reason when the fan-out call that registered `entry` ended and no inventory written since
    its issue names the job's label: the fan-out refused in preflight. None while that call may still
    write one (a live background task, or inside FANOUT_PREFLIGHT_S of its issue)."""
    if entry.get("task_id") and str(entry["task_id"]) in _background_ids(payload, None):
        return None
    issued = _issued_at(entry, payload.get("session_id"))
    since = issued if issued is not None else float(entry.get("launched_at") or time.time())
    if time.time() - since < FANOUT_PREFLIGHT_S:
        return None
    label = _label(entry)
    for written, path in _inventories(entry["fanout_dir"]):
        if (issued is None or written >= issued) and any(
                job.get("label") == label for job in _read_json(path).get("jobs") or []):
            return None
    for other in payload.get("_pending_sidecars") or []:
        if (other is not entry and other.get("kind") == "sidecar" and other.get("fanout_dir") != entry["fanout_dir"]
                and _label(other) == label and float(other.get("issued_at") or other.get("launched_at") or 0) >= since
                and resolve_sidecar_record(other, payload.get("session_id"), payload.get("_pending_sidecars"))):
            return SUPERSEDED
    return ("its fan-out call wrote no inventory within %d min, so it launched nothing; the call's "
            "output names the refusal" % (FANOUT_PREFLIGHT_S // 60))


def _sidecar_record(entry, payload):
    """The finished run's record (or its ledger row), {} when only the .exit exists, None while
    running, DIED when its launcher is gone without having written either. A finished writer seat
    waits for a live fan-out only while it runs a stage after its seats (a review's post-check or
    fold), or its legacy inventory does not say."""
    resolve_sidecar_record(entry, payload.get("session_id"), payload.get("_pending_sidecars"))
    if writer_launcher_running(entry):
        return None
    found = _sidecar_evidence(entry, payload)
    if found is None and _launcher_gone(entry.get("record")):
        # Re-read once: a launcher that wrote its exit file and then exited is finished, not killed.
        found = _sidecar_evidence(entry, payload)
        return DIED if found is None else found
    return found


def _sidecar_evidence(entry, payload):
    record = entry.get("record")
    if record and os.path.isfile(record):
        try:
            with open(record, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            pass
    exited = bool(record) and os.path.isfile(record + ".exit")
    if entry.get("fanout_dir") and not exited:
        return None
    since = _issued_at(entry, payload.get("session_id"))
    if since is None:  # no readable issue time: fall back to the later, stricter bound
        since = entry.get("launched_at")
    written = inventory_written(entry)
    if written is not None and (since is None or written > float(since)):
        since = written  # a relaunched attempt's row follows its own inventory, never the earlier attempt's
    row = _ledger_row(payload.get("session_id"), _label(entry), since)
    if entry.get("fanout_dir"):
        return row if row is not None and row.get("outcome") == ENDED else {}
    if row is not None:
        return row
    return {} if exited else None


def _route_of(plan):
    try:
        return _mr().route_of(plan)
    except Exception:
        return plan


def _projects_root(plan=None):
    """The projects root holding a transcript: `plan`'s login (a sidecar child's paying plan), else
    this session's own (CLAUDE_CONFIG_DIR). CLAUDE_PROJECTS_ROOT overrides this session's root and
    the default login's; a child on another plan's login always reads that login's."""
    override = os.environ.get("CLAUDE_PROJECTS_ROOT")
    if not plan and override:
        return override
    try:
        mr = _mr()
        config_dir = ((mr.transport_meta(plan) or {}).get("configDir") if plan
                      else os.environ.get("CLAUDE_CONFIG_DIR"))
        if config_dir and not mr.is_default_login(config_dir):
            return mr.projects_dir(config_dir)
    except Exception:
        pass
    return override or os.path.expanduser("~/.claude/projects")


def _transcript(session_id, plan=None):
    """A session's transcript under the projects root of `plan` (a sidecar child's paying plan;
    None for this session's own), or None."""
    if not session_id:
        return None
    root = _projects_root(plan)
    hits = glob.glob(os.path.join(glob.escape(root), "*", glob.escape(session_id) + ".jsonl"))
    return hits[0] if hits else None


def _agent_finished(transcript, payload, ids):
    """True when the agent has left `background_tasks` and either its last assistant record ended
    its turn (`stop_reason: end_turn`), an assistant record called SubagentHandback (this client's final
    report, recorded with no end_turn row), or its transcript has been silent QUIET_AGENT_SECONDS: an
    agent killed, errored or cut off at max_tokens never records end_turn."""
    if ids & _background_ids(payload, "subagent"):
        return False
    try:
        if time.time() - os.path.getmtime(transcript) > QUIET_AGENT_SECONDS:
            return True
    except OSError:
        return False
    last = None
    with open(transcript, encoding="utf-8") as fh:
        for line in fh:
            if '"assistant"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "assistant":
                last = row
                if _handed_back(row):
                    return True
    return bool(last) and (last.get("message") or {}).get("stop_reason") == "end_turn"


def _handed_back(row):
    """True when an assistant record calls SubagentHandback, the report that ends a subagent's job."""
    content = (row.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SubagentHandback"
        for block in content)


def _ledger_row(session_id, label, since=None):
    """The last ledger row for this session and label written at or after `since` (epoch seconds):
    a reused label must not answer for a new launch with the previous run's row."""
    if not session_id or not label:
        return None
    path = ledger_path()
    found = None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("parentSessionId") == session_id and row.get("label") == label                         and (since is None or _epoch(row.get("timestamp")) >= float(since)):
                    found = row
    except OSError:
        return None
    return found


def _uncovered_sidecars(running, session_id):
    """The running sidecar entries no live watcher covers (`watch_records._covered`).

    An entry whose record is not resolved yet is uncovered too: it is running, and no watcher names
    it. A fan-out job is resolved here the same way completion resolves it, once per entry.
    """
    state = _hook_state.read_json_salvage(_hook_state.state_path(session_id)) or {}
    watchers = state.get("sidecar_watchers") or []
    uncovered = []
    for entry in running:
        if entry.get("kind") != "sidecar":
            continue
        resolve_sidecar_record(entry, session_id, running)
        if not _covered(entry.get("record"), watchers):
            uncovered.append(entry)
    return uncovered


@functools.lru_cache(maxsize=1)
def _ps_pids():
    """The pids `ps -e` lists, or None when it cannot run: a roster writes its Git Bash (MSYS) pid, which `ps` lists.
    Cached once per process: each Stop runs this hook as a fresh process, so the one snapshot is that Stop's.
    A long-lived importer must call `_ps_pids.cache_clear()` before each check."""
    try:
        out = subprocess.run(["ps", "-e"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {int(line.split()[0]) for line in out.splitlines()[1:] if line.split() and line.split()[0].isdigit()}


def _ps_alive():
    """`roster_states`'s pid_alive over one `ps -e` set, or None to let it probe each pid itself."""
    pids = _ps_pids()
    return None if pids is None else (lambda pid: pid in pids)


def _any_roster_pid_alive():
    """True when some roster is LIVE by its `<logs>/<name>.pid`: the cheap gate before the ownership read.
    False where the benchmark suite is not installed, since only that suite starts rosters."""
    scripts = os.path.join(os.path.dirname(HERE), "scripts", "benchmark_campaign", "scoring")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import bench_lib
    except ModuleNotFoundError as exc:
        if exc.name != "bench_lib":
            raise
        return False
    logs = bench_lib.roots.logs_dir()
    if not glob.glob(os.path.join(logs, "*.pid")):
        return False
    pid_alive = _ps_alive()
    if pid_alive is None:
        return True
    return any(row["state"] == "LIVE" for row in bench_lib.roster_states(logs, pid_alive=pid_alive))


def _unwatched_rosters_reason(payload):
    """(block reason, note). The reason is set when a bench roster this session started is live and no
    session-wide watcher runs. A roster registers no sidecar entry (its seats run as
    SIDECAR_DISPATCH_OWNER=bench), so only the session watcher's own roster poll
    (`watch_records.session_rosters`) sees it. A failed roster read never blocks the stop: it returns no
    reason and a note naming the lost check, which `complete` shows the owner."""
    session_id = payload.get("session_id")
    if payload.get("stop_hook_active") or not session_id:
        return None, None
    try:
        state = _hook_state.read_json_salvage(_hook_state.state_path(session_id)) or {}
        if watch_records.session_watcher_live(state) or not _any_roster_pid_alive():
            return None, None
        verdicts = dict(state.get(ROSTER_OWNER_KEY) or {})
        known = dict(verdicts)
        units = watch_records.session_rosters(session_id, pid_alive=_ps_alive(), verdicts=verdicts)()
        if verdicts != known:
            _hook_state.update_json_locked(_hook_state.state_path(session_id),
                                           lambda st: st.setdefault(ROSTER_OWNER_KEY, {}).update(verdicts))
        live = sorted(name[len("roster:"):] for name, unit in units.items()
                      if (unit.get("roster") or {}).get("state") == "LIVE")
    except Exception as exc:
        return None, ("⚠ the bench roster check did not run (%s: %s); a live roster this session started "
                      "would go unwatched. If one is live, arm `%s` %s." % (type(exc).__name__, exc, watch_records.ARM_COMMAND, watch_records.ARM_CALL))
    return (NO_WATCHER_ROSTER_REASON % ", ".join(live) if live else None), None


def complete(payload):
    path = _hook_state.state_path(payload.get("session_id"))
    live = _background_ids(payload, None) if "background_tasks" in payload else None
    state = read_table(payload.get("session_id"), live)
    lines = list(_take_gone(path)) if state.get(GONE_KEY) else []
    pending = state.get(STATE_KEY) or []
    if not pending:
        roster_reason, roster_note = _unwatched_rosters_reason(payload)
        return "\n\n".join(lines + [roster_note] if roster_note else lines) or None, roster_reason
    payload = dict(payload, _pending_sidecars=pending, _audit_deadline=_stop_deadline())
    session = _session_dir(payload)
    now = time.time()
    done, reported, audited, reasons = set(), {}, set(), []
    for entry in pending:
        if entry.get("deferred"):
            continue
        key = (entry.get("kind"), entry.get("id"))
        name = "%s %s" % (key[0], os.path.basename(str(key[1])))
        if entry.get("verification_audit"):
            if entry["verification_audit"].get("outcome") != "running":
                done.add(key)
            else:
                audited.add(key)
            continue
        try:
            never_started = _never_started(entry, payload) if key[0] == "sidecar" else None
            if never_started == SUPERSEDED:
                done.add(key)
                continue
            if never_started:
                label = (entry.get("jobs") or [{}])[0].get("label") or UNLABELED
                lines.append("%s: never started (%s)" % (label, never_started))
                done.add(key)
                continue
            if key[0] == "workflow":
                table, ids = _newly_started(entry)
                if table:
                    lines.append(table)
                    reported[key] = list(entry.get("reported") or []) + ids
            found = _contradictions(entry, session, payload)
            if found is not None:
                block_reason = _verification_audit(entry, session, payload)
                if block_reason:
                    reasons.append(block_reason)
                if entry.get("verification_audit"):
                    audited.add(key)
        except Exception as exc:  # one bad entry must not hide the others
            lines.append("⚠ %s: unreadable (%s)" % (name, type(exc).__name__))
            done.add(key)
            continue
        if found is not None:
            lines.extend(found)
            if key not in audited:
                done.add(key)
        elif now - float(entry.get("launched_at") or now) > STALE_SECONDS:
            lines.append("⚠ %s: no completion after 24 h" % name)
            done.add(key)
    if done or reported or any(e.get("fanout_dir") and e.get("record") for e in pending):
        def change(entries):
            kept = [e for e in entries if (e.get("kind"), e.get("id")) not in done]
            for e in kept:
                key = (e.get("kind"), e.get("id"))
                if key in reported:
                    e["reported"] = reported[key]
                resolved = next((p for p in pending if (p.get("kind"), p.get("id")) == key
                                 and p.get("fanout_dir") and p.get("record")), None)
                if resolved:
                    e["record"] = resolved["record"]
                    e["inventory"] = resolved["inventory"]
                    e["jobs"] = resolved["jobs"]
            return kept
        _update_pending(payload, change)
    running = [e for e in pending if (e.get("kind"), e.get("id")) not in done | audited]
    reason = "\n\n".join(reasons) or None
    if running:
        lines.append(_running_block(running, now, payload.get("session_id")))
        if not payload.get("stop_hook_active"):
            uncovered = _uncovered_sidecars(running, payload.get("session_id"))
            if uncovered:
                reason = "\n\n".join(filter(None, (reason, NO_WATCHER_REASON % len(uncovered))))
    roster_reason, roster_note = _unwatched_rosters_reason(payload)
    if roster_note:
        lines.append(roster_note)
    reason = "\n\n".join(filter(None, (reason, roster_reason))) or None
    return "\n\n".join(lines) or None, reason  # each entry is a table or a ⚠ line; blank line between


def _elapsed(seconds):
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "<1m"
    return "%dm" % minutes if minutes < 60 else "%dh%02dm" % (minutes // 60, minutes % 60)


def _awaiting_launch(entry):
    """A planned fan-out job whose launcher has not started: its inventory names the record, and the
    record has neither the pid file a launch writes (`_never_started`'s test) nor an exit file."""
    record = entry.get("record")
    return bool(entry.get("fanout_dir") and record and not os.path.isfile(record + ".pid")
                and not os.path.isfile(record + ".exit"))


def _running_line(entry, now):
    """`name · pins · labels · elapsed` for one unfinished dispatch."""
    jobs = entry.get("jobs") or []
    kind = entry.get("kind")
    age = _elapsed(now - float(entry.get("launched_at") or now))
    if kind == "workflow":
        parts = [entry.get("name") or "Workflow"]
        if jobs:
            parts.append("%d agent%s" % (len(jobs), "" if len(jobs) == 1 else "s"))
            parts.append(workflow_pins(jobs))
        return "  " + " · ".join(parts + [age])
    job = jobs[0] if jobs else {}
    if kind == "sidecar":
        shown = _sidecar_shown(job.get("model") or UNKNOWN)
        name = "%s sidecar" % (job.get("launcher") or "")
        if _awaiting_launch(entry):
            age = "queued"
    else:
        shown, name = _shown(job.get("model")), "Agent"
    return "  " + " · ".join([name.strip(), shown, str(job.get("effort") or UNKNOWN),
                              str(job.get("label") or UNLABELED), age])


def _context_size(session_id):
    """`ctx 250k (25%)` from the status line's per-session gauge, or None when it is absent or
    unreadable. Its `last` field is the latest API call's cache-write:cache-read:input tokens, whose
    sum is the context that call sent."""
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    path = os.path.join(os.environ.get("CC_CACHESTAT_DIR") or tempfile.gettempdir(),
                        "cc-cachestat-%s.json" % (safe or "unknown"))
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        tokens = sum(int(part) for part in str(state["last"]).split(":"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    pct = state.get("context_pct")
    return "ctx %dk" % round(tokens / 1000) + (" (%d%%)" % pct if isinstance(pct, (int, float)) else "")


def _running_block(entries, now, session_id=None):
    """The Stop message's last block: every dispatch still running, one line each. The header
    carries the session's context size, so the user can judge a /compact while work runs."""
    ctx = _context_size(session_id)
    lines = ["⏳ still running · %d" % len(entries) + (" · " + ctx if ctx else "")]
    for e in entries:
        try:
            lines.append(_running_line(e, now))
        except Exception as exc:  # one bad entry must not hide the others
            lines.append("  %s %s: unreadable (%s)" % (e.get("kind"), e.get("id"), type(exc).__name__))
    return "\n".join(lines)


def _newly_started(entry):
    """(table, agent ids) for a Workflow's agents not fully reported yet — started after the last
    report, or shown with an unsettled effort — that have now responded; (None, []) when none have."""
    seen = set(entry.get("reported") or [])
    jobs = entry.get("jobs") or []
    rows, ids = [], []
    agents = _live_agents(entry.get("run_dir"), skip=seen)
    declared = dict(entry.get("declared") or {})
    models = dict(declared.get("models") or {})
    for a in agents:
        if a["served"] and a["requested"]:
            models.setdefault(a["requested"], a["served"])
    declared["models"] = models
    for agent in agents:
        job = _job_for(agent["label"], jobs)
        if agent["id"] in seen or (job and "label:" + job["label"] in seen):
            continue
        row, complete = _agent_row(agent, jobs, declared)
        if complete:
            rows.append(row)
            ids.append(agent["id"])
    if not rows:
        return None, []
    return _table("%s · update" % (entry.get("name") or "Workflow"), rows), ids


def _update_pending(payload, change, raising=False):
    path = _hook_state.state_path(payload.get("session_id"))

    def updater(state):
        state[STATE_KEY] = change(state.get(STATE_KEY) or [])[-MAX_PENDING:]

    _hook_state.update_json_locked(path, updater, raising=raising)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Show dispatch pins and audit completed evidence.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--launch", action="store_true", help="register a PostToolUse dispatch")
    modes.add_argument("--complete", action="store_true", help="audit pending dispatches at Stop")
    args = parser.parse_args()
    mode = "--complete" if args.complete else "--launch" if args.launch else None
    payload = json.load(sys.stdin)
    if mode == "--complete":
        text, reason = complete(payload)
        body = {"systemMessage": text} if text else {}
        if reason:
            body.update({"decision": "block", "reason": reason})
        if body:
            sys.stdout.write(json.dumps(body, ensure_ascii=False))
        return
    text = launch(payload) if mode == "--launch" else None
    if text:
        sys.stdout.write(json.dumps({"systemMessage": text}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        main()
    except Exception:
        # Advisory: a lost line never costs the tool call or the turn. The traceback goes to stderr,
        # which exit 0 routes to the debug log only, so the proof can tell a crash from a clean no-op.
        import traceback
        traceback.print_exc()
    sys.exit(0)
