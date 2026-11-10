"""Proof for tools/worklog.py `complete` and `add`.

Fixtures keep the real files' structure — frontmatter, Active/Ledger supersections, domain
headings, blocks with every sub-bullet form, the Future Scope callout, Linked Docs, CRLF — with
neutral text: no game noun, no person, no machine path. The canonical class and domain tables are
planted here, and every `add` run is pointed at the plant, so the proof passes in a project whose
worklog_reference names other domains. `last_updated` / `Last synced` are set to a fixed old date
so a run on any day proves the bump.

    python3 .claude/tests/test_worklog.py
"""
import datetime
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOL = REPO / ".claude" / "tools" / "worklog.py"
TODAY = datetime.date.today().isoformat()
OLD = "2026-01-01"

failures = []
ran = 0


def check(label, cond, detail=""):
    global ran
    ran += 1
    print("%-4s %s" % ("ok" if cond else "FAIL", label))
    if not cond:
        failures.append("%s %s" % (label, detail))


# --- planted canonical tables (shaped like the real SKILL.md, naming no project's domains) ---
SKILL_PLANT = """---
name: worklog_reference
description: Planted tables for the proof.
---

# Worklog Reference

## Classification (required on every item)

| Class | When to use |
|-------|-------------|
| `fix` | Known bug, known or quickly-determinable repair path |
| `debug` | Unknown root cause, investigation needed before any fix |
| `feature` | New behavior, capability, or content |
| `refactor` | Restructure with no behavior change |
| `test` | Add or expand test coverage |
| `docs` | Documentation-only update |
| `chore` | Tooling, config, build, data-file maintenance |
| `design` | Brainstorming/planning, no implementation yet |

## Scope (required on every item)

| Scope | Meaning | Heuristic |
|-------|---------|-----------|
| **1** | Mechanical, locally verifiable | A small change with an obvious check |

## Canonical domain list

| Long form (Active section header) | Short form (mirror line) |
|-----------------------------------|--------------------------|
| Alpha | `alpha` |
| Beta / Gamma | `beta` |
| Delta | `delta` |
| Other | `other` |

## Full Trigger Catalog (auto-detect signals)

None in the plant.
"""
PLANT_CLASSES = ("fix", "debug", "feature", "refactor", "test", "docs", "chore", "design")
PLANT_DOMAINS = (("Alpha", "alpha"), ("Beta / Gamma", "beta"), ("Delta", "delta"), ("Other", "other"))

TARGET = "Probe the gate reports a missing line as a clean run"
TARGET_BLOCK = (
    "- [ ] **%s** — `debug` · scope `1` · added 2026-10-29\n"
    "  - Context: the gate printed nothing for a missing line, so a reader took the run as clean.\n"
    "  - Where: scripts/probe_gate.py\n"
    "  - Source: session probe1 F14\n" % TARGET
)

W1 = """---
title: Worklog
status: live
last_updated: %s
---

# Worklog

## Active

### Alpha
%s- [ ] **Probe the runner retries a moved base instead of stopping** — `fix` · scope `2` · added 2026-10-30
  - Context: a run went red only at the base step after a peer moved ahead in the queue; each retry re-waits the whole queue. A conflict-free replay can be proven and fast-forwarded by the runner itself.
  - Where: scripts/probe_run.py
  - Source: session probe1

## Ledger

### Delta
- [ ] **Probe the queue repeat runs for four thin gaps** — `chore` · scope `1` · added 2026-10-10
  - Context: the board sweep found gaps resting on one run or inside the run-to-run band, so no cell changed.
  - Where: scripts/probe_board.py
  - Source: board sweep c6d752fb7, session probe2
  - When: after a board pick hinges on these cells
""" % (OLD, TARGET_BLOCK)

A1 = """---
title: Worklog Archive
status: archive
last_updated: %s
---

# Worklog Archive

> Completed worklog items, moved out of `Worklog.md` on completion to keep the active doc lean.
> Opaque to passive agent context: never mirrored, never loaded at SessionStart, never scanned by sweep/triage/plan.
> Read only via `/worklog history` or on explicit request. Newest completions append at the bottom of each domain section.

## Delta
- [x] `fix` · scope `1` · Probe each failing assertion keeps its message in the gate detail (completed 2026-09-30, ab8483215 + a11ed70cb: every run keeps its own log under runs/<runid>/, and the wrapper prints the failed name)
- [x] `fix` · scope `2` · Probe the counter charges one slipped cite twice (completed 2026-10-29, 4d8671541 engine 1.13)

## Alpha
- [x] `fix` · scope `2` · Probe the dry run runs the two lanes before the merge (completed 2026-11-09, dropped by the owner: the check stays; the script runs the lanes by hand)
""" % OLD

MIRROR_LINE = "- debug · 1 · " + TARGET
M1 = """# Worklog Titles

<!--
Derived mirror of Obsidian `Claude/TODO/Worklog.md`. `/worklog` is the only writer and patches it
incrementally; `/worklog show` full-regenerates it when it drifts from the source. Do not hand-edit.
-->

Last synced: %s

## Active (2)

### Alpha
%s
- fix · 2 · Probe the runner retries a moved base instead of stopping

## Ledger (1)

### Delta
- chore · 1 · Probe the queue repeat runs for four thin gaps [after: a board pick hinges on these cells]
""" % (OLD, MIRROR_LINE)

XLINE = "- [x] `debug` · scope `1` · %s (completed %s, 0b820491e)" % (TARGET, TODAY)

W2 = """---
title: Worklog
status: live
last_updated: %s
---

# Worklog

## Active

### Other
- [ ] **Apply the board sweep** — `chore` · scope `2` · added 2026-10-29
  - Context: three board cells rest on one run or inside the run-to-run band.
  - Where: scripts/probe_board.py
  - Source: session probe1

## Ledger

### Delta
- [ ] **Probe the queue repeat runs for four thin gaps** — `chore` · scope `1` · added 2026-10-10
  - Context: the board sweep found gaps resting on one run or inside the run-to-run band.
  - When: after a board pick hinges on these cells
""" % OLD

M2 = """# Worklog Titles

Last synced: %s

## Active (1)

### Other
- chore · 2 · Apply the board sweep

## Ledger (1)

### Delta
- chore · 1 · Probe the queue repeat runs for four thin gaps [after: a board pick hinges on these cells]
""" % OLD

W3 = """---
title: Worklog
status: live
last_updated: %s
---

# Worklog

## Active

### Alpha
- [ ] **Run the probe models through a sibling transport** — `design` · scope `4` · added 2026-09-23
  - Context: the probe models refuse the plain client by policy; a transport whose child is the sibling runner makes them usable for read-only jobs, but must rebuild the profiles, guards and the run record.
  - Where: scripts/probe_transport.sh (sibling transport)
  - Source: session probe2 (fd4ff546 pickup)
  - When: after the owner runs `probe login`
  - Plan doc: [[Probe-Transport]]
  - Quick-win: flagged 2026-10-20
- [ ] **Probe the runner retries a moved base instead of stopping** — `fix` · scope `2` · added 2026-10-30
  - Context: a run went red only at the base step.
  - Where: scripts/probe_run.py
  - Source: session probe1
""" % OLD

M3 = """# Worklog Titles

Last synced: %s

## Active (2)

### Alpha
- design · 4 · Run the probe models through a sibling transport [after: the owner runs `probe login`] [quick-win]
- fix · 2 · Probe the runner retries a moved base instead of stopping
""" % OLD

A_TITLE = "Probe the runner retries a moved base instead of stopping"
A_TITLE_NEAR = "Probe the runner retry a moved base instead of stopping"
B_TITLE = "Probe the duplicate provider is located on both sides"
L_TITLE = "Probe the queue repeat runs for four thin gaps"
L_WHEN = "a board pick hinges on these cells"

A_BLOCK = (
    "- [ ] **%s** — `fix` · scope `2` · added 2026-10-30\n"
    "  - Context: a run went red only at the base step after a peer moved ahead in the queue.\n"
    "  - Where: scripts/probe_run.py\n"
    "  - Source: session probe1\n" % A_TITLE)
B_BLOCK = (
    "- [ ] **%s** — `debug` · scope `3` · added 2026-07-31\n"
    "  - Context: MERGED 2026-08-25 from both side entries — same defect class, one root cause to find.\n"
    "  - Source: gate review 2026-07-31\n" % B_TITLE)
L_BLOCK = (
    "- [ ] **%s** — `chore` · scope `1` · added 2026-10-10\n"
    "  - Context: the board sweep found gaps resting on one run or inside the run-to-run band.\n"
    "  - When: after %s\n" % (L_TITLE, L_WHEN))

# Cut from the live Worklog.md order: Active, Ledger, Future Scope callout, Linked Docs.
W4 = """---
title: Worklog
status: live
last_updated: %s
---

# Worklog

## Active

### Alpha
%s
### Beta / Gamma
%s
## Ledger

### Delta
%s
## Future Scope

> [!example]- Future Scope (2 items)
> <!-- Distant-horizon items (When: future). One-liner format only — excluded from `.claude/worklog-titles.md` mirror. -->
> ### Alpha
> - `chore` · scope `1` · Existing future queue idea (added 2026-05-31; a distant idea)
>
> ### Delta
> - `design` · scope `1` · Existing future probe idea (added 2026-05-31; another distant idea)

## Linked Docs

> Larger tracked items with their own design docs in `TODO/`.
- [[Existing-Doc]] — existing doc (Active: "Some item", added 2025-12-01)
""" % (OLD, A_BLOCK, B_BLOCK, L_BLOCK)

M4 = """# Worklog Titles

<!--
Derived mirror of Obsidian `Claude/TODO/Worklog.md`. Do not hand-edit.
-->

Last synced: %s

## Active (2)

### Alpha
- fix · 2 · %s

### Beta / Gamma
- debug · 3 · %s

## Ledger (1)

### Delta
- chore · 1 · %s [after: %s]
""" % (OLD, A_TITLE, B_TITLE, L_TITLE, L_WHEN)


def stage(tmp, worklog=W1, archive=A1, mirror=M1, history=None):
    p = pathlib.Path(tmp)
    (p / "Worklog.md").write_bytes(worklog.encode("utf-8"))
    if archive is not None:
        (p / "Worklog-Archive.md").write_bytes(archive.encode("utf-8"))
    (p / "worklog-titles.md").write_bytes(mirror.encode("utf-8"))
    (p / "SKILL.md").write_bytes(SKILL_PLANT.encode("utf-8"))
    if history is not None:
        (p / "history.jsonl").write_bytes(history.encode("utf-8"))
    return p


def run(p, *args, archive_name="Worklog-Archive.md"):
    cmd = [sys.executable, str(TOOL), "complete", *args,
           "--worklog", str(p / "Worklog.md"),
           "--archive", str(p / archive_name),
           "--mirror", str(p / "worklog-titles.md"),
           "--history", str(p / "history.jsonl")]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def run_add(p, *args, skill=None):
    skill = str(p / "SKILL.md") if skill is None else skill
    cmd = [sys.executable, str(TOOL), "add", *args,
           "--skill", skill,
           "--worklog", str(p / "Worklog.md"),
           "--archive", str(p / "Worklog-Archive.md"),
           "--mirror", str(p / "worklog-titles.md"),
           "--history", str(p / "history.jsonl")]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def snapshot(p):
    return tuple(read(p, n) for n in ("Worklog.md", "worklog-titles.md", "Worklog-Archive.md"))


def read(p, name):
    return (p / name).read_bytes().decode("utf-8")


# --- exact title: block gone, XLINE last in its domain, both dates bumped, mirror line gone ---
p = stage(tempfile.mkdtemp(prefix="wl_exact_"))
r = run(p, TARGET, "--ref", "0b820491e")
check("exact title completes (rc=0)", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("exact: block gone", TARGET_BLOCK not in wl)
check("exact: the next block survives", "Probe the runner retries" in wl)
check("exact: worklog last_updated bumped", "last_updated: %s" % TODAY in wl, wl[:120])
expected_wl = W1.replace(TARGET_BLOCK, "").replace("last_updated: " + OLD, "last_updated: " + TODAY)
check("exact: every other worklog byte unchanged", wl == expected_wl)
ar = read(p, "Worklog-Archive.md")
check("exact: XLINE is the last line of its domain section", ar == A1.replace(
    "last_updated: " + OLD, "last_updated: " + TODAY) + XLINE + "\n", ar[-260:])
mi = read(p, "worklog-titles.md")
check("exact: mirror line gone", MIRROR_LINE + "\n" not in mi)
check("exact: mirror Last synced bumped", "Last synced: %s" % TODAY in mi)
check("exact: mirror Active count decremented", "## Active (1)" in mi)
expected_mi = (M1.replace("Last synced: " + OLD, "Last synced: " + TODAY)
               .replace(MIRROR_LINE + "\n", "").replace("## Active (2)", "## Active (1)"))
check("exact: every other mirror byte unchanged", mi == expected_mi)

# --- every optional sub-bullet leaves with the block ---
p = stage(tempfile.mkdtemp(prefix="wl_opts_"), worklog=W3, mirror=M3)
r = run(p, "Run the probe models through a sibling transport")
wl = read(p, "Worklog.md")
check("optional sub-bullets: completes (rc=0)", r.returncode == 0, r.stderr[-400:])
check("optional sub-bullets: no line of the block survives",
      not any(s in wl for s in ("Plan doc: [[Probe-Transport]]",
                                "Quick-win: flagged 2026-10-20",
                                "When: after the owner runs `probe login`")))
check("optional sub-bullets: next block survives", "Probe the runner retries" in wl)

# --- last item of a domain: heading goes too, in Worklog and the mirror; new archive domain ---
p = stage(tempfile.mkdtemp(prefix="wl_last_"), worklog=W2, mirror=M2)
r = run(p, "Apply the board sweep", "--ref", "abc1234")
wl = read(p, "Worklog.md")
check("last item: completes (rc=0)", r.returncode == 0, r.stderr[-400:])
check("last item: worklog domain heading gone", "### Other" not in wl)
check("last item: other domain heading stays", "### Delta" in wl)
mi = read(p, "worklog-titles.md")
check("last item: mirror category heading gone", "### Other" not in mi)
check("last item: mirror ledger heading stays", "### Delta" in mi)
check("last item: mirror Active count decremented", "## Active (0)" in mi)
ar = read(p, "Worklog-Archive.md")
new_x = "- [x] `chore` · scope `2` · Apply the board sweep (completed %s, abc1234)" % TODAY
check("missing archive domain: new section appended",
      ar == A1.replace("last_updated: " + OLD, "last_updated: " + TODAY)
      + "\n## Other\n" + new_x + "\n")

# --- archive does not exist: create it ---
p = stage(tempfile.mkdtemp(prefix="wl_noarch_"), archive=None)
r = run(p, TARGET, "--ref", "0b820491e", archive_name="new-archive.md")
check("missing archive: completes (rc=0)", r.returncode == 0, r.stderr[-400:])
ar = read(p, "new-archive.md") if (p / "new-archive.md").exists() else ""
check("missing archive: frontmatter and heading", ar.startswith(
    "---\ntitle: Worklog Archive\nstatus: archive\nlast_updated: %s\n---\n\n# Worklog Archive\n" % TODAY))
check("missing archive: opacity note", "> Completed worklog items" in ar)
check("missing archive: domain section holds the XLINE", "## Alpha\n" + XLINE + "\n" in ar)
check("missing archive: worklog block gone", TARGET_BLOCK not in read(p, "Worklog.md"))

# --- ambiguous: exit 2, nothing written ---
p = stage(tempfile.mkdtemp(prefix="wl_amb_"))
before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
r = run(p, "run")
check("ambiguous: exit 2", r.returncode == 2, r.returncode)
check("ambiguous: candidates listed", "Probe the runner retries" in r.stdout and "Probe the queue repeat runs" in r.stdout, r.stdout)
check("ambiguous: no write", (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before)

# --- no match: exit 2, nothing written ---
p = stage(tempfile.mkdtemp(prefix="wl_none_"))
before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
r = run(p, "zzz no such item")
check("no match: exit 2", r.returncode == 2, r.returncode)
check("no match: no write", (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before)

# --- CRLF files stay CRLF ---
p = stage(tempfile.mkdtemp(prefix="wl_crlf_"),
          worklog=W1.replace("\n", "\r\n"), archive=A1.replace("\n", "\r\n"),
          mirror=M1.replace("\n", "\r\n"))
r = run(p, TARGET, "--ref", "0b820491e")
check("crlf: completes (rc=0)", r.returncode == 0, r.stderr[-400:])
for name in ("Worklog.md", "Worklog-Archive.md", "worklog-titles.md"):
    b = (p / name).read_bytes()
    check("crlf: %s has no bare LF" % name, b.count(b"\n") == b.count(b"\r\n"))
check("crlf: block gone", TARGET_BLOCK.replace("\n", "\r\n").encode() not in (p / "Worklog.md").read_bytes())
check("crlf: XLINE appended", XLINE.encode() in (p / "Worklog-Archive.md").read_bytes())

# --- dry run writes nothing and prints the plan ---
p = stage(tempfile.mkdtemp(prefix="wl_dry_"))
before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
r = run(p, TARGET, "--ref", "0b820491e", "--dry-run")
check("dry run: rc=0", r.returncode == 0, r.stderr[-400:])
check("dry run: no write", (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before)
check("dry run: prints the block with its range", "lines 12-15" in r.stdout and TARGET in r.stdout, r.stdout)
check("dry run: prints the XLINE", XLINE in r.stdout, r.stdout)
check("dry run: prints the archive section", "## Alpha" in r.stdout, r.stdout)
check("dry run: prints the mirror line", MIRROR_LINE in r.stdout, r.stdout)

# --- --from: one good line, one bad line ---
p = stage(tempfile.mkdtemp(prefix="wl_from_"))
(p / "batch.jsonl").write_text(
    json.dumps({"title": TARGET, "ref": "0b820491e"}) + "\n" +
    json.dumps({"title": "zzz no such item", "ref": "deadbee"}) + "\n", encoding="utf-8")
r = run(p, "--from", str(p / "batch.jsonl"))
check("--from: nonzero exit when a line misses", r.returncode != 0, r.returncode)
check("--from: completed line reported", "completed:" in r.stdout and TARGET in r.stdout, r.stdout)
check("--from: missed line reported", "no-match:" in r.stdout, r.stdout)
check("--from: the good item still completed", TARGET_BLOCK not in read(p, "Worklog.md"))
check("--from: the bad item wrote nothing", "zzz no such item" not in read(p, "Worklog-Archive.md"))

# --- tackle-history pairing ---
tackle = json.dumps({"event": "tackle", "date": "2026-11-08", "title": TARGET}) + "\n"
p = stage(tempfile.mkdtemp(prefix="wl_hist_"), history=tackle)
r = run(p, TARGET, "--ref", "0b820491e")
check("history: completes (rc=0)", r.returncode == 0, r.stderr[-400:])
rows = [json.loads(x) for x in read(p, "history.jsonl").splitlines() if x.strip()]
check("history: completion pairs with the tackle",
      rows[-1] == {"event": "completion", "date": TODAY, "title": TARGET}, rows)

no_tackle = json.dumps({"event": "tackle", "date": "2026-11-08", "title": "some other item"}) + "\n"
p = stage(tempfile.mkdtemp(prefix="wl_hist_none_"), history=no_tackle)
run(p, TARGET, "--ref", "0b820491e")
check("history: no matching tackle, none written", read(p, "history.jsonl") == no_tackle)

done = tackle + json.dumps({"event": "completion", "date": "2026-11-08", "title": TARGET}) + "\n"
p = stage(tempfile.mkdtemp(prefix="wl_hist_done_"), history=done)
run(p, TARGET, "--ref", "0b820491e")
check("history: an existing completion is not duplicated", read(p, "history.jsonl") == done)

# ================================ add ================================

def add(p, title, cls, scope, domain, context="probe context", *extra):
    return run_add(p, title, "--class", cls, "--scope", str(scope), "--domain", domain,
                   "--context", context, "--date", TODAY, *extra)


# --- Active add into an existing domain: top of the section, both dates, mirror line ---
T1 = "Probe the add tool's Active route"
T1_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context line\n" % (T1, TODAY))
p = stage(tempfile.mkdtemp(prefix="wl_add_active_"), worklog=W4, mirror=M4)
r = add(p, T1, "chore", 1, "Alpha", "probe context line")
check("add active: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
expected_wl = (W4.replace("### Alpha\n", "### Alpha\n" + T1_BLOCK + "\n", 1)
               .replace("last_updated: " + OLD, "last_updated: " + TODAY))
check("add active: block is the first block of its domain", wl == expected_wl, wl[:600])
check("add active: item date is the add date", "added %s" % TODAY in wl)
check("add active: archive untouched", read(p, "Worklog-Archive.md") == A1)
mi = read(p, "worklog-titles.md")
expected_mi = (M4.replace("## Active (2)", "## Active (3)", 1)
               .replace("### Alpha\n", "### Alpha\n- chore · 1 · " + T1 + "\n", 1)
               .replace("Last synced: " + OLD, "Last synced: " + TODAY))
check("add active: mirror line and count, every other byte unchanged", mi == expected_mi, mi[:600])
check("add active: history not created", not (p / "history.jsonl").exists())

# --- every optional field ---
T2 = "Plan the probe follow-up"
T2_BLOCK = ("- [ ] **%s** — `design` · scope `4` · added %s\n"
            "  - Context: probe context for the plan\n"
            "  - Where: tools/probe.py\n"
            "  - Source: session probe\n"
            "  - Plan doc: [[Probe-Plan-Doc]]\n" % (T2, TODAY))
T2_LINKED = ('- [[Probe-Plan-Doc]] — probe context for the plan (Active: "%s", added %s)'
             % (T2, TODAY))
p = stage(tempfile.mkdtemp(prefix="wl_add_opts_"), worklog=W4, mirror=M4)
r = add(p, T2, "design", 4, "Alpha", "probe context for the plan",
        "--where", "tools/probe.py", "--source", "session probe",
        "--plan-doc", "Probe-Plan-Doc")
check("add options: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("add options: block carries Context, Where, Source, Plan doc in order",
      "### Alpha\n" + T2_BLOCK in wl, wl[:900])
check("add options: plan doc joins Linked Docs", T2_LINKED + "\n" in wl)
check("add options: Linked Docs keeps its existing entry",
      "- [[Existing-Doc]] — existing doc (Active: \"Some item\", added 2025-12-01)" in wl)

# --- When: after lands in the Ledger, with the mirror suffix ---
T3 = "Probe the add tool's Ledger route"
T3_BLOCK = ("- [ ] **%s** — `chore` · scope `2` · added %s\n"
            "  - Context: probe context for the ledger\n"
            "  - When: after the probe lands\n" % (T3, TODAY))
p = stage(tempfile.mkdtemp(prefix="wl_add_ledger_"), worklog=W4, mirror=M4)
r = add(p, T3, "chore", 2, "Delta", "probe context for the ledger",
        "--when", "after the probe lands")
check("add ledger: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
ledger = wl.split("## Ledger\n", 1)[1]
check("add ledger: block is the first block of the Ledger domain",
      ledger.startswith("\n### Delta\n" + T3_BLOCK), ledger[:400])
check("add ledger: not in Active", T3 not in wl.split("## Active\n", 1)[1].split("## Ledger", 1)[0])
check("add ledger: When line records the condition", "  - When: after the probe lands\n" in wl)
mi = read(p, "worklog-titles.md")
check("add ledger: mirror suffix", "- chore · 2 · %s [after: the probe lands]" % T3 in mi, mi[-400:])
check("add ledger: ledger count bumped", "## Ledger (2)" in mi and "## Active (2)" in mi)
check("add ledger: Last synced bumped", "Last synced: %s" % TODAY in mi)

# --- Future Scope: existing sub-heading ---
T4 = "Probe the Future Scope route"
FS4 = "> - `chore` · scope `1` · %s (added %s; a probe future item)" % (T4, TODAY)
p = stage(tempfile.mkdtemp(prefix="wl_add_fs_"), worklog=W4, mirror=M4)
r = add(p, T4, "chore", 1, "Alpha", "a probe future item", "--when", "future")
check("add future: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("add future: FSLINE is the first line of its Future Scope sub-heading",
      "> ### Alpha\n" + FS4 + "\n" in wl, wl[-900:])
check("add future: item count bumped", "> [!example]- Future Scope (3 items)" in wl)
check("add future: not in Active or Ledger",
      T4 not in wl.split("## Future Scope")[0])
check("add future: mirror untouched", read(p, "worklog-titles.md") == M4)
check("add future: last_updated bumped", "last_updated: %s" % TODAY in wl)

# --- Future Scope: sub-heading missing, created before the callout close ---
T5 = "Probe a new Future Scope domain"
FS5 = "> - `chore` · scope `1` · %s (added %s; another probe)" % (T5, TODAY)
p = stage(tempfile.mkdtemp(prefix="wl_add_fs_new_"), worklog=W4, mirror=M4)
r = add(p, T5, "chore", 1, "Other", "another probe", "--when", "future")
check("add future new domain: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("add future new domain: heading and line at the callout end",
      ">\n> ### Other\n" + FS5 + "\n\n## Linked Docs" in wl, wl[-700:])
check("add future new domain: count bumped", "> [!example]- Future Scope (3 items)" in wl)
check("add future new domain: mirror untouched", read(p, "worklog-titles.md") == M4)

# --- Active domain with no heading yet ---
T6 = "Probe a new Active domain"
T6_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context\n" % (T6, TODAY))
p = stage(tempfile.mkdtemp(prefix="wl_add_domain_"), worklog=W4, mirror=M4)
r = add(p, T6, "chore", 1, "Other", "probe context")
check("add new domain: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("add new domain: heading ends ## Active, just before ## Ledger",
      "### Other\n" + T6_BLOCK + "\n## Ledger" in wl, wl[:1200])
mi = read(p, "worklog-titles.md")
check("add new domain: mirror category appended at the end of Active",
      "### Other\n- chore · 1 · " + T6 + "\n\n## Ledger" in mi, mi[:900])
check("add new domain: mirror Active count bumped", "## Active (3)" in mi)

# --- a domain is accepted in the table's short form, and the heading keeps the long form ---
T7 = "Probe the short domain spelling"
T7_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context\n" % (T7, TODAY))
p = stage(tempfile.mkdtemp(prefix="wl_add_short_"), worklog=W4, mirror=M4)
r = add(p, T7, "chore", 1, "beta", "probe context")
check("add short domain: rc=0", r.returncode == 0, r.stderr[-400:])
wl = read(p, "Worklog.md")
check("add short domain: long-form heading carries the block",
      "### Beta / Gamma\n" + T7_BLOCK + "\n" in wl, wl[:1200])
check("add short domain: no short-form heading written", "### beta" not in wl)
mi = read(p, "worklog-titles.md")
check("add short domain: mirror category uses the long form",
      "### Beta / Gamma\n- chore · 1 · " + T7 + "\n" in mi, mi[:900])

# --- exact duplicate: case-insensitive, exit 2, nothing written ---
p = stage(tempfile.mkdtemp(prefix="wl_add_dup_"), worklog=W4, mirror=M4)
before = snapshot(p)
line_no = W4.splitlines().index(A_BLOCK.splitlines()[0]) + 1
for title in (A_TITLE, A_TITLE.lower()):
    r = add(p, title, "chore", 1, "Alpha", "probe context")
    check("add duplicate (%r): exit 2" % title[:20], r.returncode == 2, r.returncode)
    check("add duplicate (%r): names the existing item and line" % title[:20],
          "duplicate" in r.stdout and "line %d" % line_no in r.stdout, r.stdout)
check("add duplicate: no write", snapshot(p) == before)

# --- near-duplicate in the same domain: blocked, then allowed by --distinct ---
p = stage(tempfile.mkdtemp(prefix="wl_add_near_"), worklog=W4, mirror=M4)
before = snapshot(p)
r = add(p, A_TITLE_NEAR, "fix", 2, "Alpha", "probe context")
check("add near-duplicate: exit 2", r.returncode == 2, r.returncode)
check("add near-duplicate: says near and offers --distinct",
      "near-duplicate" in r.stdout and "--distinct" in r.stdout, r.stdout)
check("add near-duplicate: no write", snapshot(p) == before)
r = add(p, A_TITLE_NEAR, "fix", 2, "Alpha", "probe context", "--distinct")
check("add near-duplicate --distinct: added", r.returncode == 0 and "added:" in r.stdout, r.stdout)
check("add near-duplicate --distinct: item written", A_TITLE_NEAR in read(p, "Worklog.md"))
p = stage(tempfile.mkdtemp(prefix="wl_add_near_exact_"), worklog=W4, mirror=M4)
r = add(p, A_TITLE, "fix", 2, "Alpha", "probe context", "--distinct")
check("add --distinct never overrides an exact title", r.returncode == 2, r.returncode)

# --- each invalid field: exit 2, names the allowed values, writes nothing ---
def invalid_case(label, title, cls, scope, domain, *extra, needle=""):
    p = stage(tempfile.mkdtemp(prefix="wl_add_bad_"), worklog=W4, mirror=M4)
    before = snapshot(p)
    r = add(p, title, cls, scope, domain, "probe context", *extra)
    check("add invalid %s: exit 2" % label, r.returncode == 2, (r.returncode, r.stderr[-300:]))
    check("add invalid %s: names the allowed values" % label, needle in r.stderr, r.stderr[-400:])
    check("add invalid %s: no write" % label, snapshot(p) == before)


invalid_case("class", "Bad class probe", "bug", 1, "Alpha", needle="chore")
invalid_case("scope", "Bad scope probe", "chore", 5, "Alpha", needle="1, 2, 3 or 4")
invalid_case("domain", "Bad domain probe", "chore", 1, "Nonsense", needle="Alpha")
invalid_case("plan doc", "No plan doc probe", "design", 4, "Alpha", needle="plan-doc")
invalid_case("future plan doc", "Future plan doc probe", "design", 4, "Alpha",
             "--when", "future", "--plan-doc", "Probe-Plan-Doc", needle="Future Scope")

# --- a missing or table-less canonical owner is a refusal that names the file ---
p = stage(tempfile.mkdtemp(prefix="wl_add_noskill_"), worklog=W4, mirror=M4)
before = snapshot(p)
absent = str(p / "absent-skill.md")
r = run_add(p, "Probe a missing owner", "--class", "chore", "--scope", "1", "--domain", "alpha",
            "--context", "probe context", "--date", TODAY, skill=absent)
check("missing canonical owner: exit 2", r.returncode == 2, r.returncode)
check("missing canonical owner: names the file", absent in r.stderr, r.stderr)
check("missing canonical owner: no write", snapshot(p) == before)

p = stage(tempfile.mkdtemp(prefix="wl_add_badskill_"), worklog=W4, mirror=M4)
(p / "SKILL.md").write_text("# Reference\n\n## Classification\n\nNo table here.\n", encoding="utf-8")
before = snapshot(p)
r = add(p, "Probe a table-less owner", "chore", 1, "alpha", "probe context")
check("table-less canonical owner: exit 2", r.returncode == 2, r.returncode)
check("table-less canonical owner: names the file", "SKILL.md" in r.stderr, r.stderr)
check("table-less canonical owner: no write", snapshot(p) == before)

# --- CRLF files stay CRLF ---
p = stage(tempfile.mkdtemp(prefix="wl_add_crlf_"),
          worklog=W4.replace("\n", "\r\n"), mirror=M4.replace("\n", "\r\n"),
          archive=A1.replace("\n", "\r\n"))
r = add(p, T1, "chore", 1, "Alpha", "probe context line")
check("add crlf: rc=0", r.returncode == 0, r.stderr[-400:])
for name in ("Worklog.md", "Worklog-Archive.md", "worklog-titles.md"):
    b = (p / name).read_bytes()
    check("add crlf: %s has no bare LF" % name, b.count(b"\n") == b.count(b"\r\n"))
check("add crlf: block written with CRLF",
      T1_BLOCK.replace("\n", "\r\n").encode() in (p / "Worklog.md").read_bytes())

# --- dry run: writes nothing, prints block, section and mirror line ---
p = stage(tempfile.mkdtemp(prefix="wl_add_dry_"), worklog=W4, mirror=M4)
before = snapshot(p)
r = add(p, T1, "chore", 1, "Alpha", "probe context line", "--dry-run")
check("add dry run: rc=0", r.returncode == 0, r.stderr[-400:])
check("add dry run: no write", snapshot(p) == before)
check("add dry run: prints the BLOCK",
      all(("  " + line) in r.stdout for line in T1_BLOCK.splitlines()), r.stdout)
check("add dry run: prints the section and line",
      "section: ## Active > ### Alpha" in r.stdout, r.stdout)
check("add dry run: prints the mirror line", "mirror line: - chore · 1 · " + T1 in r.stdout, r.stdout)

# --- --from: one good line, one bad line; continues; nonzero exit ---
T12 = "Probe the --from batch item"
p = stage(tempfile.mkdtemp(prefix="wl_add_from_"), worklog=W4, mirror=M4)
(p / "batch.jsonl").write_text(
    json.dumps({"title": T12, "class": "chore", "scope": 1,
                "domain": "Alpha", "context": "batch context"}) + "\n" +
    json.dumps({"title": "Bad batch row", "class": "bug", "scope": 1,
                "domain": "Alpha", "context": "batch context"}) + "\n",
    encoding="utf-8")
r = run_add(p, "--from", str(p / "batch.jsonl"))
check("add --from: nonzero exit when a line is invalid", r.returncode == 2, r.returncode)
check("add --from: added line reported", "added: " + T12 in r.stdout, r.stdout)
check("add --from: invalid line reported", "invalid:" in r.stdout and "bug" in r.stdout, r.stdout)
check("add --from: the good item was written", T12 in read(p, "Worklog.md"))
check("add --from: the bad item wrote nothing", "Bad batch row" not in read(p, "Worklog.md"))

# --- round trip: add then complete returns both files except dates and the archive line ---
T13 = "Probe the round-trip item"
p = stage(tempfile.mkdtemp(prefix="wl_add_round_"))
r = add(p, T13, "chore", 1, "Alpha", "round trip context")
check("round trip: add rc=0", r.returncode == 0, r.stderr[-400:])
r = run(p, T13)
check("round trip: complete rc=0", r.returncode == 0, r.stderr[-400:])
check("round trip: Worklog is its starting bytes but for last_updated",
      read(p, "Worklog.md") == W1.replace("last_updated: " + OLD, "last_updated: " + TODAY),
      read(p, "Worklog.md")[:500])
check("round trip: mirror is its starting bytes but for Last synced",
      read(p, "worklog-titles.md") == M1.replace("Last synced: " + OLD, "Last synced: " + TODAY),
      read(p, "worklog-titles.md")[:500])
check("round trip: archive gained exactly the XLINE",
      read(p, "Worklog-Archive.md") == A1.replace("last_updated: " + OLD, "last_updated: " + TODAY)
      + "- [x] `chore` · scope `1` · %s (completed %s)\n" % (T13, TODAY))

# --- round trip through a domain and mirror category that the add creates ---
T14 = "Probe a new-domain round-trip item"
p = stage(tempfile.mkdtemp(prefix="wl_add_round_new_"), worklog=W4, mirror=M4)
r = add(p, T14, "chore", 1, "Other", "round trip context")
check("round trip new domain: add rc=0", r.returncode == 0, r.stderr[-400:])
check("round trip new domain: heading created", "### Other\n" in read(p, "Worklog.md"))
r = run(p, T14)
check("round trip new domain: complete rc=0", r.returncode == 0, r.stderr[-400:])
check("round trip new domain: Worklog is its starting bytes but for last_updated",
      read(p, "Worklog.md") == W4.replace("last_updated: " + OLD, "last_updated: " + TODAY),
      read(p, "Worklog.md")[:500])
check("round trip new domain: mirror is its starting bytes but for Last synced",
      read(p, "worklog-titles.md") == M4.replace("Last synced: " + OLD, "Last synced: " + TODAY),
      read(p, "worklog-titles.md")[:500])

# --- the vault TODO dir comes from the lock substitutions, and refuses without them ---
import importlib.util
spec = importlib.util.spec_from_file_location("worklog_tool", str(TOOL))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

v = pathlib.Path(tempfile.mkdtemp(prefix="wl_vault_"))
(v / ".claude").mkdir()
(v / ".claude" / "baseline.lock.json").write_text(json.dumps(
    {"substitutions": {tool.TOKEN % "VAULT_ROOT": "/vault",
                       tool.TOKEN % "PROJECT_NAME": "Sample"}}), encoding="utf-8")
check("vault dir: built from the lock's substitutions",
      tool.vault_todo(v) == pathlib.Path("/vault/DevProjects/Sample/Claude/TODO"), tool.vault_todo(v))
v = pathlib.Path(tempfile.mkdtemp(prefix="wl_vault_none_"))
try:
    tool.vault_todo(v)
    raised = ""
except ValueError as exc:
    raised = str(exc)
check("vault dir: a lock without substitutions refuses and names it",
      str(v / ".claude" / "baseline.lock.json") in raised, raised)

# --- the tool's canonical reader agrees with the planted and the real SKILL tables ---
def table_slice(text, start_marker, end_marker):
    body = text.split(start_marker, 1)[1].split(end_marker, 1)[0]
    return [line for line in body.splitlines() if line.startswith("|")]


def is_domain_row(row):
    cell = row.split("|")[1].strip()
    return cell not in ("", "Long form (Active section header)") and set(cell) - set("-: ")


p = pathlib.Path(tempfile.mkdtemp(prefix="wl_canon_"))
(p / "SKILL.md").write_text(SKILL_PLANT, encoding="utf-8")
planted_classes, planted_domains = tool.read_canonical(p / "SKILL.md")
check("planted canonical: the tool reads the planted class rows",
      planted_classes == PLANT_CLASSES, planted_classes)
check("planted canonical: the tool reads each planted long/short pair",
      planted_domains == PLANT_DOMAINS, planted_domains)

skill_md = (REPO / ".claude" / "skills" / "worklog_reference" / "SKILL.md").read_text(encoding="utf-8")
skill_classes = [row.split("|")[1].strip().strip("`")
                 for row in table_slice(skill_md, "## Classification", "## Scope")
                 if row.split("|")[1].strip().startswith("`")]
skill_domains = [row.split("|")[1].strip()
                 for row in table_slice(skill_md, "## Canonical domain list", "## Full Trigger Catalog")
                 if is_domain_row(row)]
skill_shorts = [row.split("|")[2].strip().strip("`")
                for row in table_slice(skill_md, "## Canonical domain list", "## Full Trigger Catalog")
                if is_domain_row(row)]
real_classes, real_domains = tool.read_canonical(REPO / ".claude" / "skills" / "worklog_reference" / "SKILL.md")
check("canonical classes: the tool list matches the skill",
      list(real_classes) == skill_classes, (real_classes, skill_classes))
check("canonical domains: the tool list matches the skill",
      [long for long, _ in real_domains] == skill_domains, (real_domains, skill_domains))
check("canonical domains: every short form matches its row",
      [short for _, short in real_domains] == skill_shorts, (real_domains, skill_shorts))

print("\n%d checks, %d failed" % (ran, len(failures)))
for f in failures:
    print("FAILED:", f)
sys.exit(1 if failures else 0)
