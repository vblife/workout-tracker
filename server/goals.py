"""Workout and nutrition goals derived from authoritative records."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json

import db as dbm

WORKOUT_METRICS = {"workout_sessions", "training_days", "workout_minutes", "cardio_minutes", "strength_sets", "strength_volume", "distance", "workout_calories"}
NUTRITION_METRICS = {"calories_kcal", "protein_g", "carbohydrates_g", "fat_g", "fiber_g", "saturated_fat_g", "sugar_g", "sodium_mg"}


def _error(message):
    from core import UserError
    return UserError(message)


def create_goal(c, *, domain, period, metric, semantics, unit, effective_start_date, target_value=None,
                lower_value=None, upper_value=None, display_precision=0, origin="user", recommendation_version=None, assumptions=None):
    if domain not in ("workout", "nutrition") or period not in ("day", "week", "month"):
        raise _error("invalid goal domain or period")
    allowed_metrics = WORKOUT_METRICS if domain == "workout" else NUTRITION_METRICS
    if metric not in allowed_metrics:
        raise _error(f"invalid metric {metric!r} for {domain} goals; use one of: {', '.join(sorted(allowed_metrics))}")
    if domain == "nutrition" and period != "day":
        raise _error("V1 nutrition goals must use the day period")
    if semantics not in ("minimum", "maximum", "exact", "range") or origin not in ("user", "recommended"):
        raise _error("invalid goal semantics or origin")
    if semantics == "range":
        if lower_value is None or upper_value is None or lower_value > upper_value:
            raise _error("range goals need ordered lower_value and upper_value")
    elif target_value is None:
        raise _error("goal target_value is required")
    try:
        date.fromisoformat(effective_start_date)
    except (TypeError, ValueError):
        raise _error("effective_start_date must be YYYY-MM-DD")
    now = dbm.now()
    with dbm.transaction(c):
        if origin == "user":
            c.execute("UPDATE goals SET active=0,updated_at=? WHERE active=1 AND domain=? AND period=? AND metric=? AND origin='recommended'",
                      (now, domain, period, metric))
        goal_id = c.execute("""INSERT INTO goals(domain,period,metric,semantics,target_value,lower_value,upper_value,unit,
            display_precision,origin,recommendation_version,assumptions_json,effective_start_date,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (domain, period, metric, semantics, target_value, lower_value, upper_value, unit,
            display_precision, origin, recommendation_version, json.dumps(assumptions or []), effective_start_date, now, now)).lastrowid
    return {"goal_id": goal_id, **_goal(c, goal_id)}


def _goal(c, goal_id):
    row = c.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if not row:
        raise _error("no such goal")
    result = dict(row)
    result.pop("id")
    result["active"] = bool(result["active"])
    result["assumptions"] = json.loads(result.pop("assumptions_json") or "[]")
    return result


def list_goals(c, include_inactive=False, domain=None):
    query, args = "SELECT id FROM goals WHERE 1=1", []
    if not include_inactive:
        query += " AND active=1"
    if domain:
        query += " AND domain=?"; args.append(domain)
    query += " ORDER BY active DESC,effective_start_date DESC,id DESC"
    return {"goals": [{"goal_id": row[0], **_goal(c, row[0])} for row in c.execute(query, args)]}


def update_goal(c, goal_id, **changes):
    current = _goal(c, goal_id)
    allowed = {"semantics", "target_value", "lower_value", "upper_value", "unit", "display_precision",
               "effective_start_date", "active"}
    extra = set(changes) - allowed
    if extra:
        raise _error("unsupported goal fields: " + ", ".join(sorted(extra)))
    value = {**current, **changes}
    if value["semantics"] not in ("minimum", "maximum", "exact", "range"):
        raise _error("invalid goal semantics")
    if value["semantics"] == "range":
        if value.get("lower_value") is None or value.get("upper_value") is None or value["lower_value"] > value["upper_value"]:
            raise _error("range goals need ordered lower_value and upper_value")
    elif value.get("target_value") is None:
        raise _error("goal target_value is required")
    if not isinstance(value["display_precision"], int) or not 0 <= value["display_precision"] <= 6:
        raise _error("display_precision must be an integer from 0 to 6")
    try:
        date.fromisoformat(value["effective_start_date"])
    except (TypeError, ValueError):
        raise _error("effective_start_date must be YYYY-MM-DD")
    with dbm.transaction(c):
        c.execute("""UPDATE goals SET semantics=?,target_value=?,lower_value=?,upper_value=?,unit=?,display_precision=?,
            effective_start_date=?,active=?,updated_at=? WHERE id=?""",
            (value["semantics"], value.get("target_value"), value.get("lower_value"), value.get("upper_value"), value["unit"],
             value["display_precision"], value["effective_start_date"], int(bool(value["active"])), dbm.now(), goal_id))
    return {"goal_id": goal_id, **_goal(c, goal_id)}


def delete_goal(c, goal_id):
    _goal(c, goal_id)
    c.execute("UPDATE goals SET active=0,updated_at=? WHERE id=?", (dbm.now(), goal_id))
    c.commit()
    return {"status": "deactivated", "goal_id": goal_id}


def _bounds(period, anchor, tz):
    if period == "day":
        start, end = anchor, anchor + timedelta(days=1)
    elif period == "week":
        start = anchor - timedelta(days=anchor.weekday()); end = start + timedelta(days=7)
    else:
        start = anchor.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    def utc(local_date):
        return datetime.combine(local_date, datetime.min.time(), tz).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start, end, utc(start), utc(end)


def _settings(c):
    return {row["key"]: json.loads(row["value"]) for row in c.execute("SELECT key,value FROM settings")}


def _current(c, goal, start_utc, end_utc, start_date, end_date, tz):
    metric = goal["metric"]
    if goal["domain"] == "nutrition":
        return c.execute(f"SELECT coalesce(sum({metric}),0) FROM nutrition_entries WHERE consumed_at>=? AND consumed_at<?", (start_utc, end_utc)).fetchone()[0]
    completed = "status='completed' AND started_at>=? AND started_at<?"
    if metric == "workout_sessions": return c.execute(f"SELECT count(*) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "training_days":
        rows = c.execute(f"SELECT started_at FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchall()
        return len({datetime.fromisoformat(row[0].replace("Z", "+00:00")).astimezone(tz).date() for row in rows})
    if metric == "workout_minutes": return c.execute(f"SELECT coalesce(sum(duration_min),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "cardio_minutes": return c.execute(f"SELECT coalesce(sum(duration_min),0) FROM sessions WHERE {completed} AND type IN('cardio','hike','hiit')", (start_utc, end_utc)).fetchone()[0]
    if metric == "distance": return c.execute(f"SELECT coalesce(sum(distance_km),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "workout_calories": return c.execute(f"SELECT coalesce(sum(estimated_calories),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "strength_sets": return c.execute("""SELECT count(*) FROM sets x JOIN sessions s ON s.id=x.session_id
        WHERE s.status='completed' AND x.is_warmup=0 AND x.performed_at>=? AND x.performed_at<?""", (start_utc, end_utc)).fetchone()[0]
    if metric == "strength_volume": return c.execute("""SELECT coalesce(sum(x.weight_kg*x.reps),0) FROM sets x JOIN sessions s ON s.id=x.session_id
        WHERE s.status='completed' AND x.is_warmup=0 AND x.performed_at>=? AND x.performed_at<?""", (start_utc, end_utc)).fetchone()[0]
    return 0


def _achieved(goal, current):
    if goal["semantics"] == "minimum": return current >= goal["target_value"]
    if goal["semantics"] == "maximum": return current <= goal["target_value"]
    if goal["semantics"] == "exact": return current == goal["target_value"]
    return goal["lower_value"] <= current <= goal["upper_value"]


def _next_period(period, start):
    if period == "day": return start + timedelta(days=1)
    if period == "week": return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def goal_progress(c, goal_id, anchor_date=None):
    goal = {"goal_id": goal_id, **_goal(c, goal_id)}
    tz = ZoneInfo(_settings(c).get("timezone", "America/Los_Angeles"))
    anchor = date.fromisoformat(anchor_date) if anchor_date else datetime.now(tz).date()
    start, end, start_utc, end_utc = _bounds(goal["period"], anchor, tz)
    current = _current(c, goal, start_utc, end_utc, start, end, tz)
    achieved = _achieved(goal, current)
    effective = date.fromisoformat(goal["effective_start_date"])
    cursor = _bounds(goal["period"], effective, tz)[0]
    history = []
    while cursor <= start and len(history) < 3660:
        h_start, h_end, h_start_utc, h_end_utc = _bounds(goal["period"], cursor, tz)
        h_current = _current(c, goal, h_start_utc, h_end_utc, h_start, h_end, tz)
        history.append({"period_start": h_start.isoformat(), "period_end_exclusive": h_end.isoformat(),
                        "current": round(h_current, goal["display_precision"]), "achieved": _achieved(goal, h_current)})
        cursor = _next_period(goal["period"], h_start)
    current_streak = 0
    for item in reversed(history):
        if not item["achieved"]: break
        current_streak += 1
    best = run = 0
    for item in history:
        run = run + 1 if item["achieved"] else 0
        best = max(best, run)
    hit_count = sum(1 for item in history if item["achieved"])
    return {**goal, "period_start": start.isoformat(), "period_end_exclusive": end.isoformat(),
            "current": round(current, goal["display_precision"]), "achieved": achieved,
            "history": history, "current_streak": current_streak, "best_streak": best,
            "hit_rate_pct": round(100 * hit_count / len(history), 1) if history else None}
