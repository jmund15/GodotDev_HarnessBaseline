#!/usr/bin/env python3
"""Re-runnable S1 proofs for the baseline sync object-store and write seams.

    python3 .claude/tests/test_baseline_sync.py
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "tools" / "baseline_sync.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harness_tests import GIT_REPO_ENV  # noqa: E402


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    # A caller committing from a temporary index (GIT_INDEX_FILE) or another repository must not
    # redirect the scratch repositories these proofs build: drop every repo-local git variable.
    for key in GIT_REPO_ENV:
        env.pop(key, None)
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(cwd: Path | None, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_env(cwd or Path.cwd()),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(ENGINE), *args], cwd=root, env=_env(root),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


_SUBSYSTEMS_SKILL_SEED = (
    "# project_subsystems\n\n```yaml\nsubsystems:\n"
    "  - id: fixture-subsystem\n"
    "    paths: [fixture]\n"
    "```\n"
).encode()


def _seed_project_subsystems(root: Path) -> None:
    """A valid `project_subsystems` adaptation contract (Design §8) -- `adaptation.json` at
    its default (an empty JSON object) plus a `SKILL.md` with a parseable `subsystems:` row
    -- so a fixture's `classify`/`ignore`/`compose` call does not trip the new refusal
    incidentally. A caller with its own scenario for these two files writes over this
    afterward, or supplies its own `rows` entry for the exact relpath."""
    _write(root / ".claude" / "skills" / "project_subsystems" / "adaptation.json", b"{}\n")
    _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", _SUBSYSTEMS_SKILL_SEED)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _git(path, "config", "user.name", "fixture")
    _git(path, "branch", "-M", "main")


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD").decode().strip()


def _remote(path: Path) -> Path:
    remote = path / "remote.git"
    _git(path, "init", "--bare", "-q", str(remote))
    return remote


def _push(work: Path, remote: Path) -> None:
    _git(work, "remote", "add", "origin", remote.as_uri())
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")


def _baseline_one(path: Path, rel: str, content: bytes) -> tuple[Path, str, Path]:
    remote = _remote(path)
    work = path / "baseline-work"
    _init_repo(work)
    _write(work / "template" / rel, content)
    commit = _commit(work, "seed")
    _push(work, remote)
    return remote, commit, work


def _baseline_two(path: Path, rel: str, first: bytes, second: bytes) -> tuple[Path, str, str]:
    remote = _remote(path)
    work = path / "baseline-work"
    _init_repo(work)
    _write(work / "template" / rel, first)
    first_commit = _commit(work, "first")
    _push(work, remote)
    _write(work / "template" / rel, second)
    second_commit = _commit(work, "second")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    return remote, first_commit, second_commit


def _clone(remote: Path, destination: Path, shallow: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = remote.as_uri()
    args = ["clone"]
    if shallow:
        args += ["--depth", "1"]
    args += [source, str(destination)]
    _git(destination.parent, *args)
    return destination


def _lock(root: Path, remote: Path, ref: str, rel: str, content: bytes) -> None:
    value = str(remote).replace("\\", "/")
    data = {
        "schema": 2,
        "baseline_repo": value,
        "baseline_ref": ref,
        "synced_commit": ref,
        "profile": "pure,coding,godot",
        "substitutions": {},
        "files": {
            rel: {"status": "tracked", "hash": _sha(content), "layer": "pure"}
        },
    }
    _write(root / ".claude" / "baseline.lock.json", (json.dumps(data, indent=2) + "\n").encode())


def _consumer(path: Path, remote: Path, ref: str, rel: str, content: bytes) -> Path:
    root = path / "consumer"
    _init_repo(root)
    _seed_project_subsystems(root)
    _write(root / rel, content)
    _lock(root, remote, ref, rel, content)
    _commit(root, "consumer")
    return root


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            target = Path(current) / name
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _remove(path: Path) -> None:
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def _fixture():
    path = Path(tempfile.mkdtemp(prefix="baseline_sync_"))
    try:
        yield path
    finally:
        _remove(path)


def _json_output(result: subprocess.CompletedProcess[bytes]) -> dict:
    text = result.stdout.decode("utf-8", errors="replace")
    return json.loads(text)


def _assert_lf(path: Path) -> None:
    if path.is_file():
        assert b"\r" not in path.read_bytes(), f"CRLF in {path.name}"


def _load_engine():
    spec = importlib.util.spec_from_file_location("baseline_sync_under_test", ENGINE)
    assert spec and spec.loader, "baseline sync target could not be loaded"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_object_store_read_ignores_worktree_deletion() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, commit, _ = _baseline_one(path, rel, b"value = 'upstream'\n")
        checkout = _clone(remote, path / "checkout")
        (checkout / "template" / rel).unlink()
        root = _consumer(path, remote, "main", rel, b"value = 'upstream'\n")
        result = _run(root, "check", "--json", "--baseline-dir", str(checkout))
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        data = _json_output(result)
        assert data["baseline_commit"] == commit
        assert data["results"][rel] == "in-sync"


def test_shallow_clone_fetches_pinned_sha() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, first, second = _baseline_two(
            path, rel, b"value = 'first'\n", b"value = 'second'\n"
        )
        root = _consumer(path, remote, first, rel, b"value = 'first'\n")
        cache = root / ".claude" / ".cache" / "baseline-repo"
        _clone(remote, cache, shallow=True)
        assert _git(cache, "rev-parse", "--is-shallow-repository").strip() == b"true"
        result = _run(root, "check", "--json")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        data = _json_output(result)
        assert data["baseline_commit"] == first
        assert data["results"][rel] == "in-sync"
        assert data["baseline_commit"] != second


def test_missing_upstream_exits_nonzero() -> None:
    rel = ".claude/tools/missing.py"
    with _fixture() as path:
        remote, commit, _ = _baseline_one(path, ".claude/tools/other.py", b"other = 1\n")
        checkout = _clone(remote, path / "checkout")
        root = _consumer(path, remote, commit, rel, b"missing = 1\n")
        for operation in (("pull", rel), ("diff", rel), ("update-lock",), ("update-lock", rel)):
            result = _run(root, *operation, "--baseline-dir", str(checkout))
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            assert result.returncode == 1, " ".join(operation) + ": " + output
            assert rel in output, " ".join(operation) + ": " + output


def test_interrupted_lock_write_keeps_old_lock() -> None:
    with _fixture() as path:
        root = path / "consumer"
        (root / ".claude").mkdir(parents=True)
        lock_path = root / ".claude" / "baseline.lock.json"
        before = b'{"old": true}\n'
        lock_path.write_bytes(before)
        module = _load_engine()
        assert hasattr(module, "os"), "atomic lock writer has no os.replace seam"
        original = module.os.replace

        def interrupt(_source, _destination):
            raise OSError("simulated interruption")

        module.os.replace = interrupt
        try:
            try:
                module.save_lock(root, {"new": True})
            except OSError:
                pass
        finally:
            module.os.replace = original
        assert lock_path.read_bytes() == before


def test_v2_init_refuses_lock_created_during_mutex() -> None:
    rel = ".claude/tools/init_race.py"
    content = b"value = 1\n"
    with _fixture() as path:
        remote, _seed_commit, work = _baseline_one(path, rel, content)
        manifest = {"version": 2, "files": [{"path": rel, "layer": "pure", "sync": "auto"}]}
        _write(work / "baseline.manifest.json", (json.dumps(manifest) + "\n").encode())
        _commit(work, "manifest")
        _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
        checkout = _clone(remote, path / "checkout")

        root = path / "init-race-consumer"
        _init_repo(root)
        _write(root / rel, content)

        module = _load_engine()
        init_lock = {"baseline_repo": str(remote), "baseline_ref": "main"}
        source = module.ensure_baseline(init_lock, root, str(checkout))
        lock_path = root / module.LOCK_RELPATH
        assert not lock_path.exists()
        racer_bytes = b'{"schema": 2, "files": {}, "sentinel": "racer"}\n'
        original_lock_mutex = module.lock_mutex

        def racing_lock_mutex(root_arg):
            # Simulate a peer process creating the lock between v2_init's
            # pre-check and its mutex-held write.
            if not lock_path.exists():
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_bytes(racer_bytes)
            return original_lock_mutex(root_arg)

        module.lock_mutex = racing_lock_mutex
        try:
            raised = False
            try:
                module.v2_init(root, str(checkout), str(remote), "main", {}, ["pure", "coding", "godot"], source, False)
            except module.BaselineError as exc:
                raised = True
                assert "already exists" in str(exc) or "read-only" in str(exc), str(exc)
        finally:
            module.lock_mutex = original_lock_mutex
            source.close()
        assert raised, "v2_init did not refuse a lock created inside the mutex window"
        assert lock_path.read_bytes() == racer_bytes, "v2_init clobbered a racer's lock without --force"


def test_in_profile_null_project_and_v1_layer() -> None:
    module = _load_engine()
    v2_layers = ["pure", "coding", "godot"]
    # null is the only layer every profile includes, v1 or v2.
    assert module.in_profile({"layer": None}, ["pure"]) is True
    assert module.in_profile({}, ["pure"]) is True
    # A layer present in the requested profile is always included.
    assert module.in_profile({"layer": "pure"}, v2_layers) is True
    assert module.in_profile({"layer": "pure"}, ["coding"]) is False
    # "project" is a local-row marker, never a wildcard, on the v2 path
    # or the v1 path.
    assert module.in_profile({"layer": "project"}, v2_layers) is False
    assert module.in_profile({"layer": "project"}, v2_layers, legacy_v1=True) is False
    # A v1 lock's pre-v2 layer name (e.g. "universal") is excluded on the
    # default (v2) path, and included only when the caller marks the read
    # as the v1-lock path.
    assert module.in_profile({"layer": "universal"}, v2_layers) is False
    assert module.in_profile({"layer": "universal"}, v2_layers, legacy_v1=True) is True


def test_stdout_bytes_have_no_crlf() -> None:
    rel = ".claude/tools/fixture.py"
    content = b"value = 'one'\n"
    with _fixture() as path:
        remote, commit, work = _baseline_one(path, rel, content)
        manifest = {"version": 2, "files": [{"path": rel, "layer": "pure", "sync": "auto"}]}
        _write(work / "baseline.manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        _commit(work, "manifest")
        _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
        checkout = _clone(remote, path / "checkout")
        root = _consumer(path, remote, "main", rel, content)
        operations = [
            ("check", "--baseline-dir", str(checkout)),
            ("diff", rel, "--baseline-dir", str(checkout)),
            ("pull", rel, "--baseline-dir", str(checkout)),
            ("update-lock", rel, "--baseline-dir", str(checkout)),
            ("candidates", "--baseline-dir", str(checkout)),
            ("paths",),
            ("fork", rel),
            ("pull", rel, "--force", "--baseline-dir", str(checkout)),
            ("track", rel),
            ("ignore", rel, "--force"),
        ]
        for operation in operations:
            result = _run(root, *operation)
            output = result.stdout + result.stderr
            assert b"\r" not in output, f"CRLF from {' '.join(operation)}"
            assert result.returncode == 0, output.decode("utf-8", errors="replace")
            _assert_lf(root / ".claude" / "baseline.lock.json")
        # `checkout/template/<rel>` is no longer written by any op in this list (the
        # retired `materialize` used to write it); nothing here still exercises that path.

        root2 = path / "init-consumer"
        _init_repo(root2)
        _write(root2 / rel, content)
        token = "{{" + "PROJECT_NAME" + "}}"
        result = _run(
            root2, "init", "--baseline-dir", str(checkout), "--repo", remote.as_uri(),
            "--ref", commit, "--sub", token + "=fixture",
        )
        output = result.stdout + result.stderr
        assert b"\r" not in output, "CRLF from init"
        assert result.returncode == 0, output.decode("utf-8", errors="replace")
        _assert_lf(root2 / ".claude" / "baseline.lock.json")


def test_materialize_is_retired() -> None:
    """R4: `materialize` is retired — it exits 2 naming `publish --dry-run`, before any
    lock read (the retirement is intercepted ahead of `project_root()`/`load_lock`)."""
    with _fixture() as path:
        root = path / "consumer"
        root.mkdir(parents=True, exist_ok=True)
        result = _run(root, "materialize", ".claude/tools/x.py")
        output = result.stdout + result.stderr
        assert result.returncode == 2, output.decode("utf-8", errors="replace")
        assert b"publish --dry-run" in result.stderr, output.decode("utf-8", errors="replace")
        assert b"\r" not in output


def test_author_start_cli_creates_reuses_and_refuses() -> None:
    """§5/§7 `author start`, exercised through the real CLI: a missing session id, a
    fresh worktree, reuse by the same session, and refusal for a worktree another
    session's owner file names."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, commit, work = _baseline_one(path, rel, b"value = 'x'\n")
        root = path / "consumer"
        _init_repo(root)
        _write(root / rel, b"value = 'x'\n")
        _lock(root, remote, "main", rel, b"value = 'x'\n")
        _commit(root, "consumer")

        env_no_session = _env(root)
        env_no_session.pop("CLAUDE_CODE_SESSION_ID", None)
        missing = subprocess.run(
            [sys.executable, str(ENGINE), "author", "start"],
            cwd=root, env=env_no_session, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert missing.returncode == 2, missing.stderr.decode("utf-8", errors="replace")

        session = "s5cli001-full-session-id"
        env = _env(root)
        env["CLAUDE_CODE_SESSION_ID"] = session
        first = subprocess.run(
            [sys.executable, str(ENGINE), "author", "start"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert first.returncode == 0, first.stderr.decode("utf-8", errors="replace")
        worktree_path = Path(first.stdout.decode("utf-8", errors="replace").strip())
        assert worktree_path.is_dir(), worktree_path
        assert b"\r" not in first.stdout + first.stderr

        second = subprocess.run(
            [sys.executable, str(ENGINE), "author", "start"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert second.returncode == 0, second.stderr.decode("utf-8", errors="replace")
        assert second.stdout.strip() == first.stdout.strip()

        # a different full session id sharing the same 8-character prefix collides on the
        # same worktree/owner-file path, and must be refused, not silently reused.
        other_env = _env(root)
        other_env["CLAUDE_CODE_SESSION_ID"] = session[:8] + "-a-different-session"
        third = subprocess.run(
            [sys.executable, str(ENGINE), "author", "start"],
            cwd=root, env=other_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert third.returncode == 1, third.stdout.decode("utf-8", errors="replace")


def test_publish_cli_dispatches_to_baseline_publish() -> None:
    with _fixture() as path:
        root = path / "consumer"
        _init_repo(root)
        # A consumer has its own `.claude/`; without it the CLI found whatever `.claude/` sat above
        # the fixture (a Windows home directory has one), so this passed there and failed on Linux.
        (root / ".claude").mkdir()
        result = subprocess.run(
            [sys.executable, str(ENGINE), "publish"],
            cwd=root, env=_env(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        # `baseline_publish`'s own usage error names both source flags -- a shape only its
        # parser produces, proving the CLI actually delegated rather than erroring itself.
        assert result.returncode == 2, output
        assert "--from-commit" in output and "--from-worktree" in output, output


def test_cli_root_stops_at_the_git_top_level() -> None:
    # `project_root` walked up past the repository into any ancestor `.claude/` -- on Windows the
    # home directory has one -- so a command run in a repo without `.claude/` adopted the home
    # directory as the project root. It must stop at the git top level.
    with _fixture() as path:
        outer = path / "outer"
        (outer / ".claude").mkdir(parents=True)
        root = outer / "consumer"
        _init_repo(root)
        result = subprocess.run(
            [sys.executable, str(ENGINE), "check"],
            cwd=root, env=_env(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        assert result.returncode != 0, output
        assert "no .claude/ directory found" in output, output



PROJECT_PLACEHOLDER = "{{" + "PROJECT_NAME" + "}}"  # assembled so the residual-placeholder check never flags this file


def _write_json(path: Path, value: object) -> None:
    _write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def _v2_fixture(path: Path, rows: dict[str, dict], *, manifest: bool = True):
    remote = _remote(path)
    baseline = path / "baseline"
    _init_repo(baseline)
    manifest_rows = []
    for relpath, entry in rows.items():
        if not entry.get("manifest", True):
            continue
        upstream = entry.get("upstream", ("upstream " + relpath + "\n").encode())
        _write(baseline / "template" / relpath, upstream)
        manifest_rows.append({"path": relpath, "layer": entry.get("layer", "pure"),
                              "sync": entry.get("sync", "auto")})
    if manifest:
        _write_json(baseline / "baseline.manifest.json", {"version": 2, "files": manifest_rows})
    baseline_commit = _commit(baseline, "baseline")
    _push(baseline, remote)

    root = path / "consumer"
    _init_repo(root)
    _seed_project_subsystems(root)
    files = {}
    for relpath, entry in rows.items():
        local = entry.get("local", entry.get("upstream", ("upstream " + relpath + "\n").encode()))
        if local is not None:
            _write(root / relpath, local)
        if not entry.get("row", True):
            continue
        files[relpath] = {
            "status": entry.get("status", "tracked"),
            "layer": entry.get("layer", "pure"),
        }
        if "hash" in entry:
            files[relpath]["hash"] = entry["hash"]
        elif entry.get("status", "tracked") == "tracked":
            files[relpath]["hash"] = _sha(entry.get("upstream", ("upstream " + relpath + "\n").encode()))
        if "judged" in entry:
            files[relpath]["judged"] = entry["judged"]
        if "base" in entry:
            files[relpath]["base"] = entry["base"]
        if "inputs" in entry:
            files[relpath]["inputs"] = entry["inputs"]
    lock = {
        "schema": 2,
        "profile": "pure,coding,godot",
        "substitutions": {},
        "baseline_repo": str(remote).replace("\\\\", "/"),
        "baseline_ref": "main",
        "synced_commit": baseline_commit,
        "identity": {"abbreviations": []},
        "files": files,
    }
    _write_json(root / ".claude" / "baseline.lock.json", lock)
    _commit(root, "consumer")
    return root, baseline, remote, baseline_commit


def _downgrade_to_v1(root: Path) -> None:
    old = _load_lock(root)
    old["schema"] = 1
    old.pop("identity", None)
    old["profile"] = "universal,jmodot"
    for entry in old["files"].values():
        entry["layer"] = "universal"
    _write_json(root / ".claude" / "baseline.lock.json", old)


def _run_baseline(root: Path, baseline: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return _run(root, *args, "--baseline-dir", str(baseline))


def _load_lock(root: Path) -> dict:
    return json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))


def _assert_no_change(result: subprocess.CompletedProcess[bytes]) -> None:
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert result.returncode == 0, output
    assert "no change" in output.lower(), output


def test_v2_classify_success_refusal_and_repeat() -> None:
    rel = ".claude/tools/classify.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        first = _run(root, "classify", rel, "--status", "local", "--force")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["status"] == "local"
        repeat = _run(root, "classify", rel, "--status", "local")
        _assert_no_change(repeat)
        refusal = _run(root, "classify", rel, "--status", "tracked")
        assert refusal.returncode == 1, refusal.stdout.decode(errors="replace")
        bad = _run(root, "classify", "tools/not-normalized.py", "--status", "local")
        assert bad.returncode == 2


def test_v2_classify_layer_sets_overrides_from_and_refuses_unknown() -> None:
    # A row new to the baseline has no manifest layer; publish needs one, and --layer records it.
    rel = ".claude/tools/layered.py"
    source = ".claude/tools/layer_source.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}, source: {"upstream": b"value = 2\n"}}
        )
        first = _run(root, "classify", rel, "--status", "local", "--layer", "coding", "--force")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "coding"
        _assert_no_change(_run(root, "classify", rel, "--status", "local", "--layer", "coding"))
        unknown = _run(root, "classify", rel, "--status", "local", "--layer", "universal", "--force")
        assert unknown.returncode == 2, unknown.stdout.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "coding"
        wrong_op = _run(root, "judge", rel, "--verdict", "keep-local", "--layer", "pure")
        assert wrong_op.returncode == 2, wrong_op.stdout.decode(errors="replace")
        seeded = _run(root, "classify", source, "--status", "local", "--layer", "godot", "--force")
        assert seeded.returncode == 0, seeded.stderr.decode(errors="replace")
        copied = _run(root, "classify", rel, "--status", "local", "--from", source)
        assert copied.returncode == 0, copied.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "godot"
        overridden = _run(root, "classify", rel, "--status", "local", "--from", source, "--layer", "pure")
        assert overridden.returncode == 0, overridden.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "pure"


def _run_env(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(ENGINE), *args], cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_v2_classify_tracked_refuses_planted_home_path() -> None:
    """S4 F8: `classify --status tracked` runs `baseline_identity.scan_changed` over the
    whole file as added lines -- a real profile-aware scan, not the retired substring check
    against lock substitutions/abbreviations alone. Every planted fragment is assembled at
    runtime so no committed line here matches the scanner it exercises."""
    rel = ".claude/commands/leaky_home.md"
    planted_user = "cla" + "udeuser"  # >= MIN_HOME_USER_LEN=5, never a literal token here
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(path, {})
        leak = "See " + "C:" + "/Users/" + planted_user + "/work for the fixture.\n"
        _write(root / rel, leak.encode())
        _git(root, "add", "-A")
        env = _env(root)
        env["USERPROFILE"] = "C:\\Users\\" + planted_user
        env.pop("HOME", None)
        result = _run_env(root, env, "classify", rel, "--status", "tracked")
        output = result.stdout + result.stderr
        assert result.returncode == 1, output.decode("utf-8", errors="replace")
        assert b"identity scan hit" in output, output
        assert "files" not in _load_lock(root) or rel not in _load_lock(root)["files"]


def test_v2_classify_tracked_refuses_planted_topology_token() -> None:
    """S4 F8, second profile kind: a compound subsystem id declared in
    `project_subsystems/SKILL.md` is a topology token (Design §3) once it is a compound of
    2+ camel/snake/kebab words -- the retired substring check never looked at this kind at
    all. Built from fragments at runtime, same rationale as the home-path case above."""
    rel = ".claude/commands/leaky_topology.md"
    subsystem_id = "brew" + "Cauldron"  # a compound (2-word) topology candidate
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(path, {})
        skill_md = (
            "# project_subsystems\n\n```yaml\nsubsystems:\n"
            "  - id: " + subsystem_id + "\n"
            "    paths: []\n"
            "```\n"
        )
        _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", skill_md.encode())
        leak = "The " + subsystem_id + " owns this file.\n"
        _write(root / rel, leak.encode())
        _git(root, "add", "-A")
        result = _run(root, "classify", rel, "--status", "tracked")
        output = result.stdout + result.stderr
        assert result.returncode == 1, output.decode("utf-8", errors="replace")
        assert b"identity scan hit" in output, output
        assert "files" not in _load_lock(root) or rel not in _load_lock(root)["files"]


def test_v2_classify_tracked_scans_the_reverse_substituted_text() -> None:
    """`publish` reverse-substitutes a row before its scrub scan, so a lock substitution value
    in a consumer file publishes as its placeholder. `classify --status tracked` scans that same
    text: the value alone is not a hit. The value is assembled at runtime from fragments."""
    rel = ".claude/commands/substituted.md"
    value = "Zorb" + "laxian"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(path, {})
        lock = _load_lock(root)
        lock["substitutions"] = {"{{" + "PROJECT_NAME" + "}}": value}
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _write(root / rel, ("The " + value + " harness runs this.\n").encode())
        _git(root, "add", "-A")
        result = _run(root, "classify", rel, "--status", "tracked")
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        assert result.returncode == 0, output
        assert _load_lock(root)["files"][rel]["status"] == "tracked"


def test_v2_judge_success_refusal_and_repeat() -> None:
    rel = ".claude/tools/judge.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        first = _run(root, "judge", rel, "--verdict", "keep-local")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        judged = _load_lock(root)["files"][rel]["judged"]
        assert judged["verdict"] == "keep-local"
        repeat = _run(root, "judge", rel, "--verdict", "keep-local")
        _assert_no_change(repeat)
        refusal = _run(root, "judge", rel, "--verdict", "push")
        assert refusal.returncode == 1, refusal.stdout.decode(errors="replace")
        bad = _run_baseline(root, baseline, "judge", rel, "--verdict", "fork", "--confirm")
        assert bad.returncode == 1


def test_v2_judge_commit_reads_that_commit_not_the_shared_index() -> None:
    # A peer's staged edit sits in the shared index; `publish --from-commit` compares the
    # judged sha with the commit's blob, so the judgment must read the same blob.
    rel = ".claude/tools/pinned.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        (root / rel).write_bytes(b"value = 2\n")
        pinned = _commit(root, "pinned content")
        (root / rel).write_bytes(b"value = 3\n")
        _git(root, "add", rel)
        result = _run(root, "judge", rel, "--verdict", "push", "--commit", pinned)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["judged"]["sha"] == _sha(b"value = 2\n")
        missing = _run(root, "judge", ".claude/tools/absent.py", "--verdict", "push", "--commit", pinned)
        assert missing.returncode == 1


def test_v2_triage_json_fields_and_truncation() -> None:
    rel = ".claude/tools/triage.py"
    with _fixture() as path:
        upstream = ("\n".join("upstream %03d" % i for i in range(180)) + "\n").encode()
        local = ("\n".join("local %03d" % i for i in range(180)) + "\n").encode()
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": upstream, "local": local}}
        )
        result = _run_baseline(root, baseline, "triage", "--batch", "1", "--json")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        rows = _json_output(result)
        assert len(rows) == 1
        assert set(rows[0]) == {"relpath", "status", "layer", "sha", "prior_verdict", "diff"}
        assert rows[0]["relpath"] == rel
        assert rows[0]["status"] == "tracked"
        assert rows[0]["sha"] == _sha(local)
        assert "... truncated " in rows[0]["diff"].splitlines()[-1]
        repeat = _run_baseline(root, baseline, "triage", "--batch", "1", "--json")
        assert repeat.returncode == 0 and repeat.stdout == result.stdout
        refusal = _run_baseline(root, baseline, "triage", "--batch", "0")
        assert refusal.returncode == 2


def test_v2_forget_success_refusal_and_repeat() -> None:
    rel = ".claude/tools/forget.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        refusal = _run(root, "forget", rel)
        assert refusal.returncode == 1
        first = _run(root, "forget", rel, "--force")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        assert rel not in _load_lock(root)["files"]
        repeat = _run(root, "forget", rel)
        assert repeat.returncode == 0
        assert "no change" in (repeat.stdout + repeat.stderr).decode().lower()


def test_v2_gc_success_refusal_and_repeat() -> None:
    drop_local = ".claude/tools/drop_local.py"
    drop_fork = ".claude/tools/drop_fork.py"
    keep = ".claude/tools/keep.py"
    with _fixture() as path:
        rows = {
            drop_local: {"status": "local", "local": None},
            drop_fork: {"status": "forked", "local": None},
            keep: {"status": "tracked", "local": None},
        }
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
        result = _run(root, "gc")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        output = (result.stdout + result.stderr).decode()
        assert "drop" in output and "keep-tracked" in output
        apply_result = _run(root, "gc", "--apply")
        assert apply_result.returncode == 0, apply_result.stderr.decode(errors="replace")
        lock = _load_lock(root)
        assert drop_local not in lock["files"] and drop_fork not in lock["files"]
        assert keep in lock["files"]
        repeat = _run(root, "gc", "--apply")
        _assert_no_change(repeat)
        corrupt = _load_lock(root)
        corrupt["files"][".claude/tools/bad.py"] = {"status": "unknown", "layer": "pure"}
        _write_json(root / ".claude" / "baseline.lock.json", corrupt)
        refused = _run(root, "gc", "--apply")
        assert refused.returncode == 1


def test_v2_classify_local_declines_an_upstream_only_path() -> None:
    """A consumer that deliberately does not carry an upstream file records that with
    `classify --status local`: the row keeps the path out of `new-upstream`, and `gc` keeps it
    although the local file is absent. A path upstream lacks too is still refused."""
    declined = ".claude/commands/declined.md"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {declined: {"upstream": b"upstream body\n", "local": None}}
        )
        lock = _load_lock(root)
        del lock["files"][declined]
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _commit(root, "no row")
        before = _json_output(_run_baseline(root, baseline, "check", "--json"))
        assert before["results"][declined] == "new-upstream"

        first = _run_baseline(root, baseline, "classify", declined, "--status", "local")
        assert first.returncode == 0, (first.stdout + first.stderr).decode(errors="replace")
        row = _load_lock(root)["files"][declined]
        assert row["status"] == "local" and row["absent"] is True
        assert row["judged"]["verdict"] == "keep-local"
        assert row["judged"]["sha"] == _sha(b"upstream body\n")
        _assert_no_change(_run_baseline(root, baseline, "classify", declined, "--status", "local"))

        strict = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert strict.returncode == 0, strict.stdout.decode(errors="replace")
        assert _json_output(strict)["results"][declined] == "local"

        gc_result = _run(root, "gc", "--apply")
        assert gc_result.returncode == 0, gc_result.stderr.decode(errors="replace")
        assert "keep-declined: " + declined in gc_result.stdout.decode()
        assert declined in _load_lock(root)["files"]

        typo = _run_baseline(root, baseline, "classify", ".claude/commands/nowhere.md", "--status", "local")
        assert typo.returncode == 1, typo.stdout.decode(errors="replace")
        tracked = _run_baseline(root, baseline, "classify", declined, "--status", "tracked", "--force")
        assert tracked.returncode == 1, tracked.stdout.decode(errors="replace")

        adopted = _run_baseline(root, baseline, "pull", declined, "--force")
        assert adopted.returncode == 0, (adopted.stdout + adopted.stderr).decode(errors="replace")
        assert (root / declined).read_bytes() == b"upstream body\n"
        assert "absent" not in _load_lock(root)["files"][declined]


def test_v2_fork_success_and_repeat() -> None:
    rel = ".claude/tools/fork.py"
    with _fixture() as path:
        root, baseline, _remote_path, commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        first = _run_baseline(root, baseline, "fork", rel)
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        entry = _load_lock(root)["files"][rel]
        assert entry["status"] == "forked" and entry["base"] == commit_sha
        assert entry["judged"]["verdict"] == "fork"
        state = _run_baseline(root, baseline, "check", "--json")
        assert _json_output(state)["results"][rel] == "forked"
        repeat = _run_baseline(root, baseline, "fork", rel)
        _assert_no_change(repeat)
        refusal = _run_baseline(root, baseline, "fork", ".claude/tools/not-a-row.py")
        assert refusal.returncode == 1

def test_v2_track_success_refusal_and_repeat() -> None:
    rel = ".claude/tools/track.py"
    local = b"value = 1\n"
    no_hash = ".claude/tools/track_no_hash.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                rel: {"status": "forked", "upstream": local, "local": local, "hash": _sha(local)},
                no_hash: {"status": "forked", "upstream": local, "local": local},
            },
        )
        # The no-hash row's fixture-assigned "hash" must be absent, not merely
        # unused: _v2_fixture only omits it when no explicit "hash" key and
        # status isn't "tracked", which already holds for a bare forked row.
        assert "hash" not in _load_lock(root)["files"][no_hash]
        _write(baseline / "template" / rel, b"new upstream\n")
        _commit(baseline, "advance upstream")
        first = _run_baseline(root, baseline, "track", rel)
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        entry = _load_lock(root)["files"][rel]
        assert entry["status"] == "tracked" and entry["hash"] == _sha(local)
        assert entry["judged"]["sha"] == _sha(local)
        assert entry["judged"]["verdict"] == "push"
        repeat = _run_baseline(root, baseline, "track", rel)
        _assert_no_change(repeat)
        refusal = _run_baseline(root, baseline, "track", ".claude/tools/missing.py")
        assert refusal.returncode == 1
        no_hash_refusal = _run_baseline(root, baseline, "track", no_hash)
        assert no_hash_refusal.returncode == 1
        no_hash_output = (no_hash_refusal.stdout + no_hash_refusal.stderr).decode(errors="replace")
        assert f"pull --force {no_hash}" in no_hash_output, no_hash_output
        assert _load_lock(root)["files"][no_hash]["status"] == "forked"


def test_v2_ignore_alias_success_refusal_and_repeat() -> None:
    rel = ".claude/tools/ignore.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        first = _run(root, "ignore", rel, "--force")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["status"] == "local"
        repeat = _run(root, "ignore", rel)
        _assert_no_change(repeat)
        refusal = _run(root, "ignore", ".claude/tools/unknown.py")
        assert refusal.returncode == 1
        no_relpaths = _run(root, "ignore")
        assert no_relpaths.returncode == 2, (no_relpaths.stdout + no_relpaths.stderr).decode(errors="replace")


def test_v2_check_strict_success_refusal_and_repeat() -> None:
    tracked = ".claude/tools/check.py"
    composed = ".claude/tools/composed.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                tracked: {"upstream": b"value = 1\n"},
                composed: {"status": "composed", "upstream": b"value = 2\n", "inputs": []},
            },
        )
        success = _run_baseline(root, baseline, "check", "--json")
        assert success.returncode == 0
        first = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert first.returncode == 1
        data = _json_output(first)
        assert data["results"][tracked] == "in-sync"
        assert data["results"][composed] == "composed-drift"
        repeat = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert repeat.returncode == 1
        assert repeat.stdout == first.stdout
        refusal = _run_baseline(root, baseline, "check", "--layers", "no-such-layer")
        assert refusal.returncode == 2


def test_v2_check_strict_forked_drift_states() -> None:
    moved = ".claude/tools/forked_moved.py"
    unknown = ".claude/tools/forked_unknown.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                moved: {"status": "forked", "upstream": b"value = 1\n", "base": "0" * 40},
                unknown: {"status": "forked", "upstream": b"value = 2\n", "base": None},
            },
        )
        result = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert result.returncode == 1
        data = _json_output(result)
        assert data["results"][moved] == "forked-upstream-moved"
        assert data["results"][unknown] == "forked-base-unknown"


def test_v2_forked_row_moves_only_when_its_own_upstream_file_changed() -> None:
    """A publish moves the pinned commit for every row. A forked row reports
    `forked-upstream-moved` only when ITS upstream file differs between the fork base and the
    pin; an unrelated upstream change leaves it `forked`."""
    stays = ".claude/tools/forked_stays.py"
    moves = ".claude/tools/forked_moves.py"
    with _fixture() as path:
        root, baseline, _remote_path, first_commit = _v2_fixture(
            path,
            {
                stays: {"status": "forked", "upstream": b"stays = 1\n", "local": b"stays = 'local'\n"},
                moves: {"status": "forked", "upstream": b"moves = 1\n", "local": b"moves = 'local'\n"},
            },
        )
        lock = _load_lock(root)
        for rel in (stays, moves):
            lock["files"][rel]["base"] = first_commit
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _write(baseline / "template" / moves, b"moves = 2\n")
        _commit(baseline, "second: only the moves file changes")
        data = _json_output(_run_baseline(root, baseline, "check", "--json"))
        assert data["results"][stays] == "forked", data["results"]
        assert data["results"][moves] == "forked-upstream-moved", data["results"]


def test_v2_paths_filters_success_refusal_and_repeat() -> None:
    first = ".claude/tools/path_first.py"
    second = ".claude/tools/path_second.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                first: {
                    "upstream": b"one\n",
                    "judged": {"sha": _sha(b"one\n"), "verdict": "push", "at": "2026-09-14T00:00:00Z"},
                },
                second: {"upstream": b"two\n", "status": "local"},
            },
        )
        result = _run(root, "paths", "--status", "tracked", "--verdict", "push", "--judged-since", "2026-09-13T00:00:00Z", "--json")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert _json_output(result) == [first]
        repeat = _run(root, "paths", "--json")
        assert repeat.returncode == 0
        assert _json_output(repeat) == [first, second]
        refusal = _run(root, "paths", "--status", "watch")
        assert refusal.returncode == 2


def test_v2_pull_explicit_states_and_new_hash() -> None:
    existing = ".claude/tools/existing.py"
    new = ".claude/tools/new.py"
    local = ".claude/tools/local_pull.py"
    forked = ".claude/tools/forked_pull.py"
    missing = ".claude/tools/missing_pull.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                existing: {"upstream": b"existing\n"},
                local: {"status": "local", "upstream": b"local upstream\n"},
                forked: {"status": "forked", "upstream": b"fork upstream\n"},
                missing: {"local": None, "upstream": b"missing upstream\n"},
            },
        )
        local_refusal = _run_baseline(root, baseline, "pull", local)
        assert local_refusal.returncode == 1
        local_pull = _run_baseline(root, baseline, "pull", local, "--force")
        assert local_pull.returncode == 0, local_pull.stderr.decode(errors="replace")
        local_entry = _load_lock(root)["files"][local]
        assert local_entry["status"] == "local" and "hash" not in local_entry
        forked_pull = _run_baseline(root, baseline, "pull", forked, "--force")
        assert forked_pull.returncode == 0, forked_pull.stderr.decode(errors="replace")
        forked_entry = _load_lock(root)["files"][forked]
        assert forked_entry["status"] == "forked" and forked_entry["hash"] == _sha(b"fork upstream\n")
        missing_pull = _run_baseline(root, baseline, "pull", missing)
        assert missing_pull.returncode == 0, missing_pull.stderr.decode(errors="replace")
        assert (root / missing).read_bytes() == b"missing upstream\n"
        old_hash = _load_lock(root)["files"][existing]["hash"]
        _write(baseline / "template" / existing, b"existing new\n")
        _commit(baseline, "stale upstream")
        _write(root / existing, b"existing new\n")
        _commit(root, "stale local copy")
        stale = _run_baseline(root, baseline, "pull")
        _assert_no_change(stale)
        assert _load_lock(root)["files"][existing]["hash"] == old_hash
        # Add a new upstream row after the consumer lock was created.
        _write(baseline / "template" / new, b"new\n")
        _commit(baseline, "new upstream")
        first = _run_baseline(root, baseline, "pull", new)
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        entry = _load_lock(root)["files"][new]
        assert entry["status"] == "tracked" and entry["hash"] == _sha(b"new\n")
        assert entry["judged"]["sha"] == _sha(b"new\n")
        repeat = _run_baseline(root, baseline, "pull", new)
        _assert_no_change(repeat)
        (root / existing).write_bytes(b"changed\n")
        refusal = _run_baseline(root, baseline, "pull", existing)
        assert refusal.returncode == 1


def test_v2_migrate_covers_every_status_mapping_source_row() -> None:
    explicit_local = [
        ".claude/skills/game_vision/SKILL.md",
        ".claude/worklog-titles.md",
        ".claude/commands/checklists/known_failure_modes.md",
        ".claude/auto-memory/MEMORY.md",
    ]
    other_watch = [
        ".claude/skills/_brainstorm_shared/common.md",
        ".claude/skills/architecture_brainstorm/SKILL.md",
        ".claude/skills/debugging/SKILL.md",
        ".claude/hooks/plan_memory_reminder.py",
        ".claude/skills/architecture_philosophy/structure_rules.md",
        ".claude/commands/structure_audit.md",
        ".claude/commands/plan_part.md",
        ".claude/commands/agents/structure_audit_agents.md",
        ".claude/commands/session_end.md",
        ".claude/commands/pr_test_checklist.md",
        ".claude/commands/merge_pr.md",
        ".claude/commands/reindex_search.md",
        ".claude/skills/worklog_reference/SKILL.md",
        ".claude/rules/godot_files.md",
        ".claude/commands/audit_test_accessors.md",
        ".claude/commands/sync_subsystems.md",
        ".claude/tools/extract_subagent_tools.py",
        ".claude/commands/sync_baseline.md",
        ".claude/commands/aiworker_write_doc_audit.md",
    ]
    rels = {
        ".claude/tracked.py": {"status": "tracked"},
        ".claude/local.py": {"status": "local"},
        ".claude/forked.py": {"status": "forked"},
        ".claude/CLAUDE.md": {"status": "watch"},
        ".claude/settings.json": {"status": "watch"},
        ".claude/reference/memory_domains.md": {"status": "tracked"},
        **{rel: {"status": "watch"} for rel in explicit_local + other_watch},
        ".claude/missing-watch.md": {"status": "watch", "local": None},
        ".claude/CLAUDE.core.md": {"status": "tracked"},
        ".claude/settings.base.json": {"status": "tracked"},
        ".claude/settings.project.json": {"status": "tracked"},
        ".claude/reference/memory_domains.base.md": {"status": "tracked"},
        ".claude/skills/project_subsystems/adaptation.json": {"status": "tracked"},
    }
    with _fixture() as path:
        root, baseline, remote_path, _commit_sha = _v2_fixture(path, rels)
        _downgrade_to_v1(root)
        before = (root / ".claude" / "baseline.lock.json").read_bytes()
        token = "{{" + "PROJECT_NAME" + "}}"
        init_refusal = _run(
            root, "init", "--baseline-dir", str(baseline), "--repo", remote_path.as_uri(),
            "--sub", token + "=fixture", "--force",
        )
        assert init_refusal.returncode == 1
        assert (root / ".claude" / "baseline.lock.json").read_bytes() == before
        check = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert check.returncode == 1
        assert _json_output(check)["results"][".claude/CLAUDE.md"] == "watch"
        assert (root / ".claude" / "baseline.lock.json").read_bytes() == before
        migrate = _run_baseline(root, baseline, "migrate")
        assert migrate.returncode == 0, migrate.stderr.decode(errors="replace")
        lock = _load_lock(root)
        assert lock["schema"] == 2 and lock["profile"] == "pure,coding,godot"
        assert lock["identity"]["abbreviations"] == []
        assert lock["files"][".claude/local.py"]["judged"]["verdict"] == "keep-local"
        assert lock["files"][".claude/forked.py"]["base"] is None
        assert lock["files"][".claude/settings.json"]["status"] == "composed"
        assert lock["files"][".claude/reference/memory_domains.md"]["status"] == "composed"
        assert lock["files"][".claude/CLAUDE.md"]["status"] == "local"
        assert lock["files"][".claude/missing-watch.md"]["judged"] is None
        assert all(lock["files"][rel]["status"] == "forked" for rel in other_watch)
        assert lock["files"][".claude/CLAUDE.core.md"]["status"] == "tracked"
        repeat_before = (root / ".claude" / "baseline.lock.json").read_bytes()
        repeat = _run_baseline(root, baseline, "migrate")
        _assert_no_change(repeat)
        assert (root / ".claude" / "baseline.lock.json").read_bytes() == repeat_before
        refusal = _run_baseline(root, baseline, "migrate", "--layers", "unknown")
        assert refusal.returncode == 2


def test_v2_migrate_layer_for_rows_absent_from_manifest() -> None:
    forked_watch = ".claude/hooks/plan_memory_reminder.py"
    composed_watch = ".claude/settings.json"
    base_input = ".claude/settings.base.json"
    project_input = ".claude/settings.project.json"
    local_watch = ".claude/worklog-titles.md"
    rels = {
        forked_watch: {"status": "watch"},
        composed_watch: {"status": "watch"},
        base_input: {"status": "tracked"},
        project_input: {"status": "tracked"},
        local_watch: {"status": "watch"},
    }
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rels, manifest=False)
        _downgrade_to_v1(root)
        migrate = _run_baseline(root, baseline, "migrate")
        assert migrate.returncode == 0, migrate.stderr.decode(errors="replace")
        lock = _load_lock(root)
        # A row absent from the manifest gets `layer: null` unless it lands
        # `local`, which gets the `project` local-row marker — never the
        # `local`-forced lookup a `forked`/`composed` row would wrongly
        # inherit from a watch row's migration-input status.
        assert lock["files"][forked_watch]["status"] == "forked"
        assert lock["files"][forked_watch]["layer"] is None
        assert lock["files"][composed_watch]["status"] == "composed"
        assert lock["files"][composed_watch]["layer"] is None
        assert lock["files"][base_input]["status"] == "tracked"
        assert lock["files"][base_input]["layer"] is None
        assert lock["files"][local_watch]["status"] == "local"
        assert lock["files"][local_watch]["layer"] == "project"


def test_v2_concurrent_classify_keeps_both_rows() -> None:
    first = ".claude/tools/concurrent_first.py"
    second = ".claude/tools/concurrent_second.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {first: {"upstream": b"one\n"}, second: {"upstream": b"two\n"}}
        )
        commands = [
            [sys.executable, str(ENGINE), "classify", first, "--status", "local", "--force"],
            [sys.executable, str(ENGINE), "classify", second, "--status", "local", "--force"],
        ]
        processes = [subprocess.Popen(command, cwd=root, env=_env(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for command in commands]
        results = []
        for process in processes:
            out, err = process.communicate()
            results.append((process.returncode, out, err))
        assert all(code == 0 for code, _out, _err in results), results
        lock = _load_lock(root)
        assert lock["files"][first]["status"] == "local"
        assert lock["files"][second]["status"] == "local"


def _adaptation_json_path(root: Path) -> Path:
    return root / ".claude" / "skills" / "project_subsystems" / "adaptation.json"


def _skill_md_path(root: Path) -> Path:
    return root / ".claude" / "skills" / "project_subsystems" / "SKILL.md"


def _adaptation_contract_scenarios(root: Path):
    """Design §8's six ways the `project_subsystems` adaptation contract can be missing or
    malformed, shared between the `classify` and `compose` refusal proofs below: a label, a
    zero-arg corruption that leaves the pair in that one broken state, and a substring the
    refusal message must contain (the file, or the file and the wrong-typed key)."""
    adaptation_path = _adaptation_json_path(root)
    skill_path = _skill_md_path(root)
    return [
        ("adaptation.json missing", lambda: adaptation_path.unlink(), "adaptation.json"),
        ("adaptation.json unparseable", lambda: _write(adaptation_path, b"{not json"), "adaptation.json"),
        ("adaptation.json not a JSON object", lambda: _write(adaptation_path, b"[]\n"), "adaptation.json"),
        ("adaptation.json key wrong-typed",
         lambda: _write_json(adaptation_path, {"tests_root": 1}), "tests_root"),
        ("SKILL.md missing", lambda: skill_path.unlink(), "SKILL.md"),
        ("SKILL.md subsystems block unparseable",
         lambda: _write(skill_path, b"# no yaml block here\n"), "SKILL.md"),
    ]


def test_v2_classify_refuses_malformed_adaptation_contract() -> None:
    """Design §8: `classify` exits 1, naming the file (or the file and key), for each way the
    `project_subsystems` adaptation contract can be missing or malformed, and never writes the
    lock for any of them."""
    rel = ".claude/tools/contract_classify.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        before_lock = (root / ".claude" / "baseline.lock.json").read_bytes()
        for label, corrupt, needle in _adaptation_contract_scenarios(root):
            _seed_project_subsystems(root)  # reset to a valid pair before each scenario
            corrupt()
            result = _run(root, "classify", rel, "--status", "local", "--force")
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            assert result.returncode == 1, f"{label}: {output}"
            assert needle in output, f"{label}: {output}"
            assert (root / ".claude" / "baseline.lock.json").read_bytes() == before_lock, label


def test_v2_compose_refuses_malformed_adaptation_contract() -> None:
    """Design §8: `compose` exits 1 the same way, before writing any composed output --
    `.claude/settings.json` stays at its pre-run (uncomposed) bytes for every scenario."""
    settings_relpath = ".claude/settings.json"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {
                ".claude/settings.base.json": {
                    "status": "tracked",
                    "upstream": b'{"permissions": {"allow": ["A"]}, "hooks": {}}\n',
                },
                ".claude/settings.project.json": {
                    "status": "local",
                    "local": b'{"permissions": {"allow": ["B"]}, "hooks": {}}\n',
                },
                settings_relpath: {
                    "status": "composed",
                    "upstream": b"not composed yet\n",
                    "inputs": [".claude/settings.base.json", ".claude/settings.project.json"],
                },
            },
        )
        before_lock = (root / ".claude" / "baseline.lock.json").read_bytes()
        before_settings = (root / settings_relpath).read_bytes()
        for label, corrupt, needle in _adaptation_contract_scenarios(root):
            _seed_project_subsystems(root)  # reset to a valid pair before each scenario
            corrupt()
            result = _run(root, "compose")
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            assert result.returncode == 1, f"{label}: {output}"
            assert needle in output, f"{label}: {output}"
            assert (root / ".claude" / "baseline.lock.json").read_bytes() == before_lock, label
            assert (root / settings_relpath).read_bytes() == before_settings, label


def test_v2_many_concurrent_classifies_lose_no_row() -> None:
    # The pid-file mutex let a waiter delete a mutex whose creator had not yet written its pid,
    # so two processes held it and one lock write was lost (1 run in 5 on Linux with two
    # processes). Six writers over three rounds must keep every row.
    rels = [".claude/tools/stress_%d.py" % index for index in range(6)]
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": ("u%d\n" % index).encode()} for index, rel in enumerate(rels)}
        )
        for round_index in range(3):
            status = "local" if round_index % 2 == 0 else "forked"
            processes = [
                subprocess.Popen([sys.executable, str(ENGINE), "classify", rel, "--status", status, "--force"],
                                 cwd=root, env=_env(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for rel in rels
            ]
            results = []
            for process in processes:
                out, err = process.communicate()
                results.append((process.returncode, out, err))
            assert all(code == 0 for code, _out, _err in results), results
            lock = _load_lock(root)
            lost = [rel for rel in rels if (lock["files"].get(rel) or {}).get("status") != status]
            assert not lost, "round %d lost rows: %s" % (round_index, lost)


def test_v2_check_strict_git_spawns_do_not_grow_with_rows() -> None:
    """`check --strict` reads every tracked row's content. One `git show` per row, index then HEAD,
    cost 2,300 process spawns and 35 s on a fresh 575-file bootstrap under Windows process creation,
    past the bootstrap proof's timeout under load. The spawn count must not grow with the rows."""
    engine = _load_engine()
    counts = []
    for n in (3, 12):
        with _fixture() as path:
            rows = {".claude/tools/spawn_%d.py" % i: {"upstream": b"value = %d\n" % i} for i in range(n)}
            root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
            spawned = []
            real_popen = engine.subprocess.Popen

            def counting_popen(args, *a, **k):
                if args and args[0] == "git":
                    spawned.append(tuple(args))
                return real_popen(args, *a, **k)

            engine.subprocess.Popen = counting_popen
            try:
                with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()):
                    code = engine.main(["check", "--strict", "--baseline-dir", str(baseline)])
            finally:
                engine.subprocess.Popen = real_popen
            assert code == 0, "fixture rows should check clean, got exit %s" % code
            counts.append(len(spawned))
    assert counts[0] == counts[1], "git spawns grew with rows: 3 rows -> %d, 12 rows -> %d" % tuple(counts)


# v1 `check --json` from engine 008b8c3, recorded on `_golden_fixture` with its baseline commit
# replaced by GOLDEN_COMMIT, so neither a shallow CI checkout nor a consumer copy needs baseline history.
V1_CHECK_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "v1_check_golden.json"
GOLDEN_COMMIT = "<baseline_commit>"


def _golden_fixture(path: Path) -> tuple[Path, Path, str]:
    """A v1 lock of 265 rows (tracked, local, forked, watch) over a pushed fixture baseline."""
    remote = _remote(path)
    baseline = path / "baseline"
    _init_repo(baseline)
    rows = {}
    counts = (("tracked", 100), ("local", 100), ("forked", 40), ("watch", 25))
    for status, count in counts:
        for index in range(count):
            rel = ".claude/tools/golden_%s_%03d.py" % (status, index)
            content = (status + " %03d\n" % index).encode()
            _write(baseline / "template" / rel, content)
            rows[rel] = {"status": status, "hash": _sha(content) if status == "tracked" else "stale", "layer": "pure"}
    baseline_commit = _commit(baseline, "golden baseline")
    _push(baseline, remote)
    root = path / "consumer"
    _init_repo(root)
    for rel in rows:
        index = int(rel.rsplit("_", 1)[1][:3])
        status = rel.rsplit("_", 2)[1]
        _write(root / rel, ("%s %03d\n" % (status, index)).encode())
    # Reuse exact bytes for tracked rows; non-tracked rows are opaque to v1 check.
    for rel, entry in rows.items():
        if entry["status"] == "tracked":
            _write(root / rel, ("tracked %03d\n" % int(rel.rsplit("_", 1)[1][:3])).encode())
    lock = {
        "baseline_repo": str(remote).replace("\\\\", "/"),
        "baseline_ref": "main",
        "synced_commit": baseline_commit,
        "profile": "pure,coding,godot",
        "substitutions": {},
        "files": rows,
    }
    _write_json(root / ".claude" / "baseline.lock.json", lock)
    _commit(root, "golden consumer")
    return root, baseline, baseline_commit


def test_v1_check_json_golden_is_read_only() -> None:
    golden = V1_CHECK_GOLDEN.read_text(encoding="utf-8")
    with _fixture() as path:
        root, baseline, baseline_commit = _golden_fixture(path)
        lock_path = root / ".claude" / "baseline.lock.json"
        before = lock_path.read_bytes()
        current = _run_baseline(root, baseline, "check", "--json")
        assert current.returncode == 0, current.stderr.decode(errors="replace")
        assert _json_output(current) == json.loads(golden.replace(GOLDEN_COMMIT, baseline_commit))
        assert lock_path.read_bytes() == before


def test_v2_pull_keeps_placeholder_ok_files_verbatim() -> None:
    # A file that names the substitution tokens (the engine itself) arrives byte-for-byte: forward
    # substitution rewrote a pulled engine's PROJECT_ROOT lookups into the consumer's own path.
    # A file that uses a token as a value is still substituted.
    root_token = "{{" + "PROJECT_ROOT" + "}}"
    name_token = "{{" + "PROJECT_NAME" + "}}"
    engine = ".claude/tools/baseline_sync.py"
    user = ".claude/hooks/uses_root.py"
    literal = ('if "%s" in subs:\n    name = "%s"\n' % (root_token, name_token)).encode()
    uses = ('REPO = r"%s"\nNAME = "%s"\n' % (root_token, name_token)).encode()
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, {
            engine: {"upstream": literal, "local": b"old engine\n", "hash": _sha(b"old engine\n")},
            user: {"upstream": uses, "local": b"old user\n", "hash": _sha(b"old user\n")},
        })
        lock = _load_lock(root)
        lock["substitutions"] = {root_token: "", name_token: "Game"}
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _commit(root, "consumer substitutions")
        pulled = _run_baseline(root, baseline, "pull", engine, user)
        assert pulled.returncode == 0, pulled.stderr.decode(errors="replace")
        assert (root / engine).read_bytes() == literal, (root / engine).read_bytes()
        used = (root / user).read_bytes()
        assert root_token.encode() not in used and b'NAME = "Game"' in used, used
        check = _run_baseline(root, baseline, "check", "--json")
        assert check.returncode == 0, check.stderr.decode(errors="replace")
        results = _json_output(check)["results"]
        assert results[engine] == "in-sync" and results[user] == "in-sync", results


def test_unimported_layer_files_names_each_overlay_claude_md_never_loads() -> None:
    engine = _load_engine()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".claude").mkdir()
        for name in ("CLAUDE.core.md", "CLAUDE.coding.md", "CLAUDE.godot.md"):
            (root / ".claude" / name).write_text("## x\n", encoding="utf-8")
        claude = root / ".claude" / "CLAUDE.md"
        claude.write_text("@CLAUDE.core.md\n\n# Project\n", encoding="utf-8")
        assert engine.unimported_layer_files(root) == [".claude/CLAUDE.coding.md", ".claude/CLAUDE.godot.md"]
        claude.write_text("@CLAUDE.core.md\n@CLAUDE.coding.md\n@CLAUDE.godot.md\n", encoding="utf-8")
        assert engine.unimported_layer_files(root) == []
        (root / ".claude" / "CLAUDE.godot.md").unlink()
        claude.write_text("@CLAUDE.core.md\n@CLAUDE.coding.md\n", encoding="utf-8")
        assert engine.unimported_layer_files(root) == []


def test_placeholder_ok_files_skip_both_substitution_directions() -> None:
    engine = _load_engine()
    name_token = "{{" + "PROJECT_NAME" + "}}"
    subs = {name_token: "Game"}
    assert engine.forward_for(".claude/tools/baseline_sync.py", name_token, subs) == name_token
    assert engine.forward_for(".claude/hooks/other.py", name_token, subs) == "Game"
    assert engine.reverse_for(".claude/tools/baseline_sync.py", 'label = "Game"\n', subs) == 'label = "Game"\n'
    assert engine.reverse_for(".claude/hooks/other.py", 'label = "Game"\n', subs) == 'label = "%s"\n' % name_token


def test_sub_adds_a_substitution_to_an_existing_lock() -> None:
    """`sub PLACEHOLDER=VALUE` updates only `substitutions`, leaving every judged row intact."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "consumer"
        (root / ".claude").mkdir(parents=True)
        lock = {
            "schema": 2,
            "baseline_repo": "https://example.invalid/baseline.git",
            "baseline_ref": "main",
            "synced_commit": "0" * 40,
            "profile": "pure",
            "substitutions": {PROJECT_PLACEHOLDER: "Consumer"},
            "files": {".claude/tools/kept.py": {"status": "tracked", "layer": "pure",
                                                 "hash": "a" * 64}},
        }
        _write(root / ".claude" / "baseline.lock.json",
               (json.dumps(lock, indent=2) + "\n").encode())

        result = _run(root, "sub", "--sub", "GenericWidget=FixtureWidget")
        assert result.returncode == 0, result.stdout + result.stderr

        after = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert after["substitutions"]["GenericWidget"] == "FixtureWidget", after["substitutions"]
        assert after["substitutions"][PROJECT_PLACEHOLDER] == "Consumer", "existing pair lost"
        assert list(after["files"]) == [".claude/tools/kept.py"], "rows must be untouched"
        assert after["files"][".claude/tools/kept.py"]["hash"] == "a" * 64, "row detail must survive"

        bad = _run(root, "sub", "--sub", "NO_EQUALS_SIGN")
        assert bad.returncode != 0, "a malformed PLACEHOLDER=VALUE must fail"


def test_sub_unset_removes_a_substitution() -> None:
    """`sub --unset PLACEHOLDER` drops a pair. A pair set by mistake corrupts every later pull,
    so removing one has to be a supported operation, not a hand edit of the lock."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "consumer"
        (root / ".claude").mkdir(parents=True)
        lock = {
            "schema": 2,
            "baseline_repo": "https://example.invalid/baseline.git",
            "baseline_ref": "main",
            "synced_commit": "0" * 40,
            "profile": "pure",
            "substitutions": {PROJECT_PLACEHOLDER: "Consumer", "Doomed": "RealName"},
            "files": {".claude/tools/kept.py": {"status": "tracked", "layer": "pure",
                                                 "hash": "a" * 64}},
        }
        _write(root / ".claude" / "baseline.lock.json",
               (json.dumps(lock, indent=2) + "\n").encode())

        result = _run(root, "sub", "--unset", "Doomed")
        assert result.returncode == 0, result.stdout + result.stderr
        after = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert "Doomed" not in after["substitutions"], after["substitutions"]
        assert after["substitutions"][PROJECT_PLACEHOLDER] == "Consumer", "other pairs must survive"
        assert list(after["files"]) == [".claude/tools/kept.py"], "rows must be untouched"

        missing = _run(root, "sub", "--unset", "NeverPresent")
        assert missing.returncode != 0, "unsetting an absent placeholder must fail loudly"


def test_env_strips_every_git_local_env_var() -> None:
    planted = {"GIT_CONFIG_PARAMETERS": "'core.bare=true'", "GIT_COMMON_DIR": "/elsewhere", "GIT_INDEX_FILE": "x"}
    saved = {k: os.environ.get(k) for k in planted}
    os.environ.update(planted)
    try:
        leaked = sorted(set(planted) & set(_env(Path(tempfile.gettempdir()))))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert not leaked, "scratch-repo env kept %s" % leaked


def _out(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def test_pull_new_seed_row_is_local() -> None:
    seed = ".claude/skills/seeded/SKILL.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            seed: {"sync": "seed", "row": False, "local": None, "upstream": b"seed text\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / seed).read_bytes() == b"seed text\n"
        row = _load_lock(root)["files"][seed]
        assert row["status"] == "local", row
        assert row["judged"]["verdict"] == "keep-local", row


def test_pull_keeps_existing_seed_file() -> None:
    seed = ".claude/skills/seeded/SKILL.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            seed: {"sync": "seed", "row": False, "local": b"project text\n", "upstream": b"seed text\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / seed).read_bytes() == b"project text\n"
        assert _load_lock(root)["files"][seed]["status"] == "local"


def test_bare_pull_skips_unrowed_differing_local() -> None:
    clash = ".claude/tools/clash.py"
    fresh = ".claude/tools/fresh.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            clash: {"row": False, "local": b"mine\n", "upstream": b"theirs\n"},
            fresh: {"row": False, "local": None, "upstream": b"fresh\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / clash).read_bytes() == b"mine\n"
        assert clash not in _load_lock(root)["files"]
        assert "local file differs, no lock row" in _out(pull) and clash in _out(pull), _out(pull)
        assert (root / fresh).read_bytes() == b"fresh\n"


def test_explicit_pull_refuses_unrowed_differing_local() -> None:
    clash = ".claude/tools/clash.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            clash: {"row": False, "local": b"mine\n", "upstream": b"theirs\n"},
        })
        refusal = _run_baseline(root, baseline, "pull", clash)
        assert refusal.returncode == 1, _out(refusal)
        assert (root / clash).read_bytes() == b"mine\n"
        forced = _run_baseline(root, baseline, "pull", clash, "--force")
        assert forced.returncode == 0, _out(forced)
        assert (root / clash).read_bytes() == b"theirs\n"


def test_migrate_core_row_absent_until_pulled() -> None:
    core = ".claude/CLAUDE.core.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            core: {"row": False, "local": None, "upstream": b"core doctrine\n"},
        })
        _downgrade_to_v1(root)
        _commit(root, "v1")
        migrate = _run_baseline(root, baseline, "migrate")
        assert migrate.returncode == 0, _out(migrate)
        assert core not in _load_lock(root)["files"]
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        check = _run_baseline(root, baseline, "check", "--json")
        assert _json_output(check)["results"][core] == "in-sync"


_REGION = (
    "# CLAUDE.md\n\n"
    "<!-- ===== BASELINE:core BEGIN =====\nsynced region\n================================== -->\n"
    "old core doctrine\n"
    "<!-- ===== BASELINE:core END ===== -->\n\n"
    "## PROJECT\nproject text\n"
)


def _v1_consumer(path: Path, *, region: bool = True, extra_permissions: dict | None = None,
                 skill: bytes | None = None, settings_text: bytes | None = None):
    base_settings = {
        "env": {"PYTHONUTF8": "1"},
        "permissions": {"allow": ["Bash(git:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/dispatch.py\""}]}]},
    }
    old_settings = {
        "env": {"MY_VAR": "x"},
        "permissions": {"allow": ["Bash(git:*)", "Bash(mytool:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python .claude/hooks/absorbed.py"},
            {"type": "command", "command": "python .claude/hooks/optin.py"},
            {"type": "command", "command": "python .claude/hooks/mine.py"}]}]},
    }
    old_settings["permissions"].update(extra_permissions or {})
    rows = {
        ".claude/CLAUDE.md": {"status": "watch", "sync": "seed",
                              "local": (_REGION if region else "# CLAUDE.md\nproject only\n").encode()},
        ".claude/settings.json": {"status": "watch", "manifest": False,
                                  "local": (json.dumps(old_settings, indent=2) + "\n").encode()},
        ".claude/hooks/dispatch.py": {"row": False, "local": None, "upstream": b"import absorbed\n"},
        ".claude/hooks/absorbed.py": {"status": "tracked"},
        ".claude/hooks/optin.py": {"status": "tracked"},
        ".claude/hooks/mine.py": {"status": "local", "manifest": False, "local": b"# mine\n"},
        ".claude/CLAUDE.core.md": {"row": False, "local": None},
        ".claude/CLAUDE.coding.md": {"row": False, "local": None, "layer": "coding"},
        ".claude/CLAUDE.godot.md": {"row": False, "local": None, "layer": "godot"},
        ".claude/settings.base.json": {"row": False, "local": None,
                                       "upstream": (json.dumps(base_settings, indent=2) + "\n").encode()},
        ".claude/settings.project.json": {"row": False, "local": None, "sync": "seed",
                                          "upstream": b"{}\n"},
        ".claude/auto-memory/foreign_memory.md": {"row": False, "local": None, "sync": "offer"},
        ".claude/offered/elsewhere.md": {"row": False, "local": None, "sync": "offer"},
    }
    if settings_text is not None:
        rows[".claude/settings.json"]["local"] = settings_text
    root, baseline, _remote, _commit_sha = _v2_fixture(path, rows)
    if skill is not None:
        _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", skill)
    _downgrade_to_v1(root)
    _commit(root, "v1 consumer")
    return root, baseline


def test_migrate_replaces_core_region_with_imports() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        migrate = _run_baseline(root, baseline, "migrate")
        assert migrate.returncode == 0, _out(migrate)
        text = (root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "BASELINE:core" not in text and "old core doctrine" not in text, text
        assert "@CLAUDE.core.md\n@CLAUDE.coding.md\n@CLAUDE.godot.md\n" in text, text
        assert "## PROJECT\nproject text\n" in text, text


def test_migrate_leaves_regionless_claude_md() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path, region=False)
        migrate = _run_baseline(root, baseline, "migrate")
        assert migrate.returncode == 0, _out(migrate)
        assert (root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == "# CLAUDE.md\nproject only\n"


def test_upgrade_v1_fixture_leaves_only_judgment_rows() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        output = _out(result)
        lock = _load_lock(root)
        assert lock["schema"] == 2, output
        assert (root / ".claude" / "CLAUDE.core.md").exists(), output
        assert lock["files"][".claude/settings.json"]["status"] == "composed", output
        assert lock["files"][".claude/settings.project.json"]["status"] == "local", output
        settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for g in settings["hooks"]["PreToolUse"] for h in g["hooks"]]
        assert any("dispatch.py" in c for c in commands), commands
        assert any("mine.py" in c for c in commands), commands
        assert not any("absorbed.py" in c for c in commands), commands
        assert any("optin.py" in c for c in commands), commands
        assert "adopted from settings.base.json" in output, output
        assert "hook PreToolUse" in output and "dispatch.py" in output, output
        assert "Bash(git:*)" not in output, output
        assert "Bash(mytool:*)" in settings["permissions"]["allow"], settings
        assert settings["env"]["MY_VAR"] == "x" and settings["env"]["PYTHONUTF8"] == "1", settings
        text = (root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.godot.md" in text, text
        assert ".claude/auto-memory/foreign_memory.md" not in lock["files"], output
        assert not (root / ".claude" / "auto-memory" / "foreign_memory.md").exists(), output
        check = _run_baseline(root, baseline, "check", "--json")
        states = _json_output(check)["results"]
        assert set(states.values()) <= {"in-sync", "local", "composed", "offered"}, states
        assert states[".claude/auto-memory/foreign_memory.md"] == "offered", states
        assert states[".claude/offered/elsewhere.md"] == "offered", states
        assert not (root / ".claude" / "offered" / "elsewhere.md").exists(), output
        assert result.returncode == 0, output
        assert "env PYTHONUTF8" in output, output


def test_upgrade_refuses_v2_lock() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {".claude/tools/a.py": {}})
        before = (root / ".claude" / "baseline.lock.json").read_bytes()
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        assert result.returncode != 0, _out(result)
        assert "already schema 2" in _out(result), _out(result)
        assert (root / ".claude" / "baseline.lock.json").read_bytes() == before


def test_upgrade_refuses_dirty_claude_md() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        claude = root / ".claude" / "CLAUDE.md"
        claude.write_text(claude.read_text(encoding="utf-8") + "uncommitted\n", encoding="utf-8")
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        assert result.returncode != 0, _out(result)
        assert "uncommitted" in _out(result) and "CLAUDE.md" in _out(result), _out(result)
        assert _load_lock(root)["schema"] == 1


def test_upgrade_records_abbreviations() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline),
                      "--abbrev", "ZZ")
        assert _load_lock(root)["identity"]["abbreviations"] == ["ZZ"], _out(result)


def test_upgrade_refuses_dirty_lock() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        lock = root / ".claude" / "baseline.lock.json"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        assert result.returncode != 0 and "baseline.lock.json" in _out(result), _out(result)
        assert _load_lock(root)["schema"] == 1


def test_migrate_refuses_dirty_claude_md() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path)
        claude = root / ".claude" / "CLAUDE.md"
        before = claude.read_text(encoding="utf-8") + "uncommitted\n"
        claude.write_text(before, encoding="utf-8")
        result = _run_baseline(root, baseline, "migrate")
        assert result.returncode != 0 and "uncommitted" in _out(result), _out(result)
        assert claude.read_text(encoding="utf-8") == before
        assert _load_lock(root)["schema"] == 1


def test_upgrade_refuses_bad_subsystems_before_writing() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path, skill=b"# project_subsystems\nno registry here\n")
        claude_before = (root / ".claude" / "CLAUDE.md").read_bytes()
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        assert result.returncode != 0 and "subsystems" in _out(result), _out(result)
        assert _load_lock(root)["schema"] == 1
        assert (root / ".claude" / "CLAUDE.md").read_bytes() == claude_before


def test_upgrade_names_unparseable_settings() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path, settings_text=b"{ not json\n")
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        output = _out(result)
        assert result.returncode == 1, output
        assert "Traceback" not in output and "settings.json" in output, output
        assert _load_lock(root)["schema"] == 1


def test_pull_seed_project_settings_makes_composed_row() -> None:
    base = (json.dumps({"permissions": {"allow": ["A"]}}, indent=2) + "\n").encode()
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            ".claude/settings.base.json": {"row": False, "local": None, "upstream": base},
            ".claude/settings.project.json": {"row": False, "local": None, "sync": "seed",
                                              "upstream": b"{}\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        files = _load_lock(root)["files"]
        assert files[".claude/settings.json"]["status"] == "composed", files
        assert files[".claude/settings.project.json"]["status"] == "local", files


def test_pull_keeps_existing_seed_file_says_kept() -> None:
    seed = ".claude/skills/seeded/SKILL.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            seed: {"sync": "seed", "row": False, "local": b"project text\n", "upstream": b"seed text\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert f"kept (seed, local row added): {seed}" in _out(pull), _out(pull)
        assert f"pulled: {seed}" not in _out(pull), _out(pull)


OFFER = ".claude/auto-memory/foreign_memory.md"


def test_bare_pull_skips_unrowed_offer_and_counts_it() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            OFFER: {"sync": "offer", "row": False, "local": None},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert not (root / OFFER).exists(), _out(pull)
        assert OFFER not in _load_lock(root)["files"], _out(pull)
        assert "offered: 1 file(s)" in _out(pull), _out(pull)


def test_named_pull_adopts_offer_as_tracked() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            OFFER: {"sync": "offer", "row": False, "local": None, "upstream": b"memory\n"},
        })
        pull = _run_baseline(root, baseline, "pull", OFFER)
        assert pull.returncode == 0, _out(pull)
        assert (root / OFFER).read_bytes() == b"memory\n"
        assert _load_lock(root)["files"][OFFER]["status"] == "tracked"


def test_bare_pull_updates_rowed_offer() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            OFFER: {"sync": "offer", "local": b"old\n", "hash": _sha(b"old\n"), "upstream": b"new\n"},
        })
        before = _json_output(_run_baseline(root, baseline, "check", "--json"))["results"]
        assert before[OFFER] == "upstream-updated", before
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / OFFER).read_bytes() == b"new\n"


def test_check_reports_offered_and_strict_passes() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            OFFER: {"sync": "offer", "row": False, "local": None},
        })
        check = _run_baseline(root, baseline, "check")
        assert "offered: 1 file(s)" in _out(check), _out(check)
        assert OFFER not in _out(check), _out(check)
        states = _json_output(_run_baseline(root, baseline, "check", "--json"))["results"]
        assert states[OFFER] == "offered", states
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert strict.returncode == 0, _out(strict)


def main() -> int:
    cases = [
        test_bare_pull_skips_unrowed_offer_and_counts_it,
        test_named_pull_adopts_offer_as_tracked,
        test_bare_pull_updates_rowed_offer,
        test_check_reports_offered_and_strict_passes,
        test_unimported_layer_files_names_each_overlay_claude_md_never_loads,
        test_env_strips_every_git_local_env_var,
        test_sub_unset_removes_a_substitution,
        test_sub_adds_a_substitution_to_an_existing_lock,
        test_object_store_read_ignores_worktree_deletion,
        test_shallow_clone_fetches_pinned_sha,
        test_missing_upstream_exits_nonzero,
        test_interrupted_lock_write_keeps_old_lock,
        test_v2_init_refuses_lock_created_during_mutex,
        test_in_profile_null_project_and_v1_layer,
        test_stdout_bytes_have_no_crlf,
        test_materialize_is_retired,
        test_author_start_cli_creates_reuses_and_refuses,
        test_publish_cli_dispatches_to_baseline_publish,
        test_cli_root_stops_at_the_git_top_level,
        test_v2_classify_success_refusal_and_repeat,
        test_v2_classify_layer_sets_overrides_from_and_refuses_unknown,
        test_v2_classify_tracked_refuses_planted_home_path,
        test_v2_classify_tracked_refuses_planted_topology_token,
        test_v2_classify_tracked_scans_the_reverse_substituted_text,
        test_v2_judge_success_refusal_and_repeat,
        test_v2_judge_commit_reads_that_commit_not_the_shared_index,
        test_v2_triage_json_fields_and_truncation,
        test_v2_forget_success_refusal_and_repeat,
        test_v2_gc_success_refusal_and_repeat,
        test_v2_classify_local_declines_an_upstream_only_path,
        test_v2_fork_success_and_repeat,
        test_v2_track_success_refusal_and_repeat,
        test_v2_ignore_alias_success_refusal_and_repeat,
        test_v2_check_strict_success_refusal_and_repeat,
        test_v2_check_strict_forked_drift_states,
        test_v2_check_strict_git_spawns_do_not_grow_with_rows,
        test_v2_forked_row_moves_only_when_its_own_upstream_file_changed,
        test_v2_paths_filters_success_refusal_and_repeat,
        test_v2_pull_explicit_states_and_new_hash,
        test_v2_pull_keeps_placeholder_ok_files_verbatim,
        test_placeholder_ok_files_skip_both_substitution_directions,
        test_v2_migrate_covers_every_status_mapping_source_row,
        test_v2_migrate_layer_for_rows_absent_from_manifest,
        test_v2_concurrent_classify_keeps_both_rows,
        test_v2_classify_refuses_malformed_adaptation_contract,
        test_v2_compose_refuses_malformed_adaptation_contract,
        test_v2_many_concurrent_classifies_lose_no_row,
        test_v1_check_json_golden_is_read_only,
        test_pull_new_seed_row_is_local,
        test_pull_keeps_existing_seed_file,
        test_bare_pull_skips_unrowed_differing_local,
        test_explicit_pull_refuses_unrowed_differing_local,
        test_migrate_core_row_absent_until_pulled,
        test_migrate_replaces_core_region_with_imports,
        test_migrate_leaves_regionless_claude_md,
        test_upgrade_v1_fixture_leaves_only_judgment_rows,
        test_upgrade_refuses_v2_lock,
        test_upgrade_refuses_dirty_claude_md,
        test_upgrade_records_abbreviations,
        test_upgrade_refuses_dirty_lock,
        test_migrate_refuses_dirty_claude_md,
        test_upgrade_refuses_bad_subsystems_before_writing,
        test_upgrade_names_unparseable_settings,
        test_pull_seed_project_settings_makes_composed_row,
        test_pull_keeps_existing_seed_file_says_kept,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
