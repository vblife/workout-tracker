"""Workout lifecycle and active-session snapshots."""
from datetime import datetime, timedelta, timezone
import json

import db as dbm

INACTIVITY = timedelta(minutes=15)
MODES = {"implicit", "routine", "conversational_plan"}


def _dt(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(message):
    from core import UserError
    return UserError(message)


def _has_work(c, session_id):
    if c.execute("SELECT 1 FROM sets WHERE session_id=? LIMIT 1", (session_id,)).fetchone():
        return True
    if c.execute("SELECT 1 FROM hiit_workouts WHERE session_id=? AND status='completed' LIMIT 1", (session_id,)).fetchone():
        return True
    row = c.execute("SELECT duration_min,distance_km FROM sessions WHERE id=?", (session_id,)).fetchone()
    return bool(row and (row["duration_min"] or row["distance_km"]))


def reconcile_active(c, at=None):
    at = _dt(at)
    row = c.execute("SELECT * FROM sessions WHERE status='in_progress'").fetchone()
    if not row:
        return {"active": None, "closed": None}
    last = _dt(row["last_activity_at"] or row["started_at"])
    if at - last <= INACTIVITY:
        return {"active": _session(c, row["id"]), "closed": None}
    has_work = _has_work(c, row["id"])
    status = "completed" if has_work else "abandoned"
    ended = last if has_work else last + INACTIVITY
    with dbm.transaction(c):
        c.execute("UPDATE sessions SET status=?,ended_at=? WHERE id=?", (status, _iso(ended), row["id"]))
        c.execute("UPDATE workout_session_items SET status='skipped' WHERE session_id=? AND status='pending'", (row["id"],))
    return {"active": None, "closed": {"session_id": row["id"], "status": status, "ended_at": _iso(ended)}}


def _session(c, session_id):
    row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["session_id"] = data.pop("id")
    data["calorie_assumptions"] = json.loads(data["calorie_assumptions"] or "[]")
    data["items"] = [dict(x) for x in c.execute("SELECT * FROM workout_session_items WHERE session_id=? ORDER BY position,id", (session_id,))]
    return data


def get_active(c, at=None):
    reconcile_active(c, at)
    row = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    return {"active_workout": _session(c, row[0]) if row else None}


def start_workout(c, *, mode, title=None, items=None, source_routine_id=None, idempotency_key=None, at=None):
    if mode not in MODES:
        raise _error("mode must be implicit|routine|conversational_plan")
    at = _dt(at)
    reconcile_active(c, at)
    if idempotency_key:
        existing = c.execute("SELECT id FROM sessions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            return {"status": "already_started", **_session(c, existing[0])}
    active = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    if active:
        raise _error("a workout is already active; resume, finish, or abandon it first")
    with dbm.transaction(c):
        sid = c.execute("""INSERT INTO sessions(started_at,type,title,source,status,source_routine_id,start_mode,last_activity_at,idempotency_key)
            VALUES(?,?,?,?, 'in_progress',?,?,?,?)""",
            (_iso(at), "strength", title, "chat", source_routine_id, mode, _iso(at), idempotency_key)).lastrowid
        for position, item in enumerate(items or [], 1):
            c.execute("""INSERT INTO workout_session_items(
                session_id,source_routine_item_id,position,exercise_id,activity_name,target_sets,target_reps,
                target_weight_kg,target_duration_s,target_distance_m,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (sid, item.get("source_routine_item_id"), item.get("position", position),
                item.get("exercise_id"), item.get("activity_name"), item.get("target_sets"), item.get("target_reps"),
                item.get("target_weight_kg"), item.get("target_duration_s"), item.get("target_distance_m"), item.get("notes")))
    return {"status": "started", **_session(c, sid)}


def session_for_log(c, at, workout_type):
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id,type,started_at,last_activity_at FROM sessions WHERE status='in_progress'").fetchone()
    # A backfilled entry predating the active workout cannot belong to it. Close
    # the newer workout at its own last activity, then create an independent one.
    if row and at < _dt(row["started_at"]):
        c.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?",
                  (row["last_activity_at"] or row["started_at"], row["id"]))
        row = None
    created = False
    if not row:
        result = start_workout(c, mode="implicit", at=at)
        sid, old_type, created = result["session_id"], workout_type, True
    else:
        sid, old_type = row["id"], row["type"]
    combined = workout_type if not old_type or old_type == workout_type else "mixed"
    c.execute("UPDATE sessions SET type=?,last_activity_at=? WHERE id=?", (combined, _iso(at), sid))
    return sid, created


def touch(c, session_id, at):
    c.execute("UPDATE sessions SET last_activity_at=? WHERE id=? AND status='in_progress'", (_iso(_dt(at)), session_id))


def finish_workout(c, at=None):
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id,last_activity_at FROM sessions WHERE status='in_progress'").fetchone()
    if not row:
        raise _error("no active workout")
    ended = _dt(row["last_activity_at"] or at) if _has_work(c, row["id"]) else at
    with dbm.transaction(c):
        c.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?", (_iso(ended), row["id"]))
        c.execute("UPDATE workout_session_items SET status='skipped' WHERE session_id=? AND status='pending'", (row["id"],))
    from calories import estimate_session
    estimate_session(c, row["id"])
    return {"status": "completed", **_session(c, row["id"])}


def abandon_workout(c, at=None):
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    if not row:
        raise _error("no active workout")
    with dbm.transaction(c):
        c.execute("UPDATE sessions SET status='abandoned',ended_at=? WHERE id=?", (_iso(at), row["id"]))
        c.execute("UPDATE workout_session_items SET status='skipped' WHERE session_id=? AND status='pending'", (row["id"],))
    return {"status": "abandoned", **_session(c, row["id"])}
