#!/usr/bin/env python3
"""Proof for scripts/validate_commands.py.

Plants a command tree and runs the checks against it: a template variable counts as injected only when a
declared orchestrator names it, a `###` subheading or an unlisted `##` section never feeds a checklist
count, the external-model registry check skips an absent file and fails an unreadable one, and the
fixture checklist selectors accept `I+test`.

    python3 .claude/tests/test_validate_commands.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_commands_under_test", CLAUDE / "scripts" / "validate_commands.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def variable_checks(vc):
    with tempfile.TemporaryDirectory() as tmp:
        commands = Path(tmp) / "commands"
        write(commands / "agents" / "x_agents.md", "### a\nuse {{FOO}}\n")
        saved = vc.COMMANDS_DIR, vc.AGENTS_DIR, vc.KNOWN_INJECTIONS
        vc.COMMANDS_DIR, vc.AGENTS_DIR = commands, commands / "agents"
        try:
            vc.KNOWN_INJECTIONS = {"FOO": ["orch.md"]}
            write(commands / "orch.md", "nothing here\n")
            out = "\n".join(vc.check_template_variables())
            check("a declared orchestrator that never names the variable fails", out.startswith("[FAIL]") and "FOO" in out, out)
            write(commands / "orch.md", "injects {{FOO}}\n")
            out = "\n".join(vc.check_template_variables())
            check("a declared orchestrator that names the variable passes", out.startswith("[PASS]"), out)
            (commands / "orch.md").unlink()
            out = "\n".join(vc.check_template_variables())
            check("a declared orchestrator that is absent fails", out.startswith("[FAIL]") and "orch.md" in out, out)
            vc.KNOWN_INJECTIONS = {}
            out = "\n".join(vc.check_template_variables())
            check("an undeclared variable fails", out.startswith("[FAIL]") and "no known injection source" in out, out)
            write(commands / "agents" / "x_agents.md", "### a\n{{PROJECT_NAME}} at {{VAULT_ROOT}}\n")
            out = "\n".join(vc.check_template_variables())
            check("a baseline sync placeholder needs no orchestrator", out.startswith("[PASS]"), out)
        finally:
            vc.COMMANDS_DIR, vc.AGENTS_DIR, vc.KNOWN_INJECTIONS = saved


def count_checks(vc):
    planted = (
        "# Q\n\n## Design (D)\n\n- [ ] **a**\n\n### Design (D)\n\n- [ ] **b**\n\n"
        "## Other\n\n- [ ] **c**\n- [ ] **c2**\n\n## Intuitiveness (I)\n\n- [ ] **d**\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        checklists = Path(tmp)
        write(checklists / "code_quality.md", planted)
        write(checklists / "test_quality.md", "# T\n\n- [ ] **t**\n")
        saved = vc.CHECKLISTS_DIR, vc.EXPECTED_COUNTS, vc.EXPECTED_CODE_QUALITY_TOTAL, vc.EXPECTED_TEST_QUALITY_TOTAL
        vc.CHECKLISTS_DIR = checklists
        vc.EXPECTED_COUNTS = {"Design (D)": 2, "Intuitiveness (I)": 1}
        vc.EXPECTED_CODE_QUALITY_TOTAL = 3
        vc.EXPECTED_TEST_QUALITY_TOTAL = 1
        try:
            out = "\n".join(vc.check_checklist_counts())
            check("a `###` subheading keeps the listed section's count, and an unlisted `##` section ends it",
                  out.startswith("[PASS]"), out)
        finally:
            vc.CHECKLISTS_DIR, vc.EXPECTED_COUNTS, vc.EXPECTED_CODE_QUALITY_TOTAL, vc.EXPECTED_TEST_QUALITY_TOTAL = saved


def shipped_tree_checks(vc):
    saved = vc.COMMANDS_DIR, vc.AGENTS_DIR, vc.CHECKLISTS_DIR
    vc.COMMANDS_DIR = CLAUDE / "commands"
    vc.AGENTS_DIR = vc.COMMANDS_DIR / "agents"
    vc.CHECKLISTS_DIR = vc.COMMANDS_DIR / "checklists"
    try:
        out = "\n".join(vc.check_template_variables())
        check("the shipped templates' variables all have a named injector", out.startswith("[PASS]"), out)
        out = "\n".join(vc.check_checklist_counts())
        check("the shipped checklist counts match the checklist files", out.startswith("[PASS]"), out)
    finally:
        vc.COMMANDS_DIR, vc.AGENTS_DIR, vc.CHECKLISTS_DIR = saved


def reference_checks(vc):
    with tempfile.TemporaryDirectory() as tmp:
        commands = Path(tmp) / "commands"
        write(commands / "here.md", "# Here\n\n## Kept rule\n")
        saved = vc.COMMANDS_DIR
        vc.COMMANDS_DIR = commands
        try:
            write(commands / "a.md", "[ok](here.md) [ok](here.md#kept-rule)\n")
            out = "\n".join(vc.check_file_references())
            check("a link to a present file passes, with or without an anchor", out.startswith("[PASS]"), out)
            write(commands / "a.md", "[gone](gone.md#some-rule)\n")
            out = "\n".join(vc.check_file_references())
            check("an anchored link to an absent file fails", out.startswith("[FAIL]") and "gone.md" in out, out)
            write(commands / "a.md", "[gone](gone.md)\n")
            out = "\n".join(vc.check_file_references())
            check("a bare link to an absent file fails", out.startswith("[FAIL]") and "gone.md" in out, out)
        finally:
            vc.COMMANDS_DIR = saved
    for label, run in (("links", vc.check_file_references), ("template variables", vc.check_template_variables)):
        out = "\n".join(run())
        check(f"the shipped command tree's {label} pass", out.startswith("[PASS]"), out)


def registry_checks(vc):
    check("the registry check exists", hasattr(vc, "check_external_model_registry"))
    if not hasattr(vc, "check_external_model_registry"):
        return
    check("the registry check runs with the others", vc.check_external_model_registry in vc.main.__code__.co_consts
          or "check_external_model_registry" in vc.main.__code__.co_names)
    saved = vc.REGISTRY_FILE, vc.TOOLS_DIR
    vc.TOOLS_DIR = CLAUDE / "tools"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            vc.REGISTRY_FILE = Path(tmp) / "absent.json"
            check("an absent registry is skipped", vc.check_external_model_registry() == [])
            broken = Path(tmp) / "external_models.json"
            broken.write_text("{ not json", encoding="utf-8")
            vc.REGISTRY_FILE = broken
            out = "\n".join(vc.check_external_model_registry())
            check("an unreadable registry fails", out.startswith("[FAIL]"), out)
        shipped = CLAUDE / "reference" / "external_models.json"
        if shipped.is_file():
            vc.REGISTRY_FILE = shipped
            out = "\n".join(vc.check_external_model_registry())
            check("the shipped registry passes", out.startswith("[PASS]"), out)
    finally:
        vc.REGISTRY_FILE, vc.TOOLS_DIR = saved


def main():
    vc = load_validator()
    variable_checks(vc)
    count_checks(vc)
    shipped_tree_checks(vc)
    reference_checks(vc)
    registry_checks(vc)
    check("the fixture selector `I+test` is valid", "I+test" in vc.VALID_CHECKLIST_SECTIONS)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        for f in FAILURES:
            print("  " + f[:300])
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
