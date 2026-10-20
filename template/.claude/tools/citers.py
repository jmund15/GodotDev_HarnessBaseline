#!/usr/bin/env python3
"""List every inbound citer and normative duty of the surfaces a plan renames, rewrites or deletes.

Usage:
  python3 .claude/tools/citers.py (--plan <plan.md> | SURFACE...) [--after REV|WORKTREE] [--vault ROOT]
                                  [--repo DIR] [--json]

A SURFACE is one JSON object; `--plan` reads the same objects from the plan's fenced `surfaces` block, one
per line. `plan_file_format.md` §Surfaces touched owns the grammar. Output is the JSON object
{surfaces, citers, duties, coverage, subjects}, or a text listing without `--json`.

The subject plan named by --plan is the change, not an inbound caller. Its matching rows and generated
JSONL rows in the enumerated append-only logs and benchmark progress streams are excluded. Consumed records, test fixtures, other plans
and vault sources remain eligible. coverage.excludedRows counts distinct matching source rows by
reason (subjectPlan, generatedJsonl), once across surfaces and before the per-file citer cap.

Exit 0 swept; 1 a rewrite does not resolve at HEAD or a rename has neither source history nor destination;
2 unusable input. Deleted surfaces still scan their citers by path/token. Duties of deleted or renamed
sources use their last held revision when available; unavailable source history infers no duties.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import layer_closure  # noqa: E402  (_doc_candidates: how a cited token resolves to a path)
import vault_read  # noqa: E402  (the vault's dehydration-aware reader and its default root)
import sentence_diff  # noqa: E402  (a surface's duties)

KINDS = ("path", "section", "key", "symbol")
CHANGES = ("rename", "rewrite", "delete")
VAULT_ROOTS = ("DevProjects/{{PROJECT_NAME}}", "DevProjects/Jmodot", "DevProjects/GeneralGameDev")
SCOPE = ".claude"
TEXT_CLIP = 200
PER_FILE_CAP = 10
GRAMMAR = (
    'one JSON object per line inside a fenced ```surfaces block:\n'
    '  {"kind": "path", "surface": "<repo-relative path>", "change": "rename|rewrite|delete"}\n'
    '  {"kind": "section", "surface": "<repo-relative path> §<heading text or bold lead label>", ...}\n'
    '  {"kind": "key", "surface": "<literal token>", ...}\n'
    '  {"kind": "symbol", "surface": "<repo-relative path>::<name>", ...}\n'
    '  a "rename" also carries "to". See .claude/skills/_brainstorm_shared/plan_file_format.md §Surfaces touched.'
)


class Usage(Exception):
    """Unusable input: exit 2."""


class Unresolved(Exception):
    """A rewrite or rename whose permitted source/destination evidence does not resolve: exit 1."""


def git(repo, *args):
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    return run.returncode, run.stdout.decode("utf-8", "replace"), run.stderr.decode("utf-8", "replace")


def head_text(repo, rel):
    code, out, _ = git(repo, "show", f"HEAD:{rel}")
    return out if code == 0 else None


def parse_surface(raw):
    """One validated surface object from a JSON string or an already-parsed dict."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as err:
            raise Usage(f"surface is not JSON ({err}): {raw[:80]}")
    if not isinstance(raw, dict):
        raise Usage(f"surface is not a JSON object: {str(raw)[:80]}")
    kind, change, surface = raw.get("kind"), raw.get("change"), raw.get("surface")
    if kind not in KINDS:
        raise Usage(f"unknown kind {kind!r} (one of {', '.join(KINDS)}): {raw}")
    if change not in CHANGES:
        raise Usage(f"unknown change {change!r} (one of {', '.join(CHANGES)}): {raw}")
    if not isinstance(surface, str) or not surface.strip():
        raise Usage(f"surface names nothing: {raw}")
    if change == "rename" and not (isinstance(raw.get("to"), str) and raw["to"].strip()):
        raise Usage(f"a rename carries `to`: {raw}")
    return {k: raw[k] for k in ("kind", "surface", "change", "to") if k in raw}


def surface_file(surface):
    """The repo-relative file a surface lives in, or None for a bare key."""
    if surface["kind"] == "path":
        return surface["surface"]
    if surface["kind"] == "section":
        return surface["surface"].partition(" §")[0]
    if surface["kind"] == "symbol":
        return surface["surface"].partition("::")[0]
    return None


HEADING_RE = re.compile(r"^(#+)\s+(.*?)\s*$")
BOLD_LEAD_RE = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+\*\*(.+?)\*\*")
ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s|^\s*\*\*")
DEF_WORDS = r"(?:def|class|function|const|let|var|func|fn|struct|interface|enum|record)"
CSHARP_MODS = r"(?:(?:public|private|protected|internal|static|override|virtual|async|abstract|sealed|readonly|const)\s+)+"


def section_text(text, anchor):
    """The text of the section `anchor` names in `text`, or None. `anchor` equals a heading's text without
    its `#` marks, or the bold lead label of a bullet; a heading runs to the next heading of its level or
    higher, a bullet to the next item at its indent, a flush line after a blank, or a heading."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        heading = HEADING_RE.match(line)
        if heading and heading.group(2) == anchor:
            level = len(heading.group(1))
            end = next((i for i in range(start + 1, len(lines))
                        if (m := HEADING_RE.match(lines[i])) and len(m.group(1)) <= level), len(lines))
            return "\n".join(lines[start:end])
        bold = BOLD_LEAD_RE.match(line)
        if bold and bold.group(2) == anchor:
            indent = len(bold.group(1))
            end, gap = len(lines), False
            for i in range(start + 1, len(lines)):
                row = lines[i]
                if not row.strip():
                    gap = True
                    continue
                flush = len(row) - len(row.lstrip()) <= indent
                if HEADING_RE.match(row) or (flush and (gap or ITEM_RE.match(row))):
                    end = i
                    break
            return "\n".join(lines[start:end]).rstrip()
    return None


def split_section(surface):
    path, _, anchor = surface["surface"].partition(" §")
    return path, anchor.strip()


def defines(text, name):
    word = re.escape(name)
    return bool(re.search(rf"(?m)^\s*(?:{CSHARP_MODS})?(?:export\s+)?(?:async\s+)?{DEF_WORDS}\s+{word}\b"
                          rf"|^\s*{word}\s*(?::[^=\n]*)?=[^=]"
                          rf"|^\s*{CSHARP_MODS}[\w<>\[\],?.]+\s+{word}\b", text))


def _resolve_at(repo, surface, rev):
    rel = surface_file(surface)
    text = text_at(repo, rev, rel) if rel is not None else None
    if rel is not None and text is None:
        raise Unresolved(f"{surface['surface']}: {rel} does not exist at {rev}")
    if surface["kind"] == "section":
        anchor = split_section(surface)[1]
        if not anchor or section_text(text, anchor) is None:
            raise Unresolved(f"{surface['surface']}: no heading or bold lead label equals {anchor!r} in {rel} at {rev}")
    elif surface["kind"] == "symbol":
        name = surface["surface"].partition("::")[2]
        if not name or not defines(text, name):
            raise Unresolved(f"{surface['surface']}: {rel} does not define {name!r} at {rev}")
    elif surface["kind"] == "key":
        code, out, _ = git(repo, "grep", "-l", "-F", "-e", surface["surface"], rev, "--", SCOPE)
        if code != 0 or not out.strip():
            raise Unresolved(f"{surface['surface']}: no file under {SCOPE}/ holds this token at {rev}")


def _history_revisions(repo, surface):
    rel = surface_file(surface)
    selector = ['--no-renames', '--diff-filter=AM'] if rel else ['-S', surface['surface']]
    code, revs, _ = git(repo, 'log', '--format=%H', *selector, 'HEAD', '--', rel or SCOPE)
    return revs.splitlines() if code == 0 else []


def rename_destination(surface):
    target = surface['to']
    separator = {'section': ' §', 'symbol': '::'}.get(surface['kind'])
    if separator and separator not in target:
        target = surface_file(surface) + separator + target
    return dict(surface, surface=target, change='rewrite')


def resolve(repo, surface):
    """Resolve HEAD; landed renames may resolve their destination or historical source. Deletes need neither."""
    if surface['change'] == 'delete':
        return
    try:
        _resolve_at(repo, surface, 'HEAD')
        return
    except Unresolved as err:
        source_error = err
    if surface['change'] == 'rename':
        try:
            _resolve_at(repo, rename_destination(surface), 'HEAD')
            return
        except Unresolved:
            pass
        for rev in _history_revisions(repo, surface):
            try:
                _resolve_at(repo, surface, rev)
                return
            except Unresolved:
                continue
    raise source_error


def scan_files(repo):
    """Repo-relative files under SCOPE: tracked plus untracked-and-not-ignored."""
    code, out, err = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", SCOPE)
    if code:
        raise RuntimeError(f"git ls-files failed: {err.strip()}")
    return sorted(set(p for p in out.split("\0") if p))


def grep_lines(repo, needles):
    """[(path, line number, text)] of every SCOPE line holding any needle as a fixed string."""
    args = ["grep", "--untracked", "-n", "-I", "-F", "-z"]
    for needle in needles:
        args += ["-e", needle]
    code, out, err = git(repo, *args, "--", SCOPE)
    if code not in (0, 1):
        raise RuntimeError(f"git grep failed: {err.strip()}")
    hits = []
    for row in out.split("\n"):
        path, _, rest = row.partition("\0")
        number, _, text = rest.partition("\0")
        if path and number.isdigit():
            hits.append((path, int(number), text.rstrip("\r")))
    return hits


LINK_RE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
SPAN_RE = re.compile(r"`([^`\n]+)`")
EDGE = "\"'()[]<>,;:*|!?"


def token_matches(token, source_dir, target, base_unique):
    """True when `token`, cited from a file in `source_dir`, names the repo-relative `target`."""
    token = token.strip(EDGE).rstrip(".")
    if not token:
        return False
    cands, _ = layer_closure._doc_candidates(token, source_dir)
    for cand in cands or ():
        if cand.startswith("@base:"):
            if base_unique and cand[len("@base:"):] == posixpath.basename(target):
                return True
        elif cand.startswith("@suffix:"):
            if target.endswith("/" + cand[len("@suffix:"):]):
                return True
        elif cand == target:
            return True
    if "/" not in token:
        return False
    rel = token.lstrip("/")
    forms = {posixpath.normpath(rel), posixpath.normpath(f"{SCOPE}/{rel}"), posixpath.normpath(posixpath.join(source_dir, rel))}
    return target in forms or (not rel.startswith(".") and target.endswith("/" + posixpath.normpath(rel)))


def path_kinds(text, source_dir, target, base_unique):
    """The citer kinds one line of text holds toward `target`."""
    kinds = []
    for link in LINK_RE.findall(text):
        if not re.match(r"[a-z]+:", link):
            joined = link.lstrip("/") if link.startswith("/.claude/") else posixpath.normpath(posixpath.join(source_dir, link))
            if joined == target:
                kinds.append("link")
    rest = LINK_RE.sub(" ", text)
    for span in SPAN_RE.findall(rest):
        tokens = [span] if len(span.split()) == 1 else [w for w in span.split() if "/" in w or "." in w]
        if any(token_matches(t, source_dir, target, base_unique) for t in tokens):
            kinds.append("backtick-path")
            break
    rest = SPAN_RE.sub(" ", rest)
    if any(("/" in t or posixpath.basename(target) == t.strip(EDGE).rstrip("."))
           and token_matches(t, source_dir, target, base_unique) for t in rest.split()):
        kinds.append("bare-path")
    return kinds


def clip(text):
    text = " ".join(text.split())
    return text if len(text) <= TEXT_CLIP else text[:TEXT_CLIP - 1] + "…"


def bounded(key):
    """A regex for `key` as a whole token: a word or hyphen neighbour never extends it."""
    before = r"(?<![\w-])" if re.match(r"[\w-]", key[0]) else ""
    after = r"(?![\w-])" if re.match(r"[\w-]", key[-1]) else ""
    return re.compile(before + re.escape(key) + after)


class Matcher:
    """One surface's search: the fixed strings that find candidate lines, and `kinds`, which classes a candidate."""

    def __init__(self, surface, files):
        self.surface = surface
        self.target = surface_file(surface)
        self.module = None
        kind = surface["kind"]
        if kind == "key":
            self.needles = [surface["surface"]]
            self.pattern = bounded(surface["surface"])
        elif kind == "symbol":
            self.name = surface["surface"].partition("::")[2]
            self.needles = [self.name]
            self.pattern = bounded(self.name)
        else:
            target = self.target
            base = posixpath.basename(target)
            self.base_unique = sum(1 for f in set(files) | {target} if posixpath.basename(f) == base) == 1
            needles = {target, base}
            if target.startswith(SCOPE + "/"):
                needles.add(target[len(SCOPE) + 1:])
            if target.endswith(".py"):
                needles.add(base[:-3])
                self.module = re.escape(base[:-3])
            if kind == "section":
                anchor = split_section(surface)[1]
                label = anchor.rstrip(".")
                number = re.match(r"\d+[a-z]?(?:-\w+)?", anchor)
                self.forms = [re.compile(r"§\s*" + re.escape(label) + r"(?![\w])"),
                              re.compile(r"\*" + re.escape(label) + r"\*")]
                needles |= {"§" + label, f"*{label}*"}
                if number:
                    self.forms.append(re.compile(r"§\s*" + re.escape(number.group(0)) + r"(?![\w-])"))
                    needles.add("§" + number.group(0))
            self.needles = sorted(needles)

    def kinds(self, path, text, source_dir):
        kind = self.surface["kind"]
        if kind == "key":
            if not self.pattern.search(text):
                return []
            return ["fixture" if path.startswith(SCOPE + "/tests/") else "key"]
        if kind == "symbol":
            if not self.pattern.search(text) or (path == self.target and defines(text, self.name)):
                return []
            imported = re.match(rf"\s*from\s+\S+\s+import\b.*(?<![\w]){re.escape(self.name)}(?![\w])", text)
            return ["import" if imported else "symbol"]
        if kind == "section":
            if not any(form.search(text) for form in self.forms):
                return []
            same = path == self.target or path_kinds(text, source_dir, self.target, self.base_unique)
            return ["section-cite"] if same else []
        if path == self.target:
            return []
        kinds = path_kinds(text, source_dir, self.target, self.base_unique)
        if self.module and re.match(
                rf"\s*(?:from\s+{self.module}(?:\.\w+)*\s+import\b"
                rf"|import\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*{self.module}\b)", text):
            kinds.append("import")
        return kinds


def ignored_tests(repo):
    """[(path, text)] of the gitignored `.claude/tests` files present on disk: they cite surfaces too (K6)."""
    code, out, err = git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", f"{SCOPE}/tests")
    if code:
        raise RuntimeError(f"git ls-files failed: {err.strip()}")
    texts = []
    for rel in sorted(p for p in out.split("\0") if p):
        try:
            data = (Path(repo) / rel).read_bytes()
        except OSError:
            continue
        if b"\0" not in data[:8192]:
            texts.append((rel, data.decode("utf-8", "replace")))
    return texts


def vault_texts(vault):
    """([(path, text)], [per-root counts]) of the three Obsidian roots `CLAUDE.md` §3 names."""
    texts, counts = [], []
    for root in VAULT_ROOTS:
        found, count = vault_read.read_vault(vault, Path(root))
        texts += found
        counts.append(count)
    return texts, counts


def generated_jsonl(path):
    return path in {".claude/orchestration_metrics.jsonl", ".claude/autolearn_outcomes.jsonl",
                    ".claude/self_evaluate_archive.jsonl"} or (
        path.startswith(".claude/scripts/benchmark_campaign/") and path.endswith(".stream.jsonl"))


def jsonl_inventory(repo):
    code, out, err = git(repo, "ls-files", "-z", "-co", "--exclude-standard", "--",
                         ".claude/*.jsonl", ".claude/**/*.jsonl")
    if code:
        raise RuntimeError(f"JSONL inventory failed: {err.strip()}")
    paths = sorted(set(p for p in out.split("\0") if p))
    generated = sum(generated_jsonl(p) for p in paths)
    streams = sum(generated_jsonl(p) and p.endswith(".stream.jsonl") for p in paths)
    return {"method": "git ls-files -co --exclude-standard -- '.claude/*.jsonl' '.claude/**/*.jsonl'; deduplicate; classify enumerated writer/consumer log classes",
            "classes": {"appendOnlyLog": generated - streams, "benchmarkProgressStream": streams,
                        "eligible": len(paths) - generated}}


def find_citers(matcher, repo, ignored, vault, plan=None, excluded=None):
    found = []
    if excluded is None:
        excluded = {"subjectPlan": set(), "generatedJsonl": set()}

    def add(path, number, text, source_dir, repo_source):
        kinds = matcher.kinds(path, text, source_dir)
        if not kinds:
            return
        reason = None
        if repo_source and plan is not None and (Path(repo) / path).resolve() == plan:
            reason = "subjectPlan"
        elif repo_source and generated_jsonl(path):
            reason = "generatedJsonl"
        if reason:
            excluded[reason].add((path, number))
        else:
            found.extend((path, number, text, k) for k in kinds)

    for path, number, text in grep_lines(repo, matcher.needles):
        add(path, number, text, posixpath.dirname(path), True)
    for source_dir_of, docs, repo_source in ((posixpath.dirname, ignored, True), (lambda _p: "", vault, False)):
        for path, body in docs:
            for number, text in enumerate(body.splitlines(), 1):
                if any(n in text for n in matcher.needles):
                    add(path, number, text, source_dir_of(path), repo_source)
    return [{"surface": matcher.surface["surface"], "path": p, "line": n, "kind": k, "text": clip(t)}
            for p, n, t, k in found]


def cap_per_file(citers):
    """(citers with at most PER_FILE_CAP per file, {path: how many more that file held}): a ledger that
    repeats a key on every row would otherwise bury the doctrine files."""
    kept, seen, cut = [], {}, {}
    for citer in citers:
        seen[citer["path"]] = seen.get(citer["path"], 0) + 1
        if seen[citer["path"]] <= PER_FILE_CAP:
            kept.append(citer)
        else:
            cut[citer["path"]] = cut.get(citer["path"], 0) + 1
    return kept, cut


def text_at(repo, rev, rel):
    code, out, _ = git(repo, "show", f"{rev}:{rel}")
    return out if code == 0 else None


class Reader:
    """The text a duty is judged against after the change: the working tree, or a revision."""

    def __init__(self, repo, rev):
        self.repo, self.rev = Path(repo), rev
        if rev != "WORKTREE" and git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")[0]:
            raise Usage(f"--after {rev}: not WORKTREE and not a revision of {repo}")

    def read(self, rel):
        if self.rev == "WORKTREE":
            try:
                return (self.repo / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
        return text_at(self.repo, self.rev, rel)

    def corpus(self):
        """[(path, whitespace-normalized text)] of every doctrine markdown file after the change."""
        if self.rev == "WORKTREE":
            rels = [r for r in scan_files(self.repo) if sentence_diff.in_doctrine_scope(r)]
        else:
            _, out, _ = git(self.repo, "ls-tree", "-r", "--name-only", "-z", self.rev, "--", SCOPE)
            rels = [r for r in out.split("\0") if r and sentence_diff.in_doctrine_scope(r)]
        return [(r, " ".join(t.split())) for r, t in ((r, self.read(r)) for r in rels) if t]


def duty_scope(repo, surface):
    """(file, duties' scope at HEAD or last held source revision for a deletion/rename) for a markdown path or section,
    else (None, None): a key or symbol names no prose, and a code file's lines are not sentences."""
    rel = surface_file(surface)
    if surface["kind"] not in ("path", "section") or not rel.endswith(".md"):
        return None, None
    anchor = split_section(surface)[1] if surface["kind"] == "section" else None
    text = head_text(repo, rel)
    if text is not None and anchor is not None:
        text = section_text(text, anchor)
    if text is None and surface['change'] in ('delete', 'rename'):
        for rev in _history_revisions(repo, surface):
            text = text_at(repo, rev, rel)
            if text is not None and anchor is not None:
                text = section_text(text, anchor)
            if text is not None:
                break
    return rel, text


def after_scope(reader, surface):
    """The after-change text of the same scope: a rename reads its `to`; a missing file or section reads empty."""
    if surface['change'] == 'rename':
        surface = rename_destination(surface)
    rel = surface_file(surface)
    anchor = split_section(surface)[1] if surface["kind"] == "section" else None
    text = reader.read(rel) or ""
    return (section_text(text, anchor) or "") if anchor else text


def duties(repo, surface, reader=None, corpus=None):
    """[{surface, sentence[, class]}]: normative sentences in the resolved scope, classed against `reader` when given."""
    rel, before = duty_scope(repo, surface)
    if before is None:
        return []
    own = sentence_diff.sentences(before)
    if reader is None:
        return [{"surface": surface["surface"], "sentence": s} for s in own]
    after = after_scope(reader, surface)
    present = set(sentence_diff.sentences(after))
    gone = dict(sentence_diff.removed(before, after))
    homes = sentence_diff.homes([s for s, rewords in gone.items() if not rewords], corpus, rel)
    rows = []
    for sentence in own:
        if sentence in present:
            mark = "kept"
        elif gone.get(sentence):
            mark = "reworded"
        elif sentence in homes:
            mark = f"moved → {homes[sentence]}"
        else:
            mark = "removed"
        rows.append({"surface": surface["surface"], "sentence": sentence, "class": mark})
    return rows


def sweep(surfaces, repo=REPO, vault=None, after=None, plan=None):
    """The {surfaces, citers, duties, coverage, subjects} of surfaces, excluding `plan` as a caller when named.
    Raises Usage or Unresolved."""
    surfaces = [parse_surface(s) for s in surfaces]
    plan = Path(plan).resolve() if plan is not None else None
    excluded = {"subjectPlan": set(), "generatedJsonl": set()}
    reader = Reader(repo, after) if after else None
    for surface in surfaces:
        resolve(repo, surface)
    files = scan_files(repo)
    ignored = ignored_tests(repo)
    vault_docs, vault_counts = vault_texts(vault or vault_read.DEFAULT_VAULT)
    corpus = reader.corpus() if reader else None
    citers, truncated, found_duties = [], [], []
    for surface in surfaces:
        kept, cut = cap_per_file(find_citers(Matcher(surface, files), repo, ignored, vault_docs, plan, excluded))
        citers += kept
        truncated += [{"surface": surface["surface"], "path": path, "omitted": n} for path, n in cut.items()]
        found_duties += duties(repo, surface, reader, corpus)
    coverage = {"files": len(files), "ignoredTests": len(ignored), "truncated": truncated, "vault": vault_counts,
                "vaultVerified": all(vault_read.verified(c) for c in vault_counts),
                "jsonlInventory": jsonl_inventory(repo),
                "excludedRows": {reason: len(rows) for reason, rows in excluded.items()}}
    subjects = []
    if plan is not None:
        code, digest, err = git(repo, "hash-object", str(plan))
        if code:
            raise Usage(f"{plan}: cannot hash subject ({err.strip()})")
        subjects.append({"path": str(plan), "hash": digest.strip(), "head": git(repo, "rev-parse", "HEAD")[1].strip()})
    return {"surfaces": surfaces, "citers": citers, "duties": found_duties, "coverage": coverage, "subjects": subjects}


SURFACES_BLOCK = re.compile(r"^```surfaces[ \t]*\n(.*?)^```", re.S | re.M)


def plan_surfaces(plan):
    """The surface objects of a plan file's fenced `surfaces` block. Raises Usage when it has none."""
    try:
        text = Path(plan).read_text(encoding="utf-8")
    except OSError as err:
        raise Usage(f"{plan}: unreadable ({err})")
    block = SURFACES_BLOCK.search(text)
    if not block:
        raise Usage(f"{plan}: no ```surfaces block.\n{GRAMMAR}")
    rows = [line for line in block.group(1).splitlines() if line.strip()]
    if not rows:
        raise Usage(f"{plan}: its ```surfaces block is empty.\n{GRAMMAR}")
    return [parse_surface(row) for row in rows]


def render_text(result):
    lines = [f"{c['path']}:{c['line']} [{c['kind']}] {c['text']}" for c in result["citers"]]
    cov = result["coverage"]
    lines.append(f"coverage: {cov['files']} files; vault verified: {cov['vaultVerified']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("surfaces", nargs="*", metavar="SURFACE", help="one JSON surface object each")
    ap.add_argument("--plan", help="a plan file whose fenced `surfaces` block lists the surfaces")
    ap.add_argument("--after", help="REV or WORKTREE: classify each duty kept|reworded|moved|removed against it")
    ap.add_argument("--vault", default=None, help="the Obsidian vault root (default: vault_read.DEFAULT_VAULT)")
    ap.add_argument("--repo", default=str(REPO), help="the checkout to sweep")
    ap.add_argument("--json", action="store_true", help="print the JSON result (the default is a text listing)")
    args = ap.parse_args(argv)
    try:
        if bool(args.plan) == bool(args.surfaces):
            raise Usage("pass --plan <plan.md> or one or more SURFACE objects, not both and not neither")
        raw = plan_surfaces(args.plan) if args.plan else args.surfaces
        result = sweep(raw, repo=args.repo, vault=args.vault, after=args.after, plan=args.plan)
    except Usage as err:
        print(f"citers.py: {err}", file=sys.stderr)
        return 2
    except Unresolved as err:
        print(f"citers.py: unresolved at HEAD: {err}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
