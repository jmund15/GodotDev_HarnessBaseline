#!/usr/bin/env python3
"""Close a findings dispatch with one command: quote verdicts, fold checks, ledger rows, coverage still owed.

A dispatch that returns findings or claims verifies them before the parent reads them. The engine runs every
check its code can; the checks that need disk or shell run here, over inputs the dispatch's author did not write.

  post_check.py snapshot --root R (--files P... | --base REV [--files P...]) [--new-paths N...] --out M
  post_check.py review   (--run-label L | --workflow-dir D | --sidecar-dir D |
                          --result F | --findings F [--digest G --snapshot M]...)
                         [--root R --spill-dir S --expect-total N --expect-sources N]
                         [--seats-file S --premise-commits C --head H --blobs B]
                         [--stage S --subject X --roster R [--task T] [--no-new-decision]] --out OUT
  post_check.py claims   (--run-label L | --workflow-dir D | --sidecar-dir D | --result F)
                         [--root R --spill-dir S] --out OUT

`snapshot` writes the pre-fold manifest the fold checker diffs against: each file as a git blob (a missing file
as null), or with `--base` a base revision. The caller runs it before dispatch, because a Workflow script cannot
run shell.

`review` reads the complete source (a Workflow transcript directory, a sidecar output directory, or a findings
file with an optional digest/snapshot pair per fold subject) and refuses a bounded preview with shed findings. It
runs the independent quote verdicts over every finding, `fold_check` per subject when the run folded, records the
panel when `--stage/--subject/--roster` are given. It records derived inline `fold` rows per file and
mandate whose predecessor is covered, except uncovered mandates and files a scoped failure voids.
`nextRound.uncovered` lists, per lens, the mandates and files no reviewer has covered since the change. It is a
fact for the session's later-round choice (`orchestration` §2 *Later rounds*), never a seat to fill. `review --findings F --applied ID[,ID...] [--ruled ID...] --files P...` applies the
same checks to any applier, using the newest common reviewed blob as its snapshot; it excludes
`--digest` and `--snapshot`. Every ledgered stage requires an adaptation `later_round_roles` row.
`nextRound.refold` names the round's held and failed-check ids, files and standalone commands.
`OUT` holds `status`, `verdicts`,
`held`, `failures`, `ledger`, `nextRound`, `changedPaths`, `unverified`, `foldReport`; `OUT.fold.json` holds the fold checker's report.

Both check modes write `status`, `counts`, the per-id `verdicts` map and `errors`, through
verify_claims.check_claims, plus `mode` and `source`, the resolved read flags the Stop audit replays. Claims without evidence are UNCHECKED, never skipped. `--root` resolves
relative evidence through verify_claims.resolve_path; folded reviews retain each subject's root and snapshot.
Caller artifact flags become absolute at intake against the invoking cwd; subject-relative `--files` and
`--new-paths`, git revisions and identifiers stay unchanged. Ledger subprocesses keep the subject cwd.
`--spill-dir` supplies `<label>.md` when a job's inline result has no block. `--expect-total` supplies
the consolidated review count before the host record exists; `--expect-sources` is the raw source count. An unfolded
branch that reads consolidated findings checks `--expect-total`, one that reads raw seat rows checks `--expect-sources`,
and each ignores the flag for a count it does not read. A folded run fails routing only when the engine's consolidated
count exceeds the archive's originals, since consolidation only merges. fold_check holds by id each original finding
no fold job read. `--seats-file` supplies the review sources'
numbering (launch order); a multi-source fold with neither the record nor the seats fails `fold-id-order-unknown`. Missing or unreadable sources write unavailable.

`claims --run-label` finds the live journal by the independent verifier's started, unreturned label.
Claims inputs are fenced claims/findings JSON Lines, structured claims/findings arrays, explore journals
or doc-audit findings. A malformed block line or incomplete explore source fails the report.

EXIT: 0 pass; 1 a check failed; 2 unusable input (a bounded preview, a missing snapshot, an unreadable source).
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))
import adaptation  # noqa: E402
import fold_check  # noqa: E402

Unusable = fold_check.Unusable
HOLD_CLASSES = ("owner-input", "own-design", "unbased-number", "new-mechanism", "path-scope",
                "read-first", "conflict", "destructive", "taste-fork", "plan-alternative", "spike", "refutation",
                "unverified")
COVERING = ("findings", "clean")


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _digest_value(path):
    value = fold_check._read_json(path, "digest")
    return value.get("digest") if isinstance(value, dict) and isinstance(value.get("digest"), dict) else value


# ---- ledger ---------------------------------------------------------------------------------------


def ledger(cwd, *args):
    """Run review_ledger.py in `cwd` (the ledger repo) -> (code, stdout, stderr)."""
    done = subprocess.run([sys.executable, str(TOOLS / "review_ledger.py"), *args], cwd=str(cwd),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout, done.stderr


def ledger_rows(cwd, subject):
    code, out, err = ledger(cwd, "rows", "--subject", subject, "--json")
    if code != 0:
        raise Unusable(f"review_ledger rows failed: {err.strip()}")
    try:
        return json.loads(out or "[]")
    except ValueError as exc:
        raise Unusable(f"review_ledger rows output is not JSON: {exc}") from None


def load_roster(roster_path):
    value = fold_check._read_json(roster_path, "roster")
    if isinstance(value, dict):
        value = value.get("roster")
    if not isinstance(value, list):
        raise Unusable(f"roster {roster_path} is not a list of seats")
    return value


# ---- sources --------------------------------------------------------------------------------------


class Source:
    """What `review` reads: the original rows, the fold jobs, and how the panel records."""

    def __init__(self):
        self.rows, self.jobs, self.record, self.errors, self.findings_path = [], [], None, [], None
        self.selection = None


def _seat_order(seats_file):
    """Review seat keys in launch order (the engine's `args.agents` order), from the launched-seats file."""
    if not seats_file:
        return None
    seats = fold_check._read_json(Path(seats_file), "launched seats")
    if not isinstance(seats, dict):
        raise Unusable(f"launched seats {seats_file} is not an object keyed by seat label")
    return [label.removeprefix("review:") for label in seats]


def _archive_rows(directory, kind="review", *, merge=True, seat_order=None):
    import session_digest
    try:
        archive = session_digest.build_workflow_result_archive(Path(directory), kind, verify=False,
                                                               seat_order=seat_order)
    except ValueError as exc:
        raise Unusable(str(exc)) from None
    rows = [dict(item["finding"], id=item["id"]) for item in archive["items"]]
    if merge and kind == 'review' and archive.get('merges'):
        import review_result
        by_id = {row['id']: row for row in rows}
        consumed, merged = set(), []
        category = {'bug': 0, 'rule': 1, 'improvement': 2}
        for group in archive['merges']:
            ids = group['merged_from']
            members = [by_id[key] for key in ids]
            rep = members[0]
            decider = min(members, key=review_result._decider_rank)
            evidence = list({json.dumps(item, sort_keys=True): item
                             for member in members for item in member.get('evidence', [])}.values())
            row = dict(rep, id=group['id'], merged_from=ids, evidence=evidence,
                       action=decider.get('action'), question=decider.get('question'),
                       options=decider.get('options'), scope=decider.get('scope'),
                       category=min(members, key=lambda m: category.get(m.get('category'), 9)).get('category'),
                       critical=any(member.get('critical') for member in members),
                       refuting=any(member.get('refuting') for member in members))
            if row['action'] == 'ASK':
                row['question'] = ' / '.join(dict.fromkeys(m['question'] for m in members if m.get('question'))) or None
                row['options'] = list(dict.fromkeys(option for m in members if m.get('action') == 'ASK'
                                                   for option in m.get('options') or [])) or None
            if any((m.get('file'), m.get('old'), m.get('new')) !=
                   (rep.get('file'), rep.get('old'), rep.get('new')) for m in members):
                row.update(old=None, new=None)
            consumed.update(ids)
            merged.append(row)
        rows = merged + [row for row in rows if row['id'] not in consumed]
    return rows, archive


def _consolidated_total(run_dir):
    """The engine's consolidated finding count from the run's host record, else None."""
    import session_digest
    try:
        result = session_digest.workflow_run_record(run_dir)["result"]
        result = json.loads(result) if isinstance(result, str) else result
        total = result["counts"]["total"]
    except (ValueError, TypeError, KeyError):
        return None
    return total if isinstance(total, int) else None


def read_source(args):
    src = Source()
    if args.run_label:
        args.workflow_dir = str(run_label_directory(args.run_label))
    if args.result:
        args.findings = args.result
    if args.workflow_dir:
        directory = Path(args.workflow_dir)
        src.jobs = fold_check.workflow_jobs(directory, required=False)
        if src.jobs:
            src.rows, archive = _archive_rows(directory, merge=False, seat_order=_seat_order(args.seats_file))
            if archive['status'] != 'completed':
                src.errors.append({'kind': 'source-incomplete', 'detail': f"workflow coverage is {archive['status']}"})
            if archive.get('idOrder') != 'engine':
                src.errors.append({'kind': 'fold-id-order-unknown', 'detail': (
                    "pass --seats-file with the launched-seats file. Without it the review sources are numbered "
                    "in journal start order, so fold ids may name other findings")})
            total = args.expect_total if args.expect_total is not None else _consolidated_total(directory)
            if total is not None and total > len(src.rows):
                src.errors.append({"kind": "fold-routing", "detail": (
                    f"the engine consolidated {total} findings but the run archive holds {len(src.rows)} originals, and "
                    "consolidation only merges; retrieve the missing ones with session_digest --workflow-kind review")})
        else:
            view_total = None
            raw_total = None
            results = _journal_results(directory)
            if args.spill_dir or any(isinstance(value, str) and _BLOCK.search(value) for _, value in results):
                for label, value in results:
                    if args.spill_dir and not (isinstance(value, str) and _BLOCK.search(value)):
                        path = Path(args.spill_dir) / f'{label}.md'
                        if path.is_file():
                            value = path.read_text(encoding='utf-8')
                    found, errors = result_claims(label, value, preserve=True)
                    src.rows.extend(found)
                    src.errors.extend({'kind': 'source-incomplete', 'detail': error} for error in errors)
                if not src.rows:
                    src.errors.append({'kind': 'source-incomplete', 'detail': 'no review findings in spill deliverables'})
            else:
                src.rows, archive = _archive_rows(directory, merge=False, seat_order=_seat_order(args.seats_file))
                raw_total = len(src.rows)
                view_total = len(src.rows) - sum(len(group['merged_from']) - 1 for group in archive.get('merges') or [])
                if archive["status"] != "completed":
                    src.errors.append({"kind": "source-incomplete", "detail": f"workflow coverage is {archive['status']}"})
            src.rows = fold_check._validated(src.rows, directory) if src.rows else []
            raw_total = len(src.rows) if raw_total is None else raw_total
            if view_total is not None and args.expect_total is not None and args.expect_total != view_total:
                src.errors.append({'kind': 'fold-routing', 'detail': f'expected {args.expect_total} findings, read {view_total}'})
            if args.expect_sources is not None and args.expect_sources != raw_total:
                src.errors.append({'kind': 'fold-routing', 'detail': f'expected {args.expect_sources} source findings, read {raw_total}'})
        src.record = ("--workflow-dir", str(directory)) if args.seats_file else ("--run", directory.name)
    elif args.sidecar_dir:
        directory = Path(args.sidecar_dir)
        review = directory / "review.json"
        document = fold_check._read_json(review, "sidecar review")
        if isinstance(document, dict):
            src.selection = document.get("reviewerSelection")
        src.rows = fold_check.original_rows(review, allow_empty=not (directory / "fold_jobs.json").is_file())
        consolidated = len(fold_check.load_rows(review, allow_empty=True))
        if args.expect_total is not None and args.expect_total != consolidated:
            src.errors.append({'kind': 'fold-routing', 'detail': f'expected {args.expect_total} consolidated findings, read {consolidated}'})
        src.findings_path = str(review)
        jobs_file = directory / "fold_jobs.json"
        if jobs_file.is_file():
            for entry in fold_check._read_json(jobs_file, "fold jobs"):
                subject = fold_check.load_subject(entry["root"], entry["snapshot"], entry["files"],
                                                  entry.get("newPaths") or [])
                try:
                    value = _digest_value(entry["digest"])
                except Unusable:
                    value = None
                src.jobs.append({"subject": subject, "digestValue": value, "digest": entry["digest"],
                                 "artifacts": entry.get("artifacts") or [], "foldActor": entry.get("foldActor")})
        src.record = ("--sidecar-result", str(review))
    else:
        if not args.findings:
            raise Unusable("review needs --workflow-dir, --sidecar-dir or --findings")
        src.rows = fold_check.original_rows(args.findings, allow_empty=not args.digest)
        src.findings_path = args.findings
        shape = fold_check._read_json(args.findings, "findings")
        if isinstance(shape, dict) and isinstance(shape.get("perAgent"), list):
            src.record = ("--result", args.findings)
        if len(args.digest) != len(args.snapshot):
            raise Unusable("each --digest needs one --snapshot, in order")
        for digest, manifest in zip(args.digest, args.snapshot):
            subject = fold_check.subject_from_manifest(manifest)
            engine_files = [path for path in (fold_check.fold_prompt_path(digest), fold_check.fold_verdicts_path(digest)) if path]
            src.jobs.append({"subject": subject, "digest": digest, "digestValue": _digest_value(digest),
                             "artifacts": [args.findings, *engine_files]})
    return src


def source_flags(args):
    """The resolved inputs a check read, as flags: the Stop audit replays exactly this source."""
    flags = []
    for name in ("workflow_dir", "sidecar_dir", "findings", "spill_dir"):
        if getattr(args, name, None):
            flags += ["--" + name.replace("_", "-"), str(Path(getattr(args, name)).resolve())]
    if not getattr(args, "findings", None) and getattr(args, "result", None):
        flags += ["--result", str(Path(args.result).resolve())]
    for digest, manifest in zip(getattr(args, "digest", None) or [], getattr(args, "snapshot", None) or []):
        flags += ["--digest", str(Path(digest).resolve()), "--snapshot", str(Path(manifest).resolve())]
    if getattr(args, "expect_total", None) is not None:
        flags += ["--expect-total", str(args.expect_total)]
    if getattr(args, "expect_sources", None) is not None:
        flags += ["--expect-sources", str(args.expect_sources)]
    for value in getattr(args, 'ruled', None) or []:
        flags += ['--ruled', value]
    return flags


def applied_job(args, src):
    import review_ledger
    if args.digest or args.snapshot:
        raise Unusable("--applied excludes --digest and --snapshot")
    if not args.files or not args.roster or not args.stage or not args.subject:
        raise Unusable("--applied requires --files, --stage, --subject and --roster")
    aliases = {row['id']: fold_check._sources(row) for row in fold_check.load_rows(args.findings)}
    applied = {source for value in args.applied for item in value.split(',') if item
               for source in aliases.get(item, [item])}
    ruled = {source for value in args.ruled for item in value.split(',') if item
             for source in aliases.get(item, [item])}
    known = {row["id"] for row in src.rows}
    if not applied or applied - known or ruled - applied:
        raise Unusable("--applied must name finding ids; --ruled must be a subset of --applied")
    authorized = ruled | {row['id'] for row in src.rows if row['id'] in applied and fold_check.hold_class(row)}
    root = Path(args.root or REPO).resolve()
    roster = review_ledger._load_roster(args.roster, review_ledger.load_catalog())
    mandates = sorted({m for seat in roster for m in seat["mandates"]})
    seats = {m: review_ledger._depth_seats(roster, m) for m in mandates}
    cover = review_ledger.cover_at(root, {})
    cover.subject_kind = review_ledger._subject_kind(args.subject)
    files = review_ledger.normalize_files(root, args.files)
    blobs = {}
    for rel in files:
        by_mandate = {m: sorted({b for row in cover.rows if (b := (row.get("files") or {}).get(rel))
                                 and all(cover.resolve(m, rel, b, seat) is not None for seat in seats[m])})
                      for m in mandates}
        covering = [m for m in mandates if by_mandate[m]]
        blob = review_ledger.reviewed_blob(cover, rel, covering, seats) if covering else None
        if blob is None:
            raise Unusable(f"no common covered blob for {rel}; per-mandate blobs: {json.dumps(by_mandate)}")
        blobs[rel] = blob
    manifest = {"kind": "blobs", "root": str(root), "files": blobs, "head": cover.ctx.head}
    manifest_path = str(args.out) + ".applied.snapshot.json"
    _write(manifest_path, manifest)
    subject = fold_check.Subject(root, files, manifest, manifest_path=manifest_path)
    src.rows = [row for row in src.rows if row['id'] in applied]
    digest = {"applied": [{"id": i, "change": "applied by the caller", "source": "ruling" if i in ruled else "finding"}
                          for i in sorted(applied)],
              "deferred": [], "designDelta": [], "changedPaths": subject.change_set()}
    return {"subject": subject, "digestValue": digest, "artifacts": [args.findings], "rows": src.rows,
            "ruled": sorted(authorized)}


# ---- report ---------------------------------------------------------------------------------------


def _hold_class(entry):
    """The class the fold author named; the first class token in the reason only for a digest that names none."""
    if entry.get("class") in HOLD_CLASSES:
        return entry["class"]
    text = str(entry.get("reason") or "").lower()
    return next((name for name in HOLD_CLASSES if name in text), "other")


def held_rows(rows, jobs, *, views=None):
    """Every id the parent rules: the fold's deferred ids with their class, each ASK and open PLAN, and on a run with
    no fold every FIX and resolved PLAN as `unfolded`, which the standalone fold applies."""
    jobs = [fold_check._expanded_job(job, job.get('rows') or views or rows) for job in jobs]
    held, seen = [], set()
    for job in jobs:
        digest = job.get("digestValue") if isinstance(job.get("digestValue"), dict) else {}
        for entry in digest.get("deferred") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"] not in seen:
                seen.add(entry["id"])
                held.append({"id": entry["id"], "class": _hold_class(entry),
                             "reason": str(entry.get("reason") or "")})
    applied = {entry.get("id") for job in jobs if isinstance(job.get("digestValue"), dict)
               for entry in job["digestValue"].get("applied") or [] if isinstance(entry, dict)}
    authorized = {i for job in jobs for i in job.get('ruled') or []}
    for row in rows:
        if row['id'] in seen or (row['id'] in applied and
                                (not fold_check.hold_class(row) or row['id'] in authorized)):
            continue
        held_class = fold_check.hold_class(row)
        if held_class:
            held.append({'id': row['id'], 'class': held_class,
                         'reason': str(row.get('question') or row.get('description') or '')})
        elif not jobs and row.get("action") in ("FIX", "PLAN"):
            held.append({"id": row["id"], "class": "unfolded", "reason": str(row.get("description") or "")})
    return held


def _covering_mandates(rows, rel, wanted):
    """The roster mandates a ledger row records as covering `rel`."""
    return sorted({row["mandate"] for row in rows if rel in (row.get("files") or {})
                   and row.get("outcome") in COVERING and row.get("mandate") in wanted})


def record_inline(cwd, args, wanted, blocks, rows, candidates, failures, subjects, roster):
    """Record each unchanged mandate over the exact checked post-fold bytes and predecessor."""
    import review_ledger
    voided = set()
    for failure in failures:
        scope = fold_check.FAILURE_SCOPE.get(failure["kind"], "subject")
        if scope in ("subject", "run"):
            voided.update(rel for subject in subjects for rel in subject.allowed
                          if scope == "run" or failure.get("subject") in (None, subject.root_text))
        else:
            voided.update(failure.get("files") or [])
    written, recorded = 0, set()
    for block, subject in zip(blocks, subjects):
        if set(subject.allowed) <= voided:
            continue
        pre = reviewed_blobs(subject)
        cover = review_ledger.cover_at(cwd, pre)
        cover.subject_kind = review_ledger._subject_kind(args.subject)
        for rel in block["changedFiles"]:
            if rel in voided or subject.post_bytes(rel) is None:
                continue
            previous = pre.get(rel)
            root = previous is None and fold_check.is_test_path(rel)
            mandates = [m for m in sorted(wanted - candidates.get(rel, set())) if root or (
                previous and cover.pair_state(m, rel, review_ledger._depth_seats(roster, m)) is None)]
            post = subprocess.run(["git", "-C", str(cwd), "hash-object", "-w", "--path", rel, "--stdin"],
                                  input=subject.post_bytes(rel), capture_output=True)
            if post.returncode:
                raise Unusable(f"cannot write checked blob for {rel}: {post.stderr.decode().strip()}")
            for mandate in mandates:
                stem = str(args.out) + ".inline"
                _write(stem + ".blobs.json", {rel: post.stdout.decode().strip()})
                _write(stem + ".from.json", {rel: previous})
                cmd = ["record", "--stage", args.stage, "--subject", args.subject, "--inline", "fold",
                       "--outcome", "clean", "--mandate", mandate, "--files", rel,
                       "--blobs", stem + ".blobs.json", "--from", stem + ".from.json"]
                if args.no_new_decision:
                    cmd.append("--no-new-decision")
                if args.task:
                    cmd += ["--task", args.task]
                code, _out, err = ledger(cwd, *cmd)
                if code:
                    raise Unusable(f"review_ledger record --inline failed for {mandate}: {err.strip()}")
                written += 1
            if set(mandates) == wanted:
                recorded.add(rel)
    return written, recorded


def reviewed_blobs(subject):
    """path -> the blob sha `review_ledger.py` would hash for the snapshot bytes of each subject file, through the
    same checkout filters it applies to the disk; a path absent at the snapshot has no entry."""
    out = {}
    for rel in subject.files:
        pre = subject.pre_bytes(rel)
        if pre is None:
            continue
        objects = subject.snapshot.get("objects")
        where = ["-C", objects, "hash-object", "-w", "--stdin"] if objects else ["-C", str(subject.root), "hash-object", "-w",
                                                                         "--path", rel, "--stdin"]
        done = subprocess.run(["git", *where], input=pre, capture_output=True)
        if done.returncode != 0:
            raise Unusable(f"git hash-object failed for the snapshot bytes of {rel}: {done.stderr.decode().strip()}")
        out[rel] = done.stdout.decode().strip()
    return out


def _common_dir(root):
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else None


def ledger_home(subjects, root):
    """(ledger checkout, subjects it records, subjects from another repository). The ledger lives in the
    invoking repository: the first subject in it (a worktree subject records in its own checkout), else `root`.
    A subject from another repository is never the home, since the frozen head and premises name this one."""
    home_repo = _common_dir(root)
    recorded = [s for s in subjects if home_repo is not None and _common_dir(s.root) == home_repo]
    foreign = [s for s in subjects if s not in recorded]
    return (recorded[0].root if recorded else Path(root)), recorded, foreign


def record_panel(cwd, args, src, subjects):
    """The panel's rows over every subject file, hashed as the snapshot bytes the panel read. A row over
    the folded disk would claim bytes no seat read, and `plan` could then find no earlier blob to merge a later
    round from."""
    if src.record is None:
        return None
    flag, value = src.record
    files = {f for s in subjects for f in s.files}
    if args.blobs:
        # A frozen blob map names the reviewed subject; without it a fold-less review records no file.
        frozen = Path(args.blobs)
        try:
            files |= set(json.loads(frozen.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass  # record reads the same map and reports it unreadable

    files = sorted(files)
    if not files and not args.scope_json and not args.blobs:
        return (1, "", "the review names no reviewed file (no fold subject, --blobs or --scope-json), so its "
                       "ledger row would cover nothing: record it with the reviewed blob map (fan-out "
                       "--ledger-blobs) or the scope file (--ledger-scope-json)")
    cmd = ["record", "--stage", args.stage, "--subject", args.subject, flag, value, "--roster", args.roster]
    if files:
        cmd += ["--files", *files]
    blobs = {rel: sha for s in subjects for rel, sha in reviewed_blobs(s).items()}
    if args.blobs:
        cmd += ["--blobs", args.blobs]
    elif blobs:
        path = Path(args.out).with_name(Path(args.out).name + ".reviewed_blobs.json")
        _write(path, blobs)
        cmd += ["--blobs", str(path)]
    if flag == "--workflow-dir":
        cmd += ["--seats-file", args.seats_file]
    for field in ("premise_commits", "head"):
        if getattr(args, field):
            cmd += ["--" + field.replace("_", "-"), getattr(args, field)]
    if args.scope_json:
        cmd += ["--scope-json", args.scope_json]
    if args.task:
        cmd += ["--task", args.task]
    if args.no_new_decision:
        cmd.append("--no-new-decision")
    return ledger(cwd, *cmd)


def candidate_mandates(blocks, roles):
    candidates = {}
    for block in blocks:
        for rel, reasons in block["fileUnits"].items():
            mine = candidates.setdefault(rel, set())
            coherence = (roles.get("coherence") or {}).get("code" if fold_check.is_code_path(rel) else "meta")
            if "grounding" in reasons and roles.get("grounding"):
                mine.add(roles["grounding"])
            if "seam" in reasons and coherence:
                mine.add(coherence)
            if "unaccounted" in reasons:
                mine.update(block.get("fileMandates", {}).get(rel, []))
                if coherence:
                    mine.add(coherence)
            if any(reason.split(":")[0] in ("fold", "ruling", "check", "noTest") for reason in reasons):
                mine.update(block.get("fileMandates", {}).get(rel, []))
    return candidates


def roster_mandates(candidates, roster):
    """`candidates` with each seat key the roster names replaced by that seat's mandates: a finding's `agent` is its
    seat key, and a lens split over several seats has keys no registry lens carries."""
    by_key = {seat["key"]: seat.get("mandates") or [] for seat in roster if isinstance(seat.get("key"), str)}
    return {rel: {m for key in mandates for m in by_key.get(key) or [key]} for rel, mandates in candidates.items()}


def next_round_uncovered(cwd, args, blocks, roster, rows, roles, candidates=None):
    """Per lens, the mandates and files unseen units leave uncovered; a mandate no roster lens carries
    names the lens that declares it."""
    import review_ledger
    candidates = candidates if candidates is not None else roster_mandates(candidate_mandates(blocks, roles), roster)
    wanted = set().union(*candidates.values()) if candidates else set()
    unit_files = sorted(rel for rel, mandates in candidates.items() if mandates)
    if not wanted or not unit_files:
        return [], []
    seats, taken = [], set()
    for seat in roster:
        mine = [m for m in seat.get("mandates") or [] if m in wanted]
        if mine:
            seats.append({k: v for k, v in dict(seat, mandates=mine).items() if k != "effort"})
            taken.update(mine)
    lenses = review_ledger._lens_sets((*review_ledger.REVIEW_REGISTRIES, "redteam"))
    for mandate in sorted(wanted - taken):
        key = next((key for entries in lenses.values() for key, mandates in entries if mandate in mandates), None)
        if key is None:
            raise Unusable(f"no lens declares later-round mandate {mandate}")
        seats.append({"key": key, "mandates": [mandate], "tier": review_ledger._lens_tier(key)})
    roster_file = Path(str(args.out) + ".candidates.json")
    _write(roster_file, seats)
    cmd = ["plan", "--stage", args.stage, "--subject", args.subject, "--roster", str(roster_file),
           "--files", *unit_files]
    code, out, err = ledger(cwd, *cmd)
    if code:
        raise Unusable(f"review_ledger plan failed: {err.strip()}")
    try:
        planned = json.loads(out)["roster"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Unusable(f"review_ledger plan output is not a roster: {exc}") from None
    return [{k: v for k, v in seat.items() if k != "effort"} for seat in planned], []


def review(args, *, independent=None):
    roles = None
    if args.stage:
        roles = adaptation.get(REPO / ".claude", "later_round_roles").get(args.stage)
        if not isinstance(roles, dict):
            raise Unusable(f"missing adaptation.json later_round_roles row for {args.stage}; add that stage's roles")
    src = read_source(args)
    source = source_flags(args)
    if args.applied:
        if not args.findings:
            raise Unusable("--applied requires --findings")
        src.jobs = [applied_job(args, src)]
        src.record = None
    elif args.ruled:
        views = (fold_check.load_rows(src.findings_path) if src.findings_path else
                 [row for job in src.jobs for row in job.get('rows') or []])
        aliases = {row['id']: fold_check._sources(row) for row in views}
        ruled = {source for value in args.ruled for item in value.split(',') if item
                 for source in aliases.get(item, [item])}
        if not src.jobs or ruled - {row['id'] for row in src.rows}:
            raise Unusable('--ruled requires a fold and known finding ids')
        for job in src.jobs:
            job['ruled'] = sorted(ruled)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fold_out = str(out) + ".fold"
    failures = list(src.errors)
    blocks, fold_report = [], None
    if src.jobs:
        fold_report = fold_check.run(src.findings_path if not args.applied and (args.findings or args.sidecar_dir) else None,
                                     src.jobs, fold_out, independent=independent, original=src.rows)
        _write(fold_out + ".json", fold_report)
        failures += fold_report["failures"]
        independent = {item["id"]: item["verdict"] for item in fold_report["independentVerdicts"]}
        blocks = fold_report["nextRound"]
    import verify_claims
    if src.jobs:
        checked = verify_claims.check_claims([])
        if src.rows:
            result = fold_check._read_json(fold_report['independentVerdictPath'], 'independent verdicts')
            for item in result['verdicts']:
                checked['verdicts'][item['id']] = {key: value for key, value in item.items() if key != 'id'}
                checked['counts'][item['verdict']] += 1
            omitted = verify_claims.check_claims([
                dict(id=row['id'], evidence=[]) for row in src.rows if row['id'] not in checked['verdicts']])
            checked['verdicts'].update(omitted['verdicts'])
            checked['counts']['UNCHECKED'] += omitted['counts']['UNCHECKED']
            checked['errors'].extend(result.get('errors') or [])
            checked['status'] = 'fail' if checked['counts']['UNVERIFIED'] or checked['counts']['UNCHECKED'] or checked['errors'] else 'pass'
    else:
        checker_rows = [dict(id=row['id'], evidence=verify_claims.items_of(row, root=args.root or REPO)) for row in src.rows]
        checked = verify_claims.check_claims(checker_rows, root=args.root or REPO)
    independent = {key: value['verdict'] for key, value in checked['verdicts'].items()}
    subjects = [job["subject"] for job in src.jobs]
    changed = sorted({rel for block in blocks for rel in block["changedFiles"]})
    changed_paths = set()
    for job in src.jobs:
        try:
            changed_paths.update(job["subject"].change_set())
        except Unusable:
            pass  # check_job already recorded fold-snapshot-missing for this subject
    changed_paths = sorted(changed_paths)
    unaccounted = [line for block in blocks for line in block["unaccounted"]]
    ledger_report = {"panelRows": 0, "inlineRows": 0}
    roles_note, unseated = None, []
    seats = [seat for block in blocks for seat in block["seats"]]
    if args.stage and args.subject and args.roster:
        cwd, subjects, foreign = ledger_home(subjects, Path(args.root or REPO))
        if foreign:
            ledger_report["notRecorded"] = sorted(str(s.root) for s in foreign)
        roster = load_roster(args.roster)
        wanted = {m for seat in roster for m in seat.get("mandates") or []}
        before = len(ledger_rows(cwd, args.subject))
        unreadable = any(f.get("kind") == "fold-snapshot-missing" for f in failures)
        done = None if unreadable else record_panel(cwd, args, src, subjects)
        if done is not None and done[0] != 0:
            failures.append({"kind": "ledger-record-failed", "detail": done[2].strip()})
        rows_now = ledger_rows(cwd, args.subject)
        ledger_report["panelRows"] = len(rows_now) - before
        candidates = roster_mandates(candidate_mandates(blocks, roles), roster)
        written, _ = record_inline(cwd, args, wanted, blocks, rows_now, candidates,
                                          failures, subjects, roster)
        ledger_report["inlineRows"] = written
        seats, unseated = next_round_uncovered(cwd, args, blocks, roster, rows_now, roles, candidates)
    units = {}
    for block in blocks:
        for seat in block["seats"]:
            units.setdefault(seat["role"], []).extend(seat.get("units") or seat.get("lines") or [])
    next_round = {"uncovered": seats, "units": units, "change": _merge_change(blocks),
                  "unaccounted": unaccounted}
    if roles_note:
        next_round["roles"] = roles_note
    if unseated:
        next_round["unseated"] = unseated
    held = held_rows(src.rows, src.jobs, views=fold_check.load_rows(src.findings_path, allow_empty=True)
                     if src.findings_path else None)
    held_ids = {entry["id"] for entry in held}
    for entry in (fold_report or {}).get("dispositions") or []:
        if entry.get("disposition") == "out-of-subject" and entry["id"] not in held_ids:
            held.append({"id": entry["id"], "class": "path-scope",
                         "reason": "its file is outside every fold subject, so no fold may edit it"})
            held_ids.add(entry["id"])
    classes = {"file": "unapplied", "finding": "unrouted"}
    id_classes = {"fold-id-coverage": "unreported"}
    failed_checks = set()
    for failure in failures:
        failure.setdefault("ids", [failure["id"]] if failure.get("id") else [])
        scope = fold_check.FAILURE_SCOPE.get(failure["kind"], "subject")
        failure.setdefault("files", sorted({rel for subject in subjects for rel in subject.allowed
                                            if scope == "run" or (scope == "subject" and
                                                failure.get("subject") in (None, subject.root_text))}))
        if scope == "id":
            failed_checks.update(failure["ids"])
        held_class = id_classes.get(failure["kind"], failure["kind"]) if scope == "id" else classes.get(scope)
        if held_class:
            for finding_id in failure["ids"]:
                if finding_id not in held_ids:
                    held.append({"id": finding_id, "class": held_class, "reason": failure["detail"]})
                    held_ids.add(finding_id)
    import shlex
    root = subjects[0].root if subjects else Path(args.root or REPO)
    refold_rows = [row for row in src.rows if row["id"] in held_ids | failed_checks]
    refold_files = sorted({rel for row in refold_rows if (
        rel := next((found for subject in subjects if (found := subject.file_of(row))), None)
        or fold_check.rel_path(fold_check._cited(row.get("file")), root))})
    manifest = str(out) + ".refold.snapshot.json"
    digest = str(out) + ".refold.digest.json"
    findings_path = fold_out + ".findings.json"
    _write(findings_path, {"findings": refold_rows})
    snapshot_cmd = ["python3", ".claude/tools/post_check.py", "snapshot", "--root", str(root),
                    "--files", *refold_files, "--out", manifest]
    check_cmd = ["python3", ".claude/tools/post_check.py", "review", "--findings", findings_path,
                 "--digest", digest, "--snapshot", manifest]
    for flag in ("stage", "subject", "roster", "task"):
        if getattr(args, flag):
            check_cmd += ["--" + flag, getattr(args, flag)]
    if held_ids:
        check_cmd += ['--ruled', ','.join(sorted(held_ids))]
    check_cmd += ["--out", str(out) + ".refold.json"]
    next_round["refold"] = {"findings": findings_path, "held": sorted(held_ids),
                            "failedCheck": sorted(failed_checks), "files": refold_files,
                            "snapshot": shlex.join(snapshot_cmd), "check": shlex.join(check_cmd)}
    report = {**checked, "status": "fail" if failures or checked["status"] != "pass" else "pass",
              "outcome": "fail" if checked["errors"] or any(
                  fold_check.FAILURE_SCOPE.get(f["kind"], "subject") not in ("id", "file", "finding")
                  or (fold_check.FAILURE_SCOPE.get(f["kind"]) == "finding" and not f.get("ids")) for f in failures)
              else "pass",
              "unverified": sorted(i for i, v in independent.items() if v == "UNVERIFIED"),
              "held": held, "failures": failures, "ledger": ledger_report,
              "nextRound": next_round, "changedPaths": changed_paths,
              "foldReport": fold_out + ".json" if fold_report else None, "mode": "review", "source": source}
    if src.selection is not None:
        report["reviewerSelection"] = src.selection
    report["errors"].extend(f"{f['kind']}: {f['detail']}" for f in failures)
    _write(out, report)
    for failure in failures:
        print(f"post_check: {failure['kind']}: {failure['detail']}", file=sys.stderr)
    counts = report.get("counts") or {}
    print(f"post_check: outcome {report['outcome']}, status {report['status']} ({len(failures)} failure(s)); claims {counts.get('VERIFIED', 0)} verified, "
          f"{counts.get('UNVERIFIED', 0)} unverified, {counts.get('UNCHECKED', 0)} unchecked; {len(held)} held -> {out}")
    return 0 if report["outcome"] == "pass" else 1


def _merge_change(blocks):
    measured = [block["change"] for block in blocks if block.get("change")]
    if not measured:
        return None
    keys = ("lines", "changedLines", "units", "unplacedLines", "unitsReached")
    merged = {key: sum(m.get(key, 0) for m in measured) for key in keys}
    merged["files"] = {rel: m for part in measured for rel, m in part["files"].items()}
    return merged


# ---- claims ---------------------------------------------------------------------------------------


def _journal_results(directory):
    """(label, result) per agent of a Workflow transcript directory, in start order, the latest result winning."""
    journal = Path(directory) / "journal.jsonl"
    try:
        events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise Unusable(f"workflow journal unreadable: {journal}: {exc}") from None
    labels, results, latest = {}, {}, {}
    for event in events:
        if isinstance(event, dict) and event.get("agentId"):
            if event.get("type") == "started":
                label = str(event.get('label') or event['agentId'])
                if event.get('phase') not in ('Verify', 'Fold', 'Merge') and not label.startswith('verify-'):
                    labels[event["agentId"]] = label
                    latest[event.get('key') or event['agentId']] = event['agentId']
            elif event.get("type") == "result":
                results[event["agentId"]] = event.get("result")
    return [(labels[agent], results.get(agent)) for agent in latest.values()]


def _sidecar_results(directory):
    """(label, deliverable text) per job of a sidecar fan-out out dir: `<label>.md` beside each `<label>.out.json`."""
    import dispatch_result
    found = []
    directory = Path(directory)
    labels = {path.name[:-len('.out.json')] for path in directory.glob('*.out.json')}
    for path in sorted(directory.glob('fanout-*.inventory.json')):
        inventory = fold_check._read_json(path, 'fan-out inventory')
        jobs = inventory.get('jobs') if isinstance(inventory, dict) else None
        if not isinstance(jobs, list) or any(not isinstance(job, dict) or
                not isinstance(job.get('label'), str) or not job['label'].strip() for job in jobs):
            raise Unusable(f'malformed fan-out inventory: {path}')
        labels.update(job['label'] for job in jobs)
    for label in sorted(labels):
        if label.startswith(('review-fold-', 'verify-')):
            continue
        path = dispatch_result.deliverable_path(directory, label)
        found.append((label, path.read_text(encoding='utf-8', errors='replace') if path.is_file() else None))
    if not found:
        raise Unusable(f"sidecar out dir holds no <label>.out.json: {directory}")
    return found


def run_label_directory(label):
    """Find the live run: the one journal whose verifier with this label has started and not returned.

    The label is derived from the checked items, so a finished run over the same items shares it;
    that run's verifier has a result and is skipped. Two live matches stay unusable. The run's store
    is the session's launch directory's, which this checkout (a worktree's copy of the tool, say) may
    not be, so every checkout of the repo is searched. A miss names the stores searched and a
    duplicate names its journals, so a stale tool copy or a wrong config dir reads off the error.
    A line that does not parse is skipped alone; the journal's other lines still count."""
    import session_digest
    stores = session_digest.project_dirs(str(REPO))
    journals = {journal for project in stores for journal in project.glob("*/subagents/workflows/*/journal.jsonl")}
    found = []
    for journal in sorted(journals):
        started, returned = set(), set()
        try:
            with journal.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line) if line.strip() else {}
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "started" and event.get("label") == label:
                        started.add(event.get("agentId"))
                    elif event.get("type") == "result":
                        returned.add(event.get("agentId"))
        except (OSError, ValueError):
            continue
        if started - returned:
            found.append(journal.parent)
    if len(found) != 1:
        holding = sorted({journal.parents[4].name for journal in journals})
        where = (f"searched {len(stores)} stores under {', '.join(sorted({str(s.parent) for s in stores}))};"
                 f" journals in: {', '.join(holding) or 'none'}" if not found
                 else f"live: {', '.join(map(str, found))}")
        raise Unusable(f"run label {label!r} matched {len(found)} live workflow journals; expected one; {where}")
    return found[0]


_BLOCK = re.compile(r"```(?:claims|findings)[ \t]*\r?\n(.*?)```", re.S)
CLAIM_MEMBERS = ('claims', 'findings', 'overlaps')  # the arrays a structured result carries its checked rows in


def result_claims(label, value, *, preserve=False, source_indices=False, required=False, root=None):
    """Complete canonical rows from a structured result or JSON Lines deliverable; a file label resolves in `root`.

    Prose without a block, or no value, is no claims. `required=True` makes either an error: only a
    caller whose job contract demands a claims deliverable (`claims_rows`) passes it."""
    import verify_claims
    errors, found = [], []
    if isinstance(value, str):
        blocks = _BLOCK.findall(value)
        if not blocks and required:
            errors.append(f'{label}: no claims or findings block')
        for index, raw in enumerate((line for block in blocks for line in block.splitlines() if line.strip()), 1):
            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError('not an object')
                found.append((index, obj))
            except ValueError as exc:
                errors.append(f'{label}#{index}: {exc}')
    elif isinstance(value, list):
        found = list(enumerate(value, 1))
    elif isinstance(value, dict):
        if (value.get('delivery') or {}).get('payloadComplete') is False:
            raise Unusable('bounded result shed claims or findings')
        if label.startswith('reach:') and 'ruling' in value:
            value = dict(value)
            subject_root = Path(value.get('root') or '')
            value['evidence'] = [
                {'file': str(verify_claims.resolve_path(subject_root, cite['path'])), 'quote': cite['quote']}
                if value.get('root') and isinstance(cite, dict)
                and isinstance(cite.get('path'), str) and isinstance(cite.get('quote'), str)
                else {}
                for cite in value.get('cites', [])
            ]
            member = [value]
        else:
            member = next((value[key] for key in CLAIM_MEMBERS if key in value), None)
        if isinstance(member, list):
            found = list(enumerate(member, 1))
        else:
            errors.append(f'{label}: no claims or findings array')
    elif required:
        errors.append(f'{label}: malformed required deliverable')
    rows = []
    for index, obj in found:
        if not isinstance(obj, dict):
            errors.append(f'{label}#{index}: not an object')
            continue
        row = dict(obj) if preserve else {}
        source_key = f'{label}#{index}' if label.startswith(('reach:', 'worklog:')) else obj.get('id') or f'{label}#{index}'
        row.update(id=str(source_key),
                   evidence=verify_claims.items_of(obj, root=root or REPO), polarity=obj.get('polarity'))
        if source_indices:
            row['__sourceIndex'] = index
        rows.append(row)
    # A worklog-relevance result (`overlaps` beside a `checked` object) carries provenance that is checked too.
    if isinstance(value, dict) and isinstance(value.get('checked'), dict) and (
            label.startswith('worklog:') or 'overlaps' in value):
        row = dict(id=f'{label}#checked', evidence=verify_claims.items_of(value['checked'], root=root or REPO))
        if source_indices:
            row['__sourceIndex'] = 'checked'
        rows.append(row)
    return rows, errors


def claims_rows(args):
    """(rows, errors, session id): every claim the claims mode checks, keyed as its engine keys it.

    The Stop audit reads through this too, so its recompute keys match the in-run report's."""
    import verify_claims
    directory, session_id = None, None
    if args.run_label:
        args.workflow_dir = str(run_label_directory(args.run_label))
    if args.workflow_dir:
        directory = Path(args.workflow_dir)
    if directory is not None:
        results = _journal_results(directory)
        session_id = directory.parents[2].name
        if any(label.startswith('explore:') for label, _ in results):
            import session_digest
            archive = session_digest.build_workflow_result_archive(directory, 'explore', root=args.root, verify=False)
            rows = [dict(id=f"{item['lens']}#{item['index']}", evidence=verify_claims.items_of(item['claim'], root=args.root or REPO),
                         polarity=item['claim'].get('polarity')) for item in archive['items']]
            coverage = [] if archive['status'] == 'completed' else [f"workflow coverage is {archive['status']}"]
            return rows, coverage, session_id
    elif args.sidecar_dir:
        results = _sidecar_results(args.sidecar_dir)
    else:
        path = Path(args.result)
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as exc:
            raise Unusable(f'result unreadable: {path}: {exc}') from None
        try:
            value = json.loads(text)
        except ValueError:
            value = text
        results = [(path.stem, value)]
    rows, errors = [], []
    for label, value in results:
        has_block = isinstance(value, str) and _BLOCK.search(value)
        if not has_block and args.spill_dir:
            spill = Path(args.spill_dir) / f'{label}.md'
            if spill.is_file():
                value = spill.read_text(encoding='utf-8')
        if value is None:
            errors.append(f'{label}: producing job has no result')
            continue
        found, bad = result_claims(label, value, required=True, root=args.root)
        rows.extend(found)
        errors.extend(bad)
    if not results:
        errors.append('no producing jobs found')
    return rows, errors, session_id


def claims_report(args):
    """All claims modes use the tracer's report and the same evidence checker."""
    import verify_claims
    rows, errors, session_id = claims_rows(args)
    report = verify_claims.check_claims(rows, root=args.root or REPO, session_id=session_id)
    report['errors'].extend(errors)
    if report['errors']:
        report['status'] = 'fail'
    report.update(mode='claims', source=source_flags(args))
    return report


def claims(args):
    report = claims_report(args)
    _write(args.out, report)
    print(f"post_check: claims {report['status']} -> {args.out}")
    return 0 if report['status'] == 'pass' else 1


# ---- snapshot -------------------------------------------------------------------------------------


def snapshot(args):
    if not args.files and not args.base:
        raise Unusable("snapshot needs --files (blobs) or --base (a revision)")
    manifest = (fold_check.snapshot_rev(args.root, args.base, args.files or (), args.new_paths or ()) if args.base
                else fold_check.snapshot_blobs(args.root, args.files, args.new_paths or ()))
    _write(args.out, manifest)
    detail = (f"{len(manifest['files'])} file(s)" if manifest["kind"] == "blobs"
              else f"base {manifest['base'][:9]}, {len(manifest.get('files') or [])} file(s)")
    print(f"post_check: {manifest['kind']} snapshot of {detail} -> {args.out}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="post_check.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = ap.add_subparsers(dest="command", required=True)
    snap = subs.add_parser("snapshot", help="write the pre-fold manifest (blobs, or a base revision)")
    snap.add_argument("--root", required=True)
    snap.add_argument("--files", nargs="+")
    snap.add_argument("--base", help="a rev snapshot: the base revision, with the `--files` findings may route to")
    snap.add_argument("--new-paths", nargs="+", help="files the fold may create, test files included")
    snap.add_argument("--out", required=True)
    rev = subs.add_parser("review", help="verify a findings dispatch and close its fold")
    rev.add_argument("--run-label")
    rev.add_argument("--result")
    rev.add_argument("--root")
    rev.add_argument("--spill-dir")
    rev.add_argument("--expect-total", type=int)
    rev.add_argument("--expect-sources", type=int)
    rev.add_argument("--workflow-dir")
    rev.add_argument("--sidecar-dir")
    rev.add_argument("--findings")
    rev.add_argument("--applied", action="append", default=[])
    rev.add_argument("--ruled", action="append", default=[])
    rev.add_argument("--files", nargs="+")
    rev.add_argument("--digest", action="append", default=[])
    rev.add_argument("--snapshot", action="append", default=[])
    rev.add_argument("--stage")
    rev.add_argument("--subject")
    rev.add_argument("--roster")
    rev.add_argument("--scope-json")
    rev.add_argument("--seats-file", help="launched seats beside the report; numbers review sources in launch order and records from the live workflow journal")
    rev.add_argument("--premise-commits", help="the frozen audit commit list, passed unchanged to record")
    rev.add_argument("--head", help="the frozen reviewed commit, passed unchanged to record")
    rev.add_argument("--blobs", help="the frozen reviewed blob map, passed unchanged to record")
    rev.add_argument("--task", help="the task id every ledger row carries, so a drive's reviews list as one unit")
    rev.add_argument("--no-new-decision", action="store_true")
    rev.add_argument("--out", required=True)
    clm = subs.add_parser("claims", help="quote verdicts over the claims a dispatch returned")
    where = clm.add_mutually_exclusive_group(required=True)
    where.add_argument("--run-label", help="the running verifier's unique journal label")
    where.add_argument("--workflow-dir")
    where.add_argument("--sidecar-dir")
    where.add_argument("--result")
    clm.add_argument("--root")
    clm.add_argument("--spill-dir")
    clm.add_argument("--out", required=True)
    return ap


def main(argv=None, *, independent=None):
    args = build_parser().parse_args(argv)
    for field in ("root", "out", "workflow_dir", "sidecar_dir", "findings", "result", "spill_dir",
                  "roster", "scope_json", "seats_file", "premise_commits", "blobs"):
        if getattr(args, field, None):
            setattr(args, field, str(Path(getattr(args, field)).resolve()))
    for field in ("digest", "snapshot"):
        if getattr(args, field, None):
            setattr(args, field, [str(Path(value).resolve()) for value in getattr(args, field)])
    try:
        if args.command == "snapshot":
            return snapshot(args)
        if args.command == "claims":
            return claims(args)
        chosen = [name for name in ("run_label", "workflow_dir", "sidecar_dir", "findings", "result") if getattr(args, name)]
        if len(chosen) != 1:
            print("post_check: review needs exactly one of --workflow-dir, --sidecar-dir, --findings", file=sys.stderr)
            return 2
        if any((args.stage, args.subject, args.roster)) and not all((args.stage, args.subject, args.roster)):
            print("post_check: --stage, --subject and --roster go together", file=sys.stderr)
            return 2
        return review(args, independent=independent)
    except (Unusable, OSError, ValueError) as exc:
        if args.command != 'snapshot':
            report = {"status": "unavailable", "counts": {"VERIFIED": 0, "UNVERIFIED": 0, "UNCHECKED": 0},
                      "verdicts": {}, "errors": [str(exc)]}
            if args.command == 'review':
                report.update(held=[], failures=[], ledger={"panelRows": 0, "inlineRows": 0},
                              nextRound={"uncovered": []}, changedPaths=[], foldReport=None)
            _write(args.out, report)
        print(f"post_check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
