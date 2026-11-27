"""Git's MSYS tools by absolute path.

A pid a Git Bash script records is an MSYS pid, which only Git's own `ps`, `bash` and `/proc` can see. A
Windows PATH can carry Git's `cmd` folder without its `usr\\bin` (a pwsh runner's does), so a bare `ps`
is then missing and every probe of such a pid reads unknown. Callers resolve the tool, Git Bash (`bash`) and
Git's install (`git_root`) here instead, so one search and one fallback list answer every caller. Git's
usr\\bin\\bash.exe does not add /usr/bin to PATH, so a `bash -c` command names its tools by absolute path.
"""
import os
import shutil
import subprocess

_GIT_ROOTS = (r"C:\Program Files\Git", r"C:\Program Files (x86)\Git")


def _is_install(folder):
    return os.path.isdir(os.path.join(folder, "usr", "bin"))


def git_root(path=None):
    """The Git for Windows install every caller uses: the one whose `git` the PATH `path` (default: this process's)
    finds first, else the first default install present, else None. The `cmd` launcher (a pwsh runner's PATH) and
    the binary in `mingw64\\bin` (Git Bash's and a proof's PATH) both name it: the folder holding `usr\\bin`."""
    git = shutil.which("git", path=path)
    folder = os.path.dirname(os.path.abspath(git)) if git else None
    for _ in range(2 if folder else 0):
        folder = os.path.dirname(folder)
        if _is_install(folder):
            return folder
    return next((root for root in _GIT_ROOTS if _is_install(root)), None)


def tool(name):
    """On Windows, `name` from Git's usr\\bin first, since a PATH hit can be another tool of the same name (System32's
    bash.exe is the WSL shim); else from PATH; else the bare name (the call then fails as it always did, and the
    caller's unknown-answer path handles it)."""
    if os.name != "nt":
        return shutil.which(name) or name
    root = git_root()
    cand = os.path.join(root, "usr", "bin", name + ".exe") if root else None
    if cand and os.path.isfile(cand):
        return cand
    return shutil.which(name) or name


def bash():
    """Git Bash, or None on Windows when none is found. A bare `bash` from Windows Python is System32's WSL shim,
    whose HOME and `/proc` belong to another filesystem, so a launcher run through it misreports logins and an MSYS
    pid reads unknown. Windows order: the path Claude Code is told (`CLAUDE_CODE_GIT_BASH_PATH`), the install's
    `bin\\bash.exe` wrapper, its raw `usr\\bin\\bash.exe`, then a PATH `bash` outside System32. Elsewhere PATH's
    `bash`, else the bare name."""
    if os.name != "nt":
        return shutil.which("bash") or "bash"
    told = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if told and os.path.isfile(told):
        return told
    root = git_root()
    for rel in (("bin", "bash.exe"), ("usr", "bin", "bash.exe")) if root else ():
        cand = os.path.join(root, *rel)
        if os.path.isfile(cand):
            return cand
    found = shutil.which("bash")
    return found if found and "system32" not in found.lower() else None


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
    """(argv, error) for a command a caller or config names. A separator-free name PATH lacks is an error line naming
    it on every platform, so a caller reports it instead of meeting a spawn error. CreateProcess applies no PATHEXT,
    so on Windows a bare `npx` never finds `npx.cmd`: the name is replaced by its PATH hit. A .cmd/.bat target runs
    under cmd.exe, which re-parses its arguments, so an argument carrying a cmd metacharacter is an error, never a
    quoted pass-through (`x"&echo y` would run `echo y`)."""
    name = argv[0]
    if os.sep in name or "/" in name:
        return argv, None
    found = shutil.which(name)
    if not found:
        return argv, "command not found on PATH: " + name
    if os.name != "nt":
        return argv, None
    if found.lower().endswith((".cmd", ".bat")):
        bad = [a for a in argv[1:] if _CMD_META & set(a)]
        if bad:
            return argv, "refused: %s runs under cmd.exe, which would re-parse the argument %r" % (name, bad[0])
    return [found, *argv[1:]], None
