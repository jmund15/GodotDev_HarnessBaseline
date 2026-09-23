#!/usr/bin/env python3
"""Canonical-shape approval for harness actions the owner authorized by class: read-only
sidecar launches and digests, and the scratch-message `git commit`."""
import json
import os
import re
import shlex
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import _git_commit  # noqa: E402

Contract = namedtuple("Contract", "name judge shape approver")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ALLOWED_SIDECAR_FLAGS = frozenset("meDGfCSR PldaTZnorsNLxt".replace(" ", ""))
_PATH_FLAGS = frozenset("dfRPSLaC")
_EXISTING_FILE_FLAGS = frozenset("fCS")
_EXISTING_DIR_FLAGS = frozenset("da")
_PARENT_FLAGS = frozenset("RPL")
_ALLOWED_SHAPES = frozenset({"any", "survey", "review"})
_ALLOWED_TOOLS = frozenset({"Read", "Glob", "Grep"})
_DIGEST_OPTIONS = frozenset({
    "--session", "--prompt-tail", "--project-dir", "--brief", "--handoff", "--full",
    "--digest-file", "--workflow-dir", "--workflow-kind", "--workflow-manifest",
    "--workflow-select", "--workflow-page", "--workflow-full", "--expect-journal-sha256",
    "--select", "--evidence-page", "--page", "--page-size", "--json-only", "--context-only",
    "--previous", "--list", "--match", "--match-file", "--tools",
})
_DIGEST_VALUE_OPTIONS = _DIGEST_OPTIONS - frozenset({
    "--brief", "--handoff", "--full", "--workflow-manifest", "--workflow-full", "--json-only",
    "--context-only", "--previous", "--tools",
})
_DIGEST_PATH_OPTIONS = frozenset({"--project-dir", "--digest-file", "--match-file", "--workflow-dir"})


def trusted_root(payload):
    """The session's project root, when the shell's working directory is that root; else None.

    A relative script or path in the command runs from the shell's cwd, so a gate judges the
    command only where that cwd is the project it trusts (CLAUDE_PROJECT_DIR). Anywhere else,
    another checkout's copy of a tool would ride the approval. A payload without `cwd` is judged
    against the project root."""
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project:
        return None
    root = Path(project).resolve()
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if cwd and os.path.normcase(str(Path(cwd).resolve())) != os.path.normcase(str(root)):
        return None
    return root


def allow(reason):
    return {"hookEventName": "PreToolUse", "permissionDecision": "allow",
            "permissionDecisionReason": "Auto-approved: " + reason}


def _inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve(root, value):
    """Resolve an argument lexically; reject traversal before any symlink inspection."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if ".." in path.parts:
        return None
    return path if path.is_absolute() else root / path


def _path_has_symlink(root, path):
    """True when any component below `root` is a symlink or a Windows directory junction, which
    `is_symlink()` does not report."""
    if not _inside(path, root):
        return True
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(current)):
            return True
    return False


def _in_peer_worktree(root, path):
    rel = path.relative_to(root).parts
    return len(rel) >= 2 and rel[0].casefold() == ".claude" and rel[1].casefold() == "worktrees"


def _regular_project_file(root, value):
    return _project_path(root, value, "file") is not None


def _project_path(root, value, kind=None):
    root = Path(root).resolve()
    path = _resolve(root, value)
    if path is None or not _inside(path, root) or _path_has_symlink(root, path):
        return None
    if _in_peer_worktree(root, path):
        return None
    if kind == "file" and not path.is_file():
        return None
    if kind == "dir" and not path.is_dir():
        return None
    if kind == "parent" and (not path.parent.is_dir() or _path_has_symlink(root, path.parent)):
        return None
    return path


def _tracked_file(root, path):
    try:
        rel = path.relative_to(root).as_posix()
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def simple_argv(command):
    """Parse exactly one simple shell command, rejecting all shell expansion and chaining."""
    if not isinstance(command, str) or not command.strip():
        return None, "an empty command"
    if command.lstrip().startswith("cd ") and "&&" in command:
        return None, "a `cd … &&` prefix"
    if _ASSIGNMENT.match(command.lstrip()):
        name = command.lstrip().split("=", 1)[0]
        return None, "an environment assignment `%s=`" % name

    quote = None
    for index, char in enumerate(command):
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char in "$`\\!":
                return None, "shell expansion in double quotes (`%s`)" % char
            continue
        if char in "'\"":
            quote = char
        elif char in ";&|<>()$`{}*?[~!#\\\n\r":
            if char in "<>":
                return None, "a redirection"
            if char == "|":
                return None, "an `||` operator" if command[index:index + 2] == "||" else "a pipe"
            if char == "&":
                return None, "an `&&` operator" if command[index:index + 2] == "&&" else "a background operator"
            if char == ";":
                return None, "a semicolon command separator"
            return None, "shell metacharacter `%s`" % char
    if quote:
        return None, "an unmatched quote"
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None, "an invalid quoted command"
    if not argv:
        return None, "an empty command"
    if _ASSIGNMENT.match(argv[0]):
        name = argv[0].split("=", 1)[0]
        return None, "an environment assignment `%s=`" % name
    return argv, ""


def _sidecar_argv_judge(argv, root):
    return sidecar_launch_shape(shlex.join(argv), root)


def _fanout_judge(_argv, _root):
    return False, "its jobs file or a job path did not pass the fan-out validation in hooks/sidecar_dispatch_context.py"


def _digest_script(root, value):
    path = _resolve(root, value)
    expected = root / ".claude" / "tools" / "session_digest.py"
    return path == expected


def _digest_path_ok(root, option, value):
    candidate = Path(value)
    if ".." in candidate.parts:
        return False
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.absolute()
    allowed_roots = (root, (Path.home() / ".claude" / "projects").resolve())
    allowed = next((base for base in allowed_roots if _inside(candidate, base)), None)
    if allowed is None or _path_has_symlink(allowed, candidate):
        return False
    if allowed == root and _in_peer_worktree(root, candidate):
        return False
    if option in ("--project-dir", "--workflow-dir"):
        return candidate.is_dir()
    return candidate.is_file()


def _digest_value_ok(option, value):
    if option == "--workflow-kind":
        return value in {"review", "explore"}
    if option == "--workflow-page":
        return value in {"items", "lenses", "reports", "gaps", "merges"}
    if option == "--evidence-page":
        return value in {"prompts", "friction", "files"}
    if option == "--expect-journal-sha256":
        return bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))
    if option in {"--page", "--page-size", "--list"}:
        try:
            number = int(value)
        except ValueError:
            return False
        return (1 <= number <= 100 if option in {"--page-size", "--list"}
                else 1 <= number <= 10000)
    return True


def _digest_shape(argv, root):
    if not isinstance(argv, list) or len(argv) < 3 or argv[0] not in ("python", "python3"):
        return False, "the command is not `python|python3 <tracked session_digest.py> <reviewed options>`"
    root = Path(root).resolve()
    expected = root / ".claude" / "tools" / "session_digest.py"
    if (not _digest_script(root, argv[1]) or not _regular_project_file(root, argv[1])
            or not _tracked_file(root, expected)):
        return False, "the session-digest script is not the tracked project tool"
    index = 2
    while index < len(argv):
        raw = argv[index]
        option, has_inline, inline_value = raw.partition("=")
        if option not in _DIGEST_OPTIONS:
            return False, "an unreviewed session-digest option `%s`" % option
        if option in _DIGEST_VALUE_OPTIONS:
            if has_inline:
                value = inline_value
            elif index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                index += 1
                value = argv[index]
            else:
                return False, "option `%s` is missing its value" % option
            if not value:
                return False, "option `%s` has an empty value" % option
            if not _digest_value_ok(option, value):
                return False, "option `%s` has an unreviewed value" % option
            if option in _DIGEST_PATH_OPTIONS and not _digest_path_ok(root, option, value):
                return False, "option `%s` must resolve inside the project or `~/.claude/projects`, " \
                    "outside peer worktrees and without symlinks" % option
        elif has_inline:
            return False, "flag `%s` does not take a value" % option
        index += 1
    return True, ""


def _commit_shape(argv, root):
    """A local commit adds no capability: the working tree already runs what it records, and
    every commit deny guard runs ahead of this approver."""
    root = Path(root).resolve()
    parsed = _git_commit_args(argv)
    if parsed is None:
        return False, "the command is not `git commit`"
    args, chdir, inline_env = parsed
    if inline_env or argv[1:len(argv) - len(args)] not in ([], ["-C", chdir]):
        return False, "only `-C <project root>` may precede `commit` (no `-c` or other global flags)"
    if chdir is not None:
        target = Path(chdir)
        if not target.is_absolute() or os.path.normcase(str(target.resolve())) != os.path.normcase(str(root)):
            return False, "`git -C` must name the project root"
    rest = args[1:]
    split = rest.index("--") if "--" in rest else len(rest)
    flags, paths = rest[:split], rest[split + 1:]
    message = None
    index = 0
    while index < len(flags):
        if flags[index] == "-q":
            index += 1
        elif flags[index] == "-F" and index + 1 < len(flags) and message is None:
            message = flags[index + 1]
            index += 2
        else:
            return False, "an unreviewed commit flag or bare pathspec `%s`" % flags[index]
    message_path = _project_path(root, message, "file") if message else None
    if message_path is None or not _inside(message_path, root / ".claude" / "scratch"):
        return False, "`-F` must name an existing message file under `.claude/scratch/`"
    if split < len(rest) and not paths:
        return False, "no paths after `--`"
    for value in paths:
        if value.startswith(":") or _project_path(root, value) is None:
            return False, "path `%s` must be a plain in-project path outside peer worktrees" % value
    return True, ""


def _git_commit_args(argv):
    """`_git_commit.git_invocation` for an argv whose subcommand is `commit`, else None."""
    parsed = _git_commit.git_invocation(shlex.join(argv))
    return parsed if parsed is not None and parsed[0][:1] == ["commit"] else None


def owning_contract(argv, root):
    if not argv:
        return None
    root = Path(root).resolve()
    if argv[0] == "git" and _git_commit_args(argv) is not None:
        return next(c for c in CONTRACTS if c.name == "git-commit")
    if argv[0] == "bash" and len(argv) > 1:
        script = _resolve(root, argv[1].replace("\\", "/"))
        if script is not None and _inside(script, root):
            rel = script.relative_to(root).as_posix()
            if rel.startswith(".claude/scripts/") and rel.endswith("_sidecar.sh"):
                return next(c for c in CONTRACTS if c.name == "sidecar-launch")
    if argv[0] in ("python", "python3") and len(argv) > 1:
        if _digest_script(root, argv[1]):
            return next(c for c in CONTRACTS if c.name == "session-digest")
        script = _resolve(root, argv[1].replace("\\", "/"))
        if script is not None and script == root / ".claude" / "tools" / "sidecar_fanout.py":
            return next(c for c in CONTRACTS if c.name == "sidecar-fanout")
    return None


def sidecar_launch_shape(command, root):
    argv, why = simple_argv(command)
    if argv is None:
        return False, why
    if len(argv) < 2 or argv[0] != "bash":
        return False, "the launcher must be invoked as `bash <tracked launcher>`"
    root = Path(root).resolve()
    launcher = _resolve(root, argv[1])
    if (launcher is None or not _inside(launcher, root)
            or not re.fullmatch(r"\.claude/scripts/[^/]+_sidecar\.sh", launcher.relative_to(root).as_posix())
            or _path_has_symlink(root, launcher) or not launcher.is_file()
            or not _tracked_file(root, launcher)):
        return False, "the launcher is not a tracked regular `.claude/scripts/*_sidecar.sh` file"
    args = argv[2:]
    import sidecar_argv
    pairs, operand_index = sidecar_argv.sidecar_flag_pairs(args)
    if operand_index != len(args):
        return False, "an operand follows the sidecar flags"
    for letter, value in pairs:
        if letter not in _ALLOWED_SIDECAR_FLAGS:
            return False, "sidecar flag `-%s` is not approved" % letter
        if letter in sidecar_argv.SIDECAR_VALUE_FLAGS and not isinstance(value, str):
            return False, "sidecar flag `-%s` is missing its value" % letter
        if letter == "G" and value not in _ALLOWED_SHAPES:
            return False, "`-G` must be `any`, `survey`, or `review`"
        if letter == "t":
            tools = set(value.split(",")) if isinstance(value, str) else set()
            if not tools or not tools <= _ALLOWED_TOOLS:
                return False, "`-t` may name only Read, Glob, and Grep"
        if letter in _PATH_FLAGS:
            kind = ("file" if letter in _EXISTING_FILE_FLAGS else
                    "dir" if letter in _EXISTING_DIR_FLAGS else "parent")
            path = _project_path(root, value, kind)
            if path is not None and letter in _PARENT_FLAGS and (
                    not _inside(path, root / ".claude" / "scratch")
                    or (letter in "RP" and path.exists())):
                return False, "`-%s` must name a new file under `.claude/scratch/`" % letter
            if path is None:
                if letter == "a" and isinstance(value, str):
                    candidate = _resolve(root, value)
                    if candidate is not None and not _inside(candidate, root):
                        return False, "`-a` grants a directory outside the project"
                return False, "`-%s` must resolve to an existing in-project %s without symlinks or `..`" % (
                    letter, kind)
    return True, ""


def _candidate_contracts(command, root):
    import sidecar_argv
    candidates = []
    for segment in sidecar_argv.shell_segments(command):
        words = sidecar_argv.strip_redirections(segment)
        for index, word in enumerate(words):
            if word == "bash" and index + 1 < len(words):
                candidates.append(words[index:])
            elif word in ("python", "python3", "git") and index + 1 < len(words):
                candidates.append(words[index:])
    return [owning_contract(argv, root) for argv in candidates]


def _contract_reason(contract, command, root):
    argv, why = simple_argv(command)
    if argv is None:
        return why
    return contract.judge(argv, root)[1]


def explain(payload):
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return None
    root = trusted_root(payload)
    try:
        base = root or Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        contracts = _candidate_contracts(command, base)
    except Exception:
        return None
    contract = next((item for item in contracts if item is not None), None)
    if contract is None:
        return None
    argv, shape_why = simple_argv(command)
    if argv is None:
        why = shape_why
    elif root is None:
        why = "the shell's working directory is not the project root; run it from the project root"
    else:
        why = _contract_reason(contract, command, root) or "the command did not meet the gate's checks"
    return "%s gate owns this command but did not approve it: %s. Canonical shape: %s." % (
        contract.name, why, contract.shape)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    root = trusted_root(payload)
    if root is None:
        return
    argv, _why = simple_argv(command)
    if argv is None:
        return
    contract = owning_contract(argv, root)
    if not contract or contract.approver != "approving_gates":
        return
    ok, reason = contract.judge(argv, root)
    if ok:
        sys.stdout.write(json.dumps({"hookSpecificOutput": allow("%s: canonical shape" % contract.name)}))


CONTRACTS = (
    Contract("sidecar-launch", _sidecar_argv_judge,
             "`bash .claude/scripts/<tracked>_sidecar.sh` with reviewed flags and project paths",
             "sidecar_dispatch_context"),
    Contract("sidecar-fanout", _fanout_judge,
             "`python3 .claude/tools/sidecar_fanout.py` with validated jobs under scratch",
             "sidecar_dispatch_context"),
    Contract("session-digest", _digest_shape,
             "`python3 .claude/tools/session_digest.py` with reviewed options and rooted paths",
             "approving_gates"),
    Contract("git-commit", _commit_shape,
             "`git commit [-q] -F .claude/scratch/<msg> [-- <in-project paths>]`, from the project root",
             "approving_gates"),
)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
