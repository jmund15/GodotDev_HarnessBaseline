#!/usr/bin/env python3
"""The launch checks both dispatch routes share: the seat launcher (`scripts/seat.sh`) and the native Workflow
entry (`hooks/workflow_transport.py`, B3). The three CLI-process refusals (a writing seat outside its own linked
worktree, a full seat, a login at its capacity floor) stay in `seat.sh`; they mean nothing for a Workflow job.

    launch_check.py check --brief <brief.md> --out <out.md> --root <checkout> --writes yes|no
        Refuses (exit 2, one stderr line) when the brief, the output directory or an `inputs:` entry does not
        resolve under --root, or a `writes: no` job's brief names a `targets:` entry other than <out.md>.
    launch_check.py seat <seat>
        Prints the seat's `seats.json` row as shell assignments for seat.sh to eval, with the row of its model: the
        efforts the model accepts (`SEAT_EFFORTS`, space-separated, empty when the model lists none), its window
        (`SEAT_CONTEXT`), output cap (`SEAT_MAX_OUTPUT`) and upstream `SEAT_BASE_URL`. Exit 2 for an unknown seat or one whose login is absent here.
    launch_check.py record <seat> <model> <effort> <rc> <proxy-version> <raw-file> <hold-stderr-file> <record-file>
        The run record of a seat that holds a local backend (scripts/seat_local.sh): writes <record-file> and prints
        the answer, which is the child's `result` text, or its raw output when that is not a result object.
    launch_check.py stage <slot> <brief.md> <out.md> <here> <workdir> <main-checkout|"">
        Writes <slot>/prompt.md (guards/rails.md once, then the brief: the one prompt every adapter reads) and
        <slot>/settings.json (the floor entry; a non-empty main checkout adds the workdir fence, with the output
        directory as its spill dir). Both go through json/str, so a backslash path stays valid.
    launch_check.py pre_launch <seat> <brief.md> <out.md> <label>
    launch_check.py post_return <seat> <brief.md> <out.md> <label>
        Run the project's launch hook (adaptation key `dispatch.pre_launch` / `dispatch.post_return`) in the
        caller's cwd, the job's workdir. An unset key runs nothing. A non-zero exit refuses with the hook's stderr
        verbatim. A `targets:` header is `targets:` on its own line followed by `- <path>` lines; `inputs:` likewise.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
PRE_KEY = "dispatch.pre_launch"
POST_KEY = "dispatch.post_return"
SEATS = "seats.json"
# Python resolves a bare `bash` to System32ash.exe (WSL) on Windows, which cannot read Git Bash paths.
BASH = shutil.which("bash") or "bash"
FLOOR_MATCHER = "Bash|PowerShell|Monitor|TaskStop"  # hooks/pre_bash.py's registered tools
LIST_HEADERS = ("inputs:", "targets:")


def parse_brief(text):
    """`{'inputs': [...], 'targets': [...]}` from the brief's `inputs:` and `targets:` list headers."""
    found = {"inputs": [], "targets": []}
    header = None
    for raw in text.splitlines():
        line = raw.strip()
        if line in LIST_HEADERS:
            header = line[:-1]
        elif header and line.startswith("- "):
            found[header].append(line[2:].strip())
        elif line:
            header = None
    return found


def _resolve(root, path):
    p = Path(path)
    return p if p.is_absolute() else Path(root) / p


def check(brief, out, root, writes):
    """The refusal causes for one job, in order. An empty list allows the launch."""
    brief_path = _resolve(root, brief)
    out_path = _resolve(root, out)
    if not brief_path.is_file():
        return ["brief does not resolve: %s" % brief]
    if not out_path.parent.is_dir():
        return ["output directory does not resolve: %s" % out_path.parent]
    parsed = parse_brief(brief_path.read_text(encoding="utf-8", errors="replace"))
    for item in parsed["inputs"]:
        if not _resolve(root, item).exists():
            return ["input does not resolve against %s: %s" % (root, item)]
    if writes == "no":
        for target in parsed["targets"]:
            if _resolve(root, target).resolve() != out_path.resolve():
                return ["read-only seat names a write target other than %s: %s" % (out, target)]
    return []


def table(claude_dir=CLAUDE_DIR):
    """The whole `seats.json` (`seats`, `models`, `native`): the one parse site. A missing file is an empty table."""
    try:
        return json.loads((Path(claude_dir) / SEATS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _login(row):
    return os.path.expanduser(row.get("configDir") or "")


def seat_rows(claude_dir=CLAUDE_DIR):
    """The seats usable on this machine: no login of their own, or a login dir that exists. Every reader of the
    table calls this, so a seat whose login is absent is invisible to the hooks and tools alike."""
    seats = table(claude_dir).get("seats") or {}
    return {name: row for name, row in seats.items() if not _login(row) or os.path.isdir(_login(row))}


def login_dirs(claude_dir=CLAUDE_DIR):
    """Every login dir, absolute, each once: the default, `CLAUDE_CONFIG_DIR`, then each usable seat's `configDir`."""
    found = ["~/.claude", os.environ.get("CLAUDE_CONFIG_DIR") or ""]
    found += [row["configDir"] for row in seat_rows(claude_dir).values() if row.get("configDir")]
    out = []
    for path in found:
        full = os.path.abspath(os.path.expanduser(path)) if path else ""
        if full and full not in out:
            out.append(full)
    return out


def seat_assignments(seat, claude_dir=CLAUDE_DIR):
    """Shell assignments for one seat. None when the seat is not in the table; a ValueError (one line) when its
    login is not on this machine."""
    row = (table(claude_dir).get("seats") or {}).get(seat)
    if row is None:
        return None
    if _login(row) and not os.path.isdir(_login(row)):
        raise ValueError("seat '%s' has no login on this machine: log in once with `claude` under CLAUDE_CONFIG_DIR=%s"
                         % (seat, _login(row)))
    model = _model_row(row, claude_dir)
    values = {
        "SEAT_ADAPTER": row["adapter"],
        "SEAT_WRITES": row["writes"],
        "SEAT_MAX": str(row["max_concurrent"]),
        "SEAT_CONFIG_DIR": _login(row),
        "SEAT_MODEL": row["model"],
        "SEAT_EFFORTS": " ".join(model.get("efforts") or []),
        "SEAT_CONTEXT": str(model.get("contextTokens") or ""),
        "SEAT_MAX_OUTPUT": str(model.get("maxOutputTokens") or ""),
        "SEAT_BASE_URL": model.get("baseUrl") or "",
    }
    return "\n".join("%s=%s" % (key, shlex.quote(value)) for key, value in values.items())


def _model_row(row, claude_dir):
    return (table(claude_dir).get("models") or {}).get(row["model"]) or {}


def stage(slot, brief, out, here, workdir, main_checkout):
    """The seat's effective prompt and settings, written under `slot`. `here` is the checkout's .claude dir."""
    slot = Path(slot)
    rails = (Path(here) / "guards" / "rails.md").read_text(encoding="utf-8")
    body = Path(brief).read_text(encoding="utf-8", errors="replace")
    (slot / "prompt.md").write_text(rails.rstrip("\n") + "\n\n" + body, encoding="utf-8", newline="\n")
    # The floor rides into every seat, whatever its workdir; it carries the wait checks, since the seat gets no
    # completion wake of its own. A writing seat adds the fence that keeps its writes out of the main checkout.
    groups = [{"matcher": FLOOR_MATCHER, "hooks": [{"type": "command", "command": "python3 " + shlex.quote("%s/hooks/pre_bash.py" % here)}]}]
    if main_checkout:
        fence = " ".join(["python3", shlex.quote("%s/hooks/sidecar_workdir_guard.py" % here)]
                         + [shlex.quote(str(a)) for a in (workdir, main_checkout, os.path.dirname(out), brief)])
        groups.append({"matcher": "Write|Edit|NotebookEdit|Bash", "hooks": [{"type": "command", "command": fence}]})
    (slot / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": groups}}), encoding="utf-8", newline="\n")


HOLD_PREFIX = "[backend_hold] "
ARGV_LABEL = "server argv: "


def record(seat, model, effort, rc, proxy_version, raw, hold_stderr):
    """(answer, record) of one held local run. `raw` is the child's `--output-format json` stdout and `hold_stderr`
    backend_hold's stderr. The record keeps the server's argv line verbatim (`serverArgv`, None when backend_hold
    printed none) and whether it names a reasoning effort (`serverArgvHasEffort`): a run whose server did not start
    with the effort the dispatch asked for is void, and only that line shows it."""
    try:
        result = json.loads(raw)
    except ValueError:
        result = None
    result = result if isinstance(result, dict) and result.get("type") == "result" else None
    held = [line[len(HOLD_PREFIX):] for line in hold_stderr.splitlines() if line.startswith(HOLD_PREFIX)]
    argv = next((line[len(ARGV_LABEL):] for line in held if line.startswith(ARGV_LABEL)), None)
    body = result or {}
    made = {
        "seat": seat, "model": model, "effort": effort, "rc": int(rc), "proxyVersion": proxy_version or None,
        "serverArgv": argv, "serverArgvHasEffort": bool(argv and "reasoning_effort" in argv), "holdLines": held,
        "isError": bool(body.get("is_error")) if result else None, "numTurns": body.get("num_turns"),
        "durationMs": body.get("duration_ms"), "sessionId": body.get("session_id"),
        "usage": body.get("usage"), "modelUsage": body.get("modelUsage"),
    }
    return (str(body.get("result", "")) if result else raw), made


def _adaptation_get(key):
    sys.path.insert(0, str(CLAUDE_DIR / "tools"))
    import adaptation  # noqa: E402  (tools/adaptation.py: the one reader of adaptation.json)
    return adaptation.get(CLAUDE_DIR, key)


def run_hook(key, args, cwd, lookup=_adaptation_get):
    """Run the project hook named by `key` with `args`; returns (exit code, stderr). Unset runs nothing."""
    command = lookup(key)
    if not command:
        return 0, ""
    line = " ".join([command] + [shlex.quote(arg) for arg in args])  # argv via the string: Git Bash drops positional args of `bash -c`
    done = subprocess.run([BASH, "-c", line], cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stderr


def main(argv=None, lookup=_adaptation_get):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: launch_check.py check|seat|record|stage|pre_launch|post_return ...", file=sys.stderr)
        return 2
    verb, rest = args[0], args[1:]
    if verb == "check" and len(rest) == 8:
        opts = dict(zip(rest[::2], rest[1::2]))
        causes = check(opts["--brief"], opts["--out"], opts["--root"], opts["--writes"])
        if causes:
            print("launch refused: " + causes[0], file=sys.stderr)
            return 2
        return 0
    if verb == "seat" and len(rest) == 1:
        try:
            text = seat_assignments(rest[0], CLAUDE_DIR)
        except ValueError as refusal:
            print("launch refused: %s" % refusal, file=sys.stderr)
            return 2
        if text is None:
            print("launch refused: unknown seat '%s' (seats.json)" % rest[0], file=sys.stderr)
            return 2
        print(text)
        return 0
    if verb == "record" and len(rest) == 8:
        seat, model, effort, rc, proxy, raw_file, err_file, out_file = rest
        read = lambda name: Path(name).read_text(encoding="utf-8", errors="replace")
        answer, made = record(seat, model, effort, rc, proxy, read(raw_file), read(err_file))
        Path(out_file).write_text(json.dumps(made, indent=1) + "\n", encoding="utf-8", newline="\n")
        sys.stdout.buffer.write(answer.encode("utf-8"))
        return 0
    if verb == "stage" and len(rest) == 6:
        stage(*rest)
        return 0
    if verb in ("pre_launch", "post_return") and len(rest) == 4:
        key = PRE_KEY if verb == "pre_launch" else POST_KEY
        rc, err = run_hook(key, rest, os.getcwd(), lookup=lookup)
        if rc:
            sys.stderr.write(err)
            return 2
        return 0
    print("usage: launch_check.py check|seat|record|stage|pre_launch|post_return ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
