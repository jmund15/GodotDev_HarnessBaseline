#!/usr/bin/env python3
"""
audit_baseline.py -- separation-health audit for the harness baseline itself.

This is the maintainer-side counterpart to baseline_sync.py (which manages
*consumer* drift). It verifies that template/ stays project-agnostic and that the
manifest stays an honest map of what's on disk, so the leaks a one-time manual
review catches get caught on every future change instead.

Checks (each emits findings with a severity):
  manifest-integrity   manifest entries <-> disk files agree (orphans / phantoms)
  manifest-staleness    on-disk manifest == what gen_manifest.py would emit now
  identity-scan         source-project identifiers / abbreviations / concatenations /
                        home paths / topology tokens, via baseline_identity.scan_tree
                        against tools/identity_digests.json (see that module's docstring)
  secret-scan           token/key/credential shapes in template/
  layer-gate            pure files naming >=4 godot/coding markers; coding files
                        naming >=4 godot markers (advisory archetype-appropriateness)

Severities: ERROR (publish blocker, exit 1), WARN (review before publish, exit 0),
INFO (advisory). Run from the baseline repo root:  python3 tools/audit_baseline.py
Pass --json for machine output, --strict to fail the run on WARN as well.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys

# Windows consoles default to cp1252; findings may quote arbitrary template bytes.
sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")
from pathlib import Path

# Reuse the generator's classification so audit and gen_manifest can never disagree
# about which files belong to which layer.
import gen_manifest as gm

ROOT = gm.ROOT
TEMPLATE = gm.TEMPLATE
MANIFEST = ROOT / "baseline.manifest.json"
IDENTITY_DIGESTS = ROOT / "tools" / "identity_digests.json"

# The source-project/abbreviation/concatenation/home-path/topology scan lives in the
# template's baseline_identity.py, so a consumer pulls the same engine this audit
# runs — see that module's docstring for the profile/digest split.
_IDENTITY_PATH = ROOT / "template" / ".claude" / "tools" / "baseline_identity.py"
_identity_spec = importlib.util.spec_from_file_location("baseline_identity", _IDENTITY_PATH)
identity = importlib.util.module_from_spec(_identity_spec)
_identity_spec.loader.exec_module(identity)

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # $-leading values are shell variable expansions, not literals — skip them.
    re.compile(r"""(?:password|secret|api[_-]?key|token)\s*[:=]\s*["'][^"'{<$\s]{8,}["']""", re.I),
]
# Concrete Godot/Jmodot-stack identifiers. A *pure*- or *coding*-tagged file
# naming several distinct ones is probably substantively godot-specific and
# would wrongly be consumed by a non-Godot profile. Generic single mentions (a
# tool-routing example naming "Blackboard" once) are not the target -- hence the
# distinct-token threshold below.
GODOT_MARKERS = [
    r"\bGodot\b", r"\bGdUnit4?\b", r"\.tscn\b", r"\.tres\b",
    r"\bBBDataSig\b", r"\bJmoLogger\b", r"\bMovementProcessor\w*\b",
    r"\bEntityStatSheet\b", r"\bCombatFactor\w*\b", r"\bBlackboard\b",
    r"\bBehaviorTree\b", r"\bIComponent\b", r"\bEntityStatModifier\b",
]
# Concrete *programming* identifiers (beyond content production). A *pure*-tagged
# file naming several distinct ones is probably substantively coding-specific.
CODING_MARKERS = [
    r"\bC#\b", r"\bcsharp\b", r"\bnamespace\b", r"\bdotnet\b",
    r"\.cs\b", r"\bcsproj\b", r"\bNuGet\b", r"\bsubmodule\b",
]
LAYER_GATE_MIN_DISTINCT = 4
# Domain nouns that must not appear in a *core*-tagged file: core is consumed by
# non-code content-production projects, so code/engine vocabulary is a mistag
# signal there the same way Jmodot types are for universal. Content-side nouns
# (YouTube etc.) are listed too — core must not absorb a consumer's domain either.
CORE_DOMAIN_TOKENS = [
    r"\bGodot\b", r"\bdotnet\b", r"\bGdUnit4?\b", r"\.tscn\b", r"\.tres\b",
    r"\bC#\b", r"\bcsharp\b", r"\bnamespace\b", r"\bsubmodule\b",
    r"\bYouTube\b", r"\bdevlog\b", r"\bDaVinci\b",
]
CORE_DOMAIN_MIN_DISTINCT = 2
CORE_DOMAIN_ALLOWLIST = {
    ".claude/CLAUDE.md",            # seed: consumer replaces domain sections
    ".claude/CLAUDE.core.md",       # universal doctrine: cites Godot/C# build commands as examples
    ".claude/settings.json",        # seed: wiring layered at bootstrap
    ".claude/settings.base.json",   # tracked: wiring layered at bootstrap (was settings.json)
    ".claude/auto-memory/MEMORY.md",
    # Core memories whose *evidence* sections cite source-domain incidents.
    # The rule text is generic; evidence stays verbatim by memory convention.
    ".claude/auto-memory/feedback_recommended_fix_means_implement.md",
    ".claude/auto-memory/feedback_session_end_full_scope.md",
    ".claude/auto-memory/feedback_verify_explore_agent_empirical_claims.md",
    ".claude/auto-memory/feedback_verify_plan_integration_target_is_live.md",
    # Known adaptation points (README) — mechanism core, source-domain nouns
    # appear only as inline examples. Genericize opportunistically, not by fiat.
    ".claude/commands/agents/orchestrator_action_protocol.md",
    ".claude/commands/autolearn.md",
    ".claude/commands/reindex_search.md",
    # Universal hooks whose Godot resource suffixes are inert where no such files exist:
    # git_guardrails refuses a one-sided checkout of .tscn/.tres during a merge.
    ".claude/hooks/git_guardrails.py",
    # reap.py recognizes MCP and language-server processes by executable name (dotnet, csharp-ls).
    ".claude/tools/reap.py",
    ".claude/tests/test_git_guardrails_advice.py",
    # Two-shape lens set (harness/doctrine AND code plans): classified pure per
    # the archetype-home rule, but its plan-check targets are code/engine nouns.
    ".claude/commands/plan_check.md",
    ".claude/skills/instruction_quality/SKILL.md",
    ".claude/skills/parallel_agents/SKILL.md",
    ".claude/workflows/review_fanout.js",
}
# Universal doctrine that legitimately discusses framework types as examples.
LAYER_MISTAG_ALLOWLIST = {
    ".claude/CLAUDE.md",
    ".claude/CLAUDE.core.md",
    ".claude/auto-memory/MEMORY.md",
    ".claude/skills/architecture_philosophy/SKILL.md",
}


class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, severity: str, check: str, path: str, detail: str) -> None:
        self.items.append({"severity": severity, "check": check,
                           "path": path, "detail": detail})

    def by_severity(self, sev: str) -> list[dict]:
        return [f for f in self.items if f["severity"] == sev]


def iter_template_files():
    # Stable cross-platform order (see gen_manifest: Path sort is OS-case-dependent).
    for p in sorted(TEMPLATE.rglob("*"), key=lambda x: x.relative_to(TEMPLATE).as_posix()):
        if not p.is_file() or p.name == ".gitkeep" or gm.is_artifact(p):
            continue  # gm.is_artifact: __pycache__/.pyc/logs/.cache/session-state
        yield p, p.relative_to(TEMPLATE).as_posix()


def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def check_manifest_integrity(f: Findings) -> dict:
    if not MANIFEST.exists():
        f.add("ERROR", "manifest-integrity", str(MANIFEST.name),
              "manifest missing -- run tools/gen_manifest.py")
        return {}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mset = {e["path"] for e in manifest["files"]}
    disk = {rel for _, rel in iter_template_files()}
    for orphan in sorted(disk - mset):
        f.add("WARN", "manifest-integrity", orphan,
              "on disk but not in manifest -- regenerate the manifest")
    for phantom in sorted(mset - disk):
        f.add("ERROR", "manifest-integrity", phantom,
              "in manifest but not on disk -- stale entry")
    return manifest


def check_manifest_staleness(f: Findings, manifest: dict) -> None:
    if not manifest:
        return
    on_disk = {e["path"]: (e["layer"], e.get("sync")) for e in manifest["files"]}
    for _, rel in iter_template_files():
        if rel not in on_disk:
            continue  # already reported by integrity check
        layer = gm.classify(rel)
        if layer is None:
            f.add("ERROR", "manifest-staleness", rel,
                  "unclassified by layer patterns -- add to a pattern list")
            continue
        sync = "seed" if gm.match(rel, gm.SEED_PATTERNS) else "auto"
        if on_disk[rel] != (layer, sync):
            f.add("ERROR", "manifest-staleness", rel,
                  f"manifest says {on_disk[rel]}, patterns say {(layer, sync)} "
                  "-- regenerate the manifest")


def check_secrets(f: Findings) -> None:
    for p, rel in iter_template_files():
        text = read_text(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for sp in SECRET_PATTERNS:
                if sp.search(line):
                    f.add("ERROR", "secret-scan", f"{rel}:{i}",
                          "possible secret/credential -- do not publish")


def check_identity(f: Findings) -> None:
    """Source-project/abbreviation/concatenation/home-path/topology leaks,
    via the shared consumer-side scanner (baseline_identity.scan_tree)."""
    try:
        hits = identity.scan_tree(ROOT, IDENTITY_DIGESTS)
    except identity.IdentityError as exc:
        f.add("ERROR", "identity-scan", IDENTITY_DIGESTS.name, str(exc))
        return
    for hit in hits:
        f.add("ERROR", "identity-scan", f"{hit.path}:{hit.line}",
              f"{hit.token_kind} match ({hit.id}): {hit.excerpt[:120]}")


def check_layer_mistag(f: Findings, manifest: dict) -> None:
    """Archetype-gate: a file must not substantively name a *narrower* archetype.

    pure files must not name >= 4 godot markers NOR >= 4 coding markers.
    coding files must not name >= 4 godot markers.
    All advisory (INFO): the classification rule's canonical home is sync_baseline.md.
    """
    if not manifest:
        return
    layer_of = {e["path"]: e["layer"] for e in manifest["files"]}
    for p, rel in iter_template_files():
        layer = layer_of.get(rel)
        if layer not in ("pure", "coding") or rel in LAYER_MISTAG_ALLOWLIST:
            continue
        text = read_text(p)
        if text is None:
            continue
        g_hits = sorted({t for t in GODOT_MARKERS if re.search(t, text)})
        c_hits = sorted({t for t in CODING_MARKERS if re.search(t, text)})
        if layer == "pure":
            if len(g_hits) >= LAYER_GATE_MIN_DISTINCT:
                f.add("INFO", "layer-gate", rel,
                      f"pure-tagged but names {len(g_hits)} godot markers "
                      f"({', '.join(g_hits)}) -- confirm stack-agnostic or move to godot")
            if len(c_hits) >= LAYER_GATE_MIN_DISTINCT:
                f.add("INFO", "layer-gate", rel,
                      f"pure-tagged but names {len(c_hits)} coding markers "
                      f"({', '.join(c_hits)}) -- confirm content-agnostic or move to coding")
        elif layer == "coding":
            if len(g_hits) >= LAYER_GATE_MIN_DISTINCT:
                f.add("INFO", "layer-gate", rel,
                      f"coding-tagged but names {len(g_hits)} godot markers "
                      f"({', '.join(g_hits)}) -- confirm engine-agnostic or move to godot")


def check_core_domain_nouns(f: Findings, manifest: dict) -> None:
    if not manifest:
        return
    layer_of = {e["path"]: e["layer"] for e in manifest["files"]}
    for p, rel in iter_template_files():
        if layer_of.get(rel) != "pure" or rel in CORE_DOMAIN_ALLOWLIST:
            continue
        text = read_text(p)
        if text is None:
            continue
        hits = sorted({tok for tok in CORE_DOMAIN_TOKENS if re.search(tok, text)})
        if len(hits) >= CORE_DOMAIN_MIN_DISTINCT:
            names = ", ".join(t.replace(r"\b", "") for t in hits)
            f.add("WARN", "core-domain-noun", rel,
                  f"pure-tagged but names {len(hits)} domain tokens ({names}) -- "
                  "extract to an adaptation point or demote a layer")


def check_layer_closure(f: Findings, manifest: dict, template: Path = TEMPLATE) -> None:
    """A file of layer X reaches consumers holding only X and the layers below it, so everything it
    cites, imports or runs must ship at X or lower. The scanner is the template's own tool, the one
    consumers run over their locks."""
    if not manifest:
        return
    tools = str(template / ".claude" / "tools")
    sys.path.insert(0, tools)
    try:
        import layer_closure
    finally:
        sys.path.remove(tools)
    for finding in layer_closure.scan(template, layer_closure.manifest_layers(manifest)):
        f.add("ERROR", "layer-closure", finding.source,
              f"{finding.kind} {finding.target} -- a consumer of this file's layer does not receive "
              "the target: move the sentence to the target's layer, reword it to a lower-layer seam, "
              "re-layer a file, or load the module through hooks/_optional_hooks.py")


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit the harness baseline for clean separation.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit nonzero on WARN as well as ERROR")
    args = ap.parse_args()

    if not TEMPLATE.is_dir():
        sys.exit(f"error: {TEMPLATE} not found -- run from the baseline repo root")

    f = Findings()
    manifest = check_manifest_integrity(f)
    check_manifest_staleness(f, manifest)
    check_secrets(f)
    check_identity(f)
    check_layer_mistag(f, manifest)
    check_core_domain_nouns(f, manifest)
    check_layer_closure(f, manifest)

    errors, warns, infos = (f.by_severity(s) for s in ("ERROR", "WARN", "INFO"))

    if args.json:
        print(json.dumps({"errors": len(errors), "warns": len(warns),
                          "infos": len(infos), "findings": f.items}, indent=2))
    else:
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        for item in sorted(f.items, key=lambda x: (order[x["severity"]], x["check"], x["path"])):
            print(f"[{item['severity']:5}] {item['check']:20} {item['path']}\n"
                  f"         {item['detail']}")
        total = len(f.items)
        print(f"\n{len(errors)} error(s), {len(warns)} warn(s), {len(infos)} info(s) "
              f"across {total} finding(s)." if total else "clean -- no separation findings.")

    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
