#!/usr/bin/env python3
"""The launch tracker reads a fan-out jobs file in every shape `sidecar_fanout.py` accepts.

`sidecar_fanout.load_jobs` takes a JSON list of jobs or a dispatch.js args object `{jobs: [...]}`,
and renames dispatch.js fields (`model` -> `alias`). `orchestration_metrics.sidecar_launches` feeds
the dispatch table and the record watcher; when it read only the list form, an object-form fan-out
showed as `unresolved`, and `watch_records.py --session --wake` woke with `UNRESOLVED jobs.json`
while every job was still running (2026-09-29).
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import orchestration_metrics as om  # noqa: E402


def _launches(spec):
    with tempfile.TemporaryDirectory() as tmp:
        jobs = os.path.join(tmp, "jobs.json")
        with open(jobs, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(spec, fh)
        out = os.path.join(tmp, "out")
        cmd = "python3 .claude/tools/sidecar_fanout.py %s --out-dir %s --max-parallel 2 2>&1 | tail -5" % (
            jobs.replace("\\", "/"), out.replace("\\", "/"))
        rows = om.sidecar_launches(cmd, tmp)
        return rows, out


def main():
    failures = []
    job_a = {"label": "lens-a", "alias": "fixture-alias", "effort": "medium", "promptFile": "a.md"}
    job_b = {"label": "lens-b", "model": "fixture-alias", "effort": "high", "promptPath": "b.md"}

    for name, spec in (("list form", [job_a, dict(job_b)]),
                       ("object form", {"jobs": [job_a, dict(job_b)], "contextPath": "ctx.md"})):
        rows, out = _launches(spec)
        got = [(r.get("label"), r.get("model"), r.get("effort"), r.get("record")) for r in rows]
        want = [("lens-a", "fixture-alias", "medium", os.path.join(out, "lens-a.record.json")),
                ("lens-b", "fixture-alias", "high", os.path.join(out, "lens-b.record.json"))]
        norm = lambda xs: [(a, b, c, os.path.normcase(os.path.abspath(d)) if d else d) for a, b, c, d in xs]
        if norm(got) != norm(want):
            failures.append("%s: sidecar_launches -> %r, want %r" % (name, got, want))

    with tempfile.TemporaryDirectory() as tmp:
        jobs = os.path.join(tmp, "jobs.json")
        spill = os.path.join(tmp, "spill")
        with open(jobs, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"jobs": [job_a], "spillDir": spill.replace("\\", "/")}, fh)
        rows = om.sidecar_launches("python3 .claude/tools/sidecar_fanout.py %s --detach" % jobs.replace("\\", "/"), tmp)
        got = [os.path.normcase(os.path.abspath(r.get("record"))) for r in rows if r.get("record")]
        if got != [os.path.normcase(os.path.join(spill, "lens-a.record.json"))]:
            failures.append("spillDir with no --out-dir: records %r, want them under the spillDir %s" % (got, spill))

    out_text = open(os.path.join(HERE, "fixtures", "dispatch_table", "fanout_detach_output.txt"),
                    encoding="utf-8").read()
    for name, rows, output, want in (
            ("one listed row is the pin", {"fixture-alias": "high"}, "", ("fixture-alias", "high")),
            ("several rows take the placement the call printed", {"fixture-alias": "high", "fixture-alt": "high"}, out_text,
             ("sonnet", "high")),
            ("several rows with no printed placement are candidates", {"fixture-alias": "high", "fixture-alt": "medium"}, "",
             ("fixture-alias|fixture-alt", "high|medium")),
            ("candidates with one shared effort show it once", {"fixture-alias": "high", "fixture-alt": "high"}, "",
             ("fixture-alias|fixture-alt", "high"))):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = os.path.join(tmp, "jobs.json")
            with open(jobs, "w", encoding="utf-8", newline="\n") as fh:
                json.dump([{"label": "bb-fx16", "promptFile": "a.md", "rows": rows}], fh)
            rows_out = om.sidecar_launches("python3 .claude/tools/sidecar_fanout.py %s --detach"
                                           % jobs.replace("\\", "/"), tmp, output=output)
        got = [(r.get("model"), r.get("effort")) for r in rows_out]
        if got != [want]:
            failures.append("rows job, %s: sidecar_launches -> %r, want %r" % (name, got, [want]))

    with tempfile.TemporaryDirectory() as tmp:
        jobs, out = os.path.join(tmp, "jobs.json"), os.path.join(tmp, "out")
        os.makedirs(out)
        with open(jobs, "w", encoding="utf-8", newline="\n") as fh:
            json.dump([job_a, dict(job_b, label="lens-b")], fh)
        native = os.path.join(out, "native.args.json")
        with open(native, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"jobs": [{"label": "lens-a", "model": "fixture-alias", "effort": "medium", "promptPath": "a.md"}],
                       "spillDir": out}, fh)
        rows_out = om.sidecar_launches("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s --detach" % (
            jobs.replace("\\", "/"), out.replace("\\", "/")), tmp)
        kinds = sorted((r.get("kind"), r.get("label")) for r in rows_out)
        if kinds != [("fanout", "lens-b"), ("handoff", "lens-a")]:
            failures.append("native split: sidecar_launches -> %r, want lens-a only as one handoff" % kinds)
        hand = next((r for r in rows_out if r.get("kind") == "handoff"), {})
        if os.path.normcase(os.path.abspath(hand.get("jobs_file") or "x")) != os.path.normcase(os.path.abspath(native)):
            failures.append("handoff row names %r, want the native args file %s" % (hand.get("jobs_file"), native))

    rows, _out = _launches({"jobs": "not a list"})
    if [r.get("model") for r in rows] != ["unresolved"]:
        failures.append("unreadable spec: want one `unresolved` row, got %r" % rows)

    for f in failures:
        print("FAIL", f)
    print("PASS" if not failures else "%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
