#!/usr/bin/env python3
"""Check the evidence a review's findings cite against the bytes they cite.

    quote_check.py <findings.md> --root <checkout>

The findings file is markdown. Each `##` section holds fenced ```json blocks, and each block is a list of
evidence items, one source per item:

  {"file": "<path>[:N[-M]]", "quote": "<text>"}      the quote is in that file. A relative path is taken from
                                                     --root. Whitespace runs collapse; everything else must match
                                                     exactly, with no `...` gaps. The line range is not checked.
  {"file": "<transcript>.jsonl", "quote": "<words>"}  an owner quote: the words are in one record the owner typed
                                                     (a user-role text record), after each line is JSON-decoded.
  {"command": "<git ...>", "output": "<text>"}       the text is in the stdout of a read-only git command run
                                                     under --root. Only commands on GIT_SPECS run: no shell
                                                     syntax, no flag outside the table. An empty output needs
                                                     empty stdout and each path the command names to match a
                                                     tracked path, so a zero count over a missing path is a miss.

Prints one `MISS` line per item that does not hold, then a total. Exit 0 when every item holds, 1 when any misses,
2 when the input cannot be read. A miss means read the source before dropping the finding; it does not decide it.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _git_run  # noqa: E402

COMMAND_TIMEOUT = 8
HEADING = re.compile(r"^## (.*)$", re.M)
FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.S)
LOCATION = re.compile(r"^(.*?):(\d+)(?:-(\d+))?$")
SHELL = re.compile(r"[|;&<>$`\n]")
BINARY_PROBE = 8000


class Spec(NamedTuple):
    """One allow-listed git subcommand: its boolean and value-taking options, and which positional words are paths.

    `paths`: "dashdash" none outside `--`; "all" every positional; "rest" every positional after the first (a
    tree-ish); "grep" every positional after the pattern, or all with `-e`; "none" no positional.
    """
    short: str = ""
    short_value: str = ""
    long: tuple = ()
    long_value: tuple = ()
    paths: str = "dashdash"


# Every permitted flag is listed; anything else, an abbreviated long option included, is refused. Write options
# (`--output`, `--ext-diff`, `--exec`, `-c`) are absent by construction.
GIT_SPECS = {
    "grep": Spec(short="nivwFEGlLchHqo", short_value="eABC", paths="grep",
                 long=("--line-number", "--ignore-case", "--word-regexp", "--invert-match", "--fixed-strings",
                       "--extended-regexp", "--basic-regexp", "--count", "--files-with-matches",
                       "--files-without-match", "--no-color", "--only-matching", "--quiet", "--full-name", "--cached"),
                 long_value=("--max-depth",)),
    "ls-files": Spec(short="cdmsio", paths="all",
                     long=("--cached", "--deleted", "--modified", "--others", "--stage", "--ignored",
                           "--exclude-standard", "--error-unmatch", "--full-name", "--directory",
                           "--no-empty-directory", "--unmerged")),
    "ls-tree": Spec(short="rdtl", paths="rest", long=("--name-only", "--full-name", "--full-tree", "--long")),
    "log": Spec(short="psi", short_value="nSG", paths="dashdash",
                long=("--oneline", "--name-only", "--name-status", "--stat", "--no-merges", "--merges", "--follow",
                      "--all", "--no-color", "--patch", "--no-patch", "--reverse", "--abbrev-commit", "--date-order",
                      "--topo-order", "--first-parent", "--ignore-case"),
                long_value=("--max-count", "--format", "--pretty", "--grep", "--author", "--since", "--until",
                            "--after", "--before", "--diff-filter", "--date", "--skip")),
    "show": Spec(short="s", paths="dashdash",
                 long=("--stat", "--name-only", "--name-status", "--oneline", "--no-patch", "--no-color", "--patch",
                       "--abbrev-commit"),
                 long_value=("--format", "--pretty")),
    "branch": Spec(short="arv", paths="none", long=("--list", "--all", "--remotes", "--show-current", "--verbose"),
                   long_value=("--contains", "--merged", "--no-merged", "--points-at")),
    "check-ignore": Spec(short="vnq", paths="all", long=("--verbose", "--non-matching", "--quiet", "--no-index")),
    "cat-file": Spec(short="ptse", paths="none"),
    "rev-parse": Spec(short="q", paths="none", long=("--short", "--verify", "--quiet", "--abbrev-ref",
                                                     "--show-toplevel", "--is-inside-work-tree", "--show-prefix")),
    "diff": Spec(paths="dashdash", long=("--stat", "--name-only", "--cached", "--staged", "--no-color", "--no-renames"),
                 long_value=("--diff-filter",)),
    "status": Spec(short="sb", paths="dashdash", long=("--short", "--branch", "--porcelain")),
}


def squash(text):
    return " ".join(str(text).split())


def parse_git(sub, spec, words):
    """The positional words that name paths, or None when a word is outside the spec."""
    seen, positional, after = set(), [], []
    dashdash, i = False, 0
    while i < len(words):
        word, i = words[i], i + 1
        if dashdash:
            after.append(word)
        elif word == "--":
            dashdash = True
        elif word.startswith("--"):
            name, eq, _value = word.partition("=")
            if name in spec.long_value:
                if not eq:
                    if i >= len(words):
                        return None
                    i += 1
            elif name not in spec.long or eq:
                return None
            seen.add(name)
        elif word.startswith("-") and len(word) > 1:
            if sub == "log" and re.fullmatch(r"-\d+", word):
                continue
            letters = word[1:]
            for at, letter in enumerate(letters):
                seen.add("-" + letter)
                if letter in spec.short_value:
                    if at + 1 == len(letters):
                        if i >= len(words):
                            return None
                        i += 1
                    break
                if letter not in spec.short:
                    return None
        else:
            positional.append(word)
    named = {"dashdash": [], "all": positional, "rest": positional[1:], "none": [],
             "grep": positional if "-e" in seen else positional[1:]}[spec.paths]
    return after + named


def _git(args, cwd):
    return _git_run.run(args, cwd=cwd, timeout=COMMAND_TIMEOUT, optional_locks=False, input=b"",
                        prefix=("--no-pager",))


def _inside(root, value):
    """`root/value` when `value` is a relative directory with no `..` under `root`, else None."""
    part = Path(value)
    if part.is_absolute() or ".." in part.parts:
        return None
    target = (root / part).resolve()
    return target if target.is_dir() and (target == root or root in target.parents) else None


def check_command(command, output, root):
    """None when the item holds, else why it does not."""
    if SHELL.search(command):
        return "refused: shell syntax"
    try:
        argv = shlex.split(command)
    except ValueError:
        return "refused: unparseable command"
    cwd = root
    if len(argv) > 3 and argv[:2] == ["git", "-C"]:
        cwd = _inside(root, argv[2])
        if cwd is None:
            return "refused: -C names no directory under the root"
        argv = ["git", *argv[3:]]
    if len(argv) < 2 or argv[0] != "git" or argv[1] not in GIT_SPECS:
        return "refused: not an allow-listed read-only git command"
    sub = argv[1]
    paths = parse_git(sub, GIT_SPECS[sub], argv[2:])
    if paths is None:
        return "refused: a flag outside the allow-list"
    try:
        run = _git(argv[1:], cwd)
    except (OSError, subprocess.TimeoutExpired):
        return "not run: git unavailable or past its timeout"
    stdout = run.text.replace("\r\n", "\n")
    if run.returncode not in ((0, 1) if sub == "grep" else (0,)) or run.err_text.strip():
        return "git failed: " + (run.err_text.strip().splitlines() or ["exit %d" % run.returncode])[0]
    if output.strip():
        return None if squash(output) in squash(stdout) else "output not in the command's stdout"
    if stdout.strip():
        return "output empty, but the command printed text"
    for spec in paths:
        try:
            listed = _git(["ls-files", "--", spec], cwd)
        except (OSError, subprocess.TimeoutExpired):
            return "not run: git unavailable or past its timeout"
        if not listed.text.strip():
            return "pathspec %s matches no tracked path" % spec
    return None


def _typed_text(record):
    """The text the owner typed in a user-role record: tool results, assistant turns and other records give ''."""
    if not isinstance(record, dict):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    if record.get("type") != "user" and message.get("role") != "user":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _source(cited, root):
    """(path, error): the file a `path[:N[-M]]` citation names, relative to root; error when it is not a file."""
    found = LOCATION.match(cited)
    path = Path(found.group(1) if found else cited)
    if not path.is_absolute():
        path = root / path
    return (path, None) if path.is_file() else (path, "no such file")


def check_file(cited, quote, root):
    if not quote.strip():
        return "empty quote"
    path, error = _source(cited, root)
    if error:
        return error
    try:
        data = path.read_bytes()
    except OSError:
        return "unreadable"
    if b"\0" in data[:BINARY_PROBE]:
        return "binary file"
    return None if squash(quote) in squash(data.decode("utf-8", "replace")) else "quote not in file"


def check_owner(cited, quote, root):
    if not quote.strip():
        return "empty quote"
    path, error = _source(cited, root)
    if error:
        return "no such transcript"
    needle = squash(quote)
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if needle in squash(_typed_text(record)):
                return None
    return "words not in any owner turn"


def check_item(item, root):
    if "command" in item:
        if not isinstance(item.get("command"), str) or not isinstance(item.get("output"), str):
            return "malformed: a command item needs command and output strings"
        return check_command(item["command"], item["output"], root)
    if not isinstance(item.get("file"), str) or not isinstance(item.get("quote"), str):
        return "malformed: a file item needs file and quote strings"
    if item["file"].endswith(".jsonl"):
        return check_owner(item["file"], item["quote"], root)
    return check_file(item["file"], item["quote"], root)


def label(item):
    return item.get("command") or item.get("file") or "(no source)"


def findings_items(text):
    """[(heading, items)] for every fenced json block under a `##` heading. ValueError when a block is not a
    JSON list of objects."""
    marks = list(HEADING.finditer(text))
    sections = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        for block in FENCE.findall(text[mark.end():end]):
            try:
                items = json.loads(block)
            except ValueError as exc:
                raise ValueError("%s: json block is not JSON (%s)" % (mark.group(1).strip(), exc))
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ValueError("%s: json block is not a list of evidence items" % mark.group(1).strip())
            sections.append((mark.group(1).strip(), items))
    return sections


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the quotes, command outputs and owner words a findings file cites.")
    parser.add_argument("findings", help="markdown findings file")
    parser.add_argument("--root", required=True, help="the reviewed checkout the citations resolve against")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print("quote_check: --root is not a directory: %s" % root, file=sys.stderr)
        return 2
    try:
        text = Path(args.findings).read_text(encoding="utf-8", errors="replace")
        sections = findings_items(text)
    except (OSError, ValueError) as exc:
        print("quote_check: %s" % exc, file=sys.stderr)
        return 2
    total = misses = 0
    for heading, items in sections:
        for number, item in enumerate(items, 1):
            total += 1
            reason = check_item(item, root)
            if reason:
                misses += 1
                print("MISS %s [item %d] %s: %s" % (heading, number, label(item), reason))
    print("quote_check: %d items, %d misses under %s" % (total, misses, root))
    return 1 if misses else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # a finding's heading may hold a character the console cannot encode
    sys.exit(main())
