#!/usr/bin/env python3
"""PreToolUse AskUserQuestion guard for the current session's /overnight run.

Homes: commands/overnight.md §Step 2 (the guard); commands/overnight_review.md (--open-questions
and --archive-closed, which read Close docs and never touch markers). State is active-<session-id>.json under
.claude/scratch/overnight; legacy anonymous active.json is never claimed.
Expired owned state is removed. Only the owner's return disarms: a UserPromptSubmit carrying
owner text (prompt_dispatch CHAIN member). The model has no disarm command, so work after the
Close doc (a /session_end the goal names) stays unattended.
`--validate-close` checks each `Q` row's Park reason and Default, plus Reversibility (clean, residue
or none) when supplied, and, when the doc holds a `Q` row, the `Next session: <currency> · <model>`
line in `## Outcome`. `--open-questions` retains legacy missing-Default rows as open.
Both Close-doc readers default `<dir>` to `<VAULT_ROOT>/DevProjects/<PROJECT_NAME>/Claude/TODO/Overnight`
from `baseline.lock.json` substitutions.
`--archive-closed <dir>` moves each session's docs to <dir>/archive/ once none holds an open,
malformed or unreadable row and none of its sessions is armed; it never overwrites an archived file.
Invalid state and hook errors fail open for AskUserQuestion but fail closed for CLI state changes.
Proof: tests/test_overnight_ask_guard.py.
"""
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from _checkout_root import main_checkout_root as checkout_root
from _hook_state import write_json_atomic
from _owner_text import ENVELOPE_RE, LEADING_INJECTION_RE

TTL_HOURS = 16
TOKEN = "{" + "{%s}" + "}"  # a literal token here would be expanded by a consumer's install
_ALLOWED_HEADINGS = {"Outcome", "Decisions made", "Decisions for you", "Left undone"}
_REQUIRED_HEADINGS = {"Outcome", "Decisions for you", "Left undone"}
_Q_ROW = re.compile(
    r"^\*\*Q(?P<number>\d+) — .+[?.] Options: .+\. "
    r"Park: (?P<reason>irreversible|out-of-scope|owner-domain|no-recommendation|failed-twice) — .+\. "
    r"Recommendation: (?P<recommendation>(?:(?!\. (?:Reversibility|Default):).)+?)"
    r"(?:\. Reversibility: (?P<reversibility>clean|residue|none))?"
    r"(?:\. Default: (?P<default>.+?))?\.\s*$"
)
_NEXT_SESSION = re.compile(r"^(?:- )?Next session: .+ · .+$", re.MULTILINE)
_NOT_OWNER = re.compile(r"<(?:cross-session-message|task-notification|agent-message)\b", re.I)
_RESUME = re.compile(
    r"^(?:/overnight\s*|"
    r"(?:<command-message>overnight</command-message>\s*)?"
    r"<command-name>/overnight</command-name>\s*"
    r"(?:<command-args>\s*</command-args>\s*)?)$", re.I
)

REASON = (
    "/overnight is armed — no one is here to answer. Decide or park per commands/overnight.md "
    "§Step 2; the final message lists the Q rows as text. This holds through /session_end and "
    "every hook that says to ask. Only the owner's next prompt disarms it."
)


def _root():
    return checkout_root(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def overnight_dir(root=None):
    lock = Path(root or _root()) / ".claude/baseline.lock.json"
    try:
        subs = json.loads(lock.read_text(encoding="utf-8"))["substitutions"]
        vault, project = subs[TOKEN % "VAULT_ROOT"], subs[TOKEN % "PROJECT_NAME"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"No Overnight dir given and {lock} has no readable VAULT_ROOT and "
                         f"PROJECT_NAME substitutions ({exc!r}); pass the dir.") from exc
    return str(Path(vault) / "DevProjects" / project / "Claude/TODO/Overnight")


def session_id(value=None):
    value = os.environ.get("CLAUDE_CODE_SESSION_ID", "") if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("A valid CLAUDE_CODE_SESSION_ID is required; anonymous state is not owned.")
    return value


def marker_path(root=None, sid=None):
    return os.path.join(root or _root(), ".claude", "scratch", "overnight", f"active-{session_id(sid)}.json")


def owned_state(root=None, sid=None):
    sid = session_id(sid)
    try:
        with open(marker_path(root, sid), encoding="utf-8") as stream:
            doc = json.load(stream)
        if not isinstance(doc, dict) or doc.get("session_id") != sid:
            return None
        stamp = doc.get("armed_at")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            return None
        if stamp > time.time() or not isinstance(doc.get("goal"), str):
            return None
        return doc
    except (OSError, ValueError):
        return None


_LEFT_UNDONE_ROW = re.compile(r"^- .+ — blocked by Q(?P<number>\d+) — worklog: (?P<title>.+?)\s*$")


def _validate_left_undone(text, q_numbers, root):
    """Each Left undone row names a Q row of this doc and an item in the worklog titles mirror."""
    section = re.search(r"^## Left undone\s*$(?P<body>.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    rows = [line for line in section.group("body").splitlines() if line.startswith("- ")] if section else []
    rows = [row for row in rows if row.strip() != "- None."]
    if not rows:
        return
    try:
        mirror = Path(root, ".claude", "worklog-titles.md").read_text(encoding="utf-8")
    except OSError:
        mirror = ""
    # A Ledger line ends in its `[after: <condition>]`; the title is the text before it.
    titles = {re.sub(r"\s*\[after: [^\]]*\]$", "", line.rsplit(" · ", 1)[-1].strip())
              for line in mirror.splitlines() if line.startswith("- ")}
    for row in rows:
        match = _LEFT_UNDONE_ROW.match(row)
        if not match or int(match.group("number")) not in q_numbers or match.group("title") not in titles:
            raise ValueError(
                "Each Left undone row reads '- <work> — blocked by Q<n> — worklog: <title>': Q<n> is a Q row "
                "of this doc and <title> an item in .claude/worklog-titles.md (overnight.md §Step 4). "
                f"Failing row: {row}")


def validate_close(path, root=None):
    """Check a Close doc's shape. It needs no armed marker: the owner's return deletes the marker, and a
    Close doc written or edited after that still owes this check. The filename binds it to the session."""
    sid = session_id()
    target = Path(path).resolve()
    if not target.is_file() or not target.name.endswith(f"-{sid}.md"):
        raise ValueError("Close doc must exist and its filename must end with the current session id.")
    text = target.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+?)\s*$", text, re.MULTILINE)
    if len(headings) != len(set(headings)) or set(headings) - _ALLOWED_HEADINGS:
        raise ValueError("Close doc has duplicate or unsupported ## headings.")
    if not _REQUIRED_HEADINGS.issubset(headings):
        raise ValueError("Close doc requires Outcome, Decisions for you, and Left undone headings.")
    q_rows = [line for line in text.splitlines() if line.startswith("**Q")]
    numbers = []
    for row in q_rows:
        match = _Q_ROW.fullmatch(row)
        if not match:
            raise ValueError("Each Q row needs Options, a named Park reason/evidence, Recommendation, "
                             "and Default; optional Reversibility must be clean, residue or none.")
        if match.group("default") is None:
            raise ValueError(
                "Each Q row ends 'Recommendation: <option|none>. Default: <what holds while it stays "
                "unanswered>.' Optional Reversibility must be clean, residue or none "
                f"(overnight.md §Step 2). Failing row: {row}")
        numbers.append(int(match.group("number")))
    if len(numbers) != len(set(numbers)):
        raise ValueError("Close doc Q numbers must be unique.")
    outcome = re.search(r"^## Outcome\s*$(?P<body>.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if numbers and not (outcome and _NEXT_SESSION.search(outcome.group("body"))):
        raise ValueError("A Close doc with a Q row needs 'Next session: <currency> · <model>' in ## Outcome "
                         "(overnight.md §Step 4).")
    _validate_left_undone(text, set(numbers), root or _root())
    print(f"VALIDATED {target}" + (" (still armed until the owner's next prompt)" if armed(root, sid) else ""))


_QUESTION_LINE = re.compile(r"^\s*(?:#{1,6}\s*|[-*]\s*)?\**Q(?P<number>\d+)\b")


def _resolved_below(lines, index):
    for later in lines[index + 1:]:
        text = later.strip()
        if text:
            return text.lstrip("- ").startswith("Resolved:")
    return False


def open_questions(folder):
    """Route every question line of every Close doc in `folder` to exactly one list."""
    out = {"docs_scanned": 0, "open": [], "malformed": [], "resolved": [], "unreadable": []}
    for doc in sorted(Path(folder).glob("*.md")):
        out["docs_scanned"] += 1
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if not text.strip():
            out["unreadable"].append(str(doc))
            continue
        lines = text.splitlines()
        section = None
        for index, line in enumerate(lines):
            heading = re.match(r"^## (.+?)\s*$", line)
            if heading:
                section = heading.group(1)
                continue
            match = _QUESTION_LINE.match(line)
            if not match or section == "Left undone":
                continue
            row = {"doc": str(doc), "number": int(match.group("number")), "row": line.strip()}
            if _resolved_below(lines, index):
                out["resolved"].append(row)
            elif _Q_ROW.fullmatch(line):
                out["open"].append(row)
            else:
                out["malformed"].append(row)
    return out


_DOC_SESSION = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<sid8>[0-9a-f]{8})(?:-|\.md$)")


def _live(doc):
    return doc is not None and time.time() - doc["armed_at"] <= TTL_HOURS * 3600


def _armed_ids(root):
    """Session ids holding a live overnight marker, read without removing an expired one."""
    ids = set()
    for path in Path(root, ".claude", "scratch", "overnight").glob("active-*.json"):
        sid = path.stem[len("active-"):]
        try:
            doc = owned_state(root, sid)
        except ValueError:
            continue
        if _live(doc):
            ids.add(sid)
    return ids


def archive_closed(folder, root=None):
    """Move every top-level doc that needs no owner action into `folder`/archive/.

    A doc's session group (`<date>-<sid8>…`: its Close doc plus same-session packets) moves only
    when no doc in it has an open, malformed or unreadable row and no session of it is armed.
    An existing archive file of the same name is never overwritten.
    """
    folder = Path(folder)
    rows = open_questions(folder)
    blocked = {}
    for kind in ("open", "malformed"):
        for row in rows[kind]:
            blocked.setdefault(Path(row["doc"]).name, kind)
    for doc in rows["unreadable"]:
        blocked[Path(doc).name] = "unreadable"
    armed_ids = _armed_ids(root or _root())
    for doc in folder.glob("*.md"):
        if any(doc.name.endswith(f"-{sid}.md") for sid in armed_ids):
            blocked[doc.name] = "armed"

    def group(name):
        match = _DOC_SESSION.match(name)
        return match.group("sid8") if match else name

    blocked_groups = {group(name): reason for name, reason in blocked.items()}
    blocked_groups.update({sid[:8]: "armed" for sid in armed_ids})
    out = {"archived": [], "kept": []}
    target_dir = folder / "archive"
    for doc in sorted(folder.glob("*.md")):
        reason = blocked.get(doc.name) or blocked_groups.get(group(doc.name))
        target = target_dir / doc.name
        if reason is None and target.exists():
            reason = "archive-target-exists"
        if reason:
            out["kept"].append({"doc": str(doc), "reason": reason})
            continue
        target_dir.mkdir(exist_ok=True)
        doc.replace(target)
        out["archived"].append(str(target))
    return out


def arm(goal, root=None):
    sid = session_id()
    path = marker_path(root, sid)
    if os.path.exists(path) and owned_state(root, sid) is None:
        raise ValueError("Existing marker has invalid ownership or state; preserve it for inspection.")
    if not write_json_atomic(path, {"goal": goal, "session_id": sid, "armed_at": time.time()}):
        raise OSError("Could not persist overnight state.")
    print(f"ARMED {path}")


def is_owner_prompt(text):
    if not isinstance(text, str):
        return False
    body = text
    while True:
        match = LEADING_INJECTION_RE.match(body)
        if not match:
            break
        body = body[match.end():]
    body = body.strip()
    return bool(body) and not ENVELOPE_RE.match(body) and not _NOT_OWNER.search(body) \
        and not _RESUME.match(body)


def owner_return(payload):
    sid = payload.get("session_id", "")
    root = checkout_root(payload.get("cwd") or _root())
    doc = owned_state(root, sid) if is_owner_prompt(payload.get("prompt")) else None
    if doc is None:
        return
    os.unlink(marker_path(root, sid))
    goal = doc.get("goal") or ""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "[overnight] An owner message disarmed /overnight for this session. If the message "
                             "leaves the unattended run going (a side request or status check, not a new direction "
                             "or an answer that ends it), re-arm before your next owner-facing fork: "
                             f"python3 .claude/hooks/overnight_ask_guard.py --arm \"{goal}\""}}))


def armed(root=None, sid=None):
    doc = owned_state(root, sid)
    if doc is None:
        return False
    if not _live(doc):
        try:
            os.unlink(marker_path(root, sid))
        except OSError:
            pass
        return False
    return True


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) >= 2:
        try:
            if argv[1] == "--arm":
                goal = " ".join(argv[2:]).strip()
                if not goal:
                    raise ValueError("--arm requires a non-empty goal.")
                arm(goal)
            elif argv[1] == "--validate-close" and len(argv) == 3:
                validate_close(argv[2])
            elif argv[1] == "--open-questions" and len(argv) in (2, 3):
                print(json.dumps(open_questions(argv[2] if len(argv) == 3 else overnight_dir())))
            elif argv[1] == "--archive-closed" and len(argv) in (2, 3):
                print(json.dumps(archive_closed(argv[2] if len(argv) == 3 else overnight_dir())))
            elif argv[1] == "--status" and len(argv) == 2:
                sid = session_id()
                active = armed(sid=sid)
                print(json.dumps({"session_id": sid, "armed": active,
                                  "marker_path": marker_path(sid=sid),
                                  "goal": (owned_state(sid=sid) or {}).get("goal") if active else None}))
            else:
                raise ValueError("Use --arm <goal>, --status, --validate-close <path>, --open-questions [dir] "
                                 "or --archive-closed [dir]; "
                                 "only the owner's next prompt disarms.")
            return 0
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict) and payload.get("hook_event_name") == "UserPromptSubmit":
            owner_return(payload)
            return 0
        if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
            return 0
        root = checkout_root(payload.get("cwd") or _root())
        if armed(root, payload.get("session_id", "")):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
