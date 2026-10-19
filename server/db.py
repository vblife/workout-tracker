import sqlite3,json,os
from contextlib import contextmanager
from datetime import datetime,timezone
from normalize import norm
import seed_data
SCHEMA_VERSION=3
DEFAULT_DB="/workspace/vblife/workout-data/workout.db"
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
  init(c)
  import workouts
  workouts.reconcile_active(c)
  return c

@contextmanager
def transaction(c):
  """Run a unit of work atomically, nesting safely through a savepoint."""
  nested=c.in_transaction
  if nested: c.execute("SAVEPOINT workout_tx")
  else: c.execute("BEGIN IMMEDIATE")
  try:
    yield c
  except Exception:
    if nested:
      c.execute("ROLLBACK TO workout_tx"); c.execute("RELEASE workout_tx")
    else: c.rollback()
    raise
  else:
    if nested: c.execute("RELEASE workout_tx")
    else: c.commit()

def _version(c):
  r=c.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
  return r[0] if r else 0

def _execute_script_transactionally(c, script):
  for statement in script.split(";"):
    statement=statement.strip()
    if statement: c.execute(statement)

def _migrate_v2(c):
  t=now()
  with transaction(c):
    _execute_script_transactionally(c,"""
CREATE TABLE workout_routines(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, estimated_duration_min REAL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','archived')),
  source_session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE workout_routine_items(
  id INTEGER PRIMARY KEY, routine_id INTEGER NOT NULL REFERENCES workout_routines(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, exercise_id INTEGER REFERENCES exercises(id), activity_name TEXT,
  target_sets INTEGER, target_reps INTEGER, target_weight_kg REAL, target_duration_s INTEGER,
  target_distance_m REAL, notes TEXT, UNIQUE(routine_id,position));
""")
    for definition in (
      "status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN('in_progress','completed','abandoned'))",
      "source_routine_id INTEGER REFERENCES workout_routines(id) ON DELETE SET NULL",
      "start_mode TEXT NOT NULL DEFAULT 'implicit' CHECK(start_mode IN('implicit','routine','conversational_plan'))",
      "last_activity_at TEXT", "idempotency_key TEXT",
      "session_weight_kg REAL", "planned_duration_min REAL", "activity_session_item_id INTEGER",
      "estimated_calories REAL", "calorie_confidence TEXT", "calorie_weight_kg REAL",
      "calorie_duration_min REAL", "calorie_met REAL", "calorie_version TEXT", "calorie_assumptions TEXT"
    ):
      c.execute("ALTER TABLE sessions ADD COLUMN "+definition)
    c.execute("UPDATE sessions SET status='completed',start_mode='implicit',last_activity_at=coalesce(ended_at,started_at)")
    c.execute("ALTER TABLE sets ADD COLUMN session_item_id INTEGER REFERENCES workout_session_items(id) ON DELETE SET NULL")
    _execute_script_transactionally(c,"""
CREATE UNIQUE INDEX one_active_workout ON sessions(status) WHERE status='in_progress';
CREATE UNIQUE INDEX sessions_idempotency ON sessions(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE workout_session_items(
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_routine_item_id INTEGER REFERENCES workout_routine_items(id) ON DELETE SET NULL,
  position INTEGER NOT NULL, exercise_id INTEGER REFERENCES exercises(id), activity_name TEXT,
  target_sets INTEGER, target_reps INTEGER, target_weight_kg REAL, target_duration_s INTEGER,
  target_distance_m REAL, notes TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','completed','skipped','replaced')),
  replaced_by_id INTEGER REFERENCES workout_session_items(id) ON DELETE SET NULL,
  UNIQUE(session_id,position));
CREATE TABLE goals(
  id INTEGER PRIMARY KEY, domain TEXT NOT NULL CHECK(domain IN('workout','nutrition')),
  period TEXT NOT NULL CHECK(period IN('day','week','month')), metric TEXT NOT NULL,
  semantics TEXT NOT NULL CHECK(semantics IN('minimum','maximum','exact','range')),
  target_value REAL, lower_value REAL, upper_value REAL, unit TEXT NOT NULL, display_precision INTEGER NOT NULL DEFAULT 0,
  origin TEXT NOT NULL DEFAULT 'user' CHECK(origin IN('user','recommended')), recommendation_version TEXT,
  assumptions_json TEXT, effective_start_date TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX goals_active_period ON goals(active,domain,period,metric);
CREATE TABLE body_weight(
  id INTEGER PRIMARY KEY, measured_at TEXT NOT NULL, input_value REAL NOT NULL CHECK(input_value>0),
  input_unit TEXT NOT NULL CHECK(input_unit IN('lb','kg')), weight_kg REAL NOT NULL CHECK(weight_kg>0),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX body_weight_time ON body_weight(measured_at);
CREATE TABLE nutrition_entries(
  id INTEGER PRIMARY KEY, consumed_at TEXT NOT NULL, capture_timezone TEXT NOT NULL, local_date TEXT NOT NULL,
  calories_kcal REAL, protein_g REAL, carbohydrates_g REAL, fat_g REAL, fiber_g REAL,
  saturated_fat_g REAL, sugar_g REAL, sodium_mg REAL,
  estimation_method TEXT NOT NULL CHECK(estimation_method IN('usda_hybrid','model_only')),
  confidence TEXT NOT NULL CHECK(confidence IN('higher','moderate','rough')),
  usda_contributed INTEGER NOT NULL DEFAULT 0, estimator_version TEXT NOT NULL,
  source_components INTEGER, uncertainty_flags_json TEXT,
  confirmed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK(calories_kcal IS NOT NULL OR protein_g IS NOT NULL OR carbohydrates_g IS NOT NULL OR fat_g IS NOT NULL OR fiber_g IS NOT NULL OR saturated_fat_g IS NOT NULL OR sugar_g IS NOT NULL OR sodium_mg IS NOT NULL));
CREATE INDEX nutrition_local_date ON nutrition_entries(local_date,consumed_at);
""")
    c.execute("UPDATE schema_version SET version=2")
def _columns(c,table): return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}

# A routine is backfilled as HIIT only when that is clear: its name says HIIT, or every item is timed
# (target_duration_s) with no sets, reps, weight or distance. Anything else stays 'standard'; a wrong
# guess is fixed with update_routine(routine_type=...).
HIIT_BACKFILL_SQL="""UPDATE workout_routines SET routine_type='hiit' WHERE routine_type='standard' AND (
  lower(name) LIKE '%hiit%' OR (
    EXISTS(SELECT 1 FROM workout_routine_items i WHERE i.routine_id=workout_routines.id) AND
    NOT EXISTS(SELECT 1 FROM workout_routine_items i WHERE i.routine_id=workout_routines.id AND (
      i.target_duration_s IS NULL OR i.target_duration_s<=0 OR i.target_sets IS NOT NULL OR i.target_reps IS NOT NULL
      OR i.target_weight_kg IS NOT NULL OR i.target_distance_m IS NOT NULL))))"""

def _migrate_v3(c):
  """Additive and idempotent: routine type, circuit rounds and per-item HIIT work/rest seconds.
  No column or row is dropped or rewritten; existing values are only copied into the new columns."""
  with transaction(c):
    cols=_columns(c,"workout_routines")
    for name,definition in (
      ("routine_type","routine_type TEXT NOT NULL DEFAULT 'standard' CHECK(routine_type IN('standard','hiit'))"),
      ("rounds","rounds INTEGER CHECK(rounds IS NULL OR rounds BETWEEN 1 AND 100)"),
      ("round_options_json","round_options_json TEXT")):
      if name not in cols: c.execute("ALTER TABLE workout_routines ADD COLUMN "+definition)
    cols=_columns(c,"workout_routine_items")
    for name,definition in (
      ("work_seconds","work_seconds INTEGER CHECK(work_seconds IS NULL OR work_seconds BETWEEN 1 AND 3600)"),
      ("rest_seconds","rest_seconds INTEGER CHECK(rest_seconds IS NULL OR rest_seconds BETWEEN 0 AND 3600)")):
      if name not in cols: c.execute("ALTER TABLE workout_routine_items ADD COLUMN "+definition)
    if _version(c)<3:
      c.execute(HIIT_BACKFILL_SQL)
      c.execute("""UPDATE workout_routine_items SET work_seconds=target_duration_s WHERE work_seconds IS NULL
        AND target_duration_s BETWEEN 1 AND 3600 AND routine_id IN(SELECT id FROM workout_routines WHERE routine_type='hiit')""")
      c.execute("UPDATE schema_version SET version=3")

def connect_readonly(path=None):
  """Open an existing database read-only (no migration, no reconcile). Used by offline tools such as
  render_timer_html.py --routine-id; the server applies migrations on startup."""
  path=path or os.environ.get("WORKOUT_DB") or DEFAULT_DB
  if not os.path.exists(path): raise FileNotFoundError(f"workout database not found: {path}")
  c=sqlite3.connect("file:"+os.path.abspath(path)+"?mode=ro",uri=True); c.row_factory=sqlite3.Row
  v=_version(c)
  if v!=SCHEMA_VERSION:
    c.close(); raise RuntimeError(f"workout database is at schema version {v}, expected {SCHEMA_VERSION}; start the workout-bot server once to migrate it")
  return c

def init(c):
  c.executescript(SCHEMA)
  if c.execute("SELECT count(*) FROM schema_version").fetchone()[0]==0:
    c.execute("INSERT INTO schema_version VALUES(1)")
  c.commit()
  if _version(c)<2: _migrate_v2(c)
  if _version(c)<3: _migrate_v3(c)
  if _version(c)!=SCHEMA_VERSION: raise RuntimeError(f"unsupported schema version {_version(c)}")
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
