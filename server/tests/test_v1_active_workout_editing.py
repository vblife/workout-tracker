import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import db as dbm
import routines
import workouts


class ActiveWorkoutEditingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "workout.db"))
        routine = routines.create_routine(self.c, "Day A", [
            {"exercise": "squat", "target_sets": 3, "target_reps": 5},
            {"exercise": "bench press", "target_sets": 3, "target_reps": 8},
        ])
        self.active = routines.start_routine(self.c, routine["routine_id"], at="2026-10-01T08:00:00Z")

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_add_replace_reorder_and_status_only_mutate_snapshot(self):
        items = self.active["items"]
        added = workouts.add_session_item(self.c, {"exercise": "deadlift", "target_sets": 2, "target_reps": 5})
        replacement = workouts.replace_session_item(self.c, items[0]["id"], {"exercise": "front squat", "target_sets": 2, "target_reps": 6})
        workouts.reorder_session_items(self.c, [replacement["item"]["id"], items[1]["id"], added["item"]["id"]])
        workouts.set_session_item_status(self.c, items[1]["id"], "skipped")

        current = workouts.get_active(self.c, at="2026-10-01T08:01:00Z")["active_workout"]
        visible = [item for item in current["items"] if item["status"] != "replaced"]
        self.assertEqual([item["position"] for item in visible], [1, 2, 3])
        self.assertEqual([item["canonical_name"] for item in visible], ["Front Squat", "Barbell Bench Press", "Deadlift"])
        self.assertEqual(visible[1]["status"], "skipped")
        self.assertEqual(current["next_unfinished_item"]["canonical_name"], "Front Squat")

    def test_logging_can_link_and_complete_snapshot_item(self):
        item_id = self.active["items"][0]["id"]
        result = core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "session_item_id": item_id}], performed_at="2026-10-01T08:05:00Z")
        self.assertEqual(result["logged"][0]["session_item_id"], item_id)
        current = workouts.get_active(self.c, at="2026-10-01T08:06:00Z")["active_workout"]
        self.assertEqual(current["items"][0]["status"], "completed")
        self.assertEqual(current["completed_work"][0]["session_item_id"], item_id)

    def test_activity_can_complete_snapshot_item_and_be_corrected(self):
        added = workouts.add_session_item(self.c, {"activity_name": "Easy run", "target_duration_s": 1200})
        item_id = added["item"]["id"]
        logged = core.log_activity(self.c, "run", 20, session_item_id=item_id, performed_at="2026-10-01T08:05:00Z")
        corrected = core.update_activity(self.c, logged["session_id"], duration_min=25, distance=5, distance_unit="km")
        self.assertEqual(corrected["duration_min"], 25)
        current = workouts.get_active(self.c, at="2026-10-01T08:06:00Z")["active_workout"]
        item = next(item for item in current["items"] if item["id"] == item_id)
        self.assertEqual(item["status"], "completed")
        self.assertEqual(current["activity"]["session_item_id"], item_id)

    def test_move_set_and_confirmed_merge_recalculate_sessions(self):
        workouts.finish_workout(self.c, at="2026-10-01T08:01:00Z")
        first = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}], performed_at="2026-10-01T09:00:00Z")
        workouts.finish_workout(self.c, at="2026-10-01T09:05:00Z")
        second = core.log_sets(self.c, [{"exercise": "bench press", "reps": 8}], performed_at="2026-10-01T09:20:00Z")
        workouts.finish_workout(self.c, at="2026-10-01T09:25:00Z")

        workouts.move_set(self.c, first["logged"][0]["set_id"], second["session_id"])
        with self.assertRaises(core.UserError):
            workouts.merge_sessions(self.c, first["session_id"], second["session_id"], confirmed=False)
        merged = workouts.merge_sessions(self.c, first["session_id"], second["session_id"], confirmed=True)
        self.assertEqual(merged["status"], "merged")
        self.assertIsNone(self.c.execute("SELECT id FROM sessions WHERE id=?", (first["session_id"],)).fetchone())

    def test_logged_set_can_be_corrected_and_recalculated(self):
        item_id = self.active["items"][0]["id"]
        logged = core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "weight": 100, "unit": "kg", "session_item_id": item_id}],
                               performed_at="2026-10-01T08:05:00Z")
        updated = core.update_set(self.c, logged["logged"][0]["set_id"], reps=6, weight=105, unit="kg")
        self.assertEqual((updated["set"]["reps"], updated["set"]["weight"]), (6, 105.0))
        self.assertGreater(updated["calorie_estimate"]["estimated_calories"], 0)

    def test_accepted_plan_accepts_exercise_names_and_snapshots_targets(self):
        workouts.abandon_workout(self.c, at="2026-10-01T08:01:00Z")
        started = workouts.start_workout(self.c, mode="conversational_plan", items=[
            {"exercise": "goblet squat", "target_sets": 3, "target_reps": 8, "target_weight": 24, "unit": "kg"}
        ], at="2026-10-01T09:00:00Z")
        self.assertEqual(started["items"][0]["canonical_name"], "Goblet Squat")
        self.assertEqual(started["items"][0]["target_weight_kg"], 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
