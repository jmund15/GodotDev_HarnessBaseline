#!/usr/bin/env python3
"""Proof for tools/citers.py: every inbound citer and normative duty of a changed surface, with typed coverage.

Every case runs against a planted git repository under this proof's own temp root, never the live checkout.

    python3 .claude/tests/test_citers.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
ROOT = Path(__file__).resolve().parents[2]
CITERS = ROOT / ".claude" / "tools" / "citers.py"
TMP = Path(tempfile.mkdtemp(prefix="citers_proof_"))
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def git(repo, *args):
    run = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                         capture_output=True, text=True, encoding="utf-8")
    if run.returncode:
        raise RuntimeError(f"git {args}: {run.stderr}")
    return run.stdout


def plant(name, tracked, ignored=None):
    """A git repo holding `tracked` ({path: text}) at HEAD and, ignored by `.gitignore`, the `ignored` files."""
    repo = TMP / name
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".claude/tests/\n", encoding="utf-8")
    for rel, text in {**tracked}.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "plant")
    for rel, text in (ignored or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return repo


def run(repo, *args, vault=None):
    argv = [sys.executable, str(CITERS), "--repo", str(repo), "--vault", str(vault or TMP / "no_vault"), "--json", *args]
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else None
    except ValueError:
        data = None
    return proc, data


def path_surface(path, change="rewrite", **extra):
    return json.dumps({"kind": "path", "surface": path, "change": change, **extra})


ALPHA = ("# Alpha\n\nAlpha must always run before beta starts. Never skip the gate when alpha runs.\n")
TRACER = {
    ".claude/commands/alpha.md": ALPHA,
    ".claude/skills/x/SKILL.md": "# X\n\nSee [the alpha command](../../commands/alpha.md) for the order.\n",
    ".claude/rules/r.md": "# R\n\nThe step is owned by `commands/alpha.md` and nowhere else.\n",
    ".claude/rules/b.md": "# B\n\nThe file .claude/commands/alpha.md is the one owner of this rule.\n",
    ".claude/rules/unrelated.md": "# U\n\nNothing here cites that command by any path.\n",
}


def tracer_checks():
    repo = plant("tracer", TRACER)
    proc, data = run(repo, path_surface(".claude/commands/alpha.md"))
    check("a path surface sweeps with exit 0", proc.returncode == 0 and data is not None, (proc.returncode, proc.stderr[-300:]))
    citers = (data or {}).get("citers", [])
    by_path = {c["path"]: c for c in citers}
    check("a relative link is a link citer with its line and text",
          by_path.get(".claude/skills/x/SKILL.md", {}).get("kind") == "link"
          and by_path[".claude/skills/x/SKILL.md"].get("line") == 3
          and "alpha.md" in by_path[".claude/skills/x/SKILL.md"].get("text", ""), citers)
    check("a backticked path is a backtick-path citer",
          by_path.get(".claude/rules/r.md", {}).get("kind") == "backtick-path", citers)
    check("a bare path is a bare-path citer", by_path.get(".claude/rules/b.md", {}).get("kind") == "bare-path", citers)
    check("a file that never cites the surface is not a citer", ".claude/rules/unrelated.md" not in by_path, citers)
    check("the surface's own file is not its citer", ".claude/commands/alpha.md" not in by_path, citers)
    duties = [d["sentence"] for d in (data or {}).get("duties", [])]
    check("duties are the surface's normative sentences at HEAD",
          "Alpha must always run before beta starts." in duties and "Never skip the gate when alpha runs." in duties, duties)
    coverage = (data or {}).get("coverage", {})
    check("coverage counts the tracked files scanned", coverage.get("files") == len(TRACER), coverage)
    vault = coverage.get("vault", [])
    check("an unconfigured vault reports absent, never clean",
          len(vault) == 3 and all(v.get("absent") is True for v in vault) and coverage.get("vaultVerified") is False, vault)

    proc, _ = run(repo, path_surface(".claude/commands/missing.md"))
    check("a surface that does not resolve at HEAD exits 1 and names it",
          proc.returncode == 1 and ".claude/commands/missing.md" in proc.stderr, (proc.returncode, proc.stderr[-200:]))

    for label, arg in (
        ("malformed JSON", "{not json"),
        ("an unknown kind", json.dumps({"kind": "shrug", "surface": "x", "change": "rewrite"})),
        ("an unknown change", json.dumps({"kind": "path", "surface": "x", "change": "polish"})),
        ("a rename with no `to`", json.dumps({"kind": "path", "surface": ".claude/commands/alpha.md", "change": "rename"})),
    ):
        proc, _ = run(repo, arg)
        check(f"{label} exits 2", proc.returncode == 2 and "Traceback" not in proc.stderr, (proc.returncode, proc.stderr[-200:]))
    proc, _ = run(repo)
    check("no surface at all exits 2", proc.returncode == 2, proc.returncode)


GAMMA = """# Gamma

## 3. Obsidian (The Design Source)

The vault is read before any design is chosen. Never invent a formula the vault lacks.

## 4. Worklog

The worklog stays authoritative for every open item.

- **Self-checked before hand-off.** The drafter sweeps every caller before review. A missed caller is a defect.
  - A nested note under the bullet stays inside its section.
- **Next bullet.** Not part of the prior section at all.

See §3 above for the vault rule.
"""

KINDS_TREE = {
    ".claude/commands/gamma.md": GAMMA,
    ".claude/rules/cites.md": "# C\n\nPer `commands/gamma.md` §3 the vault is read first.\nAlso `commands/gamma.md` §Self-checked before hand-off covers callers.\n",
    ".claude/rules/other.md": "# O\n\nAnother doc says see §3 for its own vault rule.\n",
    ".claude/commands/k.md": "# K\n\nThe rule-key-x gate runs first. A rule-key-xy gate is a different key.\n",
    ".claude/commands/k2.md": "# K2\n\nOnly the rule-key-xy gate appears in this file.\n",
    ".claude/tools/mod.py": "def do_thing(value):\n    return value\n\n\nCONST_LIMIT = 4\n",
    ".claude/tools/caller.py": "import mod\nfrom mod import do_thing\n\nprint(mod.do_thing(1))\nprint(CONST_LIMIT)\n",
    ".claude/skills/x/SKILL.md": "# X\n\nRead `../../commands/gamma.md` first, then the unique helper.md note.\n",
    ".claude/a/README.md": "# A\n",
    ".claude/b/README.md": "# B\n",
    ".claude/rules/readme_cite.md": "# R\n\nThe README.md there is generic.\n",
    ".claude/tools/helper.md": "# Helper\n",
}
KINDS_IGNORED = {
    ".claude/tests/fixture.json": '{"key": "rule-key-x"}\n',
    ".claude/tests/test_thing.py": "import mod\n",
}


def kinds_of(data):
    return sorted({(c["path"], c["kind"]) for c in (data or {}).get("citers", [])})


def kind_checks():
    repo = plant("kinds", KINDS_TREE, KINDS_IGNORED)
    sec = {"kind": "section", "surface": ".claude/commands/gamma.md §3. Obsidian (The Design Source)", "change": "rewrite"}
    proc, data = run(repo, json.dumps(sec))
    got = kinds_of(data)
    check("a heading section sweeps with exit 0", proc.returncode == 0 and data is not None, proc.stderr[-300:])
    check("a renumbered `§N` cite beside the file is a section-cite",
          (".claude/rules/cites.md", "section-cite") in got, got)
    check("an internal `§N` cite in the surface's own file is a section-cite",
          (".claude/commands/gamma.md", "section-cite") in got, got)
    check("a `§N` cite that never names the file is not a citer", ".claude/rules/other.md" not in {p for p, _ in got}, got)
    duties = [d["sentence"] for d in (data or {}).get("duties", [])]
    check("a section's duties are restricted to that section",
          "Never invent a formula the vault lacks." in duties and not any("worklog" in d for d in duties), duties)

    bold = {"kind": "section", "surface": ".claude/commands/gamma.md §Self-checked before hand-off.", "change": "rewrite"}
    proc, data = run(repo, json.dumps(bold))
    duties = [d["sentence"] for d in (data or {}).get("duties", [])]
    check("a bold lead label anchors a bullet section with its nested lines",
          proc.returncode == 0 and "A missed caller is a defect." in duties
          and any("nested note" in d for d in duties) and not any("Not part of the prior" in d for d in duties),
          (proc.stderr[-200:], duties))
    check("a bold-lead cite beside the file is a section-cite",
          (".claude/rules/cites.md", "section-cite") in kinds_of(data), kinds_of(data))

    proc, _ = run(repo, json.dumps({**sec, "surface": ".claude/commands/gamma.md §No such heading"}))
    check("a section whose anchor matches no heading or bold lead exits 1 and names it",
          proc.returncode == 1 and "No such heading" in proc.stderr, (proc.returncode, proc.stderr[-200:]))

    key = {"kind": "key", "surface": "rule-key-x", "change": "rename", "to": "rule-key-z"}
    proc, data = run(repo, json.dumps(key))
    got = kinds_of(data)
    check("a key in prose is a key citer", (".claude/commands/k.md", "key") in got, got)
    check("a key in an ignored-but-present test fixture is a fixture citer",
          (".claude/tests/fixture.json", "fixture") in got, got)
    check("a key never matches inside a longer key", ".claude/commands/k2.md" not in {p for p, _ in got}, got)
    check("coverage counts the ignored tests it read", (data or {}).get("coverage", {}).get("ignoredTests") == 2,
          (data or {}).get("coverage"))
    proc, _ = run(repo, json.dumps({**key, "surface": "token-nobody-holds"}))
    check("a key held by no file exits 1", proc.returncode == 1 and "token-nobody-holds" in proc.stderr, proc.stderr[-200:])

    sym = {"kind": "symbol", "surface": ".claude/tools/mod.py::do_thing", "change": "rename", "to": "do_other"}
    proc, data = run(repo, json.dumps(sym))
    got = kinds_of(data)
    check("a call site is a symbol citer", (".claude/tools/caller.py", "symbol") in got, got)
    check("`from m import name` is an import citer",
          any(c["kind"] == "import" and c["path"] == ".claude/tools/caller.py" and "from mod import do_thing" in c["text"]
              for c in (data or {}).get("citers", [])), got)
    check("the symbol's own definition is not a citer",
          not any(c["path"] == ".claude/tools/mod.py" for c in (data or {}).get("citers", [])), got)
    const = {"kind": "symbol", "surface": ".claude/tools/mod.py::CONST_LIMIT", "change": "rewrite"}
    proc, _ = run(repo, json.dumps(const))
    check("a module constant resolves as a symbol", proc.returncode == 0, proc.stderr[-200:])
    proc, _ = run(repo, json.dumps({**sym, "surface": ".claude/tools/mod.py::not_defined"}))
    check("a symbol the file does not define exits 1", proc.returncode == 1 and "not_defined" in proc.stderr, proc.stderr[-200:])

    proc, data = run(repo, path_surface(".claude/tools/mod.py"))
    got = kinds_of(data)
    check("`import m` of a python surface is an import citer, in a tracked and an ignored file",
          (".claude/tools/caller.py", "import") in got and (".claude/tests/test_thing.py", "import") in got, got)

    check("a python surface carries no duties: its lines are code, not prose", (data or {}).get("duties") == [], (data or {}).get("duties"))

    proc, data = run(repo, path_surface(".claude/commands/gamma.md"))
    check("a `../` path in backticks resolves the way layer_closure resolves it",
          (".claude/skills/x/SKILL.md", "backtick-path") in kinds_of(data), kinds_of(data))
    proc, data = run(repo, path_surface(".claude/tools/helper.md"))
    check("a bare basename counts when no other file shares it",
          (".claude/skills/x/SKILL.md", "bare-path") in kinds_of(data), kinds_of(data))
    proc, data = run(repo, path_surface(".claude/a/README.md"))
    check("a bare basename shared by two files is not a citer",
          ".claude/rules/readme_cite.md" not in {p for p, _ in kinds_of(data)}, kinds_of(data))


def exclusion_checks():
    target = ".claude/commands/alpha.md"
    subject = ".claude/plans/subject.md"
    key = json.dumps({"kind": "key", "surface": "inbound-key", "change": "rewrite"})
    plan = '# Plan\ninbound-key\n```surfaces\n' + key + '\n```\n'
    repo = plant("exclusions", {
        target: ALPHA + "inbound-key\n", subject: plan,
        ".claude/plans/other.md": "inbound-key\n",
        ".claude/instruction_rows/rulings.jsonl": '{"key": "inbound-key"}\n',
        ".claude/escape_log.jsonl": '{"key": "inbound-key"}\n',
        ".claude/other_generated.jsonl": '{"key": "inbound-key"}\n',
        **{p: '{"key": "inbound-key"}\n' * 12 for p in (
            ".claude/orchestration_metrics.jsonl", ".claude/autolearn_outcomes.jsonl",
            ".claude/self_evaluate_archive.jsonl", ".claude/scripts/benchmark_campaign/run.stream.jsonl")},
    }, {".claude/tests/fixture.jsonl": '{"key": "inbound-key"}\n'})
    proc, data = run(repo, "--plan", str(repo / subject))
    paths = {c["path"] for c in (data or {}).get("citers", [])}
    coverage = (data or {}).get("coverage", {})
    check("the subject plan's own lines are not inbound citers", proc.returncode == 0 and subject not in paths, paths)
    check("consumed JSONL and other generated records remain inbound citers",
          {".claude/escape_log.jsonl", ".claude/instruction_rows/rulings.jsonl", ".claude/other_generated.jsonl"} <= paths, paths)
    check("enumerated logs are excluded before caps",
          not any(p.endswith(".stream.jsonl") or p in {".claude/orchestration_metrics.jsonl",
                  ".claude/autolearn_outcomes.jsonl", ".claude/self_evaluate_archive.jsonl"} for p in paths)
          and not coverage.get("truncated"), coverage)
    check("JSONL inventory records method and class counts",
          coverage.get("jsonlInventory", {}).get("classes") == {"appendOnlyLog": 3, "benchmarkProgressStream": 1, "eligible": 3}
          and "git ls-files" in coverage.get("jsonlInventory", {}).get("method", ""), coverage)
    check("subject records worktree hash and sweep HEAD",
          (data or {}).get("subjects") == [{"path": str((repo / subject).resolve()),
              "hash": git(repo, "hash-object", subject).strip(), "head": git(repo, "rev-parse", "HEAD").strip()}], data)
    check("coverage counts excluded subject-plan rows", coverage.get("excludedRows", {}).get("subjectPlan") == 2, coverage)
    check("coverage counts excluded generated-JSONL rows", coverage.get("excludedRows", {}).get("generatedJsonl") == 48, coverage)
    check("other live plans and JSONL test fixtures remain inbound citers",
          {".claude/plans/other.md", ".claude/tests/fixture.jsonl"} <= paths, paths)
    proc, data = run(repo, key)
    check("positional surfaces do not exclude a plan that was not named by --plan",
          proc.returncode == 0 and subject in {c["path"] for c in (data or {}).get("citers", [])}, proc.stderr)


def cap_checks():
    # The cap fixture uses prose now: generated JSONL rows are excluded before the citer cap.
    repo = plant("cap", {".claude/commands/k.md": "# K\n\nThe cap-key gate.\n",
                         ".claude/log.md": "".join(f"row {i} cap-key\n" for i in range(12))})
    proc, data = run(repo, json.dumps({"kind": "key", "surface": "cap-key", "change": "rewrite"}))
    rows = [c for c in (data or {}).get("citers", []) if c["path"] == ".claude/log.md"]
    cut = (data or {}).get("coverage", {}).get("truncated", [])
    check("a file's citers past the per-file cap are counted in coverage, never dropped silently",
          len(rows) == 10 and cut == [{"surface": "cap-key", "path": ".claude/log.md", "omitted": 2}], (len(rows), cut))
    check("a file under the cap reports no truncation", all(t["path"] != ".claude/commands/k.md" for t in cut), cut)


def vault_checks():
    import citers
    import vault_read
    repo = plant("vault", {".claude/commands/alpha.md": ALPHA})
    vault = TMP / "vault_root"
    note = vault / "DevProjects/{{PROJECT_NAME}}/Claude/note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# N\n\nThe .claude/commands/alpha.md owner is recorded here.\n", encoding="utf-8")
    (note.parent / "dehydrated.md").write_text("cites .claude/commands/alpha.md too\n", encoding="utf-8")
    real = vault_read.dehydrated
    vault_read.dehydrated = lambda path: Path(path).name == "dehydrated.md"
    try:
        result = citers.sweep([{"kind": "path", "surface": ".claude/commands/alpha.md", "change": "rewrite"}],
                              repo=repo, vault=vault)
    finally:
        vault_read.dehydrated = real
    roots = {Path(v["root"]).name: v for v in result["coverage"]["vault"]}
    mine = roots.get("{{PROJECT_NAME}}", {})
    check("a readable vault note citing the surface is a citer with its absolute path",
          any(c["path"].endswith("note.md") and c["kind"] == "bare-path" for c in result["citers"]), result["citers"])
    check("a dehydrated vault file is typed unattempted, never read",
          mine.get("read") == 1 and mine.get("unattempted") == 1
          and not any(c["path"].endswith("dehydrated.md") for c in result["citers"]), mine)
    check("one unattempted file and two absent roots keep the vault unverified",
          roots.get("Jmodot", {}).get("absent") is True and result["coverage"]["vaultVerified"] is False, roots)


def plan_checks():
    repo = plant("plan", {".claude/commands/alpha.md": ALPHA, ".claude/rules/r.md": "# R\n\nSee `commands/alpha.md` here.\n"})
    good = TMP / "good_plan.md"
    good.write_text('# Plan\n\n```surfaces\n{"kind": "path", "surface": ".claude/commands/alpha.md", "change": "rewrite"}\n\n'
                    '{"kind": "key", "surface": "Alpha must always", "change": "rewrite"}\n```\n', encoding="utf-8")
    proc, data = run(repo, "--plan", str(good))
    check("--plan reads every line of the fenced surfaces block",
          proc.returncode == 0 and len((data or {}).get("surfaces", [])) == 2, (proc.returncode, proc.stderr[-200:]))
    none = TMP / "no_block_plan.md"
    none.write_text("# Plan\n\nNo block here.\n", encoding="utf-8")
    proc, _ = run(repo, "--plan", str(none))
    check("a plan with no surfaces block exits 2 and prints the grammar",
          proc.returncode == 2 and '"kind"' in proc.stderr and "§Surfaces touched" in proc.stderr, proc.stderr[-300:])
    bad = TMP / "bad_plan.md"
    bad.write_text('# Plan\n\n```surfaces\n{"kind": "shrug", "surface": "x", "change": "rewrite"}\n```\n', encoding="utf-8")
    proc, _ = run(repo, "--plan", str(bad))
    check("a block line with an unknown kind exits 2", proc.returncode == 2, proc.returncode)


DELTA = ("# Delta\n\nThe gate runs before every commit lands. Reviewers read the whole diff first. "
         "Every mandate returns separate evidence for itself. Dropped duty text appears only in this old file.\n")


def after_checks():
    repo = plant("after", {".claude/commands/delta.md": DELTA, ".claude/rules/home.md": "# H\n\nNothing relevant lives here.\n"})
    (repo / ".claude/commands/delta.md").write_text(
        "# Delta\n\nThe gate runs before every commit lands. Each mandate returns separate evidence of its own work now.\n",
        encoding="utf-8")
    (repo / ".claude/rules/home.md").write_text(
        "# H\n\nNothing relevant lives here. Reviewers read the whole diff first.\n", encoding="utf-8")
    proc, data = run(repo, path_surface(".claude/commands/delta.md"), "--after", "WORKTREE")
    classes = {d["sentence"]: d.get("class") for d in (data or {}).get("duties", [])}
    check("--after WORKTREE classes a kept duty", classes.get("The gate runs before every commit lands.") == "kept", classes)
    check("--after classes a reworded duty",
          classes.get("Every mandate returns separate evidence for itself.") == "reworded", classes)
    check("--after classes a duty another file carries as moved → path",
          classes.get("Reviewers read the whole diff first.") == "moved → .claude/rules/home.md", classes)
    check("--after classes a duty no file carries as removed",
          classes.get("Dropped duty text appears only in this old file.") == "removed", classes)
    proc, data = run(repo, path_surface(".claude/commands/delta.md"))
    check("without --after a duty carries no class", all("class" not in d for d in (data or {}).get("duties", [])), data)

    git(repo, "checkout", "-q", "-b", "edit")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "edit")
    git(repo, "checkout", "-q", "-")
    proc, data = run(repo, path_surface(".claude/commands/delta.md"), "--after", "edit")
    classes = {d["sentence"]: d.get("class") for d in (data or {}).get("duties", [])}
    check("--after REV reads the revision's text and corpus",
          classes.get("The gate runs before every commit lands.") == "kept"
          and classes.get("Reviewers read the whole diff first.") == "moved → .claude/rules/home.md", classes)

    git(repo, "checkout", "-q", "edit")
    (repo / ".claude/commands/delta2.md").write_text(
        "# Delta2\n\nThe gate runs before every commit lands. Reviewers read the whole diff first.\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rename")
    git(repo, "checkout", "-q", "-")
    rename = path_surface(".claude/commands/delta.md", "rename", to=".claude/commands/delta2.md")
    proc, data = run(repo, rename, "--after", "edit")
    classes = {d["sentence"]: d.get("class") for d in (data or {}).get("duties", [])}
    check("a rename reads the after text from its `to` path",
          classes.get("Reviewers read the whole diff first.") == "kept", classes)
    (repo / ".claude/commands/delta.md").unlink()
    proc, data = run(repo, path_surface(".claude/commands/delta.md", "delete"), "--after", "WORKTREE")
    check("a delete classes every duty moved or removed, never kept",
          proc.returncode == 0 and all(d.get("class") == "removed" or str(d.get("class")).startswith("moved")
                                       for d in (data or {}).get("duties", [])) and bool((data or {}).get("duties")), data)


def deleted_checks():
    command = ".claude/commands/gone.md"
    module = ".claude/tools/gone.py"
    never_tracked = ".claude/commands/never.md"
    repo = plant("deleted", {
        command: "# Gone\n\n## Gate\n\nThe original gate must run.\n",
        module: "def retired():\n    return True\n",
        ".claude/rules/caller.md": "Read gone.md and `.claude/commands/never.md`.\n"
                                  "Follow `commands/gone.md` §Gate.\n",
        ".claude/tools/caller.py": "import gone\nfrom gone import retired\n",
    })
    (repo / command).write_text("# Gone\n\n## Gate\n\nThe latest gate must run.\n", encoding="utf-8", newline="\n")
    git(repo, "add", command)
    git(repo, "commit", "-q", "-m", "update duty before deletion")
    git(repo, "rm", command, module)
    git(repo, "commit", "-q", "-m", "delete surfaces")
    (repo / ".claude/rules/later.md").write_text("# Later\n", encoding="utf-8", newline="\n")
    git(repo, "add", ".claude/rules/later.md")
    git(repo, "commit", "-q", "-m", "unrelated later change")

    proc, data = run(repo, path_surface(command, "delete"), "--after", "HEAD")
    check("a landed deletion still sweeps its inbound citers", proc.returncode == 0 and data is not None, proc.stderr)
    check("a deleted unique basename still resolves as a bare-path citer",
          any(c["kind"] == "bare-path" and "gone.md" in c["text"] for c in (data or {}).get("citers", [])), data)
    duty_rows = (data or {}).get("duties", [])
    check("a deleted markdown path reads the last held text, not the original duty",
          duty_rows == [{"surface": command, "sentence": "The latest gate must run.", "class": "removed"}], duty_rows)

    sec = {"kind": "section", "surface": command + " §Gate", "change": "delete"}
    proc, data = run(repo, json.dumps(sec))
    check("a section in a deleted file retains its citers and historical duties",
          proc.returncode == 0 and (".claude/rules/caller.md", "section-cite") in kinds_of(data)
          and any(d["sentence"] == "The latest gate must run." for d in (data or {}).get("duties", [])), proc.stderr)
    proc, data = run(repo, path_surface(module, "delete"))
    check("a deleted python path still finds imports",
          proc.returncode == 0 and (".claude/tools/caller.py", "import") in kinds_of(data), proc.stderr)
    sym = {"kind": "symbol", "surface": module + "::retired", "change": "delete"}
    proc, data = run(repo, json.dumps(sym))
    check("a symbol in a deleted file still finds its callers",
          proc.returncode == 0 and (".claude/tools/caller.py", "import") in kinds_of(data), proc.stderr)
    proc, data = run(repo, path_surface(never_tracked, "delete"))
    check("a deletion with no available history scans path text without inventing duties",
          proc.returncode == 0 and bool((data or {}).get("citers")) and (data or {}).get("duties") == [], proc.stderr)
    key = {"kind": "key", "surface": "already-removed-key", "change": "delete"}
    proc, data = run(repo, json.dumps(key))
    check("an already removed key yields an empty successful sweep",
          proc.returncode == 0 and (data or {}).get("citers") == [], proc.stderr)
    partial = ".claude/commands/partial.md"
    repo_partial = plant("deleted_section", {
        partial: "# Partial\n\n## Retired\n\nThe retired duty must run.\n\n## Kept\n\nOther text.\n",
        ".claude/rules/caller.md": "Follow `commands/partial.md` §Retired.\n",
    })
    (repo_partial / partial).write_text("# Partial\n\n## Kept\n\nOther text.\n", encoding="utf-8", newline="\n")
    git(repo_partial, "add", partial)
    git(repo_partial, "commit", "-q", "-m", "delete only the section")
    sec_partial = {"kind": "section", "surface": partial + " §Retired", "change": "delete"}
    proc, data = run(repo_partial, json.dumps(sec_partial), "--after", "HEAD")
    check("a deleted section in a surviving file retains its last held duties",
          proc.returncode == 0 and (data or {}).get("duties") == [
              {"surface": partial + " §Retired", "sentence": "The retired duty must run.", "class": "removed"}], data)

    for surface in (path_surface(command), json.dumps({**sec, "change": "rewrite"}),
                    json.dumps({**sym, "change": "rewrite"}), json.dumps({**key, "change": "rewrite"})):
        proc, _ = run(repo, surface)
        check("non-delete surfaces still require HEAD resolution", proc.returncode == 1 and "Traceback" not in proc.stderr, proc.stderr)


def renamed_checks():
    old = '.claude/commands/old.md'
    new = '.claude/commands/new.md'
    mod = '.claude/tools/mod.py'
    repo = plant('renamed', {
        old: '# Old\n\n## Gate\n\nThe original duty must run.\n',
        mod: 'def old_name():\n    return True\n',
        '.claude/rules/caller.md': 'Read `.claude/commands/old.md` §Gate; old-key.\n',
        '.claude/tools/caller.py': 'from mod import old_name\n',
    })
    (repo / old).rename(repo / new)
    (repo / new).write_text('# New\n\n## New Gate\n\nThe original duty must run.\n', encoding='utf-8')
    (repo / mod).write_text('def new_name():\n    return True\n', encoding='utf-8')
    (repo / '.claude/rules/caller.md').write_text('Read `.claude/commands/old.md` §Gate; new-key.\n', encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'land renames')
    surfaces = (
        {'kind': 'path', 'surface': old, 'change': 'rename', 'to': new},
        {'kind': 'section', 'surface': old + ' §Gate', 'change': 'rename', 'to': new + ' §New Gate'},
        {'kind': 'symbol', 'surface': mod + '::old_name', 'change': 'rename', 'to': 'new_name'},
        {'kind': 'key', 'surface': 'old-key', 'change': 'rename', 'to': 'new-key'},
    )
    for surface in surfaces:
        proc, data = run(repo, json.dumps(surface), '--after', 'HEAD')
        check('landed ' + surface['kind'] + ' rename resolves and retains its old subject',
              proc.returncode == 0 and (data or {}).get('surfaces') == [surface], proc.stderr)
        if surface['kind'] in ('path', 'section'):
            check('landed ' + surface['kind'] + ' rename keeps original duties from history',
                  any(row['sentence'] == 'The original duty must run.' and row['class'] == 'kept'
                      for row in (data or {}).get('duties', [])), data)
        if surface['kind'] == 'symbol':
            check('landed symbol rename still finds stale import callers',
                  ('.claude/tools/caller.py', 'import') in kinds_of(data), data)
        proc, _ = run(repo, json.dumps({**surface, 'to': 'destination-never-existed'}))
        check('a historical ' + surface['kind'] + ' source resolves even without a current destination',
              proc.returncode == 0, proc.stderr)
        proc, _ = run(repo, json.dumps({**surface, 'change': 'rewrite'}))
        check('a retired ' + surface['kind'] + ' rewrite still requires HEAD', proc.returncode == 1, proc.stderr)
    destination_only = {'kind': 'symbol', 'surface': '.claude/tools/missing.py::old_name',
                        'change': 'rename', 'to': mod + '::new_name'}
    proc, _ = run(repo, json.dumps(destination_only))
    check('a current destination resolves a rename with unavailable source history', proc.returncode == 0, proc.stderr)
    proc, _ = run(repo, json.dumps({**destination_only, 'to': mod + '::typo'}))
    check('a rename with neither source history nor destination still fails', proc.returncode == 1, proc.stderr)


def main():
    tracer_checks()
    kind_checks()
    cap_checks()
    exclusion_checks()
    vault_checks()
    plan_checks()
    after_checks()
    deleted_checks()
    renamed_checks()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
