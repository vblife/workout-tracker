import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone

sys_path = os.path.join(os.path.dirname(__file__), "..")
import sys
sys.path.insert(0, sys_path)

import db as dbm
import core
import workouts
import calories
import routines


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class WorkoutLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "workout.db"))

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def log(self, at):
        return core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "weight": 100, "unit": "lb"}], performed_at=at)

    def test_first_log_starts_implicit_workout(self):
        result = self.log("2026-10-01T08:00:00Z")
        row = self.c.execute("SELECT * FROM sessions WHERE id=?", (result["session_id"],)).fetchone()
        self.assertEqual((row["status"], row["start_mode"]), ("in_progress", "implicit"))
        self.assertEqual(row["last_activity_at"], "2026-10-01T08:00:00Z")

    def test_exactly_fifteen_minutes_joins_but_more_closes(self):
        first = self.log("2026-10-01T08:00:00Z")
        boundary = self.log("2026-10-01T08:15:00Z")
        later = self.log("2026-10-01T08:30:00.001Z")
        self.assertEqual(first["session_id"], boundary["session_id"])
        self.assertNotEqual(first["session_id"], later["session_id"])
        old = self.c.execute("SELECT status,ended_at FROM sessions WHERE id=?", (first["session_id"],)).fetchone()
        self.assertEqual((old["status"], old["ended_at"]), ("completed", "2026-10-01T08:15:00Z"))

    def test_stale_empty_explicit_workout_is_abandoned(self):
        started = workouts.start_workout(self.c, mode="conversational_plan", title="Plan", at=utc("2026-10-01T08:00:00Z"))
        result = workouts.reconcile_active(self.c, at=utc("2026-10-01T08:15:00.001Z"))
        self.assertEqual(result["closed"]["status"], "abandoned")
        row = self.c.execute("SELECT ended_at FROM sessions WHERE id=?", (started["session_id"],)).fetchone()
        self.assertEqual(row[0], "2026-10-01T08:15:00Z")

    def test_start_is_idempotent_and_does_not_replace_active(self):
        first = workouts.start_workout(self.c, mode="routine", title="A", idempotency_key="request-1", at=utc("2026-10-01T08:00:00Z"))
        retry = workouts.start_workout(self.c, mode="routine", title="A", idempotency_key="request-1", at=utc("2026-10-01T08:01:00Z"))
        self.assertEqual(first["session_id"], retry["session_id"])
        with self.assertRaises(core.UserError):
            workouts.start_workout(self.c, mode="routine", title="B", at=utc("2026-10-01T08:02:00Z"))

    def test_explicit_finish_and_abandon(self):
        self.log("2026-10-01T08:00:00Z")
        self.assertEqual(workouts.finish_workout(self.c, at=utc("2026-10-01T08:05:00Z"))["status"], "completed")
        workouts.start_workout(self.c, mode="conversational_plan", at=utc("2026-10-01T09:00:00Z"))
        self.assertEqual(workouts.abandon_workout(self.c, at=utc("2026-10-01T09:02:00Z"))["status"], "abandoned")

    def test_activity_uses_active_workout_and_estimates_calories(self):
        result = core.log_activity(self.c, "run", 30, performed_at="2026-10-01T08:00:00Z", rpe=7)
        row = self.c.execute("SELECT * FROM sessions WHERE id=?", (result["session_id"],)).fetchone()
        self.assertEqual(row["status"], "in_progress")
        self.assertGreater(row["estimated_calories"], 0)
        self.assertEqual(row["calorie_weight_kg"], 70)
        self.assertEqual(row["calorie_confidence"], "moderate")

    def test_missing_weight_and_duration_still_produce_rough_estimate(self):
        result = self.log("2026-10-01T08:00:00Z")
        estimate = calories.estimate_session(self.c, result["session_id"])
        self.assertGreater(estimate["estimated_calories"], 0)
        self.assertEqual(estimate["confidence"], "rough")
        self.assertIn("70 kg fallback", " ".join(estimate["assumptions"]))
        self.assertIn("set-count duration heuristic", " ".join(estimate["assumptions"]))

    def test_hiit_completion_is_completed_and_estimated(self):
        hiit = core.create_hiit(self.c, rounds=2, segments=[
            {"kind": "work", "seconds": 40, "exercise": "Burpee"},
            {"kind": "rest", "seconds": 20},
        ], warmup_s=10, cooldown_s=60)
        result = core.log_hiit(self.c, hiit["hiit_id"], 2, 170)
        row = self.c.execute("SELECT status,start_mode,estimated_calories FROM sessions WHERE id=?", (result["session_id"],)).fetchone()
        self.assertEqual((row["status"], row["start_mode"]), ("completed", "implicit"))
        self.assertGreater(row["estimated_calories"], 0)

    def test_hiit_completion_rolls_back_as_one_write(self):
        hiit = core.create_hiit(self.c, rounds=2, segments=[{"kind": "work", "seconds": 40}], warmup_s=0, cooldown_s=0)
        with mock.patch("calories.estimate_session", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                core.log_hiit(self.c, hiit["hiit_id"], 2, 80)
        self.assertEqual(self.c.execute("SELECT status FROM hiit_workouts WHERE id=?", (hiit["hiit_id"],)).fetchone()[0], "planned")
        self.assertEqual(self.c.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)

    def test_session_weight_and_planned_duration_precede_fallbacks(self):
        started = workouts.start_workout(self.c, mode="conversational_plan", session_weight=200,
                                         weight_unit="lb", planned_duration_min=45,
                                         at=utc("2026-10-01T08:00:00Z"))
        estimate = calories.estimate_session(self.c, started["session_id"])
        self.assertAlmostEqual(estimate["weight_kg"], 90.72, places=2)
        self.assertEqual(estimate["duration_min"], 45)
        self.assertNotIn("fallback body weight", " ".join(estimate["assumptions"]))

    def test_routine_estimated_duration_is_snapshotted_for_calories(self):
        routine = routines.create_routine(self.c, "Long Day", [{"exercise": "squat", "target_sets": 3, "target_reps": 5}],
                                          estimated_duration_min=60)
        started = routines.start_routine(self.c, routine["routine_id"], at="2026-10-01T08:00:00Z")
        estimate = calories.estimate_session(self.c, started["session_id"])
        self.assertEqual(estimate["duration_min"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
