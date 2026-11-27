"""Git for Windows' root and Git Bash, found once in `_msys` for every caller: the install is the one the `git` PATH
holds first names (the `cmd` launcher, or the binary in `mingw64/bin` that a Git Bash session's and a proof's PATH
hold), else the first default install present. Every tool, every bash and a proof's PATH come from that root."""
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "hooks"))
sys.path.insert(0, str(CLAUDE / "scripts"))
sys.path.insert(0, str(CLAUDE / "tools"))
import _msys  # noqa: E402

windows = pytest.mark.skipif(os.name != "nt", reason="Git for Windows' layout")


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    monkeypatch.delenv("SIDECAR_BASH", raising=False)
    monkeypatch.setattr(_msys, "_GIT_ROOTS", (str(tmp_path / "absent"),))
    root = tmp_path / "Git"
    for rel in ("cmd/git.exe", "mingw64/bin/git.exe", "usr/bin/sh.exe", "usr/bin/ps.exe", "usr/bin/bash.exe",
                "bin/bash.exe"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"")
    return root


def path_holds(found):
    """A `shutil.which` whose PATH holds only the named files: {"git": <path>, "bash": <path>}."""
    return lambda name, *a, **k: str(found[name]) if name in found else None


@windows
@pytest.mark.parametrize("first", ["cmd/git.exe", "mingw64/bin/git.exe"])
def test_the_root_is_the_install_whichever_git_path_holds_first(install, first):
    with mock.patch.object(_msys.shutil, "which", path_holds({"git": install / first})):
        assert Path(_msys.git_root()) == install
        assert Path(_msys.tool("sh")) == install / "usr" / "bin" / "sh.exe"


@windows
def test_the_root_is_read_from_the_path_a_caller_passes(install):
    assert Path(_msys.git_root(str(install / "cmd"))) == install


@windows
def test_no_git_on_path_falls_back_to_the_first_default_install_present(install, tmp_path):
    with mock.patch.object(_msys.shutil, "which", path_holds({})):
        assert _msys.git_root() is None
        with mock.patch.object(_msys, "_GIT_ROOTS", (str(tmp_path / "absent"), str(install))):
            assert Path(_msys.git_root()) == install


@windows
def test_bash_is_the_installs_wrapper_then_its_raw_binary(install):
    with mock.patch.object(_msys.shutil, "which", path_holds({"git": install / "mingw64" / "bin" / "git.exe"})):
        assert Path(_msys.bash()) == install / "bin" / "bash.exe"
        (install / "bin" / "bash.exe").unlink()
        assert Path(_msys.bash()) == install / "usr" / "bin" / "bash.exe"


@windows
def test_the_bash_claude_code_is_told_comes_first(install, tmp_path, monkeypatch):
    told = tmp_path / "elsewhere" / "bash.exe"
    told.parent.mkdir()
    told.write_bytes(b"")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(told))
    with mock.patch.object(_msys.shutil, "which", path_holds({"git": install / "cmd" / "git.exe"})):
        assert Path(_msys.bash()) == told


@windows
def test_with_no_install_a_path_bash_serves_but_never_the_wsl_shim(install):
    with mock.patch.object(_msys.shutil, "which", path_holds({"bash": r"D:\tools\msys\bash.exe"})):
        assert _msys.bash() == r"D:\tools\msys\bash.exe"
    with mock.patch.object(_msys.shutil, "which", path_holds({"bash": r"C:\Windows\System32\bash.exe"})):
        assert _msys.bash() is None


def test_another_platform_trusts_path_bash_else_the_bare_name():
    with mock.patch.object(_msys.os, "name", "posix"):
        with mock.patch.object(_msys.shutil, "which", path_holds({"bash": "/usr/bin/bash"})):
            assert _msys.bash() == "/usr/bin/bash"
        with mock.patch.object(_msys.shutil, "which", path_holds({})):
            assert _msys.bash() == "bash"


def _proof_runner_bash():
    from _proof_runner import attempt
    return attempt._runner_cmd("probe.sh")[0]


def _sidecar_bash():
    import sidecar_launch
    return sidecar_launch.git_bash()


def _reap_bash():
    import reap
    return reap.git_bash()


@windows
@pytest.mark.parametrize("caller", [_proof_runner_bash, _sidecar_bash, _reap_bash])
def test_every_caller_runs_the_bash_of_the_install_path_names(install, caller):
    with mock.patch.object(_msys.shutil, "which", path_holds({"git": install / "mingw64" / "bin" / "git.exe"})):
        assert Path(caller()) == install / "bin" / "bash.exe"
