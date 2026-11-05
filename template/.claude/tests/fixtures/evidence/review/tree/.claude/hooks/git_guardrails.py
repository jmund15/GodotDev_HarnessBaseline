    return None


def _push_ranges(rest, root, git_env=None):
    """Local evidence for each published source; no network calls in the hook."""
    if "--dry-run" in rest or "-n" in rest or "--delete" in rest or "-d" in rest:
        return []
    operands, remote, option = [], None, None
    for arg in rest:
        if option:
            if option == "--repo":
                remote = arg
            option = None
        elif arg in ("--repo", "--receive-pack", "--exec", "--push-option", "-o"):
            option = arg
        elif arg.startswith("--repo="):
            remote = arg.partition("=")[2]
        elif not arg.startswith("-"):
            operands.append(arg)
    branch = (run_git(["branch", "--show-current"], root, git_env) or "").strip()
    def config(key):
        return (run_git(["config", "--get", key], root, git_env) or "").strip()
    if remote is None:
        remote = operands.pop(0) if operands else (config("branch." + branch + ".pushRemote")
                 or config("remote.pushDefault") or config("branch." + branch + ".remote") or "origin")
    specs, args = [], iter(operands)
    for arg in args:
        if arg == "tag":
            name = next(args, None)
            if name is None:
                raise RuntimeError("missing push tag")
            specs.append("refs/tags/" + name)
        else:
            specs.append(arg)
    if "--all" in rest or "--mirror" in rest:
        namespace = "refs" if "--mirror" in rest else "refs/heads"
        specs += (run_git(["for-each-ref", "--format=%(refname)", namespace], root, git_env) or "").splitlines()
    if "--tags" in rest:
        specs += (run_git(["for-each-ref", "--format=%(refname)", "refs/tags"], root, git_env) or "").splitlines()
    if not specs:
        specs = (run_git(["config", "--get-all", "remote." + remote + ".push"], root, git_env) or "").splitlines()
    if not specs:
        mode = config("push.default") or "simple"
        if mode == "matching":
            specs = [":"]
        elif mode == "nothing":
            return []
        else:
            target = config("branch." + branch + ".merge") if mode in ("simple", "upstream") else ""
            specs = ["HEAD:" + (target or "refs/heads/" + branch)]
    expanded = []
    for spec in specs:
        if spec.lstrip("+") == ":":
            heads = (run_git(["for-each-ref", "--format=%(refname)", "refs/heads"], root, git_env) or "").splitlines()
            expanded += [ref for ref in heads if run_git(["rev-parse", "--verify", "refs/remotes/" + remote + "/"
                         + ref.removeprefix("refs/heads/")], root, git_env)]
        else:
            expanded.append(spec)
    ranges = []
    for spec in expanded:
        source, _, target = spec.lstrip("+").partition(":")
        if not source:
            continue
        rev = run_git(["rev-parse", "--verify", source + "^{commit}"], root, git_env)
        if not rev:
            raise RuntimeError("cannot resolve push source " + source)
        target = (target or source).removeprefix("refs/heads/")
        if target == "HEAD":
            target = (run_git(["branch", "--show-current"], root, git_env) or "").strip()
        ref = "refs/remotes/" + remote + "/" + target
        base = close_base(root, push_ref=ref, head=rev.strip(), git_env=git_env)
        ranges.append((base, rev.strip()))
    return ranges


def harness_push_verdict(rest, cwd, inline_env=None, git_env=None):
    """Floor guard: outgoing harness commits need a green close covering this HEAD."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None
    next_step = ("BLOCKED harness push (rules/harness_tooling.md) — %s; run `python3 "
                 ".claude/scripts/harness_tests.py --close` in the background, then push.")
    if _STATE_IMPORT_ERROR or _HARNESS_IMPORT_ERROR:
        return next_step % "close machinery unavailable"
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    head = run_git(["rev-parse", "HEAD"], cwd, git_env)
    if not root or not head:
        return next_step % "cannot resolve the push checkout"
    root, head = root.strip(), head.strip()
    try:
        ranges = _push_ranges(rest, root, git_env)
        outgoing = [(base, rev, close_paths(root, base, rev)) for base, rev in ranges]
    except (RuntimeError, OSError, ValueError):
        return next_step % "cannot resolve the outgoing harness range"
    harness = [(base, rev, paths) for base, rev, paths in outgoing if paths]
    if not harness:
        return None
    if any(rev != head for _base, rev, _paths in harness):
        return ("BLOCKED harness push (rules/harness_tooling.md) — a harness ref other than HEAD has no close "
                "result, and `--close` proves only HEAD; check that ref out, run `python3 "
                ".claude/scripts/harness_tests.py --close` in the background there, then push it.")
    close = (read_json_salvage(_stamp_path(root)) or {}).get("close")
    if (not isinstance(close, dict) or close.get("head") != head
            or close.get("verdict") != "green" or close.get("red")
            or any(close.get("base") != base or not set(paths) <= set(close.get("paths") or [])
                   for base, _rev, paths in harness)):
        return next_step % "no green close result covers HEAD, its outgoing base and paths"
    return None
