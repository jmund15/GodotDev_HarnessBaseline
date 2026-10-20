#!/usr/bin/env python3
"""Rule-text number check: every number on an added or changed line of in-scope rule text needs a row.

A flagged token is a digit token (found per source line by the redesign's pass-1 rules) or a
word-form count such as `at least two`. It attaches to the sentence (or table row) that holds it.
`check` fails on any flagged token of a changed line that has no row keyed by
`(path, sentence hash, token)` in `<rows_dir>/numbers.jsonl`, and on a rule row whose `basisQuote`
is absent from the token's paragraph. The scope (`paths` globs) and the row directory come from
the `instruction_scope` key of the project adaptation file, read through `tools/adaptation.py`.
This module also owns the text units `tools/restate_scan.py` reads.

  extract_numbers.py [--repo DIR] check   [--base REV] [--staged] [--paths PATH...] [--json] [--rows-dir DIR]
  extract_numbers.py [--repo DIR] list    PATH... [--json] [--rows-dir DIR]
  extract_numbers.py [--repo DIR] stub    [--base REV] [--staged] [--paths PATH...] [--rows-dir DIR]
  extract_numbers.py [--repo DIR] add     ROWS.jsonl [--rows-dir DIR]

Default claims mode is numbers. `--claims behavior` on list/stub/add/check uses the same
unit hashes and rows directory, with `instruction_scope.behavior_paths` falling back to `paths`.
Behavior classifications and typed implementation/proof bindings live in `behaviors.jsonl`.
`check --claims behavior --proof-results <runner JSON> --json` checks named-case observations;
without that argument it reads the existing runner's observation store. No proof runs here.
The on-demand consumers share `behavior_report` and `behavior_proofs` below.
Behavior check/stub report changed units against --base (HEAD by default); --rev reads a committed
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

# ---- tokenizer: pass-1 digit rules and the pass-3 word-form pattern, unchanged -----------------
DROP = [r"§\s*\d+[a-z]?(?:\.\d+)*", r"\b\d{4}-\d{2}-\d{2}(?:T[\d:]+Z?)?\b", r"\bv?\d+(?:\.\d+){1,3}\b",
        r"\b[0-9a-f]{7,40}\b", r"\b[A-Z][A-Za-z]{0,3}-?\d+[a-z]?\b", r"#\d+", r":\d+(?:-\d+)?\b",
        r"\b(?:gpt|claude|opus|sonnet|haiku|fable|kimi|glm|grok|deepseek|qwen|mimo|hy|luna|sol|astra)[\w.-]*\d[\w.-]*",
        r"\b(?:Step|Phase|Part|Pass|Stage|Round|Tier|Slice|Appendix|Wave|Lane|Rule|Check|Gate|Lens|Level|Mode|Case|Item|Option|Row|Line|line)s?\s+\d+[a-z]?\b",
        r"\]\([^)]*\)", r"`[\w./-]*[/_.][\w./-]*`", r"\b[\w./-]*\d[\w./-]*\.(?:md|js|py|sh|json|jsonl|cs|tres|tscn|ps1|txt)\b",
        r"\b\d+(?:st|nd|rd|th)\b"]
DROP_RE = re.compile("|".join(DROP))
NUM = re.compile(r"(?<![\w.])(?:\d+(?:[.,]\d+)?\s*(?:%|K|M|KB|MB|k|s|ms|min|h|x|×)?)(?![\w])")
LIST = re.compile(r"^\s*(?:\d+[.)]|#+\s*\d+[.)]?)\s")
_W = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_W2 = r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
# A bare one or single is usually an article or adjective: 688 word hits the redesign pass never judged, against
# 99 it judged rule numbers. They count only inside a quantity frame; a bare once stays (59 judged, 44 not),
# except a once that opens a clause ("once the owner confirms"): a conjunction, judged non-rule in 8 of 8 rows.
CLAUSE_OPENERS = r"the|a|an|it|its|they|their|this|that|these|those|you|your|we|our|each|every|all|any|no|both|he|she"
_ONCE_CONJ = rf"(?!\s+(?:{CLAUSE_OPENERS})\b)"
PAT = re.compile(rf"\b(?:(?:at least|at most|more than|fewer than|up to|only|exactly)\s+(?:{_W}|once)(?:\s+[a-z]+)?|{_W2}\b(?:\s+[a-z]+)?|at most\s+an?(?:\s+[a-z]+)?|(?:second|third|fourth|fifth)\s+(?:round|pass|fold|seat|review)s?\b|twice|once{_ONCE_CONJ}|several\s+[a-z]+|a few\s+[a-z]+)", re.I)
_JS_CODE = re.compile(r"(?:[<>]=?|===?|!==?)\s*\d|[A-Z_]{3,}\s*[:=]\s*\d|\b\d+\s*\*\s*\d+")
_ZERO_ONE_CONTEXT = r"(?:[<>]=?|≥|≤|at least|more than|fewer than|over|under|exceeds?|above|below|max|min)\s*"

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
    para: int = -1


@dataclass
class Para:
    line_start: int
    line_end: int
    norm: str


@dataclass
class Scope:
    paths: list
    rows_dir: str | None
    invalid: list = field(default_factory=list)


@dataclass
class Flag:
    line: int
    token: str
    unit: Unit


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = t.replace("`", "").replace("*", "")
    t = _LEAD.sub("", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def sentence_hash(normed: str) -> str:
    return hashlib.sha256(normed.encode("utf-8")).hexdigest()[:16]


def _unit(path, ls, le, heading, raw, kind, para):
    raw = re.sub(r"\s*\n\s*", " ", raw.strip())
    n = norm(raw)
    return Unit(path, ls, le, heading, raw, n, sentence_hash(n), kind, para)


class _Parsed:
    def __init__(self, path, text, behavior=False):
        self.path = path
        self.behavior = behavior
        self.units: list[Unit] = []
        self.paras: list[Para] = []
        self._para_lines: dict[int, int] = {}    # line -> para index
        self._para_offsets: dict[int, list] = {}  # para -> line offsets in the joined text
        self._para_units: dict[int, list] = {}    # para -> [(start, end, unit)]
        self.lines = text.splitlines()
        self.js = path.endswith(".js")
        (self._parse_js if self.js else self._parse_md)()

    def _new_para(self, lines_idx):
        pi = len(self.paras)
        lines = [self.lines[i] for i in lines_idx]
        self.paras.append(Para(lines_idx[0] + 1, lines_idx[-1] + 1, norm(" ".join(lines))))
        offs, pos = [], 0
        for l in lines:
            offs.append(pos)
            pos += len(l) + 1
        self._para_offsets[pi] = offs
        for i in lines_idx:
            self._para_lines[i + 1] = pi
        return pi, "\n".join(lines), offs

    def _parse_js(self):
        cur = []
        for i, raw in enumerate(self.lines):
            s = raw.strip()
            keep = s.startswith(("//", "*", "/*")) or bool(_JS_CODE.search(s))
            if keep:
                cur.append(i)
            elif cur:
                self._flush_js(cur)
                cur = []
        if cur:
            self._flush_js(cur)

    def _flush_js(self, idxs):
        pi, _joined, _offs = self._new_para(idxs)
        spans = []
        for i in idxs:
            u = _unit(self.path, i + 1, i + 1, "", self.lines[i], "js", pi)
            self.units.append(u)
            spans.append((i, u))
        self._para_units[pi] = spans

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
                    pi, _j, _o = self._new_para([i])
                    u = _unit(self.path, i + 1, i + 1, heading, raw, "table", pi)
                    self.units.append(u)
                    self._para_units[pi] = [((0, len(raw)), u)]
            elif _LIST_START.match(raw):
                flush()
                cur = [i]
            else:
                cur.append(i)
            i += 1
        flush()

    def _literal(self, idxs, kind, heading):
        pi, joined, _offs = self._new_para(idxs)
        u = _unit(self.path, idxs[0] + 1, idxs[-1] + 1, heading, joined, kind, pi)
        self.units.append(u)
        self._para_units[pi] = [((0, len(joined)), u)]

    def _flush_md(self, idxs, heading):
        pi, joined, offs = self._new_para(idxs)
        cuts = [(m.start(), m.end()) for m in _SPLIT.finditer(joined)]
        starts = [0] + [c[1] for c in cuts]
        ends = [c[0] for c in cuts] + [len(joined)]
        spans = []
        for st, en_ in zip(starts, ends):
            seg = joined[st:en_]
            if not seg.strip():
                continue
            st += len(seg) - len(seg.lstrip())
            ls = idxs[bisect.bisect_right(offs, st) - 1] + 1
            le = idxs[bisect.bisect_right(offs, max(st, en_ - 1)) - 1] + 1
            u = _unit(self.path, ls, le, heading, joined[st:en_], "prose", pi)
            self.units.append(u)
            spans.append(((st, en_), u))
        self._para_units[pi] = spans

    def unit_at(self, line, col):
        """The unit holding `col` of `line`, or a one-line unit when no sentence holds the line."""
        pi = self._para_lines.get(line)
        if pi is not None:
            spans = self._para_units[pi]
            if self.js:
                for i, u in spans:
                    if i + 1 == line:
                        return u
            else:
                first = self.paras[pi].line_start
                off = self._para_offsets[pi][line - first] + col
                best = None
                for (st, en_), u in spans:
                    if st <= off:
                        best = u
                    if st <= off < en_:
                        return u
                if best is not None:
                    return best
        raw = self.lines[line - 1]
        return _unit(self.path, line, line, "", raw, "prose", -1)


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


def paragraph_norm(path: str, text: str, unit: Unit) -> str:
    p = _parsed(path, text)
    return p.paras[unit.para].norm if 0 <= unit.para < len(p.paras) else unit.norm


# ---- tokens --------------------------------------------------------------------------------------
def _sub_map(rx, repl, s, mapping):
    out, omap, last = [], [], 0
    for m in rx.finditer(s):
        out.append(s[last:m.start()])
        omap.extend(mapping[last:m.start()])
        out.append(repl)
        omap.append(mapping[m.start()])
        last = m.end()
    out.append(s[last:])
    omap.extend(mapping[last:])
    return "".join(out), omap


def _scan_lines(path, text):
    """Yield (line number, raw line) for lines a token scan reads, as the pass scripts read them."""
    js = path.endswith(".js")
    fence = False
    for i, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not js and s.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if js and not (s.startswith(("//", "*", "/*")) or _JS_CODE.search(s)):
            continue
        yield i, raw


def _digit_hits(raw):
    """[(token, column in raw)] for one line, with pass 1's DROP, NUM and LIST rules."""
    ident = list(range(len(raw)))
    t, m1 = _sub_map(LIST, " ", raw, ident + [len(raw)])
    t, m2 = _sub_map(DROP_RE, " ", t, m1)
    hits = []
    for m in NUM.finditer(t):
        tok = m.group(0).strip()
        if tok in ("0", "1") and not re.search(_ZERO_ONE_CONTEXT + re.escape(tok), t):
            continue
        hits.append((tok, m2[m.start()]))
    return hits


def digit_tokens(path: str, text: str) -> dict:
    out = {}
    for i, raw in _scan_lines(path, text):
        toks = [t for t, _c in _digit_hits(raw)]
        if toks:
            out[i] = toks
    return out


def word_tokens(path: str, text: str) -> dict:
    out = {}
    for i, raw in _scan_lines(path, text):
        toks = [m.group(0) for m in PAT.finditer(raw)]
        if toks:
            out[i] = toks
    return out


def flagged(path: str, text: str) -> list:
    """Every flagged token, in line order, attached to the unit that holds it."""
    parsed = _parsed(path, text)
    out = []
    for i, raw in _scan_lines(path, text):
        hits = _digit_hits(raw) + [(m.group(0), m.start()) for m in PAT.finditer(raw)]
        for tok, col in sorted(hits, key=lambda h: h[1]):
            out.append(Flag(i, tok, parsed.unit_at(i, col)))
    return out


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


def _scope_file_count(repo, scope):
    names = git(repo, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
    return sum(1 for p in set(names) if p and in_scope(scope, p))


def _read_text(repo, path, staged):
    if staged:
        try:
            return git(repo, "show", ":" + path)
        except RuntimeError:
            return None
    try:
        return Path(repo, path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---- rows ----------------------------------------------------------------------------------------
VERDICTS = ("confirm", "basis-found", "litmus", "delete")
CLASSES = ("identifier", "example", "structure")
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


class Rows:
    def __init__(self, rows_dir):
        self.dir = rows_dir
        self.rows, self.bad = read_jsonl(os.path.join(rows_dir, "numbers.jsonl"))
        self.unresolved, bad2 = read_jsonl(os.path.join(rows_dir, "numbers_unresolved.jsonl"))
        self.bad += bad2
        self.by_key = {(r.get("path"), r.get("hash"), r.get("token")): r for r in self.rows}


def validate_row(r, existing_keys):
    """[reason]: empty when the row is valid."""
    why = []
    if not isinstance(r, dict):
        return ["row is not an object"]
    for k in ("path", "hash", "token", "sentence", "kind", "source"):
        if not isinstance(r.get(k), str) or not r.get(k):
            why.append("missing %s" % k)
    if why:
        return why
    if not _HASH.match(r["hash"]):
        why.append("hash is not 16 hex characters")
    elif sentence_hash(norm(r["sentence"])) != r["hash"]:
        why.append("hash is not the sentence hash of the sentence field")
    if re.sub(r"\s+", " ", r["token"]).lower() not in norm(r["sentence"]):
        why.append("token does not occur in the sentence")
    if r["kind"] == "non-rule":
        if r.get("class") not in CLASSES:
            why.append("class must be one of identifier|example|structure")
        if r.get("verdict"):
            why.append("a non-rule row carries no verdict")
    elif r["kind"] == "rule":
        if r.get("verdict") not in VERDICTS:
            why.append("verdict must be one of confirm|basis-found|litmus|delete")
        q = r.get("basisQuote")
        if q is not None and not isinstance(q, str):
            why.append("basisQuote must be text or null")
        if r["source"].startswith("author:") and not (isinstance(q, str) and q.strip()):
            why.append("an author rule row must carry a basisQuote")
    else:
        why.append("kind must be rule or non-rule")
    if (r["path"], r["hash"], r["token"]) in existing_keys:
        why.append("a row for (path, hash, token) already exists")
    return why


# ---- behavior claims: the same units, scope, rows and changed-source selection ---------------------
BEHAVIOR_CLASSES = ("behavior", "procedure", "nonclaim")
OBSERVATION_TYPES = ("check-label", "unittest-case", "case-result")
BEHAVIOR_STATUSES = ("pass", "unbound", "fail", "uncovered", "stale")
_ADAPTATION = ".claude/skills/project_subsystems/adaptation.json"
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
        return [_unit(path, start, end, "__module__", "\n".join(lines[start - 1:end]), "source", -1)
                for start, end in merged]
    if suffix in (".sh", ".bash", ".ps1", ".js", ".mjs", ".cjs"):
        return [_unit(path, i, i, "__module__", line, "source-candidate", -1)
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
        prefix = staged + ":" if isinstance(staged, str) else ":"
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
    text = _behavior_text(repo, _ADAPTATION, staged)
    try:
        raw = json.loads(text) if text is not None else None
    except ValueError:
        raw = None
    raw = raw.get("instruction_scope") if isinstance(raw, dict) else None
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
    cache = runner._PassCache(str(repo), rev=rev)
    if staged:
        cache._text = lambda rel: _behavior_text(repo, rel, staged) or ""
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
        reached = cache.reach(str(repo / rel))
        reached |= {impl["path"] for row, _ in bound for impl in row["implementation"]}
        # Entire config bytes and genuinely reached row files remain inputs. Merely registering a
        # dependency does not make the shared row file an input: fingerprint only this proof's subset.
        reached.add(_ADAPTATION)
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
        text = git(repo, "show", base + ":" + scope.rows_dir + "/behaviors.jsonl")
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
    runner = _proof_runner()
    rows = []
    for path in (runner.runs_path(), runner.passes_path()):
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows += _observations(doc)
        entries = doc.get("runs", []) if isinstance(doc, dict) and "runs" in doc else (
            doc if isinstance(doc, list) else list(doc.values()) if isinstance(doc, dict) else [])
        for entry in entries if isinstance(entries, list) else []:
            rows += _observations(entry)
    return rows


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
        diff_args = ["diff", "--name-only", "--no-renames"] + (["--cached"] if staged else []) + [base] + ([rev] if rev else [])
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
            previous = behavior_units(path, git(repo, "show", base + ":" + path))
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
            old = git(repo, "show", base + ":" + scope.rows_dir + "/behaviors.jsonl")
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
            print("extract_numbers: no such rows file: " + args.rows_file)
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
            print("extract_numbers: nothing added: " + "; ".join(problems))
            return 1
        directory = repo / scope.rows_dir
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "behaviors.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in source))
        print("extract_numbers: added %d behavior rows" % len(source))
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
        print("extract_numbers behavior: " + json.dumps(report["counts"]))
    return 0 if args.cmd == "list" else 1 if report["status"] != "pass" else 0


# ---- commands ------------------------------------------------------------------------------------
NO_SCOPE = ("extract_numbers: no instruction_scope in the project adaptation file "
            "(paths and rows_dir are required); nothing was checked, so no number is covered")


def setup_scope(repo, rows_dir_arg, no_scope):
    """(scope, absolute rows_dir), or None after printing `no_scope` and the invalid keys."""
    scope = load_scope(repo / ".claude")
    if scope.invalid:
        print(no_scope + " [" + "; ".join(scope.invalid) + "]")
        return None
    rows_dir = rows_dir_arg or scope.rows_dir
    return scope, rows_dir if os.path.isabs(rows_dir) else str(repo / rows_dir)


def _setup(args):
    repo = Path(args.repo).resolve()
    got = setup_scope(repo, args.rows_dir, NO_SCOPE)
    if got is None:
        return None
    return repo, got[0], Rows(got[1])


def _orphans(rows, path, token, live_hashes):
    out = []
    for r in rows.rows:
        if (r.get("path") == path and r.get("token") == token and r.get("kind") == "rule"
                and r.get("hash") not in live_hashes and str(r.get("source", "")).startswith("seed")):
            out.append("seeded %s (sentence gone): %s" % (r.get("verdict"), r.get("note") or r.get("sentence")))
    for r in rows.unresolved:
        if r.get("path") == path and r.get("token") == token and r.get("verdict"):
            out.append("unresolved seed %s: %s" % (r.get("verdict"), r.get("reason") or r.get("note") or ""))
    return out


def _walk_changed(repo, scope, rows, args):
    changed = changed_lines(repo, args.base, scope, paths=args.paths, staged=args.staged)
    failures, n_flag, n_row, n_lines = [], 0, 0, 0
    pending_stub = []
    for path in sorted(changed):
        lines = changed[path]
        n_lines += len(lines)
        text = _read_text(repo, path, args.staged)
        if text is None:
            failures.append({"path": path, "line": 0, "token": "", "rule": "file unreadable", "orphans": []})
            continue
        parsed = _parsed(path, text)
        live = {u.hash for u in parsed.units}
        try:
            base_hashes = {u.hash for u in _parsed(path, git(repo, "show", "%s:%s" % (args.base, path))).units}
        except RuntimeError:
            base_hashes = set()
        for f in flagged(path, text):
            para = parsed.paras[f.unit.para] if 0 <= f.unit.para < len(parsed.paras) else None
            para_changed = para is not None and any(l in lines for l in range(para.line_start, para.line_end + 1))
            on_changed = f.line in lines and f.unit.hash not in base_hashes
            if not on_changed and not para_changed:
                continue
            row = rows.by_key.get((path, f.unit.hash, f.token))
            if on_changed:
                n_flag += 1
                if row is not None:
                    n_row += 1
            if on_changed and row is None:
                live.add(f.unit.hash)
                failures.append({"path": path, "line": f.line, "token": f.token,
                                 "rule": "no row for this sentence and token",
                                 "orphans": _orphans(rows, path, f.token, live)})
                pending_stub.append((path, f))
            elif row is not None and row.get("kind") == "rule" and row.get("basisQuote"):
                if norm(row["basisQuote"]) not in paragraph_norm(path, text, f.unit):
                    failures.append({"path": path, "line": f.line, "token": f.token,
                                     "rule": "basis sentence absent", "orphans": []})
    counts = {"scope_files": _scope_file_count(repo, scope), "changed_files": len(changed),
              "changed_lines": n_lines, "flagged_tokens": n_flag, "with_rows": n_row,
              "unparseable_rows": rows.bad}
    return failures, counts, pending_stub


def _recon(c):
    return ("extract_numbers: scope files %d, changed files %d, changed lines %d, flagged tokens %d, with rows %d, unparseable rows %d"
            % (c["scope_files"], c["changed_files"], c["changed_lines"], c["flagged_tokens"], c["with_rows"],
               c["unparseable_rows"]))


def cmd_check(args):
    ctx = _setup(args)
    if ctx is None:
        return 2
    repo, scope, rows = ctx
    try:
        failures, counts, _ = _walk_changed(repo, scope, rows, args)
    except RuntimeError as e:
        print("extract_numbers: " + str(e))
        return 2
    if args.json:
        print(json.dumps({"failures": failures, "counts": counts, "reconciliation": _recon(counts)}, ensure_ascii=False))
    else:
        for f in failures:
            print("%s:%d %s: %s" % (f["path"], f["line"], f["token"], f["rule"]))
            for o in f["orphans"][:3]:
                print("    " + o)
            if len(f["orphans"]) > 3:
                print("    (+%d more seeded verdicts for this path and token)" % (len(f["orphans"]) - 3))
        print(_recon(counts))
    return 1 if failures or rows.bad else 0


def cmd_stub(args):
    ctx = _setup(args)
    if ctx is None:
        return 2
    repo, scope, rows = ctx
    try:
        _f, counts, stubs = _walk_changed(repo, scope, rows, args)
    except RuntimeError as e:
        print("extract_numbers: " + str(e))
        return 2
    seen = set()
    for path, f in stubs:
        key = (path, f.unit.hash, f.token)
        if key in seen:
            continue
        seen.add(key)
        print(json.dumps({"path": path, "hash": f.unit.hash, "token": f.token, "sentence": f.unit.norm,
                          "kind": "", "verdict": None, "basisQuote": None, "class": None, "note": "",
                          "source": ""}, ensure_ascii=False))
    print(_recon(counts) + ", stubs %d" % len(seen), file=sys.stderr)
    return 0


def cmd_list(args):
    ctx = _setup(args)
    if ctx is None:
        return 2
    repo, _scope, rows = ctx
    unres = {(r.get("path"), r.get("token")) for r in rows.unresolved}
    out, unreadable = [], []
    for p in args.paths:
        p = p.replace("\\", "/")
        text = _read_text(repo, p, False)
        if text is None:
            unreadable.append(p)
            continue
        for f in flagged(p, text):
            row = rows.by_key.get((p, f.unit.hash, f.token))
            if row is not None:
                st, verdict = "row", row.get("verdict") or row.get("class")
            elif (p, f.token) in unres:
                st, verdict = "unresolved", None
            else:
                st, verdict = "none", None
            out.append({"path": p, "line": f.line, "token": f.token, "status": st, "detail": verdict})
    tally = {s: sum(1 for e in out if e["status"] == s) for s in ("row", "unresolved", "none")}
    summary = ("extract_numbers: files %d, unreadable %d, flagged tokens %d, row %d, unresolved %d, none %d"
               % (len(args.paths), len(unreadable), len(out), tally["row"], tally["unresolved"], tally["none"]))
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        print(summary, file=sys.stderr)
    else:
        for e in out:
            print("%s:%d %s: %s%s" % (e["path"], e["line"], e["token"], e["status"], " (%s)" % e["detail"] if e["detail"] else ""))
        for p in unreadable:
            print("%s: unreadable" % p)
        print(summary)
    return 0


def cmd_add(args):
    ctx = _setup(args)
    if ctx is None:
        return 2
    _repo, _scope, rows = ctx
    if not os.path.isfile(args.rows_file):
        print("extract_numbers: no such rows file: %s" % args.rows_file)
        return 2
    src, bad = read_jsonl(args.rows_file)
    if bad:
        print("extract_numbers: %d unparseable line(s) in %s; nothing added" % (bad, args.rows_file))
        return 1
    keys = set(rows.by_key)
    problems = []
    for n, r in enumerate(src, 1):
        why = validate_row(r, keys)
        if why:
            problems.append("row %d (%s %s): %s" % (n, r.get("path"), r.get("token"), "; ".join(why)))
        else:
            keys.add((r["path"], r["hash"], r["token"]))
    if problems:
        for p in problems:
            print(p)
        print("extract_numbers: %d of %d rows refused; nothing added" % (len(problems), len(src)))
        return 1
    os.makedirs(rows.dir, exist_ok=True)
    with open(os.path.join(rows.dir, "numbers.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
        for r in src:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("extract_numbers: added %d rows" % len(src))
    return 0


def main(argv=None):
    default_repo = str(Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(prog="extract_numbers.py", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=default_repo)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rows-dir", default=None, help="replaces the scope's rows_dir")
    common.add_argument("--claims", choices=("numbers", "behavior"), default="numbers")
    diff = argparse.ArgumentParser(add_help=False)
    diff.add_argument("--base", default=None)
    source = diff.add_mutually_exclusive_group()
    source.add_argument("--staged", action="store_true")
    source.add_argument("--rev", help="check committed behavior changes (default base: its parent)")
    diff.add_argument("--paths", nargs="+", default=None)

    p = sub.add_parser("check", parents=[common, diff], help="fail on a flagged number with no row")
    p.add_argument("--json", action="store_true")
    p.add_argument("--proof-results", help="runner JSON with typed case observations (behavior mode)")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("stub", parents=[common, diff], help="print a row stub per token without a row")
    p.set_defaults(fn=cmd_stub)
    p = sub.add_parser("list", parents=[common], help="every flagged token of the named files with its row status")
    p.add_argument("paths", nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("add", parents=[common], help="validate and append rows")
    p.add_argument("rows_file")
    p.set_defaults(fn=cmd_add)
    args = ap.parse_args(argv)
    if getattr(args, "rev", None) and args.claims != "behavior":
        ap.error("--rev is supported for behavior claims only")
    if hasattr(args, "base") and args.base is None:
        args.base = args.rev + "^" if args.rev else "HEAD"
    if args.claims == "behavior":
        return cmd_behavior(args)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
