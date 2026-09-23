#!/usr/bin/env python3
"""Run the baseline CI's Ubuntu lane against a publish worktree inside WSL, before the PR exists.

    python3 .claude/tools/baseline_linux_preview.py [<journal-id>]   # default: newest worktree
    python3 .claude/tools/baseline_linux_preview.py --setup          # once: node 20 + pwsh, no sudo

Windows validation cannot see Linux-only failures: a `C:/` path is relative on POSIX, and real
symlinks exist. Each one otherwise surfaces only as a red PR check, costing a closed PR and a
fresh dry run. This tool runs every `run:` step of the worktree's own
`.github/workflows/baseline.yml` in order, so it tracks CI without a copied step list. Unlike
CI, it runs every step after a failure.

The worktree is copied, without `.git`, to `~/baseline-linux-preview` and committed to a fresh
repo, as `actions/checkout` gives. Never `/tmp`: the delete guards admit temp paths as safe
targets, which flips the proofs that plant an unsafe path. `~/.local/ci-tools/{node/bin,pwsh}`
lead PATH; `--setup` downloads Node 20 and the latest PowerShell there and checks their SHA-256.
WSL's python3 stands in for setup-python's `3.x`.

Full output: `.claude/scratch/linux_preview/<worktree>.log`. Stdout carries each step's verdict,
FAIL lines and the battery summary.
Exit: 0 every step passed; 1 a step failed; 3 no worktree, no workflow, no parsed step or no WSL.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / ".claude" / ".cache" / "baseline-publish"
LOG_DIR = REPO / ".claude" / "scratch" / "linux_preview"
WORKFLOW = Path(".github") / "workflows" / "baseline.yml"
STEP_EOF = "PREVIEW_STEP_EOF"
SHOWN = re.compile(r"^(::(step|pass|fail) |FAIL|harness_tests:|\s+\| FAIL)")

SETUP_SCRIPT = r"""set -euo pipefail
T="$HOME/.local/ci-tools"; mkdir -p "$T"; cd "$T"
N=https://nodejs.org/dist/latest-v20.x
f=$(curl -fsSL "$N/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $2}')
curl -fsSLO "$N/$f"
curl -fsSL "$N/SHASUMS256.txt" | grep "  $f\$" | sha256sum -c -
rm -rf node && mkdir node && tar -xJf "$f" -C node --strip-components=1 && rm -f "$f"
url=$(curl -fsSL https://api.github.com/repos/PowerShell/PowerShell/releases/latest \
  | grep -o 'https://[^"]*/powershell-[0-9.]*-linux-x64\.tar\.gz' | head -1)
p=${url##*/}
curl -fsSLO "$url"
want=$(curl -fsSL "${url%/*}/hashes.sha256" | iconv -f UTF-16 -t UTF-8 | tr -d '\r' \
  | awk -v f="$p" '$NF == f || $NF == "*" f {print tolower($1)}')
[ -n "$want" ] || { echo "no sha256 for $p" >&2; exit 1; }
echo "$want  $p" | sha256sum -c -
rm -rf pwsh && mkdir pwsh && tar -xzf "$p" -C pwsh && chmod +x pwsh/pwsh && rm -f "$p"
"$T/node/bin/node" --version
"$T/pwsh/pwsh" -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
"""


def ci_steps(text: str) -> list[tuple[str, str]]:
    """`(name, script)` for every step carrying `run:`, in order. Handles a one-line `run:` and a
    `run: |` block; `uses:` steps are skipped. Covers the shape baseline.yml uses, not all YAML."""
    steps: list[tuple[str, str]] = []
    lines = text.splitlines()
    starts = [n for n, line in enumerate(lines) if line.strip() == "steps:"]
    if not starts:
        return steps
    name = None
    i = starts[0] + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("- "):
            name = None
            stripped = stripped[2:].strip()
        if stripped.startswith("name:"):
            name = stripped[len("name:"):].strip().strip("'\"")
        elif stripped.startswith("run:"):
            value = stripped[len("run:"):].strip()
            if value in ("|", "|-", ">", ">-"):
                key_indent = len(lines[i]) - len(lines[i].lstrip())
                body = []
                i += 1
                while i < len(lines) and (not lines[i].strip()
                                          or len(lines[i]) - len(lines[i].lstrip()) > key_indent):
                    body.append(lines[i])
                    i += 1
                steps.append((name or "run %d" % (len(steps) + 1),
                              textwrap.dedent("\n".join(body)).strip("\n")))
                continue
            steps.append((name or "run %d" % (len(steps) + 1), value))
        i += 1
    return steps


def wsl_path(path: str) -> str:
    """`C:\\a\\b` or `C:/a/b` as WSL's `/mnt/c/a/b`."""
    p = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", p)
    return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2)) if m else p


def _quote(value: str) -> str:
    return "'%s'" % value.replace("'", "'\\''")


def preview_script(src: str, steps: list[tuple[str, str]], origin: str | None = None) -> str:
    """The bash script WSL runs: snapshot `src` under $HOME, then every step in its own shell.
    `origin` becomes the snapshot's origin remote, as checkout sets it; proofs read it."""
    parts = [
        "set -u",
        'export PATH="$HOME/.local/ci-tools/node/bin:$HOME/.local/ci-tools/pwsh:$PATH"',
        'D="$HOME/baseline-linux-preview"',
        'rm -rf "$D" && mkdir -p "$D" && cd "$D" || exit 3',
        "tar -C %s --exclude=.git -cf - . | tar -xf - || exit 3" % _quote(src),
        "git init -q -b main && git -c user.email=preview@local -c user.name=preview add -A"
        " && git -c user.email=preview@local -c user.name=preview commit -qm snapshot || exit 3",
    ]
    if origin:
        parts.append("git remote add origin %s || exit 3" % _quote(origin))
    parts.append("fail=0")
    for name, body in steps:
        label = name.replace("'", "").replace('"', "").replace("$", "").replace("`", "")
        parts += [
            "echo '::step %s'" % label,
            "if bash --noprofile --norc -eo pipefail <<'%s'" % STEP_EOF,
            body,
            STEP_EOF,
            "then echo '::pass %s'; else echo \"::fail %s rc=$?\"; fail=1; fi" % (label, label),
        ]
    parts.append("exit $fail")
    return "\n".join(parts) + "\n"


def resolve_worktree(cache: Path, journal: str | None) -> Path | None:
    """The publish worktree for `journal`, or the newest one when `journal` is None."""
    if not cache.is_dir():
        return None
    if journal:
        path = cache / (journal + "-worktree")
        return path if path.is_dir() else None
    found = sorted(p for p in cache.glob("*-worktree") if p.is_dir())
    return found[-1] if found else None


def _run_wsl(script: str, log_path: Path | None) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script_path = LOG_DIR / "preview.sh"
    script_path.write_bytes(script.encode())
    proc = subprocess.Popen(["wsl.exe", "-e", "bash", wsl_path(str(script_path))],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = open(log_path, "w", encoding="utf-8", newline="\n") if log_path else None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").replace("\0", "").rstrip("\r\n")
            if log:
                log.write(line + "\n")
            if log is None or SHOWN.match(line):
                print(line, flush=True)
    finally:
        if log:
            log.close()
    return proc.wait()


def _no_wsl() -> bool:
    if shutil.which("wsl.exe") is None:
        print("error: wsl.exe not found; this preview needs WSL", file=sys.stderr)
        return True
    return False


def main(argv: list[str]) -> int:
    if argv[:1] == ["--setup"]:
        return 3 if _no_wsl() else _run_wsl(SETUP_SCRIPT, None)
    worktree = resolve_worktree(CACHE, argv[0] if argv else None)
    if worktree is None:
        print("error: no publish worktree%s under %s"
              % (" for " + argv[0] if argv else "", CACHE), file=sys.stderr)
        return 3
    workflow = worktree / WORKFLOW
    if not workflow.is_file():
        print("error: %s has no %s" % (worktree, WORKFLOW.as_posix()), file=sys.stderr)
        return 3
    steps = ci_steps(workflow.read_text(encoding="utf-8"))
    if not steps:
        print("error: no run: step parsed from %s; a preview of nothing is not green"
              % (worktree / WORKFLOW).as_posix(), file=sys.stderr)
        return 3
    if _no_wsl():
        return 3
    log_path = LOG_DIR / (worktree.name + ".log")
    print("preview %s: %d step(s); log %s" % (worktree.name, len(steps), log_path), flush=True)
    origin = subprocess.run(["git", "-C", str(worktree), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip() or None
    rc = _run_wsl(preview_script(wsl_path(str(worktree)), steps, origin), log_path)
    return 3 if rc == 3 else (1 if rc else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
