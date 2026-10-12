import sqlite3,json,os
from datetime import datetime,timezone
from normalize import norm
import seed_data
SCHEMA_VERSION=1
DEFAULT_DB="/home/box/workout/workout.db"
SCHEMA="""
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exercises(
  id INTEGER PRIMARY KEY, canonical_name TEXT UNIQUE NOT NULL, name_zh TEXT,
  category TEXT NOT NULL CHECK(category IN('strength','cardio','hiit','mobility','hike')),
  muscle_groups TEXT, equipment TEXT, is_bodyweight INTEGER NOT NULL DEFAULT 0,
  tracking TEXT NOT NULL DEFAULT 'reps_weight' CHECK(tracking IN('reps_weight','reps','duration','distance')),
  increment_kg REAL, created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS aliases(
  alias_norm TEXT PRIMARY KEY, alias_raw TEXT NOT NULL,
  exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
  lang TEXT, source TEXT NOT NULL DEFAULT 'seed', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_alias_ex ON aliases(exercise_id);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, type TEXT, title TEXT,
  duration_min REAL, distance_km REAL, elevation_gain_m REAL, rpe REAL, notes TEXT,
  source TEXT NOT NULL DEFAULT 'chat');
CREATE TABLE IF NOT EXISTS sets(
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  exercise_id INTEGER NOT NULL REFERENCES exercises(id), set_index INTEGER, performed_at TEXT NOT NULL,
  reps INTEGER, weight_kg REAL, input_weight REAL, input_unit TEXT, duration_s INTEGER, distance_m REAL,
  rpe REAL, is_warmup INTEGER NOT NULL DEFAULT 0, e1rm_kg REAL, raw_text TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sets_ex_time ON sets(exercise_id, performed_at);
CREATE INDEX IF NOT EXISTS idx_sets_time ON sets(performed_at);
CREATE TABLE IF NOT EXISTS hiit_workouts(
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL, name TEXT,
  config_json TEXT NOT NULL, planned_seconds INTEGER, completed_rounds INTEGER, total_seconds INTEGER,
  status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN('planned','completed','aborted')),
  created_at TEXT NOT NULL, completed_at TEXT);
"""
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def connect(path=None):
  path=path or os.environ.get("WORKOUT_DB") or DEFAULT_DB
  os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
  c=sqlite3.connect(path); c.row_factory=sqlite3.Row
  c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA journal_mode=WAL")
  init(c); return c
def init(c):
  c.executescript(SCHEMA)
  if c.execute("SELECT count(*) FROM schema_version").fetchone()[0]==0:
    c.execute("INSERT INTO schema_version VALUES(?)",(SCHEMA_VERSION,))
  if c.execute("SELECT count(*) FROM exercises").fetchone()[0]==0: seed(c)
  c.commit()
def add_alias_row(c,ex_id,alias,lang=None,source="seed"):
  n=norm(alias)
  if not n: return False
  cur=c.execute("INSERT OR IGNORE INTO aliases(alias_norm,alias_raw,exercise_id,lang,source,created_at) VALUES(?,?,?,?,?,?)",
                (n,alias,ex_id,lang,source,now()))
  return cur.rowcount==1
def lang_of(s): return "zh" if any('\u4e00'<=ch<='\u9fff' for ch in s) else "en"
def seed(c):
  t=now()
  for can,zh,cat,mus,eq,bw,trk,inc,als in seed_data.EXERCISES:
    eid=c.execute("INSERT INTO exercises(canonical_name,name_zh,category,muscle_groups,equipment,is_bodyweight,tracking,increment_kg,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (can,zh,cat,json.dumps(mus),eq,bw,trk,inc,t)).lastrowid
    for a in [can,zh,*als]: add_alias_row(c,eid,a,lang_of(a))
