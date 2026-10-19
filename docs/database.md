# Database and migrations

The server stores everything in one SQLite file. Its path is resolved in this order:

1. `--db PATH` passed to `server/server.py`.
2. The `WORKOUT_DB` environment variable.
3. `db.DEFAULT_DB`, which is `/workspace/vblife/workout-data/workout.db`.

## How migrations run

Migrations run automatically, in process, when the server opens the database. `db.connect()` calls `db.init()`,
which reads `schema_version` and applies each pending step in order (`_migrate_v2`, `_migrate_v3`, ...). Each
step runs in a single transaction: if anything fails, the whole step rolls back and the version is unchanged.
After migrating, `init()` refuses to continue unless the version equals `SCHEMA_VERSION`. This means an older
server build also refuses a database that a newer build has already migrated.

Offline tools that only read data (`render_timer_html.py --routine-id`) use `db.connect_readonly()`. It opens
the file with SQLite `mode=ro`, never migrates or writes, and fails clearly if the schema is not current.

## v3: routine types (HIIT routines)

This step is additive and idempotent:

- `workout_routines.routine_type` is `TEXT NOT NULL DEFAULT 'standard'` and must be `standard` or `hiit`.
- `workout_routines.rounds` is an optional circuit repeat count from 1 to 100.
- `workout_routines.round_options_json` is an optional JSON list used by the timer's round selector.
- `workout_routine_items.work_seconds` (1-3600) and `rest_seconds` (0-3600) are optional. When unset on a HIIT
  routine, reads use 40 and 20, or `target_duration_s` for work.

Each `ALTER TABLE` runs only if its column is missing. No column, row, or existing value is dropped or
rewritten.

The backfill runs once, during the 2 → 3 transition:

- A routine becomes `hiit` only when that is clear. Either its name contains "hiit" (case-insensitive), or it
  has items and every item is timed (`target_duration_s` > 0) with no sets, reps, weight, or distance.
- Every other routine stays `standard`.
- For routines classified as HIIT, `work_seconds` is copied from `target_duration_s`. `rest_seconds` is left
  unset, so reads use the 20 s default.
- A wrong guess is fixed with `update_routine(routine_id, routine_type=...)`. Because the backfill runs only
  during the version transition, that correction is never undone.

Before the first start on a new build, back up the live file. With the server stopped, copy `workout.db`
together with its `-wal`/`-shm` files, or use `sqlite3 workout.db ".backup workout.db.bak-v2"`. To roll back
to an older build, restore that backup; the older build rejects a v3 file.

## Tests never touch the live database

Every test creates its own temporary database. `server/tests/live_db_guard.py`, which `conftest.py` loads and
which some test modules import directly for plain `unittest` runs, adds two safety nets:

- It points `WORKOUT_DB` at a throwaway file. CLI subprocesses inherit it.
- It makes `db.connect` and `db.connect_readonly` raise if they resolve to `db.DEFAULT_DB`.
