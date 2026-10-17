"""Business logic. Every public function takes a sqlite connection and plain args, returns JSON-able dicts."""
import json,os
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from normalize import norm,fuzzy
import db as dbm
import workouts,calories

LB=0.45359237
TZ=ZoneInfo("America/Los_Angeles")
AUTO_FUZZY=0.85
SETTING_KEYS={"unit":str,"days_per_week":int,"goals":list,"preferred_days":list,"session_minutes":int,
  "experience":str,"equipment":list,"timezone":str,"bodyweight":float,"injuries":str,"constraints":str,
  "rep_range_default":str,"onboarded_at":str,"reminders_enabled":bool,"daily_plan_enabled":bool,
  "workout_reminder_enabled":bool,"weekly_report_enabled":bool,"hiit_defaults":dict,
  "model_only_acknowledged":bool,"nutrition_profile":dict}
ONBOARDING_REQUIRED=()

class UserError(Exception): pass

def _parse_time(s,tz=TZ):
  if not s: return datetime.now(timezone.utc).replace(microsecond=0)
  d=datetime.fromisoformat(s.replace("Z","+00:00"))
  if d.tzinfo is None: d=d.replace(tzinfo=tz)   # naive = configured local time
  return d.astimezone(timezone.utc)
def _iso(d): return d.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def _local(s,tz=TZ): return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(tz)
def _round(x,n=1): return None if x is None else round(x,n)

# ---------- settings ----------
def get_settings(c):
  return {r["key"]:json.loads(r["value"]) for r in c.execute("SELECT key,value FROM settings")}
def user_timezone(c): return ZoneInfo(get_settings(c).get("timezone","America/Los_Angeles"))
def display_unit(c): return get_settings(c).get("unit","lb")
def to_unit(kg,unit): return None if kg is None else _round(kg/LB if unit=="lb" else kg,1)

def update_settings(c,settings):
  if not isinstance(settings,dict) or not settings: raise UserError("settings must be a non-empty object")
  for k,v in settings.items():
    if k not in SETTING_KEYS: raise UserError(f"unknown setting '{k}'. Allowed: {sorted(SETTING_KEYS)}")
    if k=="hiit_defaults" and v is None: continue
    typ=SETTING_KEYS[k]
    if typ is float and isinstance(v,int): v=float(v)
    if not isinstance(v,typ) or (typ is not bool and isinstance(v,bool)): raise UserError(f"setting '{k}' must be {typ.__name__}")
    if k=="unit" and v not in("lb","kg"): raise UserError("unit must be 'lb' or 'kg'")
    if k=="days_per_week" and not 1<=v<=7: raise UserError("days_per_week must be 1-7")
    if k=="rep_range_default":
      import re
      if not re.fullmatch(r"\d{1,2}-\d{1,2}",v): raise UserError("rep_range_default must look like 8-12")
    if k=="timezone":
      try: ZoneInfo(v)
      except Exception: raise UserError("timezone must be a valid IANA timezone")
    if k=="hiit_defaults":
      required={"work_duration_s","rest_duration_s","rounds","warmup_s","cooldown_s"}
      if set(v)!=required or any(not isinstance(v[x],int) or v[x]<0 for x in required) or v["work_duration_s"]<1 or v["rest_duration_s"]<1 or v["rounds"]<1:
        raise UserError("hiit_defaults needs positive work/rest/rounds and non-negative warmup/cooldown")
  t=dbm.now()
  for k,v in settings.items():
    if k=="hiit_defaults" and v is None:
      c.execute("DELETE FROM settings WHERE key=?",(k,)); continue
    c.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(k,json.dumps(v,ensure_ascii=False),t))
  s=get_settings(c)
  c.commit()
  return {"settings":s,"onboarding_required":[k for k in ONBOARDING_REQUIRED if k not in s]}

# ---------- exercises / aliases ----------
def _ex(c,eid):
  r=c.execute("SELECT * FROM exercises WHERE id=?",(eid,)).fetchone()
  return {"exercise_id":r["id"],"canonical":r["canonical_name"],"name_zh":r["name_zh"],"category":r["category"],
          "tracking":r["tracking"],"is_bodyweight":bool(r["is_bodyweight"]),"muscle_groups":json.loads(r["muscle_groups"] or "[]")}
def resolve_one(c,name):
  n=norm(name)
  if not n: return {"input":name,"status":"not_found","candidates":[]}
  r=c.execute("SELECT exercise_id FROM aliases WHERE alias_norm=?",(n,)).fetchone()
  if r: return {"input":name,"status":"matched","match":"exact","confidence":1.0,**_ex(c,r[0])}
  keys={row[0]:row[1] for row in c.execute("SELECT alias_norm,exercise_id FROM aliases")}
  seen,cands=set(),[]
  for k,sc in fuzzy(n,keys,n=15):
    if keys[k] in seen: continue
    seen.add(keys[k]); e=_ex(c,keys[k]); cands.append({"exercise_id":e["exercise_id"],"canonical":e["canonical"],"name_zh":e["name_zh"],"score":round(sc,2)})
  cands=cands[:5]
  if cands and cands[0]["score"]>=AUTO_FUZZY and (len(cands)==1 or cands[1]["score"]<cands[0]["score"]-0.05):
    return {"input":name,"status":"matched","match":"fuzzy","confidence":cands[0]["score"],**_ex(c,cands[0]["exercise_id"])}
  return {"input":name,"status":"ambiguous" if cands else "not_found","candidates":cands}
def resolve_exercise(c,names):
  if isinstance(names,str): names=[names]
  return {"results":[resolve_one(c,n) for n in names]}
def _lookup_exercise(c,ref):
  if isinstance(ref,int) or (isinstance(ref,str) and ref.isdigit()):
    r=c.execute("SELECT id FROM exercises WHERE id=?",(int(ref),)).fetchone()
  else:
    r=c.execute("SELECT id FROM exercises WHERE canonical_name=? COLLATE NOCASE",(ref,)).fetchone() \
      or c.execute("SELECT exercise_id FROM aliases WHERE alias_norm=?",(norm(ref),)).fetchone()
  if not r: raise UserError(f"exercise '{ref}' not found (use exercise_id or canonical name)")
  return r[0]
def add_alias(c,exercise,alias,lang=None):
  eid=_lookup_exercise(c,exercise); n=norm(alias)
  if not n: raise UserError("alias is empty after normalization")
  ex=c.execute("SELECT exercise_id FROM aliases WHERE alias_norm=?",(n,)).fetchone()
  if ex:
    if ex[0]==eid: return {"status":"exists",**_ex(c,eid),"alias":alias}
    raise UserError(f"alias '{alias}' already maps to '{_ex(c,ex[0])['canonical']}'")
  dbm.add_alias_row(c,eid,alias,lang or dbm.lang_of(alias),"user"); c.commit()
  return {"status":"added","alias":alias,"alias_norm":n,**_ex(c,eid)}
def add_exercise(c,canonical,category,name_zh=None,muscle_groups=None,equipment=None,is_bodyweight=False,tracking="reps_weight",aliases=None):
  if c.execute("SELECT 1 FROM exercises WHERE canonical_name=? COLLATE NOCASE",(canonical,)).fetchone(): raise UserError(f"'{canonical}' already exists")
  for a in [canonical,name_zh,*(aliases or [])]:
    if a and c.execute("SELECT 1 FROM aliases WHERE alias_norm=?",(norm(a),)).fetchone(): raise UserError(f"name/alias '{a}' already used by another exercise")
  try:
    eid=c.execute("INSERT INTO exercises(canonical_name,name_zh,category,muscle_groups,equipment,is_bodyweight,tracking,created_at) VALUES(?,?,?,?,?,?,?,?)",
      (canonical,name_zh,category,json.dumps(muscle_groups or []),equipment,int(bool(is_bodyweight)),tracking,dbm.now())).lastrowid
  except Exception as e: c.rollback(); raise UserError(str(e))
  for a in [canonical,name_zh,*(aliases or [])]:
    if a: dbm.add_alias_row(c,eid,a,dbm.lang_of(a),"user")
  c.commit(); return {"status":"added",**_ex(c,eid)}

# ---------- logging ----------
def epley(kg,reps):
  if not kg or not reps or reps<1: return None
  return kg if reps==1 else kg*(1+reps/30)
def _session_for(c,t,typ,session_weight_kg=None,planned_duration_min=None):
  return workouts.session_for_log(c,t,typ,session_weight_kg,planned_duration_min)
def log_sets(c,sets,performed_at=None,notes=None,session_weight=None,weight_unit="kg",planned_duration_min=None):
  if not sets: raise UserError("sets must be a non-empty list")
  if session_weight is not None and (not isinstance(session_weight,(int,float)) or isinstance(session_weight,bool) or session_weight<=0): raise UserError("session_weight must be > 0")
  if weight_unit not in("lb","kg"): raise UserError("weight_unit must be lb or kg")
  if planned_duration_min is not None and (not isinstance(planned_duration_min,(int,float)) or planned_duration_min<=0): raise UserError("planned_duration_min must be > 0")
  session_weight_kg=None if session_weight is None else float(session_weight)*(LB if weight_unit=="lb" else 1)
  tz=user_timezone(c); t0=_parse_time(performed_at,tz); unit_default=display_unit(c)
  resolved,problems=[],[]
  for i,s in enumerate(sets):
    if not isinstance(s,dict) or not s.get("exercise"): problems.append({"index":i,"error":"missing exercise"}); continue
    r=resolve_one(c,s["exercise"])
    if r["status"]!="matched": problems.append({"index":i,"input":s["exercise"],"status":r["status"],"candidates":r["candidates"]}); continue
    if not any(s.get(k) is not None for k in("reps","duration_s","distance_m")): problems.append({"index":i,"error":"need reps, duration_s or distance_m"}); continue
    for k in("reps","count"):
      if s.get(k) is not None and (not isinstance(s[k],int) or s[k]<1): problems.append({"index":i,"error":f"{k} must be a positive integer"})
    if s.get("weight") is not None and (not isinstance(s["weight"],(int,float)) or s["weight"]<0): problems.append({"index":i,"error":"weight must be >= 0"})
    if s.get("unit") not in(None,"lb","kg"): problems.append({"index":i,"error":"unit must be lb or kg"})
    if s.get("session_item_id") is not None:
      item=c.execute("""SELECT i.* FROM workout_session_items i JOIN sessions w ON w.id=i.session_id
        WHERE i.id=? AND w.status='in_progress' AND i.status NOT IN('skipped','replaced')""",(s["session_item_id"],)).fetchone()
      if not item: problems.append({"index":i,"error":"session_item_id is not available in the active workout"})
      elif item["exercise_id"] is not None and item["exercise_id"]!=r["exercise_id"]: problems.append({"index":i,"error":"exercise does not match session_item_id"})
    resolved.append((s,r))
  if problems:
    return {"status":"needs_confirmation","logged":[],"problems":problems,
            "hint":"Nothing was saved. Confirm the exercise with the user, then call add_alias (or add_exercise) and retry."}
  typ={r["category"] for _,r in resolved}; typ=typ.pop() if len(typ)==1 else "mixed"
  out=[]; now=dbm.now()
  with dbm.transaction(c):
   sid,new=_session_for(c,t0,typ,session_weight_kg,planned_duration_min)
   if notes: c.execute("UPDATE sessions SET notes=trim(coalesce(notes,'')||' '||?) WHERE id=?",(notes,sid))
   for s,r in resolved:
    unit=s.get("unit") or unit_default; w=s.get("weight")
    kg=None if w is None else (w*LB if unit=="lb" else float(w))
    prev_best=c.execute("SELECT max(e1rm_kg) FROM sets WHERE exercise_id=? AND is_warmup=0",(r["exercise_id"],)).fetchone()[0]
    prev_reps=c.execute("SELECT max(reps) FROM sets WHERE exercise_id=? AND weight_kg IS NULL",(r["exercise_id"],)).fetchone()[0]
    for _ in range(s.get("count") or 1):
      idx=c.execute("SELECT count(*)+1 FROM sets WHERE session_id=? AND exercise_id=?",(sid,r["exercise_id"])).fetchone()[0]
      e=epley(kg,s.get("reps"))
      cur=c.execute("""INSERT INTO sets(session_id,session_item_id,exercise_id,set_index,performed_at,reps,weight_kg,input_weight,input_unit,duration_s,distance_m,rpe,is_warmup,e1rm_kg,raw_text,notes,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(sid,s.get("session_item_id"),r["exercise_id"],idx,_iso(t0),s.get("reps"),kg,w,unit if w is not None else None,
        s.get("duration_s"),s.get("distance_m"),s.get("rpe"),int(bool(s.get("is_warmup"))),e,s.get("raw_text"),s.get("note"),now))
      pr=bool(not s.get("is_warmup") and ((e and (prev_best is None or e>prev_best+1e-9)) or (kg is None and s.get("reps") and (prev_reps is None or s["reps"]>prev_reps))))
      out.append({"set_id":cur.lastrowid,"session_item_id":s.get("session_item_id"),"exercise":r["canonical"],"name_zh":r["name_zh"],"set_index":idx,"reps":s.get("reps"),
        "weight":to_unit(kg,unit) if kg is not None else None,"unit":unit if kg is not None else None,
        "duration_s":s.get("duration_s"),"distance_m":s.get("distance_m"),
        "e1rm":to_unit(e,unit) if e else None,"is_pr":pr,"matched_from":s["exercise"] if r["match"]!="exact" or norm(s["exercise"])!=norm(r["canonical"]) else None})
      if e: prev_best=max(prev_best or 0,e)
      if kg is None and s.get("reps"): prev_reps=max(prev_reps or 0,s["reps"])
      if s.get("session_item_id") is not None:
        c.execute("UPDATE workout_session_items SET status='completed' WHERE id=?",(s["session_item_id"],))
   estimate=calories.estimate_session(c,sid)
  return {"status":"logged","session_id":sid,"new_session":new,"performed_at_local":_local(_iso(t0),tz).isoformat(),"logged":out,"calorie_estimate":estimate}

def _set_row(c,r,unit):
  tz=user_timezone(c)
  return {"set_id":r["id"],"session_id":r["session_id"],"exercise":r["canonical_name"],"name_zh":r["name_zh"],"set_index":r["set_index"],
    "performed_at_local":_local(r["performed_at"],tz).strftime("%Y-%m-%d %H:%M"),"reps":r["reps"],
    "weight":to_unit(r["weight_kg"],unit),"unit":unit if r["weight_kg"] is not None else None,
    "duration_s":r["duration_s"],"distance_m":r["distance_m"],"rpe":r["rpe"],"e1rm":to_unit(r["e1rm_kg"],unit)}
def list_recent(c,limit=10,exercise=None):
  unit=display_unit(c); q="SELECT x.*,e.canonical_name,e.name_zh FROM sets x JOIN exercises e ON e.id=x.exercise_id"; a=[]
  if exercise: q+=" WHERE x.exercise_id=?"; a.append(_lookup_exercise(c,exercise))
  q+=" ORDER BY x.performed_at DESC, x.id DESC LIMIT ?"; a.append(max(1,min(int(limit),100)))
  return {"unit":unit,"sets":[_set_row(c,r,unit) for r in c.execute(q,a)]}
def delete_set(c,set_id=None):
  unit=display_unit(c)
  q="SELECT x.*,e.canonical_name,e.name_zh FROM sets x JOIN exercises e ON e.id=x.exercise_id "
  r=c.execute(q+("WHERE x.id=?" if set_id else "ORDER BY x.id DESC LIMIT 1"),(set_id,) if set_id else ()).fetchone()
  if not r: raise UserError("no such set" if set_id else "no sets logged yet")
  c.execute("DELETE FROM sets WHERE id=?",(r["id"],))
  gone=False
  if not c.execute("SELECT 1 FROM sets WHERE session_id=?",(r["session_id"],)).fetchone() and \
     not c.execute("SELECT 1 FROM hiit_workouts WHERE session_id=?",(r["session_id"],)).fetchone():
    c.execute("DELETE FROM sessions WHERE id=?",(r["session_id"],)); gone=True
  c.commit(); return {"status":"deleted","deleted":_set_row(c,r,unit),"session_removed":gone}
def update_set(c,set_id,exercise=None,reps=None,weight=None,unit=None,duration_s=None,distance_m=None,rpe=None,is_warmup=None):
  row=c.execute("SELECT * FROM sets WHERE id=?",(set_id,)).fetchone()
  if not row: raise UserError("no such set")
  eid=_lookup_exercise(c,exercise) if exercise is not None else row["exercise_id"]
  new_reps=row["reps"] if reps is None else reps
  if new_reps is not None and (not isinstance(new_reps,int) or new_reps<1): raise UserError("reps must be a positive integer")
  chosen_unit=unit or row["input_unit"] or display_unit(c)
  if chosen_unit not in("lb","kg"): raise UserError("unit must be lb or kg")
  if weight is None:
    weight_kg,input_weight,input_unit=row["weight_kg"],row["input_weight"],row["input_unit"]
  else:
    if not isinstance(weight,(int,float)) or isinstance(weight,bool) or weight<0: raise UserError("weight must be >= 0")
    weight_kg=float(weight)*(LB if chosen_unit=="lb" else 1); input_weight=weight; input_unit=chosen_unit
  warmup=row["is_warmup"] if is_warmup is None else int(bool(is_warmup))
  with dbm.transaction(c):
    c.execute("""UPDATE sets SET exercise_id=?,reps=?,weight_kg=?,input_weight=?,input_unit=?,duration_s=?,distance_m=?,rpe=?,is_warmup=?,e1rm_kg=? WHERE id=?""",
      (eid,new_reps,weight_kg,input_weight,input_unit,row["duration_s"] if duration_s is None else duration_s,
       row["distance_m"] if distance_m is None else distance_m,row["rpe"] if rpe is None else rpe,warmup,epley(weight_kg,new_reps),set_id))
    estimate=calories.estimate_session(c,row["session_id"])
  updated=c.execute("SELECT x.*,e.canonical_name,e.name_zh FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE x.id=?",(set_id,)).fetchone()
  return {"status":"updated","set":_set_row(c,updated,chosen_unit),"calorie_estimate":estimate}

# ---------- stats ----------
def _bucket(local,group_by):
  if group_by=="day": return local.strftime("%Y-%m-%d")
  if group_by=="month": return local.strftime("%Y-%m")
  return (local.date()-timedelta(days=local.weekday())).isoformat()   # week starting Monday
def query_stats(c,metric,exercise=None,from_date=None,to_date=None,group_by="week"):
  if metric not in("e1rm","volume","frequency","best_set","reps"): raise UserError("metric must be e1rm|volume|frequency|best_set|reps")
  if group_by not in("day","week","month"): raise UserError("group_by must be day|week|month")
  unit=display_unit(c); tz=user_timezone(c); q="SELECT x.*,e.canonical_name FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE x.is_warmup=0"; a=[]
  ex_name=None
  if exercise: eid=_lookup_exercise(c,exercise); q+=" AND x.exercise_id=?"; a.append(eid); ex_name=_ex(c,eid)["canonical"]
  if from_date: q+=" AND x.performed_at>=?"; a.append(_iso(_parse_time(from_date+"T00:00:00" if len(from_date)==10 else from_date,tz)))
  if to_date: q+=" AND x.performed_at<?"; a.append(_iso(_parse_time(to_date+"T00:00:00" if len(to_date)==10 else to_date,tz)+(timedelta(days=1) if len(to_date)==10 else timedelta(0))))
  b={}
  for r in c.execute(q+" ORDER BY x.performed_at",a):
    k=_bucket(_local(r["performed_at"],tz),group_by); d=b.setdefault(k,{"sets":0,"reps":0,"volume_kg":0.0,"e1rm_kg":None,"sessions":set(),"best":None})
    d["sets"]+=1; d["reps"]+=r["reps"] or 0; d["sessions"].add(r["session_id"])
    if r["weight_kg"] and r["reps"]: d["volume_kg"]+=r["weight_kg"]*r["reps"]
    if r["e1rm_kg"] and (d["e1rm_kg"] is None or r["e1rm_kg"]>d["e1rm_kg"]): d["e1rm_kg"]=r["e1rm_kg"]; d["best"]=(r["weight_kg"],r["reps"])
  series=[]
  for k in sorted(b):
    d=b[k]; p={"period":k,"sets":d["sets"],"sessions":len(d["sessions"])}
    if metric=="e1rm": p["e1rm"]=to_unit(d["e1rm_kg"],unit)
    elif metric=="volume": p["volume"]=to_unit(d["volume_kg"],unit); p["reps"]=d["reps"]
    elif metric=="reps": p["reps"]=d["reps"]
    elif metric=="best_set": p["best_set"]=None if not d["best"] else {"weight":to_unit(d["best"][0],unit),"reps":d["best"][1],"e1rm":to_unit(d["e1rm_kg"],unit)}
    series.append(p)
  return {"metric":metric,"exercise":ex_name,"group_by":group_by,"unit":unit,"series":series,
          "totals":{"sets":sum(p["sets"] for p in series),"periods":len(series)}}

# ---------- context ----------
def get_context(c,days=14):
  s=get_settings(c); unit=s.get("unit","lb"); tz=ZoneInfo(s.get("timezone","America/Los_Angeles"))
  since=_iso(datetime.now(timezone.utc)-timedelta(days=int(days)))
  sess=[]
  for r in c.execute("SELECT * FROM sessions WHERE started_at>=? ORDER BY started_at DESC",(since,)):
    ex=[{"exercise":x["canonical_name"],"sets":x["n"],"top_weight":to_unit(x["w"],unit),"max_reps":x["r"]} for x in c.execute(
      "SELECT e.canonical_name,count(*) n,max(x.weight_kg) w,max(x.reps) r FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE x.session_id=? GROUP BY e.id ORDER BY min(x.id)",(r["id"],))]
    sess.append({"session_id":r["id"],"date_local":_local(r["started_at"],tz).strftime("%Y-%m-%d %a %H:%M"),"type":r["type"],"exercises":ex})
  today=datetime.now(tz).date(); wk=today-timedelta(days=today.weekday())
  days_this_week=len({_local(r[0],tz).date() for r in c.execute("SELECT performed_at FROM sets WHERE performed_at>=?",(_iso(datetime.combine(wk,datetime.min.time(),tz)),))}
                     |{_local(r[0],tz).date() for r in c.execute("SELECT completed_at FROM hiit_workouts WHERE status='completed' AND completed_at>=?",(_iso(datetime.combine(wk,datetime.min.time(),tz)),))})
  muscles={}
  for r in c.execute("SELECT e.muscle_groups,count(*) n FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE x.performed_at>=? GROUP BY e.id",(since,)):
    for m in json.loads(r["muscle_groups"] or "[]"): muscles[m]=muscles.get(m,0)+r["n"]
  weight=c.execute("SELECT measured_at,input_value,input_unit,weight_kg FROM body_weight ORDER BY measured_at DESC,id DESC LIMIT 1").fetchone()
  latest_weight=None if not weight else dict(weight)
  return {"today_local":today.isoformat(),"settings":s,"onboarding_required":[k for k in ONBOARDING_REQUIRED if k not in s],
    "latest_body_weight":latest_weight,"nutrition_setup":{"usda_available":bool(os.environ.get("USDA_FDC_API_KEY")),"model_only_acknowledged":bool(s.get("model_only_acknowledged",False))},
    "unit":unit,"window_days":int(days),"days_trained_this_week":days_this_week,"target_days_per_week":s.get("days_per_week"),
    "recent_sessions":sess,"sets_by_muscle":dict(sorted(muscles.items(),key=lambda kv:-kv[1])),
    "total_sets_all_time":c.execute("SELECT count(*) FROM sets").fetchone()[0]}

# ---------- HIIT ----------
def hiit_config(rounds=20,segments=None,name=None,warmup_s=10,cooldown_s=60):
  segments=segments or [{"kind":"work","seconds":40,"exercise":None},{"kind":"rest","seconds":20}]
  if not isinstance(rounds,int) or not 1<=rounds<=100: raise UserError("rounds must be 1-100")
  clean=[]
  for sg in segments:
    k=sg.get("kind","work"); sec=sg.get("seconds")
    if k not in("work","rest"): raise UserError("segment kind must be work|rest")
    if not isinstance(sec,int) or not 1<=sec<=3600: raise UserError("segment seconds must be 1-3600")
    clean.append({"kind":k,"seconds":sec,"exercise":sg.get("exercise")})
  for v in(warmup_s,cooldown_s):
    if not isinstance(v,int) or not 0<=v<=3600: raise UserError("warmup_s/cooldown_s must be 0-3600")
  total=warmup_s+cooldown_s+rounds*sum(x["seconds"] for x in clean)-(clean[-1]["seconds"] if clean[-1]["kind"]=="rest" else 0)  # UI skips final rest
  return {"name":name or "HIIT","rounds":rounds,"segments":clean,"warmup_s":warmup_s,"cooldown_s":cooldown_s,"planned_seconds":total}
def create_hiit(c,**kw):
  cfg=hiit_config(**kw)
  hid=c.execute("INSERT INTO hiit_workouts(name,config_json,planned_seconds,status,created_at) VALUES(?,?,?,'planned',?)",
                (cfg["name"],json.dumps(cfg,ensure_ascii=False),cfg["planned_seconds"],dbm.now())).lastrowid
  c.commit(); return {"hiit_id":hid,**cfg}
def log_hiit(c,hiit_id,completed_rounds,total_seconds,status="completed"):
  r=c.execute("SELECT * FROM hiit_workouts WHERE id=?",(hiit_id,)).fetchone()
  if not r: raise UserError("unknown hiit_id")
  if status not in("completed","aborted"): raise UserError("status must be completed|aborted")
  if r["status"]!="planned": return {"status":"already_logged","hiit_id":hiit_id,"previous_status":r["status"]}
  t=datetime.now(timezone.utc).replace(microsecond=0); start=t-timedelta(seconds=int(total_seconds))
  with dbm.transaction(c):
    sid=c.execute("INSERT INTO sessions(started_at,ended_at,type,title,duration_min,source,status,start_mode,last_activity_at) VALUES(?,?,'hiit',?,?,'hiit_timer','completed','implicit',?)",
                  (_iso(start),_iso(t),r["name"],round(total_seconds/60,1),_iso(t))).lastrowid
    c.execute("UPDATE hiit_workouts SET status=?,completed_rounds=?,total_seconds=?,completed_at=?,session_id=? WHERE id=?",
              (status,completed_rounds,total_seconds,_iso(t),sid,hiit_id))
    estimate=calories.estimate_session(c,sid)
  return {"status":status,"hiit_id":hiit_id,"session_id":sid,"completed_rounds":completed_rounds,"total_seconds":total_seconds,"calorie_estimate":estimate}

# ---------- activities (hike / cardio sessions) ----------
MI=1.609344; FT=0.3048
def log_activity(c,type,duration_min,distance=None,distance_unit=None,elevation_gain=None,elevation_unit=None,performed_at=None,rpe=None,notes=None,title=None,session_item_id=None,session_weight=None,weight_unit="kg"):
  if type not in("hike","run","walk","cycle","swim","row","cardio","other"): raise UserError("type must be hike|run|walk|cycle|swim|row|cardio|other")
  if not isinstance(duration_min,(int,float)) or duration_min<=0: raise UserError("duration_min must be > 0")
  imperial=display_unit(c)=="lb"
  du=distance_unit or ("mi" if imperial else "km"); eu=elevation_unit or ("ft" if imperial else "m")
  if du not in("km","mi") or eu not in("m","ft"): raise UserError("distance_unit km|mi, elevation_unit m|ft")
  km=None if distance is None else distance*(MI if du=="mi" else 1)
  m=None if elevation_gain is None else elevation_gain*(FT if eu=="ft" else 1)
  if session_weight is not None and (not isinstance(session_weight,(int,float)) or isinstance(session_weight,bool) or session_weight<=0): raise UserError("session_weight must be > 0")
  if weight_unit not in("lb","kg"): raise UserError("weight_unit must be lb or kg")
  session_weight_kg=None if session_weight is None else float(session_weight)*(LB if weight_unit=="lb" else 1)
  if session_item_id is not None:
    item=c.execute("""SELECT i.* FROM workout_session_items i JOIN sessions w ON w.id=i.session_id
      WHERE i.id=? AND w.status='in_progress' AND i.status NOT IN('skipped','replaced')""",(session_item_id,)).fetchone()
    if not item: raise UserError("session_item_id is not available in the active workout")
  tz=user_timezone(c); t=_parse_time(performed_at,tz); stype="hike" if type=="hike" else "cardio"
  end=t+timedelta(minutes=duration_min)
  with dbm.transaction(c):
    sid,new=workouts.session_for_log(c,t,stype,session_weight_kg)
    if session_item_id is not None and item["session_id"]!=sid: raise UserError("session_item_id belongs to another workout")
    c.execute("UPDATE sessions SET title=?,duration_min=?,distance_km=?,elevation_gain_m=?,rpe=?,notes=?,last_activity_at=?,activity_session_item_id=? WHERE id=?",
      (title or type,duration_min,km,m,rpe,notes,_iso(end),session_item_id,sid))
    if session_item_id is not None: c.execute("UPDATE workout_session_items SET status='completed' WHERE id=?",(session_item_id,))
    estimate=calories.estimate_session(c,sid)
  return {"status":"logged","session_id":sid,"type":stype,"activity":type,"duration_min":duration_min,
    "distance":distance,"distance_unit":du if distance is not None else None,"elevation_gain":elevation_gain,"elevation_unit":eu if elevation_gain is not None else None,
    "performed_at_local":_local(_iso(t),tz).isoformat(),"calorie_estimate":estimate}
def update_activity(c,session_id,duration_min,distance=None,distance_unit=None,elevation_gain=None,elevation_unit=None,rpe=None,notes=None,title=None):
  row=c.execute("SELECT * FROM sessions WHERE id=?",(session_id,)).fetchone()
  if not row or row["type"] not in("cardio","hike","mixed"): raise UserError("no such cardio or hike workout")
  if not isinstance(duration_min,(int,float)) or duration_min<=0: raise UserError("duration_min must be > 0")
  imperial=display_unit(c)=="lb"; du=distance_unit or ("mi" if imperial else "km"); eu=elevation_unit or ("ft" if imperial else "m")
  if du not in("km","mi") or eu not in("m","ft"): raise UserError("distance_unit km|mi, elevation_unit m|ft")
  km=row["distance_km"] if distance is None else distance*(MI if du=="mi" else 1)
  elevation=row["elevation_gain_m"] if elevation_gain is None else elevation_gain*(FT if eu=="ft" else 1)
  with dbm.transaction(c):
    c.execute("UPDATE sessions SET duration_min=?,distance_km=?,elevation_gain_m=?,rpe=?,notes=?,title=? WHERE id=?",
      (duration_min,km,elevation,rpe if rpe is not None else row["rpe"],notes if notes is not None else row["notes"],title if title is not None else row["title"],session_id))
    estimate=calories.estimate_session(c,session_id)
  out_distance=distance if distance is not None else (None if km is None else (km/MI if du=="mi" else km))
  out_elevation=elevation_gain if elevation_gain is not None else (None if elevation is None else (elevation/FT if eu=="ft" else elevation))
  return {"status":"updated","session_id":session_id,"duration_min":duration_min,
    "distance":out_distance,"distance_unit":du if out_distance is not None else None,
    "elevation_gain":out_elevation,"elevation_unit":eu if out_elevation is not None else None,"calorie_estimate":estimate}
def delete_session(c,session_id):
  r=c.execute("SELECT * FROM sessions WHERE id=?",(session_id,)).fetchone()
  if not r: raise UserError("no such session")
  n=c.execute("SELECT count(*) FROM sets WHERE session_id=?",(session_id,)).fetchone()[0]
  c.execute("DELETE FROM sessions WHERE id=?",(session_id,)); c.commit()
  return {"status":"deleted","session_id":session_id,"type":r["type"],"sets_deleted":n}
