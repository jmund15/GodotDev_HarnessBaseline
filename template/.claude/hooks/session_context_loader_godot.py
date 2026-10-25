#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Godot steps of the SessionStart loader (`session_context_loader.py` loads this module through
`_optional_hooks`; a consumer without the godot layer has no file and the loader skips every hook).

Every hook does nothing outside a Godot project (`project.godot` at the root), except `cloud_lines`,
which is the cloud contract any cloud checkout of this layer carries. Advisory class: a failing hook
is reported by the loader as a `step_modules` status and never blocks session start.

Hooks the loader calls, in session order:
  early_setup(root, cloud)                                 cloud toolchain install and docs cache
  setup(root, source, worktree, cloud, submodule_ready)    .runsettings, import cache, dotnet build
  persist_env(root, cloud)                                 CLAUDE_ENV_FILE exports on cloud
  env_lines(root, cloud)                                   the GODOT_BIN line
  context_lines(root, recent_commits)                      the Jmodot commits block
  cloud_lines(root)                                        the cloud capability contract
  report_lines(root)                                       the Godot docs cache issue
  runsettings_cli(argv)                                    `session_context_loader.py --setup-runsettings`
"""

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path


def godot_project(root: Path) -> bool:
    """True when the checkout is a Godot project (`project.godot` at its root); the engine, .NET,
    Jmodot and docs-cache steps run only then, and are not applicable elsewhere."""
    return (root / "project.godot").is_file()


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
    tools_dir = cloud_install().get("tools_dir", "")
    if tools_dir and (Path(tools_dir) / "pwsh").exists():
        lines.append(f"export PATH={tools_dir}:$PATH")
    if lines:
        with open(env_file, "a") as f:
            f.write("\n".join(lines) + "\n")


# cloud-setup.sh installs under /opt as root, else under ~/.local, and writes its paths to the
# manifest in the same directory. The probes below are the fallback for a snapshot without one.
CLOUD_INSTALL_NAME = ".cloud-install.json"
CLOUD_INSTALL_DIRS = (Path("/opt"), Path.home() / ".local")
CLOUD_DOTNET_ROOTS = (Path("/opt/dotnet"), Path.home() / ".local/dotnet", Path.home() / ".dotnet")


def cloud_install() -> dict:
    """The manifest cloud-setup.sh wrote, or {} on a workstation or an unreadable file."""
    for directory in CLOUD_INSTALL_DIRS:
        try:
            manifest = json.loads((directory / CLOUD_INSTALL_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return manifest if isinstance(manifest, dict) else {}
    return {}


def adopt_cloud_dotnet() -> str:
    """Point DOTNET_ROOT and the front of PATH at the manifest's dotnet root, else the first installed
    CLOUD_DOTNET_ROOTS entry."""
    recorded = cloud_install().get("dotnet_root", "")
    roots = [Path(recorded)] if recorded else []
    for d in roots + list(CLOUD_DOTNET_ROOTS):
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
    recorded = cloud_install().get("godot_bin", "")
    if recorded and Path(recorded).exists():
        return recorded
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


# Each of the import and build steps gets this long; the loader's hook registration allows 600 s in all.
STEP_TIMEOUT_S = 120


def _outran(step: str) -> str:
    """A step past its timeout has no verdict: a cold cloud import or build is slow, not broken."""
    return f"UNKNOWN: {step} did not finish in {STEP_TIMEOUT_S} s; run it in the background and read its result"


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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_S, cwd=str(root),
            env=dict(os.environ, **values), stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            return f"FAILED: import exit {result.returncode} (partial resources are not success; USERDIR={values['USERDIR']})"
        if imported_dir.exists() and any(imported_dir.iterdir()):
            return f"FIXED (regenerated; USERDIR={values['USERDIR']})"
        return f"FAILED: import returned success but produced no imported resources (USERDIR={values['USERDIR']})"
    except subprocess.TimeoutExpired:
        return _outran("import")
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


def _tree_dirty(root: Path) -> bool | None:
    """Whether the checkout has uncommitted changes, or None when it cannot be inspected."""
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        return None


def _build_cache_path(root: Path) -> Path:
    return root / ".claude" / ".cache" / "build_verify.json"


def cached_build_result(root: Path) -> str | None:
    """Return the cached OK result if fresh and HEAD-matched, else None."""
    try:
        cache = json.loads(_build_cache_path(root).read_text(encoding="utf-8"))
        if not cache.get("result", "").startswith("OK"):
            return None
        age = time.time() - float(cache.get("ts", 0))
        if not 0 <= age <= BUILD_VERIFY_TTL_SECONDS:
            return None
        head = _git_head(root)
        if not head or cache.get("head") != head:
            return None
        if cache.get("clean") is not True or _tree_dirty(root) is not False:
            return None
        return cache["result"]
    except Exception:
        return None


def store_build_result(root: Path, result: str) -> None:
    try:
        from _hook_state import write_json_atomic
        path = _build_cache_path(root)
        write_json_atomic(str(path), {
            "ts": time.time(), "head": _git_head(root), "result": result,
            "clean": _tree_dirty(root) is False,
        })
    except Exception:
        pass


# Re-entry does not establish unchanged build inputs; retained results are labelled PREVIOUS.
NO_BUILD_SOURCES = ("resume", "compact")


def stored_build_result(root: Path, source: str) -> str:
    """The last stored verify, tagged with its age and whether HEAD moved since; never builds."""
    try:
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_S, cwd=str(root)
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
        return _outran("build")
    except Exception as e:
        return f"FAILED: {e}"


def get_jmodot_commits(root: Path, recent_commits, count: int = 3) -> list[str]:
    """Recent commits from the Jmodot submodule; `recent_commits(count, cwd)` is the loader's git-log reader."""
    jmodot_path = root / "Jmodot"
    if not jmodot_path.exists() or not any(jmodot_path.iterdir()):
        return []
    return recent_commits(count, str(jmodot_path))


# cloud-setup.sh measured 29-32 s; the ceiling leaves the rest of the 600 s SessionStart budget
# to submodule init, import and build.
CLOUD_SETUP_TIMEOUT_S = 300
DOCS_CACHE_TIMEOUT_S = 60


def _load_cloud_profile(root: Path):
    """`tools/workstation_profile_godot.py` from this checkout, loaded by path.

    By path rather than by import: a hook and a tool share no sys.path, and a proof plants a copy
    under a fixture root so the whole read is exercised through the hook.
    """
    import importlib.util
    for path in (root / ".claude" / "tools" / "workstation_profile_godot.py",
                 Path(__file__).resolve().parent.parent / "tools" / "workstation_profile_godot.py"):
        if path.exists():
            spec = importlib.util.spec_from_file_location("workstation_profile_godot", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise OSError("workstation_profile_godot.py not found")


def cloud_setup_current(root: Path) -> bool:
    """True unless the script that built this snapshot is known to differ from the repo's copy.

    True when nothing can tell: re-running cloud-setup.sh on an unknown costs ~30 s of every
    session start, which is the cost the environment snapshot exists to avoid.
    """
    try:
        return _load_cloud_profile(root).cloud_setup_current(root)
    except Exception:
        return True


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
    godot_docs_cache_issue(), not this status. The caller runs it on a cloud session only.
    """
    status = _install_cloud_toolchain(root)
    docs = root / ".claude" / "scripts" / "godot_docs_cache.sh"
    if docs.exists():
        try:
            subprocess.run(["bash", str(docs)], capture_output=True, timeout=DOCS_CACHE_TIMEOUT_S, cwd=str(root))
        except (subprocess.TimeoutExpired, OSError):
            pass
    return status


def early_setup(root: Path, cloud: bool) -> dict[str, str]:
    return {"cloud_setup": run_cloud_setup(root)} if cloud and godot_project(root) else {}


def setup(root: Path, source: str, worktree: bool, cloud: bool, submodule_ready: bool) -> dict[str, str]:
    if not godot_project(root):
        return {}
    results = {}
    if worktree or cloud:
        results["runsettings"] = setup_runsettings(root)
    results["import_cache"] = (
        setup_import_cache(root) if submodule_ready else "SKIPPED (submodule not ready)")
    results["build"] = build_status(root, source, submodule_ready)
    return results


def persist_env(root: Path, cloud: bool) -> None:
    if cloud and godot_project(root):
        persist_cloud_env(get_godot_bin())


def env_lines(root: Path, cloud: bool) -> list[str]:
    """GODOT_BIN for test commands (Bash sessions don't inherit setx env vars)."""
    if not godot_project(root):
        return []
    godot_bin = get_godot_bin()
    if godot_bin:
        return [f"  GODOT_BIN: {godot_bin}"]
    return ["  GODOT_BIN: NOT FOUND (tests requiring Godot runtime will fail)"]


def context_lines(root: Path, recent_commits) -> list[str]:
    if not godot_project(root):
        return []
    commits = get_jmodot_commits(root, recent_commits, 1)
    return ["", "Recent Jmodot commits:", *([f"  {commit}" for commit in commits] or ["  (none)"])]


def cloud_lines(root: Path) -> list[str]:
    """The cloud capability contract, which a cloud VM cannot load otherwise: rules/cloud_dev.md is
    path-scoped."""
    contract = root / ".claude" / "rules" / "cloud_dev.md"
    try:
        text = contract.read_text(encoding="utf-8", errors="replace")
        parts = text.split("---", 2) if text.startswith("---") else []
        if len(parts) == 3:
            text = parts[2]
        return ["", "<cloud-session>", text.strip(), "</cloud-session>"]
    except OSError:
        return ["", "<cloud-session>Cloud contract missing: .claude/rules/cloud_dev.md</cloud-session>"]


def report_lines(root: Path) -> list[str]:
    """Godot docs cache — silent when healthy."""
    issue = godot_docs_cache_issue(root) if godot_project(root) else None
    return ["", issue] if issue else []


def runsettings_cli(argv: list[str]) -> int:
    """`--setup-runsettings <checkout>`: print setup_runsettings' status; exit 0 unless no .runsettings results."""
    status = setup_runsettings(Path(argv[0]))
    print(status)
    return 0 if status == "OK" or status.startswith("FIXED") else 1
