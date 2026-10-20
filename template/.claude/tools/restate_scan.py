#!/usr/bin/env python3
"""Cross-file duplicate-rule scan: candidate groups of rule sentences that may state one decision twice.

Units are `tools/extract_numbers.py`'s sentences and table rows over the `instruction_scope` of the project
adaptation file. Pairs form only across files, through three channels:
  lexical   TF-IDF cosine of content tokens at or above LEXICAL_CUTOFF (units with two or more content tokens);
  anchor    a shared plural-folded content token whose unit frequency is at or below ANCHOR_CAP, with a
            normative marker (never, only, always, cannot, must, every, no, not) in both units;
  citation  a unit naming a section of an in-scope file (`name` §N, or a path then §N) pairs with each unit of that
            section that shares two content tokens with the citing unit outside its own reference text. A
            sentence that only points at a file, or whose overlap is the reference's own words, restates nothing.
A second stage filters the lexical channel: a pair needs a marker in both units, and both need two shared content
tokens whose unit frequency is below STOPLIST_DF (a more common token is harness vocabulary). The citation channel
counts the same tokens against the citing unit's own text. The anchor channel skips the stage: it already needs a
marker on both sides, and one rare shared token is how it finds a copy.
A group is one center unit with its direct partners, never a connected component, and each pair appears in one
group. `pairs` centers on every unit, `--changed` on changed units and `--text` on the file's units (both flags:
either kind); `--paths` narrows the centers to the files it names, so a pair touching none of them is never
formed. A unit is a `--changed` center when the change WROTE its sentence: the sentence is absent from that file
at `<base>`, read through `git cat-file --batch`. A sentence the paragraph still holds from `<base>` is not a
center: the change replaced the hunk it sits in, not this sentence. The test reads the text, so an
insertion that shifts the lines below it changes nothing. `--staged` (with `--changed`) takes the added lines
from `git diff --cached -U0 <base>` and reads every in-scope unit from the index the environment names,
`GIT_INDEX_FILE` included, through one `git cat-file --batch` call.

A group's `hash` is the unit's key, built from `path` and sentence hash, because a sentence hash alone does not name a
unit: two files can hold one sentence, and a consumer resolving a pair's endpoints by `hash` then names one
location twice and never the second home. The pair's `sha` carries the two sentence hashes, and a ruling is keyed
by the sorted pair of those, so a pair ruled `distinct` stays suppressed until one of its sentences changes. A pair ruled `mirror` is an
intended copy: it is suppressed the same way, and the ruling names the test or check that holds it equal (`--held-by`).

  restate_scan.py [--repo DIR] pairs [--changed [--staged] [--base REV]] [--text FILE] [--paths PATH ...] [--json] [--rows-dir DIR]
  restate_scan.py [--repo DIR] rule GROUP_ID distinct|mirror --reason TEXT [--held-by PATH, mirror only] [--changed [--staged] [--base REV]] [--text FILE] [--paths PATH ...] [--rows-dir DIR]
  restate_scan.py [--repo DIR] selftest

`selftest` adds two planted files to the live scope, one holding a rule and the other a near-copy of it, and
exits 0 only when the scan returns that pair as a group: an empty `pairs` result then means no candidates, never a
blind scan. Exit 1 when the planted copy is missed, 2 when the scan could not run.

Exit 0 done, 1 an unknown group id, 2 the scan could not run (no `instruction_scope`, `--staged` without
`--changed`, or unreadable staged content), which is reported as uncovered, never as zero candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_numbers as en  # noqa: E402

Thresholds = namedtuple("Thresholds", "lexical_cutoff anchor_cap citation_rule stoplist_df")
# Each rate is measured on the tracked labeled pairs (`fixtures/restate_scan/labeled_pairs.jsonl`); the
# self-test's calibration case reproduces them. The sampled `same` share per flagged pair falls when the lexical
# cutoff drops: 0.05 on the pairs a 0.25 cutoff adds, against 0.226 at this setting.
LEXICAL_CUTOFF = 0.30  # recall 0.436, same=no rate 0.097, after the stage-two filter
ANCHOR_CAP = 6  # recall 0.069, same=no rate 0.010; the smallest cap that still finds every planted contradiction
# Sampled `same` share by channel: citation 0.20 (6 of 30), lexical 0.27 (8 of 30), anchor 0.00 (0 of 30), mixed 0.40 (4 of 10).
CITATION_RULE = "named"  # recall 0.011, same=no rate 0.010; a pointer to a whole file is not a restatement
# A token whose unit frequency is at or above this is harness vocabulary (27 tokens over 19,472 units). A frequency
# cutoff, not a top-N size, so a small corpus keeps its own vocabulary. Union: recall 0.441, same=no rate 0.117;
# it flags 121 pairs on a plan file and 293 on a 278-unit change set, against 1,270 and 2,762 unfiltered.
STOPLIST_DF = 500
THRESHOLDS = Thresholds(LEXICAL_CUTOFF, ANCHOR_CAP, CITATION_RULE, STOPLIST_DF)
# `named`: a path mention or `name` that names a section (§N); `sections` also takes a path mention with no section;
# `files` adds a /command mention and a bare `name`.
CITATION_RULES = ("named", "sections", "files")
_REF_KINDS = {"named": {"path", "section"}, "sections": {"path", "section"},
              "files": {"path", "section", "slash", "name"}}

MARKERS = frozenset("never only always cannot must every no not".split())
STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between
both but by can could did do does doing done down during each either else etc even few for from further get gets
had has have having he her here hers him his how however i if in into is it its itself just let may me might
more most my neither nor of off on once one onto or other our ours out over own per same she should so some
such than that the their theirs them then there these they this those through thus to too under until up upon
us use used uses using very via was we were what when where whether which while who whom whose why will with
within without would yet you your yours e.g i.e eg ie
""".split()) | MARKERS
_WORD = re.compile(r"[a-z][a-z0-9]+")


def fold(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def content_tokens(normed: str) -> list:
    return [fold(w) for w in _WORD.findall(normed) if len(w) >= 3 and w not in STOP]


def has_marker(normed: str) -> bool:
    return any(w in MARKERS for w in re.findall(r"[a-z]+", normed))


# ---- sections and references ------------------------------------------------------------------------
_HEAD_LINE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _title(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace("`", "").replace("*", "")).strip().lower()


def section_chains(path: str, text: str) -> dict:
    """{line: [enclosing heading titles, outermost first]} for a markdown file; empty for other files."""
    if path.endswith(".js"):
        return {}
    out, stack, fence = {}, [], False
    for i, l in enumerate(text.splitlines(), 1):
        s = l.strip()
        if s.startswith("```"):
            fence = not fence
        m = None if fence else _HEAD_LINE.match(l)
        if m:
            lv = len(m.group(1))
            stack = [h for h in stack if h[0] < lv] + [(lv, _title(m.group(2)))]
        out[i] = [t for _lv, t in stack]
    return out


def _title_words(title: str) -> list:
    t = re.split(r"\s+[—–-]\s+|[:(]", title)[0]
    words = t.split()
    if words and re.fullmatch(r"\d+[a-z]?[.)]?", words[0]):
        words = words[1:]
    return [w.strip(".,") for w in words]


def section_match(sec: str, titles) -> bool:
    s = sec.lower().rstrip(".")
    if re.fullmatch(r"\d+[a-z]?(?:\.\d+)*", s):
        return any(t.split() and t.split()[0].rstrip(".:)") == s for t in titles)
    want = [w.strip(".,*") for w in s.split()]
    for t in titles:
        tw = _title_words(t)
        if tw and want[:len(tw)] == tw:
            return True
    return False


_PATH_REF = re.compile(r"([\w.-]+(?:/[\w.-]+)*\.(?:md|js|json))")
_SEC = r"\s*§\s*(\d+[a-z]?(?:\.\d+)*|[A-Z][\w-]*(?:\s+[\w-]+){0,5})"
_NAME_SEC = re.compile(r"`([a-z][\w-]*)`(?:\s+skill)?" + _SEC)
_SLASH = re.compile(r"(?<![\w/.`-])`?/([a-z][a-z0-9_]+)\b")
_NAME = re.compile(r"`([a-z][a-z0-9_-]+)`")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_SEC_TEXT = re.compile(r"§\s*(?:\d+[a-z]?(?:\.\d+)*|[A-Z][\w-]*(?:\s+[\w-]+){0,5})(?:\s*\*[^*]*\*)?")


class Resolver:
    """Resolves a file mention, a skill or command name, or a /command against the known in-scope paths."""

    def __init__(self, paths):
        self.paths = sorted(set(paths))
        self.by_suffix = {}
        self.by_name = {}
        for p in self.paths:
            parts = p.split("/")
            for k in range(1, len(parts) + 1):
                self.by_suffix.setdefault("/".join(parts[-k:]), set()).add(p)
            stem = parts[-1].rsplit(".", 1)[0]
            name = parts[-2] if stem.upper() == "SKILL" and len(parts) > 1 else stem
            self.by_name.setdefault(name, set()).add(p)
        self.commands = {p.split("/")[-1][:-3]: p for p in self.paths if "/commands/" in p and p.endswith(".md")}

    def path(self, mention):
        m = mention[2:] if mention.startswith("./") else mention
        hits = self.by_suffix.get(m, set())
        return next(iter(hits)) if len(hits) == 1 else None

    def name(self, n):
        hits = self.by_name.get(n, set())
        skill = [p for p in hits if p.endswith("/SKILL.md")]
        if len(skill) == 1:
            return skill[0]
        return next(iter(hits)) if len(hits) == 1 else None

    def refs(self, raw):
        """[(kind, path, section or None)]"""
        out = []
        for m in _PATH_REF.finditer(raw):
            p = self.path(m.group(1))
            if p:
                sm = re.match(_SEC, raw[m.end():].lstrip("`"))
                out.append(("path", p, sm.group(1) if sm else None))
        for m in _NAME_SEC.finditer(raw):
            p = self.name(m.group(1))
            if p:
                out.append(("section", p, m.group(2)))
        for m in _SLASH.finditer(raw):
            p = self.commands.get(m.group(1))
            if p:
                out.append(("slash", p, None))
        for m in _NAME.finditer(raw):
            p = self.name(m.group(1))
            if p and not re.match(r"(?:\s+skill)?\s*§", raw[m.end():]):
                out.append(("name", p, None))
        return out

    def strip(self, raw):
        """`raw` with each resolved reference (link, path, `name`, /command) and each § section blanked."""
        blank = lambda ok: (lambda m: " " if ok(m) else m.group(0))  # noqa: E731
        s = _MD_LINK.sub(" ", raw)
        s = _SEC_TEXT.sub(" ", s)
        s = _PATH_REF.sub(blank(lambda m: self.path(m.group(1))), s)
        s = _SLASH.sub(blank(lambda m: m.group(1) in self.commands), s)
        return _NAME.sub(blank(lambda m: self.name(m.group(1))), s)


@dataclass(eq=False)
class SUnit:
    path: str
    line: int
    line_end: int
    heading: str
    hash: str
    key: str
    raw: str
    norm: str
    kind: str
    sections: list
    toks: list
    marker: bool
    refs: list = field(default_factory=list)
    extra: bool = False
    own: frozenset = frozenset()

    @property
    def tokset(self):
        s = self.__dict__.get("_tokset")
        if s is None:
            s = self.__dict__["_tokset"] = frozenset(self.toks)
        return s

    @property
    def lexical_ok(self):
        return len(self.tokset) >= 2


def unit_key(path, h):
    """A unit's identity. Its sentence hash alone does not identify it: another file can hold that sentence."""
    return hashlib.sha256(("%s\n%s" % (path, h)).encode("utf-8")).hexdigest()[:16]


def make_unit(path, line, line_end, heading, raw, sections, resolver, kind="prose", h=None, extra=False):
    n = en.norm(raw)
    refs = resolver.refs(raw) if resolver else []
    toks = content_tokens(n)
    own = frozenset(content_tokens(en.norm(resolver.strip(raw)))) if refs else frozenset(toks)
    sh = h or en.sentence_hash(n)
    return SUnit(path, line, line_end, heading, sh, unit_key(path, sh), raw, n, kind, list(sections), toks,
                 has_marker(n), refs, extra, own)


def corpus_from_texts(files: dict, known=None, extra=()) -> list:
    """Units of each {path: text}; references resolve against `known` (default: these paths)."""
    resolver = Resolver(list(known) if known is not None else list(files))
    out = []
    for path in sorted(files):
        text = files[path]
        chains = section_chains(path, text)
        for u in en.units(path, text):
            out.append(make_unit(path, u.line_start, u.line_end, u.heading, u.raw, chains.get(u.line_start, []),
                                 resolver, u.kind, u.hash, path in extra))
    return out


def scope_files(repo, scope, staged=False):
    """The in-scope files the worktree holds, or -- with `staged` -- the ones the index the environment
    names holds, tracked only."""
    names = en.git(repo, *(("ls-files", "--cached", "-z") if staged
                           else ("ls-files", "-co", "--exclude-standard", "-z"))).split("\0")
    return sorted(p for p in set(names) if p and en.in_scope(scope, p))


def index_texts(repo, paths, rev=None) -> dict:
    """{path: text} for `paths` as the index the environment names holds them, or as `rev` holds them, read
    through one `git cat-file --batch` call. A path the store lacks is skipped, never read from the worktree."""
    if not paths:
        return {}
    names = [":%s" % p if rev is None else "%s:%s" % (rev, p) for p in paths]
    try:
        r = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                           input="".join(n + "\n" for n in names).encode("utf-8"), capture_output=True)
    except OSError as e:
        raise RuntimeError("cat-file --batch failed: %s" % e)
    raw, out, i, k = r.stdout, {}, 0, 0
    while k < len(paths) and i < len(raw):
        end = raw.find(b"\n", i)
        if end < 0:
            break
        head = raw[i:end].decode("utf-8", "replace")
        i = end + 1
        if head.endswith((" missing", " ambiguous")):
            k += 1
            continue
        oid, kind, size = (head.rsplit(" ", 2) + ["", ""])[:3]
        if kind != "blob" or not size.isdigit():
            raise RuntimeError("cat-file --batch printed an unreadable header: %s" % head)
        if i + int(size) > len(raw):
            break
        out[paths[k]] = raw[i:i + int(size)].decode("utf-8", "replace")
        i += int(size) + 1
        k += 1
    if r.returncode != 0 or k < len(paths):
        raise RuntimeError("cat-file --batch answered %d of %d paths (exit %d)" % (k, len(paths), r.returncode))
    return out


def load_corpus(repo, scope, extra_texts=None) -> list:
    files = {}
    for p in scope_files(repo, scope):
        try:
            files[p] = Path(repo, p).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    extra = set()
    for p, t in (extra_texts or {}).items():
        files[p] = t
        extra.add(p)
    return corpus_from_texts(files, extra=extra)


def load_staged_corpus(repo, scope, extra_texts=None) -> list:
    files = index_texts(repo, scope_files(repo, scope, staged=True))
    extra = set()
    for p, t in (extra_texts or {}).items():
        files[p] = t
        extra.add(p)
    return corpus_from_texts(files, extra=extra)


def base_sentence_hashes(repo, base, paths) -> dict:
    """{path: frozenset(sentence hash)} for `paths` as `base` holds them. A path `base` lacks is absent from
    the map: the change wrote that whole file, so every unit of it is a changed unit."""
    return {p: frozenset(u.hash for u in en.units(p, text))
            for p, text in index_texts(repo, paths, rev=base).items()}


def load_fixture_case(fix, case) -> list:
    root = os.path.join(fix, case)
    files = {}
    for d, _dirs, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            rel = os.path.relpath(full, root).replace("\\", "/")
            rel = rel[:-4] if rel.endswith(".txt") else rel
            with open(full, encoding="utf-8") as fh:
                files[rel] = fh.read()
    return corpus_from_texts(files)


# ---- model --------------------------------------------------------------------------------------------
class Model:
    """Unit frequency per content token; the IDF source for cosine and the frequency the anchor cap reads."""

    def __init__(self, df: dict, units: int, paths=()):
        self.df, self.n, self.paths = df, units, list(paths)
        self._key = object()

    def df_of(self, t):
        return max(self.df.get(t, 0), 1)

    def idf(self, t):
        return math.log((self.n + 1) / (self.df_of(t) + 1)) + 1

    def vec(self, u):
        cached = u.__dict__.get("_vec")
        if cached is not None and cached[0] is self._key:
            return cached[1]
        tf = {}
        for t in u.toks:
            tf[t] = tf.get(t, 0) + 1
        w = {t: c * self.idf(t) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
        v = {t: x / norm for t, x in w.items()}
        u.__dict__["_vec"] = (self._key, v)
        return v

    def cosine(self, a, b):
        va, vb = self.vec(a), self.vec(b)
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(t, 0.0) for t, x in va.items())


def frequency_table(corpus) -> dict:
    df = {}
    for u in corpus:
        for t in u.tokset:
            df[t] = df.get(t, 0) + 1
    return {"units": len(corpus), "df": df, "paths": sorted({u.path for u in corpus})}


def corpus_model(corpus) -> Model:
    t = frequency_table(corpus)
    return Model(t["df"], t["units"], t["paths"])


def frozen_model(fix) -> Model:
    with open(os.path.join(fix, "unit_frequency.json"), encoding="utf-8") as fh:
        t = json.load(fh)
    return Model(t["df"], t["units"], t["paths"])


# ---- pair predicates (the scan enumerates candidates, then confirms with these) --------------------------
def lexical_pair(a, b, model, cutoff):
    return a.path != b.path and a.lexical_ok and b.lexical_ok and model.cosine(a, b) >= cutoff


def anchor_terms(a, b, model, cap):
    if a.path == b.path or not (a.marker and b.marker):
        return []
    return sorted(t for t in a.tokset & b.tokset if model.df_of(t) <= cap)


def anchor_pair(a, b, model, cap):
    return bool(anchor_terms(a, b, model, cap))


def names(ref, b, rule):
    """True when reference `ref` of the citing unit points at unit `b`: its file, and its section when it names one."""
    kind, path, sec = ref
    if kind not in _REF_KINDS[rule] or path != b.path:
        return False
    return section_match(sec, b.sections) if sec is not None else rule != "named"


def cites(a, b, rule):
    return any(names(r, b, rule) for r in a.refs)


def substantive(toks, b, model, stoplist_df):
    """Two of `toks` shared with unit `b` whose unit frequency is below `stoplist_df`; a token at or above it is
    harness vocabulary."""
    return sum(1 for t in toks & b.tokset if model.df.get(t, 0) < stoplist_df) >= 2


def citation_pair(a, b, rule, model, stoplist_df):
    """One unit cites the other's file or section and shares two substantive tokens outside its own reference text."""
    return a.path != b.path and any(
        cites(x, y, rule) and substantive(x.own, y, model, stoplist_df) for x, y in ((a, b), (b, a)))


def stage_two(channel, a, b, model, stoplist_df):
    """Precision filter on the lexical channel's candidate. Anchor pairs already need a rare shared token and a
    marker on both sides, and a single rare token is how it finds a copy, so it passes unfiltered; the citation
    channel applies its own token test in `citation_pair`."""
    if channel == "lexical":
        return a.marker and b.marker and substantive(a.tokset, b, model, stoplist_df)
    return True


def pair_channels(a, b, model, thr) -> list:
    out = []
    if lexical_pair(a, b, model, thr.lexical_cutoff) and stage_two("lexical", a, b, model, thr.stoplist_df):
        out.append("lexical")
    if anchor_pair(a, b, model, thr.anchor_cap):
        out.append("anchor")
    if citation_pair(a, b, thr.citation_rule, model, thr.stoplist_df):
        out.append("citation")
    return out


# ---- the scan -------------------------------------------------------------------------------------------
def _prefix(model, v, cutoff):
    """A unit's rarest features, up to the point where the rest cannot reach `cutoff`: any partner at or
    above the cutoff shares one of them."""
    feats = sorted(v, key=lambda t: (model.df_of(t), t))
    suffix = [0.0] * (len(feats) + 1)
    for j in range(len(feats) - 1, -1, -1):
        suffix[j] = suffix[j + 1] + v[feats[j]] ** 2
    out = []
    for j, t in enumerate(feats):
        if math.sqrt(suffix[j]) < cutoff:
            break
        out.append(t)
    return out


def _lexical_candidates(corpus, model, cutoff, centers=None):
    """Pairs that can reach `cutoff`, as (earlier, later) index pairs; `scan` re-scores each. Every pair
    at or above the cutoff shares a feature in either member's prefix (`_prefix`). With `centers`, only
    the centers' prefixes are indexed and every other unit probes them, so each pair holding a center
    that can reach the cutoff is found without ranking every unit's features."""
    index, out = {}, set()
    if centers is not None:
        for c in sorted(centers):
            if corpus[c].lexical_ok:
                for t in _prefix(model, model.vec(corpus[c]), cutoff):
                    index.setdefault(t, []).append(c)
        for i, u in enumerate(corpus):
            if not u.lexical_ok:
                continue
            for t in model.vec(u):
                for c in index.get(t, ()):
                    if c != i and corpus[c].path != u.path:
                        out.add((min(c, i), max(c, i)))
        return out
    for i, u in enumerate(corpus):
        if not u.lexical_ok:
            continue
        v = model.vec(u)
        for t in v:
            for x in index.get(t, ()):
                if corpus[x].path != u.path:
                    out.add((x, i))
        for t in _prefix(model, v, cutoff):
            index.setdefault(t, []).append(i)
    return out


def scan(corpus, model, thr=THRESHOLDS, ruled=frozenset(), centers=None) -> dict:
    pairs = {}

    def add(i, j, channel, via):
        if i == j or corpus[i].path == corpus[j].path or not stage_two(channel, corpus[i], corpus[j], model,
                                                                        thr.stoplist_df):
            return
        if centers is not None and i not in centers and j not in centers:
            return
        key = (min(i, j), max(i, j))
        p = pairs.setdefault(key, {"channels": [], "via": {}, "cited": None})
        if channel not in p["channels"]:
            p["channels"].append(channel)
            p["via"][channel] = via

    for i, j in _lexical_candidates(corpus, model, thr.lexical_cutoff, centers):
        if model.cosine(corpus[i], corpus[j]) >= thr.lexical_cutoff:
            add(i, j, "lexical", None)

    post = {}
    for i, u in enumerate(corpus):
        if u.marker:
            for t in u.tokset:
                if model.df_of(t) <= thr.anchor_cap:
                    post.setdefault(t, []).append(i)
    for t, idx in post.items():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                add(idx[a], idx[b], "anchor", t)

    by_path = {}
    for i, u in enumerate(corpus):
        by_path.setdefault(u.path, []).append(i)
    kinds = _REF_KINDS[thr.citation_rule]
    for i, u in enumerate(corpus):
        for kind, path, sec in u.refs:
            if kind not in kinds or path == u.path:
                continue
            for j in by_path.get(path, ()):
                b = corpus[j]
                if names((kind, path, sec), b, thr.citation_rule) and substantive(u.own, b, model, thr.stoplist_df):
                    add(i, j, "citation", "%s%s" % (path, " §" + sec if sec else ""))
                    if "citation" in pairs.get((min(i, j), max(i, j)), {}).get("channels", ()):
                        pairs[(min(i, j), max(i, j))]["cited"] = j

    counts = {"lexical": 0, "anchor": 0, "citation": 0}
    ruled_n = 0
    live = {}
    for (i, j), p in pairs.items():
        if tuple(sorted((corpus[i].hash, corpus[j].hash))) in ruled:
            ruled_n += 1
            continue
        for c in p["channels"]:
            counts[c] += 1
        live[(i, j)] = p
    groups = []
    for c, partners in star_groups(corpus, live, centers).items():
        ms = sorted(partners, key=lambda k: (corpus[k].path, corpus[k].line))
        gid = hashlib.sha256("\n".join([corpus[c].key] + sorted(corpus[k].key for k in ms))
                             .encode("utf-8")).hexdigest()[:12]
        gpairs, cited = [], None
        for k in ms:
            p = live[(min(c, k), max(c, k))]
            a, b = corpus[c], corpus[k]
            gpairs.append({"a": a.key, "b": b.key, "channel": p["channels"][0], "channels": p["channels"],
                           "score": round(model.cosine(a, b), 3),
                           "via": next((p["via"][ch] for ch in p["channels"] if p["via"][ch]), None)})
            if p["cited"] is not None and cited is None:
                cited = corpus[p["cited"]].path
        units = [c] + ms
        groups.append({"id": gid, "center": corpus[c].key,
                       "members": [{"path": corpus[k].path, "line": corpus[k].line, "heading": corpus[k].heading,
                                    "hash": corpus[k].key, "sha": corpus[k].hash, "text": corpus[k].raw}
                                   for k in units],
                       "pairs": gpairs, "citedOwner": cited,
                       "_units": units})
    groups.sort(key=lambda g: (g["members"][0]["path"], g["members"][0]["line"], g["id"]))
    return {"groups": groups, "counts": {"units": len(corpus), "files": len(by_path), "pairs": counts,
                                         "ruled_pairs": ruled_n, "groups": len(groups)}}


def star_groups(corpus, keys, centers=None) -> dict:
    """{center index: [partner indices]}. Each pair goes to exactly one center: the lower-hash member when both
    are centers, else the member that is one; a pair touching no center is dropped. `centers=None` makes every
    unit a center."""
    out = {}
    for i, j in keys:
        ci = centers is None or i in centers
        cj = centers is None or j in centers
        if ci and cj:
            c = i if (corpus[i].hash, i) <= (corpus[j].hash, j) else j
        elif ci or cj:
            c = i if ci else j
        else:
            continue
        out.setdefault(c, []).append(j if c == i else i)
    return out


# ---- labeled pairs (the calibration's evidence) ----------------------------------------------------------
def load_labeled(fix, model) -> list:
    resolver = Resolver(model.paths)
    out = []
    with open(os.path.join(fix, "labeled_pairs.jsonl"), encoding="utf-8") as fh:
        for l in fh:
            if not l.strip():
                continue
            r = json.loads(l)
            side = lambda s: make_unit(s["path"], s["line"] or 0, s["line"] or 0, "", s["text"],  # noqa: E731
                                       s["sections"], resolver)
            out.append({"row": r["row"], "label": r["label"], "ua": side(r["a"]), "ub": side(r["b"])})
    return out


def channel_rates(labeled, model, thr) -> dict:
    """{channel: (recall on same=yes, rate on same=no)} for lexical, anchor, citation and their union."""
    yes = [o for o in labeled if o["label"] == "yes"]
    no = [o for o in labeled if o["label"] == "no"]
    hits = {id(o): pair_channels(o["ua"], o["ub"], model, thr) for o in labeled}
    out = {}
    for ch in ("lexical", "anchor", "citation", "union"):
        f = (lambda o: bool(hits[id(o)])) if ch == "union" else (lambda o, ch=ch: ch in hits[id(o)])
        out[ch] = (sum(1 for o in yes if f(o)) / len(yes), sum(1 for o in no if f(o)) / len(no))
    return out


# ---- CLI --------------------------------------------------------------------------------------------------
NO_SCOPE = ("restate_scan: no instruction_scope in the project adaptation file "
            "(paths and rows_dir are required); nothing was scanned, so no duplicate is covered")


RULINGS = ("distinct", "mirror")


def _rulings(rows_dir):
    rows, bad = en.read_jsonl(os.path.join(rows_dir, "rulings.jsonl"))
    return frozenset(tuple(sorted((r.get("a"), r.get("b")))) for r in rows if r.get("ruling") in RULINGS), bad


def _centers(corpus, extra, changed, paths, base=None):
    """{unit index} the run centers on, or None when every unit is a center. A unit is a center when the
    `--text` file holds it, or `--changed` lists one of its lines and the change wrote its sentence: `base`
    maps a path to the sentence hashes that file held at `<base>`, and a sentence it already held is the
    base-side version of a replaced hunk, not a unit the change adds. `--paths` narrows both to the files it
    names, by the same rule `en.changed_lines` applies to its own `paths`."""
    if not (extra or changed is not None or paths):
        return None
    out = set()
    for k, u in enumerate(corpus):
        if paths and not en.narrow(paths, u.path):
            continue
        if changed is not None and not u.extra:
            if not set(range(u.line, u.line_end + 1)) & changed.get(u.path, set()):
                continue
            if u.hash in (base or {}).get(u.path, ()):
                continue
        if changed is None and not paths and not u.extra:
            continue
        out.add(k)
    return out


def _run(args):
    repo = Path(args.repo).resolve()
    staged = bool(getattr(args, "staged", False))
    if staged and not getattr(args, "changed", False):
        print("restate_scan: --staged requires --changed; it names the diff whose added lines are the centers.")
        return None
    got = en.setup_scope(repo, args.rows_dir, NO_SCOPE)
    if got is None:
        return None
    scope, rows_dir = got
    extra = {}
    if getattr(args, "text", None):
        tp = Path(args.text).resolve()
        try:
            label = tp.relative_to(repo).as_posix()
        except ValueError:
            label = tp.as_posix()
        extra[label] = tp.read_text(encoding="utf-8")
    changed = None
    if getattr(args, "changed", False):
        try:
            changed = en.changed_lines(repo, args.base, scope, staged=staged)
        except RuntimeError as e:
            print("restate_scan: " + str(e))
            return None
    try:
        corpus = load_staged_corpus(repo, scope, extra) if staged else load_corpus(repo, scope, extra)
        base = base_sentence_hashes(repo, args.base, sorted(changed)) if changed else {}
    except RuntimeError as e:
        print("restate_scan: " + str(e))
        return None
    centers = _centers(corpus, extra, changed, getattr(args, "paths", None), base)
    ruled, bad = _rulings(rows_dir)
    out = scan(corpus, corpus_model(corpus), THRESHOLDS, ruled, centers)
    out["counts"]["unparseable_rows"] = bad
    return out, rows_dir


def _summary(out, shown):
    c = out["counts"]
    p = c["pairs"]
    return ("restate_scan: scope files %d, units %d, candidate pairs lexical %d, anchor %d, citation %d, "
            "ruled pairs %d, groups %d, shown %d, unparseable rows %d"
            % (c["files"], c["units"], p["lexical"], p["anchor"], p["citation"], c["ruled_pairs"], c["groups"], shown,
               c["unparseable_rows"]))


def cmd_pairs(args):
    ctx = _run(args)
    if ctx is None:
        return 2
    out, _rows_dir = ctx
    groups = out["groups"]
    public = [{k: v for k, v in g.items() if not k.startswith("_")} for g in groups]
    if args.json:
        print(json.dumps(public, ensure_ascii=False))
        print(_summary(out, len(public)), file=sys.stderr)
    else:
        for g in public:
            chans = sorted({c for p in g["pairs"] for c in p["channels"]})
            print("group %s: %d members, channels %s%s" % (g["id"], len(g["members"]), ",".join(chans),
                                                           ", cited owner " + g["citedOwner"] if g["citedOwner"] else ""))
            for m in g["members"]:
                t = m["text"]
                print("  %s:%d  %s" % (m["path"], m["line"], t if len(t) <= 200 else t[:197] + "..."))
        print(_summary(out, len(public)))
    return 0


def cmd_rule(args):
    held_by = args.held_by
    if args.ruling == "mirror":
        if not held_by:
            print("restate_scan: a mirror ruling needs --held-by, the path of the test or check that holds the pair equal")
            return 2
        if not (Path(args.repo).resolve() / held_by).is_file():
            print("restate_scan: --held-by %s is not a file in the repo; name the test or check that holds the pair equal"
                  % held_by)
            return 1
    elif held_by:
        print("restate_scan: --held-by belongs to a mirror ruling only")
        return 2
    ctx = _run(args)
    if ctx is None:
        return 2
    out, rows_dir = ctx
    g = next((g for g in out["groups"] if g["id"] == args.group_id), None)
    if g is None:
        print("restate_scan: no unruled group has id %s; run `pairs` for the current ids" % args.group_id)
        return 1
    paths = {m["hash"]: m["path"] for m in g["members"]}
    sha = {m["hash"]: m["sha"] for m in g["members"]}
    os.makedirs(rows_dir, exist_ok=True)
    with open(os.path.join(rows_dir, "rulings.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
        for p in g["pairs"]:
            (a, a_key), (b, b_key) = sorted(((sha[p["a"]], p["a"]), (sha[p["b"]], p["b"])))
            row = {"a": a, "b": b, "ruling": args.ruling, "reason": args.reason, "group": g["id"],
                   "paths": [paths.get(a_key), paths.get(b_key)]}
            if held_by:
                row["heldBy"] = held_by
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("restate_scan: ruled %d pairs of group %s %s" % (len(g["pairs"]), g["id"], args.ruling))
    return 0


SELFTEST_FILES = {
    ".claude/__restate_selftest__/owner.md": "Never run a second native job while the five-hour window is above its "
                                              "threshold; the guard denies it and names the reset.\n",
    ".claude/__restate_selftest__/copy.md": "Never run a second native job while the five-hour window is over its "
                                             "threshold; the guard denies it and names the reset.\n",
}


def cmd_selftest(args):
    repo = Path(args.repo).resolve()
    got = en.setup_scope(repo, None, NO_SCOPE)
    if got is None:
        return 2
    scope, rows_dir = got
    try:
        corpus = load_corpus(repo, scope, dict(SELFTEST_FILES))
    except RuntimeError as e:
        print("restate_scan: " + str(e))
        return 2
    centers = _centers(corpus, SELFTEST_FILES, None, None)
    out = scan(corpus, corpus_model(corpus), THRESHOLDS, set(), centers)
    planted = set(SELFTEST_FILES)
    found = any(planted <= {m["path"] for m in g["members"]} for g in out.get("groups", []))
    print("restate_scan selftest: %s" % ("ok, the planted near-copy is a group" if found
                                          else "FAIL, the planted near-copy came back as no group"))
    return 0 if found else 1


def main(argv=None):
    default_repo = str(Path(__file__).resolve().parents[2])
    ap = argparse.ArgumentParser(prog="restate_scan.py", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=default_repo)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--rows-dir", default=None, help="replaces the scope's rows_dir")
    common.add_argument("--text", default=None, help="adds a file's units (a plan) and centers groups on them")
    common.add_argument("--changed", action="store_true", help="center groups on units changed since --base")
    common.add_argument("--staged", action="store_true",
                        help="with --changed: read the added lines and the units from the index the environment "
                             "names (GIT_INDEX_FILE included), not the worktree")
    common.add_argument("--base", default="HEAD")
    common.add_argument("--paths", nargs="+", default=None,
                        help="narrow the centers to these files or directories")
    p = sub.add_parser("pairs", parents=[common], help="print the unruled candidate groups")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_pairs)
    p = sub.add_parser("rule", parents=[common], help="record a group as distinct decisions, or as a held mirror")
    p.add_argument("group_id")
    p.add_argument("ruling", choices=list(RULINGS))
    p.add_argument("--reason", required=True)
    p.add_argument("--held-by", default=None,
                   help="mirror only: repo path of the test or check that holds the pair equal")
    p.set_defaults(fn=cmd_rule)
    p = sub.add_parser("selftest", help="prove the scan returns a planted near-copy as a group")
    p.set_defaults(fn=cmd_selftest, rows_dir=None)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
