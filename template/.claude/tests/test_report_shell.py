"""Re-runnable proof for tools/report_shell.py. Shell contract: page() assembles a self-contained document.

    python3 .claude/tests/test_report_shell.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
TOOLS = os.path.join(CLAUDE, "tools")


def main():
    failures, ran = [], []

    def check(ok, label, detail=""):
        ran.append(label)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    sys.path.insert(0, TOOLS)
    try:
        import report_shell
    except ImportError as exc:
        check(False, "report_shell imports", str(exc))
        print("\n%d/%d cases pass" % (len(ran) - len(failures), len(ran)))
        for line in failures:
            print("  FAIL " + line)
        return 1

    data = json.dumps({"note": "</script><b>x</b>"})
    page = report_shell.page("Fixture title", css_extra=".mine{color:red}", header="<h1>Head</h1>",
                             data=data, script="const B=JSON.parse(document.getElementById('data').textContent);")
    check("<title>Fixture title</title>" in page and "<h1>Head</h1>" in page and ".mine{color:red}" in page,
          "page carries the title, header markup and the caller's extra CSS")
    check("</script><b>x</b>" not in page and "<\\/script>" in page,
          "page escapes '</' in the inlined JSON so data cannot close the script element")
    check(not re.search(r"https?://|<link|<script[^>]*src=", page),
          "page loads nothing over the network")
    check("prefers-color-scheme:dark" in page and "--blue700" in page,
          "page carries the light and dark palette tokens")
    for name in ("esc", "fk", "showTip", "moveTip", "hideTip", "addTab", "show"):
        if not re.search(r"(const %s=|function %s\()" % (name, name), page):
            check(False, "page defines the JS primitive %s" % name)
            break
    else:
        check(True, "page defines the JS primitives esc, fk, tooltip and tab functions")

    print("\n%d/%d cases pass" % (len(ran) - len(failures), len(ran)))
    for line in failures:
        print("  FAIL " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
