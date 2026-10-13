"""Reusable workout routines and immutable active-workout snapshots."""
import json

import db as dbm
import workouts

LB = 0.45359237


def _error(message):
    from core import UserError
    return UserError(message)


def _exercise_id(c, value):
    import core
    return core._lookup_exercise(c, value)


def _normalize_items(c, items):
    if not items:
        raise _error("a routine needs at least one item")
    normalized = []
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict) or not (item.get("exercise") or item.get("exercise_id") or item.get("activity_name")):
            raise _error(f"routine item {position} needs an exercise or activity_name")
        exercise_id = _exercise_id(c, item.get("exercise_id") or item.get("exercise")) if (item.get("exercise") or item.get("exercise_id")) else None
        target_weight = item.get("target_weight")
        unit = item.get("unit", "kg")
        if unit not in ("lb", "kg"):
            raise _error("routine item unit must be lb or kg")
        target_weight_kg = None if target_weight is None else float(target_weight) * (LB if unit == "lb" else 1)
        normalized.append({
            "position": item.get("position", position), "exercise_id": exercise_id,
            "activity_name": item.get("activity_name"), "target_sets": item.get("target_sets"),
            "target_reps": item.get("target_reps"), "target_weight_kg": target_weight_kg,
            "target_duration_s": item.get("target_duration_s"), "target_distance_m": item.get("target_distance_m"),
            "notes": item.get("notes"),
        })
    positions = [x["position"] for x in normalized]
    if sorted(positions) != list(range(1, len(positions) + 1)):
        raise _error("routine item positions must be unique and contiguous from 1")
    return sorted(normalized, key=lambda x: x["position"])


def _items_from_session(c, session_id):
    session = c.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session or session["status"] != "completed":
        raise _error("source workout must be completed")
    rows = c.execute("""SELECT x.exercise_id,count(*) target_sets,max(x.reps) target_reps,
        max(x.weight_kg) target_weight_kg,max(x.duration_s) target_duration_s,max(x.distance_m) target_distance_m,
        min(x.id) first_id FROM sets x WHERE x.session_id=? GROUP BY x.exercise_id ORDER BY first_id""", (session_id,)).fetchall()
    if not rows:
        raise _error("source workout has no logged sets")
    return [{"position": i, **{k: row[k] for k in ("exercise_id", "target_sets", "target_reps", "target_weight_kg", "target_duration_s", "target_distance_m")}, "activity_name": None, "notes": None}
            for i, row in enumerate(rows, 1)]


def _routine(c, routine_id):
    row = c.execute("SELECT * FROM workout_routines WHERE id=?", (routine_id,)).fetchone()
    if not row:
        raise _error("no such routine")
    result = dict(row)
    result["routine_id"] = result.pop("id")
    result["items"] = []
    for item in c.execute("""SELECT i.*,e.canonical_name exercise FROM workout_routine_items i
        LEFT JOIN exercises e ON e.id=i.exercise_id WHERE i.routine_id=? ORDER BY i.position""", (routine_id,)):
        value = dict(item)
        value["routine_item_id"] = value.pop("id")
        result["items"].append(value)
    return result


def create_routine(c, name, items=None, description=None, estimated_duration_min=None, source_session_id=None):
    if not isinstance(name, str) or not name.strip():
        raise _error("routine name is required")
    normalized = _items_from_session(c, source_session_id) if source_session_id is not None else _normalize_items(c, items)
    now = dbm.now()
    with dbm.transaction(c):
        routine_id = c.execute("""INSERT INTO workout_routines(name,description,estimated_duration_min,source_session_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?)""", (name.strip(), description, estimated_duration_min, source_session_id, now, now)).lastrowid
        _insert_items(c, routine_id, normalized)
    return _routine(c, routine_id)


def _insert_items(c, routine_id, items):
    for item in items:
        c.execute("""INSERT INTO workout_routine_items(routine_id,position,exercise_id,activity_name,target_sets,target_reps,
            target_weight_kg,target_duration_s,target_distance_m,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (routine_id, item["position"], item.get("exercise_id"), item.get("activity_name"), item.get("target_sets"),
             item.get("target_reps"), item.get("target_weight_kg"), item.get("target_duration_s"), item.get("target_distance_m"), item.get("notes")))


def list_routines(c, include_archived=False):
    query = "SELECT id FROM workout_routines" + ("" if include_archived else " WHERE status='active'") + " ORDER BY updated_at DESC,id DESC"
    return {"routines": [_routine(c, row[0]) for row in c.execute(query)]}


def get_routine(c, routine_id):
    return _routine(c, routine_id)


def update_routine(c, routine_id, name=None, description=None, estimated_duration_min=None, items=None):
    current = _routine(c, routine_id)
    normalized = _normalize_items(c, items) if items is not None else None
    with dbm.transaction(c):
        c.execute("UPDATE workout_routines SET name=?,description=?,estimated_duration_min=?,updated_at=? WHERE id=?",
                  (name.strip() if isinstance(name, str) and name.strip() else current["name"],
                   description if description is not None else current["description"],
                   estimated_duration_min if estimated_duration_min is not None else current["estimated_duration_min"], dbm.now(), routine_id))
        if normalized is not None:
            c.execute("DELETE FROM workout_routine_items WHERE routine_id=?", (routine_id,))
            _insert_items(c, routine_id, normalized)
    return _routine(c, routine_id)


def set_archived(c, routine_id, archived=True):
    _routine(c, routine_id)
    c.execute("UPDATE workout_routines SET status=?,updated_at=? WHERE id=?", ("archived" if archived else "active", dbm.now(), routine_id))
    c.commit()
    return _routine(c, routine_id)


def start_routine(c, routine_id, idempotency_key=None, allow_archived=False, at=None):
    routine = _routine(c, routine_id)
    if routine["status"] == "archived" and not allow_archived:
        raise _error("routine is archived; restore it before starting")
    items = [{**item, "source_routine_item_id": item["routine_item_id"]} for item in routine["items"]]
    return workouts.start_workout(c, mode="routine", title=routine["name"], items=items,
                                  source_routine_id=routine_id, idempotency_key=idempotency_key,
                                  planned_duration_min=routine["estimated_duration_min"], at=at)
