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
                    each flagged with whether a selector already exists for it
                    (`tools/lens.py` REGISTRIES -> `commands/agents/<file>`, or
                    `tools/kfm.py` CATALOG), loaded live from those two modules
                    so this file never carries a second copy of their registry.
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
SessionStart emission is an excluded check, not measured and not budgeted:
a SessionStart hook can start a build, so a synthetic run is not
side-effect free. Re-entry trigger: a SessionStart hook gains a build-free
dry-run seam. Shared-state hooks that write real machine state resolve
every write through an env var (`ISOLATED_SHARED_STATE_HOOKS`) and run for real against
a throwaway project/home/temp tree instead of being skipped;
`shell_census.py` additionally reads a live process snapshot, so its bytes
are reported in their own column and excluded from both sums
(`LIVE_MACHINE_HOOKS`). A hook with no such seam
(`NON_ISOLATABLE_SHARED_STATE_HOOKS`) is skipped and reported with its
reason.

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
`shared_state_hook_skip`, `isolated_shared_state_hooks`, and
`code_edit_probe` (`{"path": <project-relative file>}`, profiled as the
`edit-code` case). Lists union, mappings update, and an unreadable profile is
a source error.

CLI: `python3 .claude/tools/load_census.py [--json <path>] [--profile-hooks] [--budgets]`
`--budgets` fails closed when a standing file, entrypoint, or rule `paths:` frontmatter is missing or malformed.
`--root PATH` (hidden) points the whole census at a fixture tree for tests.
"""

from __future__ import annotations

import argparse
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_ROOT = Path(__file__).resolve().parent.parent  # .claude/

FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)
DESC_RE = re.compile(r"^description:\s*(>-?|\|)?\s*\n?(.*?)(?=^\w[\w-]*:|\Z)", re.S | re.M)
PATHS_RE = re.compile(r"^paths:\s*\n((?:\s*-\s*.+\n?)+)", re.M)
PATH_ITEM_RE = re.compile(r'^\s*-\s*"?([^"\n]+?)"?\s*$', re.M)

# Each budget: (cap, baseline measured 2026-09-14, documentation only -- the
# live comparison value below is recomputed fresh every run).
BUDGETS = {
    # cap = live + 5%, rounded up to the nearest 256 B (measured 2026-09-14 after workflow
    # descriptions (26 rows, 5,263 B with the 4 `whenToUse` suffixes) joined the listing and the active
    # output style joined standing).
    "listing_bytes": (30_464, 28_870),
    # Owner-authored standing text outranks the byte target; the cap still stops growth past live + 5%.
    "standing_bytes": (34_304, 32_665),
    # Steady-state emission of an unchanged later prompt; the first prompt is reported
    # separately and never budgeted (reference/context_attribution_2026-09.md).
    "prompt_emission_bytes": (512, 324),
    # cap = live + 2 (measured 2026-09-14 after the reaper moved in-process and the PostToolUse `*` entry narrowed).
    "edit_hook_chain_subprocesses": (4, 2),
}

OVERSIZED_THRESHOLD = 24_576

# Bodies over OVERSIZED_THRESHOLD without a selector -> bytes at listing, measured
# 2026-09-14. A NEW oversized selectorless body not in this dict fails --budgets. So
# does a listed body that grew more than KNOWN_OVERSIZED_GROWTH past its recorded
# bytes, shrank below the threshold, gained a selector, or whose file is missing: a
# stale entry would mask its own regrowth, so the dict stays exact.
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

# UserPromptSubmit hooks that write shared/machine-wide state but were
# confirmed (by reading them) to resolve every write path through an env var:
# CLAUDE_PROJECT_DIR (shell_census.py's `.claude/.cache` cache file),
# HARNESS_HOOK_STATE_DIR (_hook_state.py's per-session state, already redirected
# for every hook), and HOME/USERPROFILE/TEMP/TMP/TMPDIR (a registry that resolves
# through `tempfile.gettempdir()`). Profiles add their own. Run for real against
# a throwaway project/home/temp tree instead of being skipped -- see
# `_isolated_hook_env`.
ISOLATED_SHARED_STATE_HOOKS = {"shell_census.py"}

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
PROFILE_MAPS = ("rule_bundles", "budgets", "known_oversized_no_selector", "code_edit_probe")


def load_profile(root: Path) -> dict:
    """The merged `tools/load_census.*.json` profiles under `root`, in name order, plus a
    `source_errors` list naming any unreadable one."""
    merged: dict = {key: [] for key in PROFILE_LISTS}
    merged.update({key: {} for key in PROFILE_MAPS})
    merged["source_errors"] = []
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
    return merged


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def description(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = FM.match(text)
    if not m:
        return ""
    body = m.group(1)
    dm = DESC_RE.search(body)
    return (dm.group(2) if dm else "").strip()


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

def listing_census(root: Path) -> dict:
    rows = []
    for p in sorted(root.glob("commands/**/*.md")):
        rows.append({"kind": "command", "name": str(p.relative_to(root)).replace("\\", "/"),
                     "desc_bytes": len(description(p).encode("utf-8"))})
    for p in sorted(root.glob("skills/*/SKILL.md")):
        rows.append({"kind": "skill", "name": p.parent.name,
                     "desc_bytes": len(description(p).encode("utf-8"))})
    for p in sorted(root.glob("workflows/*.js")):
        meta = workflow_meta(p)
        if meta is None:
            continue
        name, desc = meta
        rows.append({"kind": "workflow", "name": name or p.stem,
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
    style_row, style_errors = _output_style_row(root, home)
    source_errors.extend(style_errors)
    if style_row:
        rows.append(style_row)
    return {"rows": rows, "total_bytes": sum(r["bytes"] for r in rows),
            "source_errors": source_errors}


def _output_style_row(root: Path, home: Path) -> tuple[dict | None, list[str]]:
    """The active output style's standing row, and any source errors."""
    name = setting_file = None
    for settings_path in (root / "settings.local.json", root / "settings.json",
                          home / ".claude" / "settings.json"):
        if not settings_path.is_file():
            continue
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
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
        if style_path.is_file():
            return {"path": str(style_path), "bytes": style_path.stat().st_size, "exists": True}, []
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
    from `tools/lens.py` (REGISTRIES -> AGENTS dir) and `tools/kfm.py`
    (CATALOG), never duplicated here."""
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
    kfm_path = root / "tools" / "kfm.py"
    if kfm_path.exists():
        try:
            spec = importlib.util.spec_from_file_location("_load_census_kfm", kfm_path)
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
    return baseline_compose.compose_settings(base, project, root / "hooks", ["pure", "coding", "godot"])


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


def _hook_name(command: str) -> str:
    script = command.split('"')[1] if '"' in command else command.split()[-1]
    return os.path.basename(script)


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
                   timeout: float = 60) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, script], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
                            encoding="utf-8", errors="replace")
        ms = int((time.time() - t0) * 1000)
        return {"status": r.returncode, "ms": ms,
                "stdout_bytes": len((r.stdout or "").encode()),
                "stderr_bytes": len((r.stderr or "").encode())}
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "ms": int(timeout * 1000), "stdout_bytes": 0, "stderr_bytes": 0}


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
    Only the first synthetic case per hook is used (UserPromptSubmit has one
    case in `build_payloads`)."""
    state_dir = tempfile.mkdtemp(prefix="load_census_hookstate_")
    iso_root = Path(tempfile.mkdtemp(prefix="load_census_isohook_"))
    try:
        _label, payload = build_payloads(root)["UserPromptSubmit"][0]
        skipped, isolated = _hook_sets(root)
        base_env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir,
                         CLAUDE_PROJECT_DIR=str(root.parent), PYTHONIOENCODING="utf-8")
        base_env.pop("CLAUDE_CODE_SESSION_ID", None)
        rows = []
        for group in settings.get("hooks", {}).get("UserPromptSubmit", []):
            for h in group.get("hooks", []):
                cmd = h.get("command", "")
                script = cmd.split('"')[1] if '"' in cmd else cmd.split()[-1]
                script = script.replace("$CLAUDE_PROJECT_DIR", str(root.parent))
                name = os.path.basename(script)
                row = {"hook": name, "isolated": False, "run_note": None,
                       "excluded_from_budget": False, "exclude_reason": None,
                       "status_first": "-", "status_steady": "-",
                       "ms_first": 0, "ms_steady": 0,
                       "stdout_bytes_first": 0, "stdout_bytes_steady": 0}
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
                # The live harness kills a hook at its registered timeout (seconds).
                hook_timeout = float(h.get("timeout") or 60)
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
                    cmd = h.get("command", "")
                    script = cmd.split('"')[1] if '"' in cmd else cmd.split()[-1]
                    script = script.replace("$CLAUDE_PROJECT_DIR", str(root.parent))
                    name = os.path.basename(script)
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
                        hook_timeout = float(h.get("timeout") or 60)
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
                 home: Path | None = None) -> dict:
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
        "known_oversized_stale": stale_known_oversized(root, bodies),
        "transitive_load": transitive,
        "hook_chain": edit_hook_chain(settings),
        "budgets": {**BUDGETS, **{k: tuple(v) for k, v in profile["budgets"].items()}},
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
    return report


def check_budgets(report: dict) -> list[str]:
    fails = []
    budgets = report.get("budgets", BUDGETS)
    for source_error in report.get("source_errors", []):
        fails.append(f"source_invalid: {source_error}")
    cap, _ = budgets["listing_bytes"]
    if report["listing"]["total_bytes"] > cap:
        fails.append(f"listing_bytes {report['listing']['total_bytes']:,} > {cap:,}")
    cap, _ = budgets["standing_bytes"]
    if report["standing"]["total_bytes"] > cap:
        fails.append(f"standing_bytes {report['standing']['total_bytes']:,} > {cap:,}")
    for name, bundle in report["rules"].get("bundles", {}).items():
        key = f"{name}_rule_bundle_bytes"
        if key in budgets and bundle["bytes"] > budgets[key][0]:
            fails.append(f"{key} {bundle['bytes']:,} > {budgets[key][0]:,}")
    cap, _ = budgets["edit_hook_chain_subprocesses"]
    if report["hook_chain"]["edit_subprocesses"] > cap:
        fails.append(f"edit_hook_chain_subprocesses {report['hook_chain']['edit_subprocesses']} > {cap}")
    cap, _ = budgets["prompt_emission_bytes"]
    emission = report.get("prompt_emission_steady_bytes")
    if emission is not None and emission > cap:
        fails.append(f"prompt_emission_bytes (steady) {emission:,} > {cap:,}")
    unmeasured = report.get("prompt_hooks_unmeasured") or []
    if unmeasured:
        fails.append("prompt_emission_bytes unmeasured (a hook that did not exit 0 is not silent): "
                     + ", ".join(unmeasured))
    selectorless = [b for b in report["oversized_bodies"] if not b["has_selector"] and not b["known"]]
    new_oversized = [b for b in selectorless if b.get("known_bytes") is None]
    if new_oversized:
        names = ", ".join(f'{b["path"]} ({b["bytes"]:,} B)' for b in new_oversized)
        fails.append(f"body over {OVERSIZED_THRESHOLD:,} B with no selector and not in KNOWN_OVERSIZED_NO_SELECTOR: {names}")
    grown = [b for b in selectorless if b.get("known_bytes") is not None]
    if grown:
        names = ", ".join(f'{b["path"]} ({b["bytes"]:,} B > {b["known_bytes"]:,} B recorded)' for b in grown)
        fails.append(f"KNOWN_OVERSIZED_NO_SELECTOR body grew more than {KNOWN_OVERSIZED_GROWTH:.0%} "
                     f"past its recorded bytes: {names}")
    stale_known = report.get("known_oversized_stale") or []
    if stale_known:
        fails.append("KNOWN_OVERSIZED_NO_SELECTOR lists bodies that are missing or no longer oversized without "
                     "a selector; remove them so regrowth fails: " + ", ".join(stale_known))
    return fails


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

    print(f"\n== Bodies over {OVERSIZED_THRESHOLD:,} B ==")
    for b in report["oversized_bodies"]:
        flags = []
        if b["has_selector"]:
            flags.append("selector")
        if b["known"]:
            flags.append("known")
        print("  %8d  %-55s %s" % (b["bytes"], b["path"], ",".join(flags) or "NEW, no selector"))

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


def print_budgets(report: dict, failed: list[str]) -> None:
    print("\n== Budgets ==")
    for key, (cap, baseline) in report.get("budgets", BUDGETS).items():
        print(f"  {key}: cap={cap:,}  baseline={baseline:,}")
    if failed:
        print("FAILED:")
        for f in failed:
            print(f"  - {f}")
    else:
        print("all budgets within cap.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Session load census: always-loaded bytes, standing files, rule bundles, "
                     "oversized bodies, entrypoint transitive load, and hook-chain budgets.")
    parser.add_argument("--json", metavar="PATH", help="write the full report as JSON to PATH")
    parser.add_argument("--profile-hooks", action="store_true",
                         help="run every registered hook against synthetic payloads")
    parser.add_argument("--budgets", action="store_true",
                         help="check measured numbers against BUDGETS; exit 1 if any is exceeded")
    parser.add_argument("--root", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    report = build_report(root, profile_hooks_flag=args.profile_hooks, need_budget_hooks=args.budgets)
    print_report(report, profile_hooks_flag=args.profile_hooks)

    if args.json:
        output_path = Path(args.json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.budgets:
        failed = check_budgets(report)
        print_budgets(report, failed)
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
