import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import workouts
import nutrition
import reports

_spec = importlib.util.spec_from_file_location("workout_mcp_server_ui", os.path.join(os.path.dirname(__file__), "..", "server.py"))
mcp_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_server)


class ReportsUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")
        self.c = dbm.connect(self.path)
        core.update_settings(self.c, {"unit": "lb", "timezone": "America/Los_Angeles", "days_per_week": 3})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _history(self):
        start = datetime(2026, 5, 1, 15, tzinfo=timezone.utc)
        for i in range(24):
            at = start + timedelta(days=i * 7)
            logged = core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "weight": 100 + i, "unit": "lb"}], performed_at=at.isoformat())
            workouts.finish_workout(self.c, at=at + timedelta(minutes=10))
            core.log_activity(self.c, "run", 20 + i, performed_at=(at + timedelta(days=1)).isoformat())
            workouts.finish_workout(self.c, at=at + timedelta(days=1, minutes=10))

    def test_week_month_and_year_have_required_fixed_trends(self):
        self._history()
        weekly = reports.report(self.c, "week", anchor_date="2026-10-12", now=datetime(2026, 10, 12, 20, tzinfo=timezone.utc))
        monthly = reports.report(self.c, "month", anchor_date="2026-10-12", now=datetime(2026, 10, 12, 20, tzinfo=timezone.utc))
        annual = reports.report(self.c, "year", anchor_date="2026-10-12", now=datetime(2026, 10, 12, 20, tzinfo=timezone.utc))
        for payload, count in ((weekly, 6), (monthly, 6), (annual, 12)):
            points = payload["trend_series"]["points"]
            self.assertEqual(len(points), count)
            self.assertTrue(all({"training_days", "strength_volume", "cardio_minutes", "total_energy_deficit_kcal"}.issubset(point) for point in points))
            self.assertEqual(payload["trend_series"]["primary_training_metric"], "training_days")
        self.assertEqual(weekly["trend_series"]["group_by"], "week")
        self.assertEqual(monthly["trend_series"]["group_by"], "month")
        self.assertEqual(len(weekly["trend_series"]["day_points"]), 7)
        self.assertEqual(weekly["trend_series"]["points"][-2]["training_days"], 2)  # Fri lift + Sat run

    def test_daily_report_includes_nutrition_and_completed_only_workouts(self):
        draft = {"consumed_at": "2026-10-12T18:00:00Z", "capture_timezone": "America/Los_Angeles",
                 "calories_kcal": 500, "protein_g": 35, "estimation_method": "model_only", "confidence": "rough"}
        nutrition.save_entry(self.c, draft, confirmed=True)
        complete = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-12T16:00:00Z")
        workouts.finish_workout(self.c, at="2026-10-12T16:10:00Z")
        active = workouts.start_workout(self.c, mode="conversational_plan", at="2026-10-12T20:00:00Z")
        result = reports.report(self.c, "day", anchor_date="2026-10-12", now=datetime(2026, 10, 12, 20, 5, tzinfo=timezone.utc))
        self.assertEqual(result["summary"]["sessions"], 1)
        self.assertEqual(result["active_workout"]["session_id"], active["session_id"])
        self.assertEqual((result["nutrition"]["entries"], result["nutrition"]["calories_kcal"]), (1, 500))
        self.assertEqual(len(result["trend_series"]["points"]), 28)

    def test_daily_report_uses_configured_timezone(self):
        core.update_settings(self.c, {"timezone": "Asia/Tokyo"})
        core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-01T15:30:00Z")
        workouts.finish_workout(self.c, at="2026-10-01T15:35:00Z")
        result = reports.report(self.c, "day", anchor_date="2026-10-02", now=datetime(2026, 10, 1, 16, tzinfo=timezone.utc))
        self.assertEqual(result["summary"]["sessions"], 1)
        self.assertEqual(result["summary"]["days_trained"], 1)

    def test_nutrition_averages_exclude_days_where_nutrient_is_unknown(self):
        base = {"capture_timezone": "America/Los_Angeles", "calories_kcal": 500,
                "estimation_method": "model_only", "confidence": "rough"}
        nutrition.save_entry(self.c, {**base, "consumed_at": "2026-10-01T18:00:00Z", "protein_g": 40}, confirmed=True)
        nutrition.save_entry(self.c, {**base, "consumed_at": "2026-10-02T18:00:00Z", "protein_g": None}, confirmed=True)
        result = reports.report(self.c, "month", anchor_date="2026-10-12")
        self.assertEqual(result["nutrition"]["protein_g_daily_average"], 40)

    def test_ui_is_advertised_only_for_compatible_clients(self):
        no_ui = mcp_server.Server(os.path.join(self.tmp.name, "no-ui.db"))
        self.addCleanup(no_ui.c.close)
        init = no_ui.handle({"method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        self.assertNotIn("resources", init["capabilities"])
        self.assertEqual(no_ui.handle({"method": "resources/list", "params": {}})["resources"], [])
        tools = no_ui.handle({"method": "tools/list", "params": {}})["tools"]
        self.assertTrue(all("_meta" not in tool for tool in tools))
        report = no_ui.handle({"method": "tools/call", "params": {"name": "report", "arguments": {"period": "day", "anchor_date": "2026-10-12"}}})
        self.assertNotIn("_meta", report)
        self.assertNotIn("fallback_url", report["structuredContent"])

        ui = mcp_server.Server(os.path.join(self.tmp.name, "ui.db"))
        self.addCleanup(ui.c.close)
        init = ui.handle({"method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {}}}}})
        self.assertIn("resources", init["capabilities"])
        resources = ui.handle({"method": "resources/list", "params": {}})["resources"]
        self.assertEqual({item["uri"] for item in resources}, {"ui://workout/hiit.html", "ui://workout/progress.html", "ui://workout/report.html"})
        for item in resources:
            html = ui.handle({"method": "resources/read", "params": {"uri": item["uri"]}})["contents"][0]["text"]
            self.assertIn("color-scheme", html)
            self.assertIn("@media", html)
        report_html = ui.handle({"method": "resources/read", "params": {"uri": "ui://workout/report.html"}})["contents"][0]["text"]
        for section in ("goal-progress", "coach-summary", "workout-totals", "nutrition-summary", "confidence", "trend-chart", "insights"):
            self.assertIn(f'id="{section}"', report_html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
