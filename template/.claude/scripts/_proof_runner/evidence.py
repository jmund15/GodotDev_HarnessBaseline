"""One lookup over a proof's recorded passes, one codec for whole and case rows, the launch fingerprint and the
static reach.

A recorded pass is evidence: a row, the receipts it recorded and the inputs it read. `lookup` asks the row
matcher once and answers `Hit` (the receipts travel with the match) or `Miss` (the parts that moved);
`lookup_cases` does the same for a `_case_pool` proof's per-case rows. `pack_manifest` and `unpack_manifest` are
the one codec both kinds of row use. `stored_receipts` reads a store with no key and no write (the behavior
checker's coverage read). `launch_key` is the digest of the world a proof's launch puts it in; `Reach` is the
static reach a proof's key is taken over when its observed reads cannot judge a row.
"""
import ast
import base64
import functools
import hashlib
import json
import os
import re
import zlib
from typing import NamedTuple

from _proof_runner import catalog as _catalog

_TOKEN_RE = re.compile(r"[\w][\w.\-]*")
_WORD_RE = re.compile(r"\w+")

# What `launch_key` holds of the launcher's world. A change retires every row the earlier fingerprint recorded.
_LAUNCH_KEY_VERSION = 1



class Hit(NamedTuple):
    """A recorded pass that still holds, with the receipts the row recorded."""
    receipts: tuple = ()


class Miss(NamedTuple):
    """No recorded pass holds for these inputs: the parts that moved, as the row matcher named them
    (`unobserved`, `scheme`, `static`, `receipts`, or the keyed parts)."""
    reasons: tuple = ()


class CaseReuse(NamedTuple):
    """A `_case_pool` proof's case rows: `held` ({case name: manifest}) whose recorded pass still holds, and
    `missed` ({case name: why}) whose row no longer does."""
    held: dict
    missed: dict


@functools.lru_cache(maxsize=8)
def launch_policy(source, names):
    """A digest of the named top-level functions of `source`, docstrings dropped, or None when one is absent or
    the source does not parse. Comments, docstrings and line numbers do not change it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    parts = []
    for name in names:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
                parts.append(ast.dump(node))
                break
        else:
            return None
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def _local_modules(source, roots):
    """The repo modules `source` imports by absolute name and `roots` holds: the local helpers its code may run."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    found = []
    for name in sorted(names):
        for root in roots:
            for path in (os.path.join(root, name + ".py"), os.path.join(root, name, "__init__.py")):
                if os.path.isfile(path):
                    found.append(path)
                    break
            else:
                continue
            break
    return found


def launch_key(attempt, roots, recorders, interpreter, declared):
    """One digest over the world a proof's launch puts it in: the launcher's implementation, every local module
    it imports from `roots`, the recorders it installs, the interpreter and the declared proof policy. None when
    one input cannot be read, so a proof whose world is unknown reuses nothing. No checkout path or session id
    is an input, so a pass holds in a compatible checkout."""
    try:
        with open(attempt, encoding="utf-8") as fh:
            source = fh.read()
        digests = []
        for path in [attempt] + _local_modules(source, roots) + list(recorders):
            with open(path, "rb") as fh:
                digests.append(hashlib.sha256(fh.read()).hexdigest())
    except (OSError, UnicodeDecodeError):
        return None
    return hashlib.sha256(json.dumps([_LAUNCH_KEY_VERSION, digests, list(interpreter), declared],
                                     sort_keys=True).encode("utf-8")).hexdigest()


def lookup(cache, proof, key, cases=None):
    """The proof's recorded pass under `key`, or the reason none holds. One row match answers both: a `Hit`
    carries the row's receipts, so a caller never looks the row up again for them. `cases` are the proof's
    behavior cases; the row holds only when every case recorded a passing receipt for the same input."""
    row = cache.matched(proof, key)
    if row is None:
        return Miss(cache.miss_reasons(proof))
    receipts = tuple(row.get("observations") or ())
    if cases and not all(any(r.get("caseId") == c["caseId"] and r.get("inputKey") == c["inputKey"]
                             and r.get("status") == "pass" for r in receipts) for c in cases):
        return Miss(("receipts",))
    return Hit(receipts)


def lookup_cases(cache, proof):
    """The proof's `_case_pool` case rows whose recorded pass still holds, and each that does not with the part
    that moved. A case whose manifest cannot be taken (opaque reads, git state moved) is `unrecorded`."""
    prefix = _catalog.norm(os.path.relpath(proof, cache.root)) + "::"
    held, missed = {}, {}
    for name, entry in cache._rows(cache.case_path, proof).items():
        if not name.startswith(prefix):
            continue
        short, why = name[len(prefix):], ("unrecorded",)
        for row in entry if isinstance(entry, list) else []:
            manifest = unpack_manifest(row) if isinstance(row, dict) and row.get("okey") else None
            parts = cache.observed_parts(proof, manifest) if manifest else None
            if parts is not None and cache._combine(parts) == row["okey"]:
                held[short] = manifest
                break
            if parts is not None:
                why = tuple(cache._moved(row, parts))
        else:
            missed[short] = why
    return CaseReuse(held, missed)


def stored_receipts(store_dir):
    """Every receipt the pass store `store_dir` holds, read-only: each row's `observations`, flattened. It
    needs no key, migrates nothing and writes nothing; a damaged file or a row that recorded none contributes
    nothing."""
    receipts = []
    try:
        names = sorted(n for n in os.listdir(store_dir) if n.endswith(".json") and not n.startswith(".tmp_"))
    except OSError:
        return receipts
    for name in names:
        try:
            with open(os.path.join(store_dir, name), encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        for entry in doc.values() if isinstance(doc, dict) else ():
            for row in entry if isinstance(entry, list) else [entry]:
                if isinstance(row, dict):
                    receipts.extend(r for r in row.get("observations") or [] if isinstance(r, dict))
    return receipts


def pack_manifest(manifest):
    """A manifest as one short string. Raw, its probed import paths were 80% of a 118 MB cache file that
    every proof's record parsed and rewrote whole; packed, a row costs about 6 KB."""
    return base64.b64encode(zlib.compress(json.dumps(manifest, separators=(",", ":")).encode("utf-8"))).decode("ascii")


def unpack_manifest(row):
    """A row's manifest, packed or inline; None when its packed form does not decode."""
    packed = row.get("zm")
    if not packed:
        return row.get("manifest")
    try:
        return json.loads(zlib.decompress(base64.b64decode(packed)))
    except (ValueError, zlib.error, TypeError):
        return None


def packed_row(row):
    """`row` with an inline `manifest` (written before manifests were packed) moved to `zm`."""
    if not isinstance(row.get("manifest"), dict):
        return row
    return dict({k: v for k, v in row.items() if k != "manifest"}, zm=pack_manifest(row["manifest"]))


def file_digests(keyed):
    """{path: [short digest, tracked]} of a key's file rows: enough to name which file moved, never part of a key."""
    return {r: [d[:12] if d else None, t] for r, d, t in keyed}


def merge_manifests(manifests):
    """One manifest holding every read of `manifests`; opaque when any is. Each `why` cause is the first
    manifest's that names one."""
    merged = {"files": set(), "probes": set(), "dirs": set(), "static": set(), "objects": set(), "shared": set(),
              "git": set(), "opaque": False}
    why = {}
    for manifest in manifests:
        for field in ("files", "probes", "dirs", "static", "objects", "shared"):
            merged[field].update(manifest.get(field) or ())
        scopes = manifest.get("git")
        merged["git"].update({"repo"} if scopes is True else scopes or ())
        merged["opaque"] = merged["opaque"] or bool(manifest.get("opaque"))
        for scope, cause in (manifest.get("why") or {}).items():
            why.setdefault(scope, cause)
    merged["probes"] -= merged["files"]
    return dict({k: sorted(v) if isinstance(v, set) else v for k, v in merged.items()}, why=why)


class Reach(_catalog.Sources):
    """The static reach of a proof in one view (`_catalog.View`): itself, every file its text names, its
    name-rule targets, its declared inputs, the corpus it scans, the data its named or loaded code names, and the
    code any of those load, transitively. It takes no store and writes nothing, so a read-only view (a revision)
    asks it for a reach without opening a pass cache."""

    def __init__(self, view, declared=None, corpus=None):
        super().__init__(view)
        self.files = view.files
        self.by_base, self.by_stem, self.by_module = self.names
        self.declared = {}
        for rel, proofs in (declared or {}).items():
            for proof in proofs:
                self.declared.setdefault(proof, set()).add(rel)
        self.corpus = dict(corpus or {})
        self._named = {}

    def _names(self, rel, stems=True, raw=False):
        """Every file `rel` names by basename, and by code stem when `stems`; in its runnable text
        (`_code_text`) unless `raw`."""
        if (rel, stems, raw) not in self._named:
            text, found = self._text(rel) if raw else self._code_of(rel), set()
            for token in _TOKEN_RE.findall(text):
                found |= self.by_base.get(token.rstrip(".-"), set())
            if stems:
                for word in set(_WORD_RE.findall(text)):
                    found |= self.by_stem.get(word, set())
            self._named[rel, stems, raw] = found
        return self._named[rel, stems, raw]

    def _loads(self, rel):
        """The code `rel` may run: what it imports (`_imports`) and the code files its runnable text names
        by file name, which it may start or source."""
        return self._imports(rel) | {r for r in self._names(rel, stems=False) if r.endswith(_catalog.CODE_EXT)}

    def reach(self, proof):
        """The proof, what it names, the corpus it scans, the data its named or loaded code names, and the code any of those
        load, transitively. The proof's whole text names, prose included, so the reach holds every file
        `bindings` binds the proof to from its runnable text and loads; past the proof, a file only
        a comment or docstring names is not reached: that chain joins every file to every other and would
        reuse nothing. A part file (`_catalog.part_main`) also reaches all its proof reaches."""
        rel = _catalog.norm(os.path.relpath(proof, self.view.root))
        main = _catalog.part_main(rel, lambda candidate: self.view.exists(_catalog.norm(candidate)))
        seen = set()
        for own in [rel] + ([_catalog.norm(main)] if main else []):
            name = own.rsplit("/", 1)[-1]
            seen |= {own} | self.declared.get(name, set()) | self._names(own, raw=True)
            for stem, rels in self.by_stem.items():
                if _catalog.named_for(name, stem):
                    seen |= rels
            for folder in (_catalog.CORPUS.get(name) or self.corpus.get(name, ())):
                seen |= {r for r in self.files if r.startswith(folder + "/")}
        return self._expand(seen, rel)

    def _expand(self, seen, rel):
        """`seen` plus the data its named code names and the code any of it loads, transitively."""
        for named in [r for r in seen if r.endswith(_catalog.CODE_EXT) and r != rel]:
            seen |= {r for r in self._names(named) if not r.endswith(_catalog.CODE_EXT)}
        pending = [r for r in seen if r.endswith(_catalog.CODE_EXT)]
        while pending:
            code = pending.pop()
            for found in self._loads(code):
                if found not in seen:
                    seen.add(found)
                    pending.append(found)
                    seen |= {r for r in self._names(found) if not r.endswith(_catalog.CODE_EXT)}
        return seen
