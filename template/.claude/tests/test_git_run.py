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


def case_argv_prefix_order_and_optional_locks_flag():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake):
        gr.run(["status"], prefix=("-C", "x", "-c", "a=b"), optional_locks=False)
        assert seen["argv"] == ["git", "--no-optional-locks", "-C", "x", "-c", "a=b", "status"], seen["argv"]
        gr.run(["log"])
        assert seen["argv"] == ["git", "log"], seen["argv"]


def case_env_overlay_unsets_a_none_value():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake), mock.patch.dict(os.environ, {"GIT_DIR": "/x", "KEEP": "1"}):
        gr.run(["log"], env={"GIT_DIR": None, "GIT_INDEX_FILE": "idx"})
        env = seen["env"]
        assert "GIT_DIR" not in env and env["GIT_INDEX_FILE"] == "idx" and env["KEEP"] == "1"


def case_empty_overlay_inherits_the_environment():
    fake, seen = _fake()
    with mock.patch.object(subprocess, "run", fake):
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
