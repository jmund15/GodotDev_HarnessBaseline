#!/usr/bin/env python3
"""Re-runnable proof for hooks/session_context_loader_csharp.py, the C# language-server step module
the loader loads through `_optional_hooks`: it runs only in a checkout with a .sln, .slnx or .csproj
at its root, and its early-load nudge appears only for a healthy plugin on a local machine.

    python -m pytest .claude/tests/test_session_context_loader_csharp.py -q
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader_csharp as csl  # noqa: E402


def test_a_root_without_a_solution_or_project_file_is_not_a_csharp_project(tmp_path):
    assert csl.csharp_project(tmp_path) is False


def test_a_root_without_a_project_file_reports_no_lsp_status(tmp_path):
    with patch.object(csl, "verify_lsp_plugin", lambda: "OK"):
        assert csl.local_checks(tmp_path) == {}


@pytest.mark.parametrize("name", ["App.sln", "App.slnx", "App.csproj"])
def test_a_project_file_makes_the_root_a_csharp_project_that_reports_lsp_plugin(tmp_path, name):
    (tmp_path / name).write_text("", encoding="utf-8")
    with patch.object(csl, "verify_lsp_plugin", lambda: "OK"):
        assert csl.csharp_project(tmp_path) is True
        assert csl.local_checks(tmp_path) == {"lsp_plugin": "OK"}


def test_an_empty_home_is_broken_and_names_the_missing_adapter_binary_and_plugin(tmp_path, monkeypatch):
    """Every missing piece is named, and nothing raises."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("ENABLE_LSP_TOOL", "")
    result = csl.verify_lsp_plugin()
    assert (result.startswith("BROKEN: ") and "csharp-ls-adapter.js missing" in result
            and "csharp-ls-original.exe missing" in result and "plugin.json missing" in result
            and "ENABLE_LSP_TOOL" in result), result


def test_a_healthy_local_plugin_gets_the_early_load_nudge():
    nudge = "\n".join(csl.nudge_lines({"lsp_plugin": "OK"}, False))
    assert "<lsp-early-load>" in nudge and 'ToolSearch(query="select:LSP"' in nudge and "</lsp-early-load>" in nudge, nudge


@pytest.mark.parametrize("status, cloud", [({"lsp_plugin": "OK"}, True),
                                           ({"lsp_plugin": "BROKEN: x"}, False),
                                           ({}, False)],
                         ids=["cloud-session", "broken-plugin", "no-lsp-status"])
def test_no_nudge_for_a_cloud_session_a_broken_plugin_or_no_status(status, cloud):
    assert csl.nudge_lines(status, cloud) == []
