#!/usr/bin/env python3
"""Re-runnable proof for tools/workstation_profile.py: the recorded GPU tier, the live ai-worker
read, the setup stamp SessionStart compares, and the layer seam `preflight` reads.

Everything is planted: no `nvidia-smi`, no write inside `~/.claude` (the profile path is redirected
with WORKSTATION_PROFILE_PATH, the MCP config walk with CLAUDE_CONFIG_DIR and SC_WORKDIR). The one
real subprocess is `git`, for the main-checkout/worktree keying. The core runs alone in a temp
`tools/` for the seam cases, so they prove what a consumer without the engine layer receives.

    python3 .claude/tests/test_workstation_profile.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
TOOLS = CLAUDE / "tools"
sys.path.insert(0, str(TOOLS))
import workstation_profile as ws  # noqa: E402

FAILURES = []
LAYER_MODULE = "workstation_profile_" + "godot"  # the sibling the core's seam reads; spelled in parts so this pure proof cites no file of a higher layer


# ---------------------------------------------------------------- the case harness

def case(label, fn):
    """Run one case; an exception is a FAIL naming its type, never a crash of the whole proof."""
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a proof reports, it does not stop
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s%s" % (label, (" -- " + str(detail)[:200]) if detail else ""))


def eq(got, want):
    return got == want, "got %r, want %r" % (got, want)


@contextlib.contextmanager
def patched_env(values):
    """Set the named variables; a None value removes the variable for the block."""
    saved = {k: os.environ.get(k) for k in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def patched(obj, name, value):
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


class Probe:
    """A planted `nvidia-smi` reply."""

    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def probe(stdout, fn, returncode=0):
    """Call `fn` with `subprocess.run` planted to answer a probe; a None stdout raises OSError."""
    def fake(*_args, **_kwargs):
        if stdout is None:
            raise FileNotFoundError("no such command")
        return Probe(stdout, returncode)
    with patched(ws.subprocess, "run", fake):
        return fn()


def cli(argv):
    """(exit code, printed text) for one CLI call."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ws.main(argv)
    return code, out.getvalue()


# ---------------------------------------------------------------- fixtures

DECLARED = (".mcp.json", ".claude/reference/project_stack.md", "pins.json",
            ".claude/commands/workstation_setup.md")
PROJECT_STACK = "# Project Stack Pins\n\n- Runtime: **1.0**\n"
MCP_JSON = '{"mcpServers": {"x": {}}}\n'
PINS_JSON = '{"pin": "1.0"}\n'


def stamp_doc(paths=DECLARED, body="stamp-inputs:", front=True):
    """A `commands/workstation_setup.md` carrying the declaration the tool reads."""
    lines = ["---", "description: Provision or audit this harness on a workstation"]
    if body is not None:
        lines.append(body)
        lines.extend('  - "%s"' % p for p in paths)
    lines += ["---", "", "# /workstation_setup", ""]
    return "\n".join(lines) if front else "no frontmatter at all\n"


def reference_doc(name, paths=None, body="stamp-inputs:"):
    """A `commands/reference/<name>` file; `paths` None leaves the declaration out."""
    lines = ["---", "disable-model-invocation: true"]
    if paths is not None:
        lines.append(body)
        lines.extend('  - "%s"' % p for p in paths)
    lines += ["---", "", "# a layer reference", ""]
    return name, "\n".join(lines)


def plant_root(base, doc=None, inputs=DECLARED, references=()):
    """A repo root carrying the declared stamp inputs. Returns the root."""
    root = Path(base)
    commands = root / ".claude" / "commands"
    (commands / "reference").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "reference").mkdir(parents=True, exist_ok=True)
    (commands / "workstation_setup.md").write_text(stamp_doc(inputs) if doc is None else doc, encoding="utf-8")
    for name, text in references:
        (commands / "reference" / name).write_text(text, encoding="utf-8")
    (root / ".claude" / "reference" / "project_stack.md").write_text(PROJECT_STACK, encoding="utf-8")
    (root / ".mcp.json").write_text(MCP_JSON, encoding="utf-8")
    (root / "pins.json").write_text(PINS_JSON, encoding="utf-8")
    return root


def git_fixture(base):
    """A real main checkout plus a linked worktree of it. Returns (main, worktree)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "proof",
                "GIT_AUTHOR_EMAIL": "proof@example.invalid", "GIT_COMMITTER_NAME": "proof",
                "GIT_COMMITTER_EMAIL": "proof@example.invalid"})

    def git(cwd, *args):
        result = subprocess.run(["git"] + list(args), cwd=str(cwd), env=env,
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], result.stderr.strip()))
        return result.stdout

    main_co = Path(base) / "main"
    main_co.mkdir(parents=True)
    git(main_co, "init", "-q")
    git(main_co, "commit", "-q", "--allow-empty", "-m", "init")
    worktree = Path(base) / "wt"
    git(main_co, "worktree", "add", "-q", "--detach", str(worktree))
    return main_co, worktree


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def load_core_alone(base, sibling_source=None):
    """`workstation_profile.py` copied into a `tools/` of its own and loaded from there. With
    `sibling_source`, that text is the installed layer module beside it. Returns
    the module, which sees exactly the files a consumer of this layer set would hold."""
    directory = Path(base) / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy(TOOLS / "workstation_profile.py", directory / "workstation_profile.py")
    if sibling_source is not None:
        (directory / (LAYER_MODULE + ".py")).write_text(sibling_source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("wp_alone_%d" % len(list(Path(base).iterdir())),
                                                  directory / "workstation_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- cases

def tier_cases(tmp, state):
    for mib, want in ((16376, "full"), (16384, "full"), (15360, "full"), (15359, "small"),
                      (8188, "small"), (8192, "small"), (7680, "small"), (7679, "none"),
                      (4096, "none"), (0, "none"), (None, "none")):
        case("%r MiB -> %s" % (mib, want), lambda mib=mib, want=want: eq(ws.gpu_tier(mib), want))

    case("read_gpu parses the live nvidia-smi CSV row",
         lambda: eq(probe("NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB\n", ws.read_gpu),
                    ("NVIDIA GeForce RTX 4070 Ti SUPER", 16376)))
    case("read_gpu returns no GPU on a failed probe",
         lambda: eq(probe("", ws.read_gpu, returncode=9), (None, None)))
    case("read_gpu survives a missing nvidia-smi",
         lambda: eq(probe(None, ws.read_gpu), (None, None)))

    def default_path():
        saved = os.environ.pop(ws.PROFILE_ENV, None)
        try:
            return eq(ws.profile_path(), os.path.expanduser("~/.claude/workstation_profile.json"))
        finally:
            if saved is not None:
                os.environ[ws.PROFILE_ENV] = saved

    case("the default profile path is ~/.claude/workstation_profile.json", default_path)


def declaration_cases(tmp, state):
    root = plant_root(tmp / "repo")
    state["root"] = root
    case("stamp_inputs reads the declared list in order",
         lambda: eq(ws.stamp_inputs(root), list(DECLARED)))

    spaced = plant_root(tmp / "repo_spaced",
                        doc=stamp_doc(DECLARED).replace('  - "pins.json"', '\n  - "pins.json"'))
    case("a blank line inside the declaration does not truncate the list",
         lambda: eq(ws.stamp_inputs(spaced), list(DECLARED)))

    other = plant_root(tmp / "repo_other", doc=stamp_doc(("MADE-UP.md",)), inputs=("MADE-UP.md",))
    write(other / "MADE-UP.md", "x\n")
    case("a different declaration is what the tool reads, not a hard-coded copy",
         lambda: eq(ws.stamp_inputs(other), ["MADE-UP.md"]))

    variants = {
        "a missing declaration": stamp_doc(body=None),
        "a declaration with no entries": "---\nstamp-inputs:\n---\nbody\n",
        "a missing frontmatter block": stamp_doc(front=False),
        "an inline scalar declaration": stamp_doc(body="stamp-inputs: .mcp.json"),
    }
    for i, (label, doc) in enumerate(variants.items()):
        bad = plant_root(tmp / ("bad%d" % i), doc=doc)
        case("%s declares no inputs" % label, lambda bad=bad: eq(ws.stamp_inputs(bad), None))


def reference_cases(tmp, state):
    extra = reference_doc("workstation_setup_extra.md", ["extra.json"])
    root = plant_root(tmp / "with_ref", references=[extra])
    case("a reference's declaration joins the command's, after it",
         lambda: eq(ws.stamp_inputs(root), list(DECLARED) + ["extra.json"]))

    repeated = reference_doc("workstation_setup_extra.md", [".mcp.json", "extra.json"])
    case("an input declared twice is listed once, at its first place",
         lambda: eq(ws.stamp_inputs(plant_root(tmp / "dup", references=[repeated])),
                    list(DECLARED) + ["extra.json"]))

    silent = reference_doc("workstation_setup_silent.md", None)
    case("a reference with no declaration adds nothing",
         lambda: eq(ws.stamp_inputs(plant_root(tmp / "silent", references=[silent])), list(DECLARED)))

    stranger = reference_doc("unrelated_notes.md", ["stranger.json"])
    case("a file outside the workstation_setup_ naming is not read",
         lambda: eq(ws.stamp_inputs(plant_root(tmp / "stranger", references=[stranger])), list(DECLARED)))

    for label, bad in (("an empty", reference_doc("workstation_setup_bad.md", [])),
                       ("an inline scalar", reference_doc("workstation_setup_bad.md", [],
                                                          body="stamp-inputs: x.json"))):
        case("%s reference declaration reads as no inputs at all" % label,
             lambda bad=bad, label=label: eq(
                 ws.stamp_inputs(plant_root(tmp / ("badref_" + label.split()[-1]), references=[bad])), None))

    write(root / "extra.json", "{}\n")
    ws.stamp(root, preflight_failures=[])
    case("a stamped root with a reference input reads OK", lambda: eq(ws.setup_status(root), "OK"))
    write(root / "extra.json", '{"changed": true}\n')
    case("an edited reference input reads STALE and names it",
         lambda: eq(ws.setup_status(root), "STALE (changed: extra.json; run /workstation_setup)"))


def stamp_cases(tmp, state):
    root = state["root"]
    case("the CLI's default root is the repository: its stamp-inputs declaration resolves",
         lambda: (ws.stamp_inputs(ws._repo_root()) is not None, ws._repo_root()))
    case("stamp refuses with no readable declaration",
         lambda: eq(ws.stamp(plant_root(tmp / "nodecl", doc=stamp_doc(body=None)),
                             preflight_failures=[])[0], False))

    refused = ws.stamp(root, preflight_failures=["toolchain is missing"])
    case("stamp refuses on a failed preflight and records nothing",
         lambda: (not refused[0] and "refusing" in refused[1]
                  and ws.setup_status(root) != "OK", refused))

    case("a stamped root reports OK",
         lambda: (lambda r: (r[0] and ws.setup_status(root) == "OK", r))(
             ws.stamp(root, preflight_failures=[])))

    write(root / ".mcp.json", '{"mcpServers": {}}\n')
    case("an edited input reads STALE and names it",
         lambda: eq(ws.setup_status(root), "STALE (changed: .mcp.json; run /workstation_setup)"))

    (root / "pins.json").unlink()
    case("a missing declared input reads STALE and names it",
         lambda: eq(ws.setup_status(root),
                    "STALE (changed: .mcp.json, pins.json; run /workstation_setup)"))

    write(root / "pins.json", PINS_JSON)
    case("stamp again clears the stale report",
         lambda: (lambda r: (r[0] and ws.setup_status(root) == "OK", r))(
             ws.stamp(root, preflight_failures=[])))

    write(root / ".claude" / "commands" / "workstation_setup.md", stamp_doc(body="stamp-inputs: .mcp.json"))
    case("a broken declaration reads UNAUDITED even with a recorded entry",
         lambda: eq(ws.setup_status(root), "UNAUDITED (run /workstation_setup)"))
    write(root / ".claude" / "commands" / "workstation_setup.md", stamp_doc())
    case("restoring the declaration restores OK", lambda: eq(ws.setup_status(root), "OK"))

    unstamped = plant_root(tmp / "unstamped")
    case("an unstamped root reads UNAUDITED",
         lambda: eq(ws.setup_status(unstamped), "UNAUDITED (run /workstation_setup)"))
    case("a checkout without /workstation_setup has no setup status",
         lambda: eq(ws.setup_status(tmp / "no_such_checkout"), None))

    write(state["profile"], "{not json at all")
    case("a corrupt profile is not a crash: UNAUDITED, tier None, empty read",
         lambda: (ws.setup_status(root) == "UNAUDITED (run /workstation_setup)"
                  and ws.local_tier() is None and ws.read_profile() == {},
                  (ws.setup_status(root), ws.local_tier())))
    state["profile"].unlink()


def worktree_cases(tmp, state):
    try:
        main_co, worktree = git_fixture(tmp / "gitbase")
    except Exception as exc:  # noqa: BLE001 - a git fixture that cannot build is reported
        case("a linked worktree keys to its main checkout", lambda: (False, repr(exc)))
        return
    keys = (ws.profile_key(main_co), ws.profile_key(worktree))
    case("a linked worktree keys to its main checkout", lambda: eq(keys[0], keys[1]))
    plant_root(main_co)
    plant_root(worktree)
    ws.stamp(main_co, preflight_failures=[])
    case("a worktree of the stamped checkout reads OK",
         lambda: eq(ws.setup_status(worktree), "OK"))


ENGINE_ROOT_FILES = {"project.godot": "; planted\n",
                     "nuget.config": '<configuration><packageSources><add key="l" value="feed_dir"/>'
                                     "</packageSources></configuration>"}


def seam_cases(tmp, state):
    """`preflight` is the core's seam: it runs only the checks a sibling layer module supplies."""
    engine_tree = plant_root(tmp / "engine_tree")
    for name, text in ENGINE_ROOT_FILES.items():
        write(engine_tree / name, text)
    bare_env = {"GODOT_BIN": None}

    core = load_core_alone(tmp / "core_only")
    with patched_env(bare_env):
        case("a core with no layer module runs no engine, .NET or feed check on an engine tree",
             lambda: eq(core.preflight(engine_tree), []))
    case("the core carries no engine project probe", lambda: eq(hasattr(core, "godot_project"), False))
    case("the core carries no local-feed reader", lambda: eq(hasattr(core, "local_nuget_feeds"), False))
    case("the core carries no command probe for a toolchain", lambda: eq(hasattr(core, "_cmd_exists"), False))

    layered = load_core_alone(tmp / "core_plus_layer", "def preflight(root):\n    return ['planted layer failure']\n")
    case("a layer module beside the core supplies the preflight failures",
         lambda: eq(layered.preflight(engine_tree), ["planted layer failure"]))

    broken = load_core_alone(tmp / "core_plus_broken", "def preflight(root:\n")
    failures = broken.preflight(engine_tree)
    case("a layer module that cannot load refuses the stamp rather than passing it",
         lambda: (len(failures) == 1 and LAYER_MODULE in failures[0]
                  and "load" in failures[0], failures))

    headless = load_core_alone(tmp / "core_plus_headless", "VALUE = 1\n")
    failures = headless.preflight(engine_tree)
    case("a layer module with no preflight refuses the stamp rather than passing it",
         lambda: (len(failures) == 1 and LAYER_MODULE in failures[0], failures))


def ai_worker_cases(tmp, state):
    cfg = tmp / "cfg"
    (cfg / "plugins").mkdir(parents=True)

    def walk():
        with patched_env({"CLAUDE_CONFIG_DIR": str(cfg), "SC_WORKDIR": str(tmp / "nowhere")}):
            return ws.ai_worker()

    write(cfg / ".claude.json", json.dumps({"mcpServers": {"other": {}}}))
    case("ai_worker is absent without a registration", lambda: eq(walk(), "absent"))

    write(cfg / ".claude.json", json.dumps({"mcpServers": {"ai-worker": {"command": "python"}}}))
    case("ai_worker is present with a user-scope registration", lambda: eq(walk(), "present"))

    write(cfg / ".claude.json", json.dumps(
        {"projects": {str(tmp / "nowhere"): {"mcpServers": {"ai-worker": {"command": "python"}}}}}))
    case("ai_worker is present from the project scope the walk reads", lambda: eq(walk(), "present"))

    write(cfg / ".claude.json", "{not json")
    case("an unreadable registration file reads absent, never present",
         lambda: eq(walk(), "absent"))

    def unloadable():
        with patched(ws, "_tool_profiles", _raise):
            return eq(ws.ai_worker(), None)

    case("a config walk that cannot run is None, not absent", unloadable)


def _raise(*_args, **_kwargs):
    raise RuntimeError("tool_profiles unavailable")


def record_cases(tmp, state):
    before = ws.read_profile().get("setup")
    case("record probes the machine and writes the tier",
         lambda: (lambda _: (ws.read_profile().get("local_tier") == "full"
                             and ws.read_profile().get("gpu") == {
                                 "name": "NVIDIA GeForce RTX 4070 Ti SUPER", "vram_mib": 16376}
                             and ws.local_tier() == "full", ws.read_profile()))(
             probe("NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB\n", ws.record)))
    case("record keeps the ai-worker observation the caller names",
         lambda: (lambda _: (ws.read_profile().get("ai_worker") == "absent", ws.read_profile()))(
             ws.record(ai_worker_value="absent")))
    case("a machine with no GPU records tier none",
         lambda: (lambda _: (ws.local_tier() == "none", ws.read_profile()))(
             probe(None, ws.record)))
    case("record leaves the setup stamp alone",
         lambda: (before and ws.read_profile().get("setup") == before,
                  (before, ws.read_profile().get("setup"))))


def cli_cases(tmp, state):
    root = state["root"]
    case("the CLI record exits 0 and writes a tier",
         lambda: eq((cli(["record", "--ai-worker", "absent"])[0], ws.local_tier() in ws.TIERS),
                    (0, True)))
    with patched(ws, "preflight", lambda _root: []):
        code, out = cli(["stamp", "--root", str(root), "--degraded", "WSL"])
        case("the CLI stamp exits 0", lambda: eq(code, 0))
        code, out = cli(["status", "--root", str(root)])
        case("the CLI status prints the recorded status",
             lambda: eq((code, out.strip()), (0, "OK")))
        case("the CLI stamp records the degraded rows it is given",
             lambda: eq(ws.read_profile()["setup"][ws.profile_key(root)].get("degraded"), ["WSL"]))
        code, out = cli(["stamp", "--root", str(root)])
        case("a later stamp keeps the record readable",
             lambda: eq((code, ws.setup_status(root)), (0, "OK")))
    with patched(ws, "preflight", lambda _root: ["toolchain is missing"]):
        code, out = cli(["stamp", "--root", str(root)])
        case("the CLI stamp refuses with a non-zero exit and says why",
             lambda: (code != 0 and "toolchain is missing" in out, (code, out)))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wsp_"))
    state = {"profile": tmp / "profile.json"}
    os.environ[ws.PROFILE_ENV] = str(state["profile"])

    try:
        tier_cases(tmp, state)
        declaration_cases(tmp, state)
        reference_cases(tmp, state)
        stamp_cases(tmp, state)
        worktree_cases(tmp, state)
        seam_cases(tmp, state)
        ai_worker_cases(tmp, state)
        record_cases(tmp, state)
        cli_cases(tmp, state)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print("\nFAILED:\n  " + "\n  ".join(FAILURES))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
