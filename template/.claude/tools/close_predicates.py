#!/usr/bin/env python3
"""Deterministic predicates that gate `/session_end` phases: a phase runs when its input exists.

    close_predicates.py [--session <sid>] [--only <stem>... | --scope] [--json <out>] [--repo <path>] [--transcript <path>]

Output is `{"<stem>": {"value": true | false | null, "predicate": "<text>", "basis": {...}}}`.
`false` skips the phase and the skip records this predicate (`session_end_check.py --record <stem>
--status skipped --predicate <file>`). `true` and `null` run the phase: an input the predicate cannot
read is `null`, never `false`. A predicate that raises is `null` with `basis.error`; the tool still exits 0.
No `--only` evaluates every gated stem; an unknown stem exits 2 with usage.

`--scope` is the one owner of the session's scope: it writes `{"identity", "commits", "files", "basis",
"error"}` and exits 0, or exits 1 with `files: null` and the reason in `error` when the scope cannot be read.
The file-set predicates read the same scope.

A session's commits are, in order: (1) every commit on any ref whose message carries the session's own
`Claude-Session:` trailer line (the transcript's `remote_session_change` attachment) and whose committer date
lies inside the transcript's timestamps, open-ended for the live session, since one trailer URL spans the
sessions a `/clear` chains; (2) the `[<branch> <hash>] <subject>` summaries shell results print and every
commit of an `Updating <a>..<b>` range. With no trailer source, a transcript whose shell calls issue more
commit, merge, cherry-pick or revert invocations (`hooks/_git_commit.commit_invocations`, a denied or failed
one included) than it prints summaries and ranges fails closed: the scope is null and
`basis.unmatched_invocations` counts the difference. A peer session's commits carry another trailer and print
in another transcript, so they never count. The changed files are those commits' files united with the
digest's Edit/Write counter. The live `CLAUDE_CODE_SESSION_ID` also counts the uncommitted paths its
transcript names, in a tool input or a tool result, in either slash direction; a peer's dirty file the session
never named does not count. A failing git call, or a transcript that cannot be read, makes the scope null; a
hash the repo no longer has is skipped and counted in `basis.missing_commits`.
"""
import argparse
import importlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
HOOKS_DIR = TOOLS_DIR.parent / "hooks"
DEFAULT_REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR") or TOOLS_DIR.parent.parent)

# Files under one existing subsystem that warrant a refresh of its one-line summary.
DENSITY_THRESHOLD = 5


def _load_tool(name):
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    return importlib.import_module(name)


_WORKTREE_PREFIX = re.compile(r"^\.claude/worktrees/[^/]+/")

# The commit summary a shell prints: `[<branch> <hash>] <subject>`, `[<branch> (root-commit) <hash>] <subject>`.
_COMMIT_SUMMARY = re.compile(r"^\[[^\]]*? ([0-9a-f]{7,40})\]", re.M)
# The range a merge or fast-forward prints: `Updating <a>..<b>`.
_FAST_FORWARD = re.compile(r"^Updating ([0-9a-f]{4,40})\.\.([0-9a-f]{4,40})\s*$", re.M)
# Only a shell's result carries a commit summary; every tool's input and result can name a path.
_SHELL_TOOLS = frozenset({"Bash", "BashOutput", "PowerShell", "pwsh"})


def _names_path(needle, line):
    """Whether `line` holds `needle` as a whole path: no path character after it and none before it but a
    directory separator (an absolute or worktree prefix), and a name with a letter or digit, so a stray file
    called `-` never matches a bare dash."""
    return bool(re.search(r"[0-9A-Za-z]", needle)) and re.search(
        r"(?<![\w.~-])" + re.escape(needle) + r"(?![\w./~-])", line) is not None


def _ignored(repo, paths):
    """The `paths` git ignores: a session's scratch writes are not reviewable change."""
    # check-ignore dies on a path inside a submodule, and the superproject's ignore rules never reach one.
    staged = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "-z"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    if staged.returncode != 0:
        raise RuntimeError(f"git ls-files exited {staged.returncode}")
    gitlinks = [entry.split("\t", 1)[1] + "/" for entry in staged.stdout.split("\0") if entry.startswith("160000 ")]
    paths = [p for p in paths if not any(p.startswith(link) for link in gitlinks)]
    if not paths:
        return set()
    result = subprocess.run(["git", "-C", str(repo), "check-ignore", "--stdin", "-z"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=60,
                            input="\0".join(sorted(paths)) + "\0")
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore exited {result.returncode}")
    return set(filter(None, result.stdout.split("\0")))


def _repo_relative(path):
    return _WORKTREE_PREFIX.sub("", path, count=1)


class Ctx:
    def __init__(self, repo, session=None, transcript=None, live=False):
        self.repo = Path(repo)
        self.session = session
        self.live = live
        self._transcript = transcript
        self._digest = None
        self._changed = None
        self.git_basis = {}
        self.commits = None

    def transcript(self):
        if self._transcript is None and self.session:
            self._transcript = _load_tool("session_end_check").transcript_for(self.session, str(self.repo))
        return self._transcript

    def sid8(self):
        transcript = self.transcript()
        if transcript:
            return Path(transcript).stem[:8]
        return self.session[:8] if self.session else None

    def digest(self):
        """(digest dict, None) or (None, reason)."""
        if self._digest is None:
            sid8 = self.sid8()
            if not sid8:
                self._digest = (None, "no session identity")
            else:
                path = self.repo / "logs" / f"session_digest_{sid8}.json"
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._digest = (data, None) if isinstance(data, dict) else (None, f"{path.name} is not an object")
                except FileNotFoundError:
                    self._digest = (None, f"{path.name} absent")
                except (OSError, ValueError) as exc:
                    self._digest = (None, f"{path.name} unreadable: {exc}")
        return self._digest

    def git_changes(self):
        """(repo-relative paths git records for this session, None) or (None, reason).

        Fills `commits` with the session's full hashes and `git_basis` with the counts behind the answer.
        """
        self.git_basis = {"identity": "transcript", "commits": 0, "missing_commits": 0, "commit_invocations": 0,
                          "unmatched_invocations": 0, "uncommitted_named": 0}
        transcript = self.transcript()
        if not transcript:
            return None, "transcript not found"
        dirty = set()
        if self.live:
            try:
                dirty = {p for p in _status_paths(_git(self.repo, "status", "--porcelain=v1", "-z",
                                                       "--untracked-files=all")) if p}
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                return None, str(exc)
        try:
            facts = _transcript_facts(transcript, dirty)
            if not facts.records:
                return None, "transcript holds no record"
            trailed = []
            if facts.trailers and facts.first is not None:
                self.git_basis["identity"] = "trailer"
                trailed = _trailer_commits(self.repo, facts.trailers, facts.first, None if self.live else facts.last)
            printed = len(facts.commits) + len(facts.spans)
            self.git_basis["commit_invocations"] = facts.invocations
            if self.git_basis["identity"] != "trailer" and facts.invocations > printed:
                unmatched = facts.invocations - printed
                self.git_basis["unmatched_invocations"] = unmatched
                return None, (f"{unmatched} of {facts.invocations} commit invocations printed no commit summary or "
                              f"range, and no Claude-Session trailer names the session's commits")
            paths = self._commit_files(facts.commits + trailed, facts.spans)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return None, str(exc)
        self.git_basis["uncommitted_named"] = len(facts.named)
        return {p for p in paths | facts.named if p}, None

    def _commit_files(self, commits, spans):
        """The files of the session's commits and ranges; a token the repo no longer holds is skipped."""
        tokens = list(dict.fromkeys(commits + [t for span in spans for t in span]))
        paths = set()
        self.commits = []
        if not tokens:
            return paths
        found = _commits_present(self.repo, tokens)
        self.git_basis["commits"] = len(found)
        self.git_basis["missing_commits"] = len(tokens) - len(found)
        kept = [c for c in dict.fromkeys(commits) if c in found]
        ranges = [f"{a}..{b}" for a, b in dict.fromkeys(spans) if a in found and b in found]
        hashes = []
        if kept:
            paths |= _git_files(self.repo, ["--no-walk"], kept)
            hashes += _git_hashes(self.repo, ["--no-walk"], kept)
        if ranges:
            paths |= _git_files(self.repo, [], ranges)
            hashes += _git_hashes(self.repo, [], ranges)
        self.commits = list(dict.fromkeys(hashes))
        return paths

    def changed_files(self):
        """(repo-relative posix paths, None) or (None, reason): the digest's files_modified_counts united with
        the session's commits and named dirty files; a worktree path counts as its repo-relative path."""
        if self._changed is None:
            self._changed = self._read_changed_files()
        return self._changed

    def _read_changed_files(self):
        data, reason = self.digest()
        if data is None:
            return None, reason
        counts = data.get("files_modified_counts")
        if not isinstance(counts, dict):
            return None, "digest has no files_modified_counts"
        committed, reason = self.git_changes()
        if committed is None:
            return None, reason
        root = str(self.repo.resolve())
        paths = set(committed)
        for raw in counts:
            try:
                paths.add(Path(os.path.relpath(raw, root)).as_posix() if os.path.isabs(raw) else raw)
            except ValueError:
                continue
        kept = {_repo_relative(p) for p in paths if not p.startswith("..")}
        try:
            return sorted(kept - _ignored(self.repo, kept)), None
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return None, str(exc)

    def scope(self):
        """The session's commits and changed files: the one answer every close surface reads."""
        files, reason = self.changed_files()
        return {"identity": self.git_basis.get("identity"), "commits": self.commits if files is not None else None,
                "files": files, "basis": dict(self.git_basis), "error": reason}


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _timestamp(value):
    """An aware datetime from an ISO-8601 string (a naive one is UTC), or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _Facts:
    """What one pass over a transcript shows about the session's commits."""

    def __init__(self):
        self.commits, self.spans, self.named, self.records = [], [], set(), 0
        self.trailers, self.invocations, self.first, self.last = set(), 0, None, None


def _commit_invocations(command, cwd):
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    return len(importlib.import_module("_git_commit").commit_invocations(command, cwd))


def _transcript_facts(transcript, candidates):
    """One pass over a transcript: its commit summaries, `Updating` ranges, named candidates, record count,
    trailer lines, shell commit invocations, and first and last timestamps.

    A shell result's text carries the commit summaries and ranges; a tool input or result of any tool can
    name a candidate path. `candidates` is the live session's dirty set; each is matched as given and as its
    repo-relative form, against the line with each JSON-escaped backslash read as `/`, so a worktree-prefixed
    or backslash-spelled path is named by either spelling.
    """
    facts = _Facts()
    named = set()
    needles = {n for path in candidates for n in (path, _repo_relative(path))}
    tools = {}
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.lstrip().startswith("{"):
                continue
            facts.records += 1
            tool_traffic = '"tool_use"' in line or '"tool_result"' in line
            if needles and tool_traffic:
                slashed = line.replace("\\\\", "/")
                for needle in list(needles):
                    if needle in slashed and _names_path(needle, slashed):
                        named.add(needle)
                        needles.discard(needle)
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            stamp = _timestamp(record.get("timestamp"))
            if stamp is not None:
                facts.first = stamp if facts.first is None else min(facts.first, stamp)
                facts.last = stamp if facts.last is None else max(facts.last, stamp)
            attachment = record.get("attachment")
            if (record.get("type") == "attachment" and isinstance(attachment, dict)
                    and attachment.get("type") == "remote_session_change"
                    and str(attachment.get("commit", "")).startswith("Claude-Session: ")):
                facts.trailers.add(attachment["commit"].strip())
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not tool_traffic or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tools[block.get("id")] = block.get("name")
                    command = (block.get("input") or {}).get("command") if isinstance(block.get("input"), dict) else None
                    if block.get("name") in _SHELL_TOOLS and isinstance(command, str):
                        facts.invocations += _commit_invocations(command, record.get("cwd") or ".")
                elif block.get("type") == "tool_result" and tools.get(block.get("tool_use_id")) in _SHELL_TOOLS:
                    text = _result_text(block.get("content"))
                    facts.commits.extend(_COMMIT_SUMMARY.findall(text))
                    facts.spans.extend(_FAST_FORWARD.findall(text))
    facts.named = {p for p in candidates if p in named or _repo_relative(p) in named}
    return facts


def session_trailers(transcript):
    """The `Claude-Session:` trailer lines the transcript's `remote_session_change` attachments carry."""
    return set(_transcript_facts(transcript, ()).trailers)


def _trailer_commits(repo, trailers, start, end):
    """Every commit on any ref whose message holds one of `trailers` as a whole line and whose committer date
    lies in [start, end]; an `end` of None leaves the window open."""
    found = []
    low = int(start.timestamp())
    high = None if end is None else int(end.timestamp()) + 1
    for trailer in sorted(trailers):
        listed = _git(repo, "log", "--all", "--fixed-strings", f"--grep={trailer}", "--format=%x01%H %ct%n%B")
        for entry in listed.split("\x01")[1:]:
            head, _, body = entry.partition("\n")
            sha, _, stamp = head.partition(" ")
            when = int(stamp)
            if trailer in (l.strip() for l in body.splitlines()) and low <= when and (high is None or when <= high):
                found.append(sha)
    return found


def _status_paths(porcelain):
    fields = iter(porcelain.split("\0"))
    for entry in fields:
        if len(entry) > 3:
            yield entry[3:]
            if entry[0] in "RC":
                next(fields, None)

def predicate_autolearn(ctx):
    data, reason = ctx.digest()
    if data is None:
        return None, {"error": reason}
    friction = data.get("friction") or []
    signalled = [m for m in (data.get("user_messages") or []) if isinstance(m, dict) and m.get("signals")]
    return bool(friction or signalled), {"friction": len(friction), "prompt_signals": len(signalled)}


def predicate_routing_audit(ctx):
    """Rows whose `ts` postdates the stats file's `generated_at`; with no stats file yet, every row. A row whose
    `ts` cannot be read cannot be ordered, so with no newer row it makes the answer null."""
    aggregate = _load_tool("aggregate_routing_audit")
    log = aggregate.LOG_PATH.as_posix()
    path = ctx.repo / aggregate.LOG_PATH
    if not path.exists():
        return False, {"log": log, "source": "absent"}
    since = None
    stats = ctx.repo / aggregate.STATS_PATH
    if stats.exists():
        try:
            data = json.loads(stats.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, {"log": log, "error": f"{aggregate.STATS_PATH.as_posix()} unreadable: {exc}"}
        since = _timestamp(data.get("generated_at") if isinstance(data, dict) else None)
        if since is None:
            return None, {"log": log, "error": f"{aggregate.STATS_PATH.as_posix()} has no readable generated_at"}
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"log": log, "error": f"unreadable: {exc}"}
    fresh = undated = 0
    for line in lines:
        if since is None:
            fresh += 1
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        stamp = _timestamp(row.get("ts") if isinstance(row, dict) else None)
        if stamp is None:
            undated += 1
        elif stamp > since:
            fresh += 1
    value = True if fresh else (None if undated else False)
    return value, {"log": log, "source": "present", "rows": len(lines), "rows_since": fresh, "undated": undated,
                   "since": since.isoformat() if since else None}


def predicate_orchestration_metrics(ctx):
    metrics = _load_tool("orchestration_metrics")
    session_dir = metrics.find_session_dir(ctx.session)
    if not session_dir:
        return None, {"error": "no session directory found"}
    workflows = Path(session_dir) / "workflows"
    runs = len(list(workflows.glob("*.json"))) if workflows.is_dir() else 0
    sidecar = len(metrics.collect_session_sidecar(session_dir))
    return bool(runs or sidecar), {"session_dir": str(session_dir), "workflow_runs": runs, "sidecar_rows": sidecar}


def predicate_hot_set_review(ctx):
    census = _load_tool("load_census")
    report = census.build_report(ctx.repo / ".claude", profile_hooks_flag=False, need_budget_hooks=True)
    if report.get("source_errors"):
        return None, {"error": "census source errors", "source_errors": report["source_errors"]}
    rows = census.over_baseline(report)
    unmeasured = report.get("prompt_hooks_unmeasured") or []
    if unmeasured and not rows:
        return None, {"error": "prompt hooks unmeasured", "prompt_hooks_unmeasured": unmeasured}
    return bool(rows), {"rows": rows}


_PATHS_LIST = re.compile(r"reserved_paths:\s*\[([^\]]*)\]")
_PATHS_ROW = re.compile(r"^\s+paths:\s*\[([^\]]*)\]", re.M)
_SUBSYSTEM_ID = re.compile(r"^\s*-\s*id:\s*(\S+)", re.M)


def _split_list(raw):
    return [t.strip() for t in raw.split(",") if t.strip()]


def _registry(ctx):
    """(top-level roots, reserved roots, {subsystem id: paths}) from the registry's fenced YAML, or None."""
    path = ctx.repo / ".claude" / "skills" / "project_subsystems" / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    head, sep, body = text.partition("subsystems:")
    if not sep:
        return None
    reserved = _PATHS_LIST.search(head)
    groups = [_split_list(m.group(1)) for m in _PATHS_ROW.finditer(body)]
    ids = _SUBSYSTEM_ID.findall(body)
    roots = {t.split("/")[0] for group in groups for t in group}
    reserved_roots = {r.rstrip("/") for r in _split_list(reserved.group(1))} if reserved else set()
    return roots, reserved_roots, dict(zip(ids, groups))


def predicate_sync_subsystems(ctx):
    files, reason = ctx.changed_files()
    if files is None:
        return None, {"error": reason, **ctx.git_basis}
    registry = _registry(ctx)
    if registry is None:
        return None, {"error": "project_subsystems registry absent"}
    roots, reserved, subsystems = registry
    shape = sorted({p.split("/")[0] for p in files if "/" in p and p.split("/")[0] not in roots | reserved})
    density = {}
    for sid, paths in subsystems.items():
        count = sum(1 for f in files if any(f.startswith(p) for p in paths))
        if count >= DENSITY_THRESHOLD:
            density[sid] = count
    return bool(shape or density), {"shape": shape, "density": density, "changed_files": len(files),
                                    **ctx.git_basis}


def predicate_regression_gate(ctx):
    files, reason = ctx.changed_files()
    if files is None:
        return None, {"error": reason, **ctx.git_basis}
    changed = sorted(f for f in files if f.endswith(".cs"))
    return bool(changed), {"cs_files": len(changed), "sample": changed[:5], **ctx.git_basis}


_REINDEX_SUFFIXES = frozenset({"md", "markdown", "rst", "txt", "tscn", "scn", "tres", "res",
                               "json", "yaml", "yml", "gdshader"})
_WORKLOG_PATH = re.compile(r"(?:^|[\\/])(?:TODO[\\/]Worklog(?:-Archive)?\.md|"
                           r"\.claude[\\/]worklog-(?:titles|pending)\.md)\b", re.I)
_DEFERRAL = re.compile(r"\b(?:defer(?:red|ring)?|park(?:ed|ing)|postpon(?:e|ed|ing)|follow[- ]up)\b", re.I)


def predicate_reindex(ctx):
    files, reason = ctx.changed_files()
    if files is None:
        return None, {"error": reason, **ctx.git_basis}
    changed = sorted(f for f in files if Path(f).suffix.lower().lstrip(".") in _REINDEX_SUFFIXES
                     or f.startswith(".claude/auto-memory/"))
    return bool(changed), {"indexed_files": len(changed), "sample": changed[:5], **ctx.git_basis}


def predicate_worklog_sweep(ctx):
    """Gate each half independently: worklog paths touched, and assistant-recorded deferral signals.

    Deferral wording is a candidate signal, not a disposition; the add-sweep judges it. Missing or
    malformed evidence leaves the affected half null, so that half runs rather than silently skipping.
    """
    files, reason = ctx.changed_files()
    completion = None if files is None else any(_WORKLOG_PATH.search(f) for f in files)
    basis = {"completion": completion, "add": None, "deferral_signals": 0}
    if reason:
        basis["scope_error"] = reason
    transcript = ctx.transcript()
    if not transcript:
        return (True if completion else None), {**basis, "error": "transcript not found"}
    records = malformed = deferrals = 0
    with open(transcript, encoding="utf-8", errors="strict") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            records += 1
            if record.get("type") != "assistant" or record.get("isSidechain"):
                continue
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    deferrals += bool(_DEFERRAL.search(block["text"]))
                elif block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    inp = block["input"]
                    targets = [inp.get(k) for k in ("file_path", "path", "command")]
                    if any(isinstance(t, str) and _WORKLOG_PATH.search(t) for t in targets):
                        completion = True
                    if (block.get("name", "").split(".")[-1] == "Skill"
                            and inp.get("skill") == "worklog"):
                        completion = True
    if (malformed or not records) and completion is not True:
        completion = None
    add = True if deferrals else (None if malformed or not records else False)
    basis.update(completion=completion, add=add, deferral_signals=deferrals,
                 records=records, malformed=malformed)
    value = True if completion or add else (None if completion is None or add is None else False)
    return value, basis


_PLAN_REF = re.compile(r"\.claude[\\/]plans[\\/]([A-Za-z0-9_.-]+\.md)")
_ROADMAP_FILE = re.compile(r"[\\/]roadmap\.md(?![A-Za-z0-9_])", re.I)
_UPDATE_ROADMAP = re.compile(r"/update_roadmap(?!\.md)")


def _tool_inputs(transcript):
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            message = rec.get("message") if isinstance(rec, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if (not isinstance(rec, dict) or rec.get("type") != "assistant" or rec.get("isSidechain")
                    or not isinstance(content, list)):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                    yield block


def _plan_roadmap_value(repo, name):
    for folder in ("", "archive"):
        path = repo / ".claude" / "plans" / folder / name
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[:30]:
                match = re.search(r"\*\*Roadmap:\*\*\s*(.*)", line)
                if match:
                    return match.group(1).strip()
            return ""
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"cannot read the plan {name}: absent from .claude/plans and its archive")


def _roadmap_bound(value):
    return bool(value) and not value.lower().startswith("ad-hoc")


def _input_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _input_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _input_strings(item)


def _roadmap_signal(ctx):
    transcript = ctx.transcript()
    if not transcript:
        return None, {"error": "transcript not found"}
    plans, vault, update = set(), False, False
    for block in _tool_inputs(transcript):
        if block.get("name", "").split(".")[-1] == "Skill" and block["input"].get("skill") == "update_roadmap":
            update = True
        for value in _input_strings(block["input"]):
            plans.update(_PLAN_REF.findall(value))
            vault = vault or bool(_ROADMAP_FILE.search(value))
            update = update or bool(_UPDATE_ROADMAP.search(value))
    bound = sorted(n for n in plans if _roadmap_bound(_plan_roadmap_value(ctx.repo, n)))
    return bool(bound or vault or update), {"roadmap_plans": bound, "vault_roadmap": vault, "update_roadmap": update,
                                            "plans_named": len(plans)}


def predicate_roadmap_atlas(ctx):
    return _roadmap_signal(ctx)


def predicate_update_roadmap(ctx):
    return _roadmap_signal(ctx)


def _git(repo, *args, input=None):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60, input=input)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} exited {result.returncode}")
    return result.stdout


def _commits_present(repo, tokens):
    """The tokens that name a commit. A token the repo no longer holds answers `missing`, not an error."""
    lines = _git(repo, "cat-file", "--batch-check", input="".join(f"{t}\n" for t in tokens)).splitlines()
    if len(lines) != len(tokens):
        raise RuntimeError(f"git cat-file answered {len(lines)} of {len(tokens)} objects")
    return {token for token, line in zip(tokens, lines) if line.split()[1:2] == ["commit"]}


def _git_files(repo, options, specs):
    """The files one `git log` call names for every spec (a commit, or an `a..b` range), read on stdin so a
    long session's commit list never meets the command-line length cap. A merge names only the files it
    resolved: the commits it brings in count through their own entries."""
    listed = _git(repo, "log", *options, "--diff-merges=dense-combined", "--name-only", "--format=", "-z", "--stdin",
                  input="".join(f"{s}\n" for s in specs))
    return {p for p in listed.replace("\n", "\0").split("\0") if p}


def _git_hashes(repo, options, specs):
    """The full hash of every commit the specs name, oldest first."""
    listed = _git(repo, "log", *options, "--reverse", "--format=%H", "--stdin", input="".join(f"{s}\n" for s in specs))
    return listed.split()


def predicate_harness_prune(ctx):
    try:
        tracked = _git(ctx.repo, "ls-files", "--", ":(top,glob).claude/plans/*.md").split()
        untracked = _git(ctx.repo, "ls-files", "--others", "--exclude-standard", "--", ":(top,glob).claude/plans/*.md").split()
        worktrees = [l for l in _git(ctx.repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return None, {"error": str(exc)}
    scratch = ctx.repo / ".claude" / "scratch"
    owned = _load_tool("scratch_prune").TOOL_OWNED_SCRATCH
    entries = len([e for e in scratch.iterdir() if e.name not in owned]) if scratch.is_dir() else 0
    extra = max(len(worktrees) - 1, 0)
    return bool(tracked or untracked or extra or entries), {
        "plans": len(tracked) + len(untracked), "extra_worktrees": extra, "scratch_entries": entries}


# Every gated stem: (predicate, the text a skip records). `session_end_check.GATED` derives from these keys.
_ROADMAP_TEXT = "the transcript names a roadmap-bound plan, a vault roadmap.md or /update_roadmap"
PREDICATES = {
    "autolearn": (predicate_autolearn,
                  "the Phase 0 digest holds a friction row or a prompt row with signals"),
    "routing_audit": (predicate_routing_audit, "the routing log holds a row since the last aggregation"),
    "orchestration_metrics": (predicate_orchestration_metrics,
                              "the session has a Workflow run record or an attributable sidecar row"),
    "hot_set_review": (predicate_hot_set_review,
                      "a tripwire budget's measured value exceeds its recorded baseline, or an oversized skill or "
                      "command body is new or grew past its recorded bytes"),
    "sync_subsystems": (predicate_sync_subsystems,
                        "a changed path outside the registry's top-level folders, or "
                        f"{DENSITY_THRESHOLD} or more changed files under one subsystem"),
    "regression_gate": (predicate_regression_gate, "the session changed a .cs file"),
    "roadmap_atlas": (predicate_roadmap_atlas, _ROADMAP_TEXT),
    "update_roadmap": (predicate_update_roadmap, _ROADMAP_TEXT),
    "worklog": (predicate_worklog_sweep,
                "the session touched worklog items (completion half) or recorded a deferral signal (add half)"),
    "reindex_search": (predicate_reindex, "the session changed docs, memory, scenes or resources"),
    "harness_prune": (predicate_harness_prune,
                      "a top-level plan, a worktree beyond the primary checkout or a top-level scratch entry "
                      "other than a tool-owned folder exists"),
}


def evaluate(stems, ctx):
    out = {}
    for stem in stems:
        predicate, text = PREDICATES[stem]
        try:
            value, basis = predicate(ctx)
        except Exception as exc:  # noqa: BLE001 - a raising predicate is null, so the phase runs
            value, basis = None, {"error": f"{type(exc).__name__}: {exc}"}
        out[stem] = {"value": value, "predicate": text, "basis": basis}
    return out


def _write_json(out, data):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--only", nargs="+", action="extend", choices=sorted(PREDICATES), metavar="STEM",
                      help="gated stems to evaluate; default every gated stem: " + ", ".join(PREDICATES))
    mode.add_argument("--scope", action="store_true", help="write the session's commits and changed files instead")
    parser.add_argument("--json", metavar="OUT", help="write the predicate or scope object here")
    parser.add_argument("--repo", default=str(DEFAULT_REPO))
    parser.add_argument("--transcript", help="transcript JSONL path (proofs only; skips discovery)")
    args = parser.parse_args(argv)
    live = bool(args.session) and args.session == os.environ.get("CLAUDE_CODE_SESSION_ID")
    ctx = Ctx(args.repo, args.session, args.transcript, live)
    if args.scope:
        scope = ctx.scope()
        if scope["files"] is None:
            print(f"scope: null — {scope['error']}")
        else:
            print(f"scope: {len(scope['commits'])} commits, {len(scope['files'])} files ({scope['identity']})")
        if args.json:
            _write_json(args.json, scope)
        return 0 if scope["files"] is not None else 1
    result = evaluate(args.only or list(PREDICATES), ctx)
    for stem, row in result.items():
        print(f"{stem}: {json.dumps(row['value'])} — {row['predicate']}")
    if args.json:
        _write_json(args.json, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
