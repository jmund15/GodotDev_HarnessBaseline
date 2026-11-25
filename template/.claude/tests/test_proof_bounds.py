"""The shipped time bounds: a `proof_timeouts` entry in the layer defaults names a proof this folder holds and raises
its bound above the 120 s default, and the bridge's `_timeout` answers it. A row for a proof that is gone, or one at or
under the default, is dead data that reads as a decision."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import adaptation  # noqa: E402

BOUNDS = adaptation.get(str(HERE.parent), "proof_timeouts")


@pytest.mark.parametrize("name", sorted(BOUNDS))
def test_a_shipped_bound_names_a_proof_and_raises_the_default(name, bridge):
    # A consumer's own adaptation.json adds rows for its own proofs; only the template's folder holds all of its names.
    if not (HERE / name).is_file():
        pytest.skip("not a proof of this folder")
    assert BOUNDS[name] > bridge.DEFAULT_TIMEOUT_S
    assert bridge._timeout(HERE / name) == BOUNDS[name]


def test_a_proof_without_a_row_gets_the_default_bound(bridge):
    assert bridge._timeout(HERE / "test_no_such_proof_has_a_row.py") == bridge.DEFAULT_TIMEOUT_S
