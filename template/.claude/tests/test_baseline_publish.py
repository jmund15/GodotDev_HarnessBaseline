#!/usr/bin/env python3
"""Re-runnable S1 proof for the eight-step local publication tracer.

    python3 .claude/tests/test_baseline_publish.py
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
import threading
from pathlib import Path

PUBLISH = Path(__file__).resolve().parents[1] / "tools" / "baseline_publish.py"
SYNC = PUBLISH.parent / "baseline_sync.py"


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(cwd: Path | None, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=_env(cwd or Path.cwd()),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return result.stdout


def _run_sync(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Invoke the real `baseline_sync.py` engine CLI (`check`/`pull`) against `root`,
    for defect-2 proofs that a `--from-worktree` publication's step 7/8 behavior is
    visible through the consumer-facing engine commands, not just the journal."""
    return subprocess.run(
        [sys.executable, str(SYNC), *args], cwd=root, env=_env(root),
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
    """A valid `project_subsystems` adaptation contract (Design §8), so a consumer root
    that already has a lock does not trip `publish`'s new refusal incidentally."""
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
    path = Path(tempfile.mkdtemp(prefix="baseline_publish_"))
    try:
        yield path
    finally:
        _remove(path)


def _field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key)


def _load_publish():
    assert PUBLISH.exists(), "publication target is missing"
    spec = importlib.util.spec_from_file_location("baseline_publish_under_test", PUBLISH)
    assert spec and spec.loader, "publication target could not be loaded"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AssertionError("publication target failed to load: %s" % exc) from exc
    return module


# ---------------------------------------------------------------------------
# S5 fixture helpers: a local bare remote, a fake `gh` on PATH, and the minimal
# in-tree tool stubs step 5 (validate) shells out to.
# ---------------------------------------------------------------------------

STUB_GEN_MANIFEST = b"#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
STUB_AUDIT_BASELINE = b"#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
STUB_HARNESS_TESTS = b"""#!/usr/bin/env python3
import fnmatch
import os
import sys

_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "*.sh", "*.ps1")


def discover(tests_dir):
    if not os.path.isdir(tests_dir):
        return []
    return [
        os.path.join(tests_dir, name)
        for name in sorted(os.listdir(tests_dir))
        if os.path.isfile(os.path.join(tests_dir, name))
        and any(fnmatch.fnmatch(name, pattern) for pattern in _PATTERNS)
    ]


if __name__ == "__main__":
    sys.exit(0)
"""
# A discovery function that mirrors the real gotcha this repo guards against: it filters
# out any path with a `.claude` segment, so it silently returns zero proofs when run from
# inside a nested `.claude/.cache/baseline-worktrees/<id>/` worktree.
STUB_HARNESS_TESTS_VOIDED_DISCOVERY = b"""#!/usr/bin/env python3
import fnmatch
import os
import sys

_PATTERNS = ("test_*.py", "*_test.py", "*_test.js", "*.sh", "*.ps1")


def discover(tests_dir):
    if ".claude" in os.path.abspath(tests_dir).replace(os.sep, "/").split("/"):
        return []
    if not os.path.isdir(tests_dir):
        return []
    return [
        os.path.join(tests_dir, name)
        for name in sorted(os.listdir(tests_dir))
        if os.path.isfile(os.path.join(tests_dir, name))
        and any(fnmatch.fnmatch(name, pattern) for pattern in _PATTERNS)
    ]


if __name__ == "__main__":
    sys.exit(0)
"""


def _write_baseline_stubs(work: Path, *, voided_discovery: bool = False) -> None:
    """Seed a baseline `work` checkout with the minimal in-tree tools step 5 shells out
    to, so a fresh worktree materialized at its pinned commit can run `validate` end to
    end without a real baseline checkout."""
    _write(work / "tools" / "gen_manifest.py", STUB_GEN_MANIFEST)
    _write(work / "tools" / "audit_baseline.py", STUB_AUDIT_BASELINE)
    harness = STUB_HARNESS_TESTS_VOIDED_DISCOVERY if voided_discovery else STUB_HARNESS_TESTS
    _write(work / "template" / ".claude" / "scripts" / "harness_tests.py", harness)


@contextlib.contextmanager
def _patched_env(overrides: dict[str, str]):
    saved = os.environ.copy()
    os.environ.update(overrides)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


FAKE_GH_SOURCE = '''#!/usr/bin/env python3
"""Fake `gh` for baseline publish proofs: a local stand-in that stores PR state in a JSON
file (`GH_FAKE_STATE`) and performs `pr merge` against a local bare remote by reusing
`baseline_publish._merge_local`, loaded from `GH_FAKE_TOOLS_DIR`. Env toggles simulate
specific failure shapes a real GitHub PR lifecycle can produce:

    GH_FAKE_FAIL_STEP=create|checks|merge   that gh subcommand exits 1 immediately (`pr checks --json` reports FAILURE)
    GH_FAKE_MAIN_MOVE_ON_CHECKS=1           `pr checks` pushes a peer commit to main first
    GH_FAKE_NO_CHECKS_CALLS=N               the first N `pr checks` calls report no checks yet (exit 1)
    GH_FAKE_WATCH_DROPS=N                   the first N `pr checks --watch` calls lose the connection
                                            (exit 1) while `--json` still reports the check PENDING
    GH_FAKE_FAIL_AFTER_MERGE=1              `pr merge` performs the real merge, then exits 1
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

STATE_PATH = Path(os.environ["GH_FAKE_STATE"])
TOOLS_DIR = os.environ.get("GH_FAKE_TOOLS_DIR", "")


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"prs": {}, "next_number": 1}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _current_branch(cwd) -> str:
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _load_publish_module():
    spec = importlib.util.spec_from_file_location(
        "baseline_publish_gh_fixture", os.path.join(TOOLS_DIR, "baseline_publish.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find(state, url):
    return next((r for r in state["prs"].values() if r["url"] == url), None)


def main(argv):
    cwd = os.getcwd()
    if argv[:1] != ["pr"]:
        print("fake gh: unsupported command: " + " ".join(argv), file=sys.stderr)
        return 1
    sub = argv[1] if len(argv) > 1 else ""
    state = _load_state()

    if sub == "create":
        if os.environ.get("GH_FAKE_FAIL_STEP") == "create":
            print("fake gh: injected create failure", file=sys.stderr)
            return 1
        # Real gh infers the head from the local branch's upstream. A publish worktree's
        # branch has none (it pushes HEAD:refs/heads/<branch>), so real gh aborts here.
        if "--head" not in argv or "--base" not in argv:
            print("aborted: you must first push the current branch to a remote, or use the --head flag",
                  file=sys.stderr)
            return 1
        branch = argv[argv.index("--head") + 1]
        number = state["next_number"]
        url = "https://example.invalid/pr/%d" % number
        state["prs"][branch] = {
            "url": url, "state": "OPEN", "mergeCommit": None,
            "number": number, "branch": branch,
        }
        state["next_number"] = number + 1
        _save_state(state)
        print(url)
        return 0

    if sub == "list":
        head = argv[argv.index("--head") + 1] if "--head" in argv else None
        matches = [
            {"url": r["url"], "state": r["state"]}
            for r in state["prs"].values() if head is None or r["branch"] == head
        ]
        print(json.dumps(matches))
        return 0

    if sub == "view":
        url = argv[2] if len(argv) > 2 else None
        record = _find(state, url)
        if record is None:
            print("fake gh: no such PR: %s" % url, file=sys.stderr)
            return 1
        merge_commit = {"oid": record["mergeCommit"]} if record["mergeCommit"] else None
        print(json.dumps({"state": record["state"], "mergeCommit": merge_commit, "url": record["url"]}))
        return 0

    if sub == "checks":
        if "--json" in argv:
            # Real gh shape (read from a live PR): one object per check, `link` is the job URL.
            failed = os.environ.get("GH_FAKE_FAIL_STEP") == "checks"
            drops = int(os.environ.get("GH_FAKE_WATCH_DROPS", "0") or 0)
            dropped = drops and state.get("watch_calls", 0) <= drops
            print(json.dumps([{"name": "baseline",
                               "state": "PENDING" if dropped else ("FAILURE" if failed else "SUCCESS"),
                               "workflow": "baseline",
                               "link": "https://example.invalid/actions/runs/77/job/1"}]))
            return 0
        pending = int(os.environ.get("GH_FAKE_NO_CHECKS_CALLS", "0") or 0)
        state["checks_calls"] = state.get("checks_calls", 0) + 1
        _save_state(state)
        if state["checks_calls"] <= pending:
            # Real gh right after `pr create`: the workflow run is not registered yet.
            print("no checks reported on the '%s' branch" % _current_branch(cwd), file=sys.stderr)
            return 1
        drops = int(os.environ.get("GH_FAKE_WATCH_DROPS", "0") or 0)
        state["watch_calls"] = state.get("watch_calls", 0) + 1
        _save_state(state)
        if state["watch_calls"] <= drops:
            print('Post "https://api.github.com/graphql": read tcp: connection timed out', file=sys.stderr)
            return 1
        if os.environ.get("GH_FAKE_FAIL_STEP") == "checks":
            print("fake gh: injected checks failure", file=sys.stderr)
            return 1
        if os.environ.get("GH_FAKE_MAIN_MOVE_ON_CHECKS") == "1":
            remote = _git(cwd, "remote", "get-url", "origin").stdout.strip()
            scratch = STATE_PATH.parent / "gh-fake-peer-clone"
            subprocess.run(["git", "clone", "-q", remote, str(scratch)], check=True)
            subprocess.run(["git", "-C", str(scratch), "config", "user.email", "peer@invalid"], check=True)
            subprocess.run(["git", "-C", str(scratch), "config", "user.name", "peer"], check=True)
            (scratch / "PEER_MOVE.txt").write_text("peer\\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(scratch), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(scratch), "commit", "-q", "-m", "peer publish"], check=True)
            subprocess.run(["git", "-C", str(scratch), "push", "-q", "origin", "HEAD:refs/heads/main"], check=True)
        return 0

    if sub == "merge":
        url = argv[2] if len(argv) > 2 else None
        record = _find(state, url)
        if record is None:
            print("fake gh: no such PR: %s" % url, file=sys.stderr)
            return 1
        if os.environ.get("GH_FAKE_FAIL_STEP") == "merge":
            print("fake gh: injected merge failure", file=sys.stderr)
            return 1
        expected_head = argv[argv.index("--match-head-commit") + 1] if "--match-head-commit" in argv else None
        remote = _git(cwd, "remote", "get-url", "origin").stdout.strip()
        module = _load_publish_module()
        merge_parent = STATE_PATH.parent / "gh-fake-merge-work"
        merge_parent.mkdir(parents=True, exist_ok=True)
        try:
            merged_sha = module._merge_local(remote, record["branch"], expected_head, merge_parent)
        except module.PublishError as exc:
            print("fake gh: %s" % exc, file=sys.stderr)
            return 1
        record["state"] = "MERGED"
        record["mergeCommit"] = merged_sha
        _save_state(state)
        if os.environ.get("GH_FAKE_FAIL_AFTER_MERGE") == "1":
            print("fake gh: injected post-merge failure", file=sys.stderr)
            return 1
        return 0

    print("fake gh: unsupported pr subcommand: %s" % sub, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def _install_fake_gh(path: Path) -> dict[str, str]:
    """Write a fake `gh` (plus a Windows `.cmd`/POSIX shell launcher) into `path/bin`, and
    return the environment overrides (`PATH`, `GH_FAKE_STATE`, `GH_FAKE_TOOLS_DIR`) a test
    installs via `_patched_env` before calling `publish.run(...)` in-process."""
    bin_dir = path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "fake_gh.py"
    _write(script_path, FAKE_GH_SOURCE.encode("utf-8"))
    if os.name == "nt":
        launcher = bin_dir / "gh.cmd"
        content = "@echo off\r\n\"%s\" \"%s\" %%*\r\n" % (sys.executable, script_path)
        launcher.write_bytes(content.encode("utf-8"))
    else:
        launcher = bin_dir / "gh"
        content = "#!/bin/sh\nexec \"%s\" \"%s\" \"$@\"\n" % (sys.executable, script_path)
        launcher.write_bytes(content.encode("utf-8"))
        os.chmod(launcher, 0o755)
    state_path = path / "gh_state.json"
    return {
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "GH_FAKE_STATE": str(state_path),
        "GH_FAKE_TOOLS_DIR": str(PUBLISH.parent),
    }


def _remote(path: Path) -> Path:
    remote = path / "remote.git"
    _git(path, "init", "--bare", "-q", str(remote))
    return remote


def _push_main(work: Path, remote: Path) -> None:
    _git(work, "remote", "add", "origin", remote.as_uri())
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")


def _remote_main_sha(remote: Path) -> str:
    return _git(remote, "show-ref", "-s", "refs/heads/main").decode().strip()


def _seed_commit_fixture(path: Path, rel: str, baseline_content: bytes, local_content: bytes,
                          *, placeholder_value: str = "fixture") -> tuple[Path, str, Path]:
    """A pushed baseline (with tool stubs) plus a consumer with one `push`-judged row,
    ready for `--from-commit`. Returns (remote, baseline_sha, consumer_root)."""
    placeholder = "{{" + "PROJECT_NAME" + "}}"
    published_content = local_content.replace(placeholder_value.encode(), placeholder.encode())
    remote = _remote(path)
    baseline = path / "baseline-work"
    _init_repo(baseline)
    _write(baseline / "template" / rel, baseline_content)
    _write_baseline_stubs(baseline)
    manifest = {"version": 2, "files": [{"path": rel, "layer": "pure", "sync": "auto"}]}
    _write(baseline / "baseline.manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    baseline_commit = _commit(baseline, "seed")
    _push_main(baseline, remote)

    root = path / "consumer"
    _init_repo(root)
    _seed_project_subsystems(root)
    _write(root / rel, local_content)
    lock = {
        "baseline_repo": remote.as_uri(),
        "baseline_ref": "main",
        "synced_commit": baseline_commit,
        "profile": "pure",
        "substitutions": {placeholder: placeholder_value},
        "files": {
            rel: {
                "status": "tracked",
                "hash": _sha(local_content),
                "layer": "pure",
                "judged": {
                    "sha": _sha(local_content),
                    "verdict": "push",
                    "at": "2026-01-01T00:00:00Z",
                },
            },
            # The `_seed_project_subsystems` pair above is a committed, consumer-local file
            # pair -- a "commit" source's step-1 classify (`_candidates`) flags any committed
            # `.claude/` path with no lock row, so both need one to keep these fixtures a
            # clean baseline for step-1 rather than a pre-existing candidates failure.
            ".claude/skills/project_subsystems/adaptation.json": {"status": "local", "layer": "pure", "judged": None},
            ".claude/skills/project_subsystems/SKILL.md": {"status": "local", "layer": "pure", "judged": None},
        },
    }
    _write(root / ".claude" / "baseline.lock.json", (json.dumps(lock, indent=2) + "\n").encode())
    _commit(root, "consumer")
    return remote, baseline_commit, root


def _seed_bare_baseline(path: Path, *, voided_discovery: bool = False) -> tuple[Path, str]:
    """A pushed baseline repo with tool stubs and an empty manifest, for `--from-worktree`
    fixtures that need no pre-existing lock rows. Returns (remote, baseline_sha)."""
    remote = _remote(path)
    work = path / "baseline-work"
    _init_repo(work)
    _write_baseline_stubs(work, voided_discovery=voided_discovery)
    manifest = {"version": 2, "files": []}
    _write(work / "baseline.manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    baseline_commit = _commit(work, "seed")
    _push_main(work, remote)
    return remote, baseline_commit


def _seed_worktree_root(path: Path, remote: Path, baseline_commit: str) -> Path:
    """A minimal consumer root for `--from-worktree` tests: a lock naming `remote`, no
    lock rows required (a worktree source is not driven by a consumer's own lock rows)."""
    root = path / "consumer"
    _init_repo(root)
    _seed_project_subsystems(root)
    lock = {
        "baseline_repo": remote.as_uri(),
        "baseline_ref": "main",
        "synced_commit": baseline_commit,
        "profile": "pure",
        "substitutions": {},
        "files": {},
    }
    _write(root / ".claude" / "baseline.lock.json", (json.dumps(lock, indent=2) + "\n").encode())
    _commit(root, "consumer root")
    return root


def _make_author_worktree(path: Path, root: Path, remote: Path, name: str) -> tuple[Path, Path, str]:
    """Clone `remote` into `root`'s baseline cache (reusing it across calls for the same
    root) and add a linked worktree at the resolved pinned sha, mirroring `author start`
    without needing a real session id."""
    cache = root / ".claude" / ".cache" / "baseline-repo"
    if not cache.exists():
        _git(root.parent, "clone", "--no-checkout", "-q", str(remote.as_uri()), str(cache))
        _git(cache, "config", "user.email", "author@invalid")
        _git(cache, "config", "user.name", "author")
    _git(cache, "fetch", "-q", "origin", "main")
    pinned = _git(cache, "rev-parse", "FETCH_HEAD").decode().strip()
    worktree_dir = root / ".claude" / ".cache" / "baseline-worktrees"
    worktree_dir.mkdir(parents=True, exist_ok=True)
    worktree = worktree_dir / name
    branch = "author/" + name
    _git(cache, "worktree", "add", "-q", "-b", branch, str(worktree), pinned)
    return cache, worktree, pinned


def test_tracer_publishes_one_row_to_fixture_remote() -> None:
    rel = ".claude/tools/fixture.py"
    placeholder = "{{" + "PROJECT_NAME" + "}}"
    baseline_content = b"value = 'old'\n"
    published_content = ("value = '" + placeholder + "'\n").encode()
    local_content = b"value = 'fixture'\n"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, baseline_content, local_content)
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(
                root,
                {"kind": "commit", "repo": str(root), "commit": source_commit},
                [rel],
                [],
                False,
                True,
                None,
            )
        steps = _field(journal, "steps")
        assert [step["name"] for step in steps] == [
            "collect", "classify", "materialize", "scrub",
            "validate", "publish", "update-lock", "check",
        ]
        assert all(step["status"] == "green" for step in steps), steps
        assert _git(remote, "show", "main:" + "template/" + rel) == published_content

        updated = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert updated["files"][rel]["hash"] == _sha(local_content)
        assert updated["synced_commit"] == _field(journal, "baseline_sha_after")
        journals = sorted((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert journals, "publication journal was not written"
        for journal_path in journals:
            assert b"\r" not in journal_path.read_bytes()
        assert _field(journal, "pr_url"), "step 6 did not record a PR url"


def test_peer_edit_after_the_source_commit_leaves_step_eight_green() -> None:
    """Step 8 checks what was published -- the source commit's bytes -- not the working
    tree. In a shared checkout a peer may edit a published row after the source commit;
    that edit is new local work for `triage`, not a failed publication."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(
            path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()
        _write(root / rel, b"value = 'a peer edited this after the commit'\n")

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(
                root, {"kind": "commit", "repo": str(root), "commit": source_commit},
                [rel], [], False, True, None,
            )
        steps = _field(journal, "steps")
        assert all(step["status"] == "green" for step in steps), steps
        assert (root / rel).read_bytes() == b"value = 'a peer edited this after the commit'\n"


def test_collect_without_rows_takes_only_push_verdicts() -> None:
    pushed = ".claude/tools/pushed.py"
    unjudged = ".claude/tools/unjudged.py"
    with _fixture() as path:
        root = path / "consumer"
        _init_repo(root)
        _write(root / pushed, b"pushed = 1\n")
        _write(root / unjudged, b"unjudged = 1\n")
        commit = _commit(root, "consumer")
        lock = {"files": {
            pushed: {"status": "tracked", "hash": "x",
                     "judged": {"sha": _sha(b"pushed = 1\n"), "verdict": "push", "at": "t"}},
            unjudged: {"status": "tracked", "hash": "y"},
        }}
        publish = _load_publish()
        source = {"kind": "commit", "repo": str(root), "commit": commit}
        records = publish._collect(root, source, None, lock, None, "unused")
        assert [r["relpath"] for r in records] == [pushed]
        try:
            publish._collect(root, source, [unjudged], lock, None, "unused")
        except publish.PublishError:
            pass
        else:
            raise AssertionError("a listed row without a push verdict was collected")


def test_materialize_writes_source_bytes_unsubstituted() -> None:
    rel = ".claude/tools/fixture.py"
    local_content = b"value = 'fixture'\n"
    with _fixture() as path:
        cache = path / "cache"
        _init_repo(cache)
        _write(cache / "template" / rel, b"value = 'old'\n")
        baseline_sha = _commit(cache, "seed")
        root = path / "consumer"
        _init_repo(root)
        _write(root / rel, local_content)
        commit = _commit(root, "consumer")
        publish = _load_publish()
        worktree = path / "publish-worktree"
        record = {"relpath": rel, "source_path": rel, "dest_path": "template/" + rel,
                  "kind": "lock", "op": "A"}
        publish._materialize(
            root, {"kind": "commit", "repo": str(root), "commit": commit}, [record],
            {"substitutions": {}}, cache, baseline_sha, worktree, "publish/x",
        )
        assert (worktree / "template" / rel).read_bytes() == local_content


def test_local_merge_refuses_remote_url() -> None:
    publish = _load_publish()
    with _fixture() as path:
        for remote in ("https://example.invalid/baseline.git", "git@example.invalid:o/baseline.git"):
            try:
                publish._merge_local(remote, "publish/x", "0" * 40, path)
            except publish.PublishError as exc:
                assert "local" in str(exc), str(exc)
            else:
                raise AssertionError("local merge accepted a network remote: " + remote)
        assert not (path / "merge-checkout").exists()


def test_no_ci_refuses_when_workflow_exists() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        clone = path / "amend-clone"
        _git(path, "clone", "-q", str(remote), str(clone))
        _write(clone / ".github" / "workflows" / "baseline.yml", b"name: baseline\n")
        _git(clone, "config", "user.email", "x@invalid")
        _git(clone, "config", "user.name", "x")
        _commit(clone, "add ci")
        _git(clone, "push", "-q", "origin", "HEAD:refs/heads/main")

        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        try:
            publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                        [rel], [], False, True, None)
        except publish.PublishError as exc:
            assert "--no-ci" in str(exc), str(exc)
        else:
            raise AssertionError("expected --no-ci to refuse when a workflow exists at the pinned commit")
        assert not list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json")), (
            "a refused --no-ci should not even start a journal"
        )


def test_step_failures_1_through_5_leave_fixture_main_unchanged() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        before_main = _remote_main_sha(remote)
        journal_dir = root / ".claude" / ".cache" / "baseline-publish"

        def _reset_journal_dir() -> None:
            for entry in journal_dir.iterdir():
                if entry.is_dir():
                    _remove(entry)
                else:
                    entry.unlink()

        def _assert_red_and_clean(step_index: int) -> None:
            journals = sorted(journal_dir.glob("*.json"))
            assert journals, "no journal written for step %d" % step_index
            data = json.loads(journals[-1].read_text(encoding="utf-8"))
            assert data["steps"][step_index]["status"] == "red", data["steps"]
            for prior in data["steps"][:step_index]:
                assert prior["status"] == "green", data["steps"]
            assert _remote_main_sha(remote) == before_main, "fixture main moved on a failed publish"
            _reset_journal_dir()

        # step 0 (collect): a requested row with no lock entry at all.
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [".claude/tools/does_not_exist.py"], [], False, True, None)
            except publish.PublishError:
                pass
            else:
                raise AssertionError("expected step 0 (collect) to fail")
        _assert_red_and_clean(0)

        # steps 1-4 (classify/materialize/scrub/validate): inject a failure into each
        # step's own worker function on a freshly loaded module, one at a time.
        for step_index, attr in ((1, "_classify"), (2, "_materialize"), (3, "_scrub"), (4, "_validate")):
            publish = _load_publish()

            def _boom(*_a, _step_index=step_index, **_k):
                raise publish.PublishError("injected failure at step %d" % _step_index)

            setattr(publish, attr, _boom)
            env = _install_fake_gh(path)
            with _patched_env(env):
                try:
                    publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                [rel], [], False, True, None)
                except publish.PublishError:
                    pass
                else:
                    raise AssertionError("expected step %d to fail" % step_index)
            _assert_red_and_clean(step_index)


def test_dry_run_then_resume_continues_at_step_six_and_records_owner_confirmed() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], True, True, None)
        assert journal["dry_run"] is True
        for step in journal["steps"][:5]:
            assert step["status"] == "green", journal["steps"]
        for step in journal["steps"][5:]:
            assert step["status"] == "pending", journal["steps"]
        assert journal["owner_confirmed_at"] is None

        publish2 = _load_publish()
        with _patched_env(env):
            resumed = publish2.run(root, None, None, [], False, False, journal["id"])
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        assert resumed["dry_run"] is False
        assert resumed["owner_confirmed_at"] is not None
        assert _git(remote, "show", "main:template/" + rel)


def test_a_red_dry_run_is_redone_as_a_dry_run_before_the_owner_confirms() -> None:
    """The owner confirms a GREEN dry run. A dry-run journal red at steps 1-5 must not publish
    on `--resume`: plain resume refuses it, and `--resume --dry-run` redoes steps 1-5 and stops,
    still a dry run with no confirmation recorded. The next plain resume publishes."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        before_main = _remote_main_sha(remote)
        env = _install_fake_gh(path)
        publish = _load_publish()

        def _boom(*_a, **_k):
            raise publish.PublishError("injected scrub failure")

        publish._scrub = _boom
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit}, [rel], [], True, True, None)
            except publish.PublishError:
                pass
        journal_id = sorted((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))[-1].stem

        publish = _load_publish()
        with _patched_env(env):
            try:
                publish.run(root, None, None, [], False, False, journal_id)
            except publish.PublishError as exc:
                assert "--dry-run" in str(exc), exc
            else:
                raise AssertionError("a red dry-run journal published on plain --resume")
        assert _remote_main_sha(remote) == before_main

        with _patched_env(env):
            redone = _load_publish().run(root, None, None, [], True, False, journal_id)
        assert all(s["status"] == "green" for s in redone["steps"][:5]), redone["steps"]
        assert all(s["status"] == "pending" for s in redone["steps"][5:]), redone["steps"]
        assert redone["dry_run"] is True and redone["owner_confirmed_at"] is None
        assert _remote_main_sha(remote) == before_main

        with _patched_env(env):
            published = _load_publish().run(root, None, None, [], False, False, journal_id)
        assert all(s["status"] == "green" for s in published["steps"]), published["steps"]
        assert published["owner_confirmed_at"] is not None


def test_a_journal_red_at_classify_resumes_through_classify() -> None:
    """A fresh run with the same inputs refuses and names `--resume`, so a journal that stopped
    at step 2 (classify: an unrowed candidate) must resume. Resume re-runs the failed classify
    gate and makes the worktree step 3 needs; it never skips the gate."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        env = _install_fake_gh(path)
        def _red(module):
            def _red_classify(*_a, **_k):
                raise module.PublishError("source has candidates: injected")
            module._classify = _red_classify
            return module

        publish = _red(_load_publish())
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit}, [rel], [], True, True, None)
            except publish.PublishError:
                pass
        journal_id = sorted((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))[-1].stem

        still_red = _red(_load_publish())
        with _patched_env(env):
            try:
                still_red.run(root, None, None, [], True, False, journal_id)
            except still_red.PublishError:
                pass
            else:
                raise AssertionError("resume skipped a red classify gate")

        with _patched_env(env):
            redone = _load_publish().run(root, None, None, [], True, False, journal_id)
        assert all(s["status"] == "green" for s in redone["steps"][:5]), redone["steps"]
        assert redone["dry_run"] is True


def test_abbreviated_commit_source_resumes_instead_of_deadlocking() -> None:
    """A source given a short SHA -- the form the CLI is actually typed with -- must resume.

    The journal recorded the caller's commit string verbatim while `_current_source_commit`
    resolved it, so the guard compared `rev-parse(X)` against `X` and refused every abbreviated
    source. The same recorded string then matched `_find_matching_journal`, so the identical fresh
    run refused too, naming a resume that could not run: the publication had no open door. Every
    other case here passes `rev-parse HEAD`, which is why this never surfaced.
    """
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(
            path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        short = _git(root, "rev-parse", "--short", "HEAD").decode().strip()
        full = _git(root, "rev-parse", "HEAD").decode().strip()
        assert short != full, "fixture did not produce an abbreviated sha"

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": short},
                                   [rel], [], True, True, None)
        assert journal["dry_run"] is True
        assert journal["source"]["commit"] == full, journal["source"]

        # A journal written before this fix holds the abbreviation verbatim. The resume guard
        # resolves the recorded side too, so an already-stranded publication opens rather than
        # needing a hand-edited journal.
        journal_path = root / ".claude" / ".cache" / "baseline-publish" / (journal["id"] + ".json")
        stored = json.loads(journal_path.read_text(encoding="utf-8"))
        stored["source"]["commit"] = short
        journal_path.write_text(json.dumps(stored, indent=2), encoding="utf-8", newline="\n")

        publish2 = _load_publish()
        with _patched_env(env):
            resumed = publish2.run(root, None, None, [], False, False, journal["id"])
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        assert _git(remote, "show", "main:template/" + rel)


def test_repeat_publish_prints_already_published() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            first = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                 [rel], [], False, True, None)
        assert all(s["status"] == "green" for s in first["steps"])

        publish2 = _load_publish()
        buffer = io.StringIO()
        with _patched_env(env), contextlib.redirect_stdout(buffer):
            second = publish2.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], False, True, None)
        assert ("already published " + first["id"]) in buffer.getvalue(), buffer.getvalue()
        assert second["id"] == first["id"]


def test_repeat_incomplete_same_baseline_refuses_naming_resume() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()

        def _boom(*_a, **_k):
            raise publish.PublishError("injected")

        setattr(publish, "_publish", _boom)
        env = _install_fake_gh(path)
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, True, None)
            except publish.PublishError:
                pass
        journals = list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert len(journals) == 1
        first_id = json.loads(journals[0].read_text(encoding="utf-8"))["id"]

        publish2 = _load_publish()
        with _patched_env(env):
            try:
                publish2.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                             [rel], [], False, True, None)
            except publish2.PublishError as exc:
                assert first_id in str(exc), str(exc)
                assert "--resume" in str(exc), str(exc)
            else:
                raise AssertionError("expected the repeat rule to refuse a same-baseline incomplete match")


def test_repeat_incomplete_older_baseline_records_supersedes() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()

        def _boom(*_a, **_k):
            raise publish.PublishError("injected")

        setattr(publish, "_publish", _boom)
        env = _install_fake_gh(path)
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, True, None)
            except publish.PublishError:
                pass
        journals = list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        first_id = json.loads(journals[0].read_text(encoding="utf-8"))["id"]

        # baseline `main` advances between the failed attempt and the retry, simulating
        # another publish landing meanwhile.
        peer = path / "peer-clone"
        _git(path, "clone", "-q", str(remote), str(peer))
        _write(peer / "PEER.txt", b"peer\n")
        _git(peer, "config", "user.email", "peer@invalid")
        _git(peer, "config", "user.name", "peer")
        _commit(peer, "peer change")
        _git(peer, "push", "-q", "origin", "HEAD:refs/heads/main")

        publish2 = _load_publish()
        with _patched_env(env):
            second = publish2.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], False, True, None)
        assert second.get("supersedes") == first_id, second


def test_resume_finds_and_reuses_pr_after_crash_before_pr_url_written() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"])
        pr_url = journal["pr_url"]
        assert pr_url

        # Simulate a crash between `gh pr create` returning and the journal write that
        # records `pr_url`: blank the field (and the steps it would have gated) directly,
        # leaving the fake gh server-side PR record intact.
        journal_path = root / ".claude" / ".cache" / "baseline-publish" / (journal["id"] + ".json")
        crashed = json.loads(journal_path.read_text(encoding="utf-8"))
        crashed["pr_url"] = None
        for index in (5, 6, 7):
            crashed["steps"][index] = {"name": crashed["steps"][index]["name"], "status": "pending",
                                        "started_at": None, "finished_at": None, "evidence": ""}
        crashed["baseline_sha_after"] = None
        journal_path.write_text(json.dumps(crashed), encoding="utf-8")

        publish2 = _load_publish()
        with _patched_env(env):
            resumed = publish2.run(root, None, None, [], False, False, journal["id"])
        assert resumed["pr_url"] == pr_url, "resume did not reuse the existing PR"
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        state = json.loads((path / "gh_state.json").read_text(encoding="utf-8"))
        assert state["next_number"] == 2, "a second PR was allocated instead of reusing the first"


def test_gh_pr_merge_then_fail_marks_step_red_then_resume_completes() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_FAIL_AFTER_MERGE"] = "1"
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, True, None)
            except publish.PublishError:
                pass
            else:
                raise AssertionError("expected step 6 to fail after a successful server-side merge")
        journals = list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert len(journals) == 1
        data = json.loads(journals[0].read_text(encoding="utf-8"))
        assert data["steps"][5]["status"] == "red", data["steps"]
        assert _remote_main_sha(remote) != baseline_commit, "the merge should have landed server-side"

        publish2 = _load_publish()
        env2 = dict(env)
        del env2["GH_FAKE_FAIL_AFTER_MERGE"]
        with _patched_env(env2):
            resumed = publish2.run(root, None, None, [], False, False, data["id"])
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        assert resumed["baseline_sha_after"] == _remote_main_sha(remote)


def test_ci_checks_not_yet_registered_are_waited_for() -> None:
    # Real gh answers "no checks reported" (exit 1) for the first seconds after `pr create`;
    # the first CI-on publication went red on exactly that. Step 6 must wait for checks to exist.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_NO_CHECKS_CALLS"] = "2"
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        state = json.loads(Path(env["GH_FAKE_STATE"]).read_text(encoding="utf-8"))
        assert state.get("checks_calls") == 3, state.get("checks_calls")


def test_a_dropped_ci_watch_is_watched_again_while_checks_are_pending() -> None:
    # `gh pr checks --watch` exits 1 on a lost connection exactly as on a red check; the first
    # CI-on B publication went red on a graphql timeout while CI was still running. A watch that
    # ends while `--json` still reports a pending check has no verdict, so step 6 watches again.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_WATCH_DROPS"] = "2"
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        state = json.loads(Path(env["GH_FAKE_STATE"]).read_text(encoding="utf-8"))
        assert state.get("watch_calls") == 3, state.get("watch_calls")


def test_ci_on_publish_records_the_ci_run_url() -> None:
    # S6's done-condition reads `ci_run_url`; the first CI-on publication left it null.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = dict(_install_fake_gh(path))
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        assert journal["ci_run_url"] == "https://example.invalid/actions/runs/77", journal["ci_run_url"]


def test_ci_red_checks_record_the_ci_run_url() -> None:
    # A red run is the one whose log gets read; the integration publication's red step 6 kept no URL.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        publish.CHECKS_REGISTER_WAIT_S = 0.3
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_FAIL_STEP"] = "checks"
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, False, None)
            except publish.PublishError:
                pass
            else:
                raise AssertionError("red checks must stop step 6")
        journals = sorted((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert len(journals) == 1, journals
        journal = json.loads(journals[0].read_text(encoding="utf-8"))
        assert journal["ci_run_url"] == "https://example.invalid/actions/runs/77", journal["ci_run_url"]


def test_ci_checks_that_never_register_fail_step_six_after_the_wait() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        publish.CHECKS_REGISTER_WAIT_S = 0.3
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_NO_CHECKS_CALLS"] = "100000"
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, False, None)
            except publish.PublishError as exc:
                assert "no CI checks registered" in str(exc), exc
            else:
                raise AssertionError("expected step 6 to fail when checks never register")


def test_baseline_moved_before_merge_marks_step_red_and_fresh_publish_supersedes() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_MAIN_MOVE_ON_CHECKS"] = "1"
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, False, None)
            except publish.PublishError as exc:
                assert "baseline moved" in str(exc), str(exc)
            else:
                raise AssertionError("expected step 6 to detect the moved baseline")
        journals = list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert len(journals) == 1
        first_id = json.loads(journals[0].read_text(encoding="utf-8"))["id"]

        publish2 = _load_publish()
        env2 = dict(env)
        del env2["GH_FAKE_MAIN_MOVE_ON_CHECKS"]
        with _patched_env(env2):
            second = publish2.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], False, False, None)
        assert second.get("supersedes") == first_id, second
        assert all(s["status"] == "green" for s in second["steps"]), second["steps"]


def test_resume_redoes_steps_after_deleted_worktree() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], True, True, None)
        worktree = Path(journal["worktree"]["path"])
        assert worktree.exists()
        _remove(worktree)
        assert not worktree.exists()

        publish2 = _load_publish()
        with _patched_env(env):
            resumed = publish2.run(root, None, None, [], False, False, journal["id"])
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        assert Path(resumed["worktree"]["path"]).exists()


def test_corrupt_journal_resume_exits_nonzero() -> None:
    with _fixture() as path:
        root = path / "consumer"
        _init_repo(root)
        journal_dir = root / ".claude" / ".cache" / "baseline-publish"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "20260101T000000Z-deadbeef.json").write_bytes(b"{not json")
        result = subprocess.run(
            [sys.executable, str(PUBLISH), "--root", str(root),
             "--resume", "20260101T000000Z-deadbeef"],
            cwd=root, env=_env(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        assert result.returncode == 1, output
        assert "unreadable" in output.lower(), output


def test_from_worktree_refuses_dirty_foreign_nondescendant_sibling_and_nested() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        publish = _load_publish()

        # dirty: an uncommitted change in the worktree
        _cache, dirty_wt, pinned = _make_author_worktree(path, root, remote, "s5-dirty")
        _write(dirty_wt / "template" / ".claude" / "tools" / "dirty.py", b"x = 1\n")
        try:
            publish.run(root, {"kind": "worktree", "repo": str(dirty_wt), "commit": pinned},
                        None, [], True, True, None)
        except publish.PublishError as exc:
            assert "uncommitted" in str(exc), str(exc)
        else:
            raise AssertionError("expected a dirty worktree to be refused")

        # foreign: a plain clone, never registered as a worktree of the cache clone
        foreign = path / "foreign-checkout"
        _git(path, "clone", "-q", str(remote), str(foreign))
        try:
            publish.run(root, {"kind": "worktree", "repo": str(foreign), "commit": pinned},
                        None, [], True, True, None)
        except publish.PublishError as exc:
            assert "linked worktree" in str(exc) or "must be under" in str(exc), str(exc)
        else:
            raise AssertionError("expected a foreign checkout to be refused")

        # sibling-prefix path: `baseline-worktrees-x/` is not `baseline-worktrees/`
        cache, real_wt, pinned2 = _make_author_worktree(path, root, remote, "s5-sibling")
        sibling_dir = root / ".claude" / ".cache" / "baseline-worktrees-x"
        sibling_dir.mkdir(parents=True, exist_ok=True)
        sibling = sibling_dir / "s5-sibling"
        shutil.copytree(real_wt, sibling)
        try:
            publish.run(root, {"kind": "worktree", "repo": str(sibling), "commit": pinned2},
                        None, [], True, True, None)
        except publish.PublishError as exc:
            assert "must be under" in str(exc), str(exc)
        else:
            raise AssertionError("expected a sibling-prefix path to be refused")

        # nested inside another worktree
        cache3, outer_wt, pinned3 = _make_author_worktree(path, root, remote, "s5-outer")
        nested = outer_wt / ".claude" / ".cache" / "baseline-worktrees" / "s5-inner"
        nested.parent.mkdir(parents=True, exist_ok=True)
        _git(cache3, "worktree", "add", "-q", "-b", "author/s5-inner", str(nested), pinned3)
        try:
            publish.run(root, {"kind": "worktree", "repo": str(nested), "commit": pinned3},
                        None, [], True, True, None)
        except publish.PublishError as exc:
            assert "nested" in str(exc), str(exc)
        else:
            raise AssertionError("expected a worktree nested inside another worktree to be refused")

        # non-descendant: the worktree HEAD is BEHIND the freshly-resolved pinned sha
        cache4, behind_wt, pinned4 = _make_author_worktree(path, root, remote, "s5-behind")
        advance = path / "advance-clone"
        _git(path, "clone", "-q", str(remote), str(advance))
        _write(advance / "template" / ".claude" / "tools" / "advance.py", b"x = 1\n")
        _git(advance, "config", "user.email", "x@invalid")
        _git(advance, "config", "user.name", "x")
        _commit(advance, "advance baseline")
        _git(advance, "push", "-q", "origin", "HEAD:refs/heads/main")
        try:
            publish.run(root, {"kind": "worktree", "repo": str(behind_wt), "commit": pinned4},
                        None, [], True, True, None)
        except publish.PublishError as exc:
            assert "descend" in str(exc), str(exc)
        else:
            raise AssertionError("expected a worktree behind the pinned baseline to be refused")


def test_author_history_never_publishes_ancestor_token() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-history")

        token = "planted" + "-token" + "-ancestor-only"
        target = worktree / "template" / ".claude" / "tools" / "secret_bearer.py"
        _write(target, ("value = '" + token + "'\n").encode())
        _commit(worktree, "add token")
        _write(target, b"value = 'clean'\n")
        _commit(worktree, "remove token")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        # the token really is in the author branch's history, so the assertion below is
        # not vacuously true.
        log = _git(worktree, "log", "--all", "-p", "--", "template/.claude/tools/secret_bearer.py")
        assert token.encode() in log

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        published = _git(remote, "show", "main:template/.claude/tools/secret_bearer.py")
        assert token.encode() not in published
        assert b"clean" in published


def test_from_worktree_publishes_root_files_and_lock_rows() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-rootfiles")

        _write(worktree / "template" / ".claude" / "tools" / "newthing.py", b"x = 1\n")
        _write(worktree / "template" / ".claude" / "tests" / "test_newthing.py",
               b"import sys\nsys.exit(0)\n")
        _write(worktree / "tools" / "newtool.py", b"print('hi')\n")
        _commit(worktree, "author change")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        assert sorted(journal["rows_published"]) == sorted(
            [".claude/tools/newthing.py", ".claude/tests/test_newthing.py", "tools/newtool.py"]
        )
        assert _git(remote, "show", "main:template/.claude/tools/newthing.py") == b"x = 1\n"
        assert _git(remote, "show", "main:tools/newtool.py") == b"print('hi')\n"

        # root files carry no lock row, and a brand-new `.claude/` file's row is created by
        # the consumer's own `pull`, never by `publish` (§5).
        lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert ".claude/tools/newthing.py" not in lock["files"]
        assert "tools/newtool.py" not in lock["files"]


def test_battery_count_mismatch_is_detected() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path, voided_discovery=True)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-battery")

        _write(worktree / "template" / ".claude" / "tests" / "test_a.py", b"import sys\nsys.exit(0)\n")
        _write(worktree / "template" / ".claude" / "tests" / "test_b.py", b"import sys\nsys.exit(0)\n")
        _commit(worktree, "add proofs")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                            None, [], True, True, None)
            except publish.PublishError as exc:
                assert "battery discovery mismatch" in str(exc), str(exc)
            else:
                raise AssertionError("expected the voided-discovery battery to be caught")


def test_diff_records_parses_copy_as_addition_only() -> None:
    publish = _load_publish()
    raw = "\x00".join([
        "C100", "template/.claude/tools/orig.py", "template/.claude/tools/copy.py",
        "R100", "template/.claude/tools/old.py", "template/.claude/tools/new.py",
        "M", "tools/plain.py",
        "",
    ])
    records = publish._parse_diff_records(raw)
    by_key = {(r["relpath"], r["op"]) for r in records}
    assert (".claude/tools/orig.py", "D") not in by_key, "a copy must not delete its source"
    assert (".claude/tools/copy.py", "A") in by_key
    assert (".claude/tools/old.py", "D") in by_key
    assert (".claude/tools/new.py", "A") in by_key
    assert ("tools/plain.py", "M") in by_key
    assert len(records) == 4, records


def test_from_worktree_publishes_every_diff_record_including_root_config_files() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-rootconfig")

        _write(worktree / ".gitattributes", b"* text=auto eol=lf\n")
        _write(worktree / "baseline.manifest.json",
               b'{"version": 2, "files": [], "note": "s5-defect1"}\n')
        _commit(worktree, "author change: root config files")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        assert sorted(journal["rows_published"]) == sorted(
            [".gitattributes", "baseline.manifest.json"]
        ), journal["rows_published"]
        assert _git(remote, "show", "main:.gitattributes") == b"* text=auto eol=lf\n"
        assert _git(remote, "show", "main:baseline.manifest.json") == (
            b'{"version": 2, "files": [], "note": "s5-defect1"}\n'
        )


def test_publish_does_not_alter_cache_clone_shared_identity() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-identity")

        # An author's own identity, already configured on the shared cache clone (which
        # every linked worktree, including the one `_materialize` creates, shares
        # `.git/config` with).
        _git(cache, "config", "user.name", "author-identity")
        _git(cache, "config", "user.email", "author@example.invalid")
        before_name = _git(cache, "config", "--get", "user.name").decode().strip()
        before_email = _git(cache, "config", "--get", "user.email").decode().strip()

        _write(worktree / "template" / ".claude" / "tools" / "identity_check.py", b"x = 1\n")
        _commit(worktree, "author change")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]

        after_name = _git(cache, "config", "--get", "user.name").decode().strip()
        after_email = _git(cache, "config", "--get", "user.email").decode().strip()
        assert after_name == before_name, (before_name, after_name)
        assert after_email == before_email, (before_email, after_email)


def test_worktree_source_leaves_hash_for_consumer_pull_when_out_of_sync() -> None:
    rel = ".claude/tools/existing.py"
    old_content = b"value = 'old'\n"
    new_content = b"value = 'new'\n"
    with _fixture() as path:
        remote = _remote(path)
        work = path / "baseline-work"
        _init_repo(work)
        _write(work / "template" / rel, old_content)
        _write_baseline_stubs(work)
        manifest = {"version": 2, "files": [{"path": rel, "layer": "pure", "sync": "auto"}]}
        _write(work / "baseline.manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        baseline_commit = _commit(work, "seed")
        _push_main(work, remote)

        root = path / "consumer"
        _init_repo(root)
        _seed_project_subsystems(root)
        _write(root / rel, old_content)
        lock = {
            "schema": 2,
            "baseline_repo": remote.as_uri(),
            "baseline_ref": "main",
            "synced_commit": baseline_commit,
            "profile": "pure",
            "substitutions": {},
            "files": {
                rel: {
                    "status": "tracked",
                    "hash": _sha(old_content),
                    "layer": "pure",
                    "judged": {"sha": _sha(old_content), "verdict": "push",
                               "at": "2026-01-01T00:00:00Z"},
                }
            },
        }
        _write(root / ".claude" / "baseline.lock.json", (json.dumps(lock, indent=2) + "\n").encode())
        _commit(root, "consumer")

        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-defect2")
        assert pinned == baseline_commit
        _write(worktree / "template" / rel, new_content)
        _commit(worktree, "author change: update existing row")
        author_commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": author_commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        assert _git(remote, "show", "main:template/" + rel) == new_content

        updated_lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert updated_lock["files"][rel]["hash"] == _sha(old_content), (
            "step 7 must not rewrite hash for a row the consumer has not pulled yet"
        )
        assert updated_lock["synced_commit"] == journal["baseline_sha_after"]

        report = _run_sync(root, "check", "--json")
        assert report.returncode in (0, 1), (report.stdout + report.stderr).decode(errors="replace")
        results = json.loads(report.stdout.decode("utf-8"))["results"]
        assert results[rel] == "upstream-updated", results

        pulled = _run_sync(root, "pull", rel)
        assert pulled.returncode == 0, (pulled.stdout + pulled.stderr).decode(errors="replace")
        assert (root / rel).read_bytes() == new_content

        report2 = _run_sync(root, "check", "--json")
        results2 = json.loads(report2.stdout.decode("utf-8"))["results"]
        assert results2[rel] == "in-sync", results2


def _adaptation_json_path(root: Path) -> Path:
    return root / ".claude" / "skills" / "project_subsystems" / "adaptation.json"


def _skill_md_path(root: Path) -> Path:
    return root / ".claude" / "skills" / "project_subsystems" / "SKILL.md"


def _adaptation_contract_scenarios(root: Path):
    """Design §8's six ways the `project_subsystems` adaptation contract can be missing or
    malformed -- a label, a zero-arg corruption leaving the pair in that one broken state,
    and a substring the refusal message must contain (the file, or the file and key)."""
    adaptation_path = _adaptation_json_path(root)
    skill_path = _skill_md_path(root)
    return [
        ("adaptation.json missing", lambda: adaptation_path.unlink(), "adaptation.json"),
        ("adaptation.json unparseable", lambda: _write(adaptation_path, b"{not json"), "adaptation.json"),
        ("adaptation.json not a JSON object", lambda: _write(adaptation_path, b"[]\n"), "adaptation.json"),
        ("adaptation.json key wrong-typed",
         lambda: _write(adaptation_path, json.dumps({"tests_root": 1}).encode()), "tests_root"),
        ("SKILL.md missing", lambda: skill_path.unlink(), "SKILL.md"),
        ("SKILL.md subsystems block unparseable",
         lambda: _write(skill_path, b"# no yaml block here\n"), "SKILL.md"),
    ]


def test_publish_refuses_malformed_adaptation_contract_fresh_run() -> None:
    """Design §8: a fresh `publish` exits 1, naming the file (or file and key), for each way
    the adaptation contract can be broken, and creates no journal for any of them."""
    rel = ".claude/tools/contract_publish.py"
    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(
            path, rel, b"value = 'old'\n", b"value = 'fixture'\n"
        )
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        journal_dir = root / ".claude" / ".cache" / "baseline-publish"
        for label, corrupt, needle in _adaptation_contract_scenarios(root):
            _seed_project_subsystems(root)  # reset to a valid pair before each scenario
            corrupt()
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, True, None)
                raise AssertionError(f"{label}: publish did not refuse")
            except publish.PublishError as exc:
                assert needle in str(exc), f"{label}: {exc}"
            assert not journal_dir.exists() or not list(journal_dir.glob("*.json")), label


def test_publish_resume_refuses_malformed_adaptation_contract() -> None:
    """Design §8: `--resume` refuses the same way -- naming the file or key -- without
    advancing or rewriting the already-started journal from its dry-run state."""
    rel = ".claude/tools/contract_publish_resume.py"
    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(
            path, rel, b"value = 'old'\n", b"value = 'fixture'\n"
        )
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            dry = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                               [rel], [], True, True, None)
        resume_id = _field(dry, "id")
        journal_path = publish._journal_path(root, resume_id)
        before = journal_path.read_bytes()

        for label, corrupt, needle in _adaptation_contract_scenarios(root):
            _seed_project_subsystems(root)  # reset to a valid pair before each scenario
            corrupt()
            try:
                publish.run(root, None, None, [], False, False, resume_id)
                raise AssertionError(f"{label}: resume did not refuse")
            except publish.PublishError as exc:
                assert needle in str(exc), f"{label}: {exc}"
            assert journal_path.read_bytes() == before, label

def test_journal_and_lock_writes_wait_out_a_reader_holding_the_target() -> None:
    # Windows refuses os.replace onto a file another process holds open (WinError 5): an
    # indexer or scanner reading the journal crashed a real publication mid-scrub. The write
    # must wait the reader out. On POSIX the rename succeeds at once, so this passes trivially.
    publish = _load_publish()
    with tempfile.TemporaryDirectory(prefix="baseline_publish_replace_") as tmp:
        root = Path(tmp)
        journal = root / "journal.json"
        lock = root / ".claude" / "baseline.lock.json"
        lock.parent.mkdir(parents=True)
        for target, write in (
            (journal, lambda: publish._write_json_atomic(journal, {"id": "held"})),
            (lock, lambda: publish.sync.save_lock(root, {"version": 1, "files": {}})),
        ):
            target.write_bytes(b"{}\n")
            holder = open(target, "rb")
            timer = threading.Timer(0.5, holder.close)
            timer.start()
            try:
                write()
            finally:
                timer.join()
                holder.close()
            assert json.loads(target.read_bytes()) != {}, "%s was not replaced" % target.name
        leftovers = sorted(p.name for p in root.rglob("*.tmp"))
        assert not leftovers, "temporary files left behind: %s" % leftovers


# Records each run of the baseline's manifest generator: its argv and the exact layer entries it saw.
RECORDING_GEN_MANIFEST = b"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
entries_path = root / "tools" / "layer_entries.json"
entries = json.loads(entries_path.read_text(encoding="utf-8"))["entries"] if entries_path.exists() else {}
log = Path(os.environ["GEN_MANIFEST_LOG"])
calls = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
calls.append({"argv": sys.argv[1:], "entries": entries})
log.write_text(json.dumps(calls), encoding="utf-8")
sys.exit(0)
"""


def _layer_fixture(path: Path, fresh_layer: str | None):
    existing = ".claude/tools/fixture.py"
    fresh = ".claude/tools/fresh_" + "row.py"
    cache = path / "cache"
    _init_repo(cache)
    _write(cache / "template" / existing, b"value = 'old'\n")
    _write(cache / "tools" / "gen_manifest.py", RECORDING_GEN_MANIFEST)
    baseline_sha = _commit(cache, "seed")
    root = path / "consumer"
    _init_repo(root)
    _write(root / existing, b"value = 'fixture'\n")
    _write(root / fresh, b"value = 'fresh'\n")
    commit = _commit(root, "consumer")
    records = [{"relpath": rel, "source_path": rel, "dest_path": "template/" + rel,
                "kind": "lock", "op": "A"} for rel in (existing, fresh)]
    lock = {"substitutions": {}, "files": {
        existing: {"status": "tracked", "layer": "pure"},
        fresh: {"status": "tracked", "layer": fresh_layer},
    }}
    source = {"kind": "commit", "repo": str(root), "commit": commit}
    return root, source, records, lock, cache, baseline_sha, fresh


def test_materialize_records_new_row_layers_and_regenerates_manifest() -> None:
    # Validate's `gen_manifest.py --check` fails on a template file no layer list names, and on a
    # stale manifest. A commit source cannot edit baseline-root tools, so materialize must carry
    # each new row's layer as data and regenerate the manifest before validate runs.
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, "coding")
        log = path / "gen_manifest_calls.json"
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(log)}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "coding"}, entries
        calls = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
        assert [call["argv"] for call in calls] == [[]], calls
        assert calls[0]["entries"] == {fresh: "coding"}, calls


def test_materialize_refuses_a_new_row_without_a_layer_before_creating_the_worktree() -> None:
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, None)
        worktree = path / "publish-worktree"
        publish = _load_publish()
        try:
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        except publish.PublishError as exc:
            message = str(exc)
        else:
            raise AssertionError("materialize accepted a new row without a layer")
        assert fresh in message and "--layer" in message, message
        assert not worktree.exists(), "the refusal must come before the worktree exists"


def main() -> int:
    cases = [
        test_publish_renames_project_identifiers_without_touching_the_local_file,
        test_journal_and_lock_writes_wait_out_a_reader_holding_the_target,
        test_tracer_publishes_one_row_to_fixture_remote,
        test_peer_edit_after_the_source_commit_leaves_step_eight_green,
        test_collect_without_rows_takes_only_push_verdicts,
        test_materialize_writes_source_bytes_unsubstituted,
        test_materialize_records_new_row_layers_and_regenerates_manifest,
        test_materialize_refuses_a_new_row_without_a_layer_before_creating_the_worktree,
        test_local_merge_refuses_remote_url,
        test_no_ci_refuses_when_workflow_exists,
        test_step_failures_1_through_5_leave_fixture_main_unchanged,
        test_dry_run_then_resume_continues_at_step_six_and_records_owner_confirmed,
        test_a_red_dry_run_is_redone_as_a_dry_run_before_the_owner_confirms,
        test_a_journal_red_at_classify_resumes_through_classify,
        test_abbreviated_commit_source_resumes_instead_of_deadlocking,
        test_repeat_publish_prints_already_published,
        test_repeat_incomplete_same_baseline_refuses_naming_resume,
        test_repeat_incomplete_older_baseline_records_supersedes,
        test_resume_finds_and_reuses_pr_after_crash_before_pr_url_written,
        test_gh_pr_merge_then_fail_marks_step_red_then_resume_completes,
        test_ci_checks_not_yet_registered_are_waited_for,
        test_a_dropped_ci_watch_is_watched_again_while_checks_are_pending,
        test_ci_on_publish_records_the_ci_run_url,
        test_ci_red_checks_record_the_ci_run_url,
        test_ci_checks_that_never_register_fail_step_six_after_the_wait,
        test_baseline_moved_before_merge_marks_step_red_and_fresh_publish_supersedes,
        test_resume_redoes_steps_after_deleted_worktree,
        test_corrupt_journal_resume_exits_nonzero,
        test_from_worktree_refuses_dirty_foreign_nondescendant_sibling_and_nested,
        test_author_history_never_publishes_ancestor_token,
        test_from_worktree_publishes_root_files_and_lock_rows,
        test_battery_count_mismatch_is_detected,
        test_diff_records_parses_copy_as_addition_only,
        test_from_worktree_publishes_every_diff_record_including_root_config_files,
        test_publish_does_not_alter_cache_clone_shared_identity,
        test_worktree_source_leaves_hash_for_consumer_pull_when_out_of_sync,
        test_publish_refuses_malformed_adaptation_contract_fresh_run,
        test_publish_resume_refuses_malformed_adaptation_contract,
        test_published_forked_row_records_tracked_not_forked,
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



def test_publish_renames_project_identifiers_without_touching_the_local_file() -> None:
    """A lock substitution renames an identifier in the PUBLISHED copy while the source keeps it.

    This is the mechanism that makes genericising the source unnecessary: `reverse_for` maps the
    real name to the published one on the way up, `forward_for` maps it back on the way down, so
    the consumer's own rules keep citing classes that exist.
    """
    rel = ".claude/tools/fixture_rename.py"
    baseline_content = b"value = 'old'\n"
    local_content = b"# FixtureWidget is the family root\nvalue = 'fixture'\n"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, baseline_content, local_content)
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["substitutions"]["GenericWidget"] = "FixtureWidget"
        _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(
                root,
                {"kind": "commit", "repo": str(root), "commit": source_commit},
                [rel],
                [],
                False,
                True,
                None,
            )
        steps = _field(journal, "steps")
        assert all(step["status"] == "green" for step in steps), steps

        published = _git(remote, "show", "main:" + "template/" + rel)
        assert b"GenericWidget" in published, published
        assert b"FixtureWidget" not in published, published
        assert (root / rel).read_bytes() == local_content, "the local file must keep the real name"


def test_published_forked_row_records_tracked_not_forked() -> None:
    """A forked row judged `push` is upstream's content once step 7 has rewritten its hash,
    so the row is no longer a fork. Leaving `status: forked` there makes the next
    `check --strict` report `forked-upstream-moved`: the recorded `base` can no longer match a
    baseline that now carries this very content -- a clean publication that reads as drift."""
    rel = ".claude/tools/fixture_forked.py"
    baseline_content = b"value = 'old'\n"
    local_content = b"value = 'fixture'\n"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, baseline_content, local_content)
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"][rel]["status"] = "forked"
        lock["files"][rel]["base"] = baseline_commit
        # Schema 2 is what makes the consequence reachable: on a v1-shaped lock `_v2_state`
        # short-circuits a forked row to plain `forked` (quiet), so the drift this case exists
        # to prevent would not appear and the case would prove only the row-dict change.
        lock["schema"] = 2
        _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
        _commit(root, "forked row judged push")
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(
                root,
                {"kind": "commit", "repo": str(root), "commit": source_commit},
                [rel],
                [],
                False,
                True,
                None,
            )
        steps = _field(journal, "steps")
        assert all(step["status"] == "green" for step in steps), steps
        assert _git(remote, "show", "main:" + "template/" + rel) != baseline_content

        row = json.loads(lock_path.read_text(encoding="utf-8"))["files"][rel]
        assert row["status"] == "tracked", (
            "a published row now holds upstream's content; leaving it `forked` makes the "
            "next check read forked-upstream-moved: %r" % (row,)
        )
        assert "base" not in row, row


if __name__ == "__main__":
    sys.exit(main())
