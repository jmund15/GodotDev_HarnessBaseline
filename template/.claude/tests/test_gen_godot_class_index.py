#!/usr/bin/env python3
"""Proof for tools/gen_godot_class_index.py: a wrong argument count prints the usage line only, and a real
run writes the index from a planted classes directory.

    python3 .claude/tests/test_gen_godot_class_index.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, ".claude", "tools", "gen_godot_class_index.py")

CLASS_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<class name="Node3D" inherits="Node" api_type="core">
\t<brief_description>
\t\tBase object in 3D space, with [b]transform[/b] and visibility.
\t</brief_description>
</class>
"""


def run(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, cwd=REPO, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    cases = []
    rc, out = run()
    cases.append(("no arguments exits nonzero with the usage line",
                  rc != 0 and "Usage: gen_godot_class_index.py" in out))
    cases.append(("the usage exit omits the module's rationale prose",
                  "415 KB" not in out and "gitignored" not in out))
    with tempfile.TemporaryDirectory() as tmp:
        classes = os.path.join(tmp, "classes")
        os.makedirs(classes)
        with open(os.path.join(classes, "Node3D.xml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(CLASS_XML)
        out_md = os.path.join(tmp, "index.md")
        rc, out = run(classes, out_md, "4.7.1")
        body = open(out_md, encoding="utf-8").read() if os.path.exists(out_md) else ""
        cases.append(("a real run writes the class and its cleaned brief",
                      rc == 0 and "Node3D" in body and "transform" in body and "[b]" not in body))
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(cases) - len(failed)}/{len(cases)} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
