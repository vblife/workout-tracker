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
    "applies_to": "Offline fallback only: clients without MCP Apps (ui_auto_log false, delivery offline_page), where "
                  "the runner is a page rendered with render_timer_html.py. When the result has ui_auto_log true, the "
                  "attached runner already logs each Done set and calls finish_workout itself; do not ask for the "
                  "result or log those sets again.",
    "source": "the page's Send to Workout Bot button: it sends a short line plus a line starting WORKOUT_BOT_GUIDED "
              "followed by JSON {workout_bot, session_id, routine_id, ended, completed}. The page also keeps the "
              "result on the device, so reopening it shows the same Send button.",
    "rule": "When a message carries that result, immediately call log_guided_result with its session_id, completed "
            "and ended. Do not ask questions first, do not call log_sets set by set, and do not call finish_workout: "
            "log_guided_result backdates each Done set to its done_at, skips sets already saved, reopens the "
            "runner's workout if the 15-minute idle rule closed it while the user trained offline, and finishes it. Never log skipped, "
            "unreached, or prescribed-only steps.",
    "without_result": "If the user says they are done but did not send the result, ask them to tap Send to Workout "
                      "Bot on the finished page (reopening the page shows it again). If they cannot, ask where they "
                      "stopped and log only what they confirm with log_sets; never guess.",
    "not_loggable": "Steps with loggable false (activity-only items such as a warm-up walk) are not sets; "
                    "log_guided_result marks their session item completed.",
    "timing": "log_guided_result logs each set with performed_at set to its done_at, in done_at order, so the sets "
              "land in this workout even though the active workout closes after 15 minutes without logged activity.",
    "on_rejected": "status rejected means nothing was saved: another workout is active now (other_workout_active), "
                   "or the runner's workout was already finished or abandoned on purpose (session_closed). Ask the "
                   "user which workout the sets belong to. needs_confirmation means an exercise is unknown; nothing was saved.",
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


GUIDED_RESULT_ENDED = ("finished", "ended_early")


class _GuidedAbort(Exception):
    """Raised inside log_guided_result's transaction to roll every write back and return `result`."""
    def __init__(self, result):
        super().__init__(result.get("note") or result.get("status"))
        self.result = result


def _guided_set(entry, index):
    """One Done entry from the runner page's summary -> one log_sets set (or a problem string)."""
    exercise = entry.get("exercise") or entry.get("label")
    if not isinstance(exercise, str) or not exercise.strip():
        return None, f"entry {index}: missing exercise"
    out = {"exercise": exercise}
    for key in ("reps", "duration_s"):
        value = entry.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return None, f"entry {index}: {key} must be a positive integer"
            out[key] = value
    for key in ("weight", "distance_m"):
        value = entry.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                return None, f"entry {index}: {key} must be a number >= 0"
            out[key] = value
    if out.get("weight") is not None and entry.get("unit") in ("lb", "kg"):
        out["unit"] = entry["unit"]
    if not any(k in out for k in ("reps", "duration_s", "distance_m")):
        return None, f"entry {index}: needs reps, duration_s or distance_m"
    out["is_warmup"] = entry.get("is_warmup") is True
    item = entry.get("session_item_id")
    if item is not None:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            return None, f"entry {index}: session_item_id must be a positive integer"
        out["session_item_id"] = item
    return out, None


def log_guided_result(c, session_id, completed, ended="finished", finish=True):
    """Write a guided runner's Done sets back into its own workout in one atomic call, then finish it.

    For the offline page (no MCP Apps), whose summary reaches the bot only after the user returns. Each Done
    entry becomes one set backdated to its done_at, in done_at order. Idempotent: a set already saved for the
    same exercise, session item and time is skipped, so sending the same result twice logs nothing new. The
    workout may already have been closed by the 15-minute inactivity rule while the user trained offline (an
    offline run logs nothing until the end); only such an idle-closed workout is reopened for these sets and
    finished again, with only the items that idle close skipped set back to pending (the user's own skips stay
    skipped). Nothing is saved when another workout is active, or when the workout was finished or abandoned
    deliberately (a stale replay must not reopen it). A done_at before the workout started or after now (clock
    skew) is pinned to that bound. Unknown exercises save nothing (needs_confirmation). Entries with loggable
    false mark their session item completed instead."""
    import core
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
        raise _error("session_id must be a positive integer")
    if not isinstance(completed, list):
        raise _error("completed must be the runner summary's completed list")
    if ended not in GUIDED_RESULT_ENDED:
        raise _error("ended must be finished or ended_early")
    session = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        raise _error(f"workout #{session_id} does not exist")
    if session["start_mode"] != "routine":
        raise _error(f"workout #{session_id} was not started from a routine, so it is not a guided runner workout")
    tz = core.user_timezone(c)
    started = workouts._dt(session["started_at"])
    plan, items, problems = [], [], []
    for index, entry in enumerate(completed):
        if not isinstance(entry, dict):
            problems.append({"index": index, "error": "entry must be an object"}); continue
        if entry.get("loggable") is False:
            item = entry.get("session_item_id")
            if isinstance(item, int) and not isinstance(item, bool) and item > 0:
                items.append(item)
            continue
        value, problem = _guided_set(entry, index)
        if problem:
            problems.append({"index": index, "error": problem}); continue
        try:
            at = core._parse_time(entry.get("done_at"), tz) if entry.get("done_at") else None
        except (TypeError, ValueError):
            problems.append({"index": index, "error": "done_at must be an ISO time"}); continue
        resolved = core.resolve_one(c, value["exercise"])
        if resolved["status"] != "matched":
            problems.append({"index": index, "input": value["exercise"], "status": resolved["status"],
                             "candidates": resolved["candidates"]}); continue
        plan.append({"index": index, "at": at, "set": value, "exercise_id": resolved["exercise_id"]})
    if problems:
        return {"status": "needs_confirmation", "session_id": session_id, "logged": [], "problems": problems,
                "note": "Nothing was saved. Fix or confirm these entries, then send the result again."}
    now = workouts._dt()
    adjusted = 0
    for step in plan:  # a missing Done time, or one outside [started, now] (clock skew), is pinned inside it
        at = step["at"] or now
        step["at"] = max(min(at, now), started)
        adjusted += step["at"] != at
    plan.sort(key=lambda s: (s["at"], s["index"]))
    todo, duplicates = [], 0
    for step in plan:
        if c.execute("""SELECT 1 FROM sets WHERE session_id=? AND exercise_id=? AND performed_at=?
                AND coalesce(session_item_id,0)=coalesce(?,0) LIMIT 1""",
                (session_id, step["exercise_id"], workouts._iso(step["at"]), step["set"].get("session_item_id"))).fetchone():
            duplicates += 1
        else:
            todo.append(step)
    if items:  # only items that still need marking count as work to write back
        items = [row[0] for row in c.execute(
            f"SELECT id FROM workout_session_items WHERE session_id=? AND status!='completed' AND id IN ({','.join('?' * len(items))})",
            (session_id, *items))]
    active = c.execute("SELECT id FROM sessions WHERE status='in_progress'").fetchone()
    active_id = active["id"] if active else None
    status_before = session["status"]
    if status_before != "in_progress" and (todo or items) and not session["closed_by_inactivity"]:
        return {"status": "rejected", "reason": "session_closed", "session_id": session_id, "logged": [],
                "session_status": status_before, "active_session_id": active_id,
                "note": (f"Nothing saved: workout #{session_id} was already ended ({status_before}) by a finish or "
                         "abandon, not by the 15-minute inactivity rule, so it is not reopened. Ask the user whether "
                         "these sets belong to that workout or should be logged another way.")}
    if status_before != "in_progress" and (todo or items) and active_id is not None:
        return {"status": "rejected", "reason": "other_workout_active", "session_id": session_id, "logged": [],
                "session_status": status_before, "active_session_id": active_id,
                "note": (f"Nothing saved: workout #{session_id} had already ended ({status_before}) and workout "
                         f"#{active_id} is active now. Ask the user which workout these sets belong to.")}
    logged, reopened = [], False
    try:
        with dbm.transaction(c):
            if status_before != "in_progress" and (todo or items):
                first = todo[0]["at"] if todo else now
                c.execute("""UPDATE sessions SET status='in_progress',ended_at=NULL,closed_by_inactivity=0,
                    last_activity_at=? WHERE id=?""", (workouts._iso(first), session_id))
                # Only the items the idle close skipped go back to pending; intentional skips stay skipped.
                c.execute("""UPDATE workout_session_items SET status='pending',skipped_by_inactivity=0
                    WHERE session_id=? AND status='skipped' AND skipped_by_inactivity=1""", (session_id,))
                reopened = True
            for step in todo:
                # A long rest inside the run is not inactivity: keep the workout open up to each Done time.
                c.execute("UPDATE sessions SET last_activity_at=max(coalesce(last_activity_at,started_at),?) "
                          "WHERE id=? AND status='in_progress'", (workouts._iso(step["at"]), session_id))
                r = core.log_sets(c, [step["set"]], performed_at=workouts._iso(step["at"]), session_id=session_id)
                if r.get("status") != "logged":
                    raise _GuidedAbort({"status": r.get("status") or "rejected", "session_id": session_id, "logged": [],
                                        "entry": step["index"], "detail": r,
                                        "note": r.get("note") or f"Nothing saved: entry {step['index']} could not be logged."})
                logged.extend(r["logged"])
            for item in items:
                c.execute("""UPDATE workout_session_items SET status='completed'
                    WHERE id=? AND session_id=? AND status IN ('pending','skipped')""", (item, session_id))
            finished = None
            if finish:
                current = c.execute("SELECT status,last_activity_at FROM sessions WHERE id=?", (session_id,)).fetchone()
                if current["status"] == "in_progress":
                    last = workouts._dt(current["last_activity_at"]) if (todo or reopened) else now
                    finished = workouts.finish_workout(c, at=max(last, started), session_id=session_id)
    except _GuidedAbort as abort:
        return abort.result
    final = c.execute("SELECT status,ended_at FROM sessions WHERE id=?", (session_id,)).fetchone()
    note = f"Saved {len(logged)} set(s) to workout #{session_id}"
    if duplicates:
        note += f"; {duplicates} were already saved and were skipped"
    if reopened:
        note += f"; the workout had closed while the user trained offline ({status_before}) and was reopened for them"
    if adjusted:
        note += f"; {adjusted} Done time(s) before the workout started or in the future (clock skew) were pinned inside it"
    note += f". Workout status: {final['status']}."
    return {"status": "logged" if logged else ("already_logged" if duplicates else "nothing_to_log"),
            "session_id": session_id, "ended": ended, "logged": logged, "already_logged": duplicates,
            "items_completed": items, "reopened": reopened, "times_adjusted": adjusted, "session_status": final["status"],
            "ended_at": final["ended_at"], "finished": final["status"] == "completed", "note": note,
            "workout": finished if isinstance(finished, dict) else None}
