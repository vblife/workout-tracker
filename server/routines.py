"""Reusable workout routines and immutable active-workout snapshots."""
import json
import re

import db as dbm
import workouts

LB = 0.45359237
ROUTINE_TYPES = ("standard", "hiit")
DEFAULT_WORK_SECONDS = 40
DEFAULT_REST_SECONDS = 20
MAX_ROUNDS = 100
GUIDED_DEFAULT_REST_SECONDS = 90  # C1: rest after a standard-routine set when the item has no rest_seconds
MAX_GUIDED_STEPS = 2000


def _error(message):
    from core import UserError
    return UserError(message)


def _exercise_id(c, value):
    import core
    return core._lookup_exercise(c, value)


def _seconds(value, name, minimum):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 3600:
        raise _error(f"{name} must be a whole number of seconds from {minimum} to 3600")
    return value


def _routine_type(value):
    if value not in ROUTINE_TYPES:
        raise _error(f"routine_type must be one of: {', '.join(ROUTINE_TYPES)} (got {value!r})")
    return value


def _rounds_fields(rounds, round_options):
    """Validate optional circuit rounds and round options; returns (rounds, round_options or None)."""
    if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= MAX_ROUNDS):
        raise _error(f"rounds must be a whole number from 1 to {MAX_ROUNDS}")
    if round_options is None or round_options == []:
        return rounds, None
    if not isinstance(round_options, list) or not 2 <= len(round_options) <= 6 or len(set(round_options)) != len(round_options) \
            or any(isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= MAX_ROUNDS for x in round_options):
        raise _error(f"round_options must be 2-6 distinct whole numbers from 1 to {MAX_ROUNDS}")
    options = sorted(round_options)
    if rounds is None:
        rounds = options[0]
    elif rounds not in options:
        raise _error(f"rounds ({rounds}) must be one of round_options {options}")
    return rounds, options


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
            "work_seconds": _seconds(item.get("work_seconds"), "work_seconds", 1),
            "rest_seconds": _seconds(item.get("rest_seconds"), "rest_seconds", 0),
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
    options = result.pop("round_options_json", None)
    result["round_options"] = json.loads(options) if options else None
    hiit = result["routine_type"] == "hiit"
    result["items"] = []
    for item in c.execute("""SELECT i.*,e.canonical_name exercise,e.name_zh exercise_zh FROM workout_routine_items i
        LEFT JOIN exercises e ON e.id=i.exercise_id WHERE i.routine_id=? ORDER BY i.position""", (routine_id,)):
        value = dict(item)
        value["routine_item_id"] = value.pop("id")
        if hiit:  # effective HIIT timing; unset values use the defaults
            if value["work_seconds"] is None:
                value["work_seconds"] = value["target_duration_s"] if value["target_duration_s"] and value["target_duration_s"] <= 3600 else DEFAULT_WORK_SECONDS
            if value["rest_seconds"] is None:
                value["rest_seconds"] = DEFAULT_REST_SECONDS
        result["items"].append(value)
    return result


def hiit_segments(routine, lang="en"):
    """Timer segments for a HIIT routine: one work interval per item (in order) with that item's rest after it."""
    if routine["routine_type"] != "hiit":
        raise _error(f"routine {routine['routine_id']} is a standard routine; use update_routine with routine_type 'hiit' to time it")
    segments = []
    for item in routine["items"]:
        name = (item.get("exercise_zh") if lang == "zh" else None) or item.get("exercise") or item.get("activity_name")
        segments.append({"kind": "work", "seconds": item["work_seconds"], "exercise": name})
        if item["rest_seconds"]:
            segments.append({"kind": "rest", "seconds": item["rest_seconds"]})
    return segments


def _check_type_fields(routine_type, items, rounds, round_options):
    if routine_type != "hiit":
        if rounds is not None or round_options:
            raise _error("rounds and round_options apply only to hiit routines")
        # Standard items may carry rest_seconds (rest after each set in the guided runner); work_seconds stays HIIT-only.
        if any(x.get("work_seconds") is not None for x in items or []):
            raise _error("work_seconds applies only to hiit routines; standard items use target_duration_s for timed holds")


def _hiit_defaults(items):
    for item in items:
        if item.get("work_seconds") is None:
            duration = item.get("target_duration_s")
            item["work_seconds"] = duration if isinstance(duration, int) and 1 <= duration <= 3600 else DEFAULT_WORK_SECONDS
        if item.get("rest_seconds") is None:
            item["rest_seconds"] = DEFAULT_REST_SECONDS
    return items


def create_routine(c, name, items=None, description=None, estimated_duration_min=None, source_session_id=None,
                   routine_type="standard", rounds=None, round_options=None):
    if not isinstance(name, str) or not name.strip():
        raise _error("routine name is required")
    _routine_type(routine_type)
    normalized = _items_from_session(c, source_session_id) if source_session_id is not None else _normalize_items(c, items)
    _check_type_fields(routine_type, normalized, rounds, round_options)
    rounds, options = _rounds_fields(rounds, round_options)
    if routine_type == "hiit":
        _hiit_defaults(normalized)
    now = dbm.now()
    with dbm.transaction(c):
        routine_id = c.execute("""INSERT INTO workout_routines(name,description,estimated_duration_min,source_session_id,created_at,updated_at,
            routine_type,rounds,round_options_json) VALUES(?,?,?,?,?,?,?,?,?)""", (name.strip(), description, estimated_duration_min,
            source_session_id, now, now, routine_type, rounds, json.dumps(options) if options else None)).lastrowid
        _insert_items(c, routine_id, normalized)
    return _routine(c, routine_id)


def _insert_items(c, routine_id, items):
    for item in items:
        c.execute("""INSERT INTO workout_routine_items(routine_id,position,exercise_id,activity_name,target_sets,target_reps,
            target_weight_kg,target_duration_s,target_distance_m,notes,work_seconds,rest_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (routine_id, item["position"], item.get("exercise_id"), item.get("activity_name"), item.get("target_sets"),
             item.get("target_reps"), item.get("target_weight_kg"), item.get("target_duration_s"), item.get("target_distance_m"), item.get("notes"),
             item.get("work_seconds"), item.get("rest_seconds")))


def list_routines(c, include_archived=False, routine_type=None):
    where, args = [], []
    if not include_archived:
        where.append("status='active'")
    if routine_type is not None:
        where.append("routine_type=?"); args.append(_routine_type(routine_type))
    query = "SELECT id FROM workout_routines" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC,id DESC"
    return {"routines": [_routine(c, row[0]) for row in c.execute(query, args).fetchall()]}


def get_routine(c, routine_id):
    return _routine(c, routine_id)


def update_routine(c, routine_id, name=None, description=None, estimated_duration_min=None, items=None,
                   routine_type=None, rounds=None, round_options=None):
    current = _routine(c, routine_id)
    new_type = _routine_type(routine_type) if routine_type is not None else current["routine_type"]
    normalized = _normalize_items(c, items) if items is not None else None
    if new_type == "hiit":
        new_rounds = rounds if rounds is not None else current["rounds"]
        new_options = round_options if round_options is not None else current["round_options"]
        if round_options is not None and rounds is None and new_rounds not in (round_options or []):
            new_rounds = None  # new options without an explicit default: the smallest option becomes the default
        new_rounds, new_options = _rounds_fields(new_rounds, new_options)
        if normalized is not None:
            _hiit_defaults(normalized)
    else:
        _check_type_fields(new_type, normalized, rounds, round_options)
        new_rounds, new_options = current["rounds"], current["round_options"]  # kept, unused while standard
    with dbm.transaction(c):
        c.execute("""UPDATE workout_routines SET name=?,description=?,estimated_duration_min=?,routine_type=?,rounds=?,
                  round_options_json=?,updated_at=? WHERE id=?""",
                  (name.strip() if isinstance(name, str) and name.strip() else current["name"],
                   description if description is not None else current["description"],
                   estimated_duration_min if estimated_duration_min is not None else current["estimated_duration_min"],
                   new_type, new_rounds, json.dumps(new_options) if new_options else None, dbm.now(), routine_id))
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


_WARMUP = re.compile(r"warm[\s_-]*up|\u70ed\u8eab", re.I)
_COOLDOWN = re.compile(r"cool[\s_-]*down|stretch|\u653e\u677e|\u62c9\u4f38", re.I)


def _item_phase(item):
    """warmup | cooldown | work, from the item's own text (activity name or notes)."""
    text = " ".join(str(item.get(k) or "") for k in ("activity_name", "notes"))
    if _WARMUP.search(text):
        return "warmup"
    if _COOLDOWN.search(text):
        return "cooldown"
    return "work"


def _rest_seconds_arg(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3600:
        raise _error("default_rest_s must be a whole number of seconds from 0 to 3600")
    return value


def guided_steps(routine, default_rest_s=GUIDED_DEFAULT_REST_SECONDS, lang="en", unit="kg", session_items=None):
    """Ordered guided-runner steps for a standard routine.

    Each item becomes target_sets work steps (1 when unset) carrying its prescription. A rest step follows
    every work step except the last one overall: the item's rest_seconds when set (0 = none), otherwise
    default_rest_s for working sets. Warm-up and cool-down items (named so in activity_name or notes) rest only
    when they set rest_seconds themselves. A work step counts down only when the item has target_duration_s;
    otherwise the runner counts up from 0 until the user taps Done. session_items maps routine_item_id to the
    active-workout session item id, which log_sets accepts as session_item_id.
    """
    if routine["routine_type"] != "standard":
        raise _error(f"routine {routine['routine_id']} is a {routine['routine_type']} routine; use hiit_timer to time it")
    if lang not in ("en", "zh"):
        raise _error("lang must be en or zh")
    default_rest_s = _rest_seconds_arg(default_rest_s)
    import core
    session_items = session_items or {}
    steps = []
    for item in routine["items"]:
        phase = _item_phase(item)
        exercise = item.get("exercise") or item.get("activity_name")
        label = (item.get("exercise_zh") if lang == "zh" else None) or exercise
        sets = item.get("target_sets") or 1
        duration = item.get("target_duration_s")
        weight = core.to_unit(item["target_weight_kg"], unit) if item.get("target_weight_kg") is not None else None
        rest = item.get("rest_seconds")
        if rest is None:
            rest = default_rest_s if phase == "work" else 0
        for index in range(1, sets + 1):
            steps.append({
                "kind": phase, "exercise": exercise, "label": label, "name_zh": item.get("exercise_zh"),
                "set_index": index, "sets": sets, "reps": item.get("target_reps"),
                "weight": weight, "unit": unit if weight is not None else None,
                "duration_s": duration, "distance_m": item.get("target_distance_m"), "notes": item.get("notes"),
                "timer": "countdown" if duration else "countup", "is_warmup": phase == "warmup",
                "loggable": item.get("exercise_id") is not None,
                "routine_item_id": item["routine_item_id"], "session_item_id": session_items.get(item["routine_item_id"]),
            })
            if rest:
                steps.append({"kind": "rest", "seconds": rest})
    while steps and steps[-1]["kind"] == "rest":  # no rest after the final step
        steps.pop()
    if len(steps) > MAX_GUIDED_STEPS:
        raise _error(f"routine expands to more than {MAX_GUIDED_STEPS} steps")
    return steps


COMPLETION_HANDOFF = {
    "applies_to": "Offline fallback only: clients without MCP Apps, where the runner is a page rendered with "
                  "render_timer_html.py. When the result has ui_auto_log true, the attached runner already logs each "
                  "Done set and calls finish_workout itself; do not ask for the summary or log those sets again.",
    "source": "the finished (or ended) guided runner page: its 'Completed sets' list and the JSON summary under it",
    "rule": "Call log_sets only for steps the user marked Done. Never log skipped, unreached, or prescribed-only "
            "steps. If the user stopped early without the summary, ask where they stopped before logging.",
    "log_sets_fields": {
        "exercise": "step exercise (canonical name)", "reps": "step reps, or what the user says they did",
        "weight": "step weight", "unit": "step unit", "duration_s": "actual seconds for timed holds or untimed steps without reps",
        "distance_m": "step distance_m", "is_warmup": "step is_warmup", "session_item_id": "step session_item_id",
    },
    "not_loggable": "Steps with loggable false (activity-only items such as a warm-up walk) are not sets; mark their "
                    "session item completed with set_workout_item_status, or use log_activity when the user wants it recorded.",
    "timing": "Log the completed sets in order, one log_sets call per set with performed_at set to that set's "
              "done_at, before any other workout tool call. The active workout closes after 15 minutes without "
              "logged activity, so backdating each set to when it was done keeps the sets in this workout.",
    "after_logging": "Call finish_workout when the user is done for the day.",
}


def guided_routine_timer(c, routine_id, lang="en", default_rest_s=GUIDED_DEFAULT_REST_SECONDS, idempotency_key=None):
    """Start a standard routine (start_routine) and return the guided-runner config.

    MCP Apps clients show it in the attached runner view (ui://workout/guided.html), which logs each Done set;
    other clients render it offline with render_timer_html.py and log from completion_handoff."""
    import core
    routine = _routine(c, routine_id)
    if routine["routine_type"] == "hiit":
        raise _error(f"routine {routine_id} is a hiit routine; use hiit_timer with routine_id for timed circuits")
    if lang not in ("en", "zh"):
        raise _error("lang must be en or zh")
    _rest_seconds_arg(default_rest_s)
    replay = bool(idempotency_key) and c.execute("SELECT 1 FROM sessions WHERE idempotency_key=?",
                                                 (idempotency_key,)).fetchone() is not None
    started = start_routine(c, routine_id, idempotency_key=idempotency_key)
    session_items = {x["source_routine_item_id"]: x["id"] for x in started["items"]
                     if x.get("source_routine_item_id") is not None and x.get("status") != "replaced"}
    steps = guided_steps(routine, default_rest_s, lang, core.display_unit(c), session_items)
    # start_workout's own "status" key is overwritten by the session's status, so report started/replayed here.
    return {"mode": "guided", "status": "already_started" if replay else "started", "session_id": started["session_id"], "routine_id": routine_id,
            "name": routine["name"], "lang": lang, "default_rest_s": default_rest_s,
            "work_sets": sum(1 for x in steps if x["kind"] != "rest"), "steps": steps,
            "completion_handoff": COMPLETION_HANDOFF}
