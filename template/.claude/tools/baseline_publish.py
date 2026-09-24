#!/usr/bin/env python3
"""Run the baseline publication transaction's eight steps (§6 of the sync-baseline-v2 design).

    python3 .claude/tools/baseline_sync.py publish (--from-commit SHA | --from-worktree PATH)
        [--rows FILE] [--accept-hit ID]... [--dry-run] [--no-ci]
    python3 .claude/tools/baseline_sync.py publish --resume ID [--accept-hit ID]...

One journal (`.claude/.cache/baseline-publish/<id>.json`) records every step's result,
written atomically before the next step starts, so a crash anywhere is recoverable with
`--resume`. `--from-commit` publishes a consumer's own committed rows (verdict `push` in its
lock); `--from-worktree` publishes a baseline author worktree's `HEAD` tree directly — never
its commit history, so a planted token that only ever existed in an ancestor commit never
reaches the published tree.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
import baseline_sync as sync  # noqa: E402
import baseline_identity  # noqa: E402

STEP_NAMES = (
    "collect", "classify", "materialize", "scrub",
    "validate", "publish", "update-lock", "check",
)


class PublishError(RuntimeError):
    """A publication step refused to continue."""


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PublishError(f"git {' '.join(args)} failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = _lf(result.stderr).decode("utf-8", errors="replace").strip()
        raise PublishError(f"git {' '.join(args)} failed{(': ' + detail) if detail else ''}")
    return result


def _git_text(repo: Path, args: list[str]) -> str:
    return _lf(_git(repo, args).stdout).decode("utf-8", errors="replace").strip()


def _git_show(repo: Path, commit: str, relpath: str) -> bytes | None:
    result = _git(repo, ["show", f"{commit}:{relpath}"], check=False)
    if result.returncode != 0:
        return None
    return _lf(result.stdout)


def _write_lf(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    path.write_bytes(_lf(data))


def _write_json_atomic(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_lf(payload))
            stream.flush()
            os.fsync(stream.fileno())
        sync.replace_path(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _journal_dir(root: Path) -> Path:
    return root / ".claude" / ".cache" / "baseline-publish"


def _journal_path(root: Path, journal_id: str) -> Path:
    return _journal_dir(root) / f"{journal_id}.json"


def _rows_sha(rows: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(rows), separators=(",", ":")).encode("utf-8")).hexdigest()


def _new_journal(source: dict, rows: list[str], row_meta: dict[str, dict],
                  dry_run: bool, no_ci: bool, full_battery: bool, baseline_before: str,
                  journal_id: str, supersedes: str | None) -> dict:
    return {
        "id": journal_id,
        "source": source,
        "worktree": {"path": None, "branch": None, "head": None, "committed": False},
        "baseline_sha_before": baseline_before,
        "baseline_sha_after": None,
        "dry_run": dry_run,
        "no_ci": no_ci,
        "full_battery": full_battery,
        "supersedes": supersedes,
        "owner_confirmed_at": None,
        "ci_wait_s": 0,
        "started_at": _now(),
        "finished_at": None,
        "rows_published": rows,
        "row_meta": row_meta,
        "rows_sha256": _rows_sha(rows),
        "rows_judged_count": 0,
        "pr_url": None,
        "ci_run_url": None,
        "steps": [
            {"name": name, "status": "pending", "started_at": None,
             "finished_at": None, "evidence": ""}
            for name in STEP_NAMES
        ],
    }


def _step(journal: dict, path: Path, index: int, action) -> object:
    item = journal["steps"][index]
    if item["status"] == "green":
        return None
    item["started_at"] = _now()
    item["status"] = "pending"
    _write_json_atomic(path, journal)
    try:
        value = action()
    except Exception as exc:
        item["status"] = "red"
        item["finished_at"] = _now()
        item["evidence"] = str(exc)
        _write_json_atomic(path, journal)
        raise
    item["status"] = "green"
    item["finished_at"] = _now()
    item["evidence"] = "ok" if value is None else str(value)
    _write_json_atomic(path, journal)
    return value


# ---------------------------------------------------------------------------
# Step 1: collect
# ---------------------------------------------------------------------------

def _validate_rows(root: Path, source: dict, rows: list[str], lock: dict) -> list[str]:
    if len(set(rows)) != len(rows):
        raise PublishError("rows must be unique")
    collected = []
    for relpath in rows:
        entry = lock.get("files", {}).get(relpath)
        if entry is None:
            raise PublishError(f"row has no lock entry: {relpath}")
        content = _git_show(Path(source["repo"]), source["commit"], relpath)
        if content is None:
            raise PublishError(f"source commit has no row: {relpath}")
        judged = entry.get("judged") or {}
        if judged.get("verdict") != "push":
            raise PublishError(f"row is not judged push: {relpath}")
        if judged.get("sha") != sync.sha(content):
            raise PublishError(f"judged sha does not match source commit: {relpath}")
        collected.append(relpath)
    return collected


def _collect_commit(root: Path, source: dict, requested: list[str] | None,
                     lock: dict) -> list[dict]:
    if requested is not None:
        relpaths = _validate_rows(root, source, requested, lock)
    else:
        rows = [relpath for relpath, entry in sorted(lock.get("files", {}).items())
                if (entry.get("judged") or {}).get("verdict") == "push"]
        relpaths = _validate_rows(root, source, rows, lock)
    return [
        {"relpath": relpath, "source_path": relpath, "dest_path": "template/" + relpath,
         "kind": "lock", "op": "A"}
        for relpath in relpaths
    ]


def _worktree_record(raw_path: str, op: str) -> dict:
    """§5: every record in the worktree diff publishes -- there is no scope filter, the
    parenthetical path list there is examples. A `template/` path is a lock record; any
    other repo-root path is a root record."""
    if raw_path.startswith("template/"):
        relpath = raw_path[len("template/"):]
        kind = "lock"
    else:
        relpath = raw_path
        kind = "root"
    return {"relpath": relpath, "source_path": raw_path, "dest_path": raw_path,
            "kind": kind, "op": op}


def _parse_diff_records(raw: str) -> list[dict]:
    """Parse `git diff --name-status -z` output into worktree records. A `R` (rename)
    record removes the old path and adds the new one; a `C` (copy) record keeps its
    source untouched and only adds the new path."""
    parts = raw.split("\x00")
    records: list[dict] = []
    i = 0
    while i < len(parts):
        code = parts[i]
        if not code:
            i += 1
            continue
        if code[0] in ("R", "C"):
            old_path, new_path = parts[i + 1], parts[i + 2]
            i += 3
            if code[0] == "R":
                records.append(_worktree_record(old_path, "D"))
            records.append(_worktree_record(new_path, "A"))
        else:
            path = parts[i + 1]
            i += 2
            records.append(_worktree_record(path, code[0]))
    return records


def _diff_records(worktree: Path, pinned_sha: str) -> list[dict]:
    """Every changed path between `pinned_sha` and `HEAD` (§5: every record publishes)."""
    result = _git(worktree, ["diff", "--name-status", "-z", f"{pinned_sha}..HEAD"])
    raw = _lf(result.stdout).decode("utf-8", errors="replace")
    return _parse_diff_records(raw)


def _collect_worktree(worktree: Path, pinned_sha: str, requested: list[str] | None) -> list[dict]:
    records = _diff_records(worktree, pinned_sha)
    if not records:
        raise PublishError("--from-worktree has no changes since the pinned baseline commit")
    if requested is not None:
        if len(set(requested)) != len(requested):
            raise PublishError("rows must be unique")
        by_relpath = {record["relpath"]: record for record in records}
        missing = [relpath for relpath in requested if relpath not in by_relpath]
        if missing:
            raise PublishError("row not found in worktree diff: " + ", ".join(missing))
        records = [by_relpath[relpath] for relpath in requested]
    return records


def _collect(root: Path, source: dict, requested: list[str] | None, lock: dict,
             worktree_source: Path | None, pinned_sha: str) -> list[dict]:
    if source["kind"] == "worktree":
        return _collect_worktree(worktree_source, pinned_sha, requested)
    return _collect_commit(root, source, requested, lock)


# ---------------------------------------------------------------------------
# Step 2: classify
# ---------------------------------------------------------------------------

def _candidates(source: dict, lock: dict) -> list[str]:
    result = _git(Path(source["repo"]), ["ls-tree", "-r", "--name-only", source["commit"], "--", ".claude"])
    known = set(lock.get("files", {}))
    candidates = []
    for relpath in _lf(result.stdout).decode(errors="replace").splitlines():
        if relpath in known or relpath == sync.LOCK_RELPATH:
            continue
        if any(relpath.startswith(prefix) for prefix in sync.CANDIDATE_EXCLUDE_PREFIXES):
            continue
        name = relpath.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(name, pattern) for pattern in sync.CANDIDATE_EXCLUDE_NAMES):
            continue
        candidates.append(relpath)
    return candidates


def _classify(source: dict, lock: dict) -> str:
    if source["kind"] == "worktree":
        return "no candidates (worktree source)"
    candidates = _candidates(source, lock)
    if candidates:
        raise PublishError("source has candidates: " + ", ".join(candidates))
    return "no candidates"


# ---------------------------------------------------------------------------
# Step 3: materialize
# ---------------------------------------------------------------------------

MANIFEST_LAYERS = ("pure", "coding", "godot")


def _pinned_manifest_layers(cache: Path, baseline_sha: str) -> dict[str, str]:
    shown = _git(cache, ["show", f"{baseline_sha}:baseline.manifest.json"], check=False)
    if shown.returncode != 0:
        return {}
    return {entry["path"]: entry.get("layer") for entry in json.loads(shown.stdout).get("files", [])}


def _new_row_layers(records: list[dict], lock: dict, cache: Path, baseline_sha: str) -> dict[str, str]:
    """Template relpath -> layer for each lock row the pinned baseline does not have yet, and for
    each existing row whose lock layer differs from the pinned manifest's (a layer move). A new
    row with no layer refuses: validate's `gen_manifest.py --check` fails on any unclassified file."""
    layers, missing = {}, []
    pinned = _pinned_manifest_layers(cache, baseline_sha)
    for record in records:
        if record.get("kind") != "lock" or record["op"] == "D":
            continue
        layer = (lock.get("files", {}).get(record["relpath"]) or {}).get("layer")
        exists = _git(cache, ["cat-file", "-e", f"{baseline_sha}:{record['dest_path']}"], check=False)
        if exists.returncode == 0:
            template_rel = record["dest_path"][len("template/"):]
            if layer in MANIFEST_LAYERS and pinned.get(template_rel) not in (None, layer):
                layers[template_rel] = layer
            continue
        if layer in MANIFEST_LAYERS:
            layers[record["dest_path"][len("template/"):]] = layer
        else:
            missing.append(record["relpath"])
    if missing:
        raise PublishError(
            "new row(s) have no baseline layer: " + ", ".join(missing)
            + "; record each with `baseline_sync.py classify <relpath> --status tracked --layer pure|coding|godot`"
        )
    return layers


def _record_layers_and_regenerate_manifest(worktree: Path, layers: dict[str, str]) -> None:
    """Merge new rows' layers into `tools/layer_entries.json`, then regenerate the manifest, so
    validate's `gen_manifest.py --check` sees every published file classified and the manifest current."""
    if layers:
        entries_path = worktree / "tools" / "layer_entries.json"
        data = json.loads(entries_path.read_text(encoding="utf-8")) if entries_path.exists() else {"version": 1}
        merged = dict(data.get("entries") or {})
        merged.update(layers)
        data["entries"] = dict(sorted(merged.items()))
        _write_lf(entries_path, json.dumps(data, indent=2) + "\n")
    if (worktree / "tools" / "gen_manifest.py").is_file():
        _run_python_args(worktree, ["tools/gen_manifest.py"], "gen_manifest.py")


def _materialize(root: Path, source: dict, records: list[dict], lock: dict,
                  cache: Path, baseline_sha: str, worktree: Path, branch: str) -> str:
    # A worktree source is an author checkout: its author owns the pattern lists and the manifest.
    layers = _new_row_layers(records, lock, cache, baseline_sha) if source["kind"] == "commit" else None
    if worktree.exists():
        raise PublishError(f"publication worktree already exists: {worktree}")
    # A `--resume` that redoes this step (worktree lost between runs) reuses the same
    # journal id, hence the same branch name; that branch, and its administrative worktree
    # registration, may still exist from the lost attempt even though its directory is
    # gone. Prune stale registrations and reuse the branch instead of failing on "branch
    # already exists" / "missing but already registered worktree".
    _git(cache, ["worktree", "prune"])
    branch_exists = _git(
        cache, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False
    ).returncode == 0
    if branch_exists:
        _git(cache, ["worktree", "add", str(worktree), branch])
    else:
        _git(cache, ["worktree", "add", "-b", branch, str(worktree), baseline_sha])
    for record in records:
        content = None
        if record["op"] != "D":
            content = _git_show(Path(source["repo"]), source["commit"], record["source_path"])
        destination = worktree / record["dest_path"]
        if content is None:
            if destination.exists():
                _git(worktree, ["rm", "-q", "--", record["dest_path"]])
            continue
        _write_lf(destination, content)
    if layers is not None:
        _record_layers_and_regenerate_manifest(worktree, layers)
    return _git_text(worktree, ["rev-parse", "HEAD"])


# ---------------------------------------------------------------------------
# Step 4: scrub
# ---------------------------------------------------------------------------

def _hit_id(hit) -> str:
    return getattr(hit, "id", None) or f"{hit['path']}:{hit['line']}:{hit['token_kind']}:{hit['token_digest']}"


def _scrub(root: Path, worktree: Path, records: list[dict], lock: dict,
           accept_hits: list[str], baseline_dir: Path) -> str:
    for record in records:
        if record["kind"] != "lock" or record["op"] == "D":
            continue  # root files skip reverse substitution (§5); deletions have no content
        destination = worktree / record["dest_path"]
        if not destination.exists():
            continue
        rendered = destination.read_bytes().decode("utf-8", errors="replace")
        _write_lf(destination, sync.reverse_for(record["relpath"], rendered, lock.get("substitutions", {})))
    _git(worktree, ["add", "-A"])
    diff_text = _lf(_git(worktree, ["diff", "--cached"]).stdout).decode("utf-8", errors="replace")
    profile = baseline_identity.build_profile_for_repo(root, baseline_dir=str(baseline_dir))
    hits = baseline_identity.scan_changed(diff_text, profile)
    accepted = set(accept_hits)
    remaining = [hit for hit in hits if _hit_id(hit) not in accepted]
    if remaining:
        raise PublishError("identity scan found hit(s): " + ", ".join(_hit_id(h) for h in remaining))
    used = sorted(accepted & {_hit_id(hit) for hit in hits})
    return "accepted hits: " + (", ".join(used) if used else "none")


# ---------------------------------------------------------------------------
# Step 5: validate
# ---------------------------------------------------------------------------

def _battery_counts_match(worktree: Path) -> None:
    """§5 battery count check: discovery inside the worktree must find exactly as many
    proofs as `git ls-files template/.claude/tests` lists matching the runner's
    `_PATTERNS` -- guards against discovery silently voiding under a nested worktree."""
    script = worktree / "template" / ".claude" / "scripts" / "harness_tests.py"
    tests_dir = worktree / "template" / ".claude" / "tests"
    if not script.is_file():
        return
    spec = importlib.util.spec_from_file_location("harness_tests_under_publish", script)
    if spec is None or spec.loader is None:
        raise PublishError(f"could not load {script} for the battery count check")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    discovered = len(module.discover(str(tests_dir)))
    listed = _git_text(worktree, ["ls-files", "template/.claude/tests"]).splitlines()
    patterns = getattr(module, "_PATTERNS", ())
    expected = sum(
        1 for name in listed
        if name and any(fnmatch.fnmatch(Path(name).name, pattern) for pattern in patterns)
    )
    if discovered != expected:
        raise PublishError(
            f"battery discovery mismatch: discover() found {discovered}, "
            f"git ls-files template/.claude/tests lists {expected} matching proof(s)"
        )


def _run_python_args(worktree: Path, args: list[str], label: str) -> str:
    result = subprocess.run(
        [sys.executable, *args], cwd=str(worktree),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    out = _lf(result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        raise PublishError(f"{label} failed" + ((": " + out) if out else ""))
    return out


def _battery_mode(records: list[dict], full_battery: bool) -> tuple[str, str]:
    if full_battery:
        return "full", "flag"
    for record in records:
        relpath = record["relpath"].replace("\\", "/")
        if record["kind"] == "root":
            return "full", relpath
        if relpath.startswith((".claude/hooks/", ".claude/scripts/")):
            return "full", relpath
        if relpath.startswith(".claude/settings") and relpath.endswith(".json"):
            return "full", relpath
        if relpath in {".claude/tools/adaptation.py", ".claude/tools/_lock_rows.py",
                       ".claude/tools/layer_closure.py"} or (
                relpath.startswith(".claude/tools/baseline_") and relpath.endswith(".py")
        ):
            return "full", relpath
        if relpath.startswith(".claude/tests/") and record["op"] == "D":
            return "full", relpath
    return "scoped", "published rows"


def _validate(worktree: Path, records: list[dict], full_battery: bool) -> str:
    mode, reason = _battery_mode(records, full_battery)
    checks_start = time.monotonic()
    outputs = []
    try:
        outputs.append(_run_python_args(worktree, ["tools/gen_manifest.py", "--check"], "gen_manifest.py --check"))
        outputs.append(_run_python_args(worktree, ["tools/audit_baseline.py", "--strict"],
                                         "audit_baseline.py --strict"))
        tests_root = worktree / "tests"
        if tests_root.is_dir():
            for test_path in sorted(tests_root.glob("test_*.py")):
                relpath = str(test_path.relative_to(worktree))
                outputs.append(_run_python_args(worktree, [relpath], f"tests/{test_path.name}"))
        harness_script = worktree / "template" / ".claude" / "scripts" / "harness_tests.py"
        if harness_script.is_file():
            _battery_counts_match(worktree)
    except Exception as exc:
        checks_elapsed = time.monotonic() - checks_start
        raise PublishError(
            f"mode={mode}; reason={reason}; checks={checks_elapsed:.3f}s; battery=0.000s; {exc}"
        ) from exc
    checks_elapsed = time.monotonic() - checks_start
    battery_elapsed = 0.0
    battery_output = "no harness runner"
    if harness_script.is_file():
        if mode == "full":
            args = ["template/.claude/scripts/harness_tests.py"]
            label = "harness_tests.py"
        else:
            rows = [r["relpath"] for r in records if r["kind"] == "lock" and r["op"] != "D"]
            args = [".claude/scripts/harness_tests.py", "--proofs-for", *rows]
            label = "harness_tests.py --proofs-for"
        battery_start = time.monotonic()
        try:
            if mode == "full":
                battery_output = _run_python_args(worktree, args, label)
            else:
                battery_output = _run_python_args(worktree / "template", args, label)
        except Exception as exc:
            battery_elapsed = time.monotonic() - battery_start
            selected = [line.strip() for line in str(exc).splitlines()
                        if line.startswith(("OK ", "FAIL ", "CANNOT-RUN "))]
            proofs = ", ".join(selected) or ("full battery" if mode == "full" else "none selected")
            raise PublishError(
                f"mode={mode}; reason={reason}; proofs={proofs}; checks={checks_elapsed:.3f}s; "
                f"battery={battery_elapsed:.3f}s; {exc}"
            ) from exc
        battery_elapsed = time.monotonic() - battery_start
    selected = []
    if mode == "scoped":
        selected = [line.strip() for line in battery_output.splitlines()
                    if line.startswith(("OK ", "FAIL ", "CANNOT-RUN "))]
    return (f"mode={mode}; reason={reason}; proofs={', '.join(selected) or 'full battery'}; "
            f"checks={checks_elapsed:.3f}s; battery={battery_elapsed:.3f}s; "
            + "; ".join(o for o in outputs if o))


# ---------------------------------------------------------------------------
# Step 6: publish (push, PR, checks, merge)
# ---------------------------------------------------------------------------

def _merge_local(repo: str, branch: str, expected_head: str, work_parent: Path) -> str:
    """Push a branch and merge it into main in a local (non-network) repository.

    Real `gh pr merge` talks to GitHub's server; this is the mechanism a fake `gh` test
    fixture uses to actually perform that merge against a local bare remote, since there is
    no GitHub server in a proof. Not called by `_publish` itself.
    """
    if not (str(repo).lower().startswith("file://") or Path(str(repo)).is_dir()):
        raise PublishError(f"the local merge seam accepts only a local repository, not {repo}")
    merge_dir = work_parent / "merge-checkout"
    if merge_dir.exists():
        shutil.rmtree(merge_dir, ignore_errors=True)
    merge_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--no-checkout", str(repo), str(merge_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        detail = _lf(result.stderr).decode(errors="replace").strip()
        raise PublishError("could not clone merge checkout" + (": " + detail if detail else ""))
    _git(merge_dir, ["config", "user.email", "baseline@invalid"])
    _git(merge_dir, ["config", "user.name", "baseline-publisher"])
    _git(merge_dir, ["fetch", "origin", branch])
    _git(merge_dir, ["checkout", "-q", "-B", "main", "origin/main"])
    fetched_head = _git_text(merge_dir, ["rev-parse", "FETCH_HEAD^{commit}"])
    if fetched_head != expected_head:
        raise PublishError("published branch moved before merge")
    _git(merge_dir, ["merge", "--no-ff", "-m", "merge baseline publication", f"origin/{branch}"])
    _git(merge_dir, ["push", "-q", "origin", "HEAD:refs/heads/main"])
    return _git_text(merge_dir, ["rev-parse", "HEAD"])


def _resolve_gh() -> str:
    found = shutil.which("gh")
    if not found:
        raise PublishError("gh is not on PATH")
    return found


def _gh(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    exe = _resolve_gh()
    cmd = [exe, *args]
    use_shell = os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))
    try:
        if use_shell:
            result = subprocess.run(
                subprocess.list2cmdline(cmd), cwd=str(cwd), shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        else:
            result = subprocess.run(
                cmd, cwd=str(cwd),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
    except OSError as exc:
        raise PublishError(f"gh {' '.join(args)} failed: {exc}") from exc
    return result


def _gh_text(cwd: Path, args: list[str]) -> str:
    result = _gh(cwd, args)
    if result.returncode != 0:
        detail = _lf(result.stderr).decode("utf-8", errors="replace").strip()
        raise PublishError(f"gh {' '.join(args)} failed" + ((": " + detail) if detail else ""))
    return _lf(result.stdout).decode("utf-8", errors="replace").strip()


def _write_pr_body(path: Path, journal_id: str, source: dict, rows: list[str],
                    step_evidence: list[dict]) -> None:
    lines = [
        f"# baseline publish {journal_id}",
        "",
        f"Source: {source['kind']} `{source['repo']}` @ `{source['commit']}`",
        "",
        "## Rows",
        *(f"- {row}" for row in rows),
        "",
        "## Steps 1-5",
        *(f"- {step['name']}: {step['evidence']}" for step in step_evidence),
    ]
    _write_lf(path, "\n".join(lines) + "\n")


def _pinned_has_ci_workflow(baseline_cache: Path, pinned_sha: str) -> bool:
    return _git_show(baseline_cache, pinned_sha, ".github/workflows/baseline.yml") is not None


# GitHub registers a pull request's workflow run a few seconds after `pr create`; until then
# `gh pr checks` exits 1 with "no checks reported". Step 6 polls until checks exist, within a bound.
CHECKS_REGISTER_WAIT_S = 180.0
CHECKS_POLL_S = 5.0
_NO_CHECKS_YET = "no checks reported"


CHECKS_WATCH_RETRIES = 5
_PENDING_CHECK_STATES = {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}


def _checks_pending(worktree: Path, pr_url: str) -> bool:
    """True when a check has no verdict yet, or when the answer itself is unreadable."""
    listed = _gh(worktree, ["pr", "checks", pr_url, "--json", "name,state"])
    if listed.returncode != 0:
        return True
    try:
        checks = json.loads(_lf(listed.stdout).decode("utf-8", errors="replace") or "[]")
    except json.JSONDecodeError:
        return True
    return any(str(check.get("state") or "").upper() in _PENDING_CHECK_STATES for check in checks)


def _watch_checks(worktree: Path, pr_url: str) -> subprocess.CompletedProcess[bytes]:
    """`--watch` exits non-zero on a lost connection as on a red check, so a non-zero watch
    that leaves a check pending has no verdict: watch again, up to CHECKS_WATCH_RETRIES times."""
    deadline = time.monotonic() + CHECKS_REGISTER_WAIT_S
    rewatches = 0
    while True:
        checks = _gh(worktree, ["pr", "checks", pr_url, "--watch", "--fail-fast"])
        if checks.returncode == 0:
            return checks
        text = _lf(checks.stdout + checks.stderr).decode("utf-8", errors="replace")
        if _NO_CHECKS_YET in text:
            if time.monotonic() >= deadline:
                raise PublishError(
                    f"no CI checks registered on {pr_url} within {int(CHECKS_REGISTER_WAIT_S)} s"
                )
        elif rewatches < CHECKS_WATCH_RETRIES and _checks_pending(worktree, pr_url):
            rewatches += 1
        else:
            return checks
        time.sleep(CHECKS_POLL_S)


def _ci_run_url(worktree: Path, pr_url: str) -> str | None:
    """The workflow run behind the PR's checks, green or red: `gh pr checks --json` gives each check's job
    link (`.../actions/runs/<run>/job/<job>`); the run URL is that link without its job segment."""
    listed = _gh(worktree, ["pr", "checks", pr_url, "--json", "name,state,link"])
    if listed.returncode != 0:
        return None
    try:
        checks = json.loads(_lf(listed.stdout).decode("utf-8", errors="replace") or "[]")
    except json.JSONDecodeError:
        return None
    for check in checks:
        link = str(check.get("link") or "")
        if "/actions/runs/" in link:
            return link.split("/job/", 1)[0]
    return None


def _publish(worktree: Path, journal: dict, journal_path: Path, root: Path,
             no_ci: bool, branch: str, journal_id: str) -> str:
    if not journal["worktree"].get("committed"):
        _git(worktree, ["add", "-A"])
        status = _git(worktree, ["status", "--porcelain"])
        if not status.stdout:
            raise PublishError("publication has no changes")
        # A linked worktree shares `.git/config` with the cache clone and every other
        # author worktree off it -- `git config user.*` here would alter their identity.
        # Pass the identity on the commit command instead of writing shared config.
        _git(worktree, ["-c", "user.name=baseline-publisher", "-c", "user.email=baseline@invalid",
                         "commit", "-q", "-m", f"feat(baseline): publish {journal_id}"])
        journal["worktree"]["committed"] = True
    head = _git_text(worktree, ["rev-parse", "HEAD"])
    journal["worktree"]["head"] = head
    _write_json_atomic(journal_path, journal)

    pr_url = journal.get("pr_url")
    if not pr_url:
        _git(worktree, ["push", "-q", "-f", "origin", f"HEAD:refs/heads/{branch}"])
        body_path = _journal_dir(root) / f"{journal_id}.pr.md"
        _write_pr_body(body_path, journal_id, journal["source"], journal["rows_published"],
                       journal["steps"][:5])
        listed = _gh_text(worktree, ["pr", "list", "--head", branch, "--state", "all",
                                      "--json", "url,state"])
        matches = []
        if listed:
            try:
                matches = json.loads(listed)
            except json.JSONDecodeError:
                matches = []
        if matches:
            pr_url = matches[0]["url"]
        else:
            # The branch has no upstream (pushed as HEAD:refs/heads/<branch>), so gh cannot
            # infer the head; name it and the base explicitly.
            created = _gh_text(worktree, ["pr", "create", "--head", branch, "--base", "main",
                                           "--title", f"baseline publish {journal_id}",
                                           "--body-file", str(body_path)])
            candidates = [line.strip() for line in created.splitlines() if line.strip()]
            pr_url = candidates[-1] if candidates else ""
            if not pr_url:
                raise PublishError("gh pr create did not return a PR URL")
        journal["pr_url"] = pr_url
        _write_json_atomic(journal_path, journal)

    view = _gh_text(worktree, ["pr", "view", pr_url, "--json", "state,mergeCommit"])
    info = json.loads(view) if view else {}
    if info.get("state") == "MERGED":
        merge_commit = (info.get("mergeCommit") or {}).get("oid")
        if not merge_commit:
            raise PublishError("merged PR has no recorded merge commit")
        journal["baseline_sha_after"] = merge_commit
        return f"pr {pr_url} already merged as {merge_commit}"

    if not no_ci:
        start = time.monotonic()
        checks = _watch_checks(worktree, pr_url)
        journal["ci_wait_s"] = round(journal.get("ci_wait_s", 0) + (time.monotonic() - start), 3)
        journal["ci_run_url"] = _ci_run_url(worktree, pr_url)
        _write_json_atomic(journal_path, journal)
        if checks.returncode != 0:
            detail = _lf(checks.stdout + checks.stderr).decode("utf-8", errors="replace").strip()
            raise PublishError("gh pr checks failed" + ((": " + detail) if detail else ""))

    _git(worktree, ["fetch", "origin", "main"])
    current_main = _git_text(worktree, ["rev-parse", "origin/main"])
    if current_main != journal["baseline_sha_before"]:
        raise PublishError("baseline moved")

    merged = _gh(worktree, ["pr", "merge", pr_url, "--merge", "--match-head-commit", head])
    if merged.returncode != 0:
        detail = _lf(merged.stdout + merged.stderr).decode("utf-8", errors="replace").strip()
        raise PublishError("gh pr merge failed" + ((": " + detail) if detail else ""))

    view2 = _gh_text(worktree, ["pr", "view", pr_url, "--json", "state,mergeCommit"])
    info2 = json.loads(view2) if view2 else {}
    merge_commit = (info2.get("mergeCommit") or {}).get("oid")
    if not merge_commit:
        raise PublishError("gh pr view did not report a merge commit after merging")
    journal["baseline_sha_after"] = merge_commit
    evidence = f"pushed {head}; pr {pr_url}; merged {merge_commit}"
    if no_ci:
        evidence += "; ci: skipped (no workflow at pinned sha)"
    return evidence


# ---------------------------------------------------------------------------
# Steps 7-8: update-lock, check
# ---------------------------------------------------------------------------

def _update_lock(root: Path, lock: dict, records: list[dict], baseline_repo: Path,
                  baseline_sha_after: str, source: dict) -> str:
    # §5: a `--from-worktree` publication authors upstream first, so the consumer's local
    # copy of an existing tracked row still holds the pre-publication content until the
    # consumer's own `pull`. Only rewrite `hash` there when local content already equals
    # the forward-substituted merged content -- otherwise leave it, so `check` reports the
    # row as needing `pull`. `--from-commit` keeps the old unconditional rewrite: the
    # consumer is the source, so it is already in sync with what it just published.
    from_worktree = source["kind"] == "worktree"
    substitutions = lock.get("substitutions", {})
    updated = 0
    for record in records:
        if record["kind"] != "lock":
            continue  # root files carry no lock row
        relpath = record["relpath"]
        if record["op"] == "D":
            entry = lock.get("files", {}).get(relpath)
            if entry is None:
                continue
            # The consumer's file outlives the upstream copy: it becomes this project's own.
            if (root / relpath).exists():
                if sync._entry_status(entry) != "local":
                    entry["status"] = "local"
                    entry.pop("hash", None)
                    entry.pop("base", None)
                    updated += 1
                continue
            del lock["files"][relpath]
            updated += 1
            continue
        entry = lock.get("files", {}).get(relpath)
        if entry is None:
            continue  # new upstream file — the consumer's own `pull` creates its row (§5)
        content = _git_show(baseline_repo, baseline_sha_after, "template/" + relpath)
        if content is None:
            raise PublishError(f"merged baseline is missing: {relpath}")
        rendered = sync.forward_for(relpath, content.decode("utf-8", errors="replace"), substitutions)
        merged_hash = sync.sha(rendered.encode("utf-8"))
        if from_worktree:
            local = sync.local_text(root, relpath)
            local_hash = sync.sha(local.encode("utf-8")) if local is not None else None
            if local_hash != merged_hash:
                continue  # leave `hash` untouched; the consumer's `pull` catches up (§5)
        entry["hash"] = merged_hash
        # A `forked` row judged `push` is upstream's content once this hash is rewritten, so it
        # is no longer a fork. Keeping the status and its stale `base` makes the next
        # `check --strict` report `forked-upstream-moved` against a baseline that now carries
        # this very content -- a clean publication that reads as drift.
        if sync._entry_status(entry) == "forked":
            entry["status"] = "tracked"
            entry.pop("base", None)
        updated += 1
    lock["synced_commit"] = baseline_sha_after
    sync.save_lock(root, lock)
    return f"lock updated ({updated} row(s))"


def _check(root: Path, lock: dict, records: list[dict], baseline_repo: Path,
           baseline_sha_after: str, source: dict) -> str:
    # §5: for `--from-worktree`, the consumer's local copy is not required to be in sync
    # yet -- step 8 instead verifies the merged tree holds each published path's bytes
    # from the source commit (reproducing the same reverse-substitution `scrub` applied),
    # and names the rows the consumer must pull. `--from-commit` compares the merged row with
    # the source commit's bytes, never the working tree: a peer edit made after the source
    # commit is new local work for `triage`, not a failed publication.
    from_worktree = source["kind"] == "worktree"
    needs_pull: list[str] = []
    for record in records:
        merged_path = record["source_path"] if record["kind"] == "root" else "template/" + record["relpath"]
        content = _git_show(baseline_repo, baseline_sha_after, merged_path)
        if record["op"] == "D":
            if content is not None:
                raise PublishError(f"check failed (still present after deletion): {record['relpath']}")
            continue
        if content is None:
            raise PublishError(f"check missing merged row: {record['relpath']}")
        if record["kind"] == "root":
            continue  # no local counterpart to compare against in a consumer checkout
        if from_worktree:
            source_content = _git_show(Path(source["repo"]), source["commit"], record["source_path"])
            if source_content is None:
                raise PublishError(f"check missing source row: {record['relpath']}")
            expected_merged = sync.reverse_for(
                record["relpath"], source_content.decode("utf-8", errors="replace"), lock.get("substitutions", {})
            ).encode("utf-8")
            if sync.sha(content) != sync.sha(expected_merged):
                raise PublishError(
                    f"check failed (merged tree does not hold source bytes): {record['relpath']}"
                )
            needs_pull.append(record["relpath"])
            continue
        if record["relpath"] not in lock.get("files", {}):
            continue  # new upstream row — no local counterpart until the consumer's own `pull` (§5)
        expected = sync.forward_for(
            record["relpath"], content.decode("utf-8", errors="replace"), lock.get("substitutions", {})
        )
        published = _git_show(Path(source["repo"]), source["commit"], record["source_path"])
        published_text = _lf(published).decode("utf-8", errors="replace") if published is not None else None
        if published_text is None or sync.sha(published_text.encode("utf-8")) != sync.sha(expected.encode("utf-8")):
            raise PublishError(f"check failed: {record['relpath']}")
    if from_worktree and needs_pull:
        return "all published rows verified; pull needed: " + ", ".join(sorted(needs_pull))
    return "all published rows in sync"


# ---------------------------------------------------------------------------
# Repeat rule (§7) and journal discovery
# ---------------------------------------------------------------------------

def _load_journal_file(path: Path) -> dict | None:
    try:
        return json.loads(_lf(path.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _journal_complete(journal: dict) -> bool:
    return all(step.get("status") == "green" for step in journal.get("steps", []))


def _find_matching_journal(root: Path, source_commit: str, collected: list[str],
                            exclude_id: str | None = None) -> dict | None:
    directory = _journal_dir(root)
    if not directory.is_dir():
        return None
    wanted = set(collected)
    best = None
    for path in sorted(directory.glob("*.json")):
        data = _load_journal_file(path)
        if data is None:
            continue
        if exclude_id is not None and data.get("id") == exclude_id:
            continue
        if data.get("source", {}).get("commit") != source_commit:
            continue
        if set(data.get("rows_published", [])) != wanted:
            continue
        if best is None or data.get("id", "") > best.get("id", ""):
            best = data
    return best


def _pin(root: Path, lock: dict) -> tuple[Path, str]:
    source = sync.ensure_baseline(lock, root, None)
    try:
        return source.path, source.sha
    finally:
        source.close()


# ---------------------------------------------------------------------------
# --from-worktree checks (§5)
# ---------------------------------------------------------------------------

def _linked_worktree_paths(cache: Path) -> list[Path]:
    listed = _git_text(cache, ["worktree", "list", "--porcelain"])
    paths = []
    for line in listed.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree "):]).resolve())
    return paths


def _validate_worktree_source(root: Path, cache: Path, worktree: Path, pinned_sha: str) -> None:
    worktrees_root = (root / ".claude" / ".cache" / "baseline-worktrees").resolve()
    try:
        resolved = worktree.resolve()
    except OSError as exc:
        raise PublishError(f"--from-worktree path does not resolve: {exc}") from exc
    try:
        resolved.relative_to(worktrees_root)
    except ValueError:
        raise PublishError(f"--from-worktree must be under {worktrees_root}")
    linked = _linked_worktree_paths(cache)
    if resolved not in linked:
        raise PublishError("--from-worktree is not a linked worktree of the baseline cache clone")
    for other in linked:
        if other != resolved:
            try:
                resolved.relative_to(other)
            except ValueError:
                continue
            raise PublishError("--from-worktree is nested inside another worktree")
    status = _git(resolved, ["status", "--porcelain"])
    if status.stdout.strip():
        raise PublishError("--from-worktree has uncommitted changes")
    ancestor = _git(resolved, ["merge-base", "--is-ancestor", pinned_sha, "HEAD"], check=False)
    if ancestor.returncode != 0:
        raise PublishError("--from-worktree HEAD does not descend from the pinned baseline commit")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _resolve_source(root: Path, source: dict) -> dict:
    """Resolve both fields. An abbreviated commit becomes its full sha, so the journal records an
    unambiguous source that `_resume` and `_find_matching_journal` can each match."""
    repo = Path(source["repo"]).resolve()
    return {"kind": source["kind"], "repo": str(repo),
            "commit": _git_text(repo, ["rev-parse", source["commit"]])}


def _current_source_commit(source: dict) -> str:
    repo = Path(source["repo"])
    if source["kind"] == "worktree":
        return _git_text(repo, ["rev-parse", "HEAD"])
    return _git_text(repo, ["rev-parse", source["commit"]])


def _steps_from_index(journal: dict, journal_path: Path, root: Path, lock: dict,
                       source: dict, records: list[dict], worktree: Path, branch: str,
                       baseline_cache: Path, baseline_before: str, accept_hits: list[str],
                       no_ci: bool, full_battery: bool, dry_run: bool, start: int) -> None:
    if start <= 2:
        materialized_head = _step(journal, journal_path, 2, lambda: _materialize(
            root, source, records, lock, baseline_cache, baseline_before, worktree, branch
        ))
        if materialized_head:
            journal["worktree"]["head"] = materialized_head
            _write_json_atomic(journal_path, journal)
    if start <= 3:
        _step(journal, journal_path, 3, lambda: _scrub(
            root, worktree, records, lock, accept_hits, baseline_cache
        ))
    if start <= 4:
        _step(journal, journal_path, 4, lambda: _validate(worktree, records, full_battery))

    if dry_run:
        return

    _step(journal, journal_path, 5, lambda: _publish(
        worktree, journal, journal_path, root, no_ci, branch, journal["id"]
    ))
    after = journal["baseline_sha_after"]
    _git(baseline_cache, ["fetch", "origin", "main"])
    lock_records = [r for r in records if r["kind"] == "lock"]
    _step(journal, journal_path, 6, lambda: _update_lock(root, lock, lock_records, baseline_cache, after, source))
    _step(journal, journal_path, 7, lambda: _check(root, lock, records, baseline_cache, after, source))
    journal["finished_at"] = _now()
    _write_json_atomic(journal_path, journal)


def _resume(root: Path, resume_id: str, accept_hits: list[str], dry_run: bool = False,
            full_battery: bool = False) -> dict:
    journal_path = _journal_path(root, resume_id)
    journal = _load_journal_file(journal_path)
    if journal is None:
        raise PublishError(f"unreadable publication journal {resume_id}")
    upgraded = bool(full_battery and not journal.get("full_battery"))
    if upgraded and (_journal_complete(journal) or journal["steps"][5].get("status") == "green"):
        raise PublishError(f"{resume_id} is already published; --full-battery upgrades only an "
                           "unpublished journal -- run a fresh `publish --full-battery`")
    if upgraded:
        journal["full_battery"] = True
        for index in range(4, len(journal["steps"])):
            step = journal["steps"][index]
            step.update(status="pending", started_at=None, finished_at=None, evidence="")
        _write_json_atomic(journal_path, journal)
    if _journal_complete(journal):
        print("complete")
        return journal

    source = journal["source"]
    # Resolve BOTH sides. A commit source is immutable, so this only ever refuses a worktree
    # source whose HEAD moved, or a commit that no longer resolves. Comparing a resolved sha
    # against the caller's own unresolved string refused every abbreviated source, and the same
    # recorded string then matched `_find_matching_journal`, so the identical fresh run refused
    # too -- a publication with no open door.
    try:
        recorded = _git_text(Path(source["repo"]), ["rev-parse", source["commit"]])
        current = _current_source_commit(source)
    except PublishError:
        recorded = current = None
    if recorded is None or current != recorded:
        raise PublishError("source commit changed since publish started; run a fresh publish")

    lock = sync.load_lock(root)
    baseline_cache, _current_pinned = _pin(root, lock)
    baseline_before = journal["baseline_sha_before"]

    was_dry_run = bool(journal.get("dry_run"))
    # The owner confirms a GREEN dry run: a dry-run journal red in steps 1-5 is redone as a dry
    # run (`--resume <id> --dry-run`) and shown again before any plain resume may publish it.
    dry_green = all(step.get("status") == "green" for step in journal["steps"][:5])
    if dry_run and not was_dry_run:
        raise PublishError(f"--dry-run resumes only a dry-run journal; {resume_id} is not one")
    if was_dry_run and not dry_green and not dry_run and not upgraded:
        raise PublishError(
            f"dry-run journal {resume_id} is not green through step 5; run "
            f"`publish --resume {resume_id} --dry-run` and confirm its evidence first")
    if was_dry_run and not dry_run:
        journal["dry_run"] = False
        journal["owner_confirmed_at"] = _now()
        _write_json_atomic(journal_path, journal)

    steps = journal["steps"]
    if steps[0].get("status") != "green":
        raise PublishError(f"journal {resume_id} stopped at collect; run a fresh publish")
    # A fresh run with the same inputs refuses in favor of --resume, so a journal that stopped
    # at classify resumes here: re-run the gate, then make the worktree record step 3 needs.
    if steps[1].get("status") != "green":
        _step(journal, journal_path, 1, lambda: _classify(source, lock))
    if not (journal.get("worktree") or {}).get("path"):
        journal["worktree"] = {"path": str(_journal_dir(root) / (journal["id"] + "-worktree")),
                               "branch": "publish/" + journal["id"], "head": None, "committed": False}
        _write_json_atomic(journal_path, journal)

    worktree = Path(journal["worktree"]["path"])
    branch = journal["worktree"]["branch"]
    recorded_head = journal["worktree"].get("head")

    worktree_ok = worktree.exists()
    if worktree_ok:
        actual_head = _git_text(worktree, ["rev-parse", "HEAD"], ) if _git(worktree, ["rev-parse", "HEAD"], check=False).returncode == 0 else None
        worktree_ok = actual_head is not None and actual_head == recorded_head
    if not worktree_ok:
        if worktree.exists():
            _remove_dir(worktree)
        for index in (2, 3, 4):
            steps[index] = {"name": steps[index]["name"], "status": "pending",
                             "started_at": None, "finished_at": None, "evidence": ""}
        journal["worktree"]["committed"] = False
        _write_json_atomic(journal_path, journal)

    records = _records_from_meta(journal["rows_published"], journal.get("row_meta", {}))
    start = next((i for i, s in enumerate(steps) if s.get("status") != "green"), len(steps))
    start = max(start, 0)
    if start < 2:
        start = 2  # collect/classify are re-derived facts, not re-run on resume

    _steps_from_index(journal, journal_path, root, lock, source, records, worktree, branch,
                       baseline_cache, baseline_before, accept_hits,
                       bool(journal.get("no_ci")), bool(journal.get("full_battery")),
                       bool(dry_run), start)
    return journal


def _remove_dir(path: Path) -> None:
    def _chmod_retry(function, target, _exc_info):
        os.chmod(target, 0o700)
        function(target)
    hook = "onexc" if sys.version_info >= (3, 12) else "onerror"
    shutil.rmtree(path, **{hook: _chmod_retry})


def _records_from_meta(rows: list[str], row_meta: dict[str, dict]) -> list[dict]:
    records = []
    for relpath in rows:
        meta = row_meta.get(relpath, {})
        records.append({
            "relpath": relpath,
            "source_path": meta.get("source_path", relpath),
            "dest_path": meta.get("dest_path", "template/" + relpath),
            "kind": meta.get("kind", "lock"),
            "op": meta.get("op", "A"),
        })
    return records


def _make_journal_id(root: Path, commit: str) -> str:
    """`<UTC yyyymmddTHHMMSSZ>-<first 8 hex of commit>` (§6), disambiguated with a `-N`
    suffix on the rare same-second collision (two attempts at the identical source
    commit within one UTC second -- exactly what the §7 repeat rule exists to reconcile,
    but two runs must not clobber the same journal FILE before that rule can compare
    them)."""
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + commit[:8]
    candidate = base
    suffix = 2
    while _journal_path(root, candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _delete_journal(journal_path: Path) -> None:
    try:
        journal_path.unlink()
    except FileNotFoundError:
        pass


def run(root, source: dict | None, rows: list[str] | None, accept_hits: list[str],
        dry_run, no_ci, resume_id, full_battery=False):
    root = Path(root).resolve()
    # Design §8: refuse a missing/malformed `project_subsystems` adaptation contract before
    # step 1 on a fresh run, and before touching an existing journal on `--resume` -- so a
    # refusal here creates no journal and advances no step.
    try:
        sync.check_adaptation_contract(root)
    except sync.BaselineError as exc:
        raise PublishError(str(exc)) from exc
    if resume_id:
        return _resume(root, resume_id, accept_hits, bool(dry_run), bool(full_battery))
    if source is None:
        raise PublishError("a source commit or worktree is required")
    source = _resolve_source(root, source)
    lock = sync.load_lock(root)
    baseline_cache, baseline_before = _pin(root, lock)

    if no_ci and _pinned_has_ci_workflow(baseline_cache, baseline_before):
        raise PublishError("--no-ci requires no baseline workflow at the pinned commit")

    worktree_source = Path(source["repo"]) if source["kind"] == "worktree" else None
    if worktree_source is not None:
        _validate_worktree_source(root, baseline_cache, worktree_source, baseline_before)

    requested = list(rows) if rows is not None else None

    journal_id = _make_journal_id(root, source["commit"])
    journal_path = _journal_path(root, journal_id)
    journal = _new_journal(source, [], {}, bool(dry_run), bool(no_ci), bool(full_battery), baseline_before,
                            journal_id, None)
    _write_json_atomic(journal_path, journal)

    # Step 1 (collect) runs inside the journal from the start: a collect failure (a bad
    # `--rows` entry, an unjudged row) is a real step result, not a pre-journal crash.
    box: list[list[dict]] = []

    def _do_collect() -> str:
        found = _collect(root, source, requested, lock, worktree_source, baseline_before)
        box.append(found)
        return f"{len(found)} row(s): " + ", ".join(r["relpath"] for r in found)

    _step(journal, journal_path, 0, _do_collect)
    records = box[0]
    collected = [record["relpath"] for record in records]
    journal["rows_published"] = collected
    journal["row_meta"] = {record["relpath"]: record for record in records}
    journal["rows_sha256"] = _rows_sha(collected)
    journal["rows_judged_count"] = sum(
        1 for record in records
        if record["kind"] == "lock"
        and (lock.get("files", {}).get(record["relpath"], {}).get("judged") or {}).get("at")
    )
    _write_json_atomic(journal_path, journal)

    # §7 repeat rule, evaluated once collection is known.
    existing = _find_matching_journal(root, source["commit"], collected, exclude_id=journal_id)
    if existing is not None:
        if _journal_complete(existing):
            _delete_journal(journal_path)
            print("already published " + existing["id"])
            return existing
        if existing["baseline_sha_before"] == baseline_before:
            _delete_journal(journal_path)
            raise PublishError(
                f"an incomplete publication with the same inputs exists; use --resume {existing['id']}"
            )
        journal["supersedes"] = existing["id"]
        _write_json_atomic(journal_path, journal)

    _step(journal, journal_path, 1, lambda: _classify(source, lock))

    branch = "publish/" + journal_id
    worktree = _journal_dir(root) / (journal_id + "-worktree")
    journal["worktree"] = {"path": str(worktree), "branch": branch, "head": None, "committed": False}
    _write_json_atomic(journal_path, journal)

    _steps_from_index(journal, journal_path, root, lock, source, records, worktree, branch,
                       baseline_cache, baseline_before, accept_hits, bool(no_ci), bool(full_battery),
                       bool(dry_run), 2)
    return journal


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--from-commit")
    parser.add_argument("--from-worktree")
    parser.add_argument("--rows")
    parser.add_argument("--accept-hit", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-ci", action="store_true")
    parser.add_argument("--full-battery", action="store_true")
    parser.add_argument("--resume")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.resume:
        if args.from_commit or args.from_worktree or args.rows or args.no_ci:
            parser.error("--resume takes its source, rows and --no-ci from the journal")
    elif bool(args.from_commit) == bool(args.from_worktree):
        parser.error("exactly one of --from-commit and --from-worktree is required")
    source = None
    if args.from_commit:
        source = {"kind": "commit", "repo": str(root), "commit": args.from_commit}
    elif args.from_worktree:
        worktree = Path(args.from_worktree).resolve()
        source = {"kind": "worktree", "repo": str(worktree),
                  "commit": _git_text(worktree, ["rev-parse", "HEAD"])}
    selected = None
    if args.rows:
        try:
            selected = json.loads(Path(args.rows).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            parser.error(f"invalid rows file: {exc}")
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            parser.error("rows file must be a JSON list of strings")
        if len(set(selected)) != len(selected):
            parser.error("rows file must be a JSON list of unique relpath strings")
    try:
        journal = run(root, source, selected, args.accept_hit, args.dry_run, args.no_ci,
                      args.resume, args.full_battery)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(journal, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
