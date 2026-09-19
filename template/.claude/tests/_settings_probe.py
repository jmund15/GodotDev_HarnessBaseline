"""Resolve the settings a proof should assert against, in a consumer OR in the template.

A consumer carries a composed `.claude/settings.json`. The baseline template carries only
`settings.base.json` + `settings.project.json` and composes at install time (S8), so a proof that
opens `settings.json` directly cannot run upstream -- it fails with FileNotFoundError inside the
publication's battery, which is how eleven proofs went red the first time this project published
them.

`settings_path(claude_dir)` returns the composed file when one exists, and otherwise composes the
base and project layers into a temp file and returns that. The temp file lives for the process, so
a caller may hand the path to a subprocess.
"""
import json
import sys
import tempfile
from pathlib import Path

ALL_LAYERS = ["pure", "coding", "godot"]
_CACHE: dict[str, str] = {}


def _compose(claude_dir: Path) -> dict:
    tools = claude_dir / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import baseline_compose

    base = json.loads((claude_dir / "settings.base.json").read_text(encoding="utf-8"))
    project_file = claude_dir / "settings.project.json"
    project = json.loads(project_file.read_text(encoding="utf-8")) if project_file.is_file() else {}
    return baseline_compose.compose_settings(base, project, claude_dir / "hooks", ALL_LAYERS)


def settings_path(claude_dir) -> str:
    """Path to a composed settings document: the real one, or a composed temp copy."""
    claude_dir = Path(claude_dir)
    composed = claude_dir / "settings.json"
    if composed.is_file():
        return str(composed)
    key = str(claude_dir)
    if key not in _CACHE:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix="-settings.json", delete=False, encoding="utf-8", newline="\n")
        with handle:
            json.dump(_compose(claude_dir), handle, indent=2)
        _CACHE[key] = handle.name
    return _CACHE[key]


def settings(claude_dir) -> dict:
    """The composed settings document itself."""
    return json.loads(Path(settings_path(claude_dir)).read_text(encoding="utf-8"))
