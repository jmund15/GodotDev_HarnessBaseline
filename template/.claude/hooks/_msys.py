"""Git's MSYS tools by absolute path.

A pid a Git Bash script records is an MSYS pid, which only Git's own `ps`, `bash` and `/proc` can see. A
Windows PATH can carry Git's `cmd` folder without its `usr\\bin` (the land runner's does), so a bare `ps`
is then missing and every probe of such a pid reads unknown. Callers resolve the tool here instead. Git's
usr\\bin\\bash.exe does not add /usr/bin to PATH, so a `bash -c` command names its tools by absolute path.
"""
import os
import shutil
import subprocess

_GIT_ROOTS = (r"C:\Program Files\Git", r"C:\Program Files (x86)\Git")


def _git_roots():
    git = shutil.which("git")
    if git:
        yield os.path.dirname(os.path.dirname(git))  # <Git>\cmd\git.exe
    yield from _GIT_ROOTS


def tool(name):
    """On Windows, `name` from Git's usr\\bin first, since a PATH hit can be another tool of the same name (System32's
    bash.exe is the WSL shim); else from PATH; else the bare name (the call then fails as it always did, and the
    caller's unknown-answer path handles it)."""
    if os.name != "nt":
        return shutil.which(name) or name
    for root in _git_roots():
        cand = os.path.join(root, "usr", "bin", name + ".exe")
        if os.path.isfile(cand):
            return cand
    return shutil.which(name) or name


def ps_pid_alive(pid, timeout=10):
    """True or False for an MSYS pid through Git's `ps -p`, None when ps answered nothing (it complained, or printed
    no header line). Raises OSError or SubprocessError when ps cannot run, so a caller picks its own fallback."""
    r = subprocess.run([tool("ps"), "-p", str(pid)], capture_output=True, text=True, timeout=timeout)
    lines = r.stdout.splitlines()
    if (r.returncode != 0 and r.stderr.strip()) or not lines:
        return None
    return any(ln.split()[:1] == [str(pid)] for ln in lines[1:])


_CMD_META = set('"&|<>^%!\r\n')


def resolve_argv(argv):
    """(argv, error) for a command a caller or config names. CreateProcess applies no PATHEXT, so on Windows a bare
    `npx` never finds `npx.cmd`: a separator-free name resolves through PATH, and a name PATH lacks is an error line
    naming it. A .cmd/.bat target runs under cmd.exe, which re-parses its arguments, so an argument carrying a cmd
    metacharacter is an error, never a quoted pass-through (`x"&echo y` would run `echo y`)."""
    name = argv[0]
    if os.name != "nt" or os.sep in name or "/" in name:
        return argv, None
    found = shutil.which(name)
    if not found:
        return argv, "command not found on PATH: " + name
    if found.lower().endswith((".cmd", ".bat")):
        bad = [a for a in argv[1:] if _CMD_META & set(a)]
        if bad:
            return argv, "refused: %s runs under cmd.exe, which would re-parse the argument %r" % (name, bad[0])
    return [found, *argv[1:]], None
