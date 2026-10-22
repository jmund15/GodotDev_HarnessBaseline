#!/usr/bin/env python3
"""The review ledger: who reviewed what, which (file, mandate) pairs still need review, and PR review scope.

Usage:
    review_ledger.py scope <N> [--repo <path>] [--base-ref <ref>]
    review_ledger.py project <N> --scope-json <file> --out <body.md>
                                 [--date YYYY-MM-DD] [--waive] [--repo <path>] [--base-ref <ref>]
    review_ledger.py plan --stage <stage> --subject <subject> --roster <roster.json>
                          [--files <path>... | --files-from <file>] [--scope-json <file>] [--ignore-ledger]
                          [--design <path> [--no-new-decision]] [--base-ref <ref>]
    review_ledger.py record --stage <stage> --subject <subject>
                            (--run <run id> | --seats-file <seats.json> --workflow-dir <live dir>
                             | --sidecar-result <prepared.json> | --result <saved return>
                             | --script <name> --outcome <o> [--mandate <key>]
                             | --inline <command> --outcome <o> --mandate <key>)
                            [--files <path>... | --files-from <file>] [--scope-json <file>] [--head <commit>] [--blobs <map.json>] [--from <map.json>]
                            [--roster <roster.json>] [--premise-commits <commits.json>]
                            [--author-model <id>] [--task <id>] [--degraded] [--no-new-decision]
                            [--escaped <finding id>=<sha>[:<mandate key>]...] [--base-ref <ref>]
    review_ledger.py uncovered (--files <path>... | --files-from <file>) --mandates <keys | candidate> [--roster <roster.json>]
                               [--base-ref <ref>]
    review_ledger.py owed [--roster <roster.json>] [--base-ref <ref>] [--mandates <keys | candidate>] [--doctrine]
    review_ledger.py whole-scope --subject <s> (--files <path>... | --files-from <file> | --scope-json <file>)
                                [--mandates <keys | candidate>] [--roster <roster.json>] [--base-ref <ref>]
    review_ledger.py rows (--subject <s> | --task <id> | --model <id>)... [--json | --units]
    review_ledger.py keys [--check] [--stage candidate]

Stages: candidate, pr-review, pre-merge, pr-ready, design, plan-check. Subjects: `session:<id>`,
`pr:<N>`, `plan:<path>`, `doc:<path>`.

A ledger row is one JSON line per (seat, mandate): the check, the hash of the rule text it applied, the
files at their content hash (or `deleted:<deleting commit>` tombstone), the seat, and the outcome. `record`
writes a tombstone only for a named subject path absent at the reviewed head with a committed deletion;
uncommitted removals and never-created paths are not recorded. Panel engagement still applies to deletions.
`premise_commits` holds only the frozen full commit IDs supplied through `record --premise-commits`,
bound to the explicit `--head` and optional `--blobs` manifest. Missing inputs cover no premises.
Final text reuse never covers new doctrine commits' premises; `uncovered_premises` checks that duty
with the same row clauses and roster depth, independently of content hashes.
A row covers mandate `m` on file `f` only when
every clause of `CLAUSES` holds. Clause 9 (standing) is the one clause that reads another row: a panel row whose model
holds the row's tier in registry `supplementary` covers a pair only when another panel row, whose model is qualified at
its own tier, holds the same pair under clauses 1-8, with depth judged without the seat's load. `uncovered` prints the tab-separated `<file>\\t<mandate>` pairs no row
covers, and `plan` drops from a roster the mandates every file already has and names each kept seat's
`uncoveredFiles`, the files where one of its kept mandates is uncovered. What it returns is a ceiling, never a width: a later round sizes its seats per `orchestration` §2 *Later rounds*.
When every owed `(mandate, file)` pair of the kept seats has an earlier reviewed blob (the `after:<blob>` test below), `plan`
keeps the seats unchanged and reports `laterRound: true` with each seat's `earlierReviewedBlobs`
(mandate, file and blob). The session chooses the later round's width. `--ignore-ledger` omits these
facts; so does a round where any owed pair has no earlier blob. Clause 6 (depth) judges a panel row
against the roster seat that would carry the mandate, so `uncovered` applies it per mandate from `--roster`
(a mandate two seats carry is covered only when both seats' depth holds). Without a roster, or for a mandate no
roster seat carries, depth cannot be checked: a pair that only panel rows cover prints with a third field,
`<file>\\t<mandate>\\tdepth-unchecked`, never as covered. A pair whose newest row passing every clause
except content (clause 3) reviewed an earlier blob prints `<file>\\t<mandate>\\tafter:<blob>`, clause 6 judged
against `--roster` and passing without one: a later round, sized from that blob (`Cover.earlier_blob`). A
`--files` path with no file in the worktree prints no pair and one `absent, nothing to review: <path>` line on
stderr. A corrupt ledger line is skipped and counted on stderr, and never covers anything. `--mandates candidate`
resolves through `resolve_candidate`, which `owed` and `owed_staged` share: without `--roster` it is the
unconditional set, the mandates of the `/session_audit` lenses whose `admission:` labels lack `conditional`;
with one, the roster's mandates that `keys --stage candidate` lists, plus the unconditional set.
HARNESS_REVIEW_CANDIDATE_KEYS, when set, names both sets. Clause 5 reads the checkout's escape log and
the shared overlay beside the ledger (`escape_overlay_path`), so revocation reaches every worktree coverage
does. Either file existing and failing to parse (unreadable, or any corrupt line) fails clause 5 for every
row, so nothing covers until it is repaired.

A row's `from` map derives each recorded path from a covered predecessor blob, or null for a
policy root. `Cover.resolve` checks every link, including escapes and root depth; derived panel
links compare tier only. `record --inline fold` requires an exact `--from` map. Panel recording
with a roster derives each path from its newest covered other blob. Recorded blobs are written
to the repository object database.

`record` writes rows only for review_mandates keys: a `--mandate` outside the catalog, or a `--script` name
that is not a key when no `--mandate` is given, exits 2 naming it. A panel row's outcome is the engine's
per-mandate token as returned (a token outside the outcomes exits 2); a sidecar seat's comes from its typed
`checked.mandates` map, else the prepared coverage, bounded by its status and its `capabilityPartial` fact
(`_sidecar_seats`): a partial seat covers nothing unless it is partial only because of capability gaps. A `clean` or
`findings` mandate covers only the subject files its seat engaged, and every other subject file goes to an
`#unengaged` `incomplete` row with count 0 (`_seat_rows`). A seat engaged a file when a `checked` target names
exactly that repo-relative path (a directory target engages nothing beneath it), none of its gap strings
names the file's basename, and, on an engine `--run` whose seat transcript exists, a successful Read, Grep,
Glob, Bash or PowerShell call in that transcript names it (`review_result.called_paths`). A seat also engaged
each must-read file whose every line it read, and each file whose `diff --git` section in one of its must-read
diffs lies inside the lines it read of that diff (`_must_read_engaged`), unless a gap names the file. A diff taken inside a submodule names files relative to it; `_diff_subject` maps them to the reviewed `<gitlink>/<file>` path. A path under a submodule hashes from that submodule's object store (`Ctx._batch_check`, `owning_repo`). `_engaged` owns that rule. The `--result` and `--sidecar-result` routes, a `dispatch.js`
`--run` and an engine seat with no transcript have no observed calls, so the seat's own targets stand: that
is the residual. On `--run`, a seat with a `mustRead` list and a transcript has its must-read coverage judged
from the transcript's Read ranges (`review_result.called_reads`), and an entry they leave unread fails the seat
(`_engine_seats`, `_dispatch_review`); without a transcript its own `checked.toolsUsed` ranges stand. Each
row's `mustReadEvidence` is `observed`, `self-reported`, or null with no `mustRead`. A bounded return records from
`delivery.coverage.seats` when that holds every seat. `--run` reads the run's journal: an engine run whose
return shed `sources` rebuilds them from each seat's journaled result, and a `dispatch.js` run becomes one
seat per job that names its `seat`, its spill file or journaled deliverable normalized by
`review_result.ingest` as a sidecar's is (`_dispatch_review`). `--escaped <id>=<sha>` writes the finding's
mandate when its seat names exactly one catalog key; `:<mandate key>` names it otherwise. Each panel row
carries `findingFiles`, the sorted repo paths its seat's source findings cite, or null when no source names a
seat that counted findings (a model-written `agent` tag never stands in). On stage `pre-merge`, and on
`pr-review` with a `pr:` subject, it also carries `seatLedgerNew`: how many of the seat's findings cite a path
no earlier ancestor-head panel `findings` row sharing a seat mandate reported, or null when its own
`findingFiles` is null, such a row has no `findingFiles` or the ledger cannot be read (`_ledger_new`). A sidecar seat's `mandates` may name lens keys:
each expands through `lens.seat_mandates` to the catalog keys it declares, as the engine's does.

`--seats-file` records a running panel from its launched label-to-seat map, live journal and agents'
`.meta.json` models, with transcript Read ranges when present. It needs `--workflow-dir`, not a host run
record. `--run` keeps the completed-run path.

`whole-scope` reports whether one candidate panel recording (`call`, at one head) for the exact subject
covers every requested current file/mandate at the supplied roster depth. It never unions task units or
separate recordings, and absent files remain uncovered. No roster means panel depth is unchecked, so the
answer is false. Rows without a recording identity establish no whole-scope fact. `rows --units` is a
diagnostic grouping, not evidence of a whole-scope review.

`rows --units` lists one line per review unit, `<unit>\\t<file>,<file>...`, over the picked rows with
reviewer `panel` and stage `candidate`: `task:<id>` for rows carrying a task, else `run:<id prefix>`, each with
the sorted union of its rows' files that still exist in the worktree; a unit left with none is omitted. `keys --check` also cross-checks every `/session_audit` lens's
`admission: conditional` label against the `execution_depth.md` §Mandate admission rows naming it
(`check_admission`), and says on stderr when that doc is absent.

`owed` lists each unpushed commit (`@{u}..HEAD`; `<base-ref>..HEAD` with `(no upstream)` on its line when the
branch has no upstream) that has a file with an uncovered candidate pair, and exits 2 when git cannot list
or resolve those commits' files. A merge commit counts only the files it resolved differently from every parent
(`git log --cc`). A commit that moves a gitlink counts the files its submodule changed between the two pins
(`<gitlink>/<file>`, a deletion tombstoned by the submodule commit that deleted it), never the gitlink; a pin
whose submodule history is not local raises `LedgerUnreadable`.
`--mandates` selects catalog keys (default `candidate`); `--doctrine` uses the shared
markdown predicate and judges each path's final range blob, attributed to its last changing commit.
`owed_range` shares that logic with the push guard, whose `exclude_remotes` skips remote ancestors;
the CLI never excludes them. Unreadable ledger/escape state raises `LedgerUnreadable`.
It applies clause 6 as `uncovered` does: without `--roster`, a commit whose only owed pairs
are covered by panel rows alone is listed with `(depth-unchecked: no --roster)`, never as covered. `plan` adds `crossFamily` for the
pre-merge stages: the families the covering rows used and the registry rows that can seat a second opinion.

`scope` and `project` are the PR review scope of the retired `pr_review_record.py`: `scope` prints the PR's
review subject, `project` writes the PR body with the round appended to its `## Review record` section and
never edits the PR. The subject's `files` is always every file the PR changes against its merge-base, and
`plan` prunes it to the uncovered pairs. In `delta` mode, `range`, `own_commits`, `merge_resolutions`,
`gitlinks`, `delta_files` and `lines` present only what changed since the last reviewed head and never narrow
`files`. `scope` takes the last reviewed head from the newest `pr:<N>` ledger head whose round is complete,
else from the newest `<!-- pr-review: ... -->` record in the PR body. `project --waive` records the owner's word
that the head was reviewed outside the ledger (`scope=waived`); a waiver at or after the ledger's head is the
last reviewed head, `scope` reports `waived: true`, and `plan --scope-json` then plans only `delta_files`, no
seat at all when nothing changed since the waiver.

The ledger is `<primary checkout>/.claude/logs/review_ledger.jsonl`, appended through
`_hook_state.append_jsonl_rotating`; the newest `KEEP_LINES` rows survive a rotation past `MAX_BYTES`, and a
dropped row re-runs a review, never skips one. `record` exits 1 naming the ledger path when the append
fails. The mandate catalog and `review_scope` are read through `tools/adaptation.py`, so a wrong-typed table reads
as empty with adaptation's warning: no row then covers and `record` refuses every mandate. Proof overrides:
HARNESS_REVIEW_LEDGER, HARNESS_REVIEW_CLAUDE_DIR (mandate catalog and rule files),
HARNESS_ESCAPE_LOG, HARNESS_ESCAPE_LOG_TOOL, HARNESS_REVIEW_SESSIONS (workflow journal root),
HARNESS_REVIEW_CANDIDATE_KEYS (comma list).

Exit status is 2 for a usage error and for any state this tool cannot scope, 1 when a write fails or a
run's per-seat rows are not all available, else 0.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import NamedTuple

TOOLS_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = TOOLS_DIR.parent

RECORD_HEADING = "## Review record"
FENCE = "```"
SCOPE_FIELDS = ("pr", "mode", "head", "base", "range", "last_head", "own_commits")
STAGES = ("candidate", "pr-review", "pre-merge", "pr-ready", "design", "plan-check")
PRE_MERGE_STAGES = ("pre-merge", "pr-review")
REVIEW_REGISTRIES = ("review", "session_audit", "plan_check")
SUBJECT_KINDS = ("session", "pr", "plan", "doc")
OUTCOMES = ("findings", "clean", "not-reached", "incomplete", "blocked", "failed")
COVERING_OUTCOMES = ("findings", "clean")
EFFORT_ORDER = ("none", "low", "medium", "high", "xhigh", "max", "ultra")
# Sized for about 600 median-PR reviews before a rotation (sizing evidence: this comment as commit acb99030d
# wrote it); a review that outgrows the cap costs a re-run, never a skipped review.
MAX_BYTES = 16 * 1024 * 1024
KEEP_LINES = 1000

USAGE = __doc__

_RECORD_RE = re.compile(
    r"^<!-- pr-review: head=([0-9a-f]{40}) base=([0-9a-f]{40}) "
    r"scope=(full|delta|waived) range=(\S+) date=(\d{4}-\d{2}-\d{2}) -->$"
)
_FINDING_LOCATION = re.compile(r"^(.+):([1-9]\d*)$")


class ToolError(Exception):
    """A condition reported on stderr; `code` is the exit status."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _import(name: str, folder: Path = TOOLS_DIR):
    """Module `name` from `folder`, which is on `sys.path` for the import only."""
    sys.path.insert(0, str(folder))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(folder))


def _run(cmd, cwd: Path, shell: bool = False, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), shell=shell, input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        raise ToolError(f"{cmd if isinstance(cmd, str) else ' '.join(cmd)} failed: {exc}") from exc


def _git(repo: Path, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return _run(["git", *args], repo, stdin=stdin)


def _git_text(repo: Path, args: list[str]) -> str:
    result = _git(repo, args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"git {' '.join(args)} failed" + ((": " + detail) if detail else ""))
    return result.stdout.decode("utf-8", errors="replace")


def _git_lines(repo: Path, args: list[str]) -> list[str]:
    return [line for line in _git_text(repo, args).splitlines() if line.strip()]


def submodule_paths(top: Path) -> list[str]:
    """The gitlink paths (mode 160000) in `top`'s index, longest first."""
    lines = _git_text(top, ["ls-files", "--stage"]).splitlines() if (top / ".git").exists() else []
    found = [line.split("\t", 1)[1] for line in lines if line.startswith("160000 ") and "\t" in line]
    return sorted(found, key=len, reverse=True)


def split_submodule(top: Path, path: str):
    """`(gitlink path, path inside it)` when `path` lies under a submodule of `top`, else None."""
    for sub in submodule_paths(top):
        if path.startswith(sub + "/"):
            return sub, path[len(sub) + 1:]
    return None


def owning_repo(top: Path, path: str) -> tuple[Path, str]:
    """`(checkout, path inside it)` for the deepest submodule checkout that holds `path`, else `(top, path)`."""
    found = split_submodule(top, path)
    return owning_repo(top / found[0], found[1]) if found else (top, path)


def readable_submodule(top: Path, link: str, pins) -> Path:
    """The checkout of submodule `link` when it holds every commit in `pins`; raises `LedgerUnreadable` otherwise,
    so a pointer whose history is not local owes review instead of nothing."""
    inside = top / link
    if not (inside / ".git").exists() or any(
            _git(inside, ["cat-file", "-e", pin + "^{commit}"]).returncode for pin in pins):
        raise LedgerUnreadable(f"cannot read submodule {link} at {', '.join(pins)}: run `git submodule update "
                               f"--init {link}` and fetch it so the pinned commits are local")
    return inside


def gitlink_files(top: Path, link: str, old: str, new: str) -> set:
    """`<link>/<file>` for each file submodule `link` changed between pins `old` and `new`, filtered as
    `range_commits` filters; an all-zero pin is absent, so every file the other pin holds changed."""
    pins = [pin for pin in (old, new) if pin.strip("0")]
    inside = readable_submodule(top, link, pins)
    args = (["diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACDMR", old, new] if len(pins) == 2
            else ["ls-tree", "-r", "--name-only", "-z", pins[0]])
    return {f"{link}/{name}" for name in _git_text(inside, args).split("\0") if name}


def blob_at(top: Path, revision: str, path: str):
    """The blob sha of `path` at `revision` in `top`, or None when absent. A path under a submodule is read from
    that submodule's object store at the commit the gitlink pins at `revision`, since `revision:path` stops at
    the gitlink."""
    found = split_submodule(top, path)
    if found:
        pinned = _git(top, ["rev-parse", "--verify", "--quiet", f"{revision}:{found[0]}"])
        commit = pinned.stdout.decode().strip() if pinned.returncode == 0 else ""
        return blob_at(top / found[0], commit, found[1]) if commit else None
    done = _git(top, ["rev-parse", "--verify", "--quiet", f"{revision}:{path}"])
    return done.stdout.decode().strip() if done.returncode == 0 else None


def _resolve_gh() -> str:
    found = shutil.which("gh")
    if not found:
        raise ToolError("gh is not on PATH")
    return found


def _gh(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    exe = _resolve_gh()
    cmd = [exe, *args]
    use_shell = os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))
    if use_shell:
        return _run(subprocess.list2cmdline(cmd), repo, shell=True)
    return _run(cmd, repo)


def _gh_json(repo: Path, args: list[str]) -> dict:
    result = _gh(repo, args)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"gh {' '.join(args)} failed" + ((": " + detail) if detail else ""))
    try:
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ToolError(f"gh {' '.join(args)} returned no JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"gh {' '.join(args)} returned {type(data).__name__}, not an object")
    return data


def _claude_dir() -> Path:
    return Path(os.environ.get("HARNESS_REVIEW_CLAUDE_DIR") or CLAUDE_DIR)


def _adaptation(key: str, claude_dir: Path | None = None) -> dict:
    return _import("adaptation").get(claude_dir or _claude_dir(), key)


def load_catalog(claude_dir: Path | None = None) -> dict:
    return _adaptation("review_mandates", claude_dir)


# ---- PR scope (the retired pr_review_record.py's computation) -------------------------------------------


def _line_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    pos = 0
    while pos < len(text):
        newline = text.find("\n", pos)
        end = len(text) if newline == -1 else newline
        content_end = end
        if content_end > pos and text[content_end - 1] == "\r":
            content_end -= 1
        spans.append((pos, content_end))
        if newline == -1:
            break
        pos = newline + 1
    return spans


def _outside_fence_lines(text: str) -> list[tuple[int, str]]:
    out = []
    in_fence = False
    for start, content_end in _line_spans(text):
        content = text[start:content_end]
        if content.lstrip().startswith(FENCE):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((start, content))
    return out


def find_record(body: str):
    """(record, fallback reason) for the newest `pr-review` line outside fences."""
    candidates = [
        content.strip() for _, content in _outside_fence_lines(body)
        if content.strip().startswith("<!--") and "pr-review:" in content.strip()
    ]
    if not candidates:
        return None, "no record"
    match = _RECORD_RE.match(candidates[-1])
    if not match:
        return None, "unparseable record"
    head, base, scope, rng, date = match.groups()
    return {"head": head, "base": base, "scope": scope, "range": rng, "date": date}, None


def is_excluded(path: str, scope_config: dict) -> bool:
    normalized = path.replace("\\", "/")
    for directory in scope_config.get("exclude_dirs") or []:
        if normalized == directory or normalized.startswith(directory + "/"):
            return True
    return normalized.rsplit("/", 1)[-1].endswith(tuple(scope_config.get("exclude_suffixes") or ()))


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def _commit_present(repo: Path, sha: str) -> bool:
    return _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def _ensure_head(repo: Path, head: str) -> None:
    if _commit_present(repo, head):
        return
    _git(repo, ["fetch", "origin", head])
    if not _commit_present(repo, head):
        raise ToolError(f"head commit {head} is not available locally")


def _resolve_mode(repo: Path, head: str, record):
    if record is None:
        return None
    last = record["head"]
    if last == head:
        return "delta", last, "no new commits"
    if _is_ancestor(repo, last, head):
        return "delta", last, "recorded head is an ancestor"
    return "full", None, "recorded head not an ancestor (rebase or force-push)"


def _count(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _numstat_rows(text: str) -> list[tuple[str, int]]:
    rows = []
    for row in text.splitlines():
        fields = row.split("\t")
        if len(fields) < 3:
            continue
        rows.append(("\t".join(fields[2:]), _count(fields[0]) + _count(fields[1])))
    return rows


def _diff_path(token: str):
    if token == "/dev/null":
        return None
    if len(token) > 1 and token.startswith('"') and token.endswith('"'):
        token = token[1:-1]
    for prefix in ("a/", "b/"):
        if token.startswith(prefix):
            return token[len(prefix):]
    return token


def _remerge_rows(text: str) -> list[tuple[str, int]]:
    rows = []
    path = None
    count = 0
    for row in text.splitlines():
        if row.startswith("diff --git "):
            if path is not None:
                rows.append((path, count))
            path, count = None, 0
        elif row.startswith("+++ "):
            candidate = _diff_path(row[4:].split("\t")[0])
            if candidate:
                path = candidate
        elif row.startswith("--- "):
            candidate = _diff_path(row[4:].split("\t")[0])
            if candidate and path is None:
                path = candidate
        elif path is not None and (row.startswith("+") or row.startswith("-")):
            count += 1
    if path is not None:
        rows.append((path, count))
    return rows


def _changed(repo: Path, commits: list[str], merges: list[str]) -> dict:
    counts: dict[str, int] = {}
    for sha in commits:
        text = _git_text(repo, ["show", "--numstat", "--format=", "--no-renames", sha])
        for path, count in _numstat_rows(text):
            counts[path] = counts.get(path, 0) + count
    for sha in merges:
        text = _git_text(repo, ["show", "--remerge-diff", "--format=", sha])
        for path, count in _remerge_rows(text):
            counts[path] = counts.get(path, 0) + count
    return counts


def _conflict_resolutions(repo: Path, first: str, second: str) -> list[str]:
    merges = _git_lines(repo, ["rev-list", "--merges", f"{first}..{second}"])
    return [
        sha for sha in merges
        if _git_text(repo, ["show", "--remerge-diff", "--format=", sha]).strip()
    ]


def _gitlinks(repo: Path, sha: str) -> dict:
    result = _git(repo, ["ls-tree", "-r", sha])
    if result.returncode != 0:
        return {}
    links = {}
    for row in result.stdout.decode("utf-8", errors="replace").splitlines():
        meta, _, path = row.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[0] == "160000":
            links[path] = fields[2]
    return links


def _ledger_last_head(rows: list, pr: int):
    """The newest head of the PR whose pre-merge round is complete: it holds a covering row and every mandate
    recorded at it has one. A seat that failed or was not reached at a head leaves that head out, so a retry
    scopes what the failed seat never saw."""
    wanted = f"pr:{pr}"
    rounds: dict = {}
    for index, row in enumerate(rows):
        if row.get("subject") != wanted or row.get("stage") not in PRE_MERGE_STAGES or not row.get("head"):
            continue
        mandates = rounds.setdefault(row["head"], {"last": index, "covered": {}})
        mandates["last"] = index
        key = row.get("mandate") or row.get("id")
        mandates["covered"][key] = mandates["covered"].get(key, False) or row.get("outcome") in COVERING_OUTCOMES
    complete = [(info["last"], head) for head, info in rounds.items() if all(info["covered"].values())]
    return {"head": max(complete)[1]} if complete else None


def build_scope(repo: Path, base_ref: str, pr: int) -> dict:
    scope_config = _adaptation("review_scope")
    view = _gh_json(repo, ["pr", "view", str(pr), "--json", "state,headRefOid,body"])
    state = view.get("state")
    if state != "OPEN":
        raise ToolError(f"PR #{pr} is {state}; scoping a review needs an open PR")
    head = view.get("headRefOid")
    _ensure_head(repo, head)
    base = _git_text(repo, ["merge-base", base_ref, head]).strip()

    rows, _, _ = read_rows(ledger_path(repo))
    record = _ledger_last_head(rows, pr)
    source = "ledger" if record else None
    body_record, fallback = find_record(view.get("body") or "")
    if record is None:
        record = body_record
        source = "pr-body" if record else "none"
    elif _waiver_supersedes(repo, record, body_record):
        record, source = body_record, "pr-body"
    waived = bool(record and record.get("scope") == "waived")
    resolved = _resolve_mode(repo, head, record)
    if resolved is None:
        mode, last_head, reason = "full", None, fallback or "no record"
    else:
        mode, last_head, reason = resolved

    universe = dict(_numstat_rows(_git_text(repo, ["diff", "--numstat", "--no-renames", base, head])))
    if mode == "delta":
        own = _git_lines(repo, ["rev-list", "--reverse", "--no-merges",
                                f"{last_head}..{head}", f"^{base_ref}"])
        resolutions = _conflict_resolutions(repo, last_head, head)
        rng = f"{last_head}..{head}"
        counts = _changed(repo, own, resolutions)
    else:
        own = _git_lines(repo, ["rev-list", "--reverse", "--no-merges", f"{base}..{head}"])
        resolutions = []
        rng = f"{base}..{head}"
        counts = universe
    old_links = _gitlinks(repo, last_head if mode == "delta" else base)
    new_links = _gitlinks(repo, head)
    files = sorted(path for path in universe if not is_excluded(path, scope_config))
    return {
        "pr": pr,
        "mode": mode,
        "reason": reason,
        "last_head": last_head,
        "head": head,
        "base": base,
        "range": rng,
        "own_commits": own,
        "merge_resolutions": resolutions,
        "files": files,
        "delta_files": sorted(path for path in counts if not is_excluded(path, scope_config)),
        "lines": sum(count for path, count in counts.items() if not is_excluded(path, scope_config)),
        "excluded": sorted(path for path in set(universe) | set(counts) if is_excluded(path, scope_config)),
        "gitlinks": {path: {"old": old_links[path], "new": new_links[path]}
                     for path in sorted(new_links) if path in old_links},
        "source": source,
        "waived": waived,
        "covered": _pr_covered(repo, base_ref, pr, files, rows),
    }


def _waiver_supersedes(repo: Path, ledger_record: dict, body_record) -> bool:
    """An owner waiver at or after the ledger's last complete head is the newer word on the PR."""
    if not body_record or body_record.get("scope") != "waived":
        return False
    return _is_ancestor(repo, ledger_record["head"], body_record["head"])


def _pr_covered(repo: Path, base_ref: str, pr: int, files: list[str], rows: list) -> dict:
    """{mandate: [files]} the `pr:<N>` rows cover on the scope's files, depth not applied."""
    own = [row for row in rows if row.get("subject") == f"pr:{pr}"]
    if not own or not files:
        return {}
    try:
        catalog = load_catalog()
    except ToolError:
        return {}
    cover = Cover(Ctx(repo, base_ref), catalog, own, [], "pr")
    out = {}
    for mandate in sorted({row.get("mandate") for row in own} & set(catalog)):
        done = [f for f in files if cover.covering_row(mandate, f) is not None]
        if done:
            out[mandate] = done
    return out


def _review_section_end(text: str):
    lines = _outside_fence_lines(text)
    start = None
    for index, (_, content) in enumerate(lines):
        if content.strip() == RECORD_HEADING:
            start = index
            break
    if start is None:
        return None
    for offset, content in lines[start + 1:]:
        if content.strip().startswith("## "):
            return offset
    return len(text)


def _append_round(text: str, line1: str, line2: str) -> str:
    sep = "\r\n" if "\r\n" in text else "\n"
    offset = _review_section_end(text)
    if offset is None:
        return text + sep + sep + RECORD_HEADING + sep + sep + line1 + sep + line2 + sep
    insert = line1 + sep + line2 + sep
    if offset == len(text) and text and not text.endswith(("\n", "\r")):
        insert = sep + insert
    return text[:offset] + insert + text[offset:]


def _record_lines(scope: dict, date: str, waive: bool = False) -> tuple[str, str]:
    head = scope["head"]
    line1 = (f"<!-- pr-review: head={head} base={scope['base']} scope={'waived' if waive else scope['mode']} "
             f"range={scope['range']} date={date} -->")
    if waive:
        line2 = f"Owner waived review of `{head[:7]}` on {date}: reviewed outside the ledger"
    elif scope["mode"] == "delta":
        own = scope["own_commits"]
        line2 = (f"Reviewed `{head[:7]}` on {date}: delta ({len(own)} own commits "
                 f"since `{str(scope['last_head'])[:7]}`)")
    else:
        line2 = f"Reviewed `{head[:7]}` on {date}: full"
    return line1, line2


def _load_json(path: str, label: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{label} {path} is not a JSON object")
    return data


def load_files_from(path: str) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ToolError(f"cannot read --files-from {path}: {exc}") from exc
    return [line.strip() for line in text.splitlines() if line.strip()]


def _load_scope(path: str, pr: int | None = None) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"cannot read scope JSON {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ToolError(f"scope JSON {path} does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"scope JSON {path} is not an object")
    if pr is None:
        return data
    for field in SCOPE_FIELDS:
        if field not in data:
            raise ToolError(f"scope JSON {path} is missing field: {field}")
    if data["pr"] != pr:
        raise ToolError(f"scope JSON is for PR {data['pr']}, not PR {pr}")
    if data["mode"] not in ("full", "delta"):
        raise ToolError(f"scope JSON mode is {data['mode']!r}, not full or delta")
    return data


def write_record(repo: Path, pr: int, scope_path: str, out_path: str, date: str, waive: bool = False) -> None:
    view = _gh_json(repo, ["pr", "view", str(pr), "--json", "state,body"])
    state = view.get("state")
    if state != "OPEN":
        raise ToolError(f"PR #{pr} is {state}; recording a review needs an open PR")
    scope = _load_scope(scope_path, pr)
    line1, line2 = _record_lines(scope, date, waive)
    updated = _append_round(view.get("body") or "", line1, line2)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)


# ---- the mandate catalog and rule hashes ----------------------------------------------------------------


def _heading_matches(text: str, want: str) -> bool:
    text = text.strip()
    if text == want:
        return True
    return text.startswith(want) and not (text[len(want)].isalnum() or text[len(want)] in "-_")


def _section(text: str, heading: str) -> str | None:
    """The text from the heading line to the next heading of the same or higher level; None when absent."""
    lines = text.replace("\r\n", "\n").split("\n")
    in_fence = False
    start = level = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(FENCE):
            in_fence = not in_fence
            continue
        match = None if in_fence else re.match(r"^(#+)\s+(.*)$", line)
        if not match:
            continue
        if start is None:
            if _heading_matches(match.group(2), heading):
                start, level = index, len(match.group(1))
        elif len(match.group(1)) <= level:
            return "\n".join(lines[start:index]).rstrip("\n")
    return None if start is None else "\n".join(lines[start:]).rstrip("\n")


def _home_text(home: str, claude_dir: Path) -> str:
    rel, _, heading = home.partition("#")
    path = claude_dir / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"{home}: cannot read {rel}: {exc}") from exc
    if not heading:
        return text.replace("\r\n", "\n").rstrip("\n")
    section = _section(text, heading)
    if section is None:
        raise ToolError(f"{home}: no heading {heading!r} in {rel}")
    return section


def mandate_doctrine(claude_dir: Path | None = None) -> dict[str, list[str]]:
    """Each mandate key's doctrine files, sorted: every `preload:` of every lens that declares the key in the
    review registries under `claude_dir` (`lens.py`). A registry file that is absent declares nothing."""
    claude_dir = claude_dir or _claude_dir()
    lens = _import("lens")
    agents = claude_dir / "commands" / "agents"
    found: dict = {}
    for registry in REVIEW_REGISTRIES:
        if not (agents / lens.REGISTRIES[registry][0]).is_file():
            continue
        for item in lens.parse(registry, agents)[1]:
            for key in item.mandates:
                found.setdefault(key, set()).update(item.preloads)
    return {key: sorted(files) for key, files in sorted(found.items()) if files}


def rule_hash(key: str, claude_dir: Path | None = None, catalog: dict | None = None):
    """SHA-256 over the mandate's home section plus each file its lenses preload (`mandate_doctrine`); None for
    a key outside the catalog."""
    claude_dir = claude_dir or _claude_dir()
    spec = (catalog if catalog is not None else load_catalog(claude_dir)).get(key)
    if not isinstance(spec, dict):
        return None
    digest = hashlib.sha256(_home_text(spec["home"], claude_dir).encode("utf-8"))
    for rel in mandate_doctrine(claude_dir).get(key, []):
        try:
            digest.update(b"\0" + (claude_dir / rel).read_bytes())
        except OSError as exc:
            raise ToolError(f"{key}: cannot read preloaded file {rel}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def check_keys(catalog: dict, claude_dir: Path, lens_sets: dict) -> list[str]:
    """Problems with the catalog and with each registry's lens declarations; [] when sound."""
    problems = []
    for key, spec in sorted(catalog.items()):
        if isinstance(spec, dict) and "cites" in spec:
            problems.append(f"catalog key {key}: `cites` is retired; declare the file as a `preload:` of a lens "
                            f"that declares {key}")
        try:
            rule_hash(key, claude_dir, catalog)
        except ToolError as exc:
            problems.append(f"catalog key {key}: {exc}")
    for registry, lenses in sorted(lens_sets.items()):
        owners: dict = {}
        for lens, keys in lenses:
            if not keys:
                problems.append(f"{registry}: lens {lens} declares no mandates")
            for key in keys:
                if key not in catalog:
                    problems.append(f"{registry}: lens {lens} declares unknown mandate {key}")
                elif key in owners:
                    problems.append(f"{registry}: lenses {owners[key]} and {lens} both declare {key}")
                else:
                    owners[key] = lens
    return problems


ADMISSION_DOC = "skills/_brainstorm_shared/execution_depth.md"


def check_admission(lenses: list, claude_dir: Path):
    """Disagreements between each `/session_audit` lens's `admission:` labels and the `execution_depth.md`
    §Mandate admission rows whose command cell is `/session_audit` and whose mandate cell is one backticked key;
    None when that doc is absent. `lenses` are `_audit_lenses` tuples."""
    path = claude_dir / ADMISSION_DOC
    if not path.is_file():
        return None
    rows = set()
    for line in (_section(path.read_text(encoding="utf-8"), "Mandate admission") or "").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        match = re.fullmatch(r"`([^`\s]+)`", cells[1]) if line.lstrip().startswith("|") and len(cells) > 1 else None
        if cells[0] == "`/session_audit`" and match:
            rows.add(match.group(1))
    problems, keys = [], set()
    for key, _, labels, descriptor in lenses:
        keys.add(key)
        conditional = "conditional" in labels
        if conditional and key not in rows:
            problems.append(f"session_audit: lens {key} declares `admission: conditional` and {ADMISSION_DOC} "
                            f"§Mandate admission has no `/session_audit` row for it")
        if key in rows and not conditional:
            problems.append(f"session_audit: {ADMISSION_DOC} §Mandate admission admits lens {key}, whose header "
                            f"declares no `admission: conditional`")
        if "CONDITIONAL" in descriptor and not conditional:
            problems.append(f"session_audit: lens {key}'s heading says CONDITIONAL and its header declares no "
                            f"`admission: conditional`")
    for key in sorted(rows - keys):
        problems.append(f"session_audit: {ADMISSION_DOC} §Mandate admission names {key}, which is no "
                        f"session_audit lens")
    return problems


def _lens_sets(registries=REVIEW_REGISTRIES) -> dict:
    lens = _import("lens")
    sets = {}
    for registry in registries:
        _, lenses = lens.parse(registry)
        sets[registry] = [(item.key, list(getattr(item, "mandates", None) or [])) for item in lenses]
    return sets


def _audit_lenses() -> list:
    """`(key, mandates, admission labels, heading descriptor)` per `/session_audit` lens. A `lens.py` without
    `Lens.admission` gives every lens no labels, so every lens reads as unconditional."""
    lens = _import("lens")
    return [(item.key, list(getattr(item, "mandates", None) or []), list(getattr(item, "admission", None) or []),
             item.descriptor) for item in lens.parse("session_audit")[1]]


def _candidate_sets() -> tuple[list[str], list[str]]:
    """(the candidate set, the unconditional set): every `/session_audit` lens's mandates, and those of the
    lenses whose labels lack `conditional`. HARNESS_REVIEW_CANDIDATE_KEYS, when set, names both."""
    override = os.environ.get("HARNESS_REVIEW_CANDIDATE_KEYS")
    if override is not None:
        keys = sorted({key for key in re.split(r"[,\s]+", override) if key})
        return keys, keys
    lenses = _audit_lenses()
    whole = sorted({key for _, keys, _, _ in lenses for key in keys})
    floor = sorted({key for _, keys, labels, _ in lenses if "conditional" not in labels for key in keys})
    return whole, floor


def candidate_keys() -> list[str]:
    """The mandates the `/session_audit` roster's lenses declare (`keys --stage candidate`)."""
    return _candidate_sets()[0]


def resolve_candidate(roster: list | None = None) -> list[str]:
    """The `candidate` owed set: the unconditional set without a roster; with one, the roster's mandates that
    are candidate mandates plus the unconditional set, so a roster that omits an always-on lens still owes it."""
    whole, floor = _candidate_sets()
    if roster is None:
        return floor
    named = {mandate for seat in roster for mandate in seat.get("mandates") or []}
    return sorted((named & set(whole)) | set(floor))


# ---- ledger and escape-log files --------------------------------------------------------------------------


def _toplevel(repo: Path) -> Path | None:
    result = _git(repo, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    return Path(result.stdout.decode("utf-8", errors="replace").strip())


def ledger_path(repo: Path) -> Path:
    override = os.environ.get("HARNESS_REVIEW_LEDGER")
    if override:
        return Path(override)
    superproject = _git(repo, ["rev-parse", "--show-superproject-working-tree"])
    if superproject.returncode == 0 and superproject.stdout.strip():
        raise ToolError(f"{repo} is a submodule checkout: the review ledger keys superproject paths, so run this "
                        f"from the superproject {superproject.stdout.decode().strip()}")
    common = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common.returncode == 0:
        return Path(common.stdout.decode().strip()).parent / ".claude" / "logs" / "review_ledger.jsonl"
    return repo / ".claude" / "logs" / "review_ledger.jsonl"


def escape_log_path(repo: Path, toplevel: Path | None = None) -> Path:
    override = os.environ.get("HARNESS_ESCAPE_LOG")
    if override:
        return Path(override)
    return (toplevel or _toplevel(repo) or repo) / ".claude" / "escape_log.jsonl"


def escape_overlay_path(repo: Path) -> Path:
    """The shared runtime revocation overlay: beside the ledger, so it reaches every worktree the ledger does."""
    return ledger_path(repo).with_name("escape_overlay.jsonl")


def _read_jsonl(path: Path, label: str = "review ledger"):
    """([objects], corrupt line count, problem text or None)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], 0, f"no {label} at {path}"
    except OSError as exc:
        return [], 0, f"{label} {path} is unreadable: {exc}"
    rows, corrupt = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            corrupt += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            corrupt += 1
    return rows, corrupt, None


def read_rows(path: Path):
    return _read_jsonl(path)


def _append_rows(path: Path, rows: list) -> bool:
    """Append `rows` under the ledger's lock, so a rotation in one worktree never replaces the file under another's
    append. False when the lock cannot be taken or the write fails."""
    state = _import("_hook_state", CLAUDE_DIR / "hooks")
    handle = state._acquire_json_lock(str(path))
    if handle is None:
        return False
    try:
        return state.append_jsonl_rotating(str(path), rows, max_bytes=MAX_BYTES, keep_lines=KEEP_LINES)
    finally:
        state._release_json_lock(handle)


# ---- git facts for coverage -------------------------------------------------------------------------------


class Ctx:
    """The repository facts a coverage check reads, each fetched once."""

    def __init__(self, repo: Path, base_ref: str, hashes: dict | None = None, snapshot: str | None = None):
        self.repo = repo
        self.snapshot = snapshot
        self.base_ref = base_ref
        both = _git(repo, ["rev-parse", "--show-toplevel", "HEAD"])
        found = both.stdout.decode("utf-8", errors="replace").splitlines() if both.returncode == 0 else []
        self.toplevel = Path(found[0]) if len(found) >= 2 else (_toplevel(repo) or repo)
        self.overrides = dict(hashes or {})
        self._hashes: dict = {}
        self._head = snapshot or (found[1].strip() if len(found) >= 2 else "unset")
        self._merge_base = "unset"
        self._ancestor: dict = {}
        self._merges: dict = {}
        self._resolved: dict = {}

    @property
    def head(self):
        if self._head == "unset":
            result = _git(self.repo, ["rev-parse", "HEAD"])
            self._head = result.stdout.decode().strip() if result.returncode == 0 else None
        return self._head

    def merge_base(self):
        if self._merge_base == "unset":
            result = _git(self.repo, ["merge-base", self.base_ref, self.head or "HEAD"])
            self._merge_base = result.stdout.decode().strip() if result.returncode == 0 else None
        return self._merge_base

    def hash_files(self, paths) -> dict:
        """path -> content hash now (git blob sha; SHA-256 outside a git tree); None for a missing file. With a
        `snapshot` commit it is the blob at that commit, never the worktree. `INDEX` reads staged blobs."""
        todo = [p for p in paths if p not in self._hashes and p not in self.overrides]
        if self.snapshot:
            if todo:
                revision = "" if self.snapshot == "INDEX" else self.snapshot
                for path, line in zip(todo, self._batch_check(self.toplevel, revision, todo)):
                    fields = line.split()
                    self._hashes[path] = fields[0] if len(fields) == 3 and fields[1] == "blob" else None
            return {p: self.overrides.get(p, self._hashes.get(p)) for p in paths}
        present = [p for p in todo if (self.toplevel / p).is_file()]
        for path in todo:
            self._hashes[path] = None
        if present:
            result = _run(["git", "hash-object", "-w", "--stdin-paths"], self.toplevel,
                          stdin=("\n".join(present) + "\n").encode("utf-8"))
            lines = result.stdout.decode().split() if result.returncode == 0 else []
            for path, sha in zip(present, lines if len(lines) == len(present) else [None] * len(present)):
                self._hashes[path] = sha
            for path in present:
                if self._hashes[path] is None:
                    self._hashes[path] = "sha256:" + hashlib.sha256((self.toplevel / path).read_bytes()).hexdigest()
        return {p: self.overrides.get(p, self._hashes.get(p)) for p in paths}

    def _batch_check(self, repo: Path, revision: str, paths: list[str]) -> list[str]:
        """`cat-file --batch-check` lines for `revision:path` in `repo`. A path under a submodule is read from
        that submodule's object store at the commit the gitlink pins at `revision` (`missing` when it is
        absent), since `revision:path` stops at the gitlink."""
        splits = {p: split_submodule(repo, p) for p in paths}
        flat = [p for p in paths if splits[p] is None]
        lines = {}
        if flat:
            query = "".join(f"{revision}:{p}\n" for p in flat).encode("utf-8")
            result = _git(repo, ["cat-file", "--batch-check"], stdin=query)
            found = result.stdout.decode("utf-8", errors="replace").splitlines() if result.returncode == 0 else []
            if len(found) != len(flat):
                raise ToolError(f"cannot read {self.snapshot} blobs for {len(flat)} files")
            lines.update(zip(flat, found))
        for path in paths:
            if splits[path] is None:
                continue
            link, rest = splits[path]
            pinned = _git(repo, ["rev-parse", "--verify", "--quiet", f"{revision}:{link}"])
            commit = pinned.stdout.decode().strip() if pinned.returncode == 0 else ""
            lines[path] = self._batch_check(repo / link, commit, [rest])[0] if commit else "missing"
        return [lines[p] for p in paths]

    def deleted_hash(self, path: str):
        """The last deletion at the reviewed head, never an uncommitted removal or a never-created path. A path
        under a submodule takes the submodule commit that deleted it, read from the commit the gitlink pins at the
        head; with the gitlink itself gone, the superproject commit that removed it."""
        if not self.head or Ctx(self.repo, self.base_ref, snapshot=self.head).hash(path) is not None:
            return None
        found = split_submodule(self.toplevel, path)
        pinned = _git(self.toplevel, ["rev-parse", "--verify", "--quiet", f"{self.head}:{found[0]}"]) if found else None
        if pinned is not None and pinned.returncode == 0:
            pin = pinned.stdout.decode().strip()
            commits = _git_lines(readable_submodule(self.toplevel, found[0], [pin]),
                                 ["log", "-1", "--format=%H", "--no-renames", "--diff-filter=D", pin, "--", found[1]])
        else:
            commits = _git_lines(self.repo, ["log", "-1", "--format=%H", "--no-renames", "--diff-filter=D",
                                             self.head, "--", found[0] if found else path])
        return "deleted:" + commits[0] if commits else None

    def hash(self, path: str):
        return self.hash_files([path])[path]

    def is_ancestor(self, sha: str) -> bool:
        if sha not in self._ancestor:
            self._ancestor[sha] = sha == self.head or _is_ancestor(self.repo, sha, "HEAD")
        return self._ancestor[sha]

    def merge_touched(self, sha: str, path: str) -> bool:
        """True when a merge between `sha` and HEAD rewrote `path` in its conflict resolution."""
        if sha not in self._merges:
            result = _git(self.repo, ["rev-list", "--merges", f"{sha}..HEAD"])
            self._merges[sha] = result.stdout.decode().split() if result.returncode == 0 else []
        for merge in self._merges[sha]:
            if merge not in self._resolved:
                shown = _git(self.repo, ["show", "--remerge-diff", "--name-only", "--format=", merge])
                self._resolved[merge] = set(shown.stdout.decode("utf-8", errors="replace").split("\n"))
            if path in self._resolved[merge]:
                return True
        return False


def _tier_order():
    return _import("model_registry").TIER_ORDER


def _subject_kind(subject: str) -> str:
    kind, sep, rest = (subject or "").partition(":")
    if not sep or not rest or kind not in SUBJECT_KINDS:
        raise ToolError(f"subject {subject!r} must be one of " + ", ".join(f"{k}:<id>" for k in SUBJECT_KINDS))
    return kind


# ---- coverage: the clauses a covering row passes ----------------------------------------------------------


class Cover:
    """Decides whether a ledger row covers a (mandate, file) pair for one subject and one seat."""

    def __init__(self, ctx: Ctx, catalog: dict, rows: list, escapes: list | None, subject_kind: str,
                 seat: dict | None = None, design: str | None = None, claude_dir: Path | None = None):
        """`escapes` None means the escape log exists and cannot be parsed: clause 5 then holds for no row."""
        self.ctx = ctx
        self.catalog = catalog
        self.rows = rows
        self.escapes = escapes
        self.subject_kind = subject_kind
        self.seat = seat
        self.design = design
        self.claude_dir = claude_dir or _claude_dir()
        self._rules: dict = {}
        self._premise_ancestors: dict = {}
        self._tiers = None
        self._resolved_rows = {}
        self._visiting = set()
        self._resolving_blob = None
        self._premise = None
        self._registry = None

    def rule(self, mandate: str):
        if mandate not in self._rules:
            self._rules[mandate] = rule_hash(mandate, self.claude_dir, self.catalog)
        return self._rules[mandate]

    def tiers(self):
        if self._tiers is None:
            self._tiers = _tier_order()
        return self._tiers

    def exception(self, row: dict) -> bool:
        return (self.design is not None and self.subject_kind == "plan"
                and row.get("subject") == f"doc:{self.design}")

    def _row_holds(self, row, mandate, path, blob, seat):
        saved = self.seat, self._resolving_blob
        self.seat, self._resolving_blob = seat, blob
        try:
            if row.get("mandate") != mandate or not all(fn(self, row, mandate, path) for _, fn in CLAUSES):
                return False
            if "from" not in row:
                return not ((seat or {}).get("nonPanelRoot") and row.get("reviewer") == "panel")
            origins = row["from"]
            if not isinstance(origins, dict) or path not in origins:
                return False
            previous = origins[path]
            return previous is None or (isinstance(previous, str)
                                         and self.resolve(mandate, path, previous, seat) is not None)
        finally:
            self.seat, self._resolving_blob = saved

    def resolve(self, mandate: str, path: str, blob: str, seat: dict | None):
        """Newest valid direct or derived row; every predecessor passes the same clauses."""
        key = (mandate, path, blob, json.dumps(seat, sort_keys=True), self.subject_kind, self.design)
        if key in self._resolved_rows:
            return self._resolved_rows[key]
        if key in self._visiting:
            return None
        self._visiting.add(key)
        try:
            for row in reversed(self.rows):
                if self._row_holds(row, mandate, path, blob, seat):
                    self._resolved_rows[key] = row
                    return row
            # A miss through a cycle is contextual; do not memoize it.
            return None
        finally:
            self._visiting.remove(key)

    def standing(self, row: dict):
        """"qualified" or "supplementary" for a panel row whose model the registry resolves (the row's tier
        against registry `roles` and `supplementary`), else None: a non-panel row, an unresolvable model, or a
        tier the model holds in neither. A registry that cannot load reads every panel row as "supplementary"
        (fail closed: none covers), after one stderr line naming the load error."""
        if row.get("reviewer") != "panel":
            return None
        if self._registry is None:
            registry = _import("model_registry")
            try:
                self._registry = (registry, registry.load())
            except Exception as err:
                print(f"review_ledger: clause 9 cannot read the model registry ({err!r}); no panel row covers "
                      "until it loads", file=sys.stderr)
                self._registry = (registry, False)
        registry, data = self._registry
        if not data:
            return "supplementary"
        entry = registry.row_by_id(row.get("model"), data)
        return registry.seat_standing(entry, row.get("tier")) if entry else None

    def _premise_covers(self, row: dict, mandate: str, path: str, premise: str) -> bool:
        """A premise audit's row: its frozen commits include `premise`, which its head descends from."""
        saved = self._premise
        self._premise = premise
        try:
            return self._premise_row_covers(row, mandate, path, premise)
        finally:
            self._premise = saved

    def _premise_row_covers(self, row: dict, mandate: str, path: str, premise: str) -> bool:
        inputs = row.get("premise_commits")
        if not isinstance(inputs, list) or premise not in inputs or not row.get("head"):
            return False
        if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha) for sha in inputs):
            return False
        pair = (premise, row["head"])
        if pair not in self._premise_ancestors:
            self._premise_ancestors[pair] = _is_ancestor(self.ctx.repo, *pair)
        if not self._premise_ancestors[pair]:
            return False
        return row.get("mandate") == mandate and all(
            fn(self, row, mandate, path) for number, fn in CLAUSES if number != 3)

    def fold_applied(self, mandate: str, path: str, commit: str) -> bool:
        """True when a fold row of `mandate` recorded `path` at the bytes `commit` holds: the commit applies that
        review's own findings, which the review already verified, so its premises owe no new audit."""
        blob = blob_at(self.ctx.repo, commit, path)
        if blob is None:
            return False
        return any(row.get("mandate") == mandate and row.get("reviewer") == "inline:fold"
                   and (row.get("files") or {}).get(path) == blob
                   and _clause_outcome(self, row, mandate, path) and _clause_rule_text(self, row, mandate, path)
                   for row in self.rows)

    def covers(self, row: dict, mandate: str, path: str, premise_commit: str | None = None) -> bool:
        if premise_commit is not None:
            return self._premise_covers(row, mandate, path, premise_commit)
        return self._row_holds(row, mandate, path, self.ctx.hash(path), self.seat)

    def covering_row(self, mandate: str, path: str, premise_commit: str | None = None):
        if premise_commit is not None:
            for row in reversed(self.rows):
                if self._premise_covers(row, mandate, path, premise_commit):
                    return row
            return None
        return self.resolve(mandate, path, self.ctx.hash(path), self.seat)

    def covering_ids(self, paths) -> list[str]:
        """The sorted ids of the rows that cover any of `paths` for any catalog mandate under any subject kind."""
        kind, found = self.subject_kind, set()
        try:
            for subject_kind in SUBJECT_KINDS:
                self.subject_kind = subject_kind
                for path in paths:
                    for mandate in self.catalog:
                        try:
                            row = self.covering_row(mandate, path)
                        except ToolError as exc:
                            print(f"review_ledger: {exc}", file=sys.stderr)
                            continue
                        if row is not None:
                            found.add(row.get("id"))
        finally:
            self.subject_kind = kind
        return sorted(found)

    def earlier_blob(self, mandate: str, path: str, seats: list):
        """The blob of `path` that the newest row passing every clause except content (3) reviewed, or None.
        Clause 6 is judged against each of `seats`, and passes when there are none."""
        current = self.ctx.hash(path)
        for row in reversed(self.rows):
            blob = (row.get("files") or {}).get(path)
            if blob and blob != current and all(
                    self.resolve(mandate, path, blob, seat) is not None for seat in seats or [None]):
                return blob
        return None

    def _depth_holds(self, row: dict, mandate: str, path: str, seat: dict) -> bool:
        self.seat = seat
        return _clause_depth(self, row, mandate, path)

    def pair_state(self, mandate: str, path: str, seats: list, premise_commit: str | None = None):
        """None when (mandate, path) is covered, else `uncovered` or `depth-unchecked`.

        `seats` are the depth seats (tier, effort, load) that would carry the mandate, and each must find a
        covering row. With none, clause 6 cannot be judged: a pair only panel rows cover is `depth-unchecked`."""
        if premise_commit is not None:
            if seats:
                for seat in seats:
                    self.seat = seat
                    if self.covering_row(mandate, path, premise_commit) is None:
                        return "uncovered"
                return None
            self.seat = None
            reviewers = [row.get("reviewer") for row in self.rows if self.covers(row, mandate, path, premise_commit)]
            if not reviewers:
                return "uncovered"
            return "depth-unchecked" if all(reviewer == "panel" for reviewer in reviewers) else None
        blob = self.ctx.hash(path)
        if seats:
            return None if all(self.resolve(mandate, path, blob, seat) is not None for seat in seats) else "uncovered"
        if self.resolve(mandate, path, blob, {"nonPanelRoot": True}) is not None:
            return None
        return "depth-unchecked" if self.resolve(mandate, path, blob, None) is not None else "uncovered"


def _clause_rule_text(cover, row, mandate, path):
    current = cover.rule(mandate)
    return current is not None and row.get("ruleHash") == current


def _clause_outcome(cover, row, mandate, path):
    return row.get("outcome") in COVERING_OUTCOMES


def _clause_content(cover, row, mandate, path):
    if cover.exception(row):
        current = cover.ctx.hash(cover.design)
        return current is not None and (row.get("files") or {}).get(cover.design) == current
    current = cover._resolving_blob if cover._resolving_blob is not None else cover.ctx.hash(path)
    return current is not None and (row.get("files") or {}).get(path) == current


def _clause_reach(cover, row, mandate, path):
    head = row.get("head")
    target = cover.design if cover.exception(row) else path
    if not head:
        return str(row.get("subject", "")).startswith("doc:")
    return cover.ctx.is_ancestor(head) and not cover.ctx.merge_touched(head, target)


def _clause_no_escape(cover, row, mandate, path):
    if cover.escapes is None:
        return False
    ts = str(row.get("ts", ""))
    call = row.get("call")
    return not any(path in (escape.get("paths") or []) and str(escape.get("ts", "")) >= ts
                   and not (call and escape.get("foundBy") == call)
                   for escape in cover.escapes)


def _clause_depth(cover, row, mandate, path):
    if row.get("reviewer") != "panel":
        return True
    tiers = cover.tiers()
    tier, effort, load = row.get("tier"), row.get("effort"), row.get("seatMandates")
    if tier not in tiers:
        return False
    derived = "from" in row
    if not derived and (effort not in EFFORT_ORDER or not isinstance(load, list)):
        return False
    seat = cover.seat or {}
    if seat.get("tier") is not None:
        if seat["tier"] not in tiers or tiers.index(tier) > tiers.index(seat["tier"]):
            return False
    if not derived and seat.get("effort") is not None:
        if seat["effort"] not in EFFORT_ORDER or EFFORT_ORDER.index(effort) < EFFORT_ORDER.index(seat["effort"]):
            return False
    if not derived and seat.get("load") is not None and len(load) > seat["load"]:
        return False
    return True


def _clause_standing(cover, row, mandate, path):
    """A row whose model holds the row's tier in registry `supplementary` covers only beside a qualified row.

    The weakness: a review seat from such a row alone leaves defects a qualified seat finds, or reports invalid
    ones. The retirement observation: a lone seat from the row matches a qualified seat's valid-finding recall on
    the plan-review pin A/B; its entry then moves to `roles`. The claim is defined in the ladder's Role
    definitions. The partner is another panel row whose model is qualified at its own tier and holds the pair
    under clauses 1-8 (3 excepted in premise mode); its depth is judged against the current seat without the
    seat's `load`, because a merged qualified seat carries more mandates than the one-lens supplementary seat.
    Rows the clause cannot read as supplementary (non-panel, unresolvable model) pass; clauses 1-8 still bind them.
    An unloadable registry is the exception: no panel row covers (`Cover.standing`)."""
    if cover.standing(row) != "supplementary":
        return True
    seat = cover.seat
    peer = None if seat is None else {k: v for k, v in seat.items() if k != "load"}
    for other in cover.rows:
        if other is row or cover.standing(other) != "qualified":
            continue
        if cover._premise is not None:
            saved, cover.seat = cover.seat, peer
            try:
                if cover._premise_covers(other, mandate, path, cover._premise):
                    return True
            finally:
                cover.seat = saved
        elif cover._row_holds(other, mandate, path,
                              cover._resolving_blob if cover._resolving_blob is not None else cover.ctx.hash(path),
                              peer):
            return True
    return False


def _clause_merge_base(cover, row, mandate, path):
    if not (cover.catalog.get(mandate) or {}).get("interaction"):
        return True
    current = cover.ctx.merge_base()
    return current is not None and row.get("mergeBase") == current


def _clause_subject(cover, row, mandate, path):
    if cover.exception(row):
        return True
    try:
        kind = _subject_kind(row.get("subject"))
    except ToolError:
        return False
    readers = ("session", "pr")
    return kind == cover.subject_kind or (kind in readers and cover.subject_kind in readers)


CLAUSES = (
    (1, _clause_rule_text),
    (2, _clause_outcome),
    (3, _clause_content),
    (4, _clause_reach),
    (5, _clause_no_escape),
    (6, _clause_depth),
    (7, _clause_merge_base),
    (8, _clause_subject),
    (9, _clause_standing),
)


def _note_ledger(problem, corrupt) -> None:
    if problem:
        print(f"{problem}: every pair is uncovered", file=sys.stderr)
    if corrupt:
        print(f"{corrupt} corrupt ledger line(s) skipped", file=sys.stderr)


def normalize_files(toplevel: Path, files) -> list[str]:
    """Repo-relative POSIX paths: an absolute path inside `toplevel` loses that prefix, and `./` goes."""
    out = []
    for raw in files:
        path = Path(str(raw).replace("\\", "/"))
        if path.is_absolute():
            try:
                path = path.relative_to(toplevel)
            except ValueError:
                pass
        text = path.as_posix()
        out.append(text[2:] if text.startswith("./") else text)
    return list(dict.fromkeys(out))


def read_escapes(repo: Path, toplevel: Path | None = None):
    """(escape rows, problem): the checkout's tracked escape log plus the shared overlay (`escape_overlay_path`),
    or rows None with the problem text when either exists and cannot be parsed.

    An absent file is no escapes. An unreadable file or any corrupt line is unparseable, because a lost escape
    row would leave standing the coverage it was written to revoke. A row both files hold reads twice, which
    clause 5 treats as once."""
    out = []
    for path, label in ((escape_log_path(repo, toplevel), "escape log"),
                        (escape_overlay_path(repo), "escape overlay")):
        rows, corrupt, problem = _read_jsonl(path, label)
        if problem and path.exists():
            return None, problem
        if corrupt:
            return None, f"{label} {path} has {corrupt} corrupt line(s)"
        out.extend(rows)
    return out, None


def _make_cover(repo: Path, base_ref: str, subject_kind: str, seat=None, design=None, hashes=None):
    catalog = load_catalog()
    rows, corrupt, problem = read_rows(ledger_path(repo))
    _note_ledger(problem, corrupt)
    ctx = Ctx(repo, base_ref, hashes)
    escapes, escape_problem = read_escapes(repo, ctx.toplevel)
    if escape_problem:
        print(f"{escape_problem}: no row covers until it is repaired", file=sys.stderr)
    return Cover(ctx, catalog, rows, escapes, subject_kind, seat, design), problem


def cover_at(repo: Path, hashes: dict, base_ref: str = "origin/main") -> Cover:
    """A session-subject `Cover` that reads each file in `hashes` (path -> blob sha) at that blob."""
    return _make_cover(repo, base_ref, "session", hashes=hashes)[0]


def reviewed_blob(cover: Cover, path: str, mandates, seats):
    """Newest blob covered by every mandate at all its roster depth seats, else None."""
    for row in reversed(cover.rows):
        blob = (row.get("files") or {}).get(path)
        if blob and all(cover.resolve(m, path, blob, seat) is not None
                        for m in mandates for seat in seats.get(m, []) or [{"nonPanelRoot": True}]):
            return blob
    return None


def _owes(path: str, mandate: str) -> bool:
    if path.endswith(".md") and (mandate.startswith("cq.") or mandate == "tq"):
        return False
    if mandate == "harness-quality":
        return path.startswith(".claude/") or path.rsplit("/", 1)[-1] in ("CLAUDE.md", "MEMORY.md")
    return True


def uncovered_pairs(cover: Cover, files: list[str], mandates: list[str], roster: list | None = None) -> list[tuple]:
    """`(path, mandate)` for a pair no row covers; `(path, mandate, "depth-unchecked")` for a pair only panel
    rows cover when `roster` names no seat to judge clause 6 against, so it never reads as covered.

    A file owes only the mandates that judge its kind, per `execution_depth.md` §Mandate admission: a `.md`
    file owes no code-quality (`cq.*`) or test-quality (`tq`) mandate, and a file outside the harness set
    (`.claude/**`, `CLAUDE.md`, `MEMORY.md`) owes no `harness-quality` mandate."""
    cover.ctx.hash_files(files)
    pairs = []
    for path in files:
        for mandate in mandates:
            if not _owes(path, mandate):
                continue
            state = cover.pair_state(mandate, path, _depth_seats(roster or [], mandate))
            if state:
                pairs.append((path, mandate) + (("depth-unchecked",) if state == "depth-unchecked" else ()))
    return pairs


class LedgerUnreadable(Exception):
    """The ledger file exists and cannot be read."""


def owed_staged(repo: Path, hashes: dict, mandates: list[str] | None = None,
                base_ref: str = "origin/main", roster: list | None = None) -> list[tuple]:
    """`uncovered_pairs` for staged blobs given as path -> blob sha; without a `roster`, a pair only panel rows
    cover comes back with a third field, `depth-unchecked`.

    A missing ledger covers nothing, so every pair is returned; a ledger that exists and cannot be read, or
    an escape log that exists and cannot be parsed, raises `LedgerUnreadable` naming the path, so a caller
    never mistakes a failed read for coverage.
    """
    path = ledger_path(repo)
    rows, _, problem = read_rows(path)
    if problem and path.exists():
        raise LedgerUnreadable(f"{path}: {problem}")
    escapes, escape_problem = read_escapes(repo)
    if escape_problem:
        raise LedgerUnreadable(escape_problem)
    catalog = load_catalog()
    chosen = resolve_candidate(roster) if mandates is None else list(mandates)
    cover = Cover(Ctx(repo, base_ref, hashes), catalog, rows, escapes, "session")
    return uncovered_pairs(cover, list(hashes), chosen, roster)


def _resolve_mandates(text: str, roster: list | None = None) -> list[str]:
    if text.strip() == "candidate":
        return resolve_candidate(roster)
    keys = [key for key in re.split(r"[,\s]+", text) if key]
    catalog = load_catalog()
    unknown = [key for key in keys if key not in catalog]
    if not keys or unknown:
        raise ToolError(f"--mandates takes catalog keys (review_ledger.py keys) or the alias `candidate`; "
                        f"got {text!r}" + (f", unknown: {', '.join(unknown)}" if unknown else ""))
    return keys


def cmd_uncovered(args, repo: Path) -> int:
    roster = _load_roster(args.roster, load_catalog()) if args.roster else None
    mandates = _resolve_mandates(args.mandates, roster)
    if not mandates:
        print("no candidate mandates declared: nothing to report (review_ledger.py keys --stage candidate)",
              file=sys.stderr)
    cover, _ = _make_cover(repo, args.base_ref, "session")
    if not args.roster:
        print("no --roster: clause 6 (depth) is unchecked, so a pair only panel rows cover prints "
              "`depth-unchecked`; pass the roster the review would seat", file=sys.stderr)
    files = normalize_files(cover.ctx.toplevel, args.files)
    hashes = cover.ctx.hash_files(files)
    for path in files:
        if hashes[path] is None:
            print(f"absent, nothing to review: {path}", file=sys.stderr)
    present = [path for path in files if hashes[path] is not None]
    for pair in uncovered_pairs(cover, present, mandates, roster):
        if len(pair) == 2:
            depth = _depth_seats(roster or [], pair[1])
            blob = reviewed_blob(cover, pair[0], [pair[1]], {pair[1]: depth or [None]})
            pair += (f"after:{blob}",) if blob else ()
        print("\t".join(pair))
    return 0


def _depth_seats(roster: list, mandate: str) -> list[dict]:
    return [{"tier": seat.get("tier"), "effort": seat.get("effort"), "load": len(seat["mandates"])}
            for seat in roster if mandate in seat["mandates"]]


# ---- plan -------------------------------------------------------------------------------------------------


def _pick(tier: str) -> dict:
    try:
        provider_bands = _import("provider_bands")
        result = provider_bands.pick(tier, provider_bands.session_plan())
        result = dict(result, ranked=[dict(row, bandReading=provider_bands.reading(row["transport"]))
                                     for row in result.get("ranked") or []])
        return result
    except Exception as exc:
        return {"transport": None, "room": None, "payOptions": [], "ranked": [], "reason": f"unreadable: {exc}"}


CODE_REVIEW_SEAT = "lane-code-reviewer"


def cross_family(required: bool, used_families: list, used_models: list, tier: str, data: dict,
                 pick_result: dict) -> dict:
    """The cross-family seat a pre-merge panel is owed: another family with room, else another model of one."""
    out = {"required": bool(required), "usedFamilies": sorted(set(used_families)), "candidates": []}
    if not required:
        return out
    model_registry = _import("model_registry")
    ready = {row["transport"] for row in pick_result.get("ranked") or [] if row.get("room") is not None}
    pay = {row.get("id") for row in pick_result.get("payOptions") or []}

    def eligible(row):
        plans = model_registry.plans_of(row["transport"], data)
        return row["id"] in pay or any(plan in ready for plan in plans)

    rows = [row for row in model_registry.rows_for_tier(tier, data) if eligible(row)]
    used = set(used_families)
    other = [row for row in rows if row["family"] not in used]
    chosen, degraded = (other, False) if other else (
        [row for row in rows if row["family"] in used and row["id"] not in set(used_models)], True)
    out["candidates"] = [{"alias": row.get("alias"), "id": row["id"], "family": row["family"],
                          "transport": row["transport"], "degraded": degraded} for row in chosen]
    if not out["candidates"]:
        out["why"] = pick_result.get("reason") or f"no available {tier} row with room"
    return out


def _load_roster(path: str, catalog: dict) -> list[dict]:
    try:
        roster = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read roster {path}: {exc}") from exc
    if isinstance(roster, dict):
        roster = roster.get("roster")
    if not isinstance(roster, list):
        raise ToolError(f"roster {path} is not a list of seats")
    for seat in roster:
        if not isinstance(seat, dict) or not seat.get("key") or not isinstance(seat.get("mandates"), list):
            raise ToolError(f"roster {path}: each seat needs key and mandates")
        for mandate in seat["mandates"]:
            if mandate not in catalog:
                raise ToolError(f"roster {path}: seat {seat['key']} names unknown mandate {mandate!r}")
        carried = _seat_mandates(seat["key"])
        missing = set(seat["mandates"]) - set(carried or [])
        if carried and missing:
            lenses = [key for entries in _lens_sets((*REVIEW_REGISTRIES, "redteam")).values() for key, mandates in entries
                      if set(mandates) & missing]
            raise ToolError(f"roster {path}: seat {seat['key']} cannot carry {', '.join(sorted(missing))}; "
                            f"missing lenses: {', '.join(lenses)}; key the seat by its lens names joined with '+'")
    return roster


def _strongest(tiers: list) -> str:
    order = _tier_order()
    known = [tier for tier in tiers if tier in order]
    return min(known, key=order.index) if known else "executor"


def _fingerprint(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_rows(rows):
    return [json.loads(row) for row in sorted({json.dumps(r, sort_keys=True) for r in rows})]


def subject_lineage(repo, task_id, subject, files, stage):
    """Accepted base→output chains, with explicit input and checked-origin joins on the existing job row.

    inputFiles binds consumed subjects to blobs. units maps paths to checked unit names; absent means
    file-only scope. origins binds source: finding|adapted|fold to a ledger row and accepted files (and
    optionally an evidence-backed decision id). A faithful fold retains the reviewer; source: fold adds
    its actor. Coverage alone and incidental decision evidence never imply authorship. Legacy done rows
    need the same explicit acceptance write as new jobs. No labels, recorder model or transcripts are mined.
    """
    registry, tasks = _import("model_registry"), _import("task_record")
    data = registry.load()
    hashes = Ctx(repo, "HEAD").hash_files(files)
    head = Ctx(repo, "HEAD", snapshot="HEAD")
    unknown, by_file, primary, inherited = [], {}, [], []
    try:
        jobs = tasks.all_jobs(task_id) if task_id else []
        decisions = {r["id"]: r for r in tasks.all_decisions(task_id)} if task_id else {}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        jobs, decisions = [], {}
        unknown.append({"reason": "unparseable task lineage", "task": task_id, "detail": str(exc)})
    if not task_id:
        unknown.append({"reason": "missing task scope"})
    for job in jobs:
        if job.get("state") in ("done", "accepted") and not job.get("role"):
            unknown.append({"run": job.get("run_id"), "label": job.get("label"),
                            "reason": "unresolved legacy attribution: missing typed role"})
    ledger, corrupt, problem = read_rows(ledger_path(repo))
    rows = {r.get("id"): r for r in ledger if r.get("id")}
    decision_roles = {"architect", "drafter", "fold", "reviewer"}
    implementation_roles = {"executor", "fold", "transcriber", "reviewer"}
    primary_roles = decision_roles if stage in ("design", "plan-check") else implementation_roles

    def actor(row, ref, role, path, blob):
        model = row.get("model") if row.get("modelObserved") is True else None
        group = registry.review_group(model, data)
        if not model or group is None or not row.get("identityBasis"):
            unknown.append(dict(ref, file=path, reason="missing observed actor, identity basis or producer group"))
            return None
        return dict(ref, role=role, model=model, group=group, identityBasis=row["identityBasis"], scope={path: blob})

    def origins_for(job, path, blob):
        origins = job.get("origins")
        if origins is None:
            return [], True
        if not isinstance(origins, list):
            unknown.append({"file": path, "run": job.get("run_id"), "reason": "unparseable accepted origins"})
            return [], False
        found, adds_actor = [], False
        applicable = [o for o in origins if isinstance(o, dict) and path in (o.get("files") or {})]
        if not applicable:
            unknown.append({"file": path, "reason": "missing accepted origin scope"})
        for origin in applicable:
            source, row = origin.get("source"), rows.get(origin.get("row"))
            ref = {"row": origin.get("row"), "source": source}
            decision = decisions.get(origin.get("decision")) if origin.get("decision") else None
            evidence = (decision or {}).get("evidence") or []
            evidence_ok = not origin.get("decision") or (decision is not None and evidence and all(
                e.get("sha256") and tasks.sha256(e.get("path")) == e["sha256"] for e in evidence))
            scoped_units = origin.get("units") or {}
            units_ok = isinstance(scoped_units, dict) and all(isinstance(names, list)
                and all(isinstance(n, str) and n for n in names) for names in scoped_units.values())
            if (source not in ("finding", "adapted", "fold") or origin["files"][path] != blob or not units_ok
                    or not row or row.get("reviewer") != "panel" or row.get("outcome") not in COVERING_OUTCOMES
                    or path not in (row.get("files") or {})
                    or row["files"][path] != (job.get("baseFiles") or {}).get(path)
                    or not evidence_ok or corrupt
                    or (problem and ledger_path(repo).exists())):
                unknown.append(dict(ref, file=path, reason="unresolved accepted reviewer origin or decision evidence"))
                continue
            entry = actor(row, dict(ref, run=row.get("run"), seat=row.get("seat")), "reviewer", path, blob)
            if entry:
                entry["reviewedFiles"] = {path: row["files"][path]}
                if origin.get("decision"):
                    entry["decision"] = origin["decision"]
                    entry["evidence"] = evidence
                if path in (origin.get("units") or {}):
                    entry["units"] = sorted(set(origin["units"][path]))
                found.append(entry)
            adds_actor |= source == "fold"
        return found, adds_actor

    def walk(path, current, stack=(), input_join=False):
        if not isinstance(path, str) or not path or (current is not None and not isinstance(current, str)):
            unknown.append({"file": path if isinstance(path, str) else None, "reason": "unparseable subject input binding"})
            return []
        key = (path, current)
        if key in stack:
            unknown.append({"file": path, "blob": current, "reason": "cyclic subject input chain"})
            return []
        chain, visited = [], set()
        blob = current
        while blob not in visited:
            visited.add(blob)
            matches = [j for j in jobs if j.get("state") == "accepted" and path in (j.get("files") or {})
                       and j["files"][path] == blob]
            if not matches:
                if not chain or blob not in (None, head.hash(path)):
                    unknown.append({"file": path, "blob": blob, "reason":
                        "missing accepted input binding" if input_join and not chain else
                        "no accepted subject binding" if not chain else "broken base-to-accepted chain"})
                break
            bases = set()
            for job in matches:
                ref = {"run": job.get("run_id"), "label": job.get("label"), "source": job.get("source")}
                role = job.get("role")
                base_map = job.get("baseFiles")
                if role not in tasks.JOB_ROLES or not isinstance(base_map, dict) or path not in base_map:
                    unknown.append(dict(ref, file=path, reason="missing typed role or accepted base"))
                    continue
                base = base_map[path]
                if base is not None and not isinstance(base, str):
                    unknown.append(dict(ref, file=path, reason="unparseable accepted base binding"))
                    continue
                bases.add(base)
                origin_entries, adds_actor = origins_for(job, path, blob)
                for entry in origin_entries:
                    chain.append((entry, input_join))
                if adds_actor:
                    entry = actor(job, ref, role, path, blob)
                    if entry:
                        entry["baseFiles"] = {path: base}
                        entry["inputFiles"] = job.get("inputFiles") or {}
                        unit_map = job.get("units") or {}
                        if not isinstance(unit_map, dict):
                            unknown.append(dict(ref, file=path, reason="unparseable checked units"))
                            unit_map = {}
                        names = unit_map.get(path)
                        if names is not None:
                            if isinstance(names, list) and all(isinstance(n, str) and n for n in names):
                                entry["units"] = sorted(set(names))
                            else:
                                unknown.append(dict(ref, file=path, reason="unparseable checked units"))
                        chain.append((entry, input_join))
                inputs = job.get("inputFiles") or {}
                if not isinstance(inputs, dict):
                    unknown.append(dict(ref, file=path, reason="unparseable subject inputs"))
                else:
                    for input_path, input_blob in sorted(inputs.items()):
                        chain.extend(walk(input_path, input_blob, (*stack, key), True))
            # A self-base is an adopted root at unchanged bytes, not a cycle or a second branch.
            predecessors = bases - {blob}
            if len(predecessors) > 1:
                unknown.append({"file": path, "reason": "ambiguous accepted bases"})
                break
            if not predecessors:
                break
            blob = predecessors.pop()
            if blob in visited:
                unknown.append({"file": path, "blob": blob, "reason": "cyclic base-to-accepted chain"})
        return chain

    for path, current in hashes.items():
        fact = {"primary": [], "inherited": [], "units": None}
        for entry, input_join in walk(path, current):
            role = entry["role"]
            # Transcription is artifact authorship, not inherited decision authorship.
            if stage in ("design", "plan-check") and role in ("transcriber", "executor"):
                continue
            bucket = "primary" if not input_join and role in primary_roles else "inherited"
            fact[bucket].append(entry)
            if "units" in entry:
                if fact["units"] is None:
                    fact["units"] = {}
                for unit in entry["units"]:
                    fact["units"].setdefault(unit, {"primary": [], "inherited": []})[bucket].append(entry)
        for bucket in ("primary", "inherited"):
            fact[bucket] = _canonical_rows(fact[bucket])
        for unit in (fact["units"] or {}).values():
            for bucket in ("primary", "inherited"):
                unit[bucket] = _canonical_rows(unit[bucket])
        by_file[path] = fact
        primary.extend(fact["primary"])
        inherited.extend(fact["inherited"])
    out = {"subject": subject, "revision": {"files": hashes}, "primary": _canonical_rows(primary),
           "inherited": _canonical_rows(inherited), "byFile": by_file, "unknown": _canonical_rows(unknown)}
    out["lineageFingerprint"] = _fingerprint(dict(out, stage=stage))
    return out


def _freshness(selection, required, cover, roster, ignored, candidates):
    """Freshness is coverage, not author distance; reuse exact inputs only through Cover's own clauses."""
    registry = _import("model_registry")
    used = selection["coveringReviewers"]
    groups = sorted({r["group"] for r in used if r["group"]})
    models = {r["model"] for r in used if r["model"]}
    out = {"required": required, "usedGroups": groups, "reusedBy": [], "candidates": [],
           "reason": "pre-merge freshness obligation" if required else "stage has no freshness obligation"}
    if not required:
        return out
    revision = selection["revision"]["files"]
    policy_readable = not any(r.get("reason") == "unreadable review policy owner" for r in selection["unknown"])
    if not ignored and revision and policy_readable:
        for row in cover.rows:
            saved = row.get("reviewerSelection")
            if (not isinstance(saved, dict) or row.get("subject") != selection["subject"]
                    or row.get("stage") not in PRE_MERGE_STAGES or row.get("reviewer") != "panel"
                    or row.get("modelObserved") is not True or row.get("files") != revision
                    or any(saved.get(k) != selection[k] for k in
                           ("subject", "revision", "lineageFingerprint", "policyFingerprint"))):
                continue
            prior = saved.get("freshness") or {}
            group = registry.review_group(row.get("model"))
            if not group or not prior.get("required"):
                continue
            outsider = group not in (prior.get("usedGroups") or [])
            different = row.get("model") not in {r.get("model") for r in saved.get("coveringReviewers") or []}
            if not outsider and not (row.get("degraded") and different):
                continue
            mandate = row.get("mandate")
            seats = [dict(s, tier=_strongest(["architect", s.get("tier")]))
                     for s in _depth_seats(roster, mandate)] or [{"tier": "architect"}]
            if mandate not in cover.catalog or not all(
                    cover._row_holds(row, mandate, path, blob, seat)
                    for path, blob in revision.items() for seat in seats):
                continue
            # Every mandate admitted to this backstop must still be covered; dependency checks stay in Cover.
            if not all(cover.pair_state(m, p, _depth_seats(roster, m)) is None
                       for m in {m for s in roster for m in s["mandates"]} for p in revision):
                continue
            out["reusedBy"].append(row["id"])
    if out["reusedBy"]:
        out.update(required=False, reusedBy=sorted(set(out["reusedBy"])), reason="exact subject, revision, lineage, policy and dependency reuse")
        return out
    ready = [r for r in candidates if r["headroomVerified"] or r["payAvailable"]]
    outsiders = [r for r in ready if r.get("reviewGroup") and r["reviewGroup"] not in groups]
    choices = outsiders or [r for r in ready if r.get("reviewGroup") and r["id"] not in models]
    out["candidates"] = [dict(r, degraded=not bool(outsiders)) for r in choices]
    if not choices:
        out["reason"] += "; no qualified candidate with verified headroom or paying option"
    elif not outsiders:
        out["reason"] += "; no untouched covering-reviewer group, use a different model and disclose degraded freshness"
    return out


def cmd_plan(args, repo: Path) -> int:
    if args.no_new_decision and not args.design:
        raise ToolError("--no-new-decision needs --design")
    if args.design and args.stage != "plan-check":
        raise ToolError("--design applies only to --stage plan-check")
    kind = _subject_kind(args.subject)
    catalog = load_catalog()
    roster = _load_roster(args.roster, catalog)
    files_in = list(args.files or [])
    waived = False
    if args.scope_json:
        scope = _load_scope(args.scope_json)
        waived = bool(scope.get("waived"))
        # The owner vouched for every file at the waived head, so only what changed since it is owed.
        files_in += [str(p) for p in scope.get("delta_files" if waived else "files") or []]
    cover, problem = _make_cover(repo, args.base_ref, kind)
    files = normalize_files(cover.ctx.toplevel, files_in)
    design = normalize_files(cover.ctx.toplevel, [args.design])[0] if args.design and args.no_new_decision else None
    cover.design = design
    required = args.stage == "pre-merge" or (args.stage == "pr-review" and kind == "pr")
    kept, dropped, families, models = [], [], [], []
    if waived and not files:
        print("nothing changed since the owner-waived head: no seat planned", file=sys.stderr)
        roster = []
    elif args.ignore_ledger:
        print("ledger ignored (--ignore-ledger): every seat kept", file=sys.stderr)
    elif not files:
        print("no files named (--files or --scope-json): every seat kept", file=sys.stderr)
    cover.ctx.hash_files(files + ([design] if design else []))
    owed_pairs = []
    for seat in roster:
        cover.seat = {"tier": seat.get("tier"), "effort": seat.get("effort"), "load": len(seat["mandates"])}
        left, owed = [], set()
        for mandate in seat["mandates"]:
            covering = [] if args.ignore_ledger or not files else [
                cover.covering_row(mandate, path) for path in files]
            if covering and all(covering):
                ids = list(dict.fromkeys(row.get("id", "") for row in covering))
                dropped.append({"mandate": mandate, "coveredBy": ",".join(ids)})
                families += [row.get("family") for row in covering if row.get("family")]
                models += [row.get("model") for row in covering if row.get("model")]
            else:
                left.append(mandate)
                missing = [path for path, row in zip(files, covering) if row is None] if covering else files
                owed.update(missing)
                owed_pairs += [(len(kept), mandate, path) for path in missing]
        if left:
            kept.append(dict(seat, mandates=left, uncoveredFiles=sorted(owed)))
    result = {"roster": kept, "dropped": dropped}
    earlier = {(index, mandate, path): cover.earlier_blob(mandate, path, _depth_seats(roster, mandate))
               for index, mandate, path in owed_pairs} if files and not args.ignore_ledger else {}
    if kept and earlier and all(earlier.values()):
        result["laterRound"] = True
        for index, seat in enumerate(kept):
            seat["earlierReviewedBlobs"] = [
                {"mandate": mandate, "file": path, "blob": blob}
                for (owner, mandate, path), blob in earlier.items() if owner == index]
    registry = _import("model_registry")
    data = registry.load()
    currency_tier = ("executor" if any(seat["key"] == CODE_REVIEW_SEAT for seat in roster)
                     else _strongest([seat.get("tier") for seat in roster])) if required else "architect"
    pick = _pick(currency_tier)
    selection = subject_lineage(repo, getattr(args, "task", None), args.subject, files, args.stage)
    tiers = registry.TIER_ORDER
    for seat in roster:
        tier = seat.get("tier")
        if tier not in tiers or tiers.index(tier) > tiers.index("architect"):
            selection["unknown"].append({"seat": seat["key"], "tier": tier,
                "reason": "admitted seat conflicts with existing architect floor; caller must resolve without changing width or tier maps"})
    policy_owners = {}
    for rel in ("skills/orchestration/reference/review_width.md", "reference/model_ladder_evidence.md"):
        try:
            policy_owners[rel] = hashlib.sha256((_claude_dir() / rel).read_bytes()).hexdigest()
        except OSError:
            policy_owners[rel] = None
            selection["unknown"].append({"file": rel, "reason": "unreadable review policy owner"})
    selection["policyFingerprint"] = _fingerprint({"stage": args.stage, "reviewGroups": data.get("reviewGroups"),
        "owners": policy_owners,
        "mandates": sorted({m for seat in roster for m in seat["mandates"]}),
        "depth": _canonical_rows([dict(key=seat["key"], tier=seat.get("tier"), effort=seat.get("effort"),
                                       mandates=sorted(set(seat["mandates"]))) for seat in roster]),
        "rules": {m: rule_hash(m, catalog=catalog) for m in sorted({m for seat in roster for m in seat["mandates"]})},
        "noNewDecision": bool(args.no_new_decision), "design": design,
        "designBlob": cover.ctx.hash(design) if design else None})
    covering = []
    if not args.ignore_ledger:
        for seat in roster:
            cover.seat = {"tier": seat.get("tier"), "effort": seat.get("effort"), "load": len(seat["mandates"])}
            for mandate in seat["mandates"]:
                for path in files:
                    row = cover.covering_row(mandate, path)
                    if row and row.get("reviewer") == "panel":
                        observed = row.get("modelObserved") is True
                        covering.append({"row": row.get("id"), "model": row.get("model"),
                            "group": registry.review_group(row.get("model"), data) if observed else None,
                            "modelObserved": observed, "scope": {path: cover.ctx.hash(path)}})
    selection["coveringReviewers"] = _canonical_rows(covering)
    ranked = {r.get("transport"): r for r in pick.get("ranked") or []}
    paid = {r.get("id") for r in pick.get("payOptions") or []}
    candidates = []
    for row in registry.rows_for_tier("architect", data):
        plans = registry.plans_of(row["transport"], data)
        facts = [ranked[p] for p in plans if p in ranked]
        verified = any(isinstance(f.get("room"), (float, int)) and f["room"] > 0
                       and f.get("window") and isinstance(f.get("left"), (float, int)) and f["left"] > 0
                       and isinstance(f.get("bandReading"), dict) and f["bandReading"].get("band")
                       and not f.get("why") for f in facts)
        candidates.append(dict(row, reviewGroup=registry.review_group(row["id"], data), payingPlans=plans,
            bandFacts=facts, currencyReason=pick.get("reason"), headroomVerified=verified,
            payAvailable=row["id"] in paid))
    selection["candidates"] = candidates
    selection["freshness"] = _freshness(selection, required, cover, roster, args.ignore_ledger, candidates)
    if required:
        result["crossFamily"] = dict(selection["freshness"], usedFamilies=sorted(set(families)))
    result["reviewerSelection"] = selection
    print(json.dumps(result))
    return 0


# ---- record -----------------------------------------------------------------------------------------------


def served_model():
    """The session's latest assistant `message.model` from its transcript, or None when unreadable."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None
    base = Path(_import("model_registry").session_projects_dir())
    for transcript in sorted(base.glob(f"*/{session}.jsonl")):
        try:
            with transcript.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 262144))
                tail = handle.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            model = ((entry.get("message") or {}).get("model")) if entry.get("type") == "assistant" else None
            if isinstance(model, str) and model:
                return model
    return None


_CLIPPED = re.compile(r" …\[\+\d+ chars\]$")


def _engaged(path: str, names: list, called, gaps, diffed=frozenset()) -> bool:
    """True when the seat engaged subject file `path`: either `diffed` (`_must_read_engaged`) holds it, or a normalized
    target in `names` (`verify_claims.resolve_path` after `review_result.target_path`) is `path`, so a directory
    target engages nothing beneath it, and normalized `called` (the paths `review_result.called_paths` observed,
    None when the seat has no transcript) holds it. In both cases `gaps` (the seat's whole gap strings, None when the bound clipped them, which engages
    nothing) has none naming its basename as a whole word in any letter case, with backslashes read as `/`. A
    gap naming the full path names the basename; a same-basename sibling named in a gap unengages this file
    too, which fails closed."""
    if gaps is None:
        return False
    if path not in diffed and (path not in names or (called is not None and path not in called)):
        return False
    named = re.compile(r"(?<![\w.-])" + re.escape(path.rsplit("/", 1)[-1]) + r"(?![\w-])", re.I)
    return not any(named.search(str(gap).replace("\\", "/")) for gap in gaps)


_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(.+)$")


def _diff_subject(file: str, section: list, hashes: dict | None, toplevel) -> str | None:
    """The `hashes` key a diff section's `a/<file>` header names. A diff taken inside a submodule names its files
    relative to that submodule, so `<gitlink>/<file>` competes with `<file>`; when both are reviewed the
    section's `index <old>..<new>` line picks the one whose blob it shows, else the section engages nothing."""
    if hashes is None:
        return file
    found = [path for path in (file, *(f"{sub}/{file}" for sub in submodule_paths(toplevel))) if path in hashes]
    if len(found) > 1:
        shown = next((m.group(1) for line in section if (m := re.match(r"index \w+\.\.(\w{7,64})", line))), "")
        found = [path for path in found if shown and str(hashes[path]).startswith(shown)]
    return found[0] if len(found) == 1 else None


def _must_read_engaged(must_read_spans, toplevel, hashes: dict | None = None) -> set:
    """The repo paths a seat reviewed through its must-read entries: a must-read file one span of whose `read`
    lines covers every line, and in a must-read diff each `diff --git a/<path> b/<path>` section (its header
    through the line before the next header) that one span covers. `must_read_spans` is
    `review_result.must_read_spans` output; an unreadable entry engages nothing."""
    engaged = set()
    for entry in must_read_spans if toplevel is not None and isinstance(must_read_spans, list) else []:
        rel = _import("review_result").target_path(f"Read:{entry.get('path')}", toplevel)
        path = _import("verify_claims").resolve_path(toplevel, rel)
        rel = normalize_files(toplevel, [path])[0]
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            continue
        headers = [(number, match.group(1)) for number, line in enumerate(lines, 1)
                   if (match := _DIFF_HEADER.match(line))]
        if not headers and not Path(rel).is_absolute() and any(
                start <= 1 and len(lines) <= end for start, end in entry.get("read") or []):
            engaged.add(rel)
        for index, (first, file) in enumerate(headers):
            last = headers[index + 1][0] - 1 if index + 1 < len(headers) else len(lines)
            if any(start <= first and last <= end for start, end in entry.get("read") or []):
                subject = _diff_subject(file, lines[first:last], hashes, toplevel)
                if subject:
                    engaged.add(subject)
    return engaged


def _seat_outcome(status: str, token: str, stopped_at: str | None, capability_partial: bool) -> str:
    """A sidecar mandate's final outcome: its token bounded by the seat status.

    A failed seat fails; an uncovered seat or a `not-reached` token reaches nothing (`blocked` when the seat
    stopped blocked); a partial seat covers nothing unless it is partial only because of capability gaps
    (`capability_partial`, computed once by `review_result.ingest`). Such a seat keeps its map's token, because the coverage rule "a not-reached, INCOMPLETE,
    blocked or failed outcome covers nothing" reads per mandate and per engaged file: the seat's own map and
    the engagement floor in `_seat_rows` decide which pairs a gap left incomplete. This is the Python home of
    the rule `review_fanout.js` `seatMandates` applies before an engine return, whose tokens are recorded as
    returned."""
    if status == "failed":
        return "failed"
    if status == "uncovered" or token == "not-reached":
        return "blocked" if stopped_at == "blocked" else "not-reached"
    if status == "partial" and not capability_partial and token in COVERING_OUTCOMES:
        return "incomplete"
    return token if token in OUTCOMES else "not-reached"


def _live_journal_source(directory: str, seats_file: str, toplevel: Path):
    """Read launched seats and their live results without the host's completed-run record."""
    directory = Path(directory)
    seats = _load_json(seats_file, "--seats-file")
    if not seats or any(not isinstance(label, str) or not label or not isinstance(seat, dict)
                        or any(field not in seat for field in ("model", "effort", "tier", "mandates", "mustRead"))
                        for label, seat in seats.items()):
        raise ToolError("--seats-file must map labels to {model, effort, tier, mandates, mustRead}")
    try:
        events = [json.loads(line) for line in (directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    except (OSError, ValueError) as exc:
        raise ToolError(f"{directory}/journal.jsonl: unreadable live journal: {exc}", 1) from exc
    labels, results, agents = {}, {}, {}
    for event in events:
        if isinstance(event, dict) and event.get("type") == "started" and event.get("label") in seats and event.get("agentId"):
            labels[event["agentId"]] = event["label"]
            agents[event["label"]] = event["agentId"]
    for event in events:
        if isinstance(event, dict) and event.get("type") == "result" and event.get("agentId") in labels:
            label = labels[event["agentId"]]
            results[label] = event.get("result")
            agents[label] = event["agentId"]
    served, jobs = {}, []
    review_result = _import("review_result")
    for label, seat in seats.items():
        jobs.append(dict(seat, label=label, seat=label.removeprefix(ENGINE_LABEL)))
        row = dict(model=seat["model"])
        agent_id = agents.get(label)
        if agent_id:
            meta = directory / f"agent-{agent_id}.meta.json"
            if meta.is_file():
                observed = _load_json(str(meta), "agent metadata").get("model")
                if isinstance(observed, str) and observed:
                    row["served_model"] = observed
            transcript = directory / f"agent-{agent_id}.jsonl"
            reads = review_result.called_reads(transcript, toplevel)
            if reads is not None:
                row["reads"] = reads
        served[label] = row
    journal = {"runId": directory.name, "workflowName": "dispatch", "args": {"jobs": jobs}}
    return journal, {label: seat["effort"] for label, seat in seats.items()}, \
        {label: seat["tier"] for label, seat in seats.items()}, served, results


def _journal_source(run_id: str, toplevel: Path):
    """(run record, label -> effort, label -> tier, label -> served row, label -> agent result) for one run.

    An agent's label comes from its journal `started` event, else from the served row naming its agent id, and
    the label's latest result wins. A served row whose agent result lists `checked.toolsUsed` takes it as
    `targets`, and one whose agent result's `gaps` is absent or a list takes the item each names as `gaps`, the
    whole list the engine's bounded return clips or counts. A label whose agent transcript `subagents/workflows/<run id>/agent-<agentId>.jsonl` exists (the
    agent of its latest result, else the agent its label names) gets a served row carrying `called`, the
    sorted repo paths `review_result.called_paths` reads from it, and `reads`, the `(path, spans)` pairs
    `review_result.called_reads` observes in it. An engine run's `args.agents` entry with a valid `mustRead`
    puts that list on the served row of its `review:<key>` label."""
    metrics = _import("orchestration_metrics")
    root = os.environ.get("HARNESS_REVIEW_SESSIONS")
    session = metrics._session_of_run(root).get(run_id) if root else metrics._session_of_run().get(run_id)
    if not session:
        raise ToolError(f"run {run_id} has no workflow journal; save the Workflow return to a file and "
                        f"pass it as --result", 1)
    journal = json.loads(Path(session, "workflows", run_id + ".json").read_text(encoding="utf-8"))
    served = {row["label"]: row for row in metrics.run_rows(session, run_id, claims={})}
    labels = {row["agent_id"]: label for label, row in served.items() if row.get("agent_id")}
    events = []
    agents = Path(session, "subagents", "workflows", run_id, "journal.jsonl")
    if agents.is_file():
        for line in agents.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line) if line.strip() else {}
            except ValueError as exc:
                raise ToolError(f"{agents}: unreadable journal line: {exc}", 1) from exc
            if isinstance(entry, dict) and entry.get("agentId"):
                events.append(entry)
    for entry in events:
        if entry.get("type") == "started" and entry.get("label"):
            labels[entry["agentId"]] = entry["label"]
    results, agent_of = {}, {}
    for entry in events:
        if entry.get("type") == "result" and entry.get("result") is not None and entry["agentId"] in labels:
            results[labels[entry["agentId"]]] = entry["result"]
            agent_of[labels[entry["agentId"]]] = entry["agentId"]
    for label, row in served.items():
        result = results.get(label) if isinstance(results.get(label), dict) else {}
        checked = result.get("checked")
        if isinstance(checked, dict) and isinstance(checked.get("toolsUsed"), list):
            row["targets"] = checked["toolsUsed"]
        if result and isinstance(result.get("gaps", []), list):
            gap_text = _import("review_result").gap_text
            row["gaps"] = [gap_text(gap) for gap in result.get("gaps", []) if gap_text(gap)]
    for agent_id, label in labels.items():
        agent_of.setdefault(label, agent_id)
    review_result = _import("review_result")
    for label, agent_id in agent_of.items():
        transcript = Path(session, "subagents", "workflows", run_id, f"agent-{agent_id}.jsonl")
        called = review_result.called_paths(transcript, toplevel)
        if called is not None:
            served.setdefault(label, {}).update(called=sorted(called),
                                                reads=review_result.called_reads(transcript, toplevel))
    for agent in _journal_args(journal).get("agents") or []:
        if isinstance(agent, dict) and agent.get("key") and review_result.valid_must_read(agent.get("mustRead")):
            served.setdefault(ENGINE_LABEL + agent["key"], {})["mustRead"] = agent["mustRead"]
    return journal, metrics.efforts_for(journal), metrics.tiers_for(journal), served, results


def _journal_args(journal: dict) -> dict:
    """A run journal's `args`, parsed when the journal stores them as a JSON string; {} when unreadable."""
    args = journal.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return args if isinstance(args, dict) else {}


ENGINE_LABEL = "review:"


def _journal_sources(results: dict) -> list:
    """An engine run's sources rebuilt from its seats' journaled results, for a return that shed them: each
    `review:<key>` agent's findings, attributed to seat `<key>`. A seat with no journaled result has none."""
    return [{"lens": label[len(ENGINE_LABEL):], "finding": finding}
            for label, value in results.items()
            if label.startswith(ENGINE_LABEL) and isinstance(value, dict) and isinstance(value.get("findings"), list)
            for finding in value["findings"] if isinstance(finding, dict)]


def _dispatch_review(journal: dict, results: dict, served: dict, pins: dict, tiers: dict, root: Path) -> dict:
    """A `dispatch.js` run as the prepared review shape `_sidecar_seats` reads.

    Each job that names its `seat` (and `mandates` and `mustRead`, as `lens_briefs.py` writes them) is one seat. Its deliverable
    is its spill file `<spillDir>/<label>.md` (a relative spillDir resolves against `root`), else its journaled
    result unless that is a digest pointing at a missing spill. `review_result.ingest` normalizes every seat, so
    `sources` holds every valid finding with its seat. A native job's engagement is its `checked.toolsUsed`,
    which ingest requires. Its must-read coverage is judged from the `reads` its transcript shows
    (`_journal_source`), else from that self-report, and the entry's `mustReadEvidence` says which. Each
    coverage entry carries the job's served model, its pinned effort and tier."""
    args = _journal_args(journal)
    jobs = [job for job in args.get("jobs") or [] if isinstance(job, dict) and job.get("seat") and job.get("label")]
    if not jobs:
        raise ToolError(f"dispatch run {journal.get('runId')} names no job `seat`; lens_briefs.py writes one on "
                        f"every review job", 1)
    review_result, dispatch_result = _import("review_result"), _import("dispatch_result")
    result = journal.get("result") if isinstance(journal.get("result"), dict) else {}
    spill = result.get("spillDir") or args.get("spillDir")
    seats = []
    for job in jobs:
        label = job["label"]
        text = None
        if spill:
            path = dispatch_result.deliverable_path(Path(root) / spill, label)
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
        value = results.get(label)
        body = value if text is None and isinstance(value, dict) else None
        if text is None and isinstance(value, str) and not dispatch_result._spill_target(value):
            text = value
        body = body or review_result.text_body(text)
        seat = {"label": job["seat"], "mandates": list(job.get("mandates") or [job["seat"]]),
                "mustRead": job.get("mustRead"),
                "payload": {"structured_output": body} if body else None,
                "record": {"exitCode": 0 if body else None, "schemaValid": body is not None,
                           "engagement": "proven"}}
        observed = (served.get(label) or {}).get("reads")
        if observed is not None:
            seat["reads"] = observed
        seats.append(seat)
    prepared = review_result.ingest(seats)
    for entry, job in zip(prepared["coverage"], jobs):
        row = served.get(job["label"]) or {}
        entry.update(model=row.get("served_model") or row.get("model") or job.get("model"),
                     modelObserved=bool(row.get("served_model")),
                     tier=tiers.get(job["label"]) or job.get("tier"), effort=pins.get(job["label"]) or job.get("effort"))
    prepared["findings"] = [source["finding"] for source in prepared["sources"]]
    return prepared


def _must_read_evidence(row: dict):
    """What a seat's must-read verdict rests on: `observed` when its served row has a `mustRead` list and the
    `reads` of a transcript, `self-reported` (its own `checked.toolsUsed`) with the list and no transcript, else None."""
    if row.get("mustRead") is None:
        return None
    return "observed" if row.get("reads") is not None else "self-reported"


def _engine_seats(result: dict, where: str, pins=None, tiers=None, served=None, results=None) -> list[dict]:
    """One seat per engine row: `perAgent`, else a bounded return's `delivery.coverage.seats` when it holds
    every row. Each mandate token is the engine's final outcome; a token outside OUTCOMES exits 2. A seat's
    `targets` and `gaps` are the served row's whole lists from its journaled agent result when it has them,
    because a bounded row keeps eight targets and clips each gap or, in `coverage.seats`, counts both. Without
    one, `targets` are the row's `checkedTargets` (a count exits 1) and `gaps` its `gapTexts` (absent reads as
    none) when whole, else None (engage nothing). Its `capabilityPartial` is the row's, absent reading as false; its
    `called` is the served row's, None when the run has no transcript for it.

    The engine judges must-read coverage from the seat's self-reported ranges. When the served row has the
    seat's `mustRead` and its transcript `reads`, `review_result.gap_verdict` re-judges it against the observed
    reads and the agent result's gaps (`results`, label -> agent result): a bad gap or an unread entry
    records the seat `failed` with every mandate `failed`. An observed read never lifts a seat the engine
    failed. `mustReadEvidence` names the basis (`_must_read_evidence`), and `mustReadSpans` the lines read of
    each entry on that basis (`review_result.must_read_spans`), which `_must_read_engaged` reads."""
    coverage = (result.get("delivery") or {}).get("coverage") or {}
    omitted = coverage.get("rowsOmitted") or 0
    per_agent = result.get("perAgent")
    if omitted > 0 or not isinstance(per_agent, list):
        full = coverage.get("seats")
        total = (coverage.get("rowsPreviewed") or 0) + omitted
        if not isinstance(full, list) or not full or len(full) != total:
            raise ToolError(f"{where} does not hold every per-seat row (rowsOmitted={omitted}, "
                            f"coverage.seats={len(full) if isinstance(full, list) else 'absent'}); record from "
                            f"the run journal (--run) or a return that holds every row", 1)
        per_agent = full
    seats = []
    for row in per_agent:
        key = row.get("key")
        mandates = row.get("mandates") if isinstance(row.get("mandates"), dict) else {}
        unknown = sorted(f"{m}={t}" for m, t in mandates.items() if t not in OUTCOMES)
        if unknown:
            raise ToolError(f"{where}: seat {key} carries mandate outcomes outside {'|'.join(OUTCOMES)}: "
                            + ", ".join(unknown))
        label = f"review:{key}"
        seat_row = (served or {}).get(label) or {}
        model = seat_row.get("served_model") or seat_row.get("model")
        targets = row.get("checkedTargets") or []
        if isinstance(seat_row.get("targets"), list):
            targets = seat_row["targets"]
        elif not isinstance(targets, list):
            raise ToolError(f"{where}: seat {key} counts its targets without listing them; record from the "
                            f"run journal (--run), whose agent results list them", 1)
        texts = row.get("gapTexts")
        texts = [text for text in texts if isinstance(text, str)] if isinstance(texts, list) else []
        count = row.get("gaps")
        whole = not (isinstance(count, int) and count > len(texts)) and not any(_CLIPPED.search(t) for t in texts)
        if isinstance(seat_row.get("gaps"), list):
            texts, whole = seat_row["gaps"], True
        status, evidence = row.get("status"), _must_read_evidence(seat_row)
        review_result = _import("review_result")
        reads = seat_row.get("reads") if evidence == "observed" else review_result.claimed_reads(targets)
        if evidence == "observed":
            agent = (results or {}).get(label)
            gaps = agent.get("gaps", []) if isinstance(agent, dict) else []
            _legal, _bad, unread, _notes = review_result.gap_verdict(gaps, seat_row["mustRead"], reads)
            if unread and status != "failed":
                print(f"seat {key}: its transcript does not cover must-read "
                      f"{review_result.unread_text(seat_row['mustRead'], seat_row['reads'], unread) or 'entries'}"
                      f"; recorded failed", file=sys.stderr)
                status, mandates = "failed", {mandate: "failed" for mandate in mandates}
        seats.append({"seat": key, "status": status, "stopped": row.get("stoppedAt"),
                      "mustReadEvidence": evidence,
                      "mustReadSpans": review_result.must_read_spans(seat_row.get("mustRead"), reads),
                      "capabilityPartial": row.get("capabilityPartial") is True,
                      "mandates": mandates, "targets": targets, "gaps": texts if whole else None,
                      "called": seat_row.get("called"),
                      "count": row.get("count") or 0, "tier": (tiers or {}).get(label) or row.get("tier"),
                      "effort": (pins or {}).get(label) or row.get("effort"), "model": model,
                      "observed": bool(seat_row.get("served_model"))})
    return seats


def _roster_mandates(seat: dict, roster: dict, catalog: dict) -> None:
    """A split seat (one lens across several seat keys) names its mandates only in the roster, so the outcomes it
    reports under keys the catalog lacks stand for its roster mandates. The least covering outcome wins."""
    pinned = [m for m in (roster.get(seat["seat"]) or {}).get("mandates") or [] if m in catalog]
    stray = {key: value for key, value in seat["mandates"].items() if key not in catalog}
    if not pinned or not stray:
        return
    outcome = max(stray.values(), key=OUTCOMES.index)
    seat["mandates"] = {key: value for key, value in seat["mandates"].items() if key in catalog}
    for mandate in pinned:
        seat["mandates"].setdefault(mandate, outcome)


def _catalog_keys(key: str, catalog: dict) -> list[str]:
    """A job mandate as catalog keys: a catalog key names itself, a lens key expands through `lens.seat_mandates`."""
    return [key] if key in catalog else (_seat_mandates(key) or [key])


def _sidecar_seats(prepared: dict, catalog: dict) -> list[dict]:
    """One seat per prepared coverage entry, its mandates keyed by catalog key.

    A job mandate's token is the seat's typed `checked.mandates` value when that map is valid
    (`review_result.mandate_map`), else `not-reached` for an `uncoveredMandates` key, `findings` when a source
    finding names it and `clean` otherwise; `_seat_outcome` bounds it by the seat status and the entry's
    `capabilityPartial` (absent reads as false). A lens key's catalog keys take the lens key's outcome."""
    sources = prepared.get("sources") or []
    seats = []
    for entry in prepared.get("coverage") or []:
        label = entry.get("label")
        checked = entry.get("checked") if isinstance(entry.get("checked"), dict) else {}
        missing = set(entry.get("uncoveredMandates") or [])
        keys = list(entry.get("mandates") or [])
        typed = _import("review_result").mandate_map(checked, keys)
        mandates = {}
        for mandate in keys:
            found = sum(1 for s in sources if s.get("seat") == label
                        and mandate in str((s.get("finding") or {}).get("agent", "")).split(", "))
            token = (typed[mandate] if typed else "not-reached" if mandate in missing
                     else "findings" if found else "clean")
            for key in _catalog_keys(mandate, catalog):
                mandates[key] = _seat_outcome(entry.get("status"), token, checked.get("stoppedAt"),
                                              entry.get("capabilityPartial") is True)
        seats.append({"seat": label, "status": entry.get("status"), "stopped": checked.get("stoppedAt"),
                      "mustReadEvidence": entry.get("mustReadEvidence"), "mustReadSpans": entry.get("mustReadSpans"),
                      "mandates": mandates, "targets": checked.get("toolsUsed") or [],
                      "gaps": [text for text in map(_import("review_result").gap_text, entry.get("gaps") or []) if text],
                      "called": None,
                      "count": entry.get("valid") or 0, "tier": entry.get("tier"), "effort": entry.get("effort"),
                      "model": entry.get("model"), "observed": bool(entry.get("modelObserved")),
                      "identityBasis": entry.get("identityBasis"), "record": entry.get("record")})
    return seats


def _family(model):
    if not model:
        return "unknown"
    try:
        return _import("model_registry").family(model) or "unknown"
    except Exception:
        return "unknown"


def _lens_tier(key):
    try:
        return _import("lens").seat_tier(key)
    except (Exception, SystemExit):
        return None


def _seat_mandates(key):
    try:
        return _import("lens").seat_mandates(key)
    except (Exception, SystemExit):
        return None


def _finding_mandate(finding: dict, catalog: dict):
    """The one catalog key a finding's `agent` names, else None.

    `agent` is a seat key (engine) or a comma-joined mandate list (sidecar): catalog keys name themselves, a
    seat key resolves through `lens.seat_mandates`. Two or more keys name none, so the caller names one."""
    agent = str(finding.get("agent") or "")
    parts = [part.strip() for part in agent.split(",") if part.strip()]
    keys = parts if parts and all(part in catalog for part in parts) else (_seat_mandates(agent) or [])
    return keys[0] if len(keys) == 1 else None


def _seat_findings(result: dict, sidecar: bool) -> dict:
    """seat key -> the findings it returned, from the sources that name their seat: a sidecar or dispatch
    source's `seat`, an engine source's `lens`. A model-written `agent` tag never stands in for a source, so a
    seat no source names reads as unmeasured unless it counted no findings (`_seat_yield`)."""
    out: dict = {}
    seat_field = "seat" if sidecar else "lens"
    for source in result.get("sources") or []:
        if isinstance(source, dict) and isinstance(source.get("finding"), dict) and source.get(seat_field):
            out.setdefault(source[seat_field], []).append(source["finding"])
    return out


def _seat_yield(by_seat: dict, seat: dict):
    """The seat's findings: its sources, else [] when it counted none, else None (the population is lost)."""
    found = by_seat.get(seat["seat"])
    return found if found is not None else ([] if not seat.get("count") else None)


def _finding_paths(findings: list, toplevel: Path) -> list[str]:
    """The repo paths the findings cite, one per finding (`_FINDING_LOCATION`, else the whole `file`)."""
    out = []
    for finding in findings:
        cited = str(finding.get("file") or "").strip()
        located = _FINDING_LOCATION.match(cited)
        path = located.group(1) if located else cited
        out.append(normalize_files(toplevel, [
            _import("verify_claims").resolve_path(toplevel, path)])[0] if path else "")
    return out


def _ledger_new(ctx: Ctx, earlier, mandates: list, paths: list[str]):
    """How many of `paths` no earlier panel ledger row reported; None when unmeasured.

    An earlier row reported path `f` when it is a panel row, older, on a `session:` or `pr:` subject, at a head
    that is an ancestor of or equal to HEAD, with outcome `findings`, sharing a seat mandate, and its
    `findingFiles` holds `f`. Script and inline rows carry no `findingFiles` and are skipped. A panel row passing
    every other clause without `findingFiles`, or a ledger that cannot be read (`earlier` None), makes the count
    None, never zero."""
    if earlier is None:
        return None
    stamp, keys, reported = earlier[0], set(mandates), set()
    for row in earlier[1]:
        if not (row.get("reviewer") == "panel" and str(row.get("ts") or "") < stamp
                and str(row.get("subject") or "").split(":", 1)[0] in ("session", "pr")
                and row.get("outcome") == "findings"
                and keys & set(row.get("seatMandates") or []) and row.get("head")
                and ctx.is_ancestor(row["head"])):
            continue
        if not isinstance(row.get("findingFiles"), list):
            return None
        reported.update(row["findingFiles"])
    return sum(1 for path in paths if path not in reported)


def _escape_candidates(repo: Path, findings: list, base_ref: str) -> list[dict]:
    """Findings whose cited line was last changed by a commit that landed before this work began."""
    base = _git(repo, ["merge-base", base_ref, "HEAD"])
    if base.returncode != 0:
        return []
    base_sha = base.stdout.decode().strip()
    out = []
    for finding in findings:
        match = _FINDING_LOCATION.match(str(finding.get("file", "")))
        if not match:
            continue
        path, line = match.group(1), match.group(2)
        blame = _git(repo, ["blame", "-L", f"{line},{line}", "--porcelain", "HEAD", "--", path])
        first = blame.stdout.decode("utf-8", errors="replace").split(None, 1)
        if blame.returncode != 0 or not first or not re.fullmatch(r"[0-9a-f]{40}", first[0]):
            continue
        if first[0] == base_sha or _is_ancestor(repo, first[0], base_sha):
            out.append({"id": finding.get("id"), "file": path, "location": match.group(0), "sha": first[0]})
    return out


def _write_escapes(repo: Path, stage: str, chosen: list[dict], author: str, catalog: dict,
                   base_ref: str, call: str) -> None:
    tool = os.environ.get("HARNESS_ESCAPE_LOG_TOOL") or str(TOOLS_DIR / "escape_log.py")
    for finding in chosen:
        mandate = finding.get("mandate") if finding.get("mandate") in catalog else "none"
        cmd = [sys.executable, tool, "add", "--repo", str(repo), "--writer", f"review:{stage}",
               "--sha", finding["sha"], "--should-have-caught", mandate, "--author-model", author,
               "--base-ref", base_ref, "--found-by", call, "--paths", finding["file"]]
        result = _run(cmd, repo)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ToolError(f"escape_log.py add failed for {finding['id']}: {detail}", 1)


class _RecordContext(NamedTuple):
    roster: dict
    ctx: object
    hashes: dict
    base: dict
    stamp: str
    rid: str
    catalog: dict
    measured: bool
    earlier: tuple | None


def _seat_rows(seat, seat_findings, record: _RecordContext) -> list:
    roster, ctx, hashes, base, stamp, rid, catalog, measured, earlier = record
    rows = []
    pinned = roster.get(seat["seat"]) or {}
    tier = seat["tier"] or pinned.get("tier") or _lens_tier(seat["seat"])
    effort = seat["effort"] or pinned.get("effort")
    keys = sorted(seat["mandates"])
    target_path = _import("review_result").target_path
    names = [normalize_files(ctx.toplevel, [
        _import("verify_claims").resolve_path(ctx.toplevel, rel)])[0]
        for target in seat["targets"] if (rel := target_path(target, ctx.toplevel))]
    called = seat.get("called")
    if called is not None:
        called = normalize_files(ctx.toplevel, [
            _import("verify_claims").resolve_path(ctx.toplevel, path) for path in called])
    diffed = _must_read_engaged(seat.get("mustReadSpans"), ctx.toplevel, hashes)
    engaged = {p: h for p, h in hashes.items() if _engaged(p, names, called, seat.get("gaps"), diffed)}
    rest = {p: h for p, h in hashes.items() if p not in engaged}
    cited = None if seat_findings is None else [p for p in _finding_paths(seat_findings, ctx.toplevel) if p]
    yielded = {"findingFiles": None if cited is None else sorted(set(cited))}
    if measured:
        yielded["seatLedgerNew"] = None if cited is None else _ledger_new(ctx, earlier, keys, cited)
    for mandate in keys:
        rule = rule_hash(mandate, catalog=catalog)
        if rule is None:
            print(f"mandate {mandate} is not in the catalog: no row for seat {seat['seat']}", file=sys.stderr)
            continue
        outcome = seat["mandates"][mandate]
        shared = dict(base, ts=stamp, mandate=mandate, ruleHash=rule, reviewer="panel",
                      family=_family(seat["model"]), model=seat["model"] or "unknown",
                      modelObserved=seat["observed"], identityBasis=seat.get("identityBasis"),
                      record=seat.get("record"), tier=tier, effort=effort, seat=seat["seat"],
                      seatMandates=keys, findings=seat["count"] if outcome == "findings" else 0,
                      mustReadEvidence=seat.get("mustReadEvidence"), **yielded)
        row_id = f"{rid}:{seat['seat']}:{mandate}"
        if outcome in COVERING_OUTCOMES:
            if engaged or outcome == "findings":
                rows.append(dict(shared, id=row_id, files=engaged, outcome=outcome))
            if rest or not engaged:
                rows.append(dict(shared, id=row_id + "#unengaged", files=rest, outcome="incomplete", findings=0))
        else:
            rows.append(dict(shared, id=row_id, files=hashes, outcome=outcome))
    return rows


def cmd_record(args, repo: Path) -> int:
    sources = [name for name in ("run", "seats_file", "sidecar_result", "result", "script", "inline")
               if getattr(args, name) is not None]
    if len(sources) != 1:
        raise ToolError("record needs exactly one of --run, --seats-file, --sidecar-result, --result, --script, --inline")
    if bool(args.seats_file) != bool(args.workflow_dir):
        raise ToolError("--seats-file and --workflow-dir require each other")
    _subject_kind(args.subject)
    catalog = load_catalog()
    if args.script or args.inline:
        if args.outcome not in OUTCOMES:
            raise ToolError("--script and --inline need --outcome " + "|".join(OUTCOMES))
        if args.inline and not args.mandate:
            raise ToolError("--inline needs --mandate <key>")
    if args.mandate is not None and args.mandate not in catalog:
        raise ToolError(f"--mandate {args.mandate} is not a review_mandates key (review_ledger.py keys)")
    if args.script and args.mandate is None and args.script not in catalog:
        raise ToolError(f"--script {args.script} names no review_mandates key: pass --mandate <key> "
                        f"(review_ledger.py keys)")
    escaped = {}
    for item in args.escaped or []:
        finding_id, sep, rest = item.partition("=")
        sha, _, named = rest.partition(":")
        if not sep or not finding_id or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise ToolError(f"--escaped takes <finding id>=<sha>[:<mandate key>], got {item!r}")
        if named and named not in catalog:
            raise ToolError(f"--escaped {item}: {named} is not a review_mandates key (review_ledger.py keys)")
        escaped[finding_id] = (sha, named or None)

    snapshot = None
    if args.head:
        found = _git(repo, ["rev-parse", "--verify", "--quiet", args.head + "^{commit}"])
        snapshot = found.stdout.decode().strip() if found.returncode == 0 else ""
        if not snapshot:
            raise ToolError(f"--head {args.head} names no commit in {repo}")
    ctx = Ctx(repo, args.base_ref, snapshot=snapshot)
    premise_commits = []
    if args.premise_commits:
        if snapshot is None:
            raise ToolError("--premise-commits needs --head to bind the frozen audit inputs")
        try:
            premise_commits = json.loads(Path(args.premise_commits).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"cannot read --premise-commits {args.premise_commits}: {exc}") from exc
        if not isinstance(premise_commits, list):
            raise ToolError("--premise-commits must be a JSON list of full commit IDs")
        if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha) for sha in premise_commits):
            raise ToolError("--premise-commits must contain full commit IDs")
        for sha in premise_commits:
            if not _is_ancestor(repo, sha, snapshot):
                raise ToolError(f"--premise-commits {sha} is not a commit ancestor of --head {snapshot}")
    if args.blobs:
        blobs = _load_json(args.blobs, "--blobs")
        if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha) for sha in blobs.values()):
            raise ToolError(f"--blobs {args.blobs} must map each path to the blob sha the review read")
        ctx.overrides.update({normalize_files(ctx.toplevel, [path])[0]: sha for path, sha in blobs.items()})
    files_in = list(args.files or [])
    if args.scope_json:
        files_in += [str(p) for p in _load_scope(args.scope_json).get("files") or []]
    files = normalize_files(ctx.toplevel, files_in)
    origins = None
    if args.inline == "fold" and not args.from_map:
        raise ToolError("--inline fold requires --from <map.json>")
    if args.from_map:
        origins = _load_json(args.from_map, "--from")
        if set(origins) != set(files) or not all(
                value is None or (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value))
                for value in origins.values()):
            raise ToolError("--from must map exactly --files to predecessor blob shas or null")
    hashes = {}
    for path, blob in ctx.hash_files(files).items():
        state = blob if blob is not None else ctx.deleted_hash(path)
        if state is not None:
            hashes[path] = state
        else:
            print(f"file not found, not recorded: {path}", file=sys.stderr)
    if files and not hashes:
        raise ToolError(f"none of the {len(files)} named files resolved in {ctx.toplevel}, so the row would cover "
                        f"nothing: name them relative to the checkout that holds them and record from there")
    for path, blob in hashes.items():
        if path not in ctx.overrides:
            continue
        home, inner = owning_repo(ctx.toplevel, path)
        present = _git(home, ["cat-file", "-e", blob + "^{blob}"])
        if present.returncode:
            written = _git(home, ["hash-object", "-w", "--", inner])
            if written.returncode or written.stdout.decode().strip() != blob:
                raise ToolError(f"--blobs: {blob} for {path} is not an object and disk does not hold its bytes")
    roster = {}
    if args.roster:
        roster = {seat["key"]: seat for seat in _load_roster(args.roster, catalog)}

    author = args.author_model or served_model()
    if author is None:
        author = "unknown"
        print("author model unknown: no readable session transcript (pass --author-model)", file=sys.stderr)
    base = {"v": 1, "stage": args.stage, "subject": args.subject, "head": ctx.head, "mergeBase": ctx.merge_base(),
            "premise_commits": premise_commits,
            "degraded": bool(args.degraded), "authorModel": author, "task": args.task,
            "session": os.environ.get("CLAUDE_CODE_SESSION_ID"), "noNewDecision": bool(args.no_new_decision),
            "call": uuid.uuid4().hex[:12]}
    if args.roster:
        document = json.loads(Path(args.roster).read_text(encoding="utf-8"))
        if isinstance(document, dict) and "reviewerSelection" in document:
            selection = document["reviewerSelection"]
            if not isinstance(selection, dict):
                raise ToolError("roster reviewerSelection must be an object")
            revision = (selection.get("revision") or {}).get("files")
            if selection.get("subject") != args.subject or not isinstance(revision, dict) or any(
                    path not in revision or revision[path] != blob for path, blob in hashes.items()):
                raise ToolError("roster reviewerSelection does not match the recorded subject revision")
            base["reviewerSelection"] = selection
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, findings = [], []

    if args.script or args.inline:
        name = args.script or args.inline
        mandate = args.mandate or args.script
        rule = rule_hash(mandate, catalog=catalog)
        reviewer = f"script:{name}" if args.script else f"inline:{name}"
        rows.append(dict(base, id=f"{name}:{stamp}:{mandate}", ts=stamp, mandate=mandate, ruleHash=rule,
                         reviewer=reviewer, family="none", model=None, modelObserved=False, tier=None, effort=None,
                         seat=name, seatMandates=[mandate], files=hashes, outcome=args.outcome,
                         findings=0, **({"from": origins} if origins is not None else {})))
    else:
        seat_named = bool(args.sidecar_result)
        if args.seats_file:
            journal, pins, tiers, served, agent_results = _live_journal_source(
                args.workflow_dir, args.seats_file, ctx.toplevel or repo)
            result = _dispatch_review(journal, agent_results, served, pins, tiers, ctx.toplevel or repo)
            seats, seat_named = _sidecar_seats(result, catalog), True
            rid = Path(args.workflow_dir).name
        elif args.run:
            journal, pins, tiers, served, agent_results = _journal_source(args.run, ctx.toplevel or repo)
            if journal.get("workflowName") == "dispatch":
                result = _dispatch_review(journal, agent_results, served, pins, tiers, ctx.toplevel or repo)
                seats, seat_named = _sidecar_seats(result, catalog), True
            else:
                result = dict(journal.get("result") or {})
                if not isinstance(result.get("sources"), list):
                    result["sources"] = _journal_sources(agent_results)
                seats = _engine_seats(result, f"run {args.run}", pins, tiers, served, agent_results)
            rid = args.run
        elif args.result:
            result = _load_json(args.result, "--result")
            seats = _engine_seats(result, args.result)
            rid = Path(args.result).stem
        else:
            result = _load_json(args.sidecar_result, "--sidecar-result")
            seats = _sidecar_seats(result, catalog)
            rid = Path(args.sidecar_result).stem
        for seat in seats:
            _roster_mandates(seat, roster, catalog)
        empty = sorted(seat["seat"] for seat in seats if not seat["mandates"])
        mute = [key for key in empty if (roster.get(key) or {}).get("mandates")]
        unrostered = [key for key in empty if key not in mute]
        if unrostered:
            print(f"seat(s) {', '.join(unrostered)} returned no mandate outcomes and record nothing", file=sys.stderr)
        if mute:
            raise ToolError(f"roster seat(s) {', '.join(mute)} returned no mandate outcomes: the engine maps a seat "
                            f"key to mandates through the lens catalog, so a key that names no lens records "
                            f"nothing; key each seat by its lens names joined with '+'", 1)
        findings = [f for f in result.get("findings") or [] if isinstance(f, dict)]
        by_seat = _seat_findings(result, seat_named)
        measured = args.stage == "pre-merge" or (args.stage == "pr-review" and args.subject.startswith("pr:"))
        earlier = None
        if measured:
            ledger_rows, _corrupt, problem = read_rows(ledger_path(repo))
            if not (problem and ledger_path(repo).exists()):
                earlier = (stamp, ledger_rows)
        record = _RecordContext(roster=roster, ctx=ctx, hashes=hashes, base=base, stamp=stamp, rid=rid,
                                catalog=catalog, measured=measured, earlier=earlier)
        for seat in seats:
            rows.extend(_seat_rows(seat, _seat_yield(by_seat, seat), record))
        if roster:
            cover = _make_cover(repo, args.base_ref, _subject_kind(args.subject), hashes=hashes)[0]
            for row in list(rows):
                if row["outcome"] not in COVERING_OUTCOMES:
                    continue
                depth = _depth_seats(list(roster.values()), row["mandate"])
                if not depth:
                    continue
                inherited = {path: blob for path in row["files"]
                             if (blob := cover.earlier_blob(row["mandate"], path, depth)) is not None}
                if inherited:
                    # Keep paths without a predecessor direct, never turn them into policy roots.
                    direct = {p: b for p, b in row["files"].items() if p not in inherited}
                    if direct:
                        rows.append(dict(row, id=row["id"] + "#direct", files=direct))
                    row["files"] = {p: row["files"][p] for p in inherited}
                    row["from"] = inherited

    candidates = _escape_candidates(repo, findings, args.base_ref) if findings else []
    known = {f.get("id") for f in findings}
    by_id = {finding.get("id"): finding for finding in findings}
    for finding_id, (sha, _named) in escaped.items():
        if finding_id not in known:
            raise ToolError(f"--escaped names finding {finding_id!r}, which the review did not return")
        if not _commit_present(repo, sha):
            raise ToolError(f"--escaped {finding_id}={sha}: {sha} names no commit in {repo}")
        if not _finding_paths([by_id[finding_id]], ctx.toplevel)[0]:
            raise ToolError(f"--escaped {finding_id}: the finding cites no file")
    path = ledger_path(repo)
    if rows and not _append_rows(path, rows):
        raise ToolError(f"cannot append to review ledger {path}", 1)
    for item in candidates:
        if item["id"] not in escaped:
            print(f"escape candidate: {item['id']} {item['location']} landed in {item['sha'][:7]}; "
                  f"record with --escaped {item['id']}=<sha>[:<mandate key>]")
    chosen = []
    for finding_id, (sha, named) in escaped.items():
        mandate = named or _finding_mandate(by_id[finding_id], catalog)
        if mandate is None:
            print(f"escape {finding_id}: its seat names no single mandate, so the row records none; "
                  f"name one with --escaped {finding_id}=<sha>:<mandate key>", file=sys.stderr)
        chosen.append({"id": finding_id, "sha": sha, "file": _finding_paths([by_id[finding_id]], ctx.toplevel)[0],
                       "mandate": mandate})
    _write_escapes(repo, args.stage, chosen, author, catalog, args.base_ref, base["call"])
    return 0


# ---- owed, rows, keys -------------------------------------------------------------------------------------


def is_doctrine_path(path: str) -> bool:
    return _import("harness_edit_skill_reminder", CLAUDE_DIR / "hooks").is_doctrine_path(path)


def uncovered_premises(cover: Cover, commits: list[dict], roster: list | None = None) -> dict[str, list[tuple]]:
    """Premise pairs keyed by exact supplied commit IDs; content coverage is checked separately.

    Commit dictionaries carry `sha` and the doctrine `files` supplied to the audit. The same row clauses
    and roster depth apply, except content (3): a commit's premises do not change with later file bytes.
    """
    owed = {}
    for commit in commits:
        pairs = []
        for path in commit["files"]:
            if cover.fold_applied("harness-quality", path, commit["sha"]):
                continue
            state = cover.pair_state("harness-quality", path, _depth_seats(roster or [], "harness-quality"),
                                     commit["sha"])
            if state:
                pairs.append((path, "harness-quality") + (("depth-unchecked",) if state == "depth-unchecked" else ()))
        if pairs:
            owed[commit["sha"]] = pairs
    return owed


def range_commits(repo: Path, base: str, rev: str, *, exclude_remotes: bool = False) -> list[dict]:
    """Commits in base..rev as {sha, subject, files}, oldest first; merges list only authored resolutions.

    A gitlink change lists the files its submodule changed between the pins (`gitlink_files`), not the gitlink:
    the pointer has no reviewable bytes, so it is reviewed through those files. A merge lists the submodule files
    its resolved pin changed against every parent's pin."""
    rng = f"{base}..{rev}"
    log = _git(repo, ["log", "--reverse", "--cc", "--no-renames", "--diff-filter=ACDMR", "--raw", "--no-abbrev",
                      "--format=@@%H\t%s", rng] + (["--not", "--remotes"] if exclude_remotes else []))
    if log.returncode != 0:
        raise ToolError(f"cannot list commits in {rng}: {log.stderr.decode('utf-8', errors='replace').strip()}")
    commits, current = [], None
    for line in log.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("@@"):
            sha, _, subject = line[2:].partition("\t")
            current = {"sha": sha, "subject": subject, "files": []}
            commits.append(current)
        elif line.startswith(":") and current is not None:
            meta, _, path = line.partition("\t")
            parents = len(meta) - len(meta.lstrip(":"))
            fields = meta.lstrip(":").split()
            modes, pins = fields[:parents + 1], fields[parents + 1:2 * parents + 2]
            if "160000" in modes:
                changed = set.intersection(*(gitlink_files(repo, path, old, pins[-1]) for old in pins[:-1]))
                current["files"].extend(sorted(changed))
            else:
                current["files"].append(path)
    return commits


def _admits_harness_seat(triage, repo: Path, sha: str) -> bool:
    """Whether a commit's triage admits the harness-quality seat. An unreadable Triage trailer admits it:
    the audit then names that commit instead of the whole range check going unavailable."""
    try:
        fields = triage.current(repo, rev=sha)
    except ValueError:
        return True
    return triage.admits("harness-quality-seat", fields, triage.rigor(root=repo)).admitted


def owed_range(repo: Path, base: str, rev: str, mandates: list[str], roster: list | None = None,
               paths=None, *, exclude_remotes: bool = False, coverage_base: str | None = None) -> list[dict]:
    """Uncovered commits in base..rev. `paths` filters paths with a predicate or a collection.

    With a path filter, judge final text states at rev and attribute them to the last changing commit.
    Every admitted doctrine commit also owes exact frozen premise coverage under harness-quality.
    Deletions use `deleted:<deleting commit>`; paths absent at both base and rev owe no text, only premises.
    Without one, preserve owed's per-commit semantics. Only the push guard excludes remote ancestors.
    State read errors raise LedgerUnreadable; missing ledger rows cover nothing.
    """
    commits = range_commits(repo, base, rev, exclude_remotes=exclude_remotes)
    premise_inputs = []
    if paths is not None:
        admits = paths if callable(paths) else lambda path: path in paths
        commits = [dict(commit, files=[path for path in commit["files"] if admits(path)]) for commit in commits]
        commits = [commit for commit in commits if commit["files"]]
        if "harness-quality" in mandates:
            sys.path.insert(0, str(TOOLS_DIR))
            try:
                triage = _import("triage")
                commits = [commit for commit in commits if _admits_harness_seat(triage, repo, commit["sha"])]
            finally:
                sys.path.remove(str(TOOLS_DIR))
        for commit in commits:
            commit["files"] = [path for path in commit["files"] if admits(path)]
            if "harness-quality" in mandates:
                doctrine = [path for path in commit["files"] if is_doctrine_path(path)]
                if doctrine:
                    premise_inputs.append(dict(commit, files=doctrine))
        last = {path: commit["sha"] for commit in commits for path in commit["files"]}
        for commit in commits:
            commit["files"] = [path for path in commit["files"] if last.get(path) == commit["sha"]]
    queries = [f"{rev if paths is not None else c['sha']}:{f}" for c in commits for f in c["files"]]
    blobs = {}
    if queries:
        check = _git(repo, ["cat-file", "--batch-check"], stdin=("\n".join(queries) + "\n").encode("utf-8"))
        replies = check.stdout.decode().splitlines()
        if check.returncode != 0 or len(replies) != len(queries):
            raise ToolError(f"git cat-file --batch-check answered {len(replies)} of {len(queries)} objects "
                            f"(exit {check.returncode}): {check.stderr.decode('utf-8', errors='replace').strip()}")
        for query, row in zip(queries, replies):
            fields = row.split()
            if len(fields) >= 2 and fields[1] == "blob":
                blobs[query] = fields[0]
            elif split_submodule(repo, query.partition(":")[2]):
                tip, _, file = query.partition(":")
                found = blob_at(repo, tip, file)
                if found is not None:
                    blobs[query] = found
    if not queries:
        return []
    path = ledger_path(repo)
    rows, _, problem = read_rows(path)
    if problem and path.exists():
        raise LedgerUnreadable(f"{path}: {problem}")
    escapes, escape_problem = read_escapes(repo)
    if escape_problem:
        raise LedgerUnreadable(escape_problem)
    cover = Cover(Ctx(repo, coverage_base or base, snapshot=rev), load_catalog(), rows, escapes, "session")
    base_ctx = Ctx(repo, coverage_base or base, snapshot=base)
    premise_pairs = uncovered_premises(cover, premise_inputs, roster)
    owed = []
    for commit in commits:
        tip = rev if paths is not None else commit["sha"]
        cover.ctx.overrides = {}
        for file in commit["files"]:
            blob = blobs.get(f"{tip}:{file}")
            if blob is not None:
                cover.ctx.overrides[file] = blob
            elif paths is None or base_ctx.hash(file) is not None:
                state = Ctx(repo, coverage_base or base, snapshot=tip).deleted_hash(file)
                if state is not None:
                    cover.ctx.overrides[file] = state
        files = list(cover.ctx.overrides)
        pairs = uncovered_pairs(cover, files, mandates, roster) if files else []
        premises = premise_pairs.get(commit["sha"], [])
        if pairs or premises:
            owed.append(dict(commit, pairs=list(dict.fromkeys(pairs + premises))))
    return owed


def cmd_owed(args, repo: Path) -> int:
    roster = _load_roster(args.roster, load_catalog()) if args.roster else None
    mandates = _resolve_mandates(args.mandates, roster)
    if not mandates:
        print("no candidate mandates declared: nothing is owed (review_ledger.py keys --stage candidate)",
              file=sys.stderr)
        return 0
    upstream = _git(repo, ["rev-parse", "--verify", "-q", "@{u}"])
    no_upstream = upstream.returncode != 0
    base = args.base_ref if no_upstream else "@{u}"
    for commit in owed_range(repo, base, "HEAD", mandates, roster, is_doctrine_path if args.doctrine else None,
                             coverage_base=args.base_ref):
        unchecked = all(len(pair) == 3 for pair in commit["pairs"])
        print(f"{commit['sha'][:7]} {commit['subject']}" + (" (no upstream)" if no_upstream else "")
              + (" (depth-unchecked: no --roster)" if unchecked else ""))
    return 0


def whole_scope(cover: Cover, subject: str, files: list[str], mandates: list[str], roster: list) -> dict:
    """One candidate panel recording that covers the whole current scope, never a task/round union.

    `call` is the recorder's identity shared by every seat in that recording. Legacy rows without it
    remain usable by ordinary pair queries but cannot establish this stronger whole-scope fact.
    Each candidate is checked in isolation, including predecessor resolution, under all coverage clauses.
    """
    groups = {}
    for row in cover.rows:
        call = row.get("call")
        if (row.get("subject") == subject and row.get("stage") == "candidate"
                and row.get("reviewer") == "panel" and isinstance(call, str) and call and row.get("head")):
            groups.setdefault((call, row["head"]), []).append(row)
    runs = []
    for (call, head), rows in reversed(list(groups.items())):
        candidate = Cover(cover.ctx, cover.catalog, rows, cover.escapes, _subject_kind(subject),
                          claude_dir=cover.claude_dir)
        pairs = uncovered_pairs(candidate, files, mandates, roster)
        runs.append({"call": call, "head": head, "uncovered": [list(pair) for pair in pairs]})
    complete = next((run for run in runs if not run["uncovered"]), None) if files and mandates else None
    return {"wholeScope": complete is not None, "call": complete["call"] if complete else None,
            "files": files, "mandates": mandates, "runs": runs}


def cmd_whole_scope(args, repo: Path) -> int:
    kind = _subject_kind(args.subject)
    roster = _load_roster(args.roster, load_catalog()) if args.roster else []
    mandates = _resolve_mandates(args.mandates, roster or None)
    cover, _ = _make_cover(repo, args.base_ref, kind)
    files_in = list(args.files or [])
    if args.scope_json:
        scope = _load_scope(args.scope_json)
        if not isinstance(scope.get("files"), list) or not all(isinstance(p, str) for p in scope["files"]):
            raise ToolError("--scope-json needs a files list of paths")
        files_in += scope["files"]
    files = normalize_files(cover.ctx.toplevel, files_in)
    if not files:
        raise ToolError("whole-scope needs --files or --scope-json with a nonempty files list")
    print(json.dumps(whole_scope(cover, args.subject, files, mandates, roster)))
    return 0


def cmd_rows(args, repo: Path) -> int:
    if not (args.subject or args.task or args.model):
        raise ToolError("rows needs at least one of --subject, --task, --model")
    rows, corrupt, problem = read_rows(ledger_path(repo))
    if problem:
        print(problem, file=sys.stderr)
    if corrupt:
        print(f"{corrupt} corrupt ledger line(s) skipped", file=sys.stderr)
    picked = [row for row in rows
              if (not args.subject or row.get("subject") == args.subject)
              and (not args.task or row.get("task") == args.task)
              and (not args.model or row.get("model") == args.model)]
    if args.units:
        units: dict = {}
        top = Path(_git(repo, ["rev-parse", "--show-toplevel"]).stdout.decode().strip() or repo)
        for row in picked:
            if row.get("reviewer") != "panel" or row.get("stage") != "candidate":
                continue
            unit = f"task:{row['task']}" if row.get("task") else f"run:{str(row.get('id', '')).partition(':')[0]}"
            units.setdefault(unit, set()).update(f for f in (row.get("files") or {}) if (top / f).exists())
        for unit in sorted(u for u in units if units[u]):
            print(f"{unit}\t{','.join(sorted(units[unit]))}")
    elif args.json:
        print(json.dumps(picked))
    else:
        for row in picked:
            print("\t".join(str(row.get(k)) for k in ("id", "stage", "subject", "mandate", "outcome", "model")))
    return 0


def cmd_keys(args, repo: Path) -> int:
    if args.stage:
        print("\n".join(candidate_keys()))
        return 0
    catalog = load_catalog()
    if args.check:
        claude_dir = _claude_dir()
        problems = check_keys(catalog, claude_dir, _lens_sets())
        admission = check_admission(_audit_lenses(), claude_dir)
        if admission is None:
            print(f"admission cross-check did not run: {claude_dir / ADMISSION_DOC} absent", file=sys.stderr)
        problems += admission or []
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    print("\n".join(sorted(catalog)))
    return 0


# ---- CLI --------------------------------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(f"{message}\n\n{USAGE.rstrip()}")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="review_ledger.py", usage=USAGE,
                     formatter_class=argparse.RawDescriptionHelpFormatter,
                     description="The review ledger and PR review scope.")
    subs = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def common(sub, repo=False):
        sub.add_argument("--base-ref", default="origin/main")
        if repo:
            sub.add_argument("--repo", default=None)

    scope = subs.add_parser("scope")
    scope.add_argument("pr", type=int)
    common(scope, repo=True)
    project = subs.add_parser("project")
    project.add_argument("pr", type=int)
    project.add_argument("--scope-json", required=True)
    project.add_argument("--out", required=True)
    project.add_argument("--date", default=None)
    project.add_argument("--waive", action="store_true")
    common(project, repo=True)

    plan = subs.add_parser("plan")
    plan.add_argument("--stage", required=True, choices=STAGES)
    plan.add_argument("--subject", required=True)
    plan.add_argument("--roster", required=True)
    plan.add_argument("--task", help="task holding accepted subject jobs; absent lineage remains unknown")
    plan.add_argument("--files", nargs="+")
    plan.add_argument("--files-from", help="a file of paths, one per line; the same list as --files")
    plan.add_argument("--scope-json")
    plan.add_argument("--ignore-ledger", action="store_true")
    plan.add_argument("--design")
    plan.add_argument("--no-new-decision", action="store_true")
    common(plan)

    record = subs.add_parser("record")
    record.add_argument("--stage", required=True, choices=STAGES)
    record.add_argument("--subject", required=True)
    record.add_argument("--run")
    record.add_argument("--seats-file", help="label-to-launched-seat map for an in-run panel")
    record.add_argument("--workflow-dir", help="live subagents/workflows/<run id> directory; requires --seats-file")
    record.add_argument("--sidecar-result")
    record.add_argument("--result")
    record.add_argument("--script")
    record.add_argument("--inline")
    record.add_argument("--outcome")
    record.add_argument("--mandate")
    record.add_argument("--files", nargs="+")
    record.add_argument("--files-from", help="a file of paths, one per line; the same list as --files")
    record.add_argument("--scope-json")
    record.add_argument("--head", help="the commit the review read: rows hash its blobs and carry it as their head")
    record.add_argument("--blobs", help="a JSON map of path to the blob sha the review read (a fold's pre-fold "
                                        "bytes): rows hash those paths as these blobs, never the disk")
    record.add_argument("--premise-commits", help="a frozen JSON list of full commit IDs read by the audit; "
                                                "requires --head and never infers commits from current HEAD")
    record.add_argument("--from", dest="from_map", help="predecessor blobs per path; required for --inline fold")
    record.add_argument("--roster")
    record.add_argument("--author-model")
    record.add_argument("--task")
    record.add_argument("--degraded", action="store_true")
    record.add_argument("--no-new-decision", action="store_true")
    record.add_argument("--escaped", action="append")
    common(record)

    uncovered = subs.add_parser("uncovered")
    uncovered.add_argument("--files", nargs="+")
    uncovered.add_argument("--files-from", help="a file of paths, one per line; the same list as --files")
    uncovered.add_argument("--mandates", required=True)
    uncovered.add_argument("--roster")
    common(uncovered)

    owed = subs.add_parser("owed")
    owed.add_argument("--roster")
    owed.add_argument("--mandates", default="candidate")
    owed.add_argument("--doctrine", action="store_true")
    common(owed)

    whole = subs.add_parser("whole-scope")
    whole.add_argument("--subject", required=True)
    whole.add_argument("--files", nargs="+")
    whole.add_argument("--files-from", help="a file of paths, one per line; the same list as --files")
    whole.add_argument("--scope-json")
    whole.add_argument("--mandates", default="candidate")
    whole.add_argument("--roster")
    common(whole)

    rows = subs.add_parser("rows")
    rows.add_argument("--subject")
    rows.add_argument("--task")
    rows.add_argument("--model")
    rows.add_argument("--json", action="store_true")
    rows.add_argument("--units", action="store_true")

    keys = subs.add_parser("keys")
    keys.add_argument("--check", action="store_true")
    keys.add_argument("--stage", choices=("candidate",))
    return parser


def main(argv=None) -> int:
    try:
        args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        if getattr(args, "files_from", None) is not None:
            args.files = (args.files or []) + load_files_from(args.files_from)
        if args.command == "uncovered" and args.files is None:
            raise ToolError("uncovered needs --files or --files-from")
        repo = Path(getattr(args, "repo", None) or Path.cwd())
        if args.command == "scope":
            sys.stdout.write(json.dumps(build_scope(repo, args.base_ref, args.pr)) + "\n")
            return 0
        if args.command == "project":
            date = args.date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            write_record(repo, args.pr, args.scope_json, args.out, date, args.waive)
            return 0
        handler = {"plan": cmd_plan, "record": cmd_record, "uncovered": cmd_uncovered, "owed": cmd_owed,
                   "whole-scope": cmd_whole_scope, "rows": cmd_rows, "keys": cmd_keys}[args.command]
        return handler(args, repo)
    except LedgerUnreadable as exc:
        print(exc, file=sys.stderr)
        return 2
    except ToolError as exc:
        print(exc, file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
