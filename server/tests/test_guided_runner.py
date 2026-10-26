"""Guided routine runner: step expansion, the guided_routine_timer tool, guided renderer mode and its pure JS core."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db as dbm
import core
import routines
import render_timer_html as r
import server as mcp_server

sys.path.insert(0, os.path.dirname(__file__))
import live_db_guard  # noqa: E402,F401  (also under plain unittest: never open the live database)

SCRIPT = os.path.join(os.path.dirname(r.__file__), "render_timer_html.py")
PREFIX = "<script>window.openai={toolOutput:"


def template():
    with open(r.DEFAULT_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def injected(html):
    start = html.index(PREFIX) + len(PREFIX)
    return json.loads(html[start:html.index("};</script>", start)])


def node_eval(expr):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    html = template()
    code = html[html.index("/* guided-core:begin"):html.index("/* guided-core:end")]
    out = subprocess.run([node, "-e", code + "\nprocess.stdout.write(JSON.stringify(" + expr + "));"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def work(steps):
    return [s for s in steps if s["kind"] != "rest"]


def kinds(steps):
    return [s["kind"] if s["kind"] != "rest" else ("rest", s["seconds"]) for s in steps]


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "workout.db")
        self.c = dbm.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()


class GuidedStepsTest(DbCase):
    def routine(self, items, **kw):
        rid = routines.create_routine(self.c, "Upper", items=items, **kw)["routine_id"]
        return routines.get_routine(self.c, rid)

    def test_expands_sets_with_default_rest_and_no_trailing_rest(self):
        rt = self.routine([{"exercise": "bench press", "target_sets": 3, "target_reps": 8},
                           {"exercise": "push-up", "target_reps": 12}])
        steps = routines.guided_steps(rt)
        self.assertEqual(kinds(steps), ["work", ("rest", 90), "work", ("rest", 90), "work", ("rest", 90), "work"])
        self.assertEqual([(s["set_index"], s["sets"]) for s in work(steps)], [(1, 3), (2, 3), (3, 3), (1, 1)])
        self.assertEqual(routines.GUIDED_DEFAULT_REST_SECONDS, 90)
        # The default is overridable; 0 removes rests that come from the default.
        self.assertEqual(kinds(routines.guided_steps(rt, 120))[1], ("rest", 120))
        self.assertEqual(kinds(routines.guided_steps(rt, 0)), ["work"] * 4)

    def test_item_rest_seconds_wins_and_zero_means_none(self):
        rt = self.routine([{"exercise": "squat", "target_sets": 2, "rest_seconds": 180},
                           {"exercise": "lunge", "target_sets": 2, "rest_seconds": 0},
                           {"exercise": "plank", "target_duration_s": 45}])
        self.assertEqual(kinds(routines.guided_steps(rt, 60)),
                         ["work", ("rest", 180), "work", ("rest", 180), "work", "work", "work"])

    def test_warmup_and_cooldown_items_rest_only_when_set(self):
        rt = self.routine([{"activity_name": "Treadmill warm-up", "target_duration_s": 300},
                           {"exercise": "push-up", "target_sets": 1},
                           {"exercise": "plank", "target_sets": 1, "notes": "cool-down core"},
                           {"activity_name": "Stretch cooldown", "rest_seconds": 15, "target_sets": 2}])
        steps = routines.guided_steps(rt)
        self.assertEqual(kinds(steps), ["warmup", "work", ("rest", 90), "cooldown", "cooldown", ("rest", 15), "cooldown"])
        self.assertTrue(steps[0]["is_warmup"])
        self.assertFalse(steps[0]["loggable"])  # activity-only item: no exercise to log sets against
        self.assertTrue(steps[1]["loggable"])

    def test_countdown_only_with_target_duration(self):
        rt = self.routine([{"exercise": "plank", "target_duration_s": 60, "target_sets": 2},
                           {"exercise": "deadlift", "target_reps": 5, "target_weight": 100, "unit": "kg"}])
        steps = work(routines.guided_steps(rt, unit="kg"))
        self.assertEqual([s["timer"] for s in steps], ["countdown", "countdown", "countup"])
        self.assertEqual(steps[0]["duration_s"], 60)
        self.assertIsNone(steps[2]["duration_s"])
        self.assertEqual((steps[2]["weight"], steps[2]["unit"], steps[2]["reps"]), (100, "kg", 5))

    def test_display_unit_and_zh_labels(self):
        rt = self.routine([{"exercise": "bench press", "target_weight": 135, "unit": "lb"}])
        step = routines.guided_steps(rt, unit="lb", lang="zh")[0]
        self.assertEqual((step["weight"], step["unit"]), (135, "lb"))
        self.assertEqual(step["exercise"], "Barbell Bench Press")
        self.assertRegex(step["label"], r"[\u4e00-\u9fff]")
        self.assertEqual(routines.guided_steps(rt, unit="kg")[0]["unit"], "kg")

    def test_rejects_hiit_and_bad_args(self):
        hiit = routines.create_routine(self.c, "Circuit", routine_type="hiit", items=[{"exercise": "burpee"}])
        with self.assertRaisesRegex(core.UserError, "hiit routine; use hiit_timer"):
            routines.guided_steps(routines.get_routine(self.c, hiit["routine_id"]))
        rt = self.routine([{"exercise": "squat"}])
        for bad in (-1, 3601, 1.5, True):
            with self.assertRaisesRegex(core.UserError, "default_rest_s"):
                routines.guided_steps(rt, bad)
        with self.assertRaisesRegex(core.UserError, "lang"):
            routines.guided_steps(rt, lang="fr")


class StandardRestSecondsTest(DbCase):
    def test_rest_seconds_persists_on_standard_create_and_update(self):
        rt = routines.create_routine(self.c, "Legs", items=[{"exercise": "squat", "target_sets": 3, "rest_seconds": 150}])
        self.assertEqual(routines.get_routine(self.c, rt["routine_id"])["items"][0]["rest_seconds"], 150)
        routines.update_routine(self.c, rt["routine_id"], items=[{"exercise": "squat", "rest_seconds": 45},
                                                                 {"exercise": "lunge"}])
        items = routines.get_routine(self.c, rt["routine_id"])["items"]
        self.assertEqual([x["rest_seconds"] for x in items], [45, None])

    def test_work_seconds_stays_hiit_only(self):
        with self.assertRaisesRegex(core.UserError, "work_seconds applies only to hiit"):
            routines.create_routine(self.c, "Legs", items=[{"exercise": "squat", "work_seconds": 30}])


class GuidedRoutineTimerToolTest(DbCase):
    def setUp(self):
        super().setUp()
        self.std = routines.create_routine(self.c, "Push", items=[
            {"exercise": "bench press", "target_sets": 2, "target_reps": 8, "rest_seconds": 120},
            {"exercise": "plank", "target_duration_s": 30}])
        self.hiit = routines.create_routine(self.c, "Circuit", routine_type="hiit", items=[{"exercise": "burpee"}])

    def test_starts_the_workout_and_maps_session_items(self):
        out = routines.guided_routine_timer(self.c, self.std["routine_id"], idempotency_key="g1")
        self.assertEqual((out["mode"], out["status"], out["work_sets"], out["default_rest_s"]), ("guided", "started", 3, 90))
        active = self.c.execute("SELECT id, source_routine_item_id FROM workout_session_items WHERE session_id=?",
                                (out["session_id"],)).fetchall()
        mapping = {row[1]: row[0] for row in active}
        for step in work(out["steps"]):
            self.assertEqual(step["session_item_id"], mapping[step["routine_item_id"]])
        self.assertEqual(kinds(out["steps"]), ["work", ("rest", 120), "work", ("rest", 120), "work"])
        self.assertIn("log_sets", out["completion_handoff"]["rule"])
        again = routines.guided_routine_timer(self.c, self.std["routine_id"], idempotency_key="g1")
        self.assertEqual((again["status"], again["session_id"]), ("already_started", out["session_id"]))

    def test_rejects_hiit_and_an_other_active_workout(self):
        before = self.c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        with self.assertRaisesRegex(core.UserError, "use hiit_timer"):
            routines.guided_routine_timer(self.c, self.hiit["routine_id"])
        with self.assertRaisesRegex(core.UserError, "default_rest_s"):
            routines.guided_routine_timer(self.c, self.std["routine_id"], default_rest_s=-5)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], before)  # nothing started
        routines.guided_routine_timer(self.c, self.std["routine_id"])
        with self.assertRaises(core.UserError):
            routines.guided_routine_timer(self.c, self.std["routine_id"])

    def test_logging_done_sets_at_done_at_keeps_them_in_the_workout(self):
        from datetime import datetime, timedelta
        out = routines.guided_routine_timer(self.c, self.std["routine_id"])
        started = datetime.fromisoformat(self.c.execute("SELECT started_at FROM sessions WHERE id=?",
                                                        (out["session_id"],)).fetchone()[0].replace("Z", "+00:00"))
        # The user tapped Done on both bench sets (10 and 22 minutes in) and skipped the plank; the bot logs later.
        for step, minutes in zip(work(out["steps"])[:2], (10, 22)):
            done_at = (started + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
            res = core.log_sets(self.c, [{"exercise": step["exercise"], "reps": step["reps"],
                                          "session_item_id": step["session_item_id"]}], performed_at=done_at)
            self.assertEqual((res["status"], res["session_id"], res["new_session"]), ("logged", out["session_id"], False))
        n = self.c.execute("SELECT COUNT(*) FROM sets WHERE session_id=?", (out["session_id"],)).fetchone()[0]
        self.assertEqual(n, 2)
        self.assertIn("done_at", out["completion_handoff"]["timing"])


class GuidedMcpToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = mcp_server.Server(os.path.join(self.tmp.name, "workout.db"))

    def tearDown(self):
        self.s.c.close()
        self.tmp.cleanup()

    def call(self, name, arguments=None):
        return self.s.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def test_schema_and_call(self):
        tools = {t["name"]: t for t in self.s.handle({"method": "tools/list", "params": {}})["tools"]}
        schema = tools["guided_routine_timer"]["inputSchema"]
        self.assertEqual(schema["required"], ["routine_id"])
        self.assertEqual(schema["properties"]["lang"]["enum"], ["en", "zh"])
        self.assertEqual(schema["properties"]["default_rest_s"]["default"], 90)
        self.assertIn("standard", tools["create_routine"]["inputSchema"]["properties"]["items"]["items"]
                      ["properties"]["rest_seconds"]["description"])
        rid = routines.create_routine(self.s.c, "Push", items=[{"exercise": "push-up", "target_sets": 2}])["routine_id"]
        res = self.call("guided_routine_timer", {"routine_id": rid, "default_rest_s": 60})
        self.assertFalse(res.get("isError"), res)
        data = res["structuredContent"]
        self.assertEqual(kinds(data["steps"]), ["work", ("rest", 60), "work"])
        self.assertIn("render_timer_html.py", res["content"][0]["text"])
        hid = routines.create_routine(self.s.c, "C", routine_type="hiit", items=[{"exercise": "burpee"}])["routine_id"]
        self.assertTrue(self.call("guided_routine_timer", {"routine_id": hid}).get("isError"))


GUIDED = {"mode": "guided", "name": "Push", "session_id": 7, "routine_id": 3, "steps": [
    {"kind": "warmup", "label": "Walk warm-up", "duration_s": 60, "loggable": False, "session_item_id": 11},
    {"kind": "work", "label": "Bench", "exercise": "Barbell Bench Press", "reps": 8, "weight": 60, "unit": "kg",
     "set_index": 1, "sets": 2, "session_item_id": 12},
    {"kind": "rest", "seconds": 90},
    {"kind": "work", "label": "Bench", "reps": 8, "set_index": 2, "sets": 2, "session_item_id": 12},
    {"kind": "rest", "seconds": 0},
    {"kind": "work", "label": "Plank", "duration_s": "0:45", "session_item_id": 13},
    {"kind": "rest", "seconds": 90}]}


class GuidedRendererTest(unittest.TestCase):
    def test_normalize_guided(self):
        out = r.normalize_config(GUIDED)
        self.assertEqual(out["mode"], "guided")
        self.assertEqual(out["label"], "Push")
        self.assertEqual([s["kind"] for s in out["steps"]], ["warmup", "work", "rest", "work", "work"])
        self.assertEqual([s.get("timer") for s in out["steps"]], ["countdown", "countup", None, "countup", "countdown"])
        self.assertEqual(out["steps"][4]["duration_s"], 45)
        self.assertEqual(out["session_item_ids"], [11, 12, None, 12, 13])
        self.assertNotIn("session_item_id", json.dumps(out["steps"]))
        self.assertEqual((out["session_id"], out["routine_id"]), (7, 3))
        self.assertTrue(out["steps"][0]["is_warmup"])
        self.assertFalse(out["steps"][0]["loggable"])
        self.assertNotIn("intervals", out)

    def test_normalize_guided_rejects_bad_steps(self):
        for steps, msg in (([], "non-empty"), ([{"kind": "jog", "label": "x"}], "step kind"),
                           ([{"kind": "work"}], "label"), ([{"kind": "rest", "seconds": 4000}], "rest"),
                           ([{"label": "x", "unit": "st", "weight": 3}], "unit"),
                           ([{"label": "x", "reps": -1}], "reps"), ("x", "non-empty")):
            with self.assertRaisesRegex(ValueError, msg):
                r.normalize_config({"mode": "guided", "steps": steps})
        with self.assertRaisesRegex(ValueError, "mode must be"):
            r.normalize_config({"mode": "circuit"})

    def test_render_smoke(self):
        html = r.render(GUIDED, template())
        cfg = injected(html)
        self.assertEqual(cfg["mode"], "guided")
        for needle in ('id="doneBtn"', 'id="summary"', 'id="list"', 'id="forBot"', 'cfg.mode==="guided"',
                       '<button id="start" type="button">Start</button>'):
            self.assertIn(needle, html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotRegex(html, r"(?i)https?://")
        zh = r.render({**GUIDED, "lang": "zh"}, template())
        self.assertEqual(injected(zh)["lang"], "zh")

    def test_guided_buttons_share_the_timer_button_size(self):
        # The guided Done button reuses the shared button size (same as Start/Reset and the HIIT timer);
        # only its colors differ, so it does not dwarf the other controls.
        html = template()
        rule = re.search(r"button#doneBtn\{([^}]*)\}", html).group(1)
        for prop in ("font-size", "min-width", "max-width", "padding"):
            self.assertNotIn(prop, rule)
        start = re.search(r"button#start\{([^}]*)\}", html).group(1)
        self.assertNotIn("font-size", start)

    def test_cache_ignores_per_run_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = r.normalize_config(GUIDED)
            b = r.normalize_config({**GUIDED, "session_id": 8,
                                    "steps": [dict(s, session_item_id=s["session_item_id"] + 10) if "session_item_id" in s else s
                                              for s in GUIDED["steps"]]})
            html_a, key_a, hit_a = r.render_cached(a, template(), tmp)
            html_b, key_b, hit_b = r.render_cached(b, template(), tmp)
            self.assertEqual((key_a, hit_a, hit_b), (key_b, False, True))
            self.assertEqual(injected(html_a)["session_id"], 7)
            self.assertEqual(injected(html_b)["session_id"], 8)
            self.assertEqual(injected(html_b)["session_item_ids"], [21, 22, None, 22, 23])
            with open(os.path.join(tmp, key_a + ".html"), encoding="utf-8") as f:
                shared = injected(f.read())
            self.assertNotIn("session_id", shared)
            self.assertNotIn("session_item_ids", shared)
            self.assertEqual(injected(r.apply_hiit_id(html_b, 5))["hiit_id"], 5)

    def test_cli_config_and_duration_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "g.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(GUIDED, f)
            out = os.path.join(tmp, "g.html")
            subprocess.run([sys.executable, SCRIPT, out, "--config", cfg, "--lang", "zh"], check=True, capture_output=True)
            with open(out, encoding="utf-8") as f:
                data = injected(f.read())
            self.assertEqual((data["mode"], data["lang"], len(data["steps"])), ("guided", "zh", 5))
            # --duration turns the page back into a plain countdown.
            subprocess.run([sys.executable, SCRIPT, out, "--config", cfg, "--duration", "60"], check=True, capture_output=True)
            with open(out, encoding="utf-8") as f:
                data = injected(f.read())
            self.assertNotIn("mode", data)
            self.assertNotIn("steps", data)


class GuidedCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "workout.db")
        c = dbm.connect(self.db)
        self.std = routines.create_routine(c, "Push", items=[{"exercise": "push-up", "target_sets": 3},
                                                             {"exercise": "plank", "target_duration_s": 30, "rest_seconds": 20}])
        self.hiit = routines.create_routine(c, "Circuit", routine_type="hiit", items=[{"exercise": "burpee"}])
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
                data = injected(f.read())
        return res, data

    def test_guided_preview_is_read_only(self):
        before = os.path.getmtime(self.db)
        rid = str(self.std["routine_id"])
        res, data = self.run_cli("--routine-id", rid, "--db", self.db, "--guided", "--default-rest", "1:00")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(data["mode"], "guided")
        self.assertEqual(kinds(data["steps"]), ["work", ("rest", 60), "work", ("rest", 60), "work", ("rest", 60), "work"])
        self.assertNotIn("session_id", data)
        self.assertEqual(os.path.getmtime(self.db), before)
        res, data = self.run_cli("--routine-id", rid, "--db", self.db, "--guided")
        self.assertEqual(kinds(data["steps"])[1], ("rest", 90))

    def test_guided_errors(self):
        rid, hid = str(self.std["routine_id"]), str(self.hiit["routine_id"])
        for args, msg in ((["--routine-id", rid, "--db", self.db], "--guided"),
                          (["--routine-id", hid, "--db", self.db, "--guided"], "use hiit_timer"),
                          (["--routine-id", rid, "--db", self.db, "--guided", "--rounds", "2"], "--rounds"),
                          (["--routine-id", rid, "--db", self.db, "--default-rest", "30"], "needs --guided"),
                          (["--duration", "30", "--guided"], "only used with --routine-id"),
                          (["--routine-id", rid, "--db", self.db, "--guided", "--default-rest", "9999"], "default-rest")):
            res, data = self.run_cli(*args)
            self.assertNotEqual(res.returncode, 0, args)
            self.assertIn(msg, res.stderr, args)
            self.assertIsNone(data)


class GuidedCoreJsTest(unittest.TestCase):
    CFG = json.dumps(r.normalize_config(GUIDED))

    def test_prep_attaches_session_item_ids(self):
        steps = node_eval(f"gPrep({self.CFG})")
        self.assertEqual([s.get("session_item_id") for s in steps], [11, 12, None, 12, 13])
        self.assertEqual([s.get("step") for s in steps], [1, 2, 3, 4, 5])

    def test_entry_records_actuals(self):
        steps = f"gPrep({self.CFG})"
        hold_early, hold_auto, reps, untimed = node_eval(
            f"(function(s){{return [gEntry(s[4],20,false,Date.UTC(2026,9,26,15,0,0,400)),gEntry(s[4],45,true),gEntry(s[1],70,false),"
            f"gEntry(Object.assign({{}},s[1],{{reps:null}}),70,false)]}})({steps})")
        self.assertEqual((hold_early["duration_s"], hold_early["auto"]), (20, False))
        self.assertEqual(hold_early["done_at"], "2026-10-26T15:00:00Z")
        self.assertIsNone(reps["done_at"])
        self.assertEqual((hold_auto["duration_s"], hold_auto["auto"]), (45, True))
        self.assertEqual((reps["reps"], reps["duration_s"], reps["session_item_id"], reps["elapsed_s"]), (8, None, 12, 70))
        self.assertEqual(untimed["duration_s"], 70)

    def test_catch_up_advances_rests_and_holds_but_never_count_up_work(self):
        steps = f"gPrep({self.CFG})"
        # Warm-up hold (60 s) started at t=0; after 61 s it auto-completes and stops on the count-up bench set.
        res = node_eval(f"gCatchUp({steps},0,0,61000)")
        self.assertEqual(res["idx"], 1)
        self.assertEqual([e["step"] for e in res["done"]], [1])
        self.assertTrue(res["done"][0]["auto"])
        self.assertEqual(res["done"][0]["done_at"], "1970-01-01T00:01:00Z")  # when the hold reached 0
        # Count-up work never ends on its own, however long it runs.
        self.assertEqual(node_eval(f"gCatchUp({steps},1,0,9e6)")["idx"], 1)
        # A 90 s rest that ended lands on the next set, carrying the overshoot into its start time.
        res = node_eval(f"gCatchUp({steps},2,0,100000)")
        self.assertEqual((res["idx"], res["t0"], res["done"]), (3, 90000, []))
        # Past the last timed step: finished.
        self.assertEqual(node_eval(f"gCatchUp({steps},4,0,46000)")["idx"], 5)

    def test_cues(self):
        plan = node_eval("gCues(10,'work')")
        self.assertEqual([(c["t"], c["type"]) for c in plan["cues"]], [(8, "short"), (9, "short"), (10, "long")])
        self.assertEqual(plan["ticks"], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(node_eval("gCues(10,'rest')")["cues"][0]["tone"], "rest")
        self.assertEqual(node_eval("gCues(null,'work')"), {"cues": [], "ticks": []})
