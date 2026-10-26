"""MCP Apps guided runner: the guided_routine_timer UI resource and the runner's log-on-Done / finish-on-End logic."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import routines
import server as mcp_server

sys.path.insert(0, os.path.dirname(__file__))
import live_db_guard  # noqa: E402,F401  (also under plain unittest: never open the live database)

GUIDED_URI = "ui://workout/guided.html"
UI_INIT = {"protocolVersion": "2025-06-18", "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {}}}}


def page():
    with open(os.path.join(os.path.dirname(mcp_server.__file__), "ui", "timer.html"), encoding="utf-8") as f:
        return f.read()


def node_run(body):
    """Run `body` (which must print one JSON value) after the page's pure guided-core and guided-log blocks."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    html = page()
    code = (html[html.index("/* guided-core:begin"):html.index("/* guided-core:end")] +
            html[html.index("/* guided-log:begin"):html.index("/* guided-log:end")])
    out = subprocess.run([node, "-e", code + "\n" + body], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def ok_result(name):
    if name == "log_sets":
        return {"content": [{"type": "text", "text": "Logged: x"}], "structuredContent": {"status": "logged"}}
    return {"content": [{"type": "text", "text": "{}"}], "structuredContent": {}}


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def server(self, ui, name="w.db"):
        s = mcp_server.Server(os.path.join(self.tmp.name, name))
        self.addCleanup(s.c.close)
        if ui:
            s.handle({"method": "initialize", "params": UI_INIT})
        return s

    @staticmethod
    def call(s, name, arguments=None):
        return s.handle({"method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def routine(self, s):
        return routines.create_routine(s.c, "Push", items=[
            {"activity_name": "Warm-up walk", "target_duration_s": 60},
            {"exercise": "bench press", "target_sets": 2, "target_reps": 8, "target_weight": 60, "rest_seconds": 90},
            {"exercise": "plank", "target_duration_s": 30}])["routine_id"]


class GuidedUiResourceTest(ServerCase):
    def test_tool_meta_points_at_the_guided_view(self):
        tools = {t["name"]: t for t in self.server(True).handle({"method": "tools/list", "params": {}})["tools"]}
        meta = tools["guided_routine_timer"]["_meta"]
        self.assertEqual(meta["ui"]["resourceUri"], GUIDED_URI)
        self.assertEqual(meta["ui"]["visibility"], ["model", "app"])
        self.assertEqual(meta["openai/outputTemplate"], GUIDED_URI)
        for name in ("log_sets", "set_workout_item_status", "finish_workout"):  # the view calls these
            self.assertIn("app", tools[name]["_meta"]["ui"]["visibility"], name)
        self.assertIn("ui_auto_log", tools["guided_routine_timer"]["description"])

    def test_resource_serves_the_runner_page(self):
        s = self.server(True)
        listed = {r["uri"]: r for r in s.handle({"method": "resources/list", "params": {}})["resources"]}
        self.assertEqual(listed[GUIDED_URI]["mimeType"], "text/html;profile=mcp-app")
        content = s.handle({"method": "resources/read", "params": {"uri": GUIDED_URI}})["contents"][0]
        self.assertEqual(content["text"], page())
        for marker in ("ui/initialize", "ui/notifications/tool-result", "tools/call", "finish_workout", "ui/resource-teardown"):
            self.assertIn(marker, content["text"])

    def test_ui_call_attaches_the_runner_and_skips_the_offline_handoff_text(self):
        s = self.server(True)
        res = self.call(s, "guided_routine_timer", {"routine_id": self.routine(s)})
        self.assertFalse(res.get("isError"), res)
        self.assertEqual(res["_meta"]["ui"]["resourceUri"], GUIDED_URI)
        self.assertIs(res["structuredContent"]["ui_auto_log"], True)
        text = res["content"][0]["text"]
        self.assertIn("logs each set when the user taps Done", text)
        self.assertNotIn("render_timer_html.py --config.", text)

    def test_clients_without_apps_keep_the_offline_fallback(self):
        s = self.server(False)
        tools = {t["name"]: t for t in s.handle({"method": "tools/list", "params": {}})["tools"]}
        self.assertNotIn("_meta", tools["guided_routine_timer"])
        res = self.call(s, "guided_routine_timer", {"routine_id": self.routine(s)})
        self.assertNotIn("_meta", res)
        sc = res["structuredContent"]
        self.assertEqual((sc["ui_auto_log"], sc["delivery"]), (False, "offline_page"))  # explicit, never implied
        self.assertIn("io.modelcontextprotocol/ui", sc["delivery_reason"])
        self.assertIn("render_timer_html.py", res["content"][0]["text"])
        self.assertIn("Offline fallback only", res["structuredContent"]["completion_handoff"]["applies_to"])


STEPS = [
    {"step": 1, "exercise": "Walking", "loggable": False, "session_item_id": 11, "duration_s": 60, "done_at": "2026-10-26T15:01:00Z"},
    {"step": 2, "exercise": "Barbell Bench Press", "reps": 8, "weight": 60, "unit": "kg", "is_warmup": False,
     "session_item_id": 12, "duration_s": None, "distance_m": None, "done_at": "2026-10-26T15:03:00Z"},
    {"step": 5, "exercise": "Plank", "reps": None, "weight": None, "unit": None, "duration_s": 30, "session_item_id": 13,
     "done_at": "2026-10-26T15:08:00Z"},
]


class GuidedLogJsTest(unittest.TestCase):
    def test_log_call_per_done_entry(self):
        walk, bench, plank, orphan = node_run(
            f"var e={json.dumps(STEPS)};process.stdout.write(JSON.stringify([gLogCall(e[0]),gLogCall(e[1]),gLogCall(e[2]),"
            "gLogCall({step:9,loggable:false,session_item_id:null})]))")
        self.assertEqual(walk, {"name": "set_workout_item_status", "arguments": {"item_id": 11, "status": "completed"}})
        self.assertEqual(bench, {"name": "log_sets", "arguments": {"performed_at": "2026-10-26T15:03:00Z", "sets": [
            {"exercise": "Barbell Bench Press", "reps": 8, "weight": 60, "unit": "kg", "is_warmup": False, "session_item_id": 12}]}})
        self.assertEqual(plank["arguments"]["sets"], [{"exercise": "Plank", "duration_s": 30, "is_warmup": False, "session_item_id": 13}])
        self.assertIsNone(orphan)

    def test_tool_ok(self):
        cases = node_run(
            "process.stdout.write(JSON.stringify(["
            "gToolOk('log_sets',{structuredContent:{status:'logged'}}),"
            "gToolOk('log_sets',{structuredContent:{status:'needs_confirmation'}}),"
            "gToolOk('log_sets',{content:[{type:'text',text:'Nothing saved - needs confirmation'}]}),"
            "gToolOk('log_sets',{isError:true,content:[{type:'text',text:'Error: x'}]}),"
            "gToolOk('finish_workout',{structuredContent:{status:'completed'}}),"
            "gToolOk('finish_workout',{isError:true,content:[{type:'text',text:'Error: no active workout'}]}),"
            "gToolOk('set_workout_item_status',null)]))")
        self.assertEqual(cases, [True, False, False, False, True, True, False])

    def run_logger(self, script, fail="null"):
        """`script` drives a logger L whose calls are recorded in `calls`; fail(name,args,n) may return 'error'/'timeout'."""
        return node_run(
            "var calls=[],fail=" + fail + ";var ok=" + json.dumps({"log_sets": ok_result("log_sets"), "other": ok_result("x")}) + ";"
            "function call(n,a){calls.push({name:n,arguments:a});var f=fail&&fail(n,a,calls.length);"
            " if(f==='timeout')return Promise.reject(new Error('timeout'));if(f==='error')return Promise.reject(new Error('boom'));"
            " return new Promise(function(r){setTimeout(function(){r(n==='log_sets'?ok.log_sets:ok.other)},5)})}"
            "var L=gLogger(call,gToolOk);var e=" + json.dumps(STEPS) + ";" + script +
            ".then(function(st){process.stdout.write(JSON.stringify({calls:calls,state:st}))})")

    def test_logs_in_order_once_each_then_finishes_last(self):
        out = self.run_logger("L.add(e[0]);L.add(e[1]);L.add(e[1]);L.add(e[0]);L.add(e[2]);L.finish()")
        self.assertEqual([c["name"] for c in out["calls"]], ["set_workout_item_status", "log_sets", "log_sets", "finish_workout"])
        self.assertEqual([c["arguments"].get("performed_at") for c in out["calls"][1:3]], ["2026-10-26T15:03:00Z", "2026-10-26T15:08:00Z"])
        self.assertEqual(out["state"], {"finished": True, "logged": [1, 2, 5], "sets": 2, "failed": []})

    def test_end_with_nothing_done_still_finishes(self):
        out = self.run_logger("L.finish().then(function(){return L.finish()})")
        self.assertEqual([c["name"] for c in out["calls"]], ["finish_workout"])
        self.assertTrue(out["state"]["finished"])

    def test_a_failed_set_is_retried_once_and_then_blocks_finish(self):
        once = self.run_logger("L.add(e[1]);L.finish()", "function(n,a,i){return i===1?'error':null}")
        self.assertEqual([c["name"] for c in once["calls"]], ["log_sets", "log_sets", "finish_workout"])
        self.assertTrue(once["state"]["finished"])
        always = self.run_logger("L.add(e[1]);L.add(e[2]);L.finish()",
                                 "function(n,a){return a.sets&&a.sets[0].exercise==='Plank'?'error':null}")
        self.assertEqual([c["name"] for c in always["calls"]], ["log_sets", "log_sets", "log_sets"])  # no finish_workout
        self.assertEqual(always["state"]["failed"], [{"step": 5, "tool": "log_sets", "unknown": False}])
        self.assertFalse(always["state"]["finished"])

    def test_a_timed_out_set_is_not_resent(self):
        out = self.run_logger("L.add(e[1]);L.finish()", "function(n){return n==='log_sets'?'timeout':null}")
        self.assertEqual([c["name"] for c in out["calls"]], ["log_sets"])  # may have been saved: never duplicated
        self.assertEqual(out["state"]["failed"], [{"step": 2, "tool": "log_sets", "unknown": True}])


class GuidedAutoLogEndToEndTest(ServerCase):
    def test_done_sets_and_end_through_the_real_server(self):
        """The page's own call sequence for a partial workout, replayed through the MCP server it would talk to."""
        s = self.server(True)
        cfg = self.call(s, "guided_routine_timer", {"routine_id": self.routine(s)})["structuredContent"]
        sid = cfg["session_id"]
        # Steps: walk warm-up hold, bench 1, rest, bench 2, rest, plank. The user lets the walk run out, taps Done on
        # bench 1, skips bench 2, holds the plank early for 20 s, then ends. All within a few minutes of the start.
        started = s.c.execute("SELECT started_at FROM sessions WHERE id=?", (sid,)).fetchone()[0]
        out = node_run(
            f"var cfg={json.dumps(cfg)},t0=Date.parse({json.dumps(started)}),s=gPrep(cfg),calls=[];"
            "var plank=s.filter(function(x){return x.exercise==='Plank'})[0];"
            "var done=[gEntry(s[0],60,true,t0+60e3),gEntry(s[1],50,false,t0+120e3),gEntry(plank,20,false,t0+240e3)];"
            "var L=gLogger(function(n,a){calls.push({name:n,arguments:a});return Promise.resolve(" + json.dumps(ok_result("log_sets")) + ")},function(){return true});"
            "done.forEach(L.add);L.finish().then(function(){process.stdout.write(JSON.stringify(calls))})")
        names = [c["name"] for c in out]
        self.assertEqual(names, ["set_workout_item_status", "log_sets", "log_sets", "finish_workout"])
        for c in out:
            res = self.call(s, c["name"], c["arguments"])
            self.assertFalse(res.get("isError"), (c, res))
            verdict = node_run(f"process.stdout.write(JSON.stringify(gToolOk({json.dumps(c['name'])},{json.dumps(res)})))")
            self.assertTrue(verdict, (c, res))
        self.assertEqual(s.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0], "completed")
        rows = s.c.execute("SELECT e.canonical_name, s.reps, s.duration_s, s.performed_at FROM sets s JOIN exercises e ON e.id=s.exercise_id "
                           "WHERE s.session_id=? ORDER BY s.id", (sid,)).fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [("Barbell Bench Press", 8, None), ("Plank", None, 20)])
        self.assertEqual([r[3][:19] for r in rows], [c["arguments"]["performed_at"][:19] for c in out if c["name"] == "log_sets"])
        items = dict(s.c.execute("SELECT COALESCE(e.canonical_name, i.activity_name), i.status FROM workout_session_items i "
                                 "LEFT JOIN exercises e ON e.id=i.exercise_id WHERE i.session_id=?", (sid,)).fetchall())
        self.assertEqual(items["Warm-up walk"], "completed")
        self.assertEqual(items["Barbell Bench Press"], "completed")  # one set logged; the skipped second set is not
        self.assertEqual(items["Plank"], "completed")


if __name__ == "__main__":
    unittest.main()
