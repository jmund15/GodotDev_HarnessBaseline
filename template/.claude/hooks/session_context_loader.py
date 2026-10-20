#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: SessionStart - Worktree setup + development context loader

On every session start:
1. Detects if running in a git worktree or cloud environment
2. On cloud: runs cloud-setup.sh if the toolchain is missing, and fills the Godot docs cache
3. Initializes empty submodules; reports and preserves existing divergent checkouts
4. Generates .runsettings from template if missing (worktree/cloud)
5. Regenerates .godot import cache if missing (headless)
6. Runs dotnet build to verify compilation health (startup/clear only; resume/compact report the stored verify)
7. Injects git context (branch, commits, submodule status)
8. On cloud: persists env vars via CLAUDE_ENV_FILE and injects the cloud contract + memory index
9. On a local machine: reports this checkout's setup stamp (workstation OK / STALE / UNAUDITED)
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
from _bounded_run import run_bounded

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

# `git submodule status` line: optional drift prefix, sha, path.
# Empty prefix group == in sync ('-' uninit, '+' checkout != pointer, 'U' conflict).
_SUBMODULE_RE = re.compile(r"^([-+U]?)([0-9a-f]{40})\s+(\S+)")


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def is_cloud() -> bool:
    """Return True if running in a Claude Code cloud environment."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


def persist_cloud_env(godot_bin: str):
    """Write env vars to CLAUDE_ENV_FILE so subsequent Bash tool calls inherit them."""
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    lines = []
    if godot_bin:
        lines.append(f"export GODOT_BIN={godot_bin}")
    dotnet_root = os.environ.get("DOTNET_ROOT", "") or adopt_cloud_dotnet()
    if dotnet_root:
        lines.append(f"export DOTNET_ROOT={dotnet_root}")
        lines.append(f"export PATH={dotnet_root}:$PATH")
    if lines:
        with open(env_file, "a") as f:
            f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# cloud-setup.sh installs under /opt as root, else under ~/.local.
CLOUD_DOTNET_ROOTS = (Path("/opt/dotnet"), Path.home() / ".local/dotnet", Path.home() / ".dotnet")


def adopt_cloud_dotnet() -> str:
    """Point DOTNET_ROOT and the front of PATH at the first installed CLOUD_DOTNET_ROOTS entry."""
    for d in CLOUD_DOTNET_ROOTS:
        if (d / "dotnet").exists():
            os.environ["DOTNET_ROOT"] = str(d)
            if str(d) not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return str(d)
    return ""

# Update on every Godot engine upgrade — both platform fallback paths use this.
GODOT_VERSION = "4.7.1-stable"


def get_godot_bin() -> str:
    """Return the Godot binary path from GODOT_BIN or a known install."""
    env = os.environ.get("GODOT_BIN", "")
    if env and Path(env).exists():
        return env
    # Platform-specific fallback locations
    if platform.system() == "Linux":
        leaf = f"Godot_v{GODOT_VERSION}_mono_linux_x86_64/Godot_v{GODOT_VERSION}_mono_linux.x86_64"
        fallback = next((p for p in (Path("/opt/godot") / leaf, Path.home() / ".local/godot" / leaf) if p.exists()),
                        Path.home() / ".local/godot" / leaf)
    else:
        fallback = Path.home() / f"Game_Dev/Godot_Installs/Godot_v{GODOT_VERSION}_mono_win64/Godot_v{GODOT_VERSION}_mono_win64.exe"
    if fallback.exists():
        return str(fallback)
    return ""


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
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
        )
        if toplevel.returncode == 0 and common.returncode == 0:
            tl = Path(toplevel.stdout.strip()).resolve()
            cd = Path(common.stdout.strip()).resolve()
            # In a worktree, the common dir points OUTSIDE the toplevel
            return not str(cd).startswith(str(tl))
    except Exception:
        pass
    return False


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


def setup_runsettings(root: Path) -> str:
    """Generate .runsettings from template if missing (worktree-only).

    The .runsettings file is gitignored (machine-specific GODOT_BIN path).
    Worktrees get a fresh checkout without it. This generates it from the
    tracked .runsettings.template, substituting the resolved GODOT_BIN path.
    """
    runsettings_path = root / ".runsettings"
    if runsettings_path.exists():
        return "OK"

    template_path = root / ".runsettings.template"
    if not template_path.exists():
        return "SKIPPED (.runsettings.template not found)"

    godot_bin = get_godot_bin()
    if not godot_bin:
        return "SKIPPED (GODOT_BIN not resolved)"

    try:
        template_content = template_path.read_text(encoding="utf-8")
        resolved = template_content.replace("{{GODOT_BIN}}", godot_bin)
        runsettings_path.write_text(resolved, encoding="utf-8")
        return "FIXED (generated from template)"
    except Exception as e:
        return f"FAILED: {e}"


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


def verify_lsp_plugin() -> str:
    """Verify the C# LSP plugin is correctly configured and not corrupted.

    Local-only check — caller must gate on not is_cloud().
    Checks: plugin.json content, adapter file, binary, registration,
    orphaned marker, and ENABLE_LSP_TOOL env var.
    """
    home = Path.home()
    # Version-agnostic: resolve the newest installed plugin version rather than
    # hardcoding one (a hardcoded "1.0.0" silently reports BROKEN after a bump).
    plugin_cache_root = home / ".claude/plugins/cache/claude-plugins-official/csharp-lsp"
    version_candidates = sorted(plugin_cache_root.glob("*/.claude-plugin/plugin.json"))
    plugin_json_path = (
        version_candidates[-1] if version_candidates
        else plugin_cache_root / "0.0.0/.claude-plugin/plugin.json"
    )
    adapter_path = home / ".dotnet/tools/csharp-ls-adapter.js"
    binary_path = home / ".dotnet/tools/csharp-ls-original.exe"
    installed_path = home / ".claude/plugins/installed_plugins.json"
    orphaned_path = plugin_json_path.parent.parent / ".orphaned_at"

    issues = []

    if not binary_path.exists():
        issues.append("csharp-ls-original.exe missing from ~/.dotnet/tools/")

    if not adapter_path.exists():
        issues.append("csharp-ls-adapter.js missing from ~/.dotnet/tools/")

    if not plugin_json_path.exists():
        issues.append("plugin.json missing from plugin cache")
    else:
        try:
            with open(plugin_json_path, "r", encoding="utf-8") as f:
                pj = json.load(f)
            lsp_cmd = pj.get("lspServers", {}).get("csharp-ls", {}).get("command", "")
            if lsp_cmd != "node":
                issues.append(f"plugin.json command='{lsp_cmd}' (expected 'node') — marketplace overwrote it?")
        except Exception as e:
            issues.append(f"plugin.json unreadable: {e}")

    if orphaned_path.exists():
        issues.append(".orphaned_at marker present (plugin uninstalled?)")

    if installed_path.exists():
        try:
            with open(installed_path, "r", encoding="utf-8") as f:
                ip = json.load(f)
            if "csharp-lsp@claude-plugins-official" not in ip.get("plugins", {}):
                issues.append("not registered in installed_plugins.json")
        except Exception:
            issues.append("installed_plugins.json unreadable")

    if os.environ.get("ENABLE_LSP_TOOL") != "1":
        issues.append("ENABLE_LSP_TOOL env var not set to '1'")

    if not issues:
        return "OK"
    return "BROKEN: " + "; ".join(issues)


def godot_docs_cache_issue(root: Path) -> str | None:
    """One line when the version-pinned Godot class-reference cache is unusable,
    None when it is healthy — the cache is the FIRST tier of the web order
    (docs.godotengine.org is Cloudflare-gated), so a silently stale one sends
    every doc lookup down a paid or blocked path.

    A stamp alone is not health: the tree it names must actually be populated.
    Pin SSOT is project_stack.md; never hardcode it here. Fails open — this is
    advisory context, and no cache state is worth breaking session start.
    """
    try:
        stack = (root / ".claude" / "reference" / "project_stack.md").read_text(encoding="utf-8")
        match = re.search(r"Godot engine:\D*(\d+\.\d+(?:\.\d+)?)", stack)
        if not match:
            return None
        pin = match.group(1)

        cache = root / ".claude" / "cache" / "godot-docs"
        stamp_path = cache / ".stamp"
        stamp = stamp_path.read_text(encoding="utf-8").strip() if stamp_path.exists() else ""
        classes = cache / "doc" / "classes"
        count = len(list(classes.glob("*.xml"))) if classes.is_dir() else 0

        if stamp == pin and count > 0:
            return None
        if not stamp:
            state = "absent"
        elif count == 0:
            state = f"stamped {stamp} but 0 class files"
        else:
            state = f"stamped {stamp} ({count} classes)"
        return (
            f"Godot docs cache: {state}, pin is {pin} — "
            "rebuild with .claude/scripts/godot_docs_cache.sh"
        )
    except Exception:
        return None


def setup_import_cache(root: Path) -> str:
    """Report cache presence; regenerate only when absent, without hiding failed imports."""
    imported_dir = root / ".godot" / "imported"
    try:
        if imported_dir.exists() and any(imported_dir.iterdir()):
            return "PRESENT (not revalidated)"
        godot_bin = get_godot_bin()
        if not godot_bin:
            return "SKIPPED (godot binary not found)"
        from runpy import run_path
        resolve = run_path(str(Path(__file__).resolve().parents[1] / 'scripts/engine_userdata.py'))['isolation']
        values = resolve(root)
        result = subprocess.run(
            [godot_bin, "--headless", "--path", str(root), "--import", "--quit"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(root),
            env=dict(os.environ, **values), stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            return f"FAILED: import exit {result.returncode} (partial resources are not success; USERDIR={values['USERDIR']})"
        if imported_dir.exists() and any(imported_dir.iterdir()):
            return f"FIXED (regenerated; USERDIR={values['USERDIR']})"
        return f"FAILED: import returned success but produced no imported resources (USERDIR={values['USERDIR']})"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout (>120s)"
    except Exception as error:
        return f"UNKNOWN: import cache check failed ({error})"


# Re-entry reports prior verification without spawning another build. A recent,
# clean, same-HEAD cache may also avoid a startup build, but never claims current health.
BUILD_VERIFY_TTL_SECONDS = 30 * 60


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5, cwd=str(root)
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _build_cache_path(root: Path) -> Path:
    return root / ".claude" / ".cache" / "build_verify.json"


def cached_build_result(root: Path) -> str | None:
    """Return the cached OK result if fresh and HEAD-matched, else None."""
    try:
        import time
        cache = json.loads(_build_cache_path(root).read_text(encoding="utf-8"))
        if not cache.get("result", "").startswith("OK"):
            return None
        age = time.time() - float(cache.get("ts", 0))
        if not 0 <= age <= BUILD_VERIFY_TTL_SECONDS:
            return None
        head = _git_head(root)
        if not head or cache.get("head") != head:
            return None
        if cache.get("clean") is not True or _submodule_is_dirty(root, ".") is not False:
            return None
        return cache["result"]
    except Exception:
        return None


def store_build_result(root: Path, result: str) -> None:
    try:
        import time
        from _hook_state import write_json_atomic
        path = _build_cache_path(root)
        write_json_atomic(str(path), {
            "ts": time.time(), "head": _git_head(root), "result": result,
            "clean": _submodule_is_dirty(root, ".") is False,
        })
    except Exception:
        pass


# Re-entry does not establish unchanged build inputs; retained results are labelled PREVIOUS.
NO_BUILD_SOURCES = ("resume", "compact")


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


def stored_build_result(root: Path, source: str) -> str:
    """The last stored verify, tagged with its age and whether HEAD moved since; never builds."""
    try:
        import time
        cache = json.loads(_build_cache_path(root).read_text(encoding="utf-8"))
        result = str(cache.get("result") or "")
        if result:
            age_min = int((time.time() - float(cache.get("ts", 0))) / 60)
            moved = "" if cache.get("head") == _git_head(root) else ", HEAD moved since"
            return f"PREVIOUS: {result} [last verify {age_min}m ago{moved}; {source} skips the build]"
    except Exception:
        pass
    return f"SKIPPED ({source}: no stored verify)"


def build_status(root: Path, source: str, submodule_ready: bool, verify=None) -> str:
    """Build verdict for this SessionStart: no build when the submodule is not ready or the
    source is a re-entry; a fresh same-HEAD OK verify is served from cache
    (BUILD_VERIFY_TTL_SECONDS); otherwise dotnet runs and the result is stored."""
    if not submodule_ready:
        return "SKIPPED (submodule not ready)"
    if source in NO_BUILD_SOURCES:
        return stored_build_result(root, source)
    cached = cached_build_result(root)
    if cached:
        return f"PREVIOUS: {cached} [cached <{BUILD_VERIFY_TTL_SECONDS // 60}m; not a current build]"
    result = (verify or verify_build)(root)
    store_build_result(root, result)
    return result


def verify_build(root: Path) -> str:
    """Run dotnet build and report success/failure."""
    try:
        result = subprocess.run(
            ["dotnet", "build", "--nologo", "-v", "q"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(root)
        )
        # Parse error/warning counts from last lines
        output = result.stdout + result.stderr
        errors = 0
        warnings = 0
        for line in output.splitlines():
            line_stripped = line.strip()
            if "Error(s)" in line_stripped:
                try:
                    errors = int(line_stripped.split()[0])
                except (ValueError, IndexError):
                    pass
            if "Warning(s)" in line_stripped:
                try:
                    warnings = int(line_stripped.split()[0])
                except (ValueError, IndexError):
                    pass

        if result.returncode == 0 and errors == 0:
            return f"OK ({warnings} warnings)" if warnings else "OK"
        return f"FAILED ({errors} errors, {warnings} warnings)"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout (>120s)"
    except Exception as e:
        return f"FAILED: {e}"


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


def get_jmodot_commits(root: Path, count: int = 3) -> list[str]:
    """Get recent commits from Jmodot submodule using the resolved project root."""
    jmodot_path = root / "Jmodot"
    if not jmodot_path.exists() or not any(jmodot_path.iterdir()):
        return []
    return get_recent_commits(count, str(jmodot_path))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# cloud-setup.sh measured 29-32 s; the ceiling leaves the rest of the 600 s SessionStart budget
# to submodule init, import and build.
CLOUD_SETUP_TIMEOUT_S = 300
DOCS_CACHE_TIMEOUT_S = 60


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


def cloud_setup_current(root: Path) -> bool:
    """True unless the script that built this snapshot is known to differ from the repo's copy.

    True when nothing can tell: re-running cloud-setup.sh on an unknown costs ~30 s of every
    session start, which is the cost the environment snapshot exists to avoid.
    """
    try:
        return _load_workstation_profile(root).cloud_setup_current(root)
    except Exception:
        return True


def _install_cloud_toolchain(root: Path) -> str:
    """The toolchain status: OK, FIXED after running cloud-setup.sh, or FAILED."""
    adopt_cloud_dotnet()
    ready = _cmd_exists("dotnet") and bool(get_godot_bin()) and _cmd_exists("xvfb-run")
    stale = not cloud_setup_current(root)
    status = "OK (toolchain present)"
    if not ready or stale:
        script = root / ".claude" / "cloud-setup.sh"
        if not script.exists():
            return "FAILED (cloud-setup.sh not found)"
        started = time.time()
        try:
            result = subprocess.run(["bash", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=CLOUD_SETUP_TIMEOUT_S, cwd=str(root))
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"FAILED: cloud-setup.sh did not finish ({type(exc).__name__})"
        adopt_cloud_dotnet()
        lines = (result.stdout or "").strip().splitlines()
        failed = [line for line in lines if "FAILED" in line]
        took = int(time.time() - started)
        if failed or not get_godot_bin():
            return "FAILED (%s)" % ("; ".join(failed) if failed else (lines or ["no output"])[-1])
        if ready:
            status = (f"FIXED (snapshot script is stale; ran the repo copy in {took}s; "
                      "paste .claude/cloud-setup.sh into the environment Setup script)")
        else:
            status = (f"FIXED (cloud-setup.sh took {took}s; paste .claude/cloud-setup.sh into the "
                      "environment's Setup script so the snapshot carries it)")
    return status


def run_cloud_setup(root: Path) -> str:
    """Install the cloud toolchain when the environment's setup script did not, then the docs cache.

    The environment setup script (cloud-setup.sh pasted into claude.ai) is snapshotted; this
    fallback reinstalls on every new session, so its cost is reported rather than hidden. A present
    toolchain whose snapshot script no longer matches the repo's heals the same way, because a
    snapshot from an older paste never gets the script's newer steps. The docs cache builds after a
    failed install too, since it needs only GitHub. A docs cache failure surfaces through
    godot_docs_cache_issue(), not this status.
    """
    if not is_cloud():
        return ""
    status = _install_cloud_toolchain(root)
    docs = root / ".claude" / "scripts" / "godot_docs_cache.sh"
    if docs.exists():
        try:
            subprocess.run(["bash", str(docs)], capture_output=True, timeout=DOCS_CACHE_TIMEOUT_S, cwd=str(root))
        except (subprocess.TimeoutExpired, OSError):
            pass
    return status


def cloud_session_blocks(root: Path) -> list[str]:
    """The cloud capability contract and memory index, which a cloud VM cannot load otherwise.

    rules/cloud_dev.md is path-scoped, and the in-repo memory index reaches the model only through
    autoMemoryDirectory in settings.local.json, which a cloud clone does not carry.
    """
    lines = []
    contract = root / ".claude" / "rules" / "cloud_dev.md"
    try:
        text = contract.read_text(encoding="utf-8", errors="replace")
        parts = text.split("---", 2) if text.startswith("---") else []
        if len(parts) == 3:
            text = parts[2]
        lines += ["", "<cloud-session>", text.strip(), "</cloud-session>"]
    except OSError:
        lines += ["", "<cloud-session>Cloud contract missing: .claude/rules/cloud_dev.md</cloud-session>"]
    index = root / ".claude" / "auto-memory" / "MEMORY.md"
    try:
        body = index.read_text(encoding="utf-8", errors="replace").strip()
        lines += ["", "<auto-memory-index dir=\".claude/auto-memory/\">", body, "</auto-memory-index>"]
    except OSError:
        pass
    return lines


def _cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH."""
    try:
        result = subprocess.run(
            ["which", cmd] if platform.system() != "Windows" else ["where", cmd],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


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
    if cloud:
        setup_results["cloud_setup"] = run_cloud_setup(root)

    # --- Worktree/cloud setup (runs for ALL sessions, idempotent) ---
    setup_results["submodule"] = setup_submodule(root)
    if worktree or cloud:
        setup_results["runsettings"] = setup_runsettings(root)
    submodule_ready = setup_results["submodule"] == "OK" or setup_results["submodule"].startswith("FIXED (")
    setup_results["import_cache"] = setup_import_cache(root) if submodule_ready else "SKIPPED (submodule not ready)"

    setup_results["build"] = build_status(
        root, str(input_data.get("source") or "startup"), submodule_ready)

    # --- Setup stamp (local only) ---
    if not cloud:
        station_status = workstation_status(root)
        if station_status:
            setup_results["workstation"] = station_status

    # --- LSP plugin health check (local only) ---
    if not cloud:
        setup_results["lsp_plugin"] = verify_lsp_plugin()

    if not cloud:  # sidecars and benchmark rosters live on the workstation only
        setup_results.update(sidecar_health(root))
        setup_results["rosters"] = roster_health(root)

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
    jmodot_commits = get_jmodot_commits(root, 1)

    # --- Persist env for cloud ---
    godot_bin = get_godot_bin()
    if cloud:
        persist_cloud_env(godot_bin)

    # --- Build output ---
    output_lines = ["<session-context>"]

    # Worktree/cloud report
    if worktree:
        output_lines.append(f"Worktree: YES (root: {root})")
    else:
        output_lines.append("Worktree: no (main repo)")

    if cloud:
        output_lines.append("Environment: CLOUD (CLAUDE_CODE_REMOTE=true)")

    output_lines.append("")
    env_parts = []
    any_fixed = False
    for step, status in setup_results.items():
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

    # GODOT_BIN for test commands (Bash sessions don't inherit setx env vars)
    if godot_bin:
        output_lines.append(f"  GODOT_BIN: {godot_bin}")
    else:
        output_lines.append("  GODOT_BIN: NOT FOUND (tests requiring Godot runtime will fail)")

    if any_fixed:
        output_lines.append("  Auto-fixed the items marked FIXED; see remaining statuses above.")

    # Git context
    output_lines.append("")
    output_lines.append(f"Git: {branch} | {uncommitted_str}")
    output_lines.append("")
    output_lines.append("Recent {{PROJECT_NAME}} commits:")
    if main_commits:
        for commit in main_commits:
            output_lines.append(f"  {commit}")
    else:
        output_lines.append("  (none)")

    output_lines.append("")
    output_lines.append("Recent Jmodot commits:")
    if jmodot_commits:
        for commit in jmodot_commits:
            output_lines.append(f"  {commit}")
    else:
        output_lines.append("  (none)")

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

    # LSP early-load nudge: whenever LSP is available locally.
    # LSP tool schema is deferred by the harness; this nudge tells the model to load
    # it up front via ToolSearch so C# symbol queries don't default to Grep.
    # Gated only on plugin health — unconditional otherwise, because this is a C#
    # project and even read-only sessions benefit from semantic navigation.
    # The quirks/workflow details live in .claude/rules/csharp_lsp.md (auto-loads on
    # the first .cs read) — this nudge only preserves the EARLY ToolSearch timing.
    lsp_ok = not cloud and setup_results.get("lsp_plugin", "").startswith("OK")
    if lsp_ok:
        output_lines.append("")
        output_lines.append("<lsp-early-load>")
        output_lines.append(
            "csharp-lsp healthy. Load the LSP tool schema as one of your first actions: "
            "ToolSearch(query=\"select:LSP\", max_results=1); then default to LSP for C# "
            "symbol/caller/type questions. Anchor-then-navigate (semantic-search or "
            "Grep(\"class X\") -> LSP documentSymbol -> findReferences); schema quirks "
            "(filePath required on every op; workspaceSymbol has no query param) live in "
            "rules/csharp_lsp.md, which auto-loads on the first .cs read."
        )
        output_lines.append("</lsp-early-load>")

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

    # Godot docs cache — silent when healthy.
    docs_cache_issue = godot_docs_cache_issue(root)
    if docs_cache_issue:
        output_lines.append("")
        output_lines.append(docs_cache_issue)

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

    print("\n".join(output_lines))
    sys.exit(0)


def runsettings_cli(argv: list[str]) -> int:
    """`--setup-runsettings <checkout>`: print setup_runsettings' status; exit 0 unless no .runsettings results."""
    status = setup_runsettings(Path(argv[0]))
    print(status)
    return 0 if status == "OK" or status.startswith("FIXED") else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--setup-runsettings":
        sys.exit(runsettings_cli(sys.argv[2:]))
    main()
