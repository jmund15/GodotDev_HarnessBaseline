#!/usr/bin/env python3
"""`_shell_lex.executable_text` must drop heredoc bodies and keep everything that executes.

Arms: bodies are removed (the false-positive class), surrounding commands survive (so a real
violation on a command line is still matchable), and a violation OUTSIDE a heredoc is never
dropped -- without that arm a function returning "" would pass the first. The module this file
first tested, `_command_text.py`, is now a shim that re-exports `_shell_lex`, where `executable_text` lives.

    python -m pytest .claude/tests/test_command_text_heredoc.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))

from _shell_lex import executable_text  # noqa: E402

# (id, command, text that must survive, text that must be gone)
CASES = [
    # The real shape that was denied: a script written via heredoc that MENTIONS a gate.
    ("a body that mentions a gate is dropped",
     "cat > run.ps1 <<'EOF'\n# gate: final\n& pwsh gate_chain.ps1\nEOF\necho written",
     ["cat > run.ps1", "echo written", "EOF"], ["# gate: final", "gate_chain.ps1"]),
    # Quoted and unquoted delimiters, and the <<- variant.
    *[("delimiter " + delim, f"cat > f {delim}\nsecret_token_xyz\nEOF\nls", ["cat > f", "ls"], ["secret_token_xyz"])
      for delim in ("<<EOF", "<<'EOF'", '<<"EOF"', "<<-EOF")],
    # NEGATIVE CONTROL: a violation on a real command line must survive.
    ("a violation outside the heredoc survives",
     "cat > f <<'EOF'\nharmless\nEOF\npwsh gate_chain.ps1", ["gate_chain.ps1"], ["harmless"]),
    ("no heredoc is a passthrough", "pwsh gate_chain.ps1 -StaticOnly", ["pwsh gate_chain.ps1 -StaticOnly"], []),
    ("two heredocs",
     "cat > a <<'A'\nbody_a\nA\ncat > b <<'B'\nbody_b\nB\ndone_marker",
     ["cat > a", "cat > b", "done_marker"], ["body_a", "body_b"]),
    # Unterminated heredoc: the body still must not be judged as executing.
    ("an unterminated heredoc drops its body", "cat > f <<'EOF'\npwsh gate_chain.ps1\n",
     ["cat > f"], ["gate_chain.ps1"]),
    # FALSE-STRIP GUARD. A quoted MENTION of a heredoc opener is not an opener. Stripping here
    # would hide the real delete from every matcher: a false allow, worse than a false block.
    ("a quoted <<EOF mention does not strip what follows", "echo '<<EOF'\nrm -r build/", ["rm -r build/"], []),
    # A here-STRING has no body; `<<<` must not be read as `<<`.
    ("a here-string is not a heredoc", 'grep -q x <<< "$payload"\nrm -r build/', ["rm -r build/"], []),
    # An opener with trailing text on the same line is not an opener either.
    ("trailing text after the delimiter is not an opener",
     'echo "see <<EOF for the format" && rm -r build/', ["rm -r build/"], []),
]


@pytest.mark.parametrize("command,kept,dropped", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_executable_text_keeps_what_executes_and_drops_heredoc_bodies(command, kept, dropped):
    got = executable_text(command)
    assert [s for s in kept if s not in got] == [] and [s for s in dropped if s in got] == [], got
