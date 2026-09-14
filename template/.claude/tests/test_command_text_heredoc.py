#!/usr/bin/env python3
"""`executable_text` must drop heredoc bodies and keep everything that executes.

Arms: bodies are removed (the false-positive class), surrounding commands survive (so a real
violation on a command line is still matchable), and a violation OUTSIDE a heredoc is never
dropped -- without that third arm a function returning "" would pass the first two.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))

from _command_text import executable_text  # noqa: E402

fails = 0


def check(name, got, want_in=(), want_not_in=()):
    global fails
    bad = [s for s in want_in if s not in got] + [s for s in want_not_in if s in got]
    if bad:
        print(f"  FAIL {name}: offending={bad!r}")
        print(f"       got: {got!r}")
        fails += 1
    else:
        print(f"  PASS {name}")


print("_command_text.executable_text — heredoc body scrubbing")

# 1. The real shape that was denied: a script written via heredoc that MENTIONS a gate.
cmd1 = "cat > run.ps1 <<'EOF'\n# gate: final\n& pwsh regression_gate.ps1\nEOF\necho written"
check("heredoc body dropped", executable_text(cmd1),
      want_in=["cat > run.ps1", "echo written", "EOF"],
      want_not_in=["# gate: final", "regression_gate.ps1"])

# 2. Quoted and unquoted delimiters, and the <<- variant.
for delim in ("<<EOF", "<<'EOF'", '<<"EOF"', "<<-EOF"):
    c = f"cat > f {delim}\nsecret_token_xyz\nEOF\nls"
    check(f"delimiter {delim}", executable_text(c),
          want_in=["cat > f", "ls"], want_not_in=["secret_token_xyz"])

# 3. NEGATIVE CONTROL: a violation on a real command line must survive.
cmd3 = "cat > f <<'EOF'\nharmless\nEOF\npwsh regression_gate.ps1"
check("violation outside heredoc survives", executable_text(cmd3),
      want_in=["regression_gate.ps1"], want_not_in=["harmless"])

# 4. No heredoc at all -> unchanged.
cmd4 = "pwsh regression_gate.ps1 -StaticOnly"
check("no heredoc is a passthrough", executable_text(cmd4), want_in=[cmd4])

# 5. Two heredocs in one command.
cmd5 = "cat > a <<'A'\nbody_a\nA\ncat > b <<'B'\nbody_b\nB\ndone_marker"
check("two heredocs", executable_text(cmd5),
      want_in=["cat > a", "cat > b", "done_marker"], want_not_in=["body_a", "body_b"])

# 6. Unterminated heredoc: body still must not be judged as executing.
cmd6 = "cat > f <<'EOF'\npwsh regression_gate.ps1\n"
check("unterminated heredoc drops its body", executable_text(cmd6),
      want_in=["cat > f"], want_not_in=["regression_gate.ps1"])

# 7. FALSE-STRIP GUARD. A quoted MENTION of a heredoc opener is not an opener. Stripping here
# would hide the real delete from every matcher — a false allow, worse than a false block.
cmd7 = "echo '<<EOF'\nrm -r build/"
check("quoted <<EOF mention does not strip what follows", executable_text(cmd7),
      want_in=["rm -r build/"])

# 8. A here-STRING has no body; `<<<` must not be read as `<<`.
cmd8 = "grep -q x <<< \"$payload\"\nrm -r build/"
check("here-string is not a heredoc", executable_text(cmd8),
      want_in=["rm -r build/"])

# 9. An opener with trailing text on the same line is not an opener either.
cmd9 = "echo \"see <<EOF for the format\" && rm -r build/"
check("trailing text after the delimiter is not an opener", executable_text(cmd9),
      want_in=["rm -r build/"])

print()
print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
