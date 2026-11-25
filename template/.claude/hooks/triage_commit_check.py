#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|PowerShell), run by pre_bash_dispatch -- advises when a commit's staged diff raises
the work triage above what was recorded.

`tools/triage.py` derives reach and oracle from the paths and changed lines a commit publishes. The recorded
triage is the commit's `Triage:` trailer, else the session's task record (`task_record.py triage`). A field
the diff raises above it is printed with the tag and its probe. Standing reach names its computed
harness-quality-seat admission, not a declaration. A commit with no trailer under a session record gets
`--trailer "Triage: <recorded fields>"` through `updatedInput`, so `review_ledger.py owed` and the escape log
read the triage this check used. A commit whose message is one `-F <file>` (no `-m`) gets its trailer appended
into that file and its command is not rewritten, because auto mode denies a call whose input a hook changed
after review; a `-F` file not readable yet (written in the same call) takes the `updatedInput` rewrite only for a
recorded `Triage:` trailer, and is left as written when its trailer would be derived. A plain `-m` commit with
neither trailer nor record is left as written: no reader parses a derived trailer (`triage.py rederive` and
`escape_log.py` recompute from the diff), and a rewritten call costs a turn under auto mode, whose classifier
gives no verdict on a call a hook changed. A `-F` commit with neither carries `Triage-Derived: reach=<r>
oracle=<o>` in its message file when readable, so the derived fields travel with it; the
derived-fields line speaks once per distinct text per session and only above local reach.
Derived fields are not a judged triage. This hook never denies. An exception, a read error or an
import failure exits non-zero; the dispatcher reports the unavailable check on stderr and in context.

Matches the ACTION: commit invocations come from `_git_commit.commit_invocations`, so `git log --grep commit`,
an `echo` and a heredoc body pass. Only a commit in this project (`_git_commit.in_project`) is judged; another
repo that holds its own `.claude/` stays silent. A merge reads only the paths it authored
(`_commit_scope.authored_paths`).

A second advisory line names admitted candidate mandates with changed-since-reviewed files.
It fires once per changed owed set; never-reviewed files alone print nothing. Admission reads the
commit's triage and rigor through `triage.admits`. One-context mechanical commits stay silent here;
`session_end.md` Phase 1 decides their review admission with the checked gate. An unavailable check prints
once per distinct reason and points to `review_ledger.py uncovered`. Panel-only coverage is depth-unchecked and
is not counted. Decision doctrine commits check present premises through `verify_claims`;
an absent block asks about claims once per distinct commit message; prose premise sections report each distinct
message's malformed format. State uses `_hook_state.fire_once`.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
for _d in (HERE, TOOLS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from _commit_scope import publication, publishes_index  # noqa: E402
from _git_commit import (_flag_values, commit_invocations, commit_message, commit_trailer_lines,  # noqa: E402
                         in_project, run_git)
import triage  # noqa: E402

OWED = ("[review] %d staged file(s) changed since their last review (%d edited after a reviewed blob, "
        "%d never reviewed). Record a fix applied outside a fold with post_check.py review --findings <file> "
        "--applied <ids> --files <paths> (orchestrator_action_protocol.md Step 4).")
OWED_FAILED = "[review] owed check did not run: %s; run review_ledger.py uncovered"

RAISE_HEAD = "[triage] the staged diff raises the recorded triage; raise the trailer, or re-triage with task_record.py triage --reason:"


class _ReadError(Exception):
    """The staged content could not be read: reported on stderr with exit 1, never to the model."""


def _project_root():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.normpath(os.path.join(HERE, "..", ".."))


def _recorded(message, session_id, with_source=False):
    """(fields, None) | (None, unreadable-reason) | (None, None) when neither trailer nor record exists. With
    `with_source`, a third item says whether the fields came from the session's task record."""
    fields, error = triage.parse_trailer(message) if message is not None else (None, None)
    if error or fields is not None:
        return (fields, error, False) if with_source else (fields, error)
    import task_record
    fields = task_record.triage_for_session(session_id)
    return (fields, None, fields is not None) if with_source else (fields, None)


def _blobs(invocation, paths):
    """path -> content hash the commit publishes: the index blob, or the working file for a pathspec or -a commit."""
    top = triage._toplevel(invocation.cwd, invocation.git_env)
    source = publication(invocation.sub, invocation.rest, invocation.cwd, invocation.git_env).mode
    if source == "unknown":
        raise OSError("a -i commit that mixes index and working-tree content")
    if source == "worktree":
        present = [p for p in paths if os.path.isfile(os.path.join(top, *p.split("/")))]
        out = run_git(["hash-object", "--"] + present, top, invocation.git_env) if present else ""
        found = (out or "").split()
        if out is None or len(found) != len(present):
            raise OSError("git hash-object")
        return dict(zip(present, found))
    out = run_git(["-c", "core.quotepath=false", "ls-files", "-s", "-z", "--"] + list(paths), top,
                  invocation.git_env)
    if out is None:
        raise OSError("git ls-files -s")
    blobs = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if path in paths and len(fields) == 3 and fields[0] != "160000":
            blobs[path] = fields[1]
    return blobs


def _once(session_id, key, text):
    from _hook_state import fire_once
    import hashlib
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text if fire_once(session_id, key + ":" + signature) else None


def _premises(invocation, session_id, fields):
    if not triage.admits("premises", fields, triage.rigor()).admitted:
        return None
    message = commit_message(invocation.rest, invocation.cwd) or ""
    block = re.search(r"^```premises\s*\n(.*?)^```", message, re.MULTILINE | re.DOTALL)
    if not block:
        if re.search(r"^[ \t]*premises:", message, re.MULTILINE | re.IGNORECASE):
            note = ("[premises] prose `premises:` is not a fenced JSONL evidence block; write "
                    "load-bearing claims as a ```premises fence holding one JSON object per line "
                    "(reference/evidence_contract.md §Load-bearing premises).")
            return note if _once(session_id, "premises-format", message) else None
        ask = ("[premises] does this decision rest on a cause, count or ruling? If so, add its "
               "premises block (reference/evidence_contract.md).")
        return ask if _once(session_id, "premises-ask", message) else None
    try:
        rows = [dict(json.loads(line), id="premise-%d" % n)
                for n, line in enumerate(block.group(1).splitlines(), 1) if line.strip()]
    except (ValueError, TypeError):
        return ("[premises] the ```premises block is malformed: it is not one JSON object per line, so no "
                "claim was checked; rewrite it as JSONL (reference/evidence_contract.md §Load-bearing premises).")
    try:
        import verify_claims
        result = verify_claims.check_claims(rows, root=triage._toplevel(invocation.cwd, invocation.git_env),
                                          session_id=session_id)
        failures = ["%s %s: %s" % (key, item["verdict"], item.get("reason", ""))
                    for key, verdict in result["verdicts"].items() for item in verdict["items"]
                    if item["verdict"] != "VERIFIED"]
        failures += result["errors"]
        failures += [key + " UNCHECKED: no evidence" for key, verdict in result["verdicts"].items()
                     if not verdict["items"]]
        return "[premises] " + "; ".join(failures) if failures else None
    except Exception as exc:
        return "[premises] check unavailable: %s: %s" % (type(exc).__name__, exc)


def _owed(invocation, paths, fields, session_id):
    """The owed-review line, the did-not-run line, or None when no commit-time advice is due."""
    if fields.kind == "mechanical" and fields.size == "one-context":
        return None
    admission = triage.admits("candidate-lenses", fields, triage.rigor())
    if not admission.admitted:
        return None
    try:
        import review_ledger
        import lens
        mandates = (lens.seat_mandates("sa-architecture-sweep") or []
                    if admission.width != "all" else review_ledger.resolve_candidate())
    except Exception as exc:
        return _once(session_id, "review-failed", OWED_FAILED % ("%s: %s" % (type(exc).__name__, exc)))
    try:
        repo = Path(invocation.cwd)
        hashes = _blobs(invocation, paths)
        pairs = review_ledger.owed_staged(repo, hashes, mandates=mandates)
        owed_pairs = [pair for pair in pairs if len(pair) == 2]
        if not owed_pairs:
            return None
        cover = review_ledger.staged_cover(repo, hashes)
        changed = {pair[0] for pair in owed_pairs
                   if cover.earlier_blob(pair[1], pair[0], []) is not None}
    except review_ledger.LedgerUnreadable as exc:
        return _once(session_id, "review-failed", OWED_FAILED % ("ledger unreadable: %s" % exc))
    except Exception as exc:
        return _once(session_id, "review-failed", OWED_FAILED % ("%s: %s" % (type(exc).__name__, exc)))
    files = {pair[0] for pair in pairs if len(pair) == 2}
    if not changed:
        return None
    text = OWED % (len(files), len(changed), len(files - changed))
    signature = json.dumps(sorted((p, m, hashes.get(p)) for p, m in owed_pairs))
    return text if _once(session_id, "review-owed", signature) else None


def _check(invocation, session_id, tags):
    """The advisory blocks for one commit: the triage block when one applies, then the owed-review line."""
    published = publication(invocation.sub, invocation.rest, invocation.cwd, invocation.git_env)
    paths, failed = published.selection
    if paths is None:
        raise _ReadError("cannot read the content this commit publishes (%s)" % failed)
    paths = published.authored(sorted(paths))
    if not paths:
        return [], None
    changes = triage.changes_from_index(invocation.cwd, paths, invocation.git_env, invocation.rest)
    derived = triage.derive(changes, tags, root=triage._toplevel(invocation.cwd, invocation.git_env))
    recorded, _ = _recorded(commit_message(invocation.rest, invocation.cwd), session_id)
    recorded = recorded or {}
    fields = triage.Fields(max((recorded.get("reach", "local"), derived.reach), key=triage.REACH_RANK.__getitem__),
                           derived.oracle, recorded.get("kind", "decision"),
                           recorded.get("size", "milestones"), tuple(paths))
    block, trailer = _triage_block(invocation, session_id, tags, paths)
    return [b for b in (block, _premises(invocation, session_id, fields),
                       _owed(invocation, paths, fields, session_id)) if b], trailer


def _triage_block(invocation, session_id, tags, paths):
    """The triage advisory for one commit, or None when the triage stands."""
    try:
        changes = triage.changes_from_index(invocation.cwd, paths, invocation.git_env, invocation.rest)
        result = triage.derive(changes, tags, root=triage._toplevel(invocation.cwd, invocation.git_env))
    except OSError as exc:
        raise _ReadError("cannot read the content this commit publishes (%s)" % exc)
    message = commit_message(invocation.rest, invocation.cwd)
    if message is None:
        message = "\n".join(commit_trailer_lines(invocation.rest)) or None
    fields, error, from_record = _recorded(message, session_id, with_source=True)
    advice, trailer = triage_verdict(result, fields, error, message, invocation.sub,
                                     lambda: _standing_state(invocation), from_record)
    if (trailer is None and fields is None and not error and invocation.sub == "commit"
            and _has_message_file(invocation)):
        trailer = "Triage-Derived: reach=%s oracle=%s" % (result.reach, triage._format_oracle(result.oracle))
        if message and re.search(r"^Triage-Derived:", message, re.MULTILINE):
            trailer = None
    if advice and fields is None and not error:
        advice = _once(session_id, "triage-derived", advice)  # the derived fields repeat until the session records its triage
    return advice, trailer


def _standing_state(invocation):
    root = Path(triage._toplevel(invocation.cwd, invocation.git_env))
    return triage.current(root), triage.rigor(root=root)


def triage_verdict(result, fields, error, message, sub, standing_state, from_record=False):
    """(advice, trailer) for one commit from its derived triage result and its recorded fields. Pure: the caller
    reads the diff, the message and the task record. `standing_state()` returns (current triage, rigor) and runs
    only when the derived reach is standing. Fields `from_record` (the session's task record, no trailer in the
    message) are written into the commit as its `Triage:` trailer, so a later per-commit read (`owed`, the
    escape log) sees the triage this check used."""
    advice, trailer = _verdict(result, fields, error, message, sub, standing_state)
    if from_record and sub == "commit" and trailer is None:
        trailer = "Triage: " + triage.trailer_text(fields)
    return advice, trailer


def _verdict(result, fields, error, message, sub, standing_state):
    if error:
        return ("[triage] Triage: trailer unreadable (%s); grammar: triage.py --help." % error), None
    if fields is None:
        context = ("[triage] derived reach=%s oracle=%s; judged fields: execution_depth.md §Fields."
                   % (result.reach, triage._format_oracle(result.oracle))) if result.reach != "local" else None
        return context, None
    found = triage.raises(fields, result)
    if not found:
        return None, None
    standing = [r for r in found if r.field == "reach" and r.derived == "standing"]
    blocks = []
    if standing:
        current, rigor = standing_state()
        # The pending message and invocation scope win over the session's planned fields.
        current.reach = max((fields.get("reach", "local"), result.reach), key=triage.REACH_RANK.__getitem__)
        current.kind = fields.get("kind", "decision")
        current.size = fields.get("size", "milestones")
        admission = triage.admits("harness-quality-seat", current, rigor)
        blocks.append("[triage] computed reach=%s; harness-quality-seat: %s (%s)."
                      % (current.reach, "admitted" if admission.admitted else "not admitted", admission.reason))
        blocks.extend("- " + triage.describe(r) for r in standing)
    declared = [r for r in found if r not in standing]
    if declared:
        blocks.append("\n".join([RAISE_HEAD] + ["- " + triage.describe(r) for r in declared]))
    return "\n".join(blocks), None


def _rewrite(command, trailers, calls):
    """Insert each trailer at its shared source offset, escaped through its enclosing words."""
    inserts = []
    for ordinal, call in enumerate(calls):
        trailer = trailers.get(ordinal)
        if trailer is None or call.subcommand_end is None or call.body_path is None:
            continue
        option = ' --trailer "' + trailer + '"'
        for word in reversed(call.body_path):
            if word.quote == "'":
                option = option.replace("'", "'\"'\"'")
            else:
                option = re.sub(r'([\\"$`])', r'\\\1', option)
        inserts.append((call.subcommand_end, option))
    for at, option in sorted(inserts, reverse=True):
        command = command[:at] + option + command[at:]
    return command


def _has_message_file(invocation):
    """Whether the commit's message comes from a `-F`/`--file` flag, with no `-m`, readable or not."""
    files, from_stdin = _flag_values(invocation.rest, "F")
    messages, _ = _flag_values(invocation.rest, "m")
    return len(files) == 1 and not from_stdin and not messages


def _message_file(invocation):
    """The one `-F` message file of a commit whose message is that file alone, else None."""
    if not _has_message_file(invocation):
        return None
    files, _ = _flag_values(invocation.rest, "F")
    path = os.path.join(invocation.cwd, files[0])
    return path if os.path.isfile(path) else None


def _append_trailer(path, trailer):
    """Append the trailer to the message file, inside its last paragraph when that is a trailer block."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read().rstrip("\r\n")
    last = text.rsplit("\n\n", 1)[-1].splitlines()
    in_block = "\n\n" in text and all(re.match(r"^[A-Za-z][A-Za-z0-9-]*: ", line) for line in last)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + ("\n" if in_block else "\n\n") + trailer + "\n")


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    session_id = payload.get("session_id") or ""
    blocks, tags, trailers = [], None, {}
    try:
        calls = commit_invocations(command, payload.get("cwd") or os.getcwd())
        for ordinal, invocation in enumerate(calls):
            if not publishes_index(invocation.sub, invocation.rest) or not in_project(invocation.cwd, _project_root()):
                continue
            if tags is None:
                tags = triage.load_tags(os.path.join(_project_root(), ".claude"))
            found, trailer = _check(invocation, session_id, tags)
            blocks.extend(found)
            if trailer is not None:
                path = _message_file(invocation)
                if path is None and trailer.startswith("Triage-Derived:") and _has_message_file(invocation):
                    continue
                if path is None:
                    trailers[ordinal] = trailer
                else:
                    _append_trailer(path, trailer)
    except _ReadError as exc:
        sys.stderr.write("[triage] %s\n" % exc)
        return 1
    output = {"hookEventName": "PreToolUse"}
    if blocks:
        output["additionalContext"] = "\n\n".join(blocks)
    if trailers:
        output["updatedInput"] = dict(payload.get("tool_input") or {}, command=_rewrite(command, trailers, calls))
    print(json.dumps({"hookSpecificOutput": output}) if blocks or trailers else "{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
