#!/usr/bin/env python3
"""Proof for tools/lens.py: a registry's lenses are the `###` headings under its templates section.

A registry with an empty key prefix must not turn preamble subsections or a lens's own `###`
subsections into lenses.

    python3 .claude/tests/test_lens.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import lens  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")



def quoted(text):
    return "\n".join(f"> {line}".rstrip() if line.strip() else ">" for line in text.splitlines())


def run_cli(fn, *args):
    import contextlib
    import io
    out, err, code = io.StringIO(), io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*args)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if not isinstance(exc.code, int) and exc.code is not None:
                err.write(str(exc.code))
    return code, out.getvalue(), err.getvalue()


def preload_checks():
    """A template's `preload:` header line inlines that file once per brief, whole and blockquoted."""
    doc_rel = "skills/demo/SKILL.md"
    doc = "# Demo doctrine\n\n### 1. First rule\nRun `git log` to see it.\n"
    text = (
        "# Plan check agents\n\nShared preamble line.\n\n## Agent Templates\n\n"
        f"### plc-alpha (alpha) — every plan\n\npreload: {doc_rel}\n\n```\nalpha prompt\n```\n\n"
        f"### plc-beta (beta) — every plan\n\npreload: {doc_rel}\n\n```\nbeta prompt\n```\n\n"
        "### plc-gamma (gamma) — every plan\n\npreload: skills/absent/SKILL.md\n\n```\ngamma prompt\n```\n\n"
        "### plc-plain (plain) — every plan\n\n```\nplain prompt\n```\n"
    )
    saved = (lens.AGENTS, lens.REGISTRIES, getattr(lens, "DOCTRINE_ROOT", None))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "agents").mkdir()
        (root / "agents" / "plan_check_agents.md").write_text(text, encoding="utf-8")
        (root / doc_rel).parent.mkdir(parents=True)
        (root / doc_rel).write_text(doc, encoding="utf-8")
        lens.AGENTS = root / "agents"
        lens.REGISTRIES = {"plan_check": ("plan_check_agents.md", "plc-")}
        lens.DOCTRINE_ROOT = root
        try:
            header = f"> Preloaded doctrine: {doc_rel}"
            for shared in (False, True):
                code, out, err = run_cli(lens.cmd_get, ["plc-alpha"], shared)
                tag = "get --shared" if shared else "get"
                check(f"{tag}: a declared file is inlined whole and blockquoted", code == 0
                      and out.count(quoted(doc)) == 1 and out.count(header) == 1, (code, err, out[-400:]))
                check(f"{tag}: the declaration line is consumed, not echoed", "preload:" not in out, out[:300])
            code, out, _ = run_cli(lens.cmd_get, ["plc-alpha", "plc-beta"], True)
            check("two lenses declaring one file inline it once per brief",
                  code == 0 and out.count(header) == 1 and out.count(quoted(doc)) == 1, out.count(header))
            code, out, _ = run_cli(lens.cmd_get, ["plc-plain"], True)
            check("a lens declaring nothing gets no doctrine", code == 0 and "Preloaded doctrine" not in out)
            for shared in (False, True):
                code, out, err = run_cli(lens.cmd_get, ["plc-gamma"], shared)
                check(f"a missing preload path exits 2 naming it ({'--shared' if shared else 'bare'})",
                      code == 2 and "skills/absent/SKILL.md" in err, (code, err))
            alpha = next(l for l in lens.parse("plan_check")[1] if l.key == "plc-alpha")
            plain = next(l for l in lens.parse("plan_check")[1] if l.key == "plc-plain")
            check("the index size includes preloaded bytes",
                  alpha.size - plain.size >= len(quoted(doc).encode("utf-8")), (alpha.size, plain.size))
            lens.EXTRA_PRELOADS = {"plc-plain": [doc_rel], "plc-alpha": [doc_rel, "skills/demo/extra.md"]}
            plain = next(l for l in lens.parse("plan_check")[1] if l.key == "plc-plain")
            alpha = next(l for l in lens.parse("plan_check")[1] if l.key == "plc-alpha")
            check("a project's adaptation preloads attach to a lens that declares none",
                  plain.preloads == [doc_rel], plain.preloads)
            check("an adaptation preload already declared in the header is not listed twice",
                  alpha.preloads == [doc_rel, "skills/demo/extra.md"], alpha.preloads)
            code, out, _ = run_cli(lens.cmd_get, ["plc-plain"], True)
            check("an adaptation preload is inlined like a declared one",
                  code == 0 and out.count(quoted(doc)) == 1, (code, out[-200:]))
        finally:
            lens.EXTRA_PRELOADS = {}
            lens.AGENTS, lens.REGISTRIES = saved[0], saved[1]
            if saved[2] is None:
                if hasattr(lens, "DOCTRINE_ROOT"):
                    del lens.DOCTRINE_ROOT
            else:
                lens.DOCTRINE_ROOT = saved[2]


def mandate_checks():
    """A template's `mandates:` header line maps the lens to catalog keys; the live registries declare them."""
    text = (
        "# Review agents\n\nShared.\n\n## Agent Templates\n\n"
        "### xx-alpha (alpha)\n\nmandates: cq.C, cq.D , parity\nadmission: triggered, conditional\n"
        "preload: rules/design_litmus.md\nkfm: index\nciters: plan\n\n```\nalpha prompt\n```\n\n"
        "### xx-plain (plain)\n\n```\nplain prompt\nmandates: not.a.declaration\nkfm: index\nciters: plan\n```\n"
    )
    saved = (lens.AGENTS, lens.REGISTRIES)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "review_agents.md").write_text(text, encoding="utf-8")
        lens.AGENTS = Path(tmp)
        lens.REGISTRIES = {"review": ("review_agents.md", "xx-")}
        try:
            alpha, plain = lens.parse("review")[1]
            check("a mandates: header line parses to catalog keys",
                  getattr(alpha, "mandates", None) == ["cq.C", "cq.D", "parity"], getattr(alpha, "mandates", None))
            check("a mandates: line inside the prompt fence is not a declaration",
                  getattr(plain, "mandates", None) == [], getattr(plain, "mandates", None))
            check("the declaration line is consumed, not echoed, and the preload line still is",
                  "mandates:" not in alpha.body() and "preload:" not in alpha.body())
            check("a kfm: header line declares the KFM catalog and is consumed from the body",
                  getattr(alpha, "kfm", None) is True and "kfm:" not in alpha.body(), getattr(alpha, "kfm", None))
            check("an admission: header line parses to its labels and is consumed from the body",
                  getattr(alpha, "admission", None) == ["triggered", "conditional"] and "admission:" not in alpha.body(),
                  getattr(alpha, "admission", None))
            check("a lens with no admission: header line has no labels", getattr(plain, "admission", None) == [],
                  getattr(plain, "admission", None))
            check("a kfm: line inside the prompt fence is not a declaration",
                  getattr(plain, "kfm", None) is False, getattr(plain, "kfm", None))
            check("a citers: plan header line declares the citer sweep and is consumed from the body",
                  getattr(alpha, "citers", None) == "plan" and "citers:" not in alpha.body(), getattr(alpha, "citers", None))
            check("a citers: line inside the prompt fence is not a declaration",
                  getattr(plain, "citers", None) == "", getattr(plain, "citers", None))
            code, out, _ = run_cli(lens.cmd_index, ["review"])
            check("index lists each lens's keys", code == 0 and "cq.C,cq.D,parity" in out, out)
        finally:
            lens.AGENTS, lens.REGISTRIES = saved

def registry_checks():
    """A key resolves to the registry with the longest matching prefix; an empty prefix takes only the rest."""
    saved = lens.REGISTRIES
    lens.REGISTRIES = {"review": ("review_agents.md", ""), "plan_check": ("plan_check_agents.md", "plc-"),
                       "explore": ("explore_agents.md", "exp-")}
    try:
        check("a prefixed key resolves to its registry although an empty prefix is listed first",
              lens.registry_of("plc-x") == "plan_check" and lens.registry_of("exp-y") == "explore",
              (lens.registry_of("plc-x"), lens.registry_of("exp-y")))
        check("a key no prefix claims falls to the empty-prefix registry", lens.registry_of("other") == "review",
              lens.registry_of("other"))
        lens.REGISTRIES = {"plan_check": ("plan_check_agents.md", "plc-")}
        check("a key no registry claims resolves to none", lens.registry_of("other") is None, lens.registry_of("other"))
    finally:
        lens.REGISTRIES = saved


def unshipped_registry_checks():
    """A registry whose file a layer-stripped consumer never received is skipped, not fatal, by every all-registry walk."""
    text = "# Review agents\n\n## Agent Templates\n\n### code-reviewer -- `model: \"opus\"`\nbody\n"
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "review_agents.md").write_text(text, encoding="utf-8")
        saved = lens.AGENTS, lens.REGISTRIES
        lens.AGENTS = Path(tmp)
        lens.REGISTRIES = {"review": ("review_agents.md", ""), "structure_audit": ("structure_audit_agents.md", "stra-")}
        try:
            names = getattr(lens, "shipped", lambda: "no shipped()")()
            check("shipped() names only registries whose file exists", names == ["review"], names)
            code, out, err = run_cli(lens.cmd_get, ["code-reviewer"], False)
            check("get resolves a lens while another registry's file is absent",
                  code == 0 and "code-reviewer" in out, (code, err))
            saved_argv, sys.argv = sys.argv, ["lens.py", "index"]
            try:
                code, out, err = run_cli(lens.main)
            finally:
                sys.argv = saved_argv
            check("a bare index lists the shipped registries while another registry's file is absent",
                  code == 0 and "code-reviewer" in out, (code, err))
            code, _, err = run_cli(lens.parse, "structure_audit")
            check("parsing the absent registry by name still fails loudly", code != 0 and "not found" in err, (code, err))
        finally:
            lens.AGENTS, lens.REGISTRIES = saved


def rule_hash_checks():
    """review_ledger.rule_hash is the catalog's one hash, and lens carries no second name for it."""
    import review_ledger
    check("lens.py carries no rule_hash wrapper", not hasattr(lens, "rule_hash"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        checklist = root / "commands" / "checklists"
        checklist.mkdir(parents=True)
        body = "# Code Quality\n\n## Compliance (C)\n- c1\n\n## Design (D)\n- d1\n\n## Semantics (S)\n- s1\n"
        (checklist / "code_quality.md").write_text(body, encoding="utf-8")
        catalog = {
            "cq.C": {"home": "commands/checklists/code_quality.md#Compliance (C)", "interaction": False},
            "cq.D": {"home": "commands/checklists/code_quality.md#Design (D)", "interaction": False},
        }
        before = {k: review_ledger.rule_hash(k, root, catalog) for k in catalog}
        check("rule_hash returns a sha256 per catalog key",
              all(v and v.startswith("sha256:") for v in before.values()), before)
        (checklist / "code_quality.md").write_text(body.replace("- c1", "- c1 changed"), encoding="utf-8")
        after = {k: review_ledger.rule_hash(k, root, catalog) for k in catalog}
        check("a Compliance edit changes cq.C", after["cq.C"] != before["cq.C"])
        check("a Compliance edit leaves cq.D unchanged", after["cq.D"] == before["cq.D"])
        check("a key outside the catalog has no hash", review_ledger.rule_hash("nope", root, catalog) is None)


def main():
    registry = "review"
    filename, prefix = "review_agents.md", ""
    text = (
        "# Review agents\n\n## Shared Scoping Rules\n\n### Large PR Handling (>300 files)\nsplit it\n\n"
        "### Batch Sizing Rules\nsize it\n\n## Agent Templates\n\n"
        f"### {prefix}code-reviewer (aspect: `code`) -- `model: \"opus\"`\n\n## Your Checklist\n- a\n\n"
        f"### {prefix}pool-lifecycle (aspect: `pool`) -- `model: \"opus\"`\n\n## Your Checklist\n\n"
        "### Reset Ordering\n- b\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / filename).write_text(text, encoding="utf-8")
        saved = lens.AGENTS, lens.REGISTRIES
        lens.AGENTS, lens.REGISTRIES = Path(tmp), {registry: (filename, prefix)}
        try:
            preamble, lenses = lens.parse(registry)
        finally:
            lens.AGENTS, lens.REGISTRIES = saved
    keys = [item.key for item in lenses]
    check("only the template headings are lenses", keys == [f"{prefix}code-reviewer", f"{prefix}pool-lifecycle"], keys)
    check("the preamble keeps the shared sections", "Batch Sizing Rules" in preamble, preamble[-80:])
    check("a lens keeps its own subsections", "Reset Ordering" in lenses[-1].body() if lenses else False)
    preload_checks()
    mandate_checks()
    registry_checks()
    unshipped_registry_checks()
    rule_hash_checks()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
