import os,sys,tempfile,shutil,unittest
sys.path.insert(0,os.path.join(os.path.dirname(__file__),".."))
import db as dbm, core, reports

class OptionalDistance(unittest.TestCase):
  def setUp(s):
    s.d=tempfile.mkdtemp(); s.c=dbm.connect(os.path.join(s.d,"t.db"))
  def tearDown(s):
    s.c.close(); shutil.rmtree(s.d)
  def test_log_activity_without_distance_or_elevation(s):
    r=core.log_activity(s.c,"cardio",90,title="pickleball")
    s.assertIsNone(r["distance"]); s.assertIsNone(r["elevation_gain"])
    row=s.c.execute("SELECT distance_km,elevation_gain_m FROM sessions WHERE id=?",(r["session_id"],)).fetchone()
    s.assertIsNone(row["distance_km"]); s.assertIsNone(row["elevation_gain_m"])
  def test_update_activity_without_distance(s):
    sid=core.log_activity(s.c,"cardio",90,title="pickleball")["session_id"]
    r=core.update_activity(s.c,session_id=sid,duration_min=90,title="Pickleball",notes="doubles")
    s.assertIsNone(r["distance"]); s.assertIsNone(r["distance_unit"])
    s.assertIsNone(r["elevation_gain"]); s.assertIsNone(r["elevation_unit"])
    row=s.c.execute("SELECT distance_km,elevation_gain_m,title FROM sessions WHERE id=?",(sid,)).fetchone()
    s.assertIsNone(row["distance_km"]); s.assertIsNone(row["elevation_gain_m"]); s.assertEqual(row["title"],"Pickleball")
  def test_update_hike_keeps_existing_distance(s):
    sid=core.log_activity(s.c,"hike",60,distance=5,distance_unit="km",elevation_gain=300,elevation_unit="m")["session_id"]
    r=core.update_activity(s.c,session_id=sid,duration_min=70,distance_unit="km",elevation_unit="m")
    s.assertAlmostEqual(r["distance"],5); s.assertAlmostEqual(r["elevation_gain"],300)
  def test_merge_without_distance_stays_null(s):
    import workouts
    a=core.log_activity(s.c,"cardio",30,performed_at="2026-10-01T08:00:00")["session_id"]
    b=core.log_activity(s.c,"cardio",40,performed_at="2026-10-03T08:00:00")["session_id"]
    s.assertNotEqual(a,b)
    s.c.execute("UPDATE sessions SET status='completed' WHERE id IN(?,?)",(a,b)); s.c.commit()
    workouts.merge_sessions(s.c,a,b,confirmed=True)
    row=s.c.execute("SELECT duration_min,distance_km,elevation_gain_m FROM sessions WHERE id=?",(b,)).fetchone()
    s.assertEqual(row["duration_min"],70); s.assertIsNone(row["distance_km"]); s.assertIsNone(row["elevation_gain_m"])
  def test_reports_handle_missing_distance(s):
    core.log_activity(s.c,"cardio",90,title="pickleball",performed_at="2026-10-06T08:00:00")
    core.log_activity(s.c,"hike",60,performed_at="2026-10-08T08:00:00")
    r=reports.report(s.c,"week",anchor_date="2026-10-08")
    s.assertEqual(r["summary"]["hikes"],1); s.assertEqual(r["summary"]["distance"],0)
