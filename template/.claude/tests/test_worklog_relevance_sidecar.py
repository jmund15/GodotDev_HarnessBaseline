"""The dispatch preflight runs `<launcher> --check` on every `*_sidecar.sh` launch
(hooks/sidecar_dispatch_context.py), so every such script answers `--check`.

The worklog wrapper forwards it, with its model, to the launcher it hands off to.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / ".claude/hooks"))
sys.path.insert(0, str(HERE))
from _walk_files import walk_files  # noqa: E402
import sidecar_dispatch_context  # noqa: E402  the preflight's own bash, so the proof runs the shell it runs
SCRIPTS = REPO / ".claude/scripts"
STUB = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$(dirname "$0")/seen.txt"\necho OK\n'


def handles_check(path, seen=()):
    text = path.read_text(encoding="utf-8")
    if "--check" in text:
        return True
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith((". ", "source ")) and "_sidecar.sh" in stripped:
            name = stripped.rsplit("/", 1)[-1].strip('"')
            target = SCRIPTS / name
            if target.is_file() and target not in seen and handles_check(target, seen + (path,)):
                return True
    return False


def test_every_sidecar_script_answers_check():
    tracked = [f for f in walk_files(REPO, ".claude/scripts", ("_sidecar.sh",)) if f.count("/") == 2]
    assert tracked, "no *_sidecar.sh found: the sweep would pass vacuously"
    missing = [name for name in tracked if not handles_check(REPO / name)]
    assert not missing, "the preflight's --check probe would hit bad usage in: %s" % missing


def run_wrapper(*args):
    tmp = Path(tempfile.mkdtemp(prefix="wl_wrapper_"))
    shutil.copy(SCRIPTS / "worklog_relevance_sidecar.sh", tmp / "worklog_relevance_sidecar.sh")
    (tmp / "deepseek_sidecar.sh").write_text(STUB, encoding="utf-8", newline="\n")
    done = subprocess.run([sidecar_dispatch_context._msys.bash() or "bash", str(tmp / "worklog_relevance_sidecar.sh"), *args],
                          capture_output=True, text=True, timeout=30)
    seen = tmp / "seen.txt"
    return done.returncode, seen.read_text(encoding="utf-8").strip() if seen.is_file() else None, done.stderr


def test_check_forwards_to_the_launcher_with_the_wrapper_model():
    default = re.search(r'^MODEL="([^"]+)"', (SCRIPTS / "worklog_relevance_sidecar.sh").read_text(encoding="utf-8"), re.M)
    code, seen, _ = run_wrapper("--check")
    assert default and (code, seen) == (0, "--check -m " + default.group(1)), (code, seen)


def test_check_forwards_the_callers_flags():
    code, seen, _ = run_wrapper("--check", "-m", "synthetic-alias", "-A")
    assert code == 0 and seen.startswith("--check") and seen.endswith("-m synthetic-alias -A"), (code, seen)


def test_effort_none_refuses_before_forwarding():
    code, seen, error = run_wrapper("-e", "none", "--", "scope")
    assert code == 2 and seen is None and "effort none refused" in error, (code, seen, error)


def test_absent_notes_vault_refuses_before_dispatch():
    home = tempfile.mkdtemp(prefix="wl_home_")
    done = subprocess.run([sidecar_dispatch_context._msys.bash() or "bash", str(SCRIPTS / "worklog_relevance_sidecar.sh"), "--", "scope"],
                          capture_output=True, text=True, timeout=30, env=dict(os.environ, HOME=home))
    assert done.returncode == 2 and "vault dir missing" in done.stderr, (done.returncode, done.stderr)


def test_a_bad_option_is_still_bad_usage():
    code, seen, _ = run_wrapper("-Z")
    assert code == 2 and seen is None, (code, seen)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failures += 1
                print("FAIL", name, "-", type(exc).__name__, str(exc)[:300])
    sys.exit(1 if failures else 0)
