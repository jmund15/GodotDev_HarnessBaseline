"""A planted bench plugin for consumer proofs: a temp root holding the adaptation switch, a registration and a
recording stub entry built from `adaptation.ENTRY_CALLS`.

Each stub call logs its name to `accessed.log` and answers `returns.json[<call>]`; a name outside the row is logged
by the module `__getattr__` and raises, so a consumer that reaches past the row shows up in `accessed()`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import adaptation  # noqa: E402

_HEAD = r'''import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _note(name):
    with open(os.path.join(HERE, "accessed.log"), "a", encoding="utf-8") as fh:
        fh.write(name + "\n")


def _answer(call):
    _note(call)
    with open(os.path.join(HERE, "returns.json"), encoding="utf-8") as fh:
        value = json.load(fh)[call]
    if isinstance(value, dict) and "_raise" in value:
        raise RuntimeError(value["_raise"])
    return value


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    _note(name)
    raise AttributeError(name)
'''


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def plant(root, returns=None, name="bench", **registration):
    """Plant a ready plugin under `root` (`plugins.<name>` = "."); return its `.claude` directory.
    `returns` maps call name to the JSON value the stub answers; `registration` overrides registration keys."""
    root = Path(root)
    claude = root / ".claude"
    _write(claude / "skills/project_subsystems/adaptation.json", json.dumps({"plugins": {name: "."}}))
    body = _HEAD + "".join("\n\ndef %s(%s):\n    return _answer(%r)\n" % (call, ", ".join(params), call)
                           for call, params in adaptation.ENTRY_CALLS[name].items())
    _write(claude / "stub_entry.py", body)
    manifest = {"schema": 1, "core_root": ".", "entry": ".claude/stub_entry.py", "sidecar_hooks": [],
                "measurement_workflows": [], "usage_markers": []}
    _write(claude / ("plugin.%s.json" % name), json.dumps(dict(manifest, **registration)))
    _write(claude / "returns.json", json.dumps(returns or {}))
    (claude / "accessed.log").unlink(missing_ok=True)
    return claude


def plant_absent(root):
    """A `.claude` directory whose adaptation configures no plugin."""
    claude = Path(root) / ".claude"
    _write(claude / "skills/project_subsystems/adaptation.json", "{}")
    return claude


def plant_unreadable(root, name="bench"):
    """A `.claude` directory that configures the plugin and holds no registration for it."""
    claude = Path(root) / ".claude"
    _write(claude / "skills/project_subsystems/adaptation.json", json.dumps({"plugins": {name: "."}}))
    return claude


def accessed(claude, name="bench"):
    """The set of entry names the consumer touched."""
    log = Path(claude) / "accessed.log"
    return set(log.read_text(encoding="utf-8").split()) if log.exists() else set()


def within_row(claude, name="bench"):
    """(touched something, touched nothing outside the ENTRY_CALLS row)."""
    touched = accessed(claude)
    return bool(touched) and touched <= set(adaptation.ENTRY_CALLS[name])
