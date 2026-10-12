#!/usr/bin/env python3
"""Workout Tracking MCP server (Python stdlib only). Usage: server.py --stdio [--db PATH]   (env WORKOUT_DB also honored)"""
import json,sys,os,traceback
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import db as dbm, core, coach, reports, fallback
from core import UserError
HERE=os.path.dirname(os.path.abspath(__file__))
NAME,VERSION="workout-tracker","0.2.0"
MIME="text/html;profile=mcp-app"; HIIT_URI="ui://workout/hiit.html"
RMETA={"ui":{"prefersBorder":True,"csp":{"connectDomains":[],"resourceDomains":[]}},"openai/widgetDescription":"Configurable HIIT interval timer","openai/widgetPrefersBorder":True}
PROG_URI="ui://workout/progress.html"; REPORT_URI="ui://workout/report.html"
UI_FILES={HIIT_URI:"hiit.html",PROG_URI:"charts.html",REPORT_URI:"charts.html"}
FALLBACK_PAGE={"hiit_timer":"hiit","show_progress":"charts","report":"charts"}
TOOL_UI={"hiit_timer":HIIT_URI,"show_progress":PROG_URI,"report":REPORT_URI}
def _ui_html(uri): return open(os.path.join(HERE,"ui",UI_FILES[uri]),encoding="utf-8").read()
def _uimeta(uri,inv,done): return {"ui":{"resourceUri":uri,"visibility":["model","app"]},"ui/resourceUri":uri,"openai/outputTemplate":uri,"openai/widgetAccessible":True,
  "openai/toolInvocation/invoking":inv,"openai/toolInvocation/invoked":done}
O=lambda props,req=(): {"type":"object","properties":props,"required":list(req),"additionalProperties":False}
SET_ITEM={"type":"object","properties":{
  "exercise":{"type":"string","description":"Exercise name as the user said it, any language (e.g. 'push ups', '卧推'). Server normalizes."},
  "reps":{"type":"integer","minimum":1},"weight":{"type":"number","minimum":0,"description":"Omit for bodyweight"},
  "unit":{"type":"string","enum":["lb","kg"],"description":"Defaults to settings.unit (lb if unset)"},
  "count":{"type":"integer","minimum":1,"description":"Number of identical sets (e.g. 3x8 -> count 3). Default 1"},
  "duration_s":{"type":"integer","minimum":1},"distance_m":{"type":"number","minimum":0},"rpe":{"type":"number","minimum":1,"maximum":10},
  "is_warmup":{"type":"boolean"},"note":{"type":"string"},"raw_text":{"type":"string","description":"Original user phrase"}},
  "required":["exercise"],"additionalProperties":False}
SEG={"type":"object","properties":{"kind":{"type":"string","enum":["work","rest"]},"seconds":{"type":"integer","minimum":1,"maximum":3600},"exercise":{"type":"string"}},"required":["kind","seconds"],"additionalProperties":False}
TOOLS=[
 {"name":"get_context","title":"Get workout context","description":"Settings, onboarding status (onboarding_required lists missing keys: unit, days_per_week, goals), recent sessions, days trained this week, sets per muscle group. Call before planning or advising.",
  "inputSchema":O({"days":{"type":"integer","minimum":1,"maximum":365,"default":14}}),"annotations":{"readOnlyHint":True}},
 {"name":"update_settings","title":"Update settings","description":"Upsert user settings. Keys: unit('lb'|'kg'), days_per_week(1-7), goals[str], preferred_days[str], session_minutes, experience, equipment[str], timezone, bodyweight, injuries, rep_range_default('8-12').",
  "inputSchema":O({"settings":{"type":"object","additionalProperties":True}},["settings"])},
 {"name":"resolve_exercise","title":"Resolve exercise names","description":"Map names/aliases (Chinese or English) to canonical exercises. status: matched (exact|fuzzy) / ambiguous (candidates) / not_found.",
  "inputSchema":O({"names":{"type":"array","items":{"type":"string"},"minItems":1}},["names"]),"annotations":{"readOnlyHint":True}},
 {"name":"add_alias","title":"Add exercise alias","description":"Teach a new alias for an existing exercise (after confirming with the user).",
  "inputSchema":O({"exercise":{"type":["string","integer"],"description":"exercise_id or canonical name"},"alias":{"type":"string"},"lang":{"type":"string","enum":["en","zh","other"]}},["exercise","alias"])},
 {"name":"add_exercise","title":"Add exercise","description":"Create a new canonical exercise (only after the user confirms it is not an existing one).",
  "inputSchema":O({"canonical":{"type":"string"},"category":{"type":"string","enum":["strength","cardio","hiit","mobility","hike"]},"name_zh":{"type":"string"},
    "muscle_groups":{"type":"array","items":{"type":"string"}},"equipment":{"type":"string"},"is_bodyweight":{"type":"boolean"},
    "tracking":{"type":"string","enum":["reps_weight","reps","duration","distance"]},"aliases":{"type":"array","items":{"type":"string"}}},["canonical","category"])},
 {"name":"log_sets","title":"Log sets","description":"Store one or more sets parsed from the user's message. Atomic: if any exercise is unknown/ambiguous nothing is saved and status=needs_confirmation with candidates. Sets within 30 min join the same session. Returns e1RM (Epley) and PR flags.",
  "inputSchema":O({"sets":{"type":"array","items":SET_ITEM,"minItems":1},"performed_at":{"type":"string","description":"ISO time; naive = America/Los_Angeles. Default now"},"notes":{"type":"string"}},["sets"])},
 {"name":"list_recent","title":"List recent sets","description":"Most recent sets, newest first (set_id usable with delete_set).",
  "inputSchema":O({"limit":{"type":"integer","minimum":1,"maximum":100,"default":10},"exercise":{"type":"string"}}),"annotations":{"readOnlyHint":True}},
 {"name":"delete_set","title":"Delete / undo set","description":"Delete a set by set_id, or the most recently logged set if set_id omitted (undo). Removes the session if it becomes empty.",
  "inputSchema":O({"set_id":{"type":"integer"}}),"annotations":{"destructiveHint":True}},
 {"name":"query_stats","title":"Query stats","description":"Time series grouped by day/week(Mon start)/month in Charles' timezone. metric: e1rm|volume|frequency|best_set|reps. Dates YYYY-MM-DD inclusive.",
  "inputSchema":O({"metric":{"type":"string","enum":["e1rm","volume","frequency","best_set","reps"]},"exercise":{"type":"string"},"from_date":{"type":"string"},"to_date":{"type":"string"},
    "group_by":{"type":"string","enum":["day","week","month"],"default":"week"}},["metric"]),"annotations":{"readOnlyHint":True}},
 {"name":"hiit_timer","title":"HIIT Timer","description":"Show an interactive HIIT interval timer. Each round runs the segments in order (final rest of last round skipped). Completion is logged automatically via log_hiit from the UI.",
  "inputSchema":O({"name":{"type":"string"},"rounds":{"type":"integer","minimum":1,"maximum":100,"default":8},"segments":{"type":"array","items":SEG,"minItems":1},
    "warmup_s":{"type":"integer","minimum":0,"default":0},"cooldown_s":{"type":"integer","minimum":0,"default":0}}),
  "_meta":_uimeta(HIIT_URI,"Setting up timer…","Timer ready")},
 {"name":"log_hiit","title":"Log HIIT result","description":"Record a HIIT timer result (called by the timer UI, or by the model when the user says they finished on a client without UI).",
  "inputSchema":O({"hiit_id":{"type":"integer"},"completed_rounds":{"type":"integer","minimum":0},"total_seconds":{"type":"integer","minimum":0},"status":{"type":"string","enum":["completed","aborted"]}},["hiit_id","completed_rounds","total_seconds"]),
  "_meta":{"ui":{"visibility":["model","app"]}}},
 {"name":"log_activity","title":"Log hike / cardio","description":"Log a whole cardio session or hike (not individual sets). Distance defaults to mi/ft when unit=lb, km/m when kg.",
  "inputSchema":O({"type":{"type":"string","enum":["hike","run","walk","cycle","swim","row","cardio","other"]},"duration_min":{"type":"number","exclusiveMinimum":0},
    "distance":{"type":"number","minimum":0},"distance_unit":{"type":"string","enum":["km","mi"]},"elevation_gain":{"type":"number","minimum":0},"elevation_unit":{"type":"string","enum":["m","ft"]},
    "performed_at":{"type":"string"},"rpe":{"type":"number","minimum":1,"maximum":10},"title":{"type":"string"},"notes":{"type":"string"}},["type","duration_min"])},
 {"name":"delete_session","title":"Delete session","description":"Delete a whole session (and its sets) by session_id.","inputSchema":O({"session_id":{"type":"integer"}},["session_id"]),"annotations":{"destructiveHint":True}},
 {"name":"suggest_next","title":"Suggest next sets","description":"Progressive-overload prescription for one exercise (double progression in the rep range; Epley e1RM; deload after 2 missed sessions or >5% e1RM drop; -10% after >14 days off). status: no_history|add_reps|increase_weight|deload|reset|progress. Includes equipment_status (available|unknown|unavailable).",
  "inputSchema":O({"exercise":{"type":"string"},"target_rep_range":{"type":"string","description":"e.g. '5-8'; default settings.rep_range_default or 8-12"},"sets":{"type":"integer","minimum":1,"maximum":10}},["exercise"]),"annotations":{"readOnlyHint":True}},
 {"name":"daily_plan_context","title":"Daily plan context","description":"Facts for composing today's workout: week progress vs target, muscle-group recovery (48h), cardio minutes vs last week (≤10% growth cap), hikes, a rule-based suggested_focus (upper|lower|full_body|cardio_hiit|hike|rest_or_mobility|optional_light) with rationale, and candidate exercises with suggest_next prescriptions. Handles unknown equipment (marks equipment_status=unknown).",
  "inputSchema":O({"date":{"type":"string","description":"YYYY-MM-DD local; default today"}}),"annotations":{"readOnlyHint":True}},
 {"name":"report","title":"Workout report","description":"Weekly/monthly/quarterly/annual/all-time report: summary, vs previous period, adherence (weeks meeting target, streaks), PRs, main-lift e1RM change & plateaus, chart series, milestones (all). Renders a report UI; also returns fallback_url for clients without MCP Apps (show it to the user on mobile).",
  "inputSchema":O({"period":{"type":"string","enum":["week","month","quarter","year","all"]},"anchor_date":{"type":"string","description":"YYYY-MM-DD inside the period; default today"}},["period"]),
  "annotations":{"readOnlyHint":True},"_meta":_uimeta(REPORT_URI,"Building report…","Report ready")},
 {"name":"show_progress","title":"Show progress chart","description":"Chart progress for one exercise (metric e1rm|volume|best_set|reps) or overall (volume|sets|days|cardio_minutes) over range 4w|12w|6m|1y|all. Renders a chart UI; returns fallback_url for mobile.",
  "inputSchema":O({"exercise":{"type":"string"},"metric":{"type":"string","enum":["e1rm","volume","best_set","reps","sets","days","cardio_minutes"]},"range":{"type":"string","enum":["4w","12w","6m","1y","all"],"default":"12w"}}),
  "annotations":{"readOnlyHint":True},"_meta":_uimeta(PROG_URI,"Drawing chart…","Chart ready")},
]
RESOURCES=[{"uri":HIIT_URI,"name":"hiit_timer_view","title":"HIIT Timer","description":"Configurable HIIT timer","mimeType":MIME,"_meta":RMETA},
 {"uri":PROG_URI,"name":"progress_view","title":"Progress chart","description":"Progress charts","mimeType":MIME,"_meta":{**RMETA,"openai/widgetDescription":"Workout progress chart"}},
 {"uri":REPORT_URI,"name":"report_view","title":"Workout report","description":"Period report","mimeType":MIME,"_meta":{**RMETA,"openai/widgetDescription":"Workout report"}}]

def _summary(name,r):
  if name=="log_sets":
    if r["status"]!="logged": return "Nothing saved — needs confirmation: "+json.dumps(r["problems"],ensure_ascii=False)
    return "Logged: "+"; ".join(f"{x['exercise']} {x['reps'] or ''}{' reps' if x['reps'] else ''}{(' @ '+str(x['weight'])+x['unit']) if x['weight'] is not None else ''}{' (PR!)' if x['is_pr'] else ''}" for x in r["logged"])
  if name=="hiit_timer":
    return f"HIIT timer ready (id {r['hiit_id']}): {r['rounds']} rounds × "+", ".join(f"{s.get('exercise') or s['kind']} {s['seconds']}s" for s in r["segments"])+f"; total {r['planned_seconds']//60}m{r['planned_seconds']%60:02d}s. Press Start."
  return None
def call_tool(c,name,a):
  a=a or {}
  if name=="get_context": return core.get_context(c,**a)
  if name=="update_settings": return core.update_settings(c,a.get("settings"))
  if name=="resolve_exercise": return core.resolve_exercise(c,a.get("names") or [])
  if name=="add_alias": return core.add_alias(c,**a)
  if name=="add_exercise": return core.add_exercise(c,**a)
  if name=="log_sets": return core.log_sets(c,a.get("sets"),a.get("performed_at"),a.get("notes"))
  if name=="list_recent": return core.list_recent(c,**a)
  if name=="delete_set": return core.delete_set(c,a.get("set_id"))
  if name=="query_stats": return core.query_stats(c,**a)
  if name=="hiit_timer": return core.create_hiit(c,**a)
  if name=="log_hiit": return core.log_hiit(c,**a)
  if name=="log_activity": return core.log_activity(c,**a)
  if name=="delete_session": return core.delete_session(c,a.get("session_id"))
  if name=="suggest_next": return coach.suggest_next(c,**a)
  if name=="daily_plan_context": return coach.daily_plan_context(c,**a)
  if name=="report": return reports.report(c,**a)
  if name=="show_progress": return reports.show_progress(c,**a)
  raise KeyError(f"unknown tool: {name}")

class Server:
  def __init__(s,db_path=None): s.c=dbm.connect(db_path)
  def handle(s,m):
    meth=m.get("method"); p=m.get("params") or {}
    if meth=="initialize":
      pv=p.get("protocolVersion")
      return {"protocolVersion":pv if pv in("2024-11-05","2025-03-26","2025-06-18","2025-11-25") else "2025-06-18",
        "capabilities":{"tools":{"listChanged":False},"resources":{"listChanged":False},"extensions":{"io.modelcontextprotocol/ui":{"mimeTypes":[MIME]}}},
        "serverInfo":{"name":NAME,"version":VERSION},
        "instructions":"Workout tracker for Charles. Parse natural-language sets into log_sets args; the server normalizes exercise names (EN/中文). If get_context.onboarding_required is non-empty, ask for unit, days/week and goals and call update_settings."}
    if meth=="ping": return {}
    if meth=="tools/list": return {"tools":TOOLS}
    if meth=="resources/list": return {"resources":RESOURCES}
    if meth=="resources/templates/list": return {"resourceTemplates":[]}
    if meth=="prompts/list": return {"prompts":[]}
    if meth=="resources/read":
      u=p.get("uri")
      if u not in UI_FILES: raise KeyError("unknown resource")
      return {"contents":[{"uri":u,"mimeType":MIME,"text":_ui_html(u),"_meta":RMETA}]}
    if meth=="tools/call":
      name=p.get("name")
      if name not in {t["name"] for t in TOOLS}: raise KeyError(f"unknown tool: {name}")
      try: r=call_tool(s.c,name,p.get("arguments"))
      except (UserError,TypeError,ValueError) as e:
        s.c.rollback(); return {"content":[{"type":"text","text":f"Error: {e}"}],"isError":True}
      txt=_summary(name,r)
      if name in FALLBACK_PAGE:
        r["fallback_url"]=fallback.link(s.c,FALLBACK_PAGE[name],{k:v for k,v in r.items() if k!="fallback_url"})
        fb=("Open on phone / no-UI clients: "+r["fallback_url"]) if r["fallback_url"] else "(No fallback link: fallback_base_url not configured.)"
        txt=(txt+"\n"+fb) if txt else fb
      body=json.dumps(r,ensure_ascii=False)
      out={"content":[{"type":"text","text":(txt+"\n"+body) if txt else body}],"structuredContent":r}
      if name in TOOL_UI: out["_meta"]={"ui":{"resourceUri":TOOL_UI[name]}}
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
