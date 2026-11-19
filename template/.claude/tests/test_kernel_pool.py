"""Re-runnable proof for `.claude/tools/kernel_pool.py`.

Each case imports the module, replaces `pool_tags` with a stub table and runs the CLI in process. No case queries the
real kernel pool or launches a process.

    python3 .claude/tests/test_kernel_pool.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.normpath(os.path.join(HERE, "..", "tools", "kernel_pool.py"))
MB = 1 << 20


def _module():
    spec = importlib.util.spec_from_file_location("kernel_pool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = mod.main(list(argv))
    return rc, [line for line in out.getvalue().splitlines() if line.strip()]


def case_unreadable_pool_reports_unknown_and_exits_1():
    """No pool table means one UNKNOWN line and a failure exit, never a listing."""
    mod = _module()
    mod.pool_tags = lambda: None
    rc, lines = _run(mod, [])
    assert rc == 1, "exit code %r" % rc
    assert len(lines) == 1, "lines: %r" % lines
    assert lines[0].startswith("UNKNOWN"), "first line %r" % lines[0]


def case_listing_is_largest_total_first():
    """Tags sort by paged + nonpaged bytes, largest first."""
    mod = _module()
    mod.pool_tags = lambda: {
        "AAAA": (1 * MB, 10, 1 * MB, 10),    # 2 MB
        "BBBB": (10 * MB, 20, 2 * MB, 20),   # 12 MB
        "CCCC": (3 * MB, 30, 3 * MB, 30),    # 6 MB
        "DDDD": (0, 40, 1 * MB, 40),         # 1 MB
    }
    rc, lines = _run(mod, [])
    assert rc == 0, "exit code %r" % rc
    order = [line.split()[0].strip("'") for line in lines]
    assert order == ["BBBB", "CCCC", "AAAA", "DDDD"], "order: %r" % order


def case_top_limits_the_row_count():
    """`--top N` shows N rows, keeping the largest-first order."""
    mod = _module()
    mod.pool_tags = lambda: {
        "AAAA": (1 * MB, 10, 1 * MB, 10),
        "BBBB": (10 * MB, 20, 2 * MB, 20),
        "CCCC": (3 * MB, 30, 3 * MB, 30),
    }
    rc, lines = _run(mod, ["--top", "2"])
    assert rc == 0, "exit code %r" % rc
    order = [line.split()[0].strip("'") for line in lines]
    assert order == ["BBBB", "CCCC"], "rows: %r" % order


def case_launch_test_reports_live_change_per_launch():
    """Each watched tag reports (live-after - live-before) / launches, from live counts only.

    Byte totals are held constant so a formula that divided allocated bytes would report 0.0 here.
    """
    mod = _module()
    launches = []

    def fake_run(*args, **kwargs):
        launches.append(args)
        return types.SimpleNamespace(returncode=0)

    mod.subprocess = types.SimpleNamespace(run=fake_run, CREATE_NO_WINDOW=0)
    before = {"D2d ": (1000, 100, 2000, 10), "File": (500, 0, 0, 0), "Toke": (0, 0, 0, 0)}
    after = {"D2d ": (1000, 103, 2000, 13), "File": (500, 6, 0, 0), "Toke": (0, 2, 0, -2)}
    tables = [before, after]

    def fake_pool_tags():
        return tables.pop(0) if len(tables) > 1 else after

    mod.pool_tags = fake_pool_tags
    rc, lines = _run(mod, ["--launch-test", "3"])
    assert rc == 0, "exit code %r" % rc
    assert len(launches) == 3, "launches: %r" % len(launches)
    assert len(lines) == len(mod.LAUNCH_TAGS), "lines: %r" % lines
    rates = {}
    for tag in mod.LAUNCH_TAGS:
        line = next(line for line in lines if line.startswith(repr(tag)))
        rates[tag] = float(line.split("per launch")[0].rsplit(None, 1)[-1])
    assert rates == {"D2d ": 2.0, "File": 2.0, "Toke": 0.0}, "rates: %r" % rates


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_") and callable(v)]


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # the proof reports every red, not the first
            failed += 1
            last = (str(exc).strip().splitlines() or ["?"])[-1][:300]
            print("FAIL %s: %s" % (fn.__name__, last))
    print("%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
