"""Re-runnable proof for hooks/refcounted_free_guard.py's `adaptation.json` `tests_root` and
`teardown_helpers` seams (Design Doc §8).

Unlike the other hooks, this one derives its repo root from its OWN path depth
(`Path(__file__).resolve().parents[2]`, i.e. `<repo>/.claude/hooks/<file>`), so its scratch
tree nests one level deeper than `_adaptation_fixture.make_scratch` -- `<repo>/.claude/...`,
not `<repo>/...` -- and this proof builds that tree itself rather than reusing the shared
helper.

Three shapes: adaptation.json absent -> `tests_root` defaults to "Tests", `teardown_helpers`
defaults to []; present with valid values -> both apply; a wrong-typed value for either key
-> that key's default plus one stderr line. Validation cases: a `teardown_helpers` entry that
is not an existing `.cs` file under `tests_root` is skipped with one stderr line; a missing
`tests_root` directory makes the guard print CANNOT-RUN and exit 2, never OK.

    python3 .claude/tests/test_refcounted_free_guard_adaptation.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "refcounted_free_guard.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)
REAL_TOOLS_ADAPTATION = os.path.join(fx.REAL_TOOLS, "adaptation.py")


def make_repo(seed=None, tests_dir_name="Tests", write_tests_dir=True):
    """`<tmp>` playing REPO root: `.claude/hooks/refcounted_free_guard.py`,
    `.claude/tools/adaptation.py`, `.claude/skills/project_subsystems/adaptation.json`, and
    (unless suppressed) a `<tests_dir_name>/` directory so the guard has something to scan."""
    tmp = tempfile.mkdtemp(prefix="refc_repo_")
    hooks_dir = os.path.join(tmp, ".claude", "hooks")
    tools_dir = os.path.join(tmp, ".claude", "tools")
    skill_dir = os.path.join(tmp, ".claude", "skills", "project_subsystems")
    os.makedirs(hooks_dir, exist_ok=True)
    os.makedirs(tools_dir, exist_ok=True)
    os.makedirs(skill_dir, exist_ok=True)
    shutil.copy(REAL_HOOK, os.path.join(hooks_dir, HOOK_NAME))
    shutil.copy(REAL_TOOLS_ADAPTATION, os.path.join(tools_dir, "adaptation.py"))
    shutil.copy(os.path.join(fx.REAL_HOOKS, "_checkout_root.py"), os.path.join(hooks_dir, "_checkout_root.py"))
    if seed is not None:
        with open(os.path.join(skill_dir, "adaptation.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(seed if isinstance(seed, str) else json.dumps(seed))
    if write_tests_dir:
        os.makedirs(os.path.join(tmp, tests_dir_name), exist_ok=True)
    return tmp, os.path.join(hooks_dir, HOOK_NAME)


def seed_docs(repo):
    d = os.path.join(repo, ".claude", "cache", "godot-docs", "doc", "classes")
    os.makedirs(d, exist_ok=True)
    for name, parent in (("Object", None), ("RefCounted", "Object"), ("Resource", "RefCounted"),
                         ("Node", "Object")):
        attr = ' inherits="%s"' % parent if parent else ""
        with open(os.path.join(d, name + ".xml"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('<?xml version="1.0"?>\n<class name="%s"%s api_type="core">\n</class>\n' % (name, attr))


def write_cs(repo, rel, text):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run_hook(hook_path):
    r = subprocess.run([sys.executable, hook_path], capture_output=True, text=True, timeout=30)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def main():
    cases = []
    failures = []
    repos = []
    try:
        # --- absent: tests_root defaults to "Tests", teardown_helpers to [] ---------------
        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        out, err, rc = run_hook(hook)
        cases.append(("absent adaptation.json: default tests_root -> OK on an empty Tests/",
                      rc == 0 and out == "refcounted_free: OK"))
        cases.append(("absent adaptation.json: no stderr", err == ""))

        # --- present: tests_root override is honored ---------------------------------------
        repo, hook = make_repo(seed={"tests_root": "MyTests"}, tests_dir_name="MyTests")
        repos.append(repo)
        out, err, rc = run_hook(hook)
        cases.append(("present adaptation.json: tests_root override is honored",
                      rc == 0 and out == "refcounted_free: OK"))
        cases.append(("present adaptation.json: no stderr on a valid tests_root", err == ""))

        # --- present: teardown_helpers exempts a real .cs file under tests_root -----------
        repo, hook = make_repo(
            seed={"teardown_helpers": ["Tests/Helpers/MyTeardown.cs"]}, tests_dir_name="Tests")
        repos.append(repo)
        helper_dir = os.path.join(repo, "Tests", "Helpers")
        os.makedirs(helper_dir, exist_ok=True)
        with open(os.path.join(helper_dir, "MyTeardown.cs"), "w") as fh:
            fh.write(
                "class MyTeardown { void F(System.Collections.Generic.List<Godot.Resource> xs) "
                "{ foreach (var x in xs) { x.Free(); } } }"
            )
        out, err, rc = run_hook(hook)
        cases.append(("present adaptation.json: the exempted helper file is not flagged",
                      rc == 0 and out == "refcounted_free: OK"))
        cases.append(("present adaptation.json: no stderr on a valid teardown_helpers entry",
                      err == ""))

        # --- wrong-typed: tests_root is not a string ---------------------------------------
        repo, hook = make_repo(seed={"tests_root": ["Tests"]}, tests_dir_name="Tests")
        repos.append(repo)
        out, err, rc = run_hook(hook)
        cases.append(("wrong-typed tests_root: falls back to the default 'Tests'",
                      rc == 0 and out == "refcounted_free: OK"))
        cases.append(("wrong-typed tests_root: one stderr line names the key",
                      err.count("\n") == 0 and "tests_root" in err))

        # --- wrong-typed: teardown_helpers is not a list ------------------------------------
        repo, hook = make_repo(seed={"teardown_helpers": "Tests/Helpers/MyTeardown.cs"},
                                tests_dir_name="Tests")
        repos.append(repo)
        out, err, rc = run_hook(hook)
        cases.append(("wrong-typed teardown_helpers: falls back to the empty default",
                      rc == 0 and out == "refcounted_free: OK"))
        cases.append(("wrong-typed teardown_helpers: one stderr line names the key",
                      "teardown_helpers" in err))

        # --- validation: an entry that is not an existing .cs under tests_root is skipped -
        repo, hook = make_repo(
            seed={"teardown_helpers": ["Tests/DoesNotExist.cs"]}, tests_dir_name="Tests")
        repos.append(repo)
        out, err, rc = run_hook(hook)
        cases.append(("validation: a missing teardown_helpers path is skipped with stderr",
                      "DoesNotExist.cs" in err and rc == 0))

        # --- validation: a missing tests_root -> CANNOT-RUN, exit 2, never OK -------------
        repo, hook = make_repo(seed={"tests_root": "NoSuchDir"}, tests_dir_name="Tests",
                                write_tests_dir=False)
        repos.append(repo)
        # Also remove the default "Tests" dir so only the seeded root is in play.
        out, err, rc = run_hook(hook)
        cases.append(("validation: a missing tests_root prints CANNOT-RUN and exits 2",
                      rc == 2 and "CANNOT-RUN" in out and out != "refcounted_free: OK"))

        # --- typed-field detection: type map from docs `inherits` + `class X : Y` ----------
        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        seed_docs(repo)
        write_cs(repo, "Src/Types.cs",
                 "public partial class BTCondition : Resource {}\n"
                 "public partial class Category : Resource {}\n"
                 "public partial class Holder : Node {}\n"
                 "public class Mystery : Unknowable {}\n"
                 "public class Gen<T> : Category {}\n")
        write_cs(repo, "Tests/Typed.cs",
                 "class T {\n"
                 "  private BTCondition _condition;\n"
                 "  void A() { _condition?.Free(); }\n"
                 "  void B() { var c = new Category(); c.Free(); }\n"
                 "  private Gen<int> _gen;\n"
                 "  void C() { _gen.Free(); }\n"
                 "}\n")
        out, err, rc = run_hook(hook)
        cases.append(("typed Resource field `_condition?.Free()` is flagged", "Typed.cs:3" in out and rc == 1))
        cases.append(("typed Resource local `var c = new Category()` is flagged", "Typed.cs:4" in out))
        cases.append(("generic subclass of a Resource subclass is flagged", "Typed.cs:6" in out))

        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        seed_docs(repo)
        write_cs(repo, "Src/Types.cs",
                 "public partial class Holder : Node {}\npublic class Mystery : Unknowable {}\n")
        write_cs(repo, "Tests/Neg.cs",
                 "class T {\n"
                 "  private Holder _h;\n"
                 "  private Mystery _m;\n"
                 "  private Stranger _s;\n"
                 "  void A() { _h.Free(); _m?.Free(); _s.Free(); }\n"
                 "}\n")
        out, err, rc = run_hook(hook)
        cases.append(("Node-typed, unknown-parent and unknown-type receivers are not flagged",
                      rc == 0 and out == "refcounted_free: OK"))

        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        seed_docs(repo)
        write_cs(repo, "Src/Types.cs", "public partial class Category : Resource {}\n")
        write_cs(repo, "Tests/Narrow.cs",
                 "class T {\n  private Category _c;\n"
                 "  void A() { if (_c is Node) _c.Free(); }\n}\n")
        out, err, rc = run_hook(hook)
        cases.append(("`is Node` narrowing on the line still exempts a typed receiver",
                      rc == 0 and out == "refcounted_free: OK"))
        out2, _, rc2 = run_hook(hook)
        cases.append(("a second run on the cached map gives the same verdict",
                      rc2 == 0 and out2 == out))

        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        seed_docs(repo)
        write_cs(repo, "Src/Types.cs",
                 "public partial class Tracked : Resource {}\n"
                 "public partial class Holder : Node {}\n")
        write_cs(repo, "Tests/Veto.cs",
                 "class T {\n"
                 "  void A() { var t1 = new Tracked(); var t2 = new Tracked(); var t3 = new Tracked();\n"
                 "    var t4 = new Tracked(); var t5 = new Tracked(); var t6 = new Tracked();\n"
                 "    var t7 = new Tracked(); var t8 = new Tracked(); var t9 = new Tracked();\n"
                 "    var t10 = new Tracked(); var t11 = new Tracked(); var t12 = new Tracked(); }\n"
                 "  void B() {\n"
                 "    var (p, s, t1) = Make();\n"
                 "    (Holder q, Holder t2) = Make2();\n"
                 "    Get(out var t3); Get(out Holder t4);\n"
                 "    foreach (var t5 in Items()) { t5.Free(); }\n"
                 "    var t6 = Find();\n"
                 "    using var t7 = Find();\n"
                 "    if (x is Holder t8) { }\n"
                 "    t1.Free(); t2.Free(); t3.Free(); t4.Free(); t6.Free(); t7.Free(); t8.Free();\n"
                 "  }\n"
                 "  void C(Holder t9) { t9.Free(); }\n"
                 "  void D() { Run(t10 => t10.Free()); Run((a, t11) => t11.Free()); Run((Holder t12) => t12.Free()); }\n"
                 "}\n")
        out, err, rc = run_hook(hook)
        cases.append(("a name also declared where the guard cannot type it is never flagged",
                      rc == 0 and out == "refcounted_free: OK"))

        repo, hook = make_repo(seed=None, tests_dir_name="Tests")
        repos.append(repo)
        seed_docs(repo)
        write_cs(repo, "Src/Types.cs", "public partial class Tracked : Resource {}\n")
        write_cs(repo, "Tests/Still.cs",
                 "class T {\n"
                 "  void A() { var t = new Tracked(); t.Free(); }\n"
                 "  void B() { var u = new Tracked(); Tracked v = new Tracked(); if (o is Tracked w) { w.Free(); } u.Free(); }\n"
                 "}\n")
        out, err, rc = run_hook(hook)
        cases.append(("veto leaves plain typed declarations flagged",
                      "Still.cs:2" in out and "Still.cs:3" in out and rc == 1))
    finally:
        for repo in repos:
            fx.rm(repo)

    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label)

    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
