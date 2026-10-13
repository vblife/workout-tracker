"""Optional user profile facts that support workout calculations."""
import db as dbm

LB = 0.45359237


def _error(message):
    from core import UserError
    return UserError(message)


def _recalculate_from(c, measured_at):
    import calories
    for row in c.execute("SELECT id FROM sessions WHERE started_at>=? ORDER BY started_at", (measured_at,)).fetchall():
        calories.estimate_session(c, row[0])


def add_body_weight(c, value, unit, measured_at=None):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise _error("body weight must be greater than zero")
    if unit not in ("lb", "kg"):
        raise _error("body weight unit must be lb or kg")
    measured_at = measured_at or dbm.now()
    kg = float(value) * (LB if unit == "lb" else 1)
    now = dbm.now()
    weight_id = c.execute("INSERT INTO body_weight(measured_at,input_value,input_unit,weight_kg,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                          (measured_at, value, unit, kg, now, now)).lastrowid
    c.commit()
    _recalculate_from(c, measured_at)
    return {"weight_id": weight_id, "measured_at": measured_at, "input_value": value, "input_unit": unit, "weight_kg": round(kg, 3)}


def list_body_weight(c, limit=100):
    return {"entries": [{"weight_id": row["id"], "measured_at": row["measured_at"], "input_value": row["input_value"],
                          "input_unit": row["input_unit"], "weight_kg": round(row["weight_kg"], 3)}
                         for row in c.execute("SELECT * FROM body_weight ORDER BY measured_at DESC,id DESC LIMIT ?", (max(1, min(int(limit), 1000)),))]}


def delete_body_weight(c, weight_id):
    row = c.execute("SELECT * FROM body_weight WHERE id=?", (weight_id,)).fetchone()
    if not row:
        raise _error("no such body-weight entry")
    c.execute("DELETE FROM body_weight WHERE id=?", (weight_id,))
    c.commit()
    _recalculate_from(c, row["measured_at"])
    return {"status": "deleted", "weight_id": weight_id}


def update_body_weight(c, weight_id, value, unit, measured_at=None):
    old = c.execute("SELECT * FROM body_weight WHERE id=?", (weight_id,)).fetchone()
    if not old:
        raise _error("no such body-weight entry")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or unit not in ("lb", "kg"):
        raise _error("body weight needs a positive value and lb or kg unit")
    measured_at = measured_at or old["measured_at"]
    kg = float(value) * (LB if unit == "lb" else 1)
    c.execute("UPDATE body_weight SET measured_at=?,input_value=?,input_unit=?,weight_kg=?,updated_at=? WHERE id=?",
              (measured_at, value, unit, kg, dbm.now(), weight_id))
    c.commit()
    _recalculate_from(c, min(old["measured_at"], measured_at))
    return {"weight_id": weight_id, "measured_at": measured_at, "input_value": value, "input_unit": unit, "weight_kg": round(kg, 3)}
