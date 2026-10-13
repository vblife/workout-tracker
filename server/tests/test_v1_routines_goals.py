import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import workouts
import routines
import goals
import profile


class RoutinesGoalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "workout.db"))
        core.update_settings(self.c, {"unit": "lb", "timezone": "America/Los_Angeles"})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_direct_routine_preserves_order_and_snapshot_is_isolated(self):
        routine = routines.create_routine(self.c, "Full Body", [
            {"exercise": "squat", "target_sets": 3, "target_reps": 8, "target_weight": 100, "unit": "lb"},
            {"exercise": "push ups", "target_sets": 2, "target_reps": 12},
        ], estimated_duration_min=45)
        self.assertEqual([x["position"] for x in routine["items"]], [1, 2])
        active = routines.start_routine(self.c, routine["routine_id"], idempotency_key="start-a", at="2026-10-01T08:00:00Z")
        routines.update_routine(self.c, routine["routine_id"], items=[
            {"exercise": "deadlift", "target_sets": 4, "target_reps": 5}
        ])
        snapshot = self.c.execute("SELECT exercise_id,target_sets FROM workout_session_items WHERE session_id=?", (active["session_id"],)).fetchall()
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(snapshot[0]["target_sets"], 3)

    def test_create_from_completed_workout_and_archive_keeps_source_link(self):
        logged = core.log_sets(self.c, [{"exercise": "bench press", "reps": 8, "weight": 120, "unit": "lb", "count": 3}], performed_at="2026-10-01T08:00:00Z")
        workouts.finish_workout(self.c, at="2026-10-01T08:10:00Z")
        routine = routines.create_routine(self.c, "Push Day", source_session_id=logged["session_id"])
        self.assertEqual((routine["items"][0]["target_sets"], routine["items"][0]["target_reps"]), (3, 8))
        routines.set_archived(self.c, routine["routine_id"], True)
        started = routines.start_routine(self.c, routine["routine_id"], allow_archived=True, at="2026-10-02T08:00:00Z")
        routines.set_archived(self.c, routine["routine_id"], False)
        self.assertEqual(self.c.execute("SELECT source_routine_id FROM sessions WHERE id=?", (started["session_id"],)).fetchone()[0], routine["routine_id"])

    def test_start_routine_does_not_replace_active_workout(self):
        routine = routines.create_routine(self.c, "Simple", [{"exercise": "squat", "target_sets": 1, "target_reps": 5}])
        workouts.start_workout(self.c, mode="conversational_plan", at="2026-10-01T08:00:00Z")
        with self.assertRaises(core.UserError):
            routines.start_routine(self.c, routine["routine_id"], at="2026-10-01T08:01:00Z")

    def test_day_week_month_goal_progress_recalculates_after_delete(self):
        goal = goals.create_goal(self.c, domain="workout", period="week", metric="strength_sets",
                                 semantics="minimum", target_value=2, unit="sets", effective_start_date="2026-09-01")
        logged = core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "count": 2}], performed_at="2026-10-05T07:30:00Z")
        workouts.finish_workout(self.c, at="2026-10-05T07:35:00Z")
        progress = goals.goal_progress(self.c, goal["goal_id"], anchor_date="2026-10-05")
        self.assertEqual((progress["current"], progress["achieved"]), (2, True))
        core.delete_session(self.c, logged["session_id"])
        self.assertEqual(goals.goal_progress(self.c, goal["goal_id"], anchor_date="2026-10-05")["current"], 0)
        for period in ("day", "month"):
            created = goals.create_goal(self.c, domain="workout", period=period, metric="workout_sessions",
                                        semantics="minimum", target_value=1, unit="sessions", effective_start_date="2026-09-01")
            self.assertEqual(goals.goal_progress(self.c, created["goal_id"], anchor_date="2026-10-05")["period"], period)

    def test_user_goal_supersedes_recommendation(self):
        recommended = goals.create_goal(self.c, domain="nutrition", period="day", metric="protein_g",
                                        semantics="minimum", target_value=120, unit="g", origin="recommended",
                                        effective_start_date="2026-10-01")
        direct = goals.create_goal(self.c, domain="nutrition", period="day", metric="protein_g",
                                   semantics="minimum", target_value=140, unit="g", origin="user",
                                   effective_start_date="2026-10-01")
        rows = {x["goal_id"]: x for x in goals.list_goals(self.c, include_inactive=True)["goals"]}
        self.assertFalse(rows[recommended["goal_id"]]["active"])
        self.assertTrue(rows[direct["goal_id"]]["active"])

    def test_goal_can_be_updated_and_progress_returns_recomputed_streaks(self):
        goal = goals.create_goal(self.c, domain="workout", period="day", metric="workout_sessions",
                                 semantics="minimum", target_value=1, unit="sessions", effective_start_date="2026-10-01")
        for day in ("01", "02"):
            core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at=f"2026-10-{day}T16:00:00Z")
            workouts.finish_workout(self.c, at=f"2026-10-{day}T16:05:00Z")
        updated = goals.update_goal(self.c, goal["goal_id"], target_value=1, display_precision=1)
        self.assertEqual(updated["display_precision"], 1)
        progress = goals.goal_progress(self.c, goal["goal_id"], anchor_date="2026-10-02")
        self.assertEqual(progress["current_streak"], 2)
        self.assertEqual(progress["best_streak"], 2)
        self.assertEqual(len(progress["history"]), 2)

    def test_training_days_are_counted_in_configured_timezone(self):
        goal = goals.create_goal(self.c, domain="workout", period="day", metric="training_days",
                                 semantics="exact", target_value=1, unit="days", effective_start_date="2026-10-01")
        core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-05T23:30:00Z")
        workouts.finish_workout(self.c, at="2026-10-05T23:35:00Z")
        core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-06T01:00:00Z")
        workouts.finish_workout(self.c, at="2026-10-06T01:05:00Z")

        progress = goals.goal_progress(self.c, goal["goal_id"], anchor_date="2026-10-05")
        self.assertEqual(progress["current"], 1)
        self.assertTrue(progress["achieved"])

    def test_body_weight_normalizes_and_recalculates_estimates(self):
        entry = profile.add_body_weight(self.c, 220, "lb", measured_at="2026-10-01T07:00:00Z")
        self.assertAlmostEqual(entry["weight_kg"], 99.79, places=2)
        logged = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-01T08:00:00Z")
        self.assertAlmostEqual(logged["calorie_estimate"]["weight_kg"], 99.79, places=2)
        self.assertEqual(len(profile.list_body_weight(self.c)["entries"]), 1)
        profile.delete_body_weight(self.c, entry["weight_id"])
        self.assertEqual(profile.list_body_weight(self.c)["entries"], [])
        self.assertEqual(self.c.execute("SELECT calorie_weight_kg FROM sessions WHERE id=?", (logged["session_id"],)).fetchone()[0], 70)

    def test_body_weight_write_rolls_back_if_recalculation_fails(self):
        core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-01T08:00:00Z")
        with mock.patch("calories.estimate_session", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                profile.add_body_weight(self.c, 180, "lb", measured_at="2026-10-01T07:00:00Z")
        self.assertEqual(self.c.execute("SELECT count(*) FROM body_weight").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
