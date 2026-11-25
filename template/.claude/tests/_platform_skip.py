"""One idiom for a proof's platform skip, for both runners: pytest and `scripts/harness_tests.py`.

skip(name, reason)       one case cannot run on this platform; the proof keeps running its other cases, so its
                         verdict stays theirs.
skip_proof(name, reason) the whole proof cannot run here. Run as a script by `harness_tests.py`, it exits 0 with the
                         marker the runner reports as SKIPPED-PLATFORM; imported by pytest, it skips the module through
                         pytest, since a module-level `sys.exit` would end every xdist worker that collects it.
"""
import sys

CASE_SKIP = "PLATFORM-SKIP-CASE "
PROOF_SKIP = "PLATFORM-SKIP-PROOF "


def skip(name, reason):
    print(CASE_SKIP + name + ": " + reason, flush=True)


def skip_proof(name, reason):
    if sys._getframe(1).f_globals.get("__name__") == "__main__":
        print(PROOF_SKIP + name + ": " + reason, flush=True)
        sys.exit(0)
    import pytest
    pytest.skip(name + ": " + reason, allow_module_level=True)
