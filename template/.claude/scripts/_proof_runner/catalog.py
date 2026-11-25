import argparse
import fnmatch
import io
import json
import os
import posixpath
import re
import sys
import tokenize
from functools import cached_property
from typing import NamedTuple

import adaptation
import _git_run
from _hook_state import CODE_EXT, SETUP_SURFACE_FILES

PROOF_EXTS = (".py", ".js", ".mjs", ".sh", ".ps1")
PATTERNS = tuple(form % ext for ext in PROOF_EXTS for form in ("test_*%s", "*_test%s"))
COMMIT_BUDGET_S, PROOF_BUDGET_S = 30, 10
TESTS = ".claude/tests"
FLOOR = TESTS + "/floor"
# The loss floor's table rows (`tests/floor/test_floor_*.py`) each judge the whole floor through `pre_bash_dispatch`, so
# a change to any module below, or to a helper beside the rows, selects every row; a row selects itself. `discover`
# does not recurse into `floor/`, so `Catalog` finds the rows itself.
FLOOR_MODULES = tuple(".claude/hooks/%s.py" % name for name in (
    "pre_bash", "pre_bash_dispatch", "dangerous_shell_guard", "kill_guard", "baseline_classification_guard",
    "editor_live_build_guard", "sidecar_dispatch_context", "_delete_rule", "_own_worktree", "_session_lineage",
    "_shell_lex", "_git_commit", "_commit_scope", "_git_run")) + (".claude/githooks/commit-msg",)
ALWAYS = ("test_hook_state.py",)
CORPUS = {
    "test_hook_state.py": (".claude/hooks",),
    "test_claudemd_citations.py": (".claude",),
    "test_guard_text.py": (".claude/guards",),
    "test_layer_closure.py": (".claude",),
}
_PART = re.compile(r"^(.+)_part\d+(\.\w+)$")
_IMPORT_DIRS = tuple(f".claude/{d}{sub}" for d in ("hooks", "tools", "scripts") for sub in ("", "/lib"))
_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b[ \t]*(\([^)]*\)|[^\n#;]*)|import[ \t]+([\w., \t]+))", re.MULTILINE)
_STRING_MODULE = re.compile(r"""(?<![\w.])[rRuU]?(['"])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\1""")
_RUN_MODULE = re.compile(r"""(?:^|[\s'"])-m['"]?[ \t]*,?[ \t]*['"]?([A-Za-z_][\w.]*)""")
_JS_REQUIRE = re.compile(r"""(?:\brequire\(|\bimport\(|\bfrom)\s*(['"])(\.{1,2}/[^'"]+)\1""")
_LINE_COMMENT = {".ps1": "#", ".sh": "#", ".js": "//", ".mjs": "//"}
_BLOCK_COMMENT = {".ps1": re.compile(r"^[ \t]*<#(?:(?!#>).)*#>[ \t]*$", re.MULTILINE | re.DOTALL),
                  ".js": re.compile(r"^[ \t]*/\*(?:(?!\*/).)*\*/[ \t]*$", re.MULTILINE | re.DOTALL)}
_BLOCK_COMMENT[".mjs"] = _BLOCK_COMMENT[".js"]


def norm(path):
    return os.fspath(path).replace(os.sep, "/")


def proof_name(name):
    return not name.startswith("_") and any(fnmatch.fnmatch(name, p) for p in PATTERNS)


def discover(tests_dir, *, paths=None):
    if paths is None:
        try:
            with os.scandir(tests_dir) as entries:
                paths = [entry.path for entry in entries if entry.is_file()]
        except FileNotFoundError:
            return []
    return sorted(p for p in paths if os.path.dirname(p) == tests_dir and proof_name(os.path.basename(p)))


def floor_rows(files):
    """The loss floor's table rows among `files` (repository paths)."""
    return sorted(rel for rel in files if posixpath.dirname(rel) == FLOOR and rel.endswith(".py")
                  and proof_name(posixpath.basename(rel)))


def named_for(name, stem):
    return (name.startswith("test_" + stem) or name.startswith(stem + "_test")
            or (name.startswith(stem + "_") and re.search(r"_test\.\w+$", name) is not None))


def part_main(path, exists=os.path.isfile):
    found = _PART.match(os.path.basename(path))
    main = os.path.join(os.path.dirname(path), ''.join(found.groups())) if found else None
    return main if main and exists(main) else None


def shipped_row(path):
    folder, name = posixpath.split(path)
    stem, ext = posixpath.splitext(name)
    return (folder == TESTS and proof_name(name) and
            ((ext == '.py' and stem.startswith('test_') and len(stem) > len('test_')) or
             (ext in ('.js', '.mjs') and stem.endswith('_test') and len(stem) > len('_test'))))


def _object(text):
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _table(doc, key):
    rows = doc.get(key, {})
    return rows if isinstance(rows, dict) else {}


def _lists(doc, key):
    rows = doc.get(key, {})
    if not isinstance(rows, dict):
        print(f"harness_tests: adaptation.json {key} is not an object -- skipped", file=sys.stderr)
        return {}
    valid = {}
    for name, items in rows.items():
        if isinstance(items, list) and all(isinstance(item, str) for item in items):
            valid[name] = items
        else:
            label = "proof names" if key == "proof_bindings" else "directories"
            print(f"harness_tests: {key} entry {name!r} is not a list of {label} -- skipped", file=sys.stderr)
    return valid


class Policy(NamedTuple):
    excluded: dict
    heavy: dict
    serial: dict
    declared: dict
    corpus: dict

    @classmethod
    def read(cls, view):
        claude = view.full('.claude')
        if view.tree is None:
            return cls.read_worktree(claude)
        return cls.from_document(_object(view.text(norm(adaptation.adaptation_path('.claude')))))

    @classmethod
    def read_worktree(cls, claude):
        try:
            text = adaptation.adaptation_path(claude).read_text(encoding='utf-8')
        except OSError:
            text = ''
        doc = _object(text)
        doc.update({key: adaptation.get(claude, key) for key in ('proof_excluded', 'proof_heavy', 'proof_serial', 'proof_corpus')})
        return cls.from_document(doc)

    @classmethod
    def from_document(cls, doc):
        return cls(*(_table(doc, key) for key in ("proof_excluded", "proof_heavy", "proof_serial")),
                   _lists(doc, "proof_bindings"),
                   {name: tuple(folders) for name, folders in _lists(doc, "proof_corpus").items()})

    def corpus_dirs(self, name):
        return CORPUS.get(name) or self.corpus.get(name, ())


class View:
    def __init__(self, root, tree):
        self.root, self.tree = os.fspath(root), tree

    @classmethod
    def worktree(cls, root):
        return cls(root, None)

    @classmethod
    def index(cls, root):
        return cls(root, "")

    @classmethod
    def revision(cls, root, rev):
        if not rev:
            raise ValueError("a revision view needs a revision")
        return cls(root, rev)

    def full(self, rel):
        return os.path.join(self.root, *rel.split('/'))

    def _git(self, args, *, required=False):
        out = _git_run.run(args, cwd=self.root, input=b"", timeout=None)
        if out.returncode:
            if required:
                raise RuntimeError(f"git {args[0]} failed in {self.root}: {out.stderr.decode('utf-8', 'replace').strip()}")
            return ""
        return out.stdout.decode("utf-8", "replace")

    @cached_property
    def tracked(self):
        args = ["ls-tree", "-rz", "--name-only", self.tree] if self.tree else ["ls-files", "-z"]
        return sorted(set(self._git([*args, "--", ".claude"], required=self.tree is not None).split('\0')) - {""})

    @cached_property
    def files(self):
        files = set(self.tracked)
        if self.tree is None:
            files.update(filter(None, self._git(["ls-files", "-zo", "--exclude-standard", "--", ".claude"]).split('\0')))
            for folder, subdirs, names in os.walk(self.full(TESTS)):
                subdirs[:] = [d for d in subdirs if d not in ("__pycache__", "logs")]
                files.update(norm(os.path.relpath(os.path.join(folder, n), self.root)) for n in names)
            files.update(rel for rel in SETUP_SURFACE_FILES if os.path.isfile(self.full(rel)))
        return files

    def text(self, rel):
        if self.tree is not None:
            return self._git(["show", self.tree + ":" + rel])
        try:
            with open(self.full(rel), "rb") as fh:
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def exists(self, rel):
        return (os.path.isfile(self.full(norm(rel))) if self.tree is None else norm(rel) in self.files)

    def discover(self):
        if self.tree is None:
            return [norm(os.path.relpath(p, self.root)) for p in discover(self.full(TESTS))]
        return [p for p in discover(TESTS, paths=self.tracked)]


def _python_code(text):
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, ValueError):
        return text
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def offset(row_col):
        return starts[row_col[0] - 1] + row_col[1]

    spans, statement_start, i = [], True, 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == tokenize.COMMENT:
            spans.append((offset(tok.start), offset(tok.end)))
        elif tok.type == tokenize.STRING and statement_start:
            j = i
            while tokens[j].type in (tokenize.STRING, tokenize.NL, tokenize.COMMENT):
                j += 1
            if tokens[j].type in (tokenize.NEWLINE, tokenize.ENDMARKER):
                spans.extend((offset(t.start), offset(t.end)) for t in tokens[i:j])
                i = j
                continue
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            statement_start = tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
        i += 1
    out, at = [], 0
    for start, end in sorted(spans):
        out.extend((text[at:start], re.sub(r"[^\n]", " ", text[start:end])))
        at = end
    return "".join(out) + text[at:]


def code_text(rel, text):
    ext = posixpath.splitext(rel)[1]
    if ext == ".py":
        return _python_code(text)
    if ext in _BLOCK_COMMENT:
        text = _BLOCK_COMMENT[ext].sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    marker = _LINE_COMMENT.get(ext)
    return ("".join("\n" if line.lstrip().startswith(marker) else line for line in text.splitlines(keepends=True))
            if marker else text)


def _import_statements(code):
    for match in _IMPORT.finditer(code):
        if match[3]:
            for name in match[3].split(','):
                yield name.strip().split(' ')[0], 0, ''
        else:
            dotted = match[1].lstrip('.')
            yield dotted, len(match[1]) - len(dotted), match[2]


class Sources:
    def __init__(self, view):
        self.view = view
        self._texts, self._code, self._loaded = {}, {}, {}
        self.parse_code = code_text

    def _text(self, rel):
        if rel not in self._texts:
            self._texts[rel] = self.view.text(rel)
        return self._texts[rel]

    def _code_of(self, rel):
        if rel not in self._code:
            self._code[rel] = self.parse_code(rel, self._text(rel))
        return self._code[rel]

    @cached_property
    def names(self):
        by_base, by_stem, by_module = {}, {}, {}
        for rel in self.view.files:
            base = rel.rsplit('/', 1)[-1]
            by_base.setdefault(base, set()).add(rel)
            if rel.endswith(CODE_EXT):
                by_stem.setdefault(posixpath.splitext(base)[0], set()).add(rel)
            if rel.endswith('.py'):
                parts = rel[:-3].split('/')
                if parts[-1] == '__init__':
                    parts = parts[:-1]
                for i in range(len(parts)):
                    by_module.setdefault('/'.join(parts[i:]), set()).add(rel)
        return by_base, by_stem, by_module

    def _module_files(self, importer, dotted, level):
        parts = [p for p in dotted.split('.') if p]
        files = self.view.files
        if level:
            base = importer.split('/')[:-1]
            base = base[:max(0, len(base) - (level - 1))]
            exact = '/'.join(base + parts)
            found = {r for r in (exact + '.py', exact + '/__init__.py') if r in files}
        else:
            found = set(self.names[2].get('/'.join(parts), ())) if parts else set()
        for rel in list(found):
            folder = rel.split('/')[:-1]
            while folder:
                init = '/'.join(folder + ['__init__.py'])
                if init in files:
                    found.add(init)
                folder = folder[:-1]
        return found

    def _imports(self, rel):
        if rel in self._loaded:
            return self._loaded[rel]
        code = self._code_of(rel)
        found = set()
        for match in _RUN_MODULE.finditer(code):
            found |= self._module_files(rel, match[1], 0)
        if rel.endswith(('.js', '.mjs')):
            for match in _JS_REQUIRE.finditer(code):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), match[2]))
                found |= {t for t in (target, target + '.js', target + '.mjs', target + '/index.js')
                          if t in self.view.files}
        if rel.endswith('.py'):
            def module(name, level=0):
                return self._module_files(rel, name, level) | {
                    r for r in self.names[1].get(name.split('.')[0], ()) if r.endswith('.py')}

            for match in _STRING_MODULE.finditer(code):
                found |= self._module_files(rel, match[2], 0)
            for dotted, level, from_names in _import_statements(code):
                found |= module(dotted, level)
                for name in from_names.replace('(', ' ').replace(')', ' ').split(','):
                    name = name.split('#')[0].strip().split(' ')[0]
                    if name and name != '*':
                        found |= self._module_files(rel, (dotted + '.' if dotted else '') + name, level)
        self._loaded[rel] = found
        return found


def behavior_plan(repo_root, paths=None):
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'tools')
    sys.path.insert(0, tools)
    try:
        import extract_numbers
    except ImportError:
        return {"proofs": [], "cases": {}, "dependencies": {},
                "unresolved": [{"reason": "behavior checker unavailable"}]}
    finally:
        sys.path.remove(tools)
    return extract_numbers.behavior_proofs(repo_root, paths)


class Selection(NamedTuple):
    proofs: list[str]
    obligations: dict[str, list[str]]


class Candidate(NamedTuple):
    proof_id: str
    state: str


class Catalog:
    """`proofs` are the files `discover` lists. `floor` are the floor's table rows, which `select` binds to the floor's
    modules beside them and no other view of the catalog (`candidates`, `tiers`) counts as a proof."""

    def __init__(self, view, policy, proofs, floor=()):
        self.view, self.policy, self.proofs, self.floor = view, policy, proofs, list(floor)
        self.sources = Sources(view)

    @classmethod
    def open(cls, view, policy=None, *, proofs=None):
        return cls(view, Policy.read(view) if policy is None else policy,
                   view.discover() if proofs is None else list(proofs),
                   floor_rows(view.files) if proofs is None else ())

    @cached_property
    def _tracked_names(self):
        names = {}
        for rel in self.view.tracked:
            names.setdefault(posixpath.basename(rel), set()).add(rel)
        return names

    def bindings(self, touched, include_excluded=False):
        rules = {}
        for rel in dict.fromkeys(map(norm, touched)):
            folder, name = posixpath.split(rel)
            token = posixpath.basename(folder) + '/' + name if folder and self._tracked_names.get(name, set()) - {rel} else name
            rules[rel] = (posixpath.splitext(name)[0], None if rel in self.policy.declared else token,
                          self.policy.declared.get(rel, ()))

        floor = set(self.floor)

        def binds(proof, rel, stem, token, declared):
            name = posixpath.basename(proof)
            if proof in floor and (rel in FLOOR_MODULES or (posixpath.dirname(rel) == FLOOR and rel.endswith('.py')
                                                            and not proof_name(posixpath.basename(rel)))):
                return True
            return proof == rel or (not rel.endswith('.jsonl') and (
                name in ALWAYS or name in declared
                or any(rel.startswith(folder.rstrip('/') + '/') for folder in self.policy.corpus_dirs(name))
                or named_for(name, stem)
                or (token is not None and token in self.sources._text(proof) and token in self.sources._code_of(proof))
                or (rel.endswith(CODE_EXT) and stem in self.sources._text(proof) and rel in self.sources._imports(proof))))

        result = {rel: [] for rel in rules}
        for proof in self.proofs + self.floor:
            if not include_excluded and posixpath.basename(proof) in self.policy.excluded:
                continue
            main = part_main(proof, self.view.exists)
            if main:
                main = norm(main)
            for rel, rule in rules.items():
                if binds(proof, rel, *rule) or (main and binds(main, rel, *rule)):
                    result[rel].append(proof)
        return result

    def select(self, touched, include_excluded=False, *, behavior=behavior_plan):
        original = list(touched)
        touched = list(dict.fromkeys(map(norm, original)))
        obligations = self.bindings(touched, include_excluded)
        bound = {p for paths in obligations.values() for p in paths}
        if self.view.tree is None:
            bound.update(behavior(self.view.root, original)['proofs'])
        return Selection([p for p in self.proofs + self.floor if p in bound], obligations)

    def full_selection(self, touched, include_excluded=False, *, behavior=behavior_plan):
        return [self.view.full(p) for p in self.select(touched, include_excluded, behavior=behavior).proofs]

    def candidates(self, paths):
        paths = list(dict.fromkeys(map(norm, paths)))
        matched = dict.fromkeys(p for p in self.select(paths, include_excluded=True).proofs if p not in self.floor)
        known = {posixpath.basename(p): p for p in discover(TESTS, paths=self.view.tracked)}
        known.update({posixpath.basename(p): p for p in self.proofs})
        for rel in paths:
            if posixpath.dirname(rel) == TESTS and proof_name(posixpath.basename(rel)):
                matched.setdefault(rel)
            for name in self.policy.declared.get(rel, ()):
                matched.setdefault(known.get(name, TESTS + '/' + name))
        return [Candidate(p, 'missing' if posixpath.basename(p) not in known else
                          'unavailable' if p not in self.proofs else
                          'excluded' if posixpath.basename(p) in self.policy.excluded else 'usable') for p in matched]

    @cached_property
    def _module_sources(self):
        if self.view.tree is None:
            paths = (folder + '/' + name for folder in _IMPORT_DIRS if os.path.isdir(self.view.full(folder))
                     for name in os.listdir(self.view.full(folder)))
        else:
            paths = (p for p in self.view.tracked if posixpath.dirname(p) in _IMPORT_DIRS)
        return {p: {dotted for dotted, level, names in _import_statements(self.sources._text(p))
                    if not level and (not names or '.' not in dotted)}
                for p in paths if p.endswith('.py')}

    def _importer_stems(self, tool):
        folder, _, name = tool.rpartition('/')
        if folder not in _IMPORT_DIRS or not name.endswith('.py'):
            return set()
        pending, seen, importers = [name[:-3]], {name[:-3]}, set()
        while pending:
            module = pending.pop()
            for rel, imports in self._module_sources.items():
                if rel == tool or rel in importers or not any(item.split('.')[0] == module for item in imports):
                    continue
                importers.add(rel)
                stem = posixpath.basename(rel)[:-3]
                if rel.startswith('.claude/hooks/') and rel.count('/') == 2 and stem.startswith('_') and stem not in seen:
                    seen.add(stem)
                    pending.append(stem)
        return {posixpath.basename(rel)[:-3] for rel in importers}

    def shipped_for(self, tool):
        stems = {posixpath.splitext(posixpath.basename(tool))[0]} | self._importer_stems(tool)
        return [rel for rel in self.proofs if any(named_for(posixpath.basename(rel), stem) for stem in stems)]

    def tiers(self, inputs, known):
        triggers = {p: [] for p in self.proofs}
        for path, bound in self.bindings(sorted(set(inputs) - self.proofs_set), include_excluded=True).items():
            for proof in bound:
                triggers[proof].append(path)
        rows = []
        for proof in self.proofs:
            name = posixpath.basename(proof)
            seconds = known.get(name)
            numeric = isinstance(seconds, (int, float))
            budget = 'unrecorded' if not numeric else 'inner' if seconds <= PROOF_BUDGET_S else 'close'
            runs = ('excluded' if name in self.policy.excluded else 'heavy' if name in self.policy.heavy else
                    'always' if name in ALWAYS else 'serial' if name in self.policy.serial else 'bound')
            triggers_for = triggers[proof]
            flags = ['slow-always'] if runs == 'always' and numeric and seconds > PROOF_BUDGET_S else []
            if not triggers_for:
                flags.append('no-trigger')
            rows.append({'proof': name, 'seconds': seconds if numeric else 'unrecorded', 'budget': budget,
                         'runs': runs, 'trigger': {'count': len(triggers_for), 'examples': triggers_for[:3]}, 'flags': flags})
        return rows

    @cached_property
    def proofs_set(self):
        return set(self.proofs)


def build_parser(repo_root):
    parser = argparse.ArgumentParser(description="Run harness proofs and stamp unchanged passing inputs.")
    parser.add_argument("--repo", default=repo_root, help="repository containing the proofs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="include normally excluded proofs")
    mode.add_argument("--tiers", action="store_true",
                      help="read-only: print each proof's recorded seconds, budget tier, how it runs and its trigger")
    mode.add_argument("--for", dest="for_paths", nargs="+", metavar="PATH",
                      help="scoped: run only the proofs bound to these harness files and refresh their stamp entries")
    mode.add_argument("--commit", dest="commit_paths", nargs="+", metavar="PATH", help=argparse.SUPPRESS)
    mode.add_argument("--proofs-for", dest="proofs_for_paths", nargs="+", metavar="PATH",
                      help="run only proofs bound to these paths without reading or writing a stamp")
    parser.add_argument('--behavior-report', metavar='OUT', help='write typed claim results and named-case observations')
    parser.add_argument("--verbose", action="store_true", help="print every proof result, not only failures and summary")
    mode.add_argument("--close", action="store_true", help="prove outgoing harness changes at committed HEAD")
    parser.add_argument("--now", action="store_true",
                        help="with --close, start without waiting for this session's author and fold dispatches")
    parser.add_argument("--base", help="upstream revision for --close; for --for, the revision before "
                                       "the change that a red must also fail at to be pre-existing")
    parser.add_argument("--range-start", metavar="REV",
                        help="with --close, prove the commits from REV to HEAD that origin's default branch already holds; "
                             "REV is the close's base, ahead of its upstream and default-branch lookups")
    parser.add_argument("--also", nargs="+", metavar="PROOF",
                        help="with --close --range-start, add these proofs to the selection and run each once, no retry")
    parser.add_argument("--json", action="store_true", help="with --tiers, print the rows as JSON")
    return parser
