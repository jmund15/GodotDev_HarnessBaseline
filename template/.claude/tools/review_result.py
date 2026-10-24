"""Lossless review-result normalization shared by sidecar review callers."""
import json
import math
import os
from pathlib import Path
import posixpath
import re
import shlex
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude/hooks"))
from _win_path import drive_path  # noqa: E402
_EVIDENCE_KINDS = json.loads((REPO / '.claude/schemas/evidence.json').read_text(encoding='utf-8'))['items']['oneOf']

_ACTIONS = {"FIX", "ASK", "PLAN"}
_CATEGORIES = {"bug", "rule", "improvement"}
_STOPS = {"nothing-in-scope", "exhausted-leads", "trigger-not-met", "blocked"}
# The one `path:line` rule. A location is matched with surrounding whitespace stripped, here and in
# session_digest's journal validator, so a padded value is not accepted by one and rejected by the other.
LOCATION = re.compile(r"^.+:[1-9]\d*$")
MANDATE_TOKENS = ("findings", "clean", "not-reached")
# The closed gap reasons of commands/agents/review_agents.md *Seat coverage contract*; review_fanout.js
# `GAP_REASONS` holds the same list.
GAP_REASONS = ("outside-lens", "capability", "missing")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def valid_must_read(value) -> bool:
    """True for a non-empty list of exactly `{"path": <nonblank>, "lines": <int >= 0>}` entries, the shape
    `lens_briefs.py` writes and `review_fanout.js` `validMustRead` accepts."""
    return isinstance(value, list) and bool(value) and all(
        isinstance(entry, dict) and set(entry) == {"path", "lines"} and _text(entry["path"])
        and isinstance(entry["lines"], int) and not isinstance(entry["lines"], bool) and entry["lines"] >= 0
        for entry in value)


def _decider_rank(finding):
    """A merged group keeps its most restrictive decider, ASK > open PLAN > resolved PLAN > FIX, as
    review_fanout.js `deciderRank` does; the display sort keeps FIX -> ASK -> PLAN."""
    action = finding.get("action")
    if action == "PLAN":
        return 1 if _text(finding.get("question")) else 2
    return {"ASK": 0, "FIX": 3}.get(action, 9)


def review_root(jobs, root):
    """The subject root a review's claims and quote checks read: `root` when given, else the one
    `workdir` every job names, else None. Jobs naming different workdirs without `root` raise
    ValueError naming each."""
    if root:
        return root
    named = {}
    for job in jobs:
        if _text(job.get("workdir")):
            named.setdefault(os.path.normcase(os.path.abspath(job["workdir"])), job["workdir"])
    if len(named) > 1:
        raise ValueError("review jobs name different workdirs (" + ", ".join(sorted(named.values()))
                         + "); pass --root for the subject the claims and quote checks read")
    if len(named) == 1 and all(_text(job.get("workdir")) for job in jobs):
        return next(iter(named.values()))
    return None


def _mandate_ids():
    import lens  # sibling module; its registries are read only when a roster is validated
    ids = {}
    for registry, (name, _prefix) in lens.REGISTRIES.items():
        if (lens.AGENTS / name).exists():
            for entry in lens.parse(registry)[1]:
                for mandate in entry.mandates:
                    ids.setdefault(mandate, entry.key)
    return ids, {entry.key: entry.mandates for registry, (name, _p) in lens.REGISTRIES.items()
                 if (lens.AGENTS / name).exists() for entry in lens.parse(registry)[1]}


def _roster_problems(path):
    try:
        roster = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"--ledger-roster {path} is unreadable: {exc}"]
    if isinstance(roster, dict):
        roster = roster.get("roster")
    if not isinstance(roster, list):
        return [f"--ledger-roster {path} is not a list of seats"]
    ids, by_lens = _mandate_ids()
    problems = []
    for seat in roster:
        mandates = seat.get("mandates") if isinstance(seat, dict) else None
        if not isinstance(mandates, list):
            problems.append(f"--ledger-roster {path}: a seat has no mandates list")
            continue
        for mandate in mandates:
            if mandate in ids:
                continue
            hint = f" (a lens key; it carries {', '.join(by_lens[mandate])})" if mandate in by_lens else ""
            problems.append(f"--ledger-roster seat {seat.get('key')}: {mandate!r} is not a mandate id{hint}")
    return problems


def _object_kinds(home, shas):
    """sha -> object type in the checkout `home`'s store (`missing` when absent)."""
    ordered = sorted(shas)
    run = subprocess.run(["git", "-C", str(home), "cat-file", "--batch-check"],
                         input="\n".join(ordered) + "\n", capture_output=True, text=True)
    return {parts[0]: parts[1] for line in run.stdout.splitlines() if len(parts := line.split()) >= 2}


def _blob_problems(path, root):
    try:
        blobs = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"--ledger-blobs {path} is unreadable: {exc}"]
    if not isinstance(blobs, dict):
        return [f"--ledger-blobs {path} is not a path-to-blob map"]
    problems, shas = [], {}
    for rel, sha in blobs.items():
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha):
            shas[rel] = sha
        else:
            problems.append(f"--ledger-blobs {rel}: {sha!r} is not a blob sha")
    if shas:
        import review_ledger
        top = Path(root)
        links = set(review_ledger.submodule_paths(top))
        wanted = {}
        for rel, sha in shas.items():
            wanted.setdefault(review_ledger.owning_repo(top, rel)[0], set()).add(sha)
            if rel in links:
                wanted.setdefault(top / rel, set()).add(sha)
        kinds = {home: _object_kinds(home, held) for home, held in wanted.items()}
        for rel, sha in shas.items():
            homes = [review_ledger.owning_repo(top, rel)[0]] + ([top / rel] if rel in links else [])
            kind = next((k for home in homes if (k := kinds[home].get(sha, "missing")) != "missing"), "missing")
            if kind != "blob":
                why = "a commit, so a submodule gitlink" if kind == "commit" else kind
                problems.append(f"--ledger-blobs {rel}: {sha} is {why}, not a blob in {root}")
    return problems


def launch_problems(jobs, *, root, roster=None, blobs=None):
    """Every defect a review launch would otherwise find after it has spent its seats: roster mandates
    that are not lens-registry mandate ids, blob-map entries that are not blob objects of `root`, and
    `mustRead` or context paths that do not exist. One string per bad entry; [] when the launch is sound."""
    problems = []
    for job in jobs:
        base = Path(job.get("workdir") or root or REPO)
        for entry in job.get("mustRead") or []:
            path = entry.get("path") if isinstance(entry, dict) else None
            if _text(path) and not (Path(path) if Path(path).is_absolute() else base / path).is_file():
                problems.append(f"job {job.get('label')}: mustRead {path} does not exist under {base}")
        for path in job.get("contextFiles") or []:
            if _text(path) and not Path(path).is_file():
                problems.append(f"job {job.get('label')}: context file {path} does not exist")
    if roster:
        problems += _roster_problems(roster)
    if blobs:
        problems += _blob_problems(blobs, root or REPO)
    return problems


def validate_review_jobs(jobs):
    for job in jobs:
        if not isinstance(job, dict) or not _text(job.get("label")):
            raise ValueError("review jobs need a nonempty label")
        if job.get("shape") != "review":
            raise ValueError(f"review job {job['label']} needs shape review")
        mandates = job.get("mandates")
        if not isinstance(mandates, list) or not mandates or not all(_text(key) for key in mandates):
            raise ValueError(f"review job {job['label']} needs explicit mandate keys")
        if len(set(mandates)) != len(mandates):
            raise ValueError(f"review job {job['label']} repeats a mandate key")
        must_read = job.get("mustRead")
        if not valid_must_read(must_read):
            raise ValueError(f"review job {job['label']} mustRead must be a non-empty list of "
                             '{"path": <file>, "lines": <its line count>} entries (lens_briefs.py must-read)')
        context = job.get("contextFiles")
        if not isinstance(context, list) or not context or not all(
                _text(path) and Path(path).is_file() for path in context):
            raise ValueError(f"review job {job['label']} needs readable contextFiles for engagement proof")


def read_payload(path):
    if path is None or not Path(path).is_file():
        return None
    payload = None
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and (row.get("type") == "result"
                                              or "structured_output" in row):
                    payload = row
    except OSError:
        return None
    return payload


def _unbounded(stream_path):
    """The file holding a stream's events: `sidecar_fanout.bound_finished_stream` may have replaced
    a finished stream with a `seeAlso` pointer to its byte-identical sibling in the same directory."""
    path = Path(stream_path)
    try:
        pointer = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return path
    target = pointer.get("seeAlso") if isinstance(pointer, dict) else None
    if not _text(target) or Path(target).name != target:
        return path
    return path.with_name(target)


SHELL_TOOLS = ("bash", "powershell")
_TARGET_NOTE = re.compile(r"(?:\s+\([^()]*\))+\s*$")
_TARGET_LINES = re.compile(r":\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
# The Read tool's default window: a Read naming no `limit` returns at most this many lines from its offset.
READ_WINDOW = 2000
WHOLE = (1, math.inf)
CALL_INPUTS = {"Read": "file_path", "Grep": "path", "Glob": "path", "Bash": "command", "PowerShell": "command"}


def target_path(target, toplevel=REPO) -> str:
    """The repo-relative POSIX path a `tool:target` coverage entry names, or "" when it names none.

    The tool prefix goes, and so does a trailing ` (<note>)` and `:<line>`, `:<line>-<line>` or a comma list of
    them; a shell target (`Bash:git diff -- <path>`) yields its last path operand, the operands after `--` when
    present; backslashes become `/` and `.`/`..` segments collapse; the repo root is stripped in any letter case,
    in `C:/...` or `/c/...` form."""
    tool, _, name = str(target).partition(":")
    name = _TARGET_NOTE.sub("", name.strip())
    if tool.strip().lower() in SHELL_TOOLS:
        reads = _shell_reads(name, toplevel)
        if reads:
            name = reads[-1][0]
        else:
            try:
                words = shlex.split(name)
            except ValueError:
                words = name.split()
            if "--" in words:
                words = words[len(words) - words[::-1].index("--"):]
            else:
                words = words[1:]
            operands = [w for w in words if not w.startswith("-")]
            name = operands[-1] if operands else ""
    name = _TARGET_LINES.sub("", name.strip("'\"").replace("\\", "/")).rstrip("/")
    if not name:
        return ""
    name = posixpath.normpath(name)
    name = drive_path(name)
    top = Path(toplevel).as_posix().rstrip("/")
    roots = [top]
    if len(top) > 1 and top[1] == ":":
        roots.append("/" + top[0] + top[2:])
    for root in roots:
        if name.lower() == root.lower():
            return ""
        if name.lower().startswith(root.lower() + "/"):
            return name[len(root) + 1:]
    return "" if name == "." else name


def _shell_reads(command, toplevel=REPO):
    """Proven operands of cat/Get-Content, `sed -n A,Bp` and `head -n N` (their printed lines), alone or in an
    all-success `&&` chain with cd/echo.

    Unknown commands, pipelines and redirects get no credit: their final exit status does not
    prove the named file's contents reached the caller.
    """
    command = command.strip()
    if any(char in command for char in "$`\n\r"):
        return []
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    if not words or any(any(char in word for char in ";&|<>()") for word in words if word != "&&"):
        return []
    if "&&" in words:
        commands = [[]]
        for word in words:
            if word == "&&":
                commands.append([])
            else:
                commands[-1].append(word)
        cwd, reads = str(toplevel).replace("\\", "/"), []
        for part in commands:
            if not part:
                return []
            if part[0] == "cd" and len(part) == 2 and not part[1].startswith("-"):
                path = drive_path(part[1].replace("\\", "/"))
                cwd = path if re.match(r"^(?:[A-Za-z]:/|/)", path) else posixpath.join(cwd, path)
            elif part[0] == "echo":
                continue
            else:
                found = _simple_shell_reads(part)
                if not found:
                    return []
                reads.extend((path if re.match(r"^(?:[A-Za-z]:/|/)", path) else posixpath.join(cwd, path), spans)
                             for path, spans in found)
        return reads
    return _simple_shell_reads(words)


def _simple_shell_reads(words):
    operation, args = words[0].lower(), [word.replace("\\", "/") for word in words[1:]]
    if operation == "cat":
        paths, options = [], True
        for word in args:
            if options and word == "--":
                options = False
            elif options and word.startswith("-"):
                if not re.fullmatch(r"-[AbEnsTv]+", word):
                    return []
            else:
                paths.append(word)
        return [(path, [WHOLE]) for path in paths if path != "-" and not re.search(r"[*?\[\]]", path)]
    if operation == "get-content":
        paths = []
        while args:
            word = args.pop(0)
            flag = word.lower()
            if flag == "-raw":
                continue
            if flag in ("-path", "-literalpath"):
                if not args:
                    return []
                word = args.pop(0)
            elif flag.startswith("-"):
                return []
            paths.append(word)
        return [(paths[0], [WHOLE])] if len(paths) == 1 and not re.search(r"[*?\[\],]", paths[0]) else []
    if operation == "sed":
        found = re.fullmatch(r"(\d+)(?:,(\d+))?p", args[1]) if len(args) == 3 and args[0] == "-n" else None
        if found and not args[2].startswith("-") and int(found[1]) >= 1 and int(found[2] or found[1]) >= int(found[1]):
            return [(args[2], [(int(found[1]), int(found[2] or found[1]))])]
        return []
    if operation == "head":
        count, path = 10, None
        if len(args) == 3 and args[0] == "-n" and args[1].isdigit():
            count, path = int(args[1]), args[2]
        elif len(args) == 2 and re.fullmatch(r"-\d+", args[0]):
            count, path = int(args[0][1:]), args[1]
        elif len(args) == 1:
            path = args[0]
        if path is None or count < 1 or path.startswith("-"):
            return []
        return [(path, [(1, count)])]
    return []


def target_spans(target) -> list:
    """The line spans of its file that one `tool:target` coverage entry read, as `(first, last)` pairs.

    A Read names its spans as a `:<first>-<last>` suffix or a comma list of them, and one naming none read the
    Read window from line 1. A supported simple shell read counts the lines it prints; unknown commands, writes,
    redirects and searches read no line."""
    tool, _, name = str(target).partition(":")
    tool = tool.strip().lower()
    if tool in SHELL_TOOLS:
        reads = _shell_reads(_TARGET_NOTE.sub("", name.strip()))
        return reads[-1][1] if reads else []
    if tool != "read":
        return []
    name = _TARGET_NOTE.sub("", name.strip().replace("\\", "/")).strip("'\"")
    suffix = _TARGET_LINES.search(name)
    if not suffix:
        return [(1, READ_WINDOW)]
    spans = []
    for part in suffix.group(0)[1:].split(","):
        first, _, last = part.partition("-")
        spans.append((int(first), int(last or first)))
    return spans


def read_span(offset, limit):
    """The `(first, last)` lines a Read call's `offset` and `limit` inputs return: line 1 and the Read window
    when omitted."""
    first = max(int(offset), 1) if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 1
    count = int(limit) if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0 \
        else READ_WINDOW
    return first, first + count - 1


# The free CLI seat's stream (`opencode run --format json`) has one `tool_use` event per call: lower-case tool,
# the call's input and its status in `part.state`. Each maps to the Claude tool and input names CALL_INPUTS reads.
_CLI_TOOLS = {"read": ("Read", {"filePath": "file_path", "offset": "offset", "limit": "limit"}),
              "grep": ("Grep", {"path": "path"}), "glob": ("Glob", {"path": "path"}),
              "bash": ("Bash", {"command": "command"})}


def _cli_call(entry):
    """`(Claude tool name, input)` for a completed call event of the free CLI seat, else None."""
    part = entry.get("part") if isinstance(entry, dict) and entry.get("type") == "tool_use" else None
    state = part.get("state") if isinstance(part, dict) else None
    mapped = _CLI_TOOLS.get(str(part.get("tool", "")).lower()) if isinstance(state, dict) else None
    if not mapped or state.get("status") != "completed" or not isinstance(state.get("input"), dict):
        return None
    name, keys = mapped
    given = {to: state["input"][frm] for frm, to in keys.items() if frm in state["input"]}
    return (name, given) if _text(given.get(CALL_INPUTS[name])) else None


def _successful_calls(stream_path):
    """(tool name, input) per `tool_use` of a `CALL_INPUTS` tool whose paired `tool_result` is not an error, or
    None when the stream is absent or unreadable. The stream may be a `sidecar_fanout` seeAlso pointer."""
    if not stream_path or not Path(stream_path).is_file():
        return None
    calls, out = {}, []
    try:
        with _unbounded(stream_path).open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                cli = _cli_call(entry)
                if cli:
                    out.append(cli)
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("id"):
                        key = CALL_INPUTS.get(block.get("name"))
                        given = block.get("input")
                        if key and isinstance(given, dict) and _text(given.get(key)):
                            calls[block["id"]] = (block["name"], given)
                    elif (block.get("type") == "tool_result" and block.get("tool_use_id") in calls
                          and block.get("is_error") is not True):
                        out.append(calls[block["tool_use_id"]])
    except OSError:
        return None
    return out


def called_paths(stream_path, toplevel=REPO):
    """The repo paths (`target_path`) that a transcript's successful calls name, or None when it is absent.

    A call is a `tool_use` of a `CALL_INPUTS` tool whose paired `tool_result` is not an error; a call with no
    result, an errored one and any other tool name none."""
    calls = _successful_calls(stream_path)
    if calls is None:
        return None
    out = {target_path(f"{name}:{given[CALL_INPUTS[name]]}", toplevel) for name, given in calls}
    out.discard("")
    return out


def called_reads(stream_path, toplevel=REPO):
    """`(path, spans)` per successful Read or supported simple shell read, relative to `toplevel`.
    None when the stream is absent; calls without a successful paired result receive no credit."""
    calls = _successful_calls(stream_path)
    if calls is None:
        return None
    out = []
    for name, given in calls:
        if name == "Read":
            reads = [(given["file_path"], [read_span(given.get("offset"), given.get("limit"))])]
        elif name.lower() in SHELL_TOOLS:
            reads = _shell_reads(given["command"], toplevel)
        else:
            reads = []
        for operand, spans in reads:
            path = target_path(f"Read:{operand}", toplevel)
            if path:
                out.append((path, spans))
    return out


def covers(spans, lines) -> bool:
    """True when `spans` read at least one call's worth and together cover lines 1 to `lines`."""
    if not spans:
        return False
    following = 1
    for first, last in sorted(spans):
        if first > following:
            break
        following = max(following, last + 1)
    return following > lines


def _merged(spans) -> list:
    merged = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    return merged


def spans_text(spans) -> str:
    """`spans` merged and written as `a-b, c-d`, or `none`."""
    return ", ".join(f"{a}-{'end' if b == math.inf else b}" for a, b in _merged(spans)) or "none"


def claimed_reads(tools) -> list:
    """`(target_path, target_spans)` per entry of a seat's own `checked.toolsUsed`: the self-reported reads."""
    return [(target_path(item), target_spans(item)) for item in tools if _text(item)] if isinstance(tools, list) else []


def must_read_spans(must_read, reads):
    """Per must-read entry, `{"path", "lines", "read"}` with `read` the merged spans `reads` covered of it, clipped
    to lines 1 to `lines` so the record is finite JSON; None with no must-read list."""
    if must_read is None:
        return None
    return [{"path": entry["path"], "lines": entry["lines"],
             "read": [[first, min(last, entry["lines"])] for first, last in _merged(entry_spans(entry, reads))
                      if first <= entry["lines"]]}
            for entry in must_read]


def same_path(a, b) -> bool:
    """True when two `target_path` values name one file: equal in any letter case, or one ends with the other
    on a `/` boundary, so a repo-relative entry matches the absolute path a seat read. `review_fanout.js`
    `samePath` applies the same rule."""
    a, b = a.lower(), b.lower()
    return bool(a) and bool(b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))


def gap_text(gap) -> str:
    """The item a gap names: a typed gap's `item`, a legacy string itself, else ""."""
    if isinstance(gap, dict):
        return gap.get("item") if _text(gap.get("item")) else ""
    return gap if _text(gap) else ""


def _legal_gap(gap, must_read) -> bool:
    if not (isinstance(gap, dict) and set(gap) == {"item", "reason"} and _text(gap["item"])
            and gap["reason"] in GAP_REASONS):
        return False
    item = target_path(f"Read:{gap['item']}")
    return must_read is None or any(same_path(target_path(f"Read:{entry['path']}"), item) for entry in must_read)


def entry_spans(entry, reads) -> list:
    """Every span the `reads` pairs `(target_path, spans)` read of one must-read entry's file."""
    path = target_path(f"Read:{entry['path']}")
    return [span for read, spans in reads if same_path(path, read) for span in spans]


def gap_verdict(gaps, must_read, reads):
    """(legal gaps, bad gaps, unread must-read paths, gap notes) for one seat's return.

    A legal gap is exactly `{"item", "reason"}` with a reason in GAP_REASONS and, when the seat has a
    must-read list, an item that list holds. With a must-read list, every other entry excuses nothing and is a
    note; with none, it is bad. A `gaps` value that is not a list is one such entry. An entry `{"path", "lines"}`
    is read when the `reads` pairs `(target_path, spans)` naming its file together cover all its lines
    (`covers`); it is unread when it is not read and no legal gap names it. Any bad gap or unread entry fails the
    seat (commands/agents/review_agents.md *Seat coverage contract*); `review_fanout.js` `gapVerdict` applies
    the same rule."""
    legal, bad = [], []
    for gap in gaps if isinstance(gaps, list) else [gaps]:
        (legal if _legal_gap(gap, must_read) else bad).append(gap)
    notes, bad = (bad, []) if must_read is not None else ([], bad)
    named = [target_path(f"Read:{gap['item']}") for gap in legal]
    unread = [entry["path"] for entry in must_read or []
              if not covers(entry_spans(entry, reads), entry["lines"])
              and not any(same_path(target_path(f"Read:{entry['path']}"), path) for path in named)]
    return legal, bad, unread, notes


def unread_text(must_read, reads, unread) -> str:
    """Each unread entry with the lines its seat did read, e.g. `src/a.cs (read 1-437 of 900)`."""
    by_path = {entry["path"]: entry for entry in must_read or []}
    return ", ".join(f"{path} (read {spans_text(entry_spans(by_path[path], reads))} of {by_path[path]['lines']})"
                     for path in unread)


def engaged(job, stream_path):
    """True when the review job's seat observably engaged its subject. The launcher embeds contextFiles in the
    prompt, so a seat with a must-read list never Reads them: it engaged when it Read at least one must-read
    entry, and the per-entry must-read judgement then fails it for any entry it left unread. A job without a
    must-read list proves engagement by reading every contextFiles path."""
    must = [entry.get("path") for entry in job.get("mustRead") or [] if isinstance(entry, dict)]
    if not must:
        return prove_engagement(stream_path, list(job.get("contextFiles") or []))
    reads = called_reads(stream_path)
    called = {path for path, _ in reads or []}
    return bool(reads) and any(target_path(f"Read:{path}") in called for path in must)


def prove_engagement(stream_path, required_paths):
    """True when every required path is a file with a successful observed read (`called_reads`)."""
    if not required_paths or not all(Path(path).is_file() for path in required_paths):
        return False
    reads = called_reads(stream_path)
    called = {path for path, _ in reads or []}
    return reads is not None and {target_path(f"Read:{path}") for path in required_paths} <= called


def _evidence_item(item):
    return (isinstance(item, dict) and all(isinstance(v, str) for v in item.values())
            and sum(set(kind['required']) <= set(item) <= set(kind['properties']) for kind in _EVIDENCE_KINDS) == 1)


def finding_problem(value):
    """The first reason `value` is not a review finding, else None. `review_fanout.js` `findingProblem` is the
    twin and tests/fixtures/finding_id_vectors.json holds both to one verdict; `session_digest` calls this."""
    if not isinstance(value, dict):
        return "entry is not an object"
    for field in ("agent", "description", "rationale"):
        if not _text(value.get(field)):
            return f"`{field}` is missing or blank"
    if value.get("action") not in _ACTIONS:
        return "`action` is not FIX, ASK or PLAN"
    if value.get("category") not in _CATEGORIES:
        return "`category` is not bug, rule or improvement"
    if "critical" in value and not isinstance(value["critical"], bool):
        return "`critical` is not a boolean"
    if value.get("file") is not None and not (
            isinstance(value["file"], str) and LOCATION.match(value["file"].strip())):
        return "`file` is not path:line"
    for field in ("old", "new", "question"):
        if value.get(field) is not None and not isinstance(value[field], str):
            return f"`{field}` is not a string"
    for field in ("options", "scope"):
        member = value.get(field)
        if member is not None and not (isinstance(member, list) and all(isinstance(v, str) for v in member)):
            return f"`{field}` is not a list of strings"
    if "refuting" in value and not isinstance(value["refuting"], bool):
        return "`refuting` is not a boolean"
    evidence = value.get("evidence", [])
    if not isinstance(evidence, list):
        return "`evidence` is not a list"
    for index, item in enumerate(evidence):
        if not _evidence_item(item):
            return f"`evidence` item {index} is none of the evidence kinds"
    return None


def _finding(value):
    return finding_problem(value) is None


def _body(payload):
    if not isinstance(payload, dict) or payload.get("is_error") is True:
        return None
    result = payload.get("structured_output")
    if result is None and isinstance(payload.get("result"), str):
        try:
            result = json.loads(payload["result"])
        except ValueError:
            return None
    return result if isinstance(result, dict) else None


def text_body(text):
    """The review JSON object a text deliverable holds: the whole text, else its last fenced block that parses
    to an object, else None."""
    if not _text(text):
        return None
    candidates = [text] + list(reversed(re.findall(r"```[\w-]*\n(.*?)\n```", text, re.S)))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def mandate_map(checked, mandates):
    """`checked.mandates` when it maps exactly `mandates` to `findings`, `clean` or `not-reached`, else None.

    `review_fanout.js` `isMandateMap` applies the same rule: a missing or unknown key voids the map."""
    value = checked.get("mandates") if isinstance(checked, dict) else None
    if not isinstance(value, dict) or not mandates or set(value) != set(mandates):
        return None
    return dict(value) if all(value[key] in MANDATE_TOKENS for key in mandates) else None


def _coverage(body, mandates):
    if not body or not isinstance(body.get("findings"), list):
        return None, ["missing or malformed review result"], list(mandates)
    checked = body.get("checked")
    if not isinstance(checked, dict):
        return None, ["checked provenance missing"], list(mandates)
    tools = checked.get("toolsUsed")
    basis = checked.get("basis")
    stop = checked.get("stoppedAt")
    errors = []
    if not isinstance(tools, list) or not tools or not all(_text(item) for item in tools):
        errors.append("no positive read/search provenance")
    if not _text(basis):
        errors.append("checked basis missing")
    if stop not in _STOPS or stop == "blocked":
        errors.append(f"review stopped at {stop!r}")
    typed = mandate_map(checked, mandates)
    missing = []
    for key in mandates:
        if typed is not None:
            if typed[key] == "not-reached":
                missing.append(key)
            continue
        match = re.search(r"(?<![\w-])" + re.escape(key) + r"\s*:\s*([^;\n]+)", basis or "", re.I)
        outcome = match.group(1) if match else basis if len(mandates) == 1 else None
        if not _text(outcome) or re.search(r"\b(?:not reached|uncovered|blocked)\b", outcome, re.I):
            missing.append(key)
    return checked, errors, missing


def _seat_status(fatal, failed, shortfall, valid, own_limit_only):
    """`uncovered` for a fatal record, `failed` for a coverage-contract failure, `completed` with no shortfall,
    else `partial` when the seat kept a valid finding or its only shortfall is its own capability limit."""
    if fatal:
        return "uncovered"
    if failed:
        return "failed"
    if not shortfall:
        return "completed"
    return "partial" if valid or own_limit_only else "uncovered"


def _original_view(sources):
    return [dict(source["finding"], id=source["id"], merged_from=[source["id"]])
            for source in sources]


def _source_file(source):
    return re.sub(r":[1-9]\d*$", "", str(source["finding"].get("file") or ""))


def merge_candidates(sources):
    seats = {}
    for source in sources:
        name = _source_file(source)
        if name:
            seats.setdefault(name, set()).add(source["seat"])
    return [source for source in sources if len(seats.get(_source_file(source), set())) > 1]


def _claim(source):
    return re.sub(r"\s+", " ", source["finding"]["description"]).strip().casefold()


def exact_groups(sources):
    """Group merge candidates by file (line suffix stripped) and normalized claim text.

    Reworded duplicates stay separate singletons; callers see this through merge mode "exact".
    """
    groups = {}
    for source in merge_candidates(sources):
        groups.setdefault((_source_file(source), _claim(source)), []).append(source["id"])
    return {"findings": [{"merged_from": ids} for ids in groups.values()]}


def merge_view(sources, groups):
    """Validate a model's same-defect partition; an invalid one returns every original."""
    candidates = merge_candidates(sources)
    originals = _original_view(sources)
    if not candidates:
        return {"view": originals, "issues": [], "candidates": []}
    by_id = {row["id"]: row for row in candidates}
    if not isinstance(groups, dict) or not isinstance(groups.get("findings"), list):
        return {"view": originals, "issues": ["merge returned no usable groups"],
                "candidates": list(by_id)}
    seen = set()
    partitions = []
    issue = None
    for row in groups["findings"]:
        ids = row.get("merged_from") if isinstance(row, dict) else None
        if not isinstance(ids, list) or not ids or not all(_text(item) for item in ids):
            issue = "merge contains an empty or malformed group"
            break
        if len(set(ids)) != len(ids) or any(item not in by_id or item in seen for item in ids):
            issue = "merge repeats or invents a source ID"
            break
        members = [by_id[item] for item in ids]
        if len({_source_file(member) for member in members}) != 1:
            issue = "merge combines different files"
            break
        if len({_claim(member) for member in members}) != 1:
            issue = "merge combines different claims on one file"
            break
        seen.update(ids)
        partitions.append((ids, members))
    if not issue and seen != set(by_id):
        issue = "merge omitted source IDs"
    if issue:
        return {"view": originals, "issues": [issue], "candidates": list(by_id)}
    tier = {"FIX": 0, "ASK": 1, "PLAN": 2}
    category = {"bug": 0, "rule": 1, "improvement": 2}
    view = []
    merged = 0
    for ids, members in partitions:
        if len(ids) == 1:
            view.append(dict(members[0]["finding"], id=ids[0], merged_from=ids))
            continue
        merged += 1
        found = [member["finding"] for member in members]
        representative = found[0]
        decider = min(found, key=_decider_rank)
        evidence = list({json.dumps(item, sort_keys=True): item
                         for member in found for item in member.get("evidence", [])}.values())
        compatible_edit = all((member.get("old"), member.get("new")) ==
                              (representative.get("old"), representative.get("new"))
                              for member in found)
        item = dict(representative, id=f"M{merged}", merged_from=ids,
                    agent=", ".join(dict.fromkeys(member["agent"] for member in found)),
                    mandates=list(dict.fromkeys(member["agent"] for member in found)),
                    action=decider["action"], question=decider.get("question"),
                    options=decider.get("options"), scope=decider.get("scope"),
                    category=min((member["category"] for member in found), key=category.__getitem__),
                    critical=any(member.get("critical") for member in found),
                    refuting=any(member.get("refuting") for member in found),
                    evidence=evidence)
        item.pop("mandate", None)
        if item["action"] == "ASK":
            item["question"] = " / ".join(dict.fromkeys(
                member["question"] for member in found if _text(member.get("question")))) or None
            item["options"] = list(dict.fromkeys(
                option for member in found if member["action"] == "ASK"
                for option in _options(member))) or None
        if not compatible_edit:
            item.pop("old", None)
            item.pop("new", None)
        view.append(item)
    view.extend(dict(source["finding"], id=source["id"], merged_from=[source["id"]])
                for source in sources if source["id"] not in by_id)
    view.sort(key=lambda item: (not item.get("critical"), tier.get(item["action"], 9),
                                category.get(item["category"], 9)))
    return {"view": view, "issues": [], "candidates": list(by_id)}


def _options(finding):
    options = finding.get("options")
    return options if isinstance(options, list) else []


def applied_with_alternative(rows, applied_ids):
    """One {id, applied, alternative} per applied resolved PLAN carrying options[1];
    review_fanout.js derives fold.digest.appliedWithAlternative the same way."""
    by_id = {row["id"]: row for row in rows}
    found = [by_id.get(i) for i in applied_ids]
    return [{"id": f["id"], "applied": f["options"][0], "alternative": f["options"][1]}
            for f in found if f and f.get("action") == "PLAN" and not _text(f.get("question"))
            and len(_options(f)) > 1]


_TIER_SHAPE = (
    ("ask-null-question", lambda f: f["action"] == "ASK" and not _text(f.get("question"))),
    ("ask-recommended", lambda f: f["action"] == "ASK" and any(
        isinstance(o, str) and "(Recommended)" in o for o in _options(f))),
    ("alternative-without-wins-when", lambda f: any(
        not isinstance(o, str) or "wins when" not in o for o in _options(f)[1:])),
    ("plan-no-scope", lambda f: f["action"] == "PLAN" and not (
        isinstance(f.get("scope"), list) and any(_text(item) for item in f["scope"]))),
    ("fix-null-edit", lambda f: f["action"] == "FIX" and (f.get("old") is None or f.get("new") is None)),
)


def tier_shape(sources):
    """The action tiers' checkable shape over each source finding -> {predicate: [ids]}, violated only."""
    found = {}
    for predicate, violates in _TIER_SHAPE:
        ids = [source["id"] for source in sources if violates(source["finding"])]
        if ids:
            found[predicate] = ids
    return found


def ingest(seats):
    """Keep every valid finding, flag incomplete seats, and reconcile the source population.

    A finding whose `agent` names no mandate of a multi-mandate seat is kept, as `review_fanout.js` `isFinding`
    keeps it, and flagged in the seat's `issues`; it is not rejected and does not change the seat's `status`.

    A seat's optional `mustRead` list is judged by `gap_verdict` against `reads` (the `(path, spans)` its stream
    shows it read, observed through `called_reads`) when present, else its own `checked.toolsUsed` targets
    through `target_spans`; the entry's `mustReadEvidence` says which (`observed` or `self-reported`, None
    with no `mustRead`), and its `mustReadSpans` lists the lines read of each entry (`must_read_spans`). A bad
    gap or an unread entry makes a returned seat `failed`: every mandate
    uncovered, `unread` naming the entries, its findings kept as sources. A `capability` gap leaves the seat
    partial, and `capabilityPartial` marks a seat partial only by its own limit: capability gaps, or a blocked stop its
    mandate map names with `not-reached` keys while a must-read list proves engagement; `outside-lens` and
    `missing` gaps leave the seat complete."""
    sources = []
    coverage = []
    counts = {"seats": 0, "completed": 0, "partial": 0, "failed": 0, "uncovered": 0,
              "received": 0, "valid": 0, "rejected": 0}
    for seat in seats:
        label = seat["label"]
        mandates = seat["mandates"]
        counts["seats"] += 1
        record = seat.get("record") or {}
        import model_registry
        data = model_registry.load()
        identity = model_registry.record_identity(record, token=record.get("requestedModel"), data=data)
        missing_attestation = bool(record.get("transport") in data["transports"]
                                   and model_registry.attestation_for(record.get("transport"), data)
                                   and not record.get("attestedModel"))
        if missing_attestation:
            identity = {"model": None, "basis": None, "modelObserved": False}
        body = _body(seat.get("payload"))
        checked, errors, missing = _coverage(body, mandates)
        must_read = seat.get("mustRead")
        # A seat that stopped blocked and names the block on its own map (a valid map, the blocked mandates
        # `not-reached`) stopped on those mandates only; with a must-read list to prove engagement, its other tokens
        # stand. Without the map naming it, or without a must-read list, a blocked stop voids the seat's coverage.
        typed = mandate_map(checked, mandates)
        named_block = bool(checked and checked.get("stoppedAt") == "blocked" and must_read is not None
                           and typed is not None and "not-reached" in typed.values())
        provenance_failed = any(not (named_block and error == "review stopped at 'blocked'") for error in errors)
        if checked is not None and missing:
            errors.append("mandates not accounted for: " + ", ".join(missing))
        fatal = (record.get("exitCode") != 0 or record.get("schemaValid") is not True
                 or record.get("engagement") == "unproven" or missing_attestation)
        if missing_attestation:
            errors.append("required runtime model attestation missing")
        if record.get("exitCode") != 0 or record.get("schemaValid") is not True:
            errors.append("sidecar record failed")
        if record.get("engagement") == "unproven":
            errors.append("target engagement was not proven")
        rows = body.get("findings", []) if body else []
        rows = rows if isinstance(rows, list) else []
        received = len(rows)
        entries_before = counts["received"]
        counts["received"] += received
        valid = 0
        rejected = 0
        unattributed = []
        for index, finding in enumerate(rows):
            # An entry's id is its place in the run, so a rejected entry keeps its id and never shifts a neighbour's.
            entry_id = f"F{entries_before + index + 1}"
            if fatal:
                rejected += 1
                continue
            problem = finding_problem(finding)
            if problem:
                rejected += 1
                errors.append(f"finding {entry_id} (entry {index}) rejected: {problem}")
                continue
            if len(mandates) > 1 and finding["agent"] not in mandates:
                unattributed.append(f"finding {index} names no mandate of its seat")
            sources.append({"id": entry_id, "seat": label, "index": index,
                            "finding": finding})
            valid += 1
        counts["valid"] += valid
        counts["rejected"] += rejected
        gaps = body.get("gaps", []) if body else None
        reads = seat.get("reads")
        observed = reads is not None
        if not observed:
            reads = claimed_reads(checked.get("toolsUsed") if checked else None)
        legal, bad, unread, notes = gap_verdict(gaps, must_read, reads) if body else ([], [], [], [])
        if bad:
            errors.append(f"{len(bad)} gap entr{'y names' if len(bad) == 1 else 'ies name'} no "
                          f"{'listed ' if must_read is not None else ''}item with a legal reason "
                          f"({', '.join(GAP_REASONS)}): " + "; ".join(json.dumps(gap)[:200] for gap in bad))
        if unread:
            errors.append("must-read entries not read in full, with no legal gap: "
                          + unread_text(must_read, reads, unread)
                          + f"; stoppedAt={checked.get('stoppedAt') if checked else None} does not excuse them")
        capability = any(gap["reason"] == "capability" for gap in legal)
        if capability:
            errors.append("seat reported capability gaps")
        failed = bool(bad or unread)
        # Status comes from these facts; `errors` is only the report text.
        capability_limited = (not fatal and not failed and not rejected and not provenance_failed
                              and (capability or named_block))
        mapped = typed is not None
        # Capability-partial, the fact `review_fanout.js` computes in `perAgent`: no shape-rejected entry, valid
        # provenance, a valid mandate map, and a shortfall that is the seat's own limit: a capability gap, or a
        # blocked stop the map names (`named_block`). A valid map's `not-reached` keys are that limit, not a
        # disqualifier.
        capability_partial = capability_limited and mapped
        # A seat whose only shortfall is its own capability gaps engaged and covered the rest of its scope,
        # so it is partial even with no findings; any other shortfall needs a valid finding. Unaccounted
        # mandates are that other shortfall unless a valid map names them `not-reached`.
        own_limit_only = capability_limited and (mapped or not missing)
        shortfall = bool(fatal or failed or rejected or provenance_failed or missing or capability)
        status = _seat_status(fatal, failed, shortfall, valid, own_limit_only)
        counts[status] += 1
        coverage.append({"label": label, "mandates": mandates,
                         "model": identity["model"], "modelObserved": identity["modelObserved"],
                         "identityBasis": identity["basis"], "record": seat.get("recordPath"),
                         "tier": record.get("tier") or seat.get("tier"),
                         "effort": record.get("effort") or seat.get("effort"),
                         "uncoveredMandates": list(mandates) if failed else missing,
                         "status": status, "received": received, "valid": valid,
                         "rejected": rejected, "checked": checked, "issues": errors + unattributed,
                         "capabilityPartial": capability_partial,
                         "gaps": legal if isinstance(gaps, list) else None, "gapNotes": notes, "unread": unread,
                         "mustReadEvidence": None if must_read is None
                         else "observed" if observed else "self-reported",
                         "mustReadSpans": must_read_spans(must_read, reads) if body else None})
    return {"counts": counts, "coverage": coverage, "sources": sources}


def prepare_review(jobs, results):
    validate_review_jobs(jobs)
    seats = []
    for job in jobs:
        label = job["label"]
        run = results.get(label) or {}
        record_path = run.get("record")
        try:
            record = json.loads(Path(record_path).read_text(encoding="utf-8")) if record_path else {}
        except (OSError, ValueError, TypeError):
            record = {}
        if run.get("exit") != record.get("exitCode"):
            record["exitCode"] = None
        if record.get("engagement") not in ("proven", "unproven"):
            record["engagement"] = "proven" if engaged(job, run.get("stream")) else "unproven"
        seats.append({"label": label, "mandates": job["mandates"], "mustRead": job.get("mustRead"),
                      "reads": called_reads(run.get("stream")),
                      "payload": read_payload(run.get("stdout")), "record": record, "recordPath": record_path,
                      "tier": job.get("tier"), "effort": job.get("effort")})
    return ingest(seats)


def consolidate(jobs, results, out_dir, groups=None, prepared=None, *, verify=True):
    """Write complete sources and merge view; a closing host may defer evidence checks."""
    import verify_claims

    review = prepared if prepared is not None else prepare_review(jobs, results)
    root = Path(out_dir)
    merged = merge_view(review["sources"],
                        exact_groups(review["sources"]) if groups is None else groups)
    review["view"] = merged["view"]
    review["merge"] = {"mode": "exact" if groups is None else "supplied",
                       "candidates": merged["candidates"], "issues": merged["issues"]}
    review["verdicts"] = []
    review["verdictPath"] = None
    errors = list(merged["issues"])
    if any(row["finding"].get("evidence") for row in review["sources"]):
        source_path = root / "review_sources.json"
        with source_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"sources": review["sources"]}, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if verify:
            verdict_path = root / "review_verdicts.json"
            code = verify_claims.verify_findings(str(source_path), str(verdict_path), {}, False)
            if code != 0 or not verdict_path.is_file():
                errors.append("quote checker did not return a complete verdict file")
            else:
                try:
                    verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
                    review["verdicts"] = verdicts["verdicts"]
                    review["verdictPath"] = str(verdict_path)
                    review["quoteCounts"] = verdicts["counts"]
                except (OSError, ValueError, KeyError, TypeError):
                    errors.append("quote verdict file is malformed")
    review["errors"] = errors
    review["flags"] = [{"kind": "tier-shape", "detail": f"{predicate}: {len(ids)} finding"
                        f"{'' if len(ids) == 1 else 's'} ({', '.join(ids)})"}
                       for predicate, ids in tier_shape(review["sources"]).items()]
    review["status"] = "complete" if review["counts"]["completed"] == len(jobs) and not errors else "partial"
    with (root / "review.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(review, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return review

