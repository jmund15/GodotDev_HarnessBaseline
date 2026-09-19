#!/usr/bin/env python3
"""Rewrite tracked files whose WORKING COPY is CRLF back to LF, under one directory (default .claude).

  python3 .claude/tools/lf_normalize.py                 # report + fix clean files under .claude
  python3 .claude/tools/lf_normalize.py --check         # report only; exit 1 if any CRLF working copy
  python3 .claude/tools/lf_normalize.py --include-dirty # also rewrite files with uncommitted changes
  python3 .claude/tools/lf_normalize.py <dir>           # another tracked directory
  python3 .claude/tools/lf_normalize.py --help          # this text; any other unknown option exits 2

The repo is LF by attribute (`.gitattributes`: `* text=auto eol=lf`) and the index holds LF; only
working copies drift, and only because a Windows text-mode writer (Python `write_text()` without
`newline="\\n"`, PowerShell `Set-Content`/`Out-File`) rewrote them. Only files whose INDEX blob is LF
are touched — a blob git stores as CRLF or treats as binary is not drift. Dirty files are skipped by
default: a peer session may hold them open, and rewriting bytes under an `Edit` trips its
modified-since-read check. `git ls-files --eol` is the detector; `git status` is the dirty filter.
"""
import subprocess
import sys


def run(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def crlf_working_copies(root):
    out = []
    for ln in run("ls-files", "--eol", "--", root).splitlines():
        # "i/lf    w/crlf  attr/text=auto eol=lf <TAB>path"
        attrs, _, path = ln.partition("\t")
        fields = attrs.split()
        if len(fields) >= 2 and fields[0] == "i/lf" and fields[1] == "w/crlf":
            out.append(path)
    return out


KNOWN_FLAGS = ("--check", "--include-dirty")


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__.strip())
        return 0
    unknown = [a for a in args if a.startswith("-") and a not in KNOWN_FLAGS]
    operands = [a for a in args if not a.startswith("-")]
    if unknown or len(operands) > 1:
        bad = ", ".join(unknown) if unknown else "extra directory operand " + " ".join(operands[1:])
        print("lf_normalize: unrecognized %s; nothing was rewritten.\n\n%s" % (bad, __doc__.strip()), file=sys.stderr)
        return 2
    check = "--check" in args
    include_dirty = "--include-dirty" in args
    root = operands[0] if operands else ".claude"
    crlf = crlf_working_copies(root)
    if not crlf:
        print("lf_normalize: 0 CRLF working copies under %s" % root)
        return 0
    dirty = {ln[3:].strip().strip('"') for ln in run("status", "--porcelain", "--", *crlf).splitlines() if ln.strip()}
    todo = crlf if include_dirty else [p for p in crlf if p not in dirty]
    skipped = [p for p in crlf if p not in todo]
    if check:
        print("lf_normalize: %d CRLF working copies under %s (%d dirty)" % (len(crlf), root, len(dirty)))
        for p in crlf:
            print("  " + p + ("  (dirty)" if p in dirty else ""))
        return 1
    for p in todo:
        with open(p, "rb") as fh:
            data = fh.read()
        with open(p, "wb") as fh:
            fh.write(data.replace(b"\r\n", b"\n"))
    print("lf_normalize: rewrote %d file(s) to LF under %s; skipped %d dirty (pass --include-dirty)"
          % (len(todo), root, len(skipped)))
    for p in skipped:
        print("  skipped (dirty): " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
