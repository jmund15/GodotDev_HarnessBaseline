#!/usr/bin/env python3
"""
baseline_identity.py -- one consumer-side identity scanner, two modes.

A "profile" is the plaintext, consumer-side set of tokens that must never leak into
the public baseline repo: lock substitution values, `identity.abbreviations`,
PascalCase/camelCase concatenations of those, home-path shapes derived from
`USERPROFILE`/`HOME`, topology tokens (subsystem ids/paths and declared type names),
and `content_nouns`. `build_profile()` builds it from already-loaded inputs.

Two scan modes share one extraction pass over each line:

  scan_changed(diff_text, profile)   compares extracted candidates against the
                                      PLAINTEXT profile directly. Added diff lines
                                      only. Runs in a consumer, where the plaintext
                                      profile already exists.

  scan_tree(repo_root, digests)      compares extracted candidates, HASHED, against
                                      SHA-256 digests loaded from a digests file.
                                      Every line of every `git ls-files` entry. Runs
                                      in the (public) baseline repo, which must never
                                      hold a consumer's plaintext values.

`digest --out <file>` builds this consumer's profile from its own repo and writes
(or replaces) its source entry in a digests file, so a public repo can carry hashes
only. The scanner never edits a scanned file.

Matching semantics: `name` and `concatenation` match case-insensitively over word
runs -- each line is split into alphabetic segments, each segment is further split
on camelCase boundaries, and every contiguous run of 1-3 of the resulting words
(lowercased, joined with no separator, including a run that crosses a single
space/underscore/hyphen) is tested against the digests. `abbreviation` keeps the
older boundary rule instead: an exact uppercase whole token, or a lowercase token
adjacent to `_`/`-`. `home`, `topology` and `noun` match a whole token exactly,
case-sensitively.

`digest`'s consumer label is the first 12 hex characters of
`sha256(f"{baseline_repo}|{root_name}")` -- stated once here; do not restate the
join format elsewhere.

A topology candidate (subsystem id, subsystem path, or declared type name) only
becomes a topology token when it is a COMPOUND of at least two camelCase, snake
or kebab words, AND it occurs zero times in the pinned baseline `template/`
tree at digest time -- the same absence rule `content_nouns` is seeded with
(S3 fix2, 2026-09-14; see `_topology_word_count` and `_pinned_template_tokens`).
A single generic word (a subsystem id like `tests`, a type name like `State`)
never becomes a topology token, compound or not. In a real consumer, the
pinned tree is read through `baseline_sync.ensure_baseline`'s object-store
batch reader against the already-synced `.claude/.cache/baseline-repo` cache
clone -- never a working tree, and never attempted before that cache exists,
so a pre-first-sync consumer builds an unfiltered (absence-check-skipped)
profile rather than fail. A test fixture (or `--baseline-dir`) supplies
`template_tokens` directly to `build_profile()`/`build_profile_for_repo()`,
so no test needs network access or a real baseline clone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOKEN_KINDS = ("name", "abbreviation", "concatenation", "home", "topology", "noun")
MIN_TOPOLOGY_NAME_LEN = 5
MIN_HOME_USER_LEN = 5
# Kinds hashed on their LOWERCASED value, matched via case-insensitive word runs
# or a case-folded boundary rule. `home`/`topology`/`noun` stay exact-case.
_LOWERED_KINDS = frozenset({"name", "concatenation", "abbreviation"})
DEFAULT_SUBSYSTEMS_PATH = ".claude/skills/project_subsystems/SKILL.md"


class IdentityError(RuntimeError):
    """The digests file is missing, unparseable, or internally inconsistent."""


class Hit:
    """A single identity match: `{path, line, token_kind, token_digest, excerpt}`.

    `token_digest` is the first 12 hex characters of the token's SHA-256. The id
    (`path:line:token_kind:token_digest`) is what `--accept-hit` takes verbatim.
    """

    __slots__ = ("path", "line", "token_kind", "token_digest", "excerpt")

    def __init__(self, path: str, line: int, token_kind: str, token_digest: str, excerpt: str) -> None:
        self.path = path
        self.line = line
        self.token_kind = token_kind
        self.token_digest = token_digest
        self.excerpt = excerpt

    @property
    def id(self) -> str:
        return f"{self.path}:{self.line}:{self.token_kind}:{self.token_digest}"

    def to_dict(self) -> dict:
        return {
            "path": self.path, "line": self.line, "token_kind": self.token_kind,
            "token_digest": self.token_digest, "excerpt": self.excerpt,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"Hit({self.id!r}, {self.excerpt!r})"


def _digest_full(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _pascal(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _camel(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


# ---------------------------------------------------------------------------
# Profile construction (pure function of already-loaded inputs; a caller such as
# the `digest` subcommand owns reading the lock, SKILL.md and adaptation.json).
# ---------------------------------------------------------------------------

def build_profile(
    *,
    substitutions: dict | None = None,
    abbreviations: list | None = None,
    subsystems: list | None = None,
    type_names: list | None = None,
    content_nouns: list | None = None,
    home_users: list | None = None,
    template_tokens: set | frozenset | None = None,
) -> dict:
    """Build the plaintext, consumer-side profile: `{kind: set[str]}`.

    `type_names` is expected pre-filtered for framework (Jmodot) declarations by the
    caller; this function applies only the length-5 exclusion, because that rule is
    profile-shape (§3), not a file-tree-walking concern.

    `template_tokens` is the whole-token vocabulary of the pinned baseline
    `template/` tree at digest time (see module docstring). A topology
    candidate (subsystem id/path or declared type name) is added only when it
    is a compound of >= 2 camelCase/snake/kebab words AND absent from
    `template_tokens` (empty/`None` `template_tokens` -- e.g. a caller that
    has not resolved the pinned tree -- means every compound candidate passes
    the absence half of the test).
    """
    substitutions = substitutions or {}
    abbreviations = [a for a in (abbreviations or []) if a]
    subsystems = subsystems or []
    type_names = type_names or []
    content_nouns = content_nouns or []
    home_users = [u for u in (home_users or []) if u]
    tree_tokens = set(template_tokens) if template_tokens else set()

    names = {v for v in substitutions.values() if isinstance(v, str) and v}
    abbrevs = set(abbreviations)

    bases = sorted(names | abbrevs)
    concatenations = set()
    for a in bases:
        for b in bases:
            if a == b:
                continue
            concatenations.add(_pascal(a) + _pascal(b))
            concatenations.add(_camel(a) + _pascal(b))

    home_paths: set[str] = set()
    for user in home_users:
        home_paths.update({
            f"C:/Users/{user}", f"C:\\Users\\{user}",
            f"/c/Users/{user}", f"/home/{user}",
        })
        if len(user) >= MIN_HOME_USER_LEN:
            home_paths.add(user)  # the contributor's bare user name (§3)

    def _is_topology_token(candidate: str) -> bool:
        return _topology_word_count(candidate) >= 2 and candidate not in tree_tokens

    topology: set[str] = set()
    for entry in subsystems:
        sid = entry.get("id") if isinstance(entry, dict) else None
        if sid and _is_topology_token(str(sid)):
            topology.add(str(sid))
        for p in (entry.get("paths") if isinstance(entry, dict) else None) or []:
            cleaned = str(p).rstrip("/")
            if cleaned and _is_topology_token(cleaned):
                topology.add(cleaned)
    for t in type_names:
        if t and len(t) >= MIN_TOPOLOGY_NAME_LEN and _is_topology_token(t):
            topology.add(t)

    nouns = {n for n in content_nouns if n}

    return {
        "name": names,
        "abbreviation": abbrevs,
        "concatenation": concatenations,
        "home": home_paths,
        "topology": topology,
        "noun": nouns,
    }


def digest_kinds(profile: dict) -> dict[str, list]:
    """Hash every profile value per kind: `{kind: [sha256, ...]}`, sorted, deduped.

    `name`/`concatenation`/`abbreviation` are hashed lowercased, so the scan side's
    case-insensitive matching compares like with like; `home`/`topology`/`noun`
    are hashed as authored (see module docstring: Matching semantics).
    """
    result: dict[str, list] = {}
    for kind in TOKEN_KINDS:
        values = profile.get(kind, ())
        if kind in _LOWERED_KINDS:
            values = {v.lower() for v in values}
        result[kind] = sorted({_digest_full(v) for v in values})
    return result


# ---------------------------------------------------------------------------
# Shared candidate extraction — the SAME shapes on both the digest-building side
# and the scan side, so hash comparison is symmetric.
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_PATH_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_HOME_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|/(?:[A-Za-z]/)?Users/|/home/)[^\\/\s\"']+"
)

# Word-run engine for `name`/`concatenation` (see module docstring: Matching
# semantics). `_ALPHA_RUN_RE` finds alphabetic runs; a run continues into the
# next one only across a single space/underscore/hyphen (spaced/snake/kebab
# forms), so unrelated prose two words apart never gets joined. `_CAMEL_WORD_RE`
# further splits one run on camelCase boundaries.
_ALPHA_RUN_RE = re.compile(r"[A-Za-z]+")
_CAMEL_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_RUN_JOIN_CHARS = frozenset(" _-")
_RUN_WORD_KINDS = ("name", "concatenation")
_MAX_RUN_WORDS = 3
# Whole-token kinds unaffected by the word-run rewrite: exact case, exact string.
_EXACT_WORD_KINDS = ("topology", "noun", "home")


def _camel_words(segment: str) -> list[str]:
    return _CAMEL_WORD_RE.findall(segment)


_TOPOLOGY_SPLIT_RE = re.compile(r"[_\-/]+")


def _topology_word_count(token: str) -> int:
    """Count camelCase/snake/kebab words in a single topology CANDIDATE token
    (an id, a path segment, or a declared type name) -- not a line. Splits on
    `_`/`-`/`/` first, then camelCase-splits each piece, so `wizard` -> 1,
    `wizardGuild`/`wizard_guild`/`wizard-guild` -> 2, `AI` -> 1 (§3 compound
    rule)."""
    return sum(len(_camel_words(part)) for part in _TOPOLOGY_SPLIT_RE.split(token) if part)


def _line_word_runs(line: str) -> list[list[str]]:
    """Group camelCase-split alphabetic words into runs. A run continues across
    a gap of exactly one space, underscore or hyphen; any wider gap (or none at
    all between unrelated segments) starts a new run."""
    runs: list[list[str]] = []
    current: list[str] = []
    prev_end = None
    for m in _ALPHA_RUN_RE.finditer(line):
        if prev_end is not None:
            gap = line[prev_end:m.start()]
            if len(gap) != 1 or gap not in _RUN_JOIN_CHARS:
                if current:
                    runs.append(current)
                current = []
        current.extend(_camel_words(m.group(0)))
        prev_end = m.end()
    if current:
        runs.append(current)
    return runs


def _run_windows(line: str):
    """Every contiguous run of 1-3 words, lowercased and joined without a
    separator."""
    for words in _line_word_runs(line):
        lowered = [w.lower() for w in words]
        n = len(lowered)
        for i in range(n):
            for length in range(1, min(_MAX_RUN_WORDS, n - i) + 1):
                yield "".join(lowered[i:i + length])


def _abbreviation_candidates(line: str):
    """Whole alphanumeric tokens eligible for the abbreviation boundary rule:
    an exact-uppercase token, or any-case token adjacent to `_`/`-`."""
    for m in _WORD_RE.finditer(line):
        token = m.group(0)
        before = line[m.start() - 1] if m.start() > 0 else ""
        after = line[m.end()] if m.end() < len(line) else ""
        if token == token.upper() or before in "_-" or after in "_-":
            yield token.lower()


def _make_hit(path: str, line_no: int, kind: str, token: str, content: str) -> Hit:
    full = _digest_full(token)
    return Hit(path=path, line=line_no, token_kind=kind, token_digest=full[:12],
               excerpt=content.strip()[:160])


def _scan_line(path: str, line_no: int, content: str, digest_sets: dict) -> list:
    hits = []
    for window in _run_windows(content):
        digest = _digest_full(window)
        for kind in _RUN_WORD_KINDS:
            if digest in digest_sets.get(kind, ()):
                hits.append(_make_hit(path, line_no, kind, window, content))
    for candidate in _abbreviation_candidates(content):
        if _digest_full(candidate) in digest_sets.get("abbreviation", ()):
            hits.append(_make_hit(path, line_no, "abbreviation", candidate, content))
    for m in _WORD_RE.finditer(content):
        token = m.group(0)
        digest = _digest_full(token)
        for kind in _EXACT_WORD_KINDS:
            if digest in digest_sets.get(kind, ()):
                hits.append(_make_hit(path, line_no, kind, token, content))
    for tok in _PATH_RE.findall(content):
        if _digest_full(tok) in digest_sets.get("topology", ()):
            hits.append(_make_hit(path, line_no, "topology", tok, content))
    for tok in _HOME_RE.findall(content):
        if _digest_full(tok) in digest_sets.get("home", ()):
            hits.append(_make_hit(path, line_no, "home", tok, content))
    return hits


# ---------------------------------------------------------------------------
# scan_changed — added diff lines only, against the plaintext profile.
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/(.+)|/dev/null)$")


def scan_changed(diff_text: str, profile: dict) -> list:
    """Scan only added lines of a unified diff against the plaintext `profile`.

    Reuses `_scan_line`'s digest comparison against a digest set built from the
    plaintext profile in-process, so both scan modes share one matching engine.
    """
    digest_sets = {kind: set(v) for kind, v in digest_kinds(profile).items()}
    hits: list = []
    path = None
    line_no = None
    for raw in diff_text.splitlines():
        file_m = _NEW_FILE_RE.match(raw)
        if file_m:
            path = file_m.group(1)
            line_no = None
            continue
        if raw.startswith("---"):
            continue
        hunk_m = _HUNK_RE.match(raw)
        if hunk_m:
            line_no = int(hunk_m.group(1))
            continue
        if raw.startswith("+"):
            if path is None or line_no is None:
                continue
            hits.extend(_scan_line(path, line_no, raw[1:], digest_sets))
            line_no += 1
        elif raw.startswith(" "):
            if line_no is not None:
                line_no += 1
        # '-' (removed) lines and diff metadata do not advance the "+" counter.
    return hits


# ---------------------------------------------------------------------------
# scan_tree — every git-tracked line, against SHA-256 digests.
# ---------------------------------------------------------------------------

def load_digests(path: Path) -> dict:
    """Load and validate a digests file; return the per-kind union of digests.

    Raises `IdentityError` when the file is absent, unparseable, or a source's
    `kinds` list is empty although its `counts` entry for that kind is non-zero.
    """
    if not path.is_file():
        raise IdentityError(f"identity digests file missing: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityError(f"identity digests file unparseable: {path}: {exc}") from exc
    sources = doc.get("sources")
    if sources is None:
        raise IdentityError(f"identity digests file missing 'sources': {path}")
    merged: dict[str, set] = {kind: set() for kind in TOKEN_KINDS}
    for source in sources:
        counts = source.get("counts") or {}
        kinds = source.get("kinds") or {}
        label = source.get("label", "<unlabeled>")
        for kind in TOKEN_KINDS:
            values = kinds.get(kind) or []
            if counts.get(kind, 0) and not values:
                raise IdentityError(
                    f"identity digests source {label!r} has a non-zero {kind} "
                    f"count but an empty {kind} kind list"
                )
            merged[kind].update(values)
    return merged


def scan_tree(repo_root: Path, digests: Path) -> list:
    """Scan every line of every `git ls-files` entry under `repo_root` for a
    digest match. Raises `IdentityError` for an unusable digests file."""
    digest_sets = load_digests(digests)
    result = subprocess.run(
        ["git", "ls-files"], cwd=repo_root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    hits: list = []
    for rel in result.stdout.decode("utf-8", errors="replace").splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            hits.extend(_scan_line(rel, i, line, digest_sets))
    return hits


# ---------------------------------------------------------------------------
# `digest` subcommand — run in a consumer; builds that consumer's real profile.
# ---------------------------------------------------------------------------

_YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_SUBSYSTEM_ITEM_RE = re.compile(
    r"^[ \t]*-[ \t]*id:[ \t]*(\S+)[ \t]*\n[ \t]*paths:[ \t]*\[([^\]]*)\]",
    re.MULTILINE,
)
_TYPE_DECL_RE = re.compile(
    r"^[ \t]*(?:\[[^\]]*\][ \t]*)*"
    r"(?:public|internal|private|protected)?[ \t]*"
    r"(?:sealed[ \t]+|abstract[ \t]+|static[ \t]+|partial[ \t]+)*"
    r"(?:class|struct|record|interface|enum)[ \t]+(\w+)",
    re.MULTILINE,
)


def parse_subsystems_yaml(text: str) -> list[dict]:
    """Parse the `subsystems:` fenced YAML block's `id`/`paths` rows.

    Hand-rolled for this one authored shape (no `yaml` module is a project
    dependency); `summary` and any other key are ignored.
    """
    block = ""
    for m in _YAML_FENCE_RE.finditer(text):
        if "subsystems:" in m.group(1):
            block = m.group(1)
            break
    out = []
    for m in _SUBSYSTEM_ITEM_RE.finditer(block):
        sid = m.group(1).strip()
        paths = [p.strip() for p in m.group(2).split(",") if p.strip()]
        out.append({"id": sid, "paths": paths})
    return out


def _load_lock(repo_root: Path) -> dict:
    lock_path = repo_root / ".claude" / "baseline.lock.json"
    if not lock_path.is_file():
        return {}
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _home_users() -> list[str]:
    users = []
    for var in ("USERPROFILE", "HOME"):
        val = os.environ.get(var)
        if not val:
            continue
        name = Path(val.replace("\\", "/")).name
        if name:
            users.append(name)
    return sorted(set(users))


def _collect_type_names(repo_root: Path, subsystem_paths: list[str]) -> list[str]:
    names: set[str] = set()
    for rel in subsystem_paths:
        base = repo_root / rel
        if not base.is_dir():
            continue
        for p in base.rglob("*.cs"):
            if "jmodot" in str(p).lower():
                continue  # framework declarations are excluded from topology (§3)
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            names.update(_TYPE_DECL_RE.findall(text))
    return sorted(names)


_BASELINE_CACHE_RELPATH = ".claude/.cache/baseline-repo"


def _load_baseline_sync():
    """Import the sibling `baseline_sync` module by file path, the same way
    this module's own test suite loads `baseline_identity` -- both live in
    `template/.claude/tools/`, and neither is installed as a package."""
    tools_dir = Path(__file__).resolve().parent
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import baseline_sync  # type: ignore
    return baseline_sync


def _pinned_template_tokens(
    repo_root: Path, lock: dict, baseline_dir: str | None = None,
) -> set[str]:
    """The whole exact-case candidate vocabulary (`_WORD_RE`) of the pinned
    baseline `template/` tree, read through git's object store via
    `baseline_sync.ensure_baseline` -- never a working tree (module docstring:
    topology absence rule). Used only by `build_profile_for_repo`; a test (or
    a caller that already has this set) should pass `template_tokens` to
    `build_profile()`/`build_profile_for_repo()` directly instead of calling
    this function, so no test needs network access or a real baseline clone.

    Returns an empty set -- absence-check skipped, not failed -- when the
    lock has no `baseline_repo` or the local baseline cache has not been
    created yet (no sync has run). `baseline_dir` overrides the cache clone
    with an already-local baseline checkout, exactly like `baseline_sync`'s
    own `--baseline-dir` flag, and needs no network.
    """
    if not lock.get("baseline_repo"):
        return set()
    if not baseline_dir and not (repo_root / _BASELINE_CACHE_RELPATH).is_dir():
        return set()
    baseline_sync = _load_baseline_sync()
    try:
        source = baseline_sync.ensure_baseline(lock, repo_root, baseline_dir)
    except baseline_sync.BaselineError as exc:
        raise IdentityError(f"could not resolve pinned template tree: {exc}") from exc
    try:
        listing = subprocess.run(
            ["git", "-C", str(source.path), "ls-tree", "-r", "--name-only",
             f"{source.sha}:template"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        tokens: set[str] = set()
        for relpath in listing.stdout.decode("utf-8", errors="replace").splitlines():
            relpath = relpath.strip()
            if not relpath:
                continue
            blob = source.read(relpath)
            if blob is None:
                continue
            tokens.update(_WORD_RE.findall(blob.decode("utf-8", errors="ignore")))
        return tokens
    finally:
        source.close()


def build_profile_for_repo(
    repo_root: Path, subsystems_rel_path: str | None = None, abbrev_csv: str | None = None,
    template_tokens: set | frozenset | None = None, baseline_dir: str | None = None,
) -> dict:
    """Build the real profile for the consumer rooted at `repo_root`.

    `subsystems_rel_path` (default `DEFAULT_SUBSYSTEMS_PATH`) locates the
    `subsystems:` SKILL.md; `adaptation.json` is read from that same directory
    (§8: adaptation data lives beside it, in `project_subsystems`). No legacy
    project-specific directory name is hardcoded here (report Q4 / no-legacy-name).
    `abbrev_csv` is used only when the consumer lock has no
    `identity.abbreviations` -- a lock value always wins.

    `template_tokens`, when given, is passed straight to `build_profile()` and
    no tree is read (the test/offline path). When omitted (`None`), this
    function resolves it itself via `_pinned_template_tokens` -- the real
    consumer path -- using `baseline_dir` if given (no network) or the synced
    `.claude/.cache/baseline-repo` cache clone otherwise.
    """
    lock = _load_lock(repo_root)
    substitutions = lock.get("substitutions") or {}
    lock_abbreviations = (lock.get("identity") or {}).get("abbreviations") or []
    if lock_abbreviations:
        abbreviations = lock_abbreviations
    else:
        abbreviations = [a.strip() for a in (abbrev_csv or "").split(",") if a.strip()]

    subsystems_rel_path = subsystems_rel_path or DEFAULT_SUBSYSTEMS_PATH
    skill_path = repo_root / subsystems_rel_path
    subsystems: list[dict] = []
    content_nouns: list[str] = []
    if skill_path.is_file():
        subsystems = parse_subsystems_yaml(skill_path.read_text(encoding="utf-8"))
        if not subsystems:
            raise IdentityError(
                f"{subsystems_rel_path} exists but parsed to zero subsystem rows "
                "-- check the `subsystems:` fenced YAML block's `- id: / paths:` shape"
            )
        adaptation_path = skill_path.parent / "adaptation.json"
        if adaptation_path.is_file():
            try:
                content_nouns = json.loads(
                    adaptation_path.read_text(encoding="utf-8")
                ).get("content_nouns") or []
            except (OSError, json.JSONDecodeError):
                content_nouns = []

    subsystem_paths = sorted({p for s in subsystems for p in s.get("paths", [])})
    type_names = _collect_type_names(repo_root, subsystem_paths)

    if template_tokens is None:
        template_tokens = _pinned_template_tokens(repo_root, lock, baseline_dir)

    return build_profile(
        substitutions=substitutions, abbreviations=abbreviations,
        subsystems=subsystems, type_names=type_names,
        content_nouns=content_nouns, home_users=_home_users(),
        template_tokens=template_tokens,
    )


def _consumer_label(baseline_repo: str, root_name: str) -> str:
    return hashlib.sha256(f"{baseline_repo}|{root_name}".encode("utf-8")).hexdigest()[:12]


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.encode("utf-8"))


def cmd_digest(
    out: Path, repo_root: Path, subsystems_rel_path: str | None = None,
    abbrev_csv: str | None = None, baseline_dir: str | None = None,
) -> int:
    profile = build_profile_for_repo(
        repo_root, subsystems_rel_path, abbrev_csv, baseline_dir=baseline_dir,
    )
    lock = _load_lock(repo_root)
    label = _consumer_label(lock.get("baseline_repo") or "", repo_root.name)
    counts = {kind: len(profile.get(kind, ())) for kind in TOKEN_KINDS}
    kinds = digest_kinds(profile)

    doc: dict = {"version": 1, "sources": []}
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("sources"), list):
                doc = existing
        except (OSError, json.JSONDecodeError):
            pass

    sources = [s for s in doc.get("sources", []) if s.get("label") != label]
    sources.append({"label": label, "counts": counts, "kinds": kinds})
    sources.sort(key=lambda s: s.get("label", ""))
    doc = {"version": doc.get("version") or 1, "sources": sources}

    _write_lf(out, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consumer-side identity scanner for the harness baseline.")
    sub = ap.add_subparsers(dest="command", required=True)

    digest_ap = sub.add_parser("digest", help="replace this consumer's source entry in a digests file")
    digest_ap.add_argument("--out", required=True, help="digests file to update")
    digest_ap.add_argument("--repo-root", default=None, help="consumer repo root (default: cwd)")
    digest_ap.add_argument(
        "--subsystems", default=None,
        help=f"subsystems SKILL.md path, relative to repo root (default: {DEFAULT_SUBSYSTEMS_PATH})",
    )
    digest_ap.add_argument(
        "--abbrev", default=None,
        help="CSV of abbreviations; used only when the consumer lock has no "
             "identity.abbreviations (a lock value wins)",
    )
    digest_ap.add_argument(
        "--baseline-dir", default=None,
        help="local baseline checkout for the topology absence rule (§3), in place of "
             "the synced .claude/.cache/baseline-repo cache clone -- no network",
    )

    args = ap.parse_args(argv)
    if args.command == "digest":
        repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
        try:
            return cmd_digest(
                Path(args.out), repo_root, args.subsystems, args.abbrev, args.baseline_dir,
            )
        except IdentityError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    return 2  # pragma: no cover - argparse `required=True` already rejects this


if __name__ == "__main__":
    sys.exit(main())
