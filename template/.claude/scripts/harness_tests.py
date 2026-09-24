#!/usr/bin/env python3
"""Discovers every re-runnable proof under `.claude/tests/`, runs it, and on an all-green
run stamps the harness tree (whole-tree hash + per-file digests) to
`.claude/logs/harness_tests_stamp.json` via `write_json_atomic`. `git_guardrails.py` imports
`tree_entries` and `STAMP_PATH` from this module so the commit guard and this runner never
compute a digest two different ways.

    python3 .claude/scripts/harness_tests.py [--repo PATH] [--all] [--hash] [--verbose]
        [--allow-cannot-run FILE]
    python3 .claude/scripts/harness_tests.py --staged | --for PATH [PATH ...]   # scoped run
    python3 .claude/scripts/harness_tests.py --proofs-for PATH [PATH ...]     # no-stamp scoped run

A scoped run (`--staged`, `--for`) runs only the proofs bound to the given harness files and
refreshes only those files' entries on top of the last full-run stamp; the commit guard judges
per touched file, so that is exactly the certification a commit needs. The full battery stays
the session-close gate.

Any `cannot-run` proof (exit 2) makes the run INCOMPLETE: no passing stamp is written and the
run exits 2. `--allow-cannot-run FILE` names a JSON list of `{"proof", "platform", "reason"}`
objects — proofs that this machine cannot bind (a runner absent from the CI image, for example).
A listed proof's `cannot-run` no longer makes the run INCOMPLETE; an unlisted `cannot-run` still
does. The flag exits 2 before running anything when the file is missing, unparseable, holds an
entry missing `proof`, `platform` or `reason`, or lists a proof `discover()` never found. Without
the flag, an unlisted (i.e. every) `cannot-run` still makes the run INCOMPLETE.

A tested input that changes while the proofs run stays unstamped and the run exits 1; the
unchanged inputs keep their entries. A HEAD move alone is not a verdict.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
from _hook_state import write_json_atomic, HARNESS_DIRS as DIRS  # noqa: E402

sys.path.insert(0, os.path.join(_CLAUDE_DIR, "tools"))
import adaptation  # noqa: E402

STAMP_PATH = os.environ.get("HARNESS_TEST_STAMP") or os.path.join(
    _CLAUDE_DIR, "logs", "harness_tests_stamp.json"
)

# The template ships no excluded proofs and one built-in timeout, for its own slowest proof. A
# consumer adds project entries via `adaptation.json` `proof_excluded` / `proof_timeouts` (Design
# Doc §8, owner ruling R1) -- never by editing these dicts directly.
EXCLUDED = {}

_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "*.sh", "*.ps1")
_TIMEOUT_SEC = 120
# Git-fixture suites. Alone on 2026-09-23: publish 191 s, sync 132 s; both passed their caps
# beside a loaded machine only with about 2x headroom.
_BUILTIN_PROOF_TIMEOUTS = {"test_baseline_publish.py": 480, "test_baseline_sync.py": 300}
_PROOF_TIMEOUTS = dict(_BUILTIN_PROOF_TIMEOUTS)


def _merge_adaptation_proof_config() -> None:
    """Merge `adaptation.json` `proof_excluded` into `EXCLUDED` and `proof_timeouts` into
    `_PROOF_TIMEOUTS`. A `proof_timeouts` value that is not a positive int is skipped with
    one stderr line; `proof_excluded` values are reasons and need no further validation."""
    EXCLUDED.update(adaptation.get(_CLAUDE_DIR, "proof_excluded"))
    for name, seconds in adaptation.get(_CLAUDE_DIR, "proof_timeouts").items():
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            print(
                f"harness_tests: proof_timeouts entry {name!r}={seconds!r} is not a "
                "positive integer -- skipped",
                file=sys.stderr,
            )
            continue
        _PROOF_TIMEOUTS[name] = seconds


_merge_adaptation_proof_config()

# A bare "bash" on Windows PATH can resolve to System32's WSL shim, a different filesystem
# namespace that cannot see a Windows-style path. Prefer Git's bash.exe when present.
_GIT_BASH_CANDIDATES = (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")


def _bash_exe():
    if os.name == "nt":
        for candidate in _GIT_BASH_CANDIDATES:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


def discover(tests_dir):
    """Top-level proof files in `tests_dir` matching `_PATTERNS`, sorted. No recursion."""
    if not os.path.isdir(tests_dir):
        return []
    matched = []
    for name in sorted(os.listdir(tests_dir)):
        full = os.path.join(tests_dir, name)
        if os.path.isfile(full) and any(fnmatch.fnmatch(name, p) for p in _PATTERNS):
            matched.append(full)
    return matched


def _runner_cmd(path):
    if path.endswith(".py"):
        return [sys.executable, path]
    if path.endswith(".js"):
        return ["node", path]
    if path.endswith(".sh"):
        return [_bash_exe(), path]
    if path.endswith(".ps1"):
        return ["pwsh", "-NoProfile", "-File", path]
    raise ValueError("no runner for %s" % path)


_DETAIL_LINES = 20


# What `git rev-parse --local-env-vars` printed on git 2.x; used only when git cannot answer.
_GIT_LOCAL_ENV_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def git_local_env_vars():
    """Git's own list of repository-local environment variables (`git rev-parse
    --local-env-vars`), or `_GIT_LOCAL_ENV_FALLBACK` when git cannot answer."""
    try:
        result = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True,
                                env={k: v for k, v in os.environ.items() if k not in _GIT_LOCAL_ENV_FALLBACK})
        names = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
        if result.returncode == 0 and names:
            return names
    except OSError:
        pass
    return _GIT_LOCAL_ENV_FALLBACK


GIT_REPO_ENV = git_local_env_vars()
STAMP_INDEX_ENV = "HARNESS_STAMP_INDEX"


def run_proof(path, timeout=None):
    """(status, seconds, detail) — status is "pass", "cannot-run" or "fail".

    Exit 2 is unavailable coverage, not a passed or failed assertion. Its reason is
    reported, and a run with unavailable coverage does not issue a passing stamp.
    Proofs run without the caller's GIT_REPO_ENV: a private commit index must not reach a
    proof's temp-repo fixture. The stamp's own enumeration (`_git`) still honors it. The
    caller's GIT_INDEX_FILE reaches the proof as HARNESS_STAMP_INDEX, so a proof that reads
    the real repo's index passes it back as GIT_INDEX_FILE for that one call.
    """
    if timeout is None:
        timeout = _PROOF_TIMEOUTS.get(os.path.basename(path), _TIMEOUT_SEC)
    env = {k: v for k, v in os.environ.items() if k not in GIT_REPO_ENV and k != STAMP_INDEX_ENV}
    if os.environ.get("GIT_INDEX_FILE"):
        env[STAMP_INDEX_ENV] = os.environ["GIT_INDEX_FILE"]
    start = time.time()
    try:
        result = subprocess.run(_runner_cmd(path), capture_output=True, text=True, timeout=timeout, env=env)
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            status, detail = "pass", ""
        elif result.returncode == 2:
            status, detail = "cannot-run", out
        else:
            status, detail = "fail", out
    except subprocess.TimeoutExpired:
        status, detail = "fail", "TIMEOUT after %ds" % timeout
    except OSError as exc:
        status, detail = "fail", "runner unavailable: %s" % exc
    return status, time.time() - start, detail


class AllowCannotRunError(ValueError):
    """Raised for any malformed --allow-cannot-run input; the caller turns this into exit 2."""


def _load_allow_cannot_run(path):
    """{proof-basename: entry} from an --allow-cannot-run FILE. Raises AllowCannotRunError on any
    malformed input; does not check the entries against discovered proofs — the caller does that
    once it knows which proofs exist."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise AllowCannotRunError("--allow-cannot-run file not found: %s (%s)" % (path, exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AllowCannotRunError("--allow-cannot-run file is not valid JSON: %s (%s)" % (path, exc))
    if not isinstance(data, list):
        raise AllowCannotRunError("--allow-cannot-run file must be a JSON list: %s" % path)
    entries = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise AllowCannotRunError("--allow-cannot-run entry is not an object: %r" % (entry,))
        missing = [key for key in ("proof", "platform", "reason") if not entry.get(key)]
        if missing:
            raise AllowCannotRunError(
                "--allow-cannot-run entry missing %s: %r" % (", ".join(missing), entry)
            )
        entries[entry["proof"]] = entry
    return entries


def _git(repo_root, args):
    """Raise on any git failure: a hash over a failed enumeration must never be stamped."""
    result = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args[0], repo_root, result.stderr.strip()))
    return result.stdout


_CODE_EXT = (".py", ".ps1", ".sh", ".js")


_SETTINGS_FILES = (".claude/settings.json", ".claude/settings.base.json", ".claude/settings.project.json")


def _is_code(rel):
    rel = rel.replace(os.sep, "/")
    return rel in _SETTINGS_FILES or rel.endswith(_CODE_EXT)


def tree_entries(repo_root):
    """{posix-relpath: sha256(bytes)} for every tracked/untracked-unignored code file under
    `DIRS`, enumerated by `git ls-files`, run with cwd=repo_root — plus every proof
    `discover()` finds: new files under `.claude/tests/` are gitignored (existing proofs were
    force-added), so a not-yet-added proof is invisible to both `ls-files` calls and would
    leave the set unchanged. The stamp stores this map so the commit guard can judge only the
    paths a commit touches."""
    files = set()
    for out in (
        _git(repo_root, ["ls-files", "--"] + list(DIRS)),
        _git(repo_root, ["ls-files", "-o", "--exclude-standard", "--"] + list(DIRS)),
    ):
        for line in out.splitlines():
            line = line.strip()
            if line:
                files.add(line)
    for full in discover(os.path.join(repo_root, ".claude", "tests")):
        files.add(os.path.relpath(full, repo_root).replace(os.sep, "/"))

    # The stamp covers harness CODE. `.claude/scripts/benchmark_campaign/` also holds campaign
    # data a peer session writes mid-run (.env, score .json, .bak); hashing it makes every
    # peer write a stale stamp for a commit that changed no code.
    files = {f for f in files if _is_code(f)}

    entries = {}
    for rel in sorted(files):
        full = os.path.join(repo_root, rel)
        try:
            with open(full, "rb") as fh:
                entries[rel.replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
    return entries


def tree_hash(repo_root):
    """One digest over `tree_entries` — the whole-tree freshness key."""
    blob = json.dumps(sorted(tree_entries(repo_root).items())).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _write_stamp(repo_root, run, passed, excluded_list, indeterminate=(), entries=None, head=None):
    if entries is None:
        entries = tree_entries(repo_root)
    if head is None:
        head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    blob = json.dumps(sorted(entries.items())).encode("utf-8")
    stamp = {
        "ts": time.time(),
        "tree_hash": hashlib.sha256(blob).hexdigest(),
        "files": entries,
        "head": head,
        "run": run,
        "pass": passed,
        # Recorded so a green stamp cannot claim more coverage than it had: these proofs ran and
        # could not bind their target, which is not the same as passing.
        "cannotRun": [os.path.basename(p) for p in indeterminate],
        "excluded": excluded_list,
    }
    write_json_atomic(STAMP_PATH, stamp)


# Proofs that scan every hook rather than one target; a scoped run always includes them.
_DIR_SCANNING = ("test_hook_state.py",)


def _norm(rel):
    return rel.replace(os.sep, "/")


def _import_re(module):
    return re.compile(r"^\s*(?:from\s+%s\s+import\b|import\s+(?:[\w.]+\s*,\s*)*%s\b)"
                      % (re.escape(module), re.escape(module)), re.MULTILINE)


def _hook_importers(repo_root, touched):
    """Basenames of the `.claude/hooks/*.py` that import a touched `_`-prefixed hooks helper,
    directly or through another helper."""
    hooks_dir = os.path.join(repo_root, ".claude", "hooks")
    pending = [os.path.splitext(os.path.basename(t))[0] for t in touched
               if t.startswith(".claude/hooks/") and t.count("/") == 2
               and os.path.basename(t).startswith("_") and t.endswith(".py")]
    if not pending or not os.path.isdir(hooks_dir):
        return set()
    sources = {}
    for name in os.listdir(hooks_dir):
        if name.endswith(".py"):
            try:
                with open(os.path.join(hooks_dir, name), "rb") as fh:
                    sources[name] = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
    seen, importers = set(pending), set()
    while pending:
        pattern = _import_re(pending.pop())
        for name, text in sources.items():
            if name in importers or not pattern.search(text):
                continue
            importers.add(name)
            stem = name[:-3]
            if stem.startswith("_") and stem not in seen:
                seen.add(stem)
                pending.append(stem)
    return importers


def select_for(repo_root, touched, include_excluded=False):
    """The proofs bound to `touched` harness files: the touched proofs themselves, `test_<stem>*` /
    `<stem>_test.*` by name, any proof whose source names a touched file's basename, the
    dir-scanning proofs, and every proof that names `settings.json` when it is touched. A touched
    `_`-prefixed hooks helper also binds the proofs of every hook that imports it, directly or
    through another helper. Over-selection is harmless; under-selection is what the name and
    mention rules guard."""
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    if not include_excluded:
        discovered = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
    touched = [_norm(t) for t in touched]
    bound = [os.path.basename(t) for t in touched] + sorted(_hook_importers(repo_root, touched))
    stems = {os.path.splitext(b)[0] for b in bound}
    basenames = set(bound)
    settings = any(_norm(t) in _SETTINGS_FILES for t in touched)
    selected = []
    for path in discovered:
        name = os.path.basename(path)
        rel = _norm(os.path.relpath(path, repo_root))
        if rel in touched or name in _DIR_SCANNING:
            selected.append(path)
            continue
        if any(name.startswith("test_" + s) or name.startswith(s + "_test") for s in stems):
            selected.append(path)
            continue
        try:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        if any(b in text for b in basenames) or (settings and any(os.path.basename(f) in text for f in _SETTINGS_FILES)):
            selected.append(path)
    return selected


def _run_selected(selected, verbose):
    passed, failed, indeterminate, elapsed_total = 0, 0, [], 0.0
    label = {"pass": "OK", "cannot-run": "CANNOT-RUN", "fail": "FAIL"}
    for path in selected:
        status, elapsed, detail = run_proof(path)
        elapsed_total += elapsed
        if verbose or status != "pass":
            print("%s %.1fs %s" % (label[status], elapsed, path))
        if detail:
            for line in detail.strip().splitlines()[-_DETAIL_LINES:]:
                print("    | " + line)
        if status == "pass":
            passed += 1
        elif status == "cannot-run":
            indeterminate.append(path)
        else:
            failed += 1
    return passed, failed, indeterminate, elapsed_total


def run_scoped(repo_root, touched, verbose=False):
    """Run only the proofs bound to `touched` and refresh only those files' stamp entries on top of
    the last full-run stamp. The commit guard judges per touched file, so a green scoped run
    certifies exactly what this commit changes; every other entry keeps the digest its full run
    certified. Needs a base stamp: without one there is nothing to refresh into."""
    from _hook_state import read_json_salvage
    stamp = read_json_salvage(STAMP_PATH) if os.path.exists(STAMP_PATH) else None
    if not stamp or not isinstance(stamp.get("files"), dict):
        print("harness_tests: no full-run stamp to scope into; run the full battery once first")
        return 1
    tested_entries = tree_entries(repo_root)
    tested_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    touched = [_norm(t) for t in touched]
    in_scope = [t for t in touched if t in tested_entries or t in stamp["files"]]
    if not in_scope:
        print("harness_tests: none of the given paths is a stamped harness input: %s" % ", ".join(touched))
        return 1
    selected = select_for(repo_root, in_scope)
    if not selected:
        print("harness_tests: no proof is bound to %s; add test_<name>*.py or run the full battery" % ", ".join(in_scope))
        return 1
    passed, failed, indeterminate, elapsed_total = _run_selected(selected, verbose)
    print("harness_tests: scoped to %d input(s): %d run, %d pass, %d fail, %d cannot-run, %.1fs"
          % (len(in_scope), len(selected), passed, failed, len(indeterminate), elapsed_total))
    if failed:
        return 1
    if indeterminate:
        print("harness_tests: INCOMPLETE; scoped stamp not written")
        return 2
    current_entries = tree_entries(repo_root)
    moved = [t for t in in_scope if tested_entries.get(t) != current_entries.get(t)]
    if moved:
        print("harness_tests: input(s) changed during the scoped run; nothing stamped: " + ", ".join(moved))
        return 1
    files = dict(stamp["files"])
    for t in in_scope:
        if t in tested_entries:
            files[t] = tested_entries[t]
        else:
            files.pop(t, None)
    blob = json.dumps(sorted(files.items())).encode("utf-8")
    stamp["files"] = files
    stamp["tree_hash"] = hashlib.sha256(blob).hexdigest()
    stamp["scoped"] = {"ts": time.time(), "head": tested_head, "paths": in_scope,
                       "proofs": [os.path.basename(p) for p in selected]}
    write_json_atomic(STAMP_PATH, stamp)
    print("harness_tests: scoped stamp refreshed for %d input(s) (%d proofs)" % (len(in_scope), len(selected)))
    return 0


def staged_harness_paths(repo_root):
    out = _git(repo_root, ["diff", "--cached", "--name-only", "--"] + list(DIRS))
    return [_norm(line.strip()) for line in out.splitlines() if line.strip() and _is_code(line.strip())]


def run_all(repo_root, include_excluded=False, verbose=False, allow_cannot_run=None):
    tested_entries = tree_entries(repo_root)
    tested_head = _git(repo_root, ["rev-parse", "HEAD"]).strip() or None
    tests_dir = os.path.join(repo_root, ".claude", "tests")
    discovered = discover(tests_dir)
    present = {os.path.basename(p) for p in discovered}

    # `.claude/tests/` is gitignored, so a worktree or sparse checkout lacks the excluded
    # proofs. An absent entry is reported and skipped, never a FAIL that stops discovery:
    # git cannot tell "untracked file not in this checkout" from "file deleted".
    absent = sorted(name for name in EXCLUDED if name not in present)
    for name in absent:
        print("SKIP absent EXCLUDED entry: %s (%s)" % (name, EXCLUDED[name]))

    allowed_entries = None
    if allow_cannot_run is not None:
        try:
            allowed_entries = _load_allow_cannot_run(allow_cannot_run)
        except AllowCannotRunError as exc:
            print("harness_tests: %s" % exc)
            return 2
        unknown = sorted(name for name in allowed_entries if name not in present)
        if unknown:
            print(
                "harness_tests: --allow-cannot-run lists a proof discover() never found: %s"
                % ", ".join(unknown)
            )
            return 2

    if include_excluded:
        selected = discovered
        excluded_list = []
    else:
        selected = [p for p in discovered if os.path.basename(p) not in EXCLUDED]
        excluded_list = sorted(
            os.path.basename(p) for p in discovered if os.path.basename(p) in EXCLUDED
        )

    if not selected:
        print("FAIL no proofs to run: %d discovered under %s (patterns %s), %d excluded"
              % (len(discovered), tests_dir, ", ".join(_PATTERNS), len(excluded_list)))
        print("harness_tests: 0 run, 0 pass, 0 fail, 0 cannot-run, %d excluded, 0.0s"
              % len(excluded_list))
        return 1

    passed, failed, indeterminate, elapsed_total = _run_selected(selected, verbose)

    print(
        "harness_tests: %d run, %d pass, %d fail, %d cannot-run, %d excluded, %.1fs"
        % (len(selected), passed, failed, len(indeterminate), len(excluded_list), elapsed_total)
    )
    # Named, never a footnote: a proof that stopped binding its target is how a guard quietly stops
    # guarding, and the reader has to be able to tell "a peer is refactoring" from "mine broke".
    if indeterminate:
        # ASCII only: this runner prints to a Windows console whose default codepage mangles an
        # em-dash, and a summary line nobody can read is a summary line nobody reads.
        print("cannot-run (exit 2: the proof could not bind its target; not counted as failure):")
        for p in indeterminate:
            print("    - %s" % p)

    if allowed_entries is not None:
        allowed_hits = [p for p in indeterminate if os.path.basename(p) in allowed_entries]
        unlisted_hits = [p for p in indeterminate if os.path.basename(p) not in allowed_entries]
        print("harness_tests: %d cannot-run exception(s) allowed" % len(allowed_hits))
    else:
        unlisted_hits = list(indeterminate)

    if failed:
        return 1
    if unlisted_hits:
        print("harness_tests: INCOMPLETE; no passing stamp written")
        return 2

    # A HEAD move is not a verdict: tree_entries hashes working-tree bytes, so a peer commit that touched a
    # stamped input shows up below as a changed file, and one that touched only docs, ledgers or benchmark
    # results changes nothing the stamp certifies. The stamp records the tested HEAD; the commit guard judges
    # per touched file. (2026-09-15: three green batteries, 20 minutes, discarded by a head-equality gate.)
    current_entries = tree_entries(repo_root)
    changed = sorted(path for path in tested_entries.keys() | current_entries.keys()
                     if tested_entries.get(path) != current_entries.get(path))
    stamped = {path: digest for path, digest in tested_entries.items() if path not in changed}
    if changed:
        # A changed source is never certified and the run is not green (exit 1). The stamp is per file and the commit
        # guard judges only the paths a commit touches, so the UNCHANGED files keep their proof: a peer's edit
        # mid-run stales that file alone, exactly as the same edit one second after the run would.
        print("harness_tests: %d input(s) changed during verification; they stay unstamped:" % len(changed))
        for path in changed[:20]:
            print("    - " + path)
        if len(changed) > 20:
            print("    ... %d more changed inputs" % (len(changed) - 20))
        if stamped:
            _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate,
                         entries=stamped, head=tested_head)
            print("harness_tests: partial stamp written for the %d unchanged input(s)" % len(stamped))
        return 1
    _write_stamp(repo_root, len(selected), passed, excluded_list, indeterminate,
                 entries=stamped, head=tested_head)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run harness proofs and stamp unchanged passing inputs.")
    parser.add_argument("--repo", default=REPO_ROOT, help="repository containing the proofs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="include normally excluded proofs")
    mode.add_argument("--hash", action="store_true", help="print the current input hash without running proofs")
    mode.add_argument("--staged", action="store_true",
                      help="scoped: run only the proofs bound to the staged harness files and refresh their stamp entries")
    mode.add_argument("--for", dest="for_paths", nargs="+", metavar="PATH",
                      help="scoped: run only the proofs bound to these harness files and refresh their stamp entries")
    mode.add_argument("--proofs-for", dest="proofs_for_paths", nargs="+", metavar="PATH",
                      help="run only proofs bound to these paths without reading or writing a stamp")
    parser.add_argument("--verbose", action="store_true", help="print every proof result, not only failures and summary")
    parser.add_argument(
        "--allow-cannot-run",
        metavar="FILE",
        default=None,
        help="JSON list of {proof, platform, reason} objects whose cannot-run does not INCOMPLETE the run",
    )
    args = parser.parse_args(argv)
    if args.hash:
        print(tree_hash(args.repo))
        return 0
    if args.proofs_for_paths:
        selected = select_for(args.repo, args.proofs_for_paths)
        if not selected:
            print("harness_tests: no proof selected for %s" % ", ".join(args.proofs_for_paths))
            return 1
        passed, failed, indeterminate, elapsed_total = _run_selected(selected, verbose=True)
        print("harness_tests: scoped: %d run, %d pass, %d fail, %d cannot-run, %.1fs"
              % (len(selected), passed, failed, len(indeterminate), elapsed_total))
        if failed:
            return 1
        if indeterminate:
            print("cannot-run (exit 2: the proof could not bind its target; the scoped run is incomplete):")
            for proof in indeterminate:
                print("    - %s" % proof)
            return 2
        return 0
    if args.staged or args.for_paths:
        touched = args.for_paths or staged_harness_paths(args.repo)
        if not touched:
            print("harness_tests: nothing staged under the harness dirs")
            return 1
        return run_scoped(args.repo, touched, verbose=args.verbose)
    return run_all(
        args.repo,
        include_excluded=args.all,
        verbose=args.verbose,
        allow_cannot_run=args.allow_cannot_run,
    )


if __name__ == "__main__":
    sys.exit(main())
