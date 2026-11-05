
def run_review_fold(review, args, data, out_dir, seats=()):
    """Route each view row to one subject and launch one author per affected subject, its workdir the subject's
    root. Python writes the findings and the quote verdicts; the author writes only its subject files and its
    digest. `fold_jobs.json` names each subject for `post_check.review`, which decides the fold."""
    rows = review["view"]
    subjects = [fold_check.parse_subject_spec(spec) for spec in args.fold_subject]
    routed, _unrouted, _ambiguous = fold_check.route(rows, subjects)
    jobs = []
    for index, subject in enumerate(subjects, 1):
        label = FOLD_LABEL.format(index)
        jobs.append({"subject": subject, "label": label, "digest": str(out_dir / f"{label}_digest.json"),
                     "rows": [row for row in rows if routed.get(row["id"]) == index - 1]})
    _independent, verdicts = fold_check.independent_verdicts(rows, subjects, out_dir / "fold_input")
    authors = []
    for job in jobs:
        if not job["rows"]:
            with open(job["digest"], "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"applied": [], "deferred": [], "decisions": [], "verdicts": [],
                           "mechanicalChecks": []}, stream)
            continue
        subject = job["subject"]
        findings = out_dir / f"{job['label']}_findings.json"
        findings.write_text(json.dumps({"findings": job["rows"]}, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8", newline="\n")
        writes = [_posix(subject.root / item) for item in subject.files + subject.new_paths]
        prompt = out_dir / f"{job['label']}_prompt.md"
        prompt.write_text("\n".join(line for line in [
            f"FOLD STAGE of a review. Read {_posix(args.fold)} with the Read tool and follow it.",
            f"Root: {_posix(subject.root)}",
            f"Snapshot: {_posix(subject.manifest_path)}",
            f"Files: {', '.join(subject.files)}",
            f"New paths: {', '.join(subject.new_paths)}" if subject.new_paths else None,
            f"Findings file: {_posix(findings)}",
            f"Verdicts file: {_posix(verdicts)}",
            f"Digest file: {_posix(job['digest'])}",
            "The findings and verdicts files already exist: read them and never rewrite them. The snapshot is the "
            "caller's: never write it.",
            f"Write only: {', '.join(writes + [_posix(job['digest'])])}. Return verdictPath: {_posix(verdicts)}.",
            "",
            f"CONSOLIDATED FINDINGS ({len(job['rows'])} ids: "
            + ", ".join(row["id"] for row in job["rows"]) + "):",
            data_block(job["rows"]),
            "",
        ] if line is not None), encoding="utf-8", newline="\n")
        authors.append((job, dict(fold_probe(args, seats, data), label=job["label"], promptFile=str(prompt),
                                  workdir=str(subject.root))))
    planned = add_concurrency_guard([plan_job(spec, data, out_dir, args.authorize) for _job, spec in authors],
                                    [spec for _job, spec in authors], args.max_parallel, out_dir)
    results = run_jobs(planned, args.max_parallel, out_dir) if planned else {}
    author_failures = []
    for job, spec in authors:
        run = results.get(spec["label"]) or {}
        if run.get("exit") != 0:
            author_failures.append({"kind": "fold-author-failed", "subject": str(job["subject"].root),
                                    "detail": f"{spec['label']} exited {run.get('exit')!r}"})
    with open(out_dir / "fold_jobs.json", "w", encoding="utf-8", newline="\n") as stream:
        json.dump([dict(job["subject"].describe(), digest=job["digest"],
                        artifacts=[str(out_dir / f"{job['label']}_findings.json"), str(verdicts)]) for job in jobs],
                  stream, indent=2)
        stream.write("\n")
    return {"jobs": jobs, "authors": authors, "results": results, "failures": author_failures}


def finish_fold(review, fold, report, out_dir):
    """`fold_digest.json`: its status comes from the post-check's fold report and the author exits; each author's digest
    supplies only the applied ids `appliedWithAlternative` lists."""
    fold_report = json.loads(Path(report["foldReport"]).read_text(encoding="utf-8")) if report.get("foldReport") else {
        "failures": [], "dispositions": [], "independentVerdictPath": None}
    applied_ids = []
    for job in fold["jobs"]:
        try:
            value = json.loads(Path(job["digest"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        applied_ids += [entry.get("id") for entry in value.get("applied") or []
                        if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
    failures = fold_report["failures"] + fold["failures"]
    digest = {"status": "fail" if failures else "pass", "failures": failures,
              "dispositions": fold_report["dispositions"],
              "appliedWithAlternative": review_result.applied_with_alternative(review["view"], applied_ids),
              "foldCheck": report.get("foldReport"),
              "postCheck": str(out_dir / "post_check.json"),
              "independentVerdictPath": fold_report.get("independentVerdictPath"),
              "authors": [{"label": spec["label"], "subject": str(job["subject"].root), "digest": job["digest"],
                           "exit": (fold["results"].get(spec["label"]) or {}).get("exit")}
                          for job, spec in fold["authors"]]}
    with (out_dir / "fold_digest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(digest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return digest


def close_review(args, out_dir):
    """Run `post_check.review` over this run's `review.json` (and `fold_jobs.json`), writing `post_check.json`."""
    argv = ["review", "--sidecar-dir", str(out_dir), "--out", str(out_dir / "post_check.json")]
    for flag, value in (("--stage", args.ledger_stage), ("--subject", args.ledger_subject),
                        ("--roster", args.ledger_roster), ("--scope-json", args.ledger_scope_json),
                        ("--task", args.ledger_task)):
        if value:
            argv += [flag, value]
    if args.no_new_decision:
        argv.append("--no-new-decision")
    code = post_check.main(argv)
    if code == 2:
        raise fold_check.Unusable("post_check refused the review (its reason is printed above)")
    return code
