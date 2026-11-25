#!/usr/bin/env python3
"""Guard against Export VALUE LOSS in .tres / .tscn files -- two shapes, one C# default index.

Shape 1 -- null-strip (`+Field = null` ADDED). An editor resave of a scene whose script
grew a new [Export] writes previously-omitted Exports as explicit `Field = null`; on load
null coerces to type-zero, not the C# field-init default, so a MEANINGFUL default
(`= true`, `MaxSlots = 24`, `NodePath _wizardSMPath = "WizardSM"`) is lost with a green
build. (Incidents: 9b16d361; 240f9d2c/cf7d46ef wizard.tscn -> GetNode(empty) NRE.)

Shape 2 -- vanished line (`-Field = value` REMOVED, no replacement). Since 4.7.1 the text
saver OMITS every script property equal to its C# default (resource_format_text.cpp,
`PropertyUtils::get_property_default_value`), so a resave legitimately deletes
`StrengthRequired = 1` when the C# default is 1 -- a diff that reads exactly like data
loss. Compare the removed value against the C# default: equal -> fixed-point rewrite,
ignore; different -> authored value LOST (occurrence #9, 2026-08-30, was read as a strip
and was an omission). Unparseable values (enum / struct initializers) report as
UNVERIFIED -- warn, never deny.

The C# index maps every tracked `[Export]` name (Jmodot submodule included) to the SET
of initializers across classes: `Value` exists on many classes, so a removed value is
benign if it matches ANY class's default for that name (conservative against false
denials). Both shapes are diff-scoped -- they catch the NEXT strip, never committed state.

Modes:
    tres_nullstrip_guard.py              # scan staged (git diff --cached); exit 1 on strip
    tres_nullstrip_guard.py --range A..B # scan a commit range (CI / PR review); exit 1 on strip
    tres_nullstrip_guard.py --worktree   # working tree vs HEAD (uncommitted authoring); report, exit 0
    tres_nullstrip_guard.py --hook       # PreToolUse: deny a `git commit` that strips a default

Escape hatch (a field genuinely made nullable, or its C# default deliberately changed):
set HARNESS_ALLOW_TRES_NULLSTRIP=1 in the environment.

Commit scope is `hooks/_commit_scope.py`: it names the invocations that publish the index and the
paths a merge authors.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _git_run  # noqa: E402
from _git_commit import commit_invocations, bypass_declared, run_git  # noqa: E402
from _git_run import git_environ  # noqa: E402
from _commit_scope import incoming_heads, publication, publishes_index  # noqa: E402
from _checkout_root import repo_arg  # noqa: E402
from _hook_parts import bounded_decision, bounded_report  # noqa: E402

# Windows pipes default to cp1252 through Python 3.14; git diff text now decodes as real
# UTF-8, so a non-ASCII .tres string payload or path would raise UnicodeEncodeError on write.
# LF-only: Windows text mode adds a CR per line, which the cap's byte count does not see.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stderr.reconfigure(encoding="utf-8", newline="\n")

# An added scene line `+Field = null` -- Field is a leading-Uppercase Export or a
# leading-underscore private Export (this project's `[Export] private _field` form).
ADDED_NULL = re.compile(r"^\+(_?[A-Za-z][A-Za-z0-9_]*)\s*=\s*null\s*$")
# Any property assignment line, either diff side. Engine keys are never [Export]s.
PROP_LINE = re.compile(r"^([-+])(_?[A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
ENGINE_KEYS = {"script", "resource_name", "resource_local_to_scene"}
FILE_HDR = re.compile(r"^\+\+\+ b/(.*)$")

# The commit's git environment, set by hook() from the invocation it judges: the staged diff is read
# from the index that commit publishes (`GIT_INDEX_FILE=<f> git commit`). None inherits.
_GIT_ENV = None

# C# [Export] property with an initializer: `[Export...] <mods> Type Name { get; set; } = DEFAULT;`
CS_PROP = re.compile(
    r"\[Export\b[^\]]*\]"
    r"(?:\s*\[[^\]]*\])*"
    r"\s*(?:public|private|protected|internal|static|virtual|override|sealed|\s)+"
    r"[\w<>\?,\.\[\]]+\s+"
    r"(_?\w+)\s*"
    r"\{[^{}]*\}\s*"
    r"=\s*([^;]+);"
)
# C# [Export] field with an initializer: `[Export...] <mods> Type Name = DEFAULT;`
CS_FIELD = re.compile(
    r"\[Export\b[^\]]*\]"
    r"(?:\s*\[[^\]]*\])*"
    r"\s*(?:public|private|protected|internal|static|readonly|\s)+"
    r"[\w<>\?,\.\[\]]+\s+"
    r"(_?\w+)\s*"
    r"=\s*([^;{]+);"
)
# C# [Export] with NO initializer (implicit type-zero default): `... Name { get; set; }` then
# no `=`, or a bare field `... Name;`.
CS_NOINIT = re.compile(
    r"\[Export\b[^\]]*\]"
    r"(?:\s*\[[^\]]*\])*"
    r"\s*(?:public|private|protected|internal|static|virtual|override|sealed|readonly|\s)+"
    r"[\w<>\?,\.\[\]]+\s+"
    r"(_?\w+)\s*"
    r"(?:\{[^{}]*\}[ \t]*(?:\r?\n|$)|;)"
)

# Defaults that coerce to the same value `= null` loads as -> harmless, not a strip.
SAFE_DEFAULTS = {
    "null", "default", "false", '""', "string.empty",
    "0", "0f", "0d", "0m", "0u", "0l", "0ul", "0.0", "0.0f", "0.0d",
}

# Canonical tokens for value comparison.
EMPTY, NULL, UNKNOWN = "<EMPTY>", "<NULL>", None


def is_meaningful(default: str) -> bool:
    """True if losing this C# default (to a scene `= null`) changes behavior."""
    d = default.strip().rstrip(";").strip()
    # `null!` / `default!` -- the null-forgiving [RequiredExport] convention. The
    # C# default IS null (assigned in-editor); a scene `= null` loses nothing.
    d = d.rstrip("!").strip()
    dl = d.lower()
    if dl in SAFE_DEFAULTS:
        return False
    if dl.startswith("new"):      # collection / ref default -- Godot replaces on load
        return False
    if dl.startswith(">"):        # mis-captured expression-bodied member
        return False
    if dl.endswith(".zero"):      # Vector*.Zero etc -- null coerces to the same zero
        return False
    return True


def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def canon_cs(default: str):
    """Canonical form of a C# initializer, or UNKNOWN when it cannot be compared."""
    d = default.strip().rstrip(";").strip().rstrip("!").strip()
    dl = d.lower()
    if dl in ("null", "default"):
        return NULL
    if dl in ("true", "false"):
        return dl
    if dl.startswith("new") or dl in ("[]", "string.empty"):
        return EMPTY
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:e[-+]?\d+)?)[fdmul]*", dl)
    if m:
        return _num(m.group(1))
    if len(d) >= 2 and d[0] == '"' and d[-1] == '"':
        return d[1:-1]
    return UNKNOWN


def canon_tres(value: str):
    """Canonical form of a .tres/.tscn property literal, or UNKNOWN."""
    v = value.strip()
    vl = v.lower()
    if vl == "null":
        return NULL
    if vl in ("true", "false"):
        return vl
    if (re.fullmatch(r"(?:Array\[[^\]]*\]\()?\[\s*\]\)?", v)
            or re.fullmatch(r"(?:Dictionary\[[^\]]*\]\()?\{\s*\}\)?", v)
            or re.fullmatch(r"Packed\w+Array\(\s*\)", v)):
        return EMPTY
    n = _num(v)
    if n is not None:
        return n
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    if v.startswith('&"') and v.endswith('"'):
        return v[2:-1]
    return UNKNOWN


def diff_lines(range_arg, worktree=False):
    cmd = ["git", "diff", "-U0", "--no-color"]
    if worktree:
        cmd += ["HEAD"]
    elif range_arg:
        cmd += [range_arg]
    else:
        cmd += ["--cached"]
    cmd += ["--", "*.tres", "*.tscn"]
    return _git_run.run(cmd[1:], env=_GIT_ENV).text.splitlines()


def scan_diff(range_arg, worktree=False):
    """Return (added_nulls, vanished): added_nulls = [(path, field)] per added `Field = null`;
    vanished = [(path, field, old_value)] per removed `Field = value` whose file gains no
    `+Field = ...` line (the property is GONE, not changed)."""
    added_nulls = []
    removed = {}   # path -> {field: value}
    readded = {}   # path -> {field}
    current = None
    removed_node = False
    lines = diff_lines(range_arg, worktree)
    for index, line in enumerate(lines):
        line = line.rstrip("\r")
        if line.startswith(("@@", "diff --git", "+[", " [")):
            removed_node = False
        if line.startswith("-["):
            # A removed block is whole unless a block of its kind is added after it in the hunk
            # (a rename or re-id can hide a strip). A node re-added as an instance takes its
            # properties from the instanced scene, so its old lines were moved, not stripped.
            removed_node = True
            kind = line[2:].split(" ", 1)[0].rstrip("]")
            for following in lines[index + 1:]:
                if following.startswith(("@@", "diff --git")):
                    break
                if following.startswith("+[" + kind) and "instance=" not in following:
                    removed_node = False
                    break
        if removed_node and line.startswith("-"):
            continue
        hdr = FILE_HDR.match(line)
        if hdr:
            current = hdr.group(1)
            continue
        if line.startswith("+++ /dev/null"):
            current = None
            continue
        if line.startswith(("+++", "---")) or current is None:
            continue
        m = ADDED_NULL.match(line)
        if m:
            added_nulls.append((current, m.group(1)))
        p = PROP_LINE.match(line)
        if not p or p.group(2) in ENGINE_KEYS:
            continue
        sign, field, value = p.groups()
        if sign == "-":
            removed.setdefault(current, {})[field] = value
        else:
            readded.setdefault(current, set()).add(field)
    vanished = [(path, field, value)
                for path, fields in removed.items()
                for field, value in fields.items()
                if field not in readded.get(path, ())]
    return added_nulls, vanished


def export_index():
    """name -> {"defaults": set(initializer text), "noinit": bool} over every tracked C# [Export]."""
    idx = {}
    # --recurse-submodules: the Jmodot framework .cs live in a submodule, which a
    # plain `ls-files` reports as one gitlink -- missing 695 files of Export decls.
    files = _git_run.run(["ls-files", "--recurse-submodules", "*.cs"], env=_GIT_ENV).text.splitlines()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if "[Export" not in text:
            continue
        for rx in (CS_PROP, CS_FIELD):
            for name, default in rx.findall(text):
                idx.setdefault(name, {"defaults": set(), "noinit": False})["defaults"].add(default.strip())
        for name in CS_NOINIT.findall(text):
            idx.setdefault(name, {"defaults": set(), "noinit": False})["noinit"] = True
    return idx


def meaningful_export_names(idx=None):
    """Set of C# [Export] names whose declared default is meaningful."""
    idx = idx if idx is not None else export_index()
    return {n for n, e in idx.items() if any(is_meaningful(d) for d in e["defaults"])}


def classify_vanished(vanished, idx):
    """Split vanished lines into (lost, unverified). BENIGN when the removed value equals ANY
    class's C# default for that name (type-zero for a no-initializer Export); LOST when
    comparable and different from every default; UNVERIFIED otherwise. Names that are not
    an [Export] anywhere (renamed/removed in C#) are skipped."""
    lost, unverified = [], []
    for path, field, value in vanished:
        entry = idx.get(field)
        if entry is None:
            continue
        got = canon_tres(value)
        defaults = {canon_cs(d) for d in entry["defaults"]}
        if entry["noinit"]:
            defaults |= {0.0, "false", NULL, EMPTY, ""}
        if got is not UNKNOWN and got in defaults:
            continue
        if got is UNKNOWN or UNKNOWN in defaults:
            unverified.append((path, field, value))
            continue
        lost.append((path, field, value, sorted(str(d) for d in defaults)))
    return lost, unverified


class _BlobLines:
    """The stripped lines of each `ref:path` blob and the incoming refs, read once per hook run."""

    def __init__(self, repo, git_env):
        self.repo, self.git_env = repo, git_env
        self._heads = None
        self._lines = {}

    @property
    def heads(self):
        if self._heads is None:
            self._heads = incoming_heads(self.repo, self.git_env)
        return self._heads

    def count(self, ref, path, line):
        """How often `line` appears in `ref:path`, or None when that blob is unreadable."""
        key = (ref, path)
        if key not in self._lines:
            out = run_git(["show", "%s:%s" % (ref, path)], self.repo, self.git_env)
            self._lines[key] = None if out is None else [text.strip() for text in out.splitlines()]
        lines = self._lines[key]
        return None if lines is None else lines.count(line)


def _authored(findings, repo, git_env, blobs=None):
    """Keep only the rows whose path THIS commit wrote. A merge diffs the index against HEAD, so a
    parent's own .tres edit reads as this commit's; it was judged on that parent's ref. Outside a
    merge `authored_paths` returns every path, so an ordinary commit is judged as before."""
    blobs = blobs or _BlobLines(repo, git_env)
    published = publication("commit", (), repo, git_env)
    keep = set(published.authored(sorted({row[0] for row in findings})))
    return [row for row in findings if row[0] in keep and not _arrived(row, blobs)]


def _arrived(row, blobs):
    """True when a finding's line change came from the incoming parent: the staged file holds the
    line `Field = value` (or `Field = null`) exactly as often as MERGE_HEAD / CHERRY_PICK_HEAD /
    REVERT_HEAD does. A path both parents edited is authored as a whole, but a line the incoming
    side already removed or nulled was judged on that side's ref."""
    if not blobs.heads:
        return False
    path, field = row[0], row[1]
    line = "%s = %s" % (field, row[2] if len(row) > 2 else "null")
    staged = blobs.count("", path, line)
    return staged is not None and any(blobs.count(head, path, line) == staged for head in blobs.heads)


def find_strips(range_arg, worktree=False):
    """Return (null_strips, lost, unverified)."""
    added_nulls, vanished = scan_diff(range_arg, worktree)
    if not added_nulls and not vanished:
        return [], [], []
    idx = export_index()
    dangerous = meaningful_export_names(idx)
    null_strips = [(p, f) for (p, f) in added_nulls if f in dangerous]
    lost, unverified = classify_vanished(vanished, idx)
    return null_strips, lost, unverified


SCRATCH_DIR = Path(__file__).resolve().parents[1] / "scratch"


def scratch_list(session_id=""):
    """The whole-list file for this caller: one per session, so a peer session's rerun replaces only its own.
    A caller with no session id (the CLI scan) names its process instead of sharing one list."""
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in (session_id or "pid%d" % os.getpid()))
    return str(Path(SCRATCH_DIR) / ("tres_nullstrip_findings.%s.txt" % stem))
NULL_INSTRUCTION = (
    "Each of these Exports has a MEANINGFUL C# default (non-null/zero/false) that "
    "`= null` throws away -- the scene loads the type-zero / empty value instead "
    "(gotcha_godot_editor_resave_hazards). Set the explicit intended "
    "value (e.g. `MaxSlots = 24`, `ArtFacesRight = true`) -- do NOT delete the line, "
    "the editor re-strips it. If the field was genuinely made nullable (C# default "
    "now null), set HARNESS_ALLOW_TRES_NULLSTRIP=1."
)
LOST_INSTRUCTION = (
    "Re-add the line with its authored value (the editor keeps non-default values). "
    "If the C# default was deliberately changed to this value, set HARNESS_ALLOW_TRES_NULLSTRIP=1."
)


def report_parts(null_strips, lost, unverified):
    """`(lead, rows)` of the report, the verdict and instruction lines apart from the finding rows."""
    leads, rows = [], []
    if null_strips:
        leads.append(
            "[tres-nullstrip-guard] Suspected value-type Export null-strip "
            f"({len(null_strips)} line(s)):\n" + NULL_INSTRUCTION + "\n"
        )
        rows += [f"  {path}: {field} = null" for path, field in null_strips]
    if lost:
        leads.append(
            f"[tres-nullstrip-guard] Export VALUE LOST ({len(lost)} line(s)) -- the removed "
            "value differs from every C# default for that Export: data loss, NOT the 4.7.1 "
            "saver omitting a default-valued property:\n" + LOST_INSTRUCTION + "\n"
        )
        rows += [f"  {path}: -{field} = {value}   (C# default(s): {', '.join(defaults)})"
                 for path, field, value, defaults in lost]
    if unverified:
        leads.append(
            f"[tres-nullstrip-guard] {len(unverified)} vanished line(s) could not be compared to a "
            "C# default (enum / struct / expression initializer) -- verify by hand:\n"
        )
        rows += [f"  {path}: -{field} = {value}" for path, field, value in unverified]
    return "".join(leads), rows


def report(null_strips, lost, unverified, session_id=""):
    """The `--worktree` and CLI report, for the human or Bash call that reads stderr."""
    lead, rows = report_parts(null_strips, lost, unverified)
    if lead:
        sys.stderr.write(bounded_report(lead, rows, scratch_list(session_id)))


def standalone(range_arg, worktree=False):
    if os.environ.get("HARNESS_ALLOW_TRES_NULLSTRIP"):
        return 0
    null_strips, lost, unverified = find_strips(range_arg, worktree)
    if not null_strips and not lost and not unverified:
        return 0
    report(null_strips, lost, unverified)
    if worktree:
        return 0          # advisory scan of uncommitted authoring -- never blocks a session
    return 1 if (null_strips or lost) else 0


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    if data.get("tool_name") != "Bash":
        print("{}")
        return 0
    commits = [c for c in commit_invocations(data.get("tool_input", {}).get("command", ""),
                                             data.get("cwd") or ".") if publishes_index(c.sub, c.rest)]
    if not commits:
        print("{}")
        return 0
    commit = commits[-1]
    global _GIT_ENV
    _GIT_ENV = git_environ(commit.git_env)
    if bypass_declared(commit.inline_env, "HARNESS_ALLOW_TRES_NULLSTRIP"):
        print("{}")
        return 0
    try:
        os.chdir(commit.cwd)   # the scan below reads the repo the commit targets
    except OSError:
        pass

    null_strips, lost, unverified = find_strips(None)
    blobs = _BlobLines(commit.cwd, commit.git_env)
    null_strips = _authored(null_strips, commit.cwd, commit.git_env, blobs)
    lost = _authored(lost, commit.cwd, commit.git_env, blobs)
    unverified = _authored(unverified, commit.cwd, commit.git_env, blobs)
    if not null_strips and not lost:
        if unverified:
            lead, rows = report_parts([], [], unverified)
            sys.stdout.write(bounded_decision(lambda text: json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": text,
            }}) + "\n", lead, rows, scratch_list(data.get("session_id") or "")))
            return 0
        print("{}")
        return 0

    parts = []
    if null_strips:
        parts.append(f"{len(null_strips)} staged scene line(s) null-strip a value-type Export "
                     "with a meaningful C# default (loads type-zero, not the default -> silent "
                     "behavior break with a green build)")
    if lost:
        parts.append(f"{len(lost)} staged scene line(s) remove an Export whose value differs from "
                     "its C# default (authored value lost -- not a 4.7.1 default omission)")
    reason = ("Blocked (rules/godot_files.md): " + "; ".join(parts) +
              ". Set the explicit value, or set HARNESS_ALLOW_TRES_NULLSTRIP=1 if the C# default "
              "genuinely changed.")
    lead, rows = report_parts(null_strips, lost, unverified)
    sys.stdout.write(bounded_decision(lambda text: json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason + "\n" + text,
    }}) + "\n", lead, rows, scratch_list(data.get("session_id") or "")))
    return 0


def main():
    repo = repo_arg(sys.argv)
    if repo:
        os.chdir(repo)
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    if "--worktree" in args:
        return standalone(None, worktree=True)
    range_arg = args[1] if len(args) > 1 and args[0] == "--range" else None
    return standalone(range_arg)


if __name__ == "__main__":
    sys.exit(main())
