#!/usr/bin/env python3
"""Check a review fold against disk and an independent quote run; never against its own report.

A fold author reports its judgment: the ids it applied (each with its `source`, an optional `file` and `noTest`),
the ids it deferred with a class and reason, `designDelta`, and the paths it changed. This checker computes every
fact from inputs the author did not write:

  * the ORIGINAL findings (the native journal's source findings, sidecar `review.json` sources,
    or an ungrouped findings list/object), independently of the routing view;
  * `verify_claims.check_claims` over those findings, rooted per routed subject, with every quote that cites a subject file
    resolved against that file's snapshot bytes;
  * the subject's snapshot (the pre-fold bytes) and the bytes on disk now.

A subject is `{root, files, newPaths, snapshot}`. `root` is the checkout the author edits, `files`
lists the paths findings may route to, `newPaths` lists files the author may create (test files
included), and `snapshot` is a manifest naming the pre-fold bytes in a form the author cannot
write: git blobs (`{"kind": "blobs", "files": {path: sha|null}, "head": sha}`,
written by `post_check.py snapshot`) or a base revision (`{"kind": "rev", "base": sha}`).

Checks, each a failure kind in the report:
  fold-routing            a finding's file names several subject files, or a file-less finding with several
                          subjects; a file outside every subject is disposition `out-of-subject`, held for
                          the session, never a failure
                          has no single subject to take it
  fold-invalid-shape      the digest lacks its applied/deferred lists of {id} rows
  fold-id-coverage        a routed id is absent from applied + deferred (held `unreported`); an unknown id is
                          reported and ignored; a repeated id counts once (an id in both lists counts deferred)
  fold-applied-unverified an applied id with evidence is not independently VERIFIED, or an
                          applied refutation has no verified evidence, unless `--ruled` names it
  fold-not-applied        an applied old/new pair whose `old` is still present: it occurs as often as in the
                          snapshot bytes of its file (an `adapted`, `fold` or `ruling` source may keep it on a
                          line that changed). A `finding`-sourced pair whose `old`
                          is gone but whose `new` is not verbatim is recorded `adapted` for the next round's
                          re-review, never a failure
  fold-old-missing        an applied FIX whose `old` occurs zero times in its file's snapshot bytes,
                          unless another applied id in the same file changed that region
  fold-applied-held       ASK or open PLAN (a PLAN with no options) applied without a caller-authorized ruling
  fold-application-unattributed an applied id resolves to no subject file
  fold-unchanged-file     an applied id over file bytes identical to the snapshot, unless `--ruled` names it
  fold-untested-code      an applied id on a code path without `noTest`, and the change set
                          holds no test path
  fold-test-order         a rev subject's fix commit precedes the test commit of the same id, or a
                          commit's kind disagrees with the paths it touches
  fold-out-of-subject     a changed or claimed path outside `files` and `newPaths` (a claimed path naming the fold's own findings,
                          verdicts, digest or prompt file, or any path under `.claude/scratch/`, is excepted)
  fold-snapshot-missing   the snapshot or a subject file is unreadable

`FAILURE_SCOPE` defines each failure's `ids` and voided `files`; an unknown kind voids the whole
subject. An `id` or `file` scoped failure, or a `finding` scoped one that names its ids, holds those ids alone; any other scope fails a post-check's outcome. A path-prefixed `designDelta` marks only its named changed file; an unnamed element marks
all changed files. Post-fold bytes are captured once, so recording never credits a later disk edit.

A successful child exit, a present digest or a self-reported pass never overrides a failure here.

  fold_check.py --findings <path> --subject <root>::<manifest>::<file>[,<file>...][::<new>[,<new>...]]
                --digest <digest.json> [--subject ... --digest ...] --out <report.json>
  fold_check.py --workflow-dir <review_fanout transcriptDir> --out <report.json>

`--ruled ID[,ID...]` supplies caller-authorized rulings; a digest's `source: ruling`
alone grants no authority. A fileless applied entry over several files names its
subject-relative `file`; changedPaths alone cannot attribute it.

`--workflow-dir` is the native route: each fold agent's first prompt (engine-authored) supplies
its `Root:`, `Snapshot:`, `Files:` and complete findings, and the journal supplies its returned
digest; the engine already flagged findings it could not route. A bounded result whose findings
were shed is refused as a `--findings` input.

`--subject/--digest` repeat once per subject, in the same order. Each finding routes to the
one subject whose `files` holds its `file`.
The report holds one disposition per source finding (a merged view id expands to its
`merged_from` sources), and one `nextRound` block per subject: the next review round's seats read
from the fold (`orchestration` §2 *Later rounds*). A `re-review` seat carries the units the fold
wrote itself, the `noTest` code entries, the reworded edits and the units whose check failed, with the mandates whose
findings cite them; a `grounding` seat carries each added line that states a number, path or line
cite; a `coherence` seat carries the digest's `designDelta`. `fileUnits` maps each changed file to
the reasons it holds a unit. `change` measures how much of the reviewed subject changed and how far
(`change_measure`); `--measure REVIEWED CURRENT` prints the same block for any two copies of a file.
`unaccounted` lists each added line holding text no applied finding proposed, read from the diff
rather than the fold's own report. `inline` is true when the subject changed and no seat is needed.
Beside `--out` it writes `<out>.findings.json` and `<out>.verdicts.json`, the independent checker
input and output.

EXIT: 0 pass; 1 a check failed; 2 unusable input.
"""
import argparse
from dataclasses import dataclass
import functools
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
import difflib  # noqa: E402

import adaptation  # noqa: E402
import extract_numbers  # noqa: E402
from _hook_state import is_harness_code  # noqa: E402
import verify_claims  # noqa: E402

_LINE = re.compile(r":[1-9]\d*(?:-[1-9]\d*)?$")
from typing import Literal

Scope = Literal["id", "file", "path", "finding", "subject", "none", "run"]
FAILURE_SCOPE: dict[str, Scope] = {
    "fold-not-applied": "id", "fold-applied-unverified": "id", "fold-old-missing": "id",
    "fold-applied-held": "id", "fold-application-unattributed": "id",
    "fold-test-order": "id", "fold-untested-code": "id", "fold-unchanged-file": "file",
    "fold-out-of-subject": "path", "fold-id-coverage": "id", "fold-routing": "finding",
    "fold-invalid-shape": "subject", "fold-snapshot-missing": "subject",
    "source-incomplete": "none", "ledger-record-failed": "run",
}
_CONTENT_FAILURES = tuple(kind for kind, scope in FAILURE_SCOPE.items() if scope == "id")


def scoped_failure(kind, detail, subject=None, rows=(), **extra):
    ids = extra.pop("ids", [extra["id"]] if extra.get("id") else [row["id"] for row in rows])
    given = extra.pop("files", None)
    scope = FAILURE_SCOPE.get(kind, "subject")
    files = []
    if given is not None:
        files = list(given)
    elif subject is not None:
        if scope in ("subject", "run"):
            files = sorted(subject.allowed)
        elif scope == "id":
            files = sorted({rel for row in rows if row["id"] in ids
                            if (rel := subject.file_of(row)) is not None})
    return dict(kind=kind, detail=detail, ids=sorted(set(ids)), files=files,
                **({"subject": subject.root_text} if subject else {}), **extra)

_FACT = re.compile(r"`[^`]*[/.][^`]*`|\b[\w.-]+\.(?:md|py|js|json|jsonl|cs|tres|tscn|ps1|sh|txt)\b|\w:\d+")


def test_dirs(claude_dir=None):
    """The project's test roots: `adaptation.json` `tests_root` and the harness proofs under `.claude/tests`."""
    return (adaptation.get(claude_dir or REPO / ".claude", "tests_root").strip("/"), ".claude/tests")


TEST_DIRS = test_dirs()


class Unusable(Exception):
    """Input the checker cannot evaluate; exit 2."""


def _abs(value):
    path = Path(str(value))
    return path if path.is_absolute() else REPO / path


def _slash(value):
    return str(value or "").strip().replace("\\", "/")


def _key(value):
    return os.path.normcase(os.path.normpath(_slash(value))).replace("\\", "/").lower()


def path_key(value, root=None):
    text = _slash(value)
    path = Path(text)
    base = Path(root) if root is not None else REPO
    anchored = path if path.is_absolute() else (base if base.is_absolute() else REPO / base) / path
    return os.path.normcase(os.path.normpath(str(anchored))).replace("\\", "/")


def _cited(value):
    return _LINE.sub("", str(value or "").strip())


_FOLD_DIGEST_NAME = re.compile(r"(fold\d*_)digest\.json")


def fold_prompt_path(digest):
    """The prompt file beside a fold digest the review engine named (`fold_digest.json`, joint
    `fold<N>_digest.json`), else None. The engine writes one prompt per fold next to its digest."""
    match = _FOLD_DIGEST_NAME.fullmatch(os.path.basename(str(digest)))
    return os.path.join(os.path.dirname(str(digest)), match[1] + "prompt.md") if match else None


def fold_verdicts_path(digest):
    """The checker-verdicts file the review engine names beside a fold digest, else None."""
    match = _FOLD_DIGEST_NAME.fullmatch(os.path.basename(str(digest)))
    return os.path.join(os.path.dirname(str(digest)), match[1] + "verdicts.json") if match else None


def rel_path(value, root=None):
    """A path as the posix path relative to `root` when it lies under it, else the posix path as written."""
    text = _slash(value)
    if not text:
        return ""
    if root is not None and Path(text).is_absolute():
        try:
            text = Path(text).relative_to(Path(root)).as_posix()
        except ValueError:
            return text
    normal = posixpath.normpath(text)
    return normal[2:] if normal.startswith("./") else normal


def is_test_path(path, root=None):
    """True under `Tests/` or `.claude/tests/` (relative to `root` when the path is absolute)."""
    rel = rel_path(path, root)
    return any(rel == d or rel.startswith(d + "/") for d in TEST_DIRS)


def is_code_path(path, root=None):
    """True for a `.cs` file or harness code (`_hook_state.is_harness_code`: a script or a settings file) that is not a test.

    The proof runner's own predicate, widened with `.cs`: a path `select_for` binds only by a proof's mention of
    its basename would make every documented markdown file code."""
    rel = rel_path(path, root)
    if is_test_path(rel):
        return False
    return rel.lower().endswith(".cs") or is_harness_code(rel)


def _read_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unusable(f"{what} unreadable: {path}: {exc}") from None


def load_rows(path, allow_empty=False):
    """-> view rows with ids. A sidecar review.json folds its `view`; anything else its findings. A review with
    no findings is a clean result, so `allow_empty` returns [] for it; a fold of nothing stays unusable."""
    value = _read_json(path, "findings")
    if isinstance(value, dict) and isinstance(value.get('delivery'), dict) and value['delivery'].get('payloadComplete') is False:
        raise Unusable(f'bounded preview with shed findings is not the original set: {path}')
    if isinstance(value, dict) and isinstance(value.get("view"), list):
        rows = value["view"]
    elif isinstance(value, dict):
        delivery = value.get("delivery")
        if isinstance(delivery, dict) and delivery.get("payloadComplete") is False:
            raise Unusable(f"bounded preview with shed findings is not the original set: {path}")
        rows = value.get("findings")
    else:
        rows = value
    if allow_empty and isinstance(rows, list) and not rows:
        return []
    return _validated(rows, path)


def original_rows(path, allow_empty=False):
    """Canonical originals, independently of the view used to route fold prompts."""
    value = _read_json(path, 'findings')
    views = load_rows(path, allow_empty=allow_empty)
    if isinstance(value, dict) and 'sources' in value:
        sources = value['sources']
        if not isinstance(sources, list) or any(not isinstance(source, dict) or
                not isinstance(source.get('finding'), dict) for source in sources):
            raise Unusable(f'original sources lack finding bodies: {path}')
        rows = [dict(source['finding'], id=source.get('id')) for source in sources]
        return [] if allow_empty and not rows else _validated(rows, path)
    if any(_sources(row) != [row['id']] for row in views):
        raise Unusable(f'grouped view lacks original finding bodies: {path}')
    return views


def _validated(rows, path):
    if not isinstance(rows, list) or not rows:
        raise Unusable(f"findings input has no nonempty findings/view array: {path}")
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise Unusable(f"finding {index} has no usable id")
        if row["id"] in seen:
            raise Unusable(f"duplicate finding id {row['id']}")
        seen.add(row["id"])
        evidence = row.get("evidence") or []
        if not isinstance(evidence, list) or any(
                not isinstance(item, dict) or not any(
                    all(isinstance(item.get(key), str) for key in keys)
                    for keys in (("file", "quote"), ("command", "output"), ("owner",), ("url", "quote")))
                for item in evidence):
            raise Unusable(f"finding {row['id']} has malformed evidence")
    return rows


# ---- git, snapshots and subjects -----------------------------------------------------------------


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    except OSError as exc:
        raise Unusable(f"git unavailable: {exc}") from None


def _git_text(root, *args):
    done = _git(root, *args)
    return done.stdout.decode("utf-8", errors="replace").strip() if done.returncode == 0 else None


def snapshot_blobs(root, files, new_paths=(), objects=REPO):
    """The blob manifest for `files` under `root`: each existing file written to the object database, each
    missing one recorded as null, plus the HEAD. `new_paths` declares files the author may create; the change
    set is these declared paths only, because another session's edit elsewhere in a shared checkout is not
    the fold's. A root outside any git checkout (a vault) writes its blobs to the `objects` repository (this
    project's by default), which the manifest names."""
    base = _abs(root)
    in_git = (base / ".git").exists() or _git_text(base, "rev-parse", "--git-dir") is not None
    store = base if in_git else _abs(objects)
    entries = {}
    for item in files:
        rel = rel_path(item, base)
        if (base / rel).is_file():
            done = _git(store, "hash-object", "--no-filters", "-w", "--", rel if in_git else str(base / rel))
            if done.returncode != 0:
                raise Unusable(f"git hash-object failed for {rel}: {done.stderr.decode(errors='replace').strip()}")
            entries[rel] = done.stdout.decode().strip()
        else:
            entries[rel] = None
    manifest = {"kind": "blobs", "root": str(root),
                "head": _git_text(base, "rev-parse", "--verify", "--quiet", "HEAD") if in_git else None,
                "files": entries}
    if not in_git:
        manifest["objects"] = str(store)
    if new_paths:
        manifest["newPaths"] = [rel_path(item, base) for item in new_paths]
    return manifest


def snapshot_rev(root, base_rev, files=(), new_paths=()):
    """The rev manifest: the base commit, plus the `files` findings may route to and the `new_paths` the author may
    create (a rev subject has no blobs to name them by)."""
    base = _abs(root)
    sha = _git_text(base, "rev-parse", "--verify", "--quiet", str(base_rev) + "^{commit}")
    if not sha:
        raise Unusable(f"rev snapshot base {base_rev} names no commit in {root}")
    manifest = {"kind": "rev", "root": str(root), "base": sha}
    if files:
        manifest["files"] = [rel_path(item, base) for item in files]
    if new_paths:
        manifest["newPaths"] = [rel_path(item, base) for item in new_paths]
    return manifest


def _ledger():
    import review_ledger
    return review_ledger


def _expand_gitlinks(root, base, changed):
    """`changed` with each submodule path replaced by the files changed inside it since the commit `base` pins."""
    ledger = _ledger()
    out = set(changed)
    for link in ledger.submodule_paths(root):
        if link not in out:
            continue
        out.discard(link)
        pin = _git_text(root, "rev-parse", "--verify", "--quiet", f"{base}:{link}")
        inside = Path(root) / link
        if not pin or not (inside / ".git").exists():
            out.add(link)
            continue
        names = _git(inside, "diff", "--name-only", "-z", pin).stdout.decode("utf-8", errors="replace").split("\0")
        names += _git(inside, "ls-files", "-z", "--others", "--exclude-standard").stdout.decode(
            "utf-8", errors="replace").split("\0")
        out |= {f"{link}/{name}" for name in _expand_gitlinks(inside, pin, {n for n in names if n})}
    return out


class Subject:
    """One fold subject: where the author edits, the files findings route to and the pre-fold bytes."""

    def __init__(self, root, files, snapshot, new_paths=(), manifest_path=None):
        self.root_text = str(root)
        self.root = _abs(root)
        self.files = [rel_path(item, self.root) for item in files]
        self.new_paths = [rel_path(item, self.root) for item in new_paths]
        self.snapshot = snapshot
        self.manifest_path = manifest_path
        self._pre = {}
        self._post = {}
        if not isinstance(snapshot, dict) or snapshot.get("kind") not in ("blobs", "rev"):
            raise Unusable(f"snapshot manifest has no kind blobs|rev: {manifest_path}")
        if snapshot["kind"] == "blobs":
            held = snapshot.get("files")
            if not isinstance(held, dict):
                raise Unusable(f"blob snapshot lists no files: {manifest_path}")
            missing = [item for item in self.files if item not in held and item not in self.new_paths]
            if missing:
                raise Unusable(f"snapshot {manifest_path} holds no entry for {', '.join(missing)}")
        elif not snapshot.get("base"):
            raise Unusable(f"rev snapshot names no base: {manifest_path}")

    @property
    def allowed(self):
        return set(self.files) | set(self.new_paths)

    def pre_bytes(self, rel):
        """The snapshot bytes of `rel`, or None when the path did not exist then."""
        if rel in self._pre:
            return self._pre[rel]
        snap = self.snapshot
        if snap["kind"] == "blobs":
            sha = snap["files"].get(rel)
            if sha is None:
                value = None
            else:
                done = _git(Path(snap["objects"]) if snap.get("objects") else self.root, "cat-file", "blob", sha)
                if done.returncode != 0:
                    raise Unusable(f"snapshot blob {sha} for {rel} is unreadable in {snap.get('objects') or self.root}")
                value = done.stdout
        else:
            sha = _ledger().blob_at(self.root, snap["base"], rel)
            done = _git(_ledger().owning_repo(self.root, rel)[0], "cat-file", "blob", sha) if sha else None
            value = done.stdout if done is not None and done.returncode == 0 else None
        self._pre[rel] = value
        return value

    def post_bytes(self, rel):
        if rel not in self._post:
            try:
                self._post[rel] = (self.root / rel).read_bytes()
            except OSError:
                self._post[rel] = None
        return self._post[rel]

    def change_set(self):
        """The paths that differ from the snapshot, read from disk: never from the digest."""
        snap = self.snapshot
        if snap["kind"] == "blobs":
            return sorted(item for item in self.files + [p for p in self.new_paths if p not in self.files]
                          if self.pre_bytes(item) != self.post_bytes(item))
        done = _git(self.root, "diff", "--name-status", "-z", snap["base"])
        if done.returncode != 0:
            raise Unusable(f"git diff against rev snapshot base {snap['base']} failed in {self.root}: "
                           f"{done.stderr.decode('utf-8', errors='replace').strip()}")
        fields = done.stdout.decode("utf-8", errors="replace").split("\0")
        changed, i = set(), 0
        while i < len(fields):
            status = fields[i]
            i += 1
            if not status:
                continue
            count = 2 if status[0] in "RC" else 1
            changed.update(fields[i:i + count])
            i += count
        extra = _git(self.root, "ls-files", "-z", "--others", "--exclude-standard")
        changed.update(item for item in extra.stdout.decode("utf-8", errors="replace").split("\0") if item)
        return sorted(_expand_gitlinks(self.root, snap["base"], changed))

    def tracked_at_snapshot(self, rel):
        """True when the snapshot's HEAD (or base) tracks `rel`."""
        snap = self.snapshot
        rev = snap.get("head") if snap["kind"] == "blobs" else snap.get("base")
        return bool(rev) and _ledger().blob_at(self.root, rev, rel) is not None

    def exact_hits(self, cited):
        """The subject files a cited path names exactly. An absolute citation names a file only when it is that
        file. Case-insensitive on every OS, as review_fanout.js routedTo; tests/fold_routing_vectors.json holds both."""
        text = _slash(_cited(cited))
        if not text:
            return []
        if Path(text).is_absolute():
            return [item for item in self.files if path_key(item, self.root).lower() == path_key(text).lower()]
        wanted = _key(text)
        return [item for item in self.files if _key(item) == wanted]

    def hits(self, cited):
        """The subject files a cited path names: an exact path first, else a relative citation as a path suffix.
        `route` asks every subject for an exact hit before any suffix hit."""
        exact = self.exact_hits(cited)
        text = _slash(_cited(cited))
        if exact or not text or Path(text).is_absolute():
            return exact
        wanted = _key(text)
        return [item for item in self.files if ("/" + _key(item)).endswith("/" + wanted)]

    def file_of(self, row):
        """The subject file a finding is about: the one its `file` names, else the sole file of a one-file subject."""
        cited = row.get("file")
        if cited:
            found = self.hits(cited)
            return found[0] if len(found) == 1 else None
        return self.files[0] if len(self.files) == 1 else None

    def application_file(self, row, decision):
        """Resolve explicit attribution without overriding a finding's cited target."""
        rel = self.file_of(row)
        named = decision.get('file')
        if named is None:
            return rel
        if not isinstance(named, str) or not named.strip():
            return None
        target = rel_path(_cited(named), self.root)
        if target not in self.allowed and '/' not in target:
            same = [path for path in self.allowed if posixpath.basename(path) == target]
            target = same[0] if len(same) == 1 else target
        if target not in self.allowed or (row.get('file') and target != rel):
            return None
        return target

    def describe(self):
        return {"root": self.root_text, "files": self.files, "newPaths": self.new_paths,
                "snapshot": self.manifest_path}


def load_subject(root, manifest_path, files, new_paths=()):
    manifest = _read_json(_abs(manifest_path), "snapshot manifest")
    return Subject(root, files, manifest, new_paths, str(manifest_path))


def subject_from_manifest(manifest_path, root=None):
    """A subject whose files are the manifest's own: the standalone form that has no prompt to read."""
    manifest = _read_json(_abs(manifest_path), "snapshot manifest")
    held = manifest.get("files") if isinstance(manifest, dict) else None
    files = sorted(held) if isinstance(held, dict) else held if isinstance(held, list) else None
    if not files:
        raise Unusable(f"snapshot manifest lists no files to fold (a rev manifest needs a `files` list): {manifest_path}")
    new_paths = manifest.get("newPaths") if isinstance(manifest.get("newPaths"), list) else []
    return Subject(root or manifest.get("root") or ".", files, manifest, new_paths, str(manifest_path))


def parse_subject_spec(spec):
    parts = str(spec).split("::")
    if len(parts) not in (3, 4) or not all(parts[:3]):
        raise Unusable(f"--subject needs <root>::<manifest>::<file>[,<file>...][::<new>[,<new>...]]: {spec!r}")
    files = [item for item in parts[2].split(",") if item]
    new = [item for item in parts[3].split(",") if item] if len(parts) == 4 else []
    return load_subject(parts[0], parts[1], files, new)


# ---- native route: fold prompts and the journal ---------------------------------------------------

_FOLD_PROMPT = "FOLD STAGE of a review."
# The runtime may relay the triggering user request first and wrap the script's prompt in a
# computed-task frame whose body is indented two spaces.
_RELAY_FRAME = "[Workflow harness — user request]"
_TASK_FRAME = "[Workflow harness — computed task]"
_TASK_BODY = "The computed task text follows:\n"


def _unframed(text):
    if not text.startswith(_TASK_FRAME) or _TASK_BODY not in text:
        return text
    body = text.split(_TASK_BODY, 1)[1]
    return "\n".join(line[2:] if line.startswith("  ") else line for line in body.splitlines())


def _first_prompt(path):
    """The engine-authored task text: the first user message that is not the runtime's relay
    of the triggering user request, with any computed-task frame removed."""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "user":
                    continue
                content = (event.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "".join(b.get("text", "") for b in content
                                      if isinstance(b, dict) and b.get("type") == "text")
                if not isinstance(content, str):
                    return None
                if content.startswith(_RELAY_FRAME):
                    continue
                return _unframed(content)
    except OSError:
        return None
    return None


def workflow_jobs(run_dir, required=True):
    """Native fold jobs from a Workflow transcript: the engine-authored fold prompt supplies the
    subject (`Root:`, `Snapshot:`, `Files:`, `New paths:`) and complete findings; the journal supplies the
    returned digest. With `required` false a run holding no fold agent gives no jobs."""
    run = Path(run_dir)
    journal = run / "journal.jsonl"
    results = {}
    try:
        for line in journal.read_text(encoding="utf-8").splitlines():
            row = json.loads(line) if line.strip() else {}
            if isinstance(row, dict) and row.get("type") == "result" and row.get("agentId"):
                results[row["agentId"]] = row.get("result")
    except (OSError, ValueError) as exc:
        raise Unusable(f"workflow journal unreadable: {journal}: {exc}") from None
    jobs = []
    for transcript in sorted(run.glob("agent-*.jsonl")):
        prompt = _first_prompt(transcript)
        if not prompt or not prompt.startswith(_FOLD_PROMPT):
            continue
        agent = transcript.stem[len("agent-"):]
        lines = prompt.splitlines()
        field = lambda name: next((l[len(name):].strip() for l in lines if l.startswith(name)), None)  # noqa: E731,E741
        start = next((i for i, l in enumerate(lines) if l.startswith("CONSOLIDATED FINDINGS (")), None)
        try:
            # The engine fences the rows as data (```json ... ```); an older prompt carries them bare.
            body = None if start is None else start + (2 if lines[start + 1].startswith("```") else 1)
            rows = json.loads(lines[body]) if body is not None else None
        except (IndexError, ValueError):
            rows = None
        root, snapshot, files = field("Root:"), field("Snapshot:"), field("Files:")
        new_paths = field("New paths:") or ""
        if not root or not snapshot or not files or rows is None:
            raise Unusable(f"fold prompt in {transcript.name} lacks Root, Snapshot, Files or findings")
        if agent not in results:
            raise Unusable(f"fold agent {agent} has no result in {journal}")
        subject = load_subject(root, snapshot, [f.strip() for f in files.split(",") if f.strip()],
                               [f.strip() for f in new_paths.split(",") if f.strip()])
        artifacts = [field(name) for name in ("Findings file:", "Verdicts file:", "Digest file:", "Prompt file:")
                     if field(name)]
        jobs.append({"subject": subject, "agentId": agent, "digestValue": results[agent], "artifacts": artifacts,
                     "rows": _validated(rows, transcript)})
    if not jobs and required:
        raise Unusable(f"no fold agent transcript in {run}")
    return jobs


_SOURCES = ("finding", "adapted", "fold", "ruling")


def _source_of(entry):
    """The applied entry's source; a missing or unknown value is the finding's own fix."""
    source = entry.get("source")
    return source if source in _SOURCES else "finding"


def _sources(row):
    merged = row.get("merged_from")
    if isinstance(merged, list) and merged and all(isinstance(i, str) and i for i in merged):
        return merged
    return [row["id"]]


def route(rows, subjects):
    """-> {id: subject index} plus unrouted and ambiguous ids. A finding goes to the one subject whose `files`
    holds its cited file; a finding citing no file goes to the sole subject when there is exactly one."""
    routed, unrouted, ambiguous = {}, [], []
    for row in rows:
        if not row.get("file"):
            if len(subjects) == 1:
                routed[row["id"]] = 0
            else:
                unrouted.append(row["id"])
            continue
        found = [subject.exact_hits(row["file"]) for subject in subjects]
        if not any(found):
            found = [subject.hits(row["file"]) for subject in subjects]
        hits = [i for i, files in enumerate(found) if files]
        if len(hits) == 1 and len(found[hits[0]]) == 1:
            routed[row["id"]] = hits[0]
        elif hits:
            ambiguous.append(row["id"])
        else:
            unrouted.append(row["id"])
    return routed, unrouted, ambiguous


def independent_verdicts(rows, subjects, out):
    """Seam 1 over the original evidence; a quote citing a subject file resolves against that file's snapshot
    bytes, written beside `out` where the author cannot reach them."""
    snap_dir = Path(str(out) + ".snap")
    artifacts = {}

    def artifact_of(subject, rel, index):
        if (id(subject), rel) not in artifacts:
            data = subject.pre_bytes(rel)
            if data is None:
                artifacts[(id(subject), rel)] = None
            else:
                snap_dir.mkdir(parents=True, exist_ok=True)
                target = snap_dir / f"{index}_{len(artifacts)}_{posixpath.basename(rel) or 'file'}"
                target.write_bytes(data)
                artifacts[(id(subject), rel)] = str(target)
        return artifacts[(id(subject), rel)]

    if not rows:
        return {}, None
    checker_rows = []
    for row in rows:
        evidence = []
        for item in row.get("evidence") or []:
            copy = dict(item)
            for number, subject in enumerate(subjects if isinstance(item.get("file"), str) else []):
                found = subject.hits(item["file"])
                if len(found) == 1:
                    snapshot = artifact_of(subject, found[0], number)
                    if snapshot:
                        copy["artifact"] = snapshot
                    break
            evidence.append(copy)
        checker_rows.append(dict(row, evidence=evidence))
    findings_path = Path(str(out) + ".findings.json")
    verdicts_path = Path(str(out) + ".verdicts.json")
    findings_path.write_text(json.dumps({"findings": checker_rows}, indent=2, ensure_ascii=False)
                             + "\n", encoding="utf-8", newline="\n")
    routed, _unrouted, _ambiguous = route(rows, subjects)
    verdicts, errors = [], []
    for row in checker_rows:
        if not row['evidence']:
            continue
        subject = subjects[routed[row["id"]]] if row["id"] in routed else None
        report = verify_claims.check_claims([row], root=subject.root if subject else REPO)
        errors.extend(report["errors"])
        verdicts.extend(dict(id=key, **value) for key, value in report["verdicts"].items())
    result = {"status": "complete" if not errors else "failed", "verdicts": verdicts, "errors": errors}
    verdicts_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="\n")
    if errors:
        raise Unusable("independent checker failed: " + "; ".join(errors))
    return {row["id"]: row["verdict"] for row in result["verdicts"]}, str(verdicts_path)


# ---- checks ---------------------------------------------------------------------------------------


def _flat(text):
    return re.sub(r"\s+", " ", text).strip()


def _unit_changed(before, after, old):
    """True when some pre-fold line run holding `old` is no longer a run of whole lines after the fold."""
    lines = [_flat(line) for line in before.splitlines()]
    kept = "\n" + "\n".join(_flat(line) for line in after.splitlines()) + "\n"
    for i in range(len(lines)):
        for j in range(i, len(lines)):
            if old in _flat(" ".join(lines[i:j + 1])):
                if old not in _flat(" ".join(lines[i + 1:j + 1])) and \
                        "\n" + "\n".join(lines[i:j + 1]) + "\n" not in kept:
                    return True
                break
            if len(_flat(" ".join(lines[i + 1:j + 1]))) >= len(old):
                break
    return False


def _text(data):
    return "" if data is None else data.decode("utf-8", errors="replace")


def hold_class(row):
    """Finding classes whose application needs a caller-authorized ruling: an ASK, and a PLAN with no `options`."""
    if row.get('action') == 'ASK':
        return 'ask'
    if row.get('action') == 'PLAN' and not any(str(o or '').strip() for o in row.get('options') or ()):
        return 'open-plan'
    return None


def check_job(job, rows, independent, failures):
    """Validate one subject's digest over the ids routed to it; -> {id: (disposition, taken)}.

    The digest is judgment only: `taken` is True for an applied id, False for a deferred one, None for an absent one."""
    subject = job["subject"]
    fail = lambda kind, detail, **extra: failures.append(scoped_failure(kind, detail, subject, rows, **extra))  # noqa: E731
    ids = [row["id"] for row in rows]
    by_id = {row["id"]: row for row in rows}
    digest = job["digestValue"]
    job["reworded"] = set()
    lists = ("applied", "deferred")
    if not isinstance(digest, dict) or any(not isinstance(digest.get(k), list) for k in lists) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
            for k in lists for entry in digest[k]):
        fail("fold-invalid-shape", "digest lacks applied/deferred lists of {id} rows")
        return {i: ("missing", None) for i in ids}
    listed = [entry["id"] for k in lists for entry in digest[k]]
    deferred = [i for i in dict.fromkeys(entry["id"] for entry in digest["deferred"]) if i in by_id]
    applied = [i for i in dict.fromkeys(entry["id"] for entry in digest["applied"]) if i in by_id and i not in deferred]
    unknown = sorted({i for i in listed if i not in by_id})
    repeated = sorted({i for i in listed if listed.count(i) > 1})
    absent = [i for i in ids if i not in listed]
    if unknown or repeated:
        fail("fold-id-coverage", f"unknown {unknown or '-'}; repeated {repeated or '-'}; counted once", ids=[])
    if absent:
        fail("fold-id-coverage", f"absent from applied and deferred: {', '.join(absent)}", ids=absent, files=[])
    entries = {}
    for entry in digest["applied"]:
        entries.setdefault(entry["id"], entry)
    sources = {i: _source_of(entries[i]) for i in applied}
    for i in applied:
        row = by_id[i]
        held = hold_class(row)
        if held and i not in (job.get('ruled') or ()):
            fail('fold-applied-held', f'{i}: {held} applied without a caller-authorized ruling', id=i)
        has_evidence = bool(row.get("evidence"))
        if i in (job.get('ruled') or ()):
            continue
        if (has_evidence and independent.get(i) != "VERIFIED") or (row.get("refuting") is True and not has_evidence):
            fail("fold-applied-unverified", f"{i} is applied without independently VERIFIED "
                                            f"evidence", id=i)
    try:
        tables = {rel: (subject.pre_bytes(rel), subject.post_bytes(rel)) for rel in subject.allowed}
        change = subject.change_set()
    except Unusable as exc:
        fail("fold-snapshot-missing", str(exc))
        tables, change = None, None
    if tables is not None:
        file_of = {i: subject.application_file(by_id[i], entries.get(i, {})) for i in ids}
        for i in applied:
            if file_of.get(i) is None:
                fail('fold-application-unattributed', f'{i}: applied id resolves to no subject file', id=i)
        state = _FoldState(by_id, applied, file_of, tables, sources, entries, digest)
        job["reworded"] = _check_old_new(state, fail)
        _check_unchanged_files(state, fail, set(job.get('ruled') or ()))
        _check_untested_code(state, change, fail)
        _check_out_of_subject(subject, job, digest, change, fail)
        if subject.snapshot["kind"] == "rev":
            _check_commit_order(subject, digest, fail)
    return {i: ("applied" if i in applied else "deferred" if i in deferred else "missing",
                True if i in applied else False if i in deferred else None) for i in ids}


def _old_touched_by_other(i, old, rel, applied, file_of, by_id):
    """True when another applied id in the same file introduced or rewrote `old`'s text."""
    return any(
        j != i and file_of.get(j) == rel and any(
            isinstance(by_id[j].get(k), str) and by_id[j][k].strip() and (
                _flat(old) in _flat(by_id[j][k]) or (k == "old" and _flat(by_id[j][k]) in _flat(old)))
            for k in ("old", "new"))
        for j in applied)


@dataclass
class _FoldState:
    """One fold job's findings by id, applied ids, file of each id, (pre, post) bytes per file, each applied id's
    source and entry, and the digest, as the per-job checks read them."""
    by_id: dict
    applied: list
    file_of: dict
    tables: dict
    sources: dict
    entries: dict
    digest: dict


def _check_old_new(state, fail):
    """Fail an applied id whose `old` is still present; -> the ids whose `old` is gone but whose `new` is not verbatim
    though the fold named `finding` as the source: those are reworded edits for the next round's re-review."""
    by_id, applied, file_of, tables = state.by_id, state.applied, state.file_of, state.tables
    reworded = set()
    for i in applied:
        row = by_id.get(i) or {}
        old, new = row.get("old"), row.get("new")
        rel = file_of.get(i)
        if not (isinstance(old, str) and old.strip() and isinstance(new, str) and new.strip()) or rel is None:
            continue
        before, text = _text(tables[rel][0]), _text(tables[rel][1])
        flat, flat_before = _flat(text), _flat(before)
        had = flat_before.count(_flat(old))
        if had == 0 and not _old_touched_by_other(i, old, rel, applied, file_of, by_id):
            fail("fold-old-missing", f"{i}: old occurs zero times in the snapshot bytes of {rel}", id=i)
        old_absent = _flat(old) in _flat(new) or had == 0 or flat.count(_flat(old)) < had
        if not old_absent and state.sources.get(i) in ("adapted", "fold", "ruling"):
            old_absent = _unit_changed(before, text, _flat(old))
        if not old_absent:
            fail("fold-not-applied", f"{i}: old is still present in {rel}", id=i)
        elif _flat(new) not in flat and state.sources.get(i) == "finding":
            reworded.add(i)
    return reworded


def _check_unchanged_files(state, fail, ruled=()):
    """An applied id over bytes identical to the snapshot fails, unless the caller ruled on it: a ruling may revert a
    fold edit."""
    unchanged = {}
    for i in state.applied:
        rel = state.file_of.get(i)
        if i not in ruled and rel is not None and state.tables[rel][0] == state.tables[rel][1]:
            unchanged.setdefault(rel, []).append(i)
    for rel, owed in sorted(unchanged.items()):
        fail("fold-unchanged-file", f"{len(owed)} applied id(s) {', '.join(owed)} over {rel} bytes identical "
                                    f"to the snapshot", ids=owed)


def _check_untested_code(state, change, fail):
    tested = any(is_test_path(p) for p in change)
    for i in state.applied:
        rel = state.file_of.get(i)
        if rel is not None and is_code_path(rel) and not tested and not state.entries.get(i, {}).get("noTest"):
            fail("fold-untested-code", f"{i} changes code path {rel} with no test path in the change set "
                                       f"and no noTest reason", id=i)


def _check_out_of_subject(subject, job, digest, change, fail):
    allowed = subject.allowed
    outside = sorted(p for p in change if p not in allowed)
    own = {rel_path(p, subject.root) for p in [*(job.get("artifacts") or ()), job.get("digest"),
                                               fold_verdicts_path(job.get("digest") or "")] if isinstance(p, str) and p}
    claimed = [rel_path(p, subject.root) for p in digest.get("changedPaths") or []
               if isinstance(p, str) and rel_path(p, subject.root) not in own
               and not rel_path(p, subject.root).startswith(".claude/scratch/")]
    outside += sorted(p for p in claimed if p not in allowed and p not in outside)
    held = subject.snapshot.get("files") if subject.snapshot["kind"] == "blobs" else None
    for p in claimed:
        if (held is not None and held.get(p) is None and not is_test_path(p)
                and subject.tracked_at_snapshot(p) and p not in outside):
            outside.append(p)
    if outside:
        fail("fold-out-of-subject", "changed or claimed outside the subject's files and new paths: "
                                    + ", ".join(outside), ids=[], paths=outside)


def _check_commit_order(subject, digest, fail):
    commits = digest.get("commits")
    if not isinstance(commits, list) or not commits:
        return
    done = _git(subject.root, "rev-list", "--reverse", f"{subject.snapshot['base']}..HEAD")
    order = [sha for sha in done.stdout.decode().split() if sha] if done.returncode == 0 else []
    seen = {}
    for entry in commits:
        sha = entry.get("sha") if isinstance(entry, dict) else None
        kind = entry.get("kind") if isinstance(entry, dict) else None
        ids = [i for i in (entry.get("ids") if isinstance(entry, dict) else None) or [] if isinstance(i, str)]
        full = next((c for c in order if sha and c.startswith(sha)), None)
        if full is None or kind not in ("test", "fix"):
            fail("fold-test-order", f"commit {sha!r} is not in {subject.snapshot['base'][:9]}..HEAD or has no kind test|fix", ids=ids)
            continue
        paths = _git_text(subject.root, "show", "--name-only", "--format=", full) or ""
        touched = [p for p in paths.splitlines() if p]
        if (kind == "test" and not any(is_test_path(p) for p in touched)) or (
                kind == "fix" and not any(not is_test_path(p) for p in touched)):
            fail("fold-test-order", f"commit {full[:9]} is labelled {kind} but touches {touched or 'nothing'}", ids=ids)
        for i in ids:
            seen.setdefault(i, []).append((order.index(full), kind))
    for i, rows in sorted(seen.items()):
        tests = [pos for pos, kind in rows if kind == "test"]
        fixes = [pos for pos, kind in rows if kind == "fix"]
        if fixes and (not tests or min(fixes) < min(tests)):
            fail("fold-test-order", f"{i}: its fix commit precedes its test commit" if tests
                 else f"{i}: a fix commit with no test commit", id=i)


# ---- next round -----------------------------------------------------------------------------------


def _unaccounted(added, news, before):
    """Added lines holding text no applied finding proposed: each line minus the findings' `new`
    text must be text the pre-fold file already held, or the line is listed."""
    pre = _flat(before)
    out = []
    for line in added:
        flat = _flat(line)
        if not flat or any(flat in new for new in news):
            continue
        hits = sorted((new for new in news if new in flat), key=len, reverse=True)
        if hits and all(part.strip() in pre for part in re.split("|".join(map(re.escape, hits)), flat)
                        if part.strip()):
            continue
        out.append(line.strip())
    return out


_UNIT = re.compile(r"^\s*[-*]\s+\*\*([A-Za-z][\w-]*(?: \d+)?)")
_STEP = re.compile(r"^\s*(\d+)[.)]\s")
_HEADING = re.compile(r"^#+\s+(.*\S)")


def _units(lines):
    """Each line's owning unit and each unit's citation pattern. A unit is a heading (its own prose), a
    bold-labelled bullet or a numbered step (`<section> <n>`); a line belongs to the nearest unit above it."""
    owner, cite, section, current = {}, {}, "", None
    for i, line in enumerate(lines):
        heading, bullet, step = _HEADING.match(line), _UNIT.match(line), _STEP.match(line)
        if heading:
            section = current = heading.group(1).strip()
            cite[current] = r"(?i)" + re.escape(section)
        elif bullet:
            current = bullet.group(1)
            cite[current] = r"(?<![\w-])" + re.escape(current) + r"(?![\w-])"
        elif step:
            current = f"{section} {step.group(1)}".strip()
            cite[current] = r"(?i)(?<![\w-])step\s+" + step.group(1) + r"(?![\w-])"
        if current is not None:
            owner[i] = current
    return owner, cite


def change_measure(pre, post, markdown=True):
    """How much of a reviewed file changed, and how far: the inputs a later round is sized from.

    `changedLines` counts the non-blank lines each diff hunk touches on its longer side, over `lines`,
    the current file's non-blank lines. For markdown, units are headings, bold-labelled bullets and numbered steps
    (`_units`); `changedUnits` hold a changed, added or removed line, `unplacedLines` counts changed lines
    no unit holds, and `reach` maps each changed unit to the unchanged units that cite it by label, step
    number or heading: literal citations only, so a dependency by concept stays the reader's judgment.
    A heading cites by its literal words, so a common-word heading over-reaches. Any other file is measured as
    changed lines and hunks (`git diff --stat` semantics): it has no units, so `hunks` counts its changed runs.
    The measure informs the width and depth judgment; it sets neither."""
    if not markdown:
        count, hunks = 0, 0
        for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, pre, post, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            hunks += 1
            count += max(sum(1 for line in pre[a1:a2] if line.strip()), sum(1 for line in post[b1:b2] if line.strip()))
        return {"lines": sum(1 for line in post if line.strip()), "changedLines": count, "hunks": hunks,
                "units": 0, "changedUnits": [], "unplacedLines": count, "reach": {}, "unitsReached": 0}
    (was, _), (now, cite) = _units(pre), _units(post)
    changed, count, unplaced = set(), 0, 0
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, pre, post, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        count += max(sum(1 for line in pre[a1:a2] if line.strip()), sum(1 for line in post[b1:b2] if line.strip()))
        for owners, lines, lo, hi in ((was, pre, a1, a2), (now, post, b1, b2)):
            for i in range(lo, hi):
                if owners.get(i):
                    changed.add(owners[i])
                elif lines[i].strip() and owners is now:
                    unplaced += 1
    text = {}
    for i, label in now.items():
        text.setdefault(label, []).append(post[i])
    steady = {label: "\n".join(body) for label, body in text.items() if label not in changed}
    reach = {label: sorted(other for other, body in steady.items()
                           if label in cite and re.search(cite[label], body))
             for label in sorted(changed)}
    return {"lines": sum(1 for line in post if line.strip()), "changedLines": count,
            "units": len(text), "changedUnits": sorted(changed), "unplacedLines": unplaced, "reach": reach,
            "unitsReached": len({other for cited in reach.values() for other in cited})}


@functools.lru_cache(maxsize=None)
def _seat_mandates(part):
    """The registry's mandates for one agent part, read once per process; None when the registry cannot name it."""
    try:
        import lens
        return tuple(lens.seat_mandates(part) or ()) or None
    except (Exception, SystemExit):
        return None


def _mandates_of(agent):
    """Catalog keys a finding's `agent` names: a lens seat key expands through `lens.seat_mandates`, a catalog key
    names itself."""
    out = []
    for part in str(agent or "").split(","):
        part = part.strip()
        if not part:
            continue
        out.extend(key for key in (_seat_mandates(part) or [part]) if key not in out)
    return out


def next_round(job, mine, failures):
    """The next review round's seats for one subject, read from its fold (`orchestration` §2 *Later rounds*)."""
    subject = job["subject"]
    digest = job.get("digestValue") if isinstance(job.get("digestValue"), dict) else {}
    applied = [d for d in digest.get("applied") or [] if isinstance(d, dict)]
    by_id = {row["id"]: row for row in mine}
    ids = set(by_id)
    taken = {d.get("id") for d in applied}
    failed = {f["id"] for f in failures if f.get("id") in ids and f["kind"] in _CONTENT_FAILURES}
    reworded = set(job.get("reworded") or ())
    try:
        change = subject.change_set()
        before = {rel: subject.pre_bytes(rel) for rel in subject.allowed}
        after = {rel: subject.post_bytes(rel) for rel in subject.allowed}
    except Unusable:
        change = before = after = None
    entries = {d.get("id"): d for d in applied}
    file_of = {i: subject.application_file(row, entries.get(i, {})) for i, row in by_id.items()}
    notest_code = {d.get("id") for d in applied if d.get("noTest")
                   and file_of.get(d.get("id")) and is_code_path(file_of[d["id"]])}
    sources = {d.get("id"): _source_of(d) for d in applied}
    sources.update({i: "adapted" for i in reworded})
    fold_units = {i for i, source in sources.items() if source in ("fold", "ruling")} | reworded
    units = sorted((fold_units | notest_code | failed) & ids)
    anchors = {row.get("file") for row in mine if row["id"] in units and row.get("file")}
    mandates = sorted({key for row in mine if row["id"] in units or row.get("file") in anchors
                       for key in _mandates_of(row.get("agent"))})
    facts, unaccounted, per_file, measures, per_mandate = [], [], {}, {}, {}
    if change is not None:
        for rel in [p for p in change if p in subject.allowed and not is_test_path(p)]:
            reasons = per_file.setdefault(rel, [])
            was, now = _text(before.get(rel)), _text(after.get(rel))
            pre_lines, post_lines = was.splitlines(), now.splitlines()
            markdown = rel.lower().endswith(".md")
            measures[rel] = change_measure(pre_lines, post_lines, markdown)
            added = [line[1:] for line in difflib.unified_diff(pre_lines, post_lines, lineterm="", n=0)
                     if line.startswith("+") and not line.startswith("+++")]
            if markdown:
                numbered = {f.line for f in extract_numbers.flagged("plan.md", "\n".join(added) + "\n")}
                found = [line.strip() for i, line in enumerate(added, 1) if i in numbered or _FACT.search(line)]
                if found:
                    facts += found
                    reasons.append("grounding")
            local = [row for row in mine if file_of[row["id"]] == rel]
            news = [_flat(row["new"]) for row in local if row["id"] in taken
                    and sources.get(row["id"]) in ("finding", "adapted")
                    and isinstance(row.get("new"), str) and row["new"].strip()]
            for row in local:
                if row["id"] not in taken or sources.get(row["id"]) not in ("finding", "adapted"):
                    continue
                if row.get("action") == "PLAN" and row.get("options"):
                    news.append(_flat(str(row["options"][0]).replace("(Recommended)", "")))
                if sources.get(row["id"]) == "adapted" and row.get("old"):
                    for tag, a, z, b, y in difflib.SequenceMatcher(None, pre_lines, post_lines,
                                                                autojunk=False).get_opcodes():
                        if tag == "replace" and z - a == y - b:
                            for old_line, new_line in zip(pre_lines[a:z], post_lines[b:y]):
                                if _flat(row["old"]) in _flat(old_line):
                                    news.append(_flat(new_line))
            lost = _unaccounted(added, news, was)
            per_mandate[rel] = sorted({key for row in local if row["id"] in units or lost
                                      for key in _mandates_of(row.get("agent"))})
            if lost:
                unaccounted += lost
                reasons.append("unaccounted")
            for i, rel_of in file_of.items():
                if rel_of == rel:
                    if i in taken and sources.get(i) in ("fold", "adapted", "ruling"):
                        reasons.append(f"{sources[i]}:{i}")
                    if i in failed:
                        reasons.append(f"check:{i}")
                    if i in notest_code:
                        reasons.append(f"noTest:{i}")
    delta = [d for d in digest.get("designDelta") or [] if isinstance(d, str) and d.strip()]
    for element in delta:
        named = [rel for rel in subject.allowed if element.startswith(rel + ":")]
        for rel in named or per_file:
            if rel in per_file and "seam" not in per_file[rel]:
                per_file[rel].append("seam")
    seats = []
    if units:
        seats.append({"role": "re-review", "units": units, "mandates": mandates})
    if facts:
        seats.append({"role": "grounding", "lines": facts})
    if delta:
        seats.append({"role": "coherence", "units": delta})
    totals = None
    if change is not None:
        keys = ("lines", "changedLines", "units", "unplacedLines", "unitsReached")
        totals = {key: sum(m.get(key, 0) for m in measures.values()) for key in keys}
        totals["files"] = measures
    return {"subject": subject.describe(), "seats": seats, "change": totals, "unaccounted": unaccounted,
            "fileUnits": {rel: reasons for rel, reasons in per_file.items() if reasons},
            "fileMandates": per_mandate,
            "changedFiles": sorted(p for p in change or [] if p in subject.allowed),
            "inline": not seats and bool(per_file), "unknown": change is None}


# ---- run ------------------------------------------------------------------------------------------


def _expanded_job(job, views):
    """Project a grouped disposition to originals; never project the original evidence or edit."""
    digest = job.get('digestValue')
    aliases = {row['id']: _sources(row) for row in views}
    copy = dict(job, viewIds={source: view for view, sources in aliases.items() for source in sources})
    if not isinstance(digest, dict):
        return copy
    expanded = dict(digest)
    for key in ('applied', 'deferred'):
        entries = digest.get(key)
        if isinstance(entries, list):
            expanded[key] = []
            for entry in entries:
                if isinstance(entry, dict):
                    expanded[key].extend(dict(entry, id=source)
                                         for source in aliases.get(entry.get('id'), [entry.get('id')]))
                else:
                    expanded[key].append(entry)
    if isinstance(digest.get('commits'), list):
        expanded['commits'] = [dict(entry, ids=[source for key in entry.get('ids', [])
                                               for source in aliases.get(key, [key])])
                               if isinstance(entry, dict) else entry for entry in digest['commits']]
    copy['digestValue'] = expanded
    copy['ruled'] = list(job.get('ruled') or [])
    return copy


def run(findings, jobs, out, *, independent=None, original=None):
    subjects = [job["subject"] for job in jobs]
    views = (load_rows(findings) if findings is not None else
             _validated([row for job in jobs for row in job['rows']], 'fold prompts'))
    grouped = any(_sources(row) != [row['id']] for row in views)
    rows = original if original is not None else original_rows(findings) if findings is not None and grouped else views
    by_source = {}
    failures = []
    known = {row['id'] for row in rows}
    for view in views:
        for source in _sources(view):
            if source in by_source or source not in known:
                failures.append(scoped_failure('fold-id-coverage', f'view {view["id"]}: repeated or unknown original {source}', ids=[source]))
            by_source[source] = view['id']
    routed, unrouted, ambiguous = route(rows, subjects)
    filed = {row["id"] for row in rows if row.get("file")}
    outside = [i for i in unrouted if i in filed]
    missing = sorted(known - set(by_source) - set(outside))
    if missing:
        failures.append(scoped_failure('fold-routing', f'originals absent from routed view: {missing}', ids=missing))
    if original is None and findings is None and any(_sources(row) != [row['id']] for row in views):
        raise Unusable('grouped fold prompts lack independently loaded original findings')
    for job in jobs:
        if 'digestValue' not in job:
            value = _read_json(job['digest'], 'digest')
            job['digestValue'] = value.get('digest') if isinstance(value, dict) and isinstance(value.get('digest'), dict) else value
    jobs = [_expanded_job(job, job.get('rows') or views) for job in jobs]
    aliases = {view['id']: _sources(view) for view in views}
    for job in jobs:
        job['ruled'] = [source for key in job.get('ruled') or []
                        for source in ([key] if key in known else aliases.get(key, [key]))]
    # Host receipts over a consolidated row cannot verify its independently falsifiable members.
    if any(by_source.get(row['id'], row['id']) != row['id'] for row in rows):
        independent = None
    independent, verdicts_path = independent if independent is not None else independent_verdicts(rows, subjects, out)
    if set(independent) != {row['id'] for row in rows if row.get('evidence')}:
        raise Unusable('host independent verdicts do not cover this fold')
    unrouted = [i for i in unrouted if i not in filed]
    if unrouted or ambiguous:
        failures.append(scoped_failure("fold-routing", f"no subject file: {unrouted or '-'}; "
                                       f"several: {ambiguous or '-'}", ids=unrouted + ambiguous))
    state = {i: ("unrouted", None) for i in unrouted}
    state.update({i: ("ambiguous", None) for i in ambiguous})
    state.update({i: ("out-of-subject", None) for i in outside})
    rounds = []
    for index, job in enumerate(jobs):
        mine = [row for row in rows if routed.get(row["id"]) == index]
        state.update(check_job(job, mine, independent, failures))
        rounds.append(next_round(job, mine, failures))
    dispositions = []
    entries = {entry['id']: entry for job in jobs if isinstance(job.get('digestValue'), dict)
               for entry in job['digestValue'].get('applied') or []
               if isinstance(entry, dict) and isinstance(entry.get('id'), str)}
    for row in rows:
        disposition, taken = state[row["id"]]
        subject = subjects[routed[row["id"]]] if row["id"] in routed else None
        dispositions.append({"id": row['id'], "viewId": by_source.get(row['id']),
                             "subject": subject.root_text if subject else None,
                             "file": subject.application_file(row, entries.get(row['id'], {})) if subject else None,
                             "disposition": disposition, "taken": taken,
                             "verdict": independent.get(row['id'])})
    return {"status": "fail" if failures else "pass", "failures": failures,
            "dispositions": dispositions, "nextRound": rounds,
            "independentVerdicts": [{"id": i, "verdict": v} for i, v in independent.items()],
            "independentVerdictPath": verdicts_path,
            "jobs": [dict(job["subject"].describe(), **({"agentId": job["agentId"]} if "agentId" in job else {}))
                     for job in jobs]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check a review fold against disk and independent verdicts.")
    ap.add_argument("--findings")
    ap.add_argument("--workflow-dir")
    ap.add_argument("--subject", action="append", default=[],
                    help="<root>::<snapshot manifest>::<file>[,<file>...][::<new path>[,<new path>...]]")
    ap.add_argument("--digest", action="append", default=[])
    ap.add_argument('--ruled', action='append', default=[], help='caller-authorized finding ids, comma-separated')
    ap.add_argument("--measure", nargs=2, metavar=("REVIEWED", "CURRENT"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.measure:
        try:
            pre, post = (_abs(p).read_text(encoding="utf-8").splitlines() for p in args.measure)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"fold_check: --measure: {exc}", file=sys.stderr)
            return 2
        Path(args.out).write_text(json.dumps(change_measure(pre, post, markdown=args.measure[1].lower().endswith(".md")), indent=2) + "\n", encoding="utf-8",
                                  newline="\n")
        print(f"fold_check: change measure -> {args.out}")
        return 0
    if args.workflow_dir:
        if args.findings or args.subject or args.digest:
            print("fold_check: --workflow-dir derives findings, subjects and digests; pass nothing else",
                  file=sys.stderr)
            return 2
        try:
            jobs = workflow_jobs(args.workflow_dir)
            for job in jobs:
                job['ruled'] = [i for value in args.ruled for i in value.split(',') if i]
            import post_check
            original, archive = post_check._archive_rows(args.workflow_dir, merge=False)
            report = run(None, jobs, args.out, original=original)
            if archive['status'] != 'completed':
                report['failures'].append(scoped_failure('source-incomplete', f"workflow coverage is {archive['status']}"))
                report['status'] = 'fail'
        except Unusable as exc:
            print(f"fold_check: {exc}", file=sys.stderr)
            return 2
        return _emit(report, args.out)
    if not args.findings:
        print("fold_check: pass --findings with --subject/--digest, or --workflow-dir", file=sys.stderr)
        return 2
    if not args.subject or len(args.subject) != len(args.digest):
        print("fold_check: each --subject needs one --digest, in order", file=sys.stderr)
        return 2
    try:
        subjects = [parse_subject_spec(spec) for spec in args.subject]
        claimed = [(path_key(item, s.root), item) for s in subjects for item in s.files]
        if len({key for key, _ in claimed}) != len(claimed):
            print("fold_check: a subject file repeats", file=sys.stderr)
            return 2
        jobs = [{"subject": s, "digest": d,
                 'ruled': [i for value in args.ruled for i in value.split(',') if i]}
                for s, d in zip(subjects, args.digest)]
        report = run(args.findings, jobs, args.out)
    except Unusable as exc:
        print(f"fold_check: {exc}", file=sys.stderr)
        return 2
    return _emit(report, args.out)


def _emit(report, out):
    Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
    for failure in report["failures"]:
        print(f"fold_check: {failure['kind']}: {failure['detail']}", file=sys.stderr)
    print(f"fold_check: {report['status']} ({len(report['failures'])} failure(s)) -> {out}")
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
