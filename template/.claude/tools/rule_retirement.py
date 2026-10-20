"""Scan codified rules for fired retirement triggers.

A codified rule may carry a retirement trigger (`/codify` Step 6); a file
without one is intentionally unmarked and yields no row. A trigger is a
`retire_when:` frontmatter field on a memory file, or a
`<!-- retire-when: ... -->` comment beside a rule that lives in a command,
skill or rules file. This scanner evaluates those triggers against supplied
evidence and reports which rules are past their trigger.

Trigger kinds (one per trigger string):

    claude >= X                          fires when the client version is >= X
    tool:<name> absent                   fires when <name> is missing from --tools
    load_census budget <name> under X    fires when the census measurement is < X
    review-by: YYYY-MM-DD                fires when --today is on or after the date
    model-rule: <JSON object>            fires when the session-driver version changes

The model-rule object has exactly four nonempty string fields: `role` (the stable registry driver
alias, not a capability tier or vendor id), `model` (that row's `version` frozen at landing),
`weakness` and `removeWhen` (the registry driverNotes vocabulary). JSON must fit the marker container:
no literal `-->` inside a comment. A memory frontmatter value is a single-quoted, one-line scalar;
encode apostrophes inside its JSON as Unicode escapes. For example:

    <!-- retire-when: model-rule: {"role":"<driver alias>","model":"<frozen version>",
         "weakness":"<behavior compensated>","removeWhen":"<removal observation>"} -->

A different version flags each marked rule for an ablation, never automatic deletion. The registry
loads lazily through `model_registry.load` from --registry or <root>/reference/external_models.json.
Missing, invalid or unresolved registry evidence is undecidable. The row carries the four recorded
fields plus current_model and current_version. Driver aliases are used because registry capability
roles each admit several models rather than selecting one session default.

The evidence kind is its own comment, beside the rule it judges (`/codify` Step 6):

    <!-- basis: thin|reasoned; reverses-if: <the observation, in prose>;
         review-when: <source> [<field>:<value>]... since:<YYYY-MM-DD> >= <N> -->

It fires when the source holds N or more units dated on or after `since:` that match every field; a value
written `!<value>` matches every unit that the plain value does not. `since:`
is required: a count with no start date counts history the rule already saw and re-fires after its first
crossing. `N` is a positive integer. The prose of `reverses-if:` holds neither `; review-when:` nor `-->`.
A missing part, a basis other than `thin` or `reasoned`, an unknown source or an unknown field is malformed.

    source   unit counted                          fields (all exact unless noted)
    escape   escape-log rows                       path (fnmatch over `paths`), mandate, model, tier,
                                                   domain (member), writer
    ledger   reviews: distinct (stage, subject,    mandate, stage, model, seat, outcome
             ts) of review-ledger rows
    yield    archived Workflow seat rows           mandate (key in `mandates`), seat, model
                                                   (`author_model`), domain (member)
    tasks    task records, by `opened`             reach, reversibility, design, grounding, oracle
                                                   (member), size

An absent source counts zero and is reported `absent`; a source that exists and cannot be read in full, a
corrupt row included, is undecidable. A fired evidence marker is due at one: its author already chose the count.

Evidence that was not supplied makes a trigger **undecidable**, never live and
never fired — an unsupplied tool list must not silently retire every rule that
names a tool. Malformed frontmatter and unparseable trigger strings are
reported in their own bucket for the same reason.

    python3 .claude/tools/rule_retirement.py [--root .claude] [--json <path>]
        [--tools <csv|@file>] [--client-version X.Y.Z] [--census <path>]
        [--today YYYY-MM-DD] [--escape-log <path>] [--ledger <path>] [--yield-archive <path>]
        [--tasks-dir <path>] [--registry <path>]

Each source path defaults to its owner's resolver (`escape_log.log_path`, `review_ledger.ledger_path`,
`orchestration_metrics.ARCHIVE`, `task_record.record_dir`). A source loads only when a marker names it.

Exit: 2 when any malformed row exists, 1 when any rule fired, else 0.

Consumers: `/autolearn` (proposes the retirements it lists) and
`/eval_dashboard` (renders them). The census default is the generated runtime
report at `logs/load_census.json`, resolved under `--root`. The reader uses the
canonical `load_census.py --json` fields: `listing.total_bytes`,
`standing.total_bytes`, `prompt_emission_steady_bytes`,
`hook_chain.edit_subprocesses`, and `rules.cs_bundle_bytes`.
"""
import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PRINT_LIMIT = 20
# `plans`: a plan is an execution contract, not a rule surface, so a marker it quotes is data.
SKIP_DIRS = {".git", "__pycache__", "node_modules", "scratch", "cache", ".cache",
             "worktrees", "logs", "retired", "plans"}
DEFAULT_CENSUS = os.path.join("logs", "load_census.json")

CENSUS_FIELDS = {
    "listing_bytes": ("listing", "total_bytes"),
    "standing_bytes": ("standing", "total_bytes"),
    "prompt_emission_bytes": ("prompt_emission_steady_bytes",),
    "edit_hook_chain_subprocesses": ("hook_chain", "edit_subprocesses"),
    "cs_rule_bundle_bytes": ("rules", "cs_bundle_bytes"),
}

FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
RETIRE_FIELD = re.compile(r"^[ \t]*retire_when:[ \t]*(.*)$", re.M)
LIST_ITEM = re.compile(r"^[ \t]*-[ \t]*(.+?)[ \t]*$")
COMMENT = re.compile(r"<!--[ \t]*retire-when:[ \t]*(.*?)[ \t]*-->", re.S)
EVIDENCE = re.compile(r"<!--[ \t]*basis:[ \t]*(.*?)[ \t]*-->", re.S)
EVIDENCE_WHEN = re.compile(r"^(\S+)((?:\s+[A-Za-z_]+:\S+)*?)\s+since:(\S+)\s*>=\s*(\S+)$")
EVIDENCE_FIELDS = {
    "escape": ("path", "mandate", "model", "tier", "domain", "writer"),
    "ledger": ("mandate", "stage", "model", "seat", "outcome"),
    "yield": ("mandate", "seat", "model", "domain"),
    "tasks": ("reach", "reversibility", "design", "grounding", "oracle", "size"),
}
FENCED = re.compile(r"^[ \t]*```.*?^[ \t]*```[ \t]*$", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`|<!--.*?-->", re.S)


def prose_only(text):
    """Blank code examples without stripping backticks inside a live marker."""
    return INLINE_CODE.sub(lambda m: m.group(0) if m.group(0).startswith("<!--") else "",
                           FENCED.sub("", text))


CLIENT_RE = re.compile(r"^claude\s*>=\s*(\S+)$")
TOOL_RE = re.compile(r"^tool:\s*(\S+)\s+absent$")
CENSUS_RE = re.compile(r"^load_census\s+budget\s+(\S+)\s+under\s+(\d+(?:\.\d+)?)$")
REVIEW_RE = re.compile(r"^review-by:\s*(\d{4}-\d{2}-\d{2})$")
MODEL_RE = re.compile(r"^model-rule:\s*(.*)$", re.S)
MODEL_FIELDS = {"role", "model", "weakness", "removeWhen"}


def parse_model_rule(trigger):
    """(spec, problem) for the JSON model-compensation trigger."""
    match = MODEL_RE.match(trigger)
    if not match:
        return None, "not a model-rule trigger"
    try:
        spec = json.loads(match.group(1))
    except ValueError as exc:
        return None, "model-rule JSON unreadable: %s" % exc
    if not isinstance(spec, dict) or set(spec) != MODEL_FIELDS:
        return None, "model-rule needs exactly role, model, weakness and removeWhen"
    if any(not isinstance(value, str) or not value.strip() for value in spec.values()):
        return None, "model-rule fields must be nonempty strings"
    return spec, None


def model_registry_source(ctx):
    """Load a scan's registry once; an evidence-free evaluate caller gets no implicit live input."""
    path = ctx.get("registry_path")
    if not path:
        return None, "no registry path supplied"
    if "model_registry" not in ctx:
        try:
            import model_registry
            ctx["model_registry"] = (model_registry.load(path=path), None)
        except Exception as exc:
            ctx["model_registry"] = (None, "registry undecidable at %s: %s" % (path, exc))
    return ctx["model_registry"]


def evaluate_model_rule(trigger, ctx):
    """(bucket, reason, extra) for a model change; a flag requests review, not deletion."""
    spec, problem = parse_model_rule(trigger)
    if problem:
        return "malformed", problem, {}
    registry, problem = model_registry_source(ctx)
    if problem:
        return "undecidable", problem, spec
    import model_registry
    try:
        current = model_registry.resolve(spec["role"], data=registry)
    except model_registry.UnknownModel:
        return "undecidable", "driver alias %r absent from the registry" % spec["role"], spec
    if current["alias"] != spec["role"]:
        return "undecidable", "role %r is a vendor id, not a stable driver alias" % spec["role"], spec
    extra = dict(spec, current_model=current["id"], current_version=current["version"])
    changed = current["version"] != spec["model"]
    return ("fired" if changed else "live",
            "driver %s version %s %s recorded %s" % (spec["role"], current["version"],
                                                     "differs from" if changed else "matches", spec["model"]), extra)


VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def version_tuple(text):
    if not isinstance(text, str) or not VERSION_RE.fullmatch(text.strip()):
        return None
    return tuple(int(p) for p in text.strip().split("."))


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def read_tools(spec):
    """--tools accepts a comma list or @<file> holding one name per line."""
    if spec is None:
        return None
    if spec.startswith("@"):
        path = spec[1:]
        if not os.path.isfile(path):
            raise FileNotFoundError("--tools file does not exist: %s" % path)
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        names = re.split(r"[,\s]+", raw)
    else:
        names = spec.split(",")
    return {n.strip() for n in names if n.strip()}


def read_census(path, required=False):
    if not path or not os.path.isfile(path):
        if required:
            raise FileNotFoundError("--census file does not exist: %s" % path)
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return "unreadable"


def census_value(census, name):
    """Read one value from the canonical load_census report schema."""
    if not isinstance(census, dict) or name not in CENSUS_FIELDS:
        return None
    value = census
    for key in CENSUS_FIELDS[name]:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def evaluate(trigger, ctx):
    """Return (bucket, kind, reason) for one trigger string."""
    if MODEL_RE.match(trigger):
        bucket, reason, _ = evaluate_model_rule(trigger, ctx)
        return bucket, "model-change", reason
    match = REVIEW_RE.match(trigger)
    if match:
        due = match.group(1)
        if ctx["today"] >= due:
            return "fired", "review-by", "review date %s reached (today %s)" % (due, ctx["today"])
        return "live", "review-by", "review date %s not reached (today %s)" % (due, ctx["today"])

    match = TOOL_RE.match(trigger)
    if match:
        name = match.group(1)
        tools = ctx["tools"]
        if tools is None:
            return "undecidable", "tool-absent", "no tool list supplied (--tools)"
        if name in tools:
            return "live", "tool-absent", "tool %s present in the supplied list" % name
        return "fired", "tool-absent", "tool %s absent from the supplied list of %d" % (name, len(tools))

    match = CLIENT_RE.match(trigger)
    if match:
        threshold = version_tuple(match.group(1))
        current = ctx["client_version"]
        if current is None:
            return "undecidable", "client-version", "no client version supplied (--client-version)"
        if threshold is None:
            return "malformed", "client-version", "unparseable version %r" % match.group(1)
        if current >= threshold:
            return "fired", "client-version", "client %s >= %s" % (
                ctx["client_version_text"], match.group(1))
        return "live", "client-version", "client %s < %s" % (
            ctx["client_version_text"], match.group(1))

    match = CENSUS_RE.match(trigger)
    if match:
        name, threshold = match.group(1), float(match.group(2))
        census = ctx["census"]
        if census is None:
            return "undecidable", "load-census", "census JSON absent at %s" % ctx["census_path"]
        if census == "unreadable":
            return "malformed", "load-census", "census JSON unreadable at %s" % ctx["census_path"]
        value = census_value(census, name)
        if value is None:
            return "undecidable", "load-census", "budget %s not present in %s" % (name, ctx["census_path"])
        if value < threshold:
            return "fired", "load-census", "budget %s measured %s, under %s" % (
                name, value, match.group(2))
        return "live", "load-census", "budget %s measured %s, not under %s" % (
            name, value, match.group(2))

    return "malformed", "unknown", "trigger matches no known kind: %r" % trigger


def parse_evidence(inner):
    """(spec, None) for a well-formed evidence marker body, else (None, the problem). `inner` is the text
    between `<!-- basis:` and `-->`."""
    head, sep, rest = inner.partition(";")
    basis = head.strip()
    if basis not in ("thin", "reasoned"):
        return None, "evidence basis must be thin or reasoned, got %r" % basis
    rest = rest.strip()
    if not sep or not rest.startswith("reverses-if:"):
        return None, "evidence marker needs `reverses-if:` after the basis"
    parts = re.split(r";[ \t]*review-when:", rest[len("reverses-if:"):])
    if len(parts) == 1:
        return None, "evidence marker needs `; review-when:` after the reverses-if prose"
    if len(parts) > 2:
        return None, "the reverses-if prose holds `; review-when:`"
    if not parts[0].strip():
        return None, "reverses-if names no observation"
    found = EVIDENCE_WHEN.match(parts[1].strip())
    if not found:
        return None, "review-when needs `<source> [<field>:<value>]... since:<YYYY-MM-DD> >= <N>`"
    source, raw_filters, since, need = found.groups()
    if source not in EVIDENCE_FIELDS:
        return None, "unknown evidence source %r (one of %s)" % (source, ", ".join(sorted(EVIDENCE_FIELDS)))
    try:
        datetime.date.fromisoformat(since)
    except ValueError:
        return None, "since: %r is not a real YYYY-MM-DD date" % since
    if not need.isdigit() or int(need) < 1:
        return None, "N must be a positive integer, got %r" % need
    filters = []
    for token in raw_filters.split():
        name, _, value = token.partition(":")
        if name not in EVIDENCE_FIELDS[source]:
            return None, "unknown field %r for source %s" % (name, source)
        if any(name == seen for seen, _ in filters):
            return None, "field %r appears twice" % name
        if value == "!":
            return None, "field %r negates no value" % name
        filters.append((name, value))
    return {"basis": basis, "source": source, "filters": filters, "since": since, "need": int(need)}, None


def _absent():
    return {"status": "absent", "rows": []}


def _undecidable(reason):
    return {"status": "undecidable", "rows": [], "reason": reason}


def _read_file_source(path, read):
    if not os.path.exists(path):
        return _absent()
    try:
        with open(path, "rb") as handle:
            handle.read(1)
        rows, unread = read(path)
    except OSError as exc:
        return _undecidable("%s unreadable: %s" % (path, exc.__class__.__name__))
    if unread:
        return _undecidable("%s holds %d row(s) that could not be read" % (path, unread))
    return {"status": "present", "rows": rows}


def _load_jsonl(path):
    import review_ledger
    from pathlib import Path
    rows, corrupt, problem = review_ledger.read_rows(Path(path))
    if problem:
        raise OSError(problem)
    return rows, corrupt


def _load_escape(paths):
    import escape_log
    return _read_file_source(paths.get("escape_log") or escape_log.log_path(), _load_jsonl)


def _load_ledger(paths):
    import review_ledger
    from pathlib import Path
    path = paths.get("ledger") or review_ledger.ledger_path(Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
    return _read_file_source(path, _load_jsonl)


def _load_yield(paths):
    import orchestration_metrics
    path = paths.get("yield_archive") or orchestration_metrics.ARCHIVE
    if not os.path.exists(path) and not os.path.exists(path + ".1"):
        return _absent()
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                handle.read(1)
        except OSError as exc:
            return _undecidable("%s unreadable: %s" % (path, exc.__class__.__name__))
    return {"status": "present", "rows": orchestration_metrics.archive_rows(archive=path)[0]}


def _read_task_records(directory):
    """(records, count of `.json` files that are not a readable object)."""
    records, unread = [], 0
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                unread += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                unread += 1
    return records, unread


def _load_tasks(paths):
    import task_record
    directory = paths.get("tasks_dir") or task_record.record_dir()
    if not os.path.exists(directory):
        return _absent()
    try:
        records, unread = _read_task_records(directory)
    except OSError as exc:
        return _undecidable("%s unreadable: %s" % (directory, exc.__class__.__name__))
    if unread:
        return _undecidable("%s holds %d record(s) that could not be read" % (directory, unread))
    return {"status": "present", "rows": records}


def _day(value):
    return value[:10] if isinstance(value, str) else None


def _member(value, wanted):
    return wanted in value if isinstance(value, (list, tuple)) else value == wanted


def _yield_date(row):
    import orchestration_metrics
    return orchestration_metrics.row_date(row)


def _yield_seat(row):
    block = row.get("seat_overlap")
    return block.get("key") if isinstance(block, dict) else None


def _yield_id(row):
    return "%s:%s" % (row.get("run"), row.get("label"))


def _task_field(name):
    def read(row):
        fields = (row.get("triage") or {}).get("fields")
        return fields.get(name) if isinstance(fields, dict) else None
    return read


def _plain(key):
    return lambda row: row.get(key)


EVIDENCE_SOURCES = {
    "escape": {"load": _load_escape, "date": lambda r: _day(r.get("ts")), "id": _plain("id"),
               "match": {"path": lambda r, v: any(fnmatch.fnmatchcase(p, v) for p in r.get("paths") or []),
                         "mandate": lambda r, v: r.get("shouldHaveCaught") == v,
                         "model": lambda r, v: r.get("authorModel") == v,
                         "tier": lambda r, v: r.get("tier") == v,
                         "domain": lambda r, v: _member(r.get("domain"), v),
                         "writer": lambda r, v: r.get("writer") == v}},
    "ledger": {"load": _load_ledger, "date": lambda r: _day(r.get("ts")),
               "id": lambda r: "%s %s %s" % (r.get("stage"), r.get("subject"), r.get("ts")),
               "match": {name: (lambda r, v, n=name: r.get(n) == v)
                         for name in EVIDENCE_FIELDS["ledger"]}},
    "yield": {"load": _load_yield, "date": _yield_date, "id": _yield_id,
              "match": {"mandate": lambda r, v: v in (r.get("mandates") or {}),
                        "seat": lambda r, v: _yield_seat(r) == v,
                        "model": lambda r, v: r.get("author_model") == v,
                        "domain": lambda r, v: _member(r.get("domain"), v)}},
    "tasks": {"load": _load_tasks, "date": lambda r: _day(r.get("opened")), "id": _plain("task_id"),
              "match": {name: (lambda r, v, n=name: _member(_task_field(n)(r), v))
                        for name in EVIDENCE_FIELDS["tasks"]}},
}


def load_source(name, ctx):
    """The named source's rows and status, loaded once per run."""
    cache = ctx.setdefault("sources", {})
    if name not in cache:
        try:
            cache[name] = EVIDENCE_SOURCES[name]["load"](ctx.get("paths") or {})
        except Exception as exc:  # an owner that cannot import or read is undecidable, never a crash
            cache[name] = _undecidable("%s: %s" % (exc.__class__.__name__, exc))
    return cache[name]


def evaluate_evidence(inner, ctx):
    """(bucket, reason, extra) for one evidence marker body."""
    spec, problem = parse_evidence(inner)
    if problem:
        return "malformed", problem, {}
    name = spec["source"]
    source = load_source(name, ctx)
    label = " ".join([name] + ["%s:%s" % f for f in spec["filters"]] + ["since:" + spec["since"]])
    if source["status"] == "undecidable":
        return "undecidable", "%s: source undecidable (%s)" % (label, source["reason"]), {"evidence_source": "undecidable"}
    table = EVIDENCE_SOURCES[name]
    counted = {}
    for row in source["rows"]:
        date = table["date"](row)
        if date and date >= spec["since"] and all(
                (not table["match"][f](row, v[1:])) if v.startswith("!") else table["match"][f](row, v)
                for f, v in spec["filters"]):
            counted.setdefault(table["id"](row), row)
    ids = sorted(str(key) for key in counted)
    count = len(ids)
    shown = ", ".join(ids[:PRINT_LIMIT]) + (" (+%d more)" % (count - PRINT_LIMIT) if count > PRINT_LIMIT else "")
    bucket = "fired" if count >= spec["need"] else "live"
    reason = "%s: count %d %s %d%s" % (label, count, ">=" if bucket == "fired" else "<", spec["need"],
                                       "; ids %s" % shown if ids else "")
    return bucket, reason, {"count": count, "need": spec["need"], "ids": ids, "evidence_source": source["status"]}


def frontmatter_triggers(text, rel, malformed):
    """Triggers declared in a memory file's frontmatter, plus malformed rows."""
    match = FRONTMATTER.match(text)
    if not match:
        if text.startswith("---") and "retire_when" in text:
            malformed.append({"path": rel, "source": "frontmatter",
                              "problem": "frontmatter opened with `---` but never closed"})
        return []
    body = match.group(1)
    found = RETIRE_FIELD.search(body)
    if not found:
        return []
    inline = unquote(found.group(1))
    if inline:
        return [inline]
    items = []
    for line in body[found.end():].splitlines():
        if not line.strip():
            continue
        item = LIST_ITEM.match(line)
        if not item:
            break
        items.append(unquote(item.group(1)))
    if not items:
        malformed.append({"path": rel, "source": "frontmatter",
                          "problem": "retire_when declared with no trigger"})
    return items


def scan(root, ctx):
    ctx.setdefault("registry_path", os.path.join(root, "reference", "external_models.json"))
    fired, live, undecidable, malformed = [], [], [], []
    scanned = 0
    ruled = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace("\\", "/")
            scanned += 1
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError as exc:
                malformed.append({"path": rel, "source": "file", "problem": "unreadable: %s" % exc})
                continue
            triggers = [("frontmatter", t) for t in frontmatter_triggers(text, rel, malformed)]
            triggers += [("comment", unquote(m)) for m in COMMENT.findall(prose_only(text))]
            markers = EVIDENCE.findall(prose_only(text))
            if not triggers and not markers:
                continue
            ruled.add(rel)
            for inner in markers:
                bucket, reason, extra = evaluate_evidence(inner, ctx)
                row = dict(extra, path=rel, source="comment", trigger="basis: " + inner, kind="evidence",
                           reason=reason)
                if bucket == "malformed":
                    malformed.append({"path": rel, "source": "comment", "problem": reason,
                                      "trigger": row["trigger"]})
                else:
                    {"fired": fired, "live": live, "undecidable": undecidable}[bucket].append(row)
            rows = []
            for source, trigger in triggers:
                if not trigger:
                    malformed.append({"path": rel, "source": source,
                                      "problem": "retire-when comment carries no trigger"})
                    continue
                extra = {}
                if MODEL_RE.match(trigger):
                    bucket, reason, extra = evaluate_model_rule(trigger, ctx)
                    kind = "model-change"
                else:
                    bucket, kind, reason = evaluate(trigger, ctx)
                rows.append((bucket, dict(extra, path=rel, source=source, trigger=trigger,
                                          kind=kind, reason=reason)))
            has_fired = any(bucket == "fired" for bucket, _ in rows)
            for bucket, row in rows:
                if bucket == "fired":
                    fired.append(row)
                elif bucket == "live":
                    if not has_fired or row["kind"] == "model-change":
                        live.append(row)
                elif bucket == "undecidable":
                    undecidable.append(row)
                else:
                    malformed.append({"path": rel, "source": row["source"],
                                      "problem": row["reason"], "trigger": row["trigger"]})
    return {"scanned": scanned, "rules": len(ruled), "fired": fired, "live": live,
            "undecidable": undecidable, "malformed": malformed}


def _marked_files(claude_root):
    """`.md` paths under `claude_root` holding `<!-- basis:`: the tracked set when git answers, else
    a walk, so a tree outside a repository is still judged."""
    try:
        proc = subprocess.run(["git", "-C", claude_root, "grep", "-l", "-F", "<!-- basis:", "--", "*.md"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        proc = None
    if proc is not None and proc.returncode in (0, 1):
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    found = []
    for dirpath, dirnames, filenames in os.walk(claude_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".md"):
                path = os.path.join(dirpath, filename)
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        if "<!-- basis:" in handle.read():
                            found.append(os.path.relpath(path, claude_root))
                except OSError:
                    continue
    return found


def evidence_due(claude_root, paths=None):
    """Fired and malformed evidence markers in tracked `.claude/**/*.md`, outside SKIP_DIRS.

    The SessionStart due line's view (`tools/self_improvement_due.py`): a marker reaches it from any
    location. Each row is `{"path", "bucket": "fired"|"malformed", "reason"|"problem", ...}`. `paths`
    overrides source paths as `main` does; the default is each owner's resolver. A marker whose source
    cannot be read is not due."""
    ctx = {"paths": paths or {}, "sources": {}}
    rows = []
    for rel in sorted(_marked_files(claude_root)):
        rel = rel.replace("\\", "/")
        if any(part in SKIP_DIRS for part in rel.split("/")[:-1]):
            continue
        try:
            with open(os.path.join(claude_root, rel), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for inner in EVIDENCE.findall(prose_only(text)):
            bucket, reason, extra = evaluate_evidence(inner, ctx)
            if bucket == "fired":
                rows.append(dict(extra, path=rel, bucket="fired", reason=reason))
            elif bucket == "malformed":
                rows.append({"path": rel, "bucket": "malformed", "problem": reason})
    return rows


def _review_scope(claude_root):
    """CLAUDE.md, every `rules/**/*.md`, and top-level `auto-memory/*.md` except the MEMORY.md index:
    the instruction files a session loads before any search, plus hot memory."""
    paths = []
    top = os.path.join(claude_root, "CLAUDE.md")
    if os.path.isfile(top):
        paths.append(top)
    for dirpath, dirnames, filenames in os.walk(os.path.join(claude_root, "rules")):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        paths.extend(os.path.join(dirpath, name) for name in sorted(filenames) if name.endswith(".md"))
    memory = os.path.join(claude_root, "auto-memory")
    if os.path.isdir(memory):
        paths.extend(os.path.join(memory, name) for name in sorted(os.listdir(memory))
                     if name.endswith(".md") and name != "MEMORY.md"
                     and os.path.isfile(os.path.join(memory, name)))
    return paths


def review_due(claude_root, today):
    """Fired `review-by` rows inside the review scope; `today` is YYYY-MM-DD.

    The SessionStart due line's cheap view (`tools/self_improvement_due.py`): no tool list, client
    version or census, so only review dates are judged. An unreadable file is skipped here; the full
    `scan` reports it."""
    rows = []
    for path in _review_scope(claude_root):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        rel = os.path.relpath(path, claude_root).replace("\\", "/")
        triggers = frontmatter_triggers(text, rel, []) + [unquote(m) for m in COMMENT.findall(prose_only(text))]
        for trigger in triggers:
            if trigger and REVIEW_RE.match(trigger):
                bucket, _, reason = evaluate(trigger, {"today": today})
                if bucket == "fired":
                    rows.append({"path": rel, "trigger": trigger, "reason": reason})
    return rows


def report(result, ctx):
    print("== Rule retirement scan ==")
    print("root: %s   markdown scanned: %d   rules with a trigger: %d"
          % (ctx["root"], result["scanned"], result["rules"]))
    print("evidence: today=%s client=%s tools=%s census=%s"
          % (ctx["today"], ctx["client_version_text"] or "(none)",
             "%d names" % len(ctx["tools"]) if ctx["tools"] is not None else "(none)",
             ctx["census_path"] if ctx["census"] not in (None,) else "(absent)"))
    for label, key in (("FIRED — past their trigger", "fired"),
                       ("MALFORMED — reported, not skipped", "malformed"),
                       ("UNDECIDABLE — evidence not supplied", "undecidable")):
        rows = result[key]
        print("\n%s: %d" % (label, len(rows)))
        for row in rows[:PRINT_LIMIT]:
            detail = row.get("reason") or row.get("problem", "")
            print("  %-60s %s" % (row["path"], detail))
        if len(rows) > PRINT_LIMIT:
            print("  ... %d more" % (len(rows) - PRINT_LIMIT))
    print("\nLIVE — trigger not yet met: %d" % len(result["live"]))
    for name, source in sorted(result.get("evidence_sources", {}).items()):
        detail = ("present %d rows" % source["rows"] if source["status"] == "present"
                  else source["status"] + (" (%s)" % source["reason"] if source.get("reason") else ""))
        print("evidence source %s: %s" % (name, detail))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan codified rules for fired retirement triggers.")
    parser.add_argument("--root", default=".claude")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--tools", help="comma list of live tool names, or @<file>")
    parser.add_argument("--client-version")
    parser.add_argument("--census", help="load-census JSON (default: generated logs/load_census.json under --root)")
    parser.add_argument("--today", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--escape-log", help="escape log for `escape` markers (default: escape_log.log_path())")
    parser.add_argument("--ledger", help="review ledger for `ledger` markers (default: review_ledger.ledger_path())")
    parser.add_argument("--yield-archive",
                        help="orchestration archive for `yield` markers (default: orchestration_metrics.ARCHIVE)")
    parser.add_argument("--tasks-dir", help="task records for `tasks` markers (default: task_record.record_dir())")
    parser.add_argument("--registry", help="model-rule registry (default: reference/external_models.json under --root)")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.root):
        print("UNKNOWN: root %s is not a directory" % args.root)
        return 2

    input_errors = []
    today = args.today or datetime.date.today().isoformat()
    if args.today:
        try:
            datetime.date.fromisoformat(today)
        except ValueError:
            input_errors.append({"path": args.today, "source": "input",
                                 "problem": "--today is not a real YYYY-MM-DD date: %s" % args.today})

    client_text = args.client_version or os.environ.get("CLAUDE_CODE_VERSION") or ""
    client_version = version_tuple(client_text) if client_text else None
    if client_text and client_version is None:
        input_errors.append({"path": client_text, "source": "input",
                             "problem": "--client-version is not a numeric dotted version: %s" % client_text})

    census_path = args.census or os.path.join(args.root, DEFAULT_CENSUS)
    try:
        tools = read_tools(args.tools)
    except OSError as exc:
        tools = None
        input_errors.append({"path": args.tools[1:], "source": "input", "problem": str(exc)})
    try:
        census = read_census(census_path, required=bool(args.census))
    except OSError as exc:
        census = None
        input_errors.append({"path": census_path, "source": "input", "problem": str(exc)})
    ctx = {
        "root": args.root,
        "today": today,
        "tools": tools,
        "client_version_text": client_text,
        "client_version": client_version,
        "census_path": census_path.replace("\\", "/"),
        "census": census,
        "registry_path": args.registry or os.path.join(args.root, "reference", "external_models.json"),
        "paths": {"escape_log": args.escape_log, "ledger": args.ledger,
                  "yield_archive": args.yield_archive, "tasks_dir": args.tasks_dir},
        "sources": {},
    }

    if input_errors:
        result = {"scanned": 0, "rules": 0, "fired": [], "live": [],
                  "undecidable": [], "malformed": input_errors}
    else:
        result = scan(args.root, ctx)
    result["evidence_sources"] = {
        name: {"status": src["status"], "rows": len(src["rows"]),
               **({"reason": src["reason"]} if "reason" in src else {})}
        for name, src in ctx["sources"].items()}
    result["generated"] = today
    result["root"] = args.root
    result["inputs"] = {
        "today": today,
        "client_version": client_text or None,
        "tools_supplied": None if ctx["tools"] is None else sorted(ctx["tools"]),
        "census_path": ctx["census_path"],
        "census_present": ctx["census"] not in (None, "unreadable"),
        "registry_path": ctx["registry_path"].replace("\\", "/"),
    }
    report(result, ctx)
    if args.json_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_path)), exist_ok=True)
        with open(args.json_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
        print("\njson: %s" % args.json_path)
    if result["malformed"]:
        return 2
    return 1 if result["fired"] else 0


if __name__ == "__main__":
    sys.exit(main())
