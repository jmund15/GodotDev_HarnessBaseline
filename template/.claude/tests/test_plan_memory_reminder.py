#!/usr/bin/env python3
"""Regression proof for the registered PostToolUse plan-memory hook."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

import _settings_probe

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASENAME = "plan_memory_reminder.py"


def _project_domains():
    """The project's own `memory_domains` rows, which the hook appends to its floor table.

    The cases that need a project content domain are skipped when the project carries
    none, so the same proof runs in a checkout without one.
    """
    path = os.path.join(ROOT, ".claude", "skills", "project_subsystems", "adaptation.json")
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh).get("memory_domains") or []
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("triggers")]


DOMAINS = _project_domains()


def _trigger(row):
    return row["triggers"][0]


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()


def meta_plan(row=None):
    """A harness-only plan whose prose names the project's own domain keyword."""
    game_words = f"{_trigger(row)} systems, gameplay, or authored game data" if row else "gameplay or authored game data"
    return f"""# Harness status and return routing

This plan updates how AI tools report statuses and returns. The change is entirely
inside the Claude harness. It does not alter {game_words}. The implementation keeps
existing output channels, atomic state, bounded lists, tests, ownership, and failure
reporting. Review focuses on hook payload provenance and honest state names. Every
result remains attributable and missing evidence stays unknown rather than green.

## Critical files

`.claude/hooks/plan_memory_reminder.py`, `.claude/hooks/prompt_memory_loader.py`,
`.claude/tool_routing.py`, `.claude/tests/test_plan_memory_reminder.py`.
These AI tools expose statuses and returns to the harness only.
"""


def game_plan(row):
    trigger = _trigger(row)
    return f"""# {trigger} behavior

Implement the game behavior with deterministic coverage and retain the authored data
contract. The {trigger} behavior applies through the runtime system while the entity
is active. Tests cover activation, transition, expiry, and visible gameplay outcome.
The work remains within the named game systems and does not alter harness routing.
Review the existing abstractions before implementation and keep the behavior loosely
coupled to delivery.

## Critical files

`game/{_slug(trigger)}/ThingEffect.cs`, `game/{_slug(trigger)}/ThingEffectTests.cs`.
The {trigger} behavior is production game scope.
"""


def mixed_plan(row):
    trigger = _trigger(row)
    return f"""# Mixed harness and {trigger} scope

Update the plan reminder and then add the game behavior that proves mixed scope remains
visible. The harness portion changes classification only. The game portion wires one
{trigger} behavior into the runtime and retains its behavioral test. Keep both scopes
explicit, preserve output channels, and do not infer completion from a reminder. The
plan is intentionally mixed so an all-meta shortcut would be a false negative.

## Critical files

`.claude/hooks/plan_memory_reminder.py`, `.claude/tests/test_plan_memory_reminder.py`,
`game/{_slug(trigger)}/ThingEffect.cs` — the {trigger} behavior is real production
scope beside the harness fix.
"""


def mixed_nonwhitelist_path(row, path):
    trigger = _trigger(row)
    return f"""# Mixed harness and game asset scope

The plan changes one harness reminder and one game artifact. The game path is real scope,
so the hook must infer its {trigger} domain from the files section. Keep the two changes
separate and retain focused proof for each. This fixture has enough body text to pass the
minimum plan size without relying on prose outside the files section for scope. The
non-harness path uses a file class that the old extension list missed.

## Critical files

`.claude/hooks/plan_memory_reminder.py`, `{path}` — the {trigger} artifact is real scope.
"""


def meta_no_section(row):
    trigger = _trigger(row)
    return f"""# Harness dispatcher consolidation

Consolidate the per-event hook chains so one interpreter runs them in order. The work
covers `.claude/hooks/pre_edit_dispatch.py` and `.claude/hooks/post_edit_dispatch.py`,
their proofs in `.claude/tests/test_pre_edit_dispatch.py`, and the registration in
`.claude/settings.json`. Refactor the status reporting so a {trigger} advisory keeps
its channel: pooling the spawn of eleven processes into one is the whole point, and
each returned status stays attributable to the sub-hook that produced it.
"""


def game_no_section(row):
    trigger = _trigger(row)
    return f"""# {trigger} behavioral delivery

Wire the new {trigger} behavior into the runtime so the system applies it on contact
and the tick runner expires it. The change lands in `game/{_slug(trigger)}/ThingEffect.cs`
and `game/{_slug(trigger)}/ThingEffectTests.cs`. Keep the effect loosely coupled to
delivery and review the existing abstractions before adding a new one.
"""


def mixed_no_section(row):
    trigger = _trigger(row)
    return f"""# Reminder fix beside a real {trigger} change

Fix the plan reminder in `.claude/hooks/plan_memory_reminder.py` and, in the same drive,
add the {trigger} behavior that proves the mixed case stays visible. The game portion
lands in `game/{_slug(trigger)}/ThingEffect.cs`, so an all-meta shortcut here would be
a false negative. Keep both scopes explicit and preserve the existing output channels.
"""


def registered_hook():
    """Path to the hook, once its registration is proved through the dispatcher.

    The hook reaches PostToolUse Write|Edit inside `post_edit_dispatch.py` rather than as
    its own settings.json command, so wiring is two facts: the dispatcher is registered,
    and this hook is in its chain. The cases below then drive the hook's own `main()`,
    which is the same code path the dispatcher's `process()` feeds.
    """
    settings = _settings_probe.settings(os.path.join(ROOT, ".claude"))
    registered = any(
        "post_edit_dispatch.py" in hook.get("command", "")
        for group in settings["hooks"]["PostToolUse"]
        if "Write" in group.get("matcher", "")
        for hook in group.get("hooks", [])
    )
    if not registered:
        raise AssertionError("post_edit_dispatch.py is not registered for PostToolUse Write")
    dispatcher = os.path.join(ROOT, ".claude", "hooks", "post_edit_dispatch.py")
    spec = importlib.util.spec_from_file_location("post_edit_dispatch_plan_probe", dispatcher)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not any(entry[0] == BASENAME[:-3] for entry in module.CHAIN):
        raise AssertionError(f"{BASENAME} is not in the post_edit_dispatch chain")
    return os.path.join(ROOT, ".claude", "hooks", BASENAME)


def run(hook, content, path, session, env):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "session_id": session,
        "tool_input": {"file_path": path, "content": content},
        "tool_response": {"filePath": path},
    }
    result = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=30, env=env)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def context(output):
    if not output or output == "{}":
        return ""
    payload = json.loads(output)
    specific = payload.get("hookSpecificOutput") or {}
    assert specific.get("hookEventName") == "PostToolUse"
    return specific.get("additionalContext") or ""


def edited_section(hook, fragment, path, session, env):
    """An Edit that rewrites ONE section of a plan that already exists on disk."""
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "session_id": session,
        "tool_input": {"file_path": path, "old_string": "placeholder", "new_string": fragment},
        "tool_response": {"filePath": path},
    }
    result = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=30, env=env)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return context(result.stdout.strip())


def main():
    hook = registered_hook()
    tmp = tempfile.mkdtemp(prefix="plan_memory_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")
    failures = []
    cases = []
    plan_path = os.path.join(ROOT, ".claude", "plans", "a3-meta-fixture.md")

    # A project content domain comes from adaptation.json; a checkout without one skips
    # the domain cases rather than asserting another project's nouns.
    row = DOMAINS[0] if DOMAINS else None
    if row:
        meta = context(run(hook, meta_plan(row), plan_path, "pmrmeta1", env))
        cases.append(("meta-only critical files suppress game-domain reminders", meta == ""))

        game = context(run(hook, game_plan(row),
                           os.path.join(ROOT, ".claude", "plans", "a3-game-fixture.md"),
                           "pmrgame1", env))
        cases.append(("game scope stays positive", row["name"] in game))
        cases.append(("positive reminder uses PostToolUse additionalContext", bool(game)))

        for index, game_path in enumerate((
            "game/assets/arc.gdshader",
            "GameApp.csproj",
            "art/entity.png",
            "game/assets/",
        )):
            reminder = context(run(
                hook,
                mixed_nonwhitelist_path(row, game_path),
                os.path.join(ROOT, ".claude", "plans", f"e11-mixed-{index}.md"),
                f"pmre11{index}",
                env,
            ))
            cases.append((f"E11: mixed scope sees non-harness path {game_path}", bool(reminder)))

    # Path detection must not read prose abbreviations, versions or decimals as file paths.
    sys.path.insert(0, os.path.dirname(hook))
    spec = importlib.util.spec_from_file_location("plan_memory_reminder_probe", hook)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    prose = ("Edit .claude/hooks/x.py and .claude/tests/test_x.py, e.g. keep proxy 0.1.36 behavior, "
             "i.e. the 3.5 KB budget.")
    cases.append(("abbreviations, versions and decimals are not paths", probe._paths_are_all_harness(prose)))
    cases.append(("an explicit harness files section with prose abbreviations stays meta",
                  probe._is_explicit_meta_scope("Files: .claude/hooks/x.py, .claude/rules/y.md (i.e. harness only)")))
    cases.append(("a bare project file still counts as a non-harness path",
                  not probe._paths_are_all_harness("Edit .claude/hooks/x.py and project.godot")))
    cases.append(("plan scope recognizes uppercase Windows harness paths",
                  probe._is_harness_path(r"C:\\repo\\.CLAUDE\\worktrees\\SYNC-PR110\\.CLAUDE\\rules\\x.md")))
    cases.append(("plan scope excludes a project path after dot-segment escape",
                  not probe._is_harness_path("C:/repo/.claude/worktrees/sync-pr110/./.claude/./hooks/../../game/Fire.cs")))

    if row:
        # A silent meta classification must not claim the file and suppress a later mixed edit.
        mixed = context(run(hook, mixed_plan(row), plan_path, "pmrmeta1", env))
        cases.append(("the same plan becoming mixed scope still emits", row["name"] in mixed))

    # A section edit uses a plan created inside this fixture. Its scope statement stays
    # outside the edited fragment and still controls classification.
    local_plan = os.path.join(tmp, "repo", ".claude", "plans", "e15-meta.md")
    os.makedirs(os.path.dirname(local_plan), exist_ok=True)
    whole = meta_plan(row) + "\n## Phase B\n\nRefactor the return path inside the harness only.\n"
    with open(local_plan, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(whole)
    fragment = "## Phase B\n\nRefactor the return path inside the harness only.\n"
    emitted = edited_section(hook, fragment, local_plan, "pmrlive1", env)
    cases.append(("E15: a section edit uses only the local harness-plan fixture",
                  emitted == ""))

    if row:
        # No files section anywhere: the paths carry the scope on their own.
        meta_np = context(run(hook, meta_no_section(row),
                              os.path.join(ROOT, ".claude", "plans", "b2-meta-nosection.md"),
                              "pmrmnos1", env))
        cases.append(("a sectionless plan whose every path is under .claude/ stays silent",
                      meta_np == ""))

        game_np = context(run(hook, game_no_section(row),
                              os.path.join(ROOT, ".claude", "plans", "b2-game-nosection.md"),
                              "pmrgnos1", env))
        cases.append(("a sectionless plan with non-harness paths still fires",
                      row["name"] in game_np))

        mixed_np = context(run(hook, mixed_no_section(row),
                               os.path.join(ROOT, ".claude", "plans", "b2-mixed-nosection.md"),
                               "pmrxnos1", env))
        cases.append(("a sectionless mixed plan fires for its game paths",
                      row["name"] in mixed_np))

        # Shared state is salvageable; identical domains add no new state across files.
        sid = "pmrstate"
        state_path = os.path.join(state_dir, sid[:8] + ".json")
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write('{"peer_field": true} trailing-debris')
        for index in range(55):
            path = os.path.join(ROOT, ".claude", "plans", f"a3-cap-{index}.md")
            context(run(hook, game_plan(row), path, sid, env))
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        seen = [key for key in state.get('fired_once', {}) if key.startswith('plan_memory_reminder:')]
        cases.append(("torn shared state is salvaged without losing peer fields",
                      state.get("peer_field") is True))
        cases.append(("one domain set across 55 plans stores one fact", len(seen) == 1))

    total = 0
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        total += 1
        if not ok:
            failures.append(label)
    print("\n%d/%d cases pass" % (total - len(failures), total))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
