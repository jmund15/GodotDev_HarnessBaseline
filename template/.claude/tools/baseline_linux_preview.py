#!/usr/bin/env python3
"""Run the baseline CI's Ubuntu lane against a publish worktree inside WSL, to reproduce a red PR lane.

    python3 .claude/tools/baseline_linux_preview.py [<journal-id>]   # default: newest worktree
    python3 .claude/tools/baseline_linux_preview.py --worktree PATH  # a tree anywhere
    python3 .claude/tools/baseline_linux_preview.py --worktree PATH --lanes a,b  # only those lanes
    python3 .claude/tools/baseline_linux_preview.py <id> --changed   # only the proofs bound to the commit's paths
    python3 .claude/tools/baseline_linux_preview.py --setup          # once: python 3.x + node 20 + pwsh + dotnet runtime, no sudo
    python3 .claude/tools/baseline_linux_preview.py --help

Windows validation cannot see Linux-only failures: a `C:/` path is relative on POSIX, and real
symlinks exist. Each one otherwise surfaces only as a red PR check, costing a closed PR and a
fresh dry run. This tool runs every `run:` step of every job, one lane per job, in the worktree's own
`.github/workflows/baseline.yml` in order, so it tracks CI without a copied step list. Unlike
CI, it runs every step after a failure. A person runs it to rerun a red; `publish` runs it before each PR push, each lane limited to the proofs bound to the paths the publish commit changes (`scope_to_paths`).

The worktree is copied, without `.git`, to the run's own tree under `~/baseline-linux-preview`, named
for the worktree and the run's pid, and committed to a fresh repo, as `actions/checkout` gives; a run
owns only its own directory, removes it when green, and sweeps the trees of runs that ended. Never
`/tmp`: the delete guards admit temp paths as safe
targets, which flips the proofs that plant an unsafe path. `~/.local/ci-tools/{py/bin,node/bin,pwsh}`
lead PATH. WSL's Windows `/mnt` entries are dropped from it, as a runner has none. `--setup` downloads the latest Python (a uv-managed venv, as setup-python's `3.x`), Node 20
and the latest PowerShell there and checks their SHA-256.

Full output: `.claude/scratch/linux_preview/<worktree>.log`. Stdout carries each step's verdict,
FAIL lines and the battery summary, then one `::result <json>` line: `status` green | red | incomplete |
unavailable, and per lane its seconds, failed steps and failed proofs.
Exit: 0 every lane passed; 1 a lane failed; 2 an unknown option; 3 the preview broke (no worktree, a workflow with no parsed
step, a launch that produced no lane verdicts); 4 this machine cannot run it (no WSL, a missing
`--setup` tool, or a tree with no baseline workflow), which is no verdict on the lanes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_identity  # noqa: E402
import machine_slots  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / ".claude" / ".cache" / "baseline-publish"
LOG_DIR = REPO / ".claude" / "scratch" / "linux_preview"
WORKFLOW = Path(".github") / "workflows" / "baseline.yml"
STEP_EOF = "PREVIEW_STEP_EOF"
# WSL appends the Windows PATH under /mnt; a CI runner has none of it, so a proof that finds a Windows
# binary there (claude.cmd) passes here and fails in CI.
STRIP_HOST_PATH = ('_kept=; IFS=: read -ra _dirs <<< "$PATH"; for _d in "${_dirs[@]}"; do'
                   ' case "$_d" in /mnt/*) ;; *) _kept="${_kept:+$_kept:}$_d" ;; esac; done; PATH="$_kept"')
SHOWN = re.compile(r"^(::(step|pass|fail|lane|unavailable) |FAIL|harness_tests:|\s+\| FAIL)")
# What the verdict reads; a battery's progress lines stay in the log.
VERDICT_LINE = re.compile(r"^(::(fail|lane|unavailable) |FAIL \d|harness_tests: (scoped: )?\d+ run, )")
BATTERY = re.compile(r"^harness_tests: (?:scoped: )?(\d+) run, (\d+) pass, (\d+) fail, (\d+) cannot-run")
LANE_END = re.compile(r"^::lane (\S+) (\d+)s$")
STEP_FAIL = re.compile(r"^::fail (.+) rc=\d+ \d+s$")
PROOF_FAIL = re.compile(r"^FAIL \d+(?:\.\d+)?s (\S.*)$")
BATTERY_CALL = re.compile(r"python3 (\S*harness_tests\.py)(?=\s|$)(?! --)")
ROOT_PROOFS = re.compile(r"for f in tests/test_\*\.py")
SKIPPED_SCOPED = ("echo 'harness_tests: scoped: 0 run, 0 pass, 0 fail, 0 cannot-run, 0 skipped"
                  " (no template path changed)'")
TEMPLATE = "template/"
# `--proofs-for` exits 1 for "no proof selected" as for a red proof; a change that binds no proof is no red.
SCOPED_SHIM = (r'_scoped() { local o rc; o=$(mktemp); set +e; "$@" 2>&1 | tee "$o"; rc=${PIPESTATUS[0]}; set -e;'
               r' if [ "$rc" = 1 ] && grep -q "^harness_tests: no proof selected" "$o"; then'
               r' echo "harness_tests: scoped: 0 run, 0 pass, 0 fail, 0 cannot-run, 0 skipped (no proof bound to the changed paths)";'
               r' rc=0; fi; rm -f "$o"; return $rc; }')
TOOLS = ("py/bin/python", "node/bin/node", "pwsh/pwsh", "dotnet/dotnet")

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
U=https://github.com/astral-sh/uv/releases/latest/download
q=uv-x86_64-unknown-linux-gnu.tar.gz
curl -fsSLO "$U/$q"
want=$(curl -fsSL "$U/$q.sha256" | awk '{print tolower($1)}')
[ -n "$want" ] || { echo "no sha256 for $q" >&2; exit 1; }
echo "$want  $q" | sha256sum -c -
rm -rf uv py && mkdir uv && tar -xzf "$q" -C uv --strip-components=1 && rm -f "$q"
UV_PYTHON_INSTALL_DIR="$T/pythons" "$T/uv/uv" venv --seed --managed-python py
"$T/py/bin/python" --version
"$T/node/bin/node" --version
"$T/pwsh/pwsh" -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
curl -fsSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh
bash dotnet-install.sh --runtime dotnet --channel LTS --install-dir "$T/dotnet" && rm -f dotnet-install.sh
"$T/dotnet/dotnet" --list-runtimes
"""


def _run_steps(lines: list[str]) -> list[tuple[str, str]]:
    """`(name, script)` for every step carrying `run:` in one job's lines."""
    steps: list[tuple[str, str]] = []
    name = None
    i = 0
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


def ci_jobs(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """`(job id, [(name, script)])` for every job with a `run:` step, in file order. A job that
    declares `needs:` is skipped: it gates other jobs' results, and this tool's exit code is that
    gate. Handles a one-line `run:` and a `run: |` block; `uses:` steps are skipped. Covers the
    shape baseline.yml uses, not all YAML."""
    lines = text.splitlines()
    try:
        start = next(n for n, line in enumerate(lines) if line.rstrip() == "jobs:") + 1
    except StopIteration:
        return []
    heads = [n for n in range(start, len(lines)) if re.match(r"^  [A-Za-z0-9_-]+:\s*$", lines[n])]
    jobs: list[tuple[str, list[tuple[str, str]]]] = []
    for k, head in enumerate(heads):
        body = lines[head + 1:heads[k + 1] if k + 1 < len(heads) else len(lines)]
        if any(re.match(r"^    needs:", line) for line in body):
            continue
        at = next((n for n, line in enumerate(body) if line.strip() == "steps:"), None)
        steps = _run_steps(body[at + 1:]) if at is not None else []
        if steps:
            jobs.append((lines[head].strip()[:-1], steps))
    return jobs


def wsl_path(path: str) -> str:
    """`C:\\a\\b` or `C:/a/b` as WSL's `/mnt/c/a/b`."""
    p = str(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", p)
    return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2)) if m else p


def _quote(value: str) -> str:
    return "'%s'" % value.replace("'", "'\\''")


def exec_paths_of(worktree: Path) -> list[str]:
    """The worktree's 100755 index entries: the files checkout gives the executable bit."""
    out = subprocess.run(["git", "-C", str(worktree), "ls-files", "-s", "-z"], capture_output=True).stdout
    return [path for path, mode in baseline_identity.file_modes(out).items() if mode == "100755"]


def summarize(lines: list[str], expected: list[str]) -> dict:
    """The verdict of one preview run, read from its output. Each lane's block ends at its `::lane` line, so
    the `::fail` and `FAIL` lines before it belong to that lane. `green` needs every expected lane closed and
    none failed; a lane the log never closes makes the run `incomplete`, which is not green."""
    lanes: list[dict] = []
    steps: list[str] = []
    proofs: list[str] = []
    counts: dict | None = None
    for line in lines:
        failed = STEP_FAIL.match(line)
        if failed:
            steps.append(failed.group(1))
            continue
        proof = PROOF_FAIL.match(line)
        if proof:
            name = proof.group(1).rsplit("/tests/", 1)[-1]
            if name not in proofs:
                proofs.append(name)
            continue
        battery = BATTERY.match(line)
        if battery:
            run, passed, failed_count, not_run = (int(n) for n in battery.groups())
            counts = {"run": run, "pass": passed, "fail": failed_count, "cannot_run": not_run}
            continue
        closed = LANE_END.match(line)
        if closed:
            lanes.append({"lane": closed.group(1), "status": "red" if steps else "green",
                          "seconds": int(closed.group(2)), "failed_steps": steps, "failed_proofs": proofs,
                          "counts": counts})
            steps, proofs, counts = [], [], None
    seen = [lane["lane"] for lane in lanes]
    missing = [name for name in expected if name not in seen]
    status = "incomplete" if (not lanes or missing) else "red" if any(l["status"] == "red" for l in lanes) else "green"
    return {"status": status, "lanes": lanes, "missing": missing}


def changed_paths(worktree: Path) -> list[str]:
    """The paths the worktree's HEAD commit adds, changes or deletes against its parent: a publish commit's scope."""
    out = subprocess.run(["git", "-C", str(worktree), "diff", "--no-renames", "--name-only", "-z", "HEAD^", "HEAD"],
                         capture_output=True)
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def scope_to_paths(jobs: list[tuple[str, list[tuple[str, str]]]],
                   paths: list[str] | None) -> list[tuple[str, list[tuple[str, str]]]]:
    """`jobs` with each `harness_tests.py` call limited to the proofs bound to `paths` (the runner's `--proofs-for`),
    so a lane's wall tracks the change. Every battery the workflow runs, the template's or a bootstrapped profile's,
    reads template-relative paths, so `paths` under `template/` lose that prefix. None keeps every battery whole. A
    publish that changes nothing under `template/` leaves the battery nothing to run. The baseline-repo root proofs
    (`tests/test_*.py`, over the root tools and the template as a whole) run only when a path outside `template/`
    changed."""
    if paths is None:
        return jobs
    mine = [p[len(TEMPLATE):] for p in paths if p.startswith(TEMPLATE)]
    root_changed = any(not p.startswith(TEMPLATE) for p in paths)
    args = " ".join(_quote(p) for p in mine)
    scoped = []
    for job, steps in jobs:
        out = []
        for name, body in steps:
            if ROOT_PROOFS.search(body):
                out.append((name, body if root_changed else
                            "echo 'baseline-repo root proofs: 0 run (no path outside template/ changed)'"))
            elif not BATTERY_CALL.search(body):
                out.append((name, body))
            elif mine:
                out.append((name, SCOPED_SHIM + "\n" + BATTERY_CALL.sub(
                    lambda m: "_scoped python3 %s --proofs-for %s" % (m.group(1), args), body)))
            else:
                out.append((name, BATTERY_CALL.sub(lambda m: SKIPPED_SCOPED, body)))
        scoped.append((job, out))
    return scoped


def preview_script(src: str, jobs: list[tuple[str, list[tuple[str, str]]]], origin: str | None = None,
                   exec_paths: list[str] | None = None, run: str = "preview", preflight: bool = False) -> str:
    """The bash script WSL runs: snapshot `src` once under $HOME, copy it per job, then run every job
    as its own lane at once, each step in its own shell. `origin` becomes the snapshot's origin remote,
    as checkout sets it; proofs read it. `exec_paths` are the 100755 entries, which get their
    executable bit back after the 644 reset. Each run has its own tree, named for the worktree and
    the run's pid, so a second preview never deletes a running one; a green run removes its own tree
    and a run sweeps the trees of runs that ended. Each lane has its own copy and HOME, as a CI job
    has its own checkout and runner. A lane is a coordinator and holds no machine slot: its runner takes one
    Windows slot per proof through the interop broker, so the lanes together run as wide as the pool. Lane logs print in job order; the exit is 1 when any lane
    failed. `run` labels the run's own directory under ~/baseline-linux-preview; `preflight` exits 4
    with `::unavailable` before any copy when a `--setup` tool is missing."""
    slot_script = str(Path(machine_slots.__file__).resolve())
    bootstrap = wsl_path(str(REPO / '.claude' / 'scripts' / '_slot_bootstrap'))
    parts = ["set -u",
             'export HARNESS_WINDOWS_PYTHON=' + _quote(wsl_path(sys.executable)),
             'export HARNESS_WINDOWS_SLOT_SCRIPT=' + _quote(slot_script),
             'export HARNESS_MACHINE_ROOT=' + _quote(wsl_path(machine_slots.machine_root())),
             'export HARNESS_SLOT_SCRIPT=' + _quote(wsl_path(slot_script)),
             'export HARNESS_SLOT_PYTHON=python3',
             'export BASH_ENV=' + _quote(bootstrap + '/bash_slots.sh'),
             'export PYTHONPATH=' + _quote(bootstrap) + '${PYTHONPATH:+:$PYTHONPATH}',
             'export NODE_OPTIONS=' + _quote('--require ' + json.dumps(bootstrap + '/node_slots.js')),
             'unset HARNESS_SLOT_DIR HARNESS_SLOT_BUDGET']
    if preflight:
        parts.append('for t in %s; do [ -x "$HOME/.local/ci-tools/$t" ] || { echo "::unavailable ci-tools missing $t;'
                     ' run baseline_linux_preview.py --setup"; exit 4; }; done' % " ".join(TOOLS))
    parts += [
        STRIP_HOST_PATH,
        'export PATH="$HOME/.local/ci-tools/py/bin:$HOME/.local/ci-tools/node/bin:$HOME/.local/ci-tools/pwsh:$HOME/.local/ci-tools/dotnet:$PATH"',
        'R="$HOME/baseline-linux-preview"; D="$R/%s.$$"' % run,
        # A dot entry is the shared slot dir, not a run's tree; a live pid's tree belongs to a peer.
        'for d in "$R"/*; do n=${d##*/}; case "$n" in .*) continue ;; esac;'
        ' [ -d "$d" ] && ! kill -0 "${n##*.}" 2>/dev/null && rm -rf "$d"; done',
        'rm -rf "$D" && mkdir -p "$D/src" && cd "$D/src" || exit 3',
        "tar -C %s --exclude=.git -cf - . | tar -xf - || exit 3" % _quote(src),
        # drvfs reports every file executable; checkout gives 100644 except the 100755 entries, restored below
        "find . -type f -exec chmod 644 {} +",
    ]
    if exec_paths:
        parts.append("chmod +x " + " ".join(_quote(p) for p in exec_paths))
    parts += [
        "git init -q -b main && git -c user.email=preview@local -c user.name=preview add -A"
        " && git -c user.email=preview@local -c user.name=preview commit -qm snapshot || exit 3",
    ]
    if origin:
        parts.append("git remote add origin %s || exit 3" % _quote(origin))
    parts += [
        'cd "$D"; export D',
        "pids=()",
    ]
    for job, steps in jobs:
        parts += [
            'cp -a "$D/src" "$D/%s" && mkdir -p "$D/%s.home" || exit 3' % (job, job),
            "( /bin/bash --noprofile --norc <<'PREVIEW_LANE_EOF'",
            'HOME="$D/%s.home"; export HOME; cd "$D/%s" || exit 3; fail=0; lane0=$SECONDS' % (job, job),
        ]
        for name, body in steps:
            label = name.replace("'", "").replace('"', "").replace("$", "").replace("`", "")
            parts += [
                "echo '::step %s'; s0=$SECONDS" % label,
                "if bash --noprofile --norc -eo pipefail <<'%s'" % STEP_EOF,
                body,
                STEP_EOF,
                "then echo \"::pass %s $((SECONDS-s0))s\"; else rc=$?; echo \"::fail %s rc=$rc $((SECONDS-s0))s\"; fail=1; fi"
                % (label, label),
            ]
        parts += ['echo "::lane %s $((SECONDS-lane0))s"; exit $fail' % job,
                  'PREVIEW_LANE_EOF', ') > "$D/%s.log" 2>&1 &' % job, "pids+=($!)"]
    parts += ["fail=0", 'for p in "${pids[@]}"; do wait "$p" || fail=1; done']
    parts += ['cat "$D/%s.log"' % job for job, _ in jobs]
    parts += ['[ "$fail" = 0 ] && rm -rf "$D"', "exit $fail"]
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


def _run_wsl(script: str, log_path: Path | None, sink: list[str] | None = None) -> int:
    """Run `script` in WSL; the full output goes to `log_path`, the SHOWN lines to stdout and the lines a
    verdict reads to `sink`."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script_path = LOG_DIR / ((log_path.stem if log_path else "setup") + ".sh")
    script_path.write_bytes(script.encode())
    proc = subprocess.Popen(["wsl.exe", "-e", "bash", wsl_path(str(script_path))],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = open(log_path, "w", encoding="utf-8", newline="\n") if log_path else None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").replace("\0", "").rstrip("\r\n")
            if log:
                log.write(line + "\n")
            if sink is not None and VERDICT_LINE.match(line):
                sink.append(line)
            if log is None or SHOWN.match(line):
                print(line, flush=True)
    finally:
        if log:
            log.close()
    return proc.wait()


def _no_wsl() -> bool:
    return shutil.which("wsl.exe") is None


def _unavailable(reason: str) -> tuple[int, dict]:
    print("::unavailable " + reason)
    return 4, {"status": "unavailable", "reason": reason, "lanes": []}


def preview(worktree: Path, only: list[str] | None = None,
            paths: list[str] | None = None) -> tuple[int, dict | None]:
    """Run the lanes of `worktree`'s baseline workflow in WSL. Returns (exit code, result): the result is the
    `::result` object (None when the run never starts, and that error goes to stderr). Exit codes as the module
    docstring gives them. `only` names the lanes to run; None runs every lane. `paths` limits each lane's battery to the
    proofs bound to those changed paths; None runs every battery whole."""
    workflow = worktree / WORKFLOW
    if not workflow.is_file():
        return _unavailable("no %s in the tree: no CI lane to run" % WORKFLOW.as_posix())
    jobs = ci_jobs(workflow.read_text(encoding="utf-8"))
    if only:
        unknown = [name for name in only if name not in [job for job, _ in jobs]]
        if unknown:
            print("error: no lane %s in %s" % (", ".join(unknown), WORKFLOW.as_posix()), file=sys.stderr)
            return 3, None
        jobs = [(job, job_steps) for job, job_steps in jobs if job in only]
    jobs = scope_to_paths(jobs, paths)
    step_count = sum(len(job_steps) for _, job_steps in jobs)
    if not step_count:
        print("error: no run: step parsed from %s; a preview of nothing is not green"
              % (worktree / WORKFLOW).as_posix(), file=sys.stderr)
        return 3, None
    if _no_wsl():
        return _unavailable("no WSL (wsl.exe not found)")
    log_path = LOG_DIR / (worktree.name + ("." + "+".join(only) if only else "") + ".log")
    print("preview %s: %d step(s); log %s" % (worktree.name, step_count, log_path), flush=True)
    origin = subprocess.run(["git", "-C", str(worktree), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip() or None
    run = re.sub(r"[^A-Za-z0-9._-]", "_", worktree.name)
    sink: list[str] = []
    rc = _run_wsl(preview_script(wsl_path(str(worktree)), jobs, origin, exec_paths_of(worktree), run, True),
                  log_path, sink)
    if rc == 4:
        said = next((line[len("::unavailable "):] for line in sink if line.startswith("::unavailable ")),
                    "a tool the preview needs is missing")
        return _unavailable(said)
    result = summarize(sink, [job for job, _ in jobs])
    result["log"] = str(log_path)
    if rc == 3 or result["status"] not in ("green", "red"):
        return 3, result
    return (1 if result["status"] == "red" or rc else 0), result


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print((__doc__ or "").strip())
        return 0
    only: list[str] = []
    if "--lanes" in argv[1:]:
        at = argv.index("--lanes", 1)
        only = [name for name in (argv[at + 1] if at + 1 < len(argv) else "").split(",") if name]
        argv = argv[:at] + argv[at + 2:]
    changed = "--changed" in argv
    argv = [a for a in argv if a != "--changed"]
    if argv[:1] and argv[0].startswith("-") and argv[0] not in ("--setup", "--worktree"):
        print("usage: baseline_linux_preview.py [<journal-id> | --worktree PATH] [--lanes a,b] [--changed] | --setup | --help\n"
              "error: unknown option %s" % argv[0], file=sys.stderr)
        return 2
    if argv[:1] == ["--setup"]:
        if _no_wsl():
            print("error: wsl.exe not found; this preview needs WSL", file=sys.stderr)
            return 3
        return _run_wsl(SETUP_SCRIPT, None)
    if argv[:1] == ["--worktree"]:
        given = argv[1] if len(argv) > 1 else ""
        worktree = Path(given).resolve() if given and Path(given).is_dir() else None
        if worktree is None:
            print("error: no publish worktree at %s" % (given or "(no path given)"), file=sys.stderr)
            return 3
    else:
        worktree = resolve_worktree(CACHE, argv[0] if argv else None)
        if worktree is None:
            print("error: no publish worktree%s under %s"
                  % (" for " + argv[0] if argv else "", CACHE), file=sys.stderr)
            return 3
    rc, result = preview(worktree, only or None, changed_paths(worktree) if changed else None)
    if result is not None:
        print("::result " + json.dumps(result), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
