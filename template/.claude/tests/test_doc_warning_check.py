"""Proof for doc_warning_check.sh: a stub `dotnet` (a function loaded through BASH_ENV, since Git Bash orders its own PATH)
plays a clean build, a build with XML-documentation defects, and a failed build. The project file name is read from the
script's own PROJ line, so the proof names no project. No real build runs."""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, run_bash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "doc_warning_check.sh"

DOTNET = r'''dotnet() {
  case "$STUB_MODE" in
    clean) echo "Build succeeded."; return 0 ;;
    defect) echo 'C:\planted\Thing.cs(3,5): warning CS1574: planted cref'; echo "Build succeeded."; return 0 ;;
    fail) printf '%s\n' 'C:\planted\Thing.cs(4,1): error CS0103: planted' '    1 Error(s)'; return 1 ;;
  esac
}
'''


def contract(temp_root):
    temp_root = Path(temp_root)
    tree = temp_root / "tree"
    script = tree / ".claude" / "scripts" / "doc_warning_check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(SCRIPT.read_bytes())
    # The launcher's project path, read from the script itself.
    proj = re.search(r'^PROJ="\$REPO/([^"]+)"', SCRIPT.read_text(encoding="utf-8"), re.M).group(1)
    (tree / proj).write_text("<Project />\n", encoding="utf-8", newline="\n")
    stub = temp_root / "dotnet_stub.sh"
    stub.write_text(DOTNET, encoding="utf-8", newline="\n")
    env = launch_env(temp_root, BASH_ENV=str(stub))
    env["BASH_ENV"] = stub.as_posix()

    clean = run_bash(script, tree, dict(env, STUB_MODE="clean"))
    assert clean.returncode == 0 and "0 doc defects" in clean.stdout, (clean.returncode, clean.stdout[-300:])

    defect = run_bash(script, tree, dict(env, STUB_MODE="defect"))
    assert defect.returncode == 1 and "1 XML-documentation defects" in defect.stdout, (defect.returncode, defect.stdout[-300:])

    failed = run_bash(script, tree, dict(env, STUB_MODE="fail"))
    assert failed.returncode == 2 and "BUILD FAILED" in failed.stderr, (failed.returncode, failed.stderr[-300:])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="doc_warning_check_proof_") as tmp:
        contract(tmp)
    print("ok doc_warning_check.sh")
