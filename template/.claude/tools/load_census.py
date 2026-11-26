#!/usr/bin/env python3
"""Session load census: every byte a session pays before a task starts, as a
regression check with budgets. Productionized from
`.claude/scratch/harness-finish-3a/{load_census,hook_profile}.py`.

Sections (all computed on every run, printed always):
  - listing     -- every `commands/**/*.md` and `skills/*/SKILL.md` frontmatter
                    `description:` field, plus every `workflows/*.js`
                    `export const meta = {...}` `description` string (rows of
                    kind `workflow`): the always-loaded skill listing.
  - standing    -- `.claude/CLAUDE.md`, `.claude/auto-memory/MEMORY.md`,
                    `~/.claude/CLAUDE.md`, and the active output style: the
                    first `outputStyle` in `.claude/settings.local.json`, then
                    `.claude/settings.json`, then `~/.claude/settings.json`,
                    read from `.claude/output-styles/<name>.md`, then
                    `~/.claude/output-styles/<name>.md`. A named style with no
                    file is a source error; no style set (or `default`) is not.
  - rules       -- `rules/**/*.md` grouped by exact `paths:` glob-list family, plus
                    the profiles' CONTAINS bundles, called out separately since
                    each loads on every session's first touch of a matching
                    file regardless of exact-family membership: a `glob`
                    bundle is every rule file whose `paths:` list contains that
                    glob; a `rule` bundle is every rule file whose `paths:`
                    list contains ANY glob from that named rule's own `paths:`
                    list. A bundle is empty when nothing matches or the named
                    rule is absent.
  - oversized   -- bodies (`skills/**/*.md` + `commands/**/*.md`) over 24,576 B,
                    a review trigger like every other tripwire here, each flagged
                    with whether a selector already exists for it
                    (`tools/lens.py` REGISTRIES -> `commands/agents/<file>`, or any
                    `tools/*.py` module-level CATALOG), loaded live from those modules
                    so this file never carries a second copy of their registry.
  - reattach    -- invocable bodies (`commands/*.md`, `commands/checklists/*.md`,
                    `skills/*/SKILL.md`) longer than `SKILL_REATTACH_CHARS`
                    (`hooks/_hook_state.py`), counted in characters as the client cuts them.
  - transitive  -- for each ENTRYPOINTS file, its own bytes plus every OTHER
                    `.claude/`-relative markdown file it references
                    UNCONDITIONALLY (see heuristic below).
  - hook_chain  -- static count, from `settings.json` alone (no subprocess),
                    of PreToolUse + PostToolUse hooks whose matcher fires on a
                    Write or an Edit tool call -- the subprocess chain length
                    one edit pays.

`--profile-hooks` additionally runs every registered PreToolUse/PostToolUse
hook against synthetic payloads (state is redirected via HARNESS_HOOK_STATE_DIR
to a throwaway temp dir, never real state)
and prints wall ms / stdout bytes / stderr bytes per hook. `--budgets` needs
a UserPromptSubmit measurement regardless of `--profile-hooks` (an
emitted-byte budget cannot be checked from source bytes), so it always runs
`profile_user_prompt_hooks_two_pass` -- every registered UserPromptSubmit
hook, TWICE, against one shared HARNESS_HOOK_STATE_DIR/session: pass 1 is the
first prompt of a session, pass 2 is the steady-state floor an unchanged
later prompt pays (a fire-once hook like `budget_posture.py` emits only on pass
1). `prompt_emission_first_bytes` is reported, never budgeted;
`prompt_emission_bytes` budgets `prompt_emission_steady_bytes`.
A registered `prompt_dispatch.py` is expanded, by reading its module-level
`CHAIN` literal with `ast` (never importing it), into one row per member run
under that member's `CHAIN` budget and its own isolation rule; a member whose
hook file is absent from the tree is skipped with no row, as the dispatcher
skips it; an unreadable `CHAIN` is one `UNREADABLE` row for the dispatcher,
which `unmeasured_prompt_hooks` reports.
SessionStart emission: every registered SessionStart command runs isolated (`profile_session_start_hooks`) and
its stdout is budgeted at HOOK_TEXT_CAP, except the scripts `SESSION_START_SKIP` (extended by the
`session_start_skip` profile key) names with their reasons. A group with no matcher fires at every source, so both
the `startup` and `compact` paths run. Because that text is part of what a session loads,
`session_start_injection` also counts it per reader: a reader's bytes are its own case's summed stdout plus every
command whose text does not vary by reader at that source, and its `load_bytes` adds the standing total.
Shared-state hooks that write real machine state resolve every write through an env var
(`ISOLATED_SHARED_STATE_HOOKS`) and run for real against a throwaway project/home/temp tree instead of being
skipped; `shell_census.py` additionally reads a live process snapshot, so its bytes are reported in their own
column and excluded from both sums (`LIVE_MACHINE_HOOKS`). A hook with no such seam
(`NON_ISOLATABLE_SHARED_STATE_HOOKS`) is skipped and reported with its reason.
A prompt hook that caches under the project's `.claude/.cache` is redirected into the run's own state dir by
`HARNESS_GIT_STATE_CACHE_DIR` and `HARNESS_REAPER_CACHE_DIR`, never the real repository.

Transitive-load heuristic (approximate by design; the two failure directions
are: undercounting a real unconditional load, or overcounting a conditional
one -- this heuristic is tuned to be readable and re-checkable, not perfect):
a backticked span that looks like a relative markdown path (`.claude/`-rooted
or not, ending `.md`) or a bare `skills/<name>` directory reference is a
candidate. A candidate is UNCONDITIONAL, and counted into the entrypoint's
transitive bytes, unless its own LINE contains a phase-gate cue substring
(case-insensitive): "if ", "when ", "only if", "only when", "unless",
"during the", "at the", "phase-gated", "conditionally", "conditional on".
A gated line's candidates are read (traced) but excluded from the sum. Each
candidate resolves first against the census root (`.claude/`), then against
the entrypoint's own directory; an unresolvable reference is dropped silently
(it names something outside `.claude/`, e.g. a `Tests/` path).

Profiles: every `tools/load_census.*.json` under the census root extends the
pure defaults below, so each layer or project that ships rules, hooks or
entrypoints declares them itself. Keys: `rule_bundles` (name -> `{"glob": g}`
or `{"rule": "<file>.md"}`; reported as `<name>_bundle_files/_bytes`),
`budgets` (key -> `[cap, baseline]`; a bundle's key is
`<name>_rule_bundle_bytes`), `entrypoints`, `known_oversized_no_selector`,
`shared_state_hook_skip`, `isolated_shared_state_hooks`, `session_start_skip`
(name -> reason), and `code_edit_probe` (`{"path": <project-relative file>}`,
profiled as the `edit-code` case). Lists union, mappings update, and an
unreadable profile is a source error. `tools/load_census.project.json` is the
project's own profile: its `budgets` hold the project's baselines. The base ships
cap budgets only; a tripwire (cap `null`, `[null, N]`) measures the project's own
text, so a project that records no baseline there gets no tripwire row.

A budget is `[cap, recorded baseline]`. A `cap` of `null` makes it a tripwire:
`--budgets` prints one `tripwire` line per crossing and exits 0, and
`over_baseline` returns the same rows for the `/session_end` hot-set review.
A selectorless body over OVERSIZED_THRESHOLD, or a KNOWN_OVERSIZED_NO_SELECTOR body past its
recorded bytes, is likewise an `oversized_body` row there and never fails `--budgets`; only a stale
KNOWN_OVERSIZED_NO_SELECTOR entry fails, as bookkeeping.

CLI: `python3 .claude/tools/load_census.py [--json <path>] [--profile-hooks] [--budgets] [--caps]`
`--delta <base>` runs separately: print changed standing and conditional guidance bytes from the committed
base to the working tree, including new Markdown under rules/commands/skills/output-styles. Standing
imports and unscoped rules count as standing; unchanged external home files have no versioned delta.
Hook emission is marked unmeasured: source bytes do not measure a runtime channel.
`--budgets` fails closed when a standing file, entrypoint, or rule `paths:` frontmatter is missing or malformed.
`--caps` (with `--budgets`) also fails on a per-kind LISTING_CAPS violation; the over-cap report
always prints with `--budgets` regardless of `--caps`.
`--root PATH` (hidden) points the whole census at a fixture tree for tests.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
from _git_run import run as git_run  # noqa: E402
from _hook_state import SKILL_REATTACH_CHARS, reattach_over_cap  # noqa: E402,F401
from amend_delivery import HOOK_TEXT_CAP  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_ROOT = Path(__file__).resolve().parent.parent  # .claude/

FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _field_re(name: str) -> re.Pattern:
    """A frontmatter field's value: inline, or a `>`/`>-`/`|` block scalar up to the next key."""
    return re.compile(r"^" + name + r":\s*(>-?|\|)?\s*\n?(.*?)(?=^\w[\w-]*:|\Z)", re.S | re.M)


DESC_RE = _field_re("description")
WHEN_TO_USE_RE = _field_re("when_to_use")
PATHS_RE = re.compile(r"^paths:\s*\n((?:\s*-\s*.+\n?)+)", re.M)
PATH_ITEM_RE = re.compile(r'^\s*-\s*"?([^"\n]+?)"?\s*$', re.M)

# Each budget is `(cap, recorded baseline)`; live values are recomputed on every run.
# Re-basing a cap budget sets cap = baseline + 5% rounded up to 256 B (subprocess counts: baseline + 2).
# The base ships cap budgets only. A tripwire (cap None) measures the consuming project's own text, so its
# baseline is that project's number: it lives in `tools/load_census.project.json` `budgets` as `[null, N]`,
# never here. This tool is shared by every project that pulls the base, so a literal written here would be
# one project's number in every project's tool.
BUDGETS = {
    # One SessionStart command's stdout, the largest over its cases: the client's hook-text cap (a fixed client
    # limit, never re-based). The baseline is the largest emission measured when this budget landed.
    "session_start_emission_bytes": (HOOK_TEXT_CAP, 9_953),
    # Keep a small allowance for the registered Write/Edit hook chain.
    "edit_hook_chain_subprocesses": (4, 2),
}

OVERSIZED_THRESHOLD = 24_576

# Per-kind listed-text caps (UTF-8 bytes of the text the listing prints for one entry).
# `instruction_quality` §7/§8 owns the caps; `check_listing_caps` reuses `listing_census()`'s
# rows, so no second enumerator exists.
LISTING_CAPS = {"skill": 300, "command": 60, "workflow": 60}

# `over_baseline` reports a selectorless body over OVERSIZED_THRESHOLD, and a listed body that grew
# past KNOWN_OVERSIZED_GROWTH, for the close's hot-set review. --budgets fails only on a listed body
# that fell below the threshold, gained a selector or went missing.
KNOWN_OVERSIZED_NO_SELECTOR: dict[str, int] = {}
KNOWN_OVERSIZED_GROWTH = 0.05

ENTRYPOINTS = [
    "commands/session_end.md",
    "commands/worklog.md",
]

PHASE_GATE_CUES = (
    "if ", "when ", "only if", "only when", "unless", "during the",
    "at the", "phase-gated", "conditionally", "conditional on",
    # Shape-gated: handed out for one plan shape (plan_check 1g gives design_litmus and
    # scene_authoring to code plans only), so not an every-invocation load.
    "code plan", "meta plan", "code design", "code shape", "meta shape",
)
MD_REF_RE = re.compile(r"`((?:\.claude/)?(?:[\w.-]+/)+[\w.-]+\.md|skills/[\w-]+)`")

# Shared-state hooks skipped by name (copied from hook_profile.py): running
# them against synthetic payloads would touch real machine-wide registries,
# and no env-var seam isolates them (unlike ISOLATED_SHARED_STATE_HOOKS below).
SHARED_STATE_HOOK_SKIP = {
    "overnight_ask_guard.py",
    "subagent_dispatch_guard.py",
}

# UserPromptSubmit hooks that write shared/machine-wide state and need the throwaway
# project/home/temp tree `_isolated_hook_env` builds. Empty in this tree: its dispatcher chain
# (prompt_git_state_delta, sidecar_watch_rearm, overnight_ask_guard, skill_load_marker) either resolves every write
# through a cache or state seam the profiler already redirects into its own temp tree
# (HARNESS_GIT_STATE_CACHE_DIR, HARNESS_REAPER_CACHE_DIR, HARNESS_HOOK_STATE_DIR) or is skipped by
# name. A profile (a layer that registers a hook resolving a write through CLAUDE_PROJECT_DIR or
# HOME) adds its own. A hook that joins this set has its live case beside `IsolatedHookCwdTests`
# in `tests/test_load_census.py`, seeded there: no production list is assumed.
ISOLATED_SHARED_STATE_HOOKS: set[str] = set()

# hook name -> reason its emission measures live machine state rather than
# harness-controlled behavior, so it is reported in its own column and
# excluded from the prompt_emission_bytes sum even though it is isolated and
# run for real. shell_census.py's number depends on which shells happen to be
# running on the machine at profile time; it is not a stable, budgetable
# quantity.
LIVE_MACHINE_HOOKS = {
    "shell_census.py": "reads a live Win32_Process CIM snapshot of this machine's "
                        "processes; not reproducible from a synthetic payload",
}

# hook name -> reason it cannot be isolated. Empty in production: every known
# shared-state UserPromptSubmit hook resolves through an env-var seam (see
# ISOLATED_SHARED_STATE_HOOKS). Kept as a live dict, not a constant, so a
# future hook that genuinely has no seam can be recorded here with its
# reason rather than silently skipped.
NON_ISOLATABLE_SHARED_STATE_HOOKS: dict[str, str] = {}

PROFILE_GLOB = "tools/load_census.*.json"
PROFILE_LISTS = ("entrypoints", "shared_state_hook_skip", "isolated_shared_state_hooks")
PROFILE_MAPS = ("rule_bundles", "budgets", "known_oversized_no_selector", "code_edit_probe",
                "session_start_skip")


def load_profile(root: Path) -> dict:
    """The merged `tools/load_census.*.json` profiles under `root`, in name order, plus a
    `source_errors` list naming any unreadable one and `budget_sources` naming the file each
    merged budget key came from."""
    merged: dict = {key: [] for key in PROFILE_LISTS}
    merged.update({key: {} for key in PROFILE_MAPS})
    merged["source_errors"] = []
    merged["budget_sources"] = {}
    for path in sorted(root.glob(PROFILE_GLOB)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except (OSError, ValueError) as exc:
            merged["source_errors"].append(f"unreadable census profile: {path}: {exc}")
            continue
        for key in PROFILE_LISTS:
            merged[key].extend(x for x in data.get(key, []) if x not in merged[key])
        for key in PROFILE_MAPS:
            merged[key].update(data.get(key, {}))
        merged["budget_sources"].update(
            {name: _repo_rel(root, path) for name in data.get("budgets", {})})
    return merged


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def frontmatter(path: Path) -> str | None:
    """The file's frontmatter block, or None when it has none. One read per listed file."""
    m = FM.match(path.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def _field(fm: str | None, regex: re.Pattern) -> str:
    m = regex.search(fm) if fm is not None else None
    return (m.group(2) if m else "").strip()


def description(fm: str | None) -> str:
    return _field(fm, DESC_RE)


def _first_line(path: Path) -> str:
    """The first non-empty body line, heading marks stripped: what the client lists for a command
    with no `description`."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FM.match(text)
    for line in (text[m.end():] if m else text).splitlines():
        if line.strip():
            return line.strip().lstrip("#").strip()
    return ""


def when_to_use(fm: str | None) -> str:
    """The skill's `when_to_use` field, or '' if absent. The client lists a skill as `description`
    plus `when_to_use` when the field is present."""
    return _field(fm, WHEN_TO_USE_RE)


def listed(fm: str | None) -> bool:
    """False when the frontmatter keeps the entry out of the model's skill listing:
    `disable-model-invocation: true`, or a `paths:` scope that surfaces it only on matching reads."""
    if fm is None:
        return True
    return not (re.search(r"^disable-model-invocation:\s*true\b", fm, re.M)
                or re.search(r"^paths:", fm, re.M))


WORKFLOW_META_RE = re.compile(r"export\s+const\s+meta\s*=\s*\{(.*?)^\}", re.S | re.M)


def _js_string_field(block: str, field: str) -> str | None:
    """The first `<field>: '...'` (or "..." / `...`) string in `block`, unescaped."""
    m = re.search(r"^\s*" + field + r"""\s*:\s*(['"`])((?:\\.|(?!\1).)*)\1""", block, re.M | re.S)
    if not m:
        return None
    return re.sub(r"\\(.)", r"\1", m.group(2), flags=re.S)


def workflow_meta(path: Path) -> tuple[str | None, str] | None:
    """(name, listed text) from a workflow's `export const meta = {...}` block, or None. The client
    lists a workflow as `name: description - whenToUse`, so the listed text carries both."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = WORKFLOW_META_RE.search(text)
    if not m:
        return None
    desc = _js_string_field(m.group(1), "description")
    if desc is None:
        return None
    when = _js_string_field(m.group(1), "whenToUse")
    return _js_string_field(m.group(1), "name"), desc + (" - " + when if when else "")


def _parse_paths_frontmatter(path: Path) -> tuple[list[str], list[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], [f"{path}: unreadable: {exc}"]
    m = FM.match(text)
    if not m:
        return [], [f"{path}: missing frontmatter"]
    body = m.group(1)
    pm = PATHS_RE.search(body)
    if not pm:
        return [], [f"{path}: paths frontmatter must be a non-empty YAML list"]
    paths = PATH_ITEM_RE.findall(pm.group(1))
    if not paths:
        return [], [f"{path}: paths frontmatter must be a non-empty YAML list"]
    return paths, []


def parse_paths_frontmatter(path: Path) -> list[str]:
    paths, _errors = _parse_paths_frontmatter(path)
    return paths


# ---------------------------------------------------------------------------
# Census sections
# ---------------------------------------------------------------------------

def _repo_rel(root: Path, path: Path) -> str:
    """`path` relative to the repo (the census root's parent), or `external:<path>` outside it."""
    try:
        return path.resolve().relative_to(root.resolve().parent).as_posix()
    except ValueError:
        return f"external:{path}"


def listing_census(root: Path) -> dict:
    rows = []
    # The client lists every command file, a subdirectory's as `<dir>:<name>`, and one with no
    # `description` under its first body line.
    for p in sorted(root.glob("commands/**/*.md")):
        fm = frontmatter(p)
        if not listed(fm):
            continue
        rows.append({"kind": "command", "name": str(p.relative_to(root)).replace("\\", "/"),
                     "path": _repo_rel(root, p),
                     "desc_bytes": len((description(fm) or _first_line(p)).encode("utf-8"))})
    for p in sorted(root.glob("skills/*/SKILL.md")):
        fm = frontmatter(p)
        if not listed(fm):
            continue
        listed_text = description(fm)
        when = when_to_use(fm)
        if when:
            listed_text = listed_text + " " + when
        rows.append({"kind": "skill", "name": p.parent.name, "path": _repo_rel(root, p),
                     "desc_bytes": len(listed_text.encode("utf-8"))})
    for p in sorted(root.glob("workflows/*.js")):
        meta = workflow_meta(p)
        if meta is None:
            continue
        name, desc = meta
        rows.append({"kind": "workflow", "name": name or p.stem, "path": _repo_rel(root, p),
                     "desc_bytes": len(desc.encode("utf-8"))})
    total = sum(r["desc_bytes"] for r in rows)
    top12 = sorted(rows, key=lambda r: -r["desc_bytes"])[:12]
    return {
        "rows": rows,
        "count": len(rows),
        "commands": sum(1 for r in rows if r["kind"] == "command"),
        "skills": sum(1 for r in rows if r["kind"] == "skill"),
        "workflows": sum(1 for r in rows if r["kind"] == "workflow"),
        "total_bytes": total,
        "top12": top12,
    }


def standing_census(root: Path, home: Path | None = None) -> dict:
    home = home or Path.home()
    files = [root / "CLAUDE.md", root / "auto-memory" / "MEMORY.md", home / ".claude" / "CLAUDE.md"]
    rows = []
    source_errors = []
    for f in files:
        try:
            exists = f.is_file()
            size = f.stat().st_size if exists else 0
        except OSError as exc:
            exists = False
            size = 0
            source_errors.append(f"unreadable standing file: {f}: {exc}")
        if not exists and not any(str(f) in error for error in source_errors):
            source_errors.append(f"missing standing file: {f}")
        rows.append({"path": str(f), "bytes": size, "exists": exists})
    rows.extend(_import_rows(files, source_errors))
    style_row, style_errors = _output_style_row(root, home)
    source_errors.extend(style_errors)
    if style_row:
        rows.append(style_row)
    return {"rows": rows, "total_bytes": sum(r["bytes"] for r in rows),
            "source_errors": source_errors}


IMPORT_MAX_HOPS = 4


def _import_targets(importer: Path) -> list[Path]:
    """Paths of the `@path` lines in a file, outside fenced code blocks; a backslash-space continues a path."""
    targets = []
    fence = None
    for line in importer.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = line.lstrip()[:3]
        if marker in ("```", "~~~"):
            fence = None if fence == marker else (fence or marker)
            continue
        if fence or not line.startswith("@"):
            continue
        raw = re.match(r"(?:\\ |\S)+", line[1:])
        if raw:
            path = Path(raw.group(0).replace("\\ ", " ")).expanduser()
            targets.append((importer.parent / path).resolve())
    return targets


def _import_rows(files: list[Path], source_errors: list[str]) -> list[dict]:
    """One row per file a standing file pulls in by an `@path` line, each once, up to four hops deep.

    Claude Code (https://code.claude.com/docs/en/memory): "Relative paths resolve relative to the file
    containing the import, not the working directory. Imported files can recursively import other files,
    with a maximum depth of four hops." and "Import parsing skips Markdown code spans and fenced code
    blocks." An imported file loads with its importer, so it is standing text; missing it undercounts
    every always-loaded budget. A file reached twice (a cycle) is counted once.
    """
    seen = {f.resolve() for f in files if f.is_file()}
    queue = [(f, 0) for f in files if f.is_file()]
    rows = []
    while queue:
        importer, hops = queue.pop(0)
        if hops >= IMPORT_MAX_HOPS:
            continue
        for target in _import_targets(importer):
            if target in seen:
                continue
            seen.add(target)
            if not target.is_file():
                source_errors.append(f"missing standing import: {target} (imported by {importer})")
                continue
            rows.append({"path": str(target), "bytes": target.stat().st_size, "exists": True})
            queue.append((target, hops + 1))
    return rows


def _output_style_row(root: Path, home: Path, *, read=None) -> tuple[dict | None, list[str]]:
    """The active output style's standing row; read may supply snapshot bytes (None when absent)."""
    if read is None:
        def read(path):
            return path.read_bytes() if path.is_file() else None

    name = setting_file = None
    for settings_path in (root / "settings.local.json", root / "settings.json",
                          home / ".claude" / "settings.json"):
        try:
            source = read(settings_path)
            if source is None:
                continue
            data = json.loads(source)
        except (OSError, ValueError) as exc:
            return None, [f"unreadable settings file while resolving outputStyle: {settings_path}: {exc}"]
        value = data.get("outputStyle") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            name, setting_file = value.strip(), settings_path
            break
    if name is None or name.lower() == "default":
        return None, []
    candidates = (root / "output-styles" / f"{name}.md", home / ".claude" / "output-styles" / f"{name}.md")
    for style_path in candidates:
        try:
            source = read(style_path)
        except OSError as exc:
            return None, [f"unreadable output style file: {style_path}: {exc}"]
        if source is not None:
            return {"path": str(style_path), "bytes": len(source), "exists": True}, []
    return ({"path": str(candidates[0]), "bytes": 0, "exists": False},
            [f"missing output style file for outputStyle '{name}' (set in {setting_file}); looked in "
             + ", ".join(str(c) for c in candidates)])


def rules_census(root: Path) -> dict:
    entries = []
    source_errors = []
    # The client loads rules/ subdirectories too; a file there without paths: loads in every session.
    for p in sorted((root / "rules").glob("**/*.md")):
        globs, errors = _parse_paths_frontmatter(p)
        source_errors.extend(f"malformed rule frontmatter: {error}" for error in errors)
        try:
            size = p.stat().st_size
        except OSError as exc:
            size = 0
            source_errors.append(f"unreadable rule source: {p}: {exc}")
        entries.append({"name": p.relative_to(root / "rules").as_posix(), "bytes": size, "paths": globs})
    families: dict[tuple, list] = {}
    for e in entries:
        families.setdefault(tuple(e["paths"]), []).append(e)
    family_rows = sorted(
        ({"paths": list(k), "files": [f["name"] for f in v], "total_bytes": sum(f["bytes"] for f in v)}
         for k, v in families.items()),
        key=lambda r: -r["total_bytes"],
    )

    def _bundle(spec: dict) -> dict:
        """CONTAINS match: a sibling rule can share one glob while its full paths tuple
        differs. A `rule` bundle keys on every glob of that named rule, and is empty (no
        error) when the rule is absent from this tree."""
        if "glob" in spec:
            keys = [spec["glob"]]
        else:
            owner = next((e for e in entries if e["name"] == spec.get("rule")), None)
            keys = owner["paths"] if owner else []
        matched = [e for e in entries if any(k in e["paths"] for k in keys)]
        return {"files": sorted(e["name"] for e in matched),
                "bytes": sum(e["bytes"] for e in matched)}

    report = {"entries": entries, "families": family_rows, "bundles": {}, "source_errors": source_errors}
    for name, spec in load_profile(root)["rule_bundles"].items():
        bundle = _bundle(spec)
        report["bundles"][name] = bundle
        report[f"{name}_bundle_files"] = bundle["files"]
        report[f"{name}_bundle_bytes"] = bundle["bytes"]
    return report


def load_selector_registry(root: Path) -> set:
    """Resolved absolute Paths already covered by a selector tool, read live
    from `tools/lens.py` (REGISTRIES -> AGENTS dir) and every `tools/*.py`
    that assigns a module-level `CATALOG` (`kfm.py`, `subsys.py`), never
    duplicated here."""
    covered: set = set()
    lens_path = root / "tools" / "lens.py"
    if lens_path.exists():
        try:
            spec = importlib.util.spec_from_file_location("_load_census_lens", lens_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            agents_dir = getattr(mod, "AGENTS", root / "commands" / "agents")
            for filename, _prefix in getattr(mod, "REGISTRIES", {}).values():
                covered.add((agents_dir / filename).resolve())
        except Exception:
            pass
    for tool in sorted((root / "tools").glob("*.py")):
        try:
            if not re.search(r"^CATALOG\s*=", tool.read_text(encoding="utf-8"), re.M):
                continue
            spec = importlib.util.spec_from_file_location("_load_census_" + tool.stem, tool)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            catalog = getattr(mod, "CATALOG", None)
            if catalog:
                covered.add(Path(catalog).resolve())
        except Exception:
            pass
    return covered


def oversized_bodies(root: Path) -> list[dict]:
    selector_covered = load_selector_registry(root)
    known = _known_oversized(root)
    rows = []
    seen = set()
    for p in sorted(list(root.glob("skills/**/*.md")) + list(root.glob("commands/**/*.md"))):
        resolved = p.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        size = p.stat().st_size
        if size <= OVERSIZED_THRESHOLD:
            continue
        rel = ".claude/" + str(p.relative_to(root)).replace("\\", "/")
        known_bytes = known.get(rel)
        rows.append({
            "path": rel,
            "bytes": size,
            "has_selector": resolved in selector_covered,
            "known_bytes": known_bytes,
            "known": known_bytes is not None and size <= known_bytes * (1 + KNOWN_OVERSIZED_GROWTH),
        })
    return sorted(rows, key=lambda r: -r["bytes"])


def _known_oversized(root: Path) -> dict:
    return {**KNOWN_OVERSIZED_NO_SELECTOR, **load_profile(root)["known_oversized_no_selector"]}


def stale_known_oversized(root: Path, bodies: list[dict]) -> list[str]:
    """Listed KNOWN_OVERSIZED_NO_SELECTOR entries whose file is missing under `root`, or is no
    longer an oversized body without a selector."""
    current = {b["path"] for b in bodies if not b["has_selector"]}
    return [rel for rel in _known_oversized(root)
            if not (root.parent / rel).is_file() or rel not in current]


def _is_phase_gated(line: str) -> bool:
    low = line.lower()
    return any(cue in low for cue in PHASE_GATE_CUES)


def _resolve_reference(ref: str, root: Path, entrypoint_dir: Path) -> Path | None:
    stripped = ref[len(".claude/"):] if ref.startswith(".claude/") else ref
    if stripped.startswith("skills/") and not stripped.endswith(".md"):
        stripped = stripped.rstrip("/") + "/SKILL.md"
    for base in (root, entrypoint_dir):
        candidate = base / stripped
        if candidate.exists():
            return candidate
    return None


def transitive_load(entry_rel: str, root: Path) -> dict:
    entry_path = root / entry_rel
    text = entry_path.read_text(encoding="utf-8", errors="replace")
    entry_bytes = len(text.encode("utf-8"))
    entry_resolved = entry_path.resolve()
    referenced: dict[str, Path] = {}
    gated_refs: dict[str, Path] = {}
    for line in text.splitlines():
        gated = _is_phase_gated(line)
        for m in MD_REF_RE.finditer(line):
            resolved = _resolve_reference(m.group(1), root, entry_path.parent)
            if resolved is None:
                continue
            key = str(resolved.resolve())
            if key == str(entry_resolved):
                continue
            (gated_refs if gated else referenced).setdefault(key, resolved)
    ref_bytes = sum(p.stat().st_size for p in referenced.values())
    return {
        "entry": entry_rel,
        "entry_bytes": entry_bytes,
        "referenced": sorted(".claude/" + str(p.relative_to(root)).replace("\\", "/") for p in referenced.values()),
        "referenced_bytes": ref_bytes,
        "gated_excluded": sorted(".claude/" + str(p.relative_to(root)).replace("\\", "/") for p in gated_refs.values()),
        "total_bytes": entry_bytes + ref_bytes,
    }


# ---------------------------------------------------------------------------
# Hook chain (static) and hook profiling (executed)
# ---------------------------------------------------------------------------

def load_settings(root: Path) -> dict:
    """The composed settings document: the real `settings.json` when a consumer has one,
    else the S8 split (`settings.base.json` + `settings.project.json`) composed the same way
    `.claude/tests/_settings_probe.py` does -- the baseline template ships only the split and
    composes at install time, so a bare `root / "settings.json"` read cannot run there."""
    composed = root / "settings.json"
    if composed.is_file():
        return json.loads(composed.read_text(encoding="utf-8"))
    base_file = root / "settings.base.json"
    if not base_file.is_file():
        return json.loads(composed.read_text(encoding="utf-8"))
    tools_dir = Path(__file__).resolve().parent
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import baseline_compose
    base = json.loads(base_file.read_text(encoding="utf-8"))
    project_file = root / "settings.project.json"
    project = json.loads(project_file.read_text(encoding="utf-8")) if project_file.is_file() else {}
    overlays = [json.loads(path.read_text(encoding="utf-8"))
                for path in baseline_compose.settings_overlay_files(root, ("pure", "coding", "godot"))]
    return baseline_compose.compose_settings(base, project, root / "hooks", overlays)


EXACT_MATCHER_RE = re.compile(r"[A-Za-z0-9_|, -]+")


def hook_matches(matcher: str, tool: str) -> bool:
    """The client's PreToolUse/PostToolUse rule: a matcher of only `[A-Za-z0-9_|, -]` is an exact
    name list split on `[|,]`; any other matcher is an unanchored regex search."""
    if not matcher or matcher == "*":
        return True
    if EXACT_MATCHER_RE.fullmatch(matcher):
        return tool in {name.strip() for name in re.split(r"[|,]", matcher)}
    try:
        return re.search(matcher, tool) is not None
    except re.error:
        return False


def _hook_script(command: str) -> str:
    return command.split('"')[1] if '"' in command else command.split()[-1]


def _hook_args(command: str) -> list[str]:
    """The arguments after a registered command's script: `.../x.py" --part 1` gives `--part 1`."""
    return command.split('"', 2)[2].split() if command.count('"') >= 2 else []


def _hook_name(command: str) -> str:
    return os.path.basename(_hook_script(command))


def edit_hook_chain(settings: dict) -> dict:
    """Subprocess count per tool call, PreToolUse+PostToolUse combined. The
    budgeted number is the `Edit` count specifically (the quantity the plan's
    2026-09-14 baseline of 11 measured, via hook_profile.py's edit-cs /
    edit-harness-md cases); `Write` is reported alongside for visibility since
    at least one hook (`pre_read_dispatch.py`, matcher `Read|Grep|Glob|Write|
    ...`) fires on Write but not Edit, so the two counts can diverge."""
    counts = {"Write": 0, "Edit": 0}
    names: dict[str, list] = {"Write": [], "Edit": []}
    for event in ("PreToolUse", "PostToolUse"):
        for group in settings.get("hooks", {}).get(event, []):
            matcher = group.get("matcher", "*")
            for h in group.get("hooks", []):
                name = _hook_name(h.get("command", ""))
                for tool in ("Write", "Edit"):
                    if hook_matches(matcher, tool):
                        counts[tool] += 1
                        names[tool].append(f"{event}:{name}")
    return {"per_tool": counts, "names": names, "edit_subprocesses": counts["Edit"],
            "write_subprocesses": counts["Write"]}


def build_payloads(root: Path) -> dict:
    sid = "census0001"
    harness_md = root / "commands" / "session_end.md"
    payloads = {
        "UserPromptSubmit": [
            ("prompt", {"hook_event_name": "UserPromptSubmit", "session_id": sid,
                        "prompt": "Fix the null check in the config loader and add a test."}),
        ],
        "PreToolUse": [
            ("edit-harness-md", {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": "Edit",
                                  "tool_input": {"file_path": str(harness_md), "old_string": "a", "new_string": "b"}}),
            ("read-md", {"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": "Read",
                         "tool_input": {"file_path": str(harness_md)}}),
        ],
        "PostToolUse": [
            ("edit-harness-md", {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "Edit",
                                  "tool_input": {"file_path": str(harness_md), "old_string": "a", "new_string": "b"},
                                  "tool_response": {"filePath": str(harness_md)}}),
            ("read-md", {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "Read",
                         "tool_input": {"file_path": str(harness_md)}, "tool_response": {"content": "x" * 500}}),
        ],
    }
    probe = load_profile(root)["code_edit_probe"].get("path")
    if probe:
        edit = {"file_path": str(root.parent / probe), "old_string": "a", "new_string": "b"}
        payloads["PreToolUse"].insert(1, ("edit-code", {"hook_event_name": "PreToolUse", "session_id": sid,
                                                        "tool_name": "Edit", "tool_input": edit}))
        payloads["PostToolUse"].insert(1, ("edit-code", {"hook_event_name": "PostToolUse", "session_id": sid,
                                                         "tool_name": "Edit", "tool_input": edit,
                                                         "tool_response": {"filePath": "x"}}))
    return payloads


def _hook_sets(root: Path) -> tuple[set, set]:
    """(skipped, isolated) shared-state hook names: the module defaults plus the profiles'."""
    profile = load_profile(root)
    return (SHARED_STATE_HOOK_SKIP | set(profile["shared_state_hook_skip"]),
            ISOLATED_SHARED_STATE_HOOKS | set(profile["isolated_shared_state_hooks"]))


def _isolated_hook_env(base_env: dict, iso_root: Path) -> dict:
    """Extra env vars that redirect a shared-state hook's every write seam
    into `iso_root`, never real machine state: CLAUDE_PROJECT_DIR (so a
    `.claude/.cache` write lands under a throwaway project dir, not the real
    one), HARNESS_HOOK_STATE_DIR (the per-session state `_hook_state.py` already
    honors), and HOME/USERPROFILE/TEMP/TMP/TMPDIR (both `tempfile.gettempdir()`
    and `os.path.expanduser('~')` read these on Windows and POSIX)."""
    fake_project = iso_root / "project"
    (fake_project / ".claude").mkdir(parents=True, exist_ok=True)
    fake_state = iso_root / "state"
    fake_state.mkdir(parents=True, exist_ok=True)
    fake_home = iso_root / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_tmp = iso_root / "tmp"
    fake_tmp.mkdir(parents=True, exist_ok=True)
    env = dict(base_env)
    env.update({
        "CLAUDE_PROJECT_DIR": str(fake_project),
        "HARNESS_HOOK_STATE_DIR": str(fake_state),
        "HOME": str(fake_home),
        "USERPROFILE": str(fake_home),
        "TEMP": str(fake_tmp),
        "TMP": str(fake_tmp),
        "TMPDIR": str(fake_tmp),
    })
    return env


def _run_hook_once(script: str, payload: dict, env: dict, cwd: str | None = None,
                   timeout: float = 60, args=()) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, script, *args], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
                            encoding="utf-8", errors="replace")
        ms = int((time.time() - t0) * 1000)
        return {"status": r.returncode, "ms": ms,
                "stdout_bytes": len((r.stdout or "").encode()),
                "stderr_bytes": len((r.stderr or "").encode())}
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "ms": int(timeout * 1000), "stdout_bytes": 0, "stderr_bytes": 0}


DISPATCHER_NAME = "prompt_dispatch.py"


def _read_dispatch_chain(dispatcher: Path) -> tuple[list[tuple[str, float]], str | None]:
    """(members, None), or ([], reason) when the dispatcher's module-level `CHAIN` literal
    cannot be read. Parsed with `ast`, never imported or run."""
    try:
        tree = ast.parse(dispatcher.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return [], f"{dispatcher.name} not readable ({type(exc).__name__}: {exc})"
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in targets):
            value = node.value
    if value is None:
        return [], "no module-level CHAIN assignment"
    try:
        chain = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return [], "CHAIN is not a literal"
    if not isinstance(chain, (tuple, list)) or not chain:
        return [], "CHAIN is not a non-empty sequence"
    members = []
    for m in chain:
        ok = (isinstance(m, (tuple, list)) and len(m) == 2 and isinstance(m[0], str) and m[0]
              and os.path.basename(m[0]) == m[0] and isinstance(m[1], (int, float))
              and not isinstance(m[1], bool) and m[1] > 0)
        if not ok:
            return [], f"CHAIN member {m!r} is not (module basename, budget seconds)"
        members.append((m[0], float(m[1])))
    return members, None


def _hook_entries(h: dict, root: Path, expand_dispatcher: bool) -> list[dict]:
    """One profiling entry per hook a registered command runs: `script`, `name`, `timeout`
    (seconds) and `unreadable` (a reason, else None). A `prompt_dispatch.py` command expands
    into its `CHAIN` members under their `CHAIN` budgets, so each is profiled as if registered
    directly, and a member with no file in the tree is skipped; an unreadable `CHAIN` yields one entry for the dispatcher carrying the reason."""
    script = _hook_script(h.get("command", "")).replace("$CLAUDE_PROJECT_DIR", str(root.parent))
    name = os.path.basename(script)
    if not (expand_dispatcher and name == DISPATCHER_NAME):
        return [{"script": script, "name": name, "timeout": float(h.get("timeout") or 60),
                 "unreadable": None}]
    members, reason = _read_dispatch_chain(Path(script))
    if reason:
        return [{"script": script, "name": name, "timeout": 0.0, "unreadable": reason}]
    hooks_dir = Path(script).parent
    return [{"script": str(hooks_dir / f"{module}.py"), "name": f"{module}.py",
             "timeout": budget, "unreadable": None}
            for module, budget in members if (hooks_dir / f"{module}.py").is_file()]


def profile_user_prompt_hooks_two_pass(root: Path, settings: dict) -> list[dict]:
    """Every registered UserPromptSubmit hook, run TWICE against one shared
    HARNESS_HOOK_STATE_DIR/session id: pass 1 is the first prompt of a session,
    pass 2 is the steady-state floor an unchanged later prompt pays (a fire-once
    hook such as `budget_posture.py` emits on pass 1 and stays silent on
    pass 2). `SHARED_STATE_HOOK_SKIP` and `NON_ISOLATABLE_SHARED_STATE_HOOKS`
    hooks are not run at all, each reported with its reason.
    `ISOLATED_SHARED_STATE_HOOKS` hooks run for real against a throwaway
    project/home/temp tree built by `_isolated_hook_env`, reused across both
    passes so any shared-state behavior between prompts still shows up.
    A registered `prompt_dispatch.py` is expanded into its `CHAIN` members, each
    run under its own budget and isolation rule (see `_hook_entries`).
    Only the first synthetic case per hook is used (UserPromptSubmit has one
    case in `build_payloads`)."""
    state_dir = tempfile.mkdtemp(prefix="load_census_hookstate_")
    iso_root = Path(tempfile.mkdtemp(prefix="load_census_isohook_"))
    try:
        _label, payload = build_payloads(root)["UserPromptSubmit"][0]
        skipped, isolated = _hook_sets(root)
        # A capacity warm started by pass 1 can land before pass 2 and re-emit the posture line.
        base_env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir, HARNESS_CAPACITY_WARM="0",
                         CLAUDE_PROJECT_DIR=str(root.parent), PYTHONIOENCODING="utf-8",
                         # Hooks that cache under the project's .claude/.cache write into the run's own state dir instead.
                         HARNESS_GIT_STATE_CACHE_DIR=os.path.join(state_dir, "git-state-cache"),
                         HARNESS_REAPER_CACHE_DIR=os.path.join(state_dir, "reaper-cache"))
        base_env.pop("CLAUDE_CODE_SESSION_ID", None)
        rows = []
        for group in settings.get("hooks", {}).get("UserPromptSubmit", []):
            for h in group.get("hooks", []):
                for entry in _hook_entries(h, root, expand_dispatcher=True):
                    script, name = entry["script"], entry["name"]
                    row = {"hook": name, "isolated": False, "run_note": None,
                           "excluded_from_budget": False, "exclude_reason": None,
                           "status_first": "-", "status_steady": "-",
                           "ms_first": 0, "ms_steady": 0,
                           "stdout_bytes_first": 0, "stdout_bytes_steady": 0}
                    if entry["unreadable"]:
                        # Not "-": a "-" row is skipped by unmeasured_prompt_hooks, and an
                        # unreadable CHAIN must not pass the emission budget as silence.
                        row["run_note"] = f"unreadable(CHAIN: {entry['unreadable']})"
                        row["status_first"] = row["status_steady"] = "UNREADABLE"
                        rows.append(row)
                        continue
                    if name in skipped:
                        row["run_note"] = "skipped(shared-state, no env seam confirmed)"
                        rows.append(row)
                        continue
                    if name in NON_ISOLATABLE_SHARED_STATE_HOOKS:
                        row["run_note"] = f"skipped(cannot-isolate: {NON_ISOLATABLE_SHARED_STATE_HOOKS[name]})"
                        rows.append(row)
                        continue
                    if name in isolated:
                        hook_iso_root = iso_root / name
                        hook_iso_root.mkdir(parents=True, exist_ok=True)
                        env = _isolated_hook_env(base_env, hook_iso_root)
                        # A hook that resolves its root from the payload `cwd` or os.getcwd()
                        # must see the throwaway project, not this checkout.
                        hook_cwd = env["CLAUDE_PROJECT_DIR"]
                        hook_payload = dict(payload, cwd=hook_cwd)
                        row["isolated"] = True
                        row["run_note"] = "isolated(shared-state, throwaway project/home/temp)"
                    else:
                        env = base_env
                        hook_cwd, hook_payload = None, payload
                    if name in LIVE_MACHINE_HOOKS:
                        row["excluded_from_budget"] = True
                        row["exclude_reason"] = LIVE_MACHINE_HOOKS[name]
                    # The live harness kills a hook at its registered timeout (seconds); a
                    # dispatcher member is killed at its CHAIN budget.
                    hook_timeout = entry["timeout"]
                    first = _run_hook_once(script, hook_payload, env, cwd=hook_cwd, timeout=hook_timeout)
                    steady = _run_hook_once(script, hook_payload, env, cwd=hook_cwd, timeout=hook_timeout)
                    row["status_first"], row["ms_first"], row["stdout_bytes_first"] = (
                        first["status"], first["ms"], first["stdout_bytes"])
                    row["status_steady"], row["ms_steady"], row["stdout_bytes_steady"] = (
                        steady["status"], steady["ms"], steady["stdout_bytes"])
                    rows.append(row)
        return rows
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
        shutil.rmtree(iso_root, ignore_errors=True)


def compute_prompt_emission(rows: list[dict]) -> tuple[int, int, list[dict]]:
    """(first_total, steady_total, excluded_rows). A row that was skipped
    (`status_first == "-"`) contributes nothing. A row `excluded_from_budget`
    (a live-machine hook) contributes nothing to either total and is returned
    separately so its number is still printed."""
    ran = [r for r in rows if r["status_first"] != "-"]
    excluded = [r for r in ran if r["excluded_from_budget"]]
    counted = [r for r in ran if not r["excluded_from_budget"]]
    first_total = sum(r["stdout_bytes_first"] for r in counted)
    steady_total = sum(r["stdout_bytes_steady"] for r in counted)
    return first_total, steady_total, excluded


def unmeasured_prompt_hooks(rows: list[dict]) -> list[str]:
    """Budgeted hooks that ran but did not exit 0 on both passes (crash, non-zero exit,
    TIMEOUT). Their zero bytes mean "not measured", so the emission budget cannot pass on them."""
    bad = []
    for r in rows:
        if r["status_first"] == "-" or r["excluded_from_budget"]:
            continue
        if r["status_first"] not in (0, "0") or r["status_steady"] not in (0, "0"):
            bad.append(f'{r["hook"]} (exit {r["status_first"]}/{r["status_steady"]})')
    return bad


# SessionStart scripts the census does not run, by registered script name, with the reason. Every other registered
# SessionStart command runs isolated once per case and its stdout is budgeted at HOOK_TEXT_CAP. A profile's
# `session_start_skip` map extends this: its names come from that layer's own registrations.
SESSION_START_SKIP = {
    "session_context_loader.py": "can start a build, so a synthetic run is not side-effect free",
    "login_parity_check.py": "spawns a detached process that outlives the run",
    "compact_directive_anchor.py": "writes the full-transcript file and a run log; its output depends on a real compaction transcript",
    "sidecar_recompact_reprompt.py": "its output depends on a recompact brief the census cannot plant; it keeps its own proof",
    "subagent_compaction_rail.py": "its one print sits behind a PreCompact marker the census cannot plant; it keeps its own proof",
}

SESSION_START_SID = "census0001"
# Env a SessionStart case must not inherit from the census's own process: a delegate, transport or state seam set in
# the caller's shell would change which text the case prints. HOME stays real: the rails text reads the ai-worker
# registration under it, so an isolated HOME prints a different text.
_CASE_ENV_PREFIXES = ("CLAUDE_CODE_", "ANTHROPIC_", "CC_", "HARNESS_")


def _session_start_skips(root: Path) -> dict[str, str]:
    """The tool's default skip map plus every profile's `session_start_skip` entries."""
    return {**SESSION_START_SKIP, **load_profile(root)["session_start_skip"]}


def _model_registry():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_registry
    return model_registry


def _session_start_payload(model, source: str) -> dict:
    body = {"hook_event_name": "SessionStart", "session_id": SESSION_START_SID, "source": source}
    if model:
        body["model"] = model
    return body


def _session_start_env(overrides: dict, iso_root: Path) -> dict:
    """The environment one SessionStart case runs in: the caller's env minus the harness seams, throwaway project,
    state, cache and temp dirs, then the case's own overrides."""
    project = iso_root / "project"
    state = iso_root / "state"
    tmp = iso_root / "tmp"
    for d in (project / ".claude", state, tmp):
        d.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(_CASE_ENV_PREFIXES)}
    env.update({"CLAUDE_PROJECT_DIR": str(project), "HARNESS_HOOK_STATE_DIR": str(state),
                "CC_CACHESTAT_DIR": str(state), "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp),
                "PYTHONIOENCODING": "utf-8"})
    env.update(overrides)
    return env


def _default_session_start_cases(root: Path, matcher: str) -> list[tuple[str, dict, dict]]:
    """A group with no matcher fires at every source, so both the startup and the compact path run; a
    matched group runs the one path its matcher names."""
    if matcher in (None, "", "*"):
        sources = ("startup", "compact")
    else:
        sources = ("compact",) if matcher == "compact" else ("startup",)
    model = _widest_notes_model(root)
    return [(source, {}, _session_start_payload(model, source)) for source in sources]


def _widest_notes_model(root: Path):
    """The registry model whose driver notes add the most text to the rails (None when no row carries notes). The
    notes block is the only part of the rails text that depends on the model, so the widest one bounds it."""
    registry = _model_registry()
    noted = [row["id"] for row in registry.load(start=root)["models"] if registry.driver_notes(row["id"])]
    return max(noted, key=lambda m: sum(len(text) + len(ref) for text, ref in registry.driver_notes(m)),
               default=None)


def _sidecar_kind_per_guard(root: Path) -> dict[str, str]:
    """One sidecar kind per guard the kinds name, plus `common` as the empty kind (an unrecognized kind reads as common)."""
    profiles = root / "guards" / "tool_profiles.json"
    kinds = json.loads(profiles.read_text(encoding="utf-8"))["kinds"] if profiles.is_file() else {}
    per_guard = {"common": ""}
    for name in sorted(kinds):
        guard = kinds[name].get("guard")
        if guard:
            per_guard.setdefault(guard, name)
    return per_guard


def _session_model_rails_cases(root: Path) -> list[tuple[str, dict, dict]]:
    """Every shape the rails hook prints: each model tier and each model with driver notes as an orchestrator, an
    absent model, a compaction, each registered transport, and each sidecar delegate guard on a model of each registry rail tier and on the widest-notes model."""
    registry = _model_registry()
    rows = registry.load(start=root)["models"]
    tier_rep = {}
    for row in rows:
        tier_rep.setdefault(registry.rail_tier(row["id"]), row["id"])
    noted = [row["id"] for row in rows if registry.driver_notes(row["id"])]
    widest = _widest_notes_model(root)
    cases = [("orchestrator model=%s" % m, {}, _session_start_payload(m, "startup"))
             for m in dict.fromkeys([*tier_rep.values(), *noted])]
    cases.append(("orchestrator model=absent", {}, _session_start_payload(None, "startup")))
    cases.append(("orchestrator compact", {}, _session_start_payload(widest, "compact")))
    for transport in sorted(registry.load(start=root)["transports"]):
        cases.append(("transport=%s" % transport, {"CLAUDE_CODE_TRANSPORT": transport},
                      _session_start_payload(widest, "startup")))
    for guard, kind in _sidecar_kind_per_guard(root).items():
        # A delegate reads its own model's row tier, as every session does; the widest notes are the largest text.
        for model in dict.fromkeys([*tier_rep.values(), widest]):
            overrides = {"CLAUDE_CODE_SIDECAR": "1", "CLAUDE_CODE_SIDECAR_KIND": kind}
            cases.append(("delegate guard=%s model=%s (tier %s)" % (guard, model, registry.rail_tier(model)),
                          overrides, _session_start_payload(model, "startup")))
    return cases


SESSION_START_CASES = {"session_model_rails.py": _session_model_rails_cases}


def profile_session_start_hooks(root: Path, settings: dict) -> tuple[list[dict], list[dict], list[str]]:
    """(rows, skipped, source_errors). One row per registered SessionStart command per case: the command runs
    isolated with the case's env and payload and its stdout bytes are budgeted at HOOK_TEXT_CAP. A script named
    in the skip map is returned in `skipped` with its reason and never run. A case builder that cannot read its
    input becomes one named source error beside every other case's rows, never a traceback out of build_report."""
    rows, skipped, source_errors = [], [], []
    skips = _session_start_skips(root)
    iso_base = Path(tempfile.mkdtemp(prefix="load_census_sessionstart_"))
    try:
        for group in settings.get("hooks", {}).get("SessionStart", []):
            matcher = group.get("matcher", "*")
            for h in group.get("hooks", []):
                for entry in _hook_entries(h, root, expand_dispatcher=False):
                    name = entry["name"]
                    if name in skips:
                        skipped.append({"hook": name, "reason": skips[name]})
                        continue
                    args = _hook_args(h.get("command", ""))
                    try:
                        cases = (SESSION_START_CASES[name](root) if name in SESSION_START_CASES
                                 else _default_session_start_cases(root, matcher))
                    except Exception as exc:  # noqa: BLE001 - a bad input is a row, never a traceback
                        # The model registry raises its own RegistryError for a malformed file, so the
                        # ruled input tuple alone would still crash the whole report.
                        registry_error = getattr(_model_registry(), "RegistryError", ())
                        if not isinstance(exc, (OSError, ValueError, KeyError, AttributeError, registry_error)):
                            raise
                        source_errors.append(
                            f"session_start case input invalid: {name}: {type(exc).__name__}: {exc}")
                        continue
                    for i, (label, overrides, body) in enumerate(cases):
                        iso = iso_base / ("%s-%d" % (name, i))
                        run = _run_hook_once(entry["script"], body, _session_start_env(overrides, iso),
                                             cwd=str(iso / "project"), timeout=entry["timeout"], args=args)
                        rows.append({"hook": name, "args": " ".join(args), "case": label,
                                     "source": body.get("source"),
                                     "status": run["status"], "stdout_bytes": run["stdout_bytes"]})
        return rows, skipped, source_errors
    finally:
        shutil.rmtree(iso_base, ignore_errors=True)


def session_start_injection(rows: list[dict], standing_bytes: int = 0) -> dict:
    """What one reader pays at SessionStart, per reader.

    A reader is a case the census runs: the model, transport or delegate guard+tier a hook varies its
    text by, or, for a hook whose text does not vary, the startup/compact path. A reader's `bytes` sum
    every registered command's measured stdout in its case -- the parts of one hook included, so it is
    the text that reader's client injects, not one command's share -- plus the commands whose text does
    not vary by reader at that source. `load_bytes` adds the standing total: the least that session
    loads before its task. A command that did not exit 0 is not counted; `unmeasured` names it."""
    ran = [r for r in rows if r.get("status") in (0, "0")]
    unmeasured = sorted({f"{r['hook']} [{r['case']}]" for r in rows if r.get("status") not in (0, "0")})
    common: dict[str, int] = {}
    readers: dict[tuple[str, str], int] = {}
    for row in ran:
        source = row.get("source") or row["case"]
        if row["case"] == source:
            common[source] = common.get(source, 0) + row["stdout_bytes"]
        else:
            key = (row["case"], source)
            readers[key] = readers.get(key, 0) + row["stdout_bytes"]
    covered = {source for _, source in readers}
    out = []
    for (label, source), own in readers.items():
        total = own + common.get(source, 0)
        out.append({"reader": label, "source": source, "bytes": total, "load_bytes": standing_bytes + total})
    for source, total in common.items():
        if source not in covered:
            out.append({"reader": source, "source": source, "bytes": total,
                        "load_bytes": standing_bytes + total})
    out.sort(key=lambda row: (-row["bytes"], row["reader"]))
    return {"rows": out, "unmeasured": unmeasured}


def profile_hooks(root: Path, settings: dict, events: list[str] | None = None) -> list[dict]:
    """General hook profiler for PreToolUse/PostToolUse (and, unisolated,
    UserPromptSubmit for callers that only need skip/shape parity -- the
    budgeted, isolated UserPromptSubmit measurement lives in
    `profile_user_prompt_hooks_two_pass`, never here). Both
    `ISOLATED_SHARED_STATE_HOOKS` and `NON_ISOLATABLE_SHARED_STATE_HOOKS`
    hooks are skipped here, same as `SHARED_STATE_HOOK_SKIP` -- this function
    has no isolation seam, so running them for real here would touch real
    machine-wide state."""
    state_dir = tempfile.mkdtemp(prefix="load_census_hookstate_")
    try:
        payloads = build_payloads(root)
        skipped, isolated = _hook_sets(root)
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir, CLAUDE_PROJECT_DIR=str(root.parent),
                   PYTHONIOENCODING="utf-8")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        rows = []
        for event, cases in payloads.items():
            if events is not None and event not in events:
                continue
            for group in settings.get("hooks", {}).get(event, []):
                matcher = group.get("matcher", "*")
                for h in group.get("hooks", []):
                    for entry in _hook_entries(h, root, expand_dispatcher=event == "UserPromptSubmit"):
                        script, name = entry["script"], entry["name"]
                        if entry["unreadable"]:
                            rows.append({"event": event, "case": "-", "hook": name,
                                         "status": f"unreadable(CHAIN: {entry['unreadable']})",
                                         "ms": 0, "stdout_bytes": 0, "stderr_bytes": 0})
                            continue
                        if name in skipped or name in isolated or name in NON_ISOLATABLE_SHARED_STATE_HOOKS:
                            rows.append({"event": event, "case": "-", "hook": name,
                                         "status": "skipped(shared-state)", "ms": 0,
                                         "stdout_bytes": 0, "stderr_bytes": 0})
                            continue
                        for label, payload in cases:
                            tool = payload.get("tool_name", "")
                            if event != "UserPromptSubmit" and not hook_matches(matcher, tool):
                                continue
                            t0 = time.time()
                            hook_timeout = entry["timeout"]
                            try:
                                r = subprocess.run([sys.executable, script], input=json.dumps(payload),
                                                    capture_output=True, text=True, timeout=hook_timeout, env=env,
                                                    encoding="utf-8", errors="replace")
                                ms = int((time.time() - t0) * 1000)
                                rows.append({"event": event, "case": label, "hook": name, "status": r.returncode,
                                             "ms": ms, "stdout_bytes": len((r.stdout or "").encode()),
                                             "stderr_bytes": len((r.stderr or "").encode())})
                            except subprocess.TimeoutExpired:
                                rows.append({"event": event, "case": label, "hook": name, "status": "TIMEOUT",
                                             "ms": int(hook_timeout * 1000), "stdout_bytes": 0, "stderr_bytes": 0})
        return rows
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Report assembly, printing, budgets
# ---------------------------------------------------------------------------

def build_report(root: Path, profile_hooks_flag: bool = False, need_budget_hooks: bool = False,
                 need_session_start_hooks: bool = False, home: Path | None = None) -> dict:
    """The census report. `--profile-hooks` sets both needs; `--budgets` sets both because it judges the
    SessionStart emission cap; the close's tripwire-only caller sets `need_budget_hooks` alone and pays
    no SessionStart matrix (`over_baseline` reads only null-cap budgets, and no SessionStart budget is one)."""
    settings = load_settings(root)
    profile = load_profile(root)
    standing = standing_census(root, home=home)
    rules = rules_census(root)
    transitive = {}
    source_errors = (list(profile["source_errors"]) + list(standing.get("source_errors", []))
                     + list(rules.get("source_errors", [])))
    for entry in ENTRYPOINTS + [e for e in profile["entrypoints"] if e not in ENTRYPOINTS]:
        path = root / entry
        if not path.is_file():
            source_errors.append(f"missing entrypoint: {entry}")
            continue
        try:
            transitive[entry] = transitive_load(entry, root)
        except OSError as exc:
            source_errors.append(f"unreadable entrypoint: {entry}: {exc}")
    bodies = oversized_bodies(root)
    report = {
        "root": str(root),
        "listing": listing_census(root),
        "standing": standing,
        "rules": rules,
        "oversized_bodies": bodies,
        "reattach_over_cap": reattach_over_cap(root),
        "known_oversized_stale": stale_known_oversized(root, bodies),
        "transitive_load": transitive,
        "hook_chain": edit_hook_chain(settings),
        "budgets": {**BUDGETS, **{k: tuple(v) for k, v in profile["budgets"].items()}},
        "budget_sources": {**{name: _repo_rel(root, root / "tools" / "load_census.py")
                              for name in BUDGETS},
                           **profile["budget_sources"]},
        "source_errors": source_errors,
    }
    if profile_hooks_flag:
        report["hook_profile"] = profile_hooks(root, settings, events=["PreToolUse", "PostToolUse"])
    if profile_hooks_flag or need_budget_hooks:
        prompt_rows = profile_user_prompt_hooks_two_pass(root, settings)
        first_total, steady_total, excluded = compute_prompt_emission(prompt_rows)
        report["prompt_hook_rows"] = prompt_rows
        report["prompt_hooks_unmeasured"] = unmeasured_prompt_hooks(prompt_rows)
        report["prompt_emission_first_bytes"] = first_total
        report["prompt_emission_steady_bytes"] = steady_total
        report["prompt_emission_excluded"] = [
            {"hook": r["hook"], "reason": r["exclude_reason"],
             "first_bytes": r["stdout_bytes_first"], "steady_bytes": r["stdout_bytes_steady"]}
            for r in excluded
        ]
    # A tripwire-only caller (the close's hot-set review) needs the prompt profile but no SessionStart
    # matrix: `over_baseline` reads only null-cap budgets, and no SessionStart budget is one.
    if profile_hooks_flag or need_session_start_hooks:
        rows, skipped, errors = profile_session_start_hooks(root, settings)
        report["session_start_rows"], report["session_start_skipped"] = rows, skipped
        report["source_errors"].extend(errors)
        report["session_start_injection"] = session_start_injection(
            rows, report["standing"].get("total_bytes", 0))
    return report


def _memory_row(report: dict) -> dict:
    """The standing row of the memory index, or {} when the index is absent."""
    return next((r for r in (report.get("standing") or {}).get("rows") or []
                 if r.get("exists") and Path(r["path"]).parts[-2:] == ("auto-memory", "MEMORY.md")), {})


def measured_budgets(report: dict) -> dict:
    """The live value of each budget key the report measures. A key the report does not measure (no hook
    profiling this run) maps to None; a tripwire then gives an `unmeasured` row, never a silent skip."""
    memory_row = _memory_row(report)
    standing = report.get("standing") or {}
    values = {
        "listing_bytes": (report.get("listing") or {}).get("total_bytes"),
        "standing_bytes": standing.get("total_bytes"),
        # The standing census always emits rows; with rows present and no memory row, the index is absent (0).
        "memory_hot_bytes": (memory_row.get("bytes") if memory_row
                             else (0 if "rows" in standing else None)),
        "edit_hook_chain_subprocesses": (report.get("hook_chain") or {}).get("edit_subprocesses"),
        "prompt_emission_bytes": report.get("prompt_emission_steady_bytes"),
        "session_start_emission_bytes": max((r["stdout_bytes"] for r in report.get("session_start_rows") or []),
                                            default=0),
    }
    for name, bundle in (report.get("rules") or {}).get("bundles", {}).items():
        values[f"{name}_rule_bundle_bytes"] = bundle["bytes"]
    return values


def _tripwire_files(report: dict, budget: str) -> list[str]:
    """The files a tripwire's review must inspect. Each budget's resolver sits beside its own key in one
    map, so a budget cannot fall through to a different budget's files; a bundle key resolves from the
    report's own bundle rows. An unknown key names no files."""
    root = Path(report["root"]) if report.get("root") else DEFAULT_ROOT

    def memory_hot_files() -> list[str]:
        row = _memory_row(report)
        return [_repo_rel(root, Path(row["path"]))] if row else []

    def standing_files() -> list[str]:
        return [_repo_rel(root, Path(r["path"])) for r in report["standing"]["rows"] if r.get("exists")]

    def listing_files() -> list[str]:
        return [r["path"] for r in report["listing"]["rows"] if r.get("path")]

    def prompt_hook_files() -> list[str]:
        return sorted({_repo_rel(root, root / "hooks" / r["hook"])
                       for r in report.get("prompt_hook_rows") or []
                       if r.get("stdout_bytes_steady") and not r.get("excluded_from_budget")})

    def session_start_files() -> list[str]:
        return sorted({_repo_rel(root, root / "hooks" / r["hook"])
                       for r in report.get("session_start_rows") or [] if r.get("status") in (0, "0")})

    resolvers = {
        "memory_hot_bytes": memory_hot_files,
        "standing_bytes": standing_files,
        "listing_bytes": listing_files,
        "prompt_emission_bytes": prompt_hook_files,
        "session_start_emission_bytes": session_start_files,
        "edit_hook_chain_subprocesses": lambda: [_repo_rel(root, root / "settings.json")],
    }
    for name in (report.get("rules") or {}).get("bundles", {}):
        resolvers[f"{name}_rule_bundle_bytes"] = (
            lambda name=name: [_repo_rel(root, root / "rules" / f)
                               for f in report["rules"]["bundles"][name]["files"]])
    resolver = resolvers.get(budget)
    return resolver() if resolver else []


def _baseline_commit(root: Path, budget: str, baseline: int, source: str) -> str | None:
    """The last commit that recorded this budget's baseline literal in `source`: the project profile's
    `[null, N]` when the profile supplies the budget, else the `BUDGETS` `(None, N)` pair. A commit that
    removed the literal does not count, so a re-base names the commit that set the value now asked about."""
    if source.endswith(".json"):
        number = r"_?".join(str(baseline))
        flag = "-G"
        pickaxe = added_re = rf'"{re.escape(budget)}"\s*:\s*\[\s*null\s*,\s*{number}\s*\]'
    else:
        flag = "-S"
        pickaxe = f'"{budget}": (None, {baseline:_}),'
        added_re = re.escape(pickaxe)
    cwd = str(root.parent)
    try:
        args = ["log", "--format=%H", flag, pickaxe, "--", source]
        commits = git_run(args, cwd=cwd, timeout=30).check(args).text.split()
        for sha in commits:
            show = ["show", sha, "--format=", "--", source]
            diff = git_run(show, cwd=cwd, timeout=30).check(show).text
            if any(line.startswith("+") and re.search(added_re, line) for line in diff.splitlines()):
                return sha
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def over_baseline(report: dict) -> list[dict]:
    """One row per budget whose cap is None (a tripwire) and whose measured value exceeds its recorded
    baseline, plus one `oversized_body` row per selectorless body past OVERSIZED_THRESHOLD or past its
    KNOWN_OVERSIZED_NO_SELECTOR bytes by more than KNOWN_OVERSIZED_GROWTH. A tripwire the report cannot
    measure gives an `error: unmeasured` row."""
    root = Path(report["root"]) if report.get("root") else DEFAULT_ROOT
    budgets = report.get("budgets") or BUDGETS
    sources = report.get("budget_sources") or {}
    base_tool = _repo_rel(root, root / "tools" / "load_census.py")
    measured = measured_budgets(report)
    rows = []
    for budget, (cap, baseline) in budgets.items():
        if cap is not None:
            continue
        value = measured.get(budget)
        if value is None:
            rows.append({"budget": budget, "measured": None, "baseline": baseline, "delta": None,
                         "error": "unmeasured"})
            continue
        if value <= baseline:
            continue
        rows.append({"budget": budget, "measured": value, "baseline": baseline,
                     "delta": value - baseline, "files": _tripwire_files(report, budget),
                     "baselineCommit": _baseline_commit(root, budget, baseline,
                                                        sources.get(budget, base_tool))})
    for b in report.get("oversized_bodies") or []:
        if b["has_selector"] or b["known"]:
            continue
        baseline = b["known_bytes"] if b["known_bytes"] is not None else OVERSIZED_THRESHOLD
        rows.append({"budget": "oversized_body", "measured": b["bytes"], "baseline": baseline,
                     "delta": b["bytes"] - baseline, "files": [b["path"]]})
    return rows


def check_budgets(report: dict) -> list[str]:
    fails = []
    budgets = report.get("budgets") or BUDGETS
    measured = measured_budgets(report)
    for source_error in report.get("source_errors", []):
        fails.append(f"source_invalid: {source_error}")
    for key, (cap, _baseline) in budgets.items():
        if cap is None or key == "session_start_emission_bytes":
            continue
        value = measured.get(key)
        if value is None:
            fails.append(f"{key} unmeasured (a cap budget the report cannot measure is not within cap)")
        elif value > cap:
            fails.append(f"{key} {value:,} > {cap:,}")
    cap = budgets.get("session_start_emission_bytes", (HOOK_TEXT_CAP, 0))[0]
    for row in report.get("session_start_rows") or []:
        where = f"{row['hook']} {row['args']} [{row['case']}]".replace("  ", " ")
        if row["status"] not in (0, "0"):
            fails.append(f"session_start_emission_bytes unmeasured: {where} exit {row['status']} "
                         "(a SessionStart command that did not exit 0 is not silent)")
        elif row["stdout_bytes"] > cap:
            fails.append(f"session_start_emission_bytes {row['stdout_bytes']:,} > {cap:,}: {where} "
                         "(the client shows a preview past HOOK_TEXT_CAP)")
    unmeasured = report.get("prompt_hooks_unmeasured") or []
    if unmeasured:
        fails.append("prompt_emission_bytes unmeasured (a hook that did not exit 0 is not silent): "
                     + ", ".join(unmeasured))
    stale_known = report.get("known_oversized_stale") or []
    if stale_known:
        fails.append("KNOWN_OVERSIZED_NO_SELECTOR lists bodies that are missing or no longer oversized without "
                     "a selector; remove them so regrowth is reported: " + ", ".join(stale_known))
    return fails


def check_listing_caps(report: dict) -> list[dict]:
    """Every listing row over its kind's LISTING_CAPS cap. Reuses listing_census()'s rows
    (built from `description`/`when_to_use`/`workflow_meta`); always computed, but only
    turned into a --budgets failure when --caps is passed (see main())."""
    over = []
    for r in (report.get("listing") or {}).get("rows", []):
        cap = LISTING_CAPS.get(r["kind"])
        if cap is not None and r["desc_bytes"] > cap:
            over.append({"kind": r["kind"], "name": r["name"], "bytes": r["desc_bytes"], "cap": cap})
    return over


def print_report(report: dict, profile_hooks_flag: bool) -> None:
    listing = report["listing"]
    print("== Always-loaded listing (frontmatter descriptions) ==")
    print(f"commands: {listing['commands']}  skills: {listing['skills']}  "
          f"workflows: {listing.get('workflows', 0)}  entries: {listing['count']}")
    print(f"description bytes total: {listing['total_bytes']:,}")
    print("top 12 descriptions by bytes:")
    for r in listing["top12"]:
        print("  %-8s %-45s desc=%5d" % (r["kind"], r["name"], r["desc_bytes"]))

    print("\n== Standing files ==")
    for r in report["standing"]["rows"]:
        print("  %-60s %6d%s" % (r["path"], r["bytes"], "" if r["exists"] else "  (missing)"))
    print(f"total: {report['standing']['total_bytes']:,}")
    if report.get("source_errors"):
        print("source errors:")
        for error in report["source_errors"]:
            print(f"  - {error}")

    print("\n== rules/**/*.md by paths: family ==")
    for fam in report["rules"]["families"]:
        print(f"  {fam['total_bytes']:6,} B  {fam['paths']}  {fam['files']}")
    for name, bundle in report["rules"].get("bundles", {}).items():
        print(f"{name} bundle: {bundle['bytes']:,} B  {bundle['files']}")

    print(f"\n== Bodies over {OVERSIZED_THRESHOLD:,} B (review trigger) ==")
    for b in report["oversized_bodies"]:
        flags = []
        if b["has_selector"]:
            flags.append("selector")
        if b["known"]:
            flags.append("known")
        print("  %8d  %-55s %s" % (b["bytes"], b["path"], ",".join(flags) or "no selector"))

    print(f"\n== Bodies over {SKILL_REATTACH_CHARS:,} characters (client re-attach cap) ==")
    print("  Counted in characters, not bytes: independent of the byte-based oversized review trigger above, "
          "so a body can trip either. The client cuts a body over the cap when it re-attaches it after a compaction.")
    for b in report.get("reattach_over_cap") or []:
        print("  %8d  %s" % (b["chars"], b["path"]))
    if not report.get("reattach_over_cap"):
        print("  none")

    print("\n== Transitive load of top entrypoints ==")
    for e, t in report["transitive_load"].items():
        print(f"  {e}: entry {t['entry_bytes']:,} B + {len(t['referenced'])} unconditional refs "
              f"({t['referenced_bytes']:,} B) = {t['total_bytes']:,} B  "
              f"[{len(t['gated_excluded'])} phase-gated refs ignored]")

    print("\n== Write|Edit hook chain (static, from settings.json) ==")
    hc = report["hook_chain"]
    print(f"  Edit subprocesses (budgeted): {hc['edit_subprocesses']}")
    print(f"  Write subprocesses (informational -- pre_read_dispatch.py's broader matcher can diverge from Edit): "
          f"{hc['write_subprocesses']}")
    for tool, n in hc["per_tool"].items():
        print(f"  {tool}: {n}  {hc['names'][tool]}")

    if profile_hooks_flag and "hook_profile" in report:
        print("\n== Hook profile (synthetic payloads, PreToolUse/PostToolUse) ==")
        print("%-16s %-16s %-36s %-10s %6s %7s %7s" % ("event", "case", "hook", "status", "ms", "stdout", "stderr"))
        for r in report["hook_profile"]:
            print("%-16s %-16s %-36s %-10s %6s %7s %7s" % (
                r["event"], r["case"], r["hook"], r["status"], r["ms"], r["stdout_bytes"], r["stderr_bytes"]))

    if "prompt_hook_rows" in report:
        print("\n== UserPromptSubmit hooks: first prompt vs steady state (two synthetic passes) ==")
        print("%-36s %-9s %10s %10s %10s %10s %s" % (
            "hook", "isolated", "status_1st", "bytes_1st", "status_2nd", "bytes_2nd", "note"))
        for r in report["prompt_hook_rows"]:
            print("%-36s %-9s %10s %10s %10s %10s %s" % (
                r["hook"], "yes" if r["isolated"] else "no",
                r["status_first"], r["stdout_bytes_first"],
                r["status_steady"], r["stdout_bytes_steady"], r["run_note"] or ""))

    if "prompt_emission_first_bytes" in report:
        print(f"\nper-prompt UserPromptSubmit emission -- first prompt: {report['prompt_emission_first_bytes']:,} B "
              f"(reported, not budgeted)")
        print(f"per-prompt UserPromptSubmit emission -- steady state:  {report['prompt_emission_steady_bytes']:,} B "
              f"(budgeted)")
        for x in report.get("prompt_emission_excluded", []):
            print(f"  excluded from sum -- {x['hook']}: first={x['first_bytes']:,} B steady={x['steady_bytes']:,} B "
                  f"({x['reason']})")


def print_budgets(report: dict, failed: list[str], cap_over: list[dict] | None = None) -> None:
    print("\n== Budgets ==")
    for key, (cap, baseline) in (report.get("budgets") or BUDGETS).items():
        cap_text = "tripwire" if cap is None else f"cap={cap:,}"
        print(f"  {key}: {cap_text}  recorded_baseline={baseline:,}")
    for row in over_baseline(report):
        files = f"  {', '.join(row['files'])}" if row["budget"] == "oversized_body" else ""
        print(f"  tripwire {row['budget']}: measured {row['measured']:,} > baseline "
              f"{row['baseline']:,} (+{row['delta']:,}){files}")
    peaks = {}
    for row in report.get("session_start_rows") or []:
        key = (row["hook"], row["args"])
        if row["stdout_bytes"] >= peaks.get(key, (-1, ""))[0]:
            peaks[key] = (row["stdout_bytes"], row["case"])
    for (hook, args), (peak, case) in peaks.items():
        print(f"  session_start {hook}{' ' + args if args else ''}: max {peak:,} B at [{case}]")
    injection = report.get("session_start_injection") or {}
    if injection.get("rows"):
        print("\n== SessionStart injection per reader (standing + injected = the least that session loads) ==")
        for row in injection["rows"][:12]:
            print("  %-46s %7d B injected  %8d B total" % (row["reader"], row["bytes"], row["load_bytes"]))
        if len(injection["rows"]) > 12:
            print("  ... %d more readers in the JSON report" % (len(injection["rows"]) - 12))
    for where in injection.get("unmeasured") or []:
        print("  session_start unmeasured (not counted): %s" % where)
    skipped = {row["hook"]: row["reason"] for row in report.get("session_start_skipped") or []}
    for hook, reason in skipped.items():
        print(f"  session_start skipped {hook}: {reason}")
    if cap_over:
        print("\n== Listing caps (per-kind; informational unless --caps) ==")
        for c in cap_over:
            print("  %-8s %-45s %4d B > %3d B" % (c["kind"], c["name"], c["bytes"], c["cap"]))
    if failed:
        print("FAILED:")
        for f in failed:
            print(f"  - {f}")
    else:
        print("all budgets within cap.")


def _is_prior_report(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and {"root", "listing"} <= data.keys()


def context_delta(root: Path, base: str) -> dict:
    """Changed guidance bytes from a committed base to the working tree, including new files."""
    repo = root.parent

    def git(*args: str) -> bytes:
        proc = git_run(args, cwd=str(repo))
        if proc.returncode:
            raise ValueError(proc.stderr.decode("utf-8", errors="replace").strip())
        return proc.stdout

    try:
        revision = git("rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
    except ValueError as exc:
        raise ValueError(f"cannot read delta base {base}: {exc}") from exc
    prefix = root.name + "/"
    # Every git path form below is cwd-relative: the census root's parent need not be the git toplevel
    # (this base's own layout puts `.claude` under `template/`). `ls-tree` already prints cwd-relative
    # names; `--relative` makes `diff` do the same, and `rev:./path` resolves `show` from the cwd.
    tracked = set(git("ls-tree", "-rz", "--name-only", revision, "--", root.name,
                      "CLAUDE.md", "MEMORY.md").decode("utf-8").split("\0")) - {""}
    changed = set(git("diff", "--relative", "--name-only", "-z", revision, "--", root.name,
                      "CLAUDE.md", "MEMORY.md").decode("utf-8").split("\0")) - {""}
    index = set(git("ls-files", "-z").decode("utf-8").split("\0")) - {""}
    for family in ("rules", "commands", "skills", "output-styles"):
        for p in (root / family).rglob("*.md"):
            rel = prefix + p.relative_to(root).as_posix()
            if rel not in tracked:
                changed.add(rel)
    cache = {}
    home = Path.home()
    home_sources = {}
    # The paths the delta charges: a path here that the base tree lacks is a new file, so its before
    # bytes are absent. Other untracked paths are unchanged context (an ignored settings.local.json, an
    # imported file), so both snapshots read the working tree and neither side counts a phantom change.
    subjects = set(changed)

    def read(path: str, before: bool) -> bytes | None:
        """The bytes at `path` (cwd-relative) in the snapshot, or None when it is absent."""
        key = (path, before)
        if key not in cache:
            if before and path in tracked:
                cache[key] = git("show", f"{revision}:./{path}")
            elif before and (path in index or path in subjects):
                cache[key] = None
            else:
                target = repo / path
                cache[key] = target.read_bytes() if target.is_file() else None
        return cache[key]

    def standing(before: bool) -> set[str]:
        seen = set()
        queue = [prefix + "CLAUDE.md", prefix + "auto-memory/MEMORY.md", "CLAUDE.md", "MEMORY.md"]
        while queue:
            path = queue.pop()
            if path in seen:
                continue
            seen.add(path)
            for line in (read(path, before) or b"").decode("utf-8", errors="replace").splitlines():
                if line.startswith("@") and line[1:].strip():
                    target = ((repo / path).parent / line[1:].strip()).resolve()
                    if target.is_relative_to(repo.resolve()):
                        queue.append(target.relative_to(repo.resolve()).as_posix())
        def style_read(path: Path):
            if path.is_relative_to(root):
                return read(prefix + path.relative_to(root).as_posix(), before)
            if path not in home_sources:
                home_sources[path] = path.read_bytes() if path.is_file() else None
            return home_sources[path]

        style, errors = _output_style_row(root, home, read=style_read)
        if errors:
            raise ValueError("; ".join(errors))
        if style:
            path = Path(style["path"])
            if path.is_relative_to(root):
                seen.add(prefix + path.relative_to(root).as_posix())
        return seen

    old_standing, new_standing = standing(True), standing(False)
    changed.update(old_standing ^ new_standing)
    totals = {"standing": 0, "conditional": 0}
    rows = []
    for path in sorted(changed):
        values = []
        for before, always in ((True, old_standing), (False, new_standing)):
            content = read(path, before) or b""
            if path in always:
                kind = "standing"
            elif path.startswith(prefix + "rules/") and path.endswith(".md"):
                text = content.decode("utf-8", errors="replace")
                match = FM.match(text)
                kind = "conditional" if match and re.search(r"^paths:", match.group(1), re.M) else "standing"
            elif path.endswith(".md") and any(path.startswith(prefix + family + "/")
                                              for family in ("commands", "skills", "output-styles")):
                kind = "conditional"
            else:
                kind = None
            values.append((kind, len(content)))
            if kind:
                totals[kind] += (-1 if before else 1) * len(content)
        if values[0] != values[1]:
            rows.append({"path": path, "before": values[0], "after": values[1]})
    return {**totals, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Session load census: always-loaded bytes, standing files, rule bundles, "
                     "oversized bodies, entrypoint transitive load, and hook-chain budgets.")
    parser.add_argument("--json", metavar="PATH", help="write the full report as JSON to PATH")
    parser.add_argument("--profile-hooks", action="store_true",
                         help="run every registered hook against synthetic payloads")
    parser.add_argument("--budgets", action="store_true",
                         help="check measured numbers against BUDGETS; exit 1 when a cap is exceeded "
                              "(a tripwire crossing prints and exits 0)")
    parser.add_argument("--caps", action="store_true",
                         help="with --budgets, also fail on a per-kind listing-cap violation "
                              "(LISTING_CAPS); the over-cap report always prints with --budgets")
    parser.add_argument("--delta", metavar="BASE", help="print changed standing and conditional bytes since BASE")
    parser.add_argument("--root", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.caps and not args.budgets:
        parser.error("--caps needs --budgets")

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    if args.delta:
        if args.json or args.profile_hooks or args.budgets or args.caps:
            parser.error("--delta runs alone; census and profiling flags are separate")
        try:
            delta = context_delta(root, args.delta)
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"context cost: standing {delta['standing']:+d} B; conditional {delta['conditional']:+d} B; "
              "hook emission: unmeasured (runtime observation required)")
        return 0
    report = build_report(root, profile_hooks_flag=args.profile_hooks, need_budget_hooks=args.budgets,
                          need_session_start_hooks=args.budgets)
    print_report(report, profile_hooks_flag=args.profile_hooks)

    if args.json:
        output_path = Path(args.json)
        if output_path.exists() and not _is_prior_report(output_path):
            print(f"--json takes an output path; {output_path} exists and is not a census report, "
                  "so it was left untouched. Name a new .json path.", file=sys.stderr)
            return 2
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.budgets:
        failed = check_budgets(report)
        cap_over = check_listing_caps(report)
        if args.caps:
            failed.extend(f"listing_cap {c['kind']} {c['name']} {c['bytes']} B > {c['cap']} B"
                           for c in cap_over)
        print_budgets(report, failed, cap_over)
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
