#!/usr/bin/env python3
"""Materialize lens templates as per-job brief FILES for dispatch.js or sidecar_fanout.py.

A Workflow-dispatched agent has neither the Workflow tool nor the Agent tool, and an Agent-tool
subagent has no Workflow tool (measured 2026-09-03, both arms). So a batch orchestrator cannot
delegate "run /review_pr" and expect the inner lens fan-out to happen under pins. The flat shape is:
the orchestrator materializes every lens prompt as a file, dispatches all lenses in ONE dispatch.js
run (readOnly, spillDir), and a second run consolidates. This tool does the materialization so no
prompt byte travels through Workflow `args` (gotcha_workflow_args_generation_fidelity).

Usage:
  python3 .claude/tools/lens_briefs.py (--keys <key> | --plan <plan.json>) \
      --out .claude/scratch/pr_pipeline/briefs_pr116 --prefix pr116 \
      --pr-num 116 --branch claude/x --context-path <abs>/context_pr116.md --model <pin> --effort <rung> \
      --must-read <list file> [--must-read-for <key>=<list file>] \
      [--tier <tier>] [--model-for <key>=<pin>] [--tier-for <key>=<tier>] [--effort-for <key>=<rung>] \
      [--sub TRANSCRIPT_CORRECTIONS=<file>] \
      [--subject-plan meta:<plan.md> | code:<plan.md>]... [--vault ROOT]
  python3 .claude/tools/lens_briefs.py must-read <list file>

A panel of more than one seat takes its seats from `review_ledger.py plan`: `--plan <file>` is the JSON that
command printed, its `roster` keys are the seats, and `--keys` must be absent. Several `--keys` without `--plan`
exit non-zero, because a copied roster sizes the panel from nothing (`orchestration` §2 *Sizing the width*). A
single seat needs no plan.

Writes <out>/<prefix>_<key>.md per lens (shared registry preamble + the template's fenced prompt with
{{PLACEHOLDERS}} substituted) and <out>/jobs_<prefix>.json — the dispatch.js `jobs` array. A key
`a+b` is one seat carrying both mandates (one brief, one job), per the calling command's seat map;
`--model-for`/`--tier-for`/`--effort-for` address the seat key. Lens
catalogs own no model pins: the caller resolves them through `orchestration` and passes `--model` for
the panel plus `--model-for KEY=MODEL` per lens. A seat's tier is `--tier-for`, else the panel
`--tier`, else its lenses' tier (`lens.seat_tier`). Each job carries its `tier`, which the provider
guard checks the pin against. A seat's pin must also be a row serving its lenses' tier (the strongest of a
merged key), whatever `--tier` or `--tier-for` the job writes: `workflow_provider_guard` RULE 3 enforces that
tier through `_dispatch_checks.seat_fit`, and a pin outside it refuses the panel before any brief is built. The
one exception is a pin that holds the lens tier only in registry `supplementary`: it fills a seat only beside a
qualified seat in the same panel that carries every mandate the seat does, and refuses alone. On the sidecar
target a written `--tier` or `--tier-for` is also checked against the pin's registry `roles` alone. A native job also carries its `seat` key and `mandates` (its lens keys), the
identity `review_ledger.py record --run` joins a `dispatch.js` run's deliverables on. Effort is the caller's pin on both targets and is never derived from a
seat's tier: `--effort` sets the panel and `--effort-for KEY=RUNG` raises one seat, and a seat with
neither is refused, because a tier names a work shape and the ladder's effort cell selects the rung.
{{CHECKLIST_CDS}}/{{CHECKLIST_RP}}/{{CHECKLIST_I}} come from
commands/checklists/code_quality.md by section letter; {{TEST_QUALITY_CHECKLIST}} is test_quality.md
below its frontmatter and {{CODE_QUALITY_CHECKLIST}} is code_quality.md whole. Each code_quality
placeholder also carries the same letters of its `<stem>_godot.md` companion when the engine layer ships it. {{CONTEXT}} becomes a pointer to --context-path
(dispatch.js `contextPath` already tells the agent to read it first). Unresolved placeholders are an
error, never silently left in a brief. `--pr-num`/`--branch` are required only by a selected
template that uses their placeholder.

Every seat carries a must-read list (commands/agents/review_agents.md *Seat coverage contract*): the
subject files or diff paths its lens covers, one per line in a list file. `--must-read` gives the panel's
list and `--must-read-for KEY=<list file>` replaces it for one seat; a seat with neither is refused. The
list rides in the job as `mustRead`, one `{"path", "lines"}` entry per file with the line count the Read
tool numbers (0 for a path that does not exist), and in the brief as its done condition: the seat's Read
ranges cover every line of each entry. `must-read <list file>` prints those entries as JSON for a caller
that builds a `review_fanout.js` agents array by hand. Before any brief is written,
each seat's brief, context file and must-read files are summed at the orchestration Pre-Dispatch
Checklist's 2.5 characters per token and compared with its pin's usable window (`dispatch_size.window_fit`): the registry's effective
window less its output cap, the point where the client compacts (`model_registry.py context-window` and
`max-output`). A seat over that bound, or a pin with no declared window, refuses the whole panel with each
seat's overage; the caller splits the list across seats and never truncates it.

`--target sidecar` writes the same briefs and `<out>/jobs_<prefix>.json` in `sidecar_fanout.py`'s job
schema — `shape: review`, the canonical review schema, `disclosure: full`, the repository root as
`workdir`, the context file as `contextFiles` — so one file serves the cross-transport route. The
alias resolves through `model_registry` rather than the four-host-pin native vocabulary. On this
target the effort rung must be one the alias's transport serves (`model_registry.py effort-values`). A sidecar may or may not run project hooks, so
the read-only tool profile and source pointer travel in the brief. On both targets a seat's brief
ends with every file its templates declare as `preload:`, once per seat, blockquoted by
`lens.doctrine` so parent-only tool lines read as reference rather than as commands. A lens whose header declares
`kfm: index` (`lens.py`) gets the known-failure-modes snapshot materialized and checked by `kfm.py`
under `.claude/scratch/kfm/<catalog sha>/`, and its brief names the index path. Every sidecar brief
is checked against the review tool profile before any job is written: a line needing a tool the
profile lacks refuses with that line, never a silent `+Bash`.

A meta subject is swept once for all selected seats, including non-citer review seats `citer_coverage.py` matches.
Its summary shows the subject's worktree hash and sweep HEAD as provenance; only the hash determines freshness.
A lens whose header declares `citers: plan` (`lens.py`) judges a plan's changed surfaces, so its brief carries
the sweep of each `--subject-plan meta:<plan.md>`: `citers.py` runs over that plan's fenced `surfaces` block.
The full JSON is written once per subject beside the briefs as <prefix>_<plan-stem>_citers.json. `INBOUND_REFS`
is a blockquoted summary with totals, coverage flags, one count row per surface and a pointer to Grep the
sweep file for judged surfaces; no citer or duty payload is pasted. Such a seat needs at least one `--subject-plan`, and the
run exits non-zero on a meta subject whose block is missing or unusable (exit 2 for unusable input, 1 for a surface
that does not resolve at HEAD), printing the block grammar and its home. A `code:<plan.md>` subject is never
swept. `--vault` is the Obsidian vault root the sweep reads (default: `vault_read.DEFAULT_VAULT`).

For a meta subject, the `plc-evidence-grounding` seat also receives <prefix>_behavior.json:
`extract_numbers.behavior_report` over the subject plans, file-bearing surfaces and that seat's
must-read paths. The full artifact is a sized must-read input, not a counts-only summary. Current
claims keep the checker's typed outcomes; proposal subjects are pending, never observed proof passes.
"""
from __future__ import annotations

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
from cli_usage import usage



import argparse
import functools
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
REPO = CLAUDE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CLAUDE / "hooks"))
import _session_transport  # noqa: E402  (the host transport's own pin and effort vocabulary)
import _dispatch_checks  # noqa: E402  (the guard's tier fit)
import _dispatch_pins  # noqa: E402  (the native pin name of a row)
import dispatch_size  # noqa: E402  (a seat's reading and its fit to its pin's window)
import lens  # noqa: E402  (sibling module: registry parsing)
import model_registry  # noqa: E402  (the tier vocabulary, and the sidecar alias resolution)
import review_result  # noqa: E402  (the Read window a must-read brief states)
import tool_profiles  # noqa: E402  (the review profile's built-ins, and the brief coverage gate)

TIERS = list(model_registry.TIER_ORDER) + sorted(model_registry._TIER_ALIASES)

CHECKLISTS = CLAUDE / "commands" / "checklists"
FENCE_RE = re.compile(r"```\n(.*?)\n```", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# The native route's vocabulary IS the registry's: a dispatch.js job's `model` is the provider
# guard's pin token, and the guard derives its legal set from the same registry call, so a pin this
# refuses is one the guard would refuse too. A sidecar job's `alias` is the registry's as well, so
# that route resolves instead of listing. An unreadable registry yields an empty vocabulary, which
# refuses every pin rather than admitting one nothing verified.
NATIVE_PINS = tuple(_session_transport.legal_model_ids("anthropic", seat="anthropic"))
NATIVE_EFFORTS = tuple(_session_transport.legal_effort_values("anthropic"))
# A placeholder the CALLER supplies as a flag rather than as a `--sub` file: the refusal names the
# flag, because an absent --pr-num silently substituted with empty text produces a brief that reads
# as complete and reviews nothing.
FLAG_PLACEHOLDERS = {"PR_NUM": "--pr-num", "BRANCH": "--branch"}

# The review route's shape and schema. One home per authored value: the schema path is
# `sidecar_fanout.DEFAULT_REVIEW_SCHEMA` — the launcher's own default — and the import is deferred so
# the native route never pays for the fan-out's process machinery.
REVIEW_SHAPE = "review"


@functools.lru_cache(maxsize=None)
def kfm_section(root: str | None = None) -> str:
    """Materialize and check the known-failure-modes snapshot, then the brief section naming its index.

    `root` replaces `.claude/scratch/kfm` as the parent of the per-catalog snapshot directory; a proof
    passes its temp root so it never writes into the live checkout."""
    import kfm
    sha = hashlib.sha256(kfm.CATALOG.read_bytes()).hexdigest()[:12]
    out = (Path(root) if root else kfm.SCRATCH / "kfm") / sha
    index = kfm.materialize(kfm.parse(), out)
    if not kfm.snapshot_is_current(out, kfm.CATALOG):
        sys.exit("kfm snapshot stale or incomplete after materialize; rerun into a new root")
    return f"# Known failure modes\n\nRead the index: {index}\n"


def subject_plans(specs: list[str]) -> list[tuple[str, str]]:
    """[(shape, plan path)] of the `--subject-plan meta:<plan.md>` / `code:<plan.md>` flags."""
    found = []
    for spec in specs:
        shape, sep, path = spec.partition(":")
        if shape not in ("meta", "code") or not sep or not path:
            sys.exit(f"--subject-plan {spec}: write meta:<plan.md> or code:<plan.md>")
        if not Path(path).is_file():
            sys.exit(f"--subject-plan {spec}: {path} is not a file")
        found.append((shape, path))
    return found


def inbound_refs_section(subjects: list[tuple[str, str]], vault: str | None, *, out: Path, prefix: str) -> str:
    """Write each meta subject's full JSON beside the briefs and return a blockquoted summary.
    Summary size follows surfaces, never citer, duty or truncation rows. Code subjects are not swept;
    unusable meta blocks retain `citers.py`'s exit code and message."""
    from collections import Counter
    import citers
    meta = list(dict.fromkeys(path for shape, path in subjects if shape == "meta"))
    if not meta:
        return "# INBOUND_REFS\n\nNo meta subject plan was swept: every subject plan is a code plan.\n"
    stems = Counter(Path(path).stem for path in meta)
    lines = ["# INBOUND_REFS", ""]
    for path in meta:
        try:
            result = citers.sweep(citers.plan_surfaces(path), vault=vault, plan=path)
        except citers.Usage as err:
            print(f"citers.py: {err}", file=sys.stderr)
            sys.exit(2)
        except citers.Unresolved as err:
            print(f"citers.py: unresolved at HEAD: {err}", file=sys.stderr)
            sys.exit(1)
        stem = Path(path).stem
        if stems[stem] > 1:
            stem += "_" + hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:12]
        sweep_file = out / f"{prefix}_{stem}_citers.json"
        sweep_file.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        coverage = result["coverage"]
        citer_counts = Counter(row["surface"] for row in result["citers"])
        duty_counts = Counter(row["surface"] for row in result["duties"])
        cut_counts = Counter()
        for row in coverage["truncated"]:
            cut_counts[row["surface"]] += row["omitted"]
        flags = {"files": coverage["files"], "ignoredTests": coverage["ignoredTests"],
                 "vaultVerified": coverage["vaultVerified"], "excludedRows": coverage["excludedRows"]}
        flags["vault"] = {key: sum(int(root.get(key, 0)) for root in coverage["vault"])
                          for key in ("read", "unreadable", "unattempted", "absent")}
        lines += [f"> ## {path}", ">",
                  "> Unverified seed from `citers.py`, run over each meta subject plan's `surfaces` block at HEAD. "
                  "A citer or duty here is a lead to read and Grep, never the checklist.",
                  f"> surfaces: {len(result['surfaces'])}; citers: {len(result['citers'])}; "
                  f"duties: {len(result['duties'])}; truncated: {sum(cut_counts.values())}",
                  "> coverage: " + json.dumps(flags, ensure_ascii=False)]
        for subject in result.get("subjects", []):
            lines.append("> subject provenance: " + json.dumps(subject, ensure_ascii=False))
        for surface in result["surfaces"]:
            name = surface["surface"]
            lines.append("> " + json.dumps({"surface": name, "citers": citer_counts[name],
                                             "duties": duty_counts[name], "truncated": cut_counts[name]}, ensure_ascii=False))
        lines += [f"> Sweep file: `{sweep_file.resolve().as_posix()}`. Grep that file for the surfaces you judge.", ">"]
    return "\n".join(lines) + "\n"


def grounding_behavior_section(subjects: list[tuple[str, str]], claimed_paths: list[str], *,
                               out: Path, prefix: str, repo: Path = REPO) -> str:
    """Materialize the claim owner's full advisory report for current meta-plan surfaces.

    Proposal subjects stay pending; the report inspects current claims and stored case evidence,
    never executes proofs or interprets a resolving citation as a behavior pass.
    """
    import citers
    import extract_numbers
    meta = list(dict.fromkeys(path for shape, path in subjects if shape == "meta"))
    if not meta:
        return ""
    repo = Path(repo).resolve()

    def relative(path):
        candidate = Path(path)
        try:
            return (candidate if candidate.is_absolute() else repo / candidate).resolve().relative_to(repo).as_posix()
        except ValueError:
            return candidate.as_posix()

    paths = [relative(path) for path in meta]
    for path in meta:
        paths.extend(relative(target) for surface in citers.plan_surfaces(path)
                     if (target := citers.surface_file(surface)))
    paths.extend(relative(path) for path in claimed_paths)
    report = extract_numbers.behavior_report(repo, paths=list(dict.fromkeys(paths)), base="HEAD",
                                            staged=False, proof_results=None)

    def unclassified(unit):
        return unit.get("class") is None and unit.get("status") == "uncovered" and not unit.get("changed")
    counted = {}
    for unit in report.get("units") or []:
        if unclassified(unit):
            counted[unit["path"]] = counted.get(unit["path"], 0) + 1
    # An unchanged unclassified unit is census debt, the same for every one; listing each made the seat's must-read unreadable.
    report["units"] = [unit for unit in report.get("units") or [] if not unclassified(unit)]
    report["uncovered"] = [unit for unit in report.get("uncovered") or []
                           if not (unclassified(unit) and unit.get("reason") == "unclassified unit")]
    scope = report.get("scope") or {}
    if isinstance(scope.get("initialDebt"), list):
        scope["initialDebt"] = [unit for unit in scope["initialDebt"] if unit.get("class") is not None]
    report["unclassifiedByPath"] = counted
    report["proposalSubjects"] = [{"path": relative(path), "status": "pending",
                                  "reason": "Proposed cases/executors are not observations of existing behavior."}
                                 for path in meta]
    artifact = out / f"{prefix}_behavior.json"
    artifact.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return ("# Tool behavior grounding\n\n"
            f"Read the full typed claim artifact: `{artifact.resolve().as_posix()}`. "
            "Trace each current implementation's real input/default/branch/output and its callers; "
            "a resolving citation is not a behavior proof. Report the checker's typed outcomes and "
            "missing/stale evidence, not just its counts. Proposal sentences name future cases/executors "
            "as pending, never as passing proofs of existing behavior.\n")


def review_schema() -> Path:
    """The review findings schema the sidecar target's jobs point at."""
    import sidecar_fanout
    return sidecar_fanout.DEFAULT_REVIEW_SCHEMA


def sections(path: Path, letters: set[str]) -> str:
    """Return the `## Name (X)` sections of a checklist whose letter X is in `letters`."""
    out, keep = [], False
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        m = re.match(r"^## .*\(([A-Z])\)\s*$", line)
        if m:
            keep = m.group(1) in letters
        if keep:
            out.append(line)
    return "".join(out).rstrip() + "\n"


def with_companion(path: Path, render) -> str:
    """`render(file)` over a checklist plus, when the godot layer ships one, its `<stem>_godot.md` companion."""
    companion = path.with_name(f"{path.stem}_godot.md")
    parts = [render(path)] + ([render(companion)] if companion.exists() else [])
    return "\n".join(part for part in parts if part.strip())


def checklist_for_tests(path: Path) -> str:
    """The test-quality checklist below its frontmatter, plus its godot companion when the layer ships one."""
    return with_companion(path, lambda f: below_frontmatter(f.read_text(encoding="utf-8")))


def below_frontmatter(text: str) -> str:
    """The text after a leading `---` frontmatter block: its description addresses the skill loader, not a lens."""
    m = re.match(r"---\r?\n.*?\r?\n---\r?\n+", text, re.S)
    return text[m.end():] if m else text


def lens_facing(preamble: str) -> str:
    """Drop the `## Agent Spawn Rules` section: it addresses the CALLER (which engine fans the lenses
    out), and inside a brief it reads as an instruction to dispatch — the shape the nested-fan-out
    guard denies (hooks/dispatch_mechanism_guard.py)."""
    out, skip = [], False
    for line in preamble.splitlines(keepends=True):
        if line.startswith("## "):
            skip = line.startswith("## Agent Spawn Rules")
        if not skip:
            out.append(line)
    return "".join(out).rstrip()


def sidecar_tool_block(context_path: str, builtins: list[str]) -> str:
    """The seat's tool profile and source list — what native's CONTEXT pointer carries implicitly.

    `builtins` is the resolved review profile's list, so the line cannot promise a tool the review
    row does not grant.
    """
    context = Path(context_path).as_posix()
    return ("# Your tool profile: read-only, no shell\n\n"
            f"You are a review seat with {', '.join(builtins)}. You have no shell and no writing tool, "
            "and the transport's hooks or skills are not a substitute for this brief's mandates. "
            "When a mandate's evidence is "
            "not readable with those tools, report that mandate `not-reached` in `checked.mandates` and stop "
            "`blocked`: your other mandates' tokens still count. Never assert a fact you could not read. "
            "Every finding carries `evidence`: at least one `{file, quote}` item quoting the text that shows "
            "the defect (`reference/evidence_contract.md`); the run checks each quote, and a finding without "
            "one is held unchecked.\n\n"
            "# Sources\n\n"
            f"Read first:\n- {context}\n\n"
            f"The source list for this seat starts at `{context}`. It carries "
            "the plan or diff under review, the changed-file list, and the paths behind every "
            "citation the mandates ask you to check.")


def must_read_list(spec: str, what: str) -> list[str]:
    """The paths a list file names, one per line with blanks skipped, or a refusal naming `what`."""
    path = Path(spec)
    if not path.is_file():
        sys.exit(f"{what}: the must-read list file does not resolve: {spec}")
    entries = list(dict.fromkeys(line.strip().replace("\\", "/")
                                 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()))
    if not entries:
        sys.exit(f"{what}: {spec} lists no path; name the subject files or diff paths the seat's lens covers")
    folders = [entry for entry in entries if repo_path(entry).is_dir()]
    if folders:
        sys.exit(f"{what}: a must-read entry names a file, never a directory: {', '.join(folders)}")
    return entries


def line_count(entry: str) -> int:
    """The lines the Read tool numbers in a listed file, a final unterminated one included; 0 when the
    path does not exist, which its seat reports as a `missing` gap."""
    path = repo_path(entry)
    if not path.is_file():
        return 0
    data = path.read_bytes()
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def must_read_entries(paths: list[str]) -> list[dict]:
    """The job's `mustRead`: one `{"path", "lines"}` entry per listed path."""
    return [{"path": path, "lines": line_count(path)} for path in paths]


def repo_path(entry: str) -> Path:
    path = Path(entry)
    return path if path.is_absolute() else REPO / path


def must_read_section(entries: list[dict]) -> str:
    """The seat's done condition and its list, in the words of the contract's home."""
    return ("# Must read\n\n"
            "You are done only when your Read ranges in `checked.toolsUsed` together cover every line of each "
            "entry below. Name each Read as `Read:<path>:<first>-<last>`, the lines it returned; a Read entry "
            f"with no range counts as lines 1-{review_result.READ_WINDOW}, the Read tool's default window, so "
            "read a longer file in ranges. A search covers no line. An entry you do not read goes in `gaps` as "
            '`{"item": "<the entry>", "reason": "<reason>"}`, with reason `outside-lens` (another seat\'s '
            "lens owns it), `capability` (it needs a tool you lack, such as running code or the engine) or "
            "`missing` (the path does not exist). An entry read only in part, or a gap in any other form, fails "
            "this seat whatever `checked.stoppedAt` says. Contract: commands/agents/review_agents.md "
            "*Seat coverage contract*.\n\n"
            + "".join(f"- {entry['path']} ({entry['lines']} lines)\n" for entry in entries))


def seat_overage(*, key: str, token: str, body: str, context_path: str, entries: list[dict],
                 data: dict) -> str | None:
    """None when the seat's brief, context and must-read files fit its pin's usable window, else the refusal
    line naming the overage. `dispatch_size` owns the reading and the fit. A must-read path that does not
    exist adds nothing: its seat reports it `missing`."""
    job = {"prompt": body, "model": token, "mustRead": entries}
    seat = dispatch_size.dispatch_size({"jobs": [job], "contextPath": str(Path(context_path).resolve())},
                                       REPO, data)["perJob"][0]
    if seat["fitError"]:
        return f"{key}: {token} {seat['fitError']}"
    if not seat["over"]:
        return None
    return (f"{key}: {seat['tokens']} tokens of brief, context and must-read files on {token} exceed its usable "
            f"window of {seat['usable']} tokens; over by {seat['over']}")


def lens_tier_refusal(*, panel: dict, native: bool, data: dict) -> list[str]:
    """The refusal lines for a panel `{seat key: pin}`, in seat order; [] when every seat fits.

    The lens tier (`lens.seat_tier`, the strongest of a merged `a+b` key) is the tier `workflow_provider_guard`
    RULE 3 enforces on a review_fanout seat, through the same `_dispatch_checks.seat_fit` on the pin's own
    transport: a pin outside the tier's rows is refused, and a pin holding the tier only as `supplementary` is
    refused unless a qualified seat in the panel carries every mandate the seat does. A `--tier` or `--tier-for`
    is the job's written claim: the guard never lets it replace the lens tier, so it does not excuse the pin
    here. A pin the registry does not resolve is `seat_overage`'s refusal.
    """
    seats = []
    for key, token in panel.items():
        tier = lens.seat_tier(key)
        if not tier:
            continue
        try:
            transport = model_registry.resolve(token, data)["transport"]
        except Exception as err:
            print(f"lens_briefs: seat `{key}` not checked against its lens tier `{tier}`: pin {token!r} does not "
                  f"resolve ({err})", file=sys.stderr)
            continue
        seats.append({"label": key, "model": token, "tier": tier, "mandates": lens.seat_mandates(key) or [],
                      "transport": transport})
    pin_name = (lambda row: _dispatch_pins.native_pin(row, row["transport"])) if native else None
    return [line if is_rule else (
                f"{seat['label']}: {line}. The seat's lens tier is `{seat['tier']}` (`lens.py index`), whatever "
                f"`--tier` or `--tier-for` the job writes; pin one of those rows "
                f"(`model_registry.py for-role {seat['tier']}`)")
            for seat, line, is_rule in _dispatch_checks.seat_verdicts(seats, data, pin_name)]


def resolve_sidecar_entry(token: str, what: str, data: dict) -> dict:
    """The registry entry for a sidecar job's alias, or a refusal naming what the caller wrote."""
    try:
        entry = model_registry.resolve(token, data)
    except Exception as err:
        sys.exit(f"{what}: {err}")
    if not model_registry.model_available(entry["id"], data):
        why = model_registry.unavailable_reasons(data).get(entry["id"], "excluded")
        sys.exit(f"{what}: {token} is unavailable ({why}) — re-select under the ladder")
    return entry


def effort_rungs(entry: dict, data: dict) -> list[str]:
    """The effort rungs this alias's transport forwards; [] = undeclared (any rung passes)."""
    try:
        return model_registry.effort_values(entry, data)
    except Exception as err:
        sys.exit(f"{entry['alias']}: {err}")


def serves_tier(entry: dict, tier: str) -> bool:
    canonical = model_registry.canonical_tier(tier)
    wanted = model_registry.TIER_ORDER.index(canonical)
    if canonical in (entry.get("supplementary") or []):
        return True
    return any(model_registry.TIER_ORDER.index(role) <= wanted for role in entry.get("roles", []))


def seat_keys(keys, plan_path) -> list[str]:
    """The panel's seat keys: the roster keys of `review_ledger.py plan`'s output, or one `--keys` seat."""
    if plan_path:
        if keys:
            sys.exit("--plan seats the panel from the plan's roster: drop --keys")
        try:
            roster = json.loads(Path(plan_path).read_text(encoding="utf-8")).get("roster")
        except (OSError, ValueError, AttributeError) as err:
            sys.exit(f"--plan {plan_path}: not the JSON `review_ledger.py plan` prints: {err}")
        found = [seat.get("key") for seat in roster] if isinstance(roster, list) and all(
            isinstance(seat, dict) for seat in roster) else None
        if not found or not all(isinstance(key, str) and key.strip() for key in found):
            sys.exit(f"--plan {plan_path}: its roster holds no seat to brief (an empty roster means nothing is owed)")
        return list(dict.fromkeys(found))
    if not keys:
        sys.exit("pass --keys <one seat key> or --plan <review_ledger.py plan output>")
    if len(keys) > 1:
        sys.exit(f"{len(keys)} seat keys without --plan: a panel's seats come from the ledger, never a copied roster. "
                 "Run `python3 .claude/tools/review_ledger.py plan --stage <stage> --subject <subject> --roster <roster.json> "
                 "--files <paths>`, then pass its output as `--plan` (orchestration §2 *Sizing the width*). "
                 "A single seat needs no plan.")
    return keys


def must_read_command(argv: list[str]) -> None:
    """`must-read <list file>`: print the list's `mustRead` entries as JSON."""
    ap = argparse.ArgumentParser(prog="lens_briefs.py must-read",
                                 description="print a must-read list file's {path, lines} entries as JSON")
    ap.add_argument("list", help="file listing must-read paths, one per line")
    a = ap.parse_args(argv)
    print(json.dumps(must_read_entries(must_read_list(a.list, "must-read"))))


def acceptance_section(task_id: str) -> str:
    """The task record's requirements and exclusions as the seat's acceptance test, so a goal the owner set for
    the work reaches every seat without the brief author copying it into the shared context."""
    import task_record
    rec = task_record.load(task_id)
    if not rec:
        sys.exit(f"--task: no task record {task_id} under {task_record.record_dir()}")
    lines = ["", "## Acceptance test (task %s)" % task_id,
             "Judge the subject against each item below as well as your mandate. A subject that passes your checks "
             "but misses one of these has a finding."]
    lines += ["- Requirement: %s" % item for item in rec.get("requirements") or []]
    lines += ["- Exclusion: %s" % item for item in rec.get("exclusions") or []]
    if len(lines) == 3:
        lines.append("- The record holds no requirement or exclusion yet.")
    return "\n".join(lines) + "\n"


def main() -> None:
    if sys.argv[1:2] == ["must-read"]:
        must_read_command(sys.argv[2:])
        return
    ap = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", nargs="+", default=None, help="one seat key; a panel of several seats uses --plan")
    ap.add_argument("--plan", default=None, metavar="FILE",
                    help="the JSON `review_ledger.py plan` printed: its roster keys are the seats; excludes --keys")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--pr-num", default=None,
                    help="required when a selected template carries {{PR_NUM}}")
    ap.add_argument("--branch", default=None,
                    help="required when a selected template carries {{BRANCH}}")
    ap.add_argument("--context-path", required=True, help="absolute path of the shared CONTEXT file")
    ap.add_argument("--target", default="native", choices=["native", "sidecar"],
                    help="native: dispatch.js jobs (default, unchanged). sidecar: sidecar_fanout.py jobs")
    ap.add_argument("--sub", action="append", default=[], help="PLACEHOLDER=<file> extra substitutions")
    ap.add_argument("--model", required=True,
                    help="native: the panel pin (opus|sonnet|haiku|fable), resolved by the caller "
                         "through orchestration §5. sidecar: a model_registry alias")
    ap.add_argument("--model-for", action="append", default=[], metavar="KEY=MODEL",
                    help="per-lens model override, e.g. --model-for <key>=opus")
    ap.add_argument("--tier", default=None, choices=TIERS,
                    help="panel tier, the one its --model pin serves; default: each seat's lens tier")
    ap.add_argument("--tier-for", action="append", default=[], metavar="KEY=TIER",
                    help="per-lens tier override, paired with that lens's --model-for pin")
    ap.add_argument("--effort", default=None,
                    help="the panel's ladder rung; required unless --effort-for covers every seat")
    ap.add_argument("--effort-for", action="append", default=[], metavar="KEY=EFFORT",
                    help="per-lens override, e.g. --effort-for <key>=medium (names the ambiguity in --justification at dispatch)")
    ap.add_argument("--must-read", default=None, metavar="LIST",
                    help="file listing the panel's must-read paths, one per line: every seat's list unless "
                         "--must-read-for replaces it")
    ap.add_argument("--must-read-for", action="append", default=[], metavar="KEY=LIST",
                    help="one seat's must-read list file, replacing --must-read for that seat")
    ap.add_argument("--subject-plan", action="append", default=[], metavar="SHAPE:PLAN",
                    help="meta:<plan.md> or code:<plan.md>; repeatable. A seat declaring `citers: plan` gets a "
                         "bounded summary and sweep-file pointer for each meta subject and needs at least one subject")
    ap.add_argument("--vault", default=None, metavar="ROOT",
                    help="the Obsidian vault root a citers sweep reads (default: vault_read.DEFAULT_VAULT)")
    ap.add_argument("--task", default=None, metavar="ID",
                    help="task record whose requirements and exclusions every seat judges as its acceptance test")
    a = ap.parse_args()
    acceptance = acceptance_section(a.task) if a.task else ""

    a.keys = seat_keys(a.keys, a.plan)

    # `choices` no longer owns --model or --effort: the sidecar route's vocabulary is the registry's,
    # which changes without a release. The native route keeps the same refusal text and exit code.
    if a.target == "native" and a.model not in NATIVE_PINS:
        ap.error(f"argument --model: invalid choice: {a.model!r} "
                 f"(choose from {', '.join(repr(p) for p in NATIVE_PINS)})")
    if a.target == "native" and a.effort and a.effort not in NATIVE_EFFORTS:
        ap.error(f"argument --effort: invalid choice: {a.effort!r} "
                 f"(choose from {', '.join(repr(e) for e in NATIVE_EFFORTS)})")
    if not Path(a.context_path).is_file():
        sys.exit(f"--context-path does not resolve: {a.context_path}")

    try:
        data = model_registry.load()
    except Exception as err:
        sys.exit(f"model registry unusable: {err}")
    if a.target == "sidecar":
        schema = review_schema()
        if not schema.is_file():
            sys.exit(f"the review schema every sidecar job sets is missing: {schema} — "
                     f"restore it, or dispatch this panel natively")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    preambles, found = lens.catalog()
    # A key is one seat; `a+b` puts mandates together per the caller's seat map (orchestration §2).
    seats = {k: k.split("+") for k in a.keys}
    missing = [m for k in a.keys for m in seats[k] if m not in found]
    if missing:
        sys.exit(f"no such lens: {', '.join(missing)} — run `lens.py index`")
    mixed = [k for k in a.keys if len({found[m].registry for m in seats[k]}) > 1]
    if mixed:
        sys.exit(f"a seat draws its mandates from one registry: {', '.join(mixed)}")

    subjects = subject_plans(a.subject_plan)
    citer_seats = [k for k in a.keys if any(found[m].citers for m in seats[k])]
    if citer_seats and not subjects:
        sys.exit(f"{', '.join(citer_seats)} declares `citers: plan`: pass --subject-plan meta:<plan.md> or "
                 f"code:<plan.md> for each plan under review, so the brief carries the INBOUND_REFS sweep "
                 f"(a code plan is not swept)")
    inbound = inbound_refs_section(subjects, a.vault, out=out, prefix=a.prefix) if subjects and (
        citer_seats or any(shape == "meta" for shape, _path in subjects)) else ""

    cq = CHECKLISTS / "code_quality.md"
    tq = CHECKLISTS / "test_quality.md"
    subs = {
        "CONTEXT": f"## Context\nThe shared CONTEXT for this review is the file `{a.context_path}` — read it first (dispatch already told you to). It carries the diff, the changed-file list, the worktree path where every changed file sits at the PR head, and the Jmodot pointer pair.",
        "CHECKLIST_CDS": with_companion(cq, lambda f: sections(f, {"C", "D", "S"})),
        "CHECKLIST_RP": with_companion(cq, lambda f: sections(f, {"R", "P"})),
        "CHECKLIST_I": with_companion(cq, lambda f: sections(f, {"I"})),
        "CODE_QUALITY_CHECKLIST": with_companion(cq, lambda f: below_frontmatter(f.read_text(encoding="utf-8"))),
        "TEST_QUALITY_CHECKLIST": checklist_for_tests(tq),
    }
    if a.pr_num is not None:
        subs["PR_NUM"] = str(a.pr_num)
    if a.branch is not None:
        subs["BRANCH"] = a.branch
    for s in a.sub:
        k, _, p = s.partition("=")
        subs[k] = Path(p).read_text(encoding="utf-8")

    selected = set(a.keys)
    tier_overrides = {}
    for s in a.tier_for:
        k, _, t = s.partition("=")
        if t not in TIERS:
            sys.exit(f"--tier-for {s}: tier must be {'|'.join(TIERS)}")
        if any(part not in found for part in k.split("+")):
            sys.exit(f"--tier-for {s}: no such lens — run `lens.py index`")
        if k not in selected:
            sys.exit(f"--tier-for {s}: lens is not selected by --keys")
        tier_overrides[k] = t
    overrides = {}
    for s in a.effort_for:
        k, _, e = s.partition("=")
        if a.target == "native" and e not in NATIVE_EFFORTS:
            sys.exit(f"--effort-for {s}: effort must be {'|'.join(NATIVE_EFFORTS)}")
        if any(m not in found for m in k.split("+")):
            sys.exit(f"--effort-for {s}: no such lens — run `lens.py index`")
        if k not in selected:
            sys.exit(f"--effort-for {s}: lens is not selected by --keys")
        overrides[k] = e
    model_overrides = {}
    for s in a.model_for:
        k, _, m = s.partition("=")
        if a.target == "native" and m not in NATIVE_PINS:
            sys.exit(f"--model-for {s}: model must be {'|'.join(NATIVE_PINS)}")
        if any(part not in found for part in k.split("+")):
            sys.exit(f"--model-for {s}: no such lens — run `lens.py index`")
        if k not in selected:
            sys.exit(f"--model-for {s}: lens is not selected by --keys")
        model_overrides[k] = m
    must_overrides = {}
    for s in a.must_read_for:
        k, _, spec = s.partition("=")
        if any(part not in found for part in k.split("+")):
            sys.exit(f"--must-read-for {s}: no such lens — run `lens.py index`")
        if k not in selected:
            sys.exit(f"--must-read-for {s}: lens is not selected by --keys")
        must_overrides[k] = must_read_entries(must_read_list(spec, f"--must-read-for {k}"))
    panel_must = must_read_entries(must_read_list(a.must_read, "--must-read")) if a.must_read else None
    unlisted = [k for k in a.keys if k not in must_overrides and panel_must is None]
    if unlisted:
        sys.exit(f"{', '.join(unlisted)}: no must-read list. Pass --must-read <list file> for the panel or "
                 f"--must-read-for KEY=<list file> per seat: the subject files or diff paths each lens covers "
                 f"(commands/agents/review_agents.md *Seat coverage contract*)")
    grounding_seats = [k for k in a.keys if "plc-evidence-grounding" in seats[k]]
    grounding_paths = list(dict.fromkeys(entry["path"] for k in grounding_seats
                                        for entry in (must_overrides.get(k) or panel_must)))
    grounding = grounding_behavior_section(subjects, grounding_paths, out=out, prefix=a.prefix) if grounding_seats else ""
    over = []

    # The profile the sidecar briefs must fit: the review row's built-ins. `author` would widen the
    # seat (Edit/Write/Bash) and the `arm`/bench rows are benchmark grammars, never a review route.
    builtins = None
    if a.target == "sidecar":
        try:
            builtins = tool_profiles.resolve(REVIEW_SHAPE, walk_mcp=False)[0]["builtins"]
        except ValueError as err:
            sys.exit(f"the review tool profile does not resolve: {err}")

    if a.pr_num is not None:
        subject = f"PR #{a.pr_num}"
    elif a.branch:
        subject = f"branch {a.branch}"
    else:
        subject = a.prefix

    unfit = lens_tier_refusal(panel={k: model_overrides.get(k) or a.model for k in a.keys},
                              native=a.target == "native", data=data)
    if unfit:
        sys.exit("refused before any brief is built: a seat's pin must be a row serving its lens tier.\n"
                 + "\n".join(unfit))
    jobs = []
    briefs = []
    for k in a.keys:
        prompts = []
        for key in seats[k]:
            m = FENCE_RE.search("".join(found[key].lines))
            if not m:
                sys.exit(f"{key}: no fenced prompt block in its template")
            prompt = m.group(1)
            # Check the TEMPLATE's placeholders, not the substituted text: a substitution that carries
            # a diff of a registry file legitimately contains `{{NAME}}` strings.
            left = sorted(set(PLACEHOLDER_RE.findall(prompt)) - set(subs))
            if left:
                named = [FLAG_PLACEHOLDERS[n] for n in left if n in FLAG_PLACEHOLDERS]
                if named:
                    sys.exit(f"{key}: its template uses {{{{{', '.join(n for n in left if n in FLAG_PLACEHOLDERS)}}}}}"
                             f" — pass {' and '.join(named)}")
                sys.exit(f"{key}: unresolved placeholders {left} — pass --sub NAME=<file>")
            prompts.append(PLACEHOLDER_RE.sub(lambda mm: subs.get(mm.group(1), mm.group(0)), prompt).rstrip())
        if len(prompts) == 1:
            lens_part = "# Your lens\n\n" + prompts[0] + "\n"
        else:
            lens_part = (
                f"# Your seat: {len(prompts)} mandates\n\n"
                "Run every mandate below. Set each finding's `agent` to the mandate key that produced it. "
                "In `checked.basis`, write one `<mandate key>: <outcome>` clause per mandate, separated by "
                "`;`, where the outcome is findings, clean with the evidence checked, or not reached "
                "(orchestration §2 *Sizing the width*).\n\n"
                + "\n\n".join(f"## Mandate `{key}`\n\n{p}" for key, p in zip(seats[k], prompts)) + "\n"
            )
        preamble = lens_facing(preambles[found[seats[k][0]].registry])
        if any(found[key].kfm for key in seats[k]):
            lens_part += "\n" + kfm_section()
        if inbound and (any(found[key].citers for key in seats[k]) or any(shape == "meta" for shape, _path in subjects)):
            lens_part += "\n" + inbound
        if grounding and k in grounding_seats:
            lens_part += "\n" + grounding
        preloaded = lens.doctrine([found[key] for key in seats[k]])
        tier = lens.seat_tier(k) or tier_overrides.get(k) or a.tier
        if k not in overrides and a.effort is None:
            why = (f"{k}: {a.target} review needs --effort or --effort-for from the ladder; "
                   "the lens tier does not select a reasoning rung")
            # Native's pins are argv-shaped, so its refusal is an argument error (exit 2); the
            # sidecar route keeps its own semantic exit.
            if a.target == "native":
                ap.error(why)
            sys.exit(why)
        effort = overrides.get(k) or a.effort
        must = must_overrides.get(k) or panel_must
        if grounding and k in grounding_seats:
            artifact = (out / f"{a.prefix}_behavior.json").resolve().as_posix()
            must = must + must_read_entries([artifact])
        lens_part += acceptance + "\n" + must_read_section(must)

        if a.target == "native":
            if a.pr_num is not None:
                # The generated bytes are a contract for every caller that passes --pr-num.
                header = (f"<!-- lens brief: {k} for PR #{a.pr_num} — generated by "
                          f"tools/lens_briefs.py; do not edit -->")
            else:
                header = (f"<!-- lens brief: {k} for {subject} — generated by "
                          f"tools/lens_briefs.py; do not edit -->")
            body = (header + "\n\n# Shared rules for every lens (registry preamble)\n\n"
                    + preamble + "\n\n" + lens_part + ("\n" + preloaded if preloaded else ""))
            path = out / f"{a.prefix}_{k}.md"
            briefs.append((path, body))
            over.append(seat_overage(key=k, token=model_overrides.get(k) or a.model, body=body,
                                     context_path=a.context_path, entries=must, data=data))
            jobs.append({
                "label": f"{a.prefix}-{k}",
                "seat": k,
                "mandates": seats[k],
                "mustRead": must,
                "promptPath": str(path.resolve()).replace("\\", "/"),
                "model": model_overrides.get(k) or a.model,
                "tier": tier,
                "effort": effort,
                "agentType": "general-purpose",
                "readOnly": True,
            })
            continue

        token = model_overrides.get(k) or a.model
        entry = resolve_sidecar_entry(token, f"--model-for {k}" if k in model_overrides else "--model", data)
        written = tier_overrides.get(k) or a.tier
        if written and not serves_tier(entry, written):
            sys.exit(f"{k}: {token} does not claim {written} or a stronger review tier; "
                     "re-select a qualified row from model_registry.py for-role")
        rungs = effort_rungs(entry, data)
        if rungs and effort not in rungs:
            sys.exit(f"{k}: effort {effort!r} is not a rung {entry['alias']} serves "
                     f"({', '.join(rungs)}) — pass --effort or --effort-for with a legal rung")
        parts = [f"<!-- lens brief: {k} for {subject} — sidecar review seat; generated by "
                 f"tools/lens_briefs.py; do not edit -->",
                 "", "# Shared rules for every lens (registry preamble)", "", preamble, "",
                 sidecar_tool_block(a.context_path, builtins), ""]
        parts.append(lens_part)
        if preloaded:
            parts.append(preloaded)
        body = "\n".join(parts)
        path = out / f"{a.prefix}_{k}.md"
        # The gate reads the brief text under its final path name and the context it points at, so
        # the refusal quotes the real line. Every brief is written only after the whole panel passes.
        try:
            context_text = tool_profiles.read_brief(a.context_path)
        except OSError as exc:
            sys.exit(f"--context-path unreadable: {a.context_path} ({exc})")
        refusal = tool_profiles.brief_refusal(
            builtins, tool_profiles.brief_requirements(
                [(str(path), body), (a.context_path, context_text)], str(REPO)), "job")
        if refusal:
            sys.exit(f"{path.name}: {refusal}\nThis route dispatches a READ-ONLY review seat: rework "
                     f"the line to a read-only input, or move that mandate to a Bash-enabled seat. "
                     f"A sidecar job never grants Bash and never takes the `author` shape (C6).")
        over.append(seat_overage(key=k, token=token, body=body, context_path=a.context_path, entries=must,
                                 data=data))
        jobs.append({
            "label": f"{a.prefix}-{k}",
            "mandates": seats[k],
            "mustRead": must,
            "alias": entry["alias"],
            "promptFile": str(path.resolve()).replace("\\", "/"),
            "tier": tier,
            "effort": effort,
            "disclosure": "full",
            "shape": REVIEW_SHAPE,
            "schemaFile": str(schema).replace("\\", "/"),
            "workdir": str(REPO).replace("\\", "/"),
            "contextFiles": [a.context_path],
        })
        briefs.append((path, body))
    refused = [line for line in over if line]
    if refused:
        sys.exit("refused before launch: split each named seat's must-read list across seats with "
                 "--must-read-for KEY=<list file>, or pin a row with a larger window; never truncate a list.\n"
                 + "\n".join(refused))
    for path, body in briefs:
        path.write_text(body, encoding="utf-8", newline="\n")
    jp = out / f"jobs_{a.prefix}.json"
    artifact = jobs
    if a.plan:
        plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
        if "reviewerSelection" in plan:
            if not isinstance(plan["reviewerSelection"], dict):
                sys.exit("--plan reviewerSelection must be an object")
            artifact = {"jobs": jobs, "roster": plan["roster"], "reviewerSelection": plan["reviewerSelection"]}
    jp.write_text(json.dumps(artifact, indent=2), encoding="utf-8", newline="\n")
    print(f"wrote {len(jobs)} briefs under {out} and {jp.name}")
    for j in jobs:
        if a.target == "native":
            print(f"  {j['label']:36} {j['model']:7} {j['effort']:6} "
                  f"{Path(j['promptPath']).stat().st_size:7,}B")
        else:
            print(f"  {j['label']:36} {j['alias']:12} {j['effort']:6} "
                  f"{Path(j['promptFile']).stat().st_size:7,}B")


if __name__ == "__main__":
    main()
