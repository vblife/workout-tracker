"""Versioned, explicitly estimated workout calorie calculations."""
from datetime import datetime, timezone
import json

import db as dbm

VERSION = "met-v1"
METS = {"strength": 5.0, "mixed": 5.5, "cardio": 7.0, "hike": 6.0, "hiit": 8.0}


def _dt(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def estimate_session(c, session_id):
    row = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        from core import UserError
        raise UserError("no such session")
    assumptions = []
    weight = c.execute("SELECT weight_kg FROM body_weight WHERE measured_at<=? ORDER BY measured_at DESC,id DESC LIMIT 1", (row["started_at"],)).fetchone()
    if weight:
        weight_kg = weight[0]
    else:
        weight_kg = 70.0
        assumptions.append("70 kg fallback body weight")
    duration = row["duration_min"]
    if not duration and row["ended_at"]:
        duration = max(0, (_dt(row["ended_at"]) - _dt(row["started_at"])).total_seconds() / 60)
    if not duration and row["last_activity_at"] and row["last_activity_at"] != row["started_at"]:
        duration = max(0, (_dt(row["last_activity_at"]) - _dt(row["started_at"])).total_seconds() / 60)
    if not duration:
        count = c.execute("SELECT count(*) FROM sets WHERE session_id=?", (session_id,)).fetchone()[0]
        duration = max(5.0, count * 3.0)
        assumptions.append("set-count duration heuristic")
    met = METS.get(row["type"] or "strength", 5.0)
    if row["rpe"]:
        met *= max(0.75, min(1.25, row["rpe"] / 7))
    else:
        assumptions.append("conservative default intensity")
    kcal = round(met * 3.5 * weight_kg / 200 * duration)
    confidence = "rough" if len(assumptions) >= 2 else "moderate" if assumptions else "higher"
    with dbm.transaction(c):
        c.execute("""UPDATE sessions SET estimated_calories=?,calorie_confidence=?,calorie_weight_kg=?,
            calorie_duration_min=?,calorie_met=?,calorie_version=?,calorie_assumptions=? WHERE id=?""",
            (kcal, confidence, weight_kg, round(duration, 2), round(met, 2), VERSION, json.dumps(assumptions), session_id))
    return {"estimated_calories": kcal, "confidence": confidence, "weight_kg": weight_kg,
            "duration_min": round(duration, 2), "met": round(met, 2), "version": VERSION, "assumptions": assumptions}
