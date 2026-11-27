#!/usr/bin/env python3
"""Proof for hooks/_git_run.py, the one git subprocess every hook and tool spawns through.

    python3 .claude/tests/test_git_run.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import _git_run as gr  # noqa: E402


def _fake(returncode=0, stdout=b"", stderr=b""):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return run, seen


def _no_git_on_path():
    """PATH holding no git, so a case about argv and env meets no launcher whatever the caller's PATH."""
    return mock.patch.dict(os.environ, {"PATH": tempfile.mkdtemp(prefix="gitrun_nopath_")})


def _plant_install(root):
    """A Git for Windows layout: the `cmd` launcher, the `mingw64/bin` binary it runs, and `usr/bin`."""
    name = "git.exe" if os.name == "nt" else "git"
    for parts in (("cmd", name), ("bin", name), ("mingw64", "bin", "git.exe"), ("mingw64", "bin", name)):
        path = os.path.join(root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb"):
            pass
        os.chmod(path, 0o755)
    os.makedirs(os.path.join(root, "usr", "bin"), exist_ok=True)


def case_argv_prefix_order_and_optional_locks_flag():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake), _no_git_on_path():
        gr.run(["status"], prefix=("-C", "x", "-c", "a=b"), optional_locks=False)
        assert seen["argv"] == ["git", "--no-optional-locks", "-C", "x", "-c", "a=b", "status"], seen["argv"]
        gr.run(["log"])
        assert seen["argv"] == ["git", "log"], seen["argv"]


def case_env_overlay_unsets_a_none_value():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake), mock.patch.dict(os.environ, {"GIT_DIR": "/x", "KEEP": "1"}), \
            _no_git_on_path():
        gr.run(["log"], env={"GIT_DIR": None, "GIT_INDEX_FILE": "idx"})
        env = seen["env"]
        assert "GIT_DIR" not in env and env["GIT_INDEX_FILE"] == "idx" and env["KEEP"] == "1"


def case_empty_overlay_inherits_the_environment():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake), _no_git_on_path():
        gr.run(["log"])
        assert seen["env"] is None
        gr.run(["log"], env={})
        assert seen["env"] is None


def case_bytes_in_bytes_out_keep_crlf_and_binary():
    payload = b"a\r\nb\x00c\r\n"
    repo = tempfile.mkdtemp(prefix="gitrun_")
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        blob = gr.run(["hash-object", "-w", "--stdin"], cwd=repo, input=payload)
        assert blob.returncode == 0
        oid = blob.stdout.decode().strip()
        out = gr.run(["cat-file", "blob", oid], cwd=repo)
        assert out.stdout == payload, out.stdout
        assert isinstance(out.stdout, bytes) and isinstance(out.stderr, bytes)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def case_empty_success_is_returncode_0_not_none():
    fake, _ = _fake(0, b"")
    with mock.patch.object(subprocess, "run", fake):
        res = gr.run(["tag", "-l"])
    assert res is not None and res.returncode == 0 and res.stdout == b""
    assert gr.soft_text(res) == ""


def case_nonzero_returns_the_code_and_stderr():
    fake, _ = _fake(128, b"", b"fatal: nope")
    with mock.patch.object(subprocess, "run", fake):
        res = gr.run(["log"])
    assert (res.returncode, res.stderr) == (128, b"fatal: nope")


def case_missing_git_raises_oserror():
    def boom(*a, **k):
        raise FileNotFoundError("git")
    with mock.patch.object(subprocess, "run", boom):
        try:
            gr.run(["log"])
        except OSError:
            return
    raise AssertionError("a missing git must raise OSError")


def case_timeout_raises_timeoutexpired():
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 1)
    with mock.patch.object(subprocess, "run", boom):
        try:
            gr.run(["log"], timeout=1)
        except subprocess.TimeoutExpired:
            return
    raise AssertionError("a timeout must raise TimeoutExpired")


def case_soft_text_maps_nonzero_timeout_and_missing_to_none():
    fake, _ = _fake(1, b"out")
    with mock.patch.object(subprocess, "run", fake):
        assert gr.soft_text(gr.run(["log"])) is None
        assert gr.soft_text(["log"]) is None
    for exc in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 1)):
        def boom(*a, _e=exc, **k):
            raise _e
        with mock.patch.object(subprocess, "run", boom):
            assert gr.soft_text(["log"]) is None, exc
    fake, _ = _fake(0, "é\n".encode("utf-8") + b"\xff")
    with mock.patch.object(subprocess, "run", fake):
        assert gr.soft_text(["log"]).startswith("é\n")


def case_text_and_err_text_decode_utf8_with_replace():
    res = gr.GitResult(0, "héllo\n".encode("utf-8") + b"\xff\n", b"bad \x80 bytes")
    assert res.text == "héllo\n�\n", res.text
    assert res.err_text == "bad � bytes", res.err_text


def case_check_returns_self_on_zero_and_raises_with_argv_and_streams():
    ok = gr.GitResult(0, b"out", b"")
    assert ok.check(["status"]) is ok
    bad = gr.GitResult(3, b"stdout-bytes", b"stderr-bytes")
    try:
        bad.check(["rev-parse", "HEAD"])
    except subprocess.CalledProcessError as exc:
        assert exc.returncode == 3
        assert exc.cmd == ["git", "rev-parse", "HEAD"], exc.cmd
        assert exc.stdout == b"stdout-bytes" and exc.stderr == b"stderr-bytes", (exc.stdout, exc.stderr)
        return
    raise AssertionError("check must raise on a nonzero exit")


def case_stdin_reaches_git():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake):
        gr.run(["cat-file", "--batch-check"], input=b"HEAD\n", timeout=60)
    assert seen["input"] == b"HEAD\n" and seen["timeout"] == 60


READS = (["rev-parse", "HEAD"], ["--no-optional-locks", "status", "--porcelain"], ["-C", "x", "log", "-1"],
         ["-c", "core.quotepath=off", "diff", "--name-only"], ["--git-dir=.git", "cat-file", "-t", "HEAD"],
         ["hash-object", "--stdin"], ["ls-files"], ["for-each-ref"], ["merge-base", "a", "b"], ["rev-list", "HEAD"],
         ["show", "HEAD"], ["worktree", "list", "--porcelain"], ["remote"], ["remote", "get-url", "origin"],
         ["blame", "f"])
NOT_READS = (["commit", "-m", "x"], ["worktree", "add", "w"], ["worktree"], ["remote", "add", "o", "u"], ["push"],
             ["st"], ["--exec-path=x", "log"], ["-C", "x"], ["checkout", "--", "f"], [])


def case_a_read_through_a_launcher_spawns_the_binary_it_runs_with_its_environment():
    """`cmd/git.exe` respawns `mingw64/bin/git.exe` after prepending its `mingw64/bin`, `usr/bin` and `$HOME/bin` to
    PATH and setting MSYSTEM; a read skips that spawn and hands git the same environment. Anything else keeps the
    launcher, and a PATH whose git is no launcher, or a non-Windows host, changes nothing."""
    root = tempfile.mkdtemp(prefix="gitrun_install_")
    _plant_install(root)
    direct = os.path.join(root, "mingw64", "bin", "git.exe")
    home = os.path.join(root, "home")
    fake, seen = _fake()
    try:
        with mock.patch.object(subprocess, "run", fake), mock.patch.object(gr, "_WINDOWS", True), \
                mock.patch.dict(os.environ, {"PATH": os.path.join(root, "cmd"), "HOME": home, "KEEP": "1"}):
            for args in READS:
                gr.run(args, env={"GIT_DIR": None, "OVERLAY": "1"})
                assert seen["argv"] == ["git"] + args and seen["executable"] == direct, (args, seen)
                env = seen["env"]
                head = env["PATH"].split(os.pathsep)[:4]
                assert head == [os.path.join(root, "mingw64", "bin"), os.path.join(root, "usr", "bin"),
                                os.path.join(home, "bin"), os.path.join(root, "cmd")], (args, head)
                assert env["MSYSTEM"] == "MINGW64" and env["KEEP"] == "1" and env["OVERLAY"] == "1", args
                assert "GIT_DIR" not in env, args
            for args in NOT_READS:
                gr.run(args)
                assert seen["executable"] is None and seen["env"] is None, (args, seen)
            gr.run(["rev-parse"], prefix=("-C", "x"), optional_locks=False)
            assert seen["executable"] == direct, seen
        with mock.patch.object(subprocess, "run", fake), mock.patch.object(gr, "_WINDOWS", True), \
                mock.patch.dict(os.environ, {"PATH": os.path.join(root, "mingw64", "bin")}):
            gr.run(["log"])
            assert seen["executable"] is None and seen["env"] is None, ("direct binary already first", seen)
        with mock.patch.object(subprocess, "run", fake), mock.patch.object(gr, "_WINDOWS", False), \
                mock.patch.dict(os.environ, {"PATH": os.path.join(root, "cmd")}):
            gr.run(["log"])
            assert seen["executable"] is None and seen["env"] is None, ("not Windows", seen)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _git_for_windows_launcher():
    for root in (r"C:\Program Files\Git", r"C:\Program Files (x86)\Git"):
        if all(os.path.isfile(os.path.join(root, *p)) for p in (("cmd", "git.exe"), ("mingw64", "bin", "git.exe"))):
            return root
    return None


def case_the_environment_matches_the_live_launchers_for_every_spawn_git_makes():
    """Live: on a PATH without Git's own folders (the land runner's), what git hands a program it spawns (here an
    alias's shell, standing in for a filter, textconv or hook) is the same through the launcher and through the
    direct binary with `_launcher_env`. Not Windows or no Git for Windows install: nothing to compare."""
    root = _git_for_windows_launcher() if os.name == "nt" else None
    if root is None:
        print("     (no Git for Windows launcher on this host: the live comparison has no subject)")
        return
    own = {os.path.normcase(os.path.join(root, *p)) for p in (("usr", "bin"), ("usr", "local", "bin"),
                                                              ("mingw64", "bin"), ("bin",))}
    own.add(os.path.normcase(os.path.join(os.path.expanduser("~"), "bin")))
    base = {k: v for k, v in os.environ.items() if k not in ("MSYSTEM", "PLINK_PROTOCOL", "EXEPATH", "SHLVL")}
    base["PATH"] = os.pathsep.join(e for e in base.get("PATH", "").split(os.pathsep)
                                   if e and os.path.normcase(os.path.normpath(e)) not in own)
    alias = ["-c", "alias.e=!env", "e"]

    def child_env(exe, env):
        out = subprocess.run([exe] + alias, capture_output=True, env=env, timeout=60).stdout.decode("utf-8", "replace")
        return {k: v for k, v in (line.split("=", 1) for line in out.splitlines() if "=" in line) if k != "_"}

    for label, mod in (("as given", {}), ("HOME unset", {"HOME": None}), ("MSYSTEM preset", {"MSYSTEM": "MSYS"})):
        env = {k: v for k, v in dict(base, **mod).items() if v is not None}
        via_launcher = child_env(os.path.join(root, "cmd", "git.exe"), env)
        direct = child_env(os.path.join(root, "mingw64", "bin", "git.exe"), gr._launcher_env(env, root))
        differs = sorted(k for k in set(via_launcher) | set(direct) if via_launcher.get(k) != direct.get(k))
        assert via_launcher and not differs, (label, {k: (via_launcher.get(k), direct.get(k)) for k in differs})


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("case_"):
            try:
                fn()
                print("ok   %s" % name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%s" % ("all cases pass" if not failed else "%d failed" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
