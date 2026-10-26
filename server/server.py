#!/usr/bin/env python3
"""Workout Tracking MCP server (Python stdlib only). Usage: server.py --stdio [--db PATH]   (env WORKOUT_DB also honored)"""
import json,sys,os,traceback
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import db as dbm, core, coach, reports, workouts, routines, goals, profile, nutrition
from core import UserError
HERE=os.path.dirname(os.path.abspath(__file__))
NAME,VERSION="workout-bot","1.0.0"
MIME="text/html;profile=mcp-app"; HIIT_URI="ui://workout/hiit.html"
RMETA={"ui":{"prefersBorder":True,"csp":{"connectDomains":[],"resourceDomains":[]}},"openai/widgetDescription":"Configurable HIIT interval timer","openai/widgetPrefersBorder":True}
PROG_URI="ui://workout/progress.html"; REPORT_URI="ui://workout/report.html"; GUIDED_URI="ui://workout/guided.html"
# The guided runner view is the same self-contained page that render_timer_html.py writes for offline use.
UI_FILES={HIIT_URI:"hiit.html",PROG_URI:"progress.html",REPORT_URI:"report.html",GUIDED_URI:"timer.html"}
TOOL_UI={"hiit_timer":HIIT_URI,"show_progress":PROG_URI,"report":REPORT_URI,"guided_routine_timer":GUIDED_URI}
APP_TOOL={"ui":{"visibility":["model","app"]}}  # tools a UI view may call through tools/call
def _ui_html(uri):
  with open(os.path.join(HERE,"ui",UI_FILES[uri]),encoding="utf-8") as handle: return handle.read()
def _uimeta(uri,inv,done): return {"ui":{"resourceUri":uri,"visibility":["model","app"]},"ui/resourceUri":uri,"openai/outputTemplate":uri,"openai/widgetAccessible":True,
  "openai/toolInvocation/invoking":inv,"openai/toolInvocation/invoked":done}
O=lambda props,req=(): {"type":"object","properties":props,"required":list(req),"additionalProperties":False}
SET_ITEM={"type":"object","properties":{
  "exercise":{"type":"string","description":"Exercise name as the user said it, any language (e.g. 'push ups', '卧推'). Server normalizes."},
  "reps":{"type":"integer","minimum":1},"weight":{"type":"number","minimum":0,"description":"Omit for bodyweight"},
  "unit":{"type":"string","enum":["lb","kg"],"description":"Defaults to settings.unit (lb if unset)"},
  "count":{"type":"integer","minimum":1,"description":"Number of identical sets (e.g. 3x8 -> count 3). Default 1"},
  "session_item_id":{"type":"integer","minimum":1,"description":"Optional active-workout item this completed work belongs to"},
  "duration_s":{"type":"integer","minimum":1},"distance_m":{"type":"number","minimum":0},"rpe":{"type":"number","minimum":1,"maximum":10},
  "is_warmup":{"type":"boolean"},"note":{"type":"string"},"raw_text":{"type":"string","description":"Original user phrase"}},
  "required":["exercise"],"additionalProperties":False}
SEG={"type":"object","properties":{"kind":{"type":"string","enum":["work","rest"]},"seconds":{"type":"integer","minimum":1,"maximum":3600},"exercise":{"type":"string"}},"required":["kind","seconds"],"additionalProperties":False}
TOOLS=[
 {"name":"get_context","title":"Get workout context","description":"Settings and recent workout facts. Setup is optional and repeatable: never block logging, planning, reports, or coaching because a preference is missing. timezone_missing=true means settings.timezone is unset and day boundaries use the host's local zone: ask the user once for their IANA timezone and save it with update_settings.",
  "inputSchema":O({"days":{"type":"integer","minimum":1,"maximum":365,"default":14}}),"annotations":{"readOnlyHint":True}},
 {"name":"update_settings","title":"Update settings","description":"Repeatable setup: update only keys the user explicitly supplied. Supports units, timezone (IANA name, e.g. America/New_York), schedule, goals, equipment, constraints, reminder toggles, nutrition preference, maintenance_kcal (user-confirmed daily maintenance energy/TDEE in kcal; null clears it), and structured HIIT defaults. Never reset omitted keys.",
  "inputSchema":O({"settings":{"type":"object","additionalProperties":True}},["settings"])},
 {"name":"resolve_exercise","title":"Resolve exercise names","description":"Map names/aliases (Chinese or English) to canonical exercises. status: matched (exact|fuzzy) / ambiguous (candidates) / not_found.",
  "inputSchema":O({"names":{"type":"array","items":{"type":"string"},"minItems":1}},["names"]),"annotations":{"readOnlyHint":True}},
 {"name":"add_alias","title":"Add exercise alias","description":"Teach a new alias for an existing exercise (after confirming with the user).",
  "inputSchema":O({"exercise":{"type":["string","integer"],"description":"exercise_id or canonical name"},"alias":{"type":"string"},"lang":{"type":"string","enum":["en","zh","other"]}},["exercise","alias"])},
 {"name":"add_exercise","title":"Add exercise","description":"Create a new canonical exercise (only after the user confirms it is not an existing one).",
  "inputSchema":O({"canonical":{"type":"string"},"category":{"type":"string","enum":["strength","cardio","hiit","mobility","hike"]},"name_zh":{"type":"string"},
    "muscle_groups":{"type":"array","items":{"type":"string"}},"equipment":{"type":"string"},"is_bodyweight":{"type":"boolean"},
    "tracking":{"type":"string","enum":["reps_weight","reps","duration","distance"]},"aliases":{"type":"array","items":{"type":"string"}}},["canonical","category"])},
 {"name":"log_sets","title":"Log sets","description":"Store one or more sets parsed from the user's message. Atomic: if any exercise is unknown/ambiguous nothing is saved and status=needs_confirmation with candidates. A first log starts a workout; more than 15 minutes of inactivity closes it. Returns e1RM (Epley) and PR flags. Optional session_id: log only into that workout; if it is not the active one (completed, abandoned, missing, or another workout is active) nothing is saved, no workout is started, and the result has status rejected with reason and note. Without session_id, logs into whatever workout is active.",
  "inputSchema":O({"sets":{"type":"array","items":SET_ITEM,"minItems":1},"session_id":{"type":"integer","minimum":1,"description":"Workout these sets belong to (the guided runner passes its own); reject instead of logging elsewhere"},"performed_at":{"type":"string","description":"ISO time; a value with no offset is user-local (settings.timezone). Default now"},"notes":{"type":"string"},
    "session_weight":{"type":"number","exclusiveMinimum":0},"weight_unit":{"type":"string","enum":["lb","kg"]},"planned_duration_min":{"type":"number","exclusiveMinimum":0}},["sets"]),"_meta":APP_TOOL},
 {"name":"list_recent","title":"List recent sets","description":"Most recent sets, newest first (set_id usable with delete_set).",
  "inputSchema":O({"limit":{"type":"integer","minimum":1,"maximum":100,"default":10},"exercise":{"type":"string"}}),"annotations":{"readOnlyHint":True}},
 {"name":"delete_set","title":"Delete / undo set","description":"Delete a set by set_id, or the most recently logged set if set_id omitted (undo). Removes the session if it becomes empty.",
  "inputSchema":O({"set_id":{"type":"integer"}}),"annotations":{"destructiveHint":True}},
 {"name":"update_set","title":"Correct set","description":"Correct one logged set and recalculate its workout estimate.","inputSchema":O({"set_id":{"type":"integer"},"exercise":{"type":"string"},"reps":{"type":"integer","minimum":1},"weight":{"type":"number","minimum":0},"unit":{"type":"string","enum":["lb","kg"]},"duration_s":{"type":"integer","minimum":1},"distance_m":{"type":"number","minimum":0},"rpe":{"type":"number","minimum":1,"maximum":10},"is_warmup":{"type":"boolean"}},("set_id",))},
 {"name":"query_stats","title":"Query stats","description":"Time series grouped by day/week (Monday start)/month in the user-local timezone. metric: e1rm|volume|frequency|best_set|reps. Dates YYYY-MM-DD inclusive.",
  "inputSchema":O({"metric":{"type":"string","enum":["e1rm","volume","frequency","best_set","reps"]},"exercise":{"type":"string"},"from_date":{"type":"string"},"to_date":{"type":"string"},
    "group_by":{"type":"string","enum":["day","week","month"],"default":"week"}},["metric"]),"annotations":{"readOnlyHint":True}},
 {"name":"hiit_timer","title":"HIIT Timer","description":"Show an interactive HIIT interval timer. Each round runs the segments in order (final rest of last round skipped). Pass routine_id to time a saved hiit routine. Completion is logged automatically via log_hiit from the UI.",
  "inputSchema":O({"name":{"type":"string"},"rounds":{"type":"integer","minimum":1,"maximum":100,"description":"Circuit repeats. Omit to use the default: 20 for ad hoc segments; with routine_id, the routine's rounds (or 1). With routine_id it must be one of the routine's round_options if it has them"},"segments":{"type":"array","items":SEG,"minItems":1},
    "routine_id":{"type":"integer","description":"Build the timer from a saved hiit routine instead of segments: one work interval per exercise with its rest after it"},
    "lang":{"type":"string","enum":["en","zh"],"default":"en","description":"With routine_id: exercise-name language for the timer labels"},
    "warmup_s":{"type":"integer","minimum":0,"default":10},"cooldown_s":{"type":"integer","minimum":0,"default":60}}),
  "_meta":_uimeta(HIIT_URI,"Setting up timer…","Timer ready")},
 {"name":"log_hiit","title":"Log HIIT result","description":"Record a HIIT timer result (called by the timer UI, or by the model when the user says they finished on a client without UI).",
  "inputSchema":O({"hiit_id":{"type":"integer"},"completed_rounds":{"type":"integer","minimum":0},"total_seconds":{"type":"integer","minimum":0},"status":{"type":"string","enum":["completed","aborted"]}},["hiit_id","completed_rounds","total_seconds"]),
  "_meta":{"ui":{"visibility":["model","app"]}}},
 {"name":"log_activity","title":"Log hike / cardio","description":"Log a whole cardio session or hike (not individual sets). Distance defaults to mi/ft when unit=lb, km/m when kg.",
  "inputSchema":O({"type":{"type":"string","enum":["hike","run","walk","cycle","swim","row","cardio","other"]},"duration_min":{"type":"number","exclusiveMinimum":0},
    "distance":{"type":"number","minimum":0},"distance_unit":{"type":"string","enum":["km","mi"]},"elevation_gain":{"type":"number","minimum":0},"elevation_unit":{"type":"string","enum":["m","ft"]},
    "performed_at":{"type":"string","description":"ISO time; a value with no offset is user-local (settings.timezone). Default now"},"rpe":{"type":"number","minimum":1,"maximum":10},"title":{"type":"string"},"notes":{"type":"string"},"session_item_id":{"type":"integer"},
    "session_weight":{"type":"number","exclusiveMinimum":0},"weight_unit":{"type":"string","enum":["lb","kg"]}},["type","duration_min"])},
 {"name":"update_activity","title":"Correct cardio / hike","description":"Correct an actual cardio or hike workout and recalculate its calorie estimate.","inputSchema":O({"session_id":{"type":"integer"},"duration_min":{"type":"number","exclusiveMinimum":0},"distance":{"type":"number","minimum":0},"distance_unit":{"type":"string","enum":["km","mi"]},"elevation_gain":{"type":"number","minimum":0},"elevation_unit":{"type":"string","enum":["m","ft"]},"rpe":{"type":"number","minimum":1,"maximum":10},"title":{"type":"string"},"notes":{"type":"string"}},("session_id","duration_min"))},
 {"name":"delete_session","title":"Delete session","description":"Delete a whole session (and its sets) by session_id.","inputSchema":O({"session_id":{"type":"integer"}},["session_id"]),"annotations":{"destructiveHint":True}},
 {"name":"suggest_next","title":"Suggest next sets","description":"Progressive-overload prescription for one exercise (double progression in the rep range; Epley e1RM; deload after 2 missed sessions or >5% e1RM drop; -10% after >14 days off). status: no_history|add_reps|increase_weight|deload|reset|progress. Includes equipment_status (available|unknown|unavailable).",
  "inputSchema":O({"exercise":{"type":"string"},"target_rep_range":{"type":"string","description":"e.g. '5-8'; default settings.rep_range_default or 8-12"},"sets":{"type":"integer","minimum":1,"maximum":10}},["exercise"]),"annotations":{"readOnlyHint":True}},
 {"name":"daily_plan_context","title":"Daily plan context","description":"Facts for composing today's workout: week progress vs target, muscle-group recovery (48h), cardio minutes vs last week (≤10% growth cap), hikes, a rule-based suggested_focus (upper|lower|full_body|cardio_hiit|hike|rest_or_mobility|optional_light) with rationale, and candidate exercises with suggest_next prescriptions. Handles unknown equipment (marks equipment_status=unknown).",
  "inputSchema":O({"date":{"type":"string","description":"YYYY-MM-DD local; default today"}}),"annotations":{"readOnlyHint":True}},
 {"name":"report","title":"Workout report","description":"Daily/weekly/monthly/quarterly/annual/all-time report with workout and nutrition totals, goals, confidence, comparison, and fixed trend series. energy_balance gives the energy deficit vs settings.maintenance_kcal (maintenance minus confirmed intake; never vs workout calories) and the target deficit from the user's calories_kcal maximum goal: day totals, or for longer periods the average daily deficit and days meeting the target. Always summarize the structured results in text; a dashboard is attached only on compatible MCP Apps clients.",
  "inputSchema":O({"period":{"type":"string","enum":["day","week","month","quarter","year","all"]},"anchor_date":{"type":"string","description":"YYYY-MM-DD inside the period; default today"}},["period"]),
  "annotations":{"readOnlyHint":True},"_meta":_uimeta(REPORT_URI,"Building report…","Report ready")},
 {"name":"show_progress","title":"Show progress chart","description":"Chart progress for one exercise or overall training. Complete structured data is returned on every client; a chart is attached only on compatible MCP Apps clients.",
  "inputSchema":O({"exercise":{"type":"string"},"metric":{"type":"string","enum":["e1rm","volume","best_set","reps","sets","days","cardio_minutes"]},"range":{"type":"string","enum":["4w","12w","6m","1y","all"],"default":"12w"}}),
  "annotations":{"readOnlyHint":True},"_meta":_uimeta(PROG_URI,"Drawing chart…","Chart ready")},
]

ROUTINE_ITEM=O({"position":{"type":"integer","minimum":1},"exercise":{"type":"string"},"exercise_id":{"type":"integer"},"activity_name":{"type":"string"},
  "target_sets":{"type":"integer","minimum":1},"target_reps":{"type":"integer","minimum":1},"target_weight":{"type":"number","minimum":0},
  "unit":{"type":"string","enum":["lb","kg"]},"target_duration_s":{"type":"integer","minimum":1},"target_distance_m":{"type":"number","minimum":0},"notes":{"type":"string"}})
SESSION_ITEM=ROUTINE_ITEM
HIIT_ROUTINE_ITEM={**ROUTINE_ITEM,"properties":{**ROUTINE_ITEM["properties"],
  "work_seconds":{"type":"integer","minimum":1,"maximum":3600,"description":"hiit routines only: work interval for this exercise (default 40, or target_duration_s)"},
  "rest_seconds":{"type":"integer","minimum":0,"maximum":3600,"description":"Rest after this exercise (0 = no rest). hiit: default 20. standard: rest after each set in guided_routine_timer (default 90)"}}}
ROUTINE_TYPE={"type":"string","enum":["standard","hiit"]}
ROUTINE_ROUNDS={"routine_type":{**ROUTINE_TYPE,"description":"hiit routines can be timed with hiit_timer(routine_id); default standard"},
  "rounds":{"type":"integer","minimum":1,"maximum":100,"description":"hiit only: circuit repeats (default 1 pass)"},
  "round_options":{"type":"array","items":{"type":"integer","minimum":1,"maximum":100},"description":"hiit only: 2-6 round counts offered by the HTML timer's round selector; [] clears"}}
NUTRIENT_PROPS={"consumed_at":{"type":"string","description":"ISO time; a value with no offset is user-local. Default now"},"capture_timezone":{"type":"string","description":"IANA zone where the meal was eaten; default the user's timezone setting. Sets local_date"},"calories_kcal":{"type":["number","null"],"minimum":0},
  "protein_g":{"type":["number","null"],"minimum":0},"carbohydrates_g":{"type":["number","null"],"minimum":0},"fat_g":{"type":["number","null"],"minimum":0},
  "fiber_g":{"type":["number","null"],"minimum":0},"saturated_fat_g":{"type":["number","null"],"minimum":0},"sugar_g":{"type":["number","null"],"minimum":0},
  "sodium_mg":{"type":["number","null"],"minimum":0},"estimation_method":{"type":"string","enum":["usda_hybrid","model_only"]},
  "confidence":{"type":"string","enum":["higher","moderate","rough"]},"usda_contributed":{"type":"boolean"},"estimator_version":{"type":"string"},
  "source_components":{"type":"integer","minimum":1},"uncertainty_flags":{"type":"array","items":{"type":"string","enum":["portion_uncertain","preparation_unknown","ingredients_unknown","image_unclear","usda_match_uncertain"]}}}
NUTRIENT_DRAFT=O(NUTRIENT_PROPS,("estimation_method","confidence"))
TOOLS.extend([
 {"name":"create_goal","title":"Create goal","description":"Save a user-confirmed workout or nutrition goal. Direct user goals (origin user) supersede active recommended goals for the same metric; check list_goals first and use update_goal instead of creating a duplicate. Nutrition goals use period day. Range goals need lower_value and upper_value; other semantics need target_value.","inputSchema":O({"domain":{"type":"string","enum":["workout","nutrition"]},"period":{"type":"string","enum":["day","week","month"]},"metric":{"type":"string","enum":sorted(goals.WORKOUT_METRICS|goals.NUTRITION_METRICS),"description":"Nutrition: "+", ".join(sorted(goals.NUTRITION_METRICS))+". Workout: "+", ".join(sorted(goals.WORKOUT_METRICS))},"semantics":{"type":"string","enum":["minimum","maximum","exact","range"]},"target_value":{"type":"number"},"lower_value":{"type":"number"},"upper_value":{"type":"number"},"unit":{"type":"string"},"display_precision":{"type":"integer"},"origin":{"type":"string","enum":["user","recommended"],"description":"recommended = saved unchanged from recommend_nutrition_goals; user = the user's own or adjusted value. Default user"},"recommendation_version":{"type":"string","description":"version returned by recommend_nutrition_goals"},"assumptions":{"type":"array","items":{},"description":"Inputs and adjustments behind the value, e.g. [\"age 40\", \"objective fat_loss\"]"},"effective_start_date":{"type":"string","description":"YYYY-MM-DD local date"}},("domain","period","metric","semantics","unit","effective_start_date"))},
 {"name":"update_goal","title":"Update goal","description":"Update explicitly supplied fields on an existing goal; progress and streaks are recalculated from facts.","inputSchema":O({"goal_id":{"type":"integer"},"semantics":{"type":"string","enum":["minimum","maximum","exact","range"]},"target_value":{"type":"number"},"lower_value":{"type":"number"},"upper_value":{"type":"number"},"unit":{"type":"string"},"display_precision":{"type":"integer"},"effective_start_date":{"type":"string"},"active":{"type":"boolean"}},("goal_id",))},
 {"name":"list_goals","title":"List goals","description":"List active or historical goals.","inputSchema":O({"include_inactive":{"type":"boolean"},"domain":{"type":"string","enum":["workout","nutrition"]}}),"annotations":{"readOnlyHint":True}},
 {"name":"delete_goal","title":"Deactivate goal","description":"Deactivate one goal after the user requests it.","inputSchema":O({"goal_id":{"type":"integer"}},("goal_id",)),"annotations":{"destructiveHint":True}},
 {"name":"goal_progress","title":"Goal progress","description":"Calculate current progress from authoritative workout and nutrition records.","inputSchema":O({"goal_id":{"type":"integer"},"anchor_date":{"type":"string"}},("goal_id",)),"annotations":{"readOnlyHint":True}},
 {"name":"add_body_weight","title":"Record body weight","description":"Record optional body weight for trend and calorie estimation.","inputSchema":O({"value":{"type":"number","exclusiveMinimum":0},"unit":{"type":"string","enum":["lb","kg"]},"measured_at":{"type":"string","description":"ISO time; a value with no offset is user-local. Default now"}},("value","unit"))},
 {"name":"list_body_weight","title":"List body weight","description":"List optional body-weight entries.","inputSchema":O({"limit":{"type":"integer","minimum":1,"maximum":1000}}),"annotations":{"readOnlyHint":True}},
 {"name":"update_body_weight","title":"Correct body weight","description":"Correct one body-weight entry and recalculate affected calorie estimates.","inputSchema":O({"weight_id":{"type":"integer"},"value":{"type":"number","exclusiveMinimum":0},"unit":{"type":"string","enum":["lb","kg"]},"measured_at":{"type":"string","description":"ISO time; a value with no offset is user-local"}},("weight_id","value","unit"))},
 {"name":"delete_body_weight","title":"Delete body weight","description":"Delete one body-weight entry and recalculate affected calorie estimates.","inputSchema":O({"weight_id":{"type":"integer"}},("weight_id",)),"annotations":{"destructiveHint":True}},
 {"name":"create_routine","title":"Create workout routine","description":"Create a reusable routine directly or from a completed workout the user explicitly asked to save. The source workout is unchanged.","inputSchema":O({"name":{"type":"string"},"description":{"type":"string"},"estimated_duration_min":{"type":"number","exclusiveMinimum":0},"source_session_id":{"type":"integer"},"items":{"type":"array","items":HIIT_ROUTINE_ITEM,"minItems":1},**ROUTINE_ROUNDS},("name",))},
 {"name":"list_routines","title":"List routines","description":"List reusable workout routines, optionally only one routine_type.","inputSchema":O({"include_archived":{"type":"boolean"},"routine_type":ROUTINE_TYPE}),"annotations":{"readOnlyHint":True}},
 {"name":"get_routine","title":"Get routine","description":"Retrieve one routine and ordered targets.","inputSchema":O({"routine_id":{"type":"integer"}},("routine_id",)),"annotations":{"readOnlyHint":True}},
 {"name":"update_routine","title":"Update routine","description":"Edit future routine starts; existing workout snapshots do not change.","inputSchema":O({"routine_id":{"type":"integer"},"name":{"type":"string"},"description":{"type":"string"},"estimated_duration_min":{"type":"number"},"items":{"type":"array","items":HIIT_ROUTINE_ITEM,"minItems":1},**ROUTINE_ROUNDS},("routine_id",))},
 {"name":"archive_routine","title":"Archive routine","description":"Archive a routine without breaking historical workout links.","inputSchema":O({"routine_id":{"type":"integer"}},("routine_id",)),"annotations":{"destructiveHint":True}},
 {"name":"restore_routine","title":"Restore routine","description":"Restore an archived routine.","inputSchema":O({"routine_id":{"type":"integer"}},("routine_id",))},
 {"name":"start_routine","title":"Start routine","description":"Start a saved routine by creating an independent active-workout snapshot. Never replace an existing active workout.","inputSchema":O({"routine_id":{"type":"integer"},"idempotency_key":{"type":"string"}},("routine_id",))},
 {"name":"guided_routine_timer","title":"Guided routine runner","description":"Start a saved standard routine (same rules as start_routine: never replaces an active workout) and run it set by set: each set is a work step (count-up with Done, or a countdown when the item has target_duration_s) followed by a rest countdown (item rest_seconds, else default_rest_s), no rest after the last step. MCP Apps clients (result has ui_auto_log=true): the attached runner logs each set the moment the user marks it Done (log_sets with performed_at) and calls finish_workout when the user ends or completes the routine, so do not render a page, ask for a summary, or log those sets again. Other clients: render the steps with server/render_timer_html.py --config and log only the sets the user marks Done, using completion_handoff. HIIT routines are rejected: use hiit_timer.",
  "inputSchema":O({"routine_id":{"type":"integer"},"lang":{"type":"string","enum":["en","zh"],"default":"en","description":"Exercise-name and UI language for the runner page"},
    "default_rest_s":{"type":"integer","minimum":0,"maximum":3600,"default":90,"description":"Rest after a working set when the item has no rest_seconds"},"idempotency_key":{"type":"string"}},("routine_id",)),
  "_meta":_uimeta(GUIDED_URI,"Starting guided workout…","Guided runner ready")},
 {"name":"start_workout","title":"Start accepted plan","description":"Start an accepted conversational plan as an active snapshot; generating or showing a plan must not call this tool.","inputSchema":O({"mode":{"type":"string","enum":["conversational_plan"]},"title":{"type":"string"},"items":{"type":"array","items":SESSION_ITEM},"idempotency_key":{"type":"string"},
    "session_weight":{"type":"number","exclusiveMinimum":0},"weight_unit":{"type":"string","enum":["lb","kg"]},"planned_duration_min":{"type":"number","exclusiveMinimum":0}},("mode",))},
 {"name":"get_active_workout","title":"Resume active workout","description":"Return the one active workout, completed work, and pending items.","inputSchema":O({}),"annotations":{"readOnlyHint":True}},
 {"name":"add_workout_item","title":"Add active-workout item","description":"Add an ad-hoc item only to the active workout snapshot; the source routine is unchanged.","inputSchema":O({"item":ROUTINE_ITEM},("item",))},
 {"name":"replace_workout_item","title":"Replace active-workout item","description":"Pivot by replacing one active-workout item without changing the source routine.","inputSchema":O({"item_id":{"type":"integer"},"replacement":ROUTINE_ITEM},("item_id","replacement"))},
 {"name":"reorder_workout_items","title":"Reorder active-workout items","description":"Reorder every non-replaced item in the active workout snapshot.","inputSchema":O({"item_ids":{"type":"array","items":{"type":"integer"},"minItems":1}},("item_ids",))},
 {"name":"set_workout_item_status","title":"Update active-workout item","description":"Mark an item pending, completed, or skipped in the active workout snapshot.","inputSchema":O({"item_id":{"type":"integer"},"status":{"type":"string","enum":["pending","completed","skipped"]}},("item_id","status")),"_meta":APP_TOOL},
 {"name":"move_set","title":"Move set to workout","description":"Correct timeout grouping by moving one logged set to another non-abandoned workout and recalculating both.","inputSchema":O({"set_id":{"type":"integer"},"target_session_id":{"type":"integer"}},("set_id","target_session_id"))},
 {"name":"merge_workouts","title":"Merge adjacent workouts","description":"Merge two adjacent completed workouts only after explicit confirmation; recalculates derived values.","inputSchema":O({"source_session_id":{"type":"integer"},"target_session_id":{"type":"integer"},"confirmed":{"type":"boolean","const":True}},("source_session_id","target_session_id","confirmed")),"annotations":{"destructiveHint":True}},
 {"name":"finish_workout","title":"Finish workout","description":"Finish the active workout immediately. Optional session_id: finish only that workout; if it is not the active one (it already ended, e.g. closed after 15 minutes idle), nothing changes and the result has status already_finished with a note, leaving any other active workout untouched. Without session_id, finishes whatever workout is active.","inputSchema":O({"session_id":{"type":"integer","minimum":1}}),"_meta":APP_TOOL},
 {"name":"abandon_workout","title":"Abandon workout","description":"Abandon the active workout while preserving its history.","inputSchema":O({}),"annotations":{"destructiveHint":True}},
 {"name":"nutrition_status","title":"Nutrition estimation status","description":"Report whether USDA hybrid estimation is available and whether model-only mode was acknowledged. Never returns the key.","inputSchema":O({}),"annotations":{"readOnlyHint":True}},
 {"name":"search_usda","title":"Search USDA nutrients","description":"Search FoodData Central using an agent-produced text candidate only. Never send an image. Failure returns a model-only fallback.","inputSchema":O({"query":{"type":"string"}},("query",)),"annotations":{"readOnlyHint":True}},
 {"name":"validate_nutrition","title":"Validate nutrition draft","description":"Validate and normalize a nutrient-only draft without saving. Food names, portions, images, descriptions, queries, and identifiers are forbidden.","inputSchema":O({"draft":NUTRIENT_DRAFT},("draft",)),"annotations":{"readOnlyHint":True}},
 {"name":"save_nutrition","title":"Save confirmed nutrition","description":"Save only a final nutrient-only draft after the user explicitly confirms it in this conversation. Set confirmed=true only after confirmation.","inputSchema":O({"draft":NUTRIENT_DRAFT,"confirmed":{"type":"boolean","const":True}},("draft","confirmed"))},
 {"name":"list_nutrition","title":"List nutrition","description":"List nutrient-only entries.","inputSchema":O({"from_date":{"type":"string"},"to_date":{"type":"string"},"limit":{"type":"integer"}}),"annotations":{"readOnlyHint":True}},
 {"name":"update_nutrition","title":"Correct nutrition","description":"Update a corrected nutrient-only draft only after fresh user confirmation.","inputSchema":O({"entry_id":{"type":"integer"},"draft":NUTRIENT_DRAFT,"confirmed":{"type":"boolean","const":True}},("entry_id","draft","confirmed"))},
 {"name":"delete_nutrition","title":"Delete nutrition","description":"Delete one confirmed nutrition entry after the user requests it.","inputSchema":O({"entry_id":{"type":"integer"}},("entry_id",)),"annotations":{"destructiveHint":True}},
 {"name":"nutrition_daily_totals","title":"Daily nutrition totals","description":"Return confirmed daily nutrient totals without treating unknown nutrients as zero.","inputSchema":O({"local_date":{"type":"string"}},("local_date",)),"annotations":{"readOnlyHint":True}},
 {"name":"recommend_nutrition_goals","title":"Recommend nutrition goals","description":"Prepare unsaved educational daily nutrition recommendations for adults (18-100) from explicit inputs only; never infer sex or age. age is required; calories also need weight_kg, height_cm, sex, activity_level and objective (otherwise a partial set plus missing_for_energy is returned). Convert lb/in to kg/cm first. Goal weight and target date are not inputs. Returns recommendations (calories_kcal, maintenance_kcal, protein_g, protein_g_range, fiber_g, saturated_fat_g_max, sodium_mg_max, and calories_kcal_max for fat_loss), assumptions, and version. fat_loss calories are a daily maximum goal, not a range. maintenance_kcal is the estimated TDEE; save it with update_settings only after the user confirms. Show assumptions and ask before create_goal. Any guardrail flag set to true declines with a referral to clinical guidance.","inputSchema":O({"inputs":{"type":"object","additionalProperties":True,"required":["age"],"properties":{
   "age":{"type":"number","minimum":18,"maximum":100,"description":"Age in years, as stated by the user (alias age_years is accepted)"},
   "sex":{"type":"string","enum":list(nutrition.SEXES),"description":"Sex used for the energy equation, only as stated by the user"},
   "height_cm":{"type":"number","exclusiveMinimum":0},"weight_kg":{"type":"number","exclusiveMinimum":0,"description":"Current body weight"},
   "activity_level":{"type":"string","enum":list(nutrition.ACTIVITY_FACTORS)},
   "objective":{"type":"string","enum":list(nutrition.OBJECTIVES),"description":"fat_loss = about 350 kcal/day below maintenance, muscle_gain = about 250 above. Aliases lose/weight_loss and maintain are accepted"},
   **{flag:{"type":"boolean","description":"Guardrail: true declines personalized recommendations"} for flag in nutrition.GUARDRAIL_FLAGS}}}},("inputs",)),"annotations":{"readOnlyHint":True}},
])
RESOURCES=[{"uri":HIIT_URI,"name":"hiit_timer_view","title":"HIIT Timer","description":"Configurable HIIT timer","mimeType":MIME,"_meta":RMETA},
 {"uri":PROG_URI,"name":"progress_view","title":"Progress chart","description":"Progress charts","mimeType":MIME,"_meta":{**RMETA,"openai/widgetDescription":"Workout progress chart"}},
 {"uri":REPORT_URI,"name":"report_view","title":"Workout report","description":"Period report","mimeType":MIME,"_meta":{**RMETA,"openai/widgetDescription":"Workout report"}},
 {"uri":GUIDED_URI,"name":"guided_runner_view","title":"Guided routine runner","description":"Set-by-set runner that logs each Done set","mimeType":MIME,"_meta":{**RMETA,"openai/widgetDescription":"Guided routine runner that logs each set marked Done"}}]

def _summary(name,r,ui=False):
  if name=="log_sets":
    if r["status"]=="rejected": return r["note"]
    if r["status"]!="logged": return "Nothing saved — needs confirmation: "+json.dumps(r["problems"],ensure_ascii=False)
    return "Logged: "+"; ".join(f"{x['exercise']} {x['reps'] or ''}{' reps' if x['reps'] else ''}{(' @ '+str(x['weight'])+x['unit']) if x['weight'] is not None else ''}{' (PR!)' if x['is_pr'] else ''}" for x in r["logged"])
  if name=="hiit_timer":
    return f"HIIT timer ready (id {r['hiit_id']}): {r['rounds']} rounds × "+", ".join(f"{s.get('exercise') or s['kind']} {s['seconds']}s" for s in r["segments"])+f"; total {r['planned_seconds']//60}m{r['planned_seconds']%60:02d}s. Press Start."
  if name=="guided_routine_timer":
    head=f"Guided runner ready for workout #{r['session_id']} ({r['status']}): {r['work_sets']} sets from '{r['name']}'."
    if ui: return head+" The runner is attached: it logs each set when the user taps Done and finishes the workout when they end it. Do not render an offline page, ask for a summary, or log these sets again."
    return head+" Render it with render_timer_html.py --config."
  if name=="finish_workout" and r.get("status")=="already_finished": return r["note"]
  return None
def call_tool(c,name,a):
  a=a or {}
  if name=="get_context": return core.get_context(c,**a)
  if name=="update_settings": return core.update_settings(c,a.get("settings"))
  if name=="resolve_exercise": return core.resolve_exercise(c,a.get("names") or [])
  if name=="add_alias": return core.add_alias(c,**a)
  if name=="add_exercise": return core.add_exercise(c,**a)
  if name=="log_sets": return core.log_sets(c,a.get("sets"),a.get("performed_at"),a.get("notes"),session_id=a.get("session_id"))
  if name=="list_recent": return core.list_recent(c,**a)
  if name=="delete_set": return core.delete_set(c,a.get("set_id"))
  if name=="update_set": return core.update_set(c,**a)
  if name=="query_stats": return core.query_stats(c,**a)
  if name=="hiit_timer": return core.create_hiit(c,**a)
  if name=="log_hiit": return core.log_hiit(c,**a)
  if name=="log_activity": return core.log_activity(c,**a)
  if name=="update_activity": return core.update_activity(c,**a)
  if name=="delete_session": return core.delete_session(c,a.get("session_id"))
  if name=="suggest_next": return coach.suggest_next(c,**a)
  if name=="daily_plan_context": return coach.daily_plan_context(c,**a)
  if name=="report": return reports.report(c,**a)
  if name=="show_progress": return reports.show_progress(c,**a)
  if name=="create_goal": return goals.create_goal(c,**a)
  if name=="update_goal":
    goal_id=a.pop("goal_id")
    return goals.update_goal(c,goal_id,**a)
  if name=="list_goals": return goals.list_goals(c,**a)
  if name=="delete_goal": return goals.delete_goal(c,**a)
  if name=="goal_progress": return goals.goal_progress(c,**a)
  if name=="add_body_weight": return profile.add_body_weight(c,**a)
  if name=="list_body_weight": return profile.list_body_weight(c,**a)
  if name=="update_body_weight": return profile.update_body_weight(c,**a)
  if name=="delete_body_weight": return profile.delete_body_weight(c,**a)
  if name=="create_routine": return routines.create_routine(c,**a)
  if name=="list_routines": return routines.list_routines(c,**a)
  if name=="get_routine": return routines.get_routine(c,**a)
  if name=="update_routine": return routines.update_routine(c,**a)
  if name=="archive_routine": return routines.set_archived(c,a["routine_id"],True)
  if name=="restore_routine": return routines.set_archived(c,a["routine_id"],False)
  if name=="start_routine": return routines.start_routine(c,**a)
  if name=="guided_routine_timer": return routines.guided_routine_timer(c,**a)
  if name=="start_workout": return workouts.start_workout(c,**a)
  if name=="get_active_workout": return workouts.get_active(c)
  if name=="add_workout_item": return workouts.add_session_item(c,a["item"])
  if name=="replace_workout_item": return workouts.replace_session_item(c,a["item_id"],a["replacement"])
  if name=="reorder_workout_items": return workouts.reorder_session_items(c,a["item_ids"])
  if name=="set_workout_item_status": return workouts.set_session_item_status(c,a["item_id"],a["status"])
  if name=="move_set": return workouts.move_set(c,a["set_id"],a["target_session_id"])
  if name=="merge_workouts": return workouts.merge_sessions(c,**a)
  if name=="finish_workout": return workouts.finish_workout(c,session_id=a.get("session_id"))
  if name=="abandon_workout": return workouts.abandon_workout(c)
  if name=="nutrition_status": return nutrition.nutrition_status(c)
  if name=="search_usda": return nutrition.search_usda(**a)
  if name=="validate_nutrition": return nutrition.validate_draft(a["draft"],core.user_timezone_name(c))
  if name=="save_nutrition": return nutrition.save_entry(c,a["draft"],a.get("confirmed",False))
  if name=="list_nutrition": return nutrition.list_entries(c,**a)
  if name=="update_nutrition": return nutrition.update_entry(c,a["entry_id"],a["draft"],a.get("confirmed",False))
  if name=="delete_nutrition": return nutrition.delete_entry(c,**a)
  if name=="nutrition_daily_totals": return nutrition.daily_totals(c,a["local_date"])
  if name=="recommend_nutrition_goals": return nutrition.recommend_goals(a["inputs"])
  raise KeyError(f"unknown tool: {name}")

class Server:
  def __init__(s,db_path=None): s.c=dbm.connect(db_path); s.ui_supported=False
  def handle(s,m):
    meth=m.get("method"); p=m.get("params") or {}
    workouts.reconcile_active(s.c)
    if meth=="initialize":
      pv=p.get("protocolVersion")
      cc=p.get("capabilities") or {}; ext=cc.get("extensions") or cc.get("experimental") or {}
      s.ui_supported="io.modelcontextprotocol/ui" in ext
      caps={"tools":{"listChanged":False}}
      if s.ui_supported: caps.update({"resources":{"listChanged":False},"extensions":{"io.modelcontextprotocol/ui":{"mimeTypes":[MIME]}}})
      return {"protocolVersion":pv if pv in("2024-11-05","2025-03-26","2025-06-18","2025-11-25") else "2025-06-18",
        "capabilities":caps,
        "serverInfo":{"name":NAME,"version":VERSION},
        "instructions":"Workout Bot is a private local fitness assistant. Use tools for authoritative writes and numbers. Setup is optional and repeatable; never block logging, planning, reports, or coaching on missing preferences. Automatically log completed work, never log a proposed set as completed, confirm unknown exercises, and require explicit confirmation before saving nutrient-only nutrition drafts or destructive multi-record changes."}
    if meth=="ping": return {}
    if meth=="tools/list":
      tools=json.loads(json.dumps(TOOLS))
      if not s.ui_supported:
        for tool in tools: tool.pop("_meta",None)
      return {"tools":tools}
    if meth=="resources/list": return {"resources":RESOURCES if s.ui_supported else []}
    if meth=="resources/templates/list": return {"resourceTemplates":[]}
    if meth=="prompts/list": return {"prompts":[]}
    if meth=="resources/read":
      if not s.ui_supported: raise KeyError("MCP Apps resources were not negotiated")
      u=p.get("uri")
      if u not in UI_FILES: raise KeyError("unknown resource")
      return {"contents":[{"uri":u,"mimeType":MIME,"text":_ui_html(u),"_meta":RMETA}]}
    if meth=="tools/call":
      name=p.get("name")
      if name not in {t["name"] for t in TOOLS}: raise KeyError(f"unknown tool: {name}")
      try: r=call_tool(s.c,name,p.get("arguments"))
      except (UserError,TypeError,ValueError) as e:
        s.c.rollback(); return {"content":[{"type":"text","text":f"Error: {e}"}],"isError":True}
      ui=s.ui_supported and name in TOOL_UI
      if ui and name=="guided_routine_timer": r={**r,"ui_auto_log":True}
      txt=_summary(name,r,ui)
      body=json.dumps(r,ensure_ascii=False)
      out={"content":[{"type":"text","text":(txt+"\n"+body) if txt else body}],"structuredContent":r}
      if ui: out["_meta"]={"ui":{"resourceUri":TOOL_UI[name]}}
      return out
    raise NotImplementedError(meth)
  def respond(s,m):
    if not isinstance(m,dict) or "id" not in m or "method" not in m: return None
    try: return {"jsonrpc":"2.0","id":m["id"],"result":s.handle(m)}
    except NotImplementedError as e: return {"jsonrpc":"2.0","id":m["id"],"error":{"code":-32601,"message":f"method not found: {e}"}}
    except KeyError as e: return {"jsonrpc":"2.0","id":m["id"],"error":{"code":-32602,"message":str(e).strip("'\"")}}
    except Exception as e:
      traceback.print_exc(file=sys.stderr); return {"jsonrpc":"2.0","id":m["id"],"error":{"code":-32603,"message":str(e)}}
  def run_stdio(s):
    sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")
    print(f"{NAME}: stdio mode, db={s.c.execute('PRAGMA database_list').fetchone()[2]}",file=sys.stderr,flush=True)
    for line in sys.stdin:
      line=line.strip()
      if not line: continue
      try: body=json.loads(line)
      except Exception: out={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"parse error"}}
      else:
        out=([r for r in map(s.respond,body) if r] or None) if isinstance(body,list) else s.respond(body)
      if out is not None: sys.stdout.write(json.dumps(out,ensure_ascii=False)+"\n"); sys.stdout.flush()
if __name__=="__main__":
  a=sys.argv[1:]; dbp=a[a.index("--db")+1] if "--db" in a else None
  if "--stdio" not in a: print(__doc__,file=sys.stderr); sys.exit(2)
  Server(dbp).run_stdio()
