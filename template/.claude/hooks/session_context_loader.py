#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: SessionStart - Worktree setup + development context loader

Engine and language-server steps live in the step modules `session_context_loader_godot.py` and
`session_context_loader_csharp.py`, loaded through `_optional_hooks`; each module's docstring lists
its hooks. This file runs the generic steps, and calls a hook only when its module is installed.

On every session start:
1. Detects if running in a git worktree or cloud environment
2. Initializes empty submodules; reports and preserves existing divergent checkouts
3. Injects git context (branch, commits, submodule status)
4. On cloud: injects the memory index (the godot module adds the cloud contract and persists env vars)
5. On a local machine: reports this checkout's setup stamp (workstation OK / STALE / UNAUDITED)
6. Runs the step-module hooks of each installed layer module (toolchain, build and language-server checks)
"""

import json
import os
import platform
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks
from _bounded_run import run_bounded
from _checkout_root import linked_main_root
from _claude_scope import is_cloud

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

# `git submodule status` line: optional drift prefix, sha, path.
# Empty prefix group == in sync ('-' uninit, '+' checkout != pointer, 'U' conflict).
_SUBMODULE_RE = re.compile(r"^([-+U]?)([0-9a-f]{40})\s+(\S+)")


# ---------------------------------------------------------------------------
# Step modules of higher layers
# ---------------------------------------------------------------------------

_STEP_FAULTS: list[str] = []


def _step_fault(name, exc):
    _STEP_FAULTS.append(f"{name} import {type(exc).__name__}: {exc}")
    return None


# An installed module supplies hooks named in its docstring; an absent one is skipped.
_STEP_MODULES = tuple(module for module in _optional_hooks.load(
    "session_context_loader_csharp", "session_context_loader_godot", on_error=_step_fault) if module)


def _step_hooks(name):
    return [getattr(module, name) for module in _STEP_MODULES if hasattr(module, name)]


def _step_results(name, *args):
    """One result per installed module that has the hook `name`; a raising hook is recorded and skipped."""
    results = []
    for hook in _step_hooks(name):
        try:
            results.append(hook(*args))
        except Exception as exc:
            _STEP_FAULTS.append(f"{name} {type(exc).__name__}: {exc}")
    return results


def get_project_root() -> Path:
    """Get the git toplevel (works in worktrees too)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def is_worktree() -> bool:
    """Return True if the current directory is inside a git worktree (not the main repo)."""
    return bool(linked_main_root(os.getcwd()))


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

def submodule_status(root: Path) -> list[tuple[str, str, str]] | None:
    """[(prefix, sha, path)]; None means the probe failed or ran past its deadline, [] means no
    submodules."""
    try:
        result = run_bounded(["git", "--no-optional-locks", "submodule", "status", "--recursive"], 15, cwd=str(root))
        if result.returncode != 0:
            return None
        entries = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            match = _SUBMODULE_RE.match(line.strip())
            if not match:
                return None
            entries.append((match.group(1) or " ", match.group(2), match.group(3)))
        return entries
    except Exception:
        return None


def _submodule_is_dirty(root: Path, sub_path: str) -> bool | None:
    """Dirty state, or None when the working tree cannot be inspected."""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root / sub_path), "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        return None


def _advance_behind_pin(root: Path, sub_path: str, sha: str) -> bool:
    """Move a clean checkout that is a strict ancestor of the recorded pin onto the pin: it holds no
    independent work (`rules/jmodot_submodule.md` 7). A branch fast-forwards; a detached HEAD detaches."""
    def git(cwd: Path, *args: str):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    try:
        pin = git(root, "rev-parse", "--verify", "-q", f":{sub_path}").stdout.strip()
        sub = root / sub_path
        if not pin or _submodule_is_dirty(root, sub_path) is not False:
            return False
        if git(sub, "merge-base", "--is-ancestor", sha, pin).returncode != 0:
            return False
        on_branch = git(sub, "symbolic-ref", "-q", "HEAD").returncode == 0
        move = git(sub, "merge", "--ff-only", pin) if on_branch else git(sub, "checkout", "--detach", pin)
        return move.returncode == 0
    except Exception:
        return False


def setup_submodule(root: Path) -> str:
    """Initialize empty submodules and advance clean ones that sit behind their pin; preserve every
    other existing checkout, including dirty, ahead or diverged ones."""
    entries = submodule_status(root)
    if entries is None:
        return "UNKNOWN (submodule status failed; no automatic changes)"

    actionable = []
    advanced = []
    blocked = []
    for prefix, sha, path in entries:
        if prefix == " ":
            continue
        if prefix == "-":
            target = root / path
            try:
                if target.exists() and (not target.is_dir() or any(target.iterdir())):
                    blocked.append(f"{path}: uninitialized but nonempty; preserved")
                else:
                    actionable.append(path)
            except OSError:
                blocked.append(f"{path}: cannot inspect directory; preserved")
        elif prefix == "+":
            if _advance_behind_pin(root, path, sha):
                advanced.append(path)
            else:
                blocked.append(f"{path}: checkout {sha[:8]} differs from recorded pointer; preserved")
        else:
            blocked.append(f"{path}: conflict or unknown state; preserved")

    preserved = "; ".join(blocked)
    if not actionable and not advanced:
        return "BROKEN (not auto-fixed): " + preserved if blocked else "OK"

    if actionable:
        try:
            result = subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive", "--", *actionable],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(root)
            )
            if result.returncode != 0:
                return f"FAILED: {result.stderr.strip()[:120]}"
        except subprocess.TimeoutExpired:
            return "FAILED: timeout"
        except Exception as exc:
            return f"FAILED: {exc}"

    after = submodule_status(root)
    if after is None:
        return "UNKNOWN (update returned success; follow-up status failed)"
    if not set(actionable + advanced).issubset({path for prefix, _, path in after if prefix == " "}):
        return "BROKEN (update did not establish the recorded checkout)" + ("; " + preserved if blocked else "")
    done = ", ".join(part for part in (
        "initialized " + ", ".join(actionable) if actionable else "",
        "advanced " + ", ".join(advanced) if advanced else "") if part)
    if blocked or any(prefix != " " for prefix, _, _ in after):
        return "BROKEN (" + done + "); " + (preserved or "other submodules are not at the recorded checkout")
    return "FIXED (" + done + ")"


def resolve_bash() -> str | None:
    """Resolve Git Bash, never WSL bash.

    On Windows `bash` on PATH is C:\\Windows\\System32\\bash.exe — the WSL launcher,
    which cannot see C:/... paths (it wants /mnt/c/...) and fails with a bare
    "No such file or directory". Git Bash sits beside git.exe, so derive it from
    git's own location rather than trusting PATH order.
    """
    if platform.system() != "Windows":
        return "bash"
    git_exe = shutil.which("git")
    if git_exe:
        # .../Git/cmd/git.exe -> .../Git/bin/bash.exe
        candidate = Path(git_exe).parent.parent / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    for fallback in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(fallback).exists():
            return fallback
    found = shutil.which("bash")
    # Reject WSL: it would report every POSIX-path probe as missing.
    if found and "system32" not in found.lower():
        return found
    return None


def sidecar_launchers(registry, seat: str):
    """One available model alias per plan other than the session's `seat` plan, even when launchers
    share a script. A plan (`planOf`) probes one of its route's rows through its own launcher; a
    plan whose own status is unavailable is not listed."""
    models = registry.available_models()
    route_of = getattr(registry, "route_of", lambda name: name)
    launchers = []
    for transport in registry.transports():
        if transport == seat:
            continue
        config = registry.transport_meta(transport) or {}
        if (config.get("status") or {}).get("state") == "unavailable":
            continue
        route = route_of(transport)
        rows = [m for m in models if m.get("transport") == route and m.get("alias")]
        if not rows:
            continue
        launcher = config.get("launcher")
        if launcher:
            rows.sort(key=lambda m: (m.get("lifecycle") != "current", not bool(m.get("roles")),
                                     m.get("order", 9999)))
            launchers.append((transport, os.path.basename(launcher), rows[0]["alias"]))
    return launchers


def roster_health(root: Path) -> str:
    """Benchmark rosters that stopped writing before a terminal marker (bench_lib.roster_summary).

    A roster in a wait loop and one killed by a reboot leave the same silent log; the pid file separates
    them (2026-09-11: two rosters died at a reboot and nothing said so for five days). Discovery failure
    stays UNKNOWN -- an absent BenchmarkArms tree is not a clean one. The arms root comes from the suite's roots
    table (core/roots.py), so its BENCH_ROOTS override reaches this banner too."""
    try:
        bench = os.path.join(root, ".claude", "scripts", "benchmark_campaign")
        if not os.path.isdir(bench):
            return ""  # a project without the battery has no rosters to check
        sys.path[:0] = [bench, os.path.join(bench, "scoring")]
        from core import roots
        import bench_lib
        logs = roots.logs_dir()
        if not os.path.isdir(logs):
            return "UNKNOWN (no logs dir at %s)" % logs
        return bench_lib.roster_summary(logs)
    except Exception as error:
        return f"UNKNOWN ({type(error).__name__}: {error})"


def free_catalog_line(root: Path, source: str) -> str:
    """`free catalog: +<id> -<id>` when the live opencode catalog differs from the free pool's registry rows
    (free_pool.catalog_line), else "". The live read takes seconds, so only an owner's fresh session (startup or
    /clear) pays it: a delegate, a resume and a compaction print nothing. A failed read prints its own line; this
    never raises."""
    if source not in ("startup", "clear") or _sidecar_delegate():
        return ""
    try:
        sys.path.insert(0, str(root / ".claude" / "tools"))
        import free_pool
        return free_pool.catalog_line()
    except Exception as err:
        return f"free catalog: check failed ({err})"


def _sidecar_delegate() -> bool:
    """A sidecar child (CLAUDE_CODE_SIDECAR, set by its launcher) executes a brief; owner-facing banners skip it."""
    return bool(os.environ.get("CLAUDE_CODE_SIDECAR"))


def ladder_banner(root: Path) -> str:
    """One line when a board task has cells the model ladder was not swept for, or a ladder row's model version moved since its clauses were audited; empty when current or in a delegate."""
    if _sidecar_delegate():
        return ""
    try:
        bench = os.path.join(root, ".claude", "scripts", "benchmark_campaign")
        if not os.path.isfile(os.path.join(bench, "scoring", "ladder_lag.py")):
            return ""  # a project without the battery has no board to lag
        sys.path[:0] = [bench, os.path.join(bench, "scoring")]
        import ladder_lag
        return " | ".join(filter(None, (ladder_lag.banner(), ladder_lag.audit_banner())))
    except Exception as error:
        return f"ladder: UNKNOWN ({type(error).__name__}: {error})"


def skill_listing_banner(root: Path, input_data: dict) -> str:
    """One line naming skill-listing entries no file of ours authors and no ruling keeps; empty when none or in a delegate."""
    transcript = input_data.get("transcript_path")
    if not transcript or _sidecar_delegate():
        return ""
    try:
        sys.path.insert(0, os.path.join(root, ".claude", "tools"))
        import skill_listing_review
        return skill_listing_review.banner(Path(transcript).parent, Path(root) / ".claude")
    except Exception as error:
        return f"skill-listing: UNKNOWN ({type(error).__name__}: {error})"


def sidecar_health(root: Path) -> dict[str, str]:
    """Only the current registry selects launchers; discovery failure stays unknown. The launchers are
    checked at once, so the whole block costs one check's deadline, not one per launcher."""
    try:
        sys.path.insert(0, os.path.join(root, ".claude", "tools"))
        import model_registry as registry
        import _session_transport as transport
        launchers = sidecar_launchers(registry, transport.session_plan())
    except Exception as error:
        return {"sidecar_registry": f"UNKNOWN ({type(error).__name__})"}
    if not launchers:
        return {}
    with ThreadPoolExecutor(max_workers=len(launchers)) as pool:
        results = list(pool.map(lambda item: verify_sidecar(root, item[1], item[2]), launchers))
    return {"sidecar-" + name: result for (name, _launcher, _alias), result in zip(launchers, results)}


# Matches the dispatch preflight's deadline; live capacity probes inside a check cap at 35 s.
CHECK_SECONDS = 45


def verify_sidecar(root: Path, script_name: str = "deepseek_sidecar.sh", alias: str | None = None) -> str:
    """Probe one named model's availability; do not infer every row from a launcher check.

    Delegates to `<launcher>.sh --check` rather than reimplementing the
    preconditions, so availability has ONE definition and the hook cannot drift
    green while real dispatch fails. Invoking through bash is deliberate: bash is
    itself a precondition, so a missing shell is reported, not masked.

    Exists so budget-pressure routing (orchestration §5b) can pick a
    provider from a fact already in context, never from a mid-session investigation.
    """
    script = root / ".claude" / "scripts" / script_name
    if not script.exists():
        return f"UNAVAILABLE ({script_name} not present)"
    bash = resolve_bash()
    if not bash:
        return "UNAVAILABLE (no Git Bash found; sidecar needs a POSIX shell)"
    try:
        # Absolute POSIX form: bash strips Windows backslashes, which would make
        # the probe report UNAVAILABLE on every Windows session — a false negative
        # that silently routes all delegation back to Anthropic quota.
        command = [bash, script.resolve().as_posix(), "--check"]
        if alias:
            command.extend(("-m", alias))
        proc = run_bounded(command, CHECK_SECONDS, cwd=str(root))
    except FileNotFoundError:
        return "UNAVAILABLE (bash not on PATH)"
    except OSError as exc:
        return f"UNKNOWN (--check failed to launch: {exc})"
    if proc.returncode is None:
        return "UNKNOWN (--check timed out)"
    emitted = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if not emitted:
        return f"UNKNOWN (--check exit {proc.returncode}, no output)"
    lines = [line.strip() for line in emitted.splitlines() if line.strip()]
    if proc.returncode == 0:
        result = lines[-1]
    else:
        # A refusal's last line is its override hint; the reason is the REFUSING line above it.
        reason = next((line for line in lines if "REFUSING" in line), None)
        result = lines[-1] if reason is None or reason == lines[-1] else f"{reason} {lines[-1]}"
    return f"{alias}: {result}" if alias else result


def self_improvement_advisory(root: Path, source: str | None) -> str:
    """`tools/self_improvement_due.py --line`'s output on `source == "startup"` only — empty on
    resume, compact, clear or a missing source, or when nothing is due. The 15 s subprocess
    timeout outwaits the archive store's 10 s ledger-lock wait, so a held lock reports its reason."""
    if source != "startup":
        return ""
    script = root / ".claude" / "tools" / "self_improvement_due.py"
    if not script.exists():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(root / ".claude"), "--line"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, cwd=str(root)
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Stray semantic-search index sweep
# ---------------------------------------------------------------------------

STRAY_SEARCH_INDEX_NAME = ".search-index"
_SWEEP_GIT_STATUS_TIMEOUT = 5.0
_SWEEP_LIVE_SIDECAR_SEC = 24 * 3600
_SWEEP_PATHSPEC = ":(glob)**/" + STRAY_SEARCH_INDEX_NAME + "/**"


def _is_git_toplevel(path: Path) -> bool:
    """A directory carrying its own `.git` (file or dir) is a repo/submodule/worktree root."""
    try:
        return (path / ".git").exists()
    except OSError:
        return False


def find_stray_search_indexes(root: Path) -> list[Path]:
    """Git-aware discovery: ignored `.search-index/` entries whose parent is NOT a git
    toplevel. The repo root, a submodule root and a worktree root are kept; only an index
    built inside an ordinary subdirectory (a subdirectory `searchDir` from before
    `semantic_search_scope_guard.py`) counts as a stray. Never walks the tree itself.

    `--untracked-files=all` is required: without it git collapses a fully-ignored directory
    to its nearest untracked ancestor ("!! sub/" instead of "!! sub/.search-index/search.db"),
    which would hide the `.search-index` segment entirely. The glob pathspec limits the listing to
    `.search-index` paths, so its size follows the strays rather than every ignored file."""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--ignored", "--untracked-files=all", "--porcelain", "--", _SWEEP_PATHSPEC],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_SWEEP_GIT_STATUS_TIMEOUT, cwd=str(root)
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []

    found = set()
    for line in result.stdout.splitlines():
        if not line.startswith("!! "):
            continue
        rel = line[3:].strip().strip('"')
        parts = Path(rel).parts
        if STRAY_SEARCH_INDEX_NAME not in parts:
            continue
        idx = parts.index(STRAY_SEARCH_INDEX_NAME)
        found.add(Path(*parts[: idx + 1]))

    strays = []
    for rel_dir in found:
        candidate = root / rel_dir
        parent = candidate.parent
        if parent.resolve() == root.resolve():
            continue  # the repo root's own index is never a stray
        if _is_git_toplevel(parent):
            continue
        strays.append(candidate)
    return strays


def _index_recently_open(index_dir: Path, now: float | None = None) -> bool:
    """SQLite keeps `-wal`/`-shm` beside a database while a connection is open. One modified
    within a day means a semantic-search server may still use this index; on POSIX an rmtree
    would unlink it from under that server, so the sweep leaves it for a later session."""
    now = time.time() if now is None else now
    for name in ("search.db-wal", "search.db-shm"):
        try:
            if now - (index_dir / name).stat().st_mtime < _SWEEP_LIVE_SIDECAR_SEC:
                return True
        except OSError:
            continue
    return False


def sweep_stray_search_indexes(root: Path) -> None:
    """Best-effort removal of stray `.search-index/` directories left by out-of-scope
    semantic-search `searchDir` calls (`semantic_search_scope_guard.py` denies new ones;
    this clears the residue from before that guard shipped). Silent, fail-open: any
    exception here must never change SessionStart's own output. A locked SQLite file
    (the semantic-search MCP server holds it open) is an expected outcome, not an error —
    it is swept on a later session once the server has released it."""
    try:
        strays = find_stray_search_indexes(root)
    except Exception:
        return

    for candidate in strays:
        try:
            if candidate.is_symlink():
                continue
            if hasattr(os.path, "isjunction") and os.path.isjunction(str(candidate)):
                continue
            if not candidate.is_dir():
                continue
            if _index_recently_open(candidate):
                continue
            shutil.rmtree(candidate, onerror=lambda _func, _path, _exc_info: None)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Git context (existing functionality)
# ---------------------------------------------------------------------------

def get_git_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def get_uncommitted_count() -> int | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        if result.returncode != 0:
            return None
        lines = [l for l in result.stdout.strip().split("\n") if l]
        return len(lines)
    except Exception:
        return None


def get_upstream_drift() -> tuple[int, int, float] | None:
    """(ahead, behind, hours since the oldest unpushed commit) against the branch's upstream as last fetched, or None
    when there is no upstream or git fails."""
    try:
        counts = subprocess.run(
            ["git", "--no-optional-locks", "rev-list", "--left-right", "--count", "HEAD...@{u}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        if counts.returncode != 0:
            return None
        ahead, behind = (int(x) for x in counts.stdout.split())
        oldest = 0.0
        if ahead:
            times = subprocess.run(["git", "log", "--format=%ct", "@{u}..HEAD"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=5).stdout.split()
            oldest = (time.time() - min(int(t) for t in times)) / 3600 if times else 0.0
        return ahead, behind, oldest
    except Exception:
        return None


def get_recent_commits(count: int = 3, cwd: str | None = None) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", f"-{count}", "--format=%h %s"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, cwd=cwd
        )
        return [line.strip()[:70] for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _load_workstation_profile(root: Path):
    """`tools/workstation_profile.py` from this checkout, loaded by path.

    By path rather than by import: a hook and a tool share no sys.path, and a proof plants a copy
    under a fixture root so the whole read is exercised through main().
    """
    import importlib.util
    for path in (root / ".claude" / "tools" / "workstation_profile.py",
                 Path(__file__).resolve().parent.parent / "tools" / "workstation_profile.py"):
        if path.exists():
            spec = importlib.util.spec_from_file_location("workstation_profile", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise OSError("workstation_profile.py not found")


def workstation_status(root: Path) -> str | None:
    """This checkout's setup stamp — OK, UNAUDITED or STALE — or None to omit the step.

    The tool owns the comparison and the wording. Any failure omits the step: an unreadable profile
    must cost the session nothing, which is also why this is local-only (a cloud clone has no
    `/workstation_setup` run of its own).
    """
    try:
        return _load_workstation_profile(root).setup_status(root)
    except Exception:
        return None


def cloud_session_blocks(root: Path) -> list[str]:
    """The cloud capability contract (a step module's `cloud_lines`) and the memory index, which a cloud
    VM cannot load otherwise.

    The in-repo memory index reaches the model only through autoMemoryDirectory in
    settings.local.json, which a cloud clone does not carry.
    """
    lines = [line for block in _step_results("cloud_lines", root) for line in block]
    index = root / ".claude" / "auto-memory" / "MEMORY.md"
    try:
        body = index.read_text(encoding="utf-8", errors="replace").strip()
        lines += ["", "<auto-memory-index dir=\".claude/auto-memory/\">", body, "</auto-memory-index>"]
    except OSError:
        pass
    return lines


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        input_data = {}

    root = get_project_root()
    worktree = is_worktree()
    cloud = is_cloud()

    # Best-effort, silent — never touches output_lines; see function docstring.
    try:
        sweep_stray_search_indexes(root)
    except Exception:
        pass

    # --- Cloud auto-install (before any other setup) ---
    setup_results = {}
    for status in _step_results("early_setup", root, cloud):
        setup_results.update(status)

    # --- Worktree/cloud setup (runs for ALL sessions, idempotent) ---
    setup_results["submodule"] = setup_submodule(root)
    submodule_ready = setup_results["submodule"] == "OK" or setup_results["submodule"].startswith("FIXED (")
    for status in _step_results("setup", root, str(input_data.get("source") or "startup"), worktree, cloud,
                                submodule_ready):
        setup_results.update(status)

    # --- Setup stamp (local only) ---
    if not cloud:
        station_status = workstation_status(root)
        if station_status:
            setup_results["workstation"] = station_status

    # --- Language-server health checks (local only) ---
    if not cloud:
        for status in _step_results("local_checks", root):
            setup_results.update(status)

    if not cloud:  # sidecars and benchmark rosters live on the workstation only
        setup_results.update(sidecar_health(root))
        rosters = roster_health(root)
        if rosters:
            setup_results["rosters"] = rosters

    # --- Git context ---
    branch = get_git_branch()
    uncommitted = get_uncommitted_count()
    uncommitted_str = ("working tree UNKNOWN" if uncommitted is None else
                       f"{uncommitted} uncommitted" if uncommitted > 0 else "clean")
    drift = get_upstream_drift()
    if drift and (drift[0] or drift[1]):
        uncommitted_str += f" | {drift[0]} ahead, {drift[1]} behind upstream as last fetched"
        if drift[0]:
            uncommitted_str += f" (oldest unpushed {drift[2]:.0f}h)"

    main_commits = get_recent_commits(1)

    _step_results("persist_env", root, cloud)

    # --- Build output ---
    output_lines = ["<session-context>"]

    # A sidecar child executes a brief; the owner's worktree, env and git lines are noise to it.
    owner_view = not _sidecar_delegate()

    # Worktree/cloud report
    if owner_view and worktree:
        output_lines.append(f"Worktree: YES (root: {root})")
    elif owner_view:
        output_lines.append("Worktree: no (main repo)")

    if cloud:
        output_lines.append("Environment: CLOUD (CLAUDE_CODE_REMOTE=true)")

    env_parts = []
    any_fixed = False
    for step, status in (setup_results.items() if owner_view else ()):
        # A healthy sidecar's probe detail (endpoint, key, credential path) informs no routing choice;
        # its model and any fact after the detail (a pricing window) do.
        healthy_sidecar = re.match(r"(\S+): OK \([^)]*\)\s*(.*)$", status) if step.startswith("sidecar-") else None
        if status.startswith("OK"):
            env_parts.append(f"{step} OK")
        elif healthy_sidecar:
            env_parts.append(" ".join(filter(None, (f"{step}: {healthy_sidecar[1]} OK", healthy_sidecar[2]))))
        else:
            env_parts.append(f"{step}: {status}")
            if "FIXED" in status:
                any_fixed = True
    if owner_view:
        output_lines.append("")
        output_lines.append("Env: " + " | ".join(env_parts))
    if not cloud:
        ladder_line = ladder_banner(root)
        if ladder_line:
            output_lines.append(ladder_line)
        catalog_line = free_catalog_line(root, str(input_data.get("source") or "startup"))
        if catalog_line:
            output_lines.append(catalog_line)
    listing_line = skill_listing_banner(root, input_data)
    if listing_line:
        output_lines.append(listing_line)

    for lines in _step_results("env_lines", root, cloud):
        output_lines.extend(lines)

    if any_fixed:
        output_lines.append("  Auto-fixed the items marked FIXED; see remaining statuses above.")

    # Git context
    if owner_view:
        output_lines.append("")
        output_lines.append(f"Git: {branch} | {uncommitted_str}")
        output_lines.append("")
        output_lines.append("Recent {{PROJECT_NAME}} commits:")
        if main_commits:
            for commit in main_commits:
                output_lines.append(f"  {commit}")
        else:
            output_lines.append("  (none)")

    for lines in _step_results("context_lines", root, get_recent_commits):
        output_lines.extend(lines)

    output_lines.append("</session-context>")

    # Worklog titles mirror — NOT injected. The mirror grows unbounded with the backlog and is
    # paid by EVERY session, while the question it answers ("does an open item overlap what I am
    # about to do?") is asked better once, at scope-definition time, by a delegate that reads the
    # file itself: .claude/workflows/worklog_relevance.js (Anthropic) or
    # .claude/scripts/worklog_relevance_sidecar.sh (DeepSeek).
    # Flip to True to restore verbatim injection. A size-tiered domain+count digest was built and
    # dropped the same day — it was unread middle ground, so it is not kept here as dead code.
    WORKLOG_INLINE = False
    worklog_titles_path = root / ".claude" / "worklog-titles.md"
    if WORKLOG_INLINE and worklog_titles_path.exists():
        try:
            worklog_content = worklog_titles_path.read_text(encoding="utf-8").strip()
            if worklog_content:
                output_lines.append("")
                output_lines.append("<worklog-titles>")
                output_lines.append(worklog_content)
                output_lines.append("</worklog-titles>")
        except Exception:
            pass

    for lines in _step_results("nudge_lines", setup_results, cloud):
        output_lines.extend(lines)

    if cloud:
        output_lines.extend(cloud_session_blocks(root))

    # Cloud-worklog replay surface (local only). When a cloud session has queued
    # worklog mutations to .claude/worklog-pending.md (Unit D), flag the live count
    # so the user knows to run /worklog and replay them into Obsidian. Struck-through
    # (already-skipped) entries are excluded from the count.
    if not cloud:
        pending_path = root / ".claude" / "worklog-pending.md"
        if pending_path.exists():
            try:
                raw = pending_path.read_text(encoding="utf-8")
                # Count only the queue body, AFTER the DO-NOT-HAND-EDIT comment
                # close — the header documents the entry format with example
                # "- ADD ..." lines that must not be miscounted as pending.
                body = raw.split("-->", 1)[1] if "-->" in raw else raw
                pending_lines = [
                    l for l in body.splitlines()
                    if l.strip().startswith("- ") and not l.strip().startswith("- ~~")
                ]
                if pending_lines:
                    output_lines.append("")
                    output_lines.append(
                        f"Cloud worklog: {len(pending_lines)} pending — run /worklog to replay into Obsidian."
                    )
            except Exception:
                pass

    for lines in _step_results("report_lines", root):
        output_lines.extend(lines)

    # Self-improvement loop due-check — startup only, silent when nothing is due.
    due_line = self_improvement_advisory(root, input_data.get("source"))
    if due_line:
        output_lines.append("")
        output_lines.append(due_line)

    # Compaction drops the gotchas a session had loaded. Picking up another session's work is the
    # /session_digest command's, whose listing reaches every session.
    if input_data.get("source") == "compact":
        output_lines.append("")
        output_lines.append("<context-reload-reminder>")
        output_lines.append("Resumed from compaction: search auto-memory (%s) for task-relevant gotchas."
                            % ("Grep over .claude/auto-memory/" if cloud else "semantic-search"))
        output_lines.append("</context-reload-reminder>")

    if _STEP_FAULTS:
        output_lines.extend(["", "Session step modules: FAILED (" + "; ".join(_STEP_FAULTS) + ")"])

    print("\n".join(output_lines))
    sys.exit(0)


def runsettings_cli(argv: list[str]) -> int:
    """`--setup-runsettings <checkout>`: the godot step module's status; exit 1 when it generates nothing."""
    for hook in _step_hooks("runsettings_cli"):
        return hook(argv)
    print("SKIPPED (the godot layer is not installed; nothing generates .runsettings)")
    return 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--setup-runsettings":
        sys.exit(runsettings_cli(sys.argv[2:]))
    main()
