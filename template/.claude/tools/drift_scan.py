#!/usr/bin/env python3
"""Lists the files that still name a string literal a change removed.

The literals a diff removes from files matching `drift_scan.sources`, minus those the same file's added lines
still hold, are searched for in every file matching `drift_scan.consumers`, where a line hits only when the literal is a
whole quoted string there or the whole body of a regex literal (`/Task/i`, `/^Task$/`); `Tasks` or `Task list`
in quotes do not hit `Task`. Each hit prints as
`drift: "<literal>" left <source file>; still in <consumer>:<line>`; the run exits 1 when any hit exists.
It runs no test and starts nothing.

Config, key `drift_scan` of the project adaptation file (read through `tools/adaptation.py`):
  sources     list of globs ('**' crosses folders) whose removed literals count
  consumers   list of globs searched for those literals
  min_length  shortest literal kept (default 1)
  ignore      list of regexes; a literal matching one is dropped (re.search)
A literal is text in single quotes, double quotes or backticks, or JSX text. No `drift_scan` key: prints
`drift_scan: not configured` and exits 0.

Input: `git diff <merge-base with main>` (committed plus uncommitted); `--base REF` replaces main,
`--diff-file FILE` replaces git, `--paths P...` keeps only those source files.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adaptation  # noqa: E402

ACCEPTS = "drift_scan.py [--repo DIR] [--base REF | --diff-file FILE] [--paths PATH ...]"
EXAMPLE = "python3 .claude/tools/drift_scan.py --base main --paths src/Board.tsx"

_QUOTED = re.compile(r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`""")
_REGEX = re.compile(r"/((?:[^/\\\n*]|\\.)(?:[^/\\\n]|\\.)*)/[a-z]*")
_JSX_TEXT = re.compile(r"(?<![=\-])>([^<>{}]+)(?=<|$)")
_CODE_CHARS = re.compile(r"[<>{}();=\[\]]|^\s*(//|/\*|\*|import |export |const |let |return )")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print("drift_scan: %s\naccepts: %s\nexample: %s" % (message, ACCEPTS, EXAMPLE), file=sys.stderr)
        sys.exit(2)


def glob_re(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile("^" + out + "$")


def matches(path, globs):
    return any(glob_re(g).match(path) for g in globs)


def literals(line):
    found = []
    for m in _QUOTED.finditer(line):
        found.append(next(g for g in m.groups() if g is not None))
    for m in _JSX_TEXT.finditer(line):
        found.append(m.group(1))
    if not found and line.strip() and not _CODE_CHARS.search(line):
        found.append(line)
    return [s.strip() for s in found if s.strip()]


def whole_texts(line):
    """The trimmed texts a consumer line names whole: each quoted string and each regex literal's body."""
    found = {next(g for g in m.groups() if g is not None).strip() for m in _QUOTED.finditer(line)}
    for m in _REGEX.finditer(line):
        body = m.group(1)
        body = body[1:] if body.startswith("^") else body
        body = body[:-1] if body.endswith("$") and not body.endswith("\\$") else body
        found.add(re.sub(r"\\(.)", r"\1", body).strip())
    return found


def parse_diff(text):
    """{source path: (removed lines, added lines)} from a unified diff."""
    files, old, new = {}, None, None
    for line in text.splitlines():
        if line.startswith("--- "):
            old = line[4:].strip()
            old = old[2:] if old.startswith("a/") else None
        elif line.startswith("+++ "):
            new = line[4:].strip()
            new = new[2:] if new.startswith("b/") else None
            path = new or old
            if path:
                files.setdefault(path, ([], []))
        elif line.startswith("-") and not line.startswith("---") or line.startswith("+") and not line.startswith("+++"):
            path = new or old
            if path:
                files[path][0 if line[0] == "-" else 1].append(line[1:])
    return files


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def read_diff(repo, base):
    code, mb = git(repo, "merge-base", "HEAD", base)
    if code != 0:
        sys.exit("drift_scan: no merge-base between HEAD and %s" % base)
    _, text = git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", mb.strip())
    return text


def dropped(files, cfg, paths):
    """[(literal, source path)] removed from a source file and not held by its added lines."""
    minlen = cfg.get("min_length", 1)
    ignore = [re.compile(p) for p in cfg.get("ignore", [])]
    out = []
    for path, (removed, added) in files.items():
        if not matches(path, cfg["sources"]) or (paths and not any(path == p or matches(path, [p]) for p in paths)):
            continue
        kept = {s for line in added for s in literals(line)}
        seen = set()
        for line in removed:
            for s in literals(line):
                if s in kept or s in seen or len(s) < minlen or any(p.search(s) for p in ignore):
                    continue
                seen.add(s)
                out.append((s, path))
    return out


def consumer_files(repo, cfg):
    _, listing = git(repo, "ls-files")
    return [p for p in listing.splitlines() if matches(p, cfg["consumers"])]


def main(argv=None):
    ap = _Parser(prog="drift_scan.py", add_help=False)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--base", default="main")
    ap.add_argument("--diff-file", default=None)
    ap.add_argument("--paths", nargs="+", default=[])
    args = ap.parse_args(argv)
    repo = args.repo or git(os.getcwd(), "rev-parse", "--show-toplevel")[1].strip()
    cfg = adaptation.get(os.path.join(repo, ".claude"), "drift_scan")
    if not cfg:
        print("drift_scan: not configured")
        return 0
    if not (isinstance(cfg.get("sources"), list) and isinstance(cfg.get("consumers"), list)):
        print("drift_scan: drift_scan needs `sources` and `consumers` lists\naccepts: %s\nexample: %s" % (ACCEPTS, EXAMPLE),
              file=sys.stderr)
        return 2
    if args.diff_file:
        with open(args.diff_file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = read_diff(repo, args.base)
    drops = dropped(parse_diff(text), cfg, args.paths)
    lines, hit = [], set()
    consumers = consumer_files(repo, cfg)
    for path in consumers:
        try:
            with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
                body = fh.read().splitlines()
        except OSError:
            continue
        for lit, src in drops:
            for n, line in enumerate(body, 1):
                if lit in whole_texts(line):
                    hit.add((lit, src))
                    lines.append((lit, src, path, n))
    order = {d: i for i, d in enumerate(drops)}
    for lit, src, path, n in sorted(lines, key=lambda r: (order[(r[0], r[1])], r[2], r[3])):
        print('drift: "%s" left %s; still in %s:%d' % (lit, src, path, n))
    print("drift_scan: %d literal(s) removed from sources, %d still named in %d consumer line(s), %d consumer file(s) scanned"
          % (len(drops), len(hit), len(lines), len(consumers)))
    return 1 if lines else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
