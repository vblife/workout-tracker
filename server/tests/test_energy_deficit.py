"""Calorie maximum goals and energy-deficit reporting against settings.maintenance_kcal."""
import glob
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import goals
import nutrition
import reports
import workouts

SERVER_DIR = os.path.join(os.path.dirname(__file__), "..")
NOW = datetime(2026, 10, 25, 5, tzinfo=timezone.utc)  # 2026-10-24 evening in Los Angeles


class EnergyDeficitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "workout.db"))
        core.update_settings(self.c, {"timezone": "America/Los_Angeles", "unit": "lb"})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _eat(self, local_date, kcal, hour=12):
        nutrition.save_entry(self.c, {"consumed_at": f"{local_date}T{hour:02d}:00:00", "calories_kcal": kcal, "protein_g": 30,
                                      "estimation_method": "model_only", "confidence": "moderate"}, confirmed=True)

    def _calorie_max(self, kcal, start="2026-10-01"):
        return goals.create_goal(self.c, domain="nutrition", period="day", metric="calories_kcal", semantics="maximum",
                                 target_value=kcal, unit="kcal", effective_start_date=start)

    def test_goal_progress_with_calorie_maximum(self):
        goal = self._calorie_max(1900)
        self._eat("2026-10-20", 1100); self._eat("2026-10-20", 700, hour=19)
        self._eat("2026-10-21", 2050)
        under = goals.goal_progress(self.c, goal["goal_id"], "2026-10-20")
        over = goals.goal_progress(self.c, goal["goal_id"], "2026-10-21")
        self.assertEqual((under["semantics"], under["current"], under["achieved"]), ("maximum", 1800, True))
        self.assertEqual((over["current"], over["achieved"]), (2050, False))

    def test_day_report_energy_deficit_vs_maintenance_and_target(self):
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        self._calorie_max(1900)
        self._eat("2026-10-24", 1200); self._eat("2026-10-24", 600, hour=19)
        # Workout-burned calories must not change the deficit (deficit is vs maintenance only).
        core.log_activity(self.c, "run", 30, performed_at="2026-10-24T08:00:00")
        workouts.finish_workout(self.c, at="2026-10-24T08:30:00")
        out = reports.report(self.c, "day", anchor_date="2026-10-24", now=NOW)
        eb = out["energy_balance"]
        self.assertTrue(eb["available"])
        self.assertEqual(eb["maintenance_kcal"], 2370)
        self.assertEqual(eb["maintenance_source"], "settings")
        self.assertEqual(eb["intake_kcal"], 1800)
        self.assertEqual(eb["energy_deficit_kcal"], 570)
        self.assertEqual(eb["calorie_max_kcal"], 1900)
        self.assertEqual(eb["target_energy_deficit_kcal"], 470)
        self.assertTrue(eb["within_calorie_max"])
        self.assertTrue(eb["met_deficit_target"])
        self.assertEqual(eb["kcal_under_max"], 100)
        self.assertEqual(eb["deficit_vs_target_kcal"], 100)
        self.assertEqual(out["nutrition"]["calories_kcal"], 1800)  # consumed stays separate
        self.assertGreater(out["summary"]["estimated_calories"], 0)  # burned is reported separately
        self.assertNotIn("estimated_calories", eb)

    def test_day_report_over_maximum_misses_target(self):
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        self._calorie_max(1900)
        self._eat("2026-10-24", 2000)
        eb = reports.report(self.c, "day", anchor_date="2026-10-24", now=NOW)["energy_balance"]
        self.assertEqual((eb["energy_deficit_kcal"], eb["target_energy_deficit_kcal"]), (370, 470))
        self.assertFalse(eb["within_calorie_max"])
        self.assertFalse(eb["met_deficit_target"])
        self.assertEqual((eb["kcal_under_max"], eb["deficit_vs_target_kcal"]), (-100, -100))

    def test_week_and_month_average_deficit_and_target_days(self):
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        self._calorie_max(1900)
        for day, kcal in (("2026-10-19", 1800), ("2026-10-20", 2000), ("2026-10-21", 1850)):
            self._eat(day, kcal)
        for period in ("week", "month"):
            eb = reports.report(self.c, period, anchor_date="2026-10-21", now=NOW)["energy_balance"]
            self.assertEqual(eb["days_with_intake"], 3, period)
            self.assertEqual(eb["avg_daily_energy_deficit_kcal"], round((570 + 370 + 520) / 3, 1), period)
            self.assertEqual(eb["target_energy_deficit_kcal"], 470)
            self.assertEqual(eb["days_meeting_deficit_target"], 2, period)
            self.assertEqual(eb["days_within_calorie_max"], 2, period)
            self.assertEqual([d["energy_deficit_kcal"] for d in eb["days"]], [570, 370, 520])

    def test_period_total_energy_deficit(self):
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        self._calorie_max(1900)
        for day, kcal in (("2026-10-19", 1800), ("2026-10-20", 2000), ("2026-10-21", 1850), ("2026-09-30", 2570)):
            self._eat(day, kcal)
        week = reports.report(self.c, "week", anchor_date="2026-10-21", now=NOW)["energy_balance"]
        self.assertEqual(week["total_energy_deficit_kcal"], 570 + 370 + 520)
        month = reports.report(self.c, "month", anchor_date="2026-10-21", now=NOW)["energy_balance"]
        self.assertEqual(month["total_energy_deficit_kcal"], 570 + 370 + 520)
        year = reports.report(self.c, "year", anchor_date="2026-10-21", now=NOW)["energy_balance"]
        self.assertEqual(year["days_with_intake"], 4)
        self.assertEqual(year["total_energy_deficit_kcal"], 570 + 370 + 520 - 200)  # a surplus day subtracts
        day = reports.report(self.c, "day", anchor_date="2026-10-21", now=NOW)["energy_balance"]
        self.assertNotIn("total_energy_deficit_kcal", day)
        self.assertEqual(day["energy_deficit_kcal"], 520)
        core.update_settings(self.c, {"maintenance_kcal": None})
        week = reports.report(self.c, "week", anchor_date="2026-10-21", now=NOW)["energy_balance"]
        self.assertIsNone(week["total_energy_deficit_kcal"])
        self.assertFalse(week["available"])

    def test_trend_series_training_days_and_energy_deficit(self):
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        for day, kcal in (("2026-10-19", 1800), ("2026-10-21", 1850), ("2026-09-15", 2070)):
            self._eat(day, kcal)
        for at in ("2026-10-19T08:00:00", "2026-10-19T18:00:00", "2026-10-22T08:00:00", "2026-09-16T08:00:00"):
            core.log_activity(self.c, "run", 30, performed_at=at)
            workouts.finish_workout(self.c, at=at.replace(":00:00", ":30:00"))
        week = reports.report(self.c, "week", anchor_date="2026-10-21", now=NOW)["trend_series"]
        self.assertEqual(week["primary_training_metric"], "training_days")
        days = week["day_points"]
        self.assertEqual([d["date"] for d in days][0::6], ["2026-10-19", "2026-10-25"])
        self.assertEqual([d["trained"] for d in days], [1, 0, 0, 1, 0, 0, 0])
        self.assertEqual(days[0]["sessions"], 2)
        self.assertEqual([d["energy_deficit_kcal"] for d in days], [570, None, 520, None, None, None, None])
        self.assertEqual(week["group_by"], "week")
        current = week["points"][-1]
        self.assertEqual((current["period"], current["training_days"]), ("2026-10-19", 2))
        self.assertEqual((current["total_energy_deficit_kcal"], current["days_with_intake"]), (1090, 2))
        self.assertEqual(current["avg_daily_energy_deficit_kcal"], 545)
        month = reports.report(self.c, "month", anchor_date="2026-10-21", now=NOW)["trend_series"]
        self.assertEqual(month["primary_training_metric"], "training_days")
        self.assertNotIn("day_points", month)
        by = {p["period"]: p for p in month["points"]}
        self.assertEqual((by["2026-10"]["training_days"], by["2026-10"]["total_energy_deficit_kcal"]), (2, 1090))
        self.assertEqual((by["2026-09"]["training_days"], by["2026-09"]["total_energy_deficit_kcal"]), (1, 300))
        self.assertEqual((by["2026-08"]["training_days"], by["2026-08"]["total_energy_deficit_kcal"]), (0, None))
        year = reports.report(self.c, "year", anchor_date="2026-10-21", now=NOW)["trend_series"]
        self.assertEqual(len(year["points"]), 12)
        self.assertEqual({p["period"]: p["training_days"] for p in year["points"]}["2026-10"], 2)
        daily = reports.report(self.c, "day", anchor_date="2026-10-21", now=NOW)["trend_series"]
        self.assertNotIn("primary_training_metric", daily)
        self.assertTrue(all("training_days" not in p and "total_energy_deficit_kcal" not in p for p in daily["points"]))

    def test_uses_each_users_own_numbers(self):
        core.update_settings(self.c, {"maintenance_kcal": 2200})
        self._calorie_max(1600)
        self._eat("2026-10-24", 1650)
        eb = reports.report(self.c, "day", anchor_date="2026-10-24", now=NOW)["energy_balance"]
        self.assertEqual((eb["target_energy_deficit_kcal"], eb["energy_deficit_kcal"]), (600, 550))
        self.assertFalse(eb["met_deficit_target"])

    def test_missing_maintenance_or_goal_leaves_fields_null(self):
        self._eat("2026-10-24", 1800)
        eb = reports.report(self.c, "day", anchor_date="2026-10-24", now=NOW)["energy_balance"]
        self.assertFalse(eb["available"])
        self.assertIsNone(eb["maintenance_kcal"])
        self.assertIsNone(eb["energy_deficit_kcal"])
        self.assertIsNone(eb["target_energy_deficit_kcal"])
        core.update_settings(self.c, {"maintenance_kcal": 2370})
        # A range calorie goal is not a maximum: deficit is known, target deficit is not.
        goals.create_goal(self.c, domain="nutrition", period="day", metric="calories_kcal", semantics="range",
                          lower_value=1800, upper_value=2000, unit="kcal", effective_start_date="2026-10-01")
        eb = reports.report(self.c, "day", anchor_date="2026-10-24", now=NOW)["energy_balance"]
        self.assertEqual(eb["energy_deficit_kcal"], 570)
        self.assertIsNone(eb["target_energy_deficit_kcal"])
        self.assertIsNone(eb["within_calorie_max"])
        week = reports.report(self.c, "week", anchor_date="2026-10-24", now=NOW)["energy_balance"]
        self.assertIsNone(week["days_meeting_deficit_target"])

    def test_no_hard_coded_calorie_goal_in_product_logic(self):
        for path in glob.glob(os.path.join(SERVER_DIR, "*.py")):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIsNone(re.search(r"\b1900\b|\b2370\b", text), os.path.basename(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
