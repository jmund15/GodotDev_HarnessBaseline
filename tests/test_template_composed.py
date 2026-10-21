#!/usr/bin/env python3
"""Re-runnable proof (baseline-repo root): every composed output the template ships equals its render.

    python3 tests/test_template_composed.py

`baseline_sync.py compose --template` renders each shipped composed output (the memory-domains table, and
`settings.json` when shipped) from the template's shipped inputs with every layer adopted. The template has
no consumer lock and its edit guard refuses a hand edit of a composed copy, so this render is the only way
the copy changes, and this proof is what notices when an input changed without it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template"
SYNC = Path(".claude") / "tools" / "baseline_sync.py"
MEMORY_DOMAINS = Path(".claude") / "reference" / "memory_domains.md"

if not (TEMPLATE / SYNC).is_file():
    print("CANNOT-RUN: %s is missing" % (TEMPLATE / SYNC), file=sys.stderr)
    sys.exit(2)


def _compose(template: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(template / SYNC), "compose", "--template", *flags], cwd=template,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(template.parent)},
    )


def _copy_template() -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="template_composed_"))
    copy = tmp / "template"
    shutil.copytree(TEMPLATE, copy, ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", ".cache"))
    return tmp, copy


def test_shipped_composed_outputs_equal_their_render() -> None:
    result = _compose(TEMPLATE, "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_drifted_copy_fails_check_and_the_render_repairs_it() -> None:
    tmp, copy = _copy_template()
    try:
        target = copy / MEMORY_DOMAINS
        assert _compose(copy).returncode == 0
        rendered = target.read_bytes()
        target.write_bytes(rendered + b"| planted | row |\n")
        drifted = _compose(copy, "--check")
        assert drifted.returncode == 1 and "drift: %s" % MEMORY_DOMAINS.as_posix() in drifted.stdout, \
            drifted.stdout + drifted.stderr
        repaired = _compose(copy)
        assert repaired.returncode == 0 and target.read_bytes() == rendered, repaired.stdout + repaired.stderr
        assert _compose(copy, "--check").returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_render_follows_the_adaptation_input() -> None:
    tmp, copy = _copy_template()
    try:
        adaptation = copy / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        data = json.loads(adaptation.read_text(encoding="utf-8"))
        data["memory_domains"] = [{"name": "ZZ-Probe", "triggers": ["zzprobe"], "memory_keywords": [], "skills": [], "rules": []}] \
            + list(data.get("memory_domains", []))
        adaptation.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        result = _compose(copy)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ZZ-Probe" in (copy / MEMORY_DOMAINS).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_layer_skill_grants_sit_in_their_layer_overlay_only() -> None:
    manifest = {item["path"]: item["layer"] for item in json.loads(
        (ROOT / "baseline.manifest.json").read_text(encoding="utf-8"))["files"]}
    claude = TEMPLATE / ".claude"

    def grants(name: str) -> list[str]:
        doc = json.loads((claude / name).read_text(encoding="utf-8"))
        return [e[len("Skill("):].split(":")[0].rstrip(")") for e in doc["permissions"]["allow"]
                if e.startswith("Skill(")]

    def layer_of(skill: str) -> str | None:
        for relpath in (".claude/skills/%s/SKILL.md" % skill, ".claude/commands/%s.md" % skill):
            if relpath in manifest:
                return manifest[relpath]
        return None

    for layer in ("coding", "godot"):
        in_base = sorted(g for g in set(grants("settings.base.json")) if layer_of(g) == layer)
        assert not in_base, "%s-layer Skill grants still in settings.base.json: %s" % (layer, in_base)
        overlay = "settings.%s.json" % layer
        off_layer = sorted(g for g in set(grants(overlay)) if layer_of(g) != layer)
        assert not off_layer, "%s holds grants whose owner is not %s-layer: %s" % (overlay, layer, off_layer)
        assert grants(overlay), "%s carries no Skill grants" % overlay


def main() -> int:
    cases = [
        test_shipped_composed_outputs_equal_their_render,
        test_a_drifted_copy_fails_check_and_the_render_repairs_it,
        test_the_render_follows_the_adaptation_input,
        test_layer_skill_grants_sit_in_their_layer_overlay_only,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
