#!/usr/bin/env python3
"""Work triage lookup: derive two of the six triage fields, reach and oracle, from changed paths.

A tag registry (`sensitivity_tags` in `skills/project_subsystems/adaptation.json`) maps path globs, and
optionally a content pattern the changed lines must match, to the value each tag sets. This module holds
no project path: every project fact lives in that data.

    triage.py lookup <path>... [--acceptance owner-criterion|feel|visuals|tuning]
                                        derive over planned paths; unknown predicates stay conditional
    triage.py check-registry             validate every registry entry; exit 1 on an invalid one
    triage.py rederive <rev-range>       flag each commit whose Triage: trailer sits below its own diff

Trailer, one line, keys in any order; reversibility and kind are optional and validated when supplied.
The other five keys are required; the last `Triage:` line of a message counts:

    Triage: reach=local reversibility=clean design=settled grounding=verified oracle=gate+new-test size=one-context

    reach          local | shared | external
    reversibility  clean | residue | none
    design         settled | open
    grounding      verified | assumed
    oracle         one or more of gate | new-test | runtime | owner, joined by +, or none
    size           one-context | milestones | parts   (retired multi-part and programme still parse)
    kind           decision | mechanical (optional; current() defaults to decision)

Standing reach is derived from load_census's standing files and imports, never declared.
`sentences (--staged | --rev R)` prints changed doctrine sentences using sentence_diff.

Callers read one shape: `derive(changes, tags)` over `planned_changes`, `changes_from_index` or
`changes_from_commit`; `raises(fields, result)` compares reach and oracle, the path-derivable fields.
`domain_keys(paths, claude_dir)` is the tag-name domain an escape row and a review yield row key on.
Triage may raise reach and add oracle values, never lower or remove them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import re
import sys
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
_HOOKS = os.path.normpath(os.path.join(HERE, "..", "hooks"))
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)
import adaptation  # noqa: E402
import _git_commit  # noqa: E402

FIELDS = {
    "reach": ("local", "shared", "external"),
    "reversibility": ("clean", "residue", "none"),
    "design": ("settled", "open"),
    "grounding": ("verified", "assumed"),
    "oracle": ("gate", "new-test", "runtime", "owner"),
    "size": ("one-context", "milestones", "parts"),
}
# Retired values old commit trailers still carry, read as their replacement.
RETIRED = {"size": {"multi-part": "milestones", "programme": "parts"}}
OPTIONAL_FIELDS = {"kind": ("decision", "mechanical")}
REACH_ORDER = ("local", "shared", "standing", "external")
REACH_RANK = {value: rank for rank, value in enumerate(REACH_ORDER)}
_SET_KEYS = ("reach", "oracle")
_STATUS_LETTERS = set("ACDMRTUXB")
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTRY_KEYS = {"id", "paths", "hunk", "file", "status", "sets", "probe", "probe_validated", "source", "acceptance", "file_attributes"}
OWNER_ACCEPTANCE = ("owner-criterion", "feel", "visuals", "tuning")
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
UNTAGGED = "untagged"


# --- data model ---------------------------------------------------------------------------------------------

class Change:
    """One changed path. `status` is a git status letter, or None for a planned path; `hunk` the added and
    removed lines (no context lines) or None; `file_text` the post-image or None. A change built from git
    reads its hunk and post-image on first use, so a path no tag's glob matches costs no git call."""

    def __init__(self, path, status=None, hunk=None, file_text=None, load_hunk=None, load_file=None):
        self.path = path
        self.status = status
        self._hunk, self._file_text = hunk, file_text
        self._load_hunk, self._load_file = load_hunk, load_file

    @property
    def planned(self):
        return self.status is None and self._hunk is None and self._file_text is None \
            and self._load_hunk is None and self._load_file is None

    @property
    def hunk(self):
        if self._hunk is None and self._load_hunk:
            self._hunk, self._load_hunk = self._load_hunk(self.path), None
        return self._hunk

    @property
    def file_text(self):
        if self._file_text is None and self._load_file:
            self._file_text, self._load_file = self._load_file(self.path), None
        return self._file_text

    def __repr__(self):
        return "Change(%r, %r)" % (self.path, self.status)


@dataclass
class Tag:
    id: str
    paths: list
    sets: dict
    source: str
    hunk: object = None
    file: object = None
    status: str = ""
    probe: str = ""
    probe_validated: object = None
    _globs: list = field(default_factory=list)
    acceptance: tuple = ()
    file_attributes: tuple = ()


@dataclass
class Tags:
    valid: list
    invalid: list          # [(entry_index, reason)]


@dataclass
class Match:
    tag: str
    path: str
    sets: dict
    probe: str
    probe_validated: object
    conditional: bool
    why: str


@dataclass
class Result:
    reach: str
    oracle: frozenset
    matches: list
    invalid: list


@dataclass
class Fields:
    reach: str = "local"
    oracle: frozenset = frozenset()
    kind: str = "decision"
    size: str = "one-context"
    paths: tuple = ()


@dataclass
class Admission:
    admitted: bool
    width: str | None
    reason: str


CHECKS = ("harness-quality-seat", "candidate-lenses", "premises", "iq-audit")
RIGORS = ("light", "normal", "thorough")


def rigor(task_id: str | None = None, root: Path | None = None) -> str:
    """Recorded level, else the highest per-path default; most literal glob wins, ties take higher rigor."""
    import task_record
    root = Path(root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    directory = Path(os.environ.get("HARNESS_TASK_RECORD_DIR") or root / ".claude/logs/tasks")
    if task_id:
        record = task_record._hook_state.read_json_salvage(str(directory / (task_id + ".json")))
    else:
        session = os.environ.get("CLAUDE_CODE_SESSION_ID")
        records = [task_record._hook_state.read_json_salvage(str(p)) for p in directory.glob("*.json")]
        records = [r for r in records if session and task_record._session_key(r.get("session_id"))
                   == task_record._session_key(session)]
        record = max(records, key=lambda r: r.get("updated") or "", default={})
    if record.get("rigor") is not None:
        if record["rigor"] not in RIGORS:
            raise ValueError("unknown rigor %r" % record["rigor"])
        return record["rigor"]
    paths = ((record.get("triage") or {}).get("paths")
             or (record.get("scope") or {}).get("files") or [])
    if not paths:
        output = _git_commit.run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], str(root))
        if output is None:
            raise OSError("cannot read changed paths for rigor")
        rows = iter(output.split("\0"))
        paths = []
        for row in rows:
            if not row:
                continue
            paths.append(row[3:])
            if "R" in row[:2] or "C" in row[:2]:
                next(rows, None)
    defaults = adaptation.get(root / ".claude", "rigor_defaults")
    levels = []
    for path in paths:
        matches = []
        for project_class, entry in defaults.items():
            if (not isinstance(entry, dict) or entry.get("level") not in RIGORS
                    or not isinstance(entry.get("paths"), list)
                    or any(not isinstance(p, str) for p in entry["paths"])):
                raise ValueError("invalid rigor default for %s" % project_class)
            for pattern in entry['paths']:
                if glob_regex(pattern).fullmatch(path.replace('\\', '/')):
                    specificity = len(pattern.replace('*', '').replace('?', ''))
                    matches.append((specificity, RIGORS.index(entry['level'])))
        levels.append(RIGORS[max(matches)[1]] if matches else 'normal')
    return max(levels or ["normal"], key=RIGORS.index)


def admits(check: str, fields: Fields, rigor: str, *, gate_green: bool | None = None,
           task_id: str | None = None) -> Admission:
    """Scaled admission, owned by execution_depth's Admission table. Invalid inputs are errors."""
    for name, value, allowed in (("check", check, CHECKS), ("rigor", rigor, RIGORS),
                                 ("reach", fields.reach, REACH_ORDER),
                                 ("kind", fields.kind, OPTIONAL_FIELDS["kind"]),
                                 ("size", fields.size, FIELDS["size"])):
        if value not in allowed:
            raise ValueError("unknown %s %r" % (name, value))
    if gate_green is not None and not isinstance(gate_green, bool):
        raise ValueError("gate_green must be True, False or None")
    decision = fields.kind == "decision"
    small = fields.size == "one-context"
    if check == "harness-quality-seat":
        admitted = decision and (rigor == "thorough" or fields.reach == "standing"
                                 or (not small and rigor == "normal" and REACH_RANK[fields.reach] >= REACH_RANK["shared"]))
        adm = Admission(admitted, None, "decision at the size/rigor reach threshold" if admitted
                        else "mechanical or below the size/rigor reach threshold")
    elif check == "candidate-lenses":
        if rigor == "thorough":
            width, reason = "all", "thorough runs the full candidate roster"
        elif not decision and gate_green is True and (small or fields.reach == "local"):
            width, reason = "none", "mechanical change under a green gate"
        else:
            width, reason = "sized", "the session sizes the lenses from the change against the %s bar" % rigor
        adm = Admission(width != "none", width, reason)
    elif check == "premises":
        from harness_edit_skill_reminder import is_doctrine_path
        admitted = decision and any(is_doctrine_path(path) for path in fields.paths)
        adm = Admission(admitted, None, "decision doctrine path; claims determine the block contents" if admitted
                        else "mechanical or no doctrine path")
    else:
        adm = Admission(decision, None, "decision edit" if decision else "mechanical edit")
    if task_id is not None:
        import task_record
        task_record.record_admission(task_id, {"check": check, "admitted": adm.admitted, "width": adm.width,
                                               "rigor": rigor, "reach": fields.reach, "kind": fields.kind,
                                               "size": fields.size})
    return adm


@dataclass
class Raise:
    field: str
    recorded: object
    derived: str
    tags: list


# --- registry -----------------------------------------------------------------------------------------------

def glob_regex(pattern):
    """Regex for a repo-relative POSIX glob: `**` crosses `/`, `*` and `?` do not."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _regex(entry, key):
    """(compiled or None, reason or None) for an optional regex key."""
    if key not in entry:
        return None, None
    value = entry[key]
    if not _text(value):
        return None, "%s must be a non-empty regex string" % key
    try:
        return re.compile(value, re.MULTILINE), None
    except re.error as exc:
        return None, "%s is not a valid regex (%s)" % (key, exc)


def _validate(entry, seen):
    """(Tag, None) or (None, reason)."""
    if not isinstance(entry, dict):
        return None, "entry is not an object"
    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        return None, "unknown key %s" % ", ".join(unknown)
    tag_id = entry.get("id")
    if not _text(tag_id) or not _ID.match(tag_id):
        return None, "id %r is not kebab-case" % (tag_id,) if tag_id is not None else "id is missing (kebab-case)"
    if tag_id in seen:
        return None, "duplicate id %s" % tag_id
    paths = entry.get("paths")
    if not isinstance(paths, list) or not paths or not all(_text(p) for p in paths):
        return None, "paths must be a non-empty list of glob strings"
    hunk, reason = _regex(entry, "hunk")
    if reason:
        return None, reason
    file_re, reason = _regex(entry, "file")
    if reason:
        return None, reason
    attributes = entry.get("file_attributes", [])
    if "file_attributes" in entry and (not isinstance(attributes, list) or not attributes
            or any(not isinstance(a, str) or not re.fullmatch(r"[A-Za-z_]\w*", a) for a in attributes)):
        return None, "file_attributes must list C# attribute names"
    status = entry.get("status", "")
    if "status" in entry and (not _text(status) or not set(status) <= _STATUS_LETTERS):
        return None, "status must be git status letters from %s" % "".join(sorted(_STATUS_LETTERS))
    sets = entry.get("sets")
    if not isinstance(sets, dict) or not sets:
        return None, "sets must be an object naming reach and/or oracle"
    bad = sorted(set(sets) - set(_SET_KEYS))
    if bad:
        return None, "sets names unknown key %s (allowed: reach, oracle)" % ", ".join(bad)
    if "reach" in sets and sets["reach"] not in ("shared", "external"):
        return None, "sets.reach must be shared or external, not %r" % (sets["reach"],)
    if "oracle" in sets and sets["oracle"] not in FIELDS["oracle"]:
        return None, "sets.oracle must be one of %s, not %r" % ("|".join(FIELDS["oracle"]), sets["oracle"])
    acceptance = entry.get("acceptance", [])
    if "acceptance" in entry and (not isinstance(acceptance, list) or not acceptance
                                 or any(a not in OWNER_ACCEPTANCE for a in acceptance)):
        return None, "acceptance must list changed subjective observables or owner-criterion"
    if sets.get("oracle") == "owner" and not acceptance:
        return None, "an owner oracle needs an acceptance predicate, not directory membership"
    probe = entry.get("probe", "")
    if sets.get("oracle") == "runtime" and not _text(probe):
        return None, "a runtime oracle needs a probe: what run shows the defect class"
    if "probe" in entry and not _text(probe):
        return None, "probe must be a non-empty string"
    validated = entry.get("probe_validated")
    if validated is not None and not _text(validated):
        return None, "probe_validated must be null or the run that showed the probe sees the defect class"
    if not _text(entry.get("source")):
        return None, "source is required: the design or skill line the tag comes from"
    tag = Tag(tag_id, list(paths), dict(sets), entry["source"], hunk, file_re, status, probe or "", validated,
              [glob_regex(p) for p in paths], tuple(acceptance), tuple(attributes))
    return tag, None


def load_tags(claude_dir):
    """The registry entries, validated: `valid` Tag objects and `invalid` [(entry_index, reason)]. An invalid
    entry never matches."""
    entries = adaptation.get(claude_dir, "sensitivity_tags") or []
    valid, invalid, seen = [], [], set()
    for index, entry in enumerate(entries):
        tag, reason = _validate(entry, seen)
        if tag is None:
            invalid.append((index, reason))
        else:
            seen.add(tag.id)
            valid.append(tag)
    return Tags(valid, invalid)


# --- derivation ---------------------------------------------------------------------------------------------

_CS_NON_CODE = re.compile(r'//[^\n]*|/\*.*?\*/|"{3,}.*?"{3,}|@"(?:[^"]|"")*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_ATTRIBUTE_NAME = re.compile(r"\s*(?:[A-Za-z_]\w*\s*:\s*)?(?:global::)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:\(.*\))?\s*$", re.S)


def csharp_attributes(text):
    """Names from C# attribute lists, ignoring comments, strings and commas inside arguments."""
    code = _CS_NON_CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    names = set()
    for opening in re.finditer(r"\[", code):
        previous = opening.start() - 1
        while previous >= 0 and code[previous].isspace():
            previous -= 1
        if previous >= 0 and code[previous] not in ";{}]":
            continue
        depth, end = 1, opening.end()
        while end < len(code) and depth:
            depth += (code[end] == "[") - (code[end] == "]")
            end += 1
        if depth:
            continue
        body = code[opening.end():end - 1]
        parts, start, nesting = [], 0, 0
        for index, char in enumerate(body):
            if char in "([{":
                nesting += 1
            elif char in ")]}":
                nesting -= 1
            elif char == "," and nesting == 0:
                parts.append(body[start:index])
                start = index + 1
        parts.append(body[start:])
        matches = [_ATTRIBUTE_NAME.fullmatch(part) for part in parts]
        if not all(matches):
            continue
        for match in matches:
            name = match.group(1).rsplit(".", 1)[-1]
            names.add(name.removesuffix("Attribute"))
    return names


def _conditions(tag):
    parts = []
    if tag.hunk is not None:
        parts.append("hunk ~ /%s/" % tag.hunk.pattern)
    if tag.file is not None:
        parts.append("file ~ /%s/" % tag.file.pattern)
    if tag.file_attributes:
        parts.append("C# attribute in %s" % "/".join(tag.file_attributes))
    if tag.status:
        parts.append("status in %s" % tag.status)
    if tag.acceptance:
        parts.append("changed acceptance in %s" % "/".join(tag.acceptance))
    return parts


def _holds(tag, change, acceptance=None):
    if tag.acceptance and acceptance not in tag.acceptance:
        return False
    if tag.status and (not change.status or change.status not in tag.status):
        return False
    if tag.hunk is not None and not (change.hunk is not None and tag.hunk.search(change.hunk)):
        return False
    if tag.file is not None and not (change.file_text is not None and tag.file.search(change.file_text)):
        return False
    if tag.file_attributes and not (change.file_text is not None and
                                   set(tag.file_attributes) & csharp_attributes(change.file_text)):
        return False
    return True


def derive(changes, tags, root=None, *, acceptance=None):
    """Match every change against valid tags. Acceptance is the caller's judgment of changed subjective,
    player-facing criteria, never inferred from a path or a field name. Missing acceptance stays conditional.
    Reach takes the highest matched value; oracle holds every value matched tags set."""
    if acceptance is not None and acceptance not in OWNER_ACCEPTANCE:
        raise ValueError("unknown acceptance %r" % acceptance)
    matches = []
    standing = set()
    if root is not None:
        import load_census
        repo = Path(root).resolve()
        for row in load_census.standing_census(repo / ".claude")["rows"]:
            try:
                standing.add(Path(row["path"]).resolve().relative_to(repo).as_posix())
            except ValueError:
                continue
    reach, oracle = "local", set()
    for change in changes:
        if change.path in standing:
            matches.append(Match("standing", change.path, {"reach": "standing"}, "", None, False, "standing census"))
            if REACH_RANK[reach] < REACH_RANK["standing"]:
                reach = "standing"
        for tag in tags.valid:
            if not any(g.match(change.path) for g in tag._globs):
                continue
            conditions = _conditions(tag)
            if (change.planned and (tag.hunk is not None or tag.file is not None or tag.file_attributes or tag.status)
                    or tag.acceptance and acceptance is None):
                conditional, why = True, "applies if " + " and ".join(conditions)
            elif _holds(tag, change, acceptance):
                conditional, why = False, " and ".join(conditions) or "path"
            else:
                continue
            matches.append(Match(tag.id, change.path, dict(tag.sets), tag.probe, tag.probe_validated,
                                 conditional, why))
            if conditional:
                continue
            if REACH_RANK.get(tag.sets.get("reach"), 0) > REACH_RANK[reach]:
                reach = tag.sets["reach"]
            if "oracle" in tag.sets:
                oracle.add(tag.sets["oracle"])
    return Result(reach, frozenset(oracle), matches, list(tags.invalid))


def planned_changes(paths):
    """Changes for paths not yet written: no status, hunk or post-image."""
    cleaned = [str(p).replace("\\", "/") for p in paths]
    return [Change(p[2:] if p.startswith("./") else p) for p in cleaned]


def domain_keys(paths, claude_dir):
    """The tag names `derive` reports over planned `paths`, or `["untagged"]` when it reports none: the one
    domain function an escape row and a review yield row both key on."""
    try:
        result = derive(planned_changes(paths), load_tags(claude_dir))
    except OSError:
        return [UNTAGGED]
    return sorted({match.tag for match in result.matches}) or [UNTAGGED]


# --- changes from git ---------------------------------------------------------------------------------------

def _name_status(out):
    """[(status letter, path)] from `git diff --name-status -z`; a rename or copy keeps its new path."""
    tokens = out.split("\0")
    found, i = [], 0
    while i < len(tokens) and tokens[i]:
        letter = tokens[i][0]
        if letter in "RC" and i + 2 < len(tokens):
            found.append((letter, tokens[i + 2].replace("\\", "/")))
            i += 3
        else:
            found.append((letter, tokens[i + 1].replace("\\", "/")))
            i += 2
    return found


def _split_patch(out):
    """{path: added and removed lines, marker stripped} from a `-U0` patch; context and headers excluded."""
    hunks, path, lines, in_hunk = {}, None, [], False

    def flush():
        if path is not None:
            hunks[path] = "".join(lines)

    for raw in out.split("\n"):
        if raw.startswith("diff --git "):
            flush()
            rest = raw[len("diff --git "):]
            half = (len(rest) - 1) // 2
            path = rest[half + 3:] if rest[:half].startswith("a/") and rest[half + 1:].startswith("b/") else None
            lines, in_hunk = [], False
        elif not in_hunk and raw.startswith("rename to "):
            path = raw[len("rename to "):]
        elif not in_hunk and raw.startswith("+++ b/"):
            path = raw[len("+++ b/"):].split("\t")[0]
        elif not in_hunk and raw.startswith("--- a/") and path is None:
            path = raw[len("--- a/"):].split("\t")[0]
        elif raw.startswith("@@"):
            in_hunk = True
        elif in_hunk and raw[:1] in ("+", "-"):
            lines.append(raw[1:] + "\n")
    flush()
    return hunks


def _changes(statuses, read_patch, read_post_image):
    """Changes for `statuses`, with one shared patch read on the first hunk any of them needs. `read_patch`
    returns the patch text or None; a None raises OSError, since an unread patch is not an empty one."""
    cache = {}

    def load_hunk(path):
        if "all" not in cache:
            out = read_patch()
            if out is None:
                raise OSError("git patch read failed")
            cache["all"] = _split_patch(out)
        return cache["all"].get(path, "")

    return [Change(path, letter, load_hunk=load_hunk, load_file=lambda p: read_post_image(p))
            for letter, path in statuses]


def _toplevel(repo, git_env):
    out = _git_commit.run_git(["rev-parse", "--show-toplevel"], repo, git_env)
    return out.strip() if out else repo


def changes_from_index(repo, paths, git_env, rest):
    """The content the commit `git commit <rest>` publishes: the index, or the working tree for a pathspec or
    `-a` commit (the same rule `staged_paths` applies), limited to `paths`. Raises OSError when git cannot
    read the diff."""
    wanted = {str(p).replace("\\", "/") for p in paths}
    source = _git_commit.commit_source(rest, repo, git_env)
    if source is None:
        raise OSError("a -i commit that mixes index and working-tree content")
    worktree = source == "worktree"
    if worktree:
        base = ["HEAD"]
    elif _git_commit.parse_commit_args(rest)[1]:
        parent = _git_commit.run_git(["rev-parse", "-q", "--verify", "HEAD~1"], repo, git_env)
        base = ["--cached", parent.strip() if parent else _EMPTY_TREE]
    else:
        base = ["--cached"]
    listing = _git_commit.run_git(["-c", "core.quotepath=false", "diff", "-M", "--name-status", "-z"] + base,
                                  repo, git_env)
    if listing is None:
        raise OSError("git diff --name-status %s" % " ".join(base))
    statuses = [(letter, path) for letter, path in _name_status(listing) if path in wanted]
    top = _toplevel(repo, git_env)

    def read_post_image(path):
        if worktree:
            try:
                with open(os.path.join(top, *path.split("/")), encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except OSError:
                return None
        return _git_commit.run_git(["show", ":" + path], repo, git_env)

    patch_args = ["diff", "-U0", "-M", "--no-color", "--no-ext-diff"] + base
    return _changes(statuses, lambda: _git_commit.run_git(["-c", "core.quotepath=false"] + patch_args, repo, git_env),
                    read_post_image)


def changes_from_commit(repo, sha):
    """The content a landed commit published: its diff against its first parent (a root commit against the
    empty tree). A merge commit's diff is its first-parent diff."""
    parent = _git_commit.run_git(["rev-parse", "-q", "--verify", sha + "^"], repo)
    base = [parent.strip() if parent else _EMPTY_TREE, sha]
    listing = _git_commit.run_git(["-c", "core.quotepath=false", "diff", "-M", "--name-status", "-z"] + base, repo)
    if listing is None:
        raise OSError("git diff %s" % " ".join(base))
    patch_args = ["-c", "core.quotepath=false", "diff", "-U0", "-M", "--no-color", "--no-ext-diff"] + base
    return _changes(_name_status(listing), lambda: _git_commit.run_git(patch_args, repo),
                    lambda path: _git_commit.run_git(["show", "%s:%s" % (sha, path)], repo))


# --- trailer ------------------------------------------------------------------------------------------------

_TRAILER_LINE = re.compile(r"^[ \t]*Triage:(.*)$", re.MULTILINE)


def parse_oracle(text):
    """The sorted members of a `+`-joined oracle value, or none for `none` (the token `_format_oracle` prints
    for an empty set); ValueError naming the first unknown member."""
    if text == "none":
        return []
    members = (text or "").split("+")
    bad = [m for m in members if m not in FIELDS["oracle"]]
    if bad:
        raise ValueError("unknown value %s for oracle (allowed: %s)" % (bad[0] or "''", "|".join(FIELDS["oracle"])))
    return sorted(set(members))


def parse_trailer(message):
    """(fields, None) for a valid trailer, (None, reason) for an unreadable one, (None, None) when the
    message has no `Triage:` line. The last such line counts. `oracle` parses to a frozenset."""
    found = _TRAILER_LINE.findall(message or "")
    if not found:
        return None, None
    fields = {}
    for token in found[-1].split():
        key, sep, value = token.partition("=")
        if not sep or not value:
            return None, "%r is not key=value" % token
        if key not in FIELDS and key not in OPTIONAL_FIELDS:
            return None, "unknown key %s" % key
        if key in fields:
            return None, "key %s appears twice" % key
        if key == "oracle":
            try:
                fields[key] = frozenset(parse_oracle(value))
            except ValueError as exc:
                return None, str(exc)
        elif RETIRED.get(key, {}).get(value, value) in (FIELDS | OPTIONAL_FIELDS)[key]:
            fields[key] = RETIRED.get(key, {}).get(value, value)
        else:
            return None, "unknown value %s for %s (allowed: %s)" % (value, key, "|".join((FIELDS | OPTIONAL_FIELDS)[key]))
    missing = [key for key in FIELDS if key != "reversibility" and key not in fields]
    if missing:
        return None, "missing key %s" % ", ".join(missing)
    return fields, None


def raises(fields, res):
    """Each place the derived result sits above the recorded fields: one Raise for reach, one per oracle value
    the record lacks. Only unconditional matches count; other fields are not path-derivable."""
    found = []
    live = [m for m in res.matches if not m.conditional]
    if REACH_RANK[res.reach] > REACH_RANK.get(fields.get("reach"), -1):
        found.append(Raise("reach", fields.get("reach"), res.reach,
                           [m for m in live if m.sets.get("reach") == res.reach]))
    recorded = frozenset(fields.get("oracle") or ())
    for value in sorted(res.oracle - recorded):
        found.append(Raise("oracle", recorded, value, [m for m in live if m.sets.get("oracle") == value]))
    return found


def describe(raise_):
    """One line for a raise: the field, what it moves from and to, the first tag and its probe."""
    first = raise_.tags[0] if raise_.tags else None
    at = ": tag %s (%s)" % (first.tag, first.path) if first else ""
    if raise_.field == "reach":
        return "reach %s -> %s%s" % (raise_.recorded, raise_.derived, at)
    probe = ""
    if first and first.probe:
        probe = "; probe: %s%s" % (first.probe, "" if first.probe_validated else " [unvalidated]")
    return "oracle adds %s%s%s" % (raise_.derived, at, probe)


# --- rederive -----------------------------------------------------------------------------------------------

def _task_triage(repo, sha):
    """Newest task naming the full sha, using escape_log's textual commit-link convention.

    Task records still require all six fields. Bad evidence is diagnostic, not a guessed triage.
    """
    directory = Path(os.environ.get("HARNESS_TASK_RECORD_DIR") or
                     Path(repo) / ".claude" / "logs" / "tasks")
    try:
        paths = sorted(directory.glob("*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    except OSError as exc:
        print("%s task triage unavailable: %s" % (sha[:9], exc), file=sys.stderr)
        return None
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print("%s task triage unavailable at %s: %s" % (sha[:9], path, exc), file=sys.stderr)
            continue
        if sha not in text:
            continue
        try:
            record = json.loads(text)
            fields = record["triage"]["fields"]
            if not isinstance(fields, dict) or not set(FIELDS) <= set(fields) or set(fields) - set(FIELDS | OPTIONAL_FIELDS):
                raise ValueError("task triage requires all six fields")
            tokens = []
            for key, value in fields.items():
                if key == "oracle":
                    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                        raise ValueError("task oracle must be a list of strings")
                    value = _format_oracle(value)
                if not isinstance(value, str):
                    raise ValueError("task field %s must be text" % key)
                tokens.append("%s=%s" % (key, value))
            parsed, error = parse_trailer("Triage: " + " ".join(tokens))
            if error:
                raise ValueError(error)
            return parsed
        except (ValueError, KeyError, TypeError) as exc:
            print("%s malformed task triage at %s: %s" % (sha[:9], path, exc), file=sys.stderr)
            return None
    return None


def rederive(repo, rev_range, tags):
    """[(sha, [Raise])] for recorded triage below its diff's floor.

    A readable trailer wins; otherwise use the newest commit-linked task record.
    Malformed trailers/records remain visible on stderr.
    """
    shas = _git_commit.run_git(["rev-list", "--reverse", rev_range], repo)
    if shas is None:
        raise OSError("git rev-list %s" % rev_range)
    flagged = []
    for sha in shas.split():
        message = _git_commit.run_git(["log", "-1", "--format=%B", sha], repo) or ""
        fields, error = parse_trailer(message)
        if error:
            print("%s malformed Triage trailer: %s" % (sha[:9], error), file=sys.stderr)
        if fields is None:
            fields = _task_triage(repo, sha)
        if fields is None:
            continue
        found = raises(fields, derive(changes_from_commit(repo, sha), tags, root=repo))
        if found:
            flagged.append((sha, found))
    return flagged


def current(root: Path, *, rev: str | None = None, staged: bool = False,
            task_id: str | None = None) -> Fields:
    """Trailer, then task triage, then derived fields. The diff can only raise reach and oracle."""
    import task_record
    root = Path(root).resolve()
    recorded = None
    if rev:
        sha = _git_commit.run_git(["rev-parse", "--verify", rev + "^{commit}"], str(root))
        if not sha:
            raise OSError("cannot resolve commit %s" % rev)
        sha = sha.strip()
        message = _git_commit.run_git(["log", "-1", "--format=%B", sha], str(root))
        if message is None:
            raise OSError("cannot read commit %s" % sha)
        recorded, error = parse_trailer(message)
        if error:
            raise ValueError("Triage: " + error)
        if recorded is None:
            recorded = _task_triage(root, sha)
        changes = changes_from_commit(str(root), sha)
    elif staged:
        paths, error = _git_commit.staged_paths([], str(root))
        if paths is None:
            raise OSError("cannot read index: %s" % error)
        changes = changes_from_index(str(root), paths, {}, [])
    else:
        changes = []
    if recorded is None and (task_id or not rev):
        directory = Path(os.environ.get("HARNESS_TASK_RECORD_DIR") or root / ".claude/logs/tasks")
        if task_id:
            record = task_record._hook_state.read_json_salvage(str(directory / (task_id + ".json")))
        else:
            session = os.environ.get("CLAUDE_CODE_SESSION_ID")
            records = [task_record._hook_state.read_json_salvage(str(p)) for p in directory.glob("*.json")]
            records = [r for r in records if session and task_record._session_key(r.get("session_id"))
                       == task_record._session_key(session) and r.get("triage")]
            record = max(records, key=lambda r: r.get("updated") or "", default={})
        recorded = (record.get("triage") or {}).get("fields")
        if not rev and not staged:
            changes = planned_changes((record.get("triage") or {}).get("paths") or [])
    res = derive(changes, load_tags(root / ".claude"), root=root)
    recorded = recorded or {}
    reach = max((recorded.get("reach", "local"), res.reach), key=REACH_RANK.__getitem__)
    return Fields(reach, frozenset(recorded.get("oracle") or ()) | res.oracle,
                  recorded.get("kind", "decision"), recorded.get("size", "milestones"),
                  tuple(c.path for c in changes))


def sentences(root: Path, *, rev: str | None = None, staged: bool = False) -> list:
    """Added and removed doctrine sentences, using harness_growth_guard's shared sentence_diff."""
    import sentence_diff
    root = str(root)
    if rev:
        changes = changes_from_commit(root, rev)
        parent = _git_commit.run_git(["rev-parse", "-q", "--verify", rev + "^"], root)
        before_ref = parent.strip() if parent else _EMPTY_TREE
    elif staged:
        paths, error = _git_commit.staged_paths([], root)
        if paths is None:
            raise OSError("cannot read index: %s" % error)
        changes = changes_from_index(root, paths, {}, [])
        before_ref = "HEAD"
    else:
        raise ValueError("sentences needs --staged or --rev")
    rows = []
    for change in changes:
        if not sentence_diff.in_doctrine_scope(change.path):
            continue
        before = _git_commit.run_git(["show", before_ref + ":" + change.path], root) or ""
        after = change.file_text or ""
        rows.append({"path": change.path,
                     "added": [s for s, _ in sentence_diff.removed(after, before)],
                     "removed": [s for s, _ in sentence_diff.removed(before, after)]})
    return rows


# --- CLI ----------------------------------------------------------------------------------------------------

def _format_oracle(values):
    return "+".join(sorted(set(values))) or "none"


def _cmd_lookup(args, tags):
    res = derive(planned_changes(args.paths), tags, root=Path(args.claude_dir).resolve().parent,
                 acceptance=args.acceptance)
    for m in res.matches:
        sets = " ".join("%s=%s" % kv for kv in sorted(m.sets.items()))
        line = "%s: tag %s sets %s" % (m.path, m.tag, sets)
        if m.conditional:
            line += " (%s)" % m.why
        if m.probe:
            line += "; probe: %s%s" % (m.probe, "" if m.probe_validated else " [unvalidated]")
        print(line)
    for index, reason in res.invalid:
        print("invalid entry %d: %s" % (index, reason), file=sys.stderr)
    print("derived: reach=%s oracle=%s" % (res.reach, _format_oracle(res.oracle)))
    return 0


def _cmd_check(_args, tags):
    for index, reason in tags.invalid:
        print("entry %d: %s" % (index, reason))
    if tags.invalid:
        return 1
    print("registry ok: %d tags" % len(tags.valid))
    return 0


def _cmd_rederive(args, tags):
    try:
        flagged = rederive(args.repo, args.rev_range, tags)
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    for sha, found in flagged:
        for r in found:
            print("%s %s" % (sha[:9], describe(r)))
    return 1 if flagged else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Work triage lookup.", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude-dir", default=os.path.normpath(os.path.join(HERE, "..")),
                    help="the .claude directory whose adaptation.json holds the registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lookup", help="derive reach and oracle over planned paths")
    p.add_argument("paths", nargs="+")
    p.add_argument("--acceptance", choices=OWNER_ACCEPTANCE,
                   help="changed subjective player-facing acceptance; omit for contract-only work")
    sub.add_parser("check-registry", help="validate every registry entry")
    p = sub.add_parser("rederive", help="flag commits whose trailer sits below their diff")
    p.add_argument("rev_range")
    p.add_argument("--repo", default=".")
    p = sub.add_parser("sentences", help="print added and removed doctrine sentences")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--staged", action="store_true")
    source.add_argument("--rev")
    p.add_argument("--repo", default=".")
    p = sub.add_parser("admits", help="compute a scaled check's admission")
    p.add_argument("check", choices=CHECKS)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--staged", action="store_true")
    source.add_argument("--rev")
    source.add_argument("--task")
    p.add_argument("--json", action="store_true")
    p.add_argument("--gate-green", action=argparse.BooleanOptionalAction, default=None)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if args.cmd == "admits":
        import task_record
        try:
            root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
            fields = current(root, rev=args.rev, staged=args.staged, task_id=args.task)
            adm = admits(args.check, fields, rigor(args.task, root), gate_green=args.gate_green, task_id=args.task)
        except (OSError, ValueError, KeyError, task_record.RecordTooLarge) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(asdict(adm)))
        else:
            print("%s: %s%s — %s" % (args.check, "admitted" if adm.admitted else "not admitted",
                                     " width=" + adm.width if adm.width else "", adm.reason))
        return 0
    if args.cmd == "sentences":
        for row in sentences(Path(args.repo), rev=args.rev, staged=args.staged):
            print(row["path"])
            for key, marker in (("removed", "-"), ("added", "+")):
                for sentence in row[key]:
                    print("%s %s" % (marker, sentence))
        return 0
    tags = load_tags(args.claude_dir)
    return {"lookup": _cmd_lookup, "check-registry": _cmd_check, "rederive": _cmd_rederive}[args.cmd](args, tags)


if __name__ == "__main__":
    sys.exit(main())
