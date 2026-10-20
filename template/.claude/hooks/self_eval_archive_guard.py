#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — validates the self-evaluation snapshot and ledger.

The JSON snapshot is checked as one document. A ledger edit reports only added or changed
rows; whitespace/key-order rewrites are unchanged. Identity checks still read the snapshot,
rotations and active ledger, but report only conflicts involving the touched rows. Full store
validation keeps newest-row-wins per session and checks every row. Other paths emit `{}`.
PostToolUse cannot block; violations use `additionalContext` so the model repairs
manual writes. The archive store validates before publication. The WARN-first rules
(`entry_warnings`) are advisory at every id: they never refuse, and the store prints
them at upsert.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness_growth_guard import added_lines, pre_edit_text

OUTCOMES = {"clean", "correction", "failure"}
FACTS_KEYS = ("driver", "driver_mixed", "owner_prompts", "duration_min")
RULE_MISS_KINDS = ("not-loaded", "not-read", "not-followed")
RULE_MISS_KEYS = ("rule", "kind", "evidence")
ACCEPTED_CLASSES = ("guard-working", "outside", "owner-held", "one-off")
LEGACY_MAX_ID = 266
SPEND_MIN_ID = 310
FIX_MIN_ID = 314
# WARN-first rows (/self_evaluate §5d): the per-correction shape checks refuse at every id, while
# the coupling and the accepted-class requirements warn only above these cutoffs. Each is the live
# max id at landing, so a row published before the field existed is never warned at (S11 flips the
# warnings to refusals once a live session has exercised them).
RULE_FIELDS_MIN_ID = 316
ACCEPTED_CLASS_MIN_ID = 316
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHA_RE = re.compile(r"(?<![a-zA-Z0-9])(?=[a-fA-F0-9]*\d)[a-fA-F0-9]{7,40}(?![a-zA-Z0-9])")
ROW_ID_RE = re.compile(r"^(O|P|T|X|D\d+)(\.\w+)?$")
ENUM_HOMES = ("enum homes: patterns = Self_Evaluate_Themes.patterns in the archive; "
              "outcomes = clean|correction|failure (/self_evaluate §5d); "
              "spend = the /self_evaluate §5d spend object, row ids O|P|T|X|D<n>[.field]")


def _row_ids_ok(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and ROW_ID_RE.match(item) for item in value))


def check_spend(spend, label):
    """Violations of one entry's `spend` object (/self_evaluate §5d); [] when well formed."""
    if not isinstance(spend, dict):
        return [f"{label}: spend must be an object"]
    if "unavailable" in spend:
        reason = spend["unavailable"]
        if isinstance(reason, str) and reason.strip():
            return []
        return [f"{label}: spend.unavailable must be a non-empty reason string"]
    out = []
    if not isinstance(spend.get("rollup"), str) or not spend["rollup"].strip():
        out.append(f"{label}: spend.rollup must be the rollup JSON path")
    total = spend.get("total")
    if not isinstance(total, dict):
        out.append(f"{label}: spend.total must be an object")
    else:
        for key in ("list", "wall_min", "split", "rows"):
            if key not in total:
                out.append(f"{label}: spend.total.{key} is missing")
        if "rows" in total and not _row_ids_ok(total["rows"]):
            out.append(f"{label}: spend.total.rows must list row ids matching {ROW_ID_RE.pattern}")
        split = total.get("split")
        if "split" in total and not (isinstance(split, dict) and all(
                k in split for k in ("orchestrator", "dispatches", "proofs", "owner"))):
            out.append(f"{label}: spend.total.split must carry orchestrator, dispatches, proofs and owner")
    waste = spend.get("waste")
    if not isinstance(waste, list):
        out.append(f"{label}: spend.waste must be a list")
    else:
        for index, item in enumerate(waste):
            if not isinstance(item, dict):
                out.append(f"{label}: spend.waste[{index}] must be an object")
                continue
            for key in ("rows", "class", "list", "min", "cause", "preventer"):
                if key not in item:
                    out.append(f"{label}: spend.waste[{index}].{key} is missing")
            if "rows" in item and not _row_ids_ok(item["rows"]):
                out.append(f"{label}: spend.waste[{index}].rows has a malformed row id: {item['rows']!r}")
    for name, keys in (("costliest", ("rows", "list", "bought")), ("change", ("text", "rows"))):
        block = spend.get(name)
        if not isinstance(block, dict):
            out.append(f"{label}: spend.{name} must be an object")
            continue
        for key in keys:
            if key not in block:
                out.append(f"{label}: spend.{name}.{key} is missing")
        if "rows" in block and not _row_ids_ok(block["rows"]):
            out.append(f"{label}: spend.{name}.rows must list row ids matching {ROW_ID_RE.pattern}")
    return out


def check_facts(facts, label):
    """Violations of one entry's `facts` object (/self_evaluate §5d); [] when well formed.

    `facts` is store-derived (transcript numbers), so the guard refuses a value the store would
    never write and accepts absence at every id: rows published before the field existed carry
    none, and their readers type them absent rather than zero.
    """
    if not isinstance(facts, dict):
        return [f"{label}: facts must be an object"]
    if "unavailable" in facts:
        reason = facts["unavailable"]
        if isinstance(reason, str) and reason.strip() and len(facts) == 1:
            return []
        return [f"{label}: facts.unavailable must be the only key, a non-empty reason string"]
    out = []
    for key in sorted(set(facts) - set(FACTS_KEYS)):
        out.append(f"{label}: facts.{key} is not a store-derived fact "
                   f"({', '.join(FACTS_KEYS)}), or an unavailable reason")
    driver = facts.get("driver")
    if not isinstance(driver, dict):
        out.append(f"{label}: facts.driver must be an object")
    else:
        for key in sorted(set(driver) - {"model", "effort"}):
            out.append(f"{label}: facts.driver.{key} is not a driver field (model, effort)")
        for key in ("model", "effort"):
            value = driver.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                out.append(f"{label}: facts.driver.{key} must be a non-empty string or null")
    if "driver_mixed" in facts and facts["driver_mixed"] is not True:
        out.append(f"{label}: facts.driver_mixed, when present, is true")
    prompts = facts.get("owner_prompts")
    if isinstance(prompts, bool) or not isinstance(prompts, int) or prompts < 0:
        out.append(f"{label}: facts.owner_prompts must be a non-negative integer")
    minutes = facts.get("duration_min")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        out.append(f"{label}: facts.duration_min must be a non-negative integer of minutes")
    return out


def check_cause_counts(cause_counts, corrections, patterns, label):
    """Violations of one entry's `cause_counts` object (/self_evaluate §5d); [] when well formed.

    The model fills it: one count per pattern letter, summing to the number of `corrections`. A
    malformed value is wrong at every id, so this refuses rather than warns.
    """
    if not isinstance(cause_counts, dict):
        return [f"{label}: cause_counts must be an object of pattern letter -> count"]
    out = []
    total = 0
    well_formed_values = True
    for letter, count in sorted(cause_counts.items(), key=lambda item: str(item[0])):
        if letter not in patterns:
            out.append(f"{label}: cause_counts key {letter!r} is not a pattern letter")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            out.append(f"{label}: cause_counts[{letter!r}] must be a non-negative integer")
            well_formed_values = False
        else:
            total += count
    if not isinstance(corrections, list):
        out.append(f"{label}: cause_counts needs the corrections list to sum against")
    elif well_formed_values and total != len(corrections):
        out.append(f"{label}: cause_counts sums to {total} but corrections has {len(corrections)} row(s)")
    return out


def check_rule_misses(rule_misses, label):
    """Violations of one entry's `rule_misses` list (/self_evaluate §5d); [] when well formed.

    Each row names the rule that existed and was not used, how it was missed, and the evidence; a
    vacuous row (blank evidence) is refused, so the list cannot be padded to satisfy the coupling.
    """
    if not isinstance(rule_misses, list):
        return [f"{label}: rule_misses must be a list"]
    out = []
    for index, item in enumerate(rule_misses):
        where = f"{label}: rule_misses[{index}]"
        if not isinstance(item, dict):
            out.append(f"{where} must be an object")
            continue
        for key in sorted(set(item) - set(RULE_MISS_KEYS)):
            out.append(f"{where}.{key} is not a rule_miss field ({', '.join(RULE_MISS_KEYS)})")
        rule = item.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            out.append(f"{where}.rule must be a repo-relative path")
        if item.get("kind") not in RULE_MISS_KINDS:
            out.append(f"{where}.kind must be one of {', '.join(RULE_MISS_KINDS)}")
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            out.append(f"{where}.evidence must name a digest evidence id or quote the owner")
    return out


def check_recurrences(rows, label):
    """Validate explicit evidence judgments; never infer a recurrence from similar prose."""
    if not isinstance(rows, list):
        return [f'{label}: codified_recurrences must be a list']
    violations = []
    for index, row in enumerate(rows):
        where = f'{label}: codified_recurrences[{index}]'
        if not isinstance(row, dict):
            violations.append(f'{where} must be an object')
            continue
        for key in ('lessonId', 'claimId', 'failureKey', 'evidence', 'priorLanding'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                violations.append(f'{where}.{key} must be a non-empty string')
        if 'behaviorChanged' not in row or (row['behaviorChanged'] is not None
                                            and not isinstance(row['behaviorChanged'], bool)):
            violations.append(f'{where}.behaviorChanged must be true, false or null')
        if 'repairKind' in row and row['repairKind'] not in ('tool', 'reach-only'):
            violations.append(f'{where}.repairKind must be tool or reach-only')
    return violations


def archive_kind(file_path):
    normalized = (file_path or "").replace("\\", "/")
    if normalized.endswith("self_evaluate_archive.json"):
        return "snapshot", file_path
    if re.search(r"self_evaluate_archive\.jsonl(?:\.\d+)?$", normalized):
        archive = re.sub(r"\.jsonl(?:\.\d+)?$", ".json", file_path)
        return "ledger", archive
    return None, None


def rel_path_ok(file_path):
    return archive_kind(file_path)[0] is not None


def shas_in(strings, session_ids):
    """Sorted, unique commit-sha-like tokens; a prefix of a session id is an id, not a sha."""
    return sorted({m.group(0).lower() for text in strings for m in SHA_RE.finditer(text)
                   if not any(sid.startswith(m.group(0).lower()) for sid in session_ids)})


def _is_commit(root, sha):
    try:
        result = subprocess.run(["git", "-C", root, "cat-file", "-e", sha + "^{commit}"],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError:
        return False
    return result.returncode == 0


def _mirror_titles(root):
    """Normalized titles from the worklog mirror; `- <class> · <scope> · <title> [after: …] [quick-win]`."""
    try:
        with open(os.path.join(root, ".claude", "worklog-titles.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return set()
    titles = set()
    for line in lines:
        parts = line[2:].split(" · ", 2) if line.startswith("- ") else []
        if len(parts) == 3:
            title = re.sub(r"\s*\[(after:[^\]]*|quick-win)\]", "", parts[2])
            titles.add(" ".join(title.lower().split()))
    return titles


def _dispositions(entry):
    friction = entry.get("friction")
    if not isinstance(friction, list):
        return []
    return [row.get("disposition") for row in friction if isinstance(row, dict)]


def fix_evidence(entry, session_ids, root):
    """Fix evidence on one row: sha tokens that resolve to commits, and worklog titles on the mirror."""
    dispositions = _dispositions(entry)
    fields = dispositions + [entry.get("notes"), entry.get("key_takeaway")]
    strings = [text for text in fields if isinstance(text, str)]
    shas = [sha for sha in shas_in(strings, session_ids) if _is_commit(root, sha)]
    mirror = _mirror_titles(root)
    worklog = []
    for disposition in dispositions:
        if isinstance(disposition, str) and disposition.lower().startswith("worklog:"):
            title = disposition.split(":", 1)[1].strip()
            if " ".join(title.lower().split()) in mirror:
                worklog.append(title)
    return {"shas": shas, "worklog": worklog}


def check(text, require_session=False, root=None):
    """Return a list of `id=<n>: <violation>` strings, or [] when clean."""
    try:
        doc = json.loads(text)
    except ValueError:
        return ["id=?: archive is not valid JSON"]
    if not isinstance(doc, dict):
        return ["id=?: archive root must be an object"]
    root = root or REPO_ROOT

    violations = []
    themes = doc.get("Self_Evaluate_Themes")
    pattern_defs = themes.get("patterns") if isinstance(themes, dict) else None
    if not isinstance(pattern_defs, dict):
        violations.append("id=?: Self_Evaluate_Themes.patterns must be an object")
        patterns = {None}
    else:
        patterns = set(pattern_defs)
        patterns.add(None)

    entries = doc.get("structured_entries")
    if not isinstance(entries, list):
        violations.append("id=?: structured_entries must be a list")
        return violations
    seen_ids = {}
    seen_sessions = {}
    session_ids = [e["session_id"].lower() for e in entries
                   if isinstance(e, dict) and isinstance(e.get("session_id"), str)]

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            violations.append(f"id=?: structured entry {index} must be an object")
            continue
        eid = entry.get("id")
        outcome = entry.get("outcome")
        pattern = entry.get("pattern")
        session_id = entry.get("session_id")

        valid_id = isinstance(eid, int) and not isinstance(eid, bool)
        if not valid_id:
            violations.append(f"id=?: structured entry {index} id must be an integer")
        elif eid in seen_ids:
            violations.append(f"id={eid}: duplicate id")
        else:
            seen_ids[eid] = True

        session_valid = isinstance(session_id, str) and bool(session_id.strip())
        session_required = require_session or (valid_id and eid > LEGACY_MAX_ID)
        if session_required and not session_valid:
            violations.append(
                f"id={eid if valid_id else '?'}: session_id must be a non-empty string"
            )
        elif session_id is not None and not session_valid:
            violations.append(f"id={eid if valid_id else '?'}: session_id must be a non-empty string")
        elif session_valid and session_id in seen_sessions:
            violations.append(f"id={eid}: duplicate session_id {session_id!r}")
        elif session_valid:
            seen_sessions[session_id] = True

        if "outcome" not in entry:
            violations.append(f"id={eid if valid_id else '?'}: missing outcome")
        elif not isinstance(outcome, str) or outcome not in OUTCOMES:
            violations.append(f"id={eid if valid_id else '?'}: outcome {outcome!r} not in {sorted(OUTCOMES)}")

        valid_pattern = pattern is None or isinstance(pattern, str)
        if not valid_pattern:
            violations.append(f"id={eid if valid_id else '?'}: pattern must be a string or null")
        if valid_id and eid > LEGACY_MAX_ID:
            if valid_pattern and pattern not in patterns:
                violations.append(f"id={eid}: pattern {pattern!r} not in enum")
            if pattern is None and outcome in ("correction", "failure"):
                violations.append(f"id={eid}: null pattern with outcome {outcome!r}")
        label = f"id={eid if valid_id else '?'}"
        if 'codified_recurrences' in entry:
            violations.extend(check_recurrences(entry['codified_recurrences'], label))
        if "facts" in entry:
            violations.extend(check_facts(entry["facts"], label))
        if "cause_counts" in entry:
            violations.extend(check_cause_counts(entry["cause_counts"], entry.get("corrections"),
                                                 patterns, label))
        if "rule_misses" in entry:
            violations.extend(check_rule_misses(entry["rule_misses"], label))
        if "spend" in entry:
            violations.extend(check_spend(entry["spend"], label))
        elif valid_id and eid > SPEND_MIN_ID:
            violations.append(f"{label}: spend is required for ids above {SPEND_MIN_ID} "
                              "(/self_evaluate §5d: the rollup totals, or {\"unavailable\": reason})")

        if valid_id and eid > FIX_MIN_ID and (pattern == "E" or outcome == "failure"):
            evidence = fix_evidence(entry, session_ids, root)
            if not (evidence["shas"] or evidence["worklog"]):
                violations.append(
                    f"id={eid}: failed or cause-E session records no fix (/self_evaluate §5d friction): add the fix commit sha to notes, "
                    'or add the item through /worklog and a friction row with disposition "worklog: <exact title>" matching .claude/worklog-titles.md')

    return violations


def _accepted_class_missing(disposition):
    return not re.match(r"^accepted\s*:\s*(?:%s)\s*:" % "|".join(ACCEPTED_CLASSES),
                        disposition, re.I)


def entry_warnings(entry, root=None):
    """Advisory notes for ONE entry (never a refusal): the WARN-first rows.

    Unlike `check`, these bind only above their id cutoffs, so a row published before the field
    existed is never warned at. Three rules, all WARN-first (/self_evaluate §5d):

      * cause A carries no `not-followed`/`not-read` rule_miss (above `RULE_FIELDS_MIN_ID`);
      * a `rule_misses[].rule` path does not exist under the repo root (checked at upsert only, so
        a later file move never invalidates history);
      * an `accepted:` friction disposition names no class (above `ACCEPTED_CLASS_MIN_ID`).
    """
    if not isinstance(entry, dict):
        return []
    eid = entry.get("id")
    if not (isinstance(eid, int) and not isinstance(eid, bool)):
        return []
    root = root or REPO_ROOT
    out = []
    misses = entry.get("rule_misses")
    misses = misses if isinstance(misses, list) else []
    if eid > RULE_FIELDS_MIN_ID:
        causes = entry.get("cause_counts")
        if isinstance(causes, dict) and causes.get("A"):
            kinds = {item.get("kind") for item in misses if isinstance(item, dict)}
            if not kinds & {"not-followed", "not-read"}:
                out.append(f"id={eid}: WARN — cause A records no not-followed/not-read rule_miss "
                           "(/self_evaluate §5d: name the rule that existed and was not used)")
    for index, item in enumerate(misses):
        if not isinstance(item, dict):
            continue
        rule = item.get("rule")
        if isinstance(rule, str) and rule.strip():
            path = rule.split("§", 1)[0].strip()
            if path and not os.path.exists(os.path.join(root, path)):
                out.append(f"id={eid}: WARN — rule_misses[{index}].rule {path!r} is not a file "
                           "under the repo root")
    if eid > ACCEPTED_CLASS_MIN_ID:
        friction = entry.get("friction")
        for index, item in enumerate(friction if isinstance(friction, list) else []):
            disposition = item.get("disposition") if isinstance(item, dict) else None
            if isinstance(disposition, str) and re.match(r"^accepted\b", disposition, re.I) \
                    and _accepted_class_missing(disposition):
                out.append(f"id={eid}: WARN — friction[{index}].disposition needs a class "
                           f"({'/'.join(ACCEPTED_CLASSES)}): accepted: <class>: <why>")
    return out


def _warnings_for(entries):
    out = []
    for entry in entries:
        out.extend(entry_warnings(entry))
    return out


def _parsed_lines(text):
    """JSON objects from canonical JSONL text; an unparseable line is the ledger check's business."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


LEDGER_ROTATIONS = 6


def _ledger_paths(archive_path):
    active = archive_path[:-5] + ".jsonl" if archive_path.endswith(".json") else archive_path + ".jsonl"
    rotated = [active + f".{index}" for index in range(LEDGER_ROTATIONS, 0, -1)]
    return [path for path in rotated + [active] if os.path.isfile(path)]


def _parse_ledger(text, source):
    entries = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return [], [f"id=?: ledger row {line_number} in {source} is not valid JSON"]
        if not isinstance(entry, dict):
            return [], [f"id=?: ledger row {line_number} in {source} is not an object"]
        entries.append(entry)
    return entries, []


def _owner(entry, source, index):
    session_id = entry.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return ("session", session_id)
    return (source, index)


def _identity(entry, source, index):
    session_id = entry.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return ("session", session_id)
    if source == "snapshot":
        legacy = ("legacy", entry.get("title"), entry.get("date"))
        try:
            hash(legacy)
            return legacy
        except TypeError:
            pass
    return (source, index)


def check_ledger(text, archive_path, edited_path=None, changed_text=None):
    try:
        with open(archive_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        return ["id=?: cannot read legacy snapshot for ledger validation"]
    if not isinstance(snapshot, dict):
        return ["id=?: legacy snapshot root must be an object"]

    focused = changed_text is not None
    selected, violations = _parse_ledger(changed_text, os.path.basename(edited_path or archive_path)) if focused else ([], check(json.dumps(snapshot)))
    selected_keys = {json.dumps(row, sort_keys=True) for row in selected}
    selected_ids = {row.get("id") for row in selected if isinstance(row.get("id"), int)}
    selected_sessions = {row.get("session_id") for row in selected if isinstance(row.get("session_id"), str)}
    snapshot_entries = snapshot.get("structured_entries")
    if not isinstance(snapshot_entries, list):
        return violations

    ledger_entries = []
    edited_norm = os.path.normcase(os.path.abspath(edited_path)) if edited_path else None
    paths = _ledger_paths(archive_path)
    if edited_path and edited_norm not in {os.path.normcase(os.path.abspath(path)) for path in paths}:
        paths.append(edited_path)
    for path in paths:
        try:
            if edited_norm == os.path.normcase(os.path.abspath(path)):
                content = text
            else:
                with open(path, encoding="utf-8") as handle:
                    content = handle.read()
        except OSError:
            violations.append(f"id=?: cannot read ledger {path}")
            continue
        if focused:
            rows = []
            for line in content.splitlines():
                parsed, _errors = _parse_ledger(line, os.path.basename(path))
                rows.extend(parsed)
        else:
            rows, errors = _parse_ledger(content, os.path.basename(path))
            violations.extend(errors)
        ledger_entries.extend((row, path) for row in rows)

    themes = snapshot.get("Self_Evaluate_Themes") or {}
    for entry, _source in ledger_entries:
        if focused and json.dumps(entry, sort_keys=True) not in selected_keys:
            continue
        probe = {"Self_Evaluate_Themes": themes, "structured_entries": [entry]}
        violations.extend(check(json.dumps(probe), require_session=True))

    raw = [(entry, "snapshot") for entry in snapshot_entries if isinstance(entry, dict)]
    raw.extend(ledger_entries)
    id_owners = {}
    owner_ids = {}
    effective = {}
    for index, (entry, source) in enumerate(raw):
        owner = _owner(entry, source, index)
        eid = entry.get("id")
        if isinstance(eid, int) and not isinstance(eid, bool):
            previous_owner = id_owners.get(eid)
            session = entry.get("session_id")
            relevant = not focused or eid in selected_ids or (isinstance(session, str) and session in selected_sessions)
            if previous_owner is not None and previous_owner != owner:
                if relevant:
                    violations.append(f"id={eid}: id ownership conflict between distinct sessions")
            else:
                id_owners[eid] = owner
            previous_id = owner_ids.get(owner)
            if previous_id is not None and previous_id != eid:
                if relevant:
                    violations.append(
                        f"id={eid}: session_id {entry.get('session_id')!r} changed from id={previous_id}"
                    )
            else:
                owner_ids[owner] = eid
        effective[_identity(entry, source, index)] = entry

    probe = {"Self_Evaluate_Themes": themes, "structured_entries": list(effective.values())}
    if not focused:
        violations.extend(check(json.dumps(probe)))
    return list(dict.fromkeys(violations))


def _canonical_ledger(text):
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            lines.append(json.dumps(json.loads(line), sort_keys=True))
        except ValueError:
            lines.append(line)
    return "\n".join(lines)


def process(data):
    """Dispatcher entry: `{"context": report}` when the archive is invalid, else None.

    `post_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if data.get("tool_name") not in ("Write", "Edit"):
        return None
    file_path = (data.get("tool_input") or {}).get("file_path")
    kind, archive_path = archive_kind(file_path)
    if kind is None:
        return None

    try:
        with open(file_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        violations = [f"id=?: cannot read archive input: {type(exc).__name__}: {exc}"]
        warnings = []
    else:
        warnings = []
        if kind == "snapshot":
            violations = check(text)
            try:
                doc = json.loads(text)
            except ValueError:
                doc = None
            if isinstance(doc, dict) and isinstance(doc.get("structured_entries"), list):
                warnings = _warnings_for(doc["structured_entries"])
        else:
            before = pre_edit_text(data, text)
            if before is None:
                return None
            changed = "\n".join(added_lines(_canonical_ledger(before), _canonical_ledger(text)))
            if not changed.strip():
                return None
            violations = check_ledger(text, archive_path, file_path, changed_text=changed)
            warnings = _warnings_for(_parsed_lines(changed))
    if not violations and not warnings:
        return None

    lines = []
    if violations:
        lines.append(f"[self-eval-archive-guard] {len(violations)} violation(s) — fix before continuing:")
        lines.extend(violations[:10])
        if any(re.search(r"outcome|pattern.*(?:enum|must be a string)|row ids|malformed row id|patterns must be", violation)
               for violation in violations):
            lines.append(ENUM_HOMES)
    if warnings:
        lines.append(f"[self-eval-archive-guard] {len(warnings)} advisory warning(s) — not blocking:")
        lines.extend(warnings[:10])
    return {"context": "\n".join(lines)}


def main():
    data = json.load(sys.stdin)
    result = process(data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": result["context"]}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
