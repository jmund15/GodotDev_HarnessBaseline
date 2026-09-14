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
  leak-scan             source-project identifiers / machine paths in template/
  secret-scan           token/key/credential shapes in template/
  layer-gate            pure files naming >=4 godot/coding markers; coding files
                        naming >=4 godot markers (advisory archetype-appropriateness)

Severities: ERROR (publish blocker, exit 1), WARN (review before publish, exit 0),
INFO (advisory). Run from the baseline repo root:  python3 tools/audit_baseline.py
Pass --json for machine output, --strict to fail the run on WARN as well.
"""
from __future__ import annotations

import argparse
import hashlib
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

# Forbidden source identities are stored as digests so the audit can reject them without
# republishing the names. Candidates include whole identifiers, alphabetic segments, and
# adjacent segments joined together; this catches concatenated and spaced project names.
FORBIDDEN_IDENTIFIER_DIGESTS = {
    "f80a62ca784fb78bbf2b993e6e8393357cf28dcd9dfab141ff884d206796863e": "source project",
    "a563c232fbb0b301619080406031efb8348430f4e20bad1fa51c451590039417": "source project",
    "bd94e8955161fc6f34dc94080d4a6425286b71ee4e94dd7848bcc448d1b84772": "source contributor",
}
FORBIDDEN_ABBREVIATION_DIGEST = (
    "d53315bea08cec50d2591fcaf3b32dc5d289cdc6c16b7e8bed8c8e3f7ceaa34e"
)
# Reject concrete home paths while allowing teaching shapes and shell variables.
MACHINE_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]Users[\\/]|/(?:[A-Za-z]/)?Users/)"
    r"(?!\.{3}|\{\{[^}]+\}\}|<[^>]+>|"
    r"(?:USER|you|x)(?:[\\/]|$)|\$[A-Za-z_][A-Za-z0-9_]*)"
    r"[^\\/\s\"']+",
    re.I,
)

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
    ".claude/settings.json",        # seed: wiring layered at bootstrap
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


def identifier_leaks(line: str) -> set[str]:
    segments = re.findall(r"[A-Za-z]+", line)
    candidates = {segment.lower() for segment in segments}
    candidates.update(
        (segments[i] + segments[i + 1]).lower()
        for i in range(len(segments) - 1)
    )
    digests = {
        hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        for candidate in candidates
    }
    leaks = {
        label for digest, label in FORBIDDEN_IDENTIFIER_DIGESTS.items()
        if digest in digests
    }
    for match in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z]{2})(?![A-Za-z0-9])", line):
        raw = match.group(1)
        if raw != raw.upper() and not (
            match.start() > 0 and line[match.start() - 1] in "_-"
            or match.end() < len(line) and line[match.end()] in "_-"
        ):
            continue
        digest = hashlib.sha256(raw.lower().encode("utf-8")).hexdigest()
        if digest == FORBIDDEN_ABBREVIATION_DIGEST:
            leaks.add("source-project abbreviation")
    return leaks


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


def check_leaks_and_secrets(f: Findings) -> None:
    for p, rel in iter_template_files():
        text = read_text(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for leak in sorted(identifier_leaks(line)):
                f.add("ERROR", "leak-scan", f"{rel}:{i}",
                      f"{leak} identifier: {line.strip()[:120]}")
            if MACHINE_PATH.search(line):
                f.add("WARN", "leak-scan", f"{rel}:{i}",
                      f"machine-specific path: {line.strip()[:120]}")
            for sp in SECRET_PATTERNS:
                if sp.search(line):
                    f.add("ERROR", "secret-scan", f"{rel}:{i}",
                          "possible secret/credential -- do not publish")


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
    check_leaks_and_secrets(f)
    check_layer_mistag(f, manifest)
    check_core_domain_nouns(f, manifest)

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
