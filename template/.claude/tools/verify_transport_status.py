#!/usr/bin/env python3
"""Prove every surface that reports a transport's availability agrees with the registry.

WHY THIS EXISTS. A dispatcher should never have to work out whether a provider is usable -- that
decision is already made, and re-deriving it costs a turn and can be got wrong. The suspension is
therefore reported by six surfaces, and six surfaces are six chances to disagree. This asserts they
do not, in BOTH states, so the guarantee is checked rather than hoped for.

Run it after editing any of: external_models.json, model_registry.py, deepseek_sidecar.sh,
budget_posture.py, session_model_rails.py, CLAUDE.md, orchestration/SKILL.md.

    python3 .claude/tools/verify_transport_status.py            # check current state only
    python3 .claude/tools/verify_transport_status.py --both     # flip through both states and restore

`--both` mutates the registry and restores it. It never DISPATCHES in the active state: that would
place a real billed call, which is what the suspension exists to prevent. The active-state check
covers the availability probe only.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent          # .claude/
REPO = ROOT.parent
REGISTRY = ROOT / "reference" / "external_models.json"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "hooks"))
import model_registry  # noqa: E402
import _session_transport  # noqa: E402

# Which transport to verify. A hardcoded `deepseek` default was permissive on the exact axis
# _session_transport declares fail-closed: an unlabelled call from a codex session reported
# DeepSeek's status as if it were the session's, with nothing in the output to tell them apart.
# THIS session's transport is the default; `--transport` overrides. Never a fixed vendor.
TRANSPORT = _session_transport.resolve()[0]
SIDECAR = ""


def set_transport(name):
    """Repoint the module at `name`. Called from main() once argparse has read the flag."""
    global TRANSPORT, SIDECAR
    TRANSPORT = name
    cfg = model_registry.transport_meta(name) or {}
    SIDECAR = str((ROOT.parent / cfg["launcher"]).as_posix()) if cfg.get("launcher") else ""


set_transport(TRANSPORT)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    return bool(ok)


def _bash():
    """Git Bash explicitly. A bare `bash` from Python resolves to the WSL binary on this machine,
    which cannot read a `C:/...` path and exits 127 -- a failure that looks like a broken gate."""
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if pathlib.Path(c).exists():
            return c
    return "bash"


def sh(args):
    r = subprocess.run([_bash(), SIDECAR] + args, capture_output=True, text=True, timeout=180)
    return r.returncode, (r.stdout + r.stderr)


def surfaces(state):
    """Every surface's report, gathered fresh so nothing is cached across a state flip."""
    out = {}
    out["registry"] = model_registry.transport_status(TRANSPORT)["state"]

    rc, txt = sh(["--check"])
    out["sidecar_check_rc"] = rc
    out["sidecar_check_txt"] = txt

    # Hooks are re-imported per call: both read the registry at call time, and a module-level cache
    # would make this test pass while the live hook reported a stale state.
    import importlib
    import budget_posture
    import session_model_rails
    importlib.reload(budget_posture)
    importlib.reload(session_model_rails)
    out["posture_advertises_sidecar"] = budget_posture.sidecar_available()
    return out


def assert_state(state):
    s = surfaces(state)
    check(f"[{state}] registry reports {state}", s["registry"] == state, s["registry"])
    assert_roster(state)

    if state == "unavailable":
        check(f"[{state}] sidecar --check exits 7", s["sidecar_check_rc"] == 7,
              f"exit {s['sidecar_check_rc']}")
        check(f"[{state}] sidecar --check says UNAVAILABLE",
              "UNAVAILABLE" in s["sidecar_check_txt"])
        for alias in sorted(m["alias"] for m in model_registry.models_for(TRANSPORT)):
            rc, _txt = sh(["-m", alias, "probe"])
            check(f"[{state}] dispatch -m {alias} exits 7", rc == 7, f"exit {rc}")
        # The advisory surfaces STOP ADVERTISING it rather than explaining it. An option that cannot
        # be chosen costs attention every turn it is described.
        #
        # The posture clause answers a ROSTER-WIDE question -- is there any off-quota route at all --
        # so suspending one transport drops it only when nothing else claims a role. This check used
        # to assert the clause disappears whenever THIS transport goes out, which was indistinguishable
        # from the roster-wide question while deepseek was the only row and became a false failure the
        # moment a second transport landed.
        others = [m for m in model_registry.available_models() if m["transport"] != TRANSPORT]
        check(f"[{state}] posture clause tracks the ROSTER, not this transport",
              s["posture_advertises_sidecar"] == bool(others),
              f"advertises={s['posture_advertises_sidecar']}, other available={[m['id'] for m in others]}")
    else:
        check(f"[{state}] sidecar --check exits 0", s["sidecar_check_rc"] == 0,
              f"exit {s['sidecar_check_rc']}")
        check(f"[{state}] sidecar --check says OK", "OK (" in s["sidecar_check_txt"])
        check(f"[{state}] posture line advertises the sidecar again",
              s["posture_advertises_sidecar"])


def assert_no_prescribed_substitute():
    """No surface may name a replacement for an excluded model.

    Choosing the replacement is the dispatcher's call and depends on the task's breadth, its
    complexity and the budget -- none of which a config file or a refusal message knows. A hardcoded
    redirect makes that decision on the dispatcher's behalf and, being wrong for some tasks, is worse
    than no answer.
    """
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    blob = json.dumps(reg)
    for banned in ("routeInstead", "substituteRule", "suspendedRouteTo"):
        check(f"registry carries no '{banned}'", banned not in blob)
    _aliases = sorted(m["alias"] for m in model_registry.models_for(TRANSPORT))
    rc, txt = sh(["-m", _aliases[0], "probe"]) if _aliases else (0, "")
    for banned in ("sonnet", "opus", "haiku"):
        check(f"refusal does not prescribe '{banned}'", banned not in txt.lower(),
              "a refusal that names a replacement pre-empts the dispatcher's choice")


def transport_model_ids(only_selectable=False):
    """Registered ids on the transport under test.

    Derived, not literal. A hardcoded `{"deepseek-v4-flash", "deepseek-v4-pro"}` made the
    --transport flag a half-migration: the flag selected codex while the assertion still demanded
    DeepSeek's rows be selectable, so the tool reported a disagreement that was its own.

    `only_selectable` excludes rows the registry marks unavailable INDIVIDUALLY. A transport can be
    live while some of its rows are not (opencode: five owner-excluded rows beside one live one),
    so "every registered id is selectable" is the wrong assertion for the available branch --
    it fails for the correct reason and reads as a surface disagreement.
    """
    ids = {m["id"] for m in model_registry.models_for(TRANSPORT)}
    if only_selectable:
        ids &= {m["id"] for m in model_registry.available_models()}
    return ids


def assert_roster(state):
    """The mechanism itself: an excluded model is ABSENT from the selectable set."""
    import importlib
    importlib.reload(model_registry)
    ids = {m["id"] for m in model_registry.available_models()}
    # unavailable: NO row of this transport may be selectable, excluded ones included.
    # available:   every row not INDIVIDUALLY excluded must be selectable.
    ds = transport_model_ids(only_selectable=(state != "unavailable"))
    if state == "unavailable":
        check("[unavailable] excluded models absent from the roster", not (ids & ds),
              f"still selectable: {sorted(ids & ds)}")
        check("[unavailable] exclusion carries a reason",
              all(model_registry.unavailable_reasons().get(m) for m in ds))
    else:
        check("[available] models present in the roster", ds <= ids, f"roster: {sorted(ids)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default=None,
                    help="transport to verify (default: this session's, per hooks/_session_transport.py)")
    ap.add_argument("--both", action="store_true",
                    help="flip through both states and restore (mutates the registry)")
    args = ap.parse_args()
    if args.transport:
        if args.transport not in model_registry.transports():
            ap.error("unknown transport %r; known: %s"
                     % (args.transport, ", ".join(model_registry.transports())))
        set_transport(args.transport)
    # The ambient session is a DEFAULT, never a silent one. Two ways it bites: an unidentified
    # endpoint resolves to `unknown`, which has no registry row and no launcher, so the battery
    # would run against nothing; and `--both` MUTATES registry state, so an inferred target means
    # a destructive run whose subject came from the environment rather than the caller. A
    # verification tool's target is exactly the thing that must not vary silently by caller.
    if TRANSPORT not in model_registry.transports():
        ap.error("this session's transport resolved to %r, which is not a registry transport. "
                 "Name one explicitly: --transport {%s}"
                 % (TRANSPORT, ",".join(model_registry.transports())))
    if args.both and not args.transport:
        ap.error("--both writes registry state; name the target explicitly with --transport "
                 "rather than inheriting this session's (%s)." % TRANSPORT)

    original = REGISTRY.read_text(encoding="utf-8")
    current = model_registry.transport_status(TRANSPORT)["state"]
    try:
        if current == "unavailable":
            assert_no_prescribed_substitute()
        assert_state(current)
        if args.both:
            other = "available" if current == "unavailable" else "unavailable"
            # A reason is required to go unavailable — the validator rejects a document without
            # one, so the flip has to carry it even though this whole block is restored below.
            model_registry.set_transport_state(
                TRANSPORT, other, reason="verify_transport_status --both: temporary probe flip")
            assert_state(other)
            model_registry.set_transport_state(
                TRANSPORT, current, reason="verify_transport_status --both: restoring")
            assert_state(current)
    finally:
        REGISTRY.write_text(original, encoding="utf-8")
        check("registry restored byte-for-byte",
              REGISTRY.read_text(encoding="utf-8") == original)

    width = max(len(n) for n, _o, _d in results) + 2
    ok = sum(1 for _n, o, _d in results if o)
    print("=" * (width + 30))
    print("TRANSPORT STATUS — surface agreement")
    print("=" * (width + 30))
    for name, good, detail in results:
        print(f"  {'PASS' if good else 'FAIL'}  {name:<{width}}{'' if good else '  ' + detail}")
    print("-" * (width + 30))
    print(f"{ok}/{len(results)} checks passed")
    if ok != len(results):
        print("\nSurfaces DISAGREE. A dispatcher reading one of them would act on a stale fact.")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
