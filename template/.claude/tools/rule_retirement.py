"""Scan codified rules for fired retirement triggers.

Every codified rule carries one retirement trigger (`/codify` Step 6): a
`retire_when:` frontmatter field on a memory file, or a
`<!-- retire-when: ... -->` comment beside a rule that lives in a command,
skill or rules file. This scanner evaluates those triggers against supplied
evidence and reports which rules are past their trigger.

Trigger kinds (one per trigger string):

    claude >= X                          fires when the client version is >= X
    tool:<name> absent                   fires when <name> is missing from --tools
    load_census budget <name> under X    fires when the census measurement is < X
    review-by: YYYY-MM-DD                fires when --today is on or after the date

Evidence that was not supplied makes a trigger **undecidable**, never live and
never fired — an unsupplied tool list must not silently retire every rule that
names a tool. Malformed frontmatter and unparseable trigger strings are
reported in their own bucket for the same reason.

    python3 .claude/tools/rule_retirement.py [--root .claude] [--json <path>]
        [--tools <csv|@file>] [--client-version X.Y.Z] [--census <path>]
        [--today YYYY-MM-DD]

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
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

PRINT_LIMIT = 20
SKIP_DIRS = {".git", "__pycache__", "node_modules", "scratch", "cache",
             "worktrees", "logs", "retired"}
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
FENCED = re.compile(r"^[ \t]*```.*?^[ \t]*```[ \t]*$", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def prose_only(text):
    """Blank fenced blocks and inline code: a documented example of the comment syntax is prose,
    not a rule's trigger."""
    return INLINE_CODE.sub("", FENCED.sub("", text))


CLIENT_RE = re.compile(r"^claude\s*>=\s*(\S+)$")
TOOL_RE = re.compile(r"^tool:\s*(\S+)\s+absent$")
CENSUS_RE = re.compile(r"^load_census\s+budget\s+(\S+)\s+under\s+(\d+(?:\.\d+)?)$")
REVIEW_RE = re.compile(r"^review-by:\s*(\d{4}-\d{2}-\d{2})$")


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
            if not triggers:
                continue
            ruled.add(rel)
            rows = []
            for source, trigger in triggers:
                if not trigger:
                    malformed.append({"path": rel, "source": source,
                                      "problem": "retire-when comment carries no trigger"})
                    continue
                bucket, kind, reason = evaluate(trigger, ctx)
                rows.append((bucket, {"path": rel, "source": source, "trigger": trigger,
                                      "kind": kind, "reason": reason}))
            has_fired = any(bucket == "fired" for bucket, _ in rows)
            for bucket, row in rows:
                if bucket == "fired":
                    fired.append(row)
                elif bucket == "live":
                    if not has_fired:
                        live.append(row)
                elif bucket == "undecidable":
                    undecidable.append(row)
                else:
                    malformed.append({"path": rel, "source": row["source"],
                                      "problem": row["reason"], "trigger": row["trigger"]})
    return {"scanned": scanned, "rules": len(ruled), "fired": fired, "live": live,
            "undecidable": undecidable, "malformed": malformed}


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan codified rules for fired retirement triggers.")
    parser.add_argument("--root", default=".claude")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--tools", help="comma list of live tool names, or @<file>")
    parser.add_argument("--client-version")
    parser.add_argument("--census", help="load-census JSON (default: generated logs/load_census.json under --root)")
    parser.add_argument("--today", help="YYYY-MM-DD (default: today)")
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
    }

    if input_errors:
        result = {"scanned": 0, "rules": 0, "fired": [], "live": [],
                  "undecidable": [], "malformed": input_errors}
    else:
        result = scan(args.root, ctx)
    result["generated"] = today
    result["root"] = args.root
    result["inputs"] = {
        "today": today,
        "client_version": client_text or None,
        "tools_supplied": None if ctx["tools"] is None else sorted(ctx["tools"]),
        "census_path": ctx["census_path"],
        "census_present": ctx["census"] not in (None, "unreadable"),
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
