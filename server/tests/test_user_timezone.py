"""User-local timezone: no silent Pacific/UTC fallback, timestamps without a zone are user-local."""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import coach
import goals
import nutrition
import profile
import reports
import workouts

SERVER = os.path.join(os.path.dirname(__file__), "..")
DRAFT = {"calories_kcal": 500, "protein_g": 30, "estimation_method": "model_only", "confidence": "rough"}


class UserTimezoneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "workout.db"))

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_new_york_naive_log_is_user_local_and_counts_on_that_day(self):
        core.update_settings(self.c, {"timezone": "America/New_York"})
        result = core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "weight": 100}], performed_at="2026-10-12T22:30:00")
        self.assertTrue(result["performed_at_local"].startswith("2026-10-12T22:30:00-04:00"))
        stored = self.c.execute("SELECT performed_at FROM sets").fetchone()[0]
        self.assertEqual(stored, "2026-10-13T02:30:00Z")
        workouts.finish_workout(self.c, at="2026-10-13T02:35:00Z")
        now = datetime(2026, 10, 13, 3, tzinfo=timezone.utc)
        self.assertEqual(reports.report(self.c, "day", anchor_date="2026-10-12", now=now)["summary"]["sessions"], 1)
        self.assertEqual(reports.report(self.c, "day", anchor_date="2026-10-13", now=now)["summary"]["sessions"], 0)
        stats = core.query_stats(self.c, "volume", group_by="day")
        self.assertEqual([p["period"] for p in stats["series"]], ["2026-10-12"])
        # Today defaults to the user's date (still Oct 12 in New York at 03:00 UTC).
        self.assertEqual(reports.report(self.c, "day", now=now)["start"], "2026-10-12")

    def test_chicago_naive_nutrition_and_body_weight_are_user_local(self):
        core.update_settings(self.c, {"timezone": "America/Chicago"})
        entry = nutrition.save_entry(self.c, {**DRAFT, "consumed_at": "2026-10-12T21:00:00"}, confirmed=True)
        self.assertEqual(entry["capture_timezone"], "America/Chicago")
        self.assertEqual(entry["consumed_at"], "2026-10-13T02:00:00Z")
        self.assertEqual(entry["local_date"], "2026-10-12")
        weight = profile.add_body_weight(self.c, 180, "lb", measured_at="2026-10-12T07:00:00")
        self.assertEqual(weight["measured_at"], "2026-10-12T12:00:00Z")
        plan = coach.daily_plan_context(self.c, now=datetime(2026, 10, 13, 4, tzinfo=timezone.utc))
        self.assertEqual(plan["date"], "2026-10-12")
        self.assertFalse(plan["timezone_missing"])
        self.assertEqual(plan["timezone"], "America/Chicago")

    def test_nutrition_capture_timezone_falls_back_to_user_timezone_not_utc(self):
        core.update_settings(self.c, {"timezone": "America/New_York"})
        entry = nutrition.save_entry(self.c, {**DRAFT, "consumed_at": "2026-10-13T02:30:00Z"}, confirmed=True)
        self.assertEqual(entry["capture_timezone"], "America/New_York")
        self.assertEqual(entry["local_date"], "2026-10-12")  # UTC would say 2026-10-13
        self.assertEqual(nutrition.daily_totals(self.c, "2026-10-12")["entries"], 1)
        # Reports and nutrition goals use the same user-local day key as daily totals.
        now = datetime(2026, 10, 13, 3, tzinfo=timezone.utc)
        self.assertEqual(reports.report(self.c, "day", anchor_date="2026-10-12", now=now)["nutrition"]["calories_kcal"], 500)
        self.assertIsNone(reports.report(self.c, "day", anchor_date="2026-10-13", now=now)["nutrition"]["calories_kcal"])
        goal = goals.create_goal(self.c, domain="nutrition", period="day", metric="calories_kcal", semantics="maximum",
                                 target_value=2000, unit="kcal", effective_start_date="2026-10-12")
        self.assertEqual(goals.goal_progress(self.c, goal["goal_id"], "2026-10-12")["current"], 500)
        self.assertEqual(goals.goal_progress(self.c, goal["goal_id"], "2026-10-13")["current"], 0)
        # validate_nutrition also defaults to the user's zone.
        self.assertEqual(nutrition.validate_draft(DRAFT, core.user_timezone_name(self.c))["capture_timezone"], "America/New_York")
        with self.assertRaises(core.UserError):
            nutrition.validate_draft(DRAFT)  # no capture zone and no user zone: never guess UTC

    def test_unset_timezone_flags_missing_without_blocking_logging(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Kolkata"}):
            ctx = core.get_context(self.c)
            self.assertTrue(ctx["timezone_missing"])
            self.assertEqual((ctx["timezone"], ctx["timezone_source"]), ("Asia/Kolkata", "system"))
            logged = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-12T23:00:00")
            self.assertEqual(logged["status"], "logged")
            self.assertEqual(self.c.execute("SELECT performed_at FROM sets").fetchone()[0], "2026-10-12T17:30:00Z")
            entry = nutrition.save_entry(self.c, {**DRAFT, "consumed_at": "2026-10-12T20:00:00Z"}, confirmed=True)
            self.assertEqual((entry["capture_timezone"], entry["local_date"]), ("Asia/Kolkata", "2026-10-13"))
            report = reports.report(self.c, "day", anchor_date="2026-10-12", now=datetime(2026, 10, 12, 20, tzinfo=timezone.utc))
            self.assertTrue(report["timezone_missing"])
            self.assertEqual(report["summary"]["sets"], 1)
            self.assertTrue(coach.daily_plan_context(self.c)["timezone_missing"])
            core.update_settings(self.c, {"timezone": "America/Chicago"})
            ctx = core.get_context(self.c)
            self.assertFalse(ctx["timezone_missing"])
            self.assertEqual((ctx["timezone"], ctx["timezone_source"]), ("America/Chicago", "settings"))

    def test_unset_timezone_never_assumes_pacific(self):
        with mock.patch.dict(os.environ, {"TZ": "America/New_York"}):
            self.assertEqual(core.user_timezone_name(self.c), "America/New_York")
        for name in ("core.py", "goals.py", "nutrition.py", "reports.py", "coach.py", "profile.py"):
            with open(os.path.join(SERVER, name)) as f:
                source = f.read()
            self.assertNotIn("America/Los_Angeles", source, name)
            self.assertNotIn('or "UTC"', source, name)


    def test_routines_are_plain_local_cron_and_readme_times_match(self):
        root = os.path.join(SERVER, "..")
        with open(os.path.join(root, "bot", "routines.yaml")) as f:
            routines = f.read()
        with open(os.path.join(root, "README.md")) as f:
            readme = f.read()
        schedules = re.findall(r'(?m)^    schedule: "(.+)"$', routines)
        self.assertEqual(schedules, ["7 7 * * *", "3 17 * * {training_days}", "30 20 * * *", "0 21 * * 0"])
        self.assertTrue(all(len(x.split()) == 5 for x in schedules))
        self.assertNotIn("CRON_TZ=", "\n".join(schedules))
        for text in ("07:07 user-local", "17:03 user-local", "20:30 user-local", "Sunday at 21:00 user-local"):
            self.assertIn(text, readme)


if __name__ == "__main__":
    unittest.main()
