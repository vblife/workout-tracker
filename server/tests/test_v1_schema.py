import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm


class SchemaV1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_database_has_v1_domain_schema(self):
        c = dbm.connect(self.path)
        self.addCleanup(c.close)
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 4)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({
            "workout_routines", "workout_routine_items", "workout_session_items",
            "goals", "body_weight", "nutrition_entries",
        }.issubset(tables))
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({
            "status", "source_routine_id", "start_mode", "last_activity_at",
            "estimated_calories", "calorie_confidence", "calorie_assumptions",
        }.issubset(cols))

    def test_legacy_sessions_migrate_as_completed(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(dbm.SCHEMA)
        c.execute("INSERT INTO schema_version VALUES(1)")
        c.execute("INSERT INTO sessions(started_at, ended_at, type) VALUES('2026-10-01T08:00:00Z','2026-10-01T09:00:00Z','strength')")
        c.commit()
        dbm.init(c)
        row = c.execute("SELECT status, start_mode, last_activity_at FROM sessions").fetchone()
        self.assertEqual((row["status"], row["start_mode"]), ("completed", "implicit"))
        self.assertEqual(row["last_activity_at"], "2026-10-01T09:00:00Z")
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 4)
        c.close()

    def test_database_enforces_only_one_active_workout(self):
        c = dbm.connect(self.path)
        self.addCleanup(c.close)
        c.execute("INSERT INTO sessions(started_at,status,start_mode,last_activity_at) VALUES('2026-10-01T08:00:00Z','in_progress','implicit','2026-10-01T08:00:00Z')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO sessions(started_at,status,start_mode,last_activity_at) VALUES('2026-10-01T09:00:00Z','in_progress','implicit','2026-10-01T09:00:00Z')")

    def test_failed_migration_rolls_back_all_schema_changes(self):
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        c.execute("INSERT INTO schema_version VALUES(1)")
        c.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, type TEXT)")
        c.commit()
        with self.assertRaises(sqlite3.OperationalError):
            dbm._migrate_v2(c)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("workout_routines", tables)
        self.assertNotIn("status", {r[1] for r in c.execute("PRAGMA table_info(sessions)")})
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 1)
        c.close()

    def test_foreign_keys_and_transaction_rollback(self):
        c = dbm.connect(self.path)
        self.addCleanup(c.close)
        with self.assertRaises(sqlite3.IntegrityError):
            with dbm.transaction(c):
                c.execute("INSERT INTO workout_routines(name,created_at,updated_at) VALUES('Temporary','2026-10-01T08:00:00Z','2026-10-01T08:00:00Z')")
                c.execute("INSERT INTO workout_routine_items(routine_id,position,exercise_id) VALUES(999,1,1)")
        self.assertEqual(c.execute("SELECT count(*) FROM workout_routines").fetchone()[0], 0)

    def test_default_database_path(self):
        self.assertEqual(dbm.DEFAULT_DB, "/workspace/vblife/workout-data/workout.db")


if __name__ == "__main__":
    unittest.main(verbosity=2)
