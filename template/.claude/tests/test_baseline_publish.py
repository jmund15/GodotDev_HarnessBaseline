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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _case_pool import run_cases  # noqa: E402
from _runner_fixture import copy_runner_deps  # noqa: E402

_real_sleep = time.sleep


def _quick_slot_poll(seconds: float) -> None:
    """Step 5 (`_validate`) queues its checks behind the battery's exclusive machine hold, and
    `harness_tests._take_slots` re-polls every 0.25 s: each full publish spent about a second
    asleep for slots nobody else held. Only that poll is shortened; every other sleep is real."""
    if seconds == 0.25 and sys._getframe(1).f_code.co_name == "_take_slots":
        seconds = 0.01
    _real_sleep(seconds)


time.sleep = _quick_slot_poll

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
    return hashlib.sha256(data if b"\x00" in data else data.replace(b"\r\n", b"\n")).hexdigest()


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
    _git(path, "init", "-q", "-b", "main")
    with open(path / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        # gc and maintenance off: every commit otherwise spawns `maintenance run --auto` (~20 ms) that never has work here.
        handle.write("[user]\n\temail = fixture@example.invalid\n\tname = fixture\n"
                     "[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n")


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)
    return _branch_sha(path)


def _branch_sha(path: Path) -> str:
    if (path / ".git").is_dir():  # a linked worktree's `.git` is a file; it takes the spawn below
        head = (path / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        ref = path / ".git" / head[5:] if head.startswith("ref: ") else None
        if ref is not None and ref.is_file():  # a loose branch ref: reading it saves a `rev-parse` spawn
            return ref.read_text(encoding="utf-8").strip()
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
STUB_HARNESS_TESTS = b"#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"


def _write_baseline_stubs(work: Path) -> None:
    """Seed a baseline `work` checkout with the minimal in-tree tools step 5 shells out
    to, so a fresh worktree materialized at its pinned commit can run `validate` end to
    end without a real baseline checkout."""
    _write(work / "tools" / "gen_manifest.py", STUB_GEN_MANIFEST)
    _write(work / "tools" / "audit_baseline.py", STUB_AUDIT_BASELINE)
    _write(work / "template" / ".claude" / "scripts" / "harness_tests.py", STUB_HARNESS_TESTS)


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

    GH_FAKE_FAIL_STEP=create|checks|merge   that gh subcommand exits 1 immediately (`pr checks --json` reports the
                                            required `root` check FAILURE)
    GH_FAKE_LANE_RED=1                      the non-required `baseline` lane reports FAILURE; `root` stays green
    GH_FAKE_MAIN_MOVE_ON_CHECKS=1           the first readable `pr checks` poll pushes a peer commit to main
    GH_FAKE_NO_CHECKS_CALLS=N               the first N `pr checks --json` polls report no checks yet (exit 1)
    GH_FAKE_UNREADABLE_POLLS=N              the next N polls (after any no-checks polls) lose the connection (exit 1)
    GH_FAKE_PENDING_POLLS=N                 the first N readable polls report the check PENDING (exit 8)
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
        if "--json" not in argv:
            print("fake gh: pr checks requires --json", file=sys.stderr)
            return 1
        fields = argv[argv.index("--json") + 1].split(",")
        state["checks_calls"] = state.get("checks_calls", 0) + 1
        calls = state["checks_calls"]
        no_checks = int(os.environ.get("GH_FAKE_NO_CHECKS_CALLS", "0") or 0)
        unreadable = int(os.environ.get("GH_FAKE_UNREADABLE_POLLS", "0") or 0)
        _save_state(state)
        if calls <= no_checks:
            # Real gh right after `pr create`: the workflow run is not registered yet.
            print("no checks reported on the '%s' branch" % _current_branch(cwd), file=sys.stderr)
            return 1
        if calls <= no_checks + unreadable:
            print('Post "https://api.github.com/graphql": read tcp: connection timed out', file=sys.stderr)
            return 1
        state["readable_polls"] = state.get("readable_polls", 0) + 1
        _save_state(state)
        required_failed = os.environ.get("GH_FAKE_FAIL_STEP") == "checks"
        lane_failed = os.environ.get("GH_FAKE_LANE_RED") == "1"
        pending = state["readable_polls"] <= int(os.environ.get("GH_FAKE_PENDING_POLLS", "0") or 0)

        def verdict(failed):
            return ("PENDING", "pending") if pending else (("FAILURE", "fail") if failed else ("SUCCESS", "pass"))

        # Real gh shape (read from a live `gh pr checks` and `--required` on the baseline PR): one object per check,
        # `link` is the job URL; `--required` returns the ruleset's `root` check alone.
        records = [
            dict({"name": "baseline", "required": False, "workflow": "baseline",
                  "link": "https://example.invalid/actions/runs/77/job/2"}, state=verdict(lane_failed)[0],
                 bucket=verdict(lane_failed)[1]),
            dict({"name": "root", "required": True, "workflow": "baseline",
                  "link": "https://example.invalid/actions/runs/77/job/1"}, state=verdict(required_failed)[0],
                 bucket=verdict(required_failed)[1]),
        ]
        shown = [r for r in records if "--required" not in argv or r["required"]]
        print(json.dumps([{k: v for k, v in r.items() if k in fields} for r in shown]))
        if any(r["bucket"] == "pending" for r in shown):
            return 8
        if any(r["bucket"] == "fail" for r in shown):
            return 1
        if os.environ.get("GH_FAKE_MAIN_MOVE_ON_CHECKS") == "1" and not state.get("main_moved"):
            state["main_moved"] = True
            _save_state(state)
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
    _git(path, "init", "--bare", "-q", "-b", "main", str(remote))
    return remote


def _push_main(work: Path, remote: Path) -> None:
    """Publish `work`'s one-branch history to the empty bare `remote` and name it `origin`. The
    objects and the branch ref are copied in place of a `git push`: the result is the same repository
    state, and the push spawn was the most expensive step of every baseline fixture."""
    shutil.copytree(work / ".git" / "objects", remote / "objects", dirs_exist_ok=True)
    (remote / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (remote / "refs" / "heads" / "main").write_text(_branch_sha(work) + "\n", encoding="utf-8", newline="\n")
    with open(work / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        handle.write('[remote "origin"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n' % remote.as_uri())


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
        "schema": 2,
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


def _seed_bare_baseline(path: Path) -> tuple[Path, str]:
    """A pushed baseline repo with tool stubs and an empty manifest, for `--from-worktree`
    fixtures that need no pre-existing lock rows. Returns (remote, baseline_sha)."""
    remote = _remote(path)
    work = path / "baseline-work"
    _init_repo(work)
    _write_baseline_stubs(work)
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
        "schema": 2,
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


def _classify_rows(root: Path, relpaths: list[str], layer: str = "pure") -> None:
    """A new template row needs a consumer lock layer before publish, from either source kind
    (`_new_row_layers`). `baseline_sync.py classify` writes it; this does the same."""
    lock_path = root / ".claude" / "baseline.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for relpath in relpaths:
        lock["files"][relpath] = {"status": "tracked", "layer": layer, "judged": None}
    _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
    _commit(root, "classify new rows")


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


def test_collect_publishes_a_committed_deletion_of_a_push_row() -> None:
    gone = ".claude/tests/test_gone.py"
    never = ".claude/tests/test_never.py"
    with _fixture() as path:
        root = path / "consumer"
        _init_repo(root)
        _write(root / gone, b"gone = 1\n")
        _write(root / ".claude/tools/keep.py", b"keep = 1\n")
        _commit(root, "add")
        _git(root, "rm", "-q", "--", gone)
        commit = _commit(root, "retire the proof")
        lock = {"files": {
            gone: {"status": "tracked", "hash": "x",
                   "judged": {"sha": _sha(b"gone = 1\n"), "verdict": "push", "at": "t"}},
            never: {"status": "tracked", "hash": "y",
                    "judged": {"sha": "z", "verdict": "push", "at": "t"}},
        }}
        publish = _load_publish()
        source = {"kind": "commit", "repo": str(root), "commit": commit}
        records = publish._collect(root, source, [gone], lock, None, "unused")
        assert [(r["relpath"], r["op"]) for r in records] == [(gone, "D")], records
        try:
            publish._collect(root, source, [never], lock, None, "unused")
        except publish.PublishError:
            pass
        else:
            raise AssertionError("a push row no commit ever held was collected")


def test_materialize_keeps_a_100755_source_entry_executable() -> None:
    exec_rel = ".claude/scripts/fixture_run.sh"
    plain_rel = ".claude/tools/fixture_plain.py"
    with _fixture() as path:
        cache = path / "cache"
        _init_repo(cache)
        _write(cache / "template" / ".gitkeep", b"")
        baseline_sha = _commit(cache, "seed")
        root = path / "consumer"
        _init_repo(root)
        _write(root / exec_rel, b"#!/bin/sh\ntrue\n")
        _write(root / plain_rel, b"value = 1\n")
        _git(root, "add", "-A")
        _git(root, "update-index", "--chmod=+x", exec_rel)
        _git(root, "commit", "-q", "-m", "consumer")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        worktree = path / "publish-worktree"
        records = [{"relpath": rel, "source_path": rel, "dest_path": "template/" + rel,
                    "kind": "lock", "op": "A"} for rel in (exec_rel, plain_rel)]
        publish._materialize(
            root, {"kind": "commit", "repo": str(root), "commit": commit}, records,
            {"substitutions": {}, "files": {rel: {"layer": "pure"} for rel in (exec_rel, plain_rel)}},
            cache, baseline_sha, worktree, "publish/x",
        )

        def staged_mode(rel: str) -> str:
            return _git(worktree, "ls-files", "-s", "template/" + rel).decode().split()[0]

        _git(worktree, "add", "-A")
        assert staged_mode(exec_rel) == "100755", staged_mode(exec_rel)
        assert staged_mode(plain_rel) == "100644", staged_mode(plain_rel)


def test_materialize_stages_a_judged_row_the_baseline_gitignore_matches() -> None:
    rel = ".claude/tests/fixtures/run/tree/.claude/plans/plan.md"
    with _fixture() as path:
        cache = path / "cache"
        _init_repo(cache)
        _write(cache / ".gitignore", b"plans/\n")
        baseline_sha = _commit(cache, "seed")
        root = path / "consumer"
        _init_repo(root)
        _write(root / rel, b"# plan\n")
        commit = _commit(root, "consumer")
        publish = _load_publish()
        worktree = path / "publish-worktree"
        record = {"relpath": rel, "source_path": rel, "dest_path": "template/" + rel, "kind": "lock", "op": "A"}
        publish._materialize(
            root, {"kind": "commit", "repo": str(root), "commit": commit}, [record],
            {"substitutions": {}, "files": {rel: {"layer": "pure"}}}, cache, baseline_sha, worktree, "publish/x",
        )
        staged = _git(worktree, "ls-files", "--", "template/" + rel).decode().strip()
        assert staged == "template/" + rel, staged


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


def test_scrub_honors_the_hits_a_judge_accepted_on_each_row() -> None:
    publish = _load_publish()
    rel = ".claude/tests/test_tag.py"
    records = [{"relpath": rel, "dest_path": "template/" + rel}, {"relpath": ".claude/x.md", "dest_path": "template/.claude/x.md"}]
    lock = {"files": {rel: {"judged": {"verdict": "push", "accepted_hits": [rel + ":58:topology:04c54b07470c"]}},
                      ".claude/x.md": {"judged": {"verdict": "push", "accepted_hits": [".claude/other.md:1:topology:aa"]}}}}
    assert publish._judged_accepted_hits(records, lock) == {"template/" + rel + ":58:topology:04c54b07470c"}
    assert publish._judged_accepted_hits(records, {"files": {}}) == set()


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
    rel = ".claude/commands/fixture.md"
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
            resumed = publish2.run(root, None, None, [], False, False, journal["id"], True)
        assert all(s["status"] == "green" for s in resumed["steps"]), resumed["steps"]
        assert resumed["full_battery"] is True
        assert "mode=full" in resumed["steps"][4]["evidence"]
        assert "reason=flag" in resumed["steps"][4]["evidence"]
        assert resumed["dry_run"] is False
        assert resumed["owner_confirmed_at"] is not None
        kept = resumed["steps"][4].get("attempts", [])
        assert len(kept) == 1 and kept[0]["status"] == "green", resumed["steps"][4]
        assert kept[0]["started_at"] and kept[0]["finished_at"] and kept[0]["evidence"], kept
        assert _git(remote, "show", "main:template/" + rel)


def test_full_battery_upgrade_refuses_a_published_journal() -> None:
    """`--resume <id> --full-battery` resets step 5 onward, so on a journal that already
    published it would push, merge and move the lock a second time. It refuses instead."""
    rel = ".claude/commands/fixture.md"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                   [rel], [], True, True, None)
            published = publish.run(root, None, None, [], False, False, journal["id"])
        assert published["full_battery"] is False
        assert all(s["status"] == "green" for s in published["steps"]), published["steps"]
        publish2 = _load_publish()
        with _patched_env(env):
            try:
                publish2.run(root, None, None, [], False, False, journal["id"], True)
            except publish2.PublishError as exc:
                assert "already published" in str(exc), exc
            else:
                raise AssertionError("--full-battery must not reopen a published journal")


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


def test_open_only_opens_the_pr_before_the_land_and_the_merge_waits_for_it() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        # The project's own origin holds history without the source commit: the land has not happened.
        (path / "project").mkdir()
        project = _remote(path / "project")
        other = path / "project-work"
        _init_repo(other)
        _write(other / "README.md", b"project\n")
        _commit(other, "project main")
        _push_main(other, project)
        _git(root, "remote", "add", "origin", project.as_uri())
        _git(root, "fetch", "-q", "origin", "main")
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            dry = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                              [rel], [], True, True, None)
            opened = publish.run(root, None, None, [], False, False, dry["id"], open_only=True)
        assert opened["pr_url"], "--open-only recorded no PR"
        assert opened["steps"][5]["status"] != "green", opened["steps"]
        assert _remote_main_sha(remote) == baseline_commit, "--open-only merged"
        with _patched_env(env):
            try:
                publish.run(root, None, None, [], False, False, dry["id"])
            except publish.PublishError as exc:
                assert "not on origin/main" in str(exc), str(exc)
            else:
                raise AssertionError("a plain resume merged before the source commit landed")
        assert _remote_main_sha(remote) == baseline_commit, "the merge preceded the land"
        _git(root, "push", "-q", "-f", "origin", "HEAD:refs/heads/main")
        with _patched_env(env):
            done = publish.run(root, None, None, [], False, False, dry["id"])
        assert all(step["status"] == "green" for step in done["steps"]), done["steps"]
        assert done["pr_url"] == opened["pr_url"]
        assert _remote_main_sha(remote) != baseline_commit


def test_open_only_is_a_resume_flag_and_excludes_dry_run() -> None:
    publish = _load_publish()
    for argv in (["--open-only", "--from-commit", "HEAD"], ["--resume", "x", "--open-only", "--dry-run"]):
        errors = io.StringIO()
        try:
            with contextlib.redirect_stderr(errors):
                publish.main(argv)
        except SystemExit as exc:
            assert exc.code == 2, (argv, exc.code)
            assert "unrecognized" not in errors.getvalue() and "--open-only" in errors.getvalue(), errors.getvalue()
        else:
            raise AssertionError(f"{argv} was accepted")


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
        assert state.get("checks_calls") == 4, state.get("checks_calls")  # 2 unregistered + 1 green + run-url read


def test_pending_checks_are_polled_until_green() -> None:
    # Step 6 polls short `--json` reads; a pending check has no verdict, so it polls again until green.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_PENDING_POLLS"] = "2"
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        state = json.loads(Path(env["GH_FAKE_STATE"]).read_text(encoding="utf-8"))
        assert state.get("readable_polls") == 4, state.get("readable_polls")  # 3 polls + run-url read


def test_unreadable_poll_answers_are_polled_again() -> None:
    # A poll that times out or returns junk has no verdict either; step 6 polls again.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        publish.sync.NETWORK_RETRY_DELAYS = (0, 0)  # the fake gh's timeout stderr reads as transient: no backoff sleeps
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_UNREADABLE_POLLS"] = "2"
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        state = json.loads(Path(env["GH_FAKE_STATE"]).read_text(encoding="utf-8"))
        assert state.get("checks_calls") == 4, state.get("checks_calls")  # 2 unreadable + 1 green + run-url read


def test_unreadable_poll_answers_fail_step_six_after_the_wait() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_path, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        publish.CHECKS_POLL_S = 0.05
        publish.CHECKS_REGISTER_WAIT_S = 0.3
        publish.sync.NETWORK_RETRY_DELAYS = (0, 0)
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_UNREADABLE_POLLS"] = "100000"
        with _patched_env(env):
            try:
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                            [rel], [], False, False, None)
            except publish.PublishError:
                pass
            else:
                raise AssertionError("expected step 6 to fail when no poll answer is readable")


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
                assert "checks registered" in str(exc), exc
            else:
                raise AssertionError("expected step 6 to fail when checks never register")


def test_a_red_non_required_lane_does_not_refuse_the_merge() -> None:
    # PR #128: the required `root` check passed while five Linux lanes went red, and the publish refused on them.
    # The lanes report after the merge now (main-check rows), so step 6 merges on the required checks alone.
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = dict(_install_fake_gh(path))
        env["GH_FAKE_LANE_RED"] = "1"
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit},
                                  [rel], [], False, False, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        assert _remote_main_sha(remote) != baseline_commit, "a red non-required lane must not block the merge"


def test_a_red_required_check_still_refuses_the_merge() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
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
            except publish.PublishError as exc:
                assert "root" in str(exc), exc
            else:
                raise AssertionError("a red required check must stop step 6")
        assert _remote_main_sha(remote) == baseline_commit, "a red required check must not merge"


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


def _move_baseline(path: Path, rel: str, content: bytes) -> str:
    """Another consumer publishes a new copy of `rel`: the baseline's main moves past the consumer's pin."""
    work, remote = path / "baseline-work", path / "remote.git"
    _write(work / "template" / rel, content)
    moved = _commit(work, "another consumer moves " + rel)
    _git(work, "push", "-q", str(remote), "HEAD:refs/heads/main")
    return moved


def _publish_dry(publish, root: Path, rel: str, **keywords):
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    return publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit}, [rel], [], True, True, None,
                       **keywords)


def test_publish_reads_the_check_state_of_the_rows_it_ships() -> None:
    """Step 1's evidence names each shipped row's check state, so a publish is never blind to the baseline."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        publish = _load_publish()
        with _patched_env(_install_fake_gh(path)):
            journal = _publish_dry(publish, root, rel)
        evidence = _field(journal, "steps")[0]["evidence"]
        assert "local-modified" in evidence and rel in evidence, evidence


def test_publish_refuses_a_shipped_row_the_baseline_moved_since_its_base() -> None:
    """A row whose upstream copy changed since its base (another consumer published it) would be overwritten, losing
    that consumer's change for every lane that pins the baseline: step 1 refuses, names the state and both ways out,
    and writes no decline."""
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        _move_baseline(path, rel, b"value = 'theirs'\n")
        publish = _load_publish()
        with _patched_env(_install_fake_gh(path)):
            try:
                _publish_dry(publish, root, rel)
            except publish.PublishError as exc:
                text = str(exc)
            else:
                raise AssertionError("a publish over a moved upstream row must refuse")
        assert rel in text and "diverged" in text and "`pull`" in text and "--over-upstream " + rel in text, text
        journals = list((root / ".claude" / ".cache" / "baseline-publish").glob("*.json"))
        assert json.loads(journals[0].read_text(encoding="utf-8"))["steps"][0]["status"] == "red"
        lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert "declined" not in lock["files"][rel], lock["files"][rel]


def test_publish_refuses_a_shipped_row_whose_bound_proof_stays_behind() -> None:
    """The baseline's older copy of a bound proof would run against the newer row: step 1 names the proof; once the
    baseline's copy matches the source's, step 1 passes."""
    rel, proof = ".claude/tools/fixture.py", ".claude/tests/test_fixture.py"
    new_proof = b"import fixture  # fixture.py\nassert True\n"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        _move_baseline(path, proof, b"import fixture  # fixture.py\nassert False\n")
        _write(root / proof, new_proof)
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"][proof] = {"status": "tracked", "layer": "pure", "judged": None}
        _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
        _commit(root, "the proof moves with the row")
        publish = _load_publish()
        with _patched_env(_install_fake_gh(path)):
            try:
                _publish_dry(publish, root, rel)
            except publish.PublishError as exc:
                text = str(exc)
            else:
                raise AssertionError("a publish that leaves a differing bound proof behind must refuse")
            assert proof in text, text
            _move_baseline(path, proof, new_proof)
            _git(root, "commit", "--allow-empty", "-q", "-m", "a new source commit, so the repeat rule stays out")
            journal = _publish_dry(publish, root, rel)
        assert _field(journal, "steps")[0]["status"] == "green", _field(journal, "steps")[0]


def test_a_forked_bound_proof_left_behind_passes_step_one() -> None:
    """A forked proof records a deliberate divergence and cannot travel by `judge --verdict push` without overwriting
    the baseline's copy, so step 1 leaves it to the battery, which runs the baseline's copy against the shipped rows."""
    rel, proof = ".claude/tools/fixture.py", ".claude/tests/test_fixture.py"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        _move_baseline(path, proof, b"import fixture  # fixture.py\nassert True  # theirs\n")
        _write(root / proof, b"import fixture  # fixture.py\nassert True  # ours\n")
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"][proof] = {"status": "forked", "layer": "pure", "judged": None}
        _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
        _commit(root, "the proof is forked")
        publish = _load_publish()
        with _patched_env(_install_fake_gh(path)):
            journal = _publish_dry(publish, root, rel)
        assert _field(journal, "steps")[0]["status"] == "green", _field(journal, "steps")[0]


def test_a_decline_is_recorded_where_a_later_publish_reads_it_and_holds_for_that_upstream_only() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        _move_baseline(path, rel, b"value = 'theirs'\n")
        publish = _load_publish()
        with _patched_env(_install_fake_gh(path)):
            journal = _publish_dry(publish, root, rel, over_upstream=[rel])
            assert _field(journal, "steps")[0]["status"] == "green", _field(journal, "steps")[0]
            lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
            declined = lock["files"][rel]["declined"]
            assert declined["upstream"] and declined["at"], declined
            _git(root, "commit", "--allow-empty", "-q", "-m", "a new source commit, so the repeat rule stays out")
            again = _publish_dry(publish, root, rel)
            assert _field(again, "steps")[0]["status"] == "green", "a recorded decline must satisfy the next publish"
            _move_baseline(path, rel, b"value = 'theirs, again'\n")
            _git(root, "commit", "--allow-empty", "-q", "-m", "and another")
            try:
                _publish_dry(publish, root, rel)
            except publish.PublishError as exc:
                assert "diverged" in str(exc) and rel in str(exc), str(exc)
            else:
                raise AssertionError("a decline of one upstream copy must not cover a later one")


def test_a_publish_that_reaches_upstream_clears_the_decline() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        _remote_unused, _pin, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        _move_baseline(path, rel, b"value = 'theirs'\n")
        publish = _load_publish()
        commit = _git(root, "rev-parse", "HEAD").decode().strip()
        with _patched_env(_install_fake_gh(path)):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit}, [rel], [], False,
                                  True, None, over_upstream=[rel])
        assert all(step["status"] == "green" for step in _field(journal, "steps")), _field(journal, "steps")
        lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert "declined" not in lock["files"][rel], lock["files"][rel]


def _registered_worktrees(root: Path) -> set[str]:
    listed = _git(root / ".claude" / ".cache" / "baseline-repo", "worktree", "list", "--porcelain").decode()
    return {Path(line[len("worktree "):]).name for line in listed.splitlines() if line.startswith("worktree ")}


def _dry_run_journal(publish, root: Path, rel: str, env: dict):
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    with _patched_env(env):
        return publish.run(root, {"kind": "commit", "repo": str(root), "commit": commit}, [rel], [], True, True, None)


def test_a_finished_publish_removes_its_worktree_and_a_dry_run_keeps_it() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        publish = _load_publish()
        env = _install_fake_gh(path)
        dry = _dry_run_journal(publish, root, rel, env)
        worktree = Path(dry["worktree"]["path"])
        assert worktree.exists() and worktree.name in _registered_worktrees(root)
        with _patched_env(env):
            done = _load_publish().run(root, None, None, [], False, False, dry["id"])
        assert all(s["status"] == "green" for s in done["steps"]), done["steps"]
        assert not worktree.exists(), "a finished publish left its worktree"
        assert worktree.name not in _registered_worktrees(root)


def test_sweep_removes_terminal_and_journalless_worktrees_and_keeps_a_live_one() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        publish = _load_publish()
        live = _dry_run_journal(publish, root, rel, _install_fake_gh(path))
        cache = root / ".claude" / ".cache" / "baseline-repo"
        journals = root / ".claude" / ".cache" / "baseline-publish"
        planted = {}
        for name in ("20260101T000000Z-aaaaaaaa", "20260101T000001Z-bbbbbbbb", "20260101T000002Z-cccccccc"):
            worktree = journals / (name + "-worktree")
            _git(cache, "worktree", "add", "-q", "-b", "publish/" + name, str(worktree), baseline_commit)
            planted[name] = worktree
        done = {"id": "20260101T000000Z-aaaaaaaa", "steps": [{"status": "green"}] * 8}
        abandoned = {"id": "20260101T000001Z-bbbbbbbb", "abandoned_at": "2026-01-02T00:00:00Z",
                     "steps": [{"status": "red"}] * 8}
        for record in (done, abandoned):
            publish._write_json_atomic(journals / (record["id"] + ".json"), record)
        removed = publish.sweep_worktrees(root)
        assert sorted(removed) == sorted(p.name for p in planted.values()), removed
        assert not any(p.exists() for p in planted.values())
        assert not (_registered_worktrees(root) & {p.name for p in planted.values()})
        assert Path(live["worktree"]["path"]).exists(), "a live journal's worktree was swept"
        assert publish.sweep_worktrees(root) == []


def test_abandon_releases_the_worktree_and_resume_refuses_the_journal() -> None:
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        publish = _load_publish()
        env = _install_fake_gh(path)
        dry = _dry_run_journal(publish, root, rel, env)
        worktree = Path(dry["worktree"]["path"])
        publish.abandon(root, dry["id"])
        assert not worktree.exists()
        record = json.loads((root / ".claude" / ".cache" / "baseline-publish" / (dry["id"] + ".json")).read_text())
        assert record["abandoned_at"]
        try:
            with _patched_env(env):
                publish.run(root, None, None, [], False, False, dry["id"])
        except publish.PublishError as exc:
            assert dry["id"] in str(exc)
        else:
            raise AssertionError("resume took an abandoned journal")


def test_an_abandoned_journal_never_blocks_a_fresh_publication() -> None:
    # `publish --abandon` ends a journal for good; a fresh publish of the same inputs must start its own
    # journal, not be sent back to `--resume` the abandoned one (which resume refuses).
    rel = ".claude/tools/fixture.py"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        publish = _load_publish()
        env = _install_fake_gh(path)
        dry = _dry_run_journal(publish, root, rel, env)
        publish.abandon(root, dry["id"])
        again = _dry_run_journal(publish, root, rel, env)
        assert again["id"] != dry["id"], again["id"]


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
        assert not Path(resumed["worktree"]["path"]).exists()
        for index in (2, 3, 4):
            kept = resumed["steps"][index].get("attempts", [])
            assert len(kept) == 1 and kept[0]["status"] == "green", (index, resumed["steps"][index])


def test_step_keeps_the_red_attempt_when_it_reruns_green() -> None:
    publish = _load_publish()
    with _fixture() as path:
        journal_path = path / "journal.json"
        journal = {"steps": [{"name": "s", "status": "pending", "started_at": None,
                              "finished_at": None, "evidence": ""}]}

        def boom() -> None:
            raise RuntimeError("red evidence")

        try:
            publish._step(journal, journal_path, 0, boom)
        except RuntimeError:
            pass
        item = journal["steps"][0]
        assert "attempts" not in item, item
        first_start = item["started_at"]
        publish._step(journal, journal_path, 0, lambda: None)
        item = journal["steps"][0]
        assert item["status"] == "green", item
        assert len(item["attempts"]) == 1, item
        attempt = item["attempts"][0]
        assert attempt["status"] == "red" and attempt["evidence"] == "red evidence", attempt
        assert attempt["started_at"] == first_start and attempt["finished_at"], attempt
        saved = json.loads(journal_path.read_text(encoding="utf-8"))
        assert len(saved["steps"][0]["attempts"]) == 1, saved


def test_corrupt_journal_resume_exits_nonzero() -> None:
    with _fixture() as path:
        root = path / "consumer"
        _init_repo(root)
        _seed_project_subsystems(root)
        _write(root / ".claude" / "baseline.lock.json", b'{"schema": 2, "files": {}}\n')
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


def test_from_worktree_refuses_dirty_foreign_sibling_and_nested() -> None:
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

        # behind: the worktree HEAD is an ancestor of the freshly-resolved pinned sha
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
            assert "no changes" in str(exc), str(exc)  # absorbed, not refused: nothing of its own to publish
        else:
            raise AssertionError("expected a worktree with nothing past the advanced baseline to have no changes")
        advanced = _git(advance, "rev-parse", "HEAD").decode().strip()
        assert _git(behind_wt, "rev-parse", "HEAD").decode().strip() == advanced


LAYER_FILE = "tools/layer_entries.json"
LAYER_SEED = b'{\n  "version": 1,\n  "entries": {\n    "a": "pure"\n  }\n}\n'
LAYER_EDIT = b'{\n  "version": 1,\n  "entries": {\n    "a": "forked"\n  }\n}\n'


def _squash_published_line(path: Path):
    """An author worktree whose publish squashed its rows onto shared main through the fake gh, so the
    squash message and the layer rows are the tool's own output. Returns (publish, root, cache, line);
    `line` is that worktree, left at the author commit the squash was cut from."""
    remote, baseline_commit = _seed_bare_baseline(path)
    root = _seed_worktree_root(path, remote, baseline_commit)
    cache, line, _pinned = _make_author_worktree(path, root, remote, "absorb-line")
    rows = [".claude/tools/doomed.py", ".claude/tools/kept.py"]
    _classify_rows(root, rows)
    _write(line / LAYER_FILE, LAYER_SEED)
    for row in rows:
        _write(line / "template" / row, b"x = 1\n")
    source = _commit(line, "author change")
    publish = _load_publish()
    with _patched_env(_install_fake_gh(path)):
        journal = publish.run(root, {"kind": "worktree", "repo": str(line), "commit": source},
                              None, [], False, True, None)
    assert all(step["status"] == "green" for step in journal["steps"]), journal["steps"]
    return publish, root, cache, line


def _absorb(publish, root: Path, cache: Path, line: Path) -> str:
    """The descent step a second `--from-worktree` publish runs, at the pin the first publish left."""
    pinned = publish._pin(root, publish.sync.load_lock(root))[1]
    publish._validate_worktree_source(root, cache, line, pinned)
    return pinned


def _is_ancestor(repo: Path, ancestor: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, "HEAD"]).returncode == 0


def test_absorb_keeps_a_later_author_deletion_and_carries_the_publish_rows() -> None:
    with _fixture() as path:
        publish, root, cache, line = _squash_published_line(path)
        _git(line, "rm", "-q", "template/.claude/tools/doomed.py")
        _commit(line, "author deletes the file")
        pinned = _absorb(publish, root, cache, line)
        assert _is_ancestor(line, pinned)
        assert not (line / "template" / ".claude" / "tools" / "doomed.py").exists()
        carried = json.loads((line / LAYER_FILE).read_text(encoding="utf-8"))["entries"]
        assert ".claude/tools/doomed.py" in carried and ".claude/tools/kept.py" in carried, carried
        # the only thing left to publish is the author's own deletion
        records = publish._diff_records(line, pinned)
        assert [(r["relpath"], r["op"]) for r in records] == [(".claude/tools/doomed.py", "D")], records


def test_absorb_keeps_a_later_author_edit() -> None:
    with _fixture() as path:
        publish, root, cache, line = _squash_published_line(path)
        _write(line / "template" / ".claude" / "tools" / "kept.py", b"x = 2\n")
        _commit(line, "author edits the file")
        pinned = _absorb(publish, root, cache, line)
        assert _is_ancestor(line, pinned)
        assert (line / "template" / ".claude" / "tools" / "kept.py").read_bytes() == b"x = 2\n"
        carried = json.loads((line / LAYER_FILE).read_text(encoding="utf-8"))["entries"]
        assert ".claude/tools/kept.py" in carried, carried


def test_absorb_stops_on_a_publish_change_that_conflicts_with_a_later_author_edit() -> None:
    with _fixture() as path:
        publish, root, cache, line = _squash_published_line(path)
        # the publish added its rows beside "a"; the author line edits that same line afterwards
        _write(line / LAYER_FILE, LAYER_EDIT)
        _commit(line, "author edits the layer row")
        try:
            _absorb(publish, root, cache, line)
        except publish.PublishError as exc:
            assert LAYER_FILE in str(exc), str(exc)
        else:
            raise AssertionError("expected a conflicting publish change to stop the absorb")


def test_author_history_never_publishes_ancestor_token() -> None:
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-history")
        _classify_rows(root, [".claude/tools/secret_bearer.py"])

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
        _classify_rows(root, [".claude/tools/newthing.py", ".claude/tests/test_newthing.py"])

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

        # root files carry no lock row; `publish` adds no row beyond the consumer's classified ones (§5).
        lock = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
        assert sorted(lock["files"]) == [".claude/tests/test_newthing.py", ".claude/tools/newthing.py"], lock["files"]
        assert "tools/newtool.py" not in lock["files"]


def test_publish_strips_memory_provenance_keys_from_the_published_file() -> None:
    memory = ".claude/auto-memory/feedback_publish.md"
    with _fixture() as path:
        remote, baseline_commit = _seed_bare_baseline(path)
        root = _seed_worktree_root(path, remote, baseline_commit)
        cache, worktree, pinned = _make_author_worktree(path, root, remote, "s5-memory")
        _classify_rows(root, [memory])
        _write(worktree / "template" / memory,
               b"---\nname: rule\ntype: feedback\nmetadata:\n  node_type: memory\n"
               b"  originSessionId: session-one\n  modified: 2026-01-01T00:00:00Z\n---\nBody line\n")
        _commit(worktree, "author memory")
        commit = _git(worktree, "rev-parse", "HEAD").decode().strip()

        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "worktree", "repo": str(worktree), "commit": commit},
                                   None, [], False, True, None)
        assert all(s["status"] == "green" for s in journal["steps"]), journal["steps"]
        published = _git(remote, "show", "main:template/" + memory).decode()
        assert "originSessionId" not in published and "modified:" not in published, published
        assert "name: rule" in published and "type: feedback" in published, published


def test_battery_count_mismatch_is_detected() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = path / ".claude" / ".cache" / "baseline-publish" / "s5-battery"
        template = worktree / "template"
        tests = template / ".claude" / "tests"
        _init_repo(worktree)
        _write(template / ".claude" / "scripts" / "harness_tests.py", STUB_HARNESS_TESTS)
        _write(tests / "test_a.py", b"import sys\nsys.exit(0)\n")
        _commit(worktree, "seed")

        # Discovery must not void inside the nested `.claude/.cache/` checkout a publish builds in.
        assert publish.catalog.View.worktree(str(template)).discover() == [".claude/tests/test_a.py"]
        publish._battery_counts_match(worktree)

        _write(tests / "test_stray.py", b"import sys\nsys.exit(0)\n")
        try:
            publish._battery_counts_match(worktree)
        except publish.PublishError as exc:
            assert "battery discovery mismatch" in str(exc), str(exc)
            assert "worktree-only ['.claude/tests/test_stray.py']" in str(exc), str(exc)
        else:
            raise AssertionError("expected an untracked proof on disk to be caught")

        (tests / "test_stray.py").unlink()
        (tests / "test_a.py").unlink()
        try:
            publish._battery_counts_match(worktree)
        except publish.PublishError as exc:
            assert "index-only ['.claude/tests/test_a.py']" in str(exc), str(exc)
        else:
            raise AssertionError("expected a tracked proof missing on disk to be caught")


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
        _classify_rows(root, [".claude/tools/identity_check.py"])

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


def _layer_fixture(path: Path, fresh_layer: str | None, pinned_existing_layer: str | None = None):
    existing = ".claude/tools/fixture.py"
    fresh = ".claude/tools/fresh_" + "row.py"
    cache = path / "cache"
    _init_repo(cache)
    _write(cache / "template" / existing, b"value = 'old'\n")
    if pinned_existing_layer:
        manifest = {"version": 1, "files": [{"path": existing, "layer": pinned_existing_layer, "sync": "auto"}]}
        _write(cache / "baseline.manifest.json", json.dumps(manifest).encode("utf-8"))
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


def test_materialize_records_a_layer_move_for_an_existing_row() -> None:
    # Re-layering a shipped row (the lock says pure, the pinned manifest says coding) must reach
    # the baseline too, or pure consumers never receive the file the move was for.
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, "coding", "coding")
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "coding", ".claude/tools/fixture.py": "pure"}, entries


def test_materialize_records_no_entry_for_an_unmoved_existing_row() -> None:
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, "coding", "pure")
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "coding"}, entries


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


def _worktree_layer_fixture(path: Path, fresh_layer: str | None):
    root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, fresh_layer)
    return root, {**source, "kind": "worktree"}, records, lock, cache, baseline_sha, fresh


def test_worktree_source_refuses_a_new_row_without_a_layer_before_creating_the_worktree() -> None:
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _worktree_layer_fixture(path, None)
        worktree = path / "publish-worktree"
        publish = _load_publish()
        try:
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        except publish.PublishError as exc:
            message = str(exc)
        else:
            raise AssertionError("materialize accepted a worktree-source new row without a layer")
        assert fresh in message and "--layer" in message, message
        assert not worktree.exists(), "the refusal must come before the worktree exists"


def test_worktree_source_records_a_layered_new_row() -> None:
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _worktree_layer_fixture(path, "coding")
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "coding"}, entries


def test_worktree_source_takes_a_new_row_layer_its_own_layer_entries_record() -> None:
    # A file only the baseline worktree has can get no consumer lock row (`classify` needs a consumer
    # path), so the worktree's own `tools/layer_entries.json` at the source commit is its layer.
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _worktree_layer_fixture(path, None)
        _write(root / "tools" / "layer_entries.json",
               json.dumps({"version": 1, "entries": {fresh: "godot"}}).encode("utf-8"))
        source = {**source, "commit": _commit(root, "worktree layer entry")}
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "godot"}, entries


def test_worktree_source_takes_a_new_row_layer_its_manifest_records() -> None:
    # Most baseline files take their layer from gen_manifest.py's pattern lists, never an entry in
    # `tools/layer_entries.json`; the worktree's generated `baseline.manifest.json` at the source commit
    # holds every file's layer, so a pattern-classified new file is not refused as unlayered.
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _worktree_layer_fixture(path, None)
        _write(root / "baseline.manifest.json", json.dumps(
            {"version": 1, "files": [{"path": fresh, "layer": "coding", "sync": "auto"}]}).encode("utf-8"))
        source = {**source, "commit": _commit(root, "worktree manifest")}
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "coding"}, entries


def test_worktree_source_layers_outrank_the_consumer_lock() -> None:
    # A worktree source is the baseline's own tree, so its manifest decides each row's layer; the
    # publishing consumer's lock row (a composed file's local layer, or a layer the source has since
    # moved) never re-layers the baseline.
    with _fixture() as path:
        root, source, records, lock, cache, baseline_sha, fresh = _layer_fixture(path, "godot", "coding")
        existing = ".claude/tools/fixture.py"
        _write(root / "baseline.manifest.json", json.dumps({"version": 1, "files": [
            {"path": existing, "layer": "coding", "sync": "auto"},
            {"path": fresh, "layer": "pure", "sync": "auto"}]}).encode("utf-8"))
        source = {**source, "kind": "worktree", "commit": _commit(root, "worktree manifest")}
        worktree = path / "publish-worktree"
        publish = _load_publish()
        with _patched_env({"GEN_MANIFEST_LOG": str(path / "calls.json")}):
            publish._materialize(root, source, records, lock, cache, baseline_sha, worktree, "publish/x")
        entries = json.loads((worktree / "tools" / "layer_entries.json").read_text(encoding="utf-8"))["entries"]
        assert entries == {fresh: "pure"}, entries


def test_publication_commit_carries_a_message_past_the_command_line_limit() -> None:
    # A publication's body lists every row with its `Why:` line; a large one passed with `-m` overran
    # the OS command-line limit (WinError 206 on Windows, one argument's 128 KiB cap on Linux).
    with _fixture() as path:
        repo = path / "publication"
        _init_repo(repo)
        _write(repo / "seed.txt", b"seed\n")
        _commit(repo, "seed")
        _write(repo / "template" / "row.md", b"row\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        body = "\n".join("- .claude/tools/row_%05d.py: why %s" % (i, "x" * 60) for i in range(3000))
        publish = _load_publish()
        publish._commit_publication(repo, "feat(baseline): publish big", body)
        logged = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"],
                                check=True, capture_output=True).stdout.decode("utf-8")
        assert logged.startswith("feat(baseline): publish big\n\n"), logged[:80]
        assert logged.rstrip("\n").endswith(body.splitlines()[-1]), logged[-120:]


def test_validate_leaves_the_battery_to_ci_unless_the_flag_asks() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = path / "worktree"
        _write(worktree / "tools/gen_manifest.py", STUB_GEN_MANIFEST)
        _write(worktree / "tools/audit_baseline.py", STUB_AUDIT_BASELINE)
        template = worktree / "template"
        _write(template / ".claude/scripts/harness_tests.py", PUBLISH.parents[1].joinpath("scripts/harness_tests.py").read_bytes())
        copy_runner_deps(template / ".claude")
        _write(template / ".claude/skills/project_subsystems/adaptation.json", b"{}\n")
        proof = template / ".claude/tests/test_bound.py"
        _write(proof, b"DOC = 'fixture.md'\nraise SystemExit(1)\n")
        row = ".claude/commands/fixture.md"
        _write(template / row, b"# fixture\n")
        _init_repo(worktree)
        _commit(worktree, "fixture")
        record = {"kind": "lock", "op": "M", "relpath": row}
        evidence = publish._validate(worktree, [record], False)
        assert "mode=ci" in evidence and "harness_tests.py=" not in evidence, evidence
        try:
            publish._validate(worktree, [record], True)
        except publish.PublishError as exc:
            assert "test_bound.py" in str(exc), str(exc)
        else:
            raise AssertionError("a red proof passed the flagged full battery")


def test_battery_mode_is_full_only_on_the_flag() -> None:
    publish = _load_publish()
    for relpath, kind, op in [(".claude/hooks/tool.py", "lock", "M"), ("tools/a.py", "root", "M"),
                              (".claude/settings.base.json", "lock", "M"), (".claude/tests/test_gone.py", "lock", "D"),
                              (".claude/commands/doc.md", "lock", "M")]:
        assert publish._battery_mode([{"relpath": relpath, "kind": kind, "op": op}], False)[0] == "ci", relpath
    assert publish._battery_mode([], True) == ("full", "flag")


def test_unflagged_validate_runs_no_root_proof_and_no_battery() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = _validate_fixture(path, with_tests=True)
        _stub_validate_seams(publish, {"tests/test_a.py": "BOOTHIT", "harness_tests.py": "BATTERYHIT"})
        evidence = publish._validate(worktree, [_hook_record()], False)
    assert "mode=ci" in evidence and "tests/test_a.py=" not in evidence, evidence


def _validate_fixture(path: Path, with_tests: bool = False) -> Path:
    worktree = path / "worktree"
    _write(worktree / "template/.claude/scripts/harness_tests.py", b"# stand-in runner\n")
    _write(worktree / ".github/workflows/baseline.yml", b"name: baseline\n")
    if with_tests:
        _write(worktree / "tests/test_a.py", b"raise SystemExit(0)\n")
    return worktree


def _stub_validate_seams(publish, red_labels):
    """Stub only the subprocess seams: each python child (by label) and the count check."""

    def fake_run(worktree, args, label, env=None):
        if label in red_labels:
            raise publish.PublishError(f"{label} failed: {red_labels[label]}")
        return f"{label} ok"

    publish._run_python_args = fake_run
    publish._battery_counts_match = lambda worktree: None


def _hook_record() -> dict:
    return {"relpath": ".claude/hooks/x.py", "kind": "lock", "op": "M"}


def _validate_error(publish, worktree, records) -> str:
    try:
        publish._validate(worktree, records, True)
    except publish.PublishError as exc:
        return str(exc)
    raise AssertionError("validate passed; expected a red")


def test_validate_reports_every_red_in_one_evidence_string() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = _validate_fixture(path, with_tests=True)
        reds = {"gen_manifest.py --check": "manifest drift", "audit_baseline.py --strict": "audit hit AUDITHIT",
                "tests/test_a.py": "bootstrap crash BOOTHIT", "harness_tests.py": "FAIL BATTERYHIT"}
        _stub_validate_seams(publish, reds)
        message = _validate_error(publish, worktree, [_hook_record()])
        for needle in ("manifest drift", "AUDITHIT", "BOOTHIT", "BATTERYHIT"):
            assert needle in message, (needle, message)


def test_validate_shares_slots_with_battery_and_times_each_task() -> None:
    """The battery coordinates its own admitted proofs; checks share their pool, and every task is timed."""
    publish = _load_publish()
    with _fixture() as path:
        worktree = _validate_fixture(path, with_tests=True)
        spans: list[tuple[str, float, float]] = []
        lock = threading.Lock()
        battery_up, check_up = threading.Event(), threading.Event()
        real_guard = publish.machine_slots.guard
        active, peak, asked = 0, 0, []

        @contextlib.contextmanager
        def check_guard(*args, **kwargs):
            asked.append(kwargs.get("notice"))
            assert battery_up.wait(10), "battery coordinator did not start its proof"
            with real_guard(*args, **kwargs) as held:
                yield held

        def fake_run(worktree, args, label, env=None):
            nonlocal active, peak
            # This stands for the battery's child proof, not a reservation by _validate.
            with real_guard() if label == "harness_tests.py" else contextlib.nullcontext():
                start = time.monotonic()
                with lock:
                    active += 1
                    peak = max(peak, active)
                assert env["HARNESS_MACHINE_ROOT"] == pool, env
                if label == "harness_tests.py":
                    battery_up.set()
                    assert check_up.wait(10), "checks were barred behind the coordinator"
                else:
                    assert env["HARNESS_SLOT_BUDGET"] == "1", env
                    check_up.set()
                with lock:
                    active -= 1
                    spans.append((label, start, time.monotonic()))
            return f"{label} ok"

        publish._run_python_args = fake_run
        publish._battery_counts_match = lambda worktree: None
        publish.machine_slots.guard = check_guard
        pool = str(path / "slots")
        try:
            with _patched_env({"HARNESS_MACHINE_SLOTS": "2", "HARNESS_SLOT_BUDGET": "",
                               "HARNESS_SLOT_DIR": pool, "HARNESS_MACHINE_ROOT": pool}):
                evidence = publish._validate(worktree, [_hook_record()], True)
        finally:
            publish.machine_slots.guard = real_guard
        labels = [label for label, _, _ in spans]
        assert "windows=" in evidence and "checks=" in evidence and "battery=" in evidence, evidence
        assert asked and all(asked), ("publish admits each check through the pool with its queue notice", asked)
        for label in ("harness_tests.py", "gen_manifest.py --check", "audit_baseline.py --strict", "tests/test_a.py"):
            assert label in labels, labels
            assert f"{label}=" in evidence, (label, evidence)
        _, battery_start, battery_end = next(s for s in spans if s[0] == "harness_tests.py")
        beside = [label for label, start, end in spans
                  if label != "harness_tests.py" and start < battery_end and end > battery_start]
        assert beside, ("checks must be able to share free capacity beside a battery proof", spans)
        assert peak == 2, ("battery proof and checks share the two-slot cap", peak)


def test_validate_lists_two_red_checks_and_a_red_battery_in_one_error() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = _validate_fixture(path, with_tests=True)
        reds = {"audit_baseline.py --strict": "AUDITHIT", "tests/test_a.py": "BOOTHIT", "harness_tests.py": "BATTERYHIT"}
        _stub_validate_seams(publish, reds)
        message = _validate_error(publish, worktree, [_hook_record()])
        assert "3 red(s)" in message, message
        for needle in ("AUDITHIT", "BOOTHIT", "BATTERYHIT"):
            assert needle in message, (needle, message)


def test_a_full_mode_validate_spawns_no_linux_preview_child() -> None:
    publish = _load_publish()
    spawned: list[list[str]] = []

    class _RecordedProc:
        returncode = 0

        def __init__(self, argv, *_a, **_k) -> None:
            spawned.append([str(a) for a in argv])

        def wait(self, *_a, **_k) -> int:
            return 0

        def poll(self):
            return 0

        def kill(self) -> None:
            pass

    real_popen = subprocess.Popen
    with _fixture() as path:
        worktree = _validate_fixture(path)
        publish._run_python_args = lambda worktree, args, label, env=None: f"{label} ok"
        publish._battery_counts_match = lambda worktree: None
        subprocess.Popen = _RecordedProc
        try:
            evidence = publish._validate(worktree, [], True)
        finally:
            subprocess.Popen = real_popen
    assert "mode=full" in evidence, evidence
    assert not [argv for argv in spawned if any("baseline_linux_preview.py" in a for a in argv)], spawned
    assert "preview=" not in evidence, evidence


def test_pr_body_lists_each_carried_row() -> None:
    publish = _load_publish()
    lock = {"files": {
        ".claude/tools/a.py": {"judged": {"sha": "b" * 64, "verdict": "push", "carried_from": "a" * 64}},
        ".claude/tools/b.py": {"judged": {"sha": "c" * 64, "verdict": "push"}},
    }}
    rows = [".claude/tools/a.py", ".claude/tools/b.py"]
    with tempfile.TemporaryDirectory() as tmp:
        body = Path(tmp) / "pr.md"
        publish._write_pr_body(body, "id1", {"kind": "commit", "repo": "r", "commit": "x"}, rows, [],
                               publish._carried_rows(lock, rows))
        text = body.read_text(encoding="utf-8")
        plain = Path(tmp) / "plain.md"
        publish._write_pr_body(plain, "id2", {"kind": "commit", "repo": "r", "commit": "x"}, rows[1:], [],
                               publish._carried_rows(lock, rows[1:]))
        plain_text = plain.read_text(encoding="utf-8")
    assert "## Carried rows" in text and "- .claude/tools/a.py: judged aaaaaaaa, carried to bbbbbbbb" in text, text
    assert ".claude/tools/b.py: judged" not in text, text
    assert "## Carried rows" not in plain_text, plain_text

def test_publish_commit_message_names_rows_subject_and_ledger_reason() -> None:
    publish = _load_publish()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "source"
        _init_repo(repo)
        _write(repo / "a.txt", b"a\n")
        sha = _commit(repo, "feat: seed the source commit")
        ledger = root / ".claude" / "autolearn_outcomes.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"date": "2026-11-01", "session": "s", "target": ".claude/tools/a.py",
             "signal": "old", "recommended": "apply", "outcome": "applied", "reason": "old reason"},
            {"date": "2026-11-02", "session": "s", "target": ".claude/tools/a.py",
             "signal": "new", "recommended": "apply", "outcome": "applied", "reason": "newest reason"},
            {"date": "2026-11-02", "session": "s", "target": ".claude/tools/elsewhere.py",
             "signal": "x", "recommended": "apply", "outcome": "applied", "reason": "not published"},
        ]
        ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
        journal = {
            "id": "20260101T000000Z-aaaaaaaa",
            "source": {"kind": "commit", "repo": str(repo), "commit": sha},
            "rows_published": [".claude/tools/a.py", ".claude/tools/b.py"],
            "row_meta": {".claude/tools/a.py": {"op": "M"}, ".claude/tools/b.py": {"op": "A"}},
        }
        profile = publish.baseline_identity.build_profile()
        label = publish.baseline_identity._consumer_label("", root.name)
        title, body = publish._publish_message(root, journal, profile, label)
        ledger.unlink()
        _title2, body2 = publish._publish_message(root, journal, profile, label)
        empty = root / "empty"
        _init_repo(empty)
        _write(empty / "kept.txt", b"kept\n")
        _commit(empty, "seed")
        try:
            publish._publish(empty, {"id": "x", "source": journal["source"], "worktree": {"committed": False},
                                     "rows_published": [], "row_meta": {}},
                             root / "journal.json", root, False, "b", "x", profile)
        except publish.PublishError:
            pass
        else:
            raise AssertionError("expected an empty publication to be refused")
    assert "20260101T000000Z-aaaaaaaa" in title, title
    assert label in body and sha in body, body
    assert str(repo) not in body, body
    assert "feat: seed the source commit" in body, body
    assert "- .claude/tools/a.py (M)" in body and "- .claude/tools/b.py (A)" in body, body
    assert ".claude/tools/a.py: newest reason" in body, body
    assert body.count("Why:") == 1 and "old reason" not in body and "elsewhere" not in body, body
    assert "Why:" not in body2, body2


def test_publish_message_withholds_identity_lines_and_names_the_consumer_label() -> None:
    """The message enters the shared baseline's history: the source is the hashed consumer label,
    never the local checkout path, and any free-text line the identity profile flags is left out
    and recorded in the journal -- its text never reaching either the commit body or the PR body."""
    publish = _load_publish()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "source"
        _init_repo(repo)
        _write(repo / "a.txt", b"a\n")
        sha = _commit(repo, "feat: ship ZenithOrb to the guild")
        lock = {"baseline_repo": "git@example.invalid:org/baseline.git",
                "substitutions": {"acme": "ZenithOrb"}}
        _write(root / ".claude" / "baseline.lock.json", json.dumps(lock).encode())
        ledger = root / ".claude" / "autolearn_outcomes.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"date": "2026-11-02", "session": "s", "target": ".claude/tools/a.py",
             "signal": "x", "recommended": "apply", "outcome": "applied",
             "reason": "rename ZenithOrb everywhere"},
            {"date": "2026-11-02", "session": "s", "target": ".claude/tools/b.py",
             "signal": "y", "recommended": "apply", "outcome": "applied",
             "reason": "clean reason"},
        ]
        ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
        journal = {
            "id": "20260101T000000Z-bbbbbbbb",
            "source": {"kind": "commit", "repo": str(repo), "commit": sha},
            "rows_published": [".claude/tools/a.py", ".claude/tools/b.py"],
            "row_meta": {".claude/tools/a.py": {"op": "M"}, ".claude/tools/b.py": {"op": "A"}},
        }
        profile = publish.baseline_identity.build_profile_for_repo(root)
        label = publish.baseline_identity._consumer_label(lock["baseline_repo"], root.name)
        title, body = publish._publish_message(root, journal, profile, label)
        pr_body = root / "pr.md"
        publish._write_pr_body(pr_body, journal["id"], journal["source"], journal["rows_published"],
                               [], [], journal["row_meta"], label, journal["message_withheld"])
        text = pr_body.read_text(encoding="utf-8")
    assert f"commit {label} @ {sha}" in body, body
    assert str(repo) not in body, body
    assert "ZenithOrb" not in body, body
    assert "Source subject:" not in body, body
    assert "- .claude/tools/a.py (M)" in body and "- .claude/tools/b.py (A)" in body, body
    assert ".claude/tools/b.py: clean reason" in body, body
    assert "rename" not in body, body
    assert [entry["row"] for entry in journal["message_withheld"]] == [
        "source_subject", ".claude/tools/a.py"], journal["message_withheld"]
    assert all(entry["hit_kinds"] for entry in journal["message_withheld"]), journal["message_withheld"]
    assert label in text and str(repo) not in text and "ZenithOrb" not in text, text
    assert "2 withheld" in text and "source_subject" in text and ".claude/tools/a.py" in text, text


_RESET_STDERR = b"fatal: unable to access 'https://example.invalid/x.git/': Recv failure: Connection was reset\n"


def _replay_runs(outcomes):
    calls = []

    def _run(cmd, *args, **kwargs):
        calls.append(cmd if isinstance(cmd, str) else list(cmd))
        code, err = outcomes[min(len(calls), len(outcomes)) - 1]
        return subprocess.CompletedProcess(cmd, code, stdout=b"[]", stderr=err)

    return _run, calls


def _with_replay(outcomes, call):
    from unittest import mock
    publish = _load_publish()
    run, calls = _replay_runs(outcomes)
    with mock.patch.object(publish.sync, "NETWORK_RETRY_DELAYS", (0, 0), create=True), \
            mock.patch.object(subprocess, "run", run), \
            mock.patch.object(publish, "_resolve_gh", lambda: "gh"), \
            contextlib.redirect_stderr(io.StringIO()):
        return publish, calls, call(publish)


def test_a_push_that_resets_once_is_retried() -> None:
    _p, calls, result = _with_replay(
        [(128, _RESET_STDERR), (0, b"")],
        lambda publish: publish._git(Path("wt"), ["push", "-q", "-f", "origin", "HEAD:refs/heads/b"]))
    assert result.returncode == 0 and len(calls) == 2, calls


def test_gh_pr_create_is_never_retried() -> None:
    _p, calls, result = _with_replay(
        [(1, _RESET_STDERR)],
        lambda publish: publish._gh(Path("wt"), ["pr", "create", "--head", "b", "--base", "main"]))
    assert result.returncode == 1 and len(calls) == 1, calls


def test_gh_pr_checks_is_retried_on_a_reset() -> None:
    _p, calls, result = _with_replay(
        [(1, _RESET_STDERR), (0, b"")],
        lambda publish: publish._gh(Path("wt"), ["pr", "checks", "url", "--json", "name,state"]))
    assert result.returncode == 0 and len(calls) == 2, calls


def test_a_gh_read_that_times_out_is_retried() -> None:
    from unittest import mock
    publish = _load_publish()
    calls = []

    def _run(cmd, *args, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"[]", stderr=b"")

    with mock.patch.object(publish.sync, "NETWORK_RETRY_DELAYS", (0, 0)), \
            mock.patch.object(subprocess, "run", _run), \
            mock.patch.object(publish, "_resolve_gh", lambda: "gh"), \
            contextlib.redirect_stderr(io.StringIO()):
        result = publish._gh(Path("wt"), ["pr", "checks", "url", "--json", "name,state"])
    assert result.returncode == 0 and len(calls) == 2, calls


def test_gh_carries_the_network_timeout() -> None:
    from unittest import mock
    seen = []

    def _run(cmd, *args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    publish = _load_publish()
    with mock.patch.object(subprocess, "run", _run), mock.patch.object(publish, "_resolve_gh", lambda: "gh"):
        publish._gh(Path("wt"), ["pr", "view", "url"])
    assert len(seen) == 1 and seen[0] and seen[0] > 0, seen


def test_gh_api_post_is_never_retried_and_api_get_is() -> None:
    _p, calls, result = _with_replay(
        [(1, _RESET_STDERR)],
        lambda publish: publish._gh(Path("wt"), ["api", "-X", "POST", "repos/o/r/x"]))
    assert len(calls) == 1, calls
    _p, calls, result = _with_replay(
        [(1, _RESET_STDERR), (0, b"")],
        lambda publish: publish._gh(Path("wt"), ["api", "repos/o/r/x"]))
    assert len(calls) == 2, calls


def test_unknown_check_bucket_fails_step_six_closed() -> None:
    # A bucket outside pass/skipping/pending (missing, or one gh adds later) is not a green verdict.
    publish = _load_publish()
    for record in ({"name": "baseline", "state": "ODD", "bucket": "mystery"}, {"name": "baseline", "state": "ODD"}):
        answer = subprocess.CompletedProcess([], 0, json.dumps([record]).encode(), b"")
        original = publish._gh
        publish._gh = lambda _cwd, _args, answer=answer: answer
        try:
            green, detail = publish._wait_for_checks(Path("."), "https://example.invalid/pr/1")
        finally:
            publish._gh = original
        assert green is False and "baseline" in detail, (record, green, detail)


def test_publishing_a_binary_row_records_the_sha_of_its_bytes() -> None:
    """update-lock and check read a binary row through the engine's binary reader, never a UTF-8 decode."""
    rel = ".claude/tools/grammar.wasm"
    old = b"\x00asm\x01\x00\x00\x00old"
    new = b"\x00asm\x01\x00\x00\x00\r\n\r\xff\xfe\x80" + bytes(range(256))
    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(path, rel, old, new)
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        with _patched_env(env):
            journal = publish.run(root, {"kind": "commit", "repo": str(root), "commit": source_commit},
                                  [rel], [], False, True, None)
        steps = _field(journal, "steps")
        assert all(step["status"] == "green" for step in steps), steps
        assert _git(remote, "show", "main:template/" + rel) == new
        row = json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))["files"][rel]
        assert row["hash"] == hashlib.sha256(new).hexdigest(), row


def test_binary_bytes_survive_the_publish_copy() -> None:
    # The first binary row (a WASM grammar) grew 5.35 MB to 6.19 MB: line endings and UTF-8 replacement
    # rewrote it, and the consumer's checksum then failed.
    publish = _load_publish()
    binary = b"\x00asm\x01\x00\x00\x00\r\n\r\xff\xfe\x80" + bytes(range(256))
    assert publish._lf(binary) == binary
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "grammar.wasm"
        publish._write_lf(target, binary)
        assert target.read_bytes() == binary
    assert publish._lf(b"a\r\nb\rc") == b"a\nb\nc"


def test_only_the_full_battery_child_asks_for_reused_passes() -> None:
    publish = _load_publish()
    with _fixture() as path:
        worktree = _validate_fixture(path, with_tests=True)
        seen: dict[str, dict] = {}

        def fake_run(_worktree, _args, label, env=None):
            seen[label] = dict(env or {})
            return f"{label} ok"

        publish._run_python_args = fake_run
        publish._battery_counts_match = lambda worktree: None
        publish._validate(worktree, [], True)
        assert seen["harness_tests.py"].get("HARNESS_REUSE_PASSES") == "1", seen
        seen.clear()
        publish._validate(worktree, [{"relpath": ".claude/commands/doc.md", "kind": "lock", "op": "M"}], False)
        assert not [label for label in seen if label.startswith("harness_tests.py")], seen


def test_classify_lists_exactly_what_the_candidates_command_lists():
    """Step 1 refuses on the same candidates `baseline_sync.py candidates` prints: a never_ship path and
    a row-less test of no shipped tool are no decision to make, so neither blocks a publish."""
    publish = _load_publish()
    with _fixture() as root:
        _init_repo(root)
        _write(root / ".claude" / "auto-memory" / "note.md", b"note\n")
        _write(root / ".claude" / "tests" / "test_orphan.py", b"pass\n")
        _write(root / ".claude" / "tools" / "new_tool.py", b"pass\n")
        commit = _commit(root, "consumer")
        lock = {"never_ship": [".claude/auto-memory/"], "files": {}}
        found = publish._candidates({"kind": "commit", "repo": str(root), "commit": commit}, lock)
        assert found == [".claude/tools/new_tool.py"], found


# The cases run as PARTS proof files side by side in the runner's pool: this file is part 1 and
# `test_baseline_publish_part<K>.py` run the rest, each case in exactly one part (round-robin), so the close waits on one
# part's time, not the whole proof's.
PARTS = 2


RED_PREVIEW = {"status": "red", "log": "linux.log", "lanes": [
    {"lane": "pure", "status": "red", "seconds": 40, "failed_steps": ["baseline-repo-root proofs"],
     "failed_proofs": ["tests/test_sync_guard.py"], "counts": None},
    {"lane": "template", "status": "green", "seconds": 900, "failed_steps": [], "failed_proofs": [], "counts": None},
]}


def test_the_push_runs_no_local_linux_preview_the_prs_ci_is_the_linux_check() -> None:
    # The PR's CI runs every Linux lane's whole battery after the push, and a red required check blocks
    # the merge. A local WSL run before the push was measured slower than CI each time it was tried
    # (sync_baseline Publish; 0aad5783c, 18c0fa1a8, and a 441-row publish's 54+ min run).
    rel = ".claude/tools/fixture.py"
    sys.path.insert(0, str(PUBLISH.parent))
    import baseline_linux_preview as preview_module
    saved = preview_module.preview
    calls = []

    def recorded(worktree, only=None, paths=None):
        calls.append(paths)
        return 1, RED_PREVIEW

    with _fixture() as path:
        remote, _baseline_commit, root = _seed_commit_fixture(path, rel, b"value = 'old'\n", b"value = 'fixture'\n")
        source_commit = _git(root, "rev-parse", "HEAD").decode().strip()
        publish = _load_publish()
        env = _install_fake_gh(path)
        preview_module.preview = recorded
        try:
            with _patched_env(env):
                publish.run(root, {"kind": "commit", "repo": str(root), "commit": source_commit},
                            [rel], [], False, True, None)
        finally:
            preview_module.preview = saved
        refs = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/").decode().split()
    assert calls == [], calls
    assert any(ref.startswith("refs/heads/publish/") for ref in refs), refs


def main(part: int = 1) -> int:
    for other in range(2, PARTS + 1):  # a deleted part file would silently drop its cases
        assert (Path(__file__).parent / ("test_baseline_publish_part%d.py" % other)).is_file(), "missing part %d" % other
    cases = [
        test_classify_lists_exactly_what_the_candidates_command_lists,
        test_open_only_opens_the_pr_before_the_land_and_the_merge_waits_for_it,
        test_open_only_is_a_resume_flag_and_excludes_dry_run,
        test_only_the_full_battery_child_asks_for_reused_passes,
        test_binary_bytes_survive_the_publish_copy,
        test_publishing_a_binary_row_records_the_sha_of_its_bytes,
        test_unknown_check_bucket_fails_step_six_closed,
        test_pr_body_lists_each_carried_row,
        test_publish_commit_message_names_rows_subject_and_ledger_reason,
        test_publish_message_withholds_identity_lines_and_names_the_consumer_label,
        test_validate_leaves_the_battery_to_ci_unless_the_flag_asks,
        test_battery_mode_is_full_only_on_the_flag,
        test_unflagged_validate_runs_no_root_proof_and_no_battery,
        test_validate_reports_every_red_in_one_evidence_string,
        test_validate_shares_slots_with_battery_and_times_each_task,
        test_validate_lists_two_red_checks_and_a_red_battery_in_one_error,
        test_a_full_mode_validate_spawns_no_linux_preview_child,
        test_a_finished_publish_removes_its_worktree_and_a_dry_run_keeps_it,
        test_sweep_removes_terminal_and_journalless_worktrees_and_keeps_a_live_one,
        test_abandon_releases_the_worktree_and_resume_refuses_the_journal,
        test_an_abandoned_journal_never_blocks_a_fresh_publication,
        test_the_push_runs_no_local_linux_preview_the_prs_ci_is_the_linux_check,
        test_publish_renames_project_identifiers_without_touching_the_local_file,
        test_journal_and_lock_writes_wait_out_a_reader_holding_the_target,
        test_tracer_publishes_one_row_to_fixture_remote,
        test_peer_edit_after_the_source_commit_leaves_step_eight_green,
        test_collect_without_rows_takes_only_push_verdicts,
        test_collect_publishes_a_committed_deletion_of_a_push_row,
        test_materialize_keeps_a_100755_source_entry_executable,
        test_materialize_stages_a_judged_row_the_baseline_gitignore_matches,
        test_materialize_writes_source_bytes_unsubstituted,
        test_materialize_records_new_row_layers_and_regenerates_manifest,
        test_materialize_records_a_layer_move_for_an_existing_row,
        test_materialize_records_no_entry_for_an_unmoved_existing_row,
        test_materialize_refuses_a_new_row_without_a_layer_before_creating_the_worktree,
        test_worktree_source_refuses_a_new_row_without_a_layer_before_creating_the_worktree,
        test_worktree_source_records_a_layered_new_row,
        test_worktree_source_takes_a_new_row_layer_its_own_layer_entries_record,
        test_worktree_source_takes_a_new_row_layer_its_manifest_records,
        test_worktree_source_layers_outrank_the_consumer_lock,
        test_publication_commit_carries_a_message_past_the_command_line_limit,
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
        test_pending_checks_are_polled_until_green,
        test_unreadable_poll_answers_are_polled_again,
        test_unreadable_poll_answers_fail_step_six_after_the_wait,
        test_ci_on_publish_records_the_ci_run_url,
        test_ci_red_checks_record_the_ci_run_url,
        test_a_red_non_required_lane_does_not_refuse_the_merge,
        test_a_red_required_check_still_refuses_the_merge,
        test_ci_checks_that_never_register_fail_step_six_after_the_wait,
        test_baseline_moved_before_merge_marks_step_red_and_fresh_publish_supersedes,
        test_publish_reads_the_check_state_of_the_rows_it_ships,
        test_publish_refuses_a_shipped_row_the_baseline_moved_since_its_base,
        test_publish_refuses_a_shipped_row_whose_bound_proof_stays_behind,
        test_a_forked_bound_proof_left_behind_passes_step_one,
        test_a_decline_is_recorded_where_a_later_publish_reads_it_and_holds_for_that_upstream_only,
        test_a_publish_that_reaches_upstream_clears_the_decline,
        test_resume_redoes_steps_after_deleted_worktree,
        test_step_keeps_the_red_attempt_when_it_reruns_green,
        test_corrupt_journal_resume_exits_nonzero,
        test_from_worktree_refuses_dirty_foreign_sibling_and_nested,
        test_absorb_keeps_a_later_author_deletion_and_carries_the_publish_rows,
        test_absorb_keeps_a_later_author_edit,
        test_absorb_stops_on_a_publish_change_that_conflicts_with_a_later_author_edit,
        test_author_history_never_publishes_ancestor_token,
        test_from_worktree_publishes_root_files_and_lock_rows,
        test_publish_strips_memory_provenance_keys_from_the_published_file,
        test_battery_count_mismatch_is_detected,
        test_diff_records_parses_copy_as_addition_only,
        test_from_worktree_publishes_every_diff_record_including_root_config_files,
        test_publish_does_not_alter_cache_clone_shared_identity,
        test_worktree_source_leaves_hash_for_consumer_pull_when_out_of_sync,
        test_publish_refuses_malformed_adaptation_contract_fresh_run,
        test_publish_resume_refuses_malformed_adaptation_contract,
        test_published_forked_row_records_tracked_not_forked,
        test_published_local_row_records_tracked_not_local,
        test_scrub_honors_the_hits_a_judge_accepted_on_each_row,
        test_upstream_deletion_keeps_the_row_of_a_file_the_consumer_still_holds,
        test_update_lock_keeps_a_verdict_judged_while_the_publish_ran,
        test_worktree_deletion_leaves_every_row_for_the_consumer_pull,
        test_full_battery_upgrade_refuses_a_published_journal,
        test_a_push_that_resets_once_is_retried,
        test_gh_pr_create_is_never_retried,
        test_gh_pr_checks_is_retried_on_a_reset,
        test_a_gh_read_that_times_out_is_retried,
        test_gh_carries_the_network_timeout,
        test_gh_api_post_is_never_retried_and_api_get_is,
    ]
    # The fixtures run a copied harness_tests.py, which takes machine slots; peers holding the shared
    # ones starved a case for minutes. Case processes inherit this slot dir.
    slots = tempfile.mkdtemp(prefix="publish_slots_")
    os.environ["HARNESS_SLOT_DIR"] = slots
    try:
        return run_cases(cases[part - 1::PARTS])
    finally:
        shutil.rmtree(slots, ignore_errors=True)



def test_upstream_deletion_keeps_the_row_of_a_file_the_consumer_still_holds() -> None:
    """An upstream `D` drops the lock row only when the consumer's file is gone too.

    A file the baseline stops shipping stays this project's own while it exists here: a row
    already `local` keeps its fields, and a `tracked` row becomes `local`. Dropping either leaves
    an on-disk `.claude/` file with no row, which the commit guard refuses.
    """
    publish = _load_publish()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        held_local = ".claude/commands/held_local.md"
        held_tracked = ".claude/commands/held_tracked.md"
        gone = ".claude/commands/gone.md"
        for rel in (held_local, held_tracked):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n", encoding="utf-8")
        _write_lock(root, {"schema": 2, "files": {
            held_local: {"status": "local", "layer": "coding"},
            held_tracked: {"status": "tracked", "layer": "pure"},
            gone: {"status": "tracked", "layer": "pure"},
        }})
        records = [{"kind": "lock", "relpath": rel, "op": "D"} for rel in (held_local, held_tracked, gone)]
        publish._update_lock(root, records, root, "after", {"kind": "commit"})
        files = _read_lock(root)["files"]
        assert files.get(held_local) == {"status": "local", "layer": "coding"}, files.get(held_local)
        assert files.get(held_tracked, {}).get("status") == "local", files.get(held_tracked)
        assert "hash" not in files.get(held_tracked, {}), files.get(held_tracked)
        assert gone not in files, files.get(gone)


def _write_lock(root: Path, lock: dict) -> None:
    path = root / ".claude" / "baseline.lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock), encoding="utf-8")


def _read_lock(root: Path) -> dict:
    return json.loads((root / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))


def test_update_lock_keeps_a_verdict_judged_while_the_publish_ran() -> None:
    """The lock step applies its rows to the lock as it is on disk when the step runs.

    A publish runs for many minutes; a `judge` in that window writes the lock. A step that saves
    the snapshot it loaded at publish start erases that verdict.
    """
    publish = _load_publish()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gone, other = ".claude/commands/gone.md", ".claude/commands/other.md"
        at_start = {"schema": 2, "files": {
            gone: {"status": "tracked", "layer": "pure", "hash": "h", "judged": None},
            other: {"status": "tracked", "layer": "pure", "hash": "h", "judged": None},
        }}
        _write_lock(root, at_start)
        judged = json.loads(json.dumps(at_start))
        judged["files"][other]["judged"] = {"verdict": "push", "sha": "s2"}
        _write_lock(root, judged)
        records = [{"kind": "lock", "relpath": gone, "op": "D"}]
        publish._update_lock(root, records, root, "after", {"kind": "commit"})
        files = _read_lock(root)["files"]
        assert gone not in files, files
        assert files[other]["judged"] == {"verdict": "push", "sha": "s2"}, files[other]


def test_worktree_deletion_leaves_every_row_for_the_consumer_pull() -> None:
    """A `--from-worktree` upstream `D` changes no lock row.

    The consumer's copy is pre-publication content, so its own `pull` decides: an unmodified file
    is retired (file and row), a modified one is kept as `local`, and a missing one loses its row.
    Rewriting the row here would hide the deletion from that pull.
    """
    publish = _load_publish()

    def rows() -> dict:
        return {"files": {
            ".claude/commands/held_local.md": {"status": "local", "layer": "coding"},
            ".claude/commands/held_tracked.md": {"status": "tracked", "layer": "pure"},
            ".claude/commands/gone.md": {"status": "tracked", "layer": "pure"},
        }}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in (".claude/commands/held_local.md", ".claude/commands/held_tracked.md"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x\n", encoding="utf-8")
        _write_lock(root, {"schema": 2, **rows()})
        records = [{"kind": "lock", "relpath": rel, "op": "D"} for rel in rows()["files"]]
        publish._update_lock(root, records, root, "after", {"kind": "worktree"})
        assert _read_lock(root)["files"] == rows()["files"], _read_lock(root)


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
    _assert_published_row_records_tracked("forked")


def test_published_local_row_records_tracked_not_local() -> None:
    """A `local` row judged `push` ships upstream; left `local`, the next `check --strict`
    reads it as local-shadows-upstream against the baseline that now carries it."""
    _assert_published_row_records_tracked("local")


def _assert_published_row_records_tracked(status: str) -> None:
    rel = ".claude/tools/fixture_%s.py" % status
    baseline_content = b"value = 'old'\n"
    local_content = b"value = 'fixture'\n"
    with _fixture() as path:
        remote, baseline_commit, root = _seed_commit_fixture(path, rel, baseline_content, local_content)
        lock_path = root / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"][rel]["status"] = status
        if status == "forked":
            lock["files"][rel]["base"] = baseline_commit
        # Schema 2 is what makes the consequence reachable: on a v1-shaped lock `_v2_state`
        # short-circuits a forked row to plain `forked` (quiet), so the drift this case exists
        # to prevent would not appear and the case would prove only the row-dict change.
        lock["schema"] = 2
        _write(lock_path, (json.dumps(lock, indent=2) + "\n").encode())
        _commit(root, "%s row judged push" % status)
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

        updated_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        row = updated_lock["files"][rel]
        assert row["status"] == "tracked", (
            "a published row now holds upstream's content; leaving it `%s` makes the "
            "next check read it as drift: %r" % (status, row)
        )
        assert "base" not in row, row
        # Publish is the one tool that knows the row settled: its `push` verdict goes, so the
        # next publish does not collect the same row again.
        assert (row.get("judged") or {}).get("verdict") != "push", row
        again = publish._collect(
            root, {"kind": "commit", "repo": str(root), "commit": source_commit},
            None, updated_lock, None, "unused")
        assert again == [], again


if __name__ == "__main__":
    sys.exit(main())
