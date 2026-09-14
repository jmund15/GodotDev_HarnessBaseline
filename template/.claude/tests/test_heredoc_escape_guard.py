#!/usr/bin/env python3
"""Proof for heredoc_escape_guard — built from the five real commands that broke this session.

The positives are verbatim shapes that actually corrupted a patch. The negatives are what makes the
guard survivable: a heredoc with no escapes round-trips fine, and blocking those would push the next
session back to whatever gets past the guard rather than to the Write tool.

Run: python3 .claude/tests/test_heredoc_escape_guard.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "heg", os.path.join(HERE, "..", "hooks", "heredoc_escape_guard.py"))
heg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(heg)

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---- POSITIVES: the shapes that actually broke -------------------------------------------------

@case("the consolidate.py break: newline= kwarg collapsed mid-heredoc")
def c_newline():
    cmd = ("python3 - <<'PY'\n"
           "s = s.replace(x, y)\n"
           "open(P,'w',encoding='utf-8',newline='\\n').write(s)\n"
           "PY")
    r = heg.verdict(cmd) or ""
    return "BLOCKED" in r and "\\n" in r


@case("the kill_guard break: a regex literal with \\b and \\. in the body")
def c_regex():
    cmd = ("python3 - <<'PY'\n"
           "old = r'(r\"\\\\bclaude(\\\\.exe)?\\\\b\", \"x\")'\n"
           "PY")
    return "BLOCKED" in (heg.verdict(cmd) or "")


@case("the guard names the FIX, not just the problem")
def c_names_fix():
    cmd = "python3 - <<'PY'\nprint('a\\tb')\nPY"
    r = heg.verdict(cmd) or ""
    return ".claude/scratch/" in r and "Write" in r and "Edit tool" in r


@case("`python <<EOF` (unquoted tag, no dash) is caught too")
def c_unquoted():
    return "BLOCKED" in (heg.verdict("python <<EOF\nx = '\\n'\nEOF") or "")


@case("the report names the escapes it found")
def c_reports():
    r = heg.verdict("python3 - <<'PY'\nx='\\t'\ny='\\\\'\nPY") or ""
    return "contains:" in r


# ---- NEGATIVES: what must keep working ---------------------------------------------------------

@case("NEGATIVE: a heredoc python with NO backslash is allowed")
def c_clean():
    cmd = ("python3 - <<'PY'\n"
           "import json\n"
           "print(json.load(open('x.json'))['k'])\n"
           "PY")
    return heg.verdict(cmd) is None


@case("NEGATIVE: a heredoc to `cat > file` is not a python program")
def c_cat():
    return heg.verdict("cat > x.txt <<'EOF'\nline with \\n in it\nEOF") is None


@case("a PATH-QUALIFIED interpreter is still the command, and is caught")
def c_abs_path_interpreter():
    # The lookbehind that stopped `.py` filenames first excluded `/` too, which hid the same
    # command written with an absolute path -- the escape route left open by the fix.
    cmd = "/usr/bin/python3 - <<'PY'\nopen(P,'w',newline='\\n').write(s)\nPY"
    return heg.verdict(cmd) is not None


@case("...and so is one invoked through a relative path")
def c_rel_path_interpreter():
    cmd = "./tools/python3 - <<'PY'\ns = s.replace('a\\\\b', 'c')\nPY"
    return heg.verdict(cmd) is not None


@case("NEGATIVE: writing a .py file by heredoc is the PRESCRIBED fix, never the defect")
def c_write_py_file():
    # Misfired 2026-09-08: `\b(py)\b` matched the `.py` SUFFIX of the target filename, so the guard
    # denied the exact escape route its own message recommends.
    cmd = "cat > .claude/scratch/triage.py <<'PY'\nimport re\nre.sub(r':\\d+$', '', f)\nPY"
    return heg.verdict(cmd) is None


@case("NEGATIVE: a heredoc fed to a python SCRIPT is data, not source")
def c_script_stdin():
    # `python3 build.py <<EOF` hands the heredoc to that script's stdin. A backslash there belongs
    # to the DATA; rewriting it would corrupt exactly what the caller meant to send.
    cmd = "python3 build.py <<'EOF'\npath\\to\\thing\nEOF"
    return heg.verdict(cmd) is None


@case("NEGATIVE: `git commit -F` heredoc is untouched")
def c_commit():
    return heg.verdict("git commit -F - <<'MSG'\nfix: a\\b\nMSG") is None


@case("NEGATIVE: running a python FILE with backslashes in its args is fine")
def c_file():
    return heg.verdict("python3 .claude/scratch/patch.py --path 'a\\b'") is None


@case("NEGATIVE: no heredoc at all")
def c_none():
    return heg.verdict("python3 -c \"print('\\n')\"") is None


@case("only the offending heredoc is named when several are present")
def c_mixed():
    cmd = ("python3 - <<'A'\nprint(1)\nA\n"
           "python3 - <<'B'\nprint('\\n')\nB")
    r = heg.verdict(cmd) or ""
    return "<<B" in r and "<<A" not in r


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
