"""Add or complete a worklog item in one call.

  python3 .claude/tools/worklog.py add "<title>" --class <c> --scope <n> --domain "<Domain>"
      --context "<one line>" [--where <path>] [--source <ref>]
      [--when "after <condition>"|future] [--plan-doc "<Doc Title>"] [--date YYYY-MM-DD]
      [--distinct] [--dry-run]
  python3 .claude/tools/worklog.py add --from FILE.jsonl [--dry-run]
  python3 .claude/tools/worklog.py complete "<title or search>" [--ref REF] [--date YYYY-MM-DD] [--dry-run]
  python3 .claude/tools/worklog.py complete --from FILE.jsonl [--dry-run]

`add` runs the write steps of commands/reference/worklog_add.md §ADD: de-dup, place the BLOCK at
the top of its `## Active`/`## Ledger` domain (a missing domain heading is created at the end of
that supersection), place an FSLINE inside `## Future Scope` for a `future` item, add a scope-4
plan doc to `## Linked Docs` on the Active route, bump `last_updated:`, and patch the mirror.
Formats are the ones commands/reference/worklog_formats.md defines. Class and domain are validated
against the canonical tables in skills/worklog_reference/SKILL.md, read at run time (`--skill`
names a different copy); a missing, unreadable or table-less owner is a refusal that names it, never
an empty list. A domain is accepted in the table's long or short form, and the long form is what a
section heading carries. Scope is 1-4; a scope-4 item needs a plan doc, which Future Scope does not
carry. A duplicate (case-insensitive title anywhere, or a same-domain near-duplicate by token
overlap) writes nothing and exits 2; `--distinct` waives the near-duplicate check only.

The vault TODO directory is `<VAULT_ROOT>/DevProjects/<PROJECT_NAME>/Claude/TODO` from
.claude/baseline.lock.json's substitutions, the same owner hooks/overnight_ask_guard.py reads; a
lock without them is a refusal that names it. `--worklog`/`--archive`/`--mirror`/`--history`
override every path.

`complete` runs steps 1-7 and 9 of commands/reference/worklog_complete.md: the BLOCK leaves
Worklog.md, an XLINE joins Worklog-Archive.md under its domain, both last_updated lines bump,
the mirror line leaves .claude/worklog-titles.md, and a matching tackle event gets its completion.

Matching: an exact title wins; otherwise a case-insensitive substring over open `- [ ]` items in
`## Active` and `## Ledger`. Zero or several matches write nothing, list the candidates with line
numbers and exit 2.

Exit: 0 added/completed, 2 duplicate, invalid input, no match or ambiguous, 1 a write failed.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
from _hook_state import replace_path  # noqa: E402
LOCK = Path(".claude/baseline.lock.json")
SKILL = Path(".claude/skills/worklog_reference/SKILL.md")
WORKLOG_NAME = "Worklog.md"
ARCHIVE_NAME = "Worklog-Archive.md"
MIRROR = Path(".claude/worklog-titles.md")
HISTORY = Path(".claude/worklog-tackle-history.jsonl")

# worklog_formats.md: BLOCK header, XLINE, archive head.
ITEM = re.compile(r"^- \[ \] \*\*(.*?)\*\* — `([^`]+)` · scope `([^`]+)` · added (\d{4}-\d{2}-\d{2})\s*$")
XLINE = "- [x] `{cls}` · scope `{scope}` · {title} (completed {date}{ref})"
ARCHIVE_HEAD = """---
title: Worklog Archive
status: archive
last_updated: {date}
---

# Worklog Archive

> Completed worklog items, moved out of `Worklog.md` on completion to keep the active doc lean.
> Opaque to passive agent context: never mirrored, never loaded at SessionStart, never scanned by sweep/triage/plan.
> Read only via `/worklog history` or on explicit request. Newest completions append at the bottom of each domain section.
"""

SUPERSECTION = re.compile(r"^## (Active|Ledger)\b")
HEADING = re.compile(r"^##{2,3} ")
DOMAIN_HEAD = re.compile(r"^### (.+?)\s*$")
ARCHIVE_DOMAIN = re.compile(r"^## (.+?)\s*$")
LAST_UPDATED = re.compile(r"^last_updated: \d{4}-\d{2}-\d{2}$")
LAST_SYNCED = re.compile(r"^Last synced: \d{4}-\d{2}-\d{2}$")
MIRROR_SUFFIX = re.compile(r"(?:\s+\[(?:after: [^\]]*|quick-win)\])+$")

# A literal token here would be expanded by a consumer's install (baseline push refuses it);
# assembled from parts, as hooks/overnight_ask_guard.py does.
TOKEN = "{" + "{%s}" + "}"
CLASS_SECTION = "## Classification"
DOMAIN_SECTION = "## Canonical domain list"
DOMAIN_TABLE_HEADER = "Long form (Active section header)"

FS_HEAD = re.compile(r"^> ### (.+?)\s*$")
FS_ITEM = re.compile(r"^> - `([^`]+)` · scope `([^`]+)` · (.*?) \(added (\d{4}-\d{2}-\d{2});")
FS_COUNT = re.compile(r"^(> \[!example\]- Future Scope \()\d+ items?(\))")
NEAR_DUP = 0.7


class Item:
    def __init__(self, index, heading, title, cls, scope, tier, domain):
        self.index = index
        self.heading = heading
        self.title = title
        self.cls = cls
        self.scope = scope
        self.tier = tier
        self.domain = domain


def bare(line):
    return line[:-1] if line.endswith("\r") else line


def eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def read_text(path):
    return Path(path).read_bytes().decode("utf-8")


def section_lines(text, heading):
    """The lines under the section heading that starts with `heading`, up to the next `## ` line."""
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        if bare(raw).startswith(heading):
            for j in range(i + 1, len(lines)):
                if bare(lines[j]).startswith("## "):
                    return lines[i + 1:j]
            return lines[i + 1:]
    raise ValueError("no %r section" % heading)


def cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table_rows(text, heading):
    return [cells(line) for line in section_lines(text, heading) if line.lstrip().startswith("|")]


def read_canonical(path):
    """(classes, domains) from `path`'s canonical tables; a domain is its (long, short) row.

    Raises ValueError naming the file when it is missing, unreadable, or carries no rows, so a
    caller never validates against an empty list."""
    try:
        text = read_text(path)
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read %s (%s)" % (path, exc))
    try:
        classes = tuple(row[0].strip("`") for row in table_rows(text, CLASS_SECTION)
                        if len(row) >= 2 and row[0].startswith("`"))
        domains = []
        for row in table_rows(text, DOMAIN_SECTION):
            if not row[0] or row[0] == DOMAIN_TABLE_HEADER or set(row[0]) <= set("-: "):
                continue
            if len(row) < 2 or not row[1]:
                raise ValueError("domain row %r carries no short form" % row[0])
            domains.append((row[0], row[1].strip("`")))
    except ValueError as exc:
        raise ValueError("%s: %s" % (path, exc))
    if not classes:
        raise ValueError("%s: no class rows under %r" % (path, CLASS_SECTION))
    if not domains:
        raise ValueError("%s: no domain rows under %r" % (path, DOMAIN_SECTION))
    return classes, tuple(domains)


def resolve_domain(domains, value):
    """The long form for `value`'s long or short spelling, else None."""
    key = " ".join((value or "").split()).casefold()
    for long, short in domains:
        if key in (" ".join(long.split()).casefold(), " ".join(short.split()).casefold()):
            return long
    return None


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".worklog-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        replace_path(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_items(lines):
    items, tier, domain, heading = [], None, None, None
    for i, raw in enumerate(lines):
        line = bare(raw)
        m = SUPERSECTION.match(line)
        if m:
            tier, domain, heading = m.group(1), None, None
            continue
        if line.startswith("## "):
            tier, domain, heading = None, None, None
            continue
        m = DOMAIN_HEAD.match(line)
        if m:
            domain, heading = m.group(1), i
            continue
        m = ITEM.match(line)
        if m and tier:
            items.append(Item(i, heading, m.group(1), m.group(2), m.group(3), tier, domain))
    return items


def pick(items, query):
    """(item, candidates); exactly one side is non-None."""
    exact = [i for i in items if i.title == query]
    if exact:
        return (exact[0], []) if len(exact) == 1 else (None, exact)
    lowered = query.lower()
    exact_ci = [i for i in items if i.title.lower() == lowered]
    if exact_ci:
        return (exact_ci[0], []) if len(exact_ci) == 1 else (None, exact_ci)
    sub = [i for i in items if lowered in i.title.lower()]
    return (sub[0], []) if len(sub) == 1 else (None, sub)


def block_end(lines, start):
    j = start + 1
    while j < len(lines):
        line = bare(lines[j])
        if line.strip() and line[:1] in (" ", "\t"):
            j += 1
            continue
        break
    return j


def domain_end(lines, heading):
    j = heading + 1
    while j < len(lines) and not HEADING.match(bare(lines[j])):
        j += 1
    return j


def bump(lines, pattern, value):
    for i, raw in enumerate(lines):
        if pattern.match(bare(raw)):
            lines[i] = value + ("\r" if raw.endswith("\r") else "")
            return


def bump_count(lines, tier, delta):
    """worklog_mirror.md: the supersection count changes on every add and remove."""
    pattern = re.compile(r"^(## %s \()(\d+)(\))$" % tier)
    for i, raw in enumerate(lines):
        m = pattern.match(bare(raw))
        if m:
            lines[i] = "%s%d%s%s" % (m.group(1), int(m.group(2)) + delta, m.group(3),
                                     "\r" if raw.endswith("\r") else "")
            return


def archive_plan(text, domain, xline, fallback_eol):
    """(new text, section description)."""
    if text is None:
        head = ARCHIVE_HEAD.format(date=date.today().isoformat())
        return (head + "\n## %s\n%s\n" % (domain, xline)).replace("\n", fallback_eol), (
            "new ## %s (file will be created)" % domain)
    lines = text.split("\n")
    cr = "\r" if "\r\n" in text else ""
    marks = [i for i, raw in enumerate(lines) if bare(raw) == "## " + domain]
    if marks:
        heading = marks[-1]
        end = heading + 1
        while end < len(lines) and not ARCHIVE_DOMAIN.match(bare(lines[end])):
            end += 1
        at = end
        while at > heading + 1 and not bare(lines[at - 1]).strip():
            at -= 1
        lines.insert(at, xline + cr)
        bump(lines, LAST_UPDATED, "last_updated: " + date.today().isoformat())
        return "\n".join(lines), "## %s (line %d)" % (domain, heading + 1)
    end = len(lines)
    if lines and lines[-1] == "":
        end = len(lines) - 1
    new = ["## %s" % domain + cr, xline + cr]
    if end and bare(lines[end - 1]).strip():
        new.insert(0, cr)
    lines[end:end] = new
    bump(lines, LAST_UPDATED, "last_updated: " + date.today().isoformat())
    return "\n".join(lines), "new ## %s (appended)" % domain


def mirror_title(line):
    parts = bare(line).split(" · ", 2)
    if len(parts) < 3 or not parts[0].startswith("- "):
        return None
    return MIRROR_SUFFIX.sub("", parts[2])


def mirror_plan(text, item):
    """(new text, removed line or None)."""
    if text is None:
        return None, None
    lines = text.split("\n")
    starts = [i for i, raw in enumerate(lines) if SUPERSECTION.match(bare(raw))]
    start = None
    for i in starts:
        if SUPERSECTION.match(bare(lines[i])).group(1) == item.tier:
            start = i
    if start is None:
        return text, None
    end = start + 1
    while end < len(lines) and not bare(lines[end]).startswith("## "):
        end += 1
    target = None
    for i in range(start + 1, end):
        if mirror_title(lines[i]) == item.title:
            target = i
            break
    if target is None:
        return text, None
    removed = bare(lines[target])
    heading = None
    for i in range(target - 1, start, -1):
        if bare(lines[i]).startswith("### "):
            heading = i
            break
    del lines[target]
    if heading is not None:
        section_end = heading + 1
        while section_end < len(lines) and not bare(lines[section_end]).startswith(("### ", "## ")):
            section_end += 1
        if not any(bare(lines[i]).startswith("- ") for i in range(heading + 1, section_end)):
            del lines[heading]
            if (0 < heading < len(lines)
                    and bare(lines[heading - 1]).strip() == ""
                    and bare(lines[heading]).strip() == ""):
                del lines[heading]
    bump_count(lines, item.tier, -1)
    bump(lines, LAST_SYNCED, "Last synced: " + date.today().isoformat())
    return "\n".join(lines), removed


def history_append(text, title, day):
    if text is None:
        return None
    state = None
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if row.get("title") != title:
            continue
        if row.get("event") == "tackle":
            state = "tackle"
        elif row.get("event") == "completion":
            state = "completion"
    if state != "tackle":
        return None
    row = json.dumps({"event": "completion", "date": day, "title": title}, ensure_ascii=False)
    prefix = text if not text or text.endswith("\n") else text + "\n"
    return prefix + row + "\n"


def find_future(lines):
    items, in_fs, domain = [], False, None
    for i, raw in enumerate(lines):
        line = bare(raw)
        if line.startswith("## "):
            in_fs, domain = line.strip() == "## Future Scope", None
            continue
        if not in_fs:
            continue
        m = FS_HEAD.match(line)
        if m:
            domain = m.group(1)
            continue
        m = FS_ITEM.match(line)
        if m:
            items.append(Item(i, None, m.group(3), m.group(1), m.group(2), "Future Scope", domain))
    return items


def words(title):
    return re.findall(r"[a-z0-9]+", title.lower())


def near_dup(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    sa, sb = " ".join(wa), " ".join(wb)
    if len(wa) >= 4 and len(wb) >= 4 and (sa in sb or sb in sa):
        return True
    return len(set(wa) & set(wb)) / len(set(wa) | set(wb)) >= NEAR_DUP


def duplicate_of(items, title, domain, distinct):
    """(existing item, 'exact'|'near') or (None, None). --distinct waives only 'near'."""
    key = " ".join(title.split()).casefold()
    for item in items:
        if " ".join(item.title.split()).casefold() == key:
            return item, "exact"
    if not distinct:
        for item in items:
            if item.domain == domain and near_dup(item.title, title):
                return item, "near"
    return None, None


def build_block(f, when_line):
    out = ["- [ ] **%s** — `%s` · scope `%s` · added %s"
           % (f["title"], f["cls"], f["scope"], f["added"]),
           "  - Context: %s" % f["context"]]
    if f["where"]:
        out.append("  - Where: %s" % f["where"])
    if f["source"]:
        out.append("  - Source: %s" % f["source"])
    if when_line:
        out.append("  - When: %s" % when_line)
    if f["plan_doc"]:
        out.append("  - Plan doc: [[%s]]" % f["plan_doc"])
    return out


def build_fsline(f):
    return "> - `%s` · scope `%s` · %s (added %s; %s)" % (
        f["cls"], f["scope"], f["title"], f["added"], f["context"])


def find_section(lines, title):
    for i, raw in enumerate(lines):
        if bare(raw).strip() == title:
            end = i + 1
            while end < len(lines) and not bare(lines[end]).startswith("## "):
                end += 1
            return i, end
    return None


def find_domain(lines, start, end, domain):
    target = "### " + domain
    for i in range(start + 1, end):
        if bare(lines[i]).strip() == target:
            return i
    return None


def splice(lines, at, block, cr, blank_before=False):
    block = list(block)
    if blank_before and at > 0 and bare(lines[at - 1]).strip() != "":
        block.insert(0, "")
    if at < len(lines):
        if bare(lines[at]).strip() != "":
            block.append("")
    elif block and block[-1].strip() != "":
        block.append("")
    lines[at:at] = [b + cr for b in block]


def linked_docs_line(lines, f, cr):
    sec = find_section(lines, "## Linked Docs")
    if sec is None:
        return None
    start, end = sec
    wiki = "[[%s]]" % f["plan_doc"]
    if any(wiki in bare(lines[i]) for i in range(start + 1, end)):
        return None
    line = '- %s — %s (Active: "%s", added %s)' % (wiki, f["context"], f["title"], f["added"])
    at = end
    while at > start + 1 and bare(lines[at - 1]).strip() == "":
        at -= 1
    lines[at:at] = [line + cr]
    return line


def active_plan(text, f):
    cr = "\r" if eol_of(text) == "\r\n" else ""
    lines = text.split("\n")
    tier = "Ledger" if f["when"] else "Active"
    sec = find_section(lines, "## " + tier)
    if sec is None:
        raise ValueError("no ## %s section" % tier)
    start, end = sec
    block = build_block(f, f["when"])
    h = find_domain(lines, start, end, f["domain"])
    if h is not None:
        splice(lines, h + 1, block, cr)
        section = "## %s > ### %s (line %d)" % (tier, f["domain"], h + 1)
    else:
        at = end
        pre = 0 if at == 0 or bare(lines[at - 1]).strip() == "" else 1
        splice(lines, at, ["### " + f["domain"]] + block, cr, blank_before=bool(pre))
        section = "## %s > ### %s (new heading at line %d)" % (tier, f["domain"], at + pre + 1)
    linked = linked_docs_line(lines, f, cr) if f["plan_doc"] and tier == "Active" else None
    bump(lines, LAST_UPDATED, "last_updated: " + f["added"])
    return {"text": "\n".join(lines), "section": section, "block": block,
            "mirror_line": None, "linked": linked}


def future_plan(text, f):
    cr = "\r" if eol_of(text) == "\r\n" else ""
    lines = text.split("\n")
    fsline = build_fsline(f)
    created = False
    sec = find_section(lines, "## Future Scope")
    if sec is None:
        ld = find_section(lines, "## Linked Docs")
        if ld is None:
            raise ValueError("no ## Future Scope or ## Linked Docs section")
        at = ld[0]
        new = ["## Future Scope", "",
               "> [!example]- Future Scope (1 item)",
               "> <!-- Distant-horizon items (When: future). One-liner format only — excluded from `.claude/worklog-titles.md` mirror. -->",
               "> ### " + f["domain"], fsline, ""]
        lines[at:at] = [x + cr for x in new]
        section = "## Future Scope (new section)"
        created = True
    else:
        start, end = sec
        h = None
        for i in range(start + 1, end):
            if bare(lines[i]).strip() == "> ### " + f["domain"]:
                h = i
                break
        if h is not None:
            lines.insert(h + 1, fsline + cr)
            section = "## Future Scope > ### %s (line %d)" % (f["domain"], h + 1)
        else:
            last = start
            for i in range(start + 1, end):
                if bare(lines[i]).startswith(">"):
                    last = i
            pre = [">"] if bare(lines[last]).startswith("> - ") else []
            new = pre + ["> ### " + f["domain"], fsline]
            lines[last + 1:last + 1] = [x + cr for x in new]
            section = "## Future Scope > ### %s (new heading at line %d)" % (
                f["domain"], last + len(pre) + 2)
    if not created:
        start, end = find_section(lines, "## Future Scope")
        count = sum(1 for i in range(start + 1, end) if bare(lines[i]).startswith("> - "))
        for i in range(start + 1, end):
            m = FS_COUNT.match(bare(lines[i]))
            if m:
                lines[i] = (m.group(1) + "%d item%s" % (count, "" if count == 1 else "s")
                            + m.group(2) + ("\r" if lines[i].endswith("\r") else ""))
                break
    bump(lines, LAST_UPDATED, "last_updated: " + f["added"])
    return {"text": "\n".join(lines), "section": section, "block": [fsline],
            "mirror_line": None, "linked": None}


def mirror_add(text, f):
    """(new text, mirror line); the line is reported even when no mirror file exists."""
    line = "- %s · %s · %s" % (f["cls"], f["scope"], f["title"])
    tier = "Ledger" if f["when"] else "Active"
    if f["when"]:
        line += " [after: %s]" % f["when"][6:]
    cr = "\r" if eol_of(text) == "\r\n" else ""
    lines = text.split("\n")
    start = None
    for i, raw in enumerate(lines):
        m = SUPERSECTION.match(bare(raw))
        if m and m.group(1) == tier:
            start = i
    if start is None:
        return text, line
    end = start + 1
    while end < len(lines) and not bare(lines[end]).startswith("## "):
        end += 1
    h = find_domain(lines, start, end, f["domain"])
    if h is not None:
        lines.insert(h + 1, line + cr)
    else:
        splice(lines, end, ["### " + f["domain"], line], cr, blank_before=True)
    bump_count(lines, tier, 1)
    bump(lines, LAST_SYNCED, "Last synced: " + f["added"])
    return "\n".join(lines), line


def parse_when(value):
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    if value == "future":
        return "future"
    if value.lower().startswith("after ") and value[6:].strip():
        return "after " + value[6:].strip()
    raise ValueError('when %r is not "future" or "after <condition>"' % value)


def parse_scope(value):
    try:
        n = int(str(value))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 4 else None


def validate(f, classes, domains):
    errors = []
    if not f["title"]:
        errors.append("title is required")
    if f["cls"] not in classes:
        errors.append("class %r is not one of: %s" % (f["cls"], ", ".join(classes)))
    known = [long for long, _ in domains]
    if f["domain"] not in known:
        errors.append("domain %r is not one of: %s" % (f["domain_text"], ", ".join(known)))
    if f["scope"] not in (1, 2, 3, 4):
        errors.append("scope %r is not 1, 2, 3 or 4" % (f["scope_text"],))
    if not f["context"]:
        errors.append("context is required")
    if f["when"] == "future":
        if f["plan_doc"]:
            errors.append("a Future Scope item carries no plan doc")
        if f["scope"] == 4:
            errors.append("scope 4 needs a plan doc, which Future Scope does not carry; "
                          "use scope 3 or an Active/Ledger item")
    elif f["scope"] == 4 and not f["plan_doc"]:
        errors.append('scope 4 needs --plan-doc "<Doc Title>"')
    return errors


def fields_from_args(args, classes, domains):
    f = {"title": (args.title or "").strip(), "cls": args.cls, "scope": parse_scope(args.scope),
         "scope_text": args.scope, "domain_text": (args.domain or "").strip(),
         "context": (args.context or "").strip(),
         "where": args.where, "source": args.source, "plan_doc": args.plan_doc,
         "added": args.date or date.today().isoformat(), "when": None}
    f["domain"] = resolve_domain(domains, f["domain_text"])
    errors = []
    try:
        f["when"] = parse_when(args.when)
    except ValueError as exc:
        errors.append(str(exc))
    return f, errors + validate(f, classes, domains)


def fields_from_row(row, classes, domains):
    f = {"title": str(row.get("title") or "").strip(),
         "cls": str(row.get("class") or row.get("cls") or "").strip(),
         "scope": parse_scope(row.get("scope")),
         "scope_text": row.get("scope"),
         "domain_text": str(row.get("domain") or "").strip(),
         "context": str(row.get("context") or "").strip(),
         "where": row.get("where") or None, "source": row.get("source") or None,
         "plan_doc": row.get("plan_doc") or row.get("plan-doc") or None,
         "added": row.get("date") or date.today().isoformat(), "when": None}
    f["domain"] = resolve_domain(domains, f["domain_text"])
    errors = []
    try:
        f["when"] = parse_when(row.get("when"))
    except ValueError as exc:
        errors.append(str(exc))
    return f, errors + validate(f, classes, domains)


def add_one(f, paths, distinct, dry_run):
    worklog_path, _, mirror_path, _ = paths
    text = read_text(worklog_path)
    items = find_items(text.split("\n")) + find_future(text.split("\n"))
    existing, kind = duplicate_of(items, f["title"], f["domain"], distinct)
    if existing is not None:
        note = " (near-duplicate; pass --distinct if it is a different item)" if kind == "near" else ""
        print("duplicate: %s — existing at line %d: %s%s"
              % (f["title"], existing.index + 1, existing.title, note))
        return "duplicate"
    plan = future_plan(text, f) if f["when"] == "future" else active_plan(text, f)
    mirror_text = read_text(mirror_path) if Path(mirror_path).exists() else None
    new_mirror, mirror_line = (mirror_add(mirror_text, f)
                               if mirror_text is not None and f["when"] != "future" else (None, None))

    if dry_run:
        print("added (dry run): %s" % f["title"])
        print("section: " + plan["section"])
        for line in plan["block"]:
            print("  " + line)
        if plan["linked"]:
            print("linked docs: " + plan["linked"])
        print("mirror line: " + (mirror_line if mirror_line is not None
                                 else "(none: Future Scope is excluded)" if f["when"] == "future"
                                 else "(none: no mirror file)"))
        return "added"

    write_atomic(worklog_path, plan["text"])
    try:
        if new_mirror is not None:
            write_atomic(mirror_path, new_mirror)
    except OSError as exc:
        print("mirror write failed, Worklog.md holds the item: %s" % exc, file=sys.stderr)
        return "error"
    print("added: %s" % f["title"])
    return "added"


def print_plan(lines, start, end, xline, section, mirror_line):
    print("block lines %d-%d:" % (start + 1, end))
    for raw in lines[start:end]:
        print("  " + bare(raw))
    print("XLINE: " + xline)
    print("archive section: " + section)
    print("mirror line: " + (mirror_line if mirror_line is not None else "(none)"))


def complete_one(args, query, ref, paths):
    """(status, label); prints the candidates, the miss or the dry-run plan."""
    worklog_path, archive_path, mirror_path, history_path = paths
    text = read_text(worklog_path)
    lines = text.split("\n")
    item, candidates = pick(find_items(lines), query)
    if item is None:
        if candidates:
            for c in candidates:
                print("  line %d: %s" % (c.index + 1, c.title))
            return "ambiguous", query
        return "no-match", query

    start, end = item.index, block_end(lines, item.index)
    day = args.date or date.today().isoformat()
    xline = XLINE.format(cls=item.cls, scope=item.scope, title=item.title,
                         date=day, ref=", " + ref if ref else "")
    archive_text = read_text(archive_path) if Path(archive_path).exists() else None
    new_archive, section = archive_plan(archive_text, item.domain, xline, eol_of(text))
    mirror_text = read_text(mirror_path) if Path(mirror_path).exists() else None
    new_mirror, mirror_line = mirror_plan(mirror_text, item)

    if args.dry_run:
        print_plan(lines, start, end, xline, section, mirror_line)
        return "completed", item.title

    try:
        write_atomic(archive_path, new_archive)
    except OSError as exc:
        print("archive write failed, Worklog.md left alone: %s" % exc, file=sys.stderr)
        return "error", item.title

    del lines[start:end]
    # `add` separates a new top-of-section block with a blank line; drop it so add+complete
    # returns the section to its starting bytes.
    if (item.heading is not None and 0 < start < len(lines)
            and bare(lines[start]).strip() == ""
            and bare(lines[start - 1]).startswith("### ")):
        del lines[start]
    if item.heading is not None:
        stop = domain_end(lines, item.heading)
        if not any(bare(lines[i]).startswith("- [ ] ") for i in range(item.heading + 1, stop)):
            del lines[item.heading]
    bump(lines, LAST_UPDATED, "last_updated: " + date.today().isoformat())
    write_atomic(worklog_path, "\n".join(lines))

    if new_mirror is not None:
        write_atomic(mirror_path, new_mirror)

    if Path(history_path).exists():
        history_text = read_text(history_path)
        new_history = history_append(history_text, item.title, day)
        if new_history is not None:
            write_atomic(history_path, new_history)

    return "completed", item.title


def vault_todo(root=None):
    """`<VAULT_ROOT>/DevProjects/<PROJECT_NAME>/Claude/TODO` from the lock's substitutions, the owner
    hooks/overnight_ask_guard.py reads; raises ValueError naming the lock when it holds neither."""
    lock = Path(root or REPO) / LOCK
    try:
        subs = json.loads(read_text(lock))["substitutions"]
        vault, project = subs[TOKEN % "VAULT_ROOT"], subs[TOKEN % "PROJECT_NAME"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("%s has no readable VAULT_ROOT and PROJECT_NAME substitutions (%r)"
                         % (lock, exc))
    return Path(vault) / "DevProjects" / project / "Claude" / "TODO"


def resolve_paths(args):
    if args.worklog and args.archive and args.mirror and args.history:
        return Path(args.worklog), Path(args.archive), Path(args.mirror), Path(args.history)
    todo = vault_todo()
    return (Path(args.worklog) if args.worklog else todo / WORKLOG_NAME,
            Path(args.archive) if args.archive else todo / ARCHIVE_NAME,
            Path(args.mirror) if args.mirror else REPO / MIRROR,
            Path(args.history) if args.history else REPO / HISTORY)


def iso_day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD")


def add_paths(args):
    """apply an `add` invocation; returns the exit code."""
    try:
        classes, domains = read_canonical(Path(args.skill) if args.skill else REPO / SKILL)
        paths = resolve_paths(args)
    except ValueError as exc:
        print("invalid: %s" % exc, file=sys.stderr)
        return 2
    if args.from_file:
        statuses = []
        raw_lines = Path(args.from_file).read_text(encoding="utf-8").splitlines()
        for lineno, raw in enumerate(raw_lines, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                print("invalid: line %d — not JSON" % lineno)
                statuses.append("invalid")
                continue
            f, errors = fields_from_row(row, classes, domains)
            if errors:
                print("invalid: %s — %s" % (f["title"] or "line %d" % lineno, "; ".join(errors)))
                statuses.append("invalid")
                continue
            statuses.append(add_one(f, paths, bool(row.get("distinct", args.distinct)), args.dry_run))
        return 0 if statuses and all(s == "added" for s in statuses) else 2

    f, errors = fields_from_args(args, classes, domains)
    if errors:
        for error in errors:
            print("invalid: %s" % error, file=sys.stderr)
        return 2
    status = add_one(f, paths, args.distinct, args.dry_run)
    return 0 if status == "added" else 2 if status == "duplicate" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="worklog.py", description="Add or complete worklog items.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="append an item to Worklog.md and patch the mirror")
    add.add_argument("title", nargs="?", help="short imperative title")
    add.add_argument("--class", dest="cls", help="canonical class from worklog_reference")
    add.add_argument("--scope", help="1-4")
    add.add_argument("--domain", help="canonical domain from worklog_reference, long or short form")
    add.add_argument("--context", help="one line on why it matters")
    add.add_argument("--where", help="file path or scene reference")
    add.add_argument("--source", help="session, PR ref or commit hash")
    add.add_argument("--when", help='"after <condition>" or "future"')
    add.add_argument("--plan-doc", help='doc title for a scope-4 item: "Doc Title"')
    add.add_argument("--date", type=iso_day, help="added date, default today")
    add.add_argument("--distinct", action="store_true",
                     help="the near-duplicate is a different item (never waives an exact title)")
    add.add_argument("--from", dest="from_file", help="JSONL of item objects, one per line")
    add.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    add.add_argument("--skill", help="SKILL.md holding the canonical class and domain tables")
    add.add_argument("--worklog")
    add.add_argument("--archive")
    add.add_argument("--mirror")
    add.add_argument("--history")

    comp = sub.add_parser("complete", help="move an item from Worklog.md to Worklog-Archive.md")
    comp.add_argument("search", nargs="?", help="title or search text")
    comp.add_argument("--from", dest="from_file", help="JSONL of {title, ref} rows, one per line")
    comp.add_argument("--ref", help="commit hash, PR or session name")
    comp.add_argument("--date", type=iso_day, help="completion date, default today")
    comp.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    comp.add_argument("--worklog")
    comp.add_argument("--archive")
    comp.add_argument("--mirror")
    comp.add_argument("--history")
    args = parser.parse_args(argv)

    if args.command == "add":
        if not args.title and not args.from_file:
            parser.error("add needs a title or --from FILE.jsonl")
        if args.title and args.from_file:
            parser.error("add takes a title or --from FILE.jsonl, not both")
        return add_paths(args)

    if not args.search and not args.from_file:
        parser.error("complete needs a title or --from FILE.jsonl")
    if args.search and args.from_file:
        parser.error("complete takes a title or --from FILE.jsonl, not both")

    try:
        paths = resolve_paths(args)
    except ValueError as exc:
        print("invalid: %s" % exc, file=sys.stderr)
        return 2
    if args.from_file:
        statuses = []
        for raw in Path(args.from_file).read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                print("no-match: %s" % raw)
                statuses.append("no-match")
                continue
            title = row.get("title", "")
            status, label = complete_one(args, title, row.get("ref") or args.ref, paths)
            print("%s: %s" % (status, label))
            statuses.append(status)
        return 0 if statuses and all(s == "completed" for s in statuses) else 2

    status, label = complete_one(args, args.search, args.ref, paths)
    print("%s: %s" % (status, label))
    return 0 if status == "completed" else 1 if status == "error" else 2


if __name__ == "__main__":
    sys.exit(main())
