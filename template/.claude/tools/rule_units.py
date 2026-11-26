#!/usr/bin/env python3
"""Rule-text units and the behavior-claim check.

A unit is a sentence or table row of rule text (`units`, read by `tools/restate_scan.py`), hashed over its
normalised text. The behavior-claim ledger classifies each unit of in-scope text in
`<rows_dir>/behaviors.jsonl` and binds a behavior claim to its implementation and to a named proof case.
The scope (`paths` globs, `behavior_paths` overriding them, and the row directory) comes from the
`instruction_scope` key of the project adaptation file, read through `tools/adaptation.py`.

  rule_units.py [--repo DIR] check [--base REV | --rev REV] [--staged] [--paths PATH...] [--json] [--proof-results FILE]
  rule_units.py [--repo DIR] list  PATH... [--json]
  rule_units.py [--repo DIR] stub  [--base REV | --rev REV] [--staged] [--paths PATH...]
  rule_units.py [--repo DIR] add   ROWS.jsonl

`check --proof-results <runner JSON> --json` checks named-case observations; without that argument it
reads the existing runner's observation store. No proof runs here. The on-demand consumers share
`behavior_report` and `behavior_proofs` below.
check and stub report changed units against --base (HEAD by default); --rev reads a committed
revision with its parent as the default base. Unchanged units on an edited line are omitted.
Explicit `list` and the runner's `behavior_report` retain whole-file coverage.

Exit 0 pass, 1 a failure, 2 no `instruction_scope` (reported as uncovered, never as zero).
"""
from __future__ import annotations

import argparse
import ast
import bisect
import importlib.util
import io
import tokenize
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adaptation  # noqa: E402
from triage import glob_regex  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from _hook_state import append_jsonl_rotating  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from _proof_runner import catalog as _catalog, evidence  # noqa: E402

_JS_CODE = re.compile(r"(?:[<>]=?|===?|!==?)\s*\d|[A-Z_]{3,}\s*[:=]\s*\d|\b\d+\s*\*\s*\d+")

# ---- units ---------------------------------------------------------------------------------------
_SPLIT = re.compile(r"(?<=[.;?!])\s+(?=[A-Z`*(\[])")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_LIST_START = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_HRULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LEAD = re.compile(r"^\s*(?:(?:>\s*)+|(?:[-*+]|\d+[.)])\s+)+")


@dataclass
class Unit:
    path: str
    line_start: int
    line_end: int
    heading: str
    raw: str
    norm: str
    hash: str
    kind: str


@dataclass
class Scope:
    paths: list
    rows_dir: str | None
    invalid: list = field(default_factory=list)


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = t.replace("`", "").replace("*", "")
    t = _LEAD.sub("", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def sentence_hash(normed: str) -> str:
    return hashlib.sha256(normed.encode("utf-8")).hexdigest()[:16]


def _unit(path, ls, le, heading, raw, kind):
    raw = re.sub(r"\s*\n\s*", " ", raw.strip())
    n = norm(raw)
    return Unit(path, ls, le, heading, raw, n, sentence_hash(n), kind)


class _Parsed:
    def __init__(self, path, text, behavior=False):
        self.path = path
        self.behavior = behavior
        self.units: list[Unit] = []
        self.lines = text.splitlines()
        (self._parse_js if path.endswith(".js") else self._parse_md)()

    def _parse_js(self):
        for i, raw in enumerate(self.lines):
            s = raw.strip()
            if s.startswith(("//", "*", "/*")) or _JS_CODE.search(s):
                self.units.append(_unit(self.path, i + 1, i + 1, "", raw, "js"))

    def _parse_md(self):
        lines = self.lines
        i, n = 0, len(lines)
        if n and lines[0].strip() == "---":
            j = 1
            while j < n and lines[j].strip() != "---":
                j += 1
            if self.behavior and j < n:
                self._literal(list(range(j + 1)), "frontmatter", "__frontmatter__")
            i = j + 1 if j < n else 0
        heading, fence, cur = "", False, []

        def flush():
            nonlocal cur
            if cur:
                self._flush_md(cur, heading)
                cur = []

        while i < n:
            raw = lines[i]
            s = raw.strip()
            if s.startswith("```") or (self.behavior and s.startswith("~~~")):
                flush()
                fence = not fence
                if self.behavior:
                    self._literal([i], "fence", heading)
            elif fence:
                if self.behavior and s:
                    self._literal([i], "fenced", heading)
            elif not s:
                flush()
            elif _HEADING.match(raw):
                flush()
                heading = s.lstrip("#").strip()
                if self.behavior:
                    self._literal([i], "heading", heading)
            elif _HRULE.match(raw):
                flush()
            elif s.startswith("|"):
                flush()
                if not _TABLE_SEP.match(raw):
                    self.units.append(_unit(self.path, i + 1, i + 1, heading, raw, "table"))
            elif _LIST_START.match(raw):
                flush()
                cur = [i]
            else:
                cur.append(i)
            i += 1
        flush()

    def _literal(self, idxs, kind, heading):
        joined = "\n".join(self.lines[i] for i in idxs)
        self.units.append(_unit(self.path, idxs[0] + 1, idxs[-1] + 1, heading, joined, kind))

    def _flush_md(self, idxs, heading):
        lines = [self.lines[i] for i in idxs]
        offs, pos = [], 0
        for l in lines:
            offs.append(pos)
            pos += len(l) + 1
        joined = "\n".join(lines)
        cuts = [(m.start(), m.end()) for m in _SPLIT.finditer(joined)]
        starts = [0] + [c[1] for c in cuts]
        ends = [c[0] for c in cuts] + [len(joined)]
        for st, en_ in zip(starts, ends):
            seg = joined[st:en_]
            if not seg.strip():
                continue
            st += len(seg) - len(seg.lstrip())
            ls = idxs[bisect.bisect_right(offs, st) - 1] + 1
            le = idxs[bisect.bisect_right(offs, max(st, en_ - 1)) - 1] + 1
            self.units.append(_unit(self.path, ls, le, heading, joined[st:en_], "prose"))


_PARSE_CACHE: dict = {}


def _parsed(path, text):
    key = (path, hash(text), len(text))
    p = _PARSE_CACHE.get(key)
    if p is None:
        if len(_PARSE_CACHE) > 64:
            _PARSE_CACHE.clear()
        p = _PARSE_CACHE[key] = _Parsed(path, text)
    return p


def units(path: str, text: str) -> list:
    """Sentence and table-row units of `text`; headings, frontmatter, fences and rules make none."""
    return list(_parsed(path, text).units)


# ---- scope and git -------------------------------------------------------------------------------
def load_scope(claude_dir) -> Scope:
    raw = adaptation.get(claude_dir, "instruction_scope")
    paths = raw.get("paths") if isinstance(raw, dict) else None
    rows_dir = raw.get("rows_dir") if isinstance(raw, dict) else None
    invalid = []
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        invalid.append("instruction_scope.paths is empty or not a list of globs")
        paths = []
    if not isinstance(rows_dir, str) or not rows_dir:
        invalid.append("instruction_scope.rows_dir is missing")
        rows_dir = None
    return Scope(paths, rows_dir, invalid)


_glob_re = functools.lru_cache(maxsize=None)(glob_regex)


def _matches(globs, path):
    return any(_glob_re(g).match(path) for g in globs)


def in_scope(scope: Scope, path: str) -> bool:
    return _matches(scope.paths, path)


def narrow(paths, path):
    for p in paths:
        p = p.replace("\\", "/")
        p = p[2:] if p.startswith("./") else p
        if path == p or path.startswith(p.rstrip("/") + "/") or _glob_re(p).match(path):
            return True
    return False


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout.decode("utf-8", "replace")


def changed_lines(repo, base, scope, paths=None, staged=False, rev=None) -> dict:
    """{path: set(line)}: the added side of `git diff -U0 <base>` (`--cached` when staged) for in-scope
    files, plus every line of an untracked in-scope file unless staged. `paths` narrows both."""
    def keep(p):
        return in_scope(scope, p) and (not paths or narrow(paths, p))

    args = ["diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    diff = git(repo, *args, base, *([rev] if rev else []))
    out, cur = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[6:].split("\t")[0] if line.startswith("+++ b/") else None
            if cur is not None and not keep(cur):
                cur = None
            if cur is not None:
                out.setdefault(cur, set())
        elif line.startswith("@@") and cur is not None:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
                out[cur].update(range(start, start + count))
    if not staged and not rev:
        for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if p and keep(p):
                try:
                    n = len(Path(repo, p).read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeDecodeError):
                    out[p] = {1}
                    continue
                out[p] = set(range(1, n + 1))
    return {p: ls for p, ls in out.items() if ls}


# ---- rows ----------------------------------------------------------------------------------------
_HASH = re.compile(r"[0-9a-f]{16}\Z")


def read_jsonl(path):
    rows, bad = [], 0
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return rows, bad
    with fh:
        for l in fh:
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
    return rows, bad

# ---- behavior claims: classification rows and typed implementation/proof bindings -----------------
BEHAVIOR_CLASSES = ("behavior", "procedure", "nonclaim")
OBSERVATION_TYPES = ("check-label", "unittest-case", "case-result")
BEHAVIOR_STATUSES = ("pass", "unbound", "fail", "uncovered", "stale")
_ADAPTATION_FILES = [path.as_posix() for path in adaptation.adaptation_files(".claude")]  # repo-relative, read order
_RUNNER = None


def behavior_units(path: str, text: str) -> list:
    """Conservative classification candidates; numeric/restatement units stay unchanged.

    Python retains AST string/f-string and comment source lines (overlapping spans coalesce).
    Native scripts retain every nonblank source line for explicit classification, not a partial
    lexer pretending to establish absence. Unsupported or unparseable input is uncovered.
    """
    suffix = Path(path).suffix.lower()
    if suffix in (".md", ".markdown"):
        return list(_Parsed(path, text, behavior=True).units)
    lines = text.splitlines()
    if suffix == ".py":
        try:
            tree = ast.parse(text)
            spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
                     if isinstance(n, ast.JoinedStr) or isinstance(n, ast.Constant) and isinstance(n.value, str)]
            spans += [(t.start[0], t.end[0]) for t in tokenize.generate_tokens(io.StringIO(text).readline)
                      if t.type == tokenize.COMMENT]
        except (SyntaxError, tokenize.TokenError, IndentationError) as exc:
            raise ValueError("unsupported extraction: %s" % exc) from exc
        merged = []
        for start, end in sorted(set(spans)):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return [_unit(path, start, end, "__module__", "\n".join(lines[start - 1:end]), "source")
                for start, end in merged]
    if suffix in (".sh", ".bash", ".ps1", ".js", ".mjs", ".cjs"):
        return [_unit(path, i, i, "__module__", line, "source-candidate")
                for i, line in enumerate(lines, 1) if line.strip()]
    raise ValueError("unsupported extraction for %s" % path)


def _relative(path):
    return (isinstance(path, str) and bool(path) and "\\" not in path
            and not Path(path).is_absolute() and ":" not in path
            and all(p not in ("", ".", "..") for p in path.split("/")))


def _behavior_bytes(repo, path, staged=False):
    if not _relative(path):
        return None
    if staged:
        prefix = staged + ":./" if isinstance(staged, str) else ":./"
        r = subprocess.run(["git", "-C", str(repo), "show", prefix + path], capture_output=True)
        return r.stdout if r.returncode == 0 else None
    try:
        return Path(repo, path).read_bytes()
    except OSError:
        return None


def _behavior_text(repo, path, staged=False):
    data = _behavior_bytes(repo, path, staged)
    try:
        return data.decode("utf-8") if data is not None else None
    except UnicodeDecodeError:
        return None


def _behavior_scope(repo, staged=False, rows_dir=None):
    """The `instruction_scope` the adaptation files merge to (`adaptation.merge`, as `load` merges them), read at
    `staged` (False: the working tree)."""
    texts = [(rel, _behavior_text(repo, rel, staged)) for rel in _ADAPTATION_FILES]
    raw = adaptation.merge(adaptation.parse_values(rel, text) for rel, text in texts if text is not None)
    raw = raw.get("instruction_scope")
    paths = raw.get("behavior_paths", raw.get("paths")) if isinstance(raw, dict) else None
    directory = rows_dir or (raw.get("rows_dir") if isinstance(raw, dict) else None)
    why = []
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        why.append("instruction_scope.behavior_paths (or paths) is missing or invalid")
        paths = []
    if not _relative(directory):
        why.append("instruction_scope.rows_dir is missing or not repo-relative")
        directory = None
    return Scope(paths, directory, why)


def validate_behavior_row(row, existing_keys=(), existing_ids=()):
    """Validate metadata, not the truth of a classification or an executed proof result."""
    if not isinstance(row, dict):
        return ["row is not an object"]
    why = []
    for key in ("path", "sentence", "hash", "section", "source", "classificationQuote"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            why.append("missing %s" % key)
    if why:
        return why
    if not _relative(row["path"]):
        why.append("path must be repo-relative")
    if not _HASH.fullmatch(row["hash"]) or sentence_hash(norm(row["sentence"])) != row["hash"]:
        why.append("hash is not the sentence hash of the sentence field")
    if (row["path"], row["hash"]) in existing_keys:
        why.append("duplicate (path, hash)")
    if row.get("class") not in BEHAVIOR_CLASSES:
        why.append("class must be behavior|procedure|nonclaim")
    if row.get("class") != "behavior":
        if row.get("proofs") or row.get("claimId") or row.get("implementation"):
            why.append("procedure/nonclaim cannot carry behavior bindings")
        return why
    claim = row.get("claimId")
    if not isinstance(claim, str) or not claim.strip():
        why.append("behavior requires claimId")
    elif claim in existing_ids:
        why.append("duplicate claimId")
    for key in ("implementation", "proofs"):
        if not isinstance(row.get(key), list):
            why.append("%s must be a list" % key)
    for impl in row.get("implementation", []) if isinstance(row.get("implementation"), list) else []:
        if (not isinstance(impl, dict) or not _relative(impl.get("path"))
                or not isinstance(impl.get("quote"), str) or not impl["quote"].strip()
                or not any(isinstance(impl.get(k), str) and impl[k].strip() for k in ("symbol", "anchor"))):
            why.append("implementation needs path, symbol/anchor and deciding quote")
        if isinstance(impl, dict) and "external" in impl:
            external = impl["external"]
            if (not isinstance(external, dict)
                    or not all(isinstance(external.get(k), str) and external[k].strip()
                               for k in ("version", "sourceIdentity"))):
                why.append("external implementation needs verified version and sourceIdentity")
    lesson = row.get("lesson")
    if lesson is not None and (not isinstance(lesson, dict)
                              or not all(isinstance(lesson.get(k), str) and lesson[k].strip()
                                         for k in ("lessonId", "failureKey", "landingCommit"))):
        why.append("lesson needs lessonId, failureKey and landingCommit")
    for proof in row.get("proofs", []) if isinstance(row.get("proofs"), list) else []:
        if not isinstance(proof, dict):
            why.append("proof must be an object")
            continue
        selector = proof.get("selector")
        if not _relative(proof.get("path")):
            why.append("proof path must be repo-relative, not a command")
        if not isinstance(proof.get("caseId"), str) or not proof["caseId"].strip():
            why.append("proof needs named caseId")
        if not isinstance(proof.get("assertion"), str) or not proof["assertion"].strip():
            why.append("proof needs assertion/fixture quote")
        if (not isinstance(selector, dict) or selector.get("type") not in OBSERVATION_TYPES
                or set(selector) - {"type"}):
            why.append("selector must name check-label|unittest-case|case-result, never a regex or command")
    return why


def _behavior_rows(repo, scope, staged=False):
    path = scope.rows_dir + "/behaviors.jsonl"
    text = _behavior_text(repo, path, staged)
    errors, rows, keys, ids = [], [], set(), set()
    if text is None:
        if _behavior_bytes(repo, path, staged) is not None or (not staged and Path(repo, path).exists()):
            errors.append({"path": path, "reason": "behavior rows unreadable", "status": "uncovered"})
        return rows, errors
    for line, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            row = None
        why = validate_behavior_row(row, keys, ids)
        if why:
            errors.append({"path": path, "line": line, "reason": "; ".join(why)})
            continue
        rows.append(row)
        keys.add((row["path"], row["hash"]))
        if row.get("claimId"):
            ids.add(row["claimId"])
    return rows, errors


def _proof_runner():
    global _RUNNER
    if _RUNNER is None:
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        spec = importlib.util.spec_from_file_location("_instruction_proof_runner", scripts / "harness_tests.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        sys.path.insert(0, str(scripts))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(scripts))
        _RUNNER = module
    return _RUNNER


def _case_exists(proof, text):
    """Resolve named assertions without inventing an observation from source presence."""
    if proof["assertion"] not in text:
        return False
    if proof["selector"]["type"] != "unittest-case":
        # check-label/structured descriptors require actual runner observations to establish execution.
        return proof["caseId"] in text
    if not proof["path"].endswith(".py"):
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    parts = proof["caseId"].split(".")
    if len(parts) < 2:
        return False
    klass, method = parts[-2:]
    return any(isinstance(n, ast.ClassDef) and n.name == klass
               and any(isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == method
                       and proof["assertion"] in (ast.get_source_segment(text, f) or "") for f in n.body)
               for n in ast.walk(tree))


def _reach_sources(repo, staged):
    """The static reach a behavior proof's input key uses: the index, worktree or revision view, with the
    declared edges and corpus of that view's policy (the runner's own tables when no revision is named)."""
    view = _catalog.View.revision(str(repo), staged) if isinstance(staged, str) else _catalog.View.worktree(str(repo))
    if staged:
        view.text = lambda rel: _behavior_text(repo, rel, staged) or ""
    if isinstance(staged, str):
        policy = _catalog.Policy.read(view)
    else:
        runner = _proof_runner()
        policy = _catalog.Policy(runner.EXCLUDED, runner.HEAVY, runner.SERIAL, runner.PROOF_BINDINGS,
                                 runner.PROOF_CORPUS)
    return evidence.Reach(view, policy.declared, policy.corpus)


def _behavior_binding_state(repo, rows, staged=False, proof_paths=None):
    """Use the runner's admitted proof family and reached inputs; never execute a proof here."""
    if not any(r["class"] == "behavior" and any(proof_paths is None or p["path"] in proof_paths
                                                for p in r["proofs"]) for r in rows):
        return {}, {}, []
    runner = _proof_runner()
    rev = staged if isinstance(staged, str) else None
    tracked = set(git(repo, "ls-tree", "-r", "--name-only", "-z", rev).split("\0")) if rev else set(
        git(repo, "ls-files", "-z").split("\0"))
    inventory = [str(repo / p) for p in tracked if p] if rev else None
    admitted = {Path(p).relative_to(repo).as_posix() for p in runner.discover(
        str(repo / ".claude/tests"), paths=inventory)}
    reach = _reach_sources(repo, staged)
    groups, unresolved = {}, []
    for row in rows:
        if row["class"] != "behavior":
            continue
        for proof in row["proofs"]:
            rel = proof["path"]
            if proof_paths is not None and rel not in proof_paths:
                continue
            if rel not in admitted or rel not in tracked:
                unresolved.append({"claimId": row["claimId"], "proof": rel,
                                   "reason": "proof is not a tracked member of the runner's admitted proof family"})
                continue
            text = _behavior_text(repo, rel, staged)
            if text is None or not _case_exists(proof, text):
                unresolved.append({"claimId": row["claimId"], "proof": rel, "caseId": proof["caseId"],
                                   "reason": "proof/case/assertion absent or unreadable"})
                continue
            groups.setdefault(rel, []).append((row, proof))
    cases, dependencies = {}, {}
    for rel, bound in sorted(groups.items()):
        subset = sorted({json.dumps(row, sort_keys=True, ensure_ascii=False) for row, _ in bound})
        reached = reach.reach(str(repo / rel))
        reached |= {impl["path"] for row, _ in bound for impl in row["implementation"]}
        # Entire config bytes and genuinely reached row files remain inputs. Merely registering a
        # dependency does not make the shared row file an input: fingerprint only this proof's subset.
        reached.update(_ADAPTATION_FILES)
        reached |= {row["path"] for row, _ in bound}
        inputs = []
        for path in sorted(reached):
            data = _behavior_bytes(repo, path, staged)
            inputs.append([path, hashlib.sha256(data).hexdigest() if data is not None else None, path in tracked])
        unit_inputs = sorted({(row["path"], row["hash"]) for row, _ in bound})
        runner_hash = hashlib.sha256(Path(runner.__file__).read_bytes()).hexdigest()
        key = hashlib.sha256(json.dumps([runner_hash, subset, unit_inputs, inputs],
                                        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        unique = {}
        for row, proof in bound:
            cid = proof["caseId"]
            descriptor = unique.setdefault(cid, {"caseId": cid, "selector": proof["selector"],
                                                "assertion": proof["assertion"], "inputKey": key, "claimIds": []})
            if descriptor["selector"] != proof["selector"] or descriptor["assertion"] != proof["assertion"]:
                unresolved.append({"claimId": row["claimId"], "proof": rel, "caseId": cid,
                                   "reason": "conflicting descriptors for shared case"})
            descriptor["claimIds"].append(row["claimId"])
        cases[rel] = [dict(d, claimIds=sorted(set(d["claimIds"]))) for _, d in sorted(unique.items())]
        dependencies[rel] = {"paths": sorted(reached | {p for p, _h in unit_inputs}),
                             "inputKey": key, "bindingHash": hashlib.sha256(json.dumps(subset).encode()).hexdigest()}
    return cases, dependencies, unresolved


def _binding_delta(repo, scope, rows, base):
    try:
        text = git(repo, "show", base + ":./" + scope.rows_dir + "/behaviors.jsonl")
    except RuntimeError:
        text = ""
    old = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            old.append(row)
    return [r for r in rows if r not in old] + [r for r in old if r not in rows]


def _candidate_proofs(repo, scope, rows, paths, base):
    binding_path = scope.rows_dir + "/behaviors.jsonl"
    if not paths or any(Path(p).suffix.lower() not in (".md", ".markdown") and p != binding_path for p in paths):
        return None  # Implementation-only changes need the runner's transitive reach relation.
    selected = [r for r in rows if any(narrow(paths, p) for p in _row_paths(r))]
    if narrow(paths, binding_path):
        selected += _binding_delta(repo, scope, rows, base)
    return {p["path"] for r in selected for p in r.get("proofs", [])}


def _dependent_rows(rows, dependencies, paths):
    ids = {cid for rel, dep in dependencies.items() if any(narrow(paths, p) for p in dep["paths"])
           for row in rows if row["class"] == "behavior" and any(p["path"] == rel for p in row["proofs"])
           for cid in [row["claimId"]]}
    return [r for r in rows if any(narrow(paths, p) for p in _row_paths(r)) or r.get("claimId") in ids]


def behavior_proofs(repo, paths=None, *, staged=False, base="HEAD"):
    """Sorted repo-relative admitted proofs, deduplicated case descriptors and unresolved dependencies.

    Each descriptor carries the inputKey required on the runner's case observation. Dependencies
    include source unit, implementation, proof, configuration and runner-reached input paths.
    The function is read-only; the existing runner owns execution, scheduling and pass storage.
    """
    repo = Path(repo).resolve()
    scope = _behavior_scope(repo, staged)
    if scope.invalid:
        return {"proofs": [], "cases": {}, "dependencies": {},
                "unresolved": [{"reason": why} for why in scope.invalid]}
    rows, errors = _behavior_rows(repo, scope, staged)
    try:
        candidate = _candidate_proofs(repo, scope, rows, paths, base)
        cases, dependencies, unresolved = _behavior_binding_state(repo, rows, staged, candidate)
    except (OSError, RuntimeError, ValueError) as exc:
        return {"proofs": [], "cases": {}, "dependencies": {}, "unresolved": errors + [{"reason": str(exc)}]}
    selected = {rel for rel, dep in dependencies.items()
                if not paths or any(narrow(paths, p) for p in dep["paths"])}
    dependent = _dependent_rows(rows, dependencies, paths) if paths else rows
    if paths and narrow(paths, scope.rows_dir + "/behaviors.jsonl"):
        delta = _binding_delta(repo, scope, rows, base)
        dependent += [r for r in rows if r in delta]
        selected |= {p["path"] for r in delta for p in r.get("proofs", []) if p["path"] in cases}
    relevant = {r["claimId"] for r in dependent if r["class"] == "behavior"}
    return {"proofs": sorted(selected), "cases": {p: cases[p] for p in sorted(selected)},
            "dependencies": {p: dependencies[p] for p in sorted(selected)},
            "unresolved": errors + [u for u in unresolved if u.get("claimId") in relevant]}


def _row_paths(row):
    return [row["path"]] + [r["path"] for k in ("implementation", "proofs") for r in row.get(k, [])]


def _observations(results):
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    if isinstance(results, dict):
        rows = results.get("observations", [])
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return []


def _stored_observations():
    """Every receipt the runner already recorded: its run log's rows and the pass store's rows, read through
    the evidence module's read-only receipt reader (no key, no migration, no write)."""
    runner = _proof_runner()
    rows = []
    try:
        doc = json.loads(Path(runner.runs_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    rows += _observations(doc)
    entries = doc.get("runs", []) if isinstance(doc, dict) and "runs" in doc else (
        doc if isinstance(doc, list) else list(doc.values()) if isinstance(doc, dict) else [])
    for entry in entries if isinstance(entries, list) else []:
        rows += _observations(entry)
    return rows + evidence.stored_receipts(runner.pass_store_dir())


def _outcome(statuses):
    return next((s for s in ("fail", "unbound", "stale", "uncovered") if s in statuses), "pass")


def behavior_report(repo, paths=None, base=None, staged=False, proof_results=None, *, rows_dir=None,
                    changed_only=False, rev=None):
    """Typed claim/classification outcomes for the actual index/worktree; advisory, never a deny.

    None receipts reads the existing runner run/pass files. A file-level pass without a current
    named-case observation is uncovered. Current failure dominates pass; skip/timeout never pass.
    All selected live units are counted by default for the runner and list consumers. changed_only
    reports changed units and affected bindings, not unchanged classification debt.
    """
    repo = Path(repo).resolve()
    source = rev or staged
    base = base or (rev + "^" if rev else "HEAD")
    report = {"status": "pass", "counts": {}, "units": [], "claims": [], "uncovered": [],
              "scope": {"paths": list(paths or []), "base": base,
                        "source": "revision" if rev else "index" if staged else "worktree",
                        "revision": None, "affected": [], "initialDebt": [], "changedUnits": [], "errors": []}}
    scope = _behavior_scope(repo, source, rows_dir)
    if scope.invalid:
        report["uncovered"] = [{"reason": why, "status": "uncovered"} for why in scope.invalid]
        report["scope"]["errors"] = scope.invalid
        return _finish_behavior_report(report)
    rows, errors = _behavior_rows(repo, scope, source)
    report["uncovered"] += [dict(e, status=e.get("status", "fail")) for e in errors]
    try:
        report["scope"]["revision"] = git(repo, "rev-parse", rev or "HEAD").strip()
        changed = changed_lines(repo, base, scope, staged=staged, rev=rev)
        diff_args = ["diff", "--relative", "--name-only", "--no-renames"] + (["--cached"] if staged else []) + [base] + ([rev] if rev else [])
        touched = set(git(repo, *diff_args).splitlines())
        names = set(git(repo, "ls-tree", "-r", "--name-only", "-z", rev).split("\0")) if rev else set(
            git(repo, "ls-files", "-co", "--exclude-standard", "-z").split("\0"))
        candidate = _candidate_proofs(repo, scope, rows, paths, base)
        cases, dependencies, unresolved = _behavior_binding_state(repo, rows, source, candidate)
        binding_path = scope.rows_dir + "/behaviors.jsonl"
        delta = _binding_delta(repo, scope, rows, base) if binding_path in touched else []
        delta_units = {(r.get("path"), r.get("hash")) for r in delta}
        if paths:
            docpaths = {p for p in names if p and in_scope(scope, p) and narrow(paths, p)}
            docpaths |= {p for p in paths if _relative(p) and in_scope(scope, p)
                         and not any(c in p for c in "*?[]") and Path(repo, p).suffix}
            dependent = _dependent_rows(rows, dependencies, paths)
            docpaths |= {r["path"] for r in dependent}
            if narrow(paths, binding_path):
                docpaths |= {r["path"] for r in delta}
        else:
            dependent = _dependent_rows(rows, dependencies, list(touched)) if touched else []
            docpaths = set(changed) | {r["path"] for r in dependent} | {r["path"] for r in delta}
        selected_rows = [r for r in rows if r["path"] in docpaths]
    except (RuntimeError, OSError, ValueError) as exc:
        report["uncovered"].append({"reason": str(exc), "status": "uncovered"})
        return _finish_behavior_report(report)
    by_key = {(r["path"], r["hash"]): r for r in selected_rows}
    live = set()
    observations = (_stored_observations() if cases else []) if proof_results is None else _observations(proof_results)
    affected = {r["claimId"] for r in _dependent_rows(rows, dependencies, list(touched))
                if r["class"] == "behavior"} if touched else set()
    for path in sorted(docpaths):
        text = _behavior_text(repo, path, source)
        try:
            if text is None:
                raise ValueError("file unreadable or missing")
            parsed = behavior_units(path, text)
        except ValueError as exc:
            report["uncovered"].append({"path": path, "reason": str(exc), "status": "uncovered"})
            continue
        try:
            previous = behavior_units(path, git(repo, "show", base + ":./" + path))
            previous_units = {(u.hash, u.heading or "__module__") for u in previous}
        except (RuntimeError, ValueError):
            previous_units = set()
        for unit in parsed:
            live.add((path, unit.hash))
            row = by_key.get((path, unit.hash))
            changed_unit = ((not changed_only and any(l in changed.get(path, ())
                                                      for l in range(unit.line_start, unit.line_end + 1)))
                            or (unit.hash, unit.heading or "__module__") not in previous_units
                            or (path, unit.hash) in delta_units)
            entry = {"path": path, "line": unit.line_start, "lineEnd": unit.line_end,
                     "sentence": unit.norm, "hash": unit.hash, "section": unit.heading or "__module__",
                     "kind": unit.kind, "class": row["class"] if row else None, "changed": changed_unit,
                     "status": "pass" if row else "uncovered"}
            report["units"].append(entry)
            if changed_unit:
                report["scope"]["changedUnits"].append({"path": path, "hash": unit.hash})
            if row is None:
                debt = dict(entry, reason="unclassified unit")
                report["uncovered"].append(debt)
                if not changed_unit:
                    report["scope"]["initialDebt"].append(debt)
            elif row["section"] != entry["section"]:
                entry["status"] = "stale"
                report["uncovered"].append(dict(entry, reason="owner section changed"))
            if row and row["class"] == "behavior" and (changed_unit or any(p in touched for p in _row_paths(row))):
                affected.add(row["claimId"])
    for row in selected_rows:
        if row["class"] != "behavior":
            if (row["path"], row["hash"]) not in live:
                report["uncovered"].append({"path": row["path"], "hash": row["hash"], "status": "stale",
                                            "reason": "orphan classification; reclassify or explicitly prune"})
            continue
        result = {"claimId": row["claimId"], "path": row["path"], "hash": row["hash"],
                  "status": "pass", "reasons": [], "cases": []}
        statuses = []
        if (row["path"], row["hash"]) not in live:
            statuses.append("stale")
            result["reasons"].append("orphan claim; explicit lineage/reclassification or deletion disposition required")
            affected.add(row["claimId"])
        elif any(u["path"] == row["path"] and u["hash"] == row["hash"] and u["status"] == "stale"
                 for u in report["units"]):
            statuses.append("stale")
            result["reasons"].append("owner section changed")
        if not row["proofs"]:
            statuses.append("unbound")
            result["reasons"].append("asserted behavior has no proof")
        if not row["implementation"]:
            statuses.append("uncovered")
            result["reasons"].append("implementation trace absent")
        for impl in row["implementation"]:
            external = impl.get("external")
            if external:
                expected = {"path": impl["path"], "version": external.get("version"),
                            "sourceIdentity": external.get("sourceIdentity")}
                current_probe = any(expected in obs.get("external", [])
                                    for proof in row["proofs"] for descriptor in cases.get(proof["path"], [])
                                    for obs in observations if obs.get("proof") == proof["path"]
                                    and obs.get("caseId") == descriptor["caseId"]
                                    and obs.get("inputKey") == descriptor["inputKey"]
                                    and obs.get("status") == "pass" and obs.get("evidence"))
                if proof_results is None or not current_probe:
                    statuses.append("uncovered")
                    result["reasons"].append("external runtime/version identity missing or incompatible; run project probe")
            text = _behavior_text(repo, impl["path"], source)
            if text is None:
                statuses.append("uncovered")
                result["reasons"].append("implementation unavailable: " + impl["path"])
            elif impl["quote"] not in text:
                statuses.append("stale")
                result["reasons"].append("deciding quote changed: " + impl["path"])
        for item in unresolved:
            if item.get("claimId") == row["claimId"]:
                statuses.append("uncovered")
                result["reasons"].append(item["reason"])
        for proof in row["proofs"]:
            descriptors = [d for d in cases.get(proof["path"], []) if d["caseId"] == proof["caseId"]]
            if not descriptors:
                continue
            descriptor = descriptors[0]
            matches = [r for r in observations if r.get("proof") == proof["path"]
                       and r.get("caseId") == proof["caseId"]]
            current = [r for r in matches if r.get("inputKey") == descriptor["inputKey"]]
            if any(r.get("status") == "fail" for r in current):
                status = "fail"
            elif any(r.get("status") != "pass" or not r.get("evidence") for r in current):
                status = "uncovered"
            elif current:
                status = "pass"
            else:
                status = "stale" if matches else "uncovered"
            statuses.append(status)
            result["cases"].append({"proof": proof["path"], "caseId": proof["caseId"], "status": status,
                                    "inputKey": descriptor["inputKey"], "evidence": current or matches})
        result["status"] = _outcome(statuses)
        report["claims"].append(result)
        if result["status"] != "pass" and row["claimId"] not in affected:
            report["scope"]["initialDebt"].append(result)
    # Row-only changes, including binding metadata, cannot be waved off as initial debt.
    if scope.rows_dir + "/behaviors.jsonl" in touched:
        try:
            old = git(repo, "show", base + ":./" + scope.rows_dir + "/behaviors.jsonl")
        except RuntimeError:
            old = ""
        old_rows = []
        for line in old.splitlines():
            try:
                old_rows.append(json.loads(line))
            except ValueError:
                pass
        affected |= {r["claimId"] for r in selected_rows if r["class"] == "behavior" and r not in old_rows}
    report["scope"]["affected"] = sorted(affected)
    report["scope"]["initialDebt"] = [d for d in report["scope"]["initialDebt"] if d.get("claimId") not in affected]
    if changed_only:
        changed_keys = {(u["path"], u["hash"]) for u in report["scope"]["changedUnits"]}
        affected_rows = [r for r in rows if r["class"] == "behavior"
                         and (any(p in touched for p in _row_paths(r) if p != r["path"])
                              or any(p in touched for proof in r["proofs"]
                                     for p in dependencies.get(proof["path"], {}).get("paths", [])
                                     if p != r["path"]))]
        affected = {r["claimId"] for r in affected_rows}
        affected |= {r["claimId"] for r in report["claims"]
                     if (r["path"], r["hash"]) in changed_keys
                     or (r["path"], r["hash"]) not in live}
        affected |= {r["claimId"] for r in delta if r.get("claimId")}
        report["scope"]["affected"] = sorted(affected)
        report["units"] = [u for u in report["units"] if u["changed"]]
        report["claims"] = [r for r in report["claims"] if r["claimId"] in affected]
        report["uncovered"] = [r for r in report["uncovered"] if not r.get("hash")
                               or (r.get("path"), r["hash"]) in changed_keys]
        report["scope"]["initialDebt"] = []
    report["scope"]["selectedProofs"] = sorted({c["proof"] for r in report["claims"] for c in r["cases"]})
    return _finish_behavior_report(report)


def _finish_behavior_report(report):
    claims, gaps = report["claims"], report["uncovered"]
    report["counts"] = {"units": len(report["units"]), "claims": len(claims), "gaps": len(gaps),
                        "changedUnits": len(report["scope"]["changedUnits"])}
    for status in BEHAVIOR_STATUSES:
        report["counts"][status] = sum(c["status"] == status for c in claims) + sum(g["status"] == status for g in gaps)
    report["status"] = _outcome([r["status"] for r in claims + gaps])
    return report


def cmd_behavior(args):
    repo = Path(args.repo).resolve()
    scope = _behavior_scope(repo, getattr(args, "rev", None) or getattr(args, "staged", False), args.rows_dir)
    if scope.invalid:
        report = behavior_report(repo, proof_results=[], rows_dir=args.rows_dir)
        print(json.dumps(report) if getattr(args, "json", False) else "; ".join(scope.invalid))
        return 2
    staged = getattr(args, "staged", False)
    rows, errors = _behavior_rows(repo, scope, staged)
    if args.cmd == "add":
        source, bad = read_jsonl(args.rows_file)
        if not Path(args.rows_file).is_file():
            print("rule_units: no such rows file: " + args.rows_file)
            return 2
        keys = {(r["path"], r["hash"]) for r in rows}
        ids = {r["claimId"] for r in rows if r.get("claimId")}
        problems = [str(e) for e in errors] + (["unparseable input rows"] if bad else [])
        for row in source:
            why = validate_behavior_row(row, keys, ids)
            problems += why
            if not why:
                keys.add((row["path"], row["hash"]))
                if row.get("claimId"):
                    ids.add(row["claimId"])
        if problems:
            print("rule_units: nothing added: " + "; ".join(problems))
            return 1
        directory = repo / scope.rows_dir
        directory.mkdir(parents=True, exist_ok=True)
        append_jsonl_rotating(str(directory / "behaviors.jsonl"), source, max_bytes=None)
        print("rule_units: added %d behavior rows" % len(source))
        return 0
    receipts = None
    if getattr(args, "proof_results", None):
        try:
            receipts = json.loads(Path(args.proof_results).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report = behavior_report(repo, paths=args.paths, base=getattr(args, "base", "HEAD"),
                                     staged=staged, proof_results=[], rows_dir=args.rows_dir,
                                     changed_only=args.cmd != "list", rev=getattr(args, "rev", None))
            report["uncovered"].append({"status": "uncovered", "reason": "proof results unavailable: " + str(exc)})
            print(json.dumps(_finish_behavior_report(report), ensure_ascii=False))
            return 2
    report = behavior_report(repo, paths=args.paths, base=getattr(args, "base", "HEAD"),
                             staged=staged, proof_results=receipts, rows_dir=args.rows_dir,
                             changed_only=args.cmd != "list", rev=getattr(args, "rev", None))
    if args.cmd == "stub":
        for unit in report["units"]:
            if unit["changed"] and unit["class"] is None:
                print(json.dumps({k: unit[k] for k in ("path", "sentence", "hash", "section")}
                                 | {"class": None, "source": "", "classificationQuote": ""}, ensure_ascii=False))
        return 0
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for row in report["claims"] + report["uncovered"]:
            print("%s %s: %s" % (row.get("claimId", row.get("path", "scope")), row["status"],
                                    row.get("reason", "; ".join(row.get("reasons", [])))))
        print("rule_units behavior: " + json.dumps(report["counts"]))
    return 0 if args.cmd == "list" else 1 if report["status"] != "pass" else 0


# ---- scope setup shared with tools/restate_scan.py ------------------------------------------------


def setup_scope(repo, rows_dir_arg, no_scope):
    """(scope, absolute rows_dir), or None after printing `no_scope` and the invalid keys."""
    scope = load_scope(repo / ".claude")
    if scope.invalid:
        print(no_scope + " [" + "; ".join(scope.invalid) + "]")
        return None
    rows_dir = rows_dir_arg or scope.rows_dir
    return scope, rows_dir if os.path.isabs(rows_dir) else str(repo / rows_dir)


def main(argv=None):
    default_repo = str(Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(prog="rule_units.py", description=__doc__.split("\n")[1])
    ap.add_argument("--repo", default=default_repo)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rows-dir", default=None, help="replaces the scope's rows_dir")
    diff = argparse.ArgumentParser(add_help=False)
    diff.add_argument("--base", default=None)
    source = diff.add_mutually_exclusive_group()
    source.add_argument("--staged", action="store_true")
    source.add_argument("--rev", help="check committed behavior changes (default base: its parent)")
    diff.add_argument("--paths", nargs="+", default=None)

    p = sub.add_parser("check", parents=[common, diff], help="typed outcome of each claim and unit")
    p.add_argument("--json", action="store_true")
    p.add_argument("--proof-results", help="runner JSON with typed case observations")
    p = sub.add_parser("stub", parents=[common, diff], help="print a row stub per unclassified changed unit")
    p = sub.add_parser("list", parents=[common], help="every unit of the named files with its status")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("add", parents=[common], help="validate and append rows")
    p.add_argument("rows_file")
    args = ap.parse_args(argv)
    if hasattr(args, "base") and args.base is None:
        args.base = args.rev + "^" if args.rev else "HEAD"
    return cmd_behavior(args)


if __name__ == "__main__":
    sys.exit(main())
