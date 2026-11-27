#!/usr/bin/env python3
"""Guard against Godot editor-resave stripping script bindings from custom-Resource .tres.

A stale-class-registry editor resave silently drops the `[ext_resource type="Script"]`
+ `script = ExtResource(...)` binding from a custom-Resource .tres. The resource then
loads as a bare Godot.Resource, and any cast to its C# type throws InvalidCastException
-- with a green build and no warning. This bit the encounter/floor/procgen config .tres
TWICE: fixed in 67873ef1, then re-stripped by 55ef215c across 22 files (only 4 tests
cast-and-caught it; the damage was far wider). See
gotcha_godot_editor_resave_hazards.

Detection: a strip is a SECTION that keeps its `[sub_resource ...]` / `[resource]` header and loses its
`script = ExtResource(...)` line (the editor writes `resource_local_to_scene = ""` in its place, often
dropping the `ext_resource` too). A binding that leaves a file because its whole section left is not a strip:
a sub-resource extracted to its own file, moved to another file, or deleted. The guard pairs each removed
script line with its section by header, so a resave that renames every `ext_resource` id, or that adds an
unrelated binding beside the strip, is still caught. A scripted section whose id vanished is a rename (a strip)
when the file gained a scriptless section of the same type, and a move or deletion otherwise. A removed line the
guard cannot attribute to a section falls back to the file's net count of removed over added bindings, so an
unreadable case stays a loss. Diff-scoped -- already-committed state isn't re-flagged; this catches the *next*
strip at commit time.

Residual gap: an editor resave that drops a whole scripted section together with the property that held it
(the fresh-worktree first-open variant in gotcha_godot_editor_resave_hazards) reads as a deletion.

Modes:
    tres_script_strip_guard.py              # scan staged (git diff --cached); exit 1 on strip
    tres_script_strip_guard.py --range A..B # scan a commit range (CI / PR review); exit 1 on strip
    tres_script_strip_guard.py --hook       # PreToolUse: deny a `git commit` that strips a binding
    tres_script_strip_guard.py --worktree   # scan working tree vs HEAD; report, exit 0
    tres_script_strip_guard.py --worktree --repair-inplace  # restore stripped bindings IN PLACE
                                             # from HEAD (keeps the editor's normalization -- the
                                             # fixed-point repair; a git-restore re-plants old-format
                                             # and re-arms the dirty-flag bomb)
    tres_script_strip_guard.py --editor-check  # warn when a Godot editor runs on an assembly newer
                                             # than its boot (the preventable bind-failure window)

Owner escape hatch, which no deny offers (a deleted or moved section needs none): set
HARNESS_ALLOW_TRES_SCRIPT_REMOVAL=1 in the environment or inline on the command. The guard cannot tell who set it.

Commit scope is `hooks/_commit_scope.py`: it names the invocations that publish the index and the
paths a merge authors.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _git_run  # noqa: E402
from _commit_scope import publication, publishes_index  # noqa: E402
from _checkout_root import repo_arg  # noqa: E402
from _hook_parts import bounded_decision, bounded_report  # noqa: E402

# Windows pipes default to cp1252 through Python 3.14; git diff text now decodes as real
# UTF-8, so a non-ASCII .tres string payload or path would raise UnicodeEncodeError on write.
# LF-only: Windows text mode adds a CR per line, which the cap's byte count does not see.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stderr.reconfigure(encoding="utf-8", newline="\n")

def tree_write_blockers():
    """Live gate/suite runs this repair would void. Fails OPEN: an unreadable registry must
    not stop the repair, because the corruption it fixes breaks resource loads outright.
    Canonical rule and evidence: `activity_registry.tree_write_blockers`."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import activity_registry

        args = ["rev-parse", "--show-toplevel"]
        repo = _git_run.run(args).check(args).text.strip()
        return activity_registry.tree_write_blockers(repo)
    except Exception:
        return []


REMOVED = re.compile(r"^-\s*script = ExtResource\(")
ADDED = re.compile(r"^\+\s*script = ExtResource\(")
SCRIPT_LINE = re.compile(r"^\s*script = ExtResource\(")
SECTION = re.compile(r"^\[(gd_resource|gd_scene|ext_resource|sub_resource|resource|node|connection|editable)\b.*\]\s*$")
SECTION_TYPE = re.compile(r'\btype="([^"]*)"')
# Sections that carry a script binding of their own: a scriptless one of the same type is where a strip lands.
BOUND_KINDS = ("sub_resource", "resource", "node")
FILE_HDR = re.compile(r'^\+\+\+ (?:b/(.*?)|"b/(.*)"|/dev/null)\t?$')
# Context lines for the second read: every line of the file, so a removed binding finds its section.
WHOLE_FILE = 1000000

# The commit's git environment, set by hook() from the invocation it judges: the staged diff is read
# from the index that commit publishes (`GIT_INDEX_FILE=<f> git commit`). None inherits.
_GIT_ENV = None


def diff_lines(range_arg, worktree=False, context=0):
    cmd = ["git", "diff", "-U%d" % context, "--no-color"]
    if worktree:
        cmd += ["HEAD"]
    elif range_arg:
        cmd += [range_arg]
    else:
        cmd += ["--cached"]
    cmd += ["--", "*.tres"]
    # quotepath off: a non-ASCII path stays readable, so a finding names a path the repair and the authored filter can open.
    return _git_run.run(cmd[1:], env=_GIT_ENV, prefix=("-c", "core.quotepath=off")).text.splitlines()


def _authored(findings, repo, git_env):
    published = publication("commit", (), repo, git_env)
    keep = set(published.authored(sorted({row[0] for row in findings})))
    return [row for row in findings if row[0] in keep]


class FileDiff:
    """One changed .tres: the lines of each side the diff shows, and its script-line counts."""

    def __init__(self):
        self.old, self.new, self.removed, self.added = [], [], 0, 0


def file_diffs(lines):
    """{path: FileDiff} for every non-deleted file in a `git diff` text.

    `+++ /dev/null` is a DELETED file (group 1 None): its script binding goes away with the file, which is a
    deletion and not an editor strip. Clearing `current` is what stops those removals being charged to the
    PREVIOUS file in the diff (measured 2026-08-13: a deleted .tres reported a phantom strip in its
    alphabetical neighbour). A content line always starts with a space, `+` or `-`, so `diff --git` and `@@`
    only ever open a header or a hunk."""
    files, current, in_hunk = {}, None, False
    for line in lines:
        if line.startswith("diff --git "):
            current, in_hunk = None, False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            hdr = FILE_HDR.match(line)
            if hdr:
                current = hdr.group(1) or hdr.group(2)
                if current is not None:
                    files.setdefault(current, FileDiff())
            continue
        if current is None or line.startswith("\\"):
            continue
        diff, side = files[current], line[:1]
        if side != "+":
            diff.old.append(line[1:])
        if side != "-":
            diff.new.append(line[1:])
        if REMOVED.match(line):
            diff.removed += 1
        elif ADDED.match(line):
            diff.added += 1
    return files


def sections(lines):
    """([(header, kind, type, scripted)], stray) for a .tres' lines: `stray` counts script lines above every header."""
    found, stray = [], 0
    for line in lines:
        head = SECTION.match(line)
        if head:
            typed = SECTION_TYPE.search(line)
            found.append([line.rstrip(), head.group(1), typed.group(1) if typed else "", False])
        elif SCRIPT_LINE.match(line):
            if found:
                found[-1][3] = True
            else:
                stray += 1
    return found, stray


def stripped_sections(diff):
    """The headers of sections the change left in the file but stripped of `script`, or None when the removed
    lines cannot be tied to sections."""
    before, stray = sections(diff.old)
    if stray:
        return None
    after, _ = sections(diff.new)
    unmatched = {}
    for sec in after:
        unmatched.setdefault(sec[0], []).append(sec)
    kept, gone = [], []
    for sec in before:
        slot = unmatched.get(sec[0])
        if slot:
            kept.append((sec, slot.pop(0)))
        else:
            gone.append(sec)
    stripped = [old[0] for old, new in kept if old[3] and not new[3]]
    appeared = [sec for slot in unmatched.values() for sec in slot
                if sec[1] in BOUND_KINDS and not sec[3]]
    for sec in gone:
        if not sec[3]:
            continue
        twin = next((new for new in appeared if (new[1], new[2]) == (sec[1], sec[2])), None)
        if twin is not None:
            appeared.remove(twin)
            stripped.append(sec[0])
    return stripped


def find_strips(range_arg, worktree=False):
    """Return [(path, removed, added, sections)] for files that lost a script binding.

    `sections` names the headers that kept their place and lost `script`; it is empty for a file whose
    removed lines could not be attributed, which keeps the net count of removed over added lines."""
    changed = file_diffs(diff_lines(range_arg, worktree))
    suspects = {path: diff for path, diff in changed.items() if diff.removed}
    if not suspects:
        return []
    whole = file_diffs(diff_lines(range_arg, worktree, WHOLE_FILE))
    findings = []
    for path, diff in suspects.items():
        stripped = stripped_sections(whole[path]) if path in whole else None
        if stripped is None:
            if diff.removed > diff.added:
                findings.append((path, diff.removed, diff.added, ()))
        elif stripped:
            findings.append((path, len(stripped), diff.added, tuple(stripped)))
    return findings


SCRATCH_DIR = Path(__file__).resolve().parents[1] / "scratch"


def scratch_list(session_id=""):
    """The whole-list file for this caller: one per session, so a peer session's rerun replaces only its own.
    A caller with no session id (the CLI and SessionStart scans) names its process instead of sharing one list."""
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in (session_id or "pid%d" % os.getpid()))
    return str(Path(SCRATCH_DIR) / ("tres_script_strip_findings.%s.txt" % stem))
INSTRUCTION = (
    "A custom-Resource .tres section that keeps its place but loses its `script = ExtResource(...)` line "
    "loads as a bare Godot.Resource -> casts to its C# type throw at runtime with a green build (the "
    "stale-registry editor-resave strip; gotcha_godot_editor_resave_hazards). "
    "Restore the binding with `tres_script_strip_guard.py --worktree --repair-inplace` "
    "(by hand where the file has no `resource_local_to_scene = \"\"` marker), then stage the file again."
)


def finding_row(path, removed, added, sections=()):
    if not sections:
        return f"  {path}: script bindings removed={removed} added={added}"
    shown = "; ".join(sections[:3]) + (f"; +{len(sections) - 3} more" if len(sections) > 3 else "")
    return f"  {path}: {removed} section(s) lost `script`: {shown}"


def report_parts(findings):
    """`(lead, rows)` of the report, the verdict and instruction lines apart from the finding rows."""
    lead = (
        "[tres-script-strip-guard] Suspected editor-resave script-binding strip "
        f"({len(findings)} file(s)):\n" + INSTRUCTION + "\n"
    )
    return lead, [finding_row(*row) for row in findings]


def report(findings, session_id=""):
    """The CLI report, for the human or Bash call that reads stderr."""
    lead, rows = report_parts(findings)
    sys.stderr.write(bounded_report(lead, rows, scratch_list(session_id)))


def standalone(range_arg):
    if os.environ.get("HARNESS_ALLOW_TRES_SCRIPT_REMOVAL"):
        return 0
    findings = find_strips(range_arg)
    if not findings:
        return 0
    report(findings)
    return 1


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    if data.get("tool_name") != "Bash":
        print("{}")
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _git_commit import commit_invocations, bypass_declared, git_invocation, segments
    from _git_run import git_environ
    cmd = data.get("tool_input", {}).get("command", "")
    commits = [c for c in commit_invocations(cmd, data.get("cwd") or ".")
               if publishes_index(c.sub, c.rest)]
    if commits:
        commit = commits[-1]
        global _GIT_ENV
        _GIT_ENV = git_environ(commit.git_env)
        if bypass_declared(commit.inline_env, "HARNESS_ALLOW_TRES_SCRIPT_REMOVAL"):
            print("{}")
            return 0
        try:
            os.chdir(commit.cwd)   # the scan below reads the repo the commit targets
        except OSError:
            pass

        findings = _authored(find_strips(None), commit.cwd, commit.git_env)
        if not findings:
            print("{}")
            return 0

        reason = (
            f"Blocked (rules/godot_files.md): {len(findings)} staged .tres kept a section but lost its "
            "`script = ExtResource(...)` binding (editor-resave script strip -> bare-Resource load / "
            "InvalidCastException). Restore the binding and stage the file again."
        )
        lead, rows = report_parts(findings)
        sys.stdout.write(bounded_decision(lambda text: json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason + "\n" + text,
        }}) + "\n", lead, rows, scratch_list(data.get("session_id") or "")))
        return 0
    if any(_restores_tres(git_invocation(seg)) for seg in segments(cmd)):
        # A git-restore of a .tres re-plants the OLD-FORMAT dirty-flag bomb (missing ext_resource
        # uids -> the editor rewrites on the next save -> the strip recurs). Allow, but advise.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "[tres-script-strip-guard] Restoring an old-format .tres re-strips it on the next "
                "editor save. For a stripped file run "
                "`tres_script_strip_guard.py --worktree --repair-inplace` instead."
            ),
        }}))
        return 0
    print("{}")
    return 0


# Flags of `git restore` / `git checkout` whose value is the next token, never a pathspec.
_RESTORE_VALUE_FLAGS = {"-s", "--source", "-b", "-B", "--orphan"}


def _may_hold_tres(path):
    """A pathspec that names a .tres, or one that could contain one: `.`, a directory, a glob."""
    name = path.rstrip("/").rsplit("/", 1)[-1].lstrip(".")
    return (path.endswith(".tres") or path.endswith("/") or any(c in path for c in "*?[")
            or "." not in name)


def _restores_tres(invocation):
    """True when a parsed git invocation is a restore/checkout whose pathspecs may hold a .tres.
    Without `--`, checkout's first positional is a tree-ish unless it is `.`, a .tres or a glob."""
    if not invocation or not invocation[0] or invocation[0][0] not in ("restore", "checkout"):
        return False
    sub, rest = invocation[0][0], invocation[0][1:]
    if any(a.startswith("--pathspec-from-file") for a in rest):
        return True
    positional, after_dashdash, i = [], None, 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--" and after_dashdash is None:
            after_dashdash = len(positional)
        elif arg in _RESTORE_VALUE_FLAGS:
            i += 1
        elif not arg.startswith("-"):
            positional.append(arg)
        i += 1
    if after_dashdash is not None:
        paths = positional[after_dashdash:]
    elif sub == "checkout" and positional:
        first = positional[0]
        paths = positional[1:]
        if first.rstrip("/") == "." or first.endswith(".tres") or any(c in first for c in "*?["):
            paths = positional
    else:
        paths = positional
    return any(_may_hold_tres(p) for p in paths)


def worktree_scan(revert, repair=False):
    """SessionStart surface: report (stdout -> session context) any working-tree strip.

    Always exits 0 -- a SessionStart hook must inform, not block. --revert restores
    each stripped file from HEAD (used manually or by a recovery script, never wired
    as a default: an unstaged legit removal must not be silently clobbered).
    """
    findings = find_strips(None, worktree=True)
    if not findings:
        return 0
    header = (
        f"[tres-script-strip-guard] WORKING-TREE script-binding strip detected in "
        f"{len(findings)} file(s) -- the editor-resave corruption is present RIGHT NOW:\n"
    )
    rows = [finding_row(*row) for row in findings]
    if repair:
        blockers = tree_write_blockers()
        if blockers:
            who = ", ".join(
                f"{b.get('kind')} '{b.get('label', '?')}' (pid {b.get('pid')})" for b in blockers[:3]
            )
            lead = header + (
                f"[tres-script-strip-guard] Repair DEFERRED — {who} is live on this checkout and "
                "digests the tree mid-run; writing now would void that run with no test signal. "
                "The strip above is still present. Re-run after the run finishes:\n"
                "  python .claude/hooks/tres_script_strip_guard.py --worktree --repair-inplace\n"
            )
            sys.stdout.write(bounded_report(lead, rows, scratch_list()))
            return 0
        results = repair_inplace(findings)
        lead = header + f"[tres-script-strip-guard] Repaired {len(findings)} file(s) in place (bindings restored).\n"
        sys.stdout.write(bounded_report(lead, rows + results, scratch_list()))
    elif revert:
        _git_run.run(["checkout", "--", *[row[0] for row in findings]])
        lead = header + f"[tres-script-strip-guard] Reverted {len(findings)} file(s) from HEAD.\n" + (
            "WARNING: git-restore re-plants OLD-FORMAT .tres (ext_resource uid backfill pending), "
            "re-arming the dirty-flag bomb -- the strip recurs at the next editor save. Prefer "
            "--repair-inplace, which keeps the editor's normalization and re-adds the binding.\n"
        )
        sys.stdout.write(bounded_report(lead, rows, scratch_list()))
    else:
        lead = header + (
            "Fix now: verify each diff is a strip (not an intentional removal), then "
            "`tres_script_strip_guard.py --worktree --repair-inplace` (keeps the editor's "
            "normalization, re-adds bindings from HEAD). See gotcha_godot_editor_resave_hazards.\n"
        )
        sys.stdout.write(bounded_report(lead, rows, scratch_list()))
    return 0


def editor_boot_check():
    """Prevention signal: warn when a Godot editor runs on an assembly newer than its boot.

    The editor-resave strip needs a script-bind-failure window; the preventable one is an
    editor booted BEFORE the current assembly build -- its cached resources fail restoration
    on the next rebuild/play cycle, and the next save writes them stripped. Inform-only.
    """
    import datetime

    dll = Path(__file__).resolve().parents[1] / ".godot" / "mono" / "temp" / "bin" / "Debug" / "{{PROJECT_NAME}}.dll"
    if not dll.exists():
        return 0
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -like 'Godot*' } | Select-Object -First 1 "
             "| ForEach-Object { $_.StartTime.ToFileTime() }"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
        ).stdout.strip()
    except Exception:
        return 0
    if not out:
        return 0  # no editor running
    try:
        editor_start = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=int(out) / 10)
    except ValueError:
        return 0
    dll_mtime = datetime.datetime.fromtimestamp(dll.stat().st_mtime)
    if editor_start < dll_mtime:
        print(
            f"[tres-script-strip-guard] EDITOR BOOTED BEFORE THE CURRENT ASSEMBLY BUILD "
            f"(editor {editor_start:%H:%M} vs {{PROJECT_NAME}}.dll {dll_mtime:%H:%M}). "
            "Restart the editor so its cached resources bind against the current DLL; an "
            "editor-resave across the stale assembly strips .tres script bindings "
            "(gotcha_godot_editor_resave_hazards)."
        )
    return 0


def repair_inplace(findings):
    """Restore stripped bindings IN PLACE from HEAD, keeping the worktree's editor normalization.

    A git-checkout revert re-plants OLD-FORMAT files (missing ext_resource uids) -> the dirty-flag
    bomb (gotcha rule 4): the editor re-marks them for uid backfill, the next save rewrites them,
    and the strip recurs at the next bind-failure window. In-place keeps the editor's
    serialization and re-adds only the lost script binding, making the file a fixed point.
    """
    results = []
    for path, *_ in findings:
        wf = Path(path)
        text = wf.read_text(encoding="utf-8")
        if "resource_local_to_scene = \"\"" not in text:
            results.append(f"  {path}: no strip marker -- already repaired?")
            continue
        head = _git_run.run(["show", f"HEAD:{path}"]).text
        for marker in re.finditer(r'resource_local_to_scene = ""', text):
            block_start = text.rfind("[sub_resource", 0, marker.start())
            if block_start < 0:
                continue
            block_end = text.find("\n[", marker.start())
            if block_end < 0:
                block_end = len(text)
            block_id = re.search(r'id="([^"]+)"', text[block_start:block_start + 200])
            if not block_id:
                continue
            bid = block_id.group(1)
            head_block = re.search(
                r'\[sub_resource[^\n]*id="' + re.escape(bid) + r'"[^\n]*\]\n(.*?)(?=\n\[|\Z)',
                head, re.S)
            if not head_block:
                results.append(f"  {path}: HEAD has no sub_resource id={bid} -- manual repair required")
                continue
            head_binding = re.search(r'^script = ExtResource\("([^"]+)"\)', head_block.group(1), re.M)
            if not head_binding:
                results.append(f"  {path}: HEAD's sub_resource id={bid} has no script binding -- manual repair")
                continue
            ref_id = head_binding.group(1)
            head_ext = re.search(
                r'\[ext_resource[^\n]*id="' + re.escape(ref_id) + r'"[^\n]*\]', head)
            if not head_ext:
                results.append(f"  {path}: HEAD ext_resource id={ref_id} not found -- manual repair")
                continue
            ext_line = head_ext.group(0)
            ext_path = re.search(r'path="([^"]+)"', ext_line).group(1)
            existing = re.search(r'\[ext_resource[^\n]*path="' + re.escape(ext_path) + r'"[^\n]*\]', text)
            if existing:
                final_id = re.search(r'id="([^"]+)"', existing.group(0)).group(1)
            elif re.search(r'id="' + re.escape(ref_id) + r'"', text):
                results.append(f"  {path}: ext_resource id {ref_id} collides in worktree -- manual repair")
                continue
            else:
                insert_at = text.find("\n[ext_resource")
                text = text[:insert_at] + "\n" + ext_line + text[insert_at:]
                final_id = ref_id
            text = text.replace(
                'resource_local_to_scene = ""', f'script = ExtResource("{final_id}")', 1)
            results.append(f"  {path}: restored binding -> {ext_path} (id {final_id})")
        wf.write_text(text, encoding="utf-8", newline="\n")
    return results


def main():
    repo = repo_arg(sys.argv)
    if repo:
        os.chdir(repo)
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    if "--editor-check" in args:
        editor_boot_check()
        if "--worktree" not in args:
            return 0
    if "--worktree" in args:
        return worktree_scan(revert="--revert" in args, repair="--repair-inplace" in args)
    range_arg = args[1] if len(args) > 1 and args[0] == "--range" else None
    return standalone(range_arg)


if __name__ == "__main__":
    sys.exit(main())
