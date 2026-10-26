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

A job's reading also gets its fit: tokens from UTF-8 decoded characters (replacement on invalid
bytes) at CHARS_PER_TOKEN (the measured rate: a median of 2.50 characters per token over 43 large tool results,
range 2.10-3.04), against the input point the job compacts at (`model_registry.compaction_point` for the job's
transport, `window_fit`): 467000 for an opus job under a 500000 login. A job's reading has three buckets, each
decided from facts in the job and its brief:
  listed        the brief, `contextFiles`, `mustRead` and the shared context; counted whole.
  named inputs  a named file that is neither inside the `writeSet` of the brief's `task` block nor cited with a
                `:N` or `:N-M` suffix; counted whole. A file cited both bare outside the `writeSet` and with a
                suffix is an input.
  named spans   a named file inside the `writeSet`, or cited with a line suffix; the job reads ranges of it.
A named file whose own tokens exceed the job's point cannot be read whole: it counts 0 and is a `searchSources`
entry. `lowerTokens` is listed + inputs (`readingTokens` and `tokens` equal it); `upperTokens` adds the spans.
The line prints the heaviest job's lower share and the point it used, and the count of backticked path-shaped names
that are no file (a vault path spelled through an alias such as `V/...`), counted 0. A job is over its point when
`lowerTokens` is; when only `upperTokens` is, the fit line says `ceiling over`. A pin the registry does not resolve
is not marked. `fit_lines(size)` is the fact per job, with both numbers and the three largest counted files,
printed by `sidecar_fanout.py` before it launches.

`fact_lines(spec)` adds, after that line, a verdict per job: one line for a job pinned above a cheaper claimant of its
tier (executor included), with the claimants' cost, `±` cell and the job's reading against the point each one would
compact at for that job and its long-context price point; a `not compared` line for a job it cannot compare, with the reason; and one closing
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
try:
    import dispatch_result
except Exception:  # no task-block reader: every named file counts whole
    dispatch_result = None

KB = 1024
CHARS_PER_TOKEN = 2.5
SHARED_KEYS = ("contextPath", "contextPrefixPath")
UNREADABLE_SHOWN = 3
LARGEST_SHOWN = 3
MAX_SCAN_BYTES = 8 * KB * KB
BACKTICKED = re.compile(r"`([^`\n]+)`")
LINE_SUFFIX = re.compile(r"(?::\d+(?:[-–]\d+)?)+$")
PATH_SHAPED = re.compile(r"^[^\s<>{}*$|\\]*/[^\s<>{}*$|\\]*\.[A-Za-z0-9]{1,5}$")


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


def _named_in(text: str, root: Path, missing=None, cites=None) -> dict:
    """{normalized key: absolute path} of every existing file a backticked span of `text` names. A path-shaped span
    (a `/` and a file extension, no URL) that is no file is added to the `missing` set when one is passed. `cites`,
    when passed, gets `key: [cited bare, cited with a line suffix]`."""
    found = {}
    for span in BACKTICKED.findall(text):
        raw = span.strip()
        candidate = LINE_SUFFIX.sub("", raw)
        try:
            path = os.path.abspath(root / candidate)
            if candidate and os.path.isfile(path):
                key = os.path.normcase(path)
                found[key] = path
                if cites is not None:
                    cites.setdefault(key, [False, False])[candidate != raw] = True
            elif missing is not None and "://" not in candidate and PATH_SHAPED.match(candidate):
                missing.add(candidate)
        except (OSError, ValueError):
            continue
    return found


def _scan(path: str, root: Path, cache: dict) -> tuple:
    """(named files, unresolved path-shaped names, citations) of the text file at `path`, read once per `cache`."""
    key = os.path.normcase(os.path.abspath(root / path))
    if key not in cache:
        try:
            target = os.path.abspath(root / path)
            if os.stat(target).st_size > MAX_SCAN_BYTES:
                cache[key] = ({}, set(), {})
            else:
                missing, cites = set(), {}
                with open(target, encoding="utf-8", errors="replace") as fh:
                    cache[key] = (_named_in(fh.read(), root, missing, cites), missing, cites)
        except (OSError, ValueError):
            cache[key] = ({}, set(), {})
    return cache[key]


def _write_set(job: dict, root: Path) -> list:
    """The `writeSet` of the `task` block in the job's brief (inline prompt, `promptPath` or `promptFile`); [] when the
    brief holds none, the block is malformed, or `dispatch_result` did not import."""
    if dispatch_result is None:
        return []
    texts = [job["prompt"]] if isinstance(job.get("prompt"), str) else []
    for key in ("promptPath", "promptFile"):
        if isinstance(job.get(key), str):
            try:
                texts.append((root / job[key]).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    for text in texts:
        try:
            block = dispatch_result.task_block(text)
        except ValueError:
            continue
        if block:
            return block.get("writeSet", [])
    return []


def _in_write_set(path: str, root: Path, write_set: list) -> bool:
    try:
        relative = os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return False
    return not relative.startswith("..") and dispatch_result._inside(relative, write_set)


def _tokens_of(characters) -> int:
    return math.ceil((characters or 0) / CHARS_PER_TOKEN)


def window_fit(model, total_characters: int, data=None, job=None) -> dict:
    """`fit_tokens` of the reading `total_characters` at CHARS_PER_TOKEN."""
    return fit_tokens(model, math.ceil(total_characters / CHARS_PER_TOKEN), data, job)


def fit_tokens(model, tokens: int, data=None, job=None) -> dict:
    """`{tokens, usable, source, over, error}` for a reading of `tokens` on `model`: `usable` the
    `model_registry.compaction_point` of the pin for `job`, `source` which, `over` the tokens past it (0 when it fits
    or cannot be judged), `error` why the pin could not be sized, else None. No `model` is not an error."""
    fit = {"tokens": tokens, "usable": None, "source": None, "over": 0, "error": None}
    if not model:
        return fit
    try:
        entry = model_registry.resolve(model, data)
    except Exception:
        fit["error"] = "does not resolve in the registry"
        return fit
    try:
        fit["usable"], fit["source"] = model_registry.compaction_point(entry, job)
    except Exception as err:
        fit["error"] = "has no resolvable compaction window (%s)" % err
        return fit
    if fit["usable"] is None:
        fit["error"] = "declares no context window in the registry, so its seat cannot be sized"
    else:
        fit["over"] = max(0, fit["tokens"] - fit["usable"])
    return fit


def dispatch_size(jobs, root=None, data=None) -> dict:
    """`{jobs, perJob: [{label, model, effort, kb, namedKb, tokens, usable, pointSource, over, fitError, unresolved,
    lowerTokens, upperTokens, ceilingOver, searchSources, largest: [{path, tokens}]}],
    duplicated: [{path, kb, readBy}], duplicatedKb, totalKb, unreadable, named: [{path, kb, namedBy}], namedKb,
    unresolved: [name]}` for one dispatch; a job's `tokens` and `readingTokens` are its `lowerTokens` and `upperTokens`
    adds its named spans (see the module docstring). `root` resolves relative paths (default: the working directory); `data` is the registry for the window fit (default: the live one)."""
    root = Path(root) if root else Path.cwd()
    shared = [spec.get(key) for spec in [jobs] if isinstance(spec, dict) for key in SHARED_KEYS
              if isinstance(spec.get(key), str)]
    rows = _jobs_of(jobs)
    sizes, characters, names, readers, unreadable, per_job = {}, {}, {}, {}, [], []
    scanned, named_by, named_paths, unresolved = {}, {}, {}, set()
    for index, job in enumerate(rows):
        paths = {os.path.normcase(a): a for a in (os.path.abspath(root / p) for p in _paths_of(job) + shared)}
        total = len(job["prompt"].encode("utf-8")) if isinstance(job.get("prompt"), str) else 0
        total_characters = len(job["prompt"]) if isinstance(job.get("prompt"), str) else 0
        sources = _paths_of(job, keys=("contextFiles",)) + shared
        missing, cites = set(), {}
        named = _named_in(job["prompt"], root, missing, cites) if isinstance(job.get("prompt"), str) else {}
        for source in sources:
            found, absent, found_cites = _scan(source, root, scanned)
            named.update(found)
            missing |= absent
            for key, (bare, suffixed) in found_cites.items():
                mark = cites.setdefault(key, [False, False])
                mark[0], mark[1] = mark[0] or bare, mark[1] or suffixed
        unresolved |= missing
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
        point = fit_tokens(model, 0, data, job)["usable"]
        write_set = _write_set(job, root)
        inputs, spans, searched = [], [], []
        for key, path in named.items():
            if point and _tokens_of(characters[key]) > point:
                searched.append(path)
            elif _in_write_set(path, root, write_set) or not cites.get(key, (True, False))[0]:
                spans.append(key)
            else:
                inputs.append(key)
        lower_characters = total_characters + sum(characters[key] or 0 for key in inputs)
        upper_characters = lower_characters + sum(characters[key] or 0 for key in spans)
        counted = sorted(({"path": names[key], "tokens": _tokens_of(characters[key])}
                          for key in [*paths, *inputs, *spans] if characters[key]),
                         key=lambda item: -item["tokens"])
        fit = window_fit(model, lower_characters, data, job)
        upper = _tokens_of(upper_characters)
        per_job.append({"label": job.get("label") or job.get("key") or "job%d" % (index + 1),
                        "model": model, "effort": job.get("effort"), "kb": round(total / KB),
                        "namedKb": named_kb, "bytes": total, "characters": total_characters,
                        "namedCharacters": named_characters, "tokens": fit["tokens"], "usable": fit["usable"],
                        "pointSource": fit["source"], "over": fit["over"], "fitError": fit["error"],
                        "unresolved": len(missing), "lowerTokens": fit["tokens"], "upperTokens": upper,
                        "ceilingOver": bool(fit["usable"]) and not fit["over"] and upper > fit["usable"],
                        "searchSources": searched, "largest": counted[:LARGEST_SHOWN],
                        "readingTokens": fit["tokens"]})
    duplicated = [{"path": names[key], "kb": round((sizes[key] or 0) / KB), "readBy": len(who)}
                  for key, who in readers.items() if len(who) > 1 and sizes[key]]
    named = [{"path": named_paths[key], "kb": round((sizes[key] or 0) / KB), "namedBy": len(who)}
             for key, who in named_by.items()]
    return {"jobs": len(rows), "perJob": per_job, "duplicated": duplicated,
            "duplicatedKb": sum(d["kb"] for d in duplicated), "totalKb": sum(j["kb"] for j in per_job),
            "unreadable": unreadable, "named": named, "namedKb": sum(n["kb"] for n in named),
            "unresolved": sorted(unresolved)}


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
        line += "; heaviest reading %s of its compaction point, %d tokens%s" % (
            "%d%%" % percent if percent else "<1%", heaviest["usable"], " (%s)" % heaviest["label"] if jobs > 1 else "")
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
    if size.get("unresolved"):
        line += "; %d named path%s not resolved, counted 0: %s" % (
            len(size["unresolved"]), "" if len(size["unresolved"]) == 1 else "s",
            ", ".join(size["unresolved"][:UNREADABLE_SHOWN]) + (
                " +%d more" % (len(size["unresolved"]) - UNREADABLE_SHOWN) if len(size["unresolved"]) > UNREADABLE_SHOWN else ""))
    if size["unreadable"]:
        names = [os.path.basename(p) for p in size["unreadable"]]
        more = len(names) - UNREADABLE_SHOWN
        line += "; unreadable, counted 0: " + ", ".join(names[:UNREADABLE_SHOWN]) + (" +%d more" % more if more > 0 else "")
    return line


def _display_name(path: str) -> str:
    try:
        relative = os.path.relpath(path).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)
    return path if relative.startswith("..") else relative


def fit_lines(size: dict) -> list:
    """One fit fact per pinned job of a `dispatch_size` result: its lower reading (brief, listed files and named
    inputs) and its upper (every named file read whole) as a share of the compaction point it was measured against,
    the point's source, the three largest counted files, the search sources, and the count of path-shaped names it
    could not resolve. A job is over its point when the lower number is over, `ceiling over` when only the upper is.
    Facts only; `sidecar_fanout.py` prints these before it launches."""
    out = []
    for job in size["perJob"]:
        if not job["model"]:
            continue
        who = "`%s` (%s)" % (job["label"], job["model"])
        if job["fitError"]:
            out.append("fit: %s not sized: %s" % (who, job["fitError"]))
            continue
        percent = round(100 * job["lowerTokens"] / job["usable"]) if job["usable"] else 0
        text = "fit: %s reading %d tokens (%d if every named file is read whole), %s of its compaction point, %d tokens (%s)" % (
            who, job["lowerTokens"], job["upperTokens"], "%d%%" % percent if percent else "<1%", job["usable"],
            job["pointSource"])
        if job["over"]:
            text += "; over its point by %d tokens" % job["over"]
        elif job["ceilingOver"]:
            text += "; ceiling over"
        if job["largest"]:
            text += "; largest: " + ", ".join("%s %dk" % (_display_name(item["path"]), max(1, round(item["tokens"] / 1000)))
                                             for item in job["largest"])
        if job["searchSources"]:
            text += "; %d search source%s, counted 0: %s" % (
                len(job["searchSources"]), "" if len(job["searchSources"]) == 1 else "s",
                ", ".join(_display_name(p) for p in job["searchSources"][:UNREADABLE_SHOWN]))
        if job["unresolved"]:
            text += "; %d named path%s not resolved, counted 0" % (job["unresolved"], "" if job["unresolved"] == 1 else "s")
        out.append(text)
    return out


def fact_lines(spec, root=None, data=None, ladder=None, now=None, read=None) -> list:
    """The claimant verdict of each job of a dispatch, in job order; [] for a spec holding no job. Per job, one of:
    a `cheaper claimants:` line for a job pinned above a cheaper claimant of its tier (the job's pin and cost, then each
    cheaper row on its transport with cost, prices, `±` cell, the job's measured reading against that row's usable
    window and long-context point), followed by `_dispatch_checks.spare_plan_fact`'s fact when another plan serving
    the tier projects unused quota; a `cheaper claimants: `label` (model) not compared: reason` line for a job it
    could not compare; and, once, `pin is the cheapest claimant: labels` naming the compared jobs with nothing cheaper.
    A job's tier is `_dispatch_checks.job_tier`'s: its own `tier`, else its shape row's. Facts only: no check denies on
    these lines, and `_dispatch_checks.cheaper_claimants` owns the filter and raises on what it cannot compare.
    `now`/`read` plant the clock and the capacity reader; both have production defaults."""
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
    except Exception as err:
        return ["cheaper claimants: not compared for this dispatch: %s" % err]
    out, compared = [], []
    for job, sized in zip(jobs, size["perJob"]):
        label, model = sized["label"], sized["model"]
        try:
            tier = _dispatch_checks.job_tier(job.get("shape"), job.get("tier"))[0]
        except ValueError as err:
            out.append("cheaper claimants: `%s` (%s) not compared: %s" % (label, model, err))
            continue
        if not model:
            out.append("cheaper claimants: `%s` not compared: no model pin" % label)
            continue
        if not tier:
            out.append("cheaper claimants: `%s` (%s) not compared: neither its shape row nor its own tier names one"
                       % (label, model))
            continue
        try:
            transport = job.get("transport") or model_registry.resolve(model, data)["transport"]
            claimants = _dispatch_checks.cheaper_claimants(model, tier, transport, sized["lowerTokens"], data, ladder,
                                                           job=job)
        except Exception as err:
            reason = ("does not resolve in the registry" if isinstance(err, model_registry.UnknownModel) else str(err))
            out.append("cheaper claimants: `%s` (%s) not compared: %s" % (label, model, reason))
            continue
        if claimants:
            line = "cheaper claimants: `%s` is pinned %s (cost %s) for `%s` on %s; cheaper there: %s" % (
                label, model, claimants[0]["pinCost"], tier, transport,
                "; ".join(_dispatch_checks.claimant_text(c, sized["lowerTokens"], sized["upperTokens"]) for c in claimants))
            spare = _dispatch_checks.spare_plan_fact(tier, transport, data, now, read)
            out.append(line + ("; " + spare if spare else ""))
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
