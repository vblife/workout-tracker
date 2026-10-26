"""finish_workout(session_id): finish only the named workout; a mismatch is reported as already finished and
never touches the workout that is active now (PR #35 follow-up)."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import workouts

sys.path.insert(0, os.path.dirname(__file__))
from test_guided_apps_autolog import ServerCase, node_run, ok_result, page  # noqa: E402

T0 = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


class FinishWorkoutSessionIdTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.s = self.server(False)
        self.c = self.s.c

    def log(self, minutes):
        return core.log_sets(self.c, [{"exercise": "squat", "reps": 5, "weight": 100, "unit": "lb"}],
                             performed_at=iso(at(minutes)))["session_id"]

    def status(self, sid):
        return self.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0]

    def test_matching_session_id_finishes_it(self):
        sid = self.log(0)
        r = workouts.finish_workout(self.c, at=at(5), session_id=sid)
        self.assertEqual((r["status"], r["session_id"]), ("completed", sid))
        self.assertEqual(self.status(sid), "completed")

    def test_mismatch_leaves_the_other_active_workout_alone(self):
        runner = self.log(0)
        other = self.log(30)  # runner's workout auto-closed after 15 idle minutes; this one started from chat
        self.assertNotEqual(runner, other)
        self.assertEqual((self.status(runner), self.status(other)), ("completed", "in_progress"))
        r = workouts.finish_workout(self.c, at=at(31), session_id=runner)
        self.assertEqual(r["status"], "already_finished")
        self.assertEqual((r["session_id"], r["session_status"], r["active_session_id"]), (runner, "completed", other))
        self.assertIn("nothing was changed", r["note"])
        self.assertIn(f"#{other} is still active", r["note"])
        self.assertEqual(r["workout"]["session_id"], runner)
        self.assertEqual(self.status(other), "in_progress")
        self.assertEqual(workouts.get_active(self.c, at=at(31))["active_workout"]["session_id"], other)

    def test_named_session_already_finished_with_nothing_active(self):
        sid = self.log(0)
        workouts.finish_workout(self.c, at=at(5))
        r = workouts.finish_workout(self.c, at=at(6), session_id=sid)
        self.assertEqual((r["status"], r["session_status"], r["active_session_id"]), ("already_finished", "completed", None))
        r = workouts.finish_workout(self.c, at=at(6), session_id=sid + 999)
        self.assertEqual((r["status"], r["session_status"], r["workout"]), ("already_finished", None, None))

    def test_auto_closed_at_finish_time_counts_as_already_finished(self):
        sid = self.log(0)
        r = workouts.finish_workout(self.c, at=at(20), session_id=sid)  # reconcile closes it first
        self.assertEqual((r["status"], r["session_status"]), ("already_finished", "completed"))

    def test_omitted_session_id_keeps_old_behavior(self):
        self.log(0)
        other = self.log(30)
        r = workouts.finish_workout(self.c, at=at(31))
        self.assertEqual((r["status"], r["session_id"]), ("completed", other))
        with self.assertRaises(core.UserError):
            workouts.finish_workout(self.c, at=at(32))

    def test_rejects_non_integer_session_id(self):
        self.log(0)
        for bad in ("1", True, 0, -3):
            with self.assertRaises(core.UserError):
                workouts.finish_workout(self.c, at=at(1), session_id=bad)

    def test_mcp_tool_schema_and_call(self):
        tools = {t["name"]: t for t in self.s.handle({"method": "tools/list", "params": {}})["tools"]}
        schema = tools["finish_workout"]["inputSchema"]
        self.assertEqual(schema["properties"]["session_id"]["type"], "integer")
        self.assertEqual(schema["required"], [])
        self.assertIn("already_finished", tools["finish_workout"]["description"])
        sid = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}])["session_id"]
        self.c.execute("UPDATE sessions SET status='completed' WHERE id=?", (sid,)); self.c.commit()
        other = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}])["session_id"]
        res = self.call(self.s, "finish_workout", {"session_id": sid})
        self.assertFalse(res.get("isError"))
        self.assertEqual(res["structuredContent"]["status"], "already_finished")
        self.assertTrue(res["content"][0]["text"].startswith(f"Workout #{sid} is not the active workout"))
        self.assertEqual(self.status(other), "in_progress")
        self.assertTrue(node_run(f"process.stdout.write(JSON.stringify(gToolOk('finish_workout',{json.dumps(res)})))"))
        res = self.call(self.s, "finish_workout", {"session_id": other})
        self.assertEqual(res["structuredContent"]["status"], "completed")


class GuidedEndPassesSessionIdTest(ServerCase):
    def test_logger_finish_sends_session_id(self):
        calls = node_run(
            "var calls=[];var L=gLogger(function(n,a){calls.push({name:n,arguments:a});return Promise.resolve({structuredContent:{}})},"
            "gToolOk,{session_id:42});L.finish().then(function(){process.stdout.write(JSON.stringify(calls))})")
        self.assertEqual(calls, [{"name": "finish_workout", "arguments": {"session_id": 42}}])

    def test_runner_wires_cfg_session_id_into_logger(self):
        html = page()
        runner = html[html.index("function guidedRunner()"):]
        self.assertIn("session_id:cfg.session_id}):null", runner[:runner.index("function byStep")])

    def test_end_after_idle_close_does_not_finish_the_chat_workout(self):
        s = self.server(True)
        rid = self.call(s, "create_routine", {"name": "Push", "items": [
            {"exercise": "Barbell Bench Press", "target_sets": 1, "target_reps": 8}]})["structuredContent"]["routine_id"]
        cfg = self.call(s, "guided_routine_timer", {"routine_id": rid})["structuredContent"]
        runner = cfg["session_id"]
        # The runner sits idle past 15 minutes, so its workout closes; the user then logs from chat.
        s.c.execute("UPDATE sessions SET started_at=?,last_activity_at=? WHERE id=?",
                    ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", runner)); s.c.commit()
        other = self.call(s, "log_sets", {"sets": [{"exercise": "squat", "reps": 5}]})["structuredContent"]["session_id"]
        self.assertNotEqual(runner, other)
        out = node_run(
            f"var cfg={json.dumps(cfg)},calls=[];"
            "var L=gLogger(function(n,a){calls.push({name:n,arguments:a});return Promise.resolve({})},function(){return true},"
            "{session_id:cfg.session_id});L.finish().then(function(){process.stdout.write(JSON.stringify(calls))})")
        self.assertEqual(out, [{"name": "finish_workout", "arguments": {"session_id": runner}}])
        res = self.call(s, out[0]["name"], out[0]["arguments"])
        self.assertEqual(res["structuredContent"]["status"], "already_finished")
        self.assertEqual(res["structuredContent"]["active_session_id"], other)
        self.assertEqual(s.c.execute("SELECT status FROM sessions WHERE id=?", (other,)).fetchone()[0], "in_progress")
        self.assertTrue(node_run(f"process.stdout.write(JSON.stringify(gToolOk('finish_workout',{json.dumps(res)})))"))


if __name__ == "__main__":
    unittest.main()
