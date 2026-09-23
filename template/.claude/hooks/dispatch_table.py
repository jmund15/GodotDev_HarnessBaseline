#!/usr/bin/env python3
"""Hook: tell the USER, never the model, which model and effort each dispatch runs at.

--launch   PostToolUse on Workflow | Agent | Bash (sidecar launcher or sidecar_fanout.py). The moment
           the dispatch starts: `▶ <name> · N agents`, then one line per distinct
           `model · effort · type: agent, agent`. Lines describe what RUNS: the hook waits up to
           DISPATCH_TABLE_LAUNCH_WAIT (3 s) for started agents' first API response and reads served
           model + applied effort from their transcripts in the run dir (the per-run
           workflows/<runId>.json is only written when the run ends). Args-enumerated jobs not
           started yet show their exact pins. Agents a script starts later, and a started agent
           shown as `pending` (a script with no literal effort), print at the next Stop as
           `▶ <name> · update`. An Agent call with no effort in its payload shows `?`.
           Sidecars show the registry version of the pinned alias. TYPE is the agent type, which
           sets how much harness the child loads. Registers the dispatch as pending.
--complete Stop. Checks each finished pending dispatch against what actually served it and prints a
           line ONLY for a contradiction: `⚠ lens ran sonnet-5 · medium, asked opus · high`. A
           dispatch that ran as requested finishes silently. No event fires when background work
           ends, but its completion notification gives the main model a turn and every turn ends in Stop.

CHANNEL: `systemMessage` only — "Warning message shown to the user" (hooks.md, JSON output). No
additionalContext, no decision: the line costs the main model nothing. Must stay a SYNCHRONOUS hook:
an async hook's systemMessage is delivered to Claude instead.

EVIDENCE for the finish check, strongest first:
  codex sidecar      attestedModel / attestedEffort — the proxy's server-side record
  Anthropic agents   message.model from each API response; top-level `effort` as sent each turn
                     (orchestration_metrics.run_rows / transcript_identity). A model that takes no
                     effort parameter records none, which is not a contradiction.
  other sidecars     servedModel from the -R record. Effort only for the native anthropic
                     transport, from the child's own transcript (record sessionId): a proxied child
                     (codex, deepseek, opencode) records its client setting, not what the upstream
                     served. Unreported evidence is never a contradiction.
Requested pins: workflow_provider_guard.dispatch_jobs; sidecar launches:
orchestration_metrics.sidecar_launches. Inherited effort: the payload's `effort.level`.

STATE: `dispatch_table_pending` in _hook_state.state_path(sid), written only via update_json_locked.
Entries leave on completion; older than 24 h they print once as stale and leave. Capped at 50.

FAIL POSTURE: advisory. A crash prints nothing, writes its traceback to stderr (debug log only) and
exits 0; each dispatch is checked in its own try, so one unreadable entry cannot hide the others.
Proof: tests/test_dispatch_table.py.
"""
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, HERE)
import _hook_state  # noqa: E402

STATE_KEY = "dispatch_table_pending"
STALE_SECONDS = 24 * 3600
MAX_PENDING = 50
DONE_STATES = ("completed", "failed", "killed", "cancelled", "error", "stopped")
UNKNOWN = "?"
DEFAULT = "default"      # a sidecar flag left to the launcher
NO_EFFORT = "none"       # the served model takes no effort parameter
NOT_A_PIN = (UNKNOWN, DEFAULT, "inherit")
UNLABELED = "(unlabeled)"
QUIET_AGENT_SECONDS = 300  # a background agent gone from background_tasks and silent this long has ended


def _om():
    sys.path.insert(0, TOOLS)
    import orchestration_metrics
    return orchestration_metrics


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
    try:
        with open(os.path.join(run_dir, "journal.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "started" and e.get("agentId") and e["agentId"] not in order:
                    order.append(e["agentId"])
    except OSError:
        pass
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
PENDING = "pending"      # effort not known until the agent's first response; resolved at the next Stop


def _wait_for_agents(run_dir, expected):
    """Poll the run dir until the started set is complete — `expected` agents, or (count unknown) no
    new agent for QUIET_SECONDS — and every started agent has responded, or the launch wait runs out."""
    deadline = time.time() + _launch_wait()
    count, steady_since = -1, time.time()
    while True:
        agents = _live_agents(run_dir)
        now = time.time()
        if len(agents) != count:
            count, steady_since = len(agents), now
        all_in = (len(agents) >= expected) if expected else (agents and now - steady_since >= QUIET_SECONDS)
        if (all_in and all(a["responded"] for a in agents)) or now >= deadline:
            return agents
        time.sleep(0.25)


def _job_for(label, jobs):
    """The args job a started agent belongs to: same label, or an engine prefix (`review:<key>`)."""
    for j in jobs:
        if label == j["label"] or str(label).endswith(":" + j["label"]):
            return j
    return None


def _agent_row(agent, jobs, declared=None):
    """(row, complete) for a started agent; complete means its first response has arrived.

    Model: the served id, else what the same pin resolved to (`declared["models"]`: another agent
    of this run that already answered, or the session's own model for the same family), else the pin.
    Effort, first found: recorded → `none` for a model that takes no effort → the args job's pin →
    the script's literal pin for this label → the script's only literal effort → PENDING (a script
    that writes no literal effort anywhere)."""
    declared = declared or {}
    job = _job_for(agent["label"], jobs) or {}
    pin = job.get("model") or agent["requested"]
    model = agent["served"] or (declared.get("models") or {}).get(pin) or pin
    effort = agent["effort"] or (NO_EFFORT if agent["responded"] or _takes_no_effort(model) else None)
    for candidate in (job.get("effort"), (declared.get("by_label") or {}).get(agent["label"]), declared.get("only")):
        if effort:
            break
        if candidate not in (None, UNKNOWN):
            effort = candidate
    return (agent["label"], short(model), effort or PENDING, agent["type"]), agent["responded"]


ANTHROPIC_FAMILIES = ("opus", "sonnet", "haiku", "fable")


def _session_model(payload):
    """The main session's own served model: the last assistant record of its transcript (tail read)."""
    path = str(payload.get("transcript_path") or "")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 400_000))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        if '"assistant"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        model = (row.get("message") or {}).get("model") if row.get("type") == "assistant" else None
        if model:
            return model
    return None


def _declared(payload, agents):
    """What the dispatch declared beyond args: literal efforts in the script, and each model pin's
    resolved id. A pin resolves the same way for every agent of a run, so one answered agent settles
    it for all; before any answer, an Anthropic alias of the session's own family resolves to the
    session model (observed: `opus` ran as claude-opus-5-5 under an opus-5-5 session)."""
    import workflow_provider_guard as guard
    try:
        text = "\n".join(t for _, t in guard._script_blobs(payload)[0])
    except Exception:
        text = ""
    by_label = _om().efforts_for({"script": text, "logs": []}) if text else {}
    literals = set(re.findall(r"\beffort\s*:\s*['\"`](\w+)['\"`]", text))
    models = {}
    for a in agents:
        if a["served"] and a["requested"]:
            models.setdefault(a["requested"], a["served"])
    session = _session_model(payload)
    for a in agents:
        pin = a["requested"]
        if pin in ANTHROPIC_FAMILIES and pin not in models and session and pin in short(session).split("-"):
            models[pin] = session
    return {"by_label": by_label, "only": literals.pop() if len(literals) == 1 else None, "models": models}


def _takes_no_effort(model):
    """True when the registry row for `model` (alias or id) declares `effortParam: false`: the model
    ignores any effort pin, so its effort is `none` from launch, never `pending`."""
    if not model:
        return False
    try:
        sys.path.insert(0, TOOLS)
        import model_registry
        name = short(model)
        return any(e.get("effortParam") is False and name in (e.get("alias"), short(e.get("id")))
                   for e in model_registry.load()["models"])
    except Exception:
        return False


def _registry_version(alias):
    """The registry's `version` (else id) for a sidecar alias: the model the launcher sends."""
    try:
        sys.path.insert(0, TOOLS)
        import model_registry
        entry = model_registry.resolve(alias)
        return entry.get("version") or entry["id"]
    except Exception:
        return alias


# ---------------------------------------------------------------- launch

def _jobs(payload):
    """[{label, model, effort, record?}] — `effort` is the value the job runs at: its pin, the engine
    default, the inherited session level, or UNKNOWN."""
    tool = payload.get("tool_name")
    if tool == "Bash":
        return [{"label": s.get("label") or UNLABELED, "model": s.get("model") or DEFAULT,
                 "effort": s.get("effort") or DEFAULT, "launcher": s.get("launcher"), "record": s.get("record")}
                for s in _om().sidecar_launches(str((payload.get("tool_input") or {}).get("command") or ""),
                                                payload.get("cwd"))]
    import workflow_provider_guard as guard
    level = _effort_level(payload) or UNKNOWN
    raw = guard.dispatch_jobs(payload)
    out = []
    for j in raw:
        effort = j.get("effort")
        agent_type = j.get("agentType")
        out.append({"label": j["label"],
                    "model": j.get("model") if j.get("model") not in (None, "unresolved") else
                    ("inherit" if tool == "Agent" else UNKNOWN),
                    "effort": UNKNOWN if effort == "unresolved" else (effort or level),
                    "agentType": UNKNOWN if agent_type == "unresolved" else (agent_type or "general-purpose")})
    return out


def _workflow_name(payload):
    resp = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
    ti = payload.get("tool_input") or {}
    path = str(ti.get("scriptPath") or ti.get("name") or "")
    return resp.get("workflowName") or (os.path.splitext(os.path.basename(path))[0] if path else "Workflow")


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
        rows, reported = [], []
        declared = _declared(payload, agents)
        for agent in agents:
            row, complete = _agent_row(agent, jobs, declared)
            rows.append(row)
            if complete or row[2] != PENDING:  # a pending effort is re-reported once it is known
                reported.append(agent["id"])
        started = {_job_for(a["label"], jobs)["label"] for a in agents if _job_for(a["label"], jobs)}
        # Enumerated jobs not started yet: their pins are exactly what the engine will send.
        rows += [(j["label"], short(j["model"]), NO_EFFORT if _takes_no_effort(j["model"]) else j["effort"],
                  j["agentType"]) for j in jobs
                 if j["label"] not in started and j["effort"] != UNKNOWN]
        reported += ["label:" + j["label"] for j in jobs]
        total = max(len(jobs), len(agents))
        title = "%s · %d agent%s" % (_workflow_name(payload), total, "" if total == 1 else "s")
        if not jobs:
            title = "%s · %d started" % (_workflow_name(payload), len(agents))
        entries = [{"kind": "workflow", "id": run_id, "task_id": resp.get("taskId"), "launched_at": now,
                    "name": _workflow_name(payload), "run_dir": run_dir, "reported": reported,
                    "declared": declared, "jobs": jobs}] if run_id else []
    elif tool == "Agent":
        title = "Agent"
        entries = [{"kind": "agent", "id": resp.get("agentId"), "task_id": None, "launched_at": now,
                    "jobs": jobs}] if resp.get("agentId") else []
        transcript = os.path.join(session or "", "subagents", "agent-%s.jsonl" % resp.get("agentId"))
        deadline = time.time() + _launch_wait()
        served, effort, responded = _read_transcript(transcript)
        while not responded and resp.get("agentId") and time.time() < deadline:
            time.sleep(0.25)
            served, effort, responded = _read_transcript(transcript)
        served = served or resp.get("resolvedModel")
        rows = [(j["label"], short(served) if served else j["model"],
                 effort or (NO_EFFORT if responded or _takes_no_effort(served or j["model"]) else j["effort"]),
                 j["agentType"]) for j in jobs]
    else:
        if not jobs:
            return None
        launchers = {j["launcher"] for j in jobs}
        title = "%s sidecar" % (launchers.pop() if len(launchers) == 1 else "mixed")
        # An unlabeled launch without -R has nothing a finish check could find.
        entries = [{"kind": "sidecar", "id": j.get("record") or j["label"], "record": j.get("record"),
                    "task_id": None, "launched_at": now, "jobs": [j]} for j in jobs
                   if j.get("record") or j["label"] != UNLABELED]
        rows = [(j["label"], j["model"] if j["model"] == DEFAULT else _registry_version(j["model"]),
                 NO_EFFORT if _takes_no_effort(j["model"]) else j["effort"], "sidecar") for j in jobs]
    lines = [_table(title, rows) if rows else "▶ %s — agents listed as they start" % title]
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
    if still_pending:
        _update_pending(payload, lambda pending: pending + still_pending)
    return "\n".join(lines)


# ---------------------------------------------------------------- completion

def _model_ok(requested, resolved, served):
    """True / False, or None when there is nothing to compare."""
    if not served or requested in NOT_A_PIN:
        return None
    strip = lambda m: re.sub(r"\[[^\]]*\]$", "", str(m or ""))
    if resolved and strip(resolved) == strip(served):
        return True
    if str(requested).lower() in re.split(r"[-.:/_]", str(served).lower()):
        return True
    return not _om().model_mismatches([{"model": requested, "served_model": served}])


def _effort_ok(requested, served):
    if not served or requested in NOT_A_PIN:
        return None
    return served == requested


def _contradiction(job, resolved, served_model, served_effort, recorded):
    """The ⚠ line for one job, or None when it ran as requested (or nothing contradicts it).
    `recorded`: the effort came from a transcript, so its absence means the model takes none."""
    model, effort = job.get("model") or UNKNOWN, job.get("effort") or UNKNOWN
    if _model_ok(model, resolved, served_model) is not False and _effort_ok(effort, served_effort) is not False:
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
        if data.get("attestedModel") or data.get("attestedEffort"):
            served, effort, recorded = data.get("attestedModel") or data.get("servedModel"), data.get("attestedEffort"), False
        else:
            served = data.get("servedModel")
            served = served if isinstance(served, str) or not served else "mixed:" + ",".join(served)
            effort, recorded = None, False
            # Only a native anthropic child talks to the model itself. A proxied child (codex,
            # deepseek, opencode) records its own client setting, not what the upstream served.
            native = (data.get("transport") or job.get("launcher")) == "anthropic"
            child = _child_transcript(data.get("sessionId")) if native else None
            if child:
                child_model, effort = _om().transcript_identity(child)
                served, recorded = served or child_model, True
        line = _contradiction(job, data.get("requestedModel"), served, effort, recorded)
        return [line] if line else []
    return None


def _workflow_contradictions(entry, session, payload):
    if not session:
        return None
    journal = os.path.join(session, "workflows", "%s.json" % entry["id"])
    if not os.path.isfile(journal):
        return None
    try:
        with open(journal, encoding="utf-8") as fh:
            run = json.load(fh)
    except ValueError:
        return None  # still being written at run end; the next Stop reads it whole
    status = (run if isinstance(run, dict) else {}).get("status")
    if status:
        if status not in DONE_STATES:
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


def _sidecar_record(entry, payload):
    """The finished run's record (or its ledger row), {} when only the .exit exists, None while running."""
    record = entry.get("record")
    if record and os.path.isfile(record):
        try:
            with open(record, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            pass
    row = _ledger_row(payload.get("session_id"), (entry.get("jobs") or [{}])[0].get("label"),
                      entry.get("launched_at"))
    if row is not None:
        return row
    return {} if record and os.path.isfile(record + ".exit") else None


def _child_transcript(session_id):
    """The sidecar child's own transcript under the Claude projects root, or None."""
    if not session_id:
        return None
    root = os.environ.get("CLAUDE_PROJECTS_ROOT") or os.path.expanduser("~/.claude/projects")
    hits = glob.glob(os.path.join(glob.escape(root), "*", glob.escape(session_id) + ".jsonl"))
    return hits[0] if hits else None


def _agent_finished(transcript, payload, ids):
    """True when the agent has left `background_tasks` and either its last assistant record ended
    its turn (`stop_reason: end_turn`) or its transcript has been silent QUIET_AGENT_SECONDS: an
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
    return bool(last) and (last.get("message") or {}).get("stop_reason") == "end_turn"


def _ledger_row(session_id, label, since=None):
    """The last ledger row for this session and label written at or after `since` (epoch seconds):
    a reused label must not answer for a new launch with the previous run's row."""
    if not session_id or not label:
        return None
    path = os.environ.get("SIDECAR_LEDGER_PATH") or os.path.expanduser("~/.claude/sidecar_ledger.jsonl")
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


def _epoch(stamp):
    """ISO-8601 `2026-09-23T01:41:08Z` → epoch seconds; 0 when absent or unparseable."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def complete(payload):
    path = _hook_state.state_path(payload.get("session_id"))
    state = _hook_state.read_json_salvage(path) or {}
    pending = state.get(STATE_KEY) or []
    if not pending:
        return None
    session = _session_dir(payload)
    now = time.time()
    lines, done, reported = [], set(), {}
    for entry in pending:
        key = (entry.get("kind"), entry.get("id"))
        name = "%s %s" % (key[0], os.path.basename(str(key[1])))
        try:
            if key[0] == "workflow":
                table, ids = _newly_started(entry)
                if table:
                    lines.append(table)
                    reported[key] = list(entry.get("reported") or []) + ids
            found = _contradictions(entry, session, payload)
        except Exception as exc:  # one bad entry must not hide the others
            lines.append("⚠ %s: unreadable (%s)" % (name, type(exc).__name__))
            done.add(key)
            continue
        if found is not None:
            lines.extend(found)
            done.add(key)
        elif now - float(entry.get("launched_at") or now) > STALE_SECONDS:
            lines.append("⚠ %s: no completion after 24 h" % name)
            done.add(key)
    if done or reported:
        def change(entries):
            kept = [e for e in entries if (e.get("kind"), e.get("id")) not in done]
            for e in kept:
                if (e.get("kind"), e.get("id")) in reported:
                    e["reported"] = reported[(e.get("kind"), e.get("id"))]
            return kept
        _update_pending(payload, change)
    return "\n\n".join(lines) or None  # each entry is a table or a ⚠ line; a blank line separates them


def _newly_started(entry):
    """(table, agent ids) for a Workflow's agents not fully reported yet — started after the last
    report, or shown with a pending effort — that have now responded; (None, []) when none have."""
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


def _update_pending(payload, change):
    path = _hook_state.state_path(payload.get("session_id"))

    def updater(state):
        state[STATE_KEY] = change(state.get(STATE_KEY) or [])[-MAX_PENDING:]

    _hook_state.update_json_locked(path, updater)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.load(sys.stdin)
    text = launch(payload) if mode == "--launch" else complete(payload) if mode == "--complete" else None
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
