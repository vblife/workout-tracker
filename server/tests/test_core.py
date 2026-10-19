import os,sys,tempfile,unittest
sys.path.insert(0,os.path.join(os.path.dirname(__file__),".."))
import db as dbm, core
from normalize import norm
class T(unittest.TestCase):
  def setUp(s):
    s.d=tempfile.mkdtemp(); s.c=dbm.connect(os.path.join(s.d,"t.db"))
  def tearDown(s):
    s.c.close(); import shutil; shutil.rmtree(s.d)
  def test_seed(s):
    s.assertGreaterEqual(s.c.execute("select count(*) from exercises").fetchone()[0],75)
    s.assertEqual(s.c.execute("select version from schema_version").fetchone()[0],3)
    for t in("exercises","aliases","sessions","sets","hiit_workouts","settings","schema_version"):
      s.c.execute(f"select * from {t} limit 1")
  def test_norm(s):
    for a in("push ups","Push-Ups","PUSHUPS","pushup"): s.assertEqual(norm(a),"pushup")
    s.assertEqual(norm("臥推"),norm("卧推"))
  def test_resolve(s):
    cases={"push ups":"Push-Up","bench press":"Barbell Bench Press","卧推":"Barbell Bench Press","臥推":"Barbell Bench Press",
           "squat":"Back Squat","深蹲":"Back Squat","硬拉":"Deadlift","引体向上":"Pull-Up","RDL":"Romanian Deadlift","徒步":"Hiking","benchpres":"Barbell Bench Press"}
    for q,want in cases.items():
      r=core.resolve_one(s.c,q); s.assertEqual(r["status"],"matched",q); s.assertEqual(r["canonical"],want,q)
    s.assertNotEqual(core.resolve_one(s.c,"zercher carry")["status"],"matched")
  def test_bodyweight_pr(s):
    s.assertTrue(core.log_sets(s.c,[{"exercise":"push ups","reps":10}])["logged"][0]["is_pr"])
    s.assertFalse(core.log_sets(s.c,[{"exercise":"俯卧撑","reps":8}])["logged"][0]["is_pr"])
  def test_log_and_pr_units(s):
    r=core.log_sets(s.c,[{"exercise":"bench press","reps":8,"weight":120,"unit":"lb"}])
    x=r["logged"][0]; s.assertEqual(x["weight"],120.0); s.assertTrue(x["is_pr"]); s.assertEqual(x["e1rm"],152.0)
    r=core.log_sets(s.c,[{"exercise":"卧推","reps":8,"weight":110}]); s.assertFalse(r["logged"][0]["is_pr"]); s.assertEqual(r["session_id"],1)
    s.assertAlmostEqual(s.c.execute("select weight_kg from sets where id=1").fetchone()[0],54.431,2)
  def test_unknown_is_atomic(s):
    r=core.log_sets(s.c,[{"exercise":"push ups","reps":10},{"exercise":"zercher carry","reps":5}])
    s.assertEqual(r["status"],"needs_confirmation"); s.assertEqual(s.c.execute("select count(*) from sets").fetchone()[0],0)
  def test_alias_flow(s):
    s.assertNotEqual(core.resolve_one(s.c,"胸推")["status"],"matched")
    core.add_alias(s.c,"Machine Chest Press","胸推"); s.assertEqual(core.resolve_one(s.c,"胸推")["canonical"],"Machine Chest Press")
    with s.assertRaises(core.UserError): core.add_alias(s.c,"Deadlift","卧推")
  def test_count_delete_session(s):
    r=core.log_sets(s.c,[{"exercise":"squat","reps":8,"weight":100,"count":3}]); s.assertEqual([x["set_index"] for x in r["logged"]],[1,2,3])
    core.delete_set(s.c); s.assertEqual(len(core.list_recent(s.c)["sets"]),2)
    core.delete_set(s.c); d=core.delete_set(s.c); s.assertTrue(d["session_removed"])
    with s.assertRaises(core.UserError): core.delete_set(s.c)
  def test_session_gap(s):
    a=core.log_sets(s.c,[{"exercise":"squat","reps":5,"weight":100}],performed_at="2026-10-01T08:00:00")
    b=core.log_sets(s.c,[{"exercise":"squat","reps":5,"weight":100}],performed_at="2026-10-01T08:20:00")
    d=core.log_sets(s.c,[{"exercise":"squat","reps":5,"weight":100}],performed_at="2026-10-01T18:00:00")
    s.assertNotEqual(a["session_id"],b["session_id"]); s.assertNotEqual(b["session_id"],d["session_id"])
  def test_settings_onboarding(s):
    s.assertEqual(core.get_context(s.c)["onboarding_required"],[])
    with s.assertRaises(core.UserError): core.update_settings(s.c,{"unit":"stone"})
    r=core.update_settings(s.c,{"unit":"kg","days_per_week":5,"goals":["strength","cardio","hiking"]})
    s.assertEqual(r["onboarding_required"],[])
    x=core.log_sets(s.c,[{"exercise":"squat","reps":8,"weight":100,"unit":"lb"}])["logged"][0]
    s.assertEqual((x["weight"],x["unit"]),(100.0,"lb"))
    s.assertEqual(core.list_recent(s.c)["sets"][0]["weight"],45.4)
  def test_stats(s):
    for d,w in(("2026-09-01",100),("2026-09-08",105),("2026-09-09",110)):
      core.log_sets(s.c,[{"exercise":"squat","reps":8,"weight":w,"unit":"lb"}],performed_at=d+"T09:00:00")
    q=core.query_stats(s.c,"e1rm","深蹲",group_by="week")
    s.assertEqual([p["period"] for p in q["series"]],["2026-08-31","2026-09-07"]); s.assertEqual(q["series"][1]["e1rm"],139.3)
    v=core.query_stats(s.c,"volume","squat",from_date="2026-09-08",to_date="2026-09-08",group_by="day"); s.assertEqual(v["series"][0]["volume"],840.0)
  def test_stats_use_configured_timezone(s):
    core.update_settings(s.c,{"timezone":"Asia/Tokyo"})
    core.log_sets(s.c,[{"exercise":"squat","reps":5}],performed_at="2026-10-01T15:30:00Z")
    q=core.query_stats(s.c,"reps","squat",group_by="day")
    s.assertEqual(q["series"][0]["period"],"2026-10-02")
  def test_hiit(s):
    defaults=core.hiit_config(); s.assertEqual((defaults["rounds"],defaults["segments"][0]["seconds"],defaults["segments"][1]["seconds"],defaults["warmup_s"],defaults["cooldown_s"]),(20,40,20,10,60))
    h=core.create_hiit(s.c,name="Tabata",rounds=8,segments=[{"kind":"work","seconds":20,"exercise":"Burpee"},{"kind":"rest","seconds":10}],warmup_s=0,cooldown_s=0)
    s.assertEqual(h["planned_seconds"],230)
    r=core.log_hiit(s.c,h["hiit_id"],8,230); s.assertEqual(r["status"],"completed")
    s.assertEqual(core.log_hiit(s.c,h["hiit_id"],8,230)["status"],"already_logged")
    s.assertGreaterEqual(core.get_context(s.c)["days_trained_this_week"],1)
    with s.assertRaises(core.UserError): core.create_hiit(s.c,rounds=0)
if __name__=="__main__": unittest.main(verbosity=2)
