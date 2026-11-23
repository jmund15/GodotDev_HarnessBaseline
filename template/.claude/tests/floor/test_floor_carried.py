"""The v1 proofs carried with their modules: `tools/reap.py` (the stop tool `kill_guard` admits) and
`hooks/_regenerable_clone.py` (the worktree and clone deletion rule `dangerous_shell_guard` calls).

Both modules carry unchanged from v1 (PushinPotions' generation, plus MaddManager's loose-scratch-file deny) until
T1's one-rule deletion redesign and the small stop tool replace them; their v1 proofs run here whole, one row per
proof file, each a script that exits 0 only when every case passes. Table rows replace them with the redesign.
"""
import os
import subprocess
import sys

import pytest

from conftest import CLAUDE

CARRIED = ["test_reap.py", "test_reap_native_env.py", "test_regenerable_clone.py", "test_regenerable_clone_part2.py",
           "test_regenerable_clone_part3.py"]


@pytest.mark.parametrize("proof", CARRIED)
def test_carried_v1_proof_passes(proof, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV")}
    env.update(TMP=str(tmp_path), TEMP=str(tmp_path), TMPDIR=str(tmp_path))
    done = subprocess.run([sys.executable, str(CLAUDE / "tests" / proof)], cwd=str(CLAUDE.parent), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert done.returncode == 0, (done.stdout[-4000:], done.stderr[-4000:])
