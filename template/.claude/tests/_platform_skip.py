"""One idiom for a test module that cannot run on this platform: pytest reports it skipped, never passed.

A module-level `sys.exit` would end every xdist worker that collects the module, so the skip goes through pytest.
"""
import pytest


def skip_proof(name, reason):
    pytest.skip(name + ": " + reason, allow_module_level=True)
