#!/usr/bin/env python3
"""What one dispatch will read: job count, model and effort, reading per job, and the reading jobs repeat.

Usage:
    dispatch_size.py <file>      a `sidecar_fanout.py` jobs file, or `dispatch.js` / `dispatch_chains.js` /
                                 `review_fanout.js` Workflow args saved as JSON

Prints one line, for example
    dispatch size: 4 jobs (opus·high ×4); reading 172/170/231/168 KB; 150 KB read by all 4 (600 KB total)
    dispatch size: 4 jobs (opus·high ×4); reading 3/3/3/3 KB; names 157 KB more (read by all 4: 628 KB)
    dispatch size: 1 job (haiku·high); reading 401 KB; heaviest reading 109% of its window; over its usable window: wide (164250 tokens > 150000)

A job reads its prompt (`promptPath`, `promptFile`, or an inline `prompt`), each `contextFiles` and
`mustRead` path, and the dispatch's shared `contextPath` / `contextPrefixPath`. A path a job lists twice
counts once for that job. A path two or more jobs read is duplicated reading: it counts once per path at
its multiplier. An unreadable path counts 0 and is named in the line. Sizes are file bytes in KB (1024).

A seat told to read a file its prompt or a context file names reads it too. `names` counts each existing
file whose repo-relative or absolute path appears in backticks (an optional `:line` suffix is dropped) in a
job's prompt, `promptPath` / `promptFile`, `contextFiles` or the shared context, once per job and once per
file in the total, unless the job already lists that path. Two or more jobs naming one file are its
`read by` group. The scan is one level deep: a named file's own names are not followed, and a text file
over MAX_SCAN_BYTES is not scanned.

A job's reading also gets its window fit: tokens from UTF-8 decoded characters (replacement on invalid
bytes) at CHARS_PER_TOKEN (the rate the orchestration Pre-Dispatch
Checklist counts required reading at) against `model_registry.usable_window` of its `model` or `alias`
(`window_fit`). A job over its window is named in the line. A pin the registry does not resolve is not marked.

`fact_lines(spec)` adds, after that line, a verdict per job: one line for a job pinned above a cheaper claimant of its
tier (executor included), with the claimants' cost, `±` cell and the job's reading against each one's window and
long-context price point; a `not compared` line for a job it cannot compare, with the reason; and one closing
`pin is the cheapest claimant` line for the compared jobs with nothing cheaper. `format_line` names each job whose window
fit cannot be judged under `not sized`. These are facts read before a pin is chosen; no check denies on them.

`dispatch_size(jobs)` takes the jobs list or the whole jobs file / Workflow args object, is pure, and raises
only on a spec that holds no job list. `line(spec, root)` is its one-line form, None when the spec holds no
job or cannot be sized. `/delegate` runs it on the jobs file before launch; it informs a launch and never blocks one.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import model_registry
except Exception:  # no registry reader: a job's window fit is simply not judged
    model_registry = None
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
try:
    import _dispatch_checks
except Exception:  # no claimant facts: the fact lines are simply not printed
    _dispatch_checks = None

KB = 1024
CHARS_PER_TOKEN = 2.5
SHARED_KEYS = ("contextPath", "contextPrefixPath")
UNREADABLE_SHOWN = 3
MAX_SCAN_BYTES = 8 * KB * KB
BACKTICKED = re.compile(r"`([^`\n]+)`")
LINE_SUFFIX = re.compile(r"(?::\d+(?:[-–]\d+)?)+$")


def _jobs_of(spec) -> list:
    if isinstance(spec, list):
        return [job for job in spec if isinstance(job, dict)]
    if not isinstance(spec, dict):
        raise ValueError("a dispatch spec is a jobs list or an args object")
    jobs = []
    for key in ("jobs", "agents", "lenses"):
        value = spec.get(key)
        if isinstance(value, list):
            jobs += [job for job in value if isinstance(job, dict)]
    for chain in spec.get("chains") if isinstance(spec.get("chains"), list) else []:
        if isinstance(chain, dict) and isinstance(chain.get("jobs"), list):
            jobs += [job for job in chain["jobs"] if isinstance(job, dict)]
    return jobs


def _paths_of(job: dict, keys=("contextFiles", "mustRead")) -> list:
    paths = [job.get(key) for key in ("promptPath", "promptFile")]
    for key in keys:
        for entry in job.get(key) if isinstance(job.get(key), list) else []:
            paths.append(entry.get("path") if isinstance(entry, dict) else entry)
    return [p for p in paths if isinstance(p, str) and p.strip()]


def _size(path: str, root: Path):
    candidate = Path(path)
    try:
        data = (candidate if candidate.is_absolute() else root / candidate).read_bytes()
        return len(data), len(data.decode("utf-8", errors="replace"))
    except OSError:
        return None


def _named_in(text: str, root: Path) -> dict:
    """{normalized key: absolute path} of every existing file a backticked span of `text` names."""
    found = {}
    for span in BACKTICKED.findall(text):
        candidate = LINE_SUFFIX.sub("", span.strip())
        try:
            path = os.path.abspath(root / candidate)
            if candidate and os.path.isfile(path):
                found[os.path.normcase(path)] = path
        except (OSError, ValueError):
            continue
    return found


def _scan(path: str, root: Path, cache: dict) -> dict:
    key = os.path.normcase(os.path.abspath(root / path))
    if key not in cache:
        try:
            target = os.path.abspath(root / path)
            if os.stat(target).st_size > MAX_SCAN_BYTES:
                cache[key] = {}
            else:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    cache[key] = _named_in(fh.read(), root)
        except (OSError, ValueError):
            cache[key] = {}
    return cache[key]


def window_fit(model, total_characters: int, data=None) -> dict:
    """`fit_tokens` of the reading `total_characters` at CHARS_PER_TOKEN."""
    return fit_tokens(model, math.ceil(total_characters / CHARS_PER_TOKEN), data)


def fit_tokens(model, tokens: int, data=None) -> dict:
    """`{tokens, usable, over, error}` for a reading of `tokens` on `model`: `usable` the pin's
    `model_registry.usable_window`, `over` the tokens past it (0 when it fits or cannot be judged), `error` why the
    pin could not be sized, else None. No `model` is not an error."""
    fit = {"tokens": tokens, "usable": None, "over": 0, "error": None}
    if not model:
        return fit
    try:
        entry = model_registry.resolve(model, data)
    except Exception:
        fit["error"] = "does not resolve in the registry"
        return fit
    fit["usable"] = model_registry.usable_window(entry)
    if fit["usable"] is None:
        fit["error"] = "declares no context window in the registry, so its seat cannot be sized"
    else:
        fit["over"] = max(0, fit["tokens"] - fit["usable"])
    return fit


def dispatch_size(jobs, root=None, data=None) -> dict:
    """`{jobs, perJob: [{label, model, effort, kb, namedKb, tokens, usable, over, fitError}], duplicated: [{path, kb, readBy}], duplicatedKb,
    totalKb, unreadable, named: [{path, kb, namedBy}], namedKb}` for one dispatch. `root` resolves relative
    paths (default: the working directory); `data` is the registry for the window fit (default: the live one)."""
    root = Path(root) if root else Path.cwd()
    shared = [spec.get(key) for spec in [jobs] if isinstance(spec, dict) for key in SHARED_KEYS
              if isinstance(spec.get(key), str)]
    rows = _jobs_of(jobs)
    sizes, characters, names, readers, unreadable, per_job = {}, {}, {}, {}, [], []
    scanned, named_by, named_paths = {}, {}, {}
    for index, job in enumerate(rows):
        paths = {os.path.normcase(a): a for a in (os.path.abspath(root / p) for p in _paths_of(job) + shared)}
        total = len(job["prompt"].encode("utf-8")) if isinstance(job.get("prompt"), str) else 0
        total_characters = len(job["prompt"]) if isinstance(job.get("prompt"), str) else 0
        sources = _paths_of(job, keys=("contextFiles",)) + shared
        named = _named_in(job["prompt"], root) if isinstance(job.get("prompt"), str) else {}
        for source in sources:
            named.update(_scan(source, root, scanned))
        named = {key: path for key, path in named.items() if key not in paths}
        for key, path in named.items():
            named_by.setdefault(key, []).append(index)
            named_paths[key] = path
        for key, path in {**paths, **named}.items():
            if key not in sizes:
                measured = _size(path, root)
                sizes[key], characters[key] = measured if measured is not None else (None, None)
                names[key] = path
                if measured is None:
                    unreadable.append(path)
        named_kb = round(sum(sizes[key] or 0 for key in named) / KB)
        named_characters = sum(characters[key] or 0 for key in named)
        for key in paths:
            total += sizes[key] or 0
            total_characters += characters[key] or 0
            readers.setdefault(key, []).append(index)
        model = job.get("model") or job.get("alias")
        fit = window_fit(model, total_characters, data)
        per_job.append({"label": job.get("label") or job.get("key") or "job%d" % (index + 1),
                        "model": model, "effort": job.get("effort"), "kb": round(total / KB),
                        "namedKb": named_kb, "bytes": total, "characters": total_characters,
                        "namedCharacters": named_characters, "tokens": fit["tokens"], "usable": fit["usable"],
                        "over": fit["over"], "fitError": fit["error"],
                        "readingTokens": math.ceil((total_characters + named_characters) / CHARS_PER_TOKEN)})
    duplicated = [{"path": names[key], "kb": round((sizes[key] or 0) / KB), "readBy": len(who)}
                  for key, who in readers.items() if len(who) > 1 and sizes[key]]
    named = [{"path": named_paths[key], "kb": round((sizes[key] or 0) / KB), "namedBy": len(who)}
             for key, who in named_by.items()]
    return {"jobs": len(rows), "perJob": per_job, "duplicated": duplicated,
            "duplicatedKb": sum(d["kb"] for d in duplicated), "totalKb": sum(j["kb"] for j in per_job),
            "unreadable": unreadable, "named": named, "namedKb": sum(n["kb"] for n in named)}


def format_line(size: dict) -> str:
    """The one-line summary of a `dispatch_size` result."""
    jobs = size["jobs"]
    pins = {}
    for job in size["perJob"]:
        pin = "%s·%s" % (job["model"] or "?", job["effort"] or "?")
        pins[pin] = pins.get(pin, 0) + 1
    shown = ", ".join(pin + (" ×%d" % count if jobs > 1 else "") for pin, count in pins.items())
    line = "dispatch size: %d job%s (%s); reading %s KB" % (
        jobs, "" if jobs == 1 else "s", shown, "/".join(str(job["kb"]) for job in size["perJob"]))
    by_multiplier = {}
    for item in size["duplicated"]:
        by_multiplier[item["readBy"]] = by_multiplier.get(item["readBy"], 0) + item["kb"]
    for times, kb in sorted(by_multiplier.items(), reverse=True):
        line += "; %d KB read by %s%d (%d KB total)" % (kb, "all " if times == jobs else "", times, kb * times)
    if size["named"]:
        groups = {}
        for item in size["named"]:
            if item["namedBy"] > 1:
                groups[item["namedBy"]] = groups.get(item["namedBy"], 0) + item["kb"]
        shared_text = "; ".join("read by %s%d: %d KB" % ("all " if times == jobs else "", times, kb * times)
                                for times, kb in sorted(groups.items(), reverse=True))
        line += "; names %d KB more%s" % (size["namedKb"], " (%s)" % shared_text if shared_text else "")
    sized = [job for job in size["perJob"] if job["usable"]]
    if sized:
        heaviest = max(sized, key=lambda job: job["tokens"] / job["usable"])
        percent = round(100 * heaviest["tokens"] / heaviest["usable"])
        if percent:
            line += "; heaviest reading %d%% of its window%s" % (percent, " (%s)" % heaviest["label"] if jobs > 1 else "")
    unsized = {}
    for job in size["perJob"]:
        if job.get("fitError"):
            unsized.setdefault(job["fitError"], []).append(job["label"])
    for reason, labels in unsized.items():
        line += "; not sized: %s (%s)" % (", ".join(labels), reason)
    over = [job for job in size["perJob"] if job["over"]]
    if over:
        line += "; over its usable window: " + ", ".join(
            "%s (%d tokens > %d)" % (job["label"], job["tokens"], job["usable"]) for job in over[:UNREADABLE_SHOWN]) + (
            " +%d more" % (len(over) - UNREADABLE_SHOWN) if len(over) > UNREADABLE_SHOWN else "")
    if size["unreadable"]:
        names = [os.path.basename(p) for p in size["unreadable"]]
        more = len(names) - UNREADABLE_SHOWN
        line += "; unreadable, counted 0: " + ", ".join(names[:UNREADABLE_SHOWN]) + (" +%d more" % more if more > 0 else "")
    return line


def fact_lines(spec, root=None, data=None, ladder=None) -> list:
    """The claimant verdict of each job of a dispatch, in job order; [] for a spec holding no job. Per job, one of:
    a `cheaper claimants:` line for a job pinned above a cheaper claimant of its tier (the job's pin and cost, then each
    cheaper row on its transport with cost, prices, `±` cell, the job's measured reading against that row's usable
    window and long-context point); a `cheaper claimants: `label` (model) not compared: reason` line for a job it
    could not compare; and, once, `pin is the cheapest claimant: labels` naming the compared jobs with nothing cheaper.
    A job's tier is its shape row's (`guards/tool_profiles.json`), else its own `tier`. Facts only: no check denies on
    these lines, and `_dispatch_checks.cheaper_claimants` owns the filter and raises on what it cannot compare."""
    try:
        jobs = _jobs_of(spec)
    except ValueError:
        return []  # not a dispatch spec: it holds no job to state a verdict for
    if _dispatch_checks is None or model_registry is None:
        return ["cheaper claimants: `%s` (%s) not compared: the registry reader or the claimant facts did not import"
                % (job.get("label") or "job %d" % index, job.get("model")) for index, job in enumerate(jobs)]
    try:
        size = dispatch_size(spec, root, data)
        data = data if data is not None else model_registry.load()
        by_shape = _dispatch_checks.shape_tiers()
    except Exception as err:
        return ["cheaper claimants: not compared for this dispatch: %s" % err]
    out, compared = [], []
    for job, sized in zip(jobs, size["perJob"]):
        label, model = sized["label"], sized["model"]
        tier = by_shape.get(job.get("shape")) or job.get("tier")
        if not model:
            out.append("cheaper claimants: `%s` not compared: no model pin" % label)
            continue
        if not tier:
            out.append("cheaper claimants: `%s` (%s) not compared: neither its shape row nor its own tier names one"
                       % (label, model))
            continue
        try:
            transport = job.get("transport") or model_registry.resolve(model, data)["transport"]
            claimants = _dispatch_checks.cheaper_claimants(model, tier, transport, sized["readingTokens"], data, ladder)
        except Exception as err:
            reason = ("does not resolve in the registry" if isinstance(err, model_registry.UnknownModel) else str(err))
            out.append("cheaper claimants: `%s` (%s) not compared: %s" % (label, model, reason))
            continue
        if claimants:
            out.append("cheaper claimants: `%s` is pinned %s (cost %s) for `%s` on %s; cheaper there: %s" % (
                label, model, claimants[0]["pinCost"], tier, transport,
                "; ".join(_dispatch_checks.claimant_text(c, sized["readingTokens"]) for c in claimants)))
        else:
            compared.append(label)
    if compared:
        out.append("pin is the cheapest claimant: " + ", ".join(compared))
    return out


def line(spec, root=None):
    """The `format_line` of `spec`, or None when it holds no job or cannot be sized."""
    try:
        size = dispatch_size(spec, root)
        return format_line(size) if size["jobs"] else None
    except Exception:
        return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    try:
        spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        print(format_line(dispatch_size(spec)))
        for fact in fact_lines(spec):
            print(fact)
    except (OSError, ValueError) as exc:
        print("dispatch_size: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
