#!/usr/bin/env python3
"""Prove a benchmark arm is the model it claims, BEFORE its output is scored.

An arm whose identity is unproven is unscoreable, and the cost of discovering that after a
judge panel has run is the panel. So this gates, rather than annotating.

WHICH FIELD IS AUTHORITY DEPENDS ON THE TRANSPORT, and getting that wrong is the failure this
tool exists to prevent:

  direct vendor endpoint   `servedModel` (modelUsage[].canonicalModel) is server-confirmed.
                           A bogus id hard-400s rather than falling back silently, so the
                           field cannot report a model that did not serve.

  translating proxy        `servedModel` is NOT server-confirmed -- it is the child's own pin
                           echoed back. Measured 2026-08-20: a run with the upstream forced to
                           gpt-5.6-luna reported `gpt-5.4-mini`, the child's pin. Authority is
                           `attestedModel`, read from the proxy's own upstream capture, which
                           the arm cannot influence.

The registry's `modelAttestation` block is the discriminator, not a transport name: a
transport that declares one is declaring its client-side field untrustworthy.

`attestationAgrees == false` is NOT a void. It means the child was pinned to one model and the
server-side force overrode it -- the arm really is the attested model, and the disagreement is
evidence the force worked. Treating it as a failure would discard valid cells. The void
conditions are: no evidence at all, or evidence naming a different model than the cell claims.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402
import sidecar_launch  # noqa: E402

VALID, VOID, WARN = "VALID", "VOID", "WARN"


def verdict(record, expect):
    """(status, reason) for one run-record against the model the cell claims to measure."""
    # Completeness before identity. A run that stopped mid-tool-use produced no deliverable,
    # so proving WHICH model produced the non-deliverable settles nothing -- and the record
    # otherwise reads clean (attestation passes, tokens look plausible), so a truncated arm
    # scores as a weak one. Transport-independent: any launcher can hit -n or a -T kill.
    #
    # A successful schema-constrained run can end by calling StructuredOutput, so a final
    # stopReason of 'tool_use' is valid when exitCode is 0 and schemaValid is true. Gating on
    # stopReason by itself would void every successful schema cell.
    #
    # The truncation signature is the launcher's EXIT CODE: a capped run can report
    # stopReason 'tool_use' with exit 1 and no valid deliverable. Exit code therefore decides,
    # and stopReason only escalates when nothing else attests to a deliverable.
    stop = record.get("stopReason")
    exit_code = record.get("exitCode")
    if exit_code not in (0, None):
        return VOID, (
            f"run did not complete: exit {exit_code}, stopReason {stop!r}, "
            f"{record.get('numTurns')} turns. A capped or killed run is unscoreable -- "
            f"re-run without the cap (-T guards runaway; -n discards paid work)."
        )
    if stop and stop not in ("end_turn", "success") and record.get("schemaValid") is not True:
        return VOID, (
            f"run did not complete: stopReason {stop!r} with no valid structured output "
            f"(schemaValid={record.get('schemaValid')!r}), exit {exit_code}, "
            f"{record.get('numTurns')} turns. Nothing attests to a deliverable here."
        )

    transport = record.get("transport")
    requested = record.get("requestedModel")
    expect = expect or requested
    if not expect:
        return VOID, "no expected model given and the record names no requestedModel"
    try:
        attestation = model_registry.attestation_for(transport) if transport else None
    except Exception as exc:
        return VOID, f"transport {transport!r} not in the registry ({exc})"

    if attestation:
        attested = record.get("attestedModel")
        if not attested:
            return VOID, (
                f"transport {transport!r} declares modelAttestation, so servedModel is not "
                f"authority — and the record carries no attestedModel. The arm's identity is "
                f"unproven; re-run with the capture enabled rather than scoring this cell."
            )
        if attested != expect:
            return VOID, f"attested upstream model {attested!r} is not the cell's model {expect!r}"
        if record.get("attestationAgrees") is False:
            return WARN, (
                f"attested {attested!r} — VALID. The child reported "
                f"{record.get('servedModel')!r}, so the server-side force overrode a mispinned "
                f"child. The arm is the attested model; fix the pin so the record reads cleanly."
            )
        return VALID, f"attested upstream model {attested!r} matches"

    served = record.get("servedModel")
    if not served:
        return VOID, "no servedModel in the record and this transport has no attestation path"
    if served != expect:
        return VOID, f"servedModel {served!r} is not the cell's model {expect!r}"
    return VALID, f"servedModel {served!r} matches (server-confirmed on a direct endpoint)"


def check_record(path, expect):
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except Exception as exc:
        return VOID, f"unreadable run-record {path}: {exc}"
    return verdict(record, expect)


def engagement(progress_path, root, required=()):
    """(status, reason) for whether an arm actually ENGAGED the target it was dispatched against.

    THE THIRD LEG. `verdict` above proves a run finished (completeness) and which model produced
    it (identity). Neither proves the run touched the thing it was supposed to measure, and a run
    that engaged nothing emits a record indistinguishable from a good one: exit 0, complete,
    attested. Both failures below passed the other two legs cleanly.

      T1  (2026-08-20)  The prompt staged a 32 KB survey and said to read it first. The file was
                        absent from the root; `Read` returned "File does not exist"; the arm spent
                        104 turns and 802s assessing the survey from the prompt's one-line summary.
                        Scored 24.5/219. The valid re-run scored 48/219 -- so the void cell did not
                        merely waste a cell, it published a HALVED capability number.

      T9  (2026-08-20)  An effort-sweep cell was dispatched with `-d t9-luna-max` while the prompt's
                        line 1 still read ".../t9-luna-low". The prompt text is the instruction and
                        the flag is only a starting directory, so the arm worked in -- and COMMITTED
                        into -- its sibling's root: 1455 references to the sibling against 15 to its
                        own. It voided itself AND made the sibling's branch jointly authored, so
                        neither arm's work is separately attributable there any more.

    Both were caught by a downstream scorer, hours and a judge panel later. That is the cost this
    gate removes: engagement is decidable from artifacts that exist the moment the arm exits.

    `required` are paths the prompt tells the arm to read. A read that returned the engine's
    not-found string is treated as NOT read -- the arm having *attempted* it is exactly the
    signature of the T1 failure, not a defence against it.
    """
    try:
        with open(progress_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as exc:
        return VOID, f"unreadable progress transcript {progress_path}: {exc}"

    leaf = os.path.basename(os.path.normpath(root))
    own = len(re.findall(r'\b%s\b' % re.escape(leaf), text))

    # Sibling roots of the SAME task family. A cross-family path is legitimate context (the
    # holdout, the primary checkout); a sibling is the one that silently substitutes.
    fam = re.match(r'(t\d+)-', leaf)
    rival = {}
    if fam:
        # `(?:-r\d+)?` keeps a rerun root (t9-luna-low-r2) whole: without it every reference to
        # the arm's own -rN root also counts as a hit on its prefix sibling and a clean rerun
        # reads VOID at parity.
        for hit in re.findall(r'\b%s-[a-z0-9]+-[a-z]+(?:-r\d+)?\b' % re.escape(fam.group(1)), text):
            if hit != leaf:
                rival[hit] = rival.get(hit, 0) + 1

    worst = max(rival.items(), key=lambda kv: kv[1]) if rival else None
    if worst and worst[1] >= own:
        return VOID, (f"arm engaged {worst[0]!r} ({worst[1]} refs) at least as much as its own root "
                      f"{leaf!r} ({own} refs) -- it ran in a sibling root, so this cell measures "
                      f"nothing at its dispatched target and may have contaminated the sibling")
    if own == 0:
        return VOID, f"no reference to the dispatched root {leaf!r} anywhere in the transcript"

    # A tool CALL and its RESULT are separate records: in a JSONL transcript they are separate
    # LINES, so a same-line window can never see the failure and the check silently passes the
    # arm while reporting the input as read. Scan a small line WINDOW instead -- the call line
    # plus the next two -- which covers the JSONL call/result pair and still works on a flat log.
    lines = text.splitlines()
    unread = []
    for path in required:
        base = os.path.basename(path)
        hits = [i for i, ln in enumerate(lines) if base in ln]
        if not hits:
            unread.append(f"{base} (never referenced)")
            continue
        if all(re.search(r'File does not exist|no such file|could not be found',
                         "\n".join(lines[i:i + 3]), re.IGNORECASE) for i in hits):
            # EVERY reference to it failed. One failed read followed by a successful retry is
            # an arm recovering, not an arm running blind -- only void when none succeeded.
            unread.append(f"{base} (every read returned not-found)")
    if unread:
        return VOID, ("the arm could not read input(s) the prompt directs it to: "
                      + "; ".join(unread))

    extra = f"; {worst[1]} sibling ref(s) to {worst[0]}" if worst else ""
    return VALID, (f"engaged its own root {leaf!r} ({own} refs){extra}"
                   + (f"; {len(required)} required input(s) read" if required else ""))


def preflight(launcher, expect, effort=None, model=None):
    """Dispatch one trivial prompt and validate its record.

    Cheap proof that the transport is currently honest, run before committing a full cell's
    tokens and a judge panel to it. Not a substitute for checking the cell's own record --
    a transport can be reconfigured between calls -- which is why both modes exist.

    `model` is passed through as `-m`. Without it the probe dispatches the LAUNCHER'S DEFAULT
    while `expect` names the model the caller cares about, so a transport with more than one
    registered row would fail its own preflight for the right answer about the wrong model.
    Defaults to `expect` because the caller almost always wants to probe what it expects.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fd, rec = tempfile.mkstemp(suffix=".json", prefix="voidcheck-")
    os.close(fd)
    args = ["-o", "json", "-D", "bare", "-l", "void-check", "-R", rec]
    target = model or expect
    if target:
        args += ["-m", str(target)]
    if effort:
        args += ["-e", effort]
    args += ["Reply with exactly the word: OK"]
    try:
        run = sidecar_launch.run_launcher(launcher, args, cwd=os.path.dirname(root), timeout=600)
    except Exception as exc:
        return VOID, f"preflight dispatch failed to run: {exc}"
    if run.returncode != 0:
        tail = (run.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return VOID, f"preflight dispatch exited {run.returncode}: {tail[0]}"
    status, reason = check_record(rec, expect)
    try:
        os.unlink(rec)
    except OSError:
        pass
    return status, reason


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("record", help="validate an existing run-record")
    r.add_argument("path")
    r.add_argument("--expect", help="model the cell claims to measure (default: requestedModel)")
    p = sub.add_parser("preflight", help="dispatch a trivial probe and validate its record")
    p.add_argument("launcher")
    p.add_argument("--expect", required=True)
    p.add_argument("--effort")
    e = sub.add_parser("engagement",
                       help="prove an arm engaged its dispatched root and read its inputs")
    e.add_argument("progress", help="the arm's .progress.jsonl transcript")
    e.add_argument("--root", required=True, help="the root the cell was dispatched against")
    e.add_argument("--required", action="append", default=[],
                   help="a path the prompt directs the arm to read (repeatable)")
    args = ap.parse_args(argv)

    if args.mode == "record":
        status, reason = check_record(args.path, args.expect)
    elif args.mode == "engagement":
        status, reason = engagement(args.progress, args.root, args.required)
    else:
        status, reason = preflight(args.launcher, args.expect, args.effort)
    print(f"{status}: {reason}")
    # WARN exits 0 -- the cell is scoreable. Only VOID gates.
    return 1 if status == VOID else 0


if __name__ == "__main__":
    sys.exit(main())
