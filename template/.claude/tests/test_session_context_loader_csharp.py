#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader_csharp.py, the C# language-server step module
the loader loads through `_optional_hooks`: it runs only in a checkout with a .sln, .slnx or .csproj
at its root, and its early-load nudge appears only for a healthy plugin on a local machine.

    python3 .claude/tests/test_session_context_loader_csharp.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader_csharp as csl  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail[:200] if detail else ""))

    with tempfile.TemporaryDirectory(prefix="csl_") as tmp:
        root = Path(tmp)
        check("a root without a solution or project file is not a C# project", csl.csharp_project(root) is False)
        with patch.object(csl, "verify_lsp_plugin", lambda: "OK"):
            check("no C# project: no lsp_plugin status", csl.local_checks(root) == {})
            for name in ("App.sln", "App.slnx", "App.csproj"):
                (root / name).write_text("", encoding="utf-8")
                check("%s makes it a C# project that reports lsp_plugin" % name,
                      csl.csharp_project(root) is True and csl.local_checks(root) == {"lsp_plugin": "OK"})
                (root / name).unlink()

    # The plugin check against an empty home: every missing piece is named, and nothing raises.
    with tempfile.TemporaryDirectory(prefix="csl_home_") as home, \
            patch.object(Path, "home", lambda: Path(home)), patch.dict(os.environ, {"ENABLE_LSP_TOOL": ""}):
        result = csl.verify_lsp_plugin()
        check("an empty home is BROKEN and names the missing adapter, binary and plugin",
              result.startswith("BROKEN: ") and "csharp-ls-adapter.js missing" in result
              and "csharp-ls-original.exe missing" in result and "plugin.json missing" in result
              and "ENABLE_LSP_TOOL" in result, result)

    ok = {"lsp_plugin": "OK"}
    nudge = "\n".join(csl.nudge_lines(ok, False))
    check("a healthy local plugin gets the early-load nudge",
          "<lsp-early-load>" in nudge and 'ToolSearch(query="select:LSP"' in nudge and "</lsp-early-load>" in nudge, nudge)
    check("a cloud session gets no nudge", csl.nudge_lines(ok, True) == [])
    check("a broken plugin gets no nudge", csl.nudge_lines({"lsp_plugin": "BROKEN: x"}, False) == [])
    check("no lsp_plugin status gets no nudge", csl.nudge_lines({}, False) == [])

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
