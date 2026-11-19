#!/usr/bin/env python3
"""Will a job fit inside its plan's quota windows? The launcher asks before it starts a job (`sc_check_quota_fit`).

A job a usage limit cuts mid-run is waste: its work stops half done, and a resume replays its context. So a job
starts only when some plan that pays for its model holds its expected drain beside the jobs already running there,
the same test `sidecar_fanout` places and paces fan-outs by (`sidecar_fanout.place_job`: `provider_bands.job_drain`
priced per window from finished jobs, charged only the share of the run before each window's reset). A forecast
never refuses on its own: a caller that can wait waits for the reset, and one that cannot is told and runs.

CLI: quota_fit.py <alias> <effort> [shape] [--plan PLAN] [--exclude TEXT]   (`-` for an effort or shape the launch did not set)
  --plan     judges only that paying plan (a launcher's own `SC_TRANSPORT`); without it every plan of the alias's route counts
  --exclude  leaves out the live launcher processes whose command line contains TEXT (the asking launcher's -R
             record or -l label), so a job never counts against itself
  exit 0   fits, or no reading decides (the launcher's observed-capacity gate still runs)
  exit 10  does not fit now: prints `<seconds until the binding window resets> <reason>`
  exit 4   no plan holds it before a long window resets (beyond one short window): prints the reason
  exit 2   bad arguments or an alias that does not resolve
"""
import sys
import time
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import model_registry  # noqa: E402
import provider_bands  # noqa: E402
import sidecar_fanout  # noqa: E402

# A short window is the five-hour kind; a reset further out than one of them plus slack is a long window's.
SHORT_WINDOW_S = 5 * 3600 + 600


def live_load(data, exclude=None):
    """{plan: [running job signature]} of the live launchers (`sidecar_fanout.live_plan_load`), less the processes
    whose command line contains `exclude`."""
    import reap
    table = reap.process_table() or {}
    if exclude:
        table = {k: row for k, row in table.items() if exclude not in str(row.get("cmd") or "")}
    return sidecar_fanout.live_plan_load(table, data)


def decide(alias, effort, shape=None, data=None, read=None, now=None, load=None, exclude=None, plan=None):
    """(exit code, text) for one job; see the module docstring. `read`, `now` and `load` are seams for proofs;
    `load` None reads `live_load(data, exclude)`. `plan` narrows the candidates to that one plan."""
    effort, shape, plan = (None if v in (None, "", "-") else v for v in (effort, shape, plan))
    data = data if data is not None else model_registry.load()
    read = read or provider_bands.read_capacity
    now = time.time() if now is None else now
    if load is None:
        load = live_load(data, exclude)
    job = {"alias": alias, "effort": effort, "kind": shape, "label": "fit-check"}
    try:
        _route, plans = sidecar_fanout.job_routes(job, data)
    except Exception as exc:
        return 2, "alias %r does not resolve: %s" % (alias, str(exc).split(";")[0])
    if plan:
        plans = [plan]
    signature = sidecar_fanout.job_signature(job, data)
    placed = sidecar_fanout.place_job(plans, data, read, now, load, signature)
    if not placed.held or any(held[0] for held in placed.held.values()):
        return 0, "fits" if placed.held else "no reading decides"
    resets = [drain.get("resetsAt") for _holds, drain, _c in placed.held.values()
              if isinstance(drain.get("resetsAt"), (int, float))]
    reason = "; ".join(placed.unfit.values()) or "no plan holds the job"
    if not resets:
        return 0, "no reset known (%s)" % reason
    wait = max(int(min(resets) - now), 0)
    if wait > SHORT_WINDOW_S:
        return 4, "no plan holds the job before %s: %s" % (provider_bands.reset_text(min(resets), now), reason)
    return 10, "%d %s" % (wait, reason)


def _take(argv, flag):
    """(value or None, argv without `flag VALUE`); a flag with no value leaves its value None."""
    if flag not in argv:
        return None, argv
    at = argv.index(flag)
    value = argv[at + 1] if at + 1 < len(argv) else None
    return value, argv[:at] + argv[at + 2:]


def main(argv):
    exclude, argv = _take(argv, "--exclude")
    plan, argv = _take(argv, "--plan")
    if len(argv) not in (2, 3) or ("--exclude" in sys.argv and not exclude) or ("--plan" in sys.argv and not plan):
        print(__doc__.split("\n\n")[2], file=sys.stderr)
        return 2
    code, text = decide(*argv, exclude=exclude, plan=plan)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
