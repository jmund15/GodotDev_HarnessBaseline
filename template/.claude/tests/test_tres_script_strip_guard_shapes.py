#!/usr/bin/env python3
"""Shape proof for hooks/tres_script_strip_guard.py: which commits lose a script binding, and which only move it.

A real git repository is built once and copied per case. Each case stages one commit shape against a HEAD
holding scripted sub-resources, then asks the guard in two modes: the commit hook on the staged index and
`--range HEAD~1..HEAD` on the commit just made. A shape that removes a `script = ExtResource(...)` line
without losing a binding must pass in both (extraction to its own file, a move between files, a standalone
file inlined, a sub-resource deleted, id churn). A strip must be denied in both (the editor resave that keeps
the section and drops the script line, with or without renamed ids, with an unrelated binding added beside it).
Mode cases cover a separate commit index (`GIT_INDEX_FILE`) and the working-tree scan with `--repair-inplace`.

    python3 .claude/tests/test_tres_script_strip_guard_shapes.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tres_script_strip_guard.py")
sys.path.insert(0, HERE)
from _hook_call import call, copy_repo  # noqa: E402

TMP = tempfile.mkdtemp(prefix="strip_shapes_")
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_", "HARNESS_"))}
ENV.update(HOME=TMP, USERPROFILE=TMP, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

HEADER = '[gd_resource type="Resource" format=3]\n\n'
CAST = (
    HEADER
    + '[ext_resource type="Script" path="res://Cond.cs" id="1_cond"]\n'
    + '[ext_resource type="Script" path="res://Wrap.cs" id="2_wrap"]\n\n'
    + '[sub_resource type="Resource" id="Resource_a"]\nscript = ExtResource("1_cond")\nKey = "a"\n\n'
    + '[sub_resource type="Resource" id="Resource_b"]\nscript = ExtResource("1_cond")\nKey = "b"\n\n'
    + '[resource]\nscript = ExtResource("2_wrap")\nConds = [SubResource("Resource_a"), SubResource("Resource_b")]\n'
)
OTHER = (
    HEADER
    + '[ext_resource type="Script" path="res://Wrap.cs" id="1_wrap"]\n\n'
    + '[resource]\nscript = ExtResource("1_wrap")\nName = "other"\n'
)
STANDALONE = (
    HEADER
    + '[ext_resource type="Script" path="res://Cond.cs" id="1_x"]\n\n'
    + '[resource]\nscript = ExtResource("1_x")\nKey = "solo"\n'
)
PLAIN = HEADER + '[resource]\nValue = 1\n'
CHURN = (
    HEADER
    + '[ext_resource type="Script" path="res://Cond.cs" id="1_cond"]\n'
    + '[ext_resource type="Script" path="res://Wrap.cs" id="2_wrap"]\n\n'
    + '[resource]\nscript = ExtResource("2_wrap")\nName = "churn"\n'
)
BASE = {
    "AI/cast.tres": CAST,
    "AI/other.tres": OTHER,
    "AI/solo.tres": STANDALONE,
    "AI/plain.tres": PLAIN,
    "AI/churn.tres": CHURN,
    "AI/second.tres": CAST.replace("Resource_a", "Resource_p").replace("Resource_b", "Resource_q"),
    "AI/Dür.tres": CAST,
}


def sub(sid, key, ext="1_cond"):
    return '[sub_resource type="Resource" id="%s"]\nscript = ExtResource("%s")\nKey = "%s"\n\n' % (sid, ext, key)


def without_sections(text, *ids):
    """`text` minus the sub_resource sections named by `ids` and their `SubResource(...)` references."""
    out, skip = [], False
    for line in text.split("\n"):
        if line.startswith("["):
            skip = any('id="%s"' % i in line for i in ids)
        if not skip:
            out.append(line)
    text = "\n".join(out)
    for i in ids:
        text = text.replace('SubResource("%s")' % i, "").replace("[, ", "[").replace(", ]", "]").replace("[, ]", "[]")
    return text


STRIPPED_A = CAST.replace('script = ExtResource("1_cond")\nKey = "a"', 'resource_local_to_scene = ""\nKey = "a"')
CASES = []  # (name, expect, changes, mode); changes maps path -> text, None deletes it


def case(name, expect, changes, *, moved=False):
    CASES.append((name, expect, changes, moved))


# ---- allowed: a binding leaves its file, none is lost ----
case("extract sub-resource to its own file (the wizard cast condition)", "allow", {
    "AI/cast.tres": without_sections(CAST, "Resource_a").replace(
        '[ext_resource type="Script" path="res://Wrap.cs" id="2_wrap"]',
        '[ext_resource type="Script" path="res://Wrap.cs" id="2_wrap"]\n'
        '[ext_resource type="Resource" path="res://AI/has.tres" id="3_has"]'),
    "AI/has.tres": STANDALONE.replace("solo", "has"),
})
case("move sub-resource into another existing file", "allow", {
    "AI/cast.tres": without_sections(CAST, "Resource_a"),
    "AI/other.tres": OTHER.replace(
        '[resource]', '[ext_resource type="Script" path="res://Cond.cs" id="2_cond"]\n\n' + sub("Resource_a", "a", "2_cond") + '[resource]'),
})
case("inline a standalone file as a sub-resource", "allow", {
    "AI/solo.tres": None,
    "AI/other.tres": OTHER.replace(
        '[resource]', '[ext_resource type="Script" path="res://Cond.cs" id="2_cond"]\n\n' + sub("Resource_solo", "solo", "2_cond") + '[resource]'),
})
case("delete a scripted sub-resource", "allow", {"AI/cast.tres": without_sections(CAST, "Resource_b")})
case("delete two scripted sub-resources", "allow", {"AI/cast.tres": without_sections(CAST, "Resource_a", "Resource_b")})
case("delete a whole scripted file", "allow", {"AI/solo.tres": None})
case("extract one sub-resource and delete another from the same file", "allow", {
    "AI/cast.tres": without_sections(CAST, "Resource_a", "Resource_b"),
    "AI/has.tres": STANDALONE.replace("solo", "has"),
})
case("rename ext_resource ids, bindings intact", "allow", {
    "AI/cast.tres": CAST.replace("1_cond", "9_zzz").replace("2_wrap", "8_yyy"),
})
case("reorder sections, bindings intact", "allow", {
    "AI/cast.tres": HEADER
    + '[ext_resource type="Script" path="res://Cond.cs" id="1_cond"]\n'
    + '[ext_resource type="Script" path="res://Wrap.cs" id="2_wrap"]\n\n'
    + sub("Resource_b", "b") + sub("Resource_a", "a")
    + '[resource]\nscript = ExtResource("2_wrap")\nConds = [SubResource("Resource_a"), SubResource("Resource_b")]\n',
})
case("rename a sub-resource id, binding intact", "allow", {"AI/cast.tres": CAST.replace("Resource_a", "Resource_zz")})
case("rename a file with a small edit", "allow", {
    "AI/other.tres": None, "AI/other_renamed.tres": OTHER.replace('"other"', '"other2"')})
case("delete a scripted sub-resource in a non-ASCII path", "allow", {"AI/Dür.tres": without_sections(CAST, "Resource_b")})
case("change a plain value", "allow", {"AI/plain.tres": PLAIN.replace("1", "2")})
case("rebind a section to a different script", "allow", {"AI/other.tres": OTHER.replace("Wrap.cs", "Wrap2.cs")})

# ---- blocked: a section keeps its header and loses the script ----
case("editor strip: script line replaced, ext_resource kept", "block", {"AI/cast.tres": STRIPPED_A})
case("editor strip: script line replaced, its ext_resource dropped too", "block", {
    "AI/other.tres": OTHER.replace('[ext_resource type="Script" path="res://Wrap.cs" id="1_wrap"]\n\n', "").replace(
        'script = ExtResource("1_wrap")', 'resource_local_to_scene = ""'),
})
case("editor strip in a non-ASCII path", "block", {"AI/Dür.tres": STRIPPED_A})
case("editor strip: script line deleted outright", "block", {"AI/cast.tres": CAST.replace('script = ExtResource("1_cond")\nKey = "a"', 'Key = "a"')})
case("editor strip while every ext_resource id is renamed", "block", {
    "AI/cast.tres": STRIPPED_A.replace("1_cond", "9_zzz").replace("2_wrap", "8_yyy"),
})
case("editor strip beside an unrelated binding added in the same file", "block", {
    "AI/cast.tres": STRIPPED_A.replace('[resource]', sub("Resource_new", "n") + '[resource]'),
})
case("editor strip beside a binding added in another file", "block", {
    "AI/cast.tres": STRIPPED_A,
    "AI/other.tres": OTHER.replace('[resource]', sub("Resource_new", "n", "1_wrap") + '[resource]'),
})
case("editor strip beside a binding to the same script added in a new file", "block", {
    "AI/cast.tres": STRIPPED_A, "AI/has.tres": STANDALONE.replace("solo", "has"),
})
case("editor strip that also renames the section id", "block", {
    "AI/cast.tres": STRIPPED_A.replace("Resource_a", "Resource_zz"),
})
case("one section extracted, another stripped, in one file", "block", {
    "AI/cast.tres": without_sections(CAST, "Resource_a").replace(
        'script = ExtResource("1_cond")\nKey = "b"', 'resource_local_to_scene = ""\nKey = "b"'),
    "AI/has.tres": STANDALONE.replace("solo", "has"),
})
case("strip in the [resource] section while a sub-resource is deleted", "block", {
    "AI/cast.tres": without_sections(CAST, "Resource_a").replace(
        '[resource]\nscript = ExtResource("2_wrap")', '[resource]\nresource_local_to_scene = ""'),
})
case("script line removed with no section above it (unattributable)", "block", {
    "AI/orphan.tres": HEADER + '[ext_resource type="Script" path="res://C.cs" id="1_x"]\n',
})


def git(repo, *args, env=None):
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env or ENV)
    if done.returncode:
        raise RuntimeError("git %s: %s" % (args, done.stderr))
    return done.stdout


def write(repo, changes):
    for rel, text in changes.items():
        path = os.path.join(repo, rel)
        if text is None:
            if os.path.exists(path):
                os.remove(path)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def build_template():
    repo = os.path.join(TMP, "template")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    base = dict(BASE)
    base["AI/orphan.tres"] = 'script = ExtResource("1_x")\n' + HEADER + '[ext_resource type="Script" path="res://C.cs" id="1_x"]\n'
    write(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def hook_decision(repo, command="git commit -m x", env_extra=None):
    payload = {"tool_name": "Bash", "cwd": repo, "tool_input": {"command": command}, "session_id": "s"}
    code, out, err, hso = call(HOOK, payload, env=dict(ENV, **(env_extra or {})), cwd=repo, argv=["--hook"])
    if code != 0 or "Traceback" in err:
        return "CRASH"
    return "deny" if hso.get("permissionDecision") == "deny" else "allow", hso


def range_decision(repo, rng="HEAD~1..HEAD"):
    code, out, err, _ = call(HOOK, "{}", env=ENV, cwd=repo, argv=["--range", rng])
    if code not in (0, 1) or "Traceback" in err:
        return "CRASH"
    return "deny" if code == 1 else "allow"


def run_cases(template):
    failures = 0
    for name, expect, changes, _ in CASES:
        repo = os.path.join(TMP, "case")
        copy_repo(template, repo)
        write(repo, changes)
        git(repo, "add", "-A")
        staged = hook_decision(repo)
        staged = staged if staged == "CRASH" else staged[0]
        git(repo, "commit", "-q", "-m", "shape")
        ranged = range_decision(repo)
        want = "allow" if expect == "allow" else "deny"
        ok = staged == want and ranged == want
        failures += 0 if ok else 1
        print("%-4s %-5s %s%s" % ("ok" if ok else "FAIL", expect, name,
                                  "" if ok else "  [staged=%s range=%s]" % (staged, ranged)))
    return failures


def mode_cases(template):
    failures = 0

    def check(name, ok, detail=""):
        nonlocal failures
        failures += 0 if ok else 1
        print("%-4s mode  %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "  [%s]" % detail))

    extract = {
        "AI/cast.tres": without_sections(CAST, "Resource_a"),
        "AI/has.tres": STANDALONE.replace("solo", "has"),
    }
    repo = os.path.join(TMP, "mode")

    # A commit index other than the default one: the staged diff is read from it.
    for label, changes, want in (("extraction", extract, "allow"), ("strip", {"AI/cast.tres": STRIPPED_A}, "deny")):
        copy_repo(template, repo)
        index = os.path.join(TMP, "alt.index")
        shutil.copyfile(os.path.join(repo, ".git", "index"), index)
        write(repo, changes)
        git(repo, "add", "-A", env=dict(ENV, GIT_INDEX_FILE=index))
        git(repo, "reset", "-q")  # the default index stays clean; only the named index carries the change
        got = hook_decision(repo, "GIT_INDEX_FILE=%s git commit -m x" % index.replace("\\", "/"))
        got = got if got == "CRASH" else got[0]
        check("GIT_INDEX_FILE commit, %s" % label, got == want, got)

    # Working-tree scan: an extraction reports nothing and repair leaves it alone; a strip is found and repaired.
    for label, changes in (("extraction", extract), ("strip", {"AI/cast.tres": STRIPPED_A})):
        copy_repo(template, repo)
        write(repo, changes)
        before = open(os.path.join(repo, "AI", "cast.tres"), encoding="utf-8").read()
        code, out, err, _ = call(HOOK, "{}", env=ENV, cwd=repo, argv=["--worktree", "--repair-inplace"])
        after = open(os.path.join(repo, "AI", "cast.tres"), encoding="utf-8").read()
        if label == "extraction":
            check("--worktree --repair-inplace leaves a moved binding where it is", code == 0 and not out.strip() and after == before,
                  "code=%s out=%r changed=%s" % (code, out[:80], after != before))
        else:
            check("--worktree --repair-inplace restores a strip", code == 0 and "restored binding" in out
                  and 'resource_local_to_scene' not in after, "code=%s out=%r" % (code, out[:120]))
    return failures


def deny_route_proof(template):
    """The deny an agent reads names the repair and the section it applies to, and offers no env bypass."""
    repo = os.path.join(TMP, "deny")
    copy_repo(template, repo)
    write(repo, {"AI/cast.tres": STRIPPED_A})
    git(repo, "add", "-A")
    got = hook_decision(repo)
    ok = got != "CRASH" and got[0] == "deny"
    reason = got[1].get("permissionDecisionReason", "") if ok else ""
    for token, present in (("--repair-inplace", True), ("Resource_a", True), ("HARNESS_ALLOW_TRES_SCRIPT_REMOVAL", False)):
        ok = ok and ((token in reason) == present)
    print("%-4s deny names the repair command and the stripped section, and no env bypass" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    template = build_template()
    failures = run_cases(template) + mode_cases(template) + deny_route_proof(template)
    shutil.rmtree(TMP, ignore_errors=True)
    print("%s: %d failure(s)" % ("FAIL" if failures else "OK", failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
