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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _case_pool import run_cases  # noqa: E402


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
    _git(path, "init", "-q", "-b", "main")
    with open(path / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        handle.write("[user]\n\temail = fixture@example.invalid\n\tname = fixture\n")


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
        for operation in (("diff", rel), ("update-lock",), ("update-lock", rel)):
            result = _run(root, *operation, "--baseline-dir", str(checkout))
            output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            assert result.returncode == 1, " ".join(operation) + ": " + output
            assert rel in output, " ".join(operation) + ": " + output
        # An unmodified tracked row whose upstream file is gone is retired by pull, not refused.
        pull = _run(root, "pull", rel, "--baseline-dir", str(checkout))
        assert pull.returncode == 0, _out(pull)
        assert not (root / rel).exists() and rel not in _load_lock(root)["files"]


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
    # identity's template-token read lists `template/`; a baseline without one has nothing to list.
    _write(baseline / "template" / ".gitkeep", b"")
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


def test_intent_to_add_path_reads_its_working_bytes() -> None:
    # `git add -N` puts the empty blob in the index; classify then judged a new file as empty content.
    engine = _load_engine()
    rel = ".claude/hooks/_new_helper.py"
    with _fixture() as path:
        root = Path(path) / "repo"
        (root / ".claude" / "hooks").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / rel).write_bytes(b"VALUE = 1\n")
        subprocess.run(["git", "-C", str(root), "add", "-N", rel], check=True)
        assert engine._git_contents(root, [rel]) == {rel: b"VALUE = 1\n"}, engine._git_contents(root, [rel])


def test_many_paths_read_without_one_oversized_command_line() -> None:
    # The intent-to-add probe once named every path on the git command line; Windows refuses a line past
    # 32,767 characters (WinError 206), which aborted `check` on the real lock's tracked-file count.
    engine = _load_engine()
    with _fixture() as path:
        root = Path(path) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        rels = [f".claude/tools/{'long_name_' * 6}{i:04d}.py" for i in range(700)]
        (root / ".claude" / "tools").mkdir(parents=True)
        for rel in rels:
            (root / rel).write_bytes(b"VALUE = 1\n")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        got = engine._git_contents(root, rels)
        assert len(got) == len(rels) and set(got.values()) == {b"VALUE = 1\n"}, len(got)


def test_relpaths_after_an_option_parse_on_every_python() -> None:
    # Python before 3.12.7 lets the empty `relpaths` match at `op` when an option follows it, so a
    # trailing relpath is `unrecognized arguments`; Ubuntu 24.04 ships 3.12.3.
    rel = ".claude/tools/classify.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}}
        )
        result = _run(root, "classify", "--status", "local", "--force", rel)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["status"] == "local"


def test_v2_classify_layer_sets_overrides_from_and_refuses_unknown() -> None:
    # A row new to the baseline has no manifest layer; publish needs one, and --layer records it.
    rel = ".claude/tools/layered.py"
    source = ".claude/tools/layer_source.py"
    with _fixture() as path:
        root, _baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {rel: {"upstream": b"value = 1\n"}, source: {"upstream": b"value = 2\n"}}
        )
        first = _run(root, "classify", rel, "--status", "local", "--layer", "coding", "--force", "--relayer")
        assert first.returncode == 0, first.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "coding"
        _assert_no_change(_run(root, "classify", rel, "--status", "local", "--layer", "coding"))
        unknown = _run(root, "classify", rel, "--status", "local", "--layer", "universal", "--force")
        assert unknown.returncode == 2, unknown.stdout.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "coding"
        wrong_op = _run(root, "judge", rel, "--verdict", "keep-local", "--layer", "pure")
        assert wrong_op.returncode == 2, wrong_op.stdout.decode(errors="replace")
        seeded = _run(root, "classify", source, "--status", "local", "--layer", "godot", "--force", "--relayer")
        assert seeded.returncode == 0, seeded.stderr.decode(errors="replace")
        copied = _run(root, "classify", rel, "--status", "local", "--from", source)
        assert copied.returncode == 0, copied.stderr.decode(errors="replace")
        assert _load_lock(root)["files"][rel]["layer"] == "godot"
        overridden = _run(root, "classify", rel, "--status", "local", "--from", source, "--layer", "pure", "--relayer")
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


def test_push_refuses_a_literal_placeholder_a_pull_would_render() -> None:
    rel = ".claude/tests/literal_placeholder.py"
    token = "{{" + "PROJECT_NAME" + "}}"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"x = 1\n"}})
        lock = _load_lock(root)
        lock["substitutions"] = {token: "Zorb" + "laxian"}
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _write(root / rel, ("name = '" + token + "'\n").encode())
        _git(root, "add", "-A")
        refused = _run_baseline(root, baseline, "judge", rel, "--verdict", "push")
        assert refused.returncode == 1 and token in _out(refused), _out(refused)
        _write(root / rel, b"name = '{{' + 'PROJECT_NAME' + '}}'\n")
        _git(root, "add", "-A")
        built = _run_baseline(root, baseline, "judge", rel, "--verdict", "push")
        assert built.returncode == 0, _out(built)


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


def _proof_rows(*specs: tuple[str, str, bytes]) -> dict[str, dict]:
    return {rel: {"status": status, "upstream": body, "manifest": status != "local"} for rel, status, body in specs}


def test_check_strict_lists_each_tool_without_a_shipped_proof() -> None:
    bare, mention, named = ".claude/tools/bare.py", ".claude/tools/mention.py", ".claude/tools/named.py"
    rows = _proof_rows(
        (bare, "tracked", b"x = 1\n"),
        (mention, "tracked", b"x = 2\n"),
        (named, "tracked", b"x = 3\n"),
        (".claude/tests/test_unrelated.py", "tracked", b"# exercises mention.py\n"),
        (".claude/tests/test_named.py", "tracked", b"pass\n"),
    )
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
        result = _run_baseline(root, baseline, "check", "--strict")
        text = _out(result)
        assert result.returncode == 1, text
        assert "unproven: " + bare in text, text
        assert "unproven: " + mention in text, text
        assert "unproven: " + named not in text, text
        assert "unproven: .claude/tests/" not in text, text


def test_check_strict_counts_a_local_proof_as_no_proof() -> None:
    tool = ".claude/tools/lonely.py"
    rows = _proof_rows((tool, "tracked", b"x = 1\n"), (".claude/tests/test_lonely.py", "local", b"pass\n"))
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
        assert "unproven: " + tool in _out(_run_baseline(root, baseline, "check", "--strict"))


def test_check_strict_counts_a_proof_above_its_tools_layer_as_no_proof() -> None:
    """A pure consumer receives the pure tool and not its godot proof, so it reads the tool unproven;
    a checkout holding every layer must report that gap too."""
    high, level = ".claude/tools/high.py", ".claude/tools/level.py"
    rows = {
        high: {"upstream": b"x = 1\n", "layer": "pure"},
        ".claude/tests/test_high.py": {"upstream": b"pass\n", "layer": "godot"},
        level: {"upstream": b"x = 2\n", "layer": "coding"},
        ".claude/tests/test_level.py": {"upstream": b"pass\n", "layer": "coding"},
    }
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
        text = _out(_run_baseline(root, baseline, "check", "--strict"))
        assert "unproven: " + high in text, text
        assert "unproven: " + level not in text, text


def test_check_strict_names_a_shipped_row_whose_layer_differs_from_the_manifest() -> None:
    """A pull never rewrites an existing row's layer, so an upstream relayer leaves the lock stale; the
    layer-closure and unproven checks then read the stale layer."""
    drifted, kept = ".claude/commands/drifted.md", ".claude/commands/kept.md"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {drifted: {"layer": "pure"}, kept: {"layer": "pure"}})
        lock = _load_lock(root)
        lock["files"][drifted]["layer"] = "godot"
        (root / ".claude" / "baseline.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        result = _run_baseline(root, baseline, "check", "--strict")
        text = _out(result)
        assert result.returncode == 1, text
        assert "layer-drift: " + drifted + " (lock godot, manifest pure)" in text, text
        assert kept not in text, text


def test_check_text_names_every_strict_finding() -> None:
    """A strict finding the state buckets do not list (a row needing judgment) still prints its path."""
    rel = ".claude/commands/judge_me.md"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, {rel: {"upstream": b"one\n"}})
        (root / rel).write_bytes(b"two\n")
        _commit(root, "local edit")
        (root / rel).write_bytes(b"one\n")  # the working file matches upstream; the committed bytes differ
        text = _out(_run_baseline(root, baseline, "check", "--strict"))
        assert "needs-judgment: " + rel in text, text


def _new_tool_fixture(path: Path, proof_status: str):
    tool, proof = ".claude/tools/fresh.py", ".claude/tests/test_fresh.py"
    rows = _proof_rows((tool, "local", b"x = 1\n"), (proof, proof_status, b"pass\n"))
    root, baseline, _remote_path, _commit_sha = _v2_fixture(path, rows)
    return root, tool, proof


def test_judge_push_refuses_a_new_tool_whose_proof_is_local() -> None:
    with _fixture() as path:
        root, tool, _proof = _new_tool_fixture(path, "local")
        result = _run(root, "judge", tool, "--verdict", "push")
        assert result.returncode == 1, _out(result)
        assert "shipped proof" in _out(result), _out(result)
        assert "judged" not in _load_lock(root)["files"][tool]
        none = _run(root, "judge", tool, "--verdict", "keep-local")
        assert none.returncode == 0, _out(none)


def test_judge_push_allows_a_new_tool_with_a_shipped_proof() -> None:
    with _fixture() as path:
        root, tool, _proof = _new_tool_fixture(path, "tracked")
        result = _run(root, "judge", tool, "--verdict", "push")
        assert result.returncode == 0, _out(result)
        assert _load_lock(root)["files"][tool]["judged"]["verdict"] == "push"


def test_judge_push_allows_a_new_tool_and_its_new_proof_in_one_range() -> None:
    with _fixture() as path:
        root, tool, proof = _new_tool_fixture(path, "local")
        together = _run(root, "judge", tool, proof, "--verdict", "push")
        assert together.returncode == 0, _out(together)
        files = _load_lock(root)["files"]
        assert files[tool]["judged"]["verdict"] == files[proof]["judged"]["verdict"] == "push"


def test_judge_push_counts_a_proof_judged_push_at_its_current_sha() -> None:
    with _fixture() as path:
        root, tool, proof = _new_tool_fixture(path, "local")
        assert _run(root, "judge", proof, "--verdict", "push").returncode == 0
        assert _run(root, "judge", tool, "--verdict", "push").returncode == 0
        _write(root / proof, b"pass  # edited\n")
        _write(root / tool, b"x = 2\n")
        _commit(root, "edit")
        stale = _run(root, "judge", tool, "--verdict", "push")
        assert stale.returncode == 1 and "shipped proof" in _out(stale), _out(stale)


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
        assert set(rows[0]) == {"relpath", "status", "layer", "sha", "prior_verdict", "diff", "identity"}
        assert rows[0]["identity"] == []
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


def test_v2_classify_local_declines_a_pulled_path_once_its_file_is_gone() -> None:
    """A bare pull takes every new-upstream row, so a decline usually follows the pull: once the
    consumer removes the pulled file, `classify --status local` records the decline without
    `--force`, since no local content remains to protect. A tracked row whose file is still
    present keeps the refusal."""
    declined = ".claude/commands/declined.md"
    kept = ".claude/commands/kept.md"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {declined: {"upstream": b"upstream body\n", "local": None},
                   kept: {"upstream": b"kept body\n", "local": b"kept body\n"}}
        )
        lock = _load_lock(root)
        del lock["files"][declined]
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _commit(root, "no row")
        pulled = _run_baseline(root, baseline, "pull", declined)
        assert pulled.returncode == 0, (pulled.stdout + pulled.stderr).decode(errors="replace")
        assert _load_lock(root)["files"][declined]["status"] == "tracked"
        _commit(root, "pulled")
        (root / declined).unlink()
        _commit(root, "declined after the pull")

        gone = _run_baseline(root, baseline, "classify", declined, "--status", "local")
        assert gone.returncode == 0, (gone.stdout + gone.stderr).decode(errors="replace")
        row = _load_lock(root)["files"][declined]
        assert row["status"] == "local" and row["absent"] is True

        present = _run_baseline(root, baseline, "classify", kept, "--status", "local")
        assert present.returncode == 1 and b"use --force" in present.stderr, present.stderr


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


def test_paths_unshipped_lists_every_push_row_the_baseline_lacks_whenever_judged() -> None:
    shipped = ".claude/tools/shipped_row.py"
    old_judged = ".claude/tools/old_judged.py"
    never = ".claude/tools/never_shipped.py"
    kept = ".claude/tools/kept_row.py"
    with _fixture() as path:
        root, _baseline, _r, _c = _v2_fixture(path, {
            shipped: {"upstream": b"one\n",
                      "judged": {"sha": _sha(b"one\n"), "verdict": "push", "at": "2026-09-14T00:00:00Z"}},
            old_judged: {"upstream": b"two\n", "local": b"two local\n",
                         "judged": {"sha": _sha(b"two local\n"), "verdict": "push", "at": "2020-01-01T00:00:00Z"}},
            never: {"manifest": False, "local": b"three\n", "hash": None,
                    "judged": {"sha": _sha(b"three\n"), "verdict": "push", "at": "2020-01-01T00:00:00Z"}},
            kept: {"upstream": b"four\n", "local": b"four local\n",
                   "judged": {"sha": _sha(b"four local\n"), "verdict": "keep-local", "at": "2026-09-14T00:00:00Z"}},
        })
        result = _run(root, "paths", "--verdict", "push", "--unshipped", "--json")
        assert result.returncode == 0, _out(result)
        assert _json_output(result) == sorted([old_judged, never]), _out(result)


def test_paths_unshipped_names_a_push_row_edited_after_its_verdict() -> None:
    moved = ".claude/tools/moved_row.py"
    with _fixture() as path:
        root, _baseline, _r, _c = _v2_fixture(path, {
            moved: {"upstream": b"one\n", "local": b"one edited again\n",
                    "judged": {"sha": _sha(b"one edited\n"), "verdict": "push", "at": "2026-09-14T00:00:00Z"}},
        })
        result = _run(root, "paths", "--verdict", "push", "--unshipped", "--json")
        assert result.returncode == 0, _out(result)
        assert _json_output(result) == [], _out(result)
        stderr = result.stderr.decode(errors="replace")
        assert f"stale verdict: {moved}" in stderr and "--carry" in stderr, stderr


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
        assert stale.returncode == 0, _out(stale)
        assert old_hash != _sha(b"existing new\n")
        assert _load_lock(root)["files"][existing]["hash"] == _sha(b"existing new\n")
        assert (root / existing).read_bytes() == b"existing new\n"
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
            rows.update({".claude/tests/test_spawn_%d.py" % i: {} for i in range(n)})
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


def test_bare_pull_names_a_diverged_row_it_holds() -> None:
    mine = ".claude/tools/mine.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {mine: {"upstream": b"v1\n"}})
        _write(root / mine, b"local edit\n")
        _commit(root, "local edit")
        _write(baseline / "template" / mine, b"v2\n")
        _commit(baseline, "upstream edit")
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / mine).read_bytes() == b"local edit\n"
        assert "held (diverged): " + mine in _out(pull) and "pull --force " + mine in _out(pull), _out(pull)


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
                 skill: bytes | None = None, settings_text: bytes | None = None,
                 overlay_permission: str | None = None):
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
    if overlay_permission is not None:
        old_settings["permissions"]["allow"].append(overlay_permission)
    rows = {
        ".claude/CLAUDE.md": {"status": "watch", "sync": "seed",
                              "local": (_REGION if region else "# CLAUDE.md\nproject only\n").encode()},
        ".claude/settings.json": {"status": "watch", "manifest": False,
                                  "local": (json.dumps(old_settings, indent=2) + "\n").encode()},
        ".claude/hooks/dispatch.py": {"row": False, "local": None, "upstream": b"import absorbed\n"},
        ".claude/hooks/absorbed.py": {"status": "tracked"},
        ".claude/hooks/optin.py": {"status": "tracked"},
        ".claude/tests/test_dispatch.py": {"row": False, "local": None},
        ".claude/tests/test_absorbed.py": {"status": "tracked"},
        ".claude/tests/test_optin.py": {"status": "tracked"},
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
    if overlay_permission is not None:
        rows[".claude/settings.godot.json"] = {
            "row": False, "local": None, "layer": "godot",
            "upstream": (json.dumps({"permissions": {"allow": [overlay_permission]}}, indent=2) + "\n").encode()}
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


def test_pull_seed_project_settings_records_layer_overlay_inputs() -> None:
    base = (json.dumps({"permissions": {"allow": ["A"]}}, indent=2) + "\n").encode()
    overlay = (json.dumps({"permissions": {"allow": ["G"]}}, indent=2) + "\n").encode()
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ".claude/tools/a.py": {},
            ".claude/settings.base.json": {"row": False, "local": None, "upstream": base},
            ".claude/settings.godot.json": {"row": False, "local": None, "upstream": overlay, "layer": "godot"},
            ".claude/settings.project.json": {"row": False, "local": None, "sync": "seed",
                                              "upstream": b"{}\n"},
        })
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        row = _load_lock(root)["files"][".claude/settings.json"]
        assert row["inputs"] == [".claude/settings.base.json", ".claude/settings.godot.json",
                                 ".claude/settings.project.json"], row
        compose = _run_baseline(root, baseline, "compose")
        assert compose.returncode == 0, _out(compose)
        settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert settings["permissions"]["allow"] == ["A", "G"], settings


def test_migrate_derives_project_settings_over_layer_overlays() -> None:
    with _fixture() as path:
        root, baseline = _v1_consumer(path, overlay_permission="Bash(overlay_tool:*)")
        result = _run(root.parent, "upgrade", "--project", str(root), "--baseline-dir", str(baseline))
        output = _out(result)
        assert _load_lock(root)["schema"] == 2, output
        project = json.loads((root / ".claude" / "settings.project.json").read_text(encoding="utf-8"))
        assert "Bash(overlay_tool:*)" not in project["permissions"]["allow"], project
        settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "Bash(overlay_tool:*)" in settings["permissions"]["allow"], settings
        assert "Bash(mytool:*)" in settings["permissions"]["allow"], settings


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
            ".claude/tests/test_a.py": {},
            OFFER: {"sync": "offer", "row": False, "local": None},
        })
        check = _run_baseline(root, baseline, "check")
        assert "offered: 1 file(s)" in _out(check), _out(check)
        assert OFFER not in _out(check), _out(check)
        states = _json_output(_run_baseline(root, baseline, "check", "--json"))["results"]
        assert states[OFFER] == "offered", states
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert strict.returncode == 0, _out(strict)


def test_check_reports_layer_closure_and_strict_fails() -> None:
    skill = ".claude/skills/demo/SKILL.md"
    detail = ".claude/skills/demo/reference/detail.md"
    cites = b"Read [detail](reference/detail.md) first.\n"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            skill: {"upstream": cites},
            detail: {"status": "local", "manifest": False, "local": b"detail\n"},
        })
        data = _json_output(_run_baseline(root, baseline, "check", "--json"))
        closure = data.get("layer_closure", [])
        assert len(closure) == 1 and closure[0].startswith(skill + ": local-only"), data
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert strict.returncode == 1, _out(strict)
        assert "layer-closure" in _out(strict) and skill in _out(strict), _out(strict)
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            skill: {"upstream": cites},
            detail: {"upstream": b"detail\n"},
        })
        data = _json_output(_run_baseline(root, baseline, "check", "--json"))
        assert "layer_closure" not in data, data
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert strict.returncode == 0, _out(strict)


def _drop_layer(root: Path, rel: str) -> None:
    lock = _load_lock(root)
    del lock["files"][rel]["layer"]
    _write_json(root / ".claude" / "baseline.lock.json", lock)
    _commit(root, "drop layer")


def test_j3_push_judge_refuses_an_identity_hit_and_accept_hit_allows_it() -> None:
    rel = ".claude/commands/leaky_judged.md"
    subsystem_id = "brew" + "Cauldron"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"clean\n"}})
        skill_md = "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: " + subsystem_id + "\n    paths: []\n```\n"
        _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", skill_md.encode())
        _write(root / rel, ("The " + subsystem_id + " owns this file.\n").encode())
        _git(root, "add", "-A")
        refused = _run_baseline(root, baseline, "judge", rel, "--verdict", "push")
        out = _out(refused)
        assert refused.returncode == 1 and "identity scan hit" in out, out
        assert "judged" not in _load_lock(root)["files"][rel], out
        hit_id = out.split("--accept-hit ", 1)[1].split()[0]
        accepted = _run_baseline(root, baseline, "judge", rel, "--verdict", "push", "--accept-hit", hit_id)
        assert accepted.returncode == 0, _out(accepted)
        assert _load_lock(root)["files"][rel]["judged"]["verdict"] == "push"
        assert _load_lock(root)["files"][rel]["judged"]["accepted_hits"] == [hit_id]
        _write(root / rel, ("The " + subsystem_id + " owns this file.\nSo does " + subsystem_id + ".\n").encode())
        _git(root, "add", "-A")
        second = _run_baseline(root, baseline, "judge", rel, "--verdict", "push", "--accept-hit", hit_id)
        assert second.returncode == 1 and ":2:" in _out(second), _out(second)
        kept = _run_baseline(root, baseline, "judge", rel, "--verdict", "keep-local", "--force")
        assert kept.returncode == 0, _out(kept)


def test_triage_lists_every_identity_hit_the_push_scan_would_refuse() -> None:
    rel = ".claude/commands/leaky_triaged.md"
    subsystem_id = "brew" + "Cauldron"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"clean\n"}})
        skill_md = "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: " + subsystem_id + "\n    paths: []\n```\n"
        _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", skill_md.encode())
        _write(root / rel, ("clean\nThe " + subsystem_id + " owns this.\nAgain " + subsystem_id + ".\n").encode())
        _git(root, "add", "-A")
        result = _run_baseline(root, baseline, "triage", "--json")
        assert result.returncode == 0, _out(result)
        row = next(r for r in _json_output(result) if r["relpath"] == rel)
        assert [h["line"] for h in row["identity"]] == [2, 3], row["identity"]
        assert all(set(h) == {"id", "line", "kind", "excerpt"} for h in row["identity"]), row["identity"]
        refused = _run_baseline(root, baseline, "judge", rel, "--verdict", "push")
        assert row["identity"][0]["id"] in _out(refused), _out(refused)


def test_bare_pull_refreshes_a_lock_stale_row() -> None:
    rel = ".claude/tools/stale.wasm"
    data = b"\x00asm\x01\x00\x00\x00same"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": data, "local": data, "hash": "0" * 64}})
        assert "in-sync-lock-stale" in _out(_run_baseline(root, baseline, "check"))
        pulled = _run_baseline(root, baseline, "pull")
        assert pulled.returncode == 0, _out(pulled)
        assert _load_lock(root)["files"][rel]["hash"] != "0" * 64, _out(pulled)
        assert (root / rel).read_bytes() == data
        assert "in-sync-lock-stale" not in _out(_run_baseline(root, baseline, "check"))


def test_j3_push_judge_refuses_an_unlayered_row_upstream_lacks() -> None:
    new_rel = ".claude/tools/brand_new.py"
    shipped = ".claude/tools/shipped.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            new_rel: {"manifest": False, "local": b"v = 1\n", "hash": None},
            shipped: {"upstream": b"v = 2\n", "local": b"v = 3\n"},
        })
        _drop_layer(root, new_rel)
        _drop_layer(root, shipped)
        refused = _run_baseline(root, baseline, "judge", new_rel, "--verdict", "push")
        out = _out(refused)
        assert refused.returncode == 1, out
        assert "classify " + new_rel in out and "--layer pure|coding|godot" in out, out
        assert "judged" not in _load_lock(root)["files"][new_rel]
        ok = _run_baseline(root, baseline, "judge", shipped, "--verdict", "push")
        assert ok.returncode == 0, _out(ok)


def test_j3_write_prints_the_layer_closure_it_adds() -> None:
    skill = ".claude/skills/demo/SKILL.md"
    detail = ".claude/skills/demo/reference/detail.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            skill: {"upstream": b"Read [detail](reference/detail.md) first.\n"},
            detail: {"manifest": False, "local": b"detail\n", "hash": None},
        })
        result = _run_baseline(root, baseline, "ignore", detail, "--force")
        out = _out(result)
        assert result.returncode == 0, out
        assert "layer-closure" in out and skill + ": local-only" in out, out
        again = _run_baseline(root, baseline, "ignore", skill, "--force")
        assert skill + ": local-only" not in _out(again), _out(again)


def test_j3_keep_local_on_an_unshipped_tracked_row_sets_local() -> None:
    rel = ".claude/tools/never_shipped.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            rel: {"manifest": False, "local": b"v = 1\n", "hash": None},
        })
        before = _json_output(_run_baseline(root, baseline, "check", "--json"))
        assert before["results"][rel] == "unpublished", before
        judged = _run_baseline(root, baseline, "judge", rel, "--verdict", "keep-local")
        assert judged.returncode == 0, _out(judged)
        row = _load_lock(root)["files"][rel]
        assert row["status"] == "local" and "hash" not in row, row
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert strict.returncode == 0 and "unpublished" not in _out(strict), _out(strict)


def test_j3_relpath_with_control_or_edge_whitespace_refuses() -> None:
    rel = ".claude/tools/crlf.py"
    with _fixture() as path:
        root, _baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"v = 1\n"}})
        for bad in (rel + "\r", rel + " ", rel + "\t", "\n" + rel):
            result = _run(root, "judge", bad, "--verdict", "keep-local")
            assert result.returncode == 2, (repr(bad), _out(result))
        assert "judged" not in _load_lock(root)["files"][rel]


def test_j3_layer_on_an_existing_row_needs_relayer() -> None:
    rel = ".claude/tools/relayer.py"
    with _fixture() as path:
        root, _baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"v = 1\n"}})
        refused = _run(root, "classify", rel, "--status", "local", "--layer", "coding", "--force")
        assert refused.returncode == 1 and "--relayer" in _out(refused), _out(refused)
        assert _load_lock(root)["files"][rel]["layer"] == "pure"
        allowed = _run(root, "classify", rel, "--status", "local", "--layer", "coding", "--force", "--relayer")
        assert allowed.returncode == 0, _out(allowed)
        assert _load_lock(root)["files"][rel]["layer"] == "coding"
        no_layer = _run(root, "classify", rel, "--status", "local", "--relayer")
        assert no_layer.returncode == 2, _out(no_layer)


def _delete_upstream(baseline: Path, relpath: str) -> None:
    (baseline / "template" / relpath).unlink()
    _commit(baseline, "upstream deletes " + relpath)


def test_pull_retires_unmodified_file_removed_upstream() -> None:
    gone = ".claude/tools/gone_unmodified.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {gone: {"upstream": b"old\n"}})
        _delete_upstream(baseline, gone)
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert not (root / gone).exists()
        assert gone not in _load_lock(root)["files"]
        assert f"retired: {gone}" in _out(pull), _out(pull)


def test_explicit_pull_keeps_modified_file_removed_upstream_as_local() -> None:
    gone = ".claude/tools/gone_modified.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {gone: {"upstream": b"old\n"}})
        _delete_upstream(baseline, gone)
        _write(root / gone, b"project edit\n")
        pull = _run_baseline(root, baseline, "pull", gone)
        assert pull.returncode == 0, _out(pull)
        assert (root / gone).read_bytes() == b"project edit\n"
        entry = _load_lock(root)["files"][gone]
        assert entry["status"] == "local" and "hash" not in entry, entry
        assert f"kept local (modified, removed upstream): {gone}" in _out(pull), _out(pull)


def test_pull_drops_row_of_file_removed_upstream_and_missing_locally() -> None:
    gone = ".claude/tools/gone_missing.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {gone: {"upstream": b"old\n", "local": None}})
        _delete_upstream(baseline, gone)
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert gone not in _load_lock(root)["files"]
        assert f"retired: {gone}" in _out(pull), _out(pull)


def test_a_tracked_row_never_published_is_unpublished_and_pull_keeps_it() -> None:
    mine = ".claude/tools/mine_unpublished.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {mine: {"upstream": b"v1\n"}})
        _delete_upstream(baseline, mine)
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = _load_lock(root)
        lock["files"][mine].pop("hash", None)
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")
        _commit(root, "an unpublished tracked row")
        check = _run_baseline(root, baseline, "check", "--json")
        assert _json_output(check)["results"][mine] == "unpublished", _out(check)
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert (root / mine).is_file() and _load_lock(root)["files"][mine]["status"] == "tracked", _out(pull)
        update = _run_baseline(root, baseline, "update-lock")
        assert update.returncode == 0, _out(update)


def _diverge(root: Path, baseline: Path, rel: str, local: bytes, upstream: bytes) -> None:
    """Both sides edit a tracked row after its recorded base: a committed local edit, an upstream commit."""
    _write(root / rel, local)
    _commit(root, "local edit " + rel)
    _write(baseline / "template" / rel, upstream)
    _commit(baseline, "upstream edit " + rel)


def test_pull_merge_combines_both_sides_of_a_diverged_row() -> None:
    rel = ".claude/rules/merged.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"one\ntwo\nthree\n"}})
        _diverge(root, baseline, rel, b"ONE\ntwo\nthree\n", b"one\ntwo\nTHREE\n")
        refused = _run_baseline(root, baseline, "pull", rel)
        assert refused.returncode != 0 and "diverged" in _out(refused), _out(refused)
        pull = _run_baseline(root, baseline, "pull", "--merge", rel)
        assert pull.returncode == 0, _out(pull)
        assert (root / rel).read_bytes() == b"ONE\ntwo\nTHREE\n", (root / rel).read_bytes()
        assert _load_lock(root)["files"][rel]["hash"] == _sha(b"one\ntwo\nTHREE\n")
        assert f"merged: {rel}" in _out(pull), _out(pull)


def test_pull_merge_refuses_a_diverged_binary_row_and_leaves_it_untouched() -> None:
    rel = ".claude/tools/grammar.wasm"
    base, local, upstream = b"\x00asm base\n", b"\x00asm local\r\n\xff", b"\x00asm upstream\r\n\xfe"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": base}})
        _diverge(root, baseline, rel, local, upstream)
        before = _load_lock(root)["files"][rel]["hash"]
        pull = _run_baseline(root, baseline, "pull", "--merge", rel)
        out = _out(pull)
        assert pull.returncode == 1, out
        assert f"conflict: {rel} (binary row" in out, out
        assert (root / rel).read_bytes() == local
        assert _load_lock(root)["files"][rel]["hash"] == before


def test_pull_merge_reports_a_conflict_and_leaves_the_row_untouched() -> None:
    rel = ".claude/rules/clash.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"one\ntwo\nthree\n"}})
        _diverge(root, baseline, rel, b"one\nLOCAL\nthree\n", b"one\nUPSTREAM\nthree\n")
        before = _load_lock(root)["files"][rel]["hash"]
        pull = _run_baseline(root, baseline, "pull", "--merge")
        out = _out(pull)
        assert pull.returncode == 1, out
        assert (root / rel).read_bytes() == b"one\nLOCAL\nthree\n"
        assert _load_lock(root)["files"][rel]["hash"] == before
        line = next((l for l in out.splitlines() if l.startswith(f"conflict: {rel} ")), "")
        assert line, out
        copy = Path(line.rsplit(" -> ", 1)[-1].strip())
        assert copy.is_file() and b"<<<<<<< local" in copy.read_bytes() and b"UPSTREAM" in copy.read_bytes(), line


def test_resolve_installs_a_marker_free_copy_and_refuses_one_with_markers() -> None:
    rel = ".claude/rules/clash.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"one\ntwo\nthree\n"}})
        _diverge(root, baseline, rel, b"one\nLOCAL\nthree\n", b"one\nUPSTREAM\nthree\n")
        pull = _run_baseline(root, baseline, "pull", "--merge")
        copy = Path(next(l for l in _out(pull).splitlines() if l.startswith("conflict: ")).rsplit(" -> ", 1)[-1].strip())
        refused = _run_baseline(root, baseline, "resolve", rel)
        assert refused.returncode != 0 and "markers" in _out(refused), _out(refused)
        assert (root / rel).read_bytes() == b"one\nLOCAL\nthree\n"
        _write(copy, b"one\nLOCAL UPSTREAM\nthree\n")
        done = _run_baseline(root, baseline, "resolve", rel)
        assert done.returncode == 0, _out(done)
        assert (root / rel).read_bytes() == b"one\nLOCAL UPSTREAM\nthree\n"
        assert _load_lock(root)["files"][rel]["hash"] == _sha(b"one\nUPSTREAM\nthree\n")
        assert not copy.exists() and f"resolved: {rel}" in _out(done), _out(done)


def test_resolve_refuses_when_upstream_moved_or_the_file_changed_since_the_merge() -> None:
    rel = ".claude/rules/clash.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {"upstream": b"one\ntwo\nthree\n"}})
        _diverge(root, baseline, rel, b"one\nLOCAL\nthree\n", b"one\nUPSTREAM\nthree\n")
        pull = _run_baseline(root, baseline, "pull", "--merge")
        copy = Path(next(l for l in _out(pull).splitlines() if l.startswith("conflict: ")).rsplit(" -> ", 1)[-1].strip())
        _write(copy, b"one\nLOCAL UPSTREAM\nthree\n")
        _write(root / rel, b"one\nLOCAL EDITED\nthree\n")
        dirty = _run_baseline(root, baseline, "resolve", rel)
        assert dirty.returncode != 0 and "dirty" in _out(dirty), _out(dirty)
        assert (root / rel).read_bytes() == b"one\nLOCAL EDITED\nthree\n"
        _write(root / rel, b"one\nLOCAL\nthree\n")
        _write(baseline / "template" / rel, b"one\nUPSTREAM\nthree\nfour\n")
        _commit(baseline, "upstream moves again " + rel)
        before = _load_lock(root)["files"][rel]["hash"]
        moved = _run_baseline(root, baseline, "resolve", rel)
        assert moved.returncode != 0 and f"pull --merge {rel}" in _out(moved), _out(moved)
        assert _load_lock(root)["files"][rel]["hash"] == before
        assert (root / rel).read_bytes() == b"one\nLOCAL\nthree\n" and copy.is_file()


def test_pull_merge_reports_a_dirty_diverged_row_and_pulls_the_rest() -> None:
    dirty, other = ".claude/rules/dirty.md", ".claude/rules/other.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {dirty: {"upstream": b"a\n"}, other: {"upstream": b"b\n"}})
        _diverge(root, baseline, dirty, b"A\n", b"a2\n")
        _write(root / dirty, b"uncommitted\n")
        _write(baseline / "template" / other, b"b2\n")
        _commit(baseline, "upstream edit " + other)
        pull = _run_baseline(root, baseline, "pull", "--merge")
        out = _out(pull)
        assert pull.returncode == 1, out
        assert (root / dirty).read_bytes() == b"uncommitted\n"
        assert (root / other).read_bytes() == b"b2\n", out
        assert f"conflict: {dirty} (uncommitted local edits" in out, out


def _keep_local_judgment(content: bytes) -> dict:
    return {"sha": _sha(content), "verdict": "keep-local", "at": "2026-10-20T00:00:00Z",
            "borderline": False, "confirmed_at": None}


def test_v2_check_strict_clears_only_a_current_keep_local_verdict() -> None:
    kept = ".claude/tools/kept_local.py"
    stale = ".claude/tools/stale_local.py"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {kept: {"upstream": b"value = 1\n", "local": b"value = 2\n",
                    "judged": _keep_local_judgment(b"value = 2\n")},
             ".claude/tests/test_kept_local.py": {}},
        )
        current = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert current.returncode == 0, _out(current)
        assert _json_output(current)["results"][kept] == "local-modified"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path,
            {stale: {"upstream": b"value = 1\n", "local": b"value = 3\n",
                     "judged": _keep_local_judgment(b"value = 2\n")}},
        )
        moved = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert moved.returncode == 1, _out(moved)
        assert _json_output(moved)["results"][stale] == "local-modified"


MEMORY_UPSTREAM = b"---\nname: rule\ntype: feedback\nmetadata:\n  node_type: memory\n---\nBody line\n"
MEMORY_WITH_PROVENANCE = (
    b"---\nname: rule\ntype: feedback\nmetadata:\n  node_type: memory\n"
    b"  originSessionId: session-one\n  modified: 2026-01-01T00:00:00Z\n---\nBody line\n"
)


def test_v2_memory_provenance_keys_never_make_a_row_differ() -> None:
    memory = ".claude/auto-memory/feedback_rule.md"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {memory: {"upstream": MEMORY_UPSTREAM, "local": MEMORY_WITH_PROVENANCE}},
        )
        result = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert result.returncode == 0, _out(result)
        assert _json_output(result)["results"][memory] == "in-sync"


def test_v2_pull_writes_memory_without_provenance_keys() -> None:
    memory = ".claude/auto-memory/feedback_pull.md"
    old = b"---\nname: pull\n---\nOld body\n"
    upstream = (b"---\nname: pull\nmetadata:\n  node_type: memory\n  originSessionId: session-two\n"
                b"  modified: 2026-01-02T00:00:00Z\n---\nBody\n")
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {memory: {"upstream": upstream, "local": old, "hash": _sha(old)}},
        )
        pull = _run_baseline(root, baseline, "pull", memory)
        assert pull.returncode == 0, _out(pull)
        assert (root / memory).read_bytes() == b"---\nname: pull\nmetadata:\n  node_type: memory\n---\nBody\n"


def test_v2_provenance_strip_leaves_non_memory_files_alone() -> None:
    tool = ".claude/tools/front_matter.py"
    upstream = b"---\nmodified: yes\n---\nvalue = 1\n"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(
            path, {tool: {"upstream": upstream, "local": upstream.replace(b"yes", b"no")}},
        )
        result = _run_baseline(root, baseline, "check", "--strict", "--json")
        assert _json_output(result)["results"][tool] == "local-modified"


def test_pull_prints_each_written_row_the_gitignore_matches() -> None:
    ignored = ".claude/tests/test_planted.py"
    open_row = ".claude/tools/open.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            ignored: {"local": None, "row": False},
            open_row: {"local": None, "row": False},
        })
        _write(root / ".gitignore", b".claude/tests/" + bytes([10]))
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert f"ignored: {ignored} (add with git add -f)" in _out(pull), _out(pull)
        assert f"ignored: {open_row}" not in _out(pull), _out(pull)


def test_pull_chmods_a_100755_entry_and_leaves_a_100644_entry() -> None:
    exec_row = ".claude/scripts/planted_run.sh"
    plain_row = ".claude/tools/planted_plain.py"
    engine = _load_engine()
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            exec_row: {"local": None, "row": False},
            plain_row: {"local": None, "row": False},
        })
        _git(baseline, "update-index", "--chmod=+x", "template/" + exec_row)
        _git(baseline, "commit", "-q", "-m", "exec bit")
        chmods = []
        real_chmod = engine.os.chmod

        def recording_chmod(target, mode, *a, **k):
            chmods.append((Path(target).name, mode))
            return real_chmod(target, mode, *a, **k)

        engine.os.chmod = recording_chmod
        try:
            with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()):
                code = engine.main(["pull", "--baseline-dir", str(baseline)])
        finally:
            engine.os.chmod = real_chmod
        assert code == 0, "pull exited %s" % code
        assert ("planted_run.sh", 0o755) in chmods, chmods
        assert not [c for c in chmods if c[0] == "planted_plain.py"], chmods


def test_pull_keeps_the_posix_exec_bit_of_a_100755_entry() -> None:
    if os.name != "posix":
        return
    exec_row = ".claude/scripts/planted_run.sh"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {exec_row: {"local": None, "row": False}})
        _git(baseline, "update-index", "--chmod=+x", "template/" + exec_row)
        _git(baseline, "commit", "-q", "-m", "exec bit")
        pull = _run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, _out(pull)
        assert os.stat(root / exec_row).st_mode & 0o111, "pulled 100755 file is not executable"


def _judged_push(data: bytes) -> dict:
    return {"sha": _sha(data), "verdict": "push", "at": "2026-09-14T00:00:00Z",
            "borderline": False, "confirmed_at": None}


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").decode().strip()


def test_carry_walks_the_range_and_leaves_a_peer_row_alone() -> None:
    a, b = ".claude/tools/carry_a.py", ".claude/tools/carry_b.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            a: {"local": b"a = 0\n", "judged": _judged_push(b"a = 0\n")},
            b: {"local": b"b = 0\n", "judged": _judged_push(b"b = 0\n")},
        })
        base = _head(root)
        _write(root / a, b"a = 1\n")
        _write(root / b, b"b = 1\n")
        _commit(root, "one")
        _write(root / a, b"a = 2\nmore = 1\n")
        _commit(root, "two")
        result = _run_baseline(root, baseline, "judge", a, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 0, out
        assert "carried: " + a in out and "+2" in out and "-1" in out, out
        files = _load_lock(root)["files"]
        assert files[a]["judged"]["sha"] == _sha(b"a = 2\nmore = 1\n"), files[a]
        assert files[a]["judged"]["verdict"] == "push"
        assert files[a]["judged"]["carried_from"] == _sha(b"a = 0\n")
        assert files[b]["judged"]["sha"] == _sha(b"b = 0\n"), files[b]
        assert "carried_from" not in files[b]["judged"]


def test_carry_lists_a_row_the_range_adds_deletes_or_leaves_alone() -> None:
    added, gone, still = ".claude/tools/c_added.py", ".claude/tools/c_gone.py", ".claude/tools/c_still.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            added: {"local": None, "judged": _judged_push(b"x = 0\n")},
            gone: {"local": b"g = 0\n", "judged": _judged_push(b"g = 0\n")},
            still: {"local": b"s = 0\n", "judged": _judged_push(b"s = 0\n")},
        })
        base = _head(root)
        _write(root / added, b"x = 0\n")
        (root / gone).unlink()
        _commit(root, "add and delete")
        result = _run_baseline(root, baseline, "judge", added, gone, still, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 0, out
        for rel in (added, gone, still):
            assert f"not carried: {rel} (" in out, out
        assert "carried: " not in out.replace("not carried: ", ""), out
        files = _load_lock(root)["files"]
        assert files[gone]["judged"]["sha"] == _sha(b"g = 0\n")
        assert files[added]["judged"]["sha"] == _sha(b"x = 0\n")


def test_carry_leaves_a_row_judged_keep_local_alone() -> None:
    rel = ".claude/tools/carry_kept.py"
    with _fixture() as path:
        kept = {**_judged_push(b"k = 0\n"), "verdict": "keep-local"}
        root, baseline, _r, _c = _v2_fixture(path, {
            rel: {"local": b"k = 0\n", "judged": kept},
        })
        base = _head(root)
        _write(root / rel, b"k = 1\n")
        _commit(root, "edit")
        result = _run_baseline(root, baseline, "judge", rel, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 0, out
        assert f"not carried: {rel} (judged keep-local, not push)" in out, out
        judged = _load_lock(root)["files"][rel]["judged"]
        assert judged["verdict"] == "keep-local" and judged["sha"] == _sha(b"k = 0\n"), judged


def test_carry_names_a_broken_chain() -> None:
    rel = ".claude/tools/chain.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            rel: {"local": b"v = 0\n", "judged": _judged_push(b"v = 0\n")},
        })
        _write(root / rel, b"v = 9\n")
        _commit(root, "unjudged edit")
        base = _head(root)
        _write(root / rel, b"v = 10\n")
        _commit(root, "in range")
        result = _run_baseline(root, baseline, "judge", rel, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 0, out
        assert f"not carried: {rel} (chain broken)" in out, out
        assert _load_lock(root)["files"][rel]["judged"]["sha"] == _sha(b"v = 0\n")


def test_carry_starts_from_a_pulled_row_whose_judged_sha_is_older() -> None:
    rel = ".claude/tools/pulled.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            rel: {"upstream": b"p = 1\n", "local": b"p = 1\n", "judged": _judged_push(b"p = 0\n")},
        })
        base = _head(root)
        _write(root / rel, b"p = 2\n")
        _commit(root, "edit the pulled copy")
        result = _run_baseline(root, baseline, "judge", rel, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 0, out
        assert "carried: " + rel in out, out
        judged = _load_lock(root)["files"][rel]["judged"]
        assert judged["sha"] == _sha(b"p = 2\n") and judged["carried_from"] == _sha(b"p = 1\n"), judged


def test_carry_refuses_an_identity_hit_and_a_layerless_new_row() -> None:
    leaky, fresh = ".claude/commands/carry_leaky.md", ".claude/tools/carry_fresh.py"
    subsystem_id = "brew" + "Cauldron"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            leaky: {"upstream": b"clean\n", "judged": _judged_push(b"clean\n")},
            fresh: {"manifest": False, "local": b"f = 0\n", "hash": None, "judged": _judged_push(b"f = 0\n")},
        })
        _drop_layer(root, fresh)
        skill_md = "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: " + subsystem_id + "\n    paths: []\n```\n"
        _write(root / ".claude" / "skills" / "project_subsystems" / "SKILL.md", skill_md.encode())
        _commit(root, "subsystem")
        base = _head(root)
        _write(root / leaky, ("The " + subsystem_id + " owns this file.\n").encode())
        _write(root / fresh, b"f = 1\n")
        _commit(root, "edits")
        rng = f"{base}..{_head(root)}"
        hit = _run_baseline(root, baseline, "judge", leaky, "--carry", rng)
        assert hit.returncode == 1 and "identity scan hit" in _out(hit), _out(hit)
        layer = _run_baseline(root, baseline, "judge", fresh, "--carry", rng)
        assert layer.returncode == 1 and "--layer pure|coding|godot" in _out(layer), _out(layer)
        files = _load_lock(root)["files"]
        assert files[leaky]["judged"]["sha"] == _sha(b"clean\n")
        assert files[fresh]["judged"]["sha"] == _sha(b"f = 0\n")


def test_carry_usage_errors_exit_two_naming_the_flag() -> None:
    rel = ".claude/tools/usage.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {rel: {}})
        for extra, flag in (
            (["--verdict", "push"], "--verdict"),
            (["--commit", "HEAD"], "--commit"),
            (["--borderline"], "--borderline"),
            (["--confirm"], "--confirm"),
        ):
            result = _run_baseline(root, baseline, "judge", rel, "--carry", "HEAD~1..HEAD", *extra)
            assert result.returncode == 2 and flag in _out(result), (flag, _out(result))
        other = _run_baseline(root, baseline, "check", "--carry", "HEAD~1..HEAD")
        assert other.returncode == 2 and "--carry" in _out(other), _out(other)
        for bad in ("HEAD", "a..", "..b", "a...b"):
            result = _run_baseline(root, baseline, "judge", rel, "--carry", bad)
            assert result.returncode == 2 and "--carry" in _out(result), (bad, _out(result))


_DEP_CITER = ".claude/commands/dep_citer.md"
_DEP_HELPER = ".claude/tools/dep_helper.py"
_DEP_OLD, _DEP_NEW = b"h = 0\n", b"h = 1\n"
_DEP_CITES = b"Run:\n```\npython3 \"$CLAUDE_PROJECT_DIR/.claude/tools/dep_helper.py\"\n```\n"


def _dep_rows(helper: dict) -> dict[str, dict]:
    return {
        _DEP_CITER: {"status": "local", "manifest": False, "local": _DEP_CITES},
        _DEP_HELPER: helper,
    }


def _dep_helpers() -> dict[str, dict]:
    return {
        "local": {"status": "local", "manifest": False, "local": _DEP_NEW},
        "local-modified": {"upstream": _DEP_OLD, "local": _DEP_NEW},
        "forked": {"status": "forked", "upstream": _DEP_OLD, "local": _DEP_NEW},
        "local-shipped": {"status": "local", "upstream": _DEP_OLD, "local": _DEP_NEW},
        "in-sync": {"upstream": _DEP_OLD, "local": _DEP_OLD},
        "judged-local-modified": {"upstream": _DEP_OLD, "local": _DEP_NEW, "judged": _judged_push(_DEP_NEW)},
        "judged-unpublished": {"manifest": False, "hash": None, "local": _DEP_NEW,
                               "judged": _judged_push(_DEP_NEW)},
    }


def _dep_fixture(path: Path, helper: dict):
    root, baseline, remote, commit = _v2_fixture(path, _dep_rows(helper))
    lock = _load_lock(root)
    if lock["files"][_DEP_HELPER]["status"] == "forked":
        lock["files"][_DEP_HELPER]["base"] = commit
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _commit(root, "fork base")
    return root, baseline


def test_judge_push_refuses_a_citer_whose_dependency_does_not_ship() -> None:
    meaning = {"local": "never ships", "local-modified": "differs from the baseline copy"}
    for state, words in meaning.items():
        with _fixture() as path:
            root, baseline = _dep_fixture(path, _dep_helpers()[state])
            result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
            out = _out(result)
            assert result.returncode == 1, (state, out)
            assert _DEP_HELPER in out and state in out and words in out, (state, out)
            assert f"judge {_DEP_HELPER} --verdict push" in out, (state, out)
            assert "judged" not in _load_lock(root)["files"][_DEP_CITER], state


def test_judge_push_allows_a_citer_whose_dependency_ships_or_is_judged_push() -> None:
    # A forked row, or a local row the baseline ships (a seed), reaches every consumer as the baseline's
    # copy; refusing it would block the citer for as long as the fork or seed stands.
    for state in ("in-sync", "judged-local-modified", "judged-unpublished", "forked", "local-shipped"):
        with _fixture() as path:
            root, baseline = _dep_fixture(path, _dep_helpers()[state])
            result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
            assert result.returncode == 0, (state, _out(result))
            assert _load_lock(root)["files"][_DEP_CITER]["judged"]["verdict"] == "push", state


def _shipped_citer_rows(helper: dict, *, baseline_cites: bool) -> dict[str, dict]:
    # The citer ships (a baseline copy exists); its local copy always differs by one line.
    old = _DEP_CITES if baseline_cites else b"Run nothing.\n"
    return {
        _DEP_CITER: {"upstream": old, "local": _DEP_CITES + b"More.\n"},
        _DEP_HELPER: helper,
    }


def test_judge_push_passes_when_the_baseline_copy_already_cites_the_modified_dependency() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, _shipped_citer_rows(_dep_helpers()["local-modified"],
                                                                      baseline_cites=True))
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
        assert result.returncode == 0, _out(result)
        assert _load_lock(root)["files"][_DEP_CITER]["judged"]["verdict"] == "push"


def test_judge_push_refuses_a_citation_the_change_adds_to_a_modified_dependency() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, _shipped_citer_rows(_dep_helpers()["local-modified"],
                                                                      baseline_cites=False))
        # the local copy must cite the helper: replace the one-line addition with the citation
        _write(root / _DEP_CITER, _DEP_CITES)
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
        out = _out(result)
        assert result.returncode == 1, out
        assert _DEP_HELPER in out and "adds the citation" in out, out


def test_judge_push_refuses_a_local_dependency_even_when_the_baseline_copy_cites_it() -> None:
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, _shipped_citer_rows(_dep_helpers()["local"],
                                                                      baseline_cites=True))
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
        out = _out(result)
        assert result.returncode == 1, out
        assert _DEP_HELPER in out and "never ships" in out, out


def test_judge_push_keeps_the_rule_for_a_citer_new_to_the_baseline() -> None:
    with _fixture() as path:
        root, baseline = _dep_fixture(path, _dep_helpers()["local-modified"])
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--verdict", "push")
        out = _out(result)
        assert result.returncode == 1, out
        assert _DEP_HELPER in out and "differs from the baseline copy" in out, out


def test_judge_push_allows_a_citer_and_its_local_dependency_in_one_call() -> None:
    with _fixture() as path:
        proof = ".claude/tests/test_dep_helper.py"
        rows = _dep_rows(_dep_helpers()["local"])
        rows[proof] = {"status": "local", "manifest": False, "local": b"pass\n"}
        root, baseline, _r, _c = _v2_fixture(path, rows)
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, _DEP_HELPER, proof, "--verdict", "push")
        assert result.returncode == 0, _out(result)


def test_carry_refuses_a_row_whose_dependency_is_local() -> None:
    with _fixture() as path:
        rows = _dep_rows(_dep_helpers()["local"])
        rows[_DEP_CITER]["judged"] = _judged_push(_DEP_CITES)
        root, baseline, _r, _c = _v2_fixture(path, rows)
        base = _head(root)
        _write(root / _DEP_CITER, _DEP_CITES + b"More.\n")
        _commit(root, "edit citer")
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, "--carry", f"{base}..{_head(root)}")
        out = _out(result)
        assert result.returncode == 1, out
        assert _DEP_HELPER in out and "local" in out and f"judge {_DEP_HELPER} --verdict push" in out, out
        assert _load_lock(root)["files"][_DEP_CITER]["judged"]["sha"] == _sha(_DEP_CITES)


def test_carry_refuses_a_new_tool_whose_proof_is_local() -> None:
    with _fixture() as path:
        tool, proof = ".claude/tools/fresh.py", ".claude/tests/test_fresh.py"
        rows = _proof_rows((tool, "local", b"x = 1\n"), (proof, "local", b"pass\n"))
        rows[tool]["judged"] = _judged_push(b"x = 1\n")
        root, baseline, _r, _c = _v2_fixture(path, rows)
        base = _head(root)
        _write(root / tool, b"x = 2\n")
        _commit(root, "edit tool")
        result = _run_baseline(root, baseline, "judge", tool, "--carry", f"{base}..{_head(root)}")
        assert result.returncode == 1, _out(result)
        assert "shipped proof" in _out(result), _out(result)
        assert _load_lock(root)["files"][tool]["judged"]["sha"] == _sha(b"x = 1\n")


def test_carry_refuses_a_citer_whose_named_dependency_is_not_carried() -> None:
    # A named dependency judged push at an older blob, which this range does not carry, must not pass the
    # citer's S-deps check: it is not being pushed at its current sha.
    with _fixture() as path:
        rows = _dep_rows(_dep_helpers()["local"])
        rows[_DEP_CITER]["judged"] = _judged_push(_DEP_CITES)
        rows[_DEP_HELPER]["judged"] = _judged_push(_DEP_OLD)
        root, baseline, _r, _c = _v2_fixture(path, rows)
        base = _head(root)
        _write(root / _DEP_CITER, _DEP_CITES + b"More.\n")
        _commit(root, "edit citer")
        result = _run_baseline(root, baseline, "judge", _DEP_CITER, _DEP_HELPER, "--carry", f"{base}..{_head(root)}")
        assert result.returncode == 1, _out(result)
        assert _DEP_HELPER in _out(result), _out(result)


def test_dependency_check_failure_refuses_the_push() -> None:
    # An unreadable dependency scan must refuse, never let the citer pass unchecked.
    sys.path.insert(0, str(ENGINE.parent))
    import baseline_sync as bs
    real = bs.layer_closure.row_dependencies

    def unreadable(*_args, **_kwargs):
        raise OSError("planted unreadable manifest")

    bs.layer_closure.row_dependencies = unreadable
    try:
        try:
            bs._refuse_unshipped_dependencies(Path("."), _DEP_CITER, _DEP_CITES, {"files": {}}, None, {}, None)
        except bs.BaselineError as exc:
            assert "dependency check failed" in str(exc), str(exc)
            return
        raise AssertionError("an unreadable dependency scan passed the push")
    finally:
        bs.layer_closure.row_dependencies = real


def test_unproven_tools_binds_every_tool_in_one_scan() -> None:
    engine = _load_engine()
    ht = engine._harness_tests()
    names = ("alpha", "beta", "gamma")
    with _fixture() as path:
        for name in names:
            _write(path / ".claude" / "tools" / (name + ".py"), b"x = 1\n")
        _write(path / ".claude" / "tests" / "test_alpha.py", b"pass\n")
        files = {".claude/tools/%s.py" % n: {"status": "tracked"} for n in names}
        files[".claude/tests/test_alpha.py"] = {"status": "tracked"}
        calls = {"discover": 0}
        real = ht.discover

        def counted(*a, **k):
            calls["discover"] += 1
            return real(*a, **k)

        ht.discover = counted
        try:
            got = engine.unproven_tools(path, files, list(files))
        finally:
            ht.discover = real
        assert calls["discover"] == 1, calls
        assert got == [".claude/tools/beta.py", ".claude/tools/gamma.py"], got

_RESET_STDERR = b"fatal: unable to access 'https://example.invalid/x.git/': Recv failure: Connection was reset\n"


def _stub_runs(outcomes):
    """A subprocess.run stub replaying (returncode, stderr) outcomes; records each argv."""
    calls = []

    def _run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        code, err = outcomes[min(len(calls), len(outcomes)) - 1]
        return subprocess.CompletedProcess(cmd, code, stdout=b"deadbeef\n", stderr=err)

    return _run, calls


def _resolve_with(outcomes):
    from unittest import mock
    engine = _load_engine()
    run, calls = _stub_runs(outcomes)
    err = io.StringIO()
    with mock.patch.object(engine, "NETWORK_RETRY_DELAYS", (0, 0), create=True), \
            mock.patch.object(subprocess, "run", run), contextlib.redirect_stderr(err):
        try:
            pinned, failure = engine._resolve_default(Path("cache"), "repo", "main"), None
        except engine.BaselineError as exc:
            pinned, failure = None, exc
    return engine, calls, err.getvalue(), pinned, failure


def test_fetch_retries_a_connection_reset_then_succeeds() -> None:
    _engine, calls, err, pinned, exc = _resolve_with([(128, _RESET_STDERR), (0, b""), (0, b"")])
    assert exc is None, "a single reset must not fail the fetch: %s" % exc
    fetches = [c for c in calls if "fetch" in c]
    assert len(fetches) == 2, calls
    assert "retry" in err and "Connection was reset" in err, err


def test_fetch_does_not_retry_repository_not_found() -> None:
    _engine, calls, _err, _pinned, exc = _resolve_with(
        [(128, b"remote: Repository not found.\nfatal: repository 'x' not found\n")])
    assert exc is not None and "could not fetch baseline ref main" in str(exc), exc
    assert len([c for c in calls if "fetch" in c]) == 1, calls


def test_a_non_network_subcommand_is_not_retried_on_reset_looking_stderr() -> None:
    from unittest import mock
    engine = _load_engine()
    run, calls = _stub_runs([(128, _RESET_STDERR)])
    with mock.patch.object(engine, "NETWORK_RETRY_DELAYS", (0, 0), create=True), \
            mock.patch.object(subprocess, "run", run):
        result = engine._git(Path("cache"), ["cat-file", "-e", "abc"], check=False)
    assert result.returncode == 128 and len(calls) == 1, calls


def test_three_transient_fetch_failures_raise_the_existing_error() -> None:
    _engine, calls, _err, _pinned, exc = _resolve_with([(128, _RESET_STDERR)])
    assert exc is not None and str(exc).startswith("could not fetch baseline ref main: "), exc
    assert "Connection was reset" in str(exc), exc
    assert len(calls) == 3, calls


def test_fetch_does_not_retry_a_permanent_tls_certificate_failure() -> None:
    for text in (b"SSL certificate problem: unable to get local issuer certificate",
                 b"SSL certificate problem: certificate verify failed"):
        err = b"fatal: unable to access 'https://example.invalid/x.git/': " + text + b"\n"
        _engine, calls, _err, _pinned, exc = _resolve_with([(128, err)])
        assert exc is not None and "could not fetch baseline ref main" in str(exc), exc
        assert len([c for c in calls if "fetch" in c]) == 1, calls


def test_fetch_still_retries_a_transient_tls_failure() -> None:
    for text in (b"Recv failure: SSL_ERROR_SYSCALL", b"OpenSSL SSL_read: Connection was reset, errno 10054",
                 b"OpenSSL SSL_read: SSL_ERROR_SYSCALL, errno 104"):
        err = b"fatal: unable to access 'https://example.invalid/x.git/': " + text + b"\n"
        _engine, calls, _err, _pinned, exc = _resolve_with([(128, err), (0, b""), (0, b"")])
        assert exc is None, (text, exc)
        assert len([c for c in calls if "fetch" in c]) == 2, (text, calls)


def test_clone_retries_a_reset_then_succeeds() -> None:
    from unittest import mock
    engine = _load_engine()
    run, calls = _stub_runs([(128, _RESET_STDERR), (0, b"")])
    with mock.patch.object(engine, "NETWORK_RETRY_DELAYS", (0, 0), create=True), \
            mock.patch.object(subprocess, "run", run), contextlib.redirect_stderr(io.StringIO()):
        engine._clone_no_checkout("https://example.invalid/x.git", Path(tempfile.gettempdir()) / "nr-clone-x")
    assert len(calls) == 2, calls


def test_network_retry_converts_a_timeout_into_a_retryable_failure() -> None:
    from unittest import mock
    engine = _load_engine()
    calls = []

    def _run():
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=120)
        return subprocess.CompletedProcess(["git", "fetch"], 0, stdout=b"", stderr=b"")

    with mock.patch.object(engine, "NETWORK_RETRY_DELAYS", (0, 0)), contextlib.redirect_stderr(io.StringIO()):
        result = engine.run_with_network_retry("git fetch", _run)
    assert result.returncode == 0 and len(calls) == 2, calls
    calls.clear()

    def _always():
        calls.append(1)
        raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=120)

    with mock.patch.object(engine, "NETWORK_RETRY_DELAYS", (0, 0)), contextlib.redirect_stderr(io.StringIO()):
        result = engine.run_with_network_retry("git fetch", _always, retryable=False)
    assert result.returncode == 124 and b"timed out after 120 s" in result.stderr and len(calls) == 1, (result, calls)


def test_remote_git_carries_the_network_timeout_and_local_git_does_not() -> None:
    from unittest import mock
    engine = _load_engine()
    seen = []

    def _run(cmd, *args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    with mock.patch.object(subprocess, "run", _run):
        engine._git(Path("cache"), ["fetch", "origin"], check=False)
        engine._git(Path("cache"), ["status"], check=False)
    assert seen == [engine.NETWORK_CALL_TIMEOUT_S, None], seen


def test_pull_moves_a_tracked_rows_verdict_to_the_copy_it_writes() -> None:
    rel = ".claude/reference/repulled.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            rel: {"upstream": b"v2\n", "local": b"v1\n", "hash": _sha(b"v1\n"), "judged": _judged_push(b"v1\n")},
        })
        result = _run_baseline(root, baseline, "pull")
        assert result.returncode == 0, _out(result)
        row = _load_lock(root)["files"][rel]
        assert row["hash"] == row["judged"]["sha"] == _sha(b"v2\n"), row
        assert row["judged"]["verdict"] == "push", row


def test_pull_updates_the_running_engine_first_and_reruns_with_it() -> None:
    engine_rel = ".claude/tools/baseline_sync.py"
    old = ENGINE.read_bytes()
    anchor = b"_TOOLS_DIR = Path(__file__).resolve().parent\n"
    new = old.replace(anchor, anchor + b'Path.cwd().joinpath(".new_engine_ran").write_text("1")\n', 1)
    assert new != old
    other = ".claude/reference/other.md"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {
            engine_rel: {"upstream": new, "local": old, "hash": _sha(old)},
            other: {"upstream": b"new other\n", "local": b"old other\n", "hash": _sha(b"old other\n")},
        })
        for module in ENGINE.parent.glob("*.py"):
            if module.name != "baseline_sync.py":
                shutil.copy(module, root / ".claude" / "tools" / module.name)
        _commit(root, "engine siblings")
        result = subprocess.run([sys.executable, str(root / engine_rel), "pull", "--baseline-dir", str(baseline)],
                                cwd=root, env=_env(root), stdin=subprocess.DEVNULL, capture_output=True)
        out = _out(result)
        assert result.returncode == 0, out
        assert (root / engine_rel).read_bytes() == new, "the engine row was not pulled"
        assert (root / ".new_engine_ran").exists(), "the rest of the pull did not run on the pulled engine: " + out
        assert (root / other).read_bytes() == b"new other\n", out


def test_pull_stops_when_the_engine_pull_reports_a_conflict() -> None:
    # v2_pull's return code is the engine pull's verdict: a nonzero one must end the bare pull before the
    # re-exec. The stub is the seam's contract (v2_pull with merge=False returns 0 on today's inputs).
    engine = _load_engine()
    ENGINE_RELPATH_TAIL = "baseline_sync.py"
    with _fixture() as path:
        root, baseline, _r, _c = _v2_fixture(path, {"other.md": {"upstream": b"new other\n"}})
        reexec = []
        real_run = engine.subprocess.run
        real_pending, real_pull = engine._pending_engine_rows, engine.v2_pull
        engine._pending_engine_rows = lambda *a, **k: [engine.ENGINE_RELPATH]
        engine.v2_pull = lambda *a, **k: 1
        def recording_run(cmd, *a, **k):
            if ENGINE_RELPATH_TAIL in " ".join(map(str, cmd)):
                reexec.append(cmd)
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *a, **k)

        engine.subprocess.run = recording_run
        try:
            with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()) as out:
                code = engine.main(["pull", "--baseline-dir", str(baseline)])
        finally:
            engine.subprocess.run = real_run
            engine._pending_engine_rows, engine.v2_pull = real_pending, real_pull
        assert code == 1, "a conflicted engine pull exited %s" % code
        assert not reexec, "the rest of the pull ran on the old engine"
        assert "engine updated" not in out.getvalue(), out.getvalue()


def test_pull_delivers_a_binary_row_byte_for_byte() -> None:
    # A file holding NUL bytes is binary: no line-ending rewrite, no UTF-8 round trip, no substitution.
    name_token = "{{" + "PROJECT_NAME" + "}}"
    binary = b"\x00asm\x01\x00\x00\x00\r\n\r\xff\xfe\x80" + name_token.encode() + bytes(range(256))
    rel = ".claude/tools/grammar.wasm"
    with _fixture() as path:
        root, baseline, _remote_path, _commit_sha = _v2_fixture(path, {rel: {"upstream": binary, "local": None}})
        lock = _load_lock(root)
        lock["substitutions"] = {name_token: "Game"}
        _write_json(root / ".claude" / "baseline.lock.json", lock)
        _commit(root, "consumer substitutions")
        pulled = _run_baseline(root, baseline, "pull", rel)
        assert pulled.returncode == 0, pulled.stderr.decode(errors="replace")
        assert (root / rel).read_bytes() == binary, "pull changed the bytes of a binary row"
        again = _run_baseline(root, baseline, "check")
        assert _load_lock(root)["files"][rel]["hash"] == hashlib.sha256(binary).hexdigest(), "a binary row's hash is the sha of its bytes"
        strict = _run_baseline(root, baseline, "check", "--strict")
        assert b"needs-judgment" not in strict.stdout, strict.stdout
        report = again.stdout.decode(errors="replace")
        assert "in-sync: 1 file(s)" in report and "placeholder" not in report and "modified" not in report, report


def main() -> int:
    cases = [
        test_pull_moves_a_tracked_rows_verdict_to_the_copy_it_writes,
        test_pull_merge_refuses_a_diverged_binary_row_and_leaves_it_untouched,
        test_pull_updates_the_running_engine_first_and_reruns_with_it,
        test_pull_stops_when_the_engine_pull_reports_a_conflict,
        test_pull_delivers_a_binary_row_byte_for_byte,
        test_unproven_tools_binds_every_tool_in_one_scan,
        test_carry_refuses_a_new_tool_whose_proof_is_local,
        test_carry_refuses_a_citer_whose_named_dependency_is_not_carried,
        test_dependency_check_failure_refuses_the_push,
        test_pull_chmods_a_100755_entry_and_leaves_a_100644_entry,
        test_pull_keeps_the_posix_exec_bit_of_a_100755_entry,
        test_pull_prints_each_written_row_the_gitignore_matches,
        test_j3_push_judge_refuses_an_identity_hit_and_accept_hit_allows_it,
        test_triage_lists_every_identity_hit_the_push_scan_would_refuse,
        test_bare_pull_refreshes_a_lock_stale_row,
        test_paths_unshipped_lists_every_push_row_the_baseline_lacks_whenever_judged,
        test_paths_unshipped_names_a_push_row_edited_after_its_verdict,
        test_push_refuses_a_literal_placeholder_a_pull_would_render,
        test_j3_push_judge_refuses_an_unlayered_row_upstream_lacks,
        test_j3_write_prints_the_layer_closure_it_adds,
        test_j3_keep_local_on_an_unshipped_tracked_row_sets_local,
        test_j3_relpath_with_control_or_edge_whitespace_refuses,
        test_j3_layer_on_an_existing_row_needs_relayer,
        test_pull_retires_unmodified_file_removed_upstream,
        test_explicit_pull_keeps_modified_file_removed_upstream_as_local,
        test_pull_drops_row_of_file_removed_upstream_and_missing_locally,
        test_pull_merge_reports_a_dirty_diverged_row_and_pulls_the_rest,
        test_resolve_installs_a_marker_free_copy_and_refuses_one_with_markers,
        test_resolve_refuses_when_upstream_moved_or_the_file_changed_since_the_merge,
        test_a_tracked_row_never_published_is_unpublished_and_pull_keeps_it,
        test_pull_merge_combines_both_sides_of_a_diverged_row,
        test_pull_merge_reports_a_conflict_and_leaves_the_row_untouched,
        test_check_reports_layer_closure_and_strict_fails,
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
        test_bare_pull_names_a_diverged_row_it_holds,
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
        test_pull_seed_project_settings_records_layer_overlay_inputs,
        test_migrate_derives_project_settings_over_layer_overlays,
        test_pull_keeps_existing_seed_file_says_kept,
        test_v2_check_strict_clears_only_a_current_keep_local_verdict,
        test_v2_memory_provenance_keys_never_make_a_row_differ,
        test_v2_pull_writes_memory_without_provenance_keys,
        test_v2_provenance_strip_leaves_non_memory_files_alone,
        test_carry_walks_the_range_and_leaves_a_peer_row_alone,
        test_carry_lists_a_row_the_range_adds_deletes_or_leaves_alone,
        test_carry_names_a_broken_chain,
        test_carry_refuses_an_identity_hit_and_a_layerless_new_row,
        test_carry_usage_errors_exit_two_naming_the_flag,
        test_check_strict_lists_each_tool_without_a_shipped_proof,
        test_check_strict_counts_a_local_proof_as_no_proof,
        test_judge_push_refuses_a_new_tool_whose_proof_is_local,
        test_judge_push_allows_a_new_tool_with_a_shipped_proof,
        test_judge_push_allows_a_new_tool_and_its_new_proof_in_one_range,
        test_judge_push_counts_a_proof_judged_push_at_its_current_sha,
        test_judge_push_refuses_a_citer_whose_dependency_does_not_ship,
        test_judge_push_allows_a_citer_whose_dependency_ships_or_is_judged_push,
        test_judge_push_allows_a_citer_and_its_local_dependency_in_one_call,
        test_judge_push_passes_when_the_baseline_copy_already_cites_the_modified_dependency,
        test_judge_push_refuses_a_citation_the_change_adds_to_a_modified_dependency,
        test_judge_push_refuses_a_local_dependency_even_when_the_baseline_copy_cites_it,
        test_judge_push_keeps_the_rule_for_a_citer_new_to_the_baseline,
        test_carry_refuses_a_row_whose_dependency_is_local,
        test_fetch_retries_a_connection_reset_then_succeeds,
        test_fetch_does_not_retry_repository_not_found,
        test_a_non_network_subcommand_is_not_retried_on_reset_looking_stderr,
        test_network_retry_converts_a_timeout_into_a_retryable_failure,
        test_remote_git_carries_the_network_timeout_and_local_git_does_not,
        test_three_transient_fetch_failures_raise_the_existing_error,
        test_clone_retries_a_reset_then_succeeds,
        test_fetch_does_not_retry_a_permanent_tls_certificate_failure,
        test_fetch_still_retries_a_transient_tls_failure,
        test_intent_to_add_path_reads_its_working_bytes,
        test_many_paths_read_without_one_oversized_command_line,
        test_relpaths_after_an_option_parse_on_every_python,
        test_check_strict_counts_a_proof_above_its_tools_layer_as_no_proof,
        test_check_strict_names_a_shipped_row_whose_layer_differs_from_the_manifest,
        test_check_text_names_every_strict_finding,
        test_carry_leaves_a_row_judged_keep_local_alone,
        test_carry_starts_from_a_pulled_row_whose_judged_sha_is_older,
    ]
    return run_cases(cases)


if __name__ == "__main__":
    sys.exit(main())
