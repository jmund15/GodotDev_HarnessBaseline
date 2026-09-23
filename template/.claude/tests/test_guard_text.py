#!/usr/bin/env python3
"""Re-runnable proof for tools/guard_text.py, the one parser of delegate-guard tiers.

    python3 .claude/tests/test_guard_text.py

Planted cases copy the tool into a temp `.claude/tools/` beside planted `.claude/guards/` files
with known sections: the tool resolves `guards/` from its own path, so the exact output is known.
A live case runs the real tool on the real guard files for every shape and tier.
"""
import os
import shutil
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "guard_text.py")

PLANTED = {
    "any.md": "# any\n\n## detailed\nANY-DETAILED\n\n## condensed\nANY-CONDENSED\n",
    "review.md": "# review\n\n## detailed\nREVIEW-DETAILED\n\n## condensed\nREVIEW-CONDENSED\n",
    "survey.md": "# survey\n\n## detailed\nSURVEY-DETAILED\n",
    "author.md": "# author\n\n## detailed\nAUTHOR-DETAILED\n",
}


OVERLAYS = {
    "any.coding.md": "# any coding\n\n## detailed\nANY-CODING-DETAILED\n",
    "review.godot.md": "# review godot\n\n## detailed\nREVIEW-GODOT-DETAILED\n",
}


def planted_tool(tmp):
    tools = os.path.join(tmp, ".claude", "tools")
    guards = os.path.join(tmp, ".claude", "guards")
    os.makedirs(tools)
    os.makedirs(guards)
    shutil.copyfile(TOOL, os.path.join(tools, "guard_text.py"))
    for name, text in PLANTED.items():
        with open(os.path.join(guards, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return os.path.join(tools, "guard_text.py")


def run(tool, *args):
    r = subprocess.run([sys.executable, tool, *args], capture_output=True, timeout=30)
    # Text-mode stdout on Windows writes CRLF; the delivered rails are compared as text.
    return (r.returncode, r.stdout.decode("utf-8").replace("\r\n", "\n"),
            r.stderr.decode("utf-8").replace("\r\n", "\n"))


def main():
    cases = []
    with tempfile.TemporaryDirectory(prefix="guard_text_") as tmp:
        tool = planted_tool(tmp)

        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("a shape gets the `any` section, then its own, under one header",
                      rc == 0 and out == "[delegate rails — shape 'review', detailed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/review.md]\nANY-DETAILED\n\nREVIEW-DETAILED"))
        rc, out, _ = run(tool, "any", "detailed")
        cases.append(("shape `any` gets its section alone",
                      rc == 0 and out == "[delegate rails — shape 'any', detailed tier; home: .claude/guards/any.md]\nANY-DETAILED"))
        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("a section stops at the next heading", "CONDENSED" not in out))
        rc, out, _ = run(tool, "review", "none")
        cases.append(("tier `none` delivers the empty string", rc == 0 and out == ""))
        rc, out, err = run(tool, "survey", "condensed")
        cases.append(("a missing section exits 2 and names it", rc == 2 and out == "" and "no '## condensed' section" in err))
        rc, _, err = run(tool, "planner", "detailed")
        cases.append(("an unknown shape exits 2 with the legal set", rc == 2 and "any, survey, review, author" in err))
        rc, _, err = run(tool, "review", "loud")
        cases.append(("an unknown tier exits 2 with the legal set", rc == 2 and "detailed, condensed, minimal, none" in err))
        rc, _, err = run(tool, "review", "strict")
        cases.append(("a retired tier name exits 2", rc == 2))
        rc, _, err = run(tool, "review")
        cases.append(("a wrong argument count exits 2 with usage", rc == 2 and "usage: guard_text.py" in err))

    with tempfile.TemporaryDirectory(prefix="guard_text_overlay_") as tmp:
        tool = planted_tool(tmp)
        for name, text in OVERLAYS.items():
            with open(os.path.join(tmp, ".claude", "guards", name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("each present layer overlay follows its base section, listed in the header",
                      rc == 0 and out == "[delegate rails — shape 'review', detailed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/any.coding.md + .claude/guards/review.md"
                                        " + .claude/guards/review.godot.md]\nANY-DETAILED\n\nANY-CODING-DETAILED"
                                        "\n\nREVIEW-DETAILED\n\nREVIEW-GODOT-DETAILED"))
        rc, out, _ = run(tool, "review", "condensed")
        cases.append(("an overlay without the tier adds nothing",
                      rc == 0 and out == "[delegate rails — shape 'review', condensed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/review.md]\nANY-CONDENSED\n\nREVIEW-CONDENSED"))

    live = []
    for shape in ("any", "survey", "review", "author"):
        for tier in ("detailed", "condensed", "minimal"):
            rc, out, err = run(TOOL, shape, tier)
            if rc != 0 or not out.startswith("[delegate rails — shape '%s', %s tier;" % (shape, tier)):
                live.append("%s/%s rc=%d %s" % (shape, tier, rc, err.strip()[:80]))
    cases.append(("the real guards deliver every shape at every tier" + ("" if not live else ": " + "; ".join(live)),
                  not live))

    sys.path.insert(0, os.path.dirname(TOOL))
    import guard_text
    redirected = []
    guards = os.path.join(os.path.dirname(os.path.dirname(TOOL)), "guards")
    for name in sorted(os.listdir(guards)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(guards, name)
        with open(path, encoding="utf-8") as fh:
            if "Read this file's `## condensed` section." not in fh.read():
                continue
        shape = name.split(".")[0]
        rc, out, _ = run(TOOL, shape, "minimal")
        if "Read this file" in out or guard_text._section(path, "condensed") not in out:
            redirected.append(name)
    cases.append(("a minimal section that points at another section delivers that section's text"
                  + ("" if not redirected else ": " + ", ".join(redirected)), not redirected))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
