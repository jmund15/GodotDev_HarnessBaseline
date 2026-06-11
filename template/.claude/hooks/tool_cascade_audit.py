#!/usr/bin/env python3
"""
Tool Cascade Audit — static analyzer + gate for the Godot `[Tool]` attribute cascade.

THE CASCADE RULE (why this exists): if a `[Tool]` script has an `[Export]` typed to a
Resource/Node subclass, that subclass AND every concrete subclass reachable under that
field in a `.tres`/`.tscn` MUST also be `[Tool]`. Otherwise the editor loads the instance
as a bare `Godot.Resource`/`Node` and the auto-generated setter throws InvalidCastException
at load time. Godot's C# source generator does NOT honor attribute inheritance, so each
concrete subclass needs its own `[Tool]`. The failure is EDITOR-ONLY — no runtime/GdUnit4
test can catch it — so detection is static (this script) or headless-editor import.

WHAT THIS DOES:
  - Parses every `.cs` file (excluding .godot/.claude/obj/bin) into a type graph:
    inheritance bases, `[Tool]`/`[GlobalClass]` markers, and typed-`[Export]` edges.
  - Computes the CASCADE-REQUIRED set: every class reachable via a typed-`[Export]` chain
    rooted at a `[Tool]` class (expanded across subclasses at each hop — fixpoint).
  - Runs the Category-A editor-code heuristic per file.
  - Classifies each `[Tool]` class: Justified / Cascade-driven / Convention / Unknown.
  - Emits `logs/tool_audit_inventory.md` (the re-runnable inventory deliverable).

DUAL PURPOSE:
  - Inventory generator (Phase 1 deliverable).
  - GATE: a cascade GAP = a cascade-required class that lacks `[Tool]`. Exit 1 if any gap,
    exit 0 if clean. Run in /regression_gate for precise, Godot-free static coverage.

LIMITATION (by design): the escape hatch — `[Export] Resource? X` typed as the base engine
type, cast at runtime — is invisible to static analysis (no concrete edge). A `.tres` placing
a non-`[Tool]` concrete subclass under such a field is NOT caught here; the headless-import
gate (Option B) or the blanket-on-Resources policy is the backstop for that case. Reported.

Usage:
  python3 tool_cascade_audit.py                 # scan, write inventory, gate exit code (0/1)
  python3 tool_cascade_audit.py --inventory-only # scan, write inventory, always exit 0
  python3 tool_cascade_audit.py --root <dir>     # override project root (default: cwd up to repo)
  python3 tool_cascade_audit.py --quiet          # suppress stdout summary
"""

import os
import re
import sys

# --- Godot base-type knowledge (roots of the Resource / Node cascade surfaces) ---
RESOURCE_ROOTS = {"Resource"}
NODE_ROOTS = {
    "Node", "Node2D", "Node3D", "Control", "CanvasItem", "CanvasLayer",
    "CharacterBody3D", "CharacterBody2D", "RigidBody3D", "RigidBody2D",
    "StaticBody3D", "StaticBody2D", "Area3D", "Area2D", "PhysicsBody3D",
    "CollisionObject3D", "CollisionObject2D", "CollisionShape3D", "MeshInstance3D",
    "Camera3D", "Camera2D", "Sprite3D", "Sprite2D", "AnimatedSprite3D",
    "AnimatedSprite2D", "AnimationPlayer", "AnimationTree", "Timer", "Marker3D",
    "Marker2D", "GpuParticles3D", "GpuParticles2D", "Path3D", "PathFollow3D",
    "NavigationAgent3D", "NavigationRegion3D", "Label", "RichTextLabel", "Panel",
    "PanelContainer", "Button", "TextureRect", "ColorRect", "Container",
    "VBoxContainer", "HBoxContainer", "EditorPlugin", "EditorInspectorPlugin",
    "EditorProperty", "Window", "SubViewport", "AudioStreamPlayer",
    "AudioStreamPlayer3D", "Skeleton3D", "BoneAttachment3D", "Line2D",
}

# Editor-time-code markers → Category A (Justified). File-level grep.
EDITOR_CODE_MARKERS = [
    "Engine.IsEditorHint", "_ValidateProperty", "_GetPropertyList",
    "_PropertyGetRevert", "_PropertyCanRevert", "EditorPlugin", "EditorInterface",
    "EditorInspector", "ToolButton", "ExportToolButton", "#if TOOLS",
]

# Jmodot framework base types whose subclasses are kept `[Tool]` by convention (Category C).
CONVENTION_BASES = {
    "State", "CompoundState", "BTState", "RootState",
    "BehaviorTask", "BehaviorAction", "SteeringBehaviorAction", "CompositeTask",
    "BehaviorTree", "BTCondition", "TransitionCondition", "StateTransition",
}

# Type tokens that are never user cascade targets even if they slip through extraction.
PRIMITIVE_TYPES = {
    "bool", "byte", "sbyte", "short", "ushort", "int", "uint", "long", "ulong",
    "float", "double", "decimal", "char", "string", "object", "void", "var",
    "Variant", "StringName", "NodePath", "Vector2", "Vector2I", "Vector3",
    "Vector3I", "Vector4", "Color", "Rect2", "Rect2I", "Aabb", "Basis",
    "Transform2D", "Transform3D", "Quaternion", "Plane", "Callable", "Rid",
    "PackedScene", "Texture2D", "Texture", "Curve", "Gradient", "Material",
    "Mesh", "AudioStream", "Font", "Shader", "Array", "Dictionary",
    "Godot", "Collections", "GCol", "GColl", "System",
}

CLASS_DECL_RE = re.compile(
    r"^\s*(?:public|internal|private|protected|abstract|sealed|static|partial|new|file|\s)*"
    r"\b(?:class|struct|record)\s+([A-Za-z_]\w*)"
    r"\s*(<[^>{]*>)?"           # group 2: optional generic params (captured for exclusion)
    r"\s*(?::\s*([^{]+?))?\s*(?:where\b.*)?$",   # group 3: base list
    re.MULTILINE,
)
# Attribute line: starts (after whitespace) with '[' — this anchor excludes the
# `<c>[GlobalClass, Tool]</c>` doc-comment false positives (those start with /// or *).
ATTR_LINE_RE = re.compile(r"^\s*\[")
TOOL_TOKEN_RE = re.compile(r"(?<![A-Za-z_])Tool(?![A-Za-z_])")          # 'Tool' not 'ToolButton'
GLOBALCLASS_TOKEN_RE = re.compile(r"(?<![A-Za-z_])GlobalClass(?![A-Za-z_])")
EXPORT_ATTR_RE = re.compile(r"\[\s*Export\b")
# A member declaration: <Type> <Name> followed by { get / ; / = / =>  (but not a method '(').
MEMBER_DECL_RE = re.compile(
    r"\b([A-Za-z_][\w\.]*\s*(?:<[^;{=]*?>)?\s*\??)\s+([A-Za-z_]\w*)\s*(?:\{|;|=>|=(?!=))"
)
IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def module_of(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if "/Jmodot/" in p or p.startswith("Jmodot/"):
        if "/Core/" in p:
            return "Jmodot.Core"
        if "/Implementation/" in p:
            return "Jmodot.Implementation"
        if "/Examples/" in p:
            return "Jmodot.Examples"
        return "Jmodot"
    if "/Tests/" in p or p.startswith("Tests/"):
        return "Tests"
    if "/addons/" in p or p.startswith("addons/"):
        return "addons"
    return "{{PROJECT_NAME}}"


def iter_cs_files(root: str):
    skip_dirs = {".godot", ".claude", ".git", "obj", "bin", "TestResults",
                 ".search-index", "gdunit4_testadapter_v5", "script_templates"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn.endswith(".cs") and not fn.endswith(".generated.cs"):
                yield os.path.join(dirpath, fn)


def split_bases(base_str: str):
    """Split a ': A, B<C,D>, E' base list into top-level identifiers (generic-aware)."""
    if not base_str:
        return []
    bases, depth, cur = [], 0, []
    for ch in base_str:
        if ch == "<":
            depth += 1
            cur.append(ch)
        elif ch == ">":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            bases.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        bases.append("".join(cur).strip())
    # Strip generic args: 'Base<T>' -> 'Base'; namespace 'A.B.Base' -> 'Base'.
    out = []
    for b in bases:
        b = b.split("<")[0].strip()
        b = b.split(".")[-1].strip()
        if b:
            out.append(b)
    return out


def parse_files(root: str):
    """Returns (types, files). types[name] = dict(merged across partials)."""
    types = {}
    files = {}  # path -> {content, classes:[(name, line_idx)], has_editor_code, module}
    for path in iter_cs_files(root):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        lines = content.split("\n")
        module = module_of(rel)
        has_editor_code = any(m in content for m in EDITOR_CODE_MARKERS)

        # Locate every class/struct/record declaration with its line index.
        decls = []  # (name, bases_list, line_idx, is_generic)
        for m in CLASS_DECL_RE.finditer(content):
            name = m.group(1)
            is_generic = bool(m.group(2))
            bases = split_bases(m.group(3) or "")
            line_idx = content.count("\n", 0, m.start())
            decls.append((name, bases, line_idx, is_generic))

        files[rel] = {
            "lines": lines, "decls": decls,
            "has_editor_code": has_editor_code, "module": module,
        }

        for (name, bases, line_idx, is_generic) in decls:
            # Attribute block = consecutive attribute/comment/blank lines directly above.
            attrs = []
            i = line_idx - 1
            while i >= 0:
                ln = lines[i]
                stripped = ln.strip()
                if ATTR_LINE_RE.match(ln):
                    attrs.append(stripped)
                    i -= 1
                elif stripped == "" or stripped.startswith("//") or stripped.startswith("/*") \
                        or stripped.startswith("*") or stripped.startswith("///"):
                    i -= 1
                else:
                    break
            attr_block = " ".join(attrs)
            has_tool = bool(TOOL_TOKEN_RE.search(attr_block))
            has_gc = bool(GLOBALCLASS_TOKEN_RE.search(attr_block))

            entry = types.get(name)
            if entry is None:
                entry = {
                    "name": name, "rel": rel, "module": module,
                    "bases": list(bases), "has_tool": has_tool,
                    "has_globalclass": has_gc, "has_editor_code": has_editor_code,
                    "is_generic": is_generic, "decl_lines": [(rel, line_idx)],
                }
                types[name] = entry
            else:
                # Merge partial-class parts: OR the markers, union the bases.
                entry["has_tool"] = entry["has_tool"] or has_tool
                entry["has_globalclass"] = entry["has_globalclass"] or has_gc
                entry["has_editor_code"] = entry["has_editor_code"] or has_editor_code
                entry["is_generic"] = entry["is_generic"] or is_generic
                for b in bases:
                    if b not in entry["bases"]:
                        entry["bases"].append(b)
                entry["decl_lines"].append((rel, line_idx))
                # Prefer a PP path as the canonical rel if the first was framework/test.
                if entry["module"] not in ("{{PROJECT_NAME}}",) and module == "{{PROJECT_NAME}}":
                    entry["rel"], entry["module"] = rel, module
    return types, files


def resolve_godot_kind(name, types, memo):
    """Return 'Resource' | 'Node' | 'Unknown' for the ultimate Godot ancestor of `name`."""
    if name in RESOURCE_ROOTS:
        return "Resource"
    if name in NODE_ROOTS:
        return "Node"
    if name in memo:
        return memo[name]
    memo[name] = "Unknown"  # guard against inheritance cycles
    entry = types.get(name)
    if entry is None:
        return "Unknown"
    # C# attribute classes (`FooAttribute : Attribute`) collide by simple-name with
    # the Jmodot `Attribute` Resource. They are NOT Godot types — never cascade.
    if entry.get("is_attr"):
        return "Unknown"
    result = "Unknown"
    for b in entry["bases"]:
        k = resolve_godot_kind(b, types, memo)
        if k == "Resource":
            result = "Resource"
            break
        if k == "Node":
            result = "Node"
    memo[name] = result
    return result


def split_top_level_commas(s):
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "<":
            depth += 1
            cur.append(ch)
        elif ch == ">":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def type_name_candidates(expr):
    """Type-name candidates of a type expression: the LAST dotted segment of the outer
    type, plus (recursively) those of any generic arguments. Critically, for a nested-type
    reference like `SpellSpawner.AnchorMode` this yields `AnchorMode` (the real type), NOT
    the qualifier `SpellSpawner` — preventing a false cascade edge to the outer class."""
    expr = expr.strip()
    if not expr:
        return []
    out = []
    lt = expr.find("<")
    if lt == -1:
        outer, inner = expr, ""
    else:
        outer = expr[:lt]
        depth, inner = 0, ""
        for i in range(lt, len(expr)):
            if expr[i] == "<":
                depth += 1
            elif expr[i] == ">":
                depth -= 1
                if depth == 0:
                    inner = expr[lt + 1:i]
                    break
    outer = outer.strip().rstrip("?").strip()
    if outer:
        last = outer.split(".")[-1].strip().rstrip("?").strip()
        if last:
            out.append(last)
    if inner:
        for part in split_top_level_commas(inner):
            out.extend(type_name_candidates(part))
    return out


def extract_export_targets(decl_text, types):
    """From a member declaration's TYPE portion, return user-type names referenced."""
    m = MEMBER_DECL_RE.search(decl_text)
    if not m:
        return []
    type_expr = m.group(1)
    # Skip method declarations (member name immediately followed by '(').
    after = decl_text[m.end(2):].lstrip()
    if after.startswith("("):
        return []
    targets = []
    for name in type_name_candidates(type_expr):
        if name in PRIMITIVE_TYPES:
            continue
        if name in types:  # only user-declared types form cascade edges
            targets.append(name)
    return targets


def build_export_edges(types, files, kind_memo):
    """edges[parent] = set(child types); reverse[child] = set(parents)."""
    edges = {}
    reverse = {}
    for rel, fi in files.items():
        lines = fi["lines"]
        decls = sorted(fi["decls"], key=lambda d: d[2])
        if not decls:
            continue

        def owner_at(line_idx):
            owner = None
            for (nm, _b, li, _g) in decls:
                if li <= line_idx:
                    owner = nm
                else:
                    break
            return owner

        n = len(lines)
        for idx in range(n):
            if not EXPORT_ATTR_RE.search(lines[idx]):
                continue
            # Build declaration text: tail of this line after the export attribute,
            # plus following lines until a member declaration terminator is seen.
            tail = lines[idx]
            decl_text = tail[tail.find("[Export"):]
            # If the [Export...] is the whole line (attribute alone), look ahead.
            j = idx
            while not re.search(r"\{|;|=>|=(?!=)", decl_text) and j + 1 < n and (j - idx) < 4:
                j += 1
                decl_text += " " + lines[j].strip()
            owner = owner_at(idx)
            if owner is None or owner not in types:
                continue
            for child in extract_export_targets(decl_text, types):
                k = resolve_godot_kind(child, types, kind_memo)
                if k in ("Resource", "Node"):
                    edges.setdefault(owner, set()).add(child)
                    reverse.setdefault(child, set()).add(owner)
    return edges, reverse


def build_subclass_map(types):
    """direct_sub[base] = set(direct subclasses). Then transitive expansion on demand."""
    direct = {}
    for name, e in types.items():
        for b in e["bases"]:
            direct.setdefault(b, set()).add(name)
    return direct


def all_subclasses(name, direct, cache):
    if name in cache:
        return cache[name]
    cache[name] = set()  # cycle guard
    out = set()
    for sub in direct.get(name, ()):
        out.add(sub)
        out |= all_subclasses(sub, direct, cache)
    cache[name] = out
    return out


def compute_cascade_required(types, edges, direct):
    """Fixpoint: every type reachable via a typed-[Export] chain from a [Tool] class,
    expanded across all subclasses at each hop (concrete .tres-instantiable subclasses)."""
    required = set()
    sub_cache = {}
    work = []
    for name, e in types.items():
        if e["has_tool"]:
            for child in edges.get(name, ()):
                work.append(child)
    while work:
        t = work.pop()
        family = {t} | all_subclasses(t, direct, sub_cache)
        for member in family:
            if member in required:
                continue
            minfo = types.get(member, {})
            if minfo.get("is_attr"):
                continue  # C# attribute name-collision; not a Godot cascade member
            if minfo.get("is_generic"):
                continue  # generic class — Godot can't register/serialize it as [Tool]/.tres
            required.add(member)
            # member will be [Tool] (required) → its own typed exports cascade further.
            for child in edges.get(member, ()):
                work.append(child)
    return required


def ancestors(name, types, _seen=None):
    """All transitive base type names of `name` that are known user types."""
    if _seen is None:
        _seen = set()
    out = set()
    e = types.get(name)
    if e is None:
        return out
    for b in e["bases"]:
        if b in _seen:
            continue
        _seen.add(b)
        out.add(b)
        out |= ancestors(b, types, _seen)
    return out


def cascade_sources(name, types, reverse):
    """The `[Tool]` parent classes whose typed `[Export]` pulls `name` into the cascade,
    found by walking `name` and its ancestors for direct exporters."""
    srcs = set()
    for t in {name} | ancestors(name, types):
        srcs |= reverse.get(t, set())
    return sorted(srcs)


def classify(name, types, required, kind_memo):
    e = types[name]
    if e["has_editor_code"]:
        return "Justified"
    if name in required:
        return "Cascade-driven"
    # Convention: extends a Jmodot framework base, or is itself a Jmodot class.
    if any(b in CONVENTION_BASES for b in e["bases"]):
        return "Convention"
    if e["module"].startswith("Jmodot"):
        return "Convention"
    # Policy: a [GlobalClass] Resource carries [Tool] under the blanket-on-Resources
    # policy (side-effect-free; closes the escape-hatch blind spot uniformly).
    if e.get("has_globalclass") and kind_memo.get(name) == "Resource":
        return "Policy"
    return "Unknown"


def main():
    args = sys.argv[1:]
    inventory_only = "--inventory-only" in args
    quiet = "--quiet" in args
    root = os.getcwd()
    if "--root" in args:
        root = os.path.abspath(args[args.index("--root") + 1])
    else:
        # Walk up to the repo root (dir containing project.godot).
        d = os.getcwd()
        while d != os.path.dirname(d):
            if os.path.exists(os.path.join(d, "project.godot")):
                root = d
                break
            d = os.path.dirname(d)

    types, files = parse_files(root)
    # Flag C# attribute classes (`FooAttribute : Attribute`) — they collide by simple
    # name with the Jmodot `Attribute` Resource but are not Godot cascade members.
    for nm, e in types.items():
        e["is_attr"] = (nm.endswith("Attribute") and nm != "Attribute"
                        and "Attribute" in e["bases"])
    kind_memo = {}
    for name in list(types):
        resolve_godot_kind(name, types, kind_memo)
    edges, reverse = build_export_edges(types, files, kind_memo)
    direct = build_subclass_map(types)
    required = compute_cascade_required(types, edges, direct)

    # Cascade GAPS: cascade-required classes lacking [Tool] (and not a C# attribute).
    gaps = sorted(n for n in required
                  if n in types and not types[n]["has_tool"]
                  and not types[n].get("is_attr"))

    # Classify every [Tool] class.
    tool_classes = sorted(n for n, e in types.items() if e["has_tool"])
    cat_counts = {"Justified": 0, "Cascade-driven": 0, "Convention": 0,
                  "Policy": 0, "Unknown": 0}
    rows = []
    for name in tool_classes:
        e = types[name]
        cat = classify(name, types, required, kind_memo)
        cat_counts[cat] += 1
        rows.append((name, e, cat))

    # Bucket gaps by module + Godot kind. {{PROJECT_NAME}} gaps are the actionable set;
    # Jmodot (black-box framework contract) and Tests (throwaway fixtures) are
    # reported-but-not-gated.
    pp_gaps = [n for n in gaps if types[n]["module"] == "{{PROJECT_NAME}}"]
    framework_gaps = [n for n in gaps if types[n]["module"].startswith("Jmodot")]
    test_gaps = [n for n in gaps if types[n]["module"] in ("Tests", "addons")]
    pp_resource_gaps = [n for n in pp_gaps if kind_memo.get(n) == "Resource"]
    pp_node_gaps = [n for n in pp_gaps if kind_memo.get(n) == "Node"]

    # Unknown [Tool] classes ({{PROJECT_NAME}}) — the "justify-or-drop" set for policy.
    pp_unknown = [name for name, e, cat in rows
                  if cat == "Unknown" and e["module"] == "{{PROJECT_NAME}}"]

    # Blanket-Resources worklist: EVERY {{PROJECT_NAME}} [GlobalClass] Resource lacking
    # [Tool] (superset of the Resource cascade gaps). Sizes the "blanket" policy diff.
    pp_blanket_resources = sorted(
        n for n, e in types.items()
        if e["module"] == "{{PROJECT_NAME}}" and e["has_globalclass"]
        and not e["has_tool"] and not e.get("is_attr")
        and kind_memo.get(n) == "Resource")

    write_inventory(root, types, edges, reverse, required, gaps, rows,
                    cat_counts, kind_memo,
                    pp_gaps=pp_gaps, framework_gaps=framework_gaps,
                    test_gaps=test_gaps, pp_unknown=pp_unknown,
                    pp_blanket_resources=pp_blanket_resources)

    # Emit the Resource-rooted class-name allowlist consumed by the edit-time hook
    # (pattern_enforcer.py) so it can recognize indirect Resource bases (e.g. a class
    # declared `: SpellEffect`) without re-deriving the whole type graph per file.
    write_resource_allowlist(root, types, kind_memo)

    if not quiet:
        print_summary(types, rows, cat_counts, gaps, required, reverse, kind_memo,
                      pp_gaps, pp_resource_gaps, pp_node_gaps,
                      framework_gaps, test_gaps, pp_unknown, pp_blanket_resources)

    if inventory_only:
        return 0
    # Gate semantics enforce the CHOSEN POLICY (blanket [Tool] on every PP [GlobalClass]
    # Resource). The broader cascade-required closure (pp_gaps) is reported as informational;
    # the headless-import gate backstops the narrower cases the blanket rule doesn't cover
    # (non-[GlobalClass] inline Resources, Node cascades, escape-hatch placements).
    return 1 if pp_blanket_resources else 0


def write_inventory(root, types, edges, reverse, required, gaps, rows,
                    cat_counts, kind_memo, pp_gaps=(), framework_gaps=(),
                    test_gaps=(), pp_unknown=(), pp_blanket_resources=()):
    logs_dir = os.path.join(root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    out_path = os.path.join(logs_dir, "tool_audit_inventory.md")

    def fmt_parents(name):
        ps = cascade_sources(name, types, reverse)
        return ", ".join(ps) if ps else "—"

    by_module = {}
    for name, e, cat in rows:
        by_module.setdefault(e["module"], 0)
        by_module[e["module"]] += 1

    lines = []
    lines.append("# `[Tool]` Attribute Inventory\n")
    lines.append("> Auto-generated by `.claude/hooks/tool_cascade_audit.py`. Re-run to refresh. "
                 "Do not hand-edit.\n")
    lines.append("## Summary\n")
    lines.append(f"- **Total `[Tool]` classes:** {len(rows)}")
    for mod in sorted(by_module):
        lines.append(f"  - {mod}: {by_module[mod]}")
    lines.append("- **Category counts:**")
    for cat in ("Justified", "Cascade-driven", "Convention", "Policy", "Unknown"):
        lines.append(f"  - {cat}: {cat_counts[cat]}")
    lines.append(f"- **Cascade-required classes (theoretical closure, any module):** {len(required)}")
    lines.append(f"- **CASCADE GAPS (required-but-not-`[Tool]`):** total {len(gaps)} "
                 f"— {{PROJECT_NAME}} {len(pp_gaps)} (actionable) / "
                 f"Jmodot {len(framework_gaps)} (black-box) / "
                 f"Tests+addons {len(test_gaps)} (fixtures)\n")
    lines.append("> **Closure semantics:** a gap is a class reachable via a typed `[Export]` "
                 "chain from a `[Tool]` root (expanded across all subclasses). This is the "
                 "*theoretical* superset; whether a gap actually throws depends on a `.tres`/"
                 "`.tscn` placing a concrete instance under that field. Resource gaps are the "
                 "cheap-to-close surface (`[Tool]` on a Resource is side-effect-free); Node "
                 "gaps drag editor lifecycle execution and need scrutiny.\n")

    lines.append("## Cascade Gaps — {{PROJECT_NAME}} actionable worklist\n")
    if pp_gaps:
        lines.append("Reachable via a typed `[Export]` chain from a `[Tool]` class but lacking "
                     "`[Tool]`. Each is a latent `InvalidCastException` at editor load if a "
                     "`.tres`/`.tscn` places it under that field.\n")
        lines.append("| Class | Kind | Base | Cascade source (`[Tool]` exporter of family) | File |")
        lines.append("|---|---|---|---|---|")
        for name in pp_gaps:
            e = types[name]
            kind = kind_memo.get(name, "Unknown")
            base = ", ".join(e["bases"]) if e["bases"] else "—"
            lines.append(f"| `{name}` | {kind} | `{base}` | {fmt_parents(name)} | `{e['rel']}` |")
    else:
        lines.append("**None.** Every {{PROJECT_NAME}} cascade-required class carries `[Tool]`. ✅")
    lines.append("")
    lines.append("### Out-of-scope gaps (reported, not gated)\n")
    lines.append(f"- **Jmodot (framework, black-box — paired-PR only):** {len(framework_gaps)}")
    lines.append(f"- **Tests + addons (fixtures / editor plugins):** {len(test_gaps)}\n")

    lines.append("## Blanket-Resources worklist ({{PROJECT_NAME}})\n")
    lines.append(f"Every PP `[GlobalClass]` Resource lacking `[Tool]` ({len(pp_blanket_resources)} "
                 "total) — the diff a *blanket-on-Resources* policy would apply. Superset of the "
                 "Resource cascade gaps above. `[Tool]` on a Resource is side-effect-free.\n")
    if pp_blanket_resources:
        for name in pp_blanket_resources:
            e = types[name]
            gapmark = " **(cascade gap)**" if name in pp_gaps else ""
            lines.append(f"- `{name}`{gapmark} — `{e['rel']}`")
    lines.append("")

    lines.append("## Unknown-category `[Tool]` classes ({{PROJECT_NAME}}) — justify-or-drop\n")
    if pp_unknown:
        lines.append("These carry `[Tool]` but have no editor-time code, no typed-`[Export]` "
                     "cascade pressure, and don't extend a framework convention base. Under a "
                     "*precise* policy they are drop candidates; under *blanket-Resources* the "
                     "Resources keep `[Tool]`.\n")
        lines.append("| Class | Kind | Base | File |")
        lines.append("|---|---|---|---|")
        for name in pp_unknown:
            e = types[name]
            kind = kind_memo.get(name, "Unknown")
            base = ", ".join(e["bases"]) if e["bases"] else "—"
            lines.append(f"| `{name}` | {kind} | `{base}` | `{e['rel']}` |")
    else:
        lines.append("**None.** Every {{PROJECT_NAME}} `[Tool]` class is justified. ✅")
    lines.append("")

    lines.append("## Full `[Tool]` class inventory\n")
    lines.append("| Class | Module | Base | Kind | EditorCode | TypedExports | "
                 "ExportedByToolParents | Convention | Category | File |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, e, cat in rows:
        kind = kind_memo.get(name, "Unknown")
        base = ", ".join(e["bases"]) if e["bases"] else "—"
        editor = "yes" if e["has_editor_code"] else ""
        has_exports = "yes" if edges.get(name) else ""
        convention = "yes" if (any(b in CONVENTION_BASES for b in e["bases"])
                               or e["module"].startswith("Jmodot")) else ""
        lines.append(f"| `{name}` | {e['module']} | `{base}` | {kind} | {editor} | "
                     f"{has_exports} | {fmt_parents(name)} | {convention} | {cat} | "
                     f"`{e['rel']}` |")
    lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def write_resource_allowlist(root, types, kind_memo):
    """Write newline-separated names of all Resource-rooted classes to
    .claude/hooks/tool_resource_classes.txt — the edit-time hook's allowlist for detecting
    that a class's declared base is a Resource (direct or via a Resource subclass). Lives in
    .claude/hooks/ (committed, gate-regenerated — same pattern as regression_baseline.json)
    so the hook is functional on a fresh checkout, not just after a local audit run."""
    out_dir = os.path.join(root, ".claude", "hooks")
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in types
                   if kind_memo.get(n) == "Resource" and not types[n].get("is_attr"))
    header = ("# Resource-rooted class names — generated by tool_cascade_audit.py.\n"
              "# Consumed by pattern_enforcer.py to flag a [GlobalClass] Resource missing\n"
              "# [Tool] at edit time. Refresh by re-running the audit. Do not hand-edit.\n")
    with open(os.path.join(out_dir, "tool_resource_classes.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(header + "\n".join(names) + "\n")


def print_summary(types, rows, cat_counts, gaps, required, reverse, kind_memo,
                  pp_gaps, pp_resource_gaps, pp_node_gaps,
                  framework_gaps, test_gaps, pp_unknown, pp_blanket_resources):
    print("=== Tool Cascade Audit ===")
    print(f"Parsed types: {len(types)}")
    print(f"[Tool] classes: {len(rows)}  "
          f"(Justified={cat_counts['Justified']}, "
          f"Cascade-driven={cat_counts['Cascade-driven']}, "
          f"Convention={cat_counts['Convention']}, "
          f"Policy={cat_counts['Policy']}, "
          f"Unknown={cat_counts['Unknown']})")
    print(f"Cascade-required (theoretical closure): {len(required)}")
    print(f"CASCADE GAPS total={len(gaps)}  "
          f"PP={len(pp_gaps)} (Resource={len(pp_resource_gaps)}, Node={len(pp_node_gaps)})  "
          f"Jmodot={len(framework_gaps)}  Tests+addons={len(test_gaps)}")
    print(f"\n--- {{PROJECT_NAME}} cascade gaps ({len(pp_gaps)}) — actionable ---")
    for name in pp_gaps:
        e = types[name]
        kind = kind_memo.get(name, "Unknown")
        srcs = ", ".join(cascade_sources(name, types, reverse)) or "?"
        print(f"  [{kind:8}] {name}  base={','.join(e['bases'])}  "
              f"via={srcs}  ({e['rel']})")
    print(f"\n--- {{PROJECT_NAME}} Unknown-category [Tool] classes ({len(pp_unknown)}) "
          f"— justify-or-drop ---")
    for name in pp_unknown:
        e = types[name]
        kind = kind_memo.get(name, "Unknown")
        print(f"  [{kind:8}] {name}  base={','.join(e['bases'])}  ({e['rel']})")
    print(f"\nBlanket-Resources worklist (every PP [GlobalClass] Resource lacking "
          f"[Tool]): {len(pp_blanket_resources)}")
    print("\nInventory written to logs/tool_audit_inventory.md")


if __name__ == "__main__":
    sys.exit(main())
