"""Workout lifecycle and active-session snapshots."""
from datetime import datetime, timedelta, timezone
import json

import db as dbm

INACTIVITY = timedelta(minutes=15)
MODES = {"implicit", "routine", "conversational_plan"}
LB = 0.45359237


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
    data["items"] = [dict(x) for x in c.execute("""SELECT i.*,e.canonical_name FROM workout_session_items i
        LEFT JOIN exercises e ON e.id=i.exercise_id WHERE i.session_id=? ORDER BY CASE WHEN i.position<0 THEN 1 ELSE 0 END,i.position,i.id""", (session_id,))]
    data["completed_work"] = [dict(x) for x in c.execute("""SELECT x.id set_id,x.session_item_id,e.canonical_name exercise,
        x.reps,x.weight_kg,x.duration_s,x.distance_m,x.performed_at FROM sets x JOIN exercises e ON e.id=x.exercise_id
        WHERE x.session_id=? ORDER BY x.performed_at,x.id""", (session_id,))]
    data["activity"] = None if not (data.get("duration_min") or data.get("distance_km") or data.get("elevation_gain_m")) else {
        "session_item_id": data.get("activity_session_item_id"), "type": data.get("type"), "title": data.get("title"),
        "duration_min": data.get("duration_min"), "distance_km": data.get("distance_km"), "elevation_gain_m": data.get("elevation_gain_m"),
        "rpe": data.get("rpe"), "notes": data.get("notes")}
    data["next_unfinished_item"] = next((item for item in data["items"] if item["status"] == "pending"), None)
    return data


def _active_id(c):
    row = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    if not row:
        raise _error("no active workout")
    return row[0]


def _normalized_item(c, item):
    import routines
    return routines._normalize_items(c, [item])[0]


def _start_items(c, items):
    normalized = []
    import core
    for position, item in enumerate(items or [], 1):
        if not isinstance(item, dict) or not (item.get("exercise") or item.get("exercise_id") or item.get("activity_name")):
            raise _error(f"workout item {position} needs an exercise or activity_name")
        exercise_id = core._lookup_exercise(c, item.get("exercise_id") or item.get("exercise")) if (item.get("exercise") or item.get("exercise_id")) else None
        weight_kg = item.get("target_weight_kg")
        if weight_kg is None and item.get("target_weight") is not None:
            unit = item.get("unit", "kg")
            if unit not in ("lb", "kg"):
                raise _error("workout item unit must be lb or kg")
            weight_kg = float(item["target_weight"]) * (LB if unit == "lb" else 1)
        normalized.append({"source_routine_item_id": item.get("source_routine_item_id"),
            "position": item.get("position", position), "exercise_id": exercise_id, "activity_name": item.get("activity_name"),
            "target_sets": item.get("target_sets"), "target_reps": item.get("target_reps"), "target_weight_kg": weight_kg,
            "target_duration_s": item.get("target_duration_s"), "target_distance_m": item.get("target_distance_m"), "notes": item.get("notes")})
    positions = [item["position"] for item in normalized]
    if positions and sorted(positions) != list(range(1, len(positions) + 1)):
        raise _error("workout item positions must be unique and contiguous from 1")
    return sorted(normalized, key=lambda item: item["position"])


def _item(c, item_id, active_only=True):
    query = "SELECT * FROM workout_session_items WHERE id=?"
    args = [item_id]
    if active_only:
        query += " AND session_id=(SELECT id FROM sessions WHERE status='in_progress')"
    row = c.execute(query, args).fetchone()
    if not row:
        raise _error("no such item in the active workout")
    return row


def add_session_item(c, item):
    sid = _active_id(c); value = _normalized_item(c, item)
    position = c.execute("SELECT coalesce(max(position),0)+1 FROM workout_session_items WHERE session_id=? AND position>0", (sid,)).fetchone()[0]
    with dbm.transaction(c):
        item_id = c.execute("""INSERT INTO workout_session_items(session_id,position,exercise_id,activity_name,target_sets,target_reps,
            target_weight_kg,target_duration_s,target_distance_m,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, position, value["exercise_id"], value["activity_name"], value["target_sets"], value["target_reps"],
             value["target_weight_kg"], value["target_duration_s"], value["target_distance_m"], value["notes"])).lastrowid
    return {"status": "added", "item": next(x for x in _session(c, sid)["items"] if x["id"] == item_id)}


def replace_session_item(c, item_id, replacement):
    old = _item(c, item_id); value = _normalized_item(c, replacement)
    with dbm.transaction(c):
        c.execute("UPDATE workout_session_items SET position=?,status='replaced' WHERE id=?", (-item_id, item_id))
        new_id = c.execute("""INSERT INTO workout_session_items(session_id,position,exercise_id,activity_name,target_sets,target_reps,
            target_weight_kg,target_duration_s,target_distance_m,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (old["session_id"], old["position"], value["exercise_id"], value["activity_name"], value["target_sets"], value["target_reps"],
             value["target_weight_kg"], value["target_duration_s"], value["target_distance_m"], value["notes"])).lastrowid
        c.execute("UPDATE workout_session_items SET replaced_by_id=? WHERE id=?", (new_id, item_id))
    return {"status": "replaced", "replaced_item_id": item_id,
            "item": next(x for x in _session(c, old["session_id"])["items"] if x["id"] == new_id)}


def reorder_session_items(c, item_ids):
    sid = _active_id(c)
    visible = [row[0] for row in c.execute("SELECT id FROM workout_session_items WHERE session_id=? AND status!='replaced' ORDER BY position,id", (sid,))]
    if len(item_ids) != len(set(item_ids)) or set(item_ids) != set(visible):
        raise _error("item_ids must contain every non-replaced active-workout item exactly once")
    with dbm.transaction(c):
        for item_id in item_ids:
            c.execute("UPDATE workout_session_items SET position=? WHERE id=?", (-1000000-item_id, item_id))
        for position, item_id in enumerate(item_ids, 1):
            c.execute("UPDATE workout_session_items SET position=? WHERE id=?", (position, item_id))
    return {"status": "reordered", "items": [x for x in _session(c, sid)["items"] if x["status"] != "replaced"]}


def set_session_item_status(c, item_id, status):
    if status not in ("pending", "completed", "skipped"):
        raise _error("status must be pending|completed|skipped")
    row = _item(c, item_id)
    if row["status"] == "replaced":
        raise _error("a replaced item cannot change status")
    with dbm.transaction(c):
        c.execute("UPDATE workout_session_items SET status=? WHERE id=?", (status, item_id))
    return {"status": status, "item_id": item_id}


def move_set(c, set_id, target_session_id):
    row = c.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    target = c.execute("SELECT * FROM sessions WHERE id=?", (target_session_id,)).fetchone()
    if not row or not target or target["status"] == "abandoned":
        raise _error("set and non-abandoned target session are required")
    source_id = row["session_id"]
    if source_id == target_session_id:
        return {"status": "unchanged", "set_id": set_id, "session_id": target_session_id}
    with dbm.transaction(c):
        c.execute("UPDATE sets SET session_id=?,session_item_id=NULL WHERE id=?", (target_session_id, set_id))
        for sid in (source_id, target_session_id):
            _refresh_bounds(c, sid)
            from calories import estimate_session
            estimate_session(c, sid)
    return {"status": "moved", "set_id": set_id, "from_session_id": source_id, "session_id": target_session_id}


def _refresh_bounds(c, session_id):
    row = c.execute("SELECT min(performed_at),max(performed_at) FROM sets WHERE session_id=?", (session_id,)).fetchone()
    if row and row[0]:
        c.execute("UPDATE sessions SET started_at=?,last_activity_at=?,ended_at=CASE WHEN status='completed' THEN ? ELSE ended_at END WHERE id=?",
                  (row[0], row[1], row[1], session_id))


def merge_sessions(c, source_session_id, target_session_id, confirmed=False):
    if confirmed is not True:
        raise _error("explicit confirmation is required before merging workouts")
    if source_session_id == target_session_id:
        raise _error("source and target workouts must differ")
    source = c.execute("SELECT * FROM sessions WHERE id=?", (source_session_id,)).fetchone()
    target = c.execute("SELECT * FROM sessions WHERE id=?", (target_session_id,)).fetchone()
    if not source or not target or source["status"] != "completed" or target["status"] != "completed":
        raise _error("only completed workouts can be merged")
    lo, hi = sorted((source["started_at"], target["started_at"]))
    between = c.execute("SELECT count(*) FROM sessions WHERE status='completed' AND started_at>? AND started_at<?", (lo, hi)).fetchone()[0]
    if between:
        raise _error("only adjacent completed workouts can be merged")
    with dbm.transaction(c):
        offset = c.execute("SELECT coalesce(max(position),0) FROM workout_session_items WHERE session_id=? AND position>0", (target_session_id,)).fetchone()[0]
        items = c.execute("SELECT id FROM workout_session_items WHERE session_id=? AND status!='replaced' ORDER BY position,id", (source_session_id,)).fetchall()
        for position, item in enumerate(items, offset + 1):
            c.execute("UPDATE workout_session_items SET session_id=?,position=? WHERE id=?", (target_session_id, position, item[0]))
        c.execute("UPDATE sets SET session_id=?,session_item_id=NULL WHERE session_id=?", (target_session_id, source_session_id))
        c.execute("UPDATE hiit_workouts SET session_id=? WHERE session_id=?", (target_session_id, source_session_id))
        c.execute("""UPDATE sessions SET type=?,duration_min=coalesce(duration_min,0)+?,distance_km=CASE WHEN distance_km IS NULL AND ? IS NULL THEN NULL ELSE coalesce(distance_km,0)+coalesce(?,0) END,
            elevation_gain_m=CASE WHEN elevation_gain_m IS NULL AND ? IS NULL THEN NULL ELSE coalesce(elevation_gain_m,0)+coalesce(?,0) END WHERE id=?""",
            (target["type"] if target["type"] == source["type"] else "mixed", source["duration_min"] or 0,
             source["distance_km"], source["distance_km"], source["elevation_gain_m"], source["elevation_gain_m"], target_session_id))
        c.execute("DELETE FROM sessions WHERE id=?", (source_session_id,))
        _refresh_bounds(c, target_session_id)
        from calories import estimate_session
        estimate_session(c, target_session_id)
    return {"status": "merged", "source_session_id": source_session_id, "session_id": target_session_id,
            "workout": _session(c, target_session_id)}


def get_active(c, at=None):
    reconcile_active(c, at)
    row = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    return {"active_workout": _session(c, row[0]) if row else None}


def start_workout(c, *, mode, title=None, items=None, source_routine_id=None, idempotency_key=None,
                  session_weight=None, weight_unit="kg", planned_duration_min=None, at=None):
    if mode not in MODES:
        raise _error("mode must be implicit|routine|conversational_plan")
    if session_weight is not None and (not isinstance(session_weight, (int, float)) or isinstance(session_weight, bool) or session_weight <= 0):
        raise _error("session_weight must be greater than zero")
    if weight_unit not in ("lb", "kg"):
        raise _error("weight_unit must be lb or kg")
    if planned_duration_min is not None and (not isinstance(planned_duration_min, (int, float)) or planned_duration_min <= 0):
        raise _error("planned_duration_min must be greater than zero")
    session_weight_kg = None if session_weight is None else float(session_weight) * (LB if weight_unit == "lb" else 1)
    items = _start_items(c, items)
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
        sid = c.execute("""INSERT INTO sessions(started_at,type,title,source,status,source_routine_id,start_mode,last_activity_at,idempotency_key,
            session_weight_kg,planned_duration_min) VALUES(?,?,?,?, 'in_progress',?,?,?,?,?,?)""",
            (_iso(at), "strength", title, "chat", source_routine_id, mode, _iso(at), idempotency_key,
             session_weight_kg, planned_duration_min)).lastrowid
        for position, item in enumerate(items, 1):
            c.execute("""INSERT INTO workout_session_items(
                session_id,source_routine_item_id,position,exercise_id,activity_name,target_sets,target_reps,
                target_weight_kg,target_duration_s,target_distance_m,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (sid, item.get("source_routine_item_id"), item.get("position", position),
                item.get("exercise_id"), item.get("activity_name"), item.get("target_sets"), item.get("target_reps"),
                item.get("target_weight_kg"), item.get("target_duration_s"), item.get("target_distance_m"), item.get("notes")))
    return {"status": "started", **_session(c, sid)}


def session_for_log(c, at, workout_type, session_weight_kg=None, planned_duration_min=None, session_id=None):
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id,type,started_at,last_activity_at FROM sessions WHERE status='in_progress'").fetchone()
    if session_id is not None and (not row or row["id"] != session_id or at < _dt(row["started_at"])):
        raise _error(f"workout #{session_id} is not the active workout")  # log_target_rejection runs first
    # A backfilled entry predating the active workout cannot belong to it. Close
    # the newer workout at its own last activity, then create an independent one.
    if row and at < _dt(row["started_at"]):
        c.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?",
                  (row["last_activity_at"] or row["started_at"], row["id"]))
        row = None
    created = False
    if not row:
        result = start_workout(c, mode="implicit", session_weight=session_weight_kg, weight_unit="kg",
                               planned_duration_min=planned_duration_min, at=at)
        sid, old_type, created = result["session_id"], workout_type, True
    else:
        sid, old_type = row["id"], row["type"]
    combined = workout_type if not old_type or old_type == workout_type else "mixed"
    c.execute("""UPDATE sessions SET type=?,last_activity_at=?,session_weight_kg=coalesce(?,session_weight_kg),
        planned_duration_min=coalesce(?,planned_duration_min) WHERE id=?""",
        (combined, _iso(at), session_weight_kg, planned_duration_min, sid))
    return sid, created


def log_target_rejection(c, session_id, at):
    """Guard for log_sets(session_id=...): return None when sets logged at `at` would land in that workout (it is
    the active one and `at` is not before its start), else a rejection result. Nothing is written for a
    rejection: the sets are never redirected into another active workout and no new workout is started."""
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
        raise _error("session_id must be a positive integer")
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id,started_at FROM sessions WHERE status='in_progress'").fetchone()
    active_id = row["id"] if row else None
    if active_id == session_id and at >= _dt(row["started_at"]):
        return None
    workout = c.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
    if active_id == session_id:
        reason, why = "before_session_start", "performed_at is before that workout started"
    elif not workout:
        reason, why = "session_not_found", "that workout does not exist"
    else:
        reason, why = "session_not_active", f"that workout is no longer active ({workout['status']})"
    note = (f"Nothing saved: these sets were for workout #{session_id}, but {why}"
            + (f"; workout #{active_id} is active and was left unchanged." if active_id not in (None, session_id) else ".")
            + " The sets were not logged to any other workout.")
    return {"status": "rejected", "reason": reason, "logged": [], "session_id": session_id,
            "session_status": workout["status"] if workout else None, "active_session_id": active_id, "note": note}


def touch(c, session_id, at):
    c.execute("UPDATE sessions SET last_activity_at=? WHERE id=? AND status='in_progress'", (_iso(_dt(at)), session_id))


def finish_workout(c, at=None, session_id=None):
    """Finish the active workout. With session_id, finish it only if it is that workout: when another workout
    (or none) is active, the requested one already ended (e.g. closed after 15 minutes idle), so report it as
    already finished and leave whatever is active untouched."""
    if session_id is not None and (isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1):
        raise _error("session_id must be a positive integer")
    at = _dt(at)
    reconcile_active(c, at)
    row = c.execute("SELECT id,last_activity_at FROM sessions WHERE status='in_progress'").fetchone()
    if session_id is not None and (not row or row["id"] != session_id):
        return _already_finished(c, session_id, row["id"] if row else None)
    if not row:
        raise _error("no active workout")
    ended = _dt(row["last_activity_at"] or at) if _has_work(c, row["id"]) else at
    with dbm.transaction(c):
        c.execute("UPDATE sessions SET status='completed',ended_at=? WHERE id=?", (_iso(ended), row["id"]))
        c.execute("UPDATE workout_session_items SET status='skipped' WHERE session_id=? AND status='pending'", (row["id"],))
    from calories import estimate_session
    estimate_session(c, row["id"])
    return {"status": "completed", **_session(c, row["id"])}


def _already_finished(c, session_id, active_id):
    workout = _session(c, session_id)
    note = (f"Workout #{session_id} is not the active workout; it had already ended"
            + (f" ({workout['status']})" if workout else " (not found)") + ", so nothing was changed"
            + (f"; workout #{active_id} is still active." if active_id is not None else "."))
    return {"status": "already_finished", "session_id": session_id,
            "session_status": workout["status"] if workout else None,
            "active_session_id": active_id, "note": note, "workout": workout}


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
