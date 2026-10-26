"""Confirmed nutrient-only nutrition records and optional USDA lookups."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
import urllib.parse
import urllib.request

import db as dbm

NUTRIENTS = ("calories_kcal", "protein_g", "carbohydrates_g", "fat_g", "fiber_g", "saturated_fat_g", "sugar_g", "sodium_mg")
ALLOWED = set(NUTRIENTS) | {"consumed_at", "capture_timezone", "estimation_method", "confidence", "usda_contributed", "estimator_version", "source_components", "uncertainty_flags"}
FLAGS = {"portion_uncertain", "preparation_unknown", "ingredients_unknown", "image_unclear", "usda_match_uncertain"}


def _error(message):
    from core import UserError
    return UserError(message)


def _user_timezone_name(c):
    from core import user_timezone_name
    return user_timezone_name(c)


def nutrition_status(c, environ=None):
    environ = os.environ if environ is None else environ
    settings = {row["key"]: json.loads(row["value"]) for row in c.execute("SELECT key,value FROM settings")}
    return {"usda_available": bool(environ.get("USDA_FDC_API_KEY")),
            "model_only_acknowledged": bool(settings.get("model_only_acknowledged", False)),
            "mode": "usda_hybrid" if environ.get("USDA_FDC_API_KEY") else "model_only"}


def validate_draft(payload, default_timezone=None):
    """Normalize a nutrient-only draft.

    capture_timezone defaults to default_timezone (the user's timezone; never UTC). A consumed_at with no
    zone/offset is wall time in capture_timezone, i.e. user-local. local_date is the consumed day in
    capture_timezone and is the day key used by daily totals, nutrition goals, and reports.
    """
    if not isinstance(payload, dict):
        raise _error("nutrition draft must be an object")
    extra = sorted(set(payload) - ALLOWED)
    if extra:
        raise _error("nutrition draft contains forbidden fields: " + ", ".join(extra))
    clean = {name: payload.get(name) for name in NUTRIENTS}
    for name, value in clean.items():
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
            raise _error(f"{name} must be a non-negative number or null")
    if all(value is None for value in clean.values()):
        raise _error("at least one nutrient value is required")
    method = payload.get("estimation_method")
    confidence = payload.get("confidence")
    if method not in ("usda_hybrid", "model_only") or confidence not in ("higher", "moderate", "rough"):
        raise _error("estimation_method and confidence are required")
    flags = payload.get("uncertainty_flags") or []
    if not isinstance(flags, list) or any(flag not in FLAGS for flag in flags):
        raise _error("uncertainty_flags contains an unsupported generic flag")
    capture_timezone = payload.get("capture_timezone") or default_timezone
    if not capture_timezone:
        raise _error("capture_timezone is required when the user's timezone is unknown")
    consumed_at = payload.get("consumed_at") or dbm.now()
    try:
        zone = ZoneInfo(capture_timezone)
        instant = datetime.fromisoformat(consumed_at.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=zone)  # no zone = user-local wall time
        instant = instant.astimezone(timezone.utc)
        local_date = instant.astimezone(zone).date().isoformat()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _error("consumed_at needs an ISO timestamp and capture_timezone needs an IANA zone")
    return {**clean, "consumed_at": instant.isoformat().replace("+00:00", "Z"), "capture_timezone": capture_timezone,
            "local_date": local_date, "estimation_method": method, "confidence": confidence,
            "usda_contributed": bool(payload.get("usda_contributed", method == "usda_hybrid")),
            "estimator_version": payload.get("estimator_version") or "agent-v1",
            "source_components": payload.get("source_components"), "uncertainty_flags": flags}


def _entry(c, entry_id):
    row = c.execute("SELECT * FROM nutrition_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise _error("no such nutrition entry")
    result = dict(row)
    result["entry_id"] = result.pop("id")
    result["usda_contributed"] = bool(result["usda_contributed"])
    result["uncertainty_flags"] = json.loads(result.pop("uncertainty_flags_json") or "[]")
    return result


def save_entry(c, payload, confirmed=False):
    if confirmed is not True:
        raise _error("explicit user confirmation is required before saving nutrition")
    draft = validate_draft(payload, _user_timezone_name(c))
    now = dbm.now()
    values = [draft[name] for name in NUTRIENTS]
    with dbm.transaction(c):
        entry_id = c.execute("""INSERT INTO nutrition_entries(
            consumed_at,capture_timezone,local_date,calories_kcal,protein_g,carbohydrates_g,fat_g,fiber_g,saturated_fat_g,sugar_g,sodium_mg,
            estimation_method,confidence,usda_contributed,estimator_version,source_components,uncertainty_flags_json,confirmed_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (draft["consumed_at"], draft["capture_timezone"], draft["local_date"], *values,
             draft["estimation_method"], draft["confidence"], int(draft["usda_contributed"]), draft["estimator_version"],
             draft["source_components"], json.dumps(draft["uncertainty_flags"]), now, now, now)).lastrowid
    return _entry(c, entry_id)


def update_entry(c, entry_id, payload, confirmed=False):
    _entry(c, entry_id)
    if confirmed is not True:
        raise _error("explicit user confirmation is required before updating nutrition")
    draft = validate_draft(payload, _user_timezone_name(c))
    values = [draft[name] for name in NUTRIENTS]
    c.execute("""UPDATE nutrition_entries SET consumed_at=?,capture_timezone=?,local_date=?,
        calories_kcal=?,protein_g=?,carbohydrates_g=?,fat_g=?,fiber_g=?,saturated_fat_g=?,sugar_g=?,sodium_mg=?,
        estimation_method=?,confidence=?,usda_contributed=?,estimator_version=?,source_components=?,uncertainty_flags_json=?,
        confirmed_at=?,updated_at=? WHERE id=?""", (draft["consumed_at"], draft["capture_timezone"], draft["local_date"], *values,
        draft["estimation_method"], draft["confidence"], int(draft["usda_contributed"]), draft["estimator_version"],
        draft["source_components"], json.dumps(draft["uncertainty_flags"]), dbm.now(), dbm.now(), entry_id))
    c.commit()
    return _entry(c, entry_id)


def list_entries(c, from_date=None, to_date=None, limit=100):
    query, args = "SELECT id FROM nutrition_entries WHERE 1=1", []
    if from_date: query += " AND local_date>=?"; args.append(from_date)
    if to_date: query += " AND local_date<=?"; args.append(to_date)
    query += " ORDER BY consumed_at DESC,id DESC LIMIT ?"; args.append(max(1, min(int(limit), 1000)))
    return {"entries": [_entry(c, row[0]) for row in c.execute(query, args)]}


def delete_entry(c, entry_id):
    _entry(c, entry_id)
    c.execute("DELETE FROM nutrition_entries WHERE id=?", (entry_id,))
    c.commit()
    return {"status": "deleted", "entry_id": entry_id}


def daily_totals(c, local_date):
    expressions = ",".join(f"sum({name}) {name}" for name in NUTRIENTS)
    row = c.execute(f"SELECT count(*) entries,{expressions} FROM nutrition_entries WHERE local_date=?", (local_date,)).fetchone()
    return {"local_date": local_date, **{key: row[key] for key in row.keys()}}


def search_usda(query, api_key=None, opener=None):
    api_key = api_key or os.environ.get("USDA_FDC_API_KEY")
    if not api_key:
        return {"status": "model_only_fallback", "reason": "USDA API key is not configured", "matches": []}
    opener = opener or urllib.request.urlopen
    body = json.dumps({"query": query, "pageSize": 5}).encode("utf-8")
    request = urllib.request.Request("https://api.nal.usda.gov/fdc/v1/foods/search?" + urllib.parse.urlencode({"api_key": api_key}),
                                     data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        matches = [{"description": item.get("description"), "fdc_id": item.get("fdcId"), "nutrients": item.get("foodNutrients", [])}
                   for item in data.get("foods", [])]
        return {"status": "ok" if matches else "model_only_fallback", "reason": None if matches else "no credible USDA match", "matches": matches}
    except Exception:
        return {"status": "model_only_fallback", "reason": "USDA lookup unavailable", "matches": []}


ACTIVITY_FACTORS = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.9}
OBJECTIVES = ("maintenance", "fat_loss", "muscle_gain")
SEXES = ("male", "female")
RECOMMEND_INPUTS = ("age", "weight_kg", "height_cm", "sex", "activity_level", "objective")
GUARDRAIL_FLAGS = ("pregnant", "breastfeeding", "eating_disorder", "medical_condition")
# Unambiguous spellings seen from callers; normalized before validation.
INPUT_ALIASES = {"age_years": "age"}
OBJECTIVE_ALIASES = {"lose": "fat_loss", "weight_loss": "fat_loss", "maintain": "maintenance"}


def _normalize_recommend_inputs(inputs):
    if not isinstance(inputs, dict):
        raise _error("inputs must be an object with: " + ", ".join(RECOMMEND_INPUTS))
    inputs = dict(inputs)
    for alias, key in INPUT_ALIASES.items():
        if alias in inputs:
            value = inputs.pop(alias)
            if inputs.get(key) is not None and inputs[key] != value:
                raise _error(f"{alias} and {key} disagree; send only {key}")
            if inputs.get(key) is None:
                inputs[key] = value
    if isinstance(inputs.get("objective"), str):
        inputs["objective"] = OBJECTIVE_ALIASES.get(inputs["objective"], inputs["objective"])
    return inputs


def recommend_goals(inputs):
    inputs = _normalize_recommend_inputs(inputs)
    required = RECOMMEND_INPUTS
    if inputs.get("age") is None:
        raise _error("age is required to confirm that adult recommendations are appropriate")
    if not isinstance(inputs["age"], (int, float)) or not 18 <= inputs["age"] <= 100:
        raise _error("personalized nutrition recommendations are limited to adults")
    if any(inputs.get(flag) for flag in GUARDRAIL_FLAGS):
        raise _error("this profile needs qualified clinical nutrition guidance")
    factors = ACTIVITY_FACTORS
    if inputs.get("sex") is not None and inputs["sex"] not in SEXES:
        raise _error(f"sex used for calculation must be one of: {', '.join(SEXES)} (got {inputs['sex']!r})")
    if inputs.get("activity_level") is not None and inputs["activity_level"] not in factors:
        raise _error(f"unsupported activity_level {inputs['activity_level']!r}; use one of: {', '.join(factors)}")
    if inputs.get("objective") is not None and inputs["objective"] not in OBJECTIVES:
        raise _error(f"unsupported objective {inputs['objective']!r}; use one of: {', '.join(OBJECTIVES)}")
    for key in ("weight_kg", "height_cm"):
        if inputs.get(key) is not None and (isinstance(inputs[key], bool) or not isinstance(inputs[key], (int, float)) or inputs[key] <= 0):
            raise _error(f"{key} must be a number greater than zero")
    recommendations = {"sodium_mg_max": 2300}
    if inputs.get("weight_kg") is not None:
        recommendations["protein_g_range"] = [round(inputs["weight_kg"] * 1.2), round(inputs["weight_kg"] * 1.8)]
    if inputs.get("sex") is not None:
        recommendations["fiber_g"] = 38 if inputs["sex"] == "male" and inputs["age"] <= 50 else 25
    missing = [key for key in required if inputs.get(key) is None]
    if not missing:
        sex_term = 5 if inputs["sex"] == "male" else -161
        resting = 10 * inputs["weight_kg"] + 6.25 * inputs["height_cm"] - 5 * inputs["age"] + sex_term
        calories = resting * factors[inputs["activity_level"]] + {"maintenance": 0, "fat_loss": -350, "muscle_gain": 250}[inputs["objective"]]
        protein = inputs["weight_kg"] * (1.8 if inputs["objective"] == "muscle_gain" else 1.6)
        recommendations.update({"calories_kcal": round(calories / 10) * 10, "protein_g": round(protein),
                                "saturated_fat_g_max": round(calories * 0.1 / 9)})
    assumptions = {key: inputs[key] for key in required if inputs.get(key) is not None}
    ignored = sorted(key for key in inputs if key not in RECOMMEND_INPUTS and key not in GUARDRAIL_FLAGS)
    result_extra = {"ignored_inputs": ignored} if ignored else {}
    return {**result_extra, "saved": False, "version": "mifflin-dga-v1", "basis": "Mifflin-St Jeor plus Dietary Guidelines guardrails",
            "recommendations": recommendations, "missing_for_energy": missing, "assumptions": assumptions,
            "notice": "Educational estimate for generally healthy adults; confirm before saving."}
