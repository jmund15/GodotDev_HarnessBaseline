#!/usr/bin/env python3
"""Proof for tools/model_registry.py -- the `for-role` resolver and the ladder tier reader.

First coverage for this file. The resolver's job is to make a provider seat SEE the row it should
hop to; the failure it exists to remove is a seat quietly downgrading itself to stay in-transport.
So the load-bearing cases are the ones where in-transport is EMPTY and the answer lives across a
hop, and the one where a weaker in-transport row is offered with its tier delta stated.

Fixtures are literal in-memory dicts and literal ladder markdown -- no temp files, no live-file
coupling in the fixture cases. The live registry and the live ladder are checked at the bottom,
because a resolver that only ever sees its own fixture cannot tell a working anchor from one that
no longer matches the SSOT.

Run: python3 .claude/tests/test_model_registry.py
"""
import contextlib
import copy
import importlib.util
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "model_registry.py")
spec = importlib.util.spec_from_file_location("mr", MOD)
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)

# ---------------------------------------------------------------- fixtures
LADDER = """## Pick by work shape

| work shape | pin | also | never |
|---|---|---|---|
| deep review | `opus.xhigh` | `luna.max` | sonnet |

## Role guidance

| model | strengths | evidence | effort |
|---|---|---|---|
| fable | ideal design | bench | high |
| opus | design & execution | bench | xhigh |
| sonnet | fan-out, validation | bench | high |
| haiku | read-only locate | bench | low |
| luna | scoped planning + spec-tight execution | bench | max |
"""

DATA = {
    "transports": {
        "anthropic": {"costModel": "plan-quota"},
        "codex": {"costModel": "plan-quota", "launcher": ".claude/scripts/codex_proxy_sidecar.sh"},
        "opencode": {"costModel": "marginal-usd", "launcher": ".claude/scripts/opencode_sidecar.sh"},
    },
    "models": [
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus", "roles": ["architect", "executor"]},
        {"transport": "anthropic", "id": "claude-sonnet-5", "alias": "sonnet", "roles": ["fanout"]},
        {"transport": "anthropic", "id": "claude-haiku-4-5", "alias": "haiku", "roles": ["scout"]},
        {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable", "roles": ["orchestrator"]},
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna", "roles": ["fanout", "scout"],
         "effort": {"converged": "low", "open": "max"}},
        {"transport": "codex", "id": "gpt-6-astra", "alias": "astra",
         "roles": ["executor"], "status": {"state": "unavailable", "reason": "owner decision"}},
        {"transport": "opencode", "id": "muse-spark-1.3", "alias": "muse", "roles": ["fanout"]},
    ],
}


TIERS = mr.parse_role_tiers(LADDER.splitlines(True), DATA)

def aliases(rows):
    return sorted(m["alias"] for m in rows)


def res(role, seat):
    return mr.for_role(role, seat, data=DATA)


def raises(fn):
    try:
        fn()
    except mr.RegistryError:
        return True
    return False


def _astra_available():
    """DATA with astra's exclusion lifted, for the availability control case."""
    d = copy.deepcopy(DATA)
    for m in d["models"]:
        if m.get("alias") == "astra":
            m.pop("status", None)
    return d


def _fresh_registry_copy():
    """A throwaway copy of the live registry, so a state-flip case never touches the real file."""
    import shutil
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
    shutil.copyfile(str(mr.find_registry()), tmp)
    return tmp


def _with_sol():
    """DATA plus a codex row that claims the architect tier by name in `roles`."""
    d = copy.deepcopy(DATA)
    d["models"].append({"transport": "codex", "id": "gpt-6-sol", "alias": "sol",
                        "roles": ["architect"]})
    return d


_WELL_FORMED_ROWS = ["| fable | x | bench |", "| opus | x | bench |", "| sonnet | x | bench |",
                     "| haiku | x | bench |", "| luna | prose | bench |", "| astra | x | bench |",
                     "| muse | x | bench |"]


def _ladder_check_error(planted):
    """check_ladder over a temp ladder: the well-formed rows plus `planted` ('' adds nothing; None
    writes an empty table). Returns the error text, '' when the check passes."""
    import tempfile
    rows = [] if planted is None else _WELL_FORMED_ROWS + ([planted] if planted else [])
    text = "\n".join(["## Role guidance", "", "| model | strengths | evidence |", "|---|---|---|"] + rows) + "\n"
    path = os.path.join(tempfile.mkdtemp(prefix="mladder_"), "ladder.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        mr.check_ladder(path, data=DATA)
    except mr.RegistryError as exc:
        return str(exc)
    return ""


def _ladder_check_error_without(model):
    """check_ladder over the well-formed rows minus `model`'s row."""
    import tempfile
    rows = [r for r in _WELL_FORMED_ROWS if not r.startswith("| %s |" % model)]
    text = "\n".join(["## Role guidance", "", "| model | strengths | evidence |", "|---|---|---|"] + rows) + "\n"
    path = os.path.join(tempfile.mkdtemp(prefix="mladder_"), "ladder.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        mr.check_ladder(path, data=DATA)
    except mr.RegistryError as exc:
        return str(exc)
    return ""


def _only_tiers(keep):
    """DATA where the codex seat serves only `keep` -- so a tier between two kept ones has no
    in-transport row and the nearest search has a genuine tie to break."""
    d = copy.deepcopy(DATA)
    role_for = {t: t for t in mr.TIER_ORDER}
    for m in d["models"]:
        if m["transport"] == "codex":
            m.pop("status", None)
            m["roles"] = [role_for[t] for t in keep]
    return d


def _roundtrip_bytes():
    """Bytes `set_transport_state` writes, flipping a copy of the LIVE registry off to the side."""
    import shutil
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
    shutil.copyfile(str(mr.find_registry()), tmp)
    mr.set_transport_state("codex", "unavailable", path=tmp, reason="proof: a state flip")
    mr.set_transport_state("codex", "available", path=tmp)
    return open(tmp, "rb").read()


def _astra_sidecar_scoped():
    """DATA with astra excluded for the HOP only -- the one-word change its own reason text names."""
    d = copy.deepcopy(DATA)
    for m in d["models"]:
        if m.get("alias") == "astra":
            m["status"]["scope"] = "sidecar"
    return d


def _capture_context(argv, limits):
    """Run the context-window CLI branch against a supplied limits block."""
    original = mr.resolve
    mr.resolve = lambda _name: {"limits": limits}
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = mr._cmd_context_window(argv)
    finally:
        mr.resolve = original
    return code, output.getvalue().strip()


def _with_env(values, fn):
    """Run `fn` with `values` set in os.environ (an empty value unsets), restoring the old values after."""
    import os
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _anthropic_window_cli(argv):
    """Run the anthropic-window CLI branch against a synthetic Claude row and return its JSON."""
    original = mr.resolve
    mr.resolve = lambda _name: {"id": "claude-synthetic", "longContextTier": None,
                                "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                           "compactAtTokens": 200000}}
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = mr._cmd_anthropic_window(argv)
    finally:
        mr.resolve = original
    return __import__("json").loads(output.getvalue()) if code == 0 else {"exit": code}


def _full_window_cli(argv):
    """True when the CLI sets the compaction window to the model's full window, never leaving it unset."""
    out = _anthropic_window_cli(argv)
    return out.get("autoCompactWindow") == out.get("contextWindow") == 1000000


_TIERED = {"limits": {"contextTokens": 1000000, "maxOutputTokens": 128000},
           "longContextTier": {"thresholdInputTokens": 100000, "multipliers": {"input": 5, "output": 5}}}


def _capture_max_output(argv, limits):
    """Run the max-output CLI branch against a supplied limits block."""
    original = mr.resolve
    mr.resolve = lambda _name: {"limits": limits}
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = mr._cmd_max_output(argv) if hasattr(mr, "_cmd_max_output") else 1
    finally:
        mr.resolve = original
    return code, output.getvalue().strip()


def _login_dirs_case():
    data = {"transports": {"anthropic": {},
                           "anthropic-second": {"planOf": "anthropic", "configDir": "~/.claude-second"},
                           "codex": {"configDir": "~/.claude"}}}
    saved = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(os.path.expanduser("~"), "elsewhere")
    try:
        got = [mr.login_dir(p) for p in mr.login_dirs(data)]
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = saved
    want = [mr.login_dir(p) for p in ("~/.claude", "~/.claude-second", "~/elsewhere")]
    return sorted(got) == sorted(want) and len(got) == len(set(got))


def _missing_ladder_cli():
    original = mr.role_tiers
    mr.role_tiers = lambda *_a, **_k: (_ for _ in ()).throw(mr.RegistryError("missing ladder"))
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            rc = mr._cmd_for_role(["executor", "--from", "codex"])
    finally:
        mr.role_tiers = original
    return rc == 0 and "flash" in stream.getvalue() and "ladder row(s)" not in stream.getvalue()


def _role_validation(roles, evidence="measured", reference=None):
    d = copy.deepcopy(mr.load())
    row = next(m for m in d["models"] if m["alias"] == "flash")
    row["roles"] = roles
    row["effort"]["evidence"] = evidence
    row["effort"].pop("evidenceRef", None)
    if reference is not None:
        row["effort"]["evidenceRef"] = reference
    try:
        mr._validate(d, "fixture")
    except mr.RegistryError as exc:
        return str(exc)
    return ""


def _missing_ladder_route():
    original = mr.role_tiers
    mr.role_tiers = lambda *_a, **_k: (_ for _ in ()).throw(mr.RegistryError("missing ladder"))
    try:
        return "flash" in aliases(mr.rows_for_tier("executor", mr.load()))
    finally:
        mr.role_tiers = original


def _capture_for_role(role, seat, data=DATA):
    original_load, original_tiers = mr.load, mr.role_tiers
    mr.load = lambda *_a, **_k: data
    mr.role_tiers = lambda *_a, **_k: TIERS
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = mr._cmd_for_role([role, '--from', seat])
    finally:
        mr.load, mr.role_tiers = original_load, original_tiers
    return code, output.getvalue()


def _marked_for_role(role_tier, mark_tier):
    """for-role output where the `sonnet` row carries one owner probation mark on `mark_tier`."""
    d = copy.deepcopy(mr.load())
    next(m for m in d["models"] if m["alias"] == "sonnet")["probation"] = [
        {"tier": mark_tier, "text": "owner-planted-mark", "evidenceRef": "planted"}]
    return _capture_for_role(role_tier, "anthropic", data=d)[1]


def _owner_display():
    d = copy.deepcopy(mr.load())
    row = next(m for m in d["models"] if m["alias"] == "flash")
    row["effort"] = {"evidence": "owner", "evidenceRef": "2026-09-25 transcript:owner/42"}
    original = mr.load
    mr.load = lambda *_a, **_k: d
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            mr._cmd_for_role(["executor", "--from", "codex"])
    finally:
        mr.load = original
    return "owner-claimed" in next(line for line in stream.getvalue().splitlines() if "flash " in line)


def _ladder_disagreement(tokens="`architect` `executor` `scout`"):
    import tempfile
    source = mr.ladder_path()
    text = source.read_text(encoding="utf-8")
    # Anchored on the row's start, not its strengths prose, which ladder edits reword.
    before = "\n| opus | "
    assert text.count(before) == 1
    text = text.replace(before, before + tokens + " — ", 1)
    path = os.path.join(tempfile.mkdtemp(prefix="mladder_"), "ladder.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    original = mr.ladder_path
    mr.ladder_path = lambda *_a, **_k: path
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            mr._cmd_check([])
    except mr.RegistryError as exc:
        return "opus" in str(exc) and "registry `roles`" in str(exc)
    finally:
        mr.ladder_path = original
    return False


CASES = [
    ("E2 every ladder review family has a producer group", lambda: all(
        any(row.get("family") == family and mr.review_group(row["id"]) == group
            for row in mr.load()["models"])
        for family, group in {"fable": "anthropic", "opus": "anthropic", "sonnet": "anthropic",
                              "luna": "openai", "sol": "openai", "flash": "deepseek"}.items())),
    ("E2 all routes of a review family keep its producer", lambda: all(
        mr.review_group(row["id"]) == mr.load()["reviewGroups"][row["family"]]
        for row in mr.load()["models"] if row.get("family") in mr.load()["reviewGroups"])),
    ("E2 absent and malformed identity evidence stays unknown", lambda: mr.record_identity([]) ==
        {"model": None, "basis": None, "modelObserved": False} and mr.record_identity("not a record") ==
        {"model": None, "basis": None, "modelObserved": False}),
    ("E1 observed identity annotates existing authority", lambda: mr.record_identity(
        {"attestedModel": "gpt-6.1-sol", "servedModel": "client", "transport": "codex"}) ==
        {"model": "gpt-6.1-sol", "basis": "attestedModel", "modelObserved": True}),
    ("E1 requested identity is inferred, never observed", lambda: not mr.record_identity(
        {}, token="sol", when="2026-10-07")["modelObserved"]),
    ("E1 producer group maps executor and reviewer", lambda: mr.review_group("gpt-6.1-sol") == "openai"
        and mr.review_group("claude-opus-5-5") == "anthropic" and mr.review_group("unknown") is None),
    ("T1 alias in roles is rejected", lambda: "roles" in _role_validation(["sonnet"])),
    ("T1 alias claim serves no tier", lambda: mr.entry_tiers({"alias": "x", "roles": ["sonnet"]}) == []),
    ("T1 tier routes without ladder", _missing_ladder_route),
    ("T1 for-role prints rows without ladder", _missing_ladder_cli),
    ("a tier token planted in the live ladder fails `--check`", _ladder_disagreement),
    ("...whichever tier it names", lambda: _ladder_disagreement("`scout`")),
    ("T2 dated owner claim validates", lambda: _role_validation(
        ["executor"], "owner", "2026-09-25 transcript:owner/42") == ""
        and _role_validation(["executor"], "owner", '2026-09-25 owner: "I trust this tier"') == ""),
    ("T2 missing owner reference fails", lambda: "evidenceRef" in _role_validation(["executor"], "owner")),
    ("T2 undated owner reference fails", lambda: "evidenceRef" in _role_validation(
        ["executor"], "owner", "transcript:owner/42")
        and "evidenceRef" in _role_validation(["executor"], "owner", 'owner: "I trust this tier"')),
    ("T2 bare-date owner reference fails", lambda: "evidenceRef" in _role_validation(["executor"], "owner", "2026-09-25")),
    ("T2 owner claim displays owner-claimed", _owner_display),
    ("T1 registered claims are the row's tiers", lambda: mr.row_tier_claims(
        "opus", DATA) == ["architect", "executor"]),
    # ---- the ladder reader -------------------------------------------------
    ("a ladder row's tiers are its registry claims, on every transport; a two-tier row is under BOTH",
     lambda: TIERS == {"orchestrator": ["fable"], "architect": ["opus"], "executor": ["opus"],
                       "fanout": ["sonnet", "luna"], "scout": ["haiku", "luna"]}),

    # A strengths cell opens with every tier it claims. Reading only the first token dropped the second
    # claim, so a design-and-execution row vanished from one of its two tiers.
    ("for-role architect lists the two-token row",
     lambda: aliases(res("architect", "anthropic")["inTransport"]) == ["opus"]),

    ("...and for-role executor lists the same row",
     lambda: aliases(res("executor", "anthropic")["inTransport"]) == ["opus"]),

    ("a strengths cell never mints a tier: an unregistered row with tier words claims nothing",
     lambda: mr.parse_role_tiers(["## Role guidance\n", "| model | strengths | evidence |\n", "|---|---|---|\n",
                                  "| m | `executor` `fanout` x | bench |\n"], DATA) == {}),

    # A non-Anthropic row claims a tier by naming it in registry `roles`, beside the alias route.
    ("a registry row naming a tier in `roles` serves that tier",
     lambda: aliases(mr.for_role("architect", "codex", _with_sol())["inTransport"]) == ["sol"]),

    ("...and only that tier: the architect claim does not make it an executor",
     lambda: "sol" not in aliases(
         mr.for_role("executor", "codex", _with_sol())["inTransport"])),

    # An architect-only claimant is the nearest in-transport row for executor work on its seat. The
    # offer names its tier and direction, so the dispatcher weighs its `±` cell before taking it.
    ("codex seat with an architect claimant, executor: it is offered as nearest, ONE tier ABOVE",
     lambda: [(m["alias"], t, d) for m, t, d in mr.for_role("executor", "codex", _with_sol())["nearest"]]
             == [("sol", "architect", -1)]),

    # ---- a tier's listing is facts, never a pick ------------------------------
    # The session chooses by the ladder's selection line; a listing sorted by one axis reads as the order
    # to pick by. cheapest_first stays for after-the-fact review (orchestration_metrics.pricier_pins).
    ("cheapest_first orders by ladder cost (higher = cheaper), unknown cost last, ties stable",
     lambda: [m["alias"] for m in mr.cheapest_first(
         [{"id": "a", "alias": "a"}, {"id": "b", "alias": "b"}, {"id": "c", "alias": "c"}, {"id": "d", "alias": "d"}],
         costs={"a": 5, "b": None, "c": 10, "d": 5})] == ["c", "a", "d", "b"]),

    ("for_role keeps registry order in each section: no sort stands in for the choice",
     lambda: all([m["id"] for m in mr.for_role(t, "anthropic")["inTransport"]]
                 == [m["id"] for m in mr.rows_for_tier(t, seat="anthropic") if m["transport"] == "anthropic"]
                 for t in ("fanout", "executor", "architect"))),

    ("the for-role report says its order is not a pick and names the selection line",
     lambda: "not a pick" in _capture_for_role("fanout", "anthropic", data=mr.load())[1]
             and "selection line" in _capture_for_role("fanout", "anthropic", data=mr.load())[1]),

    ("the for-role report prints each row's ladder cost",
     lambda: __import__("re").search(r"cost (\d+|\?) \(ladder; higher = cheaper\)",
                                     _capture_for_role("fanout", "anthropic", data=mr.load())[1]) is not None),


    ("the for-role row carries the owner's probation mark for the requested tier",
     lambda: [l for l in _marked_for_role("fanout", "fanout").splitlines()
              if l.startswith("  sonnet") and 'probation "owner-planted-mark"' in l]),

    ("...and no mark for another tier, nor on a row without one",
     lambda: 'probation "' not in _marked_for_role("fanout", "scout")
             and all('probation "' not in l for l in _marked_for_role("fanout", "fanout").splitlines()
                     if not l.startswith("  sonnet"))),

    # ---- one predicate for every tier a registry row serves -----------------
    ("entry_tiers reads only explicit tier tokens",
     lambda: mr.entry_tiers({"alias": "sol", "roles": ["architect"]}) == ["architect"]
             and mr.entry_tiers({"alias": "luna", "roles": ["fanout", "scout"]})
             == ["fanout", "scout"]
             and mr.entry_tiers({"alias": "opus", "roles": []}) == []),

    ("row_tier_claims reads registered claims on any transport",
     lambda: mr.row_tier_claims("sol", _with_sol()) == ["architect"]
             and mr.row_tier_claims("opus", DATA) == ["architect", "executor"]),

    ("row_tier_claims on an unregistered name claims nothing",
     lambda: mr.row_tier_claims("qwen-local", DATA) == []),

    # ---- a malformed strengths cell fails `--check`, naming the row ----------------
    ("a strengths cell opening with a tier token fails the ladder check and names the row: claims live in registry roles",
     lambda: "opus" in _ladder_check_error("| opus | `architect` - x | bench |")
             and "registry" in _ladder_check_error("| opus | `architect` - x | bench |")),

    ("a tier-claiming registry row with no ladder row fails the check, on any transport",
     lambda: "muse" in _ladder_check_error_without("muse")),

    ("an empty Role guidance table fails the ladder check",
     lambda: "no ladder row claiming" in _ladder_check_error(None)),

    ("a well-formed ladder passes the ladder check",
     lambda: _ladder_check_error("") == ""),

    ("a work-shape row never contributes a tier",
     lambda: not any("deep review" in v for vs in TIERS.values() for v in vs)),

    ("a ladder name the registry does not know claims no tier",
     lambda: not any("deep review" in v for vs in mr.parse_role_tiers(
         ["## Role guidance\n", "| model | strengths | evidence |\n", "|---|---|---|\n", "| deep review | x | pub |\n"],
         DATA).values() for v in vs)),

    ("a ladder missing a tier is an ERROR, not an empty answer",
     lambda: raises(lambda: mr.role_tiers(path=os.path.join(HERE, "does-not-exist.md")))),

    # ---- the whole point: an empty in-transport set names the hop ----------
    ("codex seat, executor: nothing in-transport",
     lambda: res("executor", "codex")["inTransport"] == []),

    ("codex seat, executor: opus is named across the hop",
     lambda: aliases(res("executor", "codex")["crossTransport"]) == ["opus"]),

    ("codex seat, executor: luna offered as nearest, ONE tier below",
     lambda: [(m["alias"], t, d) for m, t, d in res("executor", "codex")["nearest"]]
             == [("luna", "fanout", 1)]),

    ("codex seat, orchestrator: nearest states THREE tiers below",
     lambda: [(m["alias"], t, d) for m, t, d in res("orchestrator", "codex")["nearest"]]
             == [("luna", "fanout", 3)]),

    ("nearest is EMPTY when in-transport already serves the tier",
     lambda: res("fanout", "codex")["nearest"] == []),

    # ---- claiming an alias IS the agnostic route --------------------------
    ("a row claiming `sonnet` serves the fanout tier",
     lambda: aliases(res("fanout", "codex")["inTransport"]) == ["luna"]),

    ("fanout across the hop reaches BOTH the Anthropic row and the free one",
     lambda: aliases(res("fanout", "codex")["crossTransport"]) == ["muse", "sonnet"]),

    ("anthropic seat, executor: opus is in-transport and nothing hops",
     lambda: aliases(res("executor", "anthropic")["inTransport"]) == ["opus"]
             and res("executor", "anthropic")["crossTransport"] == []),

    ("a provider for-role report names its canonical native Workflow pin, not the sidecar alias",
     lambda: (lambda rc, text: rc == 0 and "gpt-5.6-luna" in text
              and "a Workflow/Agent pin takes the first column, the alias" not in text
              and "provider pin: gpt-5.6-luna" in text)(*_capture_for_role("fanout", "codex"))),
    ("a host for-role report names its legal native alias or ID",
     lambda: (lambda rc, text: rc == 0 and "host pin: sonnet or claude-sonnet-5" in text)(
         *_capture_for_role("fanout", "anthropic"))),
    ("a sidecar recipe includes disclosure, shape, effort, record, stream and workdir",
     lambda: all(s in mr._sidecar_line(DATA["models"][-1], DATA)
                 for s in ("-e <", "-D <", "-G <", "-f <", "-R <", "-P <", "-d <"))),

    # ---- availability gates dispatch --------------------------------------
    ("an unavailable row never serves a tier",
     lambda: not any(m["alias"] == "astra"
                     for r in ("orchestrator", "executor", "fanout", "scout")
                     for m in mr.rows_for_tier(r, DATA))),

    # The control that makes the gate OBSERVABLE: the same row, available, serves the tier its
    # `opus` role claims. Without this, "never serves" is satisfied by any predicate that drops the
    # row for any reason -- including one that no longer checks availability at all.
    ("...and the SAME row serves it once available -- so the gate, not the role match, excluded it",
     lambda: any(m["alias"] == "astra"
                 for m in mr.rows_for_tier("executor", _astra_available()))),

    # ---- a sidecar-scoped exclusion is about the HOP, and the tier filter must agree ------
    # `rows_for_tier` was seat-blind while `workflow_provider_guard` filtered with the seat-aware
    # `dispatchable`. The row then vanished from role resolution on the one seat that may still pin
    # it, while the guard went on allowing the pin -- excluded and broken emitting one evidence.
    ("a sidecar-scoped row still serves its tier FROM ITS OWN transport",
     lambda: any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped(), seat="codex"))),

    ("...and is gone for every other seat -- the scope word is what does it",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped(), seat="anthropic"))),

    ("a scope:all exclusion is gone from its own transport too",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", DATA, seat="codex"))),

    ("for_role threads the seat, so a codex seat sees its own sidecar-scoped row",
     lambda: "astra" in aliases(mr.for_role(
         "executor", "codex", _astra_sidecar_scoped())["inTransport"])),

    ("...and an anthropic seat is not offered it across the hop",
     lambda: "astra" not in aliases(mr.for_role(
         "executor", "anthropic", _astra_sidecar_scoped())["crossTransport"])),

    ("seat=None keeps the seat-blind answer for callers reporting on no session",
     lambda: not any(m["alias"] == "astra" for m in mr.rows_for_tier(
         "executor", _astra_sidecar_scoped()))),

    # ---- an equidistant tie resolves toward the STRONGER tier -------------
    # `scout` is one rung below `fanout` and `executor` one above, so both are distance 1.
    # Sorting on abs(distance) alone let list position decide, which could offer the weaker row --
    # a silent downgrade, the exact failure for_role exists to make visible.
    ("an equidistant nearest tie offers the STRONGER tier, not list order",
     lambda: set(t for _, t, _ in mr.for_role(
         "fanout", "codex", _only_tiers(("executor", "scout")))["nearest"])
         == {"executor"}),

    ("...and the weaker side is genuinely available, so the tie was real",
     lambda: any(m["transport"] == "codex" for m in mr.rows_for_tier(
         "scout", _only_tiers(("executor", "scout")), seat="codex"))),

    # ---- a status flip is a ONE-LINE diff ---------------------------------
    # Windows text mode turns every \n into CRLF, so a one-field flip rewrote all ~700 lines and the
    # flip itself became unreviewable. Bytes, not the source text: only the file settles this.
    ("set_transport_state writes LF, so the diff shows the field that moved",
     lambda: b"\r\n" not in _roundtrip_bytes()),

    ("...and the flip actually landed, so the LF check is not passing on an unwritten file",
     lambda: b'"codex"' in _roundtrip_bytes()),

    # The validator demands a reason on any unavailable transport, and the function had no parameter
    # to supply one -- so the ONE state it exists to set was unreachable through its own API.
    ("suspending a transport without a reason is refused, not written",
     lambda: raises(lambda: mr.set_transport_state(
         "codex", "unavailable", path=_fresh_registry_copy()))),

    ("...and the same call WITH a reason succeeds -- the parameter, not the state, was missing",
     lambda: mr.set_transport_state("codex", "unavailable", path=_fresh_registry_copy(),
                                    reason="proof")["state"] == "unavailable"),

    # ---- one reader for the ladder table ----------------------------------
    ("parse_ladder_rows keys by column NAME and skips the separator row",
     lambda: mr.parse_ladder_rows(
         ["## Role guidance", "| model | strengths | effort |", "|---|---|---|",
          "| opus | `executor` x | high |"], ("model", "strengths", "effort"))
         == [{"model": "opus", "strengths": "`executor` x", "effort": "high"}]),

    ("...and returns nothing when a required column is absent, rather than guessing",
     lambda: mr.parse_ladder_rows(
         ["## Role guidance", "| model | strengths |", "|---|---|", "| opus | `executor` |"],
         ("model", "strengths", "effort")) == []),

    # ---- validation is not a fifth tier -----------------------------------
    ("`validation` resolves to the fanout rows",
     lambda: res("validation", "codex")["tier"] == "fanout"),

    ("`validation` says it is an EFFORT move, not a different row",
     lambda: "effort" in (res("validation", "codex")["note"] or "").lower()),

    ("`validation` is absent from the closed tier set",
     lambda: "validation" not in mr.TIER_ORDER),

    # ---- Anthropic client's window differs from the proxy output-cap reserve ----
    ("Claude operating point adds the capped reserve and fixed buffer, and opts into 1M",
     lambda: mr.anthropic_window({"id": "claude-haiku-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                            "compactAtTokens": 200000}}) == {
         "model": "claude-haiku-test[1m]", "autoCompactWindow": 233000,
         "maxContextTokens": None, "contextWindow": 233000,
         "contextWindowSource": "anthropic-auto-compact"}),
    ("recognized native 1M without an operating point keeps the 1M window",
     lambda: mr.anthropic_window({"id": "claude-opus-5-5[1m]", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000}})["autoCompactWindow"] == 1000000),
    ("a smaller output cap changes the Claude reserve, not the fixed buffer",
     lambda: mr.anthropic_window({"id": "claude-test", "longContextTier": None,
                                 "limits": {"contextTokens": 200000, "maxOutputTokens": 10000,
                                            "compactAtTokens": 100000}})["autoCompactWindow"] == 123000),
    ("a non-Claude name keeps the max-context environment contract",
     lambda: mr.anthropic_window({"id": "vendor-test", "longContextTier": None,
                                 "limits": {"contextTokens": 272000}}) == {
         "model": "vendor-test", "autoCompactWindow": None, "maxContextTokens": 272000,
         "contextWindow": 272000, "contextWindowSource": "launcher-declared"}),
    ("a sub-1M row cannot claim a larger Claude window or request the 1M suffix",
     lambda: (lambda w: w["model"] == "claude-test" and w["contextWindow"] is None
              and w["contextWindowSource"] == "unknown")(
         mr.anthropic_window({"id": "claude-test", "longContextTier": None,
                              "limits": {"contextTokens": 500000}}))),
    ("a compact-point override replaces compactAtTokens and derives the window from it",
     lambda: mr.anthropic_window({"id": "claude-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "compactAtTokens": 200000}},
                                compact_override=117000)["autoCompactWindow"] == 150000),

    ("a review-shaped job compacts only at the model's full window, set explicitly so a login's autoCompactWindow setting cannot apply, named review-shape",
     lambda: mr.anthropic_window({"id": "claude-haiku-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                            "compactAtTokens": 200000}}, shape="review") == {
         "model": "claude-haiku-test[1m]", "autoCompactWindow": 1000000, "maxContextTokens": None,
         "contextWindow": 1000000, "contextWindowSource": "review-shape"}),
    ("an author-shaped job keeps the compaction window",
     lambda: mr.anthropic_window({"id": "claude-haiku-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                            "compactAtTokens": 200000}}, shape="author")["autoCompactWindow"] == 233000),
    ("a review-shaped job with an explicit compact point keeps that override",
     lambda: mr.anthropic_window({"id": "claude-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "compactAtTokens": 200000}},
                                compact_override=117000, shape="review")["autoCompactWindow"] == 150000),
    ("anthropic-window --shape review reaches the resolver through argv",
     lambda: _full_window_cli(["synthetic", "--shape", "review"])
     and _anthropic_window_cli(["synthetic"])["autoCompactWindow"] == 233000
     and _anthropic_window_cli(["synthetic", "--max", "--shape", "author"])["autoCompactWindow"] == 233000),
    ("a job marked resident-subject compacts only at the model's full window whatever its tool-profile shape",
     lambda: mr.anthropic_window({"id": "claude-haiku-test", "longContextTier": None,
                                 "limits": {"contextTokens": 1000000, "maxOutputTokens": 128000,
                                            "compactAtTokens": 200000}}, shape="arm", resident=True) == {
         "model": "claude-haiku-test[1m]", "autoCompactWindow": 1000000, "maxContextTokens": None,
         "contextWindow": 1000000, "contextWindowSource": "resident-subject"}),
    ("SIDECAR_RESIDENT_SUBJECT=1 reaches the resolver through the launcher's environment; unset keeps the point",
     lambda: _with_env({"SIDECAR_RESIDENT_SUBJECT": "1"},
                       lambda: _full_window_cli(["synthetic", "--shape", "arm"]))
     and _with_env({"SIDECAR_RESIDENT_SUBJECT": ""},
                   lambda: _anthropic_window_cli(["synthetic", "--shape", "arm"]))["autoCompactWindow"] == 233000),
    ("an explicit compact point still overrides the resident-subject mark",
     lambda: _with_env({"SIDECAR_RESIDENT_SUBJECT": "1", "SIDECAR_COMPACT_AT_TOKENS": "117000"},
                       lambda: _anthropic_window_cli(["synthetic", "--shape", "arm"]))["autoCompactWindow"] == 150000),

    # ---- context-window modes ----------------------------------------------
    ("the default context query uses contextTokens and the provider percentage",
     lambda: _capture_context(["luna"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95}) == (0, "258400")),

    ("the max context query uses maxContextTokens and the same percentage",
     lambda: _capture_context(["luna", "--max"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95}) == (0, "828400")),

    ("max context prints nothing when the registry declares no max",
     lambda: _capture_context(["luna", "--max"], {
         "contextTokens": 272000, "effectiveContextPercent": 95}) == (0, "")),

    ("context-window rejects an unknown mode instead of treating it as max",
     lambda: _capture_context(["luna", "--huge"], {
         "contextTokens": 272000, "maxContextTokens": 872000,
         "effectiveContextPercent": 95})[0] == 2),

    # ---- max-output: the child's output cap is the model's own maximum, not the CLI's 32K default ----
    # (2026-10-01: six comparator children lost their paid reasoning at the CLI's default cap)
    ("max-output prints the model's own maximum when it fits a quarter of the window",
     lambda: _capture_max_output(["m"], {"contextTokens": 1000000, "maxOutputTokens": 128000}) == (0, "128000")),
    ("max-output bounds a maximum past a quarter of the window, so auto-compaction stays at 75% or later",
     lambda: _capture_max_output(["m"], {"contextTokens": 1000000, "maxOutputTokens": 384000}) == (0, "250000")),
    ("max-output bounds against the effective window the provider enforces",
     lambda: _capture_max_output(["m"], {"contextTokens": 262144, "maxOutputTokens": 262144,
                                         "effectiveContextPercent": 50}) == (0, "32768")),
    ("max-output prints nothing when the registry states no maximum",
     lambda: _capture_max_output(["m"], {"contextTokens": 272000}) == (0, "")),
    ("max-output prints the maximum alone when no window is stated",
     lambda: _capture_max_output(["m"], {"maxOutputTokens": 64000}) == (0, "64000")),
    ("max-output --max bounds against the max window, not the default one",
     lambda: _capture_max_output(["m", "--max"], {"contextTokens": 100000, "maxContextTokens": 1000000,
                                                  "maxOutputTokens": 128000}) == (0, "128000")),
    ("max-output refuses an unknown flag with the usage exit",
     lambda: _capture_max_output(["m", "--bogus"], {"maxOutputTokens": 64000})[0] == 2),
    ("context-window and max-output derive the effective window in one helper",
     lambda: (mr._effective_window({"contextTokens": 272000, "effectiveContextPercent": 95}, False) == 258400
              and mr._effective_window({"contextTokens": 1, "maxContextTokens": 1000}, True) == 1000
              and mr._effective_window({"maxOutputTokens": 64000}, False) is None)),
    ("usable_window is the effective window less the max-output cap, the cap bounded to a quarter of the window",
     lambda: (mr.usable_window({"limits": {"contextTokens": 100000, "maxOutputTokens": 10000}}) == 90000
              and mr.usable_window({"limits": {"contextTokens": 1000000, "maxOutputTokens": 384000}}) == 750000
              and mr.usable_window({"limits": {"contextTokens": 272000, "effectiveContextPercent": 95}}) == 258400
              and mr.usable_window({"limits": {"maxOutputTokens": 64000}}) is None
              and mr.usable_window({}) is None)),
    ("a row with a price step keeps its input under the step: usable is 95% of the threshold, the window adds "
     "the output reserve, and --max ignores the step",
     lambda: (mr.usable_window(_TIERED) == 95000
              and mr.effective_window(_TIERED) == 126666
              and mr.usable_window(dict(_TIERED, limits={"contextTokens": 1000000, "maxOutputTokens": 10000})) == 95000
              and mr.effective_window(_TIERED, use_max=True) == 1000000
              and mr.usable_window(dict(_TIERED, longContextTier=None)) == 872000
              and mr.effective_window({"limits": {"contextTokens": 272000, "effectiveContextPercent": 95},
                                       "longContextTier": {"thresholdInputTokens": 272000,
                                                           "multipliers": {"input": 2, "output": 1.5}}}) == 258400)),
    ("a declared compactAtTokens replaces the step room: the child compacts there, past the step by choice",
     lambda: (mr.usable_window(dict(_TIERED, limits=dict(_TIERED["limits"], compactAtTokens=200000))) == 200000
              and mr.effective_window(dict(_TIERED, limits=dict(_TIERED["limits"], compactAtTokens=200000))) == 266666
              and mr.effective_window(dict(_TIERED, limits=dict(_TIERED["limits"], compactAtTokens=200000)),
                                      use_max=True) == 1000000)),
    ("max-output prints the cap usable_window subtracts, from one output_cap",
     lambda: (mr.output_cap({"contextTokens": 1000000, "maxOutputTokens": 384000}) == 250000
              and _capture_max_output(["m"], {"contextTokens": 1000000, "maxOutputTokens": 384000}) == (0, "250000")
              and mr.output_cap({"contextTokens": 272000}) is None)),

    # ---- login_dirs: every login store the registry declares, plus the default and CLAUDE_CONFIG_DIR ----
    ("login_dirs lists each transport's configDir, ~/.claude and CLAUDE_CONFIG_DIR once each",
     lambda: _login_dirs_case()),

    # ---- refusals ---------------------------------------------------------
    ("an unknown role raises and names the legal set",
     lambda: raises(lambda: mr.canonical_tier("reviewer"))),

    # ---- the sidecar line is the hop, spelled out -------------------------
    ("a cross-transport row prints its launcher and alias",
     lambda: ".claude/scripts/opencode_sidecar.sh" in mr._sidecar_line(DATA["models"][6], DATA)
             and "-m muse" in mr._sidecar_line(DATA["models"][6], DATA)),

    ("a transport with no launcher says so rather than printing a broken command",
     lambda: "cannot hop" in mr._sidecar_line(DATA["models"][0], DATA)),
]


SCHEDULE = {
    "offPeakMultiplier": 0.5,
    "peakWindowsUTC": [
        {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "01:00", "end": "04:00"},
        {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "06:00", "end": "10:00"},
    ],
    "source": "https://api-docs.deepseek.com/quick_start/pricing",
    "asOf": "2026-09-15",
}


def _schedule_cases(live):
    """Pricing schedule, peak policy, measuredVersion and effort-values. Returns (failed, total).

    2026-09-15 is a Tuesday; 2026-09-19 a Saturday; 2026-09-21 the following Monday.
    """
    # Local so this block stands alone against a file whose module imports differ (separability).
    import contextlib
    import io
    import json
    import tempfile

    def planted():
        d = copy.deepcopy(live)
        d["transports"]["deepseek"]["pricingSchedule"] = copy.deepcopy(SCHEDULE)
        return d

    def _overnight():
        d = planted()
        d["transports"]["deepseek"]["pricingSchedule"]["peakWindowsUTC"] = [
            {"days": ["Fri"], "start": "22:00", "end": "02:00"}]
        return d

    def deepseek_row(d, alias="flash"):
        return next(m for m in d["models"] if m["alias"] == alias)

    def rejects(mutate, needle):
        d = planted()
        mutate(d)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def accepts(mutate):
        d = planted()
        mutate(d)
        mr._validate(d, "fixture")
        return True

    def window(at, transport="deepseek"):
        return mr.price_window(transport, at=at, data=planted())

    def cli(argv, data):
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        old = os.environ.get(mr.ENV_OVERRIDE)
        os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mr.main(argv)
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return code, out.getvalue().strip()

    def default_check():
        d = planted()
        deepseek_row(d)['effort']['default'] = 'ultra'
        try:
            rc, out = cli(['--check'], d)
        except mr.RegistryError as exc:
            return 'effort.default' in str(exc)
        return rc != 0 and 'effort.default' in out

    def with_env_at(value, fn):
        old = os.environ.get("SC_PRICE_AT")
        os.environ["SC_PRICE_AT"] = value
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("SC_PRICE_AT", None)
            else:
                os.environ["SC_PRICE_AT"] = old

    def set_measured(d, version):
        row = deepseek_row(d)
        row["version"] = "DeepSeek-V4.1-Flash"
        row["effort"] = {"evidence": "measured", "measuredVersion": version}

    def unpriced_problem():
        d = planted()
        deepseek_row(d).pop("price")
        usd, problem = mr.list_price("flash", 1, 0, 1, data=d)
        return problem if usd is None else ""

    def run_price(at):
        return mr.price_run("flash", 1_000_000, 0, 1_000_000, data=planted(), at=at)

    cases = [
        ("Tue 02:00Z is peak at full price, ending 04:00Z",
         lambda: window("2026-09-15T02:00:00Z") == {
             "window": "peak", "multiplier": 1.0, "changesAt": "2026-09-15T04:00:00Z"}),
        ("Tue 05:00Z is off-peak at the multiplier, ending 06:00Z",
         lambda: window("2026-09-15T05:00:00Z") == {
             "window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-15T06:00:00Z"}),
        ("a window END is exclusive: Tue 04:00Z is off-peak",
         lambda: window("2026-09-15T04:00:00Z")["window"] == "off-peak"),
        ("a window START is inclusive: Tue 06:00Z is peak",
         lambda: window("2026-09-15T06:00:00Z")["window"] == "peak"),
        ("Saturday inside the hours is off-peak, changing at Monday 01:00Z",
         lambda: window("2026-09-19T02:00:00Z") == {
             "window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-21T01:00:00Z"}),
        ("a transport with no schedule is flat, full price, no change",
         lambda: window("2026-09-15T02:00:00Z", transport="codex") == {
             "window": "flat", "multiplier": 1.0, "changesAt": None}),
        ("price_run halves every rate off-peak",
         lambda: abs(run_price("2026-09-15T05:00:00Z") * 2 - run_price("2026-09-15T02:00:00Z")) < 1e-9
                 and run_price("2026-09-15T02:00:00Z") > 0),
        ("price_run with no `at` reads SC_PRICE_AT",
         lambda: with_env_at("2026-09-15T05:00:00Z", lambda: abs(
             mr.price_run("flash", 1_000_000, 0, 0, data=planted())
             - run_price_fresh_off_peak(planted())) < 1e-9)),
        ("list_price resolves a payload model string and prices it through price_run",
         lambda: mr.list_price("flash", 1_000_000, 0, 1_000_000, multiplier=1.0, data=planted())
                 == (mr.price_run("flash", 1_000_000, 0, 1_000_000, multiplier=1.0, data=planted()), None)),
        ("list_price names an unregistered model instead of pricing it",
         lambda: mr.list_price("zz-unregistered", 1, 0, 1, data=planted())
                 == (None, "model 'zz-unregistered' is not in the registry")),
        ("list_price names a row with no list price as UNPRICED",
         lambda: unpriced_problem().startswith("UNPRICED: ")),
        ("the shipped schedule shape validates",
         lambda: accepts(lambda d: None)),
        ("offPeakMultiplier 0 is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             offPeakMultiplier=0), "offPeakMultiplier")),
        ("offPeakMultiplier above 1 is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             offPeakMultiplier=1.5), "offPeakMultiplier")),
        ("an unknown day name is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(days=["Funday"]), "days")),
        ("a zero-length window (start equal to end) is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(start="04:00", end="04:00"), "start")),
        # A window crossing midnight belongs to the day it STARTS: Fri 22:00-02:00 covers Fri 22:00-24:00
        # and Sat 00:00-02:00. 2026-09-18 is a Friday.
        ("a cross-midnight window validates",
         lambda: accepts(lambda d: d["transports"]["deepseek"]["pricingSchedule"].update(
             peakWindowsUTC=[{"days": ["Fri"], "start": "22:00", "end": "02:00"}]))),
        ("cross-midnight: Fri 23:00Z is peak, ending Sat 02:00Z",
         lambda: mr.price_window("deepseek", at="2026-09-18T23:00:00Z", data=_overnight())
                 == {"window": "peak", "multiplier": 1.0, "changesAt": "2026-09-19T02:00:00Z"}),
        ("cross-midnight: Sat 01:00Z (the tail on the next day) is peak",
         lambda: mr.price_window("deepseek", at="2026-09-19T01:00:00Z", data=_overnight())["window"] == "peak"),
        ("cross-midnight: Sat 02:00Z is off-peak, and Fri 21:59Z is off-peak until 22:00Z",
         lambda: mr.price_window("deepseek", at="2026-09-19T02:00:00Z", data=_overnight())["window"] == "off-peak"
                 and mr.price_window("deepseek", at="2026-09-18T21:59:00Z", data=_overnight())
                 == {"window": "off-peak", "multiplier": 0.5, "changesAt": "2026-09-18T22:00:00Z"}),
        ("cross-midnight: Thu 01:00Z is off-peak (the tail belongs to the listed day's NEXT day only)",
         lambda: mr.price_window("deepseek", at="2026-09-17T01:00:00Z", data=_overnight())["window"] == "off-peak"),
        ("a malformed HH:MM is rejected",
         lambda: rejects(lambda d: d["transports"]["deepseek"]["pricingSchedule"][
             "peakWindowsUTC"][0].update(start="25:00"), "HH:MM")),
        ("a schedule on a plan-quota transport is rejected",
         lambda: rejects(lambda d: d["transports"]["codex"].update(
             pricingSchedule=copy.deepcopy(SCHEDULE)), "pricingSchedule")),
        ("peakPolicy refuse on a transport with no schedule is rejected",
         lambda: rejects(lambda d: next(m for m in d["models"] if m["alias"] == "muse")[
             "gate"].update(peakPolicy="refuse"), "peakPolicy")),
        ("peakPolicy outside the closed set is rejected",
         lambda: rejects(lambda d: deepseek_row(d)["gate"].update(peakPolicy="sometimes"),
                         "peakPolicy")),
        ("peakPolicy refuse on a scheduled transport validates",
         lambda: accepts(lambda d: deepseek_row(d)["gate"].update(peakPolicy="refuse"))),
        ("measured evidence without measuredVersion on a versioned row is rejected",
         lambda: rejects(lambda d: set_measured(d, None), "measuredVersion")),
        ("measured evidence from an unregistered version is rejected",
         lambda: rejects(lambda d: set_measured(d, "DeepSeek-V3-Flash"), "measuredVersion")),
        # Owner ruling 2026-09-22: a new version takes over its family's claims until measured, so
        # evidence measured on a registered SAME-FAMILY row stands (inherited); another family's does not.
        ("measured evidence inherited from a same-family predecessor validates",
         lambda: accepts(lambda d: set_measured(d, "DeepSeek-V4-Flash-0731"))),
        ("measured evidence from another family's version is rejected",
         lambda: rejects(lambda d: set_measured(d, "gpt-5.6-luna"), "measuredVersion")),
        ("measured evidence matching the version validates",
         lambda: accepts(lambda d: set_measured(d, "DeepSeek-V4.1-Flash"))),
        ("`price-window` CLI prints the window and effective rates",
         lambda: (lambda r: r[0] == 0 and json.loads(r[1])["window"] == "off-peak"
                  and abs(json.loads(r[1])["rates"]["outputPer1M"]
                          - deepseek_row(planted())["price"]["outputPer1M"] * 0.5) < 1e-9)(
             with_env_at("2026-09-15T05:00:00Z", lambda: cli(["price-window", "flash"], planted())))),
        ("illegal effort.default is refused by --check",
         default_check),
        ("legal effort.default validates",
         lambda: accepts(lambda d: deepseek_row(d)['effort'].update(default='high'))),
        ("`effort-values` prints a transport's declared vocabulary",
         lambda: (lambda d: (d["transports"]["deepseek"].update(effortValues=["low", "high", "max"]),
                             cli(["effort-values", "flash"], d))[1])(planted())
                 == (0, '["low", "high", "max"]')),
        ("`effort-values` prints [] for a row and transport that declare none",
         lambda: (lambda d: (d["transports"]["anthropic"].pop("effortValues", None),
                             [m["effort"].pop("default", None) for m in d["models"] if m["transport"] == "anthropic"],
                             cli(["effort-values", "opus"], d))[2])(planted()) == (0, "[]")),
        ("the host transport declares the rungs the native engines accept",
         lambda: cli(["effort-values", "opus"], planted()) == (0, '["low", "medium", "high", "xhigh", "max"]')),
    ]

    failed = 0
    for name, fn in cases:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def _suffixed_vocabulary_resolves(live):
    """Every id `workflow_provider_guard.transport_injection` injects as `args.__transport.ids` (the one builder of
    that vocabulary, never a copied list) resolves to a registry row, and so does every row's suffixed id and alias."""
    import json
    hooks = os.path.join(HERE, "..", "hooks")
    sys.path.insert(0, hooks)
    wpg_spec = importlib.util.spec_from_file_location("wpg", os.path.join(hooks, "workflow_provider_guard.py"))
    wpg = importlib.util.module_from_spec(wpg_spec)
    wpg_spec.loader.exec_module(wpg)
    payload = {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/x.js", "args": {"jobs": []}}}
    names = []
    for transport in sorted({m["transport"] for m in live["models"]}):
        injected = wpg.transport_injection(payload, transport, live)
        args = injected["args"]
        args = json.loads(args) if isinstance(args, str) else args
        names += args["__transport"]["ids"]
    for m in live["models"]:
        if m.get("contextSuffix"):
            names += [m["id"] + m["contextSuffix"], m["alias"] + m["contextSuffix"]]
    if not any(n.endswith("[1m]") for n in names):
        return False  # the vocabulary must actually carry a suffixed pin, or the case proves nothing
    return all(mr.resolve(n, live) is not None for n in names)


def _identity_cases(live):
    """Model identity (S1): versioned aliases, dated alias history, record resolution, lifecycle.

    Live cases pin the owner's O-alias ruling (bare alias = current version; a superseded version
    gets a versioned alias). Planted cases carry the arm-record shapes the census found, so a
    resolver that only ever sees the live roster cannot pass them by accident.
    """
    import contextlib
    import io
    import json
    import re
    import tempfile

    RUNG_FLAG = re.compile(r" -e (none|low|medium|high|xhigh|max|ultra) ")

    def rid(name):
        return mr.resolve(name, live)["id"]

    def rejects(mutate, needle):
        d = copy.deepcopy(live)
        mutate(d)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def accepts(mutate):
        d = copy.deepcopy(live)
        mutate(d)
        mr._validate(d, "fixture")
        return True

    def raises_registry(fn):
        try:
            fn()
        except mr.RegistryError:
            return True
        return False

    def row(d, alias):
        return next(m for m in d["models"] if m["alias"] == alias)

    def cli(argv, data=None):
        old = os.environ.get(mr.ENV_OVERRIDE)
        if data is not None:
            tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh)
            os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = mr.main(argv)
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return code, out.getvalue()

    def routes_to(model_id):
        """Every (tier, seat) whose for-role report names model_id in any section."""
        tiers = mr.role_tiers()
        hits = []
        for tier in mr.TIER_ORDER:
            for seat in live["transports"]:
                res = mr.for_role(tier, seat, live)
                named = [m["id"] for m in res["inTransport"] + res["crossTransport"]]
                named += [m["id"] for m, _t, _d in res["nearest"]]
                if model_id in named:
                    hits.append((tier, seat))
        return hits

    # Arm-record shapes observed in the 2026-09-22 census of every *.record.json under the six data
    # roots: a proxied child whose own modelUsage names its client pin, a direct child, a record
    # with no served id at all, and the served spellings that are not registry ids verbatim.
    proxied = {"transport": "codex", "servedModel": "gpt-5.4-mini", "attestedModel": "gpt-5.6-luna"}
    direct = {"transport": "anthropic", "servedModel": "claude-opus-5"}
    bare = {"transport": "anthropic"}
    census_ids = ["big-pickle", "claude-fable-5-1", "claude-haiku-4-5", "claude-haiku-4-5-20251001",
                  "claude-opus-5", "claude-sonnet-5", "deepseek-flash", "deepseek-v4-flash",
                  "gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra", "hy3-free", "laguna-s-2.1-free",
                  "mimo-v2.5-free", "muse-spark-1.2-contributor-free",
                  "muse-spark-1.3-contributor-free", "nemotron-3-ultra-free",
                  "nemotron-3.5-lightning-free", "x-preview-f-free"]

    def shared_upstream():
        d = copy.deepcopy(live)
        deepseek = row(d, "flash")
        d["transports"]["go"] = dict(d["transports"]["go"])
        go = copy.deepcopy(deepseek)
        go["gate"].pop("peakPolicy", None)
        go.update(id="go/deepseek-flash", alias="go-flash", transport="go",
                  upstreamId="deepseek-flash", order=max(m["order"] for m in d["models"]) + 1)
        d["models"] = [m for m in d["models"] if m["id"] not in (go["id"], "go/deepseek-flash-v4.1")] + [go]
        return d

    def versioned_pair():
        """A deepseek row on a new version, its retired predecessor, and a Go row whose servedIds spell the old version."""
        d = shared_upstream()
        current = row(d, "flash")
        current["version"] = "DeepSeek-V4.1-Flash-1017"
        next(m for m in d["models"] if m["id"] == "go/deepseek-flash")["servedIds"] = ["deepseek-v4.1-flash"]
        d["models"].append(dict(copy.deepcopy(current), id="deepseek-flash-v4.1", alias="flash-0910",
                                version="DeepSeek-V4.1-Flash", lifecycle="retired",
                                order=max(m["order"] for m in d["models"]) + 1))
        return d

    cases = [
        ("a shared upstream id on separate transports validates",
         lambda: (mr._validate(shared_upstream(), "fixture") is None)),
        ("a repeated upstream id on one transport fails",
         lambda: rejects(lambda d: d["models"].append(dict(row(d, "flash"),
             id="deepseek/duplicate", alias="duplicate-flash", family="duplicate",
             upstreamId="deepseek-flash", order=max(m["order"] for m in d["models"]) + 1)), "upstreamId")),
        ("row_by_id prefers the record transport for a shared upstream, keeping positional data",
         lambda: mr.row_by_id("deepseek-flash", shared_upstream(), transport="go")["id"] == "go/deepseek-flash"
         and mr.row_by_id("deepseek-flash", shared_upstream(), transport="deepseek")["id"] == "deepseek-flash"
         and mr.row_by_id("deepseek-flash", shared_upstream())["id"] == "deepseek-flash"),
        ("record modelVersion names the row on its recorded transport",
         lambda: all(mr.row_by_id(mr.record_model_id({"transport": t, "servedModel": "deepseek-flash",
             "modelVersion": "deepseek-flash"}, data=shared_upstream()), shared_upstream(), transport=t)["transport"] == t
             for t in ("go", "deepseek"))),
        ("record_model_id returns the matched row id, not the upstream spelling it recorded",
         lambda: [mr.record_model_id({"transport": t, key: "deepseek-flash"}, data=shared_upstream())
                  for t in ("go", "deepseek") for key in ("modelVersion", "servedModel")]
         == ["go/deepseek-flash", "go/deepseek-flash", "deepseek-flash", "deepseek-flash"]),
        ("a recorded vendor version names its own transport's row, never another transport's served id",
         lambda: [mr.record_model_id({"transport": "deepseek", "modelVersion": v}, data=versioned_pair())
                  for v in ("DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash-1017")]
         == ["deepseek-flash-v4.1", "deepseek-flash"]),
        ("explicit messages, chat and responses api modes validate",
         lambda: all(accepts(lambda d: row(d, "flash").update(apiMode=mode))
                     for mode in ("messages", "chat", "responses"))),
        ("the Go tracer rows name separate API paths",
         lambda: all(mr.resolve(alias, live)["transport"] == "go"
                     and mr.resolve(alias, live)["apiMode"] == mode
                     for alias, mode in (("go-kimi", "messages"), ("go-glm", "chat")))),
        ("transport-meta exposes Go base URL to its launcher",
         lambda: cli(["transport-meta", "go"])[0] == 0
                 and json.loads(cli(["transport-meta", "go"])[1])["baseUrl"] ==
                     "https://opencode.ai/zen/go/v1"),
        # ---- effortParam: a row declaring `effortParam: false` takes no effort, by alias, id or served id ----
        ("takes_effort: the `haiku45` alias takes none", lambda: mr.takes_effort("haiku45", live) is False),
        ("takes_effort: haiku's served id `claude-haiku-4-5` takes none",
         lambda: mr.takes_effort("claude-haiku-4-5", live) is False),
        ("takes_effort: haiku's registry id with a context suffix takes none",
         lambda: mr.takes_effort("claude-haiku-4-5-20251001[1m]", live) is False),
        ("takes_effort: `opus` and its id take an effort",
         lambda: mr.takes_effort("opus", live) is True and mr.takes_effort("claude-opus-5-5", live) is True),
        ("takes_effort: an unknown name, `inherit` or `?` takes an effort",
         lambda: all(mr.takes_effort(n, live) is True for n in ("no-such-model", "inherit", "?", None))),
        # ---- O-alias: the bare alias names the CURRENT version on every route ----
        ("`opus` resolves to claude-opus-5-5", lambda: rid("opus") == "claude-opus-5-5"),
        ("`opus5` resolves to claude-opus-5", lambda: rid("opus5") == "claude-opus-5"),
        ("`sol` resolves to gpt-6.1-sol and `sol56` to gpt-5.6-sol",
         lambda: rid("sol") == "gpt-6.1-sol" and rid("sol56") == "gpt-5.6-sol"),
        ("`luna` resolves to gpt-6-luna and `luna56` to gpt-5.6-luna",
         lambda: rid("luna") == "gpt-6-luna" and rid("luna56") == "gpt-5.6-luna"),
        ("`resolve gpt-6-sol` succeeds by exact id", lambda: rid("gpt-6-sol") == "gpt-6-sol"),
        ("`claude-opus-5-5` resolves by exact id", lambda: rid("claude-opus-5-5") == "claude-opus-5-5"),

        # ---- owner ruling 2026-09-22: a new version takes over its family ----
        # REWRITTEN for the one-effort-home slice: the ruling is about WHICH version's cells stand
        # for the family, and `measuredVersion` still names that predecessor. What moved is the
        # rung's home -- `see-ladder`, because the ladder's sol/luna cell owns it -- so the case
        # asserts the transferred claim, not the retired `evidence: measured` spelling of it.
        ("every id the guard's suffixed vocabulary injects, and every suffixed row, resolves to its row",
         lambda: _suffixed_vocabulary_resolves(live)),
        ("an unknown name with a context suffix still raises",
         lambda: raises(lambda: mr.resolve("no-such-model-5-5[1m]", live))),
        ("the current luna/sol rows take their family's roles and its measured predecessor's cells",
         lambda: mr.resolve("luna", live)["roles"] == ["fanout", "scout", "executor"]
                 and mr.resolve("sol", live)["roles"] == ["architect", "executor", "fanout"]
                 and all(mr.resolve(a, live)["effort"].get("evidence") == "see-ladder"
                         and mr.resolve(a, live)["effort"].get("measuredVersion") == "gpt-5.6-" + a
                         for a in ("sol", "luna"))),
        ("for-role routes the fanout tier to gpt-6-luna, never to the superseded row",
         lambda: ("fanout", "codex") in routes_to("gpt-6-luna") and routes_to("gpt-5.6-luna") == []),
        ("...and the SAME report routes to claude-opus-5-5 (the probe reaches a routed row)",
         lambda: ("executor", "codex") in routes_to("claude-opus-5-5")),
        ("the superseded 5.6 rows claim no role and keep their own measured evidence",
         lambda: all(mr.resolve(a, live)["roles"] == []
                     and mr.resolve(a, live)["effort"].get("measuredVersion") == mr.resolve(a, live)["version"]
                     for a in ("sol56", "luna56"))),

        # ---- the dated alias history ----
        ("resolve_alias_at(opus, 2026-09-10) -> claude-opus-5",
         lambda: mr.resolve_alias_at("opus", "2026-09-10", live) == "claude-opus-5"),
        ("resolve_alias_at(opus, 2026-07-20) -> claude-opus-4-8",
         lambda: mr.resolve_alias_at("opus", "2026-07-20", live) == "claude-opus-4-8"),
        ("resolve_alias_at(opus, a time after the last boundary) -> claude-opus-5-5",
         lambda: mr.resolve_alias_at("opus", "2026-09-23T01:00:00Z", live) == "claude-opus-5-5"),
        ("a date before the first evidenced service is unresolved, never guessed",
         lambda: mr.resolve_alias_at("opus", "2026-05-01", live) is None),
        ("fable before its 5.1 boundary -> claude-fable-5",
         lambda: mr.resolve_alias_at("fable", "2026-08-15", live) == "claude-fable-5"),
        ("an alias with no history serves its current row", lambda: mr.resolve_alias_at(
            "laguna", "2026-08-25", live) == "laguna-s-2.1-free"),
        ("an unknown alias resolves to None", lambda: mr.resolve_alias_at("mythos", "2026-09-10", live) is None),
        ("every history id is a registered row",
         lambda: all(mr.row_by_id(e["id"], live) for h in live["aliasHistory"].values() for e in h)),

        # ---- record identity: attested, then version, then direct served, then history ----
        ("a proxied record labels by attestedModel, not its servedModel",
         lambda: mr.record_model_id(proxied, "luna", "2026-09-01", live) == "gpt-5.6-luna"
                 and mr.label(mr.record_model_id(proxied, "luna", "2026-09-01", live), live)
                 == mr.row_by_id("gpt-5.6-luna", live)["label"]),
        ("...and a proxied servedModel alone is never identity (codex is not direct)",
         lambda: not mr.transport_is_direct("codex", live)
                 and mr.record_model_id({"transport": "codex", "servedModel": "gpt-5.4-mini"},
                                        "luna", "2026-09-01", live) == "gpt-5.6-luna"),
        ("a direct record labels by servedModel", lambda: mr.transport_is_direct("anthropic", live)
         and mr.record_model_id(direct, "opus", "2026-09-23", live) == "claude-opus-5"),
        ("a record with no served id falls back to the dated alias history",
         lambda: mr.record_model_id(bare, "opus", "2026-09-10", live) == "claude-opus-5"),
        ("an unregistered modelVersion is returned raw, never relabelled by the alias history",
         lambda: mr.record_model_id({"transport": "anthropic", "modelVersion": "claude-opus-6"},
                                    "opus", "2026-09-23", live) == "claude-opus-6"
                 and mr.row_by_id("claude-opus-6", live) is None),
        ("an unknown transport is never direct", lambda: not mr.transport_is_direct(None, live)
         and not mr.transport_is_direct("anthropic (Workflow)", live)),
        ("every model id in the census record set resolves to a row",
         lambda: [i for i in census_ids if mr.row_by_id(i, live) is None] == []),
        ("row_by_id strips the context suffix", lambda: mr.row_by_id("claude-opus-5-5[1m]", live)["id"]
         == "claude-opus-5-5"),
        ("row_by_id is None for an unregistered id, and label falls back to it",
         lambda: mr.row_by_id("mythos-x", live) is None and mr.label("mythos-x", live) == "mythos-x"),

        # ---- one model table ----
        ("lifecycle: opus-5-5 current, opus-5 superseded, opus-4-8 retired",
         lambda: [mr.lifecycle(i, live) for i in ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8")]
                 == ["current", "superseded", "retired"]),
        ("family groups the versions of one line",
         lambda: mr.family("claude-opus-5-5", live) == mr.family("claude-opus-5", live) == "opus"
                 and mr.family("gpt-6-sol", live) == mr.family("gpt-5.6-sol", live)),
        ("rows(lifecycle='current') holds each current row and no superseded one",
         lambda: {"claude-opus-5-5", "gpt-6.1-sol", "gpt-6-luna"} <= {m["id"] for m in mr.rows("current", live)}
                 and not {"claude-opus-5", "gpt-5.6-sol", "gpt-6-sol"} & {m["id"] for m in mr.rows("current", live)}),
        ("anthropic_ids lists the dispatchable Anthropic rows only",
         lambda: {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5"} <= set(mr.anthropic_ids(live))
                 and not any(i.startswith("gpt-") for i in mr.anthropic_ids(live))
                 and "claude-opus-4-8" not in mr.anthropic_ids(live)),
        ("every row carries label, family, version, lifecycle, order, costRank and prior",
         lambda: all(all(k in m for k in ("label", "family", "version", "lifecycle", "order",
                                          "costRank", "prior")) for m in live["models"])),
        ("codex effort-values come from the row, bounded by the transport's pin vocabulary",
         lambda: cli(["effort-values", "sol"])[1].strip() == '["low", "medium", "high", "xhigh", "max"]'),
        ("a row whose own effortValues share no rung with its transport is refused, never all rungs",
         lambda: raises_registry(lambda: mr.effort_values(
             dict(mr.resolve("sol", live), effortValues=["ultra"]), live))),
        ("a row declaring an empty effortValues list is refused, never all rungs",
         lambda: raises_registry(lambda: mr.effort_values(
             dict(mr.resolve("sol", live), effortValues=[]), live))),
        ("`available` names both versions of each moved alias",
         lambda: all(s in cli(["available"])[1] for s in (
             "opus     claude-opus-5-5", "opus5    claude-opus-5", "sol      gpt-6.1-sol",
             "sol56    gpt-5.6-sol", "luna     gpt-6-luna", "luna56   gpt-5.6-luna"))),
        ("every anthropic row carries its first-party list price: input, output, cache read, 5m and 1h writes",
         lambda: all(isinstance(m.get("price"), dict)
                     and all(isinstance(m["price"].get(k), (int, float)) for k in (
                         "cacheMissPer1M", "outputPer1M", "cacheHitPer1M",
                         "cacheWrite5mPer1M", "cacheWrite1hPer1M"))
                     and str(m["price"].get("source", "")).startswith("https://platform.claude.com/")
                     for m in live["models"] if m["transport"] == "anthropic")),
        ("`available` prints no dollar rate beside a plan-quota row and keeps it for a marginal-usd row",
         lambda: (lambda pairs: pairs and all(
             ("/1M in" in rate_line) == (live["transports"][row["transport"]]["costModel"] != "plan-quota")
             for row, rate_line in pairs if isinstance(row.get("price"), dict))
             and any("/1M in" in rate_line for _row, rate_line in pairs))(
             [(mr.row_by_id(ln.split()[1], live), lines[i + 1])
              for lines in [[ln for ln in cli(["available"])[1].splitlines() if ln.startswith("  ")]]
              for i, ln in enumerate(lines[:-1])
              if len(ln.split()) > 1 and mr.row_by_id(ln.split()[1], live)])),
        ("a price's optional cache-write rate must be a number",
         lambda: raises_registry(lambda: mr._validate(dict(live, models=[
             dict(m, price=dict(m["price"], cacheWrite5mPer1M="5")) if isinstance(m.get("price"), dict) else m
             for m in live["models"]]), "planted"))),

        # ---- A3: for-role picks no effort from ladder prose ----
        ("an Anthropic hop line carries no -e rung", lambda: not RUNG_FLAG.search(mr._sidecar_line(
            mr.resolve("opus", live), live))),
        ("...and points at the ladder cell instead", lambda: "ladder" in mr._sidecar_line(
            mr.resolve("opus", live), live)),
        ("an unmeasured row's placeholder rungs never become a printed -e",
         lambda: not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("terra", live), live))),
        ("a measured provider row prints no -e either: the ladder cell owns the rung",
         lambda: not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("flash", live), live))
                 and not RUNG_FLAG.search(mr._sidecar_line(mr.resolve("luna56", live), live))),
        ("`for-role scout` and `fanout` print no `-e` on any hop line",
         lambda: not any(RUNG_FLAG.search(ln + " ") for r in ("scout", "fanout")
                         for ln in cli(["for-role", r, "--from", "anthropic"])[1].splitlines())),
        ("`for-role executor --from codex` supplies no chosen effort on the opus hop",
         lambda: not RUNG_FLAG.search(cli(["for-role", "executor", "--from", "codex"])[1])),
        ("`for-role architect` on the live ladder lists opus in-transport and sol across the hop",
         lambda: (lambda r: r[0] == 0 and "claude-opus-5-5" in r[1] and "gpt-6.1-sol" in r[1])(
             cli(["for-role", "architect", "--from", "anthropic"]))),
        ("`for-role orchestrator --from codex` offers sol as nearest with its tier and direction",
         lambda: (lambda r: r[0] == 0 and any(
             "gpt-6.1-sol" in ln and "[`architect`, 1 tier below]" in ln
             for ln in r[1].splitlines()))(cli(["for-role", "orchestrator", "--from", "codex"]))),
        ("`for-role executor --from codex` lists sol in-transport (sol claims executor)",
         lambda: (lambda r: r[0] == 0 and "gpt-6.1-sol" in r[1].split("CROSS-TRANSPORT")[0])(
             cli(["for-role", "executor", "--from", "codex"]))),
        ("`--from` never reports the seat as resolved from this session",
         lambda: "resolved from this session" not in cli(["for-role", "executor", "--from", "codex"])[1]),
        ("`--check` runs the ladder check: the live ladder passes it",
         lambda: mr.check_ladder() is None),
        ("`for-role executor` on the live ladder still lists opus",
         lambda: (lambda r: r[0] == 0 and "claude-opus-5-5" in r[1])(
             cli(["for-role", "executor", "--from", "anthropic"]))),

        # ---- validation of the new fields ----
        ("aliasHistory naming an unregistered id is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][0].update(id="claude-opus-9"), "aliasHistory")),
        ("aliasHistory whose last entry is not the alias's current row is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"].pop(), "aliasHistory")),
        ("aliasHistory with non-increasing boundaries is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][1].update(
             until=d["aliasHistory"]["opus"][0]["until"]), "aliasHistory")),
        ("an aliasHistory boundary without evidence is rejected",
         lambda: rejects(lambda d: d["aliasHistory"]["opus"][0].pop("evidence"), "evidence")),
        # ---- one effort home: the ladder cell owns the rung of every row it covers ----
        ("no row carries `converged`/`open`: the ladder cell is the rung's one home",
         lambda: [m["alias"] for m in live["models"]
                  if {"converged", "open"} & set(m.get("effort") or {})] == []),
        ("a codex row takes `see-ladder`: the ladder cell owns a non-host row's rung too",
         lambda: mr.resolve("sol", live)["effort"].get("evidence") == "see-ladder"
                 and mr.resolve("flash", live)["effort"].get("evidence") == "see-ladder"),
        ("`for-role` prints the same effort phrase for a host row and a codex row",
         lambda: (lambda lines: len(lines) >= 2 and any("gpt-6.1-sol" in l for l in lines)
                  and any("claude-opus-5-5" in l for l in lines)
                  and all("effort per the ladder row" in l for l in lines))(
             [l for l in cli(["for-role", "executor", "--from", "codex"])[1].splitlines()
              if l.startswith("  ") and "claims " in l])),
        ("`see-ladder` validates on a non-host transport: the rung home is not host-only",
         lambda: accepts(lambda d: row(d, "sol").update(effort={"evidence": "see-ladder"}))),
        ("a `see-ladder` row carrying `converged`/`open` is rejected - two homes is the defect",
         lambda: rejects(lambda d: row(d, "sol").update(
             effort={"evidence": "see-ladder", "converged": "low"}), "converged")),
        ("a `measured` row carrying `converged`/`open` is rejected too: no row holds a rung",
         lambda: rejects(lambda d: row(d, "sol").update(
             effort={"evidence": "measured", "open": "high"}), "open")),
        ("a planted `gate.maxProviderBand` is rejected: the per-row plan ceiling is retired",
         lambda: rejects(lambda d: row(d, "sol")["gate"].update(maxProviderBand="Ahead"),
                         "maxProviderBand")),

        ("a lifecycle outside current|superseded|retired is rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(lifecycle="old"), "lifecycle")),
        ("two current rows in one family are rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(lifecycle="current"), "current")),
        ("a row without a label is rejected", lambda: rejects(lambda d: row(d, "opus").pop("label"), "label")),
        ("a prior outside frontier|mid|small is rejected",
         lambda: rejects(lambda d: row(d, "opus").update(prior="huge"), "prior")),
        ("a duplicate display order is rejected",
         lambda: rejects(lambda d: row(d, "opus5").update(order=row(d, "opus")["order"]), "order")),
    ]

    failed = 0
    for name, fn in cases:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def run_price_fresh_off_peak(data):
    """1M fresh tokens at half the list cache-miss rate -- the expected SC_PRICE_AT answer."""
    return next(m for m in data["models"] if m["alias"] == "flash")["price"]["cacheMissPer1M"] * 0.5


def _non_thinking_cases():
    """`effort.nonThinking`: the rungs at which a row does not think, validated against the rungs its
    transport forwards. Planted rows on a synthetic `host` transport; the last case reads the LIVE
    registry, because it proves the shipped file carries a valid declaration."""
    tcfg = {"effortValues": ["low", "medium", "high"]}

    def err(effort):
        entry = {"id": "host-fan-1", "alias": "h-fan", "transport": "host", "effort": effort}
        try:
            mr._validate_non_thinking(entry, {"transports": {"host": tcfg}, "models": [entry]}, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        except AttributeError as exc:
            return "no validator: %s" % exc
        return ""

    cases = [
        ("a row with no nonThinking passes", err({"evidence": "see-ladder"}) == ""),
        ("a declared forwarded rung with its ref passes",
         err({"evidence": "see-ladder", "nonThinking": ["low"], "nonThinkingRef": "ladder cell"}) == ""),
        ("a non-list nonThinking is rejected",
         "nonThinking" in err({"evidence": "see-ladder", "nonThinking": "low", "nonThinkingRef": "x"})),
        ("a rung the transport does not forward is rejected",
         "bogus" in err({"evidence": "see-ladder", "nonThinking": ["bogus"], "nonThinkingRef": "x"})),
        ("an empty list is rejected",
         "nonThinking" in err({"evidence": "see-ladder", "nonThinking": [], "nonThinkingRef": "x"})),
        ("a declaration with no nonThinkingRef is rejected",
         "nonThinkingRef" in err({"evidence": "see-ladder", "nonThinking": ["low"]})),
    ]
    try:
        live = mr.load()
        live_ok = any((m.get("effort") or {}).get("nonThinking") for m in live["models"])
    except mr.RegistryError as exc:
        live_ok = False
        print("  live registry: %s" % exc)
    cases.append(("LIVE registry: loads, and a row declares its non-thinking rung", live_ok))
    failed = 0
    for name, ok in cases:
        failed += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", name))
    return failed, len(cases)


def _avoid_cases():
    """`effort.avoid`: the rungs a row's measured waste keeps out of a pin, with `effort.avoidRef` citing the
    evidence. The route's vocabulary says what exists; the row's avoid says what is wasteful. Planted rows
    check the validator; the live cases prove the shipped file and the CLI the launchers read."""
    import contextlib
    import io
    import re

    def err(effort):
        entry = {"id": "host-fan-1", "alias": "h-fan", "transport": "host", "effort": effort}
        try:
            mr._validate_avoid(entry, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        except AttributeError:
            return "validator missing"
        return ""

    def cli(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mr.main(list(argv))
        return code, buf.getvalue().strip()

    cases = [
        ("a row with no avoid passes", err({"evidence": "see-ladder"}) == ""),
        ("an empty avoid passes", err({"evidence": "see-ladder", "avoid": []}) == ""),
        ("an avoided served rung with its ref passes",
         err({"evidence": "see-ladder", "avoid": ["max"], "avoidRef": "owner ruling"}) == ""),
        ("a non-list avoid is rejected", "avoid" in err({"evidence": "see-ladder", "avoid": "max", "avoidRef": "x"})),
        ("a rung the row does not serve may still be listed (a row narrowed from another row's keeps its list)",
         err({"evidence": "see-ladder", "avoid": ["bogus"], "avoidRef": "x"}) == ""),
        ("a non-empty avoid with no avoidRef is rejected",
         "avoidRef" in err({"evidence": "see-ladder", "avoid": ["max"]})),
    ]
    try:
        live = mr.load()
        vocab = mr.transport_effort_values("anthropic", live)
        rows = [m for m in live["models"] if m["transport"] == "anthropic" and m["lifecycle"] == "current"]
        avoiding = next((m for m in rows if mr.avoided_rungs(m)), None)
        rung = mr.avoided_rungs(avoiding)[0] if avoiding else None
        free = next((m for m in rows if rung and rung in mr.effort_values(m, live) and rung not in mr.avoided_rungs(m)), None)
        other = next((r for r in mr.effort_values(avoiding, live) if r not in mr.avoided_rungs(avoiding)), None) if avoiding else None
        cases += [
            ("LIVE: the Anthropic route's vocabulary carries max", "max" in vocab),
            ("LIVE: a current row avoids a rung and cites its evidence",
             bool(avoiding) and bool(mr.avoid_evidence(avoiding).strip())),
            ("LIVE: a current row serves that rung without avoiding it", bool(free)),
            ("LIVE: effort-avoid prints the evidence for an avoided rung",
             bool(avoiding) and cli("effort-avoid", avoiding["alias"], rung) == (0, mr.avoid_evidence(avoiding))),
            ("LIVE: effort-avoid prints nothing for a rung the row does not avoid",
             bool(avoiding and free and other) and cli("effort-avoid", avoiding["alias"], other) == (0, "")
             and cli("effort-avoid", free["alias"], rung) == (0, "")),
            ("LIVE: no row's avoidRef carries a provenance label or a date (instruction_quality section 5)",
             not any(re.search(r"owner|ruling|\d{4}-\d{2}-\d{2}", mr.avoid_evidence(m), re.I)
                     for m in live["models"] if mr.avoided_rungs(m))),
        ]
    except (mr.RegistryError, AttributeError, KeyError) as exc:
        cases.append(("LIVE registry: avoid accessors and rows", False))
        print("  live registry: %s: %s" % (type(exc).__name__, exc))
    failed = 0
    for name, ok in cases:
        failed += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", name))
    return failed, len(cases)


def _probation_cases(live):
    """Probation (orchestration §5). A model is on probation for a tier when the
    owner marks it, or when it has neither a `roles` claim nor a clean-rated archive row at that tier.
    Computation runs on a planted dict and a planted archive; validation plants into the live
    registry, whose transport rules fire before any model field."""
    import json
    import tempfile

    data = {"transports": {"host": {}}, "models": [
        {"transport": "host", "id": "host-exec-1", "alias": "h-exec", "roles": ["architect", "executor"],
         "probation": [{"tier": "executor", "text": "planted mark", "evidenceRef": "fixture"}]},
        {"transport": "host", "id": "host-fan-1", "alias": "h-fan", "roles": ["fanout"]},
    ]}
    folder = tempfile.mkdtemp(prefix="mreg_prob_")

    def archive(rows):
        path = os.path.join(folder, "metrics_%d.jsonl" % len(os.listdir(folder)))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        return path

    empty = archive([])
    unclaimed = "no executor claim in registry roles and no clean-rated executor dispatch"

    def prob(model, tier, path=empty):
        try:
            return mr.probation(model, tier, data=data, archive_path=path)
        except AttributeError as exc:
            return "no probation(): %s" % exc

    pick = next(m["alias"] for m in live["models"]
                if "executor" in (m.get("roles") or []) and "scout" not in m["roles"])

    def rejects(mutate, needle):
        d = copy.deepcopy(live)
        mutate(next(m for m in d["models"] if m["alias"] == pick))
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def accepts(mutate):
        d = copy.deepcopy(live)
        mutate(next(m for m in d["models"] if m["alias"] == pick))
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError:
            return False
        return True

    mark = {"tier": "executor", "text": "why", "evidenceRef": "ref"}

    def cli(argv):
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_"), "external_models.json")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(live, fh)
        old = os.environ.get(mr.ENV_OVERRIDE)
        os.environ[mr.ENV_OVERRIDE] = tmp
        out, err = io.StringIO(), io.StringIO()
        real_archive = getattr(mr, "metrics_archive", None)
        mr.metrics_archive = lambda: empty
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = mr.main(argv)
                except mr.RegistryError as exc:
                    print(exc, file=sys.stderr)
                    code = 2
        finally:
            mr.metrics_archive = real_archive
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return code, out.getvalue()

    claimed_code, claimed_out = cli(["review-state", pick, "executor"])
    open_code, open_out = cli(["review-state", pick, "scout"])
    clean = {"model": "host-fan-1[1m]", "tier": "executor", "outcome": "clean"}

    cases = [
        ("an owner mark puts a claimed tier on probation, naming the mark",
         prob("h-exec", "executor") == "owner mark: planted mark"),
        ("...and leaves the row's other claimed tier off it", prob("h-exec", "architect") is None),
        ("an unclaimed tier with no archive row is on probation", prob("h-fan", "executor") == unclaimed),
        ("one clean archive row at that tier, for the same row, takes it off",
         prob("h-fan", "executor", archive([clean])) is None),
        ("a defects row does not lift it",
         prob("h-fan", "executor", archive([dict(clean, outcome="defects")])) == unclaimed),
        ("a clean row at another tier does not lift it",
         prob("h-fan", "executor", archive([dict(clean, tier="architect")])) == unclaimed),
        ("a clean row for another model does not lift it",
         prob("h-fan", "executor", archive([dict(clean, model="host-exec-1")])) == unclaimed),
        ("a claimed tier never reads the archive (an unreadable path costs nothing)",
         prob("h-fan", "fanout", folder) is None),
        ("a mark missing evidenceRef fails _validate",
         rejects(lambda m: m.__setitem__("probation", [dict(mark, evidenceRef="")]), "probation")),
        ("a mark with a tier outside TIER_ORDER fails _validate",
         rejects(lambda m: m.__setitem__("probation", [dict(mark, tier="Wide")]), "probation")),
        ("a well-formed mark passes _validate", accepts(lambda m: m.__setitem__("probation", [mark]))),
        ("review-state on a claimed tier prints `probation: none` and nothing else",
         claimed_code == 0 and claimed_out.splitlines() == ["probation: none"]),
        ("review-state on an unclaimed tier prints its basis",
         open_code == 0 and open_out.startswith("probation: no scout claim in registry roles")),
        ("review-state exits 2 on an unknown alias", cli(["review-state", "no-such", "executor"])[0] == 2),
        ("review-state exits 2 on an unknown tier", cli(["review-state", pick, "Wide"])[0] == 2),
    ]
    failed = 0
    for name, ok in cases:
        failed += not ok
        print("%s %s" % ("ok  " if ok else "FAIL", name))
    return failed, len(cases)


# ---------------------------------------------------------------- ladder families and evidence
# A ladder `model` cell names a product-line family: it covers that family's rows on every
# transport, plus the row an existing cell names by alias. A `scout pool` cell covers each family it
# lists. The `evidence` cell is a `+`-joined subset of pub/bench/use.
FAMILY_DATA = {
    "transports": {"anthropic": {}, "codex": {}, "go": {}},
    "models": [
        {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable", "family": "fable",
         "roles": ["orchestrator"]},
        {"transport": "anthropic", "id": "claude-opus-5-5", "alias": "opus", "family": "opus",
         "roles": ["architect", "executor"]},
        {"transport": "codex", "id": "gpt-6-luna", "alias": "luna", "family": "luna", "roles": ["fanout"]},
        {"transport": "go", "id": "go/gpt-5.6-luna", "alias": "go-luna56", "family": "luna", "roles": []},
        {"transport": "go", "id": "kimi-k3", "alias": "go-kimi", "family": "kimi", "roles": ["fanout"]},
        {"transport": "go", "id": "minimax-m3", "alias": "go-minimax", "family": "minimax", "roles": ["scout"]},
        {"transport": "go", "id": "go/hy3", "alias": "go-hy3", "family": "hy", "roles": ["scout"]},
    ],
}
FAMILY_ROWS = ["| fable | x | use | high |", "| opus | x | bench | high |", "| luna | x | bench+use | low |",
               "| kimi | x | pub | high |", "| scout pool (minimax, hy) | x | pub | low |"]


def _family_ladder(rows=None, header="| model | strengths | evidence | effort |"):
    rows = FAMILY_ROWS if rows is None else rows
    return ["## Role guidance\n", "\n", header + "\n", "|---|---|---|---|\n"] + [r + "\n" for r in rows]


def _family_check_error(rows=None, header="| model | strengths | evidence | effort |"):
    """check_ladder over a temp family ladder; the error text, '' when it passes."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="mladder_"), "ladder.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(_family_ladder(rows, header))
    try:
        mr.check_ladder(path, data=FAMILY_DATA)
    except mr.RegistryError as exc:
        return str(exc)
    return ""


def _with_evidence(cell):
    return [r.replace("| pub |", "| %s |" % cell) if r.startswith("| kimi ") else r for r in FAMILY_ROWS]


INTENDED_GO_FANOUT = {"go-deepseek-flash", "go-mimo-v2-6-flash", "go-qwen3-8-flash", "go-gpt-6-luna",
                      "go-mimo-v2-6-pro"}
INTENDED_GO_NO_ROLE = {"go-kimi", "go-glm", "go-qwen3-8-max", "go-grok-4-7", "go-hy4-preview"}
INTENDED_GO_SCOUT = {"go-minimax-m3", "go-longcat-2-0", "go-kimi-k2-7-code", "go-hy3"}


def _row_versions():
    import tempfile
    versions = {"claude-fable-5-1": ("f-2", "current"), "claude-opus-5-5": ("o-2", "current"),
                "gpt-6-luna": ("l-1", "current"), "go/gpt-5.6-luna": ("l-1b", "current"),
                "kimi-k3": ("k-1", "superseded"), "minimax-m3": ("m-1", "current"), "go/hy3": (None, "current")}
    data = {"transports": FAMILY_DATA["transports"],
            "models": [dict(m, version=versions[m["id"]][0], lifecycle=versions[m["id"]][1])
                       for m in FAMILY_DATA["models"]]}
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "ladder.md")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(_family_ladder())
        return mr.ladder_row_versions(path=path, data=data)


def _ladder_family_cases(live):
    def ids(cell):
        return sorted(m["id"] for m in mr.ladder_rows(cell, FAMILY_DATA))

    def cross(role):
        return {m["alias"] for m in mr.for_role(role, "anthropic", live)["crossTransport"]}

    cases = [
        ("a family row resolves to its versions on two transports",
         lambda: ids("luna") == ["go/gpt-5.6-luna", "gpt-6-luna"]),
        ("a family row with no alias of its own resolves by family",
         lambda: ids("kimi") == ["kimi-k3"]),
        ("a scout pool row resolves to each member family",
         lambda: ids("scout pool (minimax, hy)") == ["go/hy3", "minimax-m3"]),
        ("a family row and the scout pool carry their members' tier claims",
         lambda: mr.parse_role_tiers(_family_ladder(), FAMILY_DATA)
         == {"orchestrator": ["fable"], "architect": ["opus"], "executor": ["opus"],
             "fanout": ["luna", "kimi"], "scout": ["scout pool"]}),
        ("a family-covered and a pool-covered claimant pass the ladder check",
         lambda: _family_check_error() == ""),
        ("a claimant whose family has no row fails the check, naming it",
         lambda: "go-kimi" in _family_check_error([r for r in FAMILY_ROWS if not r.startswith("| kimi ")])),
        ("a pool member missing from the pool list fails the check, naming it",
         lambda: "go-hy3" in _family_check_error(
             [r.replace("(minimax, hy)", "(minimax)") for r in FAMILY_ROWS])),
        ("an evidence cell `pub+bench` is accepted", lambda: _family_check_error(_with_evidence("pub+bench")) == ""),
        ("...in any order", lambda: _family_check_error(_with_evidence("use+pub")) == ""),
        ("a misspelled evidence tag is rejected, naming the row and the tag",
         lambda: "kimi" in _family_check_error(_with_evidence("pubb"))
         and "pubb" in _family_check_error(_with_evidence("pubb"))),
        ("an empty evidence cell is rejected", lambda: "evidence" in _family_check_error(_with_evidence(""))),
        ("a repeated evidence tag is rejected", lambda: "evidence" in _family_check_error(_with_evidence("pub+pub"))),
        ("a removed evidence column parses to nothing",
         lambda: mr.parse_role_tiers(_family_ladder([r.replace(" | pub |", " |").replace(" | bench |", " |")
                                                     .replace(" | use |", " |").replace(" | bench+use |", " |")
                                                     for r in FAMILY_ROWS], "| model | strengths | effort |"),
                                     FAMILY_DATA) == {}),
        ("...and fails the ladder check",
         lambda: _family_check_error(FAMILY_ROWS, "| model | strengths | effort | note |") != ""),
        ("ladder_row_versions lists each row's current versions, skipping superseded ones and rows with none",
         lambda: _row_versions() == {"fable": ["f-2"], "opus": ["o-2"], "luna": ["l-1", "l-1b"], "kimi": [],
                                     "scout pool": ["m-1"]}),
        ("LIVE: `for-role fanout` lists the intended Go rows across the hop",
         lambda: INTENDED_GO_FANOUT <= cross("fanout")),
        ("LIVE: `for-role fanout` never lists a Go row the plan does not run",
         lambda: not (INTENDED_GO_NO_ROLE & cross("fanout"))),
        ("LIVE: `for-role scout` lists the scout pool's Go rows across the hop",
         lambda: INTENDED_GO_SCOUT <= cross("scout")),
        ("LIVE: every live ladder row carries a valid evidence cell",
         lambda: all(mr.evidence_problem(r["evidence"]) is None for r in mr.parse_ladder_rows(
             mr.ladder_path().read_text(encoding="utf-8").splitlines(True), ("model", "evidence")))
         and len(mr.parse_ladder_rows(mr.ladder_path().read_text(encoding="utf-8").splitlines(True),
                                      ("model", "evidence"))) >= 9),
    ]
    failed = 0
    for name, fn in cases:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def _supplementary_cases(live):
    """Registry `supplementary`: a tier a row fills only as an added review seat. `roles` stays the claim.
    Plants into a copy of the live registry; the shipped data carries no `supplementary` until S5a."""
    haiku = next(m for m in live["models"] if m["alias"] == "haiku")

    def planted(roles=None, supplementary=("architect",), evidence=None):
        d = copy.deepcopy(live)
        row = next(m for m in d["models"] if m["alias"] == "haiku")
        if roles is not None:
            row["roles"] = list(roles)
        if supplementary is not None:
            row["supplementary"] = list(supplementary)
        if evidence:
            row["effort"] = dict(row["effort"], evidence=evidence)
        return d

    def rejects(d, needle):
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return needle in str(exc)
        return False

    def run(fn, *a, **k):
        try:
            return fn(*a, **k)
        except AttributeError as exc:
            return "missing: %s" % exc

    d = planted()
    row = next(m for m in d["models"] if m["alias"] == "haiku")
    flash = copy.deepcopy(row)
    flash["roles"] = ["architect", "executor"]
    both = copy.deepcopy(live)
    both["models"] = [m for m in both["models"] if m["alias"] != "haiku"] + [dict(flash, supplementary=["architect"])]
    cases = [
        ("supplementary accepts tier tokens", lambda: mr._validate(planted(), "fixture") is None),
        ("supplementary refuses a non-tier token", lambda: rejects(planted(supplementary=["wizard"]), "supplementary")),
        ("supplementary refuses a repeat", lambda: rejects(planted(supplementary=["architect", "architect"]), "supplementary")),
        ("supplementary refuses a non-list", lambda: rejects(planted(supplementary="architect"), "supplementary")),
        ("supplementary refuses an unmeasured row", lambda: rejects(
            planted(roles=[], evidence="unmeasured"), "supplementary")),
        ("supplementary_tiers lists in TIER_ORDER",
         lambda: run(mr.supplementary_tiers, {"supplementary": ["scout", "architect"]}) == ["architect", "scout"]),
        ("supplementary_tiers is [] when absent", lambda: run(mr.supplementary_tiers, {"roles": ["fanout"]}) == []),
        ("seat_standing: qualified", lambda: run(mr.seat_standing, {"roles": ["fanout"]}, "fanout") == "qualified"),
        ("seat_standing: supplementary-only", lambda: run(mr.seat_standing, row, "architect") == "supplementary"),
        ("seat_standing: plain+supplementary is supplementary",
         lambda: run(mr.seat_standing, both["models"][-1], "architect") == "supplementary"),
        ("seat_standing: none", lambda: run(mr.seat_standing, row, "orchestrator") is None),
        ("rows_for_tier excludes a supplementary-only row",
         lambda: "haiku" not in aliases(mr.rows_for_tier("architect", d))),
        ("entry_tiers ignores supplementary", lambda: "architect" not in mr.entry_tiers(row)),
        ("supplementary_rows lists the row",
         lambda: "haiku" in aliases(run(mr.supplementary_rows, "architect", d))),
        ("for_role lists the supplementary row apart from the claimants", lambda: (
            "haiku" in aliases(run(lambda: mr.for_role("architect", "anthropic", d)["supplementary"]))
            and "haiku" not in aliases(mr.for_role("architect", "anthropic", d)["inTransport"]))),
        ("check_ladder demands a ladder row for a supplementary-only claim", lambda: rejects_ladder(d)),
    ]

    def rejects_ladder(data):
        data = copy.deepcopy(data)
        for m in data["models"]:
            if m["alias"] == "haiku":
                m.update(roles=[], family="orphanfam", alias="orphan", id="orphan-1")
        path = mr.ladder_path()
        try:
            mr.check_ladder(path=path, data=data)
        except mr.RegistryError as exc:
            return "orphan" in str(exc) and "architect" in str(exc)
        except Exception:
            return False
        return False

    def cli_for_role(data):
        import json
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_supp_"), "external_models.json")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        old = os.environ.get(mr.ENV_OVERRIDE)
        os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                mr.main(["for-role", "architect", "--from", "anthropic"])
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return out.getvalue()

    def cli_available(data):
        import json
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(prefix="mreg_supp_"), "external_models.json")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        old = os.environ.get(mr.ENV_OVERRIDE)
        os.environ[mr.ENV_OVERRIDE] = tmp
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                mr.main(["available"])
        finally:
            if old is None:
                os.environ.pop(mr.ENV_OVERRIDE, None)
            else:
                os.environ[mr.ENV_OVERRIDE] = old
        return out.getvalue()

    cases += [
        ("for-role prints the supplementary block under its header",
         lambda: "added review seats only: never lone, never author" in cli_for_role(d)),
        ("available prints supplementary= beside roles=",
         lambda: "supplementary=architect" in cli_available(d)),
    ]
    failed = 0
    for name, fn in cases:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    return failed, len(cases)


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    # ---- live SSOT coupling ------------------------------------------------
    live_tiers = mr.role_tiers()
    ok = sorted(live_tiers) == sorted(mr.TIER_ORDER)
    failed += not ok
    print("%s the live ladder claims every tier: %r" % ("ok  " if ok else "FAIL", sorted(live_tiers)))

    # A publication context (or any consumer whose external_models.json is not this project's own)
    # can carry a registry `load()` rejects for reasons unrelated to this suite's own cases -- a
    # bare crash here would abort every case below with a raw traceback and no tally instead of
    # one clean FAIL, the same "proof ran to completion" shape guarded elsewhere in this battery.
    try:
        live = mr.load()
    except Exception as exc:
        failed += 1
        total = len(CASES) + 2
        print("FAIL the live registry loads for the SSOT-coupling cases below  raised %s: %s"
              % (type(exc).__name__, exc))
        print("\n%d/%d passed" % (total - failed, total))
        return 1 if failed else 0
    ok = all(mr.rows_for_tier(t, live) for t in mr.TIER_ORDER)
    failed += not ok
    print("%s every tier has at least one available row in the live registry"
          % ("ok  " if ok else "FAIL"))

    # `--check` must stay green: the harness_tests stamp the commit needs depends on it, and this
    # slice deliberately adds no validation that the shipped `roles` free-text would red.
    ok = mr._cmd_check([]) == 0
    failed += not ok
    print("%s `--check` still accepts the shipped registry" % ("ok  " if ok else "FAIL"))

    # An unvalidated closed set is unenforceable: `sidcar` is not `all`, so a typo would silently
    # narrow the exclusion to hops and leave the row dispatchable where it was meant to be gone.
    # Built by planting the typo in the SHIPPED registry, not in a minimal dict: the validator has
    # transport-level rules that fire first, so a hand-built fixture reds on an unrelated field and
    # the case would pass without ever reaching the scope check.
    import copy

    def why(scope):
        d = copy.deepcopy(live)
        for m in d["models"]:
            if m.get("alias") == "astra":
                m["status"]["scope"] = scope
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        return ""

    ok = "status.scope" in why("sidcar")
    failed += not ok
    print("%s `--check` rejects an unknown status.scope: %r"
          % ("ok  " if ok else "FAIL", why("sidcar")[:70]))

    ok = why("sidecar") == "" and why("all") == ""
    failed += not ok
    print("%s ...and accepts both legal scopes" % ("ok  " if ok else "FAIL"))

    def why_limits(patch):
        d = copy.deepcopy(live)
        row = next(m for m in d["models"] if m.get("alias") == "luna")
        row["limits"].update(patch)
        try:
            mr._validate(d, "fixture")
        except mr.RegistryError as exc:
            return str(exc)
        return ""

    invalid_limit_cases = [
        ({"contextTokens": "272k"}, "limits.contextTokens"),
        ({"contextTokens": 0}, "limits.contextTokens"),
        ({"maxContextTokens": -1}, "limits.maxContextTokens"),
        ({"effectiveContextPercent": "95"}, "limits.effectiveContextPercent"),
        ({"effectiveContextPercent": 0}, "limits.effectiveContextPercent"),
        ({"effectiveContextPercent": 101}, "limits.effectiveContextPercent"),
        ({"contextTokens": 872000, "maxContextTokens": 272000}, "maxContextTokens"),
    ]
    for patch, field in invalid_limit_cases:
        reason = why_limits(patch)
        ok = field in reason
        failed += not ok
        print("%s `--check` rejects malformed limits %r: %r"
              % ("ok  " if ok else "FAIL", patch, reason[:90]))

    ok = mr.status_scope({"status": {"state": "unavailable", "reason": "r"}}) == "all"
    failed += not ok
    print("%s an exclusion with no scope defaults to `all`" % ("ok  " if ok else "FAIL"))

    # The arity check used to run on the UNSTRIPPED argv, so `--from codex` with no role passed it
    # and then raised IndexError at args[0]. main() catches only RegistryError -> raw traceback.
    ok = mr._cmd_for_role(["--from", "codex"]) == 2
    failed += not ok
    print("%s `for-role --from codex` with no role exits 2, not a traceback"
          % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["--from"]) == 2
    failed += not ok
    print("%s `--from` with no name exits 2" % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["executor", "--from", "nosuchtransport"]) == 2
    failed += not ok
    print("%s an unregistered transport exits 2" % ("ok  " if ok else "FAIL"))

    ok = mr._cmd_for_role(["executor", "--from", "codex"]) == 0
    failed += not ok
    print("%s a well-formed for-role still exits 0" % ("ok  " if ok else "FAIL"))

    # The header points at each row's native pin: Anthropic accepts alias or ID; provider seats
    # require the canonical ID. A blanket "alias" rule breaks off-transport Workflow calls.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mr._cmd_for_role(["executor", "--from", "anthropic"])
    header = next((l for l in buf.getvalue().splitlines() if l.startswith("IN-TRANSPORT")), "")
    ok = "native Workflow/Agent pin" in header and "alias, not the id" not in header
    failed += not ok
    print("%s the in-transport header defers to each row's native pin (%r)"
          % ("ok  " if ok else "FAIL", header))

    # ---- time-of-day pricing, peak gate policy, version-bound evidence, effort vocabulary ----
    # Fixture = the SHIPPED registry with a schedule planted on the deepseek transport, for the same
    # reason as the scope cases: transport-level rules fire first on a hand-built dict.
    sched_failed, sched_total = _schedule_cases(live)
    failed += sched_failed

    ident_failed, ident_total = _identity_cases(live)
    failed += ident_failed

    nt_failed, nt_total = _non_thinking_cases()
    failed += nt_failed

    av_failed, av_total = _avoid_cases()
    failed += av_failed

    fam_failed, fam_total = _ladder_family_cases(live)
    failed += fam_failed

    prob_failed, prob_total = _probation_cases(live)
    failed += prob_failed

    supp_failed, supp_total = _supplementary_cases(live)
    failed += supp_failed

    total = len(CASES) + 3 + 3 + len(invalid_limit_cases) + 5
    total += sched_total + ident_total + nt_total + av_total + fam_total + prob_total + supp_total
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
