"""Routing-hook registration checks for the UserPromptSubmit proofs: a hook is registered directly or
through prompt_dispatch.py's CHAIN. The shipped settings probe does not carry these checks."""
import ast
import os
import tempfile


def dispatcher_chain(hooks_dir):
    """The CHAIN literal of hooks/prompt_dispatch.py, read from source so the dispatcher never runs.
    `()` when the file is absent (a template or a consumer without a local dispatcher)."""
    path = os.path.join(hooks_dir, "prompt_dispatch.py")
    if not os.path.isfile(path):
        return ()
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "CHAIN" for t in targets):
            return ast.literal_eval(value)
    return ()


def registered_via(settings, hooks_dir, basename):
    """"direct" when a UserPromptSubmit command names the hook, "dispatcher" when one names
    prompt_dispatch.py and its CHAIN lists the hook, else None."""
    module = basename[:-3] if basename.endswith(".py") else basename
    chained = None
    for group in settings["hooks"]["UserPromptSubmit"]:
        for hook in group.get("hooks", []):
            command = hook.get("command", "")
            if basename in command:
                return "direct"
            if "prompt_dispatch.py" in command:
                if chained is None:
                    chained = {name for name, _budget in dispatcher_chain(hooks_dir)}
                if module in chained:
                    return "dispatcher"
    return None


def registration_cases(basename):
    """Planted (label, ok) cases proving `registered_via` for `basename`; a proof appends them."""
    def settings_for(command):
        return {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": command}]}]}}

    def hooks_dir_with(chain_source):
        tmp = tempfile.mkdtemp(prefix="registered_via_")
        if chain_source is not None:
            with open(os.path.join(tmp, "prompt_dispatch.py"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(chain_source)
        return tmp

    dispatcher = settings_for("python3 .claude/hooks/prompt_dispatch.py")
    listed = hooks_dir_with('CHAIN = (("other_hook", 5), ("%s", 15))\n' % basename[:-3])
    unlisted = hooks_dir_with('CHAIN = (("other_hook", 5),)\n')
    absent = hooks_dir_with(None)
    direct = settings_for("python3 .claude/hooks/" + basename)
    return [
        ("dispatcher route: CHAIN naming the hook is accepted",
         bool(registered_via(dispatcher, listed, basename))),
        ("dispatcher route: hook absent from CHAIN is rejected",
         not registered_via(dispatcher, unlisted, basename)),
        ("dispatcher route: missing prompt_dispatch.py is rejected",
         not registered_via(dispatcher, absent, basename)),
        ("direct route: a command naming the hook is still accepted",
         bool(registered_via(direct, absent, basename))),
        ("nothing registered is rejected",
         not registered_via(settings_for("python3 .claude/hooks/other.py"), listed, basename)),
    ]
