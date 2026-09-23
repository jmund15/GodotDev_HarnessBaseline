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

    write(os.path.join(base, "settings.json"), json.dumps({"hooks": {}}))

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

    print()
    if FAILURES:
        print("FAILED %d case(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS — rule_retirement.py proof green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
