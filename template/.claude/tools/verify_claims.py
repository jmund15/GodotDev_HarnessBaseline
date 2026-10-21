#!/usr/bin/env python3
"""Check a claim's `evidence` against the bytes it was taken from: the one executable evidence contract.

The contract (`reference/evidence_contract.md`, machine twin `schemas/evidence.json`) gives every claim
`evidence: [item, ...]`, each item one of four kinds: `{file, quote}`, `{command, output}`, `{owner, question?, task?}`
and `{url, quote, artifact?}`. Each item gets a verdict, and a claim gets one:

  VERIFIED    every item of the claim is VERIFIED
  UNVERIFIED  an item is checkable and wrong: the file is readable and lacks the quote, the path does not
              exist, the command's stdout lacks the output, no owner turn holds the words
  UNCHECKED   no verdict could be reached, and it is not a failure: context7, WebSearch, memory, a URL without
              fetched bytes, a command outside the allow-list or past its timeout, a transcript or file that
              cannot be read, a claim with no items, polarity `unclear`. UNCHECKED is counted and shown,
              never folded into a pass, and a claim with any UNCHECKED item is never VERIFIED.

A match proves presence, never the claim's conclusion. Advisory by default: it reports and the reader decides.

`check_claims(rows, *, root, session_id, manifest)` is the entry: `rows = [{"id", "evidence": [items],
"polarity"?}]`, `root` the subject checkout. A citation prefixed by that checkout's path relative to the
main checkout loses that prefix; only absent gitignored evidence falls back to the main checkout.
Tracked deletions never use the main checkout's pre-change copy.
`items_of(claim)` is the legacy adapter that turns a string `evidence` plus `file` into items. Both CLI shapes
below and `verify_findings` share one item checker, `check_item`; `post_check.py` reaches it through
`fold_check.independent_verdicts`.

Input: legacy claims JSON array (file or stdin), or `--findings` over a complete review object, sidecar
result, native Workflow journal, or directory. A directory skips `*.record.json*` and `*schema*.json`
members, and each verdict names its `source` file so positional IDs from different seats stay distinct.
Findings without evidence are counted in `omittedNoEvidence`; an empty container still fails. A finding's
evidence is a list of items; a malformed item fails the whole input. `fetch_source.sh`'s manifest carries
the URL->artifact mapping when a claim cites a URL.

  verify_claims.py claims.json [--manifest <tsv>] [--root <dir>] [--session <id>]
  verify_claims.py --findings <path> --out <verdicts.json> [--root <dir>] [--session <id>]

Exit is 0 unless the input itself is unusable.
"""
import argparse
import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from functools import cached_property
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _d in (str(REPO / ".claude" / "hooks"), str(Path(__file__).resolve().parent)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

VERIFIED, UNVERIFIED, UNCHECKED = "VERIFIED", "UNVERIFIED", "UNCHECKED"
ITEM_KINDS = ("command", "owner", "url", "file")
# Measured 2026-10-06 over 21 allow-listed commands on this repo: p95 0.13 s, max 3.5 s (a whole-history `git log -S`);
# the two dossiers' four runnable command mentions took 22-48 ms. Twice the max, under the Stop hook's 10 s.
COMMAND_TIMEOUT = 8
SHORT_FRAGMENT = 16

_TAGS = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]*?)?\s*/?>")
_SCRIPT = re.compile(r"<(script|style).*?</\1>", re.S | re.I)
# Class-reference BBCode + markdown emphasis: present in the source, absent from a sensibly-quoted line.
_BB = re.compile(r"\[/?(?:b|i|u|s|code|codeblock|codeblocks|gdscript|csharp|url|param|member|"
                 r"method|constant|enum|signal|theme_item|annotation|class)(?=[\s=\]])[^\]]*\]", re.I)
_EMPH = re.compile(r"[*_`]+")


def normalize(text: str) -> str:
    """Collapse a source and a quote onto common ground.

    Measured necessity: a correct quote false-negatived because the README wrapped the version in
    <strong>. Matching raw bytes rejects true quotes; this is the minimum that stops that.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _SCRIPT.sub(" ", text)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    text = _BB.sub("", text)
    text = _EMPH.sub("", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def prepare(text: str) -> str:
    return normalize(text).casefold()


def load_manifest(path):
    """fetch_source.sh TSV: status, http, bytes, artifact, url."""
    mapping = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 5 and parts[0] == "OK" and parts[3] != "-":
                mapping[parts[4]] = parts[3]
    return mapping


_LOCATION = re.compile(r"^(.*):([1-9]\d*)(?:-([1-9]\d*))?$")
_COMMAND = re.compile(r"^(?:bash|python\d*|opencode|git|npm|dot(?:net)|curl)\s", re.I)
_NON_PATH = re.compile(r"^(?:context7|websearch|memory)(?:[:\s(]|$)", re.I)
_GREP_PREFIX = re.compile(r"^\s*(?:(?:[A-Za-z]:)?[\w.\\/-]+:\d+:\s*|\d+[:\t]\s*|\[\d+\]\s*)", re.M)


def citation_path(value):
    text = str(value or "").strip()
    match = _LOCATION.match(text)
    if not match:
        return text, None
    start = int(match.group(2))
    end = int(match.group(3) or match.group(2))
    return match.group(1), [start, end]


def quoted_text(value):
    return _GREP_PREFIX.sub("", value)


def _bounded(body, part, at=0):
    """Index of `part` in `body` at or after `at` with no word character touching either edge, else -1."""
    while True:
        found = body.find(part, at)
        if found < 0:
            return -1
        end = found + len(part)
        before = found > 0 and part[0].isalnum() and body[found - 1].isalnum()
        after = end < len(body) and part[-1].isalnum() and body[end].isalnum()
        if not (before or after):
            return found
        at = found + 1


def _raw_hit(text, quote):
    """The quote as a literal substring, whitespace collapsed and case-folded: normalize() strips BBCode and
    emphasis, so a quote that is itself such a token (a C# `[Signal]` line) normalizes to nothing."""
    raw = re.sub(r"\s+", " ", quoted_text(str(quote or ""))).strip().casefold()
    return bool(raw) and raw in re.sub(r"\s+", " ", text).casefold()


def _match(body, quote, whole_words=False):
    """`body` is `prepare`d. In-order `...` gaps are allowed; both sides are case-folded.

    `whole_words` rejects a hit inside a longer word, a one-letter owner quote matches only a body that is exactly that letter."""
    find = (lambda text, part, at: _bounded(text, part, at)) if whole_words else (lambda text, part, at: text.find(part, at))
    normalized = normalize(quoted_text(quote)).casefold()
    if not normalized:
        return False
    if whole_words and len(normalized) == 1:
        return body.strip() == normalized
    parts = [part.strip() for part in re.split(r"\.\.\.|…", normalized) if part.strip()]
    if len(parts) == 1 and "..." not in normalized and "…" not in normalized:
        return find(body, normalized, 0) >= 0
    if not parts:
        return False
    at = 0
    for part in parts:
        found = find(body, part, at)
        if found < 0:
            return False
        at = found + len(part)
    return True


def quote_matches(body, quote):
    """`body` is `normalize`d text (any case)."""
    return _match(body.casefold(), quote)


# ---- the four legs -------------------------------------------------------------------------------------


class _Context:
    def __init__(self, root, session_id, manifest, command_timeout):
        self.root = Path(root) if root else REPO
        self.session_id = session_id
        self.manifest = manifest or {}
        self.timeout = COMMAND_TIMEOUT if command_timeout is None else command_timeout
        self.bodies = {}
        self.hashes = {}
        self.turns = {}
        self.answers = {}

    @cached_property
    def main_root(self):
        common = _path_git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common is None or common.returncode:
            return None
        listed = _path_git(self.root, "--git-dir", common.stdout.strip(), "worktree", "list", "--porcelain")
        if listed is None or listed.returncode:
            return None
        first = listed.stdout.splitlines()[0] if listed.stdout else ""
        return Path(first[len("worktree "):]).resolve() if first.startswith("worktree ") else None


def _result(kind, verdict, reason, **extra):
    return dict({"kind": kind, "verdict": verdict, "reason": reason}, **extra)


def kind_of(item):
    if not isinstance(item, dict):
        return None
    return next((kind for kind in ITEM_KINDS if kind in item), None)


def _item_error(item):
    """Why an item is malformed, or None: each kind's required fields are strings."""
    kind = kind_of(item)
    if kind is None:
        return "an evidence item holds none of file, command, owner or url"
    need = {"file": ("file", "quote"), "command": ("command", "output"), "owner": ("owner",),
            "url": ("url", "quote")}[kind]
    for key in need + tuple(k for k in ("task", "artifact", "question") if k in item):
        if item.get(key) is not None and not isinstance(item.get(key), str):
            return f"{kind} item field {key} is not a string"
    missing = [key for key in need if not isinstance(item.get(key), str)]
    if missing and not (kind == "file" and item.get("file") is None and item.get("legacy")):
        return f"{kind} item lacks {', '.join(missing)}"
    return None


def _path_git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _resolve(ctx, value):
    """Use subject bytes; only absent gitignored evidence may use the main checkout."""
    path = Path(value)
    if path.is_absolute():
        return path
    root = ctx.root.resolve()
    main = ctx.main_root
    if main is not None and root != main:
        try:
            prefix = root.relative_to(main)
            path = path.relative_to(prefix)
        except ValueError:
            pass
    candidate = root / path
    if candidate.exists() or main is None or main == root:
        return candidate
    ignored = _path_git(root, "check-ignore", "-q", "--", str(path))
    if ignored is not None and ignored.returncode == 0:
        return main / path
    return candidate


def resolve_path(root, value):
    """Resolve a reviewer-cited path with the evidence contract's checkout rules."""
    return _resolve(_Context(root, None, None, None), value)


def _body(ctx, path):
    key = str(path)
    if key not in ctx.bodies:
        try:
            data = Path(path).read_bytes()
            text = data.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
            ctx.hashes[key] = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
            ctx.bodies[key] = (prepare(text), text)
        except OSError:
            ctx.bodies[key] = None
    return ctx.bodies[key]


def _check_quote(kind, cited, quote, artifact, ctx, legacy):
    path_text, span = citation_path(cited) if cited else (None, None)
    source = None
    if artifact:
        candidate = _resolve(ctx, artifact)
        source = candidate if candidate.is_file() else None
    if source is None:
        source = _resolve(ctx, path_text) if path_text else None
    if source is None:
        return _result(kind, UNVERIFIED, "missing-file")
    source = source.resolve()
    fingerprint = {'path': str(_resolve(ctx, path_text).resolve()) if artifact and path_text else str(source),
                   'blobHash': None}
    if not source.exists():
        return _result(kind, UNVERIFIED, "missing-file", file=cited, **fingerprint)
    held = _body(ctx, source)
    if held is None:
        return _result(kind, UNCHECKED, "unreadable", file=cited, **fingerprint)
    prepared, text = held
    fingerprint['blobHash'] = ctx.hashes[str(source)]
    extra = {"file": cited, "rangeHit": None, "citedRange": span, **fingerprint}
    if not isinstance(quote, str) or not quote.strip():
        return _result(kind, UNVERIFIED, "empty-quote", **extra)
    if span:
        lines = text.splitlines()[span[0] - 1:span[1]]
        extra["rangeHit"] = _match(prepare("\n".join(lines)), quote)
    if not _match(prepared, quote) and not _raw_hit(text, quote):
        return _result(kind, UNVERIFIED, "quote-absent", **extra)
    if legacy:
        needle, body = normalize(quoted_text(quote)).casefold(), prepared
        if not needle:
            needle = re.sub(r"\s+", " ", quoted_text(quote)).strip().casefold()
            body = re.sub(r"\s+", " ", text).casefold()
        if len(needle) < SHORT_FRAGMENT and body.count(needle) > 1:
            return _result(kind, UNCHECKED, "short-fragment", **extra)
    return _result(kind, VERIFIED, "quote-found", **extra)


def _check_file(item, ctx):
    cited = item.get("file")
    if cited is None or not str(cited).strip():
        return _result("file", UNCHECKED, item.get("why") or "no-file")
    cited = str(cited).strip()
    if cited.startswith(("http://", "https://")) or _NON_PATH.match(cited):
        return _result("file", UNCHECKED, "external", file=cited)
    if _COMMAND.match(cited):
        return _result("file", UNCHECKED, "not-a-path", file=cited)
    return _check_quote("file", cited, item.get("quote"), item.get("artifact"), ctx, bool(item.get("legacy")))


def _check_url(item, ctx):
    artifact = item.get("artifact") or ctx.manifest.get(item.get("url"))
    if not artifact:
        return _result("url", UNCHECKED, "external", url=item.get("url"))
    return _check_quote("url", artifact, item.get("quote"), None, ctx, False)


# ---- command leg ---------------------------------------------------------------------------------------


class _Spec:
    def __init__(self, short="", short_value="", long=(), long_value=(), optval=(), numeric=False,
                 require=None, pathspecs=None):
        self.short, self.short_value = set(short), set(short_value)
        self.long, self.long_value, self.optval = set(long), set(long_value), set(optval)
        self.numeric = numeric
        self.require = require or (lambda seen, positional: True)
        self.pathspecs = pathspecs or (lambda seen, positional: positional)


def _any_of(*names):
    return lambda seen, positional: bool(seen & set(names))


# Each subcommand lists every permitted flag in full; anything else, an abbreviated long option included,
# is refused. Short options are split and each letter checked.
GIT_SPECS = {
    "grep": _Spec(short="nivwFEGlLchHqo", short_value="eABC",
                  long=("--line-number", "--ignore-case", "--word-regexp", "--invert-match", "--fixed-strings",
                        "--extended-regexp", "--basic-regexp", "--count", "--files-with-matches",
                        "--files-without-match", "--no-color", "--only-matching", "--quiet", "--full-name",
                        "--cached"),
                  long_value=("--max-depth",),
                  require=lambda seen, positional: bool(positional) or "-e" in seen,
                  pathspecs=lambda seen, positional: positional if "-e" in seen else positional[1:]),
    "ls-files": _Spec(short="cdmsio",
                      long=("--cached", "--deleted", "--modified", "--others", "--stage", "--ignored",
                            "--exclude-standard", "--error-unmatch", "--full-name", "--directory",
                            "--no-empty-directory", "--unmerged")),
    "ls-tree": _Spec(short="rdtl", long=("--name-only", "--full-name", "--full-tree", "--long"),
                     require=lambda seen, positional: bool(positional),
                     pathspecs=lambda seen, positional: positional[1:]),
    "log": _Spec(short="psi", short_value="nSG", numeric=True,
                 long=("--oneline", "--name-only", "--name-status", "--stat", "--no-merges", "--merges",
                       "--follow", "--all", "--no-color", "--patch", "--no-patch", "--reverse",
                       "--abbrev-commit", "--date-order", "--topo-order", "--first-parent", "--ignore-case"),
                 long_value=("--max-count", "--format", "--pretty", "--grep", "--author", "--since", "--until",
                             "--after", "--before", "--diff-filter", "--date", "--skip")),
    "show": _Spec(short="s",
                  long=("--stat", "--name-only", "--name-status", "--oneline", "--no-patch", "--no-color",
                        "--patch", "--abbrev-commit"),
                  long_value=("--format", "--pretty")),
    "branch": _Spec(short="arv", long=("--list", "--all", "--remotes", "--show-current", "--verbose"),
                    long_value=("--contains", "--merged", "--no-merged", "--points-at"),
                    require=lambda seen, positional: not positional or "--list" in seen,
                    pathspecs=lambda seen, positional: []),
    "check-ignore": _Spec(short="vnq", long=("--verbose", "--non-matching", "--quiet"),
                          require=lambda seen, positional: bool(positional)),
    "cat-file": _Spec(short="ptse",
                      require=lambda seen, positional: len(positional) == 1 and len(seen & {"-p", "-t", "-s", "-e"}) == 1,
                      pathspecs=lambda seen, positional: []),
    "rev-parse": _Spec(short="q", long=("--short", "--verify", "--quiet", "--abbrev-ref", "--show-toplevel",
                                         "--is-inside-work-tree", "--show-prefix"),
                       optval=("--short", "--abbrev-ref"),
                       pathspecs=lambda seen, positional: []),
    "diff": _Spec(long=("--stat", "--name-only", "--cached", "--staged", "--no-color", "--no-renames"),
                  long_value=("--diff-filter",), require=_any_of("--stat", "--name-only")),
    "status": _Spec(short="sb", long=("--short", "--branch", "--porcelain"),
                    require=_any_of("-s", "--short", "--porcelain")),
}
_SHELL_CHARS = re.compile(r"[|;&<>$`]")
_FIXED_REV = re.compile(r"[0-9a-f]{7,40}(?:~\d*|\^\d*)*")


def parse_git(command):
    """(subcommand, argv-after-git, pathspecs) for an allow-listed read-only git command, else None."""
    if not isinstance(command, str) or _SHELL_CHARS.search(command):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) < 2 or argv[0] != "git" or argv[1].startswith("-") or argv[1] not in GIT_SPECS:
        return None
    sub, spec, rest = argv[1], GIT_SPECS[argv[1]], argv[2:]
    seen, positional, after = set(), [], []
    index, dashdash = 0, False
    while index < len(rest):
        token = rest[index]
        index += 1
        if dashdash:
            (positional if sub == "branch" else after).append(token)
        elif token == "--":
            dashdash = True
        elif token.startswith("--"):
            name, eq, _value = token.partition("=")
            if name in spec.long:
                if eq and name not in spec.optval:
                    return None
            elif name in spec.long_value:
                if not eq:
                    if index >= len(rest):
                        return None
                    index += 1
            else:
                return None
            seen.add(name)
        elif token.startswith("-") and len(token) > 1:
            if spec.numeric and re.fullmatch(r"-\d+", token):
                continue
            letters, at = token[1:], 0
            while at < len(letters):
                letter = letters[at]
                if letter in spec.short_value:
                    if at + 1 >= len(letters):
                        if index >= len(rest):
                            return None
                        index += 1
                    seen.add("-" + letter)
                    break
                if letter not in spec.short:
                    return None
                seen.add("-" + letter)
                at += 1
        else:
            positional.append(token)
    if not spec.require(seen, positional):
        return None
    pathspecs = after if dashdash else spec.pathspecs(seen, positional)
    return sub, [sub, *rest], [p for p in pathspecs if p]


def _run_git(rest, root, timeout):
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_DIR")}
    env.update(GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    done = subprocess.run(["git", "--no-pager", *rest], cwd=str(root), capture_output=True, timeout=timeout, env=env)
    return (done.returncode, done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n"),
            done.stderr.decode("utf-8", errors="replace"))


def _check_command(item, ctx):
    parsed = parse_git(item.get("command"))
    if parsed is None:
        return _result("command", UNCHECKED, "not-runnable")
    sub, rest, pathspecs = parsed
    if sub == "show" and any(":" in token and not _FIXED_REV.fullmatch(token.split(":", 1)[0])
                             for token in rest[1:] if not token.startswith("-")):
        return _result("command", UNCHECKED, "moving-ref")
    output = item.get("output")
    try:
        code, stdout, stderr = _run_git(rest, ctx.root, ctx.timeout)
    except subprocess.TimeoutExpired:
        return _result("command", UNCHECKED, "timeout")
    except OSError:
        return _result("command", UNCHECKED, "not-runnable")
    if code not in ((0, 1) if sub == "grep" else (0,)) or stderr.strip():
        return _result("command", UNCHECKED, "vacuous-scope", detail=f"exit {code}: {stderr.strip()[:120]}")
    if output.strip():
        found = _match(prepare(stdout), output)
        return _result("command", VERIFIED if found else UNVERIFIED, "output-found" if found else "output-absent")
    if stdout.strip():
        return _result("command", UNVERIFIED, "output-differs")
    for spec in pathspecs:
        try:
            code, listed, stderr = _run_git(["ls-files", "--", spec], ctx.root, ctx.timeout)
        except subprocess.TimeoutExpired:
            return _result("command", UNCHECKED, "timeout")
        except OSError:
            return _result("command", UNCHECKED, "not-runnable")
        if code != 0 or stderr.strip() or not listed.strip():
            return _result("command", UNCHECKED, "vacuous-scope", detail=f"pathspec {spec} matches no tracked path")
    return _result("command", VERIFIED, "empty-over-tracked-scope")


# ---- owner leg -----------------------------------------------------------------------------------------


def _owner_turns(ctx):
    """`(turns, why, answers)`: the owner's turns, and the answer rows keyed by uuid with their (question, label) pairs."""
    sid = ctx.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not sid:
        return None, "no-session", {}
    if sid not in ctx.turns:
        import _owner_text
        import task_record
        path = task_record._session_transcript(sid)
        ctx.turns[sid] = _owner_text.owner_turns(path)
        ctx.answers[sid] = dict(_owner_text.owner_answer_pairs(path) or [])
    turns = ctx.turns[sid]
    return turns, None if turns is not None else "transcript-unreadable", ctx.answers[sid]


def _check_owner(item, ctx):
    quote = item.get("owner")
    if not quote.strip():
        return _result("owner", UNVERIFIED, "empty-quote")
    turns, why, answers = _owner_turns(ctx)
    question = item.get("question")
    bare_label = False
    answered = []
    for uuid, text in turns or []:
        if not _match(prepare(text), quote, whole_words=True):
            continue
        if uuid not in answers:
            return _result("owner", VERIFIED, "owner-turn")
        if not question:
            bare_label = True
        elif any(_match(prepare(label), quote, whole_words=True) and _match(prepare(asked), question)
                 for asked, label in answers[uuid]):
            return _result("owner", VERIFIED, "owner-turn")
        else:
            answered.extend(asked for asked, _ in answers[uuid])
    task = item.get("task")
    if task:
        import task_record
        rows = [row for row in (task_record.load(task) or {}).get("conditions") or []
                if isinstance(row, dict) and _match(prepare(str(row.get("quote") or "")), quote, whole_words=True)]
        statuses = [task_record._condition_row(row)["status"] for row in rows]
        if "fresh" in statuses:
            return _result("owner", VERIFIED, "task-record")
        if statuses:
            return _result("owner", UNCHECKED, statuses[0].lower())
    if turns is None:
        return _result("owner", UNCHECKED, why)
    if bare_label:
        return _result("owner", UNVERIFIED, "answer-without-question",
                       detail="a picked label verifies with `question`, the question the owner answered")
    if answered:
        return _result("owner", UNVERIFIED, "label-question-mismatch",
                       detail="the owner picked that label under another question: " + "; ".join(answered))
    return _result("owner", UNVERIFIED, "quote-absent")


_LEGS = {"file": _check_file, "command": _check_command, "owner": _check_owner, "url": _check_url}


def check_item(item, ctx):
    if isinstance(item, dict) and "file" not in item and isinstance(item.get("path"), str):
        item = dict(item, file=item["path"])  # `path` is the key other harness rows use; same meaning
    error = _item_error(item)
    if error:
        return _result(kind_of(item) or "unknown", UNCHECKED, "malformed-item", detail=error)
    return _LEGS[kind_of(item)](item, ctx)


def claim_verdict(item_verdicts, polarity=None):
    if any(v == UNVERIFIED for v in item_verdicts):
        return UNVERIFIED
    if item_verdicts and all(v == VERIFIED for v in item_verdicts) and polarity != "unclear":
        return VERIFIED
    return UNCHECKED


def check_claims(rows, *, root=REPO, session_id=None, manifest=None, command_timeout=None):
    """Verdicts for `rows = [{"id", "evidence": [items], "polarity"?}]` against `root`.

    -> {"status": "pass"|"fail", "counts": {VERIFIED, UNVERIFIED, UNCHECKED}, "verdicts": {id: {"verdict",
    "items": [{"kind", "verdict", "reason", ...}]}}, "errors": [...]}. `status` is `pass` only when no claim is
    UNVERIFIED or UNCHECKED and no row was unusable."""
    ctx = _Context(root, session_id, manifest, command_timeout)
    verdicts, errors = {}, []
    counts = {VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: 0}
    for row in rows:
        key = row.get("id") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key:
            errors.append(f"a claim row has no usable id: {str(row)[:80]}")
            continue
        if key in verdicts:
            errors.append(f"duplicate claim id {key}")
            continue
        evidence = row.get("evidence")
        items = [check_item(item, ctx) for item in (evidence if isinstance(evidence, list) else [])]
        verdict = claim_verdict([i["verdict"] for i in items], row.get("polarity"))
        verdicts[key] = {"verdict": verdict, "items": items}
        counts[verdict] += 1
    status = "pass" if not counts[UNVERIFIED] and not counts[UNCHECKED] and not errors else "fail"
    return {"status": status, "counts": counts, "verdicts": verdicts, "errors": errors}


# ---- legacy adapter ------------------------------------------------------------------------------------

_JOINERS = re.compile(r"\s\|\s|\s;\s|\s{2}/\s{2}")
_PROSE_LABEL = re.compile(r"^[^\"'`:]{1,60}:\s*([\"'`])(.+)\1$", re.S)
_LINE_LABEL = re.compile(r"^(?:lines?\s+\d+(?:\s*[-–]\s*\d+)?|L\d+(?:-L?\d+)?)\s*[:\-]\s*", re.I)
_FILE_LABEL = re.compile(r"^([\w./\\-]+\.\w{1,6})(?::\d+(?:-\d+)?)?(?:\s+[^\"'`:]{1,40})?:\s+(.*)$", re.S)


def _unwrap(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1].strip()
    return text


def _split_joiners(text):
    """Split on ` | `, ` ; ` and a two-space slash, except inside a double-quoted span: a quoted markdown table row
    or code line keeps its own pipes. An odd number of double quotes splits everywhere."""
    if text.count('"') % 2:
        return _JOINERS.split(text)
    out, inside, start, index = [], False, 0, 0
    while index < len(text):
        if text[index] == '"':
            inside = not inside
        elif not inside:
            joiner = _JOINERS.match(text, index)
            if joiner:
                out.append(text[start:index])
                start = index = joiner.end()
                continue
        index += 1
    out.append(text[start:])
    return out


def _quoted_runs(piece):
    """The fragments of a string made only of quoted fragments joined by whitespace, a comma, `and`, `or` or `...`, else None.
    A closing quote counts only before a joiner and the next opening quote, so a quote character inside a
    fragment never splits it."""
    runs, start = [], 0
    while start < len(piece):
        quote = piece[start]
        if quote not in "\"'`":
            return None
        end = start
        while True:
            end = piece.find(quote, end + 1)
            if end < 0:
                return None
            gap = re.match(r"\s*(?:,|and\b|or\b|\.\.\.|…)?\s*", piece[end + 1:])
            after = piece[end + 1 + gap.end():end + 2 + gap.end()]
            if after == "" or after in ("\"", "'", "`"):
                break
        runs.append(piece[start + 1:end])
        start = end + 1 + gap.end()
    return runs if len(runs) > 1 else None


def _tracked(root):
    try:
        code, out, _err = _run_git(["ls-files"], root, 20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return out.split("\n") if code == 0 else []


def items_of(claim, *, root=REPO, tracked=None):
    """The evidence items of a claim: a list `evidence` is returned as it is; a string `evidence` plus `file`
    is split into one item per fragment. Splitting cannot create a false pass, because every fragment must
    match. A fragment is dropped only when, label stripped, it holds no letter or digit; one too short to
    locate uniquely in its file is checked as UNCHECKED, never dropped."""
    evidence = claim.get("evidence")
    if isinstance(evidence, list):
        return list(evidence)
    if not isinstance(evidence, str) or not evidence.strip():
        return []
    file, artifact = claim.get("file"), claim.get("artifact")
    file = file.strip() if isinstance(file, str) and file.strip() else None
    own = citation_path(file)[0].replace("\\", "/").rsplit("/", 1)[-1] if file else None
    items, listing = [], tracked
    for part in _split_joiners(evidence):
        part = _LINE_LABEL.sub("", part.strip())
        target, why = file, None
        label = _FILE_LABEL.match(part)
        if label:
            name = label.group(1).replace("\\", "/")
            part = label.group(2)
            base = name.rsplit("/", 1)[-1]
            if base != own:
                if listing is None:
                    listing = _tracked(root)
                found = [p for p in listing if p.rsplit("/", 1)[-1] == base and (p == name or p.endswith("/" + name))]
                target, why = (found[0], None) if len(found) == 1 else (None, "unresolved-label")
        for piece in _quoted_runs(part.strip()) or [part]:
            text = _unwrap(piece)
            prose = _PROSE_LABEL.match(text)
            if prose:
                text = prose.group(2).strip()
            if not any(ch.isalnum() for ch in text):
                continue
            if target and target.startswith(("http://", "https://")):
                item = {"url": target, "quote": text}
            else:
                item = {"file": target, "quote": text, "legacy": True}
                if target is None:
                    item["why"] = why or "no-file"
            if artifact:
                item["artifact"] = artifact
            items.append(item)
    return items


# ---- findings mode -------------------------------------------------------------------------------------


def _findings_from_payload(value, source):
    if isinstance(value, dict) and (value.get("is_error") is True
                                    or value.get("schemaValid") is False):
        raise ValueError("sidecar returned an error or invalid schema")
    if isinstance(value, dict) and isinstance(value.get("structured_output"), (dict, list)):
        value = value["structured_output"]
    elif isinstance(value, dict) and isinstance(value.get("result"), str):
        value = json.loads(value["result"])
    if isinstance(value, dict) and "checked" in value and not isinstance(value["checked"], dict):
        raise ValueError("review checked metadata is malformed")
    if isinstance(value, dict) and (value.get("checked") or {}).get("stoppedAt") == "blocked":
        raise ValueError("review seat stopped at blocked; no clean coverage")
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        if "sources" in value:
            rows = value["sources"]
        elif "items" in value:
            rows = value["items"]
        else:
            rows = value.get("findings")
    else:
        rows = None
    if not isinstance(rows, list) or not rows:
        raise ValueError("findings container has no nonempty findings/sources/items array")
    result = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"finding {index} is not an object")
        finding = row.get("finding", row)
        if not isinstance(finding, dict):
            raise ValueError(f"finding {index} is not an object")
        identifier = row.get("id") or finding.get("id") or f"F{index}"
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"finding {index} has no usable id")
        evidence = finding.get("evidence", [])
        if not isinstance(evidence, list) or any(_item_error(item) for item in evidence):
            raise ValueError(f"finding {identifier} has malformed evidence")
        result.append({"id": identifier, "source": source, "evidence": evidence})
    return result


def load_findings(location):
    path = Path(location)
    if not path.exists():
        raise ValueError(f"findings input does not exist: {path}")
    errors = []
    if path.is_dir() and (path / "journal.jsonl").is_file():
        import session_digest
        archive = session_digest.build_workflow_result_archive(path, "review")
        rows = _findings_from_payload({"items": archive["items"]}, path.name)
        if archive["status"] != "completed":
            errors.append(f"workflow coverage is {archive['status']}, not completed")
        return rows, errors
    if path.is_dir():
        sources = sorted(child for child in path.iterdir() if child.is_file()
                         and (child.name.endswith(".json") or child.name.endswith(".json.out"))
                         and ".record.json" not in child.name
                         and not ("schema" in child.name and child.name.endswith(".json")))
        if not sources:
            raise ValueError(f"findings directory has no JSON inputs: {path}")
    else:
        sources = [path]
    rows = []
    for source in sources:
        try:
            with source.open(encoding="utf-8") as stream:
                rows.extend(_findings_from_payload(json.load(stream), source.name))
        except (OSError, ValueError) as exc:
            errors.append(f"{source}: {exc}")
    if not rows and not errors:
        errors.append("no findings read")
    for name in dict.fromkeys(row["source"] for row in rows):
        identifiers = [row["id"] for row in rows if row["source"] == name]
        repeats = sorted({item for item in identifiers if identifiers.count(item) > 1})
        if repeats:
            errors.append(f"duplicate finding IDs in {name}: " + ", ".join(repeats))
    return rows, errors


def verify_findings(location, out_path, manifest, strict, root=None, session_id=None, command_timeout=None):
    try:
        findings, errors = load_findings(location)
    except (OSError, ValueError) as exc:
        print(f"verify_claims: {exc}", file=sys.stderr)
        return 2
    if not findings:
        errors.append("no usable findings")
    ctx = _Context(root, session_id, manifest, command_timeout)
    verdicts = []
    counts = {"inputFindings": len(findings), "findings": 0, "evidence": 0,
              "verified": 0, "unverified": 0, "unchecked": 0, "omittedNoEvidence": 0}
    for finding in findings:
        evidence = finding["evidence"]
        if not evidence:
            counts["omittedNoEvidence"] += 1
            continue
        claims = []
        for item in evidence:
            result = check_item(item, ctx)
            counts[result["verdict"].lower()] += 1
            counts["evidence"] += 1
            row = {key: value for key, value in item.items() if key != "legacy"}
            row.update(verdict=result["verdict"], reason=result["reason"], kind=result["kind"],
                       citedRange=result.get("citedRange"))
            claims.append(row)
        verdicts.append({"id": finding["id"], "source": finding["source"],
                         "verdict": claim_verdict([row["verdict"] for row in claims]), "claims": claims})
        counts["findings"] += 1
    result = {"status": "partial" if errors else "complete", "counts": counts,
              "verdicts": verdicts, "errors": errors}
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                  encoding="utf-8", newline="\n")
    else:
        print(json.dumps(result, ensure_ascii=False))
    for error in errors:
        print(f"verify_claims: {error}", file=sys.stderr)
    return 2 if errors else 1 if strict and counts["unverified"] else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("claims", nargs="?", default="-")
    ap.add_argument("--findings")
    ap.add_argument("--out")
    ap.add_argument("--manifest")
    ap.add_argument("--root")
    ap.add_argument("--session")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest) if args.manifest else {}
    root = Path(args.root).resolve() if args.root else REPO
    if args.findings:
        if args.claims != "-":
            ap.error("--findings and a legacy claims path are mutually exclusive")
        return verify_findings(args.findings, args.out, manifest, False, root=root, session_id=args.session)
    if args.out:
        ap.error("--out requires --findings")

    raw = sys.stdin.read() if args.claims == "-" else open(args.claims, encoding="utf-8").read()
    try:
        claims = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"verify_claims: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if isinstance(claims, dict):
        claims = claims.get("claims", [])
    if not isinstance(claims, list):
        print("verify_claims: expected a JSON array of claims", file=sys.stderr)
        return 2

    ctx = _Context(root, args.session, manifest, None)
    rows, counts, tracked = [], {VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: 0}, None
    for idx, claim in enumerate(claims):
        subject = str(claim.get("subject") or claim.get("claim") or f"claim[{idx}]")[:70]
        if tracked is None and isinstance(claim.get("evidence"), str):
            tracked = _tracked(root)
        results = [check_item(item, ctx) for item in items_of(claim, root=root, tracked=tracked)]
        verdict = claim_verdict([r["verdict"] for r in results], claim.get("polarity"))
        note = ", ".join(sorted({r["reason"] for r in results})) or "no evidence"
        if verdict == UNVERIFIED:
            note += " - may be a composed or cross-element quote rather than a fabrication; read it before discarding"
        counts[verdict] += 1
        rows.append((verdict, subject, note))

    width = max((len(r[1]) for r in rows), default=10)
    for verdict, subject, note in rows:
        print(f"{verdict:15} {subject:<{width}}  {note}")
    print(f"\n{counts[VERIFIED]} verified, {counts[UNVERIFIED]} unverified, "
          f"{counts[UNCHECKED]} unchecked  (advisory - UNVERIFIED means LOOK, not discard)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
