#!/usr/bin/env python3
"""The one `--settings` value a sidecar child gets: every fragment merged in one place.

The CLI keeps only the last --settings flag, so every launcher passes its child settings through this.

Project rows come from each ready plugin's registration `sidecar_hooks` (tools/adaptation.py
`plugins`): matcher, command and optional when. when maps environment names to exact values
(null means nonempty); commands remain self-gated. {root}, {plugin_root}, {shape}, {prompt},
{ruler_scope}, {workdir}, {main_root} and {spill_dir} expand to shell-quoted values.

An unreadable plugin stops (exit 2) a launch whose guards/tool_profiles.json kind row names it in
`plugin`, because that launch would run without the plugin's guard; any other launch prints one
stderr line and proceeds. Each launcher stops on the exit.

This merge adds no enabledPlugins switch. Every launcher passes --strict-mcp-config with the resolved
profile's own config, which alone decides which servers a child loads, plugin servers included;
a settings switch beside it would be a second, silently disagreeing answer.

A bench-score child (the score daemon's driver) with a -f brief gets scoring/drift_guard.py on
Workflow: the call's args are checked against that brief before the paid panel runs.
BENCH_RULER_SCOPE adds the succession ruler's staged-file Read allowlist.
"""
import json
import os
import shlex
import sys
from pathlib import Path


class SettingsError(Exception):
    """A launch whose kind needs a plugin that cannot be read; the launcher exits 2 on it."""


def merge(base_json, root, shape="", prompt_file="", bench_arm="", ruler_scope="", workdir="",
          record="", progress="", model="", builtins=""):
    """`(settings text, warning lines)`: the merged JSON ("" when nothing to pass) and one line per
    unreadable plugin that this launch does not need. Raises SettingsError for one it does need."""
    base = json.loads(base_json) if base_json.strip() else {}
    root = Path(root).resolve()
    if prompt_file:
        prompt = Path(prompt_file).resolve().as_posix()
        command = 'python3 "%s" --prompt-file "%s"' % (
            (root / "hooks/sidecar_recompact_reprompt.py").as_posix(), prompt)
        events = [("SessionStart", "compact"), ("PreCompact", None)]
        # A Claude child given a compaction point (AC_AUTO_COMPACT_WINDOW) below its model's real window holds its
        # first auto-compaction until it saves its findings (the hook's --hard-window). The hold is wired only for a
        # child whose resolved tools include one of the hook's save tools: a read-only child cannot answer the nudge.
        hard = None
        sys.path.insert(0, (root / "hooks").as_posix())
        try:
            from sidecar_recompact_reprompt import WRITE_TOOLS
        except Exception:
            WRITE_TOOLS = ()
        tools = {t.strip() for t in (builtins or "").split(",") if t.strip()}
        can_write = bool(tools.intersection(WRITE_TOOLS))
        if model:
            sys.path.insert(0, (root / "tools").as_posix())
            import model_registry
            entry = model_registry.resolve(model)
            hard = model_registry.effective_window(entry, True) or model_registry.effective_window(entry, False)
            if not can_write:
                hard = None
        # A read-only child cannot save findings itself, so the hook copies each continuation summary beside the
        # record, where the parent's recovery reads it. An unreadable tool set or a launch with no record path fails
        # closed: no spill.
        record_path = record or progress
        if tools and not can_write and record_path:
            command += ' --spill "%s"' % (os.path.abspath(record_path).replace("\\", "/") + ".compaction.md")
            events += [("PostToolUse", None), ("Stop", None)]
        if hard:
            command += " --hard-window %d" % hard
            events.append(("PostToolUse", None))
        for event, matcher in events:
            rows_ = base.setdefault("hooks", {}).setdefault(event, [])
            if not any(row.get("matcher") == matcher and any(
                    "sidecar_recompact_reprompt.py" in hook.get("command", "")
                    for hook in row.get("hooks", [])) for row in rows_):
                row = {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
                rows_.append({"matcher": matcher, **row} if matcher else row)
        # The author prompt promises amendment delivery; the child's own checkout may predate the hook, so the
        # launcher's root supplies it. Its receipts dedupe a second registration from the checkout's settings.
        if shape == "author":
            command = 'python3 "%s"' % (root / "hooks/amend_delivery.py").as_posix()
            for event, matcher in (("PreToolUse", "*"), ("Stop", None)):
                row = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
                base.setdefault("hooks", {}).setdefault(event, []).append({"matcher": matcher, **row} if matcher else row)
    sys.path.insert(0, (root / "tools").as_posix())
    import adaptation, tool_profiles
    kind_plugin = (tool_profiles.load_table()["kinds"].get(shape) or {}).get("plugin", "")
    rows = []
    warnings = []
    for name, plugin in adaptation.plugins(root):
        if plugin.state == "ready":
            rows += [dict(row, _root=plugin.root) for row in plugin.manifest["sidecar_hooks"] if isinstance(row, dict)]
        elif kind_plugin == name:
            raise SettingsError("plugin %s unreadable (%s): a %s launch needs it. Fix %s/.claude/plugin.%s.json, "
                                "then relaunch." % (name, plugin.error, shape, plugin.root or "its root", name))
        else:
            warnings.append("plugin %s unreadable (%s): its hook rows are not merged." % (name, plugin.error))
    # The launcher's own row: a child in a linked worktree may write only its workdir, spill dir and brief's writeSet.
    builtin = {"matcher": "Write|Edit|NotebookEdit|Bash", "when": {"SC_MAIN_ROOT": None},
               "command": "python3 {root}/hooks/sidecar_workdir_guard.py {workdir} {main_root} {spill_dir} {prompt}"}
    rows = [builtin] + [row for row in (rows if isinstance(rows, list) else [])
                        if not (isinstance(row, dict) and "sidecar_workdir_guard.py" in str(row.get("command", "")))]
    env = dict(os.environ, SC_SHAPE=shape, SC_PROMPT_FILE=prompt_file,
               BENCH_ARM=bench_arm, BENCH_RULER_SCOPE=ruler_scope)
    sys.path.insert(0, (root / "hooks").as_posix())
    from _checkout_root import linked_main_root
    workdir = os.path.abspath(workdir) if workdir else ""
    main_root = linked_main_root(workdir) if workdir else ""
    main_root = Path(main_root).as_posix() if main_root else ""
    spill = os.path.dirname(os.path.abspath(record or progress)) if (record or progress) else ""
    env["SC_MAIN_ROOT"] = main_root
    values = {"root": root.as_posix(), "shape": shape, "workdir": workdir.replace("\\", "/"),
              "main_root": main_root, "spill_dir": spill.replace("\\", "/"),
              "prompt": os.path.abspath(prompt_file).replace("\\", "/") if prompt_file else "",
              "ruler_scope": env.get("BENCH_RULER_SCOPE", "")}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("matcher", "command")):
            continue
        when = row.get("when", {})
        if not isinstance(when, dict) or not all(bool(env.get(k)) if v is None else env.get(k, "") == v
                                               for k, v in when.items()):
            continue
        command = row["command"]
        for key, value in dict(values, plugin_root=Path(row["_root"]).as_posix() if row.get("_root") else "").items():
            command = command.replace("{" + key + "}", shlex.quote(value))
        base.setdefault("hooks", {}).setdefault("PreToolUse", []).append({
            "matcher": row["matcher"],
            "hooks": [{"type": "command", "command": command, "timeout": 10}],
        })
    # A child never runs with fewer delegate guards than its dispatcher: the loss floor's one entry (hooks/pre_bash.py, which
    # carries the wait clamp) comes from the launcher's root unless the child's own checkout registers it. The hook finds
    # its siblings and tools from its own file, so a workdir with no .claude/ runs it unchanged.
    def _registers_floor(workdir):
        for name in ("settings.json", "settings.local.json"):
            try:
                if "hooks/pre_bash.py" in (Path(workdir) / ".claude" / name).read_text(encoding="utf-8"):
                    return True
            except OSError:
                pass
        return False
    if not _registers_floor(workdir or os.getcwd()):
        base.setdefault("hooks", {}).setdefault("PreToolUse", []).append({
            "matcher": "Bash|PowerShell|Monitor|TaskStop",
            "hooks": [{"type": "command", "command": 'python3 "%s"' % (root / "hooks/pre_bash.py").as_posix(), "timeout": 75}]})
    return (json.dumps(base) if base else ""), warnings


def main(argv):
    if len(argv) != 11:
        print("usage: launch_settings.py <base-json> <root> <shape> <prompt-file> <bench-arm> <ruler-scope> "
              "<workdir> <record> <progress> <model-or-empty> <builtins>", file=sys.stderr)
        return 2
    try:
        text, warnings = merge(*argv)
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for line in warnings:
        print(line, file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
