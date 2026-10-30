"""Contract proof for the drive spine, dispatch topology, explore selection, and salvage recovery."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


ORCHESTRATION = ".claude/skills/orchestration/SKILL.md"
DRIVE_DISPATCH = ".claude/skills/orchestration/reference/drive_dispatch.md"
CHECKPOINTING = ".claude/skills/orchestration/reference/checkpointing.md"
LATER_ROUNDS = ".claude/skills/orchestration/reference/later_rounds.md"
REVIEW_WIDTH = ".claude/skills/orchestration/reference/review_width.md"
EXECUTION_DEPTH = ".claude/skills/_brainstorm_shared/execution_depth.md"
SPINE = ".claude/skills/_brainstorm_shared/drive_spine.md"
AUDIT_AGENTS = ".claude/commands/agents/session_audit_agents.md"
HEADER_ADAPTERS = (
    ".claude/commands/part_drive.md",
    ".claude/commands/part_execute.md",
    ".claude/commands/feature_drive.md",
    ".claude/commands/design_drive.md",
    ".claude/commands/reference/worklog_drive.md",
)
OVERNIGHT = ".claude/commands/overnight.md"
DRIVE_FILES = HEADER_ADAPTERS + (".claude/commands/reference/worklog_drive_execute.md", OVERNIGHT)
HEADER_LABELS = ("**Input:**", "**Enters at:**", "**Owner stop:**")
H4_NEEDLES = (
    "trivial in-scope fix",
    "unaccounted baseline drop",
    "critical finding left unresolved",
    "absent, partial or timed-out",
    "never green",
    "quoted waiver of a named check exits H4 for that check only",
)


def drive_texts() -> dict:
    return {rel: text(rel) for rel in (SPINE, ORCHESTRATION, DRIVE_DISPATCH, CHECKPOINTING) + DRIVE_FILES}


def section(body: str, heading: str) -> str:
    if heading + "\n" not in body:
        return ""
    return body.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


def topology_errors(texts: dict) -> list:
    errors = []
    for heading in (
        "## 0. Dispatch Shape",
        "## 5. Model & Effort Selection",
        "## 11. Dispatch Doctrine",
        "## Pre-Dispatch Checklist",
    ):
        if heading not in texts[ORCHESTRATION]:
            errors.append("orchestration lacks " + heading)
    slices = section(texts[SPINE], "## Slices")
    for needle in ("orchestration", "§0", "§5", "§11", "Pre-Dispatch Checklist", "task_record.py job"):
        if needle not in slices:
            errors.append("spine §Slices lacks " + needle)
    for rel in DRIVE_FILES:
        if "dispatch topology receipt" in texts[rel].lower():
            errors.append(rel + " keeps the Dispatch Topology Receipt")
    return errors + slice_rule_errors(texts)


def slice_rule_errors(texts: dict) -> list:
    errors = []
    slices = section(texts[SPINE], "## Slices")
    verifier = re.search(r"^- A separate spec verifier.*?(?=^- |\Z)", slices, re.M | re.S)
    body = " ".join(verifier.group(0).split()) if verifier else ""
    if "executor is on probation" not in body:
        errors.append("spine verifier bullet lacks the executor-on-probation trigger")
    if "unplanned configuration surface" in body:
        errors.append("spine verifier bullet keeps the interim unplanned-surface trigger")
    if "the plan's job map" not in slices or "write set and dependencies" not in slices:
        errors.append("spine §Slices lacks the slice-to-job map")
    doctrine = texts[DRIVE_DISPATCH].split("\n", 1)[-1]
    if "continue file" not in doctrine:
        errors.append("drive_dispatch.md lacks the continue file")
    if "Slices are commit units" in doctrine:
        errors.append("drive_dispatch.md keeps the Slices are commit units paragraph")
    return errors


def halt_list_errors(spine: str) -> list:
    halts = section(spine, "## Halts")
    leads = [int(n) for n in re.findall(r"^- \*\*H(\d)[:*]", halts, re.M)]
    errors = [] if leads == list(range(1, 7)) else ["spine §Halts leads are H%s, not H1..H6 once each" % leads]
    h4 = re.search(r"^- \*\*H4[:*].*?(?=^- |\Z)", halts, re.M | re.S)
    body = " ".join(h4.group(0).split()) if h4 else ""
    errors += ["spine H4 lacks " + needle for needle in H4_NEEDLES if needle not in body]
    return errors


def header_errors(rel: str, body: str) -> list:
    lines = body.splitlines()
    h1 = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    if h1 is None:
        return [rel + " has no H1"]
    first = [line for line in lines[h1 + 1:] if line.strip()][:3]
    errors = []
    for label, line in zip(HEADER_LABELS, first + [""] * (3 - len(first))):
        if not line.startswith(label):
            errors.append("%s header line %r is not %s" % (rel, line[:40], label))
    blocks = [block for block in re.split(r"\n\s*\n", "\n".join(lines[h1 + 1:])) if block.strip()]
    if rel == OVERNIGHT:
        if "_brainstorm_shared" in body:
            errors.append(rel + " names a _brainstorm_shared file")
    elif "drive_spine.md" not in "\n".join(blocks[:2]):
        errors.append(rel + " header block has no drive_spine.md link")
    return errors


def retired_valve_errors(rel: str, body: str) -> list:
    errors = []
    if re.search(r"^#+ .*halt valves", body, re.M | re.I):
        errors.append(rel + " keeps a Halt valves heading")
    if "valve (" in body.lower():
        errors.append(rel + " keeps a valve ( token")
    if re.search(r"halt \([a-g]\)", body, re.I):
        errors.append(rel + " keeps a letter halt")
    if "dispatch topology receipt" in body.lower():
        errors.append(rel + " keeps the Dispatch Topology Receipt")
    return errors


def spine_contract_errors(texts: dict) -> list:
    errors = halt_list_errors(texts[SPINE])
    for rel in HEADER_ADAPTERS + (OVERNIGHT,):
        errors += header_errors(rel, texts[rel])
    for rel in DRIVE_FILES:
        errors += retired_valve_errors(rel, texts[rel])
    return errors


def planted_errors(check, texts: dict, planted: dict) -> list:
    """Name every plant whose anchor is missing or whose mutant the check passes."""
    failures = []
    for name, (rel, old, new) in planted.items():
        if not old or old not in texts[rel]:
            failures.append("plant anchor missing: " + name)
        elif not check(dict(texts, **{rel: texts[rel].replace(old, new, 1)})):
            failures.append("mutant passed: " + name)
    return failures


def topology_plants(texts: dict) -> dict:
    return {
        "a lost orchestration heading": (ORCHESTRATION, "## 11. Dispatch Doctrine", "## 11. Doctrine"),
        "a spine without the job record": (SPINE, "task_record.py job", "the task record"),
        "a restored receipt": (".claude/commands/feature_drive.md", "\n", "\nCompose a Dispatch Topology Receipt.\n"),
        "a verifier without probation": (SPINE, "executor is on probation for the tier", "executor is flagged for the tier"),
        "the interim verifier trigger": (SPINE, "whose executor is on probation", "that adds an unplanned configuration surface"),
        "a spine without the slice map": (SPINE, "the plan's job map", "the plan"),
        "no continue file in drive_dispatch": (DRIVE_DISPATCH, "waits for the parent's continue file", "waits"),
        "a restored commit-units paragraph": (
            DRIVE_DISPATCH, "**Continue file.**",
            "**Slices are commit units, not dispatch units.** A delegate's edits get review.\n\n**Continue file.**"),
    }


def spine_contract_plants(texts: dict) -> dict:
    halts = section(texts[SPINE], "## Halts")
    bullet = {}
    for n in (3, 5):
        match = re.search(r"^- \*\*H%d[:*].*?(?=^- |\Z)" % n, halts, re.M | re.S)
        bullet[n] = match.group(0) if match else ""
    part = ".claude/commands/part_drive.md"
    labels = [line for line in texts[part].splitlines() if line.startswith(HEADER_LABELS)][:2]
    pointer = next((line for line in texts[part].splitlines() if "drive_spine.md" in line), "")
    plants = {
        "a duplicated H3": (SPINE, bullet[3], bullet[3] + bullet[3]),
        "a deleted H5": (SPINE, bullet[5], ""),
        "reordered header labels": (part, "\n".join(labels), "\n".join(reversed(labels))),
        "a removed spine pointer": (part, pointer, "This file holds only what a Part adds."),
        "overnight linking the spine": (OVERNIGHT, "\n## ",
                                        "\nSee [spine](../skills/_brainstorm_shared/drive_spine.md).\n\n## "),
        "a Halt valves heading": (".claude/commands/part_execute.md", "\n## ", "\n## Halt valves\n\n## "),
        "a letter valve": (".claude/commands/design_drive.md", "\n", "\nThat is valve (b).\n"),
        "a letter halt": (".claude/commands/feature_drive.md", "\n", "\nThat fires halt (d).\n"),
        "a restored receipt": (".claude/commands/reference/worklog_drive.md", "\n", "\nDispatch Topology Receipt\n"),
    }
    for needle in H4_NEEDLES:
        plants["an H4 without " + needle] = (SPINE, needle, "")
    return plants


def close_step(spine: str, number: int) -> str:
    match = re.search(r"^%d\. .*?(?=^\d+\. |\Z)" % number, section(spine, "## Close"), re.M | re.S)
    return match.group(0) if match else ""


CLOSE_STEP_NEEDLES = ("/session_audit", "--files", "review_ledger.py", "rows --task", "sa-architecture-sweep",
                      "--plan", "§Gate cadence")
SEAM_NEEDLES = ("--lens sa-architecture-sweep", "--plan", "--task", "task_record.py add", "H4")


def seam_bullet(spine: str) -> str:
    match = re.search(r"^- Seam review.*?(?=^- |\Z)", section(spine, "## Slices"), re.M | re.S)
    return match.group(0) if match else ""


def admission_row(depth: str, cell: str) -> str:
    match = re.search(r"^\| (?:`/session_audit` \| )?%s \|.*$" % re.escape(cell), depth, re.M)
    return match.group(0) if match else ""


def close_review_errors(texts: dict) -> list:
    errors = []
    step = close_step(texts[SPINE], 2)
    for needle in CLOSE_STEP_NEEDLES:
        if needle not in step:
            errors.append("spine §Close step 2 lacks " + needle)
    for needle in ("composition versus inheritance", "Design items", "units no check saw", "Its skip conditions"):
        if needle in step:
            errors.append("spine §Close step 2 still carries " + needle)
    seam = seam_bullet(texts[SPINE])
    for needle in SEAM_NEEDLES:
        if needle not in seam:
            errors.append("spine Seam review bullet lacks " + needle)
    for needle in ('/session_audit --files <the drive\'s files> --plan <plan path> --task <task id>',
                   '.claude/scratch/<task>/short_plan.md', 'Plan file row admitted none',
                   'conversation\'s short plan', 'pass that path'):
        if needle not in step:
            errors.append('spine §Close step 2 lacks mandatory plan delivery: ' + needle)
    if 'when plan check was not admitted' in step:
        errors.append('spine §Close step 2 keeps conditional plan delivery')
    for rel in (SPINE,) + DRIVE_FILES:
        for call in re.findall(r'`(/session_audit [^`]+)`', texts[rel]):
            if '--task' not in call or '--plan' not in call:
                errors.append(rel + ' issues a drive audit without both flags: ' + call)
    depth = texts[EXECUTION_DEPTH]
    if 'divergence from the approved plan and executor design the plan did not state' not in texts[AUDIT_AGENTS]:
        errors.append('architecture sweep template lacks the drive-close scope')
    seam_row = admission_row(depth, "Seam review")
    if not seam_row:
        errors.append("execution_depth has no Seam review row")
    elif "a later milestone" not in seam_row:
        errors.append("execution_depth Seam review row lacks the milestone trigger")
    if admission_row(depth, "Tracer review"):
        errors.append("execution_depth keeps the Tracer review row")
    if "first slice of a multi-slice Part" in depth:
        errors.append("execution_depth keeps the first-slice clause")
    if "at drive close" not in admission_row(depth, "`sa-architecture-sweep`"):
        errors.append("execution_depth sweep row lacks the drive-close case")
    if "whenever §Candidate width admits review" not in admission_row(depth, "`sa-architecture-sweep`"):
        errors.append("execution_depth sweep row lacks scaled width admission")
    if not admission_row(depth, "`sa-holistic`"):
        errors.append("execution_depth has no sa-holistic row")
    if "Skip mandates the review ledger covers" not in admission_row(depth, "Candidate review"):
        errors.append("execution_depth Candidate review row lacks the ledger skip")
    if "Skipped only when every one of these holds" in depth:
        errors.append("execution_depth Candidate review row keeps the retired skip clause")
    if "candidate review owed" not in depth:
        errors.append("execution_depth §Recording lacks the owed print")
    if "review_ledger.py rows" not in depth:
        errors.append("execution_depth Models lacks the inline-work evidence")
    orchestration = texts[ORCHESTRATION] + texts[CHECKPOINTING] + texts.get(REVIEW_WIDTH, "") + text(LATER_ROUNDS)
    for needle in ("review_ledger.py plan", "--degraded", "--ignore-ledger", "review_ledger.py rows --task"):
        if needle not in orchestration:
            errors.append("orchestration lacks " + needle)
    return errors


def close_review_plants(texts: dict) -> dict:
    step = close_step(texts[SPINE], 2)
    seam = seam_bullet(texts[SPINE])
    depth = texts[EXECUTION_DEPTH]
    seam_row = admission_row(depth, "Seam review")
    sweep_row = admission_row(depth, "`sa-architecture-sweep`")
    holistic_row = admission_row(depth, "`sa-holistic`")
    if not (step and seam and seam_row and sweep_row and holistic_row):
        return {}
    plants = {
        'no short-plan write': (SPINE, '.claude/scratch/<task>/short_plan.md', 'the conversation'),
        'no fallback admission condition': (SPINE, 'Plan file row admitted none', 'plan check did not run'),
        'no fallback path passed': (SPINE, 'pass that path', 'continue'),
        'conditional plan delivery restored': (SPINE, step, step + ' when plan check was not admitted\n'),
        'a later drive audit without plan': (SPINE, step, step + '`/session_audit --files changed --task task`\n'),
        'a later drive audit without task': (SPINE, step, step + '`/session_audit --files changed --plan plan.md`\n'),
        'drive-close scope lost': (AUDIT_AGENTS, 'divergence from the approved plan and executor design the plan did not state', 'design ideality'),
        "the four-axis list restored": (SPINE, step, step + " on composition versus inheritance and the Design items.\n"),
        "the narrowing restored": (SPINE, step, step + " Over the units no check saw.\n"),
        "a step without the audit command": (SPINE, step, step.replace("/session_audit --files", "a review")),
        "a Tracer review row": (EXECUTION_DEPTH, "| Seam review |", "| Tracer review |"),
        "the first-slice clause restored": (EXECUTION_DEPTH, seam_row,
                                            seam_row[:-1] + "; also the first slice of a multi-slice Part |"),
        "a seam row without the milestone trigger": (EXECUTION_DEPTH, seam_row,
                                                     seam_row.replace("a later milestone", "a later slice")),
        "a sweep row without the drive close": (EXECUTION_DEPTH, sweep_row, sweep_row.replace("at drive close", "")),
        "a sweep row without width admission": (EXECUTION_DEPTH, sweep_row, sweep_row.replace("whenever §Candidate width admits review", "")),
        "no sa-holistic row": (EXECUTION_DEPTH, holistic_row + "\n", ""),
        "the retired skip clause": (EXECUTION_DEPTH, "Skip mandates the review ledger covers",
                                    "Skipped only when every one of these holds"),
        "no owed print": (EXECUTION_DEPTH, "candidate review owed", "review owed"),
        "no cross-family sentence": (REVIEW_WIDTH, "review_ledger.py plan", "the planner"),
        "no ledger pointer in checkpointing": (CHECKPOINTING, "review_ledger.py rows --task", "the rows"),
    }
    for needle in CLOSE_STEP_NEEDLES:
        plants["a step without " + needle] = (SPINE, step, step.replace(needle, ""))
    for needle in SEAM_NEEDLES:
        plants["a seam bullet without " + needle] = (SPINE, seam, seam.replace(needle, ""))
    return plants


def test_every_drive_composes_the_existing_topology_owners():
    texts = drive_texts()
    errors = topology_errors(texts)
    assert not errors, errors
    failures = planted_errors(topology_errors, texts, topology_plants(texts))
    assert not failures, failures


def test_drive_spine_owns_the_halt_list():
    texts = drive_texts()
    errors = spine_contract_errors(texts)
    assert not errors, errors
    failures = planted_errors(spine_contract_errors, texts, spine_contract_plants(texts))
    assert not failures, failures


def test_close_review_runs_through_the_audit_and_the_ledger():
    texts = drive_texts()
    texts[EXECUTION_DEPTH] = text(EXECUTION_DEPTH)
    texts[REVIEW_WIDTH] = text(REVIEW_WIDTH)
    texts[AUDIT_AGENTS] = text(AUDIT_AGENTS)
    errors = close_review_errors(texts)
    assert not errors, errors
    plants = close_review_plants(texts)
    assert plants, "close step 2, the Seam review bullet or an admission row is missing"
    failures = planted_errors(close_review_errors, texts, plants)
    assert not failures, failures


def test_explore_selector_excludes_meta_and_merges_contained_bespoke_work():
    body = text(".claude/commands/agents/explore_agents.md")
    lower = body.lower()
    assert "harness/meta" in lower
    assert "never trigger `exp-design-source`" in lower or "never triggers `exp-design-source`" in lower
    assert "contained by a selected mandate" in lower
    assert "extends that prompt" in lower
    assert "instead of adding a seat" in lower


def test_stateful_source_authority_has_live_and_history_branches():
    body = text(".claude/skills/instruction_quality/SKILL.md")
    lower = body.lower()
    for needle in (
        "snapshot the live owner",
        "enumerate the complete intended population",
        "typed outcome",
        "reconcile totals",
        "recheck revision",
        "intent/history",
        "conversation/transcript",
    ):
        assert needle in lower, needle


def _sections(raw: str) -> dict:
    """Lowercased `## ` sections of a command file, each whitespace-normalized, keyed by heading."""
    parts = ("\n" + raw).split("\n## ")
    return {part.split("\n", 1)[0].strip(): " ".join(part.split("\n", 1)[1].split()) for part in parts[1:]}


def test_salvage_contract_covers_terminal_schema_and_typed_recovery():
    body = text(".claude/commands/salvage_fanout.md")
    sections = _sections(body.lower())
    inputs, procedure, output = sections["inputs"], sections["procedure"], sections["output contract"]
    lower = " ".join(body.lower().split())
    for needle in ("<transcriptdir-or-run-receipt>", "<lens-key>", "--kind explore|review|dispatch", "--spill-dir <path>",
                   "missing or unknown workflow kind"):
        assert needle in inputs, needle
    for needle in (
        "session_digest.py --workflow-dir",
        "`session_digest` supports only explore and review",
        "`dispatch` reads `journal.jsonl` directly",
        "selected `started`",
        "agent-<agentid>.jsonl",
        "exact spill path",
        "uses `claims`",
        "free-form job result",
        "require the engine's terminal row",
        "still running",
        "resumable session",
        "native workflow",
        "parent reconstruction",
        "complete tool results",
        "mandate engagement",
        "failed-lens-only",
        "native workflow child ids are not sendmessage sessions",
        "recovered",
        "uncovered",
        "redispatched",
        "accounted lenses n/n",
    ):
        assert needle in procedure, needle
    assert "the final report names the resolved kind" in output
    assert "<key>.spill.md" not in lower
    assert "workflow" in body.splitlines()[1].lower()
    assert "dispatch topology gate" not in lower
    for needle in ("orchestration", "pre-dispatch checklist", "task_record.py job"):
        assert needle in lower, needle
    assert "dispatch topology receipt" not in lower


if __name__ == "__main__":
    test_every_drive_composes_the_existing_topology_owners()
    test_drive_spine_owns_the_halt_list()
    test_close_review_runs_through_the_audit_and_the_ledger()
    test_explore_selector_excludes_meta_and_merges_contained_bespoke_work()
    test_stateful_source_authority_has_live_and_history_branches()
    test_salvage_contract_covers_terminal_schema_and_typed_recovery()
    print("ok")
