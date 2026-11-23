"""Proof for tools/worklog.py `complete` and `add`.

Fixtures keep the real files' structure — frontmatter, Active/Ledger supersections, domain
headings, blocks with every sub-bullet form, the Future Scope callout, Linked Docs, CRLF — with
neutral text: no game noun, no person, no machine path. The canonical class and domain tables are
planted here, and every `add` run is pointed at the plant, so the proof passes in a project whose
worklog_reference names other domains. `last_updated` / `Last synced` are set to a fixed old date
so a run on any day proves the bump.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import contextlib
import importlib.util
import io
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOL = REPO / ".claude" / "tools" / "worklog.py"
TODAY = datetime.date.today().isoformat()
OLD = "2026-01-01"

def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)


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


def stage(tmp, worklog=W1, archive=A1, mirror=M1):
    p = pathlib.Path(tmp)
    (p / "Worklog.md").write_bytes(worklog.encode("utf-8"))
    if archive is not None:
        (p / "Worklog-Archive.md").write_bytes(archive.encode("utf-8"))
    (p / "worklog-titles.md").write_bytes(mirror.encode("utf-8"))
    (p / "SKILL.md").write_bytes(SKILL_PLANT.encode("utf-8"))
    return p


spec = importlib.util.spec_from_file_location("worklog_tool", str(TOOL))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def _dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _call(argv, session=None):
    """The tool's `main(argv)` in process, with the runtime session id planted; a result shaped like a process's."""
    out, err = io.StringIO(), io.StringIO()
    saved = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    if session:
        os.environ["CLAUDE_CODE_SESSION_ID"] = session
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = tool.main(argv) or 0
            except SystemExit as exc:  # argparse refusals exit 2
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if saved is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = saved
    return types.SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())


def run(p, *args, archive_name="Worklog-Archive.md"):
    return _call(["complete", *args, "--worklog", str(p / "Worklog.md"), "--archive", str(p / archive_name),
                  "--mirror", str(p / "worklog-titles.md")])


def run_add(p, *args, skill=None, session=None):
    skill = str(p / "SKILL.md") if skill is None else skill
    return _call(["add", *args, "--skill", skill, "--worklog", str(p / "Worklog.md"),
                  "--archive", str(p / "Worklog-Archive.md"), "--mirror", str(p / "worklog-titles.md")], session)


def snapshot(p):
    return tuple(read(p, n) for n in ("Worklog.md", "worklog-titles.md", "Worklog-Archive.md"))


def read(p, name):
    return (p / name).read_bytes().decode("utf-8")



def table_slice(text, start_marker, end_marker):
    body = text.split(start_marker, 1)[1].split(end_marker, 1)[0]
    return [line for line in body.splitlines() if line.startswith("|")]


def is_domain_row(row):
    cell = row.split("|")[1].strip()
    return cell not in ("", "Long form (Active section header)") and set(cell) - set("-: ")


SID_A, SID_B = "probe-session-a", "probe-session-b"


def add(p, title, cls, scope, domain, context="probe context", *extra):
    return run_add(p, title, "--class", cls, "--scope", str(scope), "--domain", domain,
                   "--context", context, "--date", TODAY, *extra)

T1 = "Probe the add tool's Active route"

T1_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context line\n" % (T1, TODAY))

T2 = "Plan the probe follow-up"

T2_BLOCK = ("- [ ] **%s** — `design` · scope `4` · added %s\n"
            "  - Context: probe context for the plan\n"
            "  - Where: tools/probe.py\n"
            "  - Source: session probe\n"
            "  - Plan doc: [[Probe-Plan-Doc]]\n" % (T2, TODAY))

T2_LINKED = ('- [[Probe-Plan-Doc]] — probe context for the plan (Active: "%s", added %s)'
             % (T2, TODAY))

T3 = "Probe the add tool's Ledger route"

T3_BLOCK = ("- [ ] **%s** — `chore` · scope `2` · added %s\n"
            "  - Context: probe context for the ledger\n"
            "  - When: after the probe lands\n" % (T3, TODAY))

T4 = "Probe the Future Scope route"

FS4 = "> - `chore` · scope `1` · %s (added %s; a probe future item)" % (T4, TODAY)

T5 = "Probe a new Future Scope domain"

FS5 = "> - `chore` · scope `1` · %s (added %s; another probe)" % (T5, TODAY)

T6 = "Probe a new Active domain"

T6_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context\n" % (T6, TODAY))

T7 = "Probe the short domain spelling"

T7_BLOCK = ("- [ ] **%s** — `chore` · scope `1` · added %s\n"
            "  - Context: probe context\n" % (T7, TODAY))

def invalid_case(tmp_path, label, title, cls, scope, domain, *extra, needle=""):
    p = stage(_dir(tmp_path, "bad_" + "".join(c if c.isalnum() else "_" for c in label)), worklog=W4, mirror=M4)
    before = snapshot(p)
    r = add(p, title, cls, scope, domain, "probe context", *extra)
    check("add invalid %s: exit 2" % label, r.returncode == 2, (r.returncode, r.stderr[-300:]))
    check("add invalid %s: names the allowed values" % label, needle in r.stderr, r.stderr[-400:])
    check("add invalid %s: no write" % label, snapshot(p) == before)

T12 = "Probe the --from batch item"

T13 = "Probe the round-trip item"

T14 = "Probe a new-domain round-trip item"



def test_exact_title_block_gone_xline_last_in_its_domain_both_dates_b(tmp_path):
    p = stage(_dir(tmp_path, "wl_exact_"))
    r = run(p, TARGET, "--ref", "0b820491e")
    assert (r.returncode == 0), "exact title completes (rc=0)"
    wl = read(p, "Worklog.md")
    assert (TARGET_BLOCK not in wl), "exact: block gone"
    assert ("Probe the runner retries" in wl), "exact: the next block survives"
    assert ("last_updated: %s" % TODAY in wl), "exact: worklog last_updated bumped"
    expected_wl = W1.replace(TARGET_BLOCK, "").replace("last_updated: " + OLD, "last_updated: " + TODAY)
    assert (wl == expected_wl), "exact: every other worklog byte unchanged"
    ar = read(p, "Worklog-Archive.md")
    assert (ar == A1.replace(
        "last_updated: " + OLD, "last_updated: " + TODAY) + XLINE + "\n"), "exact: XLINE is the last line of its domain section"
    mi = read(p, "worklog-titles.md")
    assert (MIRROR_LINE + "\n" not in mi), "exact: mirror line gone"
    assert ("Last synced: %s" % TODAY in mi), "exact: mirror Last synced bumped"
    assert ("## Active (1)" in mi), "exact: mirror Active count decremented"
    expected_mi = (M1.replace("Last synced: " + OLD, "Last synced: " + TODAY)
                   .replace(MIRROR_LINE + "\n", "").replace("## Active (2)", "## Active (1)"))
    assert (mi == expected_mi), "exact: every other mirror byte unchanged"



def test_every_optional_sub_bullet_leaves_with_the_block(tmp_path):
    p = stage(_dir(tmp_path, "wl_opts_"), worklog=W3, mirror=M3)
    r = run(p, "Run the probe models through a sibling transport")
    wl = read(p, "Worklog.md")
    assert (r.returncode == 0), "optional sub-bullets: completes (rc=0)"
    assert (not any(s in wl for s in ("Plan doc: [[Probe-Transport]]",
                                    "Quick-win: flagged 2026-10-20",
                                    "When: after the owner runs `probe login`"))), "optional sub-bullets: no line of the block survives"
    assert ("Probe the runner retries" in wl), "optional sub-bullets: next block survives"



def test_last_item_of_a_domain_heading_goes_too_in_worklog_and_the_mi(tmp_path):
    p = stage(_dir(tmp_path, "wl_last_"), worklog=W2, mirror=M2)
    r = run(p, "Apply the board sweep", "--ref", "abc1234")
    wl = read(p, "Worklog.md")
    assert (r.returncode == 0), "last item: completes (rc=0)"
    assert ("### Other" not in wl), "last item: worklog domain heading gone"
    assert ("### Delta" in wl), "last item: other domain heading stays"
    mi = read(p, "worklog-titles.md")
    assert ("### Other" not in mi), "last item: mirror category heading gone"
    assert ("### Delta" in mi), "last item: mirror ledger heading stays"
    assert ("## Active (0)" in mi), "last item: mirror Active count decremented"
    ar = read(p, "Worklog-Archive.md")
    new_x = "- [x] `chore` · scope `2` · Apply the board sweep (completed %s, abc1234)" % TODAY
    assert (ar == A1.replace("last_updated: " + OLD, "last_updated: " + TODAY)
          + "\n## Other\n" + new_x + "\n"), "missing archive domain: new section appended"



def test_archive_does_not_exist_create_it(tmp_path):
    p = stage(_dir(tmp_path, "wl_noarch_"), archive=None)
    r = run(p, TARGET, "--ref", "0b820491e", archive_name="new-archive.md")
    assert (r.returncode == 0), "missing archive: completes (rc=0)"
    ar = read(p, "new-archive.md") if (p / "new-archive.md").exists() else ""
    assert (ar.startswith(
        "---\ntitle: Worklog Archive\nstatus: archive\nlast_updated: %s\n---\n\n# Worklog Archive\n" % TODAY)), "missing archive: frontmatter and heading"
    assert ("> Completed worklog items" in ar), "missing archive: opacity note"
    assert ("## Alpha\n" + XLINE + "\n" in ar), "missing archive: domain section holds the XLINE"
    assert (TARGET_BLOCK not in read(p, "Worklog.md")), "missing archive: worklog block gone"



def test_ambiguous_exit_2_nothing_written(tmp_path):
    p = stage(_dir(tmp_path, "wl_amb_"))
    before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
    r = run(p, "run")
    assert (r.returncode == 2), "ambiguous: exit 2"
    assert ("Probe the runner retries" in r.stdout and "Probe the queue repeat runs" in r.stdout), "ambiguous: candidates listed"
    assert ((read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before), "ambiguous: no write"



def test_no_match_exit_2_nothing_written(tmp_path):
    p = stage(_dir(tmp_path, "wl_none_"))
    before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
    r = run(p, "zzz no such item")
    assert (r.returncode == 2), "no match: exit 2"
    assert ((read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before), "no match: no write"



def test_crlf_files_stay_crlf(tmp_path):
    p = stage(_dir(tmp_path, "wl_crlf_"),
              worklog=W1.replace("\n", "\r\n"), archive=A1.replace("\n", "\r\n"),
              mirror=M1.replace("\n", "\r\n"))
    r = run(p, TARGET, "--ref", "0b820491e")
    assert (r.returncode == 0), "crlf: completes (rc=0)"
    for name in ("Worklog.md", "Worklog-Archive.md", "worklog-titles.md"):
        b = (p / name).read_bytes()
        check("crlf: %s has no bare LF" % name, b.count(b"\n") == b.count(b"\r\n"))
    assert (TARGET_BLOCK.replace("\n", "\r\n").encode() not in (p / "Worklog.md").read_bytes()), "crlf: block gone"
    assert (XLINE.encode() in (p / "Worklog-Archive.md").read_bytes()), "crlf: XLINE appended"



def test_dry_run_writes_nothing_and_prints_the_plan(tmp_path):
    p = stage(_dir(tmp_path, "wl_dry_"))
    before = (read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md"))
    r = run(p, TARGET, "--ref", "0b820491e", "--dry-run")
    assert (r.returncode == 0), "dry run: rc=0"
    assert ((read(p, "Worklog.md"), read(p, "Worklog-Archive.md"), read(p, "worklog-titles.md")) == before), "dry run: no write"
    assert ("lines 12-15" in r.stdout and TARGET in r.stdout), "dry run: prints the block with its range"
    assert (XLINE in r.stdout), "dry run: prints the XLINE"
    assert ("## Alpha" in r.stdout), "dry run: prints the archive section"
    assert (MIRROR_LINE in r.stdout), "dry run: prints the mirror line"



def test_from_one_good_line_one_bad_line(tmp_path):
    p = stage(_dir(tmp_path, "wl_from_"))
    (p / "batch.jsonl").write_text(
        json.dumps({"title": TARGET, "ref": "0b820491e"}) + "\n" +
        json.dumps({"title": "zzz no such item", "ref": "deadbee"}) + "\n", encoding="utf-8")
    r = run(p, "--from", str(p / "batch.jsonl"))
    assert (r.returncode != 0), "--from: nonzero exit when a line misses"
    assert ("completed:" in r.stdout and TARGET in r.stdout), "--from: completed line reported"
    assert ("no-match:" in r.stdout), "--from: missed line reported"
    assert (TARGET_BLOCK not in read(p, "Worklog.md")), "--from: the good item still completed"
    assert ("zzz no such item" not in read(p, "Worklog-Archive.md")), "--from: the bad item wrote nothing"



def test_active_add_into_an_existing_domain_top_of_the_section_both_d(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_active_"), worklog=W4, mirror=M4)
    r = add(p, T1, "chore", 1, "Alpha", "probe context line")
    assert (r.returncode == 0), "add active: rc=0"
    wl = read(p, "Worklog.md")
    expected_wl = (W4.replace("### Alpha\n", "### Alpha\n" + T1_BLOCK + "\n", 1)
                   .replace("last_updated: " + OLD, "last_updated: " + TODAY))
    assert (wl == expected_wl), "add active: block is the first block of its domain"
    assert ("added %s" % TODAY in wl), "add active: item date is the add date"
    assert (read(p, "Worklog-Archive.md") == A1), "add active: archive untouched"
    mi = read(p, "worklog-titles.md")
    expected_mi = (M4.replace("## Active (2)", "## Active (3)", 1)
                   .replace("### Alpha\n", "### Alpha\n- chore · 1 · " + T1 + "\n", 1)
                   .replace("Last synced: " + OLD, "Last synced: " + TODAY))
    assert (mi == expected_mi), "add active: mirror line and count, every other byte unchanged"



def test_every_optional_field(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_opts_"), worklog=W4, mirror=M4)
    r = add(p, T2, "design", 4, "Alpha", "probe context for the plan",
            "--where", "tools/probe.py", "--source", "session probe",
            "--plan-doc", "Probe-Plan-Doc")
    assert (r.returncode == 0), "add options: rc=0"
    wl = read(p, "Worklog.md")
    assert ("### Alpha\n" + T2_BLOCK in wl), "add options: block carries Context, Where, Source, Plan doc in order"
    assert (T2_LINKED + "\n" in wl), "add options: plan doc joins Linked Docs"
    assert ("- [[Existing-Doc]] — existing doc (Active: \"Some item\", added 2025-12-01)" in wl), "add options: Linked Docs keeps its existing entry"



def test_when_after_lands_in_the_ledger_with_the_mirror_suffix(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_ledger_"), worklog=W4, mirror=M4)
    r = add(p, T3, "chore", 2, "Delta", "probe context for the ledger",
            "--when", "after the probe lands")
    assert (r.returncode == 0), "add ledger: rc=0"
    wl = read(p, "Worklog.md")
    ledger = wl.split("## Ledger\n", 1)[1]
    assert ("### Delta" in ledger and T3_BLOCK in ledger), "add ledger: block is the first block of the Ledger domain"
    assert (T3 not in wl.split("## Active\n", 1)[1].split("## Ledger", 1)[0]), "add ledger: not in Active"
    assert ("  - When: after the probe lands\n" in wl), "add ledger: When line records the condition"
    mi = read(p, "worklog-titles.md")
    assert (T3 in mi and "after: the probe lands" in mi), "add ledger: mirror suffix"
    assert ("## Ledger (2)" in mi and "## Active (2)" in mi), "add ledger: ledger count bumped"
    assert ("Last synced: %s" % TODAY in mi), "add ledger: Last synced bumped"



def test_future_scope_existing_sub_heading(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_fs_"), worklog=W4, mirror=M4)
    r = add(p, T4, "chore", 1, "Alpha", "a probe future item", "--when", "future")
    assert (r.returncode == 0), "add future: rc=0"
    wl = read(p, "Worklog.md")
    assert ("> ### Alpha\n" + FS4 + "\n" in wl), "add future: FSLINE is the first line of its Future Scope sub-heading"
    assert ("> [!example]- Future Scope (3 items)" in wl), "add future: item count bumped"
    assert (T4 not in wl.split("## Future Scope")[0]), "add future: not in Active or Ledger"
    assert (read(p, "worklog-titles.md") == M4), "add future: mirror untouched"
    assert ("last_updated: %s" % TODAY in wl), "add future: last_updated bumped"



def test_future_scope_sub_heading_missing_created_before_the_callout_(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_fs_new_"), worklog=W4, mirror=M4)
    r = add(p, T5, "chore", 1, "Other", "another probe", "--when", "future")
    assert (r.returncode == 0), "add future new domain: rc=0"
    wl = read(p, "Worklog.md")
    assert (">\n> ### Other\n" + FS5 + "\n\n## Linked Docs" in wl), "add future new domain: heading and line at the callout end"
    assert ("> [!example]- Future Scope (3 items)" in wl), "add future new domain: count bumped"
    assert (read(p, "worklog-titles.md") == M4), "add future new domain: mirror untouched"



def test_active_domain_with_no_heading_yet(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_domain_"), worklog=W4, mirror=M4)
    r = add(p, T6, "chore", 1, "Other", "probe context")
    assert (r.returncode == 0), "add new domain: rc=0"
    wl = read(p, "Worklog.md")
    assert ("### Other\n" + T6_BLOCK + "\n## Ledger" in wl), "add new domain: heading ends ## Active, just before ## Ledger"
    mi = read(p, "worklog-titles.md")
    assert ("### Other\n- chore · 1 · " + T6 + "\n\n## Ledger" in mi), "add new domain: mirror category appended at the end of Active"
    assert ("## Active (3)" in mi), "add new domain: mirror Active count bumped"



def test_a_domain_is_accepted_in_the_table_s_short_form_and_the_headi(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_short_"), worklog=W4, mirror=M4)
    r = add(p, T7, "chore", 1, "beta", "probe context")
    assert (r.returncode == 0), "add short domain: rc=0"
    wl = read(p, "Worklog.md")
    assert ("### Beta / Gamma\n" + T7_BLOCK + "\n" in wl), "add short domain: long-form heading carries the block"
    assert ("### beta" not in wl), "add short domain: no short-form heading written"
    mi = read(p, "worklog-titles.md")
    assert ("### Beta / Gamma\n- chore · 1 · " + T7 + "\n" in mi), "add short domain: mirror category uses the long form"



def test_exact_duplicate_case_insensitive_exit_2_nothing_written(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_dup_"), worklog=W4, mirror=M4)
    before = snapshot(p)
    line_no = W4.splitlines().index(A_BLOCK.splitlines()[0]) + 1
    for title in (A_TITLE, A_TITLE.lower()):
        r = add(p, title, "chore", 1, "Alpha", "probe context")
        check("add duplicate (%r): exit 2" % title[:20], r.returncode == 2, r.returncode)
        check("add duplicate (%r): names the existing item and line" % title[:20],
              "duplicate" in r.stdout and "line %d" % line_no in r.stdout, r.stdout)
    assert (snapshot(p) == before), "add duplicate: no write"



def test_near_duplicate_in_the_same_domain_blocked_then_allowed_by_di(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_near_"), worklog=W4, mirror=M4)
    before = snapshot(p)
    r = add(p, A_TITLE_NEAR, "fix", 2, "Alpha", "probe context")
    assert (r.returncode == 2), "add near-duplicate: exit 2"
    assert ("near-duplicate" in r.stdout and "--distinct" in r.stdout), "add near-duplicate: says near and offers --distinct"
    assert (snapshot(p) == before), "add near-duplicate: no write"
    r = add(p, A_TITLE_NEAR, "fix", 2, "Alpha", "probe context", "--distinct")
    assert (r.returncode == 0 and "added:" in r.stdout), "add near-duplicate --distinct: added"
    assert (A_TITLE_NEAR in read(p, "Worklog.md")), "add near-duplicate --distinct: item written"
    p = stage(_dir(tmp_path, "wl_add_near_exact_"), worklog=W4, mirror=M4)
    r = add(p, A_TITLE, "fix", 2, "Alpha", "probe context", "--distinct")
    assert (r.returncode == 2), "add --distinct never overrides an exact title"



def test_each_invalid_field_exit_2_names_the_allowed_values_writes_no(tmp_path):
    invalid_case(tmp_path, "class", "Bad class probe", "bug", 1, "Alpha", needle="chore")
    invalid_case(tmp_path, "scope", "Bad scope probe", "chore", 5, "Alpha", needle="1, 2, 3 or 4")
    invalid_case(tmp_path, "domain", "Bad domain probe", "chore", 1, "Nonsense", needle="Alpha")
    invalid_case(tmp_path, "plan doc", "No plan doc probe", "design", 4, "Alpha", needle="plan-doc")
    invalid_case(tmp_path, "future plan doc", "Future plan doc probe", "design", 4, "Alpha",
                 "--when", "future", "--plan-doc", "Probe-Plan-Doc", needle="Future Scope")



def test_a_missing_or_table_less_canonical_owner_is_a_refusal_that_na(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_noskill_"), worklog=W4, mirror=M4)
    before = snapshot(p)
    absent = str(p / "absent-skill.md")
    r = run_add(p, "Probe a missing owner", "--class", "chore", "--scope", "1", "--domain", "alpha",
                "--context", "probe context", "--date", TODAY, skill=absent)
    assert (r.returncode == 2), "missing canonical owner: exit 2"
    assert (absent in r.stderr), "missing canonical owner: names the file"
    assert (snapshot(p) == before), "missing canonical owner: no write"
    p = stage(_dir(tmp_path, "wl_add_badskill_"), worklog=W4, mirror=M4)
    (p / "SKILL.md").write_text("# Reference\n\n## Classification\n\nNo table here.\n", encoding="utf-8")
    before = snapshot(p)
    r = add(p, "Probe a table-less owner", "chore", 1, "alpha", "probe context")
    assert (r.returncode == 2), "table-less canonical owner: exit 2"
    assert ("SKILL.md" in r.stderr), "table-less canonical owner: names the file"
    assert (snapshot(p) == before), "table-less canonical owner: no write"



def test_crlf_files_stay_crlf_2(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_crlf_"),
              worklog=W4.replace("\n", "\r\n"), mirror=M4.replace("\n", "\r\n"),
              archive=A1.replace("\n", "\r\n"))
    r = add(p, T1, "chore", 1, "Alpha", "probe context line")
    assert (r.returncode == 0), "add crlf: rc=0"
    for name in ("Worklog.md", "Worklog-Archive.md", "worklog-titles.md"):
        b = (p / name).read_bytes()
        check("add crlf: %s has no bare LF" % name, b.count(b"\n") == b.count(b"\r\n"))
    assert (T1_BLOCK.replace("\n", "\r\n").encode() in (p / "Worklog.md").read_bytes()), "add crlf: block written with CRLF"



def test_dry_run_writes_nothing_prints_block_section_and_mirror_line(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_dry_"), worklog=W4, mirror=M4)
    before = snapshot(p)
    r = add(p, T1, "chore", 1, "Alpha", "probe context line", "--dry-run")
    assert (r.returncode == 0), "add dry run: rc=0"
    assert (snapshot(p) == before), "add dry run: no write"
    assert (all(("  " + line) in r.stdout for line in T1_BLOCK.splitlines())), "add dry run: prints the BLOCK"
    assert ("section:" in r.stdout and "Active" in r.stdout and "Alpha" in r.stdout), "add dry run: prints the section and line"
    assert ("mirror line:" in r.stdout and T1 in r.stdout), "add dry run: prints the mirror line"



def test_from_one_good_line_one_bad_line_continues_nonzero_exit(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_from_"), worklog=W4, mirror=M4)
    (p / "batch.jsonl").write_text(
        json.dumps({"title": T12, "class": "chore", "scope": 1,
                    "domain": "Alpha", "context": "batch context"}) + "\n" +
        json.dumps({"title": "Bad batch row", "class": "bug", "scope": 1,
                    "domain": "Alpha", "context": "batch context"}) + "\n",
        encoding="utf-8")
    r = run_add(p, "--from", str(p / "batch.jsonl"))
    assert (r.returncode == 2), "add --from: nonzero exit when a line is invalid"
    assert ("added: " + T12 in r.stdout), "add --from: added line reported"
    assert ("invalid:" in r.stdout and "bug" in r.stdout), "add --from: invalid line reported"
    assert (T12 in read(p, "Worklog.md")), "add --from: the good item was written"
    assert ("Bad batch row" not in read(p, "Worklog.md")), "add --from: the bad item wrote nothing"



def test_round_trip_add_then_complete_returns_both_files_except_dates(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_round_"))
    r = add(p, T13, "chore", 1, "Alpha", "round trip context")
    assert (r.returncode == 0), "round trip: add rc=0"
    r = run(p, T13)
    assert (r.returncode == 0), "round trip: complete rc=0"
    assert (read(p, "Worklog.md") == W1.replace("last_updated: " + OLD, "last_updated: " + TODAY)), "round trip: Worklog is its starting bytes but for last_updated"
    assert (read(p, "worklog-titles.md") == M1.replace("Last synced: " + OLD, "Last synced: " + TODAY)), "round trip: mirror is its starting bytes but for Last synced"
    assert (read(p, "Worklog-Archive.md") == A1.replace("last_updated: " + OLD, "last_updated: " + TODAY)
          + "- [x] `chore` · scope `1` · %s (completed %s)\n" % (T13, TODAY)), "round trip: archive gained exactly the XLINE"



def test_round_trip_through_a_domain_and_mirror_category_that_the_add(tmp_path):
    p = stage(_dir(tmp_path, "wl_add_round_new_"), worklog=W4, mirror=M4)
    r = add(p, T14, "chore", 1, "Other", "round trip context")
    assert (r.returncode == 0), "round trip new domain: add rc=0"
    assert ("### Other\n" in read(p, "Worklog.md")), "round trip new domain: heading created"
    r = run(p, T14)
    assert (r.returncode == 0), "round trip new domain: complete rc=0"
    assert (read(p, "Worklog.md") == W4.replace("last_updated: " + OLD, "last_updated: " + TODAY)), "round trip new domain: Worklog is its starting bytes but for last_updated"
    assert (read(p, "worklog-titles.md") == M4.replace("Last synced: " + OLD, "Last synced: " + TODAY)), "round trip new domain: mirror is its starting bytes but for Last synced"



def test_vault_todo_dir_and_project_domains_come_from_adaptation_json(tmp_path):
    v = _dir(tmp_path, "wl_vault_")
    adaptation_json = v / "skills" / "project_subsystems" / "adaptation.json"
    adaptation_json.parent.mkdir(parents=True)
    adaptation_json.write_text(json.dumps({"vault_root": "/vault", "project_name": "Sample",
                                           "worklog_domains": [["Gameplay / Combat", "gameplay"]]}), encoding="utf-8")
    assert (tool.vault_todo(v) == pathlib.Path("/vault/DevProjects/Sample/Claude/TODO")), "vault dir: built from adaptation.json vault_root and project_name"
    assert (tool.project_domains(v) == (("Gameplay / Combat", "gameplay"),)), "project domains: read from adaptation.json worklog_domains as (long, short)"
    v = _dir(tmp_path, "wl_vault_none_")
    try:
        tool.vault_todo(v)
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    assert ("vault_root" in raised and "adaptation.json" in raised), "vault dir: an unset vault_root refuses and names adaptation.json"




def test_add_without_source_records_the_runtime_session_on_the_block_(tmp_path):
    d = str(tmp_path)
    # --- add without --source records the runtime session on the block, after Context ---
    T6 = "Probe the session record"
    p = stage(_dir(tmp_path, "wl_add_sess_"), worklog=W4, mirror=M4)
    r = run_add(p, T6, "--class", "chore", "--scope", "1", "--domain", "Alpha", "--context", "session context",
                "--date", TODAY, session=SID_A)
    assert (r.returncode == 0), "session: add rc=0"
    wl = read(p, "Worklog.md")
    assert (("### Alpha\n- [ ] **%s** — `chore` · scope `1` · added %s\n  - Context: session context\n"
                   "  - Session: %s\n") % (T6, TODAY, SID_A) in wl), "session: block records the runtime session after Context"


def test_a_source_pr_ref_stays_and_the_session_is_recorded_on_its_own(tmp_path):
    d = str(tmp_path)
    # --- a --source PR ref stays, and the session is recorded on its own line ---
    T7 = "Probe a source ref item"
    p = stage(_dir(tmp_path, "wl_add_sess_pr_"), worklog=W4, mirror=M4)
    r = run_add(p, T7, "--class", "chore", "--scope", "1", "--domain", "Alpha", "--context", "ctx",
                "--date", TODAY, "--source", "PR #42", session=SID_A)
    wl = read(p, "Worklog.md")
    assert ("  - Source: PR #42\n  - Session: %s\n" % SID_A in wl), "session: PR ref source stays and the session is recorded too"


def test_a_source_that_already_is_the_runtime_session_is_the_record_n(tmp_path):
    d = str(tmp_path)
    # --- a --source that already is the runtime session is the record: no second line ---
    T8 = "Probe a session source item"
    p = stage(_dir(tmp_path, "wl_add_sess_src_"), worklog=W4, mirror=M4)
    r = run_add(p, T8, "--class", "chore", "--scope", "1", "--domain", "Alpha", "--context", "ctx",
                "--date", TODAY, "--source", SID_A, session=SID_A)
    wl = read(p, "Worklog.md")
    assert ("  - Source: %s\n" % SID_A in wl and "  - Session:" not in wl), "session: source naming the runtime session adds no Session line"


def test_fsline_records_the_session_as_a_trailing_tag_parenthetical_u(tmp_path):
    d = str(tmp_path)
    # --- FSLINE records the session as a trailing tag, parenthetical unchanged ---
    T9 = "Probe a future session item"
    p = stage(_dir(tmp_path, "wl_add_sess_fs_"), worklog=W4, mirror=M4)
    r = run_add(p, T9, "--class", "chore", "--scope", "1", "--domain", "Alpha", "--context", "a probe future item",
                "--date", TODAY, "--when", "future", session=SID_A)
    wl = read(p, "Worklog.md")
    assert ("> - `chore` · scope `1` · %s (added %s; a probe future item) · session %s\n" % (T9, TODAY, SID_A) in wl), "session: FSLINE carries the session tag"


def test_the_close_s_read_exactly_the_two_items_session_a_added_none_(tmp_path):
    d = str(tmp_path)
    # --- the close's read: exactly the two items session A added, none of session B's or the untagged one ---
    p = stage(_dir(tmp_path, "wl_session_read_"), worklog=W4, mirror=M4)
    run_add(p, "Tighten the alpha seam", "--class", "chore", "--scope", "1", "--domain", "Alpha",
            "--context", "active context", "--date", TODAY, session=SID_A)
    run_add(p, "Plan epsilon migration", "--class", "design", "--scope", "2", "--domain", "Alpha",
            "--context", "future context", "--date", TODAY, "--when", "future", session=SID_A)
    run_add(p, "Rename the beta probe", "--class", "chore", "--scope", "1", "--domain", "Alpha",
            "--context", "other context", "--date", TODAY, session=SID_B)
    run_add(p, "Measure gamma latency", "--class", "chore", "--scope", "1", "--domain", "Alpha",
            "--context", "untagged context", "--date", TODAY)
    wl = read(p, "Worklog.md")
    got = tool.session_additions(wl, SID_A)
    assert (got == [("Tighten the alpha seam", "active context"), ("Plan epsilon migration", "future context")]), "session read: lists exactly the two items session A added, in document order"
    assert (tool.session_additions(wl, SID_B) == [("Rename the beta probe", "other context")]), "session read: session B's read lists only its own item"

    p = _dir(tmp_path, "wl_canon_")
    (p / "SKILL.md").write_text(SKILL_PLANT, encoding="utf-8")
    planted_classes, planted_domains = tool.read_canonical(p / "SKILL.md")
    assert (tool.read_canonical(p / "SKILL.md", (("Gameplay / Combat", "gameplay"),))[1]
                  == PLANT_DOMAINS + (("Gameplay / Combat", "gameplay"),)), "planted canonical: project domains follow the table's rows"
    assert (planted_classes == PLANT_CLASSES), "planted canonical: the tool reads the planted class rows"
    assert (planted_domains == PLANT_DOMAINS), "planted canonical: the tool reads each planted long/short pair"

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
    assert (list(real_classes) == skill_classes), "canonical classes: the tool list matches the skill"
    assert ([long for long, _ in real_domains] == skill_domains), "canonical domains: the tool list matches the skill"
    assert ([short for _, short in real_domains] == skill_shorts), "canonical domains: every short form matches its row"



def test_cli_entry_point_runs_as_a_script(tmp_path):
    p = stage(_dir(tmp_path, "cli"))
    r = subprocess.run([sys.executable, str(TOOL), "complete", TARGET, "--ref", "0b820491e",
                        "--worklog", str(p / "Worklog.md"), "--archive", str(p / "Worklog-Archive.md"),
                        "--mirror", str(p / "worklog-titles.md")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and TARGET_BLOCK not in read(p, "Worklog.md"), r.stderr[-300:]
