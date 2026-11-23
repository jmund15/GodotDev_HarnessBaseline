"""The v1 proofs carried with their module: `tools/reap.py` (the stop tool `kill_guard` admits), carried unchanged
until T1's small stop tool replaces it; its v1 proofs run here whole, one row per proof file, each a script that
exits 0 only when every case passes. The deletion rule's carried proofs became `test_floor_retire.py`'s rows.
"""
import os
import subprocess
import sys

import pytest

from conftest import CLAUDE

CARRIED = ["test_reap.py", "test_reap_native_env.py"]


@pytest.mark.parametrize("proof", CARRIED)
def test_carried_v1_proof_passes(proof, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BASH_ENV")}
    env.update(TMP=str(tmp_path), TEMP=str(tmp_path), TMPDIR=str(tmp_path))
    done = subprocess.run([sys.executable, str(CLAUDE / "tests" / proof)], cwd=str(CLAUDE.parent), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert done.returncode == 0, (done.stdout[-4000:], done.stderr[-4000:])
