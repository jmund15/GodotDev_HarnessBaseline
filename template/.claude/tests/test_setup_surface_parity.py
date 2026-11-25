#!/usr/bin/env python3
"""Re-runnable proof that every setup surface dereferences `reference/project_stack.md`.

`project_stack.md` is the pins SSOT, and four surfaces hard-code a copy of one of its values: the
cloud script's engine and .NET pins, the SessionStart loader's Godot pin, the `.mcp.json` server's
package version and the cloud script's copy of it. Nothing compared them, so each could drift on its
own. This proof reads all of them.

Every assertion runs twice: green against this checkout, and RED against a planted copy carrying the
drift it is meant to catch. The RED arm is what proves the assertion binds a real value instead of
comparing a source with itself.

    python3 .claude/tests/test_setup_surface_parity.py
"""
import ast
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
REPO = CLAUDE.parent

SURFACES = ("cloud-setup.sh", "project_stack.md", "session_context_loader_godot.py", ".mcp.json",
            "workstation_setup.md", "cloud_dev.md")

COPIES = {
    "cloud-setup.sh": ".claude/cloud-setup.sh",
    "project_stack.md": ".claude/reference/project_stack.md",
    "session_context_loader_godot.py": ".claude/hooks/session_context_loader_godot.py",
    ".mcp.json": ".mcp.json",
    "workstation_setup.md": ".claude/commands/workstation_setup.md",
    "cloud_dev.md": ".claude/rules/cloud_dev.md",
}

FAILURES = []


def case(label, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a proof reports, it does not stop
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s%s" % (label, (" -- " + str(detail)[:220]) if detail else ""))


# ---------------------------------------------------------------- readers

def _read(root, which):
    return (Path(root) / COPIES[which]).read_text(encoding="utf-8")


def project_stack_pins(root):
    """`{godot, godot_release, dotnet, godot_mcp}` from the SSOT. A pin with no line parses None."""
    text = _read(root, "project_stack.md")
    engine = re.search(r"Godot engine:\s*\*\*([0-9][\w.]*)\*\*\s*([A-Za-z]+)", text)
    dotnet = re.search(r"C\s*#\s*/\s*\.NET:\s*\*\*([0-9][\w.]*)\*\*", text)
    mcp_line = next((line for line in text.splitlines() if "godot-mcp-runtime" in line), "")
    mcp = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)", mcp_line)
    shell = re.search(r"PowerShell \(cloud\):\s*\*\*([0-9][\w.]*)\*\*", text)
    return {
        "powershell": shell.group(1) if shell else None,
        "godot": engine.group(1) if engine else None,
        "godot_release": engine.group(2) if engine else None,
        "dotnet": dotnet.group(1) if dotnet else None,
        "godot_mcp": mcp.group(1) if mcp else None,
    }


def _assignments(text):
    """{NAME: value} for every top-level `NAME = <literal str>` in a Python source."""
    found = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            found[node.targets[0].id] = node.value.value
    return found


def cloud_script_pins(root):
    """`KEY="value"` assignments in the cloud script, plus a literal `godot-mcp-runtime@<v>`."""
    text = _read(root, "cloud-setup.sh")
    pins = dict(re.findall(r'(?m)^([A-Z_]+)="([^"]*)"', text))
    if not pins.get("GODOT_MCP_VERSION"):
        spec = re.search(r"godot-mcp-runtime@([0-9][\w.\-]*)", text)
        pins["GODOT_MCP_VERSION"] = spec.group(1) if spec else ""
    return pins


def mcp_package_version(root):
    """The `godot-mcp-runtime@<version>` a `.mcp.json` server launches, or None."""
    config = json.loads((Path(root) / COPIES[".mcp.json"]).read_text(encoding="utf-8"))["mcpServers"]
    for _name, entry in config.items():
        for arg in entry.get("args") or []:
            spec = re.search(r"godot-mcp-runtime@([^\s\"']+)", str(arg))
            if spec:
                return spec.group(1)
    return None


# ---------------------------------------------------------------- the assertions

def godot_pin_agrees(root):
    """(a) the cloud script's engine pins and the loader's Godot pin equal the SSOT's."""
    pins = project_stack_pins(root)
    wanted = (pins["godot"], pins["godot_release"])
    got = []
    script = cloud_script_pins(root)
    got.append(("cloud-setup.sh GODOT_VERSION/GODOT_RELEASE",
                (script.get("GODOT_VERSION"), script.get("GODOT_RELEASE"))))
    loader = _assignments(_read(root, "session_context_loader_godot.py")).get("GODOT_VERSION")
    version, _sep, release = (loader or "").partition("-")
    got.append(("session_context_loader_godot.py GODOT_VERSION", (version or None, release or None)))
    disagreements = ["%s is %s, SSOT says %s" % (who, value, wanted) for who, value in got
                     if value != wanted]
    return not disagreements, "; ".join(disagreements)


def dotnet_pin_agrees(root):
    """(a) the cloud script's .NET channel equals the SSOT's."""
    wanted = project_stack_pins(root)["dotnet"]
    got = cloud_script_pins(root).get("DOTNET_CHANNEL")
    return got == wanted and bool(wanted), "cloud-setup.sh DOTNET_CHANNEL is %r, SSOT says %r" % (
        got, wanted)


def powershell_pin_agrees(root):
    """(a) the cloud script's PowerShell version equals the SSOT's."""
    wanted = project_stack_pins(root)["powershell"]
    got = cloud_script_pins(root).get("POWERSHELL_VERSION")
    return got == wanted and bool(wanted), "cloud-setup.sh POWERSHELL_VERSION is %r, SSOT says %r" % (
        got, wanted)


def mcp_version_agrees(root):
    """(b) the MCP package version in `.mcp.json` and in the cloud script equals the SSOT's."""
    wanted = project_stack_pins(root)["godot_mcp"]
    got = {"cloud-setup.sh": cloud_script_pins(root).get("GODOT_MCP_VERSION") or None,
           ".mcp.json": mcp_package_version(root)}
    disagreements = ["%s is %r, SSOT says %r" % (who, value, wanted) for who, value in got.items()
                     if value != wanted]
    return bool(wanted) and not disagreements, "; ".join(disagreements)


def every_server_is_documented(root):
    """(c) every `.mcp.json` server name is named in the command and the cloud contract."""
    config = json.loads((Path(root) / COPIES[".mcp.json"]).read_text(encoding="utf-8"))["mcpServers"]
    command = _read(root, "workstation_setup.md").lower()
    contract = _read(root, "cloud_dev.md").lower()
    missing = [name for name in config
               if name.lower() not in command or name.lower() not in contract]
    return not missing, "servers no surface names: %s" % missing


def fingerprint_is_current(root):
    """(d) the cloud script's stored SETUP_FINGERPRINT is its own digest."""
    ok, message = _workstation_profile().check_fingerprint(str(Path(root) / COPIES["cloud-setup.sh"]))
    return ok, message


def _workstation_profile():
    """The repo's own tool, loaded by path so the proof reads it wherever it is run from."""
    spec = importlib.util.spec_from_file_location("parity_workstation_profile_godot",
                                                  REPO / ".claude" / "tools" / "workstation_profile_godot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKS = (("(a) the cloud script's engine pins and the loader's Godot pin match the SSOT",
           godot_pin_agrees, ("godot_script", "godot_loader")),
          ("(a) the cloud script's .NET channel matches the SSOT", dotnet_pin_agrees, ("dotnet",)),
          ("(a) the cloud script's PowerShell version matches the SSOT", powershell_pin_agrees, ("pwsh_script",)),
          ("(b) the MCP package version in .mcp.json and the cloud script matches the SSOT",
           mcp_version_agrees, ("mcp_script", "mcp_json")),
          ("(c) every .mcp.json server is named in the command and the cloud contract",
           every_server_is_documented, ("extra_server",)),
          ("(d) the cloud script's stored fingerprint is current", fingerprint_is_current,
           ("unstored_fingerprint",)))


# ---------------------------------------------------------------- the planted drift arms

def drift_root(tmp, edits):
    """A copy of the setup surface with `edits` applied. {SURFACES key: (old, new)}."""
    root = Path(tmp) / ("drift_" + re.sub(r"\W+", "_", "".join(edits))[:40])
    for key, rel in COPIES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, target)
    for key, (old, new) in edits.items():
        target = root / COPIES[key]
        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise AssertionError("planted edit not found in %s: %r" % (key, old))
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return root


DRIFT = {
    "godot_script": {"cloud-setup.sh": ('GODOT_VERSION="4.7.1"', 'GODOT_VERSION="4.6.0"')},
    "godot_loader": {"session_context_loader_godot.py": ('GODOT_VERSION = "4.7.1-stable"',
                                                   'GODOT_VERSION = "4.6.0-stable"')},
    "dotnet": {"cloud-setup.sh": ('DOTNET_CHANNEL="9.0"', 'DOTNET_CHANNEL="10.0"')},
    "pwsh_script": {"cloud-setup.sh": ('POWERSHELL_VERSION="7.5.12"', 'POWERSHELL_VERSION="7.4.0"')},
    "mcp_script": {"cloud-setup.sh": ('GODOT_MCP_VERSION="3.8.0"', 'GODOT_MCP_VERSION="3.7.0"')},
    "mcp_json": {".mcp.json": ('"godot-mcp-runtime@3.8.0"', '"godot-mcp-runtime@3.7.0"')},
    "extra_server": {".mcp.json": ('"godot": {', '"godot": {}, "extraServer": {')},
    "unstored_fingerprint": {"cloud-setup.sh": ("set -u", "set -u\n# drifted\n")},
}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="parity_"))

    # An unplanted copy and a planted one run through the SAME function: green on the first, red on
    # the second. Only the planted value differs, so the arm proves the assertion binds it.
    clean = drift_root(tmp, {})
    for label, check, arms in CHECKS:
        case("green: " + label, lambda check=check: check(clean))
        for arm in arms:
            try:
                planted = drift_root(tmp, DRIFT[arm])
            except AssertionError as exc:
                case("RED on planted drift (%s): %s" % (arm, label), lambda exc=exc: (False, str(exc)))
                continue
            case("RED on planted drift (%s): %s" % (arm, label),
                 lambda check=check, planted=planted: (not check(planted)[0], check(planted)[1]))

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
