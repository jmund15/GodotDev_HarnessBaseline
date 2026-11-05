#!/usr/bin/env python3
"""`review_ledger.py` records who reviewed what, scopes PR reviews, and reports uncovered (file, mandate) pairs.

    python3 .claude/tests/test_review_ledger.py

Every case builds a throwaway repo by writing git objects directly (a process spawn costs seconds on a
loaded machine), a planted `.claude` directory (mandate catalog and rule files) and a planted ledger, then
calls the tool's `main` in-process. The real tool never reaches the network, a real pull request or the real
ledger: `_gh_json` is replaced by a planted stand-in, and one subprocess case runs the CLI end to end
against a fake `gh` on PATH. The ported `scope`/`project` cases run against `review_ledger.py`. Each coverage
clause also runs against a tool copy with only that clause disabled, which must stop printing the pair.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
TOOL = TOOLS / "review_ledger.py"
LIVE_CLAUDE = TOOLS.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ledger_rows import ledger_row  # noqa: E402

GITLINK_OLD = "1" * 40
GITLINK_NEW = "2" * 40
BOGUS_SHA = "f" * 40

FAKE_GH_SOURCE = '''#!/usr/bin/env python3
"""A `gh pr view` stand-in that answers from the planted JSON state file."""
import json
import os
import sys


def main(argv):
    if argv[:2] != ["pr", "view"] or len(argv) < 3:
        print("fake gh: unsupported: " + " ".join(argv), file=sys.stderr)
        return 1
    state_path = os.environ["GH_FAKE_STATE"]
    with open(state_path, "r", encoding="utf-8", newline="") as handle:
        state = json.load(handle)
    record = state.get("prs", {}).get(argv[2])
    if record is None:
        print("fake gh: no such PR: " + argv[2], file=sys.stderr)
        return 1
    fields = argv[argv.index("--json") + 1].split(",") if "--json" in argv else []
    out = {}
    for field in fields:
        if field == "body":
            with open(record["body"], "rb") as handle:
                out["body"] = handle.read().decode("utf-8")
        else:
            out[field] = record.get(field)
    sys.stdout.buffer.write(json.dumps(out).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

FAKE_ESCAPE_SOURCE = '''#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_ESCAPE_OUT"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
'''

CODE_QUALITY = ("# Shared Code Quality Checklist\n\n## Compliance (C)\n\n- c1\n\n## Design (D)\n\n- d1\n\n"
                "## Semantics (S)\n\n- s1\n")
PR_READY = "# /pr_ready\n\n## Step 0\n\nzero\n\n### Step 1b: the other lenses\n\nrows\n\n## Step 2\n\ntwo\n"
LITMUS = "litmus one\n"

CATALOG = {
    "cq.C": {"home": "commands/checklists/code_quality.md#Compliance (C)", "interaction": False},
    "cq.D": {"home": "commands/checklists/code_quality.md#Design (D)", "interaction": False},
    "api-consume": {"home": "commands/pr_ready.md#Step 1b", "interaction": True},
    "parity": {"home": "commands/checklists/code_quality.md", "interaction": False},
    "worklog-status": {"home": "commands/pr_ready.md#Step 2", "interaction": False},
    "architecture-sweep": {"home": "commands/agents/session_audit_agents.md#sa-architecture-sweep",
                           "interaction": False},
}
# The fixture's lens registry: cq.D's lens preloads the litmus, so the litmus is cq.D's doctrine dependency.
# `{prefix}` is the live review registry's lens prefix (`lens.REGISTRIES`), which the project's adaptation sets.
REVIEW_AGENTS = ("# Review agents\n\n### {prefix}design-fixture — fixture lens\n\nmandates: cq.D\n"
                 "preload: rules/design_litmus.md\n\n```\nprompt\n```\n")
DOCTRINE = {"cq.D": ["rules/design_litmus.md"]}
# The fixture's `/session_audit` registry: one lens that always runs and one conditional lens.
SA_AGENTS = ("# Session audit agents\n\n### sa-fixture-code (code) — fixture lens\n\nmandates: cq.C, cq.D\n\n"
             "```\nprompt\n```\n\n### sa-architecture-sweep (sweep) — CONDITIONAL\n\nmandates: architecture-sweep\n"
             "admission: conditional\n\n```\nprompt\n```\n")
REVIEW_SCOPE = {"exclude_dirs": [".claude"], "exclude_suffixes": [".png", ".json"]}


_MODULES: dict = {}
_TEMPLATE: dict = {}


def record_line(head, base, scope, rng, date):
    return ("<!-- pr-review: head=%s base=%s scope=%s range=%s date=%s -->"
            % (head, base, scope, rng, date))


def load_module(path: Path):
    key = str(path)
    if key not in _MODULES:
        spec = importlib.util.spec_from_file_location("rl_%s_%d" % (re.sub(r"\W", "_", path.stem), len(_MODULES)), path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(TOOLS))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(TOOLS))
        _MODULES[key] = module
    return _MODULES[key]


def install_fake_gh(bin_dir: Path) -> None:
    script = bin_dir / "fake_gh.py"
    script.write_bytes(FAKE_GH_SOURCE.encode("utf-8"))
    if os.name == "nt":
        launcher = bin_dir / "gh.cmd"
        launcher.write_bytes(
            ("@echo off\r\n\"%s\" \"%s\" %%*\r\n" % (sys.executable, script)).encode("utf-8"))
    else:
        launcher = bin_dir / "gh"
        launcher.write_bytes(
            ("#!/bin/sh\nexec \"%s\" \"%s\" \"$@\"\n" % (sys.executable, script)).encode("utf-8"))
        os.chmod(launcher, 0o755)


def section_text(text: str, heading: str) -> str:
    """Independent re-statement of the rule-text slice: heading line to the next heading of the same or higher level."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        match = re.match(r"^(#+)\s+(.*)$", line)
        if match and match.group(2).strip().startswith(heading):
            level = len(match.group(1))
            end = len(lines)
            for index in range(start + 1, len(lines)):
                other = re.match(r"^(#+)\s", lines[index])
                if other and len(other.group(1)) <= level:
                    end = index
                    break
            return "\n".join(lines[start:end]).rstrip("\n")
    raise AssertionError("no heading " + heading)


def setUpModule():
    root = Path(tempfile.mkdtemp(prefix="rl_template_"))
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    shutil.rmtree(root / ".git" / "hooks", ignore_errors=True)
    _TEMPLATE["root"] = root


def tearDownModule():
    shutil.rmtree(_TEMPLATE["root"], ignore_errors=True)


class Result:
    def __init__(self, returncode, out, err):
        self.returncode = returncode
        self.stdout = out.encode("utf-8")
        self.stderr = err.encode("utf-8")


class Repo:
    """A git repository built by writing objects, so a fixture costs no process spawn."""

    def __init__(self, root: Path):
        shutil.copytree(_TEMPLATE["root"] / ".git", root / ".git")
        self.root = root
        self.files: dict = {}
        self.links: dict = {}
        self.branch = "main"
        self.tips: dict = {}
        self.snap: dict = {}
        self.parents: dict = {}
        self.seq: dict = {}
        self.clock = 1_700_000_000

    def _object(self, kind: str, data: bytes) -> str:
        body = b"%s %d\0" % (kind.encode(), len(data)) + data
        sha = hashlib.sha1(body).hexdigest()
        path = self.root / ".git" / "objects" / sha[:2] / sha[2:]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zlib.compress(body))
        return sha

    def _tree(self, entries: dict) -> str:
        nested: dict = {}
        for path, value in entries.items():
            head, _, rest = path.partition("/")
            if rest:
                nested.setdefault(head, {})[rest] = value
            else:
                nested[head] = value
        rows = []
        for name, value in nested.items():
            if isinstance(value, dict):
                rows.append((name + "/", b"40000 " + name.encode() + b"\0" + bytes.fromhex(self._tree(value))))
            else:
                mode, sha = value
                rows.append((name, mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha)))
        return self._object("tree", b"".join(row for _, row in sorted(rows, key=lambda r: r[0].encode())))

    @staticmethod
    def blob_sha(data: bytes) -> str:
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def write(self, rel: str, text: str):
        data = text.encode("utf-8")
        self.files[rel] = data
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def head(self):
        return self.tips.get(self.branch)

    def _point_head(self):
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/%s\n" % self.branch)

    def _set_tip(self, branch: str, sha: str):
        self.tips[branch] = sha
        ref = self.root / ".git" / "refs" / "heads" / branch
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(sha + "\n")

    def _commit_object(self, tree: str, parents: list, message: str) -> str:
        self.clock += 60
        lines = ["tree " + tree] + ["parent " + p for p in parents]
        ident = "t <t@t> %d +0000" % self.clock
        lines += ["author " + ident, "committer " + ident, "", message, ""]
        sha = self._object("commit", "\n".join(lines).encode("utf-8"))
        self.parents[sha] = list(parents)
        self.snap[sha] = (dict(self.files), dict(self.links))
        self.seq[sha] = len(self.seq)
        return sha

    def _entries(self, files: dict) -> dict:
        entries = {p: ("100644", self._object("blob", d)) for p, d in files.items()}
        entries.update({p: ("160000", s) for p, s in self.links.items()})
        return entries

    def commit(self, message: str, gitlink=None) -> str:
        if gitlink is not None:
            self.links["Jmodot"] = gitlink
        parents = [self.head()] if self.head() else []
        sha = self._commit_object(self._tree(self._entries(self.files)), parents, message)
        self._set_tip(self.branch, sha)
        self._point_head()
        return sha

    def _load(self, sha: str):
        files, links = self.snap[sha]
        for rel in list(self.files):
            if rel not in files:
                (self.root / rel).unlink(missing_ok=True)
        self.files, self.links = dict(files), dict(links)
        for rel, data in self.files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def checkout(self, name: str, create: bool = False):
        if create:
            self._set_tip(name, self.head())
        self.branch = name
        self._point_head()
        self._load(self.tips[name])

    def reset_hard(self, sha: str):
        self._set_tip(self.branch, sha)
        self._load(sha)

    def ancestors(self, sha: str) -> set:
        seen, stack = set(), [sha]
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self.parents.get(current, []))
        return seen

    def merge_base(self, left: str, right: str) -> str:
        common = self.ancestors(left) & self.ancestors(right)
        return max(common, key=lambda sha: self.seq[sha])

    def merge(self, other: str, message: str, resolved: dict | None = None) -> str:
        ours, theirs = self.head(), self.tips[other]
        base_files = self.snap[self.merge_base(ours, theirs)][0]
        our_files, their_files = self.snap[ours][0], self.snap[theirs][0]
        merged, conflicts = {}, []
        for rel in sorted(set(base_files) | set(our_files) | set(their_files)):
            b, o, t = base_files.get(rel), our_files.get(rel), their_files.get(rel)
            if o == t or t == b:
                value = o
            elif o == b:
                value = t
            else:
                conflicts.append(rel)
                value = o
            if value is not None:
                merged[rel] = value
        for rel in conflicts:
            if not resolved or rel not in resolved:
                raise AssertionError("unresolved conflict in " + rel)
        for rel, text in (resolved or {}).items():
            merged[rel] = text.encode("utf-8")
        self.files = merged
        sha = self._commit_object(self._tree(self._entries(merged)), [ours, theirs], message)
        self._set_tip(self.branch, sha)
        self._load(sha)
        return sha

    def set_upstream(self, remote_tip: str):
        git_dir = self.root / ".git"
        with (git_dir / "config").open("a", encoding="utf-8") as handle:
            handle.write('[remote "origin"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
                         '[branch "main"]\n\tremote = origin\n\tmerge = refs/heads/main\n' % (git_dir / "x").as_posix())
        ref = git_dir / "refs" / "remotes" / "origin" / "main"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(remote_tip + "\n")

    def unset_upstream(self):
        config = self.root / ".git" / "config"
        text = config.read_text(encoding="utf-8")
        config.write_text(re.sub(r'\[branch "main"\][^\[]*', "", text), encoding="utf-8")


class Fixture(unittest.TestCase):
    tool = TOOL
    record_cmd = "record"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state_path = self.root / "gh_state.json"
        self.state_path.write_bytes(b'{"prs": {}}')
        self.claude = self.root / "claude"
        self.ledger = self.root / "logs" / "review_ledger.jsonl"
        self.escape_log = self.root / "escape_log.jsonl"
        self._plant_claude()
        self.r = Repo(self.repo)
        self._write("f.txt", "base\n")
        self._commit("A")
        self.base_sha = self.head()

    def _plant_claude(self):
        review_agents = REVIEW_AGENTS.format(prefix=load_module(TOOLS / "lens.py").REGISTRIES["review"][1])
        for rel, text in (("commands/checklists/code_quality.md", CODE_QUALITY),
                          ("commands/pr_ready.md", PR_READY), ("rules/design_litmus.md", LITMUS),
                          ("commands/agents/review_agents.md", review_agents),
                          ("commands/agents/session_audit_agents.md", SA_AGENTS)):
            path = self.claude / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        adaptation = self.claude / "skills" / "project_subsystems" / "adaptation.json"
        adaptation.parent.mkdir(parents=True, exist_ok=True)
        adaptation.write_text(json.dumps({"review_mandates": CATALOG, "review_scope": REVIEW_SCOPE}),
                              encoding="utf-8")

    def env(self):
        env = {"GH_FAKE_STATE": str(self.state_path), "GIT_TERMINAL_PROMPT": "0",
               "GIT_CEILING_DIRECTORIES": str(self.root),
               "HARNESS_REVIEW_LEDGER": str(self.ledger), "HARNESS_REVIEW_CLAUDE_DIR": str(self.claude),
               "HARNESS_ESCAPE_LOG": str(self.escape_log),
               "HARNESS_REVIEW_SESSIONS": str(self.root / "sessions"),
               "CLAUDE_CONFIG_DIR": str(self.root / "config")}
        if getattr(self, "registry", None):
            env["HARNESS_MODEL_REGISTRY"] = self.registry
        return env

    def plant_registry(self, *supplementary_ids):
        """The live registry with `supplementary: ["architect"]` on each named row id, until S5a ships it."""
        data = copy.deepcopy(load_module(TOOLS / "model_registry.py").load())
        for entry in data["models"]:
            if entry["id"] in supplementary_ids:
                entry["supplementary"] = ["architect"]
        path = self.root / "registry.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.registry = str(path)

    def _write(self, rel, text):
        self.r.write(rel, text)

    def _commit(self, message, gitlink=None):
        return self.r.commit(message, gitlink)

    def head(self):
        return self.r.head()

    def blob(self, rel):
        return Repo.blob_sha((self.repo / rel).read_bytes())

    def checkout(self, name):
        self.r.checkout(name)

    def branch(self, name="feat"):
        self.r.checkout(name, create=True)

    def merge(self, other, message, resolved=None):
        return self.r.merge(other, message, resolved)

    def parent(self, sha=None):
        return self.r.parents[sha or self.head()][0]

    def plant(self, head, body, pr=1, state="OPEN"):
        body_path = self.root / ("body_%d.md" % pr)
        body_path.write_bytes(body.encode("utf-8"))
        self.state_path.write_bytes(json.dumps({
            "prs": {str(pr): {"state": state, "headRefOid": head, "body": str(body_path)}}
        }).encode("utf-8"))

    def _fake_gh_json(self, module):
        def fake(repo, args):
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            record = state.get("prs", {}).get(args[2])
            if record is None:
                raise module.ToolError("gh %s failed: no such PR: %s" % (" ".join(args), args[2]))
            out = {}
            for field in args[args.index("--json") + 1].split(","):
                if field == "body":
                    out["body"] = Path(record["body"]).read_bytes().decode("utf-8")
                else:
                    out[field] = record.get(field)
            return out
        return fake

    def run_tool(self, *args, tool=None, extra_env=None, attrs=None):
        module = load_module(tool or self.tool)
        args = list(args)
        if args and args[0] == "record" and self.record_cmd != "record":
            args[0] = self.record_cmd
        # The live `_pick` probes provider capacity through ~11 subprocesses (2.6 s a plan); only the seam cases below use it.
        overrides = dict({"_pick": NO_PICK["_pick"]}, **(attrs or {}), _gh_json=self._fake_gh_json(module))
        originals = {name: getattr(module, name) for name in overrides}
        for name, value in overrides.items():
            setattr(module, name, value)
        saved = dict(os.environ)
        cwd = os.getcwd()
        out, err = io.StringIO(), io.StringIO()
        for name in ("CLAUDE_CODE_SESSION_ID", "HARNESS_REVIEW_CANDIDATE_KEYS", "HARNESS_ESCAPE_LOG_TOOL"):
            os.environ.pop(name, None)
        os.environ.update(dict(self.env(), **(extra_env or {})))
        os.chdir(self.repo)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = module.main(args)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(saved)
            for name, value in originals.items():
                setattr(module, name, value)
        return Result(code, out.getvalue(), err.getvalue())

    def scope(self, pr=1):
        result = self.run_tool("scope", str(pr), "--base-ref", "main")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout.decode("utf-8"))

    def write_scope(self, scope, name="scope.json"):
        path = self.root / name
        path.write_text(json.dumps(scope), encoding="utf-8")
        return path

    def full_scope(self, head, own=(), files=(), lines=0):
        return {"pr": 1, "mode": "full", "reason": "no record", "last_head": None,
                "head": head, "base": self.base_sha,
                "range": "%s..%s" % (self.base_sha, head), "own_commits": list(own),
                "merge_resolutions": [], "files": list(files), "lines": lines,
                "excluded": [], "jmodot": None, "gitlinks": {}}


def link_of(scope):
    if "gitlinks" in scope:
        return (scope["gitlinks"] or {}).get("Jmodot")
    return scope.get("jmodot")


class ScopeCases:
    def test_delta_over_two_own_commits(self):
        recorded = self.head()
        self.branch()
        self._write("one.txt", "one\n")
        self._commit("B")
        self._write("two.txt", "two\n")
        self._commit("C")
        head = self.head()
        self.plant(head, record_line(recorded, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, recorded), "2026-09-01") + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["last_head"], recorded)
        self.assertEqual(scope["head"], head)
        self.assertEqual(scope["base"], self.base_sha)
        self.assertEqual(scope["range"], "%s..%s" % (recorded, head))
        self.assertEqual(scope["own_commits"], [self.parent(), head])
        self.assertEqual(scope["merge_resolutions"], [])
        self.assertEqual(scope["files"], ["one.txt", "two.txt"])
        self.assertEqual(scope["lines"], 2)
        self.assertIsNone(link_of(scope))
        self.assertEqual(scope["excluded"], [])
        self.assertEqual(scope["pr"], 1)

    def test_merge_from_main_is_excluded(self):
        self.branch()
        self._write("feat.txt", "b\n")
        self._commit("B")
        recorded = self.head()
        self._write("feat.txt", "b\nc\n")
        self._commit("C")
        own = self.head()
        self.checkout("main")
        self._write("main_only.txt", "m\n")
        self._commit("A2")
        self.checkout("feat")
        self.merge("main", "M")
        head = self.head()
        self.plant(head, record_line(recorded, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, recorded), "2026-09-01") + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["own_commits"], [own])
        self.assertEqual(scope["merge_resolutions"], [])
        self.assertIn("feat.txt", scope["files"])
        self.assertNotIn("main_only.txt", scope["files"])

    def test_full_scope_excludes_work_merged_from_main(self):
        self.branch()
        self._write("feat.txt", "b\n")
        self._commit("B")
        own = self.head()
        self._write("feat.txt", "c\n")
        self._commit("C revert-ish")
        own2 = self.head()
        self.checkout("main")
        self._write("main_only.txt", "m\n")
        self._commit("A2")
        self.checkout("feat")
        self.merge("main", "M")
        self.plant(self.head(), "No record.\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "full")
        self.assertEqual(scope["own_commits"], [own, own2])
        self.assertEqual(scope["files"], ["feat.txt"])
        self.assertEqual(scope["lines"], 1)

    def test_merge_conflict_resolution_is_included(self):
        self.branch()
        self._write("f.txt", "feat\n")
        self._commit("B")
        recorded = self.head()
        self.checkout("main")
        self._write("f.txt", "main\n")
        self._commit("A2")
        self.checkout("feat")
        head = self.merge("main", "M", resolved={"f.txt": "resolved\n"})
        self.plant(head, record_line(recorded, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, recorded), "2026-09-01") + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["own_commits"], [])
        self.assertEqual(scope["merge_resolutions"], [head])
        self.assertEqual(scope["files"], ["f.txt"])
        self.assertEqual(scope["lines"], 6)

    def test_rebased_branch_falls_back_to_full(self):
        self.branch()
        self._write("feat.txt", "first\n")
        self._commit("B moved")
        recorded = self.head()
        self.r.reset_hard(self.base_sha)
        self._write("feat.txt", "rewritten\n")
        self._commit("B rewritten")
        head = self.head()
        self.plant(head, record_line(recorded, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, recorded), "2026-09-01") + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "full")
        self.assertEqual(scope["reason"], "recorded head not an ancestor (rebase or force-push)")
        self.assertIsNone(scope["last_head"])
        self.assertEqual(scope["range"], "%s..%s" % (self.base_sha, head))
        self.assertEqual(scope["own_commits"], [head])

    def test_no_record_falls_back_to_full(self):
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B")
        head = self.head()
        self.plant(head, "Just a body with no record.\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "full")
        self.assertEqual(scope["reason"], "no record")
        self.assertIsNone(scope["last_head"])
        self.assertEqual(scope["own_commits"], [head])
        self.assertEqual(scope["files"], ["feat.txt"])

    def test_malformed_newest_record_falls_back_to_full(self):
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B")
        head = self.head()
        self.plant(head, "Body.\n<!-- pr-review: head=abc base=def -->\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "full")
        self.assertEqual(scope["reason"], "unparseable record")
        self.assertIsNone(scope["last_head"])

    def test_two_records_newest_wins(self):
        first = self.head()
        self.branch()
        self._write("feat.txt", "b\n")
        self._commit("B")
        second = self.head()
        self._write("feat.txt", "b\nc\n")
        self._commit("C")
        head = self.head()
        self.plant(head, "\n".join([
            record_line(first, self.base_sha, "full", "%s..%s" % (self.base_sha, first), "2026-09-01"),
            record_line(second, self.base_sha, "delta", "%s..%s" % (first, second), "2026-09-02"),
        ]) + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["last_head"], second)
        self.assertEqual(scope["own_commits"], [head])

    def test_record_inside_a_fence_is_ignored(self):
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B")
        head = self.head()
        fenced = record_line(BOGUS_SHA, BOGUS_SHA, "delta",
                             "%s..%s" % (BOGUS_SHA, BOGUS_SHA), "2026-09-03")
        self.plant(head, "\n".join([
            record_line(self.base_sha, self.base_sha, "full",
                        "%s..%s" % (self.base_sha, self.base_sha), "2026-09-01"),
            "```markdown",
            fenced,
            "```",
        ]) + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["last_head"], self.base_sha)
        self.assertEqual(scope["own_commits"], [head])

    def test_gitlink_change_is_reported(self):
        self._commit("A2 gitlink", gitlink=GITLINK_OLD)
        recorded = self.head()
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B gitlink", gitlink=GITLINK_NEW)
        head = self.head()
        self.plant(head, record_line(recorded, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, recorded), "2026-09-01") + "\n")

        scope = self.scope()

        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(link_of(scope), {"old": GITLINK_OLD, "new": GITLINK_NEW})

    def test_excluded_paths_never_appear_in_files_or_lines(self):
        self.branch()
        self._write(".claude/x.md", "claude\n")
        self._write("a.png", "png\n")
        self._write("b.json", "{}\n")
        self._write("keep.txt", "keep\n")
        self._commit("B")
        head = self.head()
        self.plant(head, "No record here.\n")

        scope = self.scope()

        self.assertEqual(scope["files"], ["keep.txt"])
        self.assertEqual(scope["excluded"], [".claude/x.md", "a.png", "b.json"])
        self.assertEqual(scope["lines"], 1)

    def test_merged_pr_exits_2(self):
        head = self.head()
        self.plant(head, "Body.\n", state="MERGED")

        result = self.run_tool("scope", "1", "--base-ref", "main")

        self.assertEqual(result.returncode, 2)
        self.assertIn("MERGED", result.stderr.decode())

    def test_bad_arguments_exit_2_with_usage(self):
        for args in (["scope"], ["scope", "abc"], ["scope", "1", "--base-ref"],
                     ["record", "1", "--scope-json", "x.json"]):
            with self.subTest(args=args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn("Usage:", result.stderr.decode())


class RecordCases:
    def test_full_round_trip_preserves_the_body(self):
        head = self.head()
        body = "Intro line.\n\n- a bullet\n"
        self.plant(head, body)
        scope_path = self.write_scope(self.full_scope(head))
        out = self.root / "out.md"

        result = self.run_tool("record", "1", "--scope-json", str(scope_path),
                               "--out", str(out), "--date", "2026-09-28")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        written = out.read_bytes()
        self.assertTrue(written.startswith(body.encode("utf-8")))
        text = written.decode("utf-8")
        self.assertIn("## Review record", text)
        self.assertIn(record_line(head, self.base_sha, "full",
                                  "%s..%s" % (self.base_sha, head), "2026-09-28"), text)
        self.assertIn("Reviewed `%s` on 2026-09-28: full" % head[:7], text)

    def test_delta_round_trip_carries_the_own_commit_count(self):
        first = self.head()
        self.branch()
        self._write("one.txt", "one\n")
        self._commit("B")
        self._write("two.txt", "two\n")
        self._commit("C")
        head = self.head()
        self.plant(head, "Body.\n")
        scope = {"pr": 1, "mode": "delta", "reason": "recorded head is an ancestor",
                 "last_head": first, "head": head, "base": self.base_sha,
                 "range": "%s..%s" % (first, head),
                 "own_commits": [self.parent(), head],
                 "merge_resolutions": [], "files": ["one.txt", "two.txt"], "lines": 2,
                 "excluded": [], "jmodot": None, "gitlinks": {}}
        scope_path = self.write_scope(scope)
        out = self.root / "out.md"

        result = self.run_tool("record", "1", "--scope-json", str(scope_path),
                               "--out", str(out), "--date", "2026-09-28")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        text = out.read_bytes().decode("utf-8")
        self.assertIn(record_line(head, self.base_sha, "delta",
                                  "%s..%s" % (first, head), "2026-09-28"), text)
        self.assertIn("Reviewed `%s` on 2026-09-28: delta (2 own commits since `%s`)"
                      % (head[:7], first[:7]), text)

    def test_crlf_body_keeps_its_line_endings(self):
        head = self.head()
        body = "Intro line.\r\n\r\n- a bullet\r\n"
        self.plant(head, body)
        scope_path = self.write_scope(self.full_scope(head))
        out = self.root / "out.md"

        result = self.run_tool("record", "1", "--scope-json", str(scope_path),
                               "--out", str(out), "--date", "2026-09-28")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        written = out.read_bytes()
        self.assertTrue(written.startswith(body.encode("utf-8")))
        self.assertNotIn(b"\r\r\n", written)
        self.assertIn(b"## Review record\r\n", written)
        self.assertIn(("Reviewed `%s` on 2026-09-28: full\r\n" % head[:7]).encode("utf-8"), written)

    def test_existing_section_is_appended_to_not_duplicated(self):
        first = self.head()
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B")
        head = self.head()
        old = record_line(first, self.base_sha, "full",
                          "%s..%s" % (self.base_sha, first), "2026-09-01")
        body = "Intro.\n\n## Review record\n\n%s\n\nReviewed `%s` on 2026-09-01: full\n\n## Findings\n\n- one\n" % (
            old, first[:7])
        self.plant(head, body)
        scope = self.full_scope(head)
        scope["mode"] = "delta"
        scope["last_head"] = first
        scope["own_commits"] = [head]
        scope["range"] = "%s..%s" % (first, head)
        scope_path = self.write_scope(scope)
        out = self.root / "out.md"

        result = self.run_tool("record", "1", "--scope-json", str(scope_path),
                               "--out", str(out), "--date", "2026-09-28")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        text = out.read_bytes().decode("utf-8")
        self.assertEqual(text.count("## Review record"), 1)
        self.assertIn(old, text)
        self.assertIn(record_line(head, self.base_sha, "delta",
                                  "%s..%s" % (first, head), "2026-09-28"), text)
        self.assertIn("## Findings\n\n- one\n", text)
        self.assertTrue(text.startswith("Intro.\n\n## Review record\n\n%s\n\nReviewed `%s` on 2026-09-01: full\n"
                                        % (old, first[:7])))

    def test_scope_json_mismatch_and_malformed_inputs_exit_2(self):
        head = self.head()
        self.plant(head, "Body.\n")
        out = self.root / "out.md"

        wrong_pr = self.full_scope(head)
        wrong_pr["pr"] = 999
        missing = self.full_scope(head)
        missing.pop("last_head")

        cases = {
            "mismatched pr": self.write_scope(wrong_pr, "wrong.json"),
            "missing field": self.write_scope(missing, "missing.json"),
        }
        for label, path in cases.items():
            with self.subTest(case=label):
                result = self.run_tool("record", "1", "--scope-json", str(path),
                                       "--out", str(out), "--date", "2026-09-28")
                self.assertEqual(result.returncode, 2, result.stderr.decode())

        broken = self.root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        result = self.run_tool("record", "1", "--scope-json", str(broken),
                               "--out", str(out), "--date", "2026-09-28")
        self.assertEqual(result.returncode, 2, result.stderr.decode())

    def test_record_on_a_merged_pr_exits_2(self):
        head = self.head()
        self.plant(head, "Body.\n", state="CLOSED")
        scope_path = self.write_scope(self.full_scope(head))
        out = self.root / "out.md"

        result = self.run_tool("record", "1", "--scope-json", str(scope_path),
                               "--out", str(out), "--date", "2026-09-28")

        self.assertEqual(result.returncode, 2)
        self.assertIn("CLOSED", result.stderr.decode())
        self.assertFalse(out.exists())


class LedgerScopeTests(ScopeCases, Fixture):
    pass


class LedgerProjectTests(RecordCases, Fixture):
    record_cmd = "project"


class CliSmokeTests(Fixture):
    def test_the_cli_entry_runs_scope_and_rejects_no_arguments(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        install_fake_gh(bin_dir)
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B")
        self.plant(self.head(), "no record\n")
        env = dict(os.environ, **self.env())
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        ok = subprocess.run([sys.executable, str(TOOL), "scope", "1", "--base-ref", "main"], cwd=self.repo, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(ok.returncode, 0, ok.stderr.decode())
        self.assertEqual(json.loads(ok.stdout)["files"], ["feat.txt"])
        bad = subprocess.run([sys.executable, str(TOOL)], cwd=self.repo, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("Usage:", bad.stderr.decode())


class ScopeExtrasTests(Fixture):
    def _branch_with_commit(self, message="B"):
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit(message)
        return self.head()

    def test_source_is_none_without_any_record(self):
        head = self._branch_with_commit()
        self.plant(head, "no record\n")
        scope = self.scope()
        self.assertEqual(scope["source"], "none")
        self.assertNotIn("triage", scope)
        self.assertEqual(scope["gitlinks"], {})

    def test_source_is_pr_body_when_only_the_body_holds_a_record(self):
        first = self.head()
        head = self._branch_with_commit()
        self.plant(head, record_line(first, self.base_sha, "full",
                                     "%s..%s" % (self.base_sha, first), "2026-09-01") + "\n")
        self.assertEqual(self.scope()["source"], "pr-body")

    def test_a_ledger_row_for_the_pr_outranks_the_body(self):
        first = self.head()
        head = self._branch_with_commit()
        self.plant(head, "no record\n")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps({
            "v": 1, "id": "run:s:cq.C", "ts": "2026-09-01T00:00:00Z", "stage": "pre-merge",
            "subject": "pr:1", "mandate": "cq.C", "head": first, "outcome": "clean", "files": {}}) + "\n",
            encoding="utf-8")
        scope = self.scope()
        self.assertEqual(scope["source"], "ledger")
        self.assertEqual(scope["mode"], "delta")
        self.assertEqual(scope["last_head"], first)

    def _plant_pr_rows(self, *rows):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text("".join(json.dumps(dict({
            "v": 1, "ts": "2026-09-01T00:00:00Z", "stage": "pre-merge", "subject": "pr:1", "files": {},
            "id": "run:%s:%s:%d" % (r["head"][:4], r["mandate"], i)}, **r)) + "\n" for i, r in enumerate(rows)),
            encoding="utf-8")

    def test_a_round_with_a_failed_mandate_does_not_move_the_cursor_until_it_is_retried(self):
        first = self.head()
        second = self._branch_with_commit()
        head = self._branch_with_commit("C")
        self.plant(head, "no record\n")
        done = [{"head": first, "mandate": "cq.C", "outcome": "clean"},
                {"head": first, "mandate": "cq.D", "outcome": "clean"}]
        partial = [{"head": second, "mandate": "cq.C", "outcome": "clean"},
                   {"head": second, "mandate": "cq.D", "outcome": "failed"}]
        self._plant_pr_rows(*done, *partial)
        self.assertEqual(self.scope()["last_head"], first)
        self._plant_pr_rows(*done, *partial, {"head": second, "mandate": "cq.D", "outcome": "clean"})
        self.assertEqual(self.scope()["last_head"], second)

    def test_a_later_scope_keeps_the_whole_pr_as_its_subject_and_the_delta_only_as_presentation(self):
        self.branch()
        self._write("a.txt", "a\n")
        self._write("b.txt", "b\n")
        self._commit("B")
        first = self.head()
        self.plant(first, "no record\n")
        self._plant_pr_rows({"head": first, "mandate": "cq.C", "outcome": "clean"})
        again = self.scope()
        self.assertEqual((again["mode"], again["last_head"]), ("delta", first))
        self.assertEqual(again["files"], ["a.txt", "b.txt"], "a partial round at a head never empties the subject")
        self.assertEqual(again["delta_files"], [])
        self._write("c.txt", "c\n")
        self._commit("C")
        self.plant(self.head(), "no record\n")
        later = self.scope()
        self.assertEqual(later["files"], ["a.txt", "b.txt", "c.txt"])
        self.assertEqual(later["delta_files"], ["c.txt"])
        self.assertEqual(later["lines"], 1)

    def test_triage_trailer_is_not_a_scope_projection(self):
        self.branch()
        self._write("feat.txt", "x\n")
        self._commit("B\n\nTriage: reach=shared reversibility=residue design=settled grounding=verified "
                     "oracle=gate size=one-context")
        self.plant(self.head(), "no record\n")
        scope = self.scope()
        self.assertNotIn("triage", scope)
        self.assertEqual(scope["files"], ["feat.txt"])

    def test_project_needs_every_flag(self):
        head = self.head()
        self.plant(head, "Body.\n")
        scope_path = self.write_scope(self.full_scope(head))
        result = self.run_tool("project", "1", "--scope-json", str(scope_path))
        self.assertEqual(result.returncode, 2)


class LedgerFixture(Fixture):
    """A repo with one reviewed file and helpers to plant ledger rows and read `uncovered` output."""

    def setUp(self):
        super().setUp()
        self._write("src/a.cs", "one\n")
        self._commit("B")
        self.ledger.parent.mkdir(parents=True, exist_ok=True)

    def reset_ledger(self):
        self.ledger.unlink(missing_ok=True)

    def rule_hash(self, key):
        spec = CATALOG[key]
        rel, _, heading = spec["home"].partition("#")
        text = (self.claude / rel).read_text(encoding="utf-8")
        section = section_text(text, heading) if heading else text.rstrip("\n")
        digest = hashlib.sha256(section.encode("utf-8"))
        for rel in DOCTRINE.get(key, []):
            digest.update(b"\0" + (self.claude / rel).read_bytes())
        return "sha256:" + digest.hexdigest()

    def rule_blob(self, key):
        """The git blob id of the exact rule text `rule_hash` digests: the home section, then each preload."""
        spec = CATALOG[key]
        rel, _, heading = spec["home"].partition("#")
        text = (self.claude / rel).read_text(encoding="utf-8")
        data = (section_text(text, heading) if heading else text.rstrip("\n")).encode("utf-8")
        for rel in DOCTRINE.get(key, []):
            data += b"\0" + (self.claude / rel).read_bytes()
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def merge_base(self):
        return self.r.merge_base(self.head(), self.r.tips["main"])

    def row(self, mandate="cq.C", **over):
        fields = dict(head=self.head(), files={"src/a.cs": self.blob("src/a.cs")}, mergeBase=self.merge_base())
        fields.update(over)
        return ledger_row(mandate, self.rule_hash(mandate), **fields)

    def plant_rows(self, *rows, raw=()):
        lines = [json.dumps(r) for r in rows] + list(raw)
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def uncovered(self, files=("src/a.cs",), mandates="cq.C", *extra, tool=None, roster=None):
        """`uncovered` under `roster` (default: one executor/high seat per catalog key, each carrying one)."""
        seats = roster if roster is not None else [self.seat(key, (key,)) for key in CATALOG]
        path = self.root / "uncovered_roster.json"
        path.write_text(json.dumps(seats), encoding="utf-8")
        return self.run_tool("uncovered", "--files", *files, "--mandates", mandates,
                             "--base-ref", "main", "--roster", str(path), *extra, tool=tool)

    def pairs(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return [line.strip() for line in result.stdout.decode().splitlines() if line.strip()]

    def pair(self, mandate="cq.C", path="src/a.cs"):
        return "%s\t%s" % (path, mandate)

    def plan(self, roster, *extra, stage="candidate", subject="session:s1", files=("src/a.cs",), attrs=None):
        roster_path = self.root / "roster.json"
        roster_path.write_text(json.dumps(roster), encoding="utf-8")
        args = ["plan", "--stage", stage, "--subject", subject, "--roster", str(roster_path),
                "--base-ref", "main"]
        if files:
            args += ["--files", *files]
        return self.run_tool(*args, *extra, attrs=attrs)

    def kept(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout.decode())

    def seat(self, key="seat", mandates=("cq.C",), tier="executor", effort="high"):
        return {"key": key, "mandates": list(mandates), "tier": tier, "effort": effort}

    def record(self, *args, stage="candidate", subject="session:s1", extra_env=None):
        return self.run_tool("record", "--stage", stage, "--subject", subject, "--base-ref", "main", *args,
                             extra_env=extra_env)

    def rows(self):
        if not self.ledger.is_file():
            return []
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]



class OwnerWaiverTests(LedgerFixture):
    """The owner's word that a PR head is reviewed (a review the ledger never saw) is recorded once and
    holds: later runs plan only what changed since the waived head."""

    def _waive(self, head, date="2026-10-11"):
        self.plant(head, "Body.\n")
        scope = self.scope()
        out = self.root / "waived.md"
        result = self.run_tool("project", "1", "--scope-json", str(self.write_scope(scope)),
                               "--out", str(out), "--date", date, "--waive")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return scope, out.read_bytes().decode("utf-8")

    def _plan_pr(self):
        scope_path = self.write_scope(self.scope(), "now.json")
        return self.kept(self.plan([self.seat()], "--scope-json", str(scope_path), stage="pre-merge",
                                   subject="pr:1", files=(), attrs=NO_PICK))

    def _feature(self, rel="feat.txt"):
        self.branch()
        self._write(rel, "x\n")
        self._commit("F")
        return self.head()

    def test_project_waive_writes_a_waived_record(self):
        head = self._feature()
        scope, text = self._waive(head)
        self.assertIn(record_line(head, scope["base"], "waived", scope["range"], "2026-10-11"), text)
        self.assertIn("Owner waived review of `%s` on 2026-10-11" % head[:7], text)

    def test_an_unchanged_waived_head_plans_no_seat(self):
        head = self._feature()
        _, text = self._waive(head)
        self.plant(head, text)
        scope = self.scope()
        self.assertEqual((scope["mode"], scope["last_head"], scope.get("waived")), ("delta", head, True))
        self.assertEqual(self._plan_pr()["roster"], [])

    def test_a_commit_after_the_waiver_is_the_only_file_planned(self):
        head = self._feature()
        _, text = self._waive(head)
        self._write("later.txt", "y\n")
        self._commit("G")
        self.plant(self.head(), text)
        roster = self._plan_pr()["roster"]
        self.assertEqual([seat["uncoveredFiles"] for seat in roster], [["later.txt"]])

    def test_a_waiver_after_a_ledger_round_outranks_the_ledger(self):
        first = self._feature()
        self.plant_rows({"v": 1, "id": "run:s:cq.C", "ts": "2026-09-01T00:00:00Z", "stage": "pre-merge",
                         "subject": "pr:1", "mandate": "cq.C", "head": first, "outcome": "clean", "files": {}})
        self._write("second.txt", "z\n")
        self._commit("G")
        head = self.head()
        _, text = self._waive(head)
        self.plant(head, text)
        scope = self.scope()
        self.assertEqual((scope["last_head"], scope["source"], scope.get("waived")), (head, "pr-body", True))


def write_mutant(tool_path: Path, clause: int, out_dir: Path) -> Path:
    """A copy of the tool whose coverage clause `clause` always holds."""
    source = tool_path.read_text(encoding="utf-8")
    override = ("CLAUSES = tuple((n, (lambda *a, **k: True) if n == %d else fn) for n, fn in CLAUSES)\n\n" % clause)
    anchor = "TOOLS_DIR = Path(__file__).resolve().parent\n"
    if anchor not in source:
        raise AssertionError("tool has no TOOLS_DIR anchor for a mutant")
    source = source.replace(anchor, "TOOLS_DIR = Path(%r)\n" % str(TOOLS))
    marker = 'if __name__ == "__main__":'
    if marker not in source:
        raise AssertionError("tool has no main guard to anchor a mutant")
    path = out_dir / ("mutant_clause_%d.py" % clause)
    path.write_text(source.replace(marker, override + marker), encoding="utf-8")
    return path


NO_PICK = {"_pick": lambda tier: {"transport": None, "room": None, "payOptions": [], "ranked": []}}


class DerivedCoverageTests(LedgerFixture):
    def chain(self, root=True, panel=False):
        old = self.blob("src/a.cs")
        rows = [self.row(reviewer="panel")] if root else []
        self._write("src/a.cs", "fixed\n")
        derived = self.row(reviewer="panel" if panel else "inline:fold", effort="medium",
                           seatMandates=["cq.C", "cq.D"], **{"from": {"src/a.cs": old}})
        self.plant_rows(*rows, derived)
        return old

    def test_inline_chain_and_all_three_readers_agree(self):
        self.chain()
        self.assertEqual(self.pairs(self.uncovered()), [])
        self.assertEqual(self.kept(self.plan([self.seat()]))["roster"], [])
        module = load_module(TOOL)
        from unittest.mock import patch
        with patch.dict(os.environ, self.env()):
            self.assertEqual(module.owed_staged(self.repo, {"src/a.cs": self.blob("src/a.cs")},
                                               ["cq.C"], "main", [self.seat()]), [])

    def test_uncovered_predecessor_cannot_cover(self):
        self.chain(root=False)
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_derived_panel_relaxes_effort_and_load_not_root_depth(self):
        self.chain(panel=True)
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_panel_root_stays_depth_unchecked_without_roster(self):
        self.chain()
        result = self.run_tool("uncovered", "--files", "src/a.cs", "--mandates", "cq.C", "--base-ref", "main")
        self.assertEqual(self.pairs(result), [self.pair() + "\tdepth-unchecked"])

    def test_reviewed_blob_requires_one_common_covered_predecessor(self):
        first = self.blob("src/a.cs")
        self.plant_rows(self.row("cq.C"), self.row("cq.D"))
        self._write("src/a.cs", "current delta\n")
        module = load_module(TOOL)
        from unittest.mock import patch
        with patch.dict(os.environ, self.env()):
            cover = module.cover_at(self.repo, {}, "main")
            seats = {m: module._depth_seats([self.seat(m, (m,))], m) for m in ("cq.C", "cq.D")}
            self.assertEqual(module.reviewed_blob(cover, "src/a.cs", ["cq.C", "cq.D"], seats), first)
            self.assertIsNone(module.reviewed_blob(cover, "src/a.cs", ["cq.C", "parity"], seats))

    def test_revert_cycle_terminates_without_root(self):
        first = self.blob("src/a.cs")
        self._write("src/a.cs", "second\n")
        second = self.blob("src/a.cs")
        self.plant_rows(self.row(files={"src/a.cs": first}, **{"from": {"src/a.cs": second}}),
                        self.row(files={"src/a.cs": second}, **{"from": {"src/a.cs": first}}))
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])


class MandateKindTests(unittest.TestCase):
    """`uncovered_pairs` asks a file only the mandates that judge its kind."""

    class Open:
        """A cover where nothing is reviewed."""
        class ctx:
            hash_files = staticmethod(lambda files: None)

        pair_state = staticmethod(lambda mandate, path, seats: "uncovered")
        subject_kind = "session"
        request_rigor = "normal"

    def owed(self, path, mandate, rigor="normal"):
        cover = type("Open", (self.Open,), {"request_rigor": rigor})
        return [m for _p, m in load_module(TOOL).uncovered_pairs(cover, [path], [mandate])]

    def test_prose_harness_scope_owes_only_the_harness_lens_and_thorough_owes_all(self):
        for path in (".claude/rules/x.md", "CLAUDE.md", ".claude/baseline.lock.json"):
            self.assertEqual(self.owed(path, "architecture-sweep"), [], path)
            self.assertEqual(self.owed(path, "architecture-sweep", rigor="thorough"), ["architecture-sweep"], path)
        self.assertEqual(self.owed(".claude/hooks/a.py", "architecture-sweep"), ["architecture-sweep"])

    def test_markdown_owes_no_code_or_test_quality_mandate(self):
        self.assertEqual(self.owed("docs/a.md", "cq.R"), [])
        self.assertEqual(self.owed("docs/a.md", "tq"), [])

    def test_non_harness_file_owes_no_harness_quality(self):
        self.assertEqual(self.owed("src/a.cs", "harness-quality"), [])

    def test_harness_code_file_owes_both(self):
        self.assertEqual(self.owed(".claude/tools/x.py", "cq.R"), ["cq.R"])
        self.assertEqual(self.owed(".claude/tools/x.py", "harness-quality"), ["harness-quality"])

    def test_harness_markdown_and_root_doctrine_owe_harness_quality_only(self):
        self.assertEqual(self.owed(".claude/rules/x.md", "harness-quality"), ["harness-quality"])
        self.assertEqual(self.owed("CLAUDE.md", "harness-quality"), ["harness-quality"])

    def test_data_files_keep_every_mandate(self):
        self.assertEqual(self.owed("data/a.json", "cq.R"), ["cq.R"])


class CoverageClauseTests(LedgerFixture):
    def test_covering_row_prints_nothing(self):
        self.plant_rows(self.row())
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_no_row_prints_the_pair(self):
        self.plant_rows(self.row("cq.D"))
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_missing_ledger_prints_every_pair_and_says_why(self):
        result = self.uncovered(mandates="cq.C,cq.D")
        self.assertEqual(sorted(self.pairs(result)), sorted([self.pair("cq.C"), self.pair("cq.D")]))
        self.assertIn("ledger", result.stderr.decode())

    def test_corrupt_line_is_skipped_counted_and_never_covers(self):
        self.plant_rows(raw=['{"not": "a row"', "garbage"])
        result = self.uncovered()
        self.assertEqual(self.pairs(result), [self.pair()])
        self.assertIn("2 corrupt", result.stderr.decode())
        self.plant_rows(self.row(), raw=["garbage"])
        result = self.uncovered()
        self.assertEqual(self.pairs(result), [])
        self.assertIn("1 corrupt", result.stderr.decode())

    def test_clause1_rule_text_edit_drops_coverage_and_other_sections_do_not(self):
        self.plant_rows(self.row())
        design = self.claude / "commands/checklists/code_quality.md"
        text = design.read_text(encoding="utf-8")
        design.write_text(text.replace("- d1", "- d1 edited"), encoding="utf-8")
        self.assertEqual(self.pairs(self.uncovered()), [], "an edit under Design must not drop cq.C")
        design.write_text(text.replace("- c1", "- c1 edited"), encoding="utf-8")
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_clause1_an_edit_to_a_file_the_mandates_lens_preloads_drops_coverage(self):
        self.plant_rows(self.row("cq.D"))
        self.assertEqual(self.pairs(self.uncovered(mandates="cq.D")), [])
        (self.claude / "rules/design_litmus.md").write_text("litmus two\n", encoding="utf-8")
        self.assertEqual(self.pairs(self.uncovered(mandates="cq.D")), [self.pair("cq.D")])

    def _edit_cq_c_rule(self):
        design = self.claude / "commands/checklists/code_quality.md"
        design.write_text(design.read_text(encoding="utf-8").replace("- c1", "- c1 edited"), encoding="utf-8")

    def test_rule_delta_names_the_old_rule_blob_when_only_clause1_fails(self):
        old_blob = self.rule_blob("cq.C")
        self.plant_rows(self.row(ruleBlob=old_blob))
        self.assertEqual(self.pairs(self.uncovered()), [])
        self._edit_cq_c_rule()
        self.assertEqual(self.pairs(self.uncovered()), [self.pair() + "\trule-after:" + old_blob])

    def test_rule_delta_row_covers_the_pair_under_the_new_rule(self):
        old_blob = self.rule_blob("cq.C")
        old_row = self.row(ruleBlob=old_blob)
        self._edit_cq_c_rule()
        delta = self.row(id="delta", ruleBlob=self.rule_blob("cq.C"), ruleFrom=old_blob)
        self.plant_rows(old_row, delta)
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_rule_delta_row_over_a_pair_the_old_rule_never_covered_covers_nothing(self):
        old_blob = self.rule_blob("cq.C")
        self._edit_cq_c_rule()
        delta = self.row(id="delta", ruleBlob=self.rule_blob("cq.C"), ruleFrom=old_blob)
        self.plant_rows(delta)
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_record_stores_the_rule_blob_and_a_rule_delta_names_its_predecessor(self):
        old_blob = self.rule_blob("cq.C")
        result = self.record("--script", "check", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[-1]["ruleBlob"], old_blob)
        result = self.record("--script", "check", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--rule-from", old_blob)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[-1]["ruleFrom"], old_blob)

    def test_plan_seats_a_rule_delta_with_its_old_and_new_rule_blobs(self):
        old_blob = self.rule_blob("cq.C")
        self.plant_rows(self.row(ruleBlob=old_blob))
        self._edit_cq_c_rule()
        plan = self.kept(self.plan([self.seat()]))
        self.assertEqual(plan["roster"][0]["ruleDelta"], [{"mandate": "cq.C", "file": "src/a.cs",
            "ruleFrom": old_blob, "ruleTo": self.rule_blob("cq.C")}])

    def test_clause2_outcomes_that_do_not_cover(self):
        for outcome in ("incomplete", "not-reached", "blocked", "failed", "bogus"):
            with self.subTest(outcome=outcome):
                self.plant_rows(self.row(outcome=outcome))
                self.assertEqual(self.pairs(self.uncovered()), [self.pair()])
        self.plant_rows(self.row(outcome="findings", findings=2))
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_clause3_edited_file_is_uncovered_and_a_commit_keeps_coverage(self):
        reviewed = self.blob("src/a.cs")
        self.plant_rows(self.row())
        self._write("src/a.cs", "one\ntwo\n")
        self.assertEqual(self.pairs(self.uncovered()), [self.pair() + "\tafter:" + reviewed])
        self._write("src/a.cs", "one\n")
        self.assertEqual(self.pairs(self.uncovered()), [])
        self._write("src/b.cs", "other\n")
        self._commit("C elsewhere")
        self.assertEqual(self.pairs(self.uncovered()), [], "a later commit on other files keeps coverage")

    def test_clause4_merge_resolution_touching_the_file_drops_coverage(self):
        self.branch()
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit")
        reviewed = self.row(files={"src/a.cs": self.blob("src/a.cs")})
        self.checkout("main")
        self._write("src/a.cs", "main\n")
        self._commit("main edit")
        self.checkout("feat")
        self.merge("main", "M", resolved={"src/a.cs": "feat\n"})
        self.plant_rows(reviewed)
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_clause4_clean_merge_from_main_keeps_coverage(self):
        self.branch()
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit")
        reviewed = self.row(files={"src/a.cs": self.blob("src/a.cs")})
        self.checkout("main")
        self._write("main_only.txt", "m\n")
        self._commit("main edit")
        self.checkout("feat")
        self.merge("main", "M")
        self.plant_rows(reviewed)
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_clause4_a_rebased_head_reaches_head_through_patch_equivalent_commits(self):
        self.branch()
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit")
        reviewed = self.row(files={"src/a.cs": self.blob("src/a.cs")})
        self.r.reset_hard(self.r.tips["main"])
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit rewritten")
        self.plant_rows(reviewed)
        self.assertEqual(self.pairs(self.uncovered()), [])

    def test_clause4_a_conflict_resolved_replay_owes_only_the_file_whose_own_patch_changed(self):
        self.branch()
        self._write("src/a.cs", "feat\n")
        self._write("src/b.cs", "feat b\n")
        self._commit("feat edit")
        files = {p: self.blob(p) for p in ("src/a.cs", "src/b.cs")}
        rows = [self.row(files=files), self.row(files=files)]
        self.r.reset_hard(self.r.tips["main"])
        self._write("src/b.cs", "peer b\n")
        self._commit("peer edit")
        self._write("src/a.cs", "feat\n")
        self._write("src/b.cs", "feat b\n")
        self._commit("feat edit")
        self.plant_rows(*rows)
        self.assertEqual(self.pairs(self.uncovered(files=("src/a.cs", "src/b.cs"))),
                         [self.pair(path="src/b.cs")])

    def test_clause4_a_rewritten_commit_that_touched_the_file_leaves_it_owed(self):
        self.branch()
        self._write("src/a.cs", "draft\n")
        self._commit("feat draft")
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit")
        reviewed = self.row(files={"src/a.cs": self.blob("src/a.cs")})
        self.r.reset_hard(self.r.tips["main"])
        self._write("src/a.cs", "feat\n")
        self._commit("feat squashed")
        self.plant_rows(reviewed)
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_clause5_newer_escape_row_drops_coverage_and_an_older_one_does_not(self):
        self.plant_rows(self.row(ts="2026-09-30T00:00:00Z"))
        for ts, path, expected in (("2026-09-29T00:00:00Z", "src/a.cs", []),
                                   ("2026-10-01T00:00:00Z", "src/a.cs", [self.pair()]),
                                   ("2026-10-01T00:00:00Z", "src/other.cs", [])):
            with self.subTest(ts=ts, path=path):
                self.escape_log.write_text(json.dumps({"ts": ts, "paths": [path]}) + "\n", encoding="utf-8")
                self.assertEqual(self.pairs(self.uncovered()), expected)

    def test_clause5_a_same_second_escape_drops_coverage_unless_the_row_is_its_own_finding_call(self):
        stamp = "2026-09-30T00:00:00Z"
        self.plant_rows(self.row(ts=stamp, call="c1"))
        for escape, expected in (({"ts": stamp, "paths": ["src/a.cs"]}, [self.pair()]),
                                 ({"ts": stamp, "paths": ["src/a.cs"], "foundBy": "other"}, [self.pair()]),
                                 ({"ts": stamp, "paths": ["src/a.cs"], "foundBy": "c1"}, [])):
            with self.subTest(escape=escape):
                self.escape_log.write_text(json.dumps(escape) + "\n", encoding="utf-8")
                self.assertEqual(self.pairs(self.uncovered()), expected)

    def test_clause5_an_escape_row_in_the_shared_overlay_drops_coverage_before_any_tracked_log_holds_it(self):
        overlay = self.ledger.with_name("escape_overlay.jsonl")
        self.plant_rows(self.row(ts="2026-09-30T00:00:00Z"))
        for ts, expected in (("2026-09-29T00:00:00Z", []), ("2026-10-01T00:00:00Z", [self.pair()])):
            with self.subTest(ts=ts):
                overlay.write_text(json.dumps({"ts": ts, "paths": ["src/a.cs"]}) + "\n", encoding="utf-8")
                self.assertFalse(self.escape_log.exists())
                self.assertEqual(self.pairs(self.uncovered()), expected)
        overlay.write_text("{not json\n", encoding="utf-8")
        corrupt = self.uncovered()
        self.assertEqual(self.pairs(corrupt), [self.pair()])
        self.assertIn(str(overlay), corrupt.stderr.decode())

    def test_clause5_an_escape_log_that_cannot_be_parsed_drops_coverage(self):
        self.plant_rows(self.row())
        self.escape_log.write_text("{not json\n", encoding="utf-8")
        corrupt = self.uncovered()
        self.assertEqual(self.pairs(corrupt), [self.pair()])
        self.assertIn(str(self.escape_log), corrupt.stderr.decode())
        self.escape_log.unlink()
        self.escape_log.mkdir()
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_clause6_the_roster_seat_carrying_the_mandate_sets_the_load(self):
        self.plant_rows(self.row(seatMandates=["cq.C", "k0", "k1"]))
        one = [self.seat("one", ("cq.C",))]
        three = [self.seat("three", ("cq.C", "cq.D", "parity"))]
        self.assertEqual(self.pairs(self.uncovered(roster=one)), [self.pair()])
        self.assertEqual(self.pairs(self.uncovered(roster=three)), [])
        self.assertEqual(self.pairs(self.uncovered(roster=three + one)), [self.pair()],
                         "a mandate two seats carry is covered only when both seats' depth holds")

    def test_clause6_the_roster_seat_tier_and_effort_bind_uncovered_as_they_bind_plan(self):
        self.plant_rows(self.row(tier="executor", effort="low"))
        self.assertEqual(self.pairs(self.uncovered(roster=[self.seat(tier="architect")])), [self.pair()])
        self.assertEqual(self.pairs(self.uncovered(roster=[self.seat(effort="high")])), [self.pair()])
        self.assertEqual(self.pairs(self.uncovered(roster=[self.seat(effort="low")])), [])

    def test_clause6_with_no_roster_a_panel_covered_pair_prints_depth_unchecked(self):
        self.plant_rows(self.row(), self.row("worklog-status", reviewer="script:worklog-status", tier=None,
                                              effort=None, seatMandates=[]))
        result = self.run_tool("uncovered", "--files", "src/a.cs", "--mandates", "cq.C,cq.D,worklog-status",
                               "--base-ref", "main")
        self.assertEqual(self.pairs(result), [self.pair() + "\tdepth-unchecked", self.pair("cq.D")])
        self.assertIn("--roster", result.stderr.decode())

    def test_clause6_a_mandate_no_roster_seat_carries_prints_depth_unchecked(self):
        self.plant_rows(self.row())
        self.assertEqual(self.pairs(self.uncovered(roster=[self.seat("other", ("cq.D",))])),
                         [self.pair() + "\tdepth-unchecked"])

    def test_clause6_tier_in_both_directions_and_null_tier(self):
        cases = (("executor", "architect", True), ("architect", "executor", False),
                 ("executor", "executor", False), (None, "executor", True), ("bogus", "executor", True))
        for row_tier, seat_tier, stays in cases:
            with self.subTest(row=row_tier, seat=seat_tier):
                self.plant_rows(self.row(tier=row_tier))
                plan = self.kept(self.plan([self.seat(tier=seat_tier)]))
                self.assertEqual(bool(plan["roster"]), stays)
                self.assertEqual(bool(plan["dropped"]), not stays)

    def test_clause6_effort_below_equal_above_and_unknown(self):
        cases = (("low", "high", True), ("high", "high", False), ("max", "medium", False),
                 ("turbo", "high", True), (None, "high", True), ("ultra", "xhigh", False))
        for row_effort, seat_effort, stays in cases:
            with self.subTest(row=row_effort, seat=seat_effort):
                self.plant_rows(self.row(effort=row_effort))
                plan = self.kept(self.plan([self.seat(effort=seat_effort)]))
                self.assertEqual(bool(plan["roster"]), stays)

    def test_clause6_script_rows_cover_their_mandate_without_depth(self):
        self.plant_rows(self.row(reviewer="script:worklog-status", tier=None, effort=None, seatMandates=[]))
        plan = self.kept(self.plan([self.seat(tier="architect", effort="max")]))
        self.assertEqual(plan["roster"], [])

    def test_clause7_interaction_mandate_needs_the_current_merge_base(self):
        self.plant_rows(self.row("api-consume", mergeBase="0" * 40))
        self.assertEqual(self.pairs(self.uncovered(mandates="api-consume")), [self.pair("api-consume")])
        self.plant_rows(self.row("api-consume"))
        self.assertEqual(self.pairs(self.uncovered(mandates="api-consume")), [])
        self.plant_rows(self.row("cq.C", mergeBase="0" * 40))
        self.assertEqual(self.pairs(self.uncovered()), [], "a non-interaction mandate ignores the merge-base")

    def test_clause8_plan_row_does_not_cover_a_session_subject(self):
        self.plant_rows(self.row(subject="plan:.claude/plans/x.md"))
        self.assertEqual(self.pairs(self.uncovered()), [self.pair()])

    def test_clause8_session_and_pr_rows_cover_each_other(self):
        self.plant_rows(self.row(subject="pr:7"))
        self.assertEqual(self.pairs(self.uncovered()), [])
        self.plant_rows(self.row(subject="session:s9"))
        plan = self.kept(self.plan([self.seat()], subject="pr:7", stage="pre-merge", attrs=NO_PICK))
        self.assertEqual(plan["roster"], [])

    HAIKU = "claude-haiku-5-5"
    OPUS = "claude-opus-5-5"

    def supp_row(self, model=None, **over):
        return self.row(model=model or self.HAIKU, tier="architect", id="supp", **over)

    def qual_row(self, **over):
        return self.row(**dict(dict(model=self.OPUS, tier="architect", id="qual"), **over))

    def arch_seat(self, mandates=("cq.C",), effort="high"):
        return self.seat("s" + "+".join(mandates), mandates, tier="architect", effort=effort)

    def cover_for(self, seat):
        with patch.dict(os.environ, self.env()):
            cover = load_module(TOOL)._make_cover(self.repo, "main", "session")[0]
            cover.standing({"reviewer": "panel"})
        cover.seat = seat
        return cover

    def test_clause9_a_lone_supplementary_panel_row_covers_nothing(self):
        self.plant_registry(self.HAIKU)
        self.plant_rows(self.supp_row())
        self.assertEqual(self.pairs(self.uncovered(roster=[self.arch_seat()])), [self.pair()])

    def test_clause9_a_supplementary_row_beside_a_qualified_row_covers_and_both_cover(self):
        self.plant_registry(self.HAIKU)
        supp, qual = self.supp_row(), self.qual_row()
        self.plant_rows(qual, supp)
        self.assertEqual(self.pairs(self.uncovered(roster=[self.arch_seat()])), [])
        cover = self.cover_for({"tier": "architect", "effort": "high", "load": 1})
        self.assertTrue(cover.covers(supp, "cq.C", "src/a.cs"))
        self.assertTrue(cover.covers(qual, "cq.C", "src/a.cs"))

    def test_clause9_a_merged_qualified_seat_not_reached_leaves_the_supplementary_pair_uncovered(self):
        self.plant_registry(self.HAIKU)
        merged = self.qual_row(outcome="incomplete", seatMandates=["cq.C", "cq.D", "parity"])
        self.plant_rows(merged, self.supp_row())
        roster = [self.arch_seat(("cq.C", "cq.D", "parity")), self.arch_seat(("cq.C",))]
        self.assertEqual(self.pairs(self.uncovered(roster=roster)), [self.pair()])

    def test_clause9_a_qualified_partner_below_the_roster_effort_does_not_back_the_pair(self):
        self.plant_registry(self.HAIKU)
        self.plant_rows(self.qual_row(effort="low"), self.supp_row())
        self.assertEqual(self.pairs(self.uncovered(roster=[self.arch_seat()])), [self.pair()])

    def test_clause9_a_qualified_partner_meeting_the_effort_with_a_larger_load_backs_the_pair(self):
        self.plant_registry(self.HAIKU)
        self.plant_rows(self.qual_row(seatMandates=["cq.C", "cq.D", "parity"]), self.supp_row())
        self.assertEqual(self.pairs(self.uncovered(roster=[self.arch_seat()])), [])

    def test_clause9_a_model_the_registry_cannot_resolve_passes(self):
        self.plant_registry(self.HAIKU)
        self.plant_rows(self.supp_row(model="no-such-model"))
        self.assertEqual(self.pairs(self.uncovered(roster=[self.arch_seat()])), [])

    def test_clause9_an_unloadable_registry_fails_closed_and_names_the_error_once(self):
        (self.root / "registry.json").write_text("{not json", encoding="utf-8")
        self.registry = str(self.root / "registry.json")
        self.plant_rows(self.supp_row(), self.qual_row())
        result = self.uncovered(roster=[self.arch_seat()])
        self.assertEqual(self.pairs(result), [self.pair()])
        err = result.stderr.decode()
        self.assertEqual(len([l for l in err.splitlines() if "clause 9" in l]), 1, err)

    def test_clause9_a_derived_supplementary_row_needs_a_qualified_partner_at_the_current_blob(self):
        self.plant_registry(self.HAIKU)
        old = self.blob("src/a.cs")
        root = self.qual_row(id="root")
        self._write("src/a.cs", "fixed\n")
        derived = self.supp_row(effort="medium", seatMandates=["cq.C", "cq.D"], **{"from": {"src/a.cs": old}})
        roster = [self.arch_seat()]
        self.plant_rows(root, derived)
        self.assertEqual(self.pairs(self.uncovered(roster=roster)), [self.pair() + "\tafter:" + old])
        self.plant_rows(root, derived, self.qual_row())
        self.assertEqual(self.pairs(self.uncovered(roster=roster)), [])

    def test_clause9_a_premise_audit_judges_the_premise_row_and_its_partner_in_premise_mode(self):
        self.plant_registry(self.HAIKU)
        premise, head = self.parent(), self.head()
        shared = dict(premise_commits=[premise], head=head, stage="candidate")
        supp, qual = self.supp_row(**shared), self.qual_row(**shared)
        seat = {"tier": "architect", "effort": "high", "load": 1}
        self.plant_rows(supp)
        cover = self.cover_for(seat)
        self.assertEqual(cover.pair_state("cq.C", "src/a.cs", [seat], premise), "uncovered")
        self.plant_rows(qual, supp)
        cover = self.cover_for(seat)
        self.assertIsNone(cover.pair_state("cq.C", "src/a.cs", [seat], premise))
        self.assertTrue(cover._premise_covers(supp, "cq.C", "src/a.cs", premise))
        self.assertTrue(cover._premise_covers(qual, "cq.C", "src/a.cs", premise))

    def test_plan_names_each_kept_seats_uncovered_files_so_the_subject_outside_the_delta_is_presented(self):
        self._write("src/b.cs", "two\n")
        self._commit("C")
        self.plant_rows(self.row(files={"src/a.cs": self.blob("src/a.cs")}))
        plan = self.kept(self.plan([self.seat("one", ("cq.C",)), self.seat("two", ("cq.D",))],
                                   files=("src/a.cs", "src/b.cs")))
        self.assertEqual({s["key"]: s["uncoveredFiles"] for s in plan["roster"]},
                         {"one": ["src/b.cs"], "two": ["src/a.cs", "src/b.cs"]})
        ignored = self.kept(self.plan([self.seat("one", ("cq.C",)), self.seat("two", ("cq.D",))], "--ignore-ledger",
                                      files=("src/a.cs", "src/b.cs")))
        self.assertEqual({s["key"]: s["uncoveredFiles"] for s in ignored["roster"]},
                         {"one": ["src/a.cs", "src/b.cs"], "two": ["src/a.cs", "src/b.cs"]})

    def test_clause8_plan_row_covers_a_plan_subject_and_doc_row_covers_only_docs(self):
        self.plant_rows(self.row(subject="plan:p"))
        self.assertEqual(self.kept(self.plan([self.seat()], subject="plan:p", stage="plan-check"))["roster"], [])
        self.plant_rows(self.row(subject="doc:d"))
        self.assertTrue(self.kept(self.plan([self.seat()], subject="plan:p", stage="plan-check"))["roster"])
        self.assertEqual(self.kept(self.plan([self.seat()], subject="doc:d", stage="design"))["roster"], [])

    def _design_fixture(self, edit_design=False):
        self._write("docs/design.md", "design\n")
        self._write("plans/p.md", "plan\n")
        self._commit("design and plan")
        self.plant_rows(self.row(subject="doc:docs/design.md", files={"docs/design.md": self.blob("docs/design.md")}))
        if edit_design:
            self._write("docs/design.md", "design changed\n")

    def _design_plan(self, *extra, stage="plan-check", subject="plan:plans/p.md"):
        return self.plan([self.seat()], "--design", "docs/design.md", *extra,
                         stage=stage, subject=subject, files=("plans/p.md",))

    def test_clause8_doc_exception_covers_with_design_and_no_new_decision(self):
        self._design_fixture()
        plan = self.kept(self._design_plan("--no-new-decision"))
        self.assertEqual(plan["roster"], [])
        self.assertEqual(plan["dropped"][0]["mandate"], "cq.C")

    def test_clause8_doc_exception_off_when_the_design_changed(self):
        self._design_fixture(edit_design=True)
        self.assertTrue(self.kept(self._design_plan("--no-new-decision"))["roster"])

    def test_clause8_doc_exception_off_without_no_new_decision(self):
        self._design_fixture()
        self.assertTrue(self.kept(self._design_plan())["roster"])

    def test_clause8_doc_exception_off_for_a_session_subject(self):
        self._design_fixture()
        result = self.plan([self.seat()], "--design", "docs/design.md", "--no-new-decision",
                           stage="plan-check", subject="session:s1", files=("plans/p.md",))
        self.assertTrue(self.kept(result)["roster"])

    def test_plan_flag_misuse_exits_2(self):
        self.assertEqual(self.plan([self.seat()], "--no-new-decision", stage="plan-check",
                                   subject="plan:p").returncode, 2)
        self.assertEqual(self.plan([self.seat()], "--design", "docs/design.md", stage="candidate").returncode, 2)

    def test_uncovered_usage_errors_exit_2(self):
        self.assertEqual(self.run_tool("uncovered").returncode, 2)
        result = self.uncovered(mandates="nonsense,cq.C")
        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate", result.stderr.decode())
        self.assertEqual(self.run_tool("rows").returncode, 2)
        self.assertEqual(self.run_tool("plan").returncode, 2)

    def test_candidate_alias_expands_to_the_one_mandate_set(self):
        env = {"HARNESS_REVIEW_CANDIDATE_KEYS": "cq.C,cq.D"}
        listed = self.run_tool("keys", "--stage", "candidate", extra_env=env)
        self.assertEqual(listed.stdout.decode().split(), ["cq.C", "cq.D"])
        self.plant_rows(self.row("cq.C"))
        roster = self.root / "candidate_roster.json"
        roster.write_text(json.dumps([self.seat("c", ("cq.C",)), self.seat("d", ("cq.D",))]), encoding="utf-8")
        result = self.run_tool("uncovered", "--files", "src/a.cs", "--mandates", "candidate",
                               "--base-ref", "main", "--roster", str(roster), extra_env=env)
        self.assertEqual(self.pairs(result), [self.pair("cq.D")])

    def test_ignore_ledger_keeps_every_seat_and_says_why(self):
        self.plant_rows(self.row())
        result = self.plan([self.seat()], "--ignore-ledger")
        plan = self.kept(result)
        self.assertEqual([s["key"] for s in plan["roster"]], ["seat"])
        self.assertEqual(plan["dropped"], [])
        self.assertIn("ignore", result.stderr.decode().lower())

    def test_plan_drops_a_covered_mandate_from_a_merged_seat_and_keeps_the_rest(self):
        self.plant_rows(self.row("cq.C"))
        plan = self.kept(self.plan([self.seat(mandates=("cq.C", "cq.D"))]))
        self.assertEqual(plan["roster"][0]["mandates"], ["cq.D"])
        self.assertEqual(plan["dropped"], [{"mandate": "cq.C", "coveredBy": "run1:seat:cq.C"}])

    def test_plan_keeps_every_seat_and_says_why_with_no_ledger(self):
        result = self.plan([self.seat()])
        plan = self.kept(result)
        self.assertEqual(len(plan["roster"]), 1)
        self.assertIn("ledger", result.stderr.decode())

    def test_plan_unknown_mandate_in_the_roster_exits_2(self):
        self.assertEqual(self.plan([self.seat(mandates=("nope",))]).returncode, 2)


class ExecutorLineageTracerTests(LedgerFixture):
    def test_candidate_selection_reports_missing_task_without_recorder_guess(self):
        result = self.kept(self.plan([self.seat()]))
        selection = result["reviewerSelection"]
        self.assertEqual(selection["primary"], [])
        self.assertTrue(selection["unknown"])
        self.assertEqual(selection["revision"]["files"], {"src/a.cs": self.blob("src/a.cs")})
        self.assertFalse(selection["freshness"]["required"])

    def test_corrupt_task_archive_is_unknown_lineage_not_clean_or_a_crash(self):
        directory = self.root / "tasks"
        directory.mkdir()
        (directory / "broken.archive.jsonl").write_text("not json\n", encoding="utf-8")
        roster = self.root / "broken-roster.json"
        roster.write_text(json.dumps([self.seat()]), encoding="utf-8")
        result = self.run_tool("plan", "--stage", "candidate", "--subject", "session:s1", "--task", "broken",
            "--roster", str(roster), "--files", "src/a.cs", "--base-ref", "main",
            extra_env={"HARNESS_TASK_RECORD_DIR": str(directory)})
        selection = self.kept(result)["reviewerSelection"]
        self.assertEqual(selection["primary"], [])
        self.assertTrue(any("unparseable" in row["reason"] for row in selection["unknown"]))

    def test_full_plan_roster_keeps_bare_list_compatibility(self):
        seats = [self.seat()]
        planned = self.kept(self.plan({"roster": seats, "reviewerSelection": {"lineageFingerprint": "old"}}))
        self.assertEqual(planned["roster"][0]["key"], "seat")
        self.assertNotEqual(planned["reviewerSelection"]["lineageFingerprint"], "old")


class CompleteLineageTests(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.jobs = []
        self.decisions = []
        for rel in ("skills/orchestration/reference/review_width.md", "reference/model_ladder_evidence.md"):
            path = self.claude / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("settled review policy\n", encoding="utf-8")
        self.mod = load_module(TOOL)
        self.tasks = self.mod._import("task_record")

    def job(self, role, model="claude-opus-5-5", path="src/a.cs", blob=None, base=None, **extra):
        row = dict(run_id="run-" + str(len(self.jobs)), label=role, source="sidecar", state="accepted",
                   role=role, model=model, modelObserved=True, identityBasis="attestedModel",
                   files={path: blob or self.blob(path)}, baseFiles={path: base or self.blob(path)})
        return dict(row, **extra)

    def selection(self, stage="candidate", files=("src/a.cs",), ignore=False, subject="session:s1"):
        with patch.object(self.tasks, "all_jobs", return_value=self.jobs), \
                patch.object(self.tasks, "all_decisions", return_value=self.decisions, create=True):
            return self.kept(self.plan([self.seat(tier="architect")], "--task", "task-e2",
                *(["--ignore-ledger"] if ignore else []), stage=stage, subject=subject, files=files,
                attrs=NO_PICK))["reviewerSelection"]

    def test_every_stage_separates_decisions_from_implementation_and_faithful_text(self):
        self.jobs = [self.job(role, model) for role, model in (
            ("architect", "claude-opus-5-5"), ("drafter", "claude-sonnet-5-5"),
            ("transcriber", "gpt-6.1-sol"), ("executor", "gpt-6.1-sol"), ("fold", "claude-opus-5-5"))]
        for stage in self.mod.STAGES:
            with self.subTest(stage=stage):
                out = self.selection(stage)
                roles = {r["role"] for r in out["primary"]}
                self.assertEqual(roles, {"architect", "drafter", "fold"} if stage in ("design", "plan-check")
                                 else {"executor", "fold", "transcriber"})
                self.assertEqual({r["role"] for r in out["inherited"]},
                                 set() if stage in ("design", "plan-check") else {"architect", "drafter"})

    def test_serial_executors_preserve_both_groups_and_broken_base_is_unknown(self):
        old = self.blob("src/a.cs")
        first = self.job("executor", base=old)
        self.jobs.append(first)
        self._write("src/a.cs", "second\n")
        self.jobs.append(self.job("executor", "gpt-6.1-sol", base=old))
        out = self.selection()
        self.assertEqual({r["group"] for r in out["primary"]}, {"anthropic", "openai"})
        self.assertFalse(out["unknown"])
        self.jobs[-1]["baseFiles"]["src/a.cs"] = "f" * 40
        broken = self.selection()
        self.assertTrue(any(r["reason"] == "broken base-to-accepted chain" for r in broken["unknown"]))

    def test_inherited_input_is_revision_bound_and_mixed_scope_stays_per_file(self):
        self._write("design.md", "design\n")
        self._commit("design")
        self.jobs = [self.job("architect", path="design.md"),
                     self.job("executor", "gpt-6.1-sol", inputFiles={"design.md": self.blob("design.md")})]
        out = self.selection()
        self.assertEqual({r["group"] for r in out["primary"]}, {"openai"})
        self.assertEqual({r["group"] for r in out["inherited"]}, {"anthropic"})
        self.assertIn("design.md", out["byFile"]["src/a.cs"]["inherited"][0]["scope"])
        self.jobs.append(self.job("executor", path="design.md"))
        mixed = self.selection(files=("src/a.cs", "design.md"))
        self.assertEqual({r["group"] for r in mixed["byFile"]["src/a.cs"]["primary"]}, {"openai"})
        self.assertEqual({r["group"] for r in mixed["byFile"]["design.md"]["primary"]}, {"anthropic"})

    def test_coverage_reviewers_rejected_unrelated_and_legacy_recorder_are_not_authors(self):
        valid = self.job("executor", "gpt-6.1-sol")
        self.jobs = [valid, dict(self.job("architect"), state="launched"),
                     dict(self.job("drafter"), state="rejected"),
                     dict(run_id="legacy", label="executor", state="done", authorModel="claude-opus-5-5"),
                     self.job("architect", path="other.cs", blob="d" * 40, base="d" * 40)]
        self.plant_rows(self.row(reviewer="panel", model="claude-opus-5-5", modelObserved=True))
        out = self.selection(ignore=True)
        self.assertEqual({r["group"] for r in out["primary"]}, {"openai"})
        self.assertTrue(any("legacy" in r["reason"] for r in out["unknown"]))
        self.assertEqual(out["inherited"], [])

    def test_missing_role_actor_base_and_inputs_are_typed_unknown(self):
        for field in ("role", "model", "baseFiles"):
            row = self.job("executor")
            row.pop(field)
            self.jobs = [row]
            with self.subTest(field=field):
                out = self.selection()
                self.assertEqual(out["primary"], [])
                self.assertTrue(out["unknown"])
        self.jobs = [self.job("executor", inputFiles={"missing.md": "a" * 40})]
        self.assertTrue(any("input" in r["reason"] for r in self.selection()["unknown"]))

    def test_accepted_recommendations_preserve_origin_and_unit_scope(self):
        blob = self.blob("src/a.cs")
        reviewer = self.row(id="origin", reviewer="panel", model="gpt-6.1-sol", modelObserved=True,
                            identityBasis="attestedModel")
        self.plant_rows(reviewer)
        self.jobs = [self.job("fold", origins=[{"source": "adapted", "row": "origin",
                        "files": {"src/a.cs": blob}, "units": {"src/a.cs": ["Choice"]}}])]
        out = self.selection("plan-check")
        self.assertEqual({r["group"] for r in out["primary"]}, {"openai"})
        self.assertEqual(out["byFile"]["src/a.cs"]["units"]["Choice"]["primary"][0]["group"], "openai")
        self.jobs[0]["origins"][0]["source"] = "fold"
        changed = self.selection("plan-check")
        self.assertEqual({r["group"] for r in changed["primary"]}, {"openai", "anthropic"})
        self.assertNotEqual(out["lineageFingerprint"], changed["lineageFingerprint"])

    def test_fingerprint_ignores_order_and_time_but_binds_identity_and_inputs(self):
        self.jobs = [self.job("architect"), self.job("executor", "gpt-6.1-sol")]
        before = self.selection()
        self.jobs.reverse()
        for job in self.jobs:
            job["at"] = "later"
        self.assertEqual(before["lineageFingerprint"], self.selection()["lineageFingerprint"])
        self.jobs[0]["identityBasis"] = "sessionTranscript"
        self.assertNotEqual(before["lineageFingerprint"], self.selection()["lineageFingerprint"])

    def test_malformed_archive_shapes_are_unknown_not_a_crash(self):
        directory = self.root / "tasks"
        directory.mkdir()
        (directory / "shape.archive.jsonl").write_text('[1, 2]\n', encoding="utf-8")
        roster = self.root / "shape-roster.json"
        roster.write_text(json.dumps([self.seat()]), encoding="utf-8")
        result = self.run_tool("plan", "--stage", "design", "--subject", "doc:src/a.cs", "--task", "shape",
            "--roster", str(roster), "--files", "src/a.cs", "--base-ref", "main",
            extra_env={"HARNESS_TASK_RECORD_DIR": str(directory)}, attrs=NO_PICK)
        out = self.kept(result)["reviewerSelection"]
        self.assertTrue(any("unparseable" in r["reason"] for r in out["unknown"]))

    def test_malformed_job_inputs_and_units_stay_unknown(self):
        for fields in ({"inputFiles": {"design.md": []}}, {"units": {"src/a.cs": 12}},
                       {"units": ["Choice"]}, {"baseFiles": {"src/a.cs": []}},
                       {"origins": [{"source": "finding", "row": "malformed-unit-origin",
                                     "files": {"src/a.cs": self.blob("src/a.cs")}, "units": {"src/a.cs": 12}}]}):
            with self.subTest(fields=fields):
                self.plant_rows(self.row(id="malformed-unit-origin", reviewer="panel", model="gpt-6.1-sol",
                                         modelObserved=True, identityBasis="attestedModel"))
                self.jobs = [self.job("executor", **fields)]
                self.assertTrue(self.selection()["unknown"])

    def test_decision_join_uses_archived_evidence_not_incidental_task_association(self):
        blob = self.blob("src/a.cs")
        evidence = self.root / "decision-evidence.txt"
        evidence.write_text("accepted choice", encoding="utf-8")
        self.decisions = [{"id": "D1", "text": "accepted", "at": "now", "evidence": [
            {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}]
        self.plant_rows(self.row(id="recommendation", reviewer="panel", model="gpt-6.1-sol", modelObserved=True,
                                identityBasis="attestedModel"))
        self.jobs = [self.job("fold", origins=[{"source": "finding", "row": "recommendation", "decision": "D1",
                                               "files": {"src/a.cs": blob}}])]
        out = self.selection("design")
        self.assertEqual(out["primary"][0]["decision"], "D1")
        self.decisions[0]["at"] = "later"
        self.assertEqual(out["lineageFingerprint"], self.selection("design")["lineageFingerprint"])
        evidence.write_text("unaccepted edit", encoding="utf-8")
        missing = self.selection("design")
        self.assertFalse(missing["primary"])
        self.assertTrue(any("decision evidence" in r["reason"] for r in missing["unknown"]))
        self.jobs = [self.job("architect")]
        self.assertEqual({r["group"] for r in self.selection("design")["primary"]}, {"anthropic"})

    def test_all_groups_used_freshness_falls_back_to_a_different_model(self):
        self.jobs = [self.job("architect"), self.job("drafter", "gpt-6.1-sol"),
                     self.job("fold", "deepseek-flash")]
        out = self.selection("design")
        self.assertEqual({r["group"] for r in out["primary"]}, {"anthropic", "openai", "deepseek"})
        selection = dict(out, coveringReviewers=[{"model": "claude-opus-5-5", "group": "anthropic"},
                                                {"model": "gpt-6.1-sol", "group": "openai"}])
        candidates = [{"id": "claude-sonnet-5-5", "reviewGroup": "anthropic", "headroomVerified": True,
                       "payAvailable": False}]
        with patch.dict(os.environ, self.env()):
            cover = self.mod._make_cover(self.repo, "main", "session")[0]
            fresh = self.mod._freshness(selection, True, cover, [], True, candidates)
        self.assertTrue(fresh["required"])
        self.assertTrue(fresh["candidates"][0]["degraded"])
        self.assertIn("no untouched", fresh["reason"])

    def test_below_floor_roster_is_disclosed_not_rewritten_or_expanded(self):
        self.jobs = [self.job("executor")]
        with patch.object(self.tasks, "all_jobs", return_value=self.jobs), \
                patch.object(self.tasks, "all_decisions", return_value=[]):
            out = self.kept(self.plan([self.seat(tier="executor")], "--task", "task-e2", attrs=NO_PICK))
        self.assertEqual(len(out["roster"]), 1)
        self.assertEqual(out["roster"][0]["tier"], "executor")
        selection = out["reviewerSelection"]
        self.assertTrue(any("architect floor" in r["reason"] for r in selection["unknown"]))
        self.assertTrue(all("architect" in r.get("roles", []) for r in selection["candidates"]))

    def test_executor_depth_cannot_reuse_a_freshness_judgment_seat(self):
        self.jobs = [self.job("executor")]
        with patch.object(self.tasks, "all_jobs", return_value=self.jobs), \
                patch.object(self.tasks, "all_decisions", return_value=[]):
            first = self.kept(self.plan([self.seat(tier="executor")], "--task", "task-e2",
                                       stage="pre-merge", attrs=NO_PICK))["reviewerSelection"]
            self.plant_rows(self.row(id="shallow-fresh", reviewer="panel", model="gpt-6.1-sol", modelObserved=True,
                                     stage="pre-merge", tier="executor", reviewerSelection=first))
            next_plan = self.kept(self.plan([self.seat(tier="executor")], "--task", "task-e2",
                                           stage="pre-merge", attrs=NO_PICK))
        self.assertTrue(next_plan["reviewerSelection"]["freshness"]["required"])

    def test_currency_facts_keep_the_band_reading_and_binding_window(self):
        bands = self.mod._import("provider_bands")
        picked = {"ranked": [{"transport": "anthropic", "room": 1.2, "window": "weekly", "binding": "five-hour",
                              "bindingRoom": 0.8, "left": 75}], "payOptions": []}
        reading = {"band": "Hot", "routingWindow": "weekly", "sourceKind": "cache", "observedAt": "now"}
        with patch.object(bands, "pick", return_value=picked), patch.object(bands, "reading", return_value=reading):
            out = self.mod._pick("architect")
        self.assertEqual(out["ranked"][0].get("bandReading"), reading)
        self.assertEqual(out["ranked"][0]["binding"], "five-hour")

    def test_unreadable_policy_owner_cannot_be_reused_as_current_policy(self):
        self.jobs = [self.job("executor", "gpt-6.1-sol")]
        path = self.claude / "skills/orchestration/reference/review_width.md"
        path.unlink()
        first = self.selection("pre-merge")
        self.plant_rows(self.row(id="fresh", reviewer="panel", model="claude-opus-5-5", modelObserved=True,
                                stage="pre-merge", tier="architect", reviewerSelection=first))
        out = self.selection("pre-merge")
        self.assertTrue(out["freshness"]["required"])
        self.assertTrue(any("policy owner" in r["reason"] for r in out["unknown"]))

    def test_policy_owners_invalidate_reuse_even_when_mandate_text_is_unchanged(self):
        self.jobs = [self.job("executor", "gpt-6.1-sol")]
        first = self.selection("pre-merge")
        self.plant_rows(self.row(id="fresh", reviewer="panel", model="claude-opus-5-5", modelObserved=True,
                                stage="pre-merge", tier="architect", reviewerSelection=first))
        self.assertFalse(self.selection("pre-merge")["freshness"]["required"])
        for rel in ("skills/orchestration/reference/review_width.md", "reference/model_ladder_evidence.md"):
            path = self.claude / rel
            path.write_text("new review policy\n", encoding="utf-8")
            changed = self.selection("pre-merge")
            self.assertNotEqual(first["policyFingerprint"], changed["policyFingerprint"])
            self.assertTrue(changed["freshness"]["required"])
            path.write_text("settled review policy\n", encoding="utf-8")

    def test_origin_must_join_the_reviewed_base_not_an_unrelated_revision(self):
        self.plant_rows(self.row(id="wrong", reviewer="panel", model="gpt-6.1-sol", modelObserved=True,
            identityBasis="attestedModel", files={"src/a.cs": "d" * 40}))
        self.jobs = [self.job("fold", origins=[{"source": "finding", "row": "wrong",
                                                "files": {"src/a.cs": self.blob("src/a.cs")}}])]
        out = self.selection("design")
        self.assertEqual(out["primary"], [])
        self.assertTrue(any("origin" in r["reason"] for r in out["unknown"]))

    def test_non_null_room_without_reading_is_not_verified_headroom(self):
        self.jobs = [self.job("executor")]
        pick = {"_pick": lambda tier: {"ranked": [{"transport": "anthropic", "room": 1, "window": "weekly",
                                                   "left": 75, "bandReading": None}], "payOptions": []}}
        with patch.object(self.tasks, "all_jobs", return_value=self.jobs), \
                patch.object(self.tasks, "all_decisions", return_value=[]):
            out = self.kept(self.plan([self.seat(tier="architect")], "--task", "task-e2", attrs=pick))["reviewerSelection"]
        rows = [r for r in out["candidates"] if r["transport"] == "anthropic"]
        self.assertTrue(rows)
        self.assertFalse(any(r["headroomVerified"] for r in rows))
        self.assertTrue(all(r["bandFacts"][0]["room"] == 1 for r in rows))

    def test_exact_freshness_reuse_and_empty_roster_do_not_cancel_obligation(self):
        self.jobs = [self.job("executor", "gpt-6.1-sol")]
        self.plant_rows(self.row(reviewer="panel", model="claude-opus-5-5", modelObserved=True, tier="architect"))
        first = self.selection("pre-merge")
        self.assertTrue(first["freshness"]["required"])
        self.assertEqual(first["freshness"]["usedGroups"], ["anthropic"])
        fresh = self.row(id="fresh", reviewer="panel", model="gpt-6.1-sol", modelObserved=True,
                         stage="pre-merge", tier="architect", reviewerSelection=first)
        self.plant_rows(fresh)
        second = self.selection("pre-merge")
        self.assertFalse(second["freshness"]["required"])
        self.assertEqual(second["freshness"]["reusedBy"], ["fresh"])
        self.assertTrue(self.selection("pre-merge", ignore=True)["freshness"]["required"])
        for field in ("lineageFingerprint", "policyFingerprint"):
            changed = dict(first, **{field: "wrong"})
            self.plant_rows(dict(fresh, reviewerSelection=changed))
            self.assertTrue(self.selection("pre-merge")["freshness"]["required"])
        self.plant_rows(dict(fresh, outcome="incomplete"))
        self.assertTrue(self.selection("pre-merge")["freshness"]["required"])
        self.plant_rows(dict(fresh, modelObserved=False))
        self.assertTrue(self.selection("pre-merge")["freshness"]["required"])
        self.plant_rows(dict(fresh, ruleHash="changed"))
        self.assertTrue(self.selection("pre-merge")["freshness"]["required"])


    def test_a_supplementary_cross_family_outsider_reuses_only_where_a_qualified_row_covers(self):
        self.plant_registry("deepseek-flash")
        self.jobs = [self.job("executor", "gpt-6.1-sol")]
        covering = self.row(id="qual", reviewer="panel", model="claude-opus-5-5", modelObserved=True, tier="architect")
        self.plant_rows(covering)
        first = self.selection("pre-merge")
        fresh = self.row(id="fresh", reviewer="panel", model="deepseek-flash", modelObserved=True,
                         stage="pre-merge", tier="architect", reviewerSelection=first)
        self.plant_rows(covering, fresh)
        self.assertEqual(self.selection("pre-merge")["freshness"]["reusedBy"], ["fresh"])
        self.plant_rows(fresh)
        self.assertTrue(self.selection("pre-merge")["freshness"]["required"])
        self.plant_rows(dict(covering, outcome="incomplete"), fresh)
        self.assertTrue(self.selection("pre-merge")["freshness"]["required"])


class RosterContractTests(LedgerFixture):
    def test_plan_object_is_a_roster_input(self):
        result = self.plan({"roster": [self.seat()]})
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_resolved_lens_cannot_claim_a_superset(self):
        result = self.plan([self.seat("sa-architecture-sweep", ("architecture-sweep", "cq.C"))])
        self.assertEqual(result.returncode, 2)
        self.assertIn("cq.C", result.stderr.decode())
        self.assertIn("joined with '+'", result.stderr.decode())

    def test_later_round_single_seat_preserves_inherited_effort(self):
        old = self.blob("src/a.cs")
        self.plant_rows(self.row())
        self._write("src/a.cs", "new\n")
        result = self.plan([self.seat()])
        self.assertEqual(self.kept(result)["roster"][0]["effort"], self.seat()["effort"])
        self.assertNotIn("default effort", result.stderr.decode())

    def test_prose_only_harness_scope_admits_no_candidate_seat_but_executable_change_does(self):
        parity = self.seat("parity-seat", ("parity",))
        for subject in ("session:s1", "pr:7"):
            for path in (".claude/rules/x.md", "CLAUDE.md", ".claude/baseline.lock.json"):
                self.assertEqual(self.kept(self.plan([parity], subject=subject, files=(path,)))["roster"], [], path)
            for path in (".claude/hooks/a.py", ".claude/tools/t.js", ".claude/workflows/w.json",
                         ".claude/scripts/s.sh", ".claude/settings.base.json", ".claude/tests/test_x.py"):
                kept = self.kept(self.plan([parity], subject=subject, files=(path,)))["roster"]
                self.assertEqual([seat["uncoveredFiles"] for seat in kept], [[path]], path)
        mixed = self.kept(self.plan([parity], files=(".claude/rules/x.md", ".claude/hooks/a.py")))["roster"]
        self.assertEqual([seat["uncoveredFiles"] for seat in mixed], [[".claude/hooks/a.py", ".claude/rules/x.md"]])
        game_plan = self.kept(self.plan([parity], subject="plan:world-ui", stage="plan-check",
                                        files=(".claude/plans/world-ui.md",)))["roster"]
        self.assertEqual([seat["uncoveredFiles"] for seat in game_plan], [[".claude/plans/world-ui.md"]])

    def test_whole_scope_of_a_prose_harness_scope_owes_nothing_so_it_admits_no_holistic_seat(self):
        for path, whole in ((".claude/rules/x.md", True), (".claude/hooks/a.py", False), ("src/a.cs", False)):
            self._write(path, "x\n")
            result = self.run_tool("whole-scope", "--subject", "session:s1", "--files", path,
                                   "--mandates", "architecture-sweep", "--base-ref", "main")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout.decode())["wholeScope"], whole, path)

    def test_ignore_ledger_keeps_a_named_prose_harness_seat(self):
        parity = self.seat("parity-seat", ("parity",))
        kept = self.kept(self.plan([parity], "--ignore-ledger", files=(".claude/rules/x.md",)))["roster"]
        self.assertEqual([seat["key"] for seat in kept], ["parity-seat"])

    def test_uncovered_owes_a_prose_harness_scope_no_candidate_pair(self):
        for path, owed in ((".claude/rules/x.md", False), (".claude/hooks/a.py", True)):
            self._write(path, "x\n")
            result = self.run_tool("uncovered", "--files", path, "--mandates", "parity", "--base-ref", "main")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual("parity" in result.stdout.decode(), owed, (path, result.stdout.decode()))

    def test_plan_function_entry_returns_the_dict_the_cli_prints(self):
        """`plan()` is what `prepare` calls: no argv or stdout in between, the same plan the CLI prints."""
        module = load_module(TOOL)
        roster_path = self.root / "entry_roster.json"
        roster_path.write_text(json.dumps([self.seat()]), encoding="utf-8")
        saved = os.getcwd()
        os.chdir(self.repo)
        try:
            with patch.dict(os.environ, self.env()), patch.object(module, "_pick", NO_PICK["_pick"]), \
                    patch.object(module, "_gh_json", self._fake_gh_json(module)):
                direct = module.plan(self.repo, stage="candidate", subject="session:s1", roster=str(roster_path),
                                     base_ref="main", files=["src/a.cs"])
        finally:
            os.chdir(saved)
        self.assertIsInstance(direct, dict)
        self.assertEqual([seat["key"] for seat in direct["roster"]], [self.seat()["key"]])
        printed = self.kept(self.plan([self.seat()], attrs=NO_PICK))
        self.assertEqual(direct["roster"], printed["roster"])
        self.assertEqual(direct["dropped"], printed["dropped"])
        with self.assertRaises(module.ToolError):
            module.plan(self.repo, stage="candidate", subject="session:s1", roster=str(roster_path),
                        no_new_decision=True)


class LaterRoundFactsTests(LedgerFixture):
    """`plan` reports earlier blobs without choosing later-round width."""

    MANDATES = ("cq.C", "cq.D", "parity", "api-consume")

    def four_seats(self):
        shape = (("executor", "medium"), ("fanout", "high"), ("executor", "low"), ("fanout", "medium"))
        return [self.seat("fx-%s" % key, (key,), tier, effort) for key, (tier, effort) in zip(self.MANDATES, shape)]

    def later_round(self, reviewed=MANDATES):
        old = self.blob("src/a.cs")
        self.plant_rows(*[self.row(key, files={"src/a.cs": old}) for key in reviewed])
        self._write("src/a.cs", "edited\n")
        return old

    def test_a_later_round_keeps_four_seats_and_reports_each_earlier_pair(self):
        old = self.later_round()
        result = self.plan(self.four_seats())
        plan = self.kept(result)
        self.assertEqual(len(plan["roster"]), 4)
        for seat, original in zip(plan["roster"], self.four_seats()):
            self.assertEqual(seat, dict(original, uncoveredFiles=["src/a.cs"],
                earlierReviewedBlobs=[{"mandate": original["mandates"][0], "file": "src/a.cs", "blob": old}]))
        self.assertIs(plan["laterRound"], True)
        self.assertNotIn("seats became", result.stderr.decode())

    def test_later_round_facts_include_only_the_seats_own_missing_pairs(self):
        self._write("src/b.cs", "b\n")
        self._commit("add b")
        old = {p: self.blob(p) for p in ("src/a.cs", "src/b.cs")}
        self._write("src/a.cs", "changed a\n")
        self._write("src/b.cs", "changed b\n")
        self.plant_rows(self.row("cq.C", files=old),
                        self.row("cq.C", files={"src/b.cs": self.blob("src/b.cs")}, tier="executor", effort="low"))
        seats = [self.seat("fx-deep", ("cq.C",), "executor", "high"),
                 self.seat("fx-shallow", ("cq.C",), "executor", "low")]
        plan = self.kept(self.plan(seats, files=("src/a.cs", "src/b.cs")))
        self.assertTrue(plan["laterRound"])
        self.assertEqual(plan["roster"][0]["earlierReviewedBlobs"], [
            {"mandate": "cq.C", "file": "src/a.cs", "blob": old["src/a.cs"]},
            {"mandate": "cq.C", "file": "src/b.cs", "blob": old["src/b.cs"]}])
        self.assertEqual(plan["roster"][1]["earlierReviewedBlobs"], [
            {"mandate": "cq.C", "file": "src/a.cs", "blob": old["src/a.cs"]}])

    def test_ignore_ledger_keeps_four_seats_on_a_later_round(self):
        self.later_round()
        plan = self.kept(self.plan(self.four_seats(), "--ignore-ledger"))
        self.assertEqual([s["key"] for s in plan["roster"]], ["fx-%s" % key for key in self.MANDATES])
        self.assertNotIn("laterRound", plan)

    def test_a_first_round_file_keeps_per_seat_output(self):
        plan = self.kept(self.plan(self.four_seats()))
        self.assertEqual(len(plan["roster"]), 4)
        self.assertNotIn("laterRound", plan)

    def test_one_owed_pair_with_no_earlier_blob_keeps_per_seat_output(self):
        self.later_round(reviewed=self.MANDATES[:3])
        plan = self.kept(self.plan(self.four_seats()))
        self.assertEqual(len(plan["roster"]), 4)
        self.assertNotIn("laterRound", plan)

    def test_seats_of_different_registries_stay_separate(self):
        self.later_round()
        seats = [self.seat("fx-a", ("cq.C",)), self.seat("sa-b", ("cq.D",)), self.seat("fx-c", ("parity",)),
                 self.seat("sa-d", ("api-consume",))]
        plan = self.kept(self.plan(seats))
        self.assertEqual([s["key"] for s in plan["roster"]], [s["key"] for s in seats])

    def test_explicit_joined_keys_keep_roster_order(self):
        self.later_round()
        seats = [self.seat("fx-a+fx-b", ("cq.C", "cq.D")), self.seat("fx-b+fx-c", ("parity",))]
        plan = self.kept(self.plan(seats))
        self.assertEqual([s["key"] for s in plan["roster"]], [s["key"] for s in seats])
        self.assertEqual(plan["roster"][0]["mandates"], ["cq.C", "cq.D"])


class ClauseMutantTests(LedgerFixture):
    """Each scenario: the real tool prints the pair, and the tool with only that clause disabled does not."""

    def scenario_1(self):
        self.plant_rows(self.row())
        path = self.claude / "commands/checklists/code_quality.md"
        path.write_text(path.read_text(encoding="utf-8").replace("- c1", "- c1 edited"), encoding="utf-8")
        return self.uncovered

    def scenario_2(self):
        self.plant_rows(self.row(outcome="incomplete"))
        return self.uncovered

    def scenario_3(self):
        self.plant_rows(self.row())
        self._write("src/a.cs", "changed\n")
        return self.uncovered

    def scenario_4(self):
        self.branch()
        self._write("src/a.cs", "feat\n")
        self._commit("feat edit")
        reviewed = self.row(files={"src/a.cs": self.blob("src/a.cs")})
        self.checkout("main")
        self._write("src/a.cs", "main\n")
        self._commit("main edit")
        self.checkout("feat")
        self.merge("main", "M", resolved={"src/a.cs": "feat\n"})
        self.plant_rows(reviewed)
        return self.uncovered

    def scenario_5(self):
        self.plant_rows(self.row())
        self.escape_log.write_text(json.dumps({"ts": "2026-10-01T00:00:00Z", "paths": ["src/a.cs"]}) + "\n",
                                   encoding="utf-8")
        return self.uncovered

    def scenario_6(self):
        self.plant_rows(self.row(seatMandates=["cq.C"] + ["k%d" % n for n in range(6)]))
        return lambda tool=None: self.uncovered(tool=tool, roster=[self.seat("one", ("cq.C",))])

    def scenario_7(self):
        self.plant_rows(self.row("api-consume", mergeBase="0" * 40))
        return lambda tool=None: self.uncovered(("src/a.cs",), "api-consume", tool=tool)

    def scenario_8(self):
        self.plant_rows(self.row(subject="plan:p"))
        return self.uncovered

    def scenario_9(self):
        self.plant_registry("claude-haiku-5-5")
        self.plant_rows(self.row(model="claude-haiku-5-5", tier="architect"))
        return self.uncovered

    def expected(self, clause):
        """Clause 3's pair names the blob its row reviewed, because only content keeps the row from covering."""
        after = "\tafter:" + Repo.blob_sha(b"one\n") if clause == 3 else ""
        return self.pair("api-consume" if clause == 7 else "cq.C") + after

    def _fresh(self, name):
        fresh = type(self)(name)
        fresh.setUp()
        self.addCleanup(fresh.temp.cleanup)
        return fresh

    def test_each_clause_scenario_prints_its_pair_and_goes_silent_on_its_own_mutant(self):
        out = Path(tempfile.mkdtemp(prefix="rl_mutants_"))
        self.addCleanup(shutil.rmtree, out, True)
        for clause in range(1, 10):
            with self.subTest(clause=clause):
                fresh = self._fresh("test_each_clause_scenario_prints_its_pair_and_goes_silent_on_its_own_mutant")
                run = getattr(fresh, "scenario_%d" % clause)()
                self.assertEqual(fresh.pairs(run()), [fresh.expected(clause)])
                self.assertEqual(fresh.pairs(run(tool=write_mutant(TOOL, clause, out))), [],
                                 "disabling clause %d must stop the pair printing" % clause)


class RangeCommitsTests(unittest.TestCase):
    def test_shared_enumeration_preserves_parse_and_remote_flags(self):
        module = load_module(TOOL)
        a, b, c, z = "a" * 40, "b" * 40, "c" * 40, "0" * 40
        output = ("ignored before header\n@@first\tfirst subject\twith tab\n\n"
                  f":100644 100644 {a} {b} M\tsrc/a.cs\n:100644 000000 {a} {z} D\t.claude/rules/old.md\n"
                  f"@@second\tsecond subject\n\n:000000 100644 {z} {b} A\t.claude/rules/new.md\n"
                  f"@@merge\tno authored files\n@@resolved\tmerge resolution\n\n"
                  f"::100644 100644 100644 {a} {b} {c} MM\t.claude/rules/both.md\n")
        expected = [
            {"sha": "first", "subject": "first subject\twith tab",
             "files": ["src/a.cs", ".claude/rules/old.md"]},
            {"sha": "second", "subject": "second subject", "files": [".claude/rules/new.md"]},
            {"sha": "merge", "subject": "no authored files", "files": []},
            {"sha": "resolved", "subject": "merge resolution", "files": [".claude/rules/both.md"]},
        ]
        repo = Path("planted-repo")
        with patch.object(module, "_git", return_value=Result(0, output, "")) as git:
            self.assertEqual(module.range_commits(repo, "base", "tip"), expected)
            git.assert_called_once_with(repo, [
                "log", "--reverse", "--cc", "--no-renames", "--diff-filter=ACDMR",
                "--raw", "--no-abbrev", "--format=@@%H\t%s", "base..tip"])

    def test_shared_enumeration_fails_closed_on_git_error(self):
        module = load_module(TOOL)
        with patch.object(module, "_git", return_value=Result(128, "", "fatal: missing ref")):
            with self.assertRaisesRegex(module.ToolError, "cannot list commits in base..tip: fatal: missing ref"):
                module.range_commits(Path("planted-repo"), "base", "tip")

    def test_owed_range_uses_shared_enumeration(self):
        module = load_module(TOOL)
        repo = Path("planted-repo")
        with patch.object(module, "range_commits", return_value=[], create=True) as enumerate_range,                 patch.object(module, "_git", return_value=Result(0, "", "")) as git:
            self.assertEqual(module.owed_range(repo, "base", "tip", ["cq.C"]), [])
            enumerate_range.assert_called_once_with(repo, "base", "tip")
            git.assert_not_called()

    def test_owed_has_no_doctrine_debt_branch(self):
        """The ledger owes per-commit text coverage only: no `--doctrine` flag, no premise pairs, no path filter."""
        import inspect
        module = load_module(TOOL)
        with self.assertRaisesRegex(module.ToolError, "unrecognized arguments: --doctrine"):
            module._build_parser().parse_args(["owed", "--doctrine"])
        self.assertFalse(hasattr(module, "uncovered_premises"))
        self.assertNotIn("paths", inspect.signature(module.owed_range).parameters)
        for function in (module.owed_range, module.range_commits):
            self.assertNotIn("exclude_remotes", inspect.signature(function).parameters, function.__name__)


class OwedTests(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.r.set_upstream(self.head())
        self.keys = {"HARNESS_REVIEW_CANDIDATE_KEYS": "cq.C"}

    def owed(self, roster=True):
        """`owed` under a roster seating one executor/high seat per catalog key; `roster=False` passes none."""
        args = []
        if roster:
            path = self.root / "owed_roster.json"
            path.write_text(json.dumps([self.seat(key, (key,)) for key in CATALOG]), encoding="utf-8")
            args = ["--roster", str(path)]
        result = self.run_tool("owed", "--base-ref", "main", *args, extra_env=self.keys)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return [line for line in result.stdout.decode().splitlines() if line.strip()]

    def test_an_unpushed_commit_with_no_row_is_listed(self):
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        lines = self.owed()
        self.assertEqual(len(lines), 1)
        self.assertIn(self.head()[:7], lines[0])
        self.assertIn("C new file", lines[0])
        self.assertNotIn("no upstream", lines[0])

    def test_a_pushed_head_lists_nothing(self):
        self.assertEqual(self.owed(), [])

    def test_a_commit_whose_files_a_row_covers_is_not_listed(self):
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        self.plant_rows(self.row(files={"src/b.cs": self.blob("src/b.cs")}))
        self.assertEqual(self.owed(), [])

    def test_with_no_roster_a_commit_only_panel_rows_cover_is_listed_depth_unchecked(self):
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        self.plant_rows(self.row(files={"src/b.cs": self.blob("src/b.cs")}))
        lines = self.owed(roster=False)
        self.assertEqual(len(lines), 1)
        self.assertIn("C new file", lines[0])
        self.assertIn("depth-unchecked", lines[0])

    def test_a_row_that_covers_only_another_mandate_leaves_it_listed(self):
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        self.plant_rows(self.row("cq.D", files={"src/b.cs": self.blob("src/b.cs")}))
        self.assertEqual(len(self.owed()), 1)

    def merged_pair(self, feat_file, feat_text, main_file, main_text, resolved=None):
        self.branch("feat")
        self._write(feat_file, feat_text)
        self._commit("feat work")
        self.checkout("main")
        self._write(main_file, main_text)
        self._commit("main work")
        self.checkout("feat")
        return self.merge("main", "merge main", resolved)

    def test_an_authored_merge_resolution_nobody_reviewed_is_listed(self):
        merge = self.merged_pair("src/a.cs", "feat\n", "src/a.cs", "main\n", {"src/a.cs": "resolved\n"})
        self.assertTrue(any(merge[:7] in line for line in self.owed()))

    def test_a_merge_that_authored_nothing_is_not_listed(self):
        merge = self.merged_pair("src/f.cs", "f\n", "src/m.cs", "m\n")
        lines = self.owed()
        self.assertTrue(lines)
        self.assertFalse(any(merge[:7] in line for line in lines))

    def test_a_cat_file_failure_exits_2_rather_than_listing_nothing(self):
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        real = load_module(TOOL)._git

        def broken(repo, args, stdin=None):
            if args[:2] == ["cat-file", "--batch-check"]:
                return subprocess.CompletedProcess(args, 128, b"", b"fatal: broken")
            return real(repo, args, stdin)
        result = self.run_tool("owed", "--base-ref", "main", extra_env=self.keys, attrs={"_git": broken})
        self.assertEqual(result.returncode, 2, result.stdout.decode() + result.stderr.decode())
        self.assertEqual(result.stdout.decode().strip(), "")

    def test_a_commit_listing_that_fails_exits_2_naming_why(self):
        self.r.unset_upstream()
        result = self.run_tool("owed", "--base-ref", "no-such-ref", extra_env=self.keys)
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn("cannot list commits", result.stderr.decode())

    def test_with_no_upstream_every_uncovered_commit_is_listed_and_the_line_says_so(self):
        self.r.unset_upstream()
        self.branch("feat")
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        lines = self.owed()
        self.assertTrue(lines)
        self.assertTrue(all("no upstream" in line for line in lines))


class KeysTests(LedgerFixture):
    def module(self):
        return load_module(TOOL)

    def test_keys_lists_the_planted_catalog(self):
        result = self.run_tool("keys")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().split(), sorted(CATALOG))

    def test_an_empty_project_file_still_accepts_the_coding_layer_keys(self):
        skill_dir = self.claude / "skills" / "project_subsystems"
        (skill_dir / "adaptation.json").write_text(json.dumps({"review_scope": REVIEW_SCOPE}), encoding="utf-8")
        shutil.copy(LIVE_CLAUDE / "skills" / "project_subsystems" / "adaptation.coding.defaults.json", skill_dir)
        keys = self.run_tool("keys").stdout.decode().split()
        self.assertIn("architecture-sweep", keys)
        self.assertIn("cq.C", keys)
        self.assertIsNotNone(self.module().rule_hash("architecture-sweep", self.claude))

    def test_rule_hash_changes_with_its_section_only(self):
        mod = self.module()
        before = mod.rule_hash("cq.C", self.claude)
        self.assertEqual(before, self.rule_hash("cq.C"))
        other = self.claude / "commands/checklists/code_quality.md"
        original = other.read_text(encoding="utf-8")
        other.write_text(original.replace("- d1", "- d1 x"), encoding="utf-8")
        self.assertEqual(mod.rule_hash("cq.C", self.claude), before)
        other.write_text(original.replace("- c1", "- c1 x"), encoding="utf-8")
        self.assertNotEqual(mod.rule_hash("cq.C", self.claude), before)
        self.assertEqual(mod.rule_hash("cq.C", self.claude), self.rule_hash("cq.C"))

    def check(self, lens_sets, catalog=None):
        return self.module().check_keys(catalog or CATALOG, self.claude, lens_sets)

    def test_check_passes_a_clean_declaration(self):
        self.assertEqual(self.check({"review": [("seat-a", ["cq.C"]), ("seat-b", ["cq.D"])],
                                     "session_audit": [("sa-a", ["cq.C", "cq.D"])]}), [])

    def test_check_fails_an_undeclared_lens(self):
        problems = self.check({"review": [("seat-a", ["cq.C"]), ("seat-b", [])]})
        self.assertTrue(any("seat-b" in p for p in problems))

    def test_check_fails_an_unknown_key(self):
        problems = self.check({"review": [("seat-a", ["cq.ZZ"])]})
        self.assertTrue(any("cq.ZZ" in p for p in problems))

    def test_check_fails_a_key_two_lenses_of_one_registry_declare(self):
        problems = self.check({"review": [("seat-a", ["cq.C"]), ("seat-b", ["cq.C"])]})
        self.assertTrue(any("cq.C" in p and "seat-a" in p and "seat-b" in p for p in problems))

    def test_check_accepts_the_same_key_in_two_registries(self):
        self.assertEqual(self.check({"review": [("seat-a", ["cq.C"])], "session_audit": [("sa-a", ["cq.C"])]}), [])

    def test_check_fails_a_catalog_row_that_still_names_cites(self):
        catalog = dict(CATALOG, **{"cq.X": {"home": "commands/pr_ready.md#Step 2", "cites": ["rules/design_litmus.md"],
                                            "interaction": False}})
        problems = self.check({"review": [("seat-a", ["cq.X"])]}, catalog=catalog)
        self.assertTrue(any("cq.X" in p and "preload" in p for p in problems), problems)

    def test_a_mandates_doctrine_is_every_file_its_declaring_lenses_preload(self):
        self.assertEqual(self.module().mandate_doctrine(self.claude), DOCTRINE)
        agents = self.claude / "commands/agents"
        (agents / "session_audit_agents.md").write_text(
            "# SA\n\n### sa-fixture — x\n\nmandates: cq.D, cq.C\npreload: commands/pr_ready.md\n\n```\np\n```\n",
            encoding="utf-8")
        self.assertEqual(self.module().mandate_doctrine(self.claude),
                         {"cq.C": ["commands/pr_ready.md"], "cq.D": ["commands/pr_ready.md", "rules/design_litmus.md"]})

    def test_the_production_catalog_derives_doctrine_for_the_architecture_sweep_and_every_file_resolves(self):
        mod = self.module()
        live = TOOLS.parent
        doctrine = mod.mandate_doctrine(live)
        self.assertIn("skills/architecture_philosophy/SKILL.md", doctrine.get("architecture-sweep", []))
        catalog = mod.load_catalog(live)
        self.assertTrue(catalog)
        self.assertFalse([key for key, spec in catalog.items() if "cites" in spec])
        self.assertEqual([p for p in mod.check_keys(catalog, live, {}) if "cites" in p or "preload" in p], [])

    def test_check_fails_a_home_that_does_not_resolve(self):
        catalog = dict(CATALOG, **{"cq.X": {"home": "commands/nowhere.md#Heading", "interaction": False}})
        problems = self.check({"review": [("seat-a", ["cq.X"])]}, catalog=catalog)
        self.assertTrue(any("cq.X" in p and "nowhere" in p for p in problems))
        catalog = dict(CATALOG, **{"cq.Y": {"home": "commands/pr_ready.md#No such heading",
                                            "interaction": False}})
        problems = self.check({"review": [("seat-a", ["cq.Y"])]}, catalog=catalog)
        self.assertTrue(any("cq.Y" in p for p in problems))


ENGINE_PER_AGENT_TWO = {"key": "seat-code-reviewer", "count": 0, "status": "completed",
                        "stoppedAt": "exhausted-leads", "basis": "read", "toolsUsed": 2, "gaps": 0,
                        "mandates": {"cq.C": "clean", "cq.D": "not-reached"},
                        "checkedTargets": ["Read:src/a.cs"]}


ENGINE_RUN = Path(__file__).resolve().parent / "_engine_run.js"


class RecordBase(LedgerFixture):
    def engine_return(self, per_agent, findings=(), rows_omitted=0):
        return {"findings": list(findings), "counts": {}, "perAgent": per_agent,
                "delivery": {"coverage": {"rowsOmitted": rows_omitted}}}

    def save(self, payload, name="return.json"):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def roster(self, *seats):
        path = self.root / "roster_record.json"
        path.write_text(json.dumps(list(seats)), encoding="utf-8")
        return path


class AcceptedExecutorPipelineTests(RecordBase):
    def test_accepted_executor_reaches_full_plan_combined_record_and_metrics(self):
        sys.path.insert(0, str(TOOLS))
        import task_record
        import review_result
        import post_check
        import orchestration_metrics
        from unittest.mock import patch
        from types import SimpleNamespace
        env = dict(self.env(), HARNESS_TASK_RECORD_DIR=str(self.root / "tasks"), CLAUDE_PROJECT_DIR=str(self.repo))
        subprocess.run(["git", "-C", str(self.repo), "read-tree", "HEAD"], check=True)
        actor = self.repo / "executor.record.json"
        actor.write_text(json.dumps({"transport": "codex", "attestedModel": "gpt-6.1-sol"}), encoding="utf-8")
        with patch.dict(os.environ, env):
            task_record.open_record("trace", "s1", "executor")
            task_record.add_job("trace", str(actor), "exec", "sidecar", "launched", subject=["src/a.cs"])
            task_record.add_job("trace", str(actor), "exec", "sidecar", "done")
            task_record.add_job("trace", str(actor), "exec", "sidecar", "accepted", role="executor", subject=["src/a.cs"])
        seats = self.roster(self.seat("runtime-seat", ("cq.C",), tier="architect"))
        result = self.run_tool("plan", "--stage", "candidate", "--subject", "session:s1", "--task", "trace",
                               "--roster", str(seats), "--files", "src/a.cs", "--base-ref", "main", extra_env=env)
        planned = self.kept(result)
        selection = planned["reviewerSelection"]
        self.assertEqual([row["group"] for row in selection["primary"]], ["openai"])
        self.assertEqual(selection["primary"][0]["role"], "executor")
        design = self.run_tool("plan", "--stage", "design", "--subject", "session:s1", "--task", "trace",
            "--roster", str(seats), "--files", "src/a.cs", "--base-ref", "main", extra_env=env)
        self.assertEqual(self.kept(design)["reviewerSelection"]["primary"], [],
                         "slice 1 cannot relabel an executor as a design author")
        self.assertEqual(selection["revision"]["files"], {"src/a.cs": self.blob("src/a.cs")})
        plan_path = self.save(planned, "full-plan.json")
        runtime = {"exitCode": 0, "schemaValid": True, "engagement": "proven", "transport": "anthropic",
                   "modelVersion": "claude-opus-5-5", "tier": "architect", "effort": "high"}
        prepared = review_result.ingest([{"label": "runtime-seat", "mandates": ["cq.C"], "record": runtime,
            "payload": {"structured_output": {"findings": [], "gaps": [], "checked": {
                "toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads", "basis": "checked",
                "mandates": {"cq.C": "clean"}}}}}])
        prepared_path = self.save(prepared, "runtime.json")
        args = SimpleNamespace(stage="candidate", subject="session:s1", roster=str(plan_path), task="trace",
            blobs=str(self.save({"src/a.cs": self.blob("src/a.cs")}, "blobs.json")), out=str(self.root / "check"),
            premise_commits=None, head=None, scope_json=None, no_new_decision=False)
        src = post_check.Source()
        src.record = ("--sidecar-result", str(prepared_path))
        def record(_cwd, *argv):
            done = self.run_tool(*argv, extra_env=env)
            return done.returncode, done.stdout.decode(), done.stderr.decode()
        with patch.object(post_check, "ledger", side_effect=record) as call:
            code, _out, err = post_check.record_panel(self.repo, args, src, [])
        self.assertEqual(code, 0, err)
        self.assertEqual(call.call_count, 1)
        row = self.rows()[-1]
        self.assertEqual(row["reviewerSelection"], selection)
        self.assertEqual((row["model"], row["modelObserved"], row["identityBasis"]),
                         ("claude-opus-5-5", True, "modelVersion"))
        metric = {"source": "workflow", "workflow": "review-fanout", "run": row["id"].split(":")[0],
                  "label": "review:runtime-seat"}
        orchestration_metrics.attach_review_ledger([metric], [row])
        self.assertEqual(metric["reviewer_selection"], selection)
        self.assertEqual(metric["reviewer_model"], row["model"])


class DerivedRecordTests(RecordBase):
    def test_fold_requires_exact_origin_map(self):
        args = ("--inline", "fold", "--mandate", "cq.C", "--outcome", "clean", "--files", "src/a.cs")
        missing = self.record(*args)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--from", missing.stderr.decode())
        wrong = self.record(*args, "--from", str(self.save({"other": None})))
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("--files", wrong.stderr.decode())

    def test_inline_origin_and_recorded_blob_are_readable_objects(self):
        old = self.blob("src/a.cs")
        self.plant_rows(self.row())
        self._write("src/a.cs", "uncommitted fix\n")
        result = self.record("--inline", "fold", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--from", str(self.save({"src/a.cs": old})))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        row = self.rows()[-1]
        self.assertEqual(row["from"], {"src/a.cs": old})
        obj = subprocess.run(["git", "cat-file", "blob", row["files"]["src/a.cs"]], cwd=self.repo,
                             capture_output=True)
        self.assertEqual(obj.returncode, 0, obj.stderr.decode())
        self.assertEqual(obj.stdout, b"uncommitted fix\n")

    def test_recorded_override_blob_is_written_even_when_not_yet_an_object(self):
        self._write("src/a.cs", "override bytes\n")
        blob = self.blob("src/a.cs")
        result = self.record("--script", "check", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--blobs", str(self.save({"src/a.cs": blob})))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        obj = subprocess.run(["git", "cat-file", "blob", blob], cwd=self.repo, capture_output=True)
        self.assertEqual(obj.returncode, 0, obj.stderr.decode())
        self.assertEqual(obj.stdout, b"override bytes\n")

    def test_panel_origin_uses_covered_predecessor_at_roster_depth(self):
        old = self.blob("src/a.cs")
        self.plant_rows(self.row())
        self._write("src/a.cs", "reviewed delta\n")
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean"},
                                                  effort="medium", checkedTargets=["Read:src/a.cs"])]))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster",
                             str(self.roster(self.seat("seat-code-reviewer"))))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[-1]["from"], {"src/a.cs": old})
        self.assertEqual(self.pairs(self.uncovered()), [])


class RecordTests(RecordBase):
    def test_live_seats_source_records_without_a_completed_run_record(self):
        directory = self.root / 'sessions/sess1/subagents/workflows/wf_live'
        directory.mkdir(parents=True)
        body = {'checked': {'toolsUsed': ['Read:src/a.cs'], 'stoppedAt': 'nothing-in-scope',
                            'basis': 'cq.C: clean', 'mandates': {'cq.C': 'clean'}}, 'findings': [], 'gaps': []}
        events = [{'type': 'started', 'agentId': 'a1', 'label': 'review:seat-code-reviewer', 'phase': 'Review'},
                  {'type': 'result', 'agentId': 'a1', 'result': body}]
        (directory / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (directory / 'agent-a1.meta.json').write_text(json.dumps({'model': 'served-live', 'agentType': 'workflow-subagent'}))
        seats = self.save({'review:seat-code-reviewer': {'model': 'requested-live', 'effort': 'high',
                           'tier': 'executor', 'mandates': ['cq.C'], 'mustRead': []}}, 'seats.json')
        self.assertFalse((directory.parents[2] / 'workflows/wf_live.json').exists())
        result = self.record('--seats-file', str(seats), '--workflow-dir', str(directory), '--files', 'src/a.cs')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        row = self.rows()[0]
        self.assertEqual((row['seat'], row['model'], row['modelObserved'], row['effort'], row['tier']),
                         ('seat-code-reviewer', 'served-live', True, 'high', 'executor'))
        self.assertEqual(row['outcome'], 'clean')
        self.assertEqual(row['files'], {'src/a.cs': self.blob('src/a.cs')})

    def test_a_split_seat_records_its_roster_mandates(self):
        # Captured live: wf_58595384-a21 split one lens into seats sa-harness-quality-a..e; the seats file named no
        # mandate, so each seat recorded under its own key, which no catalog holds, and the panel wrote no row.
        directory = self.root / 'sessions/sess1/subagents/workflows/wf_split'
        directory.mkdir(parents=True)
        body = {'checked': {'toolsUsed': ['Read:src/a.cs'], 'stoppedAt': 'nothing-in-scope', 'basis': 'read src/a.cs',
                            'mandates': {'seat-split-b': 'clean'}}, 'findings': [], 'gaps': []}
        events = [{'type': 'started', 'agentId': 'a1', 'label': 'review:seat-split-b', 'phase': 'Review'},
                  {'type': 'result', 'agentId': 'a1', 'result': body}]
        (directory / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (directory / 'agent-a1.meta.json').write_text(json.dumps({'model': 'served-live', 'agentType': 'workflow-subagent'}))
        seats = self.save({'review:seat-split-b': {'model': 'requested-live', 'effort': 'high',
                           'tier': 'executor', 'mandates': [], 'mustRead': []}}, 'seats.json')
        result = self.record('--seats-file', str(seats), '--workflow-dir', str(directory), '--files', 'src/a.cs',
                             '--roster', str(self.roster(self.seat('seat-split-b'))))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn('not in the catalog', result.stderr.decode())
        self.assertEqual([(r['seat'], r['mandate'], r['outcome']) for r in self.rows()], [('seat-split-b', 'cq.C', 'clean')])

    def test_record_deleted_subject_writes_commit_keyed_tombstone(self):
        self.r.files.pop("src/a.cs")
        (self.repo / "src/a.cs").unlink()
        deleted = self._commit("delete subject")
        self._write("src/b.cs", "later\n")
        self._commit("later unrelated change")
        result = self.record("--script", "check", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "never-existed.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[0]["files"], {"src/a.cs": "deleted:" + deleted})
        self.assertIn("file not found, not recorded: never-existed.cs", result.stderr.decode())

    def test_record_whose_every_named_file_fails_to_resolve_refuses(self):
        # A row whose every named path is absent covers nothing; recording it silently reads as coverage.
        result = self.record("--script", "check", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "gone/src/a.cs", "never-existed.cs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("none of the 2 named files", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_result_row_fields_match_the_schema(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "findings"},
                                                 count=1, checkedTargets=["Read:src/a.cs", "Grep:src"])]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster),
                             "--author-model", "author-x", "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = {r["mandate"]: r for r in self.rows()}
        self.assertEqual(sorted(rows), ["cq.C", "cq.D"])
        row = rows["cq.C"]
        for field in ("v", "id", "ts", "stage", "subject", "mandate", "ruleHash", "head", "mergeBase", "reviewer",
                      "family", "model", "modelObserved", "tier", "effort", "seat", "seatMandates", "files",
                      "outcome", "findings", "degraded", "authorModel", "task", "session", "noNewDecision"):
            self.assertIn(field, row)
        self.assertEqual(row["v"], 1)
        self.assertEqual(row["outcome"], "clean")
        self.assertEqual(rows["cq.D"]["outcome"], "findings")
        self.assertEqual(row["reviewer"], "panel")
        self.assertEqual(row["seat"], "seat-code-reviewer")
        self.assertEqual(row["seatMandates"], ["cq.C", "cq.D"])
        self.assertEqual(row["ruleHash"], self.rule_hash("cq.C"))
        self.assertEqual(row["head"], self.head())
        self.assertEqual(row["files"], {"src/a.cs": self.blob("src/a.cs")})
        self.assertEqual((row["tier"], row["effort"]), ("executor", "high"))
        self.assertEqual((row["authorModel"], row["task"]), ("author-x", "t1"))
        self.assertIs(row["degraded"], False)
        self.assertEqual(row["id"].split(":")[-2:], ["seat-code-reviewer", "cq.C"])

    def test_a_ledger_lock_that_cannot_be_taken_writes_no_row(self):
        sys.path.insert(0, str(TOOLS.parent / "hooks"))
        try:
            import _file_lock
        finally:
            sys.path.remove(str(TOOLS.parent / "hooks"))
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        held = _file_lock.acquire(str(self.ledger) + ".lock", 2.0)
        self.assertIsNotNone(held)
        try:
            result = self.record("--script", "worklog-status", "--mandate", "cq.C", "--outcome", "clean",
                                 "--files", "src/a.cs")
        finally:
            _file_lock.release(held)
        self.assertEqual(result.returncode, 1, result.stdout.decode() + result.stderr.decode())
        self.assertIn("cannot append", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_head_records_the_reviewed_commit_blob_not_the_live_worktree(self):
        reviewed = self.head()
        reviewed_blob = self.blob("src/a.cs")
        self._write("src/a.cs", "two\n")
        self._commit("C")
        self._write("src/a.cs", "three\n")
        result = self.record("--script", "worklog-status", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "src/gone.cs", "--head", reviewed)
        self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())
        row = self.rows()[0]
        self.assertEqual(row["head"], reviewed)
        self.assertEqual(row["files"], {"src/a.cs": reviewed_blob})
        self.assertIn("file not found, not recorded: src/gone.cs", result.stderr.decode())

    def test_a_head_that_names_no_commit_exits_2(self):
        result = self.record("--script", "worklog-status", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--head", "deadbeef")
        self.assertEqual(result.returncode, 2, result.stdout.decode() + result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_blobs_records_the_bytes_the_review_read_not_the_folded_worktree(self):
        reviewed_blob = self.blob("src/a.cs")
        self._write("src/a.cs", "folded\n")
        blobs = self.save({"src/a.cs": reviewed_blob}, name="blobs.json")
        result = self.record("--script", "worklog-status", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--blobs", str(blobs))
        self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())
        self.assertEqual(self.rows()[0]["files"], {"src/a.cs": reviewed_blob})

    def test_blobs_that_is_not_a_path_to_sha_map_exits_2(self):
        blobs = self.save({"src/a.cs": None}, name="blobs.json")
        result = self.record("--script", "worklog-status", "--mandate", "cq.C", "--outcome", "clean",
                             "--files", "src/a.cs", "--blobs", str(blobs))
        self.assertEqual(result.returncode, 2, result.stdout.decode() + result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_recorded_clean_row_then_covers_the_file(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "clean"})]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
        seated = [self.seat("seat-code-reviewer", ("cq.C", "cq.D"))]
        self.assertEqual(self.pairs(self.uncovered(mandates="cq.C", roster=seated)), [])

    def test_a_roster_seat_whose_engine_row_carries_no_mandates_exits_1_and_writes_no_row(self):
        # The engine maps a seat's key to mandates through the lens catalog: a key naming no lens returns
        # `mandates: {}`, which would drop the seat's coverage silently.
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, key="adhoc-seat", mandates={})]))
        roster = self.roster(self.seat("adhoc-seat", ("cq.C", "cq.D")))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
        self.assertEqual(result.returncode, 1, result.stdout.decode() + result.stderr.decode())
        self.assertIn("adhoc-seat", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_seat_absent_from_the_roster_with_no_mandates_is_named_on_stderr(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, key="adhoc-seat", mandates={})]))
        roster = self.roster(self.seat("cq.C+cq.D", ("cq.C", "cq.D")))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
        self.assertIn("adhoc-seat", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_seat_with_no_mandates_and_no_roster_is_named_on_stderr(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, key="adhoc-seat", mandates={})]))
        result = self.record("--result", str(ret), "--files", "src/a.cs")
        self.assertIn("adhoc-seat returned no mandate outcomes", result.stderr.decode())

    def test_engagement_splits_clean_into_a_covering_row_and_an_incomplete_row(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "not-reached"},
                                                 checkedTargets=["Read:src/a.cs"])]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "src/b.cs", "--roster", str(roster))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        cq_c = [r for r in self.rows() if r["mandate"] == "cq.C"]
        self.assertEqual(sorted((r["outcome"], tuple(r["files"])) for r in cq_c),
                         [("clean", ("src/a.cs",)), ("incomplete", ("src/b.cs",))])
        cq_d = [r for r in self.rows() if r["mandate"] == "cq.D"]
        self.assertEqual({r["outcome"] for r in cq_d}, {"not-reached"})
        seated = [self.seat("seat-code-reviewer", ("cq.C", "cq.D"))]
        self.assertEqual(self.pairs(self.uncovered(("src/a.cs", "src/b.cs"), "cq.C", roster=seated)),
                         [self.pair("cq.C", "src/b.cs")])

    def test_a_directory_target_engages_no_file_beneath_it(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, checkedTargets=["Grep:src"],
                                                 mandates={"cq.C": "clean", "cq.D": "clean"})]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
        self.assertEqual({(r["mandate"], r["outcome"], r["id"].endswith("#unengaged"), tuple(r["files"]))
                          for r in self.rows()},
                         {("cq.C", "incomplete", True, ("src/a.cs",)), ("cq.D", "incomplete", True, ("src/a.cs",))})

    def test_a_ranged_or_annotated_target_engages_its_file(self):
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        for target in ("Read:src/a.cs:1-225", "Read:src/a.cs:12", "Read:src/a.cs:1-5,9-12", "Read:src/a.cs (full)",
                       "Read:src/a.cs:3-9 (tail)"):
            with self.subTest(target=target):
                self.reset_ledger()
                ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, checkedTargets=[target],
                                                         mandates={"cq.C": "clean", "cq.D": "clean"})]))
                result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({(r["mandate"], r["outcome"]) for r in self.rows()},
                                 {("cq.C", "clean"), ("cq.D", "clean")})

    def test_a_findings_mandate_covers_only_its_engaged_files(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        cited = [{"id": "F%d" % n, "agent": "seat-code-reviewer", "file": path, "description": "d"}
                 for n, path in enumerate(("src/a.cs:1", "src/a.cs:2", "src/b.cs:1"), 1)]
        ret = self.engine_return([dict(ENGINE_PER_AGENT_TWO, count=3, mandates={"cq.C": "findings", "cq.D": "clean"},
                                       checkedTargets=["Read:src/a.cs"])], cited)
        ret["sources"] = [{"id": f["id"], "lens": "seat-code-reviewer", "index": n, "finding": f}
                          for n, f in enumerate(cited)]
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        result = self.record("--result", str(self.save(ret)), "--files", "src/a.cs", "src/b.cs",
                             "--roster", str(roster))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        cq_c = sorted((r["id"].endswith("#unengaged"), r["outcome"], r["findings"], tuple(r["files"]),
                       tuple(r["findingFiles"])) for r in self.rows() if r["mandate"] == "cq.C")
        self.assertEqual(cq_c, [(False, "findings", 3, ("src/a.cs",), ("src/a.cs", "src/b.cs")),
                                (True, "incomplete", 0, ("src/b.cs",), ("src/a.cs", "src/b.cs"))])

    def test_a_file_a_gap_names_is_not_engaged(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        for gaps, covered in ((["did not fully read src/a.cs past line 40"], ("src/b.cs",)),
                              (["did not fully read C:\\repo\\src\\a.cs"], ("src/b.cs",)),
                              ([], ("src/a.cs", "src/b.cs"))):
            with self.subTest(gaps=gaps):
                self.reset_ledger()
                ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, gaps=len(gaps), gapTexts=gaps,
                                                         checkedTargets=["Read:src/a.cs", "Read:src/b.cs"],
                                                         mandates={"cq.C": "clean", "cq.D": "clean"})]))
                result = self.record("--result", str(ret), "--files", "src/a.cs", "src/b.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                clean = {p for r in self.rows() if r["mandate"] == "cq.C" and r["outcome"] == "clean" for p in r["files"]}
                self.assertEqual(tuple(sorted(clean)), covered)

    def test_engaged_owns_every_engagement_clause(self):
        engaged = load_module(TOOL)._engaged
        names = ["src/a.cs", "src", ".claude/tests/test_x.py"]
        cases = (("a target names it, no transcript, no gap", "src/a.cs", None, [], True),
                 ("a directory target engages nothing beneath it", "src/b.cs", None, [], False),
                 ("the bound clipped the gaps", "src/a.cs", None, None, False),
                 ("the transcript shows a call on it", "src/a.cs", ["src/a.cs"], [], True),
                 ("the transcript shows no call on it", "src/a.cs", ["src/b.cs"], [], False),
                 ("a gap names it", "src/a.cs", None, ["skimmed src/a.cs"], False),
                 ("a gap names another file", "src/a.cs", None, ["skimmed src/b.cs"], True),
                 ("a gap names it by basename", ".claude/tests/test_x.py", None,
                  ["test_x.py and close_predicates.py were not read"], False),
                 ("a gap names an unrelated basename", ".claude/tests/test_x.py", None,
                  ["close_predicates.py and my_test_x.pyc were not read"], True))
        for name, path, called, gaps, expected in cases:
            with self.subTest(name):
                self.assertIs(engaged(path, names, called, gaps), expected)

    def test_a_sidecar_seat_gap_naming_a_file_leaves_it_unengaged(self):
        coverage = {"label": "sa-seat", "mandates": ["cq.C", "cq.D"], "uncoveredMandates": [], "status": "completed",
                    "valid": 0, "gaps": ["src/a.cs was only skimmed"],
                    "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads"}}
        self.assertEqual(self._sidecar(coverage), {"cq.C": "incomplete", "cq.D": "incomplete"})

    def test_a_sidecar_seat_that_read_every_line_of_a_must_read_file_engages_it(self):
        lines = len((self.repo / "src/a.cs").read_text(encoding="utf-8").splitlines())
        for name, read, expected in (("whole", [[1, lines]], "clean"), ("short", [[1, lines - 1]], "incomplete")):
            with self.subTest(name):
                self.reset_ledger()
                coverage = {"label": "sa-seat", "mandates": ["cq.C"], "uncoveredMandates": [], "status": "completed",
                            "valid": 0, "gaps": [], "mustReadEvidence": "observed",
                            "mustReadSpans": [{"path": "src/a.cs", "lines": lines, "read": read}],
                            "checked": {"toolsUsed": ["Read"], "stoppedAt": "exhausted-leads"}}
                self.assertEqual(self._sidecar(coverage), {"cq.C": expected})

    def test_a_clipped_or_short_gap_list_engages_nothing(self):
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        for count, texts in ((2, ["one gap"]), (1, ["a long gap clipped by the bound …[+40 chars]"])):
            with self.subTest(texts=texts):
                self.reset_ledger()
                ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, gaps=count, gapTexts=texts,
                                                         mandates={"cq.C": "clean", "cq.D": "clean"})]))
                result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows()}, {"incomplete"})

    def test_an_absolute_or_shell_target_engages_its_file(self):
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.repo, capture_output=True,
                             text=True, check=True).stdout.strip()
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        for target in ("Read:" + top.swapcase().replace("/", "\\") + "\\src\\a.cs",
                       "Read:" + top + "/src/a.cs",
                       "Bash:git diff main -- src/a.cs",
                       "Bash:git log -p -- " + top + "/src/a.cs"):
            with self.subTest(target=target):
                self.reset_ledger()
                ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, checkedTargets=[target],
                                                         mandates={"cq.C": "clean", "cq.D": "clean"})]))
                result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows() if r["mandate"] == "cq.C"}, {"clean"})

    def test_a_mandate_outside_the_catalog_is_refused_naming_it(self):
        for args in (("--script", "pr_scripted_checks", "--mandate", "nope-key"),
                     ("--inline", "session_audit", "--mandate", "nope-key"),
                     ("--script", "nope-key")):
            with self.subTest(args=args):
                result = self.record(*args, "--outcome", "clean", "--files", "src/a.cs")
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn("nope-key", result.stderr.decode())
                self.assertEqual(self.rows(), [])

    def test_a_seat_with_no_targets_yields_no_clean_row(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, checkedTargets=[],
                                                 mandates={"cq.C": "clean", "cq.D": "clean"})]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
        self.assertTrue(self.rows())
        self.assertNotIn("clean", {r["outcome"] for r in self.rows()})
        self.assertEqual(self.pairs(self.uncovered(mandates="cq.C")), [self.pair()])

    def _ingested(self, reply, mandates=("cq.C", "cq.D")):
        """A prepared sidecar review built by `review_result.ingest` from one `sa-seat` return."""
        sys.path.insert(0, str(TOOLS))
        try:
            import review_result
        finally:
            sys.path.remove(str(TOOLS))
        record = {"exitCode": 0, "schemaValid": True, "engagement": "proven"}
        return self.save(review_result.ingest([{"label": "sa-seat", "mandates": list(mandates),
                                                "payload": {"structured_output": reply}, "record": record}]),
                         "prepared.json")

    def test_a_capability_partial_seat_leaves_an_unopened_file_incomplete_and_covers_a_named_one(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        roster = self.roster(self.seat("sa-seat", ("cq.C", "cq.D")))
        finding = {"agent": "cq.D", "action": "FIX", "category": "bug", "file": "src/a.cs:1",
                   "description": "defect", "rationale": "reason"}
        cases = (("gap names the unopened file", ["Read:src/a.cs"], {"item": "src/b.cs", "reason": "capability"},
                  {("cq.C", "clean", ("src/a.cs",)), ("cq.D", "findings", ("src/a.cs",)),
                   ("cq.C", "incomplete", ("src/b.cs",)), ("cq.D", "incomplete", ("src/b.cs",))}),
                 ("a target names the file", ["Read:src/a.cs", "Read:src/b.cs"],
                  {"item": "caller trace", "reason": "capability"},
                  {("cq.C", "clean", ("src/a.cs", "src/b.cs")), ("cq.D", "findings", ("src/a.cs", "src/b.cs"))}))
        for name, targets, gap, expected in cases:
            with self.subTest(name):
                self.reset_ledger()
                reply = {"findings": [finding], "gaps": [gap],
                         "checked": {"toolsUsed": targets, "stoppedAt": "exhausted-leads", "basis": "read the diff",
                                     "mandates": {"cq.C": "clean", "cq.D": "findings"}}}
                result = self.record("--sidecar-result", str(self._ingested(reply)), "--files", "src/a.cs",
                                     "src/b.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({(r["mandate"], r["outcome"], tuple(r["files"])) for r in self.rows()}, expected)

    def test_a_recorded_capability_partial_seat_return_covers_the_file_its_targets_name(self):
        """End to end: the committed seat return runs through `review_fanout.js`, `record --result` saves its
        rows, and `uncovered` reads them."""
        self._write("src/b.cs", "b\n")
        self._commit("C")
        reply = json.loads((Path(__file__).resolve().parent / "fixtures" / "review_gap_only_seat_return.json")
                           .read_text(encoding="utf-8"))
        must_read = [{"path": "src/a.cs", "lines": 1}, {"path": "callers outside this diff", "lines": 0}]
        args = {"agents": [{"key": "sa-seat", "prompt": "Review fixture.", "model": "fixture-model",
                            "effort": "medium", "agentType": "general-purpose", "mustRead": must_read}],
                "consolidate": False, "resultMode": "full", "checker": {"model": "fixture-model", "effort": "medium"},
                "__lensMandates": {"sa-seat": ["cq.C", "cq.D"]}}
        engine = subprocess.run(["node", str(ENGINE_RUN)], capture_output=True, text=True, input=json.dumps(
            {"script": str(TOOLS.parent / "workflows" / "review_fanout.js"), "args": args, "reply": reply}))
        self.assertEqual(engine.returncode, 0, engine.stderr)
        ret = self.save(json.loads(engine.stdout))
        roster = self.roster(self.seat("sa-seat", ("cq.C", "cq.D")))
        result = self.record("--result", str(ret), "--files", "src/a.cs", "src/b.cs", "--roster", str(roster))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        seated = [self.seat("sa-seat", ("cq.C", "cq.D"))]
        self.assertEqual(self.pairs(self.uncovered(("src/a.cs",), "cq.C", roster=seated)), [])
        self.assertEqual(self.pairs(self.uncovered(("src/b.cs",), "cq.C", roster=seated)),
                         [self.pair("cq.C", "src/b.cs")])

    def test_the_engine_token_is_the_row_outcome(self):
        cases = (("partial", "incomplete", "incomplete"), ("failed", "failed", "failed"),
                 ("uncovered", "not-reached", "not-reached"), ("completed", "findings", "findings"),
                 ("partial", "clean", "clean"))
        for status, token, expected in cases:
            with self.subTest(status=status):
                self.reset_ledger()
                agent = {"key": "seat-x", "count": 1, "status": status, "stoppedAt": "exhausted-leads",
                         "mandates": {"cq.C": token}, "checkedTargets": ["Read:src/a.cs"]}
                roster = self.roster(self.seat("seat-x", ("cq.C",)))
                ret = self.save(self.engine_return([agent]))
                result = self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows()}, {expected})

    def test_an_engine_token_outside_the_outcomes_exits_2_and_writes_nothing(self):
        agent = {"key": "seat-x", "count": 0, "status": "completed", "stoppedAt": "exhausted-leads",
                 "mandates": {"cq.C": "maybe"}, "checkedTargets": ["Read:src/a.cs"]}
        roster = self.roster(self.seat("seat-x", ("cq.C",)))
        result = self.record("--result", str(self.save(self.engine_return([agent]))), "--files", "src/a.cs",
                             "--roster", str(roster))
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn("maybe", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_blocked_seat_yields_blocked(self):
        agent = {"key": "seat-x", "count": 0, "status": "uncovered", "stoppedAt": "blocked",
                 "mandates": {"cq.C": "blocked"}, "checkedTargets": []}
        roster = self.roster(self.seat("seat-x", ("cq.C",)))
        self.record("--result", str(self.save(self.engine_return([agent]))), "--files", "src/a.cs",
                    "--roster", str(roster))
        self.assertEqual({r["outcome"] for r in self.rows()}, {"blocked"})

    def test_result_with_omitted_rows_and_no_journal_exits_1_and_writes_nothing(self):
        ret = self.save(self.engine_return([ENGINE_PER_AGENT_TWO], rows_omitted=3))
        result = self.record("--result", str(ret), "--files", "src/a.cs")
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual(self.rows(), [])
        run = self.record("--run", "wf_absent", "--files", "src/a.cs")
        self.assertEqual(run.returncode, 1, run.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_bounded_return_of_eleven_seats_records_every_seat_from_coverage_seats(self):
        keys = ["seat%d" % i for i in range(11)]
        seats = [{"key": k, "status": "completed", "stoppedAt": "exhausted-leads", "mandates": {"cq.C": "clean"},
                  "checkedTargets": ["Read:src/a.cs"], "count": 0} for k in keys]
        ret = self.engine_return([dict(s) for s in seats[:10]], rows_omitted=1)
        ret["delivery"]["coverage"].update(rowsPreviewed=10, seats=seats)
        roster = self.roster(*[self.seat(k, ("cq.C",)) for k in keys])
        result = self.record("--result", str(self.save(ret)), "--files", "src/a.cs", "--roster", str(roster))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(sorted({(r["seat"], r["outcome"]) for r in self.rows()}),
                         sorted((k, "clean") for k in keys))
        self.ledger.unlink()
        ret["delivery"]["coverage"]["seats"] = seats[:10]
        short = self.record("--result", str(self.save(ret)), "--files", "src/a.cs", "--roster", str(roster))
        self.assertEqual(short.returncode, 1, "coverage.seats short of every row is not a full record")
        self.assertEqual(self.rows(), [])

    def _journal(self, name="wf_test", rows_omitted=0, result=None):
        session = self.root / "sessions" / "sess1"
        (session / "workflows").mkdir(parents=True, exist_ok=True)
        pins = {"review:seat-code-reviewer": "high tier:architect"}
        journal = {"runId": name, "workflowName": "review-fanout", "logs": ["PINS " + json.dumps(pins)],
                   "result": result or self.engine_return(
                       [dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "clean"})],
                       rows_omitted=rows_omitted),
                   "workflowProgress": [{"type": "workflow_agent", "agentId": "a1", "label": "review:seat-code-reviewer",
                                         "model": "claude-x", "state": "completed"}]}
        (session / "workflows" / (name + ".json")).write_text(json.dumps(journal), encoding="utf-8")

    def test_run_reads_the_journal_result_and_its_pins(self):
        self._journal()
        result = self.record("--run", "wf_test", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = self.rows()
        self.assertEqual({r["mandate"] for r in rows}, {"cq.C", "cq.D"})
        self.assertEqual({(r["tier"], r["effort"]) for r in rows}, {("architect", "high")})
        self.assertTrue(all(r["id"].startswith("wf_test:") for r in rows))
        self.assertTrue(all(r["modelObserved"] is False for r in rows))

    def test_run_over_a_journal_that_reports_omitted_rows_exits_1(self):
        self._journal(rows_omitted=2)
        result = self.record("--run", "wf_test", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def _bounded_return(self):
        """The engine's bounded shape: `coverage.seats` counts each seat's targets instead of listing them."""
        seat = {"key": "seat-code-reviewer", "status": "completed", "stoppedAt": "exhausted-leads",
                "mandates": {"cq.C": "clean", "cq.D": "clean"}, "checkedTargets": 1, "count": 0}
        ret = self.engine_return([], rows_omitted=1)
        ret["delivery"]["coverage"].update(rowsPreviewed=0, seats=[seat])
        return ret

    def _agent_journal(self, name, targets):
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / name
        sub.mkdir(parents=True, exist_ok=True)
        row = {"type": "result", "agentId": "a1",
               "result": {"findings": [], "checked": {"toolsUsed": targets, "stoppedAt": "exhausted-leads",
                                                      "basis": "read"}}}
        (sub / "journal.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_a_bounded_run_takes_each_seats_targets_from_its_agent_result(self):
        self._journal("wf_bounded", result=self._bounded_return())
        self._agent_journal("wf_bounded", ["Read:src/a.cs"])
        result = self.record("--run", "wf_bounded", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual({(r["mandate"], r["outcome"], tuple(r["files"])) for r in self.rows()},
                         {("cq.C", "clean", ("src/a.cs",)), ("cq.D", "clean", ("src/a.cs",))})

    def test_a_half_written_agent_journal_line_exits_1_naming_the_journal(self):
        self._journal("wf_torn", result=self._bounded_return())
        self._agent_journal("wf_torn", ["Read:src/a.cs"])
        journal = self.root / "sessions" / "sess1" / "subagents" / "workflows" / "wf_torn" / "journal.jsonl"
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write('{"type": "result", "agentId": "a')
        result = self.record("--run", "wf_torn", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertNotIn("Traceback", result.stderr.decode())
        self.assertIn("journal.jsonl", result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_run_whose_return_shed_its_sources_recovers_them_from_each_seats_agent_result(self):
        result = self.engine_return([dict(ENGINE_PER_AGENT_TWO, count=1,
                                          mandates={"cq.C": "findings", "cq.D": "clean"})])
        self._journal("wf_shed", result=result)
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / "wf_shed"
        sub.mkdir(parents=True, exist_ok=True)
        events = [{"type": "started", "agentId": "a1", "label": "review:seat-code-reviewer"},
                  {"type": "result", "agentId": "a1", "result": {
                      "findings": [{"agent": "x", "file": "src/a.cs:1", "description": "d"}],
                      "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads", "basis": "read"}}}]
        (sub / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        recorded = self.record("--run", "wf_shed", "--files", "src/a.cs", stage="pre-merge", subject="pr:1")
        self.assertEqual(recorded.returncode, 0, recorded.stderr.decode())
        self.assertEqual({tuple(r["findingFiles"]) for r in self.rows()}, {("src/a.cs",)})

    def _observed_run(self, name, calls):
        """An engine run whose seat transcript holds `calls` ((tool name, input) pairs, or (tool name, input, True)
        for a call whose result errored), each followed by its `tool_result`, in the line shapes of a live run
        (`agent-<agentId>.jsonl` beside `journal.jsonl`), or no transcript when None."""
        result = self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "clean"},
                                          checkedTargets=["Read:src/a.cs", "Read:src/b.cs"])])
        self._journal(name, result=result)
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / name
        sub.mkdir(parents=True, exist_ok=True)
        events = [{"type": "started", "key": "v2:k", "agentId": "a1", "label": "review:seat-code-reviewer",
                   "phase": "Review"},
                  {"type": "result", "key": "v2:k", "agentId": "a1", "result": {"findings": [], "checked": {
                      "toolsUsed": ["Read:src/a.cs", "Read:src/b.cs"], "stoppedAt": "exhausted-leads",
                      "basis": "read"}, "gaps": []}}]
        (sub / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        if calls is None:
            return
        lines = [{"parentUuid": None, "isSidechain": True, "agentId": "a1", "type": "user",
                  "message": {"role": "user", "content": "brief"}}]
        for n, (tool, given, *errored) in enumerate(calls):
            lines.append({"parentUuid": "u%d" % n, "isSidechain": True, "agentId": "a1", "type": "assistant",
                          "message": {"id": "m%d" % n, "role": "assistant", "type": "message", "model": "claude-x",
                                      "content": [{"type": "tool_use", "id": "toolu_%d" % n, "name": tool,
                                                   "input": given, "caller": {"type": "direct"}}]}})
            lines.append({"parentUuid": "m%d" % n, "isSidechain": True, "agentId": "a1", "type": "user",
                          "message": {"role": "user", "content": [
                              {"type": "tool_result", "tool_use_id": "toolu_%d" % n,
                               "content": "<tool_use_error>failed</tool_use_error>" if errored else "ok",
                               "is_error": bool(errored)}]}})
        (sub / "agent-a1.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")

    def test_a_run_engages_a_named_target_only_when_its_transcript_shows_a_call_on_it(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.repo, capture_output=True,
                             text=True, check=True).stdout.strip()
        calls = [("Read", {"file_path": top.replace("/", "\\") + "\\src\\a.cs", "offset": 1, "limit": 40}),
                 ("Grep", {"pattern": "b", "path": top.replace("/", "\\") + "\\src", "output_mode": "content"}),
                 ("Glob", {"pattern": "src/**/*.cs", "path": top}),
                 ("Bash", {"command": "git log --oneline -3", "description": "history"}),
                 ("StructuredOutput", {"checked": {"toolsUsed": ["Read:src/b.cs"]}})]
        for name, planted, covered in (("wf_seen", calls, ("src/a.cs",)),
                                       ("wf_bash", [("Bash", {"command": "cat src/b.cs"})], ("src/b.cs",)),
                                       ("wf_pwsh", [("PowerShell", {"command": "Get-Content src/b.cs"})],
                                        ("src/b.cs",)),
                                       ("wf_errored", [("Read", {"file_path": "src/a.cs"}, True),
                                                       ("Read", {"file_path": "src/b.cs"})], ("src/b.cs",)),
                                       ("wf_untraced", None, ("src/a.cs", "src/b.cs"))):
            with self.subTest(run=name):
                self.reset_ledger()
                self._observed_run(name, planted)
                result = self.record("--run", name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                cq_c = {r["outcome"]: tuple(r["files"]) for r in self.rows() if r["mandate"] == "cq.C"}
                self.assertEqual(cq_c.get("clean"), covered, cq_c)
                rest = tuple(p for p in ("src/a.cs", "src/b.cs") if p not in covered)
                self.assertEqual(cq_c.get("incomplete"), rest or None, cq_c)

    def test_a_run_takes_each_seats_whole_gap_list_from_its_agent_result(self):
        """The bound clips a gap string (and `coverage.seats` carries only the count), so on `--run` the seat's
        gaps come from its journaled agent result; with no agent result a clipped list engages nothing."""
        self._write("src/b.cs", "b\n")
        self._commit("C")
        gap = "x" * 200 + " src/b.cs was not read"
        clipped = gap[:160] + " …[+%d chars]" % (len(gap) - 160)
        mandates = {"cq.C": "clean", "cq.D": "clean"}
        targets = ["Read:src/a.cs", "Read:src/b.cs"]
        preview = self.engine_return([dict(ENGINE_PER_AGENT_TWO, status="partial", gaps=1, gapTexts=[clipped],
                                           capabilityPartial=True, mandates=mandates, checkedTargets=targets)])
        bounded = self.engine_return([], rows_omitted=1)
        bounded["delivery"]["coverage"].update(rowsPreviewed=0, seats=[{
            "key": "seat-code-reviewer", "status": "partial", "stoppedAt": "exhausted-leads", "mandates": mandates,
            "checkedTargets": 2, "count": 0, "gaps": 1, "capabilityPartial": True}])
        agent = {"type": "result", "agentId": "a1", "result": {"findings": [], "gaps": [gap], "checked": {
            "toolsUsed": targets, "stoppedAt": "exhausted-leads", "basis": "read"}}}
        typed = json.loads(json.dumps(agent))
        typed["result"]["gaps"] = [{"item": "src/b.cs", "reason": "capability"}]
        for name, result, events, covered in (("wf_gclip", preview, [agent], ("src/a.cs",)),
                                              ("wf_gbound", bounded, [agent], ("src/a.cs",)),
                                              ("wf_gtyped", bounded, [typed], ("src/a.cs",)),
                                              ("wf_gnone", preview, [], None)):
            with self.subTest(run=name):
                self.reset_ledger()
                self._journal(name, result=result)
                sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / name
                sub.mkdir(parents=True, exist_ok=True)
                (sub / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                recorded = self.record("--run", name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(recorded.returncode, 0, recorded.stderr.decode())
                cq_c = {r["outcome"]: tuple(r["files"]) for r in self.rows() if r["mandate"] == "cq.C"}
                self.assertEqual(cq_c.get("clean"), covered, cq_c)

    def test_a_run_takes_each_seats_whole_target_list_from_its_agent_result(self):
        """A bounded `perAgent` preview row lists at most eight targets, so on `--run` the journaled agent
        result's `checked.toolsUsed` is the seat's target list."""
        self._write("src/b.cs", "b\n")
        self._commit("C")
        result = self.engine_return([dict(ENGINE_PER_AGENT_TWO, toolsUsed=2, checkedTargets=["Read:src/a.cs"],
                                          mandates={"cq.C": "clean", "cq.D": "clean"})])
        self._journal("wf_tclip", result=result)
        self._agent_journal("wf_tclip", ["Read:src/a.cs", "Read:src/b.cs"])
        recorded = self.record("--run", "wf_tclip", "--files", "src/a.cs", "src/b.cs")
        self.assertEqual(recorded.returncode, 0, recorded.stderr.decode())
        self.assertEqual({(r["outcome"], tuple(r["files"])) for r in self.rows() if r["mandate"] == "cq.C"},
                         {("clean", ("src/a.cs", "src/b.cs"))})

    def _dispatch_run(self, name, deliverable, spilled=True, must_read=None):
        """A dispatch.js run: one lens job naming its seat (and its `mustRead` when given), plus a non-review job,
        as the session journals it."""
        # The ledger expands this seat through the live review registry, which each consumer forks (keep-local): a
        # checkout whose registry carries no such lens never ships this project's lens, so there is nothing to run.
        if "cq.C" not in (load_module(TOOLS / "lens.py").seat_mandates("lane-code-reviewer") or []):
            self.skipTest("this checkout's review registry has no lane-code-reviewer lens")
        session = self.root / "sessions" / "sess1"
        (session / "workflows").mkdir(parents=True, exist_ok=True)
        spill = ".claude/scratch/pr1/spill"
        lens_job = {"label": "pr1-lane-code-reviewer", "seat": "lane-code-reviewer", "mandates": ["lane-code-reviewer"],
                    "promptPath": "p.md", "model": "m", "effort": "high", "tier": "executor"}
        if must_read is not None:
            lens_job["mustRead"] = must_read
        jobs = [lens_job,
                {"label": "conflicts", "promptPath": "c.md", "model": "m", "effort": "low", "tier": "fanout"}]
        pins = {"pr1-lane-code-reviewer": "m/high/general-purpose tier:executor", "conflicts": "m/low tier:fanout"}
        text = json.dumps(deliverable)
        digest = ("digest\ncouldNotSatisfy: none\nFULL: %s/pr1-lane-code-reviewer.md" % spill) if spilled else text
        journal = {"runId": name, "workflowName": "dispatch", "args": json.dumps({"jobs": jobs, "spillDir": spill}),
                   "logs": ["PINS " + json.dumps(pins)],
                   "result": {"spillDir": spill, "digests": {"pr1-lane-code-reviewer": digest, "conflicts": "none"}},
                   "workflowProgress": [{"type": "workflow_agent", "agentId": "a1", "label": "pr1-lane-code-reviewer",
                                         "model": "claude-x", "state": "completed"}]}
        (session / "workflows" / (name + ".json")).write_text(json.dumps(journal), encoding="utf-8")
        sub = session / "subagents" / "workflows" / name
        sub.mkdir(parents=True, exist_ok=True)
        events = [{"type": "started", "agentId": "a1", "label": "pr1-lane-code-reviewer"},
                  {"type": "result", "agentId": "a1", "result": digest},
                  {"type": "started", "agentId": "a2", "label": "conflicts"},
                  {"type": "result", "agentId": "a2", "result": "none"}]
        (sub / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        if spilled:
            path = self.repo / spill / "pr1-lane-code-reviewer.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("```json\n" + text + "\n```\n", encoding="utf-8")

    DISPATCH_FINDING = {"agent": "lane-code-reviewer", "action": "FIX", "category": "bug", "file": "src/a.cs:1",
                        "description": "d", "rationale": "r", "evidence": []}

    def test_a_dispatch_run_enters_the_ledger_through_its_journal_one_seat_per_lens_job(self):
        self._dispatch_run("wf_dispatch", {"findings": [self.DISPATCH_FINDING], "checked": {
            "toolsUsed": ["Read:src/a.cs"], "basis": "read every changed file", "stoppedAt": "exhausted-leads"}})
        result = self.record("--run", "wf_dispatch", "--files", "src/a.cs", stage="pre-merge", subject="pr:1")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = {r["mandate"]: r for r in self.rows()}
        self.assertTrue({"cq.C", "cq.D", "parity"} <= set(rows), sorted(rows))
        self.assertEqual({r["seat"] for r in rows.values()}, {"lane-code-reviewer"})
        self.assertEqual({r["outcome"] for r in rows.values()}, {"findings"})
        self.assertEqual({(r["tier"], r["effort"]) for r in rows.values()}, {("executor", "high")})
        self.assertEqual({tuple(r["findingFiles"]) for r in rows.values()}, {("src/a.cs",)})
        self.assertTrue(all(r["id"].startswith("wf_dispatch:") for r in rows.values()))

    def test_a_dispatch_job_deliverable_held_inline_in_the_journal_records_the_same(self):
        self._dispatch_run("wf_inline", {"findings": [], "checked": {
            "toolsUsed": ["Read:src/a.cs"], "basis": "read every changed file", "stoppedAt": "exhausted-leads"}},
            spilled=False)
        result = self.record("--run", "wf_inline", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual({(r["mandate"], r["outcome"], tuple(r["files"])) for r in self.rows()
                          if r["mandate"] == "cq.C"}, {("cq.C", "clean", ("src/a.cs",))})

    def test_a_dispatch_run_keeps_its_seats_own_targets_beside_a_transcript(self):
        self._dispatch_run("wf_dtrace", {"findings": [], "checked": {
            "toolsUsed": ["Read:src/a.cs"], "basis": "read every changed file", "stoppedAt": "exhausted-leads"}},
            spilled=False)
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / "wf_dtrace"
        (sub / "agent-a1.jsonl").write_text(json.dumps({"type": "assistant", "agentId": "a1", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "no calls"}]}}) + "\n", encoding="utf-8")
        result = self.record("--run", "wf_dtrace", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual({(r["outcome"], tuple(r["files"])) for r in self.rows() if r["mandate"] == "cq.C"},
                         {("clean", ("src/a.cs",))})

    def test_a_dispatch_seat_whose_own_entries_leave_a_must_read_entry_unread_records_no_mandate_covering(self):
        # No transcript: the seat's own entries credit reads but prove nothing, so an uncovered entry leaves the
        # seat unproven (partial) and every mandate non-covering; observed reads that leave an entry unread fail it.
        self._write("src/b.cs", "b\nb\nb\n")
        self._commit("C")
        read_a = {"toolsUsed": ["Read:src/a.cs"], "basis": "read a.cs", "stoppedAt": "exhausted-leads"}
        read_part = {"toolsUsed": ["Read:src/a.cs", "Read:src/b.cs:1-2"], "basis": "read a.cs and part of b.cs",
                     "stoppedAt": "exhausted-leads"}
        for name, gaps, checked, outcomes in (
                ("wf_unread", [], read_a, {"not-reached"}),
                ("wf_part", [], read_part, {"not-reached"}),
                ("wf_outside", [{"item": "src/b.cs", "reason": "outside-lens"}], read_a, {"clean", "incomplete"})):
            with self.subTest(name):
                self.reset_ledger()
                self._dispatch_run(name, {"findings": [], "gaps": gaps, "checked": checked},
                                   must_read=[{"path": "src/a.cs", "lines": 1}, {"path": "src/b.cs", "lines": 3}])
                result = self.record("--run", name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows()}, outcomes)

    def _transcript(self, name, reads):
        """Seat agent a1's transcript for run `name`: one successful Read per `(file_path, offset, limit)`."""
        lines = []
        for n, (path, offset, limit) in enumerate(reads):
            given = {"file_path": path, "offset": offset, "limit": limit}
            lines.append({"type": "assistant", "agentId": "a1", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_%d" % n, "name": "Read", "input": given}]}})
            lines.append({"type": "user", "agentId": "a1", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_%d" % n, "content": "ok", "is_error": False}]}})
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / name
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-a1.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines), encoding="utf-8")

    MUST_READ = [{"path": "src/a.cs", "lines": 1}, {"path": "src/b.cs", "lines": 3}]
    CLAIMED = ["Read:src/a.cs", "Read:src/b.cs:1-3"]

    def _engine_must_read_run(self, name, reads, must_read=None, claimed=None, agent_gaps=(), **row):
        """An engine run whose seat claims every must-read line (or `claimed`); its transcript holds `reads`, or
        none when None. `row` overrides the engine's perAgent row."""
        must_read = self.MUST_READ if must_read is None else must_read
        claimed = self.CLAIMED if claimed is None else claimed
        self._journal(name, result=self.engine_return([dict(ENGINE_PER_AGENT_TWO, checkedTargets=claimed,
                                                            mandates={"cq.C": "clean", "cq.D": "clean"}, **row)]))
        path = self.root / "sessions" / "sess1" / "workflows" / (name + ".json")
        journal = json.loads(path.read_text(encoding="utf-8"))
        journal["args"] = json.dumps({"agents": [{"key": "seat-code-reviewer", "mustRead": must_read}]})
        path.write_text(json.dumps(journal), encoding="utf-8")
        sub = self.root / "sessions" / "sess1" / "subagents" / "workflows" / name
        sub.mkdir(parents=True, exist_ok=True)
        events = [{"type": "started", "agentId": "a1", "label": "review:seat-code-reviewer"},
                  {"type": "result", "agentId": "a1", "result": {"findings": [], "gaps": list(agent_gaps), "checked": {
                      "toolsUsed": claimed, "stoppedAt": "exhausted-leads", "basis": "read"}}}]
        (sub / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        if reads is not None:
            self._transcript(name, reads)

    def test_an_engine_seat_whose_transcript_never_read_its_claimed_range_records_failed(self):
        self._write("src/b.cs", "b\nb\nb\n")
        self._commit("C")
        for name, reads, outcomes, evidence in (
                ("wf_mr_short", [("src/a.cs", 1, 40), ("src/b.cs", 1, 2)], {"failed"}, {"observed"}),
                ("wf_mr_whole", [("src/a.cs", 1, 40), ("src/b.cs", 1, 3)], {"clean"}, {"observed"}),
                ("wf_mr_none", None, {"clean"}, {"self-reported"})):
            with self.subTest(name):
                self.reset_ledger()
                self._engine_must_read_run(name, reads)
                result = self.record("--run", name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows()}, outcomes, self.rows())
                self.assertEqual({r.get("mustReadEvidence") for r in self.rows()}, evidence)

    def test_a_dispatch_seat_whose_transcript_never_read_its_claimed_range_records_failed(self):
        self._write("src/b.cs", "b\nb\nb\n")
        self._commit("C")
        checked = {"toolsUsed": self.CLAIMED, "basis": "read a.cs and b.cs", "stoppedAt": "exhausted-leads"}
        for name, reads, outcomes, evidence in (
                ("wf_dmr_short", [("src/a.cs", 1, 40), ("src/b.cs", 1, 2)], {"failed"}, {"observed"}),
                ("wf_dmr_whole", [("src/a.cs", 1, 40), ("src/b.cs", 1, 3)], {"clean"}, {"observed"}),
                ("wf_dmr_none", None, {"clean"}, {"self-reported"})):
            with self.subTest(name):
                self.reset_ledger()
                self._dispatch_run(name, {"findings": [], "gaps": [], "checked": checked}, must_read=self.MUST_READ)
                if reads is not None:
                    self._transcript(name, reads)
                result = self.record("--run", name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual({r["outcome"] for r in self.rows()}, outcomes, self.rows())
                self.assertEqual({r.get("mustReadEvidence") for r in self.rows()}, evidence)

    DIFF = ".claude/scratch/pr1/d.diff"
    DIFF_TEXT = ("diff --git a/src/a.cs b/src/a.cs\n--- a/src/a.cs\n+++ b/src/a.cs\n@@ -1 +1 @@\n-x\n+a\n"
                 "diff --git a/src/b.cs b/src/b.cs\n--- /dev/null\n+++ b/src/b.cs\n@@ -0,0 +1,3 @@\n+b\n+b\n+b\n")

    def _diff_cases(self):
        """(name, last line read, gaps, expected cq.C outcome -> files): the whole diff, then lines 1-6 (src/a.cs's
        section) with a capability gap naming the diff so the seat stays partial."""
        self._write("src/b.cs", "b\nb\nb\n")
        self._write(self.DIFF, self.DIFF_TEXT)
        self._commit("C")
        gap = [{"item": self.DIFF, "reason": "capability"}]
        return (("whole", 13, [], {"clean": ("src/a.cs", "src/b.cs")}),
                ("part", 6, gap, {"clean": ("src/a.cs",), "incomplete": ("src/b.cs",)}))

    def test_an_engine_seat_that_read_a_must_read_diff_engages_the_files_its_read_lines_cover(self):
        for name, last, gaps, expected in self._diff_cases():
            with self.subTest(name):
                self.reset_ledger()
                partial = dict(status="partial", gaps=1, gapTexts=[self.DIFF + " (capability)"],
                               capabilityPartial=True) if gaps else {}
                self._engine_must_read_run("wf_ediff_" + name, [(self.DIFF, 1, last)],
                                           must_read=[{"path": self.DIFF, "lines": 13}],
                                           claimed=["Read:%s:1-%d" % (self.DIFF, last)], agent_gaps=gaps, **partial)
                result = self.record("--run", "wf_ediff_" + name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                cq_c = {r["outcome"]: tuple(r["files"]) for r in self.rows() if r["mandate"] == "cq.C"}
                self.assertEqual(cq_c, expected)

    def test_a_dispatch_seat_that_read_a_must_read_diff_engages_the_files_its_read_lines_cover(self):
        for name, last, gaps, expected in self._diff_cases():
            with self.subTest(name):
                self.reset_ledger()
                checked = {"toolsUsed": ["Read:%s:1-%d" % (self.DIFF, last)], "basis": "read the diff",
                           "stoppedAt": "exhausted-leads", "mandates": {"lane-code-reviewer": "clean"}}
                self._dispatch_run("wf_ddiff_" + name, {"findings": [], "gaps": gaps, "checked": checked},
                                   must_read=[{"path": self.DIFF, "lines": 13}])
                self._transcript("wf_ddiff_" + name, [(self.DIFF, 1, last)])
                result = self.record("--run", "wf_ddiff_" + name, "--files", "src/a.cs", "src/b.cs")
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                cq_c = {r["outcome"]: tuple(r["files"]) for r in self.rows() if r["mandate"] == "cq.C"}
                self.assertEqual(cq_c, expected)

    def test_a_dispatch_job_whose_spill_is_missing_records_its_seat_as_not_reached(self):
        self._dispatch_run("wf_lost", {"findings": [], "checked": {}})
        (self.repo / ".claude/scratch/pr1/spill/pr1-lane-code-reviewer.md").unlink()
        result = self.record("--run", "wf_lost", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(self.rows())
        self.assertEqual({r["outcome"] for r in self.rows()}, {"not-reached"})

    def test_a_target_count_with_no_agent_result_exits_1_naming_run(self):
        ret = self.save(self._bounded_return())
        result = self.record("--result", str(ret), "--files", "src/a.cs")
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn("--run", result.stderr.decode())
        self.assertNotIn("Traceback", result.stderr.decode())
        self._journal("wf_noagents", result=self._bounded_return())
        run = self.record("--run", "wf_noagents", "--files", "src/a.cs")
        self.assertEqual(run.returncode, 1, run.stderr.decode())
        self.assertNotIn("Traceback", run.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_sidecar_result_maps_coverage_status_and_uncovered_mandates(self):
        prepared = {"counts": {}, "sources": [{"id": "F1", "seat": "sa-seat", "index": 0,
                                               "finding": {"agent": "cq.D"}}],
                    "coverage": [{"label": "sa-seat", "mandates": ["cq.C", "cq.D", "parity"],
                                  "uncoveredMandates": ["parity"], "status": "completed",
                                  "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads"}}]}
        path = self.save(prepared, "prepared.json")
        roster = self.roster(self.seat("sa-seat", ("cq.C", "cq.D", "parity")))
        result = self.record("--sidecar-result", str(path), "--files", "src/a.cs", "--roster", str(roster))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        outcomes = {r["mandate"]: r["outcome"] for r in self.rows()}
        self.assertEqual(outcomes, {"cq.C": "clean", "cq.D": "findings", "parity": "not-reached"})

    def test_script_and_inline_sources_take_an_outcome(self):
        result = self.record("--script", "worklog-status", "--outcome", "clean", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        row = self.rows()[-1]
        self.assertEqual((row["reviewer"], row["mandate"], row["outcome"]),
                         ("script:worklog-status", "worklog-status", "clean"))
        self.assertEqual(self.record("--inline", "session_audit", "--outcome", "clean", "--mandate", "parity",
                                     "--files", "src/a.cs").returncode, 0)
        row = self.rows()[-1]
        self.assertEqual((row["reviewer"], row["mandate"]), ("inline:session_audit", "parity"))
        self.assertEqual(self.pairs(self.uncovered(mandates="parity")), [])
        self.assertEqual(self.record("--script", "x").returncode, 2, "an outcome is required")
        self.assertEqual(self.record("--inline", "session_audit", "--outcome", "clean").returncode, 2,
                         "an inline row names its mandate")

    def test_exactly_one_outcome_source(self):
        ret = self.save(self.engine_return([ENGINE_PER_AGENT_TWO]))
        self.assertEqual(self.record("--files", "src/a.cs").returncode, 2)
        both = self.record("--result", str(ret), "--script", "x", "--outcome", "clean", "--files", "src/a.cs")
        self.assertEqual(both.returncode, 2)
        self.assertEqual(self.run_tool("record").returncode, 2)

    def test_plan_check_no_new_decision_row_carries_the_flag(self):
        self.record("--script", "worklog-status", "--outcome", "clean", "--files", "src/a.cs",
                    "--no-new-decision", stage="plan-check", subject="plan:p")
        self.assertIs(self.rows()[-1]["noNewDecision"], True)

    def test_degraded_flag_is_recorded(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "clean"})]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        self.record("--result", str(ret), "--files", "src/a.cs", "--roster", str(roster), "--degraded")
        self.assertTrue(all(r["degraded"] is True for r in self.rows()))

    def test_unwritable_ledger_exits_1_naming_the_path(self):
        self.ledger.mkdir(parents=True)
        result = self.record("--script", "worklog-status", "--outcome", "clean", "--files", "src/a.cs")
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.ledger), result.stderr.decode())

    def test_bad_stage_and_subject_exit_2(self):
        bad_stage = self.run_tool("record", "--stage", "nope", "--subject", "session:s", "--script", "x",
                                  "--outcome", "clean")
        self.assertEqual(bad_stage.returncode, 2)
        bad_subject = self.run_tool("record", "--stage", "candidate", "--subject", "nowhere", "--script", "x",
                                    "--outcome", "clean")
        self.assertEqual(bad_subject.returncode, 2)

    def test_author_model_defaults_to_the_session_transcript_model(self):
        project = self.root / "config" / "projects" / "proj"
        project.mkdir(parents=True)
        lines = [json.dumps({"type": "assistant", "message": {"model": "model-old"}}),
                 json.dumps({"type": "user", "message": {"content": "hi"}}),
                 json.dumps({"type": "assistant", "message": {"model": "model-new"}})]
        (project / "sess-42.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = self.record("--script", "worklog-status", "--outcome", "clean",
                             extra_env={"CLAUDE_CODE_SESSION_ID": "sess-42"})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        row = self.rows()[-1]
        self.assertEqual((row["authorModel"], row["session"]), ("model-new", "sess-42"))

    def test_author_model_reads_a_config_dir_named_with_a_tilde(self):
        home = self.root / "home"
        project = home / "cfg" / "projects" / "proj"
        project.mkdir(parents=True)
        (project / "sess-7.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"model": "model-t"}}) + "\n", encoding="utf-8")
        result = self.record("--script", "worklog-status", "--outcome", "clean",
                             extra_env={"CLAUDE_CODE_SESSION_ID": "sess-7", "CLAUDE_CONFIG_DIR": "~/cfg",
                                        "HOME": str(home), "USERPROFILE": str(home)})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[-1]["authorModel"], "model-t")

    def _sidecar(self, coverage, sources=(), attrs=None):
        path = self.save({"counts": {}, "sources": list(sources), "coverage": [coverage]}, "prepared.json")
        roster = self.roster(self.seat(coverage["label"], ("cq.C", "cq.D")))
        result = self.run_tool("record", "--stage", "candidate", "--subject", "session:s1", "--base-ref", "main",
                               "--sidecar-result", str(path), "--files", "src/a.cs", "--roster", str(roster),
                               attrs=attrs)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return {r["mandate"]: r["outcome"] for r in self.rows()}

    def test_a_lens_keyed_sidecar_result_writes_catalog_keyed_rows(self):
        lens = {"_seat_mandates": lambda key: ["cq.C", "cq.D"] if key == "sa-lens" else None}
        coverage = {"label": "sa-seat", "mandates": ["sa-lens"], "uncoveredMandates": [], "status": "completed",
                    "valid": 0, "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads"}}
        self.assertEqual(self._sidecar(coverage, attrs=lens), {"cq.C": "clean", "cq.D": "clean"})

    def test_a_sidecar_seat_typed_mandate_map_sets_each_token(self):
        coverage = {"label": "sa-seat", "mandates": ["cq.C", "cq.D"], "uncoveredMandates": [], "status": "completed",
                    "valid": 0, "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads",
                                            "mandates": {"cq.C": "not-reached", "cq.D": "findings"}}}
        self.assertEqual(self._sidecar(coverage), {"cq.C": "not-reached", "cq.D": "findings"})

    def test_a_partial_sidecar_seat_bounds_its_map_tokens_as_the_engine_does(self):
        coverage = {"label": "sa-seat", "mandates": ["cq.C", "cq.D"], "uncoveredMandates": [], "status": "partial",
                    "valid": 1, "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads",
                                            "mandates": {"cq.C": "clean", "cq.D": "findings"}}}
        self.assertEqual(self._sidecar(coverage), {"cq.C": "incomplete", "cq.D": "incomplete"})

    def test_a_wrong_typed_catalog_reads_as_adaptation_reads_it(self):
        adaptation = self.claude / "skills" / "project_subsystems" / "adaptation.json"
        adaptation.write_text(json.dumps({"review_mandates": ["cq.C"], "review_scope": REVIEW_SCOPE}),
                              encoding="utf-8")
        result = self.run_tool("keys")
        self.assertEqual((result.returncode, result.stdout.decode().strip()), (0, ""), result.stderr.decode())
        self.assertIn("review_mandates", result.stderr.decode())

    def test_run_tool_restores_every_attribute_it_overrides(self):
        module = load_module(TOOL)
        before = (module._pick, module._seat_mandates, module._gh_json)
        self.run_tool("keys", attrs=dict(NO_PICK, _seat_mandates=lambda key: ["cq.C"]))
        self.assertEqual((module._pick, module._seat_mandates, module._gh_json), before)

    def test_unreadable_author_model_is_unknown_and_says_so(self):
        result = self.record("--script", "worklog-status", "--outcome", "clean")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.rows()[-1]["authorModel"], "unknown")
        self.assertIn("unknown", result.stderr.decode())

    def test_rows_filters_and_json(self):
        self.plant_rows(self.row("cq.C", subject="pr:5", task="t9", model="mX"),
                        self.row("cq.D", subject="session:s", task="t1", model="mY"))
        by_subject = self.run_tool("rows", "--subject", "pr:5", "--json")
        self.assertEqual([r["mandate"] for r in json.loads(by_subject.stdout)], ["cq.C"])
        by_task = self.run_tool("rows", "--task", "t1", "--json")
        self.assertEqual([r["mandate"] for r in json.loads(by_task.stdout)], ["cq.D"])
        by_model = self.run_tool("rows", "--model", "mY")
        self.assertEqual(by_model.returncode, 0)
        self.assertIn("cq.D", by_model.stdout.decode())
        self.assertNotIn("cq.C", by_model.stdout.decode())
        missing = self.run_tool("rows", "--subject", "pr:404")
        self.assertEqual((missing.returncode, missing.stdout.decode().strip()), (0, ""))

    def test_rows_with_no_ledger_prints_nothing_and_says_why(self):
        result = self.run_tool("rows", "--task", "t1")
        self.assertEqual((result.returncode, result.stdout.decode().strip()), (0, ""))
        self.assertIn("ledger", result.stderr.decode())


class OwedSetTests(RecordBase):
    """Owed sets over the planted `/session_audit` registry (`lens.AGENTS` points at it): without a roster,
    `candidate` owes only the lenses that always run; a roster adds its own candidate mandates."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(TOOLS))
        try:
            import lens
        finally:
            sys.path.remove(str(TOOLS))
        self.agents = self.claude / "commands" / "agents"
        (self.agents / "plan_check_agents.md").write_text("# Plan check agents\n", encoding="utf-8")
        saved = lens.AGENTS
        lens.AGENTS = self.agents
        self.addCleanup(setattr, lens, "AGENTS", saved)
        self.sweep = self.seat("sa-architecture-sweep", ("architecture-sweep",), tier="architect")
        self.code = self.seat("sa-fixture-code", ("cq.C", "cq.D"))

    def inline(self, mandate, path="src/a.cs"):
        return self.row(mandate, id="inline1:" + mandate, reviewer="inline", files={path: self.blob(path)})

    def owed_pairs(self, *files, mandates="candidate", roster=None, env=None):
        args = ["uncovered", "--files", *(files or ("src/a.cs",)), "--mandates", mandates, "--base-ref", "main"]
        if roster is not None:
            path = self.root / "owed_set_roster.json"
            path.write_text(json.dumps(roster), encoding="utf-8")
            args += ["--roster", str(path)]
        return self.pairs(self.run_tool(*args, extra_env=env))

    def test_without_a_roster_candidate_owes_no_conditional_pair(self):
        self.plant_rows(self.inline("cq.C"), self.inline("cq.D"))
        self.assertEqual(self.owed_pairs(), [])
        self.plant_rows(self.inline("cq.D"))
        self.assertEqual(self.owed_pairs(), [self.pair("cq.C")])

    def test_a_roster_holding_only_the_sweep_owes_the_sweep_and_the_unconditional_floor(self):
        self.plant_rows(self.inline("cq.C"))
        self.assertEqual(self.owed_pairs(roster=[self.sweep]),
                         [self.pair("architecture-sweep"), self.pair("cq.D")])
        self.assertEqual([s["key"] for s in self.kept(self.plan([self.sweep]))["roster"]], ["sa-architecture-sweep"])

    def test_a_roster_without_the_sweep_owes_no_sweep_pair(self):
        self.plant_rows(self.inline("cq.D"))
        self.assertEqual(self.owed_pairs(roster=[self.code]), [self.pair("cq.C")])

    def test_the_override_names_both_the_candidate_set_and_the_floor(self):
        env = {"HARNESS_REVIEW_CANDIDATE_KEYS": "cq.C"}
        self.assertEqual(self.owed_pairs(roster=[self.code, self.sweep], env=env), [self.pair("cq.C")])
        self.assertEqual(self.owed_pairs(env=env), [self.pair("cq.C")])

    def test_owed_without_a_roster_lists_no_commit_owing_only_a_conditional_pair(self):
        self.r.set_upstream(self.head())
        self._write("src/b.cs", "b\n")
        self._commit("C new file")
        self.plant_rows(self.inline("cq.C", "src/b.cs"), self.inline("cq.D", "src/b.cs"))
        listed = self.run_tool("owed", "--base-ref", "main")
        self.assertEqual((listed.returncode, listed.stdout.decode().strip()), (0, ""), listed.stderr.decode())
        self.plant_rows(self.inline("cq.D", "src/b.cs"))
        listed = self.run_tool("owed", "--base-ref", "main")
        self.assertIn("C new file", listed.stdout.decode())

    def test_a_lens_whose_labels_include_conditional_is_conditional(self):
        (self.agents / "session_audit_agents.md").write_text(
            SA_AGENTS.replace("admission: conditional", "admission: triggered, conditional"), encoding="utf-8")
        self.plant_rows(self.inline("cq.C"), self.inline("cq.D"))
        self.assertEqual(self.owed_pairs(), [])

    def test_keys_check_reports_each_admission_disagreement(self):
        depth = self.claude / "skills" / "_brainstorm_shared" / "execution_depth.md"
        absent = self.run_tool("keys", "--check")
        self.assertEqual(absent.returncode, 0, absent.stderr.decode())
        self.assertIn("admission cross-check did not run: %s absent" % depth, absent.stderr.decode())
        depth.parent.mkdir(parents=True, exist_ok=True)
        table = ("# Execution depth\n\n### Mandate admission\n\n| Command | Conditional mandate or mode | Admitted when |\n"
                 "|---|---|---|\n%s| `/session_audit` and the review commands | the checklists | not doc-only |\n\n"
                 "### Entry routing\n\n| `/session_audit` | `sa-fixture-code` | elsewhere |\n")
        sweep_row = "| `/session_audit` | `sa-architecture-sweep` | open design |\n"
        depth.write_text(table % sweep_row, encoding="utf-8")
        agreed = self.run_tool("keys", "--check")
        self.assertEqual(agreed.returncode, 0, agreed.stderr.decode())
        registry = self.agents / "session_audit_agents.md"
        cases = (("a conditional lens with no row", table % "", SA_AGENTS, "sa-architecture-sweep"),
                 ("a row naming a lens without the label",
                  table % (sweep_row + "| `/session_audit` | `sa-fixture-code` | always |\n"), SA_AGENTS,
                  "sa-fixture-code"),
                 ("a CONDITIONAL heading without the label", table % sweep_row,
                  SA_AGENTS.replace("(code) — fixture lens", "(code) — CONDITIONAL"), "sa-fixture-code"))
        for name, doc, agents, lens_key in cases:
            with self.subTest(name):
                depth.write_text(doc, encoding="utf-8")
                registry.write_text(agents, encoding="utf-8")
                found = self.run_tool("keys", "--check")
                self.assertEqual(found.returncode, 1, found.stderr.decode())
                self.assertIn(lens_key, found.stderr.decode())

    def test_a_deleted_path_prints_no_pair_and_says_it_is_absent(self):
        result = self.run_tool("uncovered", "--files", "src/gone.cs", "src/a.cs", "--mandates", "cq.C",
                               "--base-ref", "main")
        self.assertEqual(self.pairs(result), [self.pair("cq.C")])
        absent = [line for line in result.stderr.decode().splitlines() if "absent" in line]
        self.assertEqual(absent, ["absent, nothing to review: src/gone.cs"])

    def test_a_pair_covered_at_an_earlier_blob_prints_that_blob(self):
        old = self.blob("src/a.cs")
        self._write("src/b.cs", "b\n")
        self._commit("C")
        self.plant_rows(self.row("cq.C"))
        self._write("src/a.cs", "edited\n")
        self._commit("D")
        self.assertEqual(self.pairs(self.uncovered(("src/a.cs", "src/b.cs"), "cq.C")),
                         [self.pair("cq.C") + "\tafter:" + old, self.pair("cq.C", "src/b.cs")])

    def test_units_lists_each_task_and_task_less_run_with_its_files(self):
        for rel in ("src/b.cs", "src/c.cs", "src/d.cs", "src/e.cs"):
            self._write(rel, "x\n")
        self.plant_rows(self.row("cq.C", id="runA:seat:cq.C", task="t1", files={"src/a.cs": "1" * 40}),
                        self.row("cq.D", id="runB:seat:cq.D", task="t1", files={"src/b.cs": "2" * 40}),
                        self.row("cq.C", id="runC:seat:cq.C", files={"src/c.cs": "3" * 40}),
                        self.row("cq.C", id="inline9:cq.C", reviewer="inline", files={"src/d.cs": "4" * 40}),
                        self.row("cq.C", id="runD:seat:cq.C", subject="session:s2", files={"src/e.cs": "5" * 40}))
        result = self.run_tool("rows", "--subject", "session:s1", "--units")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().splitlines(), ["run:runC\tsrc/c.cs", "task:t1\tsrc/a.cs,src/b.cs"])

    def test_units_drops_a_file_absent_from_the_worktree(self):
        self._write("src/kept.cs", "k\n")
        self.plant_rows(self.row("cq.C", id="runM:seat:cq.C", files={"src/kept.cs": "1" * 40, "src/moved.cs": "2" * 40}),
                        self.row("cq.C", id="runG:seat:cq.C", files={"src/gone.cs": "3" * 40}))
        result = self.run_tool("rows", "--subject", "session:s1", "--units")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode().splitlines(), ["run:runM\tsrc/kept.cs"])

    def test_a_recorded_sweep_seat_covers_its_named_file_until_an_edit_makes_it_a_later_round(self):
        self._write("src/b.cs", "b\n")
        self._commit("C")
        old = self.blob("src/a.cs")
        seat = {"key": "sa-architecture-sweep", "count": 0, "status": "completed", "stoppedAt": "exhausted-leads",
                "basis": "read", "toolsUsed": 1, "gaps": 0, "mandates": {"architecture-sweep": "clean"},
                "checkedTargets": ["Read:src/a.cs"]}
        recorded = self.record("--result", str(self.save(self.engine_return([seat]))), "--files", "src/a.cs",
                               "src/b.cs", "--roster", str(self.roster(self.sweep)))
        self.assertEqual(recorded.returncode, 0, recorded.stderr.decode())
        files = ("src/a.cs", "src/b.cs")
        self.assertEqual(self.owed_pairs(*files, mandates="architecture-sweep", roster=[self.sweep]),
                         [self.pair("architecture-sweep", "src/b.cs")])
        self._write("src/a.cs", "edited\n")
        self.assertEqual(self.owed_pairs(*files, mandates="architecture-sweep", roster=[self.sweep]),
                         [self.pair("architecture-sweep") + "\tafter:" + old,
                          self.pair("architecture-sweep", "src/b.cs")])


class EscapeCandidateTests(RecordBase):
    def _setup(self):
        self._write("src/a.cs", "old line\nkept line\n")
        self._commit("landed before")
        self.branch("work")
        self._write("src/a.cs", "old line\nkept line\nnew line\n")
        self._commit("this work")
        self.fake = self.root / "fake_escape.py"
        self.fake.write_text(FAKE_ESCAPE_SOURCE, encoding="utf-8")
        self.fake_out = self.root / "fake_escape_out.jsonl"
        finding = {"id": "F1", "agent": "seat-code-reviewer", "file": "src/a.cs:1", "description": "old line is wrong"}
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "findings", "cq.D": "clean"})],
                                           findings=[finding]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        return ["--result", str(ret), "--files", "src/a.cs", "--roster", str(roster)]

    def _env(self):
        return {"HARNESS_ESCAPE_LOG_TOOL": str(self.fake), "FAKE_ESCAPE_OUT": str(self.fake_out)}

    def _record(self, args, *extra, attrs=None):
        return self.run_tool("record", "--stage", "pr-review", "--subject", "pr:1", "--base-ref", "main",
                             *args, *extra, extra_env=self._env(), attrs=attrs)

    def _calls(self):
        if not self.fake_out.exists():
            return []
        return [json.loads(line) for line in self.fake_out.read_text(encoding="utf-8").splitlines()]

    def test_an_escape_row_names_the_one_mandate_its_seat_carries_and_the_repo(self):
        args = self._setup()
        roster_path = Path(args[args.index("--roster") + 1])
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        for seat in roster:
            seat["mandates"] = ["cq.C"]
        roster_path.write_text(json.dumps(roster), encoding="utf-8")
        sha = self.r.tips["main"]
        result = self._record(args, "--escaped", "F1=" + sha, attrs={"_seat_mandates": lambda key: ["cq.C"]})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        call = self._calls()[0]
        self.assertEqual(call[call.index("--should-have-caught") + 1], "cq.C")
        self.assertEqual(Path(call[call.index("--repo") + 1]).resolve(), self.repo.resolve())

    def test_an_escape_the_log_would_refuse_leaves_no_ledger_rows_behind(self):
        args = self._setup()
        missing = self._record(args, "--escaped", "F1=" + "1" * 40)
        self.assertEqual(missing.returncode, 2, missing.stderr.decode())
        self.assertIn("names no commit", missing.stderr.decode())
        ret = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        ret["findings"][0]["file"] = ""
        Path(args[1]).write_text(json.dumps(ret), encoding="utf-8")
        no_file = self._record(args, "--escaped", "F1=" + self.r.tips["main"])
        self.assertEqual(no_file.returncode, 2, no_file.stderr.decode())
        self.assertIn("cites no file", no_file.stderr.decode())
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.fake_out.exists())

    def test_an_unreadable_result_file_is_a_usage_error_not_a_traceback(self):
        self._setup()
        truncated = self.root / "truncated.json"
        truncated.write_text('{"findings": [', encoding="utf-8")
        for flag, path in (("--result", self.root / "nope.json"), ("--result", truncated),
                           ("--sidecar-result", truncated), ("--result", self.root)):
            with self.subTest(flag=flag, path=path.name):
                result = self._record([flag, str(path), "--files", "src/a.cs"])
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(str(path), result.stderr.decode())
        self.assertEqual(self.rows(), [])

    def test_a_seat_with_two_mandates_takes_the_named_one_else_none(self):
        args = self._setup()
        sha = self.r.tips["main"]
        two = {"_seat_mandates": lambda key: ["cq.C", "cq.D"]}
        self.assertEqual(self._record(args, "--escaped", "F1=" + sha, attrs=two).returncode, 0)
        self.assertEqual(self._record(args, "--escaped", "F1=%s:cq.D" % sha, attrs=two).returncode, 0)
        keys = [call[call.index("--should-have-caught") + 1] for call in self._calls()]
        self.assertEqual(keys, ["none", "cq.D"])
        bad = self._record(args, "--escaped", "F1=%s:cq.Z" % sha, attrs=two)
        self.assertEqual(bad.returncode, 2, bad.stderr.decode())
        self.assertIn("cq.Z", bad.stderr.decode())
        self.assertEqual(len(self._calls()), 2)

    def test_a_finding_on_a_pre_base_line_is_printed_as_a_candidate_and_writes_nothing(self):
        result = self._record(self._setup())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("F1", result.stdout.decode())
        self.assertIn("escape", result.stdout.decode().lower())
        self.assertFalse(self.fake_out.exists())

    def test_a_finding_on_a_line_this_work_added_is_not_a_candidate(self):
        args = self._setup()
        ret = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        ret["findings"][0]["file"] = "src/a.cs:3"
        Path(args[1]).write_text(json.dumps(ret), encoding="utf-8")
        result = self._record(args)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn("F1", result.stdout.decode())

    def test_escaped_writes_one_escape_row_through_the_escape_tool(self):
        args = self._setup()
        sha = self.r.tips["main"]
        result = self._record(args, "--escaped", "F1=" + sha)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        calls = [json.loads(line) for line in self.fake_out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call[0], "add")
        self.assertEqual(call[call.index("--sha") + 1], sha)
        self.assertEqual(call[call.index("--writer") + 1], "review:pr-review")
        self.assertEqual(call[call.index("--paths") + 1], "src/a.cs")
        self.assertEqual(call[call.index("--base-ref") + 1], "main")

    def test_an_absolute_or_dotted_finding_path_reaches_the_escape_tool_repo_relative(self):
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.repo, capture_output=True,
                             text=True, check=True).stdout.strip()
        args = self._setup()
        for cited in (top + "/src/a.cs:1", "./src/a.cs:1"):
            with self.subTest(cited=cited):
                ret = json.loads(Path(args[1]).read_text(encoding="utf-8"))
                ret["findings"][0]["file"] = cited
                Path(args[1]).write_text(json.dumps(ret), encoding="utf-8")
                if self.fake_out.exists():
                    self.fake_out.unlink()
                result = self._record(args, "--escaped", "F1=" + self.r.tips["main"])
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                call = self._calls()[0]
                self.assertEqual(call[call.index("--paths") + 1], "src/a.cs")

    def test_escaped_naming_an_unknown_finding_exits_2(self):
        result = self._record(self._setup(), "--escaped", "F99=" + "a" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.fake_out.exists())


class LedgerNewTests(RecordBase):
    """`findingFiles` on every panel row; `seatLedgerNew` on pre-merge rows."""

    SEAT = "seat-code-reviewer"
    OLD = "2020-01-01T00:00:00Z"

    def setUp(self):
        super().setUp()
        self.branch("side")
        self._write("src/side.cs", "side\n")
        self.side_head = self._commit("side work")
        self.checkout("main")

    def _return(self):
        findings = [{"id": "F1", "agent": self.SEAT, "file": "src/a.cs:1", "description": "d"},
                    {"id": "F2", "agent": "other-seat", "file": "src/z.cs:1", "description": "d"}]
        ret = self.engine_return(
            [dict(ENGINE_PER_AGENT_TWO, count=1, mandates={"cq.C": "findings", "cq.D": "clean"})], findings)
        ret["sources"] = [{"id": "F1", "lens": self.SEAT, "index": 0, "finding": findings[0]},
                          {"id": "F2", "lens": "other-seat", "index": 0, "finding": findings[1]}]
        return self.save(ret)

    def earlier(self, **over):
        fields = dict(id="old:seat:cq.C", ts=self.OLD, stage="pre-merge", subject="pr:1", outcome="findings",
                      seat="old-seat", seatMandates=["cq.C"], findingFiles=["src/a.cs"])
        fields.update(over)
        row = self.row(**fields)
        if row["findingFiles"] is None:
            del row["findingFiles"]
        return row

    def seat_rows(self, *planted, stage="pre-merge", subject="pr:1", tool=None):
        self.plant_rows(*planted)
        roster = self.roster(self.seat(self.SEAT, ("cq.C", "cq.D")))
        result = self.run_tool("record", "--stage", stage, "--subject", subject, "--base-ref", "main",
                               "--result", str(self._return()), "--files", "src/a.cs", "--roster", str(roster),
                               tool=tool)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = [r for r in self.rows() if r.get("seat") == self.SEAT]
        self.assertTrue(rows)
        return rows

    def new_count(self, *planted, **kw):
        values = {r.get("seatLedgerNew", "missing") for r in self.seat_rows(*planted, **kw)}
        self.assertEqual(len(values), 1, values)
        return values.pop()

    def test_each_panel_row_carries_the_paths_its_seat_findings_cite(self):
        rows = self.seat_rows(stage="candidate", subject="session:s1")
        self.assertEqual({tuple(r["findingFiles"]) for r in rows}, {("src/a.cs",)})

    def test_a_finding_on_a_path_no_earlier_row_reported_counts_one(self):
        self.assertEqual(self.new_count(), 1)

    def test_a_path_an_earlier_ancestor_head_findings_row_reported_counts_zero(self):
        self.assertEqual(self.new_count(self.earlier()), 0)

    def test_a_pr_review_row_on_a_pr_subject_carries_the_count(self):
        self.assertEqual(self.new_count(self.earlier(), stage="pr-review"), 0)

    def test_an_earlier_row_at_a_non_ancestor_head_does_not_suppress(self):
        self.assertEqual(self.new_count(self.earlier(head=self.side_head)), 1)

    def test_an_earlier_row_with_disjoint_seat_mandates_does_not_suppress(self):
        self.assertEqual(self.new_count(self.earlier(seatMandates=["parity"])), 1)

    def test_an_earlier_clean_or_plan_row_does_not_suppress(self):
        self.assertEqual(self.new_count(self.earlier(outcome="clean")), 1)
        self.ledger.unlink()
        self.assertEqual(self.new_count(self.earlier(subject="plan:plans/p.md")), 1)

    def test_an_earlier_row_without_finding_files_makes_the_count_null(self):
        self.assertIsNone(self.new_count(self.earlier(findingFiles=None)))

    def test_an_earlier_inline_or_script_row_leaves_the_count_measured(self):
        for reviewer in ("inline:session_audit", "script:worklog-status"):
            with self.subTest(reviewer=reviewer):
                self.reset_ledger()
                self.assertEqual(self.new_count(self.earlier(reviewer=reviewer, findingFiles=None)), 1)

    def test_a_candidate_stage_row_carries_no_count(self):
        self.assertEqual(self.new_count(self.earlier(), stage="candidate", subject="session:s1"), "missing")

    def test_an_engine_return_without_sources_leaves_a_finding_seats_yield_unmeasured(self):
        findings = [{"id": "F1", "agent": self.SEAT, "file": "src/a.cs:1", "description": "d"}]
        ret = self.engine_return([dict(ENGINE_PER_AGENT_TWO, count=1, mandates={"cq.C": "findings", "cq.D": "clean"}),
                                  dict(ENGINE_PER_AGENT_TWO, key="quiet-seat", count=0,
                                       mandates={"cq.C": "clean", "cq.D": "clean"})], findings)
        roster = self.roster(self.seat(self.SEAT, ("cq.C", "cq.D")), self.seat("quiet-seat", ("cq.C", "cq.D")))
        result = self.record("--result", str(self.save(ret, "unsourced.json")), "--files", "src/a.cs",
                             "--roster", str(roster), stage="pre-merge", subject="pr:1")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        by_seat = {(r["seat"], r["mandate"]): r for r in self.rows()}
        self.assertIsNone(by_seat[(self.SEAT, "cq.C")]["findingFiles"])
        self.assertIsNone(by_seat[(self.SEAT, "cq.C")]["seatLedgerNew"])
        self.assertEqual(by_seat[("quiet-seat", "cq.C")]["findingFiles"], [])
        self.assertEqual(by_seat[("quiet-seat", "cq.C")]["seatLedgerNew"], 0)

    def test_an_engine_seat_takes_its_finding_paths_from_its_sources_not_the_agent_tag(self):
        merged = [{"id": "M1", "agent": "seat-code-reviewer:cq.C", "file": "src/a.cs:1", "description": "d"}]
        ret = self.engine_return([dict(ENGINE_PER_AGENT_TWO, count=1, mandates={"cq.C": "findings", "cq.D": "clean"})],
                                 merged)
        ret["sources"] = [{"id": "F1", "lens": self.SEAT, "index": 0,
                           "finding": {"agent": "seat-code-reviewer:cq.C", "file": "src/a.cs:1", "description": "d"}},
                          {"id": "F2", "lens": "other-seat", "index": 0,
                           "finding": {"agent": "other-seat:cq.D", "file": "src/z.cs:1", "description": "d"}}]
        roster = self.roster(self.seat(self.SEAT, ("cq.C", "cq.D")))
        result = self.record("--result", str(self.save(ret, "tagged.json")), "--files", "src/a.cs",
                             "--roster", str(roster), stage="pre-merge", subject="pr:1")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = [r for r in self.rows() if r.get("seat") == self.SEAT]
        self.assertEqual({tuple(r["findingFiles"]) for r in rows}, {("src/a.cs",)})
        self.assertEqual({r["seatLedgerNew"] for r in rows}, {1})

    def test_a_sidecar_seat_takes_its_finding_paths_from_its_sources(self):
        prepared = {"counts": {}, "sources": [
            {"id": "F1", "seat": "sa-seat", "index": 0, "finding": {"agent": "cq.D", "file": "src/a.cs:1"}},
            {"id": "F2", "seat": "sa-other", "index": 0, "finding": {"agent": "cq.D", "file": "src/z.cs:1"}}],
            "coverage": [{"label": "sa-seat", "mandates": ["cq.C", "cq.D"], "uncoveredMandates": [],
                          "status": "completed", "valid": 1,
                          "checked": {"toolsUsed": ["Read:src/a.cs"], "stoppedAt": "exhausted-leads"}}]}
        roster = self.roster(self.seat("sa-seat", ("cq.C", "cq.D")))
        result = self.record("--sidecar-result", str(self.save(prepared, "prepared.json")), "--files", "src/a.cs",
                             "--roster", str(roster), stage="pre-merge", subject="pr:1")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        rows = self.rows()
        self.assertEqual({tuple(r["findingFiles"]) for r in rows}, {("src/a.cs",)})
        self.assertEqual({r["seatLedgerNew"] for r in rows}, {1})

    def test_a_copy_that_ignores_ancestry_counts_the_non_ancestor_row_as_reported(self):
        source = TOOL.read_text(encoding="utf-8").replace(
            "TOOLS_DIR = Path(__file__).resolve().parent\n", "TOOLS_DIR = Path(%r)\n" % str(TOOLS))
        marker = 'if __name__ == "__main__":'
        self.assertIn(marker, source)
        mutant = self.root / "mutant_ancestry.py"
        mutant.write_text(source.replace(marker, "Ctx.reaches = lambda self, sha, path=None: True\n\n" + marker),
                          encoding="utf-8")
        self.assertEqual(self.new_count(self.earlier(head=self.side_head), tool=mutant), 0)


class CrossFamilyTests(LedgerFixture):
    """`cross_family` over a planted registry: two executor rows of family `fa`, one of `fb`."""

    DATA = {"transports": {"ta": {}, "tb": {}}, "models": [
        {"id": "a1", "alias": "a1", "family": "fa", "transport": "ta", "roles": ["executor"]},
        {"id": "a2", "alias": "a2", "family": "fa", "transport": "ta", "roles": ["executor"]},
        {"id": "b1", "alias": "b1", "family": "fb", "transport": "tb", "roles": ["executor"]}]}

    def setUp(self):
        super().setUp()
        self.mod = load_module(TOOL)
        self.data = json.loads(json.dumps(self.DATA))

    def room_everywhere(self):
        return {"transport": "ta", "room": 1.0, "payOptions": [],
                "ranked": [{"transport": t, "room": 1.0} for t in sorted(self.data["transports"])]}

    def test_pre_merge_over_one_family_returns_another_family_candidate(self):
        out = self.mod.cross_family(True, ["fa"], [], "executor", self.data, self.room_everywhere())
        self.assertTrue(out["required"])
        self.assertEqual(out["usedFamilies"], ["fa"])
        self.assertEqual([(c["id"], c["family"], c["degraded"]) for c in out["candidates"]], [("b1", "fb", False)])

    def test_with_one_family_registered_it_returns_a_same_family_row_marked_degraded(self):
        one = dict(self.data, models=[m for m in self.data["models"] if m["family"] == "fa"])
        out = self.mod.cross_family(True, ["fa"], ["a1"], "executor", one, self.room_everywhere())
        self.assertTrue(out["required"])
        self.assertEqual([(c["id"], c["family"], c["degraded"]) for c in out["candidates"]], [("a2", "fa", True)])

    def test_pick_reaches_provider_bands_through_its_public_seams(self):
        bands = self.mod._import("provider_bands")
        self.assertTrue(callable(getattr(bands, "session_plan", None)) and callable(bands.pick))

    def test_a_stage_that_is_not_pre_merge_does_not_require_one(self):
        out = self.mod.cross_family(False, [], [], "executor", self.data, self.room_everywhere())
        self.assertFalse(out["required"])

    def test_no_room_anywhere_names_why(self):
        none = {"transport": None, "room": None, "payOptions": [], "ranked": []}
        out = self.mod.cross_family(True, ["fa"], [], "executor", self.data, none)
        self.assertEqual(out["candidates"], [])
        self.assertTrue(out.get("why"))

    def test_plan_output_carries_cross_family_only_for_pre_merge_stages(self):
        self.plant_rows(self.row(subject="pr:7", family="alpha"))
        merge = self.kept(self.plan([self.seat()], subject="pr:7", stage="pre-merge", attrs=NO_PICK))
        self.assertEqual(merge["roster"], [])
        self.assertTrue(merge["crossFamily"]["required"])
        self.assertEqual(merge["crossFamily"]["reusedBy"], [])
        self.assertEqual(merge["crossFamily"]["usedFamilies"], ["alpha"])
        candidate = self.kept(self.plan([self.seat()], stage="candidate"))
        self.assertNotIn("crossFamily", candidate)
        pr_review = self.kept(self.plan([self.seat()], subject="session:s1", stage="pr-review", attrs=NO_PICK))
        self.assertNotIn("crossFamily", pr_review)
        pr_mode = self.kept(self.plan([self.seat()], subject="pr:7", stage="pr-review", attrs=NO_PICK))
        self.assertTrue(pr_mode["crossFamily"]["required"])

    def test_the_pre_merge_tier_follows_the_code_reviewer_lens_key_exactly_never_a_substring(self):
        asked = []
        pick = {"_pick": lambda tier: asked.append(tier) or {"transport": None, "room": None, "payOptions": [],
                                                              "ranked": []}}
        for key, expected in (("lane-code-reviewer", "executor"), ("lane-code-reviewer-lite", "architect"),
                              ("decode-reviewer", "architect")):
            with self.subTest(key=key):
                del asked[:]
                self.kept(self.plan([self.seat(key, tier="architect")], subject="pr:7", stage="pre-merge",
                                    attrs=pick))
                self.assertEqual(asked, [expected])


class OwedStagedTests(LedgerFixture):
    """`owed_staged` is the function the commit check calls: the same pairs `uncovered` prints, for staged blobs."""

    def call(self, hashes, mandates=("cq.C",), roster=None):
        mod = load_module(TOOL)
        saved, cwd = dict(os.environ), os.getcwd()
        os.environ.update(self.env())
        os.chdir(self.repo)
        try:
            return mod.owed_staged(self.repo, hashes, list(mandates), "main", roster)
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(saved)

    def test_a_covered_staged_blob_is_not_owed(self):
        self.plant_rows(self.row())
        self.assertEqual(self.call({"src/a.cs": self.blob("src/a.cs")}, roster=[self.seat()]), [])

    def test_with_no_roster_a_staged_blob_only_panel_rows_cover_is_owed_depth_unchecked(self):
        self.plant_rows(self.row())
        self.assertEqual(self.call({"src/a.cs": self.blob("src/a.cs")}),
                         [("src/a.cs", "cq.C", "depth-unchecked")])

    def test_an_uncovered_staged_blob_is_owed_and_a_different_blob_is_not_covered_by_the_old_row(self):
        self.plant_rows(self.row())
        self.assertEqual(self.call({"src/a.cs": "a" * 40}), [("src/a.cs", "cq.C")])
        self.assertEqual(self.call({"src/new.cs": "b" * 40}), [("src/new.cs", "cq.C")])

    def test_a_missing_ledger_owes_every_pair(self):
        self.assertEqual(self.call({"src/a.cs": self.blob("src/a.cs")}, ("cq.C", "cq.D")),
                         [("src/a.cs", "cq.C"), ("src/a.cs", "cq.D")])

    def test_an_unreadable_escape_log_raises_naming_the_path(self):
        self.plant_rows(self.row())
        self.escape_log.write_text("{not json\n", encoding="utf-8")
        mod = load_module(TOOL)
        with self.assertRaises(mod.LedgerUnreadable) as caught:
            self.call({"src/a.cs": self.blob("src/a.cs")})
        self.assertIn(str(self.escape_log), str(caught.exception))

    def test_an_unreadable_ledger_raises_naming_the_path_and_never_reads_as_covered(self):
        self.ledger.mkdir(parents=True)
        mod = load_module(TOOL)
        with self.assertRaises(mod.LedgerUnreadable) as caught:
            self.call({"src/a.cs": self.blob("src/a.cs")})
        self.assertIn(str(self.ledger), str(caught.exception))


class LedgerPathInSubmoduleTests(unittest.TestCase):
    def test_a_submodule_checkout_is_refused_rather_than_given_a_dead_path(self):
        module = load_module(TOOL)
        base = Path(tempfile.mkdtemp(prefix="rl_submodule_"))
        self.addCleanup(shutil.rmtree, base, True)
        git = ["git", "-c", "protocol.file.allow=always", "-c", "user.name=t", "-c", "user.email=t@t"]
        for name in ("lib", "app"):
            subprocess.run(["git", "init", "-q", "-b", "main", str(base / name)], check=True)
            subprocess.run(git + ["-C", str(base / name), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
        subprocess.run(git + ["-C", str(base / "app"), "submodule", "add", "-q", str(base / "lib"), "lib"], check=True)
        with patch.dict(os.environ, {"HARNESS_REVIEW_LEDGER": ""}):
            with self.assertRaises(module.ToolError) as caught:
                module.ledger_path(base / "app" / "lib")
        self.assertIn("submodule", str(caught.exception))


class SubmoduleReviewTests(RecordBase):
    """A review rooted at a superproject records and covers files under its submodules like parent files."""

    GIT = ["git", "-c", "protocol.file.allow=always", "-c", "user.name=t", "-c", "user.email=t@t"]

    def sh(self, cwd, *args):
        done = subprocess.run(self.GIT + list(args), cwd=cwd, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def setUp(self):
        super().setUp()
        lib = self.root / "lib_origin"
        subprocess.run(["git", "init", "-q", "-b", "main", str(lib)], check=True)
        (lib / "core").mkdir()
        (lib / "core" / "g.cs").write_text("one\n", encoding="utf-8")
        self.sh(lib, "add", "-A")
        self.sh(lib, "commit", "-q", "-m", "L1")
        (lib / "core" / "g.cs").write_text("one\ntwo\n", encoding="utf-8")
        self.sh(lib, "commit", "-q", "-am", "L2")
        self.sh(self.repo, "submodule", "add", "-q", str(lib), "lib")
        self.sh(self.repo, "add", "-A")
        self.sh(self.repo, "commit", "-q", "-m", "S")
        self.sub = self.repo / "lib"
        self.tip = self.sh(self.repo, "rev-parse", "HEAD")
        self.sub_blob = self.sh(self.sub, "rev-parse", "HEAD:core/g.cs")
        self.diffs = {"lib": self.save_text("lib.diff", self.sh(self.sub, "diff", "HEAD~1", "HEAD")),
                      "app": self.save_text(
                          "app.diff", "diff --git a/src/a.cs b/src/a.cs\n--- a/src/a.cs\n+++ b/src/a.cs\n@@ -1 +1 @@\n-x\n+one\n")}

    def save_text(self, name, text):
        path = self.root / name
        path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        return path

    def blobs(self):
        return self.save({"src/a.cs": self.blob("src/a.cs"), "lib/core/g.cs": self.sub_blob}, "blobs.json")

    def record_review(self, *extra):
        spans = [{"path": str(path), "lines": len(path.read_text(encoding="utf-8").splitlines()),
                  "read": [[1, len(path.read_text(encoding="utf-8").splitlines())]]} for path in self.diffs.values()]
        coverage = {"label": "sa-seat", "mandates": ["cq.C"], "uncoveredMandates": [], "status": "completed",
                    "valid": 0, "gaps": [], "mustReadEvidence": "observed", "mustReadSpans": spans,
                    "checked": {"toolsUsed": ["Read"], "stoppedAt": "exhausted-leads"}}
        prepared = self.save({"counts": {}, "sources": [], "coverage": [coverage]}, "prepared.json")
        roster = self.roster(self.seat("sa-seat", ("cq.C",)))
        return self.record("--sidecar-result", str(prepared), "--roster", str(roster), "--head", self.tip,
                           "--files", "src/a.cs", "lib/core/g.cs", *extra)

    def test_a_review_of_a_submodule_diff_records_a_covering_row_for_the_submodule_file(self):
        done = self.record_review("--blobs", str(self.blobs()))
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        covered = {path: row["outcome"] for row in self.rows() for path in row["files"]}
        self.assertEqual(covered, {"src/a.cs": "clean", "lib/core/g.cs": "clean"})

    def test_a_covered_submodule_file_is_not_uncovered(self):
        self.assertEqual(self.record_review("--blobs", str(self.blobs())).returncode, 0)
        self.assertEqual(self.pairs(self.uncovered(("src/a.cs", "lib/core/g.cs"))), [])

    def test_a_blob_only_the_submodule_holds_is_trusted_as_a_parent_blob_is(self):
        (self.sub / "core" / "g.cs").write_text("folded after the review\n", encoding="utf-8")
        done = self.record_review("--blobs", str(self.blobs()))
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        self.assertIn("lib/core/g.cs", {path for row in self.rows() for path in row["files"]})

    def test_no_blob_map_hashes_a_submodule_file_at_the_snapshot(self):
        done = self.record_review()
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        self.assertEqual({path: blob for row in self.rows() for path, blob in row["files"].items()}["lib/core/g.cs"],
                         self.sub_blob)


class WorktreeBlobTests(unittest.TestCase):
    """A path under a linked worktree of the main repo hashes from that worktree's own commits at the snapshot."""

    GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]

    def sh(self, cwd, *args):
        done = subprocess.run(self.GIT + list(args), cwd=cwd, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rl_worktree_"))
        self.main = self.tmp / "main"
        self.main.mkdir()
        self.sh(self.main, "init", "-q", "-b", "main")
        (self.main / "a.txt").write_text("main\n", encoding="utf-8")
        self.sh(self.main, "add", "-A")
        self.sh(self.main, "commit", "-q", "-m", "M")
        self.wt = self.main / ".claude" / "worktrees" / "sub"
        self.sh(self.main, "worktree", "add", "-q", "-b", "sub", str(self.wt))
        (self.wt / "f.cs").write_text("worktree\n", encoding="utf-8")
        self.sh(self.wt, "add", "-A")
        self.sh(self.wt, "commit", "-q", "-m", "W")
        self.tip = self.sh(self.wt, "rev-parse", "HEAD")
        self.blob = self.sh(self.wt, "rev-parse", "HEAD:f.cs")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_worktree_file_hashes_at_the_snapshot_commit(self):
        ctx = load_module(TOOL).Ctx(self.main, "main", snapshot=self.tip)
        self.assertEqual(ctx.hash(".claude/worktrees/sub/f.cs"), self.blob)

    def test_a_worktree_file_absent_at_the_snapshot_has_no_blob(self):
        ctx = load_module(TOOL).Ctx(self.main, "main", snapshot=self.tip)
        self.assertIsNone(ctx.hash(".claude/worktrees/sub/gone.cs"))


    def test_worktree_files_hash_in_one_cat_file_call_per_worktree(self):
        (self.wt / "g.cs").write_text("second\n", encoding="utf-8")
        self.sh(self.wt, "add", "-A")
        self.sh(self.wt, "commit", "-q", "-m", "W2")
        tip = self.sh(self.wt, "rev-parse", "HEAD")
        module = load_module(TOOL)
        calls = []
        real = module._git

        def spy(repo, args, stdin=None):
            if args[:1] == ["cat-file"]:
                calls.append(str(repo))
            return real(repo, args, stdin=stdin)

        ctx = module.Ctx(self.main, "main", snapshot=tip)
        with patch.object(module, "_git", spy):
            got = ctx.hash_files([".claude/worktrees/sub/f.cs", ".claude/worktrees/sub/g.cs"])
        self.assertEqual(got[".claude/worktrees/sub/f.cs"], self.blob)
        self.assertIsNotNone(got[".claude/worktrees/sub/g.cs"])
        self.assertEqual(len(calls), 1, calls)

    def test_a_main_rooted_key_finds_the_row_a_worktree_subject_recorded(self):
        from types import SimpleNamespace
        module = load_module(TOOL)
        key = ".claude/worktrees/sub/f.cs"
        row = {"files": {"f.cs": self.blob}}
        ctx = module.Ctx(self.main, "main", snapshot=self.tip)
        cover = SimpleNamespace(exception=lambda r: False, _resolving_blob=None, ctx=ctx)
        self.assertTrue(module._clause_content(cover, row, "m", key))
        self.assertFalse(module._clause_content(cover, {"files": {"f.cs": "0" * 40}}, "m", key))
        self.assertEqual(module.row_blob(row, key), self.blob)
        self.assertIsNone(module.row_blob(row, ".claude/worktrees/sub/other.cs"))

    def test_worktree_split_names_the_worktree_root_and_the_rest_of_a_main_rooted_path(self):
        module = load_module(TOOL)
        self.assertEqual(module.worktree_split(".claude/worktrees/sub/f.cs"), (".claude/worktrees/sub", "f.cs"))
        self.assertEqual(module.worktree_split(".claude/worktrees/sub/a/b.cs"), (".claude/worktrees/sub", "a/b.cs"))
        self.assertIsNone(module.worktree_split(".claude/worktrees/sub"))
        self.assertIsNone(module.worktree_split("src/a.cs"))


class SubmodulePointerMoveTests(RecordBase):
    """A superproject commit that moves a gitlink owes the files the submodule changed between the two pins,
    as `<gitlink>/<file>`; a deleted one owes its submodule tombstone; an unreadable pin fails closed."""

    GIT = SubmoduleReviewTests.GIT
    sh = SubmoduleReviewTests.sh
    MOVED = ["lib/core/gone.cs", "lib/core/mod.cs", "lib/core/new.cs"]

    def setUp(self):
        super().setUp()
        self.origin = self.root / "lib_origin"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.origin)], check=True)
        (self.origin / "core").mkdir()
        for name, text in (("keep.cs", "k\n"), ("gone.cs", "g\n"), ("mod.cs", "m1\n")):
            (self.origin / "core" / name).write_text(text, encoding="utf-8")
        self.sh(self.origin, "add", "-A")
        self.sh(self.origin, "commit", "-q", "-m", "L1")
        self.sh(self.repo, "submodule", "add", "-q", str(self.origin), "lib")
        self.sh(self.repo, "add", "-A")
        self.sh(self.repo, "commit", "-q", "-m", "S1 add lib")
        self.base = self.sh(self.repo, "rev-parse", "HEAD")
        (self.origin / "core" / "mod.cs").write_text("m2\n", encoding="utf-8")
        (self.origin / "core" / "new.cs").write_text("n\n", encoding="utf-8")
        (self.origin / "core" / "gone.cs").unlink()
        self.sh(self.origin, "add", "-A")
        self.sh(self.origin, "commit", "-q", "-m", "L2")
        self.l2 = self.sh(self.origin, "rev-parse", "HEAD")
        self.sub = self.repo / "lib"
        self.sh(self.sub, "pull", "-q", "origin", "main")
        self.sh(self.repo, "commit", "-q", "-am", "S2 move lib")
        self.tip = self.sh(self.repo, "rev-parse", "HEAD")

    def module(self):
        module = load_module(TOOL)
        return module

    def owed(self):
        listed = self.run_tool("owed", "--base-ref", self.base, "--mandates", "cq.C")
        self.assertEqual(listed.returncode, 0, listed.stderr.decode())
        return listed.stdout.decode()

    def covering(self, *skip):
        states = {path: self.sh(self.sub, "rev-parse", "HEAD:" + path[len("lib/"):])
                  for path in ("lib/core/mod.cs", "lib/core/new.cs")}
        states["lib/core/gone.cs"] = "deleted:" + self.l2
        return self.row("cq.C", head=self.tip, mergeBase=self.base, reviewer="inline", id="inline1:cq.C",
                        files={path: state for path, state in states.items() if path not in skip})

    def test_a_pointer_move_lists_the_submodule_files_it_changed_not_the_gitlink(self):
        commits = self.module().range_commits(self.repo, self.base, self.tip)
        self.assertEqual([(c["subject"], c["files"]) for c in commits], [("S2 move lib", self.MOVED)])

    def test_a_deleted_submodule_file_gets_its_submodule_tombstone(self):
        module = self.module()
        self.assertEqual(module.Ctx(self.repo, self.base, snapshot=self.tip).deleted_hash("lib/core/gone.cs"),
                         "deleted:" + self.l2)

    def test_each_submodule_pair_is_owed_until_a_row_covers_it(self):
        self.assertIn("S2 move lib", self.owed())
        for path in self.MOVED:
            with self.subTest(uncovered=path):
                self.plant_rows(self.covering(path))
                self.assertIn("S2 move lib", self.owed())
        self.plant_rows(self.covering())
        self.assertEqual(self.owed().strip(), "")

    def test_a_pinned_commit_absent_locally_fails_closed(self):
        absent = self.sh(self.origin, "commit-tree", "-p", self.l2, "-m", "never fetched", self.l2 + "^{tree}")
        self.sh(self.repo, "update-index", "--cacheinfo", "160000,%s,lib" % absent)
        self.sh(self.repo, "commit", "-q", "-m", "S3 pin an unfetched commit")
        module = self.module()
        tip = self.sh(self.repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(module.LedgerUnreadable, "lib"):
            module.range_commits(self.repo, self.tip, tip)
        with self.assertRaisesRegex(module.LedgerUnreadable, "lib"):
            module.Ctx(self.repo, self.base, snapshot=tip).deleted_hash("lib/core/gone.cs")
        listed = self.run_tool("owed", "--base-ref", self.tip, "--mandates", "cq.C")
        self.assertEqual(listed.returncode, 2, listed.stdout.decode())


class FilesFromTests(RecordBase):
    """`--files-from <file>` names the same paths as `--files`: one per line, CRLF or LF, blank lines skipped."""
    PATHS = ["src/a.cs", "src/b.cs"]
    LIVE_QUOTA_KEYS = {"bandFacts", "currencyReason", "headroomVerified", "payAvailable"}

    def setUp(self):
        super().setUp()
        self._write("src/b.cs", "two\n")
        self._commit("C")
        self.list_file = self.root / "files.txt"
        self.list_file.write_bytes("\r\n\r\n".join(self.PATHS).encode("utf-8") + b"\r\n")

    def assertSameOutcome(self, by_flag, by_file):
        self.assertEqual((by_file.returncode, by_file.stdout, by_file.stderr),
                         (by_flag.returncode, by_flag.stdout, by_flag.stderr))

    def test_uncovered_reads_the_same_list(self):
        by_flag = self.run_tool("uncovered", "--files", *self.PATHS, "--mandates", "cq.C", "--base-ref", "main")
        by_file = self.run_tool("uncovered", "--files-from", str(self.list_file), "--mandates", "cq.C",
                                "--base-ref", "main")
        self.assertSameOutcome(by_flag, by_file)
        self.assertIn("src/b.cs", by_file.stdout.decode())

    def test_uncovered_894_paths_from_a_list_file_match_the_flag_form(self):
        paths = ["gone/dir%d/file%d.cs" % (i // 30, i) for i in range(894)]
        listing = self.root / "894.txt"
        listing.write_text("\n".join(paths) + "\n", encoding="utf-8")
        by_flag = self.run_tool("uncovered", "--files", *paths, "--mandates", "cq.C", "--base-ref", "main")
        by_file = self.run_tool("uncovered", "--files-from", str(listing), "--mandates", "cq.C", "--base-ref", "main")
        self.assertSameOutcome(by_flag, by_file)
        self.assertEqual(by_file.stderr.decode().count("absent, nothing to review: "), 894)

    def test_uncovered_needs_files_or_files_from(self):
        result = self.run_tool("uncovered", "--mandates", "cq.C", "--base-ref", "main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--files-from", result.stderr.decode())

    def test_missing_list_file_exits_2_naming_it(self):
        missing = self.root / "nope.txt"
        result = self.run_tool("uncovered", "--files-from", str(missing), "--mandates", "cq.C", "--base-ref", "main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("nope.txt", result.stderr.decode())

    def test_plan_reads_the_same_list(self):
        roster = [self.seat("seat-code-reviewer", ("cq.C",))]
        by_flag = self.plan(roster, files=self.PATHS)
        by_file = self.plan(roster, "--files-from", str(self.list_file), files=())

        def without_live_quota(node):
            if isinstance(node, dict):
                return {k: without_live_quota(v) for k, v in node.items() if k not in self.LIVE_QUOTA_KEYS}
            if isinstance(node, list):
                return [without_live_quota(v) for v in node]
            return node

        self.assertEqual((by_file.returncode, by_file.stderr), (by_flag.returncode, by_flag.stderr))
        self.assertEqual(without_live_quota(self.kept(by_file)), without_live_quota(self.kept(by_flag)))

    def test_whole_scope_reads_the_same_list(self):
        by_flag = self.run_tool("whole-scope", "--subject", "session:s1", "--files", *self.PATHS, "--base-ref", "main")
        by_file = self.run_tool("whole-scope", "--subject", "session:s1", "--files-from", str(self.list_file),
                                "--base-ref", "main")
        self.assertSameOutcome(by_flag, by_file)

    def test_record_files_from_records_the_same_files(self):
        ret = self.save(self.engine_return([dict(ENGINE_PER_AGENT_TWO, mandates={"cq.C": "clean", "cq.D": "clean"})]))
        roster = self.roster(self.seat("seat-code-reviewer", ("cq.C", "cq.D")))
        flagged = self.record("--result", str(ret), "--files", *self.PATHS, "--roster", str(roster))
        by_flag = [row["files"] for row in self.rows()]
        self.reset_ledger()
        by_file = self.record("--result", str(ret), "--files-from", str(self.list_file), "--roster", str(roster))
        self.assertEqual(by_file.returncode, flagged.returncode, by_file.stderr.decode())
        self.assertTrue(by_flag)
        self.assertEqual([row["files"] for row in self.rows()], by_flag)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_sharded
    sys.exit(run_sharded(__file__))
