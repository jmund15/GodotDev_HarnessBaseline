#!/usr/bin/env python3
"""Re-runnable proof for the `## Job map` gate in tools/lens_briefs.py `subject_plans`.

`/plan_check` passes every subject plan through `subject_plans` before any brief is spent. A plan
of either shape without a `## Job map` heading (`plan_file_format.md` §Job map) is refused; a plan
with the heading passes. The refusal is the real function's exit, not a stub's return.

    python3 .claude/tests/test_lens_briefs_job_map.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
TOOL = os.path.join(CLAUDE, "tools", "lens_briefs.py")
JOB_MAP = "\n## Job map\n\n| Slice | Write set | Waits on | Route |\n|---|---|---|---|\n| 1 | src/app.cs | none | job |\n"
META_NO_MAP = "# Meta plan\n\n**Shape:** meta\n\n## Surfaces\n\n```surfaces\n- .claude/tools/x.py\n```\n"


def load_tool():
    spec = importlib.util.spec_from_file_location("lens_briefs_job_map_probe", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refused(lb, spec):
    """(True, message) when subject_plans exits on `spec`; (False, result) when it accepts it."""
    try:
        return False, lb.subject_plans([spec])
    except SystemExit as exc:
        return True, str(exc.code)


def main():
    tmp = tempfile.mkdtemp(prefix="lens_briefs_job_map_")
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))

    try:
        lb = load_tool()
        code_no_map = os.path.join(tmp, "code-plan.md")
        with open(code_no_map, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Code plan\n\n## Slices\n\n1. app\n")
        code_map = os.path.join(tmp, "code-plan-mapped.md")
        with open(code_map, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Code plan\n\n## Slices\n\n1. app\n" + JOB_MAP)
        meta_no_map = os.path.join(tmp, "meta-plan.md")
        with open(meta_no_map, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(META_NO_MAP)
        meta_map = os.path.join(tmp, "meta-plan-mapped.md")
        with open(meta_map, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(META_NO_MAP + JOB_MAP)
        eleven_slice = os.path.join(tmp, "plan-eleven-slices.md")
        with open(eleven_slice, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Eleven-slice plan\n\n## Slices\n\n" + "".join("%d. slice %d\n" % (n, n) for n in range(1, 12)))

        refused_code, msg = refused(lb, "code:" + code_no_map)
        check("code plan without `## Job map` is refused", refused_code, msg)
        check("refusal names the Job map section", refused_code and "Job map" in msg, msg)

        refused_meta, msg = refused(lb, "meta:" + meta_no_map)
        check("meta plan without `## Job map` is refused", refused_meta, msg)

        refused_long, msg = refused(lb, "code:" + eleven_slice)
        check("eleven-slice plan without `## Job map` is refused", refused_long, msg)

        accepted_code, got = refused(lb, "code:" + code_map)
        check("code plan with `## Job map` is accepted", not accepted_code and got == [("code", code_map)], got)

        accepted_meta, got = refused(lb, "meta:" + meta_map)
        check("meta plan with `## Job map` passes this gate", not accepted_meta and got == [("meta", meta_map)], got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in results if ok)
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}: {label}" + ("" if ok else f" ({str(detail)[:300]})"))
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
