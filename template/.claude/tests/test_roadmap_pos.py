"""Proof cases for tools/roadmap_pos.py — Pos computed from Deps per roadmap_schema.md §6.2 *Pos*."""
import os
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "roadmap_pos.py")
sys.path.insert(0, os.path.dirname(TOOL))

ROADMAP = """---
last_revised: 2026-10-24
---

# Demo — Roadmap

## Parts

| Pos | Part | State | Deps | Trigger | Source |
| --- | --- | --- | --- | --- | --- |
| 1 | Core, Data & Save | arch-pending | — | — | x |
|   | ─── unsequenced ─── |   |   |   |   |
| — | MVP 1 | complete | — | — | x |
| — | Board | arch-pending | Core, Data & Save | — | x |
| 9 | Polish | arch-pending | Board | — | x |
| — | Later Idea | idea-pending | — | someday | x |
| — | Needs Idea | arch-pending | Later Idea | — | x |

## Current state — Mermaid

```mermaid
graph TD
    A["1: Core, Data & Save"]:::arch
    B["Board"]:::arch
    M["MVP 1"]:::complete
```

## Revision Log
- 2026-10-24 — x
"""


def run(path, *flags):
    out = subprocess.run([sys.executable, TOOL, path, *flags], capture_output=True, text=True)
    return out.returncode, out.stdout


def pos_of(text):
    import roadmap_pos
    _lines, _s, _e, _idx, rows = roadmap_pos.parse(text)
    return {r["part"]: r["pos"] for r in rows if not r.get("divider")}


def test_check_apply_and_idempotence():
    with tempfile.TemporaryDirectory(prefix="roadmap_pos_") as root:
        path = os.path.join(root, "roadmap.md")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(ROADMAP)
        rc, out = run(path)
        assert rc == 1, out
        rc, out = run(path, "--apply")
        assert rc == 0, out
        text = open(path, encoding="utf-8").read()
        got = pos_of(text)
        # A complete Part with no Pos is placed first: finished work reads first.
        assert got["MVP 1"] == 1, got
        # Open work follows; Board follows its Dep (comma-holding name parsed whole).
        assert got["Core, Data & Save"] == 2 and got["Board"] == 3, got
        assert got["Polish"] == 4, got
        # idea-pending is un-sequenced, and so is a Part that depends on it.
        assert got["Later Idea"] is None and got["Needs Idea"] is None, got
        assert '["3: Board"]' in text and '["1: MVP 1"]' in text, text
        table = text.split("## Parts")[1].split("## Current")[0]
        assert table.index("Polish") < table.index("unsequenced") < table.index("Later Idea"), table
        rc, out = run(path)
        assert rc == 0 and "matches" in out, out


def test_complete_part_reads_first():
    import roadmap_pos
    text = ROADMAP.replace("| — | MVP 1 | complete |", "| 1 | MVP 1 | complete |").replace(
        "| 1 | Core, Data & Save |", "| — | Core, Data & Save |")
    result, _ = roadmap_pos.render(text)
    got = pos_of(result)
    assert got["MVP 1"] == 1 and got["Core, Data & Save"] == 2 and got["Board"] == 3, got


def test_a_part_depending_on_a_complete_part_without_pos_is_placed():
    import roadmap_pos
    text = ROADMAP.replace("| — | Board | arch-pending | Core, Data & Save |",
                           "| — | Board | arch-pending | MVP 1, Core, Data & Save |")
    got = pos_of(roadmap_pos.render(text)[0])
    assert got["MVP 1"] == 1 and got["Board"] == 3, got


def test_a_later_pos_is_kept_for_priority():
    import roadmap_pos
    # Polish and Extra share Board's floor; Polish sits earlier in the table but at a later Pos.
    text = ROADMAP.replace("| — | Later Idea |", "| — | Extra | arch-pending | Board | — | x |\n| — | Later Idea |")
    got = pos_of(roadmap_pos.render(text)[0])
    assert got["Extra"] < got["Polish"], got


def test_unsequenced_states_and_their_dependents():
    import roadmap_pos
    for state in ("workshop-pending", "abandoned"):
        got = pos_of(roadmap_pos.render(ROADMAP.replace("| Later Idea | idea-pending |",
                                                        "| Later Idea | %s |" % state))[0])
        assert got["Later Idea"] is None and got["Needs Idea"] is None, (state, got)


def test_a_malformed_table_exits_two_without_writing():
    bad = {
        "no table": ROADMAP.split("| Pos |")[0] + "\n## Revision Log\n",
        "short row": ROADMAP.replace("| 9 | Polish | arch-pending | Board | — | x |", "| 9 | Polish |"),
    }
    with tempfile.TemporaryDirectory(prefix="roadmap_pos_") as root:
        for name, text in bad.items():
            path = os.path.join(root, "roadmap.md")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            rc, out = run(path, "--apply")
            assert rc == 2 and "roadmap_pos:" in out and "Traceback" not in out, (name, out)
            assert open(path, encoding="utf-8").read() == text, name


def test_labels_change_only_inside_the_mermaid_fence():
    import roadmap_pos
    text = ROADMAP.replace("## Revision Log", 'Prose names ["Board"] here.\n\n## Revision Log')
    result = roadmap_pos.render(text)[0]
    assert 'Prose names ["Board"] here.' in result and '["3: Board"]' in result, result


def test_a_short_divider_row_is_still_a_divider():
    import roadmap_pos
    text = ROADMAP.replace("|   | ─── unsequenced ─── |   |   |   |   |", "| | ─── unsequenced ─── |")
    got = pos_of(roadmap_pos.render(text)[0])
    assert got["MVP 1"] == 1 and got["Later Idea"] is None, got


def test_a_cross_folder_dep_adds_no_constraint():
    import roadmap_pos
    text = ROADMAP.replace("| — | Board | arch-pending | Core, Data & Save |",
                           "| — | Board | arch-pending | ../other/roadmap.md#Engine, Core, Data & Save |")
    got = pos_of(roadmap_pos.render(text)[0])
    assert got["Board"] == 3, got


def test_a_pos_below_its_dep_is_raised():
    import roadmap_pos
    text = ROADMAP.replace("| 9 | Polish |", "| 1 | Polish |")
    got = pos_of(roadmap_pos.render(text)[0])
    assert got["Polish"] > got["Board"], got


def test_a_deps_cycle_refuses():
    with tempfile.TemporaryDirectory(prefix="roadmap_pos_") as root:
        path = os.path.join(root, "roadmap.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ROADMAP.replace("| 1 | Core, Data & Save | arch-pending | — |",
                                     "| 1 | Core, Data & Save | arch-pending | Polish |"))
        rc, out = run(path)
        assert rc == 2 and "cycle" in out, out


def test_no_argument_prints_usage():
    out = subprocess.run([sys.executable, TOOL], capture_output=True, text=True)
    assert out.returncode == 2 and "roadmap_pos.py" in out.stdout, out.stdout


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001 - proof runner reports every case
                fails += 1
                print("FAIL", name, "-", type(exc).__name__, str(exc)[:400])
    sys.exit(1 if fails else 0)
