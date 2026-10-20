#!/usr/bin/env python3
"""Proof for plans in tools/model_registry.py: a transport entry with `planOf` pays for another
transport's rows (route = model vocabulary, plan = who pays).

Fixtures are deep copies of the SHIPPED registry, because transport-level validation rules fire
first on a hand-built dict. `forced()` flips `anthropic-secondary` to available in memory only.

Run: python3 .claude/tests/test_model_registry_plans.py
"""
import contextlib
import copy
import importlib.util
import io
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "model_registry.py")
spec = importlib.util.spec_from_file_location("mr_plans", MOD)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

PRIMARY = "anthropic"
SECONDARY = "anthropic-secondary"
# The Console-credit plans: the same route, paid by a Console inference key instead of a login.
CONSOLE = "anthropic-console"
CONSOLE_SECOND = "anthropic-console-secondary"
BASE_LAUNCHER = ".claude/scripts/anthropic_sidecar.sh"
SECOND_LAUNCHER = ".claude/scripts/anthropic_secondary_sidecar.sh"
CONSOLE_LAUNCHER = ".claude/scripts/anthropic_console_sidecar.sh"
CONSOLE_SECOND_LAUNCHER = ".claude/scripts/anthropic_console_secondary_sidecar.sh"
PROBE = ".claude/scripts/console_credit_probe.py"


def live():
    return copy.deepcopy(mr.load())


def suspended():
    d = live()
    d["transports"][SECONDARY]["status"] = {"state": "unavailable", "reason": "planted suspension"}
    return d


def forced():
    d = live()
    d["transports"][SECONDARY]["status"] = {"state": "available"}
    return d


def console_forced():
    """The shipped registry with both Console plans live. They ship available since S3; forcing the
    state keeps these cases independent of a later suspension, and only a live plan resolves or
    appears in plans_of."""
    d = live()
    for name in (CONSOLE, CONSOLE_SECOND):
        d["transports"][name]["status"] = {"state": "available"}
    return d


def rejection(doc):
    try:
        mr._validate(doc, "fixture")
    except mr.RegistryError as exc:
        return str(exc)
    return ""


def exec_row(data):
    return next(m for m in data["models"] if m["transport"] == PRIMARY and "executor" in (m.get("roles") or [])
                and mr.model_available(m["id"], data))


def capture(fn, argv, data):
    original_load = mr.load
    mr.load = lambda *_a, **_k: data
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = fn(argv)
    finally:
        mr.load = original_load
    return code, out.getvalue()


def cross_section(text):
    start = text.find("CROSS-TRANSPORT")
    end = text.find("NEAREST", start)
    return text[start:end if end > 0 else None]


def main():
    cases = []

    def case(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
        cases.append((name, bool(ok), detail))

    # ---- the shipped entry -------------------------------------------------------------------
    def shipped():
        d = live()
        sec = d["transports"].get(SECONDARY) or {}
        probe = {k: v for k, v in (sec.get("capacityProbe") or {}).items() if k != "refresh"}
        ok = (sec.get("planOf") == PRIMARY and sec.get("configDir") == "~/.claude-secondary"
              and sec.get("launcher") == SECOND_LAUNCHER and sec.get("costModel") == "plan-quota"
              and (sec.get("status") or {}).get("state") in ("available", "unavailable")
              and probe == d["transports"][PRIMARY]["capacityProbe"]
              and d["transports"][PRIMARY].get("configDir") == "~/.claude"
              and "credentialPath" not in d["transports"][PRIMARY])
        return ok, repr(sec)[:400]
    case("the shipped registry carries anthropic-secondary (planOf anthropic) and anthropic's "
         "configDir, with no credentialPath", shipped)

    def shipped_refresh():
        d = live()
        refresh = (d["transports"][SECONDARY].get("capacityProbe") or {}).get("refresh") or {}
        row = mr.row_by_id(refresh.get("model"), d, transport=PRIMARY) if refresh.get("model") else None
        ok = (row is not None and "scout" in (row.get("roles") or []) and refresh.get("effort") == "low"
              and isinstance(refresh.get("timeoutSeconds"), int) and 1 <= refresh["timeoutSeconds"] <= 35)
        return ok, repr(refresh)
    case("the secondary's capacityProbe.refresh pins the route's scout row at low", shipped_refresh)

    # ---- the Console-credit plans (S1) -------------------------------------------------------
    def shipped_console():
        d = live()
        t = d["transports"]
        if not all(k in t for k in (CONSOLE, CONSOLE_SECOND)):
            return False, "absent: %s" % sorted(set(t) - {CONSOLE, CONSOLE_SECOND})
        p, s = t[CONSOLE], t[CONSOLE_SECOND]
        got = {
            "planOf": (p.get("planOf"), s.get("planOf")),
            "costModel": (p.get("costModel"), s.get("costModel")),
            "configDir": (p.get("configDir"), s.get("configDir")),
            "launcher": (p.get("launcher"), s.get("launcher")),
            "credentialFile": (p.get("credentialFile"), s.get("credentialFile")),
            "credentialVar": (p.get("credentialVar"), s.get("credentialVar")),
            "workspaceIdVar": (p.get("workspaceIdVar"), s.get("workspaceIdVar")),
            "credit": (p.get("credit"), s.get("credit")),
            "probe": ((p.get("capacityProbe") or {}).get("kind"), (p.get("capacityProbe") or {}).get("args"),
                      (s.get("capacityProbe") or {}).get("args")),
            "state": ((p.get("status") or {}).get("state"), (s.get("status") or {}).get("state")),
            "reason": (bool((p.get("status") or {}).get("reason")), bool((s.get("status") or {}).get("reason"))),
        }
        want = {
            "planOf": (PRIMARY, PRIMARY),
            "costModel": ("plan-quota", "plan-quota"),
            "configDir": ("~/.claude-console-primary", "~/.claude-console-secondary"),
            "launcher": (CONSOLE_LAUNCHER, CONSOLE_SECOND_LAUNCHER),
            "credentialFile": ("~/.env.ai-worker.cmd", "~/.env.ai-worker.cmd"),
            "credentialVar": ("ANTHROPIC_CONSOLE_KEY_PRIMARY", "ANTHROPIC_CONSOLE_KEY_SECONDARY"),
            "workspaceIdVar": (None, None),
            "credit": ({"grantUSD": 200, "cycleStart": "2026-10-10", "cycleEnd": "2026-10-20"},
                       {"grantUSD": 100, "cycleStart": "2026-10-10", "cycleEnd": "2026-10-31"}),
            "probe": ("quota", [PROBE], [PROBE]),
            "state": ("available", "available"),
            "reason": (True, True),
        }
        ok = got == want and (p.get("capacityProbe") or {}).get("command") in ("python", "python3")
        return ok, repr({k: v for k, v in got.items() if v != want.get(k)}) if not ok else repr(got)
    case("the shipped registry carries both Console plans: key credential, credit block, quota probe "
         "and the available status S3's probe unlocks", shipped_console)

    def console_routable_since_the_probe():
        d = live()
        names = mr.plans_of(PRIMARY, d)
        return (CONSOLE in names and CONSOLE_SECOND in names), repr(names)
    case("both Console plans are routable now that their probe exists", console_routable_since_the_probe)

    def console_plans_resolve():
        d = console_forced()
        names = mr.plans_of(PRIMARY, d)
        got = (mr.plan_for_launcher(CONSOLE_LAUNCHER, d),
               mr.plan_for_launcher(CONSOLE_SECOND_LAUNCHER, d),
               mr.plan_for_launcher("C:\\r\\.claude\\scripts\\anthropic_console_sidecar.sh", d),
               mr.plan_for_launcher("/c/r/.claude/scripts/console_credit_probe.py", d))
        want = (CONSOLE, CONSOLE_SECOND, CONSOLE, None)
        return got == want and names == [PRIMARY, CONSOLE, CONSOLE_SECOND, SECONDARY], repr((names, got))
    case("a live Console plan resolves through plan_for_launcher and lists in plans_of(anthropic)",
         console_plans_resolve)


    def refresh_rejections():
        d = live()
        pin = dict(d["transports"][SECONDARY]["capacityProbe"]["refresh"])
        other_route = next(m["id"] for m in d["models"] if m["transport"] != PRIMARY)
        out = []
        for bad, want in (({"effort": "low", "timeoutSeconds": 15}, "model id"),
                          (dict(pin, effort="ultra"), "effort"),
                          (dict(pin, timeoutSeconds=99), "timeoutSeconds")):
            d = forced()
            d["transports"][SECONDARY]["capacityProbe"]["refresh"] = bad
            out.append(want in rejection(d))
        for model in ("no-such-model", other_route):
            d = forced()
            d["transports"][SECONDARY]["capacityProbe"]["refresh"] = dict(pin, model=model)
            out.append(rejection(d) == "" and any(model in p for p in mr.refresh_pin_problems(d)))
        out.append(mr.refresh_pin_problems(live()) == [])
        return all(out), repr(out)
    case("a refresh pin without a model, at an undeclared effort or past 35 s fails load; one off its "
         "route fails --check", refresh_rejections)

    def refresh_leaves_get_room():
        d = forced()
        probe = d["transports"][SECONDARY]["capacityProbe"]
        probe["timeoutSeconds"] = 20
        probe["refresh"] = dict(probe["refresh"], timeoutSeconds=30)
        why = rejection(d)
        return "timeoutSeconds" in why and SECONDARY in why, why
    case("a refresh timeout that leaves the usage GET no room inside the probe cap fails load",
         refresh_leaves_get_room)

    def duplicate_config_dir():
        d = forced()
        os.environ["MR_TEST_HOME"] = os.path.expanduser("~")
        d["transports"][SECONDARY]["configDir"] = "$MR_TEST_HOME/.claude/"
        why = rejection(d)
        return "configDir" in why and PRIMARY in why and SECONDARY in why, why
    case("two plans of one route sharing a configDir in any spelling fail load", duplicate_config_dir)

    def login_dir_forms():
        os.environ["MR_TEST_HOME"] = os.path.expanduser("~")
        home = os.path.expanduser("~")
        forms = ["~/.claude", "$MR_TEST_HOME/.claude", "${MR_TEST_HOME}/.claude/",
                 os.path.join(home, ".claude") + os.sep]
        if os.name == "nt":
            forms += ["%MR_TEST_HOME%\\.claude","/" + home[0].lower() + home[2:].replace("\\", "/") + "/.claude",
                      os.path.join(home, ".claude").upper()]
        got = {mr.login_dir(f) for f in forms}
        return (len(got) == 1 and mr.is_default_login("$MR_TEST_HOME/.claude")
                and not mr.is_default_login("~/.claude-secondary")), repr(got)
    case("login_dir reads ~, $VAR, ${VAR}, %VAR%, Git Bash /c/, a trailing separator and case as one login",
         login_dir_forms)

    def projects_dir_forms():
        os.environ["MR_TEST_HOME"] = os.path.expanduser("~")
        want = os.path.join(os.path.expanduser("~"), ".claude-x", "projects")
        norm = lambda p: os.path.normcase(os.path.normpath(p))
        got = [mr.projects_dir(p) for p in ("~/.claude-x", "$MR_TEST_HOME/.claude-x/")]
        default = mr.projects_dir(None)
        return (all(norm(g) == norm(want) for g in got)
                and norm(default) == norm(os.path.expanduser("~/.claude/projects"))), repr((got, default))
    case("projects_dir is <expanded login dir>/projects, the default login's for None", projects_dir_forms)

    def plan_for_launcher_var_default():
        d = forced()
        os.environ["MR_TEST_HOME"] = os.path.expanduser("~")
        d["transports"][PRIMARY]["configDir"] = "$MR_TEST_HOME/.claude"
        _, base = capture(mr._cmd_plan_for_launcher, [BASE_LAUNCHER], d)
        fields = base.rstrip("\n").split("\t")
        return (fields[2:] == ["1", "", ""] and "$" not in fields[1]), repr(base)
    case("plan-for-launcher expands a $VAR spelling of ~/.claude, flags it the default login and leaves "
         "a login plan's credential fields empty", plan_for_launcher_var_default)

    def plan_for_launcher_credential_fields():
        os.environ["MR_TEST_HOME"] = os.path.expanduser("~")
        d = live()
        for name in (CONSOLE, CONSOLE_SECOND):
            d["transports"][name]["credentialFile"] = "$MR_TEST_HOME/.env.ai-worker.cmd"
        _, key = capture(mr._cmd_plan_for_launcher, [CONSOLE_SECOND_LAUNCHER], d)
        _, login = capture(mr._cmd_plan_for_launcher, [SECOND_LAUNCHER], d)
        kf = key.rstrip("\n").split("\t")
        lf = login.rstrip("\n").split("\t")
        ok = (len(kf) == 5 and kf[0] == CONSOLE_SECOND and kf[2] == "0"
              and kf[3] == "ANTHROPIC_CONSOLE_KEY_SECONDARY" and "$" not in kf[4]
              and kf[4].replace("\\", "/").endswith("/.env.ai-worker.cmd")
              and len(lf) == 5 and lf[3:] == ["", ""])
        return ok, repr((key, login))
    case("plan-for-launcher prints the credential variable and the expanded credential file after the "
         "three original fields", plan_for_launcher_credential_fields)

    def sidecar_fields_plan():
        row = exec_row(live())["id"]
        _, off = capture(mr._cmd_sidecar_fields, [row, "--plan", SECONDARY], suspended())
        _, on = capture(mr._cmd_sidecar_fields, [row, "--plan", SECONDARY], forced())
        _, bare = capture(mr._cmd_sidecar_fields, [row], live())
        cols = lambda t: t.strip().split("|")
        ok = (cols(off)[7] == "unavailable" and cols(off)[8] and cols(on)[7] == "available"
              and cols(bare)[7] == "available" and cols(on)[9] == "plan-quota")
        return ok, repr((off, on, bare))
    case("sidecar-fields --plan reports the plan's own status in column 8", sidecar_fields_plan)

    def sidecar_fields_foreign_plan():
        row = exec_row(live())["id"]
        try:
            capture(mr._cmd_sidecar_fields, [row, "--plan", "codex"], forced())
        except mr.RegistryError as exc:
            return "codex" in str(exc), str(exc)
        return False, "no error"
    case("sidecar-fields --plan refuses a plan that does not pay for the row", sidecar_fields_foreign_plan)

    def plan_for_launcher_cli():
        d = forced()
        _, second = capture(mr._cmd_plan_for_launcher, ["/c/r/.claude/scripts/anthropic_secondary_sidecar.sh"], d)
        _, base = capture(mr._cmd_plan_for_launcher, [r"C:\r\.claude\scripts\anthropic_sidecar.sh"], d)
        code, none = capture(mr._cmd_plan_for_launcher, ["/c/r/other.sh"], d)
        s2, b2 = second.rstrip("\n").split("\t"), base.rstrip("\n").split("\t")
        ok = (len(s2) == 5 and len(b2) == 5
              and s2[0] == SECONDARY and s2[1].replace("\\", "/").endswith("/.claude-secondary") and s2[2] == "0"
              and b2[0] == PRIMARY and b2[1].replace("\\", "/").endswith("/.claude") and b2[2] == "1"
              and code == 1 and none == "")
        return ok, repr((second, base, code, none))
    case("plan-for-launcher prints plan, expanded configDir, default flag and empty credential fields",
         plan_for_launcher_cli)
    case("`--check` accepts the shipped registry", lambda: (mr._cmd_check([]) == 0, ""))

    # ---- seam 2: route_of, plans_of, plan_for_launcher ---------------------------------------
    case("route_of maps a plan to its route and a route to itself",
         lambda: ((mr.route_of(SECONDARY, forced()), mr.route_of(PRIMARY, forced()),
                   mr.route_of("codex", forced())) == (PRIMARY, PRIMARY, "codex"), ""))
    case("plans_of lists the route and its live plans, the Console plans included",
         lambda: (mr.plans_of(PRIMARY, forced()) == [PRIMARY, CONSOLE, CONSOLE_SECOND, SECONDARY],
                  repr(mr.plans_of(PRIMARY, forced()))))
    case("plans_of skips an unavailable plan",
         lambda: (mr.plans_of(PRIMARY, suspended()) == [PRIMARY, CONSOLE, CONSOLE_SECOND],
                  repr(mr.plans_of(PRIMARY, suspended()))))
    case("plans_of of a transport with no plans is itself",
         lambda: (mr.plans_of("codex", forced()) == ["codex"], ""))

    def launcher_lookup():
        d = forced()
        got = (mr.plan_for_launcher(SECOND_LAUNCHER, d),
               mr.plan_for_launcher("C:\\Users\\u\\repo\\.claude\\scripts\\anthropic_secondary_sidecar.sh", d),
               mr.plan_for_launcher("/c/Users/u/repo/.claude/scripts/anthropic_sidecar.sh", d),
               mr.plan_for_launcher("/c/Users/u/repo/.claude/scripts/not_a_launcher.sh", d),
               mr.plan_for_launcher("/c/x/extra_anthropic_sidecar.sh", d))
        return got == (SECONDARY, SECONDARY, PRIMARY, None, None), repr(got)
    case("plan_for_launcher reverse-maps relative, Windows and Git Bash launcher paths", launcher_lookup)
    case("plan_for_launcher names no plan for a script two transports share",
         lambda: (mr.plan_for_launcher(".claude/scripts/opencode_sidecar.sh", forced()) is None,
                  repr(mr.plan_for_launcher(".claude/scripts/opencode_sidecar.sh", forced()))))

    # ---- seam 2: validation --------------------------------------------------------------------
    def row_on_plan():
        d = forced()
        row = copy.deepcopy(exec_row(d))
        row.update(transport=SECONDARY, id="claude-planted-9", alias="planted9", order=99991)
        row.pop("servedIds", None)
        d["models"].append(row)
        why = rejection(d)
        return "plan" in why and SECONDARY in why, why
    case("a model row naming a plan transport fails validation", row_on_plan)

    def chain():
        d = forced()
        d["transports"]["anthropic-third"] = dict(copy.deepcopy(d["transports"][SECONDARY]),
                                                  planOf=SECONDARY, configDir="~/.claude-third")
        why = rejection(d)
        return "planOf" in why and "anthropic-third" in why, why
    case("a planOf chain fails validation", chain)

    def dangling():
        d = forced()
        d["transports"][SECONDARY]["planOf"] = "nosuch"
        why = rejection(d)
        return "planOf" in why, why
    case("a planOf naming no transport fails validation", dangling)

    def no_cost():
        d = forced()
        d["transports"][SECONDARY].pop("costModel")
        why = rejection(d)
        return "costModel" in why and SECONDARY in why, why
    case("a plan missing costModel fails validation", no_cost)

    def no_probe():
        d = forced()
        d["transports"][SECONDARY].pop("capacityProbe")
        why = rejection(d)
        return "capacityProbe" in why and SECONDARY in why, why
    case("a plan missing capacityProbe fails validation", no_probe)

    def no_config_dir(name):
        def run():
            d = forced()
            d["transports"][name].pop("configDir")
            why = rejection(d)
            return "configDir" in why and name in why, why
        return run
    case("a plan missing configDir fails validation", no_config_dir(SECONDARY))
    case("the route of a plan missing configDir fails validation", no_config_dir(PRIMARY))
    case("a transport with no plans needs no configDir", lambda: (rejection(live()) == "", rejection(live())))

    # ---- seam 2: transport_meta / effort inheritance --------------------------------------------
    def inherit():
        d = forced()
        meta = mr.transport_meta(SECONDARY, d)
        base = d["transports"][PRIMARY]
        return (meta.get("effortValues") == base["effortValues"]
                and meta.get("roleSource") == base["roleSource"]
                and meta.get("launcher") == SECOND_LAUNCHER
                and mr.transport_effort_values(SECONDARY, d) == base["effortValues"]), repr(meta)[:300]
    case("a plan inherits effortValues and roleSource from its route, keeps its own launcher", inherit)

    # ---- seam 3: plan_launchers, for_role otherPlans, CLI ---------------------------------------
    def launchers():
        d = forced()
        row = exec_row(d)
        got = (mr.plan_launchers(row, PRIMARY, d), mr.plan_launchers(row, SECONDARY, d),
               mr.plan_launchers(row, PRIMARY, suspended()))
        console = [(CONSOLE, CONSOLE_LAUNCHER), (CONSOLE_SECOND, CONSOLE_SECOND_LAUNCHER)]
        return (got == (console + [(SECONDARY, SECOND_LAUNCHER)],
                        [(PRIMARY, BASE_LAUNCHER)] + console, console), repr(got))
    case("plan_launchers lists every other live plan's launcher, the seat's own plan excluded", launchers)

    def other_plans():
        d = forced()
        res = mr.for_role("executor", PRIMARY, d)
        others = [(m["id"], p, l) for m, p, l in res.get("otherPlans") or []]
        row = exec_row(d)
        return ((row["id"], SECONDARY, SECOND_LAUNCHER) in others
                and row["id"] in [m["id"] for m in res["inTransport"]]), repr(others)
    case("for_role reports the other plan of an in-transport row in otherPlans", other_plans)

    def from_secondary():
        d = forced()
        res = mr.for_role("executor", SECONDARY, d)
        others = [(m["id"], p, l) for m, p, l in res.get("otherPlans") or []]
        row = exec_row(d)
        return (row["id"] in [m["id"] for m in res["inTransport"]]
                and (row["id"], PRIMARY, BASE_LAUNCHER) in others), repr(others)
    case("from the secondary seat the route's rows are in-transport and the primary is the other plan",
         from_secondary)

    def cli(seat, data, want, unwanted=None):
        def run():
            original = mr.role_tiers
            mr.role_tiers = lambda *_a, **_k: None
            try:
                code, text = capture(mr._cmd_for_role, ["executor", "--from", seat], data())
            finally:
                mr.role_tiers = original
            cross = cross_section(text)
            ok = code == 0 and want in cross and "bash " + want in cross
            if unwanted:
                ok = ok and "bash " + unwanted not in cross
            return ok, text[-900:]
        return run
    case("`for-role executor` from the primary seat prints the secondary launcher under CROSS-TRANSPORT",
         cli(PRIMARY, forced, SECOND_LAUNCHER, unwanted=BASE_LAUNCHER))
    case("`for-role executor` from the secondary seat prints the primary launcher under CROSS-TRANSPORT",
         cli(SECONDARY, forced, BASE_LAUNCHER, unwanted=SECOND_LAUNCHER))

    def cli_session_plan():
        original = mr.role_tiers
        mr.role_tiers = lambda *_a, **_k: None
        saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_TRANSPORT",
                                                  "ANTHROPIC_BASE_URL")}
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(os.path.expanduser("~"), ".claude-secondary")
        os.environ.pop("CLAUDE_CODE_TRANSPORT", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        try:
            code, text = capture(mr._cmd_for_role, ["executor"], forced())
        finally:
            mr.role_tiers = original
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        cross = cross_section(text)
        return (code == 0 and "seat: " + SECONDARY in text and "bash " + BASE_LAUNCHER in cross
                and "bash " + SECOND_LAUNCHER not in cross), text[-900:]
    case("`for-role executor` with no --from seats the session PLAN (a secondary session hops to the primary)",
         cli_session_plan)

    def cli_unavailable():
        original = mr.role_tiers
        mr.role_tiers = lambda *_a, **_k: None
        try:
            code, text = capture(mr._cmd_for_role, ["executor", "--from", PRIMARY], suspended())
        finally:
            mr.role_tiers = original
        return code == 0 and SECOND_LAUNCHER not in text, text[-600:]
    case("an unavailable plan's launcher is not printed by `for-role`", cli_unavailable)

    def available_cli(data, present):
        def run():
            code, text = capture(mr._cmd_available, [], data())
            return code == 0 and ((SECOND_LAUNCHER in text) == present) and BASE_LAUNCHER in text, text[-600:]
        return run
    case("`available` prints each row's plan launchers", available_cli(forced, True))
    case("`available` omits an unavailable plan's launcher", available_cli(suspended, False))

    def set_status_roundtrip():
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_plan_"), "external_models.json")
        shutil.copyfile(str(mr.find_registry()), tmp)
        mr.set_transport_state(SECONDARY, "available", path=tmp)
        on = mr.plans_of(PRIMARY, mr.load(tmp))
        mr.set_transport_state(SECONDARY, "unavailable", path=tmp, reason="planted suspension")
        off = mr.plans_of(PRIMARY, mr.load(tmp))
        return (on == [PRIMARY, CONSOLE, CONSOLE_SECOND, SECONDARY]
                and off == [PRIMARY, CONSOLE, CONSOLE_SECOND]), repr((on, off))
    case("set-status flips a plan in and out of plans_of", set_status_roundtrip)

    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:600]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
