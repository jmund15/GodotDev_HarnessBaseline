"""Per-test profile of a gate's TRX results.

Usage: test_profile.py <runs-dir> [--top N] [--json]

Reads the *.trx files under <runs-dir> (a gate's TestResults/runs/<runId>/) and reports the slowest N
tests, per-class and per-segment duration totals, the share of total duration held by the slowest
1, 5 and 10 percent of tests, and failed tests with their messages.

A segment is the namespace component after "Tests.Integration." for Integration tests, "Logic"
for "Tests.Logic." tests, and the component after "Tests." for anything else.

The share columns are report columns, not thresholds.

Each subdirectory of <runs-dir> is one unit's run. A unit's `<unit>_retry` dir, the gate's retry
pass, replaces the unit's first pass, as the gate's own verdict does. A unit dir with no TRX, a
malformed TRX, or a <runs-dir> with none is listed by path under "problems".
Exit 0: report printed. 1: at least one problem (report still printed). 2: usage error.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

SHARE_PERCENTS = (1, 5, 10)
RETRY_SUFFIX = "_retry"


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_duration(text):
    days = 0
    if "." in text.split(":")[0]:
        day_text, text = text.split(".", 1)
        days = int(day_text)
    hours, minutes, seconds = text.split(":")
    return days * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def segment_of(class_name):
    parts = class_name.split(".")
    if "Tests" not in parts:
        return parts[0]
    rest = parts[parts.index("Tests") + 1:]
    if not rest:
        return "Tests"
    if rest[0] == "Integration" and len(rest) > 1:
        return rest[1]
    return rest[0]


def parse_trx(path):
    root = ET.parse(path).getroot()
    class_by_id = {}
    for element in root.iter():
        if local(element.tag) == "UnitTest":
            for child in element.iter():
                if local(child.tag) == "TestMethod":
                    class_by_id[element.get("id")] = (child.get("className"), child.get("name") or "")
    tests = []
    for element in root.iter():
        if local(element.tag) != "UnitTestResult":
            continue
        name = element.get("testName")
        class_name, method = class_by_id.get(element.get("testId")) or (name.rsplit(".", 1)[0], "")
        base = method.split(":", 1)[0].split(" (", 1)[0]
        if base and class_name.endswith("." + base):
            class_name = class_name[:-len(base) - 1]
            name = f"{class_name}.{method}"
        message = None
        for child in element.iter():
            if local(child.tag) == "Message":
                message = (child.text or "").strip()
                break
        tests.append({"name": name, "class": class_name,
                      "duration": parse_duration(element.get("duration") or "00:00:00"),
                      "outcome": element.get("outcome"), "message": message})
    return tests


def select_trx(runs_dir):
    files = sorted(runs_dir.glob("*.trx"))
    missing = []
    units = {p.name: p for p in runs_dir.iterdir() if p.is_dir()}
    for name, unit_dir in sorted(units.items()):
        if f"{name}{RETRY_SUFFIX}" in units:
            continue
        unit_files = sorted(unit_dir.rglob("*.trx"))
        if not unit_files:
            missing.append(unit_dir)
        files.extend(unit_files)
    return files, missing


def build_report(runs_dir, top=20):
    runs_dir = Path(runs_dir)
    files, missing = select_trx(runs_dir)
    problems = [{"path": str(unit_dir), "error": "unit dir has no *.trx"} for unit_dir in missing]
    tests = []
    if not files and not missing:
        problems.append({"path": str(runs_dir), "error": "no *.trx files found"})
    for path in files:
        try:
            tests.extend(parse_trx(path))
        except (ET.ParseError, ValueError, TypeError, AttributeError, OSError) as error:
            problems.append({"path": str(path), "error": f"{type(error).__name__}: {error}"})
    ranked = sorted(tests, key=lambda t: t["duration"], reverse=True)
    total = sum(t["duration"] for t in tests)
    classes, segments = {}, {}
    for t in tests:
        classes[t["class"]] = classes.get(t["class"], 0.0) + t["duration"]
        segment = segment_of(t["class"])
        segments[segment] = segments.get(segment, 0.0) + t["duration"]
    shares = {}
    for percent in SHARE_PERCENTS:
        count = max(1, math.ceil(len(ranked) * percent / 100))
        shares[str(percent)] = sum(t["duration"] for t in ranked[:count]) / total if total else 0.0
    return {
        "runs_dir": str(runs_dir),
        "test_count": len(tests),
        "total_seconds": round(total, 6),
        "slowest": [{"name": t["name"], "class": t["class"], "duration": t["duration"]}
                    for t in ranked[:top]],
        "classes": {k: round(v, 6) for k, v in sorted(classes.items(), key=lambda kv: -kv[1])},
        "segments": {k: round(v, 6) for k, v in sorted(segments.items(), key=lambda kv: -kv[1])},
        "shares": shares,
        "failed": [{"name": t["name"], "message": t["message"]}
                   for t in tests if t["outcome"] == "Failed"],
        "problems": problems,
    }


def format_report(report):
    lines = [f"{report['test_count']} tests, {report['total_seconds']:.3f}s total "
             f"({report['runs_dir']})", "", "Slowest:"]
    lines += [f"  {t['duration']:9.3f}s  {t['name']}" for t in report["slowest"]]
    lines += ["", "Segments:"]
    lines += [f"  {v:9.3f}s  {k}" for k, v in report["segments"].items()]
    lines += ["", "Classes:"]
    lines += [f"  {v:9.3f}s  {k}" for k, v in report["classes"].items()]
    lines += ["", "Share of total duration in the slowest tests:"]
    lines += [f"  slowest {p}%: {v:.1%}" for p, v in report["shares"].items()]
    lines += ["", f"Failed ({len(report['failed'])}):"]
    lines += [f"  {f['name']}: {f['message']}" for f in report["failed"]]
    lines += ["", f"Problems ({len(report['problems'])}):"]
    lines += [f"  {p['path']}: {p['error']}" for p in report["problems"]]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-test profile of a gate's TRX results.")
    parser.add_argument("runs_dir")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not Path(args.runs_dir).is_dir():
        print(f"error: no such directory: {args.runs_dir}", file=sys.stderr)
        return 2
    report = build_report(args.runs_dir, args.top)
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
