"""log_sets(session_id): log only into the named workout. When it is not the active one, nothing is saved and the
result is status=rejected; the sets never land in another active workout and no workout is started (PR #36
follow-up for the guided runner's per-set Done path)."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import workouts

sys.path.insert(0, os.path.dirname(__file__))
from test_guided_apps_autolog import ServerCase, node_run, page  # noqa: E402

T0 = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
SQUAT = [{"exercise": "squat", "reps": 5, "weight": 100, "unit": "lb"}]


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class LogSetsSessionIdTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.s = self.server(False)
        self.c = self.s.c

    def log(self, minutes, **kw):
        return core.log_sets(self.c, SQUAT, performed_at=iso(minutes), **kw)

    def status(self, sid):
        return self.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0]

    def count(self, sid=None):
        q, a = "SELECT count(*) FROM sets", ()
        if sid is not None:
            q, a = q + " WHERE session_id=?", (sid,)
        return self.c.execute(q, a).fetchone()[0]

    def sessions(self):
        return self.c.execute("SELECT count(*) FROM sessions").fetchone()[0]

    def test_matching_session_id_logs_into_it(self):
        sid = self.log(0)["session_id"]
        r = self.log(3, session_id=sid)
        self.assertEqual((r["status"], r["session_id"], r["new_session"]), ("logged", sid, False))
        self.assertEqual(self.count(sid), 2)

    def test_matching_session_item_is_completed(self):
        sid = workouts.start_workout(self.c, mode="conversational_plan", at=iso(0), items=[{"exercise": "squat", "target_sets": 1}])["session_id"]
        item = self.c.execute("SELECT id FROM workout_session_items WHERE session_id=?", (sid,)).fetchone()[0]
        r = core.log_sets(self.c, [dict(SQUAT[0], session_item_id=item)], performed_at=iso(1), session_id=sid)
        self.assertEqual((r["status"], r["session_id"]), ("logged", sid))
        self.assertEqual(self.c.execute("SELECT status FROM workout_session_items WHERE id=?", (item,)).fetchone()[0], "completed")

    def test_mismatch_rejects_and_leaves_the_other_workout_alone(self):
        runner = self.log(0)["session_id"]
        other = self.log(30)["session_id"]  # runner's workout closed after 15 idle minutes; this one is from chat
        self.assertNotEqual(runner, other)
        before = self.count(other)
        r = self.log(31, session_id=runner)
        self.assertEqual((r["status"], r["reason"], r["logged"]), ("rejected", "session_not_active", []))
        self.assertEqual((r["session_id"], r["session_status"], r["active_session_id"]), (runner, "completed", other))
        self.assertTrue(r["note"].startswith("Nothing saved"))
        self.assertIn(f"#{other} is active and was left unchanged", r["note"])
        self.assertEqual((self.count(other), self.count(runner)), (before, 1))
        self.assertEqual(self.status(other), "in_progress")

    def test_rejects_completed_or_abandoned_with_nothing_active_and_starts_nothing(self):
        sid = self.log(0)["session_id"]
        workouts.finish_workout(self.c, at=iso(5))
        n = self.sessions()
        r = self.log(6, session_id=sid)
        self.assertEqual((r["status"], r["reason"], r["session_status"], r["active_session_id"]),
                         ("rejected", "session_not_active", "completed", None))
        self.assertEqual(self.sessions(), n)
        abandoned = workouts.start_workout(self.c, mode="implicit", at=iso(10))["session_id"]
        workouts.abandon_workout(self.c, at=iso(11))
        r = self.log(12, session_id=abandoned)
        self.assertEqual((r["status"], r["session_status"]), ("rejected", "abandoned"))
        self.assertEqual(self.sessions(), n + 1)
        self.assertIsNone(workouts.get_active(self.c, at=iso(12))["active_workout"])

    def test_missing_session_is_rejected(self):
        n = self.sessions()
        r = self.log(0, session_id=999)
        self.assertEqual((r["status"], r["reason"], r["session_status"]), ("rejected", "session_not_found", None))
        self.assertEqual((self.sessions(), self.count()), (n, 0))

    def test_auto_closed_at_log_time_is_rejected(self):
        sid = self.log(0)["session_id"]
        r = self.log(20, session_id=sid)  # reconcile closes it first: >15 idle minutes
        self.assertEqual((r["status"], r["session_status"], r["active_session_id"]), ("rejected", "completed", None))
        self.assertEqual(self.count(), 1)

    def test_backdated_before_start_is_rejected_not_split(self):
        sid = self.log(10)["session_id"]
        r = self.log(5, session_id=sid)  # without session_id this would close #sid and start a new workout
        self.assertEqual((r["status"], r["reason"], r["active_session_id"]), ("rejected", "before_session_start", sid))
        self.assertEqual(self.status(sid), "in_progress")
        self.assertEqual(self.sessions(), 1)

    def test_rejection_wins_over_exercise_confirmation(self):
        sid = self.log(0)["session_id"]
        workouts.finish_workout(self.c, at=iso(1))
        r = core.log_sets(self.c, [{"exercise": "zzz unknown", "reps": 5}], performed_at=iso(2), session_id=sid)
        self.assertEqual(r["status"], "rejected")

    def test_omitted_session_id_keeps_old_behavior(self):
        self.log(0)
        other = self.log(30)["session_id"]
        r = self.log(31)
        self.assertEqual((r["status"], r["session_id"]), ("logged", other))

    def test_rejects_non_integer_session_id(self):
        for bad in ("1", True, 0, -3, 1.5):
            with self.assertRaises(core.UserError):
                self.log(0, session_id=bad)
        self.assertEqual(self.count(), 0)

    def test_mcp_tool_schema_and_call(self):
        tools = {t["name"]: t for t in self.s.handle({"method": "tools/list", "params": {}})["tools"]}
        schema = tools["log_sets"]["inputSchema"]
        self.assertEqual(schema["properties"]["session_id"]["type"], "integer")
        self.assertEqual(schema["required"], ["sets"])
        self.assertIn("rejected", tools["log_sets"]["description"])
        sid = core.log_sets(self.c, SQUAT)["session_id"]
        self.c.execute("UPDATE sessions SET status='completed' WHERE id=?", (sid,)); self.c.commit()
        other = core.log_sets(self.c, SQUAT)["session_id"]
        res = self.call(self.s, "log_sets", {"sets": SQUAT, "session_id": sid})
        self.assertFalse(res.get("isError"))
        self.assertEqual(res["structuredContent"]["status"], "rejected")
        self.assertTrue(res["content"][0]["text"].startswith("Nothing saved"))
        self.assertEqual(self.count(other), 1)
        # The runner's success check treats a rejection as not saved, so End will not finish the workout.
        self.assertFalse(node_run(f"process.stdout.write(JSON.stringify(gToolOk('log_sets',{json.dumps(res)})))"))
        text_only = {"content": res["content"]}
        self.assertFalse(node_run(f"process.stdout.write(JSON.stringify(gToolOk('log_sets',{json.dumps(text_only)})))"))
        res = self.call(self.s, "log_sets", {"sets": SQUAT, "session_id": other})
        self.assertEqual(res["structuredContent"]["status"], "logged")
        res = self.call(self.s, "log_sets", {"sets": SQUAT, "session_id": "x"})
        self.assertTrue(res["isError"])


class GuidedDonePassesSessionIdTest(ServerCase):
    def test_log_call_carries_session_id(self):
        calls = node_run(
            "var e={step:1,exercise:'squat',reps:5,done_at:'2026-10-26T15:00:00Z',session_item_id:7};"
            "process.stdout.write(JSON.stringify([gLogCall(e,42),gLogCall(e)]))")
        self.assertEqual(calls[0]["arguments"]["session_id"], 42)
        self.assertNotIn("session_id", calls[1]["arguments"])
        self.assertEqual(calls[0]["arguments"]["sets"][0]["session_item_id"], 7)

    def test_logger_done_sends_hooks_session_id(self):
        calls = node_run(
            "var calls=[];var L=gLogger(function(n,a){calls.push({name:n,arguments:a});"
            "return Promise.resolve(n==='log_sets'?{structuredContent:{status:'logged'}}:{structuredContent:{}})},gToolOk,{session_id:42});"
            "L.add({step:1,exercise:'squat',reps:5,done_at:'2026-10-26T15:00:00Z'});"
            "L.add({step:2,loggable:false,session_item_id:9});"
            "L.finish().then(function(){process.stdout.write(JSON.stringify(calls))})")
        self.assertEqual([c["name"] for c in calls], ["log_sets", "set_workout_item_status", "finish_workout"])
        self.assertEqual(calls[0]["arguments"]["session_id"], 42)
        self.assertEqual(calls[1]["arguments"], {"item_id": 9, "status": "completed"})
        self.assertEqual(calls[2]["arguments"], {"session_id": 42})

    def test_runner_wires_cfg_session_id_into_logger(self):
        html = page()
        self.assertIn("function add(e){var c=gLogCall(e,hooks.session_id)", html)
        runner = html[html.index("function guidedRunner()"):]
        self.assertIn("session_id:cfg.session_id}):null", runner[:runner.index("function byStep")])

    def test_done_after_idle_close_is_rejected_and_end_does_not_finish(self):
        s = self.server(True)
        rid = self.call(s, "create_routine", {"name": "Push", "items": [
            {"exercise": "Barbell Bench Press", "target_sets": 1, "target_reps": 8}]})["structuredContent"]["routine_id"]
        cfg = self.call(s, "guided_routine_timer", {"routine_id": rid})["structuredContent"]
        runner = cfg["session_id"]
        s.c.execute("UPDATE sessions SET started_at=?,last_activity_at=? WHERE id=?",
                    ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", runner)); s.c.commit()
        other = self.call(s, "log_sets", {"sets": [{"exercise": "squat", "reps": 5}]})["structuredContent"]["session_id"]
        self.assertNotEqual(runner, other)
        work = next(x for x in cfg["steps"] if x["kind"] != "rest")
        entry = {"step": 0, "exercise": work.get("exercise"), "reps": work.get("reps") or 8,
                 "session_item_id": work.get("session_item_id")}
        # Drive the real runner logger against the real server: the Done set is rejected (twice, with the retry),
        # so finish_workout is never called and the chat workout keeps only its own set.
        script = (f"var cfg={json.dumps(cfg)},calls=[];var L=gLogger(function(n,a){{calls.push({{name:n,arguments:a}});"
                  "return Promise.resolve(RESULTS.shift())},gToolOk,{session_id:cfg.session_id});"
                  f"L.add({json.dumps(entry)});L.finish().then(function(st){{process.stdout.write(JSON.stringify({{calls:calls,state:st}}))}})")
        first = node_run("var RESULTS=[{structuredContent:{status:'pending'}}];" + script.replace("RESULTS.shift()", "RESULTS[0]"))
        call = first["calls"][0]
        self.assertEqual((call["name"], call["arguments"]["session_id"]), ("log_sets", runner))
        res = self.call(s, call["name"], call["arguments"])
        self.assertEqual(res["structuredContent"]["status"], "rejected")
        self.assertEqual(res["structuredContent"]["active_session_id"], other)
        out = node_run(f"var RESULTS=[{json.dumps(res)},{json.dumps(res)}];" + script)
        self.assertEqual([c["name"] for c in out["calls"]], ["log_sets", "log_sets"])
        self.assertFalse(out["state"]["finished"])
        self.assertEqual(out["state"]["failed"], [{"step": 0, "tool": "log_sets", "unknown": False}])
        self.assertEqual(s.c.execute("SELECT count(*) FROM sets WHERE session_id=?", (other,)).fetchone()[0], 1)
        self.assertEqual(s.c.execute("SELECT status FROM sessions WHERE id=?", (other,)).fetchone()[0], "in_progress")


if __name__ == "__main__":
    unittest.main()
