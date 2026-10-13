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
    if metric not in (WORKOUT_METRICS if domain == "workout" else NUTRITION_METRICS):
        raise _error("invalid metric for goal domain")
    if domain == "nutrition" and period != "day":
        raise _error("V1 nutrition goals must use the day period")
    if semantics not in ("minimum", "maximum", "exact", "range") or origin not in ("user", "recommended"):
        raise _error("invalid goal semantics or origin")
    if semantics == "range":
        if lower_value is None or upper_value is None or lower_value > upper_value:
            raise _error("range goals need ordered lower_value and upper_value")
    elif target_value is None:
        raise _error("goal target_value is required")
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


def _current(c, goal, start_utc, end_utc, start_date, end_date):
    metric = goal["metric"]
    if goal["domain"] == "nutrition":
        return c.execute(f"SELECT coalesce(sum({metric}),0) FROM nutrition_entries WHERE consumed_at>=? AND consumed_at<?", (start_utc, end_utc)).fetchone()[0]
    completed = "status='completed' AND started_at>=? AND started_at<?"
    if metric == "workout_sessions": return c.execute(f"SELECT count(*) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "training_days": return c.execute(f"SELECT count(DISTINCT substr(started_at,1,10)) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "workout_minutes": return c.execute(f"SELECT coalesce(sum(duration_min),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "cardio_minutes": return c.execute(f"SELECT coalesce(sum(duration_min),0) FROM sessions WHERE {completed} AND type IN('cardio','hike','hiit')", (start_utc, end_utc)).fetchone()[0]
    if metric == "distance": return c.execute(f"SELECT coalesce(sum(distance_km),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "workout_calories": return c.execute(f"SELECT coalesce(sum(estimated_calories),0) FROM sessions WHERE {completed}", (start_utc, end_utc)).fetchone()[0]
    if metric == "strength_sets": return c.execute("""SELECT count(*) FROM sets x JOIN sessions s ON s.id=x.session_id
        WHERE s.status='completed' AND x.is_warmup=0 AND x.performed_at>=? AND x.performed_at<?""", (start_utc, end_utc)).fetchone()[0]
    if metric == "strength_volume": return c.execute("""SELECT coalesce(sum(x.weight_kg*x.reps),0) FROM sets x JOIN sessions s ON s.id=x.session_id
        WHERE s.status='completed' AND x.is_warmup=0 AND x.performed_at>=? AND x.performed_at<?""", (start_utc, end_utc)).fetchone()[0]
    return 0


def goal_progress(c, goal_id, anchor_date=None):
    goal = {"goal_id": goal_id, **_goal(c, goal_id)}
    tz = ZoneInfo(_settings(c).get("timezone", "America/Los_Angeles"))
    anchor = date.fromisoformat(anchor_date) if anchor_date else datetime.now(tz).date()
    start, end, start_utc, end_utc = _bounds(goal["period"], anchor, tz)
    current = _current(c, goal, start_utc, end_utc, start, end)
    if goal["semantics"] == "minimum": achieved = current >= goal["target_value"]
    elif goal["semantics"] == "maximum": achieved = current <= goal["target_value"]
    elif goal["semantics"] == "exact": achieved = current == goal["target_value"]
    else: achieved = goal["lower_value"] <= current <= goal["upper_value"]
    return {**goal, "period_start": start.isoformat(), "period_end_exclusive": end.isoformat(),
            "current": round(current, goal["display_precision"]), "achieved": achieved}
