#!/usr/bin/env python3
"""Re-runnable proof for hooks/design_surface_reminder.py.

The hook decides on the class's own members and on the repo: an edit that INTRODUCES an exported bool into a
file whose class already exports a slot typed by a family, a type the repo declares as abstract, an interface, or
a base of two or more types (design_litmus #1: a seam has a second implementation). No type name decides. Each
case plants the file as the edit left it on disk and the tool_response a Write or Edit returns (`originalFile` is
the file before the edit, null for a create; the shape was cut from a live transcript's tool results), inside a
scratch git repository whose tracked files declare the families.

    python3 .claude/tests/test_design_surface_reminder.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
HOOK = os.path.join(HOOKS, "design_surface_reminder.py")
sys.path.insert(0, HOOKS)
import design_surface_reminder as hook  # noqa: E402

TMP = tempfile.mkdtemp(prefix="design_surface_reminder_")

# What the scratch repository declares: one family of each shape, and types that are no family.
DECLARATIONS = {
    "Families.cs": (
        "public abstract partial class BaseMovementStrategy3D : Resource, IMovementStrategy3D { }\n"
        "public interface IGate { }\n"
        "public abstract class Handler : Node { }\n"
        "public partial class Pair : Resource { }\n"
        "public partial class PairA : Pair { }\n"
        "public partial class PairB : Pair, IGate { }\n"
        "public partial class Single : Resource { }\n"
        "public partial class SingleA : Single { }\n"
        "public partial class LoneStrategy : Resource { }\n"
        "public partial class Walker : Node3D { }\n"
        "public partial class Runner : Node3D { }\n"
        "public partial class AttackConfig : Resource { }\n"),
}


def _git(*args):
    subprocess.run(["git", *args], cwd=TMP, check=True, capture_output=True)


_git("init", "-q")
for _name, _text in DECLARATIONS.items():
    with open(os.path.join(TMP, _name), "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write(_text)
_git("add", ".")

SLOT = "    [Export] public BaseMovementStrategy3D? EndStrategy { get; private set; }\n"
FLAG = "    [Export] public bool UseHardStop = false;\n"
PLAIN = "    [Export] public float Speed = 1f;\n"
RESOURCE = "    [Export, RequiredExport] private AttackConfig _attackConfig = null!;\n"


def slot(type_, name="Slot"):
    return "    [Export] public %s %s { get; set; }\n" % (type_, name)


def cs(*members):
    return "public partial class Probe : Node\n{\n" + "".join(members) + "}\n"


def planted(name, after):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(after)
    return path


def edit(name, before, after, old, new):
    """A PostToolUse payload for an Edit that turned `before` into `after`."""
    path = planted(name, after)
    return {"tool_name": "Edit", "session_id": "probe0001",
            "tool_input": {"file_path": path, "old_string": old, "new_string": new},
            "tool_response": {"filePath": path, "oldString": old, "newString": new,
                              "originalFile": before, "replaceAll": False, "userModified": False}}


def write(name, before, after):
    """A PostToolUse payload for a Write; `before` None is a create."""
    path = planted(name, after)
    return {"tool_name": "Write", "session_id": "probe0001",
            "tool_input": {"file_path": path, "content": after},
            "tool_response": {"type": "create" if before is None else "update", "filePath": path,
                              "content": after, "originalFile": before, "userModified": False}}


def context(result):
    return (result or {}).get("context") or ""


def run_main(payload):
    r = subprocess.run([sys.executable, HOOK], input=payload if isinstance(payload, str) else json.dumps(payload),
                       capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def silent_outside_repo():
    outside = tempfile.mkdtemp(prefix="design_surface_reminder_norepo_")
    path = os.path.join(outside, "x.cs")
    after = cs(slot("Handler"), FLAG)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(after)
    payload = {"tool_name": "Write", "tool_input": {"file_path": path, "content": after},
               "tool_response": {"originalFile": cs(slot("Handler")), "filePath": path}}
    return hook.process(payload) is None


def main():
    cases = []
    before = cs(SLOT, PLAIN)
    after = cs(SLOT, PLAIN, FLAG)

    ctx = context(hook.process(edit("a.cs", before, after, PLAIN, PLAIN + FLAG)))
    cases.append(("an Edit that adds an exported bool beside a strategy slot fires the litmus",
                  "UseHardStop" in ctx and "EndStrategy" in ctx and "design_litmus" in ctx))
    cases.append(("a bool added to a class with no slot is silent",
                  hook.process(edit("b.cs", cs(PLAIN), cs(PLAIN, FLAG), PLAIN, PLAIN + FLAG)) is None))
    cases.append(("an Edit that leaves an existing bool beside a slot alone is silent",
                  hook.process(edit("c.cs", after, after.replace("= false", "= true"),
                                    "UseHardStop = false", "UseHardStop = true")) is None))
    cases.append(("a non-bool export added beside a strategy slot is silent",
                  hook.process(edit("d.cs", before, cs(SLOT, PLAIN, RESOURCE), PLAIN, PLAIN + RESOURCE)) is None))
    cases.append(("a bool that is not exported is silent beside a slot",
                  hook.process(edit("f.cs", before, cs(SLOT, PLAIN, "    private bool _armed;\n"),
                                    PLAIN, PLAIN + "    private bool _armed;\n")) is None))
    cases.append(("a Write that creates a class with a bool and a slot fires",
                  "UseHardStop" in context(hook.process(write("g.cs", None, cs(SLOT, FLAG))))))
    cases.append(("a Write that replaces a file with the same members is silent",
                  hook.process(write("h.cs", after, after + "// note\n")) is None))
    cases.append(("a Write that adds the bool to an existing file fires",
                  "UseHardStop" in context(hook.process(write("i.cs", before, after)))))
    cases.append(("the same introduction is not announced twice: the bool is then in the file before the edit",
                  hook.process(edit("j.cs", after, after, FLAG, FLAG)) is None))
    cases.append(("an export with attribute arguments and a nullable slot type is read as a member",
                  "UseHardStop" in context(hook.process(edit(
                      "k.cs", cs(SLOT), cs(SLOT, '    [Export(PropertyHint.None, "x")] public bool UseHardStop { get; set; }\n'),
                      SLOT, SLOT + "x")))))
    # The family is a fact the repo shows, never a name.
    def fires(type_, tag):
        ctx = context(hook.process(edit(tag + ".cs", cs(slot(type_)), cs(slot(type_), FLAG), PLAIN, PLAIN + FLAG)))
        return "UseHardStop" in ctx and "Slot" in ctx and type_.split("<")[-1].rstrip(">?[]") in ctx

    def silent(type_, tag):
        return hook.process(edit(tag + ".cs", cs(slot(type_)), cs(slot(type_), FLAG), PLAIN, PLAIN + FLAG)) is None

    cases.append(("a slot typed by an abstract class the repo declares is a family", fires("Handler", "fam1")))
    cases.append(("a slot typed by an interface the repo declares is a family", fires("IGate", "fam2")))
    cases.append(("a slot typed by a concrete base of two types is a family", fires("Pair", "fam3")))
    cases.append(("a nullable and a collection of a family are a family too",
                  fires("Handler?", "fam4") and fires("Godot.Collections.Array<Pair>", "fam5")))
    cases.append(("a concrete type with one derived type is no family", silent("Single", "nf1")))
    cases.append(("a concrete type nothing derives from is no family, whatever its name", silent("LoneStrategy", "nf2")))
    cases.append(("an engine type the repo does not declare is no family however many types derive from it",
                  silent("Node3D", "nf3")))
    cases.append(("a type name the repo never declares is silent, a Strategy suffix notwithstanding",
                  silent("UnknownStrategy", "nf4")))
    cases.append(("a class outside any git repository is silent, not guessed", silent_outside_repo()))
    # Shapes the earlier hook matched by text and this one does not decide on.
    cases.append(("a null strategy argument is not a member and is silent",
                  hook.process(edit("l.cs", "", "Run(strategy: null);\n", "", "Run(strategy: null);\n")) is None))
    cases.append(("an abstract rung is not decided on text and is silent",
                  hook.process(write("m.cs", None, "public abstract class Wide : BaseEffect { }\n")) is None))

    plain_md = write("n.md", None, cs(SLOT, FLAG))
    cases.append(("a non-.cs file never fires", hook.process(plain_md) is None))
    read = write("o.cs", None, cs(SLOT, FLAG))
    read["tool_name"] = "Read"
    cases.append(("a non-Write/Edit tool never fires", hook.process(read) is None))
    cases.append(("a Write with no originalFile key is silent, not guessed",
                  hook.process({**write("p.cs", None, cs(SLOT, FLAG)), "tool_response": {"filePath": "x"}}) is None))
    cases.append(("a file that is gone from disk is silent",
                  hook.process({**write("q.cs", None, cs(SLOT, FLAG)),
                                "tool_input": {"file_path": os.path.join(TMP, "gone.cs"), "content": "x"}}) is None))
    for label, bad in (("an empty payload", {}),
                       ("a non-dict tool_input", {"tool_name": "Write", "tool_input": "x"}),
                       ("a non-dict tool_response", {"tool_name": "Edit", "tool_input": {"file_path": "a.cs"},
                                                     "tool_response": "x"})):
        try:
            ok = hook.process(bad) is None
        except Exception:
            ok = False
        cases.append((label + " does not crash process()", ok))

    # The registered command: stdin payload to stdout on the model channel.
    rc, out, err = run_main(write("r.cs", None, cs(SLOT, FLAG)))
    if rc not in (0, 2) or "Traceback" in err:
        cases.append(("standalone channel: introduction (CRASH classification)", False))
    else:
        try:
            specific = json.loads(out).get("hookSpecificOutput") or {}
            ok = (rc == 0 and specific.get("hookEventName") == "PostToolUse"
                  and "UseHardStop" in (specific.get("additionalContext") or ""))
        except (json.JSONDecodeError, ValueError):
            ok = False
        cases.append(("standalone channel emits the reminder as PostToolUse additionalContext", ok))
    rc, out, err = run_main(write("s.cs", None, cs(PLAIN)))
    cases.append(("standalone channel is silent for a clean file", rc == 0 and out in ("", "{}") and not err))
    rc, out, err = run_main("not json at all")
    cases.append(("standalone channel exits 0 with no output on malformed stdin",
                  rc == 0 and out in ("", "{}") and "Traceback" not in err))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
