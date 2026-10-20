"""Proof for tools/type_decls.py: one `declared_types(line)` for C# and GDScript type declarations.

A declaration is anchored at the start of the code after attributes and modifiers, so comment lines,
doc prose and `where T : class` constraints never yield a name, and `record struct Foo` yields `Foo`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "type_decls.py"


def _load():
    spec = importlib.util.spec_from_file_location("type_decls_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plain_declarations_yield_their_name():
    declared = _load().declared_types
    assert declared("public class Widget : Node") == ["Widget"]
    assert declared("    internal sealed partial class Gadget") == ["Gadget"]
    assert declared("public enum Mode { A, B }") == ["Mode"]
    assert declared("public interface IThing") == ["IThing"]
    assert declared("public readonly struct Vec") == ["Vec"]
    assert declared("public static class Helpers") == ["Helpers"]


def test_attributes_and_generic_arity_do_not_hide_the_name():
    declared = _load().declared_types
    assert declared("[Tool] [GlobalClass] public partial class Spell : Resource") == ["Spell"]
    assert declared("public class Box<T> where T : class") == ["Box"]


def test_record_class_and_record_struct_capture_the_name_not_the_keyword():
    declared = _load().declared_types
    assert declared("public record struct Foo(int X);") == ["Foo"]
    assert declared("public readonly record struct Bar(int X);") == ["Bar"]
    assert declared("public sealed record class Baz(int X);") == ["Baz"]
    assert declared("public record Qux(int X);") == ["Qux"]


def test_gdscript_class_name_and_inner_class_are_kept():
    declared = _load().declared_types
    assert declared("class_name Wizard extends Node") == ["Wizard"]
    assert declared("class Inner:") == ["Inner"]


def test_comment_and_prose_lines_never_match():
    declared = _load().declared_types
    assert declared("// remove this class once X ships") == []
    assert declared("/// Base class for spells") == []
    assert declared("    * a record struct is a value type") == []
    assert declared("# Base class for spells") == []
    assert declared("var x = typeof(class)") == []
    assert declared("") == []


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                failed += 1
                print("FAIL", name, exc)
    sys.exit(1 if failed else 0)
