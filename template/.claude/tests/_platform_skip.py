"""One idiom for a proof's platform skip, read by `scripts/harness_tests.py`.

skip(name, reason)       one case cannot run on this platform; the proof keeps running its other cases,
                         so its verdict stays theirs.
skip_proof(name, reason) the whole proof cannot run here: it exits 0 and the runner reports it
                         SKIPPED-PLATFORM, never as passed and never as missing coverage.
"""
import sys

CASE_SKIP = "PLATFORM-SKIP-CASE "
PROOF_SKIP = "PLATFORM-SKIP-PROOF "


def skip(name, reason):
    print(CASE_SKIP + name + ": " + reason, flush=True)


def skip_proof(name, reason):
    print(PROOF_SKIP + name + ": " + reason, flush=True)
    sys.exit(0)
