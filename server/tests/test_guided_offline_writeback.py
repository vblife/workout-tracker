"""Offline guided runner: rest-button copy, the Send to Workout Bot handoff, on-device persistence, and
log_guided_result writing the Done sets back into the runner's own workout (also after an idle close)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core
import db as dbm
import render_timer_html as r
import routines
import server as mcp_server
import workouts

sys.path.insert(0, os.path.dirname(__file__))
import live_db_guard  # noqa: E402,F401  (also under plain unittest: never open the live database)

MARK = "WORKOUT_BOT_GUIDED "

# Minimal DOM/browser stub: runs the rendered offline page's scripts under node and drives window.__guided.
HARNESS = r"""
const scripts=JSON.parse(process.env.SCRIPTS),store=JSON.parse(process.env.STORE||"{}"),els={};
function el(id){return{id,textContent:"",hidden:false,disabled:false,checked:true,className:"",value:"",style:{},firstChild:{style:{}},innerHTML:"",
 appendChild(){},setAttribute(k,v){this[k]=v},focus(){},select(){}}}
function $(id){return els[id]||(els[id]=el(id))}
global.window=global;global.parent=global;
global.document={getElementById:$,createElement:el,body:el("body"),documentElement:{lang:"",classList:{add(){}}},addEventListener(){},visibilityState:"visible",title:""};
global.location={search:"",hash:"",href:"file:///runner.html"};
global.localStorage={getItem:k=>k in store?store[k]:null,setItem:(k,v)=>{store[k]=String(v)},removeItem:k=>{delete store[k]}};
let clip=null;
Object.defineProperty(global,"navigator",{value:{clipboard:{writeText:t=>{clip=t;return Promise.resolve()}}},configurable:true});
global.addEventListener=()=>{};global.setInterval=()=>0;
let T=Number(process.env.T0);Date.now=()=>T;global.performance={now:()=>T};
for(const s of scripts)eval(s);
const g=window.__guided,out={};
(async()=>{
 if(process.env.RUN){g.toggle();T+=11000;g.tick();
  for(let i=0;i<100&&g.state().phase!=="finished";i++){const s=g.steps[g.state().idx];
   if(s.kind==="rest"){out.restLabel=$("doneBtn").textContent;out.next=$("next").textContent;T+=1000;g.done()}
   else if(s.duration_s){T+=(s.duration_s+1)*1000;g.tick()}
   else{out.workLabel=$("doneBtn").textContent;T+=60000;g.done()}}}
 await new Promise(r=>setTimeout(r,10));
 out.state=g.state();out.sendHidden=$("sendBtn").hidden;out.send=$("sendBtn").textContent;out.msg=$("msg").textContent;
 if(!out.sendHidden&&$("sendBtn").onclick){$("sendBtn").onclick();await new Promise(r=>setTimeout(r,10))}
 out.clip=clip;out.store=store;console.log(JSON.stringify(out))})();
"""


def template():
    with open(os.path.join(os.path.dirname(mcp_server.__file__), "ui", "timer.html"), encoding="utf-8") as f:
        return f.read()


def run_page(config, run=True, store=None, t0=None):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    html = r.render(config, template())
    env = {**os.environ, "SCRIPTS": json.dumps(re.findall(r"<script>(.*?)</script>", html, re.S)),
           "STORE": json.dumps(store or {}), "T0": str(t0 if t0 is not None else 1_793_000_000_000)}
    if run:
        env["RUN"] = "1"
    out = subprocess.run([node, "-e", HARNESS], check=True, capture_output=True, text=True, env=env).stdout
    return json.loads(out)


def marker_json(text):
    line = next(x for x in text.splitlines() if x.startswith(MARK))
    return json.loads(line[len(MARK):])


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = dbm.connect(os.path.join(self.tmp.name, "w.db"))
        self.rid = routines.create_routine(self.c, "Upper", items=[
            {"activity_name": "Warm-up walk", "target_duration_s": 60},
            {"exercise": "bench press", "target_sets": 2, "target_reps": 8, "target_weight": 60, "rest_seconds": 60},
            {"exercise": "plank", "target_duration_s": 30}])["routine_id"]

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def start(self, lang="en"):
        return routines.guided_routine_timer(self.c, self.rid, lang=lang)

    def started_at(self, sid):
        return workouts._dt(self.c.execute("SELECT started_at FROM sessions WHERE id=?", (sid,)).fetchone()[0])

    def entries(self, out, minutes=(5, 8, 12)):
        """Done entries the page would send: warm-up (activity), both bench sets, plank, at minutes after start."""
        base, steps = self.started_at(out["session_id"]), [s for s in out["steps"] if s["kind"] != "rest"]
        done = []
        for step, m in zip(steps, (2,) + tuple(minutes)):
            done.append({k: step.get(k) for k in ("step", "exercise", "label", "reps", "weight", "unit", "duration_s",
                                                   "distance_m", "is_warmup", "loggable", "session_item_id")}
                        | {"done_at": workouts._iso(base + timedelta(minutes=m))})
        return done

    def sets(self, sid):
        return self.c.execute("SELECT exercise_id,reps,performed_at FROM sets WHERE session_id=? ORDER BY performed_at",
                              (sid,)).fetchall()

    def age(self, sid, hours=1):
        """Move a still-active workout's start back so Done times minutes after it are in the past, not the future."""
        old = workouts._iso(workouts._dt() - timedelta(hours=hours))
        self.c.execute("UPDATE sessions SET started_at=?,last_activity_at=? WHERE id=?", (old, old, sid))
        self.c.commit()

    def item_status(self, sid):
        return dict(self.c.execute("SELECT id,status FROM workout_session_items WHERE session_id=?", (sid,)).fetchall())

    def idle_close(self, sid, hours=2):
        """The user trained offline (nothing logged): age the workout so the 15-minute rule closes it."""
        old = workouts._iso(workouts._dt() - timedelta(hours=hours))
        self.c.execute("UPDATE sessions SET started_at=?,last_activity_at=? WHERE id=?", (old, old, sid))
        self.c.commit()
        workouts.reconcile_active(self.c)
        return self.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0]


class LogGuidedResultTest(DbCase):
    def test_writes_done_sets_at_done_at_and_finishes(self):
        out = self.start()
        self.age(out["session_id"])
        res = routines.log_guided_result(self.c, out["session_id"], self.entries(out))
        self.assertEqual((res["status"], len(res["logged"]), res["session_status"], res["finished"]),
                         ("logged", 3, "completed", True))
        rows = self.sets(out["session_id"])
        base = self.started_at(out["session_id"])
        self.assertEqual([workouts._dt(x[2]) for x in rows], [base + timedelta(minutes=m) for m in (5, 8, 12)])
        items = dict(self.c.execute("SELECT id,status FROM workout_session_items WHERE session_id=?", (out["session_id"],)).fetchall())
        self.assertEqual(items[out["steps"][0]["session_item_id"]], "completed")  # loggable:false warm-up

    def test_idle_closed_offline_workout_is_reopened_and_gets_the_sets(self):
        # Charles's case: the page logged nothing, so the workout was abandoned 15 minutes in.
        out = self.start()
        self.assertEqual(self.idle_close(out["session_id"]), "abandoned")
        with self.assertRaises(Exception):  # the old path: log_sets into the closed workout is refused
            r_ = core.log_sets(self.c, [{"exercise": "bench press", "reps": 8}], session_id=out["session_id"])
            if r_.get("status") != "logged":
                raise RuntimeError(r_.get("note"))
        res = routines.log_guided_result(self.c, out["session_id"], self.entries(out), ended="finished")
        self.assertEqual((res["status"], len(res["logged"]), res["reopened"], res["session_status"]),
                         ("logged", 3, True, "completed"))
        self.assertEqual(len(self.sets(out["session_id"])), 3)
        ended = self.c.execute("SELECT ended_at FROM sessions WHERE id=?", (out["session_id"],)).fetchone()[0]
        self.assertEqual(workouts._dt(ended), self.started_at(out["session_id"]) + timedelta(minutes=12))
        self.assertIsNone(self.c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone())

    def test_long_rest_inside_the_run_does_not_split_the_workout(self):
        out = self.start()
        self.idle_close(out["session_id"])
        res = routines.log_guided_result(self.c, out["session_id"], self.entries(out, minutes=(5, 40, 70)))
        self.assertEqual((res["status"], len(res["logged"])), ("logged", 3))
        self.assertEqual(self.c.execute("SELECT COUNT(DISTINCT session_id) FROM sets").fetchone()[0], 1)

    def test_sending_twice_is_idempotent(self):
        out = self.start()
        self.age(out["session_id"])
        done = self.entries(out)
        routines.log_guided_result(self.c, out["session_id"], done)
        again = routines.log_guided_result(self.c, out["session_id"], done)
        self.assertEqual((again["status"], again["logged"], again["already_logged"], again["reopened"]),
                         ("already_logged", [], 3, False))
        self.assertEqual(len(self.sets(out["session_id"])), 3)

    def test_other_active_workout_saves_nothing(self):
        out = self.start()
        self.idle_close(out["session_id"])
        other = core.log_sets(self.c, [{"exercise": "squat", "reps": 5}])
        res = routines.log_guided_result(self.c, out["session_id"], self.entries(out))
        self.assertEqual((res["status"], res["reason"], res["active_session_id"]),
                         ("rejected", "other_workout_active", other["session_id"]))
        self.assertEqual(self.sets(out["session_id"]), [])
        self.assertEqual(self.c.execute("SELECT status FROM sessions WHERE id=?", (other["session_id"],)).fetchone()[0], "in_progress")

    def test_deliberately_finished_or_abandoned_workout_is_not_reopened(self):
        # #38 follow-up 1: only an idle close reopens; a stale replay must not revive a workout ended on purpose.
        for end in ("finish", "abandon"):
            out = self.start()
            sid = out["session_id"]
            if end == "finish":
                workouts.finish_workout(self.c, session_id=sid)
            else:
                workouts.abandon_workout(self.c)
            self.age(sid, hours=48)  # the result is replayed two days later
            done = self.entries(out)
            status = self.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0]
            res = routines.log_guided_result(self.c, sid, done)
            self.assertEqual((res["status"], res["reason"], res["session_status"]), ("rejected", "session_closed", status))
            self.assertEqual(self.sets(sid), [])
            self.assertEqual(self.c.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0], status)
            self.assertIsNone(self.c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone())

    def test_replay_after_reopen_and_finish_does_not_reopen_again(self):
        out = self.start()
        sid = out["session_id"]
        self.idle_close(sid)
        done = self.entries(out)
        self.assertTrue(routines.log_guided_result(self.c, sid, done[:2])["reopened"])
        # The workout is now finished by log_guided_result itself, not idle-closed: new entries are refused.
        res = routines.log_guided_result(self.c, sid, done)
        self.assertEqual((res["status"], res["reason"]), ("rejected", "session_closed"))
        self.assertEqual(len(self.sets(sid)), 1)
        # An exact replay of what was already saved is still a harmless no-op.
        again = routines.log_guided_result(self.c, sid, done[:2])
        self.assertEqual((again["status"], again["reopened"]), ("already_logged", False))

    def test_reopen_keeps_intentional_skips(self):
        # #38 follow-up 2: only the items the idle close skipped go back to pending.
        out = self.start()
        sid = out["session_id"]
        items = [row[0] for row in self.c.execute(
            "SELECT id FROM workout_session_items WHERE session_id=? ORDER BY position", (sid,))]
        warmup, bench, plank = items
        workouts.set_session_item_status(self.c, plank, "skipped")  # the user skipped the plank on purpose
        self.assertEqual(self.idle_close(sid), "abandoned")
        self.assertEqual(self.item_status(sid), {warmup: "skipped", bench: "skipped", plank: "skipped"})
        res = routines.log_guided_result(self.c, sid, self.entries(out)[:1], finish=False)  # warm-up only
        self.assertEqual((res["reopened"], res["session_status"]), (True, "in_progress"))
        self.assertEqual(self.item_status(sid), {warmup: "completed", bench: "pending", plank: "skipped"})

    def test_future_done_at_is_pinned_to_now(self):
        # #38 follow-up 3: a clock-skewed Done time after now is saved as now, never in the future.
        out = self.start()
        sid = out["session_id"]
        before = workouts._dt()
        done = self.entries(out, minutes=(30, 60, 90))  # the workout started just now, so these are in the future
        res = routines.log_guided_result(self.c, sid, done)
        after = workouts._dt()
        self.assertEqual((res["status"], len(res["logged"]), res["times_adjusted"]), ("logged", 3, 3))
        for row in self.sets(sid):
            self.assertTrue(before <= workouts._dt(row[2]) <= after)
        ended = workouts._dt(self.c.execute("SELECT ended_at FROM sessions WHERE id=?", (sid,)).fetchone()[0])
        self.assertLessEqual(ended, after)

    def test_schema_tracks_idle_closes(self):
        self.assertEqual(self.c.execute("SELECT version FROM schema_version").fetchone()[0], 4)
        out = self.start()
        sid = out["session_id"]
        self.idle_close(sid)
        self.assertEqual(self.c.execute("SELECT closed_by_inactivity FROM sessions WHERE id=?", (sid,)).fetchone()[0], 1)
        self.assertEqual({r[0] for r in self.c.execute(
            "SELECT skipped_by_inactivity FROM workout_session_items WHERE session_id=?", (sid,))}, {1})

    def test_unknown_exercise_saves_nothing(self):
        out = self.start()
        done = self.entries(out)
        done[2]["exercise"] = "zzqx mystery lift"
        res = routines.log_guided_result(self.c, out["session_id"], done)
        self.assertEqual(res["status"], "needs_confirmation")
        self.assertEqual(self.sets(out["session_id"]), [])
        self.assertEqual(self.c.execute("SELECT status FROM sessions WHERE id=?", (out["session_id"],)).fetchone()[0], "in_progress")

    def test_validates_inputs(self):
        out = self.start()
        for kwargs, msg in (({"session_id": 0, "completed": []}, "session_id"), ({"session_id": out["session_id"], "completed": {}}, "completed"),
                            ({"session_id": 999, "completed": []}, "does not exist"),
                            ({"session_id": out["session_id"], "completed": [], "ended": "x"}, "ended")):
            with self.assertRaisesRegex(core.UserError, msg):
                routines.log_guided_result(self.c, **kwargs)


class OfflinePageTest(DbCase):
    def config(self, out):
        return {**out, "label": out["name"]}

    def test_rest_button_and_up_next_say_next_set(self):
        out = self.start(lang="zh")
        page = run_page(self.config(out), t0=int(self.started_at(out["session_id"]).timestamp() * 1000))
        self.assertEqual((page["workLabel"], page["restLabel"]), ("\u5b8c\u6210", "\u4e0b\u4e00\u7ec4"))  # 下一组 on the rest step's main button
        self.assertTrue(page["next"].startswith("\u4e0b\u4e00\u7ec4: "), page["next"])
        en = run_page(self.config(self.start_other()), t0=1_793_000_000_000)
        self.assertEqual((en["workLabel"], en["restLabel"]), ("Done", "Next set"))
        self.assertTrue(en["next"].startswith("Next set: "), en["next"])

    def start_other(self):
        self.c.execute("UPDATE sessions SET status='completed',ended_at=started_at WHERE status='in_progress'")
        self.c.commit()
        return routines.guided_routine_timer(self.c, self.rid, lang="en")

    def test_page_has_no_bot_text_or_summary_block(self):
        html = template()
        for gone in ('id="forBot"', 'id="summary"', "tellBot", "forBot:"):
            self.assertNotIn(gone, html)
        self.assertIn('id="sendBtn"', html)

    def test_send_result_round_trips_into_log_guided_result(self):
        out = self.start(lang="zh")
        t0 = int(self.started_at(out["session_id"]).timestamp() * 1000)
        page = run_page(self.config(out), t0=t0)
        self.assertEqual(page["state"]["phase"], "finished")
        self.assertFalse(page["sendHidden"])
        self.assertEqual(page["send"], "\u53d1\u9001\u7ed9 Workout Bot")
        sent = marker_json(page["clip"])
        self.assertEqual((sent["workout_bot"], sent["session_id"], sent["ended"]), ("guided_routine", out["session_id"], "finished"))
        s = mcp_server.Server(os.path.join(self.tmp.name, "w.db"))
        self.addCleanup(s.c.close)
        res = s.handle({"method": "tools/call", "params": {"name": "log_guided_result", "arguments": {
            "session_id": sent["session_id"], "completed": sent["completed"], "ended": sent["ended"]}}})
        self.assertFalse(res.get("isError"), res)
        sc = res["structuredContent"]
        self.assertEqual((sc["status"], sc["session_status"]), ("logged", "completed"))
        self.assertEqual(len(sc["logged"]), len([e for e in sent["completed"] if e.get("loggable") is not False]))
        self.assertIn(f"workout #{out['session_id']}", res["content"][0]["text"])

    def test_result_is_kept_on_the_device_and_shown_again(self):
        out = self.start()
        t0 = int(self.started_at(out["session_id"]).timestamp() * 1000)
        first = run_page(self.config(out), t0=t0)
        key = f"workout-bot-guided:{out['session_id']}"
        self.assertIn(key, first["store"])
        again = run_page(self.config(out), run=False, store=first["store"], t0=t0 + 3_600_000)
        self.assertEqual((again["state"]["phase"], again["state"]["ended"]), ("finished", "finished"))
        self.assertEqual(again["state"]["completed"], first["state"]["completed"])
        self.assertFalse(again["sendHidden"])
        self.assertEqual(marker_json(again["clip"]), marker_json(first["clip"]))

    def test_reload_mid_workout_resumes_paused_with_done_sets(self):
        out = self.start()
        store = {f"workout-bot-guided:{out['session_id']}": json.dumps(
            {"v": 1, "idx": 3, "ended": "", "completed": [{"step": 1, "exercise": "Barbell Bench Press", "reps": 8}]})}
        page = run_page(self.config(out), run=False, store=store)
        self.assertEqual((page["state"]["phase"], page["state"]["idx"], len(page["state"]["completed"])), ("paused", 3, 1))


class DeliveryTest(unittest.TestCase):
    def test_render_refuses_an_auto_logging_run(self):
        with self.assertRaisesRegex(ValueError, "ui_auto_log"):
            r.normalize_config({"mode": "guided", "ui_auto_log": True, "steps": [{"label": "x", "reps": 5}]})
        r.normalize_config({"mode": "guided", "ui_auto_log": False, "steps": [{"label": "x", "reps": 5}]})

    def test_tool_is_listed_with_the_runner_marker(self):
        with tempfile.TemporaryDirectory() as d:
            s = mcp_server.Server(os.path.join(d, "w.db"))
            try:
                tools = {t["name"]: t for t in s.handle({"method": "tools/list", "params": {}})["tools"]}
            finally:
                s.c.close()
        self.assertIn("WORKOUT_BOT_GUIDED", tools["log_guided_result"]["description"])
        self.assertIn("log_guided_result", tools["guided_routine_timer"]["description"])


if __name__ == "__main__":
    unittest.main()
