"""Re-runnable proof for tools/rule_retirement.py.

Builds a fixture `.claude`-shaped tree carrying planted retirement triggers of
every kind, runs the scanner as a subprocess, and asserts on its JSON:

  * an expired `review-by:` memory file is listed as fired;
  * a live one is not;
  * `tool:<name> absent` fires only when the name is missing from the supplied
    tool list;
  * `claude >= X` fires only at or above X;
  * `load_census budget <name> under X` fires from the census JSON and is
    reported undecidable (never silently dropped) when the census is absent;
  * malformed frontmatter and unparseable triggers are reported, not skipped.

    python3 .claude/tests/test_rule_retirement.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANNER = os.path.join(ROOT, "tools", "rule_retirement.py")
LOAD_CENSUS = os.path.join(ROOT, "tools", "load_census.py")

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" — " + detail) if detail else ""))
        FAILURES.append(label)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def produce_census(root, output):
    proc = subprocess.run(
        [sys.executable, LOAD_CENSUS, "--root", root, "--json", output, "--budgets"],
        capture_output=True, text=True)
    if proc.returncode not in (0, 1) or not os.path.isfile(output):
        raise SystemExit("CRASH: load_census.py failed: %s %s" % (proc.returncode, proc.stderr))
    with open(output, encoding="utf-8") as handle:
        return json.load(handle)


def build_tree(base):
    """A miniature .claude tree with one planted rule per trigger kind."""
    mem = os.path.join(base, "auto-memory", "archive")

    write(os.path.join(mem, "expired_review.md"), (
        "---\n"
        "name: expired-review-rule\n"
        "description: planted rule whose review date has passed\n"
        "metadata:\n"
        "  type: feedback\n"
        "retire_when: \"review-by: 2020-01-01\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "live_review.md"), (
        "---\n"
        "name: live-review-rule\n"
        "description: planted rule whose review date is far out\n"
        "metadata:\n"
        "  type: feedback\n"
        "retire_when: \"review-by: 2999-01-01\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "tool_gated.md"), (
        "---\n"
        "name: tool-gated-rule\n"
        "description: planted rule that dies with its tool\n"
        "metadata:\n"
        "  type: feedback\n"
        "retire_when: \"tool:ghost_tool absent\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "census_gated.md"), (
        "---\n"
        "name: census-gated-rule\n"
        "description: planted rule that dies when a load budget falls\n"
        "metadata:\n"
        "  type: feedback\n"
        "retire_when: \"load_census budget cs_rule_bundle_bytes under 1\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "multi_trigger.md"), (
        "---\n"
        "name: multi-trigger-rule\n"
        "description: planted rule with two triggers, one of them expired\n"
        "metadata:\n"
        "  type: feedback\n"
        "retire_when:\n"
        "  - \"tool:Read absent\"\n"
        "  - \"review-by: 2019-06-01\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "malformed_unclosed.md"), (
        "---\n"
        "name: unclosed-frontmatter-rule\n"
        "retire_when: \"review-by: 2020-01-01\"\n"
        "\n"
        "Body with no closing frontmatter fence.\n"
    ))

    write(os.path.join(mem, "malformed_trigger.md"), (
        "---\n"
        "name: unparseable-trigger-rule\n"
        "description: planted rule whose trigger matches no known kind\n"
        "retire_when: \"when it feels stale\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "empty_trigger.md"), (
        "---\n"
        "name: empty-trigger-rule\n"
        "description: planted rule that declares retire_when with no value\n"
        "retire_when:\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    write(os.path.join(mem, "no_trigger.md"), (
        "---\n"
        "name: untriggered-rule\n"
        "description: an ordinary memory file with no retirement trigger\n"
        "---\n\n"
        "Planted rule body.\n"
    ))

    # Rule placed in a command: the inline-comment form.
    write(os.path.join(base, "commands", "planted.md"), (
        "---\n"
        "description: planted fixture command\n"
        "---\n\n"
        "Do the thing the old way.\n"
        "<!-- retire-when: claude >= 3.0.0 -->\n"
    ))

    # A documented example of the comment syntax, in inline code or a fence, is prose, not a trigger.
    write(os.path.join(base, "commands", "documents_the_syntax.md"), (
        "---\n"
        "description: fixture command that documents the comment syntax\n"
        "---\n\n"
        "Record it as a `<!-- retire-when: ... -->` comment below the rule.\n\n"
        "```\n"
        "<!-- retire-when: <trigger> -->\n"
        "```\n"
    ))

    # Scratch is excluded from the scan: a trigger here must not be reported.
    write(os.path.join(base, "scratch", "draft.md"), (
        "<!-- retire-when: review-by: 2020-01-01 -->\n"
    ))
    # A plan is an execution contract, not a rule surface: a marker it quotes is data.
    write(os.path.join(base, "plans", "archive", "quoted_plan.md"), (
        "| P1 | `x.md:5` — “<!-- retire-when: review-by: 2020-01-01 -->” | R |\n"
    ))

    write(os.path.join(base, "settings.json"), json.dumps({"hooks": {}}))
    # The C# bundle is a layer profile's declaration, not a load_census default.
    write(os.path.join(base, "tools", "load_census.fixture.json"),
          json.dumps({"rule_bundles": {"cs": {"glob": "**/*.cs"}}}))

    census_low = os.path.join(base, "logs", "load_census.json")
    low_report = produce_census(base, census_low)
    write(os.path.join(base, "auto-memory", "schema_triggers.md"), (
        "---\n"
        "name: schema-trigger-rule\n"
        "description: proves the reader consumes the producer's report schema\n"
        "retire_when:\n"
        "  - \"load_census budget listing_bytes under 999999999\"\n"
        "  - \"load_census budget standing_bytes under 999999999\"\n"
        "  - \"load_census budget prompt_emission_bytes under 1\"\n"
        "  - \"load_census budget edit_hook_chain_subprocesses under 1\"\n"
        "---\n\n"
        "Planted schema integration rule.\n"
    ))
    write(os.path.join(base, "rules", "cs_bundle.md"), (
        "---\n"
        "paths:\n"
        "  - \"**/*.cs\"\n"
        "---\n"
        "xx\n"
    ))
    census_high = os.path.join(base, "census_high.json")
    high_report = produce_census(base, census_high)
    if not isinstance(low_report.get("prompt_emission_steady_bytes"), (int, float)):
        raise SystemExit("CRASH: --budgets census omitted prompt_emission_steady_bytes")
    if not high_report.get("rules", {}).get("cs_bundle_bytes", 0) > 1:
        raise SystemExit("CRASH: high census fixture did not grow the C# bundle")
    return census_low, census_high


def build_fired_only_tree(base):
    """A well-formed tree with one expired rule and no malformed rows."""
    write(os.path.join(base, "auto-memory", "expired_review.md"), (
        "---\n"
        "name: expired-review-rule\n"
        "description: planted rule whose review date has passed\n"
        "retire_when: \"review-by: 2020-01-01\"\n"
        "---\n\n"
        "Planted rule body.\n"
    ))


def run(base, *args):
    out_json = os.path.join(base, "out-%d.json" % run.counter)
    run.counter += 1
    cmd = [sys.executable, SCANNER, "--root", base, "--json", out_json] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1, 2):
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit("CRASH: rule_retirement.py exited %d" % proc.returncode)
    if not os.path.exists(out_json):
        print(proc.stdout)
        print(proc.stderr)
        raise SystemExit("CRASH: rule_retirement.py wrote no JSON (exit %d)" % proc.returncode)
    with open(out_json, encoding="utf-8") as handle:
        return proc, json.load(handle)


run.counter = 0


def names(rows):
    return sorted(os.path.basename(row["path"]) for row in rows)


def review_due_cases():
    """case 8 — review_due reads only the review scope: CLAUDE.md, rules/** and top-level memory."""
    import importlib.util
    print("case 8 — review_due returns expired review-by rows inside the review scope only")
    spec = importlib.util.spec_from_file_location("rule_retirement_review_probe", SCANNER)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    review_due = getattr(tool, "review_due", None)
    check("review_due exists", callable(review_due))
    if not callable(review_due):
        return
    with tempfile.TemporaryDirectory(prefix="reviewdue_") as base:
        expired = "<!-- retire-when: review-by: 2020-01-01 -->\n"
        write(os.path.join(base, "CLAUDE.md"), "# Guide\n\nBody.\n\n<!-- retire-when: review-by: 2999-01-01 -->\n")
        write(os.path.join(base, "rules", "expired_rule.md"),
              "---\npaths:\n  - \"**/*.cs\"\n---\n\nRule.\n\n" + expired)
        write(os.path.join(base, "rules", "sub", "nested_rule.md"), "Rule.\n\n" + expired)
        write(os.path.join(base, "rules", "tool_rule.md"), "Rule.\n\n<!-- retire-when: tool:Nope absent -->\n")
        write(os.path.join(base, "auto-memory", "expired_hot.md"),
              "---\nname: hot\ndescription: d\nretire_when:\n  - review-by: 2020-01-01\n---\n\nBody.\n")
        write(os.path.join(base, "auto-memory", "MEMORY.md"), "# Index\n\n" + expired)
        write(os.path.join(base, "auto-memory", "archive", "expired_cold.md"),
              "---\nname: cold\ndescription: d\nretire_when:\n  - review-by: 2020-01-01\n---\n\nBody.\n")
        write(os.path.join(base, "commands", "expired_command.md"), "Do it.\n\n" + expired)
        rows = review_due(base, "2026-09-15")
        got = names(rows)
        check("CLAUDE.md, rules/** and top-level memory expired rows only",
              got == ["expired_hot.md", "expired_rule.md", "nested_rule.md"], str(got))
        check("each row carries its path and review-by trigger",
              all(row.get("path") and str(row.get("trigger", "")).startswith("review-by") for row in rows), str(rows))
        check("nothing is due before the dates", review_due(base, "2019-01-01") == [])


def jsonl(path, rows):
    write(path, "".join(json.dumps(row) + "\n" for row in rows))


def marker(when, basis="thin", reverses="the planted observation"):
    return "<!-- basis: %s; reverses-if: %s; review-when: %s -->\n" % (basis, reverses, when)


def evidence_run(base, text, **sources):
    """Scan a one-file tree holding `text`, with each named source pointed at a planted path."""
    root = os.path.join(base, "tree-%d" % evidence_run.counter)
    evidence_run.counter += 1
    write(os.path.join(root, "rules", "planted.md"), "Rule.\n\n" + text)
    flags = []
    for flag, path in sources.items():
        flags += ["--" + flag.replace("_", "-"), path]
    return run(root, "--today", "2026-09-14", *flags)


evidence_run.counter = 0


def bucket_of(data, needle="planted.md"):
    return [name for name in ("fired", "live", "undecidable", "malformed")
            if any(needle in row["path"] for row in data[name])]


def counted_row(data):
    rows = [r for r in data["fired"] + data["live"] if "planted.md" in r["path"]]
    return rows[0] if rows else {}


def evidence_cases():
    print("case 9 — the evidence kind counts source rows since a date and fires at N")
    with tempfile.TemporaryDirectory(prefix="evidence_") as base:
        escapes = os.path.join(base, "escape.jsonl")
        planted = [{"id": "a%d" % i, "ts": "2026-06-0%dT00:00:00Z" % (i + 1), "paths": ["Jmodot", "x/y.cs"],
                    "shouldHaveCaught": "m1", "authorModel": "opus", "tier": "inline",
                    "domain": ["untagged"], "writer": "debugging"} for i in range(3)]
        planted.append({"id": "old", "ts": "2019-01-01T00:00:00Z", "paths": ["Jmodot"], "shouldHaveCaught": "m1",
                        "authorModel": "opus", "tier": "inline", "domain": ["untagged"], "writer": "debugging"})
        planted.append({"id": "other", "ts": "2026-06-09T00:00:00Z", "paths": ["z.cs"], "shouldHaveCaught": "m2",
                        "authorModel": "sonnet", "tier": "executor", "domain": ["net"], "writer": "self_evaluate"})
        jsonl(escapes, planted)
        _proc, data = evidence_run(base, marker("escape path:Jmodot since:2026-01-01 >= 3"), escape_log=escapes)
        check("count N fires", bucket_of(data) == ["fired"], str(bucket_of(data)))
        row = counted_row(data)
        check("the fired row carries its kind, count and matched ids",
              row.get("kind") == "evidence" and row.get("count") == 3
              and sorted(row.get("ids", [])) == ["a0", "a1", "a2"], str(row))
        _proc, data = evidence_run(base, marker("escape path:Jmodot since:2026-01-01 >= 4"), escape_log=escapes)
        check("count N-1 is live", bucket_of(data) == ["live"], str(bucket_of(data)))
        _proc, data = evidence_run(base, marker("escape path:Jmodot since:2026-06-02 >= 3"), escape_log=escapes)
        check("rows before since: do not count", bucket_of(data) == ["live"], str(bucket_of(data)))
        for when, expect in (("mandate:m1", 3), ("model:sonnet", 1), ("tier:executor", 1), ("domain:net", 1),
                             ("writer:self_evaluate", 1), ("path:x/*.cs", 3), ("mandate:m1 model:sonnet", 0),
                             ("tier:!inline", 1), ("tier:!executor mandate:m1", 3), ("domain:!untagged", 1)):
            _proc, data = evidence_run(base, marker("escape %s since:2026-01-01 >= 1" % when), escape_log=escapes)
            check("escape filter %s counts %d" % (when, expect),
                  counted_row(data).get("count") == expect, str(counted_row(data)))
        _proc, data = evidence_run(base, marker("escape since:2026-01-01 >= 1"),
                                   escape_log=os.path.join(base, "nope.jsonl"))
        row = counted_row(data)
        check("an absent source counts zero and is live, recording source absent",
              bucket_of(data) == ["live"] and row.get("count") == 0 and row.get("evidence_source") == "absent",
              str(data["live"]))
        check("the result reconciles each loaded source",
              data.get("evidence_sources", {}).get("escape", {}).get("status") == "absent",
              str(data.get("evidence_sources")))
        unreadable = os.path.join(base, "dir.jsonl")
        os.makedirs(unreadable)
        _proc, data = evidence_run(base, marker("escape since:2026-01-01 >= 1"), escape_log=unreadable)
        check("a source that exists and cannot be read is undecidable", bucket_of(data) == ["undecidable"],
              str(bucket_of(data)))

        partial = os.path.join(base, "partial.jsonl")
        jsonl(partial, [{"id": "p0", "ts": "2026-06-05T00:00:00Z", "paths": ["Jmodot/a.cs"], "shouldHaveCaught": "m1",
                         "authorModel": "opus", "tier": "inline", "domain": ["x"], "writer": "debugging"}])
        with open(partial, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("{not json\n")
        for flag, source in (("escape_log", "escape"), ("ledger", "ledger")):
            _proc, data = evidence_run(base, marker("%s since:2026-01-01 >= 1" % source), **{flag: partial})
            check("a %s source with a corrupt line is undecidable, not present" % source,
                  bucket_of(data) == ["undecidable"], str(bucket_of(data)))
        task_dir = os.path.join(base, "task_records")
        write(os.path.join(task_dir, "good.json"), json.dumps({"task_id": "t1", "opened": "2026-06-01"}))
        write(os.path.join(task_dir, "bad.json"), "{not json")
        _proc, data = evidence_run(base, marker("tasks since:2026-01-01 >= 1"), tasks_dir=task_dir)
        check("a task directory holding an unreadable record is undecidable, not present",
              bucket_of(data) == ["undecidable"], str(bucket_of(data)))

        ledger = os.path.join(base, "ledger.jsonl")
        jsonl(ledger, [
            {"id": "r1:s:m1", "ts": "2026-05-01T00:00:00Z", "stage": "candidate", "subject": "session:x",
             "mandate": "m1", "model": "opus", "seat": "s", "outcome": "findings"},
            {"id": "r1:s:m2", "ts": "2026-05-01T00:00:00Z", "stage": "candidate", "subject": "session:x",
             "mandate": "m2", "model": "opus", "seat": "s", "outcome": "findings"},
            {"id": "r2:s:m1", "ts": "2026-05-02T00:00:00Z", "stage": "pre-merge", "subject": "pr:3",
             "mandate": "m1", "model": "sonnet", "seat": "t", "outcome": "clean"}])
        _proc, data = evidence_run(base, marker("ledger since:2026-01-01 >= 2"), ledger=ledger)
        check("a ledger review counts once however many rows one record call wrote",
              bucket_of(data) == ["fired"] and counted_row(data).get("count") == 2, str(data))
        for when, expect in (("stage:pre-merge", 1), ("mandate:m1", 2), ("model:opus", 1), ("seat:t", 1),
                             ("outcome:clean", 1)):
            _proc, data = evidence_run(base, marker("ledger %s since:2026-01-01 >= 1" % when), ledger=ledger)
            check("ledger filter %s counts %d" % (when, expect),
                  counted_row(data).get("count") == expect, str(counted_row(data)))

        archive = os.path.join(base, "archive.jsonl")
        jsonl(archive, [
            {"run": "w1", "label": "review:a", "run_date": "2026-06-01T00:00:00Z", "author_model": "opus",
             "domain": ["net"], "mandates": {"m1": {"unique": 1}}, "seat_overlap": {"key": "sa"}},
            {"run": "w2", "label": "review:b", "run_date": "2026-06-02T00:00:00Z", "author_model": "sonnet",
             "domain": ["untagged"], "mandates": {"m2": {"unique": 0}}, "seat_overlap": {"key": "sb"}},
            {"run": "w3", "label": "review:a", "run_date": "2019-06-02T00:00:00Z", "author_model": "opus",
             "domain": ["net"], "mandates": {"m1": {"unique": 1}}, "seat_overlap": {"key": "sa"}}])
        for filt, expect in ((None, 2), ("mandate:m1", 1), ("seat:sb", 1), ("model:opus", 1), ("domain:net", 1)):
            when = "yield " + (filt + " " if filt else "") + "since:2026-01-01 >= 1"
            _proc, data = evidence_run(base, marker(when), yield_archive=archive)
            check("yield filter %s counts %d" % (filt, expect),
                  counted_row(data).get("count") == expect, str(counted_row(data)))

        tasks = os.path.join(base, "tasks")
        write(os.path.join(tasks, "a.json"), json.dumps({"task_id": "a", "opened": "2026-07-01T00:00:00Z",
              "triage": {"fields": {"reach": "shared", "oracle": ["gate", "new-test"], "size": "milestones"}}}))
        write(os.path.join(tasks, "b.json"), json.dumps({"task_id": "b", "opened": "2026-07-02T00:00:00Z",
              "triage": {"fields": {"reach": "local", "oracle": ["gate"], "size": "one-context"}}}))
        write(os.path.join(tasks, "c.json"), json.dumps({"task_id": "c", "opened": "2019-07-02T00:00:00Z",
              "triage": {"fields": {"reach": "shared", "oracle": ["gate"], "size": "one-context"}}}))
        write(os.path.join(tasks, "a.archive.jsonl"), "{}\n")
        for filt, expect in ((None, 2), ("reach:shared", 1), ("oracle:new-test", 1), ("size:one-context", 1)):
            when = "tasks " + (filt + " " if filt else "") + "since:2026-01-01 >= 1"
            _proc, data = evidence_run(base, marker(when), tasks_dir=tasks)
            check("tasks filter %s counts %d" % (filt, expect),
                  counted_row(data).get("count") == expect, str(counted_row(data)))

    print("case 10 — each malformed evidence shape is reported, never parsed")
    with tempfile.TemporaryDirectory(prefix="evidence_bad_") as base:
        bad = {
            "no since": marker("escape path:x >= 1"),
            "bad basis": marker("escape since:2026-01-01 >= 1", basis="guess"),
            "unknown source": marker("nowhere since:2026-01-01 >= 1"),
            "unknown field": marker("escape colour:red since:2026-01-01 >= 1"),
            "field not valid for the source": marker("ledger writer:x since:2026-01-01 >= 1"),
            "empty negation escape": marker("escape tier:! since:2026-01-01 >= 1"),
            "empty negation path": marker("escape path:! since:2026-01-01 >= 1"),
            "empty negation ledger": marker("ledger stage:! since:2026-01-01 >= 1"),
            "empty negation yield": marker("yield domain:! since:2026-01-01 >= 1"),
            "empty negation tasks": marker("tasks oracle:! since:2026-01-01 >= 1"),
            "N of 0": marker("escape since:2026-01-01 >= 0"),
            "N not a number": marker("escape since:2026-01-01 >= many"),
            "since not a real date": marker("escape since:2026-13-45 >= 1"),
            "missing reverses-if": "<!-- basis: thin; review-when: escape since:2026-01-01 >= 1 -->\n",
            "review-when inside reverses-if prose": marker(
                "escape since:2026-01-01 >= 1", reverses="x; review-when: escape since:2020-01-01 >= 1"),
            "comment terminator inside reverses-if prose": marker(
                "escape since:2026-01-01 >= 1", reverses="x --> y"),
        }
        for label, text in bad.items():
            _proc, data = evidence_run(base, text, escape_log=os.path.join(base, "none.jsonl"))
            got = bucket_of(data)
            check("malformed: " + label, got == ["malformed"], str(got))
        fenced = "```\n" + marker("escape path:x >= 1") + "```\nSee `" + marker("nowhere >= 1").strip() + "`.\n"
        _proc, data = evidence_run(base, fenced)
        check("a marker in a fenced block or inline code is not read", bucket_of(data) == [], str(bucket_of(data)))

    print("case 11 — evidence_due reads tracked markers in any .md file and skips SKIP_DIRS")
    import importlib.util
    spec = importlib.util.spec_from_file_location("rule_retirement_evidence_probe", SCANNER)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    due = getattr(tool, "evidence_due", None)
    check("evidence_due exists", callable(due))
    if not callable(due):
        return
    with tempfile.TemporaryDirectory(prefix="evidence_due_") as base:
        claude = os.path.join(base, ".claude")
        escapes = os.path.join(base, "escape.jsonl")
        jsonl(escapes, [{"id": "e1", "ts": "2026-06-01T00:00:00Z", "paths": ["Jmodot"], "shouldHaveCaught": "m1"}])
        write(os.path.join(claude, "commands", "fires.md"), "Step.\n\n" + marker("escape since:2026-01-01 >= 1"))
        write(os.path.join(claude, "commands", "live.md"), "Step.\n\n" + marker("escape since:2026-01-01 >= 2"))
        write(os.path.join(claude, "commands", "bad.md"), "Step.\n\n" + marker("escape >= 1"))
        write(os.path.join(claude, "commands", "fenced.md"), "```\n" + marker("escape since:2026-01-01 >= 1") + "```\n")
        write(os.path.join(claude, "scratch", "skipped.md"), marker("escape since:2026-01-01 >= 1"))
        write(os.path.join(claude, "commands", "plain.md"), "No marker.\n")
        subprocess.run(["git", "init", "-q", base], check=True)
        subprocess.run(["git", "-C", base, "add", "-A", "-f"], check=True)
        write(os.path.join(claude, "commands", "untracked.md"), marker("escape since:2026-01-01 >= 1"))
        saved = os.environ.get("HARNESS_ESCAPE_LOG")
        os.environ["HARNESS_ESCAPE_LOG"] = escapes
        try:
            rows = due(claude)
        finally:
            if saved is None:
                del os.environ["HARNESS_ESCAPE_LOG"]
            else:
                os.environ["HARNESS_ESCAPE_LOG"] = saved
        got = sorted((os.path.basename(r["path"]), r["bucket"]) for r in rows)
        check("fired and malformed tracked markers only",
              got == [("bad.md", "malformed"), ("fires.md", "fired")], str(got))


def model_cases():
    print("case 13 — model compensation flags track the registry's driver alias and version")
    import copy
    import importlib.util
    from pathlib import Path
    with open(os.path.join(ROOT, "reference", "external_models.json"), encoding="utf-8") as handle:
        registry = json.load(handle)
    driver = next(row for row in registry["models"] if row.get("driverNotes"))
    role, version = driver["alias"], driver["version"]
    note = driver["driverNotes"][0]
    spec = {"role": role, "model": version, "weakness": note["weakness"], "removeWhen": note["removeWhen"]}

    def trigger(value):
        return "model-rule: " + json.dumps(value)

    with tempfile.TemporaryDirectory(prefix="model_rules_") as base:
        root = os.path.join(base, ".claude")
        registry_path = os.path.join(root, "reference", "external_models.json")
        rule_path = os.path.join(root, "rules", "planted.md")

        def fixture(value=spec, data=registry):
            write(registry_path, json.dumps(data))
            write(rule_path, "Rule.\n<!-- retire-when: " + trigger(value) + " -->\n")

        fixture()
        proc, data = run(root)
        check("unchanged registry version keeps a model rule live",
              proc.returncode == 0 and bucket_of(data) == ["live"], str(data))
        row = counted_row(data)
        check("the row carries the frozen model, role, weakness and removeWhen",
              all(row.get(key) == value for key, value in spec.items()), str(row))

        revised = copy.deepcopy(registry)
        revised_driver = next(row for row in revised["models"] if row["alias"] == role)
        revised_driver["version"] = version + "-revised"
        fixture(data=revised)
        proc, data = run(root)
        check("same vendor id with a new version fires a review flag",
              proc.returncode == 1 and bucket_of(data) == ["fired"], str(data))
        row = counted_row(data)
        check("the review flag identifies the current driver and revision",
              row.get("kind") == "model-change" and row.get("current_model") == driver["id"]
              and row.get("current_version") == version + "-revised", str(row))

        replaced = copy.deepcopy(registry)
        old = next(row for row in replaced["models"] if row["alias"] == role)
        replacement = next(row for row in replaced["models"] if row["version"] != version)
        old["alias"], replacement["alias"] = replacement["alias"], old["alias"]
        for row in (old, replacement):
            history = replaced.get("aliasHistory", {}).get(row["alias"])
            if history:
                history[-1].update(until="2099-01-01", evidence="planted alias remapping")
                history.append({"id": row["id"]})
        fixture(data=replaced)
        proc, data = run(root)
        check("alias remapping to a different model fires",
              proc.returncode == 1 and counted_row(data).get("current_model") == replacement["id"], str(data))

        fixture()
        other = dict(spec, model=version + "-old", weakness="A distinct failure")
        write(rule_path, "First rule.\n<!-- retire-when: " + trigger(spec)
              + " -->\nSecond rule.\n<!-- retire-when: " + trigger(other) + " -->\n")
        _, data = run(root)
        check("each model rule in one file has its own result",
              len(data["live"]) == 1 and len(data["fired"]) == 1
              and data["fired"][0].get("weakness") == "A distinct failure", str(data))

        fixture(other)
        os.remove(rule_path)
        memory = os.path.join(root, "auto-memory", "archive", "planted.md")
        memory_spec = dict(other, weakness="Skips the owner's check", removeWhen="Both arms run `the check`")
        write(memory, "---\nname: model-rule\nretire_when: '" + trigger(memory_spec).replace("'", "\\u0027")
              + "'\n---\nRule.\n")
        _, data = run(root)
        check("memory frontmatter uses the same model grammar", bucket_of(data) == ["fired"], str(data))
        check("quoted memory JSON preserves apostrophes and backticks",
              counted_row(data).get("weakness") == memory_spec["weakness"]
              and counted_row(data).get("removeWhen") == memory_spec["removeWhen"], str(data))
        os.remove(memory)

        for key in spec:
            bad = dict(spec)
            del bad[key]
            fixture(bad)
            proc, data = run(root)
            check("missing model marker field %s is malformed" % key,
                  proc.returncode == 2 and bucket_of(data) == ["malformed"], str(data))
        for label, bad in (("empty weakness", dict(spec, weakness=" ")),
                           ("non-string removal", dict(spec, removeWhen=[])),
                           ("extra field", dict(spec, obsolete=True)),
                           ("not an object", [spec])):
            fixture(bad)
            proc, data = run(root)
            check(label + " is malformed", proc.returncode == 2 and bucket_of(data) == ["malformed"], str(data))
        fixture()
        write(rule_path, "Rule.\n<!-- retire-when: model-rule: {broken -->\n")
        proc, data = run(root)
        check("broken model JSON is malformed", proc.returncode == 2 and bucket_of(data) == ["malformed"], str(data))

        for label, value in (("unknown driver alias", "not-a-driver"), ("vendor id is not a role", driver["id"])):
            fixture(dict(spec, role=value))
            _, data = run(root)
            check(label + " is undecidable", bucket_of(data) == ["undecidable"], str(data))
        fixture()
        os.remove(registry_path)
        _, data = run(root)
        check("absent registry is undecidable, never a deletion flag", bucket_of(data) == ["undecidable"], str(data))
        for label, text in (("corrupt", "{broken"), ("invalid", '{"models": []}')):
            write(registry_path, text)
            _, data = run(root)
            check(label + " registry is undecidable", bucket_of(data) == ["undecidable"], str(data))

        coded = dict(spec, weakness="Ignores `the step`", removeWhen="Both arms run `the step`")
        fixture(coded)
        _, data = run(root)
        check("backticks inside a live marker preserve its machine-readable facts",
              counted_row(data).get("weakness") == coded["weakness"]
              and counted_row(data).get("removeWhen") == coded["removeWhen"], str(data))

        fixture(other)
        example = "<!-- retire-when: " + trigger(other) + " -->"
        write(rule_path, "`" + example + "`\n```\n" + example + "\n```\n")
        write(os.path.join(root, "scratch", "ignored.md"), example)
        write(os.path.join(root, "plans", "ignored.md"), example)
        _, data = run(root)
        check("model examples and excluded directories produce no rows",
              all(not data[key] for key in ("fired", "live", "undecidable", "malformed")), str(data))

        fixture(other)
        override = os.path.join(base, "override.json")
        write(override, json.dumps(revised))
        command = [sys.executable, SCANNER, "--root", root, "--registry", override,
                   "--json", os.path.join(base, "override-out.json")]
        proc = subprocess.run(command, capture_output=True, text=True)
        check("explicit registry input is supported without a crash", proc.returncode == 1, proc.stderr)
        if os.path.isfile(command[-1]):
            override_data = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            check("explicit registry overrides the root's registry",
                  counted_row(override_data).get("current_version") == version + "-revised", str(override_data))

        module_spec = importlib.util.spec_from_file_location("model_retirement_probe", SCANNER)
        tool = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(tool)
        bucket, kind, _ = tool.evaluate(trigger(spec), {})
        check("the edit advisory's evidence-free evaluate call stays undecidable",
              (bucket, kind) == ("undecidable", "model-change"), str((bucket, kind)))


def doc_cases():
    print("case 12 — /codify (body and landing reference) lists every trigger kind the docstring names; /autolearn names evidence")
    with open(SCANNER, encoding="utf-8") as handle:
        doc = handle.read().split('"""')[1]
    codify = ""
    for rel in (("codify.md",), ("reference", "codify_landing.md")):
        with open(os.path.join(ROOT, "commands", *rel), encoding="utf-8") as handle:
            codify += handle.read() + "\n"
    with open(os.path.join(ROOT, "commands", "autolearn.md"), encoding="utf-8") as handle:
        autolearn = handle.read()
    check("the docstring lists the evidence kind", "basis: thin|reasoned" in doc)
    for token in ("retire-when", "review-by", "basis:"):
        check("codify.md names %s" % token, token in codify)
    check("codify.md names the grammar's home", "rule_retirement.py" in codify and "reverses-if" in codify)
    # The scope is stated without the tool's constant names, which the tool owns (instruction_quality §17).
    check("codify.md states evidence-marker due scanning covers supported .claude/ Markdown",
          "Evidence-marker due scanning covers supported `.claude/` Markdown." in codify)
    check("autolearn.md names the evidence rows and their three dispositions",
          "`evidence`" in autolearn and "confirm" in autolearn and "re-date `since:`" in autolearn)
    with open(os.path.join(ROOT, "commands", "reference", "codify_text_probe.md"), encoding="utf-8") as handle:
        probe = handle.read()
    check("codify requires the model marker for a model compensation",
          "model-rule:" in codify and "weakness" in codify and "removeWhen" in codify)
    check("the scanner docstring owns the model marker grammar", "model-rule:" in doc and "--registry" in doc)
    for token in ("## Rule-removal ablation", "flagged", "thin-basis", "retained", "removed",
                  "current registry", "independent", "both arms succeed", "delete", "not require"):
        check("ablation recipe states %s" % token, token in probe)


def main():
    if not os.path.exists(SCANNER):
        raise SystemExit("RED: %s does not exist" % SCANNER)

    with tempfile.TemporaryDirectory() as base:
        census_low, census_high = build_tree(base)

        with tempfile.TemporaryDirectory(prefix="rulefiredonly_") as fired_base:
            build_fired_only_tree(fired_base)
            fired_proc, fired_only = run(
                fired_base, "--today", "2026-09-14", "--tools", "Read")
            check("exit code 1 when only a well-formed rule fired",
                  fired_proc.returncode == 1
                  and len(fired_only["fired"]) == 1
                  and not fired_only["malformed"],
                  "exit %d" % fired_proc.returncode)

        print("case 0 — a generated .cache mirror is not a live rule owner")
        with tempfile.TemporaryDirectory(prefix="rulecache_") as cache_base:
            planted = "Rule.\n\n<!-- retire-when: review-by: 2020-01-01 -->\n"
            write(os.path.join(cache_base, ".cache", "mirror", "rule.md"), planted)
            write(os.path.join(cache_base, "rules", "rule.md"), planted)
            _proc, cache_data = run(cache_base, "--today", "2026-09-14")
            paths = [row["path"] for bucket in ("fired", "live", "undecidable", "malformed")
                     for row in cache_data[bucket]]
            check("positive control: rules/rule.md outside .cache is scanned",
                  any(p.endswith("rules/rule.md") for p in paths), str(paths))
            check("the .cache mirror yields no row", not any(".cache" in p for p in paths), str(paths))

        print("case 1 — expired review-by fires, live one does not")
        proc, data = run(base, "--today", "2026-09-14", "--tools", "Read,Edit,Grep")
        fired = names(data["fired"])
        live = names(data["live"])
        check("expired_review.md is fired", "expired_review.md" in fired, str(fired))
        check("live_review.md is not fired", "live_review.md" not in fired, str(fired))
        check("live_review.md is reported live", "live_review.md" in live, str(live))
        check("no_trigger.md is neither fired nor live",
              "no_trigger.md" not in fired and "no_trigger.md" not in live)
        check("exit code 2 when fired and malformed rows exist", proc.returncode == 2,
              "exit %d" % proc.returncode)

        print("case 2 — tool:<name> absent fires only when the tool is absent")
        check("ghost_tool absent from the list fires", "tool_gated.md" in fired, str(fired))
        _, data_present = run(base, "--today", "2026-09-14", "--tools", "Read,Edit,ghost_tool")
        check("ghost_tool present in the list does not fire",
              "tool_gated.md" not in names(data_present["fired"]), str(names(data_present["fired"])))
        check("a present tool leaves the rule live",
              "tool_gated.md" in names(data_present["live"]))
        _, data_notools = run(base, "--today", "2026-09-14")
        check("no tool list makes the trigger undecidable, not fired",
              "tool_gated.md" in names(data_notools["undecidable"])
              and "tool_gated.md" not in names(data_notools["fired"]),
              str(names(data_notools["undecidable"])))
        missing_tools_proc, missing_tools = run(
            base, "--today", "2026-09-14", "--tools", "@" + os.path.join(base, "missing-tools.txt"))
        check("missing explicit @tools evidence exits 2",
              missing_tools_proc.returncode == 2
              and any(row.get("source") == "input" and "tools" in row.get("problem", "")
                      for row in missing_tools["malformed"]),
              str(missing_tools))
        missing_census_proc, missing_census = run(
            base, "--today", "2026-09-14", "--census", os.path.join(base, "missing-census.json"))
        check("missing explicit census evidence exits 2",
              missing_census_proc.returncode == 2
              and any(row.get("source") == "input" and "census" in row.get("problem", "")
                      for row in missing_census["malformed"]),
              str(missing_census))

        print("case 3 — malformed frontmatter is reported, not skipped")
        malformed = names(data["malformed"])
        check("unclosed frontmatter reported", "malformed_unclosed.md" in malformed, str(malformed))
        check("unparseable trigger reported", "malformed_trigger.md" in malformed, str(malformed))
        check("empty retire_when reported", "empty_trigger.md" in malformed, str(malformed))
        documented = "documents_the_syntax.md"
        check("a documented example of the comment syntax is not a trigger",
              all(documented not in names(data[bucket])
                  for bucket in ("malformed", "fired", "live", "undecidable")),
              str({b: names(data[b]) for b in ("malformed", "fired", "live", "undecidable")}))
        check("every malformed row names the problem",
              all(row.get("problem") for row in data["malformed"]))
        check("exit code 2 when malformed rows exist", proc.returncode == 2,
              "exit %d" % proc.returncode)

        print("case 4 — claude >= X compares the supplied client version")
        _, old_client = run(base, "--today", "2026-09-14", "--client-version", "2.1.0")
        check("older client does not fire the version trigger",
              "planted.md" not in names(old_client["fired"]), str(names(old_client["fired"])))
        _, new_client = run(base, "--today", "2026-09-14", "--client-version", "3.4.1")
        check("client at or above the threshold fires",
              "planted.md" in names(new_client["fired"]), str(names(new_client["fired"])))
        check("the inline-comment trigger records its source form",
              any(row["source"] == "comment" for row in new_client["fired"]
                  if os.path.basename(row["path"]) == "planted.md"))

        bad_date_proc, bad_date = run(base, "--today", "2026-99-99")
        check("invalid --today exits 2 and is reported as malformed",
              bad_date_proc.returncode == 2
              and any(row.get("source") == "input" and "today" in row.get("problem", "")
                      for row in bad_date["malformed"]),
              str(bad_date))
        bad_version_proc, bad_version = run(base, "--client-version", "nope")
        check("invalid --client-version exits 2 and is reported as malformed",
              bad_version_proc.returncode == 2
              and any(row.get("source") == "input" and "client-version" in row.get("problem", "")
                      for row in bad_version["malformed"]),
              str(bad_version))

        print("case 5 — load_census budget <name> under X")
        _, low = run(base, "--today", "2026-09-14", "--census", census_low)
        check("budget under the threshold fires",
              "census_gated.md" in names(low["fired"]), str(names(low["fired"])))
        schema_rows = [row for row in low["fired"]
                       if os.path.basename(row["path"]) == "schema_triggers.md"]
        schema_keys = {row["trigger"].split()[2] for row in schema_rows}
        check("reader consumes all canonical load_census budget fields",
              {"listing_bytes", "standing_bytes", "prompt_emission_bytes",
               "edit_hook_chain_subprocesses"} <= schema_keys,
              str(schema_keys))
        with open(census_low, encoding="utf-8") as handle:
            runtime_text = handle.read()
        os.remove(census_low)
        _, absent_default = run(base, "--today", "2026-09-14")
        check("absent default census makes the trigger undecidable",
              "census_gated.md" in names(absent_default["undecidable"]),
              str(names(absent_default["undecidable"])))
        with open(census_low, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(runtime_text)
        _, default_census = run(base, "--today", "2026-09-14")
        check("default census reads the generated runtime report",
              "census_gated.md" in names(default_census["fired"]),
              str(names(default_census["fired"])))
        _, high = run(base, "--today", "2026-09-14", "--census", census_high)
        check("budget above the threshold does not fire",
              "census_gated.md" not in names(high["fired"]), str(names(high["fired"])))
        check("census absent makes the trigger undecidable",
              "census_gated.md" in names(absent_default["undecidable"]), str(names(absent_default["undecidable"])))

        print("case 6 — multi-trigger and scan scope")
        check("a file fires when any one of its triggers fires",
              "multi_trigger.md" in fired, str(fired))
        check("scratch/ is outside the scan",
              "draft.md" not in fired + live + malformed + names(data["undecidable"]))
        check("plans/ is outside the scan",
              "quoted_plan.md" not in fired + live + malformed + names(data["undecidable"]), str(fired))
        check("every fired row carries its trigger text and reason",
              all(row.get("trigger") and row.get("reason") for row in data["fired"]))

        print("case 7 — clean tree exits 0")
        clean = os.path.join(base, "clean")
        write(os.path.join(clean, "auto-memory", "archive", "ok.md"), (
            "---\nname: ok\ndescription: no trigger\n---\n\nBody.\n"
        ))
        proc_clean, clean_data = run(clean, "--today", "2026-09-14")
        check("clean tree exits 0", proc_clean.returncode == 0, "exit %d" % proc_clean.returncode)
        check("clean tree reports no fired rows", clean_data["fired"] == [])
        check("clean tree still reports the scanned count",
              isinstance(clean_data.get("scanned"), int))

    review_due_cases()
    evidence_cases()
    model_cases()
    doc_cases()

    print()
    if FAILURES:
        print("FAILED %d case(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS — rule_retirement.py proof green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
