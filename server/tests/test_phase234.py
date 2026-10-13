import os,sys,tempfile,shutil,unittest
from datetime import datetime,timezone,timedelta
sys.path.insert(0,os.path.join(os.path.dirname(__file__),".."))
import db as dbm, core, coach, reports
NOW=datetime(2026,10,9,20,0,tzinfo=timezone.utc)   # Fri Oct 9 2026 13:00 PT
def L(c,ex,reps,w,day,n=3,unit="lb",rpe=None):
  return core.log_sets(c,[{"exercise":ex,"reps":r,"weight":w,"unit":unit,"rpe":rpe} for r in (reps if isinstance(reps,list) else [reps]*n)],performed_at=day+"T08:00:00")
class T(unittest.TestCase):
  def setUp(s): s.d=tempfile.mkdtemp(); s.c=dbm.connect(os.path.join(s.d,"t.db")); core.update_settings(s.c,{"unit":"lb","days_per_week":4,"goals":["strength","cardio","hiking"]})
  def tearDown(s): s.c.close(); shutil.rmtree(s.d)
  # ---- Phase 3: progressive overload ----
  def test_no_history_and_unknown_equipment(s):
    r=coach.suggest_next(s.c,"bench press",now=NOW); s.assertEqual(r["status"],"no_history"); s.assertEqual(r["equipment_status"],"unknown"); s.assertIn("equipment_note",r)
    s.assertEqual(coach.suggest_next(s.c,"push ups",now=NOW)["equipment_status"],"available")
  def test_add_reps(s):
    L(s.c,"bench press",[10,9,8],120,"2026-10-06"); r=coach.suggest_next(s.c,"卧推",now=NOW)
    s.assertEqual((r["status"],r["weight"],r["reps"]),("add_reps",120.0,11))
  def test_increase_weight_upper_lower(s):
    L(s.c,"bench press",12,120,"2026-10-06"); r=coach.suggest_next(s.c,"bench press",now=NOW); s.assertEqual((r["status"],r["weight"],r["reps"]),("increase_weight",125.0,8))
    L(s.c,"squat",12,100,"2026-10-07"); r=coach.suggest_next(s.c,"深蹲",now=NOW); s.assertEqual((r["status"],r["weight"]),("increase_weight",110.0))
  def test_high_rpe_blocks_increase(s):
    L(s.c,"bench press",12,120,"2026-10-06",rpe=10); s.assertEqual(coach.suggest_next(s.c,"bench press",now=NOW)["status"],"add_reps")
  def test_kg_increment(s):
    core.update_settings(s.c,{"unit":"kg"}); L(s.c,"deadlift",12,100,"2026-10-06",unit="kg")
    r=coach.suggest_next(s.c,"硬拉",now=NOW); s.assertEqual((r["weight"],r["unit"]),(105.0,"kg"))
  def test_deload_after_two_misses(s):
    L(s.c,"bench press",[7,6,6],135,"2026-10-02"); L(s.c,"bench press",[7,6,5],135,"2026-10-06")
    r=coach.suggest_next(s.c,"bench press",now=NOW); s.assertEqual(r["status"],"deload"); s.assertEqual(r["weight"],120.0)
  def test_reset_after_layoff(s):
    L(s.c,"bench press",10,120,"2026-09-01"); r=coach.suggest_next(s.c,"bench press",now=NOW); s.assertEqual((r["status"],r["weight"]),("reset",110.0))
  def test_bodyweight_and_custom_range(s):
    core.log_sets(s.c,[{"exercise":"push ups","reps":15,"count":3}],performed_at="2026-10-08T08:00:00")
    s.assertEqual(coach.suggest_next(s.c,"push ups",now=NOW)["reps"],16)
    L(s.c,"squat",8,200,"2026-10-07"); r=coach.suggest_next(s.c,"squat",target_rep_range="5-8",now=NOW); s.assertEqual(r["status"],"increase_weight"); s.assertEqual(r["reps"],5)
  def test_daily_plan(s):
    p=coach.daily_plan_context(s.c,now=NOW); s.assertEqual(p["suggested_focus"],"full_body"); s.assertFalse(p["equipment_known"]); s.assertTrue(p["notes"])
    L(s.c,"squat",8,100,"2026-10-09")
    p=coach.daily_plan_context(s.c,date="2026-10-09",now=NOW+timedelta(hours=1)); s.assertEqual(p["suggested_focus"],"upper"); s.assertFalse(p["muscle_recovery"]["legs"]["recovered"])
    s.assertTrue(all(not(set(x["muscles"])<=coach.LOWER) for x in p["candidate_exercises"]))
    core.update_settings(s.c,{"equipment":["dumbbell","bodyweight"]})
    p=coach.daily_plan_context(s.c,date="2026-10-09",now=NOW+timedelta(hours=1)); s.assertTrue(p["candidate_exercises"])
    s.assertTrue(all(x["equipment"] in("dumbbell","bodyweight","none") for x in p["candidate_exercises"]))
  def test_daily_plan_rest_and_hike(s):
    for d in("2026-10-06","2026-10-07","2026-10-08"): L(s.c,"push ups",[10],None,d)
    s.assertEqual(coach.daily_plan_context(s.c,date="2026-10-09",now=NOW)["suggested_focus"],"rest_or_mobility")
    s.assertEqual(coach.daily_plan_context(s.c,date="2026-10-10",now=NOW+timedelta(days=1))["suggested_focus"],"hike")
  # ---- activities / reports ----
  def test_activity_and_reports(s):
    core.log_activity(s.c,"hike",180,distance=8,elevation_gain=2000,performed_at="2026-10-03T09:00:00")
    L(s.c,"bench press",8,120,"2026-09-29"); L(s.c,"bench press",8,125,"2026-10-06"); L(s.c,"squat",8,100,"2026-10-07")
    w=reports.report(s.c,"week",anchor_date="2026-10-09",now=NOW)
    s.assertEqual(w["label"],"2026-10-05 – 2026-10-11"); s.assertEqual(w["summary"]["days_trained"],2); s.assertEqual(w["summary"]["sets"],6)
    s.assertEqual(w["vs_previous"]["days_trained"]["prev"],2); s.assertEqual({p["exercise"] for p in w["prs"]},{"Barbell Bench Press","Back Squat"})
    m=reports.report(s.c,"month",anchor_date="2026-10-09",now=NOW); s.assertEqual(m["label"],"2026-10"); s.assertEqual(m["summary"]["hikes"],1)
    s.assertEqual(m["summary"]["hike_distance"],8.0); s.assertEqual(m["summary"]["hike_elevation"],2000)
    q=reports.report(s.c,"quarter",anchor_date="2026-10-09",now=NOW); s.assertEqual(q["label"],"2026 Q4")
    y=reports.report(s.c,"year",anchor_date="2026-10-09",now=NOW); s.assertEqual(y["series"]["group_by"],"month")
    bp=[l for l in y["lifts"] if l["exercise"]=="Barbell Bench Press"][0]; s.assertEqual(bp["e1rm_change_pct"],4.2)
    a=reports.report(s.c,"all",now=NOW); s.assertEqual(a["milestones"]["sessions_total"],4); s.assertNotIn("vs_previous",a)
    with s.assertRaises(core.UserError): reports.report(s.c,"decade")
  def test_show_progress(s):
    for i,w in enumerate((100,105,110,115)): L(s.c,"squat",8,w,(datetime(2026,9,14)+timedelta(days=7*i)).date().isoformat())
    p=reports.show_progress(s.c,"squat",range="12w",now=NOW); s.assertEqual(len(p["points"]),4); s.assertEqual(p["stats"]["change_pct"],15.0); s.assertEqual(len(p["prs"]),4)
    o=reports.show_progress(s.c,metric="days",range="12w",now=NOW); s.assertEqual(o["stats"]["total"],4)
if __name__=="__main__": unittest.main(verbosity=2)
