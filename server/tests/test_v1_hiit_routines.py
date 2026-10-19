import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import routines
import render_timer_html as r

sys.path.insert(0, os.path.dirname(__file__))
import live_db_guard  # noqa: E402,F401  (also under plain unittest: never open the live database)

SCRIPT = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
T = "2026-10-01T08:00:00Z"


def legacy_v2_db(path):
    """A schema-v2 database (before routine types) with one timed circuit and one strength routine."""
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(dbm.SCHEMA)
    c.execute("INSERT INTO schema_version VALUES(1)")
    c.commit()
    dbm.seed(c)
    dbm._migrate_v2(c)
    ex = [row[0] for row in c.execute("SELECT id FROM exercises ORDER BY id LIMIT 6")]

    def routine(name, items):
        rid = c.execute("INSERT INTO workout_routines(name,created_at,updated_at) VALUES(?,?,?)", (name, T, T)).lastrowid
        for pos, item in enumerate(items, 1):
            c.execute("""INSERT INTO workout_routine_items(routine_id,position,exercise_id,target_sets,target_reps,
                target_weight_kg,target_duration_s,notes) VALUES(?,?,?,?,?,?,?,?)""", (rid, pos, *item))
        return rid

    # Same shapes as a real install: a 20-item 40 s circuit named HIIT, and a sets/reps routine.
    hiit = routine("HIIT 四组循环", [(ex[i % 4], None, None, None, 40, f"group {i // 5 + 1}") for i in range(20)])
    std = routine("胸背上肢", [(ex[4], 3, 12, 9.07, None, None), (ex[5], 3, 10, None, None, None)])
    timed = routine("Core finisher", [(ex[0], None, None, None, 30, None), (ex[1], None, None, None, 45, None)])
    named = routine("Friday hiit", [(ex[2], 3, 15, None, None, None)])
    mixed = routine("Legs and cardio", [(ex[3], None, None, None, 60, None), (ex[4], 3, 8, 50, None, None)])
    empty = routine("Empty", [])
    c.commit()
    return c, {"hiit": hiit, "std": std, "timed": timed, "named": named, "mixed": mixed, "empty": empty}


def snapshot(c):
    return {table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("workout_routines", "workout_routine_items")}


class MigrationV3Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_backfill_is_conservative_and_non_destructive(self):
        c, ids = legacy_v2_db(self.path)
        before = snapshot(c)
        dbm.init(c)
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 3)
        types = dict(c.execute("SELECT id,routine_type FROM workout_routines").fetchall())
        self.assertEqual(types[ids["hiit"]], "hiit")     # name says HIIT (and every item is timed)
        self.assertEqual(types[ids["std"]], "standard")  # sets/reps/weight
        self.assertEqual(types[ids["timed"]], "hiit")    # all items timed, no sets/reps
        self.assertEqual(types[ids["named"]], "hiit")    # name contains hiit, case-insensitive
        self.assertEqual(types[ids["mixed"]], "standard")
        self.assertEqual(types[ids["empty"]], "standard")
        after = snapshot(c)
        # Every pre-existing value is unchanged; only new columns were appended.
        for table in before:
            self.assertEqual(len(before[table]), len(after[table]))
            for old, new in zip(before[table], after[table]):
                self.assertEqual(new[:len(old)], old)
        work = c.execute("SELECT DISTINCT work_seconds,rest_seconds FROM workout_routine_items WHERE routine_id=?", (ids["hiit"],)).fetchall()
        self.assertEqual([tuple(x) for x in work], [(40, None)])  # copied from target_duration_s; rest uses the default
        self.assertEqual(c.execute("SELECT count(work_seconds) FROM workout_routine_items WHERE routine_id=?", (ids["std"],)).fetchone()[0], 0)
        c.close()

    def test_migration_is_idempotent(self):
        c, ids = legacy_v2_db(self.path)
        dbm.init(c)
        routines.update_routine(c, ids["timed"], routine_type="standard")  # user correction survives re-runs
        once = snapshot(c)
        dbm._migrate_v3(c)
        dbm.init(c)
        c.close()
        c = dbm.connect(self.path)
        self.assertEqual(snapshot(c), once)
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 3)
        self.assertEqual(routines.get_routine(c, ids["timed"])["routine_type"], "standard")
        c.close()

    def test_failed_migration_rolls_back(self):
        c, ids = legacy_v2_db(self.path)
        c.execute("DROP TABLE workout_routine_items")  # make the second ALTER fail
        c.commit()
        with self.assertRaises(sqlite3.OperationalError):
            dbm._migrate_v3(c)
        self.assertNotIn("routine_type", dbm._columns(c, "workout_routines"))
        self.assertEqual(c.execute("SELECT version FROM schema_version").fetchone()[0], 2)
        c.close()

    def test_new_database_constraints(self):
        c = dbm.connect(self.path)
        self.addCleanup(c.close)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO workout_routines(name,created_at,updated_at,routine_type) VALUES('x',?,?,'tabata')", (T, T))
        rid = c.execute("INSERT INTO workout_routines(name,created_at,updated_at) VALUES('x',?,?)", (T, T)).lastrowid
        self.assertEqual(c.execute("SELECT routine_type FROM workout_routines WHERE id=?", (rid,)).fetchone()[0], "standard")

    def test_readonly_connection_requires_current_schema_and_never_writes(self):
        c, _ = legacy_v2_db(self.path)
        c.close()
        with self.assertRaises(RuntimeError) as raised:
            dbm.connect_readonly(self.path)
        self.assertIn("schema version 2", str(raised.exception))
        c = dbm.connect(self.path)
        c.close()
        ro = dbm.connect_readonly(self.path)
        self.addCleanup(ro.close)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("UPDATE workout_routines SET name='x'")
        with self.assertRaises(FileNotFoundError):
            dbm.connect_readonly(os.path.join(self.tmp.name, "missing.db"))

    def test_tests_cannot_open_the_live_database(self):
        with self.assertRaises(RuntimeError):
            dbm.connect(dbm.DEFAULT_DB)
        with self.assertRaises(RuntimeError):
            dbm.connect_readonly(dbm.DEFAULT_DB)
        self.assertNotEqual(os.path.realpath(os.environ["WORKOUT_DB"]), os.path.realpath(dbm.DEFAULT_DB))


class HiitRoutineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")
        self.c = dbm.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def make(self, **kw):
        items = kw.pop("items", [{"exercise": "burpee"}, {"exercise": "jump squat", "work_seconds": 30, "rest_seconds": 15},
                                 {"exercise": "jumping jack"}])
        kw.setdefault("routine_type", "hiit")
        return routines.create_routine(self.c, kw.pop("name", "Circuit"), items=items, **kw)

    def test_crud_with_type_defaults_and_filter(self):
        hiit = self.make()
        self.assertEqual(hiit["routine_type"], "hiit")
        self.assertEqual([(i["exercise"], i["work_seconds"], i["rest_seconds"]) for i in hiit["items"]],
                         [("Burpee", 40, 20), ("Jump Squat", 30, 15), ("Jumping Jack", 40, 20)])
        self.assertIsNone(hiit["rounds"])
        std = routines.create_routine(self.c, "Push", items=[{"exercise": "push-up", "target_sets": 3, "target_reps": 10}])
        self.assertEqual(std["routine_type"], "standard")
        self.assertIsNone(std["items"][0]["work_seconds"])
        self.assertEqual([x["routine_id"] for x in routines.list_routines(self.c, routine_type="hiit")["routines"]], [hiit["routine_id"]])
        self.assertEqual([x["routine_id"] for x in routines.list_routines(self.c, routine_type="standard")["routines"]], [std["routine_id"]])
        self.assertEqual(len(routines.list_routines(self.c)["routines"]), 2)
        # Fixing a wrong classification is one call; timed items pick up the HIIT defaults.
        fixed = routines.update_routine(self.c, std["routine_id"], routine_type="hiit", rounds=3)
        self.assertEqual((fixed["routine_type"], fixed["rounds"], fixed["items"][0]["work_seconds"]), ("hiit", 3, 40))
        back = routines.update_routine(self.c, std["routine_id"], routine_type="standard")
        self.assertEqual(back["routine_type"], "standard")
        updated = routines.update_routine(self.c, hiit["routine_id"], items=[{"exercise": "burpee", "rest_seconds": 0},
                                                                             {"exercise": "plank", "target_duration_s": 50}])
        self.assertEqual([(i["work_seconds"], i["rest_seconds"]) for i in updated["items"]], [(40, 0), (50, 20)])

    def test_validation(self):
        bad = [
            dict(routine_type="tabata"),
            dict(items=[{"exercise": "burpee", "work_seconds": 0}]),
            dict(items=[{"exercise": "burpee", "rest_seconds": -1}]),
            dict(items=[{"exercise": "burpee", "work_seconds": True}]),
            dict(rounds=0), dict(rounds=101), dict(round_options=[10]), dict(round_options=[10, 10]),
            dict(round_options=[10, 20], rounds=15),
        ]
        for kw in bad:
            with self.assertRaises(core.UserError, msg=kw):
                self.make(**kw)
        with self.assertRaises(core.UserError):
            routines.create_routine(self.c, "Std", items=[{"exercise": "burpee", "work_seconds": 30}])
        with self.assertRaises(core.UserError):
            routines.create_routine(self.c, "Std", items=[{"exercise": "burpee"}], rounds=3)
        with self.assertRaises(core.UserError):
            routines.list_routines(self.c, routine_type="cardio")
        self.assertEqual(self.make(round_options=[20, 10])["rounds"], 10)

    def test_hiit_timer_from_routine(self):
        hiit = self.make(rounds=2)
        cfg = core.create_hiit(self.c, routine_id=hiit["routine_id"])
        self.assertEqual(cfg["routine_id"], hiit["routine_id"])
        self.assertEqual(cfg["name"], "Circuit")
        self.assertEqual(cfg["rounds"], 2)
        self.assertEqual([(s["kind"], s["seconds"], s["exercise"]) for s in cfg["segments"]], [
            ("work", 40, "Burpee"), ("rest", 20, None), ("work", 30, "Jump Squat"), ("rest", 15, None),
            ("work", 40, "Jumping Jack"), ("rest", 20, None)])
        self.assertEqual(cfg["planned_seconds"], 10 + 60 + 2 * 165 - 20)  # final rest skipped
        stored = json.loads(self.c.execute("SELECT config_json FROM hiit_workouts WHERE id=?", (cfg["hiit_id"],)).fetchone()[0])
        self.assertEqual(stored["routine_id"], hiit["routine_id"])
        # The timer template expands it: N exercises -> N work intervals per round, rests between, no final rest.
        out = r.normalize_config(cfg)
        works = [x for x in out["intervals"] if x["kind"] == "work"]
        self.assertEqual([x["label"] for x in works], ["Burpee", "Jump Squat", "Jumping Jack"] * 2)
        self.assertEqual(out["intervals"][-2]["kind"], "work")
        self.assertEqual(out["intervals"][-1]["kind"], "cooldown")
        self.assertEqual(len(out["intervals"]), 1 + 6 + 5 + 1)
        zh = core.create_hiit(self.c, routine_id=hiit["routine_id"], lang="zh", rounds=1)
        self.assertEqual(zh["segments"][0]["exercise"], "波比跳")
        self.assertEqual(zh["rounds"], 1)

    def test_hiit_timer_routine_errors_and_round_options(self):
        std = routines.create_routine(self.c, "Push", items=[{"exercise": "push-up", "target_sets": 3}])
        with self.assertRaises(core.UserError) as raised:
            core.create_hiit(self.c, routine_id=std["routine_id"])
        self.assertIn("routine_type", str(raised.exception))
        hiit = self.make(round_options=[2, 4])
        with self.assertRaises(core.UserError):
            core.create_hiit(self.c, routine_id=hiit["routine_id"], rounds=3)
        with self.assertRaises(core.UserError):
            core.create_hiit(self.c, routine_id=hiit["routine_id"], segments=[{"kind": "work", "seconds": 10}])
        cfg = core.create_hiit(self.c, routine_id=hiit["routine_id"])
        self.assertEqual((cfg["rounds"], cfg["round_options"]), (2, [2, 4]))
        out = r.normalize_config(cfg)
        self.assertEqual(out["round_options"], [2, 4])
        self.assertEqual(out["round_totals"], {"2": 10 + 60 + 2 * 165 - 20, "4": 10 + 60 + 4 * 165 - 20})

    def test_ad_hoc_hiit_still_works(self):
        cfg = core.create_hiit(self.c, rounds=3, segments=[{"kind": "work", "seconds": 40, "exercise": "Squats"},
                                                           {"kind": "rest", "seconds": 20}])
        self.assertNotIn("routine_id", cfg)
        self.assertEqual(cfg["planned_seconds"], 10 + 60 + 3 * 60 - 20)
        self.assertEqual(core.create_hiit(self.c)["rounds"], 20)

    def test_start_routine_still_snapshots_hiit_routines(self):
        hiit = self.make()
        started = routines.start_routine(self.c, hiit["routine_id"])
        self.assertEqual(started["start_mode"], "routine")


class RoutineTimerCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "workout.db")
        c = dbm.connect(self.db)
        self.hiit = routines.create_routine(c, "Circuit", routine_type="hiit", rounds=2,
                                            items=[{"exercise": "burpee"}, {"exercise": "jump squat", "rest_seconds": 10}])
        self.opts = routines.create_routine(c, "Options", routine_type="hiit", round_options=[10, 20],
                                            items=[{"exercise": "burpee"}])
        self.std = routines.create_routine(c, "Push", items=[{"exercise": "push-up", "target_sets": 3}])
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = os.path.join(self.tmp.name, "t.html")
        if os.path.exists(out):
            os.remove(out)
        res = subprocess.run([sys.executable, SCRIPT, out, *args], capture_output=True, text=True)
        data = None
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                html = f.read()
            data = json.loads(html[html.index("toolOutput:") + 11:html.index("};</script>")])
        return res, data

    def test_cli_routine_id(self):
        before = os.path.getmtime(self.db)
        res, data = self.run_cli("--routine-id", str(self.hiit["routine_id"]), "--db", self.db)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(data["label"], "Circuit")
        self.assertEqual([x["label"] for x in data["intervals"] if x["kind"] == "work"], ["Burpee", "Jump Squat"] * 2)
        self.assertEqual([x["kind"] for x in data["intervals"]],
                         ["warmup", "work", "rest", "work", "rest", "work", "rest", "work", "cooldown"])
        self.assertEqual(data["total_s"], 10 + 60 + 2 * 110 - 10)
        self.assertEqual(os.path.getmtime(self.db), before)  # read-only
        res, data = self.run_cli("--routine-id", str(self.hiit["routine_id"]), "--db", self.db, "--lang", "zh",
                                 "--label", "循环", "--rounds", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((data["lang"], data["rounds"], data["intervals"][1]["label"]), ("zh", 1, "波比跳"))
        res, data = self.run_cli("--routine-id", str(self.opts["routine_id"]), "--db", self.db)
        self.assertEqual((data["round_options"], data["rounds"]), ([10, 20], 10))
        # WORKOUT_DB is honored when --db is omitted.
        env = {**os.environ, "WORKOUT_DB": self.db}
        out = os.path.join(self.tmp.name, "env.html")
        res = subprocess.run([sys.executable, SCRIPT, out, "--routine-id", str(self.hiit["routine_id"])],
                             capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_cli_routine_id_errors(self):
        rid = str(self.hiit["routine_id"])
        cases = [
            (["--routine-id", "999", "--db", self.db], "no such routine"),
            (["--routine-id", str(self.std["routine_id"]), "--db", self.db], "routine_type"),
            (["--routine-id", rid, "--db", os.path.join(self.tmp.name, "missing.db")], "not found"),
            (["--routine-id", rid, "--db", self.db, "--work", "40"], "cannot be combined"),
            (["--routine-id", rid, "--db", self.db, "--rounds", "1,2"], "one number"),
            (["--routine-id", str(self.opts["routine_id"]), "--db", self.db, "--rounds", "15"], "round_options"),
            (["--duration", "60", "--db", self.db], "only used with --routine-id"),
        ]
        for args, needle in cases:
            res, data = self.run_cli(*args)
            self.assertEqual(res.returncode, 2, args)
            self.assertIsNone(data)
            errors = [x for x in res.stderr.splitlines() if "error:" in x]
            self.assertEqual(len(errors), 1, res.stderr)
            self.assertIn(needle, errors[0], args)


class HiitRoutineSchemaTest(unittest.TestCase):
    def test_tool_schemas_expose_type_and_per_item_timing(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("workout_mcp_server_h", os.path.join(os.path.dirname(r.__file__), "server.py"))
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        tools = {t["name"]: t["inputSchema"] for t in server.TOOLS}
        for name in ("create_routine", "update_routine"):
            props = tools[name]["properties"]
            self.assertEqual(props["routine_type"]["enum"], ["standard", "hiit"])
            self.assertIn("rounds", props)
            self.assertIn("round_options", props)
            item = props["items"]["items"]["properties"]
            self.assertIn("work_seconds", item)
            self.assertIn("rest_seconds", item)
        self.assertEqual(tools["list_routines"]["properties"]["routine_type"]["enum"], ["standard", "hiit"])
        self.assertIn("routine_id", tools["hiit_timer"]["properties"])
        self.assertEqual(tools["hiit_timer"]["properties"]["lang"]["enum"], ["en", "zh"])
        # Active-workout items keep the original contract.
        self.assertNotIn("work_seconds", tools["add_workout_item"]["properties"]["item"]["properties"])


class TimerNextUpTest(unittest.TestCase):
    def test_work_interval_names_the_next_exercise(self):
        with open(r.DEFAULT_TEMPLATE, encoding="utf-8") as f:
            html = f.read()
        self.assertIn('s.kind==="work"&&r&&r.kind==="rest"&&w&&w.kind==="work"&&w.label?w:null', html)
        self.assertIn('up?t("next")+": "+up.label', html)
        with open(os.path.join(os.path.dirname(r.__file__), "ui", "hiit.html"), encoding="utf-8") as f:
            widget = f.read()
        self.assertIn("up?`Next: ${up.exercise}`", widget)


if __name__ == "__main__":
    unittest.main(verbosity=2)
