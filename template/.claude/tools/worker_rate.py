#!/usr/bin/env python3
"""Ground-truth ratings for ai-worker calls — the axis the ledger cannot measure itself.

`~/.claude/ai_worker_calls.jsonl` (written by the ai-worker MCP server) records MECHANICAL
signals: `degenerate`, `truncated`, `error`, latency, chars. All of them detect a call that
COLLAPSED. None detects the failure that actually costs you: a fluent, well-formed digest that
is simply wrong about the files. On a local model that is the dominant risk, and it scores
clean today.

Only the consumer can supply that verdict, and only at the moment of consumption — which is why
this is a tool you call, not a screen the server runs.

RATINGS ARE A SEPARATE APPEND-ONLY FILE, joined on (ts, tool). The server owns the ledger and
appends to it from another process; rewriting it here to add a field would race that append and
can lose rows. Nothing in this tool ever opens the ledger for writing.

Auditing needs the call's inputs and output, which the ledger does not hold. `server.py`
writes one artifact per call to `~/.claude/ai_worker_calls/` — paths, question, raw
response, cwd and the repo SHA at call time — and the ledger row points at it. `show`
is the audit surface; a row with no `artifact` predates capture and is unauditable, which
it says rather than rendering an empty section.

  worker_rate.py show <ts> [--full]
  worker_rate.py rate <outcome> --anchor "<claim> | <file:line>" --derivation LEVEL
                                [--note TEXT] [--ts TS]
  worker_rate.py report [--since YYYY-MM-DD]
  worker_rate.py unrated [--since YYYY-MM-DD] [--breadth narrow|wide] [--with-artifact]

A verdict also carries WHERE it was taken. `breadth` is computed from the ledger row;
`derivation` is rated, because no field can say whether the answer was copied or inferred.
`report` prints them as a grid, never as a composite score — see the DERIVATION block below
and `print_grid`.

Outcomes separate the two quality failures that matter:

  faithful    everything you checked against the source was right
  incomplete  right about what it said, but missed material it was asked for  (recall miss)
  fabricated  asserted something the source does not support                  (invention)
  unusable    collapsed, truncated, off-task, or unparseable

`fabricated` is the one that must never be silent: it is the only outcome that makes downstream
work wrong rather than merely thin.
"""

import argparse
import collections
import json
import os
import pathlib
import sys

# Env overrides let subprocess tests use an isolated store. Module-level reassignment cannot
# isolate a child process because it imports this module afresh. Without the override, a test
# that selects the newest row could append an unverified rating to the live store.
_HOME = pathlib.Path.home() / ".claude"
LEDGER = pathlib.Path(os.environ.get("AI_WORKER_LEDGER") or _HOME / "ai_worker_calls.jsonl")
RATINGS = pathlib.Path(os.environ.get("AI_WORKER_RATINGS") or _HOME / "ai_worker_ratings.jsonl")
ARTIFACTS = pathlib.Path(os.environ.get("AI_WORKER_ARTIFACTS") or _HOME / "ai_worker_calls")

# Fidelity outcomes require a source anchor. A bare positive verdict cannot be audited later,
# and fluent output is the case this rating is meant to test.
FIDELITY = ("faithful", "incomplete", "fabricated")
OUTCOMES = FIDELITY + ("unusable",)

# ── Difficulty coordinates ────────────────────────────────────────────────────
# A verdict needs its difficulty coordinates. The same label on two small files and a wide
# synthesis does not describe the same capability.
#
# Two axes, split by who can answer them, and that split is the whole design:
#
#   BREADTH is DERIVED from the ledger row and never stored. Anything computable must be
#   computed — hand-scoring it adds rater drift to a number already on disk, and a stored
#   copy would drift from the row it duplicates.
#
#   DERIVATION is RATED, because nothing on the row can tell whether the answer was sitting
#   in the files or had to be inferred. It reuses CLAUDE.md's routing vocabulary (COPYABLE
#   vs DERIVED) rather than inventing a private scale, so a finding here IS a routing rule.
#
# Both are small ordinal bands, never a 1-10 score: nobody distinguishes a 6 from a 7, and
# the resulting central-tendency clustering is not comparable across sessions.
DERIVATION = ("copyable", "linked", "derived")
DERIVATION_HELP = {
    "copyable": "every fact was present in the supplied files — extraction or transcription",
    "linked": "facts had to be joined across files, but each join was stated in them",
    "derived": "required inference, ordering or chaining; nothing stated the answer",
}

# Defaults split breadth by either file count or prompt size. OR is intentional: a few large
# files can load the model as much as many small ones.
BREADTH_FILES = 5
BREADTH_TOKENS = 10_000
BREADTHS = ("narrow", "wide")


def breadth_of(row):
    """Which breadth band a call sits in, or '?' when the row cannot say.

    Unknown groups SEPARATELY rather than defaulting into a band. Same discipline the kv
    column already uses here: a row that predates capture has an unrecoverable coordinate,
    and assuming one would silently move a call into a cell it was never measured in.
    """
    files = row.get("paths_requested")
    toks = row.get("prompt_tokens")
    if not files and not toks:
        return "?"
    if (files or 0) >= BREADTH_FILES or (toks or 0) >= BREADTH_TOKENS:
        return "wide"
    return "narrow"


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn final line is a rounding error, not a reason to fail
    return out


def load_joined(since=None):
    """Ledger rows with `outcome`/`note` merged in from the ratings file.

    Later ratings win, so re-rating a row is an append rather than an edit.
    """
    rated = {}
    for r in read_jsonl(RATINGS):
        rated[(r.get("ts"), r.get("tool"))] = r
    rows = []
    for row in read_jsonl(LEDGER):
        if since and (row.get("ts") or "") < since:
            continue
        hit = rated.get((row.get("ts"), row.get("tool")))
        row["outcome"] = hit.get("outcome") if hit else None
        row["note"] = hit.get("note") if hit else None
        # Rated coordinate, merged like the verdict. Older ratings predate the axis and
        # carry None, which reads as UNCHARACTERISED rather than as any level.
        row["derivation"] = hit.get("derivation") if hit else None
        # Derived coordinate, computed fresh from the row every time it is read. Never
        # stored, so it cannot disagree with the ledger it comes from.
        row["breadth"] = breadth_of(row)
        rows.append(row)
    return rows


def cmd_rate(args):
    if args.outcome in FIDELITY and not args.anchor:
        print(f"'{args.outcome}' is a fidelity verdict and needs --anchor: the claim you "
              f"checked and the file:line that settles it.\n"
              f"  --anchor 'said the controller owns retries | src/Controller.cs:54 has no retry "
              f"member'\n"
              f"Rate 'unusable' instead if the call collapsed and there was nothing to check.",
              file=sys.stderr)
        return 2

    # Same gate as --anchor, for the same reason. An unstratified fidelity verdict is not a
    # cheap rating, it is an uninterpretable one: it lands in no cell, so it cannot support
    # or refute a routing decision and it inflates coverage while measuring nothing.
    # `unusable` is exempt — the call collapsed, so there is no answer whose derivation
    # could be characterised.
    if args.outcome in FIDELITY and not args.derivation:
        print(f"'{args.outcome}' is a fidelity verdict and needs --derivation: how much work "
              f"the answer required beyond copying.\n"
              + "".join(f"  --derivation {k:<9s} {v}\n" for k, v in DERIVATION_HELP.items())
              + "This is the axis no ledger field can supply, and a verdict without it "
                "pools a hard call with an easy one.",
              file=sys.stderr)
        return 2

    rows = read_jsonl(LEDGER)
    if not rows:
        print(f"no ledger at {LEDGER}", file=sys.stderr)
        return 2

    if args.ts:
        target = next((r for r in rows if (r.get("ts") or "").startswith(args.ts)), None)
    else:
        # Newest call, optionally of one tool. Rating happens right after consuming a result,
        # so "the last one" is the overwhelmingly common case and is worth not having to name.
        cands = [r for r in rows if not args.tool or r.get("tool") == args.tool]
        target = cands[-1] if cands else None
    if target is None:
        print("no matching ledger row", file=sys.stderr)
        return 2

    RATINGS.parent.mkdir(parents=True, exist_ok=True)
    with RATINGS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": target.get("ts"),
            "tool": target.get("tool"),
            "outcome": args.outcome,
            "derivation": args.derivation,
            "anchor": args.anchor,
            "note": args.note,
        }) + "\n")

    band = breadth_of(target)
    print(f"rated {target.get('ts')} {target.get('tool')} -> {args.outcome}"
          f"  [{band} x {args.derivation or 'uncharacterised'}]"
          + (f"  ({args.note})" if args.note else ""))
    if args.outcome == "fabricated":
        print("  fabricated: the mechanical screens passed this call. Check whether the same "
              "spec shape has produced invention before — `report` groups by tool+model.")
    return 0


def cmd_report(args):
    rows = load_joined(args.since)
    if not rows:
        print("no calls in range")
        return 0

    agg = collections.defaultdict(lambda: collections.Counter())
    lat = collections.defaultdict(list)
    for r in rows:
        # KV quant is part of the key, not a display column. Pooling different quantizations
        # can blend distinct accuracy and speed profiles into one misleading model row.
        key = (r.get("tool"), (r.get("model") or "-").split("/")[-1][:28],
               r.get("kv_cache_type") or "-")
        a = agg[key]
        a["n"] += 1
        for flag in ("degenerate", "truncated"):
            if r.get(flag):
                a[flag] += 1
        if r.get("error"):
            a["error"] += 1
        a[r["outcome"] or "UNRATED"] += 1
        # Mechanical recall miss: fewer files discussed than were handed over. No
        # judgment involved, and no other flag detects it.
        cov, req = r.get("paths_covered"), r.get("paths_requested")
        if cov is not None and req and cov < req:
            a["partial"] += 1
        if r.get("latency_ms"):
            lat[key].append(r["latency_ms"])

    hdr = ("tool / model / kv", "n", "med_s", "degen", "trunc", "err", "partial", "faith",
           "incompl", "FABRIC", "unusable", "UNRATED")
    print(f"{hdr[0]:<52s}" + "".join(f"{h:>9s}" for h in hdr[1:]))
    for key, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        L = sorted(lat[key])
        med = f"{L[len(L)//2]/1000:.0f}" if L else "-"
        print(f"{key[0] or '-'} / {key[1]} / {key[2]}"[:52].ljust(52)
              + f"{a['n']:>9d}{med:>9s}{a['degenerate']:>9d}{a['truncated']:>9d}"
              f"{a['error']:>9d}{a['partial']:>9d}{a['faithful']:>9d}{a['incomplete']:>9d}"
              f"{a['fabricated']:>9d}{a['unusable']:>9d}{a['UNRATED']:>9d}")

    unrated = sum(1 for r in rows if not r["outcome"])
    fab = sum(1 for r in rows if r["outcome"] == "fabricated")
    print()
    # State coverage before any verdict. Unrated rows are unknown, so omitting them would make
    # a shrinking denominator look like a quality gain.
    print(f"rated coverage: {len(rows)-unrated}/{len(rows)} "
          f"({100*(len(rows)-unrated)/len(rows):.0f}%) - unrated is UNKNOWN, not clean")
    if fab:
        print(f"FABRICATIONS: {fab} — these passed every mechanical screen")

    print_grid(rows)
    return 0


def print_grid(rows):
    """Fidelity by difficulty cell — the frontier, not a score.

    The axes stay orthogonal on purpose. Collapsing them into one composite number would
    discard exactly what stratifying bought: a scalar cannot answer "can this model take a
    12-file synthesis", which is the only question the ratings are for. What answers it is
    the LINE between cells that hold clean ratings and cells that do not.

    An empty cell prints as UNKNOWN rather than being omitted or read as clean. Empty cells
    are the backlog: they name the calls worth rating next.
    """
    rated = [r for r in rows if r["outcome"]]
    if not rated:
        print("\nno rated calls in range — the grid is entirely UNKNOWN")
        return

    keys = sorted({(r.get("tool"), (r.get("model") or "-").split("/")[-1][:24],
                    r.get("kv_cache_type") or "-") for r in rated})
    cols = DERIVATION + (None,)  # None = rated before the axis existed
    for key in keys:
        mine = [r for r in rated if (r.get("tool"), (r.get("model") or "-").split("/")[-1][:24],
                                     r.get("kv_cache_type") or "-") == key]
        print(f"\nFIDELITY BY DIFFICULTY — {key[0]} / {key[1]} / kv={key[2]}")
        print("  cell = faithful/rated, ! marks a cell containing a fabrication or "
              "incomplete, UNKNOWN = never measured")
        print("  " + "".ljust(10) + "".join(
            f"{(c or 'unchar.'):>13s}" for c in cols))
        for band in BREADTHS + ("?",):
            line = f"  {band:<10s}"
            for col in cols:
                cell = [r for r in mine if r["breadth"] == band and r["derivation"] == col]
                if not cell:
                    line += f"{'UNKNOWN':>13s}"
                    continue
                good = sum(1 for r in cell if r["outcome"] == "faithful")
                bad = any(r["outcome"] in ("fabricated", "incomplete") for r in cell)
                line += f"{f'{good}/{len(cell)}' + ('!' if bad else ''):>13s}"
            print(line)

    # The cost of stratifying, stated rather than hidden: splitting a small sample makes
    # most cells unknown. That is honest, not a regression — but it means the next ratings
    # should TARGET empty cells instead of landing wherever traffic happens to fall.
    empty = sum(1 for k in keys for b in BREADTHS for c in DERIVATION
                if not [r for r in rated
                        if (r.get("tool"), (r.get("model") or "-").split("/")[-1][:24],
                            r.get("kv_cache_type") or "-") == k
                        and r["breadth"] == b and r["derivation"] == c])
    total_cells = len(keys) * len(BREADTHS) * len(DERIVATION)
    if empty:
        print(f"\n{empty}/{total_cells} difficulty cells are UNKNOWN. "
              f"Rate into an empty cell next — `unrated --breadth wide` finds candidates.")


def cmd_show(args):
    """The audit surface: what was asked, of which files, at which commit, and what came back."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    hit = None
    for row in load_joined():
        if (row.get("ts") or "").startswith(args.ts):
            hit = row
            break
    if hit is None:
        print(f"no ledger row at {args.ts}", file=sys.stderr)
        return 2

    print(f"ts        {hit.get('ts')}")
    print(f"tool      {hit.get('tool')}   model {hit.get('model')}")
    if hit.get("kv_cache_type"):
        # The environment value is declared at server startup and cannot be read back. Report
        # the measured resident footprint beside it so an auditor can spot disagreement.
        gb = hit.get("resident_bytes")
        gb = f"{gb/1e9:.1f} GB" if isinstance(gb, (int, float)) else "?"
        print(f"runtime   kv={hit.get('kv_cache_type')} (declared)   "
              f"resident={gb} @ ctx {hit.get('resident_ctx')} (measured)")
    print(f"flags     truncated={hit.get('truncated')} degenerate={hit.get('degenerate')} "
          f"error={'YES' if hit.get('error') else '-'}")
    cov = hit.get("paths_covered")
    if cov is not None:
        req = hit.get("paths_requested")
        print(f"coverage  {cov}/{req}" + ("   <-- PARTIAL" if cov < req else ""))
    if hit.get("numerics_added"):
        print(f"numerics  {hit['numerics_added']} number(s) in the output absent from spec/references: "
              f"{hit.get('numerics_added_sample')}   <-- check each before trusting the doc")
    # Breadth is printed as computed-and-why, so the auditor can see the coordinate is
    # mechanical and does not try to second-guess it. Derivation is the one they must supply.
    print(f"breadth   {hit.get('breadth')}  (computed: {hit.get('paths_requested') or '?'} "
          f"files, {hit.get('prompt_tokens') or '?'} prompt_tokens)")
    print(f"rating    {hit.get('outcome') or 'UNRATED'}"
          f"   derivation {hit.get('derivation') or 'UNCHARACTERISED'}")

    name = hit.get("artifact")
    if not name:
        # Pre-dates artifact capture, or the write failed. Say which is unknowable rather
        # than printing an empty section that reads as "there was nothing to see".
        print("\nNO ARTIFACT — this call predates capture, so its question, paths and "
              "response are unrecoverable. Nothing to audit.")
        return 0
    path = ARTIFACTS / name
    if not path.exists():
        print(f"\nartifact missing on disk: {path}", file=sys.stderr)
        return 2
    art = json.loads(path.read_text(encoding="utf-8"))
    print(f"\ncwd       {art.get('cwd')}")
    print(f"git_sha   {art.get('git_sha')}   <-- check the sources AT THIS COMMIT")
    print(f"\nQUESTION\n{art.get('question')}")
    print(f"\nPATHS ({len(art.get('paths') or [])})")
    for p in art.get("paths") or []:
        print(f"  {p}")
    resp = art.get("response") or ""
    print(f"\nRESPONSE ({len(resp)} chars)")
    print(resp if args.full else resp[:3000])
    if not args.full and len(resp) > 3000:
        print(f"\n... {len(resp)-3000} more chars — rerun with --full")
    return 0


def cmd_unrated(args):
    rows = [r for r in load_joined(args.since) if not r["outcome"]]
    if args.breadth:
        # Targeting a band is how an empty grid cell gets filled on purpose. Without this
        # the sample follows whatever traffic happened to occur, which is how a whole
        # difficulty band stays unmeasured while coverage climbs.
        rows = [r for r in rows if r["breadth"] == args.breadth]
    if args.with_artifact:
        rows = [r for r in rows if r.get("artifact")]
    if not rows:
        print("nothing unrated matches" if (args.breadth or args.with_artifact)
              else "everything in range is rated")
        return 0
    for r in rows:
        flag_parts = [f for f in ("degenerate", "truncated") if r.get(f)]
        if r.get("numerics_added"):
            flag_parts.append(f"numerics+{r['numerics_added']}")
        flags = ",".join(flag_parts) or "-"
        err = "ERR" if r.get("error") else "-"
        art = "" if r.get("artifact") else "  NO-ARTIFACT"
        print(f"{r.get('ts','?')[:19]}  {str(r.get('tool')):14s} "
              f"{r['breadth']:<7s} files={str(r.get('paths_requested') or '-'):>3s} "
              f"chars={str(r.get('chars')):>6s}  flags={flags:<14s} {err}{art}")
    print(f"\n{len(rows)} unrated")
    return 0


def main():
    # Once, for every subcommand. `show` used to do this alone, so the grid's own dashes
    # mojibaked on a cp1252 console — a report is read by a human and must not garble.
    # stderr too: the refusal messages carry the em-dashes that explain the flags, and a
    # guard whose explanation is garbled is a guard the user works around.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - a stream without reconfigure is not worth failing over
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rate", help="stamp a verdict on a call you verified")
    p.add_argument("outcome", choices=OUTCOMES)
    p.add_argument("--anchor", help="REQUIRED for faithful/incomplete/fabricated: "
                                    "'<claim checked> | <file:line that settles it>'")
    p.add_argument("--derivation", choices=DERIVATION,
                   help="REQUIRED for faithful/incomplete/fabricated: how much work the "
                        "answer needed beyond copying. "
                        + "; ".join(f"{k}={v}" for k, v in DERIVATION_HELP.items()))
    p.add_argument("--note", help="what was wrong/right — the part worth recalling later")
    p.add_argument("--ts", help="ISO timestamp prefix; default is the most recent call")
    p.add_argument("--tool", help="restrict the default pick to one tool")
    p.set_defaults(fn=cmd_rate)

    p = sub.add_parser("show", help="print one call's artifact — paths, question, response")
    p.add_argument("ts", help="ISO timestamp prefix")
    p.add_argument("--full", action="store_true", help="whole response, not the head")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("report", help="mechanical flags and rated outcomes, by tool+model")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("unrated", help="calls still awaiting a verdict")
    p.add_argument("--since", help="YYYY-MM-DD")
    p.add_argument("--breadth", choices=BREADTHS + ("?",),
                   help="only calls in one breadth band — use it to fill an empty grid cell")
    p.add_argument("--with-artifact", action="store_true",
                   help="skip calls that predate artifact capture and cannot be audited")
    p.set_defaults(fn=cmd_unrated)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
