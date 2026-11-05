#!/usr/bin/env python3
"""Proof for tools/verify_doc_citations.py: the citation resolver /memory_audit lens 7 reads.

Plants a fake repo in a temp dir and runs the tool as a subprocess with `--repo <tmp>`. Cases:
  1. path / command / flag / harness-symbol / game-symbol rows, each asserted on the JSON status;
  2. placeholders and wikilinks: `<x>` is unparseable, `[[name]]` is no citation;
  3. the kept single-doc behavior: exit 1 on a bad `file:line`, 0 when all resolve, 2 on a
     missing doc, and the default kinds emit no path/command/flag rows;
  4. exit rules of --json: unparseable alone never fails;
  5. `--only` drops rows and emptied files from the output, never from `counts`/`files_checked`,
     and `@listfile` expands to its doc paths.

A symbol proves existence only through non-comment code: `GhostType` (only in a .md) and
`bar.qux` (only in a comment) must both be missing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, ".claude", "tools", "verify_doc_citations.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
import verify_doc_citations as vdc  # noqa: E402

MEMORY = """# fixture memory

Paths: `.claude/tools/bar.py` and `.claude/tools/gone.py`.
Commands: `/foo`, `/skl`, `/nosuch`, and {builtin} in prose.
Flags: `python3 .claude/tools/bar.py --json`, `python3 .claude/tools/bar.py --nope`, `--orphan`.
Placeholder: `.claude/<x>/y.md`. Outside: `~/somewhere/z.md`.
Symbols: `RealType`, `GhostType`, `bar.baz`, `bar.qux`.
Link: [[not_a_citation]]
"""


def plant(root, rel, text):
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return full


def plant_adaptation(root, **keys):
    return plant(root, ".claude/skills/project_subsystems/adaptation.json", json.dumps(keys))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = cls.tmp.name
        plant_adaptation(r, source_extensions=[".cs", ".gd", ".tscn", ".tres", ".godot"],
                         symbol_stopwords=["Godot", "Resource"])
        plant(r, ".claude/commands/foo.md", "# foo\n")
        plant(r, ".claude/skills/skl/SKILL.md", "# skl\n")
        plant(r, ".claude/tools/bar.py", "# qux\nUSAGE = '--json'\n\ndef baz():\n    return 1\n")
        plant(r, "Game/Thing.cs", "class RealType {}\n")
        plant(r, "notes.md", "GhostType is only mentioned here.\n")
        cls.builtin = sorted(getattr(vdc, "BUILTIN_COMMANDS", {"__missing__"}))[0]
        cls.mem = plant(r, ".claude/auto-memory/mem.md", MEMORY.format(builtin="`/%s`" % cls.builtin))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True)

    def rows(self, *files, kinds="all"):
        p = self.run_tool(*files, "--repo", self.tmp.name, "--kinds", kinds, "--json")
        self.assertIn(p.returncode, (0, 1), p.stderr)
        data = json.loads(p.stdout)
        return {(c["kind"], c["text"]): c["status"] for f in data["files"] for c in f["citations"]}, data


class TestKinds(Base):
    def status(self, kind, text):
        rows, _ = self.rows(self.mem)
        self.assertIn((kind, text), rows, sorted(rows))
        return rows[(kind, text)]

    def test_relative_links_use_the_citing_file_directory(self):
        with tempfile.TemporaryDirectory() as r:
            plant_adaptation(r, source_extensions=[], symbol_stopwords=[])
            plant(r, ".claude/rules/scene_authoring.md", "# Scene anatomy\n")
            plant(r, "rules/root_only.md", "# Root-only target\n")
            doc = plant(r, ".claude/reference/scene_construction.md",
                        "Scene anatomy stays in [`../rules/scene_authoring.md`](../rules/scene_authoring.md).\n"
                        "Bare repo path: `.claude/rules/scene_authoring.md`.\n"
                        "Relative file-line: `../rules/scene_authoring.md:1`.\n"
                        "Missing relative: `../rules/root_only.md`.\n"
                        "Dot relative: `./absent.md`.\n"
                        "Outside repo: `../../../outside.md`.\n")
            p = self.run_tool(doc, "--repo", r, "--kinds", "path,fileline", "--json")
            self.assertEqual(p.returncode, 1, p.stderr)
            data = json.loads(p.stdout)
            rows = {(row['kind'], row['text']): row['status'] for f in data['files'] for row in f['citations']}
            self.assertEqual(rows['path', '../rules/scene_authoring.md'], 'found')
            self.assertEqual(rows['path', '.claude/rules/scene_authoring.md'], 'found')
            self.assertEqual(rows['fileline', '../rules/scene_authoring.md:1'], 'found')
            self.assertEqual(rows['path', '../rules/root_only.md'], 'missing')
            self.assertEqual(rows['path', './absent.md'], 'missing')
            self.assertEqual(rows['path', '../../../outside.md'], 'unparseable')

    def test_outside_relative_fileline_is_unparseable(self):
        with tempfile.TemporaryDirectory() as r:
            plant_adaptation(r, source_extensions=[], symbol_stopwords=[])
            doc = plant(r, '.claude/reference/outside.md', '`../../../outside.md:1`\n')
            for kinds in ('fileline', 'all'):
                with self.subTest(kinds=kinds):
                    p = self.run_tool(doc, '--repo', r, '--kinds', kinds, '--json')
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                    rows = [row for f in json.loads(p.stdout)['files'] for row in f['citations']]
                    self.assertEqual([(row['kind'], row['status']) for row in rows],
                                     [('fileline', 'unparseable')])

    def test_plain_markdown_link_destinations_are_paths(self):
        with tempfile.TemporaryDirectory() as r:
            plant_adaptation(r, source_extensions=[], symbol_stopwords=[])
            plant(r, '.claude/rules/existing.md', '# Rule\n')
            plant(r, '.claude/rules/with space.md', '# Rule\n')
            plant(r, '.claude/reference/local/child.md', '# Child\n')
            doc = plant(r, '.claude/reference/links.md',
                        '[Local](local/child.md)\n'
                        '[Rule](../rules/existing.md)\n'
                        '[Section](../rules/existing.md#rule)\n'
                        '[Missing](../rules/missing.md "title")\n'
                        '[Space](<../rules/with space.md>)\n'
                        'Bare repo path: `.claude/rules/existing.md`\n'
                        '[Anchor](#rule) [Web](https://example.com/no.md)\n'
                        '[Mail](mailto:team@example.com) [Remote](//example.com/no.md)\n')
            p = self.run_tool(doc, '--repo', r, '--kinds', 'path', '--json')
            data = json.loads(p.stdout)
            rows = {row['text']: row['status'] for f in data['files'] for row in f['citations']}
            self.assertEqual(rows, {'local/child.md': 'found',
                                    '../rules/existing.md': 'found',
                                    '../rules/missing.md': 'missing',
                                    '../rules/with space.md': 'found',
                                    '.claude/rules/existing.md': 'found'})
            self.assertEqual(p.returncode, 1, p.stderr)

    def test_path_found(self):
        self.assertEqual(self.status("path", ".claude/tools/bar.py"), "found")

    def test_path_missing(self):
        self.assertEqual(self.status("path", ".claude/tools/gone.py"), "missing")

    def test_command_found_by_command_file(self):
        self.assertEqual(self.status("command", "/foo"), "found")

    def test_command_found_by_skill(self):
        self.assertEqual(self.status("command", "/skl"), "found")

    def test_command_missing(self):
        self.assertEqual(self.status("command", "/nosuch"), "missing")

    def test_builtin_command_found(self):
        self.assertEqual(self.status("command", "/" + self.builtin), "found")

    def test_flag_found(self):
        self.assertEqual(self.status("flag", "--json"), "found")

    def test_flag_missing(self):
        self.assertEqual(self.status("flag", "--nope"), "missing")

    def test_moved_path_with_directory_is_missing(self):
        self.assertEqual(vdc.Resolver(self.tmp.name).resolve_path(".claude/tools/moved/bar.py")[0], "missing")

    def test_bare_basename_still_resolves(self):
        self.assertEqual(vdc.Resolver(self.tmp.name).resolve_path("bar.py")[0], "found")

    def test_flag_prefix_of_a_longer_flag_is_not_found(self):
        self.assertFalse(vdc.Resolver(self.tmp.name).tool_has_flag(".claude/tools/bar.py", "--js"))

    def test_build_index_prunes_build_dirs_without_git(self):
        with tempfile.TemporaryDirectory() as r:
            plant(r, "obj/Gen.cs", "class G {}\n")
            plant(r, "Game/Real.cs", "class R {}\n")
            _, sources = vdc.build_index(r, (".cs",))
            names = sorted(os.path.basename(s) for s in sources)
            self.assertEqual(names, ["Real.cs"], names)

    def test_flag_without_tool_is_unparseable(self):
        self.assertEqual(self.status("flag", "--orphan"), "unparseable")

    def test_placeholder_path_is_unparseable(self):
        self.assertEqual(self.status("path", ".claude/<x>/y.md"), "unparseable")

    def test_home_path_is_unparseable(self):
        self.assertEqual(self.status("path", "~/somewhere/z.md"), "unparseable")

    def test_game_symbol_found_in_code(self):
        self.assertEqual(self.status("symbol", "RealType"), "found")

    def test_game_symbol_only_in_md_is_missing(self):
        self.assertEqual(self.status("symbol", "GhostType"), "missing")

    def test_harness_symbol_found_when_defined(self):
        self.assertEqual(self.status("symbol", "bar.baz"), "found")

    def test_harness_symbol_only_in_comment_is_missing(self):
        self.assertEqual(self.status("symbol", "bar.qux"), "missing")

    def test_wikilink_is_no_citation(self):
        rows, _ = self.rows(self.mem)
        self.assertFalse([k for k in rows if "not_a_citation" in k[1]], sorted(rows))

    def test_counts_reconcile_to_rows(self):
        rows, data = self.rows(self.mem)
        for status in ("found", "missing", "unparseable"):
            self.assertEqual(data["counts"][status], sum(1 for s in rows.values() if s == status))
        self.assertGreater(data["counts"]["unparseable"], 0)

    def test_json_exit_one_when_missing(self):
        p = self.run_tool(self.mem, "--repo", self.tmp.name, "--kinds", "all", "--json")
        self.assertEqual(p.returncode, 1)

    def test_unparseable_alone_exits_zero(self):
        doc = plant(self.tmp.name, "only_unparseable.md", "See `.claude/<x>/y.md` and `--orphan`.\n")
        p = self.run_tool(doc, "--repo", self.tmp.name, "--kinds", "all", "--json")
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_several_files_reported_separately(self):
        doc = plant(self.tmp.name, "second.md", "Only `.claude/tools/bar.py` here.\n")
        _, data = self.rows(self.mem, doc)
        self.assertEqual([os.path.abspath(f["path"]) for f in data["files"]],
                         [os.path.abspath(self.mem), os.path.abspath(doc)])

    def test_only_filters_rows_and_keeps_counts(self):
        doc = plant(self.tmp.name, "all_found.md", "Only `.claude/tools/bar.py` here.\n")
        p = self.run_tool(self.mem, doc, "--repo", self.tmp.name, "--kinds", "all", "--json",
                          "--only", "missing,unparseable")
        self.assertIn(p.returncode, (0, 1), p.stderr)
        data = json.loads(p.stdout)
        statuses = {c["status"] for f in data["files"] for c in f["citations"]}
        self.assertEqual(statuses, {"missing", "unparseable"})
        self.assertEqual([os.path.abspath(f["path"]) for f in data["files"]], [os.path.abspath(self.mem)])
        self.assertEqual(data["files_checked"], 2)
        self.assertGreater(data["counts"]["found"], 0)

    def test_listfile_expands_to_docs(self):
        listfile = plant(self.tmp.name, "docs.txt", self.mem + "\n")
        _, data = self.rows("@" + listfile)
        self.assertEqual([os.path.abspath(f["path"]) for f in data["files"]], [os.path.abspath(self.mem)])


class TestEngineNeutral(unittest.TestCase):
    """The tool names no engine: extensions, vocabulary words and skipped folders come from the repo."""

    def check(self, adaptation, files, doc_text, kinds="symbol,fileline", gitignore=None):
        with tempfile.TemporaryDirectory() as r:
            if adaptation is not None:
                plant_adaptation(r, **adaptation)
            for rel, text in files.items():
                plant(r, rel, text)
            if gitignore is not None:
                plant(r, ".gitignore", gitignore)
                subprocess.run(["git", "init", "-q", r], check=True, capture_output=True)
            doc = plant(r, "doc.md", doc_text)
            p = subprocess.run([sys.executable, TOOL, doc, "--repo", r, "--kinds", kinds, "--json"],
                               capture_output=True, text=True)
            self.assertIn(p.returncode, (0, 1), p.stderr)
            data = json.loads(p.stdout)
            return {(c["kind"], c["text"]): c for f in data["files"] for c in f["citations"]}

    def test_no_source_extensions_makes_a_symbol_unparseable(self):
        rows = self.check({}, {"a/Thing.rs": "struct RealType;\n"}, "`RealType`\n")
        row = rows[("symbol", "RealType")]
        self.assertEqual(row["status"], "unparseable")
        self.assertEqual(row["detail"], "no source_extensions configured")

    def test_absent_adaptation_file_is_the_same_as_empty(self):
        rows = self.check(None, {"a/Thing.rs": "struct RealType;\n"}, "`RealType`\n")
        self.assertEqual(rows[("symbol", "RealType")]["status"], "unparseable")

    def test_configured_extension_proves_a_symbol_and_is_a_fileline_target(self):
        rows = self.check({"source_extensions": [".rs"]},
                          {"a/Thing.rs": "struct RealType;\nfn a() {}\nfn b() {}\n", "a/Thing.cs": "GhostType\n"},
                          "`RealType` `GhostType` see `a/Thing.rs:3` and `a/Thing.rs:9`\n")
        self.assertEqual(rows[("symbol", "RealType")]["status"], "found")
        self.assertEqual(rows[("symbol", "GhostType")]["status"], "missing")
        self.assertEqual(rows[("fileline", "a/Thing.rs:3")]["status"], "found")
        self.assertEqual(rows[("fileline", "a/Thing.rs:9")]["status"], "missing")

    def test_unconfigured_extension_is_not_a_fileline_citation(self):
        rows = self.check({"source_extensions": [".rs"]}, {"a/Thing.cs": "x\n"}, "see `a/Thing.cs:3`\n")
        self.assertNotIn(("fileline", "a/Thing.cs:3"), rows)

    def test_symbol_stopword_is_not_a_symbol_citation(self):
        rows = self.check({"source_extensions": [".rs"], "symbol_stopwords": ["Widget"]},
                          {"a/Thing.rs": "struct Other;\n"}, "`Widget` and `GadgetType`\n")
        self.assertNotIn(("symbol", "Widget"), rows)
        self.assertEqual(rows[("symbol", "GadgetType")]["status"], "missing")

    def test_a_gitignored_directory_is_not_indexed(self):
        rows = self.check({"source_extensions": [".rs"]},
                          {"src/Kept.rs": "struct KeptType;\n", "target/Built.rs": "struct BuiltType;\n"},
                          "`KeptType` `BuiltType`\n", gitignore="target/\n")
        self.assertEqual(rows[("symbol", "KeptType")]["status"], "found")
        self.assertEqual(rows[("symbol", "BuiltType")]["status"], "missing")

    def test_without_a_gitignore_the_same_directory_is_indexed(self):
        rows = self.check({"source_extensions": [".rs"]},
                          {"target/Built.rs": "struct BuiltType;\n"}, "`BuiltType`\n", gitignore="")
        self.assertEqual(rows[("symbol", "BuiltType")]["status"], "found")

    def test_an_ignored_directory_under_the_harness_tree_is_still_indexed(self):
        rows = self.check({"source_extensions": [".rs"]},
                          {".claude/tests/Proof.rs": "struct ProofType;\n"}, "`ProofType`\n",
                          gitignore=".claude/tests/\n")
        self.assertEqual(rows[("symbol", "ProofType")]["status"], "found")


class TestPremiseSelfCheck(unittest.TestCase):
    def read_command(self, name):
        with open(os.path.join(ROOT, ".claude", "commands", name + ".md"), encoding="utf-8") as fh:
            return fh.read()

    def test_codify_done_checks_landing_premises(self):
        text = self.read_command("codify")
        step = text.split("## Step 0 — Does it already exist?", 1)[1].split("## Step 1", 1)[0]
        done = next(line for line in step.splitlines() if line.startswith("**Done:"))
        for required in ("diagnosis's premises", "landing commit's `premises` block",
                         "VERIFIED", "--session", "before landing",
                         "fixed or dropped", "reference/evidence_contract.md"):
            self.assertIn(required, done)
        self.assertIn("tools/verify_claims.py", done)

    def test_autolearn_applying_cites_codify_self_check(self):
        applying = self.read_command("autolearn").split("## Applying changes", 1)[1]
        self.assertIn("[Step 0 self-check](codify.md#step-0--does-it-already-exist)", applying)
        self.assertIn("before landing", applying)

    def test_edited_command_paths_and_commands_resolve(self):
        docs = [os.path.join(ROOT, ".claude", "commands", name + ".md")
                for name in ("codify", "autolearn")]
        p = subprocess.run([sys.executable, TOOL, *docs, "--repo", ROOT, "--kinds", "path,command",
                            "--json", "--only", "missing"], capture_output=True, text=True)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["files_checked"], 2)
        # A fresh or sparse checkout lacks runtime state the harness writes (logs, temp output) and the files
        # outside its cone; a cite is broken only when it is neither and git tracks no file by that path.
        runtime = ("logs/", ".claude/logs/", "/tmp/")
        # Each consumer owns its own outcomes ledger and baseline lock; the publish template never ships them.
        runtime += (".claude/autolearn_outcomes.jsonl", "baseline.lock.json")
        tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split("\n")
        # A directory cite outside `.claude/` names a project folder (a game's `Tests/`); the published template
        # does not ship project folders, so only the harness tree must resolve.
        project_dir = lambda text: text.endswith("/") and not text.startswith(".claude/")  # noqa: E731
        broken = [c["text"] for f in data["files"] for c in f["citations"]
                  if c["status"] == "missing" and not c["text"].startswith(runtime) and not project_dir(c["text"])
                  and not any(t == c["text"] or t.endswith("/" + c["text"]) for t in tracked)]
        self.assertEqual(broken, [])


class TestKeptBehavior(Base):
    def test_bad_fileline_exits_one(self):
        doc = plant(self.tmp.name, "bad_line.md", "See `Game/Thing.cs:999`.\n")
        self.assertEqual(self.run_tool(doc, "--repo", self.tmp.name).returncode, 1)

    def test_all_resolving_exits_zero(self):
        doc = plant(self.tmp.name, "good.md", "See `Game/Thing.cs:1` and `RealType`.\n")
        p = self.run_tool(doc, "--repo", self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("All citations resolve.", p.stdout)

    def test_missing_doc_exits_two(self):
        p = self.run_tool(os.path.join(self.tmp.name, "nope.md"), "--repo", self.tmp.name)
        self.assertEqual(p.returncode, 2)

    def test_missing_doc_among_several_exits_two(self):
        p = self.run_tool(self.mem, os.path.join(self.tmp.name, "nope.md"), "--repo", self.tmp.name)
        self.assertEqual(p.returncode, 2)

    def test_default_kinds_emit_no_path_command_flag_rows(self):
        p = self.run_tool(self.mem, "--repo", self.tmp.name, "--json")
        kinds = {c["kind"] for f in json.loads(p.stdout)["files"] for c in f["citations"]}
        self.assertTrue(kinds <= {"symbol", "fileline"}, kinds)
        self.assertIn("symbol", kinds)

    def test_bad_kind_exits_two(self):
        p = self.run_tool(self.mem, "--repo", self.tmp.name, "--kinds", "bogus")
        self.assertEqual(p.returncode, 2)

    def test_bad_only_exits_two(self):
        p = self.run_tool(self.mem, "--repo", self.tmp.name, "--json", "--only", "gone")
        self.assertEqual(p.returncode, 2)


class TestScratchCopiesNeverShadowLiveFiles(unittest.TestCase):
    """A stale copy under `.claude/scratch/` must not stand in for the real file or symbol."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = cls.tmp.name
        plant_adaptation(r, source_extensions=[".cs"], symbol_stopwords=[])
        plant(r, "Game/Deep.cs", "class LiveType {}\n" + "// pad\n" * 9)
        plant(r, ".claude/scratch/old/Game/Deep.cs", "class StaleType {}\n")
        plant(r, ".claude/scratch/notes.md", "scratch note\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, text):
        doc = plant(self.tmp.name, "doc.md", text)
        p = subprocess.run([sys.executable, TOOL, doc, "--repo", self.tmp.name, "--kinds", "all", "--json"],
                           capture_output=True, text=True)
        self.assertIn(p.returncode, (0, 1), p.stderr)
        return {(c["kind"], c["text"]): c["status"]
                for f in json.loads(p.stdout)["files"] for c in f["citations"]}

    def test_bare_basename_line_resolves_against_the_real_file(self):
        self.assertEqual(self.rows("See `Deep.cs:8`.\n")[("fileline", "Deep.cs:8")], "found")

    def test_symbol_only_in_a_scratch_copy_is_missing(self):
        self.assertEqual(self.rows("Old `StaleType`, live `LiveType`.\n")[("symbol", "StaleType")], "missing")

    def test_full_scratch_path_still_resolves(self):
        self.assertEqual(self.rows("See `.claude/scratch/notes.md`.\n")[("path", ".claude/scratch/notes.md")], "found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
