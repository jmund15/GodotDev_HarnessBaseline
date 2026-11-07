#!/usr/bin/env python3
"""Proof for sidecar_fanout's `needs` input filter: a listed row whose catalog input lacks a needed type is
dropped once with its reason, an unknown (inputGap) row stays, and a job no listed row can serve refuses."""
import contextlib
import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)
GAP = {"reason": "no catalog entry", "checked": "https://example.test/api.json", "asOf": "2026-11-05"}
DATA = {"transports": {}, "models": [
    {"id": "img-row", "alias": "img", "transport": "go", "input": ["text", "image"]},
    {"id": "text-row", "alias": "txt", "transport": "go", "input": ["text"]},
    {"id": "unknown-row", "alias": "unk", "transport": "go", "inputGap": GAP},
]}
spec = importlib.util.spec_from_file_location("fanout_needs_under_test",
                                              os.path.join(TOOLS, "sidecar_fanout.py"))
fan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fan)


def raises(fn, needle):
    try:
        fn()
    except fan.JobError as exc:
        return needle in str(exc)
    except Exception:
        return False
    return False


def drop(job):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        kept = fan._drop_unfit_rows([job], DATA)
    return kept[0], err.getvalue()


def main():
    cases = []

    def case(label, check):
        try:
            ok = bool(check())
        except Exception as exc:
            ok = False
            label += " (CRASH %s: %s)" % (type(exc).__name__, exc)
        cases.append((label, ok))

    case("normalize_job accepts a needs list",
         lambda: fan.normalize_job({"label": "j", "rows": {"img": "low"}, "needs": ["image"]}, DATA)["needs"] == ["image"])
    case("normalize_job refuses an empty needs list",
         lambda: raises(lambda: fan.normalize_job({"label": "j", "rows": {"img": "low"}, "needs": []}, DATA), "needs"))
    case("normalize_job refuses an unknown input type",
         lambda: raises(lambda: fan.normalize_job({"label": "j", "rows": {"img": "low"}, "needs": ["hologram"]}, DATA), "needs"))
    case("a row whose catalog input lacks the need is dropped",
         lambda: set(drop({"label": "j", "rows": {"img": "low", "txt": "low", "unk": "low"}, "needs": ["image"]})[0]["rows"]) == {"img", "unk"})
    case("the drop is named once, with the row and the missing type",
         lambda: (lambda err: err.count("dropping row txt") == 1 and "lacks image" in err)(
             drop({"label": "j", "rows": {"img": "low", "txt": "low"}, "needs": ["image"]})[1]))
    case("an inputGap row stays: unknown is not wrong",
         lambda: "unk" in drop({"label": "j", "rows": {"unk": "low", "txt": "low"}, "needs": ["image"]})[0]["rows"])
    case("a job needing nothing is unchanged",
         lambda: fan._drop_unfit_rows([{"label": "j", "rows": {"txt": "low"}}], DATA)[0]["rows"] == {"txt": "low"})
    case("every listed row unfit refuses naming needs",
         lambda: raises(lambda: drop({"label": "j", "rows": {"txt": "low"}, "needs": ["image"]}), "needs"))
    case("a single-row job whose row cannot serve refuses naming the type",
         lambda: raises(lambda: drop({"label": "j", "alias": "txt", "effort": "low", "needs": ["image"]}), "image"))

    failed = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
