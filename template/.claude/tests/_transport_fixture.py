"""Pin the transport a guard proof runs under.

Every hook that reads `_session_transport` resolves from the ENVIRONMENT. A proof that inherits the
developer's environment therefore asserts against whatever seat they happen to be on: the same file
passes on an Anthropic seat and fails on a codex one, where `sonnet` is denied by the VOCABULARY
rule before the rule under test is ever reached. Measured 2026-09-08 — a codex session running
`test_workflow_provider_guard_effort.py` failed `sonnet·medium ... != deny`, which is the guard
behaving correctly and the proof asserting a host assumption it never declared.

Not named `test_*.py` / `*_test.py` on purpose: `harness_tests.py` discovers by those patterns, and
a helper that runs as a proof reports a pass for having no cases.

    from _transport_fixture import hook_env
    subprocess.run([sys.executable, HOOK], env=hook_env(), ...)          # anthropic seat
    subprocess.run([sys.executable, HOOK], env=hook_env("codex"), ...)   # provider seat
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def hook_env(transport="anthropic", **extra):
    """A child environment whose seat is `transport`, whatever seat the caller runs on.

    Clears BOTH signals first. `resolve()` prefers `CLAUDE_CODE_TRANSPORT` and falls back to
    matching `ANTHROPIC_BASE_URL`'s host, so leaving the URL behind lets the developer's proxy
    decide the seat on any run where the explicit name is absent.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    env.update(extra)
    return env
