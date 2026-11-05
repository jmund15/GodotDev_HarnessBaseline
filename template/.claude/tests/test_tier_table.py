#!/usr/bin/env python3
"""Proof for `provider_bands.tier_table` and `provider_bands.render_tier_table`: one plan-filtered tier table.

    python3 .claude/tests/test_tier_table.py

Every case plants the registry (a dict, never the live one), the capacity readings (a reader
function, or a planted cache file under this test's temp root) and the draw cells, so no case
reads this machine's quota, ledger or cache. The subject is the owner's pick order: a plan the
tool dropped is never shown, a row prints once on a plan that survived, and the output carries no
dispatch mechanics (no launcher line, no transport label, no pin spelling).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)

import model_registry as mr  # noqa: E402
import provider_bands as pb  # noqa: E402
import provider_capacity as capacity  # noqa: E402

NOW = 1_800_000_000.0
WEEK_MIN = 7 * 24 * 60


def window(name="seven_day", used=40, left_days=3.0, status_band="Hot"):
    return {"name": name, "usedPercent": used, "resetsAt": NOW + left_days * 86400,
            "windowDurationMins": WEEK_MIN, "pressure": 1.0, "band": status_band}


def reading(transport, status="available", used=40, left_days=3.0, cost="plan-quota", **extra):
    win = window(used=99 if status == "dispatch-floor" else used, left_days=left_days)
    if status == "dispatch-floor":
        win["dispatchFloorPercent"] = 99
    value = {"schemaVersion": 1, "transport": transport, "costModel": cost,
             "sourceKind": "live", "observedAt": "2027-01-15T08:00:00Z", "status": status,
             "error": None, "liveFailure": None,
             "quota": {"windows": [win],
                       "routingWindow": "seven_day", "bindingWindow": "seven_day",
                       "routingBand": "Hot", "dispatchBand": "Hot"}}
    value.update(extra)
    return value


PLAN_LIMITS = {"contextTokens": 1000000, "maxOutputTokens": 128000}


def registry():
    """anthropic (with its sibling plan anthropic-console), go, and a pay-per-token route."""
    transports = {
        "anthropic": {"costModel": "plan-quota", "launcher": "a.sh", "capacityProbe": {"timeoutSeconds": 1}},
        "anthropic-console": {"costModel": "plan-quota", "planOf": "anthropic", "launcher": "c.sh"},
        "go": {"costModel": "plan-quota", "launcher": "g.sh", "capacityProbe": {"timeoutSeconds": 1}},
        "ds": {"costModel": "marginal-usd", "launcher": "d.sh"},
    }
    models = [
        {"transport": "anthropic", "id": "big-x", "alias": "big", "roles": ["architect", "executor"],
         "limits": PLAN_LIMITS},
        {"transport": "anthropic", "id": "small-x", "alias": "small", "roles": ["fanout"],
         "limits": PLAN_LIMITS},
        {"transport": "go", "id": "go-exec", "alias": "go-exec", "roles": ["executor"], "limits": PLAN_LIMITS},
        {"transport": "ds", "id": "pay-exec", "alias": "pay-exec", "roles": ["executor"], "limits": PLAN_LIMITS},
    ]
    return {"transports": transports, "models": models}


def reader(readings):
    calls = []

    def read(transport, _data=None):
        calls.append(transport)
        value = readings.get(transport)
        if isinstance(value, Exception):
            raise value
        return value
    read.calls = calls
    return read


def draw(_plan, _model, _effort):
    return {}


class PlanFilterTests(unittest.TestCase):
    def table(self, readings, seat="anthropic-console", tiers=("executor",), **kw):
        read = reader(readings)
        got = pb.tier_table(list(tiers), seat, data=registry(), read=read, now=NOW, draw=draw, **kw)
        return got, pb.render_tier_table(got), read

    def test_an_exhausted_plan_never_appears_and_its_route_is_omitted(self):
        got, text, _ = self.table({"anthropic": reading("anthropic"),
                                   "anthropic-console": reading("anthropic-console"),
                                   "go": reading("go", status="exhausted"),
                                   "ds": None})
        self.assertNotIn("route go", text)
        self.assertNotIn("go-exec", text)
        self.assertIn("anthropic-console", text)
        self.assertIn("big", text)

    def test_a_dispatch_floor_plan_and_a_non_available_plan_never_appear(self):
        got, text, _ = self.table({
            "anthropic": reading("anthropic", status="dispatch-floor"),
            "anthropic-console": reading("anthropic-console", status="auth-error"),
            "go": reading("go"), "ds": None})
        self.assertNotIn("route anthropic", text)
        self.assertNotIn("route anthropic-console", text)
        self.assertNotIn("big", text)
        self.assertIn("go-exec", text)

    def test_no_surviving_plan_prints_the_pay_per_token_rows(self):
        got, text, _ = self.table({"anthropic": reading("anthropic", status="exhausted"),
                                   "anthropic-console": reading("anthropic-console", status="exhausted"),
                                   "go": reading("go", status="exhausted"), "ds": reading("ds", cost="marginal-usd")})
        self.assertTrue(got["plansLow"])
        self.assertIn("pay-exec", text)
        self.assertIn("pay per token", text)

    def test_pay_rows_stay_out_unless_named_when_a_plan_survives(self):
        readings = {"anthropic": reading("anthropic"), "anthropic-console": reading("anthropic-console"),
                    "go": reading("go"), "ds": reading("ds", cost="marginal-usd")}
        _got, text, _ = self.table(readings)
        self.assertNotIn("pay-exec", text)
        got, paid, _ = self.table(readings, pay=True)
        self.assertFalse(got["plansLow"])
        self.assertIn("pay-exec", paid)

    def test_a_cache_only_none_lands_in_warming_and_prints_no_row(self):
        got, text, _ = self.table({
            "anthropic": reading("anthropic"),
            "anthropic-console": None,
            "go": reading("go"), "ds": None})
        self.assertIn("anthropic-console", got["warming"])
        self.assertIn("anthropic-console warming", text)
        self.assertNotIn("  plan anthropic-console", text)

    def test_groups_print_in_registry_order(self):
        got, text, _ = self.table({"anthropic": reading("anthropic"),
                                   "anthropic-console": reading("anthropic-console"),
                                   "go": reading("go"), "ds": None},
                                  tiers=("executor", "fanout"))
        routes = [group["route"] for group in got["groups"]]
        self.assertEqual(routes, ["anthropic", "go"])
        self.assertLess(text.index("route anthropic"), text.index("route go"))


class RowTests(unittest.TestCase):
    def table(self, readings, seat="anthropic-console", tiers=("executor",), **kw):
        got = pb.tier_table(list(tiers), seat, data=registry(), read=reader(readings), now=NOW,
                            draw=draw, **kw)
        return got, pb.render_tier_table(got)

    def test_each_row_and_each_plan_print_once(self):
        _got, text = self.table({"anthropic": reading("anthropic"),
                                 "anthropic-console": reading("anthropic-console"),
                                 "go": reading("go"), "ds": None}, tiers=("executor", "fanout"))
        self.assertEqual(text.count("  plan anthropic:"), 1)
        self.assertEqual(text.count("  plan anthropic-console"), 1)
        for alias in ("big", "small"):
            self.assertEqual(len([line for line in text.splitlines()
                                  if line.strip().startswith(alias + " ")]), 1, text)

    def test_a_row_line_leaves_the_ladder_cells_to_the_ladder_table_above_it(self):
        got, text = self.table({"anthropic": reading("anthropic"),
                                "anthropic-console": reading("anthropic-console"),
                                "go": reading("go"), "ds": None}, tiers=("executor", "fanout"))
        rows = [row for group in got["groups"] for row in group["rows"]]
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("ladder", row)
            line = [line for line in text.splitlines() if line.strip().startswith(row["alias"] + " ")][0]
            for cell in ("| cost ", "| speed ", "| intel ", "| ± ", "| effort "):
                self.assertNotIn(cell, line)

    def test_a_dropped_plan_carries_no_draw_cell(self):
        got, text = self.table({"anthropic": reading("anthropic", status="exhausted"),
                                "anthropic-console": reading("anthropic-console"),
                                "go": reading("go"), "ds": None}, tiers=("executor",))
        for row in [row for group in got["groups"] for row in group["rows"]]:
            self.assertNotIn("anthropic", row["draw"])
        self.assertFalse([line for line in text.splitlines()
                          if "plan anthropic:" in line or "draw anthropic " in line], text)

    def test_the_output_carries_no_dispatch_mechanics(self):
        _got, text = self.table({"anthropic": reading("anthropic"),
                                 "anthropic-console": reading("anthropic-console"),
                                 "go": reading("go"), "ds": None}, tiers=("executor",))
        for banned in ("bash ", "IN-TRANSPORT", "CROSS-TRANSPORT", "host pin:", "provider pin:"):
            self.assertNotIn(banned, text)

    def test_the_session_plan_is_marked_and_its_rows_sit_under_its_route(self):
        _got, text = self.table({"anthropic": reading("anthropic"),
                                 "anthropic-console": reading("anthropic-console", used=10),
                                 "go": reading("go"), "ds": None})
        self.assertIn("route anthropic", text)
        self.assertIn("plan anthropic-console *", text)
        anthropic_part = text.split("route anthropic", 1)[1].split("route go", 1)[0]
        self.assertIn("big", anthropic_part)

    def test_multi_tier_standing_rides_one_row(self):
        got, text = self.table({"anthropic": reading("anthropic"),
                                "anthropic-console": reading("anthropic-console"),
                                "go": reading("go"), "ds": None}, tiers=("executor", "architect"))
        opus = [row for group in got["groups"] for row in group["rows"] if row["alias"] == "big"][0]
        self.assertEqual(opus["standing"], {"architect": "qualified", "executor": "qualified"})
        self.assertEqual(len([line for line in text.splitlines() if line.strip().startswith("big ")]), 1)
        self.assertIn("architect:qualified", text)
        self.assertIn("executor:qualified", text)

    def test_a_supplementary_only_row_is_listed_with_supplementary_standing(self):
        data = registry()
        next(m for m in data["models"] if m["alias"] == "small")["supplementary"] = ["architect"]
        got = pb.tier_table(["architect"], "anthropic-console", data=data,
                            read=reader({"anthropic": reading("anthropic"),
                                         "anthropic-console": reading("anthropic-console"),
                                         "go": reading("go"), "ds": None}), now=NOW, draw=draw)
        text = pb.render_tier_table(got)
        small = [row for group in got["groups"] for row in group["rows"] if row["alias"] == "small"]
        self.assertEqual([row["standing"] for row in small], [{"architect": "supplementary"}], text)
        self.assertIn("architect:supplementary", text)

    def test_rows_outside_the_requested_tier_are_absent(self):
        got, _text = self.table({"anthropic": reading("anthropic"),
                                 "anthropic-console": reading("anthropic-console"),
                                 "go": reading("go"), "ds": None}, tiers=("executor",))
        aliases = [row["alias"] for group in got["groups"] for row in group["rows"]]
        self.assertNotIn("small", aliases)


class FreshReadTests(unittest.TestCase):
    """The table reads each plan the way the fan-out's pick does: cache first, one live read past the TTL."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "capacity.json"
        self.data = registry()

    def write(self, entry):
        self.cache.write_text(json.dumps({"anthropic": entry}), encoding="utf-8")

    def planted_read(self, runner):
        calls = []

        def read(transport, data=None):
            if transport != "anthropic":
                return None
            got = capacity.cached(transport, data=data or self.data, now=NOW, cache_path=str(self.cache))
            if got is None:
                calls.append(transport)
                got = capacity.reading(transport, data=data or self.data, now=NOW,
                                       cache_path=str(self.cache), runner=runner)
            return got
        read.calls = calls
        return read

    def test_a_cache_entry_inside_the_ttl_starts_no_live_read(self):
        entry = reading("anthropic")
        entry["observedAt"] = capacity._utc(NOW - 30)
        self.write(entry)
        read = self.planted_read(lambda *_a: self.fail("live read on a fresh cache entry"))
        got = pb.tier_table(["executor"], "anthropic", data=self.data, read=read, now=NOW, draw=draw)
        self.assertEqual(read.calls, [])
        self.assertEqual(got["groups"][0]["route"], "anthropic")

    def test_an_entry_past_the_ttl_triggers_exactly_one_live_read(self):
        entry = reading("anthropic")
        entry["observedAt"] = capacity._utc(NOW - capacity.CACHE_TTL_SECONDS - 100)
        self.write(entry)
        fresh = reading("anthropic", used=55)
        fresh["observedAt"] = capacity._utc(NOW - 1)
        read = self.planted_read(lambda _spec, _transport: fresh)
        got = pb.tier_table(["executor"], "anthropic", data=self.data, read=read, now=NOW, draw=draw)
        self.assertEqual(read.calls, ["anthropic"])
        self.assertEqual(got["groups"][0]["plans"][0]["left"], 45.0)

    def test_a_failed_live_read_is_listed_with_its_cached_reading_age(self):
        entry = reading("anthropic", used=30)
        entry["observedAt"] = capacity._utc(NOW - 700)
        self.write(entry)

        def runner(_spec, transport):
            return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota",
                    "sourceKind": "live", "observedAt": capacity._utc(NOW),
                    "status": "network-error", "error": {"kind": "network", "message": "planted timeout"},
                    "liveFailure": None, "quota": None, "balance": None}

        read = self.planted_read(runner)
        got = pb.tier_table(["executor"], "anthropic", data=self.data, read=read, now=NOW, draw=draw)
        plans = got["groups"][0]["plans"]
        self.assertEqual(plans[0]["plan"], "anthropic")
        self.assertEqual(got["ages"]["anthropic"], 700)
        self.assertIn("700 s old", pb.render_tier_table(got))


class JsonTests(unittest.TestCase):
    def test_the_result_carries_plans_low_and_every_fact_the_text_prints(self):
        got = pb.tier_table(["executor"], "go", data=registry(),
                            read=reader({"anthropic": reading("anthropic"),
                                         "anthropic-console": reading("anthropic-console"),
                                         "go": reading("go"), "ds": None}), now=NOW, draw=draw)
        json.dumps(got)
        self.assertEqual(got["tiers"], ["executor"])
        self.assertIn("plansLow", got)
        self.assertEqual(got["seat"], "go")
        self.assertEqual(set(got["groups"][0]["plans"][0]),
                         {"plan", "left", "timeLeft", "unusedAtReset", "launchers"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
