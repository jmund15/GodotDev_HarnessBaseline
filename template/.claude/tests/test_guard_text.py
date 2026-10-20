#!/usr/bin/env python3
"""Re-runnable proof for tools/guard_text.py, the one parser of delegate-guard tiers.

    python3 .claude/tests/test_guard_text.py

Planted cases copy the tool into a temp `.claude/tools/` beside planted `.claude/guards/` files
with known sections: the tool resolves `guards/` from its own path, so the exact output is known.
A live case runs the real tool on the real guard files for every shape and tier.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "guard_text.py")

PLANTED = {
    "any.md": "# any\n\n## detailed\nANY-DETAILED\n\n## condensed\nANY-CONDENSED\n",
    "review.md": "# review\n\n## detailed\nREVIEW-DETAILED\n\n## condensed\nREVIEW-CONDENSED\n",
    "survey.md": "# survey\n\n## detailed\nSURVEY-DETAILED\n",
    "author.md": "# author\n\n## detailed\nAUTHOR-DETAILED\n",
}


OVERLAYS = {
    "any.coding.md": "# any coding\n\n## detailed\nANY-CODING-DETAILED\n",
    "review.godot.md": "# review godot\n\n## detailed\nREVIEW-GODOT-DETAILED\n",
}


def planted_tool(tmp):
    tools = os.path.join(tmp, ".claude", "tools")
    guards = os.path.join(tmp, ".claude", "guards")
    os.makedirs(tools)
    os.makedirs(guards)
    shutil.copyfile(TOOL, os.path.join(tools, "guard_text.py"))
    for name, text in PLANTED.items():
        with open(os.path.join(guards, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return os.path.join(tools, "guard_text.py")


def run(tool, *args, env=None):
    r = subprocess.run([sys.executable, tool, *args], capture_output=True, timeout=30, env=env)
    # Text-mode stdout on Windows writes CRLF; the delivered rails are compared as text.
    return (r.returncode, r.stdout.decode("utf-8").replace("\r\n", "\n"),
            r.stderr.decode("utf-8").replace("\r\n", "\n"))


def profile_env(tier, ai_worker):
    """An environment where this machine's tier and ai-worker registration read as planted."""
    root = tempfile.mkdtemp(prefix="guard_text_profile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=cfg, SC_WORKDIR=os.path.join(root, "nowhere"),
               WORKSTATION_PROFILE_PATH=os.path.join(root, "workstation_profile.json"))
    if tier is not None:
        with open(env["WORKSTATION_PROFILE_PATH"], "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    return env


def main():
    cases = []
    with tempfile.TemporaryDirectory(prefix="guard_text_") as tmp:
        tool = planted_tool(tmp)

        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("a shape gets the `any` section, then its own, under one header",
                      rc == 0 and out == "[delegate rails — shape 'review', detailed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/review.md]\nANY-DETAILED\n\nREVIEW-DETAILED"))
        rc, out, _ = run(tool, "any", "detailed")
        cases.append(("shape `any` gets its section alone",
                      rc == 0 and out == "[delegate rails — shape 'any', detailed tier; home: .claude/guards/any.md]\nANY-DETAILED"))
        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("a section stops at the next heading", "CONDENSED" not in out))
        rc, out, _ = run(tool, "review", "none")
        cases.append(("tier `none` delivers the empty string", rc == 0 and out == ""))
        rc, out, err = run(tool, "survey", "condensed")
        cases.append(("a missing section exits 2 and names it", rc == 2 and out == "" and "no '## condensed' section" in err))
        rc, _, err = run(tool, "planner", "detailed")
        cases.append(("an unknown shape exits 2 with the legal set", rc == 2 and "any, survey, review, author" in err))
        rc, _, err = run(tool, "review", "loud")
        cases.append(("an unknown tier exits 2 with the legal set", rc == 2 and "detailed, condensed, minimal, none" in err))
        rc, _, err = run(tool, "review", "strict")
        cases.append(("a retired tier name exits 2", rc == 2))
        rc, _, err = run(tool, "review")
        cases.append(("a wrong argument count exits 2 with usage", rc == 2 and "usage: guard_text.py" in err))

    with tempfile.TemporaryDirectory(prefix="guard_text_overlay_") as tmp:
        tool = planted_tool(tmp)
        for name, text in OVERLAYS.items():
            with open(os.path.join(tmp, ".claude", "guards", name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out, _ = run(tool, "review", "detailed")
        cases.append(("each present layer overlay follows its base section, listed in the header",
                      rc == 0 and out == "[delegate rails — shape 'review', detailed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/any.coding.md + .claude/guards/review.md"
                                        " + .claude/guards/review.godot.md]\nANY-DETAILED\n\nANY-CODING-DETAILED"
                                        "\n\nREVIEW-DETAILED\n\nREVIEW-GODOT-DETAILED"))
        rc, out, _ = run(tool, "review", "condensed")
        cases.append(("an overlay without the tier adds nothing",
                      rc == 0 and out == "[delegate rails — shape 'review', condensed tier; home: .claude/guards/any.md"
                                        " + .claude/guards/review.md]\nANY-CONDENSED\n\nREVIEW-CONDENSED"))

    live = []
    for shape in ("any", "survey", "review", "author"):
        for tier in ("detailed", "condensed", "minimal"):
            rc, out, err = run(TOOL, shape, tier)
            if rc != 0 or not out.startswith("[delegate rails — shape '%s', %s tier;" % (shape, tier)):
                live.append("%s/%s rc=%d %s" % (shape, tier, rc, err.strip()[:80]))
    cases.append(("the real guards deliver every shape at every tier" + ("" if not live else ": " + "; ".join(live)),
                  not live))

    # C7: the rails name a tool that a machine without ai-worker does not have, so the clause is
    # rendered as the route that machine does have. Unknown (no profile, unreadable walk) keeps it.
    for tier, ai_worker, label, ok in (
        ("full", "absent", "an absent ai-worker renders the ai-worker clause as the local route",
         lambda out: "mcp__ai-worker__" not in out and "bounded Read or a subagent" in out),
        ("full", "present", "a present ai-worker keeps the clause's tool name",
         lambda out: "mcp__ai-worker__read_files" in out and "bounded Read or a subagent" not in out),
        (None, "present", "no profile keeps the clause's tool name",
         lambda out: "mcp__ai-worker__read_files" in out),
    ):
        rc, out, _ = run(TOOL, "any", "detailed", env=profile_env(tier, ai_worker))
        cases.append((label, rc == 0 and ok(out)))
    rc, out, _ = run(TOOL, "any", "condensed", env=profile_env("full", "absent"))
    cases.append(("the condensed tier's ai-worker clause is rendered the same way",
                  rc == 0 and "mcp__ai-worker__" not in out and "bounded Read or a subagent" in out))

    sys.path.insert(0, os.path.dirname(TOOL))
    import guard_text
    redirected = []
    guards = os.path.join(os.path.dirname(os.path.dirname(TOOL)), "guards")
    for name in sorted(os.listdir(guards)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(guards, name)
        with open(path, encoding="utf-8") as fh:
            if "Read this file's `## condensed` section." not in fh.read():
                continue
        shape = name.split(".")[0]
        rc, out, _ = run(TOOL, shape, "minimal")
        if "Read this file" in out or guard_text._section(path, "condensed") not in out:
            redirected.append(name)
    cases.append(("a minimal section that points at another section delivers that section's text"
                  + ("" if not redirected else ": " + ", ".join(redirected)), not redirected))

    # The launcher's call shape: lib/sidecar_common.sh passes the child's model as a third argument
    # so the isolated tiers get that model's registry driver notes after the rails. A planted
    # registry keeps the proof off the live roster.
    reg_dir = tempfile.mkdtemp()
    registry = os.path.join(reg_dir, "registry.json")
    live_path = os.path.join(os.path.dirname(os.path.dirname(TOOL)), "reference", "external_models.json")
    with open(live_path, encoding="utf-8") as fh:
        live = json.load(fh)
    template = next(m for m in live["models"] if m.get("transport") in live.get("transports", {}))
    planted = []
    for order, (name, notes) in enumerate((("noted", [{"text": "PLANTED-NOTE", "evidenceRef": "planted",
                                                       "weakness": "PLANTED-WEAKNESS",
                                                       "removeWhen": "PLANTED-REMOVE-WHEN"}]),
                                           ("plain", None))):
        row = dict(template, id="vendor-%s-1" % name, alias="planted-%s" % name, family="planted-%s" % name,
                   order=order)
        row.pop("servedIds", None)
        row.pop("driverNotes", None)
        if notes:
            row["driverNotes"] = notes
        planted.append(row)
    live["models"] = planted
    live.pop("aliasHistory", None)
    for tcfg in live.get("transports", {}).values():
        tcfg.pop("driverNotes", None)
    with open(registry, "w", encoding="utf-8") as fh:
        json.dump(live, fh)
    env = dict(os.environ, HARNESS_MODEL_REGISTRY=registry)
    rc2, plain, _ = run(TOOL, "review", "detailed", env=env)
    rc3, noted, err3 = run(TOOL, "review", "detailed", "vendor-noted-1", env=env)
    cases.append(("the launcher's (shape, tier, model) call exits 0", rc3 == 0 and not err3.startswith("usage")))
    cases.append(("a model's registry driver notes follow the rails", noted.startswith(plain) and "PLANTED-NOTE" in noted))
    rc4, bare, _ = run(TOOL, "review", "detailed", "vendor-plain-1", env=env)
    cases.append(("a model with no driver notes gets the rails unchanged", rc4 == 0 and bare == plain))
    rc5, none_out, _ = run(TOOL, "review", "none", "vendor-noted-1", env=env)
    cases.append(("tier none suppresses shape rails, not model corrections",
                  rc5 == 0 and "[delegate rails" not in none_out and "PLANTED-NOTE" in none_out))

    # Caller contract: run the invocation lib/sidecar_common.sh actually writes, so an argument
    # added on either side of the shell/Python boundary fails here instead of at dispatch.
    common = os.path.join(os.path.dirname(os.path.dirname(TOOL)), "scripts", "lib", "sidecar_common.sh")
    with open(common, encoding="utf-8") as fh:
        calls = [line.split("guard_text.py\"", 1)[1].split(">", 1)[0].split()
                 for line in fh if 'tools/guard_text.py"' in line]
    values = {'"$shape"': "review", '"$SC_SHAPE"': "review", '"$SC_MODEL"': "vendor-noted-1"}
    argv = [[values.get(a, a.strip('"')) for a in call] for call in calls]
    results = [run(TOOL, *args, env=env) for args in argv]
    cases.append(("every guard_text.py call in sidecar_common.sh runs clean (%d found)" % len(argv),
                  bool(argv) and all(rc == 0 and "PLANTED-NOTE" in out for rc, out, _ in results)))

    import glob
    import re
    unconditioned = []
    for gpath in sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(TOOL)), "guards", "*.md"))):
        with open(gpath, encoding="utf-8") as fh:
            for line in fh:
                for sentence in re.split(r"(?<=[.;])\s+", line):
                    if re.search(r"\bSTOP\b", sentence) and re.search(r"single-flight|test or build run", sentence) \
                            and not re.search(r"concurren", sentence, re.I):
                        unconditioned.append("%s: %s" % (os.path.basename(gpath), sentence.strip()[:90]))
    cases.append(("every STOP on a single-flight run names its concurrency condition in the same sentence"
                  + ("" if not unconditioned else ": " + " | ".join(unconditioned)), not unconditioned))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
